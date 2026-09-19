"""A strategy that owns MANY underlyings.

Everything before 2026-09-18 assumed a strategy owned exactly one: a symbol on
a segment, matched by equality. A momentum rotation owns 500 of the 9,884 NSE
EQUITY rows in Dhan's master, so ownership resolves against a named universe
instead.

Two properties are asserted together throughout, because the second is the one
that actually costs money: the new shape works, AND the MCX crude module
resolves exactly as it did before.
"""
import textwrap

import pytest

from src.instruments.services.instrument_master_service import (
    InstrumentMasterError,
    InstrumentMasterService,
)
from src.strategies.services.strategy_definition import (
    InstrumentSet,
    StrategyConfigError,
    SubscriptionPolicy,
    build_definition,
)
from src.strategies.services.strategy_registry import (
    StrategyRegistry,
    get_strategy_registry,
)

import yaml


# --- fixtures ---------------------------------------------------------------

MASTER_HEADER = (
    "EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,"
    "UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,"
    "LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE"
)

# Row shapes copied from the live master, verified 2026-09-18.
MASTER_ROWS = [
    # An ordinary constituent.
    "NSE,E,438,INE257A01026,EQUITY,,BHEL,BHEL,Bharat Heavy Electricals,ES,EQ,"
    "1.0,,,,5.0000",
    # A constituent in the trade-for-trade series. Still the share.
    "NSE,E,21954,INE548A01028,EQUITY,,HFCL,HFCL LIMITED,HFCL,ES,BE,"
    "1.0,,,,1.0000",
    # The share...
    "NSE,E,685,INE121A01024,EQUITY,,CHOLAFIN,CHOLAMANDALAM IN & FIN CO,"
    "Cholamandalam Investment,ES,EQ,1.0,,,,10.0000",
    # ...and a listed NCD published under the SAME underlying symbol.
    "NSE,E,19257,INE121A08PJ0,EQUITY,,CHOLAFIN,CHOLAMANDALAM IN & FIN CO,"
    "CIFCL-7.5%-30092026-NCD,ES,D1,1.0,,,,5.0000",
    # Not in the universe.
    "NSE,E,1594,INE009A01021,EQUITY,,INFY,INFOSYS LIMITED,Infosys,ES,EQ,"
    "1.0,,,,5.0000",
    # MCX crude, so the two strategies can be parsed in one pass.
    "MCX,M,565899,NA,FUTCOM,294,CRUDEOIL,CRUDEOIL,CRUDEOIL SEP FUT,FUTCOM,NA,"
    "1.0,2026-09-21,0.00000,XX,100.0000",
    "MCX,M,576266,NA,OPTFUT,294,CRUDEOIL,CRUDEOIL,CRUDEOIL 17 SEP 7000 CALL,"
    "OPTFUT,NA,1.0,2026-09-17,7000.00000,CE,10.0000",
]


@pytest.fixture
def master_csv(tmp_path):
    path = tmp_path / "master.csv"
    path.write_text("\n".join([MASTER_HEADER, *MASTER_ROWS]) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def universe_file(tmp_path):
    """Four symbols, one of which the master does not publish."""
    path = tmp_path / "universe.csv"
    path.write_text(
        textwrap.dedent(
            """\
            symbol,security_id,name,exchange_segment
            BHEL,438,Bharat Heavy Electricals Ltd.,NSE_EQ
            HFCL,21951,HFCL Ltd.,NSE_EQ
            CHOLAFIN,685,Cholamandalam Investment,NSE_EQ
            DELISTEDCO,99999,No Longer Listed Ltd.,NSE_EQ
            """
        ),
        encoding="utf-8",
    )
    return str(path)


def _equity_document(universe_file, **overrides):
    document = {
        "key": "test-rotation",
        "label": "Test Rotation",
        "enabled_by_default": False,
        "underlying": {
            "symbol": "TESTUNIVERSE",
            "exchange_segment": "NSE_EQ",
            "exchange_segment_code": 1,
            "exchange_id": "NSE",
        },
        "universe": {"name": "test", "file": universe_file},
        "instrument_sets": [
            {
                "instrument_type": "EQUITY",
                "exchange_segment": "NSE_EQ",
                "exchange_segment_code": 1,
                "symbols": "universe",
                "lot_size_source": "master",
                "series": ["EQ", "BE"],
                "trading_symbol_source": "underlying_symbol",
            }
        ],
        "reference_instruments": {
            "regime_index": {
                "symbol": "NIFTY",
                "label": "NIFTY 50",
                "security_id": "13",
                "exchange_segment": "IDX_I",
                "exchange_segment_code": 0,
                "instrument": "INDEX",
            }
        },
        "contract_specs": {"tick_size_divisor": 100},
        "market_hours": {
            "timezone": "Asia/Kolkata",
            "open": "09:15",
            "close": "15:30",
            "trading_days": [0, 1, 2, 3, 4],
        },
        "subscription": {"kind": "positions"},
        "charges": {"rate_card": "nse-equity-delivery"},
        "capabilities": ["pnl-reports"],
    }
    document.update(overrides)
    return document


@pytest.fixture
def rotation(universe_file):
    return build_definition(_equity_document(universe_file), source="test-rotation.yaml")


@pytest.fixture
def crude():
    return get_strategy_registry().require("mcx-crude-options")


# --- ownership --------------------------------------------------------------


def test_a_universe_strategy_owns_every_symbol_in_its_file(rotation):
    assert len(rotation.universe.symbols) == 4
    for symbol in ("BHEL", "HFCL", "CHOLAFIN", "DELISTEDCO"):
        assert rotation.owns_instrument("NSE_EQ", symbol), symbol


def test_a_universe_strategy_owns_nothing_outside_its_file(rotation):
    assert not rotation.owns_instrument("NSE_EQ", "INFY")
    assert not rotation.owns_instrument("NSE_EQ", "RELIANCE")


def test_ownership_is_still_segment_scoped(rotation):
    """BHEL on another segment is not this strategy's instrument."""
    assert not rotation.owns_instrument("BSE_EQ", "BHEL")
    assert not rotation.owns_instrument("MCX_COMM", "BHEL")


def test_the_single_underlying_strategy_is_unchanged(crude):
    """MCX crude must resolve exactly as it did before universes existed."""
    assert crude.universe is None
    assert crude.owns_instrument("MCX_COMM", "CRUDEOIL")
    assert not crude.owns_instrument("MCX_COMM", "GOLD")
    assert not crude.owns_instrument("NSE_EQ", "CRUDEOIL")
    assert crude.symbols() == frozenset({"CRUDEOIL"})
    assert crude.segments() == frozenset({"MCX_COMM"})
    assert crude.subscription.kind == SubscriptionPolicy.OPTION_CHAIN
    assert crude.subscription.strike_window == 20


def test_a_single_underlying_strategy_synthesises_its_instrument_sets(crude):
    """The crude YAML declares none, so they come from its option/futures types."""
    types = {one.instrument_type for one in crude.instrument_sets}
    assert types == {"OPTFUT", "FUTCOM"}
    assert all(one.exchange_segment == "MCX_COMM" for one in crude.instrument_sets)
    assert all(not one.trusts_master_lot_size() for one in crude.instrument_sets)


def test_the_regime_index_is_declared_but_not_an_instrument_set(rotation):
    """It is read, never traded -- and never ingested, because ids collide.

    Dhan's security ids are unique per segment: 13 is NIFTY in IDX_I and ABB
    in NSE_EQ, and ABB is a Nifty 500 constituent.
    """
    index = rotation.reference_instrument("regime_index")
    assert index is not None
    assert (index.security_id, index.exchange_segment) == ("13", "IDX_I")
    assert not rotation.owns_instrument("IDX_I", "NIFTY")
    assert "IDX_I" not in rotation.segments()


# --- configuration errors ---------------------------------------------------


def test_a_missing_universe_file_is_fatal(tmp_path):
    document = _equity_document(str(tmp_path / "nope.csv"))
    with pytest.raises(StrategyConfigError, match="does not exist"):
        build_definition(document, source="test.yaml")


def test_a_universe_with_a_repeated_symbol_is_fatal(tmp_path):
    path = tmp_path / "dupes.csv"
    path.write_text("symbol\nBHEL\nBHEL\n", encoding="utf-8")
    with pytest.raises(StrategyConfigError, match="more than once"):
        build_definition(_equity_document(str(path)), source="test.yaml")


def test_claiming_the_universe_without_declaring_one_is_fatal(tmp_path):
    document = _equity_document(str(tmp_path / "x.csv"))
    document.pop("universe")
    with pytest.raises(StrategyConfigError, match="no 'universe' block"):
        build_definition(document, source="test.yaml")


def test_an_unknown_lot_size_source_is_fatal(universe_file):
    document = _equity_document(universe_file)
    document["instrument_sets"][0]["lot_size_source"] = "vibes"
    with pytest.raises(StrategyConfigError, match="lot_size_source"):
        build_definition(document, source="test.yaml")


# --- ingestion --------------------------------------------------------------


def test_the_masters_lot_size_is_trusted_for_equities(rotation, master_csv):
    """LOT_SIZE=1.0 is CORRECT for a delivery trade -- the opposite of MCX."""
    service = InstrumentMasterService(repository=None, strategies=[rotation])
    rows, _, _ = service.parse(master_csv)

    assert {row["lot_size"] for row in rows} == {1}
    assert all("LOT_SIZE<=1" not in row.get("warning", "") for row in rows)


def test_an_equity_strategy_needs_no_contract_specs_lot_size(rotation, master_csv):
    """500 contract_specs entries restating a number the master gets right."""
    assert rotation.lot_size() is None
    InstrumentMasterService(repository=None, strategies=[rotation]).parse(master_csv)


def test_a_strategy_that_does_not_trust_the_master_still_needs_one(
    universe_file, master_csv
):
    document = _equity_document(universe_file)
    document["instrument_sets"][0]["lot_size_source"] = "config"
    definition = build_definition(document, source="test.yaml")

    service = InstrumentMasterService(repository=None, strategies=[definition])
    with pytest.raises(InstrumentMasterError, match="contract_specs"):
        service.parse(master_csv)


def test_only_the_configured_series_is_ingested(rotation, master_csv):
    """CHOLAFIN publishes a share (EQ) and an NCD (D1) under one symbol."""
    service = InstrumentMasterService(repository=None, strategies=[rotation])
    rows, _, _ = service.parse(master_csv)

    cholafin = [row for row in rows if row["underlying_symbol"] == "CHOLAFIN"]
    assert len(cholafin) == 1, "the debenture must not be ingested as the share"
    assert cholafin[0]["security_id"] == "685"


def test_the_trading_symbol_is_the_ticker_for_an_equity(rotation, master_csv):
    service = InstrumentMasterService(repository=None, strategies=[rotation])
    rows, _, _ = service.parse(master_csv)
    bhel = next(row for row in rows if row["underlying_symbol"] == "BHEL")

    assert bhel["trading_symbol"] == "BHEL"
    assert bhel["display_name"] == "Bharat Heavy Electricals"


def test_tick_size_is_converted_from_paise_on_nse_too(rotation, master_csv):
    """The crude YAML claimed NSE needs no divisor. It does."""
    from decimal import Decimal

    service = InstrumentMasterService(repository=None, strategies=[rotation])
    rows, _, _ = service.parse(master_csv)
    by_symbol = {row["underlying_symbol"]: row for row in rows}

    assert by_symbol["BHEL"]["tick_size"] == Decimal("0.05")
    assert by_symbol["HFCL"]["tick_size"] == Decimal("0.01")
    assert by_symbol["CHOLAFIN"]["tick_size"] == Decimal("0.10")


def test_a_symbol_the_master_does_not_publish_is_reported_not_dropped(
    rotation, master_csv
):
    """A universe that quietly shrinks still produces a ranking, just a wrong one."""
    service = InstrumentMasterService(repository=None, strategies=[rotation])
    rows, _, warnings = service.parse(master_csv)

    assert "DELISTEDCO" not in {row["underlying_symbol"] for row in rows}
    assert any("DELISTEDCO" in warning for warning in warnings)


def test_a_declared_security_id_that_differs_from_the_master_is_reported(
    rotation, master_csv
):
    """The master wins; the divergence is said out loud."""
    service = InstrumentMasterService(repository=None, strategies=[rotation])
    rows, _, warnings = service.parse(master_csv)

    hfcl = next(row for row in rows if row["underlying_symbol"] == "HFCL")
    assert hfcl["security_id"] == "21954", "the master is authoritative"
    assert any("HFCL" in warning and "21951" in warning for warning in warnings)


def test_both_strategies_ingest_from_one_pass(rotation, crude, master_csv):
    service = InstrumentMasterService(repository=None, strategies=[crude, rotation])
    rows, _, _ = service.parse(master_csv)

    by_segment = {}
    for row in rows:
        by_segment.setdefault(row["exchange_segment"], []).append(row)

    assert sorted(by_segment) == ["MCX_COMM", "NSE_EQ"]
    assert len(by_segment["NSE_EQ"]) == 3
    assert len(by_segment["MCX_COMM"]) == 2
    assert {row["lot_size"] for row in by_segment["MCX_COMM"]} == {100}
    assert {row["lot_size"] for row in by_segment["NSE_EQ"]} == {1}


def test_two_strategies_may_share_a_symbol_when_they_ingest_it_identically(
    universe_file, master_csv
):
    """Two strategies on the same universe is the point, not a collision.

    The rotation and BTST Overnight both trade the Nifty 500 out of `NSE_EQ`,
    with the same series filter, the same lot-size source and the same
    trading-symbol source -- so the `instruments` row either of them would
    ingest is byte for byte the same row. There is nothing to disambiguate:
    the claim decides how a MASTER ROW IS PARSED, and ownership of a TRADE is
    `strategy_key`, stored on the order and the position at placement.

    Before 2026-09-19 this was refused outright, which was right while one
    strategy traded the Nifty 500 and became wrong the moment two did.
    """
    first = build_definition(_equity_document(universe_file), source="a.yaml")
    second = build_definition(
        _equity_document(universe_file, key="other-rotation"), source="b.yaml"
    )
    service = InstrumentMasterService(repository=None, strategies=[first, second])

    rows, _, warnings = service.parse(master_csv)

    # Ingested ONCE, not twice: a shared claim is one row, not two.
    assert rows, "the shared universe still produced instrument rows"
    security_ids = [row["security_id"] for row in rows]
    assert len(security_ids) == len(set(security_ids))

    # And the shared names are credited to BOTH strategies, not only to
    # whichever was loaded first -- otherwise the second would report its whole
    # universe as unresolved.
    unresolved = " ".join(one for one in warnings if "did not resolve" in one)
    assert "test-rotation" not in unresolved or "1 of 4" in unresolved
    assert not any("4 of 4" in one for one in warnings)


def test_two_strategies_may_not_claim_the_same_symbol_with_DIFFERENT_rules(
    universe_file, master_csv
):
    """Sharing is allowed; disagreeing is not.

    A lot size taken from the master against one taken from config produces a
    genuinely different row, and "whichever strategy was loaded first" is not
    an answer to which one is right.
    """
    first = build_definition(_equity_document(universe_file), source="a.yaml")
    document = _equity_document(universe_file, key="other-rotation")
    document["instrument_sets"][0]["trading_symbol_source"] = "display_name"
    second = build_definition(document, source="b.yaml")
    service = InstrumentMasterService(repository=None, strategies=[first, second])

    with pytest.raises(InstrumentMasterError, match="disagree about how to ingest"):
        service.parse(master_csv)


def test_a_security_id_in_two_segments_is_refused(universe_file, tmp_path):
    """Dhan's ids are unique per segment; this table's are unique globally.

    Ingesting both would silently merge two instruments' prices, because the
    market book is keyed by security id alone.
    """
    path = tmp_path / "colliding.csv"
    path.write_text(
        "\n".join(
            [
                MASTER_HEADER,
                "NSE,E,438,INE257A01026,EQUITY,,BHEL,BHEL,Bharat Heavy,ES,EQ,"
                "1.0,,,,5.0000",
                "NSE,I,438,NA,INDEX,438,HFCL,HFCL,Some Index,INDEX,NA,"
                "1.0,0001-01-01,,XX,5.0000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    document = _equity_document(universe_file)
    document["instrument_sets"].append(
        {
            "instrument_type": "INDEX",
            "exchange_segment": "IDX_I",
            "exchange_segment_code": 0,
            "symbols": ["HFCL"],
            "lot_size_source": "master",
        }
    )
    definition = build_definition(document, source="test.yaml")
    service = InstrumentMasterService(repository=None, strategies=[definition])

    with pytest.raises(InstrumentMasterError, match="unique"):
        service.parse(str(path))


# --- the shipped module -----------------------------------------------------


def test_the_shipped_swing_module_loads_and_is_the_enabled_strategy():
    """On by default since 2026-09-18 -- but enabled is not armed.

    The safety property is not that the module is off; it is that being on
    starts the decision journal and nothing else. Submitting an order needs
    the separate arming switch, which stays false (see
    `test_the_strategy_ships_unarmed` in test_swing_parity.py).
    """
    registry = get_strategy_registry()
    swing = registry.require("nse-swing-momentum")

    assert swing.enabled_by_default is True
    # `automation` became a FRAMEWORK key on 2026-09-18, when the scheduler
    # made "does this module trade unattended" something the Strategies page,
    # the health page and the scheduler all have to know without understanding
    # momentum. It is no longer in `module_config`.
    assert swing.automation.automated is True
    assert swing.automation.armed_by_default is False
    assert registry.is_armed("nse-swing-momentum") is False
    assert len(swing.universe.symbols) == 500
    assert swing.subscription.kind == SubscriptionPolicy.POSITIONS
    assert swing.instrument_sets[0].trusts_master_lot_size()
    assert swing.instrument_sets[0].series == ("EQ", "BE")
    assert swing.tick_size_divisor == 100


def test_the_shipped_swing_module_declares_no_option_capabilities():
    swing = get_strategy_registry().require("nse-swing-momentum")

    assert not swing.supports("option-chain")
    assert not swing.supports("greeks")
    assert not swing.supports("chart-trading")
    assert swing.supports("pnl-reports")


def test_a_disabled_strategy_contributes_no_instruments():
    """Off means off: it claims nothing until an operator switches it on.

    Crude is the module that ships off now, and the property is the same one
    either way -- a disabled strategy contributes no filter to the instrument
    master and no target to the feed.
    """
    registry = get_strategy_registry()
    registry.set_enabled("mcx-crude-options", False)
    enabled = {one.key for one in registry.enabled()}

    assert "mcx-crude-options" not in enabled
    assert "nse-swing-momentum" in enabled
