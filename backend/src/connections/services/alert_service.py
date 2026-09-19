"""Writing alerts into the outbox, and deciding which ones not to write.

Everything that knows a fact calls this; nothing that knows a fact calls
Telegram. The delivery half is `alert_dispatcher`.

**Rate limiting and de-duplication are not optional, they are the feature.**
This codebase has produced exactly the flood this would have forwarded, twice
in one evening: the swing stop monitor logged "database is locked" once a
second for twelve minutes, and the nightly re-ran every fifteen minutes until
it was fixed. Unthrottled, either would have sent hundreds of messages and hit
Telegram's flood control -- and flood control is not merely noisy, it stops the
ONE message that mattered from arriving.

So: the first occurrence goes out immediately; the rest collapse onto the row
already holding the window open, which counts them. When the floor elapses the
next occurrence goes out saying how many there have been. The key is the logger
name plus a NORMALISED message -- normalised because a message carrying its own
counter ("this has now happened 47 times") would otherwise produce a new key
every time and defeat the de-duplication it is reporting.

**The alert path must never alert about its own failure.** A Telegram delivery
error that logs an ERROR that raises an alert that fails to deliver is a loop
that ends in flood control. `ALERTING_LOGGER_PREFIX` is excluded from the log
sink explicitly, and `tests/test_alerts.py` asserts it.
"""
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src import config_utils
# Normalisation lives in `alert_sink` because THAT module is import-light
# enough to be loaded during logging bootstrap. Importing it from here rather
# than duplicating it is what keeps the sink and the writer keyed identically.
from src.alert_sink import normalise_for_dedupe
from src.connections.database.db_models.alert_model import (
    ALERT_PENDING,
    ALERT_SUPPRESSED,
    KIND_COMMAND,
    KIND_ERROR,
    KIND_HEALTH,
    KIND_TRADE_BOUGHT,
    KIND_TRADE_SOLD,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    Alert,
)
from src.connections.database.db_operations.alert_repository import AlertRepository
from src.connections.services import providers
from src.connections.services.connection_store import ConnectionStore
from src.core.time_utils import to_ist, utc_now
from src.logging_config import get_logger

logger = get_logger("connections.alerts")

DEFAULT_DEDUPE_SECONDS = 300
DEFAULT_MAX_BODY_CHARS = 3500  # Telegram's own limit is 4096 per message.

# --- how repeats are collapsed, and why there are two answers --------------
#
# An EVENT that keeps happening and a CONDITION that is simply true need
# opposite treatment, and treating them the same is what put ten missed
# sessions on somebody's phone every five minutes for a day.
#
# WINDOW: a floor BETWEEN MESSAGES. The first goes immediately, then one per
# window carrying the running count. Right for an error flood -- it keeps
# happening, and "still happening, now 4,000 times" is news.
DEDUPE_WINDOW = "WINDOW"
# CONDITION: alert on the TRANSITION, not on the state. One message when it
# appears, silence while it persists, and a new message only once it has
# stopped being observed and comes back. Right for a dead task or a stale
# feed, where the second message says exactly what the first one did.
DEDUPE_CONDITION = "CONDITION"

# How long a condition must go UNOBSERVED before its return counts as new.
# Comfortably more than the watcher's 60 s pass, so an ordinary pass never
# re-arms it, and short enough that a real recurrence is reported promptly.
DEFAULT_CONDITION_REARM_SECONDS = 900

# A standing condition is re-sent at most this often, so a critical problem
# nobody acted on is not silent for ever. Once a day is a reminder; once every
# five minutes is what this replaced.
DEFAULT_CONDITION_REMINDER_HOURS = 24


def _dedupe_seconds() -> int:
    return config_utils.get_property_value_int(
        "connections.alert_dedupe_seconds", DEFAULT_DEDUPE_SECONDS
    )


def _condition_rearm_seconds() -> int:
    return config_utils.get_property_value_int(
        "connections.alert_condition_rearm_seconds",
        DEFAULT_CONDITION_REARM_SECONDS,
    )


def _condition_reminder_hours() -> int:
    return config_utils.get_property_value_int(
        "connections.alert_condition_reminder_hours",
        DEFAULT_CONDITION_REMINDER_HOURS,
    )


def _money(value: Optional[Decimal]) -> str:
    if value is None:
        return "not measured"
    return f"₹{Decimal(str(value)):,.2f}"


def _ist(moment) -> str:
    if moment is None:
        return "an unknown time"
    return to_ist(moment).strftime("%H:%M IST, %d %b %Y")


class AlertService:
    """Writes outbox rows. Never sends anything."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.alerts = AlertRepository(session)
        self.store = ConnectionStore(session)

    # --- the one write -----------------------------------------------------
    async def raise_alert(
        self,
        kind: str,
        severity: str,
        title: str,
        body: str,
        dedupe_key: Optional[str] = None,
        strategy_key: Optional[str] = None,
        dedupe_mode: str = DEDUPE_WINDOW,
    ) -> Optional[Alert]:
        """Record something worth telling somebody about.

        Returns the row, or None when this occurrence was collapsed onto an
        existing one. Never raises: a caller in the middle of a fill must not
        lose the fill because an alert could not be written.

        `dedupe_mode` decides what a repeat MEANS -- see DEDUPE_WINDOW and
        DEDUPE_CONDITION above.
        """
        try:
            if dedupe_key:
                existing = await self._open_for(dedupe_key, dedupe_mode)
                if existing is not None:
                    existing.suppressed_count = int(existing.suppressed_count or 0) + 1
                    await self.session.flush()
                    logger.debug(
                        "Collapsed a repeat alert onto row %s (%s further "
                        "occurrence(s))",
                        existing.id, existing.suppressed_count,
                    )
                    return None

            connection = await self.store.get(providers.PROVIDER_TELEGRAM)
            status = ALERT_PENDING
            reason: Optional[str] = None
            if connection is None:
                status = ALERT_SUPPRESSED
                reason = "No Telegram connection is configured, so nothing carries this."
            elif not connection.enabled:
                status = ALERT_SUPPRESSED
                reason = "The Telegram connection is switched off."

            row = await self.alerts.add(
                kind=kind,
                severity=severity,
                title=title,
                body=body[:DEFAULT_MAX_BODY_CHARS],
                status=status,
                connection_id=connection.id if connection is not None else None,
                dedupe_key=dedupe_key,
                last_error=reason,
                strategy_key=strategy_key,
            )
            return row
        except Exception:  # noqa: BLE001 - an alert must never break its caller
            logger.warning(
                "Could not record an alert (%s / %s); the underlying event is "
                "unaffected", kind, title, exc_info=True,
            )
            return None

    async def _open_for(self, dedupe_key: str, mode: str) -> Optional[Alert]:
        """The row this occurrence should collapse onto, if any.

        WINDOW looks at when the last message was SENT, so a continuing flood
        gets a fresh one each window with the running count.

        CONDITION looks at when the condition was last OBSERVED, so a watcher
        reporting the same thing every 60 s keeps one row alive and sends
        nothing further -- until either the condition stops being observed for
        the re-arm window (its return is news) or it has been standing for the
        reminder interval (a critical problem nobody acted on should not go
        silent for ever).
        """
        now = utc_now()
        if mode == DEDUPE_CONDITION:
            observed = await self.alerts.latest_observed_for_dedupe(
                dedupe_key, now - timedelta(seconds=_condition_rearm_seconds())
            )
            if observed is None:
                return None
            standing_since = observed.created_at
            if standing_since is not None and standing_since <= now - timedelta(
                hours=_condition_reminder_hours()
            ):
                # Still true a day later. Say so once, then go quiet again.
                return None
            return observed

        return await self.alerts.latest_open_for_dedupe(
            dedupe_key, now - timedelta(seconds=_dedupe_seconds())
        )

    # --- trades ------------------------------------------------------------
    async def record_fill(
        self,
        *,
        strategy_key: str,
        portfolio_id: int,
        security_id: str,
        trading_symbol: str,
        side: str,
        quantity: int,
        price: Decimal,
        realized: Optional[Decimal],
        charges: Optional[Decimal],
        position,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Alert]:
        """A share changed hands. Raised from `PositionService.apply_fill`.

        That is deliberately the hook: chart trading, MCX crude and the
        rotation all converge on `apply_fill`, so one emit covers every way a
        position moves, and a way added later inherits the alert rather than
        having to remember it.

        **P&L comes from the position row, never from a third calculation.**
        `realized` is the figure `apply_fill` just posted onto
        `position.realized_pnl`, and `reports/services/pnl_service.py` replays
        fills to the same number by construction. A third calculation here
        would be a third thing that can disagree, and a message on somebody's
        phone is the worst place to find that out.
        """
        context = context or {}
        buying = str(side).upper() == "BUY"
        portfolio_name = await self._portfolio_name(portfolio_id)
        value = (Decimal(str(price)) * Decimal(int(quantity))).quantize(Decimal("0.01"))

        lines = [
            f"{trading_symbol}",
            f"{'Bought' if buying else 'Sold'} {quantity} @ {Decimal(str(price)):,.2f}",
            f"Value: {_money(value)}",
            f"Strategy: {strategy_key}",
            f"Portfolio: {portfolio_name}",
        ]

        if buying:
            reason = context.get("reason")
            if reason:
                # For the rotation this is its rank and score, already written
                # onto the PLACED event by submit_paper_order(reason=...).
                lines.append(f"Why: {reason}")
            cash = await self._cash_remaining(portfolio_id)
            lines.append(f"Cash remaining: {cash}")
            return await self.raise_alert(
                kind=KIND_TRADE_BOUGHT,
                severity=SEVERITY_INFO,
                title=f"Bought {trading_symbol}",
                body="\n".join(lines),
                strategy_key=strategy_key,
            )

        if realized is not None:
            # The entry cost is DERIVED from the two figures already in hand --
            # realised = (exit - entry) x quantity, so entry x quantity is
            # `value - realised`. Reading `position.average_price` would give
            # zero on the fill that closed the position, because closing a row
            # resets it.
            cost = value - Decimal(str(realized))
            percent = (
                (Decimal(str(realized)) / cost * 100) if cost else None
            )
            lines.append(
                "Realised: "
                + _money(realized)
                + (f" ({percent:+.2f}%)" if percent is not None else "")
            )
        else:
            lines.append("Realised: not measured for this fill")

        if charges is not None:
            lines.append(f"Charges: {_money(charges)}")

        held = self._held_for(position)
        if held:
            lines.append(f"Held: {held}")

        exit_kind = await self._exit_kind(strategy_key, portfolio_id, security_id)
        lines.append(f"Why it left: {exit_kind}")

        cash = await self._cash_remaining(portfolio_id)
        lines.append(f"Cash remaining: {cash}")

        return await self.raise_alert(
            kind=KIND_TRADE_SOLD,
            severity=SEVERITY_INFO,
            title=f"Sold {trading_symbol}",
            body="\n".join(lines),
            strategy_key=strategy_key,
        )

    # --- errors ------------------------------------------------------------
    async def record_error(
        self, logger_name: str, level: str, message: str
    ) -> Optional[Alert]:
        """An ERROR-level log record. WARNING is too chatty to put on a phone."""
        return await self.raise_alert(
            kind=KIND_ERROR,
            severity=SEVERITY_ERROR if level == "ERROR" else SEVERITY_WARNING,
            title=f"{level} in {logger_name}",
            body=message,
            dedupe_key=normalise_for_dedupe(logger_name, message),
        )

    # --- system health -----------------------------------------------------
    async def record_health(
        self,
        event: str,
        title: str,
        body: str,
        severity: str = SEVERITY_WARNING,
        strategy_key: Optional[str] = None,
    ) -> Optional[Alert]:
        """One of the watched conditions. Keyed on the EVENT, not the text.

        **A CONDITION, not an event.** A dead task stays dead and ten missed
        sessions stay missed, so this alerts on the TRANSITION: once when the
        condition appears, silence while it persists, and again only when it
        has cleared and come back -- or once a day, so a critical problem
        nobody acted on does not go silent for ever.

        Keying on the event's name is what makes the repeat recognisable at
        all; treating it as a floor between messages rather than as a state is
        what sent ten missed sessions to somebody's phone every five minutes
        for a day.
        """
        return await self.raise_alert(
            kind=KIND_HEALTH,
            severity=severity,
            title=title,
            body=body,
            dedupe_key=f"health|{event}",
            strategy_key=strategy_key,
            dedupe_mode=DEDUPE_CONDITION,
        )

    # --- commands ----------------------------------------------------------
    async def record_command(
        self,
        command: str,
        telegram_user_id: int,
        user_id: Optional[int],
        outcome: str,
        allowed: bool,
    ) -> Optional[Alert]:
        """Every state-changing command, journalled with its sender.

        An action with no record is the thing this codebase most consistently
        refuses. The thing the command CHANGED carries `updated_by_user_id`
        exactly as a UI action does -- that is the payoff of resolving the
        sender to an application user rather than keeping a second allowlist --
        and this row is the record that the instruction arrived and what came
        of it.
        """
        body = "\n".join(
            [
                f"Command: {command}",
                f"Telegram user: {telegram_user_id}",
                f"Application user: {user_id if user_id is not None else 'not mapped'}",
                f"Authorised: {'yes' if allowed else 'no'}",
                f"Outcome: {outcome}",
                f"At: {_ist(utc_now())}",
            ]
        )
        return await self.raise_alert(
            kind=KIND_COMMAND,
            severity=SEVERITY_INFO if allowed else SEVERITY_WARNING,
            title=f"Telegram command {command}",
            body=body,
        )

    # --- enrichment --------------------------------------------------------
    async def _portfolio_name(self, portfolio_id: int) -> str:
        try:
            from src.portfolios.database.db_operations.portfolio_repository import (
                PortfolioRepository,
            )

            portfolio = await PortfolioRepository(self.session).get_by_id(portfolio_id)
            return portfolio.name if portfolio is not None else f"#{portfolio_id}"
        except Exception:  # noqa: BLE001
            return f"#{portfolio_id}"

    async def _cash_remaining(self, portfolio_id: int) -> str:
        """`BalanceService` is the only place cash is computed (root CLAUDE.md)."""
        try:
            from src.portfolios.services.balance_service import BalanceService

            available = await BalanceService(self.session).available_balance(
                portfolio_id
            )
            return _money(Decimal(str(available)))
        except Exception:  # noqa: BLE001
            return "not measured"

    @staticmethod
    def _held_for(position) -> Optional[str]:
        opened = getattr(position, "opened_at", None)
        if opened is None:
            return None
        delta = utc_now() - opened
        days = delta.days
        hours = delta.seconds // 3600
        if days:
            return f"{days}d {hours}h"
        minutes = (delta.seconds % 3600) // 60
        return f"{hours}h {minutes}m"

    async def _exit_kind(
        self, strategy_key: str, portfolio_id: int, security_id: str
    ) -> str:
        """Why the position left, as a STORED fact rather than a guess.

        `swing_stops.exit_kind` is TRAIL_STOP / ROTATION / REGIME / MANUAL. A
        sale with no exit-kind row is a manual close and says so, rather than
        being assigned a category it was never given.
        """
        try:
            from src.swing.database.db_operations.swing_stop_repository import (
                SwingStopRepository,
            )
            from src.swing.services.swing_service import EXIT_LABELS

            stop = await SwingStopRepository(self.session).latest_for_security(
                portfolio_id, security_id
            )
            if stop is not None and stop.exit_kind:
                return EXIT_LABELS.get(stop.exit_kind, stop.exit_kind)
        except Exception:  # noqa: BLE001
            return "not recorded"
        return "closed by hand (no exit-kind recorded)"
