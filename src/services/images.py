"""ImageService — ảnh bìa của dự án và phân khu, lưu trên Cloudinary.

MỘT service cho CẢ HAI thực thể: `projects` và `areas` có cùng cặp cột
(`cover_image_url`, `cover_image_public_id`) và cùng quy tắc nghiệp vụ, nên tách
làm hai bản sao chỉ tạo ra hai chỗ để lệch nhau. Thực thể được chọn qua
`ImageOwner`, phần còn lại dùng chung.

Mỗi bản ghi có TỐI ĐA một ảnh. `POST` tạo mới và từ chối nếu đã có
(`IMAGE_ALREADY_EXISTS`); `PUT` thay thế và tự dọn ảnh cũ.

Thứ tự thao tác được chọn để KHÔNG bao giờ để lại file mồ côi:

1. Đọc file vào bộ nhớ, kiểm tra đuôi + dung lượng + rỗng.
2. Upload lên Cloudinary.
3. Ghi DB. Nếu bước này hỏng → xoá ngay ảnh vừa upload (bù trừ) rồi mới ném lỗi.
4. Chỉ khi DB đã commit mới xoá ảnh CŨ (với thao tác thay thế).

Làm ngược lại — ghi DB trước rồi upload — sẽ để bản ghi trỏ vào ảnh không tồn
tại khi upload hỏng. Xoá ảnh cũ trước khi commit thì mất ảnh cũ mà bản ghi vẫn
trỏ vào nó nếu DB rollback.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import areas, projects

log = get_logger("src.services.images")

# Định dạng ảnh chấp nhận. Kiểm theo đuôi file giống `file_upload.validate_suffix`
# để hai đường upload hành xử nhất quán.
ALLOWED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

CHUNK_SIZE = 1024 * 1024


class ImageRejectedError(Exception):
    """Yêu cầu bị từ chối vì lý do nghiệp vụ; router map `error_code` sang HTTP."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class AsyncFileReader(Protocol):
    """Phần giao diện của `fastapi.UploadFile` mà service này dùng."""

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ImageOwner:
    """Mô tả một thực thể mang ảnh. Thêm bảng mới chỉ cần thêm một hằng ở đây."""

    kind: str  # "project" | "area"
    table: sa.Table
    not_found_code: str


PROJECT_OWNER = ImageOwner(kind="project", table=projects, not_found_code="PROJECT_NOT_FOUND")
AREA_OWNER = ImageOwner(kind="area", table=areas, not_found_code="AREA_NOT_FOUND")


@dataclass(slots=True)
class ImageRecord:
    owner_id: str
    url: str
    public_id: str


def validate_image_suffix(filename: str) -> str:
    """Chặn định dạng lạ TRƯỚC khi đọc body — rẻ hơn nhiều so với đọc rồi mới bỏ."""
    from pathlib import Path

    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ImageRejectedError(
            "UNSUPPORTED_IMAGE_FORMAT",
            f"Định dạng '{suffix or filename}' không được hỗ trợ. "
            f"Chấp nhận: {', '.join(sorted(ALLOWED_IMAGE_SUFFIXES))}",
        )
    return suffix


class CloudinaryClient:
    """Bọc SDK Cloudinary lại để test thay thế được mà không cần gọi mạng thật."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.cloudinary_configured:
            raise ImageRejectedError(
                "STORAGE_NOT_CONFIGURED",
                "Chưa cấu hình Cloudinary (CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET)",
            )
        import cloudinary

        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key.get_secret_value(),
            api_secret=settings.cloudinary_api_secret.get_secret_value(),
            secure=True,
        )
        self._folder = settings.cloudinary_folder

    def upload(self, data: bytes, *, public_id: str) -> tuple[str, str]:
        """Tải ảnh lên, trả `(secure_url, public_id)`."""
        import cloudinary.uploader

        result = cloudinary.uploader.upload(
            data,
            folder=self._folder,
            public_id=public_id,
            resource_type="image",
            overwrite=True,
        )
        return result["secure_url"], result["public_id"]

    def destroy(self, public_id: str) -> None:
        """Xoá ảnh. Ảnh đã biến mất từ trước cũng coi là thành công (idempotent)."""
        import cloudinary.uploader

        result = cloudinary.uploader.destroy(public_id, resource_type="image")
        outcome = result.get("result")
        if outcome not in ("ok", "not found"):
            raise ImageRejectedError("STORAGE_DELETE_FAILED", f"Cloudinary không xoá được ảnh: {outcome}")


class ImageService:
    """CRUD ảnh bìa cho dự án và phân khu."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        client: CloudinaryClient | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()
        # Tạo client lười: chưa cấu hình Cloudinary vẫn khởi tạo được service,
        # chỉ thao tác nào thực sự cần mới báo lỗi.
        self._client = client

    def _cloudinary(self) -> CloudinaryClient:
        if self._client is None:
            self._client = CloudinaryClient()
        return self._client

    async def get(self, owner: ImageOwner, owner_id: uuid.UUID | str) -> ImageRecord:
        """Đọc ảnh hiện tại. Không có bản ghi → 404; có bản ghi mà chưa có ảnh → 404."""
        owner_uuid = uuid.UUID(str(owner_id))
        async with self._session_factory() as session:
            row = await self._fetch(session, owner, owner_uuid)

        if not row.cover_image_public_id:
            raise ImageRejectedError("IMAGE_NOT_FOUND", f"{owner.kind} này chưa có ảnh bìa")
        return ImageRecord(
            owner_id=str(owner_uuid),
            url=row.cover_image_url,
            public_id=row.cover_image_public_id,
        )

    async def create(
        self, owner: ImageOwner, owner_id: uuid.UUID | str, upload: AsyncFileReader, filename: str
    ) -> ImageRecord:
        """Tạo ảnh mới. Đã có ảnh → từ chối, buộc dùng PUT cho rõ ý định."""
        return await self._store(owner, owner_id, upload, filename, replace=False)

    async def replace(
        self, owner: ImageOwner, owner_id: uuid.UUID | str, upload: AsyncFileReader, filename: str
    ) -> ImageRecord:
        """Thay ảnh. Chưa có ảnh nào cũng chấp nhận — PUT mang nghĩa "đặt thành"."""
        return await self._store(owner, owner_id, upload, filename, replace=True)

    async def delete(self, owner: ImageOwner, owner_id: uuid.UUID | str) -> None:
        """Xoá ảnh khỏi Cloudinary rồi mới xoá tham chiếu trong DB.

        Cloudinary hỏng thì DỪNG, không đụng DB: xoá tham chiếu trước sẽ mất dấu
        public_id và ảnh nằm lại trên Cloudinary vĩnh viễn, không ai dọn được.
        """
        owner_uuid = uuid.UUID(str(owner_id))
        async with self._session_factory() as session:
            row = await self._fetch(session, owner, owner_uuid)
            if not row.cover_image_public_id:
                raise ImageRejectedError("IMAGE_NOT_FOUND", f"{owner.kind} này chưa có ảnh bìa")
            public_id = row.cover_image_public_id

        self._cloudinary().destroy(public_id)

        async with self._session_factory() as session:
            async with session.begin():
                await self._clear(session, owner, owner_uuid)

        log.info("image.deleted", kind=owner.kind, owner_id=str(owner_uuid))

    # --- phần dùng chung -----------------------------------------------------

    async def _store(
        self,
        owner: ImageOwner,
        owner_id: uuid.UUID | str,
        upload: AsyncFileReader,
        filename: str,
        *,
        replace: bool,
    ) -> ImageRecord:
        owner_uuid = uuid.UUID(str(owner_id))
        validate_image_suffix(filename)

        async with self._session_factory() as session:
            row = await self._fetch(session, owner, owner_uuid)
        old_public_id = row.cover_image_public_id

        if old_public_id and not replace:
            raise ImageRejectedError(
                "IMAGE_ALREADY_EXISTS",
                f"{owner.kind} này đã có ảnh bìa. Dùng PUT để thay thế.",
            )

        data = await self._read_capped(upload)
        client = self._cloudinary()

        # public_id cố định theo thực thể: ảnh mới ghi đè đúng chỗ, không sinh rác.
        public_id = f"{owner.kind}-{owner_uuid}"
        try:
            url, stored_public_id = client.upload(data, public_id=public_id)
        except ImageRejectedError:
            raise
        except Exception as exc:
            log.error("image.upload_failed", kind=owner.kind, error_type=type(exc).__name__, exc_info=exc)
            raise ImageRejectedError("STORAGE_UPLOAD_FAILED", "Không tải được ảnh lên Cloudinary") from exc

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        sa.update(owner.table)
                        .where(owner.table.c.id == owner_uuid)
                        .values(cover_image_url=url, cover_image_public_id=stored_public_id)
                    )
        except Exception:
            # DB hỏng sau khi đã upload: xoá ảnh vừa đưa lên, nếu không nó thành
            # file mồ côi mà không bản ghi nào trỏ tới.
            log.error("image.db_failed_rolling_back_storage", kind=owner.kind, public_id=stored_public_id)
            with_suppressed_cleanup(client, stored_public_id)
            raise

        # Chỉ dọn ảnh cũ khi DB đã commit. public_id trùng nhau thì upload đã ghi
        # đè, không cần xoá thêm.
        if old_public_id and old_public_id != stored_public_id:
            with_suppressed_cleanup(client, old_public_id)

        log.info("image.stored", kind=owner.kind, owner_id=str(owner_uuid), replaced=bool(old_public_id))
        return ImageRecord(owner_id=str(owner_uuid), url=url, public_id=stored_public_id)

    async def _read_capped(self, upload: AsyncFileReader) -> bytes:
        """Đọc theo khối và chặn quá cỡ NGAY trong lúc đọc, không nạp hết rồi mới đo."""
        max_size = get_settings().image_max_size
        chunks: list[bytes] = []
        size = 0
        while chunk := await upload.read(CHUNK_SIZE):
            size += len(chunk)
            if size > max_size:
                raise ImageRejectedError("IMAGE_TOO_LARGE", f"Ảnh vượt giới hạn {max_size // (1024 * 1024)} MB")
            chunks.append(chunk)

        if size == 0:
            raise ImageRejectedError("EMPTY_IMAGE", "File ảnh rỗng")
        return b"".join(chunks)

    async def _fetch(self, session: AsyncSession, owner: ImageOwner, owner_uuid: uuid.UUID) -> Any:
        row = (
            await session.execute(
                sa.select(
                    owner.table.c.id,
                    owner.table.c.cover_image_url,
                    owner.table.c.cover_image_public_id,
                ).where(owner.table.c.id == owner_uuid)
            )
        ).one_or_none()
        if row is None:
            raise ImageRejectedError(owner.not_found_code, f"Không tìm thấy {owner.kind} '{owner_uuid}'")
        return row

    async def _clear(self, session: AsyncSession, owner: ImageOwner, owner_uuid: uuid.UUID) -> None:
        await session.execute(
            sa.update(owner.table)
            .where(owner.table.c.id == owner_uuid)
            .values(cover_image_url=None, cover_image_public_id=None)
        )


def with_suppressed_cleanup(client: CloudinaryClient, public_id: str) -> None:
    """Dọn ảnh trên Cloudinary, nuốt lỗi.

    Dùng ở đường BÙ TRỪ (đã có lỗi khác đang được xử lý) và ở bước dọn ảnh cũ sau
    khi DB đã commit. Ném lỗi ở đây chỉ che mất lỗi gốc, hoặc biến một thao tác
    đã thành công thành thất bại. Ảnh sót lại được ghi log để dọn tay.
    """
    try:
        client.destroy(public_id)
    except Exception as exc:
        log.warning("image.cleanup_failed", public_id=public_id, error_type=type(exc).__name__)
