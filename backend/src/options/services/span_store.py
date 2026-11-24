"""The SPAN file currently in force, and the task that keeps it current.

One process-wide store. `current()` is synchronous and never touches the
network, so the balance path (polled every 2 s by several pages) can read it
freely. The `span-refresher` task looks for a newer file every
`options.span_refresh_interval_seconds`, downloads it, parses it in a thread,
and swaps it in -- the previous file stays in force until the new one has
parsed, so a bad download never leaves the book without margin.

WHICH FILE IS A BROKER-PARITY DECISION, not a detail. NSE publishes, per
session D: `nsccl.D.i1.zip` (beginning of day, out the evening before),
`i2`..`i5` (intraday snapshots), and `nsccl.D.s.zip` (end of day). Names
verified 2026-11-24 by probing the archive.

`options.span_snapshot`:
  * `beginning_of_day` (default) -- the `i1` file of the latest session on or
    before today. This is what DHAN margins against: on 2026-11-24 (a market
    holiday) its public calculator reproduced the 2026-11-23 `i01` file's
    scan risk plus option value plus exposure to 0.64% mean error across 25
    contracts, and NOT the newer 25-Nov file Zerodha had already moved to.
  * `latest` -- the newest file of any kind, which is what Zerodha's
    calculator matched to the rupee the same evening.

INTRADAY SNAPSHOTS (Phase 5). NSE published the 23-Nov-2026 files at (IST):
i1 20:58 the previous Friday, i2 11:08, i3 12:37, i4 14:08, i5 15:46, s 17:12
-- read from the archive's Last-Modified headers on 2026-11-25. Under
`latest` the book is re-margined on each one, the way an exchange-mandated
intraday re-margin works; the risk desk reads `current()` every pass, so a
snapshot that widens risk can produce a shortfall the next pass acts on.
Rather than find a snapshot up to half an hour late, the refresher also wakes
just after each publication time (`options.span_snapshot_times`).

Whether DHAN re-margins on the intraday snapshots is NOT yet measured: the
2026-11-24 calibration ran on a market holiday, when there were none. The
default therefore stays `beginning_of_day` until Dhan's calculator is
compared against i1 and i2 after 11:10 on a trading day.

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
FILE_PATTERN = re.compile(r"nsccl\.(\d{8})\.(s|i[1-5])\.zip$")
# Within one session, later snapshots sort after earlier ones; `s` is last.
VARIANT_ORDER = {"i1": 1, "i2": 2, "i3": 3, "i4": 4, "i5": 5, "s": 6}
BEGINNING_OF_DAY = "beginning_of_day"
LATEST = "latest"
LOOKBACK_DAYS = 7


def _cache_dir() -> str:
    return config_utils.get_property_value("options.span_cache_dir", "./data/span")


def _interval() -> int:
    return config_utils.get_property_value_int("options.span_refresh_interval_seconds", 1800)


SNAPSHOT_LABELS = {
    "i1": "beginning of day",
    "i2": "intraday snapshot 1 (about 11:00)",
    "i3": "intraday snapshot 2 (about 12:30)",
    "i4": "intraday snapshot 3 (about 14:00)",
    "i5": "intraday snapshot 4 (after the close)",
    "s": "end of day",
}
DEFAULT_WAKE_TIMES = ("11:10", "12:40", "14:10", "15:50", "17:15", "21:05")


def wake_times() -> list:
    raw = config_utils.get_property_value("options.span_snapshot_times", "")
    items = [one.strip() for one in str(raw).strip("[]").replace("'", "").replace('"', "").split(",") if one.strip()]
    parsed = []
    for item in items or DEFAULT_WAKE_TIMES:
        hour, minute = item.split(":")
        parsed.append((int(hour), int(minute)))
    return sorted(parsed)


def seconds_until_next_pass(now: datetime, interval: int) -> float:
    """The regular interval, or sooner if a snapshot is due before then."""
    best = float(interval)
    for offset in range(0, 8):
        day = (now + timedelta(days=offset)).date()
        if day.weekday() >= 5:
            continue
        for hour, minute in wake_times():
            moment = now.replace(year=day.year, month=day.month, day=day.day, hour=hour, minute=minute,
                                 second=0, microsecond=0)
            if moment > now:
                return max(30.0, min(best, (moment - now).total_seconds()))
    return best


def snapshot_policy() -> str:
    value = str(config_utils.get_property_value("options.span_snapshot", BEGINNING_OF_DAY))
    return value if value in (BEGINNING_OF_DAY, LATEST) else BEGINNING_OF_DAY


def refresh_enabled() -> bool:
    return config_utils.get_property_value_boolean("options.span_refresh_enabled", True)


class SpanStore:
    def __init__(self) -> None:
        self.data: Optional[span_file.SpanData] = None
        self.loaded_from: Optional[str] = None
        self.loaded_key: Optional[tuple] = None
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
            "snapshot": self.loaded_key[1] if self.loaded_key else None,
            "snapshotLabel": SNAPSHOT_LABELS.get(self.loaded_key[1]) if self.loaded_key else None,
            "policy": snapshot_policy(),
            "created": data.created if data else None,
            "contracts": len(data.contracts) if data else 0,
            "loadedAt": self.loaded_at.isoformat() if self.loaded_at else None,
            "lastAttemptAt": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "lastError": self.last_error,
            "unsupported": list(data.unsupported[:10]) if data else [],
            "refreshEnabled": refresh_enabled(),
        }

    # --- loading -------------------------------------------------------------
    def _local_files(self) -> Dict[tuple, str]:
        """{(session date, variant): path} for every zip in the cache."""
        found: Dict[tuple, str] = {}
        for path in glob.glob(os.path.join(_cache_dir(), "nsccl.*.zip")):
            match = FILE_PATTERN.search(os.path.basename(path))
            if match:
                day = datetime.strptime(match.group(1), "%Y%m%d").date()
                found[(day, match.group(2))] = path
        return found

    @staticmethod
    def _choose(local: Dict[tuple, str], today: date, policy: str) -> Optional[tuple]:
        if not local:
            return None
        if policy == BEGINNING_OF_DAY:
            sessions = [key for key in local if key[1] == "i1" and key[0] <= today]
            if sessions:
                return max(sessions)
        # LATEST, or no beginning-of-day file at all: the newest of anything.
        return max(local, key=lambda key: (key[0], VARIANT_ORDER.get(key[1], 0)))

    @staticmethod
    def _candidates(today: date, policy: str):
        """Files worth asking NSE for, most wanted first."""
        days = [today - timedelta(days=offset) for offset in range(LOOKBACK_DAYS)]
        days = [day for day in days if day.weekday() < 5]
        if policy == BEGINNING_OF_DAY:
            return [(day, "i1") for day in days]
        wanted = [(today + timedelta(days=1), "i1")] if (today + timedelta(days=1)).weekday() < 5 else []
        for day in days:
            wanted += [(day, variant) for variant in ("s", "i5", "i4", "i3", "i2", "i1")]
        return wanted

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
        """Put the right file in force; fetch it from NSE if allowed and missing."""
        async with self._lock:
            self.last_attempt_at = ist_now()
            os.makedirs(_cache_dir(), exist_ok=True)
            policy = snapshot_policy()
            today = ist_now().date()
            local = self._local_files()
            wanted = self._choose(local, today, policy)
            if download:
                # Candidates come in PREFERENCE order, so the first one that is
                # either already here or downloadable is the one to use. An
                # "is it newer" comparison was wrong for beginning-of-day: it
                # ranked Monday's end-of-day file above Monday's i1 and never
                # fetched the file Dhan actually uses (found live 2026-11-24).
                for day, variant in self._candidates(today, policy):
                    key = (day, variant)
                    if key in local:
                        break
                    destination = os.path.join(_cache_dir(), f"nsccl.{day:%Y%m%d}.{variant}.zip")
                    try:
                        await nse_span_client.download(day, destination, variant=variant)
                        local[key] = destination
                        break
                    except nse_span_client.SpanNotPublished:
                        continue
                    except nse_span_client.SpanDownloadError as error:
                        self.last_error = str(error)
                        logger.warning("%s", error)
                        break
                wanted = self._choose(local, today, policy)
            if wanted is None:
                if self.last_error is None:
                    self.last_error = "No NSE SPAN file is available yet."
                return self.status()
            if self.loaded_key != wanted:
                try:
                    await self._load_zip(wanted[0], local[wanted])
                    self.loaded_key = wanted
                    self.last_error = None
                except Exception as error:  # noqa: BLE001 - keep the old file in force
                    self.last_error = f"Could not parse the {wanted[0]} {wanted[1]} SPAN file: {type(error).__name__}"
                    logger.exception("Parsing the NSE SPAN file %s failed", wanted)
            return self.status()

    # --- the task --------------------------------------------------------------
    async def _run(self) -> None:
        from src.options.services.contract_limits import get_contract_limits

        while True:
            try:
                await self.refresh(download=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the task must not die
                logger.exception("SPAN refresh pass failed")
            try:
                # Same host, same cadence, same task: NSE's F&O contract file
                # carries the freeze quantity and price range (D6).
                await get_contract_limits().refresh(download=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Contract-limits refresh pass failed")
            await asyncio.sleep(seconds_until_next_pass(ist_now(), _interval()))

    async def start(self) -> None:
        if not refresh_enabled():
            # Still load whatever is on disk, so margin works offline.
            from src.options.services.contract_limits import get_contract_limits

            await self.refresh(download=False)
            await get_contract_limits().refresh(download=False)
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
    store = get_span_store()
    store.data = data
    store.loaded_key = (data.business_date, "test") if data else None
