"""Kiểm chứng lớp OIDC generic (Keycloak) mà KHÔNG cần Keycloak thật.

Ba thứ dễ sai nhất khi tích hợp Keycloak-trong-Docker được khoá lại ở đây:
1. FRONT-CHANNEL giữ host công khai, BACK-CHANNEL viết lại về host nội bộ.
2. Roles đọc được từ `realm_access.roles` (Keycloak) chứ không chỉ top-level.
3. Issuer verification kiểm canonical issuer công khai — token sai iss bị từ chối.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.config import get_settings
from src.services import entra_auth, oidc

PUBLIC = "http://localhost:9090/realms/p100"
INTERNAL = "http://keycloak:8080/realms/p100"

DISCOVERY = {
    "issuer": PUBLIC,
    "authorization_endpoint": f"{PUBLIC}/protocol/openid-connect/auth",
    "token_endpoint": f"{PUBLIC}/protocol/openid-connect/token",
    "jwks_uri": f"{PUBLIC}/protocol/openid-connect/certs",
    "end_session_endpoint": f"{PUBLIC}/protocol/openid-connect/logout",
}


@pytest.fixture
def oidc_env(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", PUBLIC)
    monkeypatch.setenv("OIDC_INTERNAL_BASE_URL", INTERNAL)
    monkeypatch.setenv("OIDC_CLIENT_ID", "absorbiq-client")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "local-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8000/api/v1/auth/callback")
    monkeypatch.setenv("SESSION_SECRET", "x" * 40)
    get_settings.cache_clear()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "get_discovery", lambda: DISCOVERY)
    yield
    get_settings.cache_clear()
    oidc.reset_caches()


def test_oidc_active_switches_provider(oidc_env):
    assert oidc.oidc_configured() is True
    assert entra_auth.oidc_active() is True
    assert entra_auth.entra_configured() is True  # provider-agnostic


def test_front_channel_keeps_public_host(oidc_env):
    url = entra_auth.build_authorize_url(state="s", nonce="n", code_challenge="c")
    assert url.startswith(f"{PUBLIC}/protocol/openid-connect/auth?")
    assert "keycloak:8080" not in url  # browser không bao giờ thấy host Docker
    assert oidc.end_session_endpoint().startswith(PUBLIC)


def test_back_channel_rewrites_to_internal(oidc_env):
    assert oidc.token_endpoint() == f"{INTERNAL}/protocol/openid-connect/token"
    assert oidc.jwks_uri() == f"{INTERNAL}/protocol/openid-connect/certs"


def _rs256_token(claims, key):
    return jwt.encode(claims, key, algorithm="RS256")


def test_verify_reads_realm_access_roles_and_issuer(oidc_env):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = _rs256_token(
        {
            "sub": "kc-user-1",
            "iss": PUBLIC,
            "aud": "absorbiq-client",
            "exp": now + 300,
            "iat": now,
            "email": "demo@example.com",
            "preferred_username": "demo",
            "realm_access": {"roles": ["admin", "offline_access"]},
        },
        priv,
    )
    identity = entra_auth.verify_token(token, public_key=priv.public_key())
    assert identity.subject == "kc-user-1"
    assert "admin" in identity.roles  # đọc từ realm_access.roles
    # resolve_role chấp nhận realm role trùng tên vai trò nội bộ ở chế độ OIDC
    assert entra_auth.resolve_role(identity) == "admin"


def test_verify_rejects_wrong_issuer(oidc_env):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = _rs256_token(
        {
            "sub": "u", "iss": "http://evil/realms/p100", "aud": "absorbiq-client",
            "exp": now + 300, "iat": now, "realm_access": {"roles": ["admin"]},
        },
        priv,
    )
    with pytest.raises(Exception):
        entra_auth.verify_token(token, public_key=priv.public_key())


def test_self_registered_viewer_is_not_admin(oidc_env):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = _rs256_token(
        {
            "sub": "u2", "iss": PUBLIC, "aud": "absorbiq-client", "exp": now + 300,
            "iat": now, "realm_access": {"roles": ["business_viewer"]},
        },
        priv,
    )
    identity = entra_auth.verify_token(token, public_key=priv.public_key())
    assert entra_auth.resolve_role(identity) == "business_viewer"
