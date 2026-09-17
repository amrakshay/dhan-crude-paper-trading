"""P&L reporting.

Realised P&L is computed by REPLAYING every fill in chronological order through
the same weighted-average logic the live position book uses. That matters for
two reasons:

* the report and the positions screen can never disagree, because they apply
  identical rules; and
* replaying produces a realisation EVENT with a timestamp, a strike and an
  expiry attached, which is what makes "by day / by expiry / by strike" and the
  equity curve possible at all. A stored running total could not be sliced.

Gross vs net: realised P&L is computed gross of charges, and charges are
attributed separately per order. Net is gross minus the charges of the orders
that produced it. Both are always reported -- a paper-trading tool that quotes
only gross P&L is lying by omission, since charges are frequently the
difference between a winning and a losing strategy at this size.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from src.constants import OrderSide
from src.core.time_utils import to_ist
from src.logging_config import get_logger
from src.orders.database.db_operations.order_repository import OrderRepository

logger = get_logger("reports.pnl")

MONEY = Decimal("0.01")
# Prices carry four decimals, money two. The replay MUST quantise the running
# average price at the same precision the live position book uses
# (src.positions.services.position_service), or the report and the positions
# screen will disagree on realised P&L by a few paise per round trip.
PRICE = Decimal("0.0001")
ZERO = Decimal("0")


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _qp(value: Decimal) -> Decimal:
    return Decimal(value).quantize(PRICE, rounding=ROUND_HALF_UP)


@dataclass
class RealisationEvent:
    """One realised P&L event, produced when a fill reduces a position."""

    at: datetime
    security_id: str
    trading_symbol: str
    expiry_date: Optional[date]
    strike_price: Optional[Decimal]
    option_type: Optional[str]
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    order_id: int


@dataclass
class _RunningPosition:
    net_quantity: int = 0
    average_price: Decimal = ZERO


@dataclass
class PnlReport:
    realised_gross: Decimal = ZERO
    total_charges: Decimal = ZERO
    realised_net: Decimal = ZERO
    unrealised: Optional[Decimal] = None
    net_including_unrealised: Optional[Decimal] = None
    charge_components: Dict[str, Decimal] = field(default_factory=dict)
    events: List[RealisationEvent] = field(default_factory=list)
    by_day: List[Dict[str, Any]] = field(default_factory=list)
    by_expiry: List[Dict[str, Any]] = field(default_factory=list)
    by_strike: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    open_positions_without_marks: int = 0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0


class PnlService:
    def __init__(self, order_repository: OrderRepository, book=None):
        self.orders = order_repository
        self._book = book

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- core replay -------------------------------------------------------
    async def replay_fills(
        self,
        security_id: Optional[str] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        strategy_key: Optional[str] = None,
    ) -> tuple[List[RealisationEvent], Dict[str, _RunningPosition]]:
        """Walk every fill oldest-first, emitting a realisation on each reduce."""
        rows = await self.orders.list_fills(
            security_id=security_id, placed_from=placed_from, placed_to=placed_to,
            strategy_key=strategy_key,
        )

        positions: Dict[str, _RunningPosition] = defaultdict(_RunningPosition)
        events: List[RealisationEvent] = []

        for fill, order in rows:
            key = order.security_id
            position = positions[key]
            quantity = int(fill.quantity)
            price = Decimal(str(fill.price))
            signed = quantity if order.side == OrderSide.BUY.value else -quantity

            net = position.net_quantity
            if net == 0 or (net > 0) == (signed > 0):
                # Opening or adding.
                total = position.average_price * abs(net) + price * quantity
                position.net_quantity = net + signed
                position.average_price = (
                    _qp(total / Decimal(abs(position.net_quantity)))
                    if position.net_quantity
                    else ZERO
                )
                continue

            closing = min(quantity, abs(net))
            entry = position.average_price
            gross = (
                (price - entry) * closing if net > 0 else (entry - price) * closing
            )
            events.append(
                RealisationEvent(
                    at=fill.fill_at,
                    security_id=key,
                    trading_symbol=order.trading_symbol,
                    expiry_date=order.expiry_date,
                    strike_price=order.strike_price,
                    option_type=order.option_type,
                    quantity=closing,
                    entry_price=entry,
                    exit_price=price,
                    gross_pnl=_q(gross),
                    order_id=order.id,
                )
            )

            position.net_quantity = net + signed
            if quantity > closing:
                # Reversal: the residual opens at this price.
                position.average_price = price
            elif position.net_quantity == 0:
                position.average_price = ZERO

        return events, positions

    # --- charges -----------------------------------------------------------
    async def collect_charges(
        self,
        security_id: Optional[str] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        strategy_key: Optional[str] = None,
    ) -> tuple[Dict[str, Decimal], Dict[date, Decimal], Decimal]:
        """Charge totals overall, and per IST calendar day."""
        orders, _total = await self.orders.list_orders(
            security_id=security_id,
            strategy_key=strategy_key,
            placed_from=placed_from,
            placed_to=placed_to,
            page=0,
            size=100000,
        )
        charge_map = await self.orders.list_charges_for_orders(
            [order.id for order in orders]
        )

        components: Dict[str, Decimal] = {
            "brokerage": ZERO, "ctt": ZERO, "exchangeTransactionCharge": ZERO,
            "sebiTurnoverFee": ZERO, "stampDuty": ZERO, "gst": ZERO,
        }
        by_day: Dict[date, Decimal] = defaultdict(lambda: ZERO)
        total = ZERO

        for order in orders:
            charge = charge_map.get(order.id)
            if charge is None:
                continue
            components["brokerage"] += Decimal(str(charge.brokerage or 0))
            components["ctt"] += Decimal(str(charge.ctt or 0))
            components["exchangeTransactionCharge"] += Decimal(
                str(charge.exchange_transaction_charge or 0)
            )
            components["sebiTurnoverFee"] += Decimal(str(charge.sebi_turnover_fee or 0))
            components["stampDuty"] += Decimal(str(charge.stamp_duty or 0))
            components["gst"] += Decimal(str(charge.gst or 0))

            order_total = Decimal(str(charge.total_charges or 0))
            total += order_total
            by_day[to_ist(order.placed_at).date()] += order_total

        return (
            {key: _q(value) for key, value in components.items()},
            {key: _q(value) for key, value in by_day.items()},
            _q(total),
        )

    # --- unrealised --------------------------------------------------------
    def _mark(self, security_id: str) -> Optional[Decimal]:
        row = self.book.get(security_id)
        if not row:
            return None
        if row.get("ltp"):
            return Decimal(str(row["ltp"])).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        bid, ask = row.get("bid"), row.get("ask")
        if bid and ask:
            return ((Decimal(str(bid)) + Decimal(str(ask))) / 2).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        return None

    def compute_unrealised(
        self, positions: Dict[str, _RunningPosition]
    ) -> tuple[Optional[Decimal], int]:
        """Mark-to-market on whatever is still open after the replay."""
        total = ZERO
        marked = 0
        unmarked = 0
        for security_id, position in positions.items():
            if position.net_quantity == 0:
                continue
            mark = self._mark(security_id)
            if mark is None:
                unmarked += 1
                continue
            total += (mark - position.average_price) * position.net_quantity
            marked += 1
        return (_q(total) if marked else None), unmarked

    # --- aggregation -------------------------------------------------------
    @staticmethod
    def _aggregate(events: List[RealisationEvent], key_fn, label_fn) -> List[Dict[str, Any]]:
        buckets: Dict[Any, Dict[str, Any]] = {}
        for event in events:
            key = key_fn(event)
            bucket = buckets.setdefault(
                key,
                {
                    "key": label_fn(key),
                    "grossPnl": ZERO,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "quantity": 0,
                },
            )
            bucket["grossPnl"] += event.gross_pnl
            bucket["trades"] += 1
            bucket["quantity"] += event.quantity
            if event.gross_pnl > 0:
                bucket["wins"] += 1
            elif event.gross_pnl < 0:
                bucket["losses"] += 1

        rows = []
        for bucket in buckets.values():
            bucket["grossPnl"] = _q(bucket["grossPnl"])
            rows.append(bucket)
        return sorted(rows, key=lambda row: str(row["key"]))

    @staticmethod
    def build_equity_curve(
        events: List[RealisationEvent], charges_by_day: Dict[date, Decimal]
    ) -> List[Dict[str, Any]]:
        """Cumulative realised P&L over time, gross and net of charges.

        Points are placed at realisation events. Charges are applied on the day
        they were incurred, so the net line steps down even on days with no
        realisation (for example a day that only opened positions).
        """
        by_day_gross: Dict[date, Decimal] = defaultdict(lambda: ZERO)
        for event in events:
            by_day_gross[to_ist(event.at).date()] += event.gross_pnl

        all_days = sorted(set(by_day_gross) | set(charges_by_day))
        curve = []
        cumulative_gross = ZERO
        cumulative_net = ZERO
        for day in all_days:
            gross = by_day_gross.get(day, ZERO)
            charges = charges_by_day.get(day, ZERO)
            cumulative_gross += gross
            cumulative_net += gross - charges
            curve.append(
                {
                    "date": day.isoformat(),
                    "grossPnl": _q(gross),
                    "charges": _q(charges),
                    "netPnl": _q(gross - charges),
                    "cumulativeGross": _q(cumulative_gross),
                    "cumulativeNet": _q(cumulative_net),
                }
            )
        return curve

    # --- report ------------------------------------------------------------
    async def build_report(
        self,
        security_id: Optional[str] = None,
        placed_from: Optional[datetime] = None,
        placed_to: Optional[datetime] = None,
        strategy_key: Optional[str] = None,
    ) -> PnlReport:
        # A DISABLED strategy's history is still reported. Its totals must not
        # move when a toggle does (decision 3), so nothing here filters on
        # whether a strategy is running -- only on which one is asked for.
        events, positions = await self.replay_fills(
            security_id, placed_from, placed_to, strategy_key
        )
        components, charges_by_day, total_charges = await self.collect_charges(
            security_id, placed_from, placed_to, strategy_key
        )
        unrealised, unmarked = self.compute_unrealised(positions)

        realised_gross = _q(sum((event.gross_pnl for event in events), ZERO))
        realised_net = _q(realised_gross - total_charges)

        report = PnlReport(
            realised_gross=realised_gross,
            total_charges=total_charges,
            realised_net=realised_net,
            unrealised=unrealised,
            net_including_unrealised=(
                _q(realised_net + unrealised) if unrealised is not None else None
            ),
            charge_components=components,
            events=events,
            by_day=self._aggregate(
                events, lambda e: to_ist(e.at).date(), lambda key: key.isoformat()
            ),
            by_expiry=self._aggregate(
                events,
                lambda e: e.expiry_date,
                lambda key: key.isoformat() if key else "unknown",
            ),
            by_strike=self._aggregate(
                events,
                lambda e: (e.strike_price, e.option_type),
                lambda key: (
                    f"{int(key[0])} {key[1]}" if key[0] is not None else "unknown"
                ),
            ),
            equity_curve=self.build_equity_curve(events, charges_by_day),
            open_positions_without_marks=unmarked,
            trade_count=len(events),
            win_count=sum(1 for event in events if event.gross_pnl > 0),
            loss_count=sum(1 for event in events if event.gross_pnl < 0),
        )
        return report
