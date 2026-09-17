"""Trade notes: lifecycle, search, and linkage to orders."""
from datetime import date
from decimal import Decimal

import pytest

from src.core.time_utils import utc_now
from src.database.session import get_session_factory
from src.orders.database.db_models.order_model import Order

# Every row in this suite belongs to the one configured strategy module.
STRATEGY = "mcx-crude-options"


async def _seed_order(symbol="CRUDEOIL 17 SEP 6800 CALL", security_id="576375") -> int:
    session = get_session_factory()()
    try:
        order = Order(
            strategy_key=STRATEGY,
            client_order_id=f"note-test-{security_id}-{symbol}",
            security_id=security_id,
            trading_symbol=symbol,
            expiry_date=date(2026, 9, 17),
            strike_price=Decimal("6800"),
            option_type="CE",
            lot_size=100,
            side="BUY",
            order_type="MARKET",
            lots=1,
            quantity=100,
            status="FILLED",
            filled_quantity=100,
            average_fill_price=Decimal("50"),
            placed_at=utc_now(),
            last_event_at=utc_now(),
        )
        session.add(order)
        await session.commit()
        return order.id
    finally:
        await session.close()


async def test_notes_require_authentication(api_client):
    assert (await api_client.get("/api/notes")).status_code == 401
    assert (await api_client.post("/api/notes", json={"noteText": "x"})).status_code == 401


async def test_a_note_can_be_attached_to_an_order(auth_client):
    order_id = await _seed_order()

    response = await auth_client.post(
        "/api/notes", json={"orderId": order_id, "noteText": "Paid up for thin depth."}
    )
    body = response.json()

    assert response.status_code == 201
    assert body["orderId"] == order_id
    assert body["noteText"] == "Paid up for thin depth."
    assert body["notedAt"] is not None
    assert body["editedAt"] is None


async def test_a_note_inherits_the_contract_from_its_order(auth_client):
    """Denormalised so the note stays searchable by contract after the security
    id is retired from the instrument master."""
    order_id = await _seed_order()

    response = await auth_client.post(
        "/api/notes", json={"orderId": order_id, "noteText": "note"}
    )
    body = response.json()

    assert body["tradingSymbol"] == "CRUDEOIL 17 SEP 6800 CALL"
    assert body["securityId"] == "576375"


async def test_an_empty_note_is_rejected(auth_client):
    order_id = await _seed_order()

    response = await auth_client.post(
        "/api/notes", json={"orderId": order_id, "noteText": "   "}
    )
    assert response.status_code in (400, 422)


async def test_a_note_must_be_attached_to_something(auth_client):
    response = await auth_client.post("/api/notes", json={"noteText": "floating note"})
    assert response.status_code == 400


async def test_a_note_on_an_unknown_order_is_rejected(auth_client):
    response = await auth_client.post(
        "/api/notes", json={"orderId": 999999, "noteText": "note"}
    )
    assert response.status_code == 400


async def test_a_note_can_be_edited_and_records_the_edit(auth_client):
    order_id = await _seed_order()
    created = await auth_client.post(
        "/api/notes", json={"orderId": order_id, "noteText": "first take"}
    )
    note_id = created.json()["id"]

    response = await auth_client.put(
        f"/api/notes/{note_id}", json={"noteText": "revised take"}
    )
    body = response.json()

    assert body["noteText"] == "revised take"
    assert body["editedAt"] is not None
    assert body["notedAt"] == created.json()["notedAt"], "the original stamp is kept"


async def test_editing_an_unknown_note_is_404(auth_client):
    response = await auth_client.put("/api/notes/999999", json={"noteText": "x"})
    assert response.status_code == 404


async def test_a_note_can_be_deleted(auth_client):
    order_id = await _seed_order()
    note_id = (
        await auth_client.post("/api/notes", json={"orderId": order_id, "noteText": "x"})
    ).json()["id"]

    assert (await auth_client.delete(f"/api/notes/{note_id}")).status_code == 200
    assert (await auth_client.get("/api/notes")).json()["total"] == 0


async def test_notes_are_searchable_by_text(auth_client):
    order_id = await _seed_order()
    await auth_client.post(
        "/api/notes", json={"orderId": order_id, "noteText": "slippage was brutal"}
    )
    await auth_client.post(
        "/api/notes", json={"orderId": order_id, "noteText": "clean entry at the touch"}
    )

    response = await auth_client.get("/api/notes?q=slippage")
    body = response.json()

    assert body["total"] == 1
    assert "slippage" in body["notes"][0]["noteText"]


async def test_note_search_is_case_insensitive(auth_client):
    order_id = await _seed_order()
    await auth_client.post(
        "/api/notes", json={"orderId": order_id, "noteText": "Slippage was brutal"}
    )

    assert (await auth_client.get("/api/notes?q=SLIPPAGE")).json()["total"] == 1


async def test_notes_are_searchable_by_contract(auth_client):
    """Searching the contract is how the P&L view finds notes for a strike."""
    call_order = await _seed_order("CRUDEOIL 17 SEP 6800 CALL", "111")
    put_order = await _seed_order("CRUDEOIL 17 SEP 6900 PUT", "222")
    await auth_client.post("/api/notes", json={"orderId": call_order, "noteText": "a"})
    await auth_client.post("/api/notes", json={"orderId": put_order, "noteText": "b"})

    response = await auth_client.get("/api/notes?q=6900 PUT")

    assert response.json()["total"] == 1
    assert response.json()["notes"][0]["tradingSymbol"] == "CRUDEOIL 17 SEP 6900 PUT"


async def test_notes_can_be_filtered_by_order(auth_client):
    first = await _seed_order("A", "111")
    second = await _seed_order("B", "222")
    await auth_client.post("/api/notes", json={"orderId": first, "noteText": "one"})
    await auth_client.post("/api/notes", json={"orderId": second, "noteText": "two"})

    response = await auth_client.get(f"/api/notes?orderId={first}")

    assert response.json()["total"] == 1
    assert response.json()["notes"][0]["noteText"] == "one"


async def test_notes_are_returned_newest_first(auth_client):
    order_id = await _seed_order()
    await auth_client.post("/api/notes", json={"orderId": order_id, "noteText": "older"})
    await auth_client.post("/api/notes", json={"orderId": order_id, "noteText": "newer"})

    notes = (await auth_client.get("/api/notes")).json()["notes"]

    assert notes[0]["noteText"] == "newer"
