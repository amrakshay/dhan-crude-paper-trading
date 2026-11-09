"""The rules, on hand-built bars. Run from backend/:
    .venv/bin/python -m pytest ../experiments/sentiment-sector-vwap/tests -q
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

EXP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXP))

from svlib.book import Book  # noqa: E402
from svlib.costs import RateCard  # noqa: E402
from svlib.engine import (  # noqa: E402
    LONG, SHORT, WAIT_CROSS, WAIT_RETEST, Bar, Point, Setup, bar_stop_fill, ema_series,
    entry_features, excursions, pick_sector, pick_stocks, trail, vwap_series, with_indicators,
)
from svlib.scrape import Sector, parse_sectors, parse_sentiment  # noqa: E402

D = datetime(2026, 10, 7, 9, 15)


def bar(i, o, h, l, c, v=1000, day=D):
    return Bar(day + timedelta(minutes=5 * i), o, h, l, c, v)


def pt(i, ema, vwap, o=100, h=101, l=99, c=100):
    return Point(bar(i, o, h, l, c), ema, vwap)


# --- indicators ---------------------------------------------------------------
def test_ema_seeded_with_sma():
    e = ema_series([float(x) for x in range(1, 12)], 9)
    assert e[7] is None and e[8] == pytest.approx(5.0)
    assert e[9] == pytest.approx(0.2 * 10 + 0.8 * 5.0)


def test_vwap_resets_each_session():
    yday = D - timedelta(days=1)
    bars = [bar(0, 10, 10, 10, 10, 100, yday), bar(0, 20, 20, 20, 20, 100), bar(1, 30, 30, 30, 30, 300)]
    v = vwap_series(bars)
    assert v[0] == 10 and v[1] == 20          # reset, not (10+20)/2
    assert v[2] == pytest.approx((20 * 100 + 30 * 300) / 400)


# --- setups ---------------------------------------------------------------------
def test_fresh_cross_after_tracking_enters_with_candle_low_stop():
    pts = [pt(0, 99, 100), pt(1, 99.5, 100)]           # EMA below VWAP when picked up
    s = Setup.start("X", LONG, pts)
    assert s.mode == WAIT_CROSS
    pts.append(pt(2, 100.5, 100, l=98.7, c=101))       # crosses above
    sig = s.update(pts)
    assert [x.kind for x in sig] == ["CROSS"] and sig[0].stop == 98.7 and sig[0].price == 101


def test_cross_before_tracking_waits_for_retest():
    pts = [pt(0, 99, 100), pt(1, 101, 100)]            # already crossed on the last closed bar
    s = Setup.start("X", LONG, pts)
    assert s.mode == WAIT_RETEST
    pts.append(pt(2, 101.5, 100, l=100.5, c=102))     # does not touch VWAP
    assert s.update(pts) == []
    pts.append(pt(3, 101.6, 100.2, l=100.1, c=101))   # touches 100.2, closes above
    sig = s.update(pts)
    assert [x.kind for x in sig] == ["RETEST"] and sig[0].stop == 100.1


def test_close_through_vwap_is_not_a_retest_and_cross_back_cancels():
    pts = [pt(0, 101, 100)]
    s = Setup.start("X", LONG, pts)
    pts.append(pt(1, 100.8, 100.2, l=99.5, c=99.9))   # touched but CLOSED below VWAP
    assert s.update(pts) == []
    pts.append(pt(2, 99.9, 100.1))                     # EMA crosses back under
    s.update(pts)
    assert s.mode == WAIT_CROSS


def test_short_mirror():
    pts = [pt(0, 101, 100)]
    s = Setup.start("X", SHORT, pts)
    assert s.mode == WAIT_CROSS
    pts.append(pt(1, 99.5, 100, h=101.2, c=99))
    sig = s.update(pts)
    assert sig[0].kind == "CROSS" and sig[0].stop == 101.2


def test_bars_already_seen_are_not_reprocessed():
    pts = [pt(0, 99, 100)]
    s = Setup.start("X", LONG, pts)
    pts.append(pt(1, 101, 100))
    assert len(s.update(pts)) == 1
    assert s.update(pts) == []


# --- stops -----------------------------------------------------------------------
def test_trail_only_tightens():
    assert trail(LONG, 98, 99) == 99 and trail(LONG, 98, 97) == 98
    assert trail(SHORT, 102, 101) == 101 and trail(SHORT, 102, 103) == 102


def test_bar_stop_fill_is_gap_aware():
    assert bar_stop_fill(LONG, 98, bar(0, 100, 101, 98.5, 99)) is None
    assert bar_stop_fill(LONG, 98, bar(0, 100, 101, 97, 99)) == 98
    assert bar_stop_fill(LONG, 98, bar(0, 96, 97, 95, 96)) == 96      # gapped through: the open
    assert bar_stop_fill(SHORT, 102, bar(0, 104, 105, 103, 104)) == 104


# --- selection ---------------------------------------------------------------------
SECTORS = [Sector("METAL", -2.33, {"NATIONALUM": -4.74, "HINDALCO": -2.94, "ADANIENT": -3.87, "COAL": 0.5}),
           Sector("PSU BANK", 1.0, {"PNB": 2.69, "SBIN": -0.47}),
           Sector("NIFTY", 2.0, {"A": 1.0})]


def test_pick_sector_respects_side_and_exclusions():
    assert pick_sector(SECTORS, "BEARISH", {"NIFTY"}).name == "METAL"
    assert pick_sector(SECTORS, "BULLISH", {"NIFTY"}).name == "PSU BANK"
    assert pick_sector(SECTORS, "NEUTRAL", set()) is None


def test_pick_stocks_ranks_gainers_and_losers_together():
    assert [s for s, _ in pick_stocks(SECTORS[0], "BEARISH")] == ["NATIONALUM", "ADANIENT", "HINDALCO"]
    # a bullish sector with one gainer still gives the next-strongest names
    assert [s for s, _ in pick_stocks(SECTORS[1], "BULLISH")] == ["PNB", "SBIN"]


# --- page parsing (strings as the pages rendered them on 2026-10-07) ---------------
def test_parse_sentiment_expanded_and_collapsed():
    s = parse_sentiment("Market Sentiment (based on OI)\nBearish\nBearish market conditions\n70%\nPCR:\n0.71(-0.48)")
    assert (s.label, s.pct, s.pcr) == ("BEARISH", 70.0, 0.71)
    assert parse_sentiment("Market Sentiment Bullish · 62%").label == "BULLISH"
    assert parse_sentiment("Market Sentiment Neutral · %") is None       # not loaded yet


def test_parse_sectors():
    cards = [{"name": "METAL", "pct": "(-2.33%)",
              "rows": [[None, ["NATIONALUM", -4.74]], [["COALINDIA", 0.59], ["ADANIENT", -3.87]]]},
             {"name": "SENSEX", "pct": None, "rows": []}]
    out = parse_sectors(cards)
    assert len(out) == 1 and out[0].pct == -2.33
    assert out[0].stocks == {"NATIONALUM": -4.74, "COALINDIA": 0.59, "ADANIENT": -3.87}


# --- the book ------------------------------------------------------------------------
def test_short_trade_pnl_pays_slippage_and_charges(tmp_path):
    cfg = {"costs": {"slippage_pct_per_side": 0.0005}, "sizing": {"capital_per_trade": 100000}}
    day_dir = tmp_path / "2026-10-07"
    book = Book("plan", day_dir, cfg, RateCard.load(EXP / "conf" / "intraday_costs.yaml"))
    s = Setup.start("X", SHORT, [pt(0, 101, 100)])
    sig = s.update([pt(0, 101, 100), pt(1, 99.5, 100, h=101.2, c=99)])[0]
    t = book.enter(D, sig, 100.0, "1", "LIVE", {"angle_between_deg": 7.0})
    assert t["qty"] == 1000 and t["entry_price"] == pytest.approx(99.95)
    done = book.exit(D, "X", 98.0, "STOP", "LIVE")
    assert done["exit_price"] == pytest.approx(98.049)
    assert done["gross_pnl"] == pytest.approx((99.95 - 98.049) * 1000, abs=0.01)
    assert 0 < done["charges"] < 100 and done["net_pnl"] < done["gross_pnl"]
    assert done["angle_between_deg"] == 7.0 and (day_dir / "trades_plan.csv").exists()
    # a restart reloads the day
    again = Book("plan", day_dir, cfg, RateCard.load(EXP / "conf" / "intraday_costs.yaml"))
    assert len(again.closed) == 1 and again.trades_today == {"X": 1}


# --- data points -----------------------------------------------------------------------
def test_entry_features_signs_and_angle():
    yday = D - timedelta(days=1)
    bars = [bar(i, 100, 100.5, 99.5, 100, 1000, yday) for i in range(20)]
    bars += [bar(i, 100 - 0.1 * i, 100 - 0.1 * i + 0.3, 100 - 0.1 * i - 0.3, 100 - 0.1 * i - 0.1, 1000)
             for i in range(6)]
    pts = with_indicators(bars)
    f = entry_features(pts, len(pts) - 1, SHORT, stop=pts[-1].bar.high)
    assert f["ema_slope_pct"] < 0 and f["ema_deg"] < 0          # falling, signed as on the chart
    assert f["angle_between_deg"] == pytest.approx(abs(f["ema_deg"] - f["vwap_deg"]), abs=0.11)
    assert f["day_change_pct"] < 0 and f["from_open_pct"] < 0
    assert f["risk_pct"] > 0 and f["rel_volume_20"] == pytest.approx(1.0)


def test_excursions_pessimistic_and_held_to_close():
    bars = [bar(1, 100, 101, 99.5, 100.5), bar(2, 100.5, 102.2, 100, 102),   # +2.2R reached
            bar(3, 102, 102.5, 98.9, 99), bar(4, 99, 99.5, 98, 98.5)]         # then the stop (99)
    x = excursions(LONG, 100.0, 99.0, bars, exit_at=bars[1].start)
    assert x["mfe_r_in_trade"] == pytest.approx(2.2) and x["mae_r_in_trade"] == pytest.approx(0.5)
    assert x["reached_2r_before_stop"] and not x["reached_3r_before_stop"]
    assert x["r_if_held_to_1500"] == pytest.approx(-1.5) and x["mae_r_to_1500"] == pytest.approx(2.0)
    # a bar that touches both the stop and 1R counts as stopped first
    y = excursions(LONG, 100.0, 99.0, [bar(1, 100, 101.5, 98.5, 100)], None)
    assert not y["reached_1r_before_stop"]


def test_ema_trail_follows_the_ema_and_counterfactual_differs():
    pts = [pt(i, ema=100 + i, vwap=100, o=100.5 + i, h=101.5 + i, l=100 + i, c=101 + i) for i in range(1, 6)]
    from svlib.engine import simulate_trail, trail_level
    assert trail_level(pts[0], "ema9") == 101 and trail_level(pts[0], "vwap") == 100
    assert trail(LONG, 99, trail_level(pts[2], "ema9")) == 103
    # after 5 rising bars the EMA trail sits at 105, the VWAP trail at 100.
    # A drop to 103.5 (close 104) takes the EMA trail out at 105; the VWAP
    # trail survives and exits at the last close.
    pts.append(pt(6, ema=105.5, vwap=100.5, o=106, h=106, l=103.5, c=104))
    ema_r = simulate_trail(LONG, 100.0, 99.0, pts, "ema9")
    vwap_r = simulate_trail(LONG, 100.0, 99.0, pts, "vwap")
    assert ema_r == pytest.approx(5.0) and vwap_r == pytest.approx(4.0)


# --- Dhan market data (svlib.market) ----------------------------------------------------
from types import SimpleNamespace as _NS  # noqa: E402

from svlib import market  # noqa: E402

_RULE = {"window_points": 500, "pcr_strong_bull": 1.3, "pcr_bull": 1.05, "pcr_bear": 0.95,
         "pcr_strong_bear": 0.8, "flow_cut": 0.3, "nifty_move_pct": 0.3, "lean_at": 1, "very_at": 3}


def _leg(oi, prev):
    return _NS(oi=oi, previous_oi=prev)


def test_chain_reproduces_stockmojo_oct7_ratios():
    # Oct 7 card: call OI 6.5Cr, put 4.62Cr, call chg +3.38Cr, put chg +91.47L
    strikes = {22600.0: {"CE": _leg(65_000_000, 31_200_000), "PE": _leg(46_200_000, 37_053_000)}}
    t = market.chain_totals(strikes, 22603.05, 500)
    assert round(t.pcr, 2) == 0.71 and round(t.pcr_chg, 2) == 0.27
    assert t.flow < -0.3                                   # mostly calls written: bearish


def test_window_excludes_far_strikes():
    strikes = {22600.0: {"CE": _leg(10, 10), "PE": _leg(10, 10)},
               24000.0: {"CE": _leg(1000, 1000), "PE": _leg(0, 0)}}
    assert market.chain_totals(strikes, 22600, 500).pcr == 1.0
    assert market.chain_totals(strikes, 22600, None).pcr == pytest.approx(10 / 1010)


def test_sentiment_labels_follow_score():
    strikes = {22600.0: {"CE": _leg(65_000_000, 31_200_000), "PE": _leg(46_200_000, 37_053_000)}}
    sent, rec = market.dhan_sentiment(strikes, 22603.05, -0.76, _RULE)
    assert sent.label == "BEARISH" and rec["dhan_label"] == "Very Bearish" and rec["score"] == -4
    calm = {22600.0: {"CE": _leg(100, 100), "PE": _leg(100, 100)}}
    assert market.dhan_sentiment(calm, 22600, 0.1, _RULE)[0].label == "NEUTRAL"


def test_dhan_sectors_index_members_and_coverage():
    members = {"METAL": ["A", "B", "C"], "CHEMICALS": ["X", "Y"], "IT": ["P", "Q", "R", "S"]}
    ids = {"METAL": "31", "CHEMICALS": None, "IT": "29"}
    change = {"A": -2.0, "B": -1.0, "C": None, "X": 1.0, "Y": 3.0, "P": 1.0}
    out, skipped = market.dhan_sectors(members, ids, change, {"METAL": -1.5, "IT": 2.0})
    by = {s.name: s for s in out}
    assert by["METAL"].pct == -1.5 and by["METAL"].stocks == {"A": -2.0, "B": -1.0}
    assert by["CHEMICALS"].pct == 2.0                      # no index: members' average
    assert "IT" not in by and any(x.startswith("IT") for x in skipped)   # 1/4 priced


def test_day_change():
    assert market.day_change(101.0, 100.0) == pytest.approx(1.0)
    assert market.day_change(None, 100.0) is None and market.day_change(101.0, None) is None
