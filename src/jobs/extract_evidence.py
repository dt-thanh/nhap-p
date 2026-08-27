"""RQ job: parse → chunk → embed → upsert for one evidence document (§21.6).

Chạy ngoài request cycle vì cùng lý do với `parse_upload`/`recompute_domain`:
gọi OpenAI + đọc/parse PDF tốn thời gian, giữ trong request sẽ chiếm event loop
của uvicorn.

**Thất bại có chủ đích KHÔNG làm job fail.** PDF hỏng, mime không hỗ trợ, hay
không trích được chữ nào đều là kết quả hợp lệ — ghi một dòng
`ranking_evidence_extraction_attempts` với status `'failed'`/`'not_supported'`
rồi trả về bình thường (R18, §21.11: "wraps extraction in try/except → failed,
matching the existing upload_errors pattern"). CHỈ lỗi hạ tầng thật (DB, Redis)
mới ném lại cho RQ đánh dấu job failed và retry.

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

SUPPORTED_MIME_TYPES = ("application/pdf", "text/plain", "text/markdown")


def _load_bytes(object_storage_key: str) -> bytes:
    """Đọc từ `settings.upload_dir` — quy ước lưu trữ đã có
    (`src/services/file_upload.py`). Chưa có route multipart upload cho
    evidence documents (§21.1's "known gap"); caller phải đã đặt file đúng
    chỗ trước khi gọi `POST /governance/evidence/{id}/extract`."""
    settings = get_settings()
    return (Path(settings.upload_dir) / object_storage_key).read_bytes()


def _extract_text_pages(mime_type: str, data: bytes) -> list[tuple[int | None, str]]:
    """PDF → list[(số trang từ 1, chữ)]; text/markdown → một "trang" None."""
    if mime_type == "application/pdf":
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
    # text/plain, text/markdown
    return [(None, data.decode("utf-8", errors="replace"))]


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
    current = await evidence_extraction.latest_extraction_status(document_id)
    if current == "succeeded":
        return {"status": "succeeded", "chunk_count": None, "skipped": True}

    document = await evidence_extraction.get_document(document_id)
    if document is None:
        raise evidence_extraction.ExtractionError(
            "DOCUMENT_NOT_FOUND", f"Không có ranking_evidence_documents {document_id}"
        )

    if document["mime_type"] not in SUPPORTED_MIME_TYPES:
        await evidence_extraction.mark_extraction_attempt_failed(document_id, status="not_supported")
        return {"status": "not_supported", "chunk_count": 0, "skipped": False}

    try:
        data = _load_bytes(document["object_storage_key"])
        pages = _extract_text_pages(document["mime_type"], data)
    except Exception as exc:
        await evidence_extraction.mark_extraction_attempt_failed(
            document_id, status="failed", error_summary=f"{type(exc).__name__}: {exc}"[:500]
        )
        return {"status": "failed", "chunk_count": 0, "skipped": False}

    rows = _split_into_chunk_rows(pages)
    if not rows:
        await evidence_extraction.mark_extraction_attempt_failed(
            document_id, status="failed", error_summary="no extractable text in document"
        )
        return {"status": "failed", "chunk_count": 0, "skipped": False}

    vectors = evidence_extraction.embed_texts([row["content"] for row in rows])
    for row, vector in zip(rows, vectors, strict=True):
        row["embedding"] = vector
        row["embedding_model"] = EMBEDDING_MODEL
        row["token_count"] = max(len(row["content"]) // _CHARS_PER_TOKEN, 1)

    chunk_count = await evidence_extraction.insert_chunks_and_mark_succeeded(document_id, rows)
    return {"status": "succeeded", "chunk_count": chunk_count, "skipped": False}


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
        # Chỉ lỗi hạ tầng thật tới đây — thất bại trích xuất có chủ đích đã
        # được `_run` bắt và ghi lại rồi return bình thường ở trên.
        log.error(
            "evidence_extraction.job.failed",
            document_id=document_id,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        raise
    finally:
        job_id_var.reset(token)
