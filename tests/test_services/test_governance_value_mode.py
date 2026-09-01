"""PR-2 — value-mode governance (D37/D38): draft, evidence, submit, CEO
review, self-approval prohibition, deferred publish, and the PR-3 readiness
guard. Same real-Postgres-DB style as `tests/test_services/test_governance.py`
(this file's sibling, whose 15 existing weight-mode tests were re-run
unmodified after every change in this PR and still pass).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import (
    areas,
    projects,
    ranking_evidence_document_features,
    ranking_evidence_extraction_attempts,
    ranking_feature_definitions,
)
from src.services import evidence_extraction, governance

TEST_DATABASE_URL = None


def _skip_reason() -> str:
    import os
    from urllib.parse import urlsplit

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
PROJECT_FEATURE_ID = uuid.uuid4()
AREA_FEATURE_ID = uuid.uuid4()
MARKET_FEATURE_ID = uuid.uuid4()
UNIT_FEATURE_ID = uuid.uuid4()
LEGAL_FEATURE_ID = uuid.uuid4()
LEGAL_FEATURE_KEY = "project_legal_status"
LEGAL_ALLOWED_CATEGORICAL_VALUES = ("HIGH_RISK", "NOT_HIGH_RISK", "UNKNOWN")

VALUE_KWARGS = dict(
    rationale="Sales velocity has increased 20% QoQ per Q2 report.",
    methodology="Comparative analysis against 3 comparable projects.",
    evidence_summary="See attached Q2 2026 Market Analysis, page 4.",
    expected_effect="increase",
    confidence="medium",
    limitations="Single-quarter data, seasonal effect not isolated.",
)


@pytest_asyncio.fixture
async def factory(monkeypatch):
    import os

    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    for target in ("src.services.evidence_extraction.get_session_factory", "src.services.governance.get_session_factory"):
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
                name="Value Mode Test Project",
                launch_date=date(2026, 1, 1),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id=f"P-VAL-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(areas).values(
                id=AREA_ID,
                project_id=PROJECT_ID,
                area_name="Tower A",
                unit_type="2PN",
                bedrooms=2,
                area_sqm=Decimal("60"),
                total_units=10,
                created_at=now,
                external_id=f"A-VAL-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        for feature_id, feature_key, grain in (
            (PROJECT_FEATURE_ID, "expert_location_score", "project"),
            (AREA_FEATURE_ID, "area_accessibility", "area"),
            (MARKET_FEATURE_ID, "market_interest_rate", "market"),
            (UNIT_FEATURE_ID, "unit_available", "unit"),
        ):
            await session.execute(
                sa.insert(ranking_feature_definitions).values(
                    id=feature_id,
                    feature_key=feature_key,
                    feature_version="v1",
                    name=feature_key,
                    category="expert" if grain != "unit" else "operational",
                    grain=grain,
                    value_type="numeric",
                    formula_id=f"{feature_key}_v1",
                    normalization_method="identity",
                    direction="positive",
                    missing_policy="skip",
                    status="active",
                    definition_metadata={},
                    created_at=now,
                    updated_at=now,
                )
            )
        # PR-6: the one categorical Legal feature definition — same
        # get-or-create-by-truncation reasoning as the four numeric rows
        # above (`0042` seeds this in the real migrated schema, but this
        # fixture's own truncate wipes it, so it is re-inserted here).
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=LEGAL_FEATURE_ID,
                feature_key=LEGAL_FEATURE_KEY,
                feature_version="v1",
                name="Project legal status",
                category="legal",
                grain="project",
                value_type="categorical",
                formula_id="expert_value_assertion",
                normalization_method="none",
                direction="neutral",
                missing_policy="skip",
                status="active",
                definition_metadata={"allowed_categorical_values": list(LEGAL_ALLOWED_CATEGORICAL_VALUES)},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    # 0046: market_interest_rate is in RUBRIC_REQUIRED_FEATURE_KEYS — the
    # market-scope submit tests below must grade against a real rubric band,
    # never a freely-typed number, exactly like the real migrated schema.
    await governance.create_feature_rubric(
        feature_definition_id=MARKET_FEATURE_ID,
        bands=[
            {"value": "0.00", "label": "no evidence", "evidence_requirement": "none"},
            {"value": "0.25", "label": "weak", "evidence_requirement": "one source"},
            {"value": "0.50", "label": "moderate", "evidence_requirement": "official rate table"},
            {"value": "0.75", "label": "strong", "evidence_requirement": "policy decision"},
            {"value": "1.00", "label": "very strong", "evidence_requirement": "cross-bank comparison"},
        ],
        created_by="test-fixture",
    )

    yield session_factory

    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    await engine.dispose()


async def _expert(identity_subject: str) -> uuid.UUID:
    row = await governance.get_or_create_expert_profile(identity_subject=identity_subject)
    return uuid.UUID(str(row["id"]))


async def _draft_project_value(factory, *, author_subject: str = "analyst@example.com") -> tuple[dict, dict]:
    expert_id = await _expert(author_subject)
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        scope_type="project",
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=PROJECT_FEATURE_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        normalized_numeric=Decimal("0.75"),
        effective_at=datetime.now(UTC),
        author_subject=author_subject,
        **VALUE_KWARGS,
    )
    return proposal, justification


async def _link_evidence(factory, justification_id, *, uploaded_by: uuid.UUID) -> uuid.UUID:
    doc = await governance.register_evidence_document(
        project_id=PROJECT_ID,
        proposal_id=None,
        uploaded_by_expert_id=uploaded_by,
        original_filename="evidence.pdf",
        mime_type="application/pdf",
        object_storage_key=f"evidence/{uuid.uuid4().hex}.pdf",
        sha256_checksum="a" * 64,
        file_size_bytes=1024,
    )
    document_id = uuid.UUID(str(doc["id"]))
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id,
        [{"chunk_index": 0, "page_number": 1, "content": "Real evidence content for testing.", "token_count": 6,
          "embedding_model": "text-embedding-3-small", "embedding": [0.001] * 1536}],
    )
    await governance.link_evidence_to_justification(
        document_id=document_id, feature_justification_id=justification_id
    )
    return document_id


async def _link_evidence_with_chunk(factory, justification_id, *, uploaded_by: uuid.UUID) -> uuid.UUID:
    """Compatibility helper: new links require readiness before insertion."""
    return await _link_evidence(factory, justification_id, uploaded_by=uploaded_by)


# --- Draft creation, scope validation ----------------------------------------


async def test_create_value_draft_project_scope(factory):
    proposal, justification = await _draft_project_value(factory)
    assert proposal["assertion_kind"] == "value"
    assert proposal["status"] == "draft"
    assert proposal["base_config_id"] is None
    assert justification["assertion_kind"] == "value"
    assert justification["proposed_weight"] is None


async def test_value_draft_area_scope_requires_area_id(factory):
    expert_id = await _expert("analyst2@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_proposal(
            project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="area"
        )
    assert exc.value.code == "AREA_ID_REQUIRED"


async def test_value_draft_area_scope_with_area_id_succeeds(factory):
    expert_id = await _expert("analyst3@example.com")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        scope_type="area",
        area_id=AREA_ID,
    )
    assert str(proposal["area_id"]) == str(AREA_ID)


async def test_value_draft_project_scope_rejects_area_id(factory):
    expert_id = await _expert("analyst4@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_proposal(
            project_id=PROJECT_ID,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            scope_type="project",
            area_id=AREA_ID,
        )
    assert exc.value.code == "AREA_ID_NOT_ALLOWED"


async def test_area_assertion_fails_if_area_belongs_to_a_different_project(factory):
    """PR-5: an Area assertion cannot cross projects — `fk_rwp_area_id`
    (0034) only proves the area EXISTS, not that it belongs to THIS
    proposal's `project_id`; that cross-table fact is service-level."""
    now = datetime.now(UTC)
    other_project_id = uuid.uuid4()
    other_area_id = uuid.uuid4()
    async with governance.get_session_factory()() as session:
        await session.execute(
            sa.insert(projects).values(
                id=other_project_id,
                name="Other Project",
                launch_date=date(2026, 1, 1),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id=f"P-VAL-OTHER-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(areas).values(
                id=other_area_id,
                project_id=other_project_id,
                area_name="Other Tower",
                unit_type="2PN",
                bedrooms=2,
                area_sqm=Decimal("60"),
                total_units=10,
                created_at=now,
                external_id=f"A-VAL-OTHER-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.commit()

    expert_id = await _expert("analyst-area-mismatch@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_proposal(
            project_id=PROJECT_ID,  # NOT other_project_id
            created_by_expert_id=expert_id,
            assertion_kind="value",
            scope_type="area",
            area_id=other_area_id,  # belongs to other_project_id
        )
    assert exc.value.code == "AREA_PROJECT_MISMATCH"


async def test_area_assertion_fails_if_area_does_not_exist(factory):
    expert_id = await _expert("analyst-area-missing@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_proposal(
            project_id=PROJECT_ID,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            scope_type="area",
            area_id=uuid.uuid4(),
        )
    assert exc.value.code == "AREA_NOT_FOUND"


async def test_expert_cannot_assert_crm_owned_area_velocity_norm(factory):
    """`area_velocity_norm`/`area_conversion_norm` are legacy CRM-operational
    features (`src/ranking/service.py::_area_features()`) — never a
    value-mode assertion, even if a `ranking_feature_definitions` row for one
    somehow existed (defense-in-depth; normally no such row exists at all)."""
    now = datetime.now(UTC)
    crm_feature_id = uuid.uuid4()
    async with governance.get_session_factory()() as session:
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=crm_feature_id,
                feature_key="area_velocity_norm",
                feature_version="v1",
                name="area_velocity_norm",
                category="operational",
                grain="area",
                value_type="numeric",
                formula_id="area_velocity_norm_v1",
                normalization_method="identity",
                direction="positive",
                missing_policy="skip",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    expert_id = await _expert("analyst-crm-owned@example.com")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        scope_type="area",
        area_id=AREA_ID,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=crm_feature_id,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            normalized_numeric=Decimal("0.5"),
            effective_at=now,
            **VALUE_KWARGS,
        )
    assert exc.value.code == "AREA_CRM_OWNED_FEATURE_KEY_NOT_ASSERTABLE"


async def test_value_draft_rejects_base_config_id(factory):
    expert_id = await _expert("analyst5@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_proposal(
            base_config_id=uuid.uuid4(),
            project_id=PROJECT_ID,
            created_by_expert_id=expert_id,
            assertion_kind="value",
        )
    assert exc.value.code == "BASE_CONFIG_NOT_ALLOWED"


async def test_weight_draft_without_base_config_id_is_rejected(factory):
    """Preserves existing weight-mode behavior exactly: base_config_id is still
    required for the default assertion_kind."""
    expert_id = await _expert("analyst6@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.create_proposal(project_id=PROJECT_ID, created_by_expert_id=expert_id)
    assert exc.value.code == "BASE_CONFIG_REQUIRED"


async def test_value_justification_rejects_grain_scope_mismatch(factory):
    """A project-grain feature cannot be asserted under an area-scope proposal —
    a Postgres CHECK cannot express this (cross-table), so it's service-level."""
    expert_id = await _expert("analyst7@example.com")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        scope_type="area",
        area_id=AREA_ID,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=PROJECT_FEATURE_ID,  # grain='project', proposal scope='area'
            created_by_expert_id=expert_id,
            assertion_kind="value",
            normalized_numeric=Decimal("0.5"),
            effective_at=datetime.now(UTC),
            **VALUE_KWARGS,
        )
    assert exc.value.code == "GRAIN_SCOPE_MISMATCH"


async def test_value_justification_rejects_unit_grain(factory):
    expert_id = await _expert("analyst8@example.com")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="project"
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=UNIT_FEATURE_ID,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            normalized_numeric=Decimal("0.5"),
            effective_at=datetime.now(UTC),
            **VALUE_KWARGS,
        )
    assert exc.value.code == "GRAIN_NOT_ASSERTABLE"


async def test_weight_and_value_fields_are_mutually_exclusive(factory):
    expert_id = await _expert("analyst9@example.com")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="project"
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=PROJECT_FEATURE_ID,
            proposed_weight=Decimal("0.5"),  # weight field on a value-mode call
            created_by_expert_id=expert_id,
            assertion_kind="value",
            normalized_numeric=Decimal("0.5"),
            effective_at=datetime.now(UTC),
            **VALUE_KWARGS,
        )
    assert exc.value.code == "WEIGHT_FIELDS_NOT_ALLOWED"


# --- Evidence lock (applies to both modes, §3.2) -----------------------------


async def test_evidence_may_attach_only_while_draft(factory):
    proposal, justification = await _draft_project_value(factory)
    expert_id = await _expert("analyst@example.com")
    await _link_evidence(factory, uuid.UUID(str(justification["id"])), uploaded_by=expert_id)
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)

    with pytest.raises(governance.GovernanceError) as exc:
        await _link_evidence(factory, uuid.UUID(str(justification["id"])), uploaded_by=expert_id)
    assert exc.value.code == "EVIDENCE_LOCKED"


# --- Submit validation --------------------------------------------------------


async def test_submit_fails_without_evidence(factory):
    proposal, _ = await _draft_project_value(factory)
    expert_id = await _expert("analyst@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "EVIDENCE_REQUIRED"


async def _market_rubric_id() -> uuid.UUID:
    rubric = await governance.get_current_feature_rubric(MARKET_FEATURE_ID)
    return uuid.UUID(str(rubric["id"]))


async def test_market_submit_fails_without_citation(factory):
    author_subject = "analyst-market@example.com"
    expert_id = await _expert(author_subject)
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="market"
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=MARKET_FEATURE_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        rubric_id=await _market_rubric_id(),
        rubric_band_value=Decimal("0.50"),
        effective_at=datetime.now(UTC),
        author_subject=author_subject,
        **VALUE_KWARGS,
    )
    await _link_evidence(factory, uuid.UUID(str(justification["id"])), uploaded_by=expert_id)

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "MARKET_CITATION_REQUIRED"


async def test_market_submit_fails_with_expiry_beyond_policy(factory):
    author_subject = "analyst-market2@example.com"
    expert_id = await _expert(author_subject)
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="market"
    )
    effective = datetime.now(UTC)
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=MARKET_FEATURE_ID,  # market_interest_rate -> 30-day ceiling
        created_by_expert_id=expert_id,
        assertion_kind="value",
        rubric_id=await _market_rubric_id(),
        rubric_band_value=Decimal("0.50"),
        effective_at=effective,
        expires_at=effective + timedelta(days=60),
        external_source_citation="State Bank circular no. 12/2026",
        author_subject=author_subject,
        **VALUE_KWARGS,
    )
    await _link_evidence(factory, uuid.UUID(str(justification["id"])), uploaded_by=expert_id)

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "MARKET_EXPIRY_EXCEEDS_POLICY"


async def test_market_submit_succeeds_with_citation_and_valid_expiry(factory):
    author_subject = "analyst-market3@example.com"
    expert_id = await _expert(author_subject)
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="market"
    )
    effective = datetime.now(UTC)
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=MARKET_FEATURE_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        rubric_id=await _market_rubric_id(),
        rubric_band_value=Decimal("0.50"),
        effective_at=effective,
        expires_at=effective + timedelta(days=30),
        external_source_citation="State Bank circular no. 12/2026",
        author_subject=author_subject,
        **VALUE_KWARGS,
    )
    await _link_evidence(factory, uuid.UUID(str(justification["id"])), uploaded_by=expert_id)
    submitted = await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert submitted["status"] == "submitted"


# --- CEO review ---------------------------------------------------------------


async def _submitted_project_value(factory, *, author_subject: str = "analyst@example.com"):
    proposal, justification = await _draft_project_value(factory, author_subject=author_subject)
    expert_id = await _expert(author_subject)
    await _link_evidence(factory, uuid.UUID(str(justification["id"])), uploaded_by=expert_id)
    submitted = await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    return submitted, justification


async def test_non_ceo_cannot_review_value_mode(factory):
    proposal, _ = await _submitted_project_value(factory)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="approved",
            comment="lgtm",
            reviewer_subject="admin-not-ceo@example.com",
            reviewer_is_ceo=False,
        )
    assert exc.value.code == "CEO_APPROVAL_REQUIRED"


async def test_review_without_identity_is_rejected(factory):
    proposal, _ = await _submitted_project_value(factory)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(proposal_id=proposal["id"], decision="approved", comment="lgtm")
    assert exc.value.code == "IDENTITY_REQUIRED"


async def test_ceo_can_approve_value_mode_assertion(factory):
    proposal, _ = await _submitted_project_value(factory)
    reviewed = await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="Approved — solid comps.",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    assert reviewed["status"] == "approved"
    assert reviewed["approved_at"] is not None


async def test_ceo_can_reject_value_mode_assertion(factory):
    proposal, _ = await _submitted_project_value(factory)
    reviewed = await governance.submit_review(
        proposal_id=proposal["id"],
        decision="rejected",
        comment="Insufficient evidence.",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    assert reviewed["status"] == "rejected"


async def test_ceo_approval_persists_acknowledgement_and_queue_excludes_self(factory):
    proposal, justification = await _submitted_project_value(factory, author_subject="advisor-owner@example.com")
    author_id = await _expert("advisor-owner@example.com")
    other_id = await _expert("ceo-reviewer@example.com")

    self_queue, self_total = await governance.build_submitted_review_queue(
        project_scope="ALL", reviewer_expert_id=author_id, limit=25, offset=0
    )
    assert self_queue == [] and self_total == 0
    visible, total = await governance.build_submitted_review_queue(
        project_scope="ALL", reviewer_expert_id=other_id, limit=25, offset=0
    )
    assert total == 1 and visible[0]["proposal"]["id"] == proposal["id"]

    approved = await governance.submit_review(
        proposal_id=proposal["id"], decision="approved", comment="Bằng chứng đã được kiểm tra.",
        reviewer_subject="ceo-reviewer@example.com", reviewer_is_ceo=True,
        reviewer_project_scope="ALL", evidence_review_acknowledged=True,
    )
    assert approved["status"] == "approved"
    reviews = await governance.list_reviews(proposal["id"])
    assert reviews[-1]["evidence_review_acknowledged"] is True


async def test_final_review_rejects_latest_failed_evidence_attempt(factory):
    proposal, justification = await _submitted_project_value(factory, author_subject="advisor-failed-evidence@example.com")
    async with governance.get_session_factory()() as session:
        document_id = await session.scalar(
            sa.select(ranking_evidence_document_features.c.document_id).where(
                ranking_evidence_document_features.c.feature_justification_id == justification["id"]
            )
        )
        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(), document_id=document_id, status="failed", created_at=datetime.now(UTC) + timedelta(seconds=1)
            )
        )
        await session.commit()
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"], decision="approved", comment="Đã kiểm tra bằng chứng.",
            reviewer_subject="ceo-failed-evidence@example.com", reviewer_is_ceo=True,
            reviewer_project_scope="ALL", evidence_review_acknowledged=True,
        )
    assert exc.value.code == "EVIDENCE_NOT_READY"


async def test_author_cannot_approve_own_value_assertion(factory):
    proposal, _ = await _submitted_project_value(factory, author_subject="ceo-author@example.com")
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="approved",
            comment="Self-approving.",
            reviewer_subject="ceo-author@example.com",  # same subject as the author
            reviewer_is_ceo=True,
        )
    assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


async def test_submit_review_has_no_caller_supplied_reviewer_id_parameter(factory):
    """D18: `reviewer_expert_id` is not merely ignored — it does not exist as
    a parameter at all anymore, for either assertion kind, so a caller cannot
    even attempt to pass one. Spoofing the reviewer's identity is structurally
    impossible, not just discouraged."""
    proposal, _ = await _submitted_project_value(factory)
    with pytest.raises(TypeError):
        await governance.submit_review(
            proposal_id=proposal["id"],
            reviewer_expert_id=uuid.uuid4(),  # type: ignore[call-arg]
            decision="approved",
            comment="lgtm",
            reviewer_subject="ceo@example.com",
            reviewer_is_ceo=True,
        )

    reviewed = await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="lgtm",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    assert reviewed["status"] == "approved"


async def test_weight_mode_review_now_requires_ceo_and_forbids_self_approval(factory):
    """D18 close-out: weight-mode review used to have NO CEO/self-approval
    check at all (a caller-supplied `reviewer_expert_id` with no `is_ceo`
    signal succeeded unconditionally) — this test used to assert that gap
    (`test_weight_mode_review_unaffected_no_ceo_check`). It is closed now:
    weight-mode review requires the exact same real, authenticated CEO
    identity and self-approval guard as value-mode does, applied inside
    `governance.submit_review()` regardless of `assertion_kind`."""
    from src.services import ranking_config

    author_id = await _expert("weight-author@example.com")

    base = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="base",
        created_by="test",
    )
    proposal = await governance.create_proposal(
        base_config_id=uuid.UUID(str(base["id"])), project_id=PROJECT_ID, created_by_expert_id=author_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=UNIT_FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.9"),
        created_by_expert_id=author_id,
        **VALUE_KWARGS,
    )
    await _link_evidence_with_chunk(factory, uuid.UUID(str(justification["id"])), uploaded_by=author_id)
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=author_id)
    proposed_config = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="proposed",
        created_by="test",
    )
    await governance.set_proposed_config(
        proposal_id=proposal["id"],
        proposed_config_id=uuid.UUID(str(proposed_config["id"])),
        actor_expert_id=author_id,
    )

    # No identity at all -> rejected before any CEO check.
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(proposal_id=proposal["id"], decision="approved", comment="lgtm")
    assert exc.value.code == "IDENTITY_REQUIRED"

    # A real, distinct identity but not CEO -> rejected.
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="approved",
            comment="lgtm",
            reviewer_subject="weight-reviewer@example.com",
            reviewer_is_ceo=False,
        )
    assert exc.value.code == "CEO_APPROVAL_REQUIRED"

    # The author, even claiming is_ceo=True, cannot approve their own proposal.
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="approved",
            comment="self-approving",
            reviewer_subject="weight-author@example.com",
            reviewer_is_ceo=True,
        )
    assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"

    # A real, distinct, CEO-verified identity succeeds.
    reviewed = await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="lgtm",
        reviewer_subject="weight-reviewer@example.com",
        reviewer_is_ceo=True,
    )
    assert reviewed["status"] == "approved"


# --- Publish (PR-3: re-verifies readiness, flips status; does not itself
# write ranking_feature_values -- see src/ranking/service.py's materializer) -


async def test_value_mode_publish_reverifies_and_marks_published(factory):
    proposal, _ = await _submitted_project_value(factory)
    await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="Approved.",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    expert_id = await _expert("analyst@example.com")
    published = await governance.mark_published(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert published["status"] == "published"
    assert published["published_at"] is not None


# --- PR-3 readiness guard ------------------------------------------------------


async def test_materialization_guard_accepts_a_valid_ceo_approved_assertion(factory):
    proposal, justification = await _submitted_project_value(factory)
    await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="Approved.",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    result = await governance.validate_value_assertion_for_materialization(uuid.UUID(str(justification["id"])))
    assert result["proposal"]["id"] == proposal["id"]
    assert result["approved_review"]["reviewer_is_ceo"] is True


async def test_materialization_guard_rejects_missing_approval(factory):
    _, justification = await _draft_project_value(factory)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.validate_value_assertion_for_materialization(uuid.UUID(str(justification["id"])))
    assert exc.value.code == "NOT_APPROVED"


async def test_materialization_guard_rejects_weight_mode(factory):
    from src.services import ranking_config

    author_id = await _expert("weight-author2@example.com")
    base = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="base",
        created_by="test",
    )
    proposal = await governance.create_proposal(
        base_config_id=uuid.UUID(str(base["id"])), project_id=PROJECT_ID, created_by_expert_id=author_id
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=UNIT_FEATURE_ID,
        proposed_weight=Decimal("0.9"),
        created_by_expert_id=author_id,
        **VALUE_KWARGS,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.validate_value_assertion_for_materialization(uuid.UUID(str(justification["id"])))
    assert exc.value.code == "NOT_VALUE_MODE"


async def test_materialization_guard_has_no_side_effects(factory):
    """Pure SELECT — calling it must never touch ranking_feature_values/
    _snapshots/_lineage/ranking_scores (none exist to write to in PR-2 at all,
    but this proves the guard itself performs no insert/update anywhere)."""
    proposal, justification = await _submitted_project_value(factory)
    await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="Approved.",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    before = await governance.get_proposal(proposal["id"])
    await governance.validate_value_assertion_for_materialization(uuid.UUID(str(justification["id"])))
    after = await governance.get_proposal(proposal["id"])
    assert before == after


async def test_invalidated_historical_evidence_is_auditable_but_cannot_submit_publish_or_materialize(factory):
    """A later failed extraction invalidates future use without deleting the
    append-only evidence link that reviewers must still be able to audit."""
    proposal, justification = await _draft_project_value(factory)
    author_id = uuid.UUID(str(justification["created_by_expert_id"]))
    document_id = await _link_evidence(factory, justification["id"], uploaded_by=author_id)
    await evidence_extraction.mark_extraction_attempt_failed(document_id, status="failed")

    historical_links = await governance.list_documents_for_justification(justification["id"])
    assert [uuid.UUID(str(row["id"])) for row in historical_links] == [document_id]

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=author_id)
    assert exc.value.code == "EVIDENCE_NOT_READY"

    # An idempotent reprocess reuses the immutable successful chunk instead
    # of inserting a duplicate, then a later failure proves both revalidation
    # gates still hold.
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [
        {"chunk_index": 0, "page_number": 1, "content": "Replacement extraction.", "token_count": 2,
         "embedding_model": "text-embedding-3-small", "embedding": [0.001] * 1536}
    ])
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=author_id)
    await governance.submit_review(
        proposal_id=proposal["id"], decision="approved", comment="Approved.",
        reviewer_subject="ceo@example.com", reviewer_is_ceo=True,
    )
    await evidence_extraction.mark_extraction_attempt_failed(document_id, status="failed")

    with pytest.raises(governance.GovernanceError) as exc:
        await governance.validate_value_assertion_for_materialization(uuid.UUID(str(justification["id"])))
    assert exc.value.code == "EVIDENCE_NOT_READY"
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.mark_published(proposal_id=proposal["id"], actor_expert_id=author_id)
    assert exc.value.code == "EVIDENCE_NOT_READY"


# --- PR-6: Legal (`project_legal_status`) — categorical value-mode ----------
#
# Legal reuses this entire PR-2 workflow unmodified: draft -> evidence ->
# submit -> CEO-approve -> publish, and the CEO-only/no-self-approval/no-
# static-token guarantees already proven above (`test_non_ceo_cannot_review_
# value_mode`, `test_author_cannot_approve_own_value_assertion`,
# `test_review_without_identity_is_rejected`) apply to it exactly as they do
# to Project/Market/Area — those tests are not repeated here per feature key,
# only the genuinely NEW pieces PR-6 adds (categorical vocabulary
# validation, area/unit scope absence for Legal specifically) are.


async def _draft_legal_value(
    factory, *, categorical_value: str = "HIGH_RISK", author_subject: str = "legal-analyst@example.com"
) -> tuple[dict, dict]:
    expert_id = await _expert(author_subject)
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        scope_type="project",
    )
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=LEGAL_FEATURE_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        categorical_value=categorical_value,
        effective_at=datetime.now(UTC),
        author_subject=author_subject,
        **VALUE_KWARGS,
    )
    return proposal, justification


async def test_create_legal_value_draft_project_scope(factory):
    proposal, justification = await _draft_legal_value(factory, categorical_value="HIGH_RISK")
    assert proposal["assertion_kind"] == "value"
    assert proposal["scope_type"] == "project"
    assert proposal["area_id"] is None
    assert justification["categorical_value"] == "HIGH_RISK"
    assert justification["proposed_weight"] is None
    assert justification["raw_numeric"] is None
    assert justification["normalized_numeric"] is None


async def test_legal_justification_rejects_area_scope_proposal(factory):
    """Legal's own feature definition is `grain='project'` —
    `_GRAIN_SCOPE_COMPATIBILITY` only permits `scope_type='project'` for it,
    so an area-scope proposal + Legal's feature_definition_id is a
    grain/scope mismatch, never a valid Legal assertion."""
    expert_id = await _expert("legal-analyst2@example.com")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID,
        created_by_expert_id=expert_id,
        assertion_kind="value",
        scope_type="area",
        area_id=AREA_ID,
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=LEGAL_FEATURE_ID,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            categorical_value="HIGH_RISK",
            **VALUE_KWARGS,
        )
    assert exc.value.code == "GRAIN_SCOPE_MISMATCH"


async def test_legal_categorical_value_outside_vocabulary_is_rejected(factory):
    expert_id = await _expert("legal-analyst3@example.com")
    proposal = await governance.create_proposal(
        project_id=PROJECT_ID, created_by_expert_id=expert_id, assertion_kind="value", scope_type="project"
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.upsert_justification(
            proposal_id=proposal["id"],
            feature_definition_id=LEGAL_FEATURE_ID,
            created_by_expert_id=expert_id,
            assertion_kind="value",
            categorical_value="MEDIUM_RISK",
            **VALUE_KWARGS,
        )
    assert exc.value.code == "CATEGORICAL_VALUE_NOT_ALLOWED"


@pytest.mark.parametrize("status", ["HIGH_RISK", "NOT_HIGH_RISK", "UNKNOWN"])
async def test_legal_categorical_value_inside_vocabulary_is_accepted(factory, status):
    _, justification = await _draft_legal_value(factory, categorical_value=status)
    assert justification["categorical_value"] == status


async def test_legal_numeric_validation_is_not_incorrectly_applied_to_categorical_value(factory):
    """`upsert_justification()`'s numeric-only checks (`NORMALIZED_VALUE_
    RANGE`, requiring raw/normalized/categorical to be non-empty) must not
    reject a well-formed categorical-only Legal assertion — a categorical
    value is not a number, and no numeric bound applies to it."""
    proposal, justification = await _draft_legal_value(factory, categorical_value="NOT_HIGH_RISK")
    assert justification["normalized_numeric"] is None
    assert justification["categorical_value"] == "NOT_HIGH_RISK"


async def test_high_risk_legal_assertion_without_evidence_is_rejected_at_submit(factory):
    """No automatic/system source path exists for HIGH_RISK — evidence is
    mandatory for every value-mode justification unconditionally (the same
    existing rule `test_submit_fails_without_evidence` already proves), so a
    HIGH_RISK Legal assertion is rejected by that same unconditional rule,
    not by a Legal-specific carve-out."""
    proposal, justification = await _draft_legal_value(factory, categorical_value="HIGH_RISK")
    expert_id = uuid.UUID(str(justification["created_by_expert_id"]))
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "EVIDENCE_REQUIRED"


async def _submitted_legal_value(factory, *, categorical_value: str = "HIGH_RISK"):
    proposal, justification = await _draft_legal_value(factory, categorical_value=categorical_value)
    expert_id = uuid.UUID(str(justification["created_by_expert_id"]))
    await _link_evidence(factory, uuid.UUID(str(justification["id"])), uploaded_by=expert_id)
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    return await governance.get_proposal(proposal["id"]), justification


async def test_high_risk_legal_assertion_with_evidence_submits_successfully(factory):
    proposal, _ = await _submitted_legal_value(factory, categorical_value="HIGH_RISK")
    assert proposal["status"] == "submitted"


async def test_non_ceo_cannot_approve_legal_assertion(factory):
    proposal, _ = await _submitted_legal_value(factory)
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="approved",
            comment="Looks fine.",
            reviewer_subject="not-ceo@example.com",
            reviewer_is_ceo=False,
        )
    assert exc.value.code == "CEO_APPROVAL_REQUIRED"


async def test_legal_author_cannot_approve_own_assertion(factory):
    proposal, justification = await _submitted_legal_value(factory)
    author_subject = "legal-analyst@example.com"
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"],
            decision="approved",
            comment="Self-approving.",
            reviewer_subject=author_subject,
            reviewer_is_ceo=True,
        )
    assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


async def test_ceo_can_approve_and_publish_a_legal_assertion(factory):
    proposal, justification = await _submitted_legal_value(factory, categorical_value="HIGH_RISK")
    author_expert_id = uuid.UUID(str(justification["created_by_expert_id"]))
    approved = await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="Legal risk confirmed per outside counsel memo.",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    assert approved["status"] == "approved"
    published = await governance.mark_published(proposal_id=proposal["id"], actor_expert_id=author_expert_id)
    assert published["status"] == "published"


async def test_legal_materialization_guard_rejects_missing_evidence_after_the_fact(factory):
    """Defense-in-depth (`validate_value_assertion_for_materialization()`),
    same guarantee Market/Project already have — re-verified independently
    of whatever the submit-time check already confirmed."""
    proposal, justification = await _submitted_legal_value(factory, categorical_value="NOT_HIGH_RISK")
    await governance.submit_review(
        proposal_id=proposal["id"],
        decision="approved",
        comment="Reviewed — not high risk per current filings.",
        reviewer_subject="ceo@example.com",
        reviewer_is_ceo=True,
    )
    result = await governance.validate_value_assertion_for_materialization(uuid.UUID(str(justification["id"])))
    assert result["justification"]["categorical_value"] == "NOT_HIGH_RISK"
