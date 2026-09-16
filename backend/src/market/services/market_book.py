"""The in-memory live book: the single source of truth for current prices.

Design constraints, in priority order:

1. The tick path must be cheap. Rows are plain dicts keyed the way the browser
   wants them, so a tick costs a dict lookup and a few assignments and the
   broadcast path costs a ``json.dumps`` with no transformation step.
2. Nothing here touches the database. Persisting inside the tick handler would
   put disk I/O on the hot path; orders and fills are persisted, individual
   ticks are not.
3. There is exactly one of these per process, shared by the upstream feed, the
   greeks poller, the fill simulator and every browser client.

Concurrency: every writer runs on the same asyncio event loop, so mutations are
already serialised and no lock is needed. This is only true while the app runs
single-process -- see the workers guard in server.py.
"""
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from src.logging_config import get_logger

logger = get_logger("market.book")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


# Fields carried straight through from a feed packet to the browser row. The
# right-hand side is the browser-facing key, so no rename happens per tick.
_PACKET_TO_ROW = {
    "ltp": "ltp",
    "ltq": "ltq",
    "ltt": "ltt",
    "avg_price": "avgPrice",
    "volume": "volume",
    "total_buy_qty": "totalBuyQty",
    "total_sell_qty": "totalSellQty",
    "oi": "oi",
    "oi_day_high": "oiDayHigh",
    "oi_day_low": "oiDayLow",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "prev_close": "prevClose",
    "prev_oi": "prevOi",
}


class MarketBook:
    """Current state of every subscribed instrument."""

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}
        self._dirty: Set[str] = set()
        self.tick_count: int = 0
        self.last_tick_ms: int = 0
        self.last_packet_type: Optional[int] = None

    # --- static contract metadata -----------------------------------------
    def register_instrument(self, security_id: str, meta: Dict[str, Any]) -> None:
        """Attach contract details so a row can describe itself to the UI."""
        security_id = str(security_id)
        self._meta[security_id] = meta
        row = self._rows.get(security_id)
        if row is None:
            row = self._new_row(security_id)
            self._rows[security_id] = row
        row.update(meta)
        self._dirty.add(security_id)

    def register_many(self, instruments: Dict[str, Dict[str, Any]]) -> None:
        for security_id, meta in instruments.items():
            self.register_instrument(security_id, meta)

    def forget(self, security_ids: Iterable[str]) -> int:
        """Drop instruments that are no longer subscribed."""
        removed = 0
        for security_id in security_ids:
            security_id = str(security_id)
            if self._rows.pop(security_id, None) is not None:
                removed += 1
            self._meta.pop(security_id, None)
            self._dirty.discard(security_id)
        return removed

    def _new_row(self, security_id: str) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "securityId": security_id,
            "ltp": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
            "oi": None,
            "bid": None,
            "ask": None,
            "bidQty": None,
            "askQty": None,
            "depth": None,
            "ts": None,
        }
        meta = self._meta.get(security_id)
        if meta:
            row.update(meta)
        return row

    # --- hot path ----------------------------------------------------------
    def apply_packet(self, fields: Dict[str, Any]) -> Optional[str]:
        """Merge one decoded feed packet. Returns the security id it touched.

        Called once per packet. Keep it allocation-light.
        """
        security_id = str(fields["security_id"])
        row = self._rows.get(security_id)
        if row is None:
            row = self._new_row(security_id)
            self._rows[security_id] = row

        for packet_key, row_key in _PACKET_TO_ROW.items():
            value = fields.get(packet_key)
            if value is not None:
                row[row_key] = value

        depth = fields.get("depth")
        if depth is not None:
            row["depth"] = depth
            # Best bid/ask lifted out of level 1 so the fill simulator and the
            # UI never have to index into the depth array.
            top = depth[0]
            bid_qty, ask_qty, _bid_orders, _ask_orders, bid_price, ask_price = top
            row["bid"] = bid_price
            row["ask"] = ask_price
            row["bidQty"] = bid_qty
            row["askQty"] = ask_qty

        timestamp = now_ms()
        row["ts"] = timestamp
        self.last_tick_ms = timestamp
        self.tick_count += 1
        self._dirty.add(security_id)
        return security_id

    def apply_frame(self, packets: Iterable) -> int:
        """Merge every packet decoded from one WebSocket frame."""
        applied = 0
        for packet_type, fields in packets:
            self.last_packet_type = packet_type
            self.apply_packet(fields)
            applied += 1
        return applied

    def merge_greeks(self, security_id: str, greeks: Dict[str, Any]) -> None:
        """Merge option-chain derived values (IV, greeks, OI change).

        Comes from the 3-second REST poller, never from the tick stream -- the
        WebSocket feed carries no greeks. Deliberately a separate entry point so
        it can never be confused with a tick.
        """
        security_id = str(security_id)
        row = self._rows.get(security_id)
        if row is None:
            row = self._new_row(security_id)
            self._rows[security_id] = row
        row.update(greeks)
        row["greeksTs"] = now_ms()
        self._dirty.add(security_id)

    # --- reads -------------------------------------------------------------
    def get(self, security_id: str) -> Optional[Dict[str, Any]]:
        return self._rows.get(str(security_id))

    def get_many(self, security_ids: Iterable[str]) -> List[Dict[str, Any]]:
        rows = []
        for security_id in security_ids:
            row = self._rows.get(str(security_id))
            if row is not None:
                rows.append(row)
        return rows

    def snapshot(self) -> List[Dict[str, Any]]:
        """Every row. Used to prime a newly connected browser client."""
        return list(self._rows.values())

    def drain_dirty(self) -> List[Dict[str, Any]]:
        """Rows changed since the last drain, clearing the dirty set.

        This is what makes coalescing work: a security that ticked 40 times in
        the last broadcast interval is sent once, at its latest state.
        """
        if not self._dirty:
            return []
        dirty = self._dirty
        self._dirty = set()
        rows = self._rows
        return [rows[security_id] for security_id in dirty if security_id in rows]

    def last_tick_age_ms(self) -> Optional[int]:
        """How stale the book is. A stale book that looks live is the worst
        failure mode this tool has, so this is surfaced all the way to the UI."""
        if not self.last_tick_ms:
            return None
        return now_ms() - self.last_tick_ms

    @property
    def size(self) -> int:
        return len(self._rows)

    def stats(self) -> Dict[str, Any]:
        return {
            "instruments": len(self._rows),
            "tickCount": self.tick_count,
            "lastTickMs": self.last_tick_ms or None,
            "lastTickAgeMs": self.last_tick_age_ms(),
        }

    def clear(self) -> None:
        self._rows.clear()
        self._meta.clear()
        self._dirty.clear()
        self.tick_count = 0
        self.last_tick_ms = 0
