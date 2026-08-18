"""Migration 0016 — nới `ck_upload_files_status` để nhận `completed_with_conflicts`.

Cùng cách làm với 0011–0015: database dùng-một-lần, chạy alembic thật, GHI dữ
liệu thật để chứng minh ràng buộc có/không có hiệu lực — không đoán qua việc đọc
mã nguồn migration.
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

PREVIOUS_REVISION = "0015_ranking_results"
REVISION = "0016_completed_with_conflicts"

# Cột đủ để vượt qua các NOT NULL/CHECK khác của `upload_files` — không phải
# trọng tâm của test này, chỉ cần một dòng ghi được.
_INSERT_SQL = sa.text(
    "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, "
    "source_system, source_instance_id, input_format, transport_mode, sync_mode, schema_version, "
    "rows_received, error_summary) "
    "VALUES (gen_random_uuid(), :project_id, :status, 0, 0, now(), "
    "'mini_crm', 'mig16-probe', 'json', 'api_push', 'incremental', 1, 0, '{}'::jsonb)"
)


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
    name = f"mig16_{uuid.uuid4().hex[:12]}_test"
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


def _seed_project(engine: sa.Engine) -> uuid.UUID:
    project_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'MIG16', '2026-01-01', now())"),
            {"id": project_id},
        )
    return project_id


def test_the_old_status_value_is_rejected_before_0016(scratch_db):
    """Chứng minh TIỀN ĐỀ: constraint cũ THẬT SỰ chặn — không chỉ đọc mã nguồn."""
    _alembic(scratch_db, "upgrade", PREVIOUS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id = _seed_project(engine)

    with pytest.raises(sa.exc.IntegrityError, match="ck_upload_files_status"):
        with engine.begin() as conn:
            conn.execute(_INSERT_SQL, {"project_id": project_id, "status": "completed_with_conflicts"})
    engine.dispose()


def test_0016_accepts_completed_with_conflicts(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id = _seed_project(engine)

    with engine.begin() as conn:
        conn.execute(_INSERT_SQL, {"project_id": project_id, "status": "completed_with_conflicts"})
        count = conn.execute(
            sa.text("SELECT count(*) FROM upload_files WHERE status = 'completed_with_conflicts'")
        ).scalar()
    assert count == 1
    engine.dispose()


def test_0016_still_rejects_a_value_outside_the_six(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id = _seed_project(engine)

    with pytest.raises(sa.exc.IntegrityError, match="ck_upload_files_status"):
        with engine.begin() as conn:
            conn.execute(_INSERT_SQL, {"project_id": project_id, "status": "made-up-status"})
    engine.dispose()


def test_downgrade_fails_closed_when_a_row_uses_the_new_status(scratch_db):
    """`downgrade()` không âm thầm xoá dữ liệu — nó phải NỔ nếu dữ liệu không còn
    hợp lệ với constraint cũ, để người vận hành tự quyết định, không phải migration."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id = _seed_project(engine)
    with engine.begin() as conn:
        conn.execute(_INSERT_SQL, {"project_id": project_id, "status": "completed_with_conflicts"})
    engine.dispose()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", PREVIOUS_REVISION],
        env={**os.environ, "DATABASE_URL": scratch_db},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "downgrade phải thất bại khi còn dòng 'completed_with_conflicts'"


def test_downgrade_then_upgrade_again_round_trips_cleanly(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
    _alembic(scratch_db, "upgrade", REVISION)

    engine = sa.create_engine(_sync_url(scratch_db))
    project_id = _seed_project(engine)
    with engine.begin() as conn:
        conn.execute(_INSERT_SQL, {"project_id": project_id, "status": "completed_with_conflicts"})
    engine.dispose()
