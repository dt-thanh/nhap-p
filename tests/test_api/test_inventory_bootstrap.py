"""Development Inventory bootstrap stays isolated and safe to repeat."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa

from src.models.tables import areas, deals, projects, units
from src.services.inventory_bootstrap import (
    AREA_EXTERNAL_ID,
    PROJECT_EXTERNAL_ID,
    SOURCE_INSTANCE_ID,
)


def _db_skip_reason() -> str:
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        return "No TEST_DATABASE_URL/DATABASE_URL configured"
    if not urlsplit(database_url).path.lstrip("/").endswith("_test"):
        return "Refusing to run outside a *_test database"
    return ""


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(_db_skip_reason()), reason=_db_skip_reason() or ""),
]


async def test_bootstrap_creates_one_isolated_demo_scope_and_is_idempotent(client, truncate_all, monkeypatch):
    """It may only create its reserved source namespace, never user rows."""
    from src.config import get_settings
    import src.db as db_module

    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.get_session_factory.cache_clear()

    first = await client.post("/api/v1/inventory/bootstrap-default")
    assert first.status_code == 200
    body = first.json()
    assert body["created"] is True
    assert body["project"]["external_id"] == PROJECT_EXTERNAL_ID
    assert body["project"]["name"] == "AbsorptionIQ Demo Project"
    assert body["area"]["external_id"] == AREA_EXTERNAL_ID
    assert body["area"]["area_name"] == "Default Area"
    assert [unit["status"] for unit in body["inventory"]["units"]] == [
        "available",
        "available",
        "available",
        "available",
        "reserved",
        "sold",
    ]

    second = await client.post("/api/v1/inventory/bootstrap-default")
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["project"]["project_id"] == body["project"]["project_id"]

    async with truncate_all.connect() as connection:
        project_count = await connection.scalar(
            sa.select(sa.func.count()).select_from(projects).where(projects.c.source_instance_id == SOURCE_INSTANCE_ID)
        )
        area_count = await connection.scalar(
            sa.select(sa.func.count()).select_from(areas).where(areas.c.source_instance_id == SOURCE_INSTANCE_ID)
        )
        unit_count = await connection.scalar(
            sa.select(sa.func.count()).select_from(units).where(units.c.source_instance_id == SOURCE_INSTANCE_ID)
        )
        deal_count = await connection.scalar(
            sa.select(sa.func.count()).select_from(deals).where(deals.c.source_instance_id == SOURCE_INSTANCE_ID)
        )

    assert (project_count, area_count, unit_count, deal_count) == (1, 1, 6, 2)
