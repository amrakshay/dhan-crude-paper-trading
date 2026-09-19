"""What the Connections page shows, and what saving on it does.

**Opening the page costs no network call.** Decision 3: the status pill is LAST
KNOWN, checked in the background, and every card carries the AGE of its check.
A check that has never run says so rather than showing a green pill, and a
connection that has gone quiet shows its last result with its age rather than a
stale success. Same rule `FeedStatusIndicator` already follows.

**The pill has more than two states.** `Connected`, `Not configured`,
`Expiring soon`, `Error`, `Never checked` and `Switched off`. Two states would
force "not set up yet" and "set up and broken" into the same red, and those are
the two an operator most needs to tell apart.
"""
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.connections.database.db_models.connection_model import Connection
from src.connections.services import providers
from src.connections.services.connection_store import (
    ConnectionStore,
    ConnectionStoreError,
)
from src.core.time_utils import to_ist
from src.logging_config import get_logger

logger = get_logger("connections.service")

# The pill's states. More than Running/Stopped, deliberately.
STATUS_CONNECTED = "CONNECTED"
STATUS_NOT_CONFIGURED = "NOT_CONFIGURED"
STATUS_EXPIRING_SOON = "EXPIRING_SOON"
# EXPIRED is not EXPIRING_SOON, and the difference is the whole point of having
# more than two pill states. "Expiring soon" is something to get to today;
# expired means the feed is ALREADY down and automatic renewal cannot recover
# it -- Dhan renews only an active token. Showing an amber "expiring soon" over
# a dead credential is the page understating the one thing it exists to report.
STATUS_EXPIRED = "EXPIRED"
STATUS_ERROR = "ERROR"
STATUS_NEVER_CHECKED = "NEVER_CHECKED"
STATUS_DISABLED = "DISABLED"


def _iso_ist(value) -> Optional[str]:
    """Naive UTC out of the database, IST WITH ITS OFFSET on the wire.

    A naive ISO string is parsed by the browser as LOCAL time, which once made
    the swing Health tab report a switch as moved five and a half hours before
    it was, beside an IST clock reading correctly.
    """
    return to_ist(value).isoformat() if value is not None else None


class ConnectionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.store = ConnectionStore(session)

    # --- the page ----------------------------------------------------------
    async def page(self) -> Dict[str, Any]:
        """Every provider this build knows, configured or not.

        A provider with no row is still a card -- "Not configured" is a state
        somebody has to be able to see in order to do something about it --
        and a ROW naming a provider this build does not know is ignored.
        """
        rows = {
            connection.provider: connection
            for connection in await self.store.connections.list_all()
            if providers.is_known(connection.provider)
        }
        cards: List[Dict[str, Any]] = []
        for spec in providers.all_providers():
            cards.append(await self.card(spec, rows.get(spec.key)))
        return {"connections": cards}

    async def card(
        self, spec: providers.ProviderSpec, connection: Optional[Connection]
    ) -> Dict[str, Any]:
        values = (
            await self.store.values_for(connection) if connection is not None else {}
        )
        configured = self._is_configured(spec, values)
        detail = await self._provider_detail(spec, values)

        status = self._status(spec, connection, configured, detail)
        return {
            "provider": spec.key,
            "label": spec.label,
            "name": connection.name if connection is not None else spec.default_name,
            "subtitle": spec.subtitle,
            "capabilities": list(self._active_capabilities(spec, values)),
            "enabled": bool(connection.enabled) if connection is not None else False,
            "configured": configured,
            "status": status,
            "statusDetail": self._status_detail(status, connection, detail),
            # Decision 3: the age is the operator's, computed in the browser
            # off this absolute timestamp so it keeps ticking between polls.
            "lastCheckedAt": _iso_ist(
                connection.last_checked_at if connection is not None else None
            ),
            # NULL is a third state: never checked is not the same as checked
            # and failed, and they must not share a red pill.
            "lastCheckOk": connection.last_check_ok if connection is not None else None,
            "lastCheckDetail": (
                connection.last_check_detail if connection is not None else None
            ),
            "metricLabel": spec.metric_label,
            "metricValue": detail.get("metricValue"),
            "detail": detail,
        }

    async def detail(self, provider: str) -> Dict[str, Any]:
        """One connection's form: its settings, masked where they are secret."""
        spec = providers.get_provider(provider)
        if spec is None:
            raise ConnectionStoreError(
                f"{provider!r} is not a connection provider this build knows about."
            )
        connection = await self.store.get(spec.key)
        card = await self.card(spec, connection)
        card["settings"] = (
            await self.store.describe(connection) if connection is not None else {}
        )
        return card

    # --- saving ------------------------------------------------------------
    async def save(
        self,
        provider: str,
        settings: Dict[str, Any],
        enabled: Optional[bool] = None,
        name: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Store what changed.

        An OMITTED secret leaves the stored one untouched, so the browser never
        has to round-trip a token to change another field -- the same rule the
        Settings page's access token already follows. Clearing is explicit.
        """
        spec = providers.get_provider(provider)
        if spec is None:
            raise ConnectionStoreError(
                f"{provider!r} is not a connection provider this build knows about."
            )

        connection = await self.store.ensure(
            spec.key, name=name, updated_by_user_id=user_id
        )
        if name:
            connection.name = name[:80]
        if enabled is not None:
            connection.enabled = bool(enabled)
        connection.updated_by_user_id = user_id or connection.updated_by_user_id

        for key, value in (settings or {}).items():
            if key not in spec.known_keys:
                # Ignored rather than refused: a browser sending a field this
                # build does not know is a version skew, not an attack, and
                # refusing the whole save would make the page unusable.
                logger.debug("Ignoring unknown %s setting %r", spec.label, key)
                continue
            if spec.is_secret(key) and value is None:
                continue  # omitted: keep what is stored
            await self.store.put(spec.key, key, value, updated_by_user_id=user_id)

        await self.session.commit()
        logger.info(
            "Connection %s saved by user %s (enabled=%s)",
            spec.label, user_id, connection.enabled,
        )
        return await self.detail(spec.key)

    # --- the alert catalogue -----------------------------------------------
    async def alert_catalogue(
        self, strategy_key: Optional[str] = None, category: Optional[str] = None
    ) -> Dict[str, Any]:
        """Every alert rule this build has, with when it last fired.

        `category` is the second way to narrow it, and it is a SELECTOR rather
        than a second scoping concept: the IPO dashboard's rules are
        deliberately not strategy-scoped -- no strategy owns an IPO -- so
        `rules_for(strategy_key)` correctly refuses them and a page that is not
        a strategy page needs some other way to ask. The two are mutually
        exclusive; the route rejects both at once.

        READ-ONLY, on both surfaces. What gets alerted is a property of the
        build, decided in code and reviewed like code -- there is deliberately
        no edit and no delete.

        **Never fired is not zero.** A rule with no stored row reports
        `lastFiredAt: null`, which the page renders as "never" rather than as a
        timestamp or a count of nothing. That is the same rule the connection
        pill follows and the same one the swing Health tab follows.
        """
        from src.connections.database.db_operations.alert_repository import (
            AlertRepository,
        )
        from src.connections.services import alert_catalogue
        from src.connections.services.alert_dispatcher import get_alert_dispatcher
        from src.connections.services.alert_watcher import get_alert_watcher
        from src.connections.services.telegram_service import TelegramService

        repository = AlertRepository(self.session)
        stats = await repository.stats_by_key(strategy_key=strategy_key)
        last_rows = await repository.by_ids([row["lastId"] for row in stats])

        selected = (
            alert_catalogue.rules_for_category(category)
            if category is not None
            else alert_catalogue.rules_for(strategy_key)
        )

        rules: List[Dict[str, Any]] = []
        for rule in selected:
            matched = [row for row in stats if self._matches(rule, row)]
            payload = rule.as_dict()
            if not matched:
                # Three states, and only one of them is "working": never
                # fired, fired and delivered, fired and not delivered.
                payload.update(
                    {
                        "lastFiredAt": None,
                        "lastStatus": None,
                        "lastTitle": None,
                        "timesFired": 0,
                        "timesCollapsed": 0,
                    }
                )
            else:
                newest_id = max(row["lastId"] for row in matched)
                newest = last_rows.get(newest_id)
                payload.update(
                    {
                        "lastFiredAt": _iso_ist(
                            newest.created_at if newest is not None else None
                        ),
                        "lastStatus": newest.status if newest is not None else None,
                        "lastTitle": newest.title if newest is not None else None,
                        "timesFired": sum(row["total"] for row in matched),
                        "timesCollapsed": sum(row["collapsed"] for row in matched),
                    }
                )
            rules.append(payload)

        config = await TelegramService(self.session).config()
        dispatcher = get_alert_dispatcher().status()
        watcher = get_alert_watcher().status()

        return {
            "strategyKey": strategy_key,
            "rules": rules,
            # Recording and DELIVERING are different things, and a page that
            # conflated them would show a healthy list while every message was
            # being dropped on the floor.
            "delivery": {
                "recording": True,
                "delivering": bool(config.can_send and dispatcher.get("enabled")),
                "telegramConfigured": bool(config.bot_token and config.chat_id),
                "telegramEnabled": bool(config.enabled),
                "channel": config.chat_title or config.chat_id or None,
                "dispatcherRunning": bool(dispatcher.get("running")),
                "watcherRunning": bool(watcher.get("running")),
                "lastDispatchAt": dispatcher.get("lastPassAt"),
                "lastWatchAt": watcher.get("lastPassAt"),
                "sink": dispatcher.get("sink"),
            },
            "counts": await repository.counts_by_status(),
            "recent": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "severity": row.severity,
                    "title": row.title,
                    "body": row.body,
                    "status": row.status,
                    "strategyKey": row.strategy_key,
                    "suppressedCount": int(row.suppressed_count or 0),
                    "lastError": row.last_error,
                    "createdAt": _iso_ist(row.created_at),
                    "sentAt": _iso_ist(row.sent_at),
                }
                for row in await repository.recent(
                    limit=30, strategy_key=strategy_key
                )
                # A category's page shows the rows ITS rules produced. Filtered
                # here rather than in SQL because what identifies a rule is its
                # kind plus a dedupe PREFIX, which `_matches` already knows and
                # a query would have to learn twice.
                if category is None
                or any(
                    self._matches(
                        rule,
                        {"kind": row.kind, "dedupeKey": row.dedupe_key},
                    )
                    for rule in selected
                )
            ],
            "notes": self._catalogue_notes(strategy_key, category),
        }

    @staticmethod
    def _matches(rule, row: Dict[str, Any]) -> bool:
        """Whether a stored row belongs to a catalogue rule.

        A health event's dedupe key is `health|<event>` and may carry a suffix
        -- the task names, the sessions, the strategy -- so it is matched on the
        PREFIX. Everything else is identified by its kind alone.
        """
        if row["kind"] != rule.kind:
            return False
        if rule.dedupe_prefix is None:
            return True
        return str(row.get("dedupeKey") or "").startswith(rule.dedupe_prefix)

    @staticmethod
    def _catalogue_notes(
        strategy_key: Optional[str], category: Optional[str] = None
    ) -> List[str]:
        """What this page cannot tell you, said rather than implied away."""
        notes = [
            "This list is read-only. What gets alerted is a property of the "
            "build, decided in code and reviewed like code -- not a preference.",
            "\"Last fired\" is when the alert was RECORDED. Whether it was "
            "delivered is its status, which is a separate thing: a fact is "
            "recorded even when nothing is configured to carry it.",
            "Counts are for the whole history in this database, not a window. "
            "The status totals beside them cover the last 24 hours.",
        ]
        if strategy_key is not None:
            notes.append(
                "Only alerts ABOUT this strategy are shown. Process-wide ones "
                "-- a dead task, the feed, the Dhan token -- live on the System "
                "Health page, because they are not this strategy's business."
            )
        if category is not None:
            notes.append(
                "Only this feature's own rules are shown. They are NOT "
                "withdrawn from the System Health page, which lists every rule "
                "in the build -- this is a narrower view of the same list, not "
                "a second one."
            )
        return notes

    # --- status ------------------------------------------------------------
    @staticmethod
    def _is_configured(spec: providers.ProviderSpec, values: Dict[str, str]) -> bool:
        if spec.key == providers.PROVIDER_DHAN:
            return bool(
                values.get(providers.DHAN_CLIENT_ID)
                and values.get(providers.DHAN_ACCESS_TOKEN)
            )
        if spec.key == providers.PROVIDER_TELEGRAM:
            # A bot token with no channel is HALF configured and cannot send.
            return bool(
                values.get(providers.TELEGRAM_BOT_TOKEN)
                and values.get(providers.TELEGRAM_CHAT_ID)
            )
        return bool(values)

    @staticmethod
    def _active_capabilities(
        spec: providers.ProviderSpec, values: Dict[str, str]
    ) -> List[str]:
        """One chip per capability the connection ACTUALLY has, not per capability
        the provider could have.

        A Telegram connection with commands switched off does not carry a
        COMMANDS chip, because it does not accept commands. A chip that claims
        a capability nobody enabled is the page lying about what is switched
        on.
        """
        active = []
        for capability in spec.capabilities:
            if capability == providers.CAPABILITY_COMMANDS:
                if str(
                    values.get(providers.TELEGRAM_COMMANDS_ENABLED, "")
                ).strip().lower() not in ("true", "1", "yes", "y", "on"):
                    continue
            active.append(capability)
        return active

    @staticmethod
    def _status(
        spec: providers.ProviderSpec,
        connection: Optional[Connection],
        configured: bool,
        detail: Dict[str, Any],
    ) -> str:
        if connection is None or not configured:
            return STATUS_NOT_CONFIGURED
        if not connection.enabled:
            return STATUS_DISABLED
        if detail.get("expired"):
            return STATUS_EXPIRED
        if detail.get("expiringSoon"):
            return STATUS_EXPIRING_SOON
        if connection.last_check_ok is None:
            return STATUS_NEVER_CHECKED
        if connection.last_check_ok is False:
            return STATUS_ERROR
        return STATUS_CONNECTED

    @staticmethod
    def _status_detail(
        status: str, connection: Optional[Connection], detail: Dict[str, Any]
    ) -> str:
        if status == STATUS_NOT_CONFIGURED:
            return "Nothing is configured yet."
        if status == STATUS_DISABLED:
            return "Configured, but switched off here."
        if status == STATUS_EXPIRED:
            return (
                detail.get("expiryNote")
                or "The credential has expired and has to be replaced by hand."
            )
        if status == STATUS_EXPIRING_SOON:
            return detail.get("expiryNote") or "The credential is close to expiry."
        if status == STATUS_NEVER_CHECKED:
            return (
                "Configured, but never checked. Press Validate to find out "
                "whether it works."
            )
        if status == STATUS_ERROR:
            return (connection.last_check_detail if connection else None) or (
                "The last check failed."
            )
        return (connection.last_check_detail if connection else None) or "Last check succeeded."

    # --- provider-specific extras -----------------------------------------
    async def _provider_detail(
        self, spec: providers.ProviderSpec, values: Dict[str, str]
    ) -> Dict[str, Any]:
        if spec.key == providers.PROVIDER_DHAN:
            return await self._dhan_detail(values)
        if spec.key == providers.PROVIDER_TELEGRAM:
            return await self._telegram_detail(values)
        return {}

    async def _dhan_detail(self, values: Dict[str, str]) -> Dict[str, Any]:
        """The Dhan card: the token's own expiry and the renewal state.

        Nothing here contacts Dhan. `inspect_token()` reads the `exp` claim
        LOCALLY, the renewal counters are a `status()` dict the task already
        keeps, and `dhan_auth_state` is a record of how the LAST real call went
        rather than a fresh one -- so the card can say that Dhan is refusing
        the token without asking Dhan whether it is.
        """
        from src import config_utils
        from src.market.services import dhan_auth_state
        from src.settings.services.settings_service import (
            KEY_ACCESS_TOKEN,
            KEY_CLIENT_ID,
            SettingsService,
            inspect_token,
        )
        from src.settings.services.token_refresh_service import (
            DEFAULT_RENEW_BEFORE_HOURS,
            get_token_refresh_monitor,
        )

        env = SettingsService.env_fallbacks()
        client_id = values.get(providers.DHAN_CLIENT_ID) or env.get(KEY_CLIENT_ID) or ""
        token = values.get(providers.DHAN_ACCESS_TOKEN) or env.get(KEY_ACCESS_TOKEN) or ""
        info = inspect_token(token)

        renew_before = config_utils.get_property_value_int(
            "dhan.renew_token_before_hours", DEFAULT_RENEW_BEFORE_HOURS
        )
        hours_left = (
            (info.seconds_remaining / 3600) if info.seconds_remaining is not None else None
        )
        # Kept apart: one is a warning, the other is an outage.
        expired = bool(info.present and info.expired)
        expiring = bool(
            not expired and hours_left is not None and hours_left < renew_before
        )

        detail: Dict[str, Any] = {
            "clientId": client_id,
            "clientIdSource": "database" if values.get(providers.DHAN_CLIENT_ID) else (
                "env" if env.get(KEY_CLIENT_ID) else "default"
            ),
            "accessTokenSource": "database" if values.get(providers.DHAN_ACCESS_TOKEN) else (
                "env" if env.get(KEY_ACCESS_TOKEN) else "default"
            ),
            "token": info.as_dict(),
            "autoRenew": get_token_refresh_monitor().status(),
            "expired": expired,
            "expiringSoon": expiring,
            # OBSERVED, as against the declared expiry above. A token can be
            # revoked server-side long before `exp` -- it happened on
            # 2026-09-19, four hours into a twenty-four hour token -- and
            # nothing local can see that, so the card would otherwise show a
            # comfortable countdown for a token Dhan was refusing outright.
            "dhanVerdict": dhan_auth_state.state_for(token).as_dict(),
            "metricValue": await self._instruments_on_the_feed(),
        }
        if dhan_auth_state.state_for(token).refused:
            # BEFORE the expiry notes, and instead of them: when Dhan is
            # refusing the token, how long it had left is not the useful fact.
            detail["expiryNote"] = (
                "DHAN IS REFUSING THIS TOKEN, whatever the countdown says. A "
                "token can be revoked long before it expires -- generating a "
                "new one for the same client id invalidates the previous one, "
                "and a lapsed Data APIs subscription does the same. Generate a "
                "fresh token on Dhan Web and save it here."
            )
        elif info.expired:
            detail["expiryNote"] = (
                "The token has expired. Automatic renewal cannot recover from "
                "this -- Dhan renews only an active token, and this application "
                "deliberately cannot mint one. Paste a new token."
            )
        elif expiring and hours_left is not None:
            detail["expiryNote"] = (
                f"{hours_left:.1f} hour(s) left; automatic renewal takes over "
                f"under {renew_before}."
            )
        return detail

    @staticmethod
    async def _instruments_on_the_feed() -> Optional[int]:
        """An existing counter, joined on. Never a new measurement."""
        try:
            from src.market.services.feed_manager import get_feed_manager

            book = get_feed_manager().book
            stats = book.stats() if book is not None else {}
            value = stats.get("instruments")
            return int(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None

    async def _telegram_detail(self, values: Dict[str, str]) -> Dict[str, Any]:
        from src.connections.services.telegram_command_service import (
            get_telegram_poller,
        )
        from src.connections.services.telegram_service import counters

        chat_title = values.get(providers.TELEGRAM_CHAT_TITLE) or ""
        chat_id = values.get(providers.TELEGRAM_CHAT_ID) or ""
        return {
            "chatId": chat_id,
            "chatTitle": chat_title,
            "commandsEnabled": self._truthy(
                values.get(providers.TELEGRAM_COMMANDS_ENABLED)
            ),
            "controlCommandsEnabled": self._truthy(
                values.get(providers.TELEGRAM_CONTROL_COMMANDS)
            ),
            "poller": get_telegram_poller().status(),
            "counters": counters().as_dict(),
            "commandUsers": await self._command_users(),
            "metricValue": chat_title or chat_id or None,
        }

    @staticmethod
    def _truthy(value: Optional[str]) -> bool:
        return str(value or "").strip().lower() in ("true", "1", "yes", "y", "on")

    async def _command_users(self) -> List[Dict[str, Any]]:
        """Who may issue a command, read off the USERS table.

        Nobody by default. This is a read of the existing authorisation model,
        not a second one -- which is the whole reason the mapping lives on
        `users` (see `telegram_command_service`).
        """
        from src.users.database.db_operations.user_repository import UserRepository

        mapped = []
        for user in await UserRepository(self.session).list_users():
            if user.telegram_user_id is None:
                continue
            mapped.append(
                {
                    "userId": user.id,
                    "email": user.email,
                    "name": user.full_name,
                    "role": user.role,
                    "status": user.status,
                    "telegramUserId": user.telegram_user_id,
                }
            )
        return mapped
