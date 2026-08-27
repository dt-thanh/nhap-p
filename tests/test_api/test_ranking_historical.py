"""`GET /api/v1/ranking/historical` — điểm hấp thụ lịch sử cấp dự án.

Cùng khuôn fixture với `tests/test_api/test_ranking_endpoint.py`
(`truncate_all` + monkeypatch `get_session_factory` cho đúng module đọc), dữ
liệu riêng của file này: dựng bằng UPDATE thật lên `units`/`deals` với
`source_updated_at` khai rõ, đi qua ĐÚNG trigger 0030 — không ghi thẳng vào
`unit_status_history`/`deal_status_history`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.main import app
from src.models.tables import areas, deals, projects, units
from tests.conftest import DASHBOARD_ADMIN_TOKEN, DASHBOARD_VIEWER_TOKEN, db_skip_reason

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

API = "/api/v1/ranking/historical"
PROJECT = "P-HIST-TEST-1"
INSTANCE = "crm-hist-test"
ADMIN_HEADER = {"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}
VIEWER_HEADER = {"Authorization": f"Bearer {DASHBOARD_VIEWER_TOKEN}"}


@pytest_asyncio.fixture
async def http(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    monkeypatch.setattr("src.api.ranking.get_session_factory", lambda factory=factory: factory, raising=False)

    project_id = uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(projects).values(
                    id=project_id, name="Hist", launch_date=date(2020, 1, 1),
                    created_at=datetime.now(UTC), external_id=PROJECT,
                    source_system="mini_crm", source_instance_id=INSTANCE,
                )
            )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory  # type: ignore[attr-defined]
        client.project_id = project_id  # type: ignore[attr-defined]
        yield client


async def _seed_area(http) -> uuid.UUID:
    factory = http.session_factory
    area_id = uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(areas).values(
                    id=area_id, project_id=http.project_id, area_name="A1", unit_type="2PN", bedrooms=2,
                    area_sqm=75, total_units=10, created_at=datetime.now(UTC),
                )
            )
    return area_id


async def _insert_unit(http, area_id, *, external_id: str, born_at: datetime) -> uuid.UUID:
    factory = http.session_factory
    unit_id = uuid.uuid4()
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(units).values(
                    id=unit_id, source_system="mini_crm", source_instance_id=INSTANCE, external_unit_id=external_id,
                    area_id=area_id, unit_code=external_id, unit_type="2PN", status="available",
                    source_updated_at=born_at, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
                )
            )
    return unit_id


async def _sell_unit(http, unit_id, *, at: datetime) -> None:
    factory = http.session_factory
    async with factory() as session:
        async with session.begin():
            deal_id = uuid.uuid4()
            await session.execute(
                sa.insert(deals).values(
                    id=deal_id, source_system="mini_crm", source_instance_id=INSTANCE,
                    external_deal_id=f"D-{unit_id}", unit_id=unit_id, status="reserved", source_status="reserved",
                    reserved_at=at, source_updated_at=at, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
                )
            )
            await session.execute(
                sa.update(deals).where(deals.c.id == deal_id).values(
                    status="sold", source_status="sold", sold_at=at, source_updated_at=at, updated_at=datetime.now(UTC)
                )
            )
            await session.execute(
                sa.update(units).where(units.c.id == unit_id).values(
                    status="sold", source_updated_at=at, updated_at=datetime.now(UTC)
                )
            )


# --- Xác thực và phân quyền --------------------------------------------------


async def test_unauthenticated_read_is_rejected(http):
    response = await http.get(API, params={"external_project_id": PROJECT})
    assert response.status_code == 401


async def test_a_token_without_this_project_in_scope_is_403_not_404(http):
    response = await http.get(API, params={"external_project_id": PROJECT}, headers=VIEWER_HEADER)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


async def test_unknown_project_is_404(http):
    response = await http.get(API, params={"external_project_id": "KHONG-CO"}, headers=ADMIN_HEADER)
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"


async def test_as_of_date_in_the_future_is_400(http):
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = await http.get(
        API, params={"external_project_id": PROJECT, "as_of_date": future}, headers=ADMIN_HEADER
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "AS_OF_DATE_IN_FUTURE"


# --- Nội dung ------------------------------------------------------------


async def test_project_with_no_units_is_insufficient_history(http):
    response = await http.get(API, params={"external_project_id": PROJECT}, headers=ADMIN_HEADER)
    body = response.json()
    assert response.status_code == 200
    assert body["score"] is None
    assert body["confidence"] == "insufficient_history"


async def test_project_with_enough_history_returns_a_score(http):
    area_id = await _seed_area(http)
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    unit_a = await _insert_unit(http, area_id, external_id="U-A", born_at=as_of - timedelta(days=200))
    unit_b = await _insert_unit(http, area_id, external_id="U-B", born_at=as_of - timedelta(days=200))
    await _sell_unit(http, unit_b, at=as_of - timedelta(days=10))

    response = await http.get(
        API, params={"external_project_id": PROJECT, "as_of_date": as_of.isoformat()}, headers=ADMIN_HEADER
    )
    body = response.json()
    assert response.status_code == 200
    assert body["score"] is not None
    assert 0 <= float(body["score"]) <= 1
    assert body["confidence"] in ("medium", "high")
    assert "absorption_30d_score" in body["components"]


async def test_repeated_calls_are_read_only_and_deterministic(http):
    """Gọi hai lần cùng `as_of_date` phải ra cùng điểm — hàm THUẦN, không ghi gì."""
    area_id = await _seed_area(http)
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    unit_a = await _insert_unit(http, area_id, external_id="U-A", born_at=as_of - timedelta(days=200))
    await _sell_unit(http, unit_a, at=as_of - timedelta(days=10))

    params = {"external_project_id": PROJECT, "as_of_date": as_of.isoformat()}
    first = (await http.get(API, params=params, headers=ADMIN_HEADER)).json()
    second = (await http.get(API, params=params, headers=ADMIN_HEADER)).json()
    assert first["score"] == second["score"]
    assert first["confidence"] == second["confidence"]
