"""Scoring the detectors against ground truth.

DEFINITIONS, because "accuracy" means nothing until they are fixed:

  HIT        a detection of the right PATTERN, reported at a bar no later
             than the true breakout + `late_tol`, whose start bar is within
             `loc_tol` x (true pattern length) of the true start.
             Both halves matter. Firing at the right time on the wrong
             geometry is luck; firing on the right geometry three weeks
             after the move is a post-mortem.
  RECALL     hits / positives
  FALSE POS  any detection of any of the three patterns on a NEGATIVE
             sample. This is the number that punishes a loose detector,
             and the reason the negative set is bigger than the positive.
  PRECISION  hits / (hits + false positives)

TWO MORE NUMBERS, because the binary per-sample rate above is misleading on
its own. A negative sample is ~200 bars, scanned at every other bar, at
three or four zigzag settings: some thousands of chances to say yes. Getting
one yes on a random walk in 200 bars is a very different failure from saying
yes on every bar, and the binary cannot tell them apart.

  FALSE ALARM RATE   detections per 1,000 bar-scans on negatives. This is
                     what an operator running a daily screen actually feels.
  SIGNAL PRECISION   the same accounting restricted to detections in the
                     `breakout` state -- the ones that would have produced a
                     trade. A `forming` detection that never breaks out costs
                     a line on a watchlist, not money.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .detect import detect_all
from .synth import Sample


@dataclass
class Result:
    recall: dict = field(default_factory=dict)
    precision: dict = field(default_factory=dict)
    hits: Counter = field(default_factory=Counter)
    misses: Counter = field(default_factory=Counter)
    false_positives: Counter = field(default_factory=Counter)
    fp_by_negative: Counter = field(default_factory=Counter)
    neg_detections: Counter = field(default_factory=Counter)
    neg_signals: Counter = field(default_factory=Counter)
    neg_scans: int = 0
    signal_fp: Counter = field(default_factory=Counter)
    signal_hits: Counter = field(default_factory=Counter)
    signal_precision: dict = field(default_factory=dict)
    fp_rate_per_1k: dict = field(default_factory=dict)
    variant_confusion: dict = field(default_factory=lambda: defaultdict(Counter))
    miss_reasons: Counter = field(default_factory=Counter)
    n_positive: Counter = field(default_factory=Counter)
    n_negative: int = 0


def _truth_span(s: Sample) -> tuple[int, int]:
    tr = s.truth
    if s.label == "cup_and_handle":
        return tr["cup_start"], tr["breakout"]
    if s.label == "vcp":
        return tr["base_start"], tr["breakout"]
    return tr["start"], tr["breakout"]


def evaluate(samples: list[Sample], start: int = 55, step: int = 2,
             late_tol: int = 4, loc_tol: float = 0.35,
             collect_reasons: bool = True, profile: str = "strict") -> Result:
    r = Result()
    for s in samples:
        bars = s.bars
        if s.label == "none":
            r.n_negative += 1
            fired, signalled = set(), set()
            for t in range(start, len(bars), step):
                r.neg_scans += 1
                for d in detect_all(bars, t, profile=profile):
                    fired.add(d.pattern)
                    r.neg_detections[d.pattern] += 1
                    if d.state == "breakout":
                        signalled.add(d.pattern)
                        r.neg_signals[d.pattern] += 1
                    r.variant_confusion[f"NEG:{s.variant}"][f"{d.pattern}/{d.variant}"] += 1
            for pat in fired:
                r.false_positives[pat] += 1
                r.fp_by_negative[f"{s.variant}->{pat}"] += 1
            for pat in signalled:
                r.signal_fp[pat] += 1
            continue

        r.n_positive[s.label] += 1
        t0, bo = _truth_span(s)
        span = max(1, bo - t0)
        deadline = min(len(bars) - 1, bo + late_tol)
        hit, best_variant, near_misses, signal = False, None, [], False
        for t in range(start, deadline + 1, step):
            for d in detect_all(bars, t, which=[s.label], profile=profile):
                if abs(d.start_idx - t0) <= loc_tol * span:
                    hit, best_variant = True, d.variant
                    signal = signal or d.state == "breakout"
                    break
                near_misses.append(d)
            if hit:
                break
        # a hit found while still forming counts as a signal if it later
        # breaks out within the deadline
        if hit and not signal:
            for t in range(start, deadline + 1, step):
                for d in detect_all(bars, t, which=[s.label], profile=profile):
                    if (abs(d.start_idx - t0) <= loc_tol * span
                            and d.state == "breakout"):
                        signal = True
                        break
                if signal:
                    break
        if hit:
            r.hits[s.label] += 1
            if signal:
                r.signal_hits[s.label] += 1
            r.variant_confusion[f"{s.label}:{s.variant}"][best_variant] += 1
        else:
            r.misses[s.label] += 1
            r.variant_confusion[f"{s.label}:{s.variant}"]["MISS"] += 1
            if collect_reasons:
                r.miss_reasons[_why_missed(s, deadline, near_misses)] += 1

    for pat in r.n_positive:
        n = r.n_positive[pat]
        h, fp = r.hits[pat], r.false_positives[pat]
        r.recall[pat] = h / n if n else 0.0
        r.precision[pat] = h / (h + fp) if (h + fp) else 0.0
        sh, sfp = r.signal_hits[pat], r.signal_fp[pat]
        r.signal_precision[pat] = sh / (sh + sfp) if (sh + sfp) else 0.0
        r.fp_rate_per_1k[pat] = (1000.0 * r.neg_detections[pat] / r.neg_scans
                                 if r.neg_scans else 0.0)
    return r


def _why_missed(s: Sample, deadline: int, near_misses) -> str:
    """Diagnose a miss: did nothing fire, or did it fire in the wrong place?"""
    if near_misses:
        return f"{s.label}:{s.variant}: fired but mislocated"
    from .detect import detect_all as _d
    at_end = _d(s.bars, min(deadline, len(s.bars) - 1), which=[s.label])
    if at_end:
        return f"{s.label}:{s.variant}: found late/offset"
    return f"{s.label}:{s.variant}: nothing fired"


def report(r: Result) -> str:
    L = []
    L.append(f"positives: {dict(r.n_positive)}   negatives: {r.n_negative}")
    L.append("")
    L.append(f"{'pattern':<18}{'recall':>8}{'prec':>8}{'sig.prec':>10}"
             f"{'FA/1k':>8}{'hit':>6}{'miss':>6}{'FP':>5}")
    for pat in sorted(r.n_positive):
        L.append(f"{pat:<18}{r.recall[pat]:>8.1%}{r.precision[pat]:>8.1%}"
                 f"{r.signal_precision[pat]:>10.1%}{r.fp_rate_per_1k[pat]:>8.1f}"
                 f"{r.hits[pat]:>6}{r.misses[pat]:>6}{r.false_positives[pat]:>5}")
    L.append(f"(FA/1k = detections per 1,000 bar-scans on the {r.neg_scans} "
             f"negative scans)")
    if r.fp_by_negative:
        L.append("")
        L.append("false positives by negative control:")
        for k, v in r.fp_by_negative.most_common():
            L.append(f"   {k:<44}{v:>4}")
    if r.miss_reasons:
        L.append("")
        L.append("misses:")
        for k, v in r.miss_reasons.most_common():
            L.append(f"   {k:<54}{v:>4}")
    return "\n".join(L)
