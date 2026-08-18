"""Vòng relay tự động (Phase C.5), container THẬT — HTTP thật, database thật, KHÔNG
ai gọi `relay_tick()` hay `/resend` bằng tay. Đây là chỗ DUY NHẤT chứng minh vòng
NỀN đang chạy TRONG container `minicrm` thật sự tồn tại và tự phục hồi — mọi test
khác (`tests/test_relay.py`) gọi trực tiếp `relay_tick()`, nhanh nhưng không chứng
minh được vòng lặp NỀN THẬT tồn tại.

Chạy:

    docker compose up -d --build minicrm_db minicrm api db
    pytest minicrm/tests/test_real_relay.py -v

Test outage/recovery DỪNG và KHỞI ĐỘNG LẠI container `api` thật — có chủ đích, vì
đó chính xác là điều cần chứng minh ("Backend outage → local commit survives" /
"outage recovery → automatic retry"). Luôn khởi động lại `api` ở `finally`, kể cả
khi assertion phía trên thất bại — không để môi trường dev chung ở trạng thái hỏng.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
import real_env as env

REPO_ROOT = Path(__file__).resolve().parents[2]

MINICRM_URL = "http://localhost:8100"
BACKEND_URL = "http://localhost:8000"
ADMIN_AUTH_HEADER = env.ADMIN_AUTH_HEADER
BACKEND_AUTH_HEADER = env.BACKEND_AUTH_HEADER


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

pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP)


def _compose(*args: str) -> None:
    result = subprocess.run(["docker", "compose", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"docker compose {' '.join(args)} thất bại:\n{result.stderr}"


def _wait_reachable(url: str, *, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _reachable(url):
            return
        time.sleep(1.5)
    raise AssertionError(f"{url} không sống lại sau {timeout}s")


def _outbox_row(batch_id: str) -> dict:
    response = httpx.get(f"{MINICRM_URL}/outbox/{batch_id}", timeout=10.0)
    response.raise_for_status()
    return response.json()


def _wait_delivered(batch_id: str, *, timeout: float = 60.0) -> dict:
    """Chờ vòng relay THẬT (đang chạy trong container, tick mỗi
    `MINICRM_RELAY_INTERVAL_SECONDS`) tự gửi — KHÔNG gọi `/resend` hay
    `relay_tick()` ở đây, đó chính là điều cần chứng minh."""
    deadline = time.time() + timeout
    row = _outbox_row(batch_id)
    while time.time() < deadline and row["http_status"] is None:
        time.sleep(1.0)
        row = _outbox_row(batch_id)
    return row


def _new_project() -> dict:
    name = f"Relay E2E {uuid.uuid4().hex[:8]}"
    response = httpx.post(
        f"{MINICRM_URL}/projects",
        json={"name": name, "launch_date": "2026-06-01"},
        headers=ADMIN_AUTH_HEADER,
        timeout=10.0,
    )
    assert response.status_code == 201, response.text
    return response.json()["record"]


def test_a_project_write_is_delivered_automatically_with_no_manual_trigger():
    """Không gọi `/resend`, không gọi `relay_tick()` bằng tay — chỉ ghi rồi CHỜ.
    Nếu vòng nền thật không tồn tại, dòng này sẽ giữ `http_status=NULL` mãi và
    test time out."""
    record = _new_project()
    batch_id_resp = httpx.get(f"{MINICRM_URL}/outbox", params={"entity": "projects", "limit": 1}, timeout=10.0)
    batch_id = batch_id_resp.json()["items"][0]["external_batch_id"]
    assert _outbox_row(batch_id)["http_status"] is None, "Phase C: chưa gửi ngay lúc ghi"

    row = _wait_delivered(batch_id)
    assert row["http_status"] in (200, 202), row

    backend_response = httpx.get(
        f"{BACKEND_URL}/api/v1/projects/{record['external_id']}", headers=BACKEND_AUTH_HEADER, timeout=10.0
    )
    assert backend_response.status_code == 200, "phải THẤY ĐƯỢC ở backend, không chỉ Mini CRM tự báo thành công"
    assert backend_response.json()["name"] == record["name"]


def test_backend_outage_survives_locally_and_recovers_automatically():
    """Dừng `api`, ghi một phân khu — commit cục bộ phải thành công dù backend
    chết. Khởi động lại `api` — dòng outbox phải TỰ được gửi ở lượt relay kế
    tiếp, không cần thao tác gì thêm."""
    project = _new_project()

    _compose("stop", "api")
    try:
        assert not _reachable(BACKEND_URL), "tiền đề: backend phải THẬT SỰ chết trước khi ghi"

        area_response = httpx.post(
            f"{MINICRM_URL}/areas",
            json={
                "external_project_id": project["external_id"],
                "area_name": "Relay Outage A1",
                "unit_type": "2PN",
                "bedrooms": 2,
                "area_sqm": 60.0,
                "total_units": 10,
            },
            headers=ADMIN_AUTH_HEADER,
            timeout=10.0,
        )
        assert area_response.status_code == 201, "ghi cục bộ phải THÀNH CÔNG dù backend chết — không phụ thuộc backend"
        area = area_response.json()["record"]

        batch_id = httpx.get(
            f"{MINICRM_URL}/outbox", params={"entity": "areas", "limit": 1}, timeout=10.0
        ).json()["items"][0]["external_batch_id"]

        # Cho relay vài lượt thử trong lúc backend còn chết — phải KHÔNG BAO GIỜ
        # "thành công giả": http_status vẫn NULL (chưa từng có phản hồi) hoặc lỗi
        # truyền tải — không phải một mã 2xx bịa ra.
        time.sleep(7.0)
        row = _outbox_row(batch_id)
        assert row["http_status"] is None, "không có phản hồi thật nào có thể tồn tại khi backend đang chết"
    finally:
        _compose("start", "api")
        _wait_reachable(BACKEND_URL)

    row = _wait_delivered(batch_id)
    assert row["http_status"] in (200, 202), "relay phải TỰ gửi lại sau khi backend sống lại, không cần /resend"

    backend_response = httpx.get(
        f"{BACKEND_URL}/api/v1/areas/{area['external_id']}", headers=BACKEND_AUTH_HEADER, timeout=10.0
    )
    assert backend_response.status_code == 200
    assert backend_response.json()["area_name"] == "Relay Outage A1"


def test_a_pending_row_captured_before_a_minicrm_restart_still_gets_relayed():
    """Ghi khi backend đang chết (outbox chỉ LƯU, chưa gửi được) → khởi động lại
    CHÍNH container `minicrm` (không phải `api`) → dòng outbox phải còn nguyên
    (bền trong database, không phải trạng thái trong bộ nhớ) và vẫn được relay
    tự động sau khi cả hai container đều sống."""
    project = _new_project()

    _compose("stop", "api")
    try:
        area_response = httpx.post(
            f"{MINICRM_URL}/areas",
            json={
                "external_project_id": project["external_id"],
                "area_name": "Relay Restart A1",
                "unit_type": "3PN",
                "bedrooms": 3,
                "area_sqm": 75.0,
                "total_units": 8,
            },
            headers=ADMIN_AUTH_HEADER,
            timeout=10.0,
        )
        assert area_response.status_code == 201
        area = area_response.json()["record"]
        batch_id = httpx.get(
            f"{MINICRM_URL}/outbox", params={"entity": "areas", "limit": 1}, timeout=10.0
        ).json()["items"][0]["external_batch_id"]
        assert _outbox_row(batch_id)["http_status"] is None

        _compose("restart", "minicrm")
        _wait_reachable(MINICRM_URL)

        # Dòng vẫn còn — bền trong database, không phải một biến trong tiến
        # trình cũ đã bị giết cùng container.
        assert _outbox_row(batch_id)["http_status"] is None
    finally:
        _compose("start", "api")
        _wait_reachable(BACKEND_URL)

    row = _wait_delivered(batch_id)
    assert row["http_status"] in (200, 202)

    backend_response = httpx.get(
        f"{BACKEND_URL}/api/v1/areas/{area['external_id']}", headers=BACKEND_AUTH_HEADER, timeout=10.0
    )
    assert backend_response.status_code == 200
    assert backend_response.json()["area_name"] == "Relay Restart A1"
