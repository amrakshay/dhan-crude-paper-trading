"""STEP 2 -- turn the vendor's 5-minute bars into bars a backtest can trust.

Reads `data/raw/5m/*.csv.gz` (untouched vendor output), writes
    data/clean/5m/<SYMBOL>.parquet   bars on the valid days only
    data/clean/days.parquet          one row per (symbol, date): kept or why not
    out/clean_report.json            counts by reason, by symbol and by month

Every rule below was derived from the data, not assumed; CLEANING.md has the
evidence for each.

  1. Timestamps are floored to their 5-minute bucket. Dhan stamps a bar at the
     first trade INSIDE its bucket (a quiet 10:40-10:45 bucket is stamped
     10:41:00 and carries a few hundred shares), so flooring recovers the
     bucket exactly; on ~10-25% of pre-2022 days in every name this is most
     of what "off-grid" means.
  2. Two bars in one bucket after flooring: keep the one with more volume.
     The observed cases are single-share stray prints (2023-10-17 12:06,
     several symbols at once, 2-5% away from the real bar).
  3. Only buckets 09:15..15:25 are kept. That drops the post-close session
     (15:30-16:00, visible in Aug 2021), the trailing 15:30 closing print, and
     Muhurat evening sessions entirely.
  4. A bar with impossible OHLC (high < max(open, close), low > min(open,
     close), or a non-positive price) is dropped, never repaired.
  5. A day is kept only if it is on the Nifty 50's own calendar AND has a bar
     at or before 09:25 (the 09:30 decision needs the opening range) AND at
     least `--min-complete` of the buckets it should have. After the Closing
     Auction Session (first seen 2026-08-03) F&O names end at 15:10, so the
     expected count is 72 there and 75 before.
  6. Missing buckets are NOT filled. A bucket with no bar is a bucket with no
     trade; inventing a price for it is the thing this codebase refuses to do.
  7. Dhan's intraday bars are NOT adjusted for splits and bonuses (EICHERMOT
     reads 21,240 on 2020-08-21 and 2,199 on 2020-08-24, its 1:10 split).
     The application's `daily_bars` ARE adjusted, so the ratio
     adjusted_daily_close / intraday_close is flat between corporate actions
     and STEPS at each one -- including rights issues, whose ratios are not
     whole numbers. A step that persists (median of the next five days
     against the previous five differs by > `--ca-step`) is an event; earlier
     bars are rescaled by the segment's median ratio so every day is in
     today's units, volume inversely. A real news gap moves both series together and leaves the
     ratio flat, so it is not mistaken for one. The factor is stored per day
     (`adj`), so raw vendor prices are always recoverable as price / adj.
     An event within the first five days of a name's history cannot be
     measured, so those opening days are dropped rather than guessed.
  9. A single bar whose extreme is > `--glitch` beyond the day's NEXT most
     extreme bar is an isolated print. At 09:15/09:20 that is usually the
     opening auction and it is KEPT -- it is part of the opening range. Later
     in the session, two or more symbols sharing one at the SAME timestamp
     is either a market shock or a vendor glitch, and the INDEX tells them
     apart: the DHFL flash crash (2018-09-21 13:05), the COVID circuit
     reopenings, the 2024 and 2026 Budget speeches all move the Nifty bar by
     4-16x its day's median, while the 2021-08-03 15:20 cluster (nine names,
     inside the broken Aug 2021 period) moves it 1.5x. A shared print with
     the index bar under `--index-confirm` x its day median is a glitch and
     those symbol-days are dropped. KNOWN COST: a SECTOR shock the index does
     not feel (oil marketing companies on 2018-10-04) is dropped with them --
     a few dozen real days lost, against trading a fabricated breakout. A
     lone isolated print is kept and counted.
  8. Overnight moves above `--gap-flag`, measured AFTER adjustment, are
     flagged. They are real events, kept, and left for the backtest to judge.
     A gap is only measured against the PREVIOUS TRADING DAY on the index
     calendar. Dhan's intraday history has holes -- CGPOWER has nothing from
     2021-06-09 to 2021-09-10 or 2021-09-16 to 2022-01-03 although
     `daily_bars` shows it traded -- and a "gap" across a hole is months of
     movement, not an overnight one. Holes are counted per symbol.

Run with the experiment venv:
    .venv/bin/python scripts/clean_bars.py
"""
from __future__ import annotations

import argparse
import collections
import json
from datetime import date, time
from pathlib import Path

import sqlite3

import numpy as np
import pandas as pd

EXP = Path(__file__).resolve().parent.parent
RAW = EXP / "data" / "raw" / "5m"
CLEAN = EXP / "data" / "clean" / "5m"
OUT = EXP / "out"

FIRST, LAST = time(9, 15), time(15, 25)
OPENING_DEADLINE = time(9, 25)
CAS_START = date(2026, 8, 3)           # first 72-bar day seen (FEASIBILITY.md trap 5)
CAS_LAST = time(15, 10)
INDEX = "NIFTY50"
DB = EXP.parent.parent / "backend" / "data" / "paper_trading.db"


def adjusted_daily_closes() -> dict[str, pd.Series]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # CAST: SQLite returns whole-number NUMERICs as INTEGER (arbitrage METHOD.md).
    df = pd.read_sql_query(
        "SELECT symbol, bar_date, CAST(close AS REAL) AS close FROM daily_bars "
        "WHERE exchange_segment='NSE_EQ'", conn)
    df["bar_date"] = pd.to_datetime(df["bar_date"]).dt.date
    return {s: g.set_index("bar_date")["close"] for s, g in df.groupby("symbol")}


def corporate_action_factors(days: pd.DataFrame, daily: pd.Series | None, step: float,
                             window: int = 5):
    """Per-date multiplier taking raw intraday prices into today's units.

    r = adjusted_daily_close / raw_intraday_close is flat between corporate
    actions and steps at each. Splits and bonuses step by whole ratios; RIGHTS
    issues do not (BHARTIARTL: 0.902 -> 0.980 in 2019, 0.982 -> 1.000 in 2021),
    so ratios are NOT snapped to fractions -- the factor is the segment median
    itself. Medians, not single days, because `daily_bars` contains isolated
    days that were never adjusted (BHARTIARTL 2018-04-20 reads 402 between 346
    and 356); a median ignores one bad row where a day-on-day jump would not.

    Returns (factor Series indexed by date, list of events). No daily series:
    factor 1 everywhere, events None, reported as unadjustable.
    """
    dates = list(days["date"])
    if daily is None or daily.empty:
        return pd.Series(1.0, index=dates), None, []
    r = pd.Series(daily.reindex(dates).values / days["close"].values, index=dates).dropna()
    r = r[r > 0]
    n = len(r)
    if n < 4:
        return pd.Series(1.0, index=dates), [], []
    lr = np.log(r.values)

    def contrast(k):  # median after k minus median before k, windows truncated at the edges
        return np.median(lr[k:k + window]) - np.median(lr[max(0, k - window):k])

    c = np.array([0.0] + [contrast(k) for k in range(1, n)])
    events, k, unadjustable = [], 1, []
    while k < n:
        if abs(c[k]) > step:
            region = range(k, min(n, k + 2 * window))
            best = max(abs(c[j]) for j in region)
            # Candidate split points: every day holding at least half the peak
            # contrast. The first version demanded a tie within 0.001 of the
            # peak and put WIPRO's 2019 bonus, UPL's and SIEMENS' demerger one
            # day EARLY -- median noise (0.2881 vs 0.2855) beat the real jump,
            # which showed up as +32% / -24% "gaps" on consecutive days. A lone
            # bad daily row cannot win here: it jumps in and straight back out,
            # with little contrast either side.
            ties = [j for j in region if abs(c[j]) >= 0.5 * best]
            # Among them, the ex-date is where the ratio itself jumps.
            j = max(ties, key=lambda t: abs(lr[t] - lr[t - 1]))
            events.append(j)
            k = j + window
        else:
            k += 1

    def segments(evs):
        bounds = [0] + evs + [n]
        return bounds, [float(np.median(r.values[a:b])) for a, b in zip(bounds[:-1], bounds[1:])]

    while True:
        bounds, seg = segments(events)
        # (a) a split point that no longer separates two levels;
        weak = [i for i in range(len(events)) if abs(np.log(seg[i + 1] / seg[i])) < step]
        # (b) a segment too short to be a level (under `window` sessions) at
        #     either end -- one stray day must not rescale nine years;
        if not weak and len(events):
            if bounds[1] - bounds[0] < window:
                # Too few days BEFORE the first event to measure its level: the
                # step is real (CONCOR, day 3 of its history) but its factor is
                # unknowable, so those opening days are dropped, not guessed.
                unadjustable.extend(r.index[:bounds[1]])
                r, lr = r.iloc[bounds[1]:], lr[bounds[1]:]
                n = len(r)
                events = [e - bounds[1] for e in events[1:]]
                continue
            if bounds[-1] - bounds[-2] < window:
                weak = [len(events) - 1]
        if weak:
            events.pop(weak[0])
            continue
        # (c) two steps that cancel within a few weeks: a stretch where the two
        #     sources disagree (CGPOWER, March 2020), not two corporate actions.
        pair = [i for i in range(len(events) - 1)
                if abs(np.log(seg[i + 2] / seg[i])) < step
                and bounds[i + 2] - bounds[i + 1] <= 4 * window]
        if not pair:
            break
        del events[pair[0]:pair[0] + 2]
    bounds, seg = segments(events)
    per_obs = np.empty(n)
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        per_obs[a:b] = seg[i] / seg[-1]
    factor = pd.Series(per_obs, index=r.index).reindex(dates).ffill().bfill().fillna(1.0)

    def kind(x):
        simple = any(abs(p / q - x) / x < 0.005 for q in range(1, 11) for p in range(1, 51))
        return "split/bonus" if simple else "other (rights/dividend)"

    ev = [{"date": str(r.index[j]), "ratio": round(seg[i + 1] / seg[i], 4),
           "kind": kind(seg[i + 1] / seg[i])} for i, j in enumerate(events)]
    return factor, ev, unadjustable


def expected_buckets(day: date, last_bucket: time) -> int:
    """75 normally; 72 for a post-CAS day whose session ends at 15:10."""
    if day >= CAS_START and last_bucket <= CAS_LAST:
        return 72
    return 75


def isolated_extremes(bars: pd.DataFrame, threshold: float) -> list[tuple]:
    """(date, ts, isolation) for bars whose high/low stands alone that day."""
    found = []
    for day, g in bars.groupby("date"):
        if len(g) < 3:
            continue
        hi = g["high"].to_numpy()
        lo = g["low"].to_numpy()
        h2, l2 = np.sort(hi)[-2], np.sort(lo)[1]
        up = hi.max() / h2 - 1 if h2 > 0 else 0.0
        dn = 1 - lo.min() / l2 if l2 > 0 else 0.0
        if max(up, dn) > threshold:
            at = g["ts"].iloc[int(np.argmax(hi) if up > dn else np.argmin(lo))]
            found.append((day, at, round(max(up, dn), 4)))
    return found


OPENING_BUCKETS = {time(9, 15), time(9, 20)}


def load_raw(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path)
    ts = pd.to_datetime(d["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    d = d.drop(columns="time")
    d.insert(0, "ts_raw", ts)
    return d


def clean_symbol(raw: pd.DataFrame, calendar: set | None, min_complete: float, gap_flag: float,
                 daily: pd.Series | None = None, ca_step: float = 0.0075,
                 prev_day: dict | None = None):
    stats = collections.Counter()
    stats["bars_raw"] = len(raw)
    d = raw.copy()
    d["ts"] = d["ts_raw"].dt.floor("5min")
    stats["bars_offgrid_floored"] = int((d["ts"] != d["ts_raw"]).sum())

    # Rule 2: collisions -- keep the higher-volume bar.
    d["volume"] = d["volume"].fillna(0.0)
    d = d.sort_values(["ts", "volume"], ascending=[True, False])
    dup = d.duplicated("ts", keep="first")
    stats["bars_collision_dropped"] = int(dup.sum())
    d = d[~dup]

    # Rule 3: session buckets only.
    tod = d["ts"].dt.time
    in_session = (tod >= FIRST) & (tod <= LAST)
    stats["bars_outside_session"] = int((~in_session).sum())
    d = d[in_session]

    # Rule 4: impossible OHLC.
    bad = (
        (d["high"] < d[["open", "close"]].max(axis=1))
        | (d["low"] > d[["open", "close"]].min(axis=1))
        | (d[["open", "high", "low", "close"]] <= 0).any(axis=1)
    )
    stats["bars_bad_ohlc"] = int(bad.sum())
    d = d[~bad].drop(columns="ts_raw")

    d["date"] = d["ts"].dt.date
    days = []
    for day, g in d.groupby("date", sort=True):
        first, last = g["ts"].iloc[0].time(), g["ts"].iloc[-1].time()
        exp = expected_buckets(day, last)
        n = len(g)
        reason = None
        if calendar is not None and day not in calendar:
            reason = "not_on_index_calendar"
        elif first > OPENING_DEADLINE:
            reason = "no_opening_range"
        elif n < min_complete * exp:
            reason = "incomplete"
        days.append({
            "date": day, "n_bars": n, "expected": exp, "first": first.strftime("%H:%M"),
            "last": last.strftime("%H:%M"), "open": float(g["open"].iloc[0]),
            "close": float(g["close"].iloc[-1]),
            "turnover_cr": float((g["close"] * g["volume"]).sum() / 1e7),
            "zero_volume_bars": int((g["volume"] == 0).sum()),
            "valid": reason is None, "reason": reason or "kept",
        })
    days = pd.DataFrame(days)
    if days.empty:
        return d.iloc[0:0], days, stats, None
    # Rule 7: corporate actions, from the valid days only.
    v = days[days["valid"]].copy()
    factor, events, unadjustable = corporate_action_factors(v, daily, ca_step)
    if unadjustable:
        hit = days["date"].isin(unadjustable)
        days.loc[hit, ["valid", "reason"]] = [False, "unadjustable_before_first_action"]
        v = v[~v["date"].isin(unadjustable)]
    days["adj"] = days["date"].map(factor).fillna(1.0)
    # Rule 8: overnight gap on ADJUSTED prices, against the previous valid day.
    v["gap"] = (v["open"] * v["date"].map(factor)) / (v["close"] * v["date"].map(factor)).shift(1) - 1.0
    if prev_day is not None:
        adjacent = v["date"].map(prev_day) == v["date"].shift(1)
        v.loc[~adjacent, "gap"] = np.nan
    days = days.merge(v[["date", "gap"]], on="date", how="left")
    days["gap_flag"] = days["gap"].abs() > gap_flag

    keep = set(days.loc[days["valid"], "date"])
    d = d[d["date"].isin(keep)].copy()
    f = d["date"].map(dict(zip(days["date"], days["adj"])))
    for col in ("open", "high", "low", "close"):
        d[col] = d[col] * f
    d["volume"] = d["volume"] / f
    d["bar"] = ((d["ts"].dt.hour * 60 + d["ts"].dt.minute) - (9 * 60 + 15)) // 5
    for reason, count in days["reason"].value_counts().items():
        stats[f"days_{reason}"] = int(count)
    stats["days_gap_flagged"] = int(days["gap_flag"].sum())
    stats["corporate_actions"] = len(events or [])
    if calendar is not None and keep:
        span = [c for c in calendar if min(keep) <= c <= max(keep)]
        stats["days_missing_in_history"] = len(span) - len(keep)
    return d[["ts", "date", "bar", "open", "high", "low", "close", "volume"]], days, stats, events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-complete", type=float, default=0.9,
                    help="fraction of expected buckets a day must have (default 0.9)")
    ap.add_argument("--ca-step", type=float, default=0.0075,
                    help="persistent log-step in the daily/intraday ratio that marks a "
                         "corporate action (default 0.0075)")
    ap.add_argument("--glitch", type=float, default=0.03,
                    help="isolation beyond the next-most-extreme bar that marks a lone print")
    ap.add_argument("--index-confirm", type=float, default=3.0,
                    help="index bar range, as a multiple of its day's median, that makes a "
                         "shared isolated print a real market move (default 3)")
    ap.add_argument("--only", nargs="*", help="restrict to these symbols (report still written)")
    ap.add_argument("--gap-flag", type=float, default=0.15,
                    help="flag overnight moves larger than this (default 15%%)")
    args = ap.parse_args()

    files = sorted(RAW.glob("*.csv.gz"))
    if not any(f.name == f"{INDEX}.csv.gz" for f in files):
        print(f"{INDEX} is not downloaded yet; its calendar is what defines a trading day.")
        return 2
    CLEAN.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # The index first: its valid days ARE the trading calendar.
    _, idx_days, _, _ = clean_symbol(load_raw(RAW / f"{INDEX}.csv.gz"), None,
                                     args.min_complete, args.gap_flag)
    calendar = set(idx_days.loc[idx_days["valid"], "date"])
    ordered = sorted(calendar)
    prev_day = dict(zip(ordered[1:], ordered[:-1]))

    daily = adjusted_daily_closes()
    if args.only:
        files = [f for f in files if f.name[: -len(".csv.gz")] in set(args.only)]
    report, all_days, by_month = {}, [], collections.Counter()
    isolated = []   # (symbol, date, ts, isolation), mid-session only
    for f in files:
        sym = f.name[: -len(".csv.gz")]
        raw = load_raw(f)
        bars, days, stats, events = clean_symbol(
            raw, None if sym == INDEX else calendar, args.min_complete, args.gap_flag,
            None if sym == INDEX else daily.get(sym), args.ca_step, prev_day)
        bars.to_parquet(CLEAN / f"{sym}.parquet", index=False)
        if sym != INDEX:
            for day, at, iso in isolated_extremes(bars, args.glitch):
                if at.time() not in OPENING_BUCKETS:
                    isolated.append((sym, day, at, iso))
        days.insert(0, "symbol", sym)
        all_days.append(days)
        report[sym] = dict(stats) | {
            "first_day": str(days["date"].min()) if len(days) else None,
            "valid_days": int(days["valid"].sum()) if len(days) else 0,
            "corporate_action_events": events,   # None = no daily_bars to adjust against
        }
        for _, r in days[~days["valid"]].iterrows():
            by_month[(r["reason"], str(r["date"])[:7])] += 1
        print(f"{sym:12s} {report[sym]['valid_days']:5d} valid days, "
              f"{stats['bars_offgrid_floored']:6d} floored, {stats['bars_collision_dropped']} collisions, "
              f"{stats.get('days_incomplete', 0)} incomplete, "
              f"{stats.get('days_missing_in_history', 0)} missing, "
              f"CA {events if events is not None else 'UNADJUSTABLE'}", flush=True)

    days = pd.concat(all_days, ignore_index=True)
    # Rule 9, across symbols: a shared timestamp is a vendor glitch.
    by_ts = collections.defaultdict(list)
    for sym, day, at, iso in isolated:
        by_ts[at].append((sym, day, iso))
    idx = pd.read_parquet(CLEAN / f"{INDEX}.parquet")
    idx_rng = (idx["high"] - idx["low"]) / idx["open"]
    idx_mult = (idx_rng / idx_rng.groupby(idx["date"]).transform("median")).set_axis(idx["ts"])
    shared = {at: hits for at, hits in by_ts.items() if len({h[0] for h in hits}) >= 2}
    market_moves = {at: hits for at, hits in shared.items()
                    if float(idx_mult.get(at, 0.0)) >= args.index_confirm}
    glitches = {at: hits for at, hits in shared.items() if at not in market_moves}
    drop = collections.defaultdict(set)
    for hits in glitches.values():
        for sym, day, _ in hits:
            drop[sym].add(day)
    for sym, dates in drop.items():
        hit = (days["symbol"] == sym) & days["date"].isin(dates)
        days.loc[hit, ["valid", "reason"]] = [False, "vendor_glitch_print"]
        path = CLEAN / f"{sym}.parquet"
        b = pd.read_parquet(path)
        b[~b["date"].isin(dates)].to_parquet(path, index=False)
        report[sym]["valid_days"] -= len(dates)
        report[sym]["days_vendor_glitch_print"] = len(dates)
        report[sym]["days_kept"] = report[sym].get("days_kept", 0) - len(dates)
    days.to_parquet(EXP / "data" / "clean" / "days.parquet", index=False)
    months = collections.defaultdict(dict)
    for (reason, month), n in sorted(by_month.items()):
        months[reason][month] = n
    totals = collections.Counter()
    for s in report.values():
        totals.update({k: v for k, v in s.items() if isinstance(v, int) and not isinstance(v, bool)})
    (OUT / "clean_report.json").write_text(json.dumps({
        "params": vars(args), "calendar_days": len(calendar),
        "calendar_range": [str(min(calendar)), str(max(calendar))],
        "totals": dict(totals), "dropped_days_by_month": months,
        "glitch_timestamps": {str(at): [(h[0], h[2]) for h in hits] for at, hits in sorted(glitches.items())},
        "shared_prints_kept_as_market_moves": {str(at): len(h) for at, h in sorted(market_moves.items())},
        "lone_isolated_prints_kept": sum(1 for at in by_ts if at not in shared),
        "symbols": report,
    }, indent=1))
    print(f"\n{len(files)} symbols, {len(calendar)} calendar days. Totals: {dict(totals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
