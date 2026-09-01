"""OpenID Connect identity provider cho Product/AbsorbIQ backend — Keycloak-only.

Backend nói chuyện với BẤT KỲ provider nào tuân thủ OIDC Discovery (thực tế: chỉ
Keycloak, xem `docker/keycloak/p100-realm.json`, realm `p100`). Đây là module DUY
NHẤT chịu trách nhiệm xác thực người dùng — không còn một nhánh provider nào khác
để rẽ tới (Microsoft Entra ID đã bị gỡ khỏi runtime, xem lịch sử migration trong
`pipeline_status.md` nếu cần dựng lại một nhà cung cấp khác trong tương lai).

FRONT-CHANNEL vs BACK-CHANNEL (điểm cốt tử của Docker local).
- FRONT-CHANNEL (trình duyệt phải mở được): `authorization_endpoint`,
  `end_session_endpoint`, và `issuer`. Trình duyệt KHÔNG resolve được hostname
  Docker `keycloak`, nên các URL này PHẢI mang host công khai `localhost:9090`.
- BACK-CHANNEL (container gọi thẳng IdP): `token_endpoint`, `jwks_uri`. Container
  KHÔNG nên gọi `localhost:9090` (localhost trong container là chính nó), nên các
  URL này PHẢI mang host nội bộ Docker `keycloak:8080`.

Cách giải. Realm Keycloak đặt `frontendUrl = http://localhost:9090` (xem
`docker/keycloak/p100-realm.json`) → Discovery + `iss` LUÔN mang host công khai,
ổn định, không phụ thuộc host nào gọi token endpoint. Backend fetch Discovery qua
`OIDC_INTERNAL_BASE_URL` (`http://keycloak:8080/realms/p100`); các endpoint
BACK-CHANNEL được viết lại từ origin công khai → origin nội bộ. Endpoint
FRONT-CHANNEL giữ nguyên host công khai. Issuer verification LUÔN kiểm canonical
`OIDC_ISSUER` — KHÔNG nới lỏng để lách Docker.

SSO GIỮA MINI CRM VÀ ABSORBIQ. Cả hai app đăng ký trong CÙNG một realm Keycloak
(`p100`), mỗi app một client Keycloak riêng. Khi người dùng đã đăng nhập ở một
app, Keycloak giữ một phiên SSO trong trình duyệt; app còn lại bắt đầu vòng OIDC
của RIÊNG nó và Keycloak trả `code` NGAY, không hiện màn hình đăng nhập lại.
Không query string mang token, không localStorage dùng chung. `OIDC_AUDIENCE`/
`client_id` của mỗi bên khác nhau, nên một token phát cho app kia bị AbsorbIQ từ
chối — ĐÚNG như mong muốn: audience tồn tại chính để chặn việc dùng lại token
sang một dịch vụ khác.

ROLES: `docker/keycloak/p100-realm.json` gắn một protocol mapper phát realm roles
ra claim `roles` (không phải `realm_access.roles` mặc định của Keycloak) trên cả
hai client — nhưng `verify_token` vẫn gộp thêm `realm_access.roles` để không phụ
thuộc ngầm vào cấu hình mapper của một realm cụ thể.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

import httpx
import jwt
import redis.asyncio as redis_asyncio
from fastapi import HTTPException, Response
from jwt import PyJWKClient

from src.config import get_settings
from src.logging_config import get_logger

log = get_logger("src.services.oidc")

SESSION_COOKIE = "absorbiq_session"

DashboardRole = Literal["business_viewer", "pipeline_operator", "admin"]
_VALID_ROLES = {"business_viewer", "pipeline_operator", "admin"}
_ROLE_LEVEL = {"business_viewer": 0, "pipeline_operator": 1, "admin": 2}

_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600
# {internal_base: (fetched_at, discovery_doc)}
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, PyJWKClient]] = {}
_revocation_redis: redis_asyncio.Redis | None = None
_SESSION_REVOCATION_PREFIX = "absorbiq:session:revoked:"
_SHARED_BLACKLIST_PREFIX = "p100:jwt:blacklist:"


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    subject: str
    email: str | None
    display_name: str | None
    roles: frozenset[str]
    groups: frozenset[str]
    expires_at: int


def oidc_configured() -> bool:
    """Đường đăng nhập người dùng BẬT khi có đủ issuer + client + secret +
    redirect + session_secret. Thiếu bất kỳ cái nào ⇒ 503 rõ ràng ở route auth,
    KHÔNG có nhà cung cấp nào khác để rơi về."""
    s = get_settings()
    return bool(
        s.oidc_issuer
        and s.oidc_client_id
        and s.oidc_client_secret.get_secret_value()
        and s.oidc_redirect_uri
        and s.session_secret.get_secret_value()
    )


# --- Discovery --------------------------------------------------------------


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
    """Ưu tiên fetch qua base NỘI BỘ (Docker DNS) để không ép browser-hostname
    lên server-to-server. Không có internal base thì dùng issuer công khai."""
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
    except Exception as exc:  # noqa: BLE001 — mọi lỗi mạng gộp thành 503 rõ ràng
        log.warning("oidc.discovery.failed", url=url, error=type(exc).__name__)
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
    """Dùng trong test và sau khi xoay khoá."""
    _discovery_cache.clear()
    _jwks_cache.clear()


def _to_internal(url: str) -> str:
    """Viết lại một endpoint BACK-CHANNEL từ origin công khai → origin nội bộ.
    Không cấu hình internal base ⇒ giữ nguyên (mọi thứ dùng chung một host)."""
    internal = _internal_origin()
    if not internal:
        return url
    public = _public_origin()
    if url.startswith(public):
        return internal + url[len(public):]
    # Discovery đã trả host nội bộ sẵn (frontendUrl không đặt) → giữ nguyên.
    return url


# --- Endpoints (front-channel giữ công khai, back-channel về nội bộ) --------


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
    """Canonical issuer để verify `iss`. Discovery.issuer PHẢI khớp OIDC_ISSUER —
    nếu lệch, cấu hình sai (thường frontendUrl chưa đặt) và ta fail thay vì đoán."""
    return get_settings().oidc_issuer.rstrip("/")


# --- PKCE ---------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(_secrets.token_bytes(64))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


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
        # Keycloak yêu cầu client_id kèm post_logout_redirect_uri (nếu không có
        # id_token_hint) để chấp nhận URL chuyển hướng sau đăng xuất.
        params["client_id"] = s.oidc_client_id
    return f"{end}?{urlencode(params)}" if params else end


# --- Đổi code / refresh -----------------------------------------------------


async def exchange_code(*, code: str, code_verifier: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_endpoint(),
            data={
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret.get_secret_value(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.oidc_redirect_uri,
                "code_verifier": code_verifier,
                # KHÔNG gửi "scope" ở đây: RFC 6749 §4.1.3 — grant_type=authorization_code
                # không nhận scope; scope đã bị ghim ở bước /authorize. Gửi kèm sẽ
                # bị Keycloak reject (400 invalid_scope) khi scope chứa optional
                # scope (vd offline_access) mà client chưa được cấp trong realm.
            },
        )
    if resp.status_code != 200:
        # Log body để debug: Keycloak trả JSON {"error":"...", "error_description":"..."}
        body = resp.text[:500]
        log.warning("oidc.code_exchange.failed", status=resp.status_code, body=body)
        raise HTTPException(
            status_code=401,
            detail={"message": "Đổi code lấy token thất bại.", "error_code": "OIDC_CODE_EXCHANGE_FAILED"},
        )
    return resp.json()


async def refresh_tokens(refresh_token: str) -> dict[str, Any]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
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
        log.warning("oidc.token_refresh.failed", status=resp.status_code)
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên đã hết hạn.", "error_code": "SESSION_EXPIRED"},
        )
    return resp.json()


async def revoke_token(token: str, *, token_type_hint: str = "refresh_token") -> bool:
    """Revoke a provider token without exposing provider errors to logout."""
    try:
        endpoint = revocation_endpoint()
        if not endpoint or not token:
            return False
        s = get_settings()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                endpoint,
                data={
                    "client_id": s.oidc_client_id,
                    "client_secret": s.oidc_client_secret.get_secret_value(),
                    "token": token,
                    "token_type_hint": token_type_hint,
                },
            )
        if resp.status_code not in (200, 204):
            log.warning("oidc.token_revoke.failed", status=resp.status_code)
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — local logout must still complete
        log.warning("oidc.token_revoke.unavailable", error=type(exc).__name__)
        return False


# --- Xác minh token ---------------------------------------------------------


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
    `p100`) VÀ `realm_access.roles` (mặc định Keycloak) — không phụ thuộc ngầm
    vào việc realm có protocol mapper tuỳ biến hay không."""
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
        "options": {"require": ["exp", "iss", "sub"], "verify_aud": bool(audience)},
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
        log.warning("oidc.token.rejected")
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


# --- Ánh xạ vai trò + phiên ---------------------------------------------


def _json_setting(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# Vai trò nghiệp vụ chuẩn (CEO/ADVISOR/SALES), dùng chung với Mini CRM — CỐ ĐỊNH
# trong code, không cấu hình qua OIDC_ROLE_MAP. `src/config.py::_reject_conflicting_canonical_role_map`
# từ chối khởi động nếu OIDC_ROLE_MAP cố định nghĩa lại một trong ba khoá này
# thành giá trị khác — nên phép `setdefault` dưới đây không bao giờ che giấu
# một xung đột thật.
CANONICAL_APP_ROLES: dict[str, DashboardRole] = {
    "CRM.CEO": "admin",
    "CRM.Admin": "admin",
    "CRM.ADVISOR": "business_viewer",
    "CRM.Viewer": "business_viewer",
    "CRM.SALES": "pipeline_operator",
    "CRM.Operator": "pipeline_operator",
}


def resolve_role(identity: OidcIdentity) -> DashboardRole:
    """Fail-closed: không khớp role/group nào ⇒ 403, KHÔNG có vai trò mặc định."""
    mapping = {
        str(k): str(v)
        for k, v in _json_setting(get_settings().oidc_role_map.get_secret_value()).items()
        if str(v) in _VALID_ROLES
    }
    for canonical_role, internal_role in CANONICAL_APP_ROLES.items():
        mapping.setdefault(canonical_role, internal_role)
    matched = [mapping[c] for c in (*identity.roles, *identity.groups) if c in mapping]
    # Realm roles có TÊN đúng bằng ba vai trò nội bộ (admin/pipeline_operator/
    # business_viewer) được chấp nhận như chính nó KỂ CẢ khi OIDC_ROLE_MAP chưa
    # liệt kê. Người tự đăng ký nhận `business_viewer` qua default role của realm
    # ⇒ KHÔNG bao giờ admin (không có role tên "admin" nào được gán mặc định).
    if not matched:
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


def resolve_scope(identity: OidcIdentity) -> list[str] | str:
    mapping = _json_setting(get_settings().oidc_project_scope.get_secret_value())
    for claim in (*identity.roles, *identity.groups):
        if claim in mapping:
            value = mapping[claim]
            return "ALL" if value == "ALL" else list(value)
    return []


def issue_session(
    identity: OidcIdentity,
    *,
    role: DashboardRole,
    scope: list[str] | str,
    refresh_token: str | None = None,
    id_token_hint: str | None = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": identity.subject,
        "email": identity.email,
        "name": identity.display_name,
        "role": role,
        "scope": scope,
        # PR-2 (D38 auth-discovery gate): `identity.roles` (raw, uncollapsed
        # realm roles) is only available HERE, at login/refresh time --
        # `resolve_role()` has already thrown it away by the time a session
        # cookie is read back. Persisting the CEO bit now is the only way it
        # survives into `DashboardPrincipal` on the session-cookie path.
        "is_ceo": "CRM.CEO" in identity.roles,
        "oidc_roles": sorted(identity.roles),
        "iss": "absorbiq",
        "iat": now,
        "exp": now + get_settings().session_ttl_seconds,
        "jti": _secrets.token_urlsafe(32),
    }
    if refresh_token:
        payload["rt"] = refresh_token
    if id_token_hint:
        payload["id_token_hint"] = id_token_hint
    return jwt.encode(payload, get_settings().session_secret.get_secret_value(), algorithm="HS256")


def _decode_session(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    options = {"require": ["exp", "sub", "role"], "verify_exp": verify_exp}
    try:
        return jwt.decode(
            token,
            get_settings().session_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer="absorbiq",
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
    """Decode a session token for non-request helpers and compatibility tests."""
    return _decode_session(token)


def _get_revocation_redis() -> redis_asyncio.Redis:
    global _revocation_redis
    if _revocation_redis is None:
        _revocation_redis = redis_asyncio.from_url(get_settings().redis_dsn, decode_responses=True)
    return _revocation_redis


def _revocation_key(jti: str) -> str:
    return f"{_SESSION_REVOCATION_PREFIX}{jti}"


def _shared_blacklist_key(token: str) -> str:
    return f"{_SHARED_BLACKLIST_PREFIX}{hashlib.sha256(token.encode()).hexdigest()}"


async def revoke_shared_token(token: str, *, ttl: int | None = None) -> bool:
    if not token:
        return False
    try:
        lifetime = ttl or get_settings().session_ttl_seconds
        await _get_revocation_redis().set(_shared_blacklist_key(token), "1", ex=max(1, lifetime))
        return True
    except Exception as exc:  # noqa: BLE001 — local cookie clearing must still complete
        log.warning("oidc.shared_revoke.unavailable", error=type(exc).__name__)
        return False


async def revoke_session(token: str) -> bool:
    """Mark the local JWT as revoked until its natural expiry."""
    try:
        claims = _decode_session(token, verify_exp=False)
        jti = str(claims.get("jti") or "")
        if not jti:
            return False
        ttl = max(1, int(claims["exp"]) - int(time.time()))
        redis = _get_revocation_redis()
        await redis.set(_revocation_key(jti), "1", ex=ttl)
        await redis.set(_shared_blacklist_key(token), "1", ex=ttl)
        return True
    except HTTPException:
        return False
    except Exception as exc:  # noqa: BLE001 — cookie clearing must still complete
        log.warning("oidc.session_revoke.unavailable", error=type(exc).__name__)
        return False


async def read_session_verified(token: str, *, verify_exp: bool = True) -> dict[str, Any]:
    """Decode and enforce server-side revocation for an authenticated request."""
    claims = _decode_session(token, verify_exp=verify_exp)
    jti = str(claims.get("jti") or "")
    if not jti:
        return claims
    try:
        redis = _get_revocation_redis()
        revoked = await redis.exists(_revocation_key(jti))
        revoked = bool(revoked or await redis.exists(_shared_blacklist_key(token)))
    except Exception as exc:  # noqa: BLE001 — fail closed if revocation is unavailable
        log.error("oidc.session_revocation.unavailable", error=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Không thể kiểm tra trạng thái phiên.",
                "error_code": "SESSION_REVOCATION_UNAVAILABLE",
            },
        ) from exc
    if revoked:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên đã bị thu hồi.", "error_code": "SESSION_REVOKED"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


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
