"""`src/services/governance.py` — Advisor-authored AHP ranking proposal (0049).

Covers the highest-priority scenarios from the mission's 22-scenario test
plan: proposal_type isolation from the pre-existing qualitative flow,
canonical-registry criterion enforcement (Rule 11), draft/freeze/submit
semantics, and the approval → config → publish → run apply path (including
its idempotent retry and honest-failure behavior). Not a full 22/22 replay —
see pipeline_status.md for the explicit list of scenarios still uncovered.

Self-contained fixture (does NOT import `tests.test_agent_e2e` — that module
is currently deleted, a pre-existing regression unrelated to this task; see
`tests/test_api/test_ranking_report_hierarchy_disclosure.py` for the same
established workaround). Run via `bash scripts/test_db.sh` (isolated `_test`
DB only) — same convention as every other DB-backed test in this repo.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import (
    areas,
    projects,
    ranking_configs,
    ranking_evidence_documents,
    ranking_feature_definitions,
    ranking_proposal_evidence_links,
    ranking_proposal_rationale_chunks,
    ranking_runs,
    ranking_weight_proposals,
)
from src.ranking import service as ranking_service
from src.services import evidence_extraction, governance, ranking_config, ranking_run_recovery, rationale_retrieval


def _skip_reason() -> str:
    import os

    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' — chạy `bash scripts/test_db.sh`"
    return ""


_SKIP = _skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

PROJECT_ID = uuid.uuid4()
AREA_ID = uuid.uuid4()
MARKET_FEATURE_ID = uuid.uuid4()
PROJECT_FEATURE_ID = uuid.uuid4()
AREA_FEATURE_ID = uuid.uuid4()


class FakeQueue:
    """Redis stand-in — records enqueue calls instead of touching real RQ,
    same pattern as `tests/test_ranking/test_triggers.py::FakeQueue`."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, func: str, **kwargs):
        self.calls.append({"func": func, **kwargs})
        return type("Job", (), {"id": f"job-{len(self.calls)}"})()


@pytest_asyncio.fixture
async def factory(monkeypatch):
    import os

    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # `_apply_ahp_proposal` reaches into `src.ranking.service`/
    # `src.services.ranking_trigger` (via `trigger_ranking`) in addition to
    # `governance`/`ranking_config` — every one of their own
    # `get_session_factory()` references must point at THIS isolated engine,
    # never the real dev DB, or a "no live side effects" requirement is
    # silently violated by this test itself.
    for target in (
        "src.services.evidence_extraction.get_session_factory",
        "src.services.governance.get_session_factory",
        "src.services.ranking_config.get_session_factory",
        "src.ranking.service.get_session_factory",
        "src.services.ranking_trigger.get_session_factory",
        "src.services.rationale_retrieval.get_session_factory",
    ):
        monkeypatch.setattr(target, lambda sf=session_factory: sf, raising=False)
    queue = FakeQueue()
    monkeypatch.setattr("src.services.ranking_dispatch.get_queue", lambda *_a, **_k: queue, raising=False)
    monkeypatch.setattr(
        "src.services.governance.get_settings",
        lambda: type("Settings", (), {"hierarchical_ranking_enabled": True})(),
    )

    tables = (
        "ranking_runs",
        "ranking_proposal_rationale_chunks",
        "ranking_config_audit_events",
        "ranking_proposal_reviews",
        "ranking_evidence_documents",
        "ranking_feature_justifications",
        "ranking_weight_proposals",
        "expert_profiles",
        "ranking_feature_definitions",
        "ranking_configs",
        "areas",
        "projects",
    )
    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))

    now = datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            sa.insert(projects).values(
                id=PROJECT_ID,
                name="AHP Proposal Test Project",
                launch_date=date(2026, 1, 1),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id=f"P-AHP-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(areas).values(
                id=AREA_ID,
                project_id=PROJECT_ID,
                area_name="Test Area",
                unit_type="apartment",
                bedrooms=2,
                area_sqm=Decimal("70"),
                total_units=10,
                created_at=now,
                updated_at=now,
            )
        )
        for feature_id, feature_key, grain in (
            (MARKET_FEATURE_ID, "market_interest_rate", "market"),
            (PROJECT_FEATURE_ID, "project_design_score", "project"),
            (AREA_FEATURE_ID, "area_accessibility", "area"),
        ):
            await session.execute(
                sa.insert(ranking_feature_definitions).values(
                    id=feature_id,
                    feature_key=feature_key,
                    feature_version="v1",
                    name=feature_key,
                    category="qualitative",
                    grain=grain,
                    value_type="numeric",
                    formula_id=f"{feature_key}_v1",
                    normalization_method="identity",
                    direction="positive",
                    missing_policy="neutral",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()

    return {"factory": session_factory, "queue": queue}


async def _published_base_config() -> dict:
    draft = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="base",
        created_by="test",
    )
    return await ranking_config.publish(version=draft["version"], published_by="test")


async def _expert(identity_subject: str) -> uuid.UUID:
    row = await governance.get_or_create_expert_profile(identity_subject=identity_subject)
    return uuid.UUID(str(row["id"]))


async def _attach_direct_evidence(*, proposal_id: uuid.UUID | None, expert_id: uuid.UUID) -> uuid.UUID:
    doc = await governance.register_evidence_document(
        project_id=PROJECT_ID,
        proposal_id=proposal_id,
        uploaded_by_expert_id=expert_id,
        original_filename="ahp-evidence.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="b" * 64,
        file_size_bytes=100,
    )
    document_id = uuid.UUID(str(doc["id"]))
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id,
        [
            {
                "chunk_index": 0,
                "page_number": 1,
                "content": "AHP proposal rationale evidence.",
                "token_count": 5,
                "embedding_model": "text-embedding-3-small",
                "embedding": [0.001] * 1536,
            }
        ],
    )
    return document_id


DIRECT_HIERARCHICAL_WEIGHTS = {
    "grain_weights": {
        "market": {"weight": 0.25, "missing_value_policy": "neutral"},
        "project": {"weight": 0.25, "missing_value_policy": "neutral"},
        "area": {"weight": 0.25, "missing_value_policy": "neutral"},
        "unit": {"weight": 0.25, "missing_value_policy": "neutral"},
    },
    "market": {"market_interest_rate": {"weight": 1.0, "direction": "negative", "missing_value_policy": "neutral"}},
    "project": {
        "project_design_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}
    },
    "area": {"area_accessibility": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}},
}

ZERO_PROJECT_HIERARCHICAL_WEIGHTS = {
    "grain_weights": {
        "market": {"weight": 0.35, "missing_value_policy": "neutral"},
        "project": {"weight": 0.0, "missing_value_policy": "neutral"},
        "area": {"weight": 0.25, "missing_value_policy": "neutral"},
        "unit": {"weight": 0.40, "missing_value_policy": "neutral"},
    },
    "market": {"market_interest_rate": {"weight": 1.0, "direction": "negative", "missing_value_policy": "neutral"}},
    "project": {},
    "area": {"area_accessibility": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}},
}


async def _draft_ahp_proposal(*, base_config_id: uuid.UUID, expert_id: uuid.UUID) -> dict:
    return await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, proposal_type="ahp_ranking_proposal"
    )


# --- A. Proposal subtype isolation --------------------------------------------


async def test_ahp_proposal_resolves_base_config_server_side_and_never_accepts_a_client_one(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    assert proposal["proposal_type"] == "ahp_ranking_proposal"
    assert proposal["base_config_id"] == base["id"]
    assert proposal["assertion_kind"] == "weight"
    assert proposal["scope_type"] == "project"
    assert proposal["area_id"] is None


async def test_ahp_proposal_create_rejects_a_caller_supplied_base_config_id(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    with pytest.raises(governance.GovernanceError) as excinfo:
        await governance.create_proposal(
            base_config_id=uuid.UUID(str(base["id"])),
            project_id=PROJECT_ID,
            created_by_expert_id=advisor_id,
            proposal_type="ahp_ranking_proposal",
        )
    assert excinfo.value.code == "BASE_CONFIG_NOT_ALLOWED"


async def test_qualitative_proposal_default_type_is_unaffected(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    proposal = await governance.create_proposal(
        base_config_id=uuid.UUID(str(base["id"])), project_id=PROJECT_ID, created_by_expert_id=advisor_id
    )
    assert proposal["proposal_type"] == "qualitative_analysis"
    assert proposal["ahp_application_status"] is None
    assert proposal["proposed_hierarchy_snapshot"] is None


# --- B. Advisor AHP authoring -------------------------------------------------


async def test_draft_save_direct_mode_stores_snapshot_and_is_re_savable(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)

    weights_with_rationale = copy.deepcopy(DIRECT_HIERARCHICAL_WEIGHTS)
    weights_with_rationale["market"]["market_interest_rate"]["rationale"] = "Lãi suất cao làm giảm sức hút thị trường."
    saved = await governance.save_ahp_proposal_draft(
        proposal_id=uuid.UUID(str(proposal["id"])),
        actor_expert_id=advisor_id,
        mode="direct",
        direct_hierarchical_weights=weights_with_rationale,
    )
    assert saved["proposed_hierarchy_snapshot"]["mode"] == "direct"
    assert saved["proposed_hierarchy_snapshot"]["frozen_at"] is None
    assert sorted(saved["proposed_hierarchy_snapshot"]["selected_criteria"]) == [
        "area_accessibility",
        "market_interest_rate",
        "project_design_score",
    ]
    assert (
        saved["proposed_hierarchy_snapshot"]["hierarchical_weights"]["market"]["market_interest_rate"]["rationale"]
        == "Lãi suất cao làm giảm sức hút thị trường."
    )

    # Re-saving overwrites, never accumulates.
    resaved = await governance.save_ahp_proposal_draft(
        proposal_id=uuid.UUID(str(proposal["id"])),
        actor_expert_id=advisor_id,
        mode="direct",
        direct_hierarchical_weights=weights_with_rationale,
    )
    assert resaved["updated_at"] >= saved["updated_at"]


async def test_draft_save_rejects_an_unregistered_criterion(factory):
    """Rule 11 — `expert_location_score` is never a real active row in
    `ranking_feature_definitions` for any grain in this fixture (mirrors the
    live registry state audited in the mission's own audit pass)."""
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    bad_block = {
        **DIRECT_HIERARCHICAL_WEIGHTS,
        "project": {"expert_location_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}},
    }
    with pytest.raises(governance.GovernanceError) as excinfo:
        await governance.save_ahp_proposal_draft(
            proposal_id=uuid.UUID(str(proposal["id"])),
            actor_expert_id=advisor_id,
            mode="direct",
            direct_hierarchical_weights=bad_block,
        )
    assert excinfo.value.code == "UNREGISTERED_CRITERION"


async def test_draft_save_by_a_non_owner_is_rejected(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    other_id = await _expert("advisor-b")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    with pytest.raises(governance.GovernanceError) as excinfo:
        await governance.save_ahp_proposal_draft(
            proposal_id=uuid.UUID(str(proposal["id"])),
            actor_expert_id=other_id,
            mode="direct",
            direct_hierarchical_weights=DIRECT_HIERARCHICAL_WEIGHTS,
        )
    assert excinfo.value.code == "PROPOSAL_OWNER_REQUIRED"


async def test_submit_without_a_saved_draft_is_rejected(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    await _attach_direct_evidence(proposal_id=uuid.UUID(str(proposal["id"])), expert_id=advisor_id)
    with pytest.raises(governance.GovernanceError) as excinfo:
        await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=advisor_id)
    assert excinfo.value.code == "AHP_HIERARCHY_REQUIRED"


async def test_submit_freezes_the_snapshot(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    await governance.save_ahp_proposal_draft(
        proposal_id=uuid.UUID(str(proposal["id"])),
        actor_expert_id=advisor_id,
        mode="direct",
        direct_hierarchical_weights=DIRECT_HIERARCHICAL_WEIGHTS,
    )
    await _attach_direct_evidence(proposal_id=uuid.UUID(str(proposal["id"])), expert_id=advisor_id)
    submitted = await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=advisor_id)
    assert submitted["status"] == "submitted"
    assert submitted["proposed_hierarchy_snapshot"]["frozen_at"] is not None


async def test_submit_auto_links_all_ready_project_documents_without_mutating_documents(factory):
    """0052 captures every ready project document in the submit transaction."""
    base = await _published_base_config()
    advisor_id = await _expert("advisor-linked-evidence")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    proposal_id = uuid.UUID(str(proposal["id"]))
    await governance.save_ahp_proposal_draft(
        proposal_id=proposal_id,
        actor_expert_id=advisor_id,
        mode="direct",
        direct_hierarchical_weights=DIRECT_HIERARCHICAL_WEIGHTS,
    )
    document_ids = {
        await _attach_direct_evidence(proposal_id=None, expert_id=advisor_id),
        await _attach_direct_evidence(proposal_id=None, expert_id=advisor_id),
    }

    submitted = await governance.submit_proposal(proposal_id=proposal_id, actor_expert_id=advisor_id)
    assert submitted["status"] == "submitted"
    assert {row["id"] for row in await governance.list_linked_evidence_for_ahp_proposal(proposal_id)} == document_ids

    async with factory["factory"]() as session:
        document_proposal_ids = (
            await session.scalars(
                sa.select(ranking_evidence_documents.c.proposal_id).where(
                    ranking_evidence_documents.c.id.in_(document_ids)
                )
            )
        ).all()
        link_count = await session.scalar(
            sa.select(sa.func.count()).select_from(ranking_proposal_evidence_links).where(
                ranking_proposal_evidence_links.c.proposal_id == proposal_id,
            )
        )
        await session.rollback()
    assert document_proposal_ids == [None, None]
    assert link_count == 2


async def test_submit_without_ready_project_document_fails_closed_and_keeps_draft(factory):
    base = await _published_base_config()
    advisor_id = await _expert("advisor-no-ready-evidence")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    proposal_id = uuid.UUID(str(proposal["id"]))
    await governance.save_ahp_proposal_draft(
        proposal_id=proposal_id,
        actor_expert_id=advisor_id,
        mode="direct",
        direct_hierarchical_weights=DIRECT_HIERARCHICAL_WEIGHTS,
    )

    with pytest.raises(governance.GovernanceError, match="Báo cáo tư vấn chi tiết") as excinfo:
        await governance.submit_proposal(proposal_id=proposal_id, actor_expert_id=advisor_id)
    assert excinfo.value.code == "EVIDENCE_REQUIRED"
    assert (await governance.get_proposal(proposal_id))["status"] == "draft"


async def test_ahp_evidence_link_rejects_non_owner_and_document_without_current_extraction(factory):
    base = await _published_base_config()
    owner_id = await _expert("advisor-link-owner")
    other_id = await _expert("advisor-link-other")
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=owner_id)
    proposal_id = uuid.UUID(str(proposal["id"]))
    ready_document_id = await _attach_direct_evidence(proposal_id=None, expert_id=owner_id)

    with pytest.raises(governance.GovernanceError, match="người tạo") as excinfo:
        await governance.link_evidence_to_ahp_proposal(
            proposal_id=proposal_id,
            document_id=ready_document_id,
            actor_expert_id=other_id,
            enforce_owner=True,
        )
    assert excinfo.value.code == "PROPOSAL_OWNER_REQUIRED"

    not_ready = await governance.register_evidence_document(
        project_id=PROJECT_ID,
        proposal_id=None,
        uploaded_by_expert_id=owner_id,
        original_filename="not-ready.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="c" * 64,
        file_size_bytes=100,
    )
    with pytest.raises(governance.GovernanceError, match="chưa có extraction") as excinfo:
        await governance.link_evidence_to_ahp_proposal(
            proposal_id=proposal_id,
            document_id=uuid.UUID(str(not_ready["id"])),
            actor_expert_id=owner_id,
            enforce_owner=True,
        )
    assert excinfo.value.code == "EVIDENCE_NOT_READY"


# --- D. Approved AHP package → config → run -----------------------------------


async def _submitted_ahp_proposal(
    *, advisor_id: uuid.UUID, hierarchical_weights: dict = DIRECT_HIERARCHICAL_WEIGHTS
) -> tuple[dict, dict]:
    base = await _published_base_config()
    proposal = await _draft_ahp_proposal(base_config_id=base["id"], expert_id=advisor_id)
    await governance.save_ahp_proposal_draft(
        proposal_id=uuid.UUID(str(proposal["id"])),
        actor_expert_id=advisor_id,
        mode="direct",
        direct_hierarchical_weights=hierarchical_weights,
    )
    await _attach_direct_evidence(proposal_id=uuid.UUID(str(proposal["id"])), expert_id=advisor_id)
    submitted = await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=advisor_id)
    return base, submitted


async def test_zero_weight_project_grain_without_criteria_can_be_approved_and_complete_a_ranking_run(factory, monkeypatch):
    """A Project feature vector is optional only when its parent weight is zero.

    This covers the complete isolated workflow: draft validation, frozen
    submission, CEO approval, and completion of the bound ranking run.
    """
    advisor_id = await _expert("advisor-zero-project")
    _base, submitted = await _submitted_ahp_proposal(
        advisor_id=advisor_id,
        hierarchical_weights=ZERO_PROJECT_HIERARCHICAL_WEIGHTS,
    )
    assert submitted["proposed_hierarchy_snapshot"]["hierarchical_weights"]["project"] == {}

    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng cấu hình AHP không dùng khối Dự án.",
        reviewer_subject="ceo@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    assert reviewed["ahp_application_status"] == "queued"

    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("Settings", (), {"hierarchical_ranking_enabled": True})(),
    )
    result = await ranking_service.run_ranking(
        PROJECT_ID,
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])),
        trigger="config_change",
    )
    assert result.config_version_id == reviewed["proposed_config_id"]

    completed = await governance.get_proposal(uuid.UUID(str(reviewed["id"])))
    assert completed["status"] == "published"
    assert completed["ahp_application_status"] == "applied"


async def test_project_design_score_with_positive_project_weight_is_frozen_approved_and_run(factory, monkeypatch):
    """The new governed Project criterion supports the normal AHP lifecycle.

    The lightweight AHP fixture intentionally has no units, so the assertion
    here is the completed, config-bound run.  Numeric Project composition is
    covered by the scoring-suite scenario using the same feature key.
    """
    advisor_id = await _expert("advisor-project-design")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    hierarchy = submitted["proposed_hierarchy_snapshot"]["hierarchical_weights"]
    assert hierarchy["grain_weights"]["project"]["weight"] > 0
    assert set(hierarchy["project"]) == {"project_design_score"}

    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng tiêu chí thiết kế dự án.",
        reviewer_subject="ceo-project-design@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("Settings", (), {"hierarchical_ranking_enabled": True})(),
    )
    result = await ranking_service.run_ranking(
        PROJECT_ID,
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])),
        trigger="config_change",
    )
    assert result.config_version_id == reviewed["proposed_config_id"]
    completed = await governance.get_proposal(uuid.UUID(str(reviewed["id"])))
    assert completed["ahp_application_status"] == "applied"


async def test_submit_embeds_rationales_and_supports_exact_and_semantic_retrieval(factory, monkeypatch):
    weights = copy.deepcopy(ZERO_PROJECT_HIERARCHICAL_WEIGHTS)
    weights["market"]["market_interest_rate"]["rationale"] = "Lãi suất cao làm giảm sức hút nên được ưu tiên trong đánh giá thị trường."
    weights["area"]["area_accessibility"]["rationale"] = "Khả năng tiếp cận tốt hỗ trợ thanh khoản tại phân khu."

    def fake_embed(texts):
        return [([1.0] + [0.0] * 1535) if "lãi suất" in text.lower() else ([0.0, 1.0] + [0.0] * 1534) for text in texts]

    monkeypatch.setattr("src.services.evidence_extraction.embed_texts", fake_embed)
    advisor_id = await _expert("advisor-rationale")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id, hierarchical_weights=weights)

    exact = await governance.get_ahp_proposal_rationale(
        uuid.UUID(str(submitted["id"])), criterion_key="market_interest_rate"
    )
    assert len(exact) == 1
    assert exact[0]["rationale"] == weights["market"]["market_interest_rate"]["rationale"]
    assert exact[0]["chunk_text"].startswith("market.market_interest_rate weight=1.0:")

    semantic = await governance.get_ahp_proposal_rationale(uuid.UUID(str(submitted["id"])), query="Lãi suất")
    assert semantic[0]["criterion_key"] == "market_interest_rate"
    assert semantic[0]["similarity"] == pytest.approx(1.0)
    async with factory["factory"]() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(ranking_proposal_rationale_chunks)) == 2
        await session.rollback()


async def test_cross_proposal_rationale_retrieval_returns_project_scoped_chunks(factory, monkeypatch):
    def fake_embed(texts):
        return [[1.0] + [0.0] * 1535 for _ in texts]

    monkeypatch.setattr("src.services.evidence_extraction.embed_texts", fake_embed)
    advisor_id = await _expert("advisor-cross-rationale")
    first = copy.deepcopy(ZERO_PROJECT_HIERARCHICAL_WEIGHTS)
    first["market"]["market_interest_rate"]["rationale"] = "Lý do thị trường thứ nhất."
    second = copy.deepcopy(ZERO_PROJECT_HIERARCHICAL_WEIGHTS)
    second["area"]["area_accessibility"]["rationale"] = "Lý do phân khu thứ hai."
    await _submitted_ahp_proposal(advisor_id=advisor_id, hierarchical_weights=first)
    await _submitted_ahp_proposal(advisor_id=advisor_id, hierarchical_weights=second)

    results = await rationale_retrieval.retrieve_rationale_cross_proposals(PROJECT_ID, "Lý do", top_k=10)
    assert len(results) == 2
    assert {row["criterion_key"] for row in results} == {"market_interest_rate", "area_accessibility"}
    assert {row["project_id"] for row in results} == {str(PROJECT_ID)}


async def test_ceo_approval_atomically_binds_one_pending_run_then_marks_applied_only_after_completion(factory):
    advisor_id = await _expert("advisor-a")
    base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)

    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng cấu hình AHP mới cho dự án.",
        reviewer_subject="ceo@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )

    assert reviewed["status"] == "approved"
    assert reviewed["ahp_application_status"] == "queued"
    assert reviewed["applied_ranking_run_id"] is not None
    assert reviewed["proposed_config_id"] is not None
    assert reviewed["proposed_config_id"] != base["id"]

    async with factory["factory"]() as session:
        new_config = (
            await session.execute(
                sa.select(ranking_configs).where(ranking_configs.c.id == reviewed["proposed_config_id"])
            )
        ).mappings().first()
        old_config = (
            await session.execute(sa.select(ranking_configs).where(ranking_configs.c.id == base["id"]))
        ).mappings().first()
        queued_runs = (
            await session.execute(
                sa.select(ranking_runs).where(ranking_runs.c.project_id == PROJECT_ID, ranking_runs.c.status == "queued")
            )
        ).mappings().all()
        await session.rollback()

    assert new_config["status"] == "published"
    assert new_config["hierarchical_weights"] == DIRECT_HIERARCHICAL_WEIGHTS
    assert old_config["status"] == "archived"
    assert len(queued_runs) == 1
    assert str(queued_runs[0]["id"]) == str(reviewed["applied_ranking_run_id"])
    assert queued_runs[0]["config_version_id"] == reviewed["proposed_config_id"]
    assert queued_runs[0]["ahp_proposal_id"] == reviewed["id"]
    assert len(factory["queue"].calls) == 1

    # Queue insertion alone is never a completed application.
    await governance.finalize_ahp_application_run(
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])), succeeded=True
    )
    still_pending = await governance.get_proposal(uuid.UUID(str(reviewed["id"])))
    assert still_pending["ahp_application_status"] == "queued"

    async with factory["factory"]() as session:
        await session.execute(
            sa.update(ranking_runs)
            .where(ranking_runs.c.id == reviewed["applied_ranking_run_id"])
            .values(status="completed", finished_at=datetime.now(UTC))
        )
        await session.commit()
    await governance.finalize_ahp_application_run(
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])), succeeded=True
    )
    applied = await governance.get_proposal(uuid.UUID(str(reviewed["id"])))
    assert applied["status"] == "published"
    assert applied["ahp_application_status"] == "applied"


async def test_approval_with_an_unrelated_queued_run_publishes_once_and_defers_the_bound_intent(factory):
    """A sync run cannot roll back an approved frozen AHP configuration.

    The pre-existing sync run is pinned to the old config, while the one
    proposal-bound run is durable but deferred until project work is terminal.
    """
    advisor_id = await _expert("advisor-deferred")
    base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    prior_run_id = uuid.uuid4()
    async with factory["factory"]() as session:
        await session.execute(
            sa.insert(ranking_runs).values(
                id=prior_run_id,
                project_id=PROJECT_ID,
                trigger="sync",
                scope_type="project",
                scope_ids={},
                config_version_id=None,
                status="queued",
                attempt=0,
                enqueued_at=datetime.now(UTC),
            )
        )
        await session.commit()

    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng sau khi đồng bộ đang chờ hoàn tất.",
        reviewer_subject="ceo-deferred@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    assert reviewed["status"] == "approved"
    assert reviewed["ahp_application_status"] == "awaiting_prior_run"
    async with factory["factory"]() as session:
        prior = (await session.execute(sa.select(ranking_runs).where(ranking_runs.c.id == prior_run_id))).mappings().one()
        bound = (
            await session.execute(
                sa.select(ranking_runs).where(ranking_runs.c.id == reviewed["applied_ranking_run_id"])
            )
        ).mappings().one()
        await session.rollback()
    assert prior["config_version_id"] == base["id"]
    assert bound["status"] == "deferred"
    assert bound["config_version_id"] == reviewed["proposed_config_id"]
    assert bound["ahp_proposal_id"] == reviewed["id"]
    assert len(factory["queue"].calls) == 0

    # After the independent run becomes terminal, the narrow reconciliation
    # path can recover the post-commit handoff if the worker died before its
    # normal promotion. It promotes the same immutable intent and dispatches
    # exactly one RQ job; a repeated call cannot enqueue another job.
    async with factory["factory"]() as session:
        await session.execute(
            sa.update(ranking_runs)
            .where(ranking_runs.c.id == prior_run_id)
            .values(status="completed", finished_at=datetime.now(UTC))
        )
        await session.commit()
    ceo_id = await _expert("ceo-deferred@example.test")
    promoted = await ranking_run_recovery.reconcile_stuck_ranking_run(
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])),
        actor_identity_subject="ceo-deferred@example.test",
        actor_expert_id=ceo_id,
        reason="Khôi phục bàn giao sau khi worker trước đó đã kết thúc phiên ranking.",
    )
    assert promoted["changed"] is True
    assert promoted["reason_code"] == "DEFERRED_RUN_PROMOTED"
    assert promoted["run"]["status"] == "queued"
    assert len(factory["queue"].calls) == 1
    repeated = await ranking_run_recovery.reconcile_stuck_ranking_run(
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])),
        actor_identity_subject="ceo-deferred@example.test",
        actor_expert_id=ceo_id,
        reason="Gọi lại để kiểm chứng recovery không tạo thêm RQ job trùng lặp.",
    )
    assert repeated["changed"] is False
    assert len(factory["queue"].calls) == 1


async def test_apply_is_idempotent_on_retry(factory):
    advisor_id = await _expert("advisor-a")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng.",
        reviewer_subject="ceo@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    ceo_id = await _expert("ceo@example.test")

    retried = await governance._apply_ahp_proposal(uuid.UUID(str(reviewed["id"])), reviewer_expert_id=ceo_id)

    assert retried["proposed_config_id"] == reviewed["proposed_config_id"]
    assert retried["applied_ranking_run_id"] == reviewed["applied_ranking_run_id"]
    async with factory["factory"]() as session:
        config_count = await session.scalar(sa.select(sa.func.count()).select_from(ranking_configs))
        run_count = await session.scalar(sa.select(sa.func.count()).select_from(ranking_runs))
        await session.rollback()
    assert config_count == 2  # base + the one AHP-derived version, never a second
    assert run_count == 1
    assert len(factory["queue"].calls) == 1  # not enqueued again


async def test_concurrent_apply_is_serialized_to_one_config_and_one_bound_run(factory):
    """The proposal row lock serializes two workers; the DB unique index is
    the second, durable backstop against a duplicate proposal/run link."""
    advisor_id = await _expert("advisor-a")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    ceo_id = await _expert("ceo@example.test")
    proposal_id = uuid.UUID(str(submitted["id"]))
    now = datetime.now(UTC)
    async with factory["factory"]() as session:
        await session.execute(
            sa.update(ranking_weight_proposals)
            .where(ranking_weight_proposals.c.id == proposal_id)
            .values(status="approved", approved_at=now, ahp_application_status="pending", updated_at=now)
        )
        await session.commit()

    first, second = await asyncio.gather(
        governance._apply_ahp_proposal(proposal_id, reviewer_expert_id=ceo_id),
        governance._apply_ahp_proposal(proposal_id, reviewer_expert_id=ceo_id),
    )

    assert first["proposed_config_id"] == second["proposed_config_id"]
    assert first["applied_ranking_run_id"] == second["applied_ranking_run_id"]
    async with factory["factory"]() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(ranking_configs)) == 2
        assert await session.scalar(sa.select(sa.func.count()).select_from(ranking_runs)) == 1
        await session.rollback()
    assert len(factory["queue"].calls) == 1


async def test_database_refuses_a_second_run_bound_to_the_same_ahp_proposal(factory):
    advisor_id = await _expert("advisor-a")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng.",
        reviewer_subject="ceo@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )

    async with factory["factory"]() as session:
        with pytest.raises(sa.exc.IntegrityError) as excinfo:
            await session.execute(
                sa.insert(ranking_runs).values(
                    id=uuid.uuid4(),
                    project_id=PROJECT_ID,
                    trigger="config_change",
                    scope_type="project",
                    scope_ids={},
                    config_version_id=reviewed["proposed_config_id"],
                    ahp_proposal_id=reviewed["id"],
                    status="completed",
                    attempt=1,
                    enqueued_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
        assert "uq_ranking_runs_ahp_proposal_id" in str(excinfo.value)
        await session.rollback()


async def test_bound_application_run_never_resolves_the_active_config_at_worker_claim(factory, monkeypatch):
    advisor_id = await _expert("advisor-a")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng.",
        reviewer_subject="ceo@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )

    async def _active_config_must_not_be_called(*_args, **_kwargs):
        raise AssertionError("AHP proposal run must use its bound proposed_config_id, never the active config")

    monkeypatch.setattr("src.ranking.service._active_config", _active_config_must_not_be_called)
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("Settings", (), {"hierarchical_ranking_enabled": False})(),
    )
    result = await ranking_service.run_ranking(
        PROJECT_ID,
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])),
        trigger="config_change",
    )

    assert result.config_version_id == reviewed["proposed_config_id"]


async def test_failed_bound_run_marks_application_failed_without_claiming_publish(factory):
    advisor_id = await _expert("advisor-a")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng.",
        reviewer_subject="ceo@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    async with factory["factory"]() as session:
        await session.execute(
            sa.update(ranking_runs)
            .where(ranking_runs.c.id == reviewed["applied_ranking_run_id"])
            .values(status="failed", finished_at=datetime.now(UTC), error_summary={"message": "simulated"})
        )
        await session.commit()

    await governance.finalize_ahp_application_run(
        run_id=uuid.UUID(str(reviewed["applied_ranking_run_id"])), succeeded=False
    )
    failed = await governance.get_proposal(uuid.UUID(str(reviewed["id"])))
    assert failed["status"] == "approved"
    assert failed["ahp_application_status"] == "failed"


async def test_ceo_retry_reuses_the_same_failed_bound_run_without_duplicate_config_or_run(factory):
    advisor_id = await _expert("advisor-recovery")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng.",
        reviewer_subject="ceo-recovery@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    run_id = uuid.UUID(str(reviewed["applied_ranking_run_id"]))
    async with factory["factory"]() as session:
        await session.execute(
            sa.update(ranking_runs)
            .where(ranking_runs.c.id == run_id)
            .values(status="failed", finished_at=datetime.now(UTC), error_summary={"code": "WORKER_BOOTSTRAP_FAILED"})
        )
        await session.commit()
    await governance.finalize_ahp_application_run(run_id=run_id, succeeded=False)
    ceo_id = await _expert("ceo-recovery@example.test")

    retried = await governance.retry_ahp_application(
        proposal_id=uuid.UUID(str(reviewed["id"])),
        actor_expert_id=ceo_id,
        actor_subject="ceo-recovery@example.test",
        actor_is_ceo=True,
        reason="Khôi phục sau khi đã xác minh lỗi bootstrap worker đã được sửa.",
    )
    assert retried["ahp_application_status"] == "queued"
    assert retried["applied_ranking_run_id"] == reviewed["applied_ranking_run_id"]
    async with factory["factory"]() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(ranking_configs)) == 2
        assert await session.scalar(sa.select(sa.func.count()).select_from(ranking_runs)) == 1
        row = (await session.execute(sa.select(ranking_runs).where(ranking_runs.c.id == run_id))).mappings().one()
        await session.rollback()
    assert row["status"] == "queued"


async def test_reconcile_marks_only_a_stale_orphaned_run_failed_and_is_idempotent(factory, monkeypatch):
    stale_run_id = uuid.uuid4()
    async with factory["factory"]() as session:
        await session.execute(
            sa.insert(ranking_runs).values(
                id=stale_run_id,
                project_id=PROJECT_ID,
                trigger="sync",
                scope_type="project",
                scope_ids={},
                status="queued",
                attempt=0,
                enqueued_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await session.commit()
    monkeypatch.setattr("src.services.ranking_run_recovery.queue_job_state", lambda _job_id: "missing")
    monkeypatch.setattr(
        "src.services.ranking_run_recovery.get_settings",
        lambda: type("Settings", (), {"ranking_run_stale_seconds": 60})(),
    )
    actor_id = await _expert("ceo-reconcile@example.test")
    first = await ranking_run_recovery.reconcile_stuck_ranking_run(
        run_id=stale_run_id,
        actor_identity_subject="ceo-reconcile@example.test",
        actor_expert_id=actor_id,
        reason="Xác minh job RQ đã biến mất sau lỗi khởi động worker.",
    )
    assert first["changed"] is True
    assert first["run"]["status"] == "failed"
    second = await ranking_run_recovery.reconcile_stuck_ranking_run(
        run_id=stale_run_id,
        actor_identity_subject="ceo-reconcile@example.test",
        actor_expert_id=actor_id,
        reason="Lần gọi lặp lại để kiểm idempotency của recovery.",
    )
    assert second["changed"] is False
    assert second["reason_code"] == "RUN_NOT_RECONCILABLE"


async def test_reconcile_preserves_a_healthy_queued_rq_job(factory, monkeypatch):
    run_id = uuid.uuid4()
    async with factory["factory"]() as session:
        await session.execute(
            sa.insert(ranking_runs).values(
                id=run_id,
                project_id=PROJECT_ID,
                trigger="sync",
                scope_type="project",
                scope_ids={},
                status="queued",
                attempt=0,
                rq_job_id="healthy-job",
                enqueued_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await session.commit()
    monkeypatch.setattr("src.services.ranking_run_recovery.queue_job_state", lambda _job_id: "scheduled")
    actor_id = await _expert("ceo-healthy-run@example.test")
    result = await ranking_run_recovery.reconcile_stuck_ranking_run(
        run_id=run_id,
        actor_identity_subject="ceo-healthy-run@example.test",
        actor_expert_id=actor_id,
        reason="Kiểm tra job retry đã được RQ schedule hợp lệ.",
    )
    assert result["changed"] is False
    assert result["reason_code"] == "RQ_JOB_STILL_LIVE"


async def test_apply_failure_keeps_approval_and_reports_an_honest_status(factory, monkeypatch):
    advisor_id = await _expert("advisor-a")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated queued-run insert failure")

    monkeypatch.setattr("src.ranking.service.enqueue_ahp_application_run_in_session", _boom, raising=False)

    reviewed = await governance.submit_review(
        proposal_id=uuid.UUID(str(submitted["id"])),
        decision="approved",
        comment="Đồng ý áp dụng.",
        reviewer_subject="ceo@example.test",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )

    # The CEO's approval itself is never undone by a downstream apply failure.
    assert reviewed["status"] == "approved"
    assert reviewed["ahp_application_status"] == "failed"
    assert reviewed["proposed_config_id"] is None
    assert reviewed["applied_ranking_run_id"] is None
    assert len(factory["queue"].calls) == 0


async def test_apply_refuses_when_hierarchical_scoring_is_disabled_before_any_config_or_run(factory, monkeypatch):
    advisor_id = await _expert("advisor-a")
    _base, submitted = await _submitted_ahp_proposal(advisor_id=advisor_id)
    monkeypatch.setattr(
        "src.services.governance.get_settings",
        lambda: type("Settings", (), {"hierarchical_ranking_enabled": False})(),
    )

    with pytest.raises(governance.GovernanceError) as excinfo:
        await governance.submit_review(
            proposal_id=uuid.UUID(str(submitted["id"])),
            decision="approved",
            comment="Đồng ý áp dụng.",
            reviewer_subject="ceo@example.test",
            reviewer_is_ceo=True,
            evidence_review_acknowledged=True,
        )

    assert excinfo.value.code == "HIERARCHICAL_RANKING_DISABLED"
    current = await governance.get_proposal(uuid.UUID(str(submitted["id"])))
    assert current["status"] == "submitted"
    assert current["ahp_application_status"] is None
    assert current["proposed_config_id"] is None
    assert current["applied_ranking_run_id"] is None
    async with factory["factory"]() as session:
        assert await session.scalar(sa.select(sa.func.count()).select_from(ranking_configs)) == 1
        assert await session.scalar(sa.select(sa.func.count()).select_from(ranking_runs)) == 0
        await session.rollback()


async def test_qualitative_approval_still_requires_proposed_config_id_and_never_touches_ahp_status(factory):
    """Regression guard: the `PROPOSED_CONFIG_MISSING` exception carved out
    for `ahp_ranking_proposal` must not leak into the pre-existing legacy
    weight-mode flow — a qualitative/legacy weight-mode proposal with no
    `proposed_config_id` must still be blocked exactly as before."""
    base = await _published_base_config()
    advisor_id = await _expert("advisor-a")
    proposal = await governance.create_proposal(
        base_config_id=uuid.UUID(str(base["id"])), project_id=PROJECT_ID, created_by_expert_id=advisor_id
    )
    await _attach_direct_evidence(proposal_id=uuid.UUID(str(proposal["id"])), expert_id=advisor_id)
    submitted = await governance.submit_proposal(proposal_id=uuid.UUID(str(proposal["id"])), actor_expert_id=advisor_id)

    with pytest.raises(governance.GovernanceError) as excinfo:
        await governance.submit_review(
            proposal_id=uuid.UUID(str(submitted["id"])),
            decision="approved",
            comment="Đồng ý.",
            reviewer_subject="ceo@example.test",
            reviewer_is_ceo=True,
            evidence_review_acknowledged=True,
        )
    assert excinfo.value.code == "PROPOSED_CONFIG_MISSING"
