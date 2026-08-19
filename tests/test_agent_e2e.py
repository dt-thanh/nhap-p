"""Phase 6: E2E cho `POST/GET /api/v1/agent/recommendations` (+ approve/reject).

Dữ liệu dựng THẲNG bằng SQL (không qua seed_dev — seed_dev không gán
`external_id`, và request/response của API này CHỈ nói external_id, khớp quy
ước FE toàn dự án). `ranking_configs` v1 do migration 0014 seed KHÔNG sống sót
qua `truncate_all` (xem `EXTRA_TRUNCATE_TABLES`, `tests/conftest.py`) nên mỗi
test tự chèn đúng bản config đó.

Bốn căn với kết quả tính TAY trước, rồi so với con số engine trả về — chứng
minh động cơ đọc dữ liệu THẬT từ `units`/`deals`/`areas`, không phải mô phỏng:

    area total_units=10, area_velocity_norm = min((1 sold/30d)/10/0.20, 1) = 0.5
    area_conversion_norm = 1 sold / 2 deal còn sống = 0.5
    u1 (available, không deal):        0.5*1 + 0.2*1 + 0.2*0.5 + 0.1*0.5 = 0.85
    u3 (available, deal reserved):     0.5*1 + 0.2*0 + 0.2*0.5 + 0.1*0.5 = 0.65
    u4 (sold, deal sold):              0.5*0 + 0.2*0 + 0.2*0.5 + 0.1*0.5 = 0.15

Chạy: `TEST_TARGET=tests/test_agent_e2e.py bash scripts/test_db.sh`
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.main import app
from src.models.tables import (
    areas,
    deals,
    projects,
    ranking_configs,
    ranking_runs,
    ranking_scores,
    sales_campaigns,
    units,
)
from tests.conftest import DASHBOARD_ADMIN_TOKEN, DASHBOARD_VIEWER_TOKEN, db_skip_reason

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

API = "/api/v1/agent"
VIEWER_HEADER = {"Authorization": f"Bearer {DASHBOARD_VIEWER_TOKEN}"}
ADMIN_HEADER = {"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}

SEED_WEIGHTS = {
    "unit_available": {
        "weight": "0.50",
        "direction": "positive",
        "missing_value_policy": "zero",
        "min_confidence": "0",
    },
    "has_active_deal": {
        "weight": "0.20",
        "direction": "negative",
        "missing_value_policy": "zero",
        "min_confidence": "0",
    },
    "area_velocity_norm": {
        "weight": "0.20",
        "direction": "positive",
        "missing_value_policy": "neutral",
        "min_confidence": "0",
    },
    "area_conversion_norm": {
        "weight": "0.10",
        "direction": "positive",
        "missing_value_policy": "neutral",
        "min_confidence": "0",
    },
}

PROJECT_ID = uuid.uuid4()
AREA_ID = uuid.uuid4()
UNIT_IDS = {f"u{i}": uuid.uuid4() for i in range(1, 6)}


@dataclass
class FakeLLM:
    """Thay `src.services.llm.get_llm()` — không gọi mạng thật trong test."""

    content: str = '{"summary": "Ưu tiên các căn còn trống ở phân khu có tốc độ bán tốt.", "recommended_actions": []}'
    calls: list = field(default_factory=list)

    async def ainvoke(self, prompt: str):
        self.calls.append(prompt)

        class _Result:
            content = self.content

        return _Result()


async def _insert_config(session_factory, *, published: bool = True) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            sa.insert(ranking_configs).values(
                id=uuid.uuid4(),
                version=1,
                status="published" if published else "draft",
                weights=SEED_WEIGHTS,
                min_weight_coverage=Decimal("0.5"),
                note="test v1",
                created_by="test",
                created_at=now,
                published_by="test" if published else None,
                published_at=now if published else None,
            )
        )
        await session.commit()


async def _insert_dataset(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            sa.insert(projects).values(
                id=PROJECT_ID,
                name="Test Project",
                launch_date=date(2026, 1, 1),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id="P-AGENT-TEST-1",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(areas).values(
                id=AREA_ID,
                project_id=PROJECT_ID,
                area_name="Tower A",
                unit_type="2PN",
                bedrooms=2,
                area_sqm=Decimal("60"),
                total_units=10,
                created_at=now,
                external_id="A-AGENT-TEST-1",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )

        unit_status = {"u1": "available", "u2": "available", "u3": "available", "u4": "sold", "u5": "available"}
        for key, unit_id in UNIT_IDS.items():
            await session.execute(
                sa.insert(units).values(
                    id=unit_id,
                    source_system="mini_crm",
                    source_instance_id="test",
                    external_unit_id=key,
                    area_id=AREA_ID,
                    unit_code=key,
                    unit_type="2PN",
                    status=unit_status[key],
                    created_at=now,
                    updated_at=now,
                )
            )

        # u3: deal 'reserved' (còn sống, không sold) ; u4: deal 'sold' trong 30 ngày qua
        await session.execute(
            sa.insert(deals).values(
                id=uuid.uuid4(),
                source_system="mini_crm",
                source_instance_id="test",
                external_deal_id="d-u3",
                unit_id=UNIT_IDS["u3"],
                status="reserved",
                source_status="reserved",
                reserved_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            sa.insert(deals).values(
                id=uuid.uuid4(),
                source_system="mini_crm",
                source_instance_id="test",
                external_deal_id="d-u4",
                unit_id=UNIT_IDS["u4"],
                status="sold",
                source_status="sold",
                sold_at=now - timedelta(days=5),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def http(truncate_all, monkeypatch):
    engine = truncate_all
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for target in (
        "src.api.agent.get_session_factory",
        "src.ranking.service.get_session_factory",
        "src.services.absorption.get_session_factory",
        "src.api.dashboard.get_session_factory",
    ):
        monkeypatch.setattr(target, lambda factory=factory: factory, raising=False)

    fake_llm = FakeLLM()
    monkeypatch.setattr("src.agents.nodes.ranking_node.get_llm", lambda: fake_llm)

    await _insert_config(factory)
    await _insert_dataset(factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory  # type: ignore[attr-defined]
        client.fake_llm = fake_llm  # type: ignore[attr-defined]
        yield client


async def test_unauthenticated_call_is_rejected(http):
    response = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"})
    assert response.status_code == 401


async def test_authenticated_call_creates_a_pending_recommendation(http):
    response = await http.post(
        f"{API}/recommendations",
        json={"project_id": "P-AGENT-TEST-1", "area_id": "A-AGENT-TEST-1"},
        headers=ADMIN_HEADER,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["project_id"] == "P-AGENT-TEST-1"
    assert body["area_id"] == "A-AGENT-TEST-1"
    assert body["decided_by"] is None
    assert body["decided_at"] is None
    assert uuid.UUID(body["recommendation_id"])
    assert uuid.UUID(body["ranking_run_id"])


async def test_engine_reads_real_db_data_not_simulated(http):
    """`ranking_scores` phải khớp CON SỐ TÍNH TAY từ dữ liệu units/deals đã chèn
    — bằng chứng trực tiếp rằng động cơ đọc DB thật, không phải giả lập."""
    response = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    assert response.status_code == 202
    run_id = uuid.UUID(response.json()["ranking_run_id"])

    async with http.session_factory() as session:
        rows = (
            await session.execute(
                sa.select(ranking_scores.c.unit_id, ranking_scores.c.score, ranking_scores.c.rank_in_project).where(
                    ranking_scores.c.project_id == PROJECT_ID
                )
            )
        ).all()
        run_row = (await session.execute(sa.select(ranking_runs).where(ranking_runs.c.id == run_id))).mappings().first()

    scores_by_unit = {str(r.unit_id): r.score for r in rows}
    assert scores_by_unit[str(UNIT_IDS["u1"])] == Decimal("0.8500")
    assert scores_by_unit[str(UNIT_IDS["u3"])] == Decimal("0.6500")
    assert scores_by_unit[str(UNIT_IDS["u4"])] == Decimal("0.1500")
    assert run_row["status"] == "completed"
    assert run_row["units_ranked"] == 5

    # O prompt gửi cho LLM phải chứa unit_id thật, không phải văn bản bịa.
    assert any(str(UNIT_IDS["u1"]) in call for call in http.fake_llm.calls)


async def test_get_recommendation_returns_correct_data(http):
    create = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = create.json()["recommendation_id"]

    response = await http.get(f"{API}/recommendations/{rec_id}", headers=ADMIN_HEADER)
    assert response.status_code == 200
    body = response.json()
    assert body["recommendation_id"] == rec_id
    assert body["status"] == "pending_approval"
    assert body["summary"]


async def test_get_unknown_recommendation_is_404(http):
    response = await http.get(f"{API}/recommendations/{uuid.uuid4()}", headers=ADMIN_HEADER)
    assert response.status_code == 404


async def test_viewer_role_cannot_approve(http):
    create = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = create.json()["recommendation_id"]

    response = await http.post(
        f"{API}/recommendations/{rec_id}/approve", json={"actor": "sales-lead", "reason": "ok"}, headers=VIEWER_HEADER
    )
    assert response.status_code == 403


async def test_approve_sets_status_and_records_actor(http):
    create = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = create.json()["recommendation_id"]

    response = await http.post(
        f"{API}/recommendations/{rec_id}/approve",
        json={"actor": "sales-lead", "reason": "Khớp dữ liệu thực tế"},
        headers=ADMIN_HEADER,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == "sales-lead"
    assert body["decision_reason"] == "Khớp dữ liệu thực tế"
    assert body["decided_at"] is not None

    reread = await http.get(f"{API}/recommendations/{rec_id}", headers=ADMIN_HEADER)
    assert reread.json()["status"] == "approved"


async def test_reject_sets_status_and_records_reason(http):
    create = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = create.json()["recommendation_id"]

    response = await http.post(
        f"{API}/recommendations/{rec_id}/reject",
        json={"actor": "sales-lead", "reason": "Không phù hợp chiến dịch hiện tại"},
        headers=ADMIN_HEADER,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_cannot_decide_an_already_decided_recommendation(http):
    create = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = create.json()["recommendation_id"]
    await http.post(f"{API}/recommendations/{rec_id}/approve", json={"actor": "a", "reason": "r"}, headers=ADMIN_HEADER)

    second = await http.post(
        f"{API}/recommendations/{rec_id}/reject", json={"actor": "b", "reason": "r2"}, headers=ADMIN_HEADER
    )
    assert second.status_code == 409


async def test_unknown_project_is_404(http):
    response = await http.post(f"{API}/recommendations", json={"project_id": "P-DOES-NOT-EXIST"}, headers=ADMIN_HEADER)
    assert response.status_code == 404


async def test_no_active_config_returns_503(http):
    async with http.session_factory() as session:
        await session.execute(sa.delete(ranking_configs))
        await session.commit()

    response = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "NO_ACTIVE_CONFIG"


async def test_pending_recommendation_cannot_be_executed(http):
    created = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = created.json()["recommendation_id"]

    response = await http.post(
        f"{API}/recommendations/{rec_id}/execute", json={"actor": "admin", "confirmed": True}, headers=ADMIN_HEADER
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "APPROVAL_REQUIRED"


async def test_approved_recommendation_creates_campaign_without_mutating_crm_units(http):
    created = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = created.json()["recommendation_id"]
    await http.post(
        f"{API}/recommendations/{rec_id}/approve", json={"actor": "sales-lead", "reason": "ok"}, headers=ADMIN_HEADER
    )

    response = await http.post(
        f"{API}/recommendations/{rec_id}/execute", json={"actor": "admin", "confirmed": True}, headers=ADMIN_HEADER
    )

    assert response.status_code == 200
    assert response.json()["status"] == "executed"
    assert response.json()["result"]["unit_count"] == 4
    async with http.session_factory() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(sales_campaigns)) == 1
        statuses = dict((await session.execute(sa.select(units.c.unit_code, units.c.status))).all())
    assert statuses == {"u1": "available", "u2": "available", "u3": "available", "u4": "sold", "u5": "available"}


async def test_execution_is_idempotent(http):
    created = await http.post(f"{API}/recommendations", json={"project_id": "P-AGENT-TEST-1"}, headers=ADMIN_HEADER)
    rec_id = created.json()["recommendation_id"]
    await http.post(
        f"{API}/recommendations/{rec_id}/approve", json={"actor": "sales-lead", "reason": "ok"}, headers=ADMIN_HEADER
    )
    payload = {"actor": "admin", "confirmed": True}
    first = await http.post(f"{API}/recommendations/{rec_id}/execute", json=payload, headers=ADMIN_HEADER)
    second = await http.post(f"{API}/recommendations/{rec_id}/execute", json=payload, headers=ADMIN_HEADER)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["error_code"] == "ALREADY_EXECUTED"
