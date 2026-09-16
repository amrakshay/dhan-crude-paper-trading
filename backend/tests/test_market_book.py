"""In-memory book: merge semantics, coalescing and staleness."""
import pytest

from src.market.services.market_book import MarketBook


def _packet(security_id="565899", **overrides):
    fields = {"security_id": security_id, "segment": 5, "ltp": 6800.0, "volume": 100}
    fields.update(overrides)
    return fields


def test_apply_packet_creates_and_updates_a_row():
    book = MarketBook()

    book.apply_packet(_packet(ltp=6800.0))
    assert book.get("565899")["ltp"] == 6800.0

    book.apply_packet(_packet(ltp=6805.5))
    assert book.get("565899")["ltp"] == 6805.5
    assert book.size == 1, "same instrument must not create a second row"


def test_partial_packets_do_not_wipe_existing_fields():
    """An OI-only packet must not blank the LTP set by an earlier Full packet."""
    book = MarketBook()
    book.apply_packet(_packet(ltp=6800.0, volume=500, oi=7000))

    book.apply_packet({"security_id": "565899", "segment": 5, "oi": 7250})

    row = book.get("565899")
    assert row["oi"] == 7250
    assert row["ltp"] == 6800.0, "LTP must survive an OI-only update"
    assert row["volume"] == 500


def test_best_bid_ask_are_lifted_out_of_depth():
    book = MarketBook()
    depth = [
        (100, 200, 3, 4, 6799.0, 6801.0),
        (150, 250, 2, 3, 6798.0, 6802.0),
        (0, 0, 0, 0, 0.0, 0.0),
        (0, 0, 0, 0, 0.0, 0.0),
        (0, 0, 0, 0, 0.0, 0.0),
    ]

    book.apply_packet(_packet(depth=depth))

    row = book.get("565899")
    assert row["bid"] == 6799.0
    assert row["ask"] == 6801.0
    assert row["bidQty"] == 100
    assert row["askQty"] == 200


def test_drain_dirty_coalesces_repeated_ticks():
    """40 ticks in one broadcast window must be sent once, at the latest state."""
    book = MarketBook()
    for price in range(6800, 6840):
        book.apply_packet(_packet(ltp=float(price)))

    changed = book.drain_dirty()

    assert len(changed) == 1
    assert changed[0]["ltp"] == 6839.0
    assert book.tick_count == 40, "every tick still counted, only the send is coalesced"


def test_drain_dirty_clears_the_set():
    book = MarketBook()
    book.apply_packet(_packet())

    assert len(book.drain_dirty()) == 1
    assert book.drain_dirty() == [], "an unchanged book must broadcast nothing"


def test_drain_dirty_reports_each_changed_instrument_once():
    book = MarketBook()
    for _ in range(5):
        book.apply_packet(_packet("111"))
        book.apply_packet(_packet("222"))

    changed = book.drain_dirty()

    assert {row["securityId"] for row in changed} == {"111", "222"}
    assert len(changed) == 2


def test_register_instrument_attaches_contract_metadata():
    book = MarketBook()
    book.register_instrument(
        "576266",
        {"tradingSymbol": "CRUDEOIL 17 SEP 7000 CALL", "strikePrice": 7000.0, "optionType": "CE"},
    )

    book.apply_packet(_packet("576266", ltp=42.5))

    row = book.get("576266")
    assert row["ltp"] == 42.5
    assert row["optionType"] == "CE"
    assert row["strikePrice"] == 7000.0, "metadata must survive a tick"


def test_greeks_merge_without_touching_tick_fields():
    book = MarketBook()
    book.apply_packet(_packet("576266", ltp=42.5))

    book.merge_greeks("576266", {"iv": 0.31, "delta": 0.55, "theta": -1.2})

    row = book.get("576266")
    assert row["ltp"] == 42.5
    assert row["delta"] == 0.55
    assert row["greeksTs"] is not None


def test_greeks_for_an_unseen_instrument_create_a_row():
    """The poller can arrive before the first tick for a quiet strike."""
    book = MarketBook()
    book.merge_greeks("999", {"iv": 0.4})

    assert book.get("999")["iv"] == 0.4


def test_last_tick_age_reports_staleness():
    book = MarketBook()
    assert book.last_tick_age_ms() is None, "no ticks yet is not the same as fresh"

    book.apply_packet(_packet())
    assert 0 <= book.last_tick_age_ms() < 1000


def test_forget_removes_instruments_that_left_the_window():
    book = MarketBook()
    book.apply_packet(_packet("111"))
    book.apply_packet(_packet("222"))

    removed = book.forget(["111"])

    assert removed == 1
    assert book.get("111") is None
    assert book.get("222") is not None


def test_forgotten_instruments_do_not_reappear_in_a_drain():
    book = MarketBook()
    book.apply_packet(_packet("111"))
    book.forget(["111"])

    assert book.drain_dirty() == []


def test_apply_frame_counts_packets():
    book = MarketBook()
    frame = [(8, _packet("111")), (8, _packet("222")), (8, _packet("333"))]

    assert book.apply_frame(frame) == 3
    assert book.size == 3
