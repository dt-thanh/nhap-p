"""Migration 0032 (replay_identity_index) — UNIQUE riêng phần cho backfill.

Hai điều phải đúng: (1) `ON CONFLICT DO NOTHING` keyed đúng ba cột thật sự
idempotent trên dòng `source='backfill_replay'`; (2) chỉ mục KHÔNG áp cho dòng
nguồn khác — trigger phát sinh (0030) ghi `source='manual'`/`'crm_sync'` và
không được phép bị chặn bởi ràng buộc này dù trùng cả ba cột.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)

PREVIOUS_REVISION = "0031_unit_inventory_daily"
REVISION = "0032_replay_identity_index"


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
    name = f"mig32_{uuid.uuid4().hex[:12]}_test"
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
                "unit_type, status, created_at, updated_at) "
                "VALUES (:i, 'mini_crm', 'crm-a', 'U-1', :a, 'A1-01', '2PN', 'available', now(), now())"
            ),
            {"i": unit_id, "a": area_id},
        )
    try:
        yield {"url": scratch_db, "engine": engine, "unit_id": unit_id}
    finally:
        engine.dispose()


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('uq_ush_replay_identity')")).scalar() is not None

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('uq_ush_replay_identity')")).scalar() is None
            assert conn.execute(sa.text("SELECT to_regclass('unit_status_history')")).scalar() is not None

        _alembic(scratch_db, "upgrade", REVISION)
    finally:
        engine.dispose()


def test_on_conflict_do_nothing_is_idempotent_for_backfill_rows(upgraded):
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    changed_at = "2026-01-10T00:00:00+00:00"
    table = sa.table(
        "unit_status_history",
        sa.column("id"),
        sa.column("unit_id"),
        sa.column("old_status"),
        sa.column("new_status"),
        sa.column("changed_at"),
        sa.column("source"),
    )
    stmt = pg_insert(table).values(
        id=uuid.uuid4(), unit_id=unit_id, old_status="available", new_status="reserved",
        changed_at=changed_at, source="backfill_replay",
    ).on_conflict_do_nothing(
        index_elements=["unit_id", "changed_at", "new_status"],
        index_where=sa.text("source = 'backfill_replay'"),
    )
    with engine.begin() as conn:
        conn.execute(stmt.values(id=uuid.uuid4()))
        conn.execute(stmt.values(id=uuid.uuid4()))  # re-run: must be a no-op, not an error
    with engine.connect() as conn:
        # Cùng unit_id còn có 1 sự kiện KHAI SINH do trigger 0030 tự phát khi
        # `upgraded` chèn unit (source='manual') — lọc đúng source backfill.
        assert conn.execute(
            sa.text(
                "SELECT count(*) FROM unit_status_history WHERE unit_id = :u AND source = 'backfill_replay'"
            ),
            {"u": unit_id},
        ).scalar() == 1


def test_the_partial_index_does_not_constrain_live_trigger_rows(upgraded):
    """Dòng `source != 'backfill_replay'` (như trigger 0030 ghi) không nằm trong
    phạm vi chỉ mục — trùng cả ba cột vẫn phải chèn được, không được NỔ."""
    engine, unit_id = upgraded["engine"], upgraded["unit_id"]
    changed_at = "2026-01-10T00:00:00+00:00"
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO unit_status_history (id, unit_id, old_status, new_status, changed_at, source) "
                "VALUES (:i, :u, 'available', 'reserved', :c, 'manual')"
            ),
            {"i": uuid.uuid4(), "u": unit_id, "c": changed_at},
        )
        # Cùng unit_id/changed_at/new_status, cùng source 'manual' -- KHÔNG nằm
        # trong WHERE source='backfill_replay' của chỉ mục, nên vẫn chèn được.
        conn.execute(
            sa.text(
                "INSERT INTO unit_status_history (id, unit_id, old_status, new_status, changed_at, source) "
                "VALUES (:i, :u, 'available', 'reserved', :c, 'manual')"
            ),
            {"i": uuid.uuid4(), "u": unit_id, "c": changed_at},
        )
    with engine.connect() as conn:
        # Lọc riêng new_status='reserved': loại sự kiện KHAI SINH
        # (new_status='available') mà trigger 0030 tự phát khi `upgraded` chèn
        # unit — cùng source='manual' nhưng không phải hai dòng đang kiểm.
        assert conn.execute(
            sa.text(
                "SELECT count(*) FROM unit_status_history "
                "WHERE unit_id = :u AND source = 'manual' AND new_status = 'reserved'"
            ),
            {"u": unit_id},
        ).scalar() == 2
