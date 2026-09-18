"""The two clock times: what may be changed, and what may not.

Added 2026-09-18 with the Configuration tab. Most of this file is a negative,
because the interesting property is the LINE rather than the feature:

* the two times are editable, and moving them moves the scheduler;
* a time that would break something is REFUSED, not warned about, and both
  refusals are about correctness rather than taste;
* a PARAMETER of the rule is not editable and does not appear in the settings
  list at all -- not the momentum floor, not the ATR multiple, and not the
  rebalance cadence, which is P18 and looks the most like a schedule of any of
  them;
* a stored value that stops validating is ignored with a loud log rather than
  stopping the strategy, and the YAML's own time is used.
"""
from datetime import datetime, time

import pytest

import test_swing_execution as ex
from src.core.time_utils import IST
from src.strategies.services.strategy_definition import (
    KNOWN_SETTINGS,
    SETTING_NIGHTLY_AT,
    SETTING_REBALANCE_AT,
    StrategyConfigError,
)
from src.strategies.services.strategy_registry import get_strategy_registry
from src.strategies.services.strategy_state_service import StrategyStateService
from src.swing.database.db_models.swing_session_model import RUN_NIGHTLY, RUN_REBALANCE
from src.swing.services.schedule_settings import (
    ScheduleSettingError,
    describe_settings,
    resolve_schedule,
    validate,
)
from src.swing.services.swing_parameters import SwingParameters

STRATEGY = ex.STRATEGY


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return SwingParameters.from_definition(definition)


@pytest.fixture(autouse=True)
def _settings_baseline():
    """No stored override for the duration of a test, restored afterwards.

    The registry is a process-wide singleton; a test that moves a clock would
    otherwise move it for every test that ran after, including the ones whose
    subject IS the shipped schedule.
    """
    registry = get_strategy_registry()
    before = registry.setting_states()
    yield
    registry.apply_state({}, {}, None, None, before)


# --- the shipped schedule ----------------------------------------------------


def test_the_shipped_times_come_from_the_yaml(definition, parameters):
    schedule = resolve_schedule(definition, parameters)
    assert schedule.nightly_at == parameters.schedule.nightly_at
    assert schedule.rebalance_at == parameters.schedule.rebalance_at


def test_only_the_two_times_are_editable(definition):
    """The list IS the line. Nothing that defines the strategy may join it.

    If a momentum floor, an ATR multiple, a lookback or the rebalance cadence
    ever appears here, the YAML has stopped being greppable against the
    specification's own table and root CLAUDE.md section 3a has stopped being
    true.
    """
    assert set(KNOWN_SETTINGS) == {SETTING_NIGHTLY_AT, SETTING_REBALANCE_AT}
    keys = {row["key"] for row in describe_settings(definition)["settings"]}
    assert keys == {SETTING_NIGHTLY_AT, SETTING_REBALANCE_AT}


def test_the_rebalance_cadence_is_not_a_setting(definition, parameters):
    """P18, and the one most likely to be mistaken for a schedule.

    Daily measured 23.4% CAGR at -22.2% drawdown against weekly's 19.9% and
    -18.3%. Choosing between them is picking a different strategy, not
    configuring this one, so it stays in the YAML.
    """
    keys = {row["key"] for row in describe_settings(definition)["settings"]}
    assert not any("cadence" in key for key in keys)
    assert parameters.schedule.rebalance_cadence == "daily"


def test_a_discretionary_module_has_no_settings():
    from src.strategies.controllers.strategy_controller import StrategyController

    definition = get_strategy_registry().require("mcx-crude-options")
    assert StrategyController._settings_for(definition) == []


# --- what is refused ---------------------------------------------------------


def test_an_analysis_time_inside_the_session_is_refused(definition):
    """The refusal that matters most, because the failure is silent.

    The analysis refreshes daily bars from Dhan, and during trading hours Dhan
    returns TODAY'S FORMING BAR -- which would be stored as a finished daily
    bar. Every ranking, ATR and chandelier stop afterwards reads that row and
    nothing downstream can tell it was half a day's trading.
    """
    with pytest.raises(ScheduleSettingError) as error:
        validate(definition, SETTING_NIGHTLY_AT, "11:00")
    assert "inside the trading session" in str(error.value)
    assert "HALF-FINISHED" in str(error.value)

    # After the close and before the open are both fine.
    assert validate(definition, SETTING_NIGHTLY_AT, "18:15") == "18:15"
    assert validate(definition, SETTING_NIGHTLY_AT, "06:30") == "06:30"


def test_an_order_time_outside_the_session_is_refused(definition):
    """A strategy that decides every day and never trades is not a schedule."""
    with pytest.raises(ScheduleSettingError) as error:
        validate(definition, SETTING_REBALANCE_AT, "18:15")
    assert "outside continuous trading" in str(error.value)

    assert validate(definition, SETTING_REBALANCE_AT, "09:16") == "09:16"
    assert validate(definition, SETTING_REBALANCE_AT, "11:30") == "11:30"


def test_an_order_time_inside_the_auction_is_allowed_and_costs_something(definition):
    """15:20 is legal: non-F&O names still trade continuously to 15:30.

    A preference rather than a correctness failure, so it is allowed and the
    cost is said, rather than refused.
    """
    assert validate(definition, SETTING_REBALANCE_AT, "15:20") == "15:20"


def test_a_value_that_is_not_a_time_is_refused(definition):
    for value in ("25:00", "half past nine", "", "9:61"):
        with pytest.raises(ScheduleSettingError):
            validate(definition, SETTING_NIGHTLY_AT, value)


# --- storing, applying, ignoring --------------------------------------------


async def test_a_stored_time_moves_the_scheduler(db_session, definition):
    """The point of the whole feature: the clock actually changes."""
    service = StrategyStateService(db_session)
    await service.set_strategy_setting(STRATEGY, SETTING_REBALANCE_AT, "09:45")

    assert resolve_schedule(definition).rebalance_at == "09:45"

    # And the scheduler fires on the new time rather than the old one.
    from src.swing.services.scheduler import SwingScheduler

    scheduler = SwingScheduler()
    fired = []

    async def _rebalance(one, now):
        fired.append(now.time())

    async def _nightly(one, now):
        pass

    scheduler._run_rebalance = _rebalance
    scheduler._run_nightly = _nightly

    monday = datetime(2026, 9, 21, 9, 30, tzinfo=IST)   # after 09:16, before 09:45
    await scheduler._tick_strategy(definition, monday)
    assert fired == []

    await scheduler._tick_strategy(definition, monday.replace(minute=50))
    assert fired == [time(9, 50)]


async def test_clearing_a_setting_goes_back_to_the_configured_time(
    db_session, definition, parameters
):
    service = StrategyStateService(db_session)
    await service.set_strategy_setting(STRATEGY, SETTING_REBALANCE_AT, "09:45")
    assert resolve_schedule(definition).rebalance_at == "09:45"

    await service.set_strategy_setting(STRATEGY, SETTING_REBALANCE_AT, None)
    assert resolve_schedule(definition).rebalance_at == parameters.schedule.rebalance_at
    # The ROW is gone, rather than a row saying "use the default" -- a value
    # meaning "no value" would be a second way to express an absence.
    assert (await service.settings.states()).get(STRATEGY, {}).get(
        SETTING_REBALANCE_AT
    ) is None


async def test_the_service_refuses_a_breaking_time_before_it_is_stored(
    db_session, definition, parameters
):
    service = StrategyStateService(db_session)
    with pytest.raises(StrategyConfigError):
        await service.set_strategy_setting(STRATEGY, SETTING_NIGHTLY_AT, "11:00")

    # Nothing was written, and nothing moved.
    assert (await service.settings.states()).get(STRATEGY, {}) == {}
    assert resolve_schedule(definition).nightly_at == parameters.schedule.nightly_at


async def test_a_discretionary_module_refuses_a_setting(db_session):
    service = StrategyStateService(db_session)
    with pytest.raises(StrategyConfigError) as error:
        await service.set_strategy_setting(
            "mcx-crude-options", SETTING_REBALANCE_AT, "10:00"
        )
    assert "automation" in str(error.value)


def test_a_stored_row_for_an_unknown_strategy_or_setting_is_ignored():
    registry = get_strategy_registry()
    registry.apply_state({}, {}, None, None, {
        "no-such-strategy": {SETTING_REBALANCE_AT: "10:00"},
        "mcx-crude-options": {SETTING_REBALANCE_AT: "10:00"},
        STRATEGY: {"parameters.momentum_floor": "0.02"},
    })
    assert registry.setting_override("no-such-strategy", SETTING_REBALANCE_AT) is None
    assert registry.setting_override("mcx-crude-options", SETTING_REBALANCE_AT) is None
    # And above all: a PARAMETER of the rule cannot be smuggled in through a row.
    assert registry.setting_override(STRATEGY, "parameters.momentum_floor") is None


def test_a_stored_value_that_stopped_validating_is_ignored_not_fatal(
    definition, parameters
):
    """The strategy keeps running on its own configured time.

    A stored string can stop being legal -- the market hours could move under
    it. Stopping the scheduler over that would be a worse failure than running
    on the shipped schedule, so it is logged loudly and dropped.
    """
    schedule = resolve_schedule(
        definition, parameters, overrides={SETTING_NIGHTLY_AT: "11:00"}
    )
    assert schedule.nightly_at == parameters.schedule.nightly_at


def test_the_description_reports_both_the_default_and_what_is_in_force(
    definition, parameters
):
    rows = {row["key"]: row for row in describe_settings(definition)["settings"]}
    row = rows[SETTING_NIGHTLY_AT]
    assert row["default"] == parameters.schedule.nightly_at
    assert row["value"] == parameters.schedule.nightly_at
    # Nobody has touched it, which is a different fact from its being set to
    # the same value as the default.
    assert row["overridden"] is False
    assert row["allowed"]


async def test_the_warnings_endpoint_says_a_value_would_be_refused(db_session):
    """The refusal is computed before the operator confirms, not after.

    A form that offers an edit the server will refuse is worse than one that
    does not, so the dialog shows the reason and disables the button.
    """
    service = StrategyStateService(db_session)

    bad = await service.setting_warnings(STRATEGY, SETTING_NIGHTLY_AT, "11:00")
    assert bad["refusal"] is not None
    assert "inside the trading session" in bad["refusal"]

    good = await service.setting_warnings(STRATEGY, SETTING_NIGHTLY_AT, "18:15")
    assert good["refusal"] is None
    assert good["warnings"]
