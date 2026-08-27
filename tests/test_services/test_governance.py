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

from src.models.tables import projects, ranking_configs, ranking_feature_definitions
from src.services import governance, ranking_config

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


@pytest_asyncio.fixture
async def factory(monkeypatch):
    import os

    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    for target in ("src.services.governance.get_session_factory", "src.services.ranking_config.get_session_factory"):
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


async def test_submit_without_justification_is_rejected(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert()
    proposal = await governance.create_proposal(
        base_config_id=base_id, project_id=PROJECT_ID, created_by_expert_id=expert_id
    )
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "NO_JUSTIFICATIONS"


async def test_full_lifecycle_draft_to_published(factory):
    """draft → (justification) → submitted → (set_proposed_config) →
    approved → published — mirroring exactly the sequence
    `ranking_v2_ahp.md` §3 already established: config draft/publish stays
    on `src/services/ranking_config.py`, governance.py only references it."""
    base_id = await _base_config(factory)
    expert_id = await _expert("author")
    reviewer_id = await _expert("reviewer")

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

    submitted = await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert submitted["status"] == "submitted"

    # A reviewer cannot approve before a proposed_config_id is attached.
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.submit_review(
            proposal_id=proposal["id"], reviewer_expert_id=reviewer_id, decision="approved", comment="lgtm"
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
        proposal_id=proposal["id"], reviewer_expert_id=reviewer_id, decision="approved", comment="Looks solid."
    )
    assert reviewed["status"] == "approved"
    assert reviewed["approved_at"] is not None

    # mark_published refuses until the underlying config is ACTUALLY published
    # — governance.py never writes ranking_configs itself.
    with pytest.raises(governance.GovernanceError) as exc:
        await governance.mark_published(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert exc.value.code == "CONFIG_NOT_PUBLISHED"

    await ranking_config.publish(version=proposed_config["version"], published_by="test")

    published = await governance.mark_published(proposal_id=proposal["id"], actor_expert_id=expert_id)
    assert published["status"] == "published"
    assert published["published_at"] is not None


async def test_reviewer_cannot_review_the_same_proposal_twice(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author2")
    reviewer_id = await _expert("reviewer2")
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
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    await governance.submit_review(
        proposal_id=proposal["id"], reviewer_expert_id=reviewer_id, decision="request_changes", comment="Needs more evidence."
    )
    with pytest.raises(governance.GovernanceError) as exc:
        # decision stays "request_changes" (not "approved") so this isolates
        # the duplicate-reviewer constraint — "approved" would hit
        # PROPOSED_CONFIG_MISSING first, which is a different, earlier check.
        await governance.submit_review(
            proposal_id=proposal["id"], reviewer_expert_id=reviewer_id, decision="request_changes", comment="changed my mind"
        )
    assert exc.value.code == "ALREADY_REVIEWED"


async def test_request_changes_keeps_proposal_under_review_not_terminal(factory):
    base_id = await _base_config(factory)
    expert_id = await _expert("author3")
    reviewer_id = await _expert("reviewer3")
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
    await governance.submit_proposal(proposal_id=proposal["id"], actor_expert_id=expert_id)
    reviewed = await governance.submit_review(
        proposal_id=proposal["id"], reviewer_expert_id=reviewer_id, decision="request_changes", comment="More data please."
    )
    assert reviewed["status"] == "under_review"


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
    await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
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
