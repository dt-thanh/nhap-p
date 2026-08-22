"""SSO Entra phía Product/AbsorbIQ — kiểm OFFLINE, không cần tenant thật (CP5).

Trọng tâm khác với bộ kiểm ở Mini CRM: ở đây thứ dễ sai nhất là RANH GIỚI
AUDIENCE. Nếu Product chấp nhận một token phát cho Mini CRM, thì hai app
registration tách bạch trở thành trang trí — bất kỳ ai lấy được token của Mini
CRM cũng gọi được API của Product. Test `test_token_for_minicrm_audience_is_rejected`
canh đúng ranh giới đó.
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.config import get_settings
from src.services import entra_auth

TENANT = "11111111-1111-1111-1111-111111111111"
PRODUCT_CLIENT = "33333333-3333-3333-3333-333333333333"
MINICRM_CLIENT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_CLIENT_ID", PRODUCT_CLIENT)
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "product-test-secret")
    monkeypatch.setenv("ENTRA_REDIRECT_URI", "http://localhost:8000/api/v1/auth/callback")
    monkeypatch.setenv("SESSION_SECRET", "product-unit-test-session-secret-value")
    monkeypatch.setenv(
        "ENTRA_ROLE_MAP",
        json.dumps({"AbsorbIQ.Admin": "admin", "AbsorbIQ.Viewer": "business_viewer"}),
    )
    monkeypatch.setenv("ENTRA_PROJECT_SCOPE", json.dumps({"AbsorbIQ.Admin": "ALL"}))
    get_settings.cache_clear()
    entra_auth.reset_jwks_cache()
    yield
    get_settings.cache_clear()


def token_for(private, audience=PRODUCT_CLIENT, **over):
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": audience,
        "sub": "sub-1",
        "oid": "shared-user-object-id",
        "preferred_username": "an.nguyen@example.com",
        "name": "An Nguyen",
        "roles": ["AbsorbIQ.Admin"],
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }
    claims.update(over)
    return jwt.encode(claims, private, algorithm="RS256")


def test_valid_product_token_accepted(keypair):
    private, public = keypair
    identity = entra_auth.verify_token(token_for(private), public_key=public)
    assert identity.subject == "shared-user-object-id"
    assert entra_auth.resolve_role(identity) == "admin"
    assert entra_auth.resolve_scope(identity) == "ALL"


def test_token_for_minicrm_audience_is_rejected(keypair):
    """Đây là RANH GIỚI của SSO. Cùng người dùng, cùng tenant, cùng khoá ký —
    nhưng token phát cho Mini CRM KHÔNG được dùng ở Product. SSO nghĩa là không
    phải đăng nhập lại, KHÔNG phải dùng chung một token cho mọi dịch vụ."""
    private, public = keypair
    with pytest.raises(HTTPException) as exc:
        entra_auth.verify_token(token_for(private, audience=MINICRM_CLIENT), public_key=public)
    assert exc.value.status_code == 401


def test_expired_token_rejected(keypair):
    private, public = keypair
    now = int(time.time())
    with pytest.raises(HTTPException) as exc:
        entra_auth.verify_token(
            token_for(private, exp=now - 60, nbf=now - 3600, iat=now - 3600), public_key=public
        )
    assert exc.value.detail["error_code"] == "TOKEN_EXPIRED"


def test_forged_signature_rejected(keypair):
    _, public = keypair
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException):
        entra_auth.verify_token(token_for(attacker), public_key=public)


def test_unmapped_role_is_403_not_default(keypair):
    private, public = keypair
    identity = entra_auth.verify_token(token_for(private, roles=["Unrelated.Role"]), public_key=public)
    with pytest.raises(HTTPException) as exc:
        entra_auth.resolve_role(identity)
    assert exc.value.status_code == 403


def test_session_cookie_roundtrip(keypair):
    private, public = keypair
    identity = entra_auth.verify_token(token_for(private), public_key=public)
    token = entra_auth.issue_session(identity, role="admin", scope="ALL")
    claims = entra_auth.read_session(token)
    assert claims["role"] == "admin" and claims["iss"] == "absorbiq"


def test_minicrm_session_cookie_is_not_valid_here(keypair):
    """Cookie phiên của Mini CRM (`iss: minicrm`) không được Product chấp nhận,
    kể cả khi hai bên vô tình dùng chung `SESSION_SECRET`."""
    private, public = keypair
    identity = entra_auth.verify_token(token_for(private), public_key=public)
    foreign = jwt.encode(
        {
            "sub": identity.subject,
            "role": "admin",
            "scope": "ALL",
            "iss": "minicrm",
            "exp": int(time.time()) + 600,
        },
        get_settings().session_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        entra_auth.read_session(foreign)
    assert exc.value.detail["error_code"] == "INVALID_SESSION"


def test_authorize_url_never_leaks_secret():
    _, challenge = entra_auth.new_pkce_pair()
    url = entra_auth.build_authorize_url(state="s", nonce="n", code_challenge=challenge)
    assert "product-test-secret" not in url
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
async def test_refresh_tokens_uses_refresh_grant(monkeypatch):
    """`refresh_tokens` phải POST tới token endpoint bằng grant_type=refresh_token,
    kèm client_id/secret của Product — đây là mảnh khiến `/auth/refresh` gia hạn
    được phiên thay vì đá người dùng ra /login mỗi lần access token hết hạn."""
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"id_token": "new-id", "refresh_token": "new-rt", "access_token": "new-at"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data
            return _Resp()

    monkeypatch.setattr(entra_auth.httpx, "AsyncClient", _Client)
    tokens = await entra_auth.refresh_tokens("old-refresh-token")

    assert tokens["id_token"] == "new-id"
    assert captured["data"]["grant_type"] == "refresh_token"
    assert captured["data"]["refresh_token"] == "old-refresh-token"
    assert captured["data"]["client_id"] == PRODUCT_CLIENT


@pytest.mark.asyncio
async def test_refresh_tokens_maps_failure_to_session_expired(monkeypatch):
    """Entra từ chối refresh (refresh token hết hạn/thu hồi) ⇒ 401 SESSION_EXPIRED,
    KHÔNG rò thân lỗi thô của Microsoft ra client."""

    class _Resp:
        status_code = 400

        def json(self):
            return {"error": "invalid_grant"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return _Resp()

    monkeypatch.setattr(entra_auth.httpx, "AsyncClient", _Client)
    with pytest.raises(HTTPException) as exc:
        await entra_auth.refresh_tokens("revoked-token")
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "SESSION_EXPIRED"
