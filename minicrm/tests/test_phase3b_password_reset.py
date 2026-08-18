"""Phase 3B password-reset tests against the single approved test database."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.auth_contract import AuthErrorCode
from app.human_auth import (
    PASSWORD_HASHER,
    HumanAuthError,
    HumanAuthService,
    hash_opaque_token,
    login_rate_limiter,
    password_reset_rate_limiter,
)
from app.main import app
from app.models import crm_auth_sessions, crm_password_reset_tokens, crm_users
from app.routers import auth as auth_router
from fastapi.testclient import TestClient

from tests.conftest import sync_url

TARGET_DATABASE = "minicrm_checkpoint1_test"
OLD_PASSWORD = "phase-three-b-old-password"
NEW_PASSWORD = "phase-three-b-new-password"


@dataclass
class RecordingResetDelivery:
    messages: list[tuple[str, str, datetime]]

    def __init__(self) -> None:
        self.messages = []

    async def send(self, *, login: str, token: str, expires_at: datetime) -> None:
        self.messages.append((login, token, expires_at))


def _target_url() -> str:
    url = os.environ.get("MINICRM_TEST_DATABASE_URL")
    if not url or urlsplit(url).path.lstrip("/") != TARGET_DATABASE:
        raise pytest.UsageError("MINICRM_TEST_DATABASE_URL must target minicrm_checkpoint1_test")
    return url


def _engine():
    return sa.create_engine(sync_url(_target_url()))


def _assert_target_database() -> None:
    engine = _engine()
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT current_database()")).scalar_one() == TARGET_DATABASE
    finally:
        engine.dispose()


def _insert_user(login: str, *, status: str = "active", password: str | None = OLD_PASSWORD) -> None:
    now = datetime.now(UTC)
    engine = _engine()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.insert(crm_users).values(
                    id=uuid4(),
                    login=login,
                    email=None,
                    password_hash=PASSWORD_HASHER.hash(password) if password else None,
                    status=status,
                    role="business_viewer",
                    auth_version=1,
                    created_at=now,
                    updated_at=now,
                    disabled_at=now if status == "disabled" else None,
                )
            )
    finally:
        engine.dispose()


def _user_id(login: str):
    engine = _engine()
    try:
        with engine.connect() as connection:
            return connection.execute(sa.select(crm_users.c.id).where(crm_users.c.login == login)).scalar_one()
    finally:
        engine.dispose()


def _reset_rows(login_prefix: str) -> None:
    engine = _engine()
    try:
        with engine.begin() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    sa.select(crm_users.c.id).where(crm_users.c.login.like(f"{login_prefix}%"))
                )
            ]
            if ids:
                connection.execute(sa.delete(crm_password_reset_tokens).where(crm_password_reset_tokens.c.user_id.in_(ids)))
                connection.execute(sa.delete(crm_auth_sessions).where(crm_auth_sessions.c.user_id.in_(ids)))
                connection.execute(sa.delete(crm_users).where(crm_users.c.id.in_(ids)))
    finally:
        engine.dispose()


@pytest.fixture
def phase3b_env(monkeypatch):
    _assert_target_database()
    prefix = f"phase3b-{uuid4().hex}"
    login = f"{prefix}@example.test"
    delivery = RecordingResetDelivery()
    monkeypatch.setenv("MINICRM_DATABASE_URL", _target_url())
    monkeypatch.setenv("MINICRM_AUTH_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("MINICRM_AUTH_AUDIENCE", "absorbiq-api")
    monkeypatch.setenv("MINICRM_AUTH_ALGORITHM", "HS256")
    monkeypatch.setenv("MINICRM_AUTH_SIGNING_SECRET", "phase-3b-test-signing-secret")
    monkeypatch.setenv("MINICRM_PASSWORD_RESET_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("MINICRM_LOGIN_RATE_LIMIT_ATTEMPTS", "2")
    monkeypatch.setenv("MINICRM_RELAY_ENABLED", "false")
    for name in (
        "MINICRM_AUTH_ADMIN_TOKEN",
        "MINICRM_AUTH_PIPELINE_OPERATOR_TOKEN",
        "MINICRM_AUTH_BUSINESS_VIEWER_TOKEN",
    ):
        monkeypatch.setenv(name, "")

    from app.config import get_settings
    from app.db import get_engine, get_session_factory

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    password_reset_rate_limiter.reset()
    login_rate_limiter.reset()
    _insert_user(login)
    monkeypatch.setattr(auth_router, "service", HumanAuthService(reset_delivery=delivery))

    try:
        yield {"login": login, "prefix": prefix, "delivery": delivery}
    finally:
        _reset_rows(prefix)
        password_reset_rate_limiter.reset()
        login_rate_limiter.reset()
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_session_factory.cache_clear()


def _client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, login: str, password: str = OLD_PASSWORD) -> dict:
    response = client.post("/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200
    return response.json()


def _request_token(client: TestClient, env: dict) -> str:
    before = len(env["delivery"].messages)
    response = client.post("/auth/password-reset/request", json={"login": env["login"]})
    assert response.status_code == 200
    assert len(env["delivery"].messages) == before + 1
    return env["delivery"].messages[-1][1]


def _reset_rows_for(login: str) -> list[dict]:
    engine = _engine()
    try:
        with engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    sa.select(crm_password_reset_tokens).where(
                        crm_password_reset_tokens.c.user_id == _user_id(login)
                    )
                ).mappings()
            ]
    finally:
        engine.dispose()


def test_request_is_generic_for_known_unknown_and_inactive_users(phase3b_env):
    inactive = f"{phase3b_env['prefix']}-inactive@example.test"
    _insert_user(inactive, status="disabled")
    with _client() as client:
        known = client.post("/auth/password-reset/request", json={"login": phase3b_env["login"]})
        unknown = client.post("/auth/password-reset/request", json={"login": "unknown@example.test"})
        disabled = client.post("/auth/password-reset/request", json={"login": inactive})

    assert known.status_code == unknown.status_code == disabled.status_code == 200
    assert known.json() == unknown.json() == disabled.json()
    assert len(phase3b_env["delivery"].messages) == 1


def test_request_normalizes_login_and_stores_only_hash_with_short_expiry(phase3b_env):
    with _client() as client:
        before = len(phase3b_env["delivery"].messages)
        response = client.post(
            "/auth/password-reset/request",
            json={"login": f"  {phase3b_env['login'].upper()}  "},
        )
        assert response.status_code == 200
        assert len(phase3b_env["delivery"].messages) == before + 1
        token = phase3b_env["delivery"].messages[-1][1]

    row = _reset_rows_for(phase3b_env["login"])[0]
    assert row["token_hash"] == hash_opaque_token(token)
    assert token not in row["token_hash"]
    assert row["expires_at"] - row["created_at"] == timedelta(seconds=900)
    assert phase3b_env["delivery"].messages[-1][0] == phase3b_env["login"]


def test_second_request_invalidates_the_previous_token(phase3b_env):
    with _client() as client:
        first = _request_token(client, phase3b_env)
        second = _request_token(client, phase3b_env)
        old = client.post(
            "/auth/password-reset/confirm",
            json={"token": first, "password": NEW_PASSWORD},
        )
        current = client.post(
            "/auth/password-reset/confirm",
            json={"token": second, "password": NEW_PASSWORD},
        )

    assert first != second
    assert old.status_code == 400
    assert current.status_code == 200
    assert old.json() == {"error_code": AuthErrorCode.PASSWORD_RESET_INVALID, "message": "Invalid password reset token"}


def test_request_rate_limits_by_ip_and_normalized_account(phase3b_env):
    second_login = f"{phase3b_env['prefix']}-second@example.test"
    _insert_user(second_login)
    service = auth_router.service

    async def request(login: str, client_key: str) -> None:
        from app.db import get_session_factory

        async with get_session_factory()() as session:
            await service.request_password_reset(session, login=login, client_key=client_key)

    asyncio.run(request(phase3b_env["login"], "same-ip"))
    asyncio.run(request(second_login, "same-ip"))
    asyncio.run(request(second_login, "same-ip"))
    assert len(phase3b_env["delivery"].messages) == 2

    password_reset_rate_limiter.reset()
    phase3b_env["delivery"].messages.clear()
    asyncio.run(request(second_login, "first-ip"))
    asyncio.run(request(second_login, "second-ip"))
    asyncio.run(request(second_login, "third-ip"))
    assert len(phase3b_env["delivery"].messages) == 2


def test_valid_reset_hashes_new_password_and_revokes_sessions_and_families(phase3b_env):
    with _client() as client:
        first = _login(client, phase3b_env["login"])
        second = _login(client, phase3b_env["login"])
        reset_token = _request_token(client, phase3b_env)
        confirmed = client.post(
            "/auth/password-reset/confirm",
            json={"token": reset_token, "password": NEW_PASSWORD},
        )
        old_password = client.post(
            "/auth/login", json={"login": phase3b_env["login"], "password": OLD_PASSWORD}
        )
        new_password = client.post(
            "/auth/login", json={"login": phase3b_env["login"], "password": NEW_PASSWORD}
        )
        first_me = client.get("/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"})
        second_me = client.get("/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})
        first_refresh = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
        second_refresh = client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})

    assert confirmed.status_code == 200
    assert "token" not in confirmed.json()
    assert "access_token" not in confirmed.json()
    assert "refresh_token" not in confirmed.json()
    assert old_password.status_code == 401
    assert new_password.status_code == 200
    assert first_me.status_code == second_me.status_code == 401
    assert first_refresh.status_code == second_refresh.status_code == 401
    assert _reset_rows_for(phase3b_env["login"])[0]["used_at"] is not None

    engine = _engine()
    try:
        with engine.connect() as connection:
            row = connection.execute(
                sa.select(crm_users.c.password_hash).where(crm_users.c.id == _user_id(phase3b_env["login"]))
            ).scalar_one()
        assert row.startswith("$argon2id$")
    finally:
        engine.dispose()


def test_invalid_expired_and_malformed_tokens_are_generic_and_single_use(phase3b_env):
    with _client() as client:
        unknown = client.post(
            "/auth/password-reset/confirm", json={"token": "unknown-reset-token", "password": NEW_PASSWORD}
        )
        short_password = client.post(
            "/auth/password-reset/confirm", json={"token": "", "password": "short"}
        )
        token = _request_token(client, phase3b_env)

    engine = _engine()
    try:
        with engine.begin() as connection:
            reset_id = connection.execute(
                sa.select(crm_password_reset_tokens.c.id).where(
                    crm_password_reset_tokens.c.token_hash == hash_opaque_token(token)
                )
            ).scalar_one()
            created_at = datetime.now(UTC) - timedelta(seconds=10)
            connection.execute(
                sa.update(crm_password_reset_tokens)
                .where(crm_password_reset_tokens.c.id == reset_id)
                .values(created_at=created_at, expires_at=created_at + timedelta(seconds=1))
            )
    finally:
        engine.dispose()

    with _client() as client:
        expired = client.post(
            "/auth/password-reset/confirm", json={"token": token, "password": NEW_PASSWORD}
        )

    assert unknown.status_code == expired.status_code == 400
    assert unknown.json() == expired.json()
    assert short_password.status_code == 422


def test_concurrent_confirmation_allows_one_success(phase3b_env):
    with _client() as client:
        token = _request_token(client, phase3b_env)

    async def confirm_once():
        from app.db import get_session_factory

        async with get_session_factory()() as session:
            try:
                await HumanAuthService().confirm_password_reset(
                    session, token=token, password=NEW_PASSWORD
                )
                return None
            except HumanAuthError as error:
                return error

    async def confirm_concurrently():
        return await asyncio.gather(confirm_once(), confirm_once())

    results = asyncio.run(confirm_concurrently())
    successes = [result for result in results if result is None]
    failures = [result for result in results if isinstance(result, HumanAuthError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].error_code == AuthErrorCode.PASSWORD_RESET_INVALID


def test_failed_schema_confirmation_does_not_consume_token(phase3b_env):
    with _client() as client:
        token = _request_token(client, phase3b_env)
        rejected = client.post(
            "/auth/password-reset/confirm", json={"token": token, "password": "short"}
        )
        accepted = client.post(
            "/auth/password-reset/confirm", json={"token": token, "password": NEW_PASSWORD}
        )

    assert rejected.status_code == 422
    assert accepted.status_code == 200


def test_reset_delivery_does_not_log_or_return_raw_token(phase3b_env, caplog):
    caplog.set_level(logging.INFO)
    with _client() as client:
        before = len(phase3b_env["delivery"].messages)
        response = client.post(
            "/auth/password-reset/request", json={"login": phase3b_env["login"]}
        )
    token = phase3b_env["delivery"].messages[before][1]
    assert response.status_code == 200
    assert token not in response.text
    assert token not in caplog.text


def test_other_users_remain_valid_and_static_tokens_remain_unconfigured(phase3b_env):
    other_login = f"{phase3b_env['prefix']}-other@example.test"
    _insert_user(other_login)
    with _client() as client:
        other = _login(client, other_login)
        token = _request_token(client, phase3b_env)
        assert client.post(
            "/auth/password-reset/confirm", json={"token": token, "password": NEW_PASSWORD}
        ).status_code == 200
        other_me = client.get("/auth/me", headers={"Authorization": f"Bearer {other['access_token']}"})

    from app.config import get_settings

    settings = get_settings()
    assert sum(
        bool(value.get_secret_value())
        for value in (
            settings.auth_admin_token,
            settings.auth_pipeline_operator_token,
            settings.auth_business_viewer_token,
        )
    ) == 0
    assert other_me.status_code == 200
