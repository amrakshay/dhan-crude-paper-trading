"""Honest-return audit of Dhan's backtest of Zen Credit Spread Overnight, run on
Rs 1,00,000 of capital.

What Dhan's own figures leave out, and what this puts back:

  * charges -- brokerage, STT, NSE transaction charge, SEBI fee, stamp duty and
    GST, each at the rate in force ON THE TRADE'S DATE (STT on option sales went
    0.10% -> 0.15% on 2026-04-01 inside this sample).  The card and engine are
    reused from experiments/hedged-option-selling, not re-typed.
  * slippage -- a half-spread paid on every fill, swept over four scenarios,
    because the backtest's prices are not fills and nothing says what they are.
  * sizing -- lots are bought with the capital actually free, at Dhan's own
    per-lot margin, so a 1L account takes the trade at 1 lot (or not at all)
    rather than Dhan's 2-6 lots on 3.2L.

Every trade is recomputed from its LEG prices; Dhan's rupee P&L is not used.
A trade held to expiry is not a transaction on the way out: no brokerage, no
exit slippage, only STT on a long leg that finished in the money.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import math
import pathlib
import sys
from dataclasses import dataclass
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "hedged-option-selling"))
from hedgelib.charges import ChargeCard, Leg  # noqa: E402

CAPITAL = 100_000.0
TICK = 0.05

# half-spread per fill = max(rel x price, ticks x 0.05)
SLIPPAGE = {
    "none": (0.0, 0),
    "low": (0.0025, 1),   # 0.25%, min 1 tick
    "base": (0.005, 2),   # 0.5%,  min 2 ticks
    "high": (0.01, 4),    # 1%,    min 4 ticks
}


@dataclass
class Trade:
    sid: str
    opened: dt.datetime
    closed: dt.datetime
    expiry: dt.date
    margin_per_lot: float
    legs: list[dict]          # side, entry, exit, lot_size
    to_expiry: bool
    dhan_lots: int


def load() -> list[Trade]:
    legs = collections.defaultdict(list)
    for l in csv.DictReader(open(ROOT / "data" / "legs.csv")):
        legs[l["signal_id"]].append({"side": l["side"], "entry": float(l["entry"]),
                                     "exit": float(l["exit"]), "lot_size": int(l["lot_size"])})
    out = []
    for r in csv.DictReader(open(ROOT / "data" / "signals.csv")):
        opened = dt.datetime.fromisoformat(r["opened_ist"])
        closed = dt.datetime.fromisoformat(r["closed_ist"])
        expiry = dt.date.fromisoformat(r["expiry"])
        # Settled at expiry: stamped at/after midnight following expiry, or a
        # close dated after the contract had already expired.
        to_expiry = closed.date() > expiry
        if to_expiry:
            closed = dt.datetime.combine(expiry, dt.time(15, 30), opened.tzinfo)
        out.append(Trade(r["signal_id"], opened, closed, expiry, float(r["margin"]),
                         legs[r["signal_id"]], to_expiry, int(r["lots"])))
    return sorted(out, key=lambda t: t.opened)


def half_spread(price: float, scen: str) -> float:
    rel, ticks = SLIPPAGE[scen]
    return max(price * rel, ticks * TICK)


def cost_trade(t: Trade, lots: int, card: ChargeCard, scen: str) -> dict:
    """Gross, slippage and charges for one trade at `lots` lots."""
    gross = slip = 0.0
    entry_legs, exit_legs, intrinsics = [], [], []
    for l in t.legs:
        q = lots * l["lot_size"]
        sell_open = l["side"] == "SELL"
        sign = 1 if sell_open else -1
        gross += sign * (l["entry"] - l["exit"]) * q
        # entry fill: worse by a half-spread
        hs_in = half_spread(l["entry"], scen)
        fill_in = l["entry"] - hs_in if sell_open else l["entry"] + hs_in
        slip += hs_in * q
        entry_legs.append(Leg(l["side"], Decimal(str(round(fill_in, 4))), q))
        if t.to_expiry:
            intrinsics.append(Decimal(str(l["exit"])))
        else:
            hs_out = half_spread(l["exit"], scen) if l["exit"] > 0 else 0.0
            closing_side = "BUY" if sell_open else "SELL"
            fill_out = l["exit"] + hs_out if closing_side == "BUY" else max(l["exit"] - hs_out, 0)
            slip += hs_out * q
            exit_legs.append(Leg(closing_side, Decimal(str(round(fill_out, 4))), q))
    ch = card.charge_trade(entry_legs, t.opened.date())
    if t.to_expiry:
        ch = ch + card.charge_expiry(entry_legs, intrinsics, t.expiry)
    else:
        ch = ch + card.charge_trade(exit_legs, t.closed.date())
    return {"gross": gross, "slippage": slip, "charges": float(ch.total), "breakdown": ch.as_dict()}


def simulate(trades: list[Trade], card: ChargeCard, scen: str, sizing: str) -> dict:
    """Event-driven: margin is blocked from entry to exit; lots are bought with
    FREE cash.  sizing 'fixed1' = 1 lot when affordable; 'compound' = as many as
    free cash buys."""
    events = []
    for i, t in enumerate(trades):
        events.append((t.opened, 1, i))
        events.append((t.closed, 0, i))   # closes sort before opens at the same instant
    events.sort()
    cash = CAPITAL
    blocked: dict[int, float] = {}
    lots_of: dict[int, int] = {}
    rows, skipped = [], []
    equity_pts = [(trades[0].opened, CAPITAL)]
    for when, kind, i in events:
        t = trades[i]
        if kind == 1:
            free = cash - sum(blocked.values())
            n = math.floor(free / t.margin_per_lot)
            if sizing == "fixed1":
                n = min(n, 1)
            if n < 1:
                skipped.append({"opened": t.opened.isoformat(), "margin_per_lot": t.margin_per_lot,
                                "free": round(free, 2)})
                continue
            lots_of[i] = n
            blocked[i] = n * t.margin_per_lot
        elif i in lots_of:
            blocked.pop(i)
            c = cost_trade(t, lots_of[i], card, scen)
            net = c["gross"] - c["slippage"] - c["charges"]
            cash += net
            equity_pts.append((t.closed, cash))
            rows.append({"opened": t.opened.isoformat(timespec="minutes"),
                         "closed": t.closed.isoformat(timespec="minutes"),
                         "lots": lots_of[i], "to_expiry": t.to_expiry,
                         "gross": round(c["gross"], 2), "slippage": round(c["slippage"], 2),
                         "charges": round(c["charges"], 2), "net": round(net, 2),
                         "equity": round(cash, 2), **{f"ch_{k}": round(v, 2) for k, v in c["breakdown"].items()}})
    return {"rows": rows, "skipped": skipped, "equity": equity_pts}


def metrics(sim: dict) -> dict:
    rows = sim["rows"]
    eq = [e for _, e in sim["equity"]]
    peak, mdd, mdd_rs = eq[0], 0.0, 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak)
        mdd_rs = max(mdd_rs, peak - e)
    start, end = sim["equity"][0][0], sim["equity"][-1][0]
    years = (end - start).days / 365.25
    final = eq[-1]
    nets = [r["net"] for r in rows]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    tot = lambda k: round(sum(r[k] for r in rows), 2)
    return {
        "trades_taken": len(rows), "trades_skipped": len(sim["skipped"]),
        "final_equity": round(final, 2), "net_pnl": round(final - CAPITAL, 2),
        "return_pct": round((final / CAPITAL - 1) * 100, 2),
        "cagr_pct": round(((final / CAPITAL) ** (1 / years) - 1) * 100, 2) if final > 0 else None,
        "years": round(years, 2),
        "max_dd_pct": round(mdd * 100, 2), "max_dd_rs": round(mdd_rs, 2),
        "win_rate_pct": round(len(wins) / len(nets) * 100, 2),
        "avg_win": round(sum(wins) / len(wins), 2), "avg_loss": round(sum(losses) / len(losses), 2),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None,
        "gross": tot("gross"), "slippage": tot("slippage"), "charges": tot("charges"),
        "costs_pct_of_gross": round((tot("slippage") + tot("charges")) / tot("gross") * 100, 1),
        "charges_breakdown": {k[3:]: tot(k) for k in rows[0] if k.startswith("ch_") and k != "ch_total"},
        "worst_trade": min(nets), "best_trade": max(nets),
    }


def main():
    card = ChargeCard()
    trades = load()
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    summary = {}
    for sizing in ("fixed1", "compound"):
        for scen in SLIPPAGE:
            sim = simulate(trades, card, scen, sizing)
            m = metrics(sim)
            summary[f"{sizing}/{scen}"] = m
            with open(out_dir / f"ledger_{sizing}_{scen}.csv", "w", newline="") as fh:
                w = csv.DictWriter(fh, list(sim["rows"][0]))
                w.writeheader()
                w.writerows(sim["rows"])
            if sim["skipped"]:
                (out_dir / f"skipped_{sizing}_{scen}.json").write_text(json.dumps(sim["skipped"], indent=1))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))

    keys = ["trades_taken", "trades_skipped", "final_equity", "return_pct", "cagr_pct", "max_dd_pct",
            "win_rate_pct", "avg_win", "avg_loss", "profit_factor", "gross", "slippage", "charges",
            "costs_pct_of_gross", "worst_trade"]
    print(f"{'':16}" + "".join(f"{k:>16}" for k in summary))
    for k in keys:
        print(f"{k:16}" + "".join(f"{str(v[k]):>16}" for v in summary.values()))
    print("\ncharges breakdown, fixed1/base:", summary["fixed1/base"]["charges_breakdown"])
    print("charges breakdown, compound/base:", summary["compound/base"]["charges_breakdown"])


if __name__ == "__main__":
    main()
