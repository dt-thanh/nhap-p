"""Cả 14 endpoint, đi qua container THẬT, mỗi cái một lượt.

Các file khác kiểm SÂU vào từng hành vi (thứ tự phiên bản, chốt lịch sử, cửa sổ
hỏng). File này kiểm RỘNG: mọi endpoint đều được gọi ít nhất một lần trên hệ đang
chạy, và mỗi lần gọi được soi bốn thứ — mã HTTP, hình dạng phản hồi, trạng thái
database, và hành vi lỗi.

Vì sao cần một lượt quét rộng riêng: một endpoint chỉ được kiểm gián tiếp (được
gọi để dựng tiền đề cho test khác) sẽ không bao giờ có ai khẳng định về HÌNH DẠNG
phản hồi của nó. `GET /deals/{id}` là ví dụ điển hình — nó được gọi suốt, nhưng
trước file này chưa test nào nói nó phải trả về những trường gì.

Điều được canh kỹ nhất ở phần lỗi: **một request bị từ chối không được để lại
thay đổi cục bộ nào.** Từ chối mà vẫn ghi một nửa là loại lỗi tệ nhất trong cả hệ
này — nó tạo ra một bản ghi không ai biết là có, và không có dòng outbox nào để
gửi nó đi.
"""

from __future__ import annotations

import uuid

import pytest
import real_env as env

pytestmark = pytest.mark.skipif(bool(env.skip_reason()), reason=env.skip_reason() or "")

UNIT_FIELDS = {
    "external_id",
    "area_name",
    "unit_type",
    "unit_code",
    "unit_status",
    "source_revision",
    "deleted_at",
    "created_at",
    "updated_at",
    "mirrored_at",
    "mirrored_revision",
    "last_sync_batch_id",
}
DEAL_FIELDS = {
    "external_id",
    "external_unit_id",
    "deal_status",
    "reserved_at",
    "sold_at",
    "lost_at",
    "source_revision",
    "deleted_at",
    "created_at",
    "updated_at",
    "mirrored_at",
    "mirrored_revision",
    "last_sync_batch_id",
}
SYNC_FIELDS = {
    "status",
    "external_batch_id",
    "http_status",
    "sync_run_id",
    "decisions",
    "projections",
    "error",
    "detail",
}
OUTBOX_FIELDS = {
    "external_batch_id",
    "entity",
    "http_status",
    "sent_at",
    "created_at",
    "attempts",
    "last_error",
    "replay_of",
    "record_count",
    "payload",
    "response",
}


@pytest.fixture(scope="module")
def walk():
    """Đi hết một vòng đời: tạo → đọc → sửa → tạo giao dịch → sửa → xoá cả hai.

    Một vòng duy nhất cho cả 14 endpoint, vì các endpoint phụ thuộc nhau thật:
    không có căn thì không có giao dịch, không có lô thì không có gì để gửi lại.
    """
    suffix = uuid.uuid4().hex[:6]
    state: dict = {}

    state["unit_create"] = env.crm(
        "POST",
        "/units",
        json={
            "area_name": env.AREA_NAME,
            "unit_type": env.UNIT_TYPE,
            "unit_code": f"EP-{suffix}",
            "unit_status": "available",
        },
    )
    unit_id = state["unit_id"] = state["unit_create"].json()["record"]["external_id"]

    state["unit_list"] = env.crm("GET", "/units", params={"limit": 1})
    state["unit_get"] = env.crm("GET", f"/units/{unit_id}")
    state["unit_patch"] = env.crm("PATCH", f"/units/{unit_id}", json={"unit_status": "reserved"})
    state["unit_batch"] = state["unit_create"].json()["sync"]["external_batch_id"]

    state["deal_create"] = env.crm(
        "POST", "/deals", json={"external_unit_id": unit_id, "deal_status": "lead"}
    )
    deal_id = state["deal_id"] = state["deal_create"].json()["record"]["external_id"]
    state["deal_list"] = env.crm("GET", "/deals")
    state["deal_get"] = env.crm("GET", f"/deals/{deal_id}")
    state["deal_patch"] = env.crm(
        "PATCH",
        f"/deals/{deal_id}",
        json={"deal_status": "reserved", "reserved_at": "2026-08-01T09:00:00+00:00"},
    )

    state["outbox_list"] = env.crm("GET", "/outbox", params={"limit": 5})
    state["outbox_get"] = env.crm("GET", f"/outbox/{state['unit_batch']}")
    state["resend"] = env.crm("POST", f"/outbox/{state['unit_batch']}/resend")
    state["replay_stale"] = env.crm("POST", "/outbox/replay-stale", json={"external_batch_id": state["unit_batch"]})

    state["deal_delete"] = env.crm("DELETE", f"/deals/{deal_id}")
    state["unit_delete"] = env.crm("DELETE", f"/units/{unit_id}")
    return state


# --- /units -------------------------------------------------------------------


def test_post_units_returns_201_with_both_halves(walk):
    assert walk["unit_create"].status_code == 201
    body = walk["unit_create"].json()
    assert set(body) == {"record", "sync"}
    assert set(body["record"]) == UNIT_FIELDS
    assert set(body["sync"]) == SYNC_FIELDS
    assert body["sync"]["status"] == "synced"


def test_get_units_returns_a_list_of_full_records(walk):
    assert walk["unit_list"].status_code == 200
    items = walk["unit_list"].json()
    assert isinstance(items, list) and items
    assert set(items[0]) == UNIT_FIELDS
    assert all(item["deleted_at"] is None for item in items), "mặc định chỉ trả căn còn sống"


def test_get_one_unit_returns_the_same_shape_as_the_list(walk):
    assert walk["unit_get"].status_code == 200
    assert set(walk["unit_get"].json()) == UNIT_FIELDS
    assert walk["unit_get"].json()["external_id"] == walk["unit_id"]


def test_patch_units_returns_200_and_advances_the_revision(walk):
    assert walk["unit_patch"].status_code == 200
    body = walk["unit_patch"].json()
    assert body["record"]["source_revision"] == 2
    assert body["record"]["unit_status"] == "reserved"
    assert body["sync"]["status"] == "synced"


def test_delete_units_returns_200_with_a_tombstoned_record(walk):
    assert walk["unit_delete"].status_code == 200
    body = walk["unit_delete"].json()
    assert body["record"]["deleted_at"] is not None
    assert env.nonzero(body["sync"]["decisions"]) == {"tombstone": 1}


# --- /deals -------------------------------------------------------------------


def test_post_deals_returns_201_with_both_halves(walk):
    assert walk["deal_create"].status_code == 201
    body = walk["deal_create"].json()
    assert set(body["record"]) == DEAL_FIELDS
    assert body["record"]["external_unit_id"] == walk["unit_id"]
    assert body["sync"]["status"] == "synced"


def test_get_deals_returns_a_list_of_full_records(walk):
    assert walk["deal_list"].status_code == 200
    items = walk["deal_list"].json()
    assert isinstance(items, list) and items
    assert set(items[0]) == DEAL_FIELDS


def test_get_one_deal_returns_the_same_shape_as_the_list(walk):
    assert walk["deal_get"].status_code == 200
    assert set(walk["deal_get"].json()) == DEAL_FIELDS
    assert walk["deal_get"].json()["external_id"] == walk["deal_id"]


def test_patch_deals_returns_200_and_carries_the_new_timestamp(walk):
    assert walk["deal_patch"].status_code == 200
    body = walk["deal_patch"].json()
    assert body["record"]["deal_status"] == "reserved"
    assert body["record"]["reserved_at"].startswith("2026-08-01T09:00:00")
    assert body["record"]["source_revision"] == 2


def test_delete_deals_returns_200_with_a_tombstoned_record(walk):
    assert walk["deal_delete"].status_code == 200
    body = walk["deal_delete"].json()
    assert body["record"]["deleted_at"] is not None
    assert env.nonzero(body["sync"]["decisions"]) == {"tombstone": 1}


# --- /outbox ------------------------------------------------------------------


def test_get_outbox_lists_batches_without_their_payloads(walk):
    assert walk["outbox_list"].status_code == 200
    body = walk["outbox_list"].json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["limit"] == 5 and len(body["items"]) <= 5
    assert body["items"][0]["payload"] is None, "danh sách KHÔNG kèm payload — xem router"


def test_get_one_outbox_batch_returns_both_directions_verbatim(walk):
    assert walk["outbox_get"].status_code == 200
    body = walk["outbox_get"].json()
    assert set(body) == OUTBOX_FIELDS
    assert body["payload"]["external_batch_id"] == walk["unit_batch"]
    assert body["response"]["sync_run_id"]


def test_resend_returns_200_and_reuses_the_batch_id(walk):
    assert walk["resend"].status_code == 200
    body = walk["resend"].json()
    assert set(body) == {"source_batch_id", "sent_batch_id", "mode", "sync"}
    assert body["mode"] == "resend"
    assert body["sent_batch_id"] == body["source_batch_id"] == walk["unit_batch"]
    assert body["sync"]["status"] == "replayed"


def test_replay_stale_returns_200_under_a_new_batch_id(walk):
    assert walk["replay_stale"].status_code == 200
    body = walk["replay_stale"].json()
    assert body["mode"] == "replay_stale"
    assert body["sent_batch_id"] != body["source_batch_id"]
    assert env.nonzero(body["sync"]["decisions"]) == {"skip_stale": 1}


# --- Hành vi lỗi: KHÔNG được để lại thay đổi một nửa -------------------------


def test_unknown_ids_return_404_on_every_read_and_write_route():
    missing_unit, missing_deal = "U-KHONG-CO", "D-KHONG-CO"
    assert env.crm("GET", f"/units/{missing_unit}").status_code == 404
    assert env.crm("PATCH", f"/units/{missing_unit}", json={"unit_status": "sold"}).status_code == 404
    assert env.crm("DELETE", f"/units/{missing_unit}").status_code == 404
    assert env.crm("GET", f"/deals/{missing_deal}").status_code == 404
    assert env.crm("PATCH", f"/deals/{missing_deal}", json={"deal_status": "lead"}).status_code == 404
    assert env.crm("DELETE", f"/deals/{missing_deal}").status_code == 404
    assert env.crm("GET", "/outbox/mc-units-khong-co").status_code == 404
    assert env.crm("POST", "/outbox/mc-units-khong-co/resend").status_code == 404


def test_a_rejected_unit_create_leaves_no_row_and_no_batch():
    """CHỐT CHÍNH của phần lỗi. Trạng thái lạ bị chặn ở tầng schema, TRƯỚC mọi lệnh ghi."""
    before_units = env.crm_rows("SELECT count(*) AS n FROM crm_units")[0]["n"]
    before_outbox = env.crm_rows("SELECT count(*) AS n FROM crm_outbox")[0]["n"]

    response = env.crm(
        "POST",
        "/units",
        json={
            "area_name": env.AREA_NAME,
            "unit_type": env.UNIT_TYPE,
            "unit_code": f"EP-{uuid.uuid4().hex[:6]}",
            "unit_status": "con_trong",
        },
    )

    assert response.status_code == 422
    assert env.crm_rows("SELECT count(*) AS n FROM crm_units")[0]["n"] == before_units
    assert env.crm_rows("SELECT count(*) AS n FROM crm_outbox")[0]["n"] == before_outbox


def test_a_rejected_deal_create_leaves_no_row_and_no_batch():
    """Giao dịch trỏ tới căn không tồn tại: 422, `sent=false`, và không một dòng nào."""
    before_deals = env.crm_rows("SELECT count(*) AS n FROM crm_deals")[0]["n"]
    before_outbox = env.crm_rows("SELECT count(*) AS n FROM crm_outbox")[0]["n"]

    response = env.crm(
        "POST", "/deals", json={"external_unit_id": f"U-KHONG-CO-{uuid.uuid4().hex[:4]}", "deal_status": "lead"}
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "UNIT_NOT_FOUND"
    assert body["sent"] is False
    assert env.crm_rows("SELECT count(*) AS n FROM crm_deals")[0]["n"] == before_deals
    assert env.crm_rows("SELECT count(*) AS n FROM crm_outbox")[0]["n"] == before_outbox


def test_a_deal_with_an_impossible_status_history_is_refused_before_any_write(walk):
    """`sold` mà thiếu `sold_at` không tồn tại được ở đây, nên nó không gửi đi được."""
    before_outbox = env.crm_rows("SELECT count(*) AS n FROM crm_outbox")[0]["n"]
    suffix = uuid.uuid4().hex[:6]
    unit = env.crm(
        "POST",
        "/units",
        json={
            "area_name": env.AREA_NAME,
            "unit_type": env.UNIT_TYPE,
            "unit_code": f"EP-{suffix}",
            "unit_status": "available",
        },
    ).json()

    before_deals = env.crm_rows("SELECT count(*) AS n FROM crm_deals")[0]["n"]
    response = env.crm(
        "POST", "/deals", json={"external_unit_id": unit["record"]["external_id"], "deal_status": "sold"}
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MISSING_STATUS_TIMESTAMP"
    assert env.crm_rows("SELECT count(*) AS n FROM crm_deals")[0]["n"] == before_deals
    # ĐÚNG HAI lô mới, cả hai của CĂN: lô v1 (gửi thật) + lô v2 (Phase C, CHỈ LƯU —
    # xem crud._capture_v2). Lô giao dịch không bao giờ tồn tại vì request bị từ
    # chối TRƯỚC khi chạm transaction ghi nào.
    assert env.crm_rows("SELECT count(*) AS n FROM crm_outbox")[0]["n"] == before_outbox + 2


def test_replaying_a_batch_that_is_not_stale_is_refused(walk):
    """Điều kiện tiền đề được KIỂM, không được giả định: một lô không cũ mà phát
    lại sẽ cho `duplicate_noop`/`conflict`, không phải `skip_stale`."""
    fresh = env.crm(
        "POST",
        "/units",
        json={
            "area_name": env.AREA_NAME,
            "unit_type": env.UNIT_TYPE,
            "unit_code": f"EP-{uuid.uuid4().hex[:6]}",
            "unit_status": "available",
        },
    ).json()
    response = env.crm("POST", "/outbox/replay-stale", json={"external_batch_id": fresh["sync"]["external_batch_id"]})
    assert response.status_code == 409
    assert response.json()["error_code"] == "BATCH_NOT_STALE"


def test_the_health_endpoint_still_carries_the_synthetic_disclaimer():
    """Nhãn đi cùng SẢN PHẨM. Ở Phase 5 hệ thống trông hoàn chỉnh hơn bao giờ hết,
    và nó vẫn không chứng minh được gì về một CRM có thật."""
    body = env.crm("GET", "/health").json()
    assert body["status"] == "ok"
    assert "TỔNG HỢP" in body["disclaimer"]
    assert "KHÔNG phải CRM của khách hàng" in body["disclaimer"]
