"""Regression coverage for the mounted Expert Analysis governance router."""

import uuid
from datetime import UTC, datetime

import pytest

from src.api import governance
from src.main import app
from src.models.schemas import ExpertProfileIn
from src.services.dashboard_auth import DashboardPrincipal


def test_governance_router_is_registered_once_with_api_prefix():
    paths = app.openapi()["paths"]
    assert sum(getattr(route, "original_router", None) is governance.router for route in app.router.routes) == 1
    assert "/api/v1/governance/experts" in paths
    assert "post" in paths["/api/v1/governance/experts"]
    assert "/api/v1/governance/advisor-analysis/review-queue" in paths
    assert "/api/v1/governance/evidence/ask" in paths
    linked_evidence_path = "/api/v1/governance/advisor-analysis/ahp-proposals/{proposal_id}/linked-evidence"
    assert linked_evidence_path in paths
    assert "get" in paths[linked_evidence_path]
    assert "post" in paths[
        "/api/v1/governance/advisor-analysis/ahp-proposals/{proposal_id}/link-evidence"
    ]


@pytest.mark.asyncio
async def test_qualified_advisor_bootstrap_uses_authenticated_oidc_subject(monkeypatch):
    captured = {}

    async def fake_get_or_create(**kwargs):
        captured.update(kwargs)
        now = datetime.now(UTC)
        return {
            "id": uuid.uuid4(),
            "identity_subject": kwargs["identity_subject"],
            "organization": kwargs["organization"],
            "title": kwargs["title"],
            "expertise_summary": kwargs["expertise_summary"],
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    monkeypatch.setattr(governance.governance, "get_or_create_expert_profile", fake_get_or_create)
    principal = DashboardPrincipal(
        role="business_viewer",
        project_scope=frozenset({"P-0001"}),
        subject="oidc-advisor-subject",
        oidc_roles=frozenset({"CRM.ADVISOR"}),
    )
    result = await governance.register_expert(
        ExpertProfileIn(organization="La Pura", title="Advisor"), principal
    )

    assert result.identity_subject == "oidc-advisor-subject"
    assert captured["identity_subject"] == "oidc-advisor-subject"


@pytest.mark.asyncio
async def test_feature_catalog_exposes_project_design_score_but_not_the_legal_gate(monkeypatch):
    async def fake_definitions(*, grain=None):
        return [
            {
                "id": uuid.uuid4(), "feature_key": "project_design_score", "name": "Điểm chất lượng thiết kế dự án",
                "category": "expert", "grain": "project", "value_type": "numeric", "direction": "positive", "missing_policy": "neutral",
            },
            {
                "id": uuid.uuid4(), "feature_key": "project_legal_status", "name": "Legal gate",
                "category": "legal", "grain": "project", "value_type": "categorical", "direction": "neutral", "missing_policy": "skip",
            },
        ]

    monkeypatch.setattr(governance.governance, "list_feature_definitions", fake_definitions)
    principal = DashboardPrincipal(role="business_viewer", project_scope=frozenset({"P-0001"}), subject="advisor")
    rows = await governance.list_feature_definitions(grain="project", principal=principal)

    assert [row.feature_key for row in rows] == ["project_design_score"]
