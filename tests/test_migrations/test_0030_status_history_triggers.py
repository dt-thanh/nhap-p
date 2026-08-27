"""Migration 0030 (status_history_triggers) — trigger phát sinh sự kiện thật.

Khác 0028/0029 (test bảng đứng một mình), file này kiểm TRIGGER: chèn/đổi
`units`/`deals` thật và đọc lại `unit_status_history`/`deal_status_history` để
chứng minh trigger đã ghi đúng, không phải suy luận từ mã nguồn.
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

PREVIOUS_REVISION = "0029_deal_status_history"
REVISION = "0030_status_history_triggers"


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
    name = f"mig30_{uuid.uuid4().hex[:12]}_test"
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
    """DB đã ở 0030 (head của nhóm này), kèm project/area để có chỗ treo units."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, area_id = uuid.uuid4(), uuid.uuid4()
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
    try:
        yield {"url": scratch_db, "engine": engine, "area_id": area_id}
    finally:
        engine.dispose()


def _insert_unit(conn, area_id, *, external="U-1", status="available"):
    unit_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, unit_code, "
            "unit_type, status, created_at, updated_at) "
            "VALUES (:i, 'mini_crm', 'crm-a', :e, :a, :e, '2PN', :s, now(), now())"
        ),
        {"i": unit_id, "e": external, "a": area_id, "s": status},
    )
    return unit_id


def _insert_deal(conn, unit_id, *, external="D-1", status="lead"):
    deal_id = uuid.uuid4()
    stamps = {"reserved": "reserved_at", "sold": "sold_at", "lost": "lost_at"}
    column = stamps.get(status)
    conn.execute(
        sa.text(
            "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, status, "
            f"source_status, {column + ', ' if column else ''}created_at, updated_at) "
            f"VALUES (:i, 'mini_crm', 'crm-a', :e, :u, :s, :s, {'now(), ' if column else ''}now(), now())"
        ),
        {"i": deal_id, "e": external, "u": unit_id, "s": status},
    )
    return deal_id


def _history_rows_for_unit(engine, unit_id):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT old_status, new_status, source FROM unit_status_history "
                "WHERE unit_id = :u ORDER BY recorded_at"
            ),
            {"u": unit_id},
        ).mappings().all()


def _history_rows_for_deal(engine, deal_id):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT old_status, new_status, prior_status_was_holding, new_status_is_holding "
                "FROM deal_status_history WHERE deal_id = :d ORDER BY recorded_at"
            ),
            {"d": deal_id},
        ).mappings().all()


# --- Tiến / lùi -------------------------------------------------------------


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            triggers = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT tgname FROM pg_trigger WHERE tgrelid IN "
                        "('units'::regclass, 'deals'::regclass) AND NOT tgisinternal"
                    )
                ).all()
            }
            assert "trg_units_status_history" in triggers
            assert "trg_deals_status_history" in triggers

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            triggers = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT tgname FROM pg_trigger WHERE tgrelid IN "
                        "('units'::regclass, 'deals'::regclass) AND NOT tgisinternal"
                    )
                ).all()
            }
            assert "trg_units_status_history" not in triggers
            assert "trg_deals_status_history" not in triggers
            # Bảng lịch sử của 0028/0029 không bị đụng.
            for legacy in ("unit_status_history", "deal_status_history"):
                assert conn.execute(sa.text(f"SELECT to_regclass('{legacy}')")).scalar() is not None

        _alembic(scratch_db, "upgrade", REVISION)
    finally:
        engine.dispose()


def test_downgrade_stops_capture_without_deleting_prior_history(scratch_db):
    """Đúng mục tiêu tách 0030 khỏi 0028/0029: lùi trigger không xoá sự kiện đã bắt."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        project_id, area_id = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', '2026-01-01', now())"
                ),
                {"i": project_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                    "created_at) VALUES (:i, :p, 'A1', '2PN', 2, 75, 100, now())"
                ),
                {"i": area_id, "p": project_id},
            )
            unit_id = _insert_unit(conn, area_id)
            conn.execute(sa.text("UPDATE units SET status = 'reserved', updated_at = now() WHERE id = :i"), {"i": unit_id})

        rows_before = _history_rows_for_unit(engine, unit_id)
        assert len(rows_before) == 2  # birth (INSERT) + available->reserved

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE units SET status = 'sold', updated_at = now() WHERE id = :i"), {"i": unit_id})

        rows_after = _history_rows_for_unit(engine, unit_id)
        assert rows_after == rows_before, "trigger đã gỡ — không được có sự kiện mới"
    finally:
        engine.dispose()


# --- Trigger units -----------------------------------------------------------


def test_trigger_emits_birth_event_on_insert(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id, status="available")
    rows = _history_rows_for_unit(engine, unit_id)
    assert len(rows) == 1
    assert rows[0]["old_status"] is None
    assert rows[0]["new_status"] == "available"


def test_trigger_emits_on_status_update(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id, status="available")
        conn.execute(sa.text("UPDATE units SET status = 'reserved', updated_at = now() WHERE id = :i"), {"i": unit_id})
    rows = _history_rows_for_unit(engine, unit_id)
    assert [(r["old_status"], r["new_status"]) for r in rows] == [(None, "available"), ("available", "reserved")]


def test_trigger_skips_update_with_no_actual_status_change(upgraded):
    """UPDATE chạm cột status nhưng gán CÙNG giá trị — không phải một sự kiện."""
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id, status="available")
        conn.execute(sa.text("UPDATE units SET status = 'available', updated_at = now() WHERE id = :i"), {"i": unit_id})
    rows = _history_rows_for_unit(engine, unit_id)
    assert len(rows) == 1, "chỉ có sự kiện khai sinh — cập nhật không đổi giá trị không được ghi thêm"


def test_trigger_source_defaults_to_manual_without_session_gate(upgraded):
    """Chưa có `SET LOCAL app.history_source` nào được dây vào — giới hạn đã biết, xem 0030 docstring."""
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id)
    rows = _history_rows_for_unit(engine, unit_id)
    assert rows[0]["source"] == "manual"


def test_trigger_source_honors_session_gate_when_set(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        conn.execute(sa.text("SET LOCAL app.history_source = 'crm_sync'"))
        unit_id = _insert_unit(conn, area_id)
    rows = _history_rows_for_unit(engine, unit_id)
    assert rows[0]["source"] == "crm_sync"


# --- Trigger deals ------------------------------------------------------------


def test_deals_trigger_flags_cancellation_of_a_held_reservation(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id, status="reserved")
        deal_id = _insert_deal(conn, unit_id, status="reserved")
        conn.execute(sa.text("UPDATE deals SET status = 'lost', lost_at = now(), updated_at = now() WHERE id = :i"), {"i": deal_id})
    rows = _history_rows_for_deal(engine, deal_id)
    cancellation = [r for r in rows if r["new_status"] == "lost"][0]
    assert cancellation["prior_status_was_holding"] is True
    assert cancellation["new_status_is_holding"] is False


def test_deals_trigger_does_not_flag_a_lost_lead_as_a_cancellation(upgraded):
    """Một lead mất KHÔNG phải một huỷ giao dịch — lead chưa từng giữ tồn kho."""
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id, status="available")
        deal_id = _insert_deal(conn, unit_id, status="lead")
        conn.execute(sa.text("UPDATE deals SET status = 'lost', lost_at = now(), updated_at = now() WHERE id = :i"), {"i": deal_id})
    rows = _history_rows_for_deal(engine, deal_id)
    lost_event = [r for r in rows if r["new_status"] == "lost"][0]
    assert lost_event["prior_status_was_holding"] is False
