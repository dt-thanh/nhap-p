"""Migration 0003 của Mini CRM: crm_projects, crm_areas, crm_units.area_id.

Chạy trên database DÙNG MỘT LẦN (`mc0003_<hex>_test`), tạo và huỷ trong từng test.

Cùng điều được canh từ 0001/0002: cây Alembic này KHÉP KÍN, của RIÊNG Mini CRM.
`down_revision` trỏ về `0002_minicrm_crud`, không bao giờ trỏ sang một revision
của backend — và ngược lại, không migration nào của BACKEND được phép nối vào
đây (kiểm bằng cách đếm file, không cần database backend).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from tests.conftest import db_url, run_alembic, sync_url, with_database

MINICRM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MINICRM_ROOT.parent
MINICRM_DB_URL = db_url()

pytestmark = pytest.mark.skipif(
    not MINICRM_DB_URL,
    reason="Không có MINICRM_TEST_DATABASE_URL/MINICRM_DATABASE_URL — bỏ qua test cần DB thật",
)

REVISION = "0003_minicrm_hierarchy"
PREVIOUS = "0002_minicrm_crud"

NEW_PROJECT_TABLE_COLUMNS = {
    "id", "external_id", "name", "launch_date", "status", "source_revision",
    "created_at", "updated_at", "mirrored_at", "mirrored_revision", "last_sync_batch_id",
}
NEW_AREA_TABLE_COLUMNS = {
    "id", "external_id", "project_id", "area_name", "unit_type", "bedrooms", "area_sqm",
    "total_units", "status", "source_revision", "created_at", "updated_at",
    "mirrored_at", "mirrored_revision", "last_sync_batch_id",
}


def _columns(conn, table: str) -> set[str]:
    return set(
        conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
        ).scalars()
    )


def _tables(conn) -> set[str]:
    return set(
        conn.execute(
            sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalars()
    )


@pytest.fixture
def scratch_db():
    name = f"mc0003_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(sync_url(with_database(MINICRM_DB_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield with_database(MINICRM_DB_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name}
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    run_alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(sync_url(scratch_db))
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


# --- Cây Alembic khép kín -----------------------------------------------


def test_head_is_the_new_revision(upgraded):
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION


def test_upgrade_is_safe_to_run_twice(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "upgrade", REVISION)
    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
    finally:
        engine.dispose()


def test_the_revision_chains_onto_0002_not_the_backend(upgraded):
    text = (MINICRM_ROOT / "alembic" / "versions" / "0003_minicrm_hierarchy.py").read_text(encoding="utf-8")
    assert f'down_revision: str | None = "{PREVIOUS}"' in text
    for backend_revision in ("0016_completed_with_conflicts", "0015_ranking_results", "0001_initial_schema"):
        assert backend_revision not in text


def test_the_backend_alembic_tree_is_untouched():
    """Không cần database backend để kiểm điều này: đếm file revision trên đĩa.

    16 file tại THỜI ĐIỂM Phase B chốt (`0001`..`0016`, xem `pipeline_status.md`
    đợt (g)) — Phase B chỉ thêm MỘT migration cho Mini CRM, KHÔNG migration nào
    cho backend. Con số này KHÔNG còn là trần hiện tại của backend (Phase D thêm
    `0017_hierarchy_projection`, xa lạ với Mini CRM và không đổi hành vi của bất
    kỳ file nào Phase B từng thấy) — `>=` giữ đúng ý định gốc (Phase B không XOÁ
    bớt, không được đếm THIẾU) mà không đòi sửa lại con số mỗi khi backend tự lớn
    lên ở một phase khác không đụng gì tới Mini CRM.
    """
    backend_versions = REPO_ROOT / "alembic" / "versions"
    revision_files = [p for p in backend_versions.glob("*.py") if p.stem != "__init__" and not p.stem.startswith("_")]
    assert len(revision_files) >= 16, (
        f"Số revision của backend ÍT hơn mốc Phase B ({len(revision_files)}) — có gì đó đã bị xoá"
    )
    assert (backend_versions / "0016_completed_with_conflicts.py").exists()


def test_downgrade_returns_to_0002_and_removes_everything_0003_added(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "downgrade", PREVIOUS)

    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == PREVIOUS
            tables = _tables(conn)
            assert "crm_projects" not in tables
            assert "crm_areas" not in tables
            assert "area_id" not in _columns(conn, "crm_units")
            sequences = set(
                conn.execute(sa.text("SELECT sequence_name FROM information_schema.sequences")).scalars()
            )
            assert sequences & {"crm_project_external_seq", "crm_area_external_seq"} == set()
            # Bảng của 0001/0002 còn nguyên: 0003 chỉ THÊM.
            assert {"crm_units", "crm_deals", "crm_outbox"} <= tables
    finally:
        engine.dispose()


# --- crm_projects ------------------------------------------------------------


def test_crm_projects_has_exactly_the_expected_columns(upgraded):
    with upgraded["engine"].connect() as conn:
        assert _columns(conn, "crm_projects") == NEW_PROJECT_TABLE_COLUMNS


def test_project_status_outside_the_lifecycle_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_crm_projects_status"):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_projects (id, external_id, name, launch_date, status, source_revision, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'P-0001', 'x', '2026-01-01', 'pending', "
                    "1, now(), now())"
                )
            )


def test_project_status_pending_and_rejected_do_not_exist_here():
    """KHÁC hẳn `ck_projects_status` của BACKEND (bốn giá trị, một quy trình
    duyệt đã bị BỎ ở mô hình sở hữu mới) — Mini CRM chỉ có hai. Đọc trực tiếp
    từ file migration, không cần database, để khẳng định này không phụ thuộc
    PostgreSQL diễn giải CHECK constraint ra sao."""
    text = (MINICRM_ROOT / "alembic" / "versions" / "0003_minicrm_hierarchy.py").read_text(encoding="utf-8")
    assert 'LIFECYCLE_STATUSES = ("active", "archived")' in text


def test_duplicate_project_external_id_is_rejected(upgraded):
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO crm_projects (id, external_id, name, launch_date, status, source_revision, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'P-0001', 'x', '2026-01-01', 'active', 1, now(), now())"
            )
        )
    with pytest.raises(IntegrityError, match="uq_crm_projects_external_id"):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_projects (id, external_id, name, launch_date, status, source_revision, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'P-0001', 'y', '2026-01-01', 'active', 1, now(), now())"
                )
            )


# --- crm_areas -----------------------------------------------------------


def test_crm_areas_has_exactly_the_expected_columns(upgraded):
    with upgraded["engine"].connect() as conn:
        assert _columns(conn, "crm_areas") == NEW_AREA_TABLE_COLUMNS


def _project(conn, external_id: str = "P-0001") -> str:
    row = conn.execute(
        sa.text(
            "INSERT INTO crm_projects (id, external_id, name, launch_date, status, source_revision, "
            "created_at, updated_at) VALUES (gen_random_uuid(), :ext, 'x', '2026-01-01', 'active', 1, now(), now()) "
            "RETURNING id"
        ),
        {"ext": external_id},
    )
    return row.scalar_one()


def test_area_requires_an_existing_project_via_foreign_key(upgraded):
    with pytest.raises(IntegrityError, match="fk_crm_areas_project_id"):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_areas (id, external_id, project_id, area_name, unit_type, bedrooms, "
                    "area_sqm, total_units, status, source_revision, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'A-0001', gen_random_uuid(), 'A1', '2PN', 2, 68.5, 100, "
                    "'active', 1, now(), now())"
                )
            )


def test_area_total_units_below_zero_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_crm_areas_total_units_nonnegative"):
        with upgraded["engine"].begin() as conn:
            project_id = _project(conn)
            conn.execute(
                sa.text(
                    "INSERT INTO crm_areas (id, external_id, project_id, area_name, unit_type, bedrooms, "
                    "area_sqm, total_units, status, source_revision, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'A-0001', :p, 'A1', '2PN', 2, 68.5, -1, 'active', 1, now(), now())"
                ),
                {"p": project_id},
            )


def test_area_sqm_zero_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_crm_areas_area_sqm_positive"):
        with upgraded["engine"].begin() as conn:
            project_id = _project(conn)
            conn.execute(
                sa.text(
                    "INSERT INTO crm_areas (id, external_id, project_id, area_name, unit_type, bedrooms, "
                    "area_sqm, total_units, status, source_revision, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'A-0001', :p, 'A1', '2PN', 2, 0, 100, 'active', 1, now(), now())"
                ),
                {"p": project_id},
            )


def test_duplicate_natural_key_within_one_project_is_rejected(upgraded):
    with upgraded["engine"].begin() as conn:
        project_id = _project(conn)
        conn.execute(
            sa.text(
                "INSERT INTO crm_areas (id, external_id, project_id, area_name, unit_type, bedrooms, area_sqm, "
                "total_units, status, source_revision, created_at, updated_at) VALUES (gen_random_uuid(), "
                "'A-0001', :p, 'A1', '2PN', 2, 68.5, 100, 'active', 1, now(), now())"
            ),
            {"p": project_id},
        )
    with pytest.raises(IntegrityError, match="uq_crm_areas_project_name_unit_type"):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_areas (id, external_id, project_id, area_name, unit_type, bedrooms, area_sqm, "
                    "total_units, status, source_revision, created_at, updated_at) VALUES (gen_random_uuid(), "
                    "'A-0002', :p, 'A1', '2PN', 3, 70, 100, 'active', 1, now(), now())"
                ),
                {"p": project_id},
            )


# --- crm_units.area_id ----------------------------------------------------


def test_units_area_id_column_exists_and_is_nullable(upgraded):
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name = 'crm_units' AND column_name = 'area_id'"
            )
        ).one()
        assert row.is_nullable == "YES", "NULLABLE có chủ đích — xem docstring migration 0003 mục 1"


def test_a_pre_hierarchy_unit_can_still_be_inserted_with_no_area(upgraded):
    """163 căn tạo TRƯỚC Phase B không có `area_id` — mô phỏng lại đúng ca đó."""
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO crm_units (id, external_id, area_id, area_name, unit_type, unit_code, unit_status, "
                "source_revision, created_at, updated_at) VALUES (gen_random_uuid(), 'U-0001', NULL, 'Di sản', "
                "'Cũ', 'X-01', 'available', 1, now(), now())"
            )
        )


def test_units_area_id_requires_an_existing_area_via_foreign_key(upgraded):
    with pytest.raises(IntegrityError, match="fk_crm_units_area_id"):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_units (id, external_id, area_id, area_name, unit_type, unit_code, unit_status, "
                    "source_revision, created_at, updated_at) VALUES (gen_random_uuid(), 'U-0001', "
                    "gen_random_uuid(), 'A1', '2PN', 'X-01', 'available', 1, now(), now())"
                )
            )


# --- Dãy sinh external_id ----------------------------------------------------


def test_project_and_area_sequences_exist_and_are_independent_of_unit_deal(upgraded):
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT nextval('crm_project_external_seq')")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT nextval('crm_area_external_seq')")).scalar_one() == 1
        # Không chạm tới dãy unit/deal đã có từ 0002.
        assert conn.execute(sa.text("SELECT nextval('crm_unit_external_seq')")).scalar_one() == 1
