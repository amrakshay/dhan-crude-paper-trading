"""The VCP page's read model: one payload, assembled from what is stored.

Reads only. Nothing here decides, scans or trades; every figure is a journal
row, a position row, the live book's last price or `BalanceService`. Money
leaves as strings (Decimal), undefined leaves as null -- never 0 -- and stored
naive-UTC timestamps leave through `to_ist`, carrying their offset (backend
CLAUDE.md 10f-bis).
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.core.time_utils import to_ist
from src.vcp.database.db_models.vcp_models import (
    RUN_MORNING,
    RUN_NIGHTLY,
    SIGNAL_IGNORED,
    SIGNAL_REPLAYED,
    STATUS_COMPLETED,
)
from src.vcp.database.db_operations.vcp_repository import (
    VcpDecisionRepository,
    VcpPositionRepository,
    VcpScanStateRepository,
    VcpSessionRepository,
    VcpSignalRepository,
)
from src.vcp.services.execution_service import next_trading_day
from src.vcp.services.vcp_parameters import VcpParameters

STRATEGY_KEY = "nse-vcp-swing"


class VcpServiceError(Exception):
    pass


def _money(value) -> Optional[str]:
    return None if value is None else str(Decimal(value).quantize(Decimal("0.01")))


def _ts(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else to_ist(value).isoformat()


def _day(value) -> Optional[str]:
    return None if value is None else value.isoformat()


class VcpService:
    def __init__(self, session, book=None):
        self.session = session
        self._book = book

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    def _definition(self):
        from src.strategies.services.strategy_registry import get_strategy_registry

        definition = get_strategy_registry().get(STRATEGY_KEY)
        if definition is None:
            raise VcpServiceError(f"Unknown strategy {STRATEGY_KEY!r}.")
        return definition

    async def overview(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        from src.portfolios.database.db_operations.portfolio_repository import (
            PortfolioRepository,
        )
        from src.portfolios.services.balance_service import BalanceService
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.swing.services.schedule_settings import resolve_schedule

        definition = self._definition()
        registry = get_strategy_registry()
        params = VcpParameters.from_definition(definition)
        schedule = resolve_schedule(definition, params)
        sessions = VcpSessionRepository(self.session)
        signals = VcpSignalRepository(self.session)

        portfolios = await PortfolioRepository(self.session).portfolios_running(STRATEGY_KEY)
        portfolio_ids = [p.id for p in portfolios]
        # The header picker may be on another strategy's book; this page shows
        # a portfolio that RUNS this strategy, or none at all.
        if portfolio_id not in portfolio_ids:
            portfolio_id = portfolio_ids[0] if portfolio_ids else None

        universe = sorted(definition.universe.symbols) if definition.universe else []
        cursors = await VcpScanStateRepository(self.session).all_for(STRATEGY_KEY)
        nightly = await sessions.latest(STRATEGY_KEY, RUN_NIGHTLY, statuses=[STATUS_COMPLETED])
        last_nightly = await sessions.latest(STRATEGY_KEY, RUN_NIGHTLY)
        last_morning = await sessions.latest(STRATEGY_KEY, RUN_MORNING, portfolio_id=portfolio_id)

        today_signals: List[Dict[str, Any]] = []
        buy_on = None
        if nightly is not None:
            buy_on = next_trading_day(nightly.session_date, definition)
            rows = await signals.list_recent(STRATEGY_KEY, limit=100)
            today_signals = [self._signal(s) for s in rows
                             if s.seen_date == nightly.session_date
                             and s.status not in (SIGNAL_REPLAYED, SIGNAL_IGNORED)]

        balance = None
        if portfolio_id is not None:
            b = await BalanceService(self.session, book=self._book).balance_for(int(portfolio_id))
            balance = {"cash": _money(b.cash), "available": _money(b.available),
                       "equity": _money(b.equity)}

        positions = await VcpPositionRepository(self.session).list_recent(
            STRATEGY_KEY, portfolio_id, limit=100)
        decisions = await VcpDecisionRepository(self.session).list_recent(portfolio_id, limit=100)
        journal = await sessions.list_recent(STRATEGY_KEY, limit=30)
        recent_signals = await signals.list_recent(STRATEGY_KEY, limit=150)

        return {
            "strategyKey": STRATEGY_KEY,
            "label": definition.label,
            "enabled": registry.is_enabled(STRATEGY_KEY),
            "armed": registry.is_armed(STRATEGY_KEY),
            "schedule": {"nightlyAtIst": schedule.nightly_at, "morningAtIst": schedule.rebalance_at},
            "parameters": {
                "positionRupees": _money(params.position_rupees),
                "minContractions": params.min_contractions,
                "minStopAtr": params.min_stop_atr,
                "trailEmaSessions": params.trail_ema_sessions,
                "breakoutWindowSessions": params.breakout_window_sessions,
                "maxHoldSessions": params.max_hold_sessions,
            },
            "bootstrap": {
                "universe": len(universe),
                "scanned": len(cursors),
                "ready": len(cursors) > 0,
                "command": "CONFIG_PATH=conf .venv/bin/python scripts/vcp_bootstrap_scan.py",
            },
            "portfolioId": portfolio_id,
            "portfolios": [{"id": p.id, "name": p.name} for p in portfolios],
            "balance": balance,
            "lastNightly": self._session(last_nightly),
            "lastMorning": self._session(last_morning),
            "candidates": {
                "sessionDate": _day(nightly.session_date) if nightly else None,
                "buyOn": _day(buy_on),
                "signals": today_signals,
            },
            "positions": [self._position(p) for p in positions],
            "decisions": [self._decision(d) for d in decisions],
            "journal": [self._session(s) for s in journal],
            "signals": [self._signal(s) for s in recent_signals
                        if s.status not in (SIGNAL_REPLAYED, SIGNAL_IGNORED)],
        }

    # --- rows ---------------------------------------------------------------
    def _session(self, s) -> Optional[Dict[str, Any]]:
        if s is None:
            return None
        return {"id": s.id, "runKind": s.run_kind, "status": s.status,
                "sessionDate": _day(s.session_date), "portfolioId": s.portfolio_id,
                "startedAt": _ts(s.started_at), "completedAt": _ts(s.completed_at),
                "message": s.message}

    @staticmethod
    def _signal(s) -> Dict[str, Any]:
        return {"id": s.id, "symbol": s.symbol, "seenDate": _day(s.seen_date),
                "variant": s.variant, "contractions": s.contractions,
                "pivot": _money(s.pivot_price), "stop": _money(s.stop_price),
                "close": _money(s.close_price),
                "stopAtr": None if s.stop_atr is None else float(s.stop_atr),
                "score": None if s.score is None else float(s.score),
                "status": s.status, "reason": s.reason}

    def _position(self, p) -> Dict[str, Any]:
        row = self.book.get(str(p.security_id)) or {}
        ltp = row.get("ltp")
        last = Decimal(str(ltp)) if ltp else None
        to_stop = (float((last - p.stop_price) / last) if last else None)
        pnl = (float((last - p.entry_price) * p.quantity) if last and p.entry_price else None)
        return {"id": p.id, "symbol": p.symbol, "status": p.status, "phase": p.phase,
                "signalDate": _day(p.signal_date), "entryDate": _day(p.entry_date),
                "entryPrice": _money(p.entry_price), "quantity": p.quantity,
                "stop": _money(p.stop_price), "pivot": _money(p.pivot_price),
                "breakoutDate": _day(p.breakout_date), "lastClose": _money(p.last_close),
                "lastEma": _money(p.last_ema), "sessionsHeld": p.sessions_held,
                "lastPrice": _money(last), "toStopPct": to_stop,
                "unrealisedPnl": None if pnl is None else round(pnl, 2),
                "exitDue": p.exit_due, "exitKind": p.exit_kind,
                "exitPrice": _money(p.exit_price), "exitedAt": _ts(p.exited_at),
                "exitReason": p.exit_reason}

    @staticmethod
    def _decision(d) -> Dict[str, Any]:
        return {"id": d.id, "sessionId": d.session_id, "symbol": d.symbol,
                "action": d.action, "quantity": d.quantity, "price": _money(d.price),
                "orderId": d.order_id, "reason": d.reason, "at": _ts(d.created_at)}
