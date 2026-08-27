"""`src/services/evidence_extraction.py` — chunk store + extraction-status log (0035, §21.4-§21.7).

Chạy bằng `bash scripts/test_db.sh` — cùng quy ước với `tests/test_services/test_governance.py`.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import expert_profiles, ranking_evidence_documents
from src.services import evidence_extraction


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

EXPERT_ID = uuid.uuid4()
_VECTOR = [0.001] * 1536


def _chunk(index: int, *, content: str = "the project sold 12 units in July") -> dict:
    return {
        "chunk_index": index,
        "page_number": 1,
        "content": content,
        "token_count": 8,
        "embedding_model": "text-embedding-3-small",
        "embedding": _VECTOR,
    }


@pytest_asyncio.fixture
async def factory(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        "src.services.evidence_extraction.get_session_factory", lambda sf=session_factory: sf, raising=False
    )

    tables = (
        "ranking_evidence_document_chunks",
        "ranking_evidence_extraction_attempts",
        "ranking_evidence_documents",
        "expert_profiles",
    )
    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))

    now = datetime.now(UTC)
    async with session_factory() as session:
        await session.execute(
            sa.insert(expert_profiles).values(
                id=EXPERT_ID, identity_subject="evidence-extraction-test", status="active"
            )
        )
        await session.commit()

    yield session_factory

    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    await engine.dispose()


async def _document(factory, *, checksum_suffix: str = "1") -> uuid.UUID:
    document_id = uuid.uuid4()
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_evidence_documents).values(
                id=document_id,
                uploaded_by_expert_id=EXPERT_ID,
                original_filename="evidence.pdf",
                mime_type="application/pdf",
                object_storage_key=f"ranking/evidence/{document_id}.pdf",
                sha256_checksum=(checksum_suffix * 64)[:64],
                file_size_bytes=10,
                extraction_status="not_requested",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return document_id


# --- Trạng thái trích xuất (log append-only, không phải cột UPDATE) --------------


async def test_latest_extraction_status_is_not_requested_with_no_attempts(factory):
    document_id = await _document(factory)
    assert await evidence_extraction.latest_extraction_status(document_id) == "not_requested"


async def test_request_extraction_unknown_document_raises(factory):
    with pytest.raises(evidence_extraction.ExtractionError) as exc:
        await evidence_extraction.request_extraction(uuid.uuid4())
    assert exc.value.code == "DOCUMENT_NOT_FOUND"


async def test_request_extraction_logs_pending_and_is_idempotent(factory):
    document_id = await _document(factory)
    assert await evidence_extraction.request_extraction(document_id) == "pending"
    assert await evidence_extraction.latest_extraction_status(document_id) == "pending"

    # Gọi lần hai trên một document đang 'pending' — KHÔNG ghi thêm dòng mới,
    # trả về trạng thái hiện tại (§21.5's "calling this twice is a no-op").
    assert await evidence_extraction.request_extraction(document_id) == "pending"
    async with factory() as session:
        count = (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM ranking_evidence_extraction_attempts WHERE document_id = :id"
                ),
                {"id": document_id},
            )
        ).scalar_one()
    assert count == 1


async def test_request_extraction_after_succeeded_stays_succeeded(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)])
    assert await evidence_extraction.request_extraction(document_id) == "succeeded"


async def test_mark_extraction_attempt_failed_records_error_summary(factory):
    document_id = await _document(factory)
    await evidence_extraction.mark_extraction_attempt_failed(
        document_id, status="failed", error_summary="could not parse PDF"
    )
    assert await evidence_extraction.latest_extraction_status(document_id) == "failed"

    # Sau một lần 'failed', request_extraction phải cho phép thử lại — không
    # coi 'failed' là trạng thái chốt như 'succeeded'.
    assert await evidence_extraction.request_extraction(document_id) == "pending"


async def test_mark_extraction_attempt_failed_rejects_bad_status(factory):
    document_id = await _document(factory)
    with pytest.raises(ValueError):
        await evidence_extraction.mark_extraction_attempt_failed(document_id, status="succeeded")


# --- Chunk + embedding -----------------------------------------------------------


async def test_insert_chunks_requires_at_least_one_chunk(factory):
    document_id = await _document(factory)
    with pytest.raises(evidence_extraction.ExtractionError) as exc:
        await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [])
    assert exc.value.code == "NO_CHUNKS"


async def test_insert_chunks_and_mark_succeeded_persists_rows_and_status(factory):
    document_id = await _document(factory)
    count = await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id, [_chunk(0), _chunk(1, content="second chunk")]
    )
    assert count == 2
    assert await evidence_extraction.latest_extraction_status(document_id) == "succeeded"

    chunks = await evidence_extraction.get_chunks_for_document(document_id)
    assert [c["chunk_index"] for c in chunks] == [0, 1]
    assert chunks[0]["content"] == "the project sold 12 units in July"


async def test_insert_chunks_twice_for_same_document_raises_not_silently_duplicates(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)])
    with pytest.raises(evidence_extraction.ExtractionError) as exc:
        await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)])
    assert exc.value.code == "CHUNKS_ALREADY_EXIST"


# --- Truy hồi theo cosine similarity ----------------------------------------------


async def test_search_similar_chunks_returns_empty_for_no_document_ids(factory):
    assert await evidence_extraction.search_similar_chunks([], _VECTOR, top_k=5) == []


async def test_search_similar_chunks_is_scoped_to_the_given_documents(factory):
    doc_a = await _document(factory, checksum_suffix="a")
    doc_b = await _document(factory, checksum_suffix="b")
    await evidence_extraction.insert_chunks_and_mark_succeeded(doc_a, [_chunk(0, content="doc A chunk")])
    await evidence_extraction.insert_chunks_and_mark_succeeded(doc_b, [_chunk(0, content="doc B chunk")])

    results = await evidence_extraction.search_similar_chunks([doc_a], _VECTOR, top_k=5)

    assert [r["document_id"] for r in results] == [doc_a]
    assert results[0]["content"] == "doc A chunk"


async def test_search_similar_chunks_orders_by_distance_and_respects_top_k(factory):
    document_id = await _document(factory)
    close = [0.001] * 1536
    # Cosine distance is scale-invariant — a vector in the SAME direction as
    # `close` (just scaled) would be equidistant from the query, not farther.
    # Use the opposite direction so it's actually maximally far (distance 2).
    far = [-0.001] * 1536
    await evidence_extraction.insert_chunks_and_mark_succeeded(
        document_id,
        [
            {**_chunk(0, content="far chunk"), "embedding": far},
            {**_chunk(1, content="close chunk"), "embedding": close},
        ],
    )

    results = await evidence_extraction.search_similar_chunks(
        [document_id], query_embedding=close, top_k=1
    )

    assert len(results) == 1
    assert results[0]["content"] == "close chunk"


# --- embed_texts: không gọi OpenAI thật, chỉ kiểm nhánh thiếu API key ------------


async def test_embed_texts_without_api_key_raises(monkeypatch):
    class _FakeSettings:
        resolved_llm_api_key = ""

    monkeypatch.setattr(evidence_extraction, "get_settings", lambda: _FakeSettings())
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        evidence_extraction.embed_texts(["some text"])
