"""Order-block playbook backtest. Reuses ../intraday-screener/islib read-only."""
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent.parent
SCREENER = EXP.parent / "intraday-screener"
if str(SCREENER) not in sys.path:
    sys.path.insert(0, str(SCREENER))
