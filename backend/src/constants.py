"""Protocol-level and domain constants.

The Dhan wire-protocol values here were read out of the official `dhanhq`
Python SDK v2.2.0 (`dhanhq/marketfeed.py`) on 2026-09-16, which is the
authoritative source for the binary feed. The SDK itself is NOT a dependency of
this project -- only these constants were lifted from it.
"""
from enum import Enum

# --- Dhan WebSocket feed: request codes -------------------------------------
# Verified against dhanhq 2.2.0 marketfeed.py. These DIFFER per mode; a single
# code for every mode would be wrong.
FEED_REQUEST_TICKER = 15
FEED_REQUEST_QUOTE = 17
FEED_REQUEST_DEPTH = 19
FEED_REQUEST_FULL = 21
# Unsubscribe is always the subscribe code + 1 (16/18/20/22).
FEED_UNSUBSCRIBE_OFFSET = 1
FEED_REQUEST_DISCONNECT = 12

FEED_MODE_REQUEST_CODES = {
    "TICKER": FEED_REQUEST_TICKER,
    "QUOTE": FEED_REQUEST_QUOTE,
    "DEPTH": FEED_REQUEST_DEPTH,
    "FULL": FEED_REQUEST_FULL,
}

# --- Dhan WebSocket feed: response packet type codes (first byte) -----------
PACKET_TICKER = 2
PACKET_MARKET_DEPTH = 3
PACKET_QUOTE = 4
PACKET_OI = 5
PACKET_PREV_CLOSE = 6
PACKET_STATUS = 7
PACKET_FULL = 8
PACKET_SERVER_DISCONNECT = 50

# Fixed on-the-wire sizes, in bytes, for each packet type.
PACKET_SIZES = {
    PACKET_TICKER: 16,
    PACKET_MARKET_DEPTH: 112,
    PACKET_QUOTE: 50,
    PACKET_OI: 12,
    PACKET_PREV_CLOSE: 16,
    PACKET_STATUS: 8,
    PACKET_FULL: 162,
    PACKET_SERVER_DISCONNECT: 10,
}

# Server-initiated disconnect reason codes.
DISCONNECT_REASONS = {
    805: "Too many active websocket connections (limit is 5 per user)",
    806: "Data APIs not subscribed on this Dhan account",
    807: "Access token expired",
    808: "Authentication failed",
    809: "Access token invalid",
}

# --- Exchange segments ------------------------------------------------------
EXCHANGE_SEGMENT_CODES = {
    0: "IDX_I",
    1: "NSE_EQ",
    2: "NSE_FNO",
    3: "NSE_CURRENCY",
    4: "BSE_EQ",
    5: "MCX_COMM",
    7: "BSE_CURRENCY",
    8: "BSE_FNO",
}
EXCHANGE_SEGMENT_NAMES = {name: code for code, name in EXCHANGE_SEGMENT_CODES.items()}

MCX_COMM = "MCX_COMM"
MCX_COMM_CODE = 5


class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


TERMINAL_ORDER_STATUSES = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    SYNTHETIC = "SYNTHETIC"
    DISABLED = "DISABLED"
    # Running, with NOTHING TO SUBSCRIBE. A distinct state from DISCONNECTED
    # because it is not a failure: no strategy wants an instrument right now,
    # so there is no socket, and there is nothing wrong. Colouring it the same
    # red as a dropped connection would make the indicator cry wolf on the
    # ordinary overnight state -- the same "off is not broken" rule the health
    # tabs follow.
    IDLE = "IDLE"


# --- users -----------------------------------------------------------------
class UserRole(str, Enum):
    """The two roles this application knows about.

    Named to match the Privacera SaaS portal's own role strings, so the
    vocabulary is the same one Akshay already uses elsewhere.
    """

    ACCOUNT_ADMIN = "ROLE_ACCOUNT_ADMIN"
    USER = "ROLE_USER"


class UserStatus(str, Enum):
    """An account can be deactivated instead of deleted.

    INACTIVE is a hard block: such a user cannot log in, and an existing
    session for one stops working on its next request rather than running to
    token expiry.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


USER_ROLES = tuple(role.value for role in UserRole)
USER_STATUSES = tuple(status.value for status in UserStatus)
