"""Build the listing universe: every NSE equity Dhan will serve, with its
full post-listing daily history.

WHY THIS EXISTS. The paper-trading app's `daily_bars` holds today's Nifty 500
and nothing else, so its 181 post-2016 first-bar dates are 27-48% of the
mainboard IPOs of those years -- and the ones it holds are the ones that later
joined the index. For a momentum study that bias is uncomfortable; for an IPO
study it is close to circular. See README.md section 1.

WHAT IT DOES. Reads Dhan's PUBLIC instrument master (no credentials), keeps the
NSE equity rows, then asks `/charts/historical` for each one's whole history.
The first bar of the response IS the NSE listing date -- verified against five
known listings in Step 0.

WHAT IT IS NOT. Read-only market data. This script reaches exactly one Dhan
endpoint, the same one the application's price chart uses, and one public CSV.
No broker surface is imported, constructed or stubbed.

Credentials come from the application's own store, decrypted in-process with
its `crypto_service`, and are never written anywhere or logged.

    python3 scripts/build_universe.py                 # full pull, 1 req/s
    python3 scripts/build_universe.py --limit 20      # smoke test
    python3 scripts/build_universe.py --resume        # skip what we have

Output: data/listings.db, whose `daily_bars` table deliberately mirrors the
application's column names so `patlib.bars.load_sqlite` reads it unchanged.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
DB_PATH = HERE / "data" / "listings.db"

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
CHARTS_URL = "https://api.dhan.co/v2/charts/historical"

# Series kept. EQ is the mainboard rolling-settlement series; BE is the trade-to-
# trade series a name can be moved into (surveillance) without ceasing to be the
# same listing. SM (NSE Emerge) is deliberately EXCLUDED: SME listings have
# different lot sizes, a 20% band from day one and liquidity that makes a daily
# OHLC bar close to fiction. Including them would double the count and halve the
# meaning.
KEEP_SERIES = ("EQ", "BE")

# The application's own client self-imposes this floor (MIN_REQUEST_INTERVAL_
# SECONDS in dhan_charts_client). Matched here rather than beaten: Dhan does not
# publish a per-endpoint limit for charts, and a number nobody has verified is
# not a number to go faster than.
MIN_REQUEST_INTERVAL_SECONDS = 1.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol            TEXT NOT NULL,
    exchange_segment  TEXT NOT NULL,
    security_id       TEXT,
    bar_date          TEXT NOT NULL,
    open              REAL NOT NULL,
    high              REAL NOT NULL,
    low               REAL NOT NULL,
    close             REAL NOT NULL,
    volume            REAL,
    PRIMARY KEY (exchange_segment, symbol, bar_date)
);
CREATE INDEX IF NOT EXISTS ix_daily_bars_symbol ON daily_bars(symbol);

CREATE TABLE IF NOT EXISTS securities (
    symbol        TEXT PRIMARY KEY,
    security_id   TEXT NOT NULL,
    isin          TEXT,
    display_name  TEXT,
    symbol_name   TEXT,
    series        TEXT,
    lot_size      INTEGER,
    status        TEXT NOT NULL,      -- ok | empty | http_error | error
    detail        TEXT,
    n_bars        INTEGER,
    first_bar     TEXT,
    last_bar      TEXT,
    fetched_at    TEXT
);
"""


# --------------------------------------------------------------------------
# credentials -- decrypted in-process, never printed, never stored
# --------------------------------------------------------------------------
def load_credentials() -> tuple[str, str]:
    backend = REPO / "backend"
    sys.path.insert(0, str(backend))
    # Absolute, because config_utils loads lazily -- inside decrypt(), long
    # after any chdir here would have been undone.
    os.environ["CONFIG_PATH"] = str(backend / "conf")

    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    from src.settings.services import crypto_service

    app_db = backend / "data" / "paper_trading.db"
    conn = sqlite3.connect(f"file:{app_db}?mode=ro", uri=True)
    try:
        rows = dict(conn.execute(
            "SELECT cs.key, COALESCE(cs.encrypted_value, cs.value) "
            "FROM connection_settings cs JOIN connections c ON c.id = cs.connection_id "
            "WHERE c.provider = 'dhan'"
        ).fetchall())
    finally:
        conn.close()

    client_id = rows.get("client_id") or os.environ.get("DHAN_CLIENT_ID", "")
    token = crypto_service.decrypt(rows.get("access_token") or "") or ""
    if not token:
        token = os.environ.get("DHAN_ACCESS_TOKEN", "")
    if not (client_id and token):
        sys.exit("No usable Dhan market-data credentials. Set them in the app's "
                 "Connections page, or DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN in .env.")
    return client_id, token


def token_expiry(token: str) -> datetime | None:
    """Read the `exp` claim locally. Never prints the token."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except Exception:
        return None


# --------------------------------------------------------------------------
# the instrument master -- public, unauthenticated
# --------------------------------------------------------------------------
def fetch_master(cache: Path) -> list[dict]:
    if not cache.exists():
        print(f"fetching instrument master -> {cache.name}", flush=True)
        with urllib.request.urlopen(MASTER_URL, timeout=120) as response:
            cache.write_bytes(response.read())
    text = cache.read_text(encoding="utf-8", errors="replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("EXCH_ID") == "NSE" and row.get("SEGMENT") == "E"
                and row.get("SERIES") in KEEP_SERIES
                and (row.get("UNDERLYING_SYMBOL") or "").strip()):
            rows.append(row)

    # One row per ticker. A name in both EQ and BE is the same listing; keep EQ.
    best: dict[str, dict] = {}
    for row in rows:
        symbol = row["UNDERLYING_SYMBOL"].strip()
        if symbol not in best or row["SERIES"] == "EQ":
            best[symbol] = row
    return [best[k] for k in sorted(best)]


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------
class Charts:
    def __init__(self, client_id: str, token: str) -> None:
        self._headers = {
            "access-token": token,
            "client-id": client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._last_request_at = 0.0

    def daily(self, security_id: str) -> dict:
        wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

        body = {
            "securityId": str(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryCode": 0,
            "oi": False,
            # Before any NSE equity's inception, so the first bar returned is
            # the listing day. `toDate` is documented as non-inclusive.
            "fromDate": "1990-01-01",
            "toDate": datetime.now().strftime("%Y-%m-%d"),
        }
        request = urllib.request.Request(
            CHARTS_URL, data=json.dumps(body).encode(),
            headers=self._headers, method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())


def bars_from_payload(payload: dict) -> list[tuple]:
    """Parallel arrays -> rows, deduplicated on date and sorted.

    Mirrors dhan_charts_client._candles_from_payload: arrays that disagree in
    length are a broken response, not something to pad.
    """
    timestamps = payload.get("timestamp") or []
    count = len(timestamps)
    for name in ("open", "high", "low", "close"):
        if len(payload.get(name) or []) != count:
            raise ValueError(f"payload arrays disagree: {count} timestamps, "
                             f"{len(payload.get(name) or [])} {name}")
    volumes = payload.get("volume") or []

    by_date: dict[str, tuple] = {}
    for i in range(count):
        try:
            # Dhan stamps a daily bar at the session's start in IST. Formatting
            # in UTC would roll the early-morning stamp back a day, so the
            # local-time conversion is deliberate -- this machine runs IST.
            day = datetime.fromtimestamp(float(timestamps[i])).strftime("%Y-%m-%d")
            row = (day,
                   float(payload["open"][i]), float(payload["high"][i]),
                   float(payload["low"][i]), float(payload["close"][i]),
                   float(volumes[i]) if i < len(volumes) else None)
        except (TypeError, ValueError):
            continue
        by_date[day] = row
    return [by_date[k] for k in sorted(by_date)]


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="stop after N symbols")
    parser.add_argument("--resume", action="store_true",
                        help="skip symbols already fetched successfully")
    parser.add_argument("--only", nargs="*", help="fetch just these tickers")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    master = fetch_master(HERE / "data" / "master.csv")
    if args.only:
        wanted = {s.upper() for s in args.only}
        master = [m for m in master if m["UNDERLYING_SYMBOL"].upper() in wanted]
    print(f"instrument master: {len(master)} NSE {'/'.join(KEEP_SERIES)} tickers",
          flush=True)

    done: set[str] = set()
    if args.resume:
        done = {r[0] for r in db.execute(
            "SELECT symbol FROM securities WHERE status = 'ok'")}
        print(f"resuming: {len(done)} already fetched", flush=True)

    client_id, token = load_credentials()
    expiry = token_expiry(token)
    if expiry:
        hours = (expiry - datetime.now(tz=timezone.utc)).total_seconds() / 3600
        print(f"token valid until {expiry:%Y-%m-%d %H:%M} UTC ({hours:+.1f} h)",
              flush=True)
        if hours < 0:
            return sys.exit("token has expired; renew it in the app first")
    charts = Charts(client_id, token)

    todo = [m for m in master if m["UNDERLYING_SYMBOL"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    started = time.monotonic()
    counts = {"ok": 0, "empty": 0, "http_error": 0, "error": 0}

    for index, row in enumerate(todo, 1):
        symbol = row["UNDERLYING_SYMBOL"].strip()
        security_id = row["SECURITY_ID"].strip()
        status, detail, bars = "ok", None, []
        try:
            bars = bars_from_payload(charts.daily(security_id))
            if not bars:
                status, detail = "empty", "no bars returned"
        except urllib.error.HTTPError as exc:
            body = exc.read()[:200].decode(errors="replace")
            status, detail = "http_error", f"HTTP {exc.code}: {body}"
            if exc.code in (401, 403):
                print(f"\nDhan refused the token on {symbol}: {detail}", flush=True)
                break
        except Exception as exc:  # noqa: BLE001 -- one bad symbol must not end the pull
            status, detail = "error", f"{type(exc).__name__}: {exc}"
        counts[status] += 1

        if bars:
            db.executemany(
                "INSERT OR REPLACE INTO daily_bars "
                "(symbol, exchange_segment, security_id, bar_date, open, high, "
                " low, close, volume) VALUES (?,'NSE_EQ',?,?,?,?,?,?,?)",
                [(symbol, security_id, *b) for b in bars],
            )
        db.execute(
            "INSERT OR REPLACE INTO securities (symbol, security_id, isin, "
            "display_name, symbol_name, series, lot_size, status, detail, "
            "n_bars, first_bar, last_bar, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol, security_id, row.get("ISIN"), row.get("DISPLAY_NAME"),
             row.get("SYMBOL_NAME"), row.get("SERIES"),
             int(float(row["LOT_SIZE"])) if row.get("LOT_SIZE") else None,
             status, detail, len(bars),
             bars[0][0] if bars else None, bars[-1][0] if bars else None,
             datetime.now(tz=timezone.utc).isoformat(timespec="seconds")),
        )
        db.commit()

        if index % 25 == 0 or index == len(todo):
            elapsed = time.monotonic() - started
            rate = index / elapsed if elapsed else 0
            remaining = (len(todo) - index) / rate / 60 if rate else 0
            print(f"  {index:5d}/{len(todo)}  {symbol:<14s} "
                  f"ok={counts['ok']} empty={counts['empty']} "
                  f"err={counts['http_error'] + counts['error']}  "
                  f"~{remaining:.0f} min left", flush=True)

    print(f"\ndone in {(time.monotonic() - started) / 60:.1f} min: {counts}", flush=True)
    total_bars, symbols = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM daily_bars").fetchone()
    print(f"{DB_PATH.name}: {total_bars:,} bars across {symbols} symbols", flush=True)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
