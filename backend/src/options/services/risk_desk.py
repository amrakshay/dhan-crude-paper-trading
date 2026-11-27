"""The options risk desk -- what a broker's RMS does without asking (Phase 3).

OPTIONS_TRADER_PLAN.md section 4.6. Three rules, taken from Dhan's own risk
policy and parameterised in the margin card's `risk_desk` block:

  INTRADAY_SQUARE_OFF  from 15:25 IST every INTRADAY option position is closed;
  MTM_LOSS             an unrealised option loss of 80% of the margin the
                       portfolio's option book blocks closes that book;
  MARGIN_SHORTFALL     a portfolio whose AVAILABLE money is negative -- a new
                       SPAN file can do that overnight -- is cut, largest
                       margin consumer first, until it is not.

It serves EVERY NSE option position on a SPAN-margined strategy, whichever
module opened it. Arming cannot opt out of it: a broker's risk desk does not
ask the algorithm. The engine names no strategy (src/options/__init__.py).

Shape: `bracket_monitor`'s. Its own task, its own session, off the tick path,
registered in TASK_DESCRIPTIONS and expected_task_names. Every action is an
ordinary paper order through the one exit path (shorts first, crossing the
spread, refusable), a row in `options_risk_actions`, and an alert. It acts
only while the market is open, and never on an unknown price: a position with
no mark is left out of the MTM rule rather than valued at zero.

A rule that has acted is not re-run for that portfolio until its condition
clears and comes back (MTM, shortfall) or until the next session (intraday);
an exit that could not fill is retried after `RETRY_SECONDS`, not every pass.
"""
import asyncio
import json
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src import config_utils
from src.core.time_utils import ist_now
from src.database.session import session_scope
from src.logging_config import get_logger
from src.options.database.db_models.risk_action_model import (
    RULE_EXPIRY_SETTLEMENT,
    RULE_INTRADAY_SQUARE_OFF,
    RULE_MARGIN_SHORTFALL,
    RULE_MTM_LOSS,
    RULE_PHYSICAL_DELIVERY,
)
from src.options.database.db_operations.risk_action_repository import RiskActionRepository
from src.strategies.services.strategy_definition import MarginModel
from src.strategies.services.strategy_registry import get_strategy_registry

logger = get_logger("options.risk_desk")

TASK_NAME = "options-rms"
RETRY_SECONDS = 60
PRODUCT_INTRADAY = "INTRADAY"
# Alert dedupe prefix; the catalogue entry matches on it.
ALERT_EVENT = "options-rms"


def enabled() -> bool:
    return config_utils.get_property_value_boolean("options.rms_enabled", True)


def _interval() -> float:
    return float(config_utils.get_property_value_int("options.rms_interval_seconds", 5))


def span_strategies():
    registry = get_strategy_registry()
    return [
        strategy for strategy in registry.enabled()
        if strategy.margin.model == MarginModel.NSE_SPAN
    ]


class RiskDesk:
    def __init__(self, book=None) -> None:
        self._book = book
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.runs = 0
        self.actions = 0
        self.last_error: Optional[str] = None
        self.last_run_at: Optional[datetime] = None
        # (portfolio, rule) -> session date the rule acted on, or the
        # monotonic time of the last attempt for a retry.
        self._acted: Dict[Tuple[int, str], Any] = {}
        self._attempted: Dict[Tuple[int, str], float] = {}

    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    # --- lifecycle -------------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not enabled():
            logger.info("The options risk desk is switched off (options.rms_enabled)")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=TASK_NAME)
        logger.info("Options risk desk started (every %.0f s)", _interval())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            await asyncio.sleep(_interval())
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the desk must not die
                self.last_error = type(error).__name__
                logger.exception("Options risk desk pass failed")

    # --- one pass --------------------------------------------------------------
    async def run_once(self, now: Optional[datetime] = None, price_provider=None) -> List[Dict[str, Any]]:
        """One pass: settle what has expired (any hour), then -- while the
        market is open -- the intraday, delivery, MTM and shortfall rules."""
        from src.options.services.expiry_service import ExpiryService
        from src.options.services.option_margin_service import load_card
        from src.strategies.services import market_clock

        now = now or ist_now()
        self.runs += 1
        self.last_run_at = now
        strategies = span_strategies()
        if not strategies:
            return []
        card = load_card(strategies[0].margin.card)
        keys = {strategy.key for strategy in strategies}
        index_scrips: Dict[str, int] = {}
        for strategy in strategies:
            for symbol, scrip in (strategy.module_section("options").get("index_underlyings") or {}).items():
                index_scrips[str(symbol).upper()] = int(scrip)
        market_open = any(market_clock.is_market_open(strategy, now) for strategy in strategies)

        taken: List[Dict[str, Any]] = []
        async with session_scope() as session:
            from src.positions.database.db_operations.position_repository import (
                PositionRepository,
            )

            async def load_open():
                return [
                    position for position in await PositionRepository(session).list_all(include_closed=False)
                    if position.strategy_key in keys and position.option_type and int(position.net_quantity or 0)
                ]

            open_positions = await load_open()

            # 1. Settlement: any hour, so a restart after the close still
            #    settles, and a position left open past its expiry is caught.
            expiry = ExpiryService(session, card, book=self.book, price_provider=price_provider)
            settled = await expiry.settle_due(open_positions, now, index_scrips)
            if settled:
                taken.extend(await self._record_settlement(session, settled, open_positions, now))
                open_positions = await load_open()

            if market_open:
                portfolios = sorted({int(position.portfolio_id) for position in open_positions})
                for portfolio_id in portfolios:
                    mine = [position for position in open_positions if int(position.portfolio_id) == portfolio_id]
                    for rule in (RULE_INTRADAY_SQUARE_OFF, RULE_PHYSICAL_DELIVERY, RULE_MTM_LOSS, RULE_MARGIN_SHORTFALL):
                        action = await self._apply(session, rule, portfolio_id, mine, card, now)
                        if action is not None:
                            taken.append(action)
                            mine = [
                                position for position in await PositionRepository(session).list_open(portfolio_id=portfolio_id)
                                if position.strategy_key in keys and position.option_type and int(position.net_quantity or 0)
                            ]
                            if not mine:
                                break
            if taken:
                await session.commit()
        self.actions += len(taken)
        return taken

    async def _record_settlement(self, session, settled, positions, now) -> List[Dict[str, Any]]:
        """One journal row (and alert) per portfolio for what expiry settled."""
        by_portfolio: Dict[int, List[Dict[str, Any]]] = {}
        for row in settled:
            by_portfolio.setdefault(int(row["portfolioId"]), []).append(row)
        actions = []
        for portfolio_id, rows in by_portfolio.items():
            done = [row for row in rows if row["outcome"] in ("SETTLED", "DELIVERED")]
            waiting = [row for row in rows if row["outcome"] == "WAITING"]
            if not done and waiting and self._acted.get((portfolio_id, "WAITING")) == now.date():
                continue   # already said today that it is waiting
            if waiting and not done:
                self._acted[(portfolio_id, "WAITING")] = now.date()
            outcome = "DONE" if not waiting else ("FAILED" if not done else "PARTIAL")
            detail = (
                f"Expiry settlement: {len([r for r in done if r['outcome'] == 'SETTLED'])} option position(s) "
                f"settled, {len([r for r in done if r['outcome'] == 'DELIVERED'])} share delivery(ies)"
                + (f"; {len(waiting)} waiting for the official close" if waiting else "")
                + "."
            )
            actions.append(await self._journal(session, RULE_EXPIRY_SETTLEMENT, portfolio_id, detail,
                                               rows, outcome, None, None, now))
        return actions

    async def _apply(self, session, rule, portfolio_id, positions, card, now):
        key = (portfolio_id, rule)
        last_try = self._attempted.get(key)
        if last_try is not None and time.monotonic() - last_try < RETRY_SECONDS:
            return None

        if rule == RULE_INTRADAY_SQUARE_OFF:
            if now.time() < card.intraday_square_off_at:
                return None
            targets = {position.id for position in positions if position.product == PRODUCT_INTRADAY}
            if not targets:
                return None
            detail = (
                f"Intraday square-off from {card.intraday_square_off_at:%H:%M} IST: "
                f"{len(targets)} Intraday option position(s) closed, shorts first."
            )
            return await self._exit(session, rule, portfolio_id, lambda position, _i: position.id in targets,
                                    detail, None, None, now)

        if rule == RULE_PHYSICAL_DELIVERY:
            from src.options.services.expiry_service import ExpiryService
            from src.portfolios.services.balance_service import BalanceService

            if now.time() < card.delivery_margin_from:
                return None
            if not any(position.expiry_date == now.date() for position in positions):
                return None
            balance = await BalanceService(session, book=self.book).balance_for(portfolio_id)
            refused = await ExpiryService(session, card, book=self.book).undeliverable(
                portfolio_id, now, float(balance.available)
            )
            if not refused:
                return None
            ids = {item["position"].id for item in refused}
            reasons = "; ".join(
                f"{item['instrument'].trading_symbol}: "
                + ("no shares to deliver" if item["obligation"] == "SELL_SHARES" else
                   f"cannot fund delivery of {item['contractValue']:,.0f}")
                for item in refused
            )
            detail = (
                f"Physical delivery from {card.delivery_margin_from:%H:%M} on expiry day: "
                f"obligations that cannot be met closed ({reasons})."
            )
            return await self._exit(session, rule, portfolio_id, lambda position, _i: position.id in ids,
                                    detail, None, None, now)

        if rule == RULE_MTM_LOSS:
            unrealised = self._unrealised(positions)
            if unrealised is None or unrealised >= 0:
                self._acted.pop(key, None)
                return None
            from src.options.services.option_margin_service import OptionMarginService

            margin = await OptionMarginService(session, book=self.book).for_portfolio(portfolio_id)
            base = margin.blocked   # what a broker shows as margin used
            if base <= 0:
                return None
            loss = -unrealised
            if loss < card.mtm_loss_fraction * base:
                self._acted.pop(key, None)
                return None
            if self._acted.get(key):
                return None
            ids = {position.id for position in positions}
            detail = (
                f"MTM loss {loss:,.0f} is {loss / base:.0%} of the {base:,.0f} margin the "
                f"option book blocks (threshold {card.mtm_loss_fraction:.0%}): every option "
                f"position in the portfolio closed, shorts first."
            )
            return await self._exit(session, rule, portfolio_id, lambda position, _i: position.id in ids,
                                    detail, loss, card.mtm_loss_fraction * base, now)

        if rule == RULE_MARGIN_SHORTFALL:
            from src.options.services.option_margin_service import OptionMarginService
            from src.portfolios.services.balance_service import BalanceService

            balance = await BalanceService(session, book=self.book).balance_for(portfolio_id)
            if balance.available >= 0:
                self._acted.pop(key, None)
                return None
            margin = await OptionMarginService(session, book=self.book).for_portfolio(portfolio_id)
            if not margin.underlyings:
                return None
            # Largest consumer first: the underlying whose book blocks most.
            worst = max(margin.underlyings.values(), key=lambda one: one.blocked)
            detail = (
                f"Margin shortfall: available {balance.available:,.2f} is below zero. "
                f"Closed the largest margin consumer, {worst.underlying} "
                f"({worst.blocked:,.0f} blocked), shorts first."
            )
            return await self._exit(
                session, rule, portfolio_id,
                lambda _p, instrument: instrument.underlying_symbol == worst.underlying,
                detail, -float(balance.available), 0.0, now,
            )
        return None

    def _unrealised(self, positions) -> Optional[float]:
        """Sum of unrealised option P&L at live marks; None if any is unmarked."""
        total = 0.0
        for position in positions:
            row = self.book.get(str(position.security_id)) or {}
            mark = row.get("ltp")
            if mark is None:
                return None
            total += (float(mark) - float(position.average_price)) * int(position.net_quantity)
        return total

    async def _exit(self, session, rule, portfolio_id, predicate, detail, measured, threshold, now):
        from src.options.services.basket_service import BasketService

        reason = f"Risk desk: {rule.replace('_', ' ').lower()}"
        orders = await BasketService(session, book=self.book).exit_matching(portfolio_id, predicate, reason)
        if not orders:
            return None
        filled = [order for order in orders if order.get("outcome") == "FILLED"]
        outcome = "DONE" if len(filled) == len(orders) else ("FAILED" if not filled else "PARTIAL")
        key = (portfolio_id, rule)
        if outcome == "DONE":
            self._acted[key] = now.date()
            self._attempted.pop(key, None)
        else:
            self._attempted[key] = time.monotonic()
        return await self._journal(session, rule, portfolio_id, detail, orders, outcome, measured, threshold, now)

    async def _journal(self, session, rule, portfolio_id, detail, orders, outcome, measured, threshold, now):
        from src.connections.services.alert_service import AlertService

        strategy_keys = sorted({order["strategyKey"] for order in orders if order.get("strategyKey")})
        row = await RiskActionRepository(session).add(
            portfolio_id=portfolio_id,
            rule=rule,
            strategy_keys=",".join(strategy_keys)[:255] or None,
            detail=detail[:500],
            measured=Decimal(str(round(measured, 2))) if measured is not None else None,
            threshold=Decimal(str(round(threshold, 2))) if threshold is not None else None,
            orders_json=json.dumps(orders, default=str),
            outcome=outcome,
        )
        severity = "CRITICAL" if outcome != "DONE" else "WARNING"
        for strategy_key in strategy_keys or [None]:
            await AlertService(session).raise_alert(
                kind="HEALTH",
                severity=severity,
                title=f"Risk desk: {rule.replace('_', ' ').title()} ({outcome})",
                body=f"Portfolio {portfolio_id}. {detail} Orders: "
                + ", ".join(f"{o.get('side', '')} {o.get('quantity', '')} {o['tradingSymbol']} -> {o['outcome']}" for o in orders),
                dedupe_key=f"health|{ALERT_EVENT}|{rule}|{portfolio_id}|{now.date().isoformat()}|{outcome}",
                strategy_key=strategy_key,
            )
        logger.warning("Risk desk %s on portfolio %s: %s (%s)", rule, portfolio_id, detail, outcome)
        return {"id": row.id, "portfolioId": portfolio_id, "rule": rule, "outcome": outcome,
                "detail": detail, "orders": orders}

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": enabled(),
            "intervalSeconds": _interval(),
            "runs": self.runs,
            "actions": self.actions,
            "lastRunAt": self.last_run_at.isoformat() if self.last_run_at else None,
            "error": self.last_error,
        }


_desk: Optional[RiskDesk] = None


def get_risk_desk() -> RiskDesk:
    global _desk
    if _desk is None:
        _desk = RiskDesk()
    return _desk


async def shutdown_risk_desk() -> None:
    global _desk
    if _desk is not None:
        await _desk.stop()
        _desk = None
