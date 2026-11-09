"""C4 engine + a bear-market leaders pot.

Copy-and-extend of pullback/backend/intrday_test_strategy/research2/gate_run/
engine.py::run (itself a copy of swing_backtest_v2.run_variant). The MAIN-book
code path is kept line-for-line with the original so that `pot_frac = 0`
reproduces spec 9.3 V0 to the rupee (tests/test_pot.py and
scripts/run_pot.py both assert it). Only the V0 knob values of the original
`G` dataclass are supported; the off-gate variants of section 11 are not
needed here and were removed to keep the main path short.

THE POT (pre-registered in README.md):
  * Active only while the NIFTY 200-SMA gate is OFF. At the first gate-OFF
    close t it is allotted pot_frac x combined equity(t); the transfer happens
    at the open of t+1, after the main book's liquidation fills, capped at the
    main book's cash.
  * Weekly ranking (same rebalance day as the main book) when the gate is OFF
    at that close. Eligible: P2, P3, P4, mom126 > 10%, mom126 > NIFTY mom126,
    ret63 > NIFTY ret63. Ranked by P7 score. Top pot_slots queued.
  * Exits: 3.5xATR chandelier (as P14/P15), pot rank > 15 / not eligible,
    and FORCED liquidation at the open after the first gate-ON close, before
    any main-book entry that same open. Its cash then rejoins the main book.

Ledgers: main_cash and pot_cash are separate; combined equity = both cash
balances + both books marked at the close. Signals at close t, fills at open
t+1, everywhere in the pot.

Known characteristic of the ORIGINAL main-book code, kept verbatim so V0
reproduces: the entry cap at the open of t+1 reads the gate at the CLOSE of
t+1 (`g_on` of the fill day). It can only BLOCK an entry (cap 0 when that
day's close is gate-off). The pot does not do this.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import ADV_MIN, MAX_POS, PRICE_MIN, SLIP, START_EQUITY, buy_cost, sell_cost


@dataclass
class Cfg:
    name: str = "V0"
    # main book: C4 / V0 fixed parts
    regime_sma: int = 200
    mom_floor: float = 0.10
    trail_atr: float = 3.5
    exit_rank: int = 15
    # pot
    pot_frac: float = 0.0          # 0 => pot disabled => exactly V0
    pot_slots: int = 5
    pot_exit_rank: int = 15
    pot_mom_floor: float = 0.10
    pot_rs126: bool = True
    pot_rs63: bool = True
    # evaluation
    end_date: str | None = None    # truncate the calendar (inclusive)
    # DIAGNOSTIC ONLY (not part of V0 or the pot): make the main-book entry cap
    # read the gate at the SIGNAL close (t) instead of the fill day's close (t+1)
    main_cap_prev_gate: bool = False


def run(syms: dict, index: pd.DataFrame, P: Cfg) -> dict:
    ix = index.copy().sort_values("date").reset_index(drop=True)
    ix["sma_g"] = ix.close.rolling(P.regime_sma).mean()
    ix["ret63"] = ix.close.pct_change(63)
    ix["mom126"] = ix.close.shift(5) / ix.close.shift(126) - 1
    if P.end_date is not None:
        ix = ix[ix.date <= P.end_date].reset_index(drop=True)
    cal = ix.date.tolist()
    gate_on = dict(zip(ix.date, ix.close > ix.sma_g))
    fast_ok = dict(zip(ix.date, ix.ret63 > 0))
    nifty_mom = dict(zip(ix.date, ix.mom126))
    nifty_r63 = dict(zip(ix.date, ix.ret63))

    idx = {s: {dt: i for i, dt in enumerate(d.date)} for s, d in syms.items()}
    for d in syms.values():
        d["mom"] = d["mom126"]
        d["atrpct"] = d.atr14 / d.close

    trades, curve, daily, events = [], [], [], []
    equity = START_EQUITY
    main_cash = START_EQUITY
    pos: dict[str, dict] = {}
    pending_entries: list[str] = []
    pending_exits: set[str] = set()
    rot_rank: dict[str, int] = {}
    slots_allowed = MAX_POS
    prev_g_on = False

    # pot state
    pot_on = P.pot_frac > 0
    pot_cash = 0.0
    pot_active = False
    pot_activate_amt: float | None = None   # set at close t, executed at open t+1
    pot_liquidate = False                   # set at close t, executed at open t+1
    pot: dict[str, dict] = {}
    pot_pending_entries: list[str] = []
    pot_pending_exits: dict[str, str] = {}
    pot_rank: dict[str, int] = {}
    pot_equity_ref = 0.0
    episode = 0

    def close_main(p, px, date, reason):
        nonlocal main_cash
        proceeds = px * p["qty"]; c = sell_cost(proceeds)
        pnl = (px - p["entry"]) * p["qty"] - c - p["entry_cost"]
        inv = p["entry"] * p["qty"]
        trades.append(dict(book="main", symbol=p["sym"], entry_date=p["entry_date"], exit_date=date,
                           days=p["days"], reason=reason, net=round(pnl, 2),
                           entry=round(p["entry"], 2), exit=round(px, 2), qty=p["qty"],
                           invested=round(inv), ret_pct=round(pnl / inv * 100, 2),
                           gate_on_entry=p["gate_on_entry"], episode=None))
        main_cash += proceeds - c

    def close_pot(p, px, date, reason):
        nonlocal pot_cash
        proceeds = px * p["qty"]; c = sell_cost(proceeds)
        pnl = (px - p["entry"]) * p["qty"] - c - p["entry_cost"]
        inv = p["entry"] * p["qty"]
        trades.append(dict(book="pot", symbol=p["sym"], entry_date=p["entry_date"], exit_date=date,
                           days=p["days"], reason=reason, net=pnl,
                           entry=round(p["entry"], 2), exit=round(px, 2), qty=p["qty"],
                           invested=round(inv), ret_pct=round(pnl / inv * 100, 2),
                           gate_on_entry=False, episode=p["episode"]))
        pot_cash += proceeds - c

    for di, date in enumerate(cal):
        if di < 260:
            curve.append((date, equity)); continue
        g_on = gate_on.get(date, False)
        entry_ok = g_on and fast_ok.get(date, True)

        # 1. main pending exits (verbatim)
        for sym in list(pending_exits):
            p = pos.get(sym)
            if p is None: pending_exits.discard(sym); continue
            i = idx[sym].get(date)
            if i is None: continue
            close_main(p, syms[sym].open.iloc[i] * (1 - SLIP), date, "signal")
            del pos[sym]; pending_exits.discard(sym)

        # 2. pot pending exits (rotation + forced liquidation), then sweep
        if pot_on:
            for sym, why in list(pot_pending_exits.items()):
                p = pot.get(sym)
                if p is None: del pot_pending_exits[sym]; continue
                i = idx[sym].get(date)
                if i is None: continue
                close_pot(p, syms[sym].open.iloc[i] * (1 - SLIP), date, why)
                del pot[sym]; del pot_pending_exits[sym]
            if pot_liquidate:
                events.append(dict(kind="liquidate", date=date, episode=episode,
                                   pot_cash=pot_cash, main_cash_before=main_cash,
                                   stragglers=len(pot)))
                pot_active = False; pot_liquidate = False; pot_pending_entries = []
            if not pot_active and pot_cash != 0.0:
                main_cash += pot_cash; pot_cash = 0.0       # capital rejoins the main book
            if events and events[-1]["kind"] == "liquidate" and events[-1]["date"] == date:
                events[-1]["main_cash_after"] = main_cash

            # 3. activation, after the main book's liquidation fills
            if pot_activate_amt is not None:
                amt = max(0.0, min(pot_activate_amt, main_cash))
                main_cash -= amt; pot_cash += amt
                pot_active = True; pot_equity_ref = amt
                events.append(dict(kind="activate", date=date, episode=episode, allotted=amt,
                                   requested=pot_activate_amt))
                pot_activate_amt = None

        main_cash_open = main_cash   # after exits, merge and activation; before entries
        # 4. main pending entries (verbatim; cap reads today's g_on as the original does)
        g_cap = prev_g_on if P.main_cap_prev_gate else g_on
        cap = slots_allowed if g_cap else 0
        for sym in pending_entries:
            if len(pos) >= cap or sym in pos: continue
            i = idx[sym].get(date)
            if i is None: continue
            row = syms[sym].iloc[i]
            size = min(equity / MAX_POS, main_cash)
            px = row.open * (1 + SLIP); qty = int(size // px)
            if qty <= 0: continue
            c = buy_cost(px * qty); main_cash -= px * qty + c
            pos[sym] = dict(sym=sym, entry_date=date, entry=px, qty=qty,
                            stop=px - P.trail_atr * row.atr14, hi_close=row.close,
                            days=0, entry_cost=c, gate_on_entry=bool(g_on))
        pending_entries = []

        # 5. pot pending entries (queued at a gate-OFF close; no same-day gate read)
        if pot_on and pot_active:
            for sym in pot_pending_entries:
                if len(pot) >= P.pot_slots or sym in pot: continue
                i = idx[sym].get(date)
                if i is None: continue
                row = syms[sym].iloc[i]
                size = min(pot_equity_ref / P.pot_slots, pot_cash)
                px = row.open * (1 + SLIP); qty = int(size // px)
                if qty <= 0: continue
                c = buy_cost(px * qty); pot_cash -= px * qty + c
                pot[sym] = dict(sym=sym, entry_date=date, entry=px, qty=qty,
                                stop=px - P.trail_atr * row.atr14, hi_close=row.close,
                                days=0, entry_cost=c, episode=episode)
        pot_pending_entries = []

        # 6. manage main positions (verbatim)
        for sym, p in list(pos.items()):
            i = idx[sym].get(date)
            if i is None: continue
            row = syms[sym].iloc[i]; p["days"] += 1
            if row.low <= p["stop"]:
                close_main(p, min(row.open, p["stop"]) * (1 - SLIP), date, "stop")
                del pos[sym]; continue
            p["hi_close"] = max(p["hi_close"], row.close)
            p["stop"] = max(p["stop"], p["hi_close"] - P.trail_atr * row.atr14)
            if rot_rank.get(sym, 999) > P.exit_rank or not g_on:
                pending_exits.add(sym)

        # 7. manage pot positions
        if pot_on:
            if g_on:                                    # forced liquidation covers every
                for sym in pot:                         # pot position, bar or no bar today
                    pot_pending_exits[sym] = "flip"
            for sym, p in list(pot.items()):
                i = idx[sym].get(date)
                if i is None: continue
                row = syms[sym].iloc[i]; p["days"] += 1
                if row.low <= p["stop"]:
                    close_pot(p, min(row.open, p["stop"]) * (1 - SLIP), date, "stop")
                    del pot[sym]; continue
                p["hi_close"] = max(p["hi_close"], row.close)
                p["stop"] = max(p["stop"], p["hi_close"] - P.trail_atr * row.atr14)
                if g_on:
                    pot_pending_exits[sym] = "flip"
                elif pot_rank.get(sym, 999) > P.pot_exit_rank:
                    pot_pending_exits.setdefault(sym, "signal")
            if g_on and pot_active:
                pot_liquidate = True
            if not g_on and not pot_active and pot_activate_amt is None:
                episode += 1                            # amount is fixed in step 9

        # 8. weekly ranking -- main (verbatim)
        is_rebal = di + 1 < len(cal) and pd.Timestamp(date).weekday() >= pd.Timestamp(cal[di + 1]).weekday()
        if is_rebal or not rot_rank:
            cands = []; n_liq = n_above = 0
            for sym, d in syms.items():
                i = idx[sym].get(date)
                if i is None: continue
                r = d.iloc[i]
                if not (r.adv20 >= ADV_MIN and r.close >= PRICE_MIN) or np.isnan(r.sma200): continue
                n_liq += 1
                above = r.close > r.sma200; n_above += int(above)
                if above and not np.isnan(r.mom) and r.mom > P.mom_floor:
                    cands.append((sym, r.mom / r.atrpct if r.atrpct > 0 else r.mom))
            breadth = n_above / max(n_liq, 1)
            cands.sort(key=lambda x: -x[1])
            rot_rank = {s: k + 1 for k, (s, _) in enumerate(cands)}
            slots_allowed = int(round(MAX_POS * min(max((breadth - 0.35) / 0.30, 0.0), 1.0)))
            if entry_ok:
                for sym, _ in cands[:MAX_POS]:
                    if sym not in pos and len(pending_entries) < MAX_POS:
                        pending_entries.append(sym)

        # 8b. weekly ranking -- pot (only at a gate-OFF close)
        if pot_on and is_rebal and not g_on:
            nm, nr = nifty_mom.get(date, np.nan), nifty_r63.get(date, np.nan)
            pc = []
            for sym, d in syms.items():
                i = idx[sym].get(date)
                if i is None: continue
                r = d.iloc[i]
                if not (r.adv20 >= ADV_MIN and r.close >= PRICE_MIN) or np.isnan(r.sma200): continue
                if not (r.close > r.sma200): continue
                if np.isnan(r.mom) or not (r.mom > P.pot_mom_floor): continue
                if P.pot_rs126 and not (r.mom > nm): continue          # NaN nm -> False -> excluded
                if P.pot_rs63 and (np.isnan(r.ret63) or not (r.ret63 > nr)): continue
                pc.append((sym, r.mom / r.atrpct if r.atrpct > 0 else r.mom))
            pc.sort(key=lambda x: -x[1])
            pot_rank = {s: k + 1 for k, (s, _) in enumerate(pc)}
            pot_pending_entries = [s for s, _ in pc[:P.pot_slots] if s not in pot]
            # like the main book, a held name that fell out is flagged by the NEXT
            # session's step 7 (exit the open after that) -- same machinery.

        # 9. mark to market
        main_mtm = main_cash
        for sym, p in pos.items():
            i = idx[sym].get(date)
            main_mtm += (syms[sym].close.iloc[i] if i is not None else p["entry"]) * p["qty"]
        pot_mtm = pot_cash
        for sym, p in pot.items():
            i = idx[sym].get(date)
            pot_mtm += (syms[sym].close.iloc[i] if i is not None else p["entry"]) * p["qty"]
        equity = main_mtm + pot_mtm
        if pot_active:
            pot_equity_ref = pot_mtm
        if pot_on and not g_on and not pot_active and pot_activate_amt is None and not pot_liquidate:
            pot_activate_amt = P.pot_frac * equity
        prev_g_on = g_on
        curve.append((date, equity))
        daily.append(dict(date=date, gate_on=bool(g_on), equity=equity, main_cash=main_cash,
                          main_cash_open=main_cash_open,
                          pot_cash=pot_cash, main_mtm=main_mtm, pot_mtm=pot_mtm,
                          n_main=len(pos), n_pot=len(pot), pot_active=pot_active, episode=episode))

    for sym, p in list(pos.items()):
        i = idx[sym].get(cal[-1])
        close_main(p, syms[sym].close.iloc[i] if i is not None else p["entry"], cal[-1], "eod_final")
    for sym, p in list(pot.items()):
        i = idx[sym].get(cal[-1])
        close_pot(p, syms[sym].close.iloc[i] if i is not None else p["entry"], cal[-1], "eod_final")
    return dict(trades=pd.DataFrame(trades), curve=curve, daily=pd.DataFrame(daily),
                events=pd.DataFrame(events))


def stats(trades: pd.DataFrame, curve) -> dict:
    eq = pd.Series({d: v for d, v in curve}); eq.index = pd.to_datetime(eq.index)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    m = eq.resample("ME").last().pct_change().dropna()
    return dict(final=eq.iloc[-1], cagr=cagr * 100, dd=dd * 100, mar=cagr / abs(dd),
                n=len(trades), win=(trades.net > 0).mean() * 100 if len(trades) else 0.0,
                worst_mo=m.min() * 100, pos_mo=(m > 0).mean() * 100)
