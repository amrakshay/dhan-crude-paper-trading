"""STEP 3a -- pack data/clean/ into the dense panel cache (data/panel.npz)."""
import time

import _bootstrap  # noqa: F401
from islib import panel

t0 = time.time()
p = panel.build()
panel.save(p)
S, D, B = p.shape
print(f"{S} symbols x {D} days x {B} bars -> {panel.CACHE} in {time.time() - t0:.0f}s "
      f"({panel.CACHE.stat().st_size / 2**20:.0f} MB)")
