"""Phase C — ý định v2 (KHÔNG gửi) trong `crm_outbox`, cho cả bốn tầng.

Khác `test_outbox.py` (v1, GỬI THẬT qua `FakeBackend`) ở đúng một điểm: những test
ở đây chứng minh Mini CRM LƯU đúng gì, KHÔNG chứng minh nó gửi đi đâu — Phase C
không gửi phong bì v2 (xem `crud._capture_v2`, `sync_client.py` mục v2). Fixture
`backend` (FakeBackend) vẫn được dùng ở những test có kèm ghi Unit/Deal, vì đường
v1 CỦA CHÚNG vẫn gửi thật và cần một backend giả để không cố nối mạng.
"""

from __future__ import annotations

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


def _v2_total(client, entity: str) -> int:
    return client.get("/outbox", params={"entity": entity}).json()["total"]


def _v2_rows(client, entity: str) -> list[dict]:
    return client.get("/outbox", params={"entity": entity, "limit": 500}).json()["items"]


# --- C1: Project/Area — exactement một dòng outbox v2 mỗi lần ghi -------------


def test_project_create_produces_exactly_one_v2_outbox_row(crm_app, backend):
    with _client() as client:
        assert _v2_total(client, "projects") == 0
        client.post("/projects", json=PROJECT)
        assert _v2_total(client, "projects") == 1


def test_project_update_produces_exactly_one_more_v2_row(crm_app, backend):
    with _client() as client:
        client.post("/projects", json=PROJECT)
        assert _v2_total(client, "projects") == 1
        client.patch("/projects/P-0001", json={"name": "Tên mới"})
        assert _v2_total(client, "projects") == 2


def test_project_archive_produces_a_delete_v2_row(crm_app, backend):
    with _client() as client:
        client.post("/projects", json=PROJECT)
        client.delete("/projects/P-0001")
        rows = _v2_rows(client, "projects")
        assert len(rows) == 2
        batch_id = rows[0]["external_batch_id"]  # mới nhất trước
        row = client.get(f"/outbox/{batch_id}").json()
        assert row["payload"]["records"][0]["operation"] == "delete"
        assert "payload" not in row["payload"]["records"][0] or row["payload"]["records"][0].get("payload") is None


def test_area_create_produces_exactly_one_v2_outbox_row(crm_app, backend):
    with _client() as client:
        assert _v2_total(client, "areas") == 0
        client.post("/areas", json=AREA)
        assert _v2_total(client, "areas") == 1


def test_area_update_and_archive_each_produce_one_more_row(crm_app, backend):
    with _client() as client:
        client.post("/areas", json=AREA)
        client.patch("/areas/A-0001", json={"total_units": 130})
        assert _v2_total(client, "areas") == 2
        client.delete("/areas/A-0001")
        assert _v2_total(client, "areas") == 3


def test_invalid_project_write_creates_no_outbox_row(crm_app, backend):
    """Không tạo dòng outbox cho một lần ghi BỊ TỪ CHỐI cục bộ — `PATCH` một dự án
    không tồn tại (`RECORD_NOT_FOUND`, 404) không chạm transaction ghi nào."""
    with _client() as client:
        before = _v2_total(client, "projects")
        response = client.patch("/projects/P-KHONG-CO", json={"name": "x"})
        assert response.status_code == 404
        assert _v2_total(client, "projects") == before


def test_area_under_missing_project_creates_no_outbox_row(crm_app, backend):
    with _client() as client:
        before = _v2_total(client, "areas")
        response = client.post("/areas", json={**AREA, "external_project_id": "P-KHONG-CO"})
        assert response.status_code == 422
        assert _v2_total(client, "areas") == before


# --- C1 payload shape: kiểm ở tầng response thật, không chỉ ở builder thuần ----


def test_captured_v2_project_payload_matches_the_written_row(crm_app, backend):
    with _client() as client:
        record = client.post("/projects", json=PROJECT).json()["record"]
        batch_id = _v2_rows(client, "projects")[0]["external_batch_id"]
        payload = client.get(f"/outbox/{batch_id}").json()["payload"]

    assert payload["schema_version"] == 2
    assert payload["project_ref"] == {"external_project_id": record["external_id"]}
    (rec,) = payload["records"]
    assert rec["entity"] == "project" and rec["operation"] == "upsert"
    assert rec["external_id"] == record["external_id"]
    assert rec["source_revision"] == record["source_revision"]
    assert rec["payload"] == {"name": record["name"], "launch_date": record["launch_date"]}


def test_captured_v2_area_payload_references_project_by_external_id(crm_app, backend):
    with _client() as client:
        client.post("/areas", json=AREA)
        batch_id = _v2_rows(client, "areas")[0]["external_batch_id"]
        payload = client.get(f"/outbox/{batch_id}").json()["payload"]

    assert payload["project_ref"] == {"external_project_id": TEST_PROJECT_EXTERNAL_ID}
    assert payload["records"][0]["payload"]["area_name"] == "A1"


# --- C4: Unit/Deal — MỘT ý định v2 THÊM, v1 không đổi --------------------------


def test_unit_create_under_a_local_area_produces_exactly_one_v2_event(crm_app, backend):
    with _client() as client:
        v1_before = client.get("/outbox", params={"entity": "units"}).json()["total"]
        v2_before = _v2_total(client, "units_v2")

        response = client.post("/units", json=UNIT)
        assert response.status_code == 201
        assert set(response.json()) == {"record", "sync"}

        assert client.get("/outbox", params={"entity": "units"}).json()["total"] == v1_before
        assert _v2_total(client, "units_v2") == v2_before + 1
        assert response.json()["sync"]["status"] == "sync_pending"
        assert backend.requests == []


def test_unit_v2_payload_uses_the_bootstrap_area_external_id(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        batch_id = _v2_rows(client, "units_v2")[0]["external_batch_id"]
        payload = client.get(f"/outbox/{batch_id}").json()["payload"]

    assert payload["records"][0]["payload"]["area_ref"] == {"external_area_id": TEST_AREA_EXTERNAL_ID}
    assert payload["project_ref"] == {"external_project_id": TEST_PROJECT_EXTERNAL_ID}


def test_unit_update_and_delete_each_produce_one_more_v2_row(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        assert _v2_total(client, "units_v2") == 1
        client.patch("/units/U-0001", json={"unit_status": "reserved"})
        assert _v2_total(client, "units_v2") == 2
        client.delete("/units/U-0001")
        assert _v2_total(client, "units_v2") == 3


def test_legacy_unit_without_a_local_area_produces_no_v2_event(crm_app, backend):
    """163 căn di sản (`area_id IS NULL`) không có phân khu cục bộ để trỏ vào —
    không thể dựng `area_ref` mà không bịa dữ liệu (§A0), nên KHÔNG có ý định v2
    nào được tạo cho chúng. Chèn thẳng bằng SQL để mô phỏng, giống
    `test_migration_0003.py::test_a_pre_hierarchy_unit_can_still_be_inserted_with_no_area`.
    """
    import sqlalchemy as sa

    from tests.conftest import sync_url

    engine = sa.create_engine(sync_url(crm_app))
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_units (id, external_id, area_id, area_name, unit_type, unit_code, "
                    "unit_status, source_revision, created_at, updated_at) VALUES (gen_random_uuid(), "
                    "'U-LEGACY-01', NULL, 'Di sản', 'Cũ', 'X-01', 'available', 1, now(), now())"
                )
            )
    finally:
        engine.dispose()

    with _client() as client:
        before = _v2_total(client, "units_v2")
        response = client.patch("/units/U-LEGACY-01", json={"unit_status": "reserved"})
        assert response.status_code == 422
        assert response.json()["error_code"] == "UNIT_AREA_REQUIRED"
        assert _v2_total(client, "units_v2") == before, "căn di sản không có area_id — không dựng được ý định v2"

        response = client.delete("/units/U-LEGACY-01")
        assert response.status_code == 422
        assert response.json()["error_code"] == "UNIT_AREA_REQUIRED"
        assert _v2_total(client, "units_v2") == before


async def test_deal_create_update_delete_each_produce_one_v2_row(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
    await relay_module.relay_tick()
    with _client() as client:
        assert _v2_total(client, "deals_v2") == 0

        client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})
        assert _v2_total(client, "deals_v2") == 1

        client.patch("/deals/D-0001", json={"deal_status": "reserved", "reserved_at": "2026-08-10T09:00:00+07:00"})
        assert _v2_total(client, "deals_v2") == 2

        client.delete("/deals/D-0001")
        assert _v2_total(client, "deals_v2") == 3


async def test_deal_v2_payload_has_no_project_or_area_ref(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
    await relay_module.relay_tick()
    with _client() as client:
        client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})
        batch_id = _v2_rows(client, "deals_v2")[0]["external_batch_id"]
        payload = client.get(f"/outbox/{batch_id}").json()["payload"]

    record_payload = payload["records"][0]["payload"]
    assert "project_ref" not in record_payload and "area_ref" not in record_payload
    assert record_payload["external_unit_id"] == "U-0001"


# --- C5/C6: dòng v2 CHỈ LƯU ngay lúc ghi; C.5b mở `resend` cho v2 -------------


def test_resend_on_a_pending_v2_row_is_rejected_not_delivered_early(crm_app, backend):
    """Một dòng v2 vừa ghi (`http_status IS NULL`, chưa tới lượt relay) VẪN bị
    `resend` từ chối 409 — không có `crud._resend_v2`/"Phase C.5b" nào trong mã
    nguồn hiện tại (xem `crud._reject_v2_delivery`, `resend()`): đường gửi DUY
    NHẤT của v2 là vòng relay tự động, kể cả khi dòng đó đang chờ lượt đầu tiên."""
    with _client() as client:
        client.post("/projects", json=PROJECT)
        batch_id = _v2_rows(client, "projects")[0]["external_batch_id"]

        response = client.post(f"/outbox/{batch_id}/resend")
        assert response.status_code == 409
        assert response.json()["error_code"] == "V2_DELIVERY_NOT_ENABLED"

        row = client.get(f"/outbox/{batch_id}").json()
        assert row["http_status"] is None
        assert row["attempts"] == 0


def test_replay_stale_on_an_explicit_v2_batch_id_is_rejected(crm_app, backend):
    """`replay-stale` — khác `resend` — vẫn KHÔNG hỗ trợ v2 (xem
    `crud._reject_v2_delivery`: khái niệm "payload cũ dưới batch id mới" chưa có
    tương đương v2 đã thiết kế)."""
    with _client() as client:
        client.post("/projects", json=PROJECT)
        batch_id = _v2_rows(client, "projects")[0]["external_batch_id"]

        response = client.post("/outbox/replay-stale", json={"external_batch_id": batch_id})
        assert response.status_code == 409
        assert response.json()["error_code"] == "V2_DELIVERY_NOT_ENABLED"


def test_repeated_resend_attempts_on_a_v2_row_are_all_rejected_and_send_nothing(crm_app, backend):
    """Gọi `resend` nhiều lần liên tiếp trên cùng một dòng v2 — MỖI lần đều 409,
    không lần nào chạm mạng (không có "lần đầu thành công, các lần sau replayed"
    như một dòng v1 thật; xem test cùng tên đã bỏ 'Phase C.5b')."""
    with _client() as client:
        client.post("/projects", json=PROJECT)
        batch_id = _v2_rows(client, "projects")[0]["external_batch_id"]
        request_count = len(backend.requests)

        for _ in range(3):
            response = client.post(f"/outbox/{batch_id}/resend")
            assert response.status_code == 409
            assert response.json()["error_code"] == "V2_DELIVERY_NOT_ENABLED"

        row = client.get(f"/outbox/{batch_id}").json()
        assert row["attempts"] == 0
        assert len(backend.requests) == request_count, "không có request nào rời khỏi máy"


def test_a_v2_row_http_status_is_still_null_immediately_after_write(crm_app, backend):
    """Phase C: ghi outbox v2 KHÔNG gửi trong cùng request — `http_status` giữ NULL
    ngay sau khi commit. Không còn "mãi mãi" (Phase C.5 thêm vòng relay tự động
    gửi nó ở lượt kế tiếp — xem `tests/test_relay.py`); vòng nền thật ngủ
    `MINICRM_RELAY_INTERVAL_SECONDS` trước lượt đầu (xem `app/relay.py`), lâu hơn
    nhiều so với thời gian một test chạy, nên khẳng định này vẫn đúng ở đây."""
    with _client() as client:
        client.post("/areas", json=AREA)
        row = client.get("/outbox", params={"entity": "areas"}).json()["items"][0]
    assert row["http_status"] is None
    assert row["attempts"] == 0
    assert row["last_error"] is None


def test_v2_capture_is_committed_before_relay_delivery(crm_app, backend):
    """The canonical v2 intent is committed before RelayLoop sends anything."""
    backend.time_out()
    with _client() as client:
        response = client.post("/units", json=UNIT)
        assert response.status_code == 201
        assert response.json()["sync"]["status"] == "sync_pending"
        assert backend.requests == []

        assert _v2_total(client, "units_v2") == 1
        v2_row = _v2_rows(client, "units_v2")[0]
        assert v2_row["http_status"] is None


def test_canonical_unit_write_is_labelled_units_v2_not_the_v1_entity_string(crm_app, backend):
    """Canonical Unit writes produce a `units_v2` outbox row, never bare
    `"units"` — `_reject_v2_delivery` checks `row["entity"] in
    sync_client.V2_CAPTURE_ENTITIES` to decide whether `resend` may touch it. A
    row mislabelled as v1 would let `resend` slip through `_reject_v2_delivery`
    and try to mark mirrored state on the wrong row shape."""
    with _client() as client:
        response = client.post("/units", json=UNIT)
        batch_id = response.json()["sync"]["external_batch_id"]
        assert _v2_rows(client, "units_v2")[0]["external_batch_id"] == batch_id

        resend = client.post(f"/outbox/{batch_id}/resend")
        assert resend.status_code == 409
        assert resend.json()["error_code"] == "V2_DELIVERY_NOT_ENABLED"
