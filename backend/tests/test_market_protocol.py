"""Dhan binary feed protocol.

These encode the wire format from the far side: each test builds a packet with
struct.pack exactly as the exchange would, then asserts the parser recovers it.
"""
import struct

import pytest

from src.constants import (
    FEED_MODE_REQUEST_CODES,
    FEED_REQUEST_FULL,
    FEED_REQUEST_TICKER,
    FEED_UNSUBSCRIBE_OFFSET,
    PACKET_FULL,
    PACKET_OI,
    PACKET_QUOTE,
    PACKET_SERVER_DISCONNECT,
    PACKET_TICKER,
)
from src.market.services.feed_protocol import (
    build_subscribe_message,
    chunk_instruments,
    parse_frame,
)

_FULL = struct.Struct("<BHBIfHIfIIIIIIffff100s")
_TICKER = struct.Struct("<BHBIfI")
_QUOTE = struct.Struct("<BHBIfHIfIIIffff")
_OI = struct.Struct("<BHBII")
_DISCONNECT = struct.Struct("<BHBIH")
_LEVEL = struct.Struct("<IIHHff")


def _depth_blob(base_bid=6799.0, base_ask=6801.0):
    return b"".join(
        _LEVEL.pack(
            100 + level,            # bid qty
            200 + level,            # ask qty
            3 + level,              # bid orders
            4 + level,              # ask orders
            base_bid - level,       # bid price
            base_ask + level,       # ask price
        )
        for level in range(5)
    )


def _full_packet(security_id=565899, ltp=6800.0):
    return _FULL.pack(
        PACKET_FULL, 162, 5, security_id,
        ltp, 7, 1789557000, 6799.5,
        123456, 500, 600,
        7000, 7100, 6900,
        6790.0, 6785.0, 6820.0, 6780.0,
        _depth_blob(),
    )


def test_full_packet_round_trips():
    packets = parse_frame(_full_packet())

    assert len(packets) == 1
    packet_type, fields = packets[0]
    assert packet_type == PACKET_FULL
    assert fields["security_id"] == 565899
    assert fields["segment"] == 5
    assert fields["ltp"] == pytest.approx(6800.0)
    assert fields["ltq"] == 7
    assert fields["volume"] == 123456
    assert fields["oi"] == 7000
    assert fields["oi_day_high"] == 7100
    assert fields["oi_day_low"] == 6900


def test_full_packet_depth_has_five_levels():
    _, fields = parse_frame(_full_packet())[0]
    depth = fields["depth"]

    assert len(depth) == 5
    bid_qty, ask_qty, bid_orders, ask_orders, bid_price, ask_price = depth[0]
    assert (bid_qty, ask_qty, bid_orders, ask_orders) == (100, 200, 3, 4)
    assert bid_price == pytest.approx(6799.0)
    assert ask_price == pytest.approx(6801.0)
    # Levels walk away from the touch in the expected direction.
    assert depth[4][4] == pytest.approx(6795.0)
    assert depth[4][5] == pytest.approx(6805.0)


def test_multiple_packets_in_one_frame_are_all_parsed():
    """Dhan concatenates packets into a single WebSocket frame.

    The official SDK reads only the first packet of a frame and drops the rest,
    which would show up here as a book that lags under load.
    """
    frame = b"".join(_full_packet(security_id=sid, ltp=6800.0 + sid % 10) for sid in range(10))

    packets = parse_frame(frame)

    assert len(packets) == 10
    assert [fields["security_id"] for _type, fields in packets] == list(range(10))


def test_mixed_packet_types_in_one_frame():
    frame = (
        _TICKER.pack(PACKET_TICKER, 16, 5, 111, 6800.0, 1789557000)
        + _full_packet(security_id=222)
        + _OI.pack(PACKET_OI, 12, 5, 333, 4242)
    )

    packets = parse_frame(frame)

    assert [packet_type for packet_type, _ in packets] == [
        PACKET_TICKER, PACKET_FULL, PACKET_OI,
    ]
    assert packets[0][1]["ltp"] == pytest.approx(6800.0)
    assert packets[2][1]["oi"] == 4242


def test_quote_packet_round_trips():
    packet = _QUOTE.pack(
        PACKET_QUOTE, 50, 5, 444,
        6801.0, 3, 1789557000, 6800.0,
        999, 100, 200,
        6790.0, 6785.0, 6820.0, 6780.0,
    )

    packet_type, fields = parse_frame(packet)[0]

    assert packet_type == PACKET_QUOTE
    assert fields["security_id"] == 444
    assert fields["volume"] == 999
    assert fields["total_sell_qty"] == 100
    assert fields["total_buy_qty"] == 200


def test_server_disconnect_is_surfaced_with_its_reason():
    packet = _DISCONNECT.pack(PACKET_SERVER_DISCONNECT, 10, 5, 0, 805)

    packet_type, fields = parse_frame(packet)[0]

    assert packet_type == PACKET_SERVER_DISCONNECT
    assert fields["disconnect_code"] == 805
    assert "connection" in fields["reason"].lower()


def test_truncated_trailing_packet_does_not_lose_earlier_ones():
    """A malformed tail must not cost us the packets already decoded."""
    frame = _full_packet(security_id=1) + _full_packet(security_id=2)[:100]

    packets = parse_frame(frame)

    assert len(packets) == 1
    assert packets[0][1]["security_id"] == 1


def test_empty_and_short_frames_are_safe():
    assert parse_frame(b"") == []
    assert parse_frame(b"\x08\x00") == []


def test_request_codes_differ_per_mode():
    """A single code for every mode would subscribe options in Ticker mode and
    silently deliver no depth or OI."""
    assert FEED_MODE_REQUEST_CODES["TICKER"] == 15
    assert FEED_MODE_REQUEST_CODES["QUOTE"] == 17
    assert FEED_MODE_REQUEST_CODES["DEPTH"] == 19
    assert FEED_MODE_REQUEST_CODES["FULL"] == 21
    assert FEED_REQUEST_FULL != FEED_REQUEST_TICKER


def test_unsubscribe_code_is_subscribe_plus_one():
    assert FEED_REQUEST_FULL + FEED_UNSUBSCRIBE_OFFSET == 22
    assert FEED_REQUEST_TICKER + FEED_UNSUBSCRIBE_OFFSET == 16


def test_subscribe_message_shape():
    message = build_subscribe_message(
        FEED_REQUEST_FULL, [("MCX_COMM", "576266"), ("MCX_COMM", "576267")]
    )

    assert message == {
        "RequestCode": 21,
        "InstrumentCount": 2,
        "InstrumentList": [
            {"ExchangeSegment": "MCX_COMM", "SecurityId": "576266"},
            {"ExchangeSegment": "MCX_COMM", "SecurityId": "576267"},
        ],
    }


def test_security_ids_are_serialised_as_strings():
    """Dhan expects SecurityId as a string even though it is numeric."""
    message = build_subscribe_message(FEED_REQUEST_FULL, [("MCX_COMM", 576266)])
    assert message["InstrumentList"][0]["SecurityId"] == "576266"


def test_chunking_respects_the_100_instrument_message_limit():
    instruments = [("MCX_COMM", str(i)) for i in range(250)]

    batches = chunk_instruments(instruments, 100)

    assert [len(batch) for batch in batches] == [100, 100, 50]
    assert sum(len(batch) for batch in batches) == 250


def test_chunking_rejects_a_nonsense_size():
    with pytest.raises(ValueError):
        chunk_instruments([1, 2, 3], 0)
