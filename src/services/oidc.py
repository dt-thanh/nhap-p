"""Generic OpenID Connect provider cho Product/AbsorbIQ (Keycloak local, hoặc bất
kỳ IdP OIDC nào tuân thủ Discovery).

VÌ SAO CÓ FILE NÀY. `src/services/entra_auth.py` dựng mọi endpoint từ `_authority()`
— một hình dạng URL RIÊNG của Microsoft (`/oauth2/v2.0/authorize`, `/discovery/
v2.0/keys`, hai dạng issuer `sts.windows.net`/`v2.0`...). Keycloak có hình dạng
khác (`/protocol/openid-connect/auth`, `/protocol/openid-connect/certs`, một issuer
duy nhất theo realm). Thay vì rẽ nhánh Microsoft-vs-Keycloak rải rác khắp nơi,
module này nói chuyện với BẤT KỲ provider nào qua Discovery document, và
`entra_auth.py` ỦY QUYỀN sang đây khi `OIDC_*` được cấu hình (xem
`entra_auth.oidc_active()`). Không có file này thì bật Keycloak = viết lại auth.

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

Cùng một `EntraIdentity` được trả về (không đặt tên lại kiểu dữ liệu) để
`resolve_role`/`issue_session`/`dashboard_auth` phía sau không phải đổi một dòng.
ROLES: Keycloak để realm roles ở `realm_access.roles` (không phải top-level
`roles` như Entra), nên `verify_token` gộp CẢ HAI nguồn vào `identity.roles`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
import jwt
from fastapi import HTTPException
from jwt import PyJWKClient

from src.config import get_settings
from src.logging_config import get_logger

log = get_logger("src.services.oidc")

# Bản dữ liệu danh tính DÙNG CHUNG với entra_auth — import muộn để tránh vòng phụ
# thuộc lúc entra_auth ủy quyền ngược lại sang đây.
from src.services.entra_auth import EntraIdentity  # noqa: E402

_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600
# {internal_base: (fetched_at, discovery_doc)}
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, PyJWKClient]] = {}


def oidc_configured() -> bool:
    """Đường OIDC generic BẬT khi có đủ issuer + client + secret + redirect +
    session_secret. Thiếu bất kỳ cái nào ⇒ đường này KHÔNG tồn tại (đóng, không
    mở) và `entra_auth` rơi về nhánh Entra/token tĩnh như cũ."""
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
    """Dùng trong test và sau khi xoay khoá / đổi provider."""
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


def expected_issuer() -> str:
    """Canonical issuer để verify `iss`. Discovery.issuer PHẢI khớp OIDC_ISSUER —
    nếu lệch, cấu hình sai (thường frontendUrl chưa đặt) và ta fail thay vì đoán."""
    return get_settings().oidc_issuer.rstrip("/")


# --- PKCE (dùng lại nguyên helper của entra_auth) ---------------------------

from src.services.entra_auth import new_pkce_pair  # noqa: E402,F401 (re-export)


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


def build_logout_url() -> str | None:
    s = get_settings()
    end = end_session_endpoint()
    if not end:
        return None
    params: dict[str, str] = {}
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
                "scope": s.oidc_scopes,
            },
        )
    if resp.status_code != 200:
        log.warning("oidc.code_exchange.failed", status=resp.status_code)
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
    """Gộp roles từ CẢ HAI vị trí: top-level `roles` (Entra/token tùy biến) VÀ
    `realm_access.roles` (Keycloak realm roles). Đây là mảnh khiến `resolve_role`
    phía sau nhìn thấy vai trò Keycloak mà không cần biết nó là Keycloak."""
    top = claims.get("roles") or []
    realm = (claims.get("realm_access") or {}).get("roles") or []
    return frozenset([*top, *realm])


def verify_token(token: str, *, public_key: Any | None = None) -> EntraIdentity:
    """Xác minh chữ ký + `iss`/`aud`/`exp`. `public_key` chỉ dùng cho test.

    KHÔNG bao giờ tắt signature/issuer/exp verification. Với Keycloak, audience
    của ID token là client_id; nếu provider phát access token có `aud` khác, đặt
    `OIDC_AUDIENCE`. Cho phép `verify_aud=False` CHỈ khi OIDC_AUDIENCE rỗng và
    provider không nhét client vào aud (một số cấu hình Keycloak) — nhưng iss +
    signature + exp vẫn bắt buộc."""
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

    return EntraIdentity(
        subject=str(claims["sub"]),
        email=claims.get("email") or claims.get("preferred_username"),
        display_name=claims.get("name") or claims.get("preferred_username"),
        roles=_collect_roles(claims),
        groups=frozenset(claims.get("groups") or []),
        expires_at=int(claims["exp"]),
    )
