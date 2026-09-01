"""Canonical five-unit ranking fixture for database-backed ranking tests.

This fixture is test data only: it creates a project, area, units, and CRM
deals with deterministic legacy scores.  It is intentionally independent of
the retired Agent E2E API module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa

from src.models.tables import areas, deals, projects, ranking_configs, units

SEED_WEIGHTS = {
    "unit_available": {"weight": "0.35", "direction": "positive", "missing_value_policy": "zero", "min_confidence": "0"},
    "unit_demand_norm": {"weight": "0.25", "direction": "positive", "missing_value_policy": "zero", "min_confidence": "0"},
    "area_velocity_norm": {"weight": "0.20", "direction": "positive", "missing_value_policy": "neutral", "min_confidence": "0"},
    "area_conversion_norm": {"weight": "0.20", "direction": "positive", "missing_value_policy": "neutral", "min_confidence": "0"},
}

PROJECT_ID = uuid.uuid4()
AREA_ID = uuid.uuid4()
UNIT_IDS = {f"u{i}": uuid.uuid4() for i in range(1, 6)}


async def _insert_config(session_factory, *, published: bool = True) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            sa.insert(ranking_configs).values(
                id=uuid.uuid4(),
                version=2,
                status="published" if published else "draft",
                weights=SEED_WEIGHTS,
                min_weight_coverage=Decimal("0.5"),
                note="test v2",
                created_by="test",
                created_at=now,
                published_by="test" if published else None,
                published_at=now if published else None,
            )
        )
        await session.commit()


async def _insert_dataset(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            sa.insert(projects).values(
                id=PROJECT_ID, name="Test Project", launch_date=date(2026, 1, 1),
                created_at=now, updated_at=now, absorption_calculator="legacy_aggregate",
                external_id="P-AGENT-TEST-1", source_system="mini_crm", source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(areas).values(
                id=AREA_ID, project_id=PROJECT_ID, area_name="Tower A", unit_type="2PN",
                bedrooms=2, area_sqm=Decimal("60"), total_units=10, created_at=now,
                external_id="A-AGENT-TEST-1", source_system="mini_crm", source_instance_id="test",
            )
        )
        statuses = {"u1": "available", "u2": "available", "u3": "available", "u4": "sold", "u5": "available"}
        for key, unit_id in UNIT_IDS.items():
            await session.execute(
                sa.insert(units).values(
                    id=unit_id, source_system="mini_crm", source_instance_id="test", external_unit_id=key,
                    area_id=AREA_ID, unit_code=key, unit_type="2PN", status=statuses[key],
                    created_at=now, updated_at=now,
                )
            )
        await session.execute(
            sa.insert(deals).values(
                id=uuid.uuid4(), source_system="mini_crm", source_instance_id="test",
                external_deal_id="d-u3", unit_id=UNIT_IDS["u3"], status="reserved", source_status="reserved",
                reserved_at=now, created_at=now, updated_at=now,
            )
        )
        await session.execute(
            sa.insert(deals).values(
                id=uuid.uuid4(), source_system="mini_crm", source_instance_id="test",
                external_deal_id="d-u4", unit_id=UNIT_IDS["u4"], status="sold", source_status="sold",
                sold_at=now - timedelta(days=5), created_at=now, updated_at=now,
            )
        )
        for i, status in enumerate(("lead", "qualified", "viewing"), start=1):
            await session.execute(
                sa.insert(deals).values(
                    id=uuid.uuid4(), source_system="mini_crm", source_instance_id="test",
                    external_deal_id=f"d-u2-{i}", unit_id=UNIT_IDS["u2"], status=status,
                    source_status=status, created_at=now, updated_at=now,
                )
            )
        await session.commit()
