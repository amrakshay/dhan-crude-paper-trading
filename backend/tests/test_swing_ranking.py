"""What the rule says about a session: regime, breadth, slots, ranking.

These assert the engine mechanics that are easy to get subtly wrong and
impossible to notice afterwards -- a breadth denominator that includes a name
it should not, a candidate priced off a close from two months ago, a gate that
reports OFF without the numbers that made it OFF.

The last test in this file reproduces section 13 of the specification against
the research project's own extended panel, and is skipped when that panel is
not on this machine.
"""
import os
import pathlib
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.services.ranking_service import (
    SKIP_BELOW_SMA200,
    SKIP_ILLIQUID,
    SKIP_MOMENTUM,
    SKIP_NOT_TRADED,
    SKIP_PRICE_FLOOR,
    SKIP_TOO_LITTLE_HISTORY,
    RankingService,
)
from src.swing.services.swing_parameters import SwingParameters


SEGMENT = "NSE_EQ"
INDEX_SEGMENT = "IDX_I"
START = date(2024, 1, 1)


def _bars(symbol, closes, segment=SEGMENT, volume=5_000_000, start=START):
    """A synthetic series on consecutive days, one bar per day."""
    rows = []
    for offset, close in enumerate(closes):
        rows.append(
            {
                "symbol": symbol,
                "exchange_segment": segment,
                "security_id": "1",
                "bar_date": start + timedelta(days=offset),
                "open": Decimal(str(close)),
                "high": Decimal(str(round(close * 1.01, 4))),
                "low": Decimal(str(round(close * 0.99, 4))),
                "close": Decimal(str(close)),
                "volume": volume,
                "source": "import",
            }
        )
    return rows


def _rising(count, start=100.0, step=1.0):
    return [round(start + step * index, 2) for index in range(count)]


def _flat(count, level=100.0):
    return [level] * count


@pytest.fixture
def definition():
    return get_strategy_registry().require("nse-swing-momentum")


@pytest.fixture
def parameters(definition):
    return SwingParameters.from_definition(definition)


def _service(session, definition, symbols):
    """A RankingService whose universe is exactly `symbols`.

    The shipped module's universe is 500 names; a test that had to seed all of
    them would be testing SQLite.
    """
    service = RankingService(DailyBarRepository(session), definition)
    service.universe_symbols = lambda: list(symbols)  # noqa: E731
    return service


# --- the regime gate --------------------------------------------------------


async def test_the_gate_reports_the_numbers_that_produced_it(db_session, definition):
    """"Gate OFF" is useless in six months; the inputs are the record."""
    repository = DailyBarRepository(db_session)
    # 260 sessions rising, then a sharp fall below the 200-session average.
    closes = _rising(260, 100.0, 1.0) + [200.0] * 5
    await repository.upsert_many(_bars("NIFTY", closes, segment=INDEX_SEGMENT))

    regime = await RankingService(repository, definition).regime_state()

    assert regime.gate_on is False
    assert regime.close == 200.0
    assert regime.sma200 is not None and regime.sma200 > regime.close
    assert regime.shortfall_percent is not None and regime.shortfall_percent > 0
    assert "200" in regime.reason and "SMA" in regime.reason
    assert regime.entries_allowed is False


async def test_a_gate_that_cannot_be_evaluated_is_off_not_assumed_on(
    db_session, definition
):
    """Too little index history must not read as a green light."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        _bars("NIFTY", _rising(50), segment=INDEX_SEGMENT)
    )

    regime = await RankingService(repository, definition).regime_state()

    assert regime.gate_on is False
    assert regime.entries_allowed is False
    assert "sessions" in regime.reason


async def test_no_index_bars_at_all_is_reported_rather_than_guessed(
    db_session, definition
):
    regime = await RankingService(
        DailyBarRepository(db_session), definition
    ).regime_state()

    assert regime.gate_on is False
    assert regime.close is None
    assert "No stored daily bars" in regime.reason


async def test_the_entry_filter_blocks_entries_without_forcing_an_exit(
    db_session, definition
):
    """P9 gates NEW ENTRIES only. It is not a liquidation signal."""
    repository = DailyBarRepository(db_session)
    # Rising for 250 sessions (so close > SMA200), then flat for 70 so the
    # 63-session return is zero rather than positive.
    closes = _rising(250, 100.0, 2.0) + _flat(70, 600.0)
    await repository.upsert_many(_bars("NIFTY", closes, segment=INDEX_SEGMENT))

    regime = await RankingService(repository, definition).regime_state()

    assert regime.gate_on is True, "the index is still above its own SMA200"
    assert regime.entries_allowed is False
    assert "NEW ENTRIES are blocked" in regime.reason
    assert "Existing positions are unaffected" in regime.reason


# --- the universe filters ---------------------------------------------------


async def test_a_symbol_that_did_not_trade_is_skipped_not_forward_filled(
    db_session, definition
):
    """The backtest's mechanic 1, and the reason JBCHEPHARM is not a candidate.

    JBCHEPHARM stopped trading on 2026-07-16. Forward-filled, it went on
    ranking as a buy candidate two months later -- at a price that no longer
    existed.
    """
    repository = DailyBarRepository(db_session)
    sessions = 300
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    await repository.upsert_many(_bars("TRADED", _rising(sessions, 100.0, 2.0)))
    # Same series, but it stops thirty sessions short.
    await repository.upsert_many(_bars("HALTED", _rising(sessions - 30, 100.0, 2.0)))

    snapshot = await _service(db_session, definition, ["TRADED", "HALTED"]).snapshot()

    assert snapshot.skipped["HALTED"] == SKIP_NOT_TRADED
    assert [candidate.symbol for candidate in snapshot.candidates] == ["TRADED"]
    assert snapshot.liquid_count == 1, "a halted name is in neither breadth term"


async def test_a_symbol_with_too_little_history_is_excluded_from_the_universe(
    db_session, definition, parameters
):
    """Excluded before every other filter, exactly as the loader excludes it."""
    repository = DailyBarRepository(db_session)
    sessions = 300
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    await repository.upsert_many(_bars("OLD", _rising(sessions, 100.0, 2.0)))
    young = parameters.minimum_sessions - 1
    await repository.upsert_many(
        _bars(
            "YOUNG",
            _rising(young, 100.0, 2.0),
            start=START + timedelta(days=sessions - young),
        )
    )

    snapshot = await _service(db_session, definition, ["OLD", "YOUNG"]).snapshot()

    assert snapshot.skipped["YOUNG"] == SKIP_TOO_LITTLE_HISTORY
    assert snapshot.liquid_count == 1


async def test_the_liquidity_and_price_floors_are_applied_and_recorded(
    db_session, definition
):
    repository = DailyBarRepository(db_session)
    sessions = 300
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    await repository.upsert_many(_bars("LIQUID", _rising(sessions, 100.0, 2.0)))
    # Rs 10 crore of turnover at a Rs 700 close needs ~143,000 shares a day.
    await repository.upsert_many(
        _bars("THIN", _rising(sessions, 100.0, 2.0), volume=100)
    )
    # Below the Rs 50 price floor, but heavily traded.
    await repository.upsert_many(
        _bars("PENNY", _rising(sessions, 1.0, 0.05), volume=500_000_000)
    )

    snapshot = await _service(
        db_session, definition, ["LIQUID", "THIN", "PENNY"]
    ).snapshot()

    assert snapshot.skipped["THIN"] == SKIP_ILLIQUID
    assert snapshot.skipped["PENNY"] == SKIP_PRICE_FLOOR
    assert snapshot.liquid_count == 1


async def test_breadth_counts_only_names_that_passed_the_floors(
    db_session, definition
):
    """P10's denominator is the LIQUID universe, not the whole one."""
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    await repository.upsert_many(_bars("UP", _rising(sessions, 100.0, 2.0)))
    # Rises for 250 sessions then collapses below its own 200-session average.
    await repository.upsert_many(
        _bars("DOWN", _rising(250, 100.0, 2.0) + _flat(sessions - 250, 120.0))
    )
    await repository.upsert_many(
        _bars("THIN", _rising(sessions, 100.0, 2.0), volume=100)
    )

    snapshot = await _service(
        db_session, definition, ["UP", "DOWN", "THIN"]
    ).snapshot()

    assert snapshot.liquid_count == 2, "the illiquid name is in neither term"
    assert snapshot.above_sma_count == 1
    assert snapshot.breadth == pytest.approx(0.5)
    assert snapshot.skipped["DOWN"] == SKIP_BELOW_SMA200


async def test_a_name_below_the_momentum_floor_is_not_a_candidate(
    db_session, definition
):
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    await repository.upsert_many(_bars("FAST", _rising(sessions, 100.0, 3.0)))
    # Above its own SMA200, but barely moving: momentum under the 10% floor.
    await repository.upsert_many(
        _bars("SLOW", _rising(sessions, 1000.0, 0.02))
    )

    snapshot = await _service(db_session, definition, ["FAST", "SLOW"]).snapshot()

    assert snapshot.above_sma_count == 2, "both are above their own SMA200"
    assert snapshot.skipped["SLOW"] == SKIP_MOMENTUM
    assert [candidate.symbol for candidate in snapshot.candidates] == ["FAST"]


async def test_every_candidate_is_ranked_not_just_the_top_ten(
    db_session, definition
):
    """A rotation exit is justified by a rank of 16 or of 40."""
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    symbols = [f"SYM{index:02d}" for index in range(14)]
    for index, symbol in enumerate(symbols):
        await repository.upsert_many(
            _bars(symbol, _rising(sessions, 100.0, 1.0 + index * 0.25))
        )

    snapshot = await _service(db_session, definition, symbols).snapshot()

    assert len(snapshot.candidates) == 14
    assert [candidate.rank for candidate in snapshot.candidates] == list(range(1, 15))
    assert snapshot.rank_of(snapshot.candidates[-1].symbol) == 14
    assert snapshot.rank_of("NOT-A-SYMBOL") is None


async def test_the_ranking_is_stable_between_two_runs_on_the_same_data(
    db_session, definition
):
    """An unstable tie-break shows up as a rotation exit nobody asked for."""
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    symbols = ["AAA", "BBB", "CCC"]
    for symbol in symbols:
        await repository.upsert_many(_bars(symbol, _rising(sessions, 100.0, 2.0)))

    service = _service(db_session, definition, symbols)
    first = await service.snapshot()
    second = await service.snapshot()

    assert [candidate.symbol for candidate in first.candidates] == [
        candidate.symbol for candidate in second.candidates
    ]
    assert [candidate.symbol for candidate in first.candidates] == symbols


async def test_the_snapshot_payload_carries_the_configuration_in_force(
    db_session, definition
):
    """A record that cannot say which parameters produced it is not a record."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        _bars("NIFTY", _rising(300, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )

    payload = (await _service(db_session, definition, []).snapshot()).as_dict()

    assert payload["parameters"]["momentumFloor"] == 0.10
    assert payload["parameters"]["rotationExitRank"] == 15
    assert payload["parameters"]["trailAtrMultiple"] == 3.5
    assert payload["parameters"]["rebalanceCadence"] == "daily"
    assert payload["parameters"]["offGateEnabled"] is False
    assert payload["regime"]["gateOn"] is True


async def test_unmeasurable_breadth_yields_no_slot_count(db_session, definition):
    """An empty liquid universe is not a breadth of zero."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        _bars("NIFTY", _rising(300, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )

    snapshot = await _service(db_session, definition, []).snapshot()

    assert snapshot.liquid_count == 0
    assert snapshot.breadth is None
    assert snapshot.slots is None


# --- section 13 of the specification ----------------------------------------

EXTENDED_PANEL = (
    pathlib.Path.home()
    / "Workarea/local/pullback/backend/intrday_test_strategy/research2/gate_run/data_ext"
)

# The specification's section 13 snapshot, as of 2026-09-17.
SPEC_13_TOP_15 = [
    "WELCORP", "HFCL", "LAURUSLABS", "CEMPRO", "ATHERENERG",
    "SYRMA", "NEULANDLAB", "BOSCHLTD", "GLAND", "OFSS",
    "RADICO", "CPPLUS", "AEGISLOG", "DIVISLAB", "BHEL",
]


# Opt-in, not merely "skipped when the panel is absent": on the owner's own
# machine the panel IS present, and importing its 1.1 million rows in every
# suite run would add four minutes to `pytest tests/`. Run it deliberately:
#
#     SWING_SECTION_13=1 .venv/bin/python -m pytest tests/test_swing_ranking.py -k section_13
RUN_SECTION_13 = os.environ.get("SWING_SECTION_13") == "1"


@pytest.mark.skipif(
    not (RUN_SECTION_13 and EXTENDED_PANEL.is_dir()),
    reason="set SWING_SECTION_13=1, with the research project's extended panel present",
)
async def test_section_13_of_the_specification_is_reproduced(db_session, definition):
    """Gate, breadth, slots and the top-15, against the owner's own snapshot.

    Slow -- it imports and ranks 500 symbols -- and skipped wherever the
    research panel is absent, which is every machine but the owner's. It is
    here because an indicator parity test proves each formula in isolation and
    this proves the whole pipeline agrees with a number a human wrote down.

    The panel is the *extended* one (Dhan spliced with a second vendor after
    2026-07-14), which is what section 13 was computed on. It is a fixture, and
    must never be imported as production data.
    """
    from src.daily_bars.services.bar_import_service import BarImportService

    repository = DailyBarRepository(db_session)
    wanted = [
        (symbol, SEGMENT, None, None)
        for symbol in sorted(definition.universe.symbols)
    ] + [("NIFTY", INDEX_SEGMENT, "13", "NIFTY50.csv")]
    # `drop_phantom_bars=False` deliberately. The extended panel carries a
    # flat zero-volume bar for 395 equities on 2026-09-14, a Monday NSE was
    # shut for, and the specification's section 13 figures were computed WITH
    # those rows. Importing them is what makes this a like-for-like
    # reproduction; the production path drops them (see test_daily_bars.py).
    await BarImportService(repository).import_symbols(
        str(EXTENDED_PANEL), wanted, drop_phantom_bars=False
    )

    snapshot = await RankingService(repository, definition).snapshot(
        as_of=date(2026, 9, 17)
    )

    assert snapshot.regime.close == pytest.approx(23270.6, abs=0.05)
    assert snapshot.regime.sma200 == pytest.approx(24501.7, abs=0.05)
    assert snapshot.regime.gate_on is False
    assert snapshot.regime.return_over_window == pytest.approx(-0.0371, abs=0.00005)
    assert snapshot.liquid_count == 448
    assert snapshot.above_sma_count == 215
    assert snapshot.breadth == pytest.approx(0.480, abs=0.0005)
    assert snapshot.slots == 4
    assert len(snapshot.candidates) == 199
    assert [
        candidate.symbol for candidate in snapshot.candidates[:15]
    ] == SPEC_13_TOP_15
