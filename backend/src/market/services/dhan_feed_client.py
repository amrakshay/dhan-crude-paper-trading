"""The single upstream Dhan market-data WebSocket connection.

MARKET DATA ONLY. This client speaks exactly one protocol -- the live feed at
wss://api-feed.dhan.co -- and can send exactly three message shapes: subscribe,
unsubscribe and disconnect. There is no code path from here to any trading,
funds or holdings endpoint, and none may be added (see
tests/test_no_real_orders.py).

Exactly ONE of these exists per process regardless of how many browser tabs are
open. Dhan allows 5 concurrent connections per user and an extra connection
costs both a slot and a hop of latency, so browser clients are fanned out to
from the shared in-memory book instead (see broadcaster.py).
"""
import asyncio
import json
import random
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import websockets
from websockets.asyncio.client import connect as ws_connect

from src import config_utils
from src.constants import (
    FEED_MODE_REQUEST_CODES,
    FEED_REQUEST_DISCONNECT,
    FEED_REQUEST_FULL,
    FEED_UNSUBSCRIBE_OFFSET,
    PACKET_SERVER_DISCONNECT,
    ConnectionState,
)
from src.logging_config import get_logger
from src.market.services.feed_protocol import (
    build_subscribe_message,
    chunk_instruments,
    parse_frame,
)
from src.market.services.market_book import MarketBook, now_ms

logger = get_logger("market.feed")

# Server disconnect codes that mean retrying is pointless until the operator
# fixes something. Reconnecting on these would just spin.
FATAL_DISCONNECT_CODES = {806, 807, 808, 809}


class DhanFeedClient:
    """Maintains one authenticated feed connection, with resubscribe on drop."""

    def __init__(
        self,
        book: MarketBook,
        on_state_change: Optional[Callable[[ConnectionState, Optional[str]], None]] = None,
    ) -> None:
        self.book = book
        self._on_state_change = on_state_change

        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self.state_detail: Optional[str] = None
        self.connected_at_ms: Optional[int] = None
        self.last_message_ms: int = 0
        self.reconnect_count: int = 0
        self.frames_received: int = 0
        self.packets_applied: int = 0

        self._subscribed: Set[Tuple[str, str]] = set()
        self._pending_subscribe: List[Tuple[str, str]] = []
        self._pending_unsubscribe: List[Tuple[str, str]] = []
        self._websocket = None
        self._task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._send_lock = asyncio.Lock()

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _mode() -> str:
        return (config_utils.get_property_value("market_feed.mode", "FULL") or "FULL").upper()

    @classmethod
    def _request_code(cls) -> int:
        return FEED_MODE_REQUEST_CODES.get(cls._mode(), FEED_REQUEST_FULL)

    @staticmethod
    def _batch_size() -> int:
        return config_utils.get_property_value_int(
            "market_feed.max_instruments_per_subscribe", 100
        )

    @staticmethod
    def _inactivity_timeout() -> float:
        return config_utils.get_property_value_float(
            "market_feed.server_inactivity_timeout_seconds", 40.0
        )

    @staticmethod
    def credentials() -> Tuple[str, str]:
        return (
            config_utils.get_property_value("dhan.client_id", "") or "",
            config_utils.get_property_value("dhan.access_token", "") or "",
        )

    @classmethod
    def has_credentials(cls) -> bool:
        client_id, access_token = cls.credentials()
        return bool(client_id and access_token)

    def _build_url(self) -> str:
        client_id, access_token = self.credentials()
        base = config_utils.get_property_value(
            "dhan.feed_ws_url", "wss://api-feed.dhan.co"
        )
        return f"{base}?version=2&token={access_token}&clientId={client_id}&authType=2"

    def _redacted_url(self) -> str:
        client_id, _ = self.credentials()
        base = config_utils.get_property_value("dhan.feed_ws_url", "wss://api-feed.dhan.co")
        return f"{base}?version=2&token=***&clientId={client_id}&authType=2"

    # --- state -------------------------------------------------------------
    def _set_state(self, state: ConnectionState, detail: Optional[str] = None) -> None:
        if self.state == state and self.state_detail == detail:
            return
        self.state = state
        self.state_detail = detail
        logger.info("Feed state -> %s%s", state.value, f" ({detail})" if detail else "")
        if self._on_state_change is not None:
            try:
                self._on_state_change(state, detail)
            except Exception:
                logger.exception("Feed state change callback failed")

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run_forever(), name="dhan-feed")
        self._watchdog_task = asyncio.create_task(self._watchdog(), name="dhan-feed-watchdog")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._watchdog_task, self._task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._watchdog_task = None
        self._task = None

        if self._websocket is not None:
            try:
                await self._websocket.send(json.dumps({"RequestCode": FEED_REQUEST_DISCONNECT}))
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None
        self._set_state(ConnectionState.DISCONNECTED, "stopped")

    async def _run_forever(self) -> None:
        """Connect, and keep reconnecting with exponential backoff and jitter."""
        initial = config_utils.get_property_value_float(
            "market_feed.reconnect_backoff_initial_seconds", 1.0
        )
        maximum = config_utils.get_property_value_float(
            "market_feed.reconnect_backoff_max_seconds", 30.0
        )
        backoff = initial

        while not self._stopping:
            try:
                await self._connect_and_consume()
                backoff = initial          # a clean session resets the backoff
            except asyncio.CancelledError:
                raise
            except _FatalFeedError as exc:
                self._set_state(ConnectionState.DISCONNECTED, str(exc))
                logger.error("Feed stopped, manual intervention needed: %s", exc)
                return
            except Exception as exc:
                self._set_state(ConnectionState.RECONNECTING, str(exc))
                logger.warning(
                    "Feed connection lost (%s: %s); reconnect #%s in %.1fs "
                    "(backoff max %.1fs, %s instruments to resubscribe)",
                    type(exc).__name__, exc, self.reconnect_count + 1,
                    backoff, maximum, len(self._subscribed),
                )

            if self._stopping:
                break

            # Jitter so a restart storm does not hammer the endpoint in lockstep.
            await asyncio.sleep(backoff * (0.8 + 0.4 * random.random()))
            backoff = min(backoff * 2, maximum)
            self.reconnect_count += 1

    async def _connect_and_consume(self) -> None:
        if not self.has_credentials():
            raise _FatalFeedError(
                "DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN are not set. Set them in .env, "
                "or set DHAN_SYNTHETIC_FEED=true to run on locally generated prices."
            )

        self._set_state(ConnectionState.CONNECTING)
        logger.info("Connecting to feed %s", self._redacted_url())

        # ping_interval=None: Dhan drives the keepalive (it pings every ~10s and
        # drops us after 40s of silence). We detect a dead link with our own
        # watchdog on received-data age instead of client-initiated pings, which
        # this endpoint is not documented to answer.
        async with ws_connect(
            self._build_url(),
            ping_interval=None,
            close_timeout=5,
            max_size=None,
            open_timeout=15,
        ) as websocket:
            self._websocket = websocket
            self.connected_at_ms = now_ms()
            self.last_message_ms = now_ms()
            self._set_state(ConnectionState.CONNECTED)

            # Resubscribe everything we had before the drop.
            if self._subscribed:
                logger.info(
                    "Feed connected; resubscribing %s instruments carried over "
                    "from the previous session", len(self._subscribed),
                )
            await self._send_subscriptions(sorted(self._subscribed), subscribe=True)
            await self._flush_pending()

            try:
                async for message in websocket:
                    self.last_message_ms = now_ms()
                    if isinstance(message, bytes):
                        self._handle_binary(message)
                    else:
                        logger.debug("Feed text message: %s", message[:200])
            finally:
                self._websocket = None

    def _handle_binary(self, message: bytes) -> None:
        """Decode a frame and merge it into the book. This is the hot path."""
        self.frames_received += 1
        packets = parse_frame(message)

        for packet_type, fields in packets:
            if packet_type == PACKET_SERVER_DISCONNECT:
                code = fields.get("disconnect_code")
                reason = fields.get("reason")
                logger.error(
                    "Server disconnected the feed: code=%s reason=%s (fatal=%s)",
                    code, reason, code in FATAL_DISCONNECT_CODES,
                )
                if code in FATAL_DISCONNECT_CODES:
                    self._stopping = True
                    self._set_state(ConnectionState.DISCONNECTED, reason)
                return

        self.packets_applied += self.book.apply_frame(packets)

    async def _watchdog(self) -> None:
        """Force a reconnect when a SUBSCRIBED feed goes quiet.

        A connection carrying subscriptions and no traffic for the inactivity
        window is dead even though the socket still looks open. A silently dead
        feed is indistinguishable from a quiet market in the UI, which is
        exactly the failure this tool must not have.

        **Silence only means anything when something is subscribed.** Dhan's
        protocol pings are handled inside the websockets library and never
        reach `async for message in websocket`, so they do not touch
        `last_message_ms`; the only thing that does is a data frame for an
        instrument this client asked for. With an empty subscription set there
        is nothing to send data, so the timer drains on a perfectly healthy
        socket and the watchdog reconnects every 45 seconds for ever.

        That is not hypothetical. It ran 9 times in 6 minutes on 2026-09-18,
        after MCX crude was switched off and the swing rotation -- which
        subscribes only what it holds, and holds nothing yet -- was switched on.
        The churn burns one of Dhan's five connection slots on a loop and
        turns the dead-feed signal into constant noise.

        **Still open, deferred to the scheduler phase:** the same false
        positive fires on a SUBSCRIBED but idle market. MCX closes at 23:30 and
        NSE at 15:30, so a book held across the close reconnects every 45
        seconds until the next open. Fixing it means asking each strategy
        whether its market is currently open -- `market_hours` is on the
        definition already -- which is the knowledge the scheduler introduces,
        so it lands with it rather than as a second half-measure here.
        """
        while not self._stopping:
            await asyncio.sleep(5)
            if self.state != ConnectionState.CONNECTED or self._websocket is None:
                continue
            if not self._subscribed:
                # Nothing was asked for, so nothing arriving proves nothing.
                continue
            silence_seconds = (now_ms() - self.last_message_ms) / 1000
            if silence_seconds > self._inactivity_timeout():
                logger.warning(
                    "No feed traffic for %.0fs (limit %.0fs); forcing reconnect",
                    silence_seconds,
                    self._inactivity_timeout(),
                )
                try:
                    await self._websocket.close()
                except Exception:
                    pass

    # --- subscriptions -----------------------------------------------------
    async def _send_subscriptions(
        self, instruments: Sequence[Tuple[str, str]], subscribe: bool
    ) -> None:
        if not instruments or self._websocket is None:
            return

        request_code = self._request_code()
        if not subscribe:
            request_code += FEED_UNSUBSCRIBE_OFFSET

        logger.debug(
            "%s security ids: %s",
            "Subscribing" if subscribe else "Unsubscribing",
            [security_id for _segment, security_id in instruments],
        )
        async with self._send_lock:
            for batch in chunk_instruments(list(instruments), self._batch_size()):
                message = build_subscribe_message(request_code, batch)
                await self._websocket.send(json.dumps(message))
        logger.info(
            "%s %s instruments (RequestCode=%s)",
            "Subscribed" if subscribe else "Unsubscribed",
            len(instruments),
            request_code,
        )

    async def _flush_pending(self) -> None:
        if self._pending_subscribe:
            pending, self._pending_subscribe = self._pending_subscribe, []
            await self._send_subscriptions(pending, subscribe=True)
        if self._pending_unsubscribe:
            pending, self._pending_unsubscribe = self._pending_unsubscribe, []
            await self._send_subscriptions(pending, subscribe=False)

    async def subscribe(self, instruments: Iterable[Tuple[str, str]]) -> int:
        """Add instruments. Already-subscribed ones are skipped."""
        new = [
            (segment, str(security_id))
            for segment, security_id in instruments
            if (segment, str(security_id)) not in self._subscribed
        ]
        if not new:
            logger.debug("Subscribe requested but every instrument is already subscribed")
            return 0

        limit = config_utils.get_property_value_int(
            "market_feed.max_instruments_per_connection", 5000
        )
        if len(self._subscribed) + len(new) > limit:
            logger.error(
                "Refusing to subscribe %s instruments: would exceed the %s per "
                "connection limit (currently %s)",
                len(new), limit, len(self._subscribed),
            )
            return 0

        self._subscribed.update(new)
        if self.state == ConnectionState.CONNECTED and self._websocket is not None:
            await self._send_subscriptions(new, subscribe=True)
        else:
            self._pending_subscribe.extend(new)
        return len(new)

    async def unsubscribe(self, instruments: Iterable[Tuple[str, str]]) -> int:
        existing = [
            (segment, str(security_id))
            for segment, security_id in instruments
            if (segment, str(security_id)) in self._subscribed
        ]
        if not existing:
            return 0

        self._subscribed.difference_update(existing)
        if self.state == ConnectionState.CONNECTED and self._websocket is not None:
            await self._send_subscriptions(existing, subscribe=False)
        else:
            self._pending_unsubscribe.extend(existing)
        return len(existing)

    @property
    def subscribed_count(self) -> int:
        return len(self._subscribed)

    def subscribed_security_ids(self) -> Set[str]:
        return {security_id for _segment, security_id in self._subscribed}

    def status(self) -> Dict[str, object]:
        return {
            "state": self.state.value,
            "detail": self.state_detail,
            "mode": self._mode(),
            "requestCode": self._request_code(),
            "subscribed": self.subscribed_count,
            "reconnects": self.reconnect_count,
            "framesReceived": self.frames_received,
            "packetsApplied": self.packets_applied,
            "connectedAtMs": self.connected_at_ms,
            "lastMessageAgeMs": (
                now_ms() - self.last_message_ms if self.last_message_ms else None
            ),
        }


class _FatalFeedError(Exception):
    """A failure that reconnecting cannot fix (bad token, no subscription)."""
