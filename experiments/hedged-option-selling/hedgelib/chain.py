"""Option chains out of the bhavcopy cache: forwards, implied vols, deltas.

THE DATA QUIRK THAT DECIDES EVERYTHING HERE
-------------------------------------------
An untraded strike still gets a row.  Its CLOSE is a stale carry-forward; its
SETTLE is the exchange's own theoretical value.  They diverge badly:

    2025-08-01, NIFTY 07-Aug 23550 CE -- close 1297.10, settle 1042.05, volume 0

A 24% gap on one contract.  So the two prices answer different questions and are
never used interchangeably:

  close   what you could have TRADED at.  Only trusted when the contract
          actually traded that session, and `tradeable()` enforces it.
  settle  what the position is MARKED at.  Defined for every contract, which is
          exactly what an end-of-day mark needs.

Entering on a stale close is a silent lookahead-shaped error -- it fills you at
a price nobody was quoting -- and it would bias a far-OTM premium seller
towards selling wings at yesterday's richer price.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from . import blackscholes as bs

# Vega floor for trusting an implied volatility off a real quote.  For IV to be
# identifiable to half a vol point from a price quoted in 0.05 steps, vega must
# exceed roughly 0.05 / 0.005 = 10.  See blackscholes.implied_vol.
MIN_VEGA_REAL = 10.0


@dataclass(frozen=True)
class Quote:
    strike: float
    is_call: bool
    close: float
    settle: float
    open: float
    high: float
    low: float
    oi: int
    volume: int
    num_trades: int | None
    lot_size: int | None
    source: str

    def tradeable(self, min_trades: int = 1, min_oi: int = 0) -> bool:
        """Did this contract actually change hands this session?

        UDiFF carries an executed-trade count; the legacy format does not, so
        before 2024-07 the test falls back to contract volume.  That is a real
        weakening of the liquidity filter on the older half of the sample and
        is reported rather than smoothed over.
        """
        if self.oi < min_oi:
            return False
        if self.num_trades is not None:
            return self.num_trades >= min_trades
        return self.volume > 0


@dataclass
class Chain:
    trade_date: date
    symbol: str
    expiry: date
    underlying: float | None
    forward: float | None
    t_years: float
    calls: dict[float, Quote]
    puts: dict[float, Quote]
    lot_size: int | None

    @property
    def dte(self) -> int:
        return (self.expiry - self.trade_date).days

    def quote(self, strike: float, is_call: bool) -> Quote | None:
        return (self.calls if is_call else self.puts).get(strike)

    def iv(self, strike: float, is_call: bool,
           price_field: str = "close") -> float | None:
        q = self.quote(strike, is_call)
        if q is None or self.forward is None or self.t_years <= 0:
            return None
        px = getattr(q, price_field)
        if px <= 0:
            return None
        return bs.implied_vol(px, self.forward, strike, self.t_years, is_call,
                              min_vega=MIN_VEGA_REAL)

    def delta(self, strike: float, is_call: bool,
              price_field: str = "close") -> float | None:
        v = self.iv(strike, is_call, price_field)
        if v is None or self.forward is None:
            return None
        return bs.greeks(self.forward, strike, self.t_years, v, is_call).delta

    def strike_by_delta(self, target_abs_delta: float, is_call: bool,
                        min_trades: int = 1, min_oi: int = 0,
                        price_field: str = "close") -> tuple[float, float, float] | None:
        """Nearest OTM strike to a target |delta|.

        Returns (strike, delta, iv), or None when nothing liquid qualifies.

        Only OTM strikes are considered: a premium seller sells OTM, and an ITM
        quote carries almost no volatility information anyway (see the vega
        floor above).
        """
        if self.forward is None:
            return None
        book = self.calls if is_call else self.puts
        best = None
        for k, q in book.items():
            if is_call and k <= self.forward:
                continue
            if not is_call and k >= self.forward:
                continue
            if not q.tradeable(min_trades, min_oi):
                continue
            v = self.iv(k, is_call, price_field)
            if v is None:
                continue
            d = abs(bs.greeks(self.forward, k, self.t_years, v, is_call).delta)
            gap = abs(d - target_abs_delta)
            if best is None or gap < best[0]:
                best = (gap, k, d, v)
        return None if best is None else (best[1], best[2], best[3])


def load_chain(conn: sqlite3.Connection, trade_date: date, symbol: str,
               expiry: date, r: float = 0.0) -> Chain | None:
    rows = conn.execute(
        "SELECT strike, opt_type, open, high, low, close, settle, oi, volume,"
        "       num_trades, lot_size, underlying, source "
        "FROM fo_bhav WHERE trade_date=? AND symbol=? AND expiry=? "
        "  AND kind IN ('IDO','STO') AND opt_type IN ('CE','PE')",
        (trade_date.isoformat(), symbol, expiry.isoformat())).fetchall()
    if not rows:
        return None

    calls: dict[float, Quote] = {}
    puts: dict[float, Quote] = {}
    underlying = None
    lot_size = None
    for (k, ot, o, h, lo, c, s, oi, vol, nt, ls, und, src) in rows:
        q = Quote(strike=float(k), is_call=(ot == "CE"), close=float(c or 0),
                  settle=float(s or 0), open=float(o or 0), high=float(h or 0),
                  low=float(lo or 0), oi=int(oi or 0), volume=int(vol or 0),
                  num_trades=(int(nt) if nt is not None else None),
                  lot_size=(int(ls) if ls else None), source=src)
        (calls if q.is_call else puts)[q.strike] = q
        if und:
            underlying = float(und)
        if ls:
            lot_size = int(ls)

    t = max((expiry - trade_date).days, 0) / 365.0

    # Forward from put-call parity on the day's OWN chain -- absorbs dividends,
    # repo and basis into an observed number.  Only strikes that actually
    # TRADED are used; a parity computed off two stale closes is noise.
    parity: dict[float, tuple[float | None, float | None]] = {}
    for k in set(calls) & set(puts):
        c, p = calls[k], puts[k]
        if c.tradeable() and p.tradeable():
            parity[k] = (c.close, p.close)
    fwd = bs.forward_from_parity(parity, t, r) if parity else None

    return Chain(trade_date=trade_date, symbol=symbol, expiry=expiry,
                 underlying=underlying, forward=fwd, t_years=t,
                 calls=calls, puts=puts, lot_size=lot_size)


def settlement_value(ch: Chain) -> tuple[float | None, str]:
    """The underlying's settlement value for an expiry-day chain.

    Returns (value, how_it_was_obtained).  Precedence, best first:

      "underlying"  the bhavcopy's own UndrlygPric (UDiFF only) -- exact.
      "settle"      the expiry-day `settle` field, which IS the underlying
                    settlement (see this module's header) -- exact where
                    non-zero, which is the whole UDiFF era.
      "parity"      median of K + C - P over strikes where BOTH sides traded.
                    At expiry C - P = S - K exactly, so this recovers S from
                    the option closes.  Used for the legacy era, where the
                    settle field is 0.0.  APPROXIMATE: the closes are last
                    traded prices struck at 15:30 while NSE settles on the
                    15:00-15:30 average of the index, so the two differ by
                    whatever the index did in that half hour.
      None          nothing usable -- the caller must not invent a number.
    """
    if ch.underlying and ch.underlying > 0:
        return float(ch.underlying), "underlying"

    vals = [q.settle for q in list(ch.calls.values()) + list(ch.puts.values())
            if q.settle > 0]
    if vals:
        # identical across strikes by construction; median guards a stray row
        return float(sorted(vals)[len(vals) // 2]), "settle"

    from statistics import median
    parity = []
    for k in set(ch.calls) & set(ch.puts):
        c, p = ch.calls[k], ch.puts[k]
        if c.tradeable() and p.tradeable() and (c.close > 0 or p.close > 0):
            parity.append(k + c.close - p.close)
    if len(parity) >= 3:
        return float(median(parity)), "parity"
    return None, "unavailable"


def expiries_on(conn: sqlite3.Connection, trade_date: date,
                symbol: str) -> list[date]:
    rows = conn.execute(
        "SELECT DISTINCT expiry FROM fo_bhav WHERE trade_date=? AND symbol=? "
        "  AND kind IN ('IDO','STO') ORDER BY expiry",
        (trade_date.isoformat(), symbol)).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def trading_days(conn: sqlite3.Connection, symbol: str,
                 start: date | None = None,
                 end: date | None = None) -> list[date]:
    q = "SELECT DISTINCT trade_date FROM fo_bhav WHERE symbol=?"
    args: list = [symbol]
    if start:
        q += " AND trade_date>=?"; args.append(start.isoformat())
    if end:
        q += " AND trade_date<=?"; args.append(end.isoformat())
    q += " ORDER BY trade_date"
    return [date.fromisoformat(r[0]) for r in conn.execute(q, args).fetchall()]


__all__ = ["Chain", "Quote", "load_chain", "expiries_on", "trading_days",
           "MIN_VEGA_REAL"]
