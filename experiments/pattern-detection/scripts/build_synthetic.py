"""Build the synthetic benchmark: positives across the parameter space,
plus negative controls, all from one seed so runs are reproducible."""
from __future__ import annotations

import numpy as np

import _bootstrap  # noqa: F401
from patlib import synth


def build(seed: int = 20260919, n_each: int = 1) -> list[synth.Sample]:
    rng = np.random.default_rng(seed)
    out: list[synth.Sample] = []

    # --- cup and handle, across the shape/size space O'Neil describes
    for depth in (0.14, 0.20, 0.28, 0.33):
        for cup_len in (45, 75, 120, 180):
            for handle_retrace in (0.20, 0.33, 0.45):
                for u in (2.6, 2.0, 1.6):
                    for _ in range(n_each):
                        out.append(synth.make_cup_and_handle(
                            rng, depth=depth, cup_len=cup_len,
                            handle_len=int(max(6, cup_len * 0.16)),
                            handle_retrace=handle_retrace, u_power=u,
                            noise=float(rng.uniform(0.006, 0.016))))

    # --- VCP, 2T..5T with varied tightening
    for depths in [(0.20, 0.10), (0.25, 0.12), (0.30, 0.14),
                   (0.22, 0.12, 0.06), (0.28, 0.15, 0.07), (0.18, 0.10, 0.05),
                   (0.25, 0.15, 0.08, 0.04), (0.32, 0.18, 0.10, 0.05),
                   (0.30, 0.18, 0.11, 0.06, 0.03)]:
        for leg in (12, 18, 26):
            for _ in range(n_each * 2):
                out.append(synth.make_vcp(
                    rng, depths=depths, leg_len=leg,
                    noise=float(rng.uniform(0.006, 0.014))))

    # --- triangles and their relatives
    for kind in ("ascending", "descending", "symmetrical",
                 "rising_wedge", "falling_wedge"):
        for length in (35, 55, 85):
            for height in (0.14, 0.22, 0.30):
                for legs in (5, 6, 8):
                    for _ in range(n_each):
                        out.append(synth.make_triangle(
                            rng, kind=kind, length=length, height=height,
                            legs=legs, noise=float(rng.uniform(0.005, 0.013))))

    # --- negatives: as many as the positives, deliberately
    n_pos = len(out)
    per = max(1, n_pos // (len(synth.NEGATIVE_MAKERS)))
    for maker in synth.NEGATIVE_MAKERS:
        for _ in range(per):
            out.append(maker(rng))
    return out


if __name__ == "__main__":
    s = build()
    from collections import Counter
    print(Counter(f"{x.label}:{x.variant}" for x in s).most_common())
    print("total", len(s))
