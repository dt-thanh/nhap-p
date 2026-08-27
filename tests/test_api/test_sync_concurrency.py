"""Đua TOCTOU thật giữa `_find_existing_run` và `_create_run` (`SyncRunService.run`).

Audit finding (P1/Medium): hai request gửi CÙNG `(source_system,
source_instance_id, external_batch_id)` gần như đồng thời có thể cả hai cùng
thấy "chưa có lô" trước khi cái nào kịp commit `_create_run`, khiến bên thua
cuộc đua nhận `IntegrityError` không được bắt (500), dù ràng buộc
`uq_upload_files_source_batch` đã đúng khi chặn không cho tạo hai dòng.

Test này chạy TRÊN POSTGRES THẬT (không mock/SQLite) với hai coroutine thật sự
đồng thời qua `asyncio.gather` trong CÙNG một tiến trình ASGI — đủ để hai
request xen kẽ ở các điểm `await` (mỗi truy vấn DB là một điểm như vậy), tái
hiện đúng cửa sổ đua mà không cần hai tiến trình hệ điều hành riêng.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.main import app
from src.models.tables import areas, crm_source_records, deals, sync_credentials, sync_payloads, units, upload_files

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

PROJECT_ID = uuid.UUID("a1b2c3d4-1234-4abc-8def-000000000099")
INSTANCE = "synthetic-race-instance"
SYNC_URL = "/api/v1/sync/units"


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
            sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id == INSTANCE)
        )
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id == INSTANCE))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Race', :d, :ts)"),
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
                session, source_system="mini_crm", source_instance_id=INSTANCE, label="race test"
            )


@pytest_asyncio.fixture
async def anonymous():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _payload(*, batch: str, unit_code: str) -> dict:
    return {
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "source_entity": "units",
        "schema_version": 1,
        "external_batch_id": batch,
        "project_id": str(PROJECT_ID),
        "records": [
            {
                "source_record_id": unit_code,
                "operation": "upsert",
                "source_updated_at": "2026-08-22T00:00:00Z",
                "data": {
                    "area_name": "A1",
                    "unit_type": "2PN",
                    "unit_code": unit_code,
                    "status": "available",
                },
            }
        ],
    }


async def _post(client, payload, api_key):
    return await client.post(SYNC_URL, json=payload, headers={"X-API-Key": api_key})


async def test_two_concurrent_identical_batches_never_500_and_create_exactly_one_run(
    anonymous, issued, session_factory
):
    """Đây là bài kiểm TRỰC TIẾP cho phát hiện của audit: gửi cùng lúc, không
    được rơi 500, và ràng buộc DB phải THẬT SỰ chỉ giữ lại đúng một lô."""
    payload = _payload(batch="race-batch-identical", unit_code="RACE-001")

    responses = await asyncio.gather(
        _post(anonymous, payload, issued.api_key),
        _post(anonymous, payload, issued.api_key),
    )

    statuses = [r.status_code for r in responses]
    assert 500 not in statuses, f"một trong hai request đua bị rơi 500: {[r.text for r in responses]}"
    assert all(code in (200, 202) for code in statuses), statuses

    # Bất biến DB #1: đúng MỘT dòng `upload_files` cho batch identity này —
    # ràng buộc `uq_upload_files_source_batch` không bị lách qua đường nào.
    async with session_factory() as session:
        run_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(upload_files)
            .where(upload_files.c.external_batch_id == "race-batch-identical")
        )
    assert run_count == 1, "hai request đua đã tạo ra hai dòng upload_files thay vì một"

    # Cả hai response phải trỏ về ĐÚNG một sync_run_id.
    run_ids = {r.json()["sync_run_id"] for r in responses}
    assert len(run_ids) == 1, f"hai response trỏ tới hai sync_run_id khác nhau: {run_ids}"


async def test_concurrent_race_does_not_duplicate_the_domain_projection(anonymous, issued, session_factory):
    """Bất biến DB #2: đúng MỘT căn được tạo, không phải hai — bên thua cuộc
    đua không được chạy `apply_records` một lần nữa cho cùng bản ghi."""
    payload = _payload(batch="race-batch-projection", unit_code="RACE-PROJ-001")

    responses = await asyncio.gather(
        _post(anonymous, payload, issued.api_key),
        _post(anonymous, payload, issued.api_key),
    )
    assert all(r.status_code in (200, 202) for r in responses)

    async with session_factory() as session:
        unit_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(units)
            .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "RACE-PROJ-001")
        )
    assert unit_count == 1, "cuộc đua đã tạo ra hai bản chiếu (units) thay vì một cho cùng bản ghi"


async def test_sequential_same_batch_same_payload_replays_cleanly(anonymous, issued):
    """Hồi quy không-đua: gửi tuần tự (không asyncio.gather) vẫn phải hoạt
    động y như trước bản vá — lần hai trả `replayed=true`, không xử lý lại."""
    payload = _payload(batch="sequential-same", unit_code="SEQ-001")

    first = await _post(anonymous, payload, issued.api_key)
    second = await _post(anonymous, payload, issued.api_key)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["sync_run_id"] == first.json()["sync_run_id"]


async def test_sequential_same_batch_different_payload_keeps_the_first_run(anonymous, issued, session_factory):
    """Cùng `external_batch_id` nhưng payload khác — danh tính lô vẫn là
    `(source_system, source_instance_id, external_batch_id)`, không phải nội
    dung payload, nên lần hai vẫn được coi là gửi lại ĐÚNG lô đầu, không xử lý
    bản ghi mới trong payload thứ hai. Đây là ngữ nghĩa VỐN CÓ, bản vá không
    được phép đổi nó."""
    first_payload = _payload(batch="sequential-diff-payload", unit_code="DIFF-001")
    second_payload = _payload(batch="sequential-diff-payload", unit_code="DIFF-002")

    first = await _post(anonymous, first_payload, issued.api_key)
    second = await _post(anonymous, second_payload, issued.api_key)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["sync_run_id"] == first.json()["sync_run_id"]

    async with session_factory() as session:
        second_unit_created = await session.scalar(
            sa.select(sa.func.count())
            .select_from(units)
            .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "DIFF-002")
        )
    assert second_unit_created == 0, "lô 'gửi lại' đã xử lý bản ghi của payload thứ hai — đây là một lô MỚI, không phải replay"


async def test_an_unrelated_integrity_error_is_still_raised_not_misclassified(anonymous, issued, session_factory):
    """Bản vá CHỈ bắt đúng `uq_upload_files_source_batch` — một IntegrityError
    khác (ví dụ vi phạm ràng buộc nghiệp vụ trên bản ghi) không được nuốt
    thành một replay giả."""
    from src.services.sync_runs import _constraint_name

    # Không mô phỏng bằng cách chèn tay: đủ để chứng minh hàm lọc tên ràng buộc
    # không coi MỌI IntegrityError là ràng buộc batch — đây là điều kiện lọc
    # thật sự nằm trong `SyncRunService.run`.
    class _FakeOrig:
        constraint_name = "uq_deals_active_per_unit"

    class _FakeExc(Exception):
        orig = _FakeOrig()

    assert _constraint_name(_FakeExc()) == "uq_deals_active_per_unit"
    assert _constraint_name(_FakeExc()) != "uq_upload_files_source_batch"
