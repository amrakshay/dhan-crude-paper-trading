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
from typing import Any, Dict, List, Optional, Set

from src import config_utils
from src.logging_config import get_logger
from src.market.services.market_book import MarketBook, now_ms

logger = get_logger("market.broadcaster")


class ClientConnection:
    """One browser WebSocket, with its own bounded outbound queue."""

    __slots__ = (
        "websocket", "queue", "security_ids", "include_depth",
        "needs_snapshot", "dropped", "client_id",
    )

    def __init__(self, websocket, client_id: str, max_queue: int) -> None:
        self.websocket = websocket
        self.client_id = client_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        # None means "everything". A view that only needs the future (the live
        # price page) sets this to one id and stops paying for the chain.
        self.security_ids: Optional[Set[str]] = None
        self.include_depth: bool = True
        self.needs_snapshot: bool = True
        self.dropped: int = 0

    def wants(self, security_id: str) -> bool:
        return self.security_ids is None or security_id in self.security_ids

    def configure(self, security_ids: Optional[List[str]], include_depth: bool) -> None:
        self.security_ids = {str(value) for value in security_ids} if security_ids else None
        self.include_depth = include_depth
        self.needs_snapshot = True


class Broadcaster:
    def __init__(self, book: MarketBook) -> None:
        self.book = book
        self._clients: Set[ClientConnection] = set()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._status_provider = None
        self.broadcasts = 0
        self.rows_sent = 0

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
        while not self._stopping:
            await asyncio.sleep(interval)
            try:
                self._flush()
            except Exception:
                logger.exception("Broadcast flush failed")

    # --- clients -----------------------------------------------------------
    def register(self, websocket, client_id: str) -> ClientConnection:
        client = ClientConnection(websocket, client_id, self._max_queue())
        self._clients.add(client)
        logger.info("Browser client %s connected (%s total)", client_id, len(self._clients))
        return client

    def unregister(self, client: ClientConnection) -> None:
        self._clients.discard(client)
        logger.info(
            "Browser client %s disconnected (%s remaining)", client.client_id, len(self._clients)
        )

    @property
    def client_count(self) -> int:
        return len(self._clients)

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

    def stats(self) -> Dict[str, Any]:
        return {
            "clients": len(self._clients),
            "broadcasts": self.broadcasts,
            "rowsSent": self.rows_sent,
            "intervalMs": config_utils.get_property_value_int(
                "fanout.broadcast_interval_ms", 100
            ),
        }
