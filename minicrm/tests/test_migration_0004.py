"""Migration 0004 của Mini CRM: nới `ck_crm_outbox_entity` cho ý định v2 (Phase C).

Chạy trên database DÙNG MỘT LẦN (`mc0004_<hex>_test`), tạo và huỷ trong từng test.
Cùng khuôn với `test_migration_0003.py`.
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

REVISION = "0004_outbox_hierarchy_entities"
PREVIOUS = "0003_minicrm_hierarchy"


@pytest.fixture
def scratch_db():
    name = f"mc0004_{uuid.uuid4().hex[:12]}_test"
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


def _insert_outbox(conn, entity: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO crm_outbox (id, external_batch_id, entity, payload, created_at, attempts) "
            "VALUES (gen_random_uuid(), :batch, :entity, '{}'::jsonb, now(), 0)"
        ),
        {"batch": f"mc-test-{entity}-{uuid.uuid4().hex[:8]}", "entity": entity},
    )


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


def test_the_revision_chains_onto_0003_not_the_backend(upgraded):
    text = (MINICRM_ROOT / "alembic" / "versions" / "0004_outbox_hierarchy_entities.py").read_text(encoding="utf-8")
    assert f'down_revision: str | None = "{PREVIOUS}"' in text
    for backend_revision in ("0016_completed_with_conflicts", "0015_ranking_results", "0001_initial_schema"):
        assert backend_revision not in text


def test_the_backend_alembic_tree_is_untouched():
    """Không cần database backend để kiểm điều này: đếm file revision trên đĩa.

    16 file tại THỜI ĐIỂM Phase C chốt (`0001`..`0016`) — Phase C chỉ thêm MỘT
    migration cho Mini CRM (nới một CHECK constraint), KHÔNG migration nào cho
    backend. Con số này KHÔNG còn là trần hiện tại (Phase D thêm
    `0017_hierarchy_projection`, xa lạ với Mini CRM) — `>=` giữ đúng ý định gốc
    (Phase C không XOÁ bớt) mà không đòi sửa lại mỗi khi backend tự lớn ở một
    phase khác, xem cùng ghi chú ở `test_migration_0003.py`.
    """
    backend_versions = REPO_ROOT / "alembic" / "versions"
    revision_files = [p for p in backend_versions.glob("*.py") if p.stem != "__init__" and not p.stem.startswith("_")]
    assert len(revision_files) >= 16, (
        f"Số revision của backend ÍT hơn mốc Phase C ({len(revision_files)}) — có gì đó đã bị xoá"
    )
    assert (backend_versions / "0016_completed_with_conflicts.py").exists()


def test_downgrade_returns_to_0003_and_restores_the_narrow_constraint(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "downgrade", PREVIOUS)

    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == PREVIOUS
        with pytest.raises(IntegrityError, match="ck_crm_outbox_entity"):
            with engine.begin() as conn:
                _insert_outbox(conn, "projects")
    finally:
        engine.dispose()


# --- ck_crm_outbox_entity: nới ra, không thu hẹp -------------------------------


@pytest.mark.parametrize("entity", ["units", "deals", "projects", "areas", "units_v2", "deals_v2", "anything_nonempty"])
def test_any_nonblank_entity_is_now_accepted(upgraded, entity):
    with upgraded["engine"].begin() as conn:
        _insert_outbox(conn, entity)
    with upgraded["engine"].connect() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM crm_outbox WHERE entity = :e"), {"e": entity}
        ).scalar_one()
    assert count == 1


def test_blank_entity_is_still_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_crm_outbox_entity"):
        with upgraded["engine"].begin() as conn:
            _insert_outbox(conn, "")


def test_null_entity_is_still_rejected_by_not_null(upgraded):
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_outbox (id, external_batch_id, entity, payload, created_at, attempts) "
                    "VALUES (gen_random_uuid(), 'mc-test-null', NULL, '{}'::jsonb, now(), 0)"
                )
            )


def test_the_constraint_is_a_check_not_an_enum_type(upgraded):
    """Xác nhận thiết kế 'danh sách MỞ' (§A4.3 phase_a_domain_freeze.md, tiền lệ
    `crm_source_records.source_entity`) chứ không phải liệt kê bốn giá trị mới
    vào một enum khép kín — đọc trực tiếp định nghĩa constraint từ catalog."""
    with upgraded["engine"].connect() as conn:
        definition = conn.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_crm_outbox_entity'"
            )
        ).scalar_one()
    assert "<> ''" in definition or "<>''" in definition.replace(" ", "")
