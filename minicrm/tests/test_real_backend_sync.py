"""HTTP THẬT, container THẬT, hai database THẬT. Không mock ở bất kỳ đâu.

    pytest chạy trên máy host
      → HTTP  → container `minicrm`  (localhost:8100)
                  → ghi database `minicrm`      (localhost:5433)
                  → HTTP → container `api`      (http://api:8000, mạng compose)
                             → ghi database backend (localhost:5432)
      → SQL   → đọc và đối chiếu CẢ HAI database

Mọi test khác trong `minicrm/tests/` dùng `FakeBackend` — chúng chứng minh Mini
CRM dựng ra đúng cái gì, không chứng minh backend làm gì. File này là chỗ DUY NHẤT
kết luận được về hành vi của phía nhận, vì nó là chỗ duy nhất thật sự hỏi phía nhận.

╔══════════════════════════════════════════════════════════════════════════╗
║  MỘT VÒNG HTTP XANH Ở ĐÂY KHÔNG PHẢI BẰNG CHỨNG TƯƠNG THÍCH CRM THẬT.    ║
║                                                                          ║
║  Mini CRM do CHÍNH DỰ ÁN NÀY viết, theo CHÍNH CÁCH ĐỌC hợp đồng của dự    ║
║  án. Nó chứng minh đúng một điều: hợp đồng v1 và phía nhận hiện tại khớp  ║
║  nhau. Nó KHÔNG nói gì về việc một CRM có thật sẽ phát ra hình dạng nào,  ║
║  dùng từ vựng trạng thái nào, hay có cấp được `source_revision` hay không.║
║                                                                          ║
║  Rủi ro ở Phase 4 LỚN HƠN Phase 3 chứ không nhỏ đi: một hệ thống có CRUD  ║
║  đầy đủ và một vòng HTTP xanh trông thuyết phục hơn hẳn một file JSON.    ║
╚══════════════════════════════════════════════════════════════════════════╝

Chạy:

    docker compose up -d --build minicrm_db minicrm
    pytest minicrm/tests/test_real_backend_sync.py -v

Test tự đọc `.env` của repo để dựng chuỗi kết nối — đúng chỗ mà compose cũng đọc,
nên không có tham số nào phải nhớ và không có mật khẩu nào nằm trong mã nguồn.

Test này GHI vào database dev của backend dưới danh nghĩa `mini-crm-dev`. Đó là
chủ đích: chứng minh luồng nhận bằng dữ liệu thật đi qua đường thật. `external_id`
lấy từ dãy của Mini CRM nên mỗi lần chạy dùng một bộ id mới, không đè lên lần trước.
"""

from __future__ import annotations

import subprocess
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]

MINICRM_URL = "http://localhost:8100"
BACKEND_URL = "http://localhost:8000"

AREA_NAME = "DEMO Toà B1"
UNIT_TYPE = "Căn hộ"

RESERVED_AT = (datetime.now(UTC) - timedelta(days=10)).replace(microsecond=0)
SOLD_AT = (datetime.now(UTC) - timedelta(days=3)).replace(microsecond=0)


def _env() -> dict[str, str]:
    """Đọc `.env` của repo mà KHÔNG thực thi nó.

    `source .env` sẽ chạy nội dung file: một dòng như `FORECAST_CRON=0 2 * * *`
    biến thành lệnh, và mọi `$(...)` trong file bị thực thi thật. Ở đây chỉ tách
    KEY=VALUE — cùng cách mà `scripts/test_db.sh` làm, vì cùng lý do.
    """
    values: dict[str, str] = {}
    path = REPO_ROOT / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


ENV = _env()


def _dsn(user: str, password: str, database: str, port: int) -> str:
    return (
        f"postgresql+psycopg2://{urllib.parse.quote(user, safe='')}:"
        f"{urllib.parse.quote(password, safe='')}@localhost:{port}/{database}"
    )


BACKEND_DSN = _dsn(
    ENV.get("POSTGRES_USER", "app"), ENV.get("POSTGRES_PASSWORD", "app"), ENV.get("POSTGRES_DB", "absorption"), 5432
)
CRM_DSN = _dsn(
    ENV.get("MINICRM_POSTGRES_USER", "minicrm"),
    ENV.get("MINICRM_POSTGRES_PASSWORD", "minicrm"),
    ENV.get("MINICRM_POSTGRES_DB", "minicrm"),
    5433,
)
SOURCE_INSTANCE = ENV.get("MINICRM_SOURCE_INSTANCE_ID", "mini-crm-dev")
PROJECT_ID = ENV.get("MINICRM_PROJECT_ID", "")

# Xác thực GHI Mini CRM (D-14). `_crm()` gắn header admin (phạm vi ALL) theo mặc
# định — file này kiểm hành vi đồng bộ/idempotency, không kiểm chính cơ chế xác
# thực (đó là `test_real_auth.py`).
ADMIN_AUTH_HEADER = {"Authorization": f"Bearer {ENV.get('MINICRM_AUTH_ADMIN_TOKEN', '')}"}


def _reachable(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


_SKIP = ""
if not _reachable(MINICRM_URL):
    _SKIP = f"Container Mini CRM không phản hồi ở {MINICRM_URL} — chạy: docker compose up -d --build minicrm_db minicrm"
elif not _reachable(BACKEND_URL):
    _SKIP = f"Backend không phản hồi ở {BACKEND_URL} — chạy: docker compose up -d api"
elif not PROJECT_ID:
    _SKIP = "`.env` thiếu MINICRM_PROJECT_ID — Mini CRM không biết đẩy vào dự án nào"

pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP)


# --- Tiện ích -----------------------------------------------------------------


def _crm(method: str, path: str, **kwargs) -> httpx.Response:
    headers = {**ADMIN_AUTH_HEADER, **kwargs.pop("headers", {})}
    with httpx.Client(base_url=MINICRM_URL, timeout=30.0) as client:
        return client.request(method, path, headers=headers, **kwargs)


def _query(dsn: str, sql: str, **params):
    engine = sa.create_engine(dsn)
    try:
        with engine.connect() as conn:
            return [dict(row) for row in conn.execute(sa.text(sql), params).mappings()]
    finally:
        engine.dispose()


def backend_rows(sql: str, **params):
    return _query(BACKEND_DSN, sql, **params)


def crm_rows(sql: str, **params):
    return _query(CRM_DSN, sql, **params)


def nonzero(counters: dict) -> dict:
    """Bỏ những khoá bằng 0 khỏi `decisions`/`projections`.

    Backend trả về TOÀN BỘ từ vựng quyết định, phần lớn bằng 0 — đó là hình dạng
    ổn định cho người đọc bảng điều khiển. Khẳng định trên dict đầy đủ sẽ khiến
    test hỏng mỗi lần backend thêm một loại quyết định mới, mà việc đó không nói
    lên điều gì về tính tương thích. Lọc số 0 rồi so trên phần CÓ THẬT.
    """
    return {key: value for key, value in (counters or {}).items() if value}


def _compose(*args: str) -> None:
    result = subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"docker compose {' '.join(args)} thất bại:\n{result.stderr}"


def _wait_for_backend(timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _reachable(BACKEND_URL):
            return
        time.sleep(2)
    raise AssertionError(f"Backend không sống lại sau {timeout}s")


# --- Toàn bộ kịch bản chạy MỘT LẦN; mỗi test khẳng định một phần -------------


@pytest.fixture(scope="module")
def flow():
    """Chạy trọn vòng CRUD → đồng bộ, ghi lại mọi phản hồi để các test soi.

    Chạy một lần thay vì mỗi test một vòng vì các bước phụ thuộc nhau THẬT (không
    có căn thì không có giao dịch; không có phiên bản mới thì không có lô cũ để
    phát lại). Tách ra sẽ phải dựng lại tiền đề ở mỗi test, và lúc đó phần lớn
    thời gian chạy là dựng tiền đề chứ không phải kiểm.
    """
    state: dict = {}
    suffix = uuid.uuid4().hex[:6]

    # --- 1. Tạo căn ---------------------------------------------------------
    created = _crm(
        "POST",
        "/units",
        json={
            "area_name": AREA_NAME,
            "unit_type": UNIT_TYPE,
            "unit_code": f"IT-{suffix}",
            "unit_status": "available",
        },
    )
    assert created.status_code == 201, created.text
    state["unit_create"] = created.json()
    unit_id = state["unit_id"] = state["unit_create"]["record"]["external_id"]
    state["unit_create_batch"] = state["unit_create"]["sync"]["external_batch_id"]

    # --- 2. Sửa căn (phiên bản 2) ------------------------------------------
    updated = _crm("PATCH", f"/units/{unit_id}", json={"unit_status": "reserved"})
    assert updated.status_code == 200, updated.text
    state["unit_update"] = updated.json()

    # Chụp CẢ HAI phía ngay tại đây. Đọc lại ở cuối vòng sẽ thấy trạng thái sau
    # khi căn đã bị tombstone, và phép đối chiếu "hai bên khớp nhau sau lần sửa"
    # sẽ nói về một thời điểm khác với thời điểm nó nhận là đang nói tới.
    state["unit_create_outbox"] = _crm("GET", f"/outbox/{state['unit_create_batch']}").json()
    state["crm_unit_after_update"] = crm_rows(
        "SELECT external_id, unit_code, unit_status, source_revision, mirrored_revision "
        "FROM crm_units WHERE external_id = :e",
        e=unit_id,
    )[0]
    state["backend_unit_after_update"] = backend_rows(
        "SELECT external_unit_id, unit_code, status, source_revision FROM units "
        "WHERE source_instance_id = :i AND external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=unit_id,
    )[0]

    # --- 3. Tạo giao dịch SAU căn ------------------------------------------
    deal = _crm("POST", "/deals", json={"external_unit_id": unit_id, "deal_status": "lead"})
    assert deal.status_code == 201, deal.text
    state["deal_create"] = deal.json()
    deal_id = state["deal_id"] = state["deal_create"]["record"]["external_id"]

    # --- 4. lead → reserved → sold (chốt A4) --------------------------------
    reserved = _crm(
        "PATCH",
        f"/deals/{deal_id}",
        json={"deal_status": "reserved", "reserved_at": RESERVED_AT.isoformat()},
    )
    assert reserved.status_code == 200, reserved.text
    state["deal_reserved"] = reserved.json()

    sold = _crm("PATCH", f"/deals/{deal_id}", json={"deal_status": "sold", "sold_at": SOLD_AT.isoformat()})
    assert sold.status_code == 200, sold.text
    state["deal_sold"] = sold.json()

    state["crm_deal_after_sold"] = crm_rows(
        "SELECT deal_status, reserved_at, sold_at, lost_at, source_revision FROM crm_deals WHERE external_id = :e",
        e=deal_id,
    )[0]
    state["backend_deal_after_sold"] = backend_rows(
        "SELECT status, source_status, reserved_at, sold_at, lost_at, source_revision FROM deals "
        "WHERE source_instance_id = :i AND external_deal_id = :e",
        i=SOURCE_INSTANCE,
        e=deal_id,
    )[0]

    # --- 5. Giao dịch trỏ tới căn KHÔNG tồn tại -----------------------------
    state["outbox_total_before_orphan"] = _crm("GET", "/outbox").json()["total"]
    state["orphan_deal"] = _crm(
        "POST", "/deals", json={"external_unit_id": f"U-KHONG-TON-TAI-{suffix}", "deal_status": "lead"}
    )
    state["outbox_total_after_orphan"] = _crm("GET", "/outbox").json()["total"]

    # --- 6. Gửi lại ĐÚNG lô cũ ---------------------------------------------
    state["backend_units_before_resend"] = backend_rows(
        "SELECT count(*) AS n FROM units WHERE source_instance_id = :i", i=SOURCE_INSTANCE
    )[0]["n"]
    resent = _crm("POST", f"/outbox/{state['unit_create_batch']}/resend")
    assert resent.status_code == 200, resent.text
    state["resend"] = resent.json()
    state["backend_units_after_resend"] = backend_rows(
        "SELECT count(*) AS n FROM units WHERE source_instance_id = :i", i=SOURCE_INSTANCE
    )[0]["n"]

    # --- 7. Phát lại bản CŨ (revision 1 trong khi hiện tại là 2) ------------
    state["unit_before_stale"] = backend_rows(
        "SELECT status, source_revision FROM units WHERE source_instance_id = :i AND external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=unit_id,
    )[0]
    stale = _crm("POST", "/outbox/replay-stale", json={"external_batch_id": state["unit_create_batch"]})
    assert stale.status_code == 200, stale.text
    state["replay_stale"] = stale.json()
    state["unit_after_stale"] = backend_rows(
        "SELECT status, source_revision FROM units WHERE source_instance_id = :i AND external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=unit_id,
    )[0]

    # --- 8. Xoá giao dịch rồi xoá căn --------------------------------------
    deleted_deal = _crm("DELETE", f"/deals/{deal_id}")
    assert deleted_deal.status_code == 200, deleted_deal.text
    state["deal_delete"] = deleted_deal.json()

    deleted_unit = _crm("DELETE", f"/units/{unit_id}")
    assert deleted_unit.status_code == 200, deleted_unit.text
    state["unit_delete"] = deleted_unit.json()

    # --- 9. Đẩy HỎNG THẬT: tắt backend, ghi cục bộ, rồi gửi lại ------------
    # Đây là cách duy nhất tạo ra một lần đẩy hỏng THẬT qua đúng đường thật.
    # Mock được điều này rồi (`test_crud_units.py`), nhưng mock không chứng minh
    # được rằng thay đổi cục bộ SỐNG SÓT qua một sự cố hạ tầng có thật.
    _compose("stop", "api")
    try:
        offline = _crm(
            "POST",
            "/units",
            json={
                "area_name": AREA_NAME,
                "unit_type": UNIT_TYPE,
                "unit_code": f"IT-{suffix}-offline",
                "unit_status": "available",
            },
        )
        assert offline.status_code == 201, offline.text
        state["offline_create"] = offline.json()
        state["offline_unit_id"] = state["offline_create"]["record"]["external_id"]
        state["offline_batch"] = state["offline_create"]["sync"]["external_batch_id"]
        state["offline_outbox"] = _crm("GET", f"/outbox/{state['offline_batch']}").json()
        # Chụp NGAY LÚC backend còn tắt. Đọc lại sau khi đã gửi lại thành công sẽ
        # thấy trạng thái ĐÃ PHỤC HỒI, và test "thay đổi sống sót qua lần đẩy
        # hỏng" sẽ khẳng định về một thời điểm khác với thời điểm nó nói tới.
        state["offline_crm_row"] = crm_rows(
            "SELECT source_revision, mirrored_revision FROM crm_units WHERE external_id = :e",
            e=state["offline_unit_id"],
        )
    finally:
        _compose("start", "api")
        _wait_for_backend()

    recovered = _crm("POST", f"/outbox/{state['offline_batch']}/resend")
    assert recovered.status_code == 200, recovered.text
    state["offline_resend"] = recovered.json()

    return state


# --- 1. Tạo căn ---------------------------------------------------------------


def test_creating_a_unit_reaches_the_backend_and_creates_exactly_one_row(flow):
    assert flow["unit_create"]["sync"]["status"] == "synced"
    assert flow["unit_create"]["sync"]["http_status"] == 202
    assert nonzero(flow["unit_create"]["sync"]["decisions"]) == {"insert": 1}
    assert flow["unit_create"]["sync"]["projections"]["inserted"] == 1
    assert flow["unit_create"]["sync"]["projections"].get("rejected", 0) == 0

    rows = backend_rows(
        "SELECT unit_code, status, source_revision, deleted_at FROM units "
        "WHERE source_instance_id = :i AND external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["unit_id"],
    )
    assert len(rows) == 1


def test_the_backend_sync_run_is_recorded_against_the_minicrm_batch_id(flow):
    """`upload_files` là nơi backend giữ trạng thái lô cho CẢ hai đường vào."""
    runs = backend_rows(
        "SELECT status, rows_received, rows_ok, rows_failed, transport_mode, source_system, source_entity "
        "FROM upload_files WHERE external_batch_id = :b",
        b=flow["unit_create_batch"],
    )
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert (runs[0]["rows_received"], runs[0]["rows_ok"], runs[0]["rows_failed"]) == (1, 1, 0)
    assert runs[0]["transport_mode"] == "api_push"
    assert runs[0]["source_system"] == "mini_crm"
    assert runs[0]["source_entity"] == "units"


def test_the_backend_records_the_source_identity(flow):
    rows = backend_rows(
        "SELECT source_revision, state, last_decision FROM crm_source_records "
        "WHERE source_instance_id = :i AND source_entity = 'units' AND source_record_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["unit_id"],
    )
    assert len(rows) == 1
    assert rows[0]["state"] in ("active", "tombstoned")


def test_the_outbox_stores_the_real_backend_response(flow):
    # Ảnh chụp ngay sau lần đẩy đầu. Đọc lại ở cuối vòng sẽ thấy phản hồi của lần
    # GỬI LẠI (HTTP 200) — đúng theo thiết kế "phản hồi mới nhất thắng", nhưng
    # không phải thứ test này đang nói tới.
    row = flow["unit_create_outbox"]
    assert row["http_status"] == 202
    assert row["response"]["sync_run_id"] == flow["unit_create"]["sync"]["sync_run_id"]
    assert row["payload"]["records"][0]["external_id"] == flow["unit_id"]
    assert row["payload"]["project_ref"] == {"project_id": PROJECT_ID}


def test_the_pushed_envelope_carries_the_synthetic_label(flow):
    assert "SYNTHETIC" in flow["unit_create_outbox"]["payload"]["_comment"]


# --- 2. Sửa căn ---------------------------------------------------------------


def test_updating_a_unit_updates_the_same_backend_row(flow):
    assert flow["unit_update"]["record"]["source_revision"] == 2
    assert nonzero(flow["unit_update"]["sync"]["decisions"]) == {"update": 1}
    assert flow["unit_update"]["sync"]["projections"]["updated"] == 1

    rows = backend_rows(
        "SELECT status, source_revision FROM units WHERE source_instance_id = :i AND external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["unit_id"],
    )
    assert len(rows) == 1, "sửa KHÔNG được tạo ra căn thứ hai"
    assert rows[0]["source_revision"] == 2


def test_the_two_sides_agree_on_id_revision_and_status_after_the_update(flow):
    """Đối chiếu TỪNG TRƯỜNG giữa hai database — đây là phép so tương thích thật."""
    crm = flow["crm_unit_after_update"]
    backend = flow["backend_unit_after_update"]

    assert crm["external_id"] == backend["external_unit_id"]
    assert crm["unit_code"] == backend["unit_code"]
    assert crm["unit_status"] == backend["status"] == "reserved"
    assert crm["source_revision"] == backend["source_revision"] == 2
    assert crm["mirrored_revision"] == 2


def test_the_backend_resolved_the_area_by_name_into_the_right_project(flow):
    """`area_ref` dạng `{area_name, unit_type}` phải tra ra phân khu ĐÚNG DỰ ÁN.

    Backend giới hạn tra cứu theo `project_id`; thiếu điều kiện đó thì một hệ
    nguồn gắn được căn của mình vào phân khu của dự án khác.
    """
    rows = backend_rows(
        "SELECT a.area_name, a.unit_type, a.project_id, u.unit_type AS unit_unit_type "
        "FROM units u JOIN areas a ON u.area_id = a.id "
        "WHERE u.source_instance_id = :i AND u.external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["unit_id"],
    )
    assert len(rows) == 1
    assert rows[0]["area_name"] == AREA_NAME
    assert rows[0]["unit_type"] == UNIT_TYPE
    assert str(rows[0]["project_id"]) == PROJECT_ID
    # `units.unit_type` lấy từ DÒNG PHÂN KHU, không từ payload (sửa ở Phase 1).
    assert rows[0]["unit_unit_type"] == UNIT_TYPE


# --- 3. Giao dịch -------------------------------------------------------------


def test_a_deal_created_after_its_unit_resolves_to_that_unit(flow):
    assert flow["deal_create"]["sync"]["status"] == "synced"
    assert nonzero(flow["deal_create"]["sync"]["decisions"]) == {"insert": 1}

    rows = backend_rows(
        "SELECT d.status, d.source_revision, u.external_unit_id FROM deals d JOIN units u ON d.unit_id = u.id "
        "WHERE d.source_instance_id = :i AND d.external_deal_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["deal_id"],
    )
    assert len(rows) == 1
    assert rows[0]["external_unit_id"] == flow["unit_id"]


def test_a_deal_pointing_at_an_unknown_unit_is_refused_locally_and_never_sent(flow):
    """422 CỤC BỘ, và KHÔNG có dòng outbox nào mới — không một byte nào rời máy."""
    assert flow["orphan_deal"].status_code == 422
    body = flow["orphan_deal"].json()
    assert body["error_code"] == "UNIT_NOT_FOUND"
    assert body["sent"] is False
    assert flow["outbox_total_after_orphan"] == flow["outbox_total_before_orphan"]


def test_reserved_then_sold_keeps_reserved_at_on_both_sides(flow):
    """CHỐT A4, kiểm ở đầu kia của một vòng HTTP thật.

    Payload `sold` được dựng lại từ dòng `crm_deals` nên nó mang cả `reserved_at`
    cũ. Nếu không, backend đã từ chối bằng `HISTORY_TIMESTAMP_DROPPED` và test này
    sẽ thấy `decisions` khác `update`.
    """
    assert nonzero(flow["deal_reserved"]["sync"]["decisions"]) == {"update": 1}
    assert nonzero(flow["deal_sold"]["sync"]["decisions"]) == {"update": 1}
    assert flow["deal_sold"]["sync"]["projections"]["updated"] == 1

    row = flow["backend_deal_after_sold"]
    assert row["status"] == "sold"
    assert row["source_revision"] == 3
    assert row["reserved_at"] == RESERVED_AT, "ngày đặt cọc KHÔNG được biến mất khi chuyển sang sold"
    assert row["sold_at"] == SOLD_AT
    assert row["lost_at"] is None


def test_both_sides_agree_on_the_deal_history(flow):
    crm = flow["crm_deal_after_sold"]
    backend = flow["backend_deal_after_sold"]
    assert crm["deal_status"] == backend["status"]
    assert crm["reserved_at"] == backend["reserved_at"]
    assert crm["sold_at"] == backend["sold_at"]
    assert crm["lost_at"] == backend["lost_at"]
    assert crm["source_revision"] == backend["source_revision"]


def test_the_backend_stored_no_field_the_contract_did_not_carry(flow):
    """Không có ánh xạ bất ngờ nào: mọi trường CRM sở hữu đúng bằng những trường
    hợp đồng chở, và không trường nào khác bị đụng tới."""
    rows = backend_rows(
        "SELECT * FROM deals WHERE source_instance_id = :i AND external_deal_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["deal_id"],
    )
    columns = set(rows[0])
    for forbidden in ("price", "customer_name", "phone", "email", "commission", "salesperson"):
        assert not any(forbidden in c for c in columns), f"bảng deals có cột {forbidden} — ngoài phạm vi hợp đồng"


# --- 4. Gửi lại đúng lô cũ ----------------------------------------------------


def test_resending_the_same_batch_is_replayed_and_creates_no_duplicate(flow):
    """Backend nhận lại `external_batch_id` đã xử lý ⇒ trả kết quả ĐÃ LƯU."""
    assert flow["resend"]["sent_batch_id"] == flow["unit_create_batch"]
    assert flow["resend"]["sync"]["status"] == "replayed"
    assert flow["resend"]["sync"]["http_status"] == 200
    assert flow["backend_units_after_resend"] == flow["backend_units_before_resend"]


def test_the_replayed_run_is_the_original_run_not_a_new_one(flow):
    """Cùng `sync_run_id` = backend KHÔNG chạy lại gì cả, nên cũng không có tác
    nhân nào phía sau bị kích hoạt lần thứ hai."""
    assert flow["resend"]["sync"]["sync_run_id"] == flow["unit_create"]["sync"]["sync_run_id"]
    runs = backend_rows(
        "SELECT count(*) AS n FROM upload_files WHERE external_batch_id = :b", b=flow["unit_create_batch"]
    )
    assert runs[0]["n"] == 1, "gửi lại KHÔNG được tạo lô thứ hai ở backend"


def test_the_outbox_row_records_the_second_attempt(flow):
    row = _crm("GET", f"/outbox/{flow['unit_create_batch']}").json()
    assert row["attempts"] >= 2
    assert row["http_status"] == 200, "phản hồi MỚI NHẤT thắng"


# --- 5. Phát lại bản CŨ -------------------------------------------------------


def test_replaying_a_stale_payload_is_skipped_by_the_backend(flow):
    """Phiên bản 1 gửi lại trong khi backend đang giữ phiên bản 2 ⇒ `skip_stale`.

    Đây là lô MỚI (batch id mới) nên backend thật sự chạy tầng so phiên bản —
    khác hẳn với `resend`, vốn dừng ở tầng nhận diện lô.
    """
    assert flow["replay_stale"]["sent_batch_id"] != flow["unit_create_batch"]
    sync = flow["replay_stale"]["sync"]
    assert sync["http_status"] == 202
    assert nonzero(sync["decisions"]) == {"skip_stale": 1}
    assert sync["projections"].get("untouched") == 1
    assert sync["sync_run_id"] != flow["unit_create"]["sync"]["sync_run_id"]


def test_the_stale_replay_did_not_overwrite_the_current_backend_state(flow):
    assert flow["unit_before_stale"] == flow["unit_after_stale"]
    assert flow["unit_after_stale"]["status"] == "reserved"
    assert flow["unit_after_stale"]["source_revision"] == 2


# --- 6. Tombstone -------------------------------------------------------------


def test_deleting_a_deal_soft_deletes_it_at_the_backend(flow):
    assert nonzero(flow["deal_delete"]["sync"]["decisions"]) == {"tombstone": 1}
    rows = backend_rows(
        "SELECT deleted_at, status FROM deals WHERE source_instance_id = :i AND external_deal_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["deal_id"],
    )
    assert len(rows) == 1, "xoá mềm, KHÔNG xoá vật lý — dòng phải còn đó"
    assert rows[0]["deleted_at"] is not None


def test_deleting_a_unit_soft_deletes_it_and_removes_it_from_active_reads(flow):
    assert nonzero(flow["unit_delete"]["sync"]["decisions"]) == {"tombstone": 1}
    assert flow["unit_delete"]["sync"]["projections"]["tombstoned"] == 1

    rows = backend_rows(
        "SELECT deleted_at FROM units WHERE source_instance_id = :i AND external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["unit_id"],
    )
    assert len(rows) == 1 and rows[0]["deleted_at"] is not None

    active = backend_rows(
        "SELECT external_unit_id FROM units "
        "WHERE source_instance_id = :i AND external_unit_id = :e AND deleted_at IS NULL",
        i=SOURCE_INSTANCE,
        e=flow["unit_id"],
    )
    assert active == []


def test_the_source_identity_row_is_marked_tombstoned(flow):
    rows = backend_rows(
        "SELECT state, last_decision FROM crm_source_records "
        "WHERE source_instance_id = :i AND source_entity = 'units' AND source_record_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["unit_id"],
    )
    assert rows[0]["state"] == "tombstoned"
    assert rows[0]["last_decision"] == "tombstone"


# --- 7. Đẩy hỏng THẬT, và phục hồi bằng gửi lại -------------------------------


def test_a_push_that_cannot_reach_the_backend_keeps_the_local_change(flow):
    """Backend bị TẮT THẬT trong lúc ghi. Thay đổi cục bộ phải sống sót."""
    assert flow["offline_create"]["record"]["source_revision"] == 1
    assert flow["offline_create"]["sync"]["status"] == "sync_failed"
    assert flow["offline_create"]["sync"]["http_status"] is None

    rows = flow["offline_crm_row"]
    assert len(rows) == 1, "thay đổi đã commit KHÔNG được biến mất vì một lần đẩy hỏng"
    assert rows[0]["source_revision"] == 1
    assert rows[0]["mirrored_revision"] is None, "và nó tự khai là chưa lên tới backend"


def test_the_failed_push_is_visible_in_the_outbox(flow):
    row = flow["offline_outbox"]
    assert row["http_status"] is None
    assert row["sent_at"] is None
    assert row["attempts"] == 1
    assert row["last_error"], "lỗi truyền tải không có mã HTTP nào — phải đọc được ở đây"
    assert row["payload"]["records"][0]["external_id"] == flow["offline_unit_id"]


def test_an_explicit_resend_recovers_the_failed_batch(flow):
    """Phase C.5 thêm vòng relay TỰ ĐỘNG (`app/relay.py`), chạy trong CHÍNH
    container Mini CRM này — nó đua với bước fixture tự tay gọi `/resend`, KHÔNG
    tất định ai gửi trước. Ba kết cục cùng hợp lệ ở phản hồi resend:

        "synced"       — resend là lần gửi thành công ĐẦU TIÊN
        "replayed"     — relay đã gửi xong trước, resend chỉ thấy batch ĐÃ xử lý
        "sync_failed"  — resend và relay THẬT SỰ đụng nhau ngay đúng khoảnh khắc
                          `api` vừa sống lại (một race hạ tầng hiếm, không phải
                          mất dữ liệu) — dòng vẫn còn `http_status=NULL`
                          (retryable), nên relay sẽ tự thử lại ở lượt kế

    Bất biến THẬT không phải "resend luôn thắng", mà là "batch RỐT CUỘC tới nơi
    ĐÚNG MỘT LẦN" — nên khi nhánh thứ ba xảy ra, test tự chờ vòng relay dọn nốt
    thay vì coi đó là hỏng."""
    sync = flow["offline_resend"]["sync"]
    assert sync["status"] in ("synced", "replayed", "sync_failed")

    if sync["status"] == "sync_failed":
        deadline = time.time() + 30
        row = _crm("GET", f"/outbox/{flow['offline_batch']}").json()
        while time.time() < deadline and row["http_status"] is None:
            time.sleep(1.5)
            row = _crm("GET", f"/outbox/{flow['offline_batch']}").json()
        assert row["http_status"] == 202, "relay phải tự dọn nốt trong vài lượt kế — nếu không, đây mới là hỏng thật"
    else:
        # 202 = resend là lần gửi ĐẦU; 200 = relay đã gửi trước, đây là bản REPLAY
        # của kết quả đã lưu — cả hai đều mang đúng quyết định `insert: 1`.
        assert sync["http_status"] in (200, 202)
        assert nonzero(sync["decisions"]) == {"insert": 1}

    rows = backend_rows(
        "SELECT source_revision FROM units WHERE source_instance_id = :i AND external_unit_id = :e",
        i=SOURCE_INSTANCE,
        e=flow["offline_unit_id"],
    )
    assert len(rows) == 1 and rows[0]["source_revision"] == 1


def test_the_recovered_unit_is_now_marked_mirrored(flow):
    rows = crm_rows(
        "SELECT source_revision, mirrored_revision FROM crm_units WHERE external_id = :e",
        e=flow["offline_unit_id"],
    )
    assert rows[0]["mirrored_revision"] == rows[0]["source_revision"] == 1


# --- 8. Cô lập hai hệ thống, kiểm lại trên hệ đang chạy -----------------------


def test_the_two_databases_never_share_a_table(flow):
    """Kiểm trên DATABASE ĐANG CHẠY, không phải trên một bản dựng lại trong test."""
    leaked_into_crm = crm_rows(
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)",
        t=["units", "deals", "areas", "projects", "upload_files", "crm_source_records", "ranking_runs"],
    )
    assert leaked_into_crm == []

    leaked_into_backend = backend_rows(
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)",
        t=["crm_units", "crm_deals", "crm_outbox"],
    )
    assert leaked_into_backend == []


def test_the_two_alembic_histories_stay_separate(flow):
    # Phase 5.5 P0 (5A): backend head tiến lên 0016. Phase B: Mini CRM head tiến
    # lên 0003 (crm_projects, crm_areas, crm_units.area_id). Phase C: Mini CRM
    # head tiến tiếp lên 0004 (nới ck_crm_outbox_entity cho ý định v2), BACKEND
    # KHÔNG đổi. Phase D: backend head tiến lên 0017 (danh tính nguồn cho
    # projects/areas — xem alembic/versions/0017_hierarchy_projection.py),
    # MINI CRM KHÔNG đổi — Phase D bị cấm đụng runtime Mini CRM.
    #
    # Hai con số CHỦ Ý cứng, giống các baseline khác của bộ test — đổi một trong
    # hai là BẰNG CHỨNG đã có migration mới ở ĐÚNG MỘT cây, không phải một cờ cần
    # nới lỏng. Nếu một ngày CẢ HAI cùng đổi trong cùng một đợt, đó là dấu hiệu
    # hai cây đã trộn vào nhau — dừng lại và điều tra, đừng cập nhật cho qua.
    assert crm_rows("SELECT version_num FROM alembic_version")[0]["version_num"] == "0004_outbox_hierarchy_entities"
    assert backend_rows("SELECT version_num FROM alembic_version")[0]["version_num"] == "0017_hierarchy_projection"


def test_no_ranking_run_was_triggered_by_any_of_this(flow):
    """Phase 4 KHÔNG thêm động cơ xếp hạng và KHÔNG kích hoạt nó.

    Cò kích hoạt xếp hạng thuộc phase sau; nếu con số này khác 0 thì có ai đó đã
    nối dây sớm, và bảng `ranking_runs` sẽ đầy dữ liệu chưa có nghĩa.
    """
    assert backend_rows("SELECT count(*) AS n FROM ranking_runs")[0]["n"] == 0
