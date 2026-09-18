"""Is a market open right now, and may an order execute in it?

Three callers with three different questions, all of which are answered from a
strategy's own `market_hours` block:

* **The stop monitor** must not act outside a session. Between the close and
  the next open the book holds yesterday's last prices, and a stop evaluated
  against one of those would fire on a price nobody can trade at.
* **The feed's inactivity watchdog** must not reconnect because a SUBSCRIBED
  but CLOSED market sent nothing. It reconnects after 40 s of silence, Dhan's
  protocol pings never reach the message loop, and MCX closes at 23:30 while
  NSE closes at 15:30 -- so a book held across the close reconnected every 45
  seconds until the next open, burning one of Dhan's five connection slots and
  drowning the real dead-feed signal in noise.
* **The Closing Auction Session** decides whether an order sent now could
  execute continuously. For an F&O-eligible NSE name after 15:15 it could not;
  it would land in an auction this simulator has no model of.

Everything here is a pure function of a definition and a moment. Nothing is
cached, nothing is measured, and none of it is on the tick path -- the watchdog
runs every 5 s and the stop monitor on its own cadence.

There is deliberately NO HOLIDAY LIST. A weekday the exchange did not trade
looks open to these functions, and that is the right trade-off: the trading
CALENDAR is the regime index's own bar dates (`daily_bars`), which is what
decides whether a session exists at all. This module answers the narrower
question of whether the clock is inside the session window, and being wrong on
a holiday costs one idle poll rather than a wrong decision -- a holiday has no
bar, so nothing is decided and nothing is ranked.
"""
from datetime import datetime, time
from typing import List, Optional

from src.core.time_utils import ist_now
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import (
    ClosingAuction,
    StrategyDefinition,
)

logger = get_logger("strategies.clock")


def is_trading_day(definition: StrategyDefinition, now: Optional[datetime] = None) -> bool:
    now = now or ist_now()
    return now.weekday() in {int(day) for day in definition.market_hours.trading_days}


def is_market_open(
    definition: StrategyDefinition, now: Optional[datetime] = None
) -> bool:
    """Inside the strategy's own session window, on a trading weekday.

    The AUCTION counts as open: the exchange is still running a session and the
    feed is still publishing. Whether an ORDER could execute is the separate
    question `can_execute_continuously` answers.
    """
    now = now or ist_now()
    if not is_trading_day(definition, now):
        return False
    hours = definition.market_hours
    close = hours.close
    auction = hours.closing_auction
    if auction is not None and auction.auction_close > close:
        close = auction.auction_close
    return hours.open <= now.time() <= close


def any_market_open(
    definitions: List[StrategyDefinition], now: Optional[datetime] = None
) -> bool:
    """Is ANY of these strategies' markets open?

    What the feed watchdog asks. One connection carries every enabled
    strategy's instruments, so silence is only evidence of a dead socket while
    at least one of those markets is trading.
    """
    now = now or ist_now()
    return any(is_market_open(definition, now) for definition in definitions)


def continuous_close(
    definition: StrategyDefinition, fno_eligible: bool = False
) -> time:
    """When continuous trading ends for one instrument.

    15:15 for an F&O-eligible NSE name since the Closing Auction Session went
    live on 3 August 2026; the ordinary close for everything else.
    """
    hours = definition.market_hours
    auction = hours.closing_auction
    if auction is None:
        return hours.close
    if auction.applies_to == ClosingAuction.APPLIES_FNO_ELIGIBLE and not fno_eligible:
        return hours.close
    return auction.continuous_close


def can_execute_continuously(
    definition: StrategyDefinition,
    fno_eligible: bool = False,
    now: Optional[datetime] = None,
) -> bool:
    """Could an order sent NOW fill in continuous trading?

    False outside the session, and false for an F&O-eligible name inside the
    closing auction. A caller that gets False must record why rather than
    placing an order this simulator cannot honestly fill.
    """
    now = now or ist_now()
    if not is_trading_day(definition, now):
        return False
    hours = definition.market_hours
    return hours.open <= now.time() <= continuous_close(definition, fno_eligible)


def in_closing_auction(
    definition: StrategyDefinition,
    fno_eligible: bool = False,
    now: Optional[datetime] = None,
) -> bool:
    """Specifically inside the call auction, rather than merely outside hours.

    The two are different answers and the stop monitor records different
    things for them: "the market is shut, this waits for the open" against
    "continuous trading for this name ended at 15:15 and the exit would land in
    an auction we do not model".
    """
    now = now or ist_now()
    auction = definition.market_hours.closing_auction
    if auction is None or not is_trading_day(definition, now):
        return False
    if auction.applies_to == ClosingAuction.APPLIES_FNO_ELIGIBLE and not fno_eligible:
        return False
    return auction.continuous_close < now.time() <= auction.auction_close


def enabled_market_open(now: Optional[datetime] = None) -> bool:
    """`any_market_open` over the currently ENABLED strategies.

    What the feed watchdog calls. The registry read is synchronous and cached,
    which is what lets a 5-second watchdog ask it without a database round
    trip.
    """
    from src.strategies.services.strategy_registry import get_strategy_registry

    return any_market_open(get_strategy_registry().enabled(), now=now)
