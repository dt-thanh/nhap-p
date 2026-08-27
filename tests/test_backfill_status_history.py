"""scripts/backfill_status_history.py — phát lại sync_payloads thành nhật ký.

Chạy trên DB thật (skip nếu không có TEST_DATABASE_URL/DATABASE_URL), scoped
theo project_id riêng của module này để không đụng dữ liệu module khác.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, deal_status_history, deals, projects, sync_payloads, unit_status_history, units, upload_files
from scripts.backfill_status_history import replay_source_instance

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DATABASE_URL, reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    ),
]

INSTANCE = "backfill-test-instance"
PROJECT_ID = uuid.UUID("11111111-2222-3333-4444-555566667777")


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async with session_factory() as session:
        async with session.begin():
            area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
            run_ids = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
            # unit_status_history/deal_status_history KHÔNG được DELETE trực
            # tiếp — trigger append-only (0028/0029) sẽ từ chối. Xoá units/deals
            # CASCADE dọn theo lịch sử của chúng (0028/0029: ondelete="CASCADE").
            await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id).where(units.c.area_id.in_(area_ids)))))
            await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
            await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(run_ids)))
            await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
            await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
            await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})
            await session.execute(
                sa.insert(projects).values(id=PROJECT_ID, name="Backfill", launch_date=date(2026, 1, 1), created_at=datetime.now(UTC))
            )
    yield


async def _seed_area_and_unit(session, *, external_unit_id="U-1", status="available") -> tuple[uuid.UUID, uuid.UUID]:
    area_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    now = datetime.now(UTC)
    await session.execute(
        sa.insert(areas).values(
            id=area_id, project_id=PROJECT_ID, area_name="A1", unit_type="2PN", bedrooms=2, area_sqm=75,
            total_units=100, created_at=now,
        )
    )
    await session.execute(
        sa.insert(units).values(
            id=unit_id, source_system="mini_crm", source_instance_id=INSTANCE, external_unit_id=external_unit_id,
            area_id=area_id, unit_code="A1-01", unit_type="2PN", status=status, created_at=now, updated_at=now,
        )
    )
    return area_id, unit_id


async def _seed_deal(session, unit_id, *, external_deal_id="D-1", status="lead") -> uuid.UUID:
    deal_id = uuid.uuid4()
    now = datetime.now(UTC)
    stamps = {"reserved": "reserved_at", "sold": "sold_at", "lost": "lost_at"}
    values = {
        "id": deal_id, "source_system": "mini_crm", "source_instance_id": INSTANCE,
        "external_deal_id": external_deal_id, "unit_id": unit_id, "status": status, "source_status": status,
        "created_at": now, "updated_at": now,
    }
    if status in stamps:
        values[stamps[status]] = now
    await session.execute(sa.insert(deals).values(**values))
    return deal_id


async def _store_payload(session, *, received_at: datetime, records: list[dict]) -> uuid.UUID:
    run_id = uuid.uuid4()
    now = datetime.now(UTC)
    await session.execute(
        sa.insert(upload_files).values(
            id=run_id, project_id=PROJECT_ID, source_system="mini_crm", source_instance_id=INSTANCE,
            external_batch_id=f"batch-{uuid.uuid4().hex[:8]}", status="completed", rows_ok=len(records),
            rows_failed=0, uploaded_at=now, source_entity="units", input_format="json",
            transport_mode="api_push", sync_mode="incremental", schema_version=1, rows_received=len(records),
            error_summary={},
        )
    )
    payload = {
        "schema_version": 1, "source_system": "mini_crm", "source_instance_id": INSTANCE,
        "external_batch_id": f"batch-{uuid.uuid4().hex[:8]}", "sync_mode": "incremental",
        "project_ref": {}, "source_extracted_at": received_at.isoformat(), "records": records,
    }
    await session.execute(
        sa.insert(sync_payloads).values(
            id=uuid.uuid4(), sync_run_id=run_id, payload=payload, payload_sha256="0" * 64,
            payload_bytes=100, record_count=len(records), received_at=received_at,
        )
    )
    return run_id


def _unit_record(external_id: str, status: str, *, source_updated_at: datetime | None = None, completeness: str = "full") -> dict:
    record = {
        "entity": "units", "external_id": external_id, "operation": "upsert",
        "payload": {"area_ref": {"external_area_id": "AR-1"}, "unit_code": "A1-01", "unit_status": status},
        "payload_completeness": completeness,
    }
    if source_updated_at is not None:
        record["source_updated_at"] = source_updated_at.isoformat()
    return record


def _unit_record_partial_no_status(external_id: str) -> dict:
    return {
        "entity": "units", "external_id": external_id, "operation": "upsert",
        "payload": {"unit_code": "A1-01"}, "payload_completeness": "partial",
    }


def _deal_record(external_id: str, status: str, external_unit_id: str, *, source_updated_at: datetime | None = None) -> dict:
    record = {
        "entity": "deals", "external_id": external_id, "operation": "upsert",
        "payload": {"deal_status": status, "external_unit_id": external_unit_id},
        "payload_completeness": "full",
    }
    if source_updated_at is not None:
        record["source_updated_at"] = source_updated_at.isoformat()
    return record


async def test_backfill_skips_partial_payloads_missing_status(session_factory):
    async with session_factory() as session:
        async with session.begin():
            _, unit_id = await _seed_area_and_unit(session)
            await _store_payload(session, received_at=datetime(2026, 1, 1, tzinfo=UTC), records=[_unit_record_partial_no_status("U-1")])

        async with session.begin():
            stats = await replay_source_instance(session, INSTANCE, dry_run=False, batch_size=100)

    assert stats.unit_events_inserted == 0
    assert stats.skipped_partial == 1


async def test_backfill_handles_incremental_collapse(session_factory):
    """Hai lượt 'available'->'reserved' rồi 'reserved'->'sold' phát ra 2 sự kiện
    (đúng số chuyển trạng thái QUAN SÁT ĐƯỢC qua các lượt payload), không phải
    một chuỗi đầy đủ hơn nếu có trạng thái trung gian nào bị bỏ lỡ giữa hai
    payload liên tiếp — đây chính là giới hạn 'gấp khúc' cần gắn nhãn lower-bound
    ở tầng đọc, không phải lỗi của bộ phát lại."""
    async with session_factory() as session:
        async with session.begin():
            _, unit_id = await _seed_area_and_unit(session)
            await _store_payload(session, received_at=datetime(2026, 1, 1, tzinfo=UTC), records=[_unit_record("U-1", "available")])
            await _store_payload(session, received_at=datetime(2026, 1, 5, tzinfo=UTC), records=[_unit_record("U-1", "reserved")])
            await _store_payload(session, received_at=datetime(2026, 1, 10, tzinfo=UTC), records=[_unit_record("U-1", "sold")])

        async with session.begin():
            stats = await replay_source_instance(session, INSTANCE, dry_run=False, batch_size=100)

        assert stats.unit_events_inserted == 3  # birth(available) -> reserved -> sold

        rows = (
            await session.execute(
                sa.select(unit_status_history.c.old_status, unit_status_history.c.new_status)
                .where(unit_status_history.c.unit_id == unit_id, unit_status_history.c.source == "backfill_replay")
                .order_by(unit_status_history.c.changed_at)
            )
        ).all()
        assert rows == [(None, "available"), ("available", "reserved"), ("reserved", "sold")]


async def test_backfill_idempotent_on_rerun(session_factory):
    async with session_factory() as session:
        async with session.begin():
            _, unit_id = await _seed_area_and_unit(session)
            await _store_payload(session, received_at=datetime(2026, 1, 1, tzinfo=UTC), records=[_unit_record("U-1", "available")])
            await _store_payload(session, received_at=datetime(2026, 1, 5, tzinfo=UTC), records=[_unit_record("U-1", "reserved")])

        async with session.begin():
            first = await replay_source_instance(session, INSTANCE, dry_run=False, batch_size=100)
        async with session.begin():
            second = await replay_source_instance(session, INSTANCE, dry_run=False, batch_size=100)

        assert first.unit_events_inserted == 2
        assert second.unit_events_inserted == 0  # tất cả đã có, ON CONFLICT DO NOTHING chặn hết

        count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(unit_status_history).where(unit_status_history.c.unit_id == unit_id, unit_status_history.c.source == "backfill_replay")
            )
        ).scalar()
        assert count == 2


async def test_backfill_dry_run_writes_nothing(session_factory):
    async with session_factory() as session:
        async with session.begin():
            _, unit_id = await _seed_area_and_unit(session)
            await _store_payload(session, received_at=datetime(2026, 1, 1, tzinfo=UTC), records=[_unit_record("U-1", "available")])

        async with session.begin():
            stats = await replay_source_instance(session, INSTANCE, dry_run=True, batch_size=100)

        assert stats.unit_events_inserted == 1  # đếm được, nhưng không ghi
        count = (
            await session.execute(
                sa.select(sa.func.count()).select_from(unit_status_history).where(unit_status_history.c.unit_id == unit_id, unit_status_history.c.source == "backfill_replay")
            )
        ).scalar()
        assert count == 0


async def test_backfill_resolves_deal_and_flags_cancellation(session_factory):
    async with session_factory() as session:
        async with session.begin():
            _, unit_id = await _seed_area_and_unit(session, status="reserved")
            deal_id = await _seed_deal(session, unit_id, status="reserved")
            await _store_payload(session, received_at=datetime(2026, 1, 1, tzinfo=UTC), records=[_deal_record("D-1", "reserved", "U-1")])
            await _store_payload(session, received_at=datetime(2026, 1, 10, tzinfo=UTC), records=[_deal_record("D-1", "cancelled", "U-1")])

        async with session.begin():
            stats = await replay_source_instance(session, INSTANCE, dry_run=False, batch_size=100)

        assert stats.deal_events_inserted == 2
        rows = (
            await session.execute(
                sa.select(deal_status_history.c.new_status, deal_status_history.c.prior_status_was_holding)
                .where(deal_status_history.c.deal_id == deal_id, deal_status_history.c.source == "backfill_replay")
                .order_by(deal_status_history.c.changed_at)
            )
        ).all()
        assert rows[0] == ("reserved", False)
        assert rows[1] == ("lost", True)  # 'cancelled' -> alias 'lost'; was holding -> cancellation thật


async def test_backfill_skips_records_with_unresolved_identity(session_factory):
    """external_unit_id không khớp unit nào hiện có — bị bỏ qua, có đếm riêng."""
    async with session_factory() as session:
        async with session.begin():
            await _seed_area_and_unit(session, external_unit_id="U-1")
            await _store_payload(session, received_at=datetime(2026, 1, 1, tzinfo=UTC), records=[_unit_record("U-DOES-NOT-EXIST", "reserved")])

        async with session.begin():
            stats = await replay_source_instance(session, INSTANCE, dry_run=False, batch_size=100)

    assert stats.unit_events_inserted == 0
    assert stats.skipped_unresolved_identity == 1


async def test_backfill_skips_delete_operation_records(session_factory):
    async with session_factory() as session:
        async with session.begin():
            _, unit_id = await _seed_area_and_unit(session)
            record = {"entity": "units", "external_id": "U-1", "operation": "delete", "payload": {}, "payload_completeness": "full"}
            await _store_payload(session, received_at=datetime(2026, 1, 1, tzinfo=UTC), records=[record])

        async with session.begin():
            stats = await replay_source_instance(session, INSTANCE, dry_run=False, batch_size=100)

    assert stats.unit_events_inserted == 0
    assert stats.skipped_delete_operation == 1
