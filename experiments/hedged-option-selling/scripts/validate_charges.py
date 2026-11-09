"""Assert the charge card against independently hand-computed worked examples.

The repo's rule is that a divergence is REPORTED, never tuned away.  Each case
below states the expected figure and where it comes from; the script prints the
gap and exits non-zero only if a case is outside its stated tolerance.

The reference is the PUBLISHED FORMULA, not a live broker calculator run:
Zerodha's calculator endpoint sits behind a Cloudflare bot challenge and
working around bot detection is out of bounds.  That is a weaker check in one
respect and a stronger one in another -- every statutory rate here is sourced
from an NSE circular or the Finance Act directly, rather than from a broker's
interpretation of them.
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hedgelib.charges import ChargeCard, Leg  # noqa: E402

D = Decimal


def show(title: str, got: Decimal, want: Decimal, tol: Decimal, detail: str) -> bool:
    gap = got - want
    ok = abs(gap) <= tol
    print(f"\n{title}")
    print(f"  {detail}")
    print(f"  computed  {got:>12.4f}")
    print(f"  expected  {want:>12.4f}   (tolerance {tol})")
    print(f"  gap       {gap:>12.4f}   {'OK' if ok else '*** DIVERGENCE ***'}")
    return ok


def main() -> int:
    card = ChargeCard()
    print(f"charge card version {card.version}")
    print(f"source: {card.path}")
    ok = True

    # ---------------------------------------------------------------- case 1
    # A single short NIFTY option, sold and bought back, in the CURRENT regime.
    # Hand computation, post-2026-04-01:
    #   sell 1 lot (75) @ 100  -> turnover 7,500
    #   buy  1 lot (75) @  40  -> turnover 3,000
    #   brokerage      20 x 2                         =  40.0000
    #   STT            0.0015 x 7500                  =  11.2500
    #   txn charge     0.0003553 x 10500              =   3.73065
    #   SEBI fee       0.000001 x 10500               =   0.01050
    #   stamp duty     0.00003 x 3000                 =   0.09000
    #   GST            0.18 x (40 + 3.73065 + 0.0105) =   7.87343
    #                                            total =  62.9546 -> 62.95
    d = date(2026, 6, 15)
    a = card.charge_trade([Leg("SELL", D("100"), 75)], d)
    b = card.charge_trade([Leg("BUY", D("40"), 75)], d)
    tot = (a + b).total
    ok &= show(
        "case 1  short option round trip, current STT regime (2026-06-15)",
        tot, D("62.95"), D("0.01"),
        "sell 75 @ 100, buy back 75 @ 40; 2 executed orders")

    # ---------------------------------------------------------------- case 2
    # The SAME trade under each STT regime, to show the card is date-aware and
    # to size the structural break the handoff asked to be treated as one.
    print("\n\ncase 2  the identical trade across all five STT regimes")
    print("  only the DATE changes.  Shown at 1 lot and at 40 lots, because")
    print("  which charge dominates depends entirely on size.\n")
    dates = [date(2016, 1, 15), date(2016, 7, 15), date(2023, 6, 15),
             date(2025, 1, 15), date(2026, 6, 15)]

    for lots in (1, 40):
        qty = 75 * lots
        print(f"  --- {lots} lot{'s' if lots > 1 else ''} "
              f"({qty} units, premium sold Rs {100 * qty:,}) ---")
        print(f"  {'date':<12} {'STT rate':>10} {'STT Rs':>10} {'brokerage':>10} "
              f"{'total Rs':>10} {'% of premium':>13}")
        rows = []
        for dd in dates:
            r = card.rate("stt.sell_option_premium_rate", dd)
            x = (card.charge_trade([Leg("SELL", D("100"), qty)], dd)
                 + card.charge_trade([Leg("BUY", D("40"), qty)], dd))
            rows.append(x)
            print(f"  {dd.isoformat():<12} {float(r) * 100:>9.4f}% "
                  f"{float(x.stt):>10.2f} {float(x.brokerage):>10.2f} "
                  f"{float(x.total):>10.2f} "
                  f"{float(x.total / (D('100') * qty) * 100):>12.3f}%")
        print(f"  STT multiple 2016-01 -> 2026-06: "
              f"{float(rows[-1].stt / rows[0].stt):.2f}x     "
              f"TOTAL cost multiple: {float(rows[-1].total / rows[0].total):.2f}x\n")

    print("  The STT itself rose ~8.8x. The TOTAL barely moves at one lot,")
    print("  because flat Rs 20 brokerage swamps it, and moves almost as much")
    print("  as the STT at forty. The Oct-2024 and Apr-2026 breaks are therefore")
    print("  structural for a sized book and nearly invisible for a toy one -- a")
    print("  backtest at one lot would under-report the break it means to measure.")

    # ---------------------------------------------------------------- case 3
    # A four-legged iron condor.  Brokerage scales with LEG COUNT, which is the
    # cost a two-leg-vs-four-leg comparison must not hide.
    d = date(2026, 6, 15)
    condor_in = card.charge_trade([
        Leg("SELL", D("120"), 75), Leg("BUY", D("55"), 75),
        Leg("SELL", D("115"), 75), Leg("BUY", D("50"), 75)], d)
    vertical_in = card.charge_trade([
        Leg("SELL", D("120"), 75), Leg("BUY", D("55"), 75)], d)
    print("\n\ncase 3  leg count is a real cost")
    print(f"  4-leg condor, one side (entry only): brokerage "
          f"{float(condor_in.brokerage):.2f}, total {float(condor_in.total):.2f}")
    print(f"  2-leg vertical, entry only         : brokerage "
          f"{float(vertical_in.brokerage):.2f}, total {float(vertical_in.total):.2f}")
    ok &= show("  condor entry brokerage is 4 orders", condor_in.brokerage,
               D("80"), D("0.001"), "Rs 20 x 4 legs")

    # ---------------------------------------------------------------- case 4
    # Expiry is NOT a transaction.  A spread allowed to expire worthless costs
    # nothing; a long wing finishing ITM pays STT on INTRINSIC.
    d = date(2026, 6, 25)
    worthless = card.charge_expiry(
        [Leg("SELL", D("0"), 75), Leg("BUY", D("0"), 75)],
        [D("0"), D("0")], d)
    ok &= show("\ncase 4a  spread expires worthless", worthless.total,
               D("0.00"), D("0.001"),
               "no sale, no exercise -> no brokerage, no STT, no GST")

    # Long 24000 put, index settles at 23000 -> intrinsic 1000 on 75 units.
    itm = card.charge_expiry(
        [Leg("SELL", D("0"), 75), Leg("BUY", D("0"), 75)],
        [D("0"), D("1000")], d)
    # 0.0015 x 1000 x 75 = 112.50
    ok &= show("case 4b  long wing exercised deep ITM", itm.total,
               D("112.50"), D("0.01"),
               "intrinsic 1000 x 75 units @ 0.15% -- charged on INTRINSIC, "
               "not premium")
    print("\n  Note the size of 4b. On a disaster trade the exercise STT on the")
    print("  wing alone can exceed every other charge on the position combined,")
    print("  which is exactly the case a backtest that omits it flatters.")

    # ---------------------------------------------------------------- case 5
    # Charges as a fraction of the CREDIT, which is the denominator that
    # actually decides whether premium selling survives its own costs.
    print("\n\ncase 5  charges against the credit received, current regime")
    print(f"  {'credit/unit':>12} {'gross credit':>13} {'charges':>9} {'% of credit':>12}")
    for credit in [D("10"), D("25"), D("50"), D("100"), D("200")]:
        # a vertical: sell at `credit`+wing cost, buy wing at 40% of it
        short_p, long_p = credit * 2, credit
        entry = card.charge_trade(
            [Leg("SELL", short_p, 75), Leg("BUY", long_p, 75)], date(2026, 6, 15))
        gross = (short_p - long_p) * 75
        print(f"  {float(credit):>12.2f} {float(gross):>13.2f} "
              f"{float(entry.total):>9.2f} {float(entry.total / gross * 100):>11.2f}%")
    print("\n  Entry charges alone. A spread sold for a small credit can lose a")
    print("  double-digit percentage of it before the market moves at all --")
    print("  which is why 'sell far OTM for a tiny premium' fails on costs.")

    print("\n" + ("ALL CASES WITHIN TOLERANCE" if ok else "DIVERGENCES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
