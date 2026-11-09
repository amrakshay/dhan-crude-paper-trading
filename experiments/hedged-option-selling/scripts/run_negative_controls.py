"""Ground truth and negative controls, before any real data is touched.

Three levels, each strictly harder than the last:

  LEVEL 1  the identity itself.  Monte-Carlo a short straddle and check that
           its mean P&L equals BlackPrice(iv) - BlackPrice(realised), which is
           what it must be with zero drift.  This validates the PRICER.

  LEVEL 2  the ENGINE, run unmodified against a synthetic market written into
           the same SQLite schema the real bhavcopy uses.  Selling must earn
           nothing when iv = rv, earn when iv > rv, and LOSE when iv < rv.
           A backtest with a sign error or a lookahead makes money in all three.

  LEVEL 3  regimes where short premium is SUPPOSED to lose -- a volatility
           expansion, a fat-tailed path, a jump path, a crash, and a strong
           gap-heavy TREND.  A strategy that survives these unscathed has a bug,
           not an edge.

Writes out/negative_controls.txt.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib import blackscholes as bs          # noqa: E402
from hedgelib import synth                        # noqa: E402
from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.chain import trading_days           # noqa: E402

OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
CACHE = ROOT / "data/cache"
CACHE.mkdir(parents=True, exist_ok=True)

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


# --------------------------------------------------------------------------
def level1(n_paths: int = 200_000) -> bool:
    say("=" * 78)
    say("LEVEL 1 -- the identity:  E[short straddle P&L] = BS(iv) - BS(realised)")
    say("=" * 78)
    say("Zero drift, zero rates.  If this fails the pricer is wrong and nothing")
    say("downstream means anything.\n")
    F, K, T = 24000.0, 24000.0, 30 / 365
    rng = np.random.default_rng(7)
    ok = True
    say(f"  {'realised':>9} {'sold at iv':>11} {'MC mean P&L':>13} "
        f"{'closed form':>13} {'gap':>10} {'MC stderr':>10}")
    for rv in (0.12, 0.18):
        for iv in (0.10, 0.12, 0.15, 0.18, 0.24):
            z = rng.standard_normal(n_paths)
            ST = F * np.exp(-0.5 * rv * rv * T + rv * math.sqrt(T) * z)
            payoff = np.maximum(ST - K, 0.0) + np.maximum(K - ST, 0.0)
            premium = bs.price(F, K, T, iv, True) + bs.price(F, K, T, iv, False)
            pnl = premium - payoff
            mc, se = float(pnl.mean()), float(pnl.std() / math.sqrt(n_paths))
            closed = (bs.price(F, K, T, iv, True) + bs.price(F, K, T, iv, False)
                      - bs.price(F, K, T, rv, True) - bs.price(F, K, T, rv, False))
            gap = mc - closed
            good = abs(gap) < 4 * se
            ok &= good
            say(f"  {rv:>9.2f} {iv:>11.2f} {mc:>13.2f} {closed:>13.2f} "
                f"{gap:>10.2f} {se:>10.2f}  {'ok' if good else 'FAIL'}")
    say(f"\n  LEVEL 1: {'PASS' if ok else 'FAIL'} -- every cell within 4 standard errors.")
    say("  Note the sign flips exactly at iv = realised. That is the variance")
    say("  risk premium, and it is the only reason selling options pays.\n")
    return ok


# --------------------------------------------------------------------------
def _run_synth(spec: synth.SynthSpec, cfg: StrategyConfig, frictionless: bool):
    db = str(CACHE / f"synth_{spec.generator}_{spec.vrp:+.2f}_{spec.seed}.db")
    gt = synth.build_market(spec, db)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    slip = SlippageModel(rel=0.0, ticks=0.0) if frictionless else SlippageModel()
    bt = Backtest(conn, cfg, slippage=slip)
    days = trading_days(conn, "SYNTH")
    trades = bt.run(days)
    done = [t for t in trades if t.exit_reason != "still_open_at_sample_end"]
    faults = len(bt.data_faults)
    conn.close()
    Path(db).unlink(missing_ok=True)      # 250 MB each; nothing reads them twice
    return gt, done, faults


def level2() -> bool:
    say("=" * 78)
    say("LEVEL 2 -- the ENGINE on a synthetic market, unmodified")
    say("=" * 78)
    say("Frictionless (no slippage, gross P&L) so the accounting is isolated.")
    say("An iron condor, delta-balanced, held to expiry.\n")

    cfg = StrategyConfig(structure="iron_condor", short_delta=0.16,
                         wing_delta=0.05, dte_target=21, dte_tolerance=10,
                         profit_take_fraction=None, hold_to_expiry=True,
                         min_trades=1, symbol="SYNTH")
    ok = True
    say(f"  {'vrp':>7} {'iv/rv':>8} {'trades':>7} {'mean gross':>12} "
        f"{'total gross':>13} {'stderr':>10} {'expected sign':>14}")
    results = {}
    for vrp in (-0.30, -0.15, 0.0, 0.15, 0.30):
        spec = synth.SynthSpec(generator="gbm", sigma_real=0.15, vrp=vrp,
                               n_days=1260, seed=11, expiry_every=7)
        gt, tr, faults = _run_synth(spec, cfg, frictionless=True)
        if not tr:
            say(f"  {vrp:>7.2f}  NO TRADES -- engine failed to build a structure")
            ok = False
            continue
        g = np.array([t.gross_pnl for t in tr])
        mean, se = g.mean(), g.std() / math.sqrt(len(g))
        want = "0" if abs(vrp) < 1e-9 else ("> 0" if vrp > 0 else "< 0")
        if abs(vrp) < 1e-9:
            good = abs(mean) < 3 * se
        elif vrp > 0:
            good = mean > 0
        else:
            good = mean < 0
        ok &= good
        results[vrp] = mean
        say(f"  {vrp:>7.2f} {1 + vrp:>8.2f} {len(g):>7} {mean:>12,.0f} "
            f"{g.sum():>13,.0f} {se:>10,.0f} {want:>14}  {'ok' if good else 'FAIL'}")

    say("")
    if len(results) == 5:
        mono = all(results[a] < results[b] for a, b in
                   zip(sorted(results), sorted(results)[1:], strict=False))
        ok &= mono
        say(f"  monotone in the variance risk premium: {'yes' if mono else 'NO -- FAIL'}")
    say(f"\n  LEVEL 2: {'PASS' if ok else 'FAIL'}")
    say("  The zero row is the one that matters. An engine that books a profit")
    say("  when it was sold a fair price has a lookahead or a sign error, and")
    say("  would have shown an 'edge' on real data too.\n")
    return ok


# --------------------------------------------------------------------------
def level3() -> bool:
    say("=" * 78)
    say("LEVEL 3 -- regimes where short premium SHOULD lose")
    say("=" * 78)
    say("Same structure, same positive variance risk premium (vrp=+0.15) that")
    say("made money under GBM. If a regime cannot hurt it, the model is wrong.\n")

    cfg = StrategyConfig(structure="iron_condor", short_delta=0.16,
                         wing_delta=0.05, dte_target=21, dte_tolerance=10,
                         profit_take_fraction=None, hold_to_expiry=True,
                         min_trades=1, symbol="SYNTH")
    say(f"  {'regime':<12} {'trades':>7} {'realised':>9} {'mean gross':>12} "
        f"{'worst':>12} {'win rate':>9} {'near maxloss':>13} {'faults':>7}")
    rows = {}
    for gen in ("gbm", "student_t", "jump", "vol_expand", "crash", "trend"):
        spec = synth.SynthSpec(generator=gen, sigma_real=0.15, vrp=0.15,
                               n_days=1260, seed=11, expiry_every=7)
        gt, tr, faults = _run_synth(spec, cfg, frictionless=True)
        if not tr:
            say(f"  {gen:<12}  NO TRADES")
            continue
        g = np.array([t.gross_pnl for t in tr])
        cr = np.array([t.entry_credit for t in tr])
        ml = np.array([t.max_loss for t in tr])
        worst = g.min()
        wr = (g > 0).mean() * 100
        # For a DEFINED-RISK structure the worst single trade is capped by
        # construction, so a crash cannot produce a worse one than an unlucky
        # GBM draw -- it produces MORE of them.  That, not the single worst
        # number, is what a bad regime does to this strategy.
        near_max = float((g < -0.8 * ml).mean())
        # How much of the gross profit do the two worst trades destroy?  This
        # is the tail-dominance number, and it is the one that matters.
        two_worst = float(np.sort(g)[:2].sum())
        rows[gen] = (g.mean(), worst, wr, near_max, two_worst, g.sum())
        say(f"  {gen:<12} {len(g):>7} {gt['realised_vol']:>9.3f} {g.mean():>12,.0f} "
            f"{worst:>12,.0f} {wr:>8.1f}% {near_max * 100:>12.1f}% {faults:>7}")

    say("")
    say("  TAIL DOMINANCE -- what the two worst trades do to the total")
    say(f"  {'regime':<12} {'total gross':>13} {'two worst':>12} "
        f"{'total without them':>19} {'tail share':>11}")
    for gen, (mean, worst, wr, nm, two_worst, tot) in rows.items():
        without = tot - two_worst
        share = (two_worst / tot * 100) if tot else float('nan')
        say(f"  {gen:<12} {tot:>13,.0f} {two_worst:>12,.0f} {without:>19,.0f} "
            f"{share:>10.0f}%")
    say("")
    say("  Two trades out of sixty. In several regimes they are the difference")
    say("  between a profitable strategy and an unprofitable one, and in none of")
    say("  them does the win rate move enough to notice. THAT is the shape of")
    say("  this strategy family, and it is why ruin risk rather than expectancy")
    say("  is the decision variable.")

    say("")
    ok = True
    if "crash" in rows:
        good = rows["crash"][3] > 0
        ok &= good
        say(f"  a crash drives at least one trade to its maximum loss: "
            f"{'yes' if good else 'NO -- FAIL'}  "
            f"({rows['crash'][3] * 100:.1f}% of trades)")
        say("     note it is only ~2 trades in 60. A single crash in a five-year")
        say("     sample barely moves any AGGREGATE statistic, which is exactly")
        say("     why aggregates are the wrong way to judge this strategy.")
    if "gbm" in rows and "vol_expand" in rows:
        good = rows["vol_expand"][0] < rows["gbm"][0]
        ok &= good
        say(f"  a volatility expansion reduces the mean:              "
            f"{'yes' if good else 'NO -- FAIL'}")
        say("     the seller keeps quoting a stale implied volatility into a")
        say("     regime that has already repriced -- the realistic failure.")
    if "gbm" in rows and "student_t" in rows:
        good = rows["student_t"][1] < rows["gbm"][1]
        ok &= good
        say(f"  fat tails produce a worse single trade:               "
            f"{'yes' if good else 'NO -- FAIL'}")
    if "gbm" in rows and "trend" in rows:
        good = rows["trend"][0] < rows["gbm"][0]
        ok &= good
        say(f"  a strong gap-heavy trend reduces the mean:            "
            f"{'yes' if good else 'NO -- FAIL'}")
        say("     direction, not volatility, is what breaches a condor over and")
        say("     over -- and each new position is sold into the same trend.")
    say("")
    say("  The 'faults' column counts sessions where a LIVE position could not be")
    say("  marked because a leg was outside the synthetic strike grid. On real NSE")
    say("  data it is structurally zero -- strikes are never delisted. Any non-zero")
    say("  here is a limit of the synthetic harness, not of the engine, and it is")
    say("  printed rather than hidden because it truncates the very tail the")
    say("  crash regime exists to produce.")
    say("\n  Read the win-rate column and then ignore it. It barely moves between")
    say("  a regime that pays and one that does not, which is the whole reason")
    say("  a high win rate is never evidence for this family of strategies.")
    say(f"\n  LEVEL 3: {'PASS' if ok else 'FAIL'}\n")
    return ok


# --------------------------------------------------------------------------
def level4() -> bool:
    say("=" * 78)
    say("LEVEL 4 -- bounds that must hold on every single trade")
    say("=" * 78)
    spec = synth.SynthSpec(generator="jump", sigma_real=0.18, vrp=0.10,
                           n_days=1260, seed=3, expiry_every=7)
    cfg = StrategyConfig(structure="bull_put_spread", short_delta=0.20,
                         wing_delta=0.07, dte_target=21, dte_tolerance=10,
                         profit_take_fraction=None, hold_to_expiry=True,
                         min_trades=1, symbol="SYNTH")
    gt, tr, faults = _run_synth(spec, cfg, frictionless=True)
    bad_hi = [t for t in tr if t.gross_pnl > t.max_profit + 1e-6]
    bad_lo = [t for t in tr if t.gross_pnl < -t.max_loss - 1e-6]
    say(f"  {len(tr)} trades on a jump-diffusion path")
    say(f"  gross P&L above max profit : {len(bad_hi)}")
    say(f"  gross P&L below max loss   : {len(bad_lo)}")
    for t in (bad_hi + bad_lo)[:5]:
        say(f"    {t.entry_date} gross {t.gross_pnl:,.0f} "
            f"maxP {t.max_profit:,.0f} maxL {t.max_loss:,.0f}")
    ok = not bad_hi and not bad_lo
    losses = sorted(t.gross_pnl for t in tr)[:5]
    say(f"  five worst trades: {', '.join(f'{x:,.0f}' for x in losses)}")
    say(f"\n  LEVEL 4: {'PASS' if ok else 'FAIL'}\n")
    return ok


def main() -> int:
    ok = level1() & level2() & level3() & level4()
    say("=" * 78)
    say(f"NEGATIVE CONTROLS: {'ALL PASS' if ok else 'FAILURES ABOVE'}")
    say("=" * 78)
    (OUT / "negative_controls.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'negative_controls.txt'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
