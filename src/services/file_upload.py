"""FileUploadService — nhận file multipart, lưu xuống đĩa, tính checksum (SRS §5.2).

Tách khỏi `excel_parser` có chủ đích: module này chỉ lo LƯU TRỮ (giới hạn dung
lượng, đặt tên an toàn, checksum), không đọc nội dung bảng tính. Parser nhận vào
một `Path` đã nằm sẵn trên đĩa nên test được mà không cần dựng request HTTP.

File ghi vào `settings.upload_dir` — trong compose đây là volume `uploads` dùng
chung giữa api và worker, nên worker đọc lại được đúng đường dẫn API vừa ghi.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.config import get_settings
from src.logging_config import get_logger
from src.services.excel_parser import CALAMINE_SUFFIXES, CSV_SUFFIXES

log = get_logger("src.services.file_upload")

ALLOWED_SUFFIXES = CALAMINE_SUFFIXES | CSV_SUFFIXES

# Đọc theo khối 1 MB: không nạp cả file vào RAM và vẫn chặn được file quá cỡ
# NGAY TRONG lúc ghi, thay vì chờ nhận xong 20 MB rồi mới từ chối.
CHUNK_SIZE = 1024 * 1024


class UploadRejectedError(Exception):
    """File bị từ chối trước khi parse (sai đuôi, quá dung lượng, rỗng)."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class AsyncFileReader(Protocol):
    """Phần giao diện của `fastapi.UploadFile` mà service này thực sự dùng."""

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """File đã nằm trên đĩa, sẵn sàng đưa cho worker parse."""

    path: Path
    checksum: str
    original_filename: str
    size: int


def validate_suffix(filename: str) -> str:
    """Kiểm tra đuôi file trước khi đọc body — từ chối sớm thì rẻ hơn nhiều."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise UploadRejectedError(
            "UNSUPPORTED_FORMAT",
            f"Định dạng '{suffix or filename}' không được hỗ trợ. Chấp nhận: {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )
    return suffix


class FileUploadService:
    """Lưu file upload và tính checksum SHA-256 (SRS §2.4)."""

    def __init__(self, *, upload_dir: Path | None = None, max_size: int | None = None) -> None:
        settings = get_settings()
        self.upload_dir = Path(upload_dir or settings.upload_dir)
        self.max_size = max_size or settings.upload_max_size

    async def save(self, upload: AsyncFileReader, filename: str) -> StoredUpload:
        """Ghi file theo từng khối, vừa ghi vừa tính SHA-256 và đếm dung lượng.

        KHÔNG dùng tên file của client làm tên trên đĩa: tên đó do người dùng
        kiểm soát và có thể chứa '../' hay ký tự lạ. Sinh tên bằng uuid4, chỉ giữ
        lại phần đuôi đã kiểm tra; tên gốc trả về cho tầng trên lưu riêng.
        """
        suffix = validate_suffix(filename)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        target = self.upload_dir / f"{uuid.uuid4().hex}{suffix}"

        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("wb") as handle:
                while chunk := await upload.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > self.max_size:
                        raise UploadRejectedError(
                            "FILE_TOO_LARGE",
                            f"File vượt giới hạn {self.max_size // (1024 * 1024)} MB",
                        )
                    digest.update(chunk)
                    handle.write(chunk)

            if size == 0:
                raise UploadRejectedError("EMPTY_FILE", "File rỗng")
        except BaseException:
            # Dọn file dở dang cho mọi lối thoát bất thường (kể cả client ngắt
            # kết nối giữa chừng), nếu không volume uploads sẽ đầy rác dần.
            target.unlink(missing_ok=True)
            raise

        log.info(
            "upload.stored",
            # KHÔNG log tên file gốc: người dùng hay đặt tên kèm thông tin dự án.
            suffix=suffix,
            size_bytes=size,
            checksum=digest.hexdigest(),
        )
        return StoredUpload(
            path=target,
            checksum=digest.hexdigest(),
            original_filename=filename,
            size=size,
        )
