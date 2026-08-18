"""Chốt an toàn cho việc SUY RA XOÁ từ một ảnh chụp đầy đủ.

Đây là đoạn mã nguy hiểm nhất của cả luồng đồng bộ, vì nó là chỗ duy nhất mà sự
VẮNG MẶT của dữ liệu được diễn giải thành một hành động. Mọi chỗ khác chỉ xử lý
thứ hệ nguồn thực sự gửi; ở đây ta kết luận từ thứ hệ nguồn KHÔNG gửi.

Bốn điều kiện, phải đủ CẢ BỐN mới được tombstone:

1. `sync_mode = 'full_snapshot'`. Lô tăng dần không nói gì về bản ghi vắng mặt.
2. Đã nhận ĐỦ `chunk_total` mảnh, và có mảnh mang `snapshot_complete = true`.
3. Có `scope` khai tường minh. "Mọi thứ không có trong ảnh chụp" là tập vô hạn
   nếu không nói rõ biên.
4. Tập vắng mặt không vượt chốt: `max(5% số bản ghi còn sống, 25 bản ghi)`.

Vì sao chốt thứ tư tồn tại, và vì sao nó chặn thay vì cảnh báo. Một ảnh chụp bị
cắt cụt — truy vấn phía nguồn lỗi, kết nối đứt giữa chừng, phân trang sai — trông
Y HỆT như "phần lớn hàng tồn vừa bị xoá". Hai tình huống đó không phân biệt được
từ dữ liệu, nên phải chọn mặc định an toàn: nghi ngờ thì KHÔNG xoá. Xoá nhầm hàng
nghìn căn rồi khôi phục là việc nhiều ngày; bỏ lỡ một đợt xoá hợp lệ chỉ là chờ
tới đợt đồng bộ sau.

Ngưỡng `max(5%, 25)` có hai vế vì hai chế độ hỏng khác nhau: 5% bắt sự cố ở tập
dữ liệu lớn, còn sàn 25 bản ghi tránh việc một dự án nhỏ (40 căn) bị chặn chỉ vì
xoá 3 căn hoàn toàn bình thường đã vượt 5%.

**Ghi đè của con người** là lối thoát duy nhất, và nó cố ý thủ công: người vận
hành phải nhìn tận mắt tập vắng mặt rồi mới chấp thuận. Không có cờ cấu hình nào
tắt được chốt này — một cờ như thế sẽ được bật một lần rồi ở đó mãi.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.models.tables import areas, crm_source_records, deals, units, upload_files

log = get_logger("src.services.snapshot_gate")

# Chốt an toàn: tỷ lệ và sàn tuyệt đối. Quyết định 4 đã chốt hai con số này.
ABSENT_FRACTION_LIMIT = 0.05
ABSENT_ABSOLUTE_FLOOR = 25

ENTITY_TABLES = {"units": units, "deals": deals}
ENTITY_EXTERNAL_ID = {"units": "external_unit_id", "deals": "external_deal_id"}


def safety_limit(live_count: int) -> int:
    """`max(5% số bản ghi còn sống, 25)` — trần số bản ghi được phép tombstone.

    Làm tròn XUỐNG phần trăm: chốt an toàn nên nghiêng về phía chặt hơn khi có
    nghi ngờ, và `floor` là cách nghiêng đó.
    """
    return max(math.floor(live_count * ABSENT_FRACTION_LIMIT), ABSENT_ABSOLUTE_FLOOR)


@dataclass(frozen=True, slots=True)
class Completeness:
    """Trạng thái đủ/thiếu mảnh của một ảnh chụp."""

    snapshot_id: str
    chunks_received: int
    chunk_total: int | None
    complete_flag_seen: bool
    missing_chunks: tuple[int, ...] = ()

    @property
    def is_complete(self) -> bool:
        """Đủ mảnh VÀ có cờ kết thúc. Thiếu một trong hai đều là chưa xong.

        Đòi cả hai chứ không chỉ đếm mảnh: hệ nguồn có thể gửi đủ số lô nhưng vẫn
        chưa chốt (`snapshot_complete=false`), và ngược lại một cờ kết thúc tới
        sớm không chứng minh các mảnh trước đó đã tới.
        """
        return (
            self.chunk_total is not None
            and self.chunks_received == self.chunk_total
            and not self.missing_chunks
            and self.complete_flag_seen
        )


@dataclass(slots=True)
class GateDecision:
    """Kết luận của chốt, kèm đủ dữ kiện để giải thích vì sao."""

    allowed: bool
    reason: str
    absent_external_ids: list[str] = field(default_factory=list)
    live_count: int = 0
    limit: int = 0
    completeness: Completeness | None = None
    overridden: bool = False

    @property
    def absent_count(self) -> int:
        return len(self.absent_external_ids)

    def as_details(self) -> dict[str, Any]:
        """Dạng máy đọc được, để ghi vào `reconciliation_findings.details`."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "absent_count": self.absent_count,
            # Cắt danh sách: một ảnh chụp cắt cụt có thể vắng hàng nghìn id, và
            # nhét hết vào một dòng finding sẽ làm bảng phình mà không thêm thông
            # tin. 50 cái đầu đủ để người vận hành nhận ra mẫu.
            "absent_sample": self.absent_external_ids[:50],
            "absent_truncated": self.absent_count > 50,
            "live_count": self.live_count,
            "limit": self.limit,
            "overridden": self.overridden,
            "chunks_received": self.completeness.chunks_received if self.completeness else None,
            "chunk_total": self.completeness.chunk_total if self.completeness else None,
            "missing_chunks": list(self.completeness.missing_chunks) if self.completeness else [],
            "complete_flag_seen": self.completeness.complete_flag_seen if self.completeness else None,
        }


class SnapshotGate:
    """Quyết định một ảnh chụp có được phép suy ra xoá hay không, và thực hiện nó."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Đủ mảnh chưa ------------------------------------------------------

    async def completeness(self, *, source_system: str, source_instance_id: str, snapshot_id: str) -> Completeness:
        """Gom các lô cùng `snapshot_id` để biết ảnh chụp đã đủ mảnh chưa."""
        rows = (
            (
                await self._session.execute(
                    sa.select(
                        upload_files.c.chunk_index,
                        upload_files.c.chunk_total,
                        upload_files.c.snapshot_complete,
                    ).where(
                        upload_files.c.source_system == source_system,
                        upload_files.c.source_instance_id == source_instance_id,
                        upload_files.c.snapshot_id == snapshot_id,
                    )
                )
            )
            .mappings()
            .all()
        )

        indexes = {row["chunk_index"] for row in rows if row["chunk_index"] is not None}
        totals = {row["chunk_total"] for row in rows if row["chunk_total"] is not None}
        complete_seen = any(bool(row["snapshot_complete"]) for row in rows)

        # Hệ nguồn khai hai `chunk_total` khác nhau cho cùng ảnh chụp là mâu
        # thuẫn; lấy giá trị LỚN NHẤT để nghiêng về phía "còn thiếu".
        chunk_total = max(totals) if totals else None
        missing = tuple(sorted(set(range(chunk_total)) - indexes)) if chunk_total is not None else ()

        return Completeness(
            snapshot_id=snapshot_id,
            chunks_received=len(indexes),
            chunk_total=chunk_total,
            complete_flag_seen=complete_seen,
            missing_chunks=missing,
        )

    # --- Tập vắng mặt trong phạm vi ----------------------------------------

    async def absent_within_scope(
        self,
        *,
        project_id: uuid.UUID,
        source_system: str,
        source_instance_id: str,
        snapshot_id: str,
        entity: str,
        scope: dict[str, Any],
    ) -> tuple[list[str], int]:
        """Các bản ghi CÒN SỐNG trong phạm vi mà ảnh chụp không nhắc tới.

        Trả `(danh sách external_id vắng mặt, số bản ghi còn sống trong phạm vi)`.

        Phạm vi được áp NGHIÊM NGẶT: chỉ những bản ghi nằm trong `scope` mới được
        xét. Bỏ qua phạm vi sẽ biến một ảnh chụp của MỘT phân khu thành lệnh xoá
        toàn dự án.
        """
        table = ENTITY_TABLES[entity]
        id_column = ENTITY_EXTERNAL_ID[entity]

        present = set(
            (
                await self._session.execute(
                    sa.select(crm_source_records.c.source_record_id).where(
                        crm_source_records.c.source_system == source_system,
                        crm_source_records.c.source_instance_id == source_instance_id,
                        crm_source_records.c.source_entity == entity,
                        crm_source_records.c.external_batch_id.in_(
                            sa.select(upload_files.c.external_batch_id).where(
                                upload_files.c.snapshot_id == snapshot_id,
                                upload_files.c.source_instance_id == source_instance_id,
                            )
                        ),
                    )
                )
            ).scalars()
        )

        live_query = sa.select(table.c[id_column]).where(
            table.c.source_instance_id == source_instance_id,
            table.c.deleted_at.is_(None),
        )
        live_query = self._apply_scope(live_query, table, entity, project_id, scope)

        live = list((await self._session.execute(live_query)).scalars())
        absent = sorted(set(live) - present)
        return absent, len(live)

    def _apply_scope(self, query, table, entity: str, project_id: uuid.UUID, scope: dict[str, Any]):
        """Thu hẹp truy vấn về đúng phạm vi ảnh chụp tự khai."""
        area_refs = scope.get("area_refs")

        if entity == "units":
            area_filter = sa.select(areas.c.id).where(areas.c.project_id == project_id)
            if area_refs:
                conditions = [
                    sa.and_(areas.c.area_name == ref.get("area_name"), areas.c.unit_type == ref.get("unit_type"))
                    for ref in area_refs
                    if ref.get("area_name")
                ]
                ids = [ref["area_id"] for ref in area_refs if ref.get("area_id")]
                if ids:
                    conditions.append(areas.c.id.in_(ids))
                if conditions:
                    area_filter = area_filter.where(sa.or_(*conditions))
            return query.where(table.c.area_id.in_(area_filter))

        # deals: phạm vi đi qua căn mà giao dịch gắn vào.
        unit_filter = sa.select(units.c.id).where(
            units.c.area_id.in_(sa.select(areas.c.id).where(areas.c.project_id == project_id))
        )
        return query.where(table.c.unit_id.in_(unit_filter))

    # --- Quyết định --------------------------------------------------------

    async def evaluate(
        self,
        *,
        project_id: uuid.UUID,
        source_system: str,
        source_instance_id: str,
        snapshot_id: str,
        entity: str,
        scope: dict[str, Any],
        override: bool = False,
    ) -> GateDecision:
        """Có được phép tombstone tập vắng mặt không, và vì sao.

        KHÔNG ghi gì. Tách quyết định khỏi hành động để phần quyết định test được
        mà không đụng dữ liệu, và để người vận hành xem trước được điều sắp xảy ra.
        """
        state = await self.completeness(
            source_system=source_system, source_instance_id=source_instance_id, snapshot_id=snapshot_id
        )

        if not state.is_complete:
            # Mảnh thiếu KHÔNG BAO GIỜ được kích hoạt xoá. Đây là bất biến quan
            # trọng nhất của module: ảnh chụp dở dang trông y hệt một đợt xoá lớn.
            return GateDecision(
                allowed=False,
                reason=(
                    f"Ảnh chụp chưa đủ mảnh ({state.chunks_received}/{state.chunk_total}), "
                    f"thiếu {list(state.missing_chunks)}; cờ kết thúc: {state.complete_flag_seen}"
                ),
                completeness=state,
            )

        if not scope or not scope.get("entities"):
            return GateDecision(
                allowed=False,
                reason="Ảnh chụp không khai phạm vi — không xác định được tập vắng mặt",
                completeness=state,
            )

        absent, live_count = await self.absent_within_scope(
            project_id=project_id,
            source_system=source_system,
            source_instance_id=source_instance_id,
            snapshot_id=snapshot_id,
            entity=entity,
            scope=scope,
        )
        limit = safety_limit(live_count)

        if len(absent) > limit and not override:
            return GateDecision(
                allowed=False,
                reason=(
                    f"Tập vắng mặt {len(absent)} vượt chốt an toàn {limit} "
                    f"(max(5% của {live_count}, {ABSENT_ABSOLUTE_FLOOR})). "
                    "Không tombstone gì cả; cần người duyệt tường minh."
                ),
                absent_external_ids=absent,
                live_count=live_count,
                limit=limit,
                completeness=state,
            )

        return GateDecision(
            allowed=True,
            reason=(
                f"Ghi đè của người vận hành: tombstone {len(absent)} bản ghi vượt chốt {limit}"
                if override and len(absent) > limit
                else f"Tập vắng mặt {len(absent)} nằm trong chốt {limit}"
            ),
            absent_external_ids=absent,
            live_count=live_count,
            limit=limit,
            completeness=state,
            overridden=bool(override and len(absent) > limit),
        )

    async def apply(self, decision: GateDecision, *, entity: str, source_instance_id: str) -> int:
        """Tombstone tập vắng mặt. Chỉ chạy khi `decision.allowed`.

        Toàn bộ nằm trong transaction của phía gọi: hoặc cả tập được đánh dấu,
        hoặc không bản ghi nào — một đợt xoá thực hiện được một nửa là trạng thái
        không ai dựng lại được.
        """
        if not decision.allowed:
            raise ValueError("Không được gọi apply() khi chốt an toàn chưa cho phép")
        if not decision.absent_external_ids:
            return 0

        table = ENTITY_TABLES[entity]
        id_column = ENTITY_EXTERNAL_ID[entity]
        now = datetime.now(UTC)

        result = await self._session.execute(
            sa.update(table)
            .where(
                table.c.source_instance_id == source_instance_id,
                table.c[id_column].in_(decision.absent_external_ids),
                table.c.deleted_at.is_(None),
            )
            .values(deleted_at=now, updated_at=now)
        )

        log.info(
            "snapshot.gate.tombstoned",
            entity=entity,
            count=result.rowcount,
            overridden=decision.overridden,
            limit=decision.limit,
        )
        return int(result.rowcount)
