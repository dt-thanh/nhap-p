"""Cửa sổ hỏng: những chỗ đồng bộ có thể sai mà KHÔNG ai thấy gì.

Bộ test ở `test_real_backend_sync.py` chứng minh đường ĐI ĐÚNG hoạt động. File này
chứng minh đường đi SAI không âm thầm phá dữ liệu — và đó là loại bằng chứng khác
hẳn, vì mọi lỗi ở đây đều trông giống "hệ thống đang chạy bình thường".

Sáu cửa sổ được mở ra bằng thao tác THẬT, không mô phỏng:

    1. Hai lần sửa CÙNG LÚC một bản ghi   → dãy phiên bản có còn hợp lệ không
    2. Hai lần gửi lại CÙNG LÚC một lô     → có sinh ra bản chiếu thứ hai không
    3. Lô CŨ tới SAU lô mới                → trạng thái mới có bị ghi đè không
    4. Backend TẮT giữa lúc ghi            → thay đổi cục bộ có sống sót không
    5. Khởi động lại MINI CRM + database   → dữ liệu và sổ gửi đi có còn không
    6. Khởi động lại BACKEND               → lô đang dở có phục hồi được không

Kịch bản 3 đáng nói riêng. Nó KHÔNG dùng `/outbox/replay-stale` — đường đó tự khai
mình đang phát lại một bản cũ. Ở đây lô cũ trở nên cũ một cách TỰ NHIÊN: nó hỏng
lúc backend tắt, một lần sửa mới hơn đi qua trước, rồi lô cũ mới được gửi lại. Đó
là đúng trình tự sẽ xảy ra trong vận hành thật, và nó chạm vào tầng so phiên bản
của backend chứ không chỉ tầng nhận diện lô.

Test này KHỞI ĐỘNG LẠI container thật. Nó chậm (hàng chục giây), và nó phải chậm:
một phép thử phục hồi sau sự cố mà không có sự cố nào thì chỉ chứng minh được rằng
mã nguồn biên dịch được.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import real_env as env

pytestmark = pytest.mark.skipif(bool(env.skip_reason()), reason=env.skip_reason() or "")


def _new_unit(suffix: str, status: str = "available") -> dict:
    return {
        "area_name": env.AREA_NAME,
        "unit_type": env.UNIT_TYPE,
        "unit_code": f"FW-{suffix}",
        "unit_status": status,
    }


def _create_unit(suffix: str) -> dict:
    response = env.crm("POST", "/units", json=_new_unit(suffix))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sync"]["status"] == "synced", body["sync"]
    return body


def _backend_unit(external_id: str) -> dict:
    rows = env.backend_rows(
        "SELECT status, source_revision, deleted_at FROM units "
        "WHERE source_instance_id = :i AND external_unit_id = :e",
        i=env.SOURCE_INSTANCE,
        e=external_id,
    )
    assert len(rows) == 1, f"kỳ vọng ĐÚNG một dòng units cho {external_id}, thấy {len(rows)}"
    return rows[0]


def _runs_for(batch_id: str) -> list[dict]:
    return env.backend_rows(
        "SELECT id, status, rows_received, rows_ok, rows_failed FROM upload_files WHERE external_batch_id = :b",
        b=batch_id,
    )


def _wait_outbox_delivered(batch_id: str, *, timeout: float = 30.0) -> dict:
    """Chờ dòng outbox có phản hồi THẬT — dùng khi một lần gọi `/resend` tường
    minh đua với vòng relay tự động (Phase C.5, `app/relay.py`) chạy trong CÙNG
    container Mini CRM và có thể va nhau đúng lúc backend vừa sống lại. Nếu lần
    gọi tường minh đó bị lỗi truyền tải thoáng qua (`http_status` vẫn NULL), dòng
    vẫn RETRYABLE và relay sẽ tự dọn nốt ở lượt kế — chờ đây thay vì coi đó là
    hỏng thật."""
    deadline = time.time() + timeout
    row = env.crm("GET", f"/outbox/{batch_id}").json()
    while time.time() < deadline and row["http_status"] is None:
        time.sleep(1.5)
        row = env.crm("GET", f"/outbox/{batch_id}").json()
    return row


# ═══════════════════════════════════════════════════════════════════════════
#  1. Hai lần sửa CÙNG LÚC một bản ghi
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def concurrent_update():
    suffix = uuid.uuid4().hex[:6]
    created = _create_unit(suffix)
    unit_id = created["record"]["external_id"]

    def patch(status: str):
        return env.crm("PATCH", f"/units/{unit_id}", json={"unit_status": status})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = (pool.submit(patch, s) for s in ("blocked", "sold"))
        responses = [first.result(), second.result()]

    return {
        "unit_id": unit_id,
        "responses": [r.json() for r in responses],
        "statuses": [r.status_code for r in responses],
        "crm": env.crm_rows(
            "SELECT unit_status, source_revision, mirrored_revision FROM crm_units WHERE external_id = :e",
            e=unit_id,
        )[0],
        "backend": _backend_unit(unit_id),
    }


def test_two_simultaneous_updates_both_succeed(concurrent_update):
    """Không lần nào được hỏng. `FOR UPDATE` xếp hàng chúng lại, không loại bỏ."""
    assert concurrent_update["statuses"] == [200, 200]


def test_two_simultaneous_updates_produce_distinct_consecutive_revisions(concurrent_update):
    """CHỐT CHÍNH.

    Thiếu khoá dòng, cả hai lần sửa cùng đọc revision 1 và cùng ghi 2 — hai trạng
    thái KHÁC NHAU gửi đi ở CÙNG một phiên bản. Backend không có căn cứ nào để xếp
    thứ tự chúng, nên nó báo `conflict` và giữ nguyên bản đầu: lần sửa thứ hai
    biến mất mà không ai thấy lỗi ở đâu.
    """
    revisions = sorted(r["record"]["source_revision"] for r in concurrent_update["responses"])
    assert revisions == [2, 3], f"dãy phiên bản hỏng: {revisions}"


def test_the_local_row_ends_at_the_highest_revision(concurrent_update):
    assert concurrent_update["crm"]["source_revision"] == 3


def test_the_backend_ends_on_the_highest_revision_regardless_of_arrival_order(concurrent_update):
    """Phiên bản CAO NHẤT thắng, bất kể lô nào tới backend trước.

    Trước bản vá khoá hàng, khẳng định này SAI và test này chỉ dám nói "hội tụ về
    một trong hai" — hai lô song song cùng đọc một bản đang giữ đã cũ, cùng quyết
    định `update`, và lô commit sau thắng kể cả khi nó mang phiên bản thấp hơn.

    Bây giờ `SourceIdentityService._load()` khoá dòng danh tính, nên phép so phiên
    bản được tuần tự hoá và lô cũ hơn nhận `skip_stale`. Đây là cùng một bất biến
    mà `tests/test_services/test_sync_concurrency.py` chứng minh ở tầng service với
    thứ tự đã ghim; ở đây nó được kiểm qua HTTP thật, qua hai container thật, với
    thứ tự do mạng quyết định.
    """
    backend = concurrent_update["backend"]
    highest = max(r["record"]["source_revision"] for r in concurrent_update["responses"])
    winner = next(r for r in concurrent_update["responses"] if r["record"]["source_revision"] == highest)

    assert backend["source_revision"] == highest
    assert backend["status"] == winner["record"]["unit_status"]
    assert concurrent_update["crm"]["source_revision"] == highest
    assert concurrent_update["crm"]["unit_status"] == backend["status"], "hai phía phải hội tụ về cùng trạng thái"


def test_the_older_concurrent_batch_is_recorded_as_skipped_at_the_backend(concurrent_update):
    """Lô thua KHÔNG được biến mất: nó phải đọc ra là `skip_stale` ở sổ lô.

    Hai lô cho cùng một căn có thể không chồng lấn nhau (mạng nhanh, máy rảnh), và
    khi đó lô cũ hơn vẫn là `skip_stale` — vì lúc đó nó thật sự tới sau. Nói cách
    khác: dù có đua hay không, kết quả phải giống nhau. Đó chính là điều bản vá
    khoá hàng mua về.
    """
    batches = sorted(
        concurrent_update["responses"], key=lambda r: r["record"]["source_revision"]
    )
    older, newer = batches[0], batches[-1]

    def decisions_for(body):
        rows = env.backend_rows(
            "SELECT error_summary->'decisions' AS decisions FROM upload_files WHERE external_batch_id = :b",
            b=body["sync"]["external_batch_id"],
        )
        assert len(rows) == 1
        return env.nonzero(rows[0]["decisions"])

    assert decisions_for(newer) == {"update": 1}
    assert decisions_for(older) in ({"update": 1}, {"skip_stale": 1}), (
        "lô cũ hơn phải là `update` (nếu nó tới TRƯỚC) hoặc `skip_stale` (nếu tới SAU) — "
        "không có kết quả thứ ba"
    )


def test_the_two_backend_tables_agree_after_the_race(concurrent_update):
    """Bản sao và sổ danh tính phải kể CÙNG một câu chuyện.

    Lệch nhau nghĩa là ngoài việc mất một bản cập nhật, hệ thống còn mất luôn khả
    năng phát hiện rằng nó đã mất.
    """
    identity = env.backend_rows(
        "SELECT source_revision FROM crm_source_records "
        "WHERE source_instance_id = :i AND source_entity = 'units' AND source_record_id = :e",
        i=env.SOURCE_INSTANCE,
        e=concurrent_update["unit_id"],
    )
    assert len(identity) == 1
    assert identity[0]["source_revision"] == concurrent_update["backend"]["source_revision"]


def test_two_simultaneous_updates_create_two_separate_batches(concurrent_update):
    """Mỗi lần ghi một lô riêng. Dùng chung một `external_batch_id` sẽ khiến lô thứ
    hai bị coi là phát lại lô thứ nhất và không bao giờ được xử lý."""
    batches = {r["sync"]["external_batch_id"] for r in concurrent_update["responses"]}
    assert len(batches) == 2
    for batch_id in batches:
        assert len(_runs_for(batch_id)) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  2. Hai lần gửi lại CÙNG LÚC một lô
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def concurrent_resend():
    suffix = uuid.uuid4().hex[:6]
    created = _create_unit(suffix)
    unit_id = created["record"]["external_id"]
    batch_id = created["sync"]["external_batch_id"]

    before = env.backend_rows(
        "SELECT count(*) AS n FROM units WHERE source_instance_id = :i", i=env.SOURCE_INSTANCE
    )[0]["n"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(env.crm, "POST", f"/outbox/{batch_id}/resend") for _ in range(2)]
        responses = [f.result() for f in futures]

    return {
        "unit_id": unit_id,
        "batch_id": batch_id,
        "original_run_id": created["sync"]["sync_run_id"],
        "statuses": [r.status_code for r in responses],
        "bodies": [r.json() for r in responses],
        "units_before": before,
        "units_after": env.backend_rows(
            "SELECT count(*) AS n FROM units WHERE source_instance_id = :i", i=env.SOURCE_INSTANCE
        )[0]["n"],
        "runs": _runs_for(batch_id),
        "outbox": env.crm("GET", f"/outbox/{batch_id}").json(),
    }


def test_two_simultaneous_resends_create_no_second_backend_run(concurrent_resend):
    """CHỐT CHÍNH. Chỉ mục duy nhất từng phần trên
    `(source_system, source_instance_id, external_batch_id)` là thứ giữ điều này
    đúng kể cả khi hai request chạy song song — logic ứng dụng thì có cửa sổ đua."""
    assert len(concurrent_resend["runs"]) == 1, (
        f"gửi lại song song đã tạo {len(concurrent_resend['runs'])} lô ở backend"
    )


def test_two_simultaneous_resends_create_no_duplicate_projection(concurrent_resend):
    assert concurrent_resend["units_after"] == concurrent_resend["units_before"]
    _backend_unit(concurrent_resend["unit_id"])  # ném nếu có nhiều hơn một dòng


def test_the_outbox_row_is_still_a_single_row_after_concurrent_resends(concurrent_resend):
    """Gửi lại KHÔNG tạo dòng outbox thứ hai — nếu tạo, "gửi lại cùng một lô" ở
    phía Mini CRM lại thành hai lô khác nhau."""
    listing = env.crm("GET", "/outbox", params={"limit": 500}).json()
    matching = [i for i in listing["items"] if i["external_batch_id"] == concurrent_resend["batch_id"]]
    assert len(matching) == 1
    assert concurrent_resend["outbox"]["attempts"] >= 3, "một lần đẩy đầu + hai lần gửi lại"


def test_at_least_one_concurrent_resend_is_reported_as_replayed(concurrent_resend):
    """Ghi nhận hành vi THẬT quan sát được, không khẳng định điều chưa kiểm.

    Hai lần gửi lại song song có thể cùng thấy lô cũ (cả hai `replayed`), hoặc một
    lần chạm vào cửa sổ giữa lúc lô kia đang được ghi. Điều BẮT BUỘC đúng là: không
    có bản chiếu thứ hai (hai test trên), và ít nhất một lần nhận ra đây là lô đã
    xử lý. Đòi CẢ HAI phải `replayed` là khẳng định về thời điểm, không phải về
    tính đúng đắn.
    """
    outcomes = [b["sync"]["status"] for b in concurrent_resend["bodies"]]
    assert "replayed" in outcomes, f"không lần nào nhận ra lô đã xử lý: {outcomes}"
    for body in concurrent_resend["bodies"]:
        if body["sync"]["status"] == "replayed":
            assert body["sync"]["sync_run_id"] == concurrent_resend["original_run_id"]


# ═══════════════════════════════════════════════════════════════════════════
#  3 + 4. Backend TẮT giữa lúc ghi, rồi một lô CŨ tới SAU lô mới
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def outage_and_out_of_order():
    """Một cửa sổ hỏng THẬT, dựng bằng đúng trình tự sẽ xảy ra trong vận hành.

        rev 1  tạo căn                          → tới backend
        --- TẮT backend ---
        rev 2  sửa thành 'blocked'              → HỎNG, nằm lại trong outbox
        --- BẬT backend ---
        rev 3  sửa thành 'sold'                 → tới backend
        gửi lại lô rev 2                        → tới SAU rev 3 ⇒ phải skip_stale

    Không dùng `/outbox/replay-stale`: đường đó tự khai mình đang phát lại bản cũ.
    Ở đây lô cũ trở nên cũ một cách tự nhiên, và nó chạm vào tầng SO PHIÊN BẢN của
    backend chứ không chỉ tầng nhận diện lô.
    """
    suffix = uuid.uuid4().hex[:6]
    created = _create_unit(suffix)
    unit_id = created["record"]["external_id"]
    state: dict = {"unit_id": unit_id, "after_create": _backend_unit(unit_id)}

    env.compose("stop", "api")
    try:
        offline = env.crm("PATCH", f"/units/{unit_id}", json={"unit_status": "blocked"})
        assert offline.status_code == 200, offline.text
        state["offline_patch"] = offline.json()
        state["stale_batch"] = state["offline_patch"]["sync"]["external_batch_id"]
        # Chụp NGAY LÚC backend còn tắt: đọc lại sau khi đã phục hồi sẽ thấy trạng
        # thái đã lành, và test "thay đổi sống sót qua sự cố" sẽ nói về một thời
        # điểm khác với thời điểm nó nhận là đang nói tới.
        state["offline_crm"] = env.crm_rows(
            "SELECT unit_status, source_revision, mirrored_revision FROM crm_units WHERE external_id = :e",
            e=unit_id,
        )[0]
        state["offline_outbox"] = env.crm("GET", f"/outbox/{state['stale_batch']}").json()
        state["offline_runs"] = _runs_for(state["stale_batch"])
    finally:
        env.compose("start", "api")
        env.wait_until_up(env.BACKEND_URL)

    newer = env.crm("PATCH", f"/units/{unit_id}", json={"unit_status": "sold"})
    assert newer.status_code == 200, newer.text
    state["newer_patch"] = newer.json()
    state["after_newer"] = _backend_unit(unit_id)

    late = env.crm("POST", f"/outbox/{state['stale_batch']}/resend")
    assert late.status_code == 200, late.text
    state["late_resend"] = late.json()
    state["after_late"] = _backend_unit(unit_id)
    state["late_outbox"] = env.crm("GET", f"/outbox/{state['stale_batch']}").json()
    return state


def test_a_write_during_the_outage_still_commits_locally(outage_and_out_of_order):
    """CHỐT CHÍNH. Backend không sống KHÔNG được phép làm hỏng một thao tác cục bộ."""
    body = outage_and_out_of_order["offline_patch"]
    assert body["record"]["source_revision"] == 2
    assert outage_and_out_of_order["offline_crm"]["source_revision"] == 2
    assert outage_and_out_of_order["offline_crm"]["unit_status"] == "blocked"


def test_the_outage_is_reported_as_failed_not_pending(outage_and_out_of_order):
    """Không mở được kết nối ⇒ lô CHẮC CHẮN chưa rời khỏi máy ⇒ `sync_failed`.

    Gộp nó vào `sync_pending` ("không biết") sẽ khiến người vận hành ngồi chờ một
    kết quả không bao giờ có, thay vì gửi lại ngay.
    """
    sync = outage_and_out_of_order["offline_patch"]["sync"]
    assert sync["status"] == "sync_failed"
    assert sync["http_status"] is None
    assert sync["error"]


def test_the_failed_batch_is_visible_in_the_outbox_during_the_outage(outage_and_out_of_order):
    row = outage_and_out_of_order["offline_outbox"]
    assert row["http_status"] is None
    assert row["sent_at"] is None
    assert row["attempts"] == 1
    assert row["last_error"], "lỗi truyền tải không có mã HTTP nào — phải đọc được ở đây"
    assert row["payload"]["records"][0]["source_revision"] == 2


def test_nothing_reached_the_backend_during_the_outage(outage_and_out_of_order):
    assert outage_and_out_of_order["offline_runs"] == []
    assert outage_and_out_of_order["after_create"] == {
        **outage_and_out_of_order["after_create"],
        "source_revision": 1,
    }


def test_the_mirror_stamp_shows_the_gap_during_the_outage(outage_and_out_of_order):
    """`mirrored_revision < source_revision` là cách đọc "có thay đổi chưa lên tới
    nơi" mà không phải suy từ sổ gửi đi."""
    row = outage_and_out_of_order["offline_crm"]
    assert row["mirrored_revision"] == 1
    assert row["source_revision"] == 2


def test_a_newer_revision_goes_through_after_recovery(outage_and_out_of_order):
    sync = outage_and_out_of_order["newer_patch"]["sync"]
    assert sync["status"] == "synced"
    assert env.nonzero(sync["decisions"]) == {"update": 1}
    assert outage_and_out_of_order["after_newer"]["source_revision"] == 3
    assert outage_and_out_of_order["after_newer"]["status"] == "sold"


def test_the_stale_batch_arriving_late_is_skipped_not_applied(outage_and_out_of_order):
    """CHỐT CHÍNH: một lô mang revision cũ hơn KHÔNG BAO GIỜ được áp lên state,
    dù nó tới backend theo trật tự nào.

    Ý đồ gốc của fixture là ép revision 2 tới SAU revision 3 để chạm tầng so
    phiên bản và nhận `skip_stale` một cách xác định. Từ khi vòng relay tự động
    (Phase C.5) sống trong container thật, trật tự đó không còn nằm trong tay
    fixture nữa: relay quét outbox theo `created_at` và revision 2 (đứng đợi từ
    lúc backend còn tắt) CŨ HƠN revision 3 — nên relay có thể tự gửi revision 2
    ngay khi backend sống lại, TRƯỚC CẢ KHI revision 3 được tạo. Khi đó revision 2
    chạm tầng so phiên bản lúc nó THẬT SỰ là bản mới nhất backend từng thấy, nên
    quyết định đúng đắn là `update`, không phải `skip_stale` — hai kết quả này
    không phải một lỗi, mà là hai lần chạy CÙNG một cuộc đua hợp lệ đi theo hai
    nhánh khác nhau. Việc `/resend` tường minh trong fixture chỉ REPLAY lại quyết
    định đã có (route dedup theo `external_batch_id`), không tính lại — nên nó
    phơi ra kết quả của cuộc đua chứ không quyết định được nó.

    Bất biến THẬT không phải "quyết định luôn là skip_stale", mà là "quyết định
    luôn mạch lạc và không có state nào bị ghi đè bởi bản cũ hơn" — vế thứ hai
    được `test_the_late_stale_batch_did_not_overwrite_the_newer_state` kiểm
    riêng, không phụ thuộc nhánh đua nào đã xảy ra.

    Phase C.5: `late_resend` cũng có thể thoáng hỏng vì đúng lúc đó relay cũng
    đang thử cùng dòng — `sync["status"]` khi đó là `sync_failed` dù dòng vẫn
    còn NGUYÊN VẸN, retryable. Đọc lại outbox (chờ relay dọn nốt nếu cần) rồi
    kiểm quyết định THẬT trên đó, thay vì tin tuyệt đối vào MỘT lần gọi.
    """
    sync = outage_and_out_of_order["late_resend"]["sync"]
    if sync["status"] == "sync_failed":
        row = _wait_outbox_delivered(outage_and_out_of_order["stale_batch"])
        assert row["http_status"] in (200, 202), "relay phải tự dọn nốt — nếu không, đây mới là hỏng thật"
        sync = row["response"]
    else:
        # "synced" (202) = resend là lần gửi ĐẦU của batch id này; "replayed"
        # (200) = ai đó (relay hoặc chính resend trước) đã gửi trước, đây chỉ
        # thấy bản ĐÃ LƯU — cả hai đều hợp lệ.
        assert sync["status"] in ("synced", "replayed"), sync["status"]
        assert sync["http_status"] in (200, 202)
    decisions = env.nonzero(sync["decisions"])
    projections = env.nonzero(sync["projections"])
    # Một replay thuần (batch đã xử lý, dedup theo batch_id) có thể không mang
    # lại decisions/projections mới — hợp lệ, không phải trạng thái sai.
    if decisions or projections:
        assert decisions in ({"skip_stale": 1}, {"update": 1}), decisions
        expected_projection = {"untouched": 1} if decisions == {"skip_stale": 1} else {"updated": 1}
        assert projections == expected_projection, projections


def test_the_late_stale_batch_did_not_overwrite_the_newer_state(outage_and_out_of_order):
    assert outage_and_out_of_order["after_late"] == outage_and_out_of_order["after_newer"]
    assert outage_and_out_of_order["after_late"]["status"] == "sold"
    assert outage_and_out_of_order["after_late"]["source_revision"] == 3


def test_the_stale_batch_did_not_drag_the_mirror_stamp_backwards(outage_and_out_of_order):
    row = env.crm_rows(
        "SELECT source_revision, mirrored_revision FROM crm_units WHERE external_id = :e",
        e=outage_and_out_of_order["unit_id"],
    )[0]
    assert row["mirrored_revision"] == row["source_revision"] == 3


def test_the_recovered_outbox_row_records_the_second_attempt(outage_and_out_of_order):
    """Phase C.5: vòng relay tự động có thể đã gửi lô này TRƯỚC bước `/resend`
    tường minh ở fixture (đua không tất định) — mỗi lần gửi THẬT đều tăng
    `attempts`, nên số lần chính xác không còn cố định ở 2 (1 lần hỏng lúc
    offline + ít nhất 1 lần gửi thành công, có thể cộng thêm 1 nếu cả relay LẪN
    `/resend` tường minh đều kịp gửi). Bất biến còn lại: ít nhất một lần hỏng
    rồi một lần thành công, và trạng thái CUỐI CÙNG phải sạch."""
    row = outage_and_out_of_order["late_outbox"]
    assert row["attempts"] >= 2
    assert row["http_status"] in (200, 202), "200 nếu relay đã gửi trước (replayed), 202 nếu resend gửi trước"
    assert row["last_error"] is None, "lần gửi thành công phải xoá lỗi cũ — nếu không, sổ báo động giả mãi mãi"


# ═══════════════════════════════════════════════════════════════════════════
#  5. Khởi động lại MINI CRM và database của nó
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def minicrm_restart():
    """Ghi cục bộ, KHỞI ĐỘNG LẠI cả ứng dụng lẫn database, rồi đọc lại.

    Điều được kiểm không phải "container sống lại" mà là: dữ liệu nằm trên volume
    chứ không trong bộ nhớ, và một lô còn dở vẫn gửi lại được sau khi mọi tiến
    trình đã chết và sinh ra lại.
    """
    suffix = uuid.uuid4().hex[:6]

    env.compose("stop", "api")
    try:
        created = env.crm("POST", "/units", json=_new_unit(suffix))
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["sync"]["status"] == "sync_failed"
    finally:
        env.compose("start", "api")
        env.wait_until_up(env.BACKEND_URL)

    unit_id = body["record"]["external_id"]
    batch_id = body["sync"]["external_batch_id"]

    before = {
        "unit": env.crm_rows("SELECT * FROM crm_units WHERE external_id = :e", e=unit_id)[0],
        "outbox": env.crm("GET", f"/outbox/{batch_id}").json(),
        "alembic": env.crm_rows("SELECT version_num FROM alembic_version")[0]["version_num"],
        "outbox_total": env.crm("GET", "/outbox").json()["total"],
    }

    env.compose("restart", "minicrm_db", "minicrm")
    env.wait_for_postgres(env.CRM_DSN)
    env.wait_until_up(env.MINICRM_URL)

    after = {
        "unit": env.crm_rows("SELECT * FROM crm_units WHERE external_id = :e", e=unit_id)[0],
        "outbox": env.crm("GET", f"/outbox/{batch_id}").json(),
        "alembic": env.crm_rows("SELECT version_num FROM alembic_version")[0]["version_num"],
        "outbox_total": env.crm("GET", "/outbox").json()["total"],
    }

    resend = env.crm("POST", f"/outbox/{batch_id}/resend")
    return {
        "unit_id": unit_id,
        "batch_id": batch_id,
        "before": before,
        "after": after,
        "resend": resend.json(),
        "resend_status": resend.status_code,
        "backend": _backend_unit(unit_id),
    }


def test_local_records_survive_a_minicrm_restart(minicrm_restart):
    assert minicrm_restart["after"]["unit"] == minicrm_restart["before"]["unit"]


def test_outbox_records_survive_a_minicrm_restart(minicrm_restart):
    """Sổ gửi đi phải nằm trên đĩa. Giữ nó trong bộ nhớ thì một lần khởi động lại
    xoá sạch mọi bằng chứng về những lô chưa tới nơi."""
    before, after = minicrm_restart["before"]["outbox"], minicrm_restart["after"]["outbox"]
    assert after["payload"] == before["payload"]
    assert after["http_status"] is None and after["sent_at"] is None
    assert after["attempts"] == before["attempts"]
    assert minicrm_restart["after"]["outbox_total"] == minicrm_restart["before"]["outbox_total"]


def test_the_alembic_state_survives_a_minicrm_restart(minicrm_restart):
    # Phase C nâng head Mini CRM lên 0004_outbox_hierarchy_entities — con số CHỦ Ý
    # cứng, cùng khuôn với `test_real_backend_sync.py::test_the_two_alembic_histories_stay_separate`.
    # Điều test này thật sự kiểm là before == after (khởi động lại KHÔNG làm mất
    # trạng thái Alembic), không phải giá trị cụ thể của con số — nhưng ghim cứng
    # giá trị đó vẫn có ích: nó là bằng chứng revision không TRÔI giữa hai lần đọc.
    assert (
        minicrm_restart["after"]["alembic"]
        == minicrm_restart["before"]["alembic"]
        == "0004_outbox_hierarchy_entities"
    )


def test_a_pending_batch_can_still_be_resent_after_a_restart(minicrm_restart):
    """Đây mới là câu hỏi thật sự: sau khi mọi tiến trình chết và sinh lại, lô còn
    dở có phục hồi được không.

    Phase C.5: vòng relay tự động (đã sống lại cùng `minicrm`) có thể đua với lần
    `/resend` tường minh mà fixture gọi — nếu đua thoáng hỏng, chờ outbox rồi đọc
    kết quả THẬT từ đó, cùng cách xử lý với các test race khác trong file này.
    """
    assert minicrm_restart["resend_status"] == 200
    sync = minicrm_restart["resend"]["sync"]
    if sync["status"] == "sync_failed":
        row = _wait_outbox_delivered(minicrm_restart["batch_id"])
        assert row["http_status"] in (200, 202), "relay phải tự dọn nốt — nếu không, đây mới là hỏng thật"
        sync = row["response"]
    else:
        assert sync["status"] in ("synced", "replayed"), sync["status"]
    assert env.nonzero(sync["decisions"]) == {"insert": 1}

    backend = _backend_unit(minicrm_restart["unit_id"])
    assert backend["source_revision"] == 1
    assert backend["deleted_at"] is None


# ═══════════════════════════════════════════════════════════════════════════
#  6. Khởi động lại BACKEND sau khi Mini CRM đã commit
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def backend_restart():
    suffix = uuid.uuid4().hex[:6]
    created = _create_unit(suffix)
    unit_id = created["record"]["external_id"]
    batch_id = created["sync"]["external_batch_id"]

    env.compose("restart", "api")
    env.wait_until_up(env.BACKEND_URL)

    return {
        "unit_id": unit_id,
        "batch_id": batch_id,
        "original_run_id": created["sync"]["sync_run_id"],
        "runs": _runs_for(batch_id),
        "backend": _backend_unit(unit_id),
        "resend": env.crm("POST", f"/outbox/{batch_id}/resend").json(),
        "runs_after_resend": _runs_for(batch_id),
    }


def test_a_committed_projection_survives_a_backend_restart(backend_restart):
    assert len(backend_restart["runs"]) == 1
    assert backend_restart["runs"][0]["status"] == "completed"
    assert backend_restart["backend"]["source_revision"] == 1


def test_the_batch_is_still_recognised_as_processed_after_a_backend_restart(backend_restart):
    """Tính bất biến của lô nằm trong DATABASE, không trong bộ nhớ tiến trình.

    Nếu backend nhận diện lô bằng một cache trong bộ nhớ, một lần khởi động lại sẽ
    khiến mọi lô cũ được xử lý LẦN HAI — và không có gì báo động.
    """
    sync = backend_restart["resend"]["sync"]
    assert sync["status"] == "replayed"
    assert sync["http_status"] == 200
    assert sync["sync_run_id"] == backend_restart["original_run_id"]
    assert len(backend_restart["runs_after_resend"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Ranh giới Phase 6, kiểm trên hệ ĐANG CHẠY
# ═══════════════════════════════════════════════════════════════════════════


def test_none_of_these_failure_windows_created_a_ranking_run():
    """Sáu cửa sổ hỏng, hàng chục lô, hai lần khởi động lại — và `ranking_runs`
    vẫn phải bằng 0. Động cơ xếp hạng chưa tồn tại; con số này là bằng chứng."""
    assert env.backend_rows("SELECT count(*) AS n FROM ranking_runs")[0]["n"] == 0
    assert env.backend_rows("SELECT count(*) AS n FROM ranking_scores")[0]["n"] == 0


def test_the_published_ranking_config_is_still_exactly_one():
    """`uq_ranking_configs_published` cho phép ĐÚNG một cấu hình `published`. Cấu
    hình hạt giống v1 của Phase 2 vẫn phải là cấu hình đó, không hơn không kém."""
    rows = env.backend_rows("SELECT version, status FROM ranking_configs WHERE status = 'published'")
    assert len(rows) == 1
    assert rows[0]["version"] == 1


def test_the_two_databases_are_still_isolated_after_every_restart():
    assert (
        env.crm_rows(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)",
            t=["units", "deals", "areas", "projects", "upload_files", "crm_source_records", "ranking_runs"],
        )
        == []
    )
    assert (
        env.backend_rows(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)",
            t=["crm_units", "crm_deals", "crm_outbox"],
        )
        == []
    )
