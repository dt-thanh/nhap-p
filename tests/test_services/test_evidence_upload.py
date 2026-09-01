"""`src/services/evidence_upload.py` — real multipart-bytes storage for
expert evidence documents. Pure filesystem, no DB — mirrors how
`src/services/file_upload.py` itself has no dedicated unit test file (only
exercised at the API layer), except this one IS unit-tested directly since
the storage-only/DB-write separation is exactly the boundary worth proving
in isolation.
"""

from __future__ import annotations

import hashlib

import pytest

from src.services.evidence_upload import (
    EvidenceUploadRejectedError,
    EvidenceUploadService,
    validate_suffix,
)


class _Reader:
    """Minimal `AsyncFileReader` — yields fixed-size chunks then empty bytes."""

    def __init__(self, data: bytes, chunk_size: int = 1024 * 1024) -> None:
        self._data = data
        self._chunk_size = chunk_size
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        chunk = self._data[self._offset : self._offset + self._chunk_size]
        self._offset += len(chunk)
        return chunk


PDF_BYTES = b"%PDF-1.4\n%mock pdf content for a real test\n%%EOF"


def test_validate_suffix_accepts_pdf_txt_md():
    assert validate_suffix("report.pdf") == ".pdf"
    assert validate_suffix("notes.txt") == ".txt"
    assert validate_suffix("summary.md") == ".md"


def test_validate_suffix_rejects_unsupported_extension():
    with pytest.raises(EvidenceUploadRejectedError) as exc:
        validate_suffix("spreadsheet.xlsx")
    assert exc.value.error_code == "UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_save_writes_pdf_and_returns_correct_metadata(tmp_path):
    service = EvidenceUploadService(upload_dir=tmp_path, max_size=10 * 1024 * 1024)
    stored = await service.save(_Reader(PDF_BYTES), "Q2 2026 Market Analysis.pdf")

    assert stored.mime_type == "application/pdf"
    assert stored.file_size_bytes == len(PDF_BYTES)
    assert stored.sha256_checksum == hashlib.sha256(PDF_BYTES).hexdigest()
    assert stored.original_filename == "Q2 2026 Market Analysis.pdf"
    on_disk = tmp_path / "governance" / "evidence" / stored.object_storage_key.rsplit("/", 1)[-1]
    assert on_disk.read_bytes() == PDF_BYTES


@pytest.mark.asyncio
async def test_save_never_uses_the_clients_filename_on_disk(tmp_path):
    """Same discipline as `file_upload.py` — the on-disk name is always a
    fresh uuid4, never anything derived from client input (path-traversal
    safety, and object_storage_key must stay globally unique regardless of
    what the client called the file)."""
    service = EvidenceUploadService(upload_dir=tmp_path, max_size=10 * 1024 * 1024)
    stored = await service.save(_Reader(PDF_BYTES), "../../etc/passwd.pdf")
    assert ".." not in stored.object_storage_key
    assert "passwd" not in stored.object_storage_key


@pytest.mark.asyncio
async def test_empty_file_is_rejected_and_leaves_no_partial_file(tmp_path):
    service = EvidenceUploadService(upload_dir=tmp_path, max_size=10 * 1024 * 1024)
    with pytest.raises(EvidenceUploadRejectedError) as exc:
        await service.save(_Reader(b""), "empty.pdf")
    assert exc.value.error_code == "EMPTY_FILE"
    assert list((tmp_path / "governance" / "evidence").glob("*")) == []


@pytest.mark.asyncio
async def test_oversized_file_is_rejected_mid_write_and_cleaned_up(tmp_path):
    service = EvidenceUploadService(upload_dir=tmp_path, max_size=16, )
    with pytest.raises(EvidenceUploadRejectedError) as exc:
        await service.save(_Reader(PDF_BYTES, chunk_size=8), "big.pdf")
    assert exc.value.error_code == "FILE_TOO_LARGE"
    assert list((tmp_path / "governance" / "evidence").glob("*")) == []


@pytest.mark.asyncio
async def test_pdf_signature_mismatch_is_rejected(tmp_path):
    """A `.pdf`-named file whose actual bytes are not a PDF (renamed/corrupt
    file) must be rejected on content, not trusted on extension alone —
    mission rule 'validate MIME type and file signature where feasible'."""
    service = EvidenceUploadService(upload_dir=tmp_path, max_size=10 * 1024 * 1024)
    with pytest.raises(EvidenceUploadRejectedError) as exc:
        await service.save(_Reader(b"this is not a pdf at all"), "fake.pdf")
    assert exc.value.error_code == "FILE_SIGNATURE_MISMATCH"
    assert list((tmp_path / "governance" / "evidence").glob("*")) == []


@pytest.mark.asyncio
async def test_txt_and_md_are_not_signature_checked(tmp_path):
    """Only PDF has a stable binary magic number; plain text has none to
    check, and rejecting all text content would be pointless."""
    service = EvidenceUploadService(upload_dir=tmp_path, max_size=10 * 1024 * 1024)
    stored = await service.save(_Reader(b"Ghi chu chuyen gia bang van ban thuan."), "notes.txt")
    assert stored.mime_type == "text/plain"
