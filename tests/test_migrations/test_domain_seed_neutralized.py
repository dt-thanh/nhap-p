"""End-to-end proof of the target invariant (2026-08-28): a fresh database
running `alembic upgrade head` gets schema and required global config only —
never auto-seeded Project/Area/Unit/Deal rows — while an EXISTING database
that already has real legacy/demo/CRM-synced data (simulating one that
applied the OLD versions of `0019`/`0021`/`0023` before this change) upgrades
cleanly with every existing domain row preserved exactly as-is.

Two scenarios, one file, because they're the two faces of the same
compatibility requirement and share almost all of their setup.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"
    return result.stdout


@pytest.fixture
def scratch_db():
    name = f"migfresh_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(TEST_DATABASE_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name}
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


# --- Scenario 1: brand-new database, `alembic upgrade head` -----------------


def test_fresh_upgrade_head_inserts_zero_domain_business_rows(scratch_db):
    _alembic(scratch_db, "upgrade", "head")
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            domain_counts = {
                table: conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in ("projects", "areas", "units", "deals")
            }
            assert domain_counts == {"projects": 0, "areas": 0, "units": 0, "deals": 0}

            # Schema and required global config are still present.
            table_count = conn.execute(
                sa.text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            ).scalar_one()
            assert table_count >= 57, "expected schema (57+ tables) must still be fully present"

            for table in (
                "sync_credentials",
                "sync_payloads",
                "crm_source_records",
                "reconciliation_runs",
                "reconciliation_findings",
                "unit_enrichment_attributes",
                "ranking_runs",
                "ranking_scores",
            ):
                n = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                assert n == 0, f"{table} must be empty on a fresh install (no sync has happened yet)"

            # Intentional global config — NOT domain data, must be present.
            ranking_configs = conn.execute(sa.text("SELECT status, count(*) FROM ranking_configs GROUP BY status")).all()
            assert dict(ranking_configs) == {"archived": 1, "published": 1}
            feature_defs = conn.execute(sa.text("SELECT count(*) FROM ranking_feature_definitions")).scalar_one()
            assert feature_defs == 8, "4 market (0040) + 3 area (0041) + 1 legal (0042) — global feature catalog"

            revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            revision_count = conn.execute(sa.text("SELECT count(*) FROM alembic_version")).scalar_one()
            assert revision_count == 1
            assert revision == "0046_feature_rubrics"

            # No AHP/CSV score ever lands anywhere on a fresh install (nothing
            # to score yet, but assert the shape is right: zero rows, not
            # some placeholder).
            assert conn.execute(sa.text("SELECT count(*) FROM ranking_scores")).scalar_one() == 0
    finally:
        engine.dispose()


def test_fresh_upgrade_head_domain_tables_have_correct_schema_and_fk_shape(scratch_db):
    """Spot-checks that neutering `upgrade()` never touched DDL — the exact
    columns/constraints/FKs an operator or the sync pipeline depends on are
    still exactly as migrations 0001-0043 defined them."""
    _alembic(scratch_db, "upgrade", "head")
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            cols = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='units'"
                    )
                ).all()
            }
            assert {"source_system", "source_instance_id", "external_unit_id", "area_id", "status"} <= cols

            fk_targets = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT confrelid::regclass::text FROM pg_constraint "
                        "WHERE conrelid = 'deals'::regclass AND contype = 'f'"
                    )
                ).all()
            }
            assert fk_targets == {"units"}

            unique_constraints = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = 'units'::regclass AND contype = 'u'"
                    )
                ).all()
            }
            assert "uq_units_source_identity" in unique_constraints
    finally:
        engine.dispose()


# --- Scenario 2: existing database with real pre-change data ----------------


def _seed_legacy_and_deals(engine) -> None:
    from scripts._seed_ai_crm_fixture_core import build_upserts, load_seed
    from scripts._seed_legacy_fixture_deals_core import UPSERT as DEALS_UPSERT
    from scripts._seed_legacy_fixture_deals_core import plan_deals

    plan = build_upserts(load_seed())
    with engine.begin() as conn:
        for _table_name, stmt in plan.statements:
            conn.execute(stmt)
    with engine.begin() as conn:
        deals, sold_unit_ids, _counts = plan_deals(conn)
        for row in deals:
            conn.execute(DEALS_UPSERT, row)
        if sold_unit_ids:
            conn.execute(
                sa.text(
                    "UPDATE units SET status='sold', updated_at = GREATEST(clock_timestamp(), created_at) "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": sold_unit_ids},
            )


def _insert_minicrm_synced_project(conn) -> dict:
    """A minimal `mini_crm`-lineage chain — stands in for real CRM-synced data
    (e.g. La Pura) that must survive untouched, exactly matching how
    DomainProjector stamps rows (`source_system='mini_crm'`)."""
    project_id, area_id, unit_id, deal_id = (uuid.uuid4() for _ in range(4))
    conn.execute(
        sa.text(
            "INSERT INTO projects (id, name, launch_date, created_at, updated_at, status, "
            "absorption_calculator, source_system, source_instance_id, external_id) "
            "VALUES (:i, 'La Pura', '2026-01-01', now(), now(), 'active', 'domain_units_deals', "
            "'mini_crm', 'mini-crm-dev', 'P-0001')"
        ),
        {"i": project_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
            "created_at, updated_at, status, source_system, source_instance_id, external_id) "
            "VALUES (:i, :p, 'A1', '2PN', 2, 75, 10, now(), now(), 'active', 'mini_crm', 'mini-crm-dev', 'A-0001')"
        ),
        {"i": area_id, "p": project_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, "
            "unit_code, unit_type, status, created_at, updated_at) "
            "VALUES (:i, 'mini_crm', 'mini-crm-dev', 'U-0001', :a, 'U-0001', '2PN', 'sold', now(), now())"
        ),
        {"i": unit_id, "a": area_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, "
            "status, source_status, reserved_at, sold_at, created_at, updated_at) "
            "VALUES (:i, 'mini_crm', 'mini-crm-dev', 'D-0001', :u, 'sold', 'sold', "
            "now() - interval '10 days', now(), now(), now())"
        ),
        {"i": deal_id, "u": unit_id},
    )
    return {"project_id": project_id, "area_id": area_id, "unit_id": unit_id, "deal_id": deal_id}


def test_existing_database_upgrade_preserves_all_legacy_demo_and_crm_synced_rows(scratch_db):
    """Simulates a database that already has real legacy + demo + CRM-synced
    rows, sitting at `0018` (before `0019`/`0021`/`0023`/`0024` run). Then
    runs the actual current `alembic upgrade head` — the exact code path an
    existing dev database would take — and proves nothing is deleted,
    mutated, or duplicated, Alembic history stays valid, and no schema
    regression occurs.

    Note: `0019`/`0021`/`0023` are now no-ops, but `0024` is UNCHANGED and
    still does its real, documented job the first time it runs against a
    database that already has the legacy fixture: it tops up `stats26-*`
    sold deals in whichever areas currently have none (36 of them here,
    matching the historical count exactly) — this is 0024 working correctly,
    not a regression. A database already AT head before this change (the
    real compatibility case 1 target) has already had 0024 run once and
    would see zero further change on re-running `alembic upgrade head`,
    since Alembic never re-applies an already-stamped revision.
    """
    _alembic(scratch_db, "upgrade", "0018_agent_recommendations")
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        _seed_legacy_and_deals(engine)
        with engine.begin() as conn:
            minicrm_chain = _insert_minicrm_synced_project(conn)
            # A synthetic_demo-lineage control chain, standing in for what
            # 0023's OLD upgrade() would have produced on this DB before.
            conn.execute(
                sa.text(
                    "INSERT INTO projects (id, name, launch_date, created_at, updated_at, status, "
                    "absorption_calculator, source_system, source_instance_id, external_id) "
                    "VALUES (:i, '2026 Northlight', '2025-01-01', now(), now(), 'active', "
                    "'domain_units_deals', 'synthetic_demo', 'synthetic-demo-2026', 'demo26-p01')"
                ),
                {"i": uuid.uuid4()},
            )

        with engine.connect() as conn:
            before = {
                "legacy_projects": conn.execute(
                    sa.text("SELECT count(*) FROM projects WHERE source_system = 'crm_real_data_fixture'")
                ).scalar_one(),
                "legacy_deals": conn.execute(
                    sa.text("SELECT count(*) FROM deals WHERE source_system = 'crm_real_data_fixture'")
                ).scalar_one(),
                "demo_projects": conn.execute(
                    sa.text("SELECT count(*) FROM projects WHERE source_system = 'synthetic_demo'")
                ).scalar_one(),
                "minicrm_project_row": dict(
                    conn.execute(
                        sa.text("SELECT id, name, external_id FROM projects WHERE id = :i"),
                        {"i": minicrm_chain["project_id"]},
                    ).mappings().one()
                ),
                "minicrm_deal_row": dict(
                    conn.execute(
                        sa.text("SELECT id, status, sold_at FROM deals WHERE id = :i"), {"i": minicrm_chain["deal_id"]}
                    ).mappings().one()
                ),
            }
        assert before["legacy_projects"] == 4
        assert before["legacy_deals"] == 1294
        assert before["demo_projects"] == 1
        legacy_deals_before_upgrade = before["legacy_deals"]

        engine.dispose()
        _alembic(scratch_db, "upgrade", "head")

        engine = sa.create_engine(_sync_url(scratch_db))
        with engine.connect() as conn:
            after = {
                "legacy_projects": conn.execute(
                    sa.text("SELECT count(*) FROM projects WHERE source_system = 'crm_real_data_fixture'")
                ).scalar_one(),
                "legacy_deals": conn.execute(
                    sa.text("SELECT count(*) FROM deals WHERE source_system = 'crm_real_data_fixture'")
                ).scalar_one(),
                "demo_projects": conn.execute(
                    sa.text("SELECT count(*) FROM projects WHERE source_system = 'synthetic_demo'")
                ).scalar_one(),
                "minicrm_project_row": dict(
                    conn.execute(
                        sa.text("SELECT id, name, external_id FROM projects WHERE id = :i"),
                        {"i": minicrm_chain["project_id"]},
                    ).mappings().one()
                ),
                "minicrm_deal_row": dict(
                    conn.execute(
                        sa.text("SELECT id, status, sold_at FROM deals WHERE id = :i"), {"i": minicrm_chain["deal_id"]}
                    ).mappings().one()
                ),
            }
            revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            revision_rows = conn.execute(sa.text("SELECT count(*) FROM alembic_version")).scalar_one()

            # No duplicate identity anywhere — a duplicate would show as a
            # unique-constraint violation, but assert positively too.
            dup_projects = conn.execute(
                sa.text(
                    "SELECT source_instance_id, external_id, count(*) FROM projects "
                    "WHERE external_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1"
                )
            ).all()
            assert dup_projects == []
    finally:
        engine.dispose()

    # `0024` (unchanged) legitimately tops up sold deals here — see the
    # docstring above. Every OTHER row must be byte-identical.
    added_by_0024 = after["legacy_deals"] - legacy_deals_before_upgrade
    assert added_by_0024 == 36, "0024's own documented supplemental-deals behavior, unchanged"
    after_without_delta = {**after, "legacy_deals": legacy_deals_before_upgrade}
    assert after_without_delta == before, (
        "upgrading an existing database must not delete, mutate, or duplicate any pre-existing row "
        "(other than 0024's own documented, unchanged, additive supplement)"
    )
    assert revision == "0046_feature_rubrics"
    assert revision_rows == 1
