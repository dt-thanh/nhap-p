"""Vòng relay tự động (Phase C.5): đọc lại `crm_outbox`, gửi lại đúng payload đã ký.

Test ở đây gọi TRỰC TIẾP `app.relay.relay_tick()` (không chờ vòng nền thật, vốn ngủ
`MINICRM_RELAY_INTERVAL_SECONDS` trước lượt đầu — xem docstring `RelayLoop`) để giữ
test nhanh và tất định. `test_real_backend_sync.py` là nơi chứng minh vòng NỀN THẬT
(đang chạy trong container, không ai gọi `relay_tick()` bằng tay) tự phục hồi sau
gián đoạn — xem test đó cho "outage → phục hồi tự động" bằng container thật.

Giới hạn CÓ CHỦ ĐÍCH, ghi lại tường minh (khớp docstring `app/relay.py`): không có
khoá hàng ở tầng DB. An toàn "không gửi trùng" ở đây dựa vào ĐÚNG MỘT vòng nền chạy
tuần tự trong một tiến trình Mini CRM (`docker-compose.yml` chỉ định nghĩa MỘT
service `minicrm`, không có bản sao) — không phải một khoá `FOR UPDATE SKIP LOCKED`
chống được nhiều tiến trình song song. `test_relay_loop_never_overlaps_its_own_ticks`
kiểm ĐÚNG bảo đảm này: vòng nền tự nó không bao giờ chạy hai lượt chồng lấn.
"""

from __future__ import annotations

import asyncio

from app import relay as relay_module
from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_AUTH_HEADER, TEST_AREA_EXTERNAL_ID, TEST_PROJECT_EXTERNAL_ID

PROJECT = {"name": "Khu do thi Ben Xanh", "launch_date": "2026-06-01"}
AREA = {
    "external_project_id": TEST_PROJECT_EXTERNAL_ID,
    "area_name": "A1",
    "unit_type": "2PN",
    "bedrooms": 2,
    "area_sqm": 68.5,
    "total_units": 120,
}
UNIT = {"external_area_id": TEST_AREA_EXTERNAL_ID, "unit_code": "B1-01-01", "unit_status": "available"}


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


def _outbox_row(client, batch_id: str) -> dict:
    return client.get(f"/outbox/{batch_id}").json()


def _v2_batch_id(client, entity: str) -> str:
    return client.get("/outbox", params={"entity": entity, "limit": 1}).json()["items"][0]["external_batch_id"]


# --- Phân phối lần đầu: v2 chỉ LƯU ở Phase C, được GỬI ở Phase C.5 ------------


async def test_relay_delivers_a_captured_project_row(crm_app, backend):
    """Phase C: `POST /projects` chỉ ghi một dòng outbox v2, KHÔNG gửi (http_status
    NULL). Phase C.5: một lượt relay phải gửi nó, KHÔNG cần `/resend` thủ công."""
    with _client() as client:
        client.post("/projects", json=PROJECT)
        batch_id = _v2_batch_id(client, "projects")
        assert _outbox_row(client, batch_id)["http_status"] is None

    result = await relay_module.relay_tick()
    assert result.picked_up == 1
    assert result.delivered == 1
    assert result.rows[0].entity == "projects"
    assert backend.requests[-1]["url"].endswith("/api/v1/sync/projects")

    with _client() as client:
        row = _outbox_row(client, batch_id)
    assert row["http_status"] == 202
    assert row["sent_at"] is not None
    assert row["attempts"] == 1


async def test_relay_delivers_area_unit_deal_v2_rows_to_the_correct_backend_route(crm_app, backend):
    """Bốn nhãn outbox v2 (`projects`/`areas`/`units_v2`/`deals_v2`) phải map đúng
    bốn URL v2 mà Phase D đã mở — không lẫn `units_v2` với `units` (v1)."""
    with _client() as client:
        client.post("/projects", json=PROJECT)
        client.post("/areas", json=AREA)
        client.post("/units", json=UNIT)
    first = await relay_module.relay_tick(limit=50)
    assert first.delivered == 3
    with _client() as client:
        client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})

    result = await relay_module.relay_tick(limit=50)
    # Tra theo `external_batch_id`, không phụ thuộc thứ tự request.
    url_by_batch_id = {r["body"]["external_batch_id"]: r["url"] for r in backend.requests}
    urls_by_entity = {row.entity: url_by_batch_id[row.external_batch_id] for row in [*first.rows, *result.rows]}
    assert urls_by_entity["projects"].endswith("/sync/projects")
    assert urls_by_entity["areas"].endswith("/sync/areas")
    assert urls_by_entity["units_v2"].endswith("/sync/units")
    assert urls_by_entity["deals_v2"].endswith("/sync/deals")
    assert result.delivered == 1


async def test_relay_payload_sent_is_byte_identical_to_what_was_captured(crm_app, backend):
    with _client() as client:
        client.post("/projects", json=PROJECT)
        batch_id = _v2_batch_id(client, "projects")
        captured_payload = _outbox_row(client, batch_id)["payload"]

    await relay_module.relay_tick()
    assert backend.requests[-1]["body"] == captured_payload


# --- Retry: 5xx/mạng có thử lại, 4xx thì không --------------------------------


async def test_relay_retries_a_row_that_failed_with_a_5xx(crm_app, backend):
    with _client() as client:
        response = client.post("/units", json=UNIT)
        assert response.json()["sync"]["status"] == "sync_pending"
        batch_id = response.json()["sync"]["external_batch_id"]
        assert _outbox_row(client, batch_id)["http_status"] is None

    backend.fail_with(503, {"detail": "tạm thời quá tải"})
    first = await relay_module.relay_tick()
    assert first.rows[0].status == "sync_failed"
    backend.accept()
    result = await relay_module.relay_tick()
    assert result.picked_up >= 1
    retried = next(r for r in result.rows if r.external_batch_id == batch_id)
    assert retried.status == "synced"

    with _client() as client:
        row = _outbox_row(client, batch_id)
    assert row["http_status"] == 202
    assert row["attempts"] == 2, "một lần relay hỏng + một lần relay thành công = 2"


async def test_relay_does_not_retry_a_row_rejected_with_a_4xx(crm_app, backend):
    """4xx nghĩa là backend đã NHÌN đúng payload này và từ chối nó — payload không
    đổi khi gửi lại, nên gửi lại chỉ tái tạo đúng lỗi cũ. Relay không được chọn dòng
    này nữa, dù có gọi bao nhiêu lượt."""
    with _client() as client:
        response = client.post("/units", json=UNIT)
        batch_id = response.json()["sync"]["external_batch_id"]
        assert _outbox_row(client, batch_id)["http_status"] is None

    backend.fail_with(422, {"detail": {"error_code": "CONTRACT_VALIDATION_FAILED"}})
    result = await relay_module.relay_tick()
    assert next(r for r in result.rows if r.external_batch_id == batch_id).status == "sync_failed"
    backend.accept()  # nếu relay lỡ chọn lại, lần gửi tiếp theo sẽ "thành công" và lộ ra ở assert dưới
    result = await relay_module.relay_tick()
    assert all(r.external_batch_id != batch_id for r in result.rows)

    with _client() as client:
        row = _outbox_row(client, batch_id)
    assert row["http_status"] == 422, "vẫn giữ nguyên mã lỗi cũ — không bị relay chạm vào"
    assert row["attempts"] == 1, "4xx terminal không được relay lại"


async def test_relay_retries_a_row_that_failed_with_a_transport_error(crm_app, backend):
    """Timeout/không nối được ⇒ `http_status` giữ NULL (không phải một mã bịa ra) —
    cùng nhánh "chưa từng có phản hồi" như một dòng v2 chưa gửi, nên relay chọn lại
    đúng như đã thiết kế, không cần một nhánh mã riêng cho lỗi truyền tải."""
    with _client() as client:
        response = client.post("/units", json=UNIT)
        assert response.json()["sync"]["status"] == "sync_pending"
        batch_id = response.json()["sync"]["external_batch_id"]
        assert _outbox_row(client, batch_id)["http_status"] is None

    backend.time_out()
    first = await relay_module.relay_tick()
    assert next(r for r in first.rows if r.external_batch_id == batch_id).status == "sync_pending"
    with _client() as client:
        assert _outbox_row(client, batch_id)["attempts"] == 1
        assert _outbox_row(client, batch_id)["last_error"] is not None
    backend.accept()
    result = await relay_module.relay_tick()
    retried = next(r for r in result.rows if r.external_batch_id == batch_id)
    assert retried.status == "synced"
    with _client() as client:
        row = _outbox_row(client, batch_id)
    assert row["http_status"] == 202
    assert row["last_error"] is None, "lần gửi thành công phải xoá lỗi truyền tải cũ"
    assert row["attempts"] == 2


# --- Idempotency: một dòng đã "chốt" không bao giờ bị chọn lại ----------------


async def test_relay_second_tick_finds_nothing_left_to_send_no_duplicate_projection(crm_app, backend):
    with _client() as client:
        client.post("/projects", json=PROJECT)
        client.post("/areas", json=AREA)
        client.post("/units", json=UNIT)

    first = await relay_module.relay_tick(limit=50)
    assert first.delivered == first.picked_up
    assert first.delivered == 3  # project + area + units_v2

    request_count_before = len(backend.requests)
    second = await relay_module.relay_tick(limit=50)
    assert second.picked_up == 0
    assert len(backend.requests) == request_count_before, "không có request thứ hai nào rời khỏi máy"


async def test_relay_does_not_offer_resend_for_canonical_v2_unit(crm_app, backend):
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        response = client.post(f"/outbox/{batch_id}/resend")
        assert response.status_code == 409
        assert response.json()["error_code"] == "V2_DELIVERY_NOT_ENABLED"


# --- Canonical Unit writes wait for RelayLoop ----------------------------------


async def test_canonical_unit_write_waits_for_relay_without_immediate_delivery(crm_app, backend):
    with _client() as client:
        response = client.post("/units", json=UNIT)
    assert response.json()["sync"]["status"] == "sync_pending"
    assert len(backend.requests) == 0


# --- Vòng nền: không tự chồng lấn lượt của chính nó ---------------------------


async def test_relay_loop_never_overlaps_its_own_ticks(crm_app, backend, monkeypatch):
    concurrent = 0
    max_concurrent = 0
    real_tick = relay_module.relay_tick

    async def _tracked_tick(*args, **kwargs):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        try:
            return await real_tick(*args, **kwargs)
        finally:
            concurrent -= 1

    monkeypatch.setattr(relay_module, "relay_tick", _tracked_tick)
    monkeypatch.setattr(relay_module.get_settings(), "relay_interval_seconds", 0.05, raising=False)

    loop = relay_module.RelayLoop()
    loop.start()
    await asyncio.sleep(0.35)
    await loop.stop()

    assert max_concurrent <= 1


async def test_relay_tick_respects_the_batch_size_limit(crm_app, backend):
    with _client() as client:
        for i in range(3):
            client.post(
                "/areas",
                json={**AREA, "area_name": f"A{i}", "unit_type": f"T{i}"},
            )

    result = await relay_module.relay_tick(limit=2)
    assert result.picked_up == 2
