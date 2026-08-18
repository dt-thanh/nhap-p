"""Migration 0002 của Mini CRM: dãy sinh id, dấu vết mirrored, sổ gửi đi đầy đủ.

Chạy trên database DÙNG MỘT LẦN (`mc0002_<hex>_test`), tạo và huỷ trong từng test.

Điều được canh kỹ nhất vẫn là thứ đã canh từ 0001: cây Alembic này là của RIÊNG
Mini CRM. `down_revision` trỏ về `0001_minicrm_initial`, không bao giờ trỏ sang
một revision của backend.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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

REVISION = "0002_minicrm_crud"
PREVIOUS = "0001_minicrm_initial"

NEW_UNIT_COLUMNS = ("mirrored_at", "mirrored_revision", "last_sync_batch_id")
NEW_OUTBOX_COLUMNS = ("attempts", "last_error", "replay_of")


@pytest.fixture
def scratch_db():
    name = f"mc0002_{uuid.uuid4().hex[:12]}_test"
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
    # Phase B (0003_minicrm_hierarchy) thêm một revision NỮA lên trên 0002 — dùng
    # "head" ở đây sẽ khiến `test_head_is_the_new_revision` đỏ mỗi khi có migration
    # mới, dù nó chẳng nói gì về 0002 cả. File này kiểm 0002 CỤ THỂ, nên nó nâng
    # cấp tới ĐÚNG revision đó, không phải "head" — cùng khuôn với `PREVIOUS` ở
    # dưới đã pin cứng "0001_minicrm_initial" cho test downgrade.
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
        "mirrored_at": None,
        "mirrored_revision": None,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO crm_units (id, external_id, area_name, unit_type, unit_code, unit_status, "
            "source_revision, mirrored_at, mirrored_revision, created_at, updated_at) "
            "VALUES (:id, :external_id, :area_name, :unit_type, :unit_code, :unit_status, :source_revision, "
            ":mirrored_at, :mirrored_revision, now(), now())"
        ),
        values,
    )
    return values


def _outbox(conn, **overrides):
    values = {
        "id": uuid.uuid4(),
        "external_batch_id": f"mc-units-{uuid.uuid4().hex[:8]}",
        "entity": "units",
        "payload": '{"records": []}',
        "attempts": 0,
        "replay_of": None,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO crm_outbox (id, external_batch_id, entity, payload, attempts, replay_of, created_at) "
            "VALUES (:id, :external_batch_id, :entity, CAST(:payload AS jsonb), :attempts, :replay_of, now())"
        ),
        values,
    )
    return values


# --- Lên / xuống / chạy lại --------------------------------------------------


def test_head_is_the_new_revision(upgraded):
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION


def test_the_revision_chains_onto_the_minicrm_root_not_the_backend(upgraded):
    """Cây Alembic của Mini CRM phải khép kín. Nối vào một revision của backend
    là trộn hai lịch sử migration, và không có đường lùi sạch cho việc đó."""
    text = (MINICRM_ROOT / "alembic" / "versions" / "0002_minicrm_crud.py").read_text(encoding="utf-8")
    assert f'down_revision: str | None = "{PREVIOUS}"' in text
    for backend_revision in ("0015_ranking_results", "0007_s3_domain_model", "0001_initial_schema"):
        assert backend_revision not in text


def test_upgrade_is_safe_to_run_twice(upgraded):
    upgraded["engine"].dispose()
    # "head" ở đây sẽ KHÔNG kiểm tính idempotent — nó sẽ nhảy sang 0003 (một
    # migration THẬT SỰ mới), không phải chạy lại migration NÀY lần thứ hai.
    # Nhắm đúng REVISION mới đo được đúng thứ tên test đang khẳng định.
    run_alembic(upgraded["url"], "upgrade", REVISION)
    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
    finally:
        engine.dispose()


def test_downgrade_returns_to_0001_and_removes_everything_0002_added(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "downgrade", PREVIOUS)

    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == PREVIOUS
            assert _columns(conn, "crm_units") & set(NEW_UNIT_COLUMNS) == set()
            assert _columns(conn, "crm_deals") & set(NEW_UNIT_COLUMNS) == set()
            assert _columns(conn, "crm_outbox") & set(NEW_OUTBOX_COLUMNS) == set()
            sequences = set(
                conn.execute(sa.text("SELECT sequence_name FROM information_schema.sequences")).scalars()
            )
            assert sequences & {"crm_unit_external_seq", "crm_deal_external_seq"} == set()
            # Ba bảng gốc còn nguyên: 0002 chỉ THÊM, không đụng vào 0001.
            assert {"crm_units", "crm_deals", "crm_outbox"} <= set(
                conn.execute(
                    sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                ).scalars()
            )
    finally:
        engine.dispose()


# --- Dãy sinh external_id ----------------------------------------------------


def test_both_sequences_exist_and_start_at_one(upgraded):
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT nextval('crm_unit_external_seq')")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT nextval('crm_deal_external_seq')")).scalar_one() == 1


def test_the_two_sequences_are_independent(upgraded):
    """Dùng chung một dãy sẽ khiến `U-0001` và `D-0002` cùng tồn tại — đọc log
    thấy số nhảy cách quãng và không ai biết vì sao."""
    with upgraded["engine"].connect() as conn:
        conn.execute(sa.text("SELECT nextval('crm_unit_external_seq')"))
        conn.execute(sa.text("SELECT nextval('crm_unit_external_seq')"))
        assert conn.execute(sa.text("SELECT nextval('crm_deal_external_seq')")).scalar_one() == 1


def test_a_rolled_back_transaction_still_consumes_the_number(upgraded):
    """Giả định A1 nhìn từ tầng lưu trữ: dãy KHÔNG lùi, kể cả khi transaction gọi
    nó bị rollback. Vài id bị bỏ phí là cái giá — và nó rẻ hơn nhiều so với việc
    gắn lịch sử của một căn đã xoá vào một căn hoàn toàn khác."""
    engine = upgraded["engine"]
    with engine.connect() as conn:
        conn.execute(sa.text("SELECT nextval('crm_unit_external_seq')"))
        conn.rollback()
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT nextval('crm_unit_external_seq')")).scalar_one() == 2


# --- Dấu vết mirrored --------------------------------------------------------


@pytest.mark.parametrize("table", ["crm_units", "crm_deals"])
def test_the_mirror_columns_exist_on_both_entity_tables(upgraded, table):
    with upgraded["engine"].connect() as conn:
        assert set(NEW_UNIT_COLUMNS) <= _columns(conn, table)


def test_mirrored_revision_zero_is_rejected(upgraded):
    """`0` sẽ là một lời nói dối: nó trông như "đã đồng bộ ở phiên bản 0", mà
    phiên bản 0 không tồn tại ở đây."""
    with pytest.raises(IntegrityError, match="ck_crm_units_mirrored_revision_positive"):
        with upgraded["engine"].begin() as conn:
            _unit(conn, mirrored_at=datetime.now(UTC), mirrored_revision=0)


def test_a_mirror_timestamp_without_a_revision_is_rejected(upgraded):
    """Một mốc thời gian không kèm phiên bản không nói được ĐIỀU GÌ đã lên tới nơi."""
    with pytest.raises(IntegrityError, match="ck_crm_units_mirrored_pair"):
        with upgraded["engine"].begin() as conn:
            _unit(conn, mirrored_at=datetime.now(UTC), mirrored_revision=None)


def test_a_revision_without_a_timestamp_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_crm_deals_mirrored_pair"):
        with upgraded["engine"].begin() as conn:
            unit = _unit(conn)
            conn.execute(
                sa.text(
                    "INSERT INTO crm_deals (id, external_id, external_unit_id, deal_status, source_revision, "
                    "mirrored_at, mirrored_revision, created_at, updated_at) "
                    "VALUES (:id, 'D-0001', :u, 'lead', 1, NULL, 3, now(), now())"
                ),
                {"id": uuid.uuid4(), "u": unit["external_id"]},
            )


def test_both_mirror_columns_null_is_the_default(upgraded):
    """Chưa từng lên tới backend là trạng thái KHỞI ĐẦU hợp lệ, không phải lỗi."""
    with upgraded["engine"].begin() as conn:
        unit = _unit(conn)
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT mirrored_at, mirrored_revision FROM crm_units WHERE external_id = :e"),
            {"e": unit["external_id"]},
        ).one()
    assert row.mirrored_at is None and row.mirrored_revision is None


# --- Sổ gửi đi ---------------------------------------------------------------


def test_attempts_defaults_to_zero_and_cannot_go_negative(upgraded):
    with upgraded["engine"].begin() as conn:
        batch = _outbox(conn)
    with upgraded["engine"].connect() as conn:
        assert conn.execute(
            sa.text("SELECT attempts FROM crm_outbox WHERE external_batch_id = :b"),
            {"b": batch["external_batch_id"]},
        ).scalar_one() == 0

    with pytest.raises(IntegrityError, match="ck_crm_outbox_attempts_non_negative"):
        with upgraded["engine"].begin() as conn:
            _outbox(conn, attempts=-1)


def test_a_replay_row_cannot_point_at_itself(upgraded):
    """Trỏ về chính mình thì nó vừa là bản gốc vừa là bản sao, và chuỗi truy ngược
    thành một vòng lặp."""
    with pytest.raises(IntegrityError, match="ck_crm_outbox_replay_of_not_self"):
        with upgraded["engine"].begin() as conn:
            batch_id = "mc-units-self"
            _outbox(conn, external_batch_id=batch_id, replay_of=batch_id)


def test_a_blank_replay_reference_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_crm_outbox_replay_of_not_blank"):
        with upgraded["engine"].begin() as conn:
            _outbox(conn, replay_of="")


def test_a_replay_row_pointing_at_another_batch_is_accepted(upgraded):
    with upgraded["engine"].begin() as conn:
        original = _outbox(conn)
        _outbox(conn, replay_of=original["external_batch_id"])
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM crm_outbox WHERE replay_of IS NOT NULL")).scalar_one() == 1


# --- Cô lập ------------------------------------------------------------------


def test_no_backend_table_appears_in_the_minicrm_database(upgraded):
    with upgraded["engine"].connect() as conn:
        leaked = list(
            conn.execute(
                sa.text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)"),
                {"t": ["units", "deals", "areas", "projects", "upload_files", "crm_source_records", "ranking_runs"]},
            ).scalars()
        )
    assert leaked == [], f"database Mini CRM chứa bảng của backend: {leaked}"


def test_the_migration_adds_no_customer_or_price_column():
    """Phase 4 không nới phạm vi dữ liệu một milimet nào."""
    text = (MINICRM_ROOT / "alembic" / "versions" / "0002_minicrm_crud.py").read_text(encoding="utf-8").lower()
    for forbidden in ('"price', '"customer', '"phone', '"email', '"commission', '"salesperson', '"payment'):
        assert forbidden not in text
