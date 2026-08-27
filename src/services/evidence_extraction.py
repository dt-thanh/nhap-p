"""Chunk store + extraction-status log for evidence documents (0035, §21.4-§21.7).

╔══════════════════════════════════════════════════════════════════════════════╗
║  Nơi ghi DUY NHẤT vào `ranking_evidence_document_chunks` và                  ║
║  `ranking_evidence_extraction_attempts`: xem `EVIDENCE_CHUNK_ALLOWED_WRITERS` ║
║  trong `tests/test_ranking_boundary.py`.                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

Tách khỏi `src/services/governance.py` có chủ đích — xem docstring module đó:
dữ liệu ở đây là KẾT QUẢ SUY RA (chunk + embedding, log trạng thái trích xuất),
không phải input trực tiếp của chuyên gia.

`ranking_evidence_documents.extraction_status` (0034) KHÔNG phải trạng thái
thật — bảng đó bị `ranking_governance_append_only_guard` chặn UPDATE/DELETE,
nên cột đó đứng yên ở `'not_requested'` từ lúc đăng ký. Trạng thái thật là dòng
mới nhất trong `ranking_evidence_extraction_attempts` — xem docstring migration
`0035_evidence_document_chunks.py`. Mọi hàm ở đây chỉ INSERT vào cả hai bảng,
không có khái niệm "sửa" một chunk hay một lần thử đã ghi.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from langchain_openai import OpenAIEmbeddings

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import (
    ranking_evidence_document_chunks,
    ranking_evidence_documents,
    ranking_evidence_extraction_attempts,
)

log = get_logger("src.services.evidence_extraction")

EXTRACTION_STATUSES = ("pending", "succeeded", "failed", "not_supported")
_TERMINAL_OR_IN_FLIGHT = ("pending", "succeeded")

# text-embedding-3-small (D16). Phải khớp `Vector(1536)` ở migration 0035 —
# đổi model là một migration ALTER cột, không phải đổi hằng số này một mình.
EMBEDDING_MODEL = "text-embedding-3-small"


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Một nơi DUY NHẤT gọi OpenAI embeddings — dùng bởi cả job chunk+embed
    (`src/jobs/extract_evidence.py`) lẫn truy hồi RAG (`retrieve_and_validate`,
    §21.7), để không có hai client OpenAI cấu hình khác nhau âm thầm lệch
    model. Không gọi API thật trong test — luôn monkeypatch hàm này."""
    settings = get_settings()
    key = settings.resolved_llm_api_key
    if not key:
        raise RuntimeError("LLM_API_KEY/OPENAI_API_KEY chưa được cấu hình — không thể tạo embedding")
    embedder = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=key)
    return embedder.embed_documents(texts)


class ExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


async def get_document(document_id: uuid.UUID) -> dict | None:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ranking_evidence_documents).where(ranking_evidence_documents.c.id == document_id)
            )
        ).mappings().first()
        await session.rollback()
    return dict(row) if row else None


async def latest_extraction_status(document_id: uuid.UUID) -> str:
    """Current status = latest attempt row. Falls back to 'not_requested' when
    no attempt has ever been logged (mirrors the column default in 0034)."""
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ranking_evidence_extraction_attempts.c.status)
                .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                .order_by(ranking_evidence_extraction_attempts.c.created_at.desc())
                .limit(1)
            )
        ).first()
        await session.rollback()
    return row[0] if row else "not_requested"


async def request_extraction(document_id: uuid.UUID) -> str:
    """Idempotent per §21.5: a document already 'pending' or 'succeeded' is
    returned as-is, no new attempt logged. Returns the resulting status."""
    document = await get_document(document_id)
    if document is None:
        raise ExtractionError("DOCUMENT_NOT_FOUND", f"Không có ranking_evidence_documents {document_id}")

    current = await latest_extraction_status(document_id)
    if current in _TERMINAL_OR_IN_FLIGHT:
        return current

    async with get_session_factory()() as session:
        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(), document_id=document_id, status="pending", created_at=_now()
            )
        )
        await session.commit()
    log.info("evidence_extraction.requested", document_id=str(document_id))
    return "pending"


async def mark_extraction_attempt_failed(
    document_id: uuid.UUID, *, status: str, error_summary: str | None = None
) -> None:
    if status not in ("failed", "not_supported"):
        raise ValueError(f"status phải là 'failed' hoặc 'not_supported', nhận '{status}'")
    async with get_session_factory()() as session:
        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(),
                document_id=document_id,
                status=status,
                error_summary=error_summary,
                created_at=_now(),
            )
        )
        await session.commit()
    log.warning(
        "evidence_extraction.attempt_failed",
        document_id=str(document_id),
        status=status,
        error_summary=error_summary,
    )


async def insert_chunks_and_mark_succeeded(document_id: uuid.UUID, chunks: list[dict[str, Any]]) -> int:
    """`chunks`: list of {chunk_index, page_number, content, token_count,
    embedding_model, embedding}. One transaction across both tables — either
    the whole extraction lands or none of it does. `uq_redc_document_chunk`
    (0035) makes a duplicate call for an already-chunked document fail loudly
    on `IntegrityError` rather than silently double-writing — callers are
    expected to check `latest_extraction_status` first (the RQ job does)."""
    if not chunks:
        raise ExtractionError("NO_CHUNKS", "Không có chunk nào để ghi — trích xuất coi như thất bại")
    async with get_session_factory()() as session:
        try:
            await session.execute(
                sa.insert(ranking_evidence_document_chunks),
                [
                    {
                        "id": uuid.uuid4(),
                        "document_id": document_id,
                        "chunk_index": chunk["chunk_index"],
                        "page_number": chunk.get("page_number"),
                        "content": chunk["content"],
                        "token_count": chunk["token_count"],
                        "embedding_model": chunk["embedding_model"],
                        "embedding": chunk["embedding"],
                        "created_at": _now(),
                    }
                    for chunk in chunks
                ],
            )
        except sa.exc.IntegrityError as exc:
            await session.rollback()
            raise ExtractionError(
                "CHUNKS_ALREADY_EXIST",
                f"Document {document_id} đã có chunk ở chunk_index đó (job chạy trùng?)",
            ) from exc

        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(), document_id=document_id, status="succeeded", created_at=_now()
            )
        )
        await session.commit()
    log.info("evidence_extraction.succeeded", document_id=str(document_id), chunk_count=len(chunks))
    return len(chunks)


async def get_chunks_for_document(document_id: uuid.UUID) -> list[dict]:
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_evidence_document_chunks)
                .where(ranking_evidence_document_chunks.c.document_id == document_id)
                .order_by(ranking_evidence_document_chunks.c.chunk_index)
            )
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]


async def search_similar_chunks(
    document_ids: list[uuid.UUID], query_embedding: list[float], *, top_k: int
) -> list[dict]:
    """Cosine-distance search restricted to `document_ids` — never a
    corpus-wide query (§21.7's "never a cross-proposal query" guarantee).
    Empty `document_ids` returns `[]` without a query: an empty `IN ()` is
    valid SQL but a wasted round trip for a case the caller already knows the
    answer to (no documents linked to this justification)."""
    if not document_ids:
        return []
    async with get_session_factory()() as session:
        distance = ranking_evidence_document_chunks.c.embedding.cosine_distance(query_embedding).label(
            "distance"
        )
        rows = (
            await session.execute(
                sa.select(ranking_evidence_document_chunks, distance)
                .where(ranking_evidence_document_chunks.c.document_id.in_(document_ids))
                .order_by(distance)
                .limit(top_k)
            )
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]
