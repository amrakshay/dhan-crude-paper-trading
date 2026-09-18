"""Swing Momentum orchestration.

Turns the read models in `swing_service` and the two runs in
`execution_service` / `swing_runner` into HTTP, and nothing else. Services
raise domain exceptions; this is where they become status codes.

The two manual-run endpoints exist because an operator has to be able to see
the strategy decide on demand rather than waiting for 18:15 -- and because the
first thing anyone does with an automated strategy is run it once by hand to
find out what it would do. They obey exactly the same gates as the scheduled
runs: a switched-off strategy refuses, and an unarmed one decides and records
without placing anything.
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.swing.services.swing_service import SwingService, SwingServiceError

logger = get_logger("swing.controller")

# Cross-package imports go INSIDE the functions that need them. `src.swing`
# reaches orders, positions, portfolios and the feed, and every one of those
# reads the strategy registry (backend/CLAUDE.md section 1).


class SwingController:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _service(self, strategy_key: str) -> SwingService:
        try:
            return SwingService.for_strategy(self.session, strategy_key)
        except SwingServiceError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    # --- reads --------------------------------------------------------------
    async def status(
        self, strategy_key: str, portfolio_id: Optional[int]
    ) -> Dict[str, Any]:
        return await self._service(strategy_key).status(portfolio_id)

    async def book(self, strategy_key: str, portfolio_id: int) -> Dict[str, Any]:
        return await self._service(strategy_key).book_for(portfolio_id)

    async def history(
        self, strategy_key: str, limit: int, run_kind: Optional[str]
    ) -> Dict[str, Any]:
        return await self._service(strategy_key).history(limit=limit, run_kind=run_kind)

    async def session_detail(
        self, strategy_key: str, session_id: int
    ) -> Dict[str, Any]:
        try:
            return await self._service(strategy_key).session_detail(session_id)
        except SwingServiceError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    async def performance(
        self, strategy_key: str, portfolio_id: Optional[int]
    ) -> Dict[str, Any]:
        return await self._service(strategy_key).performance(portfolio_id)

    def explain(self, strategy_key: str) -> Dict[str, Any]:
        """The rule as configured -- what the "How it works" page renders."""
        return self._service(strategy_key).explain()

    async def stops(
        self, strategy_key: str, portfolio_id: Optional[int], limit: int
    ) -> Dict[str, Any]:
        service = self._service(strategy_key)
        rows = await service.stops.history(strategy_key, portfolio_id, limit=limit)
        from src.swing.services.stop_service import StopService

        balances = service._balances()  # noqa: SLF001
        return {
            "strategyKey": strategy_key,
            "stops": [
                StopService.describe(row, balances.mark_for(row.security_id))
                for row in rows
            ],
            "count": len(rows),
        }

    # --- manual runs ---------------------------------------------------------
    async def run_nightly(
        self, strategy_key: str, portfolio_id: int, force: bool
    ) -> Dict[str, Any]:
        """Decide one session by hand. Places no orders, ever."""
        definition, service, runner = await self._build(strategy_key)
        try:
            record = await runner.run_nightly(
                portfolio_id=portfolio_id,
                holdings=await service._holdings(portfolio_id),  # noqa: SLF001
                force=force,
            )
        except Exception as error:  # noqa: BLE001
            logger.exception("Manual swing nightly failed for %s", strategy_key)
            raise HTTPException(status_code=400, detail=str(error)) from error

        await self.session.commit()
        return {
            "sessionId": record.session_id,
            "sessionDate": record.session_date.isoformat(),
            "runKind": record.run_kind,
            "status": record.status,
            "decisions": record.decisions,
            "message": record.message,
            "extras": record.extras,
            # Stated on the response, because the whole point of the nightly
            # run is that it decides and does not trade.
            "ordersPlaced": 0,
        }

    async def run_rebalance(
        self, strategy_key: str, portfolio_id: int, force: bool
    ) -> Dict[str, Any]:
        from src.swing.services.execution_service import SwingExecutionError

        _definition, service, _runner = await self._build(strategy_key)
        try:
            outcome = await service.run_rebalance(
                portfolio_id=portfolio_id,
                force=force,
                # A manual run has not had the scheduler's warm-up, so it waits
                # briefly for the book it just subscribed to produce depth.
                warm_seconds=2.0,
            )
        except SwingExecutionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:  # noqa: BLE001
            logger.exception("Manual swing rebalance failed for %s", strategy_key)
            raise HTTPException(status_code=400, detail=str(error)) from error

        await self.session.commit()
        return outcome.as_dict()

    async def _build(self, strategy_key: str):
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.swing.database.db_operations.swing_session_repository import (
            SwingDecisionRepository,
            SwingSessionRepository,
        )
        from src.swing.database.db_operations.swing_stop_repository import (
            SwingStopRepository,
        )
        from src.swing.services.execution_service import SwingExecutionService
        from src.swing.services.journal_service import SwingJournalService
        from src.swing.services.ranking_service import RankingService
        from src.swing.services.stop_service import StopService
        from src.swing.services.swing_parameters import SwingParameters
        from src.swing.services.swing_runner import SwingRunner

        definition = get_strategy_registry().get(strategy_key)
        if definition is None or not definition.automation.automated:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{strategy_key!r} is not an automated strategy module, so "
                    f"it has no scheduled runs to trigger."
                ),
            )

        parameters = SwingParameters.from_definition(definition)
        ranking = RankingService(
            DailyBarRepository(self.session), definition, parameters
        )
        journal = SwingJournalService(
            SwingSessionRepository(self.session),
            SwingDecisionRepository(self.session),
        )
        stops = StopService(
            SwingStopRepository(self.session), ranking, parameters, definition.key
        )
        service = SwingExecutionService(
            session=self.session,
            definition=definition,
            ranking=ranking,
            journal=journal,
            stops=stops,
            parameters=parameters,
        )
        runner = SwingRunner(ranking, journal, definition, parameters, stops=stops)
        return definition, service, runner

    # --- listing -------------------------------------------------------------
    @staticmethod
    def automated_strategies() -> List[Dict[str, Any]]:
        """Which modules this page can show at all."""
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        return [
            {
                "key": definition.key,
                "label": definition.label,
                "enabled": registry.is_enabled(definition.key),
                "armed": registry.is_armed(definition.key),
            }
            for definition in registry.automated()
        ]
