"""Put this experiment root AND pattern-detection's root on sys.path.

`patlib/` is imported, not forked: `bars`, `indicators`, `pivots`, `shapes`,
`evaluate` and `synth` are domain-neutral and are reused verbatim. Anything
specific to IPOs lives in this experiment's own `ipolib/`.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "pattern-detection"))
