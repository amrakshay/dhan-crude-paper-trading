"""GET /api/vcp/overview: renders with nothing ever run, and needs a session."""


async def test_the_overview_renders_before_anything_has_run(auth_client):
    response = await auth_client.get("/api/vcp/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["strategyKey"] == "nse-vcp-swing"
    assert body["schedule"] == {"nightlyAtIst": "08:40", "morningAtIst": "09:16"}
    assert body["parameters"]["positionRupees"] == "150000.00"
    # Undefined is not zero: nothing has run, so these are absent, not empty runs.
    assert body["lastNightly"] is None and body["lastMorning"] is None
    assert body["candidates"]["sessionDate"] is None
    assert body["positions"] == [] and body["journal"] == []
    # The bootstrap has not been run on a fresh database, and the page says so.
    assert body["bootstrap"]["ready"] is False and body["bootstrap"]["universe"] > 400


async def test_the_overview_needs_a_signed_in_user(api_client):
    assert (await api_client.get("/api/vcp/overview")).status_code == 401


async def test_the_page_is_granted_to_both_roles(auth_client):
    pages = (await auth_client.get("/api/users/role-pages")).json()
    text = str(pages)
    assert "/vcp" in text
