"""Xác thực Entra — kiểm OFFLINE, không cần tenant thật.

Toàn bộ file này chạy được mà KHÔNG có `ENTRA_TENANT_ID` thật, không có mạng,
không có PostgreSQL: một cặp khoá RSA sinh tại chỗ đóng vai khoá ký của Entra và
được tiêm thẳng vào `entra.verify_token(public_key=...)`. Cái duy nhất KHÔNG kiểm
được ở đây là việc tenant thật có phát đúng hình dạng claim này không — đó là
phần `RUNTIME_VERIFICATION_REQUIRED`.

Điều đang được kiểm là phần dễ sai nhất và nguy hiểm nhất khi sai: token hết
hạn, sai audience, sai issuer, ký bằng khoá KHÁC, và `alg: none` — mỗi cái phải
bị TỪ CHỐI, không cái nào được lọt.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import entra, session as session_mod  # noqa: E402
from app.config import get_settings  # noqa: E402
from fastapi import HTTPException  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture(autouse=True)
def entra_env(monkeypatch):
    monkeypatch.setenv("MINICRM_ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("MINICRM_ENTRA_CLIENT_ID", CLIENT)
    monkeypatch.setenv("MINICRM_ENTRA_CLIENT_SECRET", "test-secret-not-real")
    monkeypatch.setenv("MINICRM_ENTRA_REDIRECT_URI", "http://localhost:8100/auth/callback")
    monkeypatch.setenv("MINICRM_SESSION_SECRET", "unit-test-session-secret")
    monkeypatch.setenv(
        "MINICRM_ENTRA_ROLE_MAP",
        json.dumps({"CRM.Admin": "admin", "CRM.Operator": "pipeline_operator"}),
    )
    monkeypatch.setenv(
        "MINICRM_ENTRA_PROJECT_SCOPE",
        json.dumps({"CRM.Admin": "ALL", "CRM.Operator": ["P-0001"]}),
    )
    get_settings.cache_clear()
    entra.reset_jwks_cache()
    yield
    get_settings.cache_clear()


def make_token(private, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "sub": "subject-abc",
        "oid": "user-object-id-123",
        "preferred_username": "an.nguyen@example.com",
        "name": "An Nguyen",
        "roles": ["CRM.Operator"],
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256")


# --- Đường hạnh phúc --------------------------------------------------------


def test_valid_token_yields_identity(keypair):
    private, public = keypair
    identity = entra.verify_token(make_token(private), public_key=public)
    assert identity.subject == "user-object-id-123"  # `oid`, KHÔNG phải `sub`
    assert identity.email == "an.nguyen@example.com"
    assert "CRM.Operator" in identity.roles


def test_sts_windows_issuer_also_accepted(keypair):
    """Entra phát `iss` ở hai dạng tuỳ endpoint version — cả hai phải qua."""
    private, public = keypair
    token = make_token(private, iss=f"https://sts.windows.net/{TENANT}/")
    assert entra.verify_token(token, public_key=public).subject == "user-object-id-123"


# --- Những thứ PHẢI bị từ chối ---------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"exp": int(time.time()) - 3600, "nbf": int(time.time()) - 7200}, "TOKEN_EXPIRED"),
        ({"aud": "some-other-application"}, "INVALID_TOKEN"),
        ({"iss": "https://login.microsoftonline.com/other-tenant/v2.0"}, "INVALID_TOKEN"),
    ],
)
def test_rejects_bad_claims(keypair, overrides, code):
    private, public = keypair
    with pytest.raises(HTTPException) as exc:
        entra.verify_token(make_token(private, **overrides), public_key=public)
    assert exc.value.status_code == 401
    assert exc.value.detail["error_code"] == code


def test_rejects_token_signed_by_another_key(keypair):
    """Chữ ký hợp lệ về CÚ PHÁP nhưng của khoá khác — phải trượt."""
    _, public = keypair
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as exc:
        entra.verify_token(make_token(attacker), public_key=public)
    assert exc.value.status_code == 401


def test_rejects_alg_none(keypair):
    """`alg: none` là lỗ JWT kinh điển: bỏ chữ ký, giữ nguyên claim. Cấu hình
    `entra_allowed_algorithms` phải chặn nó ngay cả khi mọi claim đều đúng."""
    _, public = keypair
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": CLIENT,
            "sub": "s",
            "oid": "attacker",
            "roles": ["CRM.Admin"],
            "exp": now + 600,
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(HTTPException):
        entra.verify_token(unsigned, public_key=public)


def test_rejects_token_without_required_claims(keypair):
    private, public = keypair
    now = int(time.time())
    token = jwt.encode({"aud": CLIENT, "sub": "s", "exp": now + 600}, private, algorithm="RS256")
    with pytest.raises(HTTPException):
        entra.verify_token(token, public_key=public)


# --- Ánh xạ vai trò (fail-closed) ------------------------------------------


def test_role_and_scope_mapping(keypair):
    private, public = keypair
    identity = entra.verify_token(make_token(private), public_key=public)
    role = session_mod.resolve_role(identity)
    assert role == "pipeline_operator"
    assert session_mod.resolve_scope(identity, role) == ["P-0001"]


def test_highest_role_wins_when_multiple_claims(keypair):
    private, public = keypair
    identity = entra.verify_token(
        make_token(private, roles=["CRM.Operator", "CRM.Admin"]), public_key=public
    )
    assert session_mod.resolve_role(identity) == "admin"


def test_unmapped_user_gets_403_not_a_default_role(keypair):
    """Xác thực THÀNH CÔNG nhưng chưa được gán app role ⇒ 403, KHÔNG phải
    "mặc định business_viewer". Một tài khoản bất kỳ trong tenant không đương
    nhiên được nhìn dữ liệu bán hàng."""
    private, public = keypair
    identity = entra.verify_token(make_token(private, roles=["Some.Other.Role"]), public_key=public)
    with pytest.raises(HTTPException) as exc:
        session_mod.resolve_role(identity)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "NO_ROLE_ASSIGNED"


# --- Phiên -------------------------------------------------------------------


def test_session_roundtrip(keypair):
    private, public = keypair
    identity = entra.verify_token(make_token(private), public_key=public)
    token = session_mod.issue_session(identity, role="admin", scope="ALL", refresh_token="rt-123")
    claims = session_mod.read_session(token)
    assert claims["role"] == "admin"
    assert claims["scope"] == "ALL"
    assert claims["rt"] == "rt-123"


def test_session_signed_with_other_secret_is_rejected(monkeypatch, keypair):
    private, public = keypair
    identity = entra.verify_token(make_token(private), public_key=public)
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

    verifier, challenge = entra.new_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_authorize_url_has_no_secret_and_carries_pkce():
    verifier, challenge = entra.new_pkce_pair()
    url = entra.build_authorize_url(state="st", nonce="nc", code_challenge=challenge)
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "test-secret-not-real" not in url  # client_secret KHÔNG bao giờ ở URL
    assert verifier not in url  # verifier cũng không — chỉ challenge được gửi
