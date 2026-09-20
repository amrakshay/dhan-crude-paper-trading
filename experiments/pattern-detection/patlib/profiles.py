"""Two ways to mean "cup and handle", and both are in use.

STRICT is the textbook: O'Neil's 12-33% cup, a rounded bottom, a handle in
the upper half and no deeper than the low teens. Bulkowski's identification
guidelines, read literally.

RELAXED is how the patterns are actually labelled in practice -- in
screeners, in write-ups, and by most people pointing at a chart. Deeper
cups, a much weaker demand for roundness, looser rims.

Both are provided because the difference between them turned out to be the
most interesting result in this experiment. Every widely-published
"cup-and-handle example" checked here -- AAPL 2018-19, NVDA and POOL in
2020 -- is a 30-43% crash with a V bottom, and fails the rules it is
presented as illustrating. A detector faithful to the books will not find
them, and that is the detector being right about the books, not wrong about
the market. Which profile to run is a decision about what you are looking
for, so it is a parameter and not a default.
"""
from __future__ import annotations

from .cup_handle import CupParams
from .triangles import TriangleParams
from .vcp import VCPParams

STRICT = {
    "cup_and_handle": {},          # the module defaults ARE the strict reading
    "vcp": {},
    "triangle": {},
}

RELAXED = {
    "cup_and_handle": dict(
        depth_max=0.45, depth_max_deep=0.60, rim_tolerance=0.14,
        min_bottom_third=0.20, min_u_vs_v=0.35, handle_depth_max=0.20,
        handle_retrace_max=0.60, prior_advance_gate=0.05, near_rim_max=0.18,
        min_score=0.60,
    ),
    "vcp": dict(
        tighten_ratio=0.92, final_depth_max=0.16, first_depth_max=0.50,
        atr_squeeze_max=1.00, final_dryup_max=1.10, near_high_max=0.15,
        trend_template_min=4, prior_advance_min=0.10, min_score=0.55,
    ),
    "triangle": dict(
        converge_ratio=0.78, min_fill=0.35, touch_ratio_min=0.70,
        leg_shrink_max=0.85, min_score=0.68, min_prior_move=0.03,
    ),
}

_CLASSES = {"cup_and_handle": CupParams, "vcp": VCPParams,
            "triangle": TriangleParams}


def params(pattern: str, timeframe: str = "D", profile: str = "strict"):
    over = {"strict": STRICT, "relaxed": RELAXED}[profile][pattern]
    return _CLASSES[pattern](timeframe, **over)
