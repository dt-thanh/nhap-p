"""Xác thực máy-với-máy cho luồng đồng bộ: cấp, xác minh, xoay, thu hồi khoá API.

Đây là tầng canh cửa của mọi thứ Phase 3 dựng lên. Bốn quyết định đáng giải thích:

**1. Chỉ lưu hash, khoá thô trả về đúng một lần.** Sau khi `issue()` trả về, chuỗi
khoá không còn tồn tại ở bất cứ đâu trong hệ thống. Mất khoá thì cấp lại, không
khôi phục — vì "khôi phục được" đồng nghĩa với "đọc được từ DB", và lúc đó một
bản dump bị lộ là toàn bộ khoá bị lộ.

**2. SHA-256 chứ không bcrypt/argon2.** Khoá API là 32 byte ngẫu nhiên do máy
sinh, không phải mật khẩu người đặt. Hàm băm chậm tồn tại để chống dò từ điển
trên bí mật entropy thấp; ở đây không có entropy thấp để mà chống. Đổi lại,
xác thực nằm trên đường đi của MỌI request nên chi phí phải là hằng số nhỏ.

**3. Tra theo `key_prefix` rồi so hash bằng `compare_digest`.** Không có prefix
thì mỗi lần xác thực phải quét cả bảng. So sánh dùng hàm hằng thời gian: so bằng
`==` sẽ dừng ở byte đầu tiên khác nhau, và thời gian trả lời rò rỉ dần từng byte
của hash.

**4. Khoá buộc vào đúng một `source_instance_id`.** `authenticate()` nhận vào
instance mà lô TỰ KHAI trong phong bì, và từ chối nếu nó không khớp instance của
khoá. Đây là chỗ chặn ghi chéo instance: một khoá bị lộ chỉ ghi được vào đúng
phạm vi của nó, không mượn được danh nghĩa hệ nguồn khác.

Không bao giờ log khoá đầy đủ. Log chỉ mang `key_prefix` — đủ để người vận hành
biết khoá nào, không đủ để dùng lại.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.models.tables import sync_credentials

log = get_logger("src.services.sync_credentials")

# 32 byte ngẫu nhiên → 43 ký tự base64url. Dài hơn mức cần để dò cạn là bất khả thi.
KEY_ENTROPY_BYTES = 32

# Khớp ck_sync_credentials_key_prefix_length.
KEY_PREFIX_LENGTH = 8

# Tiền tố nhận dạng, để một khoá lỡ dán vào chỗ công khai còn nhận ra được là khoá.
KEY_NAMESPACE = "afsk_"


class CredentialError(Exception):
    """Xác thực thất bại. `error_code` đi thẳng vào phản hồi có cấu trúc."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    """Kết quả cấp khoá. `api_key` là lần DUY NHẤT chuỗi thô tồn tại."""

    credential_id: uuid.UUID
    api_key: str
    key_prefix: str
    source_system: str
    source_instance_id: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuthenticatedCaller:
    """Danh tính đã xác minh của bên gọi."""

    credential_id: uuid.UUID
    source_system: str
    source_instance_id: str
    key_prefix: str


def hash_key(raw_key: str) -> str:
    """SHA-256 hex của khoá thô. Hàm thuần, để test kiểm được mà không cần DB."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Sinh (khoá thô, prefix, hash).

    `token_urlsafe` lấy từ `secrets`, không phải `random`: `random` là bộ sinh giả
    ngẫu nhiên tất định, đoán được trạng thái từ vài giá trị đầu ra.
    """
    raw = KEY_NAMESPACE + secrets.token_urlsafe(KEY_ENTROPY_BYTES)
    return raw, raw[:KEY_PREFIX_LENGTH], hash_key(raw)


class SyncCredentialService:
    """Vòng đời khoá API. Mọi phương thức nhận session từ ngoài để gọi được trong
    cùng transaction với phần còn lại của một lô."""

    async def issue(
        self,
        session: AsyncSession,
        *,
        source_system: str,
        source_instance_id: str,
        label: str = "",
        expires_at: datetime | None = None,
    ) -> IssuedCredential:
        """Cấp một khoá mới. Trả khoá thô ĐÚNG MỘT LẦN."""
        if not source_system or not source_instance_id:
            raise CredentialError("INVALID_CREDENTIAL_SCOPE", "source_system và source_instance_id không được rỗng")

        raw, prefix, key_hash = generate_key()
        credential_id = uuid.uuid4()
        now = datetime.now(UTC)

        if expires_at is not None and expires_at <= now:
            raise CredentialError("INVALID_EXPIRY", "expires_at phải nằm sau thời điểm cấp")

        await session.execute(
            sa.insert(sync_credentials).values(
                id=credential_id,
                source_system=source_system,
                source_instance_id=source_instance_id,
                key_prefix=prefix,
                key_hash=key_hash,
                label=label,
                created_at=now,
                expires_at=expires_at,
            )
        )

        # Log prefix, KHÔNG log khoá.
        log.info(
            "sync.credential.issued",
            credential_id=str(credential_id),
            key_prefix=prefix,
            source_instance_id=source_instance_id,
        )

        return IssuedCredential(
            credential_id=credential_id,
            api_key=raw,
            key_prefix=prefix,
            source_system=source_system,
            source_instance_id=source_instance_id,
            expires_at=expires_at,
        )

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        api_key: str,
        claimed_instance_id: str | None = None,
    ) -> AuthenticatedCaller:
        """Xác minh khoá, và nếu lô có khai instance thì buộc phải khớp.

        `claimed_instance_id` là giá trị lô TỰ KHAI trong phong bì. Khoá hợp lệ mà
        khai instance khác → từ chối: đó chính là ghi chéo instance.
        """
        if not api_key:
            raise CredentialError("MISSING_API_KEY", "Thiếu khoá API")

        prefix = api_key[:KEY_PREFIX_LENGTH]
        incoming_hash = hash_key(api_key)

        rows = (
            (await session.execute(sa.select(sync_credentials).where(sync_credentials.c.key_prefix == prefix)))
            .mappings()
            .all()
        )

        matched = None
        for row in rows:
            # Hằng thời gian: `==` dừng ở byte khác đầu tiên và rò rỉ hash dần.
            if secrets.compare_digest(row["key_hash"], incoming_hash):
                matched = row
                break

        if matched is None:
            # KHÔNG phân biệt "không có prefix này" với "prefix đúng, hash sai":
            # hai thông báo khác nhau cho phép dò xem prefix nào tồn tại.
            log.warning("sync.credential.rejected", key_prefix=prefix, reason="no_match")
            raise CredentialError("INVALID_API_KEY", "Khoá API không hợp lệ")

        now = datetime.now(UTC)

        if matched["revoked_at"] is not None:
            log.warning("sync.credential.rejected", key_prefix=prefix, reason="revoked")
            raise CredentialError("REVOKED_API_KEY", "Khoá API đã bị thu hồi")

        if matched["expires_at"] is not None and matched["expires_at"] <= now:
            log.warning("sync.credential.rejected", key_prefix=prefix, reason="expired")
            raise CredentialError("EXPIRED_API_KEY", "Khoá API đã hết hạn")

        if claimed_instance_id is not None and claimed_instance_id != matched["source_instance_id"]:
            # Ranh giới cô lập. Thông báo KHÔNG nêu instance thật của khoá — nói ra
            # là giúp người gọi dò xem khoá thuộc về đâu.
            log.warning(
                "sync.credential.cross_instance_denied",
                key_prefix=prefix,
                claimed_instance_id=claimed_instance_id,
            )
            raise CredentialError(
                "INSTANCE_MISMATCH",
                "Khoá API không được phép ghi vào source_instance_id này",
            )

        await session.execute(
            sa.update(sync_credentials).where(sync_credentials.c.id == matched["id"]).values(last_used_at=now)
        )

        return AuthenticatedCaller(
            credential_id=matched["id"],
            source_system=matched["source_system"],
            source_instance_id=matched["source_instance_id"],
            key_prefix=prefix,
        )

    async def revoke(self, session: AsyncSession, credential_id: uuid.UUID) -> bool:
        """Thu hồi (xoá mềm). Trả False nếu khoá không tồn tại hoặc đã thu hồi rồi.

        Không xoá cứng: khoá bị thu hồi vẫn phải nhìn thấy được để truy vết những
        lô đã nhận bằng nó.
        """
        result = await session.execute(
            sa.update(sync_credentials)
            .where(sync_credentials.c.id == credential_id, sync_credentials.c.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        revoked = result.rowcount > 0
        if revoked:
            log.info("sync.credential.revoked", credential_id=str(credential_id))
        return revoked

    async def rotate(
        self,
        session: AsyncSession,
        credential_id: uuid.UUID,
        *,
        label: str = "",
    ) -> IssuedCredential:
        """Cấp khoá mới cùng phạm vi, rồi thu hồi khoá cũ — theo đúng thứ tự đó.

        Thu hồi trước rồi mới cấp sẽ tạo một khoảng thời gian hệ nguồn không có
        khoá nào dùng được. Cấp trước thì hai khoá cùng sống trong chốc lát, và đó
        là đánh đổi đúng: trùng khoá vô hại, gián đoạn thì không.
        """
        existing = (
            (await session.execute(sa.select(sync_credentials).where(sync_credentials.c.id == credential_id)))
            .mappings()
            .one_or_none()
        )
        if existing is None:
            raise CredentialError("UNKNOWN_CREDENTIAL", "Không tìm thấy khoá cần xoay")

        issued = await self.issue(
            session,
            source_system=existing["source_system"],
            source_instance_id=existing["source_instance_id"],
            label=label or f"rotated from {existing['key_prefix']}",
        )
        await self.revoke(session, credential_id)
        return issued
