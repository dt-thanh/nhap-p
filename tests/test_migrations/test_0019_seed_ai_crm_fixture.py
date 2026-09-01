"""Migration 0019 — DEV-only AI/CRM fixture derived from `crm_real_data.json`.

`upgrade()` is now a no-op (2026-08-28) — Alembic must never auto-seed
business/domain data on a fresh database. What's verified here:

1. **`upgrade()` writes nothing.** A brand-new database running through this
   revision (as part of `alembic upgrade head`) ends with zero rows in every
   table this fixture used to populate.
2. **`build_upserts()`/`build_downgrade_statements()` (the reusable core the
   explicit CLI now drives) are unchanged and still idempotent** — calling
   the upsert plan twice does not duplicate rows.
3. **`downgrade()` is UNCHANGED and still correctly scoped** — proven against
   a database that has this fixture's real rows (simulating an EXISTING
   database that already applied the OLD version of this revision, before
   this change): it deletes only fixture-identity rows, never a
   non-fixture row, even one inserted before `downgrade()` runs.
4. Ranking/deal tables are still never touched by this revision.

Runs on a DATABASE DÙNG MỘT LẦN (`mig19_<hex>_test`), created and destroyed
per test.
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

PREVIOUS_REVISION = "0018_agent_recommendations"
REVISION = "0019_seed_ai_crm_fixture"

SOURCE_SYSTEM = "crm_real_data_fixture"
SOURCE_INSTANCE_ID = "ai-dev-fixture"

FIXTURE_TABLE_COUNTS = {
    "projects": 4,
    "areas": 58,
    "units": 1991,
    "sales_records": 58,
    "inventory_snapshots": 58,
    "absorption_daily": 696,
}


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
    name = f"mig19_{uuid.uuid4().hex[:12]}_test"
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
def baseline(scratch_db):
    """DB ở đúng 0018 — TRƯỚC fixture, kèm MỘT dự án/phân khu KHÔNG-phải-fixture
    (danh tính nguồn khác hẳn) để kiểm downgrade không đụng vào nó."""
    _alembic(scratch_db, "upgrade", PREVIOUS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    control_project_id = uuid.uuid4()
    control_area_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, launch_date, created_at, updated_at, status, "
                "absorption_calculator, source_system, source_instance_id) "
                "VALUES (:i, 'HAND-ENTERED — not fixture', '2026-01-01', now(), now(), 'active', "
                "'legacy_aggregate', 'mini_crm', 'mini-crm-dev')"
            ),
            {"i": control_project_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at, updated_at, status, source_system, source_instance_id) "
                "VALUES (:i, :p, 'CONTROL', '2PN', 2, 75, 10, now(), now(), 'active', 'mini_crm', 'mini-crm-dev')"
            ),
            {"i": control_area_id, "p": control_project_id},
        )
    try:
        yield {"url": scratch_db, "engine": engine, "control_project_id": control_project_id, "control_area_id": control_area_id}
    finally:
        engine.dispose()


@pytest.fixture
def upgraded(baseline):
    _alembic(baseline["url"], "upgrade", REVISION)
    try:
        yield baseline
    finally:
        pass


def _counts(conn) -> dict[str, int]:
    out = {}
    for table in FIXTURE_TABLE_COUNTS:
        out[table] = conn.execute(
            sa.text(
                f"SELECT count(*) FROM {table} WHERE source_system = :s AND source_instance_id = :i"
                if table in ("projects", "areas", "units")
                else f"SELECT count(*) FROM {table} r JOIN areas a ON a.id = r.area_id "
                "WHERE a.source_system = :s AND a.source_instance_id = :i"
            ),
            {"s": SOURCE_SYSTEM, "i": SOURCE_INSTANCE_ID},
        ).scalar_one()
    return out


def _apply_upserts_directly(engine) -> None:
    """Simulates an EXISTING database that already applied the OLD version of
    this revision — bypasses the now-neutered `upgrade()` and instead calls
    the still-unchanged, still-reusable core module directly, exactly like
    `python -m scripts.seed_legacy_fixture --confirm-seed` does."""
    from scripts._seed_ai_crm_fixture_core import build_upserts, load_seed

    plan = build_upserts(load_seed())
    with engine.begin() as conn:
        for _table_name, stmt in plan.statements:
            conn.execute(stmt)


# --- 1. upgrade() is now a no-op ---------------------------------------------


def test_upgrade_writes_zero_rows_on_a_fresh_database(upgraded):
    with upgraded["engine"].connect() as conn:
        counts = _counts(conn)
    assert counts == dict.fromkeys(FIXTURE_TABLE_COUNTS, 0), (
        "upgrade() must be a no-op — Alembic must never auto-seed business/domain data"
    )


def test_upgrade_does_not_touch_ranking_or_deal_tables(upgraded):
    with upgraded["engine"].connect() as conn:
        # `ranking_configs` KHÔNG rỗng: 0014_ranking_foundation TỰ NÓ seed đúng
        # MỘT config v1 vận hành — đó là dữ liệu của 0014, không phải của 0019.
        ranking_configs_count = conn.execute(sa.text("SELECT count(*) FROM ranking_configs")).scalar_one()
        assert ranking_configs_count == 1, "0019 không được thêm/xoá dòng nào trong ranking_configs (config v1 của 0014)"
        for table in ("ranking_runs", "ranking_scores", "feature_snapshots", "deals"):
            n = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            assert n == 0, f"{table} phải rỗng — migration 0019 không được ghi vào bảng này"


def test_upgrade_does_not_disturb_a_preexisting_control_row(upgraded):
    """The `upgraded` fixture already carries a `mini_crm` control project —
    proves the no-op doesn't touch pre-existing data of any kind."""
    with upgraded["engine"].connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM projects WHERE id = :i"), {"i": upgraded["control_project_id"]}
        ).scalar_one()
    assert n == 1


# --- 2. The reusable core is unchanged and still idempotent -----------------


def test_build_upserts_is_idempotent_when_run_directly(baseline):
    """Calls `build_upserts()` twice directly against the same DB (mirroring
    two `--confirm-seed` invocations, or one invocation on a DB that already
    has this fixture) — proves the underlying upsert logic the explicit CLI
    now drives is unchanged and still idempotent, independent of Alembic."""
    _apply_upserts_directly(baseline["engine"])
    with baseline["engine"].connect() as conn:
        first = _counts(conn)
    assert first == FIXTURE_TABLE_COUNTS

    _apply_upserts_directly(baseline["engine"])
    with baseline["engine"].connect() as conn:
        second = _counts(conn)
    assert second == FIXTURE_TABLE_COUNTS, "chạy lại upsert không được đổi số dòng"


def test_fixture_units_are_scoped_to_fixture_source_instance(baseline):
    _apply_upserts_directly(baseline["engine"])
    with baseline["engine"].connect() as conn:
        distinct = conn.execute(sa.text("SELECT DISTINCT source_system, source_instance_id FROM units")).all()
    assert distinct == [(SOURCE_SYSTEM, SOURCE_INSTANCE_ID)]


# --- 3. downgrade() is UNCHANGED — proven against a database that already has
# this fixture's real rows (simulating an EXISTING pre-change database). -----


def test_downgrade_removes_only_fixture_rows_from_an_existing_database(upgraded):
    """`upgraded` starts with zero fixture rows (upgrade() is a no-op) — apply
    the core module's upserts directly first to simulate a database that
    already had this fixture BEFORE this change, then prove `downgrade()`
    (unchanged) still scopes correctly against real data, never touching the
    pre-existing control row."""
    control_project_id = upgraded["control_project_id"]
    control_area_id = upgraded["control_area_id"]
    _apply_upserts_directly(upgraded["engine"])
    with upgraded["engine"].connect() as conn:
        assert _counts(conn) == FIXTURE_TABLE_COUNTS

    upgraded["engine"].dispose()
    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            fixture_counts = {}
            for table in ("projects", "areas", "units"):
                fixture_counts[table] = conn.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE source_system = :s AND source_instance_id = :i"),
                    {"s": SOURCE_SYSTEM, "i": SOURCE_INSTANCE_ID},
                ).scalar_one()
            control_project_survives = conn.execute(
                sa.text("SELECT count(*) FROM projects WHERE id = :i"), {"i": control_project_id}
            ).scalar_one()
            control_area_survives = conn.execute(
                sa.text("SELECT count(*) FROM areas WHERE id = :i"), {"i": control_area_id}
            ).scalar_one()
            revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()

    assert fixture_counts == {"projects": 0, "areas": 0, "units": 0}
    assert control_project_survives == 1, "downgrade không được xoá dự án KHÔNG-phải-fixture"
    assert control_area_survives == 1, "downgrade không được xoá phân khu KHÔNG-phải-fixture"
    assert revision == PREVIOUS_REVISION


def test_downgrade_removes_absorption_and_upload_rows_scoped_via_fk(upgraded):
    _apply_upserts_directly(upgraded["engine"])
    upgraded["engine"].dispose()
    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            for table in ("absorption_daily", "sales_records", "inventory_snapshots"):
                n = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                assert n == 0, f"{table} phải rỗng sau downgrade (không còn area fixture nào để trỏ tới)"
            uploads = conn.execute(
                sa.text("SELECT count(*) FROM upload_files WHERE source_system = :s AND source_instance_id = :i"),
                {"s": SOURCE_SYSTEM, "i": SOURCE_INSTANCE_ID},
            ).scalar_one()
            assert uploads == 0
    finally:
        engine.dispose()
