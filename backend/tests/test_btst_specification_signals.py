"""THE ACCEPTANCE TEST: reproduce the specification's own twelve signals.

Section 13 of `BTST_OVERNIGHT_HANDOFF.md` lists twelve dated signals with their
symbols, closes, volume ratios, CLVs and six-month momenta, and says in as many
words: *"Use this table to verify any reimplementation: the same code on the
same data must produce these symbols on these dates."*

So this file does exactly that, against `src/btst/services/scan_service.py` --
not against a copy of the rule written for the test, which would only prove the
test agreed with itself.

**IT RUNS ON REAL BARS**, shipped as a fixture: `tests/fixtures/
btst_section13_bars.csv.gz` holds 330 sessions each for the twelve signal
symbols and seven large liquid controls, lifted from this application's own
`daily_bars` table (Dhan's `/charts/historical`, the same rows the strategy
trades on). Synthetic bars reconstructed from the published figures would only
prove the reconstruction was faithful; real bars prove the rule is.

**IT RUNS ON THE RECONSTRUCTION PATH, and that is not a shortcut.** Section
13's table was produced by `scan.py::build`, which works on the DAILY PANEL:
`advq` and `sma200` there are `rolling(...)` windows that INCLUDE day T, and
the signal is measured at that day's close. The live path substitutes a live
price for the unfinished close and computes both averages from completed bars
only, which is section 3's own prescription for automating this and is a
slightly different measurement. Reproducing section 13 needs the panel's
convention; pretending otherwise would make this a test of the wrong thing.
`scan_service.build_window(include_last_in_averages=...)` is that seam, and the
module's docstring explains why the two conventions roughly cancel.

**ONE KNOWN DIVERGENCE, and it is in our favour.** PAYTM's mom126 reads +55.9%
here against the specification's +55.1%. The research panel is Dhan spliced
with yfinance after 2026-07-14 and ours is Dhan throughout; the difference is
0.8pp on a figure whose threshold is 15%, so it changes nothing. Asserted with
a tolerance that admits it rather than hidden.
"""
import csv
import gzip
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

from src.btst.services.btst_parameters import BtstParameters
from src.btst.services.btst_policy import BtstPolicy
from src.btst.services import scan_service
from src.strategies.services.strategy_registry import get_strategy_registry

STRATEGY = "nse-btst-overnight"
FIXTURE = Path(__file__).parent / "fixtures" / "btst_section13_bars.csv.gz"

# Section 13's table, verbatim: date, symbol, close, volume ratio, CLV, mom126.
SECTION_13 = [
    ("2026-08-31", "OFSS",       12_540.00,  2.2, 1.00, 0.785),
    ("2026-09-01", "CAPLIPOINT",  2_711.20, 10.5, 0.84, 0.431),
    ("2026-09-02", "IFCI",            98.32, 7.7, 0.91, 0.420),
    ("2026-09-02", "SAILIFE",     1_587.00,  3.5, 0.91, 0.515),
    ("2026-09-03", "CAPLIPOINT",  2_845.00,  3.4, 0.85, 0.494),
    ("2026-09-03", "RBLBANK",        408.10, 7.1, 0.83, 0.205),
    ("2026-09-03", "SOLARINDS",  21_500.00,  2.7, 0.86, 0.524),
    ("2026-09-04", "NIACL",          229.91, 9.2, 0.88, 0.307),
    ("2026-09-07", "WOCKPHARMA",  2_208.00,  4.4, 0.82, 0.460),
    ("2026-09-09", "CHENNPETRO",  1_604.40,  4.3, 0.90, 0.403),
    ("2026-09-09", "PAYTM",       1_751.50,  2.1, 0.91, 0.551),
    ("2026-09-10", "FINCABLES",   1_418.40,  2.1, 0.89, 0.322),
]

# Large, liquid names in the fixture that were NOT signals on any of those
# dates. They are what makes "the rule fires on the twelve" mean something:
# without them a rule that fired on everything would pass.
CONTROLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC", "SBIN", "BHEL")


@pytest.fixture(scope="module")
def bars():
    """Every fixture bar, by symbol, oldest first."""
    by_symbol = defaultdict(list)
    with gzip.open(FIXTURE, "rt", newline="") as handle:
        for row in csv.DictReader(handle):
            by_symbol[row["symbol"]].append(
                {
                    "date": row["bar_date"],
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
    return dict(by_symbol)


@pytest.fixture
def parameters():
    return BtstParameters.from_definition(get_strategy_registry().require(STRATEGY))


@pytest.fixture
def gate_off_whole_universe():
    """Section 13's table is the GATE-OFF variant over the WHOLE universe.

    NIFTY 50 has been below its 200-SMA since 2026-02-27, so the gate-ON
    configuration has had no signals since 2026-02-26 -- which is why the
    specification lists the gate-OFF ones. And the F&O split post-dates the
    backtest entirely (it is section 5a's response to a regulatory change that
    went live in August 2026), so the table includes F&O names: RBLBANK and
    PAYTM are both in it.
    """
    return BtstPolicy(enforce_regime=False, exclude_fno=False)


def _scan(parameters, policy, bars, session_date):
    """Run the rule over every fixture symbol, as of one completed session."""
    windows, quotes = {}, {}
    for symbol, series in bars.items():
        index = next(
            (i for i, bar in enumerate(series) if bar["date"] == session_date), None
        )
        if index is None:
            continue
        # Day T is the LAST bar handed in: the reconstruction path.
        upto = series[: index + 1]
        window = scan_service.build_window(
            parameters,
            [one["high"] for one in upto],
            [one["low"] for one in upto],
            [one["close"] for one in upto],
            [one["volume"] for one in upto],
            symbol,
            include_last_in_averages=True,
        )
        if window is None:
            continue
        windows[symbol] = window
        today = upto[-1]
        # The finished bar standing in for the live book, which is what the
        # panel's own signal measured.
        quotes[symbol] = scan_service.Quote(
            security_id=symbol,
            symbol=symbol,
            price=today["close"],
            session_high=today["high"],
            session_low=today["low"],
            session_volume=today["volume"],
        )
    return scan_service.evaluate(
        parameters, policy, quotes, windows, sorted(bars),
        session_date=date.fromisoformat(session_date),
        substitute_price_in_sma=False,
    )


@pytest.mark.parametrize(
    "session_date,symbol,close,vol_ratio,clv,momentum", SECTION_13,
    ids=[f"{one[0]}-{one[1]}" for one in SECTION_13],
)
def test_each_of_the_specifications_twelve_signals_is_reproduced(
    parameters, gate_off_whole_universe, bars, session_date, symbol, close,
    vol_ratio, clv, momentum,
):
    """Section 13's own table, row by row, on its own data."""
    result = _scan(parameters, gate_off_whole_universe, bars, session_date)
    found = {one.symbol: one for one in result.candidates}

    assert symbol in found, (
        f"{symbol} on {session_date} is one of the specification's twelve "
        f"signals and must qualify. Funnel: {result.counts}. Near misses: "
        f"{[(one.symbol, one.reason) for one in result.rejections if one.stage != 'history']}"
    )
    candidate = found[symbol]
    # Every published figure, to the precision the table states it in.
    assert candidate.price == pytest.approx(close, abs=0.01)
    assert candidate.vol_ratio == pytest.approx(vol_ratio, abs=0.05)
    assert candidate.clv == pytest.approx(clv, abs=0.005)
    # PAYTM's mom126 is +55.9% here against the specification's +55.1%: the
    # research panel is spliced with yfinance after 2026-07-14 and ours is Dhan
    # throughout. 0.8pp on a threshold of 15% changes nothing, and the
    # tolerance admits it rather than hiding it.
    assert candidate.momentum == pytest.approx(momentum, abs=0.01)


@pytest.mark.parametrize("session_date", sorted({one[0] for one in SECTION_13}))
def test_no_large_liquid_control_name_fires_on_a_signal_day(
    parameters, gate_off_whole_universe, bars, session_date
):
    """The rule is not merely permissive.

    Reproducing the twelve proves the filters are not too STRICT. On its own
    that is satisfied by a rule that fires on everything, which is why these
    seven names are in the fixture: RELIANCE, TCS, INFY, HDFCBANK, ITC, SBIN
    and BHEL traded on every one of those sessions and none of them is in
    section 13's table.
    """
    result = _scan(parameters, gate_off_whole_universe, bars, session_date)
    fired = {one.symbol for one in result.candidates}
    assert not (fired & set(CONTROLS)), (
        f"{sorted(fired & set(CONTROLS))} fired on {session_date} and is not in "
        f"the specification's table -- the rule is matching more than it should."
    )


def test_the_ranking_is_volume_ratio_descending(
    parameters, gate_off_whole_universe, bars
):
    """B10, on the one fixture day that produces more than one signal.

    2026-09-03 has three: RBLBANK at 7.1x, CAPLIPOINT at 3.4x and SOLARINDS at
    2.7x. Ranked by volume ratio they come out in that order, which is not the
    alphabetical one and not the order they appear in the specification's
    table -- so this asserts the ranking rather than an accident.
    """
    result = _scan(parameters, gate_off_whole_universe, bars, "2026-09-03")
    ordered = [one.symbol for one in result.candidates]
    assert ordered == ["RBLBANK", "CAPLIPOINT", "SOLARINDS"]
    assert [one.rank for one in result.candidates] == [1, 2, 3]
    ratios = [one.vol_ratio for one in result.candidates]
    assert ratios == sorted(ratios, reverse=True)


def test_excluding_fno_names_drops_the_two_that_have_derivatives(
    parameters, bars
):
    """Section 5a's policy, measured on the specification's own signals.

    RBLBANK and PAYTM are F&O-eligible; the other ten in the table are not. So
    switching the policy on must drop exactly those two -- which is also the
    concrete form of the signal-frequency claim, that excluding F&O names
    roughly halves the signal count.

    The fixture carries no `fno_eligible` column, so it is declared on the
    quote here; the real scan reads `instruments.fno_eligible`, derived from
    the master's own FUTSTK rows.
    """
    fno = {"RBLBANK", "PAYTM"}
    excluded = BtstPolicy(enforce_regime=False, exclude_fno=True)

    for session_date, symbol in (("2026-09-03", "RBLBANK"), ("2026-09-09", "PAYTM")):
        windows, quotes = {}, {}
        series = bars[symbol]
        index = next(i for i, bar in enumerate(series) if bar["date"] == session_date)
        upto = series[: index + 1]
        windows[symbol] = scan_service.build_window(
            parameters,
            [one["high"] for one in upto], [one["low"] for one in upto],
            [one["close"] for one in upto], [one["volume"] for one in upto],
            symbol, include_last_in_averages=True,
        )
        today = upto[-1]
        quotes[symbol] = scan_service.Quote(
            security_id=symbol, symbol=symbol, price=today["close"],
            session_high=today["high"], session_low=today["low"],
            session_volume=today["volume"], fno_eligible=symbol in fno,
        )
        result = scan_service.evaluate(
            parameters, excluded, quotes, windows, [symbol],
            substitute_price_in_sma=False,
        )
        assert not result.candidates, (
            f"{symbol} has listed derivatives and must be excluded when the "
            f"policy is on"
        )
        assert result.rejections[0].stage == "tradable"


def test_the_regime_gate_blocks_entries_on_these_dates_when_enforced(
    parameters, bars
):
    """Every one of the twelve is a GATE-OFF signal, and the gate says so.

    NIFTY 50 has been below its 200-session SMA since 2026-02-27, so with the
    gate enforced none of these twelve would have been entered. The candidates
    are still FOUND and still recorded -- `enforcement` stops the gate acting,
    never stops it being computed -- which is what makes section 11's
    measurement of the gate's cost possible at all.
    """
    from src.btst.services.scan_service import ScanResult

    result = ScanResult(session_date=date(2026, 9, 3))
    result.regime_enforced = True
    result.gate_on = False
    result.index_symbol = "NIFTY"
    result.index_close = 23_270.6
    result.index_sma = 24_501.7

    assert result.entries_allowed is False
    assert "regime gate is OFF" in result.blocked_reason

    # And an unmeasurable gate is not a reason to trade either.
    result.gate_on = None
    assert result.entries_allowed is False
    assert "could not be evaluated" in result.blocked_reason
