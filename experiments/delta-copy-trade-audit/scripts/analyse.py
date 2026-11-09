"""Net a copy-trading leader's closed trades of their exchange's trading fees.

Two leaders, one method. Each is a LEADERS entry naming its export in data/,
its exchange's fee schedule and what its export can and cannot be trusted
for. For each, writes to out/leader-<id>/:

  trades_net.csv   one row per trade: gross, entry fee, exit fee, net
  monthly.csv      realised P&L by month, gross and net
  summary.json     the headline numbers the dashboard shows
  dashboard.html   the dashboard itself (self-contained apart from Chart.js)

Assumptions, each chosen by the user on 2026-09-28:
  * every fill pays the TAKER rate (an automated bot mostly sends market
    orders); the maker rate is computed alongside as the best case;
  * the leader's quantities are used as-is; $500 is only the starting equity;
  * exact-duplicate rows are dropped (the total with them is reported);
  * perpetual FUNDING is not in either export and is NOT modelled.

Run from this experiment's directory:
    ../hedged-option-selling/.venv/bin/python scripts/analyse.py [6001|6003]
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

START_CAPITAL = 500.0
GST = 0.18
OPTION_RE = re.compile(r"^(?P<cp>[CP])-(?P<und>[A-Z]+)-(?P<strike>[\d.]+)-(?P<exp>\d{6})$")


@dataclass(frozen=True)
class Leader:
    key: str
    source: str
    venue: str
    # kind -> {"taker": rate, "maker": rate}, fractions of notional per side
    rates: dict
    classify: callable
    option_premium_cap: float | None = None
    # The export's Exit Price is the price the position closed at. False for
    # 6003, whose column is on the losing side of entry on every row.
    exit_price_usable: bool = True
    # The export records distinct entry and exit times. False for 6003.
    has_hold_times: bool = True
    notes: list = field(default_factory=list)


def classify_delta(df: pd.DataFrame, parts: pd.DataFrame) -> pd.Series:
    kind = pd.Series("Future", index=df.index)
    kind[df["Symbol"].isin({"XAUTUSD", "PAXGUSD", "SLVONUSD"})] = "Metal future"
    kind[parts["cp"] == "C"] = "Call option"
    kind[parts["cp"] == "P"] = "Put option"
    return kind


SHARK_COMMODITIES = {"XAUUSDT", "XAGUSDT", "XPTUSDT", "PAXGUSDT", "NATGASUSDT", "BZUSDT", "CLUSDT"}


def classify_shark(df: pd.DataFrame, parts: pd.DataFrame) -> pd.Series:
    kind = pd.Series("Stock perp", index=df.index)
    kind[df["Symbol"].isin(SHARK_COMMODITIES)] = "Commodity perp"
    return kind


LEADERS = {
    # Delta Exchange India, https://www.delta.exchange/fees and support article
    # 80001177864, read 2026-09-28. The CURRENT schedule is applied back to
    # 2024 -- historical rates were not published. Futures rates VERIFIED on
    # both pages; options maker 0.01% is on the fees page and in the worked
    # example, but other Delta pages say 0.03% (unverified).
    "6001": Leader(
        key="6001",
        source="leader_6001_closed_trades_1790595548209.csv",
        venue="Delta Exchange India",
        rates={
            "Future": {"taker": 0.0005, "maker": 0.0002},
            "Metal future": {"taker": 0.0005, "maker": 0.0001},
            "Call option": {"taker": 0.0003, "maker": 0.0001},
            "Put option": {"taker": 0.0003, "maker": 0.0001},
        },
        classify=classify_delta,
        option_premium_cap=0.035,
    ),
    # Shark Exchange, https://sharkexchange.in/fee-structure, read 2026-09-28:
    # futures 0.016% maker / 0.040% taker, one rate for every trader. The page
    # does not say whether stock and commodity perps use the same rate as
    # crypto; they are assumed to (UNVERIFIED).
    "6003": Leader(
        key="6003",
        source="leader_6003_closed_trades_1790596642637.csv",
        venue="Shark Exchange",
        rates={
            "Stock perp": {"taker": 0.0004, "maker": 0.00016},
            "Commodity perp": {"taker": 0.0004, "maker": 0.00016},
        },
        classify=classify_shark,
        exit_price_usable=False,
        has_hold_times=False,
    ),
}


def load(cfg: Leader) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(ROOT / "data" / cfg.source)
    dupes = raw[raw.duplicated(keep="first")]
    df = raw.drop_duplicates().reset_index(drop=True)
    df["entry_time"] = pd.to_datetime(df["Entry Time"])
    df["exit_time"] = pd.to_datetime(df["Exit Time"])
    df["sign"] = df["Side"].map({"Buy": 1, "Short": -1})

    parts = df["Symbol"].str.extract(OPTION_RE)
    df["is_option"] = parts["cp"].notna()
    df["strike"] = pd.to_numeric(parts["strike"])
    df["expiry"] = pd.to_datetime(parts["exp"], format="%d%m%y")
    df["kind"] = cfg.classify(df, parts)

    # Exit at 17:30 on expiry day is Delta's settlement, not a trade.
    df["settled_at_expiry"] = (
        df["is_option"]
        & (df["exit_time"].dt.normalize() == df["expiry"])
        & (df["exit_time"].dt.strftime("%H:%M") == "17:30")
    )

    # The price the move implies, from the export's own P&L. Where it is wildly
    # away from entry, the row's Qty cannot be the quantity that made the P&L.
    df["implied_exit"] = df["Entry Price"] + df["PnL"] / (df["sign"] * df["Qty"])
    df["implied_move"] = df["sign"] * (df["implied_exit"] / df["Entry Price"] - 1)
    df["qty_mismatch"] = ~df["is_option"] & (df["implied_move"].abs() > 0.20)

    # Exit-side fees need an exit price. Where the export's is not one, the
    # entry price stands in: the trades are small moves, so the error is small
    # -- except on qty_mismatch rows, where the fee is understated.
    df["fee_exit_price"] = df["Exit Price"] if cfg.exit_price_usable else df["Entry Price"]
    meta = {
        "rows_in_file": len(raw),
        "duplicates_dropped": len(dupes),
        "duplicate_gross": float(dupes["PnL"].sum()),
    }
    return df, meta


def side_fee(df: pd.DataFrame, price_col: str, cfg: Leader, which: str) -> pd.Series:
    """Fee for one side of each trade, GST included."""
    qty = df["Qty"]
    price = df[price_col]
    rate = df["kind"].map({k: v[which] for k, v in cfg.rates.items()})
    fee = rate * qty * price
    if cfg.option_premium_cap is not None:
        # Options: notional is the UNDERLYING's spot x qty. The export has no
        # spot, so the strike stands in for it; the premium cap binds on most
        # rows anyway (see README), which makes the proxy matter little.
        opt = df["is_option"]
        opt_fee = pd.concat([rate * qty * df["strike"], cfg.option_premium_cap * qty * price],
                            axis=1).min(axis=1)
        fee = fee.where(~opt, opt_fee)
    return fee * (1 + GST)


def net_trades(df: pd.DataFrame, cfg: Leader) -> pd.DataFrame:
    out = df.copy()
    for which in ("taker", "maker"):
        out[f"entry_fee_{which}"] = side_fee(out, "Entry Price", cfg, which)
        exit_fee = side_fee(out, "fee_exit_price", cfg, which)
        # Settlement is charged at the taker rate whichever schedule is in force;
        # an option settling worthless pays nothing (price 0 -> cap 0).
        if which == "maker":
            exit_fee = exit_fee.where(~out["settled_at_expiry"],
                                      side_fee(out, "fee_exit_price", cfg, "taker"))
        out[f"exit_fee_{which}"] = exit_fee
        out[f"fees_{which}"] = out[f"entry_fee_{which}"] + out[f"exit_fee_{which}"]
        out[f"net_{which}"] = out["PnL"] - out[f"fees_{which}"]
    if cfg.option_premium_cap is not None:
        taker_opt = out["kind"].map({k: v["taker"] for k, v in cfg.rates.items()})
        out["cap_bound"] = out["is_option"] & (
            cfg.option_premium_cap * out["Entry Price"] < taker_opt * out["strike"])
    else:
        out["cap_bound"] = False
    return out.sort_values(["exit_time", "entry_time"]).reset_index(drop=True)


def equity_curve(t: pd.DataFrame, col: str) -> pd.DataFrame:
    eq = START_CAPITAL + t[col].cumsum()
    peak = eq.cummax().clip(lower=START_CAPITAL)
    return pd.DataFrame({"time": t["exit_time"], "equity": eq, "drawdown": eq - peak})


def _peak(t: pd.DataFrame, amount: pd.Series) -> tuple[float, str]:
    ev = pd.concat([
        pd.DataFrame({"t": t["entry_time"], "d": amount}),
        pd.DataFrame({"t": t["exit_time"], "d": -amount}),
    ]).sort_values(["t", "d"])  # exits before entries at the same second
    running = ev["d"].cumsum()
    i = running.values.argmax()
    return float(running.iloc[i]), str(ev["t"].iloc[i])


def peak_exposure(t: pd.DataFrame) -> dict:
    """Largest concurrent exposure, kept apart by what it costs to carry.

    Futures: qty x entry price. Short options: qty x strike -- the underlying
    written, which is what the margin is held against. Long options: premium
    paid, which is all they can lose. No margin model is applied, so these are
    exposures, not margin.
    """
    zero = pd.Series(0.0, index=t.index)
    fut = (t["Qty"] * t["Entry Price"]).where(~t["is_option"], zero)
    short_opt = (t["Qty"] * t["strike"]).where(t["is_option"] & (t["Side"] == "Short"), zero)
    long_opt = (t["Qty"] * t["Entry Price"]).where(t["is_option"] & (t["Side"] == "Buy"), zero)
    ones = pd.Series(1.0, index=t.index)
    out = {}
    for key, amount in [("futures_notional", fut), ("short_option_notional", short_opt),
                        ("long_option_premium", long_opt), ("open_positions", ones)]:
        value, at = _peak(t, amount)
        out[f"peak_{key}"], out[f"peak_{key}_at"] = value, at
    return out


def trade_sizes(t: pd.DataFrame) -> dict:
    """Per-trade size, for an export whose hold times make concurrency unknowable.

    If the Exit Price column is the stop-loss -- which is what it looks like:
    below entry on every Buy, above it on every Short -- then qty x the gap is
    what each trade risked. That is a reading of the export, not a fact in it.
    """
    notional = t["Qty"] * t["Entry Price"]
    risk = (t["Qty"] * (t["Entry Price"] - t["Exit Price"]).abs())
    winners = t.loc[t["PnL"] > 0, "PnL"]
    return {
        "median_trade_notional": float(notional.median()),
        "max_trade_notional": float(notional.max()),
        "stop_on_losing_side": int(((t["sign"] * (t["Exit Price"] - t["Entry Price"])) < 0).sum()),
        "median_stop_distance_pct": float(((t["Entry Price"] - t["Exit Price"]).abs() / t["Entry Price"]).median() * 100),
        "median_risk_at_stop": float(risk.median()),
        "median_win": float(winners.median()),
        "qty_mismatch_rows": int(t["qty_mismatch"].sum()),
    }


def monthly(t: pd.DataFrame) -> pd.DataFrame:
    m = t.groupby(t["exit_time"].dt.to_period("M")).agg(
        trades=("PnL", "size"), gross=("PnL", "sum"),
        fees=("fees_taker", "sum"), net=("net_taker", "sum"),
        net_maker=("net_maker", "sum"),
        wins=("net_taker", lambda s: int((s > 0).sum())),
    )
    full = pd.period_range(m.index.min(), m.index.max(), freq="M")
    m = m.reindex(full, fill_value=0)
    m["start_equity"] = START_CAPITAL + m["net"].cumsum().shift(fill_value=0)
    m["end_equity"] = m["start_equity"] + m["net"]
    m["return_pct"] = (m["net"] / m["start_equity"] * 100).where(m["start_equity"] > 0)
    m.index = m.index.astype(str)
    m.index.name = "month"
    return m


def summarise(t: pd.DataFrame, meta: dict, m: pd.DataFrame, cfg: Leader) -> dict:
    eq = equity_curve(t, "net_taker")
    by_kind = t.groupby("kind").agg(
        trades=("PnL", "size"), gross=("PnL", "sum"),
        fees=("fees_taker", "sum"), net=("net_taker", "sum"),
    ).reset_index().to_dict("records")
    opts = t[t["is_option"]]
    s = {
        **meta,
        "leader": cfg.key,
        "venue": cfg.venue,
        "trades": len(t),
        "first_entry": str(t["entry_time"].min()),
        "last_exit": str(t["exit_time"].max()),
        "start_capital": START_CAPITAL,
        "gross": float(t["PnL"].sum()),
        "fees_taker": float(t["fees_taker"].sum()),
        "net_taker": float(t["net_taker"].sum()),
        "fees_maker": float(t["fees_maker"].sum()),
        "net_maker": float(t["net_maker"].sum()),
        "end_equity": float(START_CAPITAL + t["net_taker"].sum()),
        "min_equity": float(eq["equity"].min()),
        "min_equity_at": str(eq.loc[eq["equity"].idxmin(), "time"]),
        "max_drawdown": float(eq["drawdown"].min()),
        "max_drawdown_at": str(eq.loc[eq["drawdown"].idxmin(), "time"]),
        "win_rate_gross": float((t["PnL"] > 0).mean()),
        "win_rate_net": float((t["net_taker"] > 0).mean()),
        "flipped_to_loss": int(((t["PnL"] > 0) & (t["net_taker"] <= 0)).sum()),
        "best_trade": float(t["net_taker"].max()),
        "worst_trade": float(t["net_taker"].min()),
        "options": len(opts),
        "options_cap_bound": int(opts["cap_bound"].sum()),
        "options_settled": int(opts["settled_at_expiry"].sum()),
        "empty_months": int((m["trades"] == 0).sum()),
        "by_kind": by_kind,
        **trade_sizes(t),
    }
    if cfg.has_hold_times:
        s.update(peak_exposure(t))
    return s


def usd(v: float, d: int = 0) -> str:
    return ("−$" if v < 0 else "$") + f"{abs(v):,.{d}f}"


def exposure_html(s: dict, cfg: Leader) -> str:
    if cfg.has_hold_times:
        return f"""<h2>⚠ Could $500 actually have carried these trades?</h2>
  <div>Very unlikely. This P&amp;L comes from the leader's own position sizes, and at times those were far larger than $500:</div>
  <ul>
    <li>Peak open futures notional <b>{usd(s['peak_futures_notional'])}</b> ({s['peak_futures_notional_at'][:16]}), {s['peak_futures_notional'] / START_CAPITAL:.0f}× the starting $500.</li>
    <li>Peak underlying written in short options <b>{usd(s['peak_short_option_notional'])}</b> ({s['peak_short_option_notional_at'][:16]}), {s['peak_short_option_notional'] / START_CAPITAL:.0f}× the starting $500.</li>
    <li>Up to <b>{s['peak_open_positions']:.0f}</b> positions open at the same time.</li>
  </ul>
  <div style="margin-top:6px">These are exposures, not margin: the exchange's short-option margin wasn't modelled. Treat the dollar figures above as "what the leader made", not "what $500 would have made". A scaled-down copy would pay fees at the same % rates, but the lot minimums would round many small trades away.</div>"""
    return f"""<h2>⚠ This export can't be fully trusted: read this first</h2>
  <ul>
    <li><b>Entry time equals exit time on every row.</b> How long trades were held, and how many were open at once, can't be known from this file, so the exposure against $500 can't be checked. The equity curve is plotted at the entry time.</li>
    <li><b>The "Exit Price" column is not the exit.</b> On {s['stop_on_losing_side']} of {s['trades']} rows it sits on the losing side of entry (below it for a Buy, above it for a Short), even though {s['win_rate_gross'] * 100:.0f}% of trades made money. It looks like the <b>stop-loss</b>, a median {s['median_stop_distance_pct']:.1f}% from entry. Exit fees are therefore charged at the entry price.</li>
    <li><b>If it is the stop,</b> a typical trade risked <b>{usd(s['median_risk_at_stop'], 2)}</b> to make <b>{usd(s['median_win'], 2)}</b>. That is how a {s['win_rate_gross'] * 100:.0f}% win rate is usually built: many small wins and occasional large stops. Only {s['trades']} trades over {s['first_entry'][:7]} to {s['last_exit'][:7]} is too short a sample to show how often the large losses come.</li>
    <li><b>On {s['qty_mismatch_rows']} rows the Qty can't have made the P&amp;L</b> (it would need a move of more than 20% in one trade). The quantity actually traded was larger, so those rows' fees are understated.</li>
    <li>Trade sizes: median {usd(s['median_trade_notional'])} notional, largest {usd(s['max_trade_notional'])}. That is within reach of $500 at modest leverage, unlike leader 6001.</li>
  </ul>"""


def notes_html(s: dict, cfg: Leader) -> list[str]:
    rate_lines = ", ".join(
        f"{k.lower()} {v['taker'] * 100:.3f}% taker / {v['maker'] * 100:.3f}% maker"
        for k, v in cfg.rates.items())
    notes = [f"<b>Fees:</b> {cfg.venue}'s current schedule (read 2026-09-28): {rate_lines}, of notional, on each side, plus 18% GST on the fee. Taker on both sides is the headline; maker is the best case."]
    if cfg.option_premium_cap is not None:
        notes.append(f"<b>Options:</b> fee capped at {cfg.option_premium_cap * 100:.1f}% of premium. The export has no spot price, so the strike stands in for spot. On {s['options_cap_bound']} of {s['options']} option trades the premium cap is the fee anyway. {s['options_settled']} options were settled at 17:30 on expiry day, and a worthless settlement pays no fee.")
        notes.append("The current schedule is applied back to 2024. Historical rates weren't published, and the fees page shows a 10% option cap struck through to 3.5%.")
        notes.append("<b>Gross P&amp;L</b> is the export's own figure. It matches qty × price move on every row except 9 micro-price coins (PEPE, NEIRO…), whose prices are rounded in the export.")
    else:
        notes.append("Shark's fee page doesn't say whether stock and commodity perps are charged the crypto futures rate; they are assumed to be (unverified). Its zero-fee scalping offer, which waives the closing fee within 30 minutes on \"select\" pairs, is <b>not</b> applied: hold times aren't in the export.")
        notes.append("<b>Gross P&amp;L</b> is the export's own figure. It can't be reconciled against the prices on any row, because the Exit Price column isn't an exit.")
    notes.append("<b>Funding is not included.</b> Perpetual funding payments aren't in the export and can move the result either way.")
    notes.append(f"<b>Duplicates:</b> {s['duplicates_dropped']} exact-duplicate rows dropped (their gross {usd(s['duplicate_gross'], 2)}).")
    notes.append(f"{s['empty_months']} calendar months in the range have no closed trades. Times are as exported (IST for Delta; unverified for Shark).")
    return notes


def main(key: str) -> None:
    cfg = LEADERS[key]
    out_dir = ROOT / "out" / f"leader-{key}"
    out_dir.mkdir(parents=True, exist_ok=True)
    df, meta = load(cfg)
    t = net_trades(df, cfg)
    m = monthly(t)
    s = summarise(t, meta, m, cfg)

    cols = ["Symbol", "kind", "Side", "Qty", "Entry Price", "Exit Price",
            "entry_time", "exit_time", "PnL", "entry_fee_taker", "exit_fee_taker",
            "fees_taker", "net_taker", "net_maker", "settled_at_expiry", "qty_mismatch"]
    t[cols].to_csv(out_dir / "trades_net.csv", index=False, float_format="%.6f")
    m.to_csv(out_dir / "monthly.csv", float_format="%.4f")
    (out_dir / "summary.json").write_text(json.dumps(s, indent=2, default=str))

    eq = equity_curve(t, "net_taker")
    eq_gross = equity_curve(t, "PnL")
    payload = {
        "meta": {
            "title": f"Leader {key} on {cfg.venue}",
            "time_label": "by trade close" if cfg.has_hold_times else "by trade time (the export has no separate exit time)",
            "exit_header": "Exit" if cfg.exit_price_usable else "“Exit” (export; likely the stop)",
            "exposure_html": exposure_html(s, cfg),
            "notes": notes_html(s, cfg),
        },
        "summary": s,
        "monthly": m.reset_index().to_dict("records"),
        "equity": {
            "t": eq["time"].dt.strftime("%Y-%m-%d %H:%M").tolist(),
            "net": eq["equity"].round(2).tolist(),
            "gross": eq_gross["equity"].round(2).tolist(),
        },
        "trades": [
            [r.Symbol, r.kind, r.Side, r.Qty, r._4, r._5,
             r.entry_time.strftime("%Y-%m-%d %H:%M"), r.exit_time.strftime("%Y-%m-%d %H:%M"),
             round(r.PnL, 4), round(r.fees_taker, 4), round(r.net_taker, 4),
             bool(r.settled_at_expiry)]
            for r in t[cols].itertuples(index=False)
        ],
    }
    template = (ROOT / "scripts" / "dashboard_template.html").read_text()
    (out_dir / "dashboard.html").write_text(
        template.replace("/*__DATA__*/null", json.dumps(payload, default=str))
    )
    print(json.dumps({k: v for k, v in s.items() if k != "by_kind"}, indent=2, default=str))
    print(pd.DataFrame(s["by_kind"]).to_string(index=False))
    print(m[["trades", "gross", "fees", "net", "end_equity", "return_pct"]].round(2).to_string())


if __name__ == "__main__":
    for key in (sys.argv[1:] or LEADERS):
        main(key)
