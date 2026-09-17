"""Fan-out from the shared in-memory book to browser WebSocket clients.

One upstream connection feeds N browser tabs. Clients never trigger an upstream
connection of their own, and a slow or disconnected tab cannot stall the tick
path: each client owns a bounded queue, and a client that cannot keep up is
resynchronised with a fresh snapshot instead of being allowed to back up.

Coalescing is what keeps this cheap. The book is updated on every tick, but the
socket is written on a fixed interval (default 100ms) carrying only the rows
that actually changed in that window. A contract that ticked 40 times is sent
once, at its latest state.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Set

from src import config_utils
from src.logging_config import get_logger
from src.market.services.market_book import MarketBook, now_ms

logger = get_logger("market.broadcaster")

# Tick visibility without logging on the tick path: the broadcaster already
# wakes on a fixed interval, so aggregate counters are emitted from there
# every SUMMARY_INTERVAL_SECONDS instead of one line per packet.
SUMMARY_INTERVAL_SECONDS = 10.0


class ClientConnection:
    """One browser WebSocket, with its own bounded outbound queue."""

    __slots__ = (
        "websocket", "queue", "security_ids", "include_depth",
        "needs_snapshot", "dropped", "client_id", "connected_at_ms", "user_email",
    )

    def __init__(
        self, websocket, client_id: str, max_queue: int, user_email: Optional[str] = None
    ) -> None:
        self.websocket = websocket
        self.client_id = client_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        # None means "everything". A view that only needs the future (the live
        # price page) sets this to one id and stops paying for the chain.
        self.security_ids: Optional[Set[str]] = None
        self.include_depth: bool = True
        self.needs_snapshot: bool = True
        self.dropped: int = 0
        self.connected_at_ms: int = now_ms()
        # Who opened this socket. The handshake already resolves a principal to
        # make the auth decision; keeping the email means the health page can
        # say whose tab is holding a subscription open instead of listing
        # anonymous ids. It is not used for any authorisation decision -- the
        # socket was authorised once, at the handshake.
        self.user_email: Optional[str] = user_email

    def wants(self, security_id: str) -> bool:
        return self.security_ids is None or security_id in self.security_ids

    def configure(self, security_ids: Optional[List[str]], include_depth: bool) -> None:
        self.security_ids = {str(value) for value in security_ids} if security_ids else None
        self.include_depth = include_depth
        self.needs_snapshot = True
        logger.debug(
            "Client %s configured: securityIds=%s includeDepth=%s (snapshot queued)",
            self.client_id,
            "all" if self.security_ids is None else sorted(self.security_ids),
            include_depth,
        )


class Broadcaster:
    def __init__(self, book: MarketBook) -> None:
        self.book = book
        self._clients: Set[ClientConnection] = set()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._status_provider = None
        self.broadcasts = 0
        self.rows_sent = 0
        # Handshakes refused before a socket existed (bad cookie, expired
        # session, password change owed). These are logged individually but
        # were never counted, so an auth problem looked to the operator like
        # "the page just does not update".
        self.rejected_handshakes = 0
        self._last_summary_ms = 0
        self._summary_baseline = (0, 0)
        self._stale_warned = False

    def set_status_provider(self, provider) -> None:
        """Callable returning the feed status dict pushed alongside updates."""
        self._status_provider = provider

    @staticmethod
    def _interval_seconds() -> float:
        return config_utils.get_property_value_int("fanout.broadcast_interval_ms", 100) / 1000.0

    @staticmethod
    def _max_queue() -> int:
        return config_utils.get_property_value_int("fanout.max_client_queue", 64)

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="broadcaster")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _run(self) -> None:
        interval = self._interval_seconds()
        logger.info(
            "Broadcaster started (flushing every %.0f ms, max client queue %s)",
            interval * 1000, self._max_queue(),
        )
        while not self._stopping:
            await asyncio.sleep(interval)
            try:
                self._flush()
                self._check_staleness()
                self._log_summary()
            except Exception:
                logger.exception("Broadcast flush failed")

    def _check_staleness(self) -> None:
        """Warn once when the book stops ticking, and once when it recovers.

        A stale book that still looks live is the worst failure this tool can
        have, so it is stated in the log as well as in the UI. Edge-triggered:
        a quiet market must not produce a line every 100 ms.
        """
        threshold_ms = config_utils.get_property_value_float(
            "market_feed.stale_tick_warn_seconds", 10.0
        ) * 1000
        age_ms = self.book.last_tick_age_ms()

        if age_ms is None or age_ms <= threshold_ms:
            if self._stale_warned:
                logger.info(
                    "Market book is ticking again after %s ms of silence",
                    age_ms if age_ms is not None else "?",
                )
                self._stale_warned = False
            return

        if not self._stale_warned:
            self._stale_warned = True
            status = self._status_provider() if self._status_provider else {}
            market = (status or {}).get("market") or {}
            logger.warning(
                "Market book is stale: no tick for %.1fs (threshold %.1fs). "
                "MCX open=%s, feed state=%s. Prices on screen are not current.",
                age_ms / 1000, threshold_ms / 1000,
                market.get("isOpen"),
                ((status or {}).get("feed") or {}).get("state"),
            )

    def _log_summary(self) -> None:
        """Aggregate throughput counters, at most one line per interval.

        This is the only place tick volume is logged. The tick path itself
        (feed_protocol -> MarketBook.apply_packet) must stay free of logging --
        see CLAUDE.md section 4.
        """
        if not logger.isEnabledFor(logging.DEBUG):
            return
        now = now_ms()
        if now - self._last_summary_ms < SUMMARY_INTERVAL_SECONDS * 1000:
            return
        elapsed = (now - self._last_summary_ms) / 1000 if self._last_summary_ms else 0
        previous_broadcasts, previous_rows = self._summary_baseline
        self._last_summary_ms = now
        self._summary_baseline = (self.broadcasts, self.rows_sent)
        if elapsed <= 0:
            return
        logger.debug(
            "Fanout in the last %.0fs: %s flushes, %s rows to %s client(s) "
            "(%.1f rows/s); book holds %s instruments",
            elapsed,
            self.broadcasts - previous_broadcasts,
            self.rows_sent - previous_rows,
            len(self._clients),
            (self.rows_sent - previous_rows) / elapsed,
            self.book.stats().get("instruments", "?"),
        )

    # --- clients -----------------------------------------------------------
    def register(
        self, websocket, client_id: str, user_email: Optional[str] = None
    ) -> ClientConnection:
        client = ClientConnection(websocket, client_id, self._max_queue(), user_email)
        self._clients.add(client)
        logger.debug(
            "Registered client %s with a %s-message queue", client_id, self._max_queue()
        )
        logger.info("Browser client %s connected (%s total)", client_id, len(self._clients))
        return client

    def unregister(self, client: ClientConnection) -> None:
        self._clients.discard(client)
        logger.info(
            "Browser client %s disconnected (%s remaining, %s resync(s) during "
            "its session)",
            client.client_id, len(self._clients), client.dropped,
        )

    def record_rejected_handshake(self) -> None:
        """One more socket refused at the handshake. The caller does the logging."""
        self.rejected_handshakes += 1

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def clients(self) -> List[Dict[str, Any]]:
        """One row per connected browser tab, for the system health page.

        This lives here rather than in the health package because `_clients`
        is the broadcaster's own state: reaching into it from outside would
        make the queue an implicit part of the public surface. Everything
        reported already existed on ClientConnection; none of it was reachable.
        """
        rows = []
        for client in list(self._clients):
            rows.append(
                {
                    "clientId": client.client_id,
                    "user": client.user_email,
                    "connectedAtMs": client.connected_at_ms,
                    "connectedForMs": now_ms() - client.connected_at_ms,
                    # None means the tab is taking the whole book.
                    "securityIds": (
                        None if client.security_ids is None else sorted(client.security_ids)
                    ),
                    "includeDepth": client.include_depth,
                    "needsSnapshot": client.needs_snapshot,
                    # How many times this tab fell behind and had its queue
                    # reset. Non-zero means it is losing intermediate updates.
                    "dropped": client.dropped,
                    "queued": client.queue.qsize(),
                }
            )
        rows.sort(key=lambda row: row["connectedAtMs"])
        return rows

    # --- payloads ----------------------------------------------------------
    def _project(self, row: Dict[str, Any], include_depth: bool) -> Dict[str, Any]:
        """Shape a book row for the wire.

        The book stores rows already keyed the way the browser wants them, so
        this is a shallow copy at worst, and drops `depth` for views that do
        not render it (the chain needs best bid/ask, not five levels).
        """
        if include_depth:
            return row
        if row.get("depth") is None:
            return row
        projected = dict(row)
        projected.pop("depth", None)
        return projected

    def build_snapshot(self, client: ClientConnection) -> Dict[str, Any]:
        rows = [
            self._project(row, client.include_depth)
            for row in self.book.snapshot()
            if client.wants(row["securityId"])
        ]
        return {
            "type": "snapshot",
            "ts": now_ms(),
            "rows": rows,
            "status": self._status_provider() if self._status_provider else None,
        }

    def _flush(self) -> None:
        """Push one coalesced batch to every client."""
        changed = self.book.drain_dirty()
        if not self._clients:
            return

        status = self._status_provider() if self._status_provider else None

        for client in list(self._clients):
            if client.needs_snapshot:
                payload = self.build_snapshot(client)
                client.needs_snapshot = False
            else:
                rows = [
                    self._project(row, client.include_depth)
                    for row in changed
                    if client.wants(row["securityId"])
                ]
                # Still send a heartbeat with no rows: the UI uses it to prove
                # the pipe is alive and to age the last-tick indicator.
                payload = {"type": "update", "ts": now_ms(), "rows": rows, "status": status}

            try:
                client.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # The client is not draining. Discard the backlog and mark it
                # for a fresh snapshot: a stale queue would show old prices as
                # though they were current.
                client.dropped += 1
                while not client.queue.empty():
                    try:
                        client.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                client.needs_snapshot = True
                logger.warning(
                    "Client %s is not keeping up (%s resyncs); queue reset",
                    client.client_id, client.dropped,
                )

        self.broadcasts += 1
        self.rows_sent += len(changed)

    async def send_status(self, status: Dict[str, Any]) -> None:
        """Push a status-only message to every client immediately."""
        payload = {"type": "status", "ts": now_ms(), "status": status}
        for client in list(self._clients):
            try:
                client.queue.put_nowait(payload)
            except asyncio.QueueFull:
                client.needs_snapshot = True
                logger.debug(
                    "Client %s queue full while pushing status; snapshot queued",
                    client.client_id,
                )

    def stats(self) -> Dict[str, Any]:
        return {
            "clients": len(self._clients),
            "broadcasts": self.broadcasts,
            "rowsSent": self.rows_sent,
            "intervalMs": config_utils.get_property_value_int(
                "fanout.broadcast_interval_ms", 100
            ),
            "maxClientQueue": self._max_queue(),
            "rejectedHandshakes": self.rejected_handshakes,
            # Total resyncs across every tab currently connected. A client that
            # has since disconnected took its count with it -- the per-client
            # rows in clients() are the live detail.
            "droppedTotal": sum(client.dropped for client in self._clients),
        }
