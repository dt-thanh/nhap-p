"""Test danh tính + so phiên bản của luồng đồng bộ, trên PostgreSQL THẬT.

Cùng quy ước với `tests/test_services/test_import_records.py`: không mock DB, vì
thứ đang kiểm là ràng buộc UNIQUE/CHECK và ranh giới transaction.

Chạy:  TEST_TARGET=tests/test_services/test_source_identity.py bash scripts/test_db.sh
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
from src.services.sync_runs import SyncRejectedError, SyncRunService

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

# UUID riêng của module này. Dùng chung id với test_import_records.py thì phần
# dọn dẹp của hai bên giẫm lên nhau: `areas` bên kia còn trỏ vào dự án, và
# DELETE FROM projects ở đây nổ khoá ngoại.
PROJECT_ID = uuid.UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")
SOURCE_SYSTEM = "mini_crm"
INSTANCE = "crm-project-a"

T1 = "2026-08-01T00:00:00Z"
T2 = "2026-08-02T00:00:00Z"  # mới hơn T1


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    """Dọn hai đầu: module khác cũng đụng `upload_files`, để lại rác là chúng vỡ."""

    async def wipe(session):
        # Chỉ xoá lô của dự án này; `upload_files` là bảng dùng chung với luồng CSV.
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        # Từ 0007, bản ghi nguồn được CHIẾU xuống `units`/`deals` — phải dọn cả hai.
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id))))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(crm_source_records))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Pilot', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            # Phân khu để tầng chiếu (0007) tra được `area_id` cho căn.
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
                        "total_units": 100,
                        "created_at": datetime.now(UTC),
                    }
                ],
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)


# --- Helper -----------------------------------------------------------------


def _payload(records, *, batch="batch-1", entity="units", **overrides):
    payload = {
        "source_system": SOURCE_SYSTEM,
        "source_instance_id": INSTANCE,
        "source_entity": entity,
        "schema_version": 1,
        "external_batch_id": batch,
        "project_id": str(PROJECT_ID),
        "records": records,
    }
    payload.update(overrides)
    return payload


async def _sync(session_factory, records, *, batch="batch-1", **overrides):
    envelope = JsonPayloadParser().parse(_payload(records, batch=batch, **overrides))
    return await SyncRunService(session_factory).run(envelope)


async def _mirror(session_factory, source_record_id="UNIT-1") -> dict | None:
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    sa.select(crm_source_records).where(crm_source_records.c.source_record_id == source_record_id)
                )
            )
            .mappings()
            .one_or_none()
        )
    return dict(row) if row is not None else None


async def _run_row(session_factory, sync_run_id) -> dict:
    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(upload_files).where(upload_files.c.id == uuid.UUID(sync_run_id))))
            .mappings()
            .one()
        )
    return dict(row)


async def _count_runs(session_factory, project_id: uuid.UUID = PROJECT_ID) -> int:
    """Đếm lô theo DỰ ÁN — `upload_files` dùng chung với luồng CSV của module khác."""
    async with session_factory() as session:
        return await session.scalar(
            sa.select(sa.func.count()).select_from(upload_files).where(upload_files.c.project_id == project_id)
        )


async def _errors(session_factory, sync_run_id) -> list[dict]:
    async with session_factory() as session:
        rows = (
            (await session.execute(sa.select(upload_errors).where(upload_errors.c.file_id == uuid.UUID(sync_run_id))))
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _upsert(record_id="UNIT-1", *, ts=T1, revision=None, data=None):
    """Một bản ghi `units` HỢP LỆ.

    Từ 0007, `data` được tầng chiếu diễn giải thật chứ không còn là khối mờ, nên
    payload phải đủ trường của một căn. Phần `data` truyền vào được trộn thêm để
    các test vẫn đổi được dấu vân payload mà không đụng tới ý nghĩa của chúng.
    """
    payload = {
        "area_name": "A1",
        "unit_type": "2PN",
        # Mã căn theo danh tính nguồn: `uq_units_area_unit_code` đòi duy nhất
        # trong phân khu, dùng chung một mã thì hai căn khác nhau sẽ đụng nhau.
        "unit_code": f"CODE-{record_id}",
        "status": "available",
        **(data if data is not None else {"v": 1}),
    }
    record = {"source_record_id": record_id, "operation": "upsert", "data": payload}
    if revision is not None:
        record["source_revision"] = revision
    else:
        record["source_updated_at"] = ts
    return record


def _delete(record_id="UNIT-1", *, ts=T2, revision=None):
    record = {"source_record_id": record_id, "operation": "delete"}
    if revision is not None:
        record["source_revision"] = revision
    else:
        record["source_updated_at"] = ts
    return record


# --- Bản ghi mới ------------------------------------------------------------


async def test_new_source_record_is_accepted(session_factory):
    result = await _sync(session_factory, [_upsert()])

    assert result.status == "completed"
    assert result.decisions["insert"] == 1
    assert (result.rows_received, result.rows_ok, result.rows_failed) == (1, 1, 0)

    row = await _mirror(session_factory)
    assert row["state"] == "active"
    assert row["last_decision"] == "insert"
    assert row["source_system"] == SOURCE_SYSTEM
    assert row["source_instance_id"] == INSTANCE
    assert row["source_entity"] == "units"
    assert row["external_batch_id"] == "batch-1"
    assert row["deleted_at"] is None
    assert row["conflict_count"] == 0
    assert row["first_seen_at"] == row["last_seen_at"]
    assert row["first_sync_run_id"] == row["last_sync_run_id"]


async def test_unknown_project_is_rejected_before_any_run_is_created(session_factory):
    ghost = uuid.uuid4()
    envelope = JsonPayloadParser().parse(_payload([_upsert()], project_id=str(ghost)))

    with pytest.raises(SyncRejectedError) as exc:
        await SyncRunService(session_factory).run(envelope)

    assert exc.value.error_code == "UNKNOWN_PROJECT"
    # Không tạo bản ghi lô nào: kiểm tra dự án chạy TRƯỚC khi ghi gì xuống DB.
    assert await _count_runs(session_factory, ghost) == 0


# --- So phiên bản -----------------------------------------------------------


async def test_newer_version_updates_the_tracked_record(session_factory):
    first = await _sync(session_factory, [_upsert(ts=T1, data={"unit_code": "CODE-VAR-1"})], batch="b1")
    before = await _mirror(session_factory)

    second = await _sync(session_factory, [_upsert(ts=T2, data={"unit_code": "CODE-VAR-2"})], batch="b2")
    after = await _mirror(session_factory)

    assert second.decisions["update"] == 1
    assert after["source_updated_at"] == datetime(2026, 8, 2, tzinfo=UTC)
    assert after["payload_hash"] != before["payload_hash"]
    assert after["last_decision"] == "update"
    assert after["state"] == "active"
    assert after["last_seen_at"] > before["last_seen_at"]
    # Danh tính và lô đầu tiên không đổi khi cập nhật.
    assert after["id"] == before["id"]
    assert after["first_sync_run_id"] == uuid.UUID(first.sync_run_id)
    assert after["last_sync_run_id"] == uuid.UUID(second.sync_run_id)


async def test_older_version_is_skipped_without_overwriting(session_factory):
    await _sync(session_factory, [_upsert(ts=T2, data={"unit_code": "CODE-VAR-2"})], batch="b1")
    before = await _mirror(session_factory)

    result = await _sync(session_factory, [_upsert(ts=T1, data={"unit_code": "CODE-VAR-1"})], batch="b2")
    after = await _mirror(session_factory)

    assert result.decisions["skip_stale"] == 1
    assert result.status == "completed", "bỏ qua bản cũ là kết quả bình thường, không phải lỗi"
    assert after["payload_hash"] == before["payload_hash"], "bản cũ đã ghi đè bản mới"
    assert after["source_updated_at"] == datetime(2026, 8, 2, tzinfo=UTC)
    assert after["last_decision"] == "skip_stale"
    assert after["last_seen_at"] > before["last_seen_at"], "vẫn phải ghi nhận là đã nhìn thấy"


async def test_same_version_same_hash_is_idempotent_noop(session_factory):
    await _sync(session_factory, [_upsert(ts=T1, data={"unit_code": "CODE-VAR-1"})], batch="b1")
    before = await _mirror(session_factory)

    result = await _sync(session_factory, [_upsert(ts=T1, data={"unit_code": "CODE-VAR-1"})], batch="b2")
    after = await _mirror(session_factory)

    assert result.decisions["duplicate_noop"] == 1
    assert result.status == "completed"
    assert after["payload_hash"] == before["payload_hash"]
    assert after["conflict_count"] == 0
    assert await _errors(session_factory, result.sync_run_id) == []


async def test_same_version_different_hash_is_a_conflict(session_factory):
    await _sync(session_factory, [_upsert(ts=T1, data={"unit_code": "CODE-VAR-1"})], batch="b1")
    before = await _mirror(session_factory)

    result = await _sync(session_factory, [_upsert(ts=T1, data={"unit_code": "CODE-VAR-999"})], batch="b2")
    after = await _mirror(session_factory)

    assert result.decisions["conflict"] == 1
    # Phase 5.5 P0 (5A): đụng độ là một QUYẾT ĐỊNH đã ghi nhận, không phải bản ghi
    # hỏng — lô một bản ghi mà bản ghi đó là conflict (không có gì khác hỏng)
    # không còn là 'failed'. Xem SyncRunService._terminal_status.
    assert result.status == "completed_with_conflicts", "đụng độ không tự động là thất bại toàn phần"

    # Trạng thái đã chấp nhận KHÔNG đổi — đây là điểm mấu chốt.
    assert after["payload_hash"] == before["payload_hash"]
    assert after["last_decision"] == "conflict"
    assert after["conflict_count"] == 1
    assert after["conflict_payload_hash"] != before["payload_hash"]
    assert after["conflict_detected_at"] is not None

    errors = await _errors(session_factory, result.sync_run_id)
    assert [(e["error_category"], e["error_code"]) for e in errors] == [("conflict", "VERSION_CONFLICT")]
    assert errors[0]["json_path"] == "$.records[0]"
    assert errors[0]["source_record_id"] == "UNIT-1"


async def test_numeric_revision_is_preferred_over_timestamp(session_factory):
    """CRM cấp số thứ tự thì dùng nó — không phụ thuộc đồng hồ của hệ nguồn."""
    await _sync(session_factory, [_upsert(revision=10, data={"unit_code": "CODE-VAR-1"})], batch="b1")

    # Revision nhỏ hơn → cũ hơn, dù gửi sau.
    stale = await _sync(session_factory, [_upsert(revision=9, data={"unit_code": "CODE-VAR-2"})], batch="b2")
    assert stale.decisions["skip_stale"] == 1

    fresh = await _sync(session_factory, [_upsert(revision=11, data={"unit_code": "CODE-VAR-3"})], batch="b3")
    assert fresh.decisions["update"] == 1

    row = await _mirror(session_factory)
    assert row["source_revision"] == 11


async def test_out_of_order_records_converge_to_the_newest(session_factory):
    """Ba lô đến ngược thứ tự vẫn hội tụ về bản mới nhất."""
    await _sync(session_factory, [_upsert(revision=2, data={"unit_code": "CODE-VAR-2"})], batch="b1")
    await _sync(session_factory, [_upsert(revision=1, data={"unit_code": "CODE-VAR-1"})], batch="b2")
    await _sync(session_factory, [_upsert(revision=3, data={"unit_code": "CODE-VAR-3"})], batch="b3")
    await _sync(session_factory, [_upsert(revision=1, data={"unit_code": "CODE-VAR-1"})], batch="b4")

    row = await _mirror(session_factory)
    assert row["source_revision"] == 3
    assert row["last_decision"] == "skip_stale", "lô cuối là bản cũ nên bị bỏ qua"
    # Trạng thái hội tụ phải là của revision 3 — dựng kỳ vọng từ CHÍNH payload mà
    # helper sinh ra, không chép cứng dấu vân.
    from src.services.json_payload import payload_fingerprint

    assert row["payload_hash"] == payload_fingerprint(
        "upsert", _upsert(revision=3, data={"unit_code": "CODE-VAR-3"})["data"]
    )


# --- Tombstone --------------------------------------------------------------


async def test_delete_creates_a_tombstone(session_factory):
    await _sync(session_factory, [_upsert(ts=T1)], batch="b1")
    result = await _sync(session_factory, [_delete(ts=T2)], batch="b2")

    row = await _mirror(session_factory)
    assert result.decisions["tombstone"] == 1
    assert result.status == "completed"
    assert row["state"] == "tombstoned"
    assert row["deleted_at"] is not None
    assert row["last_decision"] == "tombstone"


async def test_delete_for_an_unseen_record_is_still_tracked(session_factory):
    """Xoá một bản ghi chưa từng thấy vẫn phải ghi nhận.

    Bỏ qua thì một lô upsert CŨ đến sau sẽ tạo ra bản ghi mà hệ nguồn đã xoá.
    """
    result = await _sync(session_factory, [_delete(ts=T2)], batch="b1")

    row = await _mirror(session_factory)
    assert result.decisions["tombstone"] == 1
    assert row["state"] == "tombstoned"
    assert row["deleted_at"] is not None


async def test_older_upsert_cannot_resurrect_a_tombstone(session_factory):
    await _sync(session_factory, [_upsert(revision=1)], batch="b1")
    await _sync(session_factory, [_delete(revision=5)], batch="b2")
    tombstoned = await _mirror(session_factory)

    result = await _sync(session_factory, [_upsert(revision=4, data={"v": "zombie"})], batch="b3")
    after = await _mirror(session_factory)

    assert result.decisions["skip_stale"] == 1
    assert after["state"] == "tombstoned", "bản ghi đã xoá bị làm sống lại bằng dữ liệu cũ"
    assert after["deleted_at"] == tombstoned["deleted_at"]
    assert after["payload_hash"] == tombstoned["payload_hash"]


async def test_newer_upsert_may_resurrect_a_tombstone(session_factory):
    """CRM xoá rồi tạo lại là chuyện bình thường — CRM là nguồn sự thật."""
    await _sync(session_factory, [_delete(revision=5)], batch="b1")
    result = await _sync(session_factory, [_upsert(revision=6, data={"v": "again"})], batch="b2")

    row = await _mirror(session_factory)
    assert result.decisions["update"] == 1
    assert row["state"] == "active"
    assert row["deleted_at"] is None


# --- Idempotent theo lô -----------------------------------------------------


async def test_replaying_the_same_external_batch_id_creates_no_second_run(session_factory):
    first = await _sync(session_factory, [_upsert(ts=T1)], batch="batch-same")
    second = await _sync(session_factory, [_upsert(ts=T2, data={"unit_code": "CODE-VAR-2"})], batch="batch-same")

    assert second.replayed is True
    assert second.sync_run_id == first.sync_run_id
    assert second.status == first.status

    assert await _count_runs(session_factory) == 1

    # Nội dung lô thứ hai KHÔNG được xử lý, dù nó mới hơn.
    row = await _mirror(session_factory)
    assert row["source_updated_at"] == datetime(2026, 8, 1, tzinfo=UTC)
    assert row["last_decision"] == "insert"


async def test_same_batch_id_from_another_source_instance_is_a_different_run(session_factory):
    """Danh tính lô gồm cả hệ nguồn và kết nối, không chỉ mã lô."""
    first = await _sync(session_factory, [_upsert(ts=T1)], batch="batch-x")
    envelope = JsonPayloadParser().parse(
        _payload([_upsert("UNIT-2", ts=T1)], batch="batch-x", source_instance_id="crm-project-b")
    )
    second = await SyncRunService(session_factory).run(envelope)

    assert second.replayed is False
    assert second.sync_run_id != first.sync_run_id


# --- Đếm và trạng thái kết thúc ---------------------------------------------


async def test_counters_match_actual_processing(session_factory):
    await _sync(session_factory, [_upsert("UNIT-1", revision=5)], batch="b0")

    result = await _sync(
        session_factory,
        [
            _upsert("UNIT-1", revision=6),  # update
            _upsert("UNIT-2", revision=1),  # insert
            _upsert("UNIT-3", revision=1),  # insert
            _delete("UNIT-3", revision=2),  # ... nhưng trùng danh tính trong lô → lỗi
        ],
        batch="b1",
    )

    assert result.rows_received == 4
    assert result.rows_ok == 3, "3 bản ghi qua được parser"
    assert result.rows_failed == 1, "1 bản ghi trùng danh tính trong cùng lô"
    assert result.decisions["update"] == 1
    assert result.decisions["insert"] == 2
    assert sum(result.decisions.values()) == result.rows_ok
    assert result.status == "partially_completed"

    run = await _run_row(session_factory, result.sync_run_id)
    assert run["rows_received"] == 4
    assert run["rows_ok"] == 3
    assert run["rows_failed"] == 1
    assert run["error_summary"]["decisions"]["insert"] == 2
    assert run["error_summary"]["applied"] == 3
    assert run["finished_at"] is not None


async def test_all_records_rejected_makes_the_run_failed(session_factory):
    result = await _sync(session_factory, [{"source_record_id": "U-1", "data": {}}], batch="b1")

    assert result.status == "failed"
    assert (result.rows_ok, result.rows_failed) == (0, 1)
    run = await _run_row(session_factory, result.sync_run_id)
    assert run["status"] == "failed"
    assert run["finished_at"] is not None


async def test_pure_replay_of_all_records_is_completed(session_factory):
    await _sync(session_factory, [_upsert("UNIT-1", ts=T1), _upsert("UNIT-2", ts=T1)], batch="b1")
    result = await _sync(session_factory, [_upsert("UNIT-1", ts=T1), _upsert("UNIT-2", ts=T1)], batch="b2")

    assert result.decisions["duplicate_noop"] == 2
    assert result.status == "completed", "nạp lại y hệt là thành công, không phải lỗi"


async def test_run_metadata_is_recorded(session_factory):
    result = await _sync(
        session_factory,
        [_upsert()],
        batch="b1",
        sync_mode="full_snapshot",
        source_cursor="cur-9",
        # Từ Phase 5, lô `full_snapshot` bắt buộc mang metadata ảnh chụp.
        snapshot={
            "snapshot_id": "SNAP-T1",
            "chunk_index": 0,
            "chunk_total": 1,
            "snapshot_complete": True,
            "scope": {"entities": ["unit"]},
        },
    )
    run = await _run_row(session_factory, result.sync_run_id)

    assert run["source_system"] == SOURCE_SYSTEM
    assert run["source_instance_id"] == INSTANCE
    assert run["source_entity"] == "units"
    assert run["input_format"] == "json"
    assert run["transport_mode"] == "api_push"
    assert run["sync_mode"] == "full_snapshot"
    assert run["schema_version"] == 1
    assert run["external_batch_id"] == "b1"
    assert run["last_source_cursor"] == "cur-9"
    # Lô đẩy qua API không có file.
    assert run["filename"] is None
    assert run["checksum"] is None


# --- Thất bại và chạy lại ---------------------------------------------------


async def test_failure_during_processing_leaves_the_run_failed_not_pending(session_factory, monkeypatch):
    """Vỡ giữa lúc xử lý → lô kết thúc ở `failed`, không treo `pending`.

    Đây là bài học của S1 áp cho luồng mới: lệnh đặt `processing` nằm trong
    transaction bị rollback, nên phải có một transaction KHÁC đóng lô lại.
    """
    from src.services import source_identity

    async def explode(self, *args, **kwargs):
        raise RuntimeError("hỏng giữa chừng")

    monkeypatch.setattr(source_identity.SourceIdentityService, "apply", explode)

    envelope = JsonPayloadParser().parse(_payload([_upsert()], batch="boom"))
    with pytest.raises(RuntimeError):
        await SyncRunService(session_factory).run(envelope)

    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(upload_files).where(upload_files.c.external_batch_id == "boom")))
            .mappings()
            .one()
        )

    assert row["status"] == "failed", "lô treo ở pending — đúng lỗi mà S1 đã sửa cho luồng CSV"
    assert row["status"] != "pending"
    assert row["finished_at"] is not None
    # Không để lại dữ liệu nửa vời.
    assert await _mirror(session_factory) is None


async def test_retry_after_failure_is_safe(session_factory, monkeypatch):
    """Chạy lại sau khi gỡ nguyên nhân: không nhân đôi bản ghi, không sai số đếm."""
    from src.services import source_identity

    original = source_identity.SourceIdentityService.apply

    async def explode(self, *args, **kwargs):
        raise RuntimeError("hỏng giữa chừng")

    monkeypatch.setattr(source_identity.SourceIdentityService, "apply", explode)
    envelope = JsonPayloadParser().parse(_payload([_upsert()], batch="retry-1"))
    with pytest.raises(RuntimeError):
        await SyncRunService(session_factory).run(envelope)

    monkeypatch.setattr(source_identity.SourceIdentityService, "apply", original)
    # Lô mới cho cùng dữ liệu — lô hỏng vẫn giữ external_batch_id cũ.
    result = await _sync(session_factory, [_upsert()], batch="retry-2")

    assert result.status == "completed"
    assert result.decisions["insert"] == 1

    async with session_factory() as session:
        mirrors = await session.scalar(sa.select(sa.func.count()).select_from(crm_source_records))
    assert mirrors == 1, "chạy lại đã tạo bản ghi trùng"
