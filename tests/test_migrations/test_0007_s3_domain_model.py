"""Migration 0007 (mô hình miền S3) — tiến, lùi, và các ràng buộc thật.

Cùng cách làm với test của 0005/0006: database dùng-một-lần, soi catalog của
Postgres, và thử ghi dữ liệu THẬT để chứng minh ràng buộc có hiệu lực chứ không
chỉ tồn tại trên giấy.
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

PREVIOUS_REVISION = "0006_sync_foundation"
REVISION = "0007_s3_domain_model"


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
    name = f"mig7_{uuid.uuid4().hex[:12]}_test"
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
    """DB đã ở head 0007, kèm một dự án + phân khu để có chỗ treo căn."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, area_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', :d, now())"),
            {"i": project_id, "d": "2026-01-01"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:i, :p, 'A1', '2PN', 2, 75, 100, now())"
            ),
            {"i": area_id, "p": project_id},
        )
    try:
        yield {"url": scratch_db, "engine": engine, "project_id": project_id, "area_id": area_id}
    finally:
        engine.dispose()


def _insert_unit(conn, area_id, *, external="U-1", code="A1-01", status="available", deleted=False):
    unit_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, unit_code, "
            "unit_type, status, deleted_at, created_at, updated_at) "
            "VALUES (:i, 'mini_crm', 'crm-a', :e, :a, :c, '2PN', :s, "
            + ("now()" if deleted else "NULL")
            + ", now(), now())"
        ),
        {"i": unit_id, "e": external, "a": area_id, "c": code, "s": status},
    )
    return unit_id


def _insert_deal(conn, unit_id, *, external="D-1", status="reserved", deleted=False):
    deal_id = uuid.uuid4()
    stamps = {"reserved": "reserved_at", "sold": "sold_at", "lost": "lost_at"}
    column = stamps.get(status)
    conn.execute(
        sa.text(
            "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, status, "
            f"source_status, {column + ', ' if column else ''}deleted_at, created_at, updated_at) "
            f"VALUES (:i, 'mini_crm', 'crm-a', :e, :u, :s, :s, {'now(), ' if column else ''}"
            + ("now()" if deleted else "NULL")
            + ", now(), now())"
        ),
        {"i": deal_id, "e": external, "u": unit_id, "s": status},
    )
    return deal_id


# --- Tiến / lùi -------------------------------------------------------------


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('units')")).scalar() is not None
            assert conn.execute(sa.text("SELECT to_regclass('deals')")).scalar() is not None
            columns = {
                r[0]
                for r in conn.execute(
                    sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='absorption_daily'")
                ).all()
            }
            assert "units_remaining" in columns
            nullable = conn.execute(
                sa.text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name='absorption_daily' AND column_name='units_remaining'"
                )
            ).scalar()
            # NULL được: bộ tính cũ không tính được số này, điền 0 là nói sai.
            assert nullable == "YES"

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('units')")).scalar() is None
            assert conn.execute(sa.text("SELECT to_regclass('deals')")).scalar() is None
            columns = {
                r[0]
                for r in conn.execute(
                    sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='absorption_daily'")
                ).all()
            }
            assert "units_remaining" not in columns
            # Bảng cũ vẫn còn nguyên.
            for legacy in ("sales_records", "inventory_snapshots", "upload_files", "crm_source_records"):
                assert conn.execute(sa.text(f"SELECT to_regclass('{legacy}')")).scalar() is not None

        _alembic(scratch_db, "upgrade", REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('units')")).scalar() is not None
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == REVISION
    finally:
        engine.dispose()


def test_upgrade_preserves_legacy_rows(upgraded):
    """Dữ liệu tổng hợp cũ không bị đụng tới — 0007 chỉ THÊM."""
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, velocity_7d, velocity_30d, "
                "data_quality_status, is_observed, computed_at) "
                "VALUES (:i, :a, '2026-02-01', 3, 1.5, 0.5, 'ok', true, now())"
            ),
            {"i": uuid.uuid4(), "a": area_id},
        )
    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT units_sold, units_remaining FROM absorption_daily")).mappings().one()
    assert row["units_sold"] == 3
    assert row["units_remaining"] is None, "bộ tính cũ không ghi cột này — phải là NULL, không phải 0"


# --- Ràng buộc units --------------------------------------------------------


def test_units_source_identity_is_unique(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        _insert_unit(conn, area_id, external="U-1", code="A1-01")

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_unit(conn, area_id, external="U-1", code="A1-02")
    assert "uq_units_source_identity" in str(exc.value)


def test_unit_code_unique_only_among_live_units(upgraded):
    """Mã căn duy nhất trong phân khu — nhưng căn đã xoá không chặn tạo lại."""
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        _insert_unit(conn, area_id, external="U-1", code="A1-01", deleted=True)
        # Cùng mã căn, căn cũ đã tombstone → phải chèn được.
        _insert_unit(conn, area_id, external="U-2", code="A1-01")

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            _insert_unit(conn, area_id, external="U-3", code="A1-01")


def test_unit_status_check_rejects_unknown_value(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_unit(conn, area_id, status="khong-ton-tai")
    assert "ck_units_status" in str(exc.value)


def test_units_support_tombstone(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id)
        conn.execute(sa.text("UPDATE units SET deleted_at = now(), updated_at = now() WHERE id = :i"), {"i": unit_id})
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT deleted_at FROM units WHERE id = :i"), {"i": unit_id}).scalar() is not None


# --- Ràng buộc deals --------------------------------------------------------


def test_deal_status_check_rejects_unknown_value(upgraded):
    """`cancelled` KHÔNG phải trạng thái hợp lệ ở tầng DB — tầng chiếu ánh xạ nó về `lost`."""
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id)

    for bad in ("cancelled", "khong-ton-tai"):
        with pytest.raises(sa.exc.IntegrityError) as exc:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, "
                        "status, source_status, created_at, updated_at) "
                        "VALUES (:i, 'mini_crm', 'crm-a', :e, :u, :s, :s, now(), now())"
                    ),
                    {"i": uuid.uuid4(), "e": f"D-{bad}", "u": unit_id, "s": bad},
                )
        assert "ck_deals_status" in str(exc.value)


def test_sold_deal_requires_sold_at(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id)

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, "
                    "status, source_status, created_at, updated_at) "
                    "VALUES (:i, 'mini_crm', 'crm-a', 'D-X', :u, 'sold', 'sold', now(), now())"
                ),
                {"i": uuid.uuid4(), "u": unit_id},
            )
    assert "ck_deals_sold_requires_sold_at" in str(exc.value)


def test_only_one_active_deal_per_unit(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id)
        _insert_deal(conn, unit_id, external="D-1", status="reserved")

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_deal(conn, unit_id, external="D-2", status="sold")
    assert "uq_deals_active_per_unit" in str(exc.value)


def test_historical_deals_are_allowed_on_the_same_unit(upgraded):
    """Ràng buộc chỉ chặn giao dịch ĐANG GIỮ — lịch sử và huỷ vẫn chồng lên nhau được."""
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id)
        _insert_deal(conn, unit_id, external="D-1", status="lost")
        _insert_deal(conn, unit_id, external="D-2", status="lost")
        _insert_deal(conn, unit_id, external="D-3", status="lead")
        # Giao dịch đang giữ bị xoá thì một giao dịch giữ mới vẫn vào được.
        _insert_deal(conn, unit_id, external="D-4", status="reserved", deleted=True)
        _insert_deal(conn, unit_id, external="D-5", status="reserved")

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM deals WHERE unit_id = :u"), {"u": unit_id}).scalar() == 5


def test_deals_support_tombstone(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        unit_id = _insert_unit(conn, area_id)
        deal_id = _insert_deal(conn, unit_id, status="sold")
        conn.execute(sa.text("UPDATE deals SET deleted_at = now(), updated_at = now() WHERE id = :i"), {"i": deal_id})
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT deleted_at FROM deals WHERE id = :i"), {"i": deal_id}).scalar() is not None
