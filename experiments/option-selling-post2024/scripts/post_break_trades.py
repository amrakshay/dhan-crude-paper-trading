"""What is inside the bull put spread's post-2024-11-20 result? DESCRIPTIVE, on
data hedged-option-selling has already seen: no rule is changed, nothing here
is a test. Reruns that experiment's base rule (BULL_PUT.md, unchanged) through
its own engine, read-only, and lists every trade from the SEBI break on.

    cd experiments/option-selling-post2024 && ../hedged-option-selling/.venv/bin/python scripts/post_break_trades.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
HEDGED = HERE.parent / "hedged-option-selling"
sys.path.insert(0, str(HEDGED))

from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.chain import trading_days                                # noqa: E402

SEBI_BREAK = date(2024, 11, 20)
conn = sqlite3.connect(f"file:{HEDGED / 'data/cache/options.db'}?mode=ro", uri=True)
days = trading_days(conn, "NIFTY")
base = StrategyConfig()                      # the BULL_PUT.md base cell
trades = Backtest(conn, base, slippage=SlippageModel()).run(days)
post = [t for t in trades if t.entry_date >= SEBI_BREAK]
rows = []
for t in post:
    d = {k: getattr(t, k) for k in ("entry_date", "exit_date", "exit_reason", "net_pnl")
         if hasattr(t, k)}
    for k in ("credit", "max_loss", "lots", "lot_size", "short_strike", "long_strike",
              "expiry", "underlying_entry", "charges"):
        if hasattr(t, k):
            d[k] = getattr(t, k)
    rows.append(d)
print("fields available:", [k for k in vars(post[0])] if post else None)
tot = sum(t.net_pnl for t in post)
srt = sorted(post, key=lambda t: t.net_pnl)
print(f"post-break trades {len(post)}  total {tot:,.0f}  wins {sum(t.net_pnl > 0 for t in post)}")
print("worst 5:", [(str(t.entry_date), str(getattr(t, 'exit_date', '')), round(t.net_pnl)) for t in srt[:5]])
print("best 3:", [(str(t.entry_date), round(t.net_pnl)) for t in srt[-3:]])
print("total excluding worst 1 / 2 / 3:", [round(tot - sum(x.net_pnl for x in srt[:n])) for n in (1, 2, 3)])
(HERE / "out" / "post_break_trades.json").write_text(json.dumps(rows, indent=1, default=str))


# --- before vs after: did the trade's economics change? -----------------------
import statistics as S  # noqa: E402


def econ(ts):
    w = [t.net_pnl for t in ts if t.net_pnl > 0]
    l = [t.net_pnl for t in ts if t.net_pnl <= 0]
    cr = [t.entry_credit / t.max_loss for t in ts if t.max_loss]
    return {
        "trades": len(ts), "win_rate": round(len(w) / len(ts), 3),
        "avg_win": round(S.mean(w)) if w else None, "avg_loss": round(S.mean(l)) if l else None,
        "breakeven_win_rate": round(-S.mean(l) / (S.mean(w) - S.mean(l)), 3) if w and l else None,
        "median_credit_over_max_loss": round(S.median(cr), 4),
        "median_short_iv": round(S.median(t.entry_short_iv for t in ts), 4),
        "median_days_held": S.median(t.days_held for t in ts),
        "median_charges": round(S.median(t.charges for t in ts)),
        "median_lot_size": S.median(t.lot_size for t in ts),
        "exit_reasons": {r: sum(t.exit_reason == r for t in ts) for r in {t.exit_reason for t in ts}},
    }


pre = [t for t in trades if t.entry_date < SEBI_BREAK]
out = {"pre": econ(pre), "post": econ(post),
       "last_trade": {"entry": str(post[-1].entry_date), "exit": str(post[-1].exit_date),
                      "reason": post[-1].exit_reason, "net": round(post[-1].net_pnl)}}
print(json.dumps(out, indent=1, default=str))
(HERE / "out" / "pre_post_economics.json").write_text(json.dumps(out, indent=1, default=str))


# --- is the post-break result consistent with the pre-break distribution? -----
import numpy as np  # noqa: E402
from scipy import stats as st  # noqa: E402

closed = [t for t in post if t.exit_reason != "still_open_at_sample_end"]
r_pre = np.array([t.net_pnl / t.max_loss for t in pre])      # return on max loss, scale-free
r_post = np.array([t.net_pnl / t.max_loss for t in closed])
p_loss_pre = float((r_pre <= 0).mean())
k_post = int((r_post <= 0).sum())
binom_p = float(st.binom.sf(k_post - 1, len(r_post), p_loss_pre))     # P(X >= k)
rng = np.random.default_rng(0)
boot = rng.choice(r_pre, size=(20000, len(r_post)), replace=True).mean(axis=1)
p_mean = float((boot <= r_post.mean()).mean())
# Trades needed to tell the pre-break win rate from break-even (two-sided 5%, 80% power).
p1, p0 = 1 - p_loss_pre, out["pre"]["breakeven_win_rate"]
n_needed = int(np.ceil(((1.96 * np.sqrt(p0 * (1 - p0)) + 0.84 * np.sqrt(p1 * (1 - p1))) / (p1 - p0)) ** 2))
per_month = len(pre) / ((SEBI_BREAK - pre[0].entry_date).days / 30.44)
res = {
    "pre_loss_rate": round(p_loss_pre, 4), "post_losses": k_post, "post_trades": len(r_post),
    "binomial_p_at_least_that_many_losses": round(binom_p, 3),
    "pre_mean_return_on_max_loss": round(float(r_pre.mean()), 4),
    "post_mean_return_on_max_loss": round(float(r_post.mean()), 4),
    "bootstrap_p_pre_draws_this_bad_or_worse": round(p_mean, 3),
    "trades_needed_to_separate_pre_edge_from_breakeven": n_needed,
    "trades_per_month_historically": round(per_month, 2),
    "years_of_forward_trading_needed": round(n_needed / per_month / 12, 1),
}
print(json.dumps(res, indent=1))
(HERE / "out" / "post_break_consistency.json").write_text(json.dumps(res, indent=1))
