"""`src/agents/advisory_tools.py`'s governance evidence retrieval (§21.7-§21.8):
`get_feature_evidence`, `validate_evidence`, `retrieve_and_validate`,
`generate_justification_explanation`.

Deliberately DB-backed, unlike this directory's other test files — those test
tool-plan SELECTION with the whole data functions mocked out; the functions
here are new and their entire value is in real entity/time filtering and real
cosine-distance scoping, which mocking away would not verify. Same real-DB
convention as `tests/test_services/test_governance.py`/`test_evidence_extraction.py`.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.agents import advisory_tools
from src.models.tables import expert_profiles, projects, ranking_feature_definitions
from src.services import evidence_extraction, governance, ranking_config


def _skip_reason() -> str:
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
OTHER_PROJECT_ID = uuid.uuid4()
FEATURE_ID = uuid.uuid4()
_VECTOR = [0.001] * 1536

JUSTIFICATION_KWARGS = dict(
    rationale="Sales velocity has increased 20% QoQ per Q2 report.",
    methodology="Comparative analysis against 3 comparable projects.",
    evidence_summary="See attached Q2 2026 Market Analysis, page 4.",
    expected_effect="increase",
    confidence="medium",
    limitations="Single-quarter data, seasonal effect not isolated.",
)


@pytest_asyncio.fixture
async def factory(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    for target in ("src.services.governance.get_session_factory", "src.services.evidence_extraction.get_session_factory"):
        monkeypatch.setattr(target, lambda sf=session_factory: sf, raising=False)
    monkeypatch.setattr(evidence_extraction, "embed_texts", lambda texts: [_VECTOR for _ in texts])

    tables = (
        "ranking_evidence_document_chunks",
        "ranking_evidence_extraction_attempts",
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
        for pid, name in ((PROJECT_ID, "Evidence Retrieval Test Project"), (OTHER_PROJECT_ID, "Other Project")):
            await session.execute(
                sa.insert(projects).values(
                    id=pid,
                    name=name,
                    launch_date=date(2026, 1, 1),
                    created_at=now,
                    updated_at=now,
                    absorption_calculator="legacy_aggregate",
                    external_id=f"P-EVR-{pid.hex[:8]}",
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


async def _base_config() -> uuid.UUID:
    row = await ranking_config.create_draft(
        weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
        min_weight_coverage=0.5,
        note="base",
        created_by="test",
    )
    return uuid.UUID(str(row["id"]))


async def _expert(identity_subject: str) -> uuid.UUID:
    row = await governance.get_or_create_expert_profile(identity_subject=identity_subject)
    return row["id"]


async def _proposal_and_justification(*, project_id: uuid.UUID = PROJECT_ID) -> tuple[uuid.UUID, uuid.UUID]:
    base_id = await _base_config()
    expert_id = await _expert(f"expert-{uuid.uuid4().hex[:8]}")
    proposal = await governance.create_proposal(base_config_id=base_id, project_id=project_id, created_by_expert_id=expert_id)
    justification = await governance.upsert_justification(
        proposal_id=proposal["id"],
        feature_definition_id=FEATURE_ID,
        previous_weight=None,
        proposed_weight=Decimal("0.5"),
        created_by_expert_id=expert_id,
        **JUSTIFICATION_KWARGS,
    )
    return proposal["id"], justification["id"]


async def _evidence_document(*, proposal_id: uuid.UUID, expert_id: uuid.UUID, checksum_suffix: str) -> uuid.UUID:
    row = await governance.register_evidence_document(
        proposal_id=proposal_id,
        uploaded_by_expert_id=expert_id,
        original_filename="evidence.pdf",
        mime_type="application/pdf",
        object_storage_key=f"ranking/evidence/{uuid.uuid4()}.pdf",
        sha256_checksum=(checksum_suffix * 64)[:64],
        file_size_bytes=10,
    )
    return row["id"]


async def _linked_document_with_chunk(
    *, justification_id: uuid.UUID, proposal_id: uuid.UUID, content: str = "the project sold 12 units in July"
) -> uuid.UUID:
    expert_id = await governance.get_proposal(proposal_id)
    expert_id = expert_id["created_by_expert_id"]
    document_id = await _evidence_document(proposal_id=proposal_id, expert_id=expert_id, checksum_suffix=str(uuid.uuid4())[0])
    await governance.link_evidence_to_justification(document_id=document_id, feature_justification_id=justification_id)
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id,
        [
            {
                "chunk_index": 0,
                "page_number": 3,
                "content": content,
                "token_count": 8,
                "embedding_model": "text-embedding-3-small",
                "embedding": _VECTOR,
            }
        ],
    )
    return document_id


# --- get_feature_evidence ---------------------------------------------------------


async def test_get_feature_evidence_returns_empty_for_malformed_id(factory):
    assert await advisory_tools.get_feature_evidence("not-a-uuid") == []


async def test_get_feature_evidence_returns_empty_when_nothing_linked(factory):
    _, justification_id = await _proposal_and_justification()
    assert await advisory_tools.get_feature_evidence(str(justification_id)) == []


async def test_get_feature_evidence_returns_linked_documents(factory):
    proposal_id, justification_id = await _proposal_and_justification()
    document_id = await _linked_document_with_chunk(justification_id=justification_id, proposal_id=proposal_id)

    docs = await advisory_tools.get_feature_evidence(str(justification_id))

    assert [d["id"] for d in docs] == [document_id]


# --- validate_evidence -------------------------------------------------------------


async def test_validate_evidence_accepts_chunk_from_matching_project_before_cutoff(factory):
    proposal_id, justification_id = await _proposal_and_justification(project_id=PROJECT_ID)
    document_id = await _linked_document_with_chunk(justification_id=justification_id, proposal_id=proposal_id)
    chunk = (await evidence_extraction.get_chunks_for_document(document_id))[0]

    ok = await advisory_tools.validate_evidence(chunk, str(PROJECT_ID), datetime.now(UTC) + timedelta(days=1))

    assert ok is True


async def test_validate_evidence_rejects_project_mismatch(factory):
    proposal_id, justification_id = await _proposal_and_justification(project_id=PROJECT_ID)
    document_id = await _linked_document_with_chunk(justification_id=justification_id, proposal_id=proposal_id)
    chunk = (await evidence_extraction.get_chunks_for_document(document_id))[0]

    ok = await advisory_tools.validate_evidence(chunk, str(OTHER_PROJECT_ID), datetime.now(UTC) + timedelta(days=1))

    assert ok is False


async def test_validate_evidence_rejects_evidence_uploaded_after_cutoff(factory):
    proposal_id, justification_id = await _proposal_and_justification(project_id=PROJECT_ID)
    document_id = await _linked_document_with_chunk(justification_id=justification_id, proposal_id=proposal_id)
    chunk = (await evidence_extraction.get_chunks_for_document(document_id))[0]

    ok = await advisory_tools.validate_evidence(chunk, str(PROJECT_ID), datetime.now(UTC) - timedelta(days=365))

    assert ok is False


async def test_validate_evidence_rejects_standalone_upload_with_no_proposal(factory):
    expert_id = await _expert(f"standalone-{uuid.uuid4().hex[:8]}")
    document_id = await _evidence_document(proposal_id=None, expert_id=expert_id, checksum_suffix="9")
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id,
        [
            {
                "chunk_index": 0,
                "page_number": None,
                "content": "standalone evidence",
                "token_count": 3,
                "embedding_model": "text-embedding-3-small",
                "embedding": _VECTOR,
            }
        ],
    )
    chunk = (await evidence_extraction.get_chunks_for_document(document_id))[0]

    ok = await advisory_tools.validate_evidence(chunk, str(PROJECT_ID), datetime.now(UTC) + timedelta(days=1))

    assert ok is False


# --- retrieve_and_validate ----------------------------------------------------------


async def test_retrieve_and_validate_returns_empty_for_unknown_justification(factory):
    assert await advisory_tools.retrieve_and_validate(str(uuid.uuid4()), str(PROJECT_ID), datetime.now(UTC)) == []


async def test_retrieve_and_validate_returns_empty_when_no_documents_linked(factory):
    _, justification_id = await _proposal_and_justification()
    result = await advisory_tools.retrieve_and_validate(str(justification_id), str(PROJECT_ID), datetime.now(UTC))
    assert result == []


async def test_retrieve_and_validate_returns_validated_chunks_scoped_to_justification(factory):
    proposal_id, justification_id = await _proposal_and_justification(project_id=PROJECT_ID)
    await _linked_document_with_chunk(justification_id=justification_id, proposal_id=proposal_id, content="linked evidence")

    # A second document exists but is NOT linked to this justification — must
    # never appear in the result (R19, §21.11's cross-proposal isolation).
    other_proposal_id, other_justification_id = await _proposal_and_justification(project_id=PROJECT_ID)
    await _linked_document_with_chunk(
        justification_id=other_justification_id, proposal_id=other_proposal_id, content="unrelated evidence"
    )

    results = await advisory_tools.retrieve_and_validate(
        str(justification_id), str(PROJECT_ID), datetime.now(UTC) + timedelta(days=1)
    )

    assert [r["content"] for r in results] == ["linked evidence"]


async def test_retrieve_and_validate_excludes_chunks_failing_project_scope(factory):
    proposal_id, justification_id = await _proposal_and_justification(project_id=PROJECT_ID)
    await _linked_document_with_chunk(justification_id=justification_id, proposal_id=proposal_id)

    # Validated against the WRONG project — everything must be filtered out.
    results = await advisory_tools.retrieve_and_validate(
        str(justification_id), str(OTHER_PROJECT_ID), datetime.now(UTC) + timedelta(days=1)
    )

    assert results == []


# --- generate_justification_explanation --------------------------------------------


async def test_generate_justification_explanation_reports_insufficient_evidence_when_nothing_validates(
    factory,
):
    _, justification_id = await _proposal_and_justification()

    result = await advisory_tools.generate_justification_explanation(
        str(justification_id), str(PROJECT_ID), datetime.now(UTC), feature_key="unit_available"
    )

    assert result == {"explanation": None, "citations": [], "insufficient_evidence_features": ["unit_available"]}


async def test_generate_justification_explanation_unknown_justification(factory):
    result = await advisory_tools.generate_justification_explanation(
        str(uuid.uuid4()), str(PROJECT_ID), datetime.now(UTC), feature_key="unit_available"
    )
    assert result == {"error": "JUSTIFICATION_NOT_FOUND"}


async def test_generate_justification_explanation_calls_llm_with_validated_chunks(factory, monkeypatch):
    proposal_id, justification_id = await _proposal_and_justification(project_id=PROJECT_ID)
    await _linked_document_with_chunk(justification_id=justification_id, proposal_id=proposal_id)

    captured_prompt = {}

    async def fake_generate_content(prompt, **kwargs):
        captured_prompt["text"] = prompt
        return (
            '{"explanation": "Sales rose [D1:p3].", '
            '"citations": [{"marker": "D1:p3", "document_id": "x", "page": 3, "quote": "the project sold 12 units in July"}], '
            '"insufficient_evidence_features": []}',
            {},
        )

    monkeypatch.setattr(advisory_tools, "generate_content", fake_generate_content)

    result = await advisory_tools.generate_justification_explanation(
        str(justification_id), str(PROJECT_ID), datetime.now(UTC) + timedelta(days=1), feature_key="unit_available"
    )

    assert result["insufficient_evidence_features"] == []
    assert "[D1:p3]" in result["explanation"]
    assert "the project sold 12 units in July" in captured_prompt["text"]
