"""The chandelier trailing stop: set it, raise it, and never lower it.

P14 places a stop at `entry - 3.5 x ATR14` on the session of entry. P15 raises
it on every daily close to
`max(stop, highest_close_since_entry - 3.5 x ATR14_today)`.

**The ratchet is one-directional and that is the whole point.** ATR rises after
a violent day, so `highest_close - 3.5 x ATR_today` can be LOWER than
yesterday's stop; taking the maximum is what stops the exit sliding away from a
position that has just become more dangerous. `ratchet_one` takes that maximum
in one place, and `test_a_trailing_stop_never_ratchets_down` pins it.

**Do not tighten it.** Specification section 10 measures every tighter variant
as worse -- an 8% initial cap takes CAGR from 19.9% to 15.7%, a breakeven
ratchet to 14.4%, a 2.5x trail to 15.3%, all three together to 10.6%. Momentum
names pull back 6-10% inside intact trends, and a tight stop converts those
shakeouts into realised losses plus re-entry churn while ejecting the positions
that produce the fat right tail. The 3.5x number is not a knob.

Money is `Decimal`; ATR and closes arrive as float from the indicators and are
converted here, at the boundary, deliberately.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.swing.database.db_models.swing_session_model import ACTION_STOP_MOVED
from src.swing.database.db_models.swing_stop_model import (
    STOP_ACTIVE,
    STOP_CLOSED,
    STOP_TRIGGERED,
    SwingStop,
)
from src.swing.database.db_operations.swing_stop_repository import SwingStopRepository
from src.swing.services.journal_service import Decision
from src.swing.services.ranking_service import RankingService
from src.swing.services.swing_parameters import SwingParameters

logger = get_logger("swing.stops")

PRICE = Decimal("0.0001")
MONEY = Decimal("0.01")


def _price(value) -> Decimal:
    """Prices carry four decimals here, as everywhere else in this codebase."""
    return Decimal(str(value)).quantize(PRICE, rounding=ROUND_HALF_UP)


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


@dataclass
class RatchetResult:
    moved: List[Decision] = field(default_factory=list)
    unchanged: int = 0
    skipped: List[str] = field(default_factory=list)

    @property
    def moved_count(self) -> int:
        return len(self.moved)


class StopService:
    """Owns every `swing_stops` row and the arithmetic that moves one."""

    def __init__(
        self,
        stops: SwingStopRepository,
        ranking: RankingService,
        parameters: SwingParameters,
        strategy_key: str,
    ):
        self.stops = stops
        self.ranking = ranking
        self.parameters = parameters
        self.strategy_key = strategy_key

    # --- P14: the stop at entry -------------------------------------------
    async def open_for_entry(
        self,
        portfolio_id: int,
        symbol: str,
        security_id: str,
        quantity: int,
        entry_price: Decimal,
        entry_session: date,
        entry_atr: Optional[float],
        entry_order_id: Optional[int] = None,
    ) -> Optional[SwingStop]:
        """P14. Returns None, loudly, when the ATR is not available.

        A position with no stop is not the same as a position with a stop that
        happens to be far away, so nothing is invented here: the caller records
        that the entry has no stop yet and the next nightly ratchet sets one as
        soon as the ATR is computable. Substituting a percentage of the entry
        price would be a different rule wearing this one's name.
        """
        if entry_atr is None or float(entry_atr) <= 0:
            logger.warning(
                "No usable ATR14 for %s at entry, so no chandelier stop was set. "
                "The position is unprotected until the next nightly ratchet.",
                symbol,
            )
            return None

        multiple = Decimal(str(self.parameters.trail_atr_multiple))
        atr = _price(entry_atr)
        entry_price = _price(entry_price)
        stop_price = _price(entry_price - multiple * atr)

        record = await self.stops.create(
            strategy_key=self.strategy_key,
            portfolio_id=int(portfolio_id),
            symbol=symbol,
            security_id=str(security_id),
            entry_order_id=entry_order_id,
            entry_session=entry_session,
            entry_price=entry_price,
            quantity=int(quantity),
            entry_atr=atr,
            atr_multiple=multiple,
            # The entry price seeds the high-water mark. The first daily close
            # replaces it if it is higher; using the close of the entry session
            # instead would need a bar that does not exist yet at 09:16.
            highest_close=entry_price,
            stop_price=stop_price,
            last_atr=atr,
            last_ratcheted_session=None,
            status=STOP_ACTIVE,
        )
        logger.info(
            "Chandelier stop for %s set at %s (entry %s - %s x ATR14 %s), "
            "quantity %s",
            symbol, stop_price, entry_price, multiple, atr, quantity,
        )
        return record

    # --- P15: the nightly ratchet -----------------------------------------
    async def ratchet_all(
        self, session_date: date, segment: str
    ) -> RatchetResult:
        """Raise every active stop on the session's close. Idempotent.

        `last_ratcheted_session` is what makes a restart at 18:20 leave 18:15's
        work alone. The journal entries returned are written by the caller, in
        the same session record as everything else that run decided.
        """
        result = RatchetResult()
        active = await self.stops.list_active(strategy_key=self.strategy_key)
        for stop in active:
            if stop.last_ratcheted_session == session_date:
                result.unchanged += 1
                continue
            decision = await self.ratchet_one(stop, session_date, segment)
            if decision is None:
                result.unchanged += 1
            elif decision.action == ACTION_STOP_MOVED:
                result.moved.append(decision)
            else:
                result.skipped.append(stop.symbol)
                result.moved.append(decision)
        return result

    async def ratchet_one(
        self, stop: SwingStop, session_date: date, segment: str
    ) -> Optional[Decision]:
        """One holding's ratchet. Returns a journal entry when anything changed.

        The maximum is taken twice, deliberately:

          * `highest_close` against today's close -- a high-water mark; and
          * `stop_price` against `highest_close - k x ATR_today` -- because a
            widening ATR would otherwise LOWER the stop on exactly the day the
            position became more dangerous.
        """
        view = await self.ranking.symbol_view(
            stop.symbol, segment, as_of=session_date
        )
        if view is None or view.bar_date != session_date:
            # No bar for this session: the name did not trade. Never
            # forward-fill -- a stop recomputed off a stale close is a stop
            # moved on information that does not exist.
            logger.info(
                "No %s bar for %s on %s; its stop stays at %s.",
                segment, stop.symbol, session_date.isoformat(), stop.stop_price,
            )
            return None

        previous_stop = Decimal(str(stop.stop_price))
        previous_high = Decimal(str(stop.highest_close))
        close = _price(view.close)
        atr = _price(view.atr14) if view.atr14 else None

        new_high = max(previous_high, close)
        candidate = (
            _price(new_high - Decimal(str(stop.atr_multiple)) * atr)
            if atr is not None
            else previous_stop
        )
        # THE RATCHET. Never down -- see the module docstring.
        new_stop = max(previous_stop, candidate)

        stop.highest_close = new_high
        stop.stop_price = new_stop
        if atr is not None:
            stop.last_atr = atr
        stop.last_ratcheted_session = session_date
        await self.stops.session.flush()

        wanted_lower = candidate < previous_stop
        if new_stop == previous_stop and not wanted_lower:
            # Nothing moved and nothing was refused: not a decision, so not a
            # row. A high-water mark that rose without moving the stop is
            # already visible in the stop record itself.
            return None

        distance = close - new_stop
        percent = (distance / close * 100) if close else Decimal("0")
        if new_stop > previous_stop:
            reason = (
                f"Stop raised from {previous_stop} to {new_stop} "
                f"(highest close since entry {new_high} - "
                f"{stop.atr_multiple} x ATR14 {atr}). Close {close} is "
                f"{_money(distance)} above it ({percent:.1f}%)."
            )
        else:
            reason = (
                f"Stop held at {new_stop}: {new_high} - {stop.atr_multiple} x "
                f"ATR14 {atr} would be {candidate}, which is lower. A "
                f"chandelier stop never ratchets down."
            )
        return Decision(
            symbol=stop.symbol,
            action=ACTION_STOP_MOVED,
            quantity=int(stop.quantity),
            reference_price=new_stop,
            reason=reason,
        )

    # --- firing ------------------------------------------------------------
    @staticmethod
    def is_hit(stop: SwingStop, price: Optional[Decimal]) -> bool:
        """Intraday trigger. Never acts on a `None` price.

        `<=`, not `<`: the backtest's `row.low <= stop` is inclusive, and a
        stop that survives being touched exactly is a stop that did not do its
        job on the one tick that mattered.
        """
        if price is None:
            return False
        return Decimal(str(price)) <= Decimal(str(stop.stop_price))

    async def mark_triggered(
        self, stop: SwingStop, price: Decimal, note: Optional[str] = None
    ) -> SwingStop:
        """Record that the level was crossed, before any order exists.

        Separate from placing the exit because the two genuinely come apart:
        inside the Closing Auction Session an F&O-eligible name's stop can fire
        with no way to exit continuously, and the record of the trigger must
        not wait for a fill that cannot happen until tomorrow.
        """
        from src.swing.database.db_models.swing_stop_model import EXIT_TRAIL

        stop.status = STOP_TRIGGERED
        stop.exit_kind = EXIT_TRAIL
        stop.triggered_at = utc_now()
        stop.trigger_price = _price(price)
        if note:
            stop.note = note[:500]
        await self.stops.session.flush()
        logger.warning(
            "Chandelier stop HIT for %s: %s crossed %s%s",
            stop.symbol, _price(price), stop.stop_price,
            f" -- {note}" if note else "",
        )
        return stop

    async def attach_exit_order(self, stop: SwingStop, order_id: int) -> SwingStop:
        stop.exit_order_id = int(order_id)
        stop.closed_at = utc_now()
        await self.stops.session.flush()
        return stop

    async def close_for_exit(
        self,
        stop: SwingStop,
        exit_kind: str,
        order_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> SwingStop:
        """The position left for a reason that is not the stop.

        A rotation exit, a regime exit or an operator closing by hand. The row
        stays as the record that a stop existed and did NOT fire, which is what
        the exit mix in the performance report counts.
        """
        stop.status = STOP_CLOSED
        stop.exit_kind = exit_kind
        stop.exit_order_id = int(order_id) if order_id else None
        stop.closed_at = utc_now()
        if note:
            stop.note = note[:500]
        await self.stops.session.flush()
        return stop

    # --- reporting ---------------------------------------------------------
    @staticmethod
    def describe(stop: SwingStop, mark: Optional[Decimal]) -> Dict[str, object]:
        """One stop as the UI reads it.

        `distance` is None when there is no mark. Rendering it as zero would
        put a position on the page as though it were sitting exactly on its
        stop, which is the most alarming possible way to say "no price".
        """
        stop_price = Decimal(str(stop.stop_price))
        distance = None
        distance_percent = None
        if mark is not None:
            mark = Decimal(str(mark))
            distance = _money(mark - stop_price)
            if mark > 0:
                distance_percent = float(
                    (distance / mark * 100).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
        return {
            "id": stop.id,
            "symbol": stop.symbol,
            "securityId": stop.security_id,
            "status": stop.status,
            "quantity": int(stop.quantity),
            "entrySession": stop.entry_session.isoformat(),
            "entryPrice": str(stop.entry_price),
            "entryAtr": str(stop.entry_atr),
            "atrMultiple": str(stop.atr_multiple),
            "highestClose": str(stop.highest_close),
            "stopPrice": str(stop_price),
            "lastAtr": str(stop.last_atr) if stop.last_atr is not None else None,
            "lastRatchetedSession": (
                stop.last_ratcheted_session.isoformat()
                if stop.last_ratcheted_session
                else None
            ),
            "mark": str(mark) if mark is not None else None,
            "distanceToStop": str(distance) if distance is not None else None,
            "distancePercent": distance_percent,
            "exitKind": stop.exit_kind,
            "triggeredAt": (
                stop.triggered_at.isoformat() if stop.triggered_at else None
            ),
            "triggerPrice": (
                str(stop.trigger_price) if stop.trigger_price is not None else None
            ),
            "exitOrderId": stop.exit_order_id,
            "note": stop.note,
        }
