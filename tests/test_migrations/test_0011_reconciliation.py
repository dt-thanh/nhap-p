"""Migration 0011 — tiến, lùi, tiến lại, và các ràng buộc thật.

Cùng cách làm với 0005–0010: database dùng-một-lần, soi catalog, và GHI dữ liệu
thật để chứng minh ràng buộc có hiệu lực.
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

PREVIOUS_REVISION = "0010_sync_payload_retention"
REVISION = "0011_reconciliation"


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
    name = f"mig11_{uuid.uuid4().hex[:12]}_test"
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
    project_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', :d, now())"),
            {"i": project_id, "d": "2026-01-01"},
        )
    try:
        yield {"engine": engine, "url": scratch_db, "project_id": project_id}
    finally:
        engine.dispose()


def _tables(conn) -> set[str]:
    return set(conn.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())


def _insert_run(conn, project_id, **overrides):
    values = {
        "id": uuid.uuid4(),
        "project_id": project_id,
        "scope": "internal",
        "status": "completed",
        "passed": True,
        "error_count": 0,
        "warning_count": 0,
        "info_count": 0,
        "checks_run": 9,
        **overrides,
    }
    names = ", ".join(values)
    binds = ", ".join(f":{k}" for k in values)
    conn.execute(sa.text(f"INSERT INTO reconciliation_runs ({names}, started_at) VALUES ({binds}, now())"), values)
    return values["id"]


# --- Tiến / lùi -------------------------------------------------------------


def test_upgrade_creates_both_tables_and_snapshot_columns(upgraded):
    with upgraded["engine"].connect() as conn:
        assert {"reconciliation_runs", "reconciliation_findings"} <= _tables(conn)
        columns = set(
            conn.execute(
                sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='upload_files'")
            ).scalars()
        )
    assert {"snapshot_id", "chunk_index", "chunk_total", "snapshot_complete", "snapshot_scope"} <= columns


def test_downgrade_then_upgrade_round_trip(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            remaining = _tables(conn)
            columns = set(
                conn.execute(
                    sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='upload_files'")
                ).scalars()
            )
        assert "reconciliation_runs" not in remaining
        assert "reconciliation_findings" not in remaining
        assert "snapshot_id" not in columns
        # Bảng của giai đoạn trước phải còn nguyên.
        assert {"units", "deals", "sync_payloads", "sync_credentials"} <= remaining

        _alembic(scratch_db, "upgrade", REVISION)
        with engine.connect() as conn:
            assert {"reconciliation_runs", "reconciliation_findings"} <= _tables(conn)
    finally:
        engine.dispose()


# --- Ràng buộc --------------------------------------------------------------


def test_passed_cannot_be_true_when_errors_exist(upgraded):
    """Quy tắc "đạt" nằm ở DB: một lần chạy có lỗi mà ghi passed=true là bằng
    chứng giả cho việc cắt sang bộ tính mới."""
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_run(conn, upgraded["project_id"], passed=True, error_count=3, status="failed")
    assert "ck_reconciliation_runs_passed_requires_no_errors" in str(exc.value)


def test_failed_run_with_errors_is_allowed(upgraded):
    with upgraded["engine"].begin() as conn:
        _insert_run(conn, upgraded["project_id"], passed=False, error_count=3, status="failed")


def test_unknown_scope_is_rejected(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_run(conn, upgraded["project_id"], scope="khong-co-scope-nay")
    assert "ck_reconciliation_runs_scope" in str(exc.value)


def test_snapshot_id_requires_snapshot_scope(upgraded):
    """Chỉ scope='snapshot' mới được mang snapshot_id."""
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_run(conn, upgraded["project_id"], scope="internal", snapshot_id="SNAP-1")
    assert "ck_reconciliation_runs_snapshot_id_scope" in str(exc.value)


def test_warning_finding_must_carry_details(upgraded):
    """Cảnh báo trống buộc người đọc đi tìm lại từ đầu — thường là không tìm."""
    with upgraded["engine"].begin() as conn:
        run_id = _insert_run(conn, upgraded["project_id"])

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO reconciliation_findings (id, reconciliation_run_id, check_code, severity, "
                    "message, details, created_at) VALUES (:i, :r, 'STALE', 'warning', 'thiếu chi tiết', "
                    "'{}'::jsonb, now())"
                ),
                {"i": uuid.uuid4(), "r": run_id},
            )
    assert "ck_reconciliation_findings_warning_needs_details" in str(exc.value)


def test_error_finding_may_omit_details(upgraded):
    """Chỉ 'warning' bị buộc kèm chi tiết; 'error' đã tự nói lên vấn đề."""
    with upgraded["engine"].begin() as conn:
        run_id = _insert_run(conn, upgraded["project_id"])
        conn.execute(
            sa.text(
                "INSERT INTO reconciliation_findings (id, reconciliation_run_id, check_code, severity, "
                "message, details, created_at) VALUES (:i, :r, 'ORPHAN_DEAL', 'error', 'mồ côi', "
                "'{}'::jsonb, now())"
            ),
            {"i": uuid.uuid4(), "r": run_id},
        )


def test_deleting_a_run_cascades_to_its_findings(upgraded):
    with upgraded["engine"].begin() as conn:
        run_id = _insert_run(conn, upgraded["project_id"])
        conn.execute(
            sa.text(
                "INSERT INTO reconciliation_findings (id, reconciliation_run_id, check_code, severity, "
                "message, details, created_at) VALUES (:i, :r, 'X', 'error', 'm', '{}'::jsonb, now())"
            ),
            {"i": uuid.uuid4(), "r": run_id},
        )

    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM reconciliation_runs WHERE id = :i"), {"i": run_id})

    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM reconciliation_findings")).scalar_one() == 0


def test_chunk_index_must_be_within_chunk_total(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, "
                    "snapshot_id, chunk_index, chunk_total, snapshot_complete) "
                    "VALUES (:i, :p, 'completed', 0, 0, now(), 'SNAP-1', 5, 3, true)"
                ),
                {"i": uuid.uuid4(), "p": upgraded["project_id"]},
            )
    assert "ck_upload_files_chunk_index_within_total" in str(exc.value)


def test_snapshot_id_requires_the_full_chunk_triple(upgraded):
    """Thiếu một mảnh thông tin ảnh chụp là không dùng được."""
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, "
                    "snapshot_id) VALUES (:i, :p, 'completed', 0, 0, now(), 'SNAP-1')"
                ),
                {"i": uuid.uuid4(), "p": upgraded["project_id"]},
            )
    assert "ck_upload_files_snapshot_fields_together" in str(exc.value)


def test_the_same_chunk_cannot_be_received_twice(upgraded):
    """Nhận trùng mảnh làm phép đếm "đủ mảnh chưa" sai theo hướng nguy hiểm:
    tưởng đủ trong khi còn thiếu, rồi xoá nhầm."""

    def insert(conn, batch):
        conn.execute(
            sa.text(
                "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, "
                "source_system, source_instance_id, external_batch_id, "
                "snapshot_id, chunk_index, chunk_total, snapshot_complete) "
                "VALUES (:i, :p, 'completed', 0, 0, now(), 'mini_crm', 'crm-a', :b, 'SNAP-1', 0, 2, false)"
            ),
            {"i": uuid.uuid4(), "p": upgraded["project_id"], "b": batch},
        )

    with upgraded["engine"].begin() as conn:
        insert(conn, "B-1")

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            insert(conn, "B-2")
    assert "uq_upload_files_snapshot_chunk" in str(exc.value)
