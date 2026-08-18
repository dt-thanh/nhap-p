"""Migration 0019 — DEV-only AI/CRM fixture derived from `crm_real_data.json`.

Bốn thứ được canh kỹ nhất:

1. **Idempotent.** Chạy hai lần không nhân đôi bất kỳ bảng nào — id tất định +
   `ON CONFLICT DO UPDATE`.
2. **Downgrade CHỈ xoá đúng dòng mang danh tính fixture**
   (`source_system='crm_real_data_fixture'`, `source_instance_id='ai-dev-fixture'`),
   không đụng dữ liệu KHÔNG phải của fixture — kể cả dữ liệu chèn TRƯỚC khi
   upgrade chạy.
3. **Không chạm bốn bảng xếp hạng** (`ranking_configs`/`ranking_runs`/
   `ranking_scores`/`feature_snapshots`) hay `deals` — đúng ranh giới đã tài
   liệu hoá trong docstring migration.
4. **Khoá ngoại/khoá duy nhất còn nguyên** — 1991 unit thật, không đơn vị nào
   bị rơi thầm lặng qua `ON CONFLICT`.

Chạy trên DATABASE DÙNG MỘT LẦN (`mig19_<hex>_test`), tạo và huỷ trong từng test.
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


# --- 1. Upgrade creates exactly the expected fixture rows -------------------


def test_upgrade_creates_expected_row_counts(upgraded):
    with upgraded["engine"].connect() as conn:
        counts = _counts(conn)
    assert counts == FIXTURE_TABLE_COUNTS


def test_upgrade_does_not_touch_ranking_or_deal_tables(upgraded):
    with upgraded["engine"].connect() as conn:
        # `ranking_configs` KHÔNG rỗng: 0014_ranking_foundation TỰ NÓ seed đúng
        # MỘT config v1 vận hành — đó là dữ liệu của 0014, không phải của 0019.
        # 0019 không được cộng thêm dòng nào vào đây.
        ranking_configs_count = conn.execute(sa.text("SELECT count(*) FROM ranking_configs")).scalar_one()
        assert ranking_configs_count == 1, "0019 không được thêm/xoá dòng nào trong ranking_configs (config v1 của 0014)"
        for table in ("ranking_runs", "ranking_scores", "feature_snapshots", "deals"):
            n = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            assert n == 0, f"{table} phải rỗng — migration 0019 không được ghi vào bảng này"


def test_fixture_units_are_scoped_to_fixture_source_instance(upgraded):
    with upgraded["engine"].connect() as conn:
        distinct = conn.execute(sa.text("SELECT DISTINCT source_system, source_instance_id FROM units")).all()
    assert distinct == [(SOURCE_SYSTEM, SOURCE_INSTANCE_ID)]


# --- 2. Idempotent: running the upsert logic twice does not duplicate -------


def test_running_upserts_twice_does_not_duplicate(upgraded):
    """Gọi lại TRỰC TIẾP `build_upserts` một lần nữa trên CÙNG DB (mô phỏng
    upgrade() chạy hai lần) — Alembic tự nó không cho chạy lại revision đã áp
    dụng, nên đây là cách kiểm tính idempotent THẬT của các câu lệnh SQL."""
    from scripts._seed_ai_crm_fixture_core import build_upserts, load_seed

    data = load_seed()
    plan = build_upserts(data)
    with upgraded["engine"].begin() as conn:
        for _table_name, stmt in plan.statements:
            conn.execute(stmt)

    with upgraded["engine"].connect() as conn:
        counts = _counts(conn)
    assert counts == FIXTURE_TABLE_COUNTS, "chạy lại upsert không được đổi số dòng"


# --- 3. Downgrade scoping -----------------------------------------------------


def test_downgrade_removes_only_fixture_rows(upgraded):
    control_project_id = upgraded["control_project_id"]
    control_area_id = upgraded["control_area_id"]
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
