"""The SPAN file currently in force, and the task that keeps it current.

One process-wide store. `current()` is synchronous and never touches the
network, so the balance path (polled every 2 s by several pages) can read it
freely. The `span-refresher` task looks for a newer file every
`options.span_refresh_interval_seconds`, downloads it, parses it in a thread,
and swaps it in -- the previous file stays in force until the new one has
parsed, so a bad download never leaves the book without margin.

Which file: the newest published one. NSE publishes a session's file in the
evening, so during the day the newest is the previous session's -- the same
end-of-day parameters a broker starts the day on. Intraday snapshots are
Phase 5 (OPTIONS_TRADER_PLAN.md section 7).

A missing file is a STATE, reported with its reason, never a zero: the margin
calculator falls back to its labelled scenario model, and `status()` says so.
"""
import asyncio
import glob
import os
import re
import zipfile
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from src import config_utils
from src.core.time_utils import ist_now
from src.logging_config import get_logger
from src.options.services import nse_span_client, span_file

logger = get_logger("options.span_store")

TASK_NAME = "span-refresher"
FILE_PATTERN = re.compile(r"nsccl\.(\d{8})\.s\.zip$")
LOOKBACK_DAYS = 7


def _cache_dir() -> str:
    return config_utils.get_property_value("options.span_cache_dir", "./data/span")


def _interval() -> int:
    return config_utils.get_property_value_int("options.span_refresh_interval_seconds", 1800)


def refresh_enabled() -> bool:
    return config_utils.get_property_value_boolean("options.span_refresh_enabled", True)


class SpanStore:
    def __init__(self) -> None:
        self.data: Optional[span_file.SpanData] = None
        self.loaded_from: Optional[str] = None
        self.loaded_at: Optional[datetime] = None
        self.last_attempt_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # --- reads ---------------------------------------------------------------
    def current(self) -> Optional[span_file.SpanData]:
        return self.data

    def status(self) -> Dict[str, Any]:
        data = self.data
        return {
            "loaded": data is not None,
            "businessDate": data.business_date.isoformat() if data else None,
            "created": data.created if data else None,
            "contracts": len(data.contracts) if data else 0,
            "loadedAt": self.loaded_at.isoformat() if self.loaded_at else None,
            "lastAttemptAt": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "lastError": self.last_error,
            "unsupported": list(data.unsupported[:10]) if data else [],
            "refreshEnabled": refresh_enabled(),
        }

    # --- loading -------------------------------------------------------------
    def _local_files(self) -> Dict[date, str]:
        found: Dict[date, str] = {}
        for path in glob.glob(os.path.join(_cache_dir(), "nsccl.*.s.zip")):
            match = FILE_PATTERN.search(os.path.basename(path))
            if match:
                found[datetime.strptime(match.group(1), "%Y%m%d").date()] = path
        return found

    async def _load_zip(self, day: date, zip_path: str) -> None:
        def _work():
            directory = os.path.dirname(zip_path)
            with zipfile.ZipFile(zip_path) as archive:
                names = [one for one in archive.namelist() if one.endswith(".spn")]
                if not names:
                    raise ValueError(f"{zip_path} holds no .spn file")
                archive.extract(names[0], directory)
                spn = os.path.join(directory, names[0])
            return span_file.load(spn, day)

        data = await asyncio.to_thread(_work)
        self.data = data
        self.loaded_from = zip_path
        self.loaded_at = ist_now()
        logger.info(
            "NSE SPAN file for %s in force: %s contracts, %s underlyings",
            day, len(data.contracts), len(data.spreads),
        )

    async def refresh(self, download: bool = True) -> Dict[str, Any]:
        """Load the newest local file; fetch newer ones from NSE if allowed."""
        async with self._lock:
            self.last_attempt_at = ist_now()
            os.makedirs(_cache_dir(), exist_ok=True)
            local = self._local_files()
            newest_local = max(local) if local else None
            today = ist_now().date()
            if download:
                for offset in range(0, LOOKBACK_DAYS):
                    day = today - timedelta(days=offset)
                    if newest_local is not None and day <= newest_local:
                        break
                    if day.weekday() >= 5:
                        continue
                    destination = os.path.join(_cache_dir(), f"nsccl.{day:%Y%m%d}.s.zip")
                    try:
                        await nse_span_client.download(day, destination)
                        local[day] = destination
                        break
                    except nse_span_client.SpanNotPublished:
                        continue
                    except nse_span_client.SpanDownloadError as error:
                        self.last_error = str(error)
                        logger.warning("%s", error)
                        break
            if not local:
                if self.last_error is None:
                    self.last_error = "No NSE SPAN file is available yet."
                return self.status()
            newest = max(local)
            if self.data is None or self.data.business_date < newest:
                try:
                    await self._load_zip(newest, local[newest])
                    self.last_error = None
                except Exception as error:  # noqa: BLE001 - keep the old file in force
                    self.last_error = f"Could not parse the {newest} SPAN file: {type(error).__name__}"
                    logger.exception("Parsing the NSE SPAN file for %s failed", newest)
            elif self.last_error and "download" not in self.last_error.lower():
                self.last_error = None
            return self.status()

    # --- the task --------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            try:
                await self.refresh(download=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the task must not die
                logger.exception("SPAN refresh pass failed")
            await asyncio.sleep(_interval())

    async def start(self) -> None:
        if not refresh_enabled():
            # Still load whatever is on disk, so margin works offline.
            await self.refresh(download=False)
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=TASK_NAME)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None


_store: Optional[SpanStore] = None


def get_span_store() -> SpanStore:
    global _store
    if _store is None:
        _store = SpanStore()
    return _store


def set_span_data_for_tests(data: Optional[span_file.SpanData]) -> None:
    get_span_store().data = data
