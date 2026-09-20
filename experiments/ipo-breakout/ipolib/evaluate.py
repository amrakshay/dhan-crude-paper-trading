"""Scoring the IPO-base detector against the synthetic ground truth.

DEFINITIONS, because "accuracy" means nothing until they are fixed. These
deliberately match `pattern-detection/patlib/evaluate.py` so the two
experiments' numbers can be read side by side.

  HIT        a detection on a POSITIVE sample, reported at a bar no later than
             the true breakout + `late_tol`, whose start bar is within
             `loc_tol` x (base length) of the true pivot.
             Both halves matter. Firing at the right time on the wrong
             geometry is luck; firing on the right geometry three weeks after
             the move is a post-mortem.
  RECALL     hits / positives
  FALSE POS  ANY detection on a NEGATIVE sample. No exceptions carved out --
             see the note on `base_that_fails` below.
  PRECISION  hits / (hits + false positives)

  FALSE ALARM RATE   detections per 1,000 bar-scans on negatives. A negative
                     sample is ~140 bars scanned every other bar: hundreds of
                     chances to say yes. One yes in 140 bars and yes on every
                     bar are very different failures and the binary per-sample
                     rate cannot tell them apart.
  SIGNAL PRECISION   the same accounting restricted to `breakout` detections --
                     the ones that would have cost money. A `forming`
                     detection that never triggers costs a watchlist line.

THE `base_that_fails` CONTROL IS NOT SPECIAL-CASED. It draws a real base that
never clears its pivot, so a `forming` detection on it is the CORRECT answer
and a `breakout` is a genuine error. Rather than exempt it from the false-
positive count -- which would be marking one's own homework -- it is counted
like every other negative and then broken out in the per-control table, where
the split between forming and breakout is visible. Signal precision is the
number that isolates the error that matters.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .ipo_base import IPOBaseParams, detect, earliest_bar, params
from .synth import Sample


@dataclass
class Result:
    n_positive: int = 0
    n_negative: int = 0
    hits: int = 0
    misses: int = 0
    recall: float = 0.0
    precision: float = 0.0
    signal_precision: float = 0.0
    fp_samples: int = 0                 # negatives with >= 1 detection
    fp_signal_samples: int = 0          # negatives with >= 1 breakout detection
    neg_detections: int = 0
    neg_signals: int = 0
    neg_scans: int = 0
    fp_rate_per_1k: float = 0.0
    signal_hits: int = 0
    by_negative: Counter = field(default_factory=Counter)
    by_negative_signal: Counter = field(default_factory=Counter)
    by_depth_cell: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    by_length_cell: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    miss_reasons: Counter = field(default_factory=Counter)


def _near_miss_reason(sample: Sample, p: IPOBaseParams) -> str:
    """Why a drawn base produced no detection. Guesswork is labelled as such."""
    truth = sample.truth
    base_len = truth["breakout"] - truth["pivot_idx"]
    if truth["breakout"] < p.min_history:
        return "breakout before min_history"
    if truth["pivot_idx"] > p.left_high_window:
        return "pivot outside left_high_window"
    if base_len < p.min_base_len:
        return "base shorter than min_base_len"
    if base_len > p.max_base_len:
        return "base longer than max_base_len"
    if truth.get("measured_depth_pct", 0) > p.max_depth_pct:
        return "deeper than max_depth_pct"
    if truth["breakout"] > p.max_age_sessions:
        return "breakout after max_age_sessions"
    return "unexplained -- look at this one"


def evaluate(samples: list[Sample], p: IPOBaseParams | None = None,
             setup: str = "base", step: int = 2, late_tol: int = 4,
             loc_tol: float = 0.35) -> Result:
    p = p or params()
    result = Result()

    for sample in samples:
        start = earliest_bar(p, setup)
        stop = min(len(sample.bars), p.max_age_sessions + 1)

        if sample.label == "ipo_base":
            result.n_positive += 1
            truth = sample.truth
            pivot_idx, breakout_idx = truth["pivot_idx"], truth["breakout"]
            base_len = breakout_idx - pivot_idx
            deadline = breakout_idx + late_tol
            tolerance = max(3, int(loc_tol * base_len))

            hit = False
            signal_hit = False
            for t in range(start, min(stop, deadline + 1), step):
                detection = detect(sample.bars, t, p, setup=setup)
                if detection is None:
                    continue
                if abs(detection.start_idx - pivot_idx) > tolerance:
                    continue
                hit = True
                if detection.state == "breakout":
                    signal_hit = True
                    break

            depth_cell = f"{int(truth['measured_depth_pct'] // 10) * 10}-" \
                         f"{int(truth['measured_depth_pct'] // 10) * 10 + 10}%"
            length_cell = f"{truth['base_len']}"
            for store, key in ((result.by_depth_cell, depth_cell),
                               (result.by_length_cell, length_cell)):
                store[key][1] += 1
                if hit:
                    store[key][0] += 1

            if hit:
                result.hits += 1
                result.signal_hits += int(signal_hit)
            else:
                result.misses += 1
                result.miss_reasons[_near_miss_reason(sample, p)] += 1
        else:
            result.n_negative += 1
            detections = 0
            signals = 0
            scans = 0
            for t in range(start, stop, step):
                scans += 1
                detection = detect(sample.bars, t, p, setup=setup)
                if detection is not None:
                    detections += 1
                    signals += int(detection.state == "breakout")
            result.neg_scans += scans
            result.neg_detections += detections
            result.neg_signals += signals
            if detections:
                result.fp_samples += 1
                result.by_negative[sample.variant] += 1
            if signals:
                result.fp_signal_samples += 1
                result.by_negative_signal[sample.variant] += 1

    result.recall = result.hits / result.n_positive if result.n_positive else 0.0
    result.precision = (result.hits / (result.hits + result.fp_samples)
                        if (result.hits + result.fp_samples) else 0.0)
    result.signal_precision = (
        result.signal_hits / (result.signal_hits + result.fp_signal_samples)
        if (result.signal_hits + result.fp_signal_samples) else 0.0)
    result.fp_rate_per_1k = (result.neg_detections / result.neg_scans * 1000
                             if result.neg_scans else 0.0)
    return result


def report(result: Result, title: str = "") -> str:
    lines = []
    if title:
        lines += [title, "=" * len(title), ""]
    lines += [
        f"positives {result.n_positive}   negatives {result.n_negative}"
        f"   negative bar-scans {result.neg_scans}",
        "",
        f"  recall             {result.recall:6.1%}   "
        f"({result.hits} hit, {result.misses} missed)",
        f"  precision          {result.precision:6.1%}   "
        f"(per-sample, any detection on a negative counts against)",
        f"  signal precision   {result.signal_precision:6.1%}   "
        f"(breakout-state only -- the ones that would have traded)",
        f"  false alarms       {result.fp_rate_per_1k:6.1f} per 1,000 bar-scans",
        "",
    ]
    if result.by_negative:
        lines.append("  false positives by control (any / breakout-state):")
        for variant in sorted(set(result.by_negative) | set(result.by_negative_signal)):
            lines.append(f"    {variant:<18s} {result.by_negative[variant]:3d} / "
                         f"{result.by_negative_signal[variant]:3d}")
        lines.append("")
    if result.by_depth_cell:
        lines.append("  recall by measured base depth:")
        for cell in sorted(result.by_depth_cell,
                           key=lambda c: int(c.split("-")[0])):
            hit, total = result.by_depth_cell[cell]
            lines.append(f"    {cell:<8s} {hit:3d}/{total:<3d} {hit / total:6.1%}")
        lines.append("")
    if result.by_length_cell:
        lines.append("  recall by drawn base length:")
        for cell in sorted(result.by_length_cell, key=int):
            hit, total = result.by_length_cell[cell]
            lines.append(f"    {cell:>3s} bars {hit:3d}/{total:<3d} {hit / total:6.1%}")
        lines.append("")
    if result.miss_reasons:
        lines.append("  misses, by the first rule that would explain them:")
        for reason, count in result.miss_reasons.most_common():
            lines.append(f"    {count:3d}  {reason}")
    return "\n".join(lines)
