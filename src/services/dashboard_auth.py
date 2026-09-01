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
    """Danh tính đã xác thực của người gọi mặt vận hành. Không giữ token thô.

    `subject`/`is_ceo` (PR-2, D38's auth-discovery gate): chỉ có giá trị thật ở
    đường OIDC (JWT trực tiếp hoặc session cookie) — token tĩnh và dev-bypass
    không mang danh tính từng người nên luôn để `subject=None, is_ceo=False`,
    CHỦ Ý (fail-closed): việc duyệt giá trị-mode cần CEO không thể nào đi qua
    hai đường xác thực đó, xem `authenticate_dashboard()`.
    """

    role: DashboardRole
    project_scope: ProjectScope = frozenset()
    subject: str | None = None
    is_ceo: bool = False
    # Raw, verified OIDC roles are retained only for action-level policy.  The
    # collapsed ``role`` remains the authority for the global hierarchy; this
    # field lets us distinguish CRM.ADVISOR from CRM.Viewer when both collapse
    # to ``business_viewer``.
    oidc_roles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class DashboardCapabilities:
    """Server-derived UI hints; route dependencies remain authoritative."""

    expert_analysis: bool
    expert_analysis_authoring: bool
    global_config: bool
    ahp_admin: bool
    ceo_review: bool
    config_publish: bool
    ranking_recompute: bool
    # Advisor Analysis is a separate product surface.  These flags are derived
    # only from the verified server principal and are UX hints, never API
    # authority.
    advisor_analysis_access: bool
    advisor_analysis_authoring: bool
    # Distinct from advisor_analysis_authoring (qualitative evidence/rubric
    # authoring) — gates only the new Advisor-authored AHP hierarchy proposal
    # draft/submit surface. Same underlying verified-Advisor predicate today;
    # kept as a separate flag/dependency so the two surfaces can diverge later
    # without touching the qualitative-only flow's own gate.
    advisor_analysis_ahp_authoring: bool
    advisor_analysis_review: bool
    advisor_analysis_admin: bool
    advisor_analysis_view_own: bool
    advisor_analysis_view_submitted: bool
    advisor_analysis_upload_evidence: bool
    advisor_analysis_submit: bool
    advisor_analysis_publish: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "expert_analysis": self.expert_analysis,
            "expert_analysis_authoring": self.expert_analysis_authoring,
            "global_config": self.global_config,
            "ahp_admin": self.ahp_admin,
            "ceo_review": self.ceo_review,
            "config_publish": self.config_publish,
            "ranking_recompute": self.ranking_recompute,
            "advisor_analysis_access": self.advisor_analysis_access,
            "advisor_analysis_authoring": self.advisor_analysis_authoring,
            "advisor_analysis_ahp_authoring": self.advisor_analysis_ahp_authoring,
            "advisor_analysis_review": self.advisor_analysis_review,
            "advisor_analysis_admin": self.advisor_analysis_admin,
            "advisor_analysis_view_own": self.advisor_analysis_view_own,
            "advisor_analysis_view_submitted": self.advisor_analysis_view_submitted,
            "advisor_analysis_upload_evidence": self.advisor_analysis_upload_evidence,
            "advisor_analysis_submit": self.advisor_analysis_submit,
            "advisor_analysis_publish": self.advisor_analysis_publish,
        }


@dataclass(frozen=True, slots=True)
class DashboardRolePresentation:
    """Safe presentation metadata for an already-authenticated principal.

    ``role`` remains the canonical authorization role. ``role_code`` and
    ``role_label`` are derived from verified OIDC context, so a generic viewer
    is never presented as an Advisor merely because both share the read role.
    """

    role: DashboardRole
    role_code: str
    role_label: str
    capabilities: DashboardCapabilities


def is_verified_advisor(principal: DashboardPrincipal) -> bool:
    """True only for the OIDC-backed Advisor identity used by authoring policy."""
    return (
        principal.role == "business_viewer"
        and bool(principal.subject)
        and "CRM.ADVISOR" in principal.oidc_roles
        and (principal.project_scope == "ALL" or bool(principal.project_scope))
    )


def resolve_role_presentation(principal: DashboardPrincipal) -> DashboardRolePresentation:
    """Return non-authoritative role presentation and capability metadata.

    This function never accepts client input. Its authoring value mirrors the
    existing ``require_governance_authoring`` policy; sensitive routes retain
    their own server-side dependencies.
    """
    advisor = is_verified_advisor(principal)
    if advisor:
        role_code, role_label = "advisor", "Advisor"
    elif principal.is_ceo:
        role_code, role_label = "ceo", "CEO"
    elif principal.role == "business_viewer":
        role_code, role_label = "viewer", "Business Viewer"
    elif principal.role == "pipeline_operator" and "CRM.SALES" in principal.oidc_roles:
        role_code, role_label = "sales", "Sales"
    elif principal.role == "pipeline_operator":
        role_code, role_label = "pipeline_operator", "Pipeline Operator"
    else:
        role_code, role_label = "admin", "Admin"

    is_admin = principal.role == "admin"
    is_operator_or_admin = _ROLE_LEVEL[principal.role] >= _ROLE_LEVEL["pipeline_operator"]
    # CRM.CEO is intentionally not inferred from the collapsed admin role.
    # There is currently no separately approved non-CEO Admin review policy.
    ceo_reviewer = principal.is_ceo and principal.role == "admin" and bool(principal.subject)
    return DashboardRolePresentation(
        role=principal.role,
        role_code=role_code,
        role_label=role_label,
        capabilities=DashboardCapabilities(
            expert_analysis=advisor or ceo_reviewer,
            expert_analysis_authoring=advisor,
            global_config=is_admin,
            ahp_admin=is_admin,
            ceo_review=ceo_reviewer,
            config_publish=is_admin,
            ranking_recompute=is_operator_or_admin,
            advisor_analysis_access=advisor or ceo_reviewer,
            advisor_analysis_authoring=advisor,
            advisor_analysis_ahp_authoring=advisor,
            advisor_analysis_review=ceo_reviewer,
            advisor_analysis_admin=False,
            advisor_analysis_view_own=advisor,
            advisor_analysis_view_submitted=ceo_reviewer,
            advisor_analysis_upload_evidence=advisor,
            advisor_analysis_submit=advisor,
            advisor_analysis_publish=False,
        ),
    )


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

    # Keycloak/OIDC đứng TRƯỚC token tĩnh — nó là đường của người dùng thật.
    from src.services import oidc  # import cục bộ: tránh vòng import config

    oidc_on = oidc.oidc_configured()

    if session_cookie and oidc_on:
        claims = await oidc.read_session_verified(session_cookie)
        raw_scope = claims.get("scope", [])
        scope: ProjectScope = "ALL" if raw_scope == "ALL" else frozenset(raw_scope or [])
        return DashboardPrincipal(
            role=claims["role"],
            project_scope=scope,
            subject=claims.get("sub"),
            is_ceo=bool(claims.get("is_ceo", False)),
            oidc_roles=frozenset(claims.get("oidc_roles") or []),
        )

    if settings.app_env == "development" and settings.dev_auth_bypass and authorization is None:
        # TODO: Remove DEV_AUTH_BYPASS when real local authentication is implemented.
        return DashboardPrincipal(role="admin", project_scope="ALL")

    if not settings.dashboard_auth_configured and not oidc_on:
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

    # JWT Keycloak nhận diện bằng HÌNH DẠNG (ba đoạn ngăn bởi dấu chấm) — không
    # thử-rồi-bắt-lỗi, để mỗi request máy-với-máy không tạo một lần tra JWKS vô ích.
    if oidc_on and token.count(".") == 2:
        identity = oidc.verify_token(token)
        role_from_oidc = oidc.resolve_role(identity)
        raw = oidc.resolve_scope(identity)
        return DashboardPrincipal(
            role=role_from_oidc,
            project_scope="ALL" if raw == "ALL" else frozenset(raw),
            subject=identity.subject,
            is_ceo="CRM.CEO" in identity.roles,
            oidc_roles=identity.roles,
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


def require_governance_authoring():
    """Authorize the narrow Expert Analysis authoring surface.

    ``CRM.ADVISOR`` and ``CRM.Viewer`` intentionally collapse to the same
    read role.  Only the explicitly verified Advisor claim may use this
    authoring surface; operators/admins retain their existing access.
    Advisor ownership is derived from the verified OIDC subject by the
    governance router.
    """

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
        absorbiq_session: str | None = Cookie(default=None, alias="absorbiq_session"),
    ) -> DashboardPrincipal:
        principal = await authenticate_dashboard(authorization, absorbiq_session)
        if principal.role == "business_viewer" and "CRM.ADVISOR" not in principal.oidc_roles:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Tài khoản không có quyền soạn phân tích chuyên gia.",
                    "error_code": "GOVERNANCE_AUTHORING_FORBIDDEN",
                },
            )
        # Existing operator/admin token and OIDC paths retain their prior
        # access.  Only the newly admitted Advisor path requires a verified
        # subject because ownership is derived from it below.
        if principal.role == "business_viewer" and not principal.subject:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Thao tác soạn phân tích cần danh tính OIDC đã xác thực.",
                    "error_code": "IDENTITY_REQUIRED",
                },
            )
        return principal

    return dependency


def _analysis_forbidden(message: str = "Tài khoản không có quyền truy cập Phân tích cố vấn.") -> HTTPException:
    """Return one non-enumerating denial shape for the isolated module."""
    return HTTPException(
        status_code=403,
        detail={"message": message, "error_code": "ADVISOR_ANALYSIS_FORBIDDEN"},
    )


def require_advisor_analysis_read():
    """Allow only a fully qualified, scoped CRM.ADVISOR principal."""

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
        absorbiq_session: str | None = Cookie(default=None, alias="absorbiq_session"),
    ) -> DashboardPrincipal:
        principal = await authenticate_dashboard(authorization, absorbiq_session)
        if not is_verified_advisor(principal):
            raise _analysis_forbidden()
        return principal

    return dependency


def require_advisor_analysis_authoring():
    """Advisor Analysis writes have the same strict persona gate as reads."""
    return require_advisor_analysis_read()


def require_advisor_analysis_ahp_authoring():
    """Gates ONLY the Advisor-authored AHP hierarchy proposal draft/submit
    surface — deliberately a separate dependency instance from
    `require_advisor_analysis_authoring()` (same underlying verified-Advisor
    predicate today) so the qualitative-only Advisor Analysis flow's own gate
    can never be accidentally widened by a future change to this one."""
    return require_advisor_analysis_read()


def require_advisor_analysis_reviewer_visibility():
    """Only a verified CEO may enter the reviewer module at present."""

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
        absorbiq_session: str | None = Cookie(default=None, alias="absorbiq_session"),
    ) -> DashboardPrincipal:
        principal = await authenticate_dashboard(authorization, absorbiq_session)
        if not (
            principal.is_ceo
            and principal.role == "admin"
            and bool(principal.subject)
            and (principal.project_scope == "ALL" or bool(principal.project_scope))
        ):
            raise _analysis_forbidden()
        return principal

    return dependency


def require_verified_ceo_analysis_review():
    """CEO approval is distinct from reviewer workspace visibility."""
    return require_advisor_analysis_reviewer_visibility()


def require_ceo():
    """Factory dependency mirroring `require_role()`: xác thực rồi đòi
    `principal.is_ceo is True` (PR-2, D38). Chỉ dùng cho các đường value-mode
    review/approve/publish-verification mới — mọi route `require_role(...)`
    hiện có không đổi.

    403 khi `is_ceo=False`, dùng CHUNG mã lỗi `CEO_APPROVAL_REQUIRED` với
    `src/services/governance.py`'s own check (route-level gate + service-level
    re-check là hai lớp phòng thủ độc lập, không phải một cái thay cho cái kia)."""

    async def dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
        absorbiq_session: str | None = Cookie(default=None, alias="absorbiq_session"),
    ) -> DashboardPrincipal:
        principal = await authenticate_dashboard(authorization, absorbiq_session)
        if not principal.is_ceo:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Thao tác này chỉ CEO (xác thực qua OIDC, vai trò thật CRM.CEO) mới được thực hiện.",
                    "error_code": "CEO_APPROVAL_REQUIRED",
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
