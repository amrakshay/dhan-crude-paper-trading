"""Paper order lifecycle.

PAPER RUPEES ONLY. Placing an "order" here writes rows to the local database
and matches them against a snapshot of the in-memory book. Nothing in this
module, or anything it calls, contacts a broker.

Lifecycle:

    PENDING -> FILLED             market order, full fill
            -> PARTIALLY_FILLED   market order, book ran out -- TERMINAL
            -> OPEN               limit order resting away from the touch
            -> REJECTED           no depth, or validation failed
    OPEN -> FILLED | PARTIALLY_FILLED | CANCELLED
    PARTIALLY_FILLED (limit) -> FILLED | CANCELLED

**A PARTIALLY FILLED MARKET ORDER IS FINISHED, NOT RESTING.** There is no
price for it to rest at, so `completed_at` is stamped and the unfilled
remainder is abandoned -- not cancelled, and never filled later. Only LIMIT
orders are worked by the matcher (`try_fill_open_orders` skips anything else),
so the two states share a name and nothing else. This docstring used to say
"(stays open)" and list `PARTIALLY_FILLED -> FILLED` without qualification,
which reads as a market order that will eventually complete; it will not, and
somebody waiting for it waits forever.

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
from src.strategies.services.strategy_registry import get_strategy_registry

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
        # Replaced per order with the engine for that order's strategy, so two
        # strategies on two rate cards are charged under their own.
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
    def _max_lots(strategy=None) -> int:
        """The fat-finger rail, per strategy where one is declared.

        A "lot" is 100 barrels on MCX and one share on NSE cash, so a single
        global cap cannot be right for both: 100 crude lots is 10,000 barrels,
        100 equity "lots" is 100 shares. A strategy that needs a different rail
        declares `automation.max_lots_per_order`; everything else keeps the
        global value it always had.
        """
        configured = (
            strategy.automation.max_lots_per_order if strategy is not None else None
        )
        if configured is not None:
            return int(configured)
        return config_utils.get_property_value_int("trading.max_lots_per_order", 100)

    async def _resolve_strategy(
        self,
        instrument,
        strategy_key: Optional[str],
        portfolio_id: int,
        is_close_order: bool,
    ):
        """Which strategy is this trade under? Asked once, then stored.

        Four cases, in order, and the last one is the point:

        1. **The caller said.** An automated module always does, because it
           knows what it is. The key still has to be one of the strategies that
           actually claims the instrument -- a caller naming an unrelated
           strategy is a bug, not a preference.
        2. **Exactly one strategy claims it.** MCX crude, and every equity name
           before a second NSE module existed. Nothing changed for these.
        3. **Several claim it and this CLOSES a position.** The position
           already carries the strategy it was opened under, which is the only
           correct answer -- a close must land under the same strategy as its
           open or the two halves of a round trip end up in different books.
        4. **Several claim it and nobody said.** REFUSED. Returning the
           alphabetically first would silently put the trade under the wrong
           rate card, the wrong arming switch and the wrong journal, and
           nothing downstream would ever notice.
        """
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        matches = registry.strategies_for_instrument(
            instrument.exchange_segment, instrument.underlying_symbol
        )
        if not matches:
            raise OrderValidationError(
                f"{instrument.trading_symbol} ({instrument.exchange_segment} "
                f"{instrument.underlying_symbol}) belongs to no configured "
                f"strategy module, so there is nothing to trade it under."
            )

        if strategy_key:
            chosen = next(
                (one for one in matches if one.key == str(strategy_key)), None
            )
            if chosen is None:
                raise OrderValidationError(
                    f"Strategy {strategy_key!r} does not trade "
                    f"{instrument.trading_symbol} "
                    f"({instrument.exchange_segment} "
                    f"{instrument.underlying_symbol}). It is traded by "
                    f"{', '.join(one.key for one in matches)}."
                )
            return chosen

        if len(matches) == 1:
            return matches[0]

        if is_close_order:
            from src.positions.database.db_operations.position_repository import (
                PositionRepository,
            )

            position = await PositionRepository(
                self.orders.session
            ).get_open_for_security(int(portfolio_id), str(instrument.security_id))
            if position is not None and position.strategy_key:
                return next(
                    (one for one in matches if one.key == position.strategy_key),
                    matches[0],
                )

        raise OrderValidationError(
            f"{instrument.trading_symbol} is traded by more than one strategy "
            f"({', '.join(one.key for one in matches)}), so the order must say "
            f"which. An instrument does not decide this: the same share can be "
            f"held by two strategies with different rules, and putting the "
            f"trade under the wrong one would give it the wrong journal and "
            f"the wrong arming switch."
        )

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
        portfolio_id: Optional[int] = None,
        reason: Optional[str] = None,
        strategy_key: Optional[str] = None,
    ) -> Order:
        """Submit a paper order into one portfolio.

        `quantity_override` closes an exact barrel quantity that is not a whole
        number of lots. A market order can partially fill below one lot (the
        visible book simply ran out), and without this the residual position
        would be impossible to close.

        `portfolio_id` says whose money is at stake. Omitting it is only
        unambiguous while exactly one portfolio is active; with several, the
        resolver refuses rather than guessing, because a trade landing in the
        wrong book is the failure the portfolio selector exists to prevent.

        `reason` is WHY, and it is written onto the PLACED event rather than
        onto a column of its own. An order placed by a person has no reason
        beyond their clicking; one placed by a strategy has a specific one
        ("Rotation exit: rank 19 > 15") and the operator who finds the trade
        must be able to read it from the order. The strategy's own journal
        carries the same sentence at full length and ties the decision to this
        order through `SwingDecision.order_id`; `order_events.message` is where
        it surfaces on the order itself. A `strategy_reason` column on `orders`
        would be a third copy of one sentence, null for every human order.

        `strategy_key` says WHICH STRATEGY is trading, for the instruments that
        belong to more than one. The Nifty 500 belongs to two, so an automated
        module passes its own key; a discretionary order on an unambiguous
        contract passes nothing and is resolved from the instrument as before.
        An ambiguous instrument with no key is refused, never guessed.
        """
        side = str(side).upper()
        order_type = str(order_type).upper()

        logger.debug(
            "Paper order requested: security_id=%s side=%s type=%s lots=%s "
            "limit=%s close=%s quantity_override=%s",
            security_id, side, order_type, lots, limit_price, is_close_order,
            quantity_override,
        )

        from src.portfolios.services.portfolio_service import (
            PortfolioError,
            PortfolioService,
        )

        try:
            portfolio = await PortfolioService(
                self.orders.session, book=self._book
            ).resolve_for_trading(portfolio_id)
        except PortfolioError as error:
            raise OrderValidationError(str(error)) from error

        instrument = await self.instruments.get_by_security_id(str(security_id))
        if instrument is None:
            raise OrderValidationError(
                f"Unknown security_id {security_id}. Refresh the instrument master."
            )
        if not instrument.is_active:
            raise OrderValidationError(
                f"{instrument.trading_symbol} is no longer active (expired series)."
            )

        # Resolved HERE, once, and then stored on the order. Never re-derived
        # at read time -- the instrument row this came from will be deactivated
        # when the series expires.
        #
        # AN INSTRUMENT NO LONGER DETERMINES A STRATEGY. Until 2026-09-19 it
        # did, and this read the contract's segment and underlying and was
        # done. Then a second NSE equity module arrived: the swing rotation and
        # BTST Overnight both trade the Nifty 500, and SUNTV belongs to both.
        #
        # So `strategy_key` is now passed in by a caller that knows, exactly
        # the way `portfolio_id` is, and for exactly the reason
        # `frontend/CLAUDE.md` section 2a gives for that one -- a trade landing
        # under the wrong strategy is silent and carries the wrong rate card,
        # the wrong arming switch and the wrong journal with it. Where the
        # instrument is unambiguous nothing changed and no caller had to move;
        # where it is not, and nobody said, this REFUSES rather than picking
        # the alphabetically first.
        strategy = await self._resolve_strategy(
            instrument, strategy_key, portfolio.id, is_close_order
        )
        # A disabled strategy refuses with a SPECIFIC message rather than a
        # 404 that reads like a routing bug. Closing an existing position is
        # still allowed: switching a strategy off must not trap a trader in a
        # position it opened.
        if not get_strategy_registry().is_enabled(strategy.key) and not is_close_order:
            raise OrderValidationError(
                f"The {strategy.label} strategy is switched off, so new "
                f"positions cannot be opened in it. Its open positions can "
                f"still be closed, and its history is unchanged. Switch it "
                f"back on from Strategies & Features."
            )
        if side not in (OrderSide.BUY.value, OrderSide.SELL.value):
            raise OrderValidationError(f"side must be BUY or SELL, got {side!r}")
        if order_type not in (OrderType.MARKET.value, OrderType.LIMIT.value):
            raise OrderValidationError(
                f"order_type must be MARKET or LIMIT, got {order_type!r}"
            )
        if lots <= 0:
            raise OrderValidationError("lots must be positive")
        if lots > self._max_lots(strategy):
            raise OrderValidationError(
                f"lots exceeds the configured maximum of {self._max_lots(strategy)}"
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

        # Funds, in the validation block with every other rule rather than
        # scattered. Priced off the limit where there is one and off the live
        # mark otherwise: an estimate, because what a market order actually
        # pays is only known once it walks the book -- which is why the check
        # runs AGAIN at fill.
        reference_price = limit_price
        if reference_price is None:
            row = self.book.get(str(instrument.security_id)) or {}
            if row.get("ask"):
                reference_price = Decimal(str(row["ask"]))
            elif row.get("ltp"):
                reference_price = Decimal(str(row["ltp"]))
        if reference_price is not None:
            refusal = await self._check_funds(
                portfolio_id=portfolio.id,
                side=side,
                quantity=quantity,
                price=Decimal(str(reference_price)),
                strategy_key=strategy.key,
                lot_size=int(instrument.lot_size),
                is_close_order=is_close_order,
            )
            if refusal:
                logger.warning(
                    "Order refused for funds in portfolio %s: %s %s lot(s) of %s -- %s",
                    portfolio.name, side, lots, instrument.trading_symbol, refusal,
                )
                raise OrderValidationError(
                    f"{refusal} Deposit into {portfolio.name!r}, or trade fewer lots."
                )

        now = utc_now()

        order = Order(
            client_order_id=uuid.uuid4().hex,
            strategy_key=strategy.key,
            portfolio_id=portfolio.id,
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
        placed_message = f"{side} {lots} lot(s) {order_type}"
        if reason:
            placed_message = f"{placed_message} -- {reason}"
        await self.orders.add_event(
            order, "PLACED", OrderStatus.PENDING.value, now,
            price=limit_price, quantity=quantity,
            # OrderEvent.message is VARCHAR(255). The journal keeps the full
            # 500-character sentence; this is the readable head of it.
            message=placed_message[:255],
        )

        logger.info(
            "Paper order %s placed in portfolio %s: %s %s %s lot(s) of %s "
            "(qty %s, limit %s)",
            order.client_order_id, portfolio.name, side, order_type, lots,
            instrument.trading_symbol, quantity, limit_price,
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

        if row is None:
            logger.warning(
                "Order %s: no book row for security_id %s (%s) -- the feed has "
                "not delivered a tick for it, so there is nothing to fill against",
                order.client_order_id, order.security_id, order.trading_symbol,
            )

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
                context=order.client_order_id,
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
                context=order.client_order_id,
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
        logger.info(
            "Order %s resting at %s (%s %s): not marketable against the current touch",
            order.client_order_id, order.limit_price, order.side, order.trading_symbol,
        )
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
        registry = get_strategy_registry()
        for order in open_orders:
            if order.order_type != OrderType.LIMIT.value:
                continue
            # A disabled strategy's resting orders are FROZEN, not cancelled.
            # They stay OPEN, nothing fills them, and they resume when the
            # strategy comes back on -- cancelling them would destroy work an
            # operator set up, and this way is recoverable.
            if not registry.is_enabled(order.strategy_key):
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
                context=order.client_order_id,
            )
            if not result.is_filled:
                continue

            # SECOND funds check, at the fill rather than at placement. A
            # resting order can sit for hours while other trades spend the
            # money it was affordable against, and filling it anyway would put
            # the portfolio into a negative balance that nothing asked for.
            refusal = await self._check_funds(
                portfolio_id=order.portfolio_id,
                side=order.side,
                quantity=result.filled_quantity,
                price=Decimal(str(result.average_price)),
                strategy_key=order.strategy_key,
                lot_size=int(order.lot_size),
                is_close_order=bool(order.is_close_order),
            )
            if refusal:
                # REJECTED, not partially filled to fit. Filling what fits
                # would be the simulator quietly choosing a different trade
                # from the one that was placed.
                await self._reject(
                    order,
                    f"{refusal} The order was affordable when it was placed; "
                    f"it is not now.",
                )
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

    # --- funds -------------------------------------------------------------
    async def _estimated_debit(
        self,
        side: str,
        quantity: int,
        price: Decimal,
        strategy_key: str,
        lot_size: int,
    ) -> Decimal:
        """What this execution would take out of a portfolio.

        A BUY costs the premium plus charges. A SELL to open RECEIVES premium
        but ties up margin, and the margin estimate is larger than the credit
        for anything but a deep out-of-the-money option -- so the debit to check
        against is the margin, less what comes in.

        Deliberately an over-estimate at the margins rather than an under-one:
        this is a paper simulator, and erring towards refusing a trade is the
        direction backend/CLAUDE.md section 4 says to err in.
        """
        from src.portfolios.services.balance_service import BalanceService
        from src.strategies.services.strategy_registry import get_strategy_registry

        price = Decimal(str(price))
        quantity = int(quantity)
        consideration = (price * quantity).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

        engine = ChargesEngine.for_strategy_key(strategy_key)
        charges = Decimal(
            str(
                engine.compute_order_charges_for_quantity(
                    side=side, premium=price, quantity=quantity, lot_size=lot_size
                ).total
            )
        )

        if str(side).upper() == OrderSide.BUY.value:
            return consideration + charges

        strategy = get_strategy_registry().get(strategy_key)
        percent = (
            Decimal(str(strategy.margin.short_option_percent_of_notional))
            if strategy
            else Decimal("0")
        )
        margin = (percent * consideration).quantize(
            MONEY_QUANTUM, rounding=ROUND_HALF_UP
        )
        return (margin - consideration + charges).max(charges)

    async def _check_funds(
        self,
        portfolio_id: int,
        side: str,
        quantity: int,
        price: Decimal,
        strategy_key: str,
        lot_size: int,
        is_close_order: bool,
    ) -> Optional[str]:
        """None if affordable, otherwise why not, naming the shortfall.

        A CLOSING order is never refused for funds. Closing a long releases
        money and closing a short releases margin; refusing it would trap a
        trader in a position precisely when they most need out of it.
        """
        if is_close_order:
            return None

        from src.portfolios.services.balance_service import BalanceService

        required = await self._estimated_debit(
            side, quantity, price, strategy_key, lot_size
        )
        if required <= 0:
            return None

        available = await BalanceService(
            self.orders.session, book=self._book
        ).available_balance(portfolio_id)
        if required <= available:
            return None

        shortfall = (required - available).quantize(
            MONEY_QUANTUM, rounding=ROUND_HALF_UP
        )
        return (
            f"Insufficient funds: this would need {required} but only "
            f"{available} is available -- short by {shortfall}."
        )

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

        logger.info(
            "Order %s filled %s at average %s across %s level(s): %s",
            order.client_order_id, result.filled_quantity, result.average_price,
            len(result.fills),
            ", ".join(
                f"{fill.quantity}@{fill.price}(L{fill.book_level})"
                for fill in result.fills
            ),
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
        logger.debug(
            "Order %s charges: cumulative %s, previously charged %s, "
            "this fill accrues %s to the position",
            order.client_order_id, total_charges, previously_charged,
            incremental_charges,
        )

        await self.positions.apply_fill(
            strategy_key=order.strategy_key,
            portfolio_id=order.portfolio_id,
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
            # The enrichment the trade alert wants and the position row cannot
            # know: which order this was, and WHY it was placed. The reason is
            # already on the PLACED event (`submit_paper_order(reason=...)`),
            # and for the rotation it is its rank and score.
            alert_context={
                "clientOrderId": order.client_order_id,
                "reason": await self._placed_reason(order),
            },
        )

        await self._record_cash_effect(
            order,
            quantity=result.filled_quantity,
            price=result.average_price,
            incremental_charges=incremental_charges,
            at=now,
        )

    @staticmethod
    async def _placed_reason(order: Order) -> Optional[str]:
        """Why this order was placed, off its own PLACED event.

        Read rather than recomputed: `submit_paper_order(reason=...)` writes it
        there precisely so there is one copy, and a `strategy_reason` column on
        `orders` would be a third one, null for every human order.
        """
        try:
            for event in order.events or []:
                if event.event_type == "PLACED" and event.message:
                    return event.message
        except Exception:  # noqa: BLE001 - an alert detail is never worth a failure
            return None
        return None

    async def _record_cash_effect(
        self,
        order: Order,
        quantity: int,
        price: Decimal,
        incremental_charges: Decimal,
        at,
    ) -> None:
        """The money this fill moved, as append-only ledger entries.

        A BUY debits the premium; a SELL credits it. Charges are always a
        debit, and only the DELTA is recorded -- charges are recomputed on the
        cumulative executed quantity rather than accumulated per fill
        (backend/CLAUDE.md section 5), so posting the full figure again on a
        second fill would charge the portfolio twice for one order.
        """
        from src.portfolios.database.db_models.portfolio_model import (
            ENTRY_CHARGES,
            ENTRY_TRADE_CREDIT,
            ENTRY_TRADE_DEBIT,
        )
        from src.portfolios.database.db_operations.portfolio_repository import (
            CashLedgerRepository,
        )

        ledger = CashLedgerRepository(self.orders.session)
        consideration = (Decimal(str(price)) * Decimal(int(quantity))).quantize(
            MONEY_QUANTUM, rounding=ROUND_HALF_UP
        )

        if consideration > 0:
            is_buy = order.side == OrderSide.BUY.value
            await ledger.add_entry(
                portfolio_id=order.portfolio_id,
                entry_type=ENTRY_TRADE_DEBIT if is_buy else ENTRY_TRADE_CREDIT,
                amount=-consideration if is_buy else consideration,
                entry_at=at,
                order_id=order.id,
                note=f"{order.side} {quantity} of {order.trading_symbol}",
            )

        charges = Decimal(str(incremental_charges or 0)).quantize(
            MONEY_QUANTUM, rounding=ROUND_HALF_UP
        )
        if charges != 0:
            await ledger.add_entry(
                portfolio_id=order.portfolio_id,
                entry_type=ENTRY_CHARGES,
                amount=-charges,
                entry_at=at,
                order_id=order.id,
                note=f"Charges on {order.client_order_id}",
            )

    async def _recompute_charges(self, order: Order) -> Decimal:
        """Charges on the cumulative executed quantity.

        Recomputed rather than accumulated so brokerage -- which is per executed
        order, not per fill -- is counted exactly once however many fills the
        order takes.
        """
        import json

        engine = ChargesEngine.for_strategy_key(order.strategy_key)
        breakdown = engine.compute_order_charges_for_quantity(
            side=order.side,
            premium=Decimal(str(order.average_fill_price)),
            quantity=int(order.filled_quantity),
            lot_size=int(order.lot_size),
            strike_price=order.strike_price,
        )

        # The COMPONENTS are the breakdown. turnover, total and rates_version
        # stay real columns because they are queried and aggregated; the line
        # items do not, so a rate card can add a tax without a migration.
        await self.orders.upsert_charges(
            order,
            {
                "turnover": breakdown.turnover,
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
        logger.info(
            "Order %s -> %s (%s of %s at average %s)",
            order.client_order_id, status, order.filled_quantity, order.quantity,
            order.average_fill_price,
        )

    async def _transition(
        self, order: Order, status: str, event_type: str, message: Optional[str] = None
    ) -> None:
        now = utc_now()
        logger.debug(
            "Order %s transition: %s -> %s (%s) %s",
            order.client_order_id, order.status, status, event_type, message or "",
        )
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
        logger.warning(
            "Order %s REJECTED: %s (%s %s %s lot(s) of %s, security_id=%s, limit=%s)",
            order.client_order_id, reason, order.side, order.order_type, order.lots,
            order.trading_symbol, order.security_id, order.limit_price,
        )

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
        unfilled = int(order.quantity) - int(order.filled_quantity or 0)
        order.completed_at = utc_now()
        await self._transition(
            order, OrderStatus.CANCELLED.value, "CANCELLED",
            message=f"Cancelled with {unfilled} unfilled",
        )
        logger.info(
            "Order %s cancelled with %s of %s unfilled (%s %s)",
            order.client_order_id, unfilled, order.quantity, order.side,
            order.trading_symbol,
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
        portfolio_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """What this order would do, WITHOUT placing it.

        Order entry must show estimated charges and the net debit/credit before
        anything is confirmed, so this runs the same fill simulation against the
        same book and returns the indicative result.

        No portfolio is resolved here: a preview spends nothing, and asking for
        one would make the cost of a trade unviewable in the very case where
        the trader most needs to see it -- before choosing which book to put it
        in. The affordability check against a portfolio is a separate call.
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
            # A PREVIEW, so the first claimant is good enough and a refusal
            # would be worse than an approximation: both NSE equity modules are
            # charged under the same rate card, and a preview that silently
            # showed no charges because two strategies claim the share would
            # teach the operator nothing. The ORDER itself still refuses.
            claimants = get_strategy_registry().strategies_for_instrument(
                instrument.exchange_segment, instrument.underlying_symbol
            )
            strategy = claimants[0] if claimants else None
            engine = (
                ChargesEngine.for_strategy(strategy) if strategy else self.charges
            )
            charges = engine.compute_order_charges(
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
            # Affordability against a named portfolio, so the ticket can show
            # the debit beside the available balance and disable Confirm when
            # it does not fit -- the same rule as showing charges before the
            # button is enabled, applied to money.
            **(
                await self._affordability(
                    portfolio_id, side, estimated_quantity, indicative_price,
                    instrument,
                )
            ),
        }

    async def _affordability(
        self,
        portfolio_id: Optional[int],
        side: str,
        quantity: int,
        price: Optional[Decimal],
        instrument,
    ) -> Dict[str, Any]:
        """What a portfolio has, and whether this order fits inside it."""
        if portfolio_id is None or price is None or quantity <= 0:
            return {}

        from src.portfolios.services.balance_service import BalanceService
        from src.strategies.services.strategy_registry import get_strategy_registry

        # Same reasoning as the charges preview above: affordability is the
        # question and the first claimant answers it.
        claimants = get_strategy_registry().strategies_for_instrument(
            instrument.exchange_segment, instrument.underlying_symbol
        )
        if not claimants:
            return {}
        strategy = claimants[0]

        try:
            available = await BalanceService(
                self.orders.session, book=self._book
            ).available_balance(int(portfolio_id))
        except Exception:  # noqa: BLE001 - a preview must still render
            logger.exception(
                "Could not read the available balance for portfolio %s; the "
                "preview will not show affordability",
                portfolio_id,
            )
            return {}

        required = await self._estimated_debit(
            side, quantity, Decimal(str(price)), strategy.key,
            int(instrument.lot_size),
        )
        return {
            "portfolioId": int(portfolio_id),
            "availableBalance": available,
            "estimatedDebit": required,
            "affordable": required <= available,
        }
