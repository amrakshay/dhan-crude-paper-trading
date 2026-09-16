"""Expiry, strike and ATM resolution over the ingested instrument master.

The one thing this module exists to get right: **option expiry and futures
expiry are different dates.** For CRUDEOIL the September options expire
2026-09-17 while the September future expires 2026-09-21. The chain and the
underlying future therefore roll on different days, and an option expiry must
be mapped to its underlying future explicitly rather than by month name.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from src import config_utils
from src.core.time_utils import ist_today
from src.instruments.database.db_models.instrument_model import Instrument
from src.instruments.database.db_operations.instrument_repository import (
    InstrumentRepository,
)
from src.logging_config import get_logger

logger = get_logger("instruments.chain")


@dataclass
class ChainRow:
    """One strike, with its call and put contracts."""

    strike_price: Decimal
    call: Optional[Instrument] = None
    put: Optional[Instrument] = None


class ChainService:
    def __init__(self, repository: InstrumentRepository):
        self.repository = repository

    @staticmethod
    def _underlying_symbol() -> str:
        return config_utils.get_property_value("underlying.symbol", "CRUDEOIL")

    @staticmethod
    def _option_instrument_type() -> str:
        return config_utils.get_property_value("underlying.option_instrument_type", "OPTFUT")

    @staticmethod
    def _futures_instrument_type() -> str:
        return config_utils.get_property_value("underlying.futures_instrument_type", "FUTCOM")

    # --- expiries ----------------------------------------------------------
    async def list_option_expiries(self, include_past: bool = False) -> List[date]:
        return await self.repository.list_expiries(
            self._underlying_symbol(),
            self._option_instrument_type(),
            on_or_after=None if include_past else ist_today(),
        )

    async def list_futures_expiries(self, include_past: bool = False) -> List[date]:
        return await self.repository.list_expiries(
            self._underlying_symbol(),
            self._futures_instrument_type(),
            on_or_after=None if include_past else ist_today(),
        )

    async def nearest_option_expiries(self, count: int) -> List[date]:
        return (await self.list_option_expiries())[:count]

    # --- futures -----------------------------------------------------------
    async def get_near_future(self) -> Optional[Instrument]:
        """The front-month future: earliest futures expiry not yet passed."""
        futures = await self.repository.list_futures(
            self._underlying_symbol(), self._futures_instrument_type(), on_or_after=ist_today()
        )
        return futures[0] if futures else None

    async def resolve_underlying_future(self, option_expiry: date) -> Optional[Instrument]:
        """The future an option expiry actually settles against.

        Rule: the earliest futures contract whose expiry is on or after the
        option expiry. For CRUDEOIL this maps
            options 2026-09-17 -> SEP future (expires 2026-09-21)
            options 2026-10-15 -> OCT future (expires 2026-10-19)
            options 2026-11-17 -> NOV future (expires 2026-11-19)
        which is what makes the divergent roll dates safe: between 2026-09-17
        and 2026-09-21 the SEP future still trades with no options on it, and
        the near option expiry has already moved to October.
        """
        futures = await self.repository.list_futures(
            self._underlying_symbol(), self._futures_instrument_type()
        )
        for contract in futures:
            if contract.expiry_date and contract.expiry_date >= option_expiry:
                return contract
        logger.warning("No futures contract found on or after option expiry %s", option_expiry)
        return None

    # --- chain -------------------------------------------------------------
    async def get_chain(self, expiry: date) -> List[ChainRow]:
        """Strike-ordered rows with the CE and PE contract for each strike."""
        contracts = await self.repository.list_chain(self._underlying_symbol(), expiry)
        by_strike: Dict[Decimal, ChainRow] = {}
        for contract in contracts:
            if contract.strike_price is None:
                continue
            row = by_strike.setdefault(
                contract.strike_price, ChainRow(strike_price=contract.strike_price)
            )
            if contract.option_type == "CE":
                row.call = contract
            elif contract.option_type == "PE":
                row.put = contract
        return [by_strike[strike] for strike in sorted(by_strike)]

    async def list_strikes(self, expiry: date) -> List[Decimal]:
        return await self.repository.list_strikes(self._underlying_symbol(), expiry)

    @staticmethod
    def infer_strike_step(strikes: Sequence[Decimal]) -> Optional[Decimal]:
        """Most common gap between adjacent strikes.

        Derived from the data rather than hardcoded. CRUDEOIL is a uniform 50,
        but inferring it means a change in strike spacing does not silently
        mis-size the subscription window.
        """
        if len(strikes) < 2:
            return None
        gaps: Dict[Decimal, int] = {}
        ordered = sorted(strikes)
        for previous, current in zip(ordered, ordered[1:]):
            gap = current - previous
            if gap > 0:
                gaps[gap] = gaps.get(gap, 0) + 1
        return max(gaps, key=gaps.get) if gaps else None

    @staticmethod
    def resolve_atm_strike(
        strikes: Sequence[Decimal], spot: Optional[Decimal]
    ) -> Optional[Decimal]:
        """Strike closest to spot. Ties resolve to the lower strike."""
        if not strikes or spot is None:
            return None
        return min(sorted(strikes), key=lambda strike: (abs(strike - spot), strike))

    async def get_strike_window(
        self, expiry: date, spot: Optional[Decimal], window: int
    ) -> List[Instrument]:
        """Contracts for ATM +/- `window` strikes at one expiry.

        This is what the feed subscribes to. With no spot yet (before the first
        tick) it falls back to the middle of the listed strike range, so the
        first subscription is still sensible rather than empty.
        """
        rows = await self.get_chain(expiry)
        if not rows:
            return []

        strikes = [row.strike_price for row in rows]
        atm = self.resolve_atm_strike(strikes, spot)
        if atm is None:
            atm = strikes[len(strikes) // 2]

        centre = strikes.index(atm)
        low = max(0, centre - window)
        high = min(len(strikes), centre + window + 1)

        selected: List[Instrument] = []
        for row in rows[low:high]:
            if row.call is not None:
                selected.append(row.call)
            if row.put is not None:
                selected.append(row.put)
        return selected
