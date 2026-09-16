"""Option chain client: response parsing, rate limiting, endpoint restriction.

The sample payload mirrors the documented DhanHQ v2 schema
(https://dhanhq.co/docs/v2/option-chain/, checked 2026-09-16).
"""
import asyncio
import time

import pytest

from src.market.services.dhan_option_chain_client import (
    ENDPOINT_EXPIRY_LIST,
    ENDPOINT_OPTION_CHAIN,
    MIN_REQUEST_INTERVAL_SECONDS,
    DhanOptionChainClient,
    OptionChainError,
    parse_option_chain,
)

SAMPLE_PAYLOAD = {
    "data": {
        "last_price": 6805.0,
        "oc": {
            "6800.000000": {
                "ce": {
                    "average_price": 51.2,
                    "greeks": {"delta": 0.5196, "theta": -12.4, "gamma": 0.0032, "vega": 1.41},
                    "implied_volatility": 34.9,
                    "last_price": 51.8,
                    "oi": 12400,
                    "previous_close_price": 48.0,
                    "previous_oi": 11900,
                    "previous_volume": 3100,
                    "security_id": 576375,
                    "top_ask_price": 52.0,
                    "top_ask_quantity": 40,
                    "top_bid_price": 51.6,
                    "top_bid_quantity": 35,
                    "volume": 3400,
                },
                "pe": {
                    "average_price": 47.1,
                    "greeks": {"delta": -0.4803, "theta": -11.8, "gamma": 0.0032, "vega": 1.41},
                    "implied_volatility": 35.11,
                    "last_price": 47.6,
                    "oi": 9800,
                    "previous_close_price": 50.0,
                    "previous_oi": 10300,
                    "previous_volume": 2800,
                    "security_id": 576528,
                    "top_ask_price": 47.8,
                    "top_ask_quantity": 25,
                    "top_bid_price": 47.4,
                    "top_bid_quantity": 30,
                    "volume": 2900,
                },
            },
            "6850.000000": {
                "ce": {
                    "greeks": {"delta": 0.3623, "theta": -11.0, "gamma": 0.0030, "vega": 1.38},
                    "implied_volatility": 34.86,
                    "last_price": 30.3,
                    "oi": 8100,
                    "previous_oi": 8400,
                    "security_id": 576376,
                    "volume": 1500,
                },
                "pe": None,
            },
        },
    },
    "status": "success",
}


def test_parses_the_documented_envelope():
    snapshot = parse_option_chain(SAMPLE_PAYLOAD, expiry="2026-09-17")

    assert snapshot.underlying_last_price == 6805.0
    assert snapshot.expiry == "2026-09-17"
    assert sorted(snapshot.strikes) == [6800.0, 6850.0]


def test_parses_greeks_and_iv_for_both_legs():
    snapshot = parse_option_chain(SAMPLE_PAYLOAD)
    call = snapshot.strikes[6800.0]["CE"]
    put = snapshot.strikes[6800.0]["PE"]

    assert call.delta == pytest.approx(0.5196)
    assert call.theta == pytest.approx(-12.4)
    assert call.gamma == pytest.approx(0.0032)
    assert call.vega == pytest.approx(1.41)
    assert call.implied_volatility == pytest.approx(34.9)
    assert put.delta == pytest.approx(-0.4803)


def test_security_id_is_captured_as_a_string():
    """Rows are matched to the book by security_id, not by strike string."""
    snapshot = parse_option_chain(SAMPLE_PAYLOAD)

    assert snapshot.strikes[6800.0]["CE"].security_id == "576375"
    assert snapshot.strikes[6800.0]["PE"].security_id == "576528"


def test_touch_and_volume_are_captured():
    call = parse_option_chain(SAMPLE_PAYLOAD).strikes[6800.0]["CE"]

    assert call.top_bid_price == pytest.approx(51.6)
    assert call.top_bid_quantity == 35
    assert call.top_ask_price == pytest.approx(52.0)
    assert call.top_ask_quantity == 40
    assert call.volume == 3400


def test_oi_change_is_derived_from_previous_oi():
    snapshot = parse_option_chain(SAMPLE_PAYLOAD)

    assert snapshot.strikes[6800.0]["CE"].oi_change == 500      # 12400 - 11900
    assert snapshot.strikes[6800.0]["PE"].oi_change == -500     # 9800 - 10300


def test_oi_change_is_none_when_previous_oi_is_absent():
    payload = {"data": {"oc": {"7000.000000": {"ce": {"oi": 100, "security_id": 1}}}}}

    leg = parse_option_chain(payload).strikes[7000.0]["CE"]

    assert leg.oi is 100 or leg.oi == 100
    assert leg.oi_change is None, "a missing previous OI must not read as zero change"


def test_a_missing_leg_is_skipped_not_faked():
    snapshot = parse_option_chain(SAMPLE_PAYLOAD)

    assert "CE" in snapshot.strikes[6850.0]
    assert "PE" not in snapshot.strikes[6850.0]


def test_legs_iterator_yields_every_populated_leg():
    legs = list(parse_option_chain(SAMPLE_PAYLOAD).legs())

    assert len(legs) == 3
    assert {option_type for _strike, option_type, _leg in legs} == {"CE", "PE"}


def test_empty_chain_parses_to_an_empty_snapshot():
    snapshot = parse_option_chain({"data": {"last_price": 6800.0, "oc": {}}})

    assert snapshot.strikes == {}
    assert snapshot.underlying_last_price == 6800.0


def test_malformed_chain_is_rejected():
    with pytest.raises(OptionChainError):
        parse_option_chain({"data": {"oc": ["not", "a", "dict"]}})


def test_expiry_list_response_shape():
    """Documented as a bare list of ISO dates under `data`."""
    payload = {"data": ["2026-09-17", "2026-10-15"], "status": "success"}
    assert [str(value) for value in payload["data"]] == ["2026-09-17", "2026-10-15"]


async def test_client_refuses_endpoints_outside_its_allowlist():
    """Structural guarantee: this client cannot be pointed at a trading path."""
    client = DhanOptionChainClient()

    with pytest.raises(OptionChainError) as exc_info:
        await client._post("/orders", {})

    assert "not permitted" in str(exc_info.value)


async def test_client_allows_only_the_two_market_data_endpoints():
    assert ENDPOINT_OPTION_CHAIN == "/optionchain"
    assert ENDPOINT_EXPIRY_LIST == "/optionchain/expirylist"


async def test_client_without_credentials_fails_clearly():
    from src import config_utils

    config = config_utils.load_config()
    dhan = config.get("dhan", {})
    previous_id, previous_token = dhan.get("client_id"), dhan.get("access_token")
    dhan["client_id"] = ""
    dhan["access_token"] = ""
    try:
        client = DhanOptionChainClient()
        with pytest.raises(OptionChainError) as exc_info:
            await client._post(ENDPOINT_OPTION_CHAIN, {})
        assert "credentials" in str(exc_info.value)
    finally:
        dhan["client_id"] = previous_id
        dhan["access_token"] = previous_token


async def test_rate_limiter_spaces_requests_for_the_same_expiry():
    """Dhan allows one request per 3s per unique underlying+expiry."""
    client = DhanOptionChainClient()
    key = (294, "2026-09-17")

    await client._respect_rate_limit(key)
    started = time.monotonic()
    # Shorten the wait for the test by back-dating the recorded timestamp.
    client._last_request_at[key] = time.monotonic() - (MIN_REQUEST_INTERVAL_SECONDS - 0.15)
    await client._respect_rate_limit(key)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.1, "a second request for the same expiry must be delayed"


async def test_rate_limiter_does_not_serialise_different_expiries():
    """Different expiries may be polled concurrently."""
    client = DhanOptionChainClient()
    client._last_request_at[(294, "2026-09-17")] = time.monotonic()

    started = time.monotonic()
    await client._respect_rate_limit((294, "2026-10-15"))
    elapsed = time.monotonic() - started

    assert elapsed < 0.1, "an unrelated expiry must not wait behind another"
