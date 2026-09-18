"""The providers this build knows about, and what each one's card says.

This module is the answer to "is this row real". A `connections` row naming
anything not in `PROVIDERS` is ignored on load -- the same property
`MANAGED_KEYS` gives `app_settings`, and for the same reason: a stray row must
never start influencing configuration.

It holds no logic and no I/O. What a provider DOES lives in its own service
(`telegram_service`, and for Dhan in the settings/feed machinery that already
existed); this is the shape of its card and the names of its settings.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PROVIDER_DHAN = "dhan"
PROVIDER_TELEGRAM = "telegram"

# The tag chips on a card: one per capability the connection actually has.
CAPABILITY_MARKET_DATA = "MARKET_DATA"
CAPABILITY_ALERTS = "ALERTS"
CAPABILITY_COMMANDS = "COMMANDS"

# --- Dhan setting keys ------------------------------------------------------
DHAN_CLIENT_ID = "client_id"
DHAN_ACCESS_TOKEN = "access_token"

# --- Telegram setting keys --------------------------------------------------
TELEGRAM_BOT_TOKEN = "bot_token"
TELEGRAM_CHAT_ID = "chat_id"
TELEGRAM_CHAT_TITLE = "chat_title"
# Control commands (/arm, /disarm, /run) are OFF until somebody switches them
# on, separately from the read-only half. See telegram_command_service.
TELEGRAM_CONTROL_COMMANDS = "control_commands_enabled"
# The command poller itself, which is what makes commands-in exist at all.
TELEGRAM_COMMANDS_ENABLED = "commands_enabled"


@dataclass(frozen=True)
class ProviderSpec:
    """What a provider is, for the page and for the store."""

    key: str
    label: str
    subtitle: str
    capabilities: Tuple[str, ...]
    # Keys whose value is a secret. These go in `encrypted_value`, are
    # registered with `log_redaction` the moment they are read or saved, and
    # are never returned to the browser in full.
    secret_keys: Tuple[str, ...] = ()
    # Keys stored as plaintext. Anything not in either list is ignored.
    plain_keys: Tuple[str, ...] = ()
    # Only one row of this provider makes sense for now; the schema allows
    # more, and the page refuses to create a second until something needs it.
    singleton: bool = True
    default_name: str = ""
    # The card's left-hand metric is provider-specific; the page asks for it
    # by this label.
    metric_label: str = ""

    @property
    def known_keys(self) -> Tuple[str, ...]:
        return self.secret_keys + self.plain_keys

    def is_secret(self, key: str) -> bool:
        return key in self.secret_keys


PROVIDERS: Dict[str, ProviderSpec] = {
    PROVIDER_DHAN: ProviderSpec(
        key=PROVIDER_DHAN,
        label="Dhan",
        subtitle="Market data · Live feed, chart, option chain",
        capabilities=(CAPABILITY_MARKET_DATA,),
        secret_keys=(DHAN_ACCESS_TOKEN,),
        plain_keys=(DHAN_CLIENT_ID,),
        default_name="Dhan market data",
        metric_label="Instruments on the feed",
    ),
    PROVIDER_TELEGRAM: ProviderSpec(
        key=PROVIDER_TELEGRAM,
        label="Telegram",
        subtitle="Alerts · Commands",
        capabilities=(CAPABILITY_ALERTS, CAPABILITY_COMMANDS),
        secret_keys=(TELEGRAM_BOT_TOKEN,),
        plain_keys=(
            TELEGRAM_CHAT_ID,
            TELEGRAM_CHAT_TITLE,
            TELEGRAM_COMMANDS_ENABLED,
            TELEGRAM_CONTROL_COMMANDS,
        ),
        default_name="Telegram bot",
        metric_label="Alert channel",
    ),
}


def get_provider(key: Optional[str]) -> Optional[ProviderSpec]:
    """The spec for a provider, or None if this build does not know it.

    None is the signal to IGNORE a row rather than to raise: a database this
    process does not fully understand must still boot.
    """
    if not key:
        return None
    return PROVIDERS.get(str(key).strip().lower())


def is_known(key: Optional[str]) -> bool:
    return get_provider(key) is not None


def all_providers() -> List[ProviderSpec]:
    return [PROVIDERS[key] for key in sorted(PROVIDERS)]
