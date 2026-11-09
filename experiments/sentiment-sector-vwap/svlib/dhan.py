"""Dhan market data for the experiment: 5-minute bars, security ids, live LTP.

MARKET DATA ONLY, through the application's own modules: `DhanChartsClient`
for /charts/intraday and `feed_protocol` for the WebSocket. No new Dhan URL,
and nothing here can reach an order, funds or holdings endpoint -- this
experiment's "orders" are rows in a CSV.

Credentials are the ones stored from the app's Settings page. The running
server renews the token when under six hours remain and renewal ROTATES it,
so `reload_credentials()` is called every cycle rather than once at start.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
import time as wall
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.app_utils import load_config_properties  # noqa: E402

load_config_properties()

from src import config_utils  # noqa: E402
from src.constants import FEED_REQUEST_TICKER, FEED_UNSUBSCRIBE_OFFSET, PACKET_SERVER_DISCONNECT, PACKET_TICKER  # noqa: E402
from src.core.time_utils import to_ist  # noqa: E402
from src.database.session import session_scope  # noqa: E402
from src.market.services.dhan_charts_client import ChartsRateLimited, DhanChartsClient  # noqa: E402
from src.market.services.feed_protocol import build_subscribe_message, chunk_instruments, parse_frame  # noqa: E402
from src.settings.database.db_operations.app_setting_repository import AppSettingRepository  # noqa: E402
from src.settings.services.settings_service import SettingsService  # noqa: E402

from .engine import Bar  # noqa: E402

log = logging.getLogger("sv.dhan")
DB = BACKEND / "data" / "paper_trading.db"
NIFTY_INDEX = ("13", "IDX_I", "INDEX")   # Dhan's Nifty 50 index id, per the chart docs


async def reload_credentials() -> None:
    async with session_scope() as session:
        await SettingsService(AppSettingRepository(session)).apply_to_config()


def security_ids(symbols) -> dict[str, str]:
    """NSE cash-segment security ids from the app's instrument master (read-only)."""
    symbols = list(symbols)
    if not symbols:
        return {}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        q = ("SELECT underlying_symbol, security_id FROM instruments WHERE exchange_segment='NSE_EQ' "
             "AND instrument_type='EQUITY' AND is_active=1 AND underlying_symbol IN (%s)"
             % ",".join("?" * len(symbols)))
        return {s: str(i) for s, i in conn.execute(q, symbols).fetchall()}
    finally:
        conn.close()


def _to_bar(c) -> Bar:
    start = to_ist(datetime.utcfromtimestamp(c.time)).replace(tzinfo=None)
    return Bar(start, c.open, c.high, c.low, c.close, c.volume or 0.0)


class Bars:
    def __init__(self) -> None:
        self.client = DhanChartsClient()

    def has_credentials(self) -> bool:
        return self.client.has_credentials()

    async def fetch(self, security_id: str, since: datetime, until: datetime,
                    segment: str = "NSE_EQ", instrument: str = "EQUITY") -> list[Bar]:
        """5-minute bars whose interval has fully CLOSED by `until` (IST)."""
        for attempt in range(3):
            try:
                candles = await self.client.fetch_intraday(
                    security_id=security_id, exchange_segment=segment, instrument=instrument,
                    interval_minutes=5, from_date=since, to_date=until + timedelta(minutes=5))
                break
            except ChartsRateLimited:
                await asyncio.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"rate limited fetching {security_id}")
        bars = [_to_bar(c) for c in candles]
        return [b for b in bars if b.start + timedelta(minutes=5) <= until]

    async def prev_close(self, security_id: str, day, segment: str = "NSE_EQ",
                         instrument: str = "EQUITY") -> Optional[float]:
        """The previous session's OFFICIAL close, from the daily candle. For F&O
        names that is the closing-auction price, which the last 5-minute bar
        (15:10) is not -- a day change computed from the bar would be wrong."""
        start = datetime.combine(day - timedelta(days=12), datetime.min.time())
        end = datetime.combine(day, datetime.min.time())          # non-inclusive
        for attempt in range(3):
            try:
                candles = await self.client.fetch_daily(security_id, segment, instrument, start, end)
                break
            except ChartsRateLimited:
                await asyncio.sleep(3 * (attempt + 1))
        else:
            return None
        before = [c for c in candles
                  if to_ist(datetime.utcfromtimestamp(c.time)).date() < day]
        return before[-1].close if before else None


class TickerFeed:
    """A small, separate Dhan feed connection in Ticker mode (LTP only).

    Separate from the app's own `DhanFeedClient` because that one is bound to
    the app's MarketBook. This is a second upstream connection; Dhan allows
    five per user and the app holds one. On any error it reconnects with
    backoff; while it is down, stops are still checked on closed bars.
    """

    def __init__(self, on_tick: Callable[[str, float, float], None]) -> None:
        self.on_tick = on_tick
        # Key: the security id for an NSE_EQ stock (as before), "IDX_I:<id>" for an index.
        self.ltp: dict[str, tuple[float, float]] = {}     # key -> (price, wall time)
        self.wanted: set[str] = set()
        self._sent: set[str] = set()
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self.state = "DOWN"
        self.fatal: Optional[str] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="sv-ticker-feed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    def price(self, security_id: str, max_age_s: float = 60.0) -> Optional[float]:
        got = self.ltp.get(security_id)
        if got and wall.time() - got[1] <= max_age_s:
            return got[0]
        return None

    async def set_instruments(self, security_ids: set[str]) -> None:
        self.wanted = set(security_ids)
        if self._ws is not None:
            await self._sync()

    async def _sync(self) -> None:
        add = sorted(self.wanted - self._sent)
        drop = sorted(self._sent - self.wanted)
        pair = (lambda k: tuple(k.split(":", 1)) if ":" in k else ("NSE_EQ", k))
        # Dhan caps one message at 100 instruments.
        for batch in chunk_instruments([pair(k) for k in add], 100):
            await self._ws.send(json.dumps(build_subscribe_message(FEED_REQUEST_TICKER, batch)))
        for batch in chunk_instruments([pair(k) for k in drop], 100):
            await self._ws.send(json.dumps(build_subscribe_message(
                FEED_REQUEST_TICKER + FEED_UNSUBSCRIBE_OFFSET, batch)))
        self._sent = set(self.wanted)

    async def _run(self) -> None:
        from websockets.asyncio.client import connect as ws_connect
        backoff = 2.0
        while True:
            client_id = config_utils.get_property_value("dhan.client_id", "") or ""
            token = config_utils.get_property_value("dhan.access_token", "") or ""
            base = config_utils.get_property_value("dhan.feed_ws_url", "wss://api-feed.dhan.co")
            if not (client_id and token):
                self.state = "NO_CREDENTIALS"
                await asyncio.sleep(30)
                continue
            if not self.wanted:
                # Nothing to watch yet (before 09:45, or a neutral market):
                # holding an idle connection would just time out.
                await asyncio.sleep(5)
                continue
            try:
                url = f"{base}?version=2&token={token}&clientId={client_id}&authType=2"
                async with ws_connect(url, ping_interval=None, close_timeout=5,
                                      max_size=None, open_timeout=15) as ws:
                    self._ws, self._sent, self.state = ws, set(), "UP"
                    log.info("ticker feed connected")
                    backoff = 2.0
                    await self._sync()
                    async for message in asyncio_timeout_iter(ws, 120.0):
                        if isinstance(message, bytes):
                            self._handle(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # The URL carries the token; never log the exception's repr of it.
                log.warning("ticker feed dropped: %s", type(exc).__name__)
            finally:
                self._ws, self.state = None, "DOWN"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def _handle(self, message: bytes) -> None:
        now = wall.time()
        for ptype, fields in parse_frame(message):
            if ptype == PACKET_TICKER:
                sid = str(fields["security_id"])
                if fields.get("segment") == 0:         # IDX_I
                    sid = f"IDX_I:{sid}"
                self.ltp[sid] = (float(fields["ltp"]), now)
                try:
                    self.on_tick(sid, float(fields["ltp"]), now)
                except Exception:  # noqa: BLE001
                    log.exception("tick handler failed")
            elif ptype == PACKET_SERVER_DISCONNECT:
                log.warning("server disconnected ticker feed: %s", fields.get("reason"))


async def asyncio_timeout_iter(ws, timeout: float):
    """Yield messages; raise if the server is silent longer than `timeout`
    (Dhan pings every ~10 s, but pings are not messages; with a few liquid
    names subscribed, two minutes without a tick means a dead link)."""
    while True:
        yield await asyncio.wait_for(ws.recv(), timeout=timeout)
