"""Giữ nguyên văn payload thô của mỗi lô, và chặn payload quá lớn trước khi parse.

Hai việc, cùng một chủ đề: cái gì đi vào hệ thống thì phải đo được và giữ lại được.

**Chặn kích thước TRƯỚC khi parse, không phải sau.** `json.loads` trên một body
50 MB sẽ cấp phát bộ nhớ cho toàn bộ cây trước khi ai kịp nói "quá lớn". Đo trên
byte thô là phép đo duy nhất thực hiện được mà không phải trả giá trước.

**Kích thước đo trên byte thô; hash tính trên dạng CHUẨN HOÁ.** Hai phép đo khác
nhau vì chúng trả lời hai câu hỏi khác nhau:

* *Kích thước* là câu hỏi về đường truyền — bao nhiêu byte thực sự đi qua dây. Nó
  phải đo trên body gốc, vì đó chính là thứ ta muốn chặn.
* *Hash* là câu hỏi về toàn vẹn của bản ĐÃ LƯU — sau này băm lại có ra đúng giá
  trị cũ không. PostgreSQL lưu JSONB ở dạng đã phân tích: thứ tự khoá không giữ,
  khoá trùng bị bỏ, khoảng trắng biến mất. Nên nếu băm byte gốc, không lần nào
  băm lại từ DB khớp được, và cột hash trở thành thứ không kiểm được — tức là vô
  dụng đúng vào lúc cần nó nhất.

Vì vậy `payload_sha256` băm trên dạng chuẩn hoá (`sort_keys`, không khoảng trắng):
nó tái lập được từ JSONB đã lưu, nên `verify_integrity()` thực sự kết luận được
điều gì đó.

**`payload_sha256` KHÔNG phải phiên bản.** Nó chỉ dùng cho: kiểm toàn vẹn bản đã
lưu, và nhận ra hai lô mang cùng nội dung. Thứ tự thời gian CHỈ đến từ
`source_revision`/`source_updated_at` của từng bản ghi — xem
`docs/crm/sync_contract_v1_draft.md` mục 5.1. Băm là hàm không đơn điệu; dùng nó
để xếp trước/sau sẽ cho một thứ tự ổn định và sai ngẫu nhiên.

Một lô giữ đúng một payload — `uq_sync_payloads_run` chặn ở DB. Gửi lại cùng
`external_batch_id` trả kết quả cũ chứ không ghi thêm payload; nếu có dòng thứ
hai thì tầng idempotency đã hỏng và ta muốn biết ngay, không muốn nó im lặng.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.models.tables import sync_payloads

log = get_logger("src.services.sync_payloads")

# Trần payload đồng bộ: 5 MB. Nhỏ hơn hẳn trần upload file (20 MB) và đó là chủ ý
# — file là thao tác một lần của con người, còn payload API là dòng chảy tự động
# và nên được chia lô. Hợp đồng công bố cùng con số này (mục 14).
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024


class PayloadTooLargeError(Exception):
    """Body vượt trần. Mang theo số đo để phản hồi nói được thừa bao nhiêu."""

    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        self.error_code = "PAYLOAD_TOO_LARGE"
        self.message = f"Payload {size_bytes} byte vượt trần {limit_bytes} byte"
        super().__init__(self.message)


class PayloadIntegrityError(Exception):
    """Payload đã lưu không khớp hash của chính nó."""

    def __init__(self, sync_run_id: uuid.UUID, stored_hash: str, computed_hash: str) -> None:
        self.sync_run_id = sync_run_id
        self.stored_hash = stored_hash
        self.computed_hash = computed_hash
        self.error_code = "PAYLOAD_INTEGRITY_FAILED"
        super().__init__(f"Payload của lô {sync_run_id} không khớp hash đã lưu")


@dataclass(frozen=True, slots=True)
class RawPayload:
    """Payload thô đã đo và đã băm, sẵn sàng để lưu."""

    payload: Any
    sha256: str
    size_bytes: int
    record_count: int
    content_type: str | None = None


def canonical_bytes(payload: Any) -> bytes:
    """Dạng byte TẤT ĐỊNH của một payload đã parse.

    `sort_keys=True` + separator không khoảng trắng: cùng nội dung ra cùng byte,
    bất kể thứ tự khoá hay khoảng trắng của bản gốc. Đây là cơ sở của
    `payload_sha256` — nó tái lập được từ JSONB đã lưu, nên hash kiểm lại được.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def measure(payload: Any, *, raw_body: bytes | None = None, content_type: str | None = None) -> RawPayload:
    """Đo kích thước và băm một payload. Ném `PayloadTooLargeError` nếu vượt trần.

    Kích thước lấy từ `raw_body` khi có — đó là số byte thật đi qua đường truyền,
    và cũng là thứ trần cần chặn. Hash thì LUÔN tính trên dạng chuẩn hoá, kể cả
    khi có byte gốc: chỉ dạng chuẩn hoá mới tái lập được từ JSONB đã lưu.
    """
    size = len(raw_body) if raw_body is not None else len(canonical_bytes(payload))

    if size > MAX_PAYLOAD_BYTES:
        raise PayloadTooLargeError(size, MAX_PAYLOAD_BYTES)

    records = payload.get("records") if isinstance(payload, dict) else None
    record_count = len(records) if isinstance(records, list) else 0

    return RawPayload(
        payload=payload,
        sha256=hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        size_bytes=size,
        record_count=record_count,
        content_type=content_type,
    )


class SyncPayloadService:
    """Lưu và đọc lại payload thô của một lô."""

    async def store(
        self,
        session: AsyncSession,
        *,
        sync_run_id: uuid.UUID,
        raw: RawPayload,
        credential_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Ghi payload thô của một lô. Gọi hai lần cho cùng lô sẽ vi phạm UNIQUE."""
        payload_id = uuid.uuid4()

        await session.execute(
            sa.insert(sync_payloads).values(
                id=payload_id,
                sync_run_id=sync_run_id,
                payload=raw.payload,
                payload_sha256=raw.sha256,
                payload_bytes=raw.size_bytes,
                record_count=raw.record_count,
                content_type=raw.content_type,
                received_at=datetime.now(UTC),
                credential_id=credential_id,
            )
        )

        log.info(
            "sync.payload.stored",
            sync_run_id=str(sync_run_id),
            payload_bytes=raw.size_bytes,
            record_count=raw.record_count,
            # Log 12 ký tự đầu hash: đủ để đối chiếu, không phải cả chuỗi.
            payload_sha256=raw.sha256[:12],
        )
        return payload_id

    async def fetch(self, session: AsyncSession, sync_run_id: uuid.UUID) -> dict[str, Any] | None:
        """Đọc lại payload thô của một lô. None nếu lô không giữ payload nào."""
        row = (
            (await session.execute(sa.select(sync_payloads).where(sync_payloads.c.sync_run_id == sync_run_id)))
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    async def verify_integrity(self, session: AsyncSession, sync_run_id: uuid.UUID) -> bool:
        """Băm lại payload đã lưu và so với hash lưu kèm.

        Kết luận được vì hai bên dùng CÙNG một phép băm: `measure()` băm dạng
        chuẩn hoá lúc nhận, hàm này băm dạng chuẩn hoá lúc đọc ra. Lệch nhau nghĩa
        là bản lưu đã đổi kể từ lúc nhận.
        """
        stored = await self.fetch(session, sync_run_id)
        if stored is None:
            return False

        computed = hashlib.sha256(canonical_bytes(stored["payload"])).hexdigest()
        return bool(computed == stored["payload_sha256"])
