"""absorption_rate_at_cutoff / cancellation_adjusted_absorption / historical_ranking_score.

Ba hàm gấp `unit_status_history`/`deal_status_history` (0028/0029) tại một
cutoff lịch sử. Kịch bản dựng bằng UPDATE thật lên `units`/`deals` với
`source_updated_at` khai rõ — đi qua ĐÚNG trigger 0030 phát sinh sự kiện, không
ghi thẳng vào bảng lịch sử, để test trung thực với đường thật.
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

from src.models.tables import areas, deal_status_history, deals, projects, unit_status_history, units
from src.services.domain_absorption import (
    absorption_rate_at_cutoff,
    cancellation_adjusted_absorption,
    historical_ranking_score,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DATABASE_URL, reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    ),
]

PROJECT_ID = uuid.UUID("22223333-4444-5555-6666-777788889999")
INSTANCE = "ranking-test-instance"
# Cực xa trong quá khứ — bảo đảm KHÔNG BAO GIỜ có sẵn (kể cả nếu module test
# khác để lại rác trong cùng DB dùng chung `absorption_test`).
FAR_PAST = datetime(1990, 1, 1, tzinfo=UTC)


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
            # unit_status_history/deal_status_history dọn qua CASCADE của
            # units/deals (append-only — không DELETE trực tiếp được).
            await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id).where(units.c.area_id.in_(area_ids)))))
            await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
            await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
            await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})
            await session.execute(
                sa.insert(projects).values(id=PROJECT_ID, name="Ranking", launch_date=date(2020, 1, 1), created_at=datetime.now(UTC))
            )
    yield


async def _seed_area(session) -> uuid.UUID:
    area_id = uuid.uuid4()
    await session.execute(
        sa.insert(areas).values(
            id=area_id, project_id=PROJECT_ID, area_name="A1", unit_type="2PN", bedrooms=2, area_sqm=75,
            total_units=100, created_at=datetime.now(UTC),
        )
    )
    return area_id


async def _insert_unit(session, area_id, *, external_id: str, born_at: datetime, status: str = "available") -> uuid.UUID:
    unit_id = uuid.uuid4()
    await session.execute(
        sa.insert(units).values(
            id=unit_id, source_system="mini_crm", source_instance_id=INSTANCE, external_unit_id=external_id,
            area_id=area_id, unit_code=external_id, unit_type="2PN", status=status,
            source_updated_at=born_at, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
    )
    return unit_id


async def _update_unit_status(session, unit_id, *, status: str, at: datetime) -> None:
    await session.execute(
        sa.update(units).where(units.c.id == unit_id).values(status=status, source_updated_at=at, updated_at=datetime.now(UTC))
    )


async def _insert_deal(session, unit_id, *, external_id: str, status: str, at: datetime) -> uuid.UUID:
    deal_id = uuid.uuid4()
    stamps = {"reserved": "reserved_at", "sold": "sold_at", "lost": "lost_at"}
    values = {
        "id": deal_id, "source_system": "mini_crm", "source_instance_id": INSTANCE, "external_deal_id": external_id,
        "unit_id": unit_id, "status": status, "source_status": status, "source_updated_at": at,
        "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC),
    }
    if status in stamps:
        values[stamps[status]] = at
    await session.execute(sa.insert(deals).values(**values))
    return deal_id


async def _update_deal_status(session, deal_id, *, status: str, at: datetime) -> None:
    stamps = {"reserved": "reserved_at", "sold": "sold_at", "lost": "lost_at"}
    values = {"status": status, "source_status": status, "source_updated_at": at, "updated_at": datetime.now(UTC)}
    if status in stamps:
        values[stamps[status]] = at
    await session.execute(sa.update(deals).where(deals.c.id == deal_id).values(**values))


async def test_absorption_rate_at_cutoff_insufficient_history(session_factory):
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            await _insert_unit(session, area_id, external_id="U-1", born_at=datetime.now(UTC))

        async with session.begin():
            # Cutoff cực xa trong quá khứ: window_start chắc chắn trước MỌI
            # dòng unit_status_history đang có, kể cả dữ liệu module khác.
            result = await absorption_rate_at_cutoff(session, area_id, FAR_PAST + timedelta(days=30), window_days=30)

    assert result is None


async def test_absorption_rate_at_cutoff_valid_window(session_factory):
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            born = datetime(2020, 1, 1, tzinfo=UTC)
            await _insert_unit(session, area_id, external_id="U-1", born_at=born)

        async with session.begin():
            result = await absorption_rate_at_cutoff(session, area_id, datetime(2026, 1, 31, tzinfo=UTC), window_days=30)

    assert result is not None
    assert result == 0  # unit chưa từng bán -> 0 căn bán / 1 sellable = 0


async def test_absorption_rate_at_cutoff_matches_manual_calculation(session_factory):
    """2 unit sellable tại đầu kỳ, 1 unit bán trong kỳ -> 1/2 = 0.5000, tính tay được."""
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            born = datetime(2020, 1, 1, tzinfo=UTC)
            unit_a = await _insert_unit(session, area_id, external_id="U-A", born_at=born)
            unit_b = await _insert_unit(session, area_id, external_id="U-B", born_at=born)
            deal_b = await _insert_deal(session, unit_b, external_id="D-B", status="reserved", at=datetime(2026, 1, 10, tzinfo=UTC))

        async with session.begin():
            await _update_deal_status(session, deal_b, status="sold", at=datetime(2026, 1, 15, tzinfo=UTC))
            await _update_unit_status(session, unit_b, status="sold", at=datetime(2026, 1, 15, tzinfo=UTC))

        async with session.begin():
            # window = [2026-01-01, 2026-01-31); cả 2 unit 'available' tại
            # 2026-01-01 (đầu kỳ) -> denominator=2. Unit B bán 2026-01-15 (trong
            # kỳ) -> sold=1. 1/2 = 0.5000.
            result = await absorption_rate_at_cutoff(session, area_id, datetime(2026, 1, 31, tzinfo=UTC), window_days=30)

    assert result == pytest.approx(0.5, abs=1e-9)


async def test_cancellation_adjusted_absorption_distinguishes_leads(session_factory):
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            born = datetime(2020, 1, 1, tzinfo=UTC)
            unit_lead = await _insert_unit(session, area_id, external_id="U-LEAD", born_at=born)
            unit_cancel = await _insert_unit(session, area_id, external_id="U-CANCEL", born_at=born)
            lead_deal = await _insert_deal(session, unit_lead, external_id="D-LEAD", status="lead", at=datetime(2026, 2, 1, tzinfo=UTC))
            cancel_deal = await _insert_deal(session, unit_cancel, external_id="D-CANCEL", status="reserved", at=datetime(2026, 2, 1, tzinfo=UTC))

        async with session.begin():
            await _update_deal_status(session, lead_deal, status="lost", at=datetime(2026, 2, 10, tzinfo=UTC))
            await _update_deal_status(session, cancel_deal, status="lost", at=datetime(2026, 2, 10, tzinfo=UTC))

        async with session.begin():
            result = await cancellation_adjusted_absorption(
                session, area_id, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)
            )

    assert result["cancelled"] == 1  # chỉ U-CANCEL (đã reserved rồi mới lost) được đếm, U-LEAD (lead->lost) thì không
    assert result["confidence"] == "observed"


async def test_cancellation_adjusted_absorption_lower_bound_label(session_factory):
    """Có sự kiện `source='backfill_replay'` trong cửa sổ -> confidence='lower_bound'."""
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            born = datetime(2020, 1, 1, tzinfo=UTC)
            unit_id = await _insert_unit(session, area_id, external_id="U-1", born_at=born)
            deal_id = await _insert_deal(session, unit_id, external_id="D-1", status="reserved", at=datetime(2026, 2, 1, tzinfo=UTC))
            # Sự kiện backfill được ghi TRỰC TIẾP (INSERT, không UPDATE — append-only
            # cho phép INSERT) để mô phỏng dữ liệu phát lại từ sync_payloads.
            await session.execute(
                sa.insert(deal_status_history).values(
                    id=uuid.uuid4(), deal_id=deal_id, unit_id=unit_id, old_status="reserved", new_status="sold",
                    prior_status_was_holding=True, new_status_is_holding=True,
                    changed_at=datetime(2026, 2, 15, tzinfo=UTC), source="backfill_replay", metadata_json={},
                )
            )

        async with session.begin():
            result = await cancellation_adjusted_absorption(
                session, area_id, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)
            )

    assert result["confidence"] == "lower_bound"
    assert result["gross_sold"] == 1


async def test_historical_ranking_score_composite(session_factory):
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            born = datetime(2020, 1, 1, tzinfo=UTC)
            units_ids = [await _insert_unit(session, area_id, external_id=f"U-{i}", born_at=born) for i in range(10)]
            # Bán 3/10 trong 90 ngày gần nhất so với as_of.
            as_of = datetime(2026, 6, 1, tzinfo=UTC)
            for i, unit_id in enumerate(units_ids[:3]):
                deal_id = await _insert_deal(session, unit_id, external_id=f"D-{i}", status="reserved", at=as_of - timedelta(days=60))

        async with session.begin():
            for i, unit_id in enumerate(units_ids[:3]):
                deals_now = (await session.execute(sa.select(deals.c.id).where(deals.c.unit_id == unit_id))).scalar_one()
                await _update_deal_status(session, deals_now, status="sold", at=as_of - timedelta(days=50))

        async with session.begin():
            result = await historical_ranking_score(session, PROJECT_ID, as_of_date=as_of)

    assert result["score"] is not None
    assert 0 <= result["score"] <= 1
    assert result["confidence"] in ("high", "medium")


async def test_ranking_score_excludes_seed_data(session_factory):
    """Sự kiện `source='seed'` không được góp vào tử số/mẫu số — dữ liệu tổng
    hợp không phải quan sát CRM thật (xem docstring `_sellable_inventory_at`)."""
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            as_of = datetime(2026, 6, 1, tzinfo=UTC)
            unit_id = await _insert_unit(session, area_id, external_id="U-SEED", born_at=datetime(2020, 1, 1, tzinfo=UTC))
            deal_id = await _insert_deal(session, unit_id, external_id="D-SEED", status="reserved", at=as_of - timedelta(days=60))
            # Ghi thẳng một sự kiện 'sold' gắn nhãn seed — mô phỏng một lần
            # reseed giả định chạy SAU khi 0030 đã tồn tại (kịch bản không xảy
            # ra với 0019/0021/0023 hiện tại, nhưng bộ tính phải chịu được nó).
            await session.execute(
                sa.insert(deal_status_history).values(
                    id=uuid.uuid4(), deal_id=deal_id, unit_id=unit_id, old_status="reserved", new_status="sold",
                    prior_status_was_holding=True, new_status_is_holding=True,
                    changed_at=as_of - timedelta(days=50), source="seed", metadata_json={},
                )
            )

        async with session.begin():
            result = await historical_ranking_score(session, PROJECT_ID, as_of_date=as_of)

    # Không có sự kiện THẬT nào (chỉ có 'seed') -> không đủ lịch sử tính được
    # absorption_30d_score với đúng bằng chứng đã lọc source != 'seed'.
    assert result["rank_metadata"].get("sold_90d", 0) == 0


async def test_ranking_score_confidence_medium_with_30d_history(session_factory):
    """Chỉ có 30 ngày lịch sử thật (chưa đủ 90) -> absorption_90d_score bị loại,
    confidence='medium', không phải 'high'."""
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            as_of = datetime(2026, 6, 1, tzinfo=UTC)
            # Capture sớm nhất trong TOÀN DB chỉ 20 ngày trước as_of -> đủ cho
            # cửa sổ 30 ngày (window_start=as_of-30d < floor_ts thật ra sẽ
            # KHÔNG đủ nếu floor_ts=as_of-20d... sửa: sinh đúng để 30d đủ, 90d thiếu.
            unit_id = await _insert_unit(session, area_id, external_id="U-1", born_at=as_of - timedelta(days=40))

        async with session.begin():
            result = await historical_ranking_score(session, PROJECT_ID, as_of_date=as_of)

    assert "absorption_90d_score" in result["excluded_factors"]
    assert result["confidence"] == "medium"


async def test_ranking_score_excluded_factors_documented(session_factory):
    async with session_factory() as session:
        async with session.begin():
            area_id = await _seed_area(session)
            as_of = datetime(2026, 6, 1, tzinfo=UTC)
            await _insert_unit(session, area_id, external_id="U-1", born_at=as_of - timedelta(days=40))

        async with session.begin():
            result = await historical_ranking_score(session, PROJECT_ID, as_of_date=as_of)

    assert isinstance(result["excluded_factors"], list)
    assert all(isinstance(name, str) for name in result["excluded_factors"])
