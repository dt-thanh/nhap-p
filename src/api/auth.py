"""Mặt xác thực người dùng của Product/AbsorbIQ (CP5 — SSO với Mini CRM).

Cùng hình dạng endpoint với router xác thực tương ứng bên Mini CRM, cùng tenant
Entra, hai app registration tách bạch. Xem `src/services/entra_auth.py` để hiểu
vì sao SSO hoạt động mà KHÔNG cần truyền token giữa hai frontend.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode, urlparse

import jwt
from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

from src.config import get_settings
from src.services import entra_auth
from src.services.dashboard_auth import authenticate_dashboard

router = APIRouter(prefix="/auth", tags=["auth"])

FLOW_COOKIE = "absorbiq_oidc_flow"
_FLOW_TTL_SECONDS = 600


def _require_entra() -> None:
    if not entra_auth.entra_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Đăng nhập Entra chưa được cấu hình (ENTRA_* + SESSION_SECRET).",
                "error_code": "ENTRA_NOT_CONFIGURED",
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
    _require_entra()
    settings = get_settings()
    verifier, challenge = entra_auth.new_pkce_pair()
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
        entra_auth.build_authorize_url(state=state, nonce=nonce, code_challenge=challenge),
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
    _require_entra()
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

    tokens = await entra_auth.exchange_code(code=code, code_verifier=flow["cv"])
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Entra không trả id_token.", "error_code": "NO_ID_TOKEN"},
        )
    identity = entra_auth.verify_token(id_token)
    role = entra_auth.resolve_role(identity)
    session_token = entra_auth.issue_session(
        identity,
        role=role,
        scope=entra_auth.resolve_scope(identity),
        refresh_token=tokens.get("refresh_token"),
    )
    response = RedirectResponse(f"{_ui_base()}{flow.get('rt', '/')}", status_code=302)
    entra_auth.set_session_cookie(response, session_token)
    response.delete_cookie(FLOW_COOKIE, path="/")
    return response


@router.get("/me")
async def me(
    authorization: str | None = Header(default=None, alias="Authorization"),
    absorbiq_session: str | None = Cookie(default=None, alias=entra_auth.SESSION_COOKIE),
) -> JSONResponse:
    principal = await authenticate_dashboard(authorization, absorbiq_session)
    payload: dict = {
        "role": principal.role,
        "project_scope": "ALL"
        if principal.project_scope == "ALL"
        else sorted(principal.project_scope),
    }
    if absorbiq_session:
        claims = entra_auth.read_session(absorbiq_session)
        payload |= {
            "id": claims.get("sub"),
            "email": claims.get("email"),
            "name": claims.get("name") or claims.get("email"),
            "expires_at": claims.get("exp"),
        }
    return JSONResponse(payload)


@router.post("/refresh")
async def refresh(
    absorbiq_session: str | None = Cookie(default=None, alias=entra_auth.SESSION_COOKIE),
) -> JSONResponse:
    """Gia hạn phiên bằng refresh_token cất trong cookie phiên.

    Bản MIRROR của `/auth/refresh` bên Mini CRM (`app/routers/auth_routes.py`).
    Đây là đầu kia của hợp đồng mà `frontend/src/api/auth.js::refreshSession()`
    gọi — trước bản này endpoint chưa tồn tại nên FE nhận 404 im lặng và mọi phiên
    hết hạn đều rơi thẳng ra /login thay vì được gia hạn êm. Không tạo cơ chế phiên
    mới: đọc `rt` từ cookie hiện có, đổi lấy token mới qua Entra, phát lại cookie.
    """
    _require_entra()
    if not absorbiq_session:
        raise HTTPException(
            status_code=401,
            detail={"message": "Không có phiên.", "error_code": "MISSING_CREDENTIALS"},
        )
    # Đọc claim KHÔNG kiểm hạn: phiên hết hạn CHÍNH LÀ lúc cần refresh. `read_session`
    # kiểm hạn nên ở đây decode trực tiếp với verify_exp=False, giống Mini CRM.
    try:
        claims = jwt.decode(
            absorbiq_session,
            get_settings().session_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except Exception:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên không hợp lệ.", "error_code": "INVALID_SESSION"},
        ) from None

    refresh_token = claims.get("rt")
    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Phiên không có refresh token.", "error_code": "SESSION_EXPIRED"},
        )

    tokens = await entra_auth.refresh_tokens(refresh_token)
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Entra không trả id_token khi refresh.", "error_code": "NO_ID_TOKEN"},
        )
    identity = entra_auth.verify_token(id_token)
    role = entra_auth.resolve_role(identity)
    new_session = entra_auth.issue_session(
        identity,
        role=role,
        scope=entra_auth.resolve_scope(identity),
        refresh_token=tokens.get("refresh_token", refresh_token),
    )
    response = JSONResponse({"status": "refreshed", "role": role})
    entra_auth.set_session_cookie(response, new_session)
    return response


@router.post("/logout")
async def logout() -> JSONResponse:
    response = JSONResponse(
        {
            "status": "logged_out",
            "entra_logout_url": entra_auth.build_logout_url()
            if entra_auth.entra_configured()
            else None,
        }
    )
    entra_auth.clear_session_cookie(response)
    return response
