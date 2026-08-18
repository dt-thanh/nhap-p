"""Test API đồng bộ: `POST /sync/{entity}`, `GET /sync-runs/{id}`, `.../errors`.

Chạy qua ASGI in-process với `client` fixture của tests/conftest.py, giống
tests/test_api/test_files.py.

Từ Phase 3, `POST /sync/{entity}` BẮT BUỘC có khoá API và khoá phải thuộc đúng
`source_instance_id` mà lô tự khai. Module này vì thế che (`shadow`) fixture
`client` của conftest bằng một client mang sẵn khoá hợp lệ — nhờ vậy các test
nghiệp vụ sẵn có không phải lặp lại header ở từng lời gọi. Đường xác thực hỏng
được kiểm riêng ở `tests/test_api/test_sync_auth.py` bằng client KHÔNG mang khoá.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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

# UUID riêng của module này — xem ghi chú ở tests/test_services/test_source_identity.py.
PROJECT_ID = uuid.UUID("b7f8c1d2-3e4a-4b5c-8d9e-0f1a2b3c4d5e")
SYNC_URL = "/api/v1/sync/units"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def db_env(session_factory, monkeypatch):
    """Trỏ engine của ứng dụng vào DB test và dựng dự án cho lô đồng bộ."""
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        # Từ 0007, bản ghi nguồn được chiếu xuống `units`/`deals`.
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id))))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(crm_source_records))
        # Từ 0010, fk_sync_payloads_sync_run_id là RESTRICT: phải xoá payload
        # TRƯỚC khi xoá lô. Đó chính là điểm của ràng buộc — xoá lịch sử
        # payload phải là hành động cố ý.
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Sync', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            # Phân khu để tầng chiếu (0007) tra được `area_id` cho căn.
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


SOURCE_INSTANCE_ID = "crm-project-a"


@pytest_asyncio.fixture
async def api_key(session_factory, db_env):
    """Cấp một khoá thật cho instance mà `_payload()` khai.

    Cấp qua service chứ không cắm thẳng hash vào bảng: như vậy test đi qua đúng
    đường sinh khoá của production, và một thay đổi ở cách băm sẽ làm test đỏ
    thay vì lặng lẽ trôi.
    """
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            issued = await SyncCredentialService().issue(
                session,
                source_system="mini_crm",
                source_instance_id=SOURCE_INSTANCE_ID,
                label="test",
            )
    yield issued.api_key
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.text("DELETE FROM sync_credentials WHERE id = :i"), {"i": issued.credential_id})


@pytest_asyncio.fixture
async def client(api_key):
    """Che fixture `client` của conftest: mọi request của module này mang sẵn khoá."""
    from httpx import ASGITransport, AsyncClient

    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-API-Key": api_key}) as authorized:
        yield authorized


def _unit_record(record_id, *, updated_at="2026-08-09T00:00:00Z", **data):
    """Một bản ghi `units` hợp lệ.

    Từ 0007 `data` được tầng chiếu diễn giải thật, nên payload phải đủ trường của
    một căn; trước đó nó là khối mờ và test cũ chỉ cần `unit_code`.
    """
    return {
        "source_record_id": record_id,
        "operation": "upsert",
        "source_updated_at": updated_at,
        "data": {
            "area_name": "A1",
            "unit_type": "2PN",
            "unit_code": f"CODE-{record_id}",
            "status": "available",
            **data,
        },
    }


def _payload(records=None, *, batch="api-batch-1", **overrides):
    payload = {
        "source_system": "mini_crm",
        "source_instance_id": "crm-project-a",
        "source_entity": "units",
        "schema_version": 1,
        "external_batch_id": batch,
        "project_id": str(PROJECT_ID),
        "records": records if records is not None else [_unit_record("UNIT-001")],
    }
    payload.update(overrides)
    return payload


# --- POST /sync/{entity} ----------------------------------------------------


async def test_valid_envelope_is_accepted(client):
    response = await client.post(SYNC_URL, json=_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["replayed"] is False
    assert (body["rows_received"], body["rows_ok"], body["rows_failed"]) == (1, 1, 0)
    assert body["decisions"]["insert"] == 1
    uuid.UUID(body["sync_run_id"])  # phải là UUID hợp lệ


async def test_invalid_shape_is_rejected_with_useful_detail(client):
    response = await client.post(SYNC_URL, json=_payload(source_system=""))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "INVALID_ENVELOPE"
    assert detail["json_path"] == "$.source_system"
    assert detail["message"]


async def test_non_object_body_is_rejected(client):
    response = await client.post(SYNC_URL, json=[1, 2, 3])
    # FastAPI tự chặn ở tầng Body(...) trước khi vào parser.
    assert response.status_code == 422


async def test_unsupported_schema_version_is_rejected(client):
    response = await client.post(SYNC_URL, json=_payload(schema_version=99))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert detail["json_path"] == "$.schema_version"


async def test_unsupported_entity_is_rejected(client):
    response = await client.post("/api/v1/sync/customers", json=_payload(source_entity="customers"))

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "UNSUPPORTED_ENTITY"


async def test_route_and_envelope_entity_must_agree(client):
    response = await client.post("/api/v1/sync/deals", json=_payload(source_entity="units"))

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "ENTITY_MISMATCH"


async def test_unknown_project_is_rejected(client):
    response = await client.post(SYNC_URL, json=_payload(project_id=str(uuid.uuid4())))

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "UNKNOWN_PROJECT"


async def test_malformed_project_id_is_rejected(client):
    response = await client.post(SYNC_URL, json=_payload(project_id="khong-phai-uuid"))

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "INVALID_PROJECT_ID"


async def test_replaying_the_same_batch_returns_the_first_run(client):
    first = await client.post(SYNC_URL, json=_payload(batch="replay-me"))
    second = await client.post(SYNC_URL, json=_payload(batch="replay-me"))

    assert first.status_code == 202
    assert second.status_code == 200, "lô đã xử lý → 200, không phải 202"
    assert second.json()["replayed"] is True
    assert second.json()["sync_run_id"] == first.json()["sync_run_id"]


async def test_partial_batch_reports_both_sides(client):
    response = await client.post(
        SYNC_URL,
        json=_payload(
            records=[
                _unit_record("UNIT-1"),
                {"source_record_id": "UNIT-2", "data": {}},  # thiếu phiên bản
            ]
        ),
    )

    body = response.json()
    assert body["status"] == "partially_completed"
    assert (body["rows_received"], body["rows_ok"], body["rows_failed"]) == (2, 1, 1)
    assert body["decisions"]["insert"] == 1


# --- GET /sync-runs/{id} ----------------------------------------------------


async def test_sync_run_detail_returns_full_state(client):
    created = await client.post(SYNC_URL, json=_payload(batch="detail-1", source_cursor="cursor-9"))
    sync_run_id = created.json()["sync_run_id"]

    response = await client.get(f"/api/v1/sync-runs/{sync_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["sync_run_id"] == sync_run_id
    assert body["project_id"] == str(PROJECT_ID)
    assert body["source_system"] == "mini_crm"
    assert body["source_instance_id"] == "crm-project-a"
    assert body["source_entity"] == "units"
    assert body["external_batch_id"] == "detail-1"
    assert body["input_format"] == "json"
    assert body["transport_mode"] == "api_push"
    assert body["sync_mode"] == "incremental"
    assert body["schema_version"] == 1
    assert body["status"] == "completed"
    assert body["rows_received"] == 1
    assert body["decisions"]["insert"] == 1
    assert body["last_source_cursor"] == "cursor-9"
    assert body["started_at"] is not None
    assert body["finished_at"] is not None


async def test_sync_run_detail_404_and_422(client):
    assert (await client.get(f"/api/v1/sync-runs/{uuid.uuid4()}")).status_code == 404

    bad = await client.get("/api/v1/sync-runs/khong-phai-uuid")
    assert bad.status_code == 422
    assert bad.json()["detail"]["error_code"] == "INVALID_SYNC_RUN_ID"


# --- GET /sync-runs/{id}/errors ---------------------------------------------


async def test_errors_endpoint_returns_json_path_without_row_number(client):
    created = await client.post(
        SYNC_URL,
        json=_payload(records=[{"source_record_id": "UNIT-1", "data": "khong-phai-object"}], batch="err-1"),
    )
    sync_run_id = created.json()["sync_run_id"]

    response = await client.get(f"/api/v1/sync-runs/{sync_run_id}/errors")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    error = body["errors"][0]
    assert error["json_path"] == "$.records[0].data"
    assert error["row_number"] is None, "bản ghi JSON không có số dòng"
    assert error["error_category"] == "schema"
    assert error["error_code"] == "INVALID_DATA"
    assert error["source_record_id"] == "UNIT-1"
    assert error["retry_status"] == "open"


async def test_errors_endpoint_filters_and_paginates(client):
    records = [{"source_record_id": f"UNIT-{i}", "data": {}} for i in range(5)]
    created = await client.post(SYNC_URL, json=_payload(records=records, batch="err-many"))
    sync_run_id = created.json()["sync_run_id"]

    page = await client.get(f"/api/v1/sync-runs/{sync_run_id}/errors?limit=2&offset=0")
    assert page.json()["total"] == 5
    assert len(page.json()["errors"]) == 2

    filtered = await client.get(f"/api/v1/sync-runs/{sync_run_id}/errors?error_category=conflict")
    assert filtered.json()["total"] == 0


async def test_conflict_is_visible_through_the_errors_endpoint(client):
    # Phải khác nhau ở một TRƯỜNG ĐÃ ÁNH XẠ: từ Phase 4 dấu vân chỉ tính trên
    # những trường thật sự được lưu, nên trường ngoài ánh xạ không sinh đụng độ.
    await client.post(SYNC_URL, json=_payload(records=[_unit_record("UNIT-1", unit_code="C-1")], batch="c1"))
    created = await client.post(SYNC_URL, json=_payload(records=[_unit_record("UNIT-1", unit_code="C-2")], batch="c2"))

    response = await client.get(f"/api/v1/sync-runs/{created.json()['sync_run_id']}/errors")

    error = response.json()["errors"][0]
    assert error["error_category"] == "conflict"
    assert error["error_code"] == "VERSION_CONFLICT"
    assert error["source_record_id"] == "UNIT-1"


async def test_errors_endpoint_404_for_unknown_run(client):
    assert (await client.get(f"/api/v1/sync-runs/{uuid.uuid4()}/errors")).status_code == 404


# --- Không lộ dữ liệu nhạy cảm ----------------------------------------------


async def test_errors_never_echo_sensitive_values_or_sql(client):
    """Giá trị nhạy cảm và câu SQL không được lọt vào bản ghi lỗi."""
    created = await client.post(
        SYNC_URL,
        json=_payload(
            records=[
                {
                    "source_record_id": "UNIT-1",
                    "operation": "merge",  # thao tác không hợp lệ → sinh lỗi
                    "data": {"phone": "0901234567", "email": "khach@example.com", "note": "khách VIP"},
                }
            ],
            batch="pii-1",
        ),
    )
    response = await client.get(f"/api/v1/sync-runs/{created.json()['sync_run_id']}/errors")

    blob = response.text
    for secret in ("0901234567", "khach@example.com", "khách VIP"):
        assert secret not in blob, f"thông tin nhạy cảm '{secret}' lọt vào bản ghi lỗi"
    for leaked in ("INSERT INTO", "SELECT ", "Traceback", "asyncpg"):
        assert leaked not in blob


# --- Không phá luồng CSV đã có ----------------------------------------------


async def test_csv_upload_route_still_exists(client):
    """`POST /api/v1/files/upload` phải còn nguyên — S2 không đụng API cũ."""
    response = await client.post("/api/v1/files/upload")

    # 422 vì thiếu form field, KHÔNG phải 404: route vẫn được đăng ký.
    assert response.status_code == 422
