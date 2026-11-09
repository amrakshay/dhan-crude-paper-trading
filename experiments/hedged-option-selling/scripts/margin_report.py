"""The margin model: what it does, what it is calibrated against, and how much
the answer moves with each thing that could not be read off a published rule.

Return on MARGIN is this study's metric, so the margin model is not a detail --
it is the denominator.  Every assumption in it is swept here rather than
asserted, and the one that moves the answer most is named.

Writes out/margin_report.txt.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib import blackscholes as bs                            # noqa: E402
from hedgelib.backtest import Backtest, SlippageModel, StrategyConfig  # noqa: E402
from hedgelib.chain import trading_days                            # noqa: E402
from hedgelib.margin import MarginModel, PosLeg                    # noqa: E402
from hedgelib.metrics import trade_stats                           # noqa: E402

DB = ROOT / "data/cache/options.db"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


def reference_spread(m: MarginModel, netting: bool):
    """The calibration case: NIFTY 500-wide bull put, 1 lot, 30 DTE."""
    F, t, sig, lot = 24500.0, 30 / 365, 0.0085, 75
    on, exp = date(2025, 8, 1), date(2025, 8, 28)
    sp = PosLeg(24000, False, -lot, 0.135, bs.price(F, 24000, t, 0.135, False))
    lp = PosLeg(23500, False, lot, 0.150, bs.price(F, 23500, t, 0.150, False))
    m.cfg["extreme_loss_margin"]["net_long_against_short"] = netting
    naked = m.compute([sp], F, sig, t, on, exp)
    hedged = m.compute([sp, lp], F, sig, t, on, exp)
    credit = (sp.price - lp.price) * lot
    max_loss = 500 * lot - credit
    return naked, hedged, credit, max_loss


def main() -> int:
    say("=" * 78)
    say("MARGIN MODEL -- a SPAN-shaped approximation on NSE's own daily sigma")
    say("=" * 78)
    m = MarginModel()
    say(f"  model version {m.version}")
    say(f"  source        {m.path}")
    say("")
    say("  It is NOT real SPAN, and cannot be: NSE's historical par files are")
    say("  not archived (404 at every path probed on 2026-09-20), so no")
    say("  historical backtest by anyone can use the exchange's actual number.")
    say("")
    say("  What makes it defensible rather than invented is that the price scan")
    say("  range is driven by NSE's OWN published daily volatility (the FOVOLT")
    say("  file, available 2016-01-04 .. 2026-09-17), and that BOTH LEGS are")
    say("  repriced under every scenario -- so the wing's margin relief is")
    say("  measured, not assumed.")

    # ---------------------------------------------------------------- 1
    say("")
    say("=" * 78)
    say("1. THE CALIBRATION -- does ELM net a long against a short?")
    say("=" * 78)
    say("The NCL FAQ says ELM 'shall be netted at client and prop levels' and")
    say("does not say whether a long option at a DIFFERENT strike counts. Both")
    say("readings are implemented. This is the single assumption in the study")
    say("most likely to be wrong.")
    say("")
    say("  NIFTY 500-wide bull put spread, 1 lot, F=24,500, 30 DTE, sigma 0.0085")
    say("")
    say(f"  {'reading':<18} {'scan risk':>11} {'ELM':>11} {'margin':>11} "
        f"{'vs naked':>9} {'vs max loss':>12}")
    for netting, label in ((False, "gross"), (True, "netted")):
        n, h, credit, ml = reference_spread(m, netting)
        say(f"  {label:<18} {h.scan_risk:>11,.0f} {h.elm:>11,.0f} {h.total:>11,.0f} "
            f"{h.total / n.total * 100:>8.1f}% {h.total / ml:>11.2f}x")
    say("")
    say("  Brokers report hedged index spreads at Rs 15,000-30,000 per lot and at")
    say("  'roughly a quarter to a third of the margin a naked short needs'.")
    say("  The NETTED reading lands inside both bands; the gross reading is")
    say("  roughly double the top of each. Netted is used, as a CALIBRATION to")
    say("  reported figures rather than a reading of the rule.")
    n, h, credit, ml = reference_spread(m, True)
    say(f"\n  For reference, the naked requirement: Rs {n.total:,.0f} "
        f"(scan {n.scan_risk:,.0f} + ELM {n.elm:,.0f})")
    say(f"  and the worst SPAN scenario for the hedged spread: {h.worst_scenario_desc}")

    # ---------------------------------------------------------------- 2
    say("")
    say("=" * 78)
    say("2. THE MODEL RESPONDS TO VOLATILITY -- which a flat model does not")
    say("=" * 78)
    say("A percentage-of-notional margin holds constant through a crisis, when")
    say("the real requirement expands exactly as the position is losing. That")
    say("error flatters the strategy precisely where it is most dangerous.")
    say("")
    say(f"  {'daily sigma':>12} {'annualised':>11} {'scan range':>12} "
        f"{'margin':>11} {'vs calm':>9}")
    base = None
    for s in (0.0050, 0.0085, 0.0120, 0.0200, 0.0300, 0.0500):
        _, h2, _, _ = reference_spread(m, True)
        F, t, lot = 24500.0, 30 / 365, 75
        sp = PosLeg(24000, False, -lot, 0.135, bs.price(F, 24000, t, 0.135, False))
        lp = PosLeg(23500, False, lot, 0.150, bs.price(F, 23500, t, 0.150, False))
        r = m.compute([sp, lp], F, s, t, date(2025, 8, 1), date(2025, 8, 28))
        base = base or r.total
        say(f"  {s:>12.4f} {s * (365 ** 0.5) * 100:>10.1f}% "
            f"{r.price_scan_range:>12,.0f} {r.total:>11,.0f} {r.total / base:>8.2f}x")
    say("")
    say("  NIFTY's typical daily sigma is around 0.0085 (16% annualised), which")
    say("  sits BELOW the scan-range floor, so in calm markets the floor binds and")
    say("  margin is flat. It only starts to expand above roughly 1.7% daily")
    say("  (32% annualised) -- which is a real property of the rule, not a")
    say("  modelling shortcut, and it means margin relief arrives late.")

    # ---------------------------------------------------------------- 3
    say("")
    say("=" * 78)
    say("3. SENSITIVITY -- how much does the backtest move with each assumption?")
    say("=" * 78)
    if not DB.exists():
        say("  (cache absent -- skipping the backtest sensitivity)")
    else:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        days = trading_days(conn, "NIFTY")
        cfg = StrategyConfig(structure="iron_condor", short_delta=0.16,
                             wing_delta=0.05, dte_target=30, dte_tolerance=10,
                             profit_take_fraction=0.5, min_trades=100,
                             symbol="NIFTY")
        say(f"  sample {days[0]} .. {days[-1]}")
        say("")
        say(f"  {'variant':<28} {'n':>4} {'mean margin':>12} "
            f"{'RoM total':>10} {'maxL/margin':>12}")

        def run(label, mutate):
            mm = MarginModel()
            mutate(mm)
            bt = Backtest(conn, cfg, margin=mm, slippage=SlippageModel())
            s = trade_stats(bt.run(days))
            say(f"  {label:<28} {s.n:>4} {s.mean_margin:>12,.0f} "
                f"{s.total_return_on_margin * 100:>9.2f}% "
                f"{s.max_loss_per_margin * 100:>11.1f}%")
            return s

        run("BASE (netted ELM)", lambda x: None)
        run("ELM gross, not netted",
            lambda x: x.cfg["extreme_loss_margin"].__setitem__(
                "net_long_against_short", False))
        for mult in (2.0, 3.0, 6.0):
            run(f"scan range {mult:g} sigma",
                lambda x, mu=mult: x.cfg["price_scan_range"]["index"].__setitem__(
                    "sigma_multiple", mu))
        for fl in (0.03, 0.05, 0.093):
            run(f"scan floor {fl * 100:.1f}% of spot",
                lambda x, f=fl: x.cfg["price_scan_range"]["index"].__setitem__(
                    "minimum_fraction_of_underlying", f))
        for v in (0.05, 0.10, 0.25):
            run(f"vol scan range {v * 100:.0f}%",
                lambda x, vv=v: x.cfg["volatility_scan_range"].__setitem__(
                    "relative", vv))
        run("ELM on strike, not forward",
            lambda x: x.cfg["extreme_loss_margin"].__setitem__(
                "notional_basis", "strike"))
        say("")
        say("  The ELM netting row is the one to look at. Everything else moves")
        say("  the denominator by tens of percent; that assumption moves it by a")
        say("  factor, and it is the one with no published rule behind it.")

    (OUT / "margin_report.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'margin_report.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
