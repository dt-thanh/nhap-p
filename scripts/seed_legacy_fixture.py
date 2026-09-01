"""Explicit, confirmed, dev-only CLI for the legacy AI/CRM fixture that used
to be seeded automatically by Alembic migrations `0019_seed_ai_crm_fixture`
and `0021_seed_ai_crm_fixture_deals`. Those migrations' `upgrade()` functions
are now no-ops (Alembic must never auto-seed business/domain data on a fresh
database) — this is the one supported way to create this fixture now.

    python -m scripts.seed_legacy_fixture --dry-run
    python -m scripts.seed_legacy_fixture --confirm-seed

Writes DIRECTLY to AbsorpIQ tables via SQLAlchemy Core (never through Mini
CRM) — the same mechanism the two migrations always used, reusing their exact
mapping logic unchanged (`scripts/_seed_ai_crm_fixture_core.py` for
projects/areas/units/upload_files/upload_errors/sales_records/
inventory_snapshots/absorption_daily, `scripts/_seed_legacy_fixture_deals_
core.py` for deals — extracted from 0021, not duplicated).

Two phases, in that order, because deals are PROJECTED from units already in
the database (not from any JSON of their own):

    1. build_upserts(load_seed())  — projects/areas/units/...
    2. plan_deals(bind)            — reads the units phase 1 just wrote

Every row carries source lineage (`source_system='crm_real_data_fixture'`,
`source_instance_id='ai-dev-fixture'`), deterministic `uuid5` ids (reruns are
idempotent, never duplicate), and is completely disjoint from real Mini
CRM-synced data (`mini-crm-dev` is a different instance id entirely — the
real sync pipeline can never produce or match this fixture's identity).

    *** THIS FIXTURE IS NON-AUTHORITATIVE. *** It is not valid for CRM
    reconciliation and must never be treated as an authoritative ranking
    input. Nothing in `src/ranking/` reads it (see
    `tests/test_ranking/test_unit_enrichment_not_authoritative.py` for the
    analogous structural guard on `unit_enrichment_attributes`, and this
    fixture's own long-standing exclusion from `src/ranking/service.py`'s
    inputs — it only ever populates `projects`/`areas`/`units`/`deals` plus
    legacy `sales_records`/`inventory_snapshots`/`absorption_daily`, never a
    ranking/evidence/config table).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from scripts._seed_ai_crm_fixture_core import SeedError, build_upserts, load_seed
from scripts._seed_legacy_fixture_deals_core import UPSERT as DEALS_UPSERT
from scripts._seed_legacy_fixture_deals_core import plan_deals
from scripts.lapura_preflight import (
    ALLOWED_ABSORPTION_DB_NAMES,
    PreflightError,
    PreflightReport,
    check_absorption_target,
    check_app_env,
    resolve_execution_url,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _resolve_absorption_url(report: PreflightReport) -> str:
    from src.config import get_settings

    return resolve_execution_url(
        get_settings().database_dsn,
        compose_service="db",
        container_port=5432,
        allowed_db_names=ALLOWED_ABSORPTION_DB_NAMES,
        label="AbsorpIQ",
        explicit_override="DATABASE_URL" in os.environ,
        repo_root=REPO_ROOT,
        report=report,
    )


async def _preflight() -> PreflightReport:
    from src.config import get_settings

    report = PreflightReport()
    await check_app_env(get_settings().app_env, report)
    url = await _resolve_absorption_url(report)
    engine = create_async_engine(url)
    try:
        await check_absorption_target(engine, url, report)
    finally:
        await engine.dispose()
    return report


def _print_dry_run_preview() -> None:
    try:
        data = load_seed()
        plan = build_upserts(data)
    except SeedError as exc:
        print(f"REFUSED: {exc}")
        raise SystemExit(1) from None

    print("=== Legacy AI/CRM fixture — phase 1 (projects/areas/units/...) ===")
    for name, n in plan.counts.items():
        print(f"  [{name}] {n}")
    print(
        "\n=== Phase 2 (deals) ===\n"
        "  Deals are PROJECTED from units already in the database — this count "
        "cannot be previewed offline. On a fresh target it will match the historical "
        "figures this fixture has always produced (1294 core deals); on a target that "
        "already has this fixture's units, it reconciles by unit identity instead."
    )
    print(
        "\nsource_system='crm_real_data_fixture'  source_instance_id='ai-dev-fixture'"
        "\n*** NON-AUTHORITATIVE: not valid for CRM reconciliation or authoritative ranking. ***"
    )


async def _confirm_seed() -> int:
    report = await _preflight()
    print(report.as_text())

    try:
        data = load_seed()
        plan = build_upserts(data)
    except SeedError as exc:
        print(f"REFUSED: {exc}")
        return 1

    url = await _resolve_absorption_url(PreflightReport())
    engine = create_async_engine(url)
    try:
        print("\n=== Phase 1: projects/areas/units/upload_files/upload_errors/sales_records/inventory_snapshots/absorption_daily ===")
        async with engine.begin() as conn:
            for _table_name, stmt in plan.statements:
                await conn.execute(stmt)
        for name, n in plan.counts.items():
            print(f"  [{name}] {n} upserted")

        print("\n=== Phase 2: deals (projected from the units phase 1 just wrote) ===")
        async with engine.begin() as conn:
            deals, sold_unit_ids, counts = await conn.run_sync(lambda sync_conn: plan_deals(sync_conn))
            if not deals:
                raise SystemExit(
                    "REFUSED: no fixture units found to project deals from — phase 1 must have failed silently."
                )
            for row in deals:
                await conn.execute(DEALS_UPSERT, row)
            if sold_unit_ids:
                from sqlalchemy import text

                await conn.execute(
                    text(
                        "UPDATE units SET status='sold', "
                        "updated_at = GREATEST(clock_timestamp(), created_at) "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": sold_unit_ids},
                )
        for name, n in counts.items():
            print(f"  [{name}] {n} deals")
        print(f"  [units->sold] {len(sold_unit_ids)} units flipped available->sold")
        print(f"  [total] {len(deals)} deals upserted")
    finally:
        await engine.dispose()

    print(
        "\nsource_system='crm_real_data_fixture'  source_instance_id='ai-dev-fixture'"
        "\n*** NON-AUTHORITATIVE: not valid for CRM reconciliation or authoritative ranking. ***"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="validate the fixture source and show phase-1 counts; zero writes")
    mode.add_argument("--confirm-seed", action="store_true", help="the only write path — requires APP_ENV=development and an allowlisted target")
    args = parser.parse_args(argv)

    if args.dry_run:
        _print_dry_run_preview()
        return 0

    try:
        return asyncio.run(_confirm_seed())
    except PreflightError as exc:
        print(f"REFUSED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
