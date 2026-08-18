"""Ghi lại lịch sử so sánh hai bộ tính — và KHÔNG chạm lineage nào.

Hai bất biến được canh gắt nhất:

1. **`absorption_daily` không suy suyển.** Kiểm bằng DẤU VÂN TOÀN DÒNG (mọi cột,
   cả hai lineage) chứ không bằng số dòng: một lần ghi đè giữ nguyên số dòng sẽ
   lọt qua phép đếm, và đó đúng là kiểu hỏng khó tìm nhất.
2. **"Không có dữ liệu" khác "bằng không".** Một dự án rỗng khiến cả hai bên ra 0
   và trông như "khớp". Mười bốn ngày như thế trông y hệt mười bốn ngày chạy song
   song thành công — nên nó phải ghi NULL và bị view cổng loại ra.

Chạy: TEST_TARGET=tests/test_services/test_parallel_run.py bash scripts/test_db.sh
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import (
    absorption_daily,
    areas,
    calculator_comparisons,
    deals,
    inventory_snapshots,
    sales_records,
    units,
    upload_files,
)
from src.services.calculators import CALCULATOR_DOMAIN, CALCULATOR_LEGACY
from src.services.parallel_run import (
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULE,
    ParallelRunCaptureService,
    UnknownProjectError,
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

PROJECT_ID = uuid.UUID("d0e1f2a3-b4c5-4637-8849-abcdef012e0d")
AREA_ID = uuid.UUID("d0e1f2a3-b4c5-4637-8849-abcdef012e0e")
EMPTY_PROJECT_ID = uuid.UUID("d0e1f2a3-b4c5-4637-8849-abcdef012e0f")
# `sales_records.file_id` / `inventory_snapshots.file_id` là NOT NULL — dữ liệu
# tổng hợp cũ luôn đến từ một lô nạp file, nên seed phải có một lô thật.
FILE_ID = uuid.UUID("d0e1f2a3-b4c5-4637-8849-abcdef012e10")
INSTANCE = "crm-parallel-run"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async def wipe(session):
        for project in (PROJECT_ID, EMPTY_PROJECT_ID):
            area_ids = sa.select(areas.c.id).where(areas.c.project_id == project)
            await session.execute(
                sa.delete(calculator_comparisons).where(calculator_comparisons.c.project_id == project)
            )
            await session.execute(sa.delete(deals).where(deals.c.source_instance_id == INSTANCE))
            await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
            await session.execute(sa.delete(absorption_daily).where(absorption_daily.c.area_id.in_(area_ids)))
            await session.execute(sa.delete(sales_records).where(sales_records.c.area_id.in_(area_ids)))
            await session.execute(sa.delete(inventory_snapshots).where(inventory_snapshots.c.area_id.in_(area_ids)))
            await session.execute(sa.delete(areas).where(areas.c.project_id == project))
            await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == project))
            await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": project})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            for project, name in ((PROJECT_ID, "PR"), (EMPTY_PROJECT_ID, "PR-EMPTY")):
                await session.execute(
                    sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, :n, :d, :ts)"),
                    {"i": project, "n": name, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
                )
            await session.execute(
                sa.insert(areas),
                [
                    {
                        "id": AREA_ID,
                        "project_id": PROJECT_ID,
                        "area_name": "A1",
                        "unit_type": "2PN",
                        "bedrooms": 2,
                        "area_sqm": 75,
                        "total_units": 10,
                        "created_at": datetime.now(UTC),
                    }
                ],
            )
            await session.execute(
                sa.insert(upload_files).values(
                    id=FILE_ID,
                    project_id=PROJECT_ID,
                    status="completed",
                    rows_ok=1,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                    input_format="csv",
                    transport_mode="file_upload",
                    rows_received=1,
                    error_summary={},
                )
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)


# --- Helper -------------------------------------------------------------------


async def _add_legacy_data(session_factory, *, units_sold=4, units_remaining=6):
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(sales_records).values(
                    id=uuid.uuid4(),
                    area_id=AREA_ID,
                    file_id=FILE_ID,
                    external_record_id=f"legacy-sale-{uuid.uuid4().hex[:8]}",
                    source_row_hash=uuid.uuid4().hex,
                    sold_date=date(2026, 3, 1),
                    units_sold=units_sold,
                    created_at=datetime.now(UTC),
                )
            )
            await session.execute(
                sa.insert(inventory_snapshots).values(
                    id=uuid.uuid4(),
                    area_id=AREA_ID,
                    file_id=FILE_ID,
                    source_row_hash=uuid.uuid4().hex,
                    snapshot_date=date(2026, 3, 1),
                    units_remaining=units_remaining,
                    snapshot_type="closing",
                    created_at=datetime.now(UTC),
                )
            )


async def _add_domain_data(session_factory, *, sold=4, unit_count=10):
    """Căn + giao dịch để bộ tính miền có gì mà tính."""
    async with session_factory() as session:
        async with session.begin():
            now = datetime.now(UTC)
            for index in range(unit_count):
                unit_id = uuid.uuid4()
                status = "sold" if index < sold else "available"
                await session.execute(
                    sa.insert(units).values(
                        id=unit_id,
                        source_system="mini_crm",
                        source_instance_id=INSTANCE,
                        external_unit_id=f"PR-U-{index}",
                        area_id=AREA_ID,
                        unit_code=f"A1-{index:02d}",
                        unit_type="2PN",
                        status=status,
                        source_revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                if index < sold:
                    await session.execute(
                        sa.insert(deals).values(
                            id=uuid.uuid4(),
                            source_system="mini_crm",
                            source_instance_id=INSTANCE,
                            external_deal_id=f"PR-D-{index}",
                            unit_id=unit_id,
                            status="sold",
                            source_status="sold",
                            sold_at=datetime(2026, 3, 1, tzinfo=UTC),
                            source_revision=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )


async def _seed_absorption(session_factory):
    """Cả hai lineage, để dấu vân có gì thật mà so."""
    async with session_factory() as session:
        async with session.begin():
            for calculator, reserved in ((CALCULATOR_LEGACY, None), (CALCULATOR_DOMAIN, 2)):
                await session.execute(
                    sa.insert(absorption_daily).values(
                        id=uuid.uuid4(),
                        area_id=AREA_ID,
                        stat_date=date(2026, 3, 1),
                        units_sold=4,
                        units_remaining=6,
                        units_reserved=reserved,
                        velocity_7d=Decimal("1.0"),
                        velocity_30d=Decimal("1.0"),
                        data_quality_status="ok",
                        is_observed=True,
                        computed_at=datetime.now(UTC),
                        calculator=calculator,
                        computation_id=uuid.uuid4(),
                    )
                )


async def _absorption_digest(session_factory) -> list[tuple]:
    """MỌI cột của MỌI dòng, cả hai lineage. Đếm dòng là không đủ."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                sa.select(absorption_daily).order_by(
                    absorption_daily.c.area_id, absorption_daily.c.stat_date, absorption_daily.c.calculator
                )
            )
        ).all()
    return [tuple(row) for row in rows]


async def _rows(session_factory, project_id=PROJECT_ID) -> list[dict]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(calculator_comparisons)
            .where(calculator_comparisons.c.project_id == project_id)
            .order_by(calculator_comparisons.c.compared_at)
        )
        return [dict(r) for r in result.mappings().all()]


# === Không chạm lineage nào ===================================================


async def test_capture_does_not_modify_absorption_daily(session_factory):
    """Bất biến trung tâm, kiểm bằng dấu vân toàn dòng."""
    await _add_legacy_data(session_factory)
    await _add_domain_data(session_factory)
    await _seed_absorption(session_factory)
    before = await _absorption_digest(session_factory)
    assert before, "phải có dòng thật để so, nếu không test này không chứng minh gì"

    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    assert await _absorption_digest(session_factory) == before


async def test_capture_does_not_change_the_calculator_flag(session_factory):
    await _add_domain_data(session_factory)
    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    async with session_factory() as session:
        flags = set((await session.execute(sa.text("SELECT DISTINCT absorption_calculator FROM projects"))).scalars())
    assert flags == {CALCULATOR_LEGACY}


async def test_capture_writes_exactly_one_row(session_factory):
    await _add_domain_data(session_factory)
    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    assert len(await _rows(session_factory)) == 1


# === Lịch sử chỉ thêm =========================================================


async def test_repeated_capture_appends_and_never_overwrites(session_factory):
    await _add_legacy_data(session_factory)
    await _add_domain_data(session_factory)
    service = ParallelRunCaptureService(session_factory)

    first = await service.capture(PROJECT_ID)
    second = await service.capture(PROJECT_ID)
    third = await service.capture(PROJECT_ID)

    rows = await _rows(session_factory)
    assert len(rows) == 3
    assert {r["id"] for r in rows} == {first.comparison_id, second.comparison_id, third.comparison_id}


async def test_history_returns_newest_first(session_factory):
    await _add_domain_data(session_factory)
    service = ParallelRunCaptureService(session_factory)
    await service.capture(PROJECT_ID)
    latest = await service.capture(PROJECT_ID)

    history = await service.history(PROJECT_ID)

    assert history[0]["id"] == latest.comparison_id


async def test_the_trigger_is_recorded(session_factory):
    await _add_domain_data(session_factory)
    service = ParallelRunCaptureService(session_factory)

    await service.capture(PROJECT_ID, trigger=TRIGGER_MANUAL)
    await service.capture(PROJECT_ID, trigger=TRIGGER_SCHEDULE)

    assert {r["trigger"] for r in await _rows(session_factory)} == {TRIGGER_MANUAL, TRIGGER_SCHEDULE}


async def test_an_unknown_trigger_is_refused(session_factory):
    with pytest.raises(ValueError):
        await ParallelRunCaptureService(session_factory).capture(PROJECT_ID, trigger="thinh-thoang")


# === Bốn trạng thái dữ liệu ===================================================


async def test_both_lineages_have_data(session_factory):
    await _add_legacy_data(session_factory, units_sold=4, units_remaining=6)
    await _add_domain_data(session_factory, sold=4)

    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    row = (await _rows(session_factory))[0]
    assert row["legacy_has_data"] is True
    assert row["domain_has_data"] is True
    assert row["legacy_units_sold"] == 4
    assert row["domain_units_sold"] == 4


async def test_no_domain_data_records_nulls_not_zeros(session_factory):
    """Yêu cầu trung tâm: 0 và "không có gì" phải phân biệt được."""
    await _add_legacy_data(session_factory)

    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    row = (await _rows(session_factory))[0]
    assert row["domain_has_data"] is False
    assert row["domain_units_sold"] is None
    assert row["domain_units_remaining"] is None
    assert row["domain_units_reserved"] is None
    assert row["legacy_has_data"] is True


async def test_no_legacy_data_records_nulls_not_zeros(session_factory):
    await _add_domain_data(session_factory)

    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    row = (await _rows(session_factory))[0]
    assert row["legacy_has_data"] is False
    assert row["legacy_units_sold"] is None
    assert row["legacy_units_remaining"] is None
    assert row["domain_has_data"] is True


async def test_a_project_with_no_areas_is_recorded_not_crashed(session_factory):
    """Dự án rỗng vẫn là một quan sát hợp lệ — chỉ là không được tính vào cổng."""
    result = await ParallelRunCaptureService(session_factory).capture(EMPTY_PROJECT_ID)

    row = (await _rows(session_factory, EMPTY_PROJECT_ID))[0]
    assert result.domain_has_data is False
    assert row["legacy_has_data"] is False
    assert row["domain_has_data"] is False


async def test_an_empty_project_never_reaches_the_gate_view(session_factory):
    """Cái "khớp" rỗng tuếch không bao giờ được đếm vào 14 ngày chạy song song."""
    await ParallelRunCaptureService(session_factory).capture(EMPTY_PROJECT_ID)
    service = ParallelRunCaptureService(session_factory)

    assert len(await service.history(EMPTY_PROJECT_ID)) == 1
    assert await service.history(EMPTY_PROJECT_ID, gate_only=True) == []


async def test_an_unknown_project_is_refused(session_factory):
    with pytest.raises(UnknownProjectError):
        await ParallelRunCaptureService(session_factory).capture(uuid.uuid4())


# === Chênh lệch và bất thường =================================================


async def test_differences_are_recorded_with_their_detail(session_factory):
    """Bên cũ nói bán 9, bên miền nói bán 4 — chênh thật, phải ghi lại đủ để đọc."""
    await _add_legacy_data(session_factory, units_sold=9, units_remaining=1)
    await _add_domain_data(session_factory, sold=4)

    result = await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    row = (await _rows(session_factory))[0]
    assert result.matches is False
    assert row["matches"] is False
    assert row["difference_count"] >= 1
    metrics = {d["metric"] for d in row["differences"]}
    assert "units_sold" in metrics


async def test_anomalies_are_recorded(session_factory):
    """Giao dịch trỏ vào căn đã xoá mềm — bất thường mà bộ so sánh phát hiện."""
    await _add_domain_data(session_factory, sold=2, unit_count=4)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(units)
                .where(units.c.external_unit_id == "PR-U-0")
                .values(deleted_at=datetime.now(UTC), updated_at=datetime.now(UTC))
            )

    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    row = (await _rows(session_factory))[0]
    assert row["anomaly_count"] >= 1
    assert row["matches"] is False
    assert {a["code"] for a in row["anomalies"]}


async def test_matches_never_contradicts_the_counts(session_factory):
    await _add_legacy_data(session_factory, units_sold=9, units_remaining=1)
    await _add_domain_data(session_factory, sold=4)

    await ParallelRunCaptureService(session_factory).capture(PROJECT_ID)

    row = (await _rows(session_factory))[0]
    assert row["matches"] == (row["difference_count"] == 0 and row["anomaly_count"] == 0)


# === Chạy hết dự án ===========================================================


async def test_capture_all_covers_every_project(session_factory):
    await _add_domain_data(session_factory)

    results = await ParallelRunCaptureService(session_factory).capture_all()

    captured = {r.project_id for r in results}
    assert {PROJECT_ID, EMPTY_PROJECT_ID} <= captured


async def test_capture_all_survives_one_broken_project(session_factory, monkeypatch):
    """Dừng cả lượt vì một dự án là mất luôn quan sát của tất cả."""
    service = ParallelRunCaptureService(session_factory)
    original = service.capture

    async def flaky(project_id, **kwargs):
        if uuid.UUID(str(project_id)) == PROJECT_ID:
            raise RuntimeError("dữ liệu dự án này vỡ")
        return await original(project_id, **kwargs)

    monkeypatch.setattr(service, "capture", flaky)
    results = await service.capture_all()

    assert PROJECT_ID not in {r.project_id for r in results}
    assert EMPTY_PROJECT_ID in {r.project_id for r in results}
