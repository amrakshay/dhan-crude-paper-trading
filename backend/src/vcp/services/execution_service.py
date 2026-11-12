"""NSE VCP Swing, end to end: the nightly scan, the morning orders, the stop.

Three jobs, all reached from `module_hooks` on the shared clock:

NIGHTLY  (after the bars are refreshed; any hour OUTSIDE the session)
         Scans every symbol's NEW bars for bases, journals each one, and
         re-reads every open position's closes for a breakout, a close under
         the 50 EMA, or the holding cap. Places nothing.

MORNING  (at the open)
         Sells what the nightly flagged, then buys the session's candidates
         while the portfolio has Rs 1,50,000 available. A candidate is good for
         ONE morning only -- the first trading day after the bar it was seen
         on. A morning that was missed is reported, and the signal is not
         bought days later at a price nobody decided on (root CLAUDE.md
         section 4).

STOP     (every scheduler pass while the market is open)
         The static stop at the final contraction's low, against the live
         price. Not a separate task: the clock already wakes every 30 s, the
         stop is a daily-bar rule (the backtest filled at the stop or the
         open), and a new named task would be a new thing for both health
         pages to judge.

The CPU-heavy detection runs in a worker thread (`asyncio.to_thread`) one
symbol at a time, so the event loop -- and with it the feed and every other
strategy's work -- keeps running while the universe is scanned.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.constants import OrderSide, OrderType
from src.core.time_utils import ist_now, ist_today, utc_now
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.strategy_definition import StrategyDefinition
from src.vcp.database.db_models.vcp_models import (
    ACTION_BOUGHT,
    ACTION_FLAGGED,
    ACTION_HELD,
    ACTION_SKIPPED,
    ACTION_SOLD,
    EXIT_STOP,
    PHASE_BASE,
    PHASE_BROKEN_OUT,
    POSITION_EXITED,
    POSITION_NOT_HELD,
    POSITION_OPEN,
    RUN_MORNING,
    RUN_NIGHTLY,
    SIGNAL_CANDIDATE,
    SIGNAL_EXPIRED,
    SIGNAL_IGNORED,
    SIGNAL_NOT_TAKEN,
    SIGNAL_REPLAYED,
    SIGNAL_TAKEN,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SKIPPED,
)
from src.vcp.database.db_operations.vcp_repository import (
    VcpDecisionRepository,
    VcpPositionRepository,
    VcpScanStateRepository,
    VcpSessionRepository,
    VcpSignalRepository,
)
from src.vcp.services import signal_service as signals
from src.vcp.services.vcp_parameters import VcpParameters

logger = get_logger("vcp.execution")

# How many universe symbols may lack a scan cursor before the nightly refuses
# to run. A handful is normal (a new index member) and is replayed in line; the
# whole universe means the one-off bootstrap has not been run, and replaying
# 474 symbols here would hold the shared clock -- and the rotation's 09:16
# rebalance behind it -- for about eleven minutes.
MAX_UNSCANNED_SYMBOLS = 10

CALENDAR_ROLE = "calendar_index"


class VcpNotReady(Exception):
    """A run that cannot proceed, with the reason an operator can act on."""


def next_trading_day(day: date, definition: StrategyDefinition) -> date:
    """The next weekday the strategy trades. Not holiday-aware, the trade this
    application makes everywhere (backend CLAUDE.md 10h)."""
    nxt = day + timedelta(days=1)
    while nxt.weekday() not in definition.market_hours.trading_days:
        nxt += timedelta(days=1)
    return nxt


@dataclass
class RunOutcome:
    ok: bool = True
    detail: str = ""
    bought: int = 0
    sold: int = 0
    flagged: int = 0
    candidates: int = 0
    still_open: int = 0
    notes: List[str] = field(default_factory=list)


class VcpExecutionService:
    def __init__(self, session, definition: StrategyDefinition,
                 parameters: Optional[VcpParameters] = None, book=None, clock=None):
        self.session = session
        self.definition = definition
        self.parameters = parameters or VcpParameters.from_definition(definition)
        self._book = book
        self._clock = clock
        self.sessions = VcpSessionRepository(session)
        self.signals = VcpSignalRepository(session)
        self.decisions = VcpDecisionRepository(session)
        self.positions = VcpPositionRepository(session)
        self.cursors = VcpScanStateRepository(session)

    # --- collaborators, reached inside methods (backend CLAUDE.md section 1) --
    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    def _now(self) -> datetime:
        return self._clock() if self._clock is not None else ist_now()

    def _bars(self):
        from src.daily_bars.database.db_operations.daily_bar_repository import (
            DailyBarRepository,
        )

        return DailyBarRepository(self.session)

    def _instruments(self):
        from src.instruments.database.db_operations.instrument_repository import (
            InstrumentRepository,
        )

        return InstrumentRepository(self.session)

    def _order_service(self):
        from src.orders.database.db_operations.order_repository import OrderRepository
        from src.orders.services.order_service import OrderService
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        return OrderService(OrderRepository(self.session), self._instruments(),
                            PositionRepository(self.session), book=self._book)

    def _is_armed(self) -> bool:
        from src.strategies.services.strategy_registry import get_strategy_registry

        return bool(get_strategy_registry().is_armed(self.definition.key))

    @property
    def segment(self) -> str:
        return self.definition.instrument_sets[0].exchange_segment

    def _universe(self) -> List[str]:
        return sorted(self.definition.universe.symbols) if self.definition.universe else []

    async def newest_session(self) -> Optional[date]:
        """The calendar index's newest bar: the newest session NSE traded."""
        ref = self.definition.reference_instrument(CALENDAR_ROLE)
        if ref is None:
            return None
        dates = await self._bars().trading_dates(ref.symbol, ref.exchange_segment, limit=1)
        return dates[-1] if dates else None

    # =======================================================================
    # NIGHTLY
    # =======================================================================
    async def run_nightly(self, progress=None) -> RunOutcome:
        outcome = RunOutcome()
        key = self.definition.key
        session_date = await self.newest_session()
        if session_date is None:
            outcome.ok = False
            outcome.detail = ("No NIFTY 50 bars are stored, so there is no trading "
                              "calendar to decide a session against.")
            return outcome
        if await self.sessions.completed(key, RUN_NIGHTLY, session_date):
            outcome.detail = f"Session {session_date} already decided."
            return outcome

        universe = self._universe()
        cursors = await self.cursors.all_for(key)
        # Only names old enough to be scanned at all can be "missing": a recent
        # listing has no cursor by design until it has the history.
        missing = []
        for symbol in (s for s in universe if s not in cursors):
            if await self._bars().count_for(symbol, self.segment) >= self.parameters.minimum_listed_sessions:
                missing.append(symbol)
        record = await self.sessions.create(
            strategy_key=key, portfolio_id=None, session_date=session_date,
            run_kind=RUN_NIGHTLY, status=STATUS_RUNNING, started_at=utc_now(),
            universe_size=len(universe),
            parameters_json=json.dumps(self.parameters.as_dict(), default=str),
        )
        if len(missing) > MAX_UNSCANNED_SYMBOLS:
            outcome.ok = False
            outcome.detail = (
                f"{len(missing)} of {len(universe)} symbols have never been scanned. "
                f"Run the one-off bootstrap first: CONFIG_PATH=conf .venv/bin/python "
                f"scripts/vcp_bootstrap_scan.py. Replaying them here would hold the "
                f"shared clock for minutes."
            )
            await self.sessions.finish(record, STATUS_FAILED, outcome.detail)
            return outcome

        # Older candidates nobody acted on are spent: a signal is good for one
        # morning only.
        for stale in await self.signals.with_status(key, [SIGNAL_CANDIDATE]):
            if stale.seen_date < session_date:
                await self.signals.set_status(stale, SIGNAL_EXPIRED)

        memory_since = session_date - timedelta(days=400)
        remembered = await self.signals.remembered(key, universe, memory_since)
        latest = await self._bars().latest_dates(self.segment, universe)
        instruments: Dict[str, Any] = {}
        for one in await self._instruments().list_by_symbols(universe, self.segment):
            # One active EQUITY row per symbol is what the instrument set selects.
            if one.is_active and one.underlying_symbol not in instruments:
                instruments[one.underlying_symbol] = one

        scanned = detections = candidates = 0
        for i, symbol in enumerate(universe):
            if progress is not None:
                try:
                    progress(i, len(universe), symbol)
                except Exception:  # noqa: BLE001 - a watcher must not kill the run
                    pass
            newest = latest.get(symbol)
            if newest is None:
                continue
            through = cursors.get(symbol)
            if through is not None and newest <= through:
                continue
            rows = await self._bars().history(symbol, self.segment,
                                              limit=self.parameters.history_sessions,
                                              on_or_before=session_date)
            if len(rows) < self.parameters.minimum_listed_sessions:
                # Too recently listed: the backtest never scanned it either.
                continue
            bars = signals.bars_from_daily(symbol, rows)
            n = len(bars)
            if through is None:
                first = max(0, n - self.parameters.replay_sessions)
            else:
                at = signals.index_of(bars, through)
                first = (at + 1) if at is not None else max(0, n - self.parameters.replay_sessions)
            memory = signals.accepted_spans(bars, remembered.get(symbol, []))
            found = await asyncio.to_thread(signals.scan, bars, range(first, n), memory,
                                            self.parameters)
            scanned += 1
            inst = instruments.get(symbol)
            for base in found:
                tradable = base.seen_date == session_date
                status = base.status if tradable or base.status == SIGNAL_IGNORED else SIGNAL_REPLAYED
                reason = base.reason if tradable else (
                    f"Seen on {base.seen_date} while catching up; recorded so the "
                    f"base is not mistaken for a new one later, never traded. "
                    f"({base.reason})")
                await self.signals.create(
                    strategy_key=key, session_id=record.id, symbol=symbol,
                    security_id=str(inst.security_id) if inst is not None else None,
                    seen_date=base.seen_date, base_start_date=base.base_start_date,
                    pivot_price=_d(base.pivot), stop_price=_d(base.stop),
                    close_price=_d(base.close), atr=_d(base.atr), stop_atr=_d(base.stop_atr),
                    contractions=base.contractions, variant=base.variant,
                    score=_d(base.score), trend_template=base.trend_template,
                    status=status, reason=reason,
                )
                if tradable:
                    detections += 1
                    candidates += status == SIGNAL_CANDIDATE
            await self.cursors.advance(key, symbol, signals.to_date(bars.date[n - 1]))

        flagged = await self._review_positions(record)
        outcome.candidates = candidates
        outcome.flagged = flagged
        outcome.detail = (f"Session {session_date}: {scanned} symbol(s) scanned, "
                          f"{detections} base(s) seen, {candidates} candidate(s) for "
                          f"the next open, {flagged} exit(s) flagged.")
        await self.sessions.finish(record, STATUS_COMPLETED, outcome.detail,
                                   scanned_count=scanned, detections_count=detections,
                                   candidate_count=candidates, flagged_count=flagged)
        return outcome

    async def _review_positions(self, record) -> int:
        """Re-read every open position's closes: breakout, trail, cap, a stop
        the live monitor did not see."""
        flagged = 0
        for pos in await self.positions.open_for(self.definition.key):
            rows = await self._bars().history(pos.symbol, self.segment,
                                              limit=self.parameters.history_sessions,
                                              on_or_before=record.session_date)
            bars = signals.bars_from_daily(pos.symbol, rows)
            verdict = signals.evaluate_position(
                bars, signal_date=pos.signal_date, entry_date=pos.entry_date,
                stop=float(pos.stop_price), pivot=float(pos.pivot_price),
                checked_through=pos.last_bar_date, params=self.parameters)
            if verdict is None:
                continue
            fields: Dict[str, Any] = dict(
                breakout_date=verdict.breakout_date,
                phase=PHASE_BROKEN_OUT if verdict.breakout_date else PHASE_BASE,
                last_bar_date=verdict.last_bar_date, last_close=_d(verdict.last_close),
                last_ema=_d(verdict.last_ema), sessions_held=verdict.sessions_held,
            )
            if verdict.exit_due and not pos.exit_due:
                fields.update(exit_due=verdict.exit_due, exit_due_since=verdict.last_bar_date,
                              exit_reason=verdict.exit_reason)
                flagged += 1
                await self.decisions.create(
                    session_id=record.id, portfolio_id=pos.portfolio_id,
                    vcp_position_id=pos.id, symbol=pos.symbol, security_id=pos.security_id,
                    action=ACTION_FLAGGED, quantity=pos.quantity,
                    price=_d(verdict.last_close), reason=verdict.exit_reason)
            await self.positions.save(pos, **fields)
        return flagged

    # =======================================================================
    # MORNING
    # =======================================================================
    async def run_morning(self, portfolio_id: int,
                          price_wait_seconds: float = 0.0) -> RunOutcome:
        outcome = RunOutcome()
        key = self.definition.key
        today = self._now().date()
        nightly = await self.sessions.latest(key, RUN_NIGHTLY, statuses=[STATUS_COMPLETED])
        signal_session = nightly.session_date if nightly is not None else None
        # The session this morning acts for: the nightly's, if this is the
        # trading day right after it -- otherwise there is nothing to buy.
        fresh = signal_session is not None and next_trading_day(signal_session, self.definition) == today
        run_date = signal_session if fresh else today

        if await self.sessions.completed(key, RUN_MORNING, run_date, portfolio_id):
            outcome.detail = f"Morning run for {run_date} already done."
            return outcome

        record = await self.sessions.create(
            strategy_key=key, portfolio_id=int(portfolio_id), session_date=run_date,
            run_kind=RUN_MORNING, status=STATUS_RUNNING, started_at=utc_now())
        armed = self._is_armed()

        # --- exits first, so their cash is there for the buys ---------------
        for pos in await self.positions.open_for(key, portfolio_id):
            if not pos.exit_due:
                continue
            sold = await self._exit(record, pos, armed, pos.exit_due,
                                    pos.exit_reason or f"{pos.exit_due} exit due.")
            if sold is True:
                outcome.sold += 1
            elif sold is False:
                outcome.still_open += 1
        await self.session.commit()

        # --- entries ----------------------------------------------------------
        if not fresh:
            why = ("no completed nightly yet" if signal_session is None else
                   f"the newest decided session is {signal_session}, and its candidates "
                   f"were for {next_trading_day(signal_session, self.definition)}")
            outcome.notes.append(f"Nothing to buy: {why}.")
        else:
            todays = await self.signals.with_status(key, [SIGNAL_CANDIDATE, SIGNAL_TAKEN],
                                                    seen_date=signal_session)
            outcome.candidates = len(todays)
            if todays:
                await self._pin(todays, price_wait_seconds)
            for signal in todays:
                if await self._enter(record, signal, portfolio_id, armed):
                    outcome.bought += 1
                    await self.signals.set_status(signal, SIGNAL_TAKEN)
                await self.session.commit()

        outcome.detail = (f"{outcome.sold} sold, {outcome.bought} bought of "
                          f"{outcome.candidates} candidate(s)"
                          + ("" if armed else " -- NOT ARMED, nothing was placed")
                          + (f"; {outcome.still_open} exit(s) STILL OPEN" if outcome.still_open else "")
                          + (f". {' '.join(outcome.notes)}" if outcome.notes else "."))
        status = STATUS_FAILED if outcome.still_open else STATUS_COMPLETED
        outcome.ok = not outcome.still_open
        await self.sessions.finish(record, status, outcome.detail,
                                   bought_count=outcome.bought, sold_count=outcome.sold,
                                   candidate_count=outcome.candidates)
        return outcome

    async def close_out_candidates(self, session_date: date) -> None:
        """After every portfolio's morning: what nobody bought is NOT_TAKEN."""
        for signal in await self.signals.with_status(self.definition.key, [SIGNAL_CANDIDATE],
                                                     seen_date=session_date):
            await self.signals.set_status(signal, SIGNAL_NOT_TAKEN)

    async def _enter(self, record, signal, portfolio_id: int, armed: bool) -> bool:
        key = self.definition.key

        async def skip(reason: str, quantity=None, price=None) -> bool:
            await self.decisions.create(
                session_id=record.id, portfolio_id=portfolio_id, signal_id=signal.id,
                symbol=signal.symbol, security_id=signal.security_id,
                action=ACTION_SKIPPED, quantity=quantity, price=price, reason=reason)
            return False

        if not signal.security_id:
            return await skip("No instrument in the master for this symbol.")
        if await self.positions.get_open_for_security(key, portfolio_id, signal.security_id):
            return await skip("Already held in this portfolio; this strategy never adds.")
        refusal = await self._execution_refusal(signal.security_id)
        if refusal is not None:
            return await skip(refusal)

        row = self.book.get(str(signal.security_id)) or {}
        ltp = row.get("ltp")
        if not ltp:
            return await skip("No live price for this name yet, so it cannot be sized "
                              "or checked against its stop.")
        price = Decimal(str(ltp))
        if price <= signal.stop_price:
            return await skip(f"Opened at {price:,.2f}, at or under the stop "
                              f"{signal.stop_price:,.2f}: the base failed overnight.",
                              price=price)
        quantity = int(Decimal(str(self.parameters.position_rupees)) // price)
        if quantity <= 0:
            return await skip(f"One share costs {price:,.2f}, more than a position.", price=price)

        from src.portfolios.services.balance_service import BalanceService

        balance = await BalanceService(self.session, book=self._book).balance_for(int(portfolio_id))
        needed = Decimal(str(self.parameters.position_rupees))
        if balance.available < needed:
            return await skip(f"Rs {balance.available:,.0f} available, under the "
                              f"Rs {needed:,.0f} a position needs.", quantity, price)

        reason = (f"VCP {signal.variant} seen {signal.seen_date}: buy at the open. Stop "
                  f"{signal.stop_price:,.2f} (the final contraction's low), pivot "
                  f"{signal.pivot_price:,.2f}.")
        if not armed:
            return await skip(f"NOT ARMED -- would have bought {quantity} at about "
                              f"{price:,.2f}. {reason}", quantity, price)

        from src.orders.services.order_service import OrderValidationError

        try:
            order = await self._order_service().submit_paper_order(
                security_id=str(signal.security_id), side=OrderSide.BUY.value,
                order_type=OrderType.MARKET.value, lots=int(quantity),
                portfolio_id=int(portfolio_id), reason=reason, strategy_key=key)
        except OrderValidationError as error:
            return await skip(f"Entry refused: {error}", quantity, price)

        filled = int(order.filled_quantity or 0)
        if filled <= 0:
            return await skip("Entry placed and filled nothing: no depth.", quantity, price)
        fill = Decimal(str(order.average_fill_price)) if order.average_fill_price else price
        pos = await self.positions.create(
            strategy_key=key, portfolio_id=int(portfolio_id), signal_id=signal.id,
            symbol=signal.symbol, security_id=str(signal.security_id),
            signal_date=signal.seen_date, entry_date=self._now().date(), entry_at=utc_now(),
            entry_order_id=order.id, entry_price=fill, quantity=filled,
            stop_price=signal.stop_price, pivot_price=signal.pivot_price,
            phase=PHASE_BASE, status=POSITION_OPEN, sessions_held=0)
        await self.decisions.create(
            session_id=record.id, portfolio_id=portfolio_id, signal_id=signal.id,
            vcp_position_id=pos.id, symbol=signal.symbol, security_id=signal.security_id,
            action=ACTION_BOUGHT, quantity=filled, price=fill, order_id=order.id,
            reason=reason if filled == quantity else f"{reason} PARTIAL: {filled} of {quantity}.")
        return True

    # =======================================================================
    # EXITS (morning and stop)
    # =======================================================================
    async def _exit(self, record, pos, armed: bool, kind: str, reason: str) -> Optional[bool]:
        """Sell one position. True sold, False still open, None not attempted."""
        held = await self._open_quantity(pos)
        if held <= 0:
            await self.positions.save(pos, status=POSITION_NOT_HELD, exit_kind=kind,
                                      exited_at=utc_now(),
                                      exit_reason="Nothing left in the position book to sell.")
            return None
        if not armed:
            if record is not None:
                await self.decisions.create(
                    session_id=record.id, portfolio_id=pos.portfolio_id,
                    vcp_position_id=pos.id, symbol=pos.symbol, security_id=pos.security_id,
                    action=ACTION_HELD, quantity=held,
                    reason=f"NOT ARMED -- {kind} exit due and not placed. {reason}")
            return False
        refusal = await self._execution_refusal(pos.security_id)
        if refusal is not None:
            return False

        from src.orders.services.order_service import OrderValidationError

        try:
            order = await self._order_service().submit_paper_order(
                security_id=str(pos.security_id), side=OrderSide.SELL.value,
                order_type=OrderType.MARKET.value, lots=int(held), is_close_order=True,
                portfolio_id=int(pos.portfolio_id), strategy_key=self.definition.key,
                reason=f"VCP {kind.lower()} exit. {reason}")
        except OrderValidationError as error:
            if record is not None:
                await self.decisions.create(
                    session_id=record.id, portfolio_id=pos.portfolio_id,
                    vcp_position_id=pos.id, symbol=pos.symbol, security_id=pos.security_id,
                    action=ACTION_HELD, quantity=held, reason=f"Exit refused: {error}")
            logger.error("VCP %s exit for %s refused: %s", kind, pos.symbol, error)
            return False
        filled = int(order.filled_quantity or 0)
        if filled < held:
            logger.warning("VCP %s exit for %s filled %s of %s.", kind, pos.symbol, filled, held)
            return False
        price = Decimal(str(order.average_fill_price)) if order.average_fill_price else None
        await self.positions.save(pos, status=POSITION_EXITED, exit_kind=kind,
                                  exit_order_id=order.id, exit_price=price,
                                  exited_at=utc_now(), exit_reason=reason[:500])
        if record is not None:
            await self.decisions.create(
                session_id=record.id, portfolio_id=pos.portfolio_id, vcp_position_id=pos.id,
                symbol=pos.symbol, security_id=pos.security_id, action=ACTION_SOLD,
                quantity=filled, price=price, order_id=order.id, reason=reason)
        return True

    async def check_stops(self) -> int:
        """The live stop: sell any open position whose price is at or under it.

        Only while the market is open, never on a missing price, and inside the
        Closing Auction Session for an F&O name the trigger is RECORDED and the
        exit left for the next morning -- the same rule `SwingStopMonitor`
        follows (backend CLAUDE.md 10f).
        """
        if not market_clock.is_market_open(self.definition, now=self._now()):
            return 0
        armed = self._is_armed()
        sold = 0
        for pos in await self.positions.open_for(self.definition.key):
            if pos.exit_due == EXIT_STOP and pos.stop_triggered_at is not None:
                continue
            row = self.book.get(str(pos.security_id)) or {}
            ltp = row.get("ltp")
            if not ltp:
                continue
            price = Decimal(str(ltp))
            if price > pos.stop_price:
                continue
            reason = (f"Traded {price:,.2f}, at or under the stop {pos.stop_price:,.2f} "
                      f"(the final contraction's low).")
            if await self._execution_refusal(pos.security_id) is not None:
                await self.positions.save(pos, exit_due=EXIT_STOP,
                                          exit_due_since=self._now().date(),
                                          stop_triggered_at=utc_now(),
                                          exit_reason=f"{reason} Could not fill continuously; "
                                                      f"sells at the next open.")
                continue
            record = await self._stop_record()
            result = await self._exit(record, pos, armed, EXIT_STOP, reason)
            if result:
                sold += 1
            elif not armed:
                await self.positions.save(pos, exit_due=EXIT_STOP,
                                          exit_due_since=self._now().date(),
                                          stop_triggered_at=utc_now(), exit_reason=reason)
        return sold

    async def _stop_record(self):
        """The morning session row a live stop's decision is filed under."""
        return await self.sessions.latest(self.definition.key, RUN_MORNING)

    async def _open_quantity(self, pos) -> int:
        """What is ACTUALLY held, from the position book (backend CLAUDE.md 10h)."""
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        position = await PositionRepository(self.session).get_open_for_security(
            int(pos.portfolio_id), str(pos.security_id))
        if position is None:
            return 0
        return max(int(position.net_quantity or 0), 0)

    async def _execution_refusal(self, security_id: str) -> Optional[str]:
        now = self._now()
        instrument = await self._instruments().get_by_security_id(str(security_id))
        fno = bool(instrument is not None and instrument.fno_eligible)
        if market_clock.can_execute_continuously(self.definition, fno, now=now):
            return None
        return (f"Not placed at {now.strftime('%H:%M')} IST: this name cannot trade "
                f"continuously now" + (" (F&O-eligible: continuous trading ends at the "
                                        "closing auction)." if fno else "."))

    # --- the book -----------------------------------------------------------
    async def _pin(self, todays, wait_seconds: float = 0.0) -> None:
        """Make sure this morning's candidates are priced before any is sized.

        The warm-up three minutes earlier normally did this already, and then
        nothing here waits. A name with no live price (a restart at 09:16, a
        nightly that ran into the open, a feed that reconnected the same
        second) is pinned now, and the morning waits -- bounded by
        `wait_seconds` -- for its first tick. It used to sleep a fixed two
        seconds; on 2026-11-11 the Swing rebalance skipped every buy for the
        same race, and two seconds is no answer to a feed still connecting.
        A name still unpriced at the end is skipped with its recorded reason.
        """
        from src.market.services.feed_manager import get_feed_manager

        ids = {str(s.security_id) for s in todays if s.security_id}

        def unpriced():
            return {one for one in ids if not (self.book.get(one) or {}).get("ltp")}

        if not unpriced():
            return
        manager = get_feed_manager()
        manager.pin_instruments(self.definition.key, ids)
        await manager.resync()
        if wait_seconds <= 0:
            return
        loop = asyncio.get_running_loop()
        started = loop.time()
        while True:
            missing = unpriced()
            waited = loop.time() - started
            if not missing or waited >= wait_seconds:
                break
            await asyncio.sleep(min(0.5, wait_seconds - waited))
        if missing:
            logger.warning(
                "VCP morning for %s: %s of %s candidate(s) still had no live price "
                "after %.1f s; they will be skipped with a recorded reason.",
                self.definition.key, len(missing), len(ids), waited)
        else:
            logger.info("VCP morning for %s waited %.1f s for live prices.",
                        self.definition.key, waited)

    async def warm_book(self) -> int:
        """Subscribe this morning's candidates a few minutes before the open."""
        session_date = None
        nightly = await self.sessions.latest(self.definition.key, RUN_NIGHTLY,
                                             statuses=[STATUS_COMPLETED])
        if nightly is not None and next_trading_day(nightly.session_date,
                                                    self.definition) == self._now().date():
            session_date = nightly.session_date
        if session_date is None:
            return 0
        todays = await self.signals.with_status(self.definition.key, [SIGNAL_CANDIDATE],
                                                seen_date=session_date)
        from src.market.services.feed_manager import get_feed_manager

        ids = {str(s.security_id) for s in todays if s.security_id}
        manager = get_feed_manager()
        manager.pin_instruments(self.definition.key, ids)
        if ids:
            await manager.resync()
        return len(ids)

    async def release_book(self) -> None:
        from src.market.services.feed_manager import get_feed_manager

        manager = get_feed_manager()
        if manager.pinned_instruments(self.definition.key):
            manager.pin_instruments(self.definition.key, set())
            await manager.resync()

def _d(value) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(round(float(value), 4)))
