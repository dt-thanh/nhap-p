"""Migration 0021 `downgrade()` — UNCHANGED (2026-08-28) even though
`upgrade()` is now a no-op. This is the exact scoped-deletion logic the
approved lineage-scoped AbsorpIQ cleanup already relies on
(`crm_real_data_fixture` / `ai-dev-fixture`, 1,330 deals: 1,294 core + 36
`stats26-*` supplement from migration 0024).

Since `upgrade()` no longer creates any data, every test here first seeds the
database directly through the still-unchanged, still-reusable core modules
(`scripts/_seed_ai_crm_fixture_core.py` for projects/areas/units,
`scripts/_seed_legacy_fixture_deals_core.py` for deals — extracted from this
migration's old `upgrade()` body, not duplicated) — exactly what
`python -m scripts.seed_legacy_fixture --confirm-seed` does, and exactly what
an EXISTING database that already applied the OLD version of this revision
(before this change) already has. This makes every test here double as a
fresh-migration-strategy compatibility proof, not just a downgrade unit test.

`downgrade()` is then driven DIRECTLY — never via the Alembic downgrade CLI,
which would also reverse every migration after 0021 — by loading the
migration module from its file path and monkeypatching its module-level `op`
name with a minimal stub exposing only `.get_bind()`, the one thing
`downgrade()` calls on it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "alembic" / "versions" / "0021_seed_ai_crm_fixture_deals.py"

SOURCE_SYSTEM = "crm_real_data_fixture"
SOURCE_INSTANCE_ID = "ai-dev-fixture"
EXPECTED_TOTAL_DEALS = 1294  # via the core modules directly; the extra 36 `stats26-*`
# supplemental deals come from migration 0024's own follow-up logic, which this
# test suite does not additionally replay — see pipeline_status.md.

SYNTHETIC_SOURCE_SYSTEM = "synthetic_demo"
SYNTHETIC_SOURCE_INSTANCE_ID = "synthetic-demo-2026"


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


class _FakeOp:
    """`downgrade()` calls exactly one thing on `op`: `op.get_bind()`. This
    stub is the entire surface needed to drive it directly."""

    def __init__(self, bind):
        self._bind = bind

    def get_bind(self):
        return self._bind


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("_test_migration_0021", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_downgrade(conn) -> None:
    mod = _load_migration_module()
    mod.op = _FakeOp(conn)
    mod.downgrade()


@pytest.fixture
def scratch_db():
    name = f"mig21_{uuid.uuid4().hex[:12]}_test"
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


@pytest.fixture
def head_db(scratch_db):
    """Migrated all the way to `head`. `upgrade()` is a no-op everywhere now,
    so this starts fully schema-present but domain-empty — each test seeds
    what it needs directly through the reusable core modules."""
    _alembic(scratch_db, "upgrade", "head")
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        yield {"url": scratch_db, "engine": engine}
    finally:
        engine.dispose()


def _seed_real_fixture(engine) -> dict:
    """Seeds the FULL legacy fixture (projects/areas/units via 0019's core
    module, then deals via 0021's extracted core module) directly against
    `engine` — exactly what `python -m scripts.seed_legacy_fixture
    --confirm-seed` does, and exactly what an existing pre-change database
    already has. Returns the deal-plan's counts for assertions."""
    from scripts._seed_ai_crm_fixture_core import build_upserts, load_seed
    from scripts._seed_legacy_fixture_deals_core import UPSERT as DEALS_UPSERT
    from scripts._seed_legacy_fixture_deals_core import plan_deals

    plan = build_upserts(load_seed())
    with engine.begin() as conn:
        for _table_name, stmt in plan.statements:
            conn.execute(stmt)

    with engine.begin() as conn:
        deals, sold_unit_ids, counts = plan_deals(conn)
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
    return counts


def _insert_chain(conn, *, source_system: str, source_instance_id: str, key: str, external_deal_id: str | None = None):
    """One project/area/unit/deal chain under the given lineage, unit+deal
    both `sold`. Returns every id so callers can assert on them precisely."""
    project_id, area_id, unit_id, deal_id = (uuid.uuid4() for _ in range(4))
    conn.execute(
        sa.text(
            "INSERT INTO projects (id, name, launch_date, created_at, updated_at, status, "
            "absorption_calculator, source_system, source_instance_id, external_id) "
            "VALUES (:i, :n, '2026-01-01', now(), now(), 'active', 'legacy_aggregate', :s, :inst, :ext)"
        ),
        {"i": project_id, "n": f"CONTROL {key}", "s": source_system, "inst": source_instance_id, "ext": f"{key}-proj"},
    )
    conn.execute(
        sa.text(
            "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
            "created_at, updated_at, status, source_system, source_instance_id, external_id) "
            "VALUES (:i, :p, :n, '2PN', 2, 75, 10, now(), now(), 'active', :s, :inst, :ext)"
        ),
        {"i": area_id, "p": project_id, "n": f"CONTROL {key} area", "s": source_system, "inst": source_instance_id, "ext": f"{key}-area"},
    )
    conn.execute(
        sa.text(
            "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, "
            "unit_code, unit_type, status, created_at, updated_at) "
            "VALUES (:i, :s, :inst, :ext, :a, :code, '2PN', 'sold', now(), now())"
        ),
        {"i": unit_id, "s": source_system, "inst": source_instance_id, "ext": f"{key}-unit", "a": area_id, "code": f"{key}-U01"},
    )
    ext_deal = external_deal_id or f"D-{key}-unit-01"
    conn.execute(
        sa.text(
            "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, "
            "status, source_status, reserved_at, sold_at, created_at, updated_at) "
            "VALUES (:i, :s, :inst, :ext, :u, 'sold', 'sold', now() - interval '10 days', now(), now(), now())"
        ),
        {"i": deal_id, "s": source_system, "inst": source_instance_id, "ext": ext_deal, "u": unit_id},
    )
    return {"project_id": project_id, "area_id": area_id, "unit_id": unit_id, "deal_id": deal_id, "external_deal_id": ext_deal}


def _target_deal_counts(conn) -> int:
    return conn.execute(
        sa.text("SELECT count(*) FROM deals WHERE source_system = :s AND source_instance_id = :i"),
        {"s": SOURCE_SYSTEM, "i": SOURCE_INSTANCE_ID},
    ).scalar_one()


# --- 0. upgrade() is a no-op; the reusable core still produces real data ----


def test_upgrade_writes_zero_deals_but_the_core_module_still_works_directly(head_db):
    engine = head_db["engine"]
    with engine.connect() as conn:
        assert _target_deal_counts(conn) == 0, "upgrade() must be a no-op"

    counts = _seed_real_fixture(engine)
    assert counts["sold"] + counts["reserved"] + counts["lost"] + counts["funnel"] == EXPECTED_TOTAL_DEALS
    with engine.connect() as conn:
        assert _target_deal_counts(conn) == EXPECTED_TOTAL_DEALS


# --- 1. Exact deletion count -------------------------------------------------


def test_downgrade_deletes_exactly_the_target_lineage_deals(head_db):
    engine = head_db["engine"]
    _seed_real_fixture(engine)
    with engine.connect() as conn:
        before = _target_deal_counts(conn)
    assert before == EXPECTED_TOTAL_DEALS

    with engine.begin() as conn:
        _run_downgrade(conn)

    with engine.connect() as conn:
        after = _target_deal_counts(conn)
    assert after == 0


# --- 2. Preserves every other lineage and every protected table -------------


def test_downgrade_preserves_other_lineages_and_protected_tables(head_db):
    engine = head_db["engine"]
    _seed_real_fixture(engine)

    with engine.begin() as conn:
        minicrm_ctrl = _insert_chain(conn, source_system="mini_crm", source_instance_id="mini-crm-dev", key="lapura-like")
        synthetic_ctrl = _insert_chain(
            conn, source_system=SYNTHETIC_SOURCE_SYSTEM, source_instance_id=SYNTHETIC_SOURCE_INSTANCE_ID, key="demo-ctrl"
        )
        # Same NAME (source_system string) as the target lineage, DIFFERENT
        # source_instance_id — proves the predicate matches BOTH columns
        # together (AND), never source_system alone.
        samename_ctrl = _insert_chain(
            conn, source_system=SOURCE_SYSTEM, source_instance_id="ai-dev-fixture-OTHER", key="samename"
        )
        cred_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO sync_credentials (id, source_system, source_instance_id, key_prefix, key_hash, "
                "label, created_at) VALUES (:i, 'mini_crm', 'mini-crm-dev', 'testpfx1', "
                "'0000000000000000000000000000000000000000000000000000000000000000', 'test control', now())"
            ),
            {"i": cred_id},
        )
        upload_file_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO upload_files (id, status, rows_ok, rows_failed, uploaded_at, source_system, "
                "source_instance_id, input_format, transport_mode, sync_mode, schema_version, rows_received) "
                "VALUES (:i, 'completed', 0, 0, now(), 'mini_crm', 'mini-crm-dev', 'csv', 'api_push', "
                "'full_snapshot', 1, 0)"
            ),
            {"i": upload_file_id},
        )
        source_record_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO crm_source_records (id, source_system, source_instance_id, source_entity, "
                "source_record_id, first_sync_run_id, last_sync_run_id, payload_hash, state, last_decision, "
                "conflict_count, first_seen_at, last_seen_at) "
                "VALUES (:i, 'mini_crm', 'mini-crm-dev', 'projects', 'CONTROL-P-0001', :f, :f, 'deadbeef', "
                "'active', 'insert', 0, now(), now())"
            ),
            {"i": source_record_id, "f": upload_file_id},
        )
        ranking_run_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO ranking_runs (id, project_id, trigger, status, enqueued_at, started_at, finished_at) "
                "VALUES (:i, :p, 'manual', 'completed', now(), now(), now())"
            ),
            {"i": ranking_run_id, "p": minicrm_ctrl["project_id"]},
        )
        config_version_id = conn.execute(sa.text("SELECT id FROM ranking_configs LIMIT 1")).scalar_one()
        ranking_score_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO ranking_scores (id, unit_id, area_id, project_id, ranking_run_id, "
                "config_version_id, score, rank_in_area, rank_in_project, weight_coverage, computed_at) "
                "VALUES (:i, :u, :a, :p, :r, :c, 0.5, 1, 1, 1.0, now())"
            ),
            {
                "i": ranking_score_id,
                "u": minicrm_ctrl["unit_id"],
                "a": minicrm_ctrl["area_id"],
                "p": minicrm_ctrl["project_id"],
                "r": ranking_run_id,
                "c": config_version_id,
            },
        )
        enrichment_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO unit_enrichment_attributes (id, unit_id, is_synthetic, source_system, "
                "source_file, source_file_sha256, source_row_key, import_batch_id, imported_at, "
                "created_at, updated_at) "
                "VALUES (:i, :u, true, 'test_control', 'control.csv', 'deadbeef', 'row-1', 'batch-1', "
                "now(), now(), now())"
            ),
            {"i": enrichment_id, "u": minicrm_ctrl["unit_id"]},
        )
        before = _snapshot(conn)

    with engine.begin() as conn:
        _run_downgrade(conn)

    with engine.connect() as conn:
        after = _snapshot(conn)

    assert after == before, "downgrade must not change ANY row outside the target lineage"

    with engine.connect() as conn:
        for ctrl, label in (
            (minicrm_ctrl, "mini_crm"),
            (synthetic_ctrl, "synthetic_demo"),
            (samename_ctrl, "same-name-different-instance"),
        ):
            for table, col in (("projects", "project_id"), ("areas", "area_id"), ("units", "unit_id"), ("deals", "deal_id")):
                n = conn.execute(sa.text(f"SELECT count(*) FROM {table} WHERE id = :i"), {"i": ctrl[col]}).scalar_one()
                assert n == 1, f"{label} control row in {table} must survive downgrade"
        assert conn.execute(sa.text("SELECT count(*) FROM sync_credentials WHERE id = :i"), {"i": cred_id}).scalar_one() == 1
        assert (
            conn.execute(sa.text("SELECT count(*) FROM crm_source_records WHERE id = :i"), {"i": source_record_id}).scalar_one()
            == 1
        )
        assert conn.execute(sa.text("SELECT count(*) FROM ranking_runs WHERE id = :i"), {"i": ranking_run_id}).scalar_one() == 1
        assert (
            conn.execute(sa.text("SELECT count(*) FROM ranking_scores WHERE id = :i"), {"i": ranking_score_id}).scalar_one()
            == 1
        )
        assert (
            conn.execute(sa.text("SELECT count(*) FROM unit_enrichment_attributes WHERE id = :i"), {"i": enrichment_id}).scalar_one()
            == 1
        )


def _snapshot(conn) -> dict:
    protected_tables = (
        "sync_credentials",
        "crm_source_records",
        "sync_payloads",
        "ranking_configs",
        "ranking_feature_definitions",
        "reconciliation_runs",
        "reconciliation_findings",
    )
    out = {t: conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar_one() for t in protected_tables}
    out["alembic_version"] = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    for lineage_label, source_system in (("mini_crm", "mini_crm"), ("synthetic_demo", SYNTHETIC_SOURCE_SYSTEM)):
        out[f"projects_{lineage_label}"] = conn.execute(
            sa.text("SELECT count(*) FROM projects WHERE source_system = :s"), {"s": source_system}
        ).scalar_one()
    out["deals_samename_other_instance"] = conn.execute(
        sa.text("SELECT count(*) FROM deals WHERE source_system = :s AND source_instance_id = 'ai-dev-fixture-OTHER'"),
        {"s": SOURCE_SYSTEM},
    ).scalar_one()
    out["ranking_runs"] = conn.execute(sa.text("SELECT count(*) FROM ranking_runs")).scalar_one()
    out["ranking_scores"] = conn.execute(sa.text("SELECT count(*) FROM ranking_scores")).scalar_one()
    out["unit_enrichment_attributes"] = conn.execute(sa.text("SELECT count(*) FROM unit_enrichment_attributes")).scalar_one()
    return out


# --- 3. Unit-status reversion is scoped to fixture units only ---------------


def test_downgrade_reverts_only_fixture_units_not_control_units(head_db):
    engine = head_db["engine"]
    _seed_real_fixture(engine)
    with engine.begin() as conn:
        ctrl = _insert_chain(conn, source_system="mini_crm", source_instance_id="mini-crm-dev", key="unit-status")
        fixture_sold_unit = conn.execute(
            sa.text(
                "SELECT unit_id FROM deals WHERE source_system = :s AND source_instance_id = :i "
                "AND status = 'sold' LIMIT 1"
            ),
            {"s": SOURCE_SYSTEM, "i": SOURCE_INSTANCE_ID},
        ).scalar_one()

    with engine.begin() as conn:
        _run_downgrade(conn)

    with engine.connect() as conn:
        control_status = conn.execute(sa.text("SELECT status FROM units WHERE id = :i"), {"i": ctrl["unit_id"]}).scalar_one()
        fixture_status = conn.execute(sa.text("SELECT status FROM units WHERE id = :i"), {"i": fixture_sold_unit}).scalar_one()
    assert control_status == "sold", "a control (mini_crm) unit's status must never be touched by 0021's downgrade"
    assert fixture_status == "available", "a fixture unit's status must be reverted by 0021's downgrade"


# --- 4. Atomicity: a forced mid-transaction failure rolls back everything ---


def test_downgrade_is_atomic_rolls_back_fully_on_forced_failure(head_db):
    engine = head_db["engine"]
    _seed_real_fixture(engine)
    with engine.begin() as conn:
        before = _target_deal_counts(conn)
        fixture_sold_unit = conn.execute(
            sa.text(
                "SELECT unit_id FROM deals WHERE source_system = :s AND source_instance_id = :i "
                "AND status = 'sold' LIMIT 1"
            ),
            {"s": SOURCE_SYSTEM, "i": SOURCE_INSTANCE_ID},
        ).scalar_one()
        conn.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION _test_block_0021_delete() RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'forced failure for atomicity test';
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TRIGGER _test_block_0021_delete_trigger
                BEFORE DELETE ON deals
                FOR EACH ROW
                WHEN (OLD.source_system = 'crm_real_data_fixture' AND OLD.source_instance_id = 'ai-dev-fixture')
                EXECUTE FUNCTION _test_block_0021_delete()
                """
            )
        )

    assert before == EXPECTED_TOTAL_DEALS

    with pytest.raises(sa.exc.DBAPIError, match="forced failure for atomicity test"):
        with engine.begin() as conn:
            _run_downgrade(conn)

    with engine.begin() as conn:
        conn.execute(sa.text("DROP TRIGGER _test_block_0021_delete_trigger ON deals"))
        conn.execute(sa.text("DROP FUNCTION _test_block_0021_delete()"))

    with engine.connect() as conn:
        after = _target_deal_counts(conn)
        unit_status_after = conn.execute(sa.text("SELECT status FROM units WHERE id = :i"), {"i": fixture_sold_unit}).scalar_one()

    assert after == before, "a failed downgrade must leave every target-lineage deal exactly as it was"
    assert unit_status_after == "sold", (
        "the UPDATE that flips units back to 'available' ran in the SAME transaction as the DELETE that "
        "failed — atomicity requires it to have been rolled back too, not just the DELETE"
    )
