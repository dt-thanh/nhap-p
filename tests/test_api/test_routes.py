import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_chat_empty_message(client):
    response = await client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_market_dashboard_uses_simulation_data(client):
    response = await client.get("/api/v1/market/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["project"]["data_mode"] == "simulation"
    assert data["phase"]["kind"] in {"idle", "booking", "sale"}


@pytest.mark.asyncio
async def test_phase_change_requires_confirmation(client):
    response = await client.post(
        "/api/v1/market/phase",
        json={"direction": "next", "confirmed": False, "actor": "Admin test"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "confirmation_required"


@pytest.mark.asyncio
async def test_scenario_requires_confirmation(client):
    response = await client.post(
        "/api/v1/market/scenarios/run",
        json={"scenario_id": "buying_wave", "intensity": 40, "confirmed": False, "actor": "Admin test"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_proposal_decision_requires_confirmation(client):
    response = await client.post(
        "/api/v1/market/proposals/proposal-discount-3pn/decision",
        json={"decision": "approved", "reason": "Kiểm tra", "confirmed": False, "actor": "Admin test"},
    )
    assert response.status_code == 409
