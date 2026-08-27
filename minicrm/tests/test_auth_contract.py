"""Checkpoint 1: human-auth contracts without enabling the lifecycle."""

import json
from uuid import UUID, uuid4

import pytest
from app.auth_contract import AccessTokenClaims, AuthErrorCode, CrmRole, UserStatus
from app.config import Settings
from pydantic import ValidationError


def _claims(**overrides):
    values = {
        "iss": "http://localhost:8000",
        "aud": "absorbiq-api",
        "sub": uuid4(),
        "sid": uuid4(),
        "jti": uuid4(),
        "typ": "access",
        "iat": 100,
        "nbf": 100,
        "exp": 1_000,
        "ver": 1,
    }
    values.update(overrides)
    return values


def test_access_token_claims_are_minimal_and_typed():
    claims = AccessTokenClaims.model_validate(_claims())
    assert isinstance(claims.sub, UUID)
    assert claims.typ == "access"
    assert claims.ver == 1
    assert {"role", "project_scope", "permissions", "email", "password"}.isdisjoint(AccessTokenClaims.model_fields)


@pytest.mark.parametrize("field,value", [("typ", "refresh"), ("ver", 2)])
def test_access_token_claims_reject_wrong_fixed_claims(field, value):
    with pytest.raises(ValidationError):
        AccessTokenClaims.model_validate(_claims(**{field: value}))


def test_access_token_claims_reject_invalid_lifetime():
    with pytest.raises(ValidationError):
        AccessTokenClaims.model_validate(_claims(nbf=2_000))


def test_config_contract_uses_explicit_names_and_safe_defaults(monkeypatch):
    monkeypatch.setenv("MINICRM_AUTH_ISSUER", "https://issuer.example.test")
    monkeypatch.setenv("MINICRM_AUTH_AUDIENCE", "absorbiq-api")
    monkeypatch.setenv("MINICRM_AUTH_ALGORITHM", "HS256")
    monkeypatch.setenv("MINICRM_ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("MINICRM_REFRESH_TOKEN_TTL_SECONDS", "2592000")
    settings = Settings(_env_file=None)
    assert settings.auth_issuer == "https://issuer.example.test"
    assert settings.auth_audience == "absorbiq-api"
    assert settings.auth_algorithm == "HS256"
    assert settings.access_token_ttl_seconds == 900
    assert settings.refresh_token_ttl_seconds == 2_592_000
    assert settings.dev_auth_bypass is False


def test_config_rejects_unapproved_algorithm_and_ttl(monkeypatch):
    monkeypatch.setenv("MINICRM_AUTH_ALGORITHM", "RS256")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

    monkeypatch.delenv("MINICRM_AUTH_ALGORITHM")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, access_token_ttl_seconds=0)


def test_secret_configuration_is_not_exposed_by_settings_repr():
    settings = Settings(_env_file=None, auth_signing_secret="checkpoint-test-secret")
    assert "checkpoint-test-secret" not in repr(settings)
    assert "checkpoint-test-secret" not in str(settings)


def test_identity_and_role_vocabularies_are_explicit():
    assert {item.value for item in UserStatus} == {"invited", "active", "disabled"}
    assert {item.value for item in CrmRole} == {"business_viewer", "pipeline_operator", "admin"}
    assert AuthErrorCode.INVALID_CREDENTIALS.value == "INVALID_CREDENTIALS"


# --- MINICRM_OIDC_ROLE_MAP: cannot redefine CRM.CEO/CRM.ADVISOR/CRM.SALES ----
#
# These are the canonical business roles shared with Product/AbsorbIQ
# (`app/session.py::CANONICAL_APP_ROLES`) — fixed in code, so a misconfigured
# role map can't silently change what "CEO" means.


def test_role_map_empty_is_allowed():
    settings = Settings(_env_file=None, oidc_role_map="")
    assert settings.oidc_role_map.get_secret_value() == ""


def test_role_map_with_unrelated_keys_is_allowed():
    settings = Settings(
        _env_file=None, oidc_role_map=json.dumps({"custom.group": "admin", "CRM.Operator": "pipeline_operator"})
    )
    assert settings.oidc_role_map.get_secret_value()


@pytest.mark.parametrize(
    ("key", "canonical_value"),
    [
        ("CRM.CEO", "admin"),
        ("CRM.Admin", "admin"),
        ("CRM.ADVISOR", "business_viewer"),
        ("CRM.Viewer", "business_viewer"),
        ("CRM.SALES", "pipeline_operator"),
        ("CRM.Operator", "pipeline_operator"),
    ],
)
def test_role_map_matching_the_canonical_value_is_allowed(key, canonical_value):
    settings = Settings(_env_file=None, oidc_role_map=json.dumps({key: canonical_value}))
    assert settings.oidc_role_map.get_secret_value()


@pytest.mark.parametrize(
    ("key", "wrong_value"),
    [
        ("CRM.CEO", "business_viewer"),
        ("CRM.Admin", "business_viewer"),
        ("CRM.ADVISOR", "admin"),
        ("CRM.Viewer", "admin"),
        ("CRM.SALES", "admin"),
        ("CRM.Operator", "admin"),
    ],
)
def test_role_map_redefining_a_canonical_key_is_rejected_at_startup(key, wrong_value):
    with pytest.raises(ValidationError, match=key):
        Settings(_env_file=None, oidc_role_map=json.dumps({key: wrong_value}))


# --- MINICRM_AUTH_PROVIDER: only "keycloak" is accepted ----------------------


def test_auth_provider_defaults_to_keycloak():
    assert Settings(_env_file=None).auth_provider == "keycloak"


def test_auth_provider_rejects_unsupported_value():
    """No Entra left to select — an unrecognized value (including "entra") is
    rejected by Pydantic at startup, never silently activating another path."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_provider="entra")
