"""Real, live, two-stack Keycloak E2E: Keycloak -> MiniCRM -> relay -> AbsorpIQ.

**What "real" means here.** No fabricated JWTs, no mocked discovery documents.
This file performs the ACTUAL Authorization Code + PKCE flow against the
running Keycloak container (realm `p100`, extended for this task with three
canonical App Roles + three deterministic test users — see
`docker/keycloak/p100-realm.json` and the live-realm Admin API calls made when
this task was implemented), then presents the real, Keycloak-signed ID token
as `Authorization: Bearer` to MiniCRM's and AbsorpIQ's REAL running HTTP APIs
(`app.auth.authenticate` / `dashboard_auth.authenticate_dashboard` both accept
exactly this: see `minicrm/app/auth.py::authenticate`'s own docstring, layer 2,
"test tích hợp"). No route's auth dependency is bypassed or mocked.

**Why no browser automation.** No Playwright/Cypress exists in this repo. Per
the mission's own Phase 7 guidance, an HTTP-level Authorization Code + PKCE
flow against the real IdP is the sanctioned substitute — this file performs
that flow itself (`_real_login`), manually driving Keycloak's login form via
plain HTTP, and is labeled `KEYCLOAK_HTTP_E2E`, never `KEYCLOAK_BROWSER_E2E`.

**Known httpx quirk worked around here.** httpx's stdlib-based cookie jar
enforces the `Secure` cookie attribute literally (refuses to send a Secure
cookie over plain `http://`), while real browsers special-case `localhost` as
a "potentially trustworthy origin" and send it anyway. Keycloak always marks
its session cookies `Secure`, even on `sslRequired: none` dev realms. `_real_login`
therefore manages cookies itself (parses `Set-Cookie`, forwards them via an
explicit `Cookie:` header) instead of relying on httpx's default jar.

**What this file does NOT do** (see `Known Limitations` in `pipeline_status.md`):
outage/chaos scenarios (Keycloak/AbsorpIQ down mid-flow) are not simulated —
deliberately not stopping shared team-visible containers for destructive
testing. MiniCRM's own CRUD idempotency is not re-tested here (its create
routes always insert a new row by design, unlike the sync/relay boundary,
which already has extensive idempotency coverage in
`tests/test_api/test_sync_idempotency.py`/`test_sync_concurrency.py`).

Run: `E2E_KEYCLOAK_LIVE=1 pytest tests/e2e/test_keycloak_two_stack_flow.py -v`
(requires `docker compose up` with at least keycloak/minicrm/minicrm_db/api/db
healthy).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import urllib.parse
import uuid

import httpx
import jwt as pyjwt
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.environ.get("E2E_KEYCLOAK_LIVE") != "1",
    reason="Requires the live docker-compose stack (Keycloak/MiniCRM/AbsorpIQ/both DBs). Set E2E_KEYCLOAK_LIVE=1.",
)

KEYCLOAK = "http://localhost:9090/realms/p100"
MINICRM = "http://localhost:8100"
ABSORPIQ = "http://localhost:8000"
MINICRM_DB_URL = os.environ.get(
    "E2E_MINICRM_DB_URL", "postgresql+asyncpg://minicrm:minicrm@localhost:5434/minicrm0101"
)
ABSORPIQ_DB_URL = os.environ.get(
    "E2E_ABSORPIQ_DB_URL",
    "postgresql+asyncpg://app:ZeroToZeros_Absorption0Forecast1@localhost:5432/absorption",
)

MINICRM_CLIENT_ID = "minicrm-client"
MINICRM_CLIENT_SECRET = "local-dev-minicrm-secret"
MINICRM_REDIRECT_URI = "http://localhost:8100/auth/callback"

ABSORPIQ_CLIENT_ID = "absorbiq-client"
ABSORPIQ_CLIENT_SECRET = "local-dev-absorbiq-secret"
ABSORPIQ_REDIRECT_URI = "http://localhost:8000/api/v1/auth/callback"

# Test users provisioned into the live `p100` realm for this task (realm role
# CRM.CEO/CRM.ADVISOR/CRM.SALES each) — see docker/keycloak/p100-realm.json.
# Passwords are local-dev-only, not printed anywhere in test output or reports.
_USERS = {
    "ceo": ("e2e.ceo", "pemxOLDH9a9oKeBNLYv4sCcZ"),
    "advisor": ("e2e.advisor", "-QQ_IceKOoHWw7kL80B1xkbv"),
    "sales": ("e2e.sales", "VM_AC3KOACar6kbw30tOlPXu"),
}

EXPECTED_INTERNAL_ROLE = {"ceo": "admin", "advisor": "business_viewer", "sales": "pipeline_operator"}


# =============================================================================
# Real Authorization Code + PKCE flow against the live Keycloak container.
# =============================================================================


def _parse_set_cookie(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in values:
        first = v.split(";", 1)[0]
        if "=" in first:
            k, val = first.split("=", 1)
            out[k.strip()] = val.strip()
    return out


class RealLoginError(Exception):
    """Raised when the real Keycloak HTTP flow itself fails (not a test assertion)."""


def _real_login(*, client_id: str, client_secret: str, redirect_uri: str, username: str, password: str) -> dict:
    """Drives the ACTUAL Authorization Code + PKCE dance against live Keycloak.

    Returns the raw token response (dict with `id_token`/`access_token`/...).
    Uses the ID token downstream (not the access token): Keycloak's access
    token defaults to `aud: "account"` (the built-in resource-account
    audience) unless a custom audience mapper is added, whereas the ID token's
    `aud` is always the requesting `client_id` — exactly what
    `src/services/oidc.py`/`minicrm/app/oidc.py` expect to validate against
    `OIDC_AUDIENCE`/`MINICRM_OIDC_AUDIENCE` (or the client id fallback).
    """
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(8)

    authorize_url = KEYCLOAK + "/protocol/openid-connect/auth?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )

    with httpx.Client(timeout=10.0) as c:
        r = c.get(authorize_url)
        if r.status_code != 200:
            raise RealLoginError(f"GET authorize -> {r.status_code}")
        cookies = _parse_set_cookie(r.headers.get_list("set-cookie"))
        m = re.search(r'action="([^"]+)"', r.text)
        if not m:
            raise RealLoginError("Keycloak login form not found in authorize response")
        action = m.group(1).replace("&amp;", "&")

        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        login_resp = c.post(
            action,
            data={"username": username, "password": password, "credentialId": ""},
            headers={"Cookie": cookie_header},
            follow_redirects=False,
        )
        if login_resp.status_code != 302:
            # Real, deliberate wrong-password test relies on THIS branch —
            # callers that expect rejection catch RealLoginError.
            raise RealLoginError(f"login POST -> {login_resp.status_code} (bad credentials or form change)")
        location = login_resp.headers.get("location", "")
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code", [None])[0]
        if not code:
            raise RealLoginError(f"no ?code= in redirect: {location}")

        token_resp = c.post(
            KEYCLOAK + "/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": verifier,
            },
        )
        if token_resp.status_code != 200:
            raise RealLoginError(f"token exchange -> {token_resp.status_code}: {token_resp.text[:200]}")
        return token_resp.json()


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items() if value)


def _real_absorpiq_session(role: str) -> dict[str, str]:
    """Complete the browser cookie flow and return the live app cookies."""
    username, password = _USERS[role]
    cookies: dict[str, str] = {}
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        start = client.get(f"{ABSORPIQ}/api/v1/auth/login")
        assert start.status_code == 302, start.text
        cookies.update(_parse_set_cookie(start.headers.get_list("set-cookie")))
        authorize_url = start.headers["location"]

        authorize = client.get(authorize_url)
        assert authorize.status_code == 200
        cookies.update(_parse_set_cookie(authorize.headers.get_list("set-cookie")))
        match = re.search(r'action="([^"]+)"', authorize.text)
        if not match:
            raise RealLoginError("Keycloak login form not found for AbsorpIQ session")

        login = client.post(
            match.group(1).replace("&amp;", "&"),
            data={"username": username, "password": password, "credentialId": ""},
            headers={"Cookie": _cookie_header(cookies)},
        )
        assert login.status_code == 302
        cookies.update(_parse_set_cookie(login.headers.get_list("set-cookie")))

        callback_url = login.headers["location"]
        callback = client.get(callback_url, headers={"Cookie": _cookie_header(cookies)})
        assert callback.status_code == 302, callback.text
        cookies.update(_parse_set_cookie(callback.headers.get_list("set-cookie")))
    return cookies


def _minicrm_token(role: str) -> str:
    username, password = _USERS[role]
    tok = _real_login(
        client_id=MINICRM_CLIENT_ID,
        client_secret=MINICRM_CLIENT_SECRET,
        redirect_uri=MINICRM_REDIRECT_URI,
        username=username,
        password=password,
    )
    return tok["id_token"]


def _absorpiq_token(role: str) -> str:
    username, password = _USERS[role]
    tok = _real_login(
        client_id=ABSORPIQ_CLIENT_ID,
        client_secret=ABSORPIQ_CLIENT_SECRET,
        redirect_uri=ABSORPIQ_REDIRECT_URI,
        username=username,
        password=password,
    )
    return tok["id_token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# A. Infrastructure
# =============================================================================


def test_keycloak_is_healthy():
    # The management health port (9000) isn't published to the host — only
    # 9090 (the main HTTP listener) is, per docker-compose.yml. A live realm
    # discovery response IS the externally-observable health signal here.
    r = httpx.get(KEYCLOAK + "/.well-known/openid-configuration", timeout=5.0)
    assert r.status_code == 200


def test_keycloak_realm_discovery_works():
    r = httpx.get(KEYCLOAK + "/.well-known/openid-configuration", timeout=5.0)
    assert r.status_code == 200
    doc = r.json()
    assert doc["issuer"] == KEYCLOAK


def test_minicrm_is_healthy():
    r = httpx.get(f"{MINICRM}/health", timeout=5.0)
    assert r.status_code == 200


def test_absorpiq_is_healthy():
    r = httpx.get(f"{ABSORPIQ}/health", timeout=5.0)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_minicrm_database_is_reachable():
    engine = create_async_engine(MINICRM_DB_URL)
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT 1"))
        assert result.scalar() == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_absorpiq_database_is_reachable():
    engine = create_async_engine(ABSORPIQ_DB_URL)
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT 1"))
        assert result.scalar() == 1
    await engine.dispose()


# =============================================================================
# B. Login and session (KEYCLOAK_HTTP_E2E — real IdP, real HTTP)
# =============================================================================


@pytest.mark.parametrize("role", ["ceo", "advisor", "sales"])
def test_real_login_succeeds_for_each_role(role):
    token = _minicrm_token(role)
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["aud"] == MINICRM_CLIENT_ID
    assert claims["iss"] == KEYCLOAK


def test_invalid_credentials_are_rejected_by_keycloak_itself():
    with pytest.raises(RealLoginError, match="login POST"):
        _real_login(
            client_id=ABSORPIQ_CLIENT_ID,
            client_secret=ABSORPIQ_CLIENT_SECRET,
            redirect_uri=ABSORPIQ_REDIRECT_URI,
            username="e2e.ceo",
            password="definitely-the-wrong-password",
        )


def test_session_from_minicrm_audience_is_rejected_by_absorpiq():
    """The audience-separation boundary: a real, validly-signed, unexpired
    token — just minted for the WRONG API — must not authenticate here."""
    token = _minicrm_token("ceo")
    r = httpx.get(f"{ABSORPIQ}/api/v1/auth/me", headers=_bearer(token), timeout=5.0)
    assert r.status_code == 401


def test_session_from_absorpiq_audience_is_rejected_by_minicrm():
    """Uses a WRITE route (`app.auth.require_role`), not a read route: MiniCRM's
    GET /projects/{id} is gated by the SEPARATE Checkpoint-2 `human_auth`
    visibility system (`require_resource_visibility`), not Keycloak/OIDC at
    all — confirmed live: it 503s `AUTH_NOT_CONFIGURED` regardless of any
    Bearer token, since that gate never even inspects Authorization. Write
    routes are the ones actually gated by the Keycloak/OIDC identity this test
    cares about."""
    token = _absorpiq_token("ceo")
    r = httpx.post(
        f"{MINICRM}/projects",
        json={"name": "Should Not Be Created (wrong audience)", "launch_date": "2026-01-01"},
        headers=_bearer(token),
        timeout=5.0,
    )
    assert r.status_code == 401


def test_no_secret_appears_in_a_captured_health_response():
    """Cheap, real sanity check that ordinary traffic doesn't leak anything —
    full log-scanning is done separately in the runtime-verification pass."""
    r = httpx.get(f"{ABSORPIQ}/health", timeout=5.0)
    assert MINICRM_CLIENT_SECRET not in r.text
    assert ABSORPIQ_CLIENT_SECRET not in r.text


def test_authenticated_absorpiq_logout_revokes_session_live():
    cookies = _real_absorpiq_session("ceo")
    cookie_header = _cookie_header(cookies)

    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        before = client.get(f"{ABSORPIQ}/api/v1/auth/me", headers={"Cookie": cookie_header})
        assert before.status_code == 200, before.text

        logout = client.get(f"{ABSORPIQ}/api/v1/auth/logout", headers={"Cookie": cookie_header})
        assert logout.status_code == 303, logout.text
        assert logout.headers["location"].startswith(f"{KEYCLOAK}/protocol/openid-connect/logout")
        deleted = "\n".join(logout.headers.get_list("set-cookie"))
        assert "absorbiq_session" in deleted
        assert "minicrm_session" in deleted

        after = client.get(f"{ABSORPIQ}/api/v1/auth/me", headers={"Cookie": cookie_header})
        assert after.status_code == 401, after.text

        remote = client.get(logout.headers["location"], headers={"Cookie": cookie_header})
        assert remote.status_code in (200, 302, 303), remote.text[:200]


# =============================================================================
# C. Role and scope (real tokens, real HTTP, real running AbsorpIQ)
# =============================================================================


@pytest.mark.parametrize("role", ["ceo", "advisor", "sales"])
def test_absorpiq_me_resolves_the_correct_canonical_role(role):
    token = _absorpiq_token(role)
    r = httpx.get(f"{ABSORPIQ}/api/v1/auth/me", headers=_bearer(token), timeout=5.0)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == EXPECTED_INTERNAL_ROLE[role]


def test_anonymous_is_denied_on_a_real_protected_absorpiq_route():
    r = httpx.get(f"{ABSORPIQ}/api/v1/auth/me", timeout=5.0)
    assert r.status_code == 401


@pytest.mark.parametrize("role", ["ceo", "sales"])
def test_operator_plus_roles_pass_the_role_gate_on_an_operator_only_route(role):
    """`POST /files/upload` requires `pipeline_operator` minimum
    (`src/api/files.py`). FastAPI resolves the `require_operator` dependency
    BEFORE the handler body runs a single line of validation, so ceo(admin)/
    sales(pipeline_operator) getting anything OTHER than 401/403 here proves
    they cleared the real auth gate — the 422 they actually get (garbage
    project_id) is expected and harmless: nothing is written."""
    token = _absorpiq_token(role)
    r = httpx.post(
        f"{ABSORPIQ}/api/v1/files/upload",
        data={"template": "areas", "project_id": "not-a-real-uuid"},
        files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
        headers=_bearer(token),
        timeout=5.0,
    )
    assert r.status_code not in (401, 403), r.text


def test_business_viewer_role_is_denied_on_an_operator_only_route():
    token = _absorpiq_token("advisor")
    r = httpx.post(
        f"{ABSORPIQ}/api/v1/files/upload",
        data={"template": "areas", "project_id": "not-a-real-uuid"},
        files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
        headers=_bearer(token),
        timeout=5.0,
    )
    assert r.status_code == 403, r.text


def test_minicrm_only_ceo_can_create_a_project():
    """`admin` minimum on POST /projects (minicrm/app/routers/projects.py) —
    `sales` (pipeline_operator) must be denied, with NO row created."""
    token = _minicrm_token("sales")
    r = httpx.post(
        f"{MINICRM}/projects",
        json={"name": "Should Not Be Created", "launch_date": "2026-01-01"},
        headers=_bearer(token),
        timeout=5.0,
    )
    assert r.status_code == 403, r.text


# =============================================================================
# D. Full MiniCRM -> relay -> AbsorpIQ journey (FULL_TWO_STACK_E2E)
# =============================================================================


@pytest.fixture
def e2e_namespace():
    """A run-unique tag baked into every created name, so cleanup only ever
    touches rows this run created — never unrelated dev/business data."""
    tag = f"e2e-kc-{uuid.uuid4().hex[:10]}"
    yield tag


async def _wait_for(check, *, timeout=30.0, interval=1.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await check()
        if last:
            return last
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s (last check result: {last!r})")


@pytest.mark.asyncio
async def test_full_journey_project_area_unit_deal_mirrors_into_absorpiq(e2e_namespace):
    ceo_minicrm = _bearer(_minicrm_token("ceo"))
    ceo_absorpiq_headers = _bearer(_absorpiq_token("ceo"))
    tag = e2e_namespace
    minicrm_engine = create_async_engine(MINICRM_DB_URL)
    absorpiq_engine = create_async_engine(ABSORPIQ_DB_URL)

    try:
        with httpx.Client(timeout=10.0) as c:
            # 1. Real MiniCRM writes, through the real HTTP CRUD API.
            p = c.post(
                f"{MINICRM}/projects",
                json={"name": f"E2E Journey {tag}", "launch_date": "2026-01-01"},
                headers=ceo_minicrm,
            )
            assert p.status_code == 201, p.text
            project_ext_id = p.json()["record"]["external_id"]

            a = c.post(
                f"{MINICRM}/areas",
                json={
                    "external_project_id": project_ext_id,
                    "area_name": f"A-{tag}",
                    "unit_type": "2BR",
                    "bedrooms": 2,
                    "area_sqm": 65.0,
                    "total_units": 10,
                },
                headers=ceo_minicrm,
            )
            assert a.status_code == 201, a.text
            area_ext_id = a.json()["record"]["external_id"]

            u = c.post(
                f"{MINICRM}/units",
                json={"external_area_id": area_ext_id, "unit_code": f"U-{tag}"},
                headers=ceo_minicrm,
            )
            assert u.status_code == 201, u.text
            unit_ext_id = u.json()["record"]["external_id"]

        # 2. Verify MiniCRM's OWN database directly (not just trusting 201).
        async with minicrm_engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text("SELECT external_id, name FROM crm_projects WHERE external_id = :e"),
                    {"e": project_ext_id},
                )
            ).one_or_none()
        assert row is not None, "project row must actually exist in MiniCRM's own database"

        # 3. Wait for the REAL background relay loop (minicrm/app/relay.py,
        # started in the live container's lifespan) to mirror the unit into
        # AbsorpIQ — bounded polling, no fixed sleep.
        async def _unit_mirrored():
            async with absorpiq_engine.connect() as conn:
                return (
                    await conn.execute(
                        sa.text(
                            "SELECT count(*) FROM units WHERE external_unit_id = :u AND source_system = 'mini_crm'"
                        ),
                        {"u": unit_ext_id},
                    )
                ).scalar()

        mirrored_count = await _wait_for(_unit_mirrored, timeout=30.0, interval=1.0)
        assert mirrored_count == 1, "relay must mirror exactly one unit row, not zero or duplicated"

        # 4. Readback through AbsorpIQ's REAL, authenticated HTTP API — not a
        # direct DB peek pretending to be the API boundary.
        with httpx.Client(timeout=10.0) as c:
            proj = c.get(f"{ABSORPIQ}/api/v1/projects/{project_ext_id}", headers=ceo_absorpiq_headers)
            assert proj.status_code == 200, proj.text
            assert proj.json()["name"] == f"E2E Journey {tag}"

            inv = c.get(
                f"{ABSORPIQ}/api/v1/inventory",
                params={"external_project_id": project_ext_id, "include_units": "true"},
                headers=ceo_absorpiq_headers,
            )
            assert inv.status_code == 200, inv.text
            codes = [row["unit_code"] for row in inv.json()["units"]]
            assert f"U-{tag}" in codes

        # 5. Parent-child integrity, mirrored side: the unit's area really
        # resolves to the same project.
        async with absorpiq_engine.connect() as conn:
            joined = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT p.external_id
                        FROM units u JOIN areas ar ON u.area_id = ar.id JOIN projects p ON ar.project_id = p.id
                        WHERE u.external_unit_id = :u
                        """
                    ),
                    {"u": unit_ext_id},
                )
            ).scalar()
        assert joined == project_ext_id, "mirrored unit must resolve to the SAME project on the AbsorpIQ side"

    finally:
        # Cleanup: only this run's own namespaced rows, both databases.
        async with minicrm_engine.begin() as conn:
            await conn.execute(sa.text("DELETE FROM crm_units WHERE unit_code LIKE :p"), {"p": f"U-{tag}%"})
            await conn.execute(sa.text("DELETE FROM crm_areas WHERE area_name LIKE :p"), {"p": f"A-{tag}%"})
            await conn.execute(sa.text("DELETE FROM crm_projects WHERE name LIKE :p"), {"p": f"E2E Journey {tag}%"})
        async with absorpiq_engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "DELETE FROM units WHERE area_id IN (SELECT id FROM areas WHERE project_id IN "
                    "(SELECT id FROM projects WHERE external_id = :e))"
                ),
                {"e": project_ext_id},
            )
            await conn.execute(
                sa.text("DELETE FROM areas WHERE project_id IN (SELECT id FROM projects WHERE external_id = :e)"),
                {"e": project_ext_id},
            )
            # crm_source_records/upload_errors/sync_payloads -> upload_files ->
            # projects: the sync run(s) this journey created must go first.
            proj_id = (
                await conn.execute(sa.text("SELECT id FROM projects WHERE external_id = :e"), {"e": project_ext_id})
            ).scalar()
            if proj_id is not None:
                await conn.execute(
                    sa.text("DELETE FROM crm_source_records WHERE first_sync_run_id IN "
                            "(SELECT id FROM upload_files WHERE project_id = :p) "
                            "OR last_sync_run_id IN (SELECT id FROM upload_files WHERE project_id = :p)"),
                    {"p": proj_id},
                )
                await conn.execute(
                    sa.text("DELETE FROM upload_errors WHERE file_id IN (SELECT id FROM upload_files WHERE project_id = :p)"),
                    {"p": proj_id},
                )
                await conn.execute(
                    sa.text("DELETE FROM sync_payloads WHERE sync_run_id IN (SELECT id FROM upload_files WHERE project_id = :p)"),
                    {"p": proj_id},
                )
                await conn.execute(sa.text("DELETE FROM upload_files WHERE project_id = :p"), {"p": proj_id})
            await conn.execute(sa.text("DELETE FROM projects WHERE external_id = :e"), {"e": project_ext_id})
        await minicrm_engine.dispose()
        await absorpiq_engine.dispose()


# =============================================================================
# E. Reconciliation authorization (live) — human Keycloak roles, real HTTP.
#
# `src/api/reconciliation.py` used to authenticate ONLY via `X-API-Key` (the
# machine-to-machine sync credential) — no human dashboard/Keycloak principal
# had any way to read reconciliation results at all. This section proves the
# new dual-auth path (`_authorize_write`/`_authorize_read`) live: anonymous is
# rejected, role is checked before scope, scope is checked before the handler
# runs, and a denied request creates no `reconciliation_runs` row.
# =============================================================================


def test_reconciliation_anonymous_request_on_an_unknown_run_is_404_not_401():
    """`_load_run` runs before `_authorize_read` (same order as the pre-existing
    `src/api/sync.py::_authorize_sync_run_read` precedent) — a `run_id` that
    doesn't exist 404s regardless of credentials. The mission's own contract
    explicitly allows 404 as the documented response for an out-of-scope/
    nonexistent object; the anonymous-is-401 guarantee is tested below against
    a run that DOES exist, so it actually exercises the auth gate rather than
    the existence check."""
    r = httpx.get(f"{ABSORPIQ}/api/v1/reconciliation/runs/{uuid.uuid4()}", timeout=5.0)
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "RECONCILIATION_RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_reconciliation_role_and_scope_enforced_live(e2e_namespace):
    """One project, one real reconciliation run, three real Keycloak identities:

    - `sales` (pipeline_operator): role is SUFFICIENT to trigger a run, but this
      project is not in `sales`'s granted scope (`OIDC_PROJECT_SCOPE` only
      grants `CRM.CEO` -> ALL live) -> 403 PROJECT_OUT_OF_SCOPE, no row created.
    - `advisor` (business_viewer): role is INSUFFICIENT to trigger a run at all
      -> 403 INSUFFICIENT_ROLE, checked BEFORE scope, no row created.
    - `ceo` (admin, ALL scope): allowed to trigger AND read the run/findings —
      the full success path, live.
    """
    tag = e2e_namespace
    ceo_minicrm = _bearer(_minicrm_token("ceo"))
    minicrm_engine = create_async_engine(MINICRM_DB_URL)
    absorpiq_engine = create_async_engine(ABSORPIQ_DB_URL)

    try:
        with httpx.Client(timeout=10.0) as c:
            p = c.post(
                f"{MINICRM}/projects",
                json={"name": f"E2E Recon {tag}", "launch_date": "2026-01-01"},
                headers=ceo_minicrm,
            )
            assert p.status_code == 201, p.text
            project_ext_id = p.json()["record"]["external_id"]

        async def _project_internal_id():
            async with absorpiq_engine.connect() as conn:
                return (
                    await conn.execute(
                        sa.text("SELECT id FROM projects WHERE external_id = :e"), {"e": project_ext_id}
                    )
                ).scalar()

        project_id = await _wait_for(_project_internal_id, timeout=30.0, interval=1.0)

        async def _run_count():
            async with absorpiq_engine.connect() as conn:
                return (
                    await conn.execute(
                        sa.text("SELECT count(*) FROM reconciliation_runs WHERE project_id = :p"), {"p": project_id}
                    )
                ).scalar()

        before = await _run_count()

        with httpx.Client(timeout=10.0) as c:
            # sales: role sufficient, scope insufficient -> 403, zero DB effect.
            sales_resp = c.post(
                f"{ABSORPIQ}/api/v1/reconciliation/runs",
                json={"project_id": str(project_id), "source_instance_id": "mini-crm-dev"},
                headers=_bearer(_absorpiq_token("sales")),
            )
            assert sales_resp.status_code == 403, sales_resp.text
            assert sales_resp.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"

            # advisor: role insufficient, checked before scope -> 403, zero DB effect.
            advisor_resp = c.post(
                f"{ABSORPIQ}/api/v1/reconciliation/runs",
                json={"project_id": str(project_id), "source_instance_id": "mini-crm-dev"},
                headers=_bearer(_absorpiq_token("advisor")),
            )
            assert advisor_resp.status_code == 403, advisor_resp.text
            assert advisor_resp.json()["detail"]["error_code"] == "INSUFFICIENT_ROLE"

            after_denied = await _run_count()
            assert after_denied == before, "a denied reconciliation request left a row behind"

            # ceo: admin, ALL scope -> allowed to trigger AND read.
            ceo_headers = _bearer(_absorpiq_token("ceo"))
            ceo_resp = c.post(
                f"{ABSORPIQ}/api/v1/reconciliation/runs",
                json={"project_id": str(project_id), "source_instance_id": "mini-crm-dev"},
                headers=ceo_headers,
            )
            assert ceo_resp.status_code == 200, ceo_resp.text
            run_id = ceo_resp.json()["reconciliation_run_id"]

            detail = c.get(f"{ABSORPIQ}/api/v1/reconciliation/runs/{run_id}", headers=ceo_headers)
            assert detail.status_code == 200, detail.text
            assert detail.json()["reconciliation_run_id"] == run_id

            findings = c.get(f"{ABSORPIQ}/api/v1/reconciliation/runs/{run_id}/findings", headers=ceo_headers)
            assert findings.status_code == 200, findings.text

            # sales/advisor still cannot read this run either (same scope/role gates).
            sales_read = c.get(f"{ABSORPIQ}/api/v1/reconciliation/runs/{run_id}", headers=_bearer(_absorpiq_token("sales")))
            assert sales_read.status_code == 403
            assert sales_read.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"

            # Anonymous / garbage-token, against a run that DOES exist: this is
            # the real auth gate, not the existence check (see the standalone
            # unknown-run 404 test above for that distinction).
            anon_detail = c.get(f"{ABSORPIQ}/api/v1/reconciliation/runs/{run_id}")
            assert anon_detail.status_code == 401
            assert anon_detail.json()["detail"]["error_code"] == "MISSING_API_KEY"

            anon_findings = c.get(f"{ABSORPIQ}/api/v1/reconciliation/runs/{run_id}/findings")
            assert anon_findings.status_code == 401

            garbage = c.get(
                f"{ABSORPIQ}/api/v1/reconciliation/runs/{run_id}", headers=_bearer("not-a-real-token")
            )
            assert garbage.status_code == 401

        after_allowed = await _run_count()
        assert after_allowed == before + 1, "exactly one run must exist after the one allowed POST"

    finally:
        async with absorpiq_engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "DELETE FROM reconciliation_findings WHERE reconciliation_run_id IN "
                    "(SELECT id FROM reconciliation_runs WHERE project_id IN "
                    "(SELECT id FROM projects WHERE external_id = :e))"
                ),
                {"e": project_ext_id},
            )
            await conn.execute(
                sa.text(
                    "DELETE FROM reconciliation_runs WHERE project_id IN "
                    "(SELECT id FROM projects WHERE external_id = :e)"
                ),
                {"e": project_ext_id},
            )
            await conn.execute(sa.text("DELETE FROM projects WHERE external_id = :e"), {"e": project_ext_id})
        async with minicrm_engine.begin() as conn:
            await conn.execute(sa.text("DELETE FROM crm_projects WHERE name LIKE :p"), {"p": f"E2E Recon {tag}%"})
        await minicrm_engine.dispose()
        await absorpiq_engine.dispose()


# =============================================================================
# F. Sync credential lifecycle (live) — issue, authenticate, rotate, verify.
#
# Real Postgres, real HTTP against the running AbsorpIQ API. Uses a throwaway,
# uniquely-namespaced `source_instance_id` — NOT the shared team dev credential
# (`mini-crm-dev`) — so this test never rotates/disrupts the credential the
# live MiniCRM container is actually using for its relay.
# =============================================================================


@pytest.mark.asyncio
async def test_sync_credential_issue_rotate_old_rejected_new_accepted_live(e2e_namespace):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.services.sync_credentials import SyncCredentialService

    instance_id = f"e2e-cred-{e2e_namespace}"
    engine = create_async_engine(ABSORPIQ_DB_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = SyncCredentialService()

    def _probe(api_key: str) -> httpx.Response:
        # A garbage project_id: auth runs BEFORE the project-existence check, so
        # the response code alone distinguishes "credential accepted" (422
        # UNKNOWN_PROJECT) from "credential rejected" (401) without needing any
        # real project/area/unit fixtures.
        return httpx.post(
            f"{ABSORPIQ}/api/v1/reconciliation/runs",
            json={"project_id": str(uuid.uuid4()), "source_instance_id": instance_id},
            headers={"X-API-Key": api_key},
            timeout=10.0,
        )

    try:
        async with sessions() as session:
            async with session.begin():
                issued = await service.issue(
                    session, source_system="mini_crm", source_instance_id=instance_id, label="e2e-live-rotation"
                )

        # 1. Freshly issued key authenticates against the REAL running API.
        first = _probe(issued.api_key)
        assert first.status_code == 422, first.text
        assert first.json()["detail"]["error_code"] == "UNKNOWN_PROJECT"

        # 2. Rotate: new key persisted, old key revoked (issue-then-revoke order).
        async with sessions() as session:
            async with session.begin():
                rotated = await service.rotate(session, issued.credential_id, label="e2e-live-rotation-rotated")

        # 3. Old key is rejected — live, over real HTTP.
        old_after = _probe(issued.api_key)
        assert old_after.status_code == 401, old_after.text
        assert old_after.json()["detail"]["error_code"] == "REVOKED_API_KEY"
        assert issued.api_key not in old_after.text

        # 4. New key works — live, over real HTTP.
        new_after = _probe(rotated.api_key)
        assert new_after.status_code == 422, new_after.text
        assert new_after.json()["detail"]["error_code"] == "UNKNOWN_PROJECT"
        assert rotated.api_key not in new_after.text
    finally:
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    sa.text("DELETE FROM sync_credentials WHERE source_instance_id = :i"), {"i": instance_id}
                )
        await engine.dispose()
