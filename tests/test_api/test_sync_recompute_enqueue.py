"""Khi nào một lô đồng bộ xếp hàng tính lại — và khi nào KHÔNG.

Xếp hàng thừa không vô hại: mỗi job là một lần xoá-rồi-ghi cả lineage của phạm vi
đó, làm `computation_id` đổi vô cớ và khiến việc truy vết "lần tính nào sinh ra
dòng này" mất ý nghĩa. Nên điều kiện kích hoạt được kiểm cả hai chiều: đổi thì
PHẢI xếp, không đổi thì TUYỆT ĐỐI không.

Hàng đợi được thay bằng một đối tượng giả để khẳng định tất định — dùng Redis
thật ở đây sẽ biến các test này thành phụ thuộc thời gian mà không kiểm thêm được
gì (worker thật đã có test riêng ở `tests/test_jobs/test_recompute_domain_worker.py`).
"""

from __future__ import annotations

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

PROJECT_ID = uuid.UUID("d4e5f607-1829-4a3b-8c4d-56789abcdf90")
AREA_A = uuid.UUID("d4e5f607-1829-4a3b-8c4d-56789abcdf91")
AREA_B = uuid.UUID("d4e5f607-1829-4a3b-8c4d-56789abcdf92")
INSTANCE = "synthetic-enqueue-crm"
UNITS_URL = "/api/v1/sync/units"


class FakeJob:
    id = "fake-job-id"


class FakeQueue:
    """Hàng đợi giả: ghi lại lời gọi, hoặc nổ khi được yêu cầu."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def enqueue(self, func_name, **kwargs):
        if self.fail:
            raise ConnectionError("Redis không với tới được")
        self.calls.append({"func": func_name, **kwargs})
        return FakeJob()


@pytest.fixture
def queue(monkeypatch):
    fake = FakeQueue()
    monkeypatch.setattr("src.services.sync_runs.get_queue", lambda name: fake)
    return fake


@pytest.fixture
def failing_queue(monkeypatch):
    fake = FakeQueue(fail=True)
    monkeypatch.setattr("src.services.sync_runs.get_queue", lambda name: fake)
    return fake


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
        await session.execute(sa.delete(deals).where(deals.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(units).where(units.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id == INSTANCE))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'ENQ', :d, :t)"),
                {"p": PROJECT_ID, "d": date(2026, 1, 1), "t": datetime.now(UTC)},
            )
            for area_id, name in ((AREA_A, "A1"), (AREA_B, "A2")):
                await session.execute(
                    sa.insert(areas).values(
                        id=area_id,
                        project_id=PROJECT_ID,
                        area_name=name,
                        unit_type="2PN",
                        bedrooms=2,
                        area_sqm=75,
                        total_units=50,
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
async def client(session_factory):
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            issued = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=INSTANCE, label="enqueue test"
            )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": issued.api_key}
    ) as authorized:
        yield authorized


def _unit(external_id, *, code, area="A1", status="available", revision=1):
    return {
        "entity": "unit",
        "operation": "upsert",
        "external_id": external_id,
        "source_revision": revision,
        "payload": {
            "area_ref": {"area_name": area, "unit_type": "2PN"},
            "unit_code": code,
            "unit_status": status,
        },
    }


def _envelope(records, *, batch):
    return {
        "_comment": "FIXTURE TỔNG HỢP — KHÔNG PHẢI DỮ LIỆU CRM THẬT.",
        "schema_version": 1,
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "external_batch_id": batch,
        "sync_mode": "incremental",
        "project_ref": {"project_id": str(PROJECT_ID)},
        "source_extracted_at": "2026-08-09T02:00:00+07:00",
        "records": records,
    }


async def _sync(client, records, *, batch):
    return await client.post(UNITS_URL, json=_envelope(records, batch=batch))


# --- Kích hoạt ---------------------------------------------------------------


async def test_insert_enqueues_one_recompute(client, queue):
    response = await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-1")

    assert response.status_code == 202
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["func"] == "src.jobs.recompute_domain.run_domain_recompute"
    assert call["project_id"] == str(PROJECT_ID)
    assert call["area_ids"] == [str(AREA_A)]
    assert call["sync_run_id"] == response.json()["sync_run_id"]


async def test_update_enqueues(client, queue):
    await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-1")
    queue.calls.clear()

    await _sync(client, [_unit("SYNTH-E-1", code="A1-1", status="sold", revision=2)], batch="ENQ-2")

    assert len(queue.calls) == 1


async def test_duplicate_noop_enqueues_nothing(client, queue):
    """Cùng phiên bản, cùng nội dung → bản sao không đổi → không có gì để tính lại."""
    await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-1")
    queue.calls.clear()

    body = (await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-DUP")).json()

    assert body["decisions"]["duplicate_noop"] == 1
    assert queue.calls == []


async def test_stale_skip_enqueues_nothing(client, queue):
    await _sync(client, [_unit("SYNTH-E-1", code="A1-1", revision=5)], batch="ENQ-1")
    queue.calls.clear()

    body = (await _sync(client, [_unit("SYNTH-E-1", code="A1-1", revision=1)], batch="ENQ-STALE")).json()

    assert body["decisions"]["skip_stale"] == 1
    assert queue.calls == []


async def test_conflict_enqueues_nothing(client, queue):
    """Đụng độ giữ nguyên trạng thái đã chấp nhận, nên bản sao không đổi."""
    await _sync(client, [_unit("SYNTH-E-1", code="A1-1", revision=1)], batch="ENQ-1")
    queue.calls.clear()

    body = (await _sync(client, [_unit("SYNTH-E-1", code="A1-CHANGED", revision=1)], batch="ENQ-CONF")).json()

    assert body["decisions"]["conflict"] == 1
    assert queue.calls == []


async def test_rejected_only_batch_enqueues_nothing(client, queue):
    body = (await _sync(client, [_unit("SYNTH-E-X", code="X-1", area="KHONG-CO")], batch="ENQ-REJ")).json()

    assert body["projections"]["rejected"] == 1
    assert body["projections"]["inserted"] == 0
    assert queue.calls == []


async def test_replayed_batch_enqueues_nothing(client, queue):
    """Chạy lại lô cũ trả kết quả cũ mà không xử lý lại — không đổi gì."""
    await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-REPLAY")
    queue.calls.clear()

    body = (await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-REPLAY")).json()

    assert body["replayed"] is True
    assert queue.calls == []


# --- Phạm vi phân khu --------------------------------------------------------


async def test_only_affected_areas_are_enqueued(client, queue):
    """Lô chạm A1 không được yêu cầu tính lại A2."""
    await _sync(client, [_unit("SYNTH-E-B", code="A2-1", area="A2")], batch="ENQ-SEED-B")
    queue.calls.clear()

    await _sync(client, [_unit("SYNTH-E-A", code="A1-1", area="A1")], batch="ENQ-A")

    assert queue.calls[0]["area_ids"] == [str(AREA_A)]


async def test_a_batch_touching_two_areas_enqueues_both(client, queue):
    await _sync(
        client,
        [_unit("SYNTH-E-A", code="A1-1", area="A1"), _unit("SYNTH-E-B", code="A2-1", area="A2")],
        batch="ENQ-BOTH",
    )

    assert sorted(queue.calls[0]["area_ids"]) == sorted([str(AREA_A), str(AREA_B)])


async def test_tombstone_still_reports_its_area(client, queue, session_factory):
    """Căn bị xoá mềm vẫn phải nằm trong phạm vi tính lại.

    `area_id` được thu thập TRƯỚC khi lọc bỏ bản ghi tombstone. Thu thập sau thì
    một phân khu vừa mất hết căn sẽ không bao giờ được tính lại nữa.
    """
    await _sync(client, [_unit("SYNTH-E-1", code="A1-1", area="A1")], batch="ENQ-1")
    queue.calls.clear()

    delete_record = {
        "entity": "unit",
        "operation": "delete",
        "external_id": "SYNTH-E-1",
        "source_revision": 9,
    }
    body = (await _sync(client, [delete_record], batch="ENQ-DEL")).json()

    assert body["decisions"]["tombstone"] == 1
    assert len(queue.calls) == 1
    assert queue.calls[0]["area_ids"] == [str(AREA_A)], "phân khu của căn vừa tombstone bị bỏ sót"


# --- Hỏng khi xếp hàng -------------------------------------------------------


async def test_enqueue_failure_leaves_the_sync_committed(client, failing_queue, session_factory):
    """Redis hỏng KHÔNG được làm hỏng một lô đã commit.

    Báo lỗi cho hệ nguồn sẽ nói sai rằng dữ liệu bị từ chối, và mời họ gửi lại
    một lô đã áp dụng xong.
    """
    response = await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-FAIL")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["projections"]["inserted"] == 1

    # Dữ liệu nghiệp vụ đã commit thật.
    async with session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(units).where(units.c.source_instance_id == INSTANCE)
        )
    assert count == 1

    detail = (await client.get(f"/api/v1/sync-runs/{body['sync_run_id']}")).json()
    assert detail["status"] == "completed"


async def test_enqueue_failure_logs_all_three_identifiers(client, failing_queue, caplog):
    """Log phải đủ để xếp lại hàng bằng tay: sync_run_id, project_id, area_ids."""
    import logging

    with caplog.at_level(logging.ERROR):
        response = await _sync(client, [_unit("SYNTH-E-1", code="A1-1")], batch="ENQ-FAIL-LOG")

    run_id = response.json()["sync_run_id"]
    logged = " ".join(record.getMessage() for record in caplog.records)

    assert "domain.recompute.enqueue_failed" in logged
    assert run_id in logged
    assert str(PROJECT_ID) in logged
    assert str(AREA_A) in logged
