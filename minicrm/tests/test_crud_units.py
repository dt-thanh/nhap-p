"""CRUD căn hộ, cò đẩy tự động, và thứ tự GHI → COMMIT → GỬI.

Backend ở đây là GIẢ (`FakeBackend`), nên những test này KHÔNG chứng minh gì về
hành vi của backend. Việc của chúng là chứng minh phía Mini CRM: bản ghi được ghi
đúng, phiên bản tăng đúng, phong bì dựng đúng, dòng outbox mang đúng nguyên văn,
và một lần đẩy hỏng không bao giờ làm biến mất một thay đổi đã commit.

Kết luận về backend nằm ở `test_real_backend_sync.py`, chạy qua container thật.
"""

from __future__ import annotations

import sqlalchemy as sa
from app import relay as relay_module
from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_AUTH_HEADER, TEST_AREA_NAME, TEST_UNIT_TYPE, sync_url

UNIT = {"area_name": TEST_AREA_NAME, "unit_type": TEST_UNIT_TYPE, "unit_code": "B1-01-01", "unit_status": "available"}


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


def _relay():
    import asyncio

    return asyncio.run(relay_module.relay_tick())


def _rows(url, table):
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY created_at")).mappings()]
    finally:
        engine.dispose()


# --- 1. Tạo căn --------------------------------------------------------------


def test_create_writes_the_local_row_and_pushes_it(crm_app, backend):
    with _client() as client:
        response = client.post("/units", json=UNIT)

    assert response.status_code == 201
    body = response.json()
    assert body["record"]["external_id"] == "U-0001"
    assert body["record"]["source_revision"] == 1
    assert body["sync"]["status"] == "sync_pending"
    assert body["sync"]["http_status"] is None

    rows = _rows(crm_app, "crm_units")
    assert len(rows) == 1
    assert rows[0]["unit_code"] == "B1-01-01"
    assert rows[0]["source_revision"] == 1


def test_external_ids_come_from_a_sequence_and_are_zero_padded(crm_app, backend):
    with _client() as client:
        first = client.post("/units", json=UNIT).json()["record"]["external_id"]
        second = client.post("/units", json={**UNIT, "unit_code": "B1-01-02"}).json()["record"]["external_id"]
    assert (first, second) == ("U-0001", "U-0002")


def test_a_tombstoned_external_id_is_never_reissued(crm_app, backend):
    """Giả định A1: danh tính bền vững trọn đời. Dãy không lùi, kể cả sau khi xoá."""
    with _client() as client:
        client.post("/units", json=UNIT)
        client.delete("/units/U-0001")
        # Cùng mã căn — hợp lệ, vì `uq_crm_units_live_code` chỉ xét căn còn sống.
        reborn = client.post("/units", json=UNIT).json()["record"]["external_id"]
    assert reborn == "U-0002"


# --- 1b. Xung đột khoá tự nhiên (`uq_crm_units_live_code`) -------------------
#
# Trước khi sửa: `create_unit` không bọc `IntegrityError`, nên một mã căn trùng
# TRONG CÙNG phân khu ném lỗi thô, FastAPI trả 500 không `error_code`. Bốn test
# dưới đây khoá lại đúng ranh giới của chỉ mục từng phần
# (`WHERE deleted_at IS NULL`, gồm cả `area_name`+`unit_type`+`unit_code`).


def test_a_duplicate_live_unit_code_in_the_same_area_is_a_clean_409(crm_app, backend):
    with _client() as client:
        first = client.post("/units", json=UNIT)
        assert first.status_code == 201

        second = client.post("/units", json=UNIT)

    assert second.status_code == 409
    body = second.json()
    assert body["error_code"] == "UNIT_NATURAL_KEY_CONFLICT"

    rows = _rows(crm_app, "crm_units")
    assert len(rows) == 1, "lần POST thứ hai không được tạo dòng nào"


def test_the_same_unit_code_in_a_different_area_is_allowed(crm_app, backend):
    with _client() as client:
        first = client.post("/units", json=UNIT)
        assert first.status_code == 201

        other_area = client.post(
            "/areas",
            json={
                "external_project_id": "BOOTSTRAP-PROJECT",
                "area_name": "DEMO Toà B2",
                "unit_type": TEST_UNIT_TYPE,
                "bedrooms": 2,
                "area_sqm": 68.5,
                "total_units": 999,
            },
        )
        assert other_area.status_code == 201
        other_area_id = other_area.json()["record"]["external_id"]

        # Cùng `unit_code`, khác phân khu — khoá tự nhiên gồm cả area_name/
        # unit_type, nên đây KHÔNG phải một xung đột.
        second = client.post(
            "/units",
            json={"external_area_id": other_area_id, "unit_code": UNIT["unit_code"], "unit_status": "available"},
        )

    assert second.status_code == 201
    rows = _rows(crm_app, "crm_units")
    assert len(rows) == 2


def test_reusing_a_tombstoned_units_code_in_the_same_area_is_allowed(crm_app, backend):
    """Bổ sung cho `test_a_tombstoned_external_id_is_never_reissued` ở trên —
    test đó chứng minh `external_id` không lặp lại; test này chứng minh riêng
    NHÁNH 409 mới không hồi quy: sau khi xoá mềm, POST lại đúng mã không rơi
    vào `UNIT_NATURAL_KEY_CONFLICT` (chỉ mục CHỈ xét `deleted_at IS NULL`)."""
    with _client() as client:
        client.post("/units", json=UNIT)
        client.delete("/units/U-0001")

        reborn = client.post("/units", json=UNIT)

    assert reborn.status_code == 201
    rows = _rows(crm_app, "crm_units")
    assert len(rows) == 2
    assert sum(1 for r in rows if r["deleted_at"] is None) == 1


def test_concurrent_duplicate_unit_code_creation_is_still_a_clean_409(crm_app, backend):
    """Hai request TUẦN TỰ (TestClient đồng bộ không mô phỏng được song song
    thật ở tầng HTTP) nhưng cùng target đúng dòng đã commit — đủ để chứng minh
    nhánh `IntegrityError` chạy trên đường race thật, không chỉ trên đường
    "đọc trước rồi mới ghi" mà một test tuần tự có thể vô tình bỏ qua nếu
    handler chỉ kiểm tra tồn tại bằng SELECT trước INSERT (handler này không
    làm vậy — nó dựa hoàn toàn vào ràng buộc DB)."""
    with _client() as client:
        results = [client.post("/units", json=UNIT) for _ in range(2)]

    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 409]
    conflict = next(r for r in results if r.status_code == 409)
    assert conflict.json()["error_code"] == "UNIT_NATURAL_KEY_CONFLICT"
    assert len(_rows(crm_app, "crm_units")) == 1


def test_the_pushed_envelope_matches_the_contract_shape(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
    _relay()

    envelope = backend.envelopes[0]
    assert envelope["schema_version"] == 2
    assert envelope["sync_mode"] == "incremental"
    assert envelope["records"][0]["entity"] == "unit"
    assert envelope["records"][0]["operation"] == "upsert"
    assert envelope["records"][0]["source_revision"] == 1
    assert envelope["project_ref"] == {"external_project_id": "BOOTSTRAP-PROJECT"}
    assert envelope["records"][0]["payload"]["area_ref"] == {"external_area_id": "BOOTSTRAP-AREA"}
    assert backend.requests[0]["url"] == "http://api:8000/api/v1/sync/units"
    assert backend.requests[0]["api_key"] == "afsk_test_key"


def test_the_outbox_keeps_the_payload_and_the_response_verbatim(crm_app, backend):
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        row = client.get(f"/outbox/{batch_id}").json()

    assert row["entity"] == "units_v2"
    assert row["http_status"] is None
    assert row["attempts"] == 0
    assert row["payload"]["records"][0]["external_id"] == "U-0001"
    assert row["payload"]["schema_version"] == 2
    assert row["response"] is None


def test_every_envelope_carries_the_synthetic_label(crm_app, backend):
    """Nhãn đi cùng SẢN PHẨM. Một hệ thống có CRUD đầy đủ và một vòng HTTP xanh
    trông thuyết phục hơn hẳn một file JSON — và vẫn không chứng minh được gì."""
    with _client() as client:
        client.post("/units", json=UNIT)
        batch_id = client.post("/units", json={**UNIT, "unit_code": "B1-01-02"}).json()["sync"]["external_batch_id"]
        payload = client.get(f"/outbox/{batch_id}").json()["payload"]
    assert "SYNTHETIC" in payload["_comment"]


# --- 2. Sửa căn --------------------------------------------------------------


def test_update_increments_the_revision_and_pushes_the_full_state(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        response = client.patch("/units/U-0001", json={"unit_status": "reserved"})
    _relay()

    body = response.json()
    assert response.status_code == 200
    assert body["record"]["source_revision"] == 2
    assert body["record"]["unit_status"] == "reserved"
    # Trường KHÔNG gửi trong PATCH vẫn còn nguyên, và vẫn đi trong phong bì.
    assert body["record"]["unit_code"] == "B1-01-01"

    payload = backend.envelopes[1]["records"][0]["payload"]
    assert payload["unit_code"] == "B1-01-01"
    assert payload["unit_status"] == "reserved"
    assert backend.envelopes[1]["records"][0]["source_revision"] == 2


def test_update_never_creates_a_second_local_row(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.patch("/units/U-0001", json={"unit_status": "reserved"})
        client.patch("/units/U-0001", json={"unit_status": "sold"})

    rows = _rows(crm_app, "crm_units")
    assert len(rows) == 1
    assert rows[0]["source_revision"] == 3


def test_an_empty_patch_is_rejected(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        response = client.patch("/units/U-0001", json={})
    assert response.status_code == 422
    assert response.json()["error_code"] == "EMPTY_PATCH"
    assert len(backend.requests) == 0, "PATCH rỗng không được gửi gì đi"


def test_an_unknown_unit_status_is_rejected_by_the_schema(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        response = client.patch("/units/U-0001", json={"unit_status": "con_trong"})
    assert response.status_code == 422
    assert len(backend.requests) == 0


def test_patching_a_tombstoned_unit_is_rejected(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.delete("/units/U-0001")
        response = client.patch("/units/U-0001", json={"unit_status": "available"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "RECORD_TOMBSTONED"


# --- 3. Xoá căn --------------------------------------------------------------


def test_delete_soft_deletes_locally_and_sends_a_delete_record(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        response = client.delete("/units/U-0001")
    _relay()

    body = response.json()
    assert response.status_code == 200
    assert body["record"]["deleted_at"] is not None
    assert body["record"]["source_revision"] == 2

    record = backend.envelopes[1]["records"][0]
    assert record["operation"] == "delete"
    assert record["external_id"] == "U-0001"
    assert record["source_revision"] == 2
    # Hợp đồng CẤM thân payload trên lệnh xoá.
    assert "payload" not in record


def test_a_tombstoned_unit_disappears_from_the_default_listing(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.delete("/units/U-0001")
        assert client.get("/units").json() == []
        assert len(client.get("/units", params={"include_deleted": True}).json()) == 1
        # Bản ghi vẫn ĐỌC ĐƯỢC theo id: xoá mềm giữ lại vết đã từng tồn tại.
        assert client.get("/units/U-0001").status_code == 200


def test_deleting_twice_is_rejected(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.delete("/units/U-0001")
        response = client.delete("/units/U-0001")
    assert response.status_code == 409
    assert response.json()["error_code"] == "ALREADY_TOMBSTONED"


def test_reading_or_writing_an_unknown_unit_returns_404(crm_app, backend):
    with _client() as client:
        assert client.get("/units/U-9999").status_code == 404
        assert client.patch("/units/U-9999", json={"unit_status": "sold"}).status_code == 404
        assert client.delete("/units/U-9999").status_code == 404
    assert backend.requests == []


# --- Đẩy hỏng: thay đổi cục bộ KHÔNG được biến mất --------------------------


def test_a_rejected_push_leaves_the_local_change_committed_and_visible(crm_app, backend):
    """ĐIỀU KIỆN CHẶN của Phase 4. Một lô bị từ chối không được làm mất dữ liệu
    cục bộ, và cũng không được biến mất khỏi sổ gửi đi."""
    with _client() as client:
        response = client.post("/units", json=UNIT)
        body = response.json()

        # Thao tác CỤC BỘ thành công — mã HTTP nói đúng điều đó.
        assert response.status_code == 201
        assert body["sync"]["status"] == "sync_pending"
        assert body["sync"]["http_status"] is None

        row = client.get(f"/outbox/{body['sync']['external_batch_id']}").json()
        assert row["http_status"] is None
        assert row["attempts"] == 0

        # Căn vẫn còn, và tự khai là chưa lên tới backend.
        unit = client.get("/units/U-0001").json()
        assert unit["source_revision"] == 1
        assert unit["mirrored_revision"] is None

    backend.fail_with(422, {"detail": {"error_code": "UNKNOWN_AREA"}})
    _relay()
    with _client() as client:
        row = client.get(f"/outbox/{body['sync']['external_batch_id']}").json()
    assert row["http_status"] == 422
    assert row["response"]["detail"]["error_code"] == "UNKNOWN_AREA"
    assert row["attempts"] == 1


def test_a_timeout_is_reported_as_pending_not_failed(crm_app, backend):
    """`sync_failed` và `sync_pending` tách nhau vì hai bên cần hai hành động khác
    hẳn: một bên sửa dữ liệu rồi gửi lại, bên kia KHÔNG biết lô đã tới hay chưa."""
    with _client() as client:
        body = client.post("/units", json=UNIT).json()
        assert body["sync"]["status"] == "sync_pending"
        assert body["sync"]["http_status"] is None

        row = client.get(f"/outbox/{body['sync']['external_batch_id']}").json()
        # `http_status` giữ NULL: ghi một mã bịa ra là khẳng định một điều ta
        # không biết — lô có thể đã tới nơi và đã được xử lý xong.
        assert row["http_status"] is None
        assert row["sent_at"] is None
        assert row["attempts"] == 0
        assert row["last_error"] is None

    backend.time_out()
    _relay()
    with _client() as client:
        row = client.get(f"/outbox/{body['sync']['external_batch_id']}").json()
    assert row["attempts"] == 1
    assert "Hết thời gian chờ" in row["last_error"]


def test_the_mirror_stamp_only_moves_on_a_successful_push(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
    _relay()
    with _client() as client:
        assert client.get("/units/U-0001").json()["mirrored_revision"] == 1

        backend.fail_with(422, {"detail": {}})
        client.patch("/units/U-0001", json={"unit_status": "sold"})
    _relay()

    with _client() as client:
        unit = client.get("/units/U-0001").json()
        assert unit["source_revision"] == 2
        assert unit["mirrored_revision"] == 1, "phiên bản 2 chưa lên tới backend"


# --- Phase 5: phản hồi phải mô tả ĐÚNG lời gọi này, không phải trạng thái mới nhất


def test_the_response_record_matches_the_revision_its_own_batch_carried(crm_app, backend):
    """`record` và `sync` trong CÙNG một thân phản hồi phải nói về CÙNG một sự việc.

    Trước Phase 5, mọi thao tác ghi đọc LẠI dòng sau khi commit rồi trả về kết quả
    đọc đó. Với một request đơn lẻ thì không phân biệt được; với hai PATCH song
    song thì request thứ nhất commit ở revision 2, đọc lại, và thấy revision 3 do
    request thứ hai vừa ghi — nên nó khai revision 3 trong khi lô nó vừa gửi mang
    revision 2. Không có gì trong phản hồi cho biết điều đó.

    Test này khoá bất biến lại ở mức một request, nơi nó kiểm được rẻ và tất định;
    kịch bản song song thật nằm ở `test_real_failure_windows.py`.
    """
    with _client() as client:
        created = client.post("/units", json=UNIT).json()
        created_payload = client.get(f"/outbox/{created['sync']['external_batch_id']}").json()["payload"]
        updated = client.patch("/units/U-0001", json={"unit_status": "reserved"}).json()
        updated_payload = client.get(f"/outbox/{updated['sync']['external_batch_id']}").json()["payload"]

    for body, envelope in ((created, created_payload), (updated, updated_payload)):
        record = envelope["records"][0]
        assert body["record"]["source_revision"] == record["source_revision"]
        assert body["record"]["external_id"] == record["external_id"]
        assert body["sync"]["external_batch_id"] == envelope["external_batch_id"]


def test_the_response_reports_the_mirror_stamp_of_its_own_push(crm_app, backend):
    """Dấu mirrored trong phản hồi trả lời đúng một câu: "lô mang revision NÀY đã
    tới backend chưa" — không phải "dòng này đã đồng bộ tới đâu"."""
    with _client() as client:
        created = client.post("/units", json=UNIT).json()
    _relay()
    with _client() as client:
        assert client.get("/units/U-0001").json()["mirrored_revision"] == 1

        backend.fail_with(422, {"detail": {}})
        failed = client.patch("/units/U-0001", json={"unit_status": "sold"}).json()
    _relay()

    assert failed["record"]["source_revision"] == 2
    assert failed["record"]["mirrored_revision"] == 1, "revision 2 chưa tới nơi"
    assert failed["sync"]["status"] == "sync_pending"


def test_the_delete_response_describes_the_tombstoned_row(crm_app, backend):
    """Xoá mềm cũng đi qua `RETURNING`, không đọc lại — cùng một bất biến."""
    with _client() as client:
        client.post("/units", json=UNIT)
        deleted = client.delete("/units/U-0001").json()
        delete_payload = client.get(f"/outbox/{deleted['sync']['external_batch_id']}").json()["payload"]

    record = delete_payload["records"][0]
    assert deleted["record"]["source_revision"] == record["source_revision"] == 2
    assert deleted["record"]["deleted_at"] is not None
    assert deleted["record"]["mirrored_revision"] is None
