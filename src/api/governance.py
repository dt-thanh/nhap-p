"""Expert weight-governance routes (0033/0034 service layer, P5 — audit 2026-08-25).

Wires the schema `0033`/`0034` already shipped (tables, state-machine
`CHECK` constraints, append-only triggers) to HTTP. Before this file,
`grep "weight_proposal|justification|expert" src/api/*.py` returned nothing —
`ranking_consultant.md` §21.1 records that gap and names this file as its
resolution.

Never writes `ranking_configs` (that stays `src/services/ranking_config.py`,
its sole declared writer per `tests/test_ranking_boundary.py`) and never
writes `ranking_scores`/`ranking_runs`. See `src/services/governance.py` for
the full write-boundary rationale.

Role gating follows the existing precedent in `src/api/ranking.py`/`ahp.py`:
`viewer` for reads and self-service expert registration, the narrow
`require_governance_authoring` policy for verified CRM.ADVISOR authoring (plus
existing operator/admin compatibility), and `admin` for the higher-trust
actions that touch config linkage, review decisions, and publication.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from src.agents import advisory_tools
from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import (
    AdvisorAhpDraftIn,
    AdvisorAhpDraftOut,
    AdvisorAhpProposalCreateIn,
    AdvisorAnalysisReviewDetailOut,
    AdvisorAnalysisReviewEvidenceOut,
    AdvisorAnalysisReviewJustificationOut,
    AdvisorAnalysisReviewQueueItemOut,
    AdvisorAnalysisReviewQueuePageOut,
    AhpApplicationRetryIn,
    AhpPackageLevelSummaryOut,
    AhpPackageSummaryOut,
    AhpProposalRationaleOut,
    AuditEventOut,
    DocumentLifecycleActionIn,
    DocumentLifecycleOut,
    EvidenceChunkOut,
    EvidenceDocumentOut,
    EvidenceDocumentRegisterIn,
    EvidenceExtractionOut,
    EvidenceLinkIn,
    EvidenceUploadOut,
    ExpertAnalysisOverviewOut,
    ExpertAnswerOut,
    ExpertCitationOut,
    ExpertProfileIn,
    ExpertProfileOut,
    ExpertQuestionIn,
    FeatureDefinitionOut,
    FeatureRubricIn,
    FeatureRubricOut,
    JustificationIn,
    JustificationOut,
    ProposalCreateIn,
    ProposalEvidenceLinkIn,
    ProposalOut,
    ProposalSetConfigIn,
    RankingRunReconcileIn,
    RankingRunReconcileOut,
    RankingV3CoverageOut,
    ReviewIn,
    ReviewOut,
    RubricBandOut,
)
from src.models.tables import projects, ranking_runs
from src.services import evidence_extraction, evidence_upload, governance, ranking_run_recovery
from src.services.dashboard_auth import (
    DashboardPrincipal,
    require_advisor_analysis_ahp_authoring,
    require_advisor_analysis_authoring,
    require_advisor_analysis_read,
    require_advisor_analysis_reviewer_visibility,
    require_project_in_scope,
    require_role,
    require_verified_ceo_analysis_review,
)
from src.task_queue import INGEST_QUEUE, get_queue

router = APIRouter(prefix="/governance", tags=["governance"])
require_admin = require_role("admin")
require_advisor_read = require_advisor_analysis_read()
require_advisor_authoring = require_advisor_analysis_authoring()
# Deliberately its OWN dependency instance, never reused for the routes
# above — mission requirement: the AHP surface must never widen the
# pre-existing qualitative-only `require_advisor_authoring` gate.
require_advisor_ahp_authoring = require_advisor_analysis_ahp_authoring()
require_reviewer_visibility = require_advisor_analysis_reviewer_visibility()
require_ceo_review = require_verified_ceo_analysis_review()
log = get_logger("src.api.governance")


def _is_advisor(principal: DashboardPrincipal) -> bool:
    # Every module route is already gated; retain the predicate only for
    # service-level ownership flags.
    return principal.role == "business_viewer" and "CRM.ADVISOR" in principal.oidc_roles


def _fail(exc: governance.GovernanceError) -> HTTPException:
    not_found = exc.code.endswith("_NOT_FOUND")
    forbidden = exc.code in ("CEO_APPROVAL_REQUIRED", "SELF_APPROVAL_FORBIDDEN", "GRAIN_NOT_ASSERTABLE")
    conflict = exc.code in (
        "ALREADY_REVIEWED",
        "DUPLICATE_OBJECT_STORAGE_KEY",
        "PROPOSAL_STATUS_INVALID",
        "EVIDENCE_LOCKED",
        "VALUE_MATERIALIZATION_DEFERRED_TO_PR3",
        "DOCUMENT_ALREADY_ARCHIVED",
        "DOCUMENT_ALREADY_DELETED",
        "DOCUMENT_NOT_ARCHIVED",
        "DOCUMENT_NOT_ACTIVE",
    )
    status = 404 if not_found else 403 if forbidden else 409 if conflict else 422
    return HTTPException(status_code=status, detail={"message": exc.message, "error_code": exc.code})


def _extraction_fail(exc: evidence_extraction.ExtractionError) -> HTTPException:
    not_found = exc.code.endswith("_NOT_FOUND")
    conflict = exc.code == "CHUNKS_ALREADY_EXIST"
    status = 404 if not_found else 409 if conflict else 422
    return HTTPException(status_code=status, detail={"message": exc.message, "error_code": exc.code})


def _uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"message": f"{field} không phải UUID hợp lệ", "error_code": "INVALID_UUID"}
        ) from exc


def _decimal(value: str, field: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=422, detail={"message": f"{field} không phải số hợp lệ", "error_code": "INVALID_DECIMAL"}
        ) from exc


async def _resolve_expert_id(principal: DashboardPrincipal) -> uuid.UUID:
    """D18 close-out: the acting expert's identity is ALWAYS derived from the
    authenticated principal's verified OIDC `subject` — never from a
    client-supplied `*_expert_id`/`identity_subject` field, for EITHER
    assertion kind. Fails closed (422) for static-token/dev-bypass auth,
    exactly like the CEO gate already does (`dashboard_auth.require_ceo`) —
    neither of those two auth paths carries a real per-person identity by
    design (`DashboardPrincipal`'s own docstring), so there is nothing safe
    to derive an actor identity from."""
    if not principal.subject:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Thao tác này cần danh tính cá nhân đã xác thực (OIDC) — "
                "token tĩnh/dev-bypass không mang danh tính từng người.",
                "error_code": "IDENTITY_REQUIRED",
            },
        )
    expert = await governance.get_or_create_expert_profile(identity_subject=principal.subject)
    return uuid.UUID(str(expert["id"]))


async def _require_proposal_access(
    proposal_id: uuid.UUID, principal: DashboardPrincipal, *, owner_only: bool = False
) -> dict:
    """Resolve project scope and (for Advisors) hide non-owned proposals."""
    try:
        proposal = await governance.get_proposal(proposal_id)
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    require_project_in_scope(principal, await _resolve_project_external_id(uuid.UUID(str(proposal["project_id"]))))
    if _is_advisor(principal):
        owner_id = await governance.find_expert_profile_id(identity_subject=principal.subject or "")
        if owner_id is None or owner_id != proposal["created_by_expert_id"]:
            raise HTTPException(
                status_code=404,
                detail={"message": "Không tìm thấy đề xuất trong phạm vi của bạn", "error_code": "PROPOSAL_NOT_FOUND"},
            )
    return proposal


def _expert_out(row: dict) -> ExpertProfileOut:
    return ExpertProfileOut(
        id=str(row["id"]),
        identity_subject=row["identity_subject"],
        organization=row["organization"],
        title=row["title"],
        expertise_summary=row["expertise_summary"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _proposal_out(row: dict) -> ProposalOut:
    return ProposalOut(
        id=str(row["id"]),
        base_config_id=str(row["base_config_id"]) if row["base_config_id"] else None,
        proposed_config_id=str(row["proposed_config_id"]) if row["proposed_config_id"] else None,
        scope_type=row["scope_type"],
        project_id=str(row["project_id"]),
        area_id=str(row["area_id"]) if row["area_id"] else None,
        status=row["status"],
        created_by_expert_id=str(row["created_by_expert_id"]),
        submitted_at=row["submitted_at"],
        approved_at=row["approved_at"],
        published_at=row["published_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        assertion_kind=row["assertion_kind"],
        proposal_type=row.get("proposal_type", "qualitative_analysis"),
        ahp_application_status=row.get("ahp_application_status"),
        applied_ranking_run_id=(
            str(row["applied_ranking_run_id"]) if row.get("applied_ranking_run_id") else None
        ),
    )


def _justification_out(row: dict) -> JustificationOut:
    return JustificationOut(
        id=str(row["id"]),
        proposal_id=str(row["proposal_id"]),
        feature_definition_id=str(row["feature_definition_id"]),
        previous_weight=str(row["previous_weight"]) if row["previous_weight"] is not None else None,
        proposed_weight=str(row["proposed_weight"]) if row["proposed_weight"] is not None else None,
        rationale=row["rationale"],
        methodology=row["methodology"],
        evidence_summary=row["evidence_summary"],
        expected_effect=row["expected_effect"],
        confidence=row["confidence"],
        limitations=row["limitations"],
        created_by_expert_id=str(row["created_by_expert_id"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        assertion_kind=row["assertion_kind"],
        raw_value=str(row["raw_numeric"]) if row.get("raw_numeric") is not None else None,
        normalized_value=str(row["normalized_numeric"]) if row.get("normalized_numeric") is not None else None,
        categorical_value=row.get("categorical_value"),
        effective_at=row.get("effective_at"),
        expires_at=row.get("expires_at"),
        external_source_citation=row.get("external_source_citation"),
        rubric_id=str(row["rubric_id"]) if row.get("rubric_id") is not None else None,
        rubric_band_value=str(row["rubric_band_value"]) if row.get("rubric_band_value") is not None else None,
    )


def _rubric_out(row: dict) -> FeatureRubricOut:
    return FeatureRubricOut(
        id=str(row["id"]),
        feature_definition_id=str(row["feature_definition_id"]),
        rubric_version=row["rubric_version"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        bands=[
            RubricBandOut(
                id=str(band["id"]),
                band_value=str(band["band_value"]),
                label=band["label"],
                evidence_requirement=band["evidence_requirement"],
                display_order=band["display_order"],
            )
            for band in row["bands"]
        ],
    )


def _chunk_out(row: dict) -> EvidenceChunkOut:
    return EvidenceChunkOut(
        id=str(row["id"]),
        document_id=str(row["document_id"]),
        chunk_index=row["chunk_index"],
        page_number=row["page_number"],
        content=row["content"],
        token_count=row["token_count"],
        embedding_model=row["embedding_model"],
        created_at=row["created_at"],
    )


async def _document_out(row: dict) -> EvidenceDocumentOut:
    """Expose effective readiness, retaining immutable registration metadata for audit."""
    readiness = await evidence_extraction.get_document_readiness(uuid.UUID(str(row["id"])))
    # A row returned by these routes exists, but preserve a safe fallback for
    # a concurrently removed test fixture/record rather than reporting it as
    # retrieval-ready.
    effective_status = readiness.extraction_status if readiness is not None else "not_requested"
    lifecycle_status = readiness.lifecycle_status if readiness is not None else row.get("lifecycle_status", "active")
    return EvidenceDocumentOut(
        id=str(row["id"]),
        project_id=str(row["project_id"]) if row.get("project_id") else None,
        area_id=str(row["area_id"]) if row.get("area_id") else None,
        proposal_id=str(row["proposal_id"]) if row["proposal_id"] else None,
        uploaded_by_expert_id=str(row["uploaded_by_expert_id"]),
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        object_storage_key=row["object_storage_key"],
        sha256_checksum=row["sha256_checksum"],
        file_size_bytes=row["file_size_bytes"],
        extraction_status=effective_status,
        registration_extraction_status=row["extraction_status"],
        created_at=row["created_at"],
        # Only `governance.list_documents()` computes this field today (the
        # management-listing view); other call sites (register/upload,
        # find_document_by_checksum) hand back a freshly-inserted or
        # freshly-looked-up row that predates any lifecycle event, so
        # defaulting to "active" here is correct, not a guess.
        lifecycle_status=lifecycle_status,
        chunk_count=readiness.chunk_count if readiness is not None else 0,
        embedded_chunk_count=readiness.embedded_chunk_count if readiness is not None else 0,
        error_code=readiness.error_code if readiness is not None else None,
        # Only expose summaries written under the bounded error-code contract;
        # legacy rows may contain unstructured historical text.
        error_summary=(readiness.error_summary if readiness is not None and readiness.error_code else None),
    )


def _review_out(row: dict) -> ReviewOut:
    return ReviewOut(
        id=str(row["id"]),
        proposal_id=str(row["proposal_id"]),
        reviewer_expert_id=str(row["reviewer_expert_id"]),
        decision=row["decision"],
        comment=row["comment"],
        decided_at=row["decided_at"],
        evidence_review_acknowledged=row.get("evidence_review_acknowledged"),
    )


# --- Chuyên gia ----------------------------------------------------------------


@router.post(
    "/experts",
    response_model=ExpertProfileOut,
    summary="Tự đăng ký / lấy lại hồ sơ chuyên gia của CHÍNH principal đã xác thực",
)
async def register_expert(
    payload: ExpertProfileIn, principal: DashboardPrincipal = Depends(require_advisor_authoring)
) -> ExpertProfileOut:
    """D18: `identity_subject` không còn là trường request body — luôn suy từ
    `principal.subject` (OIDC đã xác thực). Một caller không thể tự đăng ký
    hồ sơ MANG DANH người khác chỉ bằng cách gõ một chuỗi bất kỳ."""
    if not principal.subject:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Đăng ký hồ sơ chuyên gia cần danh tính cá nhân đã xác thực (OIDC) — "
                "token tĩnh/dev-bypass không mang danh tính từng người.",
                "error_code": "IDENTITY_REQUIRED",
            },
        )
    try:
        row = await governance.get_or_create_expert_profile(
            identity_subject=principal.subject,
            organization=payload.organization,
            title=payload.title,
            expertise_summary=payload.expertise_summary,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _expert_out(row)


@router.get("/experts/{expert_id}", response_model=ExpertProfileOut)
async def get_expert(expert_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)) -> ExpertProfileOut:
    expert_uuid = _uuid(expert_id, "expert_id")
    if _is_advisor(principal):
        own_id = await governance.find_expert_profile_id(identity_subject=principal.subject or "")
        if own_id is None or own_id != expert_uuid:
            raise HTTPException(status_code=404, detail={"message": "Không tìm thấy hồ sơ", "error_code": "EXPERT_NOT_FOUND"})
    try:
        row = await governance.get_expert_profile(expert_uuid)
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _expert_out(row)


# --- Đề xuất ---------------------------------------------------------------------


@router.post(
    "/proposals",
    response_model=ProposalOut,
    status_code=201,
    summary="Tạo đề xuất trọng số (weight) hoặc value assertion (value, PR-2) — trạng thái draft",
)
async def create_proposal(
    payload: ProposalCreateIn, principal: DashboardPrincipal = Depends(require_advisor_authoring)
) -> ProposalOut:
    # D18: authorship always derives from the authenticated principal, for
    # BOTH assertion kinds — no request-body identity field exists anymore.
    created_by_expert_id = await _resolve_expert_id(principal)
    project_uuid = _uuid(payload.project_id, "project_id")
    require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))

    try:
        row = await governance.create_proposal(
            base_config_id=_uuid(payload.base_config_id, "base_config_id") if payload.base_config_id else None,
            project_id=project_uuid,
            created_by_expert_id=created_by_expert_id,
            assertion_kind=payload.assertion_kind,
            scope_type=payload.scope_type,
            area_id=_uuid(payload.area_id, "area_id") if payload.area_id else None,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.get("/proposals", response_model=list[ProposalOut])
async def list_proposals(
    project_id: str | None = None,
    status: str | None = None,
    principal: DashboardPrincipal = Depends(require_advisor_read),
) -> list[ProposalOut]:
    owner_id = None
    if _is_advisor(principal):
        if not project_id:
            raise HTTPException(status_code=422, detail={"message": "Cần project_id", "error_code": "SCOPE_REQUIRED"})
        owner_id = await governance.find_expert_profile_id(identity_subject=principal.subject or "")
        if owner_id is None:
            return []
        project_uuid = _uuid(project_id, "project_id")
        require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    elif project_id:
        project_uuid = _uuid(project_id, "project_id")
        require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    rows = await governance.list_proposals(
        project_id=_uuid(project_id, "project_id") if project_id else None, status=status,
        created_by_expert_id=owner_id,
    )
    return [_proposal_out(row) for row in rows]


@router.get("/proposals/{proposal_id}", response_model=ProposalOut)
async def get_proposal(proposal_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)) -> ProposalOut:
    try:
        row = await _require_proposal_access(_uuid(proposal_id, "proposal_id"), principal)
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.patch(
    "/proposals/{proposal_id}/config",
    response_model=ProposalOut,
    summary="Gắn một ranking_configs draft đã tồn tại vào đề xuất (không tạo config)",
)
async def set_proposal_config(
    proposal_id: str, payload: ProposalSetConfigIn, principal: DashboardPrincipal = Depends(require_admin)
) -> ProposalOut:
    actor_expert_id = await _resolve_expert_id(principal)
    try:
        row = await governance.set_proposed_config(
            proposal_id=_uuid(proposal_id, "proposal_id"),
            proposed_config_id=_uuid(payload.proposed_config_id, "proposed_config_id"),
            actor_expert_id=actor_expert_id,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.post(
    "/advisor-analysis/ahp-proposals",
    response_model=ProposalOut,
    status_code=201,
    summary="0049 — Tạo đề xuất trọng số AHP (ahp_ranking_proposal), trạng thái draft",
)
async def create_ahp_proposal(
    payload: AdvisorAhpProposalCreateIn, principal: DashboardPrincipal = Depends(require_advisor_ahp_authoring)
) -> ProposalOut:
    # Distinct route/capability from `create_proposal` above (mission: never
    # widen the existing qualitative-only Advisor Analysis flow). `base_config_id`
    # is never accepted here — `governance.create_proposal` resolves the
    # currently published config server-side for this proposal_type.
    created_by_expert_id = await _resolve_expert_id(principal)
    project_uuid = _uuid(payload.project_id, "project_id")
    require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    try:
        row = await governance.create_proposal(
            project_id=project_uuid,
            created_by_expert_id=created_by_expert_id,
            proposal_type="ahp_ranking_proposal",
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.patch(
    "/advisor-analysis/ahp-proposals/{proposal_id}/hierarchy",
    response_model=AdvisorAhpDraftOut,
    summary="0049 — Lưu bản nháp hierarchy AHP (chưa nộp, chưa ảnh hưởng ranking)",
)
async def save_ahp_proposal_hierarchy(
    proposal_id: str,
    payload: AdvisorAhpDraftIn,
    principal: DashboardPrincipal = Depends(require_advisor_ahp_authoring),
) -> AdvisorAhpDraftOut:
    # `save_ahp_proposal_draft` itself re-checks ownership/status/proposal_type
    # and re-validates fully every call (registry membership, CI/CR,
    # ENRICHMENT_SOURCED_FEATURE_KEYS guard) — this route only resolves the
    # actor and scope, never trusts the client for anything else (D18).
    actor_expert_id = await _resolve_expert_id(principal)
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal, owner_only=True)
    if payload.mode not in ("direct", "pairwise"):
        raise HTTPException(
            status_code=422, detail={"message": "mode phải thuộc direct|pairwise", "error_code": "AHP_MODE_INVALID"}
        )
    try:
        row = await governance.save_ahp_proposal_draft(
            proposal_id=proposal_uuid,
            actor_expert_id=actor_expert_id,
            mode=payload.mode,
            direct_hierarchical_weights=payload.direct_hierarchical_weights,
            pairwise_input=payload.pairwise_input,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return AdvisorAhpDraftOut(
        **_proposal_out(row).model_dump(), proposed_hierarchy_snapshot=row.get("proposed_hierarchy_snapshot")
    )


@router.get(
    "/advisor-analysis/ahp-proposals/{proposal_id}/rationale",
    response_model=list[AhpProposalRationaleOut],
    summary="Truy hồi giải thích tiêu chí của đề xuất AHP đã nộp",
)
async def get_ahp_proposal_rationale(
    proposal_id: str,
    criterion_key: str | None = Query(default=None, min_length=1),
    query: str | None = Query(default=None, min_length=1),
    top_k: int = Query(default=5, ge=1, le=10),
    principal: DashboardPrincipal = Depends(require_advisor_ahp_authoring),
) -> list[AhpProposalRationaleOut]:
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal, owner_only=True)
    try:
        rows = await governance.get_ahp_proposal_rationale(
            proposal_uuid, criterion_key=criterion_key, query=query, top_k=top_k
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return [AhpProposalRationaleOut(**row) for row in rows]


@router.post(
    "/advisor-analysis/ahp-proposals/{proposal_id}/link-evidence",
    response_model=EvidenceDocumentOut,
    status_code=201,
    summary="Gắn một evidence lifecycle-ready hiện có vào bản nháp AHP",
)
async def link_ahp_proposal_evidence(
    proposal_id: str,
    payload: ProposalEvidenceLinkIn,
    principal: DashboardPrincipal = Depends(require_advisor_ahp_authoring),
) -> EvidenceDocumentOut:
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal, owner_only=True)
    actor_expert_id = await _resolve_expert_id(principal)
    try:
        document = await governance.link_evidence_to_ahp_proposal(
            proposal_id=proposal_uuid,
            document_id=_uuid(payload.document_id, "document_id"),
            actor_expert_id=actor_expert_id,
            enforce_owner=_is_advisor(principal),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return await _document_out(document)


@router.get(
    "/advisor-analysis/ahp-proposals/{proposal_id}/linked-evidence",
    response_model=list[EvidenceDocumentOut],
    summary="Liệt kê evidence đã gắn trực tiếp vào đề xuất AHP của chính Advisor",
)
async def list_ahp_proposal_evidence(
    proposal_id: str, principal: DashboardPrincipal = Depends(require_advisor_ahp_authoring)
) -> list[EvidenceDocumentOut]:
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal, owner_only=True)
    rows = await governance.list_linked_evidence_for_ahp_proposal(proposal_uuid)
    return [await _document_out(row) for row in rows]


@router.post("/proposals/{proposal_id}/submit", response_model=ProposalOut, summary="draft → submitted")
async def submit_proposal(
    proposal_id: str, principal: DashboardPrincipal = Depends(require_advisor_authoring)
) -> ProposalOut:
    actor_expert_id = await _resolve_expert_id(principal)
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal, owner_only=True)
    try:
        row = await governance.submit_proposal(
            proposal_id=proposal_uuid,
            actor_expert_id=actor_expert_id,
            enforce_owner=_is_advisor(principal),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.post(
    "/proposals/{proposal_id}/withdraw",
    response_model=ProposalOut,
    summary="draft/submitted/under_review → withdrawn (chốt)",
)
async def withdraw_proposal(
    proposal_id: str, principal: DashboardPrincipal = Depends(require_advisor_authoring)
) -> ProposalOut:
    actor_expert_id = await _resolve_expert_id(principal)
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal, owner_only=True)
    try:
        row = await governance.withdraw_proposal(
            proposal_id=proposal_uuid,
            actor_expert_id=actor_expert_id,
            enforce_owner=_is_advisor(principal),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.post(
    "/proposals/{proposal_id}/publish",
    response_model=ProposalOut,
    summary="CEO công bố một value proposal đã được duyệt (không công bố AHP config)",
)
async def publish_proposal(
    proposal_id: str, principal: DashboardPrincipal = Depends(require_ceo_review)
) -> ProposalOut:
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    proposal = await _require_proposal_access(proposal_uuid, principal)
    # AHP proposals publish through their separate approval/application path;
    # this endpoint is intentionally limited to qualitative value assertions.
    if proposal["assertion_kind"] != "value" or proposal.get("proposal_type", "qualitative_analysis") == "ahp_ranking_proposal":
        raise HTTPException(status_code=403, detail={"message": "Chỉ value proposal đã duyệt mới được công bố.", "error_code": "VALUE_PUBLICATION_ONLY"})
    if proposal["status"] != "approved":
        raise HTTPException(status_code=409, detail={"message": "Đề xuất phải ở trạng thái approved trước khi công bố.", "error_code": "PROPOSAL_STATUS_INVALID"})
    try:
        row = await governance.mark_published(
            proposal_id=proposal_uuid,
            actor_expert_id=await _resolve_expert_id(principal),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


# --- Justification ---------------------------------------------------------------


@router.post(
    "/proposals/{proposal_id}/justifications",
    response_model=JustificationOut,
    summary="Tạo hoặc sửa justification cho một feature (chỉ khi đề xuất còn draft)",
)
async def upsert_justification(
    proposal_id: str, payload: JustificationIn, principal: DashboardPrincipal = Depends(require_advisor_authoring)
) -> JustificationOut:
    # D18: authorship always derives from the authenticated principal, for
    # BOTH assertion kinds. `author_subject` (a value-mode-only metadata
    # column — `governance.upsert_justification` rejects it as non-null for
    # weight-mode) is still populated only when assertion_kind == "value".
    created_by_expert_id = await _resolve_expert_id(principal)
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal, owner_only=True)
    author_subject = principal.subject if payload.assertion_kind == "value" else None

    try:
        row = await governance.upsert_justification(
            proposal_id=proposal_uuid,
            feature_definition_id=_uuid(payload.feature_definition_id, "feature_definition_id"),
            previous_weight=_decimal(payload.previous_weight, "previous_weight") if payload.previous_weight else None,
            proposed_weight=_decimal(payload.proposed_weight, "proposed_weight") if payload.proposed_weight else None,
            rationale=payload.rationale,
            methodology=payload.methodology,
            evidence_summary=payload.evidence_summary,
            expected_effect=payload.expected_effect,
            confidence=payload.confidence,
            limitations=payload.limitations,
            created_by_expert_id=created_by_expert_id,
            assertion_kind=payload.assertion_kind,
            raw_numeric=_decimal(payload.raw_value, "raw_value") if payload.raw_value else None,
            normalized_numeric=_decimal(payload.normalized_value, "normalized_value")
            if payload.normalized_value
            else None,
            categorical_value=payload.categorical_value,
            effective_at=payload.effective_at,
            expires_at=payload.expires_at,
            external_source_citation=payload.external_source_citation,
            author_subject=author_subject,
            rubric_id=_uuid(payload.rubric_id, "rubric_id") if payload.rubric_id else None,
            rubric_band_value=_decimal(payload.rubric_band_value, "rubric_band_value")
            if payload.rubric_band_value
            else None,
            enforce_owner=_is_advisor(principal),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _justification_out(row)


@router.get("/proposals/{proposal_id}/justifications", response_model=list[JustificationOut])
async def list_justifications(
    proposal_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)
) -> list[JustificationOut]:
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal)
    rows = await governance.list_justifications(proposal_uuid)
    return [_justification_out(row) for row in rows]


@router.get(
    "/feature-definitions",
    response_model=list[FeatureDefinitionOut],
    summary="Danh mục đặc trưng ĐANG HOẠT ĐỘNG (đọc — để soạn assertion/rubric cần feature_definition_id)",
)
async def list_feature_definitions(
    grain: str | None = Query(None), principal: DashboardPrincipal = Depends(require_advisor_read)
) -> list[FeatureDefinitionOut]:
    # This is an Advisor-safe criteria inventory, not the global feature
    # registry.  Only canonical qualitative rubric criteria are disclosed.
    rows = [
        row
        for row in await governance.list_feature_definitions(grain=grain)
        if row["feature_key"] in governance.RUBRIC_REQUIRED_FEATURE_KEYS
    ]
    return [
        FeatureDefinitionOut(
            id=str(row["id"]),
            feature_key=row["feature_key"],
            name=row["name"],
            category=row["category"],
            grain=row["grain"],
            value_type=row["value_type"],
            direction=row["direction"],
            missing_policy=row["missing_policy"],
        )
        for row in rows
    ]


# --- Rubric (0046) -------------------------------------------------------------


@router.post(
    "/feature-rubrics",
    response_model=FeatureRubricOut,
    status_code=201,
    summary="Tạo một PHIÊN BẢN rubric mới cho một đặc trưng định tính (append-only)",
)
async def create_feature_rubric(
    payload: FeatureRubricIn, principal: DashboardPrincipal = Depends(require_admin)
) -> FeatureRubricOut:
    """Ai được duy trì rubric: `admin` — cùng ngưỡng với soạn/publish config,
    vì một rubric sai lệch có thể làm sai LỊCH SỬ chấm điểm định tính. Người
    tạo luôn là principal đã xác thực (D18), không bao giờ đọc từ body."""
    created_by = principal.subject or f"static-token:{principal.role}"
    try:
        row = await governance.create_feature_rubric(
            feature_definition_id=_uuid(payload.feature_definition_id, "feature_definition_id"),
            bands=[band.model_dump() for band in payload.bands],
            created_by=created_by,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _rubric_out(row)


@router.get(
    "/feature-rubrics",
    response_model=list[FeatureRubricOut],
    summary="Toàn bộ lịch sử phiên bản rubric của một đặc trưng (cũ nhất trước)",
)
async def list_feature_rubrics(
    feature_definition_id: str = Query(...), principal: DashboardPrincipal = Depends(require_advisor_read)
) -> list[FeatureRubricOut]:
    definition_id = _uuid(feature_definition_id, "feature_definition_id")
    allowed = {
        row["id"]
        for row in await governance.list_feature_definitions()
        if row["feature_key"] in governance.RUBRIC_REQUIRED_FEATURE_KEYS
    }
    if definition_id not in allowed:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy tiêu chí trong phạm vi", "error_code": "FEATURE_NOT_FOUND"})
    rows = await governance.list_feature_rubrics(definition_id)
    return [_rubric_out(row) for row in rows]


@router.get(
    "/feature-rubrics/current",
    response_model=FeatureRubricOut | None,
    summary="Phiên bản rubric HIỆN HÀNH (rubric_version cao nhất) của một đặc trưng, nếu có",
)
async def get_current_feature_rubric(
    feature_definition_id: str = Query(...), principal: DashboardPrincipal = Depends(require_advisor_read)
) -> FeatureRubricOut | None:
    definition_id = _uuid(feature_definition_id, "feature_definition_id")
    allowed = {
        row["id"]
        for row in await governance.list_feature_definitions()
        if row["feature_key"] in governance.RUBRIC_REQUIRED_FEATURE_KEYS
    }
    if definition_id not in allowed:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy tiêu chí trong phạm vi", "error_code": "FEATURE_NOT_FOUND"})
    row = await governance.get_current_feature_rubric(definition_id)
    return _rubric_out(row) if row is not None else None


# --- Bằng chứng --------------------------------------------------------------------


@router.post(
    "/evidence",
    response_model=EvidenceDocumentOut,
    status_code=201,
    summary="Ghi hàng siêu dữ liệu cho một file ĐÃ nằm trên storage (không nhận multipart)",
)
async def register_evidence(
    payload: EvidenceDocumentRegisterIn, principal: DashboardPrincipal = Depends(require_advisor_authoring)
) -> EvidenceDocumentOut:
    uploaded_by_expert_id = await _resolve_expert_id(principal)
    project_uuid = _uuid(payload.project_id, "project_id") if payload.project_id else None
    if project_uuid is not None:
        require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    proposal_uuid = _uuid(payload.proposal_id, "proposal_id") if payload.proposal_id else None
    if proposal_uuid is not None:
        proposal = await _require_proposal_access(proposal_uuid, principal, owner_only=True)
        if project_uuid is not None and uuid.UUID(str(proposal["project_id"])) != project_uuid:
            raise HTTPException(
                status_code=422,
                detail={"message": "Evidence và proposal phải thuộc cùng một dự án", "error_code": "DOCUMENT_PROJECT_MISMATCH"},
            )
    try:
        row = await governance.register_evidence_document(
            project_id=project_uuid,
            area_id=_uuid(payload.area_id, "area_id") if payload.area_id else None,
            proposal_id=proposal_uuid,
            uploaded_by_expert_id=uploaded_by_expert_id,
            original_filename=payload.original_filename,
            mime_type=payload.mime_type,
            object_storage_key=payload.object_storage_key,
            sha256_checksum=payload.sha256_checksum,
            file_size_bytes=payload.file_size_bytes,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return await _document_out(row)


async def _resolve_project_external_id(project_id: uuid.UUID) -> str:
    """Reverse of `ranking.py::_resolve_project` — the governance API deals
    in the internal `project_id` (see `ProposalCreateIn`), but scope checks
    (`require_project_in_scope`) are keyed by external_id, so this looks the
    external_id back up to check the caller's scope against it."""
    async with get_session_factory()() as session:
        external_id = await session.scalar(sa.select(projects.c.external_id).where(projects.c.id == project_id))
    if external_id is None:
        raise HTTPException(
            status_code=404, detail={"message": f"Không tìm thấy dự án {project_id}", "error_code": "PROJECT_NOT_FOUND"}
        )
    return external_id


@router.get(
    "/projects/{project_id}/expert-analysis-overview",
    response_model=ExpertAnalysisOverviewOut,
    summary="Tổng hợp chỉ-đọc trạng thái Evidence/Expert Analysis theo dự án",
)
async def get_expert_analysis_overview(
    project_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)
) -> ExpertAnalysisOverviewOut:
    project_uuid = _uuid(project_id, "project_id")
    require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    return ExpertAnalysisOverviewOut(**(await governance.get_project_expert_analysis_overview(project_uuid)))


@router.get(
    "/projects/{project_id}/ranking-v3-coverage",
    response_model=RankingV3CoverageOut,
    summary="Coverage read-only của các value assertion cần cho Ranking V3",
)
async def get_ranking_v3_coverage(
    project_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)
) -> RankingV3CoverageOut:
    project_uuid = _uuid(project_id, "project_id")
    require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    return RankingV3CoverageOut(**(await governance.get_project_v3_coverage(project_uuid)))


@router.post(
    "/evidence/upload",
    response_model=EvidenceUploadOut,
    summary="Nhận multipart PDF/text/markdown THẬT, lưu xuống storage, rồi ghi hàng metadata",
)
async def upload_evidence(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    area_id: str | None = Form(default=None),
    proposal_id: str | None = Form(default=None),
    principal: DashboardPrincipal = Depends(require_advisor_authoring),
) -> EvidenceUploadOut:
    """The real upload route `POST /evidence` never had (see that route's own
    summary/docstring, and `ranking_consultant.md` §21.1's "known gap").
    Storage-only work (`EvidenceUploadService`) is fully separate from the
    metadata write (`governance.register_evidence_document`, unchanged) —
    same discipline `src/api/files.py` already keeps for Excel/CSV uploads.

    Idempotent on content: identical bytes (same sha256) already stored
    return the EXISTING row (`reused=True`, 200) instead of writing a
    duplicate — the just-uploaded bytes are discarded, not the existing ones.

    D18: `uploaded_by_expert_id` is no longer a form field — it is always
    derived from the authenticated principal's OIDC subject.
    """
    project_uuid = _uuid(project_id, "project_id")
    # Authorize before writing any bytes.  Project ownership is server-checked,
    # never inferred from the file, a proposal, or the uploader.
    require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    area_uuid = _uuid(area_id, "area_id") if area_id else None
    uploaded_by_expert_id = await _resolve_expert_id(principal)
    proposal_uuid = _uuid(proposal_id, "proposal_id") if proposal_id else None
    if proposal_uuid is not None:
        proposal = await _require_proposal_access(proposal_uuid, principal, owner_only=True)
        if uuid.UUID(str(proposal["project_id"])) != project_uuid:
            raise HTTPException(
                status_code=422,
                detail={"message": "Evidence và proposal phải thuộc cùng một dự án", "error_code": "DOCUMENT_PROJECT_MISMATCH"},
            )
    try:
        stored = await evidence_upload.EvidenceUploadService().save(file, file.filename or "")
    except evidence_upload.EvidenceUploadRejectedError as exc:
        status = 413 if exc.error_code == "FILE_TOO_LARGE" else 422
        raise HTTPException(status_code=status, detail={"message": exc.message, "error_code": exc.error_code}) from exc

    existing = await governance.find_document_by_checksum(stored.sha256_checksum)
    existing_project_id = (
        await governance.get_document_project_id(uuid.UUID(str(existing["id"]))) if existing is not None else None
    )
    # Reuse is project-local only.  Identical bytes in another project must not
    # reveal that document or inherit its scope.
    if existing is not None and existing_project_id == project_uuid:
        (Path(get_settings().upload_dir) / stored.object_storage_key).unlink(missing_ok=True)
        return EvidenceUploadOut(**(await _document_out(existing)).model_dump(), reused=True)

    try:
        row = await governance.register_evidence_document(
            project_id=project_uuid,
            area_id=area_uuid,
            proposal_id=proposal_uuid,
            uploaded_by_expert_id=uploaded_by_expert_id,
            original_filename=stored.original_filename,
            mime_type=stored.mime_type,
            object_storage_key=stored.object_storage_key,
            sha256_checksum=stored.sha256_checksum,
            file_size_bytes=stored.file_size_bytes,
        )
    except governance.GovernanceError as exc:
        (Path(get_settings().upload_dir) / stored.object_storage_key).unlink(missing_ok=True)
        raise _fail(exc) from exc
    return EvidenceUploadOut(**(await _document_out(row)).model_dump(), reused=False)


@router.get(
    "/evidence",
    response_model=list[EvidenceDocumentOut],
    summary="Danh sách evidence document theo dự án hoặc theo người tải lên",
)
async def list_evidence_documents(
    project_id: str | None = Query(default=None),
    uploaded_by_expert_id: str | None = Query(default=None),
    principal: DashboardPrincipal = Depends(require_advisor_read),
) -> list[EvidenceDocumentOut]:
    """Exactly one filter required — an unscoped "list every document" query
    is never a supported shape (rule: never retrieve outside the caller's
    allowed scope). `project_id` additionally re-checks `require_project_in_scope`
    (stricter than the pre-existing governance write endpoints, which do not
    scope-check `project_id` at all — a documented, pre-existing gap this
    read endpoint does not inherit)."""
    if bool(project_id) == bool(uploaded_by_expert_id):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Cần đúng một trong hai: project_id hoặc uploaded_by_expert_id",
                "error_code": "SCOPE_REQUIRED",
            },
        )
    project_uuid = None
    if project_id:
        project_uuid = _uuid(project_id, "project_id")
        external_id = await _resolve_project_external_id(project_uuid)
        require_project_in_scope(principal, external_id)

    if uploaded_by_expert_id:
        requested_uploader = _uuid(uploaded_by_expert_id, "uploaded_by_expert_id")
        # Legacy/unscoped rows are visible only to their owner (or an admin
        # conducting an audit), never as a substitute for project scope.
        if principal.role != "admin" and requested_uploader != await _resolve_expert_id(principal):
            raise HTTPException(status_code=403, detail={"message": "Chỉ được xem evidence do chính mình tải lên", "error_code": "EVIDENCE_OWNER_REQUIRED"})
    rows = await governance.list_documents(
        project_id=project_uuid,
        uploaded_by_expert_id=requested_uploader if uploaded_by_expert_id else None,
    )
    return [await _document_out(row) for row in rows]


@router.post("/evidence/link", status_code=204, summary="Liên kết một document tới một justification")
async def link_evidence(payload: EvidenceLinkIn, principal: DashboardPrincipal = Depends(require_advisor_authoring)) -> None:
    document_id = _uuid(payload.document_id, "document_id")
    document_project_id = await governance.get_document_project_id(document_id)
    if document_project_id is None:
        raise HTTPException(status_code=422, detail={"message": "Evidence lịch sử chưa có project scope", "error_code": "DOCUMENT_PROJECT_UNSCOPED"})
    require_project_in_scope(principal, await _resolve_project_external_id(document_project_id))
    try:
        actor_expert_id = await _resolve_expert_id(principal)
        justification_id = _uuid(payload.feature_justification_id, "feature_justification_id")
        await _require_proposal_access(
            uuid.UUID(str(await governance.get_justification_proposal_id(justification_id))),
            principal,
            owner_only=True,
        )
        await governance.link_evidence_to_justification(
            document_id=document_id,
            feature_justification_id=justification_id,
            actor_expert_id=actor_expert_id,
            enforce_owner=_is_advisor(principal),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc


@router.get("/justifications/{feature_justification_id}/evidence", response_model=list[EvidenceDocumentOut])
async def list_evidence_for_justification(
    feature_justification_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)
) -> list[EvidenceDocumentOut]:
    justification_uuid = _uuid(feature_justification_id, "feature_justification_id")
    try:
        project_uuid = await governance.get_justification_project_id(justification_uuid)
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    require_project_in_scope(principal, await _resolve_project_external_id(project_uuid))
    if _is_advisor(principal):
        proposal_id = await governance.get_justification_proposal_id(justification_uuid)
        await _require_proposal_access(proposal_id, principal)
    rows = await governance.list_documents_for_justification(justification_uuid)
    return [await _document_out(row) for row in rows]


# --- Trích xuất chunk + embedding (0035, §21.5-§21.6) -------------------------------
#
# Trạng thái trả về là `evidence_extraction.latest_extraction_status` — dòng mới
# nhất trong `ranking_evidence_extraction_attempts`. `EvidenceDocumentOut` cũng
# lộ cùng effective status; immutable registration metadata chỉ còn ở trường
# audit `registration_extraction_status`.


@router.post(
    "/evidence/{document_id}/extract",
    response_model=EvidenceExtractionOut,
    summary="Enqueue chunk+embed cho một evidence document đã có sẵn trong storage",
)
async def request_evidence_extraction(
    document_id: str, principal: DashboardPrincipal = Depends(require_advisor_authoring)
) -> EvidenceExtractionOut:
    doc_id = _uuid(document_id, "document_id")
    document_project_id = await governance.get_document_project_id(doc_id)
    if document_project_id is None:
        raise HTTPException(status_code=422, detail={"message": "Evidence lịch sử chưa có project scope", "error_code": "DOCUMENT_PROJECT_UNSCOPED"})
    require_project_in_scope(principal, await _resolve_project_external_id(document_project_id))
    if await governance.latest_lifecycle_status(doc_id) != "active":
        raise HTTPException(
            status_code=409,
            detail={"message": "Tài liệu không còn ở trạng thái hoạt động.", "error_code": "DOCUMENT_NOT_ACTIVE"},
        )
    try:
        previous = await evidence_extraction.latest_extraction_status(doc_id)
        status = await evidence_extraction.request_extraction(doc_id)
    except evidence_extraction.ExtractionError as exc:
        raise _extraction_fail(exc) from exc

    if previous not in ("pending", "succeeded"):
        # Chỉ enqueue khi lần gọi NÀY thật sự tạo attempt 'pending' mới — gọi
        # lại trên một document đang 'pending'/'succeeded' không xếp thêm job
        # (idempotent theo §21.5's "calling this twice is a no-op").
        try:
            get_queue(INGEST_QUEUE).enqueue(
                "src.jobs.extract_evidence.extract_and_embed_evidence_document",
                document_id=str(doc_id),
            )
        except Exception as exc:
            # The pending attempt was committed before enqueue. Convert a
            # Redis/queue failure into a durable terminal event so it cannot
            # strand the document indefinitely; never expose provider/stack
            # details to the caller.
            latest_attempt = await evidence_extraction.latest_extraction_attempt(doc_id)
            await evidence_extraction.mark_extraction_attempt_failed(
                doc_id,
                attempt_id=(uuid.UUID(str(latest_attempt["id"])) if latest_attempt else None),
                status="failed",
                error_code="ENQUEUE_FAILED",
                error_summary="ingest queue unavailable",
            )
            raise HTTPException(
                status_code=503,
                detail={"message": "Không thể xếp hàng trích xuất; vui lòng thử lại.", "error_code": "ENQUEUE_FAILED"},
            ) from exc
    readiness = await evidence_extraction.get_document_readiness(doc_id)
    return EvidenceExtractionOut(
        document_id=str(doc_id),
        extraction_status=status,
        error_code=readiness.error_code if readiness is not None else None,
        error_summary=(readiness.error_summary if readiness is not None and readiness.error_code else None),
    )


@router.get(
    "/evidence/{document_id}/chunks",
    response_model=list[EvidenceChunkOut],
    summary="Liệt kê chunk đã trích xuất cho một document (debug / reviewer UI)",
)
async def list_evidence_chunks(
    document_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)
) -> list[EvidenceChunkOut]:
    doc_id = _uuid(document_id, "document_id")
    document_project_id = await governance.get_document_project_id(doc_id)
    if document_project_id is None:
        raise HTTPException(status_code=422, detail={"message": "Evidence lịch sử chưa có project scope", "error_code": "DOCUMENT_PROJECT_UNSCOPED"})
    require_project_in_scope(principal, await _resolve_project_external_id(document_project_id))
    rows = await evidence_extraction.get_chunks_for_document(doc_id)
    return [_chunk_out(row) for row in rows]


@router.post(
    "/evidence/{document_id}/archive",
    response_model=DocumentLifecycleOut,
    summary="Lưu trữ một document — loại khỏi hỏi-đáp/trích dẫn/gắn bằng chứng mới, có thể khôi phục",
)
async def archive_evidence_document(
    document_id: str, payload: DocumentLifecycleActionIn, principal: DashboardPrincipal = Depends(require_admin)
) -> DocumentLifecycleOut:
    document_uuid = _uuid(document_id, "document_id")
    document_project_id = await governance.get_document_project_id(document_uuid)
    if document_project_id is None:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy tài liệu trong phạm vi", "error_code": "DOCUMENT_NOT_FOUND"})
    require_project_in_scope(principal, await _resolve_project_external_id(document_project_id))
    actor_expert_id = await _resolve_expert_id(principal)
    try:
        result = await governance.archive_document(
            document_id=document_uuid, actor_expert_id=actor_expert_id, reason=payload.reason
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return DocumentLifecycleOut(**result)


@router.post(
    "/evidence/{document_id}/restore",
    response_model=DocumentLifecycleOut,
    summary="Khôi phục một document đã lưu trữ (không khôi phục được document đã xoá)",
)
async def restore_evidence_document(
    document_id: str, payload: DocumentLifecycleActionIn, principal: DashboardPrincipal = Depends(require_admin)
) -> DocumentLifecycleOut:
    document_uuid = _uuid(document_id, "document_id")
    document_project_id = await governance.get_document_project_id(document_uuid)
    if document_project_id is None:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy tài liệu trong phạm vi", "error_code": "DOCUMENT_NOT_FOUND"})
    require_project_in_scope(principal, await _resolve_project_external_id(document_project_id))
    actor_expert_id = await _resolve_expert_id(principal)
    try:
        result = await governance.restore_document(
            document_id=document_uuid, actor_expert_id=actor_expert_id, reason=payload.reason
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return DocumentLifecycleOut(**result)


@router.post(
    "/evidence/{document_id}/delete",
    response_model=DocumentLifecycleOut,
    summary="Xoá một document — chốt, không khôi phục được",
)
async def delete_evidence_document(
    document_id: str, payload: DocumentLifecycleActionIn, principal: DashboardPrincipal = Depends(require_admin)
) -> DocumentLifecycleOut:
    document_uuid = _uuid(document_id, "document_id")
    document_project_id = await governance.get_document_project_id(document_uuid)
    if document_project_id is None:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy tài liệu trong phạm vi", "error_code": "DOCUMENT_NOT_FOUND"})
    require_project_in_scope(principal, await _resolve_project_external_id(document_project_id))
    actor_expert_id = await _resolve_expert_id(principal)
    try:
        result = await governance.delete_document(
            document_id=document_uuid, actor_expert_id=actor_expert_id, reason=payload.reason
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return DocumentLifecycleOut(**result)


@router.post(
    "/evidence/ask",
    response_model=ExpertAnswerOut,
    summary="Hỏi-đáp có trích dẫn trên tài liệu chuyên gia đã nhập, giới hạn đúng phạm vi dự án",
)
async def ask_expert_documents(
    payload: ExpertQuestionIn, principal: DashboardPrincipal = Depends(require_advisor_read)
) -> ExpertAnswerOut:
    """Scope is resolved and checked here, BEFORE any retrieval — never in
    `answer_expert_question` itself, which trusts whatever `document_ids` it
    is handed (rule 8: never retrieve outside the caller's allowed scope).
    Archived/deleted documents are excluded here too (mandatory-scope item 4)
    — `governance.list_documents()` intentionally returns EVERY document
    (the management view), so this route narrows to lifecycle/extraction/chunk
    ready documents itself before ever calling retrieval."""
    project_uuid = _uuid(payload.project_id, "project_id")
    external_id = await _resolve_project_external_id(project_uuid)
    require_project_in_scope(principal, external_id)

    project_document_ids = [uuid.UUID(str(row["id"])) for row in await governance.list_documents(project_id=project_uuid)]
    if payload.document_ids:
        requested = {_uuid(doc_id, "document_ids") for doc_id in payload.document_ids}
        candidate_ids = [doc_id for doc_id in project_document_ids if doc_id in requested]
    else:
        candidate_ids = project_document_ids
    eligible_ids = await governance.list_retrieval_eligible_document_ids(candidate_ids)
    document_ids = [str(doc_id) for doc_id in candidate_ids if doc_id in eligible_ids]

    result = await advisory_tools.answer_expert_question(payload.question, document_ids)
    return ExpertAnswerOut(
        answer=result.get("answer"),
        citations=[ExpertCitationOut(**citation) for citation in result.get("citations", [])],
        insufficient_evidence=bool(result.get("insufficient_evidence", False)),
        reason=result.get("reason"),
    )


@router.get(
    "/audit-events",
    response_model=list[AuditEventOut],
    summary="Lịch sử công bố/audit — theo proposal hoặc theo ranking_config",
)
async def list_audit_events(
    proposal_id: str | None = Query(default=None),
    ranking_config_id: str | None = Query(default=None),
    principal: DashboardPrincipal = Depends(require_advisor_read),
) -> list[AuditEventOut]:
    if bool(proposal_id) == bool(ranking_config_id):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Cần đúng một trong hai: proposal_id hoặc ranking_config_id",
                "error_code": "SCOPE_REQUIRED",
            },
        )
    if _is_advisor(principal):
        if ranking_config_id:
            raise HTTPException(
                status_code=404,
                detail={"message": "Không tìm thấy lịch sử trong phạm vi của bạn", "error_code": "AUDIT_NOT_FOUND"},
            )
        await _require_proposal_access(_uuid(proposal_id, "proposal_id"), principal)
    try:
        rows = await governance.list_audit_events(
            proposal_id=_uuid(proposal_id, "proposal_id") if proposal_id else None,
            ranking_config_id=_uuid(ranking_config_id, "ranking_config_id") if ranking_config_id else None,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return [
        AuditEventOut(
            id=str(row["id"]),
            ranking_config_id=str(row["ranking_config_id"]) if row["ranking_config_id"] else None,
            proposal_id=str(row["proposal_id"]) if row["proposal_id"] else None,
            actor_expert_id=str(row["actor_expert_id"]) if row["actor_expert_id"] else None,
            actor_identity_subject=row["actor_identity_subject"],
            event_type=row["event_type"],
            before_status=row["before_status"],
            after_status=row["after_status"],
            before_state=row["before_state"],
            after_state=row["after_state"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


# --- Duyệt ---------------------------------------------------------------------------


async def _reviewer_expert_id(principal: DashboardPrincipal) -> uuid.UUID | None:
    """A queue read never creates a profile; it only needs an existing id to
    exclude self-authored work.  A CEO without a profile cannot have authored
    an existing proposal through this service."""
    if not principal.subject:
        return None
    found = await governance.find_expert_profile_id(identity_subject=principal.subject)
    return uuid.UUID(str(found)) if found is not None else None


async def _submitted_review_item_or_404(proposal_id: uuid.UUID, principal: DashboardPrincipal) -> dict:
    item = await governance.get_submitted_review_detail(
        proposal_id=proposal_id,
        project_scope=principal.project_scope,
        reviewer_expert_id=await _reviewer_expert_id(principal),
    )
    if item is None:
        # Same response for unknown, terminal, self-authored and out-of-scope
        # identifiers; callers cannot enumerate governance records.
        raise HTTPException(
            status_code=404,
            detail={"message": "Không tìm thấy đề xuất cần duyệt", "error_code": "REVIEW_ITEM_NOT_FOUND"},
        )
    return item


async def _review_detail_out(item: dict) -> AdvisorAnalysisReviewDetailOut:
    proposal = item["proposal"]
    evidence: list[AdvisorAnalysisReviewEvidenceOut] = []
    for document in item["evidence_documents"]:
        readiness = await evidence_extraction.get_document_readiness(uuid.UUID(str(document["id"])))
        ready = bool(readiness and readiness.eligible)
        file_url = None
        if document["mime_type"] == "application/pdf":
            file_url = (
                f"/api/v1/governance/advisor-analysis/review-queue/{proposal['id']}"
                f"/evidence/{document['id']}/file"
            )
        evidence.append(
            AdvisorAnalysisReviewEvidenceOut(
                original_filename=document["original_filename"],
                mime_type=document["mime_type"],
                file_size_bytes=document["file_size_bytes"],
                extraction_status=readiness.extraction_status if readiness else "not_requested",
                lifecycle_status=readiness.lifecycle_status if readiness else "deleted",
                ready=ready,
                file_url=file_url,
                citation_position_note="Vị trí trang/chunk/trích dẫn không được lưu trong liên kết bằng chứng hiện tại.",
            )
        )
    justifications = [
        AdvisorAnalysisReviewJustificationOut(
            feature_name=row["feature_name"],
            rationale=row["rationale"],
            methodology=row["methodology"],
            evidence_summary=row["evidence_summary"],
            expected_effect=row["expected_effect"],
            confidence=row["confidence"],
            limitations=row["limitations"],
            derived_value=(
                str(row["rubric_band_value"])
                if row.get("rubric_band_value") is not None
                else str(row["normalized_numeric"])
                if row.get("normalized_numeric") is not None
                else row.get("categorical_value")
            ),
            rubric_band_value=str(row["rubric_band_value"]) if row.get("rubric_band_value") is not None else None,
        )
        for row in item["justifications"]
    ]
    ahp_package = None
    if proposal.get("proposal_type") == "ahp_ranking_proposal":
        snapshot = proposal.get("proposed_hierarchy_snapshot") or {}
        active_config = await governance.get_active_ranking_config()
        ahp_package = AhpPackageSummaryOut(
            mode=snapshot.get("mode", "direct"),
            hierarchical_weights=snapshot.get("hierarchical_weights") or {},
            selected_criteria=snapshot.get("selected_criteria") or [],
            levels=(
                [AhpPackageLevelSummaryOut(**level) for level in snapshot["levels"].values()]
                if snapshot.get("levels")
                else None
            ),
            frozen_at=snapshot.get("frozen_at"),
            current_active_config_version=active_config["version"] if active_config else None,
            current_active_config_note=active_config["note"] if active_config else None,
        )
    return AdvisorAnalysisReviewDetailOut(
        proposal_id=str(proposal["id"]),
        assertion_kind=proposal["assertion_kind"],
        submitted_at=proposal["submitted_at"],
        justifications=justifications,
        evidence_documents=evidence,
        evidence_ready=item["evidence_ready"],
        validation=("Bằng chứng và validation hiện đủ điều kiện tại thời điểm đọc." if item["evidence_ready"] else "Có bằng chứng chưa đủ điều kiện lifecycle/validation; không thể phê duyệt."),
        proposal_type=proposal.get("proposal_type", "qualitative_analysis"),
        ahp_package=ahp_package,
    )


@router.get(
    "/advisor-analysis/review-queue",
    response_model=AdvisorAnalysisReviewQueuePageOut,
    summary="Hàng đợi CEO: chỉ proposal đã submitted trong phạm vi server cấp",
)
async def advisor_analysis_review_queue(
    principal: DashboardPrincipal = Depends(require_reviewer_visibility),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AdvisorAnalysisReviewQueuePageOut:
    rows, total = await governance.build_submitted_review_queue(
        project_scope=principal.project_scope,
        reviewer_expert_id=await _reviewer_expert_id(principal),
        limit=limit,
        offset=offset,
    )
    return AdvisorAnalysisReviewQueuePageOut(
        items=[
            AdvisorAnalysisReviewQueueItemOut(
                proposal_id=str(row["proposal"]["id"]),
                assertion_kind=row["proposal"]["assertion_kind"],
                submitted_at=row["proposal"]["submitted_at"],
                evidence_document_count=row["evidence_document_count"],
                evidence_ready=row["evidence_ready"],
                requires_attention=not row["evidence_ready"],
                proposal_type=row["proposal"].get("proposal_type", "qualitative_analysis"),
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get(
    "/advisor-analysis/review-queue/{proposal_id}",
    response_model=AdvisorAnalysisReviewDetailOut,
    summary="Chi tiết proposal đã submitted, chỉ dành cho CEO trong phạm vi server cấp",
)
async def advisor_analysis_review_detail(
    proposal_id: str, principal: DashboardPrincipal = Depends(require_reviewer_visibility)
) -> AdvisorAnalysisReviewDetailOut:
    return await _review_detail_out(await _submitted_review_item_or_404(_uuid(proposal_id, "proposal_id"), principal))


@router.get(
    "/advisor-analysis/review-queue/{proposal_id}/evidence/{document_id}/file",
    summary="Stream PDF evidence của proposal submitted, chỉ dành cho CEO",
)
async def advisor_analysis_review_evidence_file(
    proposal_id: str,
    document_id: str,
    principal: DashboardPrincipal = Depends(require_reviewer_visibility),
) -> FileResponse:
    item = await _submitted_review_item_or_404(_uuid(proposal_id, "proposal_id"), principal)
    wanted_id = _uuid(document_id, "document_id")
    document = next((row for row in item["evidence_documents"] if row["id"] == wanted_id), None)
    if document is None or document["mime_type"] != "application/pdf":
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy file bằng chứng", "error_code": "EVIDENCE_FILE_NOT_FOUND"})
    readiness = await evidence_extraction.get_document_readiness(wanted_id)
    if readiness is None or not readiness.eligible:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy file bằng chứng", "error_code": "EVIDENCE_FILE_NOT_FOUND"})
    storage_root = Path(get_settings().upload_dir).resolve()
    candidate = (storage_root / document["object_storage_key"]).resolve()
    if storage_root not in candidate.parents or not candidate.is_file():
        log.warning("governance.review_evidence_file.unavailable", proposal_id=str(proposal_id), document_id=str(document_id))
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy file bằng chứng", "error_code": "EVIDENCE_FILE_NOT_FOUND"})
    return FileResponse(candidate, media_type="application/pdf", filename=document["original_filename"])


@router.post(
    "/proposals/{proposal_id}/reviews",
    response_model=ProposalOut,
    status_code=201,
    summary="CEO ghi một quyết định immutable (approved|rejected)",
)
async def submit_review(
    proposal_id: str, payload: ReviewIn, principal: DashboardPrincipal = Depends(require_ceo_review)
) -> ProposalOut:
    """D18 close-out: `require_admin` is only the floor (CRM.CEO collapses to
    `admin` at the OIDC layer) — the real CEO gate and self-approval guard
    live in `governance.submit_review()`, keyed off `principal.subject`/
    `.is_ceo`, for BOTH assertion kinds now. There is no `reviewer_expert_id`
    request-body field anymore for either mode to bypass."""
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _submitted_review_item_or_404(proposal_uuid, principal)
    if payload.decision == "approved" and not payload.evidence_review_acknowledged:
        raise HTTPException(
            status_code=422,
            detail={"message": "CEO phải xác nhận đã xem bằng chứng trước khi duyệt.", "error_code": "EVIDENCE_REVIEW_ACK_REQUIRED"},
        )
    if payload.decision == "rejected" and len(payload.comment.strip()) < 8:
        raise HTTPException(status_code=422, detail={"message": "Cần nêu lý do từ chối rõ ràng bằng tiếng Việt.", "error_code": "REJECTION_REASON_REQUIRED"})
    try:
        row = await governance.submit_review(
            proposal_id=proposal_uuid,
            decision=payload.decision,
            comment=payload.comment,
            reviewer_subject=principal.subject,
            reviewer_is_ceo=principal.is_ceo,
            reviewer_project_scope=principal.project_scope,
            evidence_review_acknowledged=payload.evidence_review_acknowledged,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.post(
    "/advisor-analysis/ahp-proposals/{proposal_id}/retry-application",
    response_model=ProposalOut,
    summary="CEO retry có kiểm toán cho AHP application failed/deferred",
)
async def retry_ahp_application(
    proposal_id: str,
    payload: AhpApplicationRetryIn,
    principal: DashboardPrincipal = Depends(require_ceo_review),
) -> ProposalOut:
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal)
    # `_require_proposal_access` performs the server-derived external project
    # scope check before any recovery state is disclosed or mutated.
    try:
        row = await governance.retry_ahp_application(
            proposal_id=proposal_uuid,
            actor_expert_id=await _reviewer_expert_id(principal),
            actor_subject=principal.subject or "",
            actor_is_ceo=principal.is_ceo,
            reason=payload.reason,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.post(
    "/advisor-analysis/ranking-runs/{run_id}/reconcile",
    response_model=RankingRunReconcileOut,
    summary="CEO reconciliation cho một ranking run kẹt, có kiểm toán",
)
async def reconcile_ranking_run(
    run_id: str,
    payload: RankingRunReconcileIn,
    principal: DashboardPrincipal = Depends(require_ceo_review),
) -> RankingRunReconcileOut:
    run_uuid = _uuid(run_id, "run_id")
    async with get_session_factory()() as session:
        target = (
            await session.execute(
                sa.select(ranking_runs.c.id, projects.c.external_id)
                .select_from(ranking_runs.join(projects, ranking_runs.c.project_id == projects.c.id))
                .where(ranking_runs.c.id == run_uuid)
            )
        ).first()
        await session.rollback()
    if target is None:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy ranking run", "error_code": "RANKING_RUN_NOT_FOUND"})
    require_project_in_scope(principal, target.external_id)
    try:
        result = await ranking_run_recovery.reconcile_stuck_ranking_run(
            run_id=run_uuid,
            actor_identity_subject=principal.subject or "",
            actor_expert_id=await _reviewer_expert_id(principal),
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"message": "Cần nêu lý do khôi phục rõ ràng.", "error_code": str(exc)}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": "Không tìm thấy ranking run", "error_code": str(exc)}) from exc
    run = result["run"]
    return RankingRunReconcileOut(
        changed=result["changed"], reason_code=result["reason_code"], run_id=str(run["id"]), status=run["status"]
    )


@router.get("/proposals/{proposal_id}/reviews", response_model=list[ReviewOut])
async def list_reviews(proposal_id: str, principal: DashboardPrincipal = Depends(require_advisor_read)) -> list[ReviewOut]:
    proposal_uuid = _uuid(proposal_id, "proposal_id")
    await _require_proposal_access(proposal_uuid, principal)
    rows = await governance.list_reviews(proposal_uuid)
    return [_review_out(row) for row in rows]
