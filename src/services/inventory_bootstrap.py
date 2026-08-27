"""Development-only, idempotent demo inventory for the empty Inventory page."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import get_settings
from src.db import get_session_factory
from src.models.tables import areas, deals, projects, units

SOURCE_SYSTEM = "synthetic_demo"
SOURCE_INSTANCE_ID = "inventory-page-bootstrap-v1"
PROJECT_EXTERNAL_ID = "inventory-demo-v1"
AREA_EXTERNAL_ID = "inventory-demo-v1-area-default"
# Reuse the deterministic UUID5 namespace established by the existing
# development/demo seed mechanism.  The reserved external IDs below keep this
# inventory-page fixture separate from the broader synthetic-demo-2026 data.
ID_NAMESPACE = uuid.UUID("f76e17ab-8d87-4c8c-8d1f-7d3df4b3d2d5")


@dataclass(frozen=True, slots=True)
class InventoryBootstrapSelection:
    project: sa.RowMapping
    area: sa.RowMapping
    created: bool


def _id(kind: str, external_id: str) -> uuid.UUID:
    return uuid.uuid5(ID_NAMESPACE, f"{kind}:{external_id}")


class InventoryBootstrapService:
    """Create only reserved, synthetic rows; never alter user-owned records."""

    async def ensure_default(self) -> InventoryBootstrapSelection:
        if get_settings().app_env not in {"development", "demo", "test"}:
            raise RuntimeError("inventory demo bootstrap is disabled outside development/demo/test")

        now = datetime.now(UTC)
        project_id = _id("project", PROJECT_EXTERNAL_ID)
        area_id = _id("area", AREA_EXTERNAL_ID)
        created = False

        project_row = {
            "id": project_id,
            "name": "AbsorptionIQ Demo Project",
            "launch_date": date.today() - timedelta(days=30),
            "created_at": now,
            "status": "active",
            "headline": "Synthetic demo inventory",
            "introduce": "Synthetic development data; not a customer project.",
            "absorption_calculator": "domain_units_deals",
            "external_id": PROJECT_EXTERNAL_ID,
            "source_system": SOURCE_SYSTEM,
            "source_instance_id": SOURCE_INSTANCE_ID,
            "source_revision": 1,
            "source_updated_at": now,
            "updated_at": now,
        }
        area_row = {
            "id": area_id,
            "project_id": project_id,
            "area_name": "Default Area",
            "unit_type": "2PN",
            "bedrooms": 2,
            "area_sqm": Decimal("70.00"),
            "total_units": 6,
            "created_at": now,
            "status": "active",
            "headline": "Synthetic default area",
            "introduce": "Synthetic development data; not a customer area.",
            "external_id": AREA_EXTERNAL_ID,
            "source_system": SOURCE_SYSTEM,
            "source_instance_id": SOURCE_INSTANCE_ID,
            "source_revision": 1,
            "source_updated_at": now,
            "updated_at": now,
        }

        unit_statuses = ("available", "available", "available", "available", "reserved", "sold")
        unit_rows = []
        deal_rows = []
        for number, status in enumerate(unit_statuses, start=1):
            external_id = f"{AREA_EXTERNAL_ID}-u{number:03d}"
            unit_id = _id("unit", external_id)
            unit_rows.append(
                {
                    "id": unit_id,
                    "source_system": SOURCE_SYSTEM,
                    "source_instance_id": SOURCE_INSTANCE_ID,
                    "external_unit_id": external_id,
                    "area_id": area_id,
                    "unit_code": f"DEMO-{number:03d}",
                    "unit_type": "2PN",
                    "status": status,
                    "source_revision": 1,
                    "source_updated_at": now,
                    "deleted_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            if status in {"reserved", "sold"}:
                deal_rows.append(
                    {
                        "id": _id("deal", f"{external_id}-{status}"),
                        "source_system": SOURCE_SYSTEM,
                        "source_instance_id": SOURCE_INSTANCE_ID,
                        "external_deal_id": f"{external_id}-{status}",
                        "unit_id": unit_id,
                        "status": status,
                        "source_status": status,
                        "reserved_at": now - timedelta(days=2 if status == "sold" else 1),
                        "sold_at": now if status == "sold" else None,
                        "lost_at": None,
                        "source_revision": 1,
                        "source_updated_at": now,
                        "deleted_at": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                )

        async with get_session_factory()() as session:
            async with session.begin():
                for table, row, constraint in (
                    (projects, project_row, "uq_projects_source_identity"),
                    (areas, area_row, "uq_areas_source_identity"),
                ):
                    result = await session.execute(
                        pg_insert(table).values(row).on_conflict_do_nothing(constraint=constraint).returning(table.c.id)
                    )
                    inserted = result.scalar_one_or_none() is not None
                    created = created or inserted

                for row in unit_rows:
                    result = await session.execute(
                        pg_insert(units)
                        .values(row)
                        .on_conflict_do_nothing(constraint="uq_units_source_identity")
                        .returning(units.c.id)
                    )
                    inserted = result.scalar_one_or_none() is not None
                    created = created or inserted
                for row in deal_rows:
                    result = await session.execute(
                        pg_insert(deals)
                        .values(row)
                        .on_conflict_do_nothing(constraint="uq_deals_source_identity")
                        .returning(deals.c.id)
                    )
                    inserted = result.scalar_one_or_none() is not None
                    created = created or inserted

                project = (
                    await session.execute(
                        sa.select(projects).where(
                            projects.c.source_instance_id == SOURCE_INSTANCE_ID,
                            projects.c.external_id == PROJECT_EXTERNAL_ID,
                        )
                    )
                ).mappings().one()
                area = (
                    await session.execute(
                        sa.select(areas).where(
                            areas.c.source_instance_id == SOURCE_INSTANCE_ID,
                            areas.c.external_id == AREA_EXTERNAL_ID,
                        )
                    )
                ).mappings().one()

        return InventoryBootstrapSelection(project=project, area=area, created=created)
