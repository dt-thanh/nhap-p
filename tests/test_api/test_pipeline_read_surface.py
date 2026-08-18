"""Phase 5.5 P0 — Step 2/3/4: mặt đọc vận hành mới, RBAC, và reprocess kép.

`GET /sync-runs`, `GET /sync-errors`, `GET /sync-runs/{id}/payload` đòi vai trò
`pipeline_operator+` (token `Authorization: Bearer`). `GET /deals` mở, cùng mức
với `/inventory` hiện có. `POST /sync-runs/{id}/reprocess` chấp nhận CẢ hai:
`X-API-Key` (đường vốn có, không đổi) LẪN token vai trò (đường mới, cần
`confirm=true`).

Cùng khuôn hạ tầng với `tests/test_api/test_sync.py`: database test thật, client
ASGI in-process mang sẵn `X-API-Key` hợp lệ cho việc GHI lô (điều kiện tiên
quyết để có dữ liệu mà đọc).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.main import app
from src.models.tables import (
    areas,
    crm_source_records,
    deals,
    sync_payloads,
    units,
    upload_errors,
    upload_files,
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

PROJECT_ID = uuid.UUID("a1b2c3d4-5e6f-4708-9a1b-2c3d4e5f6071")
SOURCE_INSTANCE_ID = "crm-read-surface"
VIEWER_TOKEN = "rs-viewer-token-abc"
OPERATOR_TOKEN = "rs-operator-token-abc"
ADMIN_TOKEN = "rs-admin-token-abc"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def dashboard_tokens(monkeypatch):
    import json

    from src.config import get_settings

    monkeypatch.setenv("DASHBOARD_BUSINESS_VIEWER_TOKEN", VIEWER_TOKEN)
    monkeypatch.setenv("DASHBOARD_PIPELINE_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", ADMIN_TOKEN)
    # Phase E: file này kiểm VAI TRÒ (business_viewer/pipeline_operator/admin),
    # không kiểm phạm vi dự án — và dự án dựng bằng SQL thô ở `db_env` không có
    # `external_id` (di sản), nên chỉ phạm vi ALL mới thấy được nó. Phạm vi HẸP
    # là việc riêng của `test_project_scope.py`.
    monkeypatch.setenv(
        "DASHBOARD_PROJECT_SCOPE",
        json.dumps({VIEWER_TOKEN: "ALL", OPERATOR_TOKEN: "ALL", ADMIN_TOKEN: "ALL"}),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def db_env(session_factory, monkeypatch, dashboard_tokens):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id))))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(crm_source_records))
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'RS', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            await session.execute(
                sa.insert(areas),
                [
                    {
                        "id": uuid.uuid4(),
                        "project_id": PROJECT_ID,
                        "area_name": "A1",
                        "unit_type": "2PN",
                        "bedrooms": 2,
                        "area_sqm": 75,
                        "total_units": 100,
                        "created_at": datetime.now(UTC),
                    }
                ],
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


@pytest_asyncio.fixture
async def api_key(session_factory, db_env):
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            issued = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=SOURCE_INSTANCE_ID, label="test"
            )
    yield issued.api_key
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.text("DELETE FROM sync_credentials WHERE id = :i"), {"i": issued.credential_id})


@pytest_asyncio.fixture
async def crm_client(api_key):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-API-Key": api_key}) as c:
        yield c


@pytest_asyncio.fixture
async def anon_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _unit_payload(record_id: str, *, batch: str, revision: int = 1, status: str = "available") -> dict:
    return {
        "source_system": "mini_crm",
        "source_instance_id": SOURCE_INSTANCE_ID,
        "source_entity": "units",
        "schema_version": 1,
        "external_batch_id": batch,
        "project_id": str(PROJECT_ID),
        "sync_mode": "incremental",
        "records": [
            {
                "source_record_id": record_id,
                "operation": "upsert",
                "source_revision": revision,
                "source_updated_at": "2026-08-09T00:00:00Z",
                "data": {"area_name": "A1", "unit_type": "2PN", "unit_code": record_id, "status": status},
            }
        ],
    }


async def _post_unit_batch(crm_client, record_id: str, *, batch: str, **kw) -> dict:
    response = await crm_client.post(
        "/api/v1/sync/units", json=_unit_payload(record_id, batch=batch, **kw)
    )
    assert response.status_code in (200, 202), response.text
    return response.json()


# --- GET /sync-runs -----------------------------------------------------------


async def test_sync_runs_list_requires_authentication(anon_client, db_env):
    response = await anon_client.get("/api/v1/sync-runs")
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "MISSING_CREDENTIALS"


async def test_sync_runs_list_rejects_business_viewer(anon_client, db_env):
    response = await anon_client.get("/api/v1/sync-runs", headers=_headers(VIEWER_TOKEN))
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"


async def test_sync_runs_list_rejects_an_invalid_token(anon_client, db_env):
    response = await anon_client.get("/api/v1/sync-runs", headers=_headers("not-a-real-token"))
    assert response.status_code == 401


async def test_sync_runs_list_allows_pipeline_operator(anon_client, crm_client, db_env):
    await _post_unit_batch(crm_client, "RS-U1", batch="rs-batch-1")

    response = await anon_client.get(
        "/api/v1/sync-runs", params={"external_batch_id": "rs-batch-1"}, headers=_headers(OPERATOR_TOKEN)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["external_batch_id"] == "rs-batch-1"
    assert body["items"][0]["source_system"] == "mini_crm"


async def test_sync_runs_list_allows_admin(anon_client, crm_client, db_env):
    await _post_unit_batch(crm_client, "RS-U2", batch="rs-batch-2")

    response = await anon_client.get(
        "/api/v1/sync-runs", params={"external_batch_id": "rs-batch-2"}, headers=_headers(ADMIN_TOKEN)
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_sync_runs_list_only_shows_crm_batches_not_file_uploads(anon_client, crm_client, db_env, session_factory):
    """Mặt tổng quan pipeline CHỈ là lô CRM — file tải tay ở `/files`."""
    await _post_unit_batch(crm_client, "RS-U3", batch="rs-batch-3")
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=uuid.uuid4(),
                    project_id=PROJECT_ID,
                    uploaded_by=None,
                    filename="thu-cong.csv",
                    checksum="deadbeef",
                    status="completed",
                    rows_ok=1,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
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

    response = await anon_client.get(
        "/api/v1/sync-runs", params={"project_id_unused": "x"}, headers=_headers(OPERATOR_TOKEN)
    )
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["external_batch_id"] != "thu-cong.csv"
    filenames = [item.get("source_system") for item in response.json()["items"]]
    assert "manual_upload" not in filenames


async def test_sync_runs_list_filters_by_status(anon_client, crm_client, db_env):
    await _post_unit_batch(crm_client, "RS-U4", batch="rs-batch-4")

    response = await anon_client.get(
        "/api/v1/sync-runs", params={"status": "completed", "external_batch_id": "rs-batch-4"},
        headers=_headers(OPERATOR_TOKEN),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = await anon_client.get(
        "/api/v1/sync-runs", params={"status": "failed", "external_batch_id": "rs-batch-4"},
        headers=_headers(OPERATOR_TOKEN),
    )
    assert response.json()["total"] == 0


# --- GET /sync-errors -----------------------------------------------------------


async def test_sync_errors_requires_authentication(anon_client, db_env):
    response = await anon_client.get("/api/v1/sync-errors")
    assert response.status_code == 401


async def test_sync_errors_rejects_business_viewer(anon_client, db_env):
    response = await anon_client.get("/api/v1/sync-errors", headers=_headers(VIEWER_TOKEN))
    assert response.status_code == 403


async def test_sync_errors_surfaces_a_rejected_record_with_batch_context(anon_client, crm_client, db_env):
    # Bản ghi gần như trống -> bị từ chối, ghi vào upload_errors (cùng hình dạng
    # với `test_partial_batch_reports_both_sides` ở test_sync.py).
    payload = _unit_payload("RS-U5", batch="rs-batch-err")
    payload["records"][0] = {"source_record_id": "RS-U5-BAD", "data": {}}
    response = await crm_client.post("/api/v1/sync/units", json=payload)
    assert response.status_code in (200, 202)
    assert response.json()["rows_failed"] == 1

    result = await anon_client.get(
        "/api/v1/sync-errors", params={"batch": "rs-batch-err"}, headers=_headers(OPERATOR_TOKEN)
    )
    assert result.status_code == 200
    body = result.json()
    assert body["total"] >= 1
    assert body["errors"][0]["external_batch_id"] == "rs-batch-err"
    assert body["errors"][0]["source_system"] == "mini_crm"


# --- GET /sync-runs/{id}/payload ------------------------------------------------


async def test_payload_endpoint_requires_authentication(anon_client, crm_client, db_env):
    body = await _post_unit_batch(crm_client, "RS-U6", batch="rs-batch-payload")
    response = await anon_client.get(f"/api/v1/sync-runs/{body['sync_run_id']}/payload")
    assert response.status_code == 401


async def test_payload_endpoint_rejects_business_viewer(anon_client, crm_client, db_env):
    body = await _post_unit_batch(crm_client, "RS-U7", batch="rs-batch-payload-2")
    response = await anon_client.get(
        f"/api/v1/sync-runs/{body['sync_run_id']}/payload", headers=_headers(VIEWER_TOKEN)
    )
    assert response.status_code == 403


async def test_operator_gets_redacted_view_by_default(anon_client, crm_client, db_env):
    body = await _post_unit_batch(crm_client, "RS-U8", batch="rs-batch-payload-3")
    response = await anon_client.get(
        f"/api/v1/sync-runs/{body['sync_run_id']}/payload", headers=_headers(OPERATOR_TOKEN)
    )
    assert response.status_code == 200
    out = response.json()
    assert out["view"] == "redacted"
    assert out["payload"] is None
    assert len(out["payload_sha256"]) == 64


async def test_operator_raw_view_requires_confirm(anon_client, crm_client, db_env):
    body = await _post_unit_batch(crm_client, "RS-U9", batch="rs-batch-payload-4")

    without_confirm = await anon_client.get(
        f"/api/v1/sync-runs/{body['sync_run_id']}/payload",
        params={"view": "raw"},
        headers=_headers(OPERATOR_TOKEN),
    )
    assert without_confirm.status_code == 403
    assert without_confirm.json()["detail"]["error_code"] == "RAW_PAYLOAD_CONFIRMATION_REQUIRED"

    with_confirm = await anon_client.get(
        f"/api/v1/sync-runs/{body['sync_run_id']}/payload",
        params={"view": "raw", "confirm": "true"},
        headers=_headers(OPERATOR_TOKEN),
    )
    assert with_confirm.status_code == 200
    out = with_confirm.json()
    assert out["view"] == "raw"
    assert out["payload"] is not None
    assert out["payload"]["records"][0]["source_record_id"] == "RS-U9"


async def test_admin_raw_view_never_needs_confirm(anon_client, crm_client, db_env):
    body = await _post_unit_batch(crm_client, "RS-U10", batch="rs-batch-payload-5")
    response = await anon_client.get(
        f"/api/v1/sync-runs/{body['sync_run_id']}/payload", params={"view": "raw"}, headers=_headers(ADMIN_TOKEN)
    )
    assert response.status_code == 200
    assert response.json()["payload"] is not None


# --- GET /deals -----------------------------------------------------------------


async def test_deals_endpoint_now_requires_a_token(anon_client, db_env):
    """Phase E đóng `/deals` lại — trước đây mở, không cần token (xem
    `docs/roadmap.md` R-05, đúng rủi ro đã ghi nhận và nay được đóng)."""
    response = await anon_client.get("/api/v1/deals", params={"project_id": str(PROJECT_ID)})
    assert response.status_code == 401


async def test_deals_endpoint_open_to_a_viewer_with_all_scope(anon_client, db_env):
    response = await anon_client.get(
        "/api/v1/deals", params={"project_id": str(PROJECT_ID)}, headers=_headers(VIEWER_TOKEN)
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_deals_endpoint_lists_a_real_deal(anon_client, crm_client, db_env, session_factory):
    await _post_unit_batch(crm_client, "RS-U11", batch="rs-batch-deal-unit")

    deal_payload = {
        "source_system": "mini_crm",
        "source_instance_id": SOURCE_INSTANCE_ID,
        "source_entity": "deals",
        "schema_version": 1,
        "external_batch_id": "rs-batch-deal-1",
        "project_id": str(PROJECT_ID),
        "sync_mode": "incremental",
        "records": [
            {
                "source_record_id": "RS-DEAL-1",
                "operation": "upsert",
                "source_revision": 1,
                "source_updated_at": "2026-08-09T00:00:00Z",
                "data": {"external_unit_id": "RS-U11", "status": "reserved", "reserved_at": "2026-08-09T00:00:00Z"},
            }
        ],
    }
    response = await crm_client.post("/api/v1/sync/deals", json=deal_payload)
    assert response.status_code in (200, 202), response.text

    listed = await anon_client.get(
        "/api/v1/deals", params={"project_id": str(PROJECT_ID)}, headers=_headers(VIEWER_TOKEN)
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["external_deal_id"] == "RS-DEAL-1"
    assert body["items"][0]["status"] == "reserved"


# --- POST /sync-runs/{id}/reprocess: hai đường xác thực -------------------------


async def test_reprocess_via_dashboard_role_requires_confirm(anon_client, crm_client, db_env):
    # Giao dịch tới trước căn -> bị từ chối -> lô 'failed', chạy lại được.
    deal_payload = {
        "source_system": "mini_crm",
        "source_instance_id": SOURCE_INSTANCE_ID,
        "source_entity": "deals",
        "schema_version": 1,
        "external_batch_id": "rs-batch-reprocess-1",
        "project_id": str(PROJECT_ID),
        "sync_mode": "incremental",
        "records": [
            {
                "source_record_id": "RS-DEAL-REPROCESS",
                "operation": "upsert",
                "source_revision": 1,
                "source_updated_at": "2026-08-09T00:00:00Z",
                "data": {"external_unit_id": "RS-MISSING-UNIT", "status": "reserved"},
            }
        ],
    }
    created = await crm_client.post("/api/v1/sync/deals", json=deal_payload)
    run_id = created.json()["sync_run_id"]
    assert created.json()["status"] == "failed"

    without_confirm = await anon_client.post(
        f"/api/v1/sync-runs/{run_id}/reprocess", headers=_headers(OPERATOR_TOKEN)
    )
    assert without_confirm.status_code == 422
    assert without_confirm.json()["detail"]["error_code"] == "CONFIRMATION_REQUIRED"

    denied_for_viewer = await anon_client.post(
        f"/api/v1/sync-runs/{run_id}/reprocess", params={"confirm": "true"}, headers=_headers(VIEWER_TOKEN)
    )
    assert denied_for_viewer.status_code == 403

    with_confirm = await anon_client.post(
        f"/api/v1/sync-runs/{run_id}/reprocess", params={"confirm": "true"}, headers=_headers(OPERATOR_TOKEN)
    )
    # Vẫn hỏng (căn vẫn chưa tồn tại) — nhưng CHẠY được, không bị chặn bởi RBAC.
    assert with_confirm.status_code in (200, 202)


async def test_reprocess_via_legacy_api_key_is_unchanged(anon_client, crm_client, db_env):
    """Đường xác thực VỐN CÓ (khoá CRM đúng phạm vi) không cần confirm, không đổi."""
    deal_payload = {
        "source_system": "mini_crm",
        "source_instance_id": SOURCE_INSTANCE_ID,
        "source_entity": "deals",
        "schema_version": 1,
        "external_batch_id": "rs-batch-reprocess-2",
        "project_id": str(PROJECT_ID),
        "sync_mode": "incremental",
        "records": [
            {
                "source_record_id": "RS-DEAL-REPROCESS-2",
                "operation": "upsert",
                "source_revision": 1,
                "source_updated_at": "2026-08-09T00:00:00Z",
                "data": {"external_unit_id": "RS-MISSING-UNIT-2", "status": "reserved"},
            }
        ],
    }
    created = await crm_client.post("/api/v1/sync/deals", json=deal_payload)
    run_id = created.json()["sync_run_id"]

    response = await crm_client.post(f"/api/v1/sync-runs/{run_id}/reprocess")
    assert response.status_code in (200, 202)
