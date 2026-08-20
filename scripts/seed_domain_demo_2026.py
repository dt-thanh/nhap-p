"""Create deterministic synthetic 2026 domain data for a safe demo database.

This command is deliberately separate from Alembic.  It owns only rows stamped
with ``synthetic_demo / synthetic-demo-2026`` and writes only the canonical
``projects -> areas -> units -> deals`` tables.  It never writes legacy
``sales_records`` or ``absorption_daily`` rows.

Examples::

    python -m scripts.seed_domain_demo_2026 --dry-run
    python -m scripts.seed_domain_demo_2026 --as-of-date 2026-08-16

The target must be explicitly classified as ``development``, ``demo`` or
``test``.  A test target must end in ``_test``.  The command prints only
sanitized target metadata; it never prints a database URL or secret.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, deals, projects, units

SOURCE_SYSTEM = "synthetic_demo"
SOURCE_INSTANCE_ID = "synthetic-demo-2026"
DOMAIN_CALCULATOR = "domain_units_deals"
TARGET_YEAR = 2026


@dataclass(frozen=True, slots=True)
class ProjectSpec:
    external_id: str
    name: str
    launch_offset_days: int


@dataclass(frozen=True, slots=True)
class AreaSpec:
    external_id: str
    project_external_id: str
    name: str
    unit_type: str
    bedrooms: int
    area_sqm: Decimal
    total_units: int
    scenario: str


@dataclass(frozen=True, slots=True)
class SeedConfig:
    as_of_date: date
    start_date: date
    end_date: date


@dataclass(slots=True)
class SeedPlan:
    projects: list[dict[str, Any]]
    areas: list[dict[str, Any]]
    units: list[dict[str, Any]]
    deals: list[dict[str, Any]]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "projects": len(self.projects),
            "areas": len(self.areas),
            "units": len(self.units),
            "deals": len(self.deals),
            "sold_deals": sum(row["status"] == "sold" for row in self.deals),
            "reserved_deals": sum(row["status"] == "reserved" for row in self.deals),
            "lost_deals": sum(row["status"] == "lost" for row in self.deals),
            "funnel_deals": sum(row["status"] in FUNNEL_STATUSES for row in self.deals),
        }


PROJECTS = (
    ProjectSpec("demo26-p01", "DEMO 2026 Northlight", -330),
    ProjectSpec("demo26-p02", "DEMO 2026 Rivergate", -270),
    ProjectSpec("demo26-p03", "DEMO 2026 Cedar Point", -210),
    ProjectSpec("demo26-p04", "DEMO 2026 Harbor Row", -120),
)

AREAS = (
    AreaSpec("demo26-p01-a01", "demo26-p01", "Northlight A", "Studio", 0, Decimal("32.00"), 72, "strong"),
    AreaSpec("demo26-p01-a02", "demo26-p01", "Northlight B", "2PN", 2, Decimal("68.50"), 120, "stable"),
    AreaSpec("demo26-p01-a03", "demo26-p01", "Northlight C", "3PN", 3, Decimal("92.00"), 84, "mature"),
    AreaSpec("demo26-p02-a01", "demo26-p02", "Rivergate A", "1PN", 1, Decimal("45.00"), 96, "slow"),
    AreaSpec("demo26-p02-a02", "demo26-p02", "Rivergate B", "2PN", 2, Decimal("70.00"), 144, "noisy"),
    AreaSpec("demo26-p02-a03", "demo26-p02", "Rivergate C", "3PN", 3, Decimal("105.00"), 60, "launching"),
    AreaSpec("demo26-p03-a01", "demo26-p03", "Cedar Point A", "Studio", 0, Decimal("30.00"), 60, "stable"),
    AreaSpec("demo26-p03-a02", "demo26-p03", "Cedar Point B", "2PN", 2, Decimal("66.00"), 108, "slow"),
    AreaSpec("demo26-p03-a03", "demo26-p03", "Cedar Point C", "4PN", 4, Decimal("128.00"), 48, "mature"),
    AreaSpec("demo26-p04-a01", "demo26-p04", "Harbor Row A", "1PN", 1, Decimal("48.00"), 84, "launching"),
    AreaSpec("demo26-p04-a02", "demo26-p04", "Harbor Row B", "2PN", 2, Decimal("74.00"), 132, "noisy"),
    AreaSpec("demo26-p04-a03", "demo26-p04", "Harbor Row C", "3PN", 3, Decimal("110.00"), 54, "strong"),
)

SCENARIO_SOLD_SHARE = {
    "strong": 0.62,
    "stable": 0.46,
    "slow": 0.23,
    "launching": 0.18,
    "mature": 0.72,
    "noisy": 0.39,
}
SCENARIO_RESERVED_SHARE = {
    "strong": 0.08,
    "stable": 0.06,
    "slow": 0.04,
    "launching": 0.10,
    "mature": 0.03,
    "noisy": 0.07,
}
FUNNEL_STATUSES = ("lead", "qualified", "interested", "viewing")


def _stable_int(key: str, low: int, high: int) -> int:
    if low > high:
        raise ValueError("low must not exceed high")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return low + int(digest[:16], 16) % (high - low + 1)


def _uid(kind: str, external_id: str):
    import uuid

    return uuid.uuid5(uuid.UUID("f76e17ab-8d87-4c8c-8d1f-7d3df4b3d2d5"), f"{kind}:{external_id}")


def _utc_timestamp(day: date, hour: int = 9) -> datetime:
    return datetime.combine(day, time(hour=hour, tzinfo=UTC))


def _validate_config(config: SeedConfig) -> None:
    if config.as_of_date < date(TARGET_YEAR, 1, 1):
        raise ValueError("as-of date must be in or after 2026")
    if config.start_date > config.end_date:
        raise ValueError("start date must not be after end date")
    if config.end_date > config.as_of_date:
        raise ValueError("end date must not be after as-of date")
    if config.start_date > date(TARGET_YEAR, 12, 31) or config.end_date < date(TARGET_YEAR, 1, 1):
        raise ValueError("the seed window must overlap calendar year 2026")


def _target_window(config: SeedConfig, spec: AreaSpec, project: ProjectSpec) -> tuple[date, date]:
    start = max(config.start_date, date(TARGET_YEAR, 1, 1))
    end = min(config.end_date, date(TARGET_YEAR, 12, 31))
    launch = config.as_of_date + timedelta(days=project.launch_offset_days)
    if spec.scenario == "launching":
        launch = max(launch, date(TARGET_YEAR, 4, 1))
    start = max(start, launch + timedelta(days=7))
    if start > end:
        raise ValueError(f"area {spec.external_id} has no valid 2026 sale window")
    return start, end


def _sale_date(spec: AreaSpec, rank: int, total: int, start: date, end: date) -> date:
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
    unit_id,
    status: str,
    source_status: str,
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
        "source_status": source_status,
        "reserved_at": reserved_at,
        "sold_at": sold_at,
        "lost_at": lost_at,
        "source_revision": 1,
        "source_updated_at": latest,
        "deleted_at": None,
        "created_at": created_at,
        "updated_at": latest,
    }


def build_plan(config: SeedConfig, *, project_filter: str | None = None, area_filter: str | None = None) -> SeedPlan:
    """Build deterministic rows without opening a database connection."""
    _validate_config(config)
    project_map = {spec.external_id: spec for spec in PROJECTS}
    selected_areas = [spec for spec in AREAS if not project_filter or spec.project_external_id == project_filter]
    if area_filter:
        selected_areas = [spec for spec in selected_areas if spec.external_id == area_filter]
    if project_filter and project_filter not in project_map:
        raise ValueError(f"unknown project '{project_filter}'")
    if area_filter and not selected_areas:
        raise ValueError(f"unknown area '{area_filter}'")

    selected_project_ids = {spec.project_external_id for spec in selected_areas}
    if project_filter:
        selected_project_ids.add(project_filter)
    as_of_ts = _utc_timestamp(config.as_of_date, 18)
    projects_rows: list[dict[str, Any]] = []
    for spec in PROJECTS:
        if spec.external_id not in selected_project_ids:
            continue
        launch_date = config.as_of_date + timedelta(days=spec.launch_offset_days)
        projects_rows.append(
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

    areas_rows: list[dict[str, Any]] = []
    units_rows: list[dict[str, Any]] = []
    deals_rows: list[dict[str, Any]] = []
    for spec in selected_areas:
        project = project_map[spec.project_external_id]
        launch_date = config.as_of_date + timedelta(days=project.launch_offset_days)
        created_at = _utc_timestamp(max(launch_date, config.start_date))
        areas_rows.append(
            {
                "id": _uid("area", spec.external_id),
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
        sold_count = max(1, round(sellable_units * SCENARIO_SOLD_SHARE[spec.scenario]))
        reserved_count = min(
            max(1, round(sellable_units * SCENARIO_RESERVED_SHARE[spec.scenario])),
            max(0, sellable_units - sold_count),
        )
        sale_start, sale_end = _target_window(config, spec, project)
        unit_specs = []
        for index in range(spec.total_units):
            external_unit_id = f"{spec.external_id}-u{index + 1:04d}"
            unit_specs.append((external_unit_id, _uid("unit", external_unit_id)))
        sold_ids = {unit_specs[i][0] for i in sorted(range(sellable_units), key=lambda i: _stable_int(f"sold:{spec.external_id}:{i}", 0, 1_000_000))[:sold_count]}
        remaining_ids = [external_id for external_id, _ in unit_specs if external_id not in sold_ids]
        reserved_ids = set(remaining_ids[:reserved_count])
        blocked_ids = {external_id for external_id, _ in unit_specs[sellable_units:]}
        sold_order = sorted(sold_ids, key=lambda value: _stable_int(f"order:{spec.external_id}:{value}", 0, 1_000_000))
        unit_created_at = _utc_timestamp(max(launch_date, sale_start - timedelta(days=30)))
        for external_unit_id, unit_id in unit_specs:
            status = "blocked" if external_unit_id in blocked_ids else "sold" if external_unit_id in sold_ids else "reserved" if external_unit_id in reserved_ids else "available"
            units_rows.append(
                {
                    "id": unit_id,
                    "source_system": SOURCE_SYSTEM,
                    "source_instance_id": SOURCE_INSTANCE_ID,
                    "external_unit_id": external_unit_id,
                    "area_id": _uid("area", spec.external_id),
                    "unit_code": external_unit_id.upper(),
                    "unit_type": spec.unit_type,
                    "status": status,
                    "source_revision": 1,
                    "source_updated_at": as_of_ts,
                    "deleted_at": None,
                    "created_at": unit_created_at,
                    "updated_at": as_of_ts,
                }
            )
        for rank, external_unit_id in enumerate(sold_order):
            sold_at = _utc_timestamp(_sale_date(spec, rank, len(sold_order), sale_start, sale_end), _stable_int(f"sold-hour:{external_unit_id}", 9, 17))
            reserved_at = max(unit_created_at, sold_at - timedelta(days=_stable_int(f"reserved:{external_unit_id}", 5, 30)))
            deals_rows.append(_deal_row(f"{external_unit_id}-deal-sold", _uid("unit", external_unit_id), "sold", "sold", unit_created_at, reserved_at=reserved_at, sold_at=sold_at))
        for external_unit_id in sorted(reserved_ids):
            reserved_at = _utc_timestamp(max(sale_start, sale_end - timedelta(days=_stable_int(f"hold:{external_unit_id}", 0, 30))), 10)
            deals_rows.append(_deal_row(f"{external_unit_id}-deal-reserved", _uid("unit", external_unit_id), "reserved", "reserved", unit_created_at, reserved_at=reserved_at))
        unsold_for_history = [value for value in remaining_ids if value not in reserved_ids and value not in blocked_ids]
        lost_count = min(len(unsold_for_history), max(1, round(sold_count * 0.08)))
        for external_unit_id in sorted(unsold_for_history)[:lost_count]:
            lost_at = _utc_timestamp(max(sale_start, sale_end - timedelta(days=_stable_int(f"lost:{external_unit_id}", 10, 75))), 11)
            deals_rows.append(_deal_row(f"{external_unit_id}-deal-lost", _uid("unit", external_unit_id), "lost", "cancelled" if lost_count % 3 == 0 else "lost", unit_created_at, reserved_at=max(unit_created_at, lost_at - timedelta(days=7)), lost_at=lost_at))
        for index, external_unit_id in enumerate(unsold_for_history[lost_count:]):
            if index % 5 != 0:
                continue
            status = FUNNEL_STATUSES[_stable_int(f"funnel:{external_unit_id}", 0, len(FUNNEL_STATUSES) - 1)]
            deals_rows.append(_deal_row(f"{external_unit_id}-deal-funnel", _uid("unit", external_unit_id), status, status, unit_created_at, updated_at=as_of_ts))
    return SeedPlan(projects_rows, areas_rows, units_rows, deals_rows)


def _target_metadata(
    database_dsn: str,
    *,
    classification: str | None = None,
    app_environment: str | None = None,
) -> dict[str, str]:
    url = make_url(database_dsn)
    name = url.database or ""
    app_environment = (app_environment or os.getenv("APP_ENV") or "").strip().lower()
    if app_environment in {"production", "staging"}:
        raise RuntimeError("refusing seed: APP_ENV is production-like")
    classification = (classification or os.getenv("SEED_ENVIRONMENT") or os.getenv("APP_ENV") or "").strip().lower()
    if classification not in {"development", "demo", "test"}:
        raise RuntimeError("refusing seed: SEED_ENVIRONMENT/APP_ENV must explicitly be development, demo, or test")
    if classification == "test" and not name.endswith("_test"):
        raise RuntimeError("refusing seed: test target database must end with _test")
    lowered = name.lower()
    if any(marker in lowered for marker in ("prod", "production", "live", "minicrm")):
        raise RuntimeError("refusing seed: target database name is production-like or belongs to Mini CRM")
    return {
        "classification": classification,
        "database": name,
        "host": url.host or "(local socket)",
        "port": str(url.port or 5432),
    }


def _upsert(table: sa.Table, rows: list[dict[str, Any]]) -> sa.Executable:
    if not rows:
        raise ValueError("cannot build an upsert without rows")
    insert = pg_insert(table).values(rows)
    update_values = {column.name: getattr(insert.excluded, column.name) for column in table.columns if column.name != "id"}
    return insert.on_conflict_do_update(index_elements=[table.c.id], set_=update_values)


def _delete_statements(plan: SeedPlan, *, delete_projects: bool) -> list[sa.Executable]:
    statements = [
        sa.delete(deals).where(deals.c.id.in_([row["id"] for row in plan.deals])),
        sa.delete(units).where(units.c.id.in_([row["id"] for row in plan.units])),
        sa.delete(areas).where(areas.c.id.in_([row["id"] for row in plan.areas])),
    ]
    if delete_projects:
        statements.append(sa.delete(projects).where(projects.c.id.in_([row["id"] for row in plan.projects])))
    return statements


async def _write(plan: SeedPlan, database_dsn: str, *, reset: bool, delete_projects: bool) -> None:
    engine = create_async_engine(database_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            if reset:
                for statement in _delete_statements(plan, delete_projects=delete_projects):
                    await connection.execute(statement)
            for table, rows in ((projects, plan.projects), (areas, plan.areas), (units, plan.units), (deals, plan.deals)):
                if rows:
                    await connection.execute(_upsert(table, rows))
    finally:
        await engine.dispose()


def _print_report(metadata: dict[str, str], config: SeedConfig, plan: SeedPlan, *, dry_run: bool) -> None:
    print("=== synthetic domain seed ===")
    print(f"target: {metadata['classification']} / {metadata['database']} / {metadata['host']}:{metadata['port']}")
    print(f"window: {config.start_date.isoformat()} .. {config.end_date.isoformat()} (as-of {config.as_of_date.isoformat()})")
    print(f"mode: {'dry-run' if dry_run else 'write'}")
    for key, value in plan.counts.items():
        print(f"{key}: {value}")
    print("source: synthetic_demo / synthetic-demo-2026")
    print("note: synthetic demo/test data; not real market data")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _default_config() -> SeedConfig:
    as_of = _parse_date(os.getenv("SEED_AS_OF_DATE", date.today().isoformat()))
    start = _parse_date(os.getenv("SEED_START_DATE", (as_of - timedelta(days=365)).isoformat()))
    end = _parse_date(os.getenv("SEED_END_DATE", as_of.isoformat()))
    return SeedConfig(as_of, start, end)


def main(argv: list[str] | None = None) -> int:
    defaults = _default_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="validate target and show counts without opening a DB connection")
    parser.add_argument("--project", help="limit the seed to a project external id")
    parser.add_argument("--area", help="limit the seed to an area external id")
    parser.add_argument("--as-of-date", type=_parse_date, default=defaults.as_of_date)
    parser.add_argument("--start-date", type=_parse_date, default=defaults.start_date)
    parser.add_argument("--end-date", type=_parse_date, default=defaults.end_date)
    reset_group = parser.add_mutually_exclusive_group()
    reset_group.add_argument("--reset-demo-data", action="store_true", help="delete only this seed namespace before upserting")
    reset_group.add_argument("--no-reset", action="store_true", help="explicitly select the non-destructive default")
    parser.add_argument("--confirm-reset-demo-data", action="store_true", help="required together with --reset-demo-data")
    args = parser.parse_args(argv)
    if args.reset_demo_data and not args.confirm_reset_demo_data:
        parser.error("--reset-demo-data requires --confirm-reset-demo-data")
    if args.confirm_reset_demo_data and not args.reset_demo_data:
        parser.error("--confirm-reset-demo-data requires --reset-demo-data")
    config = SeedConfig(args.as_of_date, args.start_date, args.end_date)
    plan = build_plan(config, project_filter=args.project, area_filter=args.area)
    from src.config import get_settings

    metadata = _target_metadata(
        get_settings().database_dsn,
        classification=os.getenv("SEED_ENVIRONMENT") or get_settings().app_env,
        app_environment=get_settings().app_env,
    )
    _print_report(metadata, config, plan, dry_run=args.dry_run)
    if args.dry_run:
        return 0
    asyncio.run(
        _write(
            plan,
            get_settings().database_dsn,
            reset=args.reset_demo_data,
            delete_projects=not bool(args.area),
        )
    )
    print("status: committed in one transaction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
