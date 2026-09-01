"""PR-2/D38's auth-discovery gate: `DashboardPrincipal.subject`/`.is_ceo`,
`require_ceo()`, and `issue_session()`'s new `is_ceo` claim.

Same no-live-Keycloak-needed style as `tests/auth/test_oidc_keycloak.py` (RSA
keypair signs test JWTs, `oidc.verify_token(..., public_key=...)` bypasses
JWKS fetch). The point of this whole gate: `"CRM.CEO" in identity.roles` is a
real, verifiable, non-spoofable signal that already existed one call earlier
than `DashboardPrincipal` and was previously discarded — these tests prove it
now survives, and only through the two OIDC-backed paths.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.config import get_settings
from src.services import dashboard_auth, oidc

PUBLIC = "http://localhost:9090/realms/p100"
INTERNAL = "http://keycloak:8080/realms/p100"
CLIENT = "absorbiq-client"

DISCOVERY = {
    "issuer": PUBLIC,
    "authorization_endpoint": f"{PUBLIC}/protocol/openid-connect/auth",
    "token_endpoint": f"{PUBLIC}/protocol/openid-connect/token",
    "jwks_uri": f"{PUBLIC}/protocol/openid-connect/certs",
    "end_session_endpoint": f"{PUBLIC}/protocol/openid-connect/logout",
}


@pytest.fixture
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def oidc_env(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", PUBLIC)
    monkeypatch.setenv("OIDC_INTERNAL_BASE_URL", INTERNAL)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "local-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8000/api/v1/auth/callback")
    monkeypatch.setenv("SESSION_SECRET", "x" * 40)
    # `read_session_verified()` needs Redis for revocation checks; the real
    # `.env`'s `REDIS_URL` uses the Docker-internal hostname `redis`, not
    # resolvable from this test process running on the host — point at the
    # same container's published port instead (docker-compose.yml:61-67).
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    # `_revocation_redis` is a module-level cached client tied to the event
    # loop that created it; pytest-asyncio gives each test its own loop
    # (function-scoped), so a client cached by an earlier test breaks here
    # with "attached to a different loop" — force a fresh one per test.
    monkeypatch.setattr(oidc, "_revocation_redis", None)
    get_settings.cache_clear()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "get_discovery", lambda: DISCOVERY)
    yield
    get_settings.cache_clear()
    oidc.reset_caches()


@pytest.fixture
def jwks(oidc_env, keypair, monkeypatch):
    """`authenticate_dashboard()`'s direct-JWT branch calls `oidc.verify_token(token)`
    with no `public_key` kwarg (that param is test-only) — route it through
    the self-signed test key instead of a real JWKS fetch."""
    _, public = keypair

    class _FakeSigningKey:
        key = public

    class _FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey()

    monkeypatch.setattr(oidc, "_jwk_client", lambda: _FakeJwkClient())
    return keypair


def token_for(private, *, roles, subject="user-1"):
    now = int(time.time())
    claims = {
        "iss": PUBLIC,
        "aud": CLIENT,
        "sub": subject,
        "preferred_username": f"{subject}@example.com",
        "realm_access": {"roles": list(roles)},
        "iat": now,
        "exp": now + 600,
    }
    return jwt.encode(claims, private, algorithm="RS256")


# --- Direct-JWT path (authenticate_dashboard's bearer-token branch) ---------


async def test_verified_crm_ceo_role_yields_subject_and_is_ceo_true(jwks):
    private, _ = jwks
    token = token_for(private, roles=["CRM.CEO"], subject="ceo-subject-1")
    principal = await dashboard_auth.authenticate_dashboard(f"Bearer {token}")
    assert principal.role == "admin"
    assert principal.subject == "ceo-subject-1"
    assert principal.is_ceo is True
    assert principal.oidc_roles == frozenset({"CRM.CEO"})


async def test_generic_admin_role_without_raw_ceo_yields_is_ceo_false(jwks):
    private, _ = jwks
    token = token_for(private, roles=["CRM.Admin"], subject="admin-subject-1")
    principal = await dashboard_auth.authenticate_dashboard(f"Bearer {token}")
    assert principal.role == "admin"
    assert principal.subject == "admin-subject-1"
    assert principal.is_ceo is False, "CRM.Admin collapses to the same 3-tier 'admin' role but is NOT the CEO"


async def test_ceo_role_survives_canonicalization_to_admin(jwks):
    """The 3-tier `role` collapses CRM.CEO/CRM.Admin identically to 'admin' —
    `is_ceo` is the only surviving signal that distinguishes them."""
    private, _ = jwks
    ceo_principal = await dashboard_auth.authenticate_dashboard(
        f"Bearer {token_for(private, roles=['CRM.CEO'], subject='ceo-2')}"
    )
    admin_principal = await dashboard_auth.authenticate_dashboard(
        f"Bearer {token_for(private, roles=['CRM.Admin'], subject='admin-2')}"
    )
    assert ceo_principal.role == admin_principal.role == "admin"
    assert ceo_principal.is_ceo is True
    assert admin_principal.is_ceo is False


# --- Session-cookie path (issue_session -> authenticate_dashboard) ---------


async def test_session_cookie_carries_is_ceo_through_issue_session(oidc_env, keypair):
    private, public = keypair
    identity = oidc.verify_token(token_for(private, roles=["CRM.CEO"], subject="ceo-3"), public_key=public)
    session_token = oidc.issue_session(identity, role="admin", scope="ALL")

    principal = await dashboard_auth.authenticate_dashboard(None, session_token)
    assert principal.subject == "ceo-3"
    assert principal.is_ceo is True
    assert principal.oidc_roles == frozenset({"CRM.CEO"})


async def test_session_cookie_without_ceo_role_carries_is_ceo_false(oidc_env, keypair):
    private, public = keypair
    identity = oidc.verify_token(token_for(private, roles=["CRM.SALES"], subject="sales-1"), public_key=public)
    session_token = oidc.issue_session(identity, role="pipeline_operator", scope=[])

    principal = await dashboard_auth.authenticate_dashboard(None, session_token)
    assert principal.subject == "sales-1"
    assert principal.is_ceo is False
    assert principal.oidc_roles == frozenset({"CRM.SALES"})


async def test_sessions_issued_before_is_ceo_shipped_still_decode(oidc_env, keypair):
    """`is_ceo` is read via `.get(..., False)`, never `options['require']` —
    a session token minted before this claim existed must not be rejected."""
    payload = {
        "sub": "legacy-subject",
        "email": None,
        "name": None,
        "role": "admin",
        "scope": "ALL",
        "iss": "absorbiq",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "jti": "legacy-jti",
    }
    legacy_token = jwt.encode(payload, get_settings().session_secret.get_secret_value(), algorithm="HS256")
    principal = await dashboard_auth.authenticate_dashboard(None, legacy_token)
    assert principal.is_ceo is False
    assert principal.subject == "legacy-subject"


# --- Static token / dev bypass: is_ceo structurally unavailable -------------


async def test_static_admin_token_yields_is_ceo_false(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", "static-admin-token")
    get_settings.cache_clear()
    try:
        principal = await dashboard_auth.authenticate_dashboard("Bearer static-admin-token")
        assert principal.role == "admin"
        assert principal.subject is None
        assert principal.is_ceo is False
    finally:
        get_settings.cache_clear()


async def test_dev_bypass_yields_is_ceo_false(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    get_settings.cache_clear()
    try:
        principal = await dashboard_auth.authenticate_dashboard(None)
        assert principal.role == "admin"
        assert principal.subject is None
        assert principal.is_ceo is False
    finally:
        get_settings.cache_clear()


# --- require_ceo() dependency ------------------------------------------------


async def test_require_ceo_rejects_non_ceo_admin(jwks):
    private, _ = jwks
    token = token_for(private, roles=["CRM.Admin"], subject="admin-3")
    dependency = dashboard_auth.require_ceo()
    with pytest.raises(HTTPException) as exc:
        await dependency(authorization=f"Bearer {token}", absorbiq_session=None)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "CEO_APPROVAL_REQUIRED"


async def test_require_ceo_rejects_static_token_and_dev_bypass(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", "static-admin-token-2")
    get_settings.cache_clear()
    try:
        dependency = dashboard_auth.require_ceo()
        with pytest.raises(HTTPException) as exc:
            await dependency(authorization="Bearer static-admin-token-2", absorbiq_session=None)
        assert exc.value.status_code == 403
        assert exc.value.detail["error_code"] == "CEO_APPROVAL_REQUIRED"
    finally:
        get_settings.cache_clear()


async def test_require_ceo_accepts_verified_crm_ceo(jwks):
    private, _ = jwks
    token = token_for(private, roles=["CRM.CEO"], subject="ceo-4")
    dependency = dashboard_auth.require_ceo()
    principal = await dependency(authorization=f"Bearer {token}", absorbiq_session=None)
    assert principal.is_ceo is True
    assert principal.subject == "ceo-4"


pytestmark = pytest.mark.asyncio
