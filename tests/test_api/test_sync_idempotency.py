"""Danh tính và idempotency, kiểm bằng chính 12 fixture tổng hợp qua hợp đồng v1.

Bộ test này chạy các fixture trong `docs/crm/fixtures/` qua đường HTTP thật (ASGI
in-process) và khẳng định từng kịch bản cho ra ĐÚNG quyết định mà
`fixtures/README.md` đã hứa. Nhờ vậy fixture không trôi thành tài liệu chết: đổi
hành vi mà quên sửa fixture, hoặc sửa fixture mà quên hành vi, đều làm test đỏ.

Phủ đúng danh sách của Phase 4:

| Hành vi | Test |
|---|---|
| Phân giải danh tính nguồn | `test_identity_is_scoped_to_source_instance` |
| Idempotency mức lô | `test_replaying_a_batch_returns_the_original_run` |
| Idempotency mức bản ghi | `test_same_record_in_a_new_batch_is_a_duplicate_noop` |
| Thứ tự phiên bản | `test_newer_revision_updates_the_mirror` |
| Từ chối bản ghi không phiên bản | `test_record_without_version_is_rejected` |
| Từ chối lệnh xoá không phiên bản | `test_delete_without_version_is_rejected` |
| Từ chối bản cũ | `test_stale_revision_is_skipped` |
| Đụng độ cùng phiên bản | `test_same_version_different_content_is_a_conflict` |
| Băm theo trường đã ánh xạ | `test_unmapped_field_change_is_not_a_conflict` |
| Tombstone | `test_explicit_delete_tombstones_the_mirror` |
| Chạy lại lô | `test_replaying_a_batch_returns_the_original_run` |
| Chạy lại lô hỏng | `test_failed_run_can_be_reprocessed_after_the_cause_is_fixed` |
| Giao dịch trước căn | `test_deal_before_unit_is_rejected` |
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.main import app
from src.models.tables import (
    areas,
    crm_source_records,
    deals,
    sync_credentials,
    sync_payloads,
    units,
    upload_errors,
    upload_files,
)

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

# Đúng dự án mà mọi fixture trỏ tới.
PROJECT_ID = uuid.UUID("5117d1c0-0000-4000-8000-000000000001")
INSTANCE = "synthetic-mini-crm"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "docs" / "crm" / "fixtures"

UNITS_URL = "/api/v1/sync/units"
DEALS_URL = "/api/v1/sync/deals"


def fixture(name: str, **overrides) -> dict:
    payload = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def db_env(session_factory, monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id))))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(
            sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id.like("synthetic-%"))
        )
        # RESTRICT từ 0010: payload phải đi trước lô.
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(
            sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id.like("synthetic-%"))
        )
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'SYNTH', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            await session.execute(
                sa.insert(areas).values(
                    id=uuid.uuid4(),
                    project_id=PROJECT_ID,
                    area_name="A1",
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=75,
                    total_units=100,
                    created_at=datetime.now(UTC),
                )
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


@pytest_asyncio.fixture
async def issued(session_factory):
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            return await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=INSTANCE, label="idempotency test"
            )


@pytest_asyncio.fixture
async def client(issued):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": issued.api_key}
    ) as authorized:
        yield authorized


async def _decisions(client, url, payload) -> tuple[int, dict]:
    response = await client.post(url, json=payload)
    body = response.json()
    return response.status_code, body


async def _seed_units(client):
    """Lô 01: ba căn. Nền cho hầu hết kịch bản phía sau."""
    status, body = await _decisions(client, UNITS_URL, fixture("01_units_incremental"))
    assert status == 202, body
    assert body["decisions"]["insert"] == 3
    return body


def _mirror(session_factory, external_id):
    async def read():
        async with session_factory() as session:
            row = (
                (
                    await session.execute(
                        sa.select(units).where(
                            units.c.source_instance_id == INSTANCE,
                            units.c.external_unit_id == external_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            return dict(row) if row else None

    return read()


# --- Phân giải danh tính -----------------------------------------------------


async def test_first_sight_of_a_record_is_an_insert(client, session_factory):
    body = await _seed_units(client)

    assert body["projections"]["inserted"] == 3
    row = await _mirror(session_factory, "SYNTH-U-0001")
    assert row is not None
    assert row["unit_code"] == "A1-01-01"
    assert row["status"] == "available"


async def test_identity_is_scoped_to_source_instance(client, session_factory, issued):
    """Cùng `external_id`, khác instance → hai bản ghi khác nhau.

    Danh tính là `(source_system, source_instance_id, entity, record_id)`. Thiếu
    vế instance thì hai hệ nguồn cùng đánh số từ 1 sẽ ghi đè lẫn nhau.
    """
    from src.services.sync_credentials import SyncCredentialService

    await _seed_units(client)

    other_instance = "synthetic-other-crm"
    async with session_factory() as session:
        async with session.begin():
            other = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=other_instance, label="other"
            )

    payload = fixture("01_units_incremental")
    payload["source_instance_id"] = other_instance
    payload["external_batch_id"] = "SYNTH-BATCH-OTHER"
    # Mã căn phải khác: `uq_units_area_unit_code` là duy nhất theo phân khu, không
    # theo instance — hai hệ nguồn không được cùng khai một mã căn còn sống.
    for index, record in enumerate(payload["records"]):
        record["payload"]["unit_code"] = f"OTHER-{index}"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": other.api_key}
    ) as other_client:
        status, body = await _decisions(other_client, UNITS_URL, payload)

    assert status == 202, body
    assert body["decisions"]["insert"] == 3, "instance khác phải là bản ghi MỚI, không phải trùng"

    async with session_factory() as session:
        total = await session.scalar(
            sa.select(sa.func.count())
            .select_from(crm_source_records)
            .where(crm_source_records.c.source_record_id == "SYNTH-U-0001")
        )
    assert total == 2, "cùng external_id ở hai instance phải là hai danh tính"


# --- Idempotency mức lô và mức bản ghi --------------------------------------


async def test_replaying_a_batch_returns_the_original_run(client, session_factory):
    """Fixture 03 gửi lại đúng `external_batch_id` của lô 01."""
    first = await _seed_units(client)

    status, replayed = await _decisions(client, UNITS_URL, fixture("03_replay_same_batch"))

    assert status == 200
    assert replayed["replayed"] is True
    assert replayed["sync_run_id"] == first["sync_run_id"]

    async with session_factory() as session:
        runs = await session.scalar(
            sa.select(sa.func.count()).select_from(upload_files).where(upload_files.c.project_id == PROJECT_ID)
        )
    assert runs == 1, "chạy lại đã tạo thêm một lô"


async def test_same_record_in_a_new_batch_is_a_duplicate_noop(client):
    """Idempotency mức BẢN GHI: lô mới, nội dung y hệt → không đổi gì.

    Đây là lưới an toàn khi hệ nguồn đổi `external_batch_id` giữa các lần retry —
    lúc đó idempotency mức lô không cứu được.
    """
    await _seed_units(client)

    payload = fixture("01_units_incremental", external_batch_id="SYNTH-BATCH-NEW-ID")
    status, body = await _decisions(client, UNITS_URL, payload)

    assert status == 202
    assert body["replayed"] is False, "lô mới thật sự được xử lý"
    assert body["decisions"]["duplicate_noop"] == 3
    assert body["decisions"]["insert"] == 0
    assert body["projections"]["untouched"] == 3


# --- Thứ tự phiên bản --------------------------------------------------------


async def test_newer_revision_updates_the_mirror(client, session_factory):
    await _seed_units(client)

    payload = fixture("01_units_incremental", external_batch_id="SYNTH-BATCH-NEWER")
    payload["records"] = [payload["records"][0]]
    payload["records"][0]["source_revision"] = 5
    payload["records"][0]["payload"]["unit_status"] = "blocked"

    status, body = await _decisions(client, UNITS_URL, payload)

    assert status == 202
    assert body["decisions"]["update"] == 1
    row = await _mirror(session_factory, "SYNTH-U-0001")
    assert row["status"] == "blocked"
    assert row["source_revision"] == 5


async def test_stale_revision_is_skipped(client, session_factory):
    """Fixture 04: `source_revision` cũ hơn bản đang giữ."""
    await _seed_units(client)

    status, body = await _decisions(client, UNITS_URL, fixture("04_stale_update"))

    assert status == 202
    assert body["decisions"]["skip_stale"] == 1
    row = await _mirror(session_factory, "SYNTH-U-0001")
    assert row["status"] == "available", "bản cũ đã ghi đè bản đang giữ"


async def test_record_without_version_is_rejected(client):
    payload = fixture("01_units_incremental", external_batch_id="SYNTH-BATCH-NOVER")
    payload["records"] = [payload["records"][0]]
    del payload["records"][0]["source_revision"]

    status, body = await _decisions(client, UNITS_URL, payload)

    # Chặn ngay ở cổng hợp đồng — schema đòi phải có một trong hai trường phiên bản.
    assert status == 422
    assert body["detail"]["error_code"] == "CONTRACT_VALIDATION_FAILED"


async def test_delete_without_version_is_rejected(client):
    """Lệnh xoá cũng phải mang phiên bản — không thì không phân biệt được xoá mới
    với lệnh xoá cũ đến muộn."""
    payload = fixture("06_explicit_delete", external_batch_id="SYNTH-BATCH-DELNOVER")
    del payload["records"][0]["source_revision"]

    status, body = await _decisions(client, DEALS_URL, payload)

    assert status == 422
    assert body["detail"]["error_code"] == "CONTRACT_VALIDATION_FAILED"


# --- Đụng độ và băm theo trường đã ánh xạ -----------------------------------


async def test_same_version_different_content_is_a_conflict(client, session_factory):
    """Fixture 05: cùng `source_revision`, khác `unit_status`."""
    await _seed_units(client)

    status, body = await _decisions(client, UNITS_URL, fixture("05_same_version_conflict"))

    assert status == 202
    assert body["decisions"]["conflict"] == 1

    row = await _mirror(session_factory, "SYNTH-U-0001")
    assert row["status"] == "available", "đụng độ phải GIỮ NGUYÊN bản đã chấp nhận"

    async with session_factory() as session:
        stored = (
            (
                await session.execute(
                    sa.select(crm_source_records).where(crm_source_records.c.source_record_id == "SYNTH-U-0001")
                )
            )
            .mappings()
            .one()
        )
    assert stored["conflict_count"] == 1
    assert stored["conflict_detected_at"] is not None


async def test_a_pure_conflict_batch_is_completed_with_conflicts_not_failed(client, session_factory):
    """Phase 5.5 P0 (5A): đụng độ KHÔNG tự động biến cả lô thành thất bại.

    Bug cũ: một lô một bản ghi mà bản ghi đó là `conflict` (không có bản ghi hỏng
    nào khác) báo `status='failed'` — dù không mất dữ liệu và không có lỗi hệ
    thống nào. Xem `SyncRunService._terminal_status`.
    """
    await _seed_units(client)

    status_code, body = await _decisions(client, UNITS_URL, fixture("05_same_version_conflict"))

    assert status_code == 202
    assert body["status"] == "completed_with_conflicts"
    assert body["rows_failed"] == 0, "đụng độ không phải bản ghi bị từ chối"

    detail = (await client.get(f"/api/v1/sync-runs/{body['sync_run_id']}")).json()
    assert detail["status"] == "completed_with_conflicts"


async def test_conflict_is_surfaced_as_a_structured_error(client, session_factory):
    await _seed_units(client)
    _, body = await _decisions(client, UNITS_URL, fixture("05_same_version_conflict"))

    response = await client.get(f"/api/v1/sync-runs/{body['sync_run_id']}/errors")
    errors = response.json()["errors"]

    assert any(e["error_code"] == "VERSION_CONFLICT" and e["error_category"] == "conflict" for e in errors)


async def test_unmapped_field_change_is_not_a_conflict(client, session_factory):
    """Cùng phiên bản, chỉ đổi trường KHÔNG được lưu → trùng, không phải đụng độ.

    Trước Phase 4 dấu vân băm cả payload nên trường hợp này sinh đụng độ giả.
    """
    await _seed_units(client)

    payload = fixture("01_units_incremental", external_batch_id="SYNTH-BATCH-EXTRA")
    payload["records"] = [payload["records"][0]]
    # Giữ nguyên revision=1 và mọi trường đã ánh xạ; chỉ thêm dữ liệu ta không lưu.
    payload["records"][0]["payload"]["unit_code"] = "A1-01-01"

    status, body = await _decisions(client, UNITS_URL, payload)

    assert status == 202
    assert body["decisions"]["conflict"] == 0
    assert body["decisions"]["duplicate_noop"] == 1


# --- Tombstone ---------------------------------------------------------------


async def test_explicit_delete_tombstones_the_mirror(client, session_factory):
    """Fixture 06 xoá một giao dịch; xoá là XOÁ MỀM."""
    await _seed_units(client)
    status, _ = await _decisions(client, DEALS_URL, fixture("02_deals_incremental"))
    assert status == 202

    status, body = await _decisions(client, DEALS_URL, fixture("06_explicit_delete"))

    assert status == 202
    assert body["decisions"]["tombstone"] == 1

    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(deals).where(deals.c.external_deal_id == "SYNTH-D-0001"))).mappings().one()
        )
    assert row["deleted_at"] is not None, "phải là xoá mềm"

    async with session_factory() as session:
        still_there = await session.scalar(
            sa.select(sa.func.count()).select_from(deals).where(deals.c.external_deal_id == "SYNTH-D-0001")
        )
    assert still_there == 1, "đồng bộ không được xoá vật lý"


async def test_stale_upsert_cannot_resurrect_a_tombstone(client, session_factory):
    """Bản cũ hơn không được làm sống lại bản ghi đã xoá."""
    await _seed_units(client)
    await _decisions(client, DEALS_URL, fixture("02_deals_incremental"))
    await _decisions(client, DEALS_URL, fixture("06_explicit_delete"))

    payload = fixture("02_deals_incremental", external_batch_id="SYNTH-BATCH-RESURRECT")
    payload["records"] = [payload["records"][0]]
    payload["records"][0]["source_revision"] = 1  # cũ hơn lệnh xoá (revision 9)

    status, body = await _decisions(client, DEALS_URL, payload)

    assert status == 202
    assert body["decisions"]["skip_stale"] == 1

    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(deals).where(deals.c.external_deal_id == "SYNTH-D-0001"))).mappings().one()
        )
    assert row["deleted_at"] is not None, "bản cũ đã làm sống lại tombstone"


# --- Giao dịch trước căn -----------------------------------------------------


async def test_deal_before_unit_is_rejected(client, session_factory):
    """Fixture 09: giao dịch trỏ tới căn chưa tồn tại → từ chối có cấu trúc."""
    status, body = await _decisions(client, DEALS_URL, fixture("09_deal_before_unit"))

    assert status == 202
    assert body["projections"]["rejected"] == 1
    assert body["rows_failed"] == 1

    response = await client.get(f"/api/v1/sync-runs/{body['sync_run_id']}/errors")
    errors = response.json()["errors"]
    assert any(e["error_code"] == "UNKNOWN_UNIT_REFERENCE" for e in errors)

    async with session_factory() as session:
        created = await session.scalar(sa.select(sa.func.count()).select_from(units))
    assert created == 0, "TUYỆT ĐỐI không được tự tạo căn để chứa giao dịch"


async def test_unknown_area_is_rejected_without_creating_one(client, session_factory):
    """Fixture 10: phân khu không tra được → từ chối, không tự tạo."""
    status, body = await _decisions(client, UNITS_URL, fixture("10_unknown_area"))

    assert status == 202
    assert body["projections"]["rejected"] == 1

    async with session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(areas).where(areas.c.project_id == PROJECT_ID)
        )
    assert count == 1, "đã tự tạo phân khu mới — vi phạm quyền sở hữu dữ liệu"


async def test_unknown_status_is_rejected_without_defaulting(client):
    """Fixture 11: trạng thái ngoài bảng ánh xạ → từ chối, KHÔNG mặc định."""
    await _seed_units(client)

    status, body = await _decisions(client, UNITS_URL, fixture("11_unknown_status"))

    assert status == 202
    assert body["projections"]["rejected"] == 1

    response = await client.get(f"/api/v1/sync-runs/{body['sync_run_id']}/errors")
    assert any(e["error_code"] == "UNKNOWN_UNIT_STATUS" for e in response.json()["errors"])


# --- Chạy lại lô hỏng --------------------------------------------------------


async def test_failed_run_can_be_reprocessed_after_the_cause_is_fixed(client, session_factory):
    """Lô giao dịch hỏng vì thiếu căn → gửi căn → chạy lại từ payload đã lưu.

    Đây là điều mà việc giữ payload thô mở ra: không phải xin hệ nguồn gửi lại,
    nên không phụ thuộc vào việc dữ liệu bên đó còn nguyên hay không.
    """
    # 1. Giao dịch tới trước khi có căn → bị từ chối.
    status, failed = await _decisions(client, DEALS_URL, fixture("02_deals_incremental"))
    assert status == 202
    assert failed["rows_failed"] == 2
    run_id = failed["sync_run_id"]

    detail = (await client.get(f"/api/v1/sync-runs/{run_id}")).json()
    assert detail["status"] == "failed"

    # 2. Sửa nguyên nhân: gửi căn.
    await _seed_units(client)

    # 3. Chạy lại — không gửi body nào, dùng payload đã lưu.
    response = await client.post(f"/api/v1/sync-runs/{run_id}/reprocess")
    body = response.json()

    assert response.status_code == 200, body
    assert body["sync_run_id"] == run_id, "chạy lại phải dùng lại đúng lô cũ"
    assert body["decisions"]["insert"] == 2
    assert body["rows_failed"] == 0

    async with session_factory() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(deals))
    assert count == 2


async def test_reprocessing_clears_errors_from_the_failed_attempt(client):
    _, failed = await _decisions(client, DEALS_URL, fixture("02_deals_incremental"))
    run_id = failed["sync_run_id"]

    before = (await client.get(f"/api/v1/sync-runs/{run_id}/errors")).json()
    assert before["total"] > 0

    await _seed_units(client)
    await client.post(f"/api/v1/sync-runs/{run_id}/reprocess")

    after = (await client.get(f"/api/v1/sync-runs/{run_id}/errors")).json()
    assert after["total"] == 0, "lỗi của lần chạy hỏng trước vẫn còn, chồng lên lần mới"


async def test_reprocessing_twice_never_duplicates_data(client, session_factory):
    """Chạy lại lần hai không nhân đôi gì.

    Bảo vệ ở đây có HAI lớp, và test khẳng định cả hai:

    1. Lần chạy lại thành công đưa lô về `completed`, nên lần gọi thứ hai bị chặn
       ngay bằng 409 — không có gì để sửa nữa.
    2. Kể cả nếu lớp trên bị gỡ, idempotency mức bản ghi vẫn khiến các bản ghi đã
       áp dụng thành `duplicate_noop` — xem `test_same_record_in_a_new_batch_is_a_duplicate_noop`.

    Bất biến thật sự cần giữ là SỐ DÒNG, nên đó là thứ được kiểm sau cùng.
    """
    _, failed = await _decisions(client, DEALS_URL, fixture("02_deals_incremental"))
    run_id = failed["sync_run_id"]
    await _seed_units(client)

    first = await client.post(f"/api/v1/sync-runs/{run_id}/reprocess")
    assert first.status_code == 200
    assert first.json()["decisions"]["insert"] == 2

    second = await client.post(f"/api/v1/sync-runs/{run_id}/reprocess")
    assert second.status_code == 409, "lô đã chạy lại thành công thì không còn gì để chạy lại"
    assert second.json()["detail"]["error_code"] == "RUN_NOT_REPROCESSABLE"

    async with session_factory() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(deals))
    assert count == 2, "chạy lại đã nhân đôi giao dịch"


async def test_completed_run_cannot_be_reprocessed(client):
    """Lô đã xong không có gì để sửa; cho chạy lại chỉ mở đường ghi đè ngoài ý muốn."""
    body = await _seed_units(client)

    response = await client.post(f"/api/v1/sync-runs/{body['sync_run_id']}/reprocess")

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "RUN_NOT_REPROCESSABLE"


async def test_reprocessing_an_unknown_run_is_404(client):
    response = await client.post(f"/api/v1/sync-runs/{uuid.uuid4()}/reprocess")
    assert response.status_code == 404


async def test_reprocess_requires_a_key_for_the_runs_own_instance(client, session_factory):
    """Không mượn được danh nghĩa hệ nguồn khác để chạy lại lô của họ."""
    from src.services.sync_credentials import SyncCredentialService

    _, failed = await _decisions(client, DEALS_URL, fixture("02_deals_incremental"))
    run_id = failed["sync_run_id"]

    async with session_factory() as session:
        async with session.begin():
            stranger = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id="synthetic-stranger", label="stranger"
            )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": stranger.api_key}
    ) as other:
        response = await other.post(f"/api/v1/sync-runs/{run_id}/reprocess")

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSTANCE_MISMATCH"


# --- Bảo vệ payload thô (0010) ----------------------------------------------


async def test_a_sync_run_cannot_be_hard_deleted_while_its_payload_exists(client, session_factory):
    """RESTRICT: xoá lô mà chưa xoá payload phải THẤT BẠI.

    Đây là điểm của 0010 — mất lịch sử payload phải là hành động cố ý, không phải
    tác dụng phụ của một câu DELETE gõ vội.

    BẢY khoá ngoại trỏ tới `upload_files`, và câu DELETE này vi phạm nhiều khoá
    cùng lúc. PostgreSQL chỉ báo MỘT trong số đó, và thứ tự trigger quyết định
    khoá nào — không phải thứ để test dựa vào. Nên ở đây các khoá KHÁC được dọn
    trước, để thứ còn chặn lại đúng là thứ 0010 dựng lên. Cách này chặt hơn khẳng
    định ban đầu: nó chứng minh `sync_payloads` chặn được KỂ CẢ khi không còn ai
    khác chặn hộ.
    """
    body = await _seed_units(client)
    run_id = uuid.UUID(body["sync_run_id"])

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.delete(crm_source_records).where(
                    sa.or_(
                        crm_source_records.c.first_sync_run_id == run_id,
                        crm_source_records.c.last_sync_run_id == run_id,
                    )
                )
            )
            await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id == run_id))

    with pytest.raises(sa.exc.IntegrityError) as exc:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(sa.delete(upload_files).where(upload_files.c.id == run_id))

    assert "fk_sync_payloads_sync_run_id" in str(exc.value)


async def test_retention_cleanup_can_delete_payloads_without_touching_runs(client, session_factory):
    """Chính sách lưu giữ: xoá payload theo tuổi, giữ nguyên metadata lô."""
    body = await _seed_units(client)
    run_id = uuid.UUID(body["sync_run_id"])

    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id == run_id))

    async with session_factory() as session:
        run_still_there = await session.scalar(
            sa.select(sa.func.count()).select_from(upload_files).where(upload_files.c.id == run_id)
        )
    assert run_still_there == 1, "dọn payload không được xoá lịch sử lô"


async def test_a_run_without_a_retained_payload_cannot_be_reprocessed(client, session_factory):
    """Sau khi payload bị dọn theo chính sách lưu giữ, chạy lại phải báo rõ ràng."""
    _, failed = await _decisions(client, DEALS_URL, fixture("02_deals_incremental"))
    run_id = failed["sync_run_id"]

    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id == uuid.UUID(run_id)))

    response = await client.post(f"/api/v1/sync-runs/{run_id}/reprocess")

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "PAYLOAD_NOT_RETAINED"
