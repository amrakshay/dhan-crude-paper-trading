"""What the strategy sees at 15:20, and what the rule says about it.

This module answers one question -- *given the daily bars stored and the live
book as it stands right now, which names qualify?* -- and it does not trade.
Everything it returns is a plain value object, so the same computation feeds
the Signals tab, the journal and the order placement without any of them
re-deriving it.

**THE ONE THING THAT MAKES THIS DIFFERENT FROM `ranking_service.py`.** The
rotation decides on finished daily bars: every input is a completed number and
the computation could be run at any hour and get the same answer. This decides
on the SESSION SO FAR. Three of its seven filters read the live book --

    B4  price       > hi55          the price is the last trade
    B5  vol_sofar  >= 2 x advq      cumulative volume, today, so far
    B6  CLV         > 0.8           against today's running high and low

-- and none of those numbers exists an hour later. That is why this module
takes quotes as an argument rather than fetching bars for itself, and why the
journal stores every one of them.

**Nothing here accumulates ticks.** Root `CLAUDE.md` forbids building bars out
of the feed, and this strategy does not need to: Dhan's Quote/Full packet
already carries the session's running high, low and cumulative volume per
instrument. These are READS FROM `MarketBook`, not an accumulator. If you find
yourself keeping a running maximum in this package, stop.

**Two entry points, and the difference between them is the specification's own
section 3 NOTE.**

`scan_live()` is the real thing. It substitutes the current price for today's
unfinished close in B4, B6 and B7 -- which is what section 3's NOTE prescribes
and what the section 10.3 validation measured at 83.4% precision -- and it
computes `advq` and `adv20` from COMPLETED bars only, because today's volume is
not a completed number. Section 3's pseudocode says exactly that:
`advq = mean(volume[t-20 .. t-1])`.

`scan_on_bars()` is the reconstruction, and it exists for one reason: section
13 lists twelve dated signals with their symbols, closes, volume ratios, CLVs
and realised gaps, and says *"the same code on the same data must produce
these"*. That table was produced by `scan.py::build`, which works on the daily
panel -- where `advq` and `sma200` are `rolling(...)` windows that INCLUDE day
T. Reproducing it therefore needs the panel's convention, not the live one, and
pretending otherwise would make the acceptance test a test of the wrong thing.
`tests/test_btst_specification_signals.py` is the caller.

**The two conventions differ, and they roughly cancel.** Including today's
volume in a 20-day mean raises the denominator on exactly the high-volume days
this rule fires on -- about 5% at a 2x day -- while measuring `vol_sofar` at
15:20 gives roughly 95% of the day. The live test is therefore very close to
the backtest's and, where it differs, slightly stricter. Section 14 item 1 is
explicit that the ratio must NOT be scaled up to a projected full day, and it
is not.
"""
import dataclasses
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from src.btst.services.btst_parameters import (
    BtstParameters,
    RANK_MOM126,
    RANK_VOL_RATIO,
)
from src.btst.services.btst_policy import BtstPolicy
from src.logging_config import get_logger
from src.swing.services import indicators

logger = get_logger("btst.scan")


class ScanError(Exception):
    pass


# Why a symbol did not qualify. Recorded per symbol so the journal and the
# Signals tab can answer "why was X not bought" without recomputing anything
# from a book that has since moved on.
SKIP_TOO_LITTLE_HISTORY = "too little history"
SKIP_NO_QUOTE = "no live price"
SKIP_BAD_QUOTE = "the live book's session range is not usable"
SKIP_FNO = "has listed derivatives"
SKIP_ILLIQUID = "below the liquidity floor"
SKIP_PRICE_FLOOR = "below the price floor"
SKIP_NO_BREAKOUT = "not above its 55-day high"
SKIP_VOLUME = "below the volume multiple"
SKIP_CLOSE_WEAK = "not closing in the top of its range"
SKIP_BELOW_SMA = "below its own SMA200"
SKIP_MOMENTUM = "below the momentum floor"
SKIP_NO_MOMENTUM = "momentum undefined"

# The funnel, in the order the specification's section 3 applies it. The Signals
# tab renders this list in this order, so the two cannot drift.
FILTER_STAGES = (
    ("universe", "In the universe"),
    ("tradable", "Tradable under the F&O policy"),
    ("history", "Enough history"),
    ("quoted", "Quoted on the live feed"),
    ("liquidity", "B2 liquidity: 20-day turnover"),
    ("price_floor", "B3 price floor"),
    ("breakout", "B4 above the prior 55-day high"),
    ("volume", "B5 volume multiple"),
    ("close_strength", "B6 close location"),
    ("trend", "B7 above its own SMA200"),
    ("momentum", "B8 six-month momentum"),
)


@dataclass(frozen=True)
class Quote:
    """The live book's view of one name. A READ, never an accumulation.

    `session_high`, `session_low` and `session_volume` come straight off the
    Quote/Full packet's own aggregate fields.

    ** `usable` IS THE UNVERIFIED-MAPPING GUARD. ** Root `CLAUDE.md` section 5
    records that the packet's four price fields are mapped open/close/high/low
    per the SDK and have never been checked against a live feed. If high and
    low are transposed, B6's CLV inverts and this strategy buys the weakest
    closes in the market. A transposition shows up here as a high below a low,
    or a last trade outside the session's own range, and a quote that fails
    either is REFUSED rather than computed through. That does not make the
    mapping verified -- `scripts/verify_feed_session_fields.py` does -- but it
    means the failure is a scan that qualifies nothing and says why, instead of
    a book of inverted trades.
    """

    security_id: str
    symbol: str
    price: Optional[float]
    session_high: Optional[float]
    session_low: Optional[float]
    session_volume: Optional[float]
    fno_eligible: bool = False

    @property
    def usable(self) -> bool:
        if self.price is None or self.price <= 0:
            return False
        if self.session_high is None or self.session_low is None:
            return False
        if self.session_high < self.session_low:
            return False
        # The last trade has to be inside the range the same packet reports.
        # A tolerance of nothing at all would reject a legitimate quote whose
        # high has not yet caught up with a trade in the same millisecond.
        span = self.session_high - self.session_low
        tolerance = max(span * 0.001, self.price * 0.0005)
        if self.price > self.session_high + tolerance:
            return False
        if self.price < self.session_low - tolerance:
            return False
        return True

    @property
    def unusable_reason(self) -> Optional[str]:
        if self.price is None or self.price <= 0:
            return "no last-traded price"
        if self.session_high is None or self.session_low is None:
            return "no session high/low on the packet"
        if self.session_high < self.session_low:
            return (
                f"the session high ({self.session_high:,.2f}) is BELOW the "
                f"session low ({self.session_low:,.2f}), which is the "
                f"signature of a transposed field mapping -- see root "
                f"CLAUDE.md section 5"
            )
        if not self.usable:
            return (
                f"the last trade ({self.price:,.2f}) is outside the session "
                f"range ({self.session_low:,.2f}-{self.session_high:,.2f})"
            )
        return None


@dataclass(frozen=True)
class SymbolWindow:
    """One symbol's completed-bar indicators, as the filters need them.

    Built once per symbol per scan. Holds only what B2, B4, B5, B7 and B8 read,
    and deliberately no ATR: this strategy has no stop and nothing here is
    volatility-adjusted.
    """

    symbol: str
    sessions: int
    # B4 -- the PRIOR window's high. The shift is the rule; see
    # `indicators.rolling_max_prior`.
    breakout_high: Optional[float]
    # B5 -- average SHARE volume.
    average_volume: Optional[float]
    # B2 -- average RUPEE turnover. A different column from the one above, and
    # the specification warns about the pair by name.
    turnover: Optional[float]
    # B8 -- computed from completed bars either way, so nothing is substituted.
    momentum: Optional[float]
    # B7 -- the completed part of the SMA window. The live price is appended at
    # scan time, which is section 3's NOTE; on the reconstruction path today's
    # close is already in here.
    _sma_completed: Sequence[float] = field(default_factory=tuple, repr=False)
    _sma_window: int = 200

    def sma_with(self, price: Optional[float]) -> Optional[float]:
        """SMA200 with `price` standing in for today's unfinished close.

        Section 3's NOTE: the backtest computed SMA200 including day T's close,
        and at 15:20 the close is not final, so the current price is the
        correct substitution -- it is what the section 10.3 validation did.
        Passing `None` returns the SMA of the completed window alone, which is
        what the reconstruction path wants because today's close is already in
        it.
        """
        values = list(self._sma_completed)
        if price is not None:
            values = values + [float(price)]
        if len(values) < self._sma_window:
            return None
        return sum(values[-self._sma_window:]) / float(self._sma_window)


@dataclass(frozen=True)
class Candidate:
    """A name through every filter, with everything that put it there."""

    symbol: str
    security_id: Optional[str]
    price: float
    session_high: float
    session_low: float
    session_volume: float
    vol_ratio: float
    clv: float
    breakout_high: float
    momentum: float
    sma: float
    turnover: float
    fno_eligible: bool
    rank: int = 0

    @property
    def above_breakout(self) -> float:
        """How far through its 55-day high it is, as a fraction.

        On the Signals tab because "it broke out" and "it broke out by 0.05%"
        are different things to look at with twenty minutes left in the
        session.
        """
        if self.breakout_high <= 0:
            return 0.0
        return self.price / self.breakout_high - 1.0


@dataclass(frozen=True)
class Rejection:
    symbol: str
    reason: str
    # The stage it fell at, so the funnel and the per-symbol list agree.
    stage: str


@dataclass
class ScanResult:
    """Everything one scan saw, decided and could not decide."""

    session_date: Optional[date]
    candidates: List[Candidate] = field(default_factory=list)
    rejections: List[Rejection] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    # B9, computed and recorded whether or not it is enforced.
    gate_on: Optional[bool] = None
    index_close: Optional[float] = None
    index_sma: Optional[float] = None
    index_symbol: Optional[str] = None

    # The policy this scan ran under, carried on the result so the journal
    # cannot disagree with what actually happened.
    regime_enforced: bool = True
    fno_excluded: bool = True

    # Set when something stopped the scan producing a usable answer at all.
    error: Optional[str] = None

    @property
    def entries_allowed(self) -> bool:
        """May this scan buy anything?

        `None` gate -- it could not be evaluated -- blocks entries when the
        rule is enforced. Undefined is not "on": a gate nobody could measure is
        not a reason to trade, and the rotation applies the same rule.
        """
        if not self.regime_enforced:
            return True
        return self.gate_on is True

    @property
    def blocked_reason(self) -> Optional[str]:
        if self.entries_allowed:
            return None
        if self.gate_on is None:
            return (
                "the regime gate could not be evaluated (no index history), and "
                "an unmeasurable gate is not a reason to trade"
            )
        return (
            f"the regime gate is OFF: {self.index_symbol or 'the index'} at "
            f"{self.index_close:,.1f} is below its long-run SMA of "
            f"{self.index_sma:,.1f}"
        )


def build_window(
    parameters: BtstParameters,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    symbol: str,
    include_last_in_averages: bool,
) -> Optional[SymbolWindow]:
    """One symbol's indicators from its stored bars, oldest first.

    `include_last_in_averages` is the whole difference between the live path
    and the reconstruction, and the module docstring explains it:

    * **False** (live) -- the bars handed in are COMPLETED sessions, today is
      not among them, and `advq`/`adv20` are means of the last N completed
      sessions. Section 3's pseudocode: `mean(volume[t-20 .. t-1])`.
    * **True** (reconstruction) -- the last bar handed in IS day T, and the
      averages include it, which is what `scan.py::build`'s
      `volume.rolling(20).mean()` does and what section 13's table was produced
      with.

    `hi55` is unaffected: `rolling(55).max().shift(1)` excludes day T on both
    paths, which is what makes a breakout possible at all.
    """
    if len(closes) < parameters.minimum_sessions:
        return None

    # B4. The prior window's maximum, whichever path we are on: on the live
    # path the newest bar is yesterday and the whole of it is prior; on the
    # reconstruction path the shift drops day T.
    lookback = parameters.breakout_lookback_sessions
    if include_last_in_averages:
        breakout_high = indicators.rolling_max_prior(highs, lookback)[-1]
    else:
        breakout_high = (
            max(float(one) for one in highs[-lookback:])
            if len(highs) >= lookback
            else None
        )

    average_volumes = indicators.average_share_volume(
        volumes, parameters.volume_window_sessions
    )
    turnovers = indicators.average_daily_value(
        closes, volumes, parameters.liquidity_window_sessions
    )
    # B8 is computed from completed bars on BOTH paths -- section 14 item 4 --
    # so `close.shift(skip) / close.shift(lookback)` is evaluated at the index
    # of day T, which on the live path is one past the end of the list.
    momenta = indicators.momentum(
        closes,
        parameters.momentum_lookback_sessions,
        parameters.momentum_skip_sessions,
    )

    average_volume = average_volumes[-1]
    turnover = turnovers[-1]

    if include_last_in_averages:
        # Day T is the last bar handed in, so the series is evaluated at its
        # own last index.
        momentum = momenta[-1]
    else:
        # Day T is one PAST the last bar handed in, so `close[t-k]` is the kth
        # completed bar back: `closes[-k]`. Evaluating the series at its last
        # index would give `close[t-1-k]` and shift the whole lookback by a
        # session -- the same positional mistake root `CLAUDE.md` section 10d
        # warns about for phantom bars, arrived at from the other direction.
        skip = parameters.momentum_skip_sessions
        span = parameters.momentum_lookback_sessions
        if skip < 1 or len(closes) < span:
            momentum = None
        else:
            old = float(closes[-span])
            momentum = None if old == 0 else float(closes[-skip]) / old - 1.0

    # Only the SMA window is kept, not the whole history: 500 symbols x 2,700
    # sessions of floats is a lot of memory to hold for a mean of the last 200.
    # One spare slot, because the live path appends the current price before
    # taking the window.
    keep = parameters.trend_sma_sessions + 1
    sma_completed = tuple(float(one) for one in closes[-keep:])

    return SymbolWindow(
        symbol=symbol,
        sessions=len(closes),
        breakout_high=breakout_high,
        average_volume=average_volume,
        turnover=turnover,
        momentum=momentum,
        _sma_completed=sma_completed,
        _sma_window=parameters.trend_sma_sessions,
    )


def evaluate(
    parameters: BtstParameters,
    policy: BtstPolicy,
    quotes: Dict[str, Quote],
    windows: Dict[str, SymbolWindow],
    universe: Sequence[str],
    session_date: Optional[date] = None,
    substitute_price_in_sma: bool = True,
) -> ScanResult:
    """The filter funnel, in the specification's own order.

    Pure: no database, no feed, no registry. It takes the universe, what the
    book says and what the bars say, and returns what the rule says. That is
    what lets the Signals tab render a scan nobody is about to trade on, and
    what lets the acceptance test reproduce section 13 with no application
    running at all.

    `substitute_price_in_sma` is False only on the reconstruction path, where
    today's close is already inside the SMA window.
    """
    result = ScanResult(session_date=session_date)
    result.regime_enforced = policy.enforce_regime
    result.fno_excluded = policy.exclude_fno

    counts = {key: 0 for key, _ in FILTER_STAGES}
    rejections: List[Rejection] = []
    candidates: List[Candidate] = []

    def reject(symbol: str, reason: str, stage: str) -> None:
        rejections.append(Rejection(symbol=symbol, reason=reason, stage=stage))

    for symbol in universe:
        counts["universe"] += 1

        quote = quotes.get(symbol)
        fno_eligible = bool(quote.fno_eligible) if quote is not None else False

        # The F&O policy is applied FIRST, before anything is measured. It
        # decides the population, not the outcome, and a name excluded from the
        # universe should not appear in the liquidity count as though it had
        # been considered.
        if policy.exclude_fno and fno_eligible:
            reject(symbol, SKIP_FNO, "tradable")
            continue
        counts["tradable"] += 1

        window = windows.get(symbol)
        if window is None:
            reject(symbol, SKIP_TOO_LITTLE_HISTORY, "history")
            continue
        counts["history"] += 1

        if quote is None:
            reject(symbol, SKIP_NO_QUOTE, "quoted")
            continue
        if not quote.usable:
            reject(
                symbol,
                f"{SKIP_BAD_QUOTE}: {quote.unusable_reason}",
                "quoted",
            )
            continue
        counts["quoted"] += 1

        price = float(quote.price)

        # B2 -- liquidity, in RUPEES.
        if window.turnover is None or window.turnover < parameters.liquidity_floor_rupees:
            reject(symbol, SKIP_ILLIQUID, "liquidity")
            continue
        counts["liquidity"] += 1

        # B3 -- price floor.
        if price < parameters.price_floor:
            reject(symbol, SKIP_PRICE_FLOOR, "price_floor")
            continue
        counts["price_floor"] += 1

        # B4 -- above the PRIOR 55-session high.
        if window.breakout_high is None or price <= window.breakout_high:
            reject(symbol, SKIP_NO_BREAKOUT, "breakout")
            continue
        counts["breakout"] += 1

        # B5 -- the volume surge, on what has traded SO FAR. Not scaled up.
        if (
            window.average_volume is None
            or window.average_volume <= 0
            or quote.session_volume is None
        ):
            reject(symbol, SKIP_VOLUME, "volume")
            continue
        vol_ratio = float(quote.session_volume) / float(window.average_volume)
        if vol_ratio < parameters.volume_multiple:
            reject(symbol, SKIP_VOLUME, "volume")
            continue
        counts["volume"] += 1

        # B6 -- close location within the session's range so far.
        clv = indicators.close_location_value(
            price, float(quote.session_low), float(quote.session_high)
        )
        if clv is None or clv <= parameters.close_location_minimum:
            reject(symbol, SKIP_CLOSE_WEAK, "close_strength")
            continue
        counts["close_strength"] += 1

        # B7 -- above its own SMA200, with the live price standing in for
        # today's unfinished close.
        sma = window.sma_with(price if substitute_price_in_sma else None)
        if sma is None or price <= sma:
            reject(symbol, SKIP_BELOW_SMA, "trend")
            continue
        counts["trend"] += 1

        # B8 -- six-month momentum. Undefined is not a pass.
        if window.momentum is None:
            reject(symbol, SKIP_NO_MOMENTUM, "momentum")
            continue
        if window.momentum <= parameters.momentum_floor:
            reject(symbol, SKIP_MOMENTUM, "momentum")
            continue
        counts["momentum"] += 1

        candidates.append(
            Candidate(
                symbol=symbol,
                security_id=quote.security_id,
                price=price,
                session_high=float(quote.session_high),
                session_low=float(quote.session_low),
                session_volume=float(quote.session_volume),
                vol_ratio=vol_ratio,
                clv=clv,
                breakout_high=float(window.breakout_high),
                momentum=float(window.momentum),
                sma=float(sma),
                turnover=float(window.turnover),
                fno_eligible=fno_eligible,
            )
        )

    # B10 -- rank, then number them. RANKS COVER EVERY CANDIDATE, not the top
    # five: at ~0.54 signals a session more than five is rare, but when it
    # happens the operator has to be able to see that the sixth name existed
    # and where it stood.
    candidates = rank(candidates, parameters.ranking)

    result.candidates = candidates
    result.rejections = rejections
    result.counts = counts
    return result


def rank(candidates: List[Candidate], ranking: str) -> List[Candidate]:
    """B10. Descending, with the symbol as the tie-break.

    The tie-break is alphabetical rather than arbitrary so that two runs over
    the same data pick the same names -- an unstable sort would make the
    journal unreproducible for exactly the 5.7% of signal-days where ranking
    binds at all.
    """
    if ranking == RANK_MOM126:
        key = lambda one: (-one.momentum, one.symbol)  # noqa: E731
    elif ranking == RANK_VOL_RATIO:
        key = lambda one: (-one.vol_ratio, one.symbol)  # noqa: E731
    else:  # pragma: no cover - `BtstParameters` refuses anything else
        raise ScanError(f"Unknown ranking {ranking!r}")

    ordered = sorted(candidates, key=key)
    return [
        dataclasses.replace(one, rank=index)
        for index, one in enumerate(ordered, start=1)
    ]


def evaluate_regime(
    parameters: BtstParameters,
    index_closes: Sequence[float],
    index_symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """B9: the index's close against its own 200-session SMA.

    Computed and reported whether or not it is enforced -- `enforcement:
    observe` stops the gate ACTING, never stops it being recorded. Returns
    `gate_on: None` when there is not enough history, which is "could not be
    evaluated" and is not `False`.
    """
    window = parameters.regime.sma_sessions
    if len(index_closes) < window:
        return {
            "gate_on": None,
            "index_close": float(index_closes[-1]) if index_closes else None,
            "index_sma": None,
            "index_symbol": index_symbol,
        }
    close = float(index_closes[-1])
    sma = sum(float(one) for one in index_closes[-window:]) / float(window)
    return {
        "gate_on": close > sma,
        "index_close": close,
        "index_sma": sma,
        "index_symbol": index_symbol,
    }
