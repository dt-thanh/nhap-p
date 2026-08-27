"""Mặt xác thực người dùng của Mini CRM — Keycloak-only (OIDC).

    GET  /auth/login     → 302 sang Keycloak (state + nonce + PKCE challenge)
    GET  /auth/callback  → Keycloak gọi lại, đổi code, phát cookie phiên, 302 về UI
    GET  /auth/me        → danh tính hiện tại (dùng để khôi phục state khi F5)
    POST /auth/refresh   → gia hạn phiên bằng refresh token cất trong phiên
    POST /auth/logout    → xoá cookie, trả URL đăng xuất Keycloak

VÌ SAO `state`/`nonce`/`code_verifier` nằm trong MỘT COOKIE NGẮN HẠN chứ không
trong bộ nhớ tiến trình: Mini CRM có thể chạy nhiều worker/khởi động lại giữa
lúc người dùng đang ở trang đăng nhập của Keycloak. Một dict trong RAM sẽ làm
callback thất bại ngẫu nhiên sau mỗi lần deploy. Cookie này `HttpOnly`, sống
10 phút, và bị xoá ngay khi callback dùng xong — nó KHÔNG phải phiên đăng nhập.

Cookie đó được KÝ (cùng `session_secret`) nên một `code_verifier` do kẻ tấn công
tự đặt không qua được: đây chính là mắt xích khoá `code` vào đúng phiên đã khởi
tạo (chống code-injection), lý do PKCE vẫn cần dù backend có client secret.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse

import jwt
from app import human_auth, oidc
from app import session as session_mod
from app.auth import authenticate
from app.config import get_settings
from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("minicrm.auth")

FLOW_COOKIE = "minicrm_oidc_flow"
_FLOW_TTL_SECONDS = 600


def _client_ip(request: Request) -> str:
    return request.client.host if request is not None and request.client else "unknown"


def _enforce_logout_rate_limit(request: Request, *, scope: str) -> None:
    # Every call consumes budget, success or not — unlike login (where only
    # failed guesses count), a valid logout replayed rapidly still costs a
    # real Redis write plus a Keycloak revocation call.
    key = (_client_ip(request), scope)
    if not human_auth.logout_rate_limiter.allowed(key):
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Quá nhiều yêu cầu đăng xuất từ địa chỉ này, thử lại sau.",
                "error_code": "LOGOUT_RATE_LIMITED",
            },
        )
    human_auth.logout_rate_limiter.record_failure(key)


def _require_oidc() -> None:
    if not oidc.oidc_configured() or not session_mod.session_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Đăng nhập chưa được cấu hình. Cần MINICRM_OIDC_* và "
                    "MINICRM_SESSION_SECRET."
                ),
                "error_code": "OIDC_NOT_CONFIGURED",
            },
        )


def _safe_return_to(candidate: str | None) -> str:
    """CHỈ chấp nhận đường dẫn TƯƠNG ĐỐI. Một `return_to` tuyệt đối do người gọi
    đặt là lỗ open-redirect kinh điển: kẻ tấn công gửi link `/auth/login?
    return_to=https://evil.example` và mượn thương hiệu Mini CRM để đưa người
    dùng ra ngoài NGAY SAU một lần đăng nhập thành công thật."""
    if not candidate:
        return "/"
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/"):
        return "/"
    return candidate


@router.get("/login")
async def login(return_to: str | None = Query(default=None)) -> Response:
    _require_oidc()
    verifier, challenge = oidc.new_pkce_pair()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    flow = jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "cv": verifier,
            "rt": _safe_return_to(return_to),
            "exp": int(time.time()) + _FLOW_TTL_SECONDS,
        },
        get_settings().session_secret.get_secret_value(),
        algorithm="HS256",
    )

    response = RedirectResponse(
        oidc.build_authorize_url(state=state, nonce=nonce, code_challenge=challenge),
        status_code=302,
    )
    s = get_settings()
    response.set_cookie(
        FLOW_COOKIE,
        flow,
        max_age=_FLOW_TTL_SECONDS,
        httponly=True,
        secure=s.session_cookie_secure,
        samesite=s.session_cookie_samesite,  # type: ignore[arg-type]
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    minicrm_oidc_flow: str | None = Cookie(default=None, alias=FLOW_COOKIE),
) -> Response:
    _require_oidc()
    settings = get_settings()
    ui_base = settings.cors_origins.split(",")[0].strip()

    if error:
        # Không phản chiếu `error_description` của provider vào URL: nó có thể
        # chứa chi tiết cấu hình nội bộ.
        return RedirectResponse(f"{ui_base}/login?{urlencode({'error': error})}", status_code=302)

    if not code or not state or not minicrm_oidc_flow:
        raise HTTPException(
            status_code=400,
            detail={"message": "Callback thiếu tham số.", "error_code": "INVALID_CALLBACK"},
        )

    try:
        flow = jwt.decode(
            minicrm_oidc_flow,
            settings.session_secret.get_secret_value(),
            algorithms=["HS256"],
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"message": "Phiên đăng nhập đã hết hạn, thử lại.", "error_code": "FLOW_EXPIRED"},
        ) from None

    if not secrets.compare_digest(str(flow.get("state", "")), state):
        raise HTTPException(
            status_code=400,
            detail={"message": "State không khớp.", "error_code": "STATE_MISMATCH"},
        )

    tokens = await oidc.exchange_code(code=code, code_verifier=flow["cv"])
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Provider không trả id_token.", "error_code": "NO_ID_TOKEN"},
        )

    identity = oidc.verify_token(id_token)
    role = session_mod.resolve_role(identity)
    scope = session_mod.resolve_scope(identity, role)

    # JIT: mirror danh tính OIDC vào crm_users (fail-open — không chặn auth).
    from app.jit_provisioning import upsert_from_oidc

    await upsert_from_oidc(identity, role)

    session_token = session_mod.issue_session(
        identity,
        role=role,
        scope=scope,
        refresh_token=tokens.get("refresh_token"),
        id_token_hint=id_token,
    )

    response = RedirectResponse(f"{ui_base}{flow.get('rt', '/')}", status_code=302)
    session_mod.set_session_cookie(response, session_token)
    response.delete_cookie(FLOW_COOKIE, path="/")
    return response


@router.get("/me")
async def me(
    authorization: str | None = Header(default=None, alias="Authorization"),
    minicrm_session: str | None = Cookie(default=None, alias=session_mod.SESSION_COOKIE),
) -> JSONResponse:
    """Nguồn sự thật về trạng thái đăng nhập cho frontend. Vì token nằm trong
    cookie `HttpOnly`, JavaScript KHÔNG tự đọc được danh tính — nó phải hỏi
    endpoint này. Đó là chủ ý, không phải một vòng lặp thừa."""
    principal = await authenticate(authorization, minicrm_session)
    payload: dict = {
        "role": principal.role,
        "project_scope": "ALL"
        if principal.project_scope == "ALL"
        else sorted(principal.project_scope),
    }
    if minicrm_session:
        claims = await session_mod.read_session_verified(minicrm_session)
        payload |= {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "name": claims.get("name") or claims.get("email"),
            "expires_at": claims.get("exp"),
        }
    return JSONResponse(payload)


@router.post("/refresh")
async def refresh(
    minicrm_session: str | None = Cookie(default=None, alias=session_mod.SESSION_COOKIE),
) -> JSONResponse:
    _require_oidc()
    if not minicrm_session:
        raise HTTPException(
            status_code=401,
            detail={"message": "Không có phiên.", "error_code": "MISSING_CREDENTIALS"},
        )
    # Đọc claim không kiểm hạn: phiên hết hạn chính là lúc cần refresh, nhưng
    # vẫn phải kiểm tra blacklist để logout không thể bị vượt qua bằng refresh.
    try:
        claims = await session_mod.read_session_verified(minicrm_session, verify_exp=False)
    except HTTPException:
        raise

    refresh_token = claims.get("rt")
    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên không có refresh token.", "error_code": "SESSION_EXPIRED"},
        )

    tokens = await oidc.refresh_tokens(refresh_token)
    identity = oidc.verify_token(tokens["id_token"])
    role = session_mod.resolve_role(identity)
    scope = session_mod.resolve_scope(identity, role)
    new_session = session_mod.issue_session(
        identity,
        role=role,
        scope=scope,
        refresh_token=tokens.get("refresh_token", refresh_token),
        id_token_hint=tokens.get("id_token"),
    )
    response = JSONResponse({"status": "refreshed", "role": role})
    session_mod.set_session_cookie(response, new_session)
    return response


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(
    request: Request,
    minicrm_session: str | None = Cookie(default=None, alias=session_mod.SESSION_COOKIE),
    absorbiq_session: str | None = Cookie(default=None, alias="absorbiq_session"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Response:
    """Revoke the active session for either supported authentication boundary.

    The Keycloak router is registered before the legacy human-auth router, so a
    human JWT must be handled here as a deliberate fallback.  Revoking its
    ``crm_auth_sessions`` row invalidates both the access token (on the next
    session-backed request) and its opaque refresh token without storing either
    token in logs or plaintext in the database.
    """
    _enforce_logout_rate_limit(request, scope="logout")
    claims: dict = {}
    user_id: str | None = None
    minicrm_session_revoked = False
    refresh_token_revoked = False
    absorbiq_session_revoked = False
    if minicrm_session:
        try:
            claims = session_mod._decode_session(minicrm_session, verify_exp=False)
        except HTTPException:
            claims = {}
        user_id = claims.get("sub")
        minicrm_session_revoked = await session_mod.revoke_session(minicrm_session)
        refresh_token_revoked = await oidc.revoke_token(str(claims.get("rt") or ""))
    if absorbiq_session:
        absorbiq_session_revoked = await session_mod.revoke_shared_token(absorbiq_session)
    if authorization and not minicrm_session and not absorbiq_session:
        try:
            principal = await human_auth.require_human_principal(authorization)
        except human_auth.HumanAuthError:
            # Logout is intentionally idempotent for an expired or malformed
            # bearer token; cookies are still cleared and the SSO flow ends.
            principal = None
        if principal is not None:
            user_id = str(principal.subject)
            session = human_auth.db_session()
            try:
                try:
                    await human_auth.HumanAuthService().logout_current(session, principal)
                except human_auth.HumanAuthError:
                    # The session may already have been revoked.  Do not turn
                    # an otherwise completed logout into a user-visible error.
                    pass
            finally:
                await session.close()

    # Audit trail only — never the session cookie, bearer token, or refresh
    # token themselves. `user_id` is `None` when logout is called with no
    # recognizable credential at all (still idempotent, still logged).
    logger.info(
        "auth.logout",
        extra={
            "user_id": user_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "ip": request.client.host if request is not None and request.client else "unknown",
            "minicrm_session_revoked": minicrm_session_revoked,
            "refresh_token_revoked": refresh_token_revoked,
            "absorbiq_session_revoked": absorbiq_session_revoked,
        },
    )

    logout_url = None
    if oidc.oidc_configured():
        try:
            logout_url = oidc.build_logout_url(id_token_hint=claims.get("id_token_hint"))
        except HTTPException:
            pass
    ui_base = get_settings().cors_origins.split(",")[0].strip()
    response = RedirectResponse(logout_url or f"{ui_base}/login", status_code=303)
    session_mod.clear_session_cookie(response)
    response.delete_cookie(FLOW_COOKIE, path="/")
    response.delete_cookie("absorbiq_session", path="/")
    response.delete_cookie("absorbiq_oidc_flow", path="/")
    return response


@router.post("/logout-all")
async def logout_all(
    request: Request,
    minicrm_session: str | None = Cookie(default=None, alias=session_mod.SESSION_COOKIE),
    absorbiq_session: str | None = Cookie(default=None, alias="absorbiq_session"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Response:
    """Revoke EVERY active Mini CRM session for the authenticated user — every
    device, every browser — not just the one making this request.

    Mirrors `/auth/logout`'s dual-path handling (OIDC session cookie vs. legacy
    human-auth bearer token) and its response contract, so the current browser
    also ends up logged out: it necessarily held one of the sessions just
    revoked. The user identity is ALWAYS derived from the caller's own
    validated credential — there is no client-supplied user id anywhere in this
    path — so this can only ever revoke the caller's own sessions.
    """
    _enforce_logout_rate_limit(request, scope="logout-all")
    claims: dict = {}
    user_id: str | None = None
    minicrm_session_revoked = False
    refresh_token_revoked = False
    absorbiq_session_revoked = False
    if minicrm_session:
        try:
            claims = session_mod._decode_session(minicrm_session, verify_exp=False)
        except HTTPException:
            claims = {}
        user_id = claims.get("sub")
        if user_id:
            await session_mod.revoke_all_sessions(str(user_id))
        minicrm_session_revoked = await session_mod.revoke_session(minicrm_session)
        refresh_token_revoked = await oidc.revoke_token(str(claims.get("rt") or ""))
    if absorbiq_session:
        absorbiq_session_revoked = await session_mod.revoke_shared_token(absorbiq_session)
    if authorization and not minicrm_session and not absorbiq_session:
        try:
            principal = await human_auth.require_human_principal(authorization)
        except human_auth.HumanAuthError:
            principal = None
        if principal is not None:
            user_id = str(principal.subject)
            session = human_auth.db_session()
            try:
                try:
                    await human_auth.HumanAuthService().logout_all(session, principal)
                except human_auth.HumanAuthError:
                    # Already fully revoked (e.g. a concurrent logout-all) —
                    # not a user-visible error.
                    pass
            finally:
                await session.close()

    # Same audit-log contract as `/auth/logout`: never the token itself.
    logger.info(
        "auth.logout_all",
        extra={
            "user_id": user_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "ip": _client_ip(request),
            "minicrm_session_revoked": minicrm_session_revoked,
            "refresh_token_revoked": refresh_token_revoked,
            "absorbiq_session_revoked": absorbiq_session_revoked,
        },
    )

    logout_url = None
    if oidc.oidc_configured():
        try:
            logout_url = oidc.build_logout_url(id_token_hint=claims.get("id_token_hint"))
        except HTTPException:
            pass
    ui_base = get_settings().cors_origins.split(",")[0].strip()
    response = RedirectResponse(logout_url or f"{ui_base}/login", status_code=303)
    session_mod.clear_session_cookie(response)
    response.delete_cookie(FLOW_COOKIE, path="/")
    response.delete_cookie("absorbiq_session", path="/")
    response.delete_cookie("absorbiq_oidc_flow", path="/")
    return response
