"""Dhan binary market-feed wire protocol.

This is the tick hot path. Everything here works in plain tuples, dicts and
precompiled ``struct.Struct`` objects: no Pydantic validation, no ORM objects,
no attribute lookups that can be hoisted out.

Layouts were read from the official ``dhanhq`` Python SDK v2.2.0
(``dhanhq/marketfeed.py``) on 2026-09-16, which is the authoritative
description of the wire format. The SDK is not a dependency -- only the layout
knowledge was taken from it.

Every response packet starts with the same 8-byte header:

    uint8   feed response code   (which packet type follows)
    uint16  message length
    uint8   exchange segment     (5 = MCX_COMM)
    uint32  security id

UNVERIFIED: the SDK maps the four price fields of the Quote and Full packets in
the order open, close, high, low -- not the conventional OHLC. That ordering is
reproduced here because the SDK is the best available source, but it has not
been checked against a live feed (no market-data credentials were available at
build time). If highs and lows look transposed against the chart, this is the
first place to look: see FULL_IDX_OPEN/CLOSE/HIGH/LOW below.
"""
import struct
from typing import Any, Dict, List, Optional, Tuple

from src.constants import (
    DISCONNECT_REASONS,
    PACKET_FULL,
    PACKET_MARKET_DEPTH,
    PACKET_OI,
    PACKET_PREV_CLOSE,
    PACKET_QUOTE,
    PACKET_SERVER_DISCONNECT,
    PACKET_SIZES,
    PACKET_STATUS,
    PACKET_TICKER,
)

# --- precompiled structs ---------------------------------------------------
_HEADER = struct.Struct("<BHBI")
_TICKER = struct.Struct("<BHBIfI")
_PREV_CLOSE = struct.Struct("<BHBIfI")
_QUOTE = struct.Struct("<BHBIfHIfIIIffff")
_OI = struct.Struct("<BHBII")
_FULL = struct.Struct("<BHBIfHIfIIIIIIffff100s")
_MARKET_DEPTH = struct.Struct("<BHBIf100s")
_DEPTH_LEVEL = struct.Struct("<IIHHff")
_DISCONNECT = struct.Struct("<BHBIH")

_DEPTH_LEVEL_SIZE = _DEPTH_LEVEL.size          # 20 bytes
_DEPTH_LEVELS = 5

# Field indices into the Full packet tuple.
FULL_IDX_LTP = 4
FULL_IDX_LTQ = 5
FULL_IDX_LTT = 6
FULL_IDX_AVG = 7
FULL_IDX_VOLUME = 8
FULL_IDX_TOTAL_SELL = 9
FULL_IDX_TOTAL_BUY = 10
FULL_IDX_OI = 11
FULL_IDX_OI_HIGH = 12
FULL_IDX_OI_LOW = 13
FULL_IDX_OPEN = 14
FULL_IDX_CLOSE = 15
FULL_IDX_HIGH = 16
FULL_IDX_LOW = 17
FULL_IDX_DEPTH = 18

_unpack_header = _HEADER.unpack_from
_unpack_depth_level = _DEPTH_LEVEL.unpack_from


def parse_depth(blob: bytes) -> List[Tuple[int, int, int, int, float, float]]:
    """Five levels of market depth as plain tuples.

    Each tuple is (bid_qty, ask_qty, bid_orders, ask_orders, bid_price,
    ask_price). Tuples rather than dicts: the fill simulator walks these on
    every order and the browser payload serialises them positionally.
    """
    levels = []
    offset = 0
    for _ in range(_DEPTH_LEVELS):
        levels.append(_unpack_depth_level(blob, offset))
        offset += _DEPTH_LEVEL_SIZE
    return levels


def parse_ticker(data: bytes, offset: int = 0) -> Dict[str, Any]:
    fields = _TICKER.unpack_from(data, offset)
    return {
        "security_id": fields[3],
        "segment": fields[2],
        "ltp": fields[4],
        "ltt": fields[5],
    }


def parse_prev_close(data: bytes, offset: int = 0) -> Dict[str, Any]:
    fields = _PREV_CLOSE.unpack_from(data, offset)
    return {
        "security_id": fields[3],
        "segment": fields[2],
        "prev_close": fields[4],
        "prev_oi": fields[5],
    }


def parse_quote(data: bytes, offset: int = 0) -> Dict[str, Any]:
    fields = _QUOTE.unpack_from(data, offset)
    return {
        "security_id": fields[3],
        "segment": fields[2],
        "ltp": fields[4],
        "ltq": fields[5],
        "ltt": fields[6],
        "avg_price": fields[7],
        "volume": fields[8],
        "total_sell_qty": fields[9],
        "total_buy_qty": fields[10],
        "open": fields[11],
        "close": fields[12],
        "high": fields[13],
        "low": fields[14],
    }


def parse_oi(data: bytes, offset: int = 0) -> Dict[str, Any]:
    fields = _OI.unpack_from(data, offset)
    return {"security_id": fields[3], "segment": fields[2], "oi": fields[4]}


def parse_full(data: bytes, offset: int = 0) -> Dict[str, Any]:
    fields = _FULL.unpack_from(data, offset)
    return {
        "security_id": fields[3],
        "segment": fields[2],
        "ltp": fields[FULL_IDX_LTP],
        "ltq": fields[FULL_IDX_LTQ],
        "ltt": fields[FULL_IDX_LTT],
        "avg_price": fields[FULL_IDX_AVG],
        "volume": fields[FULL_IDX_VOLUME],
        "total_sell_qty": fields[FULL_IDX_TOTAL_SELL],
        "total_buy_qty": fields[FULL_IDX_TOTAL_BUY],
        "oi": fields[FULL_IDX_OI],
        "oi_day_high": fields[FULL_IDX_OI_HIGH],
        "oi_day_low": fields[FULL_IDX_OI_LOW],
        "open": fields[FULL_IDX_OPEN],
        "close": fields[FULL_IDX_CLOSE],
        "high": fields[FULL_IDX_HIGH],
        "low": fields[FULL_IDX_LOW],
        "depth": parse_depth(fields[FULL_IDX_DEPTH]),
    }


def parse_market_depth(data: bytes, offset: int = 0) -> Dict[str, Any]:
    fields = _MARKET_DEPTH.unpack_from(data, offset)
    return {
        "security_id": fields[3],
        "segment": fields[2],
        "ltp": fields[4],
        "depth": parse_depth(fields[5]),
    }


def parse_disconnect(data: bytes, offset: int = 0) -> Dict[str, Any]:
    fields = _DISCONNECT.unpack_from(data, offset)
    code = fields[4]
    return {
        "security_id": fields[3],
        "segment": fields[2],
        "disconnect_code": code,
        "reason": DISCONNECT_REASONS.get(code, f"Server disconnect (code {code})"),
    }


_PARSERS = {
    PACKET_TICKER: parse_ticker,
    PACKET_QUOTE: parse_quote,
    PACKET_OI: parse_oi,
    PACKET_PREV_CLOSE: parse_prev_close,
    PACKET_FULL: parse_full,
    PACKET_MARKET_DEPTH: parse_market_depth,
    PACKET_SERVER_DISCONNECT: parse_disconnect,
}


def parse_frame(data: bytes) -> List[Tuple[int, Dict[str, Any]]]:
    """Parse every packet in one WebSocket binary frame.

    Dhan can concatenate several packets into a single frame -- the official
    SDK only ever reads the first one and silently drops the rest, which would
    show up here as a book that lags under load. The frame is walked using the
    fixed per-type packet sizes, falling back to the header's own declared
    length for a packet type we do not know.

    Returns a list of (packet_type, fields) pairs. Unparseable trailing bytes
    end the walk rather than raising: a malformed tail must not cost us the
    packets already decoded.
    """
    results: List[Tuple[int, Dict[str, Any]]] = []
    total = len(data)
    offset = 0

    while offset + _HEADER.size <= total:
        packet_type, declared_length, _segment, _security_id = _unpack_header(data, offset)

        size = PACKET_SIZES.get(packet_type)
        if size is None:
            # Unknown packet type: trust the declared length if it is sane,
            # otherwise stop rather than risk desynchronising the walk.
            if declared_length <= 0 or offset + declared_length > total:
                break
            offset += declared_length
            continue

        if offset + size > total:
            break

        parser = _PARSERS.get(packet_type)
        if parser is not None:
            try:
                results.append((packet_type, parser(data, offset)))
            except struct.error:
                break
        # PACKET_STATUS carries no payload worth surfacing; it is skipped.

        offset += size

    return results


def build_subscribe_message(
    request_code: int, instruments: List[Tuple[str, str]]
) -> Dict[str, Any]:
    """Build one subscribe/unsubscribe JSON message.

    `instruments` is a list of (exchange_segment, security_id) pairs. Dhan caps
    a single message at 100 instruments; batching is the caller's job (see
    chunk_instruments).
    """
    return {
        "RequestCode": request_code,
        "InstrumentCount": len(instruments),
        "InstrumentList": [
            {"ExchangeSegment": segment, "SecurityId": str(security_id)}
            for segment, security_id in instruments
        ],
    }


def chunk_instruments(instruments: List[Any], size: int = 100) -> List[List[Any]]:
    """Split into Dhan's per-message limit of 100 instruments."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [instruments[i : i + size] for i in range(0, len(instruments), size)]
