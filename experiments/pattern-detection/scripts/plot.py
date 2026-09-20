"""Render a detection to PNG so a human can check it.

The whole exercise is "detect without looking at the chart", but VERIFYING
the detector without looking at a chart is a different and much worse idea.
These plots are how a claimed cup gets checked against the only instrument
that has ever been authoritative about chart patterns: somebody's eyes.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from patlib.bars import Bars
from patlib.base import Detection


def plot_detection(bars: Bars, d: Detection, path: str | Path,
                   pad: int = 30, title: str | None = None) -> Path:
    a = max(0, d.start_idx - pad)
    b = min(len(bars), d.end_idx + pad + 1)
    x = np.arange(a, b)
    o, h, l, c = bars.open[a:b], bars.high[a:b], bars.low[a:b], bars.close[a:b]

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(13, 7), sharex=True,
        gridspec_kw=dict(height_ratios=[3.2, 1], hspace=0.06))
    up = c >= o
    ax.vlines(x, l, h, color="#556", linewidth=0.7)
    ax.vlines(x[up], o[up], c[up], color="#1a8f5a", linewidth=2.6)
    ax.vlines(x[~up], o[~up], c[~up], color="#c0392b", linewidth=2.6)

    ax.axvspan(d.start_idx, d.end_idx, color="#4a7fd4", alpha=0.07)
    ax.axhline(d.pivot_price, color="#1f6feb", ls="--", lw=1.3,
               label=f"pivot {d.pivot_price:,.2f}")
    ax.axhline(d.stop_price, color="#c0392b", ls=":", lw=1.3,
               label=f"stop {d.stop_price:,.2f}")

    m = d.metrics
    if d.pattern.endswith("cup_and_handle"):
        for key, col in (("left_rim", "#8a63d2"), ("right_rim", "#8a63d2")):
            if m.get(key):
                ax.axhline(m[key], color=col, lw=0.8, alpha=0.55)
    if d.pattern == "triangle" and m.get("upper_start") is not None:
        xs = np.array([d.start_idx, d.end_idx])
        ax.plot(xs, [m["upper_start"], m["upper_now"]], color="#1f6feb", lw=1.2, alpha=0.85)
        ax.plot(xs, [m["lower_start"], m["lower_now"]], color="#1f6feb", lw=1.2, alpha=0.85)
    if m.get("breakout_idx") is not None:
        ax.axvline(m["breakout_idx"], color="#1a8f5a", lw=1.2, alpha=0.8)

    axv.bar(x, bars.volume[a:b], color="#9aa", width=0.8)
    axv.set_ylabel("volume")
    step = max(1, (b - a) // 12)
    axv.set_xticks(x[::step])
    axv.set_xticklabels([str(dd) for dd in bars.date[a:b][::step]],
                        rotation=45, ha="right", fontsize=8)

    head = title or (f"{d.symbol} {d.timeframe}  {d.pattern}/{d.variant}  "
                     f"score={d.score:.2f}  {d.state}")
    ax.set_title(head + f"\n{d.start_date} .. {d.end_date}", fontsize=10)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.15)
    axv.grid(alpha=0.15)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path
