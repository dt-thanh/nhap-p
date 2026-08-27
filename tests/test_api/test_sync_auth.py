"""Bốn cổng của `POST /sync/{entity}`: xác thực, kích thước, hợp đồng, giữ payload.

`test_sync.py` kiểm phần NGHIỆP VỤ và dùng client mang sẵn khoá hợp lệ. Module
này kiểm đúng những gì module kia bỏ qua: chuyện gì xảy ra khi khoá thiếu, sai,
bị thu hồi, thuộc instance khác; khi payload quá lớn; khi payload sai hợp đồng;
và payload thô có thực sự được giữ lại không.

Thứ tự các cổng cũng được kiểm, không chỉ từng cổng riêng lẻ — xem
`test_size_is_checked_before_authentication`.
"""

from __future__ import annotations

import json
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
    sync_credentials,
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

PROJECT_ID = uuid.UUID("c1d2e3f4-5a6b-4c7d-8e9f-0a1b2c3d4e60")
INSTANCE = "synthetic-auth-instance"
SYNC_URL = "/api/v1/sync/units"


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
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id))))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        # LIKE chứ không phải `==`: module này dùng nhiều instance
        # (`synthetic-auth-instance`, `synthetic-auth-contract`, …) và bỏ sót một
        # cái sẽ để lại bản ghi nguồn mồ côi, chặn việc xoá `upload_files` bằng
        # khoá ngoại.
        await session.execute(
            sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id.like("synthetic-auth%"))
        )
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(
            sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id.like("synthetic-auth%"))
        )
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Auth', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            await session.execute(
                sa.insert(areas).values(
                    id=uuid.uuid4(),
                    project_id=PROJECT_ID,
                    area_name="A1",
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=75,
                    total_units=100,
                    created_at=datetime.now(UTC),
                )
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


@pytest_asyncio.fixture
async def issued(session_factory):
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            return await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=INSTANCE, label="auth test"
            )


@pytest_asyncio.fixture
async def anonymous():
    """Client KHÔNG mang khoá — dùng để kiểm đường từ chối."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _payload(records=None, *, batch="auth-batch-1", instance=INSTANCE, **overrides):
    payload = {
        "source_system": "mini_crm",
        "source_instance_id": instance,
        "source_entity": "units",
        "schema_version": 1,
        "external_batch_id": batch,
        "project_id": str(PROJECT_ID),
        "records": records
        if records is not None
        else [
            {
                "source_record_id": "AUTH-UNIT-1",
                "operation": "upsert",
                "source_updated_at": "2026-08-09T00:00:00Z",
                "data": {
                    "area_name": "A1",
                    "unit_type": "2PN",
                    "unit_code": "CODE-AUTH-1",
                    "status": "available",
                },
            }
        ],
    }
    payload.update(overrides)
    return payload


# --- Cổng 1: xác thực -------------------------------------------------------


async def test_request_without_a_key_is_rejected(anonymous):
    response = await anonymous.post(SYNC_URL, json=_payload())

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "MISSING_API_KEY"
    # Chuẩn HTTP: 401 phải nói cho client biết cách xác thực lại.
    assert "WWW-Authenticate" in response.headers


async def test_request_with_an_unknown_key_is_rejected(anonymous):
    response = await anonymous.post(SYNC_URL, json=_payload(), headers={"X-API-Key": "afsk_khong-ton-tai-dau"})

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "INVALID_API_KEY"


async def test_valid_key_is_accepted(anonymous, issued):
    response = await anonymous.post(SYNC_URL, json=_payload(), headers={"X-API-Key": issued.api_key})

    assert response.status_code == 202, response.text


async def test_revoked_key_is_rejected(anonymous, issued, session_factory):
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            await SyncCredentialService().revoke(session, issued.credential_id)

    response = await anonymous.post(SYNC_URL, json=_payload(), headers={"X-API-Key": issued.api_key})

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "REVOKED_API_KEY"


async def test_key_cannot_write_to_another_instance(anonymous, issued):
    """Khoá hợp lệ nhưng lô khai instance khác → 403, không phải 401.

    401 nghĩa là "không biết anh là ai"; ở đây ta biết rõ, chỉ là anh không được
    phép ghi vào phạm vi này. Trả sai mã sẽ khiến người tích hợp đi tìm nhầm chỗ.
    """
    response = await anonymous.post(
        SYNC_URL,
        json=_payload(instance="synthetic-auth-other-instance"),
        headers={"X-API-Key": issued.api_key},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSTANCE_MISMATCH"


async def test_rejected_request_writes_nothing(anonymous, session_factory):
    """Lô bị từ chối ở cổng xác thực không được để lại dấu vết nào trong DB."""
    await anonymous.post(SYNC_URL, json=_payload(batch="never-lands"))

    async with session_factory() as session:
        runs = await session.scalar(
            sa.select(sa.func.count()).select_from(upload_files).where(upload_files.c.project_id == PROJECT_ID)
        )
        payloads = await session.scalar(sa.select(sa.func.count()).select_from(sync_payloads))
    assert runs == 0
    assert payloads == 0


async def test_error_response_never_echoes_the_key(anonymous, issued):
    """Thông báo lỗi không được nhắc lại khoá — log và trace sẽ giữ nó lại."""
    response = await anonymous.post(
        SYNC_URL, json=_payload(instance="synthetic-auth-other"), headers={"X-API-Key": issued.api_key}
    )

    assert issued.api_key not in response.text


# --- Cổng 2: kích thước -----------------------------------------------------


async def test_oversized_payload_is_rejected_with_413(anonymous, issued):
    """Vượt trần → 413, kèm số đo để người gửi biết phải chia nhỏ bao nhiêu."""
    from src.services.sync_payloads import MAX_PAYLOAD_BYTES

    padding = "x" * (MAX_PAYLOAD_BYTES + 1024)
    payload = _payload(batch="too-big")
    payload["records"][0]["data"]["unit_code"] = padding

    response = await anonymous.post(SYNC_URL, json=payload, headers={"X-API-Key": issued.api_key})

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["error_code"] == "PAYLOAD_TOO_LARGE"
    assert detail["limit_bytes"] == MAX_PAYLOAD_BYTES
    assert detail["size_bytes"] > MAX_PAYLOAD_BYTES


async def test_size_is_checked_before_authentication(anonymous):
    """Payload quá lớn bị chặn NGAY, không cần khoá hợp lệ.

    Thứ tự này là có chủ đích: nếu xác thực chạy trước, một người gọi vô danh vẫn
    ép được máy chủ nhận trọn body khổng lồ rồi mới từ chối. Đo trước thì công
    sức bỏ ra là hằng số.
    """
    from src.services.sync_payloads import MAX_PAYLOAD_BYTES

    payload = _payload(batch="big-and-anonymous")
    payload["records"][0]["data"]["unit_code"] = "x" * (MAX_PAYLOAD_BYTES + 1024)

    response = await anonymous.post(SYNC_URL, json=payload)

    assert response.status_code == 413, "kích thước phải được kiểm trước xác thực"


async def test_malformed_json_is_a_400(anonymous, issued):
    response = await anonymous.post(
        SYNC_URL,
        content=b"{khong phai json",
        headers={"X-API-Key": issued.api_key, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "MALFORMED_JSON"


# --- Cổng 3: hợp đồng -------------------------------------------------------


async def test_contract_v1_payload_is_validated_and_accepted(anonymous, session_factory):
    """Payload theo hợp đồng v1 đi qua bộ kiểm hình dạng rồi được chuyển đổi."""
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            key = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id="synthetic-auth-contract", label="contract"
            )

    payload = {
        "_comment": "FIXTURE TỔNG HỢP — KHÔNG PHẢI DỮ LIỆU CRM THẬT.",
        "schema_version": 1,
        "source_system": "mini_crm",
        "source_instance_id": "synthetic-auth-contract",
        "external_batch_id": "SYNTH-CONTRACT-1",
        "sync_mode": "incremental",
        "project_ref": {"project_id": str(PROJECT_ID)},
        "source_extracted_at": "2026-08-09T02:00:00+07:00",
        "records": [
            {
                "entity": "unit",
                "operation": "upsert",
                "external_id": "SYNTH-U-C1",
                "source_revision": 1,
                "payload": {
                    "area_ref": {"area_name": "A1", "unit_type": "2PN"},
                    "unit_code": "A1-C-01",
                    "unit_status": "available",
                },
            }
        ],
    }

    response = await anonymous.post(SYNC_URL, json=payload, headers={"X-API-Key": key.api_key})

    assert response.status_code == 202, response.text
    assert response.json()["rows_ok"] == 1


async def test_contract_v1_payload_violating_the_schema_is_rejected(anonymous, session_factory):
    """Sai hình dạng → 422 kèm danh sách vi phạm có json_path."""
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            key = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id="synthetic-auth-contract", label="contract"
            )

    payload = {
        "schema_version": 1,
        "source_system": "mini_crm",
        "source_instance_id": "synthetic-auth-contract",
        "external_batch_id": "SYNTH-CONTRACT-BAD",
        "sync_mode": "incremental",
        "project_ref": {"project_id": str(PROJECT_ID)},
        "source_extracted_at": "2026-08-09T02:00:00+07:00",
        # Bản ghi không mang phiên bản nào — hợp đồng mục 5 cấm.
        "records": [
            {
                "entity": "unit",
                "operation": "upsert",
                "external_id": "SYNTH-U-BAD",
                "payload": {
                    "area_ref": {"area_name": "A1", "unit_type": "2PN"},
                    "unit_code": "A1-BAD",
                    "unit_status": "available",
                },
            }
        ],
    }

    response = await anonymous.post(SYNC_URL, json=payload, headers={"X-API-Key": key.api_key})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "CONTRACT_VALIDATION_FAILED"
    assert detail["errors"], "phải nêu được vi phạm nào"
    assert all(error["json_path"].startswith("$") for error in detail["errors"])


# --- Cổng 4 + lưu giữ payload -----------------------------------------------


async def test_raw_payload_is_retained_with_its_hash(anonymous, issued, session_factory):
    """Payload thô được giữ nguyên văn, kèm hash và số byte."""
    from src.services.sync_payloads import canonical_bytes

    payload = _payload(batch="retain-1")
    response = await anonymous.post(SYNC_URL, json=payload, headers={"X-API-Key": issued.api_key})
    run_id = uuid.UUID(response.json()["sync_run_id"])

    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(sync_payloads).where(sync_payloads.c.sync_run_id == run_id)))
            .mappings()
            .one()
        )

    assert row["payload"] == payload, "payload lưu xuống phải giống hệt payload gửi lên"
    assert row["record_count"] == 1
    assert row["payload_bytes"] > 0
    assert row["credential_id"] == issued.credential_id

    import hashlib

    assert row["payload_sha256"] == hashlib.sha256(canonical_bytes(payload)).hexdigest()


async def test_stored_payload_passes_integrity_verification(anonymous, issued, session_factory):
    """Băm lại bản đã lưu phải khớp hash lưu kèm."""
    from src.services.sync_payloads import SyncPayloadService

    response = await anonymous.post(SYNC_URL, json=_payload(batch="verify-1"), headers={"X-API-Key": issued.api_key})
    run_id = uuid.UUID(response.json()["sync_run_id"])

    async with session_factory() as session:
        assert await SyncPayloadService().verify_integrity(session, run_id) is True


async def test_tampered_payload_fails_integrity_verification(anonymous, issued, session_factory):
    """Sửa bản đã lưu thì kiểm toàn vẹn phải phát hiện ra.

    Không có test này, `verify_integrity` có thể trả True cho mọi thứ và không ai
    biết — một hàm kiểm luôn nói "đạt" thì tệ hơn là không có.
    """
    from src.services.sync_payloads import SyncPayloadService

    response = await anonymous.post(SYNC_URL, json=_payload(batch="tamper-1"), headers={"X-API-Key": issued.api_key})
    run_id = uuid.UUID(response.json()["sync_run_id"])

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(sync_payloads)
                .where(sync_payloads.c.sync_run_id == run_id)
                .values(payload={"records": [], "tampered": True})
            )

    async with session_factory() as session:
        assert await SyncPayloadService().verify_integrity(session, run_id) is False


async def test_replaying_a_batch_stores_no_second_payload(anonymous, issued, session_factory):
    """Gửi lại cùng `external_batch_id` → trả kết quả cũ, KHÔNG ghi payload lần hai."""
    first = await anonymous.post(SYNC_URL, json=_payload(batch="replay-p"), headers={"X-API-Key": issued.api_key})
    second = await anonymous.post(SYNC_URL, json=_payload(batch="replay-p"), headers={"X-API-Key": issued.api_key})

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["sync_run_id"] == first.json()["sync_run_id"]

    async with session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(sync_payloads)
            .where(sync_payloads.c.sync_run_id == uuid.UUID(first.json()["sync_run_id"]))
        )
    assert count == 1, "chạy lại đã ghi thêm một payload nữa"


async def test_two_different_batches_each_keep_their_own_payload(anonymous, issued, session_factory):
    await anonymous.post(SYNC_URL, json=_payload(batch="keep-a"), headers={"X-API-Key": issued.api_key})
    await anonymous.post(SYNC_URL, json=_payload(batch="keep-b"), headers={"X-API-Key": issued.api_key})

    async with session_factory() as session:
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs))
        )
    assert count == 2


# --- GET /sync-runs/{id} và /sync-runs/{id}/errors (từng hoàn toàn không xác thực) --


async def _create_a_run(anonymous, issued, *, batch: str) -> str:
    response = await anonymous.post(SYNC_URL, json=_payload(batch=batch), headers={"X-API-Key": issued.api_key})
    assert response.status_code == 202, response.text
    return response.json()["sync_run_id"]


async def test_run_detail_without_any_credential_is_401(anonymous, issued):
    run_id = await _create_a_run(anonymous, issued, batch="read-anon")

    response = await anonymous.get(f"/api/v1/sync-runs/{run_id}")

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "MISSING_API_KEY"


async def test_run_errors_without_any_credential_is_401(anonymous, issued):
    run_id = await _create_a_run(anonymous, issued, batch="read-anon-err")

    response = await anonymous.get(f"/api/v1/sync-runs/{run_id}/errors")

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "MISSING_API_KEY"


async def test_run_detail_rejects_an_invalid_api_key(anonymous, issued):
    run_id = await _create_a_run(anonymous, issued, batch="read-invalid-key")

    response = await anonymous.get(f"/api/v1/sync-runs/{run_id}", headers={"X-API-Key": "afsk_khong-ton-tai"})

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "INVALID_API_KEY"


async def test_run_detail_allows_the_owning_instances_key(anonymous, issued):
    """Đường vốn có: hệ nguồn tự poll lô của chính nó bằng đúng khoá của nó."""
    run_id = await _create_a_run(anonymous, issued, batch="read-own-key")

    response = await anonymous.get(f"/api/v1/sync-runs/{run_id}", headers={"X-API-Key": issued.api_key})

    assert response.status_code == 200
    assert response.json()["sync_run_id"] == run_id


async def test_run_detail_rejects_a_key_from_another_instance(anonymous, issued, session_factory):
    """Không dựa vào UUID khó đoán — một khoá THẬT của instance KHÁC vẫn bị chặn."""
    from src.services.sync_credentials import SyncCredentialService

    run_id = await _create_a_run(anonymous, issued, batch="read-other-instance")

    async with session_factory() as session:
        async with session.begin():
            stranger = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id="synthetic-auth-other-reader", label="x"
            )

    response = await anonymous.get(f"/api/v1/sync-runs/{run_id}", headers={"X-API-Key": stranger.api_key})

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSTANCE_MISMATCH"

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM sync_credentials WHERE source_instance_id = 'synthetic-auth-other-reader'")
            )


async def test_run_detail_rejects_a_viewer_token_with_no_project_scope(anonymous, issued):
    """Vai trò dashboard hợp lệ nhưng phạm vi RỖNG (không phải ALL) → vẫn 403."""
    from tests.conftest import DASHBOARD_VIEWER_TOKEN

    run_id = await _create_a_run(anonymous, issued, batch="read-viewer-no-scope")

    response = await anonymous.get(
        f"/api/v1/sync-runs/{run_id}", headers={"Authorization": f"Bearer {DASHBOARD_VIEWER_TOKEN}"}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


async def test_run_detail_allows_an_admin_token_with_all_scope(anonymous, issued):
    from tests.conftest import DASHBOARD_ADMIN_TOKEN

    run_id = await _create_a_run(anonymous, issued, batch="read-admin-all-scope")

    response = await anonymous.get(
        f"/api/v1/sync-runs/{run_id}", headers={"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}
    )

    assert response.status_code == 200
    assert response.json()["sync_run_id"] == run_id


async def test_run_errors_allows_an_admin_token_with_all_scope(anonymous, issued):
    from tests.conftest import DASHBOARD_ADMIN_TOKEN

    bad_record = {
        "source_record_id": "AUTH-READ-BAD",
        "operation": "upsert",
        "source_updated_at": "2026-08-09T00:00:00Z",
        "data": {"area_name": "KHONG-CO", "unit_type": "2PN", "unit_code": "X", "status": "available"},
    }
    response = await anonymous.post(
        SYNC_URL, json=_payload(records=[bad_record], batch="read-errors-admin"), headers={"X-API-Key": issued.api_key}
    )
    run_id = response.json()["sync_run_id"]

    errors_response = await anonymous.get(
        f"/api/v1/sync-runs/{run_id}/errors", headers={"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}
    )

    assert errors_response.status_code == 200
    assert errors_response.json()["errors"], "phải đọc được lỗi của lô, không chỉ trạng thái"


async def test_run_detail_of_an_unknown_run_is_404(anonymous, issued):
    response = await anonymous.get(
        f"/api/v1/sync-runs/{uuid.uuid4()}", headers={"X-API-Key": issued.api_key}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "SYNC_RUN_NOT_FOUND"


async def test_payload_survives_a_batch_that_failed_validation(anonymous, issued, session_factory):
    """Lô có bản ghi hỏng vẫn phải giữ payload — đó là lúc cần nó nhất."""
    bad_record = {
        "source_record_id": "AUTH-BAD-1",
        "operation": "upsert",
        "source_updated_at": "2026-08-09T00:00:00Z",
        "data": {"area_name": "KHONG-CO", "unit_type": "2PN", "unit_code": "X", "status": "available"},
    }
    response = await anonymous.post(
        SYNC_URL, json=_payload(records=[bad_record], batch="bad-but-kept"), headers={"X-API-Key": issued.api_key}
    )
    run_id = uuid.UUID(response.json()["sync_run_id"])

    async with session_factory() as session:
        stored = (
            (await session.execute(sa.select(sync_payloads).where(sync_payloads.c.sync_run_id == run_id)))
            .mappings()
            .one_or_none()
        )
    assert stored is not None, "lô hỏng mà không giữ payload thì không điều tra được"
    assert json.loads(json.dumps(stored["payload"]))["records"][0]["source_record_id"] == "AUTH-BAD-1"
