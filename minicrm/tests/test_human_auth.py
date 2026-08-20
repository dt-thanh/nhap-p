"""Checkpoint 2 human-auth API and token-contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
import sqlalchemy as sa
from app.auth_contract import AuthErrorCode
from app.human_auth import PASSWORD_HASHER, hash_opaque_token, login_rate_limiter
from app.main import app
from app.models import crm_auth_invites, crm_users
from fastapi.testclient import TestClient

from tests.conftest import OPERATOR_AUTH_HEADER, sync_url


@pytest.fixture
def human_auth_env(crm_app, monkeypatch):
    monkeypatch.setenv("MINICRM_AUTH_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("MINICRM_AUTH_AUDIENCE", "absorbiq-api")
    monkeypatch.setenv("MINICRM_AUTH_ALGORITHM", "HS256")
    monkeypatch.setenv("MINICRM_AUTH_SIGNING_SECRET", "checkpoint-2-test-signing-secret")
    monkeypatch.setenv("MINICRM_INVITE_TOKEN_TTL_SECONDS", "86400")
    from app.config import get_settings

    get_settings.cache_clear()
    login_rate_limiter.reset()
    yield crm_app
    login_rate_limiter.reset()
    get_settings.cache_clear()


def _client() -> TestClient:
    return TestClient(app)


def _seed_invitation(url: str, login: str, *, role: str = "business_viewer", expires_at=None) -> str:
    token = f"test-invite-{uuid4().hex}"
    now = datetime.now(UTC)
    created_at = now if expires_at is None or expires_at > now else expires_at - timedelta(seconds=1)
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.insert(crm_auth_invites).values(
                    id=uuid4(),
                    login=login,
                    role=role,
                    invite_token_hash=hash_opaque_token(token),
                    expires_at=expires_at or now + timedelta(hours=1),
                    created_at=created_at,
                )
            )
    finally:
        engine.dispose()
    return token


def _seed_human_user(url: str, login: str, *, role: str) -> None:
    now = datetime.now(UTC)
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.insert(crm_users).values(
                    id=uuid4(),
                    login=login,
                    email=None,
                    password_hash=PASSWORD_HASHER.hash("correct horse battery"),
                    status="active",
                    role=role,
                    auth_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
    finally:
        engine.dispose()


def test_invitation_creation_is_admin_only_and_does_not_return_token(human_auth_env):
    admin_login = f"admin-{uuid4().hex}@example.test"
    viewer_login = f"viewer-{uuid4().hex}@example.test"
    _seed_human_user(human_auth_env, admin_login, role="admin")
    _seed_human_user(human_auth_env, viewer_login, role="business_viewer")
    with _client() as client:
        admin_login_response = client.post(
            "/auth/login", json={"login": admin_login, "password": "correct horse battery"}
        )
        viewer_login_response = client.post(
            "/auth/login", json={"login": viewer_login, "password": "correct horse battery"}
        )
        unauthorized = client.post("/auth/invitations", json={"login": "first@example.test"})
        machine_role_token = client.post(
            "/auth/invitations",
            headers=OPERATOR_AUTH_HEADER,
            json={"login": "first@example.test"},
        )
        forbidden = client.post(
            "/auth/invitations",
            headers={"Authorization": f"Bearer {viewer_login_response.json()['access_token']}"},
            json={"login": "first@example.test"},
        )
        created = client.post(
            "/auth/invitations",
            headers={"Authorization": f"Bearer {admin_login_response.json()['access_token']}"},
            json={"login": " First@Example.Test ", "role": "pipeline_operator"},
        )

    assert unauthorized.status_code == 401
    assert machine_role_token.status_code == 401
    assert forbidden.status_code == 403
    assert created.status_code == 202
    assert created.json() == {"message": "Invitation created"}
    assert "token" not in created.json()


def test_invitation_acceptance_hashes_password_and_is_single_use(human_auth_env):
    invite = _seed_invitation(human_auth_env, "accept@example.test", role="admin")
    with _client() as client:
        accepted = client.post(
            "/auth/invitations/accept",
            json={"token": invite, "password": "correct horse battery"},
        )
        replay = client.post(
            "/auth/invitations/accept",
            json={"token": invite, "password": "correct horse battery"},
        )

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "active"
    assert accepted.json()["role"] == "admin"
    assert "password" not in accepted.json()
    assert "password_hash" not in accepted.json()
    assert replay.status_code == 400
    assert replay.json()["error_code"] == AuthErrorCode.INVITE_INVALID

    engine = sa.create_engine(sync_url(human_auth_env))
    try:
        with engine.connect() as connection:
            user = connection.execute(
                sa.select(crm_users).where(crm_users.c.login == "accept@example.test")
            ).mappings().one()
        assert user["password_hash"].startswith("$argon2id$")
        assert "correct horse battery" not in user["password_hash"]
    finally:
        engine.dispose()


def test_expired_or_unknown_invitation_is_generic(human_auth_env):
    expired = _seed_invitation(
        human_auth_env,
        "expired@example.test",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with _client() as client:
        expired_response = client.post(
            "/auth/invitations/accept",
            json={"token": expired, "password": "correct horse battery"},
        )
        unknown_response = client.post(
            "/auth/invitations/accept",
            json={"token": "test-invite-unknown", "password": "correct horse battery"},
        )

    assert expired_response.status_code == unknown_response.status_code == 400
    assert expired_response.json() == unknown_response.json()


def test_login_and_me_use_minimal_verified_access_token(human_auth_env):
    invite = _seed_invitation(human_auth_env, "login@example.test")
    with _client() as client:
        client.post("/auth/invitations/accept", json={"token": invite, "password": "correct horse battery"})
        login = client.post(
            "/auth/login",
            json={"login": "LOGIN@example.test", "password": "correct horse battery"},
        )
        token = login.json()["access_token"]
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert login.status_code == 200
    assert login.json()["token_type"] == "Bearer"
    assert login.json()["user"]["login"] == "login@example.test"
    assert me.status_code == 200
    assert me.json()["id"] == login.json()["user"]["id"]
    assert "password_hash" not in me.json()

    claims = jwt.decode(token, "checkpoint-2-test-signing-secret", algorithms=["HS256"], options={"verify_aud": False})
    assert set(claims) == {"iss", "aud", "sub", "sid", "jti", "typ", "iat", "nbf", "exp", "ver"}
    assert claims["typ"] == "access"
    assert claims["ver"] == 1


def test_wrong_and_unknown_login_are_indistinguishable(human_auth_env):
    invite = _seed_invitation(human_auth_env, "known@example.test")
    with _client() as client:
        client.post("/auth/invitations/accept", json={"token": invite, "password": "correct horse battery"})
        wrong = client.post(
            "/auth/login", json={"login": "known@example.test", "password": "wrong password"}
        )
        unknown = client.post(
            "/auth/login", json={"login": "unknown@example.test", "password": "wrong password"}
        )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_disabled_user_cannot_login_or_use_existing_access_token(human_auth_env):
    invite = _seed_invitation(human_auth_env, "disabled@example.test")
    with _client() as client:
        client.post("/auth/invitations/accept", json={"token": invite, "password": "correct horse battery"})
        login = client.post(
            "/auth/login", json={"login": "disabled@example.test", "password": "correct horse battery"}
        )
    token = login.json()["access_token"]

    engine = sa.create_engine(sync_url(human_auth_env))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.update(crm_users)
                .where(crm_users.c.login == "disabled@example.test")
                .values(status="disabled", disabled_at=datetime.now(UTC), updated_at=datetime.now(UTC))
            )
    finally:
        engine.dispose()

    with _client() as client:
        rejected_login = client.post(
            "/auth/login", json={"login": "disabled@example.test", "password": "correct horse battery"}
        )
        rejected_me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert rejected_login.status_code == 401
    assert rejected_me.status_code == 401


def test_bad_access_token_claims_are_rejected(human_auth_env):
    invite = _seed_invitation(human_auth_env, "claims@example.test")
    with _client() as client:
        client.post("/auth/invitations/accept", json={"token": invite, "password": "correct horse battery"})
        login = client.post(
            "/auth/login", json={"login": "claims@example.test", "password": "correct horse battery"}
        )
        token = login.json()["access_token"]
        decoded = jwt.decode(token, options={"verify_signature": False})
        decoded["typ"] = "refresh"
        forged = jwt.encode(decoded, "checkpoint-2-test-signing-secret", algorithm="HS256")
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401
    assert response.json()["error_code"] == AuthErrorCode.INVALID_CREDENTIALS
