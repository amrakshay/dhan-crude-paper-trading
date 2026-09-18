"""Daily OHLCV bars, stored.

**This is not the thing the fetch-never-accumulate rule prohibits.** Root
`CLAUDE.md` section 4 forbids accumulating TICKS to back a chart -- writing on
the hot path, which would break the performance contract, and rebuilding from a
stream what the vendor already serves correctly. A daily bar is a different
object with a different provenance: it is fetched whole from Dhan's
`/charts/historical` endpoint by a background job, once per symbol per day,
after the close. No tick ever touches this table, nothing here is derived from
the feed, and `apply_packet` does not know it exists. See root `CLAUDE.md`
section 4 for the full argument.

It exists because a momentum rotation needs 260+ sessions for ~500 symbols
available instantly at 09:15, and re-fetching that is a five-minute job at
Dhan's rate limits -- it cannot be done between waking up and the open.

Uniqueness is on `(exchange_segment, symbol, bar_date)` rather than on the
security id. Dhan's security ids change -- the universe file's own ids for HEG
and HFCL no longer match the master -- and keying the series on one would
silently start a second copy of a symbol's history the day its id moved. The
id is still stored, because it is what the next fetch is made with.
"""
from sqlalchemy import BigInteger, Column, Date, Index, String, UniqueConstraint

from src.database.base import Money, TimestampedModel


# Where a bar came from. A series must be traceable to one vendor: the research
# panel's own extended files splice Dhan with a second vendor, and two vendors'
# corporate-action policies can diverge on a future action.
SOURCE_DHAN = "dhan"
SOURCE_IMPORT = "import"


class DailyBar(TimestampedModel):
    """One symbol's OHLCV for one session."""

    __tablename__ = "daily_bars"

    # The ticker (or the index's symbol). Stable; the join key for indicators.
    symbol = Column(String(32), nullable=False, index=True)
    exchange_segment = Column(String(16), nullable=False)
    # Dhan's id AT THE TIME THIS BAR WAS WRITTEN. Data, not a key.
    security_id = Column(String(32), nullable=True)

    bar_date = Column(Date, nullable=False, index=True)

    open = Column(Money, nullable=False)
    high = Column(Money, nullable=False)
    low = Column(Money, nullable=False)
    close = Column(Money, nullable=False)
    # Index "volume" from Dhan is a traded-value proxy and is 0 on some
    # sessions; nullable so a missing figure is missing rather than zero.
    volume = Column(BigInteger, nullable=True)

    source = Column(String(16), nullable=False, default=SOURCE_DHAN)

    __table_args__ = (
        UniqueConstraint(
            "exchange_segment", "symbol", "bar_date", name="uq_daily_bars_series_date"
        ),
        # The read every indicator makes: one symbol's history, in order.
        Index("ix_daily_bars_symbol_date", "symbol", "bar_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<DailyBar(symbol={self.symbol}, date={self.bar_date}, "
            f"close={self.close})>"
        )
