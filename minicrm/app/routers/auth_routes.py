"""Mặt xác thực người dùng của Mini CRM (CP4).

    GET  /auth/login     → 302 sang Entra (state + nonce + PKCE challenge)
    GET  /auth/callback  → Entra gọi lại, đổi code, phát cookie phiên, 302 về UI
    GET  /auth/me        → danh tính hiện tại (dùng để khôi phục state khi F5)
    POST /auth/refresh   → gia hạn phiên bằng refresh token cất trong phiên
    POST /auth/logout    → xoá cookie, trả URL đăng xuất Entra

VÌ SAO `state`/`nonce`/`code_verifier` nằm trong MỘT COOKIE NGẮN HẠN chứ không
trong bộ nhớ tiến trình: Mini CRM có thể chạy nhiều worker/khởi động lại giữa
lúc người dùng đang ở trang đăng nhập của Microsoft. Một dict trong RAM sẽ làm
callback thất bại ngẫu nhiên sau mỗi lần deploy. Cookie này `HttpOnly`, sống
10 phút, và bị xoá ngay khi callback dùng xong — nó KHÔNG phải phiên đăng nhập.

Cookie đó được KÝ (cùng `session_secret`) nên một `code_verifier` do kẻ tấn công
tự đặt không qua được: đây chính là mắt xích khoá `code` vào đúng phiên đã khởi
tạo (chống code-injection), lý do PKCE vẫn cần dù backend có client secret.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode, urlparse

import jwt
from app import entra
from app import session as session_mod
from app.auth import authenticate
from app.config import get_settings
from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])

FLOW_COOKIE = "minicrm_oidc_flow"
_FLOW_TTL_SECONDS = 600


def _require_entra() -> None:
    if not entra.entra_configured() or not session_mod.session_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "message": (
                    "Đăng nhập Entra chưa được cấu hình. Cần MINICRM_ENTRA_* và "
                    "MINICRM_SESSION_SECRET."
                ),
                "error_code": "ENTRA_NOT_CONFIGURED",
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
    _require_entra()
    verifier, challenge = entra.new_pkce_pair()
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
        entra.build_authorize_url(state=state, nonce=nonce, code_challenge=challenge),
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
    _require_entra()
    settings = get_settings()
    ui_base = settings.cors_origins.split(",")[0].strip()

    if error:
        # Không phản chiếu `error_description` của Entra vào URL: nó có thể chứa
        # tên tenant/app registration.
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

    tokens = await entra.exchange_code(code=code, code_verifier=flow["cv"])
    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Entra không trả id_token.", "error_code": "NO_ID_TOKEN"},
        )

    identity = entra.verify_token(id_token)
    role = session_mod.resolve_role(identity)
    scope = session_mod.resolve_scope(identity, role)
    session_token = session_mod.issue_session(
        identity, role=role, scope=scope, refresh_token=tokens.get("refresh_token")
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
        claims = session_mod.read_session(minicrm_session)
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
    _require_entra()
    if not minicrm_session:
        raise HTTPException(
            status_code=401,
            detail={"message": "Không có phiên.", "error_code": "MISSING_CREDENTIALS"},
        )
    # Đọc claim KHÔNG kiểm hạn: phiên hết hạn CHÍNH LÀ lúc cần refresh.
    try:
        claims = jwt.decode(
            minicrm_session,
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

    tokens = await entra.refresh_tokens(refresh_token)
    identity = entra.verify_token(tokens["id_token"])
    role = session_mod.resolve_role(identity)
    scope = session_mod.resolve_scope(identity, role)
    new_session = session_mod.issue_session(
        identity,
        role=role,
        scope=scope,
        refresh_token=tokens.get("refresh_token", refresh_token),
    )
    response = JSONResponse({"status": "refreshed", "role": role})
    session_mod.set_session_cookie(response, new_session)
    return response


@router.post("/logout")
async def logout() -> JSONResponse:
    """Xoá phiên CỤC BỘ rồi trả URL đăng xuất Entra. Frontend điều hướng tới URL
    đó để kết thúc cả phiên SSO ở phía Microsoft — bỏ bước này thì lần bấm
    "đăng nhập" kế tiếp sẽ vào thẳng lại, và người dùng tưởng mình chưa thoát."""
    response = JSONResponse(
        {
            "status": "logged_out",
            "entra_logout_url": entra.build_logout_url() if entra.entra_configured() else None,
        }
    )
    session_mod.clear_session_cookie(response)
    return response
