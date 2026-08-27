"""OpenID Connect identity provider cho Mini CRM — Keycloak-only.

Bản MIRROR của `src/services/oidc.py` bên Product — DỰNG RIÊNG, không import chéo
ranh giới (xem `app/config.py` về lý do cô lập tuyệt đối). Đây là module DUY NHẤT
chịu trách nhiệm xác thực người dùng — Microsoft Entra ID đã bị gỡ khỏi runtime
(xem lịch sử migration trong `pipeline_status.md` nếu cần dựng lại một nhà cung
cấp khác trong tương lai).

FRONT-CHANNEL vs BACK-CHANNEL (điểm cốt tử của Docker local).
- FRONT-CHANNEL (browser): authorization_endpoint, end_session_endpoint, issuer →
  host CÔNG KHAI `http://localhost:9090`.
- BACK-CHANNEL (container → IdP): token_endpoint, jwks_uri → host NỘI BỘ Docker
  `http://keycloak:8080`.

VÌ SAO BACKEND CẦM `client_secret` (mô hình BFF) chứ không để SPA tự chạy PKCE:
access token khi đó KHÔNG BAO GIỜ chạm vào JavaScript. Trình duyệt chỉ giữ một
cookie `HttpOnly`, `SameSite=Lax`, `Secure` (xem `app/session.py`). PKCE VẪN được
dùng kèm (S256) dù đã có secret: nó khoá `code` vào đúng phiên đã khởi tạo, chặn
code-injection.

SSO GIỮA MINICRM VÀ ABSORBIQ. Cả hai app đăng ký trong CÙNG một realm Keycloak
(`p100`), hai client riêng (`minicrm-client`, `absorbiq-client`) — một token phát
cho app này bị app kia từ chối, vì audience tồn tại chính để chặn việc dùng lại
token sang một dịch vụ khác.

ROLES: gộp top-level `roles` (mapper tuỳ biến của realm `p100`) VÀ
`realm_access.roles` (mặc định Keycloak) — không phụ thuộc ngầm vào cấu hình
mapper của một realm cụ thể.

GIỚI HẠN ĐÃ BIẾT: Discovery/JWKS cache nằm trong tiến trình (dict + TTL). Mini CRM
chạy đơn tiến trình (xem `app/relay.py` về cùng giả định).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from app.config import get_settings
from fastapi import HTTPException
from jwt import PyJWKClient

_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, PyJWKClient]] = {}
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    """Danh tính người dùng rút từ token ĐÃ XÁC MINH. Không giữ token thô."""

    subject: str
    email: str | None
    display_name: str | None
    roles: frozenset[str]
    groups: frozenset[str]
    expires_at: int


def oidc_configured() -> bool:
    s = get_settings()
    return bool(
        s.oidc_issuer
        and s.oidc_client_id
        and s.oidc_client_secret.get_secret_value()
        and s.oidc_redirect_uri
        and s.session_secret.get_secret_value()
    )


def _public_origin() -> str:
    p = urlparse(get_settings().oidc_issuer)
    return f"{p.scheme}://{p.netloc}"


def _internal_origin() -> str | None:
    base = get_settings().oidc_internal_base_url
    if not base:
        return None
    p = urlparse(base)
    return f"{p.scheme}://{p.netloc}"


def _discovery_url() -> str:
    s = get_settings()
    base = (s.oidc_internal_base_url or s.oidc_issuer).rstrip("/")
    return f"{base}/.well-known/openid-configuration"


def get_discovery() -> dict[str, Any]:
    url = _discovery_url()
    hit = _discovery_cache.get(url)
    now = time.time()
    if hit and now - hit[0] < _DISCOVERY_TTL_SECONDS:
        return hit[1]
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
        resp.raise_for_status()
        doc = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Không lấy được OIDC discovery document từ provider.",
                "error_code": "OIDC_DISCOVERY_UNAVAILABLE",
            },
        ) from exc
    _discovery_cache[url] = (now, doc)
    return doc


def reset_caches() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()


def _to_internal(url: str) -> str:
    internal = _internal_origin()
    if not internal:
        return url
    public = _public_origin()
    if url.startswith(public):
        return internal + url[len(public):]
    return url


def authorization_endpoint() -> str:  # FRONT-CHANNEL
    return get_discovery()["authorization_endpoint"]


def token_endpoint() -> str:  # BACK-CHANNEL
    return _to_internal(get_discovery()["token_endpoint"])


def jwks_uri() -> str:  # BACK-CHANNEL
    return _to_internal(get_discovery()["jwks_uri"])


def end_session_endpoint() -> str | None:  # FRONT-CHANNEL
    return get_discovery().get("end_session_endpoint")


def revocation_endpoint() -> str | None:  # BACK-CHANNEL
    endpoint = get_discovery().get("revocation_endpoint")
    return _to_internal(endpoint) if endpoint else None


def expected_issuer() -> str:
    return get_settings().oidc_issuer.rstrip("/")


# --- PKCE ---------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_pkce_pair() -> tuple[str, str]:
    """`(verifier, challenge_S256)`. Verifier 43–128 ký tự theo RFC 7636."""
    verifier = _b64url(_secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(*, state: str, nonce: str, code_challenge: str) -> str:
    s = get_settings()
    return f"{authorization_endpoint()}?" + urlencode(
        {
            "client_id": s.oidc_client_id,
            "response_type": "code",
            "redirect_uri": s.oidc_redirect_uri,
            "response_mode": "query",
            "scope": s.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )


def build_logout_url(*, id_token_hint: str | None = None) -> str | None:
    s = get_settings()
    end = end_session_endpoint()
    if not end:
        return None
    params: dict[str, str] = {}
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    if s.oidc_post_logout_redirect_uri:
        params["post_logout_redirect_uri"] = s.oidc_post_logout_redirect_uri
        params["client_id"] = s.oidc_client_id
    return f"{end}?{urlencode(params)}" if params else end


async def exchange_code(*, code: str, code_verifier: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=s.sync_timeout_seconds) as client:
        resp = await client.post(
            token_endpoint(),
            data={
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret.get_secret_value(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.oidc_redirect_uri,
                "code_verifier": code_verifier,
                # KHÔNG gửi "scope" ở đây: RFC 6749 §4.1.3. Xem giải thích ở Product oidc.py.
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        # Log body giúp debug (Keycloak trả error/error_description trong JSON).
        import logging
        logging.getLogger(__name__).warning(
            "oidc.code_exchange.failed status=%s body=%s", resp.status_code, resp.text[:500]
        )
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Đổi authorization code lấy token thất bại.",
                "error_code": "OIDC_CODE_EXCHANGE_FAILED",
            },
        )
    return resp.json()


async def refresh_tokens(refresh_token: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=s.sync_timeout_seconds) as client:
        resp = await client.post(
            token_endpoint(),
            data={
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret.get_secret_value(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": s.oidc_scopes,
            },
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên đã hết hạn.", "error_code": "SESSION_EXPIRED"},
        )
    return resp.json()


async def revoke_token(token: str, *, token_type_hint: str = "refresh_token") -> bool:
    if not token:
        return False
    try:
        endpoint = revocation_endpoint()
        if not endpoint:
            logger.warning("oidc.token_revocation.unavailable reason=no_endpoint")
            return False
        s = get_settings()
        async with httpx.AsyncClient(timeout=s.sync_timeout_seconds) as client:
            response = await client.post(
                endpoint,
                data={
                    "client_id": s.oidc_client_id,
                    "client_secret": s.oidc_client_secret.get_secret_value(),
                    "token": token,
                    "token_type_hint": token_type_hint,
                },
            )
        if response.status_code in (200, 204):
            return True
        logger.warning("oidc.token_revocation.failed status=%s", response.status_code)
        return False
    except Exception as exc:
        logger.warning("oidc.token_revocation.failed error=%s", type(exc).__name__)
        return False


def _jwk_client() -> PyJWKClient:
    uri = jwks_uri()
    hit = _jwks_cache.get(uri)
    now = time.time()
    if hit and now - hit[0] < _JWKS_TTL_SECONDS:
        return hit[1]
    client = PyJWKClient(uri, cache_keys=True)
    _jwks_cache[uri] = (now, client)
    return client


def _collect_roles(claims: dict[str, Any]) -> frozenset[str]:
    """Gộp roles từ CẢ HAI vị trí: top-level `roles` (mapper tuỳ biến của realm
    `p100`) VÀ `realm_access.roles` (mặc định Keycloak)."""
    top = claims.get("roles") or []
    realm = (claims.get("realm_access") or {}).get("roles") or []
    return frozenset([*top, *realm])


def verify_token(token: str, *, public_key: Any | None = None) -> OidcIdentity:
    """Xác minh chữ ký + `iss`/`aud`/`exp`. `public_key` chỉ dùng cho test.

    KHÔNG bao giờ tắt signature/issuer/exp verification. `OIDC_AUDIENCE` rỗng ⇒
    dùng `oidc_client_id` làm audience — `oidc_configured()` đã đòi `oidc_client_id`
    khác rỗng, nên audience LUÔN được kiểm khi đường này bật."""
    s = get_settings()
    key = public_key if public_key is not None else _jwk_client().get_signing_key_from_jwt(token).key
    audience = s.oidc_audience or s.oidc_client_id
    algorithms = [a.strip() for a in s.oidc_allowed_algorithms.split(",") if a.strip()]

    decode_kwargs: dict[str, Any] = {
        "key": key,
        "algorithms": algorithms,
        "issuer": expected_issuer(),
        "options": {
            "require": ["exp", "iss", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iss": True,
            "verify_aud": bool(audience),
        },
        "leeway": s.oidc_clock_skew_seconds,
    }
    if audience:
        decode_kwargs["audience"] = audience

    try:
        claims = jwt.decode(token, **decode_kwargs)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"message": "Token đã hết hạn.", "error_code": "TOKEN_EXPIRED"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={"message": "Token không hợp lệ.", "error_code": "INVALID_TOKEN"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    return OidcIdentity(
        subject=str(claims["sub"]),
        email=claims.get("email") or claims.get("preferred_username"),
        display_name=claims.get("name") or claims.get("preferred_username"),
        roles=_collect_roles(claims),
        groups=frozenset(claims.get("groups") or []),
        expires_at=int(claims["exp"]),
    )
