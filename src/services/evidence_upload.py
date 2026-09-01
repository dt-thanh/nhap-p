"""EvidenceUploadService — real multipart bytes storage for expert evidence
documents (closes the known gap recorded in `ranking_consultant.md` §21.1 and
`pipeline_status.md`'s 2026-08-25/26 entries: "no multipart upload route —
`POST /governance/evidence` registers metadata for a file already placed in
storage by a caller").

Mirrors `src/services/file_upload.py`'s exact separation of concerns: this
module ONLY writes bytes to disk and computes a checksum — it never touches
`ranking_evidence_documents` or any other table. `src/services/governance.py`
remains the sole writer of that table (its existing `register_evidence_document`
is called, unmodified, once bytes are safely on disk), preserving the
single-writer discipline `tests/test_ranking_boundary.py` enforces.

Storage convention: same shared `settings.upload_dir` volume `file_upload.py`
already uses (mounted identically into `api`/`worker` in `docker-compose.yml`),
under an `object_storage_key` of the form `governance/evidence/<uuid>.<ext>` —
`src/jobs/extract_evidence.py::_load_bytes()` already reads
`Path(settings.upload_dir) / object_storage_key` unchanged, so no change is
needed there for this key shape to work.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.config import get_settings
from src.logging_config import get_logger
from src.services.governance import EVIDENCE_MIME_TYPES

log = get_logger("src.services.evidence_upload")

# Đuôi file hợp lệ, khớp EVIDENCE_MIME_TYPES ở services/governance.py.
ALLOWED_SUFFIXES = frozenset({".pdf", ".txt", ".md"})
SUFFIX_TO_MIME = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown"}

# Chữ ký file (magic bytes) — kiểm tra THẬT nội dung, không chỉ tin đuôi/Content-Type
# client tự khai. Chỉ PDF có chữ ký nhị phân ổn định; text/markdown là văn bản
# thuần nên không có chữ ký để kiểm.
PDF_MAGIC = b"%PDF-"

CHUNK_SIZE = 1024 * 1024
STORAGE_SUBDIR = "governance/evidence"


class EvidenceUploadRejectedError(Exception):
    """File bị từ chối trước khi ghi hàng metadata (sai đuôi, quá dung lượng,
    rỗng, hoặc chữ ký file không khớp đuôi khai báo)."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class AsyncFileReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class StoredEvidenceUpload:
    object_storage_key: str
    mime_type: str
    sha256_checksum: str
    original_filename: str
    file_size_bytes: int


def validate_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise EvidenceUploadRejectedError(
            "UNSUPPORTED_FORMAT",
            f"Định dạng '{suffix or filename}' không được hỗ trợ. Chấp nhận: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )
    return suffix


class EvidenceUploadService:
    """Lưu file evidence (PDF/text/markdown) và tính checksum SHA-256.

    Không ghi bất kỳ bảng nào — chỉ trả `StoredEvidenceUpload` cho tầng gọi
    (route) truyền vào `governance.register_evidence_document()` không đổi.
    """

    def __init__(self, *, upload_dir: Path | None = None, max_size: int | None = None) -> None:
        settings = get_settings()
        self.upload_dir = Path(upload_dir or settings.upload_dir) / STORAGE_SUBDIR
        self.max_size = max_size or settings.upload_max_size

    async def save(self, upload: AsyncFileReader, filename: str) -> StoredEvidenceUpload:
        suffix = validate_suffix(filename)
        expected_mime = SUFFIX_TO_MIME[suffix]
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        object_storage_key = f"{STORAGE_SUBDIR}/{uuid.uuid4().hex}{suffix}"
        target = self.upload_dir / Path(object_storage_key).name

        digest = hashlib.sha256()
        size = 0
        first_chunk = b""
        try:
            with target.open("wb") as handle:
                while chunk := await upload.read(CHUNK_SIZE):
                    if not first_chunk:
                        first_chunk = chunk
                    size += len(chunk)
                    if size > self.max_size:
                        raise EvidenceUploadRejectedError(
                            "FILE_TOO_LARGE",
                            f"File vượt giới hạn {self.max_size // (1024 * 1024)} MB",
                        )
                    digest.update(chunk)
                    handle.write(chunk)

            if size == 0:
                raise EvidenceUploadRejectedError("EMPTY_FILE", "File rỗng")
            if suffix == ".pdf" and not first_chunk.startswith(PDF_MAGIC):
                # Chữ ký file thật không khớp đuôi ".pdf" khai báo — có thể là
                # file đổi tên/hỏng. Không tin đuôi/Content-Type một mình.
                raise EvidenceUploadRejectedError(
                    "FILE_SIGNATURE_MISMATCH", "Nội dung file không phải PDF hợp lệ (thiếu chữ ký %PDF-)"
                )
        except BaseException:
            target.unlink(missing_ok=True)
            raise

        log.info(
            "evidence_upload.stored",
            suffix=suffix,
            size_bytes=size,
            checksum=digest.hexdigest(),
        )
        return StoredEvidenceUpload(
            object_storage_key=object_storage_key,
            mime_type=expected_mime,
            sha256_checksum=digest.hexdigest(),
            original_filename=filename,
            file_size_bytes=size,
        )


assert set(SUFFIX_TO_MIME.values()) == set(EVIDENCE_MIME_TYPES), "suffix<->mime map must stay in sync with governance.py"
