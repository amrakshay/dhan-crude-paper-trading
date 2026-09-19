"""The two runs: the afternoon scan, and the morning exit.

The only module in this package that places an order.

**The scan decides and buys in the same pass**, which is the opposite of the
rotation's arrangement and is forced by what it decides on. The rotation
decides at 18:15 on finished bars and trades at 09:16 the next morning, and the
decision survives the night because the bars do. This decides on the state of
the session at 15:20 -- a running high, a running low, a cumulative volume --
and none of that survives ten minutes, let alone overnight. A decision that
cannot be carried forward has to be acted on where it is taken.

**The exit is the most defended thing in the module, and section 10.1 is the
reason.** The same signals held to the next CLOSE instead of the next OPEN
measure a 49.0% win rate against 71.4%, and a net edge of +0.128% against
+0.317%. A missed exit does not cost a session's decision the way a missed
rotation run does; it costs the trade thesis. Four things follow:

  * the exit runs **unconditionally**. There is no rank to check, no stop to
    consult and no condition under which a position is kept;
  * a position that could not be sold is recorded FAILED and **stays open**, so
    the next pass tries again rather than the row going quiet;
  * a sale after the window is `EXITED_LATE`, a status of its own, carrying how
    many minutes late it was -- not a footnote on a normal exit;
  * anything still open raises an **alert**, because a log line saying so is
    read by nobody at 09:30.

**Nothing here has a stop.** `stop_service` and `stop_monitor` are not imported
and must not be: B15 is "none" and none is possible, since the only risk window
is one in which no order can execute at any price.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src.btst.database.db_models.btst_session_model import (
    ACTION_BOUGHT,
    ACTION_HELD,
    ACTION_NOT_ENTERED,
    ACTION_SKIPPED,
    ACTION_SOLD,
    EXIT_DONE,
    EXIT_FAILED,
    EXIT_LATE,
    EXIT_NOT_HELD,
    EXIT_PENDING,
    RUN_EXIT,
    RUN_SCAN,
    STATUS_COMPLETED,
    STATUS_SKIPPED,
)
from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_policy import BtstPolicy, resolve_policy
from src.btst.services.btst_schedule import EffectiveSchedule, resolve_schedule
from src.btst.services.journal_service import (
    BtstJournalService,
    Decision,
    SessionRecord,
    rejection_decisions,
)
from src.btst.services import scan_service
from src.btst.services.scan_service import Candidate, Quote, ScanResult
from src.constants import OrderSide, OrderType
from src.core.time_utils import ist_now, ist_today, utc_now
from src.logging_config import get_logger
from src.strategies.services import market_clock
from src.strategies.services.strategy_definition import StrategyDefinition

logger = get_logger("btst.execution")


class BtstExecutionError(Exception):
    pass


def max_staleness_days() -> int:
    """How many calendar days of staleness the scan will still trade on.

    The daily bars supply five of the seven filters -- the 55-day high, both
    averages, the SMA and the momentum -- so stale bars do not merely delay a
    decision here, they produce a wrong one against a live price. Tighter than
    the rotation's default for that reason.

    Module-level so the Health tab can show the operator the SAME sentence the
    scan would refuse with, rather than a second description that could drift.
    """
    from src import config_utils

    return config_utils.get_property_value_int("btst.max_bar_staleness_days", 3)


def staleness_reason(newest_session: Optional[date]) -> Optional[str]:
    """Why the stored bars are too old to scan on, or None."""
    if newest_session is None:
        return (
            "Refusing to scan: there are no stored daily bars for this "
            "strategy's universe at all. Five of its seven filters are "
            "computed from them. Run scripts/import_daily_bars.py, or let the "
            "rotation's nightly refresh run -- both strategies share the same "
            "`daily_bars` table and the same universe."
        )
    behind = (ist_today() - newest_session).days
    limit = max_staleness_days()
    if behind <= limit:
        return None
    return (
        f"Refusing to scan: the newest stored session is "
        f"{newest_session.isoformat()}, {behind} day(s) ago, against a limit "
        f"of {limit}. Five of this strategy's seven filters come from those "
        f"bars -- the 55-day high, both 20-day averages, the 200-day SMA and "
        f"the six-month momentum -- and comparing a LIVE price against a "
        f"stale 55-day high is how you buy a breakout that happened last week. "
        f"Nothing was placed."
    )[:500]


@dataclass
class ScanOutcome:
    record: Optional[SessionRecord] = None
    armed: bool = False
    candidates: int = 0
    picked: int = 0
    placed: int = 0
    refused_outside_hours: int = 0
    equity: Optional[Decimal] = None
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sessionId": self.record.session_id if self.record else None,
            "sessionDate": (
                self.record.session_date.isoformat()
                if self.record and self.record.session_date
                else None
            ),
            "status": self.record.status if self.record else None,
            "armed": self.armed,
            "candidates": self.candidates,
            "picked": self.picked,
            "placed": self.placed,
            "refusedOutsideHours": self.refused_outside_hours,
            "equity": str(self.equity) if self.equity is not None else None,
            "message": self.record.message if self.record else None,
            "notes": list(self.notes),
        }


@dataclass
class ExitOutcome:
    record: Optional[SessionRecord] = None
    armed: bool = False
    due: int = 0
    sold: int = 0
    late: int = 0
    failed: int = 0
    # Rows whose position was NOT HELD when the exit reached them -- closed by
    # hand, or sold by an earlier pass. Counted apart from `sold` because this
    # job did not sell them, and apart from `failed` because nothing is stuck:
    # `still_open` subtracts both, or the alarm would fire for a position that
    # is not there.
    already_closed: int = 0
    still_open: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sessionId": self.record.session_id if self.record else None,
            "status": self.record.status if self.record else None,
            "armed": self.armed,
            "due": self.due,
            "sold": self.sold,
            "late": self.late,
            "failed": self.failed,
            "alreadyClosed": self.already_closed,
            "stillOpen": self.still_open,
            "message": self.record.message if self.record else None,
            "notes": list(self.notes),
        }


class BtstExecutionService:
    """One scan or one exit, end to end."""

    def __init__(
        self,
        session,
        definition: StrategyDefinition,
        journal: BtstJournalService,
        holdings,
        bars,
        instruments,
        parameters: Optional[BtstParameters] = None,
        policy: Optional[BtstPolicy] = None,
        schedule: Optional[EffectiveSchedule] = None,
        book=None,
        clock=None,
    ):
        self.session = session
        self.definition = definition
        self.journal = journal
        self.holdings = holdings
        self.bars = bars
        self.instruments = instruments
        self.parameters = parameters or BtstParameters.from_definition(definition)
        # Resolved ONCE and carried as a value from here on. Not re-read
        # mid-run: this policy decides the UNIVERSE, so a change half-way
        # through would rank two different populations against each other.
        self.policy = policy or resolve_policy(definition, self.parameters)
        self.schedule = schedule or resolve_schedule(definition, self.parameters)
        self._book = book
        # What time it is, in IST. Injectable so the market-hours guard and the
        # lateness arithmetic can both be exercised without waiting for 09:16.
        self._clock = clock

    # --- collaborators, reached inside methods (backend/CLAUDE.md section 1) --
    @property
    def book(self):
        if self._book is None:
            from src.market.services.feed_manager import get_feed_manager

            self._book = get_feed_manager().book
        return self._book

    def _now(self) -> datetime:
        return self._clock() if self._clock is not None else ist_now()

    def _balance_service(self):
        from src.portfolios.services.balance_service import BalanceService

        return BalanceService(self.session, book=self._book)

    def _order_service(self):
        from src.orders.database.db_operations.order_repository import OrderRepository
        from src.orders.services.order_service import OrderService
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        return OrderService(
            OrderRepository(self.session),
            self.instruments,
            PositionRepository(self.session),
            book=self._book,
        )

    def _is_armed(self) -> bool:
        from src.strategies.services.strategy_registry import get_strategy_registry

        return bool(get_strategy_registry().is_armed(self.definition.key))

    # =======================================================================
    # THE SCAN
    # =======================================================================
    async def run_scan(
        self, portfolio_id: int, force: bool = False, place_orders: bool = True
    ) -> ScanOutcome:
        """Read the live session, decide, and buy.

        `place_orders=False` is what the Signals tab and the manual "scan now"
        button use: the identical computation, journalled identically, with
        nothing placed. That is deliberate and is the same property the
        rotation's unarmed run has -- a scan somebody triggered to look at must
        not be distinguishable, in the record, from one that traded.
        """
        started = utc_now()
        now = self._now()
        outcome = ScanOutcome()
        outcome.armed = place_orders and self._is_armed()
        today = now.date()

        # A closed market is a real outcome, not a failure. Recorded rather
        # than skipped silently, so the missed-run detector can tell "the job
        # ran and there was nothing to read" from "the job did not run".
        if not market_clock.is_market_open(self.definition, now=now):
            outcome.record = await self.journal.record(
                portfolio_id=portfolio_id,
                session_date=today,
                run_kind=RUN_SCAN,
                status=STATUS_SKIPPED,
                message=(
                    f"Skipped at {now.strftime('%H:%M')} IST: the market is "
                    f"closed. This strategy decides on the session SO FAR, so "
                    f"outside the session there is nothing to read."
                ),
                started_at=started,
                policy=self.policy,
            )
            return outcome

        if not force and await self.journal.already_recorded(today, RUN_SCAN):
            outcome.record = SessionRecord(
                session_id=0,
                session_date=today,
                run_kind=RUN_SCAN,
                status=STATUS_SKIPPED,
                decisions=0,
                message=(
                    "Already scanned today. A second scan of the same session "
                    "would not merely repeat work -- it would buy a second set "
                    "of positions."
                ),
            )
            return outcome

        scan, blocked = await self.scan_now()
        outcome.candidates = len(scan.candidates)

        if scan.error is not None:
            outcome.record = await self.journal.record(
                portfolio_id=portfolio_id,
                session_date=today,
                run_kind=RUN_SCAN,
                status=STATUS_SKIPPED,
                message=scan.error,
                started_at=started,
                scan=scan,
                policy=self.policy,
            )
            return outcome

        decisions: List[Decision] = []
        picked: List[Candidate] = []

        if blocked is not None:
            # A blocked run still records the whole funnel and every candidate
            # it found. "The gate was off and these four names would otherwise
            # have been bought" is exactly the record that makes the gate's
            # cost measurable afterwards -- section 11 is that measurement.
            for candidate in scan.candidates:
                decisions.append(
                    self._decision_from(
                        candidate,
                        ACTION_NOT_ENTERED,
                        f"Not entered: {blocked}",
                    )
                )
        else:
            picked = scan.candidates[: self.parameters.slots]
            equity = await self._equity(portfolio_id)
            outcome.equity = equity

            if equity is None:
                # B12 sizes from total equity, and `BalanceService` withholds
                # equity when any open position has no mark. Buying nothing and
                # saying why is the honest response; sizing off cash instead
                # would be a different rule.
                for candidate in scan.candidates:
                    decisions.append(
                        self._decision_from(
                            candidate,
                            ACTION_SKIPPED,
                            "Not sized: the portfolio's equity could not be "
                            "computed, because an open position has no live "
                            "mark. B12 sizes from total equity.",
                        )
                    )
                picked = []
            else:
                allocation = self.parameters.position_value(equity)
                for candidate in scan.candidates:
                    if candidate not in picked:
                        decisions.append(
                            self._decision_from(
                                candidate,
                                ACTION_SKIPPED,
                                f"Skipped: rank {candidate.rank} of "
                                f"{len(scan.candidates)}, and only "
                                f"{self.parameters.slots} slot(s) are traded.",
                            )
                        )
                        continue
                    decisions.append(
                        await self._enter(
                            portfolio_id, candidate, allocation, scan, outcome
                        )
                    )

        decisions.extend(rejection_decisions(scan))

        outcome.picked = len(picked)
        outcome.record = await self.journal.record(
            portfolio_id=portfolio_id,
            session_date=today,
            run_kind=RUN_SCAN,
            status=STATUS_COMPLETED,
            message=self._scan_message(outcome, scan, blocked),
            started_at=started,
            scan=scan,
            policy=self.policy,
            decisions=decisions,
            slots=self.parameters.slots,
            picked=outcome.placed if outcome.armed else len(picked),
        )

        logger.info(
            "BTST scan %s: %s candidate(s), %s picked, %s placed (%s)",
            self.definition.key, outcome.candidates, outcome.picked,
            outcome.placed,
            "ARMED" if outcome.armed else "NOT ARMED -- nothing was placed",
        )
        return outcome

    async def scan_now(self) -> Tuple[ScanResult, Optional[str]]:
        """The scan as a read: no orders, no journal, no side effects.

        The Signals tab's whole payload comes from here, refreshed while the
        session runs. Returns the result and, separately, why entries are
        blocked -- separately because a blocked run still HAS candidates and
        the page has to show them.
        """
        universe = sorted(self.definition.universe.symbols) if self.definition.universe else []
        if not universe:
            result = ScanResult(session_date=ist_today())
            result.error = (
                "This strategy declares no universe, so there is nothing to "
                "scan."
            )
            return result, None

        segment = self.definition.exchange_segment
        newest = await self._newest_session(universe, segment)
        stale = staleness_reason(newest)
        if stale is not None:
            result = ScanResult(session_date=ist_today())
            result.regime_enforced = self.policy.enforce_regime
            result.fno_excluded = self.policy.exclude_fno
            result.error = stale
            return result, None

        windows = await self._windows(universe, segment)
        quotes = await self._quotes(universe, segment)
        regime = await self._regime()

        result = scan_service.evaluate(
            self.parameters,
            self.policy,
            quotes,
            windows,
            universe,
            session_date=ist_today(),
        )
        result.gate_on = regime["gate_on"]
        result.index_close = regime["index_close"]
        result.index_sma = regime["index_sma"]
        result.index_symbol = regime["index_symbol"]
        return result, result.blocked_reason

    # --- building the scan's inputs ----------------------------------------
    async def _newest_session(self, universe, segment) -> Optional[date]:
        """The newest stored bar across the universe.

        The max rather than the min: one delisted name whose last bar is months
        old must not make the whole universe look stale. The per-symbol
        `minimum_sessions` filter is what excludes a symbol whose own history
        is short, and `_windows` skips a symbol with no recent bar at all.
        """
        latest = await self.bars.latest_dates(segment, list(universe))
        return max(latest.values()) if latest else None

    async def _windows(self, universe, segment) -> Dict[str, Any]:
        """Each symbol's completed-bar indicators.

        One query per symbol, which is 289 round trips and takes a few seconds.
        That is affordable because it happens ONCE, at the scan, and the
        specification's own timing section measures the whole indicator
        computation at 1.44 s -- the critical path at 15:20 is the quote
        snapshot, and this application does not have one: the book is already
        subscribed.
        """
        keep = max(
            self.parameters.minimum_sessions,
            self.parameters.history_needed + 1,
        )
        windows: Dict[str, Any] = {}
        for symbol in universe:
            bars = await self.bars.history(symbol, segment, limit=keep)
            if len(bars) < self.parameters.minimum_sessions:
                continue
            window = scan_service.build_window(
                self.parameters,
                [float(one.high) for one in bars],
                [float(one.low) for one in bars],
                [float(one.close) for one in bars],
                [float(one.volume or 0) for one in bars],
                symbol,
                # The live path: the bars are COMPLETED sessions and today is
                # not among them.
                include_last_in_averages=False,
            )
            if window is not None:
                windows[symbol] = window
        return windows

    async def _quotes(self, universe, segment) -> Dict[str, Quote]:
        """What the live book says, right now. A READ, never an accumulation.

        Dhan's Quote/Full packet already carries the session's running high and
        low and its cumulative volume, so nothing here keeps state between
        ticks. Root `CLAUDE.md` forbids accumulating ticks into bars and this
        strategy does not need to.
        """
        rows = await self.instruments.list_by_symbols(list(universe), segment)
        quotes: Dict[str, Quote] = {}
        for instrument in rows:
            if not instrument.is_active:
                continue
            symbol = instrument.underlying_symbol
            # One EQUITY row per symbol is what the instrument set selects for
            # (SERIES EQ/BE, never D1), so the first active row wins and a
            # second is not a state to resolve here.
            if symbol in quotes:
                continue
            row = self.book.get(str(instrument.security_id)) or {}
            quotes[symbol] = Quote(
                security_id=str(instrument.security_id),
                symbol=symbol,
                price=row.get("ltp"),
                session_high=row.get("high"),
                session_low=row.get("low"),
                session_volume=row.get("volume"),
                fno_eligible=bool(instrument.fno_eligible),
            )
        return quotes

    async def _regime(self) -> Dict[str, Any]:
        """B9, computed whether or not it is enforced."""
        reference = self.definition.reference_instrument(
            self.parameters.regime.index_role
        )
        if reference is None:
            return {
                "gate_on": None, "index_close": None,
                "index_sma": None, "index_symbol": None,
            }
        bars = await self.bars.history(
            reference.symbol,
            reference.exchange_segment,
            limit=self.parameters.regime.sma_sessions + 5,
        )
        return scan_service.evaluate_regime(
            self.parameters,
            [float(one.close) for one in bars],
            index_symbol=reference.symbol,
        )

    async def _equity(self, portfolio_id: int) -> Optional[Decimal]:
        """Total equity, or None when `BalanceService` withheld it.

        Withheld -- not zeroed -- when any open position has no mark, which is
        the rule root `CLAUDE.md` states and the only place this strategy asks
        about money. B12 sizes from equity, so a withheld figure means buying
        nothing and saying why rather than falling back to cash, which would be
        a different rule.
        """
        balance = await self._balance_service().balance_for(int(portfolio_id))
        equity = getattr(balance, "equity", None)
        return Decimal(str(equity)) if equity is not None else None

    # --- entering -----------------------------------------------------------
    async def _enter(
        self,
        portfolio_id: int,
        candidate: Candidate,
        allocation: Decimal,
        scan: ScanResult,
        outcome: ScanOutcome,
    ) -> Decision:
        """Buy one name, or record why it was not bought."""
        quantity = int(allocation // Decimal(str(candidate.price)))
        reason = (
            f"Bought: rank {candidate.rank} of {len(scan.candidates)}, "
            f"{candidate.vol_ratio:.1f}x its 20-day volume, CLV "
            f"{candidate.clv:.2f}, {candidate.above_breakout * 100:.2f}% above "
            f"its {self.parameters.breakout_lookback_sessions}-day high, "
            f"six-month momentum {candidate.momentum * 100:+.1f}%. Sells at "
            f"the next open."
        )

        if quantity <= 0:
            return self._decision_from(
                candidate,
                ACTION_SKIPPED,
                f"Skipped: one share costs {candidate.price:,.2f} and the slot "
                f"allocation is {allocation:,.2f}, so no whole share fits. B12 "
                f"floors to whole shares and the position is not shrunk to "
                f"fit.",
            )

        # An already-held name is not bought again. It cannot normally happen
        # -- everything is sold at the open -- but it can if an exit FAILED
        # and the position is still open at the next afternoon's scan, and
        # doubling up on a name whose exit is already broken is the worst
        # possible response to that.
        existing = await self.holdings.get_open_for_security(
            self.definition.key, portfolio_id, candidate.security_id
        )
        if existing is not None:
            return self._decision_from(
                candidate,
                ACTION_SKIPPED,
                f"Skipped: still holding {existing.quantity} share(s) from "
                f"{existing.entry_session_date.isoformat()} whose exit is "
                f"{existing.exit_status}. This strategy never adds to a "
                f"position, and least of all to one whose exit has not worked.",
            )

        if not outcome.armed:
            return self._decision_from(
                candidate, ACTION_BOUGHT, reason, quantity=quantity
            )

        refusal = await self._execution_refusal(candidate.security_id)
        if refusal is not None:
            outcome.refused_outside_hours += 1
            outcome.notes.append(f"{candidate.symbol}: {refusal}")
            logger.warning(
                "BTST entry for %s not placed. %s", candidate.symbol, refusal
            )
            return self._decision_from(
                candidate,
                ACTION_SKIPPED,
                f"{refusal} ({reason})",
                quantity=quantity,
            )

        from src.orders.services.order_service import OrderValidationError

        try:
            order = await self._order_service().submit_paper_order(
                security_id=candidate.security_id,
                side=OrderSide.BUY.value,
                order_type=OrderType.MARKET.value,
                # NSE cash: LOT_SIZE is 1, so lots and shares are the same
                # number. Not routed through `quantity_override`, which would
                # leave order.lots disagreeing with order.quantity /
                # order.lot_size.
                lots=int(quantity),
                portfolio_id=portfolio_id,
                reason=reason,
                # The Nifty 500 is traded by two strategies, so an instrument
                # no longer decides which. Said explicitly rather than inferred.
                strategy_key=self.definition.key,
            )
        except OrderValidationError as error:
            outcome.notes.append(f"{candidate.symbol}: {error}")
            logger.warning("BTST entry for %s was refused: %s", candidate.symbol, error)
            return self._decision_from(
                candidate,
                ACTION_SKIPPED,
                f"Entry refused: {error} ({reason})",
                quantity=quantity,
            )

        filled = int(order.filled_quantity or 0)
        if filled <= 0:
            # A market order that filled nothing at all means an empty book.
            # No holding row is written: there is no position to exit, and a
            # PENDING row for a position that does not exist would raise the
            # missed-exit alarm every morning for ever.
            outcome.notes.append(
                f"{candidate.symbol}: the order filled nothing -- no depth."
            )
            return self._decision_from(
                candidate,
                ACTION_SKIPPED,
                f"Entry placed and filled NOTHING: there was no depth to trade "
                f"against. ({reason})",
                quantity=quantity,
                order_id=order.id,
            )

        outcome.placed += 1
        await self._open_holding(portfolio_id, candidate, order, filled, scan)
        return self._decision_from(
            candidate,
            ACTION_BOUGHT,
            reason if filled == quantity else (
                f"{reason} PARTIAL: {filled} of {quantity} share(s) filled."
            ),
            quantity=filled,
            order_id=order.id,
        )

    async def _open_holding(
        self,
        portfolio_id: int,
        candidate: Candidate,
        order,
        filled: int,
        scan: ScanResult,
    ) -> None:
        """Record what is now held overnight, and under which policy.

        The policy is stamped on the row rather than looked up at the exit, the
        same rule `swing_stops` follows: a position keeps the policy it was
        opened under. It changes nothing about the exit here -- everything is
        sold regardless -- and it is what lets a performance report separate
        trades taken while the gate was relaxed from the rest.
        """
        await self.holdings.create(
            strategy_key=self.definition.key,
            portfolio_id=int(portfolio_id),
            security_id=candidate.security_id,
            symbol=candidate.symbol,
            entry_session_date=ist_today(),
            entry_at=utc_now(),
            entry_order_id=order.id,
            entry_price=(
                Decimal(str(order.average_fill_price))
                if order.average_fill_price is not None
                else None
            ),
            quantity=int(filled),
            entry_gate_on=scan.gate_on,
            entry_regime_enforced=self.policy.enforce_regime,
            exit_status=EXIT_PENDING,
        )

    # =======================================================================
    # THE EXIT
    # =======================================================================
    async def run_exit(self, portfolio_id: int, force: bool = False) -> ExitOutcome:
        """Sell everything, unconditionally. THIS IS THE STRATEGY.

        No rank is consulted, no stop exists and there is no condition under
        which a position is kept. Section 10.1: the same positions held to the
        next close instead of the next open measure a 49.0% win rate against
        71.4%.

        It runs even when nothing is due, and records that, because "the exit
        ran and there was nothing to sell" and "the exit did not run" are the
        two facts the Health tab's first verdict has to tell apart.
        """
        started = utc_now()
        now = self._now()
        outcome = ExitOutcome()
        outcome.armed = self._is_armed()

        open_holdings = await self.holdings.open_for(
            self.definition.key, portfolio_id
        )
        outcome.due = len(open_holdings)

        if not open_holdings:
            outcome.record = await self.journal.record(
                portfolio_id=portfolio_id,
                session_date=now.date(),
                run_kind=RUN_EXIT,
                status=STATUS_COMPLETED,
                message=(
                    "Nothing was held overnight, so there was nothing to sell. "
                    "At roughly half a signal a session this is the ordinary "
                    "outcome, not a failure."
                ),
                started_at=started,
                policy=self.policy,
            )
            return outcome

        decisions: List[Decision] = []
        for holding in open_holdings:
            decisions.append(
                await self._exit_one(portfolio_id, holding, now, outcome)
            )

        outcome.still_open = outcome.due - outcome.sold - outcome.already_closed
        # The session date is the one the positions were OPENED in, so the two
        # halves of a trade share a date and can be read as a pair. The oldest
        # is used when a failed exit has left more than one session open --
        # which is itself the thing that should never happen.
        session_date = min(one.entry_session_date for one in open_holdings)
        outcome.record = await self.journal.record(
            portfolio_id=portfolio_id,
            session_date=session_date,
            run_kind=RUN_EXIT,
            status=STATUS_COMPLETED,
            message=self._exit_message(outcome),
            started_at=started,
            policy=self.policy,
            decisions=decisions,
        )

        if outcome.still_open:
            await self._raise_stuck_alert(outcome, open_holdings, now)

        logger.info(
            "BTST exit %s: %s due, %s sold (%s late), %s failed, %s still open",
            self.definition.key, outcome.due, outcome.sold, outcome.late,
            outcome.failed, outcome.still_open,
        )
        return outcome

    async def _exit_one(
        self, portfolio_id: int, holding, now: datetime, outcome: ExitOutcome
    ) -> Decision:
        """One exit, and the lateness arithmetic around it.

        **The quantity comes from the POSITION, not from this row.**
        `holding.quantity` is what filled at entry and never moves again, so a
        row that has already been partly sold -- or whose position was closed
        by hand after the alarm fired -- still says the original number. Selling
        that number again sells shares that are no longer there, and nothing
        downstream refuses it: a closing order skips the funds check by design,
        and the position book simply lets the net go negative. The result is a
        SHORT in a strategy that must never sell to open, with a wrong realised
        gap attached to it.

        So the sale is bounded by both figures: never more than is actually
        held, and never more than this row opened. The first bound is the bug;
        the second stops the exit reaching a position somebody else's buy put
        in the same portfolio. `SwingStopMonitor._open_quantity` is the same
        guard on the rotation's side and is where this pattern comes from.
        """
        late_by = self._minutes_late(holding, now)
        is_late = late_by is not None and late_by > self.schedule.exit_alarm_after_minutes
        recorded = int(holding.quantity)
        held = await self._open_quantity(holding)
        sellable = min(recorded, held)

        if not outcome.armed:
            shortfall = (
                ""
                if held >= recorded
                else (
                    f" Only {held} share(s) are actually held against the "
                    f"{recorded} this row opened, so {sellable} would be sold."
                )
            )
            return Decision(
                symbol=holding.symbol,
                security_id=holding.security_id,
                action=ACTION_HELD,
                quantity=sellable,
                reason=(
                    f"NOT ARMED: {sellable} share(s) from "
                    f"{holding.entry_session_date.isoformat()} would be sold "
                    f"here. Nothing was placed.{shortfall}"
                ),
            )

        if sellable <= 0:
            # The position went away by another route -- closed by hand, or
            # sold by an earlier pass whose result this row never saw. Nothing
            # to sell, and no order is placed. Recorded NOT_HELD rather than
            # left FAILED, because FAILED counts as open and would keep the
            # stuck-exit alarm firing for a position that is not there.
            await self._mark_not_held(holding, recorded)
            outcome.already_closed += 1
            outcome.notes.append(
                f"{holding.symbol}: nothing held, so nothing was sold."
            )
            logger.warning(
                "BTST exit for %s placed NO order: the row opened %s share(s) "
                "but the position holds %s. It was closed by something else. "
                "Recorded NOT_HELD.",
                holding.symbol, recorded, held,
            )
            return Decision(
                symbol=holding.symbol,
                security_id=holding.security_id,
                action=ACTION_SKIPPED,
                quantity=0,
                reason=(
                    f"No exit placed: this row opened {recorded} share(s) and "
                    f"the position holds {held}. It was closed by something "
                    f"else -- by hand, or by an earlier pass. Nothing was sold "
                    f"and nothing is held."
                ),
            )

        refusal = await self._execution_refusal(holding.security_id)
        if refusal is not None:
            await self._mark_failed(holding, refusal)
            outcome.failed += 1
            outcome.notes.append(f"{holding.symbol}: {refusal}")
            logger.error(
                "BTST EXIT NOT PLACED for %s. %s The position is STILL OPEN "
                "and the next pass will try again.",
                holding.symbol, refusal,
            )
            return Decision(
                symbol=holding.symbol,
                security_id=holding.security_id,
                action=ACTION_HELD,
                quantity=sellable,
                reason=f"STILL HELD -- exit not placed: {refusal}",
            )

        from src.orders.services.order_service import OrderValidationError

        try:
            order = await self._order_service().submit_paper_order(
                security_id=holding.security_id,
                side=OrderSide.SELL.value,
                order_type=OrderType.MARKET.value,
                # WHAT IS HELD, not what this row opened at entry. See the
                # docstring: the recorded figure never moves, so a retry after
                # a partial sale would sell shares that are no longer there and
                # open a short.
                lots=int(sellable),
                is_close_order=True,
                portfolio_id=portfolio_id,
                strategy_key=self.definition.key,
                reason=(
                    f"BTST exit: sold at the open, unconditionally. Held from "
                    f"{holding.entry_session_date.isoformat()}."
                ),
            )
        except OrderValidationError as error:
            await self._mark_failed(holding, str(error))
            outcome.failed += 1
            outcome.notes.append(f"{holding.symbol}: {error}")
            logger.error(
                "BTST EXIT REFUSED for %s: %s The position is STILL OPEN.",
                holding.symbol, error,
            )
            return Decision(
                symbol=holding.symbol,
                security_id=holding.security_id,
                action=ACTION_HELD,
                quantity=sellable,
                reason=f"STILL HELD -- exit refused: {error}",
            )

        filled = int(order.filled_quantity or 0)
        exit_price = (
            Decimal(str(order.average_fill_price))
            if order.average_fill_price is not None
            else None
        )
        gap = self._overnight_gap(holding.entry_price, exit_price)

        if filled < sellable:
            # A partial exit leaves a real position open. It is recorded
            # FAILED -- not DONE with a note -- because the row's whole job is
            # to answer "is anything still held", and a partially closed
            # position is.
            #
            # Compared against what was SENT rather than against
            # `holding.quantity`: on a retry those differ, and measuring the
            # fill against the original figure would call a complete sale
            # partial for ever.
            await self._mark_failed(
                holding,
                f"Only {filled} of {sellable} share(s) sold: the book ran out "
                f"of depth. The rest is still held.",
                order_id=order.id,
                exit_price=exit_price,
            )
            outcome.failed += 1
            return Decision(
                symbol=holding.symbol,
                security_id=holding.security_id,
                action=ACTION_HELD,
                quantity=filled,
                order_id=order.id,
                reason=(
                    f"PARTIAL exit: {filled} of {sellable} share(s) sold, the "
                    f"rest is still held."
                ),
            )

        status = EXIT_LATE if is_late else EXIT_DONE
        await self._mark_exited(
            holding, status, order.id, exit_price, late_by, gap
        )
        outcome.sold += 1
        if is_late:
            outcome.late += 1

        gap_text = f"{gap * 100:+.2f}%" if gap is not None else "not measurable"
        reason = (
            f"Sold at the open: {holding.quantity} share(s) held from "
            f"{holding.entry_session_date.isoformat()}, overnight gap "
            f"{gap_text}."
        )
        if is_late:
            reason = (
                f"SOLD LATE, {late_by} minute(s) after the configured exit "
                f"time of {self.schedule.exit_at}. {reason} The overnight gap "
                f"is this strategy's entire edge and it is given back during "
                f"the session -- see the specification's section 10.1."
            )
            logger.warning(
                "BTST exit for %s was %s minutes LATE. %s",
                holding.symbol, late_by, reason,
            )

        return Decision(
            symbol=holding.symbol,
            security_id=holding.security_id,
            action=ACTION_SOLD,
            quantity=filled,
            order_id=order.id,
            price=float(exit_price) if exit_price is not None else None,
            reason=reason,
        )

    def _due_at(self, holding) -> datetime:
        """When this position's sale was DUE: the next trading day's exit time.

        **The next TRADING day, not the next calendar day**, and the difference
        is a whole weekend. A position entered on a Friday is sold on the
        Monday, and calling that two days late would put a LATE status and an
        alert on every Friday signal this strategy ever takes -- which at about
        one signal every two sessions is a large share of them.

        NOT HOLIDAY-AWARE, and that is the same trade this application makes
        everywhere else: there is deliberately no holiday list, because the only
        calendar it has is the regime index's own bar dates and this is a
        synchronous call with no session. A position held across an exchange
        holiday therefore reads as one day late, which errs towards reporting
        something that is fine rather than hiding something that is not.
        """
        trading_days = set(self.definition.market_hours.trading_days) or {0, 1, 2, 3, 4}
        due_from = holding.entry_session_date + timedelta(days=1)
        for _ in range(7):
            if due_from.weekday() in trading_days:
                break
            due_from = due_from + timedelta(days=1)
        return datetime.combine(due_from, self.schedule.exit)

    def _minutes_late(self, holding, now: datetime) -> Optional[int]:
        """How far past the configured exit time this sale is.

        Measured against the session the sale was DUE -- not against today's
        exit time, which would report a holding stuck for three days as being a
        few minutes late each morning and make a badly stuck position look
        trivial.
        """
        due_at = self._due_at(holding)
        if now.tzinfo is not None:
            due_at = due_at.replace(tzinfo=now.tzinfo)
        minutes = int((now - due_at).total_seconds() // 60)
        return max(minutes, 0)

    @staticmethod
    def _overnight_gap(
        entry_price: Optional[Decimal], exit_price: Optional[Decimal]
    ) -> Optional[Decimal]:
        """(exit - entry) / entry, from the prices actually PAID.

        Not from the bars. The backtest's number is
        `open(T+1) / close(T) - 1` with a 0.05%-a-side slippage assumption;
        this is what this book actually got, crossing a simulated spread both
        ways. The difference between the two is the most valuable number this
        exercise produces (specification section 10.2), and computing it from
        bars would throw it away.
        """
        if entry_price is None or exit_price is None or entry_price == 0:
            return None
        return (exit_price - entry_price) / entry_price

    async def _mark_exited(
        self, holding, status, order_id, exit_price, late_by, gap
    ) -> None:
        holding.exit_status = status
        holding.exit_order_id = order_id
        holding.exit_price = exit_price
        holding.exited_at = utc_now()
        holding.exit_delay_minutes = late_by
        holding.overnight_gap = gap
        holding.exit_reason = None
        await self.session.flush()

    async def _mark_failed(
        self, holding, reason: str, order_id=None, exit_price=None
    ) -> None:
        """Still open, and recorded as such.

        FAILED rather than a status that reads as finished, because
        `open_for()` counts FAILED as open: the next pass tries again and the
        alarm keeps firing until somebody deals with it. A failure that marked
        the row done would leave a position held indefinitely with nothing
        saying so.
        """
        holding.exit_status = EXIT_FAILED
        holding.exit_reason = (reason or "")[:500]
        if order_id is not None:
            holding.exit_order_id = order_id
        if exit_price is not None:
            holding.exit_price = exit_price
        await self.session.flush()

    async def _mark_not_held(self, holding, recorded: int) -> None:
        """The position was gone before the exit reached it. Nothing was sold.

        No exit price and no overnight gap: this job did not sell the shares
        and has no price to claim. `performance()` reads only EXITED and
        EXITED_LATE rows with a gap, so the row cannot reach the edge
        statistics -- which is the point of giving it a status of its own
        rather than calling it EXITED with a note.
        """
        holding.exit_status = EXIT_NOT_HELD
        holding.exited_at = utc_now()
        holding.exit_reason = (
            f"Not held when the exit ran: this row opened {recorded} share(s) "
            f"and the position was already closed -- by hand, or by an earlier "
            f"pass. No order was placed and no exit price is recorded."
        )[:500]
        await self.session.flush()

    async def _open_quantity(self, holding) -> int:
        """How many shares are ACTUALLY held, from the position book.

        The same lookup `SwingStopMonitor._open_quantity` makes before its own
        exit, and for the same reason: the quantity a strategy wrote down at
        entry is not the quantity it still owns. Clamped at zero -- a negative
        net is a short this strategy cannot have opened, and selling more on
        top of it would be the bug this guard exists to stop.
        """
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        position = await PositionRepository(self.session).get_open_for_security(
            int(holding.portfolio_id), str(holding.security_id)
        )
        if position is None:
            return 0
        return max(int(position.net_quantity or 0), 0)

    async def _raise_stuck_alert(self, outcome: ExitOutcome, holdings, now) -> None:
        """A position still open after the exit ran is an ALERT, not a log line.

        Root `CLAUDE.md`: an alert is a ROW first and an HTTP call second, so
        this writes the fact and `alert-dispatcher` delivers it. `CONDITION`
        rather than `WINDOW` because this is a STATE -- a position is stuck or
        it is not -- and a condition alert collapses on last observation, so a
        holding that stays stuck for a day keeps one row alive instead of
        sending a message every pass. This codebase has already produced the
        flood twice; a hundred messages about a stuck position is how the one
        that mattered fails to arrive.
        """
        try:
            from src.connections.database.db_models.alert_model import (
                SEVERITY_CRITICAL,
            )
            from src.connections.services.alert_catalogue import (
                EVENT_BTST_EXIT_INCOMPLETE,
            )
            from src.connections.services.alert_service import AlertService

            symbols = ", ".join(sorted(one.symbol for one in holdings))
            # `record_health` rather than `raise_alert`: it is the CONDITION
            # path, keyed on the event name, which alerts on the transition and
            # collapses on last observation. A position is stuck or it is not
            # -- that is a state, and a state reported every pass as a fresh
            # event is the flood this codebase has already produced twice.
            await AlertService(self.session).record_health(
                event=EVENT_BTST_EXIT_INCOMPLETE,
                strategy_key=self.definition.key,
                severity=SEVERITY_CRITICAL,
                title=(
                    f"{self.definition.label}: {outcome.still_open} "
                    f"position(s) still open after the exit"
                ),
                body=(
                    f"The exit ran at {now.strftime('%H:%M')} IST and "
                    f"{outcome.still_open} of {outcome.due} position(s) are "
                    f"still held: {symbols}. THE OVERNIGHT GAP IS THIS "
                    f"STRATEGY'S ENTIRE EDGE and it is given back during the "
                    f"session -- the same positions held to the next close "
                    f"measure a 49.0% win rate against 71.4%. The next pass "
                    f"will try again and the sale will be recorded as LATE."
                ),
            )
        except Exception:  # noqa: BLE001 - an alert must never lose the exit
            logger.exception(
                "Could not raise the stuck-exit alert. %s position(s) are "
                "still held.",
                outcome.still_open,
            )

    # --- may an order be placed for THIS instrument, right now? -------------
    async def _execution_refusal(self, security_id: str) -> Optional[str]:
        """Why an order for this instrument cannot be placed now, or None.

        Asked per instrument and immediately before each order, the same rule
        the rotation follows: F&O eligibility moves the continuous close from
        15:30 to 15:15, and this strategy's scan is at 15:20 -- so with
        `fno.exclude` switched off, the answer genuinely differs name by name
        on the very run that matters.
        """
        now = self._now()
        instrument = await self.instruments.get_by_security_id(str(security_id))
        fno_eligible = bool(instrument is not None and instrument.fno_eligible)
        if market_clock.can_execute_continuously(
            self.definition, fno_eligible, now=now
        ):
            return None

        if market_clock.in_closing_auction(self.definition, fno_eligible, now=now):
            auction = self.definition.market_hours.closing_auction
            return (
                f"Not placed at {now.strftime('%H:%M')} IST: continuous cash "
                f"trading for this F&O-eligible name ended at "
                f"{auction.continuous_close.strftime('%H:%M')} and the Closing "
                f"Auction Session runs to "
                f"{auction.auction_close.strftime('%H:%M')}. An order sent now "
                f"would land in an auction this simulator does not model. This "
                f"is exactly what the 'Exclude F&O-eligible names' policy "
                f"exists to avoid."
            )
        return (
            f"Not placed at {now.strftime('%H:%M')} IST: outside continuous "
            f"trading ({self.definition.market_hours.open.strftime('%H:%M')}-"
            f"{self.definition.market_hours.close.strftime('%H:%M')} IST, "
            f"Mon-Fri). Filling against the last session's depth book would be "
            f"a price nobody could have traded at."
        )

    # --- warming ------------------------------------------------------------
    async def warm_book(self, portfolio_id: Optional[int] = None) -> Dict[str, Any]:
        """Subscribe what the exit is about to sell, ahead of the open.

        The same arrangement the rotation's rebalance uses and for the same
        reason: the fill simulator needs a depth book for a name BEFORE the
        order rather than after it. A handful of names, never the universe --
        the universe reaches the feed through the subscription WINDOW, which is
        an afternoon thing.
        """
        from src.market.services.feed_manager import get_feed_manager

        holdings = await self.holdings.open_for(self.definition.key, portfolio_id)
        security_ids = {str(one.security_id) for one in holdings}
        manager = get_feed_manager()
        manager.pin_instruments(self.definition.key, security_ids)
        if security_ids:
            await manager.resync()
        return {
            "pinnedCount": len(security_ids),
            "symbols": sorted(one.symbol for one in holdings),
        }

    async def release_book(self) -> None:
        from src.market.services.feed_manager import get_feed_manager

        manager = get_feed_manager()
        manager.pin_instruments(self.definition.key, set())
        await manager.resync()

    # --- messages -----------------------------------------------------------
    def _decision_from(
        self,
        candidate: Candidate,
        action: str,
        reason: str,
        quantity: Optional[int] = None,
        order_id: Optional[int] = None,
    ) -> Decision:
        return Decision(
            symbol=candidate.symbol,
            security_id=candidate.security_id,
            action=action,
            rank=candidate.rank,
            price=candidate.price,
            session_high=candidate.session_high,
            session_low=candidate.session_low,
            session_volume=candidate.session_volume,
            vol_ratio=candidate.vol_ratio,
            clv=candidate.clv,
            breakout_high=candidate.breakout_high,
            momentum=candidate.momentum,
            sma=candidate.sma,
            turnover=candidate.turnover,
            quantity=quantity,
            order_id=order_id,
            fno_eligible=candidate.fno_eligible,
            reason=reason,
        )

    def _scan_message(
        self, outcome: ScanOutcome, scan: ScanResult, blocked: Optional[str]
    ) -> str:
        if blocked is not None:
            return (
                f"No entries: {blocked}. {outcome.candidates} name(s) would "
                f"otherwise have qualified; each is recorded so the gate's cost "
                f"stays measurable."
            )[:500]
        if outcome.candidates == 0:
            return (
                f"Nothing qualified. {scan.counts.get('quoted', 0)} name(s) "
                f"were quoted and measured; "
                f"{scan.counts.get('breakout', 0)} were above their "
                f"{self.parameters.breakout_lookback_sessions}-day high, "
                f"{scan.counts.get('volume', 0)} of those on "
                f"{self.parameters.volume_multiple:g}x volume. At roughly half "
                f"a signal a session this is the ordinary outcome."
            )[:500]
        if not outcome.armed:
            return (
                f"NOT ARMED: {outcome.candidates} candidate(s), "
                f"{outcome.picked} within the {self.parameters.slots} slot(s), "
                f"and no order was placed. Arming is a separate switch from "
                f"enabling, on the Strategies & Features page."
            )[:500]
        head = (
            f"Scanned: {outcome.candidates} candidate(s), {outcome.placed} of "
            f"{outcome.picked} bought. Every one sells at the next open."
        )
        if outcome.refused_outside_hours:
            head = (
                f"{head} {outcome.refused_outside_hours} order(s) were NOT "
                f"placed because the market was not in continuous trading."
            )
        return head[:500]

    def _exit_message(self, outcome: ExitOutcome) -> str:
        if not outcome.armed:
            return (
                f"NOT ARMED: {outcome.due} position(s) would have been sold "
                f"and nothing was placed."
            )[:500]
        head = f"Exited: {outcome.sold} of {outcome.due} position(s) sold."
        if outcome.already_closed:
            head = (
                f"{head} {outcome.already_closed} was/were NOT HELD when the "
                f"exit reached them -- closed by hand, or by an earlier pass -- "
                f"so no order was placed for them."
            )
        if outcome.late:
            head = (
                f"{head} {outcome.late} of them LATE -- the overnight gap is "
                f"this strategy's entire edge and holding past the open gives "
                f"it back."
            )
        if outcome.still_open:
            head = (
                f"{head} {outcome.still_open} POSITION(S) ARE STILL OPEN. The "
                f"next pass will try again."
            )
        return head[:500]
