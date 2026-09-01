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

import copy
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import HierarchicalAHPWeightsIn
from src.models.tables import (
    areas,
    expert_profiles,
    projects,
    ranking_config_audit_events,
    ranking_configs,
    ranking_evidence_document_chunks,
    ranking_evidence_document_features,
    ranking_evidence_document_lifecycle_events,
    ranking_evidence_documents,
    ranking_feature_definitions,
    ranking_feature_justifications,
    ranking_feature_rubric_bands,
    ranking_feature_rubrics,
    ranking_proposal_evidence_links,
    ranking_proposal_reviews,
    ranking_runs,
    ranking_weight_proposals,
)
from src.ranking.ahp import Judgment
from src.ranking.hierarchical_ahp import (
    HierarchicalAHPError,
    assemble_hierarchical_weights_block,
    compute_hierarchical_ahp,
)
from src.services import evidence_extraction, rationale_retrieval
from src.services.ranking_config import (
    HierarchicalConfigError,
    create_draft_in_session,
    publish_in_session,
    validate_hierarchical_weights,
)

log = get_logger("src.services.governance")

ASSERTION_KINDS = ("weight", "value")
VALUE_SCOPE_TYPES = ("project", "area", "market")

# 0049: classifies a proposal independently of `assertion_kind`.  Every
# historical row (either assertion_kind) is 'qualitative_analysis' — the
# migration's own backfill, not inferred here. Only 'ahp_ranking_proposal'
# may carry `proposed_hierarchy_snapshot`/`ahp_application_status`/
# `applied_ranking_run_id`, and only its CEO approval may ever publish a
# config or queue a ranking run — 'qualitative_analysis' approval never does
# either, regardless of its own `assertion_kind`.
PROPOSAL_TYPES = ("qualitative_analysis", "ahp_ranking_proposal")
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

# 0046: the six recommended-MVP qualitative features that MUST be graded
# against a real, versioned rubric band — never a freely-typed number. Any
# OTHER registered numeric feature (e.g. `market_liquidity`) keeps the
# pre-existing free-form `normalized_numeric` path unchanged; a rubric is
# optional (allowed, not required) for those. This is the single source of
# truth for "which keys require a rubric" — checked in `upsert_justification()`.
RUBRIC_REQUIRED_FEATURE_KEYS = frozenset(
    {
        "market_interest_rate",
        "market_demand",
        "market_credit_policy",
        "area_accessibility",
        "area_current_infrastructure",
        "area_future_infrastructure",
        "project_design_score",
    }
)

# Canonical band values every rubric must use — fixed, not per-rubric
# configurable, so every qualitative assertion in the system reads on the
# same 5-point scale regardless of feature.
RUBRIC_BAND_VALUES = (Decimal("0.00"), Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))

PROPOSAL_STATUSES = (
    "draft",
    "submitted",
    "under_review",
    "approved",
    "rejected",
    "withdrawn",
    "published",
)
REVIEW_DECISIONS = ("approved", "rejected")
EXPECTED_EFFECTS = ("increase", "decrease", "neutral", "context_dependent")
CONFIDENCE_LEVELS = ("low", "medium", "high")
EVIDENCE_MIME_TYPES = ("application/pdf", "text/plain", "text/markdown")

# Trạng thái mà một justification còn SỬA được. Sau khi đề xuất rời `draft`,
# bằng chứng đã nộp phải đứng yên — sửa `rationale` sau khi reviewer đã đọc
# biến vết duyệt thành duyệt một thứ khác với thứ đang publish.
_JUSTIFICATION_EDITABLE_STATUSES = ("draft",)
_REVIEWABLE_STATUSES = ("submitted",)


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


def _validate_categorical_vocabulary(justification: dict, feature: dict) -> None:
    """PR-6: any categorical value-mode assertion whose feature definition
    declares `definition_metadata.allowed_categorical_values` (currently only
    `project_legal_status`, D27/D40's minimal HIGH_RISK/NOT_HIGH_RISK/UNKNOWN
    gate vocabulary) must use exactly one of those values — the same
    metadata-driven-policy pattern `_validate_market_submission()`'s shelf-life
    check already established, not a table-wide `categorical_value` CHECK
    (see `0042_legal_assertion_gate.py`'s docstring for why a blanket CHECK
    would misapply one feature's vocabulary to every other categorical
    feature). A feature with no `allowed_categorical_values` entry is
    unrestricted — this is opt-in per feature, not a new global rule."""
    if feature["value_type"] != "categorical":
        return
    allowed = (feature["definition_metadata"] or {}).get("allowed_categorical_values")
    if not allowed:
        return
    value = justification.get("categorical_value")
    if value not in allowed:
        raise GovernanceError(
            "CATEGORICAL_VALUE_NOT_ALLOWED",
            f"categorical_value '{value}' không thuộc vocabulary cho phép {allowed} của '{feature['feature_key']}'",
        )


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


async def list_audit_events(
    *, proposal_id: uuid.UUID | None = None, ranking_config_id: uuid.UUID | None = None
) -> list[dict]:
    """Read-only history for the "Lịch sử công bố" (publish history) view —
    exactly one of the two filters must be given by the caller (enforced in
    `src/api/governance.py`), same discipline as `list_documents()`."""
    query = sa.select(ranking_config_audit_events)
    if proposal_id is not None:
        query = query.where(ranking_config_audit_events.c.proposal_id == proposal_id)
    elif ranking_config_id is not None:
        query = query.where(ranking_config_audit_events.c.ranking_config_id == ranking_config_id)
    else:
        raise GovernanceError("SCOPE_REQUIRED", "list_audit_events cần proposal_id hoặc ranking_config_id")

    async with get_session_factory()() as session:
        rows = (
            (await session.execute(query.order_by(ranking_config_audit_events.c.created_at.desc()))).mappings().all()
        )
        await session.rollback()
    return [dict(row) for row in rows]


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


async def find_expert_profile_id(*, identity_subject: str) -> uuid.UUID | None:
    """Read-only subject lookup used by scoped governance list/read paths."""
    async with get_session_factory()() as session:
        expert_id = await session.scalar(
            sa.select(expert_profiles.c.id).where(expert_profiles.c.identity_subject == identity_subject.strip())
        )
        await session.rollback()
    return uuid.UUID(str(expert_id)) if expert_id is not None else None


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
    proposal_type: str = "qualitative_analysis",
) -> dict:
    """Tạo đề xuất ở trạng thái `draft`. Chưa ảnh hưởng `ranking_configs` nào.

    `assertion_kind='weight'` (mặc định) chạy ĐÚNG con đường đã có từ trước —
    `scope_type`/`area_id` bị BỎ QUA, luôn ép `'project'`/`None` như hôm nay,
    `base_config_id` bắt buộc như hôm nay. `assertion_kind='value'` (PR-2,
    D37/D38) là nhánh MỚI: không có `ranking_configs` nào liên quan
    (`base_config_id` phải là `None`), `scope_type` có thể là
    `project`/`area`/`market`, và hình dạng `area_id` phải khớp `scope_type`
    (`ck_rwp_scope_shape`, 0038).

    `proposal_type='ahp_ranking_proposal'` (0049) is orthogonal to
    `assertion_kind` (always forces `'weight'` internally) and NEVER accepts
    a caller-supplied `base_config_id` — the currently published config is
    resolved here, server-side, so a client can never pin a stale/wrong
    version as the proposal's baseline."""
    if proposal_type not in PROPOSAL_TYPES:
        raise GovernanceError("PROPOSAL_TYPE_INVALID", f"proposal_type phải thuộc {PROPOSAL_TYPES}")
    if proposal_type == "ahp_ranking_proposal":
        if base_config_id is not None:
            raise GovernanceError(
                "BASE_CONFIG_NOT_ALLOWED",
                "proposal_type='ahp_ranking_proposal' tự suy base_config_id từ config đang published",
            )
        assertion_kind = "weight"

    if assertion_kind not in ASSERTION_KINDS:
        raise GovernanceError("ASSERTION_KIND_INVALID", f"assertion_kind phải thuộc {ASSERTION_KINDS}")

    if assertion_kind == "weight":
        if proposal_type != "ahp_ranking_proposal" and base_config_id is None:
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
        if proposal_type == "ahp_ranking_proposal":
            base_config_id = await session.scalar(
                sa.select(ranking_configs.c.id).where(ranking_configs.c.status == "published")
            )
            if base_config_id is None:
                await session.rollback()
                raise GovernanceError(
                    "NO_PUBLISHED_CONFIG", "Không có ranking_configs nào đang published để làm nền cho đề xuất AHP"
                )
        elif base_config_id is not None:
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
                proposal_type=proposal_type,
                proposed_hierarchy_snapshot=None,
                ahp_application_status=None,
                applied_ranking_run_id=None,
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


async def get_active_ranking_config() -> dict | None:
    """Read-only — the CEO review package's "current active config" summary
    (mission Part C). `None` only in an unconfigured/pre-seed environment."""
    async with get_session_factory()() as session:
        row = (
            await session.execute(sa.select(ranking_configs).where(ranking_configs.c.status == "published"))
        ).mappings().first()
        await session.rollback()
    return dict(row) if row is not None else None


# --- Advisor AHP ranking proposal (0049) --------------------------------------
#
# `proposal_type='ahp_ranking_proposal'` is orthogonal to the pre-existing
# `assertion_kind`. Everything below is ADDITIVE: no existing qualitative
# (`assertion_kind='value'`) or legacy admin weight-mode flow is touched.


AHP_DRAFT_MODES = ("direct", "pairwise")
AHP_RATIONALE_MAX_LENGTH = 500


async def _reject_unregistered_criteria(session, block: dict) -> None:
    """Rule 11: a criterion may enter an Advisor's hierarchy only if it is a
    real, ACTIVE row in the canonical `ranking_feature_definitions` registry
    for that exact grain — never a hardcoded/legacy key list. This is what
    structurally keeps `expert_location_score`/`expert_infrastructure_score`/
    `expert_financing_score` out: they are not registered for `project`
    (confirmed live, only `project_legal_status` — the gate — is), so any
    attempt to use them fails here, not because their names are special-cased."""
    for grain in ("market", "project", "area"):
        keys = set(block.get(grain) or {})
        if not keys:
            continue
        registered = set(
            (
                await session.execute(
                    sa.select(ranking_feature_definitions.c.feature_key).where(
                        ranking_feature_definitions.c.feature_key.in_(keys),
                        ranking_feature_definitions.c.grain == grain,
                        ranking_feature_definitions.c.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = keys - registered
        if missing:
            raise GovernanceError(
                "UNREGISTERED_CRITERION",
                f"Tiêu chí {sorted(missing)} không phải đặc trưng đã đăng ký (grain='{grain}', active)",
            )


def _pairwise_judgments(items) -> list[Judgment]:
    return [Judgment(a=j.a, b=j.b, value=Decimal(str(j.value))) for j in items]


def _normalize_ahp_rationales(block: dict) -> dict:
    """Validate optional authored rationales without changing weight semantics.

    Empty strings are treated as absent so legacy/proposal payloads without a
    rationale remain byte-for-byte compatible in all scoring paths.
    """
    normalized = copy.deepcopy(block)
    for grain in ("market", "project", "area"):
        for criterion_key, spec in (normalized.get(grain) or {}).items():
            if not isinstance(spec, dict) or "rationale" not in spec:
                continue
            rationale = spec["rationale"]
            if rationale is None:
                spec.pop("rationale", None)
                continue
            if not isinstance(rationale, str):
                raise GovernanceError(
                    "AHP_RATIONALE_INVALID",
                    f"Giải thích cho {grain}.{criterion_key} phải là chuỗi văn bản",
                )
            rationale = rationale.strip()
            if len(rationale) > AHP_RATIONALE_MAX_LENGTH:
                raise GovernanceError(
                    "AHP_RATIONALE_TOO_LONG",
                    f"Giải thích cho {grain}.{criterion_key} không được quá {AHP_RATIONALE_MAX_LENGTH} ký tự",
                )
            if rationale:
                spec["rationale"] = rationale
            else:
                spec.pop("rationale", None)
    return normalized


async def save_ahp_proposal_draft(
    *,
    proposal_id: uuid.UUID,
    actor_expert_id: uuid.UUID,
    mode: str,
    direct_hierarchical_weights: dict | None = None,
    pairwise_input: HierarchicalAHPWeightsIn | None = None,
) -> dict:
    """Advisor-only (route-gated by `require_advisor_analysis_ahp_authoring`)
    draft save — reachable ONLY while the proposal is `draft` and only by its
    own owner; re-callable any number of times before submit (each call fully
    re-validates and OVERWRITES the prior draft payload — this is the
    "local slider changes do not save automatically" contract's server side:
    nothing here is persisted until this explicit action is called, and
    nothing computed here ever touches `ranking_configs`/scoring)."""
    if mode not in AHP_DRAFT_MODES:
        raise GovernanceError("AHP_MODE_INVALID", f"mode phải thuộc {AHP_DRAFT_MODES}")

    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, ("draft",))
        if proposal["proposal_type"] != "ahp_ranking_proposal":
            await session.rollback()
            raise GovernanceError(
                "PROPOSAL_TYPE_INVALID", "Chỉ đề xuất proposal_type='ahp_ranking_proposal' mới có bản nháp hierarchy"
            )
        if proposal["created_by_expert_id"] != actor_expert_id:
            await session.rollback()
            raise GovernanceError("PROPOSAL_OWNER_REQUIRED", "Chỉ người tạo mới được sửa bản nháp này")

        levels_out: dict | None = None
        if mode == "direct":
            if not direct_hierarchical_weights:
                await session.rollback()
                raise GovernanceError("HIERARCHY_REQUIRED", "mode='direct' cần hierarchical_weights")
            block = direct_hierarchical_weights
        else:
            if pairwise_input is None:
                await session.rollback()
                raise GovernanceError("HIERARCHY_REQUIRED", "mode='pairwise' cần judgments cho cả 4 level")
            try:
                hier = compute_hierarchical_ahp(
                    grain_judgments=_pairwise_judgments(pairwise_input.grain_judgments),
                    market_judgments=_pairwise_judgments(pairwise_input.market.judgments),
                    project_judgments=_pairwise_judgments(pairwise_input.project.judgments),
                    area_judgments=_pairwise_judgments(pairwise_input.area.judgments),
                )
            except HierarchicalAHPError as exc:
                await session.rollback()
                raise GovernanceError(exc.code, exc.message) from exc
            levels_out = {
                level.level: {
                    "raw_weights": {k: str(v) for k, v in level.result.weights.items()},
                    "lambda_max": f"{level.result.lambda_max:.6f}",
                    "consistency_index": f"{level.result.consistency_index:.6f}",
                    "consistency_ratio": f"{level.result.consistency_ratio:.6f}",
                    "threshold": str(level.result.threshold),
                    "consistent": level.result.consistent,
                }
                for level in hier.levels.values()
            }
            if not hier.all_consistent:
                await session.rollback()
                raise GovernanceError(
                    "HIERARCHICAL_CR_FAILED", f"CR không đạt ngưỡng ở level: {hier.failed_levels}"
                )
            block = assemble_hierarchical_weights_block(
                hier,
                grain_missing_value_policies=pairwise_input.grain_missing_value_policies,
                market_specs=pairwise_input.market.feature_specs,
                project_specs=pairwise_input.project.feature_specs,
                area_specs=pairwise_input.area.feature_specs,
            )

        block = _normalize_ahp_rationales(block)
        await _reject_unregistered_criteria(session, block)
        try:
            validate_hierarchical_weights(block)
        except HierarchicalConfigError as exc:
            await session.rollback()
            raise GovernanceError(exc.code, exc.message) from exc

        selected_criteria = sorted(
            {key for grain in ("market", "project", "area") for key in (block.get(grain) or {})}
        )
        snapshot = {
            "mode": mode,
            "hierarchical_weights": block,
            "selected_criteria": selected_criteria,
            "levels": levels_out,
            "validated_at": _now().isoformat(),
            "frozen_at": None,
        }
        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal_id)
            .values(proposed_hierarchy_snapshot=snapshot, updated_at=_now())
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.ahp_proposal.draft_saved", proposal_id=str(proposal_id), mode=mode)
    return row


async def get_proposal(proposal_id: uuid.UUID) -> dict:
    async with get_session_factory()() as session:
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()
    if row is None:
        raise GovernanceError("PROPOSAL_NOT_FOUND", f"Không có ranking_weight_proposals {proposal_id}")
    return row


async def list_proposals(
    *, project_id: uuid.UUID | None = None, status: str | None = None,
    created_by_expert_id: uuid.UUID | None = None,
) -> list[dict]:
    stmt = sa.select(ranking_weight_proposals).order_by(ranking_weight_proposals.c.created_at.desc())
    if project_id is not None:
        stmt = stmt.where(ranking_weight_proposals.c.project_id == project_id)
    if status is not None:
        stmt = stmt.where(ranking_weight_proposals.c.status == status)
    if created_by_expert_id is not None:
        stmt = stmt.where(ranking_weight_proposals.c.created_by_expert_id == created_by_expert_id)
    async with get_session_factory()() as session:
        rows = (await session.execute(stmt)).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


async def list_submitted_proposals_for_project_scope(
    *,
    project_scope: frozenset[str] | str,
    reviewer_expert_id: uuid.UUID | None = None,
    limit: int = 25,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return only submitted proposals in the reviewer's server scope.

    The reviewer workspace must never receive drafts and must not depend on a
    client-side status filter.  ``project_scope`` comes from the authenticated
    principal, never from query parameters.
    """
    where = [ranking_weight_proposals.c.status == "submitted"]
    if reviewer_expert_id is not None:
        # Self-authored work is not merely hidden in the UI: it never enters
        # the reviewer query result set.
        where.append(ranking_weight_proposals.c.created_by_expert_id != reviewer_expert_id)
    stmt = (
        sa.select(ranking_weight_proposals)
        .select_from(ranking_weight_proposals.join(projects, ranking_weight_proposals.c.project_id == projects.c.id))
        .where(*where)
        .order_by(ranking_weight_proposals.c.submitted_at.asc(), ranking_weight_proposals.c.id.asc())
        .limit(limit)
        .offset(offset)
    )
    if project_scope != "ALL":
        stmt = stmt.where(projects.c.external_id.in_(project_scope))
    async with get_session_factory()() as session:
        rows = (await session.execute(stmt)).mappings().all()
        count_stmt = (
            sa.select(sa.func.count())
            .select_from(ranking_weight_proposals.join(projects, ranking_weight_proposals.c.project_id == projects.c.id))
            .where(*where)
        )
        if project_scope != "ALL":
            count_stmt = count_stmt.where(projects.c.external_id.in_(project_scope))
        total = int(await session.scalar(count_stmt) or 0)
        await session.rollback()
    return [dict(row) for row in rows], total


async def _review_documents_and_justifications(proposal: dict) -> tuple[list[dict], list[dict], bool]:
    """Read the evidence snapshot for a submitted proposal.

    This intentionally returns internal rows only to the API adapter.  The
    public reviewer DTO is built separately and never inherits document
    storage keys, expert identifiers, or project identifiers from these rows.
    """
    proposal_id = uuid.UUID(str(proposal["id"]))
    async with get_session_factory()() as session:
        justification_rows = (
            await session.execute(
                sa.select(
                    ranking_feature_justifications,
                    ranking_feature_definitions.c.name.label("feature_name"),
                    ranking_feature_definitions.c.feature_key.label("feature_key"),
                )
                .join(
                    ranking_feature_definitions,
                    ranking_feature_definitions.c.id == ranking_feature_justifications.c.feature_definition_id,
                )
                .where(ranking_feature_justifications.c.proposal_id == proposal_id)
                .order_by(ranking_feature_justifications.c.created_at)
            )
        ).mappings().all()
        justification_ids = [row["id"] for row in justification_rows]
        justification_document_ids = (
            sa.select(ranking_evidence_document_features.c.document_id)
            .where(ranking_evidence_document_features.c.feature_justification_id.in_(justification_ids))
            if justification_ids
            else sa.select(ranking_evidence_documents.c.id).where(sa.false())
        )
        proposal_document_ids = sa.select(ranking_proposal_evidence_links.c.document_id).where(
            ranking_proposal_evidence_links.c.proposal_id == proposal_id
        )
        document_rows = (
            await session.execute(
                sa.select(ranking_evidence_documents)
                .where(
                    sa.or_(
                        ranking_evidence_documents.c.proposal_id == proposal_id,
                        ranking_evidence_documents.c.id.in_(justification_document_ids),
                        ranking_evidence_documents.c.id.in_(proposal_document_ids),
                    ),
                    ranking_evidence_documents.c.project_id == proposal["project_id"],
                )
                .order_by(ranking_evidence_documents.c.created_at)
            )
        ).mappings().all()
        await session.rollback()

    documents = [dict(row) for row in document_rows]
    readiness = [await evidence_extraction.get_document_readiness(uuid.UUID(str(row["id"]))) for row in documents]
    # A proposal is ready only when it has linked evidence and every disclosed
    # document remains lifecycle-ready.  A stale/archived document cannot be
    # masked by one ready sibling before final review.
    evidence_ready = bool(documents) and all(item is not None and item.eligible for item in readiness)
    return [dict(row) for row in justification_rows], documents, evidence_ready


async def build_submitted_review_queue(
    *, project_scope: frozenset[str] | str, reviewer_expert_id: uuid.UUID | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Attach minimal persisted review provenance without loading drafts."""
    items: list[dict[str, Any]] = []
    proposals, total = await list_submitted_proposals_for_project_scope(
        project_scope=project_scope, reviewer_expert_id=reviewer_expert_id, limit=limit, offset=offset
    )
    for proposal in proposals:
        justifications, documents, evidence_ready = await _review_documents_and_justifications(proposal)
        items.append(
            {
                "proposal": proposal,
                "justifications": justifications,
                "evidence_documents": documents,
                "evidence_document_count": len(documents),
                "evidence_ready": evidence_ready,
            }
        )
    return items, total


async def get_submitted_review_detail(
    *, proposal_id: uuid.UUID, project_scope: frozenset[str] | str, reviewer_expert_id: uuid.UUID | None
) -> dict[str, Any] | None:
    """Return one safe-review candidate or ``None`` without revealing why.

    Scope, submitted status, and self-authorship are SQL predicates so direct
    identifier guessing cannot distinguish terminal/out-of-scope/self work.
    """
    conditions = [ranking_weight_proposals.c.id == proposal_id, ranking_weight_proposals.c.status == "submitted"]
    if reviewer_expert_id is not None:
        conditions.append(ranking_weight_proposals.c.created_by_expert_id != reviewer_expert_id)
    stmt = (
        sa.select(ranking_weight_proposals)
        .select_from(ranking_weight_proposals.join(projects, ranking_weight_proposals.c.project_id == projects.c.id))
        .where(*conditions)
    )
    if project_scope != "ALL":
        stmt = stmt.where(projects.c.external_id.in_(project_scope))
    async with get_session_factory()() as session:
        row = (await session.execute(stmt)).mappings().first()
        await session.rollback()
    if row is None:
        return None
    proposal = dict(row)
    justifications, documents, evidence_ready = await _review_documents_and_justifications(proposal)
    return {
        "proposal": proposal,
        "justifications": justifications,
        "evidence_documents": documents,
        "evidence_document_count": len(documents),
        "evidence_ready": evidence_ready,
    }


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


async def submit_proposal(
    *, proposal_id: uuid.UUID, actor_expert_id: uuid.UUID, enforce_owner: bool = False
) -> dict:
    """`draft` → `submitted`.

    Value-mode (PR-2): đòi ÍT NHẤT một justification (một value assertion
    không kèm lý do thì không có gì cho reviewer đọc) — và mỗi justification
    phải có ít nhất một evidence document đã liên kết, và (nếu
    `scope_type='market'`) citation + effective_at + expiry-trong-hạn
    (§24.5's 30/90-ngày).

    Weight-mode: KHÔNG đòi justification (per-feature rationale vẫn được hỗ
    trợ qua `upsert_justification` cho ai muốn dùng, nhưng không bắt buộc —
    thực tế sống động, phát hiện qua live E2E của chính lượt này: không
    `ranking_feature_definitions` nào tồn tại cho các đặc trưng PHẲNG/vận
    hành (`unit_available`, `unit_demand_norm`, `area_velocity_norm`,
    `area_conversion_norm`) — chúng chỉ là khoá JSON trong `ranking_configs.weights`,
    không phải hàng trong bảng đó — nên ép justification bắt buộc cho
    weight-mode sẽ khoá CHẶT cả luồng lại, không đề xuất phẳng nào nộp được).
    Thay vào đó, AHP proposal tự động liên kết TOÀN BỘ tài liệu cùng project
    đang lifecycle-ready trong chính transaction submit. Readiness luôn dùng
    resolver có extraction attempt thành công + chunk + embedding; cột
    `extraction_status` lúc đăng ký không được dùng như nguồn sự thật. Một PDF
    mới tải lên nhưng chưa trích xuất vẫn chỉ là file thô và không thể nộp."""
    async with get_session_factory()() as session:
        before = await _require_proposal_for_update(session, proposal_id, ("draft",))
        if enforce_owner and before["created_by_expert_id"] != actor_expert_id:
            await session.rollback()
            raise GovernanceError("PROPOSAL_OWNER_REQUIRED", "Chỉ người tạo mới được nộp đề xuất này")

        if before["proposal_type"] == "ahp_ranking_proposal":
            snapshot = before.get("proposed_hierarchy_snapshot")
            if not snapshot or not snapshot.get("hierarchical_weights"):
                await session.rollback()
                raise GovernanceError(
                    "AHP_HIERARCHY_REQUIRED", "Đề xuất AHP chưa có bản nháp hierarchy hợp lệ — lưu trước khi nộp"
                )
            # Defense-in-depth re-validation under lock, same discipline as
            # `_revalidate_submitted_proposal_for_review()` below — the
            # registry/config-validation contract could have changed between
            # the last draft save and this submit call.
            await _reject_unregistered_criteria(session, snapshot["hierarchical_weights"])
            try:
                validate_hierarchical_weights(snapshot["hierarchical_weights"])
            except HierarchicalConfigError as exc:
                await session.rollback()
                raise GovernanceError(exc.code, exc.message) from exc
            snapshot = {**snapshot, "frozen_at": _now().isoformat()}
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(ranking_weight_proposals.c.id == proposal_id)
                .values(proposed_hierarchy_snapshot=snapshot)
            )

            ready_document_ids = list(
                (
                    await session.scalars(
                        sa.select(ranking_evidence_documents.c.id).where(
                            ranking_evidence_documents.c.project_id == before["project_id"],
                            evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
                        )
                    )
                ).all()
            )
            if not ready_document_ids:
                await session.rollback()
                raise GovernanceError(
                    "EVIDENCE_REQUIRED",
                    "Dự án chưa có tài liệu bằng chứng sẵn sàng. Vui lòng upload bằng chứng "
                    "trong mục Báo cáo tư vấn chi tiết và chờ trích xuất hoàn tất.",
                )
            existing_document_ids = set(
                (
                    await session.scalars(
                        sa.select(ranking_proposal_evidence_links.c.document_id).where(
                            ranking_proposal_evidence_links.c.proposal_id == proposal_id,
                            ranking_proposal_evidence_links.c.document_id.in_(ready_document_ids),
                        )
                    )
                ).all()
            )
            new_document_ids = [document_id for document_id in ready_document_ids if document_id not in existing_document_ids]
            if new_document_ids:
                now = _now()
                await session.execute(
                    sa.insert(ranking_proposal_evidence_links),
                    [
                        {
                            "proposal_id": proposal_id,
                            "document_id": document_id,
                            "linked_by_expert_id": actor_expert_id,
                            "created_at": now,
                        }
                        for document_id in new_document_ids
                    ],
                )

        justifications = (
            await session.execute(
                sa.select(ranking_feature_justifications).where(
                    ranking_feature_justifications.c.proposal_id == proposal_id
                )
            )
        ).mappings().all()

        if before["assertion_kind"] == "value":
            if not justifications:
                await session.rollback()
                raise GovernanceError(
                    "NO_JUSTIFICATIONS",
                    "Đề xuất chưa có justification nào — thêm ít nhất một trước khi nộp",
                )
            for justification in justifications:
                linked_evidence_count = await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(ranking_evidence_document_features)
                    .where(ranking_evidence_document_features.c.feature_justification_id == justification["id"])
                )
                evidence_count = await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(
                        ranking_evidence_document_features.join(
                            ranking_evidence_documents,
                            ranking_evidence_documents.c.id == ranking_evidence_document_features.c.document_id,
                        )
                    )
                    .where(
                        ranking_evidence_document_features.c.feature_justification_id == justification["id"],
                        evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
                    )
                )
                if not evidence_count:
                    await session.rollback()
                    if linked_evidence_count:
                        raise GovernanceError(
                            "EVIDENCE_NOT_READY",
                            f"Value assertion {justification['id']} chỉ còn historical evidence không lifecycle-ready — "
                            "không thể nộp",
                        )
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
        else:
            proposal_evidence_document_ids = sa.select(ranking_proposal_evidence_links.c.document_id).where(
                ranking_proposal_evidence_links.c.proposal_id == proposal_id
            )
            direct_evidence_chunk_count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(
                    ranking_evidence_documents.join(
                        ranking_evidence_document_chunks,
                        ranking_evidence_document_chunks.c.document_id == ranking_evidence_documents.c.id,
                    )
                )
                .where(
                    sa.or_(
                        ranking_evidence_documents.c.proposal_id == proposal_id,
                        ranking_evidence_documents.c.id.in_(proposal_evidence_document_ids),
                    ),
                    evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
                )
            )
            justification_evidence_chunk_count = 0
            if justifications:
                justification_evidence_chunk_count = await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(
                        ranking_feature_justifications.join(
                            ranking_evidence_document_features,
                            ranking_evidence_document_features.c.feature_justification_id
                            == ranking_feature_justifications.c.id,
                        ).join(
                            ranking_evidence_documents,
                            ranking_evidence_documents.c.id == ranking_evidence_document_features.c.document_id,
                        ).join(
                            ranking_evidence_document_chunks,
                            ranking_evidence_document_chunks.c.document_id == ranking_evidence_documents.c.id,
                        )
                    )
                    .where(
                        ranking_feature_justifications.c.proposal_id == proposal_id,
                        evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
                    )
                )
            if not direct_evidence_chunk_count and not justification_evidence_chunk_count:
                await session.rollback()
                raise GovernanceError(
                    "EVIDENCE_REQUIRED",
                    "Dự án chưa có tài liệu bằng chứng sẵn sàng. Vui lòng upload bằng chứng "
                    "trong mục Báo cáo tư vấn chi tiết và chờ trích xuất hoàn tất.",
                )

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
        if before["proposal_type"] == "ahp_ranking_proposal":
            try:
                await rationale_retrieval.insert_rationale_chunks_in_session(
                    session,
                    proposal_id=proposal_id,
                    hierarchical_weights=snapshot["hierarchical_weights"],
                )
            except Exception as exc:  # no partial submitted proposal without its required retrieval projection
                await session.rollback()
                raise GovernanceError(
                    "AHP_RATIONALE_EMBEDDING_FAILED",
                    "Không thể tạo embedding cho giải thích AHP; đề xuất vẫn ở bản nháp, hãy thử lại.",
                ) from exc
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    log.info("governance.proposal.submitted", proposal_id=str(proposal_id))
    return row


async def get_ahp_proposal_rationale(
    proposal_id: uuid.UUID,
    *,
    criterion_key: str | None = None,
    query: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Read rationale chunks for one AHP proposal; retrieval never changes it."""
    proposal = await get_proposal(proposal_id)
    if proposal["proposal_type"] != "ahp_ranking_proposal":
        raise GovernanceError("PROPOSAL_TYPE_INVALID", "Chỉ đề xuất AHP có giải thích trọng số")
    try:
        return await rationale_retrieval.retrieve_rationale_for_proposal(
            proposal_id, criterion_key=criterion_key, query=query, top_k=top_k
        )
    except ValueError as exc:
        raise GovernanceError("RATIONALE_QUERY_INVALID", str(exc)) from exc


async def withdraw_proposal(
    *, proposal_id: uuid.UUID, actor_expert_id: uuid.UUID, enforce_owner: bool = False
) -> dict:
    """`draft`/`submitted`/`under_review` → `withdrawn` (chốt). Người tạo có
    thể rút đề xuất bất cứ lúc nào trước khi nó được duyệt."""
    async with get_session_factory()() as session:
        before = await _require_proposal_for_update(session, proposal_id, ("draft", "submitted", "under_review"))
        if enforce_owner and before["created_by_expert_id"] != actor_expert_id:
            await session.rollback()
            raise GovernanceError("PROPOSAL_OWNER_REQUIRED", "Chỉ người tạo mới được rút đề xuất này")
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
    rubric_id: uuid.UUID | None = None,
    rubric_band_value: Decimal | None = None,
    enforce_owner: bool = False,
) -> dict:
    """Tạo hoặc sửa justification cho MỘT feature trong MỘT đề xuất
    (`uq_ranking_feature_justification_proposal_feature`, 0034). Chỉ khi đề
    xuất còn `draft` — xem docstring module.

    `assertion_kind='weight'` (mặc định) chạy ĐÚNG con đường đã có — mọi tham
    số value-mode bị bỏ qua/phải rỗng. `assertion_kind='value'` (PR-2) là
    nhánh MỚI: `proposed_weight`/`previous_weight` phải là `None`
    (`ck_rfj_assertion_mode_xor`, 0038); rationale/methodology/evidence
    fields dùng CHUNG yêu cầu không rỗng như weight-mode.

    Rubric (0046): `rubric_id`/`rubric_band_value` là CẶP đôi, giống
    `ck_rfj_rubric_pair` ở DB. Với các khoá trong `RUBRIC_REQUIRED_FEATURE_KEYS`
    (6 đặc trưng MVP), rubric là BẮT BUỘC — chuyên gia chọn MỘT band có sẵn của
    rubric hiện có cho đặc trưng đó, KHÔNG được tự gõ một số bất kỳ.
    `normalized_numeric` không được truyền riêng khi dùng rubric — hàm này tự
    suy ra từ band đã chọn (server-side, không bao giờ tin số client tự khai
    một khi đã chọn rubric) để không có hai nguồn sự thật lệch nhau."""
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
    if (rubric_id is None) != (rubric_band_value is None):
        raise GovernanceError("RUBRIC_PAIR_REQUIRED", "rubric_id và rubric_band_value phải cùng có hoặc cùng không")
    if rubric_id is not None and normalized_numeric is not None:
        raise GovernanceError(
            "NORMALIZED_NUMERIC_NOT_ALLOWED_WITH_RUBRIC",
            "Khi dùng rubric, normalized_numeric được SUY RA từ band đã chọn — không tự truyền riêng",
        )

    if assertion_kind == "weight":
        if proposed_weight is None or not (0 <= proposed_weight <= 1):
            raise GovernanceError("PROPOSED_WEIGHT_RANGE", "proposed_weight phải trong [0, 1]")
        if any(
            v is not None
            for v in (raw_numeric, normalized_numeric, categorical_value, effective_at, expires_at,
                       external_source_citation, author_subject, rubric_id, rubric_band_value)
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
        if raw_numeric is None and normalized_numeric is None and rubric_id is None and not (categorical_value or "").strip():
            raise GovernanceError(
                "VALUE_REQUIRED",
                "assertion_kind='value' cần raw_numeric, normalized_numeric, categorical_value, hoặc rubric_id+rubric_band_value",
            )

    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, _JUSTIFICATION_EDITABLE_STATUSES)
        if enforce_owner and proposal["created_by_expert_id"] != created_by_expert_id:
            await session.rollback()
            raise GovernanceError("PROPOSAL_OWNER_REQUIRED", "Chỉ người tạo mới được sửa bản nháp này")
        if proposal["assertion_kind"] != assertion_kind:
            await session.rollback()
            raise GovernanceError(
                "ASSERTION_KIND_MISMATCH",
                f"Đề xuất {proposal_id} là '{proposal['assertion_kind']}', không phải '{assertion_kind}'",
            )
        feature = (
            await session.execute(
                sa.select(ranking_feature_definitions).where(
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
            if feature["feature_key"] in CRM_OWNED_AREA_FEATURE_KEYS:
                await session.rollback()
                raise GovernanceError(
                    "AREA_CRM_OWNED_FEATURE_KEY_NOT_ASSERTABLE",
                    f"'{feature['feature_key']}' là đặc trưng CRM — không có value-mode assertion cho khoá này",
                )
            try:
                _validate_categorical_vocabulary(
                    {"categorical_value": categorical_value.strip() if categorical_value else None}, feature
                )
            except GovernanceError:
                await session.rollback()
                raise

            if feature["feature_key"] in RUBRIC_REQUIRED_FEATURE_KEYS and rubric_id is None:
                await session.rollback()
                raise GovernanceError(
                    "RUBRIC_REQUIRED",
                    f"'{feature['feature_key']}' bắt buộc chọn một mức rubric (band) — "
                    "không được tự gõ giá trị số cho đặc trưng này",
                )
            if rubric_id is not None:
                rubric = (
                    await session.execute(
                        sa.select(ranking_feature_rubrics.c.feature_definition_id).where(
                            ranking_feature_rubrics.c.id == rubric_id
                        )
                    )
                ).mappings().first()
                if rubric is None:
                    await session.rollback()
                    raise GovernanceError("RUBRIC_NOT_FOUND", f"Không có ranking_feature_rubrics {rubric_id}")
                if uuid.UUID(str(rubric["feature_definition_id"])) != uuid.UUID(str(feature_definition_id)):
                    await session.rollback()
                    raise GovernanceError(
                        "RUBRIC_FEATURE_MISMATCH",
                        f"Rubric {rubric_id} không thuộc về đặc trưng {feature_definition_id}",
                    )
                band = (
                    await session.execute(
                        sa.select(ranking_feature_rubric_bands.c.band_value).where(
                            ranking_feature_rubric_bands.c.rubric_id == rubric_id,
                            ranking_feature_rubric_bands.c.band_value == rubric_band_value,
                        )
                    )
                ).first()
                if band is None:
                    await session.rollback()
                    raise GovernanceError(
                        "RUBRIC_BAND_VALUE_INVALID",
                        f"'{rubric_band_value}' không phải một band có thật của rubric {rubric_id}",
                    )
                # Server-derived, never the client's own separately-supplied
                # normalized_numeric — the selected band IS the value.
                normalized_numeric = band[0]

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
            rubric_id=rubric_id,
            rubric_band_value=rubric_band_value,
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


async def get_justification_proposal_id(feature_justification_id: uuid.UUID) -> uuid.UUID:
    async with get_session_factory()() as session:
        proposal_id = await session.scalar(
            sa.select(ranking_feature_justifications.c.proposal_id).where(
                ranking_feature_justifications.c.id == feature_justification_id
            )
        )
        await session.rollback()
    if proposal_id is None:
        raise GovernanceError("JUSTIFICATION_NOT_FOUND", "Không tìm thấy justification")
    return uuid.UUID(str(proposal_id))


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


# --- Rubric (0046) ------------------------------------------------------------
#
# Append-only (`ranking_governance_append_only_guard`, same as every other
# governance table) — a rubric "revision" is a NEW row with a higher
# `rubric_version`, never an edit to an existing one. "Current" rubric for a
# feature = the row with the highest `rubric_version` for that feature; no
# mutable status column exists or is needed, same pattern already used for
# `latest_extraction_status()`/`latest_lifecycle_status()`.


async def create_feature_rubric(
    *, feature_definition_id: uuid.UUID, bands: list[dict], created_by: str
) -> dict:
    """`bands`: exactly five `{value, label, evidence_requirement}` dicts,
    one per `RUBRIC_BAND_VALUES` (0.00/0.25/0.50/0.75/1.00), in any order —
    this function sorts them. `created_by` is the caller's server-resolved
    identity (never a client-supplied name) — same discipline as every other
    governance write. Auto-assigns `rubric_version = max(existing) + 1`."""
    if not created_by.strip():
        raise GovernanceError("CREATED_BY_REQUIRED", "created_by không được rỗng")
    band_values = sorted(RUBRIC_BAND_VALUES)
    by_value = {}
    for band in bands:
        try:
            value = Decimal(str(band["value"]))
        except Exception as exc:
            raise GovernanceError("RUBRIC_BAND_VALUE_INVALID", f"band.value không phải số hợp lệ: {band.get('value')}") from exc
        if value not in band_values:
            raise GovernanceError(
                "RUBRIC_BAND_VALUE_INVALID",
                f"band.value phải thuộc {[str(v) for v in band_values]}, nhận '{value}'",
            )
        if value in by_value:
            raise GovernanceError("RUBRIC_BAND_DUPLICATE", f"band.value '{value}' bị lặp lại")
        if not str(band.get("label", "")).strip():
            raise GovernanceError("RUBRIC_BAND_LABEL_REQUIRED", f"band.label không được rỗng (value={value})")
        if not str(band.get("evidence_requirement", "")).strip():
            raise GovernanceError(
                "RUBRIC_BAND_EVIDENCE_REQUIREMENT_REQUIRED", f"band.evidence_requirement không được rỗng (value={value})"
            )
        by_value[value] = band
    missing = sorted(set(band_values) - set(by_value))
    if missing:
        raise GovernanceError("RUBRIC_BAND_MISSING", f"Thiếu band cho các mức: {[str(v) for v in missing]}")

    async with get_session_factory()() as session:
        feature = (
            await session.execute(
                sa.select(ranking_feature_definitions.c.id, ranking_feature_definitions.c.value_type).where(
                    ranking_feature_definitions.c.id == feature_definition_id
                )
            )
        ).mappings().first()
        if feature is None:
            await session.rollback()
            raise GovernanceError(
                "FEATURE_DEFINITION_NOT_FOUND", f"Không có ranking_feature_definitions {feature_definition_id}"
            )
        if feature["value_type"] != "numeric":
            await session.rollback()
            raise GovernanceError(
                "RUBRIC_REQUIRES_NUMERIC_FEATURE",
                f"Rubric chỉ áp dụng cho đặc trưng value_type='numeric', '{feature_definition_id}' là '{feature['value_type']}'",
            )
        current_version = await session.scalar(
            sa.select(sa.func.max(ranking_feature_rubrics.c.rubric_version)).where(
                ranking_feature_rubrics.c.feature_definition_id == feature_definition_id
            )
        )
        next_version = (current_version or 0) + 1
        rubric_id = uuid.uuid4()
        now = _now()
        await session.execute(
            sa.insert(ranking_feature_rubrics).values(
                id=rubric_id,
                feature_definition_id=feature_definition_id,
                rubric_version=next_version,
                created_by=created_by.strip(),
                created_at=now,
            )
        )
        await session.execute(
            sa.insert(ranking_feature_rubric_bands),
            [
                {
                    "id": uuid.uuid4(),
                    "rubric_id": rubric_id,
                    "band_value": value,
                    "label": str(by_value[value]["label"]).strip(),
                    "evidence_requirement": str(by_value[value]["evidence_requirement"]).strip(),
                    "display_order": index,
                }
                for index, value in enumerate(band_values)
            ],
        )
        await session.commit()
        row = await _fetch_rubric_with_bands(session, rubric_id)
        await session.rollback()

    log.info(
        "governance.rubric.created",
        feature_definition_id=str(feature_definition_id),
        rubric_id=str(rubric_id),
        rubric_version=next_version,
    )
    return row


async def _fetch_rubric_with_bands(session, rubric_id: uuid.UUID) -> dict:
    rubric = (
        await session.execute(sa.select(ranking_feature_rubrics).where(ranking_feature_rubrics.c.id == rubric_id))
    ).mappings().first()
    band_rows = (
        await session.execute(
            sa.select(ranking_feature_rubric_bands)
            .where(ranking_feature_rubric_bands.c.rubric_id == rubric_id)
            .order_by(ranking_feature_rubric_bands.c.display_order)
        )
    ).mappings().all()
    return {**dict(rubric), "bands": [dict(b) for b in band_rows]}


async def list_feature_rubrics(feature_definition_id: uuid.UUID) -> list[dict]:
    """Full version history for one feature, oldest first, each with its
    bands — the append-only audit trail a rubric maintainer/CEO can inspect."""
    async with get_session_factory()() as session:
        rubric_ids = (
            await session.execute(
                sa.select(ranking_feature_rubrics.c.id)
                .where(ranking_feature_rubrics.c.feature_definition_id == feature_definition_id)
                .order_by(ranking_feature_rubrics.c.rubric_version)
            )
        ).scalars().all()
        return [await _fetch_rubric_with_bands(session, rubric_id) for rubric_id in rubric_ids]


async def get_current_feature_rubric(feature_definition_id: uuid.UUID) -> dict | None:
    """The highest-`rubric_version` row for a feature, with its bands —
    `None` if the feature has never had a rubric authored (still usable
    free-form, unless the feature key is in `RUBRIC_REQUIRED_FEATURE_KEYS`,
    in which case `upsert_justification()` will refuse any assertion until
    one exists)."""
    async with get_session_factory()() as session:
        rubric_id = await session.scalar(
            sa.select(ranking_feature_rubrics.c.id)
            .where(ranking_feature_rubrics.c.feature_definition_id == feature_definition_id)
            .order_by(ranking_feature_rubrics.c.rubric_version.desc())
            .limit(1)
        )
        if rubric_id is None:
            await session.rollback()
            return None
        row = await _fetch_rubric_with_bands(session, rubric_id)
        await session.rollback()
    return row


async def list_feature_definitions(*, grain: str | None = None) -> list[dict]:
    """Read-only catalog of active feature definitions — the frontend has no
    other way to discover a `feature_definition_id` for a canonical feature
    key (e.g. to call `create_feature_rubric`/`upsert_justification` for one
    of the RUBRIC_REQUIRED_FEATURE_KEYS). No side effects; never used by any
    scoring/publish path (those already resolve definitions server-side)."""
    async with get_session_factory()() as session:
        query = sa.select(ranking_feature_definitions).where(ranking_feature_definitions.c.status == "active")
        if grain is not None:
            query = query.where(ranking_feature_definitions.c.grain == grain)
        rows = (
            await session.execute(query.order_by(ranking_feature_definitions.c.grain, ranking_feature_definitions.c.feature_key))
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


async def get_project_v3_coverage(project_id: uuid.UUID) -> dict:
    """Build a single-query, read-only coverage projection for Ranking V3.

    Required keys come from the currently published hierarchical config, while
    lifecycle/status/evidence readiness come from the value-proposal tables.
    This is deliberately a projection: it never materializes values or changes
    proposal state, and it keeps draft/approved-but-unpublished rows visible as
    blockers rather than calling them published.
    """
    now = _now()
    async with get_session_factory()() as session:
        config = (
            await session.execute(
                sa.select(ranking_configs.c.version, ranking_configs.c.hierarchical_weights)
                .where(ranking_configs.c.status == "published")
                .order_by(ranking_configs.c.version.desc())
                .limit(1)
            )
        ).mappings().first()
        area_rows = (
            await session.execute(sa.select(areas.c.id, areas.c.external_id, areas.c.area_name).where(areas.c.project_id == project_id))
        ).mappings().all()
        # One grouped query covers all feature proposals and linked evidence;
        # readiness itself remains the authoritative lifecycle resolver.
        ready_predicate = evidence_extraction.document_is_ready(ranking_evidence_documents.c.id)
        rows = (
            await session.execute(
                sa.select(
                    ranking_feature_definitions.c.feature_key,
                    ranking_feature_definitions.c.grain,
                    ranking_weight_proposals.c.id.label("proposal_id"),
                    ranking_weight_proposals.c.status,
                    ranking_weight_proposals.c.scope_type,
                    ranking_weight_proposals.c.area_id,
                    ranking_feature_justifications.c.id.label("justification_id"),
                    ranking_feature_justifications.c.effective_at,
                    ranking_feature_justifications.c.expires_at,
                    ranking_feature_justifications.c.updated_at,
                    sa.func.count(sa.distinct(ranking_evidence_document_features.c.document_id)).label("evidence_count"),
                    sa.func.count(sa.distinct(ranking_evidence_document_features.c.document_id)).filter(ready_predicate).label("ready_evidence_count"),
                )
                .select_from(
                    ranking_feature_justifications.join(
                        ranking_weight_proposals,
                        ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
                    ).join(
                        ranking_feature_definitions,
                        ranking_feature_definitions.c.id == ranking_feature_justifications.c.feature_definition_id,
                    ).outerjoin(
                        ranking_evidence_document_features,
                        ranking_evidence_document_features.c.feature_justification_id == ranking_feature_justifications.c.id,
                    ).outerjoin(
                        ranking_evidence_documents,
                        ranking_evidence_documents.c.id == ranking_evidence_document_features.c.document_id,
                    )
                )
                .where(
                    ranking_weight_proposals.c.project_id == project_id,
                    ranking_weight_proposals.c.assertion_kind == "value",
                    ranking_feature_justifications.c.assertion_kind == "value",
                    ranking_feature_definitions.c.status == "active",
                )
                .group_by(
                    ranking_feature_definitions.c.feature_key,
                    ranking_feature_definitions.c.grain,
                    ranking_weight_proposals.c.id,
                    ranking_weight_proposals.c.status,
                    ranking_weight_proposals.c.scope_type,
                    ranking_weight_proposals.c.area_id,
                    ranking_feature_justifications.c.id,
                    ranking_feature_justifications.c.effective_at,
                    ranking_feature_justifications.c.expires_at,
                    ranking_feature_justifications.c.updated_at,
                )
                .order_by(ranking_feature_justifications.c.updated_at.desc())
            )
        ).mappings().all()
        await session.rollback()

    hierarchy = (config or {}).get("hierarchical_weights") or {}
    required_features = []
    for grain in ("project", "market", "area"):
        for key, spec in (hierarchy.get(grain) or {}).items():
            if isinstance(spec, dict) and float(spec.get("weight", 0) or 0) > 0:
                required_features.append({"feature_key": key, "grain": grain, "weight": spec.get("weight")})

    def classify(candidates: list[dict]) -> dict:
        if not candidates:
            return {"status": "missing", "reason": "NO_PUBLISHED_VALUE", "evidence_ready": False}
        candidate = candidates[0]
        if candidate["status"] != "published":
            return {"status": candidate["status"], "reason": "VALUE_NOT_PUBLISHED", "evidence_ready": False}
        if candidate["effective_at"] is not None and candidate["effective_at"] > now:
            return {"status": "blocked", "reason": "VALUE_NOT_EFFECTIVE", "evidence_ready": False}
        if candidate["expires_at"] is not None and candidate["expires_at"] <= now:
            return {"status": "expired", "reason": "VALUE_EXPIRED", "evidence_ready": False}
        ready = int(candidate["ready_evidence_count"] or 0) > 0
        if not ready:
            return {"status": "blocked", "reason": "EVIDENCE_NOT_READY", "evidence_ready": False}
        return {"status": "published", "reason": None, "evidence_ready": True}

    grouped: dict[tuple[str, str, str | None], list[dict]] = {}
    for row in rows:
        key = (row["feature_key"], row["grain"], str(row["area_id"]) if row["area_id"] else None)
        grouped.setdefault(key, []).append(dict(row))

    def summary(grain: str, area_id: str | None = None) -> dict:
        required = [item for item in required_features if item["grain"] == grain]
        details = []
        for item in required:
            state = classify(grouped.get((item["feature_key"], grain, area_id), []))
            details.append({**item, **state})
        return {
            "required": len(details),
            "published": sum(item["status"] == "published" for item in details),
            "missing": sum(item["status"] == "missing" for item in details),
            "blocked": sum(item["status"] in {"blocked", "draft", "submitted", "approved"} for item in details),
            "expired": sum(item["status"] == "expired" for item in details),
            "features": details,
        }

    return {
        "project_id": str(project_id),
        "config_version": config["version"] if config else None,
        "required_features": required_features,
        "project": summary("project"),
        "market": summary("market"),
        "areas": [
            {"area_id": str(row["id"]), "external_id": row.get("external_id"), "name": row.get("area_name"), **summary("area", str(row["id"]))}
            for row in area_rows
        ],
        "evidence_blockers": [
            {"feature_key": feature["feature_key"], "grain": feature["grain"], "reason": feature["reason"]}
            for scope in (summary("project"), summary("market"), *(summary("area", str(row["id"])) for row in area_rows))
            for feature in scope["features"]
            if feature["reason"]
        ],
    }


# --- Bằng chứng (upload) ---------------------------------------------------------


async def register_evidence_document(
    *,
    project_id: uuid.UUID | None = None,
    area_id: uuid.UUID | None = None,
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

        if project_id is not None:
            project_exists = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_id))
            if project_exists is None:
                await session.rollback()
                raise GovernanceError("PROJECT_NOT_FOUND", "Dự án của evidence không tồn tại")
        if area_id is not None:
            area_project_id = await session.scalar(sa.select(areas.c.project_id).where(areas.c.id == area_id))
            if area_project_id is None:
                await session.rollback()
                raise GovernanceError("AREA_NOT_FOUND", "Phân khu của evidence không tồn tại")
            if project_id is None or area_project_id != project_id:
                await session.rollback()
                raise GovernanceError("AREA_PROJECT_MISMATCH", "Phân khu phải thuộc đúng dự án của evidence")
        if proposal_id is not None:
            proposal_project_id = await session.scalar(
                sa.select(ranking_weight_proposals.c.project_id).where(ranking_weight_proposals.c.id == proposal_id)
            )
            if proposal_project_id is None:
                await session.rollback()
                raise GovernanceError("PROPOSAL_NOT_FOUND", "Đề xuất của evidence không tồn tại")
            if project_id is not None and proposal_project_id != project_id:
                await session.rollback()
                raise GovernanceError("DOCUMENT_PROJECT_MISMATCH", "Evidence và proposal phải thuộc cùng một dự án")

        document_id = uuid.uuid4()
        try:
            await session.execute(
                sa.insert(ranking_evidence_documents).values(
                    id=document_id,
                    project_id=project_id,
                    area_id=area_id,
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


async def find_document_by_checksum(sha256_checksum: str) -> dict | None:
    """Read-only lookup used by the upload route to reuse an already-stored
    document instead of writing a byte-identical duplicate row (idempotent
    re-upload — §Phase2A "idempotently reject or reuse duplicate content").
    Global by checksum: `ranking_evidence_documents` has no project/tenant
    column of its own (scoping lives one hop away, via `proposal_id` →
    `ranking_weight_proposals.project_id`), so the same PDF bytes uploaded
    twice — by the same or a different expert, linked or not yet linked to
    any proposal — are treated as the same evidence, not a scope leak: no
    read access is granted here, only the existing row's own metadata is
    returned to the uploader who just proved possession of the identical
    bytes."""
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ranking_evidence_documents).where(
                    ranking_evidence_documents.c.sha256_checksum == sha256_checksum
                )
            )
        ).mappings().first()
        await session.rollback()
    return dict(row) if row else None


async def list_documents(
    *, project_id: uuid.UUID | None = None, uploaded_by_expert_id: uuid.UUID | None = None
) -> list[dict]:
    """Read-only listing for the expert document library. Exactly one of the
    two filters must be given by the caller (enforced in `src/api/governance.py`,
    not here) — an unscoped "list everything" query is never a supported shape:

    - `project_id`: active documents directly owned by that project, plus
      legacy proposal-linked documents where no direct project has been set.
      Historic standalone rows (`project_id IS NULL`, no matching proposal)
      remain auditable only through their uploader view; they are never
      silently exposed in a project list.
    - `uploaded_by_expert_id`: every document that expert uploaded, scoped or
      not — the self-service "my uploads, including ones I haven't linked to
      a proposal/project yet" view an expert needs before feature-mapping.

    The uploader management view keeps every lifecycle state for audit.
    Project-scoped listings are intentionally active-only, so archived/deleted
    documents cannot be selected for new project work.
    """
    latest_event_type = (
        sa.select(ranking_evidence_document_lifecycle_events.c.event_type)
        .where(ranking_evidence_document_lifecycle_events.c.document_id == ranking_evidence_documents.c.id)
        .order_by(ranking_evidence_document_lifecycle_events.c.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    query = sa.select(ranking_evidence_documents, latest_event_type.label("_latest_lifecycle_event_type"))
    if project_id is not None:
        query = query.select_from(
            ranking_evidence_documents.outerjoin(
                ranking_weight_proposals, ranking_weight_proposals.c.id == ranking_evidence_documents.c.proposal_id
            )
        ).where(
            sa.or_(
                ranking_evidence_documents.c.project_id == project_id,
                sa.and_(
                    ranking_evidence_documents.c.project_id.is_(None),
                    ranking_weight_proposals.c.project_id == project_id,
                ),
            ),
            sa.or_(latest_event_type.is_(None), latest_event_type == "restored"),
        )
    elif uploaded_by_expert_id is not None:
        query = query.where(ranking_evidence_documents.c.uploaded_by_expert_id == uploaded_by_expert_id)
    else:
        raise GovernanceError("SCOPE_REQUIRED", "list_documents cần project_id hoặc uploaded_by_expert_id")

    async with get_session_factory()() as session:
        rows = (await session.execute(query.order_by(ranking_evidence_documents.c.created_at.desc()))).mappings().all()
        await session.rollback()
    return [
        {**{k: v for k, v in row.items() if k != "_latest_lifecycle_event_type"},
         "lifecycle_status": _lifecycle_status_from_event_type(row["_latest_lifecycle_event_type"])}
        for row in rows
    ]


async def get_document_project_id(document_id: uuid.UUID) -> uuid.UUID | None:
    """Return the document's authoritative project scope without guessing.

    A new document carries ``project_id``.  A pre-0047 document may still be
    safely scoped only through its proposal.  A NULL result is an immutable
    legacy/audit-only record and must not enter retrieval or new attachment.
    """
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(
                    ranking_evidence_documents.c.project_id,
                    ranking_weight_proposals.c.project_id.label("proposal_project_id"),
                )
                .select_from(
                    ranking_evidence_documents.outerjoin(
                        ranking_weight_proposals,
                        ranking_weight_proposals.c.id == ranking_evidence_documents.c.proposal_id,
                    )
                )
                .where(ranking_evidence_documents.c.id == document_id)
            )
        ).mappings().first()
        await session.rollback()
    if row is None:
        raise GovernanceError("DOCUMENT_NOT_FOUND", "Không tìm thấy evidence document")
    return row["project_id"] or row["proposal_project_id"]


async def get_justification_project_id(feature_justification_id: uuid.UUID) -> uuid.UUID:
    async with get_session_factory()() as session:
        project_id = await session.scalar(
            sa.select(ranking_weight_proposals.c.project_id)
            .select_from(
                ranking_feature_justifications.join(
                    ranking_weight_proposals,
                    ranking_feature_justifications.c.proposal_id == ranking_weight_proposals.c.id,
                )
            )
            .where(ranking_feature_justifications.c.id == feature_justification_id)
        )
        await session.rollback()
    if project_id is None:
        raise GovernanceError("JUSTIFICATION_NOT_FOUND", "Không tìm thấy justification")
    return project_id


async def get_project_expert_analysis_overview(project_id: uuid.UUID) -> dict[str, Any]:
    """Read-only aggregation for the Expert Analysis page.

    Counts are computed from the effective readiness resolver, not immutable
    registration metadata.  It intentionally contains no global configuration,
    run, proposal, or assertion information: those records belong to other
    users or ranking administration, not the Advisor workspace.
    """
    documents = await list_documents(project_id=project_id)
    ready = processing = failed = 0
    for document in documents:
        readiness = await evidence_extraction.get_document_readiness(uuid.UUID(str(document["id"])))
        if readiness is not None and readiness.eligible:
            ready += 1
        elif readiness is not None and readiness.extraction_status in ("failed", "not_supported"):
            failed += 1
        else:
            processing += 1

    if ready == 0:
        next_action = "Tải lên và trích xuất báo cáo có phạm vi dự án trước khi soạn đánh giá."
    else:
        next_action = "Có thể soạn bản nháp đánh giá định tính từ rubric và bằng chứng sẵn sàng."
    return {
        "documents_ready": ready,
        "documents_processing": processing,
        "documents_failed": failed,
        "next_action": next_action,
    }


async def link_evidence_to_justification(
    *, document_id: uuid.UUID, feature_justification_id: uuid.UUID,
    actor_expert_id: uuid.UUID | None = None, enforce_owner: bool = False,
) -> None:
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

        document_status = _lifecycle_status_from_event_type(await _latest_lifecycle_event_type(session, document_id))
        if document_status != "active":
            await session.rollback()
            raise GovernanceError(
                "DOCUMENT_NOT_ACTIVE",
                f"Document {document_id} đang ở trạng thái '{document_status}' — "
                "không thể gắn làm bằng chứng mới cho một justification",
            )
        is_ready = await session.scalar(
            sa.select(ranking_evidence_documents.c.id).where(
                ranking_evidence_documents.c.id == document_id,
                evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
            )
        )
        if is_ready is None:
            await session.rollback()
            raise GovernanceError(
                "EVIDENCE_NOT_READY",
                f"Document {document_id} chưa có extraction thành công hiện hành với chunk và embedding — "
                "không thể gắn làm bằng chứng mới",
            )

        proposal_row = (
            await session.execute(
                sa.select(ranking_weight_proposals.c.status, ranking_weight_proposals.c.project_id)
            .select_from(
                ranking_feature_justifications.join(
                    ranking_weight_proposals,
                    ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
                )
            )
            .where(ranking_feature_justifications.c.id == feature_justification_id)
            )
        ).mappings().first()
        proposal_status = proposal_row["status"] if proposal_row else None
        if enforce_owner and actor_expert_id is not None:
            # ``proposal_row`` deliberately contains only status/project_id;
            # resolve ownership through the justification's parent proposal.
            proposal_owner = await session.scalar(
                sa.select(ranking_weight_proposals.c.created_by_expert_id)
                .select_from(
                    ranking_feature_justifications.join(
                        ranking_weight_proposals,
                        ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
                    )
                )
                .where(ranking_feature_justifications.c.id == feature_justification_id)
            )
            if proposal_owner != actor_expert_id:
                await session.rollback()
                raise GovernanceError("PROPOSAL_OWNER_REQUIRED", "Chỉ người tạo mới được gắn bằng chứng vào bản nháp này")
        document_project_id = await get_document_project_id(document_id)
        if document_project_id is None:
            await session.rollback()
            raise GovernanceError(
                "DOCUMENT_PROJECT_UNSCOPED",
                "Evidence lịch sử chưa có project scope; chỉ còn dùng để audit, không thể gắn mới",
            )
        if proposal_row is None or document_project_id != proposal_row["project_id"]:
            await session.rollback()
            raise GovernanceError("DOCUMENT_PROJECT_MISMATCH", "Evidence và justification phải thuộc cùng một dự án")
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


async def link_evidence_to_ahp_proposal(
    *, proposal_id: uuid.UUID, document_id: uuid.UUID, actor_expert_id: uuid.UUID, enforce_owner: bool = False
) -> dict:
    """Append one lifecycle-ready project document to an editable AHP proposal.

    The association is intentionally separate from ``ranking_evidence_documents``:
    a ready report may support multiple proposals and its registration metadata is
    append-only.  Retrying the same link is idempotent.
    """
    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, ("draft",))
        if proposal["proposal_type"] != "ahp_ranking_proposal":
            await session.rollback()
            raise GovernanceError("PROPOSAL_TYPE_INVALID", "Chỉ đề xuất AHP mới liên kết bằng chứng cấp đề xuất")
        if enforce_owner and proposal["created_by_expert_id"] != actor_expert_id:
            await session.rollback()
            raise GovernanceError("PROPOSAL_OWNER_REQUIRED", "Chỉ người tạo mới được gắn bằng chứng cho bản nháp này")

        document = await _get_document_or_404(session, document_id)
        if document["project_id"] is None or document["project_id"] != proposal["project_id"]:
            await session.rollback()
            raise GovernanceError("DOCUMENT_PROJECT_MISMATCH", "Bằng chứng phải thuộc đúng dự án của đề xuất")
        if _lifecycle_status_from_event_type(await _latest_lifecycle_event_type(session, document_id)) != "active":
            await session.rollback()
            raise GovernanceError("DOCUMENT_NOT_ACTIVE", "Tài liệu không còn ở trạng thái hoạt động")
        ready_document = await session.scalar(
            sa.select(ranking_evidence_documents.c.id).where(
                ranking_evidence_documents.c.id == document_id,
                evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
            )
        )
        if ready_document is None:
            await session.rollback()
            raise GovernanceError(
                "EVIDENCE_NOT_READY",
                "Tài liệu chưa có extraction thành công hiện hành với chunk và embedding — không thể gắn",
            )
        exists = await session.scalar(
            sa.select(ranking_proposal_evidence_links.c.document_id).where(
                ranking_proposal_evidence_links.c.proposal_id == proposal_id,
                ranking_proposal_evidence_links.c.document_id == document_id,
            )
        )
        if exists is None:
            await session.execute(
                sa.insert(ranking_proposal_evidence_links).values(
                    proposal_id=proposal_id,
                    document_id=document_id,
                    linked_by_expert_id=actor_expert_id,
                    created_at=_now(),
                )
            )
            expert = await get_expert_profile(actor_expert_id)
            await _record_audit_event(
                session,
                ranking_config_id=None,
                proposal_id=proposal_id,
                actor_expert_id=actor_expert_id,
                actor_identity_subject=expert["identity_subject"],
                event_type="submitted",
                before_status=proposal["status"],
                after_status=proposal["status"],
                after_state={"document_id": str(document_id), "evidence_link": "proposal"},
            )
            await session.commit()
        else:
            await session.rollback()
        return document


async def list_linked_evidence_for_ahp_proposal(proposal_id: uuid.UUID) -> list[dict]:
    """Read proposal-level evidence links only; authorization belongs to the API."""
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_evidence_documents)
                .join(
                    ranking_proposal_evidence_links,
                    ranking_proposal_evidence_links.c.document_id == ranking_evidence_documents.c.id,
                )
                .where(ranking_proposal_evidence_links.c.proposal_id == proposal_id)
                .order_by(ranking_proposal_evidence_links.c.created_at)
            )
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


# --- Document archive/delete lifecycle (mandatory-scope item 4) ---------------
#
# `ranking_evidence_documents` cannot carry an `archived_at`/`deleted_at`
# column — it is one of the four tables `0034` put under
# `ranking_governance_append_only_guard` (UPDATE/DELETE unconditionally
# raise). Current lifecycle state is instead the LATEST row in the new
# append-only `ranking_evidence_document_lifecycle_events` (0044), exactly
# the same pattern `0035` already established for extraction status.

DOCUMENT_LIFECYCLE_EVENT_TYPES = ("archived", "deleted", "restored")


async def _get_document_or_404(session, document_id: uuid.UUID) -> dict:
    row = (
        await session.execute(sa.select(ranking_evidence_documents).where(ranking_evidence_documents.c.id == document_id))
    ).mappings().first()
    if row is None:
        raise GovernanceError("DOCUMENT_NOT_FOUND", f"Không có ranking_evidence_documents {document_id}")
    return dict(row)


async def _latest_lifecycle_event_type(session, document_id: uuid.UUID) -> str | None:
    row = (
        await session.execute(
            sa.select(ranking_evidence_document_lifecycle_events.c.event_type)
            .where(ranking_evidence_document_lifecycle_events.c.document_id == document_id)
            .order_by(ranking_evidence_document_lifecycle_events.c.created_at.desc())
            .limit(1)
        )
    ).first()
    return row[0] if row else None


def _lifecycle_status_from_event_type(event_type: str | None) -> str:
    """`None` (no event logged yet) and `'restored'` both mean the document is
    currently usable — `'restored'` is the ACTION, not a resting state."""
    if event_type in (None, "restored"):
        return "active"
    return event_type


async def latest_lifecycle_status(document_id: uuid.UUID) -> str:
    """`'active'` | `'archived'` | `'deleted'` — the single source of truth
    every retrieval/evidence-attach/submit path in this module and
    `src/services/evidence_extraction.py` must check before treating a
    document as usable."""
    async with get_session_factory()() as session:
        event_type = await _latest_lifecycle_event_type(session, document_id)
        await session.rollback()
    return _lifecycle_status_from_event_type(event_type)


async def list_active_document_ids(document_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Bulk version of `latest_lifecycle_status() == 'active'`, for callers
    that need to filter a candidate list (e.g. the Q&A route narrowing a
    caller-supplied `document_ids` list before it ever reaches retrieval —
    rule 8: archived/deleted content excluded even if a stale id is
    supplied)."""
    if not document_ids:
        return set()
    latest_event_type = (
        sa.select(ranking_evidence_document_lifecycle_events.c.event_type)
        .where(ranking_evidence_document_lifecycle_events.c.document_id == ranking_evidence_documents.c.id)
        .order_by(ranking_evidence_document_lifecycle_events.c.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_evidence_documents.c.id, latest_event_type.label("event_type")).where(
                    ranking_evidence_documents.c.id.in_(document_ids)
                )
            )
        ).all()
        await session.rollback()
    return {
        uuid.UUID(str(row.id))
        for row in rows
        if _lifecycle_status_from_event_type(row.event_type) == "active"
    }


async def list_retrieval_eligible_document_ids(document_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Bulk authoritative readiness filter for retrieval and citation.

    Historical document/evidence rows remain listable for audit, but only a
    currently active document whose *latest* extraction succeeded and whose
    persisted chunks all carry embeddings may be sent to retrieval.
    """
    if not document_ids:
        return set()
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_evidence_documents.c.id).where(
                    ranking_evidence_documents.c.id.in_(document_ids),
                    evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
                )
            )
        ).scalars().all()
        await session.rollback()
    return {uuid.UUID(str(document_id)) for document_id in rows}


async def _write_lifecycle_event(
    *, document_id: uuid.UUID, event_type: str, actor_expert_id: uuid.UUID, reason: str | None
) -> dict:
    async with get_session_factory()() as session:
        document = await _get_document_or_404(session, document_id)
        current_status = _lifecycle_status_from_event_type(
            await _latest_lifecycle_event_type(session, document_id)
        )

        if event_type in ("archived", "deleted") and current_status == "deleted":
            await session.rollback()
            raise GovernanceError("DOCUMENT_ALREADY_DELETED", f"Document {document_id} đã bị xoá — không thể thao tác thêm")
        if event_type == "archived" and current_status == "archived":
            await session.rollback()
            raise GovernanceError("DOCUMENT_ALREADY_ARCHIVED", f"Document {document_id} đã được lưu trữ rồi")
        if event_type == "restored" and current_status != "archived":
            await session.rollback()
            raise GovernanceError(
                "DOCUMENT_NOT_ARCHIVED",
                f"Document {document_id} đang ở trạng thái '{current_status}' — chỉ khôi phục được tài liệu 'archived'",
            )

        expert = (
            await session.execute(sa.select(expert_profiles.c.id).where(expert_profiles.c.id == actor_expert_id))
        ).first()
        if expert is None:
            await session.rollback()
            raise GovernanceError("EXPERT_NOT_FOUND", f"Không có expert_profile {actor_expert_id}")

        await session.execute(
            sa.insert(ranking_evidence_document_lifecycle_events).values(
                id=uuid.uuid4(),
                document_id=document_id,
                event_type=event_type,
                actor_expert_id=actor_expert_id,
                reason=reason,
                created_at=_now(),
            )
        )
        # Same precedent `register_evidence_document` already set: a
        # standalone document (`proposal_id IS NULL`) has no proposal
        # decision to audit against (`ck_rcae_entity_reference`), so the
        # config-audit trail is skipped for it — the lifecycle-events row
        # itself is that document's own immutable audit trail either way.
        if document["proposal_id"] is not None:
            identity = (await get_expert_profile(actor_expert_id))["identity_subject"]
            await _record_audit_event(
                session,
                ranking_config_id=None,
                proposal_id=document["proposal_id"],
                actor_expert_id=actor_expert_id,
                actor_identity_subject=identity,
                event_type=event_type,
                before_status=current_status,
                after_status=_lifecycle_status_from_event_type(event_type),
                after_state={"document_id": str(document_id), "reason": reason},
            )
        await session.commit()

    log.info(f"governance.evidence.{event_type}", document_id=str(document_id), actor_expert_id=str(actor_expert_id))
    return {
        "document_id": str(document_id),
        "lifecycle_status": _lifecycle_status_from_event_type(event_type),
        "reason": reason,
    }


async def archive_document(*, document_id: uuid.UUID, actor_expert_id: uuid.UUID, reason: str | None = None) -> dict:
    """Excludes the document from all NEW retrieval/Q&A/citation/evidence-
    attach paths going forward. Never mutates or invalidates a historical,
    already-published proposal's evidence snapshot — those keep citing this
    document's immutable id/title/page/quote, just labeled archived in the UI."""
    return await _write_lifecycle_event(
        document_id=document_id, event_type="archived", actor_expert_id=actor_expert_id, reason=reason
    )


async def delete_document(*, document_id: uuid.UUID, actor_expert_id: uuid.UUID, reason: str | None = None) -> dict:
    """Same exclusion guarantee as `archive_document`, terminal (no restore)."""
    return await _write_lifecycle_event(
        document_id=document_id, event_type="deleted", actor_expert_id=actor_expert_id, reason=reason
    )


async def restore_document(*, document_id: uuid.UUID, actor_expert_id: uuid.UUID, reason: str | None = None) -> dict:
    """`archived` → `active` only — a `deleted` document cannot be restored
    through this function (`DOCUMENT_NOT_ARCHIVED`), matching the state
    machine documented in `pipeline_status.md`."""
    return await _write_lifecycle_event(
        document_id=document_id, event_type="restored", actor_expert_id=actor_expert_id, reason=reason
    )


# --- Duyệt (review) ---------------------------------------------------------------


async def _revalidate_submitted_proposal_for_review(session, proposal: dict) -> None:
    """Recheck evidence and value gates while the proposal row is locked."""
    proposal_id = proposal["id"]
    justifications = (
        await session.execute(
            sa.select(
                ranking_feature_justifications,
                ranking_feature_definitions.c.feature_key,
                ranking_feature_definitions.c.grain,
                ranking_feature_definitions.c.definition_metadata,
                ranking_feature_definitions.c.value_type,
            )
            .join(
                ranking_feature_definitions,
                ranking_feature_definitions.c.id == ranking_feature_justifications.c.feature_definition_id,
            )
            .where(ranking_feature_justifications.c.proposal_id == proposal_id)
        )
    ).mappings().all()

    async def linked_counts(justification_id: uuid.UUID) -> tuple[int, int]:
        total = int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ranking_evidence_document_features)
                .where(ranking_evidence_document_features.c.feature_justification_id == justification_id)
            ) or 0
        )
        ready = int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(
                    ranking_evidence_document_features.join(
                        ranking_evidence_documents,
                        ranking_evidence_documents.c.id == ranking_evidence_document_features.c.document_id,
                    )
                )
                .where(
                    ranking_evidence_document_features.c.feature_justification_id == justification_id,
                    ranking_evidence_documents.c.project_id == proposal["project_id"],
                    evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
                )
            ) or 0
        )
        return total, ready

    if proposal["assertion_kind"] == "value":
        if not justifications:
            raise GovernanceError("NO_JUSTIFICATIONS", "Đề xuất định tính không còn justification hợp lệ")
        for justification in justifications:
            total, ready = await linked_counts(justification["id"])
            if not total or total != ready:
                raise GovernanceError(
                    "EVIDENCE_NOT_READY",
                    "Không thể duyệt: mọi bằng chứng liên kết phải cùng dự án và lifecycle-ready",
                )
            _check_grain_scope_compatibility(justification["grain"], proposal["scope_type"])
            if justification["normalized_numeric"] is not None and not (0 <= justification["normalized_numeric"] <= 1):
                raise GovernanceError("NORMALIZED_VALUE_RANGE", "normalized_numeric phải trong [0, 1]")
            _validate_categorical_vocabulary(justification, justification)
            if proposal["scope_type"] == "market":
                _validate_market_submission(justification, justification)
            if justification["feature_key"] in RUBRIC_REQUIRED_FEATURE_KEYS:
                if justification["rubric_id"] is None or justification["rubric_band_value"] is None:
                    raise GovernanceError("RUBRIC_REQUIRED", "Không thể duyệt: thiếu rubric/band bắt buộc")
                band_exists = await session.scalar(
                    sa.select(ranking_feature_rubric_bands.c.id).where(
                        ranking_feature_rubric_bands.c.rubric_id == justification["rubric_id"],
                        ranking_feature_rubric_bands.c.band_value == justification["rubric_band_value"],
                    )
                )
                if band_exists is None:
                    raise GovernanceError("RUBRIC_BAND_VALUE_INVALID", "Không thể duyệt: rubric/band không còn hợp lệ")
        return

    proposal_document_ids = sa.select(ranking_proposal_evidence_links.c.document_id).where(
        ranking_proposal_evidence_links.c.proposal_id == proposal_id
    )
    direct_total = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(ranking_evidence_documents).where(
                sa.or_(
                    ranking_evidence_documents.c.proposal_id == proposal_id,
                    ranking_evidence_documents.c.id.in_(proposal_document_ids),
                )
            )
        ) or 0
    )
    direct_ready = int(
        await session.scalar(
            sa.select(sa.func.count()).select_from(ranking_evidence_documents).where(
                sa.or_(
                    ranking_evidence_documents.c.proposal_id == proposal_id,
                    ranking_evidence_documents.c.id.in_(proposal_document_ids),
                ),
                ranking_evidence_documents.c.project_id == proposal["project_id"],
                evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
            )
        ) or 0
    )
    linked_total = linked_ready = 0
    for justification in justifications:
        total, ready = await linked_counts(justification["id"])
        linked_total += total
        linked_ready += ready
    if not (direct_total or linked_total) or direct_total + linked_total != direct_ready + linked_ready:
        raise GovernanceError(
            "EVIDENCE_NOT_READY",
            "Không thể duyệt: mọi bằng chứng liên kết phải cùng dự án và lifecycle-ready",
        )

    if proposal["proposal_type"] == "ahp_ranking_proposal":
        snapshot = proposal.get("proposed_hierarchy_snapshot")
        if not snapshot or not snapshot.get("hierarchical_weights") or not snapshot.get("frozen_at"):
            raise GovernanceError(
                "AHP_HIERARCHY_REQUIRED", "Không thể duyệt: đề xuất AHP chưa có bản hierarchy đã đóng băng"
            )
        await _reject_unregistered_criteria(session, snapshot["hierarchical_weights"])
        try:
            validate_hierarchical_weights(snapshot["hierarchical_weights"])
        except HierarchicalConfigError as exc:
            raise GovernanceError(exc.code, exc.message) from exc


async def submit_review(
    *,
    proposal_id: uuid.UUID,
    decision: str,
    comment: str,
    reviewer_subject: str | None = None,
    reviewer_is_ceo: bool = False,
    # HTTP callers must pass the authenticated scope.  ``ALL`` preserves the
    # pre-existing trusted in-process service contract used by historical
    # replay tests; it is never derived from a client request.
    reviewer_project_scope: frozenset[str] | str = "ALL",
    evidence_review_acknowledged: bool = False,
) -> dict:
    """Một reviewer, một quyết định cho một đề xuất
    (`uq_ranking_proposal_review_reviewer`, 0034 — gọi lại là IntegrityError,
    không phải "sửa" quyết định cũ, bảng append-only).

    Chuyển trạng thái đề xuất:
      - `approved` → `approved` (weight-mode đòi `proposed_config_id` đã gắn —
        xem `set_proposed_config`; value-mode KHÔNG đòi, không liên quan
        `ranking_configs`)
      - `rejected` → `rejected` (chốt)
      - Chỉ `approved` hoặc `rejected`; không có public request-changes flow.

    **D18 close-out (was: value-mode-only, now applies to BOTH assertion
    kinds).** There used to be an `else` branch here that let weight-mode
    proposals be approved by any caller-supplied `reviewer_expert_id` with no
    CEO check and no self-approval guard — `tests/test_services/test_governance_value_mode.py
    ::test_weight_mode_review_unaffected_no_ceo_check` explicitly asserted
    that gap existed and was deliberately left open. It is closed now: every
    review, regardless of `assertion_kind`, requires a real authenticated
    `reviewer_subject` (never a request-body field), requires
    `reviewer_is_ceo is True` (`CEO_APPROVAL_REQUIRED` otherwise), and forbids
    a reviewer whose resolved expert id matches the proposal's author
    (`SELF_APPROVAL_FORBIDDEN`). `PROPOSED_CONFIG_MISSING` remains
    weight-mode-only — value-mode assertions have no `ranking_configs` link
    to check."""
    if decision not in REVIEW_DECISIONS:
        raise GovernanceError("DECISION_INVALID", f"decision phải thuộc {REVIEW_DECISIONS}")
    if not comment.strip():
        raise GovernanceError("COMMENT_REQUIRED", "comment không được rỗng — quyết định không kèm lý do không dùng được")
    if decision == "rejected" and len(comment.strip()) < 8:
        raise GovernanceError("REJECTION_REASON_REQUIRED", "Cần nêu lý do từ chối rõ ràng bằng tiếng Việt")

    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, _REVIEWABLE_STATUSES)
        is_value_mode = proposal["assertion_kind"] == "value"

        if not reviewer_subject:
            await session.rollback()
            raise GovernanceError(
                "IDENTITY_REQUIRED", "Duyệt đề xuất cần danh tính OIDC đã xác thực (subject)"
            )
        if not reviewer_is_ceo:
            await session.rollback()
            raise GovernanceError(
                "CEO_APPROVAL_REQUIRED", "Chỉ CEO (xác thực OIDC, vai trò thật CRM.CEO) mới được duyệt đề xuất"
            )
        resolved = await get_or_create_expert_profile(identity_subject=reviewer_subject)
        reviewer_expert_id = uuid.UUID(str(resolved["id"]))
        if reviewer_expert_id == proposal["created_by_expert_id"]:
            await session.rollback()
            raise GovernanceError(
                "SELF_APPROVAL_FORBIDDEN", "Không thể tự duyệt đề xuất của chính mình"
            )
        external_project_id = await session.scalar(
            sa.select(projects.c.external_id).where(projects.c.id == proposal["project_id"])
        )
        if external_project_id is None or (
            reviewer_project_scope != "ALL" and external_project_id not in reviewer_project_scope
        ):
            await session.rollback()
            raise GovernanceError("REVIEW_ITEM_NOT_FOUND", "Không tìm thấy đề xuất cần duyệt")
        if decision == "approved" and not evidence_review_acknowledged:
            await session.rollback()
            raise GovernanceError("EVIDENCE_REVIEW_ACK_REQUIRED", "CEO phải xác nhận đã xem bằng chứng trước khi duyệt")
        await _revalidate_submitted_proposal_for_review(session, proposal)
        # An AHP decision is only meaningful when the matching hierarchical
        # scorer is enabled.  Reject before recording a CEO approval or
        # publishing a config; `_apply_ahp_proposal` repeats this guard in
        # case configuration changes between review and application.
        if (
            decision == "approved"
            and proposal["proposal_type"] == "ahp_ranking_proposal"
            and not get_settings().hierarchical_ranking_enabled
        ):
            await session.rollback()
            raise GovernanceError(
                "HIERARCHICAL_RANKING_DISABLED",
                "Không thể duyệt đề xuất AHP khi hierarchical_ranking_enabled=false",
            )
        # PROPOSED_CONFIG_MISSING only applies to the legacy admin weight-mode
        # flow (config attached beforehand via the now-disabled
        # set_proposed_config route). An 'ahp_ranking_proposal' has no
        # proposed_config_id yet at approval time BY DESIGN — its config is
        # created FROM the frozen snapshot only after this approval commits
        # (see _apply_ahp_proposal below), never before.
        if (
            not is_value_mode
            and proposal["proposal_type"] != "ahp_ranking_proposal"
            and decision == "approved"
            and proposal["proposed_config_id"] is None
        ):
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
                    reviewer_subject=reviewer_subject,
                    reviewer_is_ceo=reviewer_is_ceo,
                    evidence_review_acknowledged=(True if decision == "approved" else False),
                )
            )
        except sa.exc.IntegrityError as exc:
            await session.rollback()
            raise GovernanceError(
                "ALREADY_REVIEWED", "Chuyên gia này đã duyệt đề xuất này rồi — một người, một quyết định"
            ) from exc

        after_status = {"approved": "approved", "rejected": "rejected"}[decision]
        now = _now()
        update_values: dict[str, Any] = {"status": after_status, "updated_at": now}
        if after_status == "approved":
            update_values["approved_at"] = now
            if proposal["proposal_type"] == "ahp_ranking_proposal":
                # Recorded in THIS same review transaction — the review
                # decision and "an apply attempt is now owed" are never
                # separately committed facts. The actual config/publish/run
                # creation happens in a following, independent step
                # (`_apply_ahp_proposal`, see below) so a failure there can
                # never roll back or erase this already-durable approval.
                update_values["ahp_application_status"] = "pending"
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
            event_type=decision,
            before_status=proposal["status"],
            after_status=after_status,
            after_state={"decision": decision, "evidence_review_acknowledged": decision == "approved"},
        )
        await session.commit()
        row = await _fetch_proposal(session, proposal_id)
        await session.rollback()

    if after_status == "approved" and row["proposal_type"] == "ahp_ranking_proposal":
        # Deliberately AFTER the review session above has already committed
        # and closed — the CEO's approval is durable no matter what happens
        # next (mission Part D: "if application fails, keep review/audit
        # provenance; never pretend ranking changed").
        row = await _apply_ahp_proposal(proposal_id, reviewer_expert_id=reviewer_expert_id)

    log.info("governance.proposal.reviewed", proposal_id=str(proposal_id), decision=decision)
    return row


async def _apply_ahp_proposal(proposal_id: uuid.UUID, *, reviewer_expert_id: uuid.UUID) -> dict:
    """Atomically publish and bind an AHP config, then dispatch its run.

    Application remains ``pending`` until the worker has persisted a completed
    run. SQL publication/linkage is one transaction; Redis dispatch necessarily
    happens afterwards, leaving a durable queued run if Redis is unavailable.
    """
    try:
        if not get_settings().hierarchical_ranking_enabled:
            raise GovernanceError(
                "HIERARCHICAL_RANKING_DISABLED",
                "Không thể áp dụng đề xuất AHP khi hierarchical_ranking_enabled=false",
            )

        async with get_session_factory()() as session:
            proposal = await _require_proposal_for_update(session, proposal_id, ("approved",))
            if proposal["proposal_type"] != "ahp_ranking_proposal":
                raise GovernanceError("PROPOSAL_TYPE_INVALID", "Đây không phải đề xuất AHP")
            if proposal["ahp_application_status"] in ("applied", "failed") or proposal["applied_ranking_run_id"] is not None:
                await session.rollback()
                return proposal
            if proposal["ahp_application_status"] != "pending":
                raise GovernanceError("AHP_APPLICATION_NOT_PENDING", "Đề xuất AHP chưa ở trạng thái chờ áp dụng")

            base_config = (
                await session.execute(
                    sa.select(ranking_configs)
                    .where(ranking_configs.c.status == "published")
                    .with_for_update()
                )
            ).mappings().first()
            if base_config is None:
                raise GovernanceError("NO_PUBLISHED_CONFIG", "Không có ranking_configs published để tạo bản kế tiếp")

            # A normal queued run historically binds its config at worker
            # claim.  Freeze an already-queued run to the current published
            # version *before* publishing this proposal's version; otherwise a
            # sync job that was already waiting could accidentally calculate
            # against the newly-approved AHP configuration.
            prior_run = (
                await session.execute(
                    sa.select(ranking_runs)
                    .where(
                        ranking_runs.c.project_id == proposal["project_id"],
                        ranking_runs.c.status.in_(("queued", "running")),
                    )
                    .order_by(sa.case((ranking_runs.c.status == "running", 0), else_=1), ranking_runs.c.enqueued_at)
                    .with_for_update()
                    .limit(1)
                )
            ).mappings().first()
            if prior_run is not None and prior_run["status"] == "queued" and prior_run["config_version_id"] is None:
                await session.execute(
                    sa.update(ranking_runs)
                    .where(ranking_runs.c.id == prior_run["id"], ranking_runs.c.status == "queued")
                    .values(config_version_id=base_config["id"])
                )

            snapshot = proposal["proposed_hierarchy_snapshot"]
            draft = await create_draft_in_session(
                session,
                weights=dict(base_config["weights"]),
                hierarchical_weights=snapshot["hierarchical_weights"],
                min_weight_coverage=float(base_config["min_weight_coverage"]),
                note=f"AHP ranking proposal {proposal_id} — CEO đã duyệt",
                created_by=f"advisor-ahp-proposal:{proposal_id}",
                copied_from_version=int(base_config["version"]),
            )
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(ranking_weight_proposals.c.id == proposal_id)
                .values(proposed_config_id=draft["id"], updated_at=_now())
            )
            published = await publish_in_session(
                session, version=int(draft["version"]), published_by=f"ceo-approval:{proposal_id}"
            )
            # Import here, after this governance module has completed import.
            # `src.ranking.service` reads governance-owned feature constants and
            # finalizes proposal-bound runs, so importing its enqueue helper at
            # module load time creates a worker-startup cycle:
            # rank_project -> ranking.service -> governance -> ranking.service.
            # This path is only reached after a durable CEO approval, making a
            # narrow local import both safe and materially simpler than adding a
            # reverse ranking -> governance dependency.
            from src.ranking.service import enqueue_ahp_application_run_in_session

            intent_status = "deferred" if prior_run is not None else "queued"
            run_id = await enqueue_ahp_application_run_in_session(
                session,
                project_id=proposal["project_id"],
                proposal_id=proposal_id,
                config_version_id=published["id"],
                status=intent_status,
            )
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(ranking_weight_proposals.c.id == proposal_id)
                .values(
                    applied_ranking_run_id=run_id,
                    ahp_application_status="awaiting_prior_run" if intent_status == "deferred" else "queued",
                    updated_at=_now(),
                )
            )
            # Do not issue a second, fallible read after commit: if that read
            # lost its connection, the exception handler below could otherwise
            # mislabel an already-committed config/run linkage as failed.
            row = {
                **proposal,
                "proposed_config_id": published["id"],
                "applied_ranking_run_id": run_id,
                "ahp_application_status": "awaiting_prior_run" if intent_status == "deferred" else "queued",
            }
            reviewer = await get_expert_profile(reviewer_expert_id)
            await _record_audit_event(
                session,
                ranking_config_id=published["id"],
                proposal_id=proposal_id,
                actor_expert_id=reviewer_expert_id,
                actor_identity_subject=reviewer["identity_subject"],
                event_type="ahp_application_deferred" if intent_status == "deferred" else "ahp_application_queued",
                before_status="approved",
                after_status="approved",
                after_state={"run_id": str(run_id), "config_version_id": str(published["id"]), "prior_run_id": str(prior_run["id"]) if prior_run else None},
            )
            await session.commit()
    except Exception as exc:
        log.error("governance.ahp_proposal.apply_failed", proposal_id=str(proposal_id), error_type=type(exc).__name__, exc_info=exc)
        async with get_session_factory()() as session:
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(
                    ranking_weight_proposals.c.id == proposal_id,
                    ranking_weight_proposals.c.ahp_application_status.in_(
                        ("pending", "awaiting_prior_run", "queued", "running")
                    ),
                )
                .values(ahp_application_status="failed", updated_at=_now())
            )
            await session.commit()
            row = await _fetch_proposal(session, proposal_id)
            await session.rollback()
        return row

    # A missing run id is impossible after the atomic insert. Dispatch failure
    # leaves a durable queued intent with a structured error; deferred intents
    # are promoted only after the already-active project run becomes terminal.
    if row["ahp_application_status"] == "queued":
        from src.ranking.service import dispatch_persisted_ranking_run

        await dispatch_persisted_ranking_run(
            project_id=row["project_id"],
            run_id=row["applied_ranking_run_id"],
            trigger="config_change",
        )
    return row


async def finalize_ahp_application_run(*, run_id: uuid.UUID, succeeded: bool) -> None:
    """Move only the proposal bound to ``run_id`` out of pending.

    The worker invokes this after it has written ``ranking_runs.status``. A
    proposal cannot become applied merely because its run was queued.
    """
    async with get_session_factory()() as session:
        proposal = (
            await session.execute(
                sa.select(ranking_weight_proposals, ranking_runs.c.status.label("run_status"))
                .join(ranking_runs, ranking_runs.c.id == ranking_weight_proposals.c.applied_ranking_run_id)
                .where(ranking_weight_proposals.c.applied_ranking_run_id == run_id)
                .with_for_update()
            )
        ).mappings().first()
        if proposal is None or proposal["ahp_application_status"] not in ("queued", "running"):
            await session.rollback()
            return
        if succeeded and proposal["run_status"] == "completed":
            now = _now()
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(ranking_weight_proposals.c.id == proposal["id"])
                .values(status="published", ahp_application_status="applied", published_at=now, updated_at=now)
            )
        elif not succeeded and proposal["run_status"] == "failed":
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(ranking_weight_proposals.c.id == proposal["id"])
                .values(ahp_application_status="failed", updated_at=_now())
            )
        await session.commit()


async def mark_ahp_application_run_running(*, run_id: uuid.UUID) -> None:
    """Reflect the worker claim for one bound proposal, never for normal runs."""
    async with get_session_factory()() as session:
        proposal = (
            await session.execute(
                sa.select(ranking_weight_proposals.c.id)
                .join(ranking_runs, ranking_runs.c.id == ranking_weight_proposals.c.applied_ranking_run_id)
                .where(
                    ranking_weight_proposals.c.applied_ranking_run_id == run_id,
                    ranking_weight_proposals.c.ahp_application_status == "queued",
                    ranking_runs.c.status == "running",
                )
                .with_for_update()
            )
        ).first()
        if proposal is None:
            await session.rollback()
            return
        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal.id)
            .values(ahp_application_status="running", updated_at=_now())
        )
        await session.commit()


async def retry_ahp_application(
    *,
    proposal_id: uuid.UUID,
    actor_expert_id: uuid.UUID,
    actor_subject: str,
    actor_is_ceo: bool,
    reason: str,
) -> dict:
    """Safely re-dispatch/recreate only a failed AHP application intent.

    The frozen hierarchy is revalidated under the proposal lock.  A completed
    bound run is terminal evidence and can never be retried; an existing bound
    run is reused rather than creating a second config/version/run.
    """
    if not actor_is_ceo or not actor_subject:
        raise GovernanceError("CEO_APPROVAL_REQUIRED", "Chỉ CEO xác thực mới được khôi phục áp dụng AHP")
    normalized_reason = reason.strip()
    if len(normalized_reason) < 10:
        raise GovernanceError("RECOVERY_REASON_REQUIRED", "Cần nêu lý do khôi phục bằng tiếng Việt (ít nhất 10 ký tự)")
    if not get_settings().hierarchical_ranking_enabled:
        raise GovernanceError("HIERARCHICAL_RANKING_DISABLED", "Không thể khôi phục khi hierarchical_ranking_enabled=false")

    dispatch: tuple[uuid.UUID, uuid.UUID, str] | None = None
    async with get_session_factory()() as session:
        proposal = await _require_proposal_for_update(session, proposal_id, ("approved",))
        if proposal["proposal_type"] != "ahp_ranking_proposal":
            raise GovernanceError("PROPOSAL_TYPE_INVALID", "Đây không phải đề xuất AHP")
        if proposal["ahp_application_status"] not in ("failed", "awaiting_prior_run", "queued"):
            raise GovernanceError("AHP_APPLICATION_NOT_RECOVERABLE", "Đề xuất không ở trạng thái có thể khôi phục")
        snapshot = proposal.get("proposed_hierarchy_snapshot")
        if not snapshot or not snapshot.get("hierarchical_weights") or not snapshot.get("frozen_at"):
            raise GovernanceError("AHP_HIERARCHY_REQUIRED", "Không có hierarchy AHP đã đóng băng để khôi phục")
        await _revalidate_submitted_proposal_for_review(session, proposal)

        run = None
        if proposal["applied_ranking_run_id"] is not None:
            run = (
                await session.execute(
                    sa.select(ranking_runs)
                    .where(ranking_runs.c.id == proposal["applied_ranking_run_id"])
                    .with_for_update()
                )
            ).mappings().first()
            if run is None:
                raise GovernanceError("AHP_BOUND_RUN_MISSING", "Không tìm thấy ranking run đã gắn với đề xuất")
            if run["status"] == "completed":
                raise GovernanceError("AHP_APPLICATION_ALREADY_COMPLETED", "Ranking run đã hoàn tất; không thể chạy lại qua recovery")

        if run is None:
            # Earlier transactional failure left approval but no durable
            # config/run.  Re-enter the normal apply service after preserving
            # an immutable recovery audit event.
            next_status = "pending"
        elif run["status"] == "deferred":
            next_status = "awaiting_prior_run"
        elif run["status"] == "running":
            next_status = "running"
        elif run["status"] == "failed":
            # Reuse this exact bound run.  A new config/run would break the
            # immutable proposal-to-run provenance established at approval.
            await session.execute(
                sa.update(ranking_runs)
                .where(ranking_runs.c.id == run["id"])
                .values(status="queued", finished_at=None, error_summary={"code": "AHP_APPLICATION_RETRY", "reason": normalized_reason})
            )
            next_status = "queued"
            dispatch = (proposal["project_id"], run["id"], run["trigger"])
        elif run["status"] == "queued" and (run.get("error_summary") or {}).get("code") == "RQ_DISPATCH_FAILED":
            # Only retry a queued run after the dispatcher itself recorded a
            # failed enqueue.  Re-dispatching an otherwise healthy queued
            # intent could create a duplicate RQ job.
            await session.execute(
                sa.update(ranking_runs)
                .where(ranking_runs.c.id == run["id"], ranking_runs.c.status == "queued")
                .values(error_summary={"code": "AHP_APPLICATION_RETRY", "reason": normalized_reason})
            )
            next_status = "queued"
            dispatch = (proposal["project_id"], run["id"], run["trigger"])
        else:
            raise GovernanceError(
                "AHP_APPLICATION_NOT_RECOVERABLE",
                "Ranking run đang hoạt động hoặc chưa có lỗi dispatch có thể xác minh",
            )

        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal_id)
            .values(ahp_application_status=next_status, updated_at=_now())
        )
        await _record_audit_event(
            session,
            ranking_config_id=proposal["proposed_config_id"],
            proposal_id=proposal_id,
            actor_expert_id=actor_expert_id,
            actor_identity_subject=actor_subject,
            event_type="ahp_application_retry_requested",
            before_status=proposal["ahp_application_status"],
            after_status=next_status,
            after_state={"reason": normalized_reason, "run_id": str(run["id"]) if run else None},
        )
        await session.commit()

    if dispatch is not None:
        from src.ranking.service import dispatch_persisted_ranking_run

        await dispatch_persisted_ranking_run(project_id=dispatch[0], run_id=dispatch[1], trigger=dispatch[2])
        return await get_proposal(proposal_id)  # obtains persisted dispatch state without fabricating success
    if run is None:
        return await _apply_ahp_proposal(proposal_id, reviewer_expert_id=actor_expert_id)
    return await get_proposal(proposal_id)


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

        evidence_link_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ranking_evidence_document_features)
            .where(ranking_evidence_document_features.c.feature_justification_id == feature_justification_id)
        )
        if not evidence_link_count:
            await session.rollback()
            raise GovernanceError("EVIDENCE_MISSING", "Assertion đã duyệt nhưng không còn evidence nào liên kết")
        ready_evidence_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(
                ranking_evidence_document_features.join(
                    ranking_evidence_documents,
                    ranking_evidence_documents.c.id == ranking_evidence_document_features.c.document_id,
                )
            )
            .where(
                ranking_evidence_document_features.c.feature_justification_id == feature_justification_id,
                evidence_extraction.document_is_ready(ranking_evidence_documents.c.id),
            )
        )
        if not ready_evidence_count:
            await session.rollback()
            raise GovernanceError(
                "EVIDENCE_NOT_READY",
                "Assertion có historical evidence nhưng không có document lifecycle-ready để materialize",
            )

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
        try:
            _validate_categorical_vocabulary(justification, feature)
        except GovernanceError:
            await session.rollback()
            raise
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
