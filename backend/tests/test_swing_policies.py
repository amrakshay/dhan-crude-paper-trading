"""Trading through the regime gate: the switches, and what they do not change.

This file is the safety net for the one deliberate divergence from the
specification in this application. The regime gate (P8) and the regime exit
(P17) are the specification's hard kill switch, and section 11 of it tested
thirteen ways of trading while the gate is off without finding one that beat
holding cash. On 2026-09-18 the owner chose to trade through it anyway, in
order to exercise the whole machine against a live market with paper money --
and chose to do it as a RUNTIME SWITCH with the state recorded per trade,
rather than as a rewrite of the rule.

So most of what is asserted here is a negative:

* the SHIPPED DEFAULT is unchanged -- with the gate off it still buys nothing
  and still liquidates, and the switches merely existing disturbs none of that;
* relaxing the gate changes ONLY whether P8/P17 stop anything. The breadth ramp
  still sizes the book, the momentum floor still applies, the rotation exit
  still fires and the chandelier stop is untouched;
* re-enforcing the gate does NOT sell an open book;
* the two contradictory switches are refused rather than silently ordered;
* no PARAMETER became editable -- P1 to P19 are still only in the YAML.

The seeding helpers are imported from `test_swing_execution` rather than
copied: they are two hundred lines of bar series, instrument rows and depth
books, and a second copy would drift.
"""
from decimal import Decimal

import pytest

import test_swing_execution as ex
from src.core.time_utils import utc_now
from src.portfolios.database.db_operations.portfolio_repository import (
    CashLedgerRepository,
)
from src.strategies.services.strategy_definition import (
    POLICY_OFF_GATE_ENABLED,
    POLICY_REGIME_ENFORCE,
    POLICY_REGIME_ENFORCE_ENTRY_RETURN,
)
from src.strategies.services.strategy_registry import get_strategy_registry
from src.swing.database.db_models.swing_session_model import (
    ACTION_BOUGHT,
    ACTION_HELD,
    ACTION_SKIPPED,
    ACTION_SOLD,
)
from src.swing.database.db_operations.swing_stop_repository import SwingStopRepository
from src.swing.services.gate_policy import (
    GatePolicy,
    GatePolicyError,
    default_policy,
    describe_policies,
    resolve_gate_policy,
)
from src.swing.services.rebalance_planner import (
    VARIANT_BASELINE,
    VARIANT_OFF_GATE,
    Holding,
    RebalancePlanner,
    effective_gate,
)
from src.swing.services.swing_parameters import SwingParameters

STRATEGY = ex.STRATEGY

# Both relaxations, which is the configuration the owner asked for on
# 2026-09-18. BOTH, because P9 is an independent switch: on 2026-09-17 the
# 63-session return was -3.71%, so relaxing only the regime gate would have
# produced no trades at all.
RELAXED = GatePolicy(
    enforce_regime=False, enforce_entry_return=False, off_gate_enabled=False
)
# The specification's own behaviour, and what a fresh checkout does.
ENFORCED = GatePolicy(
    enforce_regime=True, enforce_entry_return=True, off_gate_enabled=False
)


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return SwingParameters.from_definition(definition)


@pytest.fixture(autouse=True)
def _policy_baseline():
    """No stored override for the duration of a test, restored afterwards.

    The registry is a process-wide singleton, so a test that flips a switch
    would otherwise leak it into every test that ran after -- including the
    ones whose subject IS the shipped default. Snapshotted rather than reset to
    a believed default, for the reason `strategy_state_baseline` gives.
    """
    registry = get_strategy_registry()
    before = registry.policy_states()
    yield
    registry.apply_state({}, {}, None, {
        f"{key}/{policy}": value
        for key, policies in before.items()
        for policy, value in policies.items()
    })


async def _snapshot(db_session, definition, symbols=None):
    from src.daily_bars.database.db_operations.daily_bar_repository import (
        DailyBarRepository,
    )
    from src.swing.services.ranking_service import RankingService

    ranking = RankingService(DailyBarRepository(db_session), definition)
    ranking.universe_symbols = lambda: list(symbols or ex.SYMBOLS)  # noqa: E731
    return await ranking.session_snapshot()


# --- the YAML still says what the strategy IS -------------------------------


def test_the_shipped_default_enforces_every_rule(definition, parameters):
    """A fresh checkout obeys the specification. Nothing here changes that."""
    policy = default_policy(parameters)
    assert policy.enforce_regime is True
    assert policy.enforce_entry_return is True
    assert policy.off_gate_enabled is False
    assert policy.relaxed is False


def test_no_parameter_of_the_rule_became_a_switch(definition):
    """Root CLAUDE.md section 3a: the switches say whether a rule is ENFORCED.

    The moment a momentum floor, an ATR multiple or a rank cut-off appears in
    this list, the YAML has stopped being greppable against the specification's
    own table and that section has stopped being true.
    """
    keys = {row["key"] for row in describe_policies(definition)["policies"]}
    assert keys == {
        POLICY_REGIME_ENFORCE,
        POLICY_REGIME_ENFORCE_ENTRY_RETURN,
        POLICY_OFF_GATE_ENABLED,
    }


def test_a_discretionary_module_is_offered_no_policy_at_all():
    """MCX crude has no regime gate and a person in front of every order."""
    from src.strategies.controllers.strategy_controller import StrategyController

    definition = get_strategy_registry().require("mcx-crude-options")
    assert definition.automation.automated is False
    described = StrategyController._describe_policies(definition)
    assert described["policies"] == []


# --- the gate, resolved ------------------------------------------------------


async def test_relaxing_the_gate_keeps_the_breadth_ramp_and_the_momentum_floor(
    db_session, definition, parameters
):
    """It changes whether P8/P17 stop anything. It changes nothing else."""
    await ex._seed_instruments(db_session)
    await ex._seed_gate_off(db_session)
    snapshot = await _snapshot(db_session, definition)
    assert snapshot.regime.gate_on is False

    enforced = effective_gate(snapshot, parameters, ENFORCED)
    relaxed = effective_gate(snapshot, parameters, RELAXED)

    # The specification's behaviour, untouched.
    assert enforced.liquidates is True
    assert enforced.entries_allowed is False
    assert enforced.slots == 0

    # And the relaxation: no liquidation, entries allowed, and the slot count
    # and the floor are still the ordinary ones rather than V3b's.
    assert relaxed.variant == VARIANT_BASELINE
    assert relaxed.liquidates is False
    assert relaxed.entries_allowed is True
    assert relaxed.slots == snapshot.slots
    assert relaxed.momentum_floor == parameters.momentum_floor
    assert relaxed.gate_on is False          # still computed, still reported


async def test_relaxing_only_the_regime_gate_still_blocks_entries_on_p9(
    db_session, definition, parameters
):
    """Decision 2, as arithmetic.

    The 63-session return filter is a SEPARATE block on new entries. On
    2026-09-17 it stood at -3.71%, so relaxing P8 alone would have produced no
    trades at all -- which is why both switches exist and why both were
    relaxed.
    """
    await ex._seed_instruments(db_session)
    await ex._seed_gate_off(db_session)
    snapshot = await _snapshot(db_session, definition)
    assert snapshot.regime.entries_allowed is False

    half = GatePolicy(
        enforce_regime=False, enforce_entry_return=True, off_gate_enabled=False
    )
    gate = effective_gate(snapshot, parameters, half)
    assert gate.liquidates is False          # P17 relaxed
    assert gate.entries_allowed is False     # P9 still enforced


def test_the_off_gate_variant_and_a_relaxed_gate_are_refused_together():
    """Two operator-chosen overrides of the same thing, named in the refusal."""
    with pytest.raises(GatePolicyError) as error:
        GatePolicy(
            enforce_regime=False,
            enforce_entry_return=False,
            off_gate_enabled=True,
        )
    message = str(error.value)
    assert "off_gate.enabled" in message
    assert "regime.enforce" in message


async def test_the_contradiction_is_refused_by_the_service_not_only_the_page(
    db_session, definition
):
    """A stored row or a script must not be able to reach the pair either."""
    from src.strategies.services.strategy_definition import StrategyConfigError
    from src.strategies.services.strategy_state_service import StrategyStateService

    service = StrategyStateService(db_session)
    await service.set_strategy_policy(STRATEGY, POLICY_REGIME_ENFORCE, False)
    with pytest.raises(StrategyConfigError) as error:
        await service.set_strategy_policy(STRATEGY, POLICY_OFF_GATE_ENABLED, True)
    assert "off_gate.enabled" in str(error.value)


# --- what a stored row may and may not do ------------------------------------


def test_a_stored_policy_row_for_an_unknown_strategy_is_ignored():
    registry = get_strategy_registry()
    registry.apply_state({}, {}, None, {"no-such-strategy/regime.enforce": False})
    assert registry.policy_override("no-such-strategy", POLICY_REGIME_ENFORCE) is None


def test_a_stored_policy_row_for_a_discretionary_module_is_ignored():
    """A row must not be able to govern a module that has no rules of its own."""
    registry = get_strategy_registry()
    registry.apply_state({}, {}, None, {"mcx-crude-options/regime.enforce": False})
    assert registry.policy_override("mcx-crude-options", POLICY_REGIME_ENFORCE) is None


def test_an_unknown_policy_name_is_ignored():
    registry = get_strategy_registry()
    registry.apply_state({}, {}, None, {f"{STRATEGY}/momentum.floor": False})
    assert registry.policy_override(STRATEGY, "momentum.floor") is None


def test_nothing_stored_is_not_the_same_as_stored_false(definition, parameters):
    """None means "nobody has touched this switch", which is not False."""
    registry = get_strategy_registry()
    registry.apply_state({}, {}, None, {})
    assert registry.policy_override(STRATEGY, POLICY_REGIME_ENFORCE) is None
    assert resolve_gate_policy(definition, parameters).enforce_regime is True


# --- the rebalance, under each policy ---------------------------------------


async def test_with_the_shipped_default_a_gate_off_rebalance_still_buys_nothing(
    db_session, definition
):
    """The switches existing must not disturb the specification's behaviour."""
    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_off(db_session)
    ex._seed_book(ltp=100.0)
    ex._arm(True)

    outcome = await ex._service(
        db_session, definition, policy=ENFORCED
    ).run_rebalance(portfolio_id)

    assert outcome.buys_placed == 0
    rows = await ex._decisions(db_session, outcome.record.session_id)
    assert not [row for row in rows.values() if row.action == ACTION_BOUGHT]


async def test_with_the_gate_relaxed_a_gate_off_rebalance_buys(
    db_session, definition
):
    """The deliberate divergence, and the record that makes it reversible."""
    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_off(db_session)
    ex._seed_book(ltp=100.0)
    ex._arm(True)

    outcome = await ex._service(
        db_session, definition, policy=RELAXED
    ).run_rebalance(portfolio_id)

    assert outcome.buys_placed > 0

    rows = await ex._decisions(db_session, outcome.record.session_id)
    bought = [row for row in rows.values() if row.action == ACTION_BOUGHT]
    assert bought

    # EVERY decision row carries the regime it was taken under and the policy
    # in force, which is the entire reason this is acceptable to switch on.
    stored = await _decision_models(db_session, outcome.record.session_id)
    for row in stored:
        assert row.regime_gate_on is False
        assert row.regime_index_close is not None
        assert row.regime_index_sma is not None
        assert row.regime_enforced is False
        assert row.entry_return_enforced is False
        assert row.gate_variant == VARIANT_BASELINE


async def test_a_position_opened_with_the_gate_relaxed_records_that_on_its_stop(
    db_session, definition
):
    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_off(db_session)
    ex._seed_book(ltp=100.0)
    ex._arm(True)

    await ex._service(db_session, definition, policy=RELAXED).run_rebalance(
        portfolio_id
    )

    rows = await SwingStopRepository(db_session).history(STRATEGY, portfolio_id)
    assert rows
    for row in rows:
        assert row.entry_gate_on is False
        assert row.entry_regime_enforced is False


async def test_re_enforcing_the_gate_does_not_sell_a_book_opened_without_it(
    db_session, definition, parameters
):
    """Decision 3c. A settings change is not a liquidation.

    A position keeps the policy it was opened under. Turning enforcement back
    on stops NEW entries; the open book leaves by rotation or by its trailing
    stop, like every other position.
    """
    await ex._seed_instruments(db_session)
    await ex._seed_gate_off(db_session)
    snapshot = await _snapshot(db_session, definition)
    gate = effective_gate(snapshot, parameters, ENFORCED)
    assert gate.liquidates is True

    planner = RebalancePlanner(parameters, STRATEGY, ENFORCED)
    # The top-ranked name, so a ROTATION exit cannot be what spares it.
    kept = snapshot.candidates[0].symbol
    plan = planner.plan_sells(
        snapshot,
        [
            Holding(
                symbol=kept,
                security_id=ex.SECURITY_IDS[kept],
                quantity=100,
                entry_regime_enforced=False,
            ),
            Holding(
                symbol=snapshot.candidates[1].symbol,
                security_id=ex.SECURITY_IDS[snapshot.candidates[1].symbol],
                quantity=100,
                entry_regime_enforced=True,
            ),
        ],
    )

    sold = {intent.symbol for intent in plan.sells}
    assert kept not in sold                       # opened under the other policy
    assert snapshot.candidates[1].symbol in sold  # opened under this one
    assert kept in plan.exempt

    held = {row.symbol: row.reason for row in plan.holds}
    assert kept in held
    # The reason NAMES the policy rather than leaving a position that "should
    # have been sold" unexplained.
    assert "NOT being enforced" in held[kept]
    assert "keeps the policy it was opened under" in held[kept]


async def test_an_unknown_entry_policy_is_liquidated_rather_than_exempted(
    db_session, definition, parameters
):
    """The exemption fails towards the specification, not away from it."""
    await ex._seed_instruments(db_session)
    await ex._seed_gate_off(db_session)
    snapshot = await _snapshot(db_session, definition)

    planner = RebalancePlanner(parameters, STRATEGY, ENFORCED)
    symbol = snapshot.candidates[0].symbol
    plan = planner.plan_sells(
        snapshot,
        # The default, which is what `_holdings` uses for a position whose
        # policy cannot be established.
        [Holding(symbol=symbol, security_id=ex.SECURITY_IDS[symbol], quantity=100)],
    )
    assert [intent.symbol for intent in plan.sells] == [symbol]


async def test_the_exemption_is_from_p17_only_and_a_rotation_exit_still_fires(
    db_session, definition, parameters
):
    """A position opened under the relaxed policy is not held for ever."""
    await ex._seed_instruments(db_session)
    await ex._seed_gate_off(db_session)
    snapshot = await _snapshot(db_session, definition)

    planner = RebalancePlanner(parameters, STRATEGY, ENFORCED)
    # A name that has decayed past the rotation cut-off. Taken from the tail
    # of the ranking rather than searched for, so the test cannot depend on a
    # name happening to fall out of the universe.
    dropped = snapshot.candidates[-1].symbol
    assert snapshot.rank_of(dropped) > parameters.rotation_exit_rank
    plan = planner.plan_sells(
        snapshot,
        [
            Holding(
                symbol=dropped,
                security_id=ex.SECURITY_IDS[dropped],
                quantity=100,
                entry_regime_enforced=False,
            )
        ],
    )
    assert [intent.symbol for intent in plan.sells] == [dropped]
    assert plan.sells[0].kind == "ROTATION"


def test_the_exemption_does_not_reach_the_trailing_stop():
    """The chandelier stop is untouched by any of this.

    `is_hit` is a comparison of a price against a level. Nothing about which
    policy a position was opened under appears in it, and nothing should.
    """
    import inspect

    from src.swing.services.stop_service import StopService

    source = inspect.getsource(StopService.is_hit)
    assert "regime" not in source
    assert "enforce" not in source


async def _decision_models(db_session, session_id):
    from src.swing.database.db_operations.swing_session_repository import (
        SwingDecisionRepository,
    )

    return await SwingDecisionRepository(db_session).for_session(session_id)


# --- the payoff: the numbers can be split by the regime at entry -------------


async def test_the_performance_report_splits_by_the_regime_at_entry(
    db_session, definition
):
    """The whole reason the policy is recorded per trade.

    Trading through the regime gate is a deliberate divergence from the
    specification, and the only thing that makes it reversible is being able to
    take those trades back out of the numbers afterwards. A bucket with no
    trades reports NULL rather than zero: "no trades were taken with the gate
    off" and "the trades taken with the gate off averaged nothing" are
    different statements.
    """
    from src.swing.services.swing_service import SwingService

    await ex._seed_instruments(db_session)
    portfolio_id = await ex._seed_portfolio(db_session)
    await ex._seed_gate_off(db_session)
    ex._seed_book(ltp=100.0)
    ex._arm(True)

    service = ex._service(db_session, definition, policy=RELAXED)
    outcome = await service.run_rebalance(portfolio_id)
    assert outcome.buys_placed > 0

    # Close every one of them by hand, so there are CLOSED round trips to
    # report on. A closed position is what `MetricsService` counts.
    from src.positions.database.db_operations.position_repository import (
        PositionRepository,
    )

    positions = await PositionRepository(db_session).list_all(
        include_closed=False, strategy_key=STRATEGY, portfolio_id=portfolio_id
    )
    for position in positions:
        await service._order_service().submit_paper_order(
            security_id=position.security_id,
            side="SELL",
            order_type="MARKET",
            lots=int(position.net_quantity),
            is_close_order=True,
            portfolio_id=portfolio_id,
            strategy_key=STRATEGY,
        )
    await db_session.commit()

    report = await SwingService.for_strategy(
        db_session, STRATEGY, book=ex.get_feed_manager().book
    ).performance(portfolio_id)

    split = report["byRegimeAtEntry"]
    assert split["gateOff"] is not None
    assert split["gateOff"]["trades"] > 0
    # NULL, not a row of zeroes: nothing was opened with the gate on here.
    assert split["gateOn"] is None
    assert split["unattributed"] == 0
    # The curve half is deliberately absent: an equity curve is a property of
    # the whole book and slicing it by a subset would describe a book that
    # never existed.
    assert "cagr" not in split["gateOff"]
    assert "maxDrawdown" not in split["gateOff"]
