"""The decision journal, and the nightly run that writes it.

Akshay's requirement was "every decision taken by the system should be
recorded". Order events already record what happened to an ORDER; these tests
pin the harder half -- what the STRATEGY decided, including the sessions where
it decided to do nothing, which is every session since the gate turned off on
2026-02-27.

The assertion running through all of them is that a record carries the INPUTS,
not just the conclusion. "Gate OFF" cannot be checked in six months.
"""
from datetime import date, timedelta
from decimal import Decimal

import json
import pytest

from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.database.db_models.swing_session_model import (
    ACTION_BOUGHT,
    ACTION_HELD,
    ACTION_NOT_ENTERED,
    ACTION_SKIPPED,
    RUN_NIGHTLY,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
)
from src.swing.database.db_operations.swing_session_repository import (
    SwingDecisionRepository,
    SwingSessionRepository,
)
from src.swing.services.journal_service import SwingJournalService
from src.swing.services.ranking_service import RankingService
from src.swing.services.swing_parameters import SwingParameters
from src.swing.services.swing_runner import Holding, SwingRunner


SEGMENT = "NSE_EQ"
INDEX_SEGMENT = "IDX_I"
START = date(2024, 1, 1)
PORTFOLIO_ID = 1


def _bars(symbol, closes, segment=SEGMENT, volume=5_000_000, start=START):
    return [
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
        for offset, close in enumerate(closes)
    ]


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


async def _portfolio(db_session):
    """A portfolio row to hang the journal off, created directly."""
    from src.portfolios.database.db_models.portfolio_model import Portfolio

    existing = await db_session.get(Portfolio, PORTFOLIO_ID)
    if existing is not None:
        return existing
    portfolio = Portfolio(id=PORTFOLIO_ID, name="Swing Momentum", status="ACTIVE")
    db_session.add(portfolio)
    await db_session.flush()
    return portfolio


def _runner(db_session, definition, symbols):
    ranking = RankingService(DailyBarRepository(db_session), definition)
    ranking.universe_symbols = lambda: list(symbols)  # noqa: E731
    journal = SwingJournalService(
        SwingSessionRepository(db_session), SwingDecisionRepository(db_session)
    )
    return SwingRunner(ranking, journal, definition), journal


async def _seed_gate_off(db_session, extra_symbols=()):
    """An index that rose for 260 sessions and then fell hard below its SMA."""
    repository = DailyBarRepository(db_session)
    closes = _rising(260, 20000.0, 20.0) + _flat(10, 20500.0)
    await repository.upsert_many(_bars("NIFTY", closes, segment=INDEX_SEGMENT))
    for symbol in extra_symbols:
        await repository.upsert_many(
            _bars(symbol, _rising(len(closes), 100.0, 2.0))
        )
    return repository


# --- the gate is off: buy nothing, and say why with the numbers -------------


async def test_with_the_gate_off_a_nightly_run_buys_nothing_and_records_why(
    db_session, definition
):
    await _portfolio(db_session)
    await _seed_gate_off(db_session, ["AAA", "BBB"])
    runner, journal = _runner(db_session, definition, ["AAA", "BBB"])

    record = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)

    assert record.status == STATUS_COMPLETED
    decisions = await journal.decisions.for_session(record.session_id)
    bought = [one for one in decisions if one.action == ACTION_BOUGHT]
    blocked = [one for one in decisions if one.action == ACTION_NOT_ENTERED]

    assert bought == [], "nothing may be bought with the gate off"
    assert len(blocked) == 1
    assert "regime gate is OFF" in blocked[0].reason
    assert "below its 200-session SMA" in blocked[0].reason


async def test_the_session_record_carries_the_numbers_that_produced_the_decision(
    db_session, definition
):
    """"Gate OFF" is useless in six months; 20,500 against 22,470 is auditable."""
    await _portfolio(db_session)
    await _seed_gate_off(db_session, ["AAA"])
    runner, journal = _runner(db_session, definition, ["AAA"])

    record = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)
    stored = await journal.sessions.get_by_id(record.session_id)

    assert stored.index_symbol == "NIFTY"
    assert stored.index_close == Decimal("20500.0000")
    assert stored.index_sma is not None and stored.index_sma > stored.index_close
    assert stored.gate_on is False
    assert stored.entries_allowed is False
    assert stored.index_return_over_window is not None
    # The breadth fraction is not enough: the numerator and denominator are
    # what let someone check it.
    assert stored.breadth_liquid is not None
    assert stored.breadth_above is not None
    assert stored.universe_size == 1


async def test_the_configuration_in_force_is_stored_with_the_decision(
    db_session, definition
):
    await _portfolio(db_session)
    await _seed_gate_off(db_session, ["AAA"])
    runner, journal = _runner(db_session, definition, ["AAA"])

    record = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)
    stored = await journal.sessions.get_by_id(record.session_id)
    parameters = json.loads(stored.parameters_json)

    assert parameters["momentumFloor"] == 0.10
    assert parameters["rotationExitRank"] == 15
    assert parameters["rebalanceCadence"] == "daily"
    assert parameters["offGateEnabled"] is False


# --- the gate is on but breadth is thin ------------------------------------


async def _seed_gate_on_thin_breadth(db_session):
    """Index above its own SMA; most names below theirs, so breadth < 35%."""
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    # One name above its SMA200, four below theirs -> breadth 20%.
    await repository.upsert_many(_bars("UP", _rising(sessions, 100.0, 3.0)))
    for name in ("D1", "D2", "D3", "D4"):
        await repository.upsert_many(
            _bars(name, _rising(250, 100.0, 2.0) + _flat(sessions - 250, 120.0))
        )
    return ["UP", "D1", "D2", "D3", "D4"]


async def test_breadth_below_the_floor_resolves_to_zero_slots_and_buys_nothing(
    db_session, definition
):
    await _portfolio(db_session)
    symbols = await _seed_gate_on_thin_breadth(db_session)
    runner, journal = _runner(db_session, definition, symbols)

    record = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)
    stored = await journal.sessions.get_by_id(record.session_id)
    decisions = await journal.decisions.for_session(record.session_id)

    assert stored.gate_on is True
    assert stored.breadth_above == 1 and stored.breadth_liquid == 5
    assert stored.slots == 0
    assert [one for one in decisions if one.action == ACTION_BOUGHT] == []
    blocked = [one for one in decisions if one.action == ACTION_NOT_ENTERED]
    assert len(blocked) == 1
    assert "20.0%" in blocked[0].reason and "0 slots" in blocked[0].reason


# --- rotation exits ---------------------------------------------------------


async def test_a_holding_that_falls_past_the_exit_rank_is_recorded_with_the_rank(
    db_session, definition
):
    """A rotation exit is justified by a number, and the number is stored."""
    await _portfolio(db_session)
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    # Twenty names, all rising; the slowest is last in the ranking.
    symbols = [f"S{index:02d}" for index in range(20)]
    for index, symbol in enumerate(symbols):
        await repository.upsert_many(
            _bars(symbol, _rising(sessions, 100.0, 4.0 - index * 0.15))
        )

    runner, journal = _runner(db_session, definition, symbols)
    laggard = symbols[-1]
    record = await runner.run_nightly(
        portfolio_id=PORTFOLIO_ID,
        holdings=[Holding(symbol=laggard, security_id="1", quantity=100)],
    )

    decisions = await journal.decisions.for_session(record.session_id)
    held = [one for one in decisions if one.symbol == laggard]
    assert len(held) == 1
    assert held[0].action == ACTION_HELD
    assert held[0].rank is not None and held[0].rank > 15
    assert "Rotation exit" in held[0].reason
    assert f"rank {held[0].rank} > 15" in held[0].reason


async def test_a_held_names_rank_is_stored_even_when_it_falls_out_of_the_top_list(
    db_session, definition
):
    """The one case where the tail of the ranking matters is the one held."""
    await _portfolio(db_session)
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    symbols = [f"S{index:02d}" for index in range(5)]
    for index, symbol in enumerate(symbols):
        await repository.upsert_many(
            _bars(symbol, _rising(sessions, 100.0, 4.0 - index * 0.5))
        )
    # A name that is held but fails a filter, so it is not ranked at all.
    await repository.upsert_many(_bars("DROPPED", _rising(sessions, 1.0, 0.002)))

    runner, journal = _runner(db_session, definition, symbols + ["DROPPED"])
    record = await runner.run_nightly(
        portfolio_id=PORTFOLIO_ID,
        holdings=[Holding(symbol="DROPPED", security_id="9", quantity=10)],
    )

    stored = await journal.sessions.get_by_id(record.session_id)
    ranking = json.loads(stored.ranking_json)
    held_entry = next(one for one in ranking["held"] if one["symbol"] == "DROPPED")

    assert held_entry["ranked"] is False
    assert held_entry["skipReason"] is not None

    decisions = await journal.decisions.for_session(record.session_id)
    dropped = next(one for one in decisions if one.symbol == "DROPPED")
    assert "no longer ranked" in dropped.reason


async def test_a_candidate_beyond_the_slot_count_is_skipped_with_the_reason(
    db_session, definition
):
    await _portfolio(db_session)
    repository = DailyBarRepository(db_session)
    sessions = 320
    await repository.upsert_many(
        _bars("NIFTY", _rising(sessions, 20000.0, 20.0), segment=INDEX_SEGMENT)
    )
    # Eight strong names above their own SMAs -> breadth 100% -> 10 slots,
    # so all eight would be entered; add two weak ones to pull breadth down.
    strong = [f"S{index}" for index in range(8)]
    for index, symbol in enumerate(strong):
        await repository.upsert_many(
            _bars(symbol, _rising(sessions, 100.0, 4.0 - index * 0.2))
        )
    weak = ["W1", "W2", "W3", "W4", "W5", "W6", "W7"]
    for symbol in weak:
        await repository.upsert_many(
            _bars(symbol, _rising(250, 100.0, 2.0) + _flat(sessions - 250, 120.0))
        )

    runner, journal = _runner(db_session, definition, strong + weak)
    record = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)

    stored = await journal.sessions.get_by_id(record.session_id)
    decisions = await journal.decisions.for_session(record.session_id)
    bought = [one for one in decisions if one.action == ACTION_BOUGHT]
    skipped = [one for one in decisions if one.action == ACTION_SKIPPED]

    assert stored.slots is not None and 0 < stored.slots < 8
    assert len(bought) == stored.slots
    assert skipped, "the names that missed out are recorded, not forgotten"
    assert "slots full" in skipped[0].reason


# --- idempotence and closed markets ----------------------------------------


async def test_running_twice_for_one_session_does_not_decide_twice(
    db_session, definition
):
    """A restart at 18:20 must not re-decide what was decided at 18:15."""
    await _portfolio(db_session)
    await _seed_gate_off(db_session, ["AAA"])
    runner, journal = _runner(db_session, definition, ["AAA"])

    first = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)
    second = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)

    assert second.session_id == first.session_id
    assert second.extras.get("idempotent") is True
    assert await journal.sessions.count_for(definition.key) == 1


async def test_force_re_decides_and_appends_rather_than_editing(
    db_session, definition
):
    """A reconsideration is a NEW record. Nothing is ever edited."""
    await _portfolio(db_session)
    await _seed_gate_off(db_session, ["AAA"])
    runner, journal = _runner(db_session, definition, ["AAA"])

    first = await runner.run_nightly(portfolio_id=PORTFOLIO_ID)
    second = await runner.run_nightly(portfolio_id=PORTFOLIO_ID, force=True)

    assert second.session_id != first.session_id
    assert await journal.sessions.count_for(definition.key) == 2


async def test_no_index_bar_records_a_skipped_run_rather_than_nothing(
    db_session, definition
):
    """"The job did not run" and "there was no session" are different facts."""
    await _portfolio(db_session)
    runner, journal = _runner(db_session, definition, [])

    record = await runner.run_nightly(
        portfolio_id=PORTFOLIO_ID, as_of=date(2026, 9, 17)
    )

    assert record.status == STATUS_SKIPPED
    stored = await journal.sessions.get_by_id(record.session_id)
    assert stored.run_kind == RUN_NIGHTLY
    assert "No stored daily bars" in stored.message


async def test_the_journal_refuses_to_edit_or_delete_a_record(db_session):
    """Append-only, and ENFORCED rather than merely documented.

    `BaseRepository` hands every subclass an `update` and a `delete`, so
    `CashLedgerRepository`'s "append-only" has only ever been a convention.
    The journal raises instead: a decision record that can be edited is not a
    record of what was decided.
    """
    for repository in (
        SwingSessionRepository(db_session),
        SwingDecisionRepository(db_session),
    ):
        with pytest.raises(NotImplementedError, match="append-only"):
            await repository.update(1, reason="rewritten")
        with pytest.raises(NotImplementedError, match="append-only"):
            await repository.delete(1)


async def test_a_decision_can_be_traced_back_for_one_symbol(db_session, definition):
    await _portfolio(db_session)
    await _seed_gate_off(db_session, ["AAA"])
    runner, journal = _runner(db_session, definition, ["AAA"])

    await runner.run_nightly(
        portfolio_id=PORTFOLIO_ID,
        holdings=[Holding(symbol="AAA", security_id="1", quantity=10)],
    )
    history = await journal.decisions.for_symbol(definition.key, "AAA")

    assert len(history) == 1
    assert history[0].symbol == "AAA"
    assert "Regime exit" in history[0].reason
