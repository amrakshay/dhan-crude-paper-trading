"""Flatten data/raw/trades/*.json into data/signals.csv (one row per signal) and
data/legs.csv (one row per option leg), and reconcile against the daily P&L
series the calendar shows (GetHistoricalPnlBreakdown)."""
import collections
import csv
import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def ist(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(IST)


def main():
    signals, legs = [], []
    for f in sorted((RAW / "trades").glob("*.json")):
        for s in json.loads(f.read_text())["data"] or []:
            opened, closed = ist(s["createdAt"]), ist(s["closedOn"])
            lots = s["lotsBought"]
            short = [t for t in s["trades"] if t["transactionType"] == "SELL"]
            long_ = [t for t in s["trades"] if t["transactionType"] == "BUY"]
            for t in s["trades"]:
                legs.append({
                    "signal_id": s["signalId"], "opened_ist": opened.isoformat(timespec="seconds"),
                    "symbol": t["symbol"], "side": t["transactionType"], "option_type": t["optionType"],
                    "strike": t["strikePrice"], "expiry": ist(t["expiry"]).date().isoformat(),
                    "lot_size": t["multiplier"], "lots": lots * t["quantity"],
                    "entry": t["entryPrice"], "exit": t["avgExitPrice"],
                })
            row = {
                "signal_id": s["signalId"],
                "opened_ist": opened.isoformat(timespec="seconds"),
                "closed_ist": closed.isoformat(timespec="seconds"),
                "close_date": closed.date().isoformat(),
                "hold_hours": round((closed - opened).total_seconds() / 3600, 2),
                "overnight": closed.date() > opened.date(),
                "n_legs": len(s["trades"]), "n_exits": len(s.get("exits") or []),
                "lots": lots, "margin": s["marginRequired"],
                "pnl": s["pnl"], "pnl_pct": s["pnlPercent"],
                "signal_pnl_1lot": s["signalPnl"], "signal_pnl_pct": s["signalPnlPercent"],
            }
            if len(short) == 1 and len(long_) == 1:
                sh, lg = short[0], long_[0]
                row.update({
                    "option_type": sh["optionType"], "expiry": ist(sh["expiry"]).date().isoformat(),
                    "dte_at_entry": (ist(sh["expiry"]).date() - opened.date()).days,
                    "short_strike": sh["strikePrice"], "long_strike": lg["strikePrice"],
                    "width": abs(lg["strikePrice"] - sh["strikePrice"]),
                    "lot_size": sh["multiplier"],
                    "credit": round(sh["entryPrice"] - lg["entryPrice"], 2),
                    "debit_at_exit": round(sh["avgExitPrice"] - lg["avgExitPrice"], 2),
                })
            signals.append(row)

    signals.sort(key=lambda r: r["opened_ist"])
    cols = list(dict.fromkeys(k for r in signals for k in r))
    with open(ROOT / "data" / "signals.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        w.writerows(signals)
    with open(ROOT / "data" / "legs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, list(legs[0]))
        w.writeheader()
        w.writerows(legs)

    # reconcile: calendar P&L keyed DD/MM/YYYY by close date
    cal = json.loads((RAW / "GetHistoricalPnlBreakdown.json").read_text())["data"]["pnl"]
    cal = {dt.datetime.strptime(k, "%d/%m/%Y").date().isoformat(): v for k, v in cal.items()}
    by_day = collections.defaultdict(float)
    for r in signals:
        by_day[r["close_date"]] += r["pnl"]
    days = sorted(set(cal) | set(by_day))
    bad = [(d, cal.get(d), round(by_day.get(d, 0), 2)) for d in days
           if abs(cal.get(d, 0) - by_day.get(d, 0)) > 0.5]
    print(f"signals={len(signals)} legs={len(legs)} calendar_days={len(cal)} signal_days={len(by_day)}")
    print(f"sum signals={sum(r['pnl'] for r in signals):,.2f}  sum calendar={sum(cal.values()):,.2f}")
    print(f"days that disagree: {len(bad)}")
    for b in bad[:15]:
        print("  ", b)


if __name__ == "__main__":
    main()
