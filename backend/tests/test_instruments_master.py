"""Instrument master parsing.

These lock in the two MCX quirks that would otherwise corrupt every downstream
number: the unusable LOT_SIZE and the paise-denominated TICK_SIZE.
"""
from datetime import date
from decimal import Decimal

import pytest

from src.instruments.services.instrument_master_service import (
    InstrumentMasterService,
    _parse_date,
    _parse_decimal,
)


@pytest.fixture
def service():
    return InstrumentMasterService(repository=None)


def test_parse_selects_only_the_configured_underlying(service, sample_master_csv):
    rows, scanned, _ = service.parse(sample_master_csv)

    assert scanned == 11, "every data row should be scanned"
    assert len(rows) == 9, "GOLD and NIFTY rows must be filtered out"
    assert {row["underlying_symbol"] for row in rows} == {"CRUDEOIL"}


def test_lot_size_comes_from_config_not_the_master(service, sample_master_csv):
    """The master reports LOT_SIZE=1.0 for every MCX row; 1 barrel is wrong."""
    rows, _, warnings = service.parse(sample_master_csv)

    assert {row["lot_size"] for row in rows} == {100}
    assert any("LOT_SIZE<=1" in warning for warning in warnings), (
        "substituting the lot size must be reported, not done silently"
    )


def test_tick_size_is_converted_from_paise_to_rupees(service, sample_master_csv):
    rows, _, _ = service.parse(sample_master_csv)
    by_id = {row["security_id"]: row for row in rows}

    # Futures: master says 100.0 paise -> Rs 1.00
    assert by_id["565899"]["tick_size"] == Decimal("1")
    # Options: master says 10.0 paise -> Rs 0.10
    assert by_id["576266"]["tick_size"] == Decimal("0.1")


def test_futures_and_options_are_distinguished(service, sample_master_csv):
    rows, _, _ = service.parse(sample_master_csv)
    by_id = {row["security_id"]: row for row in rows}

    future = by_id["565899"]
    assert future["instrument_type"] == "FUTCOM"
    assert future["option_type"] is None
    assert future["strike_price"] is None, "OPTION_TYPE=XX rows carry a placeholder strike"
    assert future["expiry_date"] == date(2026, 9, 21)

    call = by_id["576266"]
    assert call["instrument_type"] == "OPTFUT"
    assert call["option_type"] == "CE"
    assert call["strike_price"] == Decimal("7000")
    assert call["expiry_date"] == date(2026, 9, 17)


def test_trading_symbol_uses_the_descriptive_name(service, sample_master_csv):
    """SYMBOL_NAME is just 'CRUDEOIL'; DISPLAY_NAME identifies the contract."""
    rows, _, _ = service.parse(sample_master_csv)
    by_id = {row["security_id"]: row for row in rows}

    assert by_id["576266"]["trading_symbol"] == "CRUDEOIL 17 SEP 7000 CALL"
    assert by_id["576267"]["trading_symbol"] == "CRUDEOIL 17 SEP 7000 PUT"


def test_option_and_futures_expiries_differ(service, sample_master_csv):
    """The whole point of tracking both: they do not roll on the same date."""
    rows, _, _ = service.parse(sample_master_csv)

    option_expiries = {r["expiry_date"] for r in rows if r["instrument_type"] == "OPTFUT"}
    futures_expiries = {r["expiry_date"] for r in rows if r["instrument_type"] == "FUTCOM"}

    assert date(2026, 9, 17) in option_expiries
    assert date(2026, 9, 21) in futures_expiries
    assert not (option_expiries & futures_expiries)


def test_missing_columns_are_rejected_loudly(service, tmp_path):
    """A silent schema change upstream must fail, not produce empty results."""
    path = tmp_path / "broken.csv"
    path.write_text("EXCH_ID,SECURITY_ID\nMCX,1\n", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        service.parse(str(path))
    assert "missing expected columns" in str(exc_info.value)


def test_no_matching_contracts_is_an_error_not_an_empty_success(service, tmp_path):
    header = (
        "EXCH_ID,SEGMENT,SECURITY_ID,ISIN,INSTRUMENT,UNDERLYING_SECURITY_ID,"
        "UNDERLYING_SYMBOL,SYMBOL_NAME,DISPLAY_NAME,INSTRUMENT_TYPE,SERIES,"
        "LOT_SIZE,SM_EXPIRY_DATE,STRIKE_PRICE,OPTION_TYPE,TICK_SIZE"
    )
    row = (
        "NSE,D,111111,NA,OPTIDX,13,NIFTY,NIFTY,NIFTY CALL,OPTIDX,NA,"
        "65.0,2026-09-25,24000.00000,CE,5.0000"
    )
    path = tmp_path / "nomatch.csv"
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        service.parse(str(path))
    assert "No CRUDEOIL contracts" in str(exc_info.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-17", date(2026, 9, 17)),
        ("2026-09-17 00:00:00", date(2026, 9, 17)),
        ("NA", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date_handles_master_conventions(value, expected):
    assert _parse_date(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("7000.00000", Decimal("7000.00000")),
        ("-0.01000", Decimal("-0.01000")),
        ("XX", None),
        ("", None),
    ],
)
def test_parse_decimal_handles_master_conventions(value, expected):
    assert _parse_decimal(value) == expected
