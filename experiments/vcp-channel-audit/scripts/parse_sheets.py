"""STEP 1 -- turn the 40 channel sheets into one table of Mainboard setups.

Reads every `data/raw/vcp_setups_YYYY-MM-DD.xlsx`, keeps the `Mainboard` sheet
only (SME is out of scope), and resolves each symbol to a Dhan security id from
the application's cached instrument master. Writes `data/setups_mainboard.csv`.

The list date is the date in the FILE NAME -- the channel scans after the close
and posts at ~17:15 IST, so the earliest a reader could act is the NEXT session.

Run from this directory:
    .venv/bin/python scripts/parse_sheets.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import openpyxl

EXP = Path(__file__).resolve().parent.parent
RAW = EXP / "data" / "raw"
MASTER = EXP.parent.parent / "backend" / "data" / "instrument_master" / "api-scrip-master-detailed.csv"
OUT = EXP / "data" / "setups_mainboard.csv"

# Symbols the channel prints under a name the master no longer carries.
# HEG became HEG Advanced Materials (HEGAM); same ISIN INE545A01024.
RENAMED = {("NSE", "HEG"): "HEGAM"}

COLS = ["rank", "symbol", "exchange", "sheet_close", "adr_pct", "pct_from_52wh",
        "value20d_cr", "rs63_pct", "inside_day"]


def load_master() -> dict[tuple[str, str], dict]:
    ids = {}
    with open(MASTER, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["SEGMENT"] == "E" and r["INSTRUMENT"] == "EQUITY":
                ids[(r["EXCH_ID"], r["UNDERLYING_SYMBOL"])] = r
    return ids


def main() -> None:
    master = load_master()
    posted = {m["file"]: m["posted_at_utc"]
              for m in json.loads((EXP / "data" / "manifest.json").read_text())["files"]}
    rows, unresolved = [], set()
    for path in sorted(RAW.glob("vcp_setups_*.xlsx")):
        list_date = path.stem.removeprefix("vcp_setups_")
        ws = openpyxl.load_workbook(path, data_only=True)["Mainboard"]
        header = [c.value for c in ws[1]]
        sector_i = header.index("Sector") if "Sector" in header else None
        industry_i = header.index("Industry") if "Industry" in header else None
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r[0] is None:
                continue
            rec = dict(zip(COLS, r[:9]))
            ex, sym = rec["exchange"], rec["symbol"]
            base = sym[:-3] if sym.endswith("-BE") else sym
            base = RENAMED.get((ex, base), base)
            m = master.get((ex, base))
            if m is None:
                unresolved.add((ex, sym))
            rows.append({
                "list_date": list_date,
                "posted_at_utc": posted[path.name],
                **rec,
                "inside_day": rec["inside_day"] == "Yes",
                "sector": r[sector_i] if sector_i is not None else "",
                "industry": r[industry_i] if industry_i is not None else "",
                "dhan_symbol": base,
                "security_id": m["SECURITY_ID"] if m else "",
                "series": m["SERIES"] if m else "",
                "isin": m["ISIN"] if m else "",
            })
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} Mainboard rows from {len({r['list_date'] for r in rows})} sheets "
          f"-> {OUT.relative_to(EXP)}; unresolved: {sorted(unresolved) or 'none'}")


if __name__ == "__main__":
    main()
