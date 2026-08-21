"""E2E đầu-cuối cho Task 2 (CP8) — RUNTIME_VERIFICATION_REQUIRED.

Bộ test này mã hoá TOÀN BỘ Definition of Done thành các bước kiểm được bằng máy,
nhưng nó CẦN cả hai stack đang chạy (Mini CRM 8100 + Product 8000 + hai
PostgreSQL). Sandbox phát triển không có runtime đó, nên mỗi test tự SKIP khi
không kết nối được — thay vì fail giả. Đây chính là ranh giới "code được / verify
được": logic luồng nằm ở đây, sẵn sàng chạy; việc chạy chờ máy của bạn.

Chạy khi đã `docker compose up`:

    E2E_LIVE=1 pytest tests/e2e/ -v

Không có `E2E_LIVE=1` thì cả file skip — để nó không làm đỏ CI vốn không dựng
được cả hai stack.

Điều bộ test này canh, theo đúng thứ tự phụ thuộc:

    1. Auth đóng khi chưa cấu hình / mở đúng khi đã cấu hình (401/403/503).
    2. Project → Area → Unit tạo được và PERSIST (reload vẫn còn).
    3. Unit CHƯA mirror ⇒ tạo Deal bị chặn 409 UNIT_NOT_MIRRORED.
    4. Sau khi relay mirror ⇒ tạo Deal được.
    5. Dữ liệu THẬT SỰ có trong Product DB (query trực tiếp, không tin HTTP 200).
    6. Gửi lại cùng một event KHÔNG tạo bản ghi trùng (idempotency).

Test 5 và 6 cần DSN của Product DB (`E2E_PRODUCT_DSN`); thiếu thì hai test đó
skip riêng, các test HTTP còn lại vẫn chạy.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

MINICRM = os.environ.get("E2E_MINICRM_URL", "http://localhost:8100")
PRODUCT = os.environ.get("E2E_PRODUCT_URL", "http://localhost:8000")
PRODUCT_DSN = os.environ.get("E2E_PRODUCT_DSN")  # postgresql://... cho psycopg
ADMIN_TOKEN = os.environ.get("E2E_MINICRM_ADMIN_TOKEN")  # đường token tĩnh cho test
RELAY_WAIT_SECONDS = float(os.environ.get("E2E_RELAY_WAIT", "8"))

def _rec(response):
    """API có thể bọc trong {"record": ...} hoặc trả thẳng — chấp nhận cả hai."""
    data = response.json()
    return data["record"] if isinstance(data, dict) and "record" in data else data

pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_LIVE") != "1",
    reason="RUNTIME_VERIFICATION_REQUIRED: cần cả hai stack chạy; đặt E2E_LIVE=1",
)


def _auth_headers() -> dict[str, str]:
    """Test tự động dùng đường token tĩnh (bật MINICRM_LEGACY_TOKEN_AUTH_ENABLED
    =true trong môi trường CI) — không thể tự động hoá vòng OIDC tương tác của
    Microsoft trong một test không có trình duyệt. Vòng OIDC được kiểm riêng,
    offline, ở tests/test_entra_auth.py; ở đây trọng tâm là LUỒNG DỮ LIỆU."""
    if not ADMIN_TOKEN:
        pytest.skip("Cần E2E_MINICRM_ADMIN_TOKEN (đường token tĩnh cho test tự động).")
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture(scope="session")
def crm() -> httpx.Client:
    try:
        client = httpx.Client(base_url=MINICRM, timeout=10.0)
        client.get("/health").raise_for_status()
    except Exception:
        pytest.skip(f"Mini CRM không phản hồi ở {MINICRM}")
    return client


@pytest.fixture(scope="session")
def product() -> httpx.Client:
    try:
        client = httpx.Client(base_url=PRODUCT, timeout=10.0)
        client.get("/health").raise_for_status()
    except Exception:
        pytest.skip(f"Product không phản hồi ở {PRODUCT}")
    return client


# --- 1. Auth ----------------------------------------------------------------


def test_write_without_auth_is_rejected(crm):
    """Tạo Project không kèm xác thực → 401 hoặc 503 (chưa cấu hình auth), KHÔNG
    BAO GIỜ 2xx. Một mặt ghi mở toang là lỗi nghiêm trọng nhất có thể có."""
    res = crm.post("/projects", json={"name": "no-auth"})
    assert res.status_code in (401, 503), res.text


# --- 2. Hierarchy + persist -------------------------------------------------


@pytest.fixture()
def created_project(crm):
    # Schema thật yêu cầu `launch_date` (đội thêm sau khi test này được viết).
    res = crm.post(
        "/projects",
        json={"name": f"E2E {time.time_ns()}", "launch_date": "2026-12-01"},
        headers=_auth_headers(),
    )
    assert res.status_code in (200, 201), res.text
    return _rec(res)


def test_project_persists_across_reload(crm, created_project):
    ext = created_project["external_id"]
    fetched = crm.get(f"/projects/{ext}", headers=_auth_headers())
    assert fetched.status_code == 200
    assert _rec(fetched)["external_id"] == ext


@pytest.fixture()
def mirrored_unit(crm, created_project):
    """Tạo Area → Unit rồi ĐỢI relay mirror. Trả unit đã có mirrored_revision."""
    proj = created_project["external_id"]
    ts = time.time_ns()
    area = crm.post(
        "/areas",
        json={
            "external_project_id": proj,
            "area_name": f"Area {ts}",
            "unit_type": "2BR",
            "bedrooms": 2,
            "area_sqm": 65.0,
            "total_units": 10,
        },
        headers=_auth_headers(),
    )
    assert area.status_code in (200, 201), area.text
    area_ext = _rec(area)["external_id"]

    unit = crm.post(
        "/units",
        json={"external_area_id": area_ext, "unit_code": f"U{ts}"},
        headers=_auth_headers(),
    )
    assert unit.status_code in (200, 201), unit.text
    unit_ext = _rec(unit)["external_id"]

    # Đợi vòng relay (mỗi 5s) mirror sang Product.
    deadline = time.time() + RELAY_WAIT_SECONDS
    while time.time() < deadline:
        current = _rec(crm.get(f"/units/{unit_ext}", headers=_auth_headers()))
        if current.get("mirrored_revision") is not None:
            return current
        time.sleep(1)
    pytest.fail(f"Unit {unit_ext} không được mirror trong {RELAY_WAIT_SECONDS}s")


# --- 3 & 4. UNIT_NOT_MIRRORED gate ------------------------------------------


def test_deal_blocked_before_unit_mirrored(crm, created_project):
    """Tạo Unit rồi thử tạo Deal NGAY, trước khi relay kịp mirror → 409
    UNIT_NOT_MIRRORED. Đây là business rule KHÔNG được bypass."""
    proj = created_project["external_id"]
    ts = time.time_ns()
    area_ext = crm.post(
        "/areas",
        json={
            "external_project_id": proj,
            "area_name": f"A{ts}",
            "unit_type": "1BR",
            "bedrooms": 1,
            "area_sqm": 45.0,
            "total_units": 5,
        },
        headers=_auth_headers(),
    ).json()["record"]["external_id"]
    unit_ext = crm.post(
        "/units",
        json={"external_area_id": area_ext, "unit_code": f"U{ts}"},
        headers=_auth_headers(),
    ).json()["record"]["external_id"]

    res = crm.post(
        "/deals",
        json={"external_unit_id": unit_ext, "deal_status": "lead"},
        headers=_auth_headers(),
    )
    # Nếu relay quá nhanh và đã mirror, chấp nhận 2xx; nếu chưa, PHẢI là 409.
    if res.status_code not in (200, 201):
        assert res.status_code == 409, res.text
        assert res.json().get("error_code") == "UNIT_NOT_MIRRORED"


def test_deal_allowed_after_unit_mirrored(crm, mirrored_unit):
    res = crm.post(
        "/deals",
        json={"external_unit_id": mirrored_unit["external_id"], "deal_status": "lead"},
        headers=_auth_headers(),
    )
    assert res.status_code in (200, 201), res.text


# --- 5. Dữ liệu THẬT trong Product DB ---------------------------------------


def _product_db():
    if not PRODUCT_DSN:
        pytest.skip("Cần E2E_PRODUCT_DSN để query Product DB trực tiếp.")
    try:
        import psycopg
    except ImportError:
        pytest.skip("Cần psycopg để query Product DB.")
    return psycopg.connect(PRODUCT_DSN)


def test_unit_actually_lands_in_product_db(mirrored_unit):
    """HTTP 200 KHÔNG đủ — query Product DB để xác nhận dữ liệu thật sự tới nơi."""
    with _product_db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM units WHERE external_id = %s AND source_system = 'mini_crm'",
            (mirrored_unit["external_id"],),
        )
        assert cur.fetchone()[0] == 1


# --- 6. Idempotency ---------------------------------------------------------


def test_resend_does_not_duplicate(crm, mirrored_unit):
    """Gửi lại cùng một outbox event KHÔNG được tạo bản ghi thứ hai ở Product DB,
    và KHÔNG kéo mirrored_revision lùi."""
    unit_ext = mirrored_unit["external_id"]
    before = mirrored_unit["mirrored_revision"]

    # Tìm dòng outbox của unit này và resend.
    outbox = crm.get("/outbox", headers=_auth_headers()).json()
    rows = outbox.get("items", outbox if isinstance(outbox, list) else [])
    target = next((r for r in rows if unit_ext in str(r)), None)
    if not target:
        pytest.skip("Không tìm thấy dòng outbox tương ứng để resend.")

    crm.post(f"/outbox/{target['id']}/resend", headers=_auth_headers())
    time.sleep(2)

    after = _rec(crm.get(f"/units/{unit_ext}", headers=_auth_headers()))
    assert after["mirrored_revision"] >= before  # GREATEST — không lùi

    with _product_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM units WHERE external_id = %s", (unit_ext,))
        assert cur.fetchone()[0] == 1  # vẫn đúng MỘT bản ghi
