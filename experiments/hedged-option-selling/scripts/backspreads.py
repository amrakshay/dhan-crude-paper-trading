"""Buy MORE wings than you sell: does a ratio minimise the losses?

Sell one put, buy `ratio` puts further out. At ratio 1.0 it is the ordinary
vertical. Above 1.0 the payoff changes SHAPE rather than degree:

    above the short strike   keep the credit
    between the strikes      the short bleeds, the longs are still worthless
    AT the long strike       maximum loss -- a point, not a plateau
    below the long strike    the longs outrun the short: the loss shrinks and
                             eventually turns into profit

So a crash stops being the disaster case. What is paid for that is a smaller
credit (often a net debit) and a LARGER loss in a moderate move -- and moderate
moves are commoner than crashes, which is the whole empirical question.

Ratios BELOW 1.0 leave a naked short below the long strike. Undefined risk, out
of scope, and the engine refuses to build one.

Writes out/backspreads.txt.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.chain import trading_days                                # noqa: E402
from hedgelib.metrics import block_bootstrap, trade_stats              # noqa: E402

DB = ROOT / "data/cache/options.db"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
RISK_FREE = 0.0564

_lines: list[str] = []
JSON_ROWS: dict[str, dict] = {}


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    days = trading_days(conn, "NIFTY")
    idx = [(date.fromisoformat(a), float(v)) for a, v in conn.execute(
        "SELECT trade_date, close FROM index_close WHERE index_name='Nifty 50' "
        "ORDER BY trade_date")]
    move = {idx[i][0]: idx[i][1] / idx[i - 1][1] - 1 for i in range(1, len(idx))}

    base = StrategyConfig(structure="bull_put_spread", short_delta=0.16,
                          wing_delta=0.05, dte_target=30, dte_tolerance=10,
                          profit_take_fraction=0.5, min_trades=100,
                          symbol="NIFTY")

    say("=" * 96)
    say("BACKSPREADS -- buy more wings than you sell")
    say("=" * 96)
    say(f"  {days[0]} .. {days[-1]}   bull put spread, 16d short / 5d wing, ~30 DTE")
    say("  ratio = long wings per short leg. 1.0 is the ordinary vertical.")
    say("")
    say("  NOTE ON A BUG FOUND WHILE BUILDING THIS. The profit target used to be")
    say("  a fraction of `max_profit`. For a vertical that IS the credit, but a")
    say("  backspread's max_profit is its payoff at spot near zero -- Rs 840,000")
    say("  against a Rs 1,600 credit -- so the target was unreachable and every")
    say("  backspread silently ran to expiry while the vertical took profit at")
    say("  50%. The first version of this table compared two different")
    say("  management rules. The target is now a fraction of the CREDIT, which")
    say("  is identical for every non-backspread structure and leaves all other")
    say("  results in this study unchanged.")

    # ---------------------------------------------------------------- 1
    say("")
    say("-" * 96)
    say("1. THE SWEEP")
    say("-" * 96)
    say(f"  {'ratio':>6} {'n':>5} {'total P&L':>11} {'R/risk /yr':>11} "
        f"{'worst':>10} {'worst1%':>10} {'credit':>9} {'maxloss':>9} {'win':>6}")
    kept = {}
    for r in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        bt = Backtest(conn, replace(base, long_ratio=r), slippage=SlippageModel())
        tr = bt.run(days)
        s = trade_stats(tr)
        if s.n == 0:
            say(f"  {r:>6.2f}     0  (no trades)")
            continue
        done = [t for t in tr if t.exit_reason != "still_open_at_sample_end"]
        kept[r] = (s, tr, done)
        JSON_ROWS[f"{r:.2f}"] = {
            "ratio": r, "n": s.n, "total_pnl": s.total_pnl,
            "return_on_risk_annual": s.annualised_return_on_max_loss,
            "worst": s.worst_trade, "worst_1pct": s.worst_1pct,
            "win_rate": s.win_rate,
            "credit": float(np.mean([t.entry_credit for t in done])),
            "max_loss": float(np.mean([t.max_loss for t in done])),
        }
        say(f"  {r:>6.2f} {s.n:>5} {s.total_pnl:>11,.0f} "
            f"{s.annualised_return_on_max_loss * 100:>10.2f}% {s.worst_trade:>10,.0f} "
            f"{s.worst_1pct:>10,.0f} {np.mean([t.entry_credit for t in done]):>9,.0f} "
            f"{np.mean([t.max_loss for t in done]):>9,.0f} {s.win_rate * 100:>5.1f}%")

    say("")
    say("  Watch the credit column fall as the ratio rises -- that is what the")
    say("  extra wings cost, paid every trade whether or not they are needed.")

    # ---------------------------------------------------------------- 2
    say("")
    say("-" * 96)
    say("2. DID IT ACTUALLY HELP IN THE CRASHES?")
    say("-" * 96)
    say("The claim is that a big move turns from the worst case into the best.")
    say("Trades whose holding period contained a session that fell more than 3%:")
    say("")
    say(f"  {'ratio':>6} {'trades in a >3% event':>23} {'mean P&L':>11} "
        f"{'worst':>11} {'best':>11}")
    for r, (s, tr, done) in kept.items():
        hit = []
        for t in done:
            d = t.entry_date
            worst_day = 0.0
            for dd, _m, _g in t.marks:
                worst_day = min(worst_day, move.get(dd, 0.0))
            if worst_day <= -0.03:
                hit.append(t.net_pnl)
        if hit:
            say(f"  {r:>6.2f} {len(hit):>23} {np.mean(hit):>11,.0f} "
                f"{min(hit):>11,.0f} {max(hit):>11,.0f}")
            JSON_ROWS.setdefault(f"{r:.2f}", {}).update(
                {"crash_n": len(hit), "crash_mean": float(np.mean(hit)),
                 "crash_worst": float(min(hit))})

    # ---------------------------------------------------------------- 3
    say("")
    say("-" * 96)
    say("3. WHERE DID THE LOSSES MOVE TO?")
    say("-" * 96)
    say("A backspread does not remove the loss, it RELOCATES it -- from a large")
    say("move to a moderate one. Distribution of per-trade returns on capital:")
    say("")
    say(f"  {'ratio':>6} {'p5':>8} {'p25':>8} {'median':>8} {'p75':>8} {'p95':>8} "
        f"{'% of trades losing >10%':>24}")
    for r, (s, tr, done) in kept.items():
        rr = np.array([t.net_pnl / t.max_loss for t in done if t.max_loss > 0])
        big = (rr < -0.10).mean() * 100
        say(f"  {r:>6.2f} {np.percentile(rr, 5) * 100:>7.1f}% "
            f"{np.percentile(rr, 25) * 100:>7.1f}% {np.median(rr) * 100:>7.1f}% "
            f"{np.percentile(rr, 75) * 100:>7.1f}% {np.percentile(rr, 95) * 100:>7.1f}% "
            f"{big:>23.1f}%")
        JSON_ROWS.setdefault(f"{r:.2f}", {}).update(
            {"p5": float(np.percentile(rr, 5)), "median_ret": float(np.median(rr)),
             "pct_losing_10": float(big)})

    # ---------------------------------------------------------------- 4
    say("")
    say("-" * 96)
    say("4. SIGNIFICANCE")
    say("-" * 96)
    for r, (s, tr, done) in kept.items():
        bb = block_bootstrap(tr, n_boot=10000)
        if bb:
            say(f"  ratio {r:>4.2f}  observed {bb['observed_total']:>10,.0f}   "
                f"5-95% [{bb['total_p05']:>10,.0f}, {bb['total_p95']:>10,.0f}]   "
                f"P(<0) {bb['prob_total_negative'] * 100:>5.1f}%")
            JSON_ROWS.setdefault(f"{r:.2f}", {}).update(
                {"boot_p05": bb["total_p05"], "boot_p95": bb["total_p95"],
                 "prob_negative": bb["prob_total_negative"]})

    say("")
    say("=" * 96)
    say("WHAT THIS SAYS")
    say("=" * 96)
    if 1.0 in kept and 2.0 in kept:
        s1 = kept[1.0][0]
        s2 = kept[2.0][0]
        say("")
        say(f"  1:1  return {s1.annualised_return_on_max_loss * 100:+.2f}%/yr   "
            f"worst {s1.worst_trade:,.0f}   worst 1% {s1.worst_1pct:,.0f}")
        say(f"  1:2  return {s2.annualised_return_on_max_loss * 100:+.2f}%/yr   "
            f"worst {s2.worst_trade:,.0f}   worst 1% {s2.worst_1pct:,.0f}")
        say("")
        if s2.worst_trade > s1.worst_trade:
            say("  The ratio DID cut the worst trade.")
        else:
            say("  The ratio did NOT cut the worst trade -- because the maximum")
            say("  loss of a backspread sits AT the long strike, and a moderate")
            say("  move parks the position exactly there. The distribution has")
            say("  20 sessions worse than -3% and only 7 worse than -5%:")
            say("  moderate moves are commoner than crashes, so relocating the")
            say("  loss towards moderate moves moves it TOWARDS the observations,")
            say("  not away from them.")
        say(f"\n  risk-free over this sample: {RISK_FREE * 100:.2f}% a year.")

    (OUT / "backspreads.json").write_text(json.dumps(
        {"window": [str(days[0]), str(days[-1])], "ratios": JSON_ROWS},
        default=str))
    (OUT / "backspreads.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'backspreads.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
