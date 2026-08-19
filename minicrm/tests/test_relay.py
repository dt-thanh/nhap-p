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
        client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})

    result = await relay_module.relay_tick(limit=50)
    # Tra theo `external_batch_id`, KHÔNG theo vị trí: `backend.requests` còn chứa
    # cả hai lần gửi ĐỒNG BỘ v1 (unit/deal, đã rời máy ngay lúc tạo, TRƯỚC lượt
    # relay này) — khớp theo batch id mới đúng dòng nào ứng với request nào.
    url_by_batch_id = {r["body"]["external_batch_id"]: r["url"] for r in backend.requests}
    urls_by_entity = {row.entity: url_by_batch_id[row.external_batch_id] for row in result.rows}
    assert urls_by_entity["projects"].endswith("/sync/projects")
    assert urls_by_entity["areas"].endswith("/sync/areas")
    assert urls_by_entity["units_v2"].endswith("/sync/units")
    assert urls_by_entity["deals_v2"].endswith("/sync/deals")
    assert result.delivered == 4


async def test_relay_payload_sent_is_byte_identical_to_what_was_captured(crm_app, backend):
    with _client() as client:
        client.post("/projects", json=PROJECT)
        batch_id = _v2_batch_id(client, "projects")
        captured_payload = _outbox_row(client, batch_id)["payload"]

    await relay_module.relay_tick()
    assert backend.requests[-1]["body"] == captured_payload


# --- Retry: 5xx/mạng có thử lại, 4xx thì không --------------------------------


async def test_relay_retries_a_row_that_failed_with_a_5xx(crm_app, backend):
    backend.fail_with(503, {"detail": "tạm thời quá tải"})
    with _client() as client:
        response = client.post("/units", json=UNIT)
        assert response.json()["sync"]["status"] == "sync_failed"
        batch_id = response.json()["sync"]["external_batch_id"]
        assert _outbox_row(client, batch_id)["http_status"] == 503

    backend.accept()
    result = await relay_module.relay_tick()
    assert result.picked_up >= 1
    retried = next(r for r in result.rows if r.external_batch_id == batch_id)
    assert retried.status == "synced"

    with _client() as client:
        row = _outbox_row(client, batch_id)
    assert row["http_status"] == 202
    assert row["attempts"] == 2, "một lần gửi đồng bộ hỏng + một lần relay = 2"


async def test_relay_does_not_retry_a_row_rejected_with_a_4xx(crm_app, backend):
    """4xx nghĩa là backend đã NHÌN đúng payload này và từ chối nó — payload không
    đổi khi gửi lại, nên gửi lại chỉ tái tạo đúng lỗi cũ. Relay không được chọn dòng
    này nữa, dù có gọi bao nhiêu lượt."""
    backend.fail_with(422, {"detail": {"error_code": "CONTRACT_VALIDATION_FAILED"}})
    with _client() as client:
        response = client.post("/units", json=UNIT)
        batch_id = response.json()["sync"]["external_batch_id"]
        assert _outbox_row(client, batch_id)["http_status"] == 422

    backend.accept()  # nếu relay lỡ chọn lại, lần gửi tiếp theo sẽ "thành công" và lộ ra ở assert dưới
    result = await relay_module.relay_tick()
    assert all(r.external_batch_id != batch_id for r in result.rows)

    with _client() as client:
        row = _outbox_row(client, batch_id)
    assert row["http_status"] == 422, "vẫn giữ nguyên mã lỗi cũ — không bị relay chạm vào"
    assert row["attempts"] == 1, "không tăng thêm — relay chưa từng thử lại"


async def test_relay_retries_a_row_that_failed_with_a_transport_error(crm_app, backend):
    """Timeout/không nối được ⇒ `http_status` giữ NULL (không phải một mã bịa ra) —
    cùng nhánh "chưa từng có phản hồi" như một dòng v2 chưa gửi, nên relay chọn lại
    đúng như đã thiết kế, không cần một nhánh mã riêng cho lỗi truyền tải."""
    backend.time_out()
    with _client() as client:
        response = client.post("/units", json=UNIT)
        assert response.json()["sync"]["status"] == "sync_pending"
        batch_id = response.json()["sync"]["external_batch_id"]
        assert _outbox_row(client, batch_id)["http_status"] is None
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
    assert first.delivered == 3  # project + area + units_v2 (unit v1 đã gửi đồng bộ ngay khi tạo)

    request_count_before = len(backend.requests)
    second = await relay_module.relay_tick(limit=50)
    assert second.picked_up == 0
    assert len(backend.requests) == request_count_before, "không có request thứ hai nào rời khỏi máy"


async def test_relay_resend_of_an_already_delivered_v1_row_is_still_the_explicit_operator_path(crm_app, backend):
    """Regression: `/outbox/{id}/resend` (v1) không đổi hành vi — vẫn là đường
    TƯỜNG MINH do người vận hành gọi, quan hoàn toàn độc lập với vòng relay."""
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        response = client.post(f"/outbox/{batch_id}/resend")
        assert response.status_code == 200
        assert response.json()["sync"]["status"] == "replayed"


# --- v1 không đổi: đường đồng bộ vẫn hoạt động mà không cần relay -------------


async def test_v1_crud_still_gets_an_immediate_sync_result_without_any_relay_tick(crm_app, backend):
    with _client() as client:
        response = client.post("/units", json=UNIT)
    assert response.json()["sync"]["status"] == "synced"
    assert len(backend.requests) == 1, "đường đồng bộ v1 vẫn tự gửi ngay, không chờ relay"


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
