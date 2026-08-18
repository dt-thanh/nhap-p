"""Endpoint chạy song song: ghi một lần đo, đọc lịch sử — và ai được phép.

Đây là dữ liệu PHÂN TÍCH nội bộ theo từng dự án, nên phần lớn test ở đây kiểm
việc TỪ CHỐI. Chốt giống hệt đầu dò ở Phase 8A và dùng chung đúng một hàm
`require_ops_token`: hai bản sao của quy tắc "ai được đọc dữ liệu nội bộ" sẽ lệch
nhau, và bản lỏng hơn là bản sẽ tồn tại.

Chạy: TEST_TARGET=tests/test_api/test_parallel_run_endpoint.py bash scripts/test_db.sh
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

TOKEN = "synthetic-ops-token-parallel-run-0123456789"
PROJECT_ID = uuid.UUID("a3b4c5d6-e7f8-496a-8b7c-def012345b3a")
AREA_ID = uuid.UUID("a3b4c5d6-e7f8-496a-8b7c-def012345b3b")
INSTANCE = "crm-parallel-api"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _url(project_id=PROJECT_ID) -> str:
    return f"/api/v1/parallel-run/{project_id}"


@pytest.fixture(autouse=True)
def db_env(monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()
    yield
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


@pytest.fixture
def token_configured(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("OPS_API_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def token_unset(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("OPS_API_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def seeded():
    """Dự án + phân khu + hai căn đã bán — đủ để bộ tính miền có dữ liệu."""
    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))

    def wipe(conn):
        conn.execute(sa.text("DELETE FROM calculator_comparisons WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM deals WHERE source_instance_id = :i"), {"i": INSTANCE})
        conn.execute(sa.text("DELETE FROM units WHERE source_instance_id = :i"), {"i": INSTANCE})
        conn.execute(sa.text("DELETE FROM absorption_daily WHERE area_id = :a"), {"a": AREA_ID})
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with engine.begin() as conn:
        wipe(conn)
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'PRAPI', :d, now())"),
            {"p": PROJECT_ID, "d": date(2026, 1, 1)},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:a, :p, 'A1', '2PN', 2, 75, 20, now())"
            ),
            {"a": AREA_ID, "p": PROJECT_ID},
        )
        for index in range(2):
            unit_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, "
                    "unit_code, unit_type, status, created_at, updated_at) "
                    "VALUES (:i, 'mini_crm', :inst, :ext, :a, :code, '2PN', 'sold', now(), now())"
                ),
                {"i": unit_id, "inst": INSTANCE, "ext": f"API-U-{index}", "a": AREA_ID, "code": f"API-{index}"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, "
                    "status, source_status, sold_at, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'mini_crm', :inst, :ext, :u, 'sold', 'sold', :sold, now(), now())"
                ),
                {
                    "inst": INSTANCE,
                    "ext": f"API-D-{index}",
                    "u": unit_id,
                    "sold": datetime(2026, 3, 1 + index, tzinfo=UTC),
                },
            )
    yield
    with engine.begin() as conn:
        wipe(conn)
    engine.dispose()


# --- Từ chối ------------------------------------------------------------------


async def test_capture_is_disabled_when_no_token_is_configured(client, token_unset):
    response = await client.post(_url())

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "OPS_ENDPOINT_DISABLED"


async def test_history_is_disabled_when_no_token_is_configured(client, token_unset):
    assert (await client.get(_url())).status_code == 503


async def test_a_wrong_token_is_rejected_on_both_verbs(client, token_configured):
    for call in (
        client.post(_url(), headers={"X-Ops-Token": "sai"}),
        client.get(_url(), headers={"X-Ops-Token": "sai"}),
    ):
        assert (await call).status_code == 401


async def test_a_rejected_request_leaks_no_project_data(client, token_configured):
    response = await client.get(_url())

    assert response.status_code == 401
    assert str(PROJECT_ID) not in response.text
    assert "PRAPI" not in response.text


async def test_an_unauthorised_capture_writes_nothing(client, token_configured):
    """`POST` bị từ chối thì tuyệt đối không được để lại dòng nào."""
    await client.post(_url())

    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))
    with engine.connect() as conn:
        count = conn.scalar(
            sa.text("SELECT count(*) FROM calculator_comparisons WHERE project_id = :p"), {"p": PROJECT_ID}
        )
    engine.dispose()
    assert count == 0


# --- Cho qua ------------------------------------------------------------------


async def test_capture_records_a_comparison(client, token_configured):
    response = await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == str(PROJECT_ID)
    assert body["trigger"] == "manual"
    assert body["domain_has_data"] is True


async def test_capture_leaves_absorption_daily_alone(client, token_configured):
    """Bất biến của cả sub-phase, kiểm qua đúng đường mà người dùng sẽ gọi."""
    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))

    def digest():
        with engine.connect() as conn:
            return [
                tuple(r)
                for r in conn.execute(
                    sa.text(
                        "SELECT area_id, stat_date, units_sold, units_remaining, units_reserved, calculator, "
                        "computation_id FROM absorption_daily ORDER BY area_id, stat_date, calculator"
                    )
                ).all()
            ]

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, units_remaining, "
                "velocity_7d, velocity_30d, data_quality_status, is_observed, computed_at, calculator, "
                "computation_id) VALUES (gen_random_uuid(), :a, '2026-03-01', 2, 18, 1.0, 1.0, 'ok', true, "
                "now(), 'legacy_aggregate', gen_random_uuid())"
            ),
            {"a": AREA_ID},
        )
    before = digest()

    await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    assert digest() == before
    engine.dispose()


async def test_history_returns_newest_first(client, token_configured):
    await client.post(_url(), headers={"X-Ops-Token": TOKEN})
    second = await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    response = await client.get(_url(), headers={"X-Ops-Token": TOKEN})

    body = response.json()
    assert body["total"] == 2
    assert body["comparisons"][0]["comparison_id"] == second.json()["comparison_id"]


async def test_history_never_overwrites(client, token_configured):
    for _ in range(3):
        await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    body = (await client.get(_url(), headers={"X-Ops-Token": TOKEN})).json()

    assert body["total"] == 3
    assert len({c["comparison_id"] for c in body["comparisons"]}) == 3


async def test_every_history_response_carries_the_disclaimer(client, token_configured):
    """Một dòng `matches: true` tách khỏi ngữ cảnh sẽ được đọc thành "sẵn sàng cắt sang"."""
    body = (await client.get(_url(), headers={"X-Ops-Token": TOKEN})).json()

    assert "KHÔNG phải bằng chứng" in body["disclaimer"]
    assert "TUẦN TỰ" in body["disclaimer"]


async def test_gate_eligible_mode_reads_the_view(client, token_configured):
    """Chế độ mà cổng cắt sang BẮT BUỘC dùng — dòng không có dữ liệu miền bị loại
    ở tầng database."""
    await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    everything = (await client.get(_url(), headers={"X-Ops-Token": TOKEN})).json()
    gated = (await client.get(_url(), params={"gate_eligible_only": True}, headers={"X-Ops-Token": TOKEN})).json()

    assert everything["total"] == gated["total"] == 1, "dự án này CÓ dữ liệu miền nên nó phải qua được cổng"
    assert gated["gate_eligible_only"] is True


async def test_an_unknown_project_is_404(client, token_configured):
    response = await client.post(_url(uuid.uuid4()), headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "UNKNOWN_PROJECT"


async def test_a_malformed_project_id_is_422(client, token_configured):
    response = await client.get("/api/v1/parallel-run/khong-phai-uuid", headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 422


async def test_capture_does_not_change_the_calculator_flag(client, token_configured):
    await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))
    with engine.connect() as conn:
        flags = set(conn.execute(sa.text("SELECT DISTINCT absorption_calculator FROM projects")).scalars())
    engine.dispose()
    assert flags == {"legacy_aggregate"}


# --- Phán quyết phân loại (Phase 8E) -----------------------------------------


def _verdict_url(project_id=PROJECT_ID) -> str:
    return f"/api/v1/parallel-run/{project_id}/verdicts"


async def test_verdicts_are_protected_like_everything_else(client, token_unset):
    assert (await client.get(_verdict_url())).status_code == 503


async def test_verdicts_reject_a_wrong_token(client, token_configured):
    assert (await client.get(_verdict_url(), headers={"X-Ops-Token": "sai"})).status_code == 401


async def test_verdicts_classify_the_captured_history(client, token_configured):
    """Dự án này có dữ liệu miền nhưng KHÔNG có dữ liệu tổng hợp cũ, nên phán quyết
    phải là `no_data` — và tuyệt đối không phải bằng chứng cắt sang."""
    await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    body = (await client.get(_verdict_url(), headers={"X-Ops-Token": TOKEN})).json()

    assert body["summary"]["comparisons"] == 1
    verdict = body["verdicts"][0]
    assert verdict["verdict"] == "no_data"
    assert verdict["is_cutover_evidence"] is False
    assert {d["classification"] for d in verdict["differences"]} == {"coverage"}


async def test_the_verdict_summary_counts_evidence_not_matches(client, token_configured):
    await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    summary = (await client.get(_verdict_url(), headers={"X-Ops-Token": TOKEN})).json()["summary"]

    assert summary["cutover_evidence_count"] == 0, "thiếu dữ liệu bên cũ thì không có bằng chứng nào"
    assert summary["no_data_count"] == 1
    assert set(summary["blocking_classes"]) == {"definition_drift", "anomaly", "unexplained"}


async def test_every_verdict_response_carries_its_own_disclaimer(client, token_configured):
    body = (await client.get(_verdict_url(), headers={"X-Ops-Token": TOKEN})).json()

    assert "KHÔNG có nghĩa là đã sẵn sàng cắt sang" in body["disclaimer"]


async def test_reading_verdicts_writes_nothing(client, token_configured):
    """Phân loại là hàm thuần — đọc phán quyết không được đổi lịch sử."""
    await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))
    with engine.connect() as conn:
        before = conn.scalar(sa.text("SELECT count(*) FROM calculator_comparisons"))
    await client.get(_verdict_url(), headers={"X-Ops-Token": TOKEN})
    await client.get(_verdict_url(), headers={"X-Ops-Token": TOKEN})
    with engine.connect() as conn:
        after = conn.scalar(sa.text("SELECT count(*) FROM calculator_comparisons"))
    engine.dispose()

    assert before == after


async def test_verdicts_honour_the_gate_view(client, token_configured):
    await client.post(_url(), headers={"X-Ops-Token": TOKEN})

    gated = (
        await client.get(_verdict_url(), params={"gate_eligible_only": True}, headers={"X-Ops-Token": TOKEN})
    ).json()

    assert gated["gate_eligible_only"] is True
    assert gated["summary"]["comparisons"] == 1, "dự án này CÓ dữ liệu miền nên nó qua được view cổng"


async def test_a_malformed_project_id_is_422_on_verdicts(client, token_configured):
    response = await client.get("/api/v1/parallel-run/khong-phai-uuid/verdicts", headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 422
