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
`viewer` for reads and self-service expert registration, `operator` for
authoring (proposals, justifications, evidence), `admin` for the
higher-trust actions that touch config linkage, review decisions, and
publication — mirroring `require_admin` already used for
`POST /ranking/configs` and `POST /ranking/ahp/weights`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException

from src.logging_config import get_logger
from src.models.schemas import (
    EvidenceChunkOut,
    EvidenceDocumentOut,
    EvidenceDocumentRegisterIn,
    EvidenceExtractionOut,
    EvidenceLinkIn,
    ExpertProfileIn,
    ExpertProfileOut,
    JustificationIn,
    JustificationOut,
    ProposalActionIn,
    ProposalCreateIn,
    ProposalOut,
    ProposalSetConfigIn,
    ReviewIn,
    ReviewOut,
)
from src.services import evidence_extraction, governance
from src.services.dashboard_auth import DashboardPrincipal, require_role
from src.task_queue import INGEST_QUEUE, get_queue

router = APIRouter(prefix="/governance", tags=["governance"])
require_viewer = require_role("business_viewer")
require_operator = require_role("pipeline_operator")
require_admin = require_role("admin")
log = get_logger("src.api.governance")


def _fail(exc: governance.GovernanceError) -> HTTPException:
    not_found = exc.code.endswith("_NOT_FOUND")
    forbidden = exc.code in ("CEO_APPROVAL_REQUIRED", "SELF_APPROVAL_FORBIDDEN", "GRAIN_NOT_ASSERTABLE")
    conflict = exc.code in (
        "ALREADY_REVIEWED",
        "DUPLICATE_OBJECT_STORAGE_KEY",
        "PROPOSAL_STATUS_INVALID",
        "EVIDENCE_LOCKED",
        "VALUE_MATERIALIZATION_DEFERRED_TO_PR3",
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


def _document_out(row: dict) -> EvidenceDocumentOut:
    return EvidenceDocumentOut(
        id=str(row["id"]),
        proposal_id=str(row["proposal_id"]) if row["proposal_id"] else None,
        uploaded_by_expert_id=str(row["uploaded_by_expert_id"]),
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        object_storage_key=row["object_storage_key"],
        sha256_checksum=row["sha256_checksum"],
        file_size_bytes=row["file_size_bytes"],
        extraction_status=row["extraction_status"],
        created_at=row["created_at"],
    )


def _review_out(row: dict) -> ReviewOut:
    return ReviewOut(
        id=str(row["id"]),
        proposal_id=str(row["proposal_id"]),
        reviewer_expert_id=str(row["reviewer_expert_id"]),
        decision=row["decision"],
        comment=row["comment"],
        decided_at=row["decided_at"],
    )


# --- Chuyên gia ----------------------------------------------------------------


@router.post(
    "/experts",
    response_model=ExpertProfileOut,
    summary="Tự đăng ký / lấy lại hồ sơ chuyên gia theo identity_subject",
)
async def register_expert(
    payload: ExpertProfileIn, principal: DashboardPrincipal = Depends(require_viewer)
) -> ExpertProfileOut:
    try:
        row = await governance.get_or_create_expert_profile(
            identity_subject=payload.identity_subject,
            organization=payload.organization,
            title=payload.title,
            expertise_summary=payload.expertise_summary,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _expert_out(row)


@router.get("/experts/{expert_id}", response_model=ExpertProfileOut)
async def get_expert(expert_id: str, principal: DashboardPrincipal = Depends(require_viewer)) -> ExpertProfileOut:
    try:
        row = await governance.get_expert_profile(_uuid(expert_id, "expert_id"))
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
    payload: ProposalCreateIn, principal: DashboardPrincipal = Depends(require_operator)
) -> ProposalOut:
    if payload.assertion_kind == "value":
        # PR-2/D38: danh tính tác giả LUÔN suy từ principal đã xác thực —
        # payload.created_by_expert_id (nếu có) bị bỏ qua hoàn toàn, không đọc.
        if not principal.subject:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Tạo value assertion cần danh tính cá nhân đã xác thực (OIDC) — "
                    "token tĩnh/dev-bypass không mang subject.",
                    "error_code": "IDENTITY_REQUIRED_FOR_VALUE_MODE",
                },
            )
        expert = await governance.get_or_create_expert_profile(identity_subject=principal.subject)
        created_by_expert_id = uuid.UUID(str(expert["id"]))
    else:
        if not payload.created_by_expert_id:
            raise HTTPException(
                status_code=422,
                detail={"message": "created_by_expert_id không được rỗng", "error_code": "CREATED_BY_EXPERT_ID_REQUIRED"},
            )
        created_by_expert_id = _uuid(payload.created_by_expert_id, "created_by_expert_id")

    try:
        row = await governance.create_proposal(
            base_config_id=_uuid(payload.base_config_id, "base_config_id") if payload.base_config_id else None,
            project_id=_uuid(payload.project_id, "project_id"),
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
    principal: DashboardPrincipal = Depends(require_viewer),
) -> list[ProposalOut]:
    rows = await governance.list_proposals(
        project_id=_uuid(project_id, "project_id") if project_id else None, status=status
    )
    return [_proposal_out(row) for row in rows]


@router.get("/proposals/{proposal_id}", response_model=ProposalOut)
async def get_proposal(proposal_id: str, principal: DashboardPrincipal = Depends(require_viewer)) -> ProposalOut:
    try:
        row = await governance.get_proposal(_uuid(proposal_id, "proposal_id"))
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
    try:
        row = await governance.set_proposed_config(
            proposal_id=_uuid(proposal_id, "proposal_id"),
            proposed_config_id=_uuid(payload.proposed_config_id, "proposed_config_id"),
            actor_expert_id=_uuid(payload.actor_expert_id, "actor_expert_id"),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.post("/proposals/{proposal_id}/submit", response_model=ProposalOut, summary="draft → submitted")
async def submit_proposal(
    proposal_id: str, payload: ProposalActionIn, principal: DashboardPrincipal = Depends(require_operator)
) -> ProposalOut:
    try:
        row = await governance.submit_proposal(
            proposal_id=_uuid(proposal_id, "proposal_id"),
            actor_expert_id=_uuid(payload.actor_expert_id, "actor_expert_id"),
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
    proposal_id: str, payload: ProposalActionIn, principal: DashboardPrincipal = Depends(require_operator)
) -> ProposalOut:
    try:
        row = await governance.withdraw_proposal(
            proposal_id=_uuid(proposal_id, "proposal_id"),
            actor_expert_id=_uuid(payload.actor_expert_id, "actor_expert_id"),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.post(
    "/proposals/{proposal_id}/publish",
    response_model=ProposalOut,
    summary="approved → published. Xác nhận ranking_configs đã published từ trước — KHÔNG tự publish.",
)
async def publish_proposal(
    proposal_id: str, payload: ProposalActionIn, principal: DashboardPrincipal = Depends(require_admin)
) -> ProposalOut:
    try:
        row = await governance.mark_published(
            proposal_id=_uuid(proposal_id, "proposal_id"),
            actor_expert_id=_uuid(payload.actor_expert_id, "actor_expert_id"),
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
    proposal_id: str, payload: JustificationIn, principal: DashboardPrincipal = Depends(require_operator)
) -> JustificationOut:
    if payload.assertion_kind == "value":
        if not principal.subject:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Value assertion cần danh tính cá nhân đã xác thực (OIDC).",
                    "error_code": "IDENTITY_REQUIRED_FOR_VALUE_MODE",
                },
            )
        expert = await governance.get_or_create_expert_profile(identity_subject=principal.subject)
        created_by_expert_id = uuid.UUID(str(expert["id"]))
        author_subject = principal.subject
    else:
        if not payload.created_by_expert_id:
            raise HTTPException(
                status_code=422,
                detail={"message": "created_by_expert_id không được rỗng", "error_code": "CREATED_BY_EXPERT_ID_REQUIRED"},
            )
        created_by_expert_id = _uuid(payload.created_by_expert_id, "created_by_expert_id")
        author_subject = None

    try:
        row = await governance.upsert_justification(
            proposal_id=_uuid(proposal_id, "proposal_id"),
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
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _justification_out(row)


@router.get("/proposals/{proposal_id}/justifications", response_model=list[JustificationOut])
async def list_justifications(
    proposal_id: str, principal: DashboardPrincipal = Depends(require_viewer)
) -> list[JustificationOut]:
    rows = await governance.list_justifications(_uuid(proposal_id, "proposal_id"))
    return [_justification_out(row) for row in rows]


# --- Bằng chứng --------------------------------------------------------------------


@router.post(
    "/evidence",
    response_model=EvidenceDocumentOut,
    status_code=201,
    summary="Ghi hàng siêu dữ liệu cho một file ĐÃ nằm trên storage (không nhận multipart)",
)
async def register_evidence(
    payload: EvidenceDocumentRegisterIn, principal: DashboardPrincipal = Depends(require_operator)
) -> EvidenceDocumentOut:
    try:
        row = await governance.register_evidence_document(
            proposal_id=_uuid(payload.proposal_id, "proposal_id") if payload.proposal_id else None,
            uploaded_by_expert_id=_uuid(payload.uploaded_by_expert_id, "uploaded_by_expert_id"),
            original_filename=payload.original_filename,
            mime_type=payload.mime_type,
            object_storage_key=payload.object_storage_key,
            sha256_checksum=payload.sha256_checksum,
            file_size_bytes=payload.file_size_bytes,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _document_out(row)


@router.post("/evidence/link", status_code=204, summary="Liên kết một document tới một justification")
async def link_evidence(payload: EvidenceLinkIn, principal: DashboardPrincipal = Depends(require_operator)) -> None:
    try:
        await governance.link_evidence_to_justification(
            document_id=_uuid(payload.document_id, "document_id"),
            feature_justification_id=_uuid(payload.feature_justification_id, "feature_justification_id"),
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc


@router.get("/justifications/{feature_justification_id}/evidence", response_model=list[EvidenceDocumentOut])
async def list_evidence_for_justification(
    feature_justification_id: str, principal: DashboardPrincipal = Depends(require_viewer)
) -> list[EvidenceDocumentOut]:
    rows = await governance.list_documents_for_justification(_uuid(feature_justification_id, "feature_justification_id"))
    return [_document_out(row) for row in rows]


# --- Trích xuất chunk + embedding (0035, §21.5-§21.6) -------------------------------
#
# Trạng thái trả về là `evidence_extraction.latest_extraction_status` — dòng mới
# nhất trong `ranking_evidence_extraction_attempts`, KHÔNG phải
# `EvidenceDocumentOut.extraction_status` (cột đó đứng yên từ lúc đăng ký, xem
# docstring migration 0035).


@router.post(
    "/evidence/{document_id}/extract",
    response_model=EvidenceExtractionOut,
    summary="Enqueue chunk+embed cho một evidence document đã có sẵn trong storage",
)
async def request_evidence_extraction(
    document_id: str, principal: DashboardPrincipal = Depends(require_operator)
) -> EvidenceExtractionOut:
    doc_id = _uuid(document_id, "document_id")
    try:
        previous = await evidence_extraction.latest_extraction_status(doc_id)
        status = await evidence_extraction.request_extraction(doc_id)
    except evidence_extraction.ExtractionError as exc:
        raise _extraction_fail(exc) from exc

    if previous not in ("pending", "succeeded"):
        # Chỉ enqueue khi lần gọi NÀY thật sự tạo attempt 'pending' mới — gọi
        # lại trên một document đang 'pending'/'succeeded' không xếp thêm job
        # (idempotent theo §21.5's "calling this twice is a no-op").
        get_queue(INGEST_QUEUE).enqueue(
            "src.jobs.extract_evidence.extract_and_embed_evidence_document",
            document_id=str(doc_id),
        )
    return EvidenceExtractionOut(document_id=str(doc_id), extraction_status=status)


@router.get(
    "/evidence/{document_id}/chunks",
    response_model=list[EvidenceChunkOut],
    summary="Liệt kê chunk đã trích xuất cho một document (debug / reviewer UI)",
)
async def list_evidence_chunks(
    document_id: str, principal: DashboardPrincipal = Depends(require_viewer)
) -> list[EvidenceChunkOut]:
    rows = await evidence_extraction.get_chunks_for_document(_uuid(document_id, "document_id"))
    return [_chunk_out(row) for row in rows]


# --- Duyệt ---------------------------------------------------------------------------


@router.post(
    "/proposals/{proposal_id}/reviews",
    response_model=ProposalOut,
    status_code=201,
    summary="Một reviewer, một quyết định (approved|rejected|request_changes)",
)
async def submit_review(
    proposal_id: str, payload: ReviewIn, principal: DashboardPrincipal = Depends(require_admin)
) -> ProposalOut:
    """Weight-mode: hành vi KHÔNG đổi — `require_admin` (mức tối thiểu hôm
    nay) là đủ, `reviewer_expert_id` vẫn đọc thẳng từ body. Value-mode
    (PR-2/D38): `require_admin` chỉ là NGƯỠNG SÀN (CRM.CEO đã collapse thành
    `admin`) — chốt CEO thật (`principal.is_ceo`) và chốt tự-duyệt nằm ở
    `governance.submit_review()`, dùng `principal.subject`/`.is_ceo` chứ
    KHÔNG BAO GIỜ đọc `payload.reviewer_expert_id` cho nhánh đó."""
    try:
        row = await governance.submit_review(
            proposal_id=_uuid(proposal_id, "proposal_id"),
            reviewer_expert_id=_uuid(payload.reviewer_expert_id, "reviewer_expert_id")
            if payload.reviewer_expert_id
            else None,
            decision=payload.decision,
            comment=payload.comment,
            reviewer_subject=principal.subject,
            reviewer_is_ceo=principal.is_ceo,
        )
    except governance.GovernanceError as exc:
        raise _fail(exc) from exc
    return _proposal_out(row)


@router.get("/proposals/{proposal_id}/reviews", response_model=list[ReviewOut])
async def list_reviews(proposal_id: str, principal: DashboardPrincipal = Depends(require_viewer)) -> list[ReviewOut]:
    rows = await governance.list_reviews(_uuid(proposal_id, "proposal_id"))
    return [_review_out(row) for row in rows]
