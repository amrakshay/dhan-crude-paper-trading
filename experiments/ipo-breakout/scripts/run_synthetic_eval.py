"""Accuracy on the synthetic benchmark, where the answer is known.

    python3 scripts/run_synthetic_eval.py
    python3 scripts/run_synthetic_eval.py --profile strict --seed 11

Writes out/synthetic_<profile>.txt and out/synthetic_<profile>.json.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import _bootstrap  # noqa: F401

from ipolib import evaluate, ipo_base, synth

OUT = Path(__file__).resolve().parent.parent / "out"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", nargs="*", default=["strict", "relaxed"])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--step", type=int, default=2)
    args = parser.parse_args()

    samples = synth.build_benchmark(seed=args.seed)
    positives = sum(1 for s in samples if s.label == "ipo_base")
    print(f"benchmark: {positives} drawn bases, {len(samples) - positives} "
          f"negative controls (seed {args.seed})\n")

    OUT.mkdir(exist_ok=True)
    for profile in args.profile:
        params = ipo_base.params(profile)
        result = evaluate.evaluate(samples, params, step=args.step)
        text = evaluate.report(result, f"{profile} profile")
        print(text, "\n")
        (OUT / f"synthetic_{profile}.txt").write_text(text + "\n")
        # Built by hand rather than with asdict(): Result carries two
        # defaultdicts with lambda factories, which asdict cannot rebuild.
        payload = {
            "profile": profile,
            "n_positive": result.n_positive, "n_negative": result.n_negative,
            "hits": result.hits, "misses": result.misses,
            "recall": result.recall, "precision": result.precision,
            "signal_precision": result.signal_precision,
            "fp_samples": result.fp_samples,
            "fp_signal_samples": result.fp_signal_samples,
            "neg_detections": result.neg_detections,
            "neg_signals": result.neg_signals, "neg_scans": result.neg_scans,
            "fp_rate_per_1k": result.fp_rate_per_1k,
            "signal_hits": result.signal_hits,
            "by_negative": dict(result.by_negative),
            "by_negative_signal": dict(result.by_negative_signal),
            "by_depth_cell": dict(result.by_depth_cell),
            "by_length_cell": dict(result.by_length_cell),
            "miss_reasons": dict(result.miss_reasons),
            "params": asdict(params),
        }
        (OUT / f"synthetic_{profile}.json").write_text(json.dumps(payload, indent=2,
                                                                 default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
