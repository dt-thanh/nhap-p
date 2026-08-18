"""Migration 0005 phải chạy được cả tiến lẫn lùi trên một database THẬT.

Nâng tới ĐÚNG revision 0005 chứ không tới `head`: file này kiểm 0005, và mọi
revision thêm về sau không được làm nó đổi ý nghĩa.

Không assert nội dung file migration — đọc file chỉ chứng minh ta viết đúng chữ,
không chứng minh Postgres chấp nhận. Test này dựng một database dùng-một-lần,
chạy `alembic upgrade head`, soi lược đồ thật qua `information_schema` /
`pg_constraint`, rồi `downgrade` và soi lại.

Database scratch tách hẳn khỏi database test của các module khác: `downgrade` gỡ
cột đang được dùng, chạy chung sẽ phá suite.

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

PREVIOUS_REVISION = "0004_cover_image_public_id"
REVISION = "0005_idempotent_csv_ingestion"
VERSIONED_TABLES = ("sales_records", "inventory_snapshots")


def _sync_url(url: str) -> str:
    """DSN đồng bộ (psycopg2) — alembic/env.py cũng đổi y hệt."""
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> None:
    """Chạy alembic trong tiến trình con — DATABASE_URL của Settings có cache."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def scratch_db():
    """Database trống, dùng một lần, xoá sạch sau test kể cả khi test hỏng."""
    name = f"mig_{uuid.uuid4().hex[:12]}_test"
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


def _inspect(url: str) -> dict:
    """Ba thứ 0005 đụng tới, đọc thẳng từ catalog của Postgres."""
    engine = sa.create_engine(_sync_url(url))
    try:
        with engine.connect() as conn:
            unique = conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_constraint WHERE conname = 'uq_upload_files_project_checksum' AND contype = 'u'"
                )
            ).scalar()
            index = conn.execute(
                sa.text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_upload_files_project_id_checksum'")
            ).scalar()
            columns = {
                table: conn.execute(
                    sa.text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = 'source_updated_at'"
                    ),
                    {"t": table},
                ).scalar()
                for table in VERSIONED_TABLES
            }
            revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()
    return {"unique": bool(unique), "index": bool(index), "columns": columns, "revision": revision}


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    """Tiến → lùi → tiến lại trên database sạch, soi lược đồ ở từng chặng."""
    _alembic(scratch_db, "upgrade", REVISION)
    after_upgrade = _inspect(scratch_db)

    assert after_upgrade["revision"] == REVISION
    assert after_upgrade["unique"] is False, "uq_upload_files_project_checksum lẽ ra đã bị bỏ"
    assert after_upgrade["index"] is True, "thiếu index thường thay cho ràng buộc vừa bỏ"
    assert after_upgrade["columns"] == dict.fromkeys(VERSIONED_TABLES, "YES"), "source_updated_at phải NULL được"

    _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
    after_downgrade = _inspect(scratch_db)

    assert after_downgrade["revision"] == PREVIOUS_REVISION
    assert after_downgrade["unique"] is True, "downgrade phải trả lại ràng buộc cũ"
    assert after_downgrade["index"] is False
    assert after_downgrade["columns"] == dict.fromkeys(VERSIONED_TABLES, None), "cột phải biến mất"

    # Lên lại được thì migration mới thực sự đi lại được, không phải chỉ chạy một chiều.
    _alembic(scratch_db, "upgrade", REVISION)
    assert _inspect(scratch_db) == after_upgrade


def test_upgrade_preserves_existing_rows(scratch_db):
    """Dữ liệu có sẵn phải sống sót qua 0005 — không xoá, không mất giá trị."""
    _alembic(scratch_db, "upgrade", PREVIOUS_REVISION)

    engine = sa.create_engine(_sync_url(scratch_db))
    project_id = uuid.uuid4()
    file_id = uuid.uuid4()
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

        _alembic(scratch_db, "upgrade", REVISION)

        with engine.connect() as conn:
            row = (
                conn.execute(
                    sa.text("SELECT filename, checksum, rows_ok FROM upload_files WHERE id = :i"), {"i": file_id}
                )
                .mappings()
                .one()
            )
        assert dict(row) == {"filename": "legacy.csv", "checksum": "chk-legacy", "rows_ok": 5}
    finally:
        engine.dispose()


def test_downgrade_refuses_when_duplicate_checksums_exist(scratch_db):
    """Sau khi bỏ UNIQUE, dữ liệu có thể trùng — lúc đó downgrade PHẢI vỡ, không im lặng.

    Chốt lại một hệ quả có thật của việc bỏ ràng buộc: đi lùi chỉ an toàn khi dữ
    liệu vẫn thoả ràng buộc cũ. Vỡ ồn ào ở đây tốt hơn là mất dòng trùng.
    """
    _alembic(scratch_db, "upgrade", REVISION)

    engine = sa.create_engine(_sync_url(scratch_db))
    project_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', :d, now())"),
                {"i": project_id, "d": "2026-01-01"},
            )
            for _ in range(2):
                conn.execute(
                    sa.text(
                        "INSERT INTO upload_files "
                        "(id, project_id, filename, checksum, status, rows_ok, rows_failed, uploaded_at) "
                        "VALUES (:i, :p, 'same.csv', 'same-checksum', 'completed', 1, 0, now())"
                    ),
                    {"i": uuid.uuid4(), "p": project_id},
                )
    finally:
        engine.dispose()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", PREVIOUS_REVISION],
        env={**os.environ, "DATABASE_URL": scratch_db},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "uq_upload_files_project_checksum" in result.stderr
