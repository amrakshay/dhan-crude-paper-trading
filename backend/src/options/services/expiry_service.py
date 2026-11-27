"""How NSE option positions END: expiry day, delivery and settlement (Phase 4).

OPTIONS_TRADER_PLAN.md section 4.5, following DHAN's rule (decision 2026-11-24)
rather than the NSE E-4 ramp. Everything here is engine: it serves every NSE
option position on a SPAN-margined strategy, whichever module holds it.

ON EXPIRY DAY, for STOCK options (physically settled):
  * no FRESH position of any product -- `check_fresh_position` refuses it in
    the order path;
  * from 10:00 IST an in-the-money position that would end in a BUY of shares
    (long call, short put) blocks 100% of its contract value (strike x
    quantity) -- `delivery_margin`, added to blocked money by BalanceService;
  * from 10:00 the risk desk closes an obligation that cannot be met:
    a SELL of shares (short call, long put) with the shares not held in that
    portfolio, or a BUY the portfolio cannot fund -- `undeliverable`.

AFTER THE CLOSE (`settle_after`) every position whose expiry has come is
settled -- `settle_due`, run by the risk desk's task:
  * INDEX options: closed at intrinsic value against the closing index value
    (cash settlement). An out-of-the-money one at zero.
  * STOCK options in the money: the option closes at ZERO and the shares
    change hands at the STRIKE (long call / short put buys them; short call /
    long put sells them). Closing at intrinsic AND buying at the strike would
    count the gain twice; this way the P&L is premium lost on the option and
    (close - strike) carried on the shares, which sums to the same thing.
    A sell obligation still unmet at settlement is cash-settled at intrinsic
    and SAYS so: NSE would auction it at a penalty this simulator cannot price.
  * Charges: the broker's exercise fee per exercised contract, GST on it, STT
    on exercise (from the strategy's own rate card) for the long side, and the
    0.10% physical delivery charge on delivered contracts; the share trade
    itself is charged on the equity delivery card.

THE CLOSING PRICE IS FETCHED, NEVER GUESSED. It is the underlying's daily close
from Dhan's chart endpoint (market data, already allowlisted). If it is not
available yet, settlement waits and says why; it never settles at a live tick
or at a stale price.
"""
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.options.services.span_margin import MarginCard
from src.options.services.span_store import get_span_store

logger = get_logger("options.expiry")

OPTSTK = "OPTSTK"
OPTIDX = "OPTIDX"
MONEY = Decimal("0.01")
BUY_SHARES = "BUY_SHARES"
SELL_SHARES = "SELL_SHARES"


class SettlementPriceUnavailable(Exception):
    pass


def _q(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def intrinsic(option_type: str, strike: float, underlying: float) -> float:
    return max(underlying - strike, 0.0) if option_type == "CE" else max(strike - underlying, 0.0)


def obligation(option_type: str, net_quantity: int) -> str:
    """What an in-the-money stock option turns into at expiry."""
    long = net_quantity > 0
    if option_type == "CE":
        return BUY_SHARES if long else SELL_SHARES
    return SELL_SHARES if long else BUY_SHARES


# --- the closing price ----------------------------------------------------------
async def dhan_closing_price(symbol: str, kind: str, day: date, scrip: Optional[int]) -> Optional[float]:
    """The underlying's official close for `day`, from Dhan's daily bars.

    Index: its IDX_I id (NIFTY = 13). Stock: its NSE_EQ id. None when the
    session's bar is not published yet -- the caller waits rather than guess.
    """
    if scrip is None:
        return None
    from src.market.services.dhan_charts_client import DhanChartsClient

    client = DhanChartsClient()
    segment, instrument = ("IDX_I", "INDEX") if kind == OPTIDX else ("NSE_EQ", "EQUITY")
    start = datetime(day.year, day.month, day.day)
    candles = await client.fetch_daily(str(scrip), segment, instrument, start, start + timedelta(days=1))
    for candle in candles:
        bar_day = datetime.utcfromtimestamp(candle.time + 19800).date()
        if bar_day == day:
            return float(candle.close)
    return None


class ExpiryService:
    def __init__(self, session, card: MarginCard, book=None, price_provider=None):
        self.session = session
        self.card = card
        self._book = book
        self.instruments = InstrumentRepository(session)
        # (symbol, kind, day, scrip) -> close or None. Injectable for tests.
        self.price_provider = price_provider or dhan_closing_price

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- the order path --------------------------------------------------------
    def check_fresh_position(self, instrument, today: date) -> Optional[str]:
        """Why an OPENING order on this contract is refused today, if it is."""
        if (
            self.card.no_fresh_stock_options_on_expiry
            and instrument.instrument_type == OPTSTK
            and instrument.expiry_date == today
        ):
            return (
                f"{instrument.trading_symbol} expires today and is physically "
                f"settled: Dhan accepts no fresh stock-option position of any "
                f"product on expiry day. Closing an existing one is allowed."
            )
        return None

    # --- spot ------------------------------------------------------------------
    async def _spot(self, instrument) -> Optional[float]:
        """Live underlying price where the feed has it, else the SPAN file's."""
        from src.options.services.option_margin_service import _recent_spots

        if instrument.instrument_type == OPTSTK:
            cash = await self.instruments.find_cash_row(instrument.underlying_symbol, "NSE_EQ")
            row = self.book.get(str(cash.security_id)) if cash is not None else None
            if row and row.get("ltp"):
                return float(row["ltp"])
        cached = _recent_spots.get(instrument.underlying_symbol)
        if cached:
            return cached[1]
        data = get_span_store().current()
        return data.underlying_price.get(instrument.underlying_symbol) if data else None

    # --- expiry day: delivery ----------------------------------------------------
    async def obligations(self, portfolio_id: int, now: datetime) -> List[Dict[str, Any]]:
        """Every in-the-money STOCK option expiring today in the portfolio."""
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        today = now.date()
        positions = await PositionRepository(self.session).list_open(portfolio_id=int(portfolio_id))
        found = []
        for position in positions:
            net = int(position.net_quantity or 0)
            if not net or position.expiry_date != today or not position.option_type:
                continue
            instrument = await self.instruments.get_by_security_id(position.security_id)
            if instrument is None or instrument.instrument_type != OPTSTK:
                continue
            spot = await self._spot(instrument)
            if spot is None:
                continue
            strike = float(instrument.strike_price)
            if intrinsic(instrument.option_type, strike, spot) <= 0:
                continue
            shares_needed = abs(net)
            held = await self._shares_held(portfolio_id, instrument.underlying_symbol)
            found.append(
                {
                    "position": position,
                    "instrument": instrument,
                    "spot": spot,
                    "obligation": obligation(instrument.option_type, net),
                    "contractValue": strike * shares_needed,
                    "sharesNeeded": shares_needed,
                    "sharesHeld": held,
                }
            )
        return found

    async def _shares_held(self, portfolio_id: int, symbol: str) -> int:
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        cash = await self.instruments.find_cash_row(symbol, "NSE_EQ")
        if cash is None:
            return 0
        position = await PositionRepository(self.session).get_open_for_security(int(portfolio_id), str(cash.security_id))
        return max(int(position.net_quantity or 0), 0) if position is not None else 0

    async def delivery_margin(self, portfolio_id: int, now: datetime) -> float:
        """From `delivery_margin_from` on expiry day: 100% of contract value for
        every in-the-money stock option that would BUY shares."""
        if now.time() < self.card.delivery_margin_from:
            return 0.0
        return sum(
            self.card.delivery_margin_fraction * item["contractValue"]
            for item in await self.obligations(portfolio_id, now)
            if item["obligation"] == BUY_SHARES
        )

    async def undeliverable(self, portfolio_id: int, now: datetime, available: float) -> List[Dict[str, Any]]:
        """Obligations that cannot be met, for the risk desk to close."""
        if now.time() < self.card.delivery_margin_from:
            return []
        items = await self.obligations(portfolio_id, now)
        refused = [
            item for item in items
            if item["obligation"] == SELL_SHARES and item["sharesHeld"] < item["sharesNeeded"]
        ]
        if available < 0:
            # Largest buy obligation first, until the shortfall is covered.
            shortfall = -available
            for item in sorted(
                (one for one in items if one["obligation"] == BUY_SHARES),
                key=lambda one: one["contractValue"], reverse=True,
            ):
                if shortfall <= 0:
                    break
                refused.append(item)
                shortfall -= self.card.delivery_margin_fraction * item["contractValue"]
        return refused

    async def upcoming(self, portfolio_id: int, now: datetime, horizon_days: int = 7) -> List[Dict[str, Any]]:
        """Open option positions expiring within `horizon_days`, and what
        expiry will do to each -- for the page, so nothing about it is a
        surprise. Moneyness is at the live (or file) spot, not a promise."""
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        today = now.date()
        rows = []
        for position in await PositionRepository(self.session).list_open(portfolio_id=int(portfolio_id)):
            net = int(position.net_quantity or 0)
            expiry = position.expiry_date
            if not net or not position.option_type or expiry is None:
                continue
            days = (expiry - today).days
            if days < 0 or days > horizon_days:
                continue
            instrument = await self.instruments.get_by_security_id(position.security_id)
            if instrument is None or instrument.instrument_type not in (OPTIDX, OPTSTK):
                continue
            spot = await self._spot(instrument)
            strike = float(instrument.strike_price)
            value = intrinsic(instrument.option_type, strike, spot) if spot is not None else None
            physical = instrument.instrument_type == OPTSTK
            item = {
                "securityId": position.security_id,
                "tradingSymbol": instrument.trading_symbol,
                "expiry": expiry.isoformat(),
                "daysLeft": days,
                "netQuantity": net,
                "settlement": "PHYSICAL" if physical else "CASH",
                "spot": spot,
                "inTheMoney": None if value is None else value > 0,
                "intrinsic": None if value is None else round(value, 2),
                "obligation": None,
                "contractValue": None,
                "sharesHeld": None,
            }
            if physical and value:
                item["obligation"] = obligation(instrument.option_type, net)
                item["contractValue"] = strike * abs(net)
                item["sharesHeld"] = await self._shares_held(portfolio_id, instrument.underlying_symbol)
            rows.append(item)
        rows.sort(key=lambda one: (one["expiry"], one["tradingSymbol"]))
        return rows

    # --- after the close: settlement -----------------------------------------------
    def _due(self, position, now: datetime) -> bool:
        expiry = position.expiry_date
        if expiry is None:
            return False
        if expiry < now.date():
            return True
        return expiry == now.date() and now.time() >= self.card.settle_after

    async def settle_due(self, positions, now: datetime, index_scrips: Dict[str, int]) -> List[Dict[str, Any]]:
        """Settle every given open option position whose expiry has come."""
        from src.options.services.option_margin_service import uses_span

        results = []
        closes: Dict[tuple, Optional[float]] = {}
        for position in positions:
            net = int(position.net_quantity or 0)
            if not net or not position.option_type or not uses_span(position.strategy_key):
                continue
            if not self._due(position, now):
                continue
            instrument = await self.instruments.get_by_security_id(position.security_id)
            if instrument is None:
                continue
            kind = instrument.instrument_type
            if kind == OPTIDX:
                scrip = index_scrips.get(instrument.underlying_symbol)
            else:
                cash = await self.instruments.find_cash_row(instrument.underlying_symbol, "NSE_EQ")
                scrip = int(cash.security_id) if cash is not None else None
            key = (instrument.underlying_symbol, kind, position.expiry_date)
            if key not in closes:
                try:
                    closes[key] = await self.price_provider(
                        instrument.underlying_symbol, kind, position.expiry_date, scrip
                    )
                except Exception as error:  # noqa: BLE001 - wait, never guess
                    logger.warning(
                        "Closing price for %s %s unavailable: %s",
                        instrument.underlying_symbol, position.expiry_date, type(error).__name__,
                    )
                    closes[key] = None
            close = closes[key]
            if close is None:
                results.append({"securityId": position.security_id, "tradingSymbol": instrument.trading_symbol,
                                "portfolioId": int(position.portfolio_id), "strategyKey": position.strategy_key,
                                "outcome": "WAITING",
                                "reason": f"No official close for {instrument.underlying_symbol} on "
                                          f"{position.expiry_date} yet; settlement waits rather than guess."})
                continue
            results.extend(await self._settle_one(position, instrument, close))
        return results

    async def _settle_one(self, position, instrument, close: float) -> List[Dict[str, Any]]:
        from src.charges.services.charges_engine import ChargeComponent, ChargesEngine
        from src.options.services.basket_service import BasketService

        net = int(position.net_quantity)
        quantity = abs(net)
        strike = float(instrument.strike_price)
        value = intrinsic(instrument.option_type, strike, close)
        itm = value > 0
        physical = instrument.instrument_type == OPTSTK and itm
        side = "SELL" if net > 0 else "BUY"
        orders = BasketService(self.session, book=self._book)._order_service()
        rates = ChargesEngine.for_strategy_key(position.strategy_key).rates
        gst = float((rates.get("gst") or {}).get("rate", 0.18))
        stt_rate = float((rates.get("stt") or {}).get("exercise_settlement_rate", 0.0))

        results = []
        undeliverable = False
        if physical and obligation(instrument.option_type, net) == SELL_SHARES:
            held = await self._shares_held(position.portfolio_id, instrument.underlying_symbol)
            undeliverable = held < quantity
        settle_physically = physical and not undeliverable
        option_price = 0.0 if settle_physically else value

        components = []
        if itm:
            fee = self.card.exercise_fee_per_order
            components.append(ChargeComponent(name="brokerage", amount=_q(fee), label="Exercise / assignment fee",
                                              note="Dhan: Rs 20 per exercised or assigned contract"))
            components.append(ChargeComponent(name="gst", amount=_q(fee * gst), rate=Decimal(str(gst)), label="GST"))
            if net > 0 and stt_rate:
                components.append(ChargeComponent(
                    name="stt_exercise", amount=_q(value * quantity * stt_rate), rate=Decimal(str(stt_rate)),
                    base=_q(value * quantity), label="STT (on exercise)",
                    note="On intrinsic value, paid by the side exercising"))
            if settle_physically:
                components.append(ChargeComponent(
                    name="physical_delivery", amount=_q(strike * quantity * self.card.physical_delivery_charge_rate),
                    rate=Decimal(str(self.card.physical_delivery_charge_rate)), base=_q(strike * quantity),
                    label="Physical delivery charge", note="Dhan: 0.10% of contract value"))
        charges = (_q(value * quantity), components, "expiry-" + str(position.expiry_date))
        if settle_physically:
            label = f"Expiry {position.expiry_date}: exercised/assigned at strike {strike:g}, close {close:g}; settled by delivery"
        elif instrument.instrument_type == OPTSTK and itm:
            label = (f"Expiry {position.expiry_date}: ITM by {value:g} with NO SHARES TO DELIVER -- settled in cash "
                     f"at intrinsic (paper). NSE would have auctioned the shortfall at a penalty.")
        elif itm:
            label = f"Expiry {position.expiry_date}: cash-settled at intrinsic {value:g} (close {close:g})"
        else:
            label = f"Expiry {position.expiry_date}: expired worthless (close {close:g})"
        order = await orders.record_off_market_fill(
            instrument=instrument, portfolio_id=position.portfolio_id, strategy_key=position.strategy_key,
            side=side, quantity=quantity, price=Decimal(str(option_price)), reason=label,
            is_close_order=True, order_type="EXPIRY", charges=charges, product=position.product,
        )
        results.append({"securityId": position.security_id, "tradingSymbol": instrument.trading_symbol,
                        "side": side, "quantity": quantity, "price": option_price, "orderId": order.id,
                        "outcome": "SETTLED", "reason": label, "strategyKey": position.strategy_key,
                        "portfolioId": int(position.portfolio_id)})

        if settle_physically:
            cash = await self.instruments.find_cash_row(instrument.underlying_symbol, "NSE_EQ")
            share_side = "BUY" if obligation(instrument.option_type, net) == BUY_SHARES else "SELL"
            if cash is None:
                logger.error("No NSE_EQ row for %s; delivery could not be recorded", instrument.underlying_symbol)
            else:
                engine = ChargesEngine(rate_card=self.card.delivery_rate_card)
                breakdown = engine.compute_order_charges_for_quantity(
                    side=share_side, premium=Decimal(str(strike)), quantity=quantity, lot_size=1,
                )
                share_order = await orders.record_off_market_fill(
                    instrument=cash, portfolio_id=position.portfolio_id, strategy_key=position.strategy_key,
                    side=share_side, quantity=quantity, price=Decimal(str(strike)),
                    reason=f"Physical delivery of {instrument.trading_symbol}: {share_side} {quantity} at strike {strike:g}",
                    is_close_order=share_side == "SELL", order_type="DELIVERY",
                    charges=(breakdown.turnover, list(breakdown.components), breakdown.rates_version),
                )
                results.append({"securityId": cash.security_id, "tradingSymbol": cash.trading_symbol,
                                "side": share_side, "quantity": quantity, "price": strike,
                                "orderId": share_order.id, "outcome": "DELIVERED",
                                "reason": "shares at strike", "strategyKey": position.strategy_key,
                                "portfolioId": int(position.portfolio_id)})
        return results
