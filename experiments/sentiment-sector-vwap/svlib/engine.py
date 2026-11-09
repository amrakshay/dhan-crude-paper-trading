"""The trading rules, with no I/O: indicators, entry setups and the trailing stop.

Everything here is a pure function of closed 5-minute bars, so the same code
runs live and under test.

Rules (from the brief, with the interpretations written down):

  * Indicators on 5-minute bars. VWAP resets each session and uses the typical
    price (H+L+C)/3 -- what a chart's session VWAP shows. The 9 EMA runs
    across PREVIOUS sessions' bars too, the way it does on a chart, so it is
    defined at 09:45 rather than only from 10:00.
  * LONG when the side is bullish, SHORT when bearish.
  * Fresh crossover: the 9 EMA closes above VWAP (long) / below (short) on a
    bar that closed AFTER the stock joined the tracking list. Entry at that
    bar's close; stop = that bar's low (long) / high (short).
  * Crossover already done when the stock is picked up: wait for a RETEST --
    a bar that touches VWAP (low <= VWAP for a long, high >= VWAP for a
    short) and closes back on the trend side, while the EMA is still on the
    trend side. Stop = that bar's low / high.
  * If the EMA crosses back against the side, a pending retest is abandoned
    and the setup waits for a fresh crossover.
  * Trailing: after each closed bar the stop moves to that bar's VWAP, but
    only in the trade's favour (a long's stop never goes down). The brief
    says "trail the SL to VWAP every candle"; a stop that could loosen would
    not be a trailing stop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

LONG, SHORT = "LONG", "SHORT"
WAIT_CROSS, WAIT_RETEST = "WAIT_CROSS", "WAIT_RETEST"


@dataclass(frozen=True)
class Bar:
    start: datetime      # IST, naive; the bar covers [start, start + 5 min)
    open: float
    high: float
    low: float
    close: float
    volume: float


def ema_series(closes: list[float], length: int = 9) -> list[Optional[float]]:
    """EMA seeded with the SMA of the first `length` closes (as Pine's ta.ema)."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) < length:
        return out
    alpha = 2.0 / (length + 1)
    value = sum(closes[:length]) / length
    out[length - 1] = value
    for i in range(length, len(closes)):
        value = alpha * closes[i] + (1 - alpha) * value
        out[i] = value
    return out


def vwap_series(bars: list[Bar]) -> list[Optional[float]]:
    """Session VWAP; resets when the calendar date changes."""
    out: list[Optional[float]] = []
    day = None
    pv = vol = 0.0
    for b in bars:
        if b.start.date() != day:
            day, pv, vol = b.start.date(), 0.0, 0.0
        typical = (b.high + b.low + b.close) / 3.0
        pv += typical * (b.volume or 0.0)
        vol += b.volume or 0.0
        out.append(pv / vol if vol > 0 else typical)
    return out


@dataclass
class Point:
    """One closed bar with its indicators -- what the setup sees."""
    bar: Bar
    ema: Optional[float]
    vwap: Optional[float]


def with_indicators(bars: list[Bar], ema_length: int = 9) -> list[Point]:
    emas = ema_series([b.close for b in bars], ema_length)
    vwaps = vwap_series(bars)
    return [Point(b, e, v) for b, e, v in zip(bars, emas, vwaps)]


def aligned(side: str, p: Point) -> Optional[bool]:
    if p.ema is None or p.vwap is None:
        return None
    return p.ema > p.vwap if side == LONG else p.ema < p.vwap


@dataclass
class Signal:
    symbol: str
    side: str
    kind: str            # CROSS or RETEST
    bar: Bar
    price: float         # the signal bar's close
    stop: float
    ema: float
    vwap: float


@dataclass
class Setup:
    """Watches one tracked symbol for an entry on one side.

    `armed_after` is the start of the last bar that had CLOSED when the
    symbol joined the list. Only bars after it can produce a crossover entry;
    if the EMA was already on the trend side at that bar, the crossover
    "happened before we identified the stock" and the setup waits for a
    retest instead.
    """
    symbol: str
    side: str
    armed_after: datetime
    mode: str = WAIT_CROSS
    last_seen: Optional[datetime] = None
    history: list = field(default_factory=list)

    @classmethod
    def start(cls, symbol: str, side: str, points: list[Point]) -> "Setup":
        closed = [p for p in points if p.ema is not None]
        last = closed[-1] if closed else None
        setup = cls(symbol, side, armed_after=last.bar.start if last else datetime.min)
        if last is not None and aligned(side, last):
            setup.mode = WAIT_RETEST
        setup.last_seen = setup.armed_after
        setup.history.append((setup.armed_after, setup.mode, "start"))
        return setup

    def update(self, points: list[Point]) -> list[Signal]:
        """Feed every closed bar since the last call; return entry signals."""
        signals = []
        prev: Optional[Point] = None
        for p in points:
            if self.last_seen is not None and p.bar.start <= self.last_seen:
                prev = p
                continue
            self.last_seen = p.bar.start
            now_aligned = aligned(self.side, p)
            was_aligned = aligned(self.side, prev) if prev is not None else None
            prev = p
            if now_aligned is None:
                continue
            if now_aligned and was_aligned is False:
                signals.append(self._signal("CROSS", p))
                self._set(WAIT_RETEST, p, "cross")
            elif not now_aligned:
                if self.mode != WAIT_CROSS:
                    self._set(WAIT_CROSS, p, "ema crossed back")
            elif self.mode == WAIT_RETEST and self._is_retest(p):
                signals.append(self._signal("RETEST", p))
        return signals

    def _is_retest(self, p: Point) -> bool:
        b = p.bar
        if self.side == LONG:
            return b.low <= p.vwap and b.close > p.vwap
        return b.high >= p.vwap and b.close < p.vwap

    def _signal(self, kind: str, p: Point) -> Signal:
        stop = p.bar.low if self.side == LONG else p.bar.high
        return Signal(self.symbol, self.side, kind, p.bar, p.bar.close, stop, p.ema, p.vwap)

    def _set(self, mode: str, p: Point, why: str) -> None:
        self.mode = mode
        self.history.append((p.bar.start, mode, why))


def stop_is_valid(side: str, entry: float, stop: float) -> bool:
    return stop < entry if side == LONG else stop > entry


def stop_hit(side: str, stop: float, price: float) -> bool:
    return price <= stop if side == LONG else price >= stop


def bar_stop_fill(side: str, stop: float, bar: Bar) -> Optional[float]:
    """Price a stop fills at inside a closed bar, gap-aware; None if untouched.
    A bar that OPENS through the stop fills at the open, not at the stop."""
    if side == LONG:
        if bar.low > stop:
            return None
        return min(stop, bar.open)
    if bar.high < stop:
        return None
    return max(stop, bar.open)


TRAIL_LEVELS = ("ema9", "vwap")


def trail_level(p: "Point", trail_with: str) -> Optional[float]:
    """The line a stop trails to. "ema9" since 2026-10-09 (VWAP barely moves
    late in a session, so a VWAP trail gives most of a move back); trades
    opened before that keep "vwap"."""
    return p.ema if trail_with == "ema9" else p.vwap


def trail(side: str, stop: float, level: Optional[float]) -> float:
    """Move the stop to `level` (the 9 EMA or VWAP), never against the trade."""
    if level is None:
        return stop
    return max(stop, level) if side == LONG else min(stop, level)


def simulate_trail(side: str, entry: float, initial_stop: float, points_after: list,
                   trail_with: str) -> Optional[float]:
    """Gross R of the same entry under a given trail, from closed bars only
    (stop checked gap-aware on each bar, then trailed; whatever survives exits
    at the last bar's close). A counterfactual for comparing EMA and VWAP
    trails on identical entries -- not what the live book did."""
    risk = abs(entry - initial_stop)
    if risk <= 0 or not points_after:
        return None
    d = 1 if side == LONG else -1
    stop = initial_stop
    for p in points_after:
        fill = bar_stop_fill(side, stop, p.bar)
        if fill is not None:
            return round(d * (fill - entry) / risk, 3)
        stop = trail(side, stop, trail_level(p, trail_with))
        if (p.bar.close <= stop) if d > 0 else (p.bar.close >= stop):
            return round(d * (p.bar.close - entry) / risk, 3)
    return round(d * (points_after[-1].bar.close - entry) / risk, 3)


def pick_sector(sectors, label: str, exclude: set[str]):
    """Most bullish sector for BULLISH, most bearish for BEARISH; None otherwise."""
    pool = [s for s in sectors if s.name not in exclude]
    if not pool or label not in ("BULLISH", "BEARISH"):
        return None
    key = (lambda s: s.pct)
    return max(pool, key=key) if label == "BULLISH" else min(pool, key=key)


def pick_stocks(sector, label: str, n: int = 3) -> list[tuple[str, float]]:
    """Top n names in the sector in the sentiment's direction.

    Ranked over the sector's gainers AND losers together, so a bullish day
    whose strongest sector has only one gainer still yields three names --
    the next two strongest -- rather than a short list.
    """
    items = sorted(sector.stocks.items(), key=lambda kv: kv[1], reverse=(label == "BULLISH"))
    return items[:n]


# --- data points for optimisation ------------------------------------------------
# Recorded on every signal and every trade so that "which 2 trades a day" can be
# answered later from data rather than guessed now. Nothing here decides a trade.

ANGLE_UNIT_PCT = 0.1   # degrees are measured on a chart where 1 bar across == 0.1% of price up


def _deg(slope_pct: Optional[float]) -> Optional[float]:
    import math
    return None if slope_pct is None else math.degrees(math.atan(slope_pct / ANGLE_UNIT_PCT))


def _r(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None else round(x, n)


def entry_features(points: list[Point], i: int, side: str, stop: float) -> dict:
    """Shape of the market at signal bar `i`. Slopes and gaps are SIGNED as on
    the chart (negative = falling / EMA under VWAP); `side` says which way helps."""
    p = points[i]
    b = p.bar
    px = b.close
    prev = points[i - 1] if i > 0 else None
    back3 = points[i - 3] if i >= 3 else None
    pct = (lambda x: x / px * 100.0)
    day = b.start.date()
    today = [q for q in points[: i + 1] if q.bar.start.date() == day]
    before = [q for q in points[: i + 1] if q.bar.start.date() < day]

    ema_s = pct(p.ema - prev.ema) if prev and prev.ema is not None and p.ema is not None else None
    vwap_s = pct(p.vwap - prev.vwap) if prev and prev.vwap is not None and prev.bar.start.date() == day else None
    ema_s3 = (pct(p.ema - back3.ema) / 3) if back3 and back3.ema is not None and p.ema is not None else None

    trs = []
    for k in range(max(1, i - 13), i + 1):
        cur, pr = points[k].bar, points[k - 1].bar
        trs.append(max(cur.high - cur.low, abs(cur.high - pr.close), abs(cur.low - pr.close)))
    atr = sum(trs) / len(trs) if trs else None
    vols = [q.bar.volume for q in points[max(0, i - 20): i] if q.bar.volume]
    rel_vol = b.volume / (sum(vols) / len(vols)) if vols and b.volume else None

    signs = [(q.ema > q.vwap) for q in today if q.ema is not None and q.vwap is not None]
    flips = [k for k in range(1, len(signs)) if signs[k] != signs[k - 1]]
    day_hi = max(q.bar.high for q in today)
    day_lo = min(q.bar.low for q in today)
    rng = b.high - b.low
    risk = abs(px - stop)
    deg_e, deg_v = _deg(ema_s), _deg(vwap_s)
    return {
        "ema_slope_pct": _r(ema_s), "vwap_slope_pct": _r(vwap_s), "ema_slope3_pct": _r(ema_s3),
        "ema_deg": _r(deg_e, 1), "vwap_deg": _r(deg_v, 1),
        "angle_between_deg": _r(abs(deg_e - deg_v), 1) if deg_e is not None and deg_v is not None else None,
        "ema_vwap_gap_pct": _r(pct(p.ema - p.vwap)) if p.ema is not None else None,
        "close_vs_vwap_pct": _r(pct(px - p.vwap)),
        "candle_range_pct": _r(pct(rng)),
        "candle_body_pct": _r(pct(b.close - b.open)),
        "close_in_range": _r((b.close - b.low) / rng, 3) if rng > 0 else None,  # 1 = closed at the high
        "rel_volume_20": _r(rel_vol, 2),
        "atr14_pct": _r(pct(atr)) if atr else None,
        "risk_pct": _r(pct(risk)),
        "risk_atr": _r(risk / atr, 2) if atr else None,
        "day_change_pct": _r(pct(px - before[-1].bar.close)) if before else None,
        "from_open_pct": _r(pct(px - today[0].bar.open)),
        "day_range_pos": _r((px - day_lo) / (day_hi - day_lo), 3) if day_hi > day_lo else None,
        "ema_vwap_crosses_today": len(flips),
        "bars_since_last_cross": (len(signs) - 1 - flips[-1]) if flips else None,
    }


def excursions(side: str, entry: float, initial_stop: float, bars: list[Bar],
               exit_at: Optional[datetime]) -> dict:
    """What the trade COULD have done, from the bars after its signal bar.

    In R (1R = entry-to-initial-stop). `in_trade` runs to the exit bar,
    `to_1500` to the last bar given. Where a bar reaches both the stop and a
    target, the stop is assumed first -- the pessimistic reading."""
    risk = abs(entry - initial_stop)
    if risk <= 0 or not bars:
        return {}
    d = 1 if side == LONG else -1

    def window(bs):
        fav = max((d * ((b.high if d > 0 else b.low) - entry) for b in bs), default=0.0)
        adv = max((d * (entry - (b.low if d > 0 else b.high)) for b in bs), default=0.0)
        return fav / risk, adv / risk

    in_trade = [b for b in bars if exit_at is None or b.start <= exit_at]
    mfe_t, mae_t = window(in_trade)
    mfe_d, mae_d = window(bars)
    reached = {1: None, 2: None, 3: None}
    for b in bars:
        hit_stop = (b.low <= initial_stop) if d > 0 else (b.high >= initial_stop)
        if hit_stop:
            break
        best = d * ((b.high if d > 0 else b.low) - entry) / risk
        for k in reached:
            if reached[k] is None and best >= k:
                reached[k] = b.start
    return {
        "mfe_r_in_trade": round(mfe_t, 3), "mae_r_in_trade": round(mae_t, 3),
        "mfe_r_to_1500": round(mfe_d, 3), "mae_r_to_1500": round(mae_d, 3),
        "r_if_held_to_1500": round(d * (bars[-1].close - entry) / risk, 3),
        "reached_1r_before_stop": reached[1] is not None,
        "reached_2r_before_stop": reached[2] is not None,
        "reached_3r_before_stop": reached[3] is not None,
    }
