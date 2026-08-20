"""Seed deterministic synthetic 2026 domain data for approved demo targets.

Revision ID: 0023_seed_domain_demo_2026
Revises: 0022_ranking_config_v2
Create Date: 2026-08-16

This is an explicitly approved demo-data exception.  Mini CRM remains the
canonical owner of projects, areas, units, and deals in normal operation; this
revision writes only the reserved synthetic namespace so a disposable
development/demo/test database can exercise the domain dashboard without a
running CRM relay.

The deterministic row generator is embedded here intentionally: the production
API image mounts ``alembic/`` and ``src/`` but not the repository's ``scripts/``
directory, so an applied migration must not depend on an unmounted CLI module.
No legacy ``sales_records`` or ``absorption_daily`` rows are written.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, NamedTuple

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op
from src.models.tables import areas, deals, projects, units

revision: str = "0023_seed_domain_demo_2026"
down_revision: str | None = "0022_ranking_config_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_AS_OF_DATE = date(2026, 8, 16)
DEMO_START_DATE = date(2025, 8, 16)
DEMO_END_DATE = DEMO_AS_OF_DATE
DEMO_EXTERNAL_PREFIX = "demo26-"
SOURCE_SYSTEM = "synthetic_demo"
SOURCE_INSTANCE_ID = "synthetic-demo-2026"
DOMAIN_CALCULATOR = "domain_units_deals"
ALLOWED_ENVIRONMENTS = {"development", "demo", "test"}
PRODUCTION_ENVIRONMENTS = {"production", "staging"}
PRODUCTION_DATABASE_MARKERS = ("prod", "production", "live", "minicrm")


class _ProjectSpec(NamedTuple):
    external_id: str
    name: str
    launch_offset_days: int


class _AreaSpec(NamedTuple):
    external_id: str
    project_external_id: str
    name: str
    unit_type: str
    bedrooms: int
    area_sqm: Decimal
    total_units: int
    scenario: str


_PROJECTS = (
    _ProjectSpec("demo26-p01", "DEMO 2026 Northlight", -330),
    _ProjectSpec("demo26-p02", "DEMO 2026 Rivergate", -270),
    _ProjectSpec("demo26-p03", "DEMO 2026 Cedar Point", -210),
    _ProjectSpec("demo26-p04", "DEMO 2026 Harbor Row", -120),
)

_AREAS = (
    _AreaSpec("demo26-p01-a01", "demo26-p01", "Northlight A", "Studio", 0, Decimal("32.00"), 72, "strong"),
    _AreaSpec("demo26-p01-a02", "demo26-p01", "Northlight B", "2PN", 2, Decimal("68.50"), 120, "stable"),
    _AreaSpec("demo26-p01-a03", "demo26-p01", "Northlight C", "3PN", 3, Decimal("92.00"), 84, "mature"),
    _AreaSpec("demo26-p02-a01", "demo26-p02", "Rivergate A", "1PN", 1, Decimal("45.00"), 96, "slow"),
    _AreaSpec("demo26-p02-a02", "demo26-p02", "Rivergate B", "2PN", 2, Decimal("70.00"), 144, "noisy"),
    _AreaSpec("demo26-p02-a03", "demo26-p02", "Rivergate C", "3PN", 3, Decimal("105.00"), 60, "launching"),
    _AreaSpec("demo26-p03-a01", "demo26-p03", "Cedar Point A", "Studio", 0, Decimal("30.00"), 60, "stable"),
    _AreaSpec("demo26-p03-a02", "demo26-p03", "Cedar Point B", "2PN", 2, Decimal("66.00"), 108, "slow"),
    _AreaSpec("demo26-p03-a03", "demo26-p03", "Cedar Point C", "4PN", 4, Decimal("128.00"), 48, "mature"),
    _AreaSpec("demo26-p04-a01", "demo26-p04", "Harbor Row A", "1PN", 1, Decimal("48.00"), 84, "launching"),
    _AreaSpec("demo26-p04-a02", "demo26-p04", "Harbor Row B", "2PN", 2, Decimal("74.00"), 132, "noisy"),
    _AreaSpec("demo26-p04-a03", "demo26-p04", "Harbor Row C", "3PN", 3, Decimal("110.00"), 54, "strong"),
)

_SOLD_SHARE = {"strong": 0.62, "stable": 0.46, "slow": 0.23, "launching": 0.18, "mature": 0.72, "noisy": 0.39}
_RESERVED_SHARE = {"strong": 0.08, "stable": 0.06, "slow": 0.04, "launching": 0.10, "mature": 0.03, "noisy": 0.07}
_FUNNEL_STATUSES = ("lead", "qualified", "interested", "viewing")


def _stable_int(key: str, low: int, high: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return low + int(digest[:16], 16) % (high - low + 1)


def _uid(kind: str, external_id: str) -> uuid.UUID:
    namespace = uuid.UUID("f76e17ab-8d87-4c8c-8d1f-7d3df4b3d2d5")
    return uuid.uuid5(namespace, f"{kind}:{external_id}")


def _utc_timestamp(day: date, hour: int = 9) -> datetime:
    return datetime.combine(day, time(hour=hour, tzinfo=UTC))


def _sale_date(spec: _AreaSpec, rank: int, total: int, start: date, end: date) -> date:
    span = max((end - start).days, 1)
    fraction = rank / max(total - 1, 1)
    if spec.scenario == "strong":
        curve = 0.45 * fraction + 0.55 * fraction**0.55
    elif spec.scenario == "slow":
        curve = fraction**1.65
    elif spec.scenario == "mature":
        curve = 0.25 * fraction + 0.75 * fraction**0.58
    elif spec.scenario == "launching":
        curve = fraction**0.72
    elif spec.scenario == "noisy":
        curve = _stable_int(f"date:{spec.external_id}:{rank}", 0, 10000) / 10000
    else:
        curve = fraction
    jitter = _stable_int(f"jitter:{spec.external_id}:{rank}", -3, 3)
    return min(end, max(start, start + timedelta(days=round(span * curve) + jitter)))


def _deal_row(
    external_id: str,
    unit_id: uuid.UUID,
    status: str,
    created_at: datetime,
    *,
    reserved_at: datetime | None = None,
    sold_at: datetime | None = None,
    lost_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    latest = max((stamp for stamp in (reserved_at, sold_at, lost_at, updated_at) if stamp), default=created_at)
    return {
        "id": _uid("deal", external_id),
        "source_system": SOURCE_SYSTEM,
        "source_instance_id": SOURCE_INSTANCE_ID,
        "external_deal_id": external_id,
        "unit_id": unit_id,
        "status": status,
        "source_status": status,
        "reserved_at": reserved_at,
        "sold_at": sold_at,
        "lost_at": lost_at,
        "source_revision": 1,
        "source_updated_at": latest,
        "deleted_at": None,
        "created_at": created_at,
        "updated_at": latest,
    }


def _plan_rows() -> dict[str, list[dict[str, Any]]]:
    as_of_ts = _utc_timestamp(DEMO_AS_OF_DATE, 18)
    project_map = {spec.external_id: spec for spec in _PROJECTS}
    project_rows: list[dict[str, Any]] = []
    area_rows: list[dict[str, Any]] = []
    unit_rows: list[dict[str, Any]] = []
    deal_rows: list[dict[str, Any]] = []

    for spec in _PROJECTS:
        launch_date = DEMO_AS_OF_DATE + timedelta(days=spec.launch_offset_days)
        project_rows.append(
            {
                "id": _uid("project", spec.external_id),
                "name": spec.name,
                "launch_date": launch_date,
                "created_at": _utc_timestamp(launch_date),
                "status": "active",
                "headline": "Synthetic 2026 demo inventory",
                "introduce": "Synthetic demo data for analytics testing; not real market data.",
                "created_by": None,
                "reviewed_by": None,
                "reviewed_at": None,
                "review_reason": None,
                "absorption_calculator": DOMAIN_CALCULATOR,
                "external_id": spec.external_id,
                "source_system": SOURCE_SYSTEM,
                "source_instance_id": SOURCE_INSTANCE_ID,
                "source_revision": 1,
                "source_updated_at": as_of_ts,
                "updated_at": as_of_ts,
            }
        )

    for spec in _AREAS:
        project = project_map[spec.project_external_id]
        launch_date = DEMO_AS_OF_DATE + timedelta(days=project.launch_offset_days)
        sale_start = max(date(2026, 1, 1), launch_date + timedelta(days=7))
        sale_end = DEMO_END_DATE
        created_at = _utc_timestamp(max(launch_date, DEMO_START_DATE))
        area_id = _uid("area", spec.external_id)
        area_rows.append(
            {
                "id": area_id,
                "project_id": _uid("project", spec.project_external_id),
                "area_name": spec.name,
                "unit_type": spec.unit_type,
                "bedrooms": spec.bedrooms,
                "area_sqm": spec.area_sqm,
                "total_units": spec.total_units,
                "created_at": created_at,
                "status": "active",
                "headline": f"{spec.name} · synthetic 2026",
                "introduce": "Synthetic demo area; values are for product testing only.",
                "created_by": None,
                "reviewed_by": None,
                "reviewed_at": None,
                "review_reason": None,
                "external_id": spec.external_id,
                "source_system": SOURCE_SYSTEM,
                "source_instance_id": SOURCE_INSTANCE_ID,
                "source_revision": 1,
                "source_updated_at": as_of_ts,
                "updated_at": as_of_ts,
            }
        )
        sellable_units = spec.total_units - max(1, round(spec.total_units * _stable_int(f"blocked:{spec.external_id}", 0, 5) / 100))
        sold_count = max(1, round(sellable_units * _SOLD_SHARE[spec.scenario]))
        reserved_count = min(max(1, round(sellable_units * _RESERVED_SHARE[spec.scenario])), max(0, sellable_units - sold_count))
        unit_specs = [(f"{spec.external_id}-u{index + 1:04d}", _uid("unit", f"{spec.external_id}-u{index + 1:04d}")) for index in range(spec.total_units)]
        sellable_specs = list(enumerate(unit_specs[:sellable_units]))
        sold_ids = {
            unit_external_id
            for _index, (unit_external_id, _unit_id) in sorted(
                sellable_specs,
                key=lambda pair: _stable_int(f"sold:{spec.external_id}:{pair[0]}", 0, 1_000_000),
            )[:sold_count]
        }
        remaining_ids = [external_id for external_id, _unit_id in unit_specs if external_id not in sold_ids]
        reserved_ids = set(remaining_ids[:reserved_count])
        blocked_ids = {external_id for external_id, _unit_id in unit_specs[sellable_units:]}
        sold_order = sorted(sold_ids, key=lambda value: _stable_int(f"order:{spec.external_id}:{value}", 0, 1_000_000))
        unit_created_at = _utc_timestamp(max(launch_date, sale_start - timedelta(days=30)))
        for external_id, unit_id in unit_specs:
            status = "blocked" if external_id in blocked_ids else "sold" if external_id in sold_ids else "reserved" if external_id in reserved_ids else "available"
            unit_rows.append(
                {
                    "id": unit_id,
                    "source_system": SOURCE_SYSTEM,
                    "source_instance_id": SOURCE_INSTANCE_ID,
                    "external_unit_id": external_id,
                    "area_id": area_id,
                    "unit_code": external_id.upper(),
                    "unit_type": spec.unit_type,
                    "status": status,
                    "source_revision": 1,
                    "source_updated_at": as_of_ts,
                    "deleted_at": None,
                    "created_at": unit_created_at,
                    "updated_at": as_of_ts,
                }
            )
        for rank, external_id in enumerate(sold_order):
            sold_at = _utc_timestamp(_sale_date(spec, rank, len(sold_order), sale_start, sale_end), _stable_int(f"sold-hour:{external_id}", 9, 17))
            reserved_at = max(unit_created_at, sold_at - timedelta(days=_stable_int(f"reserved:{external_id}", 5, 30)))
            deal_rows.append(_deal_row(f"{external_id}-deal-sold", _uid("unit", external_id), "sold", unit_created_at, reserved_at=reserved_at, sold_at=sold_at))
        for external_id in sorted(reserved_ids):
            reserved_at = _utc_timestamp(max(sale_start, sale_end - timedelta(days=_stable_int(f"hold:{external_id}", 0, 30))), 10)
            deal_rows.append(_deal_row(f"{external_id}-deal-reserved", _uid("unit", external_id), "reserved", unit_created_at, reserved_at=reserved_at))
        unsold_for_history = [external_id for external_id in remaining_ids if external_id not in reserved_ids and external_id not in blocked_ids]
        lost_count = min(len(unsold_for_history), max(1, round(sold_count * 0.08)))
        for external_id in sorted(unsold_for_history)[:lost_count]:
            lost_at = _utc_timestamp(max(sale_start, sale_end - timedelta(days=_stable_int(f"lost:{external_id}", 10, 75))), 11)
            deal_rows.append(_deal_row(f"{external_id}-deal-lost", _uid("unit", external_id), "lost", unit_created_at, reserved_at=max(unit_created_at, lost_at - timedelta(days=7)), lost_at=lost_at))
        for index, external_id in enumerate(unsold_for_history[lost_count:]):
            if index % 5 != 0:
                continue
            status = _FUNNEL_STATUSES[_stable_int(f"funnel:{external_id}", 0, len(_FUNNEL_STATUSES) - 1)]
            deal_rows.append(_deal_row(f"{external_id}-deal-funnel", _uid("unit", external_id), status, unit_created_at, updated_at=as_of_ts))

    return {"projects": project_rows, "areas": area_rows, "units": unit_rows, "deals": deal_rows}


def _configured_environment() -> tuple[str, str]:
    """Return ``(classification, app_environment)`` with a fail-closed policy."""

    explicit_app_environment = os.getenv("APP_ENV", "").strip().lower()
    seed_environment = os.getenv("SEED_ENVIRONMENT", "").strip().lower()

    # Alembic's settings loader reads .env without exporting values to
    # os.environ, so use it only as a fallback.  Environment variables still
    # take precedence and production-like APP_ENV can never be overridden by
    # SEED_ENVIRONMENT.
    app_environment = explicit_app_environment
    if not app_environment:
        try:
            from src.config import get_settings

            app_environment = get_settings().app_env.strip().lower()
        except Exception:  # pragma: no cover - fail closed below
            app_environment = ""

    if app_environment in PRODUCTION_ENVIRONMENTS:
        raise RuntimeError("0023 refuses to run in a production-like APP_ENV")

    classification = seed_environment or app_environment
    if classification not in ALLOWED_ENVIRONMENTS:
        raise RuntimeError(
            "0023 requires an explicit development, demo, or test environment"
        )
    return classification, app_environment


def _assert_safe_target(bind: sa.Connection) -> None:
    classification, _app_environment = _configured_environment()
    database_name = str(bind.execute(sa.text("SELECT current_database()")).scalar_one())
    lowered_name = database_name.lower()

    if classification == "test" and not lowered_name.endswith("_test"):
        raise RuntimeError("0023 test target database must end with _test")
    if any(marker in lowered_name for marker in PRODUCTION_DATABASE_MARKERS):
        raise RuntimeError("0023 refuses a production-like or Mini CRM database")

def _upsert_batches(bind: sa.Connection, table: sa.Table, rows: list[dict[str, Any]]) -> None:
    """Bulk-upsert stable primary keys without per-row ORM work."""

    if not rows:
        return
    for offset in range(0, len(rows), 250):
        batch = rows[offset : offset + 250]
        statement = pg_insert(table).values(batch)
        update_values = {
            column.name: statement.excluded[column.name]
            for column in table.columns
            if column.name != "id"
        }
        bind.execute(statement.on_conflict_do_update(index_elements=[table.c.id], set_=update_values))


def upgrade() -> None:
    bind = op.get_bind()
    _assert_safe_target(bind)
    rows = _plan_rows()

    # Parent rows must exist before their children because all four tables have
    # real foreign keys.  The entire Alembic revision runs in one transaction.
    _upsert_batches(bind, projects, rows["projects"])
    _upsert_batches(bind, areas, rows["areas"])
    _upsert_batches(bind, units, rows["units"])
    _upsert_batches(bind, deals, rows["deals"])


def _owned_rows(table: sa.Table, external_column: str) -> sa.ColumnElement[bool]:
    return sa.and_(
        table.c.source_system == SOURCE_SYSTEM,
        table.c.source_instance_id == SOURCE_INSTANCE_ID,
        getattr(table.c, external_column).like(f"{DEMO_EXTERNAL_PREFIX}%"),
    )


def downgrade() -> None:
    """Remove only this migration's namespaced rows, children before parents."""

    bind = op.get_bind()
    _assert_safe_target(bind)
    bind.execute(sa.delete(deals).where(_owned_rows(deals, "external_deal_id")))
    bind.execute(sa.delete(units).where(_owned_rows(units, "external_unit_id")))
    bind.execute(sa.delete(areas).where(_owned_rows(areas, "external_id")))
    bind.execute(sa.delete(projects).where(_owned_rows(projects, "external_id")))
