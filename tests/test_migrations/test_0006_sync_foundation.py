"""Migration 0006 (nền đồng bộ) phải chạy được cả tiến lẫn lùi trên database THẬT.

Cùng cách làm với test của 0005: database dùng-một-lần, soi lược đồ qua catalog
của Postgres chứ không đọc nội dung file migration.

Bỏ qua khi không có TEST_DATABASE_URL — xem tests/test_services/test_import_records.py.
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

PREVIOUS_REVISION = "0005_idempotent_csv_ingestion"
REVISION = "0006_sync_foundation"

NEW_UPLOAD_FILES_COLUMNS = {
    "source_system",
    "source_instance_id",
    "source_entity",
    "input_format",
    "transport_mode",
    "sync_mode",
    "schema_version",
    "external_batch_id",
    "rows_received",
    "finished_at",
    "last_source_cursor",
    "error_summary",
}
NEW_UPLOAD_ERRORS_COLUMNS = {
    "error_category",
    "json_path",
    "source_record_id",
    "record_locator",
    "field_name",
    "raw_value_redacted",
    "retry_status",
    "resolved_at",
}


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _run_alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )


def _alembic(url: str, *args: str) -> None:
    result = _run_alembic(url, *args)
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def scratch_db():
    name = f"mig6_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")

    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(TEST_DATABASE_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"),
                {"n": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _columns(conn, table: str) -> dict[str, str]:
    rows = conn.execute(
        sa.text("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = :t"),
        {"t": table},
    ).all()
    return {name: nullable for name, nullable in rows}


def _inspect(url: str) -> dict:
    engine = sa.create_engine(_sync_url(url))
    try:
        with engine.connect() as conn:
            files = _columns(conn, "upload_files")
            errors = _columns(conn, "upload_errors")
            mirror = _columns(conn, "crm_source_records")
            statuses = conn.execute(
                sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_upload_files_status'")
            ).scalar()
            identity = conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_constraint WHERE conname = 'uq_crm_source_records_identity' AND contype = 'u'"
                )
            ).scalar()
            batch_index = conn.execute(
                sa.text("SELECT 1 FROM pg_indexes WHERE indexname = 'uq_upload_files_source_batch'")
            ).scalar()
            revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()
    return {
        "files": files,
        "errors": errors,
        "mirror": mirror,
        "statuses": statuses or "",
        "identity": bool(identity),
        "batch_index": bool(batch_index),
        "revision": revision,
    }


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    """Tiến → lùi → tiến lại, soi lược đồ ở từng chặng."""
    _alembic(scratch_db, "upgrade", REVISION)
    after = _inspect(scratch_db)

    assert after["revision"] == REVISION
    assert NEW_UPLOAD_FILES_COLUMNS <= set(after["files"])
    assert NEW_UPLOAD_ERRORS_COLUMNS <= set(after["errors"])
    assert after["files"]["filename"] == "YES", "filename phải NULL được cho lô đẩy qua API"
    assert after["files"]["checksum"] == "YES"
    assert after["errors"]["row_number"] == "YES", "bản ghi JSON không có số dòng"
    assert "partially_completed" in after["statuses"]
    assert after["identity"] is True
    assert after["batch_index"] is True
    assert after["mirror"], "thiếu bảng crm_source_records"

    _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
    back = _inspect(scratch_db)

    assert back["revision"] == PREVIOUS_REVISION
    assert not (NEW_UPLOAD_FILES_COLUMNS & set(back["files"]))
    assert not (NEW_UPLOAD_ERRORS_COLUMNS & set(back["errors"]))
    assert back["files"]["filename"] == "NO", "downgrade phải siết lại NOT NULL"
    assert back["errors"]["row_number"] == "NO"
    assert "partially_completed" not in back["statuses"]
    assert back["identity"] is False
    assert back["mirror"] == {}, "crm_source_records phải biến mất"

    _alembic(scratch_db, "upgrade", REVISION)
    assert _inspect(scratch_db) == after


def test_upgrade_preserves_existing_rows_and_labels_them_honestly(scratch_db):
    """Lô tải file có sẵn phải sống sót, và cột mới phải mô tả đúng nguồn gốc của nó."""
    _alembic(scratch_db, "upgrade", PREVIOUS_REVISION)

    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, file_id = uuid.uuid4(), uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', :d, now())"),
                {"i": project_id, "d": "2026-01-01"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO upload_files "
                    "(id, project_id, filename, checksum, status, rows_ok, rows_failed, uploaded_at) "
                    "VALUES (:i, :p, 'legacy.csv', 'chk-legacy', 'completed', 5, 0, now())"
                ),
                {"i": file_id, "p": project_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO upload_errors (id, file_id, row_number, error_code, message, created_at) "
                    "VALUES (:i, :f, 7, 'INVALID_DATE', 'sai ngày', now())"
                ),
                {"i": uuid.uuid4(), "f": file_id},
            )

        _alembic(scratch_db, "upgrade", REVISION)

        with engine.connect() as conn:
            run = (
                conn.execute(
                    sa.text(
                        "SELECT filename, rows_ok, source_system, input_format, transport_mode, "
                        "sync_mode, schema_version, rows_received FROM upload_files WHERE id = :i"
                    ),
                    {"i": file_id},
                )
                .mappings()
                .one()
            )
            err = (
                conn.execute(
                    sa.text("SELECT row_number, error_category, retry_status FROM upload_errors WHERE file_id = :f"),
                    {"f": file_id},
                )
                .mappings()
                .one()
            )
    finally:
        engine.dispose()

    assert run["filename"] == "legacy.csv"
    assert run["rows_ok"] == 5
    # Dòng cũ đến từ người dùng tải file lên — cột mới phải nói đúng như vậy.
    assert run["source_system"] == "manual_upload"
    assert run["input_format"] == "csv"
    assert run["transport_mode"] == "file_upload"
    assert run["sync_mode"] == "full_snapshot"
    assert run["schema_version"] == 1
    assert run["rows_received"] == 0

    assert err["row_number"] == 7, "lỗi theo dòng của CSV không được đụng tới"
    assert err["error_category"] == "field"
    assert err["retry_status"] == "open"


def test_downgrade_drops_rows_that_the_old_schema_cannot_hold(scratch_db):
    """Đi lùi phải xử lý tường minh dữ liệu mà lược đồ cũ không chứa được.

    Lô đẩy qua API (filename NULL) và lỗi JSON (row_number NULL) không hợp lệ ở
    0005. Migration xoá đúng chúng thay vì vỡ giữa chừng — và test này chốt lại
    rằng phần dữ liệu CSV KHÔNG bị vạ lây.
    """
    _alembic(scratch_db, "upgrade", REVISION)

    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, api_run, file_run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', :d, now())"),
                {"i": project_id, "d": "2026-01-01"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO upload_files (id, project_id, filename, checksum, status, rows_ok, "
                    "rows_failed, uploaded_at, source_system, source_instance_id, input_format, "
                    "transport_mode, external_batch_id) "
                    "VALUES (:i, :p, NULL, NULL, 'partially_completed', 1, 1, now(), 'mini_crm', "
                    "'crm-a', 'json', 'api_push', 'batch-1')"
                ),
                {"i": api_run, "p": project_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO upload_files (id, project_id, filename, checksum, status, rows_ok, "
                    "rows_failed, uploaded_at) VALUES (:i, :p, 'keep.csv', 'chk', 'completed', 2, 0, now())"
                ),
                {"i": file_run, "p": project_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO upload_errors (id, file_id, row_number, error_code, message, created_at, "
                    "error_category, json_path) VALUES (:i, :f, NULL, 'INVALID_DATA', 'sai', now(), "
                    "'schema', '$.records[0].data')"
                ),
                {"i": uuid.uuid4(), "f": api_run},
            )

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)

        with engine.connect() as conn:
            remaining = {r[0] for r in conn.execute(sa.text("SELECT id FROM upload_files")).all()}
            error_count = conn.execute(sa.text("SELECT count(*) FROM upload_errors")).scalar()
    finally:
        engine.dispose()

    assert file_run in remaining, "lô tải file phải còn nguyên sau khi đi lùi"
    assert api_run not in remaining, "lô đẩy qua API không hợp lệ ở lược đồ cũ"
    assert error_count == 0
