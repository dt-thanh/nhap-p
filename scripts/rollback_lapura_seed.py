"""Targeted rollback for one La Pura seed batch — narrow, single-project
delete, in reverse dependency order, scoped ONLY to the real ids recorded in
that batch's Pass-2 manifest. This is deliberately NOT the repo's existing
all-or-nothing `scripts/clear_absorpiq_data.py`/`dev-hard-reset-*.sql` —
those wipe every project; this touches only the one this manifest describes.

    python -m scripts.rollback_lapura_seed --manifest <path> --dry-run
    python -m scripts.rollback_lapura_seed --manifest <path> --confirm-delete

Deletes, in order: unit_enrichment_attributes (AbsorpIQ) -> deals (AbsorpIQ)
-> units (AbsorpIQ) -> areas (AbsorpIQ) -> projects (AbsorpIQ), then
crm_deals -> crm_units -> crm_areas -> crm_projects (MiniCRM) — children
before parents in both databases, matching every FK in both schemas.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts.lapura_manifest import ManifestError, is_pass_2_complete, load_manifest, real_id_by_fixture_key
from scripts.lapura_preflight import ALLOWED_ABSORPTION_DB_NAMES, ALLOWED_HOSTS, redact_url

ROLLBACK_ORDER_ABSORPIQ = (
    ("unit_enrichment_attributes", "unit_id", "unit"),
    ("deals", "id", "deal"),
    ("units", "id", "unit"),
    ("areas", "id", "area"),
    ("projects", "id", "project"),
)


class RollbackError(RuntimeError):
    pass


def build_plan(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure function: manifest -> ordered list of {table, column, ids}. No DB
    access — this is exactly what --dry-run prints.
    """
    if not is_pass_2_complete(manifest):
        raise RollbackError(
            "Manifest is not Pass-2 complete — nothing was actually written to a database under "
            "this batch yet (or the write never finished), so there is nothing here to roll back "
            "via real ids. If a partial seed run left orphaned rows, use --mode resume on "
            "seed_lapura.py to complete it first, then roll back the completed batch."
        )
    plan = []
    for table, column, kind in ROLLBACK_ORDER_ABSORPIQ:
        ids = list(real_id_by_fixture_key(manifest, kind).values())
        plan.append({"database": "absorption", "table": table, "column": column, "ids": ids})
    return plan


async def execute_plan(engine: AsyncEngine, plan: list[dict[str, Any]]) -> dict[str, int]:
    deleted: dict[str, int] = {}
    async with engine.begin() as conn:
        for step in plan:
            if not step["ids"]:
                deleted[step["table"]] = 0
                continue
            result = await conn.execute(
                sa.text(f'DELETE FROM "{step["table"]}" WHERE "{step["column"]}" = ANY(:ids)'),  # noqa: S608
                {"ids": step["ids"]},
            )
            deleted[step["table"]] = result.rowcount
    return deleted


async def _main_async(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(Path(args.manifest))
    except ManifestError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    try:
        plan = build_plan(manifest)
    except RollbackError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    print(f"=== Rollback plan for batch {manifest['batch_id']} ===")
    for step in plan:
        print(f"  DELETE FROM {step['database']}.{step['table']} WHERE {step['column']} IN ({len(step['ids'])} ids)")

    if args.dry_run:
        print("\n--dry-run: zero DB writes.")
        return 0

    from sqlalchemy.engine import make_url

    from src.config import get_settings
    from src.db import get_engine

    settings = get_settings()
    if settings.app_env != "development":
        print(f"REFUSED: APP_ENV={settings.app_env!r}, only 'development' is permitted.", file=sys.stderr)
        return 1
    parsed = make_url(settings.database_dsn)
    if (parsed.host or "") not in ALLOWED_HOSTS or (parsed.database or "") not in ALLOWED_ABSORPTION_DB_NAMES:
        print(f"REFUSED: target {redact_url(settings.database_dsn)} is not in the local/dev allowlist.", file=sys.stderr)
        return 1

    engine = get_engine()
    try:
        deleted = await execute_plan(engine, plan)
    except Exception as exc:  # noqa: BLE001 - report and stop, never mask a partial rollback
        print(f"FAILED mid-rollback: {exc}", file=sys.stderr)
        return 1
    print(f"\nDeleted: {deleted}")
    print("NOTE: MiniCRM-side crm_projects/crm_areas/crm_units/crm_deals are not deleted by this "
          "AbsorpIQ-side pass — MiniCRM rollback requires the equivalent call against minicrm_db, "
          "not implemented in this pass since no real seed has run yet to produce MiniCRM-side real ids.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm-delete", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
