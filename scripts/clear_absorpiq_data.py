"""Safely clear AbsorpIQ business data in the local development database.

This command deliberately does not recreate a database or volume.  It keeps
the schema, Alembic version, local authentication rows, application settings,
and the active MiniCRM sync credential.  The command must be explicit:

    python -m scripts.clear_absorpiq_data --dry-run
    python -m scripts.clear_absorpiq_data --yes

The allowlist is intentionally explicit.  If a future migration adds a public
table, the command stops until its data classification is reviewed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from src.config import get_settings
from src.db import get_engine

EXPECTED_ALEMBIC_HEAD = "0036_remove_historical_ranking"
ALLOWED_APP_ENVS = frozenset({"development"})
ALLOWED_DATABASE_NAMES = frozenset({"absorption", "absorption_dev"})
ALLOWED_DATABASE_HOSTS = frozenset({"db", "localhost", "127.0.0.1", "::1"})

# These tables are operational/auth/configuration data and must survive a
# business-data reset. In particular, sync_credentials contains only the
# hash/prefix of the active MiniCRM credential; the plaintext handoff file is
# outside PostgreSQL. `ranking_configs` is append-only governed policy seeded
# by migrations; clearing it leaves the worker with no published config.
PRESERVED_TABLES = (
    "alembic_version",
    "refresh_tokens",
    "ranking_configs",
    "settings",
    "sync_credentials",
    "users",
)

# Every current public table not in PRESERVED_TABLES is business/pipeline data.
# Keep this list explicit so a new migration cannot silently broaden a
# destructive command.
BUSINESS_TABLES = (
    "absorption_daily",
    "agent_executions",
    "agent_recommendations",
    "alerts",
    "approvals",
    "areas",
    "audit_logs",
    "calculator_comparisons",
    "crm_source_records",
    "deal_status_history",
    "deals",
    "expert_profiles",
    "explanations",
    "feature_snapshots",
    "forecast_jobs",
    "forecast_points",
    "forecasts",
    "inventory_snapshots",
    "llm_calls",
    "project_price_observations",
    "projects",
    "proposals",
    "ranking_config_audit_events",
    "ranking_config_features",
    "ranking_evidence_document_features",
    "ranking_evidence_documents",
    "ranking_explanations",
    "ranking_feature_definitions",
    "ranking_feature_justifications",
    "ranking_feature_lineage",
    "ranking_feature_snapshots",
    "ranking_feature_values",
    "ranking_proposal_reviews",
    "ranking_runs",
    "ranking_scores",
    "ranking_weight_proposals",
    "reconciliation_findings",
    "reconciliation_runs",
    "sales_campaign_units",
    "sales_campaigns",
    "sales_records",
    "suggestions",
    "sync_payloads",
    "unit_status_history",
    "units",
    "upload_errors",
    "upload_files",
    "user_areas",
)

ALL_CLASSIFIED_TABLES = frozenset(PRESERVED_TABLES + BUSINESS_TABLES)


class ClearDataError(RuntimeError):
    """A fail-closed preflight or database error."""


def _guard_runtime() -> tuple[str, str]:
    settings = get_settings()
    if settings.app_env not in ALLOWED_APP_ENVS:
        raise ClearDataError(
            f"TỪ CHỐI: APP_ENV={settings.app_env!r}; chỉ cho phép APP_ENV=development."
        )

    url = make_url(settings.database_dsn)
    host = url.host or ""
    database = url.database or ""
    if host not in ALLOWED_DATABASE_HOSTS:
        raise ClearDataError("TỪ CHỐI: database host không nằm trong allowlist local/dev.")
    if database not in ALLOWED_DATABASE_NAMES:
        raise ClearDataError("TỪ CHỐI: database name không nằm trong allowlist local/dev.")
    if url.port not in (None, 5432):
        raise ClearDataError("TỪ CHỐI: database port không nằm trong allowlist local/dev.")
    return host, database


def _quote_table_names(names: Iterable[str]) -> str:
    return ", ".join(f'"{name}"' for name in names)


async def _public_tables(conn) -> set[str]:
    result = await conn.execute(
        sa.text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    )
    return {row[0] for row in result}


async def _check_schema(conn) -> None:
    tables = await _public_tables(conn)
    missing = ALL_CLASSIFIED_TABLES - tables
    unexpected = tables - ALL_CLASSIFIED_TABLES
    if missing:
        raise ClearDataError(f"TỪ CHỐI: thiếu bảng đã phân loại: {sorted(missing)}")
    if unexpected:
        raise ClearDataError(
            "TỪ CHỐI: phát hiện bảng public chưa được phân loại: "
            + ", ".join(sorted(unexpected))
        )

    revisions = (await conn.execute(sa.text('SELECT version_num FROM "alembic_version"'))).scalars().all()
    if revisions != [EXPECTED_ALEMBIC_HEAD]:
        raise ClearDataError(
            "TỪ CHỐI: Alembic revision không đúng head đã kiểm toán "
            f"({EXPECTED_ALEMBIC_HEAD})."
        )

    # Truncating a child while retaining its parent is safe.  The reverse
    # direction would make this command depend on CASCADE, so fail closed.
    preserved_sql = ", ".join(f"'{name}'" for name in PRESERVED_TABLES)
    business_sql = ", ".join(f"'{name}'" for name in BUSINESS_TABLES)
    result = await conn.execute(
        sa.text(
            "SELECT tc.table_name, ccu.table_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON ccu.constraint_schema = tc.constraint_schema "
            " AND ccu.constraint_name = tc.constraint_name "
            "WHERE tc.constraint_schema = 'public' "
            "  AND tc.constraint_type = 'FOREIGN KEY' "
            f"  AND tc.table_name IN ({preserved_sql}) "
            f"  AND ccu.table_name IN ({business_sql})"
        )
    )
    reverse_dependencies = result.fetchall()
    if reverse_dependencies:
        raise ClearDataError("TỪ CHỐI: bảng được giữ lại tham chiếu business table.")


async def _counts(conn, tables: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(
            (
                await conn.execute(sa.text(f'SELECT count(*) FROM "{table}"'))  # noqa: S608
            ).scalar_one()
        )
    return counts


async def clear(*, confirm: bool) -> dict[str, object]:
    host, database = _guard_runtime()
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await _check_schema(conn)
            before = await _counts(conn, BUSINESS_TABLES)
            preserved_before = await _counts(conn, PRESERVED_TABLES)
            if confirm:
                await conn.execute(
                    sa.text(
                        "TRUNCATE TABLE "
                        + _quote_table_names(BUSINESS_TABLES)
                        + " RESTART IDENTITY"
                    )
                )
                after = await _counts(conn, BUSINESS_TABLES)
                preserved_after = await _counts(conn, PRESERVED_TABLES)
            else:
                after = before.copy()
                preserved_after = preserved_before.copy()
    finally:
        await engine.dispose()

    if confirm and any(after.values()):
        raise ClearDataError("Xóa dữ liệu không hoàn tất: vẫn còn business rows.")
    if preserved_before != preserved_after:
        raise ClearDataError("TỪ CHỐI: preserved tables thay đổi trong thao tác clear.")
    return {
        "host": host,
        "database": database,
        "business_before": before,
        "business_after": after,
        "preserved": preserved_after,
        "confirmed": confirm,
    }


def _print_report(report: dict[str, object]) -> None:
    before = report["business_before"]
    after = report["business_after"]
    preserved = report["preserved"]
    print(
        f"AbsorpIQ dev data {'cleared' if report['confirmed'] else 'dry-run'} "
        f"(database={report['database']}, host={report['host']})"
    )
    print(f"business rows: {sum(before.values())} -> {sum(after.values())}")
    print("preserved rows: " + ", ".join(f"{name}={preserved[name]}" for name in PRESERVED_TABLES))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="perform the destructive business-data clear")
    parser.add_argument("--dry-run", action="store_true", help="validate and report without writing")
    args = parser.parse_args(argv)
    if args.yes and args.dry_run:
        parser.error("--yes and --dry-run are mutually exclusive")
    try:
        report = asyncio.run(clear(confirm=args.yes))
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with one safe message
        print(f"[clear_absorpiq_data] ERROR: {exc}", file=sys.stderr)
        return 1
    _print_report(report)
    if not args.yes:
        print("No writes performed. Re-run with --yes to clear the allowlisted business tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
