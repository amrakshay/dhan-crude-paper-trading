"""Market sentiment and sector moves from DHAN data -- live all day, no login.

Replaces stockmojo as the DECISION source from 2026-10-09 (stockmojo signed out
freezes at ~10:00; see README "stockmojo needs you signed in"). stockmojo is
still read and recorded next to it, so the rule below can be calibrated
against stockmojo's own labels.

Sentiment -- inputs verified against stockmojo's card:
  PCR          = total put OI / total call OI, nearest expiry. Oct 7:
                 4.62Cr / 6.5Cr = 0.71, exactly stockmojo's figure.
  PCR OI chg   = put OI change / call OI change (vs the previous day's OI).
                 Oct 7: 91.47L / 3.38Cr = 0.27, exactly stockmojo's figure.
  Strikes      = within `window_points` of the spot. stockmojo's chart shows
                 +/-500 around the spot; whether its PCR uses that window or
                 every strike is NOT verified, so both are recorded.
The LABEL is stockmojo's own unpublished scoring (it shows fixed levels:
Neutral 50%, Bullish/Bearish 70%, Very Bullish/Bearish 85%). The score below
is a FIRST GUESS in the same shape, with every threshold in config, to be
tuned on the recorded side-by-side readings. Its components are recorded so
it can be re-scored offline without re-running anything.

Sectors -- stockmojo's sector % is the NSE sectoral index's day change
(within 0.0-0.24 pts on all 18 sectors, 2026-10-09 ~10:55), so it is taken
from the index on Dhan. CHEMICALS has no index on Dhan and is the average of
its member stocks instead (marked). Stock % = Dhan LTP vs the previous
session's official close (daily candle, which includes the closing auction;
the last 5-minute bar would not).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .scrape import Sector, Sentiment


@dataclass
class ChainTotals:
    call_oi: float
    put_oi: float
    call_chg: float
    put_chg: float
    strikes: int

    @property
    def pcr(self) -> Optional[float]:
        return self.put_oi / self.call_oi if self.call_oi else None

    @property
    def pcr_chg(self) -> Optional[float]:
        # Only meaningful when both sides ADDED OI; otherwise the ratio of a
        # positive and a negative change is noise. `flow` covers every case.
        if self.call_chg > 0 and self.put_chg > 0:
            return self.put_chg / self.call_chg
        return None

    @property
    def flow(self) -> Optional[float]:
        """Net new OI towards puts, in [-1, 1]: +1 = only puts written (bullish),
        -1 = only calls written (bearish)."""
        total = abs(self.put_chg) + abs(self.call_chg)
        return (self.put_chg - self.call_chg) / total if total else None


def chain_totals(strikes: dict, spot: Optional[float], window: Optional[float]) -> ChainTotals:
    """Sum a Dhan option chain snapshot's `strikes` {strike: {"CE": leg, "PE": leg}}."""
    ce = pe = dce = dpe = 0.0
    n = 0
    for strike, sides in strikes.items():
        if window is not None and spot is not None and abs(float(strike) - spot) > window:
            continue
        n += 1
        for side, leg in sides.items():
            if leg is None or leg.oi is None:
                continue
            chg = leg.oi - (leg.previous_oi or 0) if leg.previous_oi is not None else 0
            if side == "CE":
                ce += leg.oi
                dce += chg
            else:
                pe += leg.oi
                dpe += chg
    return ChainTotals(ce, pe, dce, dpe, n)


def score_sentiment(t: ChainTotals, nifty_pct: Optional[float], cfg: dict) -> tuple[int, dict]:
    """A score from PCR level, OI flow and Nifty's direction; higher = more bullish."""
    parts = {"pcr": 0, "flow": 0, "price": 0}
    pcr = t.pcr
    if pcr is not None:
        if pcr >= cfg["pcr_strong_bull"]:
            parts["pcr"] = 2
        elif pcr >= cfg["pcr_bull"]:
            parts["pcr"] = 1
        elif pcr <= cfg["pcr_strong_bear"]:
            parts["pcr"] = -2
        elif pcr <= cfg["pcr_bear"]:
            parts["pcr"] = -1
    if t.flow is not None:
        if t.flow >= cfg["flow_cut"]:
            parts["flow"] = 1
        elif t.flow <= -cfg["flow_cut"]:
            parts["flow"] = -1
    if nifty_pct is not None:
        if nifty_pct >= cfg["nifty_move_pct"]:
            parts["price"] = 1
        elif nifty_pct <= -cfg["nifty_move_pct"]:
            parts["price"] = -1
    return sum(parts.values()), parts


def label_for(score: int, cfg: dict) -> tuple[str, str, float]:
    """(direction for trading, stockmojo-style label, strength %)."""
    if score >= cfg["very_at"]:
        return "BULLISH", "Very Bullish", 85.0
    if score >= cfg["lean_at"]:
        return "BULLISH", "Bullish", 70.0
    if score <= -cfg["very_at"]:
        return "BEARISH", "Very Bearish", 85.0
    if score <= -cfg["lean_at"]:
        return "BEARISH", "Bearish", 70.0
    return "NEUTRAL", "Neutral", 50.0


def dhan_sentiment(strikes: dict, spot: Optional[float], nifty_pct: Optional[float],
                   cfg: dict) -> tuple[Sentiment, dict]:
    win = chain_totals(strikes, spot, cfg["window_points"])
    every = chain_totals(strikes, spot, None)
    score, parts = score_sentiment(win, nifty_pct, cfg)
    direction, label, pct = label_for(score, cfg)
    r = (lambda x, n=3: None if x is None else round(x, n))
    record = {
        "dhan_label": label, "dhan_direction": direction, "dhan_pct": pct, "score": score,
        "score_pcr": parts["pcr"], "score_flow": parts["flow"], "score_price": parts["price"],
        "spot": r(spot, 2), "nifty_pct": r(nifty_pct),
        "pcr": r(win.pcr), "pcr_chg": r(win.pcr_chg), "flow": r(win.flow),
        "call_oi": win.call_oi, "put_oi": win.put_oi, "call_oi_chg": win.call_chg,
        "put_oi_chg": win.put_chg, "strikes": win.strikes,
        "pcr_all_strikes": r(every.pcr), "pcr_chg_all_strikes": r(every.pcr_chg),
        "flow_all_strikes": r(every.flow),
    }
    raw = (f"Dhan chain: {label} {pct:.0f}% score {score} {parts} PCR {record['pcr']} "
           f"flow {record['flow']} Nifty {record['nifty_pct']}%")
    return Sentiment(direction, pct, win.pcr, raw), record


def day_change(ltp: Optional[float], prev_close: Optional[float]) -> Optional[float]:
    if not ltp or not prev_close:
        return None
    return (ltp / prev_close - 1) * 100


def dhan_sectors(members: dict[str, list[str]], index_ids: dict[str, Optional[str]],
                 change: dict[str, Optional[float]], index_change: dict[str, Optional[float]],
                 min_coverage: float = 0.6) -> tuple[list[Sector], list[str]]:
    """Sector objects shaped like the scraped ones, from Dhan numbers.

    A sector with no index on Dhan (CHEMICALS) takes its members' average. A
    sector is left out -- and named in the second return value -- when its
    index has no price yet, or too few members have one, so a half-priced
    sector can never win the pick by accident."""
    out, skipped = [], []
    for name, syms in members.items():
        stocks = {s: change[s] for s in syms if change.get(s) is not None}
        if len(stocks) < max(1, min_coverage * len(syms)):
            skipped.append(f"{name}: {len(stocks)}/{len(syms)} priced")
            continue
        idx = index_ids.get(name)
        if idx:
            pct = index_change.get(name)
            if pct is None:
                skipped.append(f"{name}: index unpriced")
                continue
        else:
            pct = sum(stocks.values()) / len(stocks)
        out.append(Sector(name, round(pct, 2), {s: round(v, 2) for s, v in stocks.items()}))
    return out, skipped
