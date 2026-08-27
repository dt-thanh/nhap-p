"""Focused contract tests for the batch historical-ranking endpoint."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from src.api import ranking
from src.services.dashboard_auth import DashboardPrincipal

PRINCIPAL = DashboardPrincipal(role="admin", project_scope="ALL")


@pytest.mark.asyncio
async def test_batch_returns_valid_projects_and_skips_invalid_or_out_of_scope(monkeypatch):
    async def fake_get(*, external_project_id, as_of_date, principal):
        if external_project_id == "missing":
            raise HTTPException(status_code=404, detail="not found")
        if external_project_id == "outside":
            raise HTTPException(status_code=403, detail="out of scope")
        return {"external_project_id": external_project_id, "as_of_date": as_of_date}

    monkeypatch.setattr(ranking, "get_historical_ranking", fake_get)

    result = await ranking.get_historical_ranking_batch(
        project_ids=["valid-a", "missing", "outside", "valid-b"],
        as_of_date=None,
        principal=PRINCIPAL,
    )

    assert [row["external_project_id"] for row in result] == ["valid-a", "valid-b"]


@pytest.mark.asyncio
async def test_batch_rejects_empty_project_list():
    with pytest.raises(HTTPException) as raised:
        await ranking.get_historical_ranking_batch(project_ids=[], as_of_date=None, principal=PRINCIPAL)

    assert raised.value.status_code == 422
    assert raised.value.detail["error_code"] == "EMPTY_PROJECT_IDS"


@pytest.mark.asyncio
async def test_batch_rejects_future_as_of_date():
    with pytest.raises(HTTPException) as raised:
        await ranking.get_historical_ranking_batch(
            project_ids=["valid"],
            as_of_date=datetime.now(UTC) + timedelta(days=1),
            principal=PRINCIPAL,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail["error_code"] == "AS_OF_DATE_IN_FUTURE"


@pytest.mark.asyncio
async def test_batch_limits_concurrency_to_ten(monkeypatch):
    active = 0
    maximum = 0

    async def fake_get(*, external_project_id, as_of_date, principal):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return {"external_project_id": external_project_id}

    monkeypatch.setattr(ranking, "get_historical_ranking", fake_get)
    result = await ranking.get_historical_ranking_batch(
        project_ids=[f"project-{index}" for index in range(25)],
        as_of_date=None,
        principal=PRINCIPAL,
    )

    assert len(result) == 25
    assert maximum <= 10
