"""Bear-market intraday research. Imported by nothing outside this directory.

Reuses `../intraday-screener/` (cleaned 5-min bars, islib) and
`../hedged-option-selling/` (bhavcopy cache) READ-ONLY, through sys.path and
read-only SQLite URIs. Nothing here writes into another experiment or into
the application's database, and nothing here can place an order.
"""
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent.parent
EXPERIMENTS = EXP.parent
REPO = EXPERIMENTS.parent
SCREENER = EXPERIMENTS / "intraday-screener"
HEDGED = EXPERIMENTS / "hedged-option-selling"
ORDER_BLOCK = EXPERIMENTS / "order-block"
APP_DB = REPO / "backend" / "data" / "paper_trading.db"
OPTIONS_DB = HEDGED / "data" / "cache" / "options.db"
OUT = EXP / "out"

if str(SCREENER) not in sys.path:
    sys.path.insert(0, str(SCREENER))
