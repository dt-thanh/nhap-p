"""Test AbsorptionCalculatorService + AreaService trên PostgreSQL thật.

Chạy bằng `bash scripts/test_db.sh` (xem test_import_records.py để biết cách
bật/skip và vì sao chỉ chấp nhận database tên `_test`).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import absorption_daily, areas, inventory_snapshots, sales_records, upload_files
from src.services.absorption import AbsorptionCalculatorService, AreaService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _skip_reason(url: str | None) -> str:
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' — chạy `bash scripts/test_db.sh`"
    return ""


_SKIP = _skip_reason(TEST_DATABASE_URL)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

PROJECT_ID = uuid.UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301")
START = date(2026, 1, 1)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async with session_factory() as session:
        async with session.begin():
            for table in (absorption_daily, sales_records, inventory_snapshots, areas, upload_files):
                await session.execute(sa.delete(table))
            await session.execute(sa.text("DELETE FROM projects"))
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Pilot', :d, :ts)"),
                {"id": PROJECT_ID, "d": START, "ts": datetime.now(UTC)},
            )
    yield


@pytest_asyncio.fixture
async def area(session_factory):
    """Một phân khu + một upload_files để sales_records có khoá ngoại hợp lệ."""
    area_id, file_id = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=file_id,
                    project_id=PROJECT_ID,
                    uploaded_by=None,
                    filename="s.csv",
                    checksum=f"chk-{uuid.uuid4()}",
                    status="completed",
                    rows_ok=0,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                )
            )
            await session.execute(
                sa.insert(areas).values(
                    id=area_id,
                    project_id=PROJECT_ID,
                    area_name="A1",
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=75,
                    total_units=100,
                    created_at=datetime.now(UTC),
                )
            )
    return area_id, file_id


async def _add_sales(session_factory, area_id, file_id, rows: list[tuple[date, int]]):
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(sales_records),
                [
                    {
                        "id": uuid.uuid4(),
                        "area_id": area_id,
                        "file_id": file_id,
                        "sold_date": sold_date,
                        "units_sold": units,
                        "external_record_id": f"TX-{index}",
                        "source_row_hash": f"h-{index}",
                        "created_at": datetime.now(UTC),
                    }
                    for index, (sold_date, units) in enumerate(rows)
                ],
            )


async def _points(session_factory, area_id):
    async with session_factory() as session:
        return (
            await session.execute(
                sa.select(absorption_daily)
                .where(absorption_daily.c.area_id == area_id)
                .order_by(absorption_daily.c.stat_date)
            )
        ).all()


# --- Tính absorption_daily --------------------------------------------------


async def test_recompute_fills_gap_days_between_sales(session_factory, area):
    """Ngày không bán vẫn phải có dòng, đánh dấu is_observed=False.

    Bỏ trống ngày không bán sẽ khiến trung bình trượt bị thổi lên vì mẫu số co lại.
    """
    area_id, file_id = area
    await _add_sales(session_factory, area_id, file_id, [(START, 4), (START + timedelta(days=2), 2)])

    written = await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    assert written == 3  # ngày 1, 2 (điền bù), 3
    rows = await _points(session_factory, area_id)
    assert [r.units_sold for r in rows] == [4, 0, 2]
    assert [r.is_observed for r in rows] == [True, False, True]


async def test_velocity_is_a_rolling_mean_not_a_running_total(session_factory, area):
    """velocity_7d = trung bình cộng, để đọc được là 'mỗi ngày bán mấy căn'."""
    area_id, file_id = area
    await _add_sales(
        session_factory,
        area_id,
        file_id,
        [(START, 4), (START + timedelta(days=1), 2), (START + timedelta(days=2), 3)],
    )

    await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    rows = await _points(session_factory, area_id)
    assert rows[0].velocity_7d == Decimal("4.0000")  # 4/1
    assert rows[1].velocity_7d == Decimal("3.0000")  # (4+2)/2
    assert rows[2].velocity_7d == Decimal("3.0000")  # (4+2+3)/3


async def test_rolling_window_drops_days_outside_seven(session_factory, area):
    """Ngày thứ 8 không còn tính ngày đầu tiên nữa."""
    area_id, file_id = area
    await _add_sales(
        session_factory,
        area_id,
        file_id,
        [(START, 70)] + [(START + timedelta(days=offset), 0) for offset in range(1, 8)],
    )

    await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    rows = await _points(session_factory, area_id)
    assert rows[6].velocity_7d == Decimal("10.0000")  # 70/7, còn trong cửa sổ
    assert rows[7].velocity_7d == Decimal("0.0000")  # đã rơi ra khỏi cửa sổ


async def test_short_history_is_flagged_as_warning(session_factory, area):
    """Chuỗi ngắn hơn 30 ngày thì velocity_30d chưa đáng tin — đánh dấu warning."""
    area_id, file_id = area
    await _add_sales(
        session_factory,
        area_id,
        file_id,
        [(START + timedelta(days=offset), 1) for offset in range(35)],
    )

    await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    rows = await _points(session_factory, area_id)
    assert rows[0].data_quality_status == "warning"
    assert rows[28].data_quality_status == "warning"
    assert rows[29].data_quality_status == "ok"  # đủ 30 ngày


async def test_recompute_is_idempotent(session_factory, area):
    """Chạy hai lần ra đúng một kết quả — không nhân đôi dòng."""
    area_id, file_id = area
    await _add_sales(session_factory, area_id, file_id, [(START, 4), (START + timedelta(days=1), 2)])

    first = await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)
    second = await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    assert first == second == 2
    assert len(await _points(session_factory, area_id)) == 2


async def test_recompute_without_areas_writes_nothing(session_factory):
    assert await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID) == 0


# --- AreaService ------------------------------------------------------------


async def test_list_areas_joins_the_latest_inventory_snapshot(session_factory, area):
    """Tồn kho lấy bản chốt MỚI NHẤT, không phải bản đầu tiên."""
    area_id, file_id = area
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(inventory_snapshots),
                [
                    {
                        "id": uuid.uuid4(),
                        "area_id": area_id,
                        "file_id": file_id,
                        "snapshot_date": START,
                        "units_remaining": 100,
                        "snapshot_type": "opening",
                        "source_row_hash": "i1",
                        "created_at": datetime.now(UTC),
                    },
                    {
                        "id": uuid.uuid4(),
                        "area_id": area_id,
                        "file_id": file_id,
                        "snapshot_date": START + timedelta(days=10),
                        "units_remaining": 80,
                        "snapshot_type": "closing",
                        "source_row_hash": "i2",
                        "created_at": datetime.now(UTC),
                    },
                ],
            )

    result = await AreaService(session_factory).list_areas(PROJECT_ID)

    assert len(result) == 1
    assert result[0].units_remaining == 80
    assert result[0].snapshot_date == START + timedelta(days=10)


async def test_list_areas_returns_area_without_any_snapshot(session_factory, area):
    """Phân khu chưa có bản chốt nào vẫn phải hiện, tồn kho để None."""
    result = await AreaService(session_factory).list_areas(PROJECT_ID)

    assert len(result) == 1
    assert result[0].units_remaining is None
    assert result[0].area_name == "A1"


async def test_absorption_series_filters_by_date_range(session_factory, area):
    area_id, file_id = area
    await _add_sales(
        session_factory,
        area_id,
        file_id,
        [(START + timedelta(days=offset), 1) for offset in range(10)],
    )
    await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    points = await AreaService(session_factory).absorption_series(
        area_id, date_from=START + timedelta(days=2), date_to=START + timedelta(days=4)
    )

    assert [p.stat_date for p in points] == [START + timedelta(days=offset) for offset in (2, 3, 4)]


async def test_weekly_granularity_sums_units_but_not_velocity(session_factory, area):
    """Gộp tuần: cộng units_sold, còn vận tốc lấy của ngày cuối tuần.

    Cộng dồn trung bình trượt lại lần nữa sẽ ra con số không có nghĩa.
    """
    area_id, file_id = area
    await _add_sales(
        session_factory,
        area_id,
        file_id,
        [(START + timedelta(days=offset), 2) for offset in range(14)],
    )
    await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    weekly = await AreaService(session_factory).absorption_series(area_id, granularity="week")
    daily = await AreaService(session_factory).absorption_series(area_id, granularity="day")

    assert len(weekly) < len(daily)
    assert sum(p.units_sold for p in weekly) == sum(p.units_sold for p in daily)
    assert all(p.velocity_7d <= Decimal("2.0001") for p in weekly)


async def test_summary_reports_inventory_sales_and_velocity(session_factory, area):
    area_id, file_id = area
    await _add_sales(session_factory, area_id, file_id, [(START, 3), (START + timedelta(days=1), 5)])
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(inventory_snapshots).values(
                    id=uuid.uuid4(),
                    area_id=area_id,
                    file_id=file_id,
                    snapshot_date=START,
                    units_remaining=92,
                    snapshot_type="closing",
                    source_row_hash="i1",
                    created_at=datetime.now(UTC),
                )
            )
    await AbsorptionCalculatorService(session_factory).recompute(PROJECT_ID)

    summary = await AreaService(session_factory).summary(PROJECT_ID)

    assert summary.units_sold == 8
    assert summary.units_remaining == 92
    assert summary.total_units == 100
    assert summary.velocity_7d == Decimal("4.0000")
    assert summary.velocity_30d == Decimal("4.0000")
    assert summary.avg_velocity_30d == Decimal("4.0000")  # (3+5)/2
    assert summary.updated_at is not None

    scoped = await AreaService(session_factory).summary(PROJECT_ID, area_id=area_id)
    assert scoped.total_units == 100
    assert scoped.units_sold == 8
    assert scoped.units_remaining == 92


async def test_summary_of_empty_project_is_all_zeros(session_factory):
    summary = await AreaService(session_factory).summary(PROJECT_ID)

    assert (summary.units_sold, summary.units_remaining) == (0, 0)
    assert summary.updated_at is None
