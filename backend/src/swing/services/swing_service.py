"""What the Swing Momentum page reads.

Everything the page shows is already stored -- `swing_sessions.ranking_json`,
`filter_counts_json`, `parameters_json`, the per-symbol `swing_decisions` rows
and the `swing_stops` table. This module assembles them; it decides nothing and
places nothing.

Three things it is careful about, all of them `frontend/CLAUDE.md` section 3
applied to an automated strategy:

* **Undefined is never zero.** A breadth that could not be measured is `None`,
  not 0%. An equity figure `BalanceService` withheld is `None` with the reason
  attached, not the cash balance standing in for it. A position with no mark
  has no distance to its stop.
* **Enabled and ARMED are reported separately**, because they are two switches
  and the difference is whether software may spend money.
* **There is no live track record**, and the payload says so until there is
  one. A page showing a CAGR from four weeks of paper trading next to the
  specification's 19.9% invites exactly the comparison that is not valid.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

import json

from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.strategy_definition import StrategyDefinition
from src.swing.database.db_models.swing_session_model import (
    RUN_NIGHTLY,
    RUN_REBALANCE,
)
from src.swing.database.db_models.swing_stop_model import (
    EXIT_MANUAL,
    EXIT_REGIME,
    EXIT_ROTATION,
    EXIT_TRAIL,
)
from src.swing.database.db_operations.swing_session_repository import (
    SwingDecisionRepository,
    SwingSessionRepository,
)
from src.swing.database.db_operations.swing_stop_repository import SwingStopRepository
from src.swing.services.ranking_service import RankingService
from src.swing.services.rebalance_planner import effective_gate
from src.swing.services.stop_service import StopService
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.service")

EXIT_LABELS = {
    EXIT_TRAIL: "Trailing stop",
    EXIT_ROTATION: "Rotation",
    EXIT_REGIME: "Regime exit",
    EXIT_MANUAL: "Closed by hand",
}


class SwingServiceError(Exception):
    pass


@dataclass
class SwingService:
    """Read models for the strategy page. Nothing here writes."""

    session: Any
    definition: StrategyDefinition
    parameters: SwingParameters
    book: Any = None

    @classmethod
    def for_strategy(cls, session, strategy_key: str, book=None) -> "SwingService":
        from src.strategies.services.strategy_registry import get_strategy_registry

        definition = get_strategy_registry().get(strategy_key)
        if definition is None:
            raise SwingServiceError(f"Unknown strategy module {strategy_key!r}")
        if not definition.automation.automated:
            raise SwingServiceError(
                f"{definition.label} is a discretionary module: it takes no "
                f"decisions of its own, so it has no decision journal to show."
            )
        return cls(
            session=session,
            definition=definition,
            parameters=SwingParameters.from_definition(definition),
            book=book,
        )

    # --- collaborators ------------------------------------------------------
    @property
    def sessions(self) -> SwingSessionRepository:
        return SwingSessionRepository(self.session)

    @property
    def decisions(self) -> SwingDecisionRepository:
        return SwingDecisionRepository(self.session)

    @property
    def stops(self) -> SwingStopRepository:
        return SwingStopRepository(self.session)

    def _ranking_service(self) -> RankingService:
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )

        return RankingService(
            DailyBarRepository(self.session), self.definition, self.parameters
        )

    def _balances(self):
        from src.portfolios.services.balance_service import BalanceService

        return BalanceService(self.session, book=self.book)

    def _equity_segment(self) -> str:
        for instrument_set in self.definition.instrument_sets:
            if instrument_set.from_universe:
                return instrument_set.exchange_segment
        return self.definition.exchange_segment

    # --- status -------------------------------------------------------------
    async def status(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """The headline: what the rule says right now, and whether it may act."""
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.swing.services.scheduler import get_swing_scheduler

        registry = get_strategy_registry()
        enabled = registry.is_enabled(self.definition.key)

        latest_nightly = await self.sessions.latest(self.definition.key, RUN_NIGHTLY)
        latest_rebalance = await self.sessions.latest(
            self.definition.key, RUN_REBALANCE
        )

        snapshot_payload: Optional[Dict[str, Any]] = None
        snapshot_error: Optional[str] = None
        if enabled:
            try:
                snapshot = await self._ranking_service().session_snapshot()
                gate = effective_gate(snapshot, self.parameters)
                snapshot_payload = snapshot.as_dict(top=15)
                snapshot_payload["effectiveGate"] = {
                    "variant": gate.variant,
                    "gateOn": gate.gate_on,
                    "liquidates": gate.liquidates,
                    "entriesAllowed": gate.entries_allowed,
                    # None, never 0: "could not be measured" and "measured as
                    # none" are different answers.
                    "slots": gate.slots,
                    "momentumFloor": gate.momentum_floor,
                }
            except Exception as error:  # noqa: BLE001 - the page must still render
                snapshot_error = str(error)
                logger.warning(
                    "Could not compute the live snapshot for %s: %s",
                    self.definition.key, error,
                )

        balance = None
        if portfolio_id is not None:
            balance = (await self._balances().balance_for(portfolio_id)).as_dict()

        return {
            "strategyKey": self.definition.key,
            "label": self.definition.label,
            "description": self.definition.description,
            "enabled": enabled,
            # Two switches, reported as two.
            "automated": self.definition.automation.automated,
            "armed": registry.is_armed(self.definition.key),
            "armedByDefault": self.definition.automation.armed_by_default,
            "marketOpen": market_clock.is_market_open(self.definition),
            "marketOpensAt": self.definition.market_hours.open.strftime("%H:%M"),
            "marketClosesAt": self.definition.market_hours.close.strftime("%H:%M"),
            "closingAuction": self._closing_auction_payload(),
            "snapshot": snapshot_payload,
            "snapshotError": snapshot_error,
            "latestNightly": self._session_payload(latest_nightly),
            "latestRebalance": self._session_payload(latest_rebalance),
            "scheduler": get_swing_scheduler().status(),
            "balance": balance,
            # Said on every payload, deliberately. The specification's 19.9% is
            # a survivorship-biased, in-sample backtest of a rule that has never
            # traded a rupee, and a page that shows this book's numbers beside
            # it without saying so invites exactly the wrong comparison.
            "trackRecord": {
                "live": False,
                "note": (
                    "No live track record. Every figure on this page comes from "
                    "paper orders in this database. The specification's 19.9% "
                    "CAGR is a backtest on today's Nifty 500 looked at backwards "
                    "-- survivorship-biased and chosen in-sample -- and its own "
                    "author expects 12-18% live."
                ),
            },
        }

    def _closing_auction_payload(self) -> Optional[Dict[str, Any]]:
        auction = self.definition.market_hours.closing_auction
        if auction is None:
            return None
        return {
            "appliesTo": auction.applies_to,
            "continuousClose": auction.continuous_close.strftime("%H:%M"),
            "auctionClose": auction.auction_close.strftime("%H:%M"),
            "note": (
                "For F&O-eligible names continuous cash trading ends at "
                f"{auction.continuous_close.strftime('%H:%M')}. A trailing stop "
                f"that fires after that is recorded and its exit waits for the "
                f"next session's open -- this simulator has no model of a call "
                f"auction."
            ),
        }

    # --- the open book ------------------------------------------------------
    async def book_for(self, portfolio_id: int) -> Dict[str, Any]:
        """Every open position, with its stop and the distance to it."""
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        positions = await PositionRepository(self.session).list_all(
            include_closed=False,
            strategy_key=self.definition.key,
            portfolio_id=portfolio_id,
        )
        balances = self._balances()
        latest = await self.sessions.latest(self.definition.key, RUN_NIGHTLY)
        ranks = self._ranks_from(latest)

        rows: List[Dict[str, Any]] = []
        unmarked = 0
        for position in positions:
            net = int(position.net_quantity or 0)
            if net == 0:
                continue
            mark = balances.mark_for(position.security_id)
            if mark is None:
                unmarked += 1
            stop = await self.stops.get_active(portfolio_id, position.security_id)
            entry = Decimal(str(position.average_price or 0))
            rows.append(
                {
                    "symbol": position.trading_symbol,
                    "securityId": position.security_id,
                    "quantity": net,
                    "averagePrice": str(entry),
                    "mark": str(mark) if mark is not None else None,
                    # None, not 0.00, when there is no mark. A P&L of zero
                    # beside a position that is down 8% is the worst possible
                    # way to say "no price".
                    "unrealised": (
                        str(((mark - entry) * net).quantize(Decimal("0.01")))
                        if mark is not None
                        else None
                    ),
                    "rank": ranks.get(position.trading_symbol),
                    "rotationExitRank": self.parameters.rotation_exit_rank,
                    "stop": (
                        StopService.describe(stop, mark) if stop is not None else None
                    ),
                    # A position with no stop is a real and visible state, not
                    # an absent field: the ATR was unavailable at entry and the
                    # next nightly ratchet will set one.
                    "hasStop": stop is not None,
                }
            )

        return {
            "portfolioId": portfolio_id,
            "positions": rows,
            "openPositions": len(rows),
            "maxPositions": self.parameters.max_positions,
            "unmarkedPositions": unmarked,
            "rankedAsOf": (
                latest.session_date.isoformat() if latest is not None else None
            ),
        }

    @staticmethod
    def _ranks_from(session_row) -> Dict[str, int]:
        """Ranks out of a stored session, including the HELD names' own.

        A holding that has fallen to rank 40 is exactly the row a rotation exit
        has to be explained by, which is why the journal stores it separately
        from the top of the list.
        """
        if session_row is None or not session_row.ranking_json:
            return {}
        try:
            payload = json.loads(session_row.ranking_json)
        except (TypeError, ValueError):
            return {}
        ranks: Dict[str, int] = {}
        for entry in payload.get("top", []):
            if entry.get("symbol") and entry.get("rank"):
                ranks[entry["symbol"]] = int(entry["rank"])
        for entry in payload.get("held", []):
            if entry.get("symbol") and entry.get("rank"):
                ranks[entry["symbol"]] = int(entry["rank"])
        return ranks

    # --- history ------------------------------------------------------------
    async def history(
        self, limit: int = 30, run_kind: Optional[str] = None
    ) -> Dict[str, Any]:
        rows = await self.sessions.list_recent(
            self.definition.key, limit=limit, run_kind=run_kind
        )
        return {
            "strategyKey": self.definition.key,
            "sessions": [self._session_payload(row) for row in rows],
            "count": len(rows),
        }

    async def session_detail(self, session_id: int) -> Dict[str, Any]:
        row = await self.sessions.get_by_id(session_id)
        if row is None or row.strategy_key != self.definition.key:
            raise SwingServiceError(f"No session {session_id} for this strategy")
        decisions = await self.decisions.for_session(session_id)
        payload = self._session_payload(row) or {}
        payload["decisions"] = [
            {
                "symbol": decision.symbol,
                "action": decision.action,
                "rank": decision.rank,
                "score": (
                    str(decision.score) if decision.score is not None else None
                ),
                "quantity": decision.quantity,
                "referencePrice": (
                    str(decision.reference_price)
                    if decision.reference_price is not None
                    else None
                ),
                "orderId": decision.order_id,
                "reason": decision.reason,
            }
            for decision in decisions
        ]
        payload["ranking"] = _load_json(row.ranking_json)
        payload["filterCounts"] = _load_json(row.filter_counts_json)
        payload["parameters"] = _load_json(row.parameters_json)
        return payload

    def _session_payload(self, row) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        breadth = None
        if row.breadth_liquid:
            breadth = round(int(row.breadth_above or 0) / int(row.breadth_liquid), 4)
        return {
            "id": row.id,
            "sessionDate": row.session_date.isoformat(),
            "runKind": row.run_kind,
            "status": row.status,
            "portfolioId": row.portfolio_id,
            "startedAt": row.started_at.isoformat() if row.started_at else None,
            "completedAt": row.completed_at.isoformat() if row.completed_at else None,
            "indexSymbol": row.index_symbol,
            "indexClose": _money(row.index_close),
            "indexSma": _money(row.index_sma),
            "gateOn": row.gate_on,
            "indexReturnOverWindow": _money(row.index_return_over_window),
            "entriesAllowed": row.entries_allowed,
            "breadthAbove": row.breadth_above,
            "breadthLiquid": row.breadth_liquid,
            # None when the denominator is zero: breadth that could not be
            # measured is not a breadth of 0%.
            "breadth": breadth,
            "slots": row.slots,
            "universeSize": row.universe_size,
            "symbolsWithBars": row.symbols_with_bars,
            "candidateCount": row.candidate_count,
            "message": row.message,
        }

    # --- performance ---------------------------------------------------------
    async def performance(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """The specification's own statistics, on this book's own history."""
        from src.orders.database.db_operations.order_repository import OrderRepository
        from src.reports.services.metrics_service import MetricsService
        from src.reports.services.pnl_service import PnlService

        metrics_service = MetricsService(self.session)
        trades = await metrics_service.closed_trades(
            strategy_key=self.definition.key, portfolio_id=portfolio_id
        )

        pnl = PnlService(OrderRepository(self.session), book=self.book)
        report = await pnl.build_report(
            strategy_key=self.definition.key, portfolio_id=portfolio_id
        )
        cash_flow_days = await self._cash_flow_days(portfolio_id)

        metrics = metrics_service.compute(
            trades, report.equity_curve, cash_flow_days
        )
        payload = metrics.as_dict()
        payload["exitMix"] = await self.exit_mix(portfolio_id)
        payload["realisedGross"] = str(report.realised_gross)
        payload["totalCharges"] = str(report.total_charges)
        payload["realisedNet"] = str(report.realised_net)
        payload["equityCurve"] = report.equity_curve
        payload["averageHoldSessions"] = await self._average_hold_sessions(trades)
        payload["trackRecord"] = {
            "live": False,
            "note": (
                "Paper orders only. These are this book's own numbers, not the "
                "backtest's -- the specification expects them to differ, and "
                "measuring that difference is the point."
            ),
        }
        return payload

    async def exit_mix(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """Trail stops against rotations, counted from `swing_stops`.

        Counted from the stop rows rather than by reading English out of a
        reason string: every closed stop carries `exit_kind`, set at the moment
        the position left. The specification's own mix is 348 trail stops
        against 324 rotations over 672 trades, and it is why the stop must not
        be tightened -- the trail exits are only 34% profitable and still
        average +2.45%, because winners leave through the ratchet too.
        """
        rows = await self.stops.history(self.definition.key, portfolio_id, limit=5000)
        counts: Dict[str, int] = {}
        for row in rows:
            if row.exit_kind is None:
                continue
            counts[row.exit_kind] = counts.get(row.exit_kind, 0) + 1
        total = sum(counts.values())
        return {
            "total": total,
            "byKind": [
                {
                    "kind": kind,
                    "label": EXIT_LABELS.get(kind, kind),
                    "count": count,
                    "share": round(count / total, 4) if total else None,
                }
                for kind, count in sorted(counts.items())
            ],
            "openStops": sum(1 for row in rows if row.exit_kind is None),
        }

    async def _average_hold_sessions(self, trades) -> Optional[float]:
        """Hold measured in SESSIONS, which is what the specification reports.

        Calendar days and sessions differ by roughly 40%, and the backtest's
        20.1 is sessions. The calendar is the regime index's own bar dates, the
        same one everything else here uses.
        """
        if not trades:
            return None
        reference = self.definition.reference_instrument(
            self.parameters.regime.index_role
        )
        if reference is None:
            return None

        from src.core.time_utils import to_ist
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )

        dates = await DailyBarRepository(self.session).trading_dates(
            reference.symbol, reference.exchange_segment
        )
        if not dates:
            return None
        index = {value: position for position, value in enumerate(dates)}

        spans: List[int] = []
        for trade in trades:
            opened = _nearest(index, dates, to_ist(trade.opened_at).date())
            closed = _nearest(index, dates, to_ist(trade.closed_at).date())
            if opened is None or closed is None:
                continue
            spans.append(max(closed - opened, 0))
        if not spans:
            return None
        return round(sum(spans) / len(spans), 2)

    async def _cash_flow_days(self, portfolio_id: Optional[int]) -> List[date]:
        if portfolio_id is None:
            return []
        from sqlalchemy import select

        from src.core.time_utils import to_ist
        from src.portfolios.database.db_models.portfolio_model import (
            ENTRY_DEPOSIT,
            ENTRY_WITHDRAWAL,
            CashLedgerEntry,
        )

        result = await self.session.execute(
            select(CashLedgerEntry.entry_at).where(
                CashLedgerEntry.portfolio_id == int(portfolio_id),
                CashLedgerEntry.entry_type.in_([ENTRY_DEPOSIT, ENTRY_WITHDRAWAL]),
            )
        )
        return sorted({to_ist(value).date() for (value,) in result.all()})


def _nearest(index: Dict[date, int], dates: List[date], value: date) -> Optional[int]:
    """Where a calendar date sits in the session calendar.

    A trade can open or close on a date with no bar (an order placed before the
    first session of the series, say), so this walks back to the last session
    on or before it rather than failing.
    """
    if value in index:
        return index[value]
    position = None
    for offset, session_date in enumerate(dates):
        if session_date <= value:
            position = offset
        else:
            break
    return position


def _load_json(value) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _money(value) -> Optional[str]:
    return str(value) if value is not None else None
