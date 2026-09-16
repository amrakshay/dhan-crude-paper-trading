"""Fill simulation against the live book.

This module decides whether a paper order fills, at what price, and for how
much. A paper-trading tool that assumes perfect fills at the last traded price
is worse than useless -- it teaches a strategy that does not survive contact
with a real book. So the modelling here errs deliberately on the pessimistic
side:

* **Market orders cross the spread.** A buy pays the ask, a sell hits the bid --
  never the LTP.
* **Large orders walk the book.** Each depth level supplies only the quantity
  displayed at it; the order consumes levels in order and the average fill price
  degrades accordingly.
* **Depth is finite.** With only five levels published, an order larger than the
  visible book partially fills. The remainder does not get invented.
* **Slippage is added on top.** `trading.slippage_ticks` models queue position
  and latency between the book snapshot and the notional exchange round trip.
* **Resting limit orders require the market to trade THROUGH them**, not merely
  to touch them. Touching your price means you joined the back of a queue at
  that price; assuming a fill there is the single most flattering error a paper
  simulator can make. Requiring a strict cross under-fills slightly, which is
  the safe direction to be wrong in.

Everything here is a pure function of a depth snapshot, so it is fully testable
without a feed.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, List, Optional, Sequence, Tuple

from src.constants import OrderSide
from src.logging_config import get_logger

logger = get_logger("orders.fill")

# A depth level as delivered by the feed:
# (bid_qty, ask_qty, bid_orders, ask_orders, bid_price, ask_price)
DepthLevel = Tuple[int, int, int, int, float, float]


@dataclass
class SimulatedFill:
    price: Decimal
    quantity: int
    book_level: int
    slippage_ticks: int = 0
    reference_price: Optional[Decimal] = None


@dataclass
class FillResult:
    fills: List[SimulatedFill] = field(default_factory=list)
    rejection_reason: Optional[str] = None
    note: str = ""

    @property
    def filled_quantity(self) -> int:
        return sum(fill.quantity for fill in self.fills)

    @property
    def average_price(self) -> Optional[Decimal]:
        total = self.filled_quantity
        if total == 0:
            return None
        value = sum(fill.price * fill.quantity for fill in self.fills)
        return (value / Decimal(total)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    @property
    def is_filled(self) -> bool:
        return self.filled_quantity > 0


PRICE_QUANTUM = Decimal("0.0001")


def _as_decimal(value: Any) -> Optional[Decimal]:
    """Convert a feed price to Decimal, quantised to 4dp.

    Depth prices arrive as IEEE floats off the wire, so Decimal(str(x)) can
    yield 50.900000000000004. Quantising here keeps that noise out of fill
    prices, event messages and charge bases alike.
    """
    if value is None:
        return None
    return Decimal(str(value)).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def extract_side_levels(
    depth: Optional[Sequence[DepthLevel]], side: str
) -> List[Tuple[Decimal, int]]:
    """The levels an aggressive order on `side` would consume.

    A BUY lifts the ASK side; a SELL hits the BID side. Returns
    [(price, quantity), ...] already ordered from the touch outwards, with
    empty or zero-priced levels dropped.
    """
    if not depth:
        return []

    levels: List[Tuple[Decimal, int]] = []
    for entry in depth:
        if entry is None or len(entry) < 6:
            continue
        bid_qty, ask_qty, _bid_orders, _ask_orders, bid_price, ask_price = entry[:6]
        if str(side).upper() == OrderSide.BUY.value:
            price, quantity = _as_decimal(ask_price), int(ask_qty or 0)
        else:
            price, quantity = _as_decimal(bid_price), int(bid_qty or 0)
        if price is None or price <= 0 or quantity <= 0:
            continue
        levels.append((price, quantity))

    # The feed publishes levels from the touch outwards already, but sort
    # defensively: a mis-ordered book would otherwise produce a better average
    # fill than reality.
    levels.sort(key=lambda item: item[0], reverse=str(side).upper() == OrderSide.SELL.value)
    return levels


def apply_slippage(
    price: Decimal, side: str, slippage_ticks: int, tick_size: Decimal
) -> Decimal:
    """Move the price adversely by `slippage_ticks`.

    Adverse always means worse for the trader: a buy pays more, a sell receives
    less.
    """
    if slippage_ticks <= 0 or tick_size <= 0:
        return price
    adjustment = Decimal(slippage_ticks) * tick_size
    if str(side).upper() == OrderSide.BUY.value:
        adjusted = price + adjustment
    else:
        adjusted = max(tick_size, price - adjustment)
    return adjusted.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def simulate_marketable_fill(
    side: str,
    quantity: int,
    depth: Optional[Sequence[DepthLevel]],
    tick_size: Decimal,
    slippage_ticks: int = 0,
    limit_price: Optional[Decimal] = None,
    allow_partial: bool = True,
) -> FillResult:
    """Walk the book for an order that is crossing the spread now.

    Used for market orders, and for limit orders that are marketable the moment
    they are placed. When `limit_price` is given, levels worse than it are not
    consumed -- that is what makes it a limit rather than a market order.
    """
    result = FillResult()

    if quantity <= 0:
        result.rejection_reason = "Order quantity must be positive"
        return result

    levels = extract_side_levels(depth, side)
    if not levels:
        result.rejection_reason = (
            "No depth on the opposite side of the book; nothing to fill against"
        )
        return result

    is_buy = str(side).upper() == OrderSide.BUY.value
    remaining = quantity

    for index, (level_price, level_quantity) in enumerate(levels, start=1):
        if remaining <= 0:
            break

        fill_price = apply_slippage(level_price, side, slippage_ticks, tick_size)

        if limit_price is not None:
            # Slippage may push the effective price through the limit. A real
            # limit order would not fill there, so neither does this one.
            if is_buy and fill_price > limit_price:
                break
            if not is_buy and fill_price < limit_price:
                break

        take = min(remaining, level_quantity)
        result.fills.append(
            SimulatedFill(
                price=fill_price,
                quantity=take,
                book_level=index,
                slippage_ticks=slippage_ticks,
                reference_price=level_price,
            )
        )
        remaining -= take

    if remaining > 0:
        if not result.fills:
            result.rejection_reason = (
                "No level within the limit price had displayed quantity"
                if limit_price is not None
                else "The book had no displayed quantity to fill against"
            )
        elif not allow_partial:
            # Rather than pretend, drop the fills and say why.
            result.fills = []
            result.rejection_reason = (
                f"Insufficient depth: only {quantity - remaining} of {quantity} "
                "could be filled and partial fills are disabled"
            )
        else:
            result.note = (
                f"Partially filled {quantity - remaining} of {quantity}: the "
                f"visible book ran out of quantity"
            )

    return result


def try_fill_resting_limit(
    side: str,
    remaining_quantity: int,
    limit_price: Decimal,
    depth: Optional[Sequence[DepthLevel]],
    tick_size: Decimal,
    requires_cross: bool = True,
) -> FillResult:
    """Attempt to fill a limit order already resting in the book.

    `requires_cross=True` (the default and the honest setting) fills only when
    the opposite touch trades strictly THROUGH the limit price:

        a resting BUY at 50 fills when the ask drops below 50, not when it
        reaches 50.

    At exactly 50 the order would have joined a queue of other bids at 50 and
    would fill only when enough of that queue ahead of it traded. Since the feed
    publishes no queue position, assuming a fill on touch would systematically
    flatter every limit strategy. Setting `requires_cross=False` restores the
    optimistic touch-fills behaviour, which is left available but is not the
    default.

    No slippage is applied here: a resting order that does fill gets its limit
    price, which is the one case where the trader is not crossing the spread.
    """
    result = FillResult()
    if remaining_quantity <= 0:
        return result

    levels = extract_side_levels(depth, side)
    if not levels:
        return result

    is_buy = str(side).upper() == OrderSide.BUY.value
    remaining = remaining_quantity

    for index, (level_price, level_quantity) in enumerate(levels, start=1):
        if remaining <= 0:
            break

        if requires_cross:
            crossed = level_price < limit_price if is_buy else level_price > limit_price
        else:
            crossed = level_price <= limit_price if is_buy else level_price >= limit_price
        if not crossed:
            break

        take = min(remaining, level_quantity)
        # A resting order fills at its own limit, not at the better price the
        # market traded through: price improvement would need queue priority we
        # cannot observe.
        result.fills.append(
            SimulatedFill(
                price=limit_price,
                quantity=take,
                book_level=index,
                slippage_ticks=0,
                reference_price=level_price,
            )
        )
        remaining -= take

    if result.fills and remaining > 0:
        result.note = (
            f"Partially filled {remaining_quantity - remaining} of "
            f"{remaining_quantity} against the quantity displayed through the limit"
        )
    return result


def is_marketable(
    side: str, limit_price: Decimal, depth: Optional[Sequence[DepthLevel]]
) -> bool:
    """Would this limit order cross immediately on arrival?"""
    levels = extract_side_levels(depth, side)
    if not levels:
        return False
    touch = levels[0][0]
    if str(side).upper() == OrderSide.BUY.value:
        return limit_price >= touch
    return limit_price <= touch
