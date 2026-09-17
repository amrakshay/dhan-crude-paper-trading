"""Trading directly from the futures chart.

The user reads and trades levels on the near-month CRUDEOIL FUTURE, but the
book only ever holds OPTIONS:

    Buy click   ->  buy the nearest ATM CALL
    Sell click  ->  buy the nearest ATM PUT

Nothing here ever writes an option. A "Sell" is a long put, not a short call,
so the worst case is always the premium paid.

## Netting

The two buttons net, the way they would on a real futures chart, so one click
can never leave an accidental straddle:

    long CE  --Sell-->  flat        (closes the call)
    flat     --Sell-->  long PE     (opens the put)
    long PE  --Buy -->  flat        (closes the put)

That is why at most one chart trade is open per underlying at a time.

## Bracket levels are FUTURES levels

The stop-loss and take-profit lines are dragged onto the futures chart, so they
are prices of the future. bracket_monitor watches the future and closes the
OPTION at market when one is crossed.

**This means a stop does not bound the loss in rupees.** What the call is worth
when the future reaches the stop depends on delta, time decay and implied
volatility, none of which the level knows about. The rupee figures this service
returns for a level are estimates computed from the option's CURRENT price, and
are labelled as estimates everywhere they surface.

## Fills are the same pessimistic simulation as everywhere else

Entries and exits go through `OrderService.submit_paper_order` as MARKET
orders, so they cross the spread, walk the book and can partially fill exactly
as a manually placed order would. A triggered stop that only partially fills
leaves the remainder resting; the chart is told so rather than being allowed to
look clean.
"""
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src import config_utils
from src.chart_trading.database.db_operations.chart_trade_repository import (
    STATUS_CLOSED,
    STATUS_OPEN,
    ChartTradeRepository,
)
from src.chart_trading.database.db_models.chart_trade_model import ChartTrade
from src.constants import OptionType, OrderSide, OrderType
from src.core.time_utils import utc_now
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.instruments.services.chain_service import ChainService
from src.logging_config import get_logger
from src.orders.database.db_operations.order_repository import OrderRepository
from src.orders.services.order_service import OrderService, OrderValidationError
from src.positions.database.db_operations.position_repository import PositionRepository
from src.positions.services.position_service import PositionService

logger = get_logger("chart_trading.service")

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

REASON_MANUAL = "MANUAL"
REASON_STOP_LOSS = "STOP_LOSS"
REASON_TAKE_PROFIT = "TAKE_PROFIT"

PRICE_QUANTUM = Decimal("0.0001")
MONEY_QUANTUM = Decimal("0.01")


class ChartTradingError(Exception):
    """Anything that stops a chart trade. Message is meant for the UI."""


class ChartTradingService:
    def __init__(
        self,
        chart_trades: ChartTradeRepository,
        instruments: InstrumentRepository,
        orders: OrderRepository,
        positions: PositionRepository,
        book=None,
    ) -> None:
        self.chart_trades = chart_trades
        self.instruments = instruments
        self.positions = positions
        self.chain = ChainService(instruments)
        self.order_service = OrderService(
            order_repository=orders,
            instrument_repository=instruments,
            position_repository=positions,
            book=book,
        )
        self._book = book

    # --- configuration -----------------------------------------------------
    @staticmethod
    def _default_lots() -> int:
        return config_utils.get_property_value_int("chart_trading.default_lots", 1)

    @staticmethod
    def _enabled() -> bool:
        return config_utils.get_property_value_boolean("chart_trading.enabled", True)

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- market reads ------------------------------------------------------
    def _row(self, security_id: str) -> Dict[str, Any]:
        return self.book.get(str(security_id)) or {}

    def underlying_price(self, security_id: str) -> Optional[Decimal]:
        """Last traded price of the charted future.

        Unknown is not zero: with no tick yet this returns None and every
        caller refuses to act rather than trading against a made-up level.
        """
        value = self._row(security_id).get("ltp")
        return Decimal(str(value)) if value else None

    def _option_mark(self, security_id: str) -> Optional[Decimal]:
        value = self._row(security_id).get("ltp")
        return Decimal(str(value)) if value else None

    # --- contract resolution ----------------------------------------------
    async def resolve_expiries(self) -> List[date]:
        """Option expiries the chart may trade, nearest first."""
        return await self.chain.list_option_expiries()

    async def resolve_atm_option(
        self, underlying_security_id: str, side: str, expiry: Optional[date] = None
    ):
        """The ATM call (BUY) or put (SELL) for an expiry.

        The strike is chosen against the FUTURE's live price, which is what the
        chart is showing -- not against the option chain's own underlying
        figure, so the strike always matches the levels being traded.
        """
        spot = self.underlying_price(underlying_security_id)
        if spot is None:
            raise ChartTradingError(
                "No live price for the charted contract yet, so the ATM strike "
                "cannot be resolved. Wait for the first tick."
            )

        expiries = await self.resolve_expiries()
        if not expiries:
            raise ChartTradingError(
                "No option expiries in the instrument master. Refresh it first."
            )
        if expiry is None:
            expiry = expiries[0]
        elif expiry not in expiries:
            raise ChartTradingError(
                f"{expiry} is not a listed option expiry. Available: "
                + ", ".join(str(value) for value in expiries[:6])
            )

        strikes = await self.chain.list_strikes(expiry)
        atm = self.chain.resolve_atm_strike(strikes, spot)
        if atm is None:
            raise ChartTradingError(f"No strikes listed for expiry {expiry}.")

        option_type = OptionType.CALL.value if side == SIDE_BUY else OptionType.PUT.value
        for row in await self.chain.get_chain(expiry):
            if row.strike_price != atm:
                continue
            contract = row.call if option_type == OptionType.CALL.value else row.put
            if contract is None:
                break
            return contract, atm, expiry, spot

        raise ChartTradingError(
            f"No {option_type} contract at strike {atm} for expiry {expiry}."
        )

    # --- preview -----------------------------------------------------------
    async def preview(
        self,
        underlying_security_id: str,
        expiry: Optional[date] = None,
        lots: Optional[int] = None,
    ) -> Dict[str, Any]:
        """What a Buy and a Sell would each do right now, without placing them.

        The chart shows this BEFORE the click, which is how one-click entry
        still honours the rule that the cost of an order is on screen before it
        is sent (frontend/CLAUDE.md section 3).
        """
        lots = lots or self._default_lots()
        sides: Dict[str, Any] = {}

        for side in (SIDE_BUY, SIDE_SELL):
            try:
                contract, strike, resolved_expiry, spot = await self.resolve_atm_option(
                    underlying_security_id, side, expiry
                )
                estimate = await self.order_service.preview_order(
                    security_id=contract.security_id,
                    side=OrderSide.BUY.value,      # always a long option leg
                    order_type=OrderType.MARKET.value,
                    lots=lots,
                )
                sides[side] = {
                    "securityId": contract.security_id,
                    "tradingSymbol": contract.trading_symbol,
                    "optionType": contract.option_type,
                    "strikePrice": strike,
                    "expiryDate": resolved_expiry,
                    "lots": lots,
                    "quantity": estimate["quantity"],
                    "estimatedPrice": estimate["estimatedPrice"],
                    # preview_order returns the computed charges object, not a
                    # dict; the chart only needs the total it will actually pay.
                    "estimatedCharges": getattr(estimate.get("charges"), "total", None),
                    "grossValue": estimate["grossValue"],
                    "netAmount": estimate["netAmount"],
                    "wouldPartiallyFill": estimate["wouldPartiallyFill"],
                    "rejectionReason": estimate["rejectionReason"],
                    "underlyingPrice": spot,
                    "error": None,
                }
            except (ChartTradingError, OrderValidationError) as error:
                sides[side] = {"error": str(error)}

        return {
            "underlyingSecurityId": str(underlying_security_id),
            "underlyingPrice": self.underlying_price(underlying_security_id),
            "expiries": await self.resolve_expiries(),
            "lots": lots,
            "buy": sides[SIDE_BUY],
            "sell": sides[SIDE_SELL],
        }

    # --- opening and closing ----------------------------------------------
    async def click(
        self,
        underlying_security_id: str,
        side: str,
        expiry: Optional[date] = None,
        lots: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One click on the chart. Nets against an open trade before opening."""
        if not self._enabled():
            raise ChartTradingError("Chart trading is disabled (chart_trading.enabled=false).")

        side = str(side).upper()
        if side not in (SIDE_BUY, SIDE_SELL):
            raise ChartTradingError(f"side must be BUY or SELL, got {side!r}")

        underlying_security_id = str(underlying_security_id)
        open_trade = await self.chart_trades.get_open_for_underlying(underlying_security_id)

        # Netting: the opposite click closes what is open rather than stacking
        # a put on top of a call.
        if open_trade is not None and open_trade.chart_side != side:
            closed = await self.close(open_trade.id, reason=REASON_MANUAL)
            return {"action": "CLOSED", "trade": closed}
        if open_trade is not None:
            raise ChartTradingError(
                f"A {open_trade.chart_side} chart trade is already open on this "
                "contract. Close it, or click the other side."
            )

        return {
            "action": "OPENED",
            "trade": await self._open(underlying_security_id, side, expiry, lots),
        }

    async def _open(
        self,
        underlying_security_id: str,
        side: str,
        expiry: Optional[date],
        lots: Optional[int],
    ) -> ChartTrade:
        lots = lots or self._default_lots()
        contract, strike, resolved_expiry, spot = await self.resolve_atm_option(
            underlying_security_id, side, expiry
        )
        underlying = await self.instruments.get_by_security_id(underlying_security_id)
        if underlying is None:
            raise ChartTradingError(
                f"Unknown charted contract {underlying_security_id}. Refresh the "
                "instrument master."
            )

        try:
            order = await self.order_service.submit_paper_order(
                security_id=contract.security_id,
                side=OrderSide.BUY.value,
                order_type=OrderType.MARKET.value,
                lots=lots,
            )
        except OrderValidationError as error:
            raise ChartTradingError(str(error)) from error

        now = utc_now()
        trade = ChartTrade(
            # The option's own strategy, taken from the order that bought it,
            # so the chart trade and its order can never disagree.
            strategy_key=order.strategy_key,
            underlying_security_id=underlying_security_id,
            underlying_symbol=underlying.trading_symbol,
            option_security_id=contract.security_id,
            option_symbol=contract.trading_symbol,
            option_type=contract.option_type,
            strike_price=strike,
            expiry_date=resolved_expiry,
            lots=lots,
            chart_side=side,
            entry_order_id=order.id,
            status=STATUS_OPEN,
            underlying_at_entry=spot,
            opened_at=now,
        )
        self.chart_trades.session.add(trade)
        await self.chart_trades.session.flush()

        logger.info(
            "Chart %s on %s -> bought %s (order %s, %s lot(s)) with the future at %s",
            side, underlying_security_id, contract.trading_symbol, order.id, lots, spot,
        )
        return trade

    async def close(
        self,
        trade_id: int,
        reason: str = REASON_MANUAL,
        triggered_level: Optional[Decimal] = None,
    ) -> ChartTrade:
        """Sell the option back at market and close the chart trade."""
        trade = await self.chart_trades.get_by_id(trade_id)
        if trade is None:
            raise ChartTradingError(f"Chart trade {trade_id} not found")
        if trade.status != STATUS_OPEN:
            raise ChartTradingError(f"Chart trade {trade_id} is already {trade.status}")

        position = await self.positions.get_open_for_security(trade.option_security_id)
        quantity = int(position.net_quantity) if position else 0
        if quantity <= 0:
            # The entry never filled, or the position was closed from the
            # Positions page. Close the row rather than sending a phantom sell.
            logger.info(
                "Chart trade %s has no open quantity to sell; closing the row only",
                trade_id,
            )
        else:
            try:
                exit_order = await self.order_service.submit_paper_order(
                    security_id=trade.option_security_id,
                    side=OrderSide.SELL.value,
                    order_type=OrderType.MARKET.value,
                    lots=trade.lots,
                    is_close_order=True,
                    quantity_override=quantity,
                )
                trade.exit_order_id = exit_order.id
            except OrderValidationError as error:
                raise ChartTradingError(str(error)) from error

        trade.status = STATUS_CLOSED
        trade.exit_reason = reason
        trade.triggered_level = triggered_level
        trade.underlying_at_exit = self.underlying_price(trade.underlying_security_id)
        trade.closed_at = utc_now()
        await self.chart_trades.session.flush()

        logger.info(
            "Chart trade %s closed (%s) with the future at %s",
            trade_id, reason, trade.underlying_at_exit,
        )
        return trade

    # --- bracket levels ----------------------------------------------------
    def validate_levels(
        self,
        chart_side: str,
        underlying_price: Optional[Decimal],
        stop_loss: Optional[Decimal],
        take_profit: Optional[Decimal],
    ) -> None:
        """A stop must sit on the losing side of the price, a target on the winning one.

        Without this a line dropped on the wrong side of the market would fire
        the instant it was armed, which reads as the chart closing the trade by
        itself.
        """
        if underlying_price is None:
            raise ChartTradingError(
                "No live price for the charted contract, so a level cannot be checked."
            )
        for level, name in ((stop_loss, "stop loss"), (take_profit, "take profit")):
            if level is not None and level <= 0:
                raise ChartTradingError(f"The {name} level must be positive")

        if chart_side == SIDE_BUY:
            # Long call: profits as the future rises.
            if stop_loss is not None and stop_loss >= underlying_price:
                raise ChartTradingError(
                    f"A stop loss on a Buy must sit below the future ({underlying_price}); "
                    f"{stop_loss} would trigger immediately."
                )
            if take_profit is not None and take_profit <= underlying_price:
                raise ChartTradingError(
                    f"A take profit on a Buy must sit above the future ({underlying_price}); "
                    f"{take_profit} would trigger immediately."
                )
        else:
            # Long put: profits as the future falls.
            if stop_loss is not None and stop_loss <= underlying_price:
                raise ChartTradingError(
                    f"A stop loss on a Sell must sit above the future ({underlying_price}); "
                    f"{stop_loss} would trigger immediately."
                )
            if take_profit is not None and take_profit >= underlying_price:
                raise ChartTradingError(
                    f"A take profit on a Sell must sit below the future ({underlying_price}); "
                    f"{take_profit} would trigger immediately."
                )

    async def set_levels(
        self,
        trade_id: int,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        clear_stop_loss: bool = False,
        clear_take_profit: bool = False,
    ) -> ChartTrade:
        """Arm, move or remove the bracket lines. Both start unset."""
        trade = await self.chart_trades.get_by_id(trade_id)
        if trade is None:
            raise ChartTradingError(f"Chart trade {trade_id} not found")
        if trade.status != STATUS_OPEN:
            raise ChartTradingError(f"Chart trade {trade_id} is {trade.status}")

        new_stop = None if clear_stop_loss else (
            stop_loss if stop_loss is not None else trade.stop_loss_level
        )
        new_target = None if clear_take_profit else (
            take_profit if take_profit is not None else trade.take_profit_level
        )
        self.validate_levels(
            trade.chart_side,
            self.underlying_price(trade.underlying_security_id),
            new_stop,
            new_target,
        )

        trade.stop_loss_level = new_stop
        trade.take_profit_level = new_target
        await self.chart_trades.session.flush()
        logger.info(
            "Chart trade %s levels set: stop=%s target=%s", trade_id, new_stop, new_target
        )
        return trade

    def level_hit(self, trade: ChartTrade, underlying_price: Decimal) -> Optional[str]:
        """Which bracket, if any, the future has crossed."""
        if trade.chart_side == SIDE_BUY:
            if trade.stop_loss_level is not None and underlying_price <= trade.stop_loss_level:
                return REASON_STOP_LOSS
            if trade.take_profit_level is not None and underlying_price >= trade.take_profit_level:
                return REASON_TAKE_PROFIT
        else:
            if trade.stop_loss_level is not None and underlying_price >= trade.stop_loss_level:
                return REASON_STOP_LOSS
            if trade.take_profit_level is not None and underlying_price <= trade.take_profit_level:
                return REASON_TAKE_PROFIT
        return None

    # --- live state --------------------------------------------------------
    async def get_state(self, underlying_security_id: str) -> Dict[str, Any]:
        """The chart's own position, levels and live P&L net of charges."""
        underlying_security_id = str(underlying_security_id)
        trade = await self.chart_trades.get_open_for_underlying(underlying_security_id)
        state: Dict[str, Any] = {
            "underlyingSecurityId": underlying_security_id,
            "underlyingPrice": self.underlying_price(underlying_security_id),
            "trade": None,
        }
        if trade is None:
            return state

        position = await self.positions.get_open_for_security(trade.option_security_id)
        mark = self._option_mark(trade.option_security_id)
        unrealized = (
            PositionService.unrealized_pnl(position, mark) if position is not None else None
        )
        charges = Decimal(str(position.total_charges)) if position is not None else Decimal("0")
        realized = Decimal(str(position.realized_pnl)) if position is not None else Decimal("0")

        # Net P&L is gross minus every charge accrued so far. The exit's own
        # charges are not in here yet -- they are only known once it fills.
        net = None
        if unrealized is not None:
            net = (realized + unrealized - charges).quantize(MONEY_QUANTUM)

        state["trade"] = {
            "id": trade.id,
            "chartSide": trade.chart_side,
            "optionSecurityId": trade.option_security_id,
            "optionSymbol": trade.option_symbol,
            "optionType": trade.option_type,
            "strikePrice": trade.strike_price,
            "expiryDate": trade.expiry_date,
            "lots": trade.lots,
            "quantity": int(position.net_quantity) if position else 0,
            "averagePrice": position.average_price if position else None,
            "markPrice": mark,
            "underlyingAtEntry": trade.underlying_at_entry,
            "stopLossLevel": trade.stop_loss_level,
            "takeProfitLevel": trade.take_profit_level,
            "unrealizedPnl": unrealized,
            "realizedPnl": realized,
            "charges": charges,
            "netPnl": net,
            "openedAt": trade.opened_at,
        }
        return state
