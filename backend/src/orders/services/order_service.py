"""Paper order lifecycle.

PAPER RUPEES ONLY. Placing an "order" here writes rows to the local database
and matches them against a snapshot of the in-memory book. Nothing in this
module, or anything it calls, contacts a broker.

Lifecycle:

    PENDING -> FILLED             market order, full fill
            -> PARTIALLY_FILLED   market order, book ran out (stays open)
            -> OPEN               limit order resting away from the touch
            -> REJECTED           no depth, or validation failed
    OPEN / PARTIALLY_FILLED -> FILLED | CANCELLED

Every transition is recorded as an OrderEvent with a millisecond timestamp, and
every execution as an OrderFill. Charges are recomputed on the cumulative
executed quantity after each fill, so a partially filled order always carries
the charges for what has actually executed -- with brokerage counted once,
because brokerage is per executed order rather than per fill.
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from src import config_utils
from src.charges.services.charges_engine import ChargesEngine
from src.constants import OrderSide, OrderStatus, OrderType
from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.orders.database.db_models.order_model import Order
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services import fill_simulator
from src.positions.database.db_operations.position_repository import PositionRepository
from src.positions.services.position_service import PositionService

logger = get_logger("orders.service")

MONEY_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.0001")


class OrderValidationError(Exception):
    pass


class OrderService:
    def __init__(
        self,
        order_repository: OrderRepository,
        instrument_repository: InstrumentRepository,
        position_repository: PositionRepository,
        book=None,
    ):
        self.orders = order_repository
        self.instruments = instrument_repository
        self.positions = PositionService(position_repository)
        self.charges = ChargesEngine()
        self._book = book

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _slippage_ticks() -> int:
        return config_utils.get_property_value_int("trading.slippage_ticks", 0)

    @staticmethod
    def _allow_partial() -> bool:
        return config_utils.get_property_value_boolean("trading.allow_partial_fills", True)

    @staticmethod
    def _reject_market_on_thin_book() -> bool:
        return config_utils.get_property_value_boolean(
            "trading.reject_market_order_on_insufficient_depth", False
        )

    @staticmethod
    def _limit_requires_cross() -> bool:
        return config_utils.get_property_value_boolean(
            "trading.limit_fill_requires_cross", True
        )

    @staticmethod
    def _max_lots() -> int:
        return config_utils.get_property_value_int("trading.max_lots_per_order", 100)

    # --- placement ---------------------------------------------------------
    # Named submit_paper_order / cancel_paper_order rather than the broker-SDK
    # spellings place_order / cancel_order. Those names are banned outright by
    # tests/test_no_real_orders.py, so a genuine broker call can never be
    # introduced under a name that blends into the local code.
    async def submit_paper_order(
        self,
        security_id: str,
        side: str,
        order_type: str,
        lots: int,
        limit_price: Optional[Decimal] = None,
        is_close_order: bool = False,
        quantity_override: Optional[int] = None,
    ) -> Order:
        """Submit a paper order.

        `quantity_override` closes an exact barrel quantity that is not a whole
        number of lots. A market order can partially fill below one lot (the
        visible book simply ran out), and without this the residual position
        would be impossible to close.
        """
        side = str(side).upper()
        order_type = str(order_type).upper()

        instrument = await self.instruments.get_by_security_id(str(security_id))
        if instrument is None:
            raise OrderValidationError(
                f"Unknown security_id {security_id}. Refresh the instrument master."
            )
        if not instrument.is_active:
            raise OrderValidationError(
                f"{instrument.trading_symbol} is no longer active (expired series)."
            )
        if side not in (OrderSide.BUY.value, OrderSide.SELL.value):
            raise OrderValidationError(f"side must be BUY or SELL, got {side!r}")
        if order_type not in (OrderType.MARKET.value, OrderType.LIMIT.value):
            raise OrderValidationError(
                f"order_type must be MARKET or LIMIT, got {order_type!r}"
            )
        if lots <= 0:
            raise OrderValidationError("lots must be positive")
        if lots > self._max_lots():
            raise OrderValidationError(
                f"lots exceeds the configured maximum of {self._max_lots()}"
            )
        if order_type == OrderType.LIMIT.value:
            if limit_price is None or Decimal(str(limit_price)) <= 0:
                raise OrderValidationError("A limit order needs a positive limit price")
            limit_price = self._align_to_tick(Decimal(str(limit_price)), instrument)

        quantity = (
            int(quantity_override)
            if quantity_override is not None
            else lots * int(instrument.lot_size)
        )
        if quantity <= 0:
            raise OrderValidationError("Order quantity must be positive")
        now = utc_now()

        order = Order(
            client_order_id=uuid.uuid4().hex,
            security_id=instrument.security_id,
            trading_symbol=instrument.trading_symbol,
            expiry_date=instrument.expiry_date,
            strike_price=instrument.strike_price,
            option_type=instrument.option_type,
            lot_size=int(instrument.lot_size),
            side=side,
            order_type=order_type,
            lots=lots,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.PENDING.value,
            filled_quantity=0,
            average_fill_price=None,
            is_close_order=is_close_order,
            placed_at=now,
            last_event_at=now,
        )
        self.orders.session.add(order)
        await self.orders.session.flush()
        await self.orders.add_event(
            order, "PLACED", OrderStatus.PENDING.value, now,
            price=limit_price, quantity=quantity,
            message=f"{side} {lots} lot(s) {order_type}",
        )

        await self._attempt_initial_fill(order, instrument)
        return order

    @staticmethod
    def _align_to_tick(price: Decimal, instrument) -> Decimal:
        """Snap a limit price to the contract's tick grid.

        A price off the grid could never rest on a real exchange, and silently
        accepting one would let a paper limit fill where a real one could not.
        """
        tick = instrument.tick_size
        if not tick or Decimal(str(tick)) <= 0:
            return price
        tick = Decimal(str(tick))
        return (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick

    async def _attempt_initial_fill(self, order: Order, instrument) -> None:
        row = self.book.get(order.security_id)
        depth = row.get("depth") if row else None
        tick = Decimal(str(instrument.tick_size or "0.1"))

        if order.order_type == OrderType.MARKET.value:
            result = fill_simulator.simulate_marketable_fill(
                side=order.side,
                quantity=order.quantity,
                depth=depth,
                tick_size=tick,
                slippage_ticks=self._slippage_ticks(),
                allow_partial=(
                    self._allow_partial() and not self._reject_market_on_thin_book()
                ),
            )
            if not result.is_filled:
                await self._reject(
                    order,
                    result.rejection_reason or "No liquidity available to fill against",
                )
                return
            await self._apply_fills(order, result)
            # A market order that could not be completely filled does not rest:
            # there is no price to rest at. It is closed out as partially filled.
            await self._finalise(
                order,
                complete_status=OrderStatus.FILLED.value,
                incomplete_status=OrderStatus.PARTIALLY_FILLED.value,
                terminal_when_incomplete=True,
                message=result.note or None,
            )
            return

        # LIMIT
        marketable = fill_simulator.is_marketable(order.side, order.limit_price, depth)
        if marketable:
            result = fill_simulator.simulate_marketable_fill(
                side=order.side,
                quantity=order.quantity,
                depth=depth,
                tick_size=tick,
                slippage_ticks=self._slippage_ticks(),
                limit_price=order.limit_price,
                allow_partial=self._allow_partial(),
            )
            if result.is_filled:
                await self._apply_fills(order, result)
                await self._finalise(
                    order,
                    complete_status=OrderStatus.FILLED.value,
                    incomplete_status=OrderStatus.PARTIALLY_FILLED.value,
                    terminal_when_incomplete=False,
                    message=result.note or None,
                )
                return

        # Rests in the book until the market trades through it.
        await self._transition(
            order, OrderStatus.OPEN.value, "RESTING",
            message="Resting until the market crosses the limit price",
        )

    # --- matching ----------------------------------------------------------
    async def try_fill_open_orders(self) -> int:
        """Match every resting order against the current book. Returns fills."""
        open_orders = await self.orders.list_open_orders()
        if not open_orders:
            return 0

        filled = 0
        for order in open_orders:
            if order.order_type != OrderType.LIMIT.value:
                continue
            remaining = int(order.quantity) - int(order.filled_quantity or 0)
            if remaining <= 0:
                continue

            row = self.book.get(order.security_id)
            if not row:
                continue
            instrument = await self.instruments.get_by_security_id(order.security_id)
            tick = Decimal(str((instrument.tick_size if instrument else None) or "0.1"))

            result = fill_simulator.try_fill_resting_limit(
                side=order.side,
                remaining_quantity=remaining,
                limit_price=Decimal(str(order.limit_price)),
                depth=row.get("depth"),
                tick_size=tick,
                requires_cross=self._limit_requires_cross(),
            )
            if not result.is_filled:
                continue

            await self._apply_fills(order, result)
            await self._finalise(
                order,
                complete_status=OrderStatus.FILLED.value,
                incomplete_status=OrderStatus.PARTIALLY_FILLED.value,
                terminal_when_incomplete=False,
                message=result.note or None,
            )
            filled += 1

        return filled

    # --- state transitions -------------------------------------------------
    async def _apply_fills(self, order: Order, result: fill_simulator.FillResult) -> None:
        now = utc_now()
        for fill in result.fills:
            await self.orders.add_fill(
                order,
                fill_at=now,
                price=fill.price,
                quantity=fill.quantity,
                book_level=fill.book_level,
                slippage_ticks=fill.slippage_ticks,
                reference_price=fill.reference_price,
            )
            await self.orders.add_event(
                order, "FILL", order.status, now,
                price=fill.price, quantity=fill.quantity,
                message=f"Filled {fill.quantity} at {fill.price} (book level {fill.book_level})",
            )

        previous_quantity = int(order.filled_quantity or 0)
        previous_value = Decimal(str(order.average_fill_price or 0)) * previous_quantity
        new_quantity = previous_quantity + result.filled_quantity
        new_value = previous_value + sum(
            fill.price * fill.quantity for fill in result.fills
        )
        order.filled_quantity = new_quantity
        order.average_fill_price = (new_value / Decimal(new_quantity)).quantize(
            PRICE_QUANTUM, rounding=ROUND_HALF_UP
        )
        order.last_event_at = now

        # Charges are recomputed on the cumulative executed quantity, so read
        # what was already charged before overwriting it -- the difference is
        # what this fill actually added, and is what the position accrues.
        existing = await self.orders.list_charges_for_orders([order.id])
        previously_charged = (
            Decimal(str(existing[order.id].total_charges))
            if order.id in existing
            else Decimal("0")
        )
        total_charges = await self._recompute_charges(order)
        incremental_charges = total_charges - previously_charged

        await self.positions.apply_fill(
            security_id=order.security_id,
            trading_symbol=order.trading_symbol,
            side=order.side,
            quantity=result.filled_quantity,
            price=result.average_price,
            lot_size=int(order.lot_size),
            expiry_date=order.expiry_date,
            strike_price=order.strike_price,
            option_type=order.option_type,
            charges=incremental_charges,
        )

    async def _recompute_charges(self, order: Order) -> Decimal:
        """Charges on the cumulative executed quantity.

        Recomputed rather than accumulated so brokerage -- which is per executed
        order, not per fill -- is counted exactly once however many fills the
        order takes.
        """
        import json

        breakdown = self.charges.compute_order_charges_for_quantity(
            side=order.side,
            premium=Decimal(str(order.average_fill_price)),
            quantity=int(order.filled_quantity),
            lot_size=int(order.lot_size),
            strike_price=order.strike_price,
        )

        await self.orders.upsert_charges(
            order,
            {
                "turnover": breakdown.turnover,
                "brokerage": breakdown.brokerage,
                "ctt": breakdown.ctt,
                "exchange_transaction_charge": breakdown.exchange_transaction_charge,
                "sebi_turnover_fee": breakdown.sebi_turnover_fee,
                "stamp_duty": breakdown.stamp_duty,
                "gst": breakdown.gst,
                "total_charges": breakdown.total,
                "rates_version": breakdown.rates_version,
                "breakdown_json": json.dumps(
                    [component.as_dict() for component in breakdown.components]
                ),
            },
        )
        return Decimal(str(breakdown.total))

    async def _finalise(
        self,
        order: Order,
        complete_status: str,
        incomplete_status: str,
        terminal_when_incomplete: bool,
        message: Optional[str] = None,
    ) -> None:
        complete = int(order.filled_quantity or 0) >= int(order.quantity)
        status = complete_status if complete else incomplete_status
        if not message:
            if complete:
                message = (
                    f"Fully filled {order.filled_quantity} at average "
                    f"{order.average_fill_price}"
                )
            else:
                message = (
                    f"Filled {order.filled_quantity} of {order.quantity} at average "
                    f"{order.average_fill_price}"
                )
        await self._transition(
            order, status, "FILLED" if complete else "PARTIAL_FILL", message=message
        )
        if complete or terminal_when_incomplete:
            order.completed_at = utc_now()

    async def _transition(
        self, order: Order, status: str, event_type: str, message: Optional[str] = None
    ) -> None:
        now = utc_now()
        order.status = status
        order.last_event_at = now
        await self.orders.add_event(
            order, event_type, status, now,
            price=order.average_fill_price, quantity=order.filled_quantity,
            message=message,
        )
        await self.orders.session.flush()

    async def _reject(self, order: Order, reason: str) -> None:
        order.rejection_reason = reason[:255]
        order.completed_at = utc_now()
        await self._transition(order, OrderStatus.REJECTED.value, "REJECTED", message=reason)
        logger.info("Order %s rejected: %s", order.client_order_id, reason)

    async def cancel_paper_order(self, order_id: int) -> Order:
        order = await self.orders.get_by_id(order_id)
        if order is None:
            raise OrderValidationError(f"Order {order_id} not found")
        if order.status in (
            OrderStatus.FILLED.value, OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value
        ):
            raise OrderValidationError(
                f"Order {order_id} is already {order.status} and cannot be cancelled"
            )
        order.completed_at = utc_now()
        await self._transition(
            order, OrderStatus.CANCELLED.value, "CANCELLED",
            message=f"Cancelled with {int(order.quantity) - int(order.filled_quantity or 0)} unfilled",
        )
        return order

    # --- preview -----------------------------------------------------------
    async def preview_order(
        self,
        security_id: str,
        side: str,
        order_type: str,
        lots: int,
        limit_price: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """What this order would do, WITHOUT placing it.

        Order entry must show estimated charges and the net debit/credit before
        anything is confirmed, so this runs the same fill simulation against the
        same book and returns the indicative result.
        """
        instrument = await self.instruments.get_by_security_id(str(security_id))
        if instrument is None:
            raise OrderValidationError(f"Unknown security_id {security_id}")

        side = str(side).upper()
        order_type = str(order_type).upper()
        quantity = lots * int(instrument.lot_size)
        row = self.book.get(str(security_id))
        depth = row.get("depth") if row else None
        tick = Decimal(str(instrument.tick_size or "0.1"))

        limit = (
            self._align_to_tick(Decimal(str(limit_price)), instrument)
            if limit_price is not None
            else None
        )

        would_rest = False
        if order_type == OrderType.MARKET.value:
            result = fill_simulator.simulate_marketable_fill(
                side=side, quantity=quantity, depth=depth, tick_size=tick,
                slippage_ticks=self._slippage_ticks(),
                allow_partial=self._allow_partial(),
            )
        elif fill_simulator.is_marketable(side, limit, depth):
            result = fill_simulator.simulate_marketable_fill(
                side=side, quantity=quantity, depth=depth, tick_size=tick,
                slippage_ticks=self._slippage_ticks(), limit_price=limit,
                allow_partial=self._allow_partial(),
            )
        else:
            result = fill_simulator.FillResult(
                note="Would rest in the book until the market crosses the limit price"
            )
            would_rest = True

        indicative_price = result.average_price or limit
        charges = None
        if indicative_price is not None:
            charges = self.charges.compute_order_charges(
                side=side,
                premium=indicative_price,
                lot_size=int(instrument.lot_size),
                lots=lots,
                strike_price=instrument.strike_price,
            )

        estimated_quantity = result.filled_quantity or (quantity if would_rest else 0)
        gross = (
            Decimal(str(indicative_price)) * estimated_quantity
            if indicative_price is not None
            else Decimal("0")
        )
        total_charges = Decimal(str(charges.total)) if charges else Decimal("0")
        # A buy costs the premium plus charges; a sell receives the premium less
        # charges. Negative means money leaves the account.
        net = (
            -(gross + total_charges)
            if side == OrderSide.BUY.value
            else (gross - total_charges)
        )

        return {
            "instrument": instrument,
            "quantity": quantity,
            "estimatedFillQuantity": estimated_quantity,
            "estimatedPrice": indicative_price,
            "wouldRest": would_rest,
            "wouldPartiallyFill": (
                not would_rest and 0 < result.filled_quantity < quantity
            ),
            "rejectionReason": result.rejection_reason,
            "note": result.note,
            "fills": result.fills,
            "charges": charges,
            "grossValue": gross.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
            "netAmount": net.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
            "limitPrice": limit,
        }
