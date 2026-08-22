"""Microsoft Entra ID cho Product/AbsorbIQ backend (CP5 — SSO).

Đây là bản MIRROR của module Entra tương ứng bên Mini CRM (`app/entra.py` +
`app/session.py` của ứng dụng đó), dựng lại thành một instance RIÊNG chứ tuyệt
đối KHÔNG import qua ranh giới — cùng lý do mà ứng dụng đó không import
`src/services/dashboard_auth.py`: hai ứng dụng có vòng đời triển khai riêng, và
một import chéo biến chúng thành một hệ thống có hai giao diện.

Test cô lập của hệ nguồn (`tests/test_health.py` bên đó) quét TOÀN VĂN mọi file
trong `src/` — kể cả comment, không chỉ câu lệnh `import`. Đó là chủ ý: một
đường dẫn được nhắc tới trong comment là bước đầu tiên của một import sẽ tới.
Vì vậy tên thư mục của hệ nguồn CỐ Ý không xuất hiện ở đâu trong file này.

**SSO hoạt động như thế nào ở đây, và vì sao KHÔNG cần truyền token giữa hai
frontend.** Cả hai app đăng ký trong CÙNG một tenant Entra. Khi người dùng đã
đăng nhập ở Mini CRM, Microsoft giữ một phiên SSO trong trình duyệt (cookie của
`login.microsoftonline.com`). Product Frontend bắt đầu vòng OIDC của RIÊNG nó;
Entra thấy phiên đó còn sống nên trả `code` NGAY, không hiện màn hình nhập mật
khẩu. Người dùng chỉ thấy một lần nháy chuyển trang. Đó là toàn bộ cơ chế —
không query string mang token, không localStorage dùng chung, không cross-app
workaround nào cả.

Hai app có thể là hai app registration khác nhau (khuyến nghị: quyền và secret
tách bạch, thu hồi được độc lập). Khi đó `ENTRA_AUDIENCE` của mỗi bên khác nhau,
và một token phát cho Mini CRM sẽ bị Product từ chối — ĐÚNG như mong muốn, vì
audience tồn tại chính để chặn việc dùng lại token sang một dịch vụ khác.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, Response
from jwt import PyJWKClient

from src.config import get_settings
from src.logging_config import get_logger

log = get_logger("src.services.entra_auth")

SESSION_COOKIE = "absorbiq_session"
_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict[str, tuple[float, PyJWKClient]] = {}

DashboardRole = Literal["business_viewer", "pipeline_operator", "admin"]
_VALID_ROLES = {"business_viewer", "pipeline_operator", "admin"}
_ROLE_LEVEL = {"business_viewer": 0, "pipeline_operator": 1, "admin": 2}


@dataclass(frozen=True, slots=True)
class EntraIdentity:
    subject: str
    email: str | None
    display_name: str | None
    roles: frozenset[str]
    groups: frozenset[str]
    expires_at: int


def oidc_active() -> bool:
    """True khi đường OIDC generic (Keycloak/…) được cấu hình và nên ĐƯỢC ƯU TIÊN
    hơn Entra. Import cục bộ để tránh vòng phụ thuộc (oidc.py import EntraIdentity
    từ file này)."""
    from src.services import oidc

    return oidc.oidc_configured()


def entra_configured() -> bool:
    """Có MỘT nhà cung cấp danh tính OIDC nào đã cấu hình chưa (Entra HOẶC generic
    OIDC như Keycloak). Tên hàm giữ nguyên để `src/api/auth.py` và `dashboard_auth`
    không phải đổi; nó KHÔNG còn có nghĩa 'riêng Entra' mà là 'đường người dùng
    OIDC đang bật'. §12: OIDC_* set ⇒ dùng OIDC; nếu không, ENTRA_* hợp lệ ⇒ Entra."""
    if oidc_active():
        return True
    s = get_settings()
    return bool(
        s.entra_tenant_id
        and s.entra_client_id
        and s.entra_client_secret.get_secret_value()
        and s.entra_redirect_uri
        and s.session_secret.get_secret_value()
    )


def _authority() -> str:
    s = get_settings()
    return f"{s.entra_authority_host.rstrip('/')}/{s.entra_tenant_id}"


def authorize_endpoint() -> str:
    return f"{_authority()}/oauth2/v2.0/authorize"


def token_endpoint() -> str:
    return f"{_authority()}/oauth2/v2.0/token"


def logout_endpoint() -> str:
    return f"{_authority()}/oauth2/v2.0/logout"


def jwks_uri() -> str:
    return f"{_authority()}/discovery/v2.0/keys"


def expected_issuers() -> list[str]:
    s = get_settings()
    if s.entra_issuer:
        return [s.entra_issuer]
    return [f"{_authority()}/v2.0", f"https://sts.windows.net/{s.entra_tenant_id}/"]


def expected_audience() -> str:
    s = get_settings()
    return s.entra_audience or s.entra_client_id


# --- PKCE -------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(_secrets.token_bytes(64))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def build_authorize_url(*, state: str, nonce: str, code_challenge: str) -> str:
    if oidc_active():
        from src.services import oidc

        return oidc.build_authorize_url(state=state, nonce=nonce, code_challenge=code_challenge)
    s = get_settings()
    return f"{authorize_endpoint()}?" + urlencode(
        {
            "client_id": s.entra_client_id,
            "response_type": "code",
            "redirect_uri": s.entra_redirect_uri,
            "response_mode": "query",
            "scope": s.entra_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )


def build_logout_url() -> str | None:
    if oidc_active():
        from src.services import oidc

        return oidc.build_logout_url()
    s = get_settings()
    if s.entra_post_logout_redirect_uri:
        return f"{logout_endpoint()}?" + urlencode(
            {"post_logout_redirect_uri": s.entra_post_logout_redirect_uri}
        )
    return logout_endpoint()


async def exchange_code(*, code: str, code_verifier: str) -> dict[str, Any]:
    if oidc_active():
        from src.services import oidc

        return await oidc.exchange_code(code=code, code_verifier=code_verifier)
    s = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_endpoint(),
            data={
                "client_id": s.entra_client_id,
                "client_secret": s.entra_client_secret.get_secret_value(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.entra_redirect_uri,
                "code_verifier": code_verifier,
                "scope": s.entra_scopes,
            },
        )
    if resp.status_code != 200:
        log.warning("entra.code_exchange.failed", status=resp.status_code)
        raise HTTPException(
            status_code=401,
            detail={"message": "Đổi code lấy token thất bại.", "error_code": "ENTRA_CODE_EXCHANGE_FAILED"},
        )
    return resp.json()


async def refresh_tokens(refresh_token: str) -> dict[str, Any]:
    """Đổi refresh_token lấy access/id token MỚI (OIDC refresh grant).

    Bản MIRROR của `app/entra.py::refresh_tokens` bên Mini CRM — cùng grant, cùng
    xử lý lỗi. Điều kiện tiên quyết: `entra_scopes` PHẢI có `offline_access` thì
    Entra mới phát refresh_token ở vòng `callback` ban đầu (xem `issue_session`,
    nơi `rt` được cất vào phiên). Thiếu scope đó → callback không có refresh_token
    để cất → `/auth/refresh` luôn trả SESSION_EXPIRED, đúng như thiết kế fail-safe.
    """
    if oidc_active():
        from src.services import oidc

        return await oidc.refresh_tokens(refresh_token)
    s = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_endpoint(),
            data={
                "client_id": s.entra_client_id,
                "client_secret": s.entra_client_secret.get_secret_value(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": s.entra_scopes,
            },
        )
    if resp.status_code != 200:
        log.warning("entra.token_refresh.failed", status=resp.status_code)
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên đã hết hạn.", "error_code": "SESSION_EXPIRED"},
        )
    return resp.json()


# --- Xác minh ---------------------------------------------------------------


def _jwk_client() -> PyJWKClient:
    uri = jwks_uri()
    hit = _jwks_cache.get(uri)
    now = time.time()
    if hit and now - hit[0] < _JWKS_TTL_SECONDS:
        return hit[1]
    client = PyJWKClient(uri, cache_keys=True)
    _jwks_cache[uri] = (now, client)
    return client


def reset_jwks_cache() -> None:
    _jwks_cache.clear()


def verify_token(token: str, *, public_key: Any | None = None) -> EntraIdentity:
    """Xác minh chữ ký RS256 + `iss`/`aud`/`exp`. `public_key` chỉ dùng cho test."""
    if oidc_active():
        from src.services import oidc

        return oidc.verify_token(token, public_key=public_key)
    s = get_settings()
    if not s.entra_tenant_id:
        raise HTTPException(
            status_code=503,
            detail={"message": "Entra chưa cấu hình.", "error_code": "AUTH_DISABLED"},
        )
    key = public_key if public_key is not None else _jwk_client().get_signing_key_from_jwt(token).key

    last: Exception | None = None
    for issuer in expected_issuers():
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[a.strip() for a in s.entra_allowed_algorithms.split(",")],
                audience=expected_audience(),
                issuer=issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
                leeway=s.entra_clock_skew_seconds,
            )
            return EntraIdentity(
                subject=str(claims.get("oid") or claims["sub"]),
                email=claims.get("preferred_username") or claims.get("email"),
                display_name=claims.get("name"),
                roles=frozenset(claims.get("roles") or []),
                groups=frozenset(claims.get("groups") or []),
                expires_at=int(claims["exp"]),
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=401,
                detail={"message": "Token đã hết hạn.", "error_code": "TOKEN_EXPIRED"},
                headers={"WWW-Authenticate": "Bearer"},
            ) from None
        except jwt.InvalidIssuerError as exc:
            last = exc
            continue
        except Exception as exc:
            last = exc
            break

    log.warning("entra.token.rejected")
    raise HTTPException(
        status_code=401,
        detail={"message": "Token không hợp lệ.", "error_code": "INVALID_TOKEN"},
        headers={"WWW-Authenticate": "Bearer"},
    ) from last


# --- Ánh xạ vai trò + phiên -------------------------------------------------


def _json_setting(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_role(identity: EntraIdentity) -> DashboardRole:
    """Fail-closed: không khớp app role/group nào ⇒ 403, KHÔNG có vai trò mặc định."""
    mapping = {
        str(k): str(v)
        for k, v in _json_setting(get_settings().entra_role_map.get_secret_value()).items()
        if str(v) in _VALID_ROLES
    }
    matched = [mapping[c] for c in (*identity.roles, *identity.groups) if c in mapping]
    # OIDC/Keycloak: realm roles có TÊN đúng bằng ba vai trò nội bộ
    # (admin/pipeline_operator/business_viewer). Chấp nhận chúng như chính nó KỂ CẢ
    # khi ENTRA_ROLE_MAP chưa liệt kê — nhưng CHỈ ở chế độ OIDC, để nhánh Entra giữ
    # nguyên fail-closed (một app-role Entra tên trùng không tự lên quyền). Người tự
    # đăng ký nhận `business_viewer` qua default role của realm ⇒ KHÔNG bao giờ admin.
    if not matched and oidc_active():
        matched = [c for c in (*identity.roles, *identity.groups) if c in _VALID_ROLES]
    if not matched:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Tài khoản chưa được cấp vai trò nào trong AbsorbIQ.",
                "error_code": "NO_ROLE_ASSIGNED",
            },
        )
    return max(matched, key=lambda r: _ROLE_LEVEL[r])  # type: ignore[return-value]


def resolve_scope(identity: EntraIdentity) -> list[str] | str:
    mapping = _json_setting(get_settings().entra_project_scope.get_secret_value())
    for claim in (*identity.roles, *identity.groups):
        if claim in mapping:
            value = mapping[claim]
            return "ALL" if value == "ALL" else list(value)
    return []


def issue_session(
    identity: EntraIdentity,
    *,
    role: DashboardRole,
    scope: list[str] | str,
    refresh_token: str | None = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": identity.subject,
        "email": identity.email,
        "name": identity.display_name,
        "role": role,
        "scope": scope,
        "iss": "absorbiq",
        "iat": now,
        "exp": now + get_settings().session_ttl_seconds,
    }
    if refresh_token:
        payload["rt"] = refresh_token
    return jwt.encode(payload, get_settings().session_secret.get_secret_value(), algorithm="HS256")


def read_session(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            get_settings().session_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer="absorbiq",
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
        httponly=True,
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
