"""Phiên đăng nhập Mini CRM: cookie `HttpOnly` mang một JWT do CHÍNH Mini CRM ký.

Vì sao không nhét thẳng access token của Keycloak vào cookie: access token đó
thuộc về Keycloak — Mini CRM không được phép rút ngắn hạn dùng của nó, không gắn
thêm được `role`/`project_scope` đã phân giải, và mỗi request lại phải xác minh
RS256 + tra JWKS. Một phiên riêng ký HS256 giải quyết cả ba: TTL do Mini CRM đặt,
vai trò/phạm vi đã phân giải MỘT LẦN tại callback, xác minh là một phép so HMAC.

Refresh token của Keycloak được cất trong cùng phiên đó (đã ký, `HttpOnly`,
không bao giờ ra tới JavaScript) để `/auth/refresh` gia hạn được mà không bắt
người dùng quay lại Keycloak.

ÁNH XẠ VAI TRÒ (fail-closed). `MINICRM_OIDC_ROLE_MAP` là JSON
`{"<claim hoặc group-id>": "admin|pipeline_operator|business_viewer"}`.
Người dùng không khớp claim nào ⇒ KHÔNG có vai trò ⇒ 403. Không có "mặc định là
business_viewer": một tài khoản bất kỳ trong realm không đương nhiên được nhìn
dữ liệu bán hàng.
"""

from __future__ import annotations

import hashlib
import json
import secrets as _secrets
import time
from typing import Any, Literal

import jwt
import redis.asyncio as redis_asyncio
from app.config import get_settings
from app.oidc import OidcIdentity
from fastapi import HTTPException, Response

SESSION_COOKIE = "minicrm_session"
_ALGORITHM = "HS256"

MiniCrmRole = Literal["business_viewer", "pipeline_operator", "admin"]
_VALID_ROLES = {"business_viewer", "pipeline_operator", "admin"}
_ROLE_LEVEL = {"business_viewer": 0, "pipeline_operator": 1, "admin": 2}
_SESSION_REVOCATION_PREFIX = "p100:jwt:blacklist:"
_REVOKE_ALL_PREFIX = "p100:jwt:revoke_all:"
_revocation_redis: redis_asyncio.Redis | None = None


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
    raw = get_settings().oidc_role_map.get_secret_value()
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
    raw = get_settings().oidc_project_scope.get_secret_value()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Vai trò nghiệp vụ chuẩn (CEO/ADVISOR/SALES), dùng chung với Product/AbsorbIQ —
# CỐ ĐỊNH trong code, không cấu hình qua MINICRM_OIDC_ROLE_MAP.
# `app/config.py::_reject_conflicting_canonical_role_map` từ chối khởi động nếu
# MINICRM_OIDC_ROLE_MAP cố định nghĩa lại một trong ba khoá này thành giá trị
# khác — nên `setdefault` dưới đây không bao giờ che giấu một xung đột thật.
CANONICAL_APP_ROLES: dict[str, MiniCrmRole] = {
    "CRM.CEO": "admin",
    "CRM.Admin": "admin",
    "CRM.ADVISOR": "business_viewer",
    "CRM.Viewer": "business_viewer",
    "CRM.SALES": "pipeline_operator",
    "CRM.Operator": "pipeline_operator",
}


def resolve_role(identity: OidcIdentity) -> MiniCrmRole:
    """Vai trò CAO NHẤT trong các claim khớp. Không khớp gì ⇒ 403."""
    mapping = _role_map()
    for canonical_role, internal_role in CANONICAL_APP_ROLES.items():
        mapping.setdefault(canonical_role, internal_role)
    matched = [
        mapping[claim]
        for claim in (*identity.roles, *identity.groups)
        if claim in mapping
    ]
    # Realm roles có TÊN đúng bằng ba vai trò nội bộ được chấp nhận như chính nó
    # kể cả khi MINICRM_OIDC_ROLE_MAP chưa liệt kê. Người tự đăng ký nhận
    # `business_viewer` qua default role của realm ⇒ KHÔNG bao giờ tự lên admin.
    if not matched:
        matched = [c for c in (*identity.roles, *identity.groups) if c in _VALID_ROLES]
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


def resolve_scope(identity: OidcIdentity, role: MiniCrmRole) -> list[str] | str:
    """Phạm vi dự án gắn theo CLAIM (không theo người dùng): cùng mô hình tĩnh
    mà `app/auth.py` đã dùng cho token, chỉ đổi khoá tra cứu. Vắng mặt = RỖNG."""
    mapping = _scope_map()
    for claim in (*identity.roles, *identity.groups):
        if claim in mapping:
            value = mapping[claim]
            return "ALL" if value == "ALL" else list(value)
    return []


def issue_session(
    identity: OidcIdentity,
    *,
    role: MiniCrmRole,
    scope: list[str] | str,
    refresh_token: str | None = None,
    id_token_hint: str | None = None,
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
        "jti": _secrets.token_urlsafe(32),
    }
    if refresh_token:
        payload["rt"] = refresh_token
    if id_token_hint:
        payload["id_token_hint"] = id_token_hint
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def _decode_session(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    options = {"require": ["exp", "sub", "role"], "verify_exp": verify_exp}
    try:
        return jwt.decode(
            token,
            _secret(),
            algorithms=[_ALGORITHM],
            issuer="minicrm",
            options=options,
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


def read_session(token: str) -> dict[str, Any]:
    return _decode_session(token)


def _get_revocation_redis() -> redis_asyncio.Redis:
    global _revocation_redis
    if _revocation_redis is None:
        _revocation_redis = redis_asyncio.from_url(
            get_settings().redis_url.get_secret_value(), decode_responses=True
        )
    return _revocation_redis


def _blacklist_key(token: str) -> str:
    return f"{_SESSION_REVOCATION_PREFIX}{hashlib.sha256(token.encode()).hexdigest()}"


def _revoke_all_key(subject: str) -> str:
    return f"{_REVOKE_ALL_PREFIX}{hashlib.sha256(subject.encode()).hexdigest()}"


async def revoke_shared_token(token: str, *, ttl: int | None = None) -> bool:
    if not token:
        return False
    lifetime = ttl or get_settings().session_ttl_seconds
    try:
        await _get_revocation_redis().set(_blacklist_key(token), "1", ex=max(1, lifetime))
        return True
    except Exception:
        return False


async def revoke_session(token: str) -> bool:
    try:
        claims = _decode_session(token, verify_exp=False)
        lifetime = max(1, int(claims["exp"]) - int(time.time()))
        await _get_revocation_redis().set(_blacklist_key(token), "1", ex=lifetime)
        return True
    except HTTPException:
        return False
    except Exception:
        return False


async def revoke_all_sessions(subject: str) -> bool:
    """Đăng xuất KHỎI MỌI THIẾT BỊ cho `subject`, không cần liệt kê từng token.

    Phiên Mini CRM là JWT tự chứa (stateless) — không có bảng phiên để xoá theo
    hàng. Thay vào đó, hàm này ghi một MỐC THỜI GIAN vào Redis; `read_session_
    verified` coi mọi phiên có `iat` (thời điểm phát hành) SỚM HƠN mốc này là đã
    bị thu hồi, bất kể nó được phát ở thiết bị/trình duyệt nào. TTL bằng đúng
    `session_ttl_seconds`: quá thời hạn đó, phiên cũ nhất còn có thể tồn tại
    cũng đã tự hết hạn qua `exp`, nên mốc không cần sống lâu hơn thế.
    """
    if not subject:
        return False
    try:
        await _get_revocation_redis().set(
            _revoke_all_key(subject),
            str(int(time.time())),
            ex=max(1, get_settings().session_ttl_seconds),
        )
        return True
    except Exception:
        return False


async def read_session_verified(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    claims = _decode_session(token, verify_exp=verify_exp)
    try:
        redis_client = _get_revocation_redis()
        revoked = bool(await redis_client.exists(_blacklist_key(token)))
        if not revoked:
            floor_raw = await redis_client.get(_revoke_all_key(str(claims.get("sub", ""))))
            if floor_raw is not None and int(claims.get("iat", 0)) <= int(floor_raw):
                revoked = True
        if revoked:
            raise HTTPException(
                status_code=401,
                detail={"message": "Phiên đã bị thu hồi.", "error_code": "SESSION_REVOKED"},
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Không thể kiểm tra trạng thái phiên.",
                "error_code": "SESSION_REVOCATION_UNAVAILABLE",
            },
        ) from exc
    return claims


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
