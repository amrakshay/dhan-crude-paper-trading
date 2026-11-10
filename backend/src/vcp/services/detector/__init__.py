"""The VCP detector, ported from experiments/pattern-detection/patlib.

COPIED, NOT REWRITTEN. Every line that decides anything is the experiment's
own, so the strategy this server paper-trades is the one that was
backtested. Only the import paths changed, and `bars.py` lost its loaders.
`tests/test_vcp_detector_port.py` holds the port to that: it replays stored
bars through this package and asserts the detections the experiment itself
produced, field for field.

If the detector ever needs changing, change the experiment first, re-run its
backtest, then copy the change here and regenerate the golden fixture with
`scripts/make_vcp_golden_fixture.py`. A fix made only here would be a
strategy nobody measured.
"""
