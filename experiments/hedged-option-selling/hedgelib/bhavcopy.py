"""NSE F&O bhavcopy: download, parse, normalise, cache.

Two upstream formats, one normalised schema:

  UDiFF   nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_...csv.zip
          2024-01-01 -> present.  Richest: carries UndrlygPric, the board lot
          quantity and the executed-trade count.
  LEGACY  archives.nseindia.com/content/historical/DERIVATIVES/foDDMMMYYYYbhav.csv.zip
          2016-01-01 -> 2024-07-05.  No underlying price, no trade count, no
          lot size; volume is in CONTRACTS, not units.

The two overlap for 2024-01-01..2024-07-05, which is what
scripts/validate_bhavcopy.py uses to check the legacy parser against the
UDiFF one on the same sessions.

Nothing here writes outside data/cache/.
"""
from __future__ import annotations

import csv
import io
import sqlite3
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

UDIFF_URL = (
    "https://nsearchives.nseindia.com/content/fo/"
    "BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
)
LEGACY_URL = (
    "https://archives.nseindia.com/content/historical/DERIVATIVES/"
    "{year}/{mon}/fo{dd}{mon}{year}bhav.csv.zip"
)
INDEX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
FOVOLT_URL = "https://nsearchives.nseindia.com/archives/nsccl/volt/FOVOLT_{ddmmyyyy}.csv"

# Earliest date each source answers for.  Probed 2026-09-20; see FEASIBILITY.md.
UDIFF_FROM = date(2024, 1, 1)
LEGACY_FROM = date(2016, 1, 1)
LEGACY_TO = date(2024, 7, 5)

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


class NotPublished(Exception):
    """The archive answered 404 -- a holiday, or a file that does not exist."""


class Blocked(Exception):
    """The archive answered 403 -- outside the retained window, or refusing us."""


def _get(url: str, timeout: int = 40, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotPublished(url) from e
            if e.code == 403:
                raise Blocked(url) from e
            last = e
        except Exception as e:  # noqa: BLE001 - transient network
            last = e
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


# --------------------------------------------------------------------------
# normalised row
# --------------------------------------------------------------------------

@dataclass(slots=True)
class FoRow:
    trade_date: date
    symbol: str
    kind: str            # IDO IDF STO STF  (index/stock option/future)
    expiry: date
    strike: float        # 0.0 for futures
    opt_type: str        # CE PE or '' for futures
    open: float
    high: float
    low: float
    close: float
    settle: float
    underlying: float | None   # UDiFF only
    oi: int
    volume: int          # units (UDiFF) or contracts*lot (legacy, reconstructed)
    num_trades: int | None     # UDiFF only
    lot_size: int | None       # UDiFF direct; legacy DERIVED, see below
    turnover: float            # rupees
    source: str          # 'udiff' | 'legacy'


def _f(v: str) -> float:
    v = (v or "").strip()
    return float(v) if v else 0.0


def _i(v: str) -> int:
    v = (v or "").strip()
    if not v:
        return 0
    return int(float(v))


def parse_udiff(blob: bytes) -> list[FoRow]:
    z = zipfile.ZipFile(io.BytesIO(blob))
    raw = z.read(z.namelist()[0]).decode("utf-8", "replace")
    out: list[FoRow] = []
    for r in csv.DictReader(io.StringIO(raw)):
        kind = r["FinInstrmTp"].strip()
        if kind not in ("IDO", "IDF", "STO", "STF"):
            continue
        out.append(FoRow(
            trade_date=date.fromisoformat(r["TradDt"].strip()),
            symbol=r["TckrSymb"].strip(),
            kind=kind,
            expiry=date.fromisoformat(r["XpryDt"].strip()),
            strike=_f(r["StrkPric"]),
            opt_type=(r["OptnTp"] or "").strip(),
            open=_f(r["OpnPric"]), high=_f(r["HghPric"]),
            low=_f(r["LwPric"]), close=_f(r["ClsPric"]),
            settle=_f(r["SttlmPric"]),
            underlying=_f(r["UndrlygPric"]) or None,
            oi=_i(r["OpnIntrst"]), volume=_i(r["TtlTradgVol"]),
            num_trades=_i(r["TtlNbOfTxsExctd"]),
            lot_size=_i(r["NewBrdLotQty"]) or None,
            turnover=_f(r["TtlTrfVal"]),
            source="udiff",
        ))
    return out


_LEGACY_KIND = {
    "OPTIDX": "IDO", "FUTIDX": "IDF",
    "OPTSTK": "STO", "FUTSTK": "STF",
}


def parse_legacy(blob: bytes) -> list[FoRow]:
    z = zipfile.ZipFile(io.BytesIO(blob))
    raw = z.read(z.namelist()[0]).decode("utf-8", "replace")
    out: list[FoRow] = []
    for r in csv.DictReader(io.StringIO(raw)):
        inst = (r.get("INSTRUMENT") or "").strip()
        kind = _LEGACY_KIND.get(inst)
        if kind is None:
            continue
        ot = (r.get("OPTION_TYP") or "").strip()
        if ot == "XX":
            ot = ""
        out.append(FoRow(
            trade_date=_legacy_date(r["TIMESTAMP"].strip()),
            symbol=r["SYMBOL"].strip(),
            kind=kind,
            expiry=_legacy_date(r["EXPIRY_DT"].strip()),
            strike=_f(r.get("STRIKE_PR", "")),
            opt_type=ot,
            open=_f(r["OPEN"]), high=_f(r["HIGH"]),
            low=_f(r["LOW"]), close=_f(r["CLOSE"]),
            settle=_f(r["SETTLE_PR"]),
            underlying=None,
            oi=_i(r["OPEN_INT"]),
            # legacy CONTRACTS is a lot count; units need the lot size, which
            # this format does not carry.  Left as the lot count and flagged by
            # source='legacy' so nothing silently compares it to a UDiFF volume.
            volume=_i(r["CONTRACTS"]),
            num_trades=None,
            lot_size=None,          # filled by derive_lot_sizes()
            turnover=_f(r.get("VAL_INLAKH", "")) * 100_000.0,
            source="legacy",
        ))
    return out


def _legacy_date(s: str) -> date:
    # '01-JUL-2024' or '31-Jul-2024'
    dd, mon, yyyy = s.split("-")
    return date(int(yyyy), MONTHS.index(mon.upper()) + 1, int(dd))


def fetch_day(d: date, prefer: str = "auto") -> tuple[list[FoRow], str]:
    """Return (rows, source) for one session.  Raises NotPublished on holidays."""
    use_udiff = prefer == "udiff" or (prefer == "auto" and d >= UDIFF_FROM)
    if use_udiff:
        blob = _get(UDIFF_URL.format(ymd=d.strftime("%Y%m%d")))
        return parse_udiff(blob), "udiff"
    url = LEGACY_URL.format(year=d.year, mon=MONTHS[d.month - 1], dd=f"{d.day:02d}")
    return parse_legacy(_get(url)), "legacy"


# --------------------------------------------------------------------------
# index closes (underlying + India VIX) and NSE's own daily volatility
# --------------------------------------------------------------------------

def fetch_index_closes(d: date) -> dict[str, dict[str, float]]:
    raw = _get(INDEX_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))).decode("utf-8", "replace")
    out: dict[str, dict[str, float]] = {}
    for r in csv.DictReader(io.StringIO(raw)):
        name = (r.get("Index Name") or "").strip()
        if not name:
            continue
        try:
            out[name] = {
                "open": float(r["Open Index Value"]),
                "high": float(r["High Index Value"]),
                "low": float(r["Low Index Value"]),
                "close": float(r["Closing Index Value"]),
            }
        except (ValueError, KeyError):
            continue
    return out


def fetch_fovolt(d: date) -> dict[str, float]:
    """symbol -> NSE's published *applicable daily volatility* for that session.

    This is the exchange's own sigma, the input its price scan range is defined
    on.  Using it is what keeps the margin model sourced rather than invented.
    """
    raw = _get(FOVOLT_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))).decode("utf-8", "replace")
    rdr = csv.reader(io.StringIO(raw))
    header = next(rdr)
    cols = [c.strip() for c in header]
    sym_i = next(i for i, c in enumerate(cols) if c.startswith("Symbol"))
    vol_i = next(i for i, c in enumerate(cols) if c.startswith("Applicable Daily Volatility"))
    out: dict[str, float] = {}
    for row in rdr:
        if len(row) <= vol_i:
            continue
        try:
            out[row[sym_i].strip()] = float(row[vol_i].strip())
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_bhav (
  trade_date TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL,
  expiry TEXT NOT NULL, strike REAL NOT NULL, opt_type TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, settle REAL,
  underlying REAL, oi INTEGER, volume INTEGER, num_trades INTEGER,
  lot_size INTEGER, source TEXT NOT NULL, turnover REAL,
  PRIMARY KEY (trade_date, symbol, kind, expiry, strike, opt_type)
);
CREATE INDEX IF NOT EXISTS ix_fo_sym_date ON fo_bhav(symbol, trade_date);
CREATE INDEX IF NOT EXISTS ix_fo_date ON fo_bhav(trade_date);

CREATE TABLE IF NOT EXISTS index_close (
  trade_date TEXT NOT NULL, index_name TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  PRIMARY KEY (trade_date, index_name)
);

CREATE TABLE IF NOT EXISTS fo_volt (
  trade_date TEXT NOT NULL, symbol TEXT NOT NULL, daily_vol REAL NOT NULL,
  PRIMARY KEY (trade_date, symbol)
);

-- one row per session we have *attempted*, so a holiday is remembered as a
-- holiday and never refetched.
CREATE TABLE IF NOT EXISTS fetch_log (
  trade_date TEXT NOT NULL, feed TEXT NOT NULL, status TEXT NOT NULL,
  rows INTEGER NOT NULL DEFAULT 0, note TEXT,
  PRIMARY KEY (trade_date, feed)
);
"""


def open_cache(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def sessions(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


# --------------------------------------------------------------------------
# lot size for the legacy era
# --------------------------------------------------------------------------

def _snap_lot(m: float) -> int:
    """Snap a derived lot to the nearest plausible board lot.

    Exchanges publish round board lots.  The derivation lands within ~0.5% of
    the truth, so on a thin contract it can read 2002 where the answer is 2000
    or 1249 where it is 1250.  Snapping by magnitude removes that without ever
    moving a value more than the refusal band below tolerates.
    """
    if m <= 0:
        return 0
    if m < 20:
        step = 1
    elif m < 100:
        step = 5
    elif m < 500:
        step = 25
    elif m < 2000:
        step = 50
    elif m < 10000:
        step = 250
    else:
        step = 500
    return int(round(m / step) * step)


def derive_lot_sizes(rows: list[FoRow]) -> int:
    """Fill in `lot_size` on legacy rows, derived from the exchange's own figures.

    The legacy bhavcopy carries no board lot quantity, but it carries CONTRACTS
    (a lot count) and VAL_INLAKH (turnover), so the lot can be recovered rather
    than remembered.

    THE QUIRK THAT MAKES THIS WORK, verified rather than assumed.  Legacy OPTION
    turnover is NOTIONAL, and the notional is measured on STRIKE PLUS PREMIUM:

        VAL_INLAKH x 100000  =  contracts x lot x (strike + premium)

    Checked against the UDiFF format's own `NewBrdLotQty` on 2024-03-01, across
    279 (symbol, expiry) groups where both formats cover the same session:

        strike + premium basis : median error 0.018%,  worst 0.50%
        strike only            : median error 2.20%,   worst 12.52%
        premium only           : useless -- off by orders of magnitude

    Getting this wrong is not a rounding matter.  A premium-based reading
    returns implied "lot sizes" in the thousands.

    WHY A DATE TABLE WOULD NOT DO.  NIFTY's lot changed four times inside this
    sample -- 50, then 25 (Apr 2024), then 75 (Nov 2024), then 65 (Oct 2025) --
    and a change applies to NEWLY INTRODUCED EXPIRIES ONLY.  Existing contracts
    keep the lot they were listed with, so two different lot sizes are live on
    the same day.  A date-keyed table would mis-size every position held across
    a change.

    Derived per (symbol, expiry) as the MEDIAN over every contract of that
    expiry that actually traded -- the median rather than the single largest,
    because one contract with a small premium amplifies any rounding in the
    published turnover.  Returns the number of rows filled.

    ACCURACY, measured against UDiFF on four overlap sessions:

        INDEX symbols (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY):  0 / 116 wrong
        all symbols including single stocks:                    99 / 1382 wrong

    The single-stock misses are all within ~2% and come from board lots that
    are not round at all (1375, 1925, 4462, 7100), which the magnitude snap
    pulls onto a grid they were never on.  Single-stock options are out of
    scope for this study (they are physically settled), and on the index --
    the only thing traded here -- the derivation is EXACT.
    """
    from collections import defaultdict
    from statistics import median

    implied: dict[tuple[str, date], list[float]] = defaultdict(list)
    for r in rows:
        if r.lot_size or r.turnover <= 0 or r.volume <= 0 or r.close <= 0:
            continue
        base = (r.strike + r.close) if r.strike > 0 else r.close
        if base <= 0:
            continue
        implied[(r.symbol, r.expiry)].append(r.turnover / (r.volume * base))

    resolved: dict[tuple[str, date], int] = {}
    for key, vals in implied.items():
        if len(vals) < 2:
            continue
        m = median(vals)
        lot = _snap_lot(m)
        # refuse rather than guess if the snap moved it more than 2%
        if lot >= 1 and abs(m - lot) / lot <= 0.02:
            resolved[key] = lot

    n = 0
    for r in rows:
        if r.lot_size:
            continue
        got = resolved.get((r.symbol, r.expiry))
        if got:
            r.lot_size = got
            n += 1
    return n
