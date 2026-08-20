"""Xác thực GHI cho Mini CRM (D-14): business_viewer / pipeline_operator / admin,
phạm vi dự án TĨNH gắn vào token.

Mirror TRỰC TIẾP `src/services/dashboard_auth.py` của backend — cùng bốn nguyên
tắc, dựng lại thành một instance riêng (không import từ `src/`, xem
`app/config.py` về lý do cô lập tuyệt đối):

1. Vai trò suy ra từ TOKEN NÀO KHỚP, không bao giờ từ một trường client tự khai
   (không `X-Role`).
2. Chưa cấu hình token nào = ĐÓNG (503), không phải mở — soi gương
   `dashboard_auth_configured` của backend.
3. `secrets.compare_digest` cho mọi phép so token.
4. Header `Authorization: Bearer <token>` — cùng cơ chế backend đã dùng.

Khác backend ở đúng một điểm, vì đây là D-14 chứ không phải Phase 5.5: mỗi token
còn mang một PHẠM VI DỰ ÁN (`ProjectScope` — `ALL`, một tập `external_id`, hoặc
tập RỖNG). `business_viewer` không ghi được gì (routes ghi đòi tối thiểu
`pipeline_operator`), nên phạm vi của nó không cần kiểm — nhưng vẫn được đọc và
giữ nguyên cho nhất quán với mô hình `role × project_scope` đã đóng băng ở
`docs/crm/phase_a_domain_freeze.md` §A7.2 (khuôn Phase E dùng lại y hệt).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Literal

from app import entra, session as session_mod
from app.config import get_settings
from fastapi import Cookie, Header, HTTPException

MiniCrmRole = Literal["business_viewer", "pipeline_operator", "admin"]

_ROLE_LEVEL: dict[MiniCrmRole, int] = {
    "business_viewer": 0,
    "pipeline_operator": 1,
    "admin": 2,
}

ProjectScope = frozenset[str] | Literal["ALL"]


@dataclass(frozen=True, slots=True)
class MiniCrmPrincipal:
    """Danh tính đã xác thực của người gọi. Không giữ token thô."""

    role: MiniCrmRole
    project_scope: ProjectScope


def _configured_tokens() -> list[tuple[MiniCrmRole, str]]:
    settings = get_settings()
    # CP4: token TĨNH chỉ còn sống khi được bật TƯỜNG MINH. Ở production
    # (`legacy_token_auth_enabled=false`) hàm này trả rỗng, nên `auth_configured()`
    # sai và đường token tĩnh biến mất hoàn toàn — kể cả khi biến môi trường
    # token vẫn còn sót lại trong `.env` cũ.
    if not settings.legacy_token_auth_enabled:
        return []
    pairs: list[tuple[MiniCrmRole, str]] = [
        ("business_viewer", settings.auth_business_viewer_token.get_secret_value()),
        ("pipeline_operator", settings.auth_pipeline_operator_token.get_secret_value()),
        ("admin", settings.auth_admin_token.get_secret_value()),
    ]
    return [(role, token) for role, token in pairs if token]


def auth_configured() -> bool:
    return bool(_configured_tokens())


def _scope_map() -> dict[str, ProjectScope]:
    """`{"<token>": ["P-0001", ...]}` hoặc `{"<token>": "ALL"}`. JSON hỏng hoặc
    rỗng ⇒ map rỗng (mọi token không có mặt ⇒ phạm vi rỗng — fail-closed, KHÔNG
    ném lỗi cấu hình làm sập toàn bộ mặt xác thực vì một giá trị gõ sai)."""
    raw = get_settings().auth_project_scope.get_secret_value()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, ProjectScope] = {}
    for token, value in parsed.items():
        result[token] = "ALL" if value == "ALL" else frozenset(value)
    return result


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


async def authenticate(
    authorization: str | None,
    session_cookie: str | None = None,
) -> MiniCrmPrincipal:
    """Ba lớp, theo ĐÚNG thứ tự này (CP4):

    1. **Cookie phiên** (`minicrm_session`) — đường của người dùng trình duyệt
       sau khi đã qua Entra. Rẻ nhất (HMAC) và phổ biến nhất, nên đứng đầu.
    2. **`Authorization: Bearer <JWT Entra>`** — đường của client không phải
       trình duyệt (script, Product Backend gọi thay người dùng, test tích hợp).
       Xác minh RS256 + JWKS thật.
    3. **Token TĨNH D-14** — chỉ khi `MINICRM_LEGACY_TOKEN_AUTH_ENABLED=true`.

    503 khi KHÔNG lớp nào được cấu hình. 401 khi có lớp nhưng không lớp nào
    nhận — không phân biệt "thiếu" với "sai" trong thông báo.
    """
    entra_on = entra.entra_configured() and session_mod.session_configured()

    if not entra_on and not auth_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Xác thực Mini CRM chưa được cấu hình (chưa có Entra, chưa có token vai trò).",
                "error_code": "AUTH_DISABLED",
            },
        )

    # --- Lớp 1: cookie phiên -------------------------------------------------
    if session_cookie and session_mod.session_configured():
        claims = session_mod.read_session(session_cookie)
        scope_raw = claims.get("scope", [])
        scope: ProjectScope = "ALL" if scope_raw == "ALL" else frozenset(scope_raw or [])
        return MiniCrmPrincipal(role=claims["role"], project_scope=scope)

    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Thiếu thông tin xác thực", "error_code": "MISSING_CREDENTIALS"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --- Lớp 2: JWT Entra ----------------------------------------------------
    # Phân biệt bằng HÌNH DẠNG (JWT có ba đoạn ngăn bởi dấu chấm), không bằng
    # thử-rồi-bắt-lỗi: thử `verify_token` trên một token tĩnh sẽ tạo một lần gọi
    # JWKS vô ích cho mỗi request máy-với-máy.
    if entra_on and token.count(".") == 2:
        identity = entra.verify_token(token)
        role = session_mod.resolve_role(identity)
        scope_value = session_mod.resolve_scope(identity, role)
        scope = "ALL" if scope_value == "ALL" else frozenset(scope_value)
        return MiniCrmPrincipal(role=role, project_scope=scope)

    # --- Lớp 3: token tĩnh D-14 (opt-in) ------------------------------------
    scopes = _scope_map()
    for role, configured in _configured_tokens():
        if secrets.compare_digest(configured, token):
            return MiniCrmPrincipal(role=role, project_scope=scopes.get(token, frozenset()))

    raise HTTPException(
        status_code=401,
        detail={"message": "Thông tin xác thực không hợp lệ", "error_code": "INVALID_CREDENTIALS"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_role(minimum: MiniCrmRole):
    """Factory dependency FastAPI: xác thực rồi đòi vai trò tối thiểu `minimum`.

    403 (không phải 401) khi danh tính hợp lệ nhưng vai trò không đủ.
    """

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
        minicrm_session: str | None = Cookie(default=None, alias=session_mod.SESSION_COOKIE),
    ) -> MiniCrmPrincipal:
        principal = await authenticate(authorization, minicrm_session)
        if _ROLE_LEVEL[principal.role] < _ROLE_LEVEL[minimum]:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": f"Vai trò '{principal.role}' không đủ quyền cho thao tác này",
                    "error_code": "INSUFFICIENT_ROLE",
                },
            )
        return principal

    return dependency


def require_scope(principal: MiniCrmPrincipal, external_project_id: str | None) -> None:
    """`external_project_id=None` nghĩa là KHÔNG suy được dự án (di sản, hoặc
    tham chiếu không hợp lệ để `crud.py` tự từ chối sau) — chỉ phạm vi `ALL` mới
    đủ, vì gán một phạm vi cụ thể cho một bản ghi không xác định được dự án là
    ĐOÁN (§A0). 403 `PROJECT_OUT_OF_SCOPE`, khớp §A7.3 của backend.
    """
    if principal.project_scope == "ALL":
        return
    if external_project_id is not None and external_project_id in principal.project_scope:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
            "error_code": "PROJECT_OUT_OF_SCOPE",
        },
    )
