"""Read-only project/area API coverage.

Project and Area writes belong to ingestion. This module deliberately contains
no create/update/delete request tests; image writes are covered by the image
service and Settings routes.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, projects

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
_name = urlsplit(TEST_DATABASE_URL).path.lstrip("/") if TEST_DATABASE_URL else ""
_skip = "Không có database test" if not TEST_DATABASE_URL else ("Từ chối database không phải test" if not _name.endswith("_test") else "")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_skip), reason=_skip)]


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory, monkeypatch):
    monkeypatch.setattr("src.api.dashboard.get_session_factory", lambda: session_factory)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.delete(areas))
            await session.execute(sa.delete(projects))
    yield


async def _insert_project(session_factory, **overrides) -> dict:
    values = {
        "id": uuid.uuid4(), "name": "Pilot Q1", "launch_date": date(2026, 1, 1),
        "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "status": "active",
        "headline": "", "introduce": "", "cover_image_url": None,
        "external_id": "P-0001", "source_system": "mini_crm", "source_instance_id": "mini-crm-dev",
        "source_revision": 1, "source_updated_at": None, **overrides,
    }
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.insert(projects).values(**values))
    return {"project_id": str(values["id"]), **values}


async def _insert_area(session_factory, project_id: str, **overrides) -> dict:
    values = {
        "id": uuid.uuid4(), "project_id": uuid.UUID(project_id), "area_name": "A1", "unit_type": "2PN",
        "bedrooms": 2, "area_sqm": Decimal("75.5"), "total_units": 100,
        "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "status": "active",
        "headline": "", "introduce": "", "cover_image_url": None,
        "external_id": "A-0001", "source_system": "mini_crm", "source_instance_id": "mini-crm-dev",
        "source_revision": 1, **overrides,
    }
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.insert(areas).values(**values))
    return {"area_id": str(values["id"]), **values}


async def test_project_list_is_read_only_and_returns_image_fields(client, session_factory):
    project = await _insert_project(session_factory, name="Đọc lại được", cover_image_url="https://res.cloudinary.com/demo/p.jpg")
    response = await client.get("/api/v1/projects")

    assert response.status_code == 200
    assert response.json() == [{
        "project_id": project["project_id"], "name": "Đọc lại được", "launch_date": "2026-01-01",
        "status": "active", "headline": "", "introduce": "",
        "cover_image_url": "https://res.cloudinary.com/demo/p.jpg", "external_id": "P-0001", "source_revision": 1,
    }]


async def test_area_list_is_read_only_and_scoped_to_project(client, session_factory):
    project = await _insert_project(session_factory)
    area = await _insert_area(session_factory, project["project_id"])
    response = await client.get(f"/api/v1/areas?external_project_id={project['external_id']}")

    assert response.status_code == 200
    assert response.json()[0]["area_id"] == str(area["id"])
    assert response.json()[0]["cover_image_url"] is None


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
async def test_project_write_routes_are_removed(client, method):
    url = f"/api/v1/projects/{uuid.uuid4()}"
    response = await getattr(client, method)(url, json={}) if method != "delete" else await client.delete(url)
    assert response.status_code in (404, 405)


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
async def test_area_write_routes_are_removed(client, method):
    url = f"/api/v1/areas/{uuid.uuid4()}"
    response = await getattr(client, method)(url, json={}) if method != "delete" else await client.delete(url)
    assert response.status_code in (404, 405)
