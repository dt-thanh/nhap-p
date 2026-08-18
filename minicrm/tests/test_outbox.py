"""Sổ gửi đi: tra, gửi lại, phát lại bản cũ.

Điều được canh kỹ nhất ở đây là hai đường KHÔNG bị lẫn vào nhau:

    resend        ĐÚNG batch id cũ, payload nguyên văn  ⇒ kiểm tính bất biến của LÔ
    replay-stale  batch id MỚI, payload và revision CŨ   ⇒ kiểm thứ tự PHIÊN BẢN

Dùng lại batch id cũ cho đường thứ hai sẽ khiến nó không bao giờ chạm tới tầng so
phiên bản của backend, và phép thử `skip_stale` sẽ luôn "đạt" mà chẳng kiểm gì.

`FakeBackend` ở đây KHÔNG mô phỏng tầng so phiên bản (xem docstring của nó), nên
những test này chứng minh MINI CRM gửi đúng thứ gì — không phải backend trả lời
thế nào. Kết luận về `skip_stale` thật nằm ở `test_real_backend_sync.py`.
"""

from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_AUTH_HEADER, TEST_AREA_NAME, TEST_UNIT_TYPE

UNIT = {"area_name": TEST_AREA_NAME, "unit_type": TEST_UNIT_TYPE, "unit_code": "B1-01-01", "unit_status": "available"}


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


# --- Tra sổ ------------------------------------------------------------------


def test_the_outbox_lists_batches_newest_first(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.patch("/units/U-0001", json={"unit_status": "reserved"})
        # `entity=units`: từ Phase C, một lần ghi Unit CÒN tạo thêm một dòng
        # outbox v2 riêng (entity="units_v2", KHÔNG gửi — xem `crud._capture_v2`).
        # Lọc theo entity để test này chỉ nói về sổ gửi đi v1, đúng thứ nó kiểm.
        listing = client.get("/outbox", params={"entity": "units"}).json()

    assert listing["total"] == 2
    assert [item["entity"] for item in listing["items"]] == ["units", "units"]
    assert all(item["http_status"] == 202 for item in listing["items"])
    assert all(item["record_count"] == 1 for item in listing["items"])


def test_the_listing_omits_payload_and_response(crm_app, backend):
    """Một lô có thể mang tới 5000 bản ghi. Kèm payload vào danh sách sẽ biến một
    lần tra trạng thái thành vài megabyte JSON, và người vận hành sẽ thôi tra."""
    with _client() as client:
        client.post("/units", json=UNIT)
        item = client.get("/outbox").json()["items"][0]
    assert item["payload"] is None
    assert item["response"] is None


def test_the_detail_route_returns_both_directions_verbatim(crm_app, backend):
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        row = client.get(f"/outbox/{batch_id}").json()

    assert row["payload"]["external_batch_id"] == batch_id
    assert row["payload"]["records"][0]["external_id"] == "U-0001"
    assert row["response"]["status"] == "completed"


def test_the_outbox_can_be_filtered_by_entity(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})
        assert client.get("/outbox", params={"entity": "deals"}).json()["total"] == 1
        assert client.get("/outbox", params={"entity": "units"}).json()["total"] == 1


def test_an_unknown_batch_id_returns_404(crm_app, backend):
    with _client() as client:
        assert client.get("/outbox/mc-units-nope").status_code == 404
        assert client.post("/outbox/mc-units-nope/resend").status_code == 404


# --- 4. Gửi lại đúng lô cũ ---------------------------------------------------


def test_resend_reuses_the_original_batch_id_and_payload(crm_app, backend):
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        response = client.post(f"/outbox/{batch_id}/resend")

    body = response.json()
    assert body["mode"] == "resend"
    assert body["source_batch_id"] == batch_id
    assert body["sent_batch_id"] == batch_id, "gửi lại PHẢI dùng đúng batch id cũ"
    assert backend.envelopes[0] == backend.envelopes[1], "payload phải nguyên văn, không dựng lại"


def test_resend_reuses_the_same_outbox_row(crm_app, backend):
    """KHÔNG tạo dòng thứ hai: nếu tạo, "gửi lại cùng một lô" ở phía Mini CRM lại
    thành hai lô khác nhau, và phép thử idempotency mất ý nghĩa."""
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        client.post(f"/outbox/{batch_id}/resend")
        client.post(f"/outbox/{batch_id}/resend")

        # `entity=units`: xem chú thích ở `test_the_outbox_lists_batches_newest_first`.
        assert client.get("/outbox", params={"entity": "units"}).json()["total"] == 1
        row = client.get(f"/outbox/{batch_id}").json()

    assert row["attempts"] == 3, "một lần đẩy đầu + hai lần gửi lại"


def test_resend_stores_the_latest_response(crm_app, backend):
    """Phản hồi MỚI NHẤT thắng: một lô hỏng rồi được gửi lại thành công phải đọc
    ra là đã thành công, nếu không thì sổ gửi đi báo động giả mãi mãi."""
    backend.fail_with(422, {"detail": {"error_code": "UNKNOWN_AREA"}})
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        assert client.get(f"/outbox/{batch_id}").json()["http_status"] == 422

        backend.accept()
        client.post(f"/outbox/{batch_id}/resend")
        row = client.get(f"/outbox/{batch_id}").json()

    assert row["http_status"] == 202
    assert row["last_error"] is None
    assert row["attempts"] == 2


def test_a_resent_batch_is_reported_as_replayed(crm_app, backend):
    """`FakeBackend` chỉ biết đúng một luật của hợp đồng: cùng `external_batch_id`
    ⇒ trả kết quả cũ kèm `replayed=true`."""
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        body = client.post(f"/outbox/{batch_id}/resend").json()

    assert body["sync"]["status"] == "replayed"
    assert body["sync"]["http_status"] == 200


def test_resend_does_not_create_a_second_local_record(crm_app, backend):
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        client.post(f"/outbox/{batch_id}/resend")
        assert len(client.get("/units").json()) == 1


# --- 5. Phát lại bản cũ ------------------------------------------------------


def test_replay_stale_sends_the_old_payload_under_a_new_batch_id(crm_app, backend):
    with _client() as client:
        stale_batch = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        client.patch("/units/U-0001", json={"unit_status": "sold"})

        body = client.post("/outbox/replay-stale", json={"external_batch_id": stale_batch}).json()

    assert body["mode"] == "replay_stale"
    assert body["source_batch_id"] == stale_batch
    assert body["sent_batch_id"] != stale_batch, (
        "batch id phải MỚI: dùng lại id cũ thì backend trả kết quả đã lưu và "
        "không bao giờ chạy tới tầng so phiên bản"
    )

    replayed = backend.envelopes[2]
    assert replayed["records"][0]["source_revision"] == 1, "phiên bản phải giữ nguyên bản CŨ"
    assert replayed["records"][0]["payload"]["unit_status"] == "available"


def test_replay_stale_links_back_to_the_original_batch(crm_app, backend):
    with _client() as client:
        stale_batch = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        client.patch("/units/U-0001", json={"unit_status": "sold"})
        sent = client.post("/outbox/replay-stale", json={"external_batch_id": stale_batch}).json()["sent_batch_id"]

        row = client.get(f"/outbox/{sent}").json()
    assert row["replay_of"] == stale_batch


def test_replay_stale_does_not_move_the_mirror_stamp_backwards(crm_app, backend):
    """Một lô cũ không đưa backend tới phiên bản nào mới cả — nó bị bỏ qua.

    Kéo `mirrored_revision` lùi về phiên bản của lô cũ sẽ khiến một bản ghi đã
    đồng bộ đầy đủ đột nhiên trông như đang lạc hậu.
    """
    with _client() as client:
        stale_batch = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        client.patch("/units/U-0001", json={"unit_status": "sold"})
        assert client.get("/units/U-0001").json()["mirrored_revision"] == 2

        client.post("/outbox/replay-stale", json={"external_batch_id": stale_batch})
        assert client.get("/units/U-0001").json()["mirrored_revision"] == 2


def test_replay_stale_picks_the_newest_stale_batch_when_none_is_named(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        second_batch = client.patch("/units/U-0001", json={"unit_status": "reserved"}).json()["sync"][
            "external_batch_id"
        ]
        client.patch("/units/U-0001", json={"unit_status": "sold"})

        body = client.post("/outbox/replay-stale", json={}).json()
    assert body["source_batch_id"] == second_batch


def test_replaying_a_batch_that_is_not_actually_stale_is_refused(crm_app, backend):
    """ĐIỀU KIỆN TIỀN ĐỀ phải được kiểm, không được giả định.

    Phát lại một lô KHÔNG cũ sẽ không cho `skip_stale` — nó cho `duplicate_noop`
    hoặc `conflict`. Một phép thử tự khẳng định kết quả mà không kiểm tiền đề thì
    chứng minh được số không.
    """
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        before = len(backend.requests)
        response = client.post("/outbox/replay-stale", json={"external_batch_id": batch_id})

    assert response.status_code == 409
    assert response.json()["error_code"] == "BATCH_NOT_STALE"
    assert len(backend.requests) == before


def test_replay_stale_without_any_stale_batch_is_refused(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        response = client.post("/outbox/replay-stale", json={})
    assert response.status_code == 409
    assert response.json()["error_code"] == "NO_STALE_BATCH"


def test_replay_stale_creates_its_own_outbox_row(crm_app, backend):
    with _client() as client:
        stale_batch = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
        client.patch("/units/U-0001", json={"unit_status": "sold"})
        client.post("/outbox/replay-stale", json={"external_batch_id": stale_batch})

        # `entity=units`: xem chú thích ở `test_the_outbox_lists_batches_newest_first`.
        listing = client.get("/outbox", params={"entity": "units"}).json()
    assert listing["total"] == 3
    assert sum(1 for item in listing["items"] if item["replay_of"]) == 1
