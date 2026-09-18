"""What the strategy sees: the regime, the breadth, the slots and the ranking.

This module answers one question -- *given the daily bars stored as of date D,
what does the rule say?* -- and it does not trade. Everything it returns is a
plain value object, so the same computation feeds a read-only page, a nightly
decision record and a rebalance without any of them re-deriving it.

It reproduces `research2/swing_backtest_v2.py::run_variant`'s signal block
exactly, including the parts that look like details and are not:

* `n_liquid` counts names passing the liquidity and price floors **and** having
  a defined SMA200. A name too young to have one is in neither the numerator
  nor the denominator of breadth.
* A candidate must be above its own SMA200 **and** clear the momentum floor.
  An undefined momentum is not a pass.
* Ranks are assigned over **every** candidate, not the top ten, because a
  rotation exit is justified by a rank of 16 or 40 and the operator has to be
  able to see which.
* Symbols with fewer than `minimum_sessions` bars are excluded from the
  universe entirely. That single filter is the whole difference between the
  specification's 480-symbol and 485-symbol reproductions.
* **A symbol with no bar on the session being decided is skipped, never
  forward-filled** (the backtest's mechanic 1). The session is the REGIME
  INDEX's own date -- the master calendar. Without this, a delisted or
  suspended name stays rankable on its last close for ever, and can be bought
  at a price that no longer exists. JBCHEPHARM is the live example: it stopped
  trading on 2026-07-16 and, forward-filled, it ranked as a candidate two
  months later.

Indicator arithmetic is float; see `indicators.py`. Nothing in this module
touches money.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.logging_config import get_logger
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.services import indicators
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.ranking")


class RankingError(Exception):
    pass


# Why a symbol is not a candidate. Recorded per symbol so a decision record can
# answer "why was X not bought" without recomputing anything.
SKIP_TOO_LITTLE_HISTORY = "too little history"
SKIP_NO_SMA200 = "no SMA200 yet"
SKIP_ILLIQUID = "below the liquidity floor"
SKIP_PRICE_FLOOR = "below the price floor"
SKIP_BELOW_SMA200 = "below its own SMA200"
SKIP_MOMENTUM = "below the momentum floor"
SKIP_NO_MOMENTUM = "momentum undefined"
SKIP_NO_SCORE = "score undefined"
SKIP_NO_BAR = "no stored bars"
SKIP_NOT_TRADED = "did not trade on this session"


@dataclass(frozen=True)
class SymbolView:
    """One symbol's indicators as of the session being decided."""

    symbol: str
    bar_date: date
    close: float
    atr14: float
    sma200: Optional[float]
    adv20: Optional[float]
    momentum: Optional[float]
    score: Optional[float]
    sessions: int

    @property
    def atr_percent(self) -> Optional[float]:
        if self.close <= 0:
            return None
        return self.atr14 / self.close


@dataclass(frozen=True)
class Candidate:
    rank: int
    symbol: str
    score: float
    momentum: float
    atr_percent: float
    close: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "symbol": self.symbol,
            "score": round(self.score, 4),
            "momentum": round(self.momentum, 6),
            "atrPercent": round(self.atr_percent, 6),
            "close": round(self.close, 2),
        }


@dataclass(frozen=True)
class RegimeState:
    """P8 and P9, with the numbers that produced them.

    "Gate OFF" is useless in six months. "NIFTY 23,270.6 against SMA200
    24,501.7, needs +5.3%" is auditable, which is why every input is carried
    rather than just the conclusion.
    """

    index_symbol: str
    bar_date: Optional[date]
    close: Optional[float]
    sma200: Optional[float]
    gate_on: bool                       # P8
    return_over_window: Optional[float]  # P9's 63-session return
    return_window_sessions: int
    entries_allowed: bool               # P9
    reason: str

    @property
    def shortfall_percent(self) -> Optional[float]:
        """How far the index must rise to reclaim its SMA200. None if above."""
        if self.close is None or self.sma200 is None or self.close >= self.sma200:
            return None
        return (self.sma200 / self.close - 1.0) * 100.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "indexSymbol": self.index_symbol,
            "barDate": self.bar_date.isoformat() if self.bar_date else None,
            "close": round(self.close, 2) if self.close is not None else None,
            "sma200": round(self.sma200, 2) if self.sma200 is not None else None,
            "gateOn": self.gate_on,
            "returnOverWindow": (
                round(self.return_over_window, 6)
                if self.return_over_window is not None
                else None
            ),
            "returnWindowSessions": self.return_window_sessions,
            "entriesAllowed": self.entries_allowed,
            "shortfallPercent": (
                round(self.shortfall_percent, 2)
                if self.shortfall_percent is not None
                else None
            ),
            "reason": self.reason,
        }


@dataclass
class RankingSnapshot:
    """Everything the rule says about one session."""

    strategy_key: str
    as_of: date
    regime: RegimeState
    liquid_count: int
    above_sma_count: int
    breadth: Optional[float]
    slots: Optional[int]
    candidates: List[Candidate] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)
    universe_size: int = 0
    symbols_with_bars: int = 0
    parameters_digest: Dict[str, Any] = field(default_factory=dict)

    def rank_of(self, symbol: str) -> Optional[int]:
        for candidate in self.candidates:
            if candidate.symbol == symbol:
                return candidate.rank
        return None

    def as_dict(self, top: Optional[int] = 15) -> Dict[str, Any]:
        shown = self.candidates if top is None else self.candidates[:top]
        return {
            "strategyKey": self.strategy_key,
            "asOf": self.as_of.isoformat(),
            "regime": self.regime.as_dict(),
            "liquidUniverse": self.liquid_count,
            "aboveOwnSma200": self.above_sma_count,
            "breadth": round(self.breadth, 4) if self.breadth is not None else None,
            "breadthPercent": (
                round(self.breadth * 100, 1) if self.breadth is not None else None
            ),
            "slots": self.slots,
            "candidateCount": len(self.candidates),
            "universeSize": self.universe_size,
            "symbolsWithBars": self.symbols_with_bars,
            "ranking": [candidate.as_dict() for candidate in shown],
            "parameters": self.parameters_digest,
        }


def _as_floats(bars) -> Tuple[List[float], List[float], List[float], List[Optional[float]]]:
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    closes = [float(bar.close) for bar in bars]
    volumes = [None if bar.volume is None else float(bar.volume) for bar in bars]
    return highs, lows, closes, volumes


class RankingService:
    """Computes the regime, the breadth and the ranking from stored bars."""

    def __init__(
        self,
        repository: DailyBarRepository,
        definition: StrategyDefinition,
        parameters: Optional[SwingParameters] = None,
    ):
        self.repository = repository
        self.definition = definition
        self.parameters = parameters or SwingParameters.from_definition(definition)

    # --- the universe ------------------------------------------------------
    def universe_symbols(self) -> List[str]:
        symbols: set = set()
        for instrument_set in self.definition.instrument_sets:
            if instrument_set.from_universe and self.definition.universe:
                symbols.update(self.definition.universe.symbols)
            else:
                symbols.update(instrument_set.symbols)
        return sorted(symbols)

    def _equity_segment(self) -> str:
        for instrument_set in self.definition.instrument_sets:
            if instrument_set.from_universe:
                return instrument_set.exchange_segment
        return self.definition.exchange_segment

    # --- the regime --------------------------------------------------------
    async def regime_state(self, as_of: Optional[date] = None) -> RegimeState:
        """P8 and P9, evaluated on the index's own daily closes."""
        policy = self.parameters.regime
        reference = self.definition.reference_instrument(policy.index_role)
        if reference is None:
            raise RankingError(
                f"Strategy {self.definition.key!r} declares no reference "
                f"instrument with role {policy.index_role!r}, so the regime gate "
                f"cannot be evaluated."
            )

        needed = max(policy.sma_sessions, policy.entry_return_sessions) + 5
        bars = await self.repository.history(
            reference.symbol,
            reference.exchange_segment,
            limit=needed,
            on_or_before=as_of,
        )
        if not bars:
            return RegimeState(
                index_symbol=reference.symbol,
                bar_date=None,
                close=None,
                sma200=None,
                gate_on=False,
                return_over_window=None,
                return_window_sessions=policy.entry_return_sessions,
                entries_allowed=False,
                reason=(
                    f"No stored daily bars for {reference.symbol}; the gate "
                    f"cannot be evaluated and nothing is assumed."
                ),
            )

        closes = [float(bar.close) for bar in bars]
        sma_series = indicators.sma(closes, policy.sma_sessions)
        returns = indicators.percent_change(closes, policy.entry_return_sessions)

        close = closes[-1]
        sma200 = sma_series[-1]
        window_return = returns[-1]

        if sma200 is None:
            return RegimeState(
                index_symbol=reference.symbol,
                bar_date=bars[-1].bar_date,
                close=close,
                sma200=None,
                gate_on=False,
                return_over_window=window_return,
                return_window_sessions=policy.entry_return_sessions,
                entries_allowed=False,
                reason=(
                    f"Only {len(bars)} stored sessions for {reference.symbol}; "
                    f"{policy.sma_sessions} are needed for the SMA the gate "
                    f"reads. The gate is treated as OFF rather than assumed ON."
                ),
            )

        gate_on = close > sma200
        entry_return_ok = (
            window_return is not None
            and window_return > policy.entry_return_minimum
        )
        entries_allowed = gate_on and entry_return_ok

        if not gate_on:
            reason = (
                f"{reference.symbol} {close:,.1f} is below its "
                f"{policy.sma_sessions}-session SMA {sma200:,.1f}; it needs "
                f"{(sma200 / close - 1) * 100:.1f}% to reclaim it. Gate OFF: "
                f"hold no positions."
            )
        elif not entry_return_ok:
            shown = (
                "undefined"
                if window_return is None
                else f"{window_return * 100:.2f}%"
            )
            reason = (
                f"{reference.symbol} {close:,.1f} is above its SMA "
                f"{sma200:,.1f}, but its {policy.entry_return_sessions}-session "
                f"return is {shown}, so NEW ENTRIES are blocked. Existing "
                f"positions are unaffected."
            )
        else:
            reason = (
                f"{reference.symbol} {close:,.1f} is above its SMA "
                f"{sma200:,.1f} and its {policy.entry_return_sessions}-session "
                f"return is {window_return * 100:.2f}%. Entries allowed."
            )

        return RegimeState(
            index_symbol=reference.symbol,
            bar_date=bars[-1].bar_date,
            close=close,
            sma200=sma200,
            gate_on=gate_on,
            return_over_window=window_return,
            return_window_sessions=policy.entry_return_sessions,
            entries_allowed=entries_allowed,
            reason=reason,
        )

    # --- one symbol --------------------------------------------------------
    async def symbol_view(
        self, symbol: str, segment: str, as_of: Optional[date] = None
    ) -> Optional[SymbolView]:
        parameters = self.parameters
        needed = (
            max(
                parameters.trend_sma_sessions,
                parameters.momentum_lookback_sessions,
                parameters.minimum_sessions,
            )
            + 10
        )
        bars = await self.repository.history(
            symbol, segment, limit=needed, on_or_before=as_of
        )
        if not bars:
            return None
        return self._view_from_bars(symbol, bars)

    def _view_from_bars(self, symbol: str, bars) -> SymbolView:
        parameters = self.parameters
        highs, lows, closes, volumes = _as_floats(bars)

        sma_series = indicators.sma(closes, parameters.trend_sma_sessions)
        atr_series = indicators.atr(highs, lows, closes, parameters.atr_sessions)
        adv_series = indicators.average_daily_value(
            closes, volumes, parameters.liquidity_window_sessions
        )
        momentum_series = indicators.momentum(
            closes,
            parameters.momentum_lookback_sessions,
            parameters.momentum_skip_sessions,
        )

        close = closes[-1]
        atr14 = atr_series[-1]
        atr_percent = (atr14 / close) if close > 0 else None
        momentum_value = momentum_series[-1]
        rank_score = (
            indicators.score(momentum_value, atr_percent)
            if parameters.volatility_adjusted_score
            else momentum_value
        )

        return SymbolView(
            symbol=symbol,
            bar_date=bars[-1].bar_date,
            close=close,
            atr14=atr14,
            sma200=sma_series[-1],
            adv20=adv_series[-1],
            momentum=momentum_value,
            score=rank_score,
            sessions=len(bars),
        )

    # --- the whole session -------------------------------------------------
    async def snapshot(
        self,
        as_of: Optional[date] = None,
        momentum_floor: Optional[float] = None,
    ) -> RankingSnapshot:
        """The full picture for one session: regime, breadth, slots, ranking.

        `momentum_floor` overrides P6, which is how the V3b off-gate variant
        applies its own floor without a second copy of this computation.
        """
        parameters = self.parameters
        segment = self._equity_segment()
        symbols = self.universe_symbols()
        floor = parameters.momentum_floor if momentum_floor is None else momentum_floor

        regime = await self.regime_state(as_of=as_of)
        # The master calendar is the regime index's own trading dates
        # (mechanic 1). A session is the date the index has a bar for, and a
        # stock without a bar on that date simply did not trade.
        session_date = regime.bar_date
        resolved_as_of = session_date or as_of

        liquid = 0
        above = 0
        scored: List[Tuple[str, float, float, float, float]] = []
        skipped: Dict[str, str] = {}
        with_bars = 0

        needed = (
            max(
                parameters.trend_sma_sessions,
                parameters.momentum_lookback_sessions,
                parameters.minimum_sessions,
            )
            + 10
        )

        for symbol in symbols:
            bars = await self.repository.history(
                symbol, segment, limit=needed, on_or_before=as_of
            )
            if not bars:
                skipped[symbol] = SKIP_NO_BAR
                continue
            with_bars += 1

            # Never forward-fill. A symbol whose newest bar predates the
            # session did not trade on it, and pricing it off a stale close is
            # how a delisted name stays on a buy list.
            if session_date is not None and bars[-1].bar_date != session_date:
                skipped[symbol] = SKIP_NOT_TRADED
                continue

            # The backtest's loader drops a symbol with too little history from
            # the universe entirely, before any filter runs. That single line is
            # the difference between its 480-symbol and 485-symbol runs.
            if len(bars) < parameters.minimum_sessions:
                skipped[symbol] = SKIP_TOO_LITTLE_HISTORY
                continue

            view = self._view_from_bars(symbol, bars)

            if view.adv20 is None or view.adv20 < parameters.liquidity_floor_rupees:
                skipped[symbol] = SKIP_ILLIQUID
                continue
            if view.close < parameters.price_floor:
                skipped[symbol] = SKIP_PRICE_FLOOR
                continue
            if view.sma200 is None:
                # Counted in neither the numerator nor the denominator of
                # breadth -- the engine's `np.isnan(r.sma200)` guard sits
                # before `n_liquid += 1`.
                skipped[symbol] = SKIP_NO_SMA200
                continue

            liquid += 1
            is_above = view.close > view.sma200
            if is_above:
                above += 1
            else:
                skipped[symbol] = SKIP_BELOW_SMA200
                continue

            if view.momentum is None:
                skipped[symbol] = SKIP_NO_MOMENTUM
                continue
            if view.momentum <= floor:
                skipped[symbol] = SKIP_MOMENTUM
                continue
            if view.score is None:
                skipped[symbol] = SKIP_NO_SCORE
                continue

            scored.append(
                (symbol, view.score, view.momentum, view.atr_percent or 0.0, view.close)
            )

        breadth = (above / liquid) if liquid else None
        slots = parameters.slots_for_breadth(breadth)

        # Descending score. Ties break on the symbol so a ranking is stable
        # between two runs on the same data -- an unstable tie would show up as
        # a rotation exit nobody asked for.
        scored.sort(key=lambda item: (-item[1], item[0]))
        candidates = [
            Candidate(
                rank=index + 1,
                symbol=symbol,
                score=value,
                momentum=momentum_value,
                atr_percent=atr_percent,
                close=close,
            )
            for index, (symbol, value, momentum_value, atr_percent, close) in enumerate(
                scored
            )
        ]

        snapshot = RankingSnapshot(
            strategy_key=self.definition.key,
            as_of=resolved_as_of or date.min,
            regime=regime,
            liquid_count=liquid,
            above_sma_count=above,
            breadth=breadth,
            slots=slots,
            candidates=candidates,
            skipped=skipped,
            universe_size=len(symbols),
            symbols_with_bars=with_bars,
            parameters_digest={
                "momentumFloor": floor,
                "liquidityFloorRupees": parameters.liquidity_floor_rupees,
                "priceFloor": parameters.price_floor,
                "maxPositions": parameters.max_positions,
                "rotationExitRank": parameters.rotation_exit_rank,
                "trailAtrMultiple": parameters.trail_atr_multiple,
                "breadthLower": parameters.breadth_lower,
                "breadthSpan": parameters.breadth_span,
                "rebalanceCadence": parameters.schedule.rebalance_cadence,
                "offGateEnabled": parameters.off_gate.enabled,
            },
        )

        logger.info(
            "Swing snapshot %s as of %s: gate=%s breadth=%s (%s/%s) slots=%s "
            "candidates=%s",
            self.definition.key,
            snapshot.as_of.isoformat(),
            "ON" if regime.gate_on else "OFF",
            f"{breadth:.3f}" if breadth is not None else "unknown",
            above, liquid, slots, len(candidates),
        )
        return snapshot
