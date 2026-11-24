"""Baskets, exits and the margin preview -- callable from CODE, not just HTTP.

OPTIONS_TRADER_PLAN.md decision D7: the page is one caller of these, and an
automated options module would be another. Nothing here knows which.

**A basket mirrors Dhan's (D3).** Legs are sent as separate paper orders, BUYS
FIRST, each through `submit_paper_order` -- so each crosses the spread, walks
the book, and is margin-checked against the portfolio AS IT STANDS after the
legs before it. A short leg therefore gets its hedge benefit only if its long
leg actually filled. A leg can fill, part-fill, rest or be refused while the
others still go; nothing is rolled back.

**Exit all covers shorts first.** Buying back a short releases margin;
selling a long first can raise it (the Hedge Break case). The order of the
orders is the point.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from src.charges.services.charges_engine import ChargesEngine
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.options.database.db_models.basket_model import (
    BASKET_COMPLETE,
    BASKET_PARTIAL,
    BASKET_PLACING,
    BASKET_REJECTED,
    BASKET_RESTING,
)
from src.options.database.db_operations.basket_repository import BasketRepository
from src.options.services.option_margin_service import (
    OptionMarginService,
    card_for,
    uses_span,
)

logger = get_logger("options.basket")

MAX_LEGS = 8
BUY = "BUY"
SELL = "SELL"


class BasketError(Exception):
    """A basket that cannot be placed at all. Carries the reason."""


@dataclass(frozen=True)
class LegRequest:
    security_id: str
    side: str
    lots: int
    order_type: str = "MARKET"
    limit_price: Optional[Decimal] = None


def _normalise(legs: Sequence[LegRequest]) -> List[LegRequest]:
    if not legs:
        raise BasketError("A basket needs at least one leg.")
    if len(legs) > MAX_LEGS:
        raise BasketError(f"A basket holds at most {MAX_LEGS} legs.")
    cleaned = []
    for leg in legs:
        side = str(leg.side).upper()
        if side not in (BUY, SELL):
            raise BasketError(f"side must be BUY or SELL, got {leg.side!r}")
        if int(leg.lots) < 1:
            raise BasketError("Every leg needs at least one lot.")
        cleaned.append(
            LegRequest(str(leg.security_id), side, int(leg.lots), str(leg.order_type).upper(), leg.limit_price)
        )
    return cleaned


def sent_order(legs: Sequence[LegRequest]) -> List[LegRequest]:
    """Buys first, then sells, each group in the order given."""
    return [leg for leg in legs if leg.side == BUY] + [leg for leg in legs if leg.side == SELL]


class BasketService:
    def __init__(self, session, book=None):
        self.session = session
        self._book = book
        self.instruments = InstrumentRepository(session)
        self.baskets = BasketRepository(session)
        self.margin = OptionMarginService(session, book=book)

    @property
    def book(self):
        return self.margin.book

    def _order_service(self):
        from src.orders.database.db_operations.order_repository import OrderRepository
        from src.orders.services.order_service import OrderService
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        return OrderService(
            order_repository=OrderRepository(self.session),
            instrument_repository=self.instruments,
            position_repository=PositionRepository(self.session),
            book=self._book,
        )

    def _price(self, instrument, side: str) -> Optional[float]:
        """The touch a market order would cross; else the last trade; else the
        chain's last price; else the SPAN file's own price for the contract --
        so a basket can still be previewed with the market shut."""
        row = self.book.get(str(instrument.security_id)) or {}
        touch = row.get("ask") if side == BUY else row.get("bid")
        value = touch or row.get("ltp") or row.get("chainLastPrice")
        if value:
            return float(value)
        from src.options.services.span_store import get_span_store

        data = get_span_store().current()
        if data is not None and instrument.option_type and instrument.expiry_date:
            entry = data.entry(
                instrument.underlying_symbol,
                "C" if instrument.option_type == "CE" else "P",
                instrument.expiry_date,
                instrument.strike_price,
            )
            if entry is not None:
                return float(entry.price)
        return None

    # --- preview -------------------------------------------------------------
    async def preview(
        self, strategy_key: str, portfolio_id: int, legs: Sequence[LegRequest]
    ) -> Dict[str, Any]:
        """What the basket would block, and what the hedge saves. Places nothing."""
        legs = _normalise(legs)
        if not uses_span(strategy_key):
            raise BasketError(f"{strategy_key} is not margined on NSE SPAN.")
        card = card_for(strategy_key)
        instruments = {
            one.security_id: one
            for one in await self.instruments.get_many_by_security_ids([leg.security_id for leg in legs])
        }
        missing = [leg.security_id for leg in legs if leg.security_id not in instruments]
        if missing:
            raise BasketError(f"Unknown contract(s): {', '.join(missing)}")

        engine = ChargesEngine.for_strategy_key(strategy_key)
        changes, rows = [], []
        premium_flow = 0.0
        charges = 0.0
        priced = True
        for leg in legs:
            instrument = instruments[leg.security_id]
            quantity = leg.lots * int(instrument.lot_size)
            price = float(leg.limit_price) if leg.limit_price else self._price(instrument, leg.side)
            if price is None:
                priced = False
            signed = quantity if leg.side == BUY else -quantity
            changes.append((leg.security_id, signed))
            if price is not None:
                premium_flow += price * quantity if leg.side == BUY else -price * quantity
                charges += float(
                    engine.compute_order_charges_for_quantity(
                        side=leg.side, premium=Decimal(str(price)), quantity=quantity,
                        lot_size=int(instrument.lot_size),
                    ).total
                )
            rows.append(
                {
                    "securityId": leg.security_id,
                    "tradingSymbol": instrument.trading_symbol,
                    "side": leg.side,
                    "lots": leg.lots,
                    "quantity": quantity,
                    "price": price,
                    "underlying": instrument.underlying_symbol,
                    "strike": float(instrument.strike_price) if instrument.strike_price else None,
                    "optionType": instrument.option_type,
                    "expiry": instrument.expiry_date.isoformat() if instrument.expiry_date else None,
                }
            )

        before, after = await self.margin.with_changes(portfolio_id, changes, card)
        blocked_change = after.blocked - before.blocked
        premium_paid = sum(
            (row["price"] or 0.0) * row["quantity"] for row in rows if row["side"] == BUY
        )
        # "Overall margin", the way Dhan shows it: each SHORT on its own, plus
        # every BUY's premium at the price it would actually be bought at.
        overall = await self.margin.each_alone(
            [change for change in changes if change[1] < 0], card
        ) + premium_paid
        premium_received = sum(
            (row["price"] or 0.0) * row["quantity"] for row in rows if row["side"] == SELL
        )
        # A broker's "final margin" for a basket: the change in blocked money
        # plus premium paid on buys -- Dhan's figure measured 2026-11-24 to
        # within 0.7% on three baskets. Premium received is not netted off
        # unless the card says the broker counts it.
        incremental = blocked_change + premium_paid
        credit = premium_received if card.count_premium_received else 0.0
        required = charges + incremental - credit

        from src.portfolios.services.balance_service import BalanceService

        available = float(await BalanceService(self.session, book=self._book).available_balance(portfolio_id))
        return {
            "legs": rows,
            "sentOrder": [leg.security_id for leg in sent_order(legs)],
            "overallMargin": round(overall, 2),
            "finalMargin": round(incremental, 2),
            "hedgeBenefit": round(max(overall - incremental, 0.0), 2),
            "marginBefore": round(before.blocked, 2),
            "marginAfter": round(after.blocked, 2),
            "premiumReceivedCounted": card.count_premium_received,
            "broker": card.broker,
            "premiumNet": round(-premium_flow, 2),
            "charges": round(charges, 2),
            "requiredFunds": round(required, 2),
            "available": round(available, 2),
            "affordable": (required <= available) if priced else None,
            "priced": priced,
            "source": after.source,
            "spanFileDate": after.span_file_date.isoformat() if after.span_file_date else None,
            "breakdown": after.as_dict(),
            "note": (
                "ESTIMATE from NSE SPAN plus exposure margin, the way "
                f"{card.broker.title()} applies them. "
                "Legs are sent buys first; each is checked against the portfolio "
                "after the legs before it, so a hedge counts only once it has filled."
            ),
        }

    # --- placing -------------------------------------------------------------
    async def submit_paper_basket(
        self,
        strategy_key: str,
        portfolio_id: int,
        legs: Sequence[LegRequest],
        product: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        from src.orders.services.order_service import OrderValidationError

        legs = _normalise(legs)
        ordered = sent_order(legs)
        previewed = None
        try:
            previewed = (await self.preview(strategy_key, portfolio_id, legs))["finalMargin"]
        except Exception:  # noqa: BLE001 - a missing preview must not stop the basket
            logger.exception("Basket preview failed; placing without a recorded preview")

        basket = await self.baskets.add_basket(
            strategy_key=strategy_key,
            portfolio_id=int(portfolio_id),
            product=product,
            status=BASKET_PLACING,
            leg_count=len(ordered),
            previewed_margin=Decimal(str(previewed)) if previewed is not None else None,
        )
        basket_id = basket.id
        service = self._order_service()
        outcomes: List[Dict[str, Any]] = []
        for sequence, leg in enumerate(ordered, start=1):
            label = f"Basket #{basket_id} leg {sequence}/{len(ordered)}"
            if reason:
                label = f"{label} -- {reason}"
            order_id = None
            symbol = None
            try:
                order = await service.submit_paper_order(
                    security_id=leg.security_id,
                    side=leg.side,
                    order_type=leg.order_type,
                    lots=leg.lots,
                    limit_price=leg.limit_price,
                    portfolio_id=int(portfolio_id),
                    reason=label,
                    strategy_key=strategy_key,
                    product=product,
                )
                order_id, symbol = order.id, order.trading_symbol
                outcome, why = order.status, order.rejection_reason
            except OrderValidationError as error:
                outcome, why = "REJECTED", str(error)
            await self.baskets.add_leg(
                basket_id=basket_id,
                sequence=sequence,
                security_id=leg.security_id,
                trading_symbol=symbol,
                side=leg.side,
                lots=leg.lots,
                order_type=leg.order_type,
                limit_price=leg.limit_price,
                order_id=order_id,
                outcome=outcome,
                reason=(why or None) and str(why)[:500],
            )
            outcomes.append(outcome)
            logger.info("%s %s %s lot(s) of %s -> %s", label, leg.side, leg.lots, leg.security_id, outcome)

        basket = await self.baskets.get(basket_id)
        basket.status = _basket_status(outcomes)
        await self.session.flush()
        return await self.describe(basket_id)

    async def describe(self, basket_id: int) -> Dict[str, Any]:
        basket = await self.baskets.get(basket_id)
        legs = await self.baskets.legs(basket_id)
        from src.orders.database.db_operations.order_repository import OrderRepository

        orders = OrderRepository(self.session)
        rows = []
        for leg in legs:
            order = await orders.get_by_id(leg.order_id) if leg.order_id else None
            rows.append(
                {
                    "sequence": leg.sequence,
                    "securityId": leg.security_id,
                    "tradingSymbol": leg.trading_symbol,
                    "side": leg.side,
                    "lots": leg.lots,
                    "orderType": leg.order_type,
                    "orderId": leg.order_id,
                    "outcome": order.status if order is not None else leg.outcome,
                    "filledQuantity": order.filled_quantity if order is not None else 0,
                    "quantity": order.quantity if order is not None else None,
                    "averagePrice": float(order.average_fill_price) if order is not None and order.average_fill_price else None,
                    "reason": leg.reason,
                }
            )
        return {
            "id": basket.id,
            "strategyKey": basket.strategy_key,
            "portfolioId": basket.portfolio_id,
            "product": basket.product,
            "status": basket.status,
            "legCount": basket.leg_count,
            "previewedMargin": float(basket.previewed_margin) if basket.previewed_margin is not None else None,
            "createdAt": basket.created_at.isoformat() if basket.created_at else None,
            "legs": rows,
        }

    # --- exits ---------------------------------------------------------------
    async def exit_group(
        self, portfolio_id: int, underlying: str, expiry=None, strategy_key: Optional[str] = None,
        reason: str = "Exit all",
    ) -> List[Dict[str, Any]]:
        """Close every open option position on an underlying (and expiry), SHORTS FIRST."""
        from src.orders.services.order_service import OrderValidationError
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        positions = await PositionRepository(self.session).list_open(portfolio_id=int(portfolio_id))
        instruments = {
            one.security_id: one
            for one in await self.instruments.get_many_by_security_ids(
                [position.security_id for position in positions]
            )
        }
        group = []
        for position in positions:
            instrument = instruments.get(position.security_id)
            net = int(position.net_quantity or 0)
            if not net or instrument is None or not instrument.option_type:
                continue
            if instrument.underlying_symbol != underlying:
                continue
            if expiry is not None and instrument.expiry_date != expiry:
                continue
            if strategy_key is not None and position.strategy_key != strategy_key:
                continue
            group.append((position, instrument, net))
        group.sort(key=lambda item: 0 if item[2] < 0 else 1)   # shorts first

        service = self._order_service()
        results = []
        for position, instrument, net in group:
            quantity = abs(net)
            try:
                order = await service.submit_paper_order(
                    security_id=position.security_id,
                    side=BUY if net < 0 else SELL,
                    order_type="MARKET",
                    lots=max(1, quantity // max(int(instrument.lot_size), 1)),
                    quantity_override=quantity,
                    is_close_order=True,
                    portfolio_id=int(portfolio_id),
                    reason=reason,
                    strategy_key=position.strategy_key,
                )
                results.append({"securityId": position.security_id, "tradingSymbol": instrument.trading_symbol,
                                "side": order.side, "orderId": order.id, "outcome": order.status,
                                "reason": order.rejection_reason})
            except OrderValidationError as error:
                results.append({"securityId": position.security_id, "tradingSymbol": instrument.trading_symbol,
                                "side": BUY if net < 0 else SELL, "orderId": None, "outcome": "REJECTED",
                                "reason": str(error)})
        return results

    # --- grouped positions ------------------------------------------------------
    async def option_positions(self, portfolio_id: int, strategy_key: Optional[str] = None) -> Dict[str, Any]:
        """Open option positions grouped by underlying and expiry, with margin."""
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        positions = await PositionRepository(self.session).list_open(portfolio_id=int(portfolio_id))
        instruments = {
            one.security_id: one
            for one in await self.instruments.get_many_by_security_ids(
                [position.security_id for position in positions]
            )
        }
        groups: Dict[tuple, Dict[str, Any]] = {}
        for position in positions:
            instrument = instruments.get(position.security_id)
            net = int(position.net_quantity or 0)
            if not net or instrument is None or not instrument.option_type:
                continue
            if strategy_key is not None and position.strategy_key != strategy_key:
                continue
            key = (instrument.underlying_symbol, instrument.expiry_date)
            row = self.book.get(str(position.security_id)) or {}
            mark = row.get("ltp")
            unrealised = (
                (float(mark) - float(position.average_price)) * net if mark is not None else None
            )
            group = groups.setdefault(
                key,
                {"underlying": key[0], "expiry": key[1].isoformat() if key[1] else None,
                 "legs": [], "unrealizedPnl": 0.0, "unmarked": 0},
            )
            group["legs"].append(
                {
                    "positionId": position.id,
                    "securityId": position.security_id,
                    "tradingSymbol": position.trading_symbol,
                    "strike": float(instrument.strike_price),
                    "optionType": instrument.option_type,
                    "product": position.product,
                    "netQuantity": net,
                    "lotSize": int(instrument.lot_size),
                    "averagePrice": float(position.average_price),
                    "mark": float(mark) if mark is not None else None,
                    "unrealizedPnl": round(unrealised, 2) if unrealised is not None else None,
                    "delta": row.get("delta"),
                    "strategyKey": position.strategy_key,
                }
            )
            if unrealised is None:
                group["unmarked"] += 1
            else:
                group["unrealizedPnl"] += unrealised

        margin = await self.margin.for_portfolio(portfolio_id) if groups else None
        by_underlying = margin.underlyings if margin else {}
        result = []
        for (underlying, _expiry), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
            group["legs"].sort(key=lambda leg: (leg["strike"], leg["optionType"]))
            group["hedged"] = any(leg["netQuantity"] > 0 for leg in group["legs"]) and any(
                leg["netQuantity"] < 0 for leg in group["legs"]
            )
            group["nakedShort"] = all(leg["netQuantity"] < 0 for leg in group["legs"])
            group["unrealizedPnl"] = None if group["unmarked"] else round(group["unrealizedPnl"], 2)
            result.append(group)
        return {
            "groups": result,
            "margin": margin.as_dict() if margin else None,
            "marginByUnderlying": {key: value.as_dict() for key, value in by_underlying.items()},
        }


def _basket_status(outcomes: List[str]) -> str:
    filled = {"FILLED"}
    open_ = {"OPEN", "PARTIALLY_FILLED", "PENDING"}
    if not outcomes:
        return BASKET_REJECTED
    if all(one in filled for one in outcomes):
        return BASKET_COMPLETE
    if all(one in ("REJECTED", "CANCELLED") for one in outcomes):
        return BASKET_REJECTED
    if all(one in filled | open_ for one in outcomes):
        return BASKET_RESTING
    return BASKET_PARTIAL
