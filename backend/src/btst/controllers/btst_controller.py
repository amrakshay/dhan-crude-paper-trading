"""Orchestration for the BTST endpoints.

Turns service results and domain exceptions into HTTP, and does no business
logic of its own (backend `CLAUDE.md` section 1). A service that imported
FastAPI would be the bug.
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from src.btst.services.btst_service import BtstService, BtstServiceError
from src.database.session import get_session_factory
from src.logging_config import get_logger

logger = get_logger("btst.controller")


class BtstController:
    def __init__(self, session):
        self.session = session

    # --- resolution ---------------------------------------------------------
    def _service(self, strategy_key: str) -> BtstService:
        try:
            return BtstService.for_strategy(self.session, strategy_key)
        except BtstServiceError as error:
            # "Unknown strategy" is a 404; a module that exists and takes no
            # decisions of its own is a 400, because the strategy is real and
            # the request does not apply to it.
            status = 404 if "Unknown strategy" in str(error) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error

    @staticmethod
    def default_strategy_key() -> str:
        """The BTST module, found by its subscription kind rather than by name.

        A key spelled into the router would be a second place the strategy is
        named. There is one universe-subscribing module and it is this one; if
        a second ever appears the caller passes `strategyKey` explicitly, which
        every endpoint already accepts.
        """
        from src.strategies.services.strategy_definition import SubscriptionPolicy
        from src.strategies.services.strategy_registry import get_strategy_registry

        for definition in get_strategy_registry().all():
            if definition.subscription.kind == SubscriptionPolicy.UNIVERSE:
                return definition.key
        return "nse-btst-overnight"

    # --- reads --------------------------------------------------------------
    async def status(
        self, strategy_key: str, portfolio_id: Optional[int]
    ) -> Dict[str, Any]:
        return await self._service(strategy_key).status(portfolio_id)

    async def history(self, strategy_key: str, limit: int) -> Dict[str, Any]:
        return await self._service(strategy_key).history(limit=limit)

    async def performance(self, strategy_key: str) -> Dict[str, Any]:
        return await self._service(strategy_key).performance()

    def configuration(self, strategy_key: str) -> Dict[str, Any]:
        return self._service(strategy_key).configuration()

    def explain(self, strategy_key: str) -> Dict[str, Any]:
        return self._service(strategy_key).explain()

    async def health(
        self, strategy_key: str, portfolio_id: Optional[int]
    ) -> Dict[str, Any]:
        from src.btst.services.btst_health_service import BtstHealthService

        try:
            service = BtstHealthService.for_strategy(self.session, strategy_key)
        except BtstServiceError as error:
            status = 404 if "Unknown strategy" in str(error) else 400
            raise HTTPException(status_code=status, detail=str(error)) from error
        return await service.payload(portfolio_id)

    async def signals(
        self, strategy_key: str, portfolio_id: Optional[int]
    ) -> Dict[str, Any]:
        """TODAY'S SCAN, as it stands right now. The Signals tab's whole payload.

        A read: it computes the identical funnel the scan computes, journals
        nothing and places nothing. That is what makes it watchable during the
        session -- the interesting thing about this strategy between 14:30 and
        15:20 is the funnel filling up, and a page that could only show the
        result after the fact would miss all of it.
        """
        service = self._service(strategy_key)
        execution = await self._execution(service, portfolio_id)
        scan, blocked = await execution.scan_now()

        from src.btst.services.scan_service import FILTER_STAGES

        return {
            "strategyKey": service.definition.key,
            "asOfIst": _now_ist(),
            "error": scan.error,
            "blocked": blocked,
            "gateOn": scan.gate_on,
            "indexSymbol": scan.index_symbol,
            "indexClose": scan.index_close,
            "indexSma": scan.index_sma,
            "regimeEnforced": scan.regime_enforced,
            "fnoExcluded": scan.fno_excluded,
            "slots": service.parameters.slots,
            "funnel": [
                {"key": key, "label": label, "count": scan.counts.get(key)}
                for key, label in FILTER_STAGES
            ],
            "candidates": [
                {
                    "rank": one.rank,
                    "symbol": one.symbol,
                    "securityId": one.security_id,
                    "price": one.price,
                    "sessionHigh": one.session_high,
                    "sessionLow": one.session_low,
                    "sessionVolume": one.session_volume,
                    "volRatio": one.vol_ratio,
                    "clv": one.clv,
                    "breakoutHigh": one.breakout_high,
                    "aboveBreakout": one.above_breakout,
                    "momentum": one.momentum,
                    "sma": one.sma,
                    "fnoEligible": one.fno_eligible,
                    # Whether it would actually be bought, which is not the
                    # same as qualifying: only `slots` of them are.
                    "wouldTrade": one.rank <= service.parameters.slots,
                }
                for one in scan.candidates
            ],
            # The near-misses, so an afternoon watcher can see what is close
            # rather than only what qualified. Everything that failed at the
            # first filter is in the funnel counts instead -- 250 rows saying
            # "did not break out" is noise.
            "nearMisses": [
                {"symbol": one.symbol, "reason": one.reason, "stage": one.stage}
                for one in scan.rejections
                if one.stage in ("volume", "close_strength", "trend", "momentum")
            ][:40],
            "note": (
                "A live read of the filter funnel. It journals nothing and "
                "places nothing -- the scheduled scan is what decides. At about "
                "one signal every two sessions, an empty candidate list is the "
                "ordinary state."
            ),
        }

    # --- runs ---------------------------------------------------------------
    async def run_scan(
        self,
        strategy_key: str,
        portfolio_id: Optional[int],
        force: bool,
        place_orders: bool,
    ) -> Dict[str, Any]:
        service = self._service(strategy_key)
        portfolios = await self._portfolios(service.definition.key, portfolio_id)
        results: List[Dict[str, Any]] = []
        for one in portfolios:
            execution = await self._execution(service, one)
            outcome = await execution.run_scan(
                one, force=force, place_orders=place_orders
            )
            await self.session.commit()
            results.append({"portfolioId": one, **outcome.as_dict()})
        return {"runs": results}

    async def run_exit(
        self, strategy_key: str, portfolio_id: Optional[int]
    ) -> Dict[str, Any]:
        service = self._service(strategy_key)
        portfolios = await self._portfolios(service.definition.key, portfolio_id)
        results: List[Dict[str, Any]] = []
        for one in portfolios:
            execution = await self._execution(service, one)
            outcome = await execution.run_exit(one)
            await self.session.commit()
            results.append({"portfolioId": one, **outcome.as_dict()})
        return {"runs": results}

    # --- wiring -------------------------------------------------------------
    async def _execution(self, service: BtstService, portfolio_id: Optional[int]):
        from src.btst.database.db_operations.btst_repository import (
            BtstDecisionRepository,
            BtstHoldingRepository,
            BtstSessionRepository,
        )
        from src.btst.services.execution_service import BtstExecutionService
        from src.btst.services.journal_service import BtstJournalService
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )
        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )

        journal = BtstJournalService(
            BtstSessionRepository(self.session),
            BtstDecisionRepository(self.session),
            service.definition,
            service.parameters,
        )
        return BtstExecutionService(
            session=self.session,
            definition=service.definition,
            journal=journal,
            holdings=BtstHoldingRepository(self.session),
            bars=DailyBarRepository(self.session),
            instruments=InstrumentRepository(self.session),
            parameters=service.parameters,
        )

    async def _portfolios(
        self, strategy_key: str, portfolio_id: Optional[int]
    ) -> List[int]:
        """Which books to run against.

        One when the caller named one; otherwise every ACTIVE portfolio this
        strategy runs in. Not "the" portfolio: the same strategy may run in
        several and their books must not mix, so each gets its own record and
        its own orders.
        """
        if portfolio_id is not None:
            return [int(portfolio_id)]

        from src.portfolios.database.db_operations.portfolio_repository import (
            PortfolioRepository,
        )

        rows = await PortfolioRepository(self.session).portfolios_running(strategy_key)
        if not rows:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No active portfolio runs this strategy, so there is "
                    "nothing to run against. Attach it to one on the "
                    "Portfolios page."
                ),
            )
        return [row.id for row in rows]


def _now_ist() -> str:
    from src.core.time_utils import ist_now

    return ist_now().isoformat()
