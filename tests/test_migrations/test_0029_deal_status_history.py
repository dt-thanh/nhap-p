"""Migration 0029 (deal_status_history) — tiến, lùi, ràng buộc, và append-only.

Chạy TỚI 0029, KHÔNG tới 0030 — bảo vệ append-only đứng vững một mình, không
phụ thuộc trigger phát sinh ở 0030 có tồn tại hay không. Cùng cách làm với
test_0028_unit_status_history.py.
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

PREVIOUS_REVISION = "0028_unit_status_history"
REVISION = "0029_deal_status_history"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def scratch_db():
    name = f"mig29_{uuid.uuid4().hex[:12]}_test"
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
def upgraded(scratch_db):
    """DB đã ở 0029, kèm project/area/unit/deal để có chỗ treo sự kiện lịch sử."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, area_id, unit_id, deal_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', '2026-01-01', now())"),
            {"i": project_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:i, :p, 'A1', '2PN', 2, 75, 100, now())"
            ),
            {"i": area_id, "p": project_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, unit_code, "
                "unit_type, status, deleted_at, created_at, updated_at) "
                "VALUES (:i, 'mini_crm', 'crm-a', 'U-1', :a, 'A1-01', '2PN', 'reserved', NULL, now(), now())"
            ),
            {"i": unit_id, "a": area_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, status, "
                "source_status, reserved_at, deleted_at, created_at, updated_at) "
                "VALUES (:i, 'mini_crm', 'crm-a', 'D-1', :u, 'reserved', 'reserved', now(), NULL, now(), now())"
            ),
            {"i": deal_id, "u": unit_id},
        )
    try:
        yield {"url": scratch_db, "engine": engine, "unit_id": unit_id, "deal_id": deal_id}
    finally:
        engine.dispose()


def _insert_event(
    conn, deal_id, unit_id, *, old="reserved", new="lost", prior_holding=True, new_holding=False, source="crm_sync"
):
    event_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO deal_status_history (id, deal_id, unit_id, old_status, new_status, "
            "prior_status_was_holding, new_status_is_holding, changed_at, source) "
            "VALUES (:i, :d, :u, :o, :n, :ph, :nh, now(), :s)"
        ),
        {"i": event_id, "d": deal_id, "u": unit_id, "o": old, "n": new, "ph": prior_holding, "nh": new_holding, "s": source},
    )
    return event_id


# --- Tiến / lùi -------------------------------------------------------------


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('deal_status_history')")).scalar() is not None
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == REVISION

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('deal_status_history')")).scalar() is None
            # 0028 và các bảng nghiệp vụ vẫn còn nguyên.
            for legacy in ("unit_status_history", "units", "deals"):
                assert conn.execute(sa.text(f"SELECT to_regclass('{legacy}')")).scalar() is not None

        _alembic(scratch_db, "upgrade", REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('deal_status_history')")).scalar() is not None
    finally:
        engine.dispose()


def test_upgrade_succeeds_on_db_with_existing_deals_data(upgraded):
    engine, deal_id = upgraded["engine"], upgraded["deal_id"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT status FROM deals WHERE id = :i"), {"i": deal_id}).scalar() == "reserved"


# --- Ràng buộc ---------------------------------------------------------------


def test_new_status_check_rejects_unknown_value(upgraded):
    engine, deal_id, unit_id = upgraded["engine"], upgraded["deal_id"], upgraded["unit_id"]
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_event(conn, deal_id, unit_id, old=None, new="khong-ton-tai", prior_holding=False)
    assert "ck_dsh_new_status" in str(exc.value)


def test_no_op_transition_is_rejected(upgraded):
    engine, deal_id, unit_id = upgraded["engine"], upgraded["deal_id"], upgraded["unit_id"]
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_event(conn, deal_id, unit_id, old="reserved", new="reserved", new_holding=True)
    assert "ck_dsh_actual_change" in str(exc.value)


def test_a_lost_lead_is_distinguishable_from_a_cancelled_reservation(upgraded):
    """`prior_status_was_holding` là cách duy nhất tách lead mất khỏi giao dịch huỷ."""
    engine, deal_id, unit_id = upgraded["engine"], upgraded["deal_id"], upgraded["unit_id"]
    with engine.begin() as conn:
        lost_lead = _insert_event(conn, deal_id, unit_id, old="lead", new="lost", prior_holding=False, new_holding=False)
        cancelled = _insert_event(
            conn, deal_id, unit_id, old="reserved", new="lost", prior_holding=True, new_holding=False
        )
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT id, prior_status_was_holding FROM deal_status_history WHERE id IN (:a, :b)"),
            {"a": lost_lead, "b": cancelled},
        ).mappings().all()
    by_id = {r["id"]: r["prior_status_was_holding"] for r in rows}
    assert by_id[lost_lead] is False
    assert by_id[cancelled] is True


def test_deleting_a_deal_cascades_to_its_history(upgraded):
    """CASCADE, không RESTRICT — cùng lý do với unit_status_history (0028)."""
    engine, deal_id, unit_id = upgraded["engine"], upgraded["deal_id"], upgraded["unit_id"]
    with engine.begin() as conn:
        _insert_event(conn, deal_id, unit_id, old="reserved", new="lost")
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM deals WHERE id = :i"), {"i": deal_id})
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM deal_status_history WHERE deal_id = :i"), {"i": deal_id}
        ).scalar() == 0


# --- Append-only guard --------------------------------------------------------


def test_append_only_guard_rejects_update(upgraded):
    engine, deal_id, unit_id = upgraded["engine"], upgraded["deal_id"], upgraded["unit_id"]
    with engine.begin() as conn:
        event_id = _insert_event(conn, deal_id, unit_id, old="reserved", new="lost")
    with pytest.raises(sa.exc.InternalError) as exc:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE deal_status_history SET new_status = 'sold' WHERE id = :i"), {"i": event_id})
    assert "append-only" in str(exc.value)


def test_append_only_guard_rejects_delete(upgraded):
    engine, deal_id, unit_id = upgraded["engine"], upgraded["deal_id"], upgraded["unit_id"]
    with engine.begin() as conn:
        event_id = _insert_event(conn, deal_id, unit_id, old="reserved", new="lost")
    with pytest.raises(sa.exc.InternalError) as exc:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM deal_status_history WHERE id = :i"), {"i": event_id})
    assert "append-only" in str(exc.value)


def test_the_core_projection_matches_the_migrated_columns(upgraded):
    from src.models.tables import deal_status_history

    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'deal_status_history'"
            )
        ).all()
    actual = {name: (is_nullable == "YES") for name, is_nullable in rows}
    expected = {c.name: c.nullable for c in deal_status_history.columns}
    assert actual == expected
