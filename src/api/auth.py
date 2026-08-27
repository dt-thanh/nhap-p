"""Mặt xác thực người dùng của Product/AbsorbIQ — Keycloak-only (OIDC).

Cùng hình dạng endpoint với router xác thực tương ứng bên Mini CRM, cùng realm
Keycloak (`p100`), mỗi app một client Keycloak tách bạch. Xem `src/services/oidc.py`
để hiểu vì sao SSO hoạt động mà KHÔNG cần truyền token giữa hai frontend.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode, urlparse

import jwt
from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

from src.config import get_settings
from src.services import oidc
from src.services.dashboard_auth import authenticate_dashboard

router = APIRouter(prefix="/auth", tags=["auth"])

FLOW_COOKIE = "absorbiq_oidc_flow"
_FLOW_TTL_SECONDS = 600


def _require_oidc() -> None:
    if not oidc.oidc_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Đăng nhập chưa được cấu hình (OIDC_* + SESSION_SECRET).",
                "error_code": "OIDC_NOT_CONFIGURED",
            },
        )


def _safe_return_to(candidate: str | None) -> str:
    """Chỉ chấp nhận đường dẫn TƯƠNG ĐỐI — chặn open-redirect."""
    if not candidate:
        return "/"
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/"):
        return "/"
    return candidate


def _ui_base() -> str:
    return get_settings().cors_origins.split(",")[0].strip()


@router.get("/login")
async def login(return_to: str | None = Query(default=None)) -> Response:
    _require_oidc()
    settings = get_settings()
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
        settings.session_secret.get_secret_value(),
        algorithm="HS256",
    )
    response = RedirectResponse(
        oidc.build_authorize_url(state=state, nonce=nonce, code_challenge=challenge),
        status_code=302,
    )
    response.set_cookie(
        FLOW_COOKIE,
        flow,
        max_age=_FLOW_TTL_SECONDS,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    absorbiq_oidc_flow: str | None = Cookie(default=None, alias=FLOW_COOKIE),
) -> Response:
    _require_oidc()
    settings = get_settings()
    if error:
        return RedirectResponse(f"{_ui_base()}/login?{urlencode({'error': error})}", status_code=302)
    if not code or not state or not absorbiq_oidc_flow:
        raise HTTPException(
            status_code=400,
            detail={"message": "Callback thiếu tham số.", "error_code": "INVALID_CALLBACK"},
        )
    try:
        flow = jwt.decode(
            absorbiq_oidc_flow, settings.session_secret.get_secret_value(), algorithms=["HS256"]
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"message": "Phiên đăng nhập đã hết hạn.", "error_code": "FLOW_EXPIRED"},
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
    role = oidc.resolve_role(identity)
    session_token = oidc.issue_session(
        identity,
        role=role,
        scope=oidc.resolve_scope(identity),
        refresh_token=tokens.get("refresh_token"),
        id_token_hint=id_token,
    )
    response = RedirectResponse(f"{_ui_base()}{flow.get('rt', '/')}", status_code=302)
    oidc.set_session_cookie(response, session_token)
    response.delete_cookie(FLOW_COOKIE, path="/")
    return response


@router.get("/me")
async def me(
    authorization: str | None = Header(default=None, alias="Authorization"),
    absorbiq_session: str | None = Cookie(default=None, alias=oidc.SESSION_COOKIE),
) -> JSONResponse:
    principal = await authenticate_dashboard(authorization, absorbiq_session)
    payload: dict = {
        "role": principal.role,
        "project_scope": "ALL"
        if principal.project_scope == "ALL"
        else sorted(principal.project_scope),
    }
    if absorbiq_session:
        claims = await oidc.read_session_verified(absorbiq_session)
        payload |= {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "name": claims.get("name") or claims.get("email"),
            "expires_at": claims.get("exp"),
        }
    return JSONResponse(payload)


@router.post("/refresh")
async def refresh(
    absorbiq_session: str | None = Cookie(default=None, alias=oidc.SESSION_COOKIE),
) -> JSONResponse:
    """Gia hạn phiên bằng refresh_token cất trong cookie phiên.

    Bản MIRROR của `/auth/refresh` bên Mini CRM (`app/routers/auth_routes.py`).
    Đây là đầu kia của hợp đồng mà `frontend/src/api/auth.js::refreshSession()`
    gọi — trước bản này endpoint chưa tồn tại nên FE nhận 404 im lặng và mọi phiên
    hết hạn đều rơi thẳng ra /login thay vì được gia hạn êm. Không tạo cơ chế phiên
    mới: đọc `rt` từ cookie hiện có, đổi lấy token mới qua Keycloak, phát lại cookie.
    """
    _require_oidc()
    if not absorbiq_session:
        raise HTTPException(
            status_code=401,
            detail={"message": "Không có phiên.", "error_code": "MISSING_CREDENTIALS"},
        )
    # Đọc claim không kiểm hạn: phiên hết hạn chính là lúc cần refresh, nhưng
    # vẫn phải kiểm tra jti để logout không thể bị vượt qua bằng refresh.
    try:
        claims = await oidc.read_session_verified(absorbiq_session, verify_exp=False)
    except HTTPException:
        raise

    refresh_token = claims.get("rt")
    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên không có refresh token.", "error_code": "SESSION_EXPIRED"},
        )

    tokens = await oidc.refresh_tokens(refresh_token)
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Provider không trả id_token khi refresh.", "error_code": "NO_ID_TOKEN"},
        )
    identity = oidc.verify_token(id_token)
    role = oidc.resolve_role(identity)
    new_session = oidc.issue_session(
        identity,
        role=role,
        scope=oidc.resolve_scope(identity),
        refresh_token=tokens.get("refresh_token", refresh_token),
        id_token_hint=id_token,
    )
    response = JSONResponse({"status": "refreshed", "role": role})
    oidc.set_session_cookie(response, new_session)
    return response


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(
    absorbiq_session: str | None = Cookie(default=None, alias=oidc.SESSION_COOKIE),
    minicrm_session: str | None = Cookie(default=None, alias="minicrm_session"),
) -> Response:
    """Revoke local/provider sessions, clear auth cookies, then end Keycloak SSO."""
    claims: dict = {}
    if absorbiq_session:
        try:
            claims = oidc._decode_session(absorbiq_session, verify_exp=False)
        except HTTPException:
            claims = {}
        await oidc.revoke_session(absorbiq_session)
        await oidc.revoke_token(str(claims.get("rt") or ""))
    if minicrm_session:
        await oidc.revoke_shared_token(minicrm_session)

    logout_url = None
    if oidc.oidc_configured():
        try:
            logout_url = oidc.build_logout_url(id_token_hint=claims.get("id_token_hint"))
        except HTTPException:
            logout_url = None
    response = RedirectResponse(logout_url or f"{_ui_base()}/login", status_code=303)
    oidc.clear_session_cookie(response)
    response.delete_cookie(FLOW_COOKIE, path="/")
    response.delete_cookie("minicrm_session", path="/")
    response.delete_cookie("minicrm_oidc_flow", path="/")
    return response
