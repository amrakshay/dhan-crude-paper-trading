"""Position bookkeeping: averaging, realisation, partial closes and reversals."""
from datetime import date
from decimal import Decimal

import pytest

from src.positions.database.db_operations.position_repository import PositionRepository
from src.positions.services.position_service import PositionService

SECURITY = "576375"
SYMBOL = "CRUDEOIL 17 SEP 6800 CALL"
LOT = 100


@pytest.fixture
async def service(db_session):
    return PositionService(PositionRepository(db_session))


async def _fill(service, side, quantity, price, charges="0"):
    return await service.apply_fill(
        security_id=SECURITY,
        trading_symbol=SYMBOL,
        side=side,
        quantity=quantity,
        price=Decimal(str(price)),
        lot_size=LOT,
        expiry_date=date(2026, 9, 17),
        strike_price=Decimal("6800"),
        option_type="CE",
        charges=Decimal(str(charges)),
    )


async def test_first_buy_opens_a_long_position(service):
    position, realized = await _fill(service, "BUY", 100, "50")

    assert position.net_quantity == 100
    assert position.average_price == Decimal("50.0000")
    assert realized == Decimal("0.00")
    assert position.is_open is True


async def test_first_sell_opens_a_short_position(service):
    position, _ = await _fill(service, "SELL", 100, "50")

    assert position.net_quantity == -100
    assert position.average_price == Decimal("50.0000")


async def test_adding_to_a_long_reweights_the_average(service):
    await _fill(service, "BUY", 100, "50")
    position, realized = await _fill(service, "BUY", 100, "60")

    assert position.net_quantity == 200
    assert position.average_price == Decimal("55.0000")
    assert realized == Decimal("0.00"), "adding realises nothing"


async def test_adding_uses_a_quantity_weighted_average(service):
    await _fill(service, "BUY", 100, "50")
    position, _ = await _fill(service, "BUY", 300, "60")

    # (100*50 + 300*60) / 400 = 57.5
    assert position.average_price == Decimal("57.5000")


async def test_partial_close_realises_against_the_average(service):
    await _fill(service, "BUY", 200, "50")
    position, realized = await _fill(service, "SELL", 100, "60")

    assert realized == Decimal("1000.00"), "(60 - 50) * 100"
    assert position.net_quantity == 100
    assert position.average_price == Decimal("50.0000"), "average is unchanged by a close"
    assert position.realized_pnl == Decimal("1000.00")
    assert position.is_open is True


async def test_closing_a_long_at_a_loss(service):
    await _fill(service, "BUY", 100, "50")
    _, realized = await _fill(service, "SELL", 100, "40")

    assert realized == Decimal("-1000.00")


async def test_covering_a_short_realises_the_opposite_way(service):
    await _fill(service, "SELL", 100, "50")
    position, realized = await _fill(service, "BUY", 100, "40")

    assert realized == Decimal("1000.00"), "a short profits when it covers lower"
    assert position.net_quantity == 0
    assert position.is_open is False


async def test_a_fully_closed_position_is_marked_closed(service):
    await _fill(service, "BUY", 100, "50")
    position, _ = await _fill(service, "SELL", 100, "55")

    assert position.net_quantity == 0
    assert position.is_open is False
    assert position.closed_at is not None
    assert position.realized_pnl == Decimal("500.00")


async def test_reopening_after_a_close_creates_a_separate_position(service):
    """Each round trip stays separable in the reports."""
    await _fill(service, "BUY", 100, "50")
    closed, _ = await _fill(service, "SELL", 100, "55")

    reopened, _ = await _fill(service, "BUY", 100, "60")

    assert reopened.id != closed.id
    assert reopened.is_open is True
    assert reopened.average_price == Decimal("60.0000")
    assert reopened.realized_pnl == Decimal("0.00"), "a new position starts flat"


async def test_a_reversal_closes_one_position_and_opens_another(service):
    """Selling 300 against a 100 long closes the long and opens a 200 short."""
    await _fill(service, "BUY", 100, "50")

    position, realized = await _fill(service, "SELL", 300, "60")

    assert realized == Decimal("1000.00"), "only the closing 100 realises"
    assert position.net_quantity == -200, "the residual opens a new short"
    assert position.average_price == Decimal("60.0000")
    assert position.is_open is True


async def test_a_reversal_leaves_the_old_position_closed(db_session, service):
    await _fill(service, "BUY", 100, "50")
    await _fill(service, "SELL", 300, "60")

    repository = PositionRepository(db_session)
    closed = await repository.list_closed()
    open_positions = await repository.list_open()

    assert len(closed) == 1
    assert closed[0].realized_pnl == Decimal("1000.00")
    assert len(open_positions) == 1
    assert open_positions[0].net_quantity == -200


async def test_charges_accumulate_on_the_position(service):
    await _fill(service, "BUY", 100, "50", charges="28.84")
    position, _ = await _fill(service, "SELL", 100, "60", charges="33.54")

    assert position.total_charges == Decimal("62.38")


async def test_running_totals_are_recorded_for_audit(service):
    await _fill(service, "BUY", 100, "50")
    position, _ = await _fill(service, "SELL", 60, "60")

    assert position.buy_quantity == 100
    assert position.sell_quantity == 60
    assert position.buy_value == Decimal("5000.00")
    assert position.sell_value == Decimal("3600.00")


# --- marks -----------------------------------------------------------------
async def test_unrealized_pnl_on_a_long(service):
    position, _ = await _fill(service, "BUY", 100, "50")

    assert PositionService.unrealized_pnl(position, Decimal("55")) == Decimal("500.00")
    assert PositionService.unrealized_pnl(position, Decimal("45")) == Decimal("-500.00")


async def test_unrealized_pnl_on_a_short_is_inverted(service):
    position, _ = await _fill(service, "SELL", 100, "50")

    assert PositionService.unrealized_pnl(position, Decimal("45")) == Decimal("500.00")
    assert PositionService.unrealized_pnl(position, Decimal("55")) == Decimal("-500.00")


async def test_unrealized_pnl_is_none_without_a_mark(service):
    """None, not zero. A position with no live price is UNKNOWN, not flat."""
    position, _ = await _fill(service, "BUY", 100, "50")

    assert PositionService.unrealized_pnl(position, None) is None


async def test_a_flat_position_has_no_unrealized_pnl(service):
    await _fill(service, "BUY", 100, "50")
    position, _ = await _fill(service, "SELL", 100, "55")

    assert PositionService.unrealized_pnl(position, Decimal("60")) is None
