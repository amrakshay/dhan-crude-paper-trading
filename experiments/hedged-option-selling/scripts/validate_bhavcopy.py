"""Validate the two bhavcopy formats against each other, and the derived lot size.

2024-01-01 .. 2024-07-05 is served by BOTH the legacy and the UDiFF feeds.  That
overlap is the only independent check available on a splice that otherwise has
to be taken on trust, and it is also where the legacy lot-size derivation can be
scored against a published figure.

Writes out/bhavcopy_validation.txt.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hedgelib import bhavcopy as b  # noqa: E402

OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

OVERLAP = [date(2024, 1, 2), date(2024, 2, 1), date(2024, 3, 1),
           date(2024, 4, 1), date(2024, 5, 2), date(2024, 6, 3),
           date(2024, 7, 1)]

_lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    _lines.append(s)


def main() -> int:
    say("=" * 78)
    say("BHAVCOPY VALIDATION -- legacy against UDiFF on the overlap window")
    say("=" * 78)
    say("Both feeds serve 2024-01-01 .. 2024-07-05.  If the splice at 2024-07-05")
    say("is safe, they must agree on the same sessions.\n")

    ok = True
    say(f"  {'session':<12} {'udiff':>8} {'legacy':>8} {'common':>8} "
        f"{'close':>7} {'settle':>7} {'OI':>7}")
    for d in OVERLAP:
        try:
            u, _ = b.fetch_day(d, prefer="udiff")
            l, _ = b.fetch_day(d, prefer="legacy")
        except b.NotPublished:
            say(f"  {d.isoformat():<12} holiday")
            continue
        ku = {(r.symbol, r.kind, r.expiry, r.strike, r.opt_type): r for r in u}
        kl = {(r.symbol, r.kind, r.expiry, r.strike, r.opt_type): r for r in l}
        common = ku.keys() & kl.keys()
        dc = sum(1 for k in common if abs(ku[k].close - kl[k].close) > 0.005)
        ds = sum(1 for k in common if abs(ku[k].settle - kl[k].settle) > 0.005)
        do = sum(1 for k in common if ku[k].oi != kl[k].oi)
        ok &= (dc == 0 and ds == 0 and do == 0
               and len(ku) == len(kl) == len(common))
        say(f"  {d.isoformat():<12} {len(ku):>8,} {len(kl):>8,} {len(common):>8,} "
            f"{dc:>7} {ds:>7} {do:>7}")
    say(f"\n  contract sets and every price/OI identical: "
        f"{'YES' if ok else 'NO -- MISMATCHES ABOVE'}")

    # ---- the derived lot size ------------------------------------------
    say("")
    say("=" * 78)
    say("DERIVED LOT SIZE -- legacy derivation scored against UDiFF's own figure")
    say("=" * 78)
    say("Legacy carries no board lot. It is derived from the exchange's own")
    say("turnover, which for options is notional on STRIKE PLUS PREMIUM:")
    say("    VAL_INLAKH x 100000 = contracts x lot x (strike + premium)")
    say("")
    say(f"  {'session':<12} {'groups':>8} {'wrong':>7} {'index groups':>14} "
        f"{'index wrong':>12}")
    tot = bad = itot = ibad = 0
    INDEX = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
    for d in OVERLAP:
        try:
            u, _ = b.fetch_day(d, prefer="udiff")
            l, _ = b.fetch_day(d, prefer="legacy")
        except b.NotPublished:
            continue
        b.derive_lot_sizes(l)
        true = {(r.symbol, r.expiry): r.lot_size for r in u if r.lot_size}
        got = {(r.symbol, r.expiry): r.lot_size for r in l if r.lot_size}
        common = set(true) & set(got)
        wrong = [k for k in common if got[k] != true[k]]
        idx = [k for k in common if k[0] in INDEX]
        iwrong = [k for k in idx if got[k] != true[k]]
        tot += len(common); bad += len(wrong)
        itot += len(idx); ibad += len(iwrong)
        say(f"  {d.isoformat():<12} {len(common):>8} {len(wrong):>7} "
            f"{len(idx):>14} {len(iwrong):>12}")
    say("")
    say(f"  ALL SYMBOLS   : {bad}/{tot} wrong ({bad / max(tot, 1) * 100:.2f}%)")
    say(f"  INDEX SYMBOLS : {ibad}/{itot} wrong "
        f"({ibad / max(itot, 1) * 100:.2f}%)   <- the only ones this study trades")
    say("")
    say("  Single-stock misses are within ~2% and come from board lots that are")
    say("  not round at all (1375, 1925, 4462, 7100). Single-stock options are")
    say("  out of scope here -- they are physically settled.")
    idx_ok = (ibad == 0)
    say(f"\n  index lot size exact: {'YES' if idx_ok else 'NO -- FAIL'}")

    (OUT / "bhavcopy_validation.txt").write_text("\n".join(_lines) + "\n")
    print(f"\nwrote {OUT / 'bhavcopy_validation.txt'}")
    return 0 if (ok and idx_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
