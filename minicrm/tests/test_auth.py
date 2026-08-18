"""Xác thực GHI của Mini CRM (D-14): business_viewer / pipeline_operator / admin,
phạm vi dự án TĨNH. Mirror `src/services/dashboard_auth.py` của backend — xem
`app/auth.py` cho nguyên tắc đầy đủ.

`crm_app` (conftest.py) cấu hình sẵn ba token cho MỌI test CRUD khác (mặc định
dùng `ADMIN_AUTH_HEADER` để không phải sửa từng lời gọi) — file này là nơi DUY
NHẤT kiểm chính cơ chế xác thực/phạm vi, không lẫn với test CRUD nghiệp vụ.
"""

from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import (
    ADMIN_AUTH_HEADER,
    ADMIN_TOKEN,
    OPERATOR_AUTH_HEADER,
    OPERATOR_TOKEN,
    TEST_PROJECT_EXTERNAL_ID,
    VIEWER_AUTH_HEADER,
)

PROJECT = {"name": "Auth E2E", "launch_date": "2026-06-01"}
AREA_IN_SCOPE = {
    "external_project_id": TEST_PROJECT_EXTERNAL_ID,
    "area_name": "InScope",
    "unit_type": "2PN",
    "bedrooms": 2,
    "area_sqm": 60.0,
    "total_units": 10,
}


def _client(headers: dict | None = None) -> TestClient:
    return TestClient(app, headers=headers or {})


# --- Không có / sai thông tin xác thực ----------------------------------------


def test_no_token_on_a_write_route_is_401(crm_app, backend):
    with _client() as client:
        response = client.post("/areas", json=AREA_IN_SCOPE)
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "MISSING_CREDENTIALS"


def test_invalid_token_on_a_write_route_is_401(crm_app, backend):
    with _client({"Authorization": "Bearer not-a-real-token"}) as client:
        response = client.post("/areas", json=AREA_IN_SCOPE)
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "INVALID_CREDENTIALS"


def test_malformed_authorization_header_is_401_not_500(crm_app, backend):
    with _client({"Authorization": OPERATOR_TOKEN}) as client:  # thiếu tiền tố "Bearer "
        response = client.post("/areas", json=AREA_IN_SCOPE)
    assert response.status_code == 401


# --- Vai trò không đủ -----------------------------------------------------------


def test_business_viewer_write_is_403(crm_app, backend):
    with _client(VIEWER_AUTH_HEADER) as client:
        response = client.post("/areas", json=AREA_IN_SCOPE)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"


def test_business_viewer_cannot_create_a_project(crm_app, backend):
    with _client(VIEWER_AUTH_HEADER) as client:
        response = client.post("/projects", json=PROJECT)
    assert response.status_code == 403


def test_pipeline_operator_cannot_create_a_project(crm_app, backend):
    """Tạo dự án đòi `admin` — một dự án MỚI không nằm trong phạm vi có sẵn của
    bất kỳ token nào, nên chỉ vai trò cao nhất mới mở rộng được nó."""
    with _client(OPERATOR_AUTH_HEADER) as client:
        response = client.post("/projects", json=PROJECT)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"


# --- Phạm vi dự án ---------------------------------------------------------------


def test_pipeline_operator_can_write_within_scope(crm_app, backend):
    with _client(OPERATOR_AUTH_HEADER) as client:
        response = client.post("/areas", json=AREA_IN_SCOPE)
    assert response.status_code == 201


def test_pipeline_operator_is_rejected_outside_scope(crm_app, backend):
    """`admin` tạo một dự án THỨ HAI (P-0002) — operator không được cấp phạm vi
    đó (`crm_app` chỉ cấu hình `[TEST_PROJECT_EXTERNAL_ID, "P-0001"]`)."""
    with _client(ADMIN_AUTH_HEADER) as client:
        client.post("/projects", json=PROJECT)  # P-0001
        second = client.post("/projects", json={"name": "Auth E2E 2", "launch_date": "2026-07-01"})  # P-0002
        assert second.status_code == 201
        other_project_id = second.json()["record"]["external_id"]

    with _client(OPERATOR_AUTH_HEADER) as client:
        response = client.post(
            "/areas",
            json={
                "external_project_id": other_project_id,
                "area_name": "OutOfScope",
                "unit_type": "3PN",
                "bedrooms": 3,
                "area_sqm": 80.0,
                "total_units": 5,
            },
        )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


def test_pipeline_operator_cannot_update_a_project_outside_scope(crm_app, backend):
    with _client(ADMIN_AUTH_HEADER) as client:
        client.post("/projects", json=PROJECT)  # P-0001, TRONG phạm vi operator
        second = client.post("/projects", json={"name": "Auth E2E 2", "launch_date": "2026-07-01"})  # P-0002
        other_id = second.json()["record"]["external_id"]

    with _client(OPERATOR_AUTH_HEADER) as client:
        response = client.patch(f"/projects/{other_id}", json={"name": "x"})
    assert response.status_code == 403


def test_admin_with_all_scope_can_write_across_projects(crm_app, backend):
    with _client(ADMIN_AUTH_HEADER) as client:
        first = client.post("/projects", json=PROJECT)
        second = client.post("/projects", json={"name": "Auth E2E 2", "launch_date": "2026-07-01"})
        assert first.status_code == 201 and second.status_code == 201

        area_first = client.post(
            "/areas",
            json={
                "external_project_id": first.json()["record"]["external_id"],
                "area_name": "A",
                "unit_type": "2PN",
                "bedrooms": 2,
                "area_sqm": 60.0,
                "total_units": 10,
            },
        )
        area_second = client.post(
            "/areas",
            json={
                "external_project_id": second.json()["record"]["external_id"],
                "area_name": "B",
                "unit_type": "3PN",
                "bedrooms": 3,
                "area_sqm": 80.0,
                "total_units": 5,
            },
        )
    assert area_first.status_code == 201
    assert area_second.status_code == 201, "admin với phạm vi ALL ghi được xuyên mọi dự án"


def test_unit_write_for_a_legacy_unit_with_no_area_is_rejected_by_v2_contract(crm_app, backend):
    """Căn DI SẢN (`area_id IS NULL`) không thể phát hành ghi v2.

    Scope vẫn được kiểm trước: operator bị từ chối vì không suy được dự án,
    còn admin đi qua scope rồi nhận đúng lỗi contract `UNIT_AREA_REQUIRED`.
    """
    import sqlalchemy as sa

    from tests.conftest import sync_url

    engine = sa.create_engine(sync_url(crm_app))
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_units (id, external_id, area_id, area_name, unit_type, unit_code, "
                    "unit_status, source_revision, created_at, updated_at) VALUES (gen_random_uuid(), "
                    "'U-LEGACY-AUTH', NULL, 'Di sản', 'Cũ', 'X-01', 'available', 1, now(), now())"
                )
            )
    finally:
        engine.dispose()

    with _client(OPERATOR_AUTH_HEADER) as client:
        response = client.patch("/units/U-LEGACY-AUTH", json={"unit_status": "reserved"})
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"

    with _client(ADMIN_AUTH_HEADER) as client:
        response = client.patch("/units/U-LEGACY-AUTH", json={"unit_status": "reserved"})
    assert response.status_code == 422
    assert response.json()["error_code"] == "UNIT_AREA_REQUIRED"


# --- Token không tự phong vai trò qua trường tự khai ----------------------------


def test_an_arbitrary_role_header_is_ignored_not_trusted(crm_app, backend):
    """Vai trò suy từ TOKEN NÀO KHỚP — một `X-Role: admin` tự khai đi kèm token
    viewer không được nâng quyền lên admin."""
    with _client({**VIEWER_AUTH_HEADER, "X-Role": "admin"}) as client:
        response = client.post("/areas", json=AREA_IN_SCOPE)
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"


# --- Fail-closed khi chưa cấu hình -----------------------------------------------


def test_unconfigured_auth_fails_closed(crm_app, backend, monkeypatch):
    from app.config import get_settings

    # Empty process values intentionally override any developer-local `.env`
    # values. Deleting the variables is insufficient because pydantic-settings
    # then reloads the local file and silently re-enables static auth.
    for name in (
        "MINICRM_AUTH_ADMIN_TOKEN",
        "MINICRM_AUTH_PIPELINE_OPERATOR_TOKEN",
        "MINICRM_AUTH_BUSINESS_VIEWER_TOKEN",
    ):
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        configured_token_count = sum(
            bool(getattr(settings, field).get_secret_value())
            for field in (
                "auth_admin_token",
                "auth_pipeline_operator_token",
                "auth_business_viewer_token",
            )
        )
        assert configured_token_count == 0, (
            "test isolation failure: expected zero configured static tokens, "
            f"found {configured_token_count}"
        )
        with _client(ADMIN_AUTH_HEADER) as client:
            response = client.post("/areas", json=AREA_IN_SCOPE)
        assert response.status_code == 503
        assert response.json()["detail"]["error_code"] == "AUTH_DISABLED"
    finally:
        get_settings.cache_clear()


# --- Đọc không bị ảnh hưởng ------------------------------------------------------


def test_resource_reads_require_authenticated_visibility(crm_app, backend):
    with _client() as client:
        assert client.get("/projects").status_code == 401
        assert client.get("/areas").status_code == 401
        assert client.get("/units").status_code == 401
        assert client.get("/deals").status_code == 401
        assert client.get("/outbox").status_code == 200


def test_secrets_never_appear_in_an_error_response(crm_app, backend):
    with _client({"Authorization": f"Bearer {ADMIN_TOKEN}x"}) as client:
        response = client.post("/areas", json=AREA_IN_SCOPE)
    assert ADMIN_TOKEN not in response.text
    assert OPERATOR_TOKEN not in response.text
