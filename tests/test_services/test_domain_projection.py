"""Chiếu bản ghi nguồn vào `units` / `deals`, và bộ tính hấp thụ mới.

Chạy qua ĐÚNG đường thật: `JsonPayloadParser` → `SyncRunService` →
`SourceIdentityService` → `DomainProjector`. Không gọi tắt vào tầng chiếu, vì
điều đáng kiểm nhất là hai tầng ăn khớp với nhau.

Chạy: TEST_TARGET=tests/test_services/test_domain_projection.py bash scripts/test_db.sh
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

from src.models.tables import (
    absorption_daily,
    areas,
    crm_source_records,
    deals,
    inventory_snapshots,
    sales_records,
    units,
    upload_errors,
    upload_files,
)
from src.services.domain_absorption import (
    DomainAbsorptionCalculatorService,
    ParallelRunComparator,
)
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

# UUID riêng của module — xem ghi chú ở test_source_identity.py.
PROJECT_ID = uuid.UUID("d4e5f6a7-b8c9-4012-a345-6789abcdef01")
INSTANCE = "crm-project-a"

T1 = "2026-08-01T00:00:00Z"
T2 = "2026-08-02T00:00:00Z"
SOLD_AT = "2026-08-05T10:00:00Z"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async def wipe(session):
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id))))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(crm_source_records))
        await session.execute(sa.delete(absorption_daily).where(absorption_daily.c.area_id.in_(area_ids)))
        # Test đối chiếu có dựng dữ liệu tổng hợp cũ; không dọn thì DELETE areas nổ khoá ngoại.
        await session.execute(sa.delete(sales_records).where(sales_records.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(inventory_snapshots).where(inventory_snapshots.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'S3', :d, :ts)"),
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

_BATCH = {"n": 0}


def _next_batch() -> str:
    _BATCH["n"] += 1
    return f"b-{uuid.uuid4().hex[:8]}-{_BATCH['n']}"


async def _sync(session_factory, entity, records, *, batch=None):
    payload = {
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "source_entity": entity,
        "schema_version": 1,
        "external_batch_id": batch or _next_batch(),
        "project_id": str(PROJECT_ID),
        "records": records,
    }
    envelope = JsonPayloadParser().parse(payload)
    return await SyncRunService(session_factory).run(envelope)


def _unit(record_id="U-1", *, code="A1-01", status="available", revision=1, area="A1", unit_type="2PN"):
    return {
        "source_record_id": record_id,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"area_name": area, "unit_type": unit_type, "unit_code": code, "status": status},
    }


def _deal(record_id="D-1", *, unit="U-1", status="reserved", revision=1, **stamps):
    data = {"external_unit_id": unit, "status": status}
    if status == "reserved":
        data.setdefault("reserved_at", T1)
    elif status == "sold":
        data.setdefault("reserved_at", T1)
        data.setdefault("sold_at", SOLD_AT)
    elif status in ("lost", "cancelled", "canceled"):
        data.setdefault("lost_at", T2)
    data.update(stamps)
    return {"source_record_id": record_id, "operation": "upsert", "source_revision": revision, "data": data}


def _tombstone(record_id, *, revision=99):
    return {"source_record_id": record_id, "operation": "delete", "source_revision": revision}


async def _rows(session_factory, table) -> list[dict]:
    async with session_factory() as session:
        result = await session.execute(sa.select(table))
        return [dict(r) for r in result.mappings().all()]


async def _absorption_rows(session_factory) -> list[dict]:
    """Chỉ dòng hấp thụ của DỰ ÁN NÀY — `absorption_daily` dùng chung với module khác."""
    async with session_factory() as session:
        result = await session.execute(
            sa.select(absorption_daily).where(
                absorption_daily.c.area_id.in_(sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID))
            )
        )
        return [dict(r) for r in result.mappings().all()]


async def _errors(session_factory, sync_run_id) -> list[dict]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(upload_errors).where(upload_errors.c.file_id == uuid.UUID(sync_run_id))
        )
        return [dict(r) for r in result.mappings().all()]


async def _seed_unit(session_factory, record_id="U-1", **kwargs):
    return await _sync(session_factory, "units", [_unit(record_id, **kwargs)])


# --- Chiếu units ------------------------------------------------------------


async def test_new_unit_is_inserted(session_factory):
    result = await _seed_unit(session_factory)

    assert result.status == "completed"
    assert result.decisions["insert"] == 1
    assert result.projections["inserted"] == 1

    rows = await _rows(session_factory, units)
    assert len(rows) == 1
    assert rows[0]["external_unit_id"] == "U-1"
    assert rows[0]["source_instance_id"] == INSTANCE
    assert rows[0]["unit_code"] == "A1-01"
    assert rows[0]["status"] == "available"
    assert rows[0]["deleted_at"] is None


async def test_new_deal_is_inserted(session_factory):
    await _seed_unit(session_factory)
    result = await _sync(session_factory, "deals", [_deal()])

    assert result.projections["inserted"] == 1
    rows = await _rows(session_factory, deals)
    assert len(rows) == 1
    assert rows[0]["external_deal_id"] == "D-1"
    assert rows[0]["status"] == "reserved"
    assert rows[0]["source_status"] == "reserved"
    assert rows[0]["reserved_at"] is not None


async def test_replaying_the_same_record_touches_nothing(session_factory):
    await _seed_unit(session_factory)
    before = await _rows(session_factory, units)

    result = await _sync(session_factory, "units", [_unit()])
    after = await _rows(session_factory, units)

    assert result.decisions["duplicate_noop"] == 1
    assert result.projections["untouched"] == 1
    assert result.projections["updated"] == 0
    assert after == before, "nạp lại y hệt đã chạm vào bảng nghiệp vụ"


async def test_newer_version_updates_the_mirror(session_factory):
    await _seed_unit(session_factory, revision=1, status="available")
    result = await _sync(session_factory, "units", [_unit(revision=2, status="reserved")])

    rows = await _rows(session_factory, units)
    assert result.projections["updated"] == 1
    assert len(rows) == 1, "cập nhật không được tạo dòng thứ hai"
    assert rows[0]["status"] == "reserved"
    assert rows[0]["source_revision"] == 2


async def test_older_version_is_ignored(session_factory):
    await _seed_unit(session_factory, revision=5, status="sold")
    result = await _sync(session_factory, "units", [_unit(revision=4, status="available")])

    rows = await _rows(session_factory, units)
    assert result.decisions["skip_stale"] == 1
    assert result.projections["untouched"] == 1
    assert rows[0]["status"] == "sold", "bản cũ đã ghi đè bản mới"
    assert rows[0]["source_revision"] == 5


async def test_same_version_different_payload_stays_a_conflict(session_factory):
    await _seed_unit(session_factory, revision=3, code="A1-01")
    result = await _sync(session_factory, "units", [_unit(revision=3, code="A1-99")])

    rows = await _rows(session_factory, units)
    assert result.decisions["conflict"] == 1
    assert result.projections["untouched"] == 1
    assert rows[0]["unit_code"] == "A1-01", "đụng độ đã ghi đè trạng thái đã chấp nhận"

    errors = await _errors(session_factory, result.sync_run_id)
    assert [e["error_code"] for e in errors] == ["VERSION_CONFLICT"]


# --- Tombstone --------------------------------------------------------------


async def test_tombstone_marks_the_unit_deleted(session_factory):
    await _seed_unit(session_factory, revision=1)
    result = await _sync(session_factory, "units", [_tombstone("U-1", revision=5)])

    rows = await _rows(session_factory, units)
    assert result.decisions["tombstone"] == 1
    assert result.projections["tombstoned"] == 1
    assert len(rows) == 1, "tombstone là xoá MỀM, dòng phải còn"
    assert rows[0]["deleted_at"] is not None


async def test_older_upsert_cannot_resurrect_a_tombstoned_unit(session_factory):
    await _seed_unit(session_factory, revision=1)
    await _sync(session_factory, "units", [_tombstone("U-1", revision=5)])

    result = await _sync(session_factory, "units", [_unit(revision=4, status="available")])
    rows = await _rows(session_factory, units)

    assert result.decisions["skip_stale"] == 1
    assert rows[0]["deleted_at"] is not None, "bản cũ đã làm sống lại căn đã xoá"


async def test_newer_upsert_resurrects_a_tombstoned_unit(session_factory):
    """Hệ nguồn xoá rồi tạo lại là hợp lệ — CRM là nguồn sự thật."""
    await _seed_unit(session_factory, revision=1)
    await _sync(session_factory, "units", [_tombstone("U-1", revision=5)])

    result = await _sync(session_factory, "units", [_unit(revision=6, status="available")])
    rows = await _rows(session_factory, units)

    assert result.projections["updated"] == 1
    assert rows[0]["deleted_at"] is None
    assert rows[0]["source_revision"] == 6


# --- Từ chối tường minh -----------------------------------------------------


async def test_unknown_deal_status_is_rejected(session_factory):
    await _seed_unit(session_factory)
    result = await _sync(session_factory, "deals", [_deal(status="khong-ton-tai")])

    assert await _rows(session_factory, deals) == [], "trạng thái lạ đã lọt vào bảng"
    assert result.projections["rejected"] == 1
    assert result.status == "failed"

    errors = await _errors(session_factory, result.sync_run_id)
    assert errors[0]["error_code"] == "UNKNOWN_DEAL_STATUS"
    assert errors[0]["source_record_id"] == "D-1"
    assert errors[0]["json_path"] == "$.records[0].data.status"
    assert errors[0]["field_name"] == "status"

    # Bản ghi bị từ chối KHÔNG được ghi nhận là đã chấp nhận ở tầng danh tính:
    # nếu không, lần gửi sửa lại sẽ bị bỏ qua vì "đã có rồi".
    assert await _rows(session_factory, crm_source_records) == [
        row for row in await _rows(session_factory, crm_source_records) if row["source_entity"] == "units"
    ]


@pytest.mark.parametrize("status", ["reserved", "sold", "lost"])
async def test_valid_deal_statuses_are_accepted(session_factory, status):
    await _seed_unit(session_factory)
    result = await _sync(session_factory, "deals", [_deal(status=status)])

    rows = await _rows(session_factory, deals)
    assert result.projections["inserted"] == 1
    assert rows[0]["status"] == status
    assert rows[0]["source_status"] == status


@pytest.mark.parametrize("alias", ["cancelled", "canceled"])
async def test_cancelled_is_mapped_to_lost_and_the_raw_value_is_kept(session_factory, alias):
    """Repo không có trạng thái `cancelled` (SRS §5.2.8) — ánh xạ về `lost`, giữ chữ gốc."""
    await _seed_unit(session_factory)
    result = await _sync(session_factory, "deals", [_deal(status=alias)])

    rows = await _rows(session_factory, deals)
    assert result.projections["inserted"] == 1
    assert rows[0]["status"] == "lost"
    assert rows[0]["source_status"] == alias, "mất chữ gốc thì không truy được vì sao thành 'lost'"


async def test_deal_pointing_at_an_unknown_unit_is_rejected(session_factory):
    result = await _sync(session_factory, "deals", [_deal(unit="KHONG-CO")])

    assert await _rows(session_factory, deals) == []
    errors = await _errors(session_factory, result.sync_run_id)
    assert errors[0]["error_code"] == "UNKNOWN_UNIT_REFERENCE"
    assert errors[0]["field_name"] == "external_unit_id"


async def test_unit_in_an_unknown_area_is_rejected(session_factory):
    result = await _sync(session_factory, "units", [_unit(area="KHONG-CO")])

    assert await _rows(session_factory, units) == []
    errors = await _errors(session_factory, result.sync_run_id)
    assert errors[0]["error_code"] == "UNKNOWN_AREA"


async def test_second_active_deal_on_one_unit_is_rejected_without_killing_the_batch(session_factory):
    """Ràng buộc DB chặn giao dịch giữ thứ hai; các bản ghi khác trong lô vẫn vào."""
    await _sync(session_factory, "units", [_unit("U-1", code="A1-01"), _unit("U-2", code="A1-02")])
    await _sync(session_factory, "deals", [_deal("D-1", unit="U-1", status="reserved")])

    result = await _sync(
        session_factory,
        "deals",
        [
            _deal("D-2", unit="U-1", status="sold"),  # căn đã có giao dịch giữ
            _deal("D-3", unit="U-2", status="reserved"),  # hợp lệ
        ],
    )

    rows = {row["external_deal_id"] for row in await _rows(session_factory, deals)}
    assert rows == {"D-1", "D-3"}
    assert result.projections["rejected"] == 1
    assert result.projections["inserted"] == 1
    assert result.status == "partially_completed"

    errors = await _errors(session_factory, result.sync_run_id)
    assert errors[0]["error_code"] == "CONSTRAINT_VIOLATION"
    assert "uq_deals_active_per_unit" in errors[0]["message"]
    # Không lộ SQL hay tham số.
    for leaked in ("INSERT INTO", "$1", "Traceback"):
        assert leaked not in errors[0]["message"]


async def test_rejected_record_can_be_resent_after_the_fix(session_factory):
    """Bản ghi bị từ chối không bị đánh dấu 'đã chấp nhận' → gửi lại sửa được."""
    await _seed_unit(session_factory)
    await _sync(session_factory, "deals", [_deal("D-1", status="khong-ton-tai")])

    fixed = await _sync(session_factory, "deals", [_deal("D-1", status="sold")])

    rows = await _rows(session_factory, deals)
    assert fixed.projections["inserted"] == 1
    assert len(rows) == 1
    assert rows[0]["status"] == "sold"


async def test_retry_of_the_same_batch_creates_no_duplicates(session_factory):
    first = await _sync(session_factory, "units", [_unit()], batch="fixed-batch")
    second = await _sync(session_factory, "units", [_unit()], batch="fixed-batch")

    assert second.replayed is True
    assert second.sync_run_id == first.sync_run_id
    assert len(await _rows(session_factory, units)) == 1


# --- Bộ tính hấp thụ mới ----------------------------------------------------


async def _stock(session_factory, sold=0, reserved=0, available=0, blocked=0):
    """Dựng một phân khu với số căn theo từng trạng thái, qua đúng luồng đồng bộ."""
    unit_records, deal_records, index = [], [], 0
    for kind, count in (("sold", sold), ("reserved", reserved), ("available", available), ("blocked", blocked)):
        for _ in range(count):
            index += 1
            unit_id = f"U-{index}"
            unit_status = "blocked" if kind == "blocked" else "available"
            unit_records.append(_unit(unit_id, code=f"A1-{index:03d}", status=unit_status))
            if kind in ("sold", "reserved"):
                deal_records.append(_deal(f"D-{index}", unit=unit_id, status=kind))
    await _sync(session_factory, "units", unit_records)
    if deal_records:
        await _sync(session_factory, "deals", deal_records)


async def test_calculator_counts_sold_reserved_and_remaining(session_factory):
    await _stock(session_factory, sold=3, reserved=2, available=5, blocked=1)

    result = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)
    area = result.inventory[0]

    assert area.units_sold == 3
    assert area.units_reserved == 2
    assert area.units_blocked == 1
    assert area.total_units == 10, "căn blocked nằm ngoài quỹ hàng bán được"
    assert area.units_remaining == 5
    assert result.anomalies == []


async def test_cancelled_deal_returns_the_unit_to_stock(session_factory):
    await _stock(session_factory, reserved=1, available=1)
    before = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)
    assert before.inventory[0].units_reserved == 1

    # `reserved_at=T1` phải đi CÙNG bản ghi huỷ: lần đặt cọc đã thực sự xảy ra,
    # và bỏ nó khỏi bản ghi đầy đủ là đánh rơi lịch sử — chốt A4 (Phase 8B) từ
    # chối đúng như vậy. Đây chính là kịch bản A4 mô tả.
    await _sync(
        session_factory,
        "deals",
        [_deal("D-1", unit="U-1", status="cancelled", revision=9, reserved_at=T1)],
    )
    after = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)

    assert after.inventory[0].units_reserved == 0
    assert after.inventory[0].units_remaining == 2, "căn bị huỷ phải quay lại quỹ hàng"


async def test_deleted_deal_is_not_counted(session_factory):
    await _stock(session_factory, sold=1, available=1)
    await _sync(session_factory, "deals", [_tombstone("D-1")])

    result = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)
    assert result.inventory[0].units_sold == 0
    assert result.inventory[0].units_remaining == 2


async def test_deleted_unit_is_not_counted(session_factory):
    await _stock(session_factory, available=2)
    await _sync(session_factory, "units", [_tombstone("U-1")])

    result = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)
    assert result.inventory[0].total_units == 1
    assert result.inventory[0].units_remaining == 1


async def test_deal_on_a_deleted_unit_is_reported_not_counted(session_factory):
    """Quan hệ không hợp lệ phải hiện ra, không được đếm im lặng."""
    await _stock(session_factory, sold=1)
    await _sync(session_factory, "units", [_tombstone("U-1")])

    result = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)

    assert result.units_sold == 0, "giao dịch treo trên căn đã xoá không được tính là đã bán"
    assert [a["code"] for a in result.anomalies] == ["DEAL_ON_DELETED_UNIT"]


async def test_historical_deals_do_not_double_count_a_unit(session_factory):
    """Một căn có nhiều giao dịch lịch sử vẫn chỉ được đếm một lần."""
    await _sync(session_factory, "units", [_unit("U-1", code="A1-01")])
    await _sync(session_factory, "deals", [_deal("D-1", unit="U-1", status="lost")])
    await _sync(session_factory, "deals", [_deal("D-2", unit="U-1", status="lost")])
    await _sync(session_factory, "deals", [_deal("D-3", unit="U-1", status="sold")])

    result = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)
    assert result.inventory[0].units_sold == 1
    assert result.inventory[0].units_remaining == 0


async def test_calculator_is_deterministic(session_factory):
    await _stock(session_factory, sold=2, reserved=1, available=3)
    service = DomainAbsorptionCalculatorService(session_factory)

    first = await service.compute(PROJECT_ID)
    second = await service.compute(PROJECT_ID)

    assert [(p.area_id, p.stat_date, p.units_sold, p.units_remaining) for p in first.points] == [
        (p.area_id, p.stat_date, p.units_sold, p.units_remaining) for p in second.points
    ]
    assert first.units_sold == second.units_sold


async def test_daily_points_carry_units_remaining(session_factory):
    await _stock(session_factory, sold=2, available=3)
    result = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)

    assert result.points, "phải có chuỗi ngày khi đã có giao dịch bán"
    last = result.points[-1]
    assert last.units_sold == 2  # cả hai căn bán cùng ngày trong fixture
    assert last.units_remaining == 3


async def test_project_without_units_returns_empty_result(session_factory):
    result = await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)
    assert result.inventory[0].total_units == 0
    assert result.points == []
    assert result.units_sold == 0


async def test_compute_never_writes_absorption_daily(session_factory):
    """`compute()` là thuần đọc — chạy song song không được đụng số liệu sản xuất."""
    await _stock(session_factory, sold=2, available=1)
    before = await _absorption_rows(session_factory)

    await DomainAbsorptionCalculatorService(session_factory).compute(PROJECT_ID)

    assert await _absorption_rows(session_factory) == before == []


async def test_persist_is_explicit_and_writes_units_remaining(session_factory):
    """Ghi xuống DB chỉ xảy ra khi được gọi tường minh — dùng sau khi cắt sang."""
    await _stock(session_factory, sold=2, available=3)
    service = DomainAbsorptionCalculatorService(session_factory)
    result = await service.compute(PROJECT_ID)

    written = await service.persist(result)
    rows = await _absorption_rows(session_factory)

    assert written == len(result.points)
    assert rows
    assert all(row["units_remaining"] is not None for row in rows)


# --- Chạy song song ---------------------------------------------------------


async def test_parallel_run_reports_differences_without_changing_production(session_factory):
    await _stock(session_factory, sold=2, reserved=1, available=2)
    before = await _absorption_rows(session_factory)

    report = await ParallelRunComparator(session_factory).compare(PROJECT_ID)

    # Bộ tính cũ đọc `sales_records` (rỗng ở đây) → chênh lệch phải hiện ra.
    assert report.legacy_units_sold == 0
    assert report.domain_units_sold == 2
    assert report.domain_units_reserved == 1
    assert not report.matches
    metrics = {d["metric"] for d in report.differences}
    assert "units_sold" in metrics
    assert "units_reserved" in metrics

    assert await _absorption_rows(session_factory) == before, "chạy song song đã đổi số liệu sản xuất"


async def test_parallel_run_reports_a_match_when_both_are_empty(session_factory):
    report = await ParallelRunComparator(session_factory).compare(PROJECT_ID)

    assert report.matches
    assert report.differences == []
    assert report.anomalies == []


async def test_parallel_run_surfaces_missing_crm_data_as_a_mismatch(session_factory):
    """Chưa đồng bộ CRM thì bộ tính mới ra 0 — phải hiện thành chênh lệch, không im lặng."""
    async with session_factory() as session:
        area_id = await session.scalar(sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID))
    file_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=file_id,
                    project_id=PROJECT_ID,
                    filename="legacy.csv",
                    checksum=f"chk-{uuid.uuid4()}",
                    status="completed",
                    rows_ok=1,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                    source_system="manual_upload",
                    source_instance_id="local",
                    input_format="csv",
                    transport_mode="file_upload",
                    sync_mode="full_snapshot",
                    schema_version=1,
                    rows_received=0,
                    error_summary={},
                )
            )
            await session.execute(
                sa.text(
                    "INSERT INTO sales_records (id, area_id, file_id, sold_date, units_sold, external_record_id, "
                    "source_row_hash, created_at) VALUES (:i, :a, :f, '2026-08-05', 4, 'TX-1', 'h1', now())"
                ),
                {"i": uuid.uuid4(), "a": area_id, "f": file_id},
            )

    report = await ParallelRunComparator(session_factory).compare(PROJECT_ID)

    assert report.legacy_units_sold == 4
    assert report.domain_units_sold == 0, "bản sao CRM chưa có gì"
    assert not report.matches
    difference = next(d for d in report.differences if d["metric"] == "units_sold")
    assert difference == {"metric": "units_sold", "legacy": 4, "domain": 0, "delta": -4}


# --- area_ref dạng area_id (Phase 1, lỗi P0) --------------------------------
#
# Hợp đồng v1 cho phép `area_ref` ở hai hình dạng (`$defs/area_ref` là `oneOf`):
# `{area_id}` hoặc `{area_name, unit_type}`. Trước Phase 1 tầng chiếu chỉ đọc
# được hình dạng thứ hai, nên một payload HỢP LỆ theo hợp đồng vẫn bị từ chối với
# `MISSING_FIELD` trên `area_name` — một trường mà payload đó không được phép
# mang. Không fixture nào trong docs/crm/fixtures/ phủ nhánh này.


async def _area_id_of(session_factory, name="A1", unit_type="2PN") -> uuid.UUID:
    async with session_factory() as session:
        return await session.scalar(
            sa.select(areas.c.id).where(
                areas.c.project_id == PROJECT_ID,
                areas.c.area_name == name,
                areas.c.unit_type == unit_type,
            )
        )


async def _add_area(session_factory, name: str, unit_type: str) -> uuid.UUID:
    area_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(areas).values(
                    id=area_id,
                    project_id=PROJECT_ID,
                    area_name=name,
                    unit_type=unit_type,
                    bedrooms=3,
                    area_sqm=95,
                    total_units=50,
                    created_at=datetime.now(UTC),
                )
            )
    return area_id


def _unit_by_area_id(area_id, record_id="U-1", *, code="A1-01", status="available", revision=1):
    """Bản ghi dùng nhánh `area_id` — KHÔNG mang area_name/unit_type.

    Đúng hình dạng mà `contract_adapter._flatten_area_ref()` sinh ra khi hệ nguồn
    gửi `"area_ref": {"area_id": "..."}`.
    """
    return {
        "source_record_id": record_id,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"area_id": str(area_id), "unit_code": code, "status": status},
    }


async def test_unit_resolves_area_by_area_id(session_factory):
    """Nhánh `area_id` đi hết đường tới `units`, và `unit_type` suy từ phân khu.

    `unit_type` KHÔNG có trong payload — hợp đồng v1 đóng `additionalProperties`
    ở `unit_payload` nên không có chỗ nào để gửi nó cùng `area_id`. Nguồn duy
    nhất là dòng `areas` đã tra được.
    """
    area_id = await _area_id_of(session_factory)

    result = await _sync(session_factory, "units", [_unit_by_area_id(area_id)])

    assert result.projections["inserted"] == 1
    assert result.projections["rejected"] == 0
    assert result.status == "completed"

    rows = await _rows(session_factory, units)
    assert len(rows) == 1
    assert rows[0]["area_id"] == area_id
    assert rows[0]["unit_type"] == "2PN", "unit_type phải lấy từ dòng areas"
    assert rows[0]["unit_code"] == "A1-01"
    assert rows[0]["status"] == "available"


async def test_area_id_from_another_project_is_rejected(session_factory):
    """Phạm vi dự án là ranh giới cứng, không phải gợi ý.

    Thiếu điều kiện `project_id` ở câu tra, một hệ nguồn gắn được căn của mình vào
    phân khu của dự án khác chỉ bằng cách đoán trúng UUID.
    """
    other_project = uuid.uuid4()
    other_area = uuid.uuid4()
    try:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'DỰ ÁN KHÁC', :d, :ts)"
                    ),
                    {"id": other_project, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
                )
                await session.execute(
                    sa.insert(areas).values(
                        id=other_area,
                        project_id=other_project,
                        area_name="Z9",
                        unit_type="1PN",
                        bedrooms=1,
                        area_sqm=45,
                        total_units=10,
                        created_at=datetime.now(UTC),
                    )
                )

        result = await _sync(session_factory, "units", [_unit_by_area_id(other_area)])

        assert result.projections["rejected"] == 1
        assert result.projections["inserted"] == 0
        assert await _rows(session_factory, units) == []

        errors = await _rows(session_factory, upload_errors)
        assert [e["error_code"] for e in errors] == ["UNKNOWN_AREA"]
        assert errors[0]["field_name"] == "area_id"
    finally:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(sa.delete(areas).where(areas.c.id == other_area))
                await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": other_project})


async def test_unknown_area_id_does_not_fall_back_to_area_name(session_factory):
    """Tra `area_id` không thấy thì TỪ CHỐI, kể cả khi `area_name` hợp lệ có mặt.

    Rơi về tên sẽ gắn căn vào một phân khu mà hệ nguồn không hề chỉ định, và
    không lỗi nào phát ra để ai kịp biết.
    """
    record = _unit_by_area_id(uuid.uuid4())
    record["data"]["area_name"] = "A1"  # hợp lệ, và CỐ Ý bị bỏ qua
    record["data"]["unit_type"] = "2PN"

    result = await _sync(session_factory, "units", [record])

    assert result.projections["rejected"] == 1
    assert await _rows(session_factory, units) == [], "không được lặng lẽ dùng area_name"

    errors = await _rows(session_factory, upload_errors)
    assert errors[0]["error_code"] == "UNKNOWN_AREA"
    assert errors[0]["field_name"] == "area_id"


async def test_malformed_area_id_is_rejected(session_factory):
    """UUID hỏng là lỗi TRƯỜNG, không phải lỗi tra cứu — mã lỗi phải nói đúng."""
    record = _unit_by_area_id("khong-phai-uuid")

    result = await _sync(session_factory, "units", [record])

    assert result.projections["rejected"] == 1
    errors = await _rows(session_factory, upload_errors)
    assert errors[0]["error_code"] == "INVALID_AREA_REF"
    assert errors[0]["error_category"] == "field"
    assert errors[0]["field_name"] == "area_id"


async def test_area_name_path_still_resolves_and_unit_type_is_unchanged(session_factory):
    """Hồi quy: nhánh cũ cho ra ĐÚNG dòng như trước khi sửa.

    `unit_type` bây giờ đọc từ `areas` thay vì từ payload. Hai giá trị luôn bằng
    nhau vì `uq_areas_project_name_unit_type` buộc thế — test này chốt điều đó.
    """
    result = await _sync(session_factory, "units", [_unit("U-9", code="A1-09")])

    assert result.projections["inserted"] == 1
    rows = await _rows(session_factory, units)
    assert len(rows) == 1
    assert rows[0]["unit_type"] == "2PN"
    assert rows[0]["area_id"] == await _area_id_of(session_factory)


async def test_partial_area_move_by_name_is_not_overridden_by_stale_mirror(session_factory):
    """Chuyển phân khu bằng bản ghi partial phải THẬT SỰ chuyển.

    Đây là lý do `history_guard._read_unit_mirror()` KHÔNG trả `area_id`.
    `merge_record()` chép mọi khoá bản sao đang giữ mà bản ghi partial không
    mang; nếu `area_id` nằm trong đó, nó sẽ được chép vào rồi THẮNG ở
    `_resolve_area`, và lệnh chuyển biến mất mà không một lỗi nào phát ra.
    """
    a1 = await _area_id_of(session_factory)
    a2 = await _add_area(session_factory, "A2", "3PN")

    await _sync(session_factory, "units", [_unit("U-7", code="A1-07")])
    assert (await _rows(session_factory, units))[0]["area_id"] == a1

    moved = {
        "source_record_id": "U-7",
        "operation": "upsert",
        "source_revision": 2,
        "payload_completeness": "partial",
        "data": {"area_name": "A2", "unit_type": "3PN"},
    }
    result = await _sync(session_factory, "units", [moved])

    assert result.projections["updated"] == 1, "bản ghi partial phải được chấp nhận"
    rows = await _rows(session_factory, units)
    assert len(rows) == 1
    assert rows[0]["area_id"] == a2, "căn phải chuyển sang A2, không bị area_id cũ kéo lại"
    assert rows[0]["unit_type"] == "3PN"
    assert rows[0]["unit_code"] == "A1-07", "trường không gửi phải được giữ nguyên"
