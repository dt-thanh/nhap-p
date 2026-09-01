"""`src/services/governance.py` — vòng đời đề xuất trọng số (P5, audit 2026-08-25).

Chạy bằng `bash scripts/test_db.sh` — cùng quy ước với mọi test DB thật khác
trong repo (xem `tests/test_services/test_absorption.py`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
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
    ranking_feature_definitions,
    ranking_feature_rubrics,
)
from src.services import evidence_extraction, governance, ranking_config

TEST_DATABASE_URL = None  # resolved in _skip_reason, see below


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
FEATURE_ID = uuid.uuid4()
AREA_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def factory(monkeypatch):
    import os

    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    for target in (
        "src.services.evidence_extraction.get_session_factory",
        "src.services.governance.get_session_factory",
        "src.services.ranking_config.get_session_factory",
    ):
        monkeypatch.setattr(target, lambda sf=session_factory: sf, raising=False)

    tables = (
        "ranking_evidence_document_features",
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
                name="Governance Test Project",
                launch_date=date(2026, 1, 1),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id=f"P-GOV-{uuid.uuid4().hex[:8]}",
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
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=FEATURE_ID,
                feature_key="unit_available",
                feature_version="v1",
                name="Unit available",
                category="operational",
                grain="unit",
                value_type="numeric",
                formula_id="unit_available_v1",
                normalization_method="identity",
                direction="positive",
                missing_policy="skip",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    yield session_factory

    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    await engine.dispose()


async def _base_config(factory) -> uuid.UUID:
    row = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="base",
        created_by="test",
    )
    return uuid.UUID(str(row["id"]))


async def _expert(identity_subject: str = "expert-1") -> uuid.UUID:
    row = await governance.get_or_create_expert_profile(identity_subject=identity_subject)
    return row["id"]


async def _attach_extracted_evidence(factory, *, expert_id: uuid.UUID, justification_id: uuid.UUID) -> uuid.UUID:
    """Registers a document, links it to `justification_id`, and gives it one
    real chunk row — mirrors what a successful extraction job would leave
    behind. Weight-mode `submit_proposal` (mandatory-scope item 3) now
    requires exactly this before it will accept a submission."""
    doc = await governance.register_evidence_document(
        project_id=PROJECT_ID,
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="evidence.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="a" * 64,
        file_size_bytes=100,
    )
    document_id = uuid.UUID(str(doc["id"]))
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id,
        [
            {
                "chunk_index": 0,
                "page_number": 1,
                "content": "Real evidence content for testing.",
                "token_count": 6,
                "embedding_model": "text-embedding-3-small",
                "embedding": [0.001] * 1536,
            }
        ],
    )
    await governance.link_evidence_to_justification(document_id=document_id, feature_justification_id=justification_id)
    return document_id


async def test_unscoped_evidence_is_rejected_with_document_project_unscoped(factory):
    """A legacy standalone document without durable project ownership must not
    be accepted as new governance evidence, even when extraction is complete."""
    base_id = await _base_config(factory)
    expert_id = await _expert("unscoped-evidence-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="legacy-standalone.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="c" * 64,
        file_size_bytes=100,
    )
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        uuid.UUID(str(doc["id"])),
        [{"chunk_index": 0, "page_number": 1, "content": "legacy", "token_count": 1,
          "embedding_model": "text-embedding-3-small", "embedding": [0.001] * 1536}],
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.link_evidence_to_justification(
            document_id=uuid.UUID(str(doc["id"])),
            feature_justification_id=uuid.UUID(str(justification["id"])),
        )
    assert exc.value.code == "DOCUMENT_PROJECT_UNSCOPED"


JUSTIFICATION_KWARGS = dict(
    rationale="Sales velocity has increased 20% QoQ per Q2 report.",
    methodology="Comparative analysis against 3 comparable projects.",
    evidence_summary="See attached Q2 2026 Market Analysis, page 4.",
    expected_effect="increase",
    confidence="medium",
    limitations="Single-quarter data, seasonal effect not isolated.",
)


# --- Chuyên gia ------------------------------------------------------------------


async def test_get_or_create_expert_profile_is_idempotent_on_identity_subject(factory):
    first = await governance.get_or_create_expert_profile(identity_subject="alice@example.com", organization="Acme")
    second = await governance.get_or_create_expert_profile(identity_subject="alice@example.com")
    assert first["id"] == second["id"]
    assert second["organization"] == "Acme"  # bản ghi đầu tiên thắng, không bị ghi đè


async def test_get_expert_profile_missing_raises(factory):
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.get_expert_profile(uuid.uuid4())
    assert exc.value.code == "EXPERT_NOT_FOUND"


# --- Đề xuất -----------------------------------------------------------------------


async def test_create_proposal_starts_in_draft(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert()
    row = await governance.create_proposal(base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id)
    assert row["status"] == "draft"
    assert row["submitted_at"] is None


async def test_advisor_owner_enforcement_rejects_another_expert(factory):
    base_id = await _base_config(factory)
    owner_id = await _expert("advisor-owner")
    other_id = await _expert("advisor-other")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=owner_id
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(
            proposal_id=proposal["id"], actor_expert_id=other_id, enforce_owner=True
        )
    assert exc.value.code == "PROPOSAL_OWNER_REQUIRED"


async def test_non_advisor_compatibility_can_be_explicitly_preserved(factory):
    base_id = await _base_config(factory)
    owner_id = await _expert("operator-owner")
    other_id = await _expert("operator-actor")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=owner_id
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(
            proposal_id=proposal["id"], actor_expert_id=other_id, enforce_owner=False
        )
    assert exc.value.code == "EVIDENCE_REQUIRED"


async def test_weight_mode_submit_without_justification_or_evidence_is_rejected(factory):
    """Weight-mode does NOT require a justification (fixed this pass — see
    `submit_proposal`'s docstring for why: no `ranking_feature_definitions`
    row exists for flat/operational features, discovered via live E2E
    testing). With neither a justification nor any evidence at all, the
    failure is `EVIDENCE_REQUIRED`, not `NO_JUSTIFICATIONS`."""
    base_id = await _base_config(factory)
    expert_id = await _expert()
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "EVIDENCE_REQUIRED"


async def test_full_lifecycle_draft_to_published(factory):
    """draft → (justification) → submitted → (set_proposed_config) →
    approved → published — mirroring exactly the sequence
    `ranking_v2_ahp.md` §3 already established: config draft/publish stays
    on `src/services/ranking_config.py`, governance.py only references it."""
    base_id = await _base_config(factory)
    expert_id = await _expert("author")

    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=Decimal("1.0"),
        proposed_weight=Decimal("0.9"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    assert justification["proposal_id"] == proposal["id"]
    await _attach_extracted_evidence(factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"])))

    submitted = await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert submitted["status"] == "submitted"

    # A reviewer cannot approve before a proposed_config_id is attached.
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="approved",
            comment="lgtm",
            reviewer_subject="reviewer",
            reviewer_is_ceo=True,
            evidence_review_acknowledged=True,
        )
    assert exc.value.code == "PROPOSED_CONFIG_MISSING"

    proposed_config = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="proposed by expert",
        created_by="test",
    )
    linked = await governance.set_proposed_config(
        proposal_id=proposal["id"], proposed_config_id=uuid.UUID(str(proposed_config["id"])), actor_expert_id=expert_id
    )
    assert str(linked["proposed_config_id"]) == str(proposed_config["id"])

    reviewed = await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="Looks solid.",
        reviewer_subject="reviewer",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    assert reviewed["status"] == "approved"
    assert reviewed["approved_at"] is not None
    assert str(reviewed["created_by_expert_id"]) == str(expert_id)  # sanity: author unaffected by review

    # mark_published refuses until the underlying config is ACTUALLY published
    # — governance.py never writes ranking_configs itself.
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.mark_published(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "CONFIG_NOT_PUBLISHED"

    await ranking_config.publish(version=proposed_config["version"], published_by="test")

    published = await governance.mark_published(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert published["status"] == "published"
    assert published["published_at"] is not None


# --- Mandatory-scope item 6: publish blocked until the linked proposal is --
# --- CEO-approved (a proposal-originated config could otherwise be --------
# --- published directly, bypassing the whole evidence/CEO workflow) -------


async def test_publish_blocked_while_linked_proposal_is_still_draft(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("publish-guard-author-1")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    proposed_config = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="proposed",
        created_by="test",
    )
    await governance.set_proposed_config(
        proposal_id=proposal["id"],
        proposed_config_id=uuid.UUID(str(proposed_config["id"])),
        actor_expert_id=expert_id,
    )
    with pytest.raises(ranking_config.ConfigError) as exc:
        await ranking_config.publish(version=proposed_config["version"], published_by="test")
    assert exc.value.code == "PROPOSAL_NOT_APPROVED"


async def test_publish_blocked_while_linked_proposal_is_only_submitted(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("publish-guard-author-2")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    await _attach_extracted_evidence(factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"])))
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    proposed_config = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="proposed",
        created_by="test",
    )
    await governance.set_proposed_config(
        proposal_id=proposal["id"],
        proposed_config_id=uuid.UUID(str(proposed_config["id"])),
        actor_expert_id=expert_id,
    )
    with pytest.raises(ranking_config.ConfigError) as exc:
        await ranking_config.publish(version=proposed_config["version"], published_by="test")
    assert exc.value.code == "PROPOSAL_NOT_APPROVED"

    # Once approved, publish succeeds — same config, no other change.
    await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="lgtm",
        reviewer_subject="publish-guard-reviewer-2",
        reviewer_is_ceo=True,
        evidence_review_acknowledged=True,
    )
    published = await ranking_config.publish(version=proposed_config["version"], published_by="test")
    assert published["status"] == "published"


async def test_publish_with_no_linked_proposal_is_unaffected(factory):
    """The admin/bootstrap path (no proposal at all, e.g.
    `scripts/enable_hierarchical_ranking.py`) must keep working exactly as
    before — this gate only fires when a proposal actually references the
    config being published."""
    draft = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="bootstrap, no proposal",
        created_by="test",
    )
    published = await ranking_config.publish(version=draft["version"], published_by="test")
    assert published["status"] == "published"


async def test_request_changes_is_not_a_supported_review_decision(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author2")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    await _attach_extracted_evidence(factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"])))
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="request_changes",
            comment="Needs more evidence.",
            reviewer_subject="reviewer2",
            reviewer_is_ceo=True,
        )
    assert exc.value.code == "DECISION_INVALID"


async def test_request_changes_is_rejected_before_state_change(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author3")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    await _attach_extracted_evidence(factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"])))
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="request_changes",
            comment="More data please.",
            reviewer_subject="reviewer3",
            reviewer_is_ceo=True,
        )
    assert exc.value.code == "DECISION_INVALID"


# --- D18 close-out: weight-mode review now requires the SAME CEO/self- ------
# --- approval gate value-mode already had (previously an explicitly --------
# --- disclosed, unaddressed gap — see test_governance_value_mode.py's ------
# --- `test_weight_mode_review_now_requires_ceo_and_forbids_self_approval`) --


async def _weight_proposal_ready_for_review(factory, *, author_subject: str) -> dict:
    base_id = await _base_config(factory)
    expert_id = await _expert(author_subject)
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    await _attach_extracted_evidence(factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"])))
    return await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)


# --- Mandatory-scope item 3: weight-mode submit now requires real, --------
# --- extracted evidence (previously no requirement existed at all) --------


async def test_weight_mode_submit_without_any_evidence_is_rejected(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("no-evidence-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "EVIDENCE_REQUIRED"


async def test_weight_mode_submit_with_unextracted_evidence_is_still_rejected(factory):
    """A document merely uploaded and linked, with no chunk yet (extraction
    never run/still pending), is not yet usable evidence — only a document
    with at least one real extracted chunk satisfies the gate."""
    base_id = await _base_config(factory)
    expert_id = await _expert("unextracted-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="not-yet-extracted.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="b" * 64,
        file_size_bytes=100,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.link_evidence_to_justification(
            document_id=uuid.UUID(str(doc["id"])), feature_justification_id=uuid.UUID(str(justification["id"]))
        )
    assert exc.value.code == "EVIDENCE_NOT_READY"


async def test_weight_mode_submit_with_extracted_evidence_succeeds(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("with-evidence-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    await _attach_extracted_evidence(factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"])))
    submitted = await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert submitted["status"] == "submitted"


async def test_weight_mode_submit_succeeds_with_evidence_linked_directly_to_the_proposal_no_justification(factory):
    """The real path the frontend actually uses: upload a document with
    `proposal_id` set directly (`register_evidence_document(proposal_id=...)`),
    never calling `upsert_justification` at all — this must be sufficient
    ("general project rationale" evidence, mission's own vocabulary), since
    flat/operational feature keys have no `ranking_feature_definitions` row
    to reference in a justification in the first place."""
    base_id = await _base_config(factory)
    expert_id = await _expert("direct-evidence-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    doc = await governance.register_evidence_document(
        proposal_id=proposal["id"],
        uploaded_by_expert_id=expert_id,
        original_filename="general-rationale.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="8" * 64,
        file_size_bytes=100,
    )
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        uuid.UUID(str(doc["id"])),
        [{"chunk_index": 0, "page_number": 1, "content": "General project rationale, no per-feature justification.",
          "token_count": 8, "embedding_model": "text-embedding-3-small", "embedding": [0.001] * 1536}],
    )

    submitted = await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert submitted["status"] == "submitted"


async def test_weight_mode_submit_rejects_direct_evidence_from_an_archived_document(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("direct-evidence-archived-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    doc = await governance.register_evidence_document(
        proposal_id=proposal["id"],
        uploaded_by_expert_id=expert_id,
        original_filename="archived-rationale.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="9" * 64,
        file_size_bytes=100,
    )
    document_id = uuid.UUID(str(doc["id"]))
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id,
        [{"chunk_index": 0, "page_number": 1, "content": "Will be archived before submit.", "token_count": 6,
          "embedding_model": "text-embedding-3-small", "embedding": [0.001] * 1536}],
    )
    await governance.archive_document(document_id=document_id, actor_expert_id=expert_id)

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "EVIDENCE_REQUIRED"


async def test_weight_mode_review_without_identity_is_rejected(factory):
    proposal = await _weight_proposal_ready_for_review(factory, author_subject="w-author-1")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(proposal_id=proposal["id"], decision="rejected", comment="no identity")
    assert exc.value.code == "IDENTITY_REQUIRED"


async def test_weight_mode_review_requires_ceo(factory):
    proposal = await _weight_proposal_ready_for_review(factory, author_subject="w-author-2")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="rejected",
            comment="admin, not CEO",
            reviewer_subject="admin-not-ceo",
            reviewer_is_ceo=False,
        )
    assert exc.value.code == "CEO_APPROVAL_REQUIRED"


async def test_weight_mode_author_cannot_approve_own_proposal(factory):
    proposal = await _weight_proposal_ready_for_review(factory, author_subject="w-author-3")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="rejected",
            comment="self-approving",
            reviewer_subject="w-author-3",  # same subject as the author
            reviewer_is_ceo=True,
        )
    assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


async def test_weight_mode_ceo_can_reject(factory):
    proposal = await _weight_proposal_ready_for_review(factory, author_subject="w-author-4")
    reviewed = await governance.submit_review(
        proposal_id=proposal["id"],
        decision="rejected",
        comment="Not enough evidence.",
        reviewer_subject="real-ceo",
        reviewer_is_ceo=True,
    )
    assert reviewed["status"] == "rejected"


async def test_withdraw_from_draft_is_terminal(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author4")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    withdrawn = await governance.withdraw_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert withdrawn["status"] == "withdrawn"
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "PROPOSAL_STATUS_INVALID"


# --- Justification validation -----------------------------------------------------


async def test_justification_rejects_unknown_expected_effect(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author5")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    bad_kwargs = {**JUSTIFICATION_KWARGS, "expected_effect": "skyrocket"}
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=FEATURE_ID,
            previous_weight=None,
            proposed_weight=Decimal("0.5"),
            created_by_expert_id=expert_id,
            **bad_kwargs,
        )
    assert exc.value.code == "EXPECTED_EFFECT_INVALID"


async def test_justification_cannot_be_edited_after_submission(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author6")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    await _attach_extracted_evidence(factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"])))
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=FEATURE_ID,
            previous_weight=None,
            proposed_weight=Decimal("0.6"),
            created_by_expert_id=expert_id,
            **JUSTIFICATION_KWARGS,
        )
    assert exc.value.code == "PROPOSAL_STATUS_INVALID"


async def test_get_justification_by_its_own_id(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author7")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    created = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )

    found = await governance.get_justification(created["id"])

    assert found is not None
    assert found["id"] == created["id"]
    assert found["proposal_id"] == proposal["id"]


async def test_get_justification_unknown_id_returns_none(factory):
    assert await governance.get_justification(uuid.uuid4()) is None


# --- Bằng chứng --------------------------------------------------------------------


async def test_evidence_document_upload_and_link(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author7")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    doc = await governance.register_evidence_document(
        proposal_id=proposal["id"],
        uploaded_by_expert_id=expert_id,
        original_filename="Q2 2026 Market Analysis.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="a" * 64,
        file_size_bytes=1024,
    )
    assert doc["extraction_status"] == "not_requested"

    await evidence_extraction.insert_chunks_and_mark_succeeded(
        uuid.UUID(str(doc["id"])),
        [{"chunk_index": 0, "page_number": 1, "content": "Q2 evidence.", "token_count": 2,
          "embedding_model": "text-embedding-3-small", "embedding": [0.001] * 1536}],
    )

    await governance.link_evidence_to_justification(
        document_id=uuid.UUID(str(doc["id"])), feature_justification_id=uuid.UUID(str(justification["id"]))
    )
    linked = await governance.list_documents_for_justification(uuid.UUID(str(justification["id"])))
    assert len(linked) == 1
    assert linked[0]["id"] == doc["id"]

    # Idempotent — linking the same pair twice does not raise.
    await governance.link_evidence_to_justification(
        document_id=uuid.UUID(str(doc["id"])), feature_justification_id=uuid.UUID(str(justification["id"]))
    )
    assert len(await governance.list_documents_for_justification(uuid.UUID(str(justification["id"])))) == 1


async def test_find_document_by_checksum(factory):
    expert_id = await _expert("checksum-author")
    checksum = "d" * 64
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="report.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum=checksum,
        file_size_bytes=100,
    )
    found = await governance.find_document_by_checksum(checksum)
    assert found is not None
    assert found["id"] == doc["id"]
    assert await governance.find_document_by_checksum("e" * 64) is None


async def test_list_documents_by_project_joins_through_proposal(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("list-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    linked = await governance.register_evidence_document(
        proposal_id=proposal["id"],
        uploaded_by_expert_id=expert_id,
        original_filename="linked.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="f" * 64,
        file_size_bytes=100,
    )
    standalone = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="standalone.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="1" * 64,
        file_size_bytes=100,
    )

    by_project = await governance.list_documents(project_id=PROJECT_ID)
    assert [row["id"] for row in by_project] == [linked["id"]], "a standalone document has no project and must not appear"

    by_expert = await governance.list_documents(uploaded_by_expert_id=expert_id)
    ids = {row["id"] for row in by_expert}
    assert ids == {linked["id"], standalone["id"]}, "the expert's own view must include standalone uploads too"


async def test_project_scoped_standalone_evidence_is_listed_without_a_proposal(factory):
    expert_id = await _expert("project-owned-document")
    document = await governance.register_evidence_document(
        project_id=PROJECT_ID,
        area_id=AREA_ID,
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="project-owned.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="2" * 64,
        file_size_bytes=100,
    )
    assert await governance.get_document_project_id(document["id"]) == PROJECT_ID
    rows = await governance.list_documents(project_id=PROJECT_ID)
    assert [row["id"] for row in rows] == [document["id"]]


async def test_list_documents_requires_a_scope(factory):
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.list_documents()
    assert exc.value.code == "SCOPE_REQUIRED"


async def test_list_audit_events_by_proposal(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("audit-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    await governance.register_evidence_document(
        proposal_id=proposal["id"],
        uploaded_by_expert_id=expert_id,
        original_filename="audit.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="2" * 64,
        file_size_bytes=100,
    )
    events = await governance.list_audit_events(proposal_id=proposal["id"])
    event_types = {event["event_type"] for event in events}
    assert "created" in event_types  # create_proposal's own audit event
    assert "submitted" in event_types  # register_evidence_document's own audit event
    assert all(event["proposal_id"] == proposal["id"] for event in events)

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.list_audit_events()
    assert exc.value.code == "SCOPE_REQUIRED"


async def test_duplicate_object_storage_key_is_rejected(factory):
    expert_id = await _expert("author8")
    key = f"evidence/{uuid.uuid4().hex}.pdf"
    await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="a.pdf",
        mime_type="application/pdf",
        object_storage_key=key,
        sha256_checksum="b" * 64,
        file_size_bytes=10,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.register_evidence_document(
            proposal_id=None,
            uploaded_by_expert_id=expert_id,
            original_filename="b.pdf",
            mime_type="application/pdf",
            object_storage_key=key,
            sha256_checksum="c" * 64,
            file_size_bytes=20,
        )
    assert exc.value.code == "DUPLICATE_OBJECT_STORAGE_KEY"


async def test_evidence_document_rejects_unsupported_mime_type(factory):
    expert_id = await _expert("author9")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.register_evidence_document(
            proposal_id=None,
            uploaded_by_expert_id=expert_id,
            original_filename="a.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            object_storage_key=f"evidence/{uuid.uuid4().hex}.docx",
            sha256_checksum="d" * 64,
            file_size_bytes=10,
        )
    assert exc.value.code == "MIME_TYPE_INVALID"


# --- Rubric (0046) -------------------------------------------------------------

VALID_BANDS = [
    {"value": "0.00", "label": "no evidence", "evidence_requirement": "none found"},
    {"value": "0.25", "label": "weak", "evidence_requirement": "one unconfirmed source"},
    {"value": "0.50", "label": "moderate", "evidence_requirement": "one confirmed source"},
    {"value": "0.75", "label": "strong", "evidence_requirement": "official approval"},
    {"value": "1.00", "label": "very strong", "evidence_requirement": "operational/observed"},
]


async def test_create_feature_rubric_persists_five_bands_in_order(factory):
    rubric = await governance.create_feature_rubric(
        feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="admin-test"
    )
    assert rubric["rubric_version"] == 1
    assert [str(b["band_value"]) for b in rubric["bands"]] == ["0.0000", "0.2500", "0.5000", "0.7500", "1.0000"]
    assert [b["display_order"] for b in rubric["bands"]] == [0, 1, 2, 3, 4]
    assert rubric["bands"][2]["label"] == "moderate"


async def test_create_feature_rubric_auto_increments_version(factory):
    first = await governance.create_feature_rubric(
        feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="admin-test"
    )
    second = await governance.create_feature_rubric(
        feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="admin-test"
    )
    assert first["rubric_version"] == 1
    assert second["rubric_version"] == 2
    assert first["id"] != second["id"]


async def test_get_current_feature_rubric_returns_the_highest_version(factory):
    await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="admin-test")
    second = await governance.create_feature_rubric(
        feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="admin-test"
    )
    current = await governance.get_current_feature_rubric(FEATURE_ID)
    assert current["id"] == second["id"]
    assert current["rubric_version"] == 2


async def test_get_current_feature_rubric_returns_none_when_no_rubric_exists(factory):
    assert await governance.get_current_feature_rubric(FEATURE_ID) is None


async def test_list_feature_rubrics_returns_full_history_oldest_first(factory):
    first = await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="a")
    second = await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="b")
    history = await governance.list_feature_rubrics(FEATURE_ID)
    assert [h["id"] for h in history] == [first["id"], second["id"]]


async def test_create_feature_rubric_rejects_missing_band(factory):
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_feature_rubric(
            feature_definition_id=FEATURE_ID, bands=VALID_BANDS[:4], created_by="admin-test"
        )
    assert exc.value.code == "RUBRIC_BAND_MISSING"


async def test_create_feature_rubric_rejects_out_of_scale_band_value(factory):
    bad_bands = [*VALID_BANDS[:4], {"value": "0.60", "label": "x", "evidence_requirement": "x"}]
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=bad_bands, created_by="admin-test")
    assert exc.value.code == "RUBRIC_BAND_VALUE_INVALID"


async def test_create_feature_rubric_rejects_duplicate_band_value(factory):
    bad_bands = [*VALID_BANDS[:4], {"value": "0.75", "label": "dup", "evidence_requirement": "dup"}]
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=bad_bands, created_by="admin-test")
    assert exc.value.code == "RUBRIC_BAND_DUPLICATE"


async def test_create_feature_rubric_rejects_blank_label(factory):
    bad_bands = [*VALID_BANDS[:4], {"value": "1.00", "label": "  ", "evidence_requirement": "x"}]
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=bad_bands, created_by="admin-test")
    assert exc.value.code == "RUBRIC_BAND_LABEL_REQUIRED"


async def test_create_feature_rubric_rejects_unknown_feature(factory):
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_feature_rubric(feature_definition_id=uuid.uuid4(), bands=VALID_BANDS, created_by="admin-test")
    assert exc.value.code == "FEATURE_DEFINITION_NOT_FOUND"


async def test_create_feature_rubric_rejects_categorical_feature(factory):
    async with factory() as session:
        legal_id = uuid.uuid4()
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=legal_id,
                feature_key="project_legal_status",
                feature_version="v1",
                name="Legal",
                category="legal",
                grain="project",
                value_type="categorical",
                formula_id="expert_value_assertion",
                normalization_method="none",
                direction="neutral",
                missing_policy="skip",
                status="active",
                definition_metadata={"allowed_categorical_values": ["HIGH_RISK", "NOT_HIGH_RISK", "UNKNOWN"]},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_feature_rubric(feature_definition_id=legal_id, bands=VALID_BANDS, created_by="admin-test")
    assert exc.value.code == "RUBRIC_REQUIRES_NUMERIC_FEATURE"


async def test_create_feature_rubric_rejects_blank_created_by(factory):
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="  ")
    assert exc.value.code == "CREATED_BY_REQUIRED"


async def test_feature_rubric_tables_are_append_only(factory):
    rubric = await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="admin-test")
    async with factory() as session:
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    sa.update(ranking_feature_rubrics)
                    .where(ranking_feature_rubrics.c.id == uuid.UUID(str(rubric["id"])))
                    .values(rubric_version=99)
                )


# --- Mandatory-scope item 4: document archive/delete lifecycle -------------


async def test_new_document_is_active_with_no_lifecycle_events(factory):
    expert_id = await _expert("lifecycle-author-1")
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="fresh.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="e" * 64,
        file_size_bytes=10,
    )
    assert await governance.latest_lifecycle_status(uuid.UUID(str(doc["id"]))) == "active"


async def test_archive_then_restore_round_trip(factory):
    expert_id = await _expert("lifecycle-author-2")
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="a.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="f" * 64,
        file_size_bytes=10,
    )
    document_id = uuid.UUID(str(doc["id"]))

    archived = await governance.archive_document(document_id=document_id, actor_expert_id=expert_id, reason="stale")
    assert archived["lifecycle_status"] == "archived"
    assert await governance.latest_lifecycle_status(document_id) == "archived"

    restored = await governance.restore_document(document_id=document_id, actor_expert_id=expert_id)
    assert restored["lifecycle_status"] == "active"
    assert await governance.latest_lifecycle_status(document_id) == "active"


async def test_delete_is_terminal_no_restore(factory):
    expert_id = await _expert("lifecycle-author-3")
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="a.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="1" * 64,
        file_size_bytes=10,
    )
    document_id = uuid.UUID(str(doc["id"]))

    deleted = await governance.delete_document(document_id=document_id, actor_expert_id=expert_id, reason="wrong file")
    assert deleted["lifecycle_status"] == "deleted"

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.restore_document(document_id=document_id, actor_expert_id=expert_id)
    assert exc.value.code == "DOCUMENT_NOT_ARCHIVED"

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.archive_document(document_id=document_id, actor_expert_id=expert_id)
    assert exc.value.code == "DOCUMENT_ALREADY_DELETED"

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.delete_document(document_id=document_id, actor_expert_id=expert_id)
    assert exc.value.code == "DOCUMENT_ALREADY_DELETED"


async def test_double_archive_is_rejected(factory):
    expert_id = await _expert("lifecycle-author-4")
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="a.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="2" * 64,
        file_size_bytes=10,
    )
    document_id = uuid.UUID(str(doc["id"]))
    await governance.archive_document(document_id=document_id, actor_expert_id=expert_id)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.archive_document(document_id=document_id, actor_expert_id=expert_id)
    assert exc.value.code == "DOCUMENT_ALREADY_ARCHIVED"


async def test_restore_before_any_archive_is_rejected(factory):
    expert_id = await _expert("lifecycle-author-5")
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="a.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="3" * 64,
        file_size_bytes=10,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.restore_document(document_id=uuid.UUID(str(doc["id"])), actor_expert_id=expert_id)
    assert exc.value.code == "DOCUMENT_NOT_ARCHIVED"


async def test_lifecycle_action_on_unknown_document_raises(factory):
    expert_id = await _expert("lifecycle-author-6")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.archive_document(document_id=uuid.uuid4(), actor_expert_id=expert_id)
    assert exc.value.code == "DOCUMENT_NOT_FOUND"


async def test_list_documents_reports_lifecycle_status_and_still_includes_archived(factory):
    """The document-management listing must keep showing an archived/deleted
    document (with its status labeled), never silently drop it — that would
    hide the user's own history. Retrieval-eligibility exclusion is a
    SEPARATE guarantee, proven by `list_active_document_ids`/
    `search_similar_chunks` tests."""
    expert_id = await _expert("lifecycle-lister")
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="a.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="4" * 64,
        file_size_bytes=10,
    )
    document_id = uuid.UUID(str(doc["id"]))
    await governance.archive_document(document_id=document_id, actor_expert_id=expert_id)

    rows = await governance.list_documents(uploaded_by_expert_id=expert_id)
    assert len(rows) == 1
    assert rows[0]["id"] == doc["id"]
    assert rows[0]["lifecycle_status"] == "archived"


async def test_list_active_document_ids_excludes_archived_and_deleted(factory):
    expert_id = await _expert("lifecycle-active-ids")
    active_doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="active.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="5" * 64,
        file_size_bytes=10,
    )
    archived_doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="archived.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="6" * 64,
        file_size_bytes=10,
    )
    await governance.archive_document(document_id=uuid.UUID(str(archived_doc["id"])), actor_expert_id=expert_id)

    active_ids = await governance.list_active_document_ids(
        [uuid.UUID(str(active_doc["id"])), uuid.UUID(str(archived_doc["id"]))]
    )
    assert active_ids == {uuid.UUID(str(active_doc["id"]))}


async def test_link_evidence_rejects_an_archived_document(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("lifecycle-link-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    doc = await governance.register_evidence_document(
        proposal_id=None,
        uploaded_by_expert_id=expert_id,
        original_filename="archived-before-link.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="7" * 64,
        file_size_bytes=10,
    )
    document_id = uuid.UUID(str(doc["id"]))
    await governance.archive_document(document_id=document_id, actor_expert_id=expert_id)

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.link_evidence_to_justification(
            document_id=document_id, feature_justification_id=uuid.UUID(str(justification["id"]))
        )
    assert exc.value.code == "DOCUMENT_NOT_ACTIVE"


async def test_weight_mode_submit_excludes_evidence_from_an_archived_document(factory):
    """An already-linked-and-chunked document that gets archived AFTER
    linking must no longer count toward the submit-time evidence gate — a
    proposal cannot ride on evidence that has since been pulled."""
    base_id = await _base_config(factory)
    expert_id = await _expert("lifecycle-submit-author")
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    document_id = await _attach_extracted_evidence(
        factory, expert_id=expert_id, justification_id=uuid.UUID(str(justification["id"]))
    )
    await governance.archive_document(document_id=document_id, actor_expert_id=expert_id)

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "EVIDENCE_REQUIRED"


# --- Rubric enforcement on value-mode justifications (0046) -----------------


async def _register_area_accessibility(factory) -> uuid.UUID:
    """`area_accessibility` is one of the six RUBRIC_REQUIRED_FEATURE_KEYS —
    this fixture's own base feature (`FEATURE_ID`, `unit_available`) is not,
    so these tests register a real second feature row for it."""
    feature_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=feature_id,
                feature_key="area_accessibility",
                feature_version="v1",
                name="Area accessibility",
                category="expert",
                grain="area",
                value_type="numeric",
                formula_id="expert_value_assertion",
                normalization_method="identity",
                direction="positive",
                missing_policy="skip",
                status="active",
                definition_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return feature_id


async def test_rubric_required_feature_rejects_freeform_numeric(factory):
    feature_id = await _register_area_accessibility(factory)
    expert_id = await _expert("rubric-required-author-1")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="area", area_id=AREA_ID
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=feature_id,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            normalized_numeric=Decimal("0.42"),  # freely-typed — must be rejected
            **JUSTIFICATION_KWARGS,
        )
    assert exc.value.code == "RUBRIC_REQUIRED"


async def test_rubric_required_feature_accepts_a_real_band_and_derives_normalized_numeric(factory):
    feature_id = await _register_area_accessibility(factory)
    rubric = await governance.create_feature_rubric(feature_definition_id=feature_id, bands=VALID_BANDS, created_by="admin-test")
    expert_id = await _expert("rubric-required-author-2")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="area", area_id=AREA_ID
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=feature_id,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        rubric_id=uuid.UUID(str(rubric["id"])),
        rubric_band_value=Decimal("0.75"),
        **JUSTIFICATION_KWARGS,
    )
    assert Decimal(str(justification["normalized_numeric"])) == Decimal("0.75")
    assert Decimal(str(justification["rubric_band_value"])) == Decimal("0.75")
    assert uuid.UUID(str(justification["rubric_id"])) == uuid.UUID(str(rubric["id"]))


async def test_rubric_band_value_must_be_a_real_band_of_that_rubric(factory):
    feature_id = await _register_area_accessibility(factory)
    rubric = await governance.create_feature_rubric(feature_definition_id=feature_id, bands=VALID_BANDS, created_by="admin-test")
    expert_id = await _expert("rubric-required-author-3")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="area", area_id=AREA_ID
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=feature_id,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            rubric_id=uuid.UUID(str(rubric["id"])),
            rubric_band_value=Decimal("0.42"),  # not one of the five real bands
            **JUSTIFICATION_KWARGS,
        )
    assert exc.value.code == "RUBRIC_BAND_VALUE_INVALID"


async def test_rubric_id_must_belong_to_the_justifications_own_feature(factory):
    area_feature_id = await _register_area_accessibility(factory)
    # A rubric that belongs to a DIFFERENT feature (FEATURE_ID, unit_available).
    other_rubric = await governance.create_feature_rubric(feature_definition_id=FEATURE_ID, bands=VALID_BANDS, created_by="admin-test")
    expert_id = await _expert("rubric-required-author-4")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="area", area_id=AREA_ID
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=area_feature_id,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            rubric_id=uuid.UUID(str(other_rubric["id"])),
            rubric_band_value=Decimal("0.75"),
            **JUSTIFICATION_KWARGS,
        )
    assert exc.value.code == "RUBRIC_FEATURE_MISMATCH"


async def test_rubric_id_and_normalized_numeric_are_mutually_exclusive(factory):
    feature_id = await _register_area_accessibility(factory)
    rubric = await governance.create_feature_rubric(feature_definition_id=feature_id, bands=VALID_BANDS, created_by="admin-test")
    expert_id = await _expert("rubric-required-author-5")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="area", area_id=AREA_ID
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=feature_id,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            rubric_id=uuid.UUID(str(rubric["id"])),
            rubric_band_value=Decimal("0.75"),
            normalized_numeric=Decimal("0.75"),
            **JUSTIFICATION_KWARGS,
        )
    assert exc.value.code == "NORMALIZED_NUMERIC_NOT_ALLOWED_WITH_RUBRIC"


async def test_non_rubric_required_feature_still_accepts_freeform_numeric(factory):
    """A project-grain feature outside RUBRIC_REQUIRED_FEATURE_KEYS — confirms
    the pre-existing free-form path is genuinely unchanged for features
    outside the MVP six (`FEATURE_ID`/`unit_available` can't be used here:
    grain='unit' is GRAIN_NOT_ASSERTABLE for any value-mode assertion)."""
    feature_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=feature_id,
                feature_key="test_non_rubric_project_feature",
                feature_version="v1",
                name="Non-rubric project test feature",
                category="expert",
                grain="project",
                value_type="numeric",
                formula_id="expert_value_assertion",
                normalization_method="identity",
                direction="positive",
                missing_policy="skip",
                status="active",
                definition_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    expert_id = await _expert("non-rubric-author")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="project"
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=feature_id,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        normalized_numeric=Decimal("0.42"),
        **JUSTIFICATION_KWARGS,
    )
    assert Decimal(str(justification["normalized_numeric"])) == Decimal("0.42")
    assert justification["rubric_id"] is None
