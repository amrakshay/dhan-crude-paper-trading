"""Parameter sensitivity: how much of the result is the idea, and how much
is the threshold?

A detector that only works at one setting of one number has not been
validated, it has been fitted. This sweeps one parameter at a time and
prints recall against the false-alarm rate, so the shape of the trade-off
is visible rather than asserted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from build_synthetic import build
from patlib.evaluate import evaluate

OUT = Path(__file__).resolve().parent.parent / "out"

GRIDS = {
    "triangle": {
        "min_score": [0.70, 0.78, 0.84, 0.90],
        "leg_shrink_max": [0.85, 0.75, 0.62, 0.50],
        "touch_ratio_min": [0.70, 0.80, 0.90, 1.00],
        "converge_ratio": [0.75, 0.68, 0.62, 0.55],
        "min_fill": [0.35, 0.45, 0.55, 0.65],
    },
    "cup_and_handle": {
        "min_score": [0.60, 0.65, 0.70, 0.78],
        "min_bottom_third": [0.20, 0.28, 0.35, 0.42],
        "min_u_vs_v": [0.35, 0.50, 0.65, 0.80],
        "rim_tolerance": [0.06, 0.10, 0.14],
    },
    "vcp": {
        "min_score": [0.55, 0.60, 0.65, 0.72],
        "tighten_ratio": [0.95, 0.90, 0.85, 0.75],
        "atr_squeeze_max": [1.00, 0.90, 0.85, 0.75],
        "final_dryup_max": [1.10, 1.00, 0.90, 0.80],
        "trend_template_min": [0, 4, 5, 6, 7],
    },
}

PARAM_CLASS = {}


def _patch(pattern: str, field: str, value):
    """Monkeypatch one default, run, restore. Crude and perfectly adequate."""
    import patlib.cup_handle as ch
    import patlib.triangles as tr
    import patlib.vcp as vc
    cls = {"triangle": tr.TriangleParams, "cup_and_handle": ch.CupParams,
           "vcp": vc.VCPParams}[pattern]
    orig = cls.__init__

    def patched(self, timeframe="D", **over):
        orig(self, timeframe, **over)
        if field not in over:
            setattr(self, field, value)
    cls.__init__ = patched
    return cls, orig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", required=True, choices=list(GRIDS))
    ap.add_argument("--step", type=int, default=3)
    a = ap.parse_args()

    samples = build()
    rows = []
    print(f"{'param':<22}{'value':>8}{'recall':>9}{'prec':>8}{'sig.prec':>10}{'FA/1k':>8}")
    for field, values in GRIDS[a.pattern].items():
        for v in values:
            cls, orig = _patch(a.pattern, field, v)
            try:
                r = evaluate(samples, step=a.step, collect_reasons=False)
            finally:
                cls.__init__ = orig
            row = dict(param=field, value=v,
                       recall=round(r.recall.get(a.pattern, 0), 4),
                       precision=round(r.precision.get(a.pattern, 0), 4),
                       signal_precision=round(r.signal_precision.get(a.pattern, 0), 4),
                       fa_per_1k=round(r.fp_rate_per_1k.get(a.pattern, 0), 2))
            rows.append(row)
            print(f"{field:<22}{v:>8}{row['recall']:>9.1%}{row['precision']:>8.1%}"
                  f"{row['signal_precision']:>10.1%}{row['fa_per_1k']:>8.1f}")
    OUT.mkdir(exist_ok=True)
    (OUT / f"sweep_{a.pattern}.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
