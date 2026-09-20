"""Exit rules for a VCP breakout, compared on one set of bases.

The flat 40-bar hold was a placeholder. A 9 EMA trail is the natural
replacement and it turns out to be too tight -- so this grid asks what is
actually wrong with it and whether the usual repairs fix it:

  LOOSER       a longer average (20, 50)
  CONFIRMED    two consecutive closes below, instead of one
  BUFFERED     a close below the average by more than a fraction of ATR
  DELAYED      do not arm the trail until the trade is up 1R, so the
               breakout gets room to work before it is managed

Everything is measured two ways, because they disagree and the disagreement
is the point:

  mean R   what one trade earns per unit risked -- favours letting winners run
  R/bar    total R over total bars held -- favours getting out and redeploying

A fast exit is SUPPOSED to lose on the first and win on the second. Whether
it wins enough is the question.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
from patlib.bars import load_sqlite
from patlib.indicators import atr

DB = Path(__file__).resolve().parents[3] / "backend" / "data" / "paper_trading.db"
OUT = Path(__file__).resolve().parent.parent / "out"


def ema(x, period):
    a = 2.0 / (period + 1.0)
    out = np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


class Exit:
    def __init__(self, name, ma=None, confirm=1, buf_atr=0.0,
                 arm_after_R=0.0, flat=None, hard_stop=True):
        self.name, self.ma, self.confirm = name, ma, confirm
        self.buf_atr, self.arm_after_R = buf_atr, arm_after_R
        self.flat, self.hard_stop = flat, hard_stop


EXITS = [
    Exit("flat 40 bars", flat=40),
    Exit("flat 60 bars", flat=60),
    Exit("EMA9", ma=9),
    Exit("EMA9, 2 closes", ma=9, confirm=2),
    Exit("EMA9, 0.5 ATR buffer", ma=9, buf_atr=0.5),
    Exit("EMA9, armed at +1R", ma=9, arm_after_R=1.0),
    Exit("EMA9, 2 closes + armed +1R", ma=9, confirm=2, arm_after_R=1.0),
    Exit("EMA20", ma=20),
    Exit("EMA20, 2 closes", ma=20, confirm=2),
    Exit("EMA20, armed at +1R", ma=20, arm_after_R=1.0),
    Exit("EMA50", ma=50),
    Exit("EMA50, armed at +1R", ma=50, arm_after_R=1.0),
]


def run(b, mas, a14, cfg, entry_idx, entry, stop, trail_from, max_hold):
    risk = entry - stop
    hi = min(len(b) - 1, entry_idx + (cfg.flat or max_hold))
    below = 0
    armed = cfg.arm_after_R <= 0
    for j in range(entry_idx + 1, hi + 1):
        if cfg.hard_stop and float(b.low[j]) <= stop:
            op = float(b.open[j])
            return j, (op if op < stop else stop), "stop"
        if cfg.flat is not None:
            continue
        if trail_from is None or j <= trail_from:
            continue          # still pre-breakout: structural stop only
        if not armed:
            if (float(b.high[j]) - entry) / risk >= cfg.arm_after_R:
                armed = True
            continue
        m = mas[cfg.ma][j]
        if not np.isfinite(m):
            continue
        thresh = m - cfg.buf_atr * float(a14[j])
        if float(b.close[j]) < thresh:
            below += 1
            if below >= cfg.confirm:
                return j, float(b.close[j]), "trail"
        else:
            below = 0
    return hi, float(b.close[hi]), "cap"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bases", default="out/vcp_entry_bases.json")
    ap.add_argument("--max-hold", type=int, default=250)
    ap.add_argument("--tag", default="vcp_exit_grid")
    a = ap.parse_args()
    rows = json.loads(Path(a.bases).read_text())
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)

    res = defaultdict(list)
    periods = sorted({c.ma for c in EXITS if c.ma})
    for sym, group in by_sym.items():
        b = load_sqlite(DB, sym)
        mas = {p: ema(b.close, p) for p in periods}
        a14 = atr(b, 14)
        for r in group:
            stop, bo = float(r["stop"]), r.get("breakout_idx")
            for ename, ikey, ekey in (("breakout", "breakout_idx", "breakout_entry"),
                                      ("early", "immediate_idx", "immediate_entry")):
                i = r.get(ikey)
                e = r.get(ekey)
                if i is None or e is None or float(e) <= stop:
                    continue
                e = float(e)
                trail = i if ename == "breakout" else bo
                for cfg in EXITS:
                    ei, ex, why = run(b, mas, a14, cfg, i, e, stop, trail, a.max_hold)
                    res[(ename, cfg.name)].append(
                        ((ex - e) / (e - stop), ex / e - 1.0, ei - i, why,
                         r["seen_date"][:4]))

    L = ["VCP exits compared, same bases, stop always at the contraction low", ""]
    for ename in ("breakout", "early"):
        L.append(f"--- entry: {ename} "
                 f"({'first close above the pivot' if ename == 'breakout' else 'the forming bar; trail arms at the breakout'})")
        L.append(f"   {'exit rule':<30}{'n':>6}{'mean R':>8}{'med R':>7}{'win%':>6}"
                 f"{'mean ret':>10}{'med bars':>9}{'R/bar':>8}{'p95 R':>7}{'trail%':>8}")
        base = None
        for cfg in EXITS:
            v = res.get((ename, cfg.name))
            if not v:
                continue
            R = np.array([x[0] for x in v], float)
            ret = np.array([x[1] for x in v], float)
            bars = np.array([x[2] for x in v], float)
            tr = np.mean([x[3] == "trail" for x in v])
            rpb = R.sum() / max(bars.sum(), 1)
            if base is None:
                base = rpb
            L.append(f"   {cfg.name:<30}{len(v):>6}{R.mean():>8.2f}{np.median(R):>7.2f}"
                     f"{(R > 0).mean():>6.0%}{ret.mean():>10.2%}{np.median(bars):>9.0f}"
                     f"{rpb:>8.3f}{np.percentile(R, 95):>7.2f}{tr:>8.0%}")
        L.append("")
    L.append("R/bar is the number that decides whether a fast exit is worth it:")
    L.append("a rule earning half the R in a quarter of the time is ahead, IF you")
    L.append("have another signal to put the money into.")
    txt = "\n".join(L)
    print(txt)
    OUT.mkdir(exist_ok=True)
    (OUT / f"{a.tag}_report.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
