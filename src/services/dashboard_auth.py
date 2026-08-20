"""RBAC cho mặt đọc vận hành (Phase 5.5 P0): business_viewer / pipeline_operator / admin.

Tái dùng ĐÚNG nguyên tắc `require_ops_token` (`src/api/ops.py`) — không dựng một
khung xác thực mới:

1. **Vai trò suy ra từ TOKEN NÀO KHỚP, không bao giờ từ trường client tự khai.**
   Không có `X-Role`. Một client gửi `Authorization: Bearer <token-của-operator>`
   kèm `X-Role: admin` vẫn chỉ được vai trò `pipeline_operator` — token nói lên
   danh tính, request không tự phong cho mình quyền cao hơn.

2. **Chưa cấu hình token nào = ĐÓNG (503), không phải mở.** Giống `ops_api_token`:
   một endpoint nội bộ mặc định mở là một endpoint sẽ bị quên bảo vệ.

3. **`compare_digest` cho mọi phép so token** — hằng thời gian, không rò rỉ theo
   từng byte khớp.

4. **Header `Authorization: Bearer <token>`** — TRÙNG với cơ chế đã có sẵn (nhưng
   chưa ai gọi) ở `frontend/src/api/client.js` (`setAccessToken`/`getAccessToken`).
   Dùng lại đúng header đó: frontend không cần dựng thêm một đường gắn token nào
   khác cho mặt vận hành.

Ba vai trò xếp lồng nhau (`admin` bao gồm mọi quyền của `pipeline_operator`, vốn
bao gồm mọi quyền của `business_viewer`) — khớp bảng quyền tối thiểu của Phase 5.5.
Hành động CÓ ĐIỀU KIỆN (xem payload thô, resend/replay/reprocess) không nằm ở đây:
router tự kiểm thêm cờ `confirm`/`view` sau khi `require_role` đã cho qua, vì điều
kiện đó gắn với TỪNG HÀNH ĐỘNG, không phải một ngưỡng vai trò chung.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from fastapi import Cookie, Header, HTTPException

from src.config import get_settings
from src.logging_config import get_logger

log = get_logger("src.services.dashboard_auth")

DashboardRole = Literal["business_viewer", "pipeline_operator", "admin"]

# Thứ tự quyền — chỉ dùng để so sánh "đủ vai trò chưa", không lộ ra ngoài.
_ROLE_LEVEL: dict[DashboardRole, int] = {
    "business_viewer": 0,
    "pipeline_operator": 1,
    "admin": 2,
}

# Phase E. `ALL` = xuyên mọi dự án (chỉ khi cấp TƯỜNG MINH — không có "admin thì
# mặc định ALL"); một `frozenset[str]` là tập `external_id` (danh tính Mini CRM)
# được cấp; tập RỖNG = không dự án nào.
ProjectScope = frozenset[str] | Literal["ALL"]


@dataclass(frozen=True, slots=True)
class DashboardPrincipal:
    """Danh tính đã xác thực của người gọi mặt vận hành. Không giữ token thô."""

    role: DashboardRole
    project_scope: ProjectScope = frozenset()


def _configured_tokens() -> list[tuple[DashboardRole, str]]:
    settings = get_settings()
    pairs: list[tuple[DashboardRole, str]] = [
        ("business_viewer", settings.dashboard_business_viewer_token.get_secret_value()),
        ("pipeline_operator", settings.dashboard_pipeline_operator_token.get_secret_value()),
        ("admin", settings.dashboard_admin_token.get_secret_value()),
    ]
    return [(role, token) for role, token in pairs if token]


def _scope_map() -> dict[str, ProjectScope]:
    """`{"<token>": ["P-0001", ...]}` hoặc `{"<token>": "ALL"}`. JSON hỏng/rỗng
    ⇒ map rỗng — một giá trị cấu hình sai không được làm sập cả mặt xác thực."""
    raw = get_settings().dashboard_project_scope.get_secret_value()
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


async def authenticate_dashboard(
    authorization: str | None,
    session_cookie: str | None = None,
) -> DashboardPrincipal:
    """Xác thực `Authorization: Bearer <token>` → vai trò khớp token đó.

    503 khi CHƯA cấu hình token nào (đóng, không phải mở). 401 khi thiếu hoặc sai
    token — không phân biệt "thiếu" với "sai" trong thông báo, tránh lộ ra manh
    mối cho người đang dò.
    """
    settings = get_settings()

    # CP5: đường Entra đứng TRƯỚC token tĩnh — nó là đường của người dùng thật.
    from src.services import entra_auth  # import cục bộ: tránh vòng import config

    entra_on = entra_auth.entra_configured()

    if session_cookie and entra_on:
        claims = entra_auth.read_session(session_cookie)
        raw_scope = claims.get("scope", [])
        scope: ProjectScope = "ALL" if raw_scope == "ALL" else frozenset(raw_scope or [])
        return DashboardPrincipal(role=claims["role"], project_scope=scope)

    if settings.app_env == "development" and settings.dev_auth_bypass and authorization is None:
        # TODO: Remove DEV_AUTH_BYPASS when real local authentication is implemented.
        return DashboardPrincipal(role="admin", project_scope="ALL")

    if not settings.dashboard_auth_configured and not entra_on:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Mặt đọc vận hành chưa được cấu hình (chưa có token vai trò nào).",
                "error_code": "DASHBOARD_AUTH_DISABLED",
            },
        )

    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"message": "Thiếu thông tin xác thực", "error_code": "MISSING_CREDENTIALS"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # JWT Entra nhận diện bằng HÌNH DẠNG (ba đoạn ngăn bởi dấu chấm) — không
    # thử-rồi-bắt-lỗi, để mỗi request máy-với-máy không tạo một lần tra JWKS vô ích.
    if entra_on and token.count(".") == 2:
        identity = entra_auth.verify_token(token)
        role_from_entra = entra_auth.resolve_role(identity)
        raw = entra_auth.resolve_scope(identity)
        return DashboardPrincipal(
            role=role_from_entra,
            project_scope="ALL" if raw == "ALL" else frozenset(raw),
        )

    scopes = _scope_map()
    for role, configured in _configured_tokens():
        if secrets.compare_digest(configured, token):
            return DashboardPrincipal(role=role, project_scope=scopes.get(token, frozenset()))

    log.warning("dashboard.auth.rejected", reason="no_match")
    raise HTTPException(
        status_code=401,
        detail={"message": "Thông tin xác thực không hợp lệ", "error_code": "INVALID_CREDENTIALS"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def role_level(role: DashboardRole) -> int:
    """Mức quyền của một vai trò — dùng khi router tự so sánh ngoài `require_role`
    (ví dụ một hành động chấp nhận CẢ token vai trò LẪN một cơ chế xác thực khác)."""
    return _ROLE_LEVEL[role]


def require_role(minimum: DashboardRole):
    """Factory dependency FastAPI: xác thực rồi đòi vai trò tối thiểu `minimum`.

    403 (không phải 401) khi danh tính hợp lệ nhưng vai trò không đủ — 401 dành
    cho "chưa chứng minh được danh tính", 403 cho "danh tính đúng, không đủ quyền".
    """

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
        absorbiq_session: str | None = Cookie(default=None, alias="absorbiq_session"),
    ) -> DashboardPrincipal:
        principal = await authenticate_dashboard(authorization, absorbiq_session)
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


def audit(action: str, principal: DashboardPrincipal, **fields: object) -> None:
    """Vết audit CHỈ QUA LOG có cấu trúc — không có bảng audit (không migration).

    Đủ để tra "ai (vai trò nào) đã làm gì, khi nào" qua công cụ tổng hợp log hiện
    có; một bảng audit truy vấn được là quyết định lớn hơn cần một migration mới,
    xem `pipeline_status.md`.
    """
    log.info(f"dashboard.audit.{action}", role=principal.role, **fields)


# --- Phạm vi dự án (Phase E) --------------------------------------------------
#
# Cưỡng chế ở TẦNG TRUY VẤN, không chỉ tầng route (§A7.3 phase_a_domain_freeze.md)
# — router gọi `require_project_in_scope` SAU KHI đã suy ra `external_id` của dự
# án mục tiêu (dù từ `project_id` UUID hay `external_project_id`), và gọi
# `resolve_scope_project_ids` TRƯỚC KHI dựng câu truy vấn liệt kê không có phạm
# vi tường minh (`GET /projects`) — cả hai đường đều không có "admin thì bỏ qua":
# `ALL` phải được CẤP tường minh qua `dashboard_project_scope`.


def scope_permits(principal: DashboardPrincipal, external_project_id: str | None) -> bool:
    """`external_project_id=None` nghĩa là KHÔNG suy được dự án (dự án/phân khu
    DI SẢN trước Phase D, hoặc một lô đồng bộ không gắn dự án nào) — chỉ phạm vi
    `ALL` mới đủ, vì gán một phạm vi cụ thể cho thứ không xác định được dự án là
    ĐOÁN (§A0)."""
    if principal.project_scope == "ALL":
        return True
    return external_project_id is not None and external_project_id in principal.project_scope


def require_project_in_scope(principal: DashboardPrincipal, external_project_id: str | None) -> None:
    """403 `PROJECT_OUT_OF_SCOPE` (không phải 404) — sự tồn tại của một dự án
    ngoài phạm vi không phải thông tin nên tiết lộ khác đi, nhưng che nó thành
    404 sẽ khiến người gọi tưởng dự án không tồn tại thay vì họ không có quyền,
    và đó là hai sự thật khác nhau mà FE (Phase F/G) cần phân biệt được để hiện
    đúng thông báo."""
    if scope_permits(principal, external_project_id):
        return
    audit("scope_denied", principal, external_project_id=external_project_id)
    raise HTTPException(
        status_code=403,
        detail={
            "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
            "error_code": "PROJECT_OUT_OF_SCOPE",
        },
    )


async def resolve_scope_project_ids(session, principal: DashboardPrincipal) -> str | list[uuid.UUID]:
    """Phạm vi (tập `external_id`) → tập UUID nội bộ, cho câu truy vấn LIỆT KÊ
    không có phạm vi tường minh trong query string (`GET /projects`).

    Trả `"ALL"` nguyên văn khi phạm vi là `ALL` — router tự nhận biết và BỎ mệnh
    đề `WHERE` thay vì truyền một danh sách rỗng (danh sách rỗng đúng nghĩa
    "không dự án nào", khác hẳn "mọi dự án").
    """
    if principal.project_scope == "ALL":
        return "ALL"
    if not principal.project_scope:
        return []
    from src.models.tables import projects

    rows = await session.execute(
        sa.select(projects.c.id).where(projects.c.external_id.in_(principal.project_scope))
    )
    return [row[0] for row in rows]
