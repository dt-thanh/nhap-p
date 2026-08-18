"""Hai bộ tính sống chung mà không can thiệp lẫn nhau, và dashboard đọc đúng một.

Bất biến trung tâm của Phase 6, phát biểu thẳng: **nạp một file Excel không được
xoá dòng của bộ tính miền, và tính lại bộ tính miền không được xoá dòng của bộ
tính cũ.** Trước Phase 6 cả hai câu DELETE đều lọc theo mỗi `area_id`, nên bên
nào chạy sau sẽ xoá sạch của bên kia — im lặng, không lỗi, không dấu vết.

Khoá duy nhất mở rộng ở 0012 chỉ làm việc sống chung KHẢ THI. Thứ thực sự bảo vệ
là phạm vi `calculator` trong câu DELETE, nên các test dưới đây kiểm bằng cách
CHẠY THẬT hai bộ tính rồi so nội dung từng dòng, không phải chỉ đếm.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import absorption_daily, areas, deals, sales_records, units, upload_files
from src.services.absorption import AbsorptionCalculatorService, AreaService
from src.services.calculators import CALCULATOR_DOMAIN, CALCULATOR_LEGACY
from src.services.domain_absorption import DomainAbsorptionCalculatorService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

PROJECT_ID = uuid.UUID("a1b2c3d4-e5f6-4708-8912-3456789abc60")
AREA_ID = uuid.UUID("a1b2c3d4-e5f6-4708-8912-3456789abc61")
INSTANCE = "synthetic-coexist-crm"
FILE_ID = uuid.UUID("a1b2c3d4-e5f6-4708-8912-3456789abc62")


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def seeded(session_factory, monkeypatch):
    """Dự án tổng hợp có CẢ dữ liệu tổng hợp (sales_records) lẫn dữ liệu miền (units/deals)."""
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        await session.execute(sa.delete(deals).where(deals.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(units).where(units.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(absorption_daily).where(absorption_daily.c.area_id == AREA_ID))
        await session.execute(sa.delete(sales_records).where(sales_records.c.area_id == AREA_ID))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'COEXIST', :d, :t)"),
                {"p": PROJECT_ID, "d": date(2026, 1, 1), "t": datetime.now(UTC)},
            )
            await session.execute(
                sa.insert(areas).values(
                    id=AREA_ID,
                    project_id=PROJECT_ID,
                    area_name="A1",
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=75,
                    total_units=100,
                    created_at=datetime.now(UTC),
                )
            )
            await session.execute(
                sa.insert(upload_files).values(
                    id=FILE_ID,
                    project_id=PROJECT_ID,
                    uploaded_by=None,
                    filename="s.csv",
                    checksum="coexist",
                    status="completed",
                    rows_ok=0,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                )
            )
            # Dữ liệu TỔNG HỢP cho bộ tính cũ.
            await session.execute(
                sa.insert(sales_records),
                [
                    {
                        "id": uuid.uuid4(),
                        "area_id": AREA_ID,
                        "file_id": FILE_ID,
                        "sold_date": date(2026, 3, day),
                        "units_sold": sold,
                        "external_record_id": f"COEX-{day}",
                        "source_row_hash": f"hash-coex-{day}",
                        "created_at": datetime.now(UTC),
                    }
                    for day, sold in ((1, 3), (2, 5))
                ],
            )
            # Dữ liệu MIỀN cho bộ tính mới.
            unit_ids = []
            for index in range(4):
                unit_id = uuid.uuid4()
                unit_ids.append(unit_id)
                await session.execute(
                    sa.insert(units).values(
                        id=unit_id,
                        source_system="mini_crm",
                        source_instance_id=INSTANCE,
                        external_unit_id=f"SYNTH-U-{index:03d}",
                        area_id=AREA_ID,
                        unit_code=f"A1-{index:03d}",
                        unit_type="2PN",
                        status="sold" if index < 2 else "reserved",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
            for index, unit_id in enumerate(unit_ids):
                sold = index < 2
                await session.execute(
                    sa.insert(deals).values(
                        id=uuid.uuid4(),
                        source_system="mini_crm",
                        source_instance_id=INSTANCE,
                        external_deal_id=f"SYNTH-D-{index:03d}",
                        unit_id=unit_id,
                        status="sold" if sold else "reserved",
                        source_status="sold" if sold else "reserved",
                        reserved_at=datetime(2026, 2, 20, tzinfo=UTC),
                        sold_at=datetime(2026, 3, 1 + index, tzinfo=UTC) if sold else None,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


async def _rows(session_factory, calculator: str) -> list[dict]:
    async with session_factory() as session:
        result = (
            (
                await session.execute(
                    sa.select(absorption_daily)
                    .where(absorption_daily.c.area_id == AREA_ID, absorption_daily.c.calculator == calculator)
                    .order_by(absorption_daily.c.stat_date)
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in result]


def _content(rows: list[dict]) -> list[tuple]:
    """Nội dung nghiệp vụ, BỎ QUA id/computed_at/computation_id.

    Ba trường đó đổi mỗi lần chạy theo thiết kế; so cả chúng thì mọi test
    idempotency đều đỏ mà không nói lên điều gì.
    """
    return [
        (r["stat_date"], r["units_sold"], r["units_remaining"], r["units_reserved"], r["velocity_30d"]) for r in rows
    ]


async def _persist_domain(session_factory):
    service = DomainAbsorptionCalculatorService()
    result = await service.compute(PROJECT_ID)
    return await service.persist(result)


# --- Không can thiệp lẫn nhau ------------------------------------------------


async def test_excel_recompute_does_not_delete_domain_rows(session_factory):
    """Nạp file Excel/CSV không được xoá chuỗi của bộ tính miền."""
    await _persist_domain(session_factory)
    domain_before = _content(await _rows(session_factory, CALCULATOR_DOMAIN))
    assert domain_before, "cần có dòng miền thì test mới có nghĩa"

    await AbsorptionCalculatorService().recompute(PROJECT_ID)

    assert _content(await _rows(session_factory, CALCULATOR_DOMAIN)) == domain_before
    assert await _rows(session_factory, CALCULATOR_LEGACY), "bộ tính cũ vẫn phải ghi được dòng của nó"


async def test_domain_recompute_does_not_delete_legacy_rows(session_factory):
    """Tính lại bộ tính miền không được xoá dòng mà dashboard đang đọc."""
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    legacy_before = _content(await _rows(session_factory, CALCULATOR_LEGACY))
    assert legacy_before

    await _persist_domain(session_factory)

    assert _content(await _rows(session_factory, CALCULATOR_LEGACY)) == legacy_before


async def test_both_lineages_end_up_in_the_table_together(session_factory):
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    await _persist_domain(session_factory)

    legacy = await _rows(session_factory, CALCULATOR_LEGACY)
    domain = await _rows(session_factory, CALCULATOR_DOMAIN)

    assert legacy and domain
    # Cùng ngày, hai lineage — điều mà khoá hẹp cũ không cho phép.
    assert set(r["stat_date"] for r in legacy) & set(r["stat_date"] for r in domain)


# --- Nguồn gốc ---------------------------------------------------------------


async def test_legacy_rows_declare_their_provenance(session_factory):
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    rows = await _rows(session_factory, CALCULATOR_LEGACY)

    assert rows
    assert all(r["calculator"] == CALCULATOR_LEGACY for r in rows)
    # Bộ tính cũ đọc `sales_records` — không có cách nào biết số căn giữ chỗ.
    assert all(r["units_reserved"] is None for r in rows)
    assert all(r["units_remaining"] is None for r in rows)
    assert all(r["computation_id"] is not None for r in rows)


async def test_domain_rows_declare_their_provenance(session_factory):
    await _persist_domain(session_factory)
    rows = await _rows(session_factory, CALCULATOR_DOMAIN)

    assert rows
    assert all(r["calculator"] == CALCULATOR_DOMAIN for r in rows)
    # Bộ tính miền ĐỌC ĐƯỢC số căn giữ chỗ từ `deals`.
    assert all(r["units_reserved"] is not None for r in rows)
    assert all(r["computation_id"] is not None for r in rows)


async def test_all_rows_of_one_run_share_a_computation_id(session_factory):
    """Một lần chạy = một computation_id. Hai giá trị lẫn lộn = ghi dở dang."""
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    rows = await _rows(session_factory, CALCULATOR_LEGACY)

    assert len({r["computation_id"] for r in rows}) == 1


async def test_each_run_gets_a_new_computation_id(session_factory):
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    first = {r["computation_id"] for r in await _rows(session_factory, CALCULATOR_LEGACY)}
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    second = {r["computation_id"] for r in await _rows(session_factory, CALCULATOR_LEGACY)}

    assert first != second, "không truy được lần chạy nếu id không đổi"


# --- Idempotent và dựng lại được ---------------------------------------------


async def test_legacy_recompute_is_idempotent(session_factory):
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    first = _content(await _rows(session_factory, CALCULATOR_LEGACY))
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    second = _content(await _rows(session_factory, CALCULATOR_LEGACY))

    assert first == second


async def test_domain_recompute_is_idempotent(session_factory):
    await _persist_domain(session_factory)
    first = _content(await _rows(session_factory, CALCULATOR_DOMAIN))
    await _persist_domain(session_factory)
    second = _content(await _rows(session_factory, CALCULATOR_DOMAIN))

    assert first == second


async def test_domain_rows_are_rebuildable_from_units_and_deals(session_factory):
    """Xoá sạch lineage miền rồi tính lại phải ra đúng nội dung cũ."""
    await _persist_domain(session_factory)
    before = _content(await _rows(session_factory, CALCULATOR_DOMAIN))

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.delete(absorption_daily).where(
                    absorption_daily.c.area_id == AREA_ID,
                    absorption_daily.c.calculator == CALCULATOR_DOMAIN,
                )
            )
    assert await _rows(session_factory, CALCULATOR_DOMAIN) == []

    await _persist_domain(session_factory)
    assert _content(await _rows(session_factory, CALCULATOR_DOMAIN)) == before


async def test_a_day_that_loses_its_data_disappears(session_factory):
    """Xoá-rồi-ghi chứ không upsert: ngày hết dữ liệu phải BIẾN MẤT."""
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    assert len(await _rows(session_factory, CALCULATOR_LEGACY)) == 2

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.delete(sales_records).where(
                    sales_records.c.area_id == AREA_ID, sales_records.c.sold_date == date(2026, 3, 2)
                )
            )

    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    remaining = await _rows(session_factory, CALCULATOR_LEGACY)
    assert [r["stat_date"] for r in remaining] == [date(2026, 3, 1)]


async def test_failed_recompute_preserves_the_previous_lineage(session_factory, monkeypatch):
    """Xoá và ghi nằm trong MỘT transaction: vỡ giữa chừng thì lineage cũ nguyên vẹn.

    Không có ranh giới này, một lần tính hỏng sẽ để lại bảng RỖNG cho lineage đó
    — dashboard mất số liệu vì một lỗi tạm thời.
    """
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    before = _content(await _rows(session_factory, CALCULATOR_LEGACY))
    assert before

    service = AbsorptionCalculatorService()
    boom = RuntimeError("hỏng ngay sau khi xoá, trước khi ghi")

    def explode(*args, **kwargs):
        raise boom

    monkeypatch.setattr(service, "_build_rows", explode)

    with pytest.raises(RuntimeError):
        await service.recompute(PROJECT_ID)

    assert _content(await _rows(session_factory, CALCULATOR_LEGACY)) == before, "lần tính hỏng đã xoá mất chuỗi cũ"


# --- Dashboard đọc đúng MỘT lineage ------------------------------------------


async def _set_project_calculator(session_factory, calculator: str) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("UPDATE projects SET absorption_calculator = :c WHERE id = :p"),
                {"c": calculator, "p": PROJECT_ID},
            )


async def test_default_project_reads_the_legacy_lineage(session_factory):
    """Hành vi mặc định KHÔNG đổi: dự án chưa cắt sang vẫn đọc bộ tính cũ."""
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    await _persist_domain(session_factory)

    points = await AreaService().absorption_series(AREA_ID)
    legacy = await _rows(session_factory, CALCULATOR_LEGACY)

    assert [p.stat_date for p in points] == [r["stat_date"] for r in legacy]
    assert [p.units_sold for p in points] == [r["units_sold"] for r in legacy]


async def test_series_returns_exactly_one_lineage_not_both(session_factory):
    """Không lọc thì mỗi ngày ra hai dòng và chuỗi hiển thị gấp đôi."""
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    await _persist_domain(session_factory)

    points = await AreaService().absorption_series(AREA_ID)
    dates = [p.stat_date for p in points]

    assert len(dates) == len(set(dates)), "chuỗi có ngày lặp — đang đọc cả hai lineage"


async def test_switching_the_project_switches_the_lineage(session_factory):
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    await _persist_domain(session_factory)

    legacy_points = await AreaService().absorption_series(AREA_ID)
    await _set_project_calculator(session_factory, CALCULATOR_DOMAIN)
    domain_points = await AreaService().absorption_series(AREA_ID)

    domain_rows = await _rows(session_factory, CALCULATOR_DOMAIN)
    assert [p.units_sold for p in domain_points] == [r["units_sold"] for r in domain_rows]
    assert [p.units_sold for p in legacy_points] != [p.units_sold for p in domain_points], (
        "hai lineage phải cho số khác nhau, nếu không test không chứng minh được gì"
    )


async def test_summary_and_series_use_the_same_lineage(session_factory):
    """Thẻ số liệu và biểu đồ trên cùng màn hình không được nói hai điều khác nhau."""
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    await _persist_domain(session_factory)
    await _set_project_calculator(session_factory, CALCULATOR_DOMAIN)

    summary = await AreaService().summary(PROJECT_ID)
    domain_rows = await _rows(session_factory, CALCULATOR_DOMAIN)

    newest = max(domain_rows, key=lambda r: r["stat_date"])
    assert summary.avg_velocity_30d == newest["velocity_30d"]


async def test_summary_default_uses_legacy(session_factory):
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    await _persist_domain(session_factory)

    summary = await AreaService().summary(PROJECT_ID)
    legacy_rows = await _rows(session_factory, CALCULATOR_LEGACY)

    newest = max(legacy_rows, key=lambda r: r["stat_date"])
    assert summary.avg_velocity_30d == newest["velocity_30d"]
