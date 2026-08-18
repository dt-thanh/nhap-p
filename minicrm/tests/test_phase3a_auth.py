"""Phase 3A refresh-token rotation and session-lifecycle tests.

These tests deliberately use only the operator-provisioned
``minicrm_checkpoint1_test`` database. They clean up rows identified by a
test-unique login prefix and never create or drop a database.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.auth_contract import AuthErrorCode
from app.human_auth import PASSWORD_HASHER, HumanAuthError, HumanAuthService, hash_opaque_token
from app.main import app
from app.models import crm_auth_invites, crm_auth_sessions, crm_users
from fastapi.testclient import TestClient

from tests.conftest import sync_url

TARGET_DATABASE = "minicrm_checkpoint1_test"
PASSWORD = "phase-three-a-password"


def _target_url() -> str:
    url = os.environ.get("MINICRM_TEST_DATABASE_URL")
    if not url or urlsplit(url).path.lstrip("/") != TARGET_DATABASE:
        raise pytest.UsageError("MINICRM_TEST_DATABASE_URL must target minicrm_checkpoint1_test")
    return url


@pytest.fixture
def phase3a_env(monkeypatch):
    url = _target_url()
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT current_database()")).scalar_one() == TARGET_DATABASE
    finally:
        engine.dispose()

    prefix = f"phase3a-{uuid4().hex}"
    login = f"{prefix}@example.test"
    monkeypatch.setenv("MINICRM_DATABASE_URL", url)
    monkeypatch.setenv("MINICRM_AUTH_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("MINICRM_AUTH_AUDIENCE", "absorbiq-api")
    monkeypatch.setenv("MINICRM_AUTH_ALGORITHM", "HS256")
    monkeypatch.setenv("MINICRM_AUTH_SIGNING_SECRET", "phase-3a-test-signing-secret")
    monkeypatch.setenv("MINICRM_REFRESH_TOKEN_TTL_SECONDS", "3600")
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

    engine = sa.create_engine(sync_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.insert(crm_users).values(
                    id=uuid4(),
                    login=login,
                    email=None,
                    password_hash=PASSWORD_HASHER.hash(PASSWORD),
                    status="active",
                    role="business_viewer",
                    auth_version=1,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
    finally:
        engine.dispose()

    try:
        yield {"url": url, "login": login, "prefix": prefix}
    finally:
        engine = sa.create_engine(sync_url(url))
        try:
            with engine.begin() as connection:
                user_ids = [
                    row[0]
                    for row in connection.execute(
                        sa.select(crm_users.c.id).where(crm_users.c.login.like(f"{prefix}%"))
                    )
                ]
                if user_ids:
                    connection.execute(sa.delete(crm_auth_sessions).where(crm_auth_sessions.c.user_id.in_(user_ids)))
                    connection.execute(sa.delete(crm_users).where(crm_users.c.id.in_(user_ids)))
                connection.execute(sa.delete(crm_auth_invites).where(crm_auth_invites.c.login.like(f"{prefix}%")))
        finally:
            engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_session_factory.cache_clear()


def _client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, login: str) -> dict:
    response = client.post("/auth/login", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()


def _session_rows(url: str, login: str) -> list[dict]:
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    sa.select(crm_auth_sessions)
                    .join(crm_users, crm_users.c.id == crm_auth_sessions.c.user_id)
                    .where(crm_users.c.login == login)
                    .order_by(crm_auth_sessions.c.created_at)
                ).mappings()
            ]
    finally:
        engine.dispose()


def test_login_returns_only_opaque_refresh_token_and_binds_session(phase3a_env):
    with _client() as client:
        result = _login(client, phase3a_env["login"])

    assert result["refresh_token"]
    assert len(result["refresh_token"]) >= 60
    rows = _session_rows(phase3a_env["url"], phase3a_env["login"])
    assert len(rows) == 1
    assert rows[0]["refresh_token_hash"] == hash_opaque_token(result["refresh_token"])
    assert result["refresh_token"] not in rows[0]["refresh_token_hash"]


def test_refresh_rotates_once_and_preserves_family_and_absolute_expiry(phase3a_env):
    with _client() as client:
        initial = _login(client, phase3a_env["login"])
        rotated = client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})
        old_again = client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != initial["refresh_token"]
    assert old_again.status_code == 401
    assert old_again.json() == {
        "error_code": AuthErrorCode.SESSION_INVALID,
        "message": "Invalid session",
    }

    rows = _session_rows(phase3a_env["url"], phase3a_env["login"])
    assert len(rows) == 2
    parent, child = rows
    assert parent["user_id"] == child["user_id"]
    assert parent["family_id"] == child["family_id"]
    assert parent["replaced_by"] == child["id"]
    assert parent["revoked_at"] is not None
    assert child["expires_at"] == parent["expires_at"]


def test_refresh_errors_are_generic_for_unknown_expired_revoked_and_malformed(phase3a_env):
    with _client() as client:
        initial = _login(client, phase3a_env["login"])
        unknown = client.post("/auth/refresh", json={"refresh_token": "not-a-refresh-token"})
        malformed = client.post("/auth/refresh", json={"refresh_token": ""})

    engine = sa.create_engine(sync_url(phase3a_env["url"]))
    try:
        with engine.begin() as connection:
            user_id = connection.execute(
                sa.select(crm_users.c.id).where(crm_users.c.login == phase3a_env["login"])
            ).scalar_one()
            created_at = datetime.now(UTC) - timedelta(seconds=10)
            connection.execute(
                sa.update(crm_auth_sessions)
                .where(crm_auth_sessions.c.user_id == user_id)
                .values(created_at=created_at, expires_at=created_at + timedelta(seconds=1))
            )
    finally:
        engine.dispose()

    with _client() as client:
        expired = client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    with _client() as client:
        other = _login(client, phase3a_env["login"])
        assert client.post("/auth/logout", headers={"Authorization": f"Bearer {other['access_token']}"}).status_code == 204
        revoked = client.post("/auth/refresh", json={"refresh_token": other["refresh_token"]})

    assert unknown.status_code == malformed.status_code == expired.status_code == revoked.status_code == 401
    assert unknown.json() == malformed.json() == expired.json() == revoked.json()


def test_reuse_revokes_the_complete_family_and_refresh_is_not_bearer_access(phase3a_env):
    with _client() as client:
        initial = _login(client, phase3a_env["login"])
        rotated = client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]}).json()
        reuse = client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})
        child_me = client.get("/auth/me", headers={"Authorization": f"Bearer {rotated['access_token']}"})
        refresh_as_bearer = client.get(
            "/auth/me", headers={"Authorization": f"Bearer {rotated['refresh_token']}"}
        )

    assert reuse.status_code == 401
    assert child_me.status_code == 401
    assert refresh_as_bearer.status_code == 401
    assert all(row["revoked_at"] is not None for row in _session_rows(phase3a_env["url"], phase3a_env["login"]))


def test_concurrent_refresh_allows_one_rotation_and_revokes_family_on_reuse(phase3a_env):
    with _client() as client:
        initial = _login(client, phase3a_env["login"])

    async def rotate_once():
        from app.db import get_session_factory

        async with get_session_factory()() as session:
            try:
                return await HumanAuthService().refresh(session, refresh_token=initial["refresh_token"])
            except HumanAuthError as error:
                return error

    async def rotate_concurrently():
        return await asyncio.gather(rotate_once(), rotate_once())

    results = asyncio.run(rotate_concurrently())
    successes = [result for result in results if not isinstance(result, HumanAuthError)]
    failures = [result for result in results if isinstance(result, HumanAuthError)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].error_code == AuthErrorCode.SESSION_INVALID
    assert all(row["revoked_at"] is not None for row in _session_rows(phase3a_env["url"], phase3a_env["login"]))


def test_current_logout_is_session_local_and_logout_all_revokes_every_session(phase3a_env):
    with _client() as client:
        first = _login(client, phase3a_env["login"])
        second = _login(client, phase3a_env["login"])
        current_logout = client.post("/auth/logout", headers={"Authorization": f"Bearer {first['access_token']}"})
        first_me = client.get("/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"})
        second_me = client.get("/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})
        second_refresh = client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
        all_logout = client.post("/auth/logout-all", headers={"Authorization": f"Bearer {second['access_token']}"})
        second_me_after = client.get("/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"})
        rotated_me_after = client.get(
            "/auth/me", headers={"Authorization": f"Bearer {second_refresh.json()['access_token']}"}
        )

    assert current_logout.status_code == 204
    assert first_me.status_code == 401
    assert second_me.status_code == 200
    assert second_refresh.status_code == 200
    assert all_logout.status_code == 204
    assert second_me_after.status_code == 401
    assert rotated_me_after.status_code == 401


def test_expired_session_and_empty_auth_configuration_fail_closed(phase3a_env, monkeypatch):
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

    monkeypatch.setenv("MINICRM_AUTH_SIGNING_SECRET", "")
    get_settings.cache_clear()
    with _client() as client:
        unconfigured = client.post(
            "/auth/login", json={"login": phase3a_env["login"], "password": PASSWORD}
        )
    assert unconfigured.status_code == 503
    assert unconfigured.json()["error_code"] == AuthErrorCode.AUTH_NOT_CONFIGURED

    monkeypatch.setenv("MINICRM_AUTH_SIGNING_SECRET", "phase-3a-test-signing-secret")
    get_settings.cache_clear()

    with _client() as client:
        initial = _login(client, phase3a_env["login"])

    engine = sa.create_engine(sync_url(phase3a_env["url"]))
    try:
        with engine.begin() as connection:
            user_id = connection.execute(
                sa.select(crm_users.c.id).where(crm_users.c.login == phase3a_env["login"])
            ).scalar_one()
            created_at = datetime.now(UTC) - timedelta(seconds=10)
            connection.execute(
                sa.update(crm_auth_sessions)
                .where(crm_auth_sessions.c.user_id == user_id)
                .values(created_at=created_at, expires_at=created_at + timedelta(seconds=1))
            )
    finally:
        engine.dispose()

    with _client() as client:
        expired_me = client.get("/auth/me", headers={"Authorization": f"Bearer {initial['access_token']}"})
        expired_refresh = client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    assert expired_me.status_code == 401
    assert expired_refresh.status_code == 401
