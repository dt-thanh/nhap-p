"""Migration 0008 của Mini CRM: `crm_units.listing_price`.

Chạy trên database DÙNG MỘT LẦN (`mc0008_<hex>_test`), tạo và huỷ trong từng
test — cùng khuôn với `test_migration_0002.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from tests.conftest import db_url, run_alembic, sync_url, with_database

MINICRM_ROOT = Path(__file__).resolve().parents[1]
MINICRM_DB_URL = db_url()

pytestmark = pytest.mark.skipif(
    not MINICRM_DB_URL,
    reason="Không có MINICRM_TEST_DATABASE_URL/MINICRM_DATABASE_URL — bỏ qua test cần DB thật",
)

REVISION = "0008_unit_listing_price"
PREVIOUS = "0007_active_password_or_keycloak"


@pytest.fixture
def scratch_db():
    name = f"mc0008_{uuid.uuid4().hex[:12]}_test"
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
    # Nâng cấp từ RỖNG tới ĐÚNG revision này (không phải "head"): file này kiểm
    # 0008 cụ thể, không kiểm những revision xuất hiện sau nó.
    run_alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(sync_url(scratch_db))
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _columns(conn, table):
    return set(
        conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
        ).scalars()
    )


def _unit(conn, **overrides):
    values = {
        "id": uuid.uuid4(),
        "external_id": f"U-{uuid.uuid4().hex[:8]}",
        "area_name": "A1",
        "unit_type": "2PN",
        "unit_code": f"A1-{uuid.uuid4().hex[:4]}",
        "unit_status": "available",
        "source_revision": 1,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO crm_units (id, external_id, area_name, unit_type, unit_code, unit_status, "
            "listing_price, source_revision, created_at, updated_at) "
            "VALUES (:id, :external_id, :area_name, :unit_type, :unit_code, :unit_status, "
            ":listing_price, :source_revision, now(), now())"
        ),
        {**values, "listing_price": values.get("listing_price")},
    )
    return values


def test_head_is_the_new_revision(upgraded):
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION


def test_the_revision_chains_onto_0007_not_a_new_branch(upgraded):
    text = (MINICRM_ROOT / "alembic" / "versions" / "0008_unit_listing_price.py").read_text(encoding="utf-8")
    assert f'down_revision = "{PREVIOUS}"' in text


def test_upgrade_is_safe_to_run_twice(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "upgrade", REVISION)
    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
    finally:
        engine.dispose()


def test_downgrade_returns_to_0007_and_removes_the_column(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "downgrade", PREVIOUS)

    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == PREVIOUS
            assert "listing_price" not in _columns(conn, "crm_units")
            # 0007 vẫn còn nguyên: 0008 chỉ THÊM, không đụng cột nào khác.
            assert {"id", "external_id", "unit_code", "unit_status"} <= _columns(conn, "crm_units")
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_ends_on_head(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "downgrade", PREVIOUS)
    run_alembic(upgraded["url"], "upgrade", REVISION)
    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
            assert "listing_price" in _columns(conn, "crm_units")
    finally:
        engine.dispose()


def test_the_column_exists_and_is_nullable(upgraded):
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT is_nullable, numeric_precision, numeric_scale FROM information_schema.columns "
                "WHERE table_name = 'crm_units' AND column_name = 'listing_price'"
            )
        ).one()
        assert row.is_nullable == "YES"
        assert row.numeric_precision == 18
        assert row.numeric_scale == 2


def test_null_listing_price_is_the_default_for_a_row_that_omits_it(upgraded):
    with upgraded["engine"].begin() as conn:
        _unit(conn)
        value = conn.execute(sa.text("SELECT listing_price FROM crm_units LIMIT 1")).scalar_one()
        assert value is None


def test_a_positive_listing_price_is_accepted(upgraded):
    with upgraded["engine"].begin() as conn:
        _unit(conn, listing_price=Decimal("8600000000.00"))
        value = conn.execute(sa.text("SELECT listing_price FROM crm_units LIMIT 1")).scalar_one()
        assert value == Decimal("8600000000.00")


def test_a_zero_listing_price_is_rejected(upgraded):
    with upgraded["engine"].connect() as conn, pytest.raises(IntegrityError):
        with conn.begin():
            _unit(conn, listing_price=Decimal("0"))


def test_a_negative_listing_price_is_rejected(upgraded):
    with upgraded["engine"].connect() as conn, pytest.raises(IntegrityError):
        with conn.begin():
            _unit(conn, listing_price=Decimal("-1"))


def test_no_backend_table_appears_in_the_minicrm_database(upgraded):
    with upgraded["engine"].connect() as conn:
        leaked = list(
            conn.execute(
                sa.text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)"),
                {"t": ["units", "deals", "areas", "projects", "project_price_observations"]},
            ).scalars()
        )
    assert leaked == [], f"database Mini CRM chứa bảng của backend: {leaked}"


def test_the_migration_adds_no_customer_column():
    """Phạm vi CHỈ MỘT cột giá — không mở rộng sang PII.

    Kiểm `op.add_column`/`op.create_check_constraint` thực tế gọi, không kiểm
    toàn văn bản: docstring của 0008 CHỦ Ý nhắc tới `transaction_price` để giải
    thích vì sao nó KHÔNG được thêm — cấm cả chữ đó xuất hiện trong văn xuôi sẽ
    cấm luôn lời giải thích hợp lệ nhất.
    """
    text = (MINICRM_ROOT / "alembic" / "versions" / "0008_unit_listing_price.py").read_text(encoding="utf-8")
    add_column_calls = [line for line in text.splitlines() if "op.add_column" in line or "op.create_check" in line]
    blob = "\n".join(add_column_calls).lower()
    for forbidden in ('"customer', '"phone', '"email', '"commission', '"salesperson', '"payment', "transaction_price"):
        assert forbidden not in blob
