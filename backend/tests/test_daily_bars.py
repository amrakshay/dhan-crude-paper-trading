"""Stored daily bars: the table, the importer and the nightly refresh.

The rule this feature sits next to is root `CLAUDE.md` section 4, "price
history is fetched, never accumulated". That rule forbids accumulating TICKS.
These bars are fetched whole from `/charts/historical` by a background job,
once per symbol per day. The tests here pin the properties that keep the
distinction true in practice: the series is keyed on the symbol rather than a
security id that moves, a re-fetch updates rather than duplicates, an unusable
bar is dropped rather than patched, and no credentials means no bars rather
than invented ones.
"""
import textwrap
from datetime import date, datetime
from decimal import Decimal

import pytest

from src.core.time_utils import IST
from src.daily_bars.database.db_models.daily_bar_model import (
    SOURCE_DHAN,
    SOURCE_IMPORT,
)
from src.daily_bars.database.db_operations.daily_bar_repository import (
    DailyBarRepository,
)
from src.daily_bars.services.bar_import_service import (
    BarImportError,
    BarImportService,
    read_bar_file,
)
from src.daily_bars.services.daily_bar_service import (
    DailyBarRefreshError,
    DailyBarRefreshService,
    candle_to_row,
)
from src.market.services.dhan_charts_client import Candle


SEGMENT = "NSE_EQ"


def _row(symbol, day, close, **overrides):
    row = {
        "symbol": symbol,
        "exchange_segment": SEGMENT,
        "security_id": "438",
        "bar_date": day,
        "open": Decimal("100"),
        "high": Decimal("110"),
        "low": Decimal("90"),
        "close": Decimal(str(close)),
        "volume": 1_000,
        "source": SOURCE_DHAN,
    }
    row.update(overrides)
    return row


# --- the table --------------------------------------------------------------


async def test_history_comes_back_oldest_first(db_session):
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        [
            _row("BHEL", date(2026, 9, 16), 100),
            _row("BHEL", date(2026, 9, 14), 98),
            _row("BHEL", date(2026, 9, 15), 99),
        ]
    )

    bars = await repository.history("BHEL", SEGMENT)

    assert [bar.bar_date for bar in bars] == [
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
    ]


async def test_a_limited_history_takes_the_newest_bars_still_oldest_first(db_session):
    """A 200-day SMA of the OLDEST 200 sessions is the failure this prevents."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        [_row("BHEL", date(2026, 9, day), 100 + day) for day in range(1, 11)]
    )

    bars = await repository.history("BHEL", SEGMENT, limit=3)

    assert [bar.bar_date.day for bar in bars] == [8, 9, 10]


async def test_re_fetching_a_session_updates_it_rather_than_duplicating(db_session):
    """Dhan restates a series after a corporate action."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many([_row("BHEL", date(2026, 9, 16), 400)])

    counts = await repository.upsert_many([_row("BHEL", date(2026, 9, 16), 200)])

    bars = await repository.history("BHEL", SEGMENT)
    assert len(bars) == 1
    assert bars[0].close == Decimal("200.0000")
    assert counts["updated"] == 1 and counts["inserted"] == 0


async def test_the_same_session_twice_in_one_batch_inserts_once(db_session):
    repository = DailyBarRepository(db_session)

    counts = await repository.upsert_many(
        [_row("BHEL", date(2026, 9, 16), 100), _row("BHEL", date(2026, 9, 16), 100)]
    )

    assert counts["inserted"] == 1
    assert len(await repository.history("BHEL", SEGMENT)) == 1


async def test_a_series_is_keyed_on_the_symbol_not_the_security_id(db_session):
    """Dhan's ids move. HEG's and HFCL's already have.

    Keying on the id would start a second copy of a symbol's history the day
    its id changed, and nothing would report it.
    """
    repository = DailyBarRepository(db_session)
    await repository.upsert_many([_row("HFCL", date(2026, 9, 16), 209, security_id="21951")])
    await repository.upsert_many([_row("HFCL", date(2026, 9, 16), 209, security_id="21954")])

    bars = await repository.history("HFCL", SEGMENT)
    assert len(bars) == 1
    assert bars[0].security_id == "21954", "the newest fetch's id is recorded"


async def test_two_segments_may_share_a_security_id(db_session):
    """Id 13 is NIFTY in IDX_I and ABB in NSE_EQ; both must be storable."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        [
            _row("ABB", date(2026, 9, 16), 5000, security_id="13"),
            _row(
                "NIFTY",
                date(2026, 9, 16),
                23270.6,
                security_id="13",
                exchange_segment="IDX_I",
            ),
        ]
    )

    assert len(await repository.history("ABB", "NSE_EQ")) == 1
    assert len(await repository.history("NIFTY", "IDX_I")) == 1


async def test_the_trading_calendar_is_the_indexs_own_dates(db_session):
    """No holiday list to maintain: a date NSE published a bar for, it traded."""
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        [
            _row("NIFTY", date(2026, 9, 14), 1, exchange_segment="IDX_I"),
            _row("NIFTY", date(2026, 9, 16), 1, exchange_segment="IDX_I"),
            _row("NIFTY", date(2026, 9, 17), 1, exchange_segment="IDX_I"),
        ]
    )

    dates = await repository.trading_dates("NIFTY", "IDX_I")

    assert dates == [date(2026, 9, 14), date(2026, 9, 16), date(2026, 9, 17)]
    assert date(2026, 9, 15) not in dates


async def test_latest_dates_answers_many_symbols_in_one_query(db_session):
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        [
            _row("BHEL", date(2026, 9, 16), 100),
            _row("BHEL", date(2026, 9, 17), 101),
            _row("HFCL", date(2026, 9, 15), 200),
        ]
    )

    latest = await repository.latest_dates(SEGMENT, ["BHEL", "HFCL", "ABSENT"])

    assert latest == {"BHEL": date(2026, 9, 17), "HFCL": date(2026, 9, 15)}


# --- the importer -----------------------------------------------------------


@pytest.fixture
def panel_file(tmp_path):
    path = tmp_path / "BHEL.csv"
    path.write_text(
        textwrap.dedent(
            """\
            date,open,high,low,close,volume
            2026-07-13,392.75,412.0,389.6,409.1,14286194.0
            2026-07-14,408.15,409.0,401.0,403.9,12494044.0
            """
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_the_panels_row_shape_is_read_as_published(panel_file):
    rows, warnings, read = read_bar_file(
        str(panel_file / "BHEL.csv"), "BHEL", SEGMENT, "438"
    )

    assert read == 2 and not warnings
    assert rows[0]["bar_date"] == date(2026, 7, 13)
    assert rows[0]["close"] == Decimal("409.1")
    assert rows[0]["volume"] == 14_286_194, "volume is published as a float"
    assert rows[0]["source"] == SOURCE_IMPORT, "an imported bar says so"


def test_an_unusable_row_is_reported_not_patched(tmp_path):
    path = tmp_path / "BROKEN.csv"
    path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-07-13,392.75,412.0,389.6,409.1,100\n"
        "not-a-date,1,2,3,4,5\n"
        "2026-07-15,1,0,3,4,5\n"          # zero high
        "2026-07-16,10,5,9,7,5\n",        # high below low
        encoding="utf-8",
    )

    rows, warnings, read = read_bar_file(str(path), "BROKEN", SEGMENT, None)

    assert read == 4
    assert len(rows) == 1
    assert len(warnings) == 3


def test_a_file_without_the_expected_columns_is_fatal(tmp_path):
    path = tmp_path / "WRONG.csv"
    path.write_text("day,price\n2026-07-13,100\n", encoding="utf-8")

    with pytest.raises(BarImportError, match="missing column"):
        read_bar_file(str(path), "WRONG", SEGMENT, None)


async def test_a_missing_symbol_file_is_reported_not_silently_skipped(
    db_session, panel_file
):
    service = BarImportService(DailyBarRepository(db_session))

    result = await service.import_symbols(
        str(panel_file),
        [("BHEL", SEGMENT, "438", None), ("NOSUCH", SEGMENT, "1", None)],
    )

    assert [one.symbol for one in result.symbols] == ["BHEL"]
    assert result.missing_files == ["NOSUCH.csv"]


async def test_the_import_is_idempotent(db_session, panel_file):
    service = BarImportService(DailyBarRepository(db_session))
    wanted = [("BHEL", SEGMENT, "438", None)]

    first = await service.import_symbols(str(panel_file), wanted)
    second = await service.import_symbols(str(panel_file), wanted)

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.symbols[0].unchanged == 2


async def test_the_index_file_may_be_named_differently_from_the_symbol(
    db_session, tmp_path
):
    """The panel calls it NIFTY50.csv; the symbol is NIFTY."""
    (tmp_path / "NIFTY50.csv").write_text(
        "date,open,high,low,close,volume\n2026-09-17,23195.25,23363.55,23193.65,23270.6,0\n",
        encoding="utf-8",
    )
    service = BarImportService(DailyBarRepository(db_session))

    await service.import_symbols(
        str(tmp_path), [("NIFTY", "IDX_I", "13", "NIFTY50.csv")]
    )

    bars = await DailyBarRepository(db_session).history("NIFTY", "IDX_I")
    assert len(bars) == 1
    assert bars[0].close == Decimal("23270.6000")


# --- the nightly refresh ----------------------------------------------------


def test_a_candles_date_is_resolved_in_ist_not_utc():
    """A session Dhan stamps 18:30Z is the SAME IST day, not the next one."""
    stamp = int(datetime(2026, 9, 17, 0, 0, tzinfo=IST).timestamp())
    row = candle_to_row(
        Candle(time=stamp, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0),
        "NIFTY",
        "IDX_I",
        "13",
    )

    assert row["bar_date"] == date(2026, 9, 17)


def test_a_candle_with_a_non_positive_price_is_dropped():
    stamp = int(datetime(2026, 9, 17, 0, 0, tzinfo=IST).timestamp())

    assert candle_to_row(
        Candle(time=stamp, open=0.0, high=2.0, low=0.5, close=1.5), "X", SEGMENT, "1"
    ) is None
    assert candle_to_row(
        Candle(time=stamp, open=1.0, high=0.4, low=0.5, close=1.5), "X", SEGMENT, "1"
    ) is None


def test_a_candle_with_no_volume_keeps_a_missing_volume():
    """Dhan reports no volume on some index sessions. Missing is not zero."""
    stamp = int(datetime(2026, 9, 17, 0, 0, tzinfo=IST).timestamp())
    row = candle_to_row(
        Candle(time=stamp, open=1.0, high=2.0, low=0.5, close=1.5, volume=None),
        "NIFTY",
        "IDX_I",
        "13",
    )

    assert row["volume"] is None


class _NoCredentialsClient:
    def has_credentials(self):
        return False


async def test_no_credentials_means_no_bars_and_never_invented_ones(db_session):
    """There is no synthetic fallback on this path at all.

    A fabricated close on the chart is labelled and looked at; a fabricated
    close here goes straight into a trading decision.
    """
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )
    from src.strategies.services.strategy_registry import get_strategy_registry

    service = DailyBarRefreshService(
        DailyBarRepository(db_session),
        InstrumentRepository(db_session),
        client=_NoCredentialsClient(),
    )

    with pytest.raises(DailyBarRefreshError, match="credentials"):
        await service.refresh_strategy(
            get_strategy_registry().require("nse-swing-momentum")
        )


class _RecordingClient:
    """Serves a fixed series and records every request it was asked for."""

    def __init__(self, candles):
        self.candles = candles
        self.requests = []

    def has_credentials(self):
        return True

    async def fetch_daily(self, **kwargs):
        self.requests.append(kwargs)
        return list(self.candles)


async def _seed_instruments(db_session, symbols):
    from src.core.time_utils import utc_now
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )

    await InstrumentRepository(db_session).upsert_many(
        [
            {
                "security_id": str(1000 + index),
                "exchange_id": "NSE",
                "exchange_segment": SEGMENT,
                "segment_code": 1,
                "instrument_type": "EQUITY",
                "underlying_symbol": symbol,
                "underlying_scrip": None,
                "trading_symbol": symbol,
                "display_name": symbol,
                "expiry_date": None,
                "strike_price": None,
                "option_type": None,
                "lot_size": 1,
                "tick_size": Decimal("0.05"),
                "is_active": True,
                "refreshed_at": utc_now(),
            }
            for index, symbol in enumerate(symbols)
        ]
    )


async def test_the_refresh_asks_only_for_what_is_missing(db_session, monkeypatch):
    """A symbol already current is skipped; a stale one is fetched from its
    last stored session, so a restated bar is picked up."""
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )
    from src.strategies.services.strategy_definition import build_definition

    await _seed_instruments(db_session, ["BHEL", "HFCL"])
    repository = DailyBarRepository(db_session)
    await repository.upsert_many(
        [
            _row("BHEL", date(2026, 9, 17), 429, security_id="1000"),
            _row("HFCL", date(2026, 9, 10), 209, security_id="1001"),
        ]
    )

    stamp = int(datetime(2026, 9, 17, 0, 0, tzinfo=IST).timestamp())
    client = _RecordingClient(
        [Candle(time=stamp, open=1.0, high=2.0, low=0.5, close=1.5, volume=5.0)]
    )
    monkeypatch.setattr(
        DailyBarRefreshService, "_request_delay", staticmethod(lambda: 0.0)
    )

    definition = build_definition(
        {
            "key": "two-name-rotation",
            "underlying": {
                "symbol": "TWO",
                "exchange_segment": SEGMENT,
                "exchange_segment_code": 1,
                "exchange_id": "NSE",
            },
            "instrument_sets": [
                {
                    "instrument_type": "EQUITY",
                    "exchange_segment": SEGMENT,
                    "exchange_segment_code": 1,
                    "symbols": ["BHEL", "HFCL"],
                    "lot_size_source": "master",
                }
            ],
            "market_hours": {"open": "09:15", "close": "15:30"},
            "subscription": {"kind": "positions"},
        },
        source="two.yaml",
    )

    service = DailyBarRefreshService(
        repository, InstrumentRepository(db_session), client=client
    )
    result = await service.refresh_strategy(definition, as_of=date(2026, 9, 17))

    by_symbol = {one.symbol: one for one in result.symbols}
    assert by_symbol["BHEL"].skipped_reason == "already current"
    assert by_symbol["HFCL"].skipped_reason is None
    assert [request["securityId"] if "securityId" in request else request["security_id"]
            for request in client.requests] == ["1001"]
    # Re-requests the last stored session too, so a restatement is picked up.
    assert client.requests[0]["from_date"].date() == date(2026, 9, 9)


async def test_one_failing_symbol_does_not_end_the_run(db_session, monkeypatch):
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )
    from src.market.services.dhan_charts_client import ChartsError
    from src.strategies.services.strategy_definition import build_definition

    await _seed_instruments(db_session, ["BHEL", "HFCL"])
    monkeypatch.setattr(
        DailyBarRefreshService, "_request_delay", staticmethod(lambda: 0.0)
    )

    stamp = int(datetime(2026, 9, 17, 0, 0, tzinfo=IST).timestamp())
    good = Candle(time=stamp, open=1.0, high=2.0, low=0.5, close=1.5, volume=5.0)

    class _FlakyClient(_RecordingClient):
        async def fetch_daily(self, **kwargs):
            self.requests.append(kwargs)
            if kwargs["security_id"] == "1000":
                raise ChartsError("DH-901 Invalid_Authentication")
            return [good]

    definition = build_definition(
        {
            "key": "two-name-rotation",
            "underlying": {
                "symbol": "TWO",
                "exchange_segment": SEGMENT,
                "exchange_segment_code": 1,
                "exchange_id": "NSE",
            },
            "instrument_sets": [
                {
                    "instrument_type": "EQUITY",
                    "exchange_segment": SEGMENT,
                    "exchange_segment_code": 1,
                    "symbols": ["BHEL", "HFCL"],
                    "lot_size_source": "master",
                }
            ],
            "market_hours": {"open": "09:15", "close": "15:30"},
            "subscription": {"kind": "positions"},
        },
        source="two.yaml",
    )

    service = DailyBarRefreshService(
        DailyBarRepository(db_session),
        InstrumentRepository(db_session),
        client=_FlakyClient([good]),
    )
    result = await service.refresh_strategy(definition, as_of=date(2026, 9, 17))

    assert len(result.failed) == 1
    assert result.failed[0].symbol == "BHEL"
    assert len(result.refreshed) == 1
    assert await DailyBarRepository(db_session).count_for("HFCL", SEGMENT) == 1


async def test_a_symbol_with_no_instrument_row_is_reported_not_guessed(
    db_session, monkeypatch
):
    from src.instruments.database.db_operations.instrument_repository import (
        InstrumentRepository,
    )
    from src.strategies.services.strategy_definition import build_definition

    monkeypatch.setattr(
        DailyBarRefreshService, "_request_delay", staticmethod(lambda: 0.0)
    )
    definition = build_definition(
        {
            "key": "one-name",
            "underlying": {
                "symbol": "ONE",
                "exchange_segment": SEGMENT,
                "exchange_segment_code": 1,
                "exchange_id": "NSE",
            },
            "instrument_sets": [
                {
                    "instrument_type": "EQUITY",
                    "exchange_segment": SEGMENT,
                    "exchange_segment_code": 1,
                    "symbols": ["JBCHEPHARM"],
                    "lot_size_source": "master",
                }
            ],
            "market_hours": {"open": "09:15", "close": "15:30"},
            "subscription": {"kind": "positions"},
        },
        source="one.yaml",
    )

    service = DailyBarRefreshService(
        DailyBarRepository(db_session),
        InstrumentRepository(db_session),
        client=_RecordingClient([]),
    )
    result = await service.refresh_strategy(definition, as_of=date(2026, 9, 17))

    assert [one.symbol for one in result.unresolved] == ["JBCHEPHARM"]
    assert result.as_dict()["symbolsUnresolved"] == 1
