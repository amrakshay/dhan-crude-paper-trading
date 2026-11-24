"""Margin for a PORTFOLIO's NSE option positions -- the engine's money seam.

A broker margins the ACCOUNT, so SPAN nets every option position on the same
underlying in a portfolio whichever strategy opened it (OPTIONS_TRADER_PLAN.md
section 3). This is the only place that turns positions and prospective
orders into `span_margin.Leg`s; `BalanceService` (blocked money), the order
service (the funds check and the hedge-break check) and the baskets all ask
here, so they cannot disagree.

A position counts when its strategy's `margin.model` is `nse_span` and it is
an option. Everything else stays on its own strategy's model, unchanged.

Inputs, and where each comes from:
  * quantity, strike, expiry, right -- the position / instrument rows;
  * underlying price -- the stock's own live row, else the last option-chain
    snapshot (indices), else the SPAN file's settlement price;
  * option price -- the live book, else the SPAN file's price;
  * IV -- the live book (used only by the labelled fallback).
"""
import os
import time
from dataclasses import replace
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from src import config_utils
from src.core.time_utils import ist_today
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger
from src.options.services import span_margin
from src.options.services.span_margin import Leg, MarginCard, PortfolioMargin
from src.options.services.span_store import get_span_store
from src.strategies.services.strategy_definition import MarginModel
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("options.margin")

OPTION_TYPES = {"OPTIDX": span_margin.KIND_INDEX, "OPTSTK": span_margin.KIND_STOCK}
SPOT_MAX_AGE_SECONDS = 300

# symbol -> (monotonic time, price): the last underlying price an option-chain
# fetch reported. Indices have no row on the feed (their IDX_I ids collide with
# NSE_EQ ids, see ReferenceInstrument), so this is their freshest price.
_recent_spots: Dict[str, Tuple[float, float]] = {}
_cards: Dict[str, MarginCard] = {}


def remember_spot(symbol: str, price: Optional[float]) -> None:
    if price:
        _recent_spots[str(symbol)] = (time.monotonic(), float(price))


def load_card(name: str) -> MarginCard:
    if name not in _cards:
        directory = os.path.join(config_utils.get_config_path(), "margins")
        with open(os.path.join(directory, f"{name}.yaml"), "r", encoding="utf-8") as handle:
            _cards[name] = MarginCard(yaml.safe_load(handle))
    return _cards[name]


def uses_span(strategy_key: Optional[str]) -> bool:
    strategy = get_strategy_registry().get(strategy_key) if strategy_key else None
    return bool(strategy and strategy.margin.model == MarginModel.NSE_SPAN)


def card_for(strategy_key: str) -> MarginCard:
    strategy = get_strategy_registry().require(strategy_key)
    return load_card(strategy.margin.card)


class OptionMarginService:
    def __init__(self, session, book=None, today: Optional[date] = None):
        self.session = session
        self._book = book
        self.instruments = InstrumentRepository(session)
        self.today = today or ist_today()

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- legs ------------------------------------------------------------------
    def _spot(self, instrument, kind: str) -> Optional[float]:
        if kind == span_margin.KIND_STOCK:
            # The stock's own cash row, if it is on the feed.
            return None  # resolved in bulk in `_stock_spots`
        cached = _recent_spots.get(instrument.underlying_symbol)
        if cached and time.monotonic() - cached[0] < SPOT_MAX_AGE_SECONDS:
            return cached[1]
        return None

    async def _stock_spots(self, symbols: Iterable[str]) -> Dict[str, float]:
        spots: Dict[str, float] = {}
        for symbol in set(symbols):
            row = await self.instruments.find_cash_row(symbol, "NSE_EQ")
            live = self.book.get(str(row.security_id)) if row is not None else None
            if live and live.get("ltp"):
                spots[symbol] = float(live["ltp"])
            else:
                cached = _recent_spots.get(symbol)
                if cached and time.monotonic() - cached[0] < SPOT_MAX_AGE_SECONDS:
                    spots[symbol] = cached[1]
        return spots

    async def legs_for(self, changes: Sequence[Tuple[str, int]]) -> List[Leg]:
        """(security id, signed units) -> legs. Non-options are skipped."""
        ids = [str(security_id) for security_id, quantity in changes if quantity]
        instruments = {
            one.security_id: one for one in await self.instruments.get_many_by_security_ids(ids)
        }
        stock_symbols = [
            one.underlying_symbol for one in instruments.values() if one.instrument_type == "OPTSTK"
        ]
        stock_spots = await self._stock_spots(stock_symbols)
        legs: List[Leg] = []
        for security_id, quantity in changes:
            instrument = instruments.get(str(security_id))
            if instrument is None or not quantity:
                continue
            kind = OPTION_TYPES.get(instrument.instrument_type)
            if kind is None or not instrument.option_type or instrument.expiry_date is None:
                continue
            live = self.book.get(str(security_id)) or {}
            iv = live.get("iv")
            spot = (
                stock_spots.get(instrument.underlying_symbol)
                if kind == span_margin.KIND_STOCK
                else self._spot(instrument, kind)
            )
            legs.append(
                Leg(
                    underlying=instrument.underlying_symbol,
                    kind=kind,
                    right="C" if instrument.option_type == "CE" else "P",
                    expiry=instrument.expiry_date,
                    strike=float(instrument.strike_price),
                    quantity=int(quantity),
                    spot=spot,
                    iv=(float(iv) / 100.0) if iv else None,
                    price=float(live["ltp"]) if live.get("ltp") else None,
                    ref=str(security_id),
                )
            )
        return legs

    async def position_changes(self, portfolio_id: int) -> List[Tuple[str, int]]:
        """Every open NSE option position in the portfolio margined on SPAN."""
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        positions = await PositionRepository(self.session).list_open(portfolio_id=int(portfolio_id))
        return [
            (str(position.security_id), int(position.net_quantity or 0))
            for position in positions
            if int(position.net_quantity or 0) and uses_span(position.strategy_key)
            and position.option_type
        ]

    # --- margin ----------------------------------------------------------------
    def _compute(self, legs: List[Leg], card: MarginCard) -> PortfolioMargin:
        return span_margin.compute(legs, get_span_store().current(), card, self.today)

    async def for_portfolio(self, portfolio_id: int, card: Optional[MarginCard] = None) -> PortfolioMargin:
        changes = await self.position_changes(portfolio_id)
        card = card or self._default_card()
        return self._compute(await self.legs_for(changes), card)

    async def with_changes(
        self, portfolio_id: int, changes: Sequence[Tuple[str, int]], card: MarginCard
    ) -> Tuple[PortfolioMargin, PortfolioMargin]:
        """(before, after) for the portfolio with these orders added."""
        current = await self.position_changes(portfolio_id)
        before = self._compute(await self.legs_for(current), card)
        merged: Dict[str, int] = {}
        for security_id, quantity in [*current, *changes]:
            merged[str(security_id)] = merged.get(str(security_id), 0) + int(quantity)
        after = self._compute(await self.legs_for(list(merged.items())), card)
        return before, after

    async def each_alone(self, changes: Sequence[Tuple[str, int]], card: MarginCard) -> float:
        """Sum of each leg margined on its own: a broker's "overall margin"."""
        total = 0.0
        for leg in await self.legs_for(changes):
            total += self._compute([leg], card).total
        return total

    @staticmethod
    def _default_card() -> MarginCard:
        """The card of the first enabled strategy on SPAN; every NSE module
        names the same exchange card today."""
        for strategy in get_strategy_registry().all():
            if strategy.margin.model == MarginModel.NSE_SPAN:
                return load_card(strategy.margin.card)
        raise LookupError("No strategy is configured with margin.model nse_span")


def with_quantity(leg: Leg, quantity: int) -> Leg:
    return replace(leg, quantity=quantity)
