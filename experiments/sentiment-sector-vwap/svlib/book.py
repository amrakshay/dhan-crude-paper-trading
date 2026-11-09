"""Virtual money: open trades, fills, P&L, and the files a day leaves behind.

Nothing here talks to a broker. A "trade" is a dict that ends up as a row in a
CSV. Fills are deliberately pessimistic: every fill pays
`slippage_pct_per_side` against us, and a stop fills at the live price that
breached it (or the bar's open when a bar gaps through it).

TWO books run side by side on the same signals:

  plan  what the operator can actually do -- at most `plan.max_trades_per_day`
        (2) trades a day, the first eligible signals.        -> trades_plan.csv
  all   every eligible signal, one open trade per symbol, no daily cap. Never
        meant to be traded; it is the data that says WHICH 2 a day would have
        been worth taking.                                     -> trades_all.csv

Files, one directory per trading day under data/YYYY-MM-DD/:
  sentiment.csv     every 5-minute reading of the OI page
  tracking.csv      every 5-minute sector pick and the 3 names
  booster.csv       every sector and every stock % on the booster, every cycle
  signals.csv       every entry signal with ~30 features and what each book did with it
  decisions.csv     setups armed, entries, skips and why, stops moved, exits
  bars.csv          each closed 5m bar evaluated, with EMA and VWAP
  trades_plan.csv   closed trades of the plan book (features + outcome + excursions)
  trades_all.csv    the same for the all book
  summary.json      both books' totals
  state_*.json      open trades and counters, so a restart mid-day resumes them
and data/daily_pnl.csv, one row per day per book.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .costs import RateCard
from .engine import LONG, Signal


def csv_append(path: Path, row: dict) -> None:
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row), extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def ts(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


class Book:
    def __init__(self, name: str, day_dir: Path, cfg: dict, rates: RateCard) -> None:
        self.name = name
        self.dir = day_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.day = day_dir.name
        self.rates = rates
        self.slip = float(cfg["costs"]["slippage_pct_per_side"])
        self.capital = float(cfg["sizing"]["capital_per_trade"])
        self.trail_with = cfg.get("strategy", {}).get("trail_with", "ema9")
        self.open: dict[str, dict] = {}          # symbol -> trade
        self.closed: list[dict] = []
        self.trades_today: dict[str, int] = {}
        self._seq = 0
        self._load()

    @property
    def taken_today(self) -> int:
        return sum(self.trades_today.values())

    def decision(self, now: datetime, symbol: str, event: str, detail: str = "") -> None:
        csv_append(self.dir / "decisions.csv", {"time": ts(now), "book": self.name,
                                                 "symbol": symbol, "event": event, "detail": detail})

    # --- trades -------------------------------------------------------------
    def _fill(self, side_of_order: str, price: float) -> float:
        return price * (1 + self.slip) if side_of_order == "BUY" else price * (1 - self.slip)

    def enter(self, now: datetime, sig: Signal, ref_price: float, security_id: str,
              price_source: str, extra: Optional[dict] = None) -> Optional[dict]:
        qty = int(self.capital // ref_price)
        if qty <= 0:
            self.decision(now, sig.symbol, "REFUSED", f"price {ref_price:.2f} above capital per trade")
            return None
        fill = self._fill("BUY" if sig.side == LONG else "SELL", ref_price)
        self._seq += 1
        trade = {
            "id": f"{self.day}-{self.name}-{self._seq:03d}", "book": self.name,
            "symbol": sig.symbol, "security_id": security_id,
            "side": sig.side, "kind": sig.kind, "signal_bar": ts(sig.bar.start),
            "entry_time": ts(now), "entry_ref": round(ref_price, 2), "entry_price": round(fill, 4),
            "entry_price_source": price_source, "qty": qty,
            "initial_stop": round(sig.stop, 2), "stop": round(sig.stop, 2),
            "ema_at_signal": round(sig.ema, 2), "vwap_at_signal": round(sig.vwap, 2),
            "stop_moves": 0, "last_trailed_bar": ts(sig.bar.start),
            # Fixed at entry: a trade keeps the trail it was opened under.
            "trail_with": self.trail_with,
        }
        trade.update(extra or {})
        self.open[sig.symbol] = trade
        self.trades_today[sig.symbol] = self.trades_today.get(sig.symbol, 0) + 1
        self.decision(now, sig.symbol, f"ENTER_{sig.side}",
                      f"{sig.kind} bar {sig.bar.start:%H:%M} qty {qty} @ {fill:.2f} "
                      f"({price_source} {ref_price:.2f}) stop {sig.stop:.2f}")
        self.save()
        return trade

    def move_stop(self, now: datetime, symbol: str, new_stop: float, bar_start: datetime) -> None:
        t = self.open[symbol]
        t["last_trailed_bar"] = ts(bar_start)
        if round(new_stop, 2) != t["stop"]:
            label = "9 EMA" if t.get("trail_with") == "ema9" else "VWAP"
            self.decision(now, symbol, "TRAIL", f"stop {t['stop']:.2f} -> {new_stop:.2f} ({label} of {bar_start:%H:%M})")
            t["stop"] = round(new_stop, 2)
            t["stop_moves"] += 1
        self.save()

    def unrealised(self, symbol: str, price: float) -> float:
        t = self.open[symbol]
        sign = 1 if t["side"] == LONG else -1
        return sign * (price - t["entry_price"]) * t["qty"]

    def exit(self, now: datetime, symbol: str, ref_price: float, reason: str, price_source: str) -> dict:
        t = self.open.pop(symbol)
        fill = self._fill("SELL" if t["side"] == LONG else "BUY", ref_price)
        sign = 1 if t["side"] == LONG else -1
        gross = sign * (fill - t["entry_price"]) * t["qty"]
        buy_value = t["qty"] * (t["entry_price"] if t["side"] == LONG else fill)
        sell_value = t["qty"] * (fill if t["side"] == LONG else t["entry_price"])
        charges = self.rates.round_trip(buy_value, sell_value)
        risk = abs(t["entry_price"] - t["initial_stop"]) * t["qty"]
        entered = datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M:%S")
        t.update({
            "exit_time": ts(now), "exit_ref": round(ref_price, 2), "exit_price": round(fill, 4),
            "exit_price_source": price_source, "exit_reason": reason,
            "minutes_in_trade": round((now - entered).total_seconds() / 60, 1),
            "gross_pnl": round(gross, 2), "charges": round(charges, 2),
            "net_pnl": round(gross - charges, 2),
            "r_multiple": round((gross - charges) / risk, 3) if risk > 0 else "",
        })
        self.closed.append(t)
        self.decision(now, symbol, "EXIT", f"{reason} @ {fill:.2f} ({price_source} {ref_price:.2f}) "
                                           f"net {t['net_pnl']:+.2f}")
        self.write_trades()
        self.save()
        return t

    # --- persistence ----------------------------------------------------------
    def write_trades(self) -> None:
        if not self.closed:
            return
        fields: list[str] = []
        for t in self.closed:                      # union, first-seen order
            fields += [k for k in t if k not in fields]
        with (self.dir / f"trades_{self.name}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.closed)

    def save(self) -> None:
        state = {"open": self.open, "closed": self.closed,
                 "trades_today": self.trades_today, "seq": self._seq}
        tmp = self.dir / f"state_{self.name}.json.tmp"
        tmp.write_text(json.dumps(state, indent=1, default=str))
        tmp.replace(self.dir / f"state_{self.name}.json")

    def _load(self) -> None:
        path = self.dir / f"state_{self.name}.json"
        if not path.exists():
            return
        state = json.loads(path.read_text())
        self.open, self.closed = state["open"], state["closed"]
        self.trades_today, self._seq = state["trades_today"], state["seq"]

    def summary(self) -> dict:
        net = sum(t["net_pnl"] for t in self.closed)
        wins = [t for t in self.closed if t["net_pnl"] > 0]
        return {
            "date": self.day, "book": self.name, "trades": len(self.closed), "wins": len(wins),
            "losses": len(self.closed) - len(wins),
            "gross_pnl": round(sum(t["gross_pnl"] for t in self.closed), 2),
            "charges": round(sum(t["charges"] for t in self.closed), 2),
            "net_pnl": round(net, 2),
            "still_open": len(self.open),
        }


def write_summaries(day_dir: Path, books: list[Book]) -> list[dict]:
    rows = [b.summary() for b in books]
    (day_dir / "summary.json").write_text(json.dumps(rows, indent=1))
    return rows


def append_daily_ledger(root: Path, rows: list[dict]) -> None:
    ledger = root / "daily_pnl.csv"
    keep = []
    days = {(r["date"], r["book"]) for r in rows}
    if ledger.exists():
        with ledger.open() as fh:
            keep = [r for r in csv.DictReader(fh) if (r["date"], r.get("book", "")) not in days]
    with ledger.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(keep + rows)
