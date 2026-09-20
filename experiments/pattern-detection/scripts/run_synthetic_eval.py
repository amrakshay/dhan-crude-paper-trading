"""Score the detectors against the synthetic benchmark."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import _bootstrap  # noqa: F401
from build_synthetic import build
from patlib.evaluate import evaluate, report

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--tag", default="synthetic")
    ap.add_argument("--profile", default="strict", choices=["strict", "relaxed"])
    a = ap.parse_args()

    samples = build(seed=a.seed)
    if a.limit:
        import random
        random.Random(1).shuffle(samples)
        samples = samples[: a.limit]
    t0 = time.time()
    r = evaluate(samples, step=a.step, profile=a.profile)
    el = time.time() - t0
    txt = report(r)
    print(txt)
    print(f"\n{len(samples)} samples in {el:.1f}s")

    OUT.mkdir(exist_ok=True)
    (OUT / f"{a.tag}_report.txt").write_text(txt + f"\n\n{len(samples)} samples in {el:.1f}s\n")
    (OUT / f"{a.tag}_metrics.json").write_text(json.dumps({
        "recall": r.recall, "precision": r.precision,
        "hits": dict(r.hits), "misses": dict(r.misses),
        "false_positives": dict(r.false_positives),
        "fp_by_negative": dict(r.fp_by_negative),
        "miss_reasons": dict(r.miss_reasons),
        "signal_precision": r.signal_precision,
        "fp_rate_per_1k": r.fp_rate_per_1k,
        "signal_hits": dict(r.signal_hits), "signal_fp": dict(r.signal_fp),
        "neg_detections": dict(r.neg_detections), "neg_scans": r.neg_scans,
        "variant_confusion": {k: dict(v) for k, v in r.variant_confusion.items()},
        "n_positive": dict(r.n_positive), "n_negative": r.n_negative,
        "seconds": round(el, 1), "step": a.step, "profile": a.profile,
    }, indent=2))


if __name__ == "__main__":
    main()
