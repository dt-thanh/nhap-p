"""Migration 0001 của Mini CRM — và bằng chứng hai lịch sử Alembic tách nhau.

Điều được canh kỹ nhất KHÔNG phải là các bảng được tạo đúng, mà là hai hệ thống
KHÔNG dính vào nhau: database Mini CRM không chứa bảng của backend, database
backend không chứa bảng của Mini CRM, và hai bảng `alembic_version` mang hai
revision khác nhau.

Chạy trên database DÙNG MỘT LẦN (`mc0001_<hex>_test`), tạo và huỷ trong từng test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from tests.conftest import db_url

MINICRM_ROOT = Path(__file__).resolve().parents[1]
MINICRM_DB_URL = db_url()

pytestmark = pytest.mark.skipif(
    not MINICRM_DB_URL,
    reason="Không có MINICRM_TEST_DATABASE_URL/MINICRM_DATABASE_URL — bỏ qua test cần DB thật",
)

REVISION = "0001_minicrm_initial"

# Bảng của BACKEND. Không cái nào được xuất hiện trong database Mini CRM.
BACKEND_TABLES = (
    "units",
    "deals",
    "areas",
    "projects",
    "absorption_daily",
    "upload_files",
    "crm_source_records",
    "feature_snapshots",
    "ranking_configs",
    "ranking_runs",
    "ranking_scores",
)
MINICRM_TABLES = ("crm_units", "crm_deals", "crm_outbox")


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> None:
    """Chạy alembic của MINI CRM (cwd = minicrm/), với MINICRM_DATABASE_URL riêng."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=MINICRM_ROOT,
        env={**os.environ, "MINICRM_DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def scratch_db():
    name = f"mc0001_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(MINICRM_DB_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(MINICRM_DB_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name}
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    # PIN đúng revision này, KHÔNG dùng `head`. Từ Phase 4 cây Alembic đã dài
    # thêm (`0002_minicrm_crud`), và một test về migration 0001 mà chạy theo head
    # sẽ lặng lẽ đổi đối tượng nó đang kiểm mỗi lần có revision mới — rồi hỏng vì
    # một lý do chẳng liên quan gì tới 0001.
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _unit(conn, **overrides):
    values = {
        "id": uuid.uuid4(),
        "external_id": f"U-{uuid.uuid4().hex[:8]}",
        "area_name": "A1",
        "unit_type": "2PN",
        "unit_code": f"A1-{uuid.uuid4().hex[:4]}",
        "unit_status": "available",
        "source_revision": 1,
        "deleted_at": None,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO crm_units (id, external_id, area_name, unit_type, unit_code, unit_status, "
            "source_revision, deleted_at, created_at, updated_at) "
            "VALUES (:id, :external_id, :area_name, :unit_type, :unit_code, :unit_status, :source_revision, "
            ":deleted_at, now(), now())"
        ),
        values,
    )
    return values


def _deal(conn, unit_external_id, **overrides):
    values = {
        "id": uuid.uuid4(),
        "external_id": f"D-{uuid.uuid4().hex[:8]}",
        "external_unit_id": unit_external_id,
        "deal_status": "reserved",
        "reserved_at": datetime.now(UTC),
        "sold_at": None,
        "lost_at": None,
        "source_revision": 1,
        "deleted_at": None,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO crm_deals (id, external_id, external_unit_id, deal_status, reserved_at, sold_at, "
            "lost_at, source_revision, deleted_at, created_at, updated_at) "
            "VALUES (:id, :external_id, :external_unit_id, :deal_status, :reserved_at, :sold_at, :lost_at, "
            ":source_revision, :deleted_at, now(), now())"
        ),
        values,
    )
    return values


# --- 1/2/11. Lên, xuống, và chạy lại được -----------------------------------


def test_upgrade_creates_the_three_minicrm_tables(upgraded):
    with upgraded["engine"].connect() as conn:
        found = set(
            conn.execute(
                sa.text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)"),
                {"t": list(MINICRM_TABLES)},
            ).scalars()
        )
    assert found == set(MINICRM_TABLES)


def test_downgrade_removes_every_minicrm_table(upgraded):
    upgraded["engine"].dispose()
    _alembic(upgraded["url"], "downgrade", "base")

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            found = list(
                conn.execute(
                    sa.text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)"),
                    {"t": list(MINICRM_TABLES)},
                ).scalars()
            )
    finally:
        engine.dispose()
    assert found == []


def test_upgrade_is_safe_to_run_twice(upgraded):
    """`upgrade` lần hai lên cùng revision là không-làm-gì, không phải một lỗi."""
    upgraded["engine"].dispose()
    _alembic(upgraded["url"], "upgrade", REVISION)

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()
    assert revision == REVISION


# --- 3/4/5. Cô lập hai hệ thống ---------------------------------------------


def test_minicrm_alembic_history_is_its_own(upgraded):
    """Bảng `alembic_version` của Mini CRM chỉ mang revision của Mini CRM.

    Một revision của backend xuất hiện ở đây nghĩa là hai cây migration đã bị nối
    vào nhau — và không có đường lùi sạch cho việc đó.
    """
    with upgraded["engine"].connect() as conn:
        revisions = list(conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalars())
    assert revisions == [REVISION]
    assert not any(r.startswith("00") and "minicrm" not in r for r in revisions)


def test_backend_tables_do_not_exist_in_the_minicrm_database(upgraded):
    with upgraded["engine"].connect() as conn:
        leaked = list(
            conn.execute(
                sa.text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)"),
                {"t": list(BACKEND_TABLES)},
            ).scalars()
        )
    assert leaked == [], f"database Mini CRM chứa bảng của backend: {leaked}"


def test_minicrm_migration_never_references_backend_tables():
    """Migration của Mini CRM không được nhắc tới bảng của backend.

    Kiểm ở mức VĂN BẢN vì đây là thứ duy nhất còn đúng kể cả khi ai đó chạy
    migration này nhầm vào database của backend.
    """
    text = (MINICRM_ROOT / "alembic" / "versions" / "0001_minicrm_initial.py").read_text(encoding="utf-8")
    for table in ("upload_files", "crm_source_records", "absorption_daily", "ranking_scores"):
        assert f'"{table}"' not in text and f"'{table}'" not in text


# --- 6/7/8. Ràng buộc soi gương backend -------------------------------------


def test_invalid_unit_status_is_rejected(upgraded):
    """Backend KHÔNG có bảng alias cho unit — nó chỉ nhận đúng bốn giá trị này.

    Chặn ở nguồn nghĩa là payload sai không bao giờ rời khỏi máy.
    """
    with pytest.raises(IntegrityError, match="ck_crm_units_status"):
        with upgraded["engine"].begin() as conn:
            _unit(conn, unit_status="con_trong")


def test_invalid_deal_status_is_rejected(upgraded):
    with upgraded["engine"].begin() as conn:
        unit = _unit(conn)

    with pytest.raises(IntegrityError, match="ck_crm_deals_status"):
        with upgraded["engine"].begin() as conn:
            _deal(conn, unit["external_id"], deal_status="da_dat_coc")


@pytest.mark.parametrize(
    ("status", "constraint"),
    [
        ("reserved", "ck_crm_deals_reserved_requires_reserved_at"),
        ("sold", "ck_crm_deals_sold_requires_sold_at"),
        ("lost", "ck_crm_deals_lost_requires_lost_at"),
    ],
)
def test_status_without_its_timestamp_is_rejected(upgraded, status, constraint):
    """Chốt A4 nhìn từ phía NGUỒN: một giao dịch thiếu mốc của chính trạng thái nó
    mang thì không tồn tại được ở đây, nên nó không gửi đi được."""
    with upgraded["engine"].begin() as conn:
        unit = _unit(conn)

    with pytest.raises(IntegrityError, match=constraint):
        with upgraded["engine"].begin() as conn:
            _deal(conn, unit["external_id"], deal_status=status, reserved_at=None, sold_at=None, lost_at=None)


def test_sold_before_reserved_is_rejected(upgraded):
    with upgraded["engine"].begin() as conn:
        unit = _unit(conn)

    now = datetime.now(UTC)
    with pytest.raises(IntegrityError, match="ck_crm_deals_sold_after_reserved"):
        with upgraded["engine"].begin() as conn:
            _deal(
                conn,
                unit["external_id"],
                deal_status="sold",
                reserved_at=now,
                sold_at=now.replace(year=now.year - 1),
            )


# --- 9/10. Ràng buộc duy nhất ------------------------------------------------


def test_only_one_holding_deal_per_unit(upgraded):
    """Soi gương `uq_deals_active_per_unit` của backend."""
    with upgraded["engine"].begin() as conn:
        unit = _unit(conn)
        _deal(conn, unit["external_id"], deal_status="reserved")

    # `reserved_at=None` để phép thử chạm ĐÚNG chỉ mục duy nhất. Giữ mặc định của
    # helper thì `sold_at` (lấy lúc gọi) hoá ra sớm hơn `reserved_at` (lấy trong
    # helper), và `ck_crm_deals_sold_after_reserved` nổ trước — che mất thứ đang kiểm.
    with pytest.raises(IntegrityError, match="uq_crm_deals_holding_per_unit"):
        with upgraded["engine"].begin() as conn:
            _deal(conn, unit["external_id"], deal_status="sold", reserved_at=None, sold_at=datetime.now(UTC))


def test_lost_deals_do_not_block_a_new_holding_deal(upgraded):
    """Lịch sử KHÔNG bị chặn: `lost` nằm ngoài tập đang giữ."""
    with upgraded["engine"].begin() as conn:
        unit = _unit(conn)
        _deal(conn, unit["external_id"], deal_status="lost", reserved_at=None, lost_at=datetime.now(UTC))
        _deal(conn, unit["external_id"], deal_status="reserved")

    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM crm_deals")).scalar_one() == 2


def test_live_unit_code_is_unique_within_an_area(upgraded):
    with upgraded["engine"].begin() as conn:
        _unit(conn, area_name="A1", unit_type="2PN", unit_code="A1-01-01")

    with pytest.raises(IntegrityError, match="uq_crm_units_live_code"):
        with upgraded["engine"].begin() as conn:
            _unit(conn, area_name="A1", unit_type="2PN", unit_code="A1-01-01")


def test_a_tombstoned_unit_frees_its_code_but_not_its_external_id(upgraded):
    """Mã căn được giải phóng khi tombstone; `external_id` thì KHÔNG BAO GIỜ.

    Đó là giả định A1 của hợp đồng: danh tính bền vững trọn đời, không dùng lại.
    """
    with upgraded["engine"].begin() as conn:
        first = _unit(conn, unit_code="A1-01-01", deleted_at=datetime.now(UTC))
        _unit(conn, unit_code="A1-01-01")

    with pytest.raises(IntegrityError, match="uq_crm_units_external_id"):
        with upgraded["engine"].begin() as conn:
            _unit(conn, external_id=first["external_id"], unit_code="A1-99-99")


def test_a_deal_pointing_at_an_unknown_unit_is_rejected(upgraded):
    """Backend cũng từ chối (`UNKNOWN_UNIT_REFERENCE`), nhưng chặn ở đây nghĩa là
    payload mồ côi không bao giờ rời khỏi máy."""
    with pytest.raises(IntegrityError, match="fk_crm_deals_external_unit_id"):
        with upgraded["engine"].begin() as conn:
            _deal(conn, "U-NEVER-EXISTED")
