"""Read-only preview of the approved lineage-scoped AbsorpIQ cleanup.

    python -m scripts.dry_run_lineage_cleanup \\
        --source-system crm_real_data_fixture --source-instance-id ai-dev-fixture

Zero writes. Every operation here is a SELECT. Mirrors, in the exact same
FK-safe order the real cleanup would use, the two migrations that write this
lineage: `alembic/versions/0021_seed_ai_crm_fixture_deals.py::downgrade()`
(deals) and `scripts/_seed_ai_crm_fixture_core.py::build_downgrade_statements()`
(upload_errors/sales_records/inventory_snapshots/absorption_daily/units/
upload_files/areas/projects) — deals must be previewed/deleted first since it
FKs to units, which 0019's own downgrade never had to consider (0019 never
touches `deals`).

Only the one `(source_system, source_instance_id)` pair this tool was audited
and approved for is supported; anything else is refused rather than guessed —
this is a preview for one specific, reviewed cleanup, not a generic
by-lineage data-deletion browser.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.lapura_preflight import (
    ALLOWED_ABSORPTION_DB_NAMES,
    PreflightError,
    PreflightReport,
    check_absorption_target,
    check_app_env,
    resolve_execution_url,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
APPROVED_SOURCE_SYSTEM = "crm_real_data_fixture"
APPROVED_SOURCE_INSTANCE_ID = "ai-dev-fixture"
PREVIEW_ID_LIMIT = 10

# (label, sql, params) — same order a real cleanup would delete in: deals
# first (FKs to units), then 0019's own child-before-parent chain.
_QUERIES = [
    (
        "deals",
        "SELECT id FROM deals WHERE source_system = :s AND source_instance_id = :i ORDER BY id",
    ),
    (
        "upload_errors",
        "SELECT ue.id FROM upload_errors ue JOIN upload_files uf ON uf.id = ue.file_id "
        "WHERE uf.source_system = :s AND uf.source_instance_id = :i ORDER BY ue.id",
    ),
    (
        "sales_records",
        "SELECT sr.id FROM sales_records sr JOIN upload_files uf ON uf.id = sr.file_id "
        "WHERE uf.source_system = :s AND uf.source_instance_id = :i ORDER BY sr.id",
    ),
    (
        "inventory_snapshots",
        "SELECT inv.id FROM inventory_snapshots inv JOIN upload_files uf ON uf.id = inv.file_id "
        "WHERE uf.source_system = :s AND uf.source_instance_id = :i ORDER BY inv.id",
    ),
    (
        "absorption_daily",
        "SELECT ad.id FROM absorption_daily ad JOIN areas a ON a.id = ad.area_id "
        "WHERE a.source_system = :s AND a.source_instance_id = :i ORDER BY ad.id",
    ),
    (
        "units",
        "SELECT id FROM units WHERE source_system = :s AND source_instance_id = :i ORDER BY id",
    ),
    (
        "upload_files",
        "SELECT id FROM upload_files WHERE source_system = :s AND source_instance_id = :i ORDER BY id",
    ),
    (
        "areas",
        "SELECT id FROM areas WHERE source_system = :s AND source_instance_id = :i ORDER BY id",
    ),
    (
        "projects",
        "SELECT id FROM projects WHERE source_system = :s AND source_instance_id = :i ORDER BY id",
    ),
]


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


async def preview(source_system: str, source_instance_id: str) -> dict[str, Any]:
    """Runs every preview query and returns `{table: {"count": n, "ids": [...]}}`.
    Never writes anything — every statement is a SELECT."""
    from src.config import get_settings

    report = PreflightReport()
    await check_app_env(get_settings().app_env, report)
    url = await _resolve_absorption_url(report)
    engine = create_async_engine(url)
    try:
        await check_absorption_target(engine, url, report)
        results: dict[str, Any] = {}
        async with engine.connect() as conn:
            for label, query in _QUERIES:
                rows = (
                    await conn.execute(sa.text(query), {"s": source_system, "i": source_instance_id})
                ).scalars().all()
                results[label] = {"count": len(rows), "ids": [str(r) for r in rows]}
        return results
    finally:
        await engine.dispose()


def _print_report(source_system: str, source_instance_id: str, results: dict[str, Any]) -> None:
    print("=== Lineage-scoped cleanup dry-run (READ-ONLY — zero writes) ===")
    print(f"  source_system={source_system!r}  source_instance_id={source_instance_id!r}")
    print("  FK-safe order (matches the real cleanup's own order):")
    for label, info in results.items():
        ids = info["ids"]
        shown = ", ".join(ids[:PREVIEW_ID_LIMIT])
        more = f" ... +{len(ids) - PREVIEW_ID_LIMIT} more" if len(ids) > PREVIEW_ID_LIMIT else ""
        print(f"  {label}: {info['count']} candidate row(s)")
        if ids:
            print(f"    ids: {shown}{more}")
    print("\n--dry-run: zero writes. No DELETE was ever constructed or executed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-system", required=True)
    parser.add_argument("--source-instance-id", required=True)
    args = parser.parse_args(argv)

    if (args.source_system, args.source_instance_id) != (APPROVED_SOURCE_SYSTEM, APPROVED_SOURCE_INSTANCE_ID):
        print(
            "REFUSED: this dry-run previews exactly one audited, approved lineage "
            f"({APPROVED_SOURCE_SYSTEM!r} / {APPROVED_SOURCE_INSTANCE_ID!r}); got "
            f"({args.source_system!r} / {args.source_instance_id!r}). Refusing to guess at an "
            "unapproved lineage's deletion scope.",
        )
        return 1

    try:
        results = asyncio.run(preview(args.source_system, args.source_instance_id))
    except PreflightError as exc:
        print(f"REFUSED: {exc}")
        return 1

    _print_report(args.source_system, args.source_instance_id, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
