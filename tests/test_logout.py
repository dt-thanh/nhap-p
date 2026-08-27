from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from src.api.auth import FLOW_COOKIE, logout
from src.config import get_settings
from src.services import oidc

DISCOVERY = {
    "end_session_endpoint": "https://keycloak.example/realms/p100/protocol/openid-connect/logout",
    "revocation_endpoint": "https://keycloak.example/realms/p100/protocol/openid-connect/revoke",
}


class FakeRevocationRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.keys[key] = value
        self.ttls[key] = ex

    async def exists(self, key: str) -> int:
        return int(key in self.keys)


@pytest.fixture
def logout_env(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "https://keycloak.example/realms/p100")
    monkeypatch.setenv("OIDC_CLIENT_ID", "absorbiq-client")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8000/api/v1/auth/callback")
    monkeypatch.setenv("OIDC_POST_LOGOUT_REDIRECT_URI", "http://localhost:5173/login")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "get_discovery", lambda: DISCOVERY)
    yield
    get_settings.cache_clear()
    oidc.reset_caches()


def _session_token() -> str:
    identity = oidc.OidcIdentity(
        subject="user-1",
        email="user@example.com",
        display_name="Test User",
        roles=frozenset({"CRM.CEO"}),
        groups=frozenset(),
        expires_at=9999999999,
    )
    return oidc.issue_session(
        identity,
        role="admin",
        scope="ALL",
        refresh_token="refresh-token",
        id_token_hint="id-token-hint",
    )


@pytest.mark.asyncio
async def test_logout_revokes_session_clears_cookies_and_redirects_to_keycloak(logout_env, monkeypatch):
    store = FakeRevocationRedis()
    monkeypatch.setattr(oidc, "_get_revocation_redis", lambda: store)
    provider_revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(oidc, "revoke_token", provider_revoke)

    token = _session_token()
    assert (await oidc.read_session_verified(token))["sub"] == "user-1"

    response = await logout(token)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(DISCOVERY["end_session_endpoint"])
    params = parse_qs(urlsplit(location).query)
    assert params["id_token_hint"] == ["id-token-hint"]
    assert params["post_logout_redirect_uri"] == ["http://localhost:5173/login"]
    provider_revoke.assert_awaited_once_with("refresh-token")
    cookie_headers = [value.decode() for key, value in response.raw_headers if key.lower() == b"set-cookie"]
    assert any(value.startswith(f"{oidc.SESSION_COOKIE}=") and 'Max-Age=0' in value for value in cookie_headers)
    assert any(value.startswith(f"{FLOW_COOKIE}=") and 'Max-Age=0' in value for value in cookie_headers)

    with pytest.raises(HTTPException) as exc:
        await oidc.read_session_verified(token)
    assert exc.value.detail["error_code"] == "SESSION_REVOKED"
    with pytest.raises(HTTPException) as refresh_exc:
        await oidc.read_session_verified(token, verify_exp=False)
    assert refresh_exc.value.detail["error_code"] == "SESSION_REVOKED"
    assert store.ttls
    assert all(ttl > 0 for ttl in store.ttls.values())


@pytest.mark.asyncio
async def test_logout_without_session_still_clears_cookies_and_uses_keycloak(logout_env, monkeypatch):
    provider_revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(oidc, "revoke_token", provider_revoke)

    response = await logout(None)

    assert response.status_code == 303
    assert response.headers["location"].startswith(DISCOVERY["end_session_endpoint"])
    provider_revoke.assert_not_awaited()
    cookie_headers = [value.decode() for key, value in response.raw_headers if key.lower() == b"set-cookie"]
    assert len(cookie_headers) == 4


@pytest.mark.asyncio
async def test_logout_blacklists_the_mini_crm_session_on_the_shared_redis(logout_env, monkeypatch):
    shared_revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(oidc, "revoke_shared_token", shared_revoke)

    response = await logout(None, minicrm_session="minicrm-session")

    assert response.status_code == 303
    shared_revoke.assert_awaited_once_with("minicrm-session")
    cookie_headers = [value.decode() for key, value in response.raw_headers if key.lower() == b"set-cookie"]
    assert any(value.startswith("minicrm_session=") and "Max-Age=0" in value for value in cookie_headers)
    assert any(value.startswith("minicrm_oidc_flow=") and "Max-Age=0" in value for value in cookie_headers)


@pytest.mark.asyncio
async def test_provider_refresh_token_is_revoked_by_back_channel(logout_env, monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data
            return FakeResponse()

    monkeypatch.setattr(oidc.httpx, "AsyncClient", FakeClient)

    assert await oidc.revoke_token("remote-refresh") is True
    assert captured["url"] == "http://keycloak:8080/realms/p100/protocol/openid-connect/revoke"
    assert captured["data"] == {
        "client_id": "absorbiq-client",
        "client_secret": "client-secret",
        "token": "remote-refresh",
        "token_type_hint": "refresh_token",
    }
