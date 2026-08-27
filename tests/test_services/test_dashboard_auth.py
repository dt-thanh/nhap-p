"""`src.services.dashboard_auth` — RBAC cho mặt đọc vận hành (Phase 5.5 P0).

Thuần, không cần DB: xác thực chỉ so một chuỗi token với ba token cấu hình qua
Settings. Cùng cách với `tests/test_api/test_ops_endpoint.py` kiểm
`require_ops_token` — phần lớn test ở đây kiểm việc TỪ CHỐI, không phải việc
cho qua, vì đó là nơi một endpoint nội bộ dễ bị lộ nhất.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.services.dashboard_auth import (
    authenticate_dashboard,
    require_role,
    role_level,
)

VIEWER = "viewer-token-0123456789"
OPERATOR = "operator-token-0123456789"
ADMIN = "admin-token-0123456789"


@pytest.fixture
def all_roles_configured(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("DASHBOARD_BUSINESS_VIEWER_TOKEN", VIEWER)
    monkeypatch.setenv("DASHBOARD_PIPELINE_OPERATOR_TOKEN", OPERATOR)
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", ADMIN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def no_roles_configured(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    for key in ("DASHBOARD_BUSINESS_VIEWER_TOKEN", "DASHBOARD_PIPELINE_OPERATOR_TOKEN", "DASHBOARD_ADMIN_TOKEN"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv(key, "")
    # "Nothing configured" must mean nothing configured — including Keycloak.
    # The real .env carries working OIDC_* defaults (matching docker-compose),
    # so isolate this fixture from that or `oidc_configured()` stays true and
    # the 503 DASHBOARD_AUTH_DISABLED path never triggers.
    for key in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_dev_auth_bypass_defaults_to_false(monkeypatch):
    from src.config import Settings

    monkeypatch.delenv("DEV_AUTH_BYPASS", raising=False)
    settings = Settings(_env_file=None)

    assert settings.dev_auth_bypass is False


# --- Chưa cấu hình = ĐÓNG -----------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_when_no_role_token_is_configured(no_roles_configured):
    with pytest.raises(HTTPException) as exc:
        await authenticate_dashboard(f"Bearer {VIEWER}")
    assert exc.value.status_code == 503
    assert exc.value.detail["error_code"] == "DASHBOARD_AUTH_DISABLED"


@pytest.mark.asyncio
async def test_disabled_even_with_a_token_that_would_otherwise_match(no_roles_configured):
    """Token đúng CHUỖI nhưng hệ thống chưa cấu hình gì — vẫn phải đóng."""
    with pytest.raises(HTTPException) as exc:
        await authenticate_dashboard("Bearer whatever")
    assert exc.value.status_code == 503


# --- Thiếu / sai thông tin xác thực -------------------------------------------


@pytest.mark.asyncio
async def test_missing_authorization_header_is_401(all_roles_configured):
    with pytest.raises(HTTPException) as exc:
        await authenticate_dashboard(None)
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "MISSING_CREDENTIALS"


@pytest.mark.asyncio
async def test_development_bypass_returns_an_all_scope_principal_without_a_token(monkeypatch, no_roles_configured):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    get_settings.cache_clear()

    principal = await authenticate_dashboard(None)

    assert principal.role == "admin"
    assert principal.project_scope == "ALL"


@pytest.mark.asyncio
async def test_development_bypass_disabled_still_requires_a_token(monkeypatch, all_roles_configured):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc:
        await authenticate_dashboard(None)
    assert exc.value.status_code == 401


def test_production_with_bypass_is_rejected_at_startup(monkeypatch, all_roles_configured):
    """Trước đây: `APP_ENV=production` + `DEV_AUTH_BYPASS=true` là một cấu hình
    HỢP LỆ mà `authenticate_dashboard` chỉ ÂM THẦM bỏ qua ở lúc chạy (401 như
    không có bypass). Giờ tổ hợp đó không còn được coi là hợp lệ nữa: `Settings`
    từ chối ngay lúc khởi động (`src/config.py::_reject_dev_bypass_outside_development`)
    — tiến trình không khởi động được thay vì khởi động với một cờ bypass nằm
    chờ, chỉ cần `APP_ENV` bị đổi là bật lại.
    """
    from pydantic import ValidationError

    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    get_settings.cache_clear()

    with pytest.raises(ValidationError, match="DEV_AUTH_BYPASS=true"):
        get_settings()


@pytest.mark.asyncio
async def test_valid_bearer_authentication_remains_functional_with_bypass_enabled(monkeypatch, all_roles_configured):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    get_settings.cache_clear()

    principal = await authenticate_dashboard(f"Bearer {ADMIN}")

    assert principal.role == "admin"


@pytest.mark.asyncio
async def test_malformed_scheme_is_treated_as_missing(all_roles_configured):
    with pytest.raises(HTTPException) as exc:
        await authenticate_dashboard(f"Basic {VIEWER}")
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "MISSING_CREDENTIALS"


@pytest.mark.asyncio
async def test_wrong_token_is_401(all_roles_configured):
    with pytest.raises(HTTPException) as exc:
        await authenticate_dashboard("Bearer not-a-real-token")
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_a_token_prefix_is_not_accepted(all_roles_configured):
    with pytest.raises(HTTPException) as exc:
        await authenticate_dashboard(f"Bearer {OPERATOR[:10]}")
    assert exc.value.status_code == 401


# --- Vai trò suy ra từ TOKEN, không phải từ trường client tự khai -------------


@pytest.mark.asyncio
async def test_each_token_resolves_to_exactly_its_own_role(all_roles_configured):
    assert (await authenticate_dashboard(f"Bearer {VIEWER}")).role == "business_viewer"
    assert (await authenticate_dashboard(f"Bearer {OPERATOR}")).role == "pipeline_operator"
    assert (await authenticate_dashboard(f"Bearer {ADMIN}")).role == "admin"


@pytest.mark.asyncio
async def test_no_arbitrary_client_role_is_trusted(all_roles_configured):
    """`authenticate_dashboard` không nhận tham số role nào từ client — chữ ký
    hàm chỉ nhận `Authorization`. Một request tự xưng admin qua bất kỳ trường
    nào khác (X-Role, body, query) không có đường nào chạm tới quyết định vai
    trò: vai trò LUÔN LÀ kết quả so token, không phải giá trị đọc lại từ request.
    """
    principal = await authenticate_dashboard(f"Bearer {VIEWER}")
    assert principal.role == "business_viewer", "token của business_viewer không thể tự nâng cấp vai trò"


# --- require_role: 403 khi vai trò không đủ -----------------------------------


@pytest.mark.asyncio
async def test_require_role_allows_an_exact_match(all_roles_configured):
    dep = require_role("pipeline_operator")
    principal = await dep(authorization=f"Bearer {OPERATOR}", absorbiq_session=None)
    assert principal.role == "pipeline_operator"


@pytest.mark.asyncio
async def test_require_role_allows_a_higher_role(all_roles_configured):
    dep = require_role("pipeline_operator")
    principal = await dep(authorization=f"Bearer {ADMIN}", absorbiq_session=None)
    assert principal.role == "admin"


@pytest.mark.asyncio
async def test_require_role_rejects_a_lower_role_with_403(all_roles_configured):
    """business_viewer không tới được mặt đọc vận hành (đòi pipeline_operator+)."""
    dep = require_role("pipeline_operator")
    with pytest.raises(HTTPException) as exc:
        await dep(authorization=f"Bearer {VIEWER}", absorbiq_session=None)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "INSUFFICIENT_ROLE"


@pytest.mark.asyncio
async def test_pipeline_operator_cannot_reach_an_admin_only_action(all_roles_configured):
    dep = require_role("admin")
    with pytest.raises(HTTPException) as exc:
        await dep(authorization=f"Bearer {OPERATOR}", absorbiq_session=None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_role_still_401s_before_403_when_credentials_are_missing(all_roles_configured):
    """Thiếu xác thực → 401 (chưa chứng minh được danh tính), KHÔNG phải 403
    (danh tính đúng nhưng thiếu quyền) — hai lỗi khác nghĩa nhau."""
    dep = require_role("business_viewer")
    with pytest.raises(HTTPException) as exc:
        await dep(authorization=None, absorbiq_session=None)
    assert exc.value.status_code == 401


def test_role_level_is_strictly_ordered():
    assert role_level("business_viewer") < role_level("pipeline_operator") < role_level("admin")
