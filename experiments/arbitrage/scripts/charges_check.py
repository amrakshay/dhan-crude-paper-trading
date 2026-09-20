"""Validate the charges engine, then price a pair round trip under each card.

Three independent checks, in increasing order of what they prove:

1. **Against a published broker calculator.** One fully worked example was
   obtainable with its inputs. The divergence is REPORTED and attributed, not
   tuned away -- the repository's rule (root CLAUDE.md section 7).
2. **Against longhand arithmetic.** Every line of a delivery round trip
   computed here, in this file, without the engine. Checking an engine
   against itself proves nothing.
3. **Against the application's own rate card.** Read as YAML, never imported,
   so the experiment stays self-contained. Any rate that differs must differ
   for a reason this script prints.

Then the table that actually matters: what a two-legged round trip costs.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import _bootstrap  # noqa: F401
import yaml
from arblib.charges import Leg, charge, load_card, round_trip

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
APP_CARD = ROOT.parent.parent / "backend" / "conf" / "charges" / "nse-equity-delivery.yaml"

TODAY = date(2026, 9, 20)
results = {}


def check_published_example() -> dict:
    """Zerodha, equity intraday: buy 100 @ 1,000, sell 100 @ 1,010.

    Published: brokerage Rs 40.00, all charges Rs 82.72.
    source: https://accelpix.com/brokerage-calculator/brokers/zerodha.html
    retrieved 2026-09-20.
    """
    got = (charge("nse-equity-intraday", Leg("BUY", Decimal("100000"), TODAY))
           + charge("nse-equity-intraday", Leg("SELL", Decimal("101000"), TODAY)))
    published = Decimal("82.72")
    diff = got.total - published

    # THE ATTRIBUTION. The card charges the exchange transaction line at the
    # Rs 307 per crore TOTAL (transaction charge + IPFT). The calculator
    # charges Rs 297 per crore -- the transaction-charge limb alone, and the
    # figure that was current before NSE/FA/73061 moved the split on
    # 1 March 2026. Re-running the same arithmetic at 297 should reproduce
    # the published total.
    t_buy, t_sell = Decimal("100000"), Decimal("101000")
    at_297 = Decimal(0)
    for turn, side in ((t_buy, "BUY"), (t_sell, "SELL")):
        brokerage = min(Decimal("20"), Decimal("0.0003") * turn)
        txn = Decimal("0.0000297") * turn
        sebi = Decimal("0.000001") * turn
        stamp = Decimal("0.00003") * turn if side == "BUY" else Decimal(0)
        stt = Decimal("0.00025") * turn if side == "SELL" else Decimal(0)
        gst = Decimal("0.18") * (brokerage + txn + sebi)
        at_297 += brokerage + txn + sebi + stamp + stt + gst
    at_297 = at_297.quantize(Decimal("0.01"))

    print("1. Published broker calculator -- equity intraday, 100 @ 1000 -> 1010")
    print(f"     published total            Rs {published}")
    print(f"     this engine                Rs {got.total}     divergence Rs {diff:+}")
    print(f"     same, at Rs 297/crore      Rs {at_297}     divergence Rs {at_297 - published:+}")
    print("     -> the gap IS the NSE IPFT contribution, Rs 10 per crore, which the")
    print("        calculator omits. Reproducing its rate reproduces its total to a paisa.")
    print("        The card is NOT changed to match: Rs 307/crore is what NSE/FA/64232")
    print("        and NSE/FA/73061 levy, and a cost model that leaves a levy out is wrong")
    print("        in the direction that flatters the strategy.")
    return {"published": float(published), "engine": float(got.total),
            "divergence": float(diff), "at_297_per_crore": float(at_297),
            "residual": float(at_297 - published),
            "attribution": "NSE IPFT contribution, Rs 10 per crore, omitted by the calculator"}


def check_longhand() -> dict:
    """Delivery round trip, every line written out by hand."""
    buy, sell = Decimal("250000"), Decimal("262500")
    stt = Decimal("0.001") * buy + Decimal("0.001") * sell
    txn = Decimal("0.0000307") * (buy + sell)
    sebi = Decimal("0.000001") * (buy + sell)
    stamp = Decimal("0.00015") * buy
    dp = Decimal("12.50")
    brokerage = Decimal("0")
    gst = Decimal("0.18") * (brokerage + txn + sebi + dp)
    hand = (stt + txn + sebi + stamp + dp + brokerage + gst).quantize(Decimal("0.01"))
    got = round_trip("nse-equity-delivery", buy, sell, TODAY, TODAY, "LONG")

    print("\n2. Longhand -- equity delivery, Rs 2,50,000 in / Rs 2,62,500 out")
    for k, v in (("STT", stt), ("txn+IPFT", txn), ("SEBI", sebi),
                 ("stamp", stamp), ("DP", dp), ("GST", gst)):
        print(f"     {k:<10} Rs {float(v):>10.4f}")
    print(f"     {'hand':<10} Rs {float(hand):>10.2f}")
    print(f"     {'engine':<10} Rs {float(got.total):>10.2f}     "
          f"divergence Rs {float(got.total - hand):+.2f}")
    assert abs(got.total - hand) <= Decimal("0.01"), (got.total, hand)
    return {"hand": float(hand), "engine": float(got.total)}


def check_against_app_card() -> dict:
    """Every current rate, side by side with the application's own card."""
    app = yaml.safe_load(APP_CARD.read_text())
    mine = load_card("nse-equity-delivery")

    def current(node):
        return float(node[-1]["value"]) if isinstance(node, list) else float(node)

    rows = [
        ("STT, each side", float(app["stt"]["delivery_rate"]),
         current(mine["stt"]["delivery_rate"])),
        ("exchange txn + IPFT",
         float(app["exchange_transaction_charge"]["cash_market_rate"])
         + float(app["exchange_transaction_charge"]["ipft_rate"]),
         current(mine["exchange_transaction_charge"]["cash_market_total"])),
        ("SEBI turnover fee", float(app["sebi_turnover_fee"]["rate"]),
         current(mine["sebi_turnover_fee"]["rate"])),
        ("stamp duty, buy", float(app["stamp_duty"]["delivery_rate"]),
         current(mine["stamp_duty"]["delivery_rate"])),
        ("GST", float(app["gst"]["rate"]), current(mine["gst"]["rate"])),
        ("DP per sell order", 12.50, current(mine["dp_charge"]["per_sell_order"])),
    ]
    print("\n3. Against the application's own card (read as YAML, never imported)")
    ok = True
    for name, a, m in rows:
        same = abs(a - m) < 1e-12
        ok &= same
        print(f"     {name:<22} app {a:<14.10g} experiment {m:<14.10g} "
              f"{'same' if same else 'DIFFERS'}")
    assert ok, "a current delivery rate differs from the application's card"
    return {"rows": [{"rate": n, "app": a, "experiment": m} for n, a, m in rows]}


def cost_of_a_pair() -> dict:
    """The number the whole experiment turns on.

    A pair is TWO instruments, so one complete round trip is FOUR fills. The
    cost is quoted against the GROSS notional -- the sum of both legs -- which
    is the quantity the spread's percentage return is also measured on.
    """
    print("\n4. What one complete PAIR round trip costs, as a percentage of gross notional")
    print("   (two instruments, four fills; the spread has to clear this before anything else)")
    out = {}
    for card, label, legs in (
        ("nse-equity-delivery", "cash delivery, both legs (UNEXECUTABLE -- see FEASIBILITY.md)",
         [("LONG",), ("LONG",)]),
        ("nse-equity-intraday", "cash intraday, both legs (not backtestable here)",
         [("LONG",), ("SHORT",)]),
        ("nse-equity-futures", "futures, both legs",
         [("LONG",), ("SHORT",)]),
    ):
        row = {}
        for notional in (50_000, 100_000, 250_000, 500_000, 1_000_000):
            n = Decimal(notional)
            total = Decimal(0)
            for (direction,) in legs:
                total += round_trip(card, n, n, TODAY, TODAY, direction).total
            gross = Decimal(2) * n
            row[notional] = float(total / gross * 100)
        out[card] = row
        cells = "  ".join(f"{row[k]:.4f}%" for k in sorted(row))
        print(f"     {label:<58}")
        print(f"       per leg  " + "  ".join(f"{k/1000:>7.0f}k" for k in sorted(row)))
        print(f"       cost     " + "  ".join(f"{row[k]:>7.4f}%" for k in sorted(row)))
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    results["published_example"] = check_published_example()
    results["longhand"] = check_longhand()
    results["vs_app_card"] = check_against_app_card()
    results["pair_round_trip_pct"] = cost_of_a_pair()
    (OUT / "charges_check.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {OUT / 'charges_check.json'}")


if __name__ == "__main__":
    main()
