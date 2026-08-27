from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import human_auth, oidc  # noqa: E402
from app import session as session_mod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.human_auth import LoginRateLimiter  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.auth_routes import logout, logout_all  # noqa: E402

DISCOVERY = {
    "end_session_endpoint": "https://keycloak.example/realms/p100/protocol/openid-connect/logout",
    "revocation_endpoint": "https://keycloak.example/realms/p100/protocol/openid-connect/revoke",
}


class FakeRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.ttls: dict[str, int] = {}
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.keys.add(key)
        self.ttls[key] = ex
        self.values[key] = value

    async def exists(self, key: str) -> int:
        return int(key in self.keys)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


@pytest.fixture
def logout_env(monkeypatch):
    monkeypatch.setenv("MINICRM_OIDC_ISSUER", "https://keycloak.example/realms/p100")
    monkeypatch.setenv("MINICRM_OIDC_CLIENT_ID", "minicrm-client")
    monkeypatch.setenv("MINICRM_OIDC_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MINICRM_OIDC_REDIRECT_URI", "http://localhost:8100/auth/callback")
    monkeypatch.setenv("MINICRM_OIDC_POST_LOGOUT_REDIRECT_URI", "http://localhost:5174/login")
    monkeypatch.setenv("MINICRM_SESSION_SECRET", "m" * 40)
    monkeypatch.setenv("MINICRM_REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()
    monkeypatch.setattr(oidc, "get_discovery", lambda: DISCOVERY)
    human_auth.logout_rate_limiter.reset()
    yield
    human_auth.logout_rate_limiter.reset()
    get_settings.cache_clear()


def _fake_request(ip: str | None = "203.0.113.7") -> SimpleNamespace:
    """Minimal stand-in for `starlette.requests.Request` — direct (non-HTTP)
    calls to `logout()` in this file don't go through FastAPI's dependency
    injection, so they must supply this explicitly. Only `.client.host` is
    read by the route."""
    return SimpleNamespace(client=SimpleNamespace(host=ip) if ip is not None else None)


def _session_token(subject: str = "user-1") -> str:
    identity = oidc.OidcIdentity(
        subject=subject,
        email=f"{subject}@example.com",
        display_name="Test User",
        roles=frozenset({"CRM.CEO"}),
        groups=frozenset(),
        expires_at=9999999999,
    )
    return session_mod.issue_session(
        identity,
        role="admin",
        scope="ALL",
        refresh_token="refresh-token",
        id_token_hint="id-token-hint",
    )


@pytest.mark.asyncio
async def test_minicrm_logout_blacklists_both_app_sessions_and_clears_cookies(logout_env, monkeypatch):
    store = FakeRedis()
    monkeypatch.setattr(session_mod, "_get_revocation_redis", lambda: store)
    monkeypatch.setattr(oidc, "revoke_token", AsyncMock(return_value=True))

    minicrm_token = _session_token()
    absorbiq_token = "absorbiq-session-from-shared-host"
    assert (await session_mod.read_session_verified(minicrm_token))["sub"] == "user-1"

    response = await logout(request=_fake_request(), minicrm_session=minicrm_token, absorbiq_session=absorbiq_token)

    assert response.status_code == 303
    assert response.headers["location"].startswith(DISCOVERY["end_session_endpoint"])
    cookie_headers = [value.decode() for key, value in response.raw_headers if key.lower() == b"set-cookie"]
    for cookie_name in ("minicrm_session", "minicrm_oidc_flow", "absorbiq_session", "absorbiq_oidc_flow"):
        assert any(value.startswith(f"{cookie_name}=") and "Max-Age=0" in value for value in cookie_headers)

    with pytest.raises(HTTPException) as exc:
        await session_mod.read_session_verified(minicrm_token)
    assert exc.value.detail["error_code"] == "SESSION_REVOKED"
    assert session_mod._blacklist_key(absorbiq_token) in store.keys
    assert store.ttls[session_mod._blacklist_key(minicrm_token)] > 0
    assert store.ttls[session_mod._blacklist_key(absorbiq_token)] > 0
    oidc.revoke_token.assert_awaited_once_with("refresh-token")


@pytest.mark.asyncio
async def test_shared_logout_revokes_human_jwt_session_and_refresh_family(logout_env, monkeypatch):
    principal = SimpleNamespace(subject="human-user-1")
    db_session = SimpleNamespace(close=AsyncMock())
    revoke_current = AsyncMock()
    service = SimpleNamespace(logout_current=revoke_current)
    monkeypatch.setattr(human_auth, "require_human_principal", AsyncMock(return_value=principal))
    monkeypatch.setattr(human_auth, "db_session", lambda: db_session)
    monkeypatch.setattr(human_auth, "HumanAuthService", lambda: service)

    response = await logout(_fake_request(), None, None, "Bearer human-access-token")

    assert response.status_code == 303
    revoke_current.assert_awaited_once_with(db_session, principal)
    db_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_logout_with_invalid_human_bearer_is_idempotent(logout_env, monkeypatch):
    monkeypatch.setattr(
        human_auth,
        "require_human_principal",
        AsyncMock(side_effect=human_auth.HumanAuthError("INVALID_CREDENTIALS", "invalid", 401)),
    )

    response = await logout(_fake_request(), None, None, "Bearer expired-or-invalid")

    assert response.status_code == 303
    cookie_headers = [value.decode() for key, value in response.raw_headers if key.lower() == b"set-cookie"]
    assert any(value.startswith("minicrm_session=") and "Max-Age=0" in value for value in cookie_headers)


@pytest.mark.asyncio
async def test_post_logout_http_route_revokes_human_session_without_following_redirect(logout_env, monkeypatch):
    principal = SimpleNamespace(subject="human-user-1")
    db_session = SimpleNamespace(close=AsyncMock())
    revoke_current = AsyncMock()
    monkeypatch.setattr(human_auth, "require_human_principal", AsyncMock(return_value=principal))
    monkeypatch.setattr(human_auth, "db_session", lambda: db_session)
    monkeypatch.setattr(human_auth, "HumanAuthService", lambda: SimpleNamespace(logout_current=revoke_current))
    monkeypatch.setattr(oidc, "oidc_configured", lambda: False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/logout",
            headers={"Authorization": "Bearer human-access-token"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/login")
    revoke_current.assert_awaited_once_with(db_session, principal)
    db_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_canonical_logout_get_redirects_but_logout_all_get_is_not_enabled(logout_env):
    """Regression for the frontend's former full-page navigation to the
    POST-only `/auth/logout-all` endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        obsolete = await client.get("/auth/logout-all", follow_redirects=False)
        canonical = await client.get("/auth/logout", follow_redirects=False)

    assert obsolete.status_code == 405
    assert canonical.status_code == 303
    assert canonical.headers["location"].startswith(DISCOVERY["end_session_endpoint"])


def test_frontend_logout_has_no_logout_all_navigation() -> None:
    frontend_src = Path(__file__).resolve().parents[1] / "crm-frontend" / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in frontend_src.rglob("*.tsx"))
    source += "\n" + "\n".join(path.read_text(encoding="utf-8") for path in frontend_src.rglob("*.ts"))

    assert "/auth/logout-all" not in source
    assert "logoutAll" not in source
    assert "window.location.replace(`${BASE}/auth/logout`)" in source
    assert "setUser(null)" in source
    assert "localStorage.removeItem(LEGACY_AUTH_STORAGE_KEY)" in source


@pytest.mark.asyncio
async def test_logout_audit_log_has_user_id_timestamp_ip_and_no_tokens(logout_env, monkeypatch, caplog):
    store = FakeRedis()
    monkeypatch.setattr(session_mod, "_get_revocation_redis", lambda: store)
    monkeypatch.setattr(oidc, "revoke_token", AsyncMock(return_value=True))

    minicrm_token = _session_token()
    with caplog.at_level("INFO", logger="minicrm.auth"):
        await logout(request=_fake_request("198.51.100.9"), minicrm_session=minicrm_token, absorbiq_session=None)

    records = [r for r in caplog.records if r.name == "minicrm.auth" and r.message == "auth.logout"]
    assert len(records) == 1
    record = records[0]
    assert record.user_id == "user-1"
    assert record.ip == "198.51.100.9"
    assert record.timestamp  # ISO-8601 string, presence is what matters here
    assert record.minicrm_session_revoked is True
    assert record.refresh_token_revoked is True
    assert record.absorbiq_session_revoked is False

    logged_text = caplog.text
    assert minicrm_token not in logged_text
    assert "refresh-token" not in logged_text
    assert "id-token-hint" not in logged_text


@pytest.mark.asyncio
async def test_logout_all_revokes_every_session_for_the_same_subject(logout_env, monkeypatch):
    store = FakeRedis()
    monkeypatch.setattr(session_mod, "_get_revocation_redis", lambda: store)
    monkeypatch.setattr(oidc, "revoke_token", AsyncMock(return_value=True))

    device_a = _session_token("user-1")
    device_b = _session_token("user-1")
    assert (await session_mod.read_session_verified(device_a))["sub"] == "user-1"
    assert (await session_mod.read_session_verified(device_b))["sub"] == "user-1"

    response = await logout_all(request=_fake_request(), minicrm_session=device_a, absorbiq_session=None)

    assert response.status_code == 303
    for token in (device_a, device_b):
        with pytest.raises(HTTPException) as exc:
            await session_mod.read_session_verified(token)
        assert exc.value.detail["error_code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_logout_all_does_not_revoke_a_different_subjects_session(logout_env, monkeypatch):
    """Coi như một cuộc tấn công `mismatched user_id`: subject bị thu hồi LUÔN
    LUÔN lấy từ chính token đang gọi — không có tham số nào cho phép người gọi
    tự khai một `user_id` khác để đăng xuất phiên của người khác."""
    store = FakeRedis()
    monkeypatch.setattr(session_mod, "_get_revocation_redis", lambda: store)
    monkeypatch.setattr(oidc, "revoke_token", AsyncMock(return_value=True))

    mine = _session_token("user-1")
    someone_elses = _session_token("user-2")

    await logout_all(request=_fake_request(), minicrm_session=mine, absorbiq_session=None)

    assert (await session_mod.read_session_verified(someone_elses))["sub"] == "user-2"


@pytest.mark.asyncio
async def test_logout_all_audit_log_uses_distinct_event_and_no_tokens(logout_env, monkeypatch, caplog):
    store = FakeRedis()
    monkeypatch.setattr(session_mod, "_get_revocation_redis", lambda: store)
    monkeypatch.setattr(oidc, "revoke_token", AsyncMock(return_value=True))

    minicrm_token = _session_token()
    with caplog.at_level("INFO", logger="minicrm.auth"):
        await logout_all(request=_fake_request("198.51.100.9"), minicrm_session=minicrm_token, absorbiq_session=None)

    records = [r for r in caplog.records if r.name == "minicrm.auth" and r.message == "auth.logout_all"]
    assert len(records) == 1
    assert records[0].user_id == "user-1"
    assert records[0].ip == "198.51.100.9"
    assert minicrm_token not in caplog.text
    assert "refresh-token" not in caplog.text


@pytest.mark.asyncio
async def test_logout_rate_limit_returns_429_after_threshold(logout_env, monkeypatch):
    monkeypatch.setattr(
        human_auth,
        "logout_rate_limiter",
        LoginRateLimiter(max_attempts=lambda: 2, window_seconds=lambda: 60.0),
    )
    monkeypatch.setattr(session_mod, "_get_revocation_redis", lambda: FakeRedis())
    monkeypatch.setattr(oidc, "revoke_token", AsyncMock(return_value=True))

    request = _fake_request("203.0.113.99")
    await logout(request=request, minicrm_session=None, absorbiq_session=None, authorization=None)
    await logout(request=request, minicrm_session=None, absorbiq_session=None, authorization=None)

    with pytest.raises(HTTPException) as exc:
        await logout(request=request, minicrm_session=None, absorbiq_session=None, authorization=None)
    assert exc.value.status_code == 429
    assert exc.value.detail["error_code"] == "LOGOUT_RATE_LIMITED"


@pytest.mark.asyncio
async def test_logout_all_rate_limit_is_tracked_separately_from_logout(logout_env, monkeypatch):
    """`logout` bị chặn không được kéo theo `logout-all` bị chặn (hai `scope`
    khoá riêng) — hai nút trong UI không được vô tình khoá lẫn nhau."""
    monkeypatch.setattr(
        human_auth,
        "logout_rate_limiter",
        LoginRateLimiter(max_attempts=lambda: 1, window_seconds=lambda: 60.0),
    )
    monkeypatch.setattr(session_mod, "_get_revocation_redis", lambda: FakeRedis())
    monkeypatch.setattr(oidc, "revoke_token", AsyncMock(return_value=True))

    request = _fake_request("203.0.113.55")
    await logout(request=request, minicrm_session=None, absorbiq_session=None, authorization=None)
    with pytest.raises(HTTPException):
        await logout(request=request, minicrm_session=None, absorbiq_session=None, authorization=None)

    # Different scope key ("logout-all" vs "logout") — budget not shared.
    response = await logout_all(request=request, minicrm_session=None, absorbiq_session=None, authorization=None)
    assert response.status_code == 303
