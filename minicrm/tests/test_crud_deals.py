"""CRUD giao dịch: thứ tự căn-trước-giao-dịch, và chốt A4 nhìn từ phía nguồn.

Hai điều được canh kỹ nhất ở đây:

**Giao dịch chỉ đi sau căn của nó.** Backend tra căn theo
`(source_instance_id, external_unit_id)` trong bảng `units` CỦA NÓ, nên "căn có
thật trong Mini CRM" là chưa đủ — căn phải đã LÊN TỚI backend. Cả hai điều kiện
được kiểm trước khi ghi, nên không có request nào rời khỏi máy và không có bản
ghi nào bị commit rồi bỏ rơi.

**`reserved → sold` giữ nguyên `reserved_at`.** Không phải vì có ai nhớ chép nó
sang, mà vì phong bì được dựng lại TỪ DÒNG ĐÃ GHI sau khi PATCH. Test ở đây khoá
đúng tính chất đó lại, vì nó là thứ dễ mất nhất khi có người "tối ưu" bằng cách
dựng phong bì thẳng từ thân request.

Backend là GIẢ. Kết luận về hành vi backend nằm ở `test_real_backend_sync.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from app import relay as relay_module
from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_AUTH_HEADER, TEST_AREA_NAME, TEST_UNIT_TYPE, sync_url

UNIT = {"area_name": TEST_AREA_NAME, "unit_type": TEST_UNIT_TYPE, "unit_code": "B1-01-01", "unit_status": "available"}

RESERVED_AT = "2026-08-01T09:00:00+00:00"
SOLD_AT = "2026-08-05T14:30:00+00:00"
LOST_AT = "2026-08-06T08:00:00+00:00"


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


def _relay():
    import asyncio

    return asyncio.run(relay_module.relay_tick())


def _seed_unit(client, **overrides):
    """Tạo một căn và chạy một lượt RelayLoop — điều kiện tiên quyết của Deal."""
    body = client.post("/units", json={**UNIT, **overrides}).json()
    assert body["sync"]["status"] == "sync_pending"
    import asyncio

    asyncio.run(relay_module.relay_tick())
    return body["record"]["external_id"]


def _rows(url, table):
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY created_at")).mappings()]
    finally:
        engine.dispose()


# --- 6. Tạo giao dịch sau khi có căn ----------------------------------------


def test_a_deal_created_after_its_unit_is_accepted_and_pushed(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        response = client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "lead"})

    assert response.status_code == 201
    body = response.json()
    assert body["record"]["external_id"] == "D-0001"
    assert body["record"]["source_revision"] == 1
    assert body["sync"]["status"] == "sync_pending"
    _relay()

    envelope = backend.envelopes[1]
    assert backend.requests[1]["url"] == "http://api:8000/api/v1/sync/deals"
    assert envelope["schema_version"] == 2
    assert envelope["project_ref"] == {"external_project_id": "BOOTSTRAP-PROJECT"}
    assert envelope["records"][0]["entity"] == "deal"
    assert envelope["records"][0]["payload"]["external_unit_id"] == unit_id
    assert envelope["records"][0]["payload"]["deal_status"] == "lead"


def test_deal_ids_use_their_own_sequence(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        first = client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "lead"}).json()
        client.patch(f"/deals/{first['record']['external_id']}", json={"deal_status": "lost", "lost_at": LOST_AT})
        second = client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "qualified"}).json()

    assert first["record"]["external_id"] == "D-0001"
    assert second["record"]["external_id"] == "D-0002"


# --- 7. Giao dịch TRƯỚC căn -------------------------------------------------


def test_a_deal_for_an_unknown_unit_is_rejected_locally_and_never_sent(crm_app, backend):
    with _client() as client:
        response = client.post("/deals", json={"external_unit_id": "U-9999", "deal_status": "lead"})

    assert response.status_code == 422
    assert response.json()["error_code"] == "UNIT_NOT_FOUND"
    assert response.json()["sent"] is False
    assert backend.requests == [], "không một request nào được phép rời khỏi máy"
    assert _rows(crm_app, "crm_deals") == []


def test_a_deal_for_an_unmirrored_unit_is_rejected_before_sending(crm_app, backend):
    """Căn CÓ cục bộ nhưng chưa lên tới backend.

    Cho tạo rồi chặn ở bước gửi nghe mềm mại hơn, nhưng nó tạo ra một bản ghi đã
    commit KHÔNG có dòng outbox nào — nên không có gì để `resend`. Đó đúng là
    "thay đổi đã commit bị âm thầm bỏ rơi" mà Phase 4 cấm.
    """
    backend.fail_with(422, {"detail": {"error_code": "UNKNOWN_AREA"}})
    with _client() as client:
        client.post("/units", json=UNIT)
        assert client.get("/units/U-0001").json()["mirrored_revision"] is None

        backend.accept()
        response = client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})

    assert response.status_code == 409
    assert response.json()["error_code"] == "UNIT_NOT_MIRRORED"
    assert len(backend.requests) == 0, "lô Unit và Deal đều chờ RelayLoop"
    assert _rows(crm_app, "crm_deals") == []


def test_the_deal_becomes_possible_once_the_unit_batch_is_relayed(crm_app, backend):
    """Deal becomes possible after the canonical Unit v2 row is relayed."""
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        assert client.post(f"/outbox/{batch_id}/resend").status_code == 409
    _relay()
    with _client() as client:
        response = client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})
    assert response.status_code == 201


def test_a_deal_on_a_tombstoned_unit_is_rejected(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        client.delete(f"/units/{unit_id}")
        response = client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "lead"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "UNIT_TOMBSTONED"


# --- 8. Giao dịch reserved --------------------------------------------------


def test_a_reserved_deal_carries_its_reserved_at(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        response = client.post(
            "/deals", json={"external_unit_id": unit_id, "deal_status": "reserved", "reserved_at": RESERVED_AT}
        )
    _relay()

    assert response.status_code == 201
    payload = backend.envelopes[1]["records"][0]["payload"]
    assert payload["deal_status"] == "reserved"
    assert payload["reserved_at"].startswith("2026-08-01T09:00:00")


# --- 9. reserved → sold: chốt A4 --------------------------------------------


def test_reserved_to_sold_preserves_reserved_at_in_the_outgoing_payload(crm_app, backend):
    """KỊCH BẢN THEN CHỐT của chốt A4.

    PATCH chỉ mang `deal_status` và `sold_at`. Phong bì gửi đi vẫn phải mang
    `reserved_at` — nếu không, backend từ chối bằng `HISTORY_TIMESTAMP_DROPPED`
    và ngày đặt cọc biến mất khỏi lịch sử.
    """
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post(
            "/deals", json={"external_unit_id": unit_id, "deal_status": "reserved", "reserved_at": RESERVED_AT}
        )
        response = client.patch("/deals/D-0001", json={"deal_status": "sold", "sold_at": SOLD_AT})
    _relay()

    body = response.json()
    assert body["record"]["source_revision"] == 2
    assert body["record"]["reserved_at"].startswith("2026-08-01T09:00:00")

    payload = backend.envelopes[2]["records"][0]["payload"]
    assert payload["deal_status"] == "sold"
    assert payload["reserved_at"].startswith("2026-08-01T09:00:00")
    assert payload["sold_at"].startswith("2026-08-05T14:30:00")


def test_reserved_to_lost_preserves_reserved_at_too(crm_app, backend):
    """Không có mốc nào là trường hợp đặc biệt: mất `reserved_at` khi chuyển sang
    `lost` cũng làm hỏng lịch sử y như khi chuyển sang `sold`."""
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post(
            "/deals", json={"external_unit_id": unit_id, "deal_status": "reserved", "reserved_at": RESERVED_AT}
        )
        client.patch("/deals/D-0001", json={"deal_status": "lost", "lost_at": LOST_AT})
    _relay()

    payload = backend.envelopes[2]["records"][0]["payload"]
    assert payload["deal_status"] == "lost"
    assert payload["reserved_at"].startswith("2026-08-01T09:00:00")
    assert payload["lost_at"].startswith("2026-08-06T08:00:00")


def test_every_deal_payload_states_all_three_history_moments(crm_app, backend):
    """Cả ba mốc LUÔN có mặt, `null` tường minh khi chưa có giá trị.

    Bản ghi này là `full`, nên với backend một mốc VẮNG MẶT trong khi bản sao đang
    giữ giá trị là đánh rơi lịch sử. `null` tường minh thì lại là một khẳng định
    hợp lệ. Mini CRM luôn muốn nói cái thứ hai.
    """
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "lead"})
    _relay()

    payload = backend.envelopes[1]["records"][0]["payload"]
    assert set(payload) == {"external_unit_id", "deal_status", "reserved_at", "sold_at", "lost_at"}
    assert payload["reserved_at"] is None
    assert payload["sold_at"] is None
    assert payload["lost_at"] is None


# --- 10. Trạng thái/mốc mâu thuẫn -------------------------------------------


def test_a_reserved_deal_without_reserved_at_is_rejected_before_sending(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        response = client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "reserved"})

    assert response.status_code == 422
    assert response.json()["error_code"] == "MISSING_STATUS_TIMESTAMP"
    assert len(backend.requests) == 1, "chỉ lô của căn; lô giao dịch không được gửi"


def test_sold_before_reserved_is_rejected_before_sending(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        response = client.post(
            "/deals",
            json={
                "external_unit_id": unit_id,
                "deal_status": "sold",
                "reserved_at": SOLD_AT,
                "sold_at": RESERVED_AT,
            },
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "SOLD_BEFORE_RESERVED"
    assert len(backend.requests) == 1


def test_an_unknown_deal_status_is_rejected_by_the_schema(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        response = client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "da_dat_coc"})
    assert response.status_code == 422
    assert len(backend.requests) == 1


def test_a_patch_that_would_drop_a_required_timestamp_is_rejected(crm_app, backend):
    """`sold` mà xoá `sold_at` bằng null tường minh — trạng thái kết quả không
    tồn tại được, nên nó bị chặn TRƯỚC khi ghi."""
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post(
            "/deals",
            json={
                "external_unit_id": unit_id,
                "deal_status": "sold",
                "reserved_at": RESERVED_AT,
                "sold_at": SOLD_AT,
            },
        )
        before = len(backend.requests)
        response = client.patch("/deals/D-0001", json={"sold_at": None})

    assert response.status_code == 422
    assert response.json()["error_code"] == "MISSING_STATUS_TIMESTAMP"
    assert len(backend.requests) == before


# --- 11. Tombstone giao dịch -------------------------------------------------


def test_deleting_a_deal_is_a_soft_delete_and_sends_a_delete_record(crm_app, backend):
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "lead"})
        response = client.delete("/deals/D-0001")
    _relay()

    body = response.json()
    assert response.status_code == 200
    assert body["record"]["deleted_at"] is not None
    assert body["record"]["source_revision"] == 2

    record = backend.envelopes[2]["records"][0]
    assert record["operation"] == "delete"
    assert record["external_id"] == "D-0001"
    assert "payload" not in record

    # Dòng vẫn còn trong DB — không có lệnh DELETE vật lý nào chạy.
    rows = _rows(crm_app, "crm_deals")
    assert len(rows) == 1
    assert rows[0]["deleted_at"] is not None


# --- 12. Một giao dịch giữ mỗi căn ------------------------------------------


def test_a_second_holding_deal_on_the_same_unit_is_rejected(crm_app, backend):
    """Soi gương `uq_deals_active_per_unit` của backend."""
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post(
            "/deals", json={"external_unit_id": unit_id, "deal_status": "reserved", "reserved_at": RESERVED_AT}
        )
        before = len(backend.requests)
        response = client.post(
            "/deals", json={"external_unit_id": unit_id, "deal_status": "sold", "sold_at": SOLD_AT}
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "UNIT_ALREADY_HELD"
    assert "D-0001" in response.json()["message"]
    assert len(backend.requests) == before


def test_a_lost_deal_does_not_block_a_new_holding_deal(crm_app, backend):
    """Lịch sử KHÔNG bị chặn: `lost` nằm ngoài tập đang giữ."""
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post("/deals", json={"external_unit_id": unit_id, "deal_status": "lost", "lost_at": LOST_AT})
        response = client.post(
            "/deals", json={"external_unit_id": unit_id, "deal_status": "reserved", "reserved_at": RESERVED_AT}
        )
    assert response.status_code == 201


def test_promoting_the_same_deal_from_reserved_to_sold_is_not_a_conflict(crm_app, backend):
    """Chính giao dịch đang giữ được phép đổi trạng thái — nó không tự chặn mình."""
    with _client() as client:
        unit_id = _seed_unit(client)
        client.post(
            "/deals", json={"external_unit_id": unit_id, "deal_status": "reserved", "reserved_at": RESERVED_AT}
        )
        response = client.patch("/deals/D-0001", json={"deal_status": "sold", "sold_at": SOLD_AT})
    assert response.status_code == 200


def test_reading_or_writing_an_unknown_deal_returns_404(crm_app, backend):
    with _client() as client:
        assert client.get("/deals/D-9999").status_code == 404
        assert client.patch("/deals/D-9999", json={"deal_status": "lead"}).status_code == 404
        assert client.delete("/deals/D-9999").status_code == 404
    assert backend.requests == []
