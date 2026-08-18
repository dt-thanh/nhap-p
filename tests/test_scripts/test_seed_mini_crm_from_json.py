"""Logic thuần và HTTP-giả của `scripts/seed_mini_crm_from_json.py`.

Không cần DB thật — mọi thứ ở đây kiểm CÁCH SCRIPT DIỄN GIẢI dữ liệu outbox/
Backend, dùng `httpx.MockTransport` để giả cả hai phía HTTP. Kết luận về hành vi
THẬT của Mini CRM/Backend nằm ở `minicrm/tests/test_crud_units.py` (409 mới) và
ở phần "Real integration tests" của báo cáo triển khai — chạy qua container thật.

Bốn câu hỏi mà bộ test này phải trả lời được, đúng như roadmap yêu cầu:

1. Dead letter LỊCH SỬ (có trước lần chạy) không được chặn một lần chạy sạch.
2. Một 4xx của CHÍNH lần chạy này PHẢI chặn.
3. Giao hàng phải được xác nhận đúng LÔ — không suy từ một dòng chiếu cũ.
4. Archive được báo `TOMBSTONE_CONFIRMED`/`TOMBSTONE_VERIFICATION_UNAVAILABLE`,
   không bao giờ là một "timeout" mơ hồ khi tombstone THẬT SỰ tồn tại.

Chạy: pytest tests/test_scripts/test_seed_mini_crm_from_json.py -q
(không cần TEST_TARGET/scripts/test_db.sh — không chạm DB nào).
"""

from __future__ import annotations

import json

import httpx
import pytest

from scripts.seed_mini_crm_from_json import (
    EXPECTED_DEAD_V1_ERROR_CODE,
    EXPECTED_DEAD_V1_STATUS,
    BackendVerifier,
    DependencyError,
    IdentityMap,
    ItemResult,
    MiniCrmSeeder,
    OutboxRow,
    OutboxTracker,
    _await_delivery,
    resend_guidance,
)

# --- OutboxRow: phân loại thuần, không I/O -----------------------------------


def test_null_http_status_is_pending_not_dead():
    row = OutboxRow("b1", "areas", None, None)
    assert row.delivery_state() == "OUTBOX_PENDING"
    assert not row.is_dead_letter


def test_2xx_is_delivery_accepted():
    row = OutboxRow("b1", "areas", 202, None)
    assert row.delivery_state() == "DELIVERY_ACCEPTED"


def test_5xx_is_retryable_not_dead():
    row = OutboxRow("b1", "areas", 503, "SOME_ERROR")
    assert row.delivery_state() == "DELIVERY_FAILED_RETRYABLE"
    assert not row.is_dead_letter


def test_4xx_is_a_dead_letter():
    row = OutboxRow("b1", "units_v2", 401, "INVALID_API_KEY")
    assert row.delivery_state() == "DEAD_LETTER"
    assert row.is_dead_letter


def test_expected_dead_v1_is_narrow_exact_entity_status_and_code():
    """Lane v1 chết CÓ CHỦ ĐÍCH — nhưng CHỈ đúng bộ ba (nhãn v1, 422,
    UNKNOWN_PROJECT). Đổi bất kỳ phần nào cũng phải RA KHỎI whitelist, vì đó
    chính là dấu hiệu của một hồi quy thật (vd. khoá đồng bộ hỏng -> 401)."""
    expected = OutboxRow("b1", "units", EXPECTED_DEAD_V1_STATUS, EXPECTED_DEAD_V1_ERROR_CODE)
    assert expected.is_expected_dead_v1

    wrong_status = OutboxRow("b1", "units", 401, EXPECTED_DEAD_V1_ERROR_CODE)
    assert not wrong_status.is_expected_dead_v1

    wrong_code = OutboxRow("b1", "units", EXPECTED_DEAD_V1_STATUS, "SOME_OTHER_CODE")
    assert not wrong_code.is_expected_dead_v1

    wrong_entity = OutboxRow("b1", "units_v2", EXPECTED_DEAD_V1_STATUS, EXPECTED_DEAD_V1_ERROR_CODE)
    assert not wrong_entity.is_expected_dead_v1, "v2 không có lane v1 chết để miễn trừ"


def test_resend_guidance_v1_gives_real_command():
    row = OutboxRow("mc-units-abc", "units", 422, "UNKNOWN_PROJECT")
    assert resend_guidance(row, "http://localhost:8100") == "POST http://localhost:8100/outbox/mc-units-abc/resend"


def test_resend_guidance_v2_states_no_route_exists():
    row = OutboxRow("mc-v2-areas-abc", "areas", 401, "INVALID_API_KEY")
    guidance = resend_guidance(row, "http://localhost:8100")
    assert "KHÔNG có lệnh gửi lại" in guidance
    assert "V2_DELIVERY_NOT_ENABLED" in guidance


# --- OutboxTracker: HTTP giả, quan hệ ảnh chụp / lô mới ----------------------


class _FakeOutbox:
    """Sổ outbox trong bộ nhớ + `httpx.MockTransport` khớp hình dạng thật của
    `GET /outbox` (`OutboxListOut`) và `GET /outbox/{id}` (`OutboxOut` +
    payload/response)."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, batch_id: str, entity: str, http_status: int | None, *, external_id: str, error_code: str | None = None):
        response = None
        if http_status is not None and http_status >= 400:
            response = {"error_code": error_code} if error_code else {}
        self.rows.append(
            {
                "external_batch_id": batch_id,
                "entity": entity,
                "http_status": http_status,
                "sent_at": None,
                "created_at": "2026-08-14T00:00:00Z",
                "attempts": 0 if http_status is None else 1,
                "last_error": None,
                "replay_of": None,
                "payload": {"records": [{"external_id": external_id}]},
                "response": response,
            }
        )

    def set_status(self, batch_id: str, http_status: int, error_code: str | None = None):
        for row in self.rows:
            if row["external_batch_id"] == batch_id:
                row["http_status"] = http_status
                row["response"] = {"error_code": error_code} if error_code else {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/outbox":
            limit = int(request.url.params.get("limit", 50))
            offset = int(request.url.params.get("offset", 0))
            # Mới nhất trước — khớp `crud.list_outbox`'s `ORDER BY created_at DESC`.
            ordered = list(reversed(self.rows))
            page = ordered[offset : offset + limit]
            items = [{k: v for k, v in r.items() if k not in ("payload", "response")} for r in page]
            return httpx.Response(200, json={"items": items, "total": len(ordered), "limit": limit, "offset": offset})
        batch_id = path.rsplit("/", 1)[-1]
        row = next((r for r in self.rows if r["external_batch_id"] == batch_id), None)
        if row is None:
            return httpx.Response(404, json={"error_code": "NOT_FOUND", "message": "no such batch"})
        return httpx.Response(200, json=row)


@pytest.fixture
def fake_outbox_client():
    store = _FakeOutbox()
    client = httpx.Client(transport=httpx.MockTransport(store.handler), base_url="http://minicrm.test")
    return store, client


def test_snapshot_puts_pre_existing_rows_in_historical_bucket(fake_outbox_client):
    store, client = fake_outbox_client
    store.add("old-1", "units", 422, external_id="U-0001", error_code="UNKNOWN_PROJECT")
    store.add("old-2", "areas", 401, external_id="A-0001", error_code="INVALID_API_KEY")

    tracker = OutboxTracker(client)
    tracker.snapshot()

    assert tracker.baseline_size() == 2
    assert len(tracker.historical_dead_letters()) == 2
    # Chưa có mutation nào sau ảnh chụp — chưa có gì thuộc lần chạy này.
    assert tracker.current_dead_letters() == []


def test_a_clean_rerun_has_zero_current_dead_letters_despite_history(fake_outbox_client):
    """Câu hỏi 1: lịch sử KHÔNG được chặn một lần chạy sạch."""
    store, client = fake_outbox_client
    store.add("old-1", "units", 422, external_id="U-0001", error_code="UNKNOWN_PROJECT")
    store.add("old-2", "units_v2", 401, external_id="U-0001", error_code="INVALID_API_KEY")

    tracker = OutboxTracker(client)
    tracker.snapshot()

    # "Lần chạy" này không sinh mutation nào mới (unchanged toàn bộ) — không
    # discover() gì cả, giống hệt một seed lần hai không đổi field nào.
    assert tracker.blocking_dead_letters() == []
    assert len(tracker.historical_dead_letters()) == 2


def test_a_new_4xx_this_run_is_discovered_and_blocks(fake_outbox_client):
    """Câu hỏi 2: một 4xx MỚI của lần chạy này phải vào bucket chặn."""
    store, client = fake_outbox_client
    store.add("old-1", "units", 422, external_id="U-0001", error_code="UNKNOWN_PROJECT")

    tracker = OutboxTracker(client)
    tracker.snapshot()

    # Mutation MỚI: một area bị Backend từ chối bằng 409 thật (không phải lane
    # v1 chết có chủ đích).
    store.add("new-1", "areas", 409, external_id="A-0099", error_code="AREA_NATURAL_KEY_CONFLICT")

    batches = tracker.discover(external_id="A-0099", entities=("areas",))
    assert batches == ["new-1"]
    row = tracker.wait_for_delivery("new-1", timeout=0.05, interval=0.01)
    assert row.delivery_state() == "DEAD_LETTER"

    assert len(tracker.blocking_dead_letters()) == 1
    assert tracker.blocking_dead_letters()[0].external_batch_id == "new-1"
    # Dòng lịch sử vẫn báo cáo, vẫn KHÔNG chặn.
    assert len(tracker.historical_dead_letters()) == 1


def test_expected_v1_dead_letter_this_run_does_not_block(fake_outbox_client):
    """Đúng cặp (nhãn v1, 422, UNKNOWN_PROJECT) của LẦN CHẠY NÀY — dự kiến theo
    cấu hình (`MINICRM_PROJECT_ID` cố ý không trỏ vào dự án thật) — không được
    lẫn vào bucket chặn, dù nó thuộc lần chạy hiện tại."""
    store, client = fake_outbox_client
    tracker = OutboxTracker(client)
    tracker.snapshot()

    store.add("new-1", "units", 422, external_id="U-0099", error_code="UNKNOWN_PROJECT")
    tracker.discover(external_id="U-0099", entities=("units", "units_v2"))
    tracker.wait_for_delivery("new-1", timeout=0.05, interval=0.01)

    assert len(tracker.expected_dead_v1()) == 1
    assert tracker.blocking_dead_letters() == []


def test_discover_does_not_pick_up_a_different_records_batch(fake_outbox_client):
    """`discover()` khớp theo `external_id` NẰM TRONG payload — một batch của
    bản ghi khác không được gán nhầm cho mutation đang xét."""
    store, client = fake_outbox_client
    tracker = OutboxTracker(client)
    tracker.snapshot()

    store.add("new-1", "areas", 202, external_id="A-0001")
    store.add("new-2", "areas", 202, external_id="A-0002")

    batches = tracker.discover(external_id="A-0002", entities=("areas",))
    assert batches == ["new-2"]


# --- _await_delivery: cổng chặn projection --------------------------------


class _StubTracker:
    """Đóng vai `OutboxTracker` chỉ cho `wait_for_delivery`, để test
    `_await_delivery` không cần dựng HTTP giả lần thứ hai."""

    def __init__(self, rows_by_batch: dict[str, OutboxRow]) -> None:
        self._rows = rows_by_batch

    def wait_for_delivery(self, batch_id: str, *, timeout: float, interval: float) -> OutboxRow:
        return self._rows[batch_id]


def _mutated_item(*, batches: list[str]) -> ItemResult:
    return ItemResult("k1", "area", "created", "A-0001", batches=list(batches))


def test_no_batches_found_is_outbox_pending_not_accepted():
    item = _mutated_item(batches=[])
    _await_delivery(_StubTracker({}), item, timeout=0.01, interval=0.01)
    assert item.delivery == "OUTBOX_PENDING"


def test_delivery_accepted_when_the_exact_batch_reaches_2xx():
    item = _mutated_item(batches=["b1"])
    tracker = _StubTracker({"b1": OutboxRow("b1", "areas", 202, None)})
    _await_delivery(tracker, item, timeout=0.01, interval=0.01)
    assert item.delivery == "DELIVERY_ACCEPTED"


def test_a_dead_letter_on_the_exact_batch_blocks_even_if_status_says_created():
    """Câu hỏi 3+4: mutation CRUD "thành công" cục bộ nhưng LÔ CHÍNH NÓ sinh ra
    bị Backend từ chối -> KHÔNG được coi là giao hàng thành công, bất kể
    `status='created'` ở tầng Mini CRM."""
    item = _mutated_item(batches=["b1"])
    tracker = _StubTracker({"b1": OutboxRow("b1", "areas", 409, "AREA_NATURAL_KEY_CONFLICT")})
    _await_delivery(tracker, item, timeout=0.01, interval=0.01)
    assert item.delivery == "DEAD_LETTER"


def test_expected_v1_dead_letter_alone_does_not_flip_delivery_to_accepted():
    """Một unit sinh HAI lô (v1 chết có chủ đích + v2). Nếu v2 chưa có kết quả
    (NULL) thì tổng thể vẫn PENDING — lô v1 chết KHÔNG được phép "che" cho v2
    còn treo."""
    item = ItemResult("u1", "unit", "created", "U-0001", batches=["v1-batch", "v2-batch"])
    tracker = _StubTracker(
        {
            "v1-batch": OutboxRow("v1-batch", "units", 422, "UNKNOWN_PROJECT"),
            "v2-batch": OutboxRow("v2-batch", "units_v2", None, None),
        }
    )
    _await_delivery(tracker, item, timeout=0.01, interval=0.01)
    assert item.delivery == "OUTBOX_PENDING"


def test_expected_v1_dead_letter_plus_accepted_v2_is_accepted():
    item = ItemResult("u1", "unit", "created", "U-0001", batches=["v1-batch", "v2-batch"])
    tracker = _StubTracker(
        {
            "v1-batch": OutboxRow("v1-batch", "units", 422, "UNKNOWN_PROJECT"),
            "v2-batch": OutboxRow("v2-batch", "units_v2", 202, None),
        }
    )
    _await_delivery(tracker, item, timeout=0.01, interval=0.01)
    assert item.delivery == "DELIVERY_ACCEPTED"


def test_unchanged_item_is_no_mutation_and_never_waits():
    item = ItemResult("k1", "area", "unchanged", "A-0001")
    _await_delivery(_StubTracker({}), item, timeout=0.01, interval=0.01)
    assert item.delivery == "NO_MUTATION"


# --- BackendVerifier: xác minh chiếu + tombstone ------------------------------


class _FakeBackend:
    """`GET /api/v1/{projects,areas,inventory,deals}` giả — đủ hình dạng để
    `BackendVerifier` chạy thật qua nó."""

    def __init__(self) -> None:
        self.projects: list[dict] = []
        self.areas: list[dict] = []
        self.units: list[dict] = []
        self.deals: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/projects":
            return httpx.Response(200, json=self.projects)
        if path.startswith("/api/v1/projects/"):
            eid = path.rsplit("/", 1)[-1]
            row = next((r for r in self.projects if r.get("external_id") == eid), None)
            if row is None:
                return httpx.Response(404, json={"error_code": "PROJECT_NOT_FOUND", "message": "not found"})
            return httpx.Response(200, json=row)
        if path == "/api/v1/areas":
            return httpx.Response(200, json=self.areas)
        if path == "/api/v1/inventory":
            include_deleted = request.url.params.get("include_deleted") == "true"
            units = self.units if include_deleted else [u for u in self.units if not u.get("deleted_at")]
            return httpx.Response(200, json={"units": units})
        if path == "/api/v1/deals":
            include_deleted = request.url.params.get("include_deleted") == "true"
            items = self.deals if include_deleted else [d for d in self.deals if not d.get("deleted_at")]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404)


@pytest.fixture
def verifier():
    backend = _FakeBackend()
    client = httpx.Client(transport=httpx.MockTransport(backend.handler), base_url="http://backend.test")
    return backend, BackendVerifier(client, timeout=0.05, interval=0.01)


def test_project_projection_confirmed_at_or_above_expected_revision(verifier):
    backend, v = verifier
    backend.projects.append({"external_id": "P-0004", "source_revision": 3, "status": "active"})
    item = ItemResult("p1", "project", "updated", "P-0004", expected_revision=3)

    state, detail = v.project(item)

    assert state == "PROJECTION_CONFIRMED"
    assert "3" in detail


def test_project_stale_revision_from_a_previous_run_is_unconfirmed_not_masked(verifier):
    """Câu hỏi 3+4, ở tầng Backend: một dòng chiếu CŨ (revision thấp hơn cái
    lần ghi này vừa tạo ra) không được đọc thành thành công."""
    backend, v = verifier
    backend.projects.append({"external_id": "P-0004", "source_revision": 2, "status": "active"})
    item = ItemResult("p1", "project", "updated", "P-0004", expected_revision=5)

    state, detail = v.project(item)

    assert state == "PROJECTION_UNCONFIRMED"
    assert "5" in detail


def test_project_archive_confirmed_as_tombstone(verifier):
    backend, v = verifier
    backend.projects.append({"external_id": "P-0004", "source_revision": 4, "status": "archived"})
    item = ItemResult("p1", "project", "archived", "P-0004", operation="archive")

    state, _ = v.project(item)

    assert state == "TOMBSTONE_CONFIRMED"


def test_area_archive_is_unavailable_not_a_false_pass(verifier):
    """`AreaOut` không phơi status/deleted_at — không có PASS giả nào được phép
    ở đây, kể cả khi dòng vẫn hiện diện."""
    backend, v = verifier
    backend.areas.append({"external_id": "A-0005", "source_revision": 3})
    item = ItemResult("a1", "area", "archived", "A-0005", operation="archive")

    state, reason = v.area(item, "P-0004")

    assert state == "TOMBSTONE_VERIFICATION_UNAVAILABLE"
    assert "AreaOut" in reason


def test_area_archive_missing_row_entirely_is_unconfirmed_not_unavailable():
    """Phân biệt hai lý do khác nhau: 'không có đường đọc' (unavailable, dòng
    CÓ tồn tại) khác hẳn 'dòng không hề chiếu' (unconfirmed, có thể là lỗi
    thật)."""
    backend = _FakeBackend()
    client = httpx.Client(transport=httpx.MockTransport(backend.handler), base_url="http://backend.test")
    v = BackendVerifier(client, timeout=0.05, interval=0.01)
    item = ItemResult("a1", "area", "archived", "A-9999", operation="archive")

    state, _ = v.area(item, "P-0004")

    assert state == "PROJECTION_UNCONFIRMED"


def test_unit_archive_confirmed_via_include_deleted_and_deleted_at(verifier):
    backend, v = verifier
    backend.units.append({"external_unit_id": "U-0009", "deleted_at": "2026-08-14T00:00:00Z", "status": "sold"})
    item = ItemResult("u1", "unit", "archived", "U-0009", operation="archive")

    state, detail = v.unit(item, "P-0004", "A-0005")

    assert state == "TOMBSTONE_CONFIRMED"
    assert "deleted_at" in detail


def test_unit_archive_not_yet_tombstoned_is_unconfirmed_not_a_false_pass(verifier):
    backend, v = verifier
    backend.units.append({"external_unit_id": "U-0009", "deleted_at": None, "status": "sold"})
    item = ItemResult("u1", "unit", "archived", "U-0009", operation="archive")

    state, _ = v.unit(item, "P-0004", "A-0005")

    assert state == "PROJECTION_UNCONFIRMED"


def test_unit_update_confirmed_by_business_field_not_revision(verifier):
    """`InventoryUnitOut` không có `source_revision` — xác minh bằng trường
    nghiệp vụ đã đổi."""
    backend, v = verifier
    backend.units.append({"external_unit_id": "U-0009", "status": "sold", "deleted_at": None})
    item = ItemResult("u1", "unit", "updated", "U-0009", expected_field=("status", "sold"))

    state, detail = v.unit(item, "P-0004", "A-0005")

    assert state == "PROJECTION_CONFIRMED"
    assert "sold" in detail


def test_unit_update_wrong_field_value_is_unconfirmed():
    backend = _FakeBackend()
    backend.units.append({"external_unit_id": "U-0009", "status": "available", "deleted_at": None})
    client = httpx.Client(transport=httpx.MockTransport(backend.handler), base_url="http://backend.test")
    v = BackendVerifier(client, timeout=0.05, interval=0.01)
    item = ItemResult("u1", "unit", "updated", "U-0009", expected_field=("status", "sold"))

    state, _ = v.unit(item, "P-0004", "A-0005")

    assert state == "PROJECTION_UNCONFIRMED"


def test_deal_archive_confirmed_as_tombstone(verifier):
    backend, v = verifier
    backend.deals.append({"external_deal_id": "D-0003", "deleted_at": "2026-08-14T00:00:00Z", "source_revision": 2})
    item = ItemResult("d1", "deal", "archived", "D-0003", operation="archive")

    state, _ = v.deal(item, "P-0004")

    assert state == "TOMBSTONE_CONFIRMED"


# --- MiniCrmSeeder: hồi quy cho ánh xạ danh tính cũ (lỗi 'unit-sim-1' cần -----
# 'area-sim-a') ----------------------------------------------------------------
#
# Tái hiện đúng kịch bản thật: `IdentityMap` (docs/.mini_crm_seed_state.json)
# còn giữ `external_key -> external_id` từ một lần chạy TRƯỚC, nhưng bản ghi đó
# đã KHÔNG CÒN tồn tại ở Mini CRM (vd. DB được migrate lại từ đầu) — GET theo id
# cũ trả 404. Không cần DB thật: `httpx.MockTransport` giả toàn bộ `/projects`,
# `/areas`, `/units` của Mini CRM.


class _NullTracker:
    """Đóng vai `OutboxTracker` cho `MiniCrmSeeder._track()` mà không cần giả
    `/outbox` — các test dưới đây kiểm hành vi CRUD/ánh xạ danh tính, không kiểm
    outbox (đã có bộ test riêng ở trên)."""

    def discover(self, *, external_id: str, entities: tuple[str, ...]) -> list[str]:
        return []


class _FakeMiniCrm:
    """Mini CRM giả tối thiểu: đủ hình dạng `/projects`, `/areas`, `/units` để
    `MiniCrmSeeder` chạy thật qua nó — sinh `external_id` theo dãy riêng, KHÔNG
    BAO GIỜ trùng với một `external_key` cục bộ (vd. 'prj-sim'), đúng ranh giới
    mà script phải giữ."""

    def __init__(self) -> None:
        self.projects: dict[str, dict] = {}
        self.areas: dict[str, dict] = {}
        self.units: dict[str, dict] = {}
        self._next = {"projects": 1, "areas": 1, "units": 1}
        self.requests: list[tuple[str, str, dict | None]] = []
        # Đặt khác None để buộc `POST /areas` thất bại THẬT (không phải do danh
        # tính cũ) — dùng cho test "cha thất bại thì con không được tạo".
        self.fail_area_create: tuple[int, dict] | None = None

    def _new_id(self, kind: str, prefix: str) -> str:
        n = self._next[kind]
        self._next[kind] += 1
        return f"{prefix}-{n:04d}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((method, path, body))
        now = "2026-08-14T00:00:00Z"

        if method == "POST" and path == "/projects":
            eid = self._new_id("projects", "P")
            record = {
                "external_id": eid,
                "name": body["name"],
                "launch_date": body["launch_date"],
                "status": "active",
                "source_revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            self.projects[eid] = record
            return httpx.Response(201, json={"record": record})

        if method == "GET" and path.startswith("/projects/"):
            record = self.projects.get(path.rsplit("/", 1)[-1])
            if record is None:
                return httpx.Response(404, json={"error_code": "PROJECT_NOT_FOUND", "message": "not found"})
            return httpx.Response(200, json=record)

        if method == "PATCH" and path.startswith("/projects/"):
            record = self.projects[path.rsplit("/", 1)[-1]]
            record.update(body)
            record["source_revision"] += 1
            return httpx.Response(200, json={"record": record})

        if method == "POST" and path == "/areas":
            if self.fail_area_create is not None:
                status, err_body = self.fail_area_create
                return httpx.Response(status, json=err_body)
            eid = self._new_id("areas", "A")
            record = {
                "external_id": eid,
                "external_project_id": body["external_project_id"],
                "area_name": body["area_name"],
                "unit_type": body["unit_type"],
                "bedrooms": body["bedrooms"],
                "area_sqm": body["area_sqm"],
                "total_units": body["total_units"],
                "status": "active",
                "source_revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            self.areas[eid] = record
            return httpx.Response(201, json={"record": record})

        if method == "GET" and path == "/areas":
            proj = request.url.params.get("external_project_id")
            rows = [r for r in self.areas.values() if proj is None or r["external_project_id"] == proj]
            return httpx.Response(200, json=rows)

        if method == "GET" and path.startswith("/areas/"):
            record = self.areas.get(path.rsplit("/", 1)[-1])
            if record is None:
                return httpx.Response(404, json={"error_code": "AREA_NOT_FOUND", "message": "not found"})
            return httpx.Response(200, json=record)

        if method == "PATCH" and path.startswith("/areas/"):
            record = self.areas[path.rsplit("/", 1)[-1]]
            record.update(body)
            record["source_revision"] += 1
            return httpx.Response(200, json={"record": record})

        if method == "GET" and path == "/units":
            return httpx.Response(200, json=list(self.units.values()))

        if method == "POST" and path == "/units":
            eid = self._new_id("units", "U")
            area = self.areas.get(body.get("external_area_id"))
            record = {
                "external_id": eid,
                "area_name": area["area_name"] if area else None,
                "unit_type": area["unit_type"] if area else None,
                "unit_code": body["unit_code"],
                "unit_status": body["unit_status"],
                "source_revision": 1,
                "deleted_at": None,
                "created_at": now,
                "updated_at": now,
            }
            self.units[eid] = record
            return httpx.Response(201, json={"record": record})

        if method == "GET" and path.startswith("/units/"):
            record = self.units.get(path.rsplit("/", 1)[-1])
            if record is None:
                return httpx.Response(404, json={"error_code": "UNIT_NOT_FOUND", "message": "not found"})
            return httpx.Response(200, json=record)

        if method == "PATCH" and path.startswith("/units/"):
            record = self.units[path.rsplit("/", 1)[-1]]
            record.update(body)
            record["source_revision"] += 1
            return httpx.Response(200, json={"record": record})

        raise AssertionError(f"unhandled {method} {path}")


def _seeder(store: _FakeMiniCrm, identity: IdentityMap) -> MiniCrmSeeder:
    client = httpx.Client(transport=httpx.MockTransport(store.handler), base_url="http://minicrm.test")
    return MiniCrmSeeder(client, identity, _NullTracker(), mirror_timeout=0.05, mirror_interval=0.01)


_PROJECT_ITEM = {
    "external_key": "prj-sim",
    "operation": "upsert",
    "name": "SIM CRUD Simulator Project v2",
    "launch_date": "2026-06-01",
}
_AREA_ITEM = {
    "external_key": "area-sim-a",
    "operation": "upsert",
    "project_external_key": "prj-sim",
    "area_name": "SIM Toa A",
    "unit_type": "Can ho 2PN",
    "bedrooms": 2,
    "area_sqm": 65.5,
    "total_units": 22,
}
_UNIT_ITEM = {
    "external_key": "unit-sim-1",
    "operation": "upsert",
    "area_external_key": "area-sim-a",
    "unit_code": "SIM-A-0101",
    "unit_status": "sold",
}


def test_stale_identity_entries_self_heal_and_unit_resolves_to_recreated_area(tmp_path):
    """Tái hiện đúng lỗi thật: `IdentityMap` trỏ tới 'P-STALE'/'A-STALE' — hai id
    KHÔNG tồn tại ở Mini CRM giả (giống DB bị migrate lại). Sau sửa, script phải
    tự phục hồi: coi ánh xạ cũ là hỏng, tạo lại project/area MỚI, và unit phải
    khớp đúng area vừa tạo lại — không phải dừng bằng `DependencyError`."""
    store = _FakeMiniCrm()
    identity = IdentityMap(tmp_path / "state.json")
    identity.set("prj-sim", "project", "P-STALE")
    identity.set("area-sim-a", "area", "A-STALE")
    seeder = _seeder(store, identity)

    project_ids = seeder.seed_projects([_PROJECT_ITEM])
    assert "prj-sim" in project_ids, "project phải tự tạo lại khi ánh xạ cũ 404, không được thất bại vĩnh viễn"
    assert project_ids["prj-sim"] != "P-STALE"
    assert project_ids["prj-sim"] in store.projects

    area_ids = seeder.seed_areas([_AREA_ITEM], project_ids)
    assert "area-sim-a" in area_ids, "area phải tự tạo lại khi ánh xạ cũ 404, không được thất bại vĩnh viễn"
    assert area_ids["area-sim-a"] != "A-STALE"
    assert area_ids["area-sim-a"] in store.areas

    # Cùng đúng bốn dòng main() dùng để dựng `area_info` (scripts/seed_mini_crm_
    # from_json.py, main(), sau seed_areas) — mapping PHẢI mang cả external_id
    # lẫn area_name, không chỉ một trong hai.
    area_info = {}
    for a in [_AREA_ITEM]:
        aid = area_ids.get(a["external_key"])
        if aid:
            area_info[a["external_key"]] = {"external_id": aid, "area_name": a["area_name"]}
    assert area_info["area-sim-a"] == {"external_id": area_ids["area-sim-a"], "area_name": "SIM Toa A"}

    unit_ids = seeder.seed_units([_UNIT_ITEM], area_info)
    assert "unit-sim-1" in unit_ids
    created_unit = store.units[unit_ids["unit-sim-1"]]
    assert created_unit["area_name"] == "SIM Toa A"
    assert not seeder.summary.failures()


def test_unit_not_created_when_area_upsert_genuinely_fails(tmp_path):
    """Khác test trên: đây là thất bại THẬT của area (422 thật từ Mini CRM), không
    phải danh tính cũ 404 — con KHÔNG được tự đoán hay tạo, và không request nào
    được gửi tới `/units`."""
    store = _FakeMiniCrm()
    store.fail_area_create = (422, {"error_code": "PROJECT_NOT_FOUND", "message": "boom"})
    identity = IdentityMap(tmp_path / "state.json")
    seeder = _seeder(store, identity)

    project_ids = seeder.seed_projects([_PROJECT_ITEM])
    area_ids = seeder.seed_areas([_AREA_ITEM], project_ids)
    assert "area-sim-a" not in area_ids
    assert any(r.kind == "area" and r.status == "failed" for r in seeder.summary.results)

    with pytest.raises(DependencyError) as exc_info:
        seeder.seed_units([_UNIT_ITEM], area_info={})

    assert exc_info.value.fixture_key == "unit-sim-1"
    assert exc_info.value.parent_fixture_key == "area-sim-a"
    assert not any(m == "POST" and p == "/units" for m, p, _ in store.requests)


def test_rerun_after_self_heal_is_idempotent_no_duplicate_writes(tmp_path):
    """Lần chạy thứ hai, với `IdentityMap` giờ trỏ tới bản ghi THẬT (không còn
    cũ) — phải là no-op hoàn toàn: không `POST` mới nào tới `/projects`/`/areas`."""
    store = _FakeMiniCrm()
    identity = IdentityMap(tmp_path / "state.json")
    seeder1 = _seeder(store, identity)
    project_ids = seeder1.seed_projects([_PROJECT_ITEM])
    seeder1.seed_areas([_AREA_ITEM], project_ids)
    assert len(store.projects) == 1
    assert len(store.areas) == 1
    posts_after_first_run = sum(1 for m, _, _ in store.requests if m == "POST")

    seeder2 = _seeder(store, identity)
    project_ids_2 = seeder2.seed_projects([_PROJECT_ITEM])
    seeder2.seed_areas([_AREA_ITEM], project_ids_2)

    assert len(store.projects) == 1, "rerun không được tạo project thứ hai"
    assert len(store.areas) == 1, "rerun không được tạo area thứ hai"
    assert all(r.status == "unchanged" for r in seeder2.summary.results)
    new_posts = sum(1 for m, _, _ in store.requests if m == "POST") - posts_after_first_run
    assert new_posts == 0


def test_generated_external_id_is_never_confused_with_the_fixture_external_key(tmp_path):
    """`external_key` ('prj-sim') là khoá CỤC BỘ của file fixture — KHÔNG BAO GIỜ
    được gửi lên Mini CRM, và KHÔNG BAO GIỜ được dùng làm `external_id` (Mini CRM
    tự cấp qua dãy `P-0001`, ...)."""
    store = _FakeMiniCrm()
    identity = IdentityMap(tmp_path / "state.json")
    seeder = _seeder(store, identity)

    project_ids = seeder.seed_projects([_PROJECT_ITEM])

    post_body = next(body for m, p, body in store.requests if m == "POST" and p == "/projects")
    assert set(post_body.keys()) == {"name", "launch_date"}, "payload gửi lên KHÔNG được lộ external_key cục bộ"
    assert project_ids["prj-sim"] != "prj-sim"
    assert project_ids["prj-sim"].startswith("P-")
    assert identity.get("prj-sim") == project_ids["prj-sim"]


def test_dependency_error_names_child_and_missing_parent_before_any_http_call(tmp_path):
    """`seed_units` phải nhận ra cha vắng mặt và raise `DependencyError` TRƯỚC KHI
    gọi API nào — 'validate missing parents before creating children'."""

    def _forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("seed_units không được gọi API trước khi giải xong area cha")

    client = httpx.Client(transport=httpx.MockTransport(_forbidden), base_url="http://minicrm.test")
    identity = IdentityMap(tmp_path / "state.json")
    seeder = MiniCrmSeeder(client, identity, _NullTracker(), mirror_timeout=0.05, mirror_interval=0.01)

    with pytest.raises(DependencyError) as exc_info:
        seeder.seed_units([_UNIT_ITEM], area_info={})

    err = exc_info.value
    assert err.fixture_key == "unit-sim-1"
    assert err.parent_fixture_key == "area-sim-a"
    assert "area_name" in err.expected_identity
