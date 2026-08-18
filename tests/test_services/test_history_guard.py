"""Chốt A4: một trường vắng mặt không được âm thầm xoá lịch sử.

Chạy qua ĐÚNG đường thật (`JsonPayloadParser` → `SyncRunService` →
`SourceIdentityService` → `history_guard` → `DomainProjector`), vì điều đáng kiểm
nhất ở đây là THỨ TỰ giữa các tầng: hợp nhất phải chạy trước khi băm, và chốt
phải chạy sau khi so phiên bản. Gọi tắt vào `history_guard` sẽ kiểm đúng hàm
nhưng bỏ qua chính thứ dễ sai.

Chạy: TEST_TARGET=tests/test_services/test_history_guard.py bash scripts/test_db.sh
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, crm_source_records, deals, units, upload_errors, upload_files
from src.services.json_payload import JsonPayloadParser
from src.services.sync_runs import SyncRunService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _refuses_to_wipe(url: str | None) -> str:
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' vì tên không kết thúc bằng '_test'."
    return ""


_SKIP_REASON = _refuses_to_wipe(TEST_DATABASE_URL)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or ""),
]

PROJECT_ID = uuid.UUID("a7b8c9d0-e1f2-4304-a516-6789abcdef8b")
INSTANCE = "crm-history-guard"

RESERVED_AT = "2026-08-01T09:30:00+07:00"
SOLD_AT = "2026-08-08T16:00:00+07:00"
LOST_AT = "2026-08-09T08:00:00+07:00"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async def wipe(session):
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        await session.execute(sa.delete(deals).where(deals.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'A4', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            await session.execute(
                sa.insert(areas),
                [
                    {
                        "id": uuid.uuid4(),
                        "project_id": PROJECT_ID,
                        "area_name": "A1",
                        "unit_type": "2PN",
                        "bedrooms": 2,
                        "area_sqm": 75,
                        "total_units": 50,
                        "created_at": datetime.now(UTC),
                    }
                ],
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)


# --- Helper -------------------------------------------------------------------

_BATCH = {"n": 0}


def _next_batch() -> str:
    _BATCH["n"] += 1
    return f"hg-{uuid.uuid4().hex[:8]}-{_BATCH['n']}"


async def _sync(session_factory, entity, records):
    payload = {
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "source_entity": entity,
        "schema_version": 1,
        "external_batch_id": _next_batch(),
        "project_id": str(PROJECT_ID),
        "records": records,
    }
    envelope = JsonPayloadParser().parse(payload)
    return await SyncRunService(session_factory).run(envelope)


def _record(record_id, data, *, revision=1, completeness=None, operation="upsert"):
    record = {"source_record_id": record_id, "operation": operation, "source_revision": revision, "data": data}
    if completeness is not None:
        record["payload_completeness"] = completeness
    return record


async def _seed_reserved_deal(session_factory, *, revision=1):
    """Một căn + một giao dịch đang giữ chỗ, có `reserved_at`. Nền của mọi test."""
    await _sync(
        session_factory,
        "units",
        [_record("U-1", {"area_name": "A1", "unit_type": "2PN", "unit_code": "A1-01", "status": "reserved"})],
    )
    await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-1", {"external_unit_id": "U-1", "status": "reserved", "reserved_at": RESERVED_AT}, revision=revision
            )
        ],
    )


async def _deal_row(session_factory) -> dict:
    async with session_factory() as session:
        row = (await session.execute(sa.select(deals).where(deals.c.source_instance_id == INSTANCE))).mappings().one()
    return dict(row)


async def _errors(session_factory, sync_run_id) -> list[dict]:
    async with session_factory() as session:
        rows = (
            (await session.execute(sa.select(upload_errors).where(upload_errors.c.file_id == uuid.UUID(sync_run_id))))
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# === full: vắng mặt ===========================================================


async def test_full_record_dropping_a_stored_timestamp_is_rejected(session_factory):
    """Kịch bản A4 nguyên bản: reserved → sold mà quên reserved_at."""
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"external_unit_id": "U-1", "status": "sold", "sold_at": SOLD_AT}, revision=2)],
    )

    assert result.projections["rejected"] == 1
    assert result.projections["updated"] == 0
    errors = await _errors(session_factory, result.sync_run_id)
    assert [e["error_code"] for e in errors] == ["HISTORY_TIMESTAMP_DROPPED"]
    assert errors[0]["field_name"] == "reserved_at"
    assert errors[0]["error_category"] == "business"


async def test_the_rejected_update_leaves_the_stored_history_untouched(session_factory):
    """Điều quan trọng nhất: từ chối rồi thì bản sao KHÔNG được đổi gì."""
    await _seed_reserved_deal(session_factory)
    before = await _deal_row(session_factory)

    await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"external_unit_id": "U-1", "status": "sold", "sold_at": SOLD_AT}, revision=2)],
    )

    after = await _deal_row(session_factory)
    assert after["reserved_at"] == before["reserved_at"]
    assert after["status"] == "reserved", "trạng thái cũng không được nhích"


async def test_full_record_keeping_the_timestamp_is_accepted(session_factory):
    """Hành vi mà A4 đòi hỏi ở Mini CRM tương lai."""
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-1",
                {"external_unit_id": "U-1", "status": "sold", "reserved_at": RESERVED_AT, "sold_at": SOLD_AT},
                revision=2,
            )
        ],
    )

    assert result.projections["updated"] == 1
    row = await _deal_row(session_factory)
    assert row["status"] == "sold"
    assert row["reserved_at"] is not None


async def test_absent_timestamp_with_nothing_stored_is_fine(session_factory):
    """Không mất gì thì không có gì để chặn — chốt không được cản đường bình thường."""
    await _sync(
        session_factory,
        "units",
        [_record("U-2", {"area_name": "A1", "unit_type": "2PN", "unit_code": "A1-02", "status": "sold"})],
    )
    result = await _sync(
        session_factory,
        "deals",
        [_record("D-2", {"external_unit_id": "U-2", "status": "sold", "sold_at": SOLD_AT}, revision=1)],
    )
    assert result.projections["inserted"] == 1

    # `lost_at` chưa bao giờ có giá trị, nên bản cập nhật không mang nó cũng không mất gì.
    result = await _sync(
        session_factory,
        "deals",
        [_record("D-2", {"external_unit_id": "U-2", "status": "sold", "sold_at": SOLD_AT}, revision=2)],
    )
    assert result.projections["updated"] == 1


async def test_insert_is_never_blocked(session_factory):
    """Bản ghi mới chưa có gì để mất — chốt chỉ đụng tới update."""
    await _sync(
        session_factory,
        "units",
        [_record("U-3", {"area_name": "A1", "unit_type": "2PN", "unit_code": "A1-03", "status": "reserved"})],
    )
    result = await _sync(
        session_factory,
        "deals",
        [_record("D-3", {"external_unit_id": "U-3", "status": "reserved", "reserved_at": RESERVED_AT})],
    )

    assert result.projections["inserted"] == 1
    assert result.projections["rejected"] == 0


async def test_the_rule_covers_sold_at_and_lost_at_too(session_factory):
    """Ba mốc đối xứng: mất `sold_at` hỏng lịch sử y như mất `reserved_at`."""
    await _sync(
        session_factory,
        "units",
        [_record("U-4", {"area_name": "A1", "unit_type": "2PN", "unit_code": "A1-04", "status": "sold"})],
    )
    await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-4",
                {"external_unit_id": "U-4", "status": "sold", "reserved_at": RESERVED_AT, "sold_at": SOLD_AT},
            )
        ],
    )

    # Bỏ `sold_at`, giữ `reserved_at` → vẫn là đánh rơi lịch sử.
    result = await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-4",
                {"external_unit_id": "U-4", "status": "lost", "reserved_at": RESERVED_AT, "lost_at": LOST_AT},
                revision=2,
            )
        ],
    )

    errors = await _errors(session_factory, result.sync_run_id)
    assert [e["error_code"] for e in errors] == ["HISTORY_TIMESTAMP_DROPPED"]
    assert errors[0]["field_name"] == "sold_at"


# === full: null tường minh ====================================================


async def test_explicit_null_clears_the_timestamp(session_factory):
    """Null tường minh là một KHẲNG ĐỊNH — hệ nguồn có quyền sửa cái nhập nhầm."""
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-1",
                {"external_unit_id": "U-1", "status": "lost", "reserved_at": None, "lost_at": LOST_AT},
                revision=2,
            )
        ],
    )

    assert result.projections["updated"] == 1
    row = await _deal_row(session_factory)
    assert row["reserved_at"] is None
    assert row["status"] == "lost"


async def test_an_explicit_clear_is_warned_about(session_factory, caplog):
    """Xoá hợp lệ vẫn phải để lại vết: một CRM hỏng phát null hàng loạt sẽ bào mòn
    lịch sử mà không ai thấy gì."""
    await _seed_reserved_deal(session_factory)

    with caplog.at_level("WARNING"):
        await _sync(
            session_factory,
            "deals",
            [
                _record(
                    "D-1",
                    {"external_unit_id": "U-1", "status": "lost", "reserved_at": None, "lost_at": LOST_AT},
                    revision=2,
                )
            ],
        )

    assert "sync.history_timestamp_cleared" in caplog.text


async def test_the_clear_count_stays_out_of_the_projection_keys(session_factory):
    """Ba khoá inserted/updated/tombstoned là điều kiện xếp hàng tính lại (Phase 7).
    Thêm một khoá vào `projections` sẽ đổi số học của một thứ chẳng liên quan."""
    await _seed_reserved_deal(session_factory)
    result = await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-1",
                {"external_unit_id": "U-1", "status": "lost", "reserved_at": None, "lost_at": LOST_AT},
                revision=2,
            )
        ],
    )

    async with session_factory() as session:
        summary = await session.scalar(
            sa.select(upload_files.c.error_summary).where(upload_files.c.id == uuid.UUID(result.sync_run_id))
        )

    assert summary["history_timestamps_cleared"] == 1
    assert "history_timestamps_cleared" not in summary["projections"]


async def test_explicit_null_on_a_non_nullable_field_is_rejected(session_factory):
    """null nghĩa là 'xoá giá trị', mà không dòng nào tồn tại được khi thiếu `status`."""
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"external_unit_id": "U-1", "status": None, "reserved_at": RESERVED_AT}, revision=2)],
    )

    errors = await _errors(session_factory, result.sync_run_id)
    assert [e["error_code"] for e in errors] == ["NULL_NOT_ALLOWED"]
    assert errors[0]["field_name"] == "status"
    assert errors[0]["error_category"] == "field"


# === partial ==================================================================


async def test_partial_update_preserves_absent_fields(session_factory):
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"status": "sold", "sold_at": SOLD_AT}, revision=2, completeness="partial")],
    )

    assert result.projections["updated"] == 1
    row = await _deal_row(session_factory)
    assert row["status"] == "sold"
    assert row["reserved_at"] is not None, "vắng mặt ở partial nghĩa là GIỮ NGUYÊN"
    assert row["sold_at"] is not None


async def test_partial_update_carries_the_unit_reference_forward(session_factory):
    """`external_unit_id` cũng phải được chép sang, nếu không giao dịch mất căn."""
    await _seed_reserved_deal(session_factory)
    before = await _deal_row(session_factory)

    await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"status": "sold", "sold_at": SOLD_AT}, revision=2, completeness="partial")],
    )

    assert (await _deal_row(session_factory))["unit_id"] == before["unit_id"]


async def test_partial_explicit_null_clears(session_factory):
    await _seed_reserved_deal(session_factory)

    await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-1", {"status": "lost", "reserved_at": None, "lost_at": LOST_AT}, revision=2, completeness="partial"
            )
        ],
    )

    assert (await _deal_row(session_factory))["reserved_at"] is None


async def test_partial_never_triggers_the_history_guard(session_factory):
    """Ở partial, vắng mặt đã có nghĩa là giữ nguyên — không còn gì để đánh rơi."""
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"status": "sold", "sold_at": SOLD_AT}, revision=2, completeness="partial")],
    )

    assert await _errors(session_factory, result.sync_run_id) == []


async def test_partial_without_a_mirror_row_is_rejected(session_factory):
    """Không có gì để vá. Bịa nốt phần thiếu là bịa dữ liệu."""
    await _sync(
        session_factory,
        "units",
        [_record("U-9", {"area_name": "A1", "unit_type": "2PN", "unit_code": "A1-09", "status": "available"})],
    )

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-9", {"status": "sold", "sold_at": SOLD_AT}, completeness="partial")],
    )

    errors = await _errors(session_factory, result.sync_run_id)
    assert [e["error_code"] for e in errors] == ["PARTIAL_UPDATE_WITHOUT_BASE"]
    assert result.projections["rejected"] == 1


async def test_partial_works_for_units_too(session_factory):
    await _sync(
        session_factory,
        "units",
        [_record("U-5", {"area_name": "A1", "unit_type": "2PN", "unit_code": "A1-05", "status": "available"})],
    )

    result = await _sync(
        session_factory, "units", [_record("U-5", {"status": "sold"}, revision=2, completeness="partial")]
    )

    assert result.projections["updated"] == 1
    async with session_factory() as session:
        row = (await session.execute(sa.select(units).where(units.c.external_unit_id == "U-5"))).mappings().one()
    assert row["status"] == "sold"
    assert row["unit_code"] == "A1-05", "trường vắng mặt phải giữ nguyên"


async def test_an_unknown_completeness_value_is_rejected(session_factory):
    """Không mặc định về 'full': đoán sai ở đây là đoán sai ý nghĩa của MỌI trường
    vắng mặt trong bản ghi."""
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"status": "sold", "sold_at": SOLD_AT}, revision=2, completeness="mostly")],
    )

    errors = await _errors(session_factory, result.sync_run_id)
    assert [e["error_code"] for e in errors] == ["UNSUPPORTED_PAYLOAD_COMPLETENESS"]


# === Thứ tự với tầng phiên bản =================================================


async def test_a_stale_record_dropping_history_is_skipped_not_rejected(session_factory):
    """Bản cũ vốn đã không được ghi. Từ chối nó vì đánh rơi lịch sử là báo lỗi cho
    một thao tác không hề xảy ra."""
    await _seed_reserved_deal(session_factory, revision=5)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"external_unit_id": "U-1", "status": "sold", "sold_at": SOLD_AT}, revision=1)],
    )

    assert result.decisions["skip_stale"] == 1
    assert result.projections["rejected"] == 0
    assert [e["error_code"] for e in await _errors(session_factory, result.sync_run_id)] == []
    assert (await _deal_row(session_factory))["reserved_at"] is not None


async def test_a_same_version_conflict_dropping_history_stays_a_conflict(session_factory):
    """Đụng độ đã giữ nguyên trạng thái cũ; chốt không được biến nó thành lỗi khác."""
    await _seed_reserved_deal(session_factory, revision=1)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"external_unit_id": "U-1", "status": "sold", "sold_at": SOLD_AT}, revision=1)],
    )

    assert result.decisions["conflict"] == 1
    codes = [e["error_code"] for e in await _errors(session_factory, result.sync_run_id)]
    assert codes == ["VERSION_CONFLICT"]
    assert (await _deal_row(session_factory))["reserved_at"] is not None


async def test_a_replayed_identical_record_is_a_duplicate_noop(session_factory):
    """Gửi lại y hệt không được biến thành lỗi lịch sử."""
    await _seed_reserved_deal(session_factory)

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"external_unit_id": "U-1", "status": "reserved", "reserved_at": RESERVED_AT}, revision=1)],
    )

    assert result.decisions["duplicate_noop"] == 1
    assert result.projections["rejected"] == 0


# === Sống lại sau tombstone ===================================================


async def test_resurrection_dropping_history_is_rejected(session_factory):
    """Xoá mềm giữ lại giá trị chính vì thế. Làm sống lại mà đánh rơi vẫn là mất."""
    await _seed_reserved_deal(session_factory)
    await _sync(session_factory, "deals", [_record("D-1", {}, revision=2, operation="delete")])

    result = await _sync(
        session_factory,
        "deals",
        [_record("D-1", {"external_unit_id": "U-1", "status": "sold", "sold_at": SOLD_AT}, revision=3)],
    )

    errors = await _errors(session_factory, result.sync_run_id)
    assert [e["error_code"] for e in errors] == ["HISTORY_TIMESTAMP_DROPPED"]
    assert (await _deal_row(session_factory))["deleted_at"] is not None, "vẫn nằm ở trạng thái đã xoá"


async def test_resurrection_keeping_history_succeeds(session_factory):
    await _seed_reserved_deal(session_factory)
    await _sync(session_factory, "deals", [_record("D-1", {}, revision=2, operation="delete")])

    result = await _sync(
        session_factory,
        "deals",
        [
            _record(
                "D-1",
                {"external_unit_id": "U-1", "status": "sold", "reserved_at": RESERVED_AT, "sold_at": SOLD_AT},
                revision=3,
            )
        ],
    )

    assert result.projections["updated"] == 1
    row = await _deal_row(session_factory)
    assert row["deleted_at"] is None
    assert row["reserved_at"] is not None


# === Chế độ thoát hiểm =========================================================


async def test_preserve_mode_carries_the_old_value_instead_of_rejecting(session_factory, monkeypatch, caplog):
    from src.config import get_settings

    monkeypatch.setenv("SYNC_PRESERVE_DROPPED_TIMESTAMPS", "true")
    get_settings.cache_clear()
    try:
        await _seed_reserved_deal(session_factory)
        with caplog.at_level("WARNING"):
            result = await _sync(
                session_factory,
                "deals",
                [_record("D-1", {"external_unit_id": "U-1", "status": "sold", "sold_at": SOLD_AT}, revision=2)],
            )
    finally:
        get_settings.cache_clear()

    assert result.projections["updated"] == 1
    assert result.projections["rejected"] == 0
    assert (await _deal_row(session_factory))["reserved_at"] is not None
    assert "sync.history_timestamp_preserved" in caplog.text


async def test_preserve_mode_is_off_by_default(session_factory):
    from src.config import get_settings

    get_settings.cache_clear()
    assert get_settings().sync_preserve_dropped_timestamps is False
