"""Put the experiment root on sys.path so scripts/ can import arblib/.

Also pin the BLAS thread pool to one thread. Every matrix in this experiment is
small -- an ADF regression is about 750 rows by 14 columns -- so threading one
costs more in coordination than it saves. It matters because the screens run
across ten worker PROCESSES: left alone, each worker spawns its own pool sized
to the whole machine, and a twelve-core box ends up more than ten times
oversubscribed. Measured: load average 141, and the screen made no progress at
all. Set before numpy is imported anywhere, which is why it lives here.
"""
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
