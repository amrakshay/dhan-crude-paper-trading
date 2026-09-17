"""Settings saved in the UI must be in effect from the first line of startup.

`apply_to_config()` overlays the `app_settings` table onto the in-memory config
that every `config_utils` caller reads. It used to be called from
`SettingsService.save()` and nowhere else, so a process that restarted after a
save ran on its `.env` values: a Dhan token configured on the Settings page was
silently not the one the feed used, and nothing in the application said so.

Two things are asserted here, because either alone would let the bug back:

  1. The overlay actually happens (behaviour).
  2. It happens BEFORE the feed starts (ordering). Applying the settings after
     `get_feed_manager().start()` would leave the feed connected with the .env
     credentials, which is the same bug wearing a different hat.
"""
import inspect

import pytest

from src import config_utils
from src.settings.services.settings_service import (
    KEY_CLIENT_ID,
    KEY_SYNTHETIC_FEED,
)


@pytest.fixture
def restore_config():
    """Put the in-memory config back.

    apply_to_config() mutates process-wide state, so a test that leaves it
    changed pollutes every test that runs after it -- which is exactly how the
    original bug's blast radius works.
    """
    config = config_utils.load_config()
    before = {
        KEY_CLIENT_ID: config_utils.get_property_value(KEY_CLIENT_ID, ""),
        KEY_SYNTHETIC_FEED: config_utils.get_property_value(KEY_SYNTHETIC_FEED, ""),
    }
    yield
    for key, value in before.items():
        section, _, leaf = key.partition(".")
        node = config.setdefault(section, {})
        if isinstance(node, dict):
            node[leaf] = value


async def test_startup_applies_a_stored_setting_over_the_env_value(
    db_session, restore_config
):
    """The regression itself: save, 'restart', and the value is still in force."""
    import main
    from src.settings.database.db_operations.app_setting_repository import (
        AppSettingRepository,
    )
    from src.settings.services.settings_service import SettingsService

    stored_client_id = "1100555444"
    service = SettingsService(AppSettingRepository(db_session))
    await service.save(
        client_id=stored_client_id, access_token=None, synthetic_feed=True
    )
    await db_session.commit()

    # Simulate the restart: throw the overlay away, leaving only .env.
    config = config_utils.load_config()
    config.setdefault("dhan", {})["client_id"] = "from-dot-env"
    assert config_utils.get_property_value(KEY_CLIENT_ID, "") == "from-dot-env"

    await main._apply_stored_settings()

    assert config_utils.get_property_value(KEY_CLIENT_ID, "") == stored_client_id


async def test_the_feed_sees_the_stored_credentials_not_the_env_ones(
    db_session, restore_config
):
    """What the bug actually cost: the feed using the wrong credentials.

    DhanFeedClient.credentials() is the call site that matters -- it is what
    builds the upstream URL.
    """
    import main
    from src.market.services.dhan_feed_client import DhanFeedClient
    from src.settings.database.db_operations.app_setting_repository import (
        AppSettingRepository,
    )
    from src.settings.services.settings_service import SettingsService

    await SettingsService(AppSettingRepository(db_session)).save(
        client_id="1100333222",
        access_token="stored-token-value-x",
        synthetic_feed=False,
    )
    await db_session.commit()

    config = config_utils.load_config()
    config.setdefault("dhan", {})["client_id"] = "from-dot-env"

    await main._apply_stored_settings()

    client_id, _token = DhanFeedClient.credentials()
    assert client_id == "1100333222"


async def test_a_failure_to_apply_settings_does_not_stop_the_application(monkeypatch):
    """Booting is more important than the overlay. It must log, not raise."""
    import main

    def exploding_scope(*args, **kwargs):
        raise RuntimeError("the database is unreachable")

    monkeypatch.setattr(
        "src.database.session.session_scope", exploding_scope, raising=True
    )

    # Must not raise.
    await main._apply_stored_settings()


def test_stored_settings_are_applied_before_the_feed_is_started():
    """Ordering, asserted on the source.

    Applying the settings after the feed has started would connect with the
    .env credentials and pick the stored ones up only at the next save -- the
    original bug, moved rather than fixed.
    """
    import main

    source = inspect.getsource(main.lifespan)

    apply_at = source.index("_apply_stored_settings()")
    feed_at = source.index("get_feed_manager().start()")

    assert apply_at < feed_at, (
        "lifespan must call _apply_stored_settings() before starting the feed"
    )


def test_the_lifespan_still_calls_it_at_all():
    """Guards against the call being dropped in a refactor.

    The failure mode is silent -- the application starts perfectly well on
    .env -- so nothing else would catch its removal.
    """
    import main

    assert "_apply_stored_settings" in inspect.getsource(main.lifespan)
