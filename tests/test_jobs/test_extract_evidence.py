"""`src/jobs/extract_evidence.py` — parse → chunk → embed → upsert (§21.6).

Không gọi OpenAI thật: `_embed_texts` luôn được monkeypatch. Không cần
`settings.upload_dir` thật: `_load_bytes` được monkeypatch trực tiếp — bài
kiểm tra là logic của JOB (idempotency, trạng thái, ranh giới lỗi có chủ đích
vs lỗi hạ tầng), không phải hành vi của pypdf/OpenAI, vốn là trách nhiệm của
chính hai thư viện đó.

Chạy bằng `bash scripts/test_db.sh` cho các test đụng DB thật; các test thuần
(chunking, page extraction) không cần DB và tự bỏ qua qua `pytestmark` bên dưới
vì toàn file import `evidence_extraction` (đụng DB ở fixture, không ở import).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pypdf
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.jobs import extract_evidence
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


def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.001] * 1536 for _ in texts]


@pytest_asyncio.fixture
async def factory(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(
        "src.services.evidence_extraction.get_session_factory", lambda sf=session_factory: sf, raising=False
    )
    monkeypatch.setattr(evidence_extraction, "embed_texts", _fake_embed)

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
            sa.insert(expert_profiles).values(id=EXPERT_ID, identity_subject="extract-job-test", status="active")
        )
        await session.commit()

    yield session_factory

    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    await engine.dispose()


async def _document(factory, *, mime_type: str = "text/plain", checksum_suffix: str = "1") -> uuid.UUID:
    document_id = uuid.uuid4()
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_evidence_documents).values(
                id=document_id,
                uploaded_by_expert_id=EXPERT_ID,
                original_filename="evidence.txt",
                mime_type=mime_type,
                object_storage_key=f"ranking/evidence/{document_id}",
                sha256_checksum=(checksum_suffix * 64)[:64],
                file_size_bytes=10,
                extraction_status="not_requested",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return document_id


# --- Trích trang PDF/text — thuần, không đụng DB --------------------------------


async def test_extract_text_pages_for_plain_text_is_one_page_with_no_number():
    pages = extract_evidence._extract_text_pages("text/plain", b"hello world")
    assert [(page_number, text) for page_number, text, _result in pages] == [(None, "hello world")]
    assert pages[0][2].nul_removed == 0
    assert pages[0][2].controls_removed == 0


async def test_extract_text_pages_for_pdf_numbers_pages_from_one(monkeypatch):
    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, _stream) -> None:
            self.pages = [FakePage("page one text"), FakePage("page two text")]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)

    pages = extract_evidence._extract_text_pages("application/pdf", b"%PDF-fake%")

    assert [(page_number, text) for page_number, text, _result in pages] == [
        (1, "page one text"),
        (2, "page two text"),
    ]


async def test_extract_text_pages_sanitizes_nul_bytes_from_pypdf(monkeypatch):
    """Reproduces the confirmed incident: pypdf emits U+0000 on some pages
    from an incomplete ToUnicode/CMap glyph mapping (PostgreSQL later rejects
    this with SQLSTATE 22021 if left unsanitized). Sanitization must happen
    here, before chunking, so downstream chunk boundaries/token counts are
    based on the already-clean text."""

    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, _stream) -> None:
            self.pages = [
                FakePage("Trang 1: nội dung sạch"),
                FakePage("Trang 2: nội dung sạch khác"),
                FakePage("Trang 3: abc\x00def"),
                FakePage("Trang 4: nội dung sạch"),
                FakePage("Trang 5: ghi\x00chu"),
            ]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)

    pages = extract_evidence._extract_text_pages("application/pdf", b"%PDF-fake%")
    texts = [text for _page_number, text, _result in pages]

    assert "\x00" not in "".join(texts)
    assert texts[2] == "Trang 3: abcdef"
    assert texts[4] == "Trang 5: ghichu"
    assert pages[2][2].nul_removed == 1
    assert pages[4][2].nul_removed == 1
    assert pages[0][2].nul_removed == 0  # clean pages report zero removal
    assert pages[1][2].nul_removed == 0


# --- Chunking — thuần, không đụng DB ---------------------------------------------


async def test_split_into_chunk_rows_indexes_sequentially_across_pages():
    pages = [(1, "short first page text."), (2, "short second page text.")]
    rows = extract_evidence._split_into_chunk_rows(pages)

    assert [r["chunk_index"] for r in rows] == list(range(len(rows)))
    assert rows[0]["page_number"] == 1
    assert rows[-1]["page_number"] == 2


async def test_split_into_chunk_rows_drops_blank_pieces():
    rows = extract_evidence._split_into_chunk_rows([(1, "   "), (2, "")])
    assert rows == []


# --- Job end-to-end (DB thật, embed giả) ------------------------------------------


async def test_unsupported_mime_type_marks_not_supported_without_raising(factory, monkeypatch):
    # `ranking_evidence_documents.ck_red_mime_type` (0034) already restricts
    # `mime_type` to the three supported values at the DB level — a row with
    # an unsupported mime type can never actually be registered. This branch
    # is defense-in-depth for if that CHECK constraint is ever loosened
    # without `SUPPORTED_MIME_TYPES` being updated to match, so it's exercised
    # here via a monkeypatched `get_document` rather than a real insert.
    document_id = await _document(factory, mime_type="text/plain")

    async def fake_get_document(doc_id):
        return {"id": doc_id, "mime_type": "application/octet-stream", "object_storage_key": "irrelevant"}

    monkeypatch.setattr(evidence_extraction, "get_document", fake_get_document)

    result = await extract_evidence._run(document_id)
    assert result == {"status": "not_supported", "chunk_count": 0, "skipped": False}
    assert await evidence_extraction.latest_extraction_status(document_id) == "not_supported"


async def test_unreadable_file_marks_failed_without_raising(factory, monkeypatch):
    document_id = await _document(factory, mime_type="text/plain")
    monkeypatch.setattr(
        extract_evidence, "_load_bytes", lambda key: (_ for _ in ()).throw(FileNotFoundError(key))
    )

    result = await extract_evidence._run(document_id)

    assert result["status"] == "failed"
    assert await evidence_extraction.latest_extraction_status(document_id) == "failed"


async def test_no_extractable_text_marks_failed(factory, monkeypatch):
    document_id = await _document(factory, mime_type="text/plain")
    monkeypatch.setattr(extract_evidence, "_load_bytes", lambda key: b"   ")

    result = await extract_evidence._run(document_id)

    assert result["status"] == "failed"
    assert await evidence_extraction.latest_extraction_status(document_id) == "failed"


async def test_successful_extraction_persists_chunks_with_embeddings(factory, monkeypatch):
    document_id = await _document(factory, mime_type="text/plain")
    monkeypatch.setattr(extract_evidence, "_load_bytes", lambda key: b"the project sold twelve units in July.")

    result = await extract_evidence._run(document_id)

    assert result == {"status": "succeeded", "chunk_count": 1, "skipped": False}
    chunks = await evidence_extraction.get_chunks_for_document(document_id)
    assert len(chunks) == 1
    assert chunks[0]["embedding_model"] == extract_evidence.EMBEDDING_MODEL
    assert chunks[0]["page_number"] is None


async def test_chunk_persistence_failure_is_terminal_and_retryable(factory, monkeypatch):
    """A DB failure after claim must not strand the attempt in pending."""
    document_id = await _document(factory, mime_type="text/plain")
    await evidence_extraction.request_extraction(document_id)
    monkeypatch.setattr(extract_evidence, "_load_bytes", lambda key: b"retryable content")
    original_insert = evidence_extraction.insert_chunks_and_mark_succeeded

    async def fail_insert(*args, **kwargs):
        raise RuntimeError('invalid byte sequence for encoding "UTF8": 0x00')

    monkeypatch.setattr(evidence_extraction, "insert_chunks_and_mark_succeeded", fail_insert)
    result = await extract_evidence._run(document_id)

    assert result["status"] == "failed"
    readiness = await evidence_extraction.get_document_readiness(document_id)
    assert readiness is not None
    assert readiness.extraction_status == "failed"
    assert readiness.error_code == "CHUNK_PERSISTENCE_FAILED"
    assert readiness.chunk_count == readiness.embedded_chunk_count == 0
    assert await evidence_extraction.get_chunks_for_document(document_id) == []

    failed_attempt = await evidence_extraction.latest_extraction_attempt(document_id)
    assert failed_attempt is not None
    assert failed_attempt["status"] == "failed"
    failed_id = failed_attempt["id"]
    assert await evidence_extraction.request_extraction(document_id) == "pending"
    pending_attempt = await evidence_extraction.latest_extraction_attempt(document_id)
    assert pending_attempt is not None
    assert pending_attempt["id"] != failed_id

    monkeypatch.setattr(evidence_extraction, "insert_chunks_and_mark_succeeded", original_insert)
    assert (await extract_evidence._run(document_id))["status"] == "succeeded"
    assert await evidence_extraction.latest_extraction_status(document_id) == "succeeded"


async def test_nul_byte_from_pypdf_is_sanitized_end_to_end_and_extraction_succeeds(factory, monkeypatch):
    """Regression for the confirmed incident: pypdf emitted U+0000 on pages 3
    and 5 (incomplete ToUnicode/CMap glyph mapping); PostgreSQL rejected the
    resulting chunk INSERT with SQLSTATE 22021
    ('invalid byte sequence for encoding "UTF8": 0x00'), rolling back every
    chunk for that run. This must now succeed end-to-end through the real
    `_run` job + `insert_chunks_and_mark_succeeded` persistence path."""
    document_id = await _document(factory, mime_type="application/pdf")

    async with factory() as session:
        before = (
            await session.execute(
                sa.select(
                    ranking_evidence_documents.c.sha256_checksum,
                    ranking_evidence_documents.c.file_size_bytes,
                    ranking_evidence_documents.c.original_filename,
                ).where(ranking_evidence_documents.c.id == document_id)
            )
        ).one()
        await session.rollback()

    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, _stream) -> None:
            self.pages = [
                FakePage("Trang 1: Diện tích 75 m², giá 55–70 triệu/m²."),
                FakePage("Trang 2: “Ưu đãi” 12% lãi suất năm đầu."),
                FakePage("Trang 3: abc\x00def"),
                FakePage("Trang 4: dữ liệu sạch không đổi."),
                FakePage("Trang 5: ghi\x00chu quan trọng về dự án."),
            ]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)
    monkeypatch.setattr(extract_evidence, "_load_bytes", lambda key: b"%PDF-fake-bytes-unchanged%")

    result = await extract_evidence._run(document_id)

    assert result == {"status": "succeeded", "chunk_count": 5, "skipped": False}

    # The original uploaded PDF row (checksum/size/filename) is registration
    # metadata this job never touches — only derived text may be sanitized.
    async with factory() as session:
        after = (
            await session.execute(
                sa.select(
                    ranking_evidence_documents.c.sha256_checksum,
                    ranking_evidence_documents.c.file_size_bytes,
                    ranking_evidence_documents.c.original_filename,
                ).where(ranking_evidence_documents.c.id == document_id)
            )
        ).one()
        await session.rollback()
    assert after.sha256_checksum == before.sha256_checksum
    assert after.file_size_bytes == before.file_size_bytes
    assert after.original_filename == before.original_filename

    chunks = await evidence_extraction.get_chunks_for_document(document_id)
    assert len(chunks) == 5
    for chunk in chunks:
        assert "\x00" not in chunk["content"]
        assert chunk["embedding"] is not None
        assert chunk["embedding_model"] == extract_evidence.EMBEDDING_MODEL

    by_page = {c["page_number"]: c["content"] for c in chunks}
    assert by_page[3] == "Trang 3: abcdef"
    assert by_page[5] == "Trang 5: ghichu quan trọng về dự án."
    # Vietnamese diacritics, m², %, dashes, and curly quotes on clean pages
    # must survive sanitization byte-for-byte.
    assert by_page[1] == "Trang 1: Diện tích 75 m², giá 55–70 triệu/m²."
    assert by_page[2] == "Trang 2: “Ưu đãi” 12% lãi suất năm đầu."
    assert by_page[4] == "Trang 4: dữ liệu sạch không đổi."

    assert await evidence_extraction.latest_extraction_status(document_id) == "succeeded"
    readiness = await evidence_extraction.get_document_readiness(document_id)
    assert readiness is not None
    assert readiness.eligible is True
    assert readiness.chunk_count == readiness.embedded_chunk_count == 5

    # RAG retrieval can use the resulting document once chunks/embeddings commit.
    hits = await evidence_extraction.search_similar_chunks(
        [document_id], [0.001] * 1536, top_k=5
    )
    assert len(hits) == 5


async def test_embedding_provider_failure_is_terminal(factory, monkeypatch):
    document_id = await _document(factory, mime_type="text/plain")
    await evidence_extraction.request_extraction(document_id)
    monkeypatch.setattr(extract_evidence, "_load_bytes", lambda key: b"provider failure content")
    monkeypatch.setattr(
        evidence_extraction,
        "embed_texts",
        lambda texts: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )

    result = await extract_evidence._run(document_id)

    assert result["status"] == "failed"
    readiness = await evidence_extraction.get_document_readiness(document_id)
    assert readiness is not None and readiness.error_code == "EMBEDDING_FAILED"


async def test_unexpected_chunk_preparation_failure_is_terminal(factory, monkeypatch):
    document_id = await _document(factory, mime_type="text/plain")
    await evidence_extraction.request_extraction(document_id)
    monkeypatch.setattr(extract_evidence, "_load_bytes", lambda key: b"unexpected failure content")
    monkeypatch.setattr(
        extract_evidence,
        "_split_into_chunk_rows",
        lambda pages: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )

    result = await extract_evidence._run(document_id)

    assert result["status"] == "failed"
    readiness = await evidence_extraction.get_document_readiness(document_id)
    assert readiness is not None and readiness.error_code == "PARSER_FAILED"


async def test_already_succeeded_document_is_skipped_not_reprocessed(factory, monkeypatch):
    document_id = await _document(factory, mime_type="text/plain")
    monkeypatch.setattr(extract_evidence, "_load_bytes", lambda key: b"first run content.")
    first = await extract_evidence._run(document_id)
    assert first["status"] == "succeeded"

    second = await extract_evidence._run(document_id)

    assert second == {"status": "succeeded", "chunk_count": None, "skipped": True}
    chunks = await evidence_extraction.get_chunks_for_document(document_id)
    assert len(chunks) == 1  # không nhân đôi


async def test_document_not_found_raises_for_rq_to_see(factory):
    with pytest.raises(evidence_extraction.ExtractionError) as exc:
        await extract_evidence._run(uuid.uuid4())
    assert exc.value.code == "DOCUMENT_NOT_FOUND"


# `extract_and_embed_evidence_document` (the sync RQ entrypoint) itself calls
# `asyncio.run(...)` — calling it from inside a test that's already running in
# pytest-asyncio's event loop would raise "asyncio.run() cannot be called from
# a running event loop". `tests/test_jobs/test_recompute_domain_worker.py`
# hits the same constraint for `run_domain_recompute` and keeps its sync
# entrypoint coverage in its own fully-synchronous file for that reason; `_run`
# above already covers every code path the sync wrapper would otherwise need
# a separate file to re-verify.
