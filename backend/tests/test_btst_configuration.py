"""The parameters, the two policies and the two clock times.

The line root `CLAUDE.md` section 3a draws runs through this whole file: the
YAML says what the strategy IS, the database says whether a rule is OBEYED and
when the machine wakes up, and nothing in B1-B16 is editable from any page. The
assertions here are what keep that true as the module changes.
"""
import pytest

from src.btst.services.btst_parameters import (
    BtstConfigError,
    BtstParameters,
    RANK_MOM126,
    RANK_VOL_RATIO,
)
from src.btst.services.btst_policy import (
    BTST_POLICIES,
    BtstPolicy,
    default_policy,
    describe_policies,
    policy_warnings,
    resolve_policy,
)
from src.btst.services.btst_schedule import (
    BtstScheduleError,
    describe_settings,
    resolve_schedule,
    setting_warnings,
    validate,
)
from src.strategies.services.strategy_definition import (
    POLICY_FNO_EXCLUDE,
    POLICY_REGIME_ENFORCE,
    POLICY_REGIME_ENFORCE_ENTRY_RETURN,
    SETTING_EXIT_AT,
    SETTING_NIGHTLY_AT,
    SETTING_SCAN_AT,
)
from src.strategies.services.strategy_registry import get_strategy_registry

STRATEGY = "nse-btst-overnight"


@pytest.fixture
def definition():
    return get_strategy_registry().require(STRATEGY)


@pytest.fixture
def parameters(definition):
    return BtstParameters.from_definition(definition)


# --- B1 to B16 --------------------------------------------------------------


def test_every_parameter_matches_the_specifications_own_table(parameters):
    """The YAML, checked against section 2 line by line.

    This is the assertion that makes "the file is greppable against the
    specification" a fact rather than an intention. If one of these changes,
    somebody has chosen a different strategy and should have to say so here.
    """
    assert parameters.liquidity_floor_rupees == 100_000_000     # B2, Rs 10 cr
    assert parameters.liquidity_window_sessions == 20
    assert parameters.price_floor == 50.0                        # B3
    assert parameters.breakout_lookback_sessions == 55           # B4
    assert parameters.volume_multiple == 2.0                     # B5
    assert parameters.volume_window_sessions == 20
    assert parameters.close_location_minimum == 0.8              # B6
    assert parameters.trend_sma_sessions == 200                  # B7
    assert parameters.momentum_lookback_sessions == 126          # B8
    assert parameters.momentum_skip_sessions == 5
    assert parameters.momentum_floor == 0.15
    assert parameters.regime.sma_sessions == 200                 # B9
    assert parameters.ranking == RANK_VOL_RATIO                  # B10
    assert parameters.slots == 5                                 # B11
    assert parameters.position_size_divisor == 5                 # B12
    # Section 6's panel rule.
    assert parameters.minimum_sessions == 300


def test_there_is_no_stop_and_the_yaml_says_so_in_words(definition, parameters):
    """B15, spelled out where somebody will look for it.

    A missing key would be an absence a reader has to interpret; the word
    "none" is a statement. And a value other than "none" is REFUSED rather than
    ignored, so adding a stop has to be a deliberate act in code -- which it
    would have to be anyway, since the only risk window is one in which no
    order can execute at any price.
    """
    assert definition.module_section("parameters")["stop"] == "none"
    assert not hasattr(parameters, "trail_atr_multiple")

    document = dict(definition.module_section("parameters"))
    document["stop"] = "chandelier"
    broken = _definition_with(definition, parameters=document)
    with pytest.raises(BtstConfigError, match="none is possible"):
        BtstParameters.from_definition(broken)


def test_a_missing_parameter_names_the_file_and_the_key(definition, parameters):
    """No defaults. A value that quietly fell back is a silently different
    strategy, and the point of the YAML is that it can be checked against the
    specification's own table."""
    document = dict(definition.module_section("parameters"))
    del document["momentum_floor"]
    broken = _definition_with(definition, parameters=document)

    with pytest.raises(BtstConfigError) as error:
        BtstParameters.from_definition(broken)
    assert "nse-btst-overnight.yaml" in str(error.value)
    assert "momentum_floor" in str(error.value)


def test_a_close_location_floor_of_one_or_more_is_refused(definition):
    """CLV is bounded above BY ONE.

    A floor of 1.0 qualifies nothing at all and a floor above it is a strategy
    that can never trade. Both are configurations nobody meant, and a strategy
    that silently never fires is the hardest kind of broken to notice.
    """
    for value in (1.0, 1.5):
        document = dict(
            get_strategy_registry().require(STRATEGY).module_section("parameters")
        )
        document["close_location_minimum"] = value
        with pytest.raises(BtstConfigError, match="below 1"):
            BtstParameters.from_definition(_definition_with(definition, parameters=document))


def _definition_with(definition, **sections):
    """A copy of the definition with one module section replaced."""
    import copy

    clone = copy.copy(definition)
    config = dict(definition.module_config or {})
    config.update(sections)
    object.__setattr__(clone, "module_config", config)
    return clone


# --- the two policies -------------------------------------------------------


def test_this_module_offers_exactly_two_switches(definition):
    """And the absent one is as deliberate as the two that are there.

    There is no entry-return switch: the rotation's P9 has no counterpart in
    this specification, so the policy is not declared and the control is not
    offered. "This strategy does not have that rule" is a different fact from
    "it has it switched off".
    """
    assert set(BTST_POLICIES) == {POLICY_REGIME_ENFORCE, POLICY_FNO_EXCLUDE}
    keys = {row["key"] for row in describe_policies(definition)["policies"]}
    assert keys == {POLICY_REGIME_ENFORCE, POLICY_FNO_EXCLUDE}
    assert POLICY_REGIME_ENFORCE_ENTRY_RETURN not in keys


def test_both_switches_ship_ON(definition, parameters):
    """The specification's own recommendations, as the shipped defaults.

    Section 11 recommends the gate ON (better MAR, and the eleven-year evidence
    is strongest for it); section 5a recommends non-F&O names only (it removes
    the closing auction from the problem and carries the better half of the
    edge). A fresh checkout obeys both.
    """
    policy = default_policy(parameters)
    assert policy.enforce_regime is True
    assert policy.exclude_fno is True
    assert parameters.armed_by_default is False


def test_a_stored_override_beats_the_yaml_and_is_reported_as_an_override(
    definition, parameters
):
    """Both, always. A page showing only the file would describe a gate nothing
    obeys; one showing only the effective value would hide that the switch had
    been moved."""
    relaxed = resolve_policy(
        definition, parameters, overrides={POLICY_REGIME_ENFORCE: False}
    )
    assert relaxed.enforce_regime is False
    assert relaxed.exclude_fno is True     # untouched
    assert relaxed.relaxed is True

    rows = {row["key"]: row for row in describe_policies(definition)["policies"]}
    assert rows[POLICY_REGIME_ENFORCE]["default"] is True
    assert rows[POLICY_REGIME_ENFORCE]["overridden"] is False


def test_excluding_fno_names_is_not_a_RELAXATION(parameters):
    """`relaxed` means a rule is not being obeyed, and this switch is not that.

    Switching `fno.exclude` off widens the universe; it does not stop a rule
    acting. Calling it relaxed would put the "not enforced" warning on the page
    for a configuration that IS the headline backtest's.
    """
    wide = BtstPolicy(enforce_regime=True, exclude_fno=False)
    assert wide.relaxed is False
    narrow_but_ungated = BtstPolicy(enforce_regime=False, exclude_fno=True)
    assert narrow_but_ungated.relaxed is True


def test_no_combination_of_the_two_switches_is_refused(definition):
    """Unlike the rotation, which refuses one pair.

    `off_gate.enabled` with a relaxed `regime.enforce` are two answers to one
    question. These two answer different questions -- WHEN to trade and WHAT to
    trade -- so every combination is a strategy somebody could mean.
    """
    from src.btst.services.module_hooks import validate_policy_change

    for policy in BTST_POLICIES:
        for enforced in (True, False):
            validate_policy_change(definition, policy, enforced)   # no raise


def test_relaxing_the_gate_warns_that_it_costs_LESS_here_than_on_the_rotation(
    definition
):
    """The warning is about this strategy, not a generic one.

    Only 259 of 3,016 signals ever fire below the SMA, because a stock making a
    55-day high on twice its volume as a six-month momentum leader is itself
    evidence of a healthy tape. An operator deserves that number rather than
    the rotation's much sterner one.
    """
    warnings = " ".join(policy_warnings(definition, POLICY_REGIME_ENFORCE, False))
    assert "259 of 3,016" in warnings
    assert "18.98%" in warnings and "19.27%" in warnings


def test_including_fno_names_warns_about_the_closing_auction(definition):
    warnings = " ".join(policy_warnings(definition, POLICY_FNO_EXCLUDE, False))
    assert "15:15" in warnings
    assert "auction" in warnings.lower()
    assert "REFUSED" in warnings


# --- the two clock times ----------------------------------------------------


def test_the_scan_time_must_be_INSIDE_the_session(definition):
    """The mirror image of the rotation's constraint, and why the validator is
    per module.

    The rotation refuses an analysis time inside the session because it would
    store a forming bar as a finished one. This strategy reads the forming
    session ON PURPOSE and is useless outside it.
    """
    assert validate(definition, SETTING_SCAN_AT, "15:20") == "15:20"
    assert validate(definition, SETTING_SCAN_AT, "14:00") == "14:00"

    with pytest.raises(BtstScheduleError, match="outside the trading session"):
        validate(definition, SETTING_SCAN_AT, "18:15")
    with pytest.raises(BtstScheduleError, match="opposite of the rotation"):
        validate(definition, SETTING_SCAN_AT, "08:00")


def test_the_exit_time_must_be_at_the_OPEN_not_merely_inside_the_session(
    definition
):
    """THE OVERNIGHT GAP IS THE EDGE and it is given back during the session.

    An exit at 14:00 is inside continuous trading and would be accepted by a
    validator that only checked the session -- and it would turn a 71.4% win
    rate into something close to the 49.0% the specification measures for a
    next-close exit.
    """
    assert validate(definition, SETTING_EXIT_AT, "09:16") == "09:16"
    assert validate(definition, SETTING_EXIT_AT, "09:45") == "09:45"

    with pytest.raises(BtstScheduleError, match="49.0%"):
        validate(definition, SETTING_EXIT_AT, "14:00")
    with pytest.raises(BtstScheduleError, match="outside continuous trading"):
        validate(definition, SETTING_EXIT_AT, "18:00")


def test_a_stored_time_that_stops_validating_is_ignored_loudly(
    definition, parameters
):
    """A strategy that stopped running because a stored string went stale would
    be a worse failure than one running on its shipped schedule."""
    schedule = resolve_schedule(
        definition, parameters, overrides={SETTING_SCAN_AT: "23:00"}
    )
    assert schedule.scan_at == parameters.schedule.scan_at == "15:20"


def test_only_the_two_times_are_editable_for_this_strategy(definition):
    """The list IS the line.

    If a momentum floor, a volume multiple, a lookback or the slot count ever
    appears here, the YAML has stopped being greppable against the
    specification's own table.
    """
    keys = {row["key"] for row in describe_settings(definition)["settings"]}
    assert keys == {SETTING_SCAN_AT, SETTING_EXIT_AT}
    # And the rotation's two are not offered for this module.
    assert SETTING_NIGHTLY_AT not in keys


def test_the_exit_warning_says_what_is_actually_at_stake(definition):
    warnings = " ".join(setting_warnings(definition, SETTING_EXIT_AT, "09:16"))
    assert "THIS IS THE EDGE" in warnings
    assert "49.0%" in warnings
    assert "71.4%" in warnings


def test_moving_the_scan_warns_that_it_SPENDS_MONEY(definition):
    """Unlike the rotation's analysis, which places nothing at any hour.

    The scan decides and buys in the same pass, because what it decides on
    cannot be carried forward. An operator moving it must know that.
    """
    warnings = " ".join(setting_warnings(definition, SETTING_SCAN_AT, "15:20"))
    assert "SPENDS MONEY" in warnings
    assert "83.4%" in warnings
