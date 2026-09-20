"""Why did a detector miss / fire? Prints per-sample detail."""
from __future__ import annotations

import argparse
from collections import Counter

import numpy as np

import _bootstrap  # noqa: F401
from build_synthetic import build
from patlib.detect import detect_all
from patlib.evaluate import _truth_span


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="cup_and_handle")
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--show", type=int, default=8)
    a = ap.parse_args()

    samples = [s for s in build() if s.label == a.label]
    missed, why = [], Counter()
    for s in samples:
        t0, bo = _truth_span(s)
        span = max(1, bo - t0)
        hit = False
        for t in range(55, min(len(s.bars) - 1, bo + 4) + 1, a.step):
            for d in detect_all(s.bars, t, which=[a.label]):
                if abs(d.start_idx - t0) <= 0.35 * span:
                    hit = True
                    break
            if hit:
                break
        if not hit:
            missed.append(s)
    print(f"{a.label}: {len(missed)}/{len(samples)} missed")

    for s in missed[: a.show]:
        t0, bo = _truth_span(s)
        print(f"\n--- {s.variant} truth start={t0} breakout={bo} "
              f"len={len(s.bars)} {s.truth}")
        # re-run with every gate relaxed to see how far it got
        from patlib.cup_handle import CupParams, detect_cup_and_handle
        from patlib.vcp import VCPParams, detect_vcp
        from patlib.triangles import TriangleParams, detect_triangles
        for t in (bo - 2, bo, min(bo + 4, len(s.bars) - 1)):
            if a.label == "cup_and_handle":
                dets = detect_cup_and_handle(s.bars, t, CupParams("D", min_score=0.0))
            elif a.label == "vcp":
                dets = detect_vcp(s.bars, t, VCPParams("D", min_score=0.0))
            else:
                dets = detect_triangles(s.bars, t, TriangleParams("D", min_score=0.0))
            if not dets:
                print(f"    t={t}: nothing even with min_score=0")
            for d in dets[:3]:
                print(f"    t={t}: start={d.start_idx} score={d.score:.2f} "
                      f"fails={d.reasons}")
                print(f"           {d.metrics}")


if __name__ == "__main__":
    main()
