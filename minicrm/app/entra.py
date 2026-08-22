"""Microsoft Entra ID (OIDC / OAuth2 Authorization Code + PKCE) cho Mini CRM.

Đây là NỀN XÁC THỰC NGƯỜI DÙNG. Ba token tĩnh của `app/auth.py` (D-14) KHÔNG bị
xoá — chúng lùi xuống thành đường máy-với-máy/dev, mặc định TẮT ở production
(`MINICRM_LEGACY_TOKEN_AUTH_ENABLED=false`, xem `app/config.py`).

Bốn nguyên tắc, mirror đúng tinh thần `app/auth.py`:

1. **Không tin bất kỳ trường nào client tự khai.** Vai trò suy ra từ claim đã
   được KÝ trong token Entra (`roles`/`groups`), không bao giờ từ body/header.
2. **Chưa cấu hình = ĐÓNG.** `entra_configured()` sai ⇒ đường Entra không tồn
   tại, chứ không mở toang.
3. **Xác minh chữ ký THẬT.** RS256 + JWKS tải từ discovery document của tenant,
   kiểm `iss`/`aud`/`exp`/`nbf`. Không bao giờ `verify_signature=False`.
4. **Không hard-code gì.** tenant/client/secret/redirect đều từ env.

VÌ SAO BACKEND CẦM `client_secret` (mô hình BFF) chứ không để SPA tự chạy PKCE:
access token khi đó KHÔNG BAO GIỜ chạm vào JavaScript. Trình duyệt chỉ giữ một
cookie `HttpOnly`, `SameSite=Lax`, `Secure` (xem `app/session.py`) — nên một lỗ
XSS ở frontend không đọc được token, khác hẳn `localStorage` mà `AuthContext`
đang dùng trước thay đổi này. PKCE VẪN được dùng kèm (S256) dù đã có secret:
nó khoá `code` vào đúng phiên đã khởi tạo, chặn code-injection.

GIỚI HẠN ĐÃ BIẾT: JWKS cache nằm trong tiến trình (dict + TTL). Mini CRM chạy
đơn tiến trình (xem `app/relay.py` về cùng giả định). Chạy nhiều worker thì mỗi
worker giữ một bản sao — vô hại, chỉ tốn thêm vài request tới Entra mỗi giờ.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from app.config import get_settings
from fastapi import HTTPException
from jwt import PyJWKClient

# TTL cache khoá công khai. Entra xoay khoá ký định kỳ; 1 giờ đủ ngắn để bắt kịp
# một lần xoay khoá bình thường, đủ dài để không gọi discovery mỗi request.
_JWKS_TTL_SECONDS = 3600

_jwks_cache: dict[str, tuple[float, PyJWKClient]] = {}


class EntraConfigError(RuntimeError):
    """Cấu hình Entra thiếu/mâu thuẫn — lỗi VẬN HÀNH, không phải lỗi người gọi."""


@dataclass(frozen=True, slots=True)
class EntraIdentity:
    """Danh tính người dùng rút từ token ĐÃ XÁC MINH. Không giữ token thô."""

    subject: str  # `oid` (ổn định trong tenant) — KHÔNG dùng email làm khoá.
    email: str | None
    display_name: str | None
    roles: frozenset[str]
    groups: frozenset[str]
    expires_at: int


def oidc_active() -> bool:
    """True khi đường OIDC generic (Keycloak/…) đã cấu hình và ĐƯỢC ƯU TIÊN hơn
    Entra. Import cục bộ để tránh vòng phụ thuộc (oidc.py import từ file này)."""
    from app import oidc

    return oidc.oidc_configured()


def entra_configured() -> bool:
    """Có MỘT nhà cung cấp OIDC nào đã cấu hình chưa (Entra HOẶC generic OIDC như
    Keycloak). Giữ tên hàm để auth_routes/session không phải đổi. §12: OIDC_* set ⇒
    OIDC; nếu không, ENTRA_* hợp lệ ⇒ Entra."""
    if oidc_active():
        return True
    s = get_settings()
    return bool(
        s.entra_tenant_id
        and s.entra_client_id
        and s.entra_client_secret.get_secret_value()
        and s.entra_redirect_uri
    )


def _authority() -> str:
    s = get_settings()
    return f"{s.entra_authority_host.rstrip('/')}/{s.entra_tenant_id}"


def discovery_url() -> str:
    return f"{_authority()}/v2.0/.well-known/openid-configuration"


def authorize_endpoint() -> str:
    return f"{_authority()}/oauth2/v2.0/authorize"


def token_endpoint() -> str:
    return f"{_authority()}/oauth2/v2.0/token"


def logout_endpoint() -> str:
    return f"{_authority()}/oauth2/v2.0/logout"


def jwks_uri() -> str:
    return f"{_authority()}/discovery/v2.0/keys"


def expected_issuers() -> list[str]:
    """Entra v2.0 phát `iss` theo TENANT ID (không theo tên miền), nên chấp nhận
    cả hai dạng thường gặp thay vì đoán một dạng rồi 401 toàn bộ.

    `MINICRM_ENTRA_ISSUER` (nếu đặt) THẮNG tuyệt đối — dùng cho tenant B2C/CIAM
    hoặc khi test với một IdP giả lập.
    """
    s = get_settings()
    if s.entra_issuer:
        return [s.entra_issuer]
    return [
        f"{_authority()}/v2.0",
        f"https://sts.windows.net/{s.entra_tenant_id}/",
    ]


def expected_audience() -> str:
    """Audience mà token PHẢI mang. Mặc định là chính client ID của Mini CRM
    (ID token). Đặt `MINICRM_ENTRA_AUDIENCE` khi Mini CRM nhận ACCESS token do
    một app registration API khác phát (`api://<app-id>`)."""
    s = get_settings()
    return s.entra_audience or s.entra_client_id


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_pkce_pair() -> tuple[str, str]:
    """`(verifier, challenge_S256)`. Verifier 43–128 ký tự theo RFC 7636."""
    verifier = _b64url(_secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(*, state: str, nonce: str, code_challenge: str) -> str:
    if oidc_active():
        from app import oidc

        return oidc.build_authorize_url(state=state, nonce=nonce, code_challenge=code_challenge)
    s = get_settings()
    params = {
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
    return f"{authorize_endpoint()}?{urlencode(params)}"


def build_logout_url() -> str | None:
    if oidc_active():
        from app import oidc

        return oidc.build_logout_url()
    s = get_settings()
    params = {}
    if s.entra_post_logout_redirect_uri:
        params["post_logout_redirect_uri"] = s.entra_post_logout_redirect_uri
    return f"{logout_endpoint()}?{urlencode(params)}" if params else logout_endpoint()


# ---------------------------------------------------------------------------
# Đổi mã lấy token
# ---------------------------------------------------------------------------


async def exchange_code(*, code: str, code_verifier: str) -> dict[str, Any]:
    """Authorization code → token set. Ném `HTTPException` 401/502 chứ không để
    lộ nguyên văn phản hồi của Entra ra ngoài (nó có thể chứa gợi ý cấu hình)."""
    if oidc_active():
        from app import oidc

        return await oidc.exchange_code(code=code, code_verifier=code_verifier)
    if not entra_configured():
        raise EntraConfigError("Entra chưa được cấu hình đầy đủ.")
    s = get_settings()
    data = {
        "client_id": s.entra_client_id,
        "client_secret": s.entra_client_secret.get_secret_value(),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": s.entra_redirect_uri,
        "code_verifier": code_verifier,
        "scope": s.entra_scopes,
    }
    async with httpx.AsyncClient(timeout=s.sync_timeout_seconds) as client:
        resp = await client.post(
            token_endpoint(),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Đổi authorization code lấy token thất bại.",
                "error_code": "ENTRA_CODE_EXCHANGE_FAILED",
            },
        )
    return resp.json()


async def refresh_tokens(refresh_token: str) -> dict[str, Any]:
    if oidc_active():
        from app import oidc

        return await oidc.refresh_tokens(refresh_token)
    s = get_settings()
    data = {
        "client_id": s.entra_client_id,
        "client_secret": s.entra_client_secret.get_secret_value(),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": s.entra_scopes,
    }
    async with httpx.AsyncClient(timeout=s.sync_timeout_seconds) as client:
        resp = await client.post(token_endpoint(), data=data)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên đã hết hạn.", "error_code": "SESSION_EXPIRED"},
        )
    return resp.json()


# ---------------------------------------------------------------------------
# Xác minh token
# ---------------------------------------------------------------------------


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
    """Dùng trong test và sau khi xoay khoá thủ công."""
    _jwks_cache.clear()


def _decode(token: str, *, key: Any, algorithms: list[str], audience: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for issuer in expected_issuers():
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=algorithms,
                audience=audience,
                issuer=issuer,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
                leeway=get_settings().entra_clock_skew_seconds,
            )
        except jwt.InvalidIssuerError as exc:  # thử issuer kế tiếp
            last_error = exc
            continue
    raise last_error or jwt.InvalidTokenError("Không có issuer nào khớp.")


def verify_token(token: str, *, public_key: Any | None = None) -> EntraIdentity:
    """Xác minh chữ ký + `iss`/`aud`/`exp` rồi rút danh tính.

    `public_key` CHỈ để test tiêm khoá cố định (xem `tests/test_entra_auth.py`);
    production để `None` ⇒ khoá lấy từ JWKS thật của tenant.
    """
    if oidc_active():
        from app import oidc

        return oidc.verify_token(token, public_key=public_key)
    if not get_settings().entra_tenant_id:
        raise HTTPException(
            status_code=503,
            detail={"message": "Entra chưa được cấu hình.", "error_code": "AUTH_DISABLED"},
        )
    try:
        if public_key is None:
            signing_key = _jwk_client().get_signing_key_from_jwt(token).key
        else:
            signing_key = public_key
        claims = _decode(
            token,
            key=signing_key,
            algorithms=list(get_settings().entra_allowed_algorithms.split(",")),
            audience=expected_audience(),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"message": "Token đã hết hạn.", "error_code": "TOKEN_EXPIRED"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except Exception:
        # Gộp mọi lý do còn lại vào MỘT thông báo: phân biệt "sai chữ ký" với
        # "sai audience" là chỉ đường cho người đang dò.
        raise HTTPException(
            status_code=401,
            detail={"message": "Token không hợp lệ.", "error_code": "INVALID_TOKEN"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    return EntraIdentity(
        subject=str(claims.get("oid") or claims["sub"]),
        email=claims.get("preferred_username") or claims.get("email"),
        display_name=claims.get("name"),
        roles=frozenset(claims.get("roles") or []),
        groups=frozenset(claims.get("groups") or []),
        expires_at=int(claims["exp"]),
    )
