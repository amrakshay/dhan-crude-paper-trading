"""Position bookkeeping.

`net_quantity` is signed and denominated in barrels (lots x lot_size), matching
the premium's per-barrel quotation, so P&L is simply
``(exit - entry) * quantity``.

Average price uses the standard weighted-average convention: adding to a
position re-weights the average, reducing it realises P&L against that average
and leaves it unchanged. A fill that flips the sign is split into a closing part
and an opening part, which closes the old position row and opens a new one --
that keeps each round trip separable in the reports rather than smearing two
different trades into one row.
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Tuple

from src.constants import OrderSide
from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.positions.database.db_models.position_model import Position
from src.positions.database.db_operations.position_repository import PositionRepository

logger = get_logger("positions.service")

PRICE_QUANTUM = Decimal("0.0001")
MONEY_QUANTUM = Decimal("0.01")


def _q(value: Decimal, quantum: Decimal = PRICE_QUANTUM) -> Decimal:
    return Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)


class PositionService:
    def __init__(self, repository: PositionRepository):
        self.repository = repository

    async def apply_fill(
        self,
        strategy_key: str,
        portfolio_id: int,
        security_id: str,
        trading_symbol: str,
        side: str,
        quantity: int,
        price: Decimal,
        lot_size: int,
        expiry_date=None,
        strike_price: Optional[Decimal] = None,
        option_type: Optional[str] = None,
        charges: Decimal = Decimal("0"),
    ) -> Tuple[Position, Decimal]:
        """Apply one execution. Returns (position, realised P&L from this fill).

        The realised figure is GROSS of charges; charges are accumulated on the
        position separately so the reports can show gross and net side by side.
        """
        side = str(side).upper()
        price = Decimal(str(price))
        signed = quantity if side == OrderSide.BUY.value else -quantity

        # Per portfolio: the same contract held in two portfolios is two
        # positions, and netting them would corrupt both books.
        position = await self.repository.get_open_for_security(
            portfolio_id, security_id
        )
        if position is None:
            position = await self._open_position(
                strategy_key, portfolio_id, security_id, trading_symbol, lot_size,
                expiry_date, strike_price, option_type,
            )

        realized = Decimal("0")
        old_net = int(position.net_quantity or 0)

        if old_net == 0 or (old_net > 0) == (signed > 0):
            # Opening or adding: re-weight the average.
            position.average_price = self._weighted_average(
                Decimal(str(position.average_price or 0)), abs(old_net), price, quantity
            )
            position.net_quantity = old_net + signed
        else:
            closing_quantity = min(quantity, abs(old_net))
            average = Decimal(str(position.average_price or 0))
            if old_net > 0:
                realized = (price - average) * closing_quantity      # long, sold
            else:
                realized = (average - price) * closing_quantity      # short, covered
            position.realized_pnl = _q(
                Decimal(str(position.realized_pnl or 0)) + realized, MONEY_QUANTUM
            )
            position.net_quantity = old_net + signed

            if quantity > closing_quantity:
                # The fill flipped the position. Close this row cleanly at zero
                # and open a fresh one for the residual, so the round trip that
                # just completed stays a separate record.
                residual = quantity - closing_quantity
                self._record_leg(position, side, closing_quantity, price, charges)
                position.net_quantity = 0
                await self._close_position(position)

                position = await self._open_position(
                    strategy_key, portfolio_id, security_id, trading_symbol,
                    lot_size, expiry_date, strike_price, option_type,
                )
                position.net_quantity = residual if side == OrderSide.BUY.value else -residual
                position.average_price = _q(price)
                self._record_leg(position, side, residual, price, Decimal("0"))
                logger.info(
                    "Position %s flipped: closed %s at %s (realised %s) and "
                    "opened %s in the opposite direction",
                    trading_symbol, closing_quantity, price, realized, residual,
                )
                await self.repository.session.flush()
                return position, _q(realized, MONEY_QUANTUM)

        self._record_leg(position, side, quantity, price, charges)

        logger.debug(
            "Position %s after %s %s @ %s: net %s -> %s, average %s, "
            "realised this fill %s, charges accrued %s",
            trading_symbol, side, quantity, price, old_net,
            position.net_quantity, position.average_price, realized, charges,
        )

        if position.net_quantity == 0:
            await self._close_position(position)

        await self.repository.session.flush()
        return position, _q(realized, MONEY_QUANTUM)

    @staticmethod
    def _weighted_average(
        current_average: Decimal, current_quantity: int, price: Decimal, quantity: int
    ) -> Decimal:
        total_quantity = current_quantity + quantity
        if total_quantity == 0:
            return Decimal("0")
        total_value = current_average * current_quantity + price * quantity
        return _q(total_value / Decimal(total_quantity))

    @staticmethod
    def _record_leg(
        position: Position, side: str, quantity: int, price: Decimal, charges: Decimal
    ) -> None:
        """Running gross totals, kept for audit."""
        value = price * quantity
        if side == OrderSide.BUY.value:
            position.buy_quantity = int(position.buy_quantity or 0) + quantity
            position.buy_value = _q(Decimal(str(position.buy_value or 0)) + value, MONEY_QUANTUM)
        else:
            position.sell_quantity = int(position.sell_quantity or 0) + quantity
            position.sell_value = _q(Decimal(str(position.sell_value or 0)) + value, MONEY_QUANTUM)
        if charges:
            position.total_charges = _q(
                Decimal(str(position.total_charges or 0)) + Decimal(str(charges)), MONEY_QUANTUM
            )

    async def _open_position(
        self, strategy_key, portfolio_id, security_id, trading_symbol, lot_size,
        expiry_date, strike_price, option_type,
    ) -> Position:
        position = Position(
            strategy_key=strategy_key,
            portfolio_id=portfolio_id,
            security_id=security_id,
            trading_symbol=trading_symbol,
            expiry_date=expiry_date,
            strike_price=strike_price,
            option_type=option_type,
            lot_size=lot_size,
            net_quantity=0,
            average_price=Decimal("0"),
            realized_pnl=Decimal("0"),
            total_charges=Decimal("0"),
            buy_quantity=0,
            sell_quantity=0,
            buy_value=Decimal("0"),
            sell_value=Decimal("0"),
            is_open=True,
            opened_at=utc_now(),
        )
        self.repository.session.add(position)
        await self.repository.session.flush()
        logger.info(
            "Position opened for %s (strategy=%s, security_id=%s, lot size %s, "
            "expiry %s)",
            trading_symbol, strategy_key, security_id, lot_size, expiry_date,
        )
        return position

    async def _close_position(self, position: Position) -> None:
        position.is_open = False
        position.closed_at = utc_now()
        position.average_price = Decimal("0")
        logger.info(
            "Position %s closed: realised %s gross, %s in charges, "
            "%s bought / %s sold",
            position.trading_symbol, position.realized_pnl,
            position.total_charges, position.buy_quantity, position.sell_quantity,
        )

    # --- marks -------------------------------------------------------------
    @staticmethod
    def unrealized_pnl(position: Position, mark_price: Optional[Decimal]) -> Optional[Decimal]:
        """Mark-to-market on the open quantity. None when there is no mark.

        Returning None rather than zero matters: a position with no live price
        has UNKNOWN unrealised P&L, and showing zero would read as flat.
        """
        net = int(position.net_quantity or 0)
        if net == 0 or mark_price is None:
            return None
        average = Decimal(str(position.average_price or 0))
        return _q((Decimal(str(mark_price)) - average) * net, MONEY_QUANTUM)
