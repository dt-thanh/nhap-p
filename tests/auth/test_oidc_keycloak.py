"""Kiểm chứng lớp OIDC/Keycloak phía Product/AbsorbIQ — KHÔNG cần Keycloak thật.

Những thứ dễ sai nhất khi tích hợp Keycloak-trong-Docker, và ranh giới bảo mật
cốt lõi của SSO, được khoá lại ở đây:
1. FRONT-CHANNEL giữ host công khai, BACK-CHANNEL viết lại về host nội bộ.
2. Roles đọc được từ `realm_access.roles` (mặc định Keycloak) VÀ top-level
   `roles` (mapper tuỳ biến của realm `p100`).
3. Issuer verification kiểm canonical issuer công khai — token sai iss bị từ chối.
4. RANH GIỚI AUDIENCE — nếu AbsorbIQ chấp nhận một token phát cho Mini CRM, hai
   client Keycloak tách bạch trở thành trang trí — bất kỳ ai lấy được token của
   Mini CRM cũng gọi được API của AbsorbIQ.
5. Vai trò nghiệp vụ chuẩn (CRM.CEO/CRM.ADVISOR/CRM.SALES) resolve đúng, fail
   closed cho vai trò không khớp, PKCE không rò rỉ secret, refresh grant đúng
   hình dạng request.
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.config import get_settings
from src.services import oidc

PUBLIC = "http://localhost:9090/realms/p100"
INTERNAL = "http://keycloak:8080/realms/p100"
ABSORBIQ_CLIENT = "absorbiq-client"
MINICRM_CLIENT = "minicrm-client"

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
    monkeypatch.setenv("OIDC_CLIENT_ID", ABSORBIQ_CLIENT)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "local-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8000/api/v1/auth/callback")
    monkeypatch.setenv("SESSION_SECRET", "x" * 40)
    monkeypatch.setenv("OIDC_PROJECT_SCOPE", json.dumps({"CRM.CEO": "ALL", "AbsorbIQ.Admin": "ALL"}))
    get_settings.cache_clear()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "get_discovery", lambda: DISCOVERY)
    yield
    get_settings.cache_clear()
    oidc.reset_caches()


def _rs256_token(claims, key):
    return jwt.encode(claims, key, algorithm="RS256")


def token_for(private, audience=ABSORBIQ_CLIENT, **over):
    now = int(time.time())
    claims = {
        "iss": PUBLIC,
        "aud": audience,
        "sub": "shared-user-object-id",
        "preferred_username": "an.nguyen@example.com",
        "name": "An Nguyen",
        "realm_access": {"roles": ["CRM.CEO"]},
        "iat": now,
        "exp": now + 600,
    }
    claims.update(over)
    return _rs256_token(claims, private)


def test_oidc_configured_when_env_set(oidc_env):
    assert oidc.oidc_configured() is True


def test_front_channel_keeps_public_host(oidc_env):
    url = oidc.build_authorize_url(state="s", nonce="n", code_challenge="c")
    assert url.startswith(f"{PUBLIC}/protocol/openid-connect/auth?")
    assert "keycloak:8080" not in url  # browser không bao giờ thấy host Docker
    assert oidc.end_session_endpoint().startswith(PUBLIC)


def test_back_channel_rewrites_to_internal(oidc_env):
    assert oidc.token_endpoint() == f"{INTERNAL}/protocol/openid-connect/token"
    assert oidc.jwks_uri() == f"{INTERNAL}/protocol/openid-connect/certs"


def test_verify_reads_realm_access_roles_and_issuer(oidc_env, keypair):
    private, public = keypair
    token = token_for(private, realm_access={"roles": ["admin", "offline_access"]})
    identity = oidc.verify_token(token, public_key=public)
    assert identity.subject == "shared-user-object-id"
    assert "admin" in identity.roles  # đọc từ realm_access.roles
    # resolve_role chấp nhận realm role trùng tên vai trò nội bộ
    assert oidc.resolve_role(identity) == "admin"


def test_verify_reads_top_level_roles_claim(oidc_env, keypair):
    """Realm `p100` gắn một mapper phát roles ra claim top-level `roles` (không
    chỉ `realm_access.roles` mặc định) — `_collect_roles` phải đọc được cả hai."""
    private, public = keypair
    token = token_for(private, realm_access={}, roles=["admin"])
    identity = oidc.verify_token(token, public_key=public)
    assert "admin" in identity.roles
    assert oidc.resolve_role(identity) == "admin"


def test_verify_rejects_wrong_issuer(oidc_env, keypair):
    private, public = keypair
    with pytest.raises(Exception):
        oidc.verify_token(token_for(private, iss="http://evil/realms/p100"), public_key=public)


def test_expired_token_rejected(oidc_env, keypair):
    private, public = keypair
    now = int(time.time())
    with pytest.raises(HTTPException) as exc:
        oidc.verify_token(token_for(private, exp=now - 60, iat=now - 3600), public_key=public)
    assert exc.value.detail["error_code"] == "TOKEN_EXPIRED"


def test_forged_signature_rejected(oidc_env, keypair):
    _, public = keypair
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException):
        oidc.verify_token(token_for(attacker), public_key=public)


def test_token_for_minicrm_audience_is_rejected(oidc_env, keypair):
    """Đây là RANH GIỚI của SSO. Cùng người dùng, cùng realm, cùng khoá ký — nhưng
    token phát cho Mini CRM KHÔNG được dùng ở AbsorbIQ. SSO nghĩa là không phải
    đăng nhập lại, KHÔNG phải dùng chung một token cho mọi dịch vụ."""
    private, public = keypair
    with pytest.raises(HTTPException) as exc:
        oidc.verify_token(token_for(private, audience=MINICRM_CLIENT), public_key=public)
    assert exc.value.status_code == 401


def test_self_registered_viewer_is_not_admin(oidc_env, keypair):
    private, public = keypair
    identity = oidc.verify_token(
        token_for(private, realm_access={"roles": ["business_viewer"]}), public_key=public
    )
    assert oidc.resolve_role(identity) == "business_viewer"


def test_unmapped_role_is_403_not_default(oidc_env, keypair):
    private, public = keypair
    identity = oidc.verify_token(
        token_for(private, realm_access={"roles": ["Unrelated.Role"]}), public_key=public
    )
    with pytest.raises(HTTPException) as exc:
        oidc.resolve_role(identity)
    assert exc.value.status_code == 403


# --- CEO/ADVISOR/SALES canonical App Roles (shared with Mini CRM) -----------
#
# These resolve WITHOUT OIDC_ROLE_MAP listing them — `oidc.CANONICAL_APP_ROLES`
# recognizes them by default, additively, on top of whatever an operator
# configures.


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
def test_canonical_app_role_resolves_without_a_role_map_entry(oidc_env, keypair, app_role, expected_internal_role):
    private, public = keypair
    identity = oidc.verify_token(token_for(private, realm_access={"roles": [app_role]}), public_key=public)
    assert oidc.resolve_role(identity) == expected_internal_role


def test_ceo_and_absorbiq_admin_receive_all_scope(oidc_env, keypair):
    private, public = keypair
    for app_role in ("CRM.CEO", "AbsorbIQ.Admin"):
        identity = oidc.verify_token(token_for(private, realm_access={"roles": [app_role]}), public_key=public)
        assert oidc.resolve_role(identity) == "admin"
        assert oidc.resolve_scope(identity) == "ALL"


def test_canonical_app_roles_are_not_overridable_by_a_compatible_role_map_entry(oidc_env, keypair, monkeypatch):
    """Một OIDC_ROLE_MAP có thể thêm khoá claim/nhóm tuỳ biến tự do — nó chỉ
    không được định nghĩa lại một khoá chuẩn thành giá trị KHÁC (bị chặn lúc
    khởi động bởi `src/config.py::_reject_conflicting_canonical_role_map`, kiểm
    riêng ở `test_config_safety.py`). Ở đây: một entry TRÙNG giá trị chuẩn vô hại."""
    monkeypatch.setenv("OIDC_ROLE_MAP", json.dumps({"custom.group": "admin", "CRM.CEO": "admin"}))
    get_settings.cache_clear()
    private, public = keypair
    identity = oidc.verify_token(token_for(private, realm_access={"roles": ["CRM.CEO"]}), public_key=public)
    assert oidc.resolve_role(identity) == "admin"


def test_highest_canonical_role_wins_when_multiple_are_present(oidc_env, keypair):
    private, public = keypair
    identity = oidc.verify_token(
        token_for(private, realm_access={"roles": ["CRM.SALES", "CRM.CEO"]}), public_key=public
    )
    assert oidc.resolve_role(identity) == "admin"


def test_session_cookie_roundtrip(oidc_env, keypair):
    private, public = keypair
    identity = oidc.verify_token(token_for(private), public_key=public)
    token = oidc.issue_session(identity, role="admin", scope="ALL")
    claims = oidc.read_session(token)
    assert claims["role"] == "admin" and claims["iss"] == "absorbiq"


def test_minicrm_session_cookie_is_not_valid_here(oidc_env, keypair):
    """Cookie phiên của Mini CRM (`iss: minicrm`) không được AbsorbIQ chấp nhận,
    kể cả khi hai bên vô tình dùng chung `SESSION_SECRET`."""
    private, public = keypair
    identity = oidc.verify_token(token_for(private), public_key=public)
    foreign = jwt.encode(
        {
            "sub": identity.subject,
            "role": "admin",
            "scope": "ALL",
            "iss": "minicrm",
            "exp": int(time.time()) + 600,
        },
        get_settings().session_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        oidc.read_session(foreign)
    assert exc.value.detail["error_code"] == "INVALID_SESSION"


def test_authorize_url_never_leaks_secret(oidc_env):
    _, challenge = oidc.new_pkce_pair()
    url = oidc.build_authorize_url(state="s", nonce="n", code_challenge=challenge)
    assert "local-secret" not in url
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
async def test_refresh_tokens_uses_refresh_grant(oidc_env, monkeypatch):
    """`refresh_tokens` phải POST tới token endpoint bằng grant_type=refresh_token,
    kèm client_id/secret của AbsorbIQ — đây là mảnh khiến `/auth/refresh` gia hạn
    được phiên thay vì đá người dùng ra /login mỗi lần access token hết hạn."""
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"id_token": "new-id", "refresh_token": "new-rt", "access_token": "new-at"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            captured["url"] = url
            captured["data"] = data
            return _Resp()

    monkeypatch.setattr(oidc.httpx, "AsyncClient", _Client)
    tokens = await oidc.refresh_tokens("old-refresh-token")

    assert tokens["id_token"] == "new-id"
    assert captured["data"]["grant_type"] == "refresh_token"
    assert captured["data"]["refresh_token"] == "old-refresh-token"
    assert captured["data"]["client_id"] == ABSORBIQ_CLIENT


@pytest.mark.asyncio
async def test_refresh_tokens_maps_failure_to_session_expired(oidc_env, monkeypatch):
    """Keycloak từ chối refresh (refresh token hết hạn/thu hồi) ⇒ 401
    SESSION_EXPIRED, KHÔNG rò thân lỗi thô của provider ra client."""

    class _Resp:
        status_code = 400

        def json(self):
            return {"error": "invalid_grant"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return _Resp()

    monkeypatch.setattr(oidc.httpx, "AsyncClient", _Client)
    with pytest.raises(HTTPException) as exc:
        await oidc.refresh_tokens("revoked-token")
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == "SESSION_EXPIRED"
