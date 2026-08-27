"""Quản trị vòng đời đề xuất trọng số của chuyên gia — 0033/0034 có bảng, module
này là nơi ghi.

╔══════════════════════════════════════════════════════════════════════════════╗
║  Nơi ghi DUY NHẤT vào bảy bảng governance (0034) + hai bảng evidence (0033   ║
║  phần không thuộc `src/ranking/service.py`): xem `ALLOWED_WRITERS` trong     ║
║  `tests/test_ranking_boundary.py`.                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝

Module này KHÔNG viết vào `ranking_configs`. Publish một bộ trọng số vẫn đi
qua đúng con đường đã có (`src/services/ranking_config.py::create_draft`/
`publish`) — y hệt kỷ luật `src/api/ahp.py` đã theo: "mở thêm một đường ghi
thứ hai chỉ để đỡ một lần gọi API là đánh đổi một bất biến thật lấy một tiện
nghi nhỏ". `mark_published()` ở đây chỉ GHI NHẬN rằng đề xuất đã gắn với một
config đã published từ trước — nó SELECT để xác nhận, không bao giờ UPDATE
`ranking_configs`.

Máy trạng thái của `ranking_weight_proposals.status`
(CHECK trong 0034, xem `alembic/versions/0034_expert_ranking_governance.py`):

    draft --submit--> submitted --review(approved)--> approved --publish--> published
      |                   |                |
      |                   +--review(rejected)--> rejected (chốt)
      |                   |
      |                   +--review(request_changes)--> submitted (không đổi, chờ sửa)
      |
      +--withdraw--> withdrawn (chốt, chỉ từ draft/submitted)

CHECK constraint ép `approved_at IS NULL OR submitted_at IS NOT NULL` và
`published_at IS NULL OR approved_at IS NOT NULL` — thứ tự trên là thứ tự
DUY NHẤT database chấp nhận, module này chỉ hiện thực hoá nó.

**Bảng nào cho phép UPDATE.** `ranking_evidence_documents`,
`ranking_evidence_document_features`, `ranking_proposal_reviews`,
`ranking_config_audit_events` bị `ranking_governance_append_only_guard`
(0034) chặn UPDATE/DELETE hoàn toàn — mọi hàm ghi các bảng đó ở dưới chỉ bao
giờ INSERT. `expert_profiles`, `ranking_weight_proposals`,
`ranking_feature_justifications` KHÔNG có trigger đó — chúng mang trạng thái
sống (status, updated_at) nên UPDATE tại chỗ là đúng thiết kế.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import (
    areas,
    expert_profiles,
    ranking_config_audit_events,
    ranking_configs,
    ranking_evidence_document_features,
    ranking_evidence_documents,
    ranking_feature_definitions,
    ranking_feature_justifications,
    ranking_proposal_reviews,
    ranking_weight_proposals,
)

log = get_logger("src.services.governance")

ASSERTION_KINDS = ("weight", "value")
VALUE_SCOPE_TYPES = ("project", "area", "market")
# Shelf-life ceiling, days, keyed by feature_key substring — the already-decided
# Market figures (`ranking_consultant.md` §24.5), not a new freshness policy.
# D26 (broader per-factor-type freshness beyond these two) remains PENDING and
# is NOT resolved by this map.
_MARKET_MAX_SHELF_LIFE_DAYS = {"market_interest_rate": 30}
_MARKET_DEFAULT_MAX_SHELF_LIFE_DAYS = 90

# PR-5: `area_velocity_norm`/`area_conversion_norm` are legacy operational
# features (`src/ranking/service.py::_area_features()`,
# `src/services/ranking_config.py::OPERATIONAL_FEATURES`) — never authored
# through this module, never a `ranking_feature_definitions` row (0041
# deliberately seeds no definition for either key, which alone would already
# make `upsert_justification()` reject them with `FEATURE_DEFINITION_NOT_FOUND`
# since no such row can exist to reference). This explicit check is
# defense-in-depth against that same rejection ever silently stopping being
# true (e.g. if some future migration accidentally registered one), and gives
# a clear, dedicated error code instead of an incidental "not found".
CRM_OWNED_AREA_FEATURE_KEYS = frozenset({"area_velocity_norm", "area_conversion_norm"})

PROPOSAL_STATUSES = (
    "draft",
    "submitted",
    "under_review",
    "approved",
    "rejected",
    "withdrawn",
    "published",
)
REVIEW_DECISIONS = ("approved", "rejected", "request_changes")
EXPECTED_EFFECTS = ("increase", "decrease", "neutral", "context_dependent")
CONFIDENCE_LEVELS = ("low", "medium", "high")
EVIDENCE_MIME_TYPES = ("application/pdf", "text/plain", "text/markdown")

# Trạng thái mà một justification còn SỬA được. Sau khi đề xuất rời `draft`,
# bằng chứng đã nộp phải đứng yên — sửa `rationale` sau khi reviewer đã đọc
# biến vết duyệt thành duyệt một thứ khác với thứ đang publish.
_JUSTIFICATION_EDITABLE_STATUSES = ("draft",)
_REVIEWABLE_STATUSES = ("submitted", "under_review")


# grain -> compatible value-mode scope_type(s). 'unit' is deliberately absent:
# unit-grain features stay 100% CRM (D37/§24.5), never an expert override.
_GRAIN_SCOPE_COMPATIBILITY: dict[str, frozenset[str]] = {
    "market": frozenset({"market"}),
    "project": frozenset({"project"}),
    "area": frozenset({"area"}),
    "project_area": frozenset({"project", "area"}),
}


def _check_grain_scope_compatibility(grain: str, scope_type: str) -> None:
    """Cross-table rule (`ranking_feature_definitions.grain` vs. this proposal's
    `scope_type`) — a Postgres CHECK constraint cannot express this (single-
    table only), so it is service-level, checked on every justification write."""
    if grain == "unit":
        raise GovernanceError(
            "GRAIN_NOT_ASSERTABLE",
            "Đặc trưng phạm vi 'unit' là 100% CRM — không có value-mode assertion cho grain này",
        )
    allowed = _GRAIN_SCOPE_COMPATIBILITY.get(grain, frozenset())
    if scope_type not in allowed:
        raise GovernanceError(
            "GRAIN_SCOPE_MISMATCH",
            f"feature_definition có grain='{grain}', không tương thích với scope_type='{scope_type}'",
        )


def _max_shelf_life_days(feature_key: str, definition_metadata: dict) -> int:
    configured = (definition_metadata or {}).get("max_shelf_life_days")
    if isinstance(configured, int) and configured > 0:
        return configured
    return _MARKET_MAX_SHELF_LIFE_DAYS.get(feature_key, _MARKET_DEFAULT_MAX_SHELF_LIFE_DAYS)


def _validate_market_submission(justification: dict, feature: dict) -> None:
    """Submit-time only (not draft-time, per the task's own "drafts may stay
    incremental" allowance) — a market-scope value assertion needs a citation,
    an effective date, and an expiry that does not exceed the already-decided
    30/90-day shelf life (`ranking_consultant.md` §24.5), before it may leave
    `draft`."""
    if not (justification["external_source_citation"] or "").strip():
        raise GovernanceError(
            "MARKET_CITATION_REQUIRED", "Market-scope value assertion cần external_source_citation trước khi nộp"
        )
    if justification["effective_at"] is None:
        raise GovernanceError("MARKET_EFFECTIVE_AT_REQUIRED", "Market-scope value assertion cần effective_at")
    max_days = _max_shelf_life_days(feature["feature_key"], feature["definition_metadata"])
    ceiling = justification["effective_at"] + timedelta(days=max_days)
    if justification["expires_at"] is None:
        return  # PR-3's snapshot builder derives it from effective_at + policy at read time
    if justification["expires_at"] > ceiling:
        raise GovernanceError(
            "MARKET_EXPIRY_EXCEEDS_POLICY",
            f"expires_at vượt quá hạn sử dụng tối đa {max_days} ngày kể từ effective_at cho đặc trưng này",
        )


class GovernanceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


async def _record_audit_event(
    session,
    *,
    ranking_config_id: uuid.UUID | None,
    proposal_id: uuid.UUID | None,
    actor_expert_id: uuid.UUID | None,
    actor_identity_subject: str,
    event_type: str,
    before_status: str | None,
    after_status: str | None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    """INSERT-only — `ranking_config_audit_events` là append-only (0034).

    Gọi TRONG CÙNG transaction với thay đổi nó ghi lại: hai câu ghi tách rời
    là hai điểm có thể lệch nhau nếu tiến trình chết ở giữa.
    """
    await session.execute(
        sa.insert(ranking_config_audit_events).values(
            id=uuid.uuid4(),
            ranking_config_id=ranking_config_id,
            proposal_id=proposal_id,
            actor_expert_id=actor_expert_id,
            actor_identity_subject=actor_identity_subject,
            event_type=event_type,
            before_status=before_status,
            after_status=after_status,
            before_state=before_state or {},
            after_state=after_state or {},
            created_at=_now(),
        )
    )


# --- Chuyên gia ---------------------------------------------------------------


async def get_or_create_expert_profile(
    *,
    identity_subject: str,
    organization: str | None = None,
    title: str | None = None,
    expertise_summary: str | None = None,
) -> dict:
    """Tự đăng ký / lấy lại hồ sơ chuyên gia theo `identity_subject`.

    `DashboardPrincipal` (`src/services/dashboard_auth.py`) chỉ mang `role` +
    `project_scope`, KHÔNG mang danh tính từng người — cùng khoảng trống mà
    `src/services/ranking_config.py::create_draft(created_by: str, ...)` đã
    sống chung từ trước bằng cách nhận danh tính như một tham số của người gọi
    thay vì tự suy ra từ token. Hàm này theo đúng tiền lệ đó: `identity_subject`
    do CALLER khai, không do middleware xác thực cấp — xem D18 trong báo cáo
    audit đi kèm việc thêm module này.
    """
    if not identity_subject.strip():
        raise GovernanceError("IDENTITY_SUBJECT_REQUIRED", "identity_subject không được rỗng")

    async with get_session_factory()() as session:
        existing = (
            await session.execute(
                sa.select(expert_profiles).where(expert_profiles.c.identity_subject == identity_subject.strip())
            )
        ).mappings().first()
        if existing is not None:
            await session.rollback()
            return dict(existing)

        now = _now()
        expert_id = uuid.uuid4()
        await session.execute(
            sa.insert(expert_profiles).values(
                id=expert_id,
                user_id=None,
                identity_subject=identity_subject.strip(),
                organization=organization,
                title=title,
                expertise_summary=expertise_summary,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
        row = (
            await session.execute(sa.select(expert_profiles).where(expert_profiles.c.id == expert_id))
        ).mappings().first()
        await session.rollback()

    log.info("governance.expert.registered", expert_id=str(expert_id), identity_subject=identity_subject)
    return dict(row)


async def get_expert_profile(expert_id: uuid.UUID) -> dict:
    async with get_session_factory()() as session:
        row = (
            await session.execute(sa.select(expert_profiles).where(expert_profiles.c.id == expert_id))
        ).mappings().first()
        await session.rollback()
    if row is None:
        raise GovernanceError("EXPERT_NOT_FOUND", f"Không có expert_profile {expert_id}")
    return dict(row)


# --- Đề xuất -------------------------------------------------------------------


async def create_proposal(
    *,
    base_config_id: uuid.UUID | None = None,
    project_id: uuid.UUID,
    created_by_expert_id: uuid.UUID,
    assertion_kind: str = "weight",
    scope_type: str = "project",
    area_id: uuid.UUID | None = None,
) -> dict:
    """Tạo đề xuất ở trạng thái `draft`. Chưa ảnh hưởng `ranking_configs` nào.

    `assertion_kind='weight'` (mặc định) chạy ĐÚNG con đường đã có từ trước —
    `scope_type`/`area_id` bị BỎ QUA, luôn ép `'project'`/`None` như hôm nay,
    `base_config_id` bắt buộc như hôm nay. `assertion_kind='value'` (PR-2,
    D37/D38) là nhánh MỚI: không có `ranking_configs` nào liên quan
    (`base_config_id` phải là `None`), `scope_type` có thể là
    `project`/`area`/`market`, và hình dạng `area_id` phải khớp `scope_type`
    (`ck_rwp_scope_shape`, 0038)."""
    if assertion_kind not in ASSERTION_KINDS:
        raise GovernanceError("ASSERTION_KIND_INVALID", f"assertion_kind phải thuộc {ASSERTION_KINDS}")

    if assertion_kind == "weight":
        if base_config_id is None:
            raise GovernanceError("BASE_CONFIG_REQUIRED", "assertion_kind='weight' cần base_config_id")
        row_scope_type = "project"
        row_area_id = None
    else:
        if base_config_id is not None:
            raise GovernanceError(
                "BASE_CONFIG_NOT_ALLOWED", "assertion_kind='value' không được kèm base_config_id"
            )
        if scope_type not in VALUE_SCOPE_TYPES:
            raise GovernanceError("SCOPE_TYPE_INVALID", f"scope_type phải thuộc {VALUE_SCOPE_TYPES}")
        if scope_type == "area" and area_id is None:
            raise GovernanceError("AREA_ID_REQUIRED", "scope_type='area' cần area_id")
        if scope_type in ("project", "market") and area_id is not None:
            raise GovernanceError(
                "AREA_ID_NOT_ALLOWED", f"scope_type='{scope_type}' không được kèm area_id"
            )
        row_scope_type = scope_type
        row_area_id = area_id

    async with get_session_factory()() as session:
        if base_config_id is not None:
            base = (
                await session.execute(sa.select(ranking_configs.c.id).where(ranking_configs.c.id == base_config_id))
            ).first()
            if base is None:
                await session.rollback()
                raise GovernanceError("BASE_CONFIG_NOT_FOUND", f"Không có ranking_configs {base_config_id}")
        expert = (
            await session.execute(sa.select(expert_profiles.c.id).where(expert_profiles.c.id == created_by_expert_id))
        ).first()
        if expert is None:
            await session.rollback()
            raise GovernanceError("EXPERT_NOT_FOUND", f"Không có expert_profile {created_by_expert_id}")

        if row_scope_type == "area":
            # A Postgres CHECK cannot express this (cross-table) — `area_id`
            # existing (`fk_rwp_area_id`, 0034) is not the same fact as it
            # belonging to THIS proposal's `project_id`. An Area assertion
            # cannot cross projects (PR-5).
            area_project_id = await session.scalar(sa.select(areas.c.project_id).where(areas.c.id == row_area_id))
            if area_project_id is None:
                await session.rollback()
                raise GovernanceError("AREA_NOT_FOUND", f"Không có areas {row_area_id}")
            if uuid.UUID(str(area_project_id)) != uuid.UUID(str(project_id)):
                await session.rollback()
                raise GovernanceError(
                    "AREA_PROJECT_MISMATCH", f"area {row_area_id} không thuộc dự án {project_id}"
                )

        now = _now()
        proposal_id = uuid.uuid4()
        await session.execute(
            sa.insert(ranking_weight_proposals).values(
                id=proposal_id,
                base_config_id=base_config_id,
                proposed_config_id=None,
                scope_type=row_scope_type,
                project_id=project_id,
                area_id=row_area_id,
                status="draft",
                created_by_expert_id=created_by_expert_id,
                submitted_at=None,
                approved_at=None,
                published_at=None,
                created_at=now,
                updated_at=now,
                assertion_kind=assertion_kind,
            )
        )
        await _record_audit_event(
            session,
            ranking_config_id=None,
            proposal_id=proposal_id,
            actor_expert_id=created_by_expert_id,
            actor_identity_subject=(await get_expert_profile(created_by_expert_id))["identity_subject"],
            event_type="created",
            before_status=None,
            after_status="draft",
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.proposal.created", proposal_id=str(proposal_id), project_id=str(project_id))
    return row


async def _fetch_proposal(session, proposal_id: uuid.UUID) -> dict | None:
    row = (
        await session.execute(sa.select(ranking_weight_proposals).where(ranking_weight_proposals.c.id == proposal_id))
    ).mappings().first()
    return dict(row) if row is not None else None


async def get_proposal(proposal_id: uuid.UUID) -> dict:
    async with get_session_factory()() as session:
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()
    if row is None:
        raise GovernanceError("PROPOSAL_NOT_FOUND", f"Không có ranking_weight_proposals {proposal_id}")
    return row


async def list_proposals(*, project_id: uuid.UUID | None = None, status: str | None = None) -> list[dict]:
    stmt = sa.select(ranking_weight_proposals).order_by(ranking_weight_proposals.c.created_at.desc())
    if project_id is not None:
        stmt = stmt.where(ranking_weight_proposals.c.project_id == project_id)
    if status is not None:
        stmt = stmt.where(ranking_weight_proposals.c.status == status)
    async with get_session_factory()() as session:
        rows = (await session.execute(stmt)).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


async def _require_proposal_for_update(session, proposal_id: uuid.UUID, allowed_statuses: tuple[str, ...]) -> dict:
    row = (
        await session.execute(
            sa.select(ranking_weight_proposals).where(ranking_weight_proposals.c.id == proposal_id).with_for_update()
        )
    ).mappings().first()
    if row is None:
        raise GovernanceError("PROPOSAL_NOT_FOUND", f"Không có ranking_weight_proposals {proposal_id}")
    if row["status"] not in allowed_statuses:
        raise GovernanceError(
            "PROPOSAL_STATUS_INVALID",
            f"Đề xuất đang ở trạng thái '{row['status']}', cần một trong {allowed_statuses}",
        )
    return dict(row)


async def submit_proposal(*, proposal_id: uuid.UUID, actor_expert_id: uuid.UUID) -> dict:
    """`draft` → `submitted`. Đòi ÍT NHẤT một justification — một đề xuất
    không kèm lý do nào thì không có gì cho reviewer đọc.

    Value-mode (PR-2), thêm hai điều kiện chỉ áp cho nhánh đó: ít nhất một
    evidence document đã liên kết, và (nếu `scope_type='market'`) citation +
    effective_at + expiry-trong-hạn (§24.5's 30/90-ngày). Weight-mode: hai
    điều kiện này không tồn tại, không đổi."""
    async with get_session_factory()() as session:
        before = await _require_proposal_for_update(session, proposal_id, ("draft",))
        justifications = (
            await session.execute(
                sa.select(ranking_feature_justifications).where(
                    ranking_feature_justifications.c.proposal_id == proposal_id
                )
            )
        ).mappings().all()
        if not justifications:
            await session.rollback()
            raise GovernanceError(
                "NO_JUSTIFICATIONS",
                "Đề xuất chưa có justification nào — thêm ít nhất một trước khi nộp",
            )

        if before["assertion_kind"] == "value":
            for justification in justifications:
                evidence_count = await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(ranking_evidence_document_features)
                    .where(ranking_evidence_document_features.c.feature_justification_id == justification["id"])
                )
                if not evidence_count:
                    await session.rollback()
                    raise GovernanceError(
                        "EVIDENCE_REQUIRED",
                        f"Value assertion {justification['id']} chưa có evidence nào liên kết — "
                        "cần ít nhất một trước khi nộp",
                    )
                if before["scope_type"] == "market":
                    feature = (
                        await session.execute(
                            sa.select(
                                ranking_feature_definitions.c.feature_key,
                                ranking_feature_definitions.c.definition_metadata,
                            ).where(ranking_feature_definitions.c.id == justification["feature_definition_id"])
                        )
                    ).mappings().first()
                    _validate_market_submission(justification, feature)

        now = _now()
        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal_id)
            .values(status="submitted", submitted_at=now, updated_at=now)
        )
        expert = await get_expert_profile(actor_expert_id)
        await _record_audit_event(
            session,
            ranking_config_id=None,
            proposal_id=proposal_id,
            actor_expert_id=actor_expert_id,
            actor_identity_subject=expert["identity_subject"],
            event_type="submitted",
            before_status=before["status"],
            after_status="submitted",
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.proposal.submitted", proposal_id=str(proposal_id))
    return row


async def withdraw_proposal(*, proposal_id: uuid.UUID, actor_expert_id: uuid.UUID) -> dict:
    """`draft`/`submitted`/`under_review` → `withdrawn` (chốt). Người tạo có
    thể rút đề xuất bất cứ lúc nào trước khi nó được duyệt."""
    async with get_session_factory()() as session:
        before = await _require_proposal_for_update(session, proposal_id, ("draft", "submitted", "under_review"))
        now = _now()
        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal_id)
            .values(status="withdrawn", updated_at=now)
        )
        expert = await get_expert_profile(actor_expert_id)
        await _record_audit_event(
            session,
            ranking_config_id=None,
            proposal_id=proposal_id,
            actor_expert_id=actor_expert_id,
            actor_identity_subject=expert["identity_subject"],
            event_type="rolled_back",
            before_status=before["status"],
            after_status="withdrawn",
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.proposal.withdrawn", proposal_id=str(proposal_id))
    return row


# --- Justification --------------------------------------------------------------


async def upsert_justification(
    *,
    proposal_id: uuid.UUID,
    feature_definition_id: uuid.UUID,
    previous_weight: Decimal | None = None,
    proposed_weight: Decimal | None = None,
    rationale: str,
    methodology: str,
    evidence_summary: str,
    expected_effect: str,
    confidence: str,
    limitations: str,
    created_by_expert_id: uuid.UUID,
    assertion_kind: str = "weight",
    raw_numeric: Decimal | None = None,
    normalized_numeric: Decimal | None = None,
    categorical_value: str | None = None,
    effective_at: datetime | None = None,
    expires_at: datetime | None = None,
    external_source_citation: str | None = None,
    author_subject: str | None = None,
) -> dict:
    """Tạo hoặc sửa justification cho MỘT feature trong MỘT đề xuất
    (`uq_ranking_feature_justification_proposal_feature`, 0034). Chỉ khi đề
    xuất còn `draft` — xem docstring module.

    `assertion_kind='weight'` (mặc định) chạy ĐÚNG con đường đã có — mọi tham
    số value-mode bị bỏ qua/phải rỗng. `assertion_kind='value'` (PR-2) là
    nhánh MỚI: `proposed_weight`/`previous_weight` phải là `None`
    (`ck_rfj_assertion_mode_xor`, 0038); rationale/methodology/evidence
    fields dùng CHUNG yêu cầu không rỗng như weight-mode."""
    if assertion_kind not in ASSERTION_KINDS:
        raise GovernanceError("ASSERTION_KIND_INVALID", f"assertion_kind phải thuộc {ASSERTION_KINDS}")
    for field_name, value, allowed in (
        ("expected_effect", expected_effect, EXPECTED_EFFECTS),
        ("confidence", confidence, CONFIDENCE_LEVELS),
    ):
        if value not in allowed:
            raise GovernanceError(
                f"{field_name.upper()}_INVALID", f"{field_name} phải thuộc {allowed}, nhận '{value}'"
            )
    for field_name, value in (
        ("rationale", rationale),
        ("methodology", methodology),
        ("evidence_summary", evidence_summary),
        ("limitations", limitations),
    ):
        if not value.strip():
            raise GovernanceError(f"{field_name.upper()}_REQUIRED", f"{field_name} không được rỗng")

    if assertion_kind == "weight":
        if proposed_weight is None or not (0 <= proposed_weight <= 1):
            raise GovernanceError("PROPOSED_WEIGHT_RANGE", "proposed_weight phải trong [0, 1]")
        if any(
            v is not None
            for v in (raw_numeric, normalized_numeric, categorical_value, effective_at, expires_at,
                       external_source_citation, author_subject)
        ):
            raise GovernanceError(
                "VALUE_FIELDS_NOT_ALLOWED", "assertion_kind='weight' không được kèm trường value-mode nào"
            )
    else:
        if proposed_weight is not None or previous_weight is not None:
            raise GovernanceError(
                "WEIGHT_FIELDS_NOT_ALLOWED", "assertion_kind='value' không được kèm proposed_weight/previous_weight"
            )
        if normalized_numeric is not None and not (0 <= normalized_numeric <= 1):
            raise GovernanceError("NORMALIZED_VALUE_RANGE", "normalized_numeric phải trong [0, 1]")
        if raw_numeric is None and normalized_numeric is None and not (categorical_value or "").strip():
            raise GovernanceError(
                "VALUE_REQUIRED", "assertion_kind='value' cần raw_numeric, normalized_numeric, hoặc categorical_value"
            )

    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, _JUSTIFICATION_EDITABLE_STATUSES)
        if proposal["assertion_kind"] != assertion_kind:
            await session.rollback()
            raise GovernanceError(
                "ASSERTION_KIND_MISMATCH",
                f"Đề xuất {proposal_id} là '{proposal['assertion_kind']}', không phải '{assertion_kind}'",
            )
        feature = (
            await session.execute(
                sa.select(ranking_feature_definitions.c.id, ranking_feature_definitions.c.grain).where(
                    ranking_feature_definitions.c.id == feature_definition_id
                )
            )
        ).mappings().first()
        if feature is None:
            await session.rollback()
            raise GovernanceError(
                "FEATURE_DEFINITION_NOT_FOUND", f"Không có ranking_feature_definitions {feature_definition_id}"
            )
        if assertion_kind == "value":
            _check_grain_scope_compatibility(feature["grain"], proposal["scope_type"])
            feature_key = await session.scalar(
                sa.select(ranking_feature_definitions.c.feature_key).where(
                    ranking_feature_definitions.c.id == feature_definition_id
                )
            )
            if feature_key in CRM_OWNED_AREA_FEATURE_KEYS:
                await session.rollback()
                raise GovernanceError(
                    "AREA_CRM_OWNED_FEATURE_KEY_NOT_ASSERTABLE",
                    f"'{feature_key}' là đặc trưng CRM — không có value-mode assertion cho khoá này",
                )

        existing = (
            await session.execute(
                sa.select(ranking_feature_justifications.c.id).where(
                    ranking_feature_justifications.c.proposal_id == proposal_id,
                    ranking_feature_justifications.c.feature_definition_id == feature_definition_id,
                )
            )
        ).first()
        now = _now()
        values = dict(
            previous_weight=previous_weight,
            proposed_weight=proposed_weight,
            rationale=rationale.strip(),
            methodology=methodology.strip(),
            evidence_summary=evidence_summary.strip(),
            expected_effect=expected_effect,
            confidence=confidence,
            limitations=limitations.strip(),
            created_by_expert_id=created_by_expert_id,
            updated_at=now,
            assertion_kind=assertion_kind,
            raw_numeric=raw_numeric,
            normalized_numeric=normalized_numeric,
            categorical_value=categorical_value.strip() if categorical_value else None,
            effective_at=effective_at,
            expires_at=expires_at,
            external_source_citation=external_source_citation.strip() if external_source_citation else None,
            author_subject=author_subject,
        )
        if existing is None:
            justification_id = uuid.uuid4()
            await session.execute(
                sa.insert(ranking_feature_justifications).values(
                    id=justification_id,
                    proposal_id=proposal_id,
                    feature_definition_id=feature_definition_id,
                    created_at=now,
                    **values,
                )
            )
        else:
            justification_id = existing[0]
            await session.execute(
                sa.update(ranking_feature_justifications)
                .where(ranking_feature_justifications.c.id == justification_id)
                .values(**values)
            )

        expert = await get_expert_profile(created_by_expert_id)
        await _record_audit_event(
            session,
            ranking_config_id=None,
            proposal_id=proposal_id,
            actor_expert_id=created_by_expert_id,
            actor_identity_subject=expert["identity_subject"],
            event_type="submitted" if proposal["status"] == "draft" else "reviewed",
            before_status=proposal["status"],
            after_status=proposal["status"],
            after_state={"feature_definition_id": str(feature_definition_id), "proposed_weight": str(proposed_weight)},
        )
        await session.commit()
        row = (
            await session.execute(
                sa.select(ranking_feature_justifications).where(
                    ranking_feature_justifications.c.id == justification_id
                )
            )
        ).mappings().first()
        await session.rollback()

    log.info("governance.justification.upserted", proposal_id=str(proposal_id), justification_id=str(justification_id))
    return dict(row)


async def get_justification(feature_justification_id: uuid.UUID) -> dict | None:
    """Tra một justification theo ID của chính nó — bổ sung cho
    `list_justifications` (theo `proposal_id`). Cần cho §21.7:
    `retrieve_and_validate` chỉ có `feature_justification_id`, chưa biết
    `proposal_id` trước khi tra."""
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ranking_feature_justifications).where(
                    ranking_feature_justifications.c.id == feature_justification_id
                )
            )
        ).mappings().first()
        await session.rollback()
    return dict(row) if row else None


async def list_justifications(proposal_id: uuid.UUID) -> list[dict]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_feature_justifications)
                .where(ranking_feature_justifications.c.proposal_id == proposal_id)
                .order_by(ranking_feature_justifications.c.created_at)
            )
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


# --- Bằng chứng (upload) ---------------------------------------------------------


async def register_evidence_document(
    *,
    proposal_id: uuid.UUID | None,
    uploaded_by_expert_id: uuid.UUID,
    original_filename: str,
    mime_type: str,
    object_storage_key: str,
    sha256_checksum: str,
    file_size_bytes: int,
) -> dict:
    """INSERT-only — bảng này append-only (0034). Việc LƯU FILE vật lý (băm
    checksum, chọn `object_storage_key`, giới hạn dung lượng) là việc của một
    lớp lưu trữ riêng, theo đúng phân tách đã có giữa
    `src/services/file_upload.py` (chỉ lưu trữ) và `src/services/excel_parser.py`
    (chỉ đọc nội dung) — hàm này chỉ ghi HÀNG SIÊU DỮ LIỆU sau khi file đã nằm
    an toàn trên đĩa/object storage, không tự nhận multipart.

    Không kích hoạt trích xuất/embedding — đó là §21 (Phase 4), vẫn đang
    `NOT FOUND` và bị khoá sau P5+P6 theo audit 2026-08-25.
    """
    if mime_type not in EVIDENCE_MIME_TYPES:
        raise GovernanceError("MIME_TYPE_INVALID", f"mime_type phải thuộc {EVIDENCE_MIME_TYPES}")
    if file_size_bytes <= 0:
        raise GovernanceError("FILE_SIZE_INVALID", "file_size_bytes phải dương")
    if not original_filename.strip():
        raise GovernanceError("FILENAME_REQUIRED", "original_filename không được rỗng")

    async with get_session_factory()() as session:
        expert = (
            await session.execute(
                sa.select(expert_profiles.c.id).where(expert_profiles.c.id == uploaded_by_expert_id)
            )
        ).first()
        if expert is None:
            await session.rollback()
            raise GovernanceError("EXPERT_NOT_FOUND", f"Không có expert_profile {uploaded_by_expert_id}")

        document_id = uuid.uuid4()
        try:
            await session.execute(
                sa.insert(ranking_evidence_documents).values(
                    id=document_id,
                    proposal_id=proposal_id,
                    uploaded_by_expert_id=uploaded_by_expert_id,
                    original_filename=original_filename.strip(),
                    mime_type=mime_type,
                    object_storage_key=object_storage_key,
                    sha256_checksum=sha256_checksum,
                    file_size_bytes=file_size_bytes,
                    extraction_status="not_requested",
                    created_at=_now(),
                )
            )
        except sa.exc.IntegrityError as exc:
            await session.rollback()
            raise GovernanceError(
                "DUPLICATE_OBJECT_STORAGE_KEY", f"object_storage_key '{object_storage_key}' đã tồn tại"
            ) from exc

        # `ck_rcae_entity_reference` (0034) requires `ranking_config_id` OR
        # `proposal_id` — a standalone evidence upload (`proposal_id=None`,
        # allowed by `ranking_evidence_documents`' own nullable FK) has
        # neither, so it has nothing valid to audit against. Skipping here is
        # not silently dropping an audit trail: there is no proposal decision
        # yet for this row to be evidence of.
        if proposal_id is not None:
            identity = (await get_expert_profile(uploaded_by_expert_id))["identity_subject"]
            await _record_audit_event(
                session,
                ranking_config_id=None,
                proposal_id=proposal_id,
                actor_expert_id=uploaded_by_expert_id,
                actor_identity_subject=identity,
                event_type="submitted",
                before_status=None,
                after_status=None,
                after_state={"document_id": str(document_id), "original_filename": original_filename},
            )
        await session.commit()
        row = (
            await session.execute(sa.select(ranking_evidence_documents).where(ranking_evidence_documents.c.id == document_id))
        ).mappings().first()
        await session.rollback()

    log.info("governance.evidence.registered", document_id=str(document_id), proposal_id=str(proposal_id))
    return dict(row)


async def link_evidence_to_justification(*, document_id: uuid.UUID, feature_justification_id: uuid.UUID) -> None:
    """INSERT vào bảng nối — append-only, không có khái niệm "sửa" một liên
    kết, chỉ có thêm hoặc để nguyên.

    **Evidence lock (PR-2, §3.2, áp dụng cho CẢ HAI mode — một khoảng trống
    đã có từ trước, không riêng value-mode):** một khi đề xuất cha rời khỏi
    `draft`, không thể liên kết thêm evidence — nếu không, CEO/reviewer có
    thể đã duyệt trên một bộ evidence khác với bộ evidence độc giả thấy sau
    này. Sửa evidence sau khi nộp cần một justification/đề xuất MỚI, đúng
    kỷ luật `_JUSTIFICATION_EDITABLE_STATUSES` đã có."""
    async with get_session_factory()() as session:
        for table, col, value, code in (
            (ranking_evidence_documents, ranking_evidence_documents.c.id, document_id, "DOCUMENT_NOT_FOUND"),
            (
                ranking_feature_justifications,
                ranking_feature_justifications.c.id,
                feature_justification_id,
                "JUSTIFICATION_NOT_FOUND",
            ),
        ):
            found = (await session.execute(sa.select(col).where(col == value))).first()
            if found is None:
                await session.rollback()
                raise GovernanceError(code, f"Không có hàng {value} trong {table.name}")

        proposal_status = await session.scalar(
            sa.select(ranking_weight_proposals.c.status)
            .select_from(
                ranking_feature_justifications.join(
                    ranking_weight_proposals,
                    ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
                )
            )
            .where(ranking_feature_justifications.c.id == feature_justification_id)
        )
        if proposal_status not in _JUSTIFICATION_EDITABLE_STATUSES:
            await session.rollback()
            raise GovernanceError(
                "EVIDENCE_LOCKED",
                f"Đề xuất đang ở trạng thái '{proposal_status}' — chỉ liên kết evidence được khi còn 'draft'",
            )
        try:
            await session.execute(
                sa.insert(ranking_evidence_document_features).values(
                    document_id=document_id, feature_justification_id=feature_justification_id
                )
            )
        except sa.exc.IntegrityError:
            await session.rollback()
            return  # liên kết đã tồn tại — idempotent, không phải lỗi
        await session.commit()

    log.info(
        "governance.evidence.linked", document_id=str(document_id), feature_justification_id=str(feature_justification_id)
    )


async def list_documents_for_justification(feature_justification_id: uuid.UUID) -> list[dict]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_evidence_documents)
                .join(
                    ranking_evidence_document_features,
                    ranking_evidence_document_features.c.document_id == ranking_evidence_documents.c.id,
                )
                .where(ranking_evidence_document_features.c.feature_justification_id == feature_justification_id)
            )
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


# --- Duyệt (review) ---------------------------------------------------------------


async def submit_review(
    *,
    proposal_id: uuid.UUID,
    reviewer_expert_id: uuid.UUID | None = None,
    decision: str,
    comment: str,
    reviewer_subject: str | None = None,
    reviewer_is_ceo: bool = False,
) -> dict:
    """Một reviewer, một quyết định cho một đề xuất
    (`uq_ranking_proposal_review_reviewer`, 0034 — gọi lại là IntegrityError,
    không phải "sửa" quyết định cũ, bảng append-only).

    Chuyển trạng thái đề xuất:
      - `approved` → `approved` (weight-mode đòi `proposed_config_id` đã gắn —
        xem `set_proposed_config`; value-mode KHÔNG đòi, không liên quan
        `ranking_configs`)
      - `rejected` → `rejected` (chốt)
      - `request_changes` → `under_review` (không đổi nếu đã `under_review`,
        chuyên gia sửa justification rồi phải nộp lại — v1 CHƯA có route
        "resubmit", ghi ở D19 như một câu hỏi mở)

    **Value-mode (PR-2, D38), hai chốt MỚI, chỉ áp cho nhánh này:**
    `reviewer_expert_id` do CALLER truyền vào bị BỎ QUA hoàn toàn — danh tính
    reviewer luôn được suy lại từ `reviewer_subject` (đã xác thực OIDC server-
    side, không phải request body); và `reviewer_is_ceo` phải `True`, nếu
    không bị từ chối `CEO_APPROVAL_REQUIRED`. Tự duyệt đề xuất của chính mình
    bị từ chối `SELF_APPROVAL_FORBIDDEN`. Weight-mode: không đổi — vẫn dùng
    thẳng `reviewer_expert_id` do caller truyền, không chốt CEO, không chốt
    tự-duyệt (khoảng trống đã có từ trước, D18, không được PR này đóng lại)."""
    if decision not in REVIEW_DECISIONS:
        raise GovernanceError("DECISION_INVALID", f"decision phải thuộc {REVIEW_DECISIONS}")
    if not comment.strip():
        raise GovernanceError("COMMENT_REQUIRED", "comment không được rỗng — quyết định không kèm lý do không dùng được")

    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, _REVIEWABLE_STATUSES)
        is_value_mode = proposal["assertion_kind"] == "value"

        if is_value_mode:
            if not reviewer_subject:
                await session.rollback()
                raise GovernanceError(
                    "IDENTITY_REQUIRED", "Duyệt value-mode assertion cần danh tính OIDC đã xác thực (subject)"
                )
            if not reviewer_is_ceo:
                await session.rollback()
                raise GovernanceError(
                    "CEO_APPROVAL_REQUIRED", "Chỉ CEO (xác thực OIDC, vai trò thật CRM.CEO) mới được duyệt value-mode"
                )
            resolved = await get_or_create_expert_profile(identity_subject=reviewer_subject)
            reviewer_expert_id = uuid.UUID(str(resolved["id"]))
            if reviewer_expert_id == proposal["created_by_expert_id"]:
                await session.rollback()
                raise GovernanceError(
                    "SELF_APPROVAL_FORBIDDEN", "Không thể tự duyệt value assertion của chính mình"
                )
        else:
            if reviewer_expert_id is None:
                await session.rollback()
                raise GovernanceError("REVIEWER_REQUIRED", "reviewer_expert_id không được rỗng")
            if decision == "approved" and proposal["proposed_config_id"] is None:
                await session.rollback()
                raise GovernanceError(
                    "PROPOSED_CONFIG_MISSING",
                    "Không thể duyệt: đề xuất chưa gắn proposed_config_id — gọi set_proposed_config trước",
                )

        review_id = uuid.uuid4()
        try:
            await session.execute(
                sa.insert(ranking_proposal_reviews).values(
                    id=review_id,
                    proposal_id=proposal_id,
                    reviewer_expert_id=reviewer_expert_id,
                    decision=decision,
                    comment=comment.strip(),
                    decided_at=_now(),
                    reviewer_subject=reviewer_subject if is_value_mode else None,
                    reviewer_is_ceo=reviewer_is_ceo if is_value_mode else None,
                )
            )
        except sa.exc.IntegrityError as exc:
            await session.rollback()
            raise GovernanceError(
                "ALREADY_REVIEWED", "Chuyên gia này đã duyệt đề xuất này rồi — một người, một quyết định"
            ) from exc

        after_status = {
            "approved": "approved",
            "rejected": "rejected",
            "request_changes": "under_review",
        }[decision]
        now = _now()
        update_values: dict[str, Any] = {"status": after_status, "updated_at": now}
        if after_status == "approved":
            update_values["approved_at"] = now
        await session.execute(
            sa.update(ranking_weight_proposals).where(ranking_weight_proposals.c.id == proposal_id).values(**update_values)
        )

        reviewer = await get_expert_profile(reviewer_expert_id)
        await _record_audit_event(
            session,
            ranking_config_id=proposal["proposed_config_id"],
            proposal_id=proposal_id,
            actor_expert_id=reviewer_expert_id,
            actor_identity_subject=reviewer["identity_subject"],
            event_type="reviewed" if decision == "request_changes" else decision,
            before_status=proposal["status"],
            after_status=after_status,
            after_state={"decision": decision},
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.proposal.reviewed", proposal_id=str(proposal_id), decision=decision)
    return row


async def list_reviews(proposal_id: uuid.UUID) -> list[dict]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_proposal_reviews)
                .where(ranking_proposal_reviews.c.proposal_id == proposal_id)
                .order_by(ranking_proposal_reviews.c.decided_at)
            )
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


# --- Gắn config và công bố ---------------------------------------------------------


async def set_proposed_config(*, proposal_id: uuid.UUID, proposed_config_id: uuid.UUID, actor_expert_id: uuid.UUID) -> dict:
    """Gắn một `ranking_configs` DRAFT (đã tạo qua `POST /ranking/configs`
    hiện có) vào đề xuất, để reviewer duyệt đúng bộ trọng số sẽ được publish.

    KHÔNG tạo config — chỉ tham chiếu một cái đã tồn tại. Người vận hành vẫn
    phải tự soạn draft đó qua `src/services/ranking_config.py::create_draft`
    trước khi gọi hàm này, đúng ba-hành-động-tách-bạch của `ranking_v2_ahp.md`
    §3, mở rộng cho luồng có chuyên gia.
    """
    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, ("draft", "submitted", "under_review"))
        config = (
            await session.execute(
                sa.select(ranking_configs.c.id, ranking_configs.c.status).where(
                    ranking_configs.c.id == proposed_config_id
                )
            )
        ).mappings().first()
        if config is None:
            await session.rollback()
            raise GovernanceError("PROPOSED_CONFIG_NOT_FOUND", f"Không có ranking_configs {proposed_config_id}")
        if config["status"] not in ("draft", "published"):
            await session.rollback()
            raise GovernanceError(
                "PROPOSED_CONFIG_STATUS_INVALID",
                f"ranking_configs {proposed_config_id} đang ở trạng thái '{config['status']}', cần 'draft'",
            )
        if proposed_config_id == proposal["base_config_id"]:
            await session.rollback()
            raise GovernanceError(
                "PROPOSED_CONFIG_SAME_AS_BASE", "proposed_config_id phải khác base_config_id (ck_rwp_distinct_configs)"
            )

        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal_id)
            .values(proposed_config_id=proposed_config_id, updated_at=_now())
        )
        expert = await get_expert_profile(actor_expert_id)
        await _record_audit_event(
            session,
            ranking_config_id=proposed_config_id,
            proposal_id=proposal_id,
            actor_expert_id=actor_expert_id,
            actor_identity_subject=expert["identity_subject"],
            event_type="submitted",
            before_status=proposal["status"],
            after_status=proposal["status"],
            after_state={"proposed_config_id": str(proposed_config_id)},
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.proposal.config_set", proposal_id=str(proposal_id), proposed_config_id=str(proposed_config_id))
    return row


async def mark_published(*, proposal_id: uuid.UUID, actor_expert_id: uuid.UUID) -> dict:
    """`approved` → `published`. SELECT-only against `ranking_configs` — never
    writes it. The actual publish (`ranking_configs.status → 'published'`,
    archiving the previous one, queuing recompute) must have ALREADY happened
    via the existing `POST /ranking/configs/{v}/publish`
    (`src/services/ranking_config.py::publish`, its own single-writer table).
    This function only confirms that happened and records the proposal's own
    `published_at` — it can never be the thing that makes a config live.
    """
    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, ("approved",))
        if proposal["assertion_kind"] == "value":
            # PR-3: 'published' now means "re-verified ready for feature
            # consumption", NOT "already copied into ranking_feature_values".
            # It CANNOT mean the latter — materializing into a specific
            # ranking_feature_snapshots row requires a ranking_run_id/cutoff
            # that only exists once an actual ranking run needs Project data
            # (`build_project_feature_snapshot_for_run()`,
            # `src/ranking/service.py`); no such context exists at proposal-
            # publish time. Every justification is re-verified here (defense
            # in depth, independent of whatever the review-time check already
            # confirmed) so an already-broken assertion (e.g. its evidence
            # was somehow orphaned) cannot reach 'published' even though no
            # feature_values row is written by this call.
            justifications = (
                await session.execute(
                    sa.select(ranking_feature_justifications.c.id).where(
                        ranking_feature_justifications.c.proposal_id == proposal_id,
                        ranking_feature_justifications.c.assertion_kind == "value",
                    )
                )
            ).scalars().all()
            for justification_id in justifications:
                await validate_value_assertion_for_materialization(justification_id)

            now = _now()
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(ranking_weight_proposals.c.id == proposal_id)
                .values(status="published", published_at=now, updated_at=now)
            )
            expert = await get_expert_profile(actor_expert_id)
            await _record_audit_event(
                session,
                ranking_config_id=None,
                proposal_id=proposal_id,
                actor_expert_id=actor_expert_id,
                actor_identity_subject=expert["identity_subject"],
                event_type="published",
                before_status="approved",
                after_status="published",
            )
            await session.commit()
            row = await _fetch_proposal(session, proposal_id)
            await session.rollback()
            log.info("governance.proposal.published", proposal_id=str(proposal_id), assertion_kind="value")
            return row
        config = (
            await session.execute(
                sa.select(ranking_configs.c.status).where(ranking_configs.c.id == proposal["proposed_config_id"])
            )
        ).mappings().first()
        if config is None or config["status"] != "published":
            await session.rollback()
            raise GovernanceError(
                "CONFIG_NOT_PUBLISHED",
                "proposed_config_id chưa ở trạng thái 'published' trong ranking_configs — "
                "gọi POST /ranking/configs/{version}/publish trước",
            )

        now = _now()
        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal_id)
            .values(status="published", published_at=now, updated_at=now)
        )
        expert = await get_expert_profile(actor_expert_id)
        await _record_audit_event(
            session,
            ranking_config_id=proposal["proposed_config_id"],
            proposal_id=proposal_id,
            actor_expert_id=actor_expert_id,
            actor_identity_subject=expert["identity_subject"],
            event_type="published",
            before_status="approved",
            after_status="published",
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.proposal.published", proposal_id=str(proposal_id))
    return row


# --- PR-3 readiness guard (read-only, no side effects) -----------------------


async def validate_value_assertion_for_materialization(feature_justification_id: uuid.UUID) -> dict:
    """Re-verifies a value-mode assertion is genuinely safe to materialize —
    called by PR-3's `materialize_published_feature_value()` (not implemented
    here), never by anything in this PR. Pure `SELECT`; raises
    `GovernanceError` on any failing condition; performs **no** insert/update
    against `ranking_feature_values`/`ranking_feature_snapshots`/
    `ranking_feature_lineage`/`ranking_scores`/any ranking table — this
    function's only side effect, on success, is returning a plain dict.

    Re-checks, independently of whatever the route-level gate already did at
    review time (defense in depth, §3.3): value mode; an `approved` review
    exists; that review's reviewer was a verified CEO *at decision time*
    (`ranking_proposal_reviews.reviewer_is_ceo`, stored server-side, never a
    live Keycloak call — this session already reasoned through why a stored
    boolean is safer for replay than a live IDP re-check, see the PR-2 report);
    reviewer != author; at least one evidence document is linked; and the
    value/scope/citation shape is still valid.
    """
    async with get_session_factory()() as session:
        justification = (
            await session.execute(
                sa.select(ranking_feature_justifications).where(
                    ranking_feature_justifications.c.id == feature_justification_id
                )
            )
        ).mappings().first()
        if justification is None:
            await session.rollback()
            raise GovernanceError(
                "JUSTIFICATION_NOT_FOUND", f"Không có ranking_feature_justifications {feature_justification_id}"
            )
        if justification["assertion_kind"] != "value":
            await session.rollback()
            raise GovernanceError("NOT_VALUE_MODE", "assertion_kind phải là 'value'")

        proposal = await _fetch_proposal(session, justification["proposal_id"])
        # PR-3: this guard is now called from two points in the lifecycle —
        # `mark_published()` above (proposal still 'approved', about to become
        # 'published') and PR-3's `materialize_published_feature_value()` at
        # actual scoring time (proposal already 'published', the only state a
        # value-mode assertion can be selected from per §3.2's snapshot-
        # selection rule). Both are legitimate re-verification points; neither
        # is "materializing an unapproved assertion".
        if proposal is None or proposal["status"] not in ("approved", "published"):
            await session.rollback()
            raise GovernanceError(
                "NOT_APPROVED", f"Đề xuất {justification['proposal_id']} chưa ở trạng thái 'approved'/'published'"
            )

        review = (
            await session.execute(
                sa.select(ranking_proposal_reviews)
                .where(
                    ranking_proposal_reviews.c.proposal_id == proposal["id"],
                    ranking_proposal_reviews.c.decision == "approved",
                )
                .order_by(ranking_proposal_reviews.c.decided_at.desc())
            )
        ).mappings().first()
        if review is None:
            await session.rollback()
            raise GovernanceError("NO_APPROVED_REVIEW", "Không tìm thấy review 'approved' cho đề xuất này")
        if not review["reviewer_is_ceo"]:
            await session.rollback()
            raise GovernanceError(
                "CEO_APPROVAL_REQUIRED", "Review 'approved' không phải do CEO đã xác thực OIDC thực hiện"
            )
        if review["reviewer_expert_id"] == proposal["created_by_expert_id"]:
            await session.rollback()
            raise GovernanceError("SELF_APPROVAL_FORBIDDEN", "Reviewer trùng với tác giả assertion")

        evidence_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_evidence_document_features)
            .where(ranking_evidence_document_features.c.feature_justification_id == feature_justification_id)
        )
        if not evidence_count:
            await session.rollback()
            raise GovernanceError("EVIDENCE_MISSING", "Assertion đã duyệt nhưng không còn evidence nào liên kết")

        feature = (
            await session.execute(
                sa.select(ranking_feature_definitions).where(
                    ranking_feature_definitions.c.id == justification["feature_definition_id"]
                )
            )
        ).mappings().first()
        if feature is None:
            await session.rollback()
            raise GovernanceError("FEATURE_DEFINITION_NOT_FOUND", "feature_definition không còn tồn tại")

        _check_grain_scope_compatibility(feature["grain"], proposal["scope_type"])
        if justification["normalized_numeric"] is not None and not (0 <= justification["normalized_numeric"] <= 1):
            await session.rollback()
            raise GovernanceError("NORMALIZED_VALUE_RANGE", "normalized_numeric phải trong [0, 1]")
        if proposal["scope_type"] == "market":
            _validate_market_submission(justification, feature)

        await session.rollback()

    log.info(
        "governance.value_assertion.materialization_validated",
        feature_justification_id=str(feature_justification_id),
        proposal_id=str(proposal["id"]),
    )
    return {
        "justification": dict(justification),
        "proposal": dict(proposal),
        "approved_review": dict(review),
        "feature_definition": dict(feature),
    }
