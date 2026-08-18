import pytest

from src.api import routes


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
async def test_chat_uses_agent_contract_without_network(client, monkeypatch):
    async def fake_agent(message, project_id, allowed_external_ids=None):
        assert message == "compare areas"
        assert project_id == "P-1"
        return "## Result", ["compare_areas"], [{"source": "PostgreSQL", "tool": "compare_areas"}], {"planner": {}}

    monkeypatch.setattr(routes, "run_advisory_agent", fake_agent)
    response = await client.post("/api/v1/chat?project_id=P-1", json={"message": "compare areas"})

    assert response.status_code == 200
    assert response.json()["response"] == "## Result"
    assert response.json()["tool_calls"] == ["compare_areas"]
    assert response.json()["resolved_project_id"] == "P-1"


@pytest.mark.asyncio
async def test_chat_returns_project_inferred_from_message(client, monkeypatch):
    async def fake_infer(message, allowed_external_ids=None):
        assert "Times City" in message
        return "prj_tmc"

    async def fake_agent(message, project_id, allowed_external_ids=None):
        assert project_id == "prj_tmc"
        return "## Times City", ["project_overview"], [{"source": "PostgreSQL", "tool": "project_overview"}], {}

    monkeypatch.setattr(routes, "_infer_project_id_from_message", fake_infer)
    monkeypatch.setattr(routes, "run_advisory_agent", fake_agent)

    response = await client.post("/api/v1/chat", json={"message": "Phân tích Vinhomes Times City"})

    assert response.status_code == 200
    assert response.json()["resolved_project_id"] == "prj_tmc"


@pytest.mark.asyncio
async def test_named_project_in_question_overrides_stale_ui_project(client, monkeypatch):
    async def fake_infer(message, allowed_external_ids=None):
        assert 'Ocean Park' in message
        return 'prj_op1'

    async def fake_agent(message, project_id, allowed_external_ids=None):
        assert project_id == 'prj_op1'
        return 'Ocean Park only', ['project_overview'], [], {}

    monkeypatch.setattr(routes, '_infer_project_id_from_message', fake_infer)
    monkeypatch.setattr(routes, 'run_advisory_agent', fake_agent)
    response = await client.post(
        '/api/v1/chat?project_id=prj_tmc',
        json={'message': 'Ocean Park con nhung can nao nen tu van truoc?'},
    )

    assert response.status_code == 200
    assert response.json()['resolved_project_id'] == 'prj_op1'


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_market_dashboard_uses_database_data(client):
    response = await client.get("/api/v1/market/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["project"]["data_mode"] == "database"
    assert data["phase"]["kind"] == "database"


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
