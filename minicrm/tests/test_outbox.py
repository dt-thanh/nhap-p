"""Sổ gửi đi: tra, gửi lại, phát lại bản cũ.

Điều được canh kỹ nhất ở đây là hai đường KHÔNG bị lẫn vào nhau:

    resend        ĐÚNG batch id cũ, payload nguyên văn  ⇒ kiểm tính bất biến của LÔ
    replay-stale  batch id MỚI, payload và revision CŨ   ⇒ kiểm thứ tự PHIÊN BẢN

Dùng lại batch id cũ cho đường thứ hai sẽ khiến nó không bao giờ chạm tới tầng so
phiên bản của backend, và phép thử `skip_stale` sẽ luôn "đạt" mà chẳng kiểm gì.

`FakeBackend` ở đây KHÔNG mô phỏng tầng so phiên bản (xem docstring của nó), nên
những test này chứng minh MINI CRM gửi đúng thứ gì — không phải backend trả lời
thế nào. Kết luận về `skip_stale` thật nằm ở `test_real_backend_sync.py`.

**`resend`/`replay-stale` chỉ còn hỗ trợ hình dạng outbox v1.** Từ khi CRUD
Unit/Deal chuyển hẳn sang ghi ý định v2 (`crud._capture_v2`, entity=
`units_v2`/`deals_v2` — xem docstring `app/sync_client.py::push` và
`app/crud.py::_reject_v2_delivery`), một dòng v2 gọi `/resend` hay
`/replay-stale` luôn nhận 409 `V2_DELIVERY_NOT_ENABLED`: đường gửi DUY NHẤT của
nó là vòng relay tự động (`app/relay.py`). Các test resend/replay-stale ở đây
vì vậy KHÔNG còn dựng batch qua `POST /units` — chúng gọi thẳng
`SyncClient.push(entity="units", ...)` để tạo đúng một dòng outbox v1, giống
cách một lần đẩy thủ công/di sản thật sự tạo ra nó. Các test về TRA SỔ (liệt
kê, lọc, xem chi tiết) vẫn dựng dữ liệu qua CRUD thật (v2) + `relay_tick()`
trực tiếp (không chờ `MINICRM_RELAY_INTERVAL_SECONDS` thật — xem
`test_relay.py` cho lý do đầy đủ của mẫu này).
"""

from __future__ import annotations

import pytest
from app import relay as relay_module
from app.main import app
from app.sync_client import SyncClient, SyncPushError, build_unit_envelope, new_batch_id
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import ADMIN_AUTH_HEADER, TEST_AREA_NAME, TEST_UNIT_TYPE

UNIT = {"area_name": TEST_AREA_NAME, "unit_type": TEST_UNIT_TYPE, "unit_code": "B1-01-01", "unit_status": "available"}


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


@pytest.fixture
def session_factory(crm_app):
    """`crm_app` yield ra DSN của database scratch — dựng thẳng một engine trỏ
    vào ĐÚNG database đó để `SyncClient.push()` ghi outbox vào cùng nơi mà
    `TestClient(app)` (route `/outbox/*`) đọc lại."""
    engine = create_async_engine(crm_app, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    engine.sync_engine.dispose()


async def _push_unit_v1(session_factory, *, external_id: str, revision: int, unit_status: str = "available"):
    """Tạo trực tiếp MỘT dòng outbox v1 (entity="units") — đường DUY NHẤT còn
    lại hỗ trợ resend/replay-stale. Không đi qua CRUD/API: `SyncClient.push()`
    tự ghi outbox rồi gửi ngay trong cùng lệnh gọi (xem docstring module này)."""
    batch_id = new_batch_id("units")
    envelope = build_unit_envelope(
        [
            {
                "external_id": external_id,
                "area_name": TEST_AREA_NAME,
                "unit_type": TEST_UNIT_TYPE,
                "unit_code": f"MANUAL-{external_id}",
                "unit_status": unit_status,
                "source_revision": revision,
            }
        ],
        batch_id=batch_id,
    )
    try:
        result = await SyncClient(session_factory).push(envelope, entity="units")
    except SyncPushError as exc:
        # `push()` NÉM lỗi khi backend từ chối/timeout (xem `test_sync_client.py`) —
        # nhưng dòng outbox vẫn được ghi và CÓ chứa trạng thái lỗi đó (đúng thứ
        # các test resend/replay-stale ở đây cần: một lô đã tồn tại, dù lần đầu
        # gửi có thành công hay không).
        return batch_id, exc
    return batch_id, result


# --- Tra sổ ------------------------------------------------------------------


async def test_the_outbox_lists_batches_newest_first(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.patch("/units/U-0001", json={"unit_status": "reserved"})
    await relay_module.relay_tick(limit=50)

    with _client() as client:
        listing = client.get("/outbox", params={"entity": "units_v2"}).json()

    assert listing["total"] == 2
    assert [item["entity"] for item in listing["items"]] == ["units_v2", "units_v2"]
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


async def test_the_detail_route_returns_both_directions_verbatim(crm_app, backend):
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
    await relay_module.relay_tick()

    with _client() as client:
        row = client.get(f"/outbox/{batch_id}").json()

    assert row["payload"]["external_batch_id"] == batch_id
    assert row["payload"]["records"][0]["external_id"] == "U-0001"
    assert row["response"]["status"] == "completed"


async def test_the_outbox_can_be_filtered_by_entity(crm_app, backend):
    with _client() as client:
        client.post("/units", json=UNIT)
    await relay_module.relay_tick()  # Deal đòi Unit đã MIRROR trước (_require_mirrored_unit).

    with _client() as client:
        deal = client.post("/deals", json={"external_unit_id": "U-0001", "deal_status": "lead"})
        assert deal.status_code == 201, deal.text
        assert client.get("/outbox", params={"entity": "deals_v2"}).json()["total"] == 1
        assert client.get("/outbox", params={"entity": "units_v2"}).json()["total"] == 1


def test_an_unknown_batch_id_returns_404(crm_app, backend):
    with _client() as client:
        assert client.get("/outbox/mc-units-nope").status_code == 404
        assert client.post("/outbox/mc-units-nope/resend").status_code == 404


async def test_resend_is_refused_for_a_v2_delivered_row(crm_app, backend):
    """CRUD Unit ghi entity `units_v2` — resend/replay-stale chỉ hỗ trợ hình
    dạng v1 (xem `app/crud.py::_reject_v2_delivery`). Chốt lại ranh giới này
    tường minh, kể cả sau khi relay đã gửi lô (http_status không còn NULL)."""
    with _client() as client:
        batch_id = client.post("/units", json=UNIT).json()["sync"]["external_batch_id"]
    await relay_module.relay_tick()

    with _client() as client:
        response = client.post(f"/outbox/{batch_id}/resend")
        replay_response = client.post("/outbox/replay-stale", json={"external_batch_id": batch_id})

    assert response.status_code == 409
    assert response.json()["error_code"] == "V2_DELIVERY_NOT_ENABLED"
    assert replay_response.status_code == 409
    assert replay_response.json()["error_code"] == "V2_DELIVERY_NOT_ENABLED"


# --- 4. Gửi lại đúng lô cũ (hình dạng v1) ------------------------------------


async def test_resend_reuses_the_original_batch_id_and_payload(crm_app, backend, session_factory):
    batch_id, _ = await _push_unit_v1(session_factory, external_id="U-9001", revision=1)
    with _client() as client:
        response = client.post(f"/outbox/{batch_id}/resend")

    body = response.json()
    assert body["mode"] == "resend"
    assert body["source_batch_id"] == batch_id
    assert body["sent_batch_id"] == batch_id, "gửi lại PHẢI dùng đúng batch id cũ"
    assert backend.envelopes[0] == backend.envelopes[1], "payload phải nguyên văn, không dựng lại"


async def test_resend_reuses_the_same_outbox_row(crm_app, backend, session_factory):
    """KHÔNG tạo dòng thứ hai: nếu tạo, "gửi lại cùng một lô" ở phía Mini CRM lại
    thành hai lô khác nhau, và phép thử idempotency mất ý nghĩa."""
    batch_id, _ = await _push_unit_v1(session_factory, external_id="U-9001", revision=1)
    with _client() as client:
        client.post(f"/outbox/{batch_id}/resend")
        client.post(f"/outbox/{batch_id}/resend")

        assert client.get("/outbox", params={"entity": "units"}).json()["total"] == 1
        row = client.get(f"/outbox/{batch_id}").json()

    assert row["attempts"] == 3, "một lần đẩy đầu + hai lần gửi lại"


async def test_resend_stores_the_latest_response(crm_app, backend, session_factory):
    """Phản hồi MỚI NHẤT thắng: một lô hỏng rồi được gửi lại thành công phải đọc
    ra là đã thành công, nếu không thì sổ gửi đi báo động giả mãi mãi."""
    backend.fail_with(422, {"detail": {"error_code": "UNKNOWN_AREA"}})
    batch_id, _ = await _push_unit_v1(session_factory, external_id="U-9001", revision=1)

    with _client() as client:
        assert client.get(f"/outbox/{batch_id}").json()["http_status"] == 422

        backend.accept()
        client.post(f"/outbox/{batch_id}/resend")
        row = client.get(f"/outbox/{batch_id}").json()

    assert row["http_status"] == 202
    assert row["last_error"] is None
    assert row["attempts"] == 2


async def test_a_resent_batch_is_reported_as_replayed(crm_app, backend, session_factory):
    """`FakeBackend` chỉ biết đúng một luật của hợp đồng: cùng `external_batch_id`
    ⇒ trả kết quả cũ kèm `replayed=true`."""
    batch_id, _ = await _push_unit_v1(session_factory, external_id="U-9001", revision=1)
    with _client() as client:
        body = client.post(f"/outbox/{batch_id}/resend").json()

    assert body["sync"]["status"] == "replayed"
    assert body["sync"]["http_status"] == 200


async def test_resend_does_not_create_a_second_local_record(crm_app, backend, session_factory):
    """`push()` ghi outbox trực tiếp, không qua CRUD — không có bản ghi Unit cục
    bộ nào để kiểm ở đây; đây chính là bằng chứng resend không tự bịa ra một
    Unit mới, chỉ gửi lại phong bì đã có."""
    batch_id, _ = await _push_unit_v1(session_factory, external_id="U-9001", revision=1)
    with _client() as client:
        client.post(f"/outbox/{batch_id}/resend")
        assert client.get("/units").json() == []


# --- 5. Phát lại bản cũ (hình dạng v1) ---------------------------------------


async def test_replay_stale_sends_the_old_payload_under_a_new_batch_id(crm_app, backend, session_factory):
    """`_is_stale` (`app/crud.py`) so payload's `source_revision` vào
    `crm_units.source_revision` HIỆN TẠI của đúng `external_id` đó — cần một
    Unit THẬT (qua CRUD, revision=2) để dòng v1 tự tạo (revision=1) THẬT SỰ cũ
    hơn trạng thái hiện tại."""
    with _client() as client:
        client.post("/units", json=UNIT)  # U-0001, revision 1
        client.patch("/units/U-0001", json={"unit_status": "sold"})  # revision 2
    stale_batch, _ = await _push_unit_v1(session_factory, external_id="U-0001", revision=1, unit_status="available")

    with _client() as client:
        body = client.post("/outbox/replay-stale", json={"external_batch_id": stale_batch}).json()

    assert body["mode"] == "replay_stale"
    assert body["source_batch_id"] == stale_batch
    assert body["sent_batch_id"] != stale_batch, (
        "batch id phải MỚI: dùng lại id cũ thì backend trả kết quả đã lưu và "
        "không bao giờ chạy tới tầng so phiên bản"
    )

    replayed = backend.envelopes[-1]
    assert replayed["records"][0]["source_revision"] == 1, "phiên bản phải giữ nguyên bản CŨ"
    assert replayed["records"][0]["payload"]["unit_status"] == "available"


async def test_replay_stale_links_back_to_the_original_batch(crm_app, backend, session_factory):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.patch("/units/U-0001", json={"unit_status": "sold"})
    stale_batch, _ = await _push_unit_v1(session_factory, external_id="U-0001", revision=1)

    with _client() as client:
        sent = client.post("/outbox/replay-stale", json={"external_batch_id": stale_batch}).json()["sent_batch_id"]
        row = client.get(f"/outbox/{sent}").json()
    assert row["replay_of"] == stale_batch


async def test_replay_stale_does_not_move_the_mirror_stamp_backwards(crm_app, backend):
    """Không còn cách nào PHÁT SINH được tình huống này qua CRUD nữa: CRUD Unit
    ghi entity `units_v2`, và `_reject_v2_delivery` chặn CẢ `resend` lẫn
    `replay-stale` cho v2 bằng 409 TRƯỚC khi chạm tới bất kỳ logic đóng dấu
    `mirrored_revision` nào (`app/crud.py::_reject_v2_delivery`) — nên bảo đảm
    "một lô cũ không kéo lùi mirror stamp" giờ đúng CÓ CẤU TRÚC cho v2, không
    cần kiểm bằng cách thử phát lại một lô cũ thật (không có lô v1 nào để tạo
    ra kịch bản đó cho một Unit CÓ THẬT — `SyncClient.push()` trực tiếp không
    đụng `crm_units`). Chốt lại bảo đảm này bằng chính hành vi chặn, khớp
    `test_resend_is_refused_for_a_v2_delivered_row`."""
    with _client() as client:
        client.post("/units", json=UNIT)
    await relay_module.relay_tick()

    with _client() as client:
        assert client.get("/units/U-0001").json()["mirrored_revision"] == 1

        client.patch("/units/U-0001", json={"unit_status": "reserved"})
    await relay_module.relay_tick()

    with _client() as client:
        assert client.get("/units/U-0001").json()["mirrored_revision"] == 2

        first_batch = client.get("/outbox", params={"entity": "units_v2", "limit": 1, "offset": 1}).json()["items"][
            0
        ]["external_batch_id"]
        response = client.post("/outbox/replay-stale", json={"external_batch_id": first_batch})
        assert response.status_code == 409

        assert client.get("/units/U-0001").json()["mirrored_revision"] == 2, (
            "một lần replay-stale bị TỪ CHỐI không được đổi trạng thái đã mirror"
        )


async def test_replay_stale_picks_the_newest_stale_batch_when_none_is_named(crm_app, backend, session_factory):
    """`current <= revision` nghĩa là KHÔNG cũ (`_is_stale`) — nên lô v1 ở ĐÚNG
    phiên bản hiện tại (3) không đủ điều kiện, và "mới nhất trong số các lô
    CŨ" là lô revision=2 (push SAU revision=1 ⇒ `created_at` mới hơn)."""
    with _client() as client:
        client.post("/units", json=UNIT)  # U-0001, revision 1
        client.patch("/units/U-0001", json={"unit_status": "reserved"})  # revision 2
        client.patch("/units/U-0001", json={"unit_status": "sold"})  # revision 3
    await _push_unit_v1(session_factory, external_id="U-0001", revision=1)
    second_batch, _ = await _push_unit_v1(session_factory, external_id="U-0001", revision=2)

    with _client() as client:
        body = client.post("/outbox/replay-stale", json={}).json()
    assert body["source_batch_id"] == second_batch


async def test_replaying_a_batch_that_is_not_actually_stale_is_refused(crm_app, backend, session_factory):
    """ĐIỀU KIỆN TIỀN ĐỀ phải được kiểm, không được giả định.

    Phát lại một lô KHÔNG cũ sẽ không cho `skip_stale` — nó cho `duplicate_noop`
    hoặc `conflict`. Một phép thử tự khẳng định kết quả mà không kiểm tiền đề thì
    chứng minh được số không.
    """
    with _client() as client:
        client.post("/units", json=UNIT)  # U-0001, revision 1
    # Đúng bằng phiên bản hiện tại (1) — KHÔNG cũ hơn.
    batch_id, _ = await _push_unit_v1(session_factory, external_id="U-0001", revision=1)

    with _client() as client:
        before = len(backend.requests)
        response = client.post("/outbox/replay-stale", json={"external_batch_id": batch_id})

    assert response.status_code == 409
    assert response.json()["error_code"] == "BATCH_NOT_STALE"
    assert len(backend.requests) == before


async def test_replay_stale_without_any_stale_batch_is_refused(crm_app, backend, session_factory):
    """CRUD Unit chỉ ghi entity `units_v2`, và `_is_stale` không coi bất kỳ
    entity nào ngoài `units`/`deals` (v1) là ứng viên (`_TABLE_FOR_ENTITY`
    không có `units_v2`) — nên một database chỉ có ghi CRUD (v2) không có lô
    "cũ" nào để chọn, kể cả khi Unit đã qua nhiều lần sửa."""
    with _client() as client:
        client.post("/units", json=UNIT)
        client.patch("/units/U-0001", json={"unit_status": "reserved"})
        response = client.post("/outbox/replay-stale", json={})
    assert response.status_code == 409
    assert response.json()["error_code"] == "NO_STALE_BATCH"


async def test_replay_stale_creates_its_own_outbox_row(crm_app, backend, session_factory):
    with _client() as client:
        client.post("/units", json=UNIT)
        client.patch("/units/U-0001", json={"unit_status": "sold"})
    stale_batch, _ = await _push_unit_v1(session_factory, external_id="U-0001", revision=1)

    with _client() as client:
        client.post("/outbox/replay-stale", json={"external_batch_id": stale_batch})
        listing = client.get("/outbox", params={"entity": "units"}).json()
    assert listing["total"] == 2, "lô v1 tự tạo + lô replay của nó — CRUD Unit không ghi entity 'units'"
    assert sum(1 for item in listing["items"] if item["replay_of"]) == 1
