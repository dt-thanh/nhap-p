"""Phiên đăng nhập Mini CRM: cookie `HttpOnly` mang một JWT do CHÍNH Mini CRM ký.

Vì sao không nhét thẳng access token của Entra vào cookie: access token của Entra
thuộc về Entra — Mini CRM không được phép rút ngắn hạn dùng của nó, không gắn
thêm được `role`/`project_scope` đã phân giải, và mỗi request lại phải xác minh
RS256 + tra JWKS. Một phiên riêng ký HS256 giải quyết cả ba: TTL do Mini CRM đặt,
vai trò/phạm vi đã phân giải MỘT LẦN tại callback, xác minh là một phép so HMAC.

Refresh token của Entra được cất trong cùng phiên đó (đã ký, `HttpOnly`, không
bao giờ ra tới JavaScript) để `/auth/refresh` gia hạn được mà không bắt người
dùng quay lại Entra.

ÁNH XẠ VAI TRÒ (fail-closed). `MINICRM_ENTRA_ROLE_MAP` là JSON
`{"<app-role hoặc group-id>": "admin|pipeline_operator|business_viewer"}`.
Người dùng không khớp claim nào ⇒ KHÔNG có vai trò ⇒ 403. Không có "mặc định là
business_viewer": một tài khoản bất kỳ trong tenant không đương nhiên được nhìn
dữ liệu bán hàng.
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

import jwt
from app.config import get_settings
from app.entra import EntraIdentity
from fastapi import HTTPException, Response

SESSION_COOKIE = "minicrm_session"
_ALGORITHM = "HS256"

MiniCrmRole = Literal["business_viewer", "pipeline_operator", "admin"]
_VALID_ROLES = {"business_viewer", "pipeline_operator", "admin"}
_ROLE_LEVEL = {"business_viewer": 0, "pipeline_operator": 1, "admin": 2}


def session_configured() -> bool:
    return bool(get_settings().session_secret.get_secret_value())


def _secret() -> str:
    value = get_settings().session_secret.get_secret_value()
    if not value:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "MINICRM_SESSION_SECRET chưa được cấu hình.",
                "error_code": "SESSION_NOT_CONFIGURED",
            },
        )
    return value


def _role_map() -> dict[str, str]:
    raw = get_settings().entra_role_map.get_secret_value()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if str(v) in _VALID_ROLES}


def _scope_map() -> dict[str, Any]:
    raw = get_settings().entra_project_scope.get_secret_value()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_role(identity: EntraIdentity) -> MiniCrmRole:
    """Vai trò CAO NHẤT trong các claim khớp. Không khớp gì ⇒ 403."""
    mapping = _role_map()
    matched = [
        mapping[claim]
        for claim in (*identity.roles, *identity.groups)
        if claim in mapping
    ]
    if not matched:
        raise HTTPException(
            status_code=403,
            detail={
                "message": (
                    "Tài khoản đã xác thực nhưng chưa được cấp vai trò nào trong Mini CRM. "
                    "Liên hệ quản trị để được gán app role."
                ),
                "error_code": "NO_ROLE_ASSIGNED",
            },
        )
    return max(matched, key=lambda r: _ROLE_LEVEL[r])  # type: ignore[return-value]


def resolve_scope(identity: EntraIdentity, role: MiniCrmRole) -> list[str] | str:
    """Phạm vi dự án gắn theo CLAIM (không theo người dùng): cùng mô hình tĩnh
    mà `app/auth.py` đã dùng cho token, chỉ đổi khoá tra cứu. Vắng mặt = RỖNG."""
    mapping = _scope_map()
    for claim in (*identity.roles, *identity.groups):
        if claim in mapping:
            value = mapping[claim]
            return "ALL" if value == "ALL" else list(value)
    return []


def issue_session(
    identity: EntraIdentity,
    *,
    role: MiniCrmRole,
    scope: list[str] | str,
    refresh_token: str | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": identity.subject,
        "email": identity.email,
        "name": identity.display_name,
        "role": role,
        "scope": scope,
        "iss": "minicrm",
        "iat": now,
        "exp": now + get_settings().session_ttl_seconds,
    }
    if refresh_token:
        payload["rt"] = refresh_token
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def read_session(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=[_ALGORITHM],
            issuer="minicrm",
            options={"require": ["exp", "sub", "role"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên đã hết hạn.", "error_code": "SESSION_EXPIRED"},
        ) from None
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên không hợp lệ.", "error_code": "INVALID_SESSION"},
        ) from None


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=s.session_ttl_seconds,
        httponly=True,  # JavaScript KHÔNG đọc được — đây là điểm chính.
        secure=s.session_cookie_secure,
        samesite=s.session_cookie_samesite,  # type: ignore[arg-type]
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=s.session_cookie_secure,
        samesite=s.session_cookie_samesite,  # type: ignore[arg-type]
    )
