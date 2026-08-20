"""Rename synthetic labels and complete missing Vinhomes domain coverage.

Revision ID: 0024_rename_synthetic_labels_vinhomes_stats
Revises: 0023_seed_domain_demo_2026
Create Date: 2026-08-16

This is a forward-only data correction for the two explicitly synthetic
namespaces already present in the development database:

* ``synthetic_demo / synthetic-demo-2026`` — visible labels from 0023 lose the
  word ``DEMO`` while their technical external IDs and provenance remain.
* ``crm_real_data_fixture / ai-dev-fixture`` — the approved Vinhomes fixture
  receives deterministic sold deals only in areas that currently have no
  effective sold deal.  These rows are synthetic and are not CRM or market
  facts.

No legacy aggregate tables are written.  The migration is namespace-scoped,
idempotent, and refuses production-like targets through the same target gate as
0023.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from src.models.tables import areas, deals, projects, units

revision: str = "0024_vinhomes_labels_stats"
down_revision: str | None = "0023_seed_domain_demo_2026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AS_OF_DATE = date(2026, 8, 16)
AS_OF_TIMESTAMP = datetime.combine(AS_OF_DATE, time(hour=18, tzinfo=UTC))

DOMAIN_SOURCE_SYSTEM = "crm_real_data_fixture"
DOMAIN_SOURCE_INSTANCE_ID = "ai-dev-fixture"
DOMAIN_DEAL_PREFIX = "stats26-"
VINHOMES_PROJECT_EXTERNAL_IDS = ("prj_op1", "prj_rvs", "prj_smc", "prj_tmc")

SYNTHETIC_SOURCE_SYSTEM = "synthetic_demo"
SYNTHETIC_SOURCE_INSTANCE_ID = "synthetic-demo-2026"
SYNTHETIC_EXTERNAL_PREFIX = "demo26-"
SYNTHETIC_PROJECT_NAMES = {
    "demo26-p01": "2026 Northlight",
    "demo26-p02": "2026 Rivergate",
    "demo26-p03": "2026 Cedar Point",
    "demo26-p04": "2026 Harbor Row",
}


def _stable_int(key: str, low: int, high: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return low + int(digest[:16], 16) % (high - low + 1)


def _uid(kind: str, external_id: str) -> uuid.UUID:
    namespace = uuid.UUID("f76e17ab-8d87-4c8c-8d1f-7d3df4b3d2d5")
    return uuid.uuid5(namespace, f"{kind}:{external_id}")


def _safe_target(bind: sa.Connection) -> None:
    app_environment = os.getenv("APP_ENV", "").strip().lower()
    if not app_environment:
        try:
            from src.config import get_settings

            app_environment = get_settings().app_env.strip().lower()
        except Exception:  # pragma: no cover - fail closed below
            app_environment = ""
    if app_environment in {"production", "staging"}:
        raise RuntimeError("0024 refuses to run in a production-like APP_ENV")

    database_name = str(bind.execute(sa.text("SELECT current_database()")).scalar_one())
    lowered_name = database_name.lower()
    if any(marker in lowered_name for marker in ("prod", "production", "live", "minicrm")):
        raise RuntimeError("0024 refuses a production-like or Mini CRM database")


def _synthetic_project_updates(bind: sa.Connection) -> None:
    for external_id, name in SYNTHETIC_PROJECT_NAMES.items():
        bind.execute(
            projects.update()
            .where(
                projects.c.external_id == external_id,
                projects.c.source_system == SYNTHETIC_SOURCE_SYSTEM,
                projects.c.source_instance_id == SYNTHETIC_SOURCE_INSTANCE_ID,
            )
            .values(name=name, updated_at=AS_OF_TIMESTAMP)
        )

    rows = bind.execute(
        sa.select(units.c.id, units.c.external_unit_id)
        .where(
            units.c.source_system == SYNTHETIC_SOURCE_SYSTEM,
            units.c.source_instance_id == SYNTHETIC_SOURCE_INSTANCE_ID,
            units.c.external_unit_id.like(f"{SYNTHETIC_EXTERNAL_PREFIX}%"),
        )
    ).all()
    for unit_id, external_unit_id in rows:
        # demo26-p01-a01-u0001 -> P01-A01-0001.  The external ID remains the
        # stable technical identity and retains the namespace provenance.
        pieces = external_unit_id.removeprefix(SYNTHETIC_EXTERNAL_PREFIX).split("-")
        if len(pieces) == 4 and pieces[-1].startswith("u"):
            unit_code = "-".join((*pieces[:3], pieces[3][1:])).upper()
            bind.execute(
                units.update().where(units.c.id == unit_id).values(
                    unit_code=unit_code,
                    updated_at=AS_OF_TIMESTAMP,
                )
            )


def _supplemental_deals(bind: sa.Connection) -> tuple[list[dict[str, Any]], list[uuid.UUID]]:
    project_ids = sa.select(projects.c.id).where(
        projects.c.external_id.in_(VINHOMES_PROJECT_EXTERNAL_IDS),
        projects.c.source_system == DOMAIN_SOURCE_SYSTEM,
        projects.c.source_instance_id == DOMAIN_SOURCE_INSTANCE_ID,
    )
    area_rows = bind.execute(
        sa.select(areas.c.id, areas.c.external_id)
        .where(
            areas.c.project_id.in_(project_ids),
            areas.c.source_system == DOMAIN_SOURCE_SYSTEM,
            areas.c.source_instance_id == DOMAIN_SOURCE_INSTANCE_ID,
        )
        .order_by(areas.c.external_id)
    ).all()

    rows: list[dict[str, Any]] = []
    changed_unit_ids: list[uuid.UUID] = []
    for area_id, area_external_id in area_rows:
        sold_exists = bind.execute(
            sa.select(sa.literal(True))
            .select_from(deals.join(units, deals.c.unit_id == units.c.id))
            .where(
                units.c.area_id == area_id,
                units.c.deleted_at.is_(None),
                deals.c.source_system == DOMAIN_SOURCE_SYSTEM,
                deals.c.source_instance_id == DOMAIN_SOURCE_INSTANCE_ID,
                deals.c.status == "sold",
                deals.c.sold_at.isnot(None),
                deals.c.sold_at <= AS_OF_TIMESTAMP,
                deals.c.deleted_at.is_(None),
            )
            .limit(1)
        ).scalar()
        if sold_exists:
            continue

        available_units = bind.execute(
            sa.select(units.c.id, units.c.external_unit_id)
            .where(
                units.c.area_id == area_id,
                units.c.source_system == DOMAIN_SOURCE_SYSTEM,
                units.c.source_instance_id == DOMAIN_SOURCE_INSTANCE_ID,
                units.c.status == "available",
                units.c.deleted_at.is_(None),
            )
            .order_by(units.c.external_unit_id)
            .limit(4)
        ).all()
        if len(available_units) < 4:
            raise RuntimeError(f"0024 needs four available units in {area_external_id}")

        for rank, (unit_id, unit_external_id) in enumerate(available_units, start=1):
            offset = _stable_int(f"{area_external_id}:{rank}", 0, 10)
            sold_day = date(2026, 8, 2) - timedelta(days=offset * rank * 7)
            external_deal_id = f"{DOMAIN_DEAL_PREFIX}{area_external_id}-sold-{rank:02d}"
            created_at = datetime.combine(sold_day - timedelta(days=21), time(hour=9, tzinfo=UTC))
            sold_at = datetime.combine(sold_day, time(hour=10 + rank, tzinfo=UTC))
            rows.append(
                {
                    "id": _uid("deal", external_deal_id),
                    "source_system": DOMAIN_SOURCE_SYSTEM,
                    "source_instance_id": DOMAIN_SOURCE_INSTANCE_ID,
                    "external_deal_id": external_deal_id,
                    "unit_id": unit_id,
                    "status": "sold",
                    "source_status": "sold",
                    "reserved_at": created_at + timedelta(days=7),
                    "sold_at": sold_at,
                    "lost_at": None,
                    "source_revision": 2,
                    "source_updated_at": sold_at,
                    "deleted_at": None,
                    "created_at": created_at,
                    "updated_at": sold_at,
                }
            )
            changed_unit_ids.append(unit_id)
            # One effective sold deal per selected unit is guaranteed by the
            # available-unit query and the deterministic external deal ID.

    return rows, changed_unit_ids


def _upsert_deals(bind: sa.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    statement = pg_insert(deals).values(rows)
    update_values = {
        column.name: statement.excluded[column.name]
        for column in deals.columns
        if column.name != "id"
    }
    bind.execute(statement.on_conflict_do_update(index_elements=[deals.c.id], set_=update_values))


def upgrade() -> None:
    bind = op.get_bind()
    _safe_target(bind)
    _synthetic_project_updates(bind)
    deal_rows, changed_unit_ids = _supplemental_deals(bind)
    _upsert_deals(bind, deal_rows)
    if changed_unit_ids:
        updated_at = sa.case(
            (units.c.created_at.is_(None), AS_OF_TIMESTAMP),
            else_=sa.func.greatest(
                units.c.created_at + sa.text("INTERVAL '1 microsecond'"),
                AS_OF_TIMESTAMP,
            ),
        )
        bind.execute(
            units.update()
            .where(units.c.id.in_(changed_unit_ids))
            .values(status="sold", source_revision=2, source_updated_at=AS_OF_TIMESTAMP, updated_at=updated_at)
        )


def downgrade() -> None:
    bind = op.get_bind()
    _safe_target(bind)
    owned_deals = sa.select(deals.c.unit_id).where(
        deals.c.source_system == DOMAIN_SOURCE_SYSTEM,
        deals.c.source_instance_id == DOMAIN_SOURCE_INSTANCE_ID,
        deals.c.external_deal_id.like(f"{DOMAIN_DEAL_PREFIX}%"),
    )
    unit_ids = [row[0] for row in bind.execute(owned_deals).all()]
    bind.execute(deals.delete().where(deals.c.unit_id.in_(owned_deals)))
    if unit_ids:
        remaining_sold = sa.select(sa.literal(True)).where(
            deals.c.unit_id == units.c.id,
            deals.c.status == "sold",
            deals.c.sold_at.isnot(None),
            deals.c.deleted_at.is_(None),
        )
        bind.execute(
            units.update()
            .where(
                units.c.id.in_(unit_ids),
                units.c.source_system == DOMAIN_SOURCE_SYSTEM,
                units.c.source_instance_id == DOMAIN_SOURCE_INSTANCE_ID,
                ~sa.exists(remaining_sold),
            )
            .values(status="available", source_revision=1, updated_at=AS_OF_TIMESTAMP)
        )

    bind.execute(
        units.update()
        .where(
            units.c.source_system == SYNTHETIC_SOURCE_SYSTEM,
            units.c.source_instance_id == SYNTHETIC_SOURCE_INSTANCE_ID,
            units.c.external_unit_id.like(f"{SYNTHETIC_EXTERNAL_PREFIX}%"),
        )
        .values(
            unit_code=sa.func.upper(units.c.external_unit_id),
            updated_at=AS_OF_TIMESTAMP,
        )
    )
    for external_id, name in SYNTHETIC_PROJECT_NAMES.items():
        bind.execute(
            projects.update()
            .where(
                projects.c.external_id == external_id,
                projects.c.source_system == SYNTHETIC_SOURCE_SYSTEM,
                projects.c.source_instance_id == SYNTHETIC_SOURCE_INSTANCE_ID,
            )
            .values(name=f"DEMO {name}", updated_at=AS_OF_TIMESTAMP)
        )
