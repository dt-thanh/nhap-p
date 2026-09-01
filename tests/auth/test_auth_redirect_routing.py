"""Regression coverage for the OIDC callback's deployment routing contract."""

from __future__ import annotations

import time
from pathlib import Path

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.api.auth import FLOW_COOKIE
from src.config import Settings, get_settings
from src.main import app
from src.services import oidc

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ORIGIN = "https://api.example.test"
FRONTEND_ORIGIN = "https://frontend.example.test"


@pytest.fixture
def oidc_redirect_env(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://keycloak.example/realms/p100")
    monkeypatch.setenv("OIDC_CLIENT_ID", "absorbiq-client")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", f"{API_ORIGIN}/api/v1/auth/callback")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    # Deliberately put the API first: this proves the success redirect no
    # longer inherits CORS ordering.
    monkeypatch.setenv("CORS_ORIGINS", f"{API_ORIGIN},{FRONTEND_ORIGIN}")
    monkeypatch.setenv("FRONTEND_BASE_URL", FRONTEND_ORIGIN)
    get_settings.cache_clear()
    oidc.reset_caches()
    yield
    get_settings.cache_clear()
    oidc.reset_caches()


@pytest.mark.asyncio
async def test_callback_route_is_registered_and_reaches_the_handler(oidc_redirect_env):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/auth/callback", follow_redirects=False)

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "INVALID_CALLBACK"


@pytest.mark.asyncio
async def test_callback_issues_session_then_redirects_to_canonical_frontend(
    oidc_redirect_env, monkeypatch
):
    state = "verified-state"
    flow = jwt.encode(
        {
            "state": state,
            "nonce": "verified-nonce",
            "cv": "verified-pkce-verifier",
            "rt": "/overview",
            "exp": int(time.time()) + 600,
        },
        "s" * 40,
        algorithm="HS256",
    )
    identity = oidc.OidcIdentity(
        subject="user-1",
        email="user@example.test",
        display_name="Test User",
        roles=frozenset({"CRM.CEO"}),
        groups=frozenset(),
        expires_at=int(time.time()) + 600,
    )

    async def exchange_code(*, code: str, code_verifier: str):
        assert code == "provider-code"
        assert code_verifier == "verified-pkce-verifier"
        return {"id_token": "provider-id-token"}

    monkeypatch.setattr(oidc, "exchange_code", exchange_code)
    monkeypatch.setattr(oidc, "verify_token", lambda token: identity)
    monkeypatch.setattr(oidc, "resolve_role", lambda _: "admin")
    monkeypatch.setattr(oidc, "resolve_scope", lambda _: "ALL")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(FLOW_COOKIE, flow)
        response = await client.get(
            f"/api/v1/auth/callback?code=provider-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == f"{FRONTEND_ORIGIN}/overview"
    assert API_ORIGIN not in response.headers["location"]
    assert any(
        header.startswith(f"{oidc.SESSION_COOKIE}=")
        for name, header in response.headers.multi_items()
        if name.lower() == "set-cookie"
    )


def test_deployment_uses_a_configured_api_origin_and_spa_fallback():
    """The deployment cannot silently rebuild against a stale Railway domain."""
    workflow = (REPO_ROOT / ".github/workflows/deploy-production.yml").read_text(encoding="utf-8")
    assert "API_URL: ${{ vars.ABSORPIQ_API_ORIGIN }}" in workflow
    assert "API_URL: https://" not in workflow
    assert "ABSORPIQ_API_ORIGIN must be one HTTPS origin without a path." in workflow

    nginx = (REPO_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    assert "try_files $uri $uri/ /index.html;" in nginx

    pages_redirects = (REPO_ROOT / "frontend/public/_redirects").read_text(encoding="utf-8")
    assert pages_redirects.strip() == "/* /index.html 200"


@pytest.mark.parametrize(
    "invalid_origin",
    ["/overview", "https://frontend.example/overview", "ftp://frontend.example"],
)
def test_frontend_redirect_origin_cannot_contain_a_route_or_non_http_scheme(invalid_origin):
    with pytest.raises(ValidationError, match="FRONTEND_BASE_URL"):
        Settings(_env_file=None, frontend_base_url=invalid_origin)


def test_configured_production_oidc_requires_an_explicit_frontend_origin():
    with pytest.raises(ValidationError, match="FRONTEND_BASE_URL bắt buộc"):
        Settings(
            _env_file=None,
            app_env="production",
            oidc_issuer="https://idp.example.test/realms/p100",
            oidc_client_id="absorbiq-client",
            oidc_client_secret="client-secret",
            oidc_redirect_uri=f"{API_ORIGIN}/api/v1/auth/callback",
            session_secret="s" * 40,
        )
