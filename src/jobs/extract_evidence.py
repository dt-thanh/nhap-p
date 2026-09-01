"""RQ job: parse → chunk → embed → upsert for one evidence document (§21.6).

Chạy ngoài request cycle vì cùng lý do với `parse_upload`/`recompute_domain`:
gọi OpenAI + đọc/parse PDF tốn thời gian, giữ trong request sẽ chiếm event loop
của uvicorn.

**Mọi lỗi sau khi attempt được claim đều trở thành trạng thái terminal.** PDF
hỏng và mime không hỗ trợ được ghi là `'failed'`/`'not_supported'`; lỗi
embedding/persistence cũng được ghi là `'failed'` trong transaction độc lập
trước khi (nếu cần) báo lỗi cho RQ.

**Idempotent.** Job kiểm `latest_extraction_status` trước khi làm gì — một
document đã `'succeeded'` không được xử lý lại. Nếu hai worker cùng chạy job
này cho một document (race), `uq_redc_document_chunk` (0035) chặn ghi trùng ở
tầng DB — xem `evidence_extraction.insert_chunks_and_mark_succeeded`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from rq import get_current_job

from src.config import get_settings
from src.logging_config import get_logger, job_id_var
from src.services import evidence_extraction

log = get_logger("src.jobs.extract_evidence")

# Giữ tên này ở đây cho các module khác/log tham chiếu — giá trị thật sống ở
# `evidence_extraction.EMBEDDING_MODEL` (nơi DUY NHẤT gọi OpenAI embeddings).
EMBEDDING_MODEL = evidence_extraction.EMBEDDING_MODEL
CHUNK_SIZE_TOKENS = 700  # trong dải 500-800 §13 quy định
CHUNK_OVERLAP_TOKENS = 100
_CHARS_PER_TOKEN = 4  # heuristic thô, không kéo thêm phụ thuộc tokenizer
_TERMINAL_FAILURE_RETRIES = 2

SUPPORTED_MIME_TYPES = ("application/pdf", "text/plain", "text/markdown")


def _load_bytes(object_storage_key: str) -> bytes:
    """Đọc từ `settings.upload_dir` — quy ước lưu trữ đã có
    (`src/services/file_upload.py`). Chưa có route multipart upload cho
    evidence documents (§21.1's "known gap"); caller phải đã đặt file đúng
    chỗ trước khi gọi `POST /governance/evidence/{id}/extract`."""
    settings = get_settings()
    return (Path(settings.upload_dir) / object_storage_key).read_bytes()


def _extract_text_pages(
    mime_type: str, data: bytes
) -> list[tuple[int | None, str, evidence_extraction.SanitizedTextResult]]:
    """PDF → list[(số trang từ 1, chữ đã sanitize, chỉ số sanitize)];
    text/markdown → một "trang" None.

    Sanitize NGAY sau khi parser trả chữ thô — trước khi văn bản được dùng để
    chia chunk, đếm token, hay tạo embedding — để mọi bước sau đó (chunk
    boundary, token_count, embedding, và cột `content` sẽ được ghi) đều làm
    việc trên đúng một bản văn bản đã sạch. pypdf có thể trả U+0000 do
    ToUnicode/CMap không đầy đủ; PostgreSQL từ chối NUL vô điều kiện
    (SQLSTATE 22021) bất kể encoding — xem `evidence_extraction.sanitize_text_for_postgres`.
    """
    if mime_type == "application/pdf":
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        raw_pages = [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
    else:
        # text/plain, text/markdown
        raw_pages = [(None, data.decode("utf-8", errors="replace"))]
    sanitized = [(page_number, evidence_extraction.sanitize_text_for_postgres(text)) for page_number, text in raw_pages]
    return [(page_number, result.text, result) for page_number, result in sanitized]


def _split_into_chunk_rows(pages: list[tuple[int | None, str]]) -> list[dict[str, Any]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE_TOKENS * _CHARS_PER_TOKEN,
        chunk_overlap=CHUNK_OVERLAP_TOKENS * _CHARS_PER_TOKEN,
    )
    rows: list[dict[str, Any]] = []
    chunk_index = 0
    for page_number, page_text in pages:
        for piece in splitter.split_text(page_text):
            piece = piece.strip()
            if not piece:
                continue
            rows.append({"chunk_index": chunk_index, "page_number": page_number, "content": piece})
            chunk_index += 1
    return rows


async def _run(document_id: uuid.UUID) -> dict[str, Any]:
    current_attempt = await evidence_extraction.latest_extraction_attempt(document_id)
    if current_attempt and current_attempt["status"] == "succeeded":
        return {"status": "succeeded", "chunk_count": None, "skipped": True}

    claimed_attempt_id = (
        uuid.UUID(str(current_attempt["id"]))
        if current_attempt and current_attempt["status"] == "pending"
        else None
    )
    try:
        document = await evidence_extraction.get_document(document_id)
    except Exception as exc:
        if claimed_attempt_id is not None:
            return await _persist_terminal_failure(
                document_id, claimed_attempt_id, "DATABASE_TRANSACTION_FAILED", f"{type(exc).__name__} during document lookup"
            )
        raise
    if document is None:
        raise evidence_extraction.ExtractionError(
            "DOCUMENT_NOT_FOUND", f"Không có ranking_evidence_documents {document_id}"
        )

    if current_attempt is None or current_attempt["status"] != "pending":
        await evidence_extraction.request_extraction(document_id)
        current_attempt = await evidence_extraction.latest_extraction_attempt(document_id)
    if not current_attempt or current_attempt["status"] != "pending":
        # A competing worker may have completed the attempt while this job
        # was starting. Never regress its terminal state.
        status = current_attempt["status"] if current_attempt else "not_requested"
        return {"status": status, "chunk_count": None, "skipped": True}
    attempt_id = uuid.UUID(str(current_attempt["id"]))

    if document["mime_type"] not in SUPPORTED_MIME_TYPES:
        recorded = await evidence_extraction.mark_extraction_attempt_failed(
            document_id, attempt_id=attempt_id, status="not_supported", error_code="UNSUPPORTED_DOCUMENT"
        )
        return {"status": "not_supported", "chunk_count": 0, "skipped": not recorded}

    try:
        data = _load_bytes(document["object_storage_key"])
        sanitized_pages = _extract_text_pages(document["mime_type"], data)
    except Exception as exc:
        return await _persist_terminal_failure(document_id, attempt_id, "PARSER_FAILED", f"{type(exc).__name__} while reading document")

    for page_number, _text, sanitize_result in sanitized_pages:
        if sanitize_result.nul_removed or sanitize_result.controls_removed:
            log.warning(
                "evidence_extraction.text_sanitized",
                document_id=str(document_id),
                attempt_id=str(attempt_id),
                stage="post_extract",
                parser=document["mime_type"],
                page_number=page_number,
                nul_removed=sanitize_result.nul_removed,
                controls_removed=sanitize_result.controls_removed,
                input_length=sanitize_result.input_length,
                output_length=sanitize_result.output_length,
            )
    pages = [(page_number, text) for page_number, text, _result in sanitized_pages]

    try:
        rows = _split_into_chunk_rows(pages)
    except Exception as exc:
        return await _persist_terminal_failure(document_id, attempt_id, "PARSER_FAILED", f"{type(exc).__name__} while preparing chunks")
    if not rows:
        return await _persist_terminal_failure(document_id, attempt_id, "PARSER_FAILED", "document contains no extractable text")

    try:
        vectors = evidence_extraction.embed_texts([row["content"] for row in rows])
    except Exception as exc:
        return await _persist_terminal_failure(document_id, attempt_id, "EMBEDDING_FAILED", f"{type(exc).__name__} while creating embeddings")
    try:
        for row, vector in zip(rows, vectors, strict=True):
            row["embedding"] = vector
            row["embedding_model"] = EMBEDDING_MODEL
            row["token_count"] = max(len(row["content"]) // _CHARS_PER_TOKEN, 1)
    except Exception as exc:
        return await _persist_terminal_failure(document_id, attempt_id, "EMBEDDING_FAILED", f"{type(exc).__name__} while preparing embeddings")

    try:
        chunk_count = await evidence_extraction.insert_chunks_and_mark_succeeded(
            document_id, rows, attempt_id=attempt_id
        )
    except evidence_extraction.ExtractionError as exc:
        if exc.code == "STALE_ATTEMPT":
            latest = await evidence_extraction.latest_extraction_attempt(document_id)
            return {"status": latest["status"] if latest else "failed", "chunk_count": None, "skipped": True}
        _log_persistence_failure(document_id, attempt_id, exc, error_code=exc.code)
        return await _persist_terminal_failure(document_id, attempt_id, "CHUNK_PERSISTENCE_FAILED", "chunk persistence failed")
    except Exception as exc:
        _log_persistence_failure(document_id, attempt_id, exc, error_code=None)
        return await _persist_terminal_failure(document_id, attempt_id, "CHUNK_PERSISTENCE_FAILED", "chunk persistence failed")
    return {"status": "succeeded", "chunk_count": chunk_count, "skipped": False}


def _log_persistence_failure(
    document_id: uuid.UUID, attempt_id: uuid.UUID, exc: Exception, *, error_code: str | None
) -> None:
    """Safe diagnostics only — error type/SQLSTATE/stage/ids, never the raw DB
    error, statement, bound parameters, or document content. This is what
    made the original NUL-byte incident (SQLSTATE 22021) require reading raw
    PostgreSQL server logs instead of application logs; this call closes that
    gap without ever logging document text."""
    orig = getattr(exc, "orig", None)
    if orig is None and exc.__cause__ is not None:
        orig = getattr(exc.__cause__, "orig", None)
    log.error(
        "evidence_extraction.chunk_persistence_failed",
        document_id=str(document_id),
        attempt_id=str(attempt_id),
        stage="insert_chunks_and_mark_succeeded",
        error_type=type(exc).__name__,
        error_code=error_code,
        sqlstate=getattr(orig, "sqlstate", None),
    )


async def _persist_terminal_failure(
    document_id: uuid.UUID, attempt_id: uuid.UUID, code: str, summary: str
) -> dict[str, Any]:
    """Persist a terminal event in a fresh transaction, with bounded retries."""
    last_error: Exception | None = None
    for retry in range(_TERMINAL_FAILURE_RETRIES):
        try:
            recorded = await evidence_extraction.mark_extraction_attempt_failed(
                document_id,
                attempt_id=attempt_id,
                status="failed",
                error_code=code,
                error_summary=summary[:500],
            )
            if recorded:
                return {"status": "failed", "chunk_count": 0, "skipped": False}
            latest = await evidence_extraction.latest_extraction_attempt(document_id)
            return {
                "status": latest["status"] if latest else "failed",
                "chunk_count": None,
                "skipped": True,
            }
        except Exception as exc:  # pragma: no cover - requires a live DB outage
            last_error = exc
            log.error(
                "evidence_extraction.failure_state_persist_failed",
                document_id=str(document_id),
                attempt_id=str(attempt_id),
                error_type=type(exc).__name__,
                retry=retry + 1,
            )
    assert last_error is not None
    raise last_error


def extract_and_embed_evidence_document(document_id: str) -> dict[str, Any]:
    """Entry point RQ. `document_id` là CHUỖI — cùng lý do với
    `run_domain_recompute`: RQ tuần tự hoá tham số qua ranh giới tiến trình."""
    current_job = get_current_job()
    token = job_id_var.set(current_job.id if current_job else None)
    started = time.perf_counter()
    log.info("evidence_extraction.job.started", document_id=document_id)
    try:
        result = asyncio.run(_run(uuid.UUID(document_id)))
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info("evidence_extraction.job.finished", document_id=document_id, duration_ms=duration_ms, **result)
        return {"document_id": document_id, "duration_ms": duration_ms, **result}
    except Exception as exc:
        # `_run` persists a terminal event before returning for all expected
        # parser/embedding/persistence failures. This wrapper only sees an
        # unexpected failure before an attempt could be identified, or a
        # failure to persist that terminal event; keep the RQ failure signal.
        log.error(
            "evidence_extraction.job.failed",
            document_id=document_id,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        raise
    finally:
        job_id_var.reset(token)
