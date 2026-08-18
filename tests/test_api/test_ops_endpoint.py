"""Endpoint vận hành phải ĐÓNG theo mặc định.

`GET /api/v1/ops/domain-recompute` trả về `project_id` nội bộ kèm mức độ lạc hậu
của lineage — đủ để dựng lại bức tranh bên trong hệ thống. Nên phần lớn test ở
đây kiểm việc TỪ CHỐI, không phải việc trả dữ liệu.

Điểm dễ hỏng nhất là trạng thái "chưa cấu hình": nếu token rỗng mà endpoint vẫn
trả dữ liệu thì nó sẽ lên môi trường thật ở trạng thái công khai, vì không ai
nhận ra có biến môi trường cần đặt.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

URL = "/api/v1/ops/domain-recompute"
TOKEN = "synthetic-ops-token-0123456789abcdef"
PROJECT_ID = uuid.UUID("f6071829-3a4b-4c5d-9e6f-789abcdeb201")


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


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

    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    monkeypatch.setenv("OPS_API_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def stale_project():
    """Một dự án lạc hậu thật, để test khẳng định được nội dung bị che giấu."""
    import json

    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))

    def wipe(conn):
        conn.execute(sa.text("DELETE FROM upload_files WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with engine.begin() as conn:
        wipe(conn)
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'OPSPROBE', :d, now())"),
            {"p": PROJECT_ID, "d": "2026-01-01"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, finished_at, "
                "transport_mode, source_instance_id, error_summary) VALUES (gen_random_uuid(), :p, 'completed', "
                "1, 0, now(), now(), 'api_push', 'synthetic-ops-crm', CAST(:s AS jsonb))"
            ),
            {"p": PROJECT_ID, "s": json.dumps({"projections": {"inserted": 1}})},
        )
    yield
    with engine.begin() as conn:
        wipe(conn)
    engine.dispose()


# --- Từ chối ------------------------------------------------------------------


async def test_disabled_when_token_is_not_configured(client, token_unset, stale_project):
    """Chưa cấu hình = ĐÓNG, không phải mở."""
    response = await client.get(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "OPS_ENDPOINT_DISABLED"


async def test_disabled_endpoint_leaks_nothing_even_with_a_token(client, token_unset, stale_project):
    response = await client.get(URL, headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 503
    assert str(PROJECT_ID) not in response.text


async def test_missing_token_is_rejected(client, token_configured, stale_project):
    response = await client.get(URL)

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "INVALID_OPS_TOKEN"


async def test_wrong_token_is_rejected(client, token_configured, stale_project):
    response = await client.get(URL, headers={"X-Ops-Token": "sai-token"})

    assert response.status_code == 401


async def test_rejected_response_never_contains_project_ids(client, token_configured, stale_project):
    """Yêu cầu chính của Phase 8A: không lộ chi tiết nội bộ khi chưa xác thực."""
    for headers in ({}, {"X-Ops-Token": "sai-token"}):
        response = await client.get(URL, headers=headers)

        assert response.status_code == 401
        assert str(PROJECT_ID) not in response.text
        assert "OPSPROBE" not in response.text


async def test_token_prefix_is_not_accepted(client, token_configured, stale_project):
    """`compare_digest` so sánh toàn bộ chuỗi, không phải tiền tố."""
    response = await client.get(URL, headers={"X-Ops-Token": TOKEN[:10]})

    assert response.status_code == 401


async def test_the_401_names_the_scheme_to_retry_with(client, token_configured):
    response = await client.get(URL)

    assert response.headers.get("WWW-Authenticate") == "X-Ops-Token"


# --- Cho qua ------------------------------------------------------------------


async def test_authorized_probe_reports_the_stale_project(client, token_configured, stale_project):
    response = await client.get(URL, headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    ours = [p for p in body["projects"] if p["project_id"] == str(PROJECT_ID)]
    assert len(ours) == 1
    assert ours[0]["never_computed"] is True
    assert body["stale_projects"] >= 1


async def test_probe_returns_200_even_when_degraded(client, token_configured, stale_project):
    """Trả 503 khi có dự án lạc hậu sẽ khiến mọi công cụ uptime báo 'API sập'."""
    response = await client.get(URL, headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 200


async def test_probe_enqueues_nothing(client, token_configured, stale_project, monkeypatch):
    """GET không được đổi trạng thái: công cụ giám sát gọi nó liên tục."""
    calls: list = []

    class Boom:
        def enqueue(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("endpoint vận hành không được xếp hàng")

    monkeypatch.setattr("src.task_queue.get_queue", lambda name=None: Boom())

    response = await client.get(URL, headers={"X-Ops-Token": TOKEN})

    assert response.status_code == 200
    assert calls == []
