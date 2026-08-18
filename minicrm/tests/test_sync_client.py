"""Sync client — hình dạng phong bì, header, và ghi nhận outbox.

Test chạy với HTTP đã MOCK (`httpx.MockTransport`): điều đáng kiểm ở đây là Mini
CRM dựng ra đúng thứ gì và ghi lại đúng thứ gì, không phải backend xử lý ra sao.
Phép thử qua HTTP thật nằm ở phần nghiệm thu của Phase 3, chạy bằng tay.

Client CỐ Ý không import validator của backend. Nếu nó import, Mini CRM sẽ luôn tự
cho mình là đúng, và bộ kiểm hợp đồng ở phía nhận mất hết ý nghĩa.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import httpx
import pytest
import sqlalchemy as sa
from app.config import Settings, get_settings
from app.models import crm_outbox
from app.sync_client import SyncClient, SyncPushError, build_unit_envelope, new_batch_id
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import db_skip_reason, db_url

_SKIP = db_skip_reason()

PROJECT_ID = "40fa9c11-4e0f-589c-80a4-9bcb1f889960"

UNITS = [
    {
        "external_id": "U-0001",
        "area_name": "DEMO Toà B1",
        "unit_type": "Căn hộ",
        "unit_code": "A1-01-01",
        "unit_status": "available",
        "source_revision": 1,
    }
]


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """Cấu hình tất định cho test, KHÔNG đọc .env của máy."""
    get_settings.cache_clear()
    monkeypatch.setenv("MINICRM_SYNC_BASE_URL", "http://api:8000")
    monkeypatch.setenv("MINICRM_SYNC_API_KEY", "afsk_test_key")
    monkeypatch.setenv("MINICRM_SOURCE_SYSTEM", "mini_crm")
    monkeypatch.setenv("MINICRM_SOURCE_INSTANCE_ID", "mini-crm-dev")
    monkeypatch.setenv("MINICRM_PROJECT_ID", PROJECT_ID)
    monkeypatch.setattr("app.config.get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr("app.sync_client.get_settings", lambda: Settings(_env_file=None))
    yield
    get_settings.cache_clear()


# --- 3/4/5/6. Hình dạng phong bì (không cần DB) ------------------------------


def test_envelope_matches_the_v1_contract_shape():
    envelope = build_unit_envelope(UNITS, batch_id="mc-units-abc")

    assert envelope["schema_version"] == 1
    assert envelope["sync_mode"] == "incremental"
    assert envelope["project_ref"] == {"project_id": PROJECT_ID}
    assert set(envelope) >= {
        "schema_version",
        "source_system",
        "source_instance_id",
        "external_batch_id",
        "sync_mode",
        "project_ref",
        "source_extracted_at",
        "records",
    }


def test_envelope_carries_the_configured_source_identity():
    """`source_instance_id` là ranh giới cô lập phía backend, và khoá API bị buộc
    vào đúng giá trị này. Sai giá trị ⇒ backend trả 403 INSTANCE_MISMATCH."""
    envelope = build_unit_envelope(UNITS, batch_id="mc-units-abc")
    assert envelope["source_system"] == "mini_crm"
    assert envelope["source_instance_id"] == "mini-crm-dev"


def test_extracted_at_carries_a_timezone_offset():
    """Backend từ chối mốc TRẦN. Một mốc không có múi giờ không nói được nó là giờ
    nào, mà đây lại là căn cứ xếp thứ tự sự kiện."""
    envelope = build_unit_envelope(UNITS, batch_id="mc-units-abc")
    parsed = datetime.fromisoformat(envelope["source_extracted_at"])
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed) is not None


def test_unit_record_uses_the_stable_area_ref_form():
    """Dạng `{area_name, unit_type}`, KHÔNG phải `{area_id}`.

    Hợp đồng cho phép cả hai, nhưng một `source_instance_id` phải dùng ĐÚNG MỘT
    hình dạng vĩnh viễn: dấu vân payload tính trên tập trường đã ánh xạ, nên đổi
    hình dạng giữa hai lô cho cùng một căn ở cùng revision sẽ sinh hai dấu vân
    khác nhau và backend báo `conflict` giả.
    """
    record = build_unit_envelope(UNITS, batch_id="mc-units-abc")["records"][0]

    assert record["entity"] == "unit"
    assert record["operation"] == "upsert"
    assert record["external_id"] == "U-0001"
    assert record["source_revision"] == 1
    assert record["payload"]["area_ref"] == {"area_name": "DEMO Toà B1", "unit_type": "Căn hộ"}
    assert "area_id" not in record["payload"]["area_ref"]
    assert record["payload"]["unit_code"] == "A1-01-01"
    assert record["payload"]["unit_status"] == "available"


def test_envelope_carries_no_customer_or_price_data():
    """Hợp đồng không yêu cầu chúng, và Mini CRM không có chúng để gửi."""
    blob = json.dumps(build_unit_envelope(UNITS, batch_id="mc-units-abc"), ensure_ascii=False).lower()
    for forbidden in ("price", "customer", "phone", "email", "commission", "salesperson"):
        assert forbidden not in blob


def test_batch_ids_are_unique_per_call():
    assert new_batch_id("units") != new_batch_id("units")


# --- 1/2/7/8/9/10/11. Đường gửi + outbox (cần DB) ---------------------------

pytestmark_db = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")


@pytest.fixture
def session_factory():
    engine = create_async_engine(db_url(), poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    engine.sync_engine.dispose()


def _transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _outbox_rows(session_factory, batch_id):
    async with session_factory() as session:
        rows = (
            await session.execute(sa.select(crm_outbox).where(crm_outbox.c.external_batch_id == batch_id))
        ).mappings().all()
    return [dict(r) for r in rows]


@pytestmark_db
async def test_push_hits_the_right_endpoint_with_the_api_key(session_factory):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("X-API-Key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"sync_run_id": str(uuid.uuid4()), "status": "completed", "replayed": False})

    batch_id = new_batch_id("units")
    envelope = build_unit_envelope(UNITS, batch_id=batch_id)
    async with _transport(handler) as client:
        result = await SyncClient(session_factory).push(envelope, entity="units", client=client)

    assert seen["url"] == "http://api:8000/api/v1/sync/units"
    assert seen["key"] == "afsk_test_key"
    assert seen["body"]["external_batch_id"] == batch_id
    assert result.status_code == 202
    assert result.accepted and not result.replayed


@pytestmark_db
async def test_payload_and_response_are_persisted_to_the_outbox(session_factory):
    """Giữ NGUYÊN VĂN cả hai chiều — đây là thứ khiến "gửi lại đúng lô cũ" ở
    Phase 4 thành một thao tác thật thay vì một lời mô tả."""
    run_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"sync_run_id": run_id, "status": "completed", "replayed": False})

    batch_id = new_batch_id("units")
    envelope = build_unit_envelope(UNITS, batch_id=batch_id)
    async with _transport(handler) as client:
        await SyncClient(session_factory).push(envelope, entity="units", client=client)

    rows = await _outbox_rows(session_factory, batch_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["entity"] == "units"
    assert row["http_status"] == 202
    assert row["response"]["sync_run_id"] == run_id
    assert row["payload"]["records"][0]["external_id"] == "U-0001"
    assert row["sent_at"] is not None


@pytestmark_db
async def test_a_422_is_reported_and_still_recorded(session_factory):
    """Lô bị từ chối vẫn phải để lại vết: nếu không, hợp đồng sai trông y hệt như
    một lô chưa bao giờ được gửi."""
    detail = {"detail": {"error_code": "CONTRACT_VALIDATION_FAILED", "errors": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json=detail)

    batch_id = new_batch_id("units")
    envelope = build_unit_envelope(UNITS, batch_id=batch_id)

    with pytest.raises(SyncPushError) as exc:
        async with _transport(handler) as client:
            await SyncClient(session_factory).push(envelope, entity="units", client=client)

    assert exc.value.status_code == 422
    assert exc.value.batch_id == batch_id

    rows = await _outbox_rows(session_factory, batch_id)
    assert rows[0]["http_status"] == 422
    assert rows[0]["response"] == detail


@pytestmark_db
async def test_timeout_leaves_the_outbox_row_unresolved(session_factory):
    """Timeout KHÔNG được ghi một mã lỗi bịa ra.

    `http_status = NULL` đọc là "đã gửi, CHƯA biết kết quả" — và đó là sự thật:
    lô có thể đã tới backend và đã được xử lý. Ghi 500 vào đây là khẳng định một
    điều ta không biết.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("quá hạn", request=request)

    batch_id = new_batch_id("units")
    envelope = build_unit_envelope(UNITS, batch_id=batch_id)

    with pytest.raises(SyncPushError, match="Hết thời gian chờ"):
        async with _transport(handler) as client:
            await SyncClient(session_factory).push(envelope, entity="units", client=client)

    rows = await _outbox_rows(session_factory, batch_id)
    assert len(rows) == 1, "dòng outbox phải tồn tại dù không biết kết quả"
    assert rows[0]["http_status"] is None
    assert rows[0]["response"] is None
    assert rows[0]["sent_at"] is None
    assert rows[0]["payload"]["external_batch_id"] == batch_id


@pytestmark_db
async def test_a_replayed_batch_is_reported_as_such(session_factory):
    """Backend trả 200 + replayed=true khi nhận lại đúng `external_batch_id`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sync_run_id": str(uuid.uuid4()), "status": "completed", "replayed": True})

    batch_id = new_batch_id("units")
    envelope = build_unit_envelope(UNITS, batch_id=batch_id)
    async with _transport(handler) as client:
        result = await SyncClient(session_factory).push(envelope, entity="units", client=client)

    assert result.status_code == 200
    assert result.accepted and result.replayed


@pytestmark_db
async def test_unknown_entity_is_rejected_before_any_write(session_factory):
    with pytest.raises(ValueError, match="entity phải là một trong"):
        await SyncClient(session_factory).push({"external_batch_id": "x"}, entity="customers")
