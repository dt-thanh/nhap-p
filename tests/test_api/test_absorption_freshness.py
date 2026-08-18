"""Phase 5.5 P0 — Step 1B/5B: bằng chứng đồng bộ THẬT trên `GET /absorption/summary`.

`last_successful_sync` / `last_attempted_sync` / `last_sync_status` phải đọc từ
`upload_files` (transport_mode='api_push'), KHÔNG PHẢI đồng hồ trình duyệt — đó
là thứ trước đây `DashboardPage.jsx` tự bịa bằng `new Date()` (F-2). `calculator`
phải luôn có mặt để frontend không bao giờ trộn nguồn dữ liệu mà không gắn nhãn
(5B).

Chèn thẳng `upload_files` bằng SQL để kiểm soát chính xác `status`/`uploaded_at`/
`finished_at` — không cần dựng một lô đồng bộ thật cho từng kịch bản thời gian.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.main import app
from src.models.tables import upload_files

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

PROJECT_ID = uuid.UUID("f1e2d3c4-b5a6-4978-8899-aabbccddeeff")
SUMMARY_URL = "/api/v1/absorption/summary"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def db_env(session_factory, monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'FRESH', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _insert_batch(session_factory, *, status: str, uploaded_at: datetime, finished_at: datetime | None) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=uuid.uuid4(),
                    project_id=PROJECT_ID,
                    uploaded_by=None,
                    filename=None,
                    checksum=None,
                    status=status,
                    rows_ok=1,
                    rows_failed=0,
                    uploaded_at=uploaded_at,
                    finished_at=finished_at,
                    source_system="mini_crm",
                    source_instance_id="crm-freshness",
                    source_entity="units",
                    input_format="json",
                    transport_mode="api_push",
                    sync_mode="incremental",
                    schema_version=1,
                    external_batch_id=f"fresh-{uuid.uuid4().hex[:8]}",
                    rows_received=1,
                    error_summary={},
                )
            )


NOW = datetime.now(UTC)


async def test_a_project_that_never_synced_reports_all_three_fields_as_none(client):
    response = await client.get(SUMMARY_URL, params={"project_id": str(PROJECT_ID)})
    assert response.status_code == 200
    body = response.json()
    assert body["last_successful_sync"] is None
    assert body["last_attempted_sync"] is None
    assert body["last_sync_status"] is None
    assert body["calculator"] == "legacy_aggregate"


async def test_a_successful_sync_populates_both_success_and_attempted(client, session_factory):
    finished = NOW - timedelta(minutes=5)
    await _insert_batch(session_factory, status="completed", uploaded_at=NOW - timedelta(minutes=6), finished_at=finished)

    body = (await client.get(SUMMARY_URL, params={"project_id": str(PROJECT_ID)})).json()
    assert body["last_sync_status"] == "completed"
    assert body["last_successful_sync"] is not None
    assert body["last_attempted_sync"] is not None


async def test_completed_with_conflicts_counts_as_a_successful_sync(client, session_factory):
    """5A liên kết với 1B: đụng độ không phải lỗi, nên vẫn tính là 'thành công'."""
    finished = NOW - timedelta(minutes=3)
    await _insert_batch(
        session_factory, status="completed_with_conflicts", uploaded_at=NOW - timedelta(minutes=4), finished_at=finished
    )

    body = (await client.get(SUMMARY_URL, params={"project_id": str(PROJECT_ID)})).json()
    assert body["last_sync_status"] == "completed_with_conflicts"
    assert body["last_successful_sync"] is not None


async def test_a_failed_sync_is_never_reported_as_successful(client, session_factory):
    """Đúng yêu cầu 'failed sync must not appear successful'."""
    await _insert_batch(session_factory, status="failed", uploaded_at=NOW - timedelta(minutes=2), finished_at=NOW - timedelta(minutes=1))

    body = (await client.get(SUMMARY_URL, params={"project_id": str(PROJECT_ID)})).json()
    assert body["last_sync_status"] == "failed"
    assert body["last_successful_sync"] is None
    assert body["last_attempted_sync"] is not None


async def test_last_attempted_reflects_the_most_recent_attempt_even_if_it_failed_after_a_success(
    client, session_factory
):
    """Bằng chứng THÀNH CÔNG gần nhất và bằng chứng ĐƯỢC GHI NHẬN gần nhất có thể
    khác thời điểm — đây chính là ca `sync_failed` mà UI phải hiển thị riêng."""
    older_success = NOW - timedelta(hours=2)
    newer_failure = NOW - timedelta(minutes=1)
    await _insert_batch(session_factory, status="completed", uploaded_at=older_success, finished_at=older_success)
    await _insert_batch(session_factory, status="failed", uploaded_at=newer_failure, finished_at=newer_failure)

    body = (await client.get(SUMMARY_URL, params={"project_id": str(PROJECT_ID)})).json()
    assert body["last_sync_status"] == "failed"
    assert body["last_successful_sync"] is not None
    last_success = datetime.fromisoformat(body["last_successful_sync"])
    last_attempt = datetime.fromisoformat(body["last_attempted_sync"])
    assert last_success < last_attempt, "thành công cũ hơn lần thử gần nhất — không được trộn hai mốc này"


async def test_a_file_upload_batch_never_counts_as_a_crm_sync(client, session_factory):
    """`transport_mode='file_upload'` không được lẫn vào bằng chứng đồng bộ CRM."""
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=uuid.uuid4(),
                    project_id=PROJECT_ID,
                    uploaded_by=None,
                    filename="thu-cong.csv",
                    checksum="abc123",
                    status="completed",
                    rows_ok=1,
                    rows_failed=0,
                    uploaded_at=NOW,
                    finished_at=NOW,
                    source_system="manual_upload",
                    source_instance_id="n/a",
                    source_entity=None,
                    input_format="csv",
                    transport_mode="file_upload",
                    sync_mode="full_snapshot",
                    schema_version=1,
                    external_batch_id=None,
                    rows_received=1,
                    error_summary={},
                )
            )

    body = (await client.get(SUMMARY_URL, params={"project_id": str(PROJECT_ID)})).json()
    assert body["last_successful_sync"] is None
    assert body["last_attempted_sync"] is None
    assert body["last_sync_status"] is None


async def test_freshness_fields_are_present_on_the_domain_calculator_too(client, session_factory):
    """5B: cả hai calculator phải mang CÙNG bằng chứng đồng bộ — không riêng bộ cũ."""
    await _insert_batch(session_factory, status="completed", uploaded_at=NOW, finished_at=NOW)

    body = (
        await client.get(SUMMARY_URL, params={"project_id": str(PROJECT_ID), "calculator": "domain_units_deals"})
    ).json()
    assert body["calculator"] == "domain_units_deals"
    assert body["last_sync_status"] == "completed"
    assert body["last_successful_sync"] is not None
