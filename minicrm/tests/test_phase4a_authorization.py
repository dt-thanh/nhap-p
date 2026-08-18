"""Phase 4A human-principal and authorization-boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.auth_contract import AuthenticatedHumanPrincipal, AuthErrorCode, CrmRole, UserStatus
from app.human_auth import PASSWORD_HASHER, HumanAuthError, authorize_human_role
from app.main import app
from app.models import crm_users
from fastapi.testclient import TestClient

from tests.conftest import sync_url

PASSWORD = "phase-four-a-password"


def _seed_user(url: str, login: str, *, role: str, status: str = "active") -> None:
    now = datetime.now(UTC)
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.insert(crm_users).values(
                    id=uuid4(),
                    login=login,
                    email=None,
                    password_hash=PASSWORD_HASHER.hash(PASSWORD),
                    status=status,
                    role=role,
                    auth_version=1,
                    created_at=now,
                    updated_at=now,
                    disabled_at=now if status == "disabled" else None,
                )
            )
    finally:
        engine.dispose()


@pytest.fixture
def phase4a_env(crm_app, monkeypatch):
    from app.config import get_settings
    from app.db import get_engine, get_session_factory

    monkeypatch.setenv("MINICRM_AUTH_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("MINICRM_AUTH_AUDIENCE", "absorbiq-api")
    monkeypatch.setenv("MINICRM_AUTH_ALGORITHM", "HS256")
    monkeypatch.setenv("MINICRM_AUTH_SIGNING_SECRET", "phase-4a-test-signing-secret")
    for name in (
        "MINICRM_AUTH_ADMIN_TOKEN",
        "MINICRM_AUTH_PIPELINE_OPERATOR_TOKEN",
        "MINICRM_AUTH_BUSINESS_VIEWER_TOKEN",
    ):
        monkeypatch.setenv(name, "")

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    admin_login = f"phase4a-admin-{uuid4().hex}@example.test"
    viewer_login = f"phase4a-viewer-{uuid4().hex}@example.test"
    inactive_login = f"phase4a-inactive-{uuid4().hex}@example.test"
    _seed_user(crm_app, admin_login, role="admin")
    _seed_user(crm_app, viewer_login, role="business_viewer")
    _seed_user(crm_app, inactive_login, role="admin", status="disabled")
    try:
        yield {
            "url": crm_app,
            "admin_login": admin_login,
            "viewer_login": viewer_login,
            "inactive_login": inactive_login,
        }
    finally:
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_session_factory.cache_clear()


def _login(client: TestClient, login: str) -> dict:
    response = client.post("/auth/login", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()


def _header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_human_admin_is_the_only_invitation_authority(phase4a_env):
    with TestClient(app) as client:
        admin = _login(client, phase4a_env["admin_login"])
        viewer = _login(client, phase4a_env["viewer_login"])
        anonymous = client.post("/auth/invitations", json={"login": "new@example.test"})
        denied = client.post(
            "/auth/invitations",
            headers=_header(viewer["access_token"]),
            json={"login": "new@example.test", "role": "admin"},
        )
        created = client.post(
            "/auth/invitations",
            headers=_header(admin["access_token"]),
            json={"login": "new@example.test", "role": "admin"},
        )

    assert anonymous.status_code == 401
    assert denied.status_code == 403
    assert created.status_code == 202


def test_global_visibility_allows_active_human_reads(phase4a_env):
    with TestClient(app) as client:
        admin = _login(client, phase4a_env["admin_login"])
        headers = _header(admin["access_token"])
        responses = [
            client.get("/projects", headers=headers),
            client.get("/projects/BOOTSTRAP-PROJECT", headers=headers),
            client.get("/areas", headers=headers),
            client.get("/areas/BOOTSTRAP-AREA", headers=headers),
            client.get("/units", headers=headers),
            client.get("/deals", headers=headers),
        ]

    assert all(response.status_code == 200 for response in responses)


def test_global_visibility_rejects_anonymous_resource_reads(phase4a_env):
    with TestClient(app) as client:
        response = client.get("/projects")
    assert response.status_code == 401


def test_inactive_human_session_is_rejected(phase4a_env):
    with TestClient(app) as client:
        admin = _login(client, phase4a_env["admin_login"])

    engine = sa.create_engine(sync_url(phase4a_env["url"]))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.update(crm_users)
                .where(crm_users.c.login == phase4a_env["admin_login"])
                .values(status="disabled", disabled_at=datetime.now(UTC), updated_at=datetime.now(UTC))
            )
    finally:
        engine.dispose()

    with TestClient(app) as client:
        response = client.post(
            "/auth/invitations", headers=_header(admin["access_token"]), json={"login": "new@example.test"}
        )
        read_response = client.get("/projects", headers=_header(admin["access_token"]))
    assert response.status_code == 401
    assert response.json()["error_code"] == AuthErrorCode.INVALID_CREDENTIALS
    assert read_response.status_code == 401


def test_unknown_or_missing_role_fails_closed():
    principal = AuthenticatedHumanPrincipal(
        user_id=uuid4(), session_id=uuid4(), role="unknown", status=UserStatus.ACTIVE
    )
    with pytest.raises(HumanAuthError) as unknown:
        authorize_human_role(principal, CrmRole.ADMIN)

    missing = AuthenticatedHumanPrincipal(
        user_id=uuid4(), session_id=uuid4(), role="", status=UserStatus.ACTIVE
    )
    with pytest.raises(HumanAuthError) as absent:
        authorize_human_role(missing, CrmRole.ADMIN)

    assert unknown.value.error_code == absent.value.error_code == AuthErrorCode.AUTHORIZATION_DENIED
    assert unknown.value.status_code == absent.value.status_code == 403


def test_machine_credentials_cannot_cross_human_boundary(phase4a_env):
    with TestClient(app) as client:
        api_key_attempt = client.post(
            "/auth/invitations",
            headers={"X-API-Key": "machine-key-is-not-a-human-credential"},
            json={"login": "new@example.test"},
        )
        api_key_read_attempt = client.get(
            "/projects", headers={"X-API-Key": "machine-key-is-not-a-human-credential"}
        )
        human = _login(client, phase4a_env["admin_login"])
        human_on_static_write = client.post(
            "/areas",
            headers=_header(human["access_token"]),
            json={
                "external_project_id": "BOOTSTRAP-PROJECT",
                "area_name": "Unauthorized",
                "unit_type": "2PN",
                "bedrooms": 2,
                "area_sqm": 60,
                "total_units": 10,
            },
        )

    assert api_key_attempt.status_code == 401
    assert api_key_read_attempt.status_code == 401
    assert human_on_static_write.status_code == 503


def test_request_identity_fields_cannot_change_authenticated_principal(phase4a_env):
    with TestClient(app) as client:
        admin = _login(client, phase4a_env["admin_login"])
        me = client.get(
            "/auth/me?user_id=00000000-0000-0000-0000-000000000000&project_id=attacker-project",
            headers=_header(admin["access_token"]),
        )
        extra = client.post(
            "/auth/invitations",
            headers=_header(admin["access_token"]),
            json={"login": "new@example.test", "role": "admin", "user_id": str(uuid4())},
        )

    assert me.status_code == 200
    assert me.json()["login"] == phase4a_env["admin_login"]
    assert extra.status_code == 422


def test_static_auth_tokens_remain_unconfigured(phase4a_env):
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


def test_global_visibility_is_rejected_in_production():
    from app.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(app_env="production", authorization_mode="global_visibility")
    assert Settings(app_env="development", authorization_mode="global_visibility").authorization_mode == (
        "global_visibility"
    )


def test_human_principal_does_not_add_sync_routes():
    paths = {route.path for route in app.routes if getattr(route, "path", "")}
    assert not any(path.startswith("/api/v1/sync") for path in paths)
