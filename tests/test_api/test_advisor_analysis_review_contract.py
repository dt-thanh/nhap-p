"""Non-DB contract tests for the CEO reviewer projection and file guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from src.api import governance as api_governance
from src.services.dashboard_auth import DashboardPrincipal
from src.services.evidence_extraction import DocumentReadiness


def _ceo() -> DashboardPrincipal:
    return DashboardPrincipal(
        role="admin", project_scope=frozenset({"P-0001"}), subject="ceo-subject", is_ceo=True,
        oidc_roles=frozenset({"CRM.CEO"}),
    )


@pytest.mark.asyncio
async def test_queue_projection_is_paginated_minimal_and_does_not_leak_sensitive_fields(monkeypatch):
    proposal_id = uuid.uuid4()
    monkeypatch.setattr(api_governance, "_reviewer_expert_id", lambda _principal: _async(None))
    monkeypatch.setattr(
        api_governance.governance,
        "build_submitted_review_queue",
        lambda **_kwargs: _async(([
            {"proposal": {"id": proposal_id, "assertion_kind": "value", "submitted_at": datetime.now(UTC)}, "evidence_document_count": 1, "evidence_ready": True}
        ], 1)),
    )
    response = await api_governance.advisor_analysis_review_queue(_ceo(), limit=25, offset=0)
    payload = response.model_dump()
    assert payload["total"] == 1
    assert payload["items"][0]["submitter_label"] == "Cố vấn"
    rendered = str(payload)
    for forbidden in ("object_storage_key", "project_id", "area_id", "identity_subject", "email", "created_by_expert_id"):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_detail_uses_safe_metadata_and_only_a_pdf_action(monkeypatch):
    proposal_id, pdf_id, text_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    item = {
        "proposal": {"id": proposal_id, "assertion_kind": "value", "submitted_at": datetime.now(UTC)},
        "justifications": [{"feature_name": "Khả năng tiếp cận", "rationale": "Có kết nối tốt", "methodology": "Rubric", "evidence_summary": "PDF", "expected_effect": "increase", "confidence": "high", "limitations": "Cần cập nhật", "rubric_band_value": 0.75, "normalized_numeric": 0.75, "categorical_value": None}],
        "evidence_documents": [
            {"id": pdf_id, "original_filename": "evidence.pdf", "mime_type": "application/pdf", "file_size_bytes": 42, "object_storage_key": "private.pdf"},
            {"id": text_id, "original_filename": "note.txt", "mime_type": "text/plain", "file_size_bytes": 10, "object_storage_key": "private.txt"},
        ],
        "evidence_ready": True,
    }
    monkeypatch.setattr(api_governance.evidence_extraction, "get_document_readiness", lambda document_id: _async(DocumentReadiness(document_id, "active", "succeeded", 1, 1, True)))
    payload = (await api_governance._review_detail_out(item)).model_dump()
    assert payload["evidence_documents"][0]["file_url"]
    assert payload["evidence_documents"][1]["file_url"] is None
    assert "private.pdf" not in str(payload)
    assert "Vị trí trang/chunk/trích dẫn không được lưu" in payload["evidence_documents"][0]["citation_position_note"]


@pytest.mark.asyncio
async def test_pdf_file_endpoint_rejects_swapped_document_id_and_never_returns_storage_metadata(monkeypatch, tmp_path):
    proposal_id, document_id = uuid.uuid4(), uuid.uuid4()
    key = "governance/evidence/opaque.pdf"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"%PDF-1.4\nfixture\n%%EOF")
    item = {
        "proposal": {"id": proposal_id}, "justifications": [], "evidence_ready": True,
        "evidence_documents": [{"id": document_id, "mime_type": "application/pdf", "object_storage_key": key, "original_filename": "evidence.pdf"}],
    }
    monkeypatch.setattr(api_governance, "_submitted_review_item_or_404", lambda *_args: _async(item))
    monkeypatch.setattr(api_governance.evidence_extraction, "get_document_readiness", lambda value: _async(DocumentReadiness(value, "active", "succeeded", 1, 1, True)))
    monkeypatch.setattr(api_governance, "get_settings", lambda: type("_Settings", (), {"upload_dir": str(tmp_path)})())
    response = await api_governance.advisor_analysis_review_evidence_file(str(proposal_id), str(document_id), _ceo())
    assert response.path == path
    assert key not in str(response.headers)
    with pytest.raises(HTTPException) as exc:
        await api_governance.advisor_analysis_review_evidence_file(str(proposal_id), str(uuid.uuid4()), _ceo())
    assert exc.value.status_code == 404


async def _async(value):
    return value
