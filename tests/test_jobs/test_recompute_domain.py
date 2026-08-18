"""Job tính lại lineage miền: phạm vi, nguồn gốc, idempotency, và worker THẬT.

Hai nhóm test, phủ hai loại rủi ro khác nhau:

* **Gọi thẳng hàm** — kiểm nội dung ghi ra: đúng lineage, đúng phạm vi phân khu,
  không đụng dòng của bộ tính cũ.
* **Worker RQ thật** (file riêng) — kiểm rằng job ĐI QUA ĐƯỢC hàng đợi.

Phần worker RQ THẬT nằm ở `test_recompute_domain_worker.py`: worker của RQ cài
signal handler nên bắt buộc chạy ở main thread, không đặt chung với các test
async được.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.jobs.recompute_domain import run_domain_recompute as _run_domain_recompute
from src.models.tables import absorption_daily, areas, deals, sales_records, units, upload_files
from src.services.absorption import AbsorptionCalculatorService
from src.services.calculators import CALCULATOR_DOMAIN, CALCULATOR_LEGACY


async def run_domain_recompute(*args, **kwargs):
    """Chạy job trong thread riêng.

    `run_domain_recompute` là hàm ĐỒNG BỘ dành cho worker RQ và tự mở event loop
    bằng `asyncio.run`. Gọi thẳng từ một test async sẽ nổ "cannot be called from
    a running event loop". Thread riêng cho nó một loop sạch — đúng bối cảnh mà
    worker thật chạy nó.
    """
    return await asyncio.to_thread(_run_domain_recompute, *args, **kwargs)


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

PROJECT_ID = uuid.UUID("b2c3d4e5-f607-4819-a02b-3456789abd70")
AREA_A = uuid.UUID("b2c3d4e5-f607-4819-a02b-3456789abd71")
AREA_B = uuid.UUID("b2c3d4e5-f607-4819-a02b-3456789abd72")
FILE_ID = uuid.UUID("b2c3d4e5-f607-4819-a02b-3456789abd73")
INSTANCE = "synthetic-recompute-crm"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _add_unit(session, *, area_id, index, status="sold", sold_day=1, deleted=False):
    unit_id = uuid.uuid4()
    await session.execute(
        sa.insert(units).values(
            id=unit_id,
            source_system="mini_crm",
            source_instance_id=INSTANCE,
            external_unit_id=f"SYNTH-U-{area_id.hex[-4:]}-{index:03d}",
            area_id=area_id,
            unit_code=f"C-{area_id.hex[-4:]}-{index:03d}",
            unit_type="2PN",
            status=status,
            deleted_at=datetime.now(UTC) if deleted else None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    if status in ("sold", "reserved"):
        await session.execute(
            sa.insert(deals).values(
                id=uuid.uuid4(),
                source_system="mini_crm",
                source_instance_id=INSTANCE,
                external_deal_id=f"SYNTH-D-{area_id.hex[-4:]}-{index:03d}",
                unit_id=unit_id,
                status=status,
                source_status=status,
                reserved_at=datetime(2026, 2, 20, tzinfo=UTC),
                sold_at=datetime(2026, 3, sold_day, tzinfo=UTC) if status == "sold" else None,
                deleted_at=datetime.now(UTC) if deleted else None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    return unit_id


@pytest_asyncio.fixture(autouse=True)
async def seeded(session_factory, monkeypatch):
    """Dự án hai phân khu, mỗi phân khu có căn đã bán, cộng dữ liệu tổng hợp."""
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        area_ids = [AREA_A, AREA_B]
        await session.execute(sa.delete(deals).where(deals.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(units).where(units.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(absorption_daily).where(absorption_daily.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(sales_records).where(sales_records.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'RECOMP', :d, :t)"),
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
            await session.execute(
                sa.insert(upload_files).values(
                    id=FILE_ID,
                    project_id=PROJECT_ID,
                    uploaded_by=None,
                    filename="s.csv",
                    checksum="recomp",
                    status="completed",
                    rows_ok=0,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                )
            )
            # Dữ liệu tổng hợp để bộ tính CŨ có gì mà ghi.
            await session.execute(
                sa.insert(sales_records),
                [
                    {
                        "id": uuid.uuid4(),
                        "area_id": AREA_A,
                        "file_id": FILE_ID,
                        "sold_date": date(2026, 3, day),
                        "units_sold": day,
                        "external_record_id": f"RC-{day}",
                        "source_row_hash": f"hash-rc-{day}",
                        "created_at": datetime.now(UTC),
                    }
                    for day in (1, 2)
                ],
            )
            for index in range(3):
                await _add_unit(session, area_id=AREA_A, index=index, sold_day=1 + index % 2)
            for index in range(2):
                await _add_unit(session, area_id=AREA_B, index=index, sold_day=1)
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


async def _domain_rows(session_factory, area_id=None):
    condition = [absorption_daily.c.calculator == CALCULATOR_DOMAIN]
    condition.append(
        absorption_daily.c.area_id == area_id if area_id else absorption_daily.c.area_id.in_([AREA_A, AREA_B])
    )
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    sa.select(absorption_daily).where(*condition).order_by(absorption_daily.c.stat_date)
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


async def _legacy_digest(session_factory) -> str | None:
    async with session_factory() as session:
        return await session.scalar(
            sa.text(
                "SELECT md5(string_agg(a::text, '|' ORDER BY a::text)) FROM absorption_daily a "
                "WHERE a.calculator = :c AND a.area_id = ANY(:areas)"
            ).bindparams(c=CALCULATOR_LEGACY, areas=[AREA_A, AREA_B])
        )


def _content(rows):
    return [(r["area_id"], r["stat_date"], r["units_sold"], r["units_remaining"], r["units_reserved"]) for r in rows]


# --- Nội dung ghi ra ---------------------------------------------------------


async def test_job_writes_only_domain_rows(session_factory):
    await AbsorptionCalculatorService().recompute(PROJECT_ID)
    legacy_before = await _legacy_digest(session_factory)

    result = await run_domain_recompute(str(PROJECT_ID))

    assert result["status"] == "done"
    assert result["rows"] > 0
    rows = await _domain_rows(session_factory)
    assert rows
    assert all(r["calculator"] == CALCULATOR_DOMAIN for r in rows)
    assert await _legacy_digest(session_factory) == legacy_before, "job đã đụng vào dòng của bộ tính cũ"


async def test_job_stamps_provenance(session_factory):
    await run_domain_recompute(str(PROJECT_ID))
    rows = await _domain_rows(session_factory)

    assert all(r["units_reserved"] is not None for r in rows)
    assert len({r["computation_id"] for r in rows}) == 1, "một lần chạy phải có đúng một computation_id"


async def test_job_is_idempotent(session_factory):
    await run_domain_recompute(str(PROJECT_ID))
    first = _content(await _domain_rows(session_factory))
    await run_domain_recompute(str(PROJECT_ID))
    second = _content(await _domain_rows(session_factory))

    assert first == second


async def test_unknown_project_is_a_clean_no_op(session_factory):
    result = await run_domain_recompute(str(uuid.uuid4()))

    assert result["status"] == "done"
    assert result["rows"] == 0


# --- Phạm vi phân khu --------------------------------------------------------


async def test_recomputing_one_area_leaves_the_other_untouched(session_factory):
    """Yêu cầu 7: tính lại A1 không được đụng lineage của A2."""
    await run_domain_recompute(str(PROJECT_ID))
    area_b_before = _content(await _domain_rows(session_factory, AREA_B))
    b_computation = {r["computation_id"] for r in await _domain_rows(session_factory, AREA_B)}
    assert area_b_before

    await run_domain_recompute(str(PROJECT_ID), area_ids=[str(AREA_A)])

    after = await _domain_rows(session_factory, AREA_B)
    assert _content(after) == area_b_before
    assert {r["computation_id"] for r in after} == b_computation, (
        "A2 đã bị ghi lại dù không nằm trong phạm vi — computation_id đổi"
    )


async def test_scoped_recompute_still_updates_its_own_area(session_factory):
    await run_domain_recompute(str(PROJECT_ID), area_ids=[str(AREA_A)])
    rows = await _domain_rows(session_factory, AREA_A)
    assert rows


async def test_empty_area_ids_means_whole_project(session_factory):
    """Danh sách rỗng = "không thu hẹp được" → phải tính ĐẦY ĐỦ, không phải rỗng."""
    result = await run_domain_recompute(str(PROJECT_ID), area_ids=[])

    assert result["rows"] > 0
    assert {r["area_id"] for r in await _domain_rows(session_factory)} == {AREA_A, AREA_B}


async def test_tombstoning_the_last_live_unit_clears_that_area(session_factory):
    """Yêu cầu 6: căn cuối cùng của phân khu bị tombstone.

    Phân khu không còn căn sống thì không sinh point nào. Nếu phạm vi xoá chỉ suy
    ra từ points, dòng cũ sẽ nằm lại vĩnh viễn và tồn kho hiển thị mãi số của
    những căn đã biến mất — nên phạm vi phải đến từ `area_ids` truyền vào.
    """
    await run_domain_recompute(str(PROJECT_ID))
    assert await _domain_rows(session_factory, AREA_B), "cần có dòng cho A2 trước đã"

    async with session_factory() as session:
        async with session.begin():
            unit_ids = list(
                (
                    await session.execute(
                        sa.select(units.c.id).where(units.c.area_id == AREA_B, units.c.deleted_at.is_(None))
                    )
                ).scalars()
            )
            now = datetime.now(UTC)
            await session.execute(
                sa.update(deals).where(deals.c.unit_id.in_(unit_ids)).values(deleted_at=now, updated_at=now)
            )
            await session.execute(
                sa.update(units).where(units.c.id.in_(unit_ids)).values(deleted_at=now, updated_at=now)
            )

    # Phạm vi được truyền vào TƯỜNG MINH, đúng như SyncRunService làm: area_id thu
    # thập trước khi lọc bỏ bản ghi tombstone.
    await run_domain_recompute(str(PROJECT_ID), area_ids=[str(AREA_B)])

    assert await _domain_rows(session_factory, AREA_B) == [], "dòng của phân khu đã rỗng vẫn nằm lại"
    assert await _domain_rows(session_factory, AREA_A), "phân khu khác không được bị ảnh hưởng"
