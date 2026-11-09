"""Trade and portfolio statistics, written around the LEFT TAIL.

This strategy family is the mirror image of a breakout system: a high win rate
with a rare large loss.  Every instinct built on positively-skewed strategies
points the wrong way here, so the summary is deliberately arranged to make the
tail unavoidable and the win rate hard to mistake for evidence.

Three rules enforced by the shape of the output:

  1. `win_rate` is computed and immediately labelled meaningless.  80-90% is
     what selling out-of-the-money options produces by construction; it says
     nothing about whether the strategy is profitable.
  2. Nothing is summarised without `worst_trade`, `worst_1pct`, `worst_month`
     and `max_loss_per_margin` beside it.  A mean and a win rate together can
     describe a strategy that goes to zero.
  3. `tail_events` counts how many genuinely bad days the SAMPLE contained.
     If the answer is zero, that is the headline, not a footnote -- a backtest
     with no disaster in it is undersampled, not safe.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np

TRADING_DAYS = 252


@dataclass
class TradeStats:
    n: int = 0
    n_open_at_end: int = 0
    win_rate: float = 0.0
    mean_pnl: float = 0.0
    median_pnl: float = 0.0
    total_pnl: float = 0.0
    total_charges: float = 0.0
    gross_pnl: float = 0.0

    # the tail
    worst_trade: float = 0.0
    worst_1pct: float = 0.0
    worst_5pct: float = 0.0
    best_trade: float = 0.0
    worst_month: float = 0.0
    worst_month_label: str = ""
    max_loss_per_margin: float = 0.0
    loss_tail_ratio: float = 0.0     # mean loss / mean win

    # margin
    mean_margin: float = 0.0
    peak_margin: float = 0.0
    mean_return_on_margin: float = 0.0
    total_return_on_margin: float = 0.0
    # Return per rupee-YEAR of margin actually deployed.  `total_return_on_margin`
    # is sum(P&L) / sum(margin) and is NOT annualisable: it ignores how long each
    # rupee was tied up, so a strategy holding 19 days and one holding 60 look
    # comparable when they are not.  This divides by sum(margin x days) / 365,
    # which is the only form that can be set beside the risk-free rate.
    annualised_return_on_margin: float = 0.0
    margin_rupee_years: float = 0.0
    # The same thing measured against MAX LOSS rather than margin.  For a
    # defined-risk structure max loss IS the capital at risk, and margin is only
    # what the exchange happens to collateralise -- a median of ~26% of it here.
    # Return on margin is therefore a LEVERAGED number; this one is not, and it
    # is the one that can honestly be set beside the risk-free rate.
    annualised_return_on_max_loss: float = 0.0
    max_loss_rupee_years: float = 0.0

    # shape
    mean_days_held: float = 0.0
    skew: float = 0.0
    exit_reasons: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def _percentile_low(x: np.ndarray, pct: float) -> float:
    """Mean of the worst `pct` of observations (a conditional tail mean).

    A single percentile point is noisy with few trades; the mean of everything
    below it is what a position-sizing decision actually needs.
    """
    if len(x) == 0:
        return 0.0
    k = max(1, int(math.ceil(len(x) * pct)))
    return float(np.sort(x)[:k].mean())


def trade_stats(trades: list, include_open: bool = False) -> TradeStats:
    done = [t for t in trades if t.exit_reason != "still_open_at_sample_end"]
    n_open = len(trades) - len(done)
    use = trades if include_open else done
    s = TradeStats(n=len(use), n_open_at_end=n_open)
    if not use:
        return s

    pnl = np.array([t.net_pnl for t in use], dtype=float)
    margin = np.array([t.margin_at_entry for t in use], dtype=float)
    peak = np.array([t.peak_margin for t in use], dtype=float)

    s.win_rate = float((pnl > 0).mean())
    s.mean_pnl = float(pnl.mean())
    s.median_pnl = float(np.median(pnl))
    s.total_pnl = float(pnl.sum())
    s.gross_pnl = float(sum(t.gross_pnl for t in use))
    s.total_charges = float(sum(t.charges for t in use))

    s.worst_trade = float(pnl.min())
    s.best_trade = float(pnl.max())
    s.worst_1pct = _percentile_low(pnl, 0.01)
    s.worst_5pct = _percentile_low(pnl, 0.05)

    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    if len(wins) and len(losses):
        s.loss_tail_ratio = float(abs(losses.mean()) / wins.mean())

    with np.errstate(divide="ignore", invalid="ignore"):
        rom = np.where(margin > 0, pnl / np.maximum(margin, 1e-9), 0.0)
    s.max_loss_per_margin = float(rom.min())
    s.mean_return_on_margin = float(rom.mean())
    if margin.sum() > 0:
        s.total_return_on_margin = float(pnl.sum() / margin.sum())
    held = np.array([max(t.days_held, 1) for t in use], dtype=float)
    rupee_years = float((margin * held).sum() / 365.0)
    s.margin_rupee_years = rupee_years
    if rupee_years > 0:
        s.annualised_return_on_margin = float(pnl.sum() / rupee_years)
    maxloss = np.array([t.max_loss if t.max_loss != float("inf") else 0.0
                        for t in use], dtype=float)
    ml_years = float((maxloss * held).sum() / 365.0)
    s.max_loss_rupee_years = ml_years
    if ml_years > 0:
        s.annualised_return_on_max_loss = float(pnl.sum() / ml_years)
    s.mean_margin = float(margin.mean())
    s.peak_margin = float(peak.max()) if len(peak) else 0.0

    by_month: dict[str, float] = defaultdict(float)
    for t in use:
        d = t.exit_date or t.entry_date
        by_month[f"{d.year}-{d.month:02d}"] += t.net_pnl
    if by_month:
        lbl = min(by_month, key=lambda k: by_month[k])
        s.worst_month, s.worst_month_label = by_month[lbl], lbl

    s.mean_days_held = float(np.mean([t.days_held for t in use]))
    if pnl.std() > 0:
        s.skew = float(((pnl - pnl.mean()) ** 3).mean() / pnl.std() ** 3)

    reasons: dict[str, int] = defaultdict(int)
    for t in use:
        reasons[t.exit_reason] += 1
    s.exit_reasons = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
    return s


# --------------------------------------------------------------------------
# tail-event counting -- "how many disasters did the sample actually contain"
# --------------------------------------------------------------------------

def tail_events(index_closes: list[tuple[date, float]],
                gap_threshold: float = 0.03,
                big_threshold: float = 0.05) -> dict:
    """Count the days that decide a premium seller's outcome.

    A backtest with no such day in it has not tested the thing that decides the
    result.  The counts are reported on every summary, and a zero is a headline.
    """
    if len(index_closes) < 2:
        return {"sessions": 0}
    arr = np.array([c for _, c in index_closes], dtype=float)
    dates = [d for d, _ in index_closes]
    r = np.diff(np.log(arr))
    down = r < 0
    out = {
        "sessions": len(r),
        "down_moves_over_3pct": int((r < -gap_threshold).sum()),
        "down_moves_over_5pct": int((r < -big_threshold).sum()),
        "up_moves_over_3pct": int((r > gap_threshold).sum()),
        "worst_session": float(np.exp(r.min()) - 1),
        "worst_session_date": dates[int(np.argmin(r)) + 1].isoformat(),
        "best_session": float(np.exp(r.max()) - 1),
    }
    # worst 20-session stretch, the "slow crash" a daily gap filter misses
    if len(r) > 20:
        roll = np.convolve(r, np.ones(20), mode="valid")
        i = int(np.argmin(roll))
        out["worst_20_session_move"] = float(np.exp(roll[i]) - 1)
        out["worst_20_session_end"] = dates[i + 20].isoformat()
    return out


# --------------------------------------------------------------------------
# equity-curve metrics
# --------------------------------------------------------------------------

def equity_metrics(curve: list[tuple[date, float]]) -> dict:
    if len(curve) < 2:
        return {}
    vals = np.array([v for _, v in curve], dtype=float)
    dates = [d for d, _ in curve]
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)

    start, end = vals[0], vals[-1]
    cagr = (end / start) ** (1 / years) - 1 if start > 0 and end > 0 else float("nan")

    peak = np.maximum.accumulate(vals)
    dd = vals / peak - 1.0
    mdd = float(dd.min())
    i_trough = int(np.argmin(dd))

    rets = np.diff(vals) / np.maximum(vals[:-1], 1e-9)
    ann_vol = float(rets.std() * math.sqrt(TRADING_DAYS)) if len(rets) > 1 else 0.0
    sharpe = float(rets.mean() / rets.std() * math.sqrt(TRADING_DAYS)) \
        if len(rets) > 1 and rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = float(rets.mean() / downside.std() * math.sqrt(TRADING_DAYS)) \
        if len(downside) > 1 and downside.std() > 0 else 0.0

    return {
        "start": float(start), "end": float(end), "years": float(years),
        "cagr": float(cagr), "max_drawdown": mdd,
        "max_drawdown_date": dates[i_trough].isoformat(),
        "ann_vol": ann_vol, "sharpe": sharpe, "sortino": sortino,
        "mar": float(cagr / abs(mdd)) if mdd < 0 else float("nan"),
        "worst_day": float(rets.min()) if len(rets) else 0.0,
    }


# --------------------------------------------------------------------------
# block bootstrap, resampling WHOLE MONTHS
# --------------------------------------------------------------------------

def block_bootstrap(trades: list, n_boot: int = 10_000,
                    seed: int = 0) -> dict:
    """Resample whole CALENDAR MONTHS of trades, with replacement.

    Resampling individual trades would destroy the one thing that matters here:
    losses in this strategy arrive CLUSTERED, because a single adverse move hits
    every open position and the next one is sold into the same regime.  An
    independent-trade bootstrap treats a crash as many independent draws and
    reports a confidence interval far too tight.
    """
    done = [t for t in trades if t.exit_reason != "still_open_at_sample_end"]
    if not done:
        return {}
    by_month: dict[str, list[float]] = defaultdict(list)
    for t in done:
        d = t.exit_date or t.entry_date
        by_month[f"{d.year}-{d.month:02d}"].append(t.net_pnl)
    months = list(by_month.values())
    if len(months) < 2:
        return {}

    rng = np.random.default_rng(seed)
    n = len(months)
    totals = np.empty(n_boot)
    means = np.empty(n_boot)
    worsts = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        pnl = np.concatenate([months[j] for j in idx])
        totals[i] = pnl.sum()
        means[i] = pnl.mean()
        worsts[i] = pnl.min()

    observed = float(sum(t.net_pnl for t in done))
    return {
        "n_months": n,
        "n_boot": n_boot,
        "observed_total": observed,
        "total_p05": float(np.percentile(totals, 5)),
        "total_p50": float(np.percentile(totals, 50)),
        "total_p95": float(np.percentile(totals, 95)),
        "mean_p05": float(np.percentile(means, 5)),
        "mean_p50": float(np.percentile(means, 50)),
        "mean_p95": float(np.percentile(means, 95)),
        "prob_total_negative": float((totals < 0).mean()),
        "worst_trade_p05": float(np.percentile(worsts, 5)),
        "worst_trade_median": float(np.percentile(worsts, 50)),
    }


__all__ = ["trade_stats", "TradeStats", "tail_events", "equity_metrics",
           "block_bootstrap"]
