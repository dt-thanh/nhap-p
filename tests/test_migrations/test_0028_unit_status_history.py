"""Migration 0028 (unit_status_history) — tiến, lùi, ràng buộc, và append-only.

Cùng cách làm với test của 0007: database dùng-một-lần, thử ghi dữ liệu THẬT để
chứng minh ràng buộc có hiệu lực, không chỉ tồn tại trên giấy. Chạy TỚI 0028,
KHÔNG tới 0030 — bảo vệ append-only phải đứng vững một mình, không phụ thuộc
trigger phát sinh ở 0030 có tồn tại hay không.
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

PREVIOUS_REVISION = "0027_project_price_observations"
REVISION = "0028_unit_status_history"


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
    name = f"mig28_{uuid.uuid4().hex[:12]}_test"
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
    """DB đã ở 0028, kèm project/area/unit để có chỗ treo sự kiện lịch sử."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, area_id, unit_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
                "VALUES (:i, 'mini_crm', 'crm-a', 'U-1', :a, 'A1-01', '2PN', 'available', NULL, now(), now())"
            ),
            {"i": unit_id, "a": area_id},
        )
    try:
        yield {"url": scratch_db, "engine": engine, "unit_id": unit_id}
    finally:
        engine.dispose()


def _insert_event(conn, unit_id, *, old="available", new="reserved", source="crm_sync"):
    event_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO unit_status_history (id, unit_id, old_status, new_status, changed_at, source) "
            "VALUES (:i, :u, :o, :n, now(), :s)"
        ),
        {"i": event_id, "u": unit_id, "o": old, "n": new, "s": source},
    )
    return event_id


# --- Tiến / lùi -------------------------------------------------------------


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('unit_status_history')")).scalar() is not None
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == REVISION

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('unit_status_history')")).scalar() is None
            # Bảng cũ vẫn còn nguyên — 0028 không được đụng tới units/deals.
            for legacy in ("units", "deals", "areas", "projects"):
                assert conn.execute(sa.text(f"SELECT to_regclass('{legacy}')")).scalar() is not None

        _alembic(scratch_db, "upgrade", REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('unit_status_history')")).scalar() is not None
    finally:
        engine.dispose()


def test_upgrade_succeeds_on_db_with_existing_units_data(upgraded):
    """upgrade() đã chạy TRÊN một DB có sẵn project/area/unit (fixture `upgraded`) — không nổ."""
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT status FROM units WHERE id = :i"), {"i": unit_id}).scalar() == "available"


# --- Ràng buộc ---------------------------------------------------------------


def test_new_status_check_rejects_unknown_value(upgraded):
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_event(conn, unit_id, old=None, new="khong-ton-tai")
    assert "ck_ush_new_status" in str(exc.value)


def test_no_op_transition_is_rejected(upgraded):
    """`old_status == new_status` không phải một sự kiện — CHECK phải chặn nó."""
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_event(conn, unit_id, old="available", new="available")
    assert "ck_ush_actual_change" in str(exc.value)


def test_unknown_source_is_rejected(upgraded):
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_event(conn, unit_id, old=None, new="reserved", source="from_thin_air")
    assert "ck_ush_source" in str(exc.value)


def test_null_old_status_is_allowed_for_birth_events(upgraded):
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with engine.begin() as conn:
        event_id = _insert_event(conn, unit_id, old=None, new="available", source="seed")
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT old_status FROM unit_status_history WHERE id = :i"), {"i": event_id}
        ).scalar() is None


def test_deleting_a_unit_cascades_to_its_history(upgraded):
    """CASCADE, không RESTRICT: xoá cứng units là luồng thật (seed idempotent 0019/0023,
    dọn dẹp test) — xem docstring module. Phải THÀNH CÔNG và dọn theo lịch sử của nó."""
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with engine.begin() as conn:
        _insert_event(conn, unit_id, old="available", new="reserved")
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM units WHERE id = :i"), {"i": unit_id})
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM unit_status_history WHERE unit_id = :i"), {"i": unit_id}
        ).scalar() == 0


def test_append_only_guard_still_rejects_a_direct_delete_after_cascade_is_wired(upgraded):
    """CASCADE cho phép xoá lồng qua FK — nhưng client gõ DELETE trực tiếp vẫn phải bị chặn."""
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with engine.begin() as conn:
        event_id = _insert_event(conn, unit_id, old="available", new="reserved")
    with pytest.raises(sa.exc.InternalError) as exc:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM unit_status_history WHERE id = :i"), {"i": event_id})
    assert "append-only" in str(exc.value)


# --- Append-only guard --------------------------------------------------------


def test_append_only_guard_rejects_update(upgraded):
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with engine.begin() as conn:
        event_id = _insert_event(conn, unit_id, old="available", new="reserved")
    with pytest.raises(sa.exc.InternalError) as exc:
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE unit_status_history SET new_status = 'sold' WHERE id = :i"), {"i": event_id}
            )
    assert "append-only" in str(exc.value)


def test_append_only_guard_rejects_delete(upgraded):
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    with engine.begin() as conn:
        event_id = _insert_event(conn, unit_id, old="available", new="reserved")
    with pytest.raises(sa.exc.InternalError) as exc:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM unit_status_history WHERE id = :i"), {"i": event_id})
    assert "append-only" in str(exc.value)
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM unit_status_history WHERE id = :i"), {"i": event_id}
        ).scalar() == 1


def test_the_core_projection_matches_the_migrated_columns(upgraded):
    """`src/models/tables.py` là hình chiếu — lệch cột thì lỗi chỉ nổ lúc chạy thật."""
    from src.models.tables import unit_status_history

    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'unit_status_history'"
            )
        ).all()
    actual = {name: (is_nullable == "YES") for name, is_nullable in rows}
    expected = {c.name: c.nullable for c in unit_status_history.columns}
    assert actual == expected
