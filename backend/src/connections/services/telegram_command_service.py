"""Commands IN: the first inbound control path this application has ever had.

Every mutation in this codebase until now arrived through the authenticated API
with a role check. This is a message from a chat app reaching an application
that, when ARMED, spends money on its own schedule. It is built accordingly.

**A CHANNEL ID IS NOT AUTHENTICATION.** It identifies a destination, not a
person -- anyone who learns it and can post in it would otherwise be issuing
commands. So a command is authorised by resolving the update's `from.id` to an
APPLICATION USER (`users.telegram_user_id`) and applying that user's existing
role.

That is not tidiness. A standalone list of Telegram ids would be a SECOND
authorisation model, and this codebase already has one whose properties took
work to get right: `require_admin` is what refuses, not the sidebar; the user
is re-read on every request so a demotion takes effect immediately rather than
at token expiry; the seeded admin cannot be deleted, demoted or deactivated,
enforced on `is_seed_user` rather than by comparing emails; and a user who owes
a password change is refused everything except the endpoints that let them stop
owing one. Mapping the sender to a user inherits every one of those for free. A
parallel list inherits none of them, and the first time somebody was
deactivated they would still be able to arm a strategy from their phone.

**NOBODY IS MAPPED BY DEFAULT.** Commands are off until somebody is, and a
Telegram connection with alerts working and no command users is a normal,
complete state.

**Matching is on the NUMERIC user id, never on `@username`** -- usernames are
reassignable, and an authorisation that can be transferred by somebody
releasing a handle is not an authorisation.

**The two halves are separate, and the second is opt-in.** Read-only commands
are most of the value and none of the risk. Control commands change what the
software does with money, so they need `ROLE_ACCOUNT_ADMIN` -- the same gate the
arming endpoint uses, not a thinner one -- AND their own switch, which is off by
default. A Telegram message that arms a strategy cannot be a thinner path than
the button, which has a confirmation dialog carrying the server's warnings.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import UserRole, UserStatus
from src.core.time_utils import ist_now, utc_now
from src.logging_config import get_logger

logger = get_logger("connections.commands")

# Read-only: these read the same services the pages read.
READ_ONLY_COMMANDS = ("/status", "/book", "/pnl", "/health", "/help", "/start")
# Control: these change what the software does with money.
CONTROL_COMMANDS = ("/arm", "/disarm", "/run")

# How long each long poll waits at Telegram before returning empty.
POLL_TIMEOUT_SECONDS = 25
# After a failure, how long before trying again. Long enough that a bad token
# is not hammered; short enough that fixing it takes effect within a minute.
RETRY_AFTER_SECONDS = 60
# How often to re-read the configuration while commands are switched off.
IDLE_INTERVAL_SECONDS = 30


@dataclass
class Authorisation:
    """Who sent this, and what they may do."""

    telegram_user_id: int
    user_id: Optional[int] = None
    user_label: str = ""
    role: Optional[str] = None
    allowed: bool = False
    may_control: bool = False
    refusal: str = ""


class TelegramCommandService:
    """Resolves a sender to a user, then answers the command with that role."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # --- authorisation -----------------------------------------------------
    async def authorise(self, telegram_user_id: Optional[int]) -> Authorisation:
        """Map `from.id` to an application user and read that user's role.

        Every refusal below mirrors one the HTTP API already makes, which is
        the entire point of mapping rather than keeping a second list.
        """
        if telegram_user_id is None:
            return Authorisation(
                telegram_user_id=0,
                refusal=(
                    "That message carried no sender. A channel post has no user, "
                    "so it cannot be authorised -- send commands as a direct "
                    "message to the bot."
                ),
            )

        from src.users.database.db_operations.user_repository import UserRepository

        user = await UserRepository(self.session).get_by_telegram_user_id(
            int(telegram_user_id)
        )
        if user is None:
            return Authorisation(
                telegram_user_id=int(telegram_user_id),
                refusal=(
                    f"Telegram user {telegram_user_id} is not mapped to an "
                    f"account here, so nothing is authorised. An administrator "
                    f"can map it on the Users page."
                ),
            )
        if user.status != UserStatus.ACTIVE.value:
            return Authorisation(
                telegram_user_id=int(telegram_user_id),
                user_id=user.id,
                user_label=user.full_name,
                role=user.role,
                refusal="That account is deactivated.",
            )
        if user.must_change_password:
            # The same refusal `require_session` makes: a user who owes a
            # password change reaches nothing until they stop owing one, and
            # they cannot change it from here.
            return Authorisation(
                telegram_user_id=int(telegram_user_id),
                user_id=user.id,
                user_label=user.full_name,
                role=user.role,
                refusal=(
                    "That account still owes a password change. Sign in to the "
                    "web application and set a password first."
                ),
            )

        return Authorisation(
            telegram_user_id=int(telegram_user_id),
            user_id=user.id,
            user_label=user.full_name,
            role=user.role,
            allowed=True,
            may_control=user.role == UserRole.ACCOUNT_ADMIN.value,
        )

    # --- dispatch ----------------------------------------------------------
    async def handle(self, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Answer one update. Returns {reply, command, ...} or None to ignore."""
        message = update.get("message") or update.get("edited_message") or {}
        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return None

        # "/arm@my_bot extra" -> "/arm"
        command = text.split()[0].split("@")[0].lower()
        arguments = text.split()[1:]
        sender = (message.get("from") or {}).get("id")

        known = command in READ_ONLY_COMMANDS or command in CONTROL_COMMANDS
        authorisation = await self.authorise(sender)

        if not authorisation.allowed:
            logger.warning(
                "Refused Telegram command %s from user id %s: %s",
                command, sender, authorisation.refusal,
            )
            return await self._journalled({
                "command": command,
                "authorisation": authorisation,
                "reply": f"Refused. {authorisation.refusal}",
                "changed": False,
            })

        if not known:
            return await self._journalled({
                "command": command,
                "authorisation": authorisation,
                "reply": f"{command} is not a command here.\n\n{self.help_text(authorisation)}",
                "changed": False,
            })

        if command in CONTROL_COMMANDS:
            enabled = await self._control_enabled()
            if not enabled:
                # A refusal must SAY it is a refusal-because-disabled, or the
                # operator has no way to tell it from a failure.
                return await self._journalled({
                    "command": command,
                    "authorisation": authorisation,
                    "reply": (
                        f"{command} is a control command and control commands "
                        f"are switched off for this connection. Switch them on "
                        f"on the Connections page if you want them."
                    ),
                    "changed": False,
                })
            if not authorisation.may_control:
                return await self._journalled({
                    "command": command,
                    "authorisation": authorisation,
                    "reply": (
                        f"{command} needs an account administrator. Your account "
                        f"is {authorisation.role}."
                    ),
                    "changed": False,
                })

        handler = {
            "/help": self._help,
            "/start": self._help,
            "/status": self._status,
            "/book": self._book,
            "/pnl": self._pnl,
            "/health": self._health,
            "/arm": self._arm,
            "/disarm": self._disarm,
            "/run": self._run,
        }[command]

        try:
            reply = await handler(authorisation, arguments)
        except Exception as error:  # noqa: BLE001 - a bad command must not end the poller
            logger.exception("Telegram command %s failed", command)
            reply = f"{command} could not be completed ({type(error).__name__})."

        return await self._journalled(
            {
                "command": command,
                "authorisation": authorisation,
                "reply": reply,
                "changed": command in CONTROL_COMMANDS,
            }
        )

    async def _journalled(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """Record a state-changing command, and every refusal, with its sender.

        Journalled HERE rather than in the poller, so the record is made where
        the command is DECIDED. An action with no record is the thing this
        codebase most consistently refuses, and a refusal that leaves no record
        is how you fail to notice an attempt.

        The thing a command CHANGED carries `updated_by_user_id` as well, filled
        in exactly as the UI fills it -- that is the payoff of resolving the
        sender to an application user. This row is the record that the
        instruction arrived at all.
        """
        authorisation = outcome["authorisation"]
        if not (outcome["changed"] or not authorisation.allowed):
            return outcome

        from src.connections.services.alert_service import AlertService

        try:
            await AlertService(self.session).record_command(
                command=outcome["command"],
                telegram_user_id=authorisation.telegram_user_id,
                user_id=authorisation.user_id,
                outcome=(outcome["reply"] or "").splitlines()[0]
                if outcome["reply"]
                else "",
                allowed=authorisation.allowed,
            )
        except Exception:  # noqa: BLE001 - never lose the command to its journal
            logger.warning("Could not journal a Telegram command", exc_info=True)
        return outcome

    async def _control_enabled(self) -> bool:
        from src.connections.services.telegram_service import TelegramService

        return (await TelegramService(self.session).config()).control_commands_enabled

    # --- read-only ---------------------------------------------------------
    @staticmethod
    def help_text(authorisation: Authorisation) -> str:
        lines = [
            "Dhan Paper Trading",
            "",
            "Read-only:",
            "  /status  - strategies, what is on and armed",
            "  /book    - open positions",
            "  /pnl     - realised P&L and equity",
            "  /health  - background tasks and the feed",
        ]
        if authorisation.may_control:
            lines += [
                "",
                "Control (administrator, and only when switched on):",
                "  /arm <strategy>    - let it place orders on its own",
                "  /disarm <strategy> - stop it placing orders",
                "  /run <strategy>    - run its analysis now",
            ]
        return "\n".join(lines)

    async def _help(self, authorisation: Authorisation, _arguments: List[str]) -> str:
        return self.help_text(authorisation)

    async def _status(self, _authorisation: Authorisation, _arguments: List[str]) -> str:
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        lines = [f"Status at {ist_now().strftime('%H:%M IST, %d %b %Y')}", ""]
        for definition in registry.all():
            enabled = registry.is_enabled(definition.key)
            bits = ["on" if enabled else "off"]
            if definition.automation is not None:
                bits.append("armed" if registry.is_armed(definition.key) else "not armed")
            lines.append(f"{definition.key}: {', '.join(bits)}")
        return "\n".join(lines)

    async def _book(self, _authorisation: Authorisation, _arguments: List[str]) -> str:
        from src.portfolios.database.db_operations.portfolio_repository import (
            PortfolioRepository,
        )
        from src.positions.database.db_operations.position_repository import (
            PositionRepository,
        )

        portfolios = await PortfolioRepository(self.session).list_portfolios()
        if not portfolios:
            return "There are no portfolios."

        lines: List[str] = []
        for portfolio in portfolios:
            open_positions = await PositionRepository(self.session).list_open(
                portfolio_id=portfolio.id
            )
            lines.append(f"{portfolio.name}: {len(open_positions)} open")
            for position in open_positions[:20]:
                lines.append(
                    f"  {position.trading_symbol}  {position.net_quantity} @ "
                    f"{position.average_price}"
                )
            if len(open_positions) > 20:
                lines.append(f"  ... and {len(open_positions) - 20} more")
        return "\n".join(lines)

    async def _pnl(self, _authorisation: Authorisation, _arguments: List[str]) -> str:
        from src.portfolios.database.db_operations.portfolio_repository import (
            PortfolioRepository,
        )
        from src.portfolios.services.balance_service import BalanceService

        portfolios = await PortfolioRepository(self.session).list_portfolios()
        if not portfolios:
            return "There are no portfolios."

        balances = BalanceService(self.session)
        lines: List[str] = []
        for portfolio in portfolios:
            balance = await balances.balance_for(portfolio.id)
            # Four figures, never one -- the same rule the UI follows. And an
            # equity figure that could not be computed says so rather than
            # valuing an unmarked position at zero.
            equity = (
                f"₹{balance.equity:,.2f}"
                if balance.equity is not None
                else "not measured (a position has no mark)"
            )
            lines += [
                portfolio.name,
                f"  cash      ₹{balance.cash:,.2f}",
                f"  blocked   ₹{balance.blocked_margin:,.2f} (estimate)",
                f"  available ₹{balance.available:,.2f}",
                f"  equity    {equity}",
            ]
        return "\n".join(lines)

    async def _health(self, _authorisation: Authorisation, _arguments: List[str]) -> str:
        from src.health.services import task_inspector

        flags = task_inspector.feed_flags()
        table = task_inspector.inspect(
            is_synthetic=flags["is_synthetic"], feed_running=flags["feed_running"]
        )
        lines = [
            f"Health at {ist_now().strftime('%H:%M IST')}",
            f"Feed: {'synthetic' if flags['is_synthetic'] else 'live'}, "
            f"{'running' if flags['feed_running'] else 'not running'}",
            f"Tasks: {table['runningCount']} running of {table['expectedCount']} expected",
        ]
        if table["missing"]:
            lines.append("MISSING: " + ", ".join(table["missing"]))
        if table["unexpected"]:
            lines.append("Unexpected: " + ", ".join(table["unexpected"]))
        if not table["missing"] and not table["unexpected"]:
            lines.append("Nothing is missing.")
        return "\n".join(lines)

    # --- control -----------------------------------------------------------
    async def _arm(self, authorisation: Authorisation, arguments: List[str]) -> str:
        return await self._set_armed(authorisation, arguments, True)

    async def _disarm(self, authorisation: Authorisation, arguments: List[str]) -> str:
        return await self._set_armed(authorisation, arguments, False)

    async def _set_armed(
        self, authorisation: Authorisation, arguments: List[str], armed: bool
    ) -> str:
        from src.strategies.services.strategy_registry import get_strategy_registry
        from src.strategies.services.strategy_state_service import (
            StrategyConfigError,
            StrategyStateService,
        )

        key = self._strategy_from(arguments)
        if key is None:
            automated = [one.key for one in get_strategy_registry().automated()]
            return (
                "Name the strategy: "
                + (", ".join(automated) if automated else "there are none to arm.")
            )

        try:
            # `updated_by_user_id` is filled in exactly as the UI fills it,
            # which is what makes a command from a phone leave the same audit
            # trail as a click.
            result = await StrategyStateService(self.session).set_strategy_armed(
                key, armed, user_id=authorisation.user_id
            )
        except StrategyConfigError as error:
            return str(error)

        warnings = result.get("warnings") or []
        lines = [f"{key} is now {'ARMED' if armed else 'not armed'}."]
        lines += [f"- {warning}" for warning in warnings]
        return "\n".join(lines)

    async def _run(self, authorisation: Authorisation, arguments: List[str]) -> str:
        from src.strategies.services.strategy_registry import get_strategy_registry

        key = self._strategy_from(arguments)
        if key is None:
            automated = [one.key for one in get_strategy_registry().automated()]
            return (
                "Name the strategy: "
                + (", ".join(automated) if automated else "there are none to run.")
            )
        definition = get_strategy_registry().get(key)
        if definition is None:
            return f"{key} is not a strategy here."

        from src.swing.services.scheduler import get_swing_scheduler

        scheduler = get_swing_scheduler()
        runs = await scheduler.tick(now=None)
        detail = ", ".join(
            f"{run.kind} {'ok' if run.ok else 'failed'}" for run in runs
        )
        return (
            f"Asked the scheduler to run what is due for {key}. "
            + (detail or "Nothing was due.")
        )

    @staticmethod
    def _strategy_from(arguments: List[str]) -> Optional[str]:
        from src.strategies.services.strategy_registry import get_strategy_registry

        registry = get_strategy_registry()
        if arguments:
            wanted = arguments[0].strip().lower()
            for definition in registry.all():
                if definition.key.lower() == wanted:
                    return definition.key
            return wanted  # let the caller report it as unknown
        automated = registry.automated()
        return automated[0].key if len(automated) == 1 else None


class TelegramCommandPoller:
    """The background task that reads updates, in the shape this codebase uses.

    Own task, own session per pass, off the tick path, registered in
    `TASK_DESCRIPTIONS` **and** `expected_task_names`, with a `status()` the
    health page reads -- the same five properties `OrderMatcher`,
    `bracket_monitor`, `swing_stop_monitor`, `swing_scheduler` and
    `dhan-token-refresh` have.

    It also fans every update out to any registered listener, which is what the
    "Listen for a test message" button borrows instead of opening a second
    `getUpdates` consumer.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._offset: Optional[int] = None
        self._listeners: List[asyncio.Queue] = []
        self.passes = 0
        self.updates = 0
        self.commands = 0
        self.refusals = 0
        self.last_pass_at = None
        self.last_error: Optional[str] = None
        self.enabled = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def polling(self) -> bool:
        """Whether it is actually CONSUMING updates right now.

        Not the same as `running`, and the difference is the whole point. The
        task starts unconditionally and re-reads the connection on each pass,
        so that switching commands on takes effect without a restart -- which
        means that while commands are OFF the task is alive and calling nothing.

        The listen test borrows this stream to avoid being a second
        `getUpdates` consumer. Borrowing a stream that is not being consumed
        would make the test wait its whole window and receive nothing, and the
        listen test has to work with commands switched OFF because it is how
        you switch them on. So: borrow only when there is something to borrow.
        """
        return self.running and self.enabled

    # --- borrowing the stream ---------------------------------------------
    async def wait_for_update(self, seconds: int) -> Optional[Dict[str, Any]]:
        """Wait for the next update this poller sees, or None on a timeout."""
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners.append(queue)
        try:
            return await asyncio.wait_for(queue.get(), timeout=seconds)
        except asyncio.TimeoutError:
            return None
        finally:
            if queue in self._listeners:
                self._listeners.remove(queue)

    def _fan_out(self, update: Dict[str, Any]) -> None:
        for queue in list(self._listeners):
            try:
                queue.put_nowait(update)
            except Exception:  # noqa: BLE001 - a full listener must not stop the poll
                pass

    # --- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self.running:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="telegram-commands")
        logger.info("Telegram command poller started")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        from src.database.session import session_scope

        while not self._stopping:
            delay = IDLE_INTERVAL_SECONDS
            try:
                async with session_scope() as session:
                    delay = await self._one_pass(session)
                self.passes += 1
                self.last_pass_at = utc_now()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a bad pass must not end the task
                self.last_error = f"{type(error).__name__}: {error}"
                logger.exception("Telegram command poll failed")
                delay = RETRY_AFTER_SECONDS

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    async def _one_pass(self, session) -> float:
        """One long poll. Returns how long to wait before the next one."""
        from src.connections.services.telegram_client import TelegramError
        from src.connections.services.telegram_service import TelegramService

        service = TelegramService(session)
        config = await service.config()
        self.enabled = bool(config.enabled and config.commands_enabled and config.bot_token)
        if not self.enabled:
            return IDLE_INTERVAL_SECONDS

        client = await service.client(config.bot_token)
        try:
            updates = await client.get_updates(
                offset=self._offset, timeout_seconds=POLL_TIMEOUT_SECONDS
            )
        except TelegramError as error:
            # A bad token cannot fix itself by being asked again immediately;
            # flood control and a network blip can. Both back off, and neither
            # ends the task.
            self.last_error = error.kind
            logger.warning("Telegram command poll refused: %s", error.kind)
            return float(error.retry_after or RETRY_AFTER_SECONDS)

        for update in updates:
            self._offset = int(update.get("update_id", 0)) + 1
            self.updates += 1
            self._fan_out(update)
            await self._handle(session, service, update)

        # No sleep between successful long polls: getUpdates blocks at Telegram
        # for its own timeout, so the loop is already paced by the server.
        return 0.1

    async def _handle(self, session, service, update: Dict[str, Any]) -> None:
        from src.connections.services.telegram_client import TelegramError

        outcome = await TelegramCommandService(session).handle(update)
        if outcome is None:
            return

        self.commands += 1
        authorisation = outcome["authorisation"]
        if not authorisation.allowed:
            self.refusals += 1

        # `handle()` has already journalled it, where the command was decided.
        # Commit before replying: a command that arrived and whose answer could
        # not be delivered still happened.
        await session.commit()

        chat = (
            (update.get("message") or update.get("edited_message") or {}).get("chat")
            or {}
        )
        chat_id = chat.get("id")
        if chat_id is None:
            return
        try:
            client = await service.client()
            await client.send_message(str(chat_id), outcome["reply"])
        except TelegramError as error:
            logger.warning("Could not reply to a Telegram command: %s", error.kind)

    def status(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "enabled": self.enabled,
            "passes": self.passes,
            "updates": self.updates,
            "commands": self.commands,
            "refusals": self.refusals,
            "listeners": len(self._listeners),
            "lastPassAt": self.last_pass_at.isoformat() if self.last_pass_at else None,
            "lastError": self.last_error,
        }


_poller: Optional[TelegramCommandPoller] = None


def get_telegram_poller() -> TelegramCommandPoller:
    global _poller
    if _poller is None:
        _poller = TelegramCommandPoller()
    return _poller


async def shutdown_telegram_poller() -> None:
    global _poller
    if _poller is not None:
        await _poller.stop()
        _poller = None
