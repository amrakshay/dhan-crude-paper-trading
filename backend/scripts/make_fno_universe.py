"""Write conf/universes/nse-fno-stocks.csv from Dhan's instrument master.

The Options Trader trades options on every NSE stock that HAS options. That
list is NSE's F&O inclusion list, which changes a few times a year, and it is
written down as a universe file -- the same shape `nifty500.csv` has -- rather
than recomputed on every master refresh. Two reasons:

  * the framework's universe is a FILE an operator can read and diff, and a
    name entering or leaving F&O is a change worth seeing in a commit; and
  * the equity row of each name (its spot price) is claimed through the same
    universe, so the list must be the same on both sides.

Run it after NSE announces a change to the F&O list, then commit the result:

    CONFIG_PATH=conf .venv/bin/python scripts/make_fno_universe.py \
        [--master data/instrument_master/api-scrip-master-detailed.csv]

Reads a local file only. Downloading a fresh master is the instrument refresh's
job (POST /api/instruments/refresh), not this script's.
"""
import argparse
import csv
import os
import sys

DEFAULT_MASTER = "data/instrument_master/api-scrip-master-detailed.csv"
OUTPUT = "conf/universes/nse-fno-stocks.csv"


def build(master_path: str):
    option_underlyings = set()
    equities = {}
    with open(master_path, "r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            exchange = (row.get("EXCH_ID") or "").strip()
            instrument = (row.get("INSTRUMENT") or "").strip()
            symbol = (row.get("UNDERLYING_SYMBOL") or "").strip()
            if exchange != "NSE" or not symbol:
                continue
            if instrument == "OPTSTK":
                option_underlyings.add(symbol)
            elif instrument == "EQUITY" and (row.get("SERIES") or "").strip() in ("EQ", "BE"):
                equities.setdefault(
                    symbol,
                    ((row.get("SECURITY_ID") or "").strip(), (row.get("DISPLAY_NAME") or "").strip()),
                )
    missing = sorted(option_underlyings - set(equities))
    rows = [
        (symbol, equities[symbol][0], equities[symbol][1], "NSE_EQ")
        for symbol in sorted(option_underlyings)
        if symbol in equities
    ]
    return rows, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--master", default=DEFAULT_MASTER)
    args = parser.parse_args()
    if not os.path.exists(args.master):
        print(f"No master at {args.master}; refresh the instrument master first.")
        return 2
    rows, missing = build(args.master)
    with open(OUTPUT, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "security_id", "name", "exchange_segment"])
        writer.writerows(rows)
    print(f"Wrote {len(rows)} F&O stocks to {OUTPUT}")
    if missing:
        # A stock with options and no cash row cannot be priced as an
        # underlying. Reported, never silently dropped.
        print(f"{len(missing)} OPTSTK underlyings have no NSE EQ/BE row: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
