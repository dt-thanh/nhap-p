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

from src.models.tables import (
    expert_profiles,
    ranking_evidence_document_chunks,
    ranking_evidence_document_lifecycle_events,
    ranking_evidence_documents,
    ranking_evidence_extraction_attempts,
)
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


# --- sanitize_text_for_postgres (thuần, không đụng DB) --------------------------
# Ranh giới DUY NHẤT trước khi text dẫn xuất từ parser chạm cột PostgreSQL
# `text` — xem incident SQLSTATE 22021 ("invalid byte sequence for encoding
# \"UTF8\": 0x00") tại `src/jobs/extract_evidence.py::_extract_text_pages`.


async def test_sanitize_removes_nul_bytes_deterministically():
    result = evidence_extraction.sanitize_text_for_postgres("abc\x00def\x00ghi")
    assert result.text == "abcdefghi"
    assert result.nul_removed == 2
    assert result.controls_removed == 0
    assert result.input_length == 11
    assert result.output_length == 9


async def test_sanitize_preserves_newline_tab_carriage_return():
    text = "line1\nline2\tcol\rline3"
    result = evidence_extraction.sanitize_text_for_postgres(text)
    assert result.text == text
    assert result.nul_removed == 0
    assert result.controls_removed == 0


async def test_sanitize_removes_other_c0_and_c1_controls_but_counts_them_separately_from_nul():
    text = "a\x01b\x02c\x1fd\x7fe\x80f\x9fg\x00h"
    result = evidence_extraction.sanitize_text_for_postgres(text)
    assert result.text == "abcdefgh"
    assert result.nul_removed == 1
    assert result.controls_removed == 6


async def test_sanitize_preserves_vietnamese_diacritics_urls_m2_percent_dashes_quotes():
    text = (
        "Diện tích 75 m², giá 55–70 triệu/m², “ưu đãi” 12% lãi suất, "
        "xem https://phatdat.com.vn/la-pura — báo cáo #16."
    )
    result = evidence_extraction.sanitize_text_for_postgres(text)
    assert result.text == text
    assert result.nul_removed == 0
    assert result.controls_removed == 0


async def test_sanitize_normalizes_unicode_to_nfc():
    decomposed = "a" + "\u0301"  # 'a' + COMBINING ACUTE ACCENT (NFD, 2 codepoints)
    precomposed = "\u00e1"  # LATIN SMALL LETTER A WITH ACUTE (NFC, 1 codepoint)
    assert len(decomposed) == 2  # sanity: the input really is decomposed
    assert decomposed != precomposed

    result = evidence_extraction.sanitize_text_for_postgres(decomposed)

    assert result.text == precomposed
    assert result.output_length == 1


async def test_sanitize_none_or_empty_is_a_safe_noop():
    result = evidence_extraction.sanitize_text_for_postgres(None)
    assert result == evidence_extraction.SanitizedTextResult(
        text="", nul_removed=0, controls_removed=0, input_length=0, output_length=0
    )
    assert evidence_extraction.sanitize_text_for_postgres("").text == ""


async def test_insert_chunks_sanitizes_content_defense_in_depth(factory):
    """Even if a future caller bypasses the parser-boundary sanitizer, the
    single INSERT boundary into `ranking_evidence_document_chunks` (this
    function — see module docstring's allowed-writer note) must never let a
    NUL byte reach PostgreSQL text storage."""
    document_id = await _document(factory)
    dirty = _chunk(0, content="Trang 3: abc\x00def")

    count = await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [dirty])

    assert count == 1
    chunks = await evidence_extraction.get_chunks_for_document(document_id)
    assert "\x00" not in chunks[0]["content"]
    assert chunks[0]["content"] == "Trang 3: abcdef"


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


async def test_stale_pending_attempt_can_be_superseded_for_recovery(factory):
    document_id = await _document(factory)
    old = datetime.now(UTC).replace(year=2020)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(), document_id=document_id, status="pending", created_at=old
            )
        )
        await session.commit()

    assert await evidence_extraction.request_extraction(document_id) == "pending"
    async with factory() as session:
        count = await session.scalar(
            sa.text("SELECT count(*) FROM ranking_evidence_extraction_attempts WHERE document_id = :id"),
            {"id": document_id},
        )
    assert count == 2


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


async def test_terminal_failure_cannot_overwrite_a_later_pending_attempt(factory):
    document_id = await _document(factory)
    await evidence_extraction.request_extraction(document_id)
    first = await evidence_extraction.latest_extraction_attempt(document_id)
    assert first is not None
    assert await evidence_extraction.mark_extraction_attempt_failed(
        document_id, attempt_id=first["id"], status="failed", error_code="PARSER_FAILED"
    ) is True
    assert await evidence_extraction.request_extraction(document_id) == "pending"
    second = await evidence_extraction.latest_extraction_attempt(document_id)
    assert second is not None and second["id"] != first["id"]
    assert await evidence_extraction.mark_extraction_attempt_failed(
        document_id, attempt_id=first["id"], status="failed", error_code="PARSER_FAILED"
    ) is False
    latest = await evidence_extraction.latest_extraction_attempt(document_id)
    assert latest is not None and latest["id"] == second["id"] and latest["status"] == "pending"


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


async def test_reprocess_reuses_existing_successful_chunks_without_duplicates(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)])
    assert await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)]) == 1
    assert len(await evidence_extraction.get_chunks_for_document(document_id)) == 1


# --- Authoritative document readiness ------------------------------------------


async def test_legacy_not_requested_row_with_current_success_chunk_and_embedding_is_eligible(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)])

    readiness = await evidence_extraction.get_document_readiness(document_id)

    assert readiness is not None
    assert readiness.extraction_status == "succeeded"
    assert readiness.lifecycle_status == "active"
    assert readiness.eligible is True
    assert readiness.chunk_count == readiness.embedded_chunk_count == 1


async def test_latest_failed_attempt_makes_old_chunk_ineligible(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)])
    await evidence_extraction.mark_extraction_attempt_failed(document_id, status="failed")

    readiness = await evidence_extraction.get_document_readiness(document_id)

    assert readiness is not None
    assert readiness.extraction_status == "failed"
    assert readiness.eligible is False
    assert readiness.reason == "EXTRACTION_NOT_SUCCEEDED"
    assert await evidence_extraction.get_chunks_for_document(document_id) == []


async def test_succeeded_attempt_without_chunks_is_ineligible(factory):
    document_id = await _document(factory)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(), document_id=document_id, status="succeeded", created_at=datetime.now(UTC)
            )
        )
        await session.commit()

    readiness = await evidence_extraction.get_document_readiness(document_id)

    assert readiness is not None
    assert readiness.eligible is False
    assert readiness.reason == "SUCCEEDED_WITHOUT_CHUNKS"


async def test_stale_chunk_without_current_succeeded_attempt_cannot_bypass_readiness(factory):
    document_id = await _document(factory)
    stale_chunk_id = uuid.uuid4()
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_evidence_document_chunks).values(
                id=stale_chunk_id, document_id=document_id, created_at=datetime.now(UTC), **_chunk(0)
            )
        )
        await session.commit()

    readiness = await evidence_extraction.get_document_readiness(document_id)

    assert readiness is not None
    assert readiness.chunk_count == 1
    assert readiness.extraction_status == "not_requested"
    assert readiness.eligible is False
    assert await evidence_extraction.get_chunks_for_document(document_id) == []
    assert await evidence_extraction.search_similar_chunks([document_id], _VECTOR, top_k=1) == []


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


# --- Mandatory-scope item 4: archived/deleted documents excluded from ------
# --- retrieval, even when their id is explicitly supplied by the caller ----


async def _lifecycle_event(factory, document_id: uuid.UUID, event_type: str) -> None:
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_evidence_document_lifecycle_events).values(
                id=uuid.uuid4(),
                document_id=document_id,
                event_type=event_type,
                actor_expert_id=EXPERT_ID,
                reason=None,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def test_search_similar_chunks_excludes_an_archived_document(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0, content="archived chunk")])
    await _lifecycle_event(factory, document_id, "archived")

    results = await evidence_extraction.search_similar_chunks([document_id], _VECTOR, top_k=5)

    assert results == [], "a stale caller-supplied document_id must not surface an archived document's chunks"
    assert (await evidence_extraction.get_document_readiness(document_id)).eligible is False


async def test_search_similar_chunks_excludes_a_deleted_document(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0, content="deleted chunk")])
    await _lifecycle_event(factory, document_id, "deleted")

    assert await evidence_extraction.search_similar_chunks([document_id], _VECTOR, top_k=5) == []
    assert (await evidence_extraction.get_document_readiness(document_id)).eligible is False


async def test_search_similar_chunks_includes_a_restored_document(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0, content="restored chunk")])
    await _lifecycle_event(factory, document_id, "archived")
    await _lifecycle_event(factory, document_id, "restored")

    results = await evidence_extraction.search_similar_chunks([document_id], _VECTOR, top_k=5)

    assert [r["content"] for r in results] == ["restored chunk"]


async def test_get_chunks_for_document_excludes_an_archived_document(factory):
    document_id = await _document(factory)
    await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, [_chunk(0)])
    assert len(await evidence_extraction.get_chunks_for_document(document_id)) == 1

    await _lifecycle_event(factory, document_id, "archived")
    assert await evidence_extraction.get_chunks_for_document(document_id) == []


# --- embed_texts: không gọi OpenAI thật, chỉ kiểm nhánh thiếu API key ------------


async def test_embed_texts_without_api_key_raises(monkeypatch):
    class _FakeSettings:
        resolved_llm_api_key = ""

    monkeypatch.setattr(evidence_extraction, "get_settings", lambda: _FakeSettings())
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        evidence_extraction.embed_texts(["some text"])
