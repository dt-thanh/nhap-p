"""Xác thực Keycloak/OIDC phía Mini CRM — kiểm OFFLINE, không cần Keycloak thật.

Toàn bộ file này chạy được mà KHÔNG có Keycloak thật, không có mạng, không có
PostgreSQL: một cặp khoá RSA sinh tại chỗ đóng vai khoá ký của Keycloak và được
tiêm thẳng vào `oidc.verify_token(public_key=...)`. Cái duy nhất KHÔNG kiểm được
ở đây là việc realm thật có phát đúng hình dạng claim này không — đó là phần
`RUNTIME_VERIFICATION_REQUIRED` (xem tests/e2e/test_keycloak_two_stack_flow.py).

Điều đang được kiểm là phần dễ sai nhất và nguy hiểm nhất khi sai: token hết
hạn, sai audience (RANH GIỚI SSO giữa Mini CRM và AbsorbIQ), sai issuer, ký bằng
khoá KHÁC, và `alg: none` — mỗi cái phải bị TỪ CHỐI, không cái nào được lọt.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import oidc  # noqa: E402
from app import session as session_mod
from app.config import get_settings  # noqa: E402
from fastapi import HTTPException  # noqa: E402

PUBLIC = "http://localhost:9090/realms/p100"
INTERNAL = "http://keycloak:8080/realms/p100"
MINICRM_CLIENT = "minicrm-client"
ABSORBIQ_CLIENT = "absorbiq-client"


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture(autouse=True)
def oidc_env(monkeypatch):
    monkeypatch.setenv("MINICRM_OIDC_ISSUER", PUBLIC)
    monkeypatch.setenv("MINICRM_OIDC_INTERNAL_BASE_URL", INTERNAL)
    monkeypatch.setenv("MINICRM_OIDC_CLIENT_ID", MINICRM_CLIENT)
    monkeypatch.setenv("MINICRM_OIDC_CLIENT_SECRET", "test-secret-not-real")
    monkeypatch.setenv("MINICRM_OIDC_REDIRECT_URI", "http://localhost:8100/auth/callback")
    monkeypatch.setenv("MINICRM_OIDC_POST_LOGOUT_REDIRECT_URI", "http://localhost:5174/login")
    monkeypatch.setenv("MINICRM_SESSION_SECRET", "unit-test-session-secret")
    monkeypatch.setenv(
        "MINICRM_OIDC_ROLE_MAP",
        json.dumps({"CRM.Admin": "admin", "CRM.Operator": "pipeline_operator"}),
    )
    monkeypatch.setenv(
        "MINICRM_OIDC_PROJECT_SCOPE",
        json.dumps({"CRM.CEO": "ALL", "CRM.Admin": "ALL", "CRM.Operator": ["P-0001"]}),
    )
    get_settings.cache_clear()
    oidc.reset_caches()
    yield
    get_settings.cache_clear()


def make_token(private, audience=MINICRM_CLIENT, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": PUBLIC,
        "aud": audience,
        "sub": "user-object-id-123",
        "preferred_username": "an.nguyen@example.com",
        "name": "An Nguyen",
        "realm_access": {"roles": ["CRM.Operator"]},
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256")


# --- Đường hạnh phúc --------------------------------------------------------


def test_valid_token_yields_identity(keypair):
    private, public = keypair
    identity = oidc.verify_token(make_token(private), public_key=public)
    assert identity.subject == "user-object-id-123"
    assert identity.email == "an.nguyen@example.com"
    assert "CRM.Operator" in identity.roles


def test_top_level_roles_claim_also_read(keypair):
    """Realm `p100` gắn một mapper phát roles ra claim top-level `roles` (không
    chỉ `realm_access.roles` mặc định) — `_collect_roles` phải đọc được cả hai."""
    private, public = keypair
    token = make_token(private, realm_access={}, roles=["CRM.Operator"])
    identity = oidc.verify_token(token, public_key=public)
    assert "CRM.Operator" in identity.roles


def test_logout_url_uses_keycloak_rp_initiated_logout_parameters(monkeypatch):
    monkeypatch.setattr(
        oidc,
        "get_discovery",
        lambda: {"end_session_endpoint": f"{PUBLIC}/protocol/openid-connect/logout"},
    )

    url = oidc.build_logout_url(id_token_hint="test-id-token")

    assert url is not None
    parsed = urlparse(url)
    assert parsed.path.endswith("/protocol/openid-connect/logout")
    query = parse_qs(parsed.query)
    assert query == {
        "client_id": [MINICRM_CLIENT],
        "id_token_hint": ["test-id-token"],
        "post_logout_redirect_uri": ["http://localhost:5174/login"],
    }


# --- RANH GIỚI AUDIENCE (SSO giữa Mini CRM và AbsorbIQ) ----------------------


def test_token_for_absorbiq_audience_is_rejected(keypair):
    """Cùng người dùng, cùng realm, cùng khoá ký — nhưng token phát cho AbsorbIQ
    KHÔNG được dùng ở Mini CRM. SSO nghĩa là không phải đăng nhập lại, KHÔNG
    phải dùng chung một token cho mọi dịch vụ."""
    private, public = keypair
    with pytest.raises(HTTPException) as exc:
        oidc.verify_token(make_token(private, audience=ABSORBIQ_CLIENT), public_key=public)
    assert exc.value.status_code == 401


# --- Những thứ PHẢI bị từ chối ---------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "TOKEN_EXPIRED"),
        ({"aud": "some-other-application"}, "INVALID_TOKEN"),
        ({"iss": "http://evil/realms/p100"}, "INVALID_TOKEN"),
    ],
)
def test_rejects_bad_claims(keypair, overrides, code):
    private, public = keypair
    with pytest.raises(HTTPException) as exc:
        oidc.verify_token(make_token(private, **overrides), public_key=public)
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == code


def test_rejects_token_signed_by_another_key(keypair):
    """Chữ ký hợp lệ về CÚ PHÁP nhưng của khoá khác — phải trượt."""
    _, public = keypair
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as exc:
        oidc.verify_token(make_token(attacker), public_key=public)
    assert exc.value.status_code == 401


def test_rejects_alg_none(keypair):
    """`alg: none` là lỗ JWT kinh điển: bỏ chữ ký, giữ nguyên claim. Cấu hình
    `oidc_allowed_algorithms` phải chặn nó ngay cả khi mọi claim đều đúng."""
    _, public = keypair
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "iss": PUBLIC,
            "aud": MINICRM_CLIENT,
            "sub": "s",
            "realm_access": {"roles": ["CRM.Admin"]},
            "exp": now + 600,
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(HTTPException):
        oidc.verify_token(unsigned, public_key=public)


def test_rejects_token_without_required_claims(keypair):
    private, public = keypair
    now = int(time.time())
    token = jwt.encode({"aud": MINICRM_CLIENT, "sub": "s", "exp": now + 600}, private, algorithm="RS256")
    with pytest.raises(HTTPException):
        oidc.verify_token(token, public_key=public)


# --- Ánh xạ vai trò (fail-closed) ------------------------------------------


def test_role_and_scope_mapping(keypair):
    private, public = keypair
    identity = oidc.verify_token(make_token(private), public_key=public)
    role = session_mod.resolve_role(identity)
    assert role == "pipeline_operator"
    assert session_mod.resolve_scope(identity, role) == ["P-0001"]


def test_highest_role_wins_when_multiple_claims(keypair):
    private, public = keypair
    identity = oidc.verify_token(
        make_token(private, realm_access={"roles": ["CRM.Operator", "CRM.Admin"]}), public_key=public
    )
    assert session_mod.resolve_role(identity) == "admin"


def test_unmapped_user_gets_403_not_a_default_role(keypair):
    """Xác thực THÀNH CÔNG nhưng chưa được gán role ⇒ 403, KHÔNG phải "mặc định
    business_viewer". Một tài khoản bất kỳ trong realm không đương nhiên được
    nhìn dữ liệu bán hàng."""
    private, public = keypair
    identity = oidc.verify_token(make_token(private, realm_access={"roles": ["Some.Other.Role"]}), public_key=public)
    with pytest.raises(HTTPException) as exc:
        session_mod.resolve_role(identity)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "NO_ROLE_ASSIGNED"


# --- CEO/ADVISOR/SALES canonical App Roles (shared with Product/AbsorbIQ) ---
#
# These resolve WITHOUT `oidc_env`'s MINICRM_OIDC_ROLE_MAP listing them —
# `session_mod.CANONICAL_APP_ROLES` recognizes them by default, additively, on
# top of whatever an operator configures (here: CRM.Admin/CRM.Operator, a
# distinct legacy vocabulary — no collision).


@pytest.mark.parametrize(
    ("app_role", "expected_internal_role"),
    [
        ("CRM.CEO", "admin"),
        ("CRM.Admin", "admin"),
        ("CRM.ADVISOR", "business_viewer"),
        ("CRM.Viewer", "business_viewer"),
        ("CRM.SALES", "pipeline_operator"),
        ("CRM.Operator", "pipeline_operator"),
    ],
)
def test_canonical_app_role_resolves_without_a_role_map_entry(keypair, app_role, expected_internal_role):
    private, public = keypair
    identity = oidc.verify_token(make_token(private, realm_access={"roles": [app_role]}), public_key=public)
    assert session_mod.resolve_role(identity) == expected_internal_role


def test_ceo_receives_all_scope(keypair):
    private, public = keypair
    identity = oidc.verify_token(make_token(private, realm_access={"roles": ["CRM.CEO"]}), public_key=public)
    role = session_mod.resolve_role(identity)
    assert role == "admin"
    assert session_mod.resolve_scope(identity, role) == "ALL"


def test_highest_canonical_role_wins_when_multiple_are_present(keypair):
    private, public = keypair
    identity = oidc.verify_token(
        make_token(private, realm_access={"roles": ["CRM.SALES", "CRM.CEO"]}), public_key=public
    )
    assert session_mod.resolve_role(identity) == "admin"


# --- Phiên -------------------------------------------------------------------


def test_session_roundtrip(keypair):
    private, public = keypair
    identity = oidc.verify_token(make_token(private), public_key=public)
    token = session_mod.issue_session(identity, role="admin", scope="ALL", refresh_token="rt-123")
    claims = session_mod.read_session(token)
    assert claims["role"] == "admin"
    assert claims["scope"] == "ALL"
    assert claims["rt"] == "rt-123"


def test_session_signed_with_other_secret_is_rejected(monkeypatch, keypair):
    private, public = keypair
    identity = oidc.verify_token(make_token(private), public_key=public)
    token = session_mod.issue_session(identity, role="admin", scope="ALL")
    monkeypatch.setenv("MINICRM_SESSION_SECRET", "a-completely-different-secret")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        session_mod.read_session(token)
    assert exc.value.detail["error_code"] == "INVALID_SESSION"


# --- PKCE / URL --------------------------------------------------------------


def test_pkce_challenge_is_s256_of_verifier():
    import base64
    import hashlib

    verifier, challenge = oidc.new_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_authorize_url_has_no_secret_and_carries_pkce(monkeypatch):
    monkeypatch.setattr(
        oidc,
        "get_discovery",
        lambda: {"authorization_endpoint": f"{PUBLIC}/protocol/openid-connect/auth"},
    )
    verifier, challenge = oidc.new_pkce_pair()
    url = oidc.build_authorize_url(state="st", nonce="nc", code_challenge=challenge)
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "test-secret-not-real" not in url  # client_secret KHÔNG bao giờ ở URL
    assert verifier not in url  # verifier cũng không — chỉ challenge được gửi
