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

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from langchain_openai import OpenAIEmbeddings

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import (
    ranking_evidence_document_chunks,
    ranking_evidence_document_lifecycle_events,
    ranking_evidence_documents,
    ranking_evidence_extraction_attempts,
)

log = get_logger("src.services.evidence_extraction")

EXTRACTION_STATUSES = ("pending", "succeeded", "failed", "not_supported")
_TERMINAL_OR_IN_FLIGHT = ("pending", "succeeded")
_SAFE_ERROR_CODES = {
    "PARSER_FAILED",
    "EMBEDDING_FAILED",
    "CHUNK_PERSISTENCE_FAILED",
    "DATABASE_TRANSACTION_FAILED",
    "UNSUPPORTED_DOCUMENT",
    "ENQUEUE_FAILED",
    "UNEXPECTED_EXTRACTION_FAILURE",
}

# text-embedding-3-small (D16). Phải khớp `Vector(1536)` ở migration 0035 —
# đổi model là một migration ALTER cột, không phải đổi hằng số này một mình.
EMBEDDING_MODEL = "text-embedding-3-small"

# C0 controls PostgreSQL/RAG text nên loại bỏ, trừ ba ký tự được giữ nguyên.
_PRESERVED_CONTROL_CHARS = frozenset("\n\r\t")


@dataclass(frozen=True)
class SanitizedTextResult:
    """Kết quả `sanitize_text_for_postgres` — chỉ số AN TOÀN để log, không bao
    giờ chứa văn bản gốc."""

    text: str
    nul_removed: int
    controls_removed: int
    input_length: int
    output_length: int


def sanitize_text_for_postgres(text: str | None) -> SanitizedTextResult:
    """Ranh giới DUY NHẤT chuẩn hoá văn bản trước khi nó chạm bất kỳ cột
    PostgreSQL `text` nào ở pipeline evidence (chunk content, và bất kỳ text
    nào khác có nguồn gốc từ parser). PostgreSQL từ chối NUL vô điều kiện —
    `invalid byte sequence for encoding "UTF8": 0x00` (SQLSTATE 22021) — bất kể
    encoding, nên đây không phải lỗi có thể sửa bằng cấu hình DB.

    KHÔNG áp dụng cho: bytes PDF gốc, checksum, tên file gốc, document_id, số
    trang, hay vector embedding — chỉ văn bản dẫn xuất (extracted/chunk text).

    - Chuẩn hoá Unicode NFC.
    - Loại bỏ U+0000 vô điều kiện.
    - Loại bỏ control C0 (trừ \\n \\r \\t) và C1 (U+007F, U+0080–U+009F).
    - Giữ nguyên mọi ký tự khác — dấu tiếng Việt, dấu câu Unicode, URL, số,
      m², %, gạch ngang, dấu ngoặc kép, ký tự Markdown.
    """
    if not text:
        return SanitizedTextResult(text="", nul_removed=0, controls_removed=0, input_length=0, output_length=0)
    normalized = unicodedata.normalize("NFC", text)
    input_length = len(normalized)
    nul_removed = 0
    controls_removed = 0
    out_chars: list[str] = []
    for ch in normalized:
        if ch == "\x00":
            nul_removed += 1
            continue
        if ch in _PRESERVED_CONTROL_CHARS:
            out_chars.append(ch)
            continue
        codepoint = ord(ch)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            controls_removed += 1
            continue
        out_chars.append(ch)
    output = "".join(out_chars)
    return SanitizedTextResult(
        text=output,
        nul_removed=nul_removed,
        controls_removed=controls_removed,
        input_length=input_length,
        output_length=len(output),
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Một nơi DUY NHẤT gọi OpenAI embeddings — dùng bởi cả job chunk+embed
    (`src/jobs/extract_evidence.py`) lẫn truy hồi RAG (`retrieve_and_validate`,
    §21.7), để không có hai client OpenAI cấu hình khác nhau âm thầm lệch
    model. Không gọi API thật trong test — luôn monkeypatch hàm này."""
    settings = get_settings()
    embedding_secret = getattr(settings, "embedding_api_key", None)
    if embedding_secret is None:
        # Compatibility for older test/config adapters that exposed only the
        # resolved LLM credential; production settings use embedding_api_key.
        key = getattr(settings, "resolved_llm_api_key", "")
        missing_label = "LLM_API_KEY"
    else:
        key = embedding_secret.get_secret_value()
        missing_label = "EMBEDDING_API_KEY"
    if not key:
        raise RuntimeError(f"{missing_label} chưa được cấu hình — không thể tạo embedding")
    embedder = OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        api_key=key,
        base_url=settings.embedding_base_url,
    )
    return embedder.embed_documents(texts)


def _document_is_active(document_id_column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """Excludes a chunk whose parent document's latest lifecycle event
    (`ranking_evidence_document_lifecycle_events`, 0044) is `'archived'`/
    `'deleted'` — a stale caller-supplied document/chunk id must never
    resolve to content from an archived/deleted document, even if the
    caller's own scope check was skipped or bypassed (defense in depth)."""
    latest_event_type = (
        sa.select(ranking_evidence_document_lifecycle_events.c.event_type)
        .where(ranking_evidence_document_lifecycle_events.c.document_id == document_id_column)
        .order_by(ranking_evidence_document_lifecycle_events.c.created_at.desc())
        .limit(1)
        # Explicit, not auto-correlated: a caller whose OWN outer query
        # already joins `ranking_evidence_document_chunks` (e.g.
        # `governance.py::submit_proposal`'s direct-evidence count) would
        # otherwise have SQLAlchemy's auto-correlation reach into THIS
        # subquery too and strip its only FROM table, raising
        # "no FROM clauses due to auto-correlation". Correlating only against
        # the table `document_id_column` itself belongs to is always correct
        # and never ambiguous, regardless of what else the outer query joins.
        .correlate(document_id_column.table)
        .scalar_subquery()
    )
    return sa.or_(latest_event_type.is_(None), latest_event_type == "restored")


def document_is_ready(document_id_column: sa.ColumnElement) -> sa.ColumnElement[bool]:
    """SQL predicate for every new retrieval/governance use.

    The immutable document-row ``extraction_status`` is registration metadata,
    not the effective state. A stale chunk identifier cannot bypass this
    predicate because both parent lifecycle and latest attempt are checked.
    """
    latest_attempt = (
        sa.select(ranking_evidence_extraction_attempts.c.status)
        .where(ranking_evidence_extraction_attempts.c.document_id == document_id_column)
        .order_by(ranking_evidence_extraction_attempts.c.created_at.desc())
        .limit(1)
        .correlate(document_id_column.table)  # see _document_is_active's comment above
        .scalar_subquery()
    )
    has_embedded_chunk = sa.exists(
        sa.select(1).where(
            ranking_evidence_document_chunks.c.document_id == document_id_column,
            ranking_evidence_document_chunks.c.embedding.is_not(None),
        )
    ).correlate(document_id_column.table)
    return sa.and_(_document_is_active(document_id_column), latest_attempt == "succeeded", has_embedded_chunk)


class ExtractionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DocumentReadiness:
    """Authoritative read-only decision for whether a document may be reused."""

    document_id: uuid.UUID
    lifecycle_status: str
    extraction_status: str
    chunk_count: int
    embedded_chunk_count: int
    eligible: bool
    reason: str | None = None
    error_code: str | None = None
    error_summary: str | None = None


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
                sa.select(
                    ranking_evidence_extraction_attempts.c.status,
                    ranking_evidence_extraction_attempts.c.created_at,
                )
                .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                .order_by(ranking_evidence_extraction_attempts.c.created_at.desc())
                .limit(1)
            )
        ).first()
        await session.rollback()
    return row[0] if row else "not_requested"


async def latest_extraction_attempt(document_id: uuid.UUID) -> dict | None:
    """Return the latest immutable attempt row for worker race protection."""
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ranking_evidence_extraction_attempts)
                .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                .order_by(
                    ranking_evidence_extraction_attempts.c.created_at.desc(),
                    ranking_evidence_extraction_attempts.c.id.desc(),
                )
                .limit(1)
            )
        ).mappings().first()
        await session.rollback()
    return dict(row) if row else None


async def get_document_readiness(document_id: uuid.UUID) -> DocumentReadiness | None:
    """Resolve the effective lifecycle state without mutating historical rows."""
    async with get_session_factory()() as session:
        exists = await session.scalar(
            sa.select(ranking_evidence_documents.c.id).where(ranking_evidence_documents.c.id == document_id)
        )
        if exists is None:
            await session.rollback()
            return None
        event_type = await session.scalar(
            sa.select(ranking_evidence_document_lifecycle_events.c.event_type)
            .where(ranking_evidence_document_lifecycle_events.c.document_id == document_id)
            .order_by(ranking_evidence_document_lifecycle_events.c.created_at.desc())
            .limit(1)
        )
        attempt_row = (
            await session.execute(
                sa.select(
                    ranking_evidence_extraction_attempts.c.status,
                    ranking_evidence_extraction_attempts.c.error_code,
                    ranking_evidence_extraction_attempts.c.error_summary,
                )
                .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                .order_by(
                    ranking_evidence_extraction_attempts.c.created_at.desc(),
                    ranking_evidence_extraction_attempts.c.id.desc(),
                )
                .limit(1)
            )
        ).mappings().first()
        chunk_count = int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ranking_evidence_document_chunks)
                .where(ranking_evidence_document_chunks.c.document_id == document_id)
            )
            or 0
        )
        embedded_chunk_count = int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ranking_evidence_document_chunks)
                .where(
                    ranking_evidence_document_chunks.c.document_id == document_id,
                    ranking_evidence_document_chunks.c.embedding.is_not(None),
                )
            )
            or 0
        )
        await session.rollback()

    lifecycle = "active" if event_type in (None, "restored") else event_type
    extraction = (attempt_row or {}).get("status") or "not_requested"
    if lifecycle != "active":
        reason = "DOCUMENT_NOT_ACTIVE"
    elif extraction != "succeeded":
        reason = "EXTRACTION_NOT_SUCCEEDED"
    elif chunk_count == 0:
        reason = "SUCCEEDED_WITHOUT_CHUNKS"
    elif embedded_chunk_count == 0:
        reason = "CHUNK_EMBEDDING_MISSING"
    else:
        reason = None
    return DocumentReadiness(
        document_id=document_id,
        lifecycle_status=lifecycle,
        extraction_status=extraction,
        chunk_count=chunk_count,
        embedded_chunk_count=embedded_chunk_count,
        eligible=reason is None,
        reason=reason,
        error_code=(attempt_row or {}).get("error_code"),
        error_summary=(attempt_row or {}).get("error_summary"),
    )


async def request_extraction(document_id: uuid.UUID) -> str:
    """Idempotent per §21.5: a document already 'pending' or 'succeeded' is
    returned as-is, no new attempt logged. Returns the resulting status."""
    document = await get_document(document_id)
    if document is None:
        raise ExtractionError("DOCUMENT_NOT_FOUND", f"Không có ranking_evidence_documents {document_id}")

    async with get_session_factory()() as session:
        # The status log is append-only, so a unique index on status='pending'
        # would incorrectly include every historical pending row. Serialize
        # requests per document instead, including the no-existing-row race.
        await session.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(str(document_id)))))
        latest = (
            await session.execute(
                sa.select(
                    ranking_evidence_extraction_attempts.c.status,
                    ranking_evidence_extraction_attempts.c.created_at,
                )
                .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                .order_by(
                    ranking_evidence_extraction_attempts.c.created_at.desc(),
                    ranking_evidence_extraction_attempts.c.id.desc(),
                )
                .limit(1)
                .with_for_update()
            )
        ).first()
        current_status = latest.status if latest else None
        stale_pending = False
        if current_status == "pending" and latest.created_at is not None:
            stale_after = getattr(get_settings(), "evidence_pending_stale_seconds", 900)
            stale_pending = (_now() - latest.created_at).total_seconds() >= stale_after
        if current_status in ("succeeded",) or (current_status == "pending" and not stale_pending):
            await session.rollback()
            return current_status
        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(), document_id=document_id, status="pending", created_at=_now()
            )
        )
        await session.commit()
    log.info("evidence_extraction.requested", document_id=str(document_id))
    return "pending"


async def mark_extraction_attempt_failed(
    document_id: uuid.UUID,
    *,
    status: str,
    error_summary: str | None = None,
    error_code: str | None = None,
    attempt_id: uuid.UUID | None = None,
) -> bool:
    if status not in ("failed", "not_supported"):
        raise ValueError(f"status phải là 'failed' hoặc 'not_supported', nhận '{status}'")
    safe_error_code = error_code if error_code in _SAFE_ERROR_CODES else None
    safe_summary = (error_summary or "extraction failed").replace("\x00", "")[:500]
    async with get_session_factory()() as session:
        await session.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(str(document_id)))))
        latest = (
            await session.execute(
                sa.select(
                    ranking_evidence_extraction_attempts.c.id,
                    ranking_evidence_extraction_attempts.c.status,
                )
                .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                .order_by(
                    ranking_evidence_extraction_attempts.c.created_at.desc(),
                    ranking_evidence_extraction_attempts.c.id.desc(),
                )
                .limit(1)
                .with_for_update()
            )
        ).first()
        if attempt_id is not None and (latest is None or latest.id != attempt_id or latest.status != "pending"):
            await session.rollback()
            return False
        # Worker calls always provide an immutable attempt_id and therefore
        # cannot regress a later terminal attempt.  The legacy document-level
        # helper remains append-only for existing callers/tests that model a
        # new terminal observation without an attempt id.
        if attempt_id is not None and latest is not None and latest.status in ("succeeded", "failed", "not_supported"):
            await session.rollback()
            return False
        await session.execute(
            sa.insert(ranking_evidence_extraction_attempts).values(
                id=uuid.uuid4(),
                document_id=document_id,
                status=status,
                error_code=safe_error_code,
                error_summary=safe_summary,
                created_at=_now(),
            )
        )
        await session.commit()
    log.warning(
        "evidence_extraction.attempt_failed",
        document_id=str(document_id),
        status=status,
        error_summary=safe_summary,
    )
    return True


async def insert_chunks_and_mark_succeeded(
    document_id: uuid.UUID, chunks: list[dict[str, Any]], *, attempt_id: uuid.UUID | None = None
) -> int:
    """`chunks`: list of {chunk_index, page_number, content, token_count,
    embedding_model, embedding}. One transaction across both tables — either
    the whole extraction lands or none of it does. Existing immutable chunks
    from an earlier successful attempt are reused on an idempotent retry;
    this appends only a new success attempt and never duplicates chunk rows."""
    if not chunks:
        raise ExtractionError("NO_CHUNKS", "Không có chunk nào để ghi — trích xuất coi như thất bại")
    # Defense-in-depth: `content` phải đã được sanitize ở parser boundary
    # (`extract_evidence._extract_text_pages`), nhưng đây là điểm DUY NHẤT ghi
    # vào `ranking_evidence_document_chunks` (xem module docstring) nên áp lại
    # sanitizer ở đây vô điều kiện — rẻ, idempotent với text đã sạch, và chặn
    # bất kỳ caller/parser tương lai nào bỏ sót bước sanitize thượng nguồn.
    for chunk in chunks:
        result = sanitize_text_for_postgres(chunk["content"])
        chunk["content"] = result.text
        if result.nul_removed or result.controls_removed:
            log.warning(
                "evidence_extraction.text_sanitized",
                document_id=str(document_id),
                attempt_id=str(attempt_id) if attempt_id else None,
                stage="pre_insert",
                chunk_index=chunk.get("chunk_index"),
                page_number=chunk.get("page_number"),
                nul_removed=result.nul_removed,
                controls_removed=result.controls_removed,
                input_length=result.input_length,
                output_length=result.output_length,
            )
    async with get_session_factory()() as session:
        await session.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtext(str(document_id)))))
        latest = (
            await session.execute(
                sa.select(
                    ranking_evidence_extraction_attempts.c.id,
                    ranking_evidence_extraction_attempts.c.status,
                )
                .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                .order_by(
                    ranking_evidence_extraction_attempts.c.created_at.desc(),
                    ranking_evidence_extraction_attempts.c.id.desc(),
                )
                .limit(1)
                .with_for_update()
            )
        ).first()
        if attempt_id is not None and (latest is None or latest.id != attempt_id or latest.status != "pending"):
            await session.rollback()
            raise ExtractionError("STALE_ATTEMPT", "Extraction attempt is no longer the active pending attempt")
        existing_chunk_count = int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ranking_evidence_document_chunks)
                .where(ranking_evidence_document_chunks.c.document_id == document_id)
            )
            or 0
        )
        if existing_chunk_count:
            prior_success = await session.scalar(
                sa.select(ranking_evidence_extraction_attempts.c.id)
                .where(
                    ranking_evidence_extraction_attempts.c.document_id == document_id,
                    ranking_evidence_extraction_attempts.c.status == "succeeded",
                )
                .limit(1)
            )
            has_embedding = await session.scalar(
                sa.select(ranking_evidence_document_chunks.c.id)
                .where(
                    ranking_evidence_document_chunks.c.document_id == document_id,
                    ranking_evidence_document_chunks.c.embedding.is_not(None),
                )
                .limit(1)
            )
            if prior_success is not None and has_embedding is not None:
                latest = await session.scalar(
                    sa.select(ranking_evidence_extraction_attempts.c.status)
                    .where(ranking_evidence_extraction_attempts.c.document_id == document_id)
                    .order_by(ranking_evidence_extraction_attempts.c.created_at.desc())
                    .limit(1)
                )
                if latest != "succeeded":
                    await session.execute(
                        sa.insert(ranking_evidence_extraction_attempts).values(
                            id=uuid.uuid4(), document_id=document_id, status="succeeded", created_at=_now()
                        )
                    )
                    await session.commit()
                else:
                    await session.rollback()
                log.info("evidence_extraction.succeeded_reused", document_id=str(document_id), chunk_count=existing_chunk_count)
                return existing_chunk_count
            await session.rollback()
            raise ExtractionError(
                "CHUNKS_ALREADY_EXIST",
                f"Document {document_id} có chunk không gắn với extraction thành công trước đó",
            )
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
    """Returns `[]` for an archived/deleted document — its chunks still
    exist (immutable historical data), but this is the RETRIEVAL-eligible
    view, not the raw table. A document-management UI that needs to inspect
    an archived document's own chunks goes through
    `src/services/governance.py::latest_lifecycle_status` plus a direct read,
    never this function."""
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_evidence_document_chunks)
                .where(
                    ranking_evidence_document_chunks.c.document_id == document_id,
                    document_is_ready(ranking_evidence_document_chunks.c.document_id),
                )
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
                .where(
                    ranking_evidence_document_chunks.c.document_id.in_(document_ids),
                    document_is_ready(ranking_evidence_document_chunks.c.document_id),
                )
                .order_by(distance)
                .limit(top_k)
            )
        ).mappings().all()
        await session.rollback()
    return [dict(row) for row in rows]
