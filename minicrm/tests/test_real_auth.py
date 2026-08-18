"""Xác thực GHI Mini CRM (D-14), container THẬT — HTTP thật, không mock.

`test_auth.py` đã kiểm cơ chế đầy đủ trên database throwaway; file này chứng
minh CHÍNH XÁC cấu hình đang chạy thật trong container `minicrm` (đọc từ `.env`,
nạp bởi `docker-compose.yml`) hoạt động đúng — ba token thật, phạm vi thật, và
"P-0001" (dự án BOOTSTRAP có thật trong database dev) làm ranh giới phạm vi của
`OPERATOR_TOKEN`.

Chạy:

    docker compose up -d --build minicrm_db minicrm
    pytest minicrm/tests/test_real_auth.py -v
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import real_env as env

_SKIP = ""
if not env.reachable(env.MINICRM_URL):
    _SKIP = f"Container Mini CRM không phản hồi ở {env.MINICRM_URL} — chạy: docker compose up -d --build minicrm_db minicrm"
elif not env.ADMIN_TOKEN or not env.OPERATOR_TOKEN or not env.VIEWER_TOKEN:
    _SKIP = "`.env` thiếu MINICRM_AUTH_*_TOKEN — xem .env.example"

pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP)


def _area_payload(project_external_id: str) -> dict:
    return {
        "external_project_id": project_external_id,
        "area_name": f"AuthE2E-{uuid.uuid4().hex[:6]}",
        "unit_type": "2PN",
        "bedrooms": 2,
        "area_sqm": 55.0,
        "total_units": 6,
    }


def test_no_token_is_401():
    response = httpx.post(f"{env.MINICRM_URL}/areas", json=_area_payload("P-0001"), timeout=10.0)
    assert response.status_code == 401


def test_invalid_token_is_401():
    response = httpx.post(
        f"{env.MINICRM_URL}/areas",
        json=_area_payload("P-0001"),
        headers={"Authorization": "Bearer definitely-not-a-real-token"},
        timeout=10.0,
    )
    assert response.status_code == 401


def test_business_viewer_write_is_403():
    response = httpx.post(
        f"{env.MINICRM_URL}/areas", json=_area_payload("P-0001"), headers=env.VIEWER_AUTH_HEADER, timeout=10.0
    )
    assert response.status_code == 403


def test_operator_can_write_within_its_configured_scope():
    """`MINICRM_AUTH_PROJECT_SCOPE` (`.env`) cấp OPERATOR_TOKEN đúng `["P-0001"]`
    — dự án BOOTSTRAP có thật, đã tồn tại từ trước trong database dev."""
    response = httpx.post(
        f"{env.MINICRM_URL}/areas", json=_area_payload("P-0001"), headers=env.OPERATOR_AUTH_HEADER, timeout=10.0
    )
    assert response.status_code == 201, response.text


def test_operator_is_rejected_outside_its_configured_scope():
    """Admin (phạm vi ALL) tạo một dự án MỚI — operator (phạm vi cố định
    `["P-0001"]`) không được cấp dự án đó."""
    created = httpx.post(
        f"{env.MINICRM_URL}/projects",
        json={"name": f"Auth E2E {uuid.uuid4().hex[:8]}", "launch_date": "2026-06-01"},
        headers=env.ADMIN_AUTH_HEADER,
        timeout=10.0,
    )
    assert created.status_code == 201, created.text
    other_project_id = created.json()["record"]["external_id"]
    assert other_project_id != "P-0001"

    response = httpx.post(
        f"{env.MINICRM_URL}/areas",
        json=_area_payload(other_project_id),
        headers=env.OPERATOR_AUTH_HEADER,
        timeout=10.0,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


def test_operator_cannot_create_a_new_project():
    response = httpx.post(
        f"{env.MINICRM_URL}/projects",
        json={"name": f"Auth E2E {uuid.uuid4().hex[:8]}", "launch_date": "2026-06-01"},
        headers=env.OPERATOR_AUTH_HEADER,
        timeout=10.0,
    )
    assert response.status_code == 403


def test_admin_with_all_scope_can_create_a_project():
    response = httpx.post(
        f"{env.MINICRM_URL}/projects",
        json={"name": f"Auth E2E {uuid.uuid4().hex[:8]}", "launch_date": "2026-06-01"},
        headers=env.ADMIN_AUTH_HEADER,
        timeout=10.0,
    )
    assert response.status_code == 201


def test_read_routes_remain_open_without_a_token():
    assert httpx.get(f"{env.MINICRM_URL}/projects", timeout=10.0).status_code == 200
    assert httpx.get(f"{env.MINICRM_URL}/areas", timeout=10.0).status_code == 200


def test_no_secret_leaks_into_an_error_body():
    response = httpx.post(
        f"{env.MINICRM_URL}/areas",
        json=_area_payload("P-0001"),
        headers={"Authorization": f"Bearer {env.ADMIN_TOKEN}x"},
        timeout=10.0,
    )
    assert env.ADMIN_TOKEN not in response.text
