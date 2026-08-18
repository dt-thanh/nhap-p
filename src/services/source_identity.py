"""SourceIdentityService — quyết định số phận của một bản ghi nguồn.

Đồng bộ là MỘT CHIỀU: CRM là nguồn sự thật, bên này chỉ soi gương. Vì vậy service
này không bao giờ ghi ngược về CRM và không tự hoà giải khi hai payload mâu thuẫn
— nó GHI NHẬN đụng độ rồi giữ nguyên trạng thái đã chấp nhận.

Mỗi bản ghi đi qua đúng một quyết định:

| Quyết định       | Khi nào |
|------------------|---------|
| `insert`         | Danh tính chưa từng thấy, lệnh là upsert |
| `update`         | Bản đến mới hơn bản đang giữ |
| `skip_stale`     | Bản đến cũ hơn — không ghi đè, kể cả lên tombstone |
| `duplicate_noop` | Cùng phiên bản, cùng dấu vân — nạp lại y hệt |
| `conflict`       | Cùng phiên bản, KHÁC dấu vân — không thể xếp thứ tự, giữ nguyên bản cũ |
| `tombstone`      | Lệnh xoá, và nó mới hơn bản đang giữ |

**So sánh phiên bản** (`_compare`) không bao giờ dùng thời điểm bản ghi tới nơi.
Giờ máy nhận không nói lên thứ tự sự kiện ở hệ nguồn; hai lô gửi lệch nhau vài
giây có thể tới ngược thứ tự. Chỉ HAI căn cứ được chấp nhận, xếp theo độ tin cậy:

1. `source_revision` — số thứ tự do CRM cấp. Không phụ thuộc đồng hồ nên ưu tiên
   nhất; dùng khi CẢ HAI bên đều có.
2. `source_updated_at` — mốc có múi giờ.

Hết. Không có căn cứ thứ ba. So không được thì kết quả là `unknown` — nghĩa là
"không biết bản nào mới hơn" — chứ không phải một phép so khác.

**`payload_hash` KHÔNG phải là phiên bản.** Nó không xuất hiện trong `_compare`
và không bao giờ được dùng để quyết định bản nào mới hơn. Hash là hàm băm nên
không đơn điệu: `hash(A) > hash(B)` không mang thông tin nào về việc A hay B xảy
ra trước, nên xếp thứ tự bằng nó sẽ cho một thứ tự ổn định, lặp lại được, và SAI
ngẫu nhiên — loại lỗi không bao giờ tự lộ ra. Hash chỉ dùng cho phép so BẰNG/KHÁC:

* cùng phiên bản + cùng hash → `duplicate_noop`;
* cùng phiên bản + khác hash → `conflict` (giữ nguyên bản đã chấp nhận);
* kiểm toàn vẹn payload, và so trường đã ánh xạ khi đối soát.

Bản ghi không mang phiên bản nào bị `JsonPayloadParser` từ chối từ trước khi tới
đây (`MISSING_SOURCE_VERSION`), kể cả lệnh xoá.

═══════════════════════════════════════════════════════════════════════════════
 KHOÁ HÀNG — vì sao `_load()` phải là `SELECT ... FOR UPDATE`
═══════════════════════════════════════════════════════════════════════════════

Toàn bộ bảng quyết định ở trên là một trình tự **đọc → so → ghi**, và một trình
tự như thế chỉ đúng khi không ai chen vào giữa. Trước bản vá này, `_load()` đọc
bằng một câu `SELECT` thường, nên hai lô song song cho CÙNG một danh tính cùng
thấy một bản đang giữ đã cũ:

    đang giữ: revision 5
    lô A (revision 7)   đọc 5  →  "newer"  →  update
    lô B (revision 6)   đọc 5  →  "newer"  →  update      ← lẽ ra phải skip_stale
    lô nào COMMIT SAU thì thắng — kể cả khi nó mang phiên bản THẤP hơn

Hậu quả không nằm ở chỗ mất một bản cập nhật, mà ở chỗ **không ai biết là đã mất**:
cả hai lô trả `202`, `rows_failed = 0`, `conflict_count = 0`. Cơ chế phát hiện
đụng độ cũng không cứu được, vì nó dựa trên việc ĐỌC ĐƯỢC bản đang giữ — mà ở đây
cả hai đọc trúng cùng một bản đã cũ, nên không bên nào nhìn thấy bên kia.

`FOR UPDATE` biến trình tự đó thành tuần tự cho từng danh tính:

    A khoá dòng → đọc 5 → nhận 7 → ghi danh tính + bản sao → COMMIT (nhả khoá)
    B chờ       → đọc 7 → 6 < 7   → skip_stale, không chạm bảng nghiệp vụ

**Khoá phải được giữ tới lúc COMMIT của transaction NGOÀI**, không phải tới cuối
SAVEPOINT của bản ghi. Điều đó đúng sẵn theo cách `apply_records` dựng transaction:
quyết định danh tính và việc chiếu xuống `units`/`deals` nằm trong cùng một
transaction, nên trong suốt khoảng đó không lô nào khác đọc được dòng danh tính
để mà quyết định sai. Đừng tách chúng ra hai transaction.

**Chỉ khoá được dòng ĐÃ TỒN TẠI.** Cuộc đua chèn lần đầu vì thế cần một cơ chế
khác — xem `_insert_or_lose_the_race()`.

**Thứ tự khoá phải tất định.** Xem `lock_identities()`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.models.tables import crm_source_records
from src.services.json_payload import SourceRecord

log = get_logger("src.services.source_identity")

Decision = Literal["insert", "update", "skip_stale", "duplicate_noop", "conflict", "tombstone"]

# Quyết định thực sự làm đổi trạng thái đã chấp nhận. Dùng để đếm và để quyết
# định trạng thái kết thúc của lô.
APPLIED_DECISIONS: frozenset[str] = frozenset({"insert", "update", "tombstone"})

Comparison = Literal["newer", "older", "same", "unknown"]


@dataclass(frozen=True, slots=True)
class Identity:
    """Khoá danh tính của một bản ghi ở hệ nguồn — khớp `uq_crm_source_records_identity`."""

    source_system: str
    source_instance_id: str
    source_entity: str
    source_record_id: str


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """Kết quả xử lý một bản ghi, đủ để giải thích vì sao lại thế."""

    identity: Identity
    decision: Decision
    comparison: Comparison
    reason: str
    stored_payload_hash: str | None = None
    incoming_payload_hash: str | None = None


def _compare(record: SourceRecord, stored_revision: int | None, stored_updated_at: datetime | None) -> Comparison:
    """So phiên bản bản đến với phiên bản đang giữ. Không dùng giờ máy nhận."""
    if record.source_revision is not None and stored_revision is not None:
        if record.source_revision > stored_revision:
            return "newer"
        return "older" if record.source_revision < stored_revision else "same"

    if record.source_updated_at is not None and stored_updated_at is not None:
        if record.source_updated_at > stored_updated_at:
            return "newer"
        return "older" if record.source_updated_at < stored_updated_at else "same"

    incoming_has_version = record.source_revision is not None or record.source_updated_at is not None
    stored_has_version = stored_revision is not None or stored_updated_at is not None

    # Bản đang giữ không mang phiên bản nào để so; một bản có phiên bản là thông
    # tin tốt hơn hẳn. Cùng quy tắc với luồng CSV của S1.
    if incoming_has_version and not stored_has_version:
        return "newer"
    # Chiều ngược lại: không hạ cấp một bản đã có phiên bản bằng một bản không có.
    if stored_has_version and not incoming_has_version:
        return "older"
    return "unknown"


class SourceIdentityService:
    """Phân giải danh tính, so phiên bản, và cập nhật `crm_source_records`.

    Nhận `AsyncSession` từ bên gọi chứ không tự mở: toàn bộ một lô phải nằm trong
    cùng ranh giới transaction với việc ghi lỗi và cập nhật trạng thái lô.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def apply(
        self,
        record: SourceRecord,
        *,
        identity: Identity,
        sync_run_id: uuid.UUID,
        external_batch_id: str | None,
        now: datetime | None = None,
    ) -> DecisionResult:
        """Xử lý đúng một bản ghi nguồn và ghi lại dấu vết của quyết định."""
        moment = now or datetime.now(UTC)
        stored = await self._load(identity)

        if stored is None:
            stored = await self._insert_or_lose_the_race(
                record, identity, sync_run_id, external_batch_id, moment
            )
            if stored is None:
                # Chèn thành công: danh tính này thật sự chưa từng thấy.
                return DecisionResult(
                    identity=identity,
                    decision="tombstone" if record.operation == "delete" else "insert",
                    comparison="newer",
                    reason="Danh tính chưa từng xuất hiện",
                    incoming_payload_hash=record.payload_hash,
                )
            # Thua cuộc đua chèn lần đầu: một lô song song đã tạo dòng này và đã
            # commit. Bây giờ ta ĐANG GIỮ khoá của nó, nên phần còn lại chạy y hệt
            # đường bình thường — so phiên bản với bản họ vừa ghi.
            #
            # `moment` phải được kéo lên ÍT NHẤT bằng `first_seen_at` của họ.
            # `moment` được chốt từ đầu lô, nên hai lô chạy song song có thể mang
            # hai mốc lệch nhau vài mili giây theo chiều bất kỳ. Nếu lô thua có mốc
            # SỚM hơn, câu cập nhật sẽ ghi `last_seen_at < first_seen_at` và
            # `ck_crm_source_records_seen_order` nổ — biến một cuộc đua đã xử lý
            # đúng thành một dòng lỗi. Đây không phải chỉnh giờ hộ hệ nguồn:
            # `first_seen_at`/`last_seen_at` là mốc của PHÍA NHẬN (lần đầu và lần
            # cuối NHÌN THẤY), không phải mốc sự kiện của hệ nguồn, và thứ tự sự
            # kiện vẫn hoàn toàn do `source_revision` quyết định.
            if stored["first_seen_at"] > moment:
                moment = stored["first_seen_at"]
            log.info(
                "sync.first_insert_race",
                source_entity=identity.source_entity,
                source_record_id=identity.source_record_id,
                stored_revision=stored["source_revision"],
                incoming_revision=record.source_revision,
            )

        comparison = _compare(record, stored["source_revision"], stored["source_updated_at"])

        if comparison == "older":
            # Bao trùm cả "upsert cũ không được làm sống lại tombstone": bản đến
            # cũ hơn thì không có quyền đụng vào trạng thái đang giữ, bất kể lệnh.
            return await self._touch(
                identity,
                stored,
                sync_run_id,
                external_batch_id,
                moment,
                decision="skip_stale",
                comparison=comparison,
                reason="Phiên bản đến cũ hơn phiên bản đang giữ",
                incoming_hash=record.payload_hash,
            )

        if comparison in ("same", "unknown"):
            if record.payload_hash == stored["payload_hash"]:
                return await self._touch(
                    identity,
                    stored,
                    sync_run_id,
                    external_batch_id,
                    moment,
                    decision="duplicate_noop",
                    comparison=comparison,
                    reason="Cùng phiên bản và cùng dấu vân payload",
                    incoming_hash=record.payload_hash,
                )
            return await self._record_conflict(
                identity, stored, sync_run_id, external_batch_id, moment, record, comparison
            )

        # comparison == "newer"
        decision: Decision = "tombstone" if record.operation == "delete" else "update"
        return await self._accept(
            identity, stored, record, sync_run_id, external_batch_id, moment, decision, comparison
        )

    # --- Đọc / ghi ---------------------------------------------------------

    async def _load(self, identity: Identity) -> dict | None:
        """Đọc trạng thái đang giữ, và KHOÁ dòng đó cho tới hết transaction.

        `FOR UPDATE` ở đây không phải một tối ưu — nó là điều kiện để mọi thứ phía
        dưới còn đúng. Xem docstring `_KHOÁ HÀNG` ở đầu module.
        """
        row = (
            (
                await self._session.execute(
                    sa.select(crm_source_records)
                    .where(
                        crm_source_records.c.source_system == identity.source_system,
                        crm_source_records.c.source_instance_id == identity.source_instance_id,
                        crm_source_records.c.source_entity == identity.source_entity,
                        crm_source_records.c.source_record_id == identity.source_record_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    async def lock_identities(self, identities: Sequence[Identity]) -> int:
        """Khoá TRƯỚC mọi dòng danh tính đã tồn tại của một lô, theo thứ tự TẤT ĐỊNH.

        Không có bước này, mỗi bản ghi tự khoá dòng của nó khi tới lượt — tức là
        theo thứ tự bản ghi xuất hiện trong phong bì. Hai lô chứa cùng hai bản ghi
        theo hai thứ tự ngược nhau (`[A, B]` và `[B, A]`) sẽ khoá chéo nhau và
        deadlock. Hợp đồng KHÔNG quy định thứ tự bản ghi trong lô, nên không được
        trông cậy vào việc hai hệ nguồn tình cờ xếp giống nhau.

        `ORDER BY source_record_id` đặt nút `LockRows` lên trên nút `Sort`, nên
        PostgreSQL khoá theo đúng thứ tự đã sắp — mọi transaction vì thế đi qua các
        dòng theo cùng một hướng, và một chu trình chờ không hình thành được.

        Chỉ khoá những dòng ĐÃ TỒN TẠI. Danh tính chưa từng thấy không có dòng để
        khoá; cuộc đua chèn lần đầu được xử lý ở `apply()` bằng ràng buộc UNIQUE.

        Thứ tự XỬ LÝ bản ghi KHÔNG đổi. Bước này chỉ lấy khoá; vòng lặp ở
        `apply_records` vẫn chạy theo đúng thứ tự phong bì, nên `json_path` và thứ
        tự lỗi trả về giữ nguyên.

        Returns:
            Số dòng đã khoá — dùng để test khẳng định bước này thật sự chạy.
        """
        if not identities:
            return 0

        # Một lô luôn thuộc đúng một `(source_system, source_instance_id,
        # source_entity)` — route `/sync/{entity}` và phong bì đã chốt cả ba. Lấy
        # từ phần tử đầu và chỉ biến thiên theo `source_record_id`.
        first = identities[0]
        record_ids = sorted({identity.source_record_id for identity in identities})

        rows = await self._session.execute(
            sa.select(crm_source_records.c.id)
            .where(
                crm_source_records.c.source_system == first.source_system,
                crm_source_records.c.source_instance_id == first.source_instance_id,
                crm_source_records.c.source_entity == first.source_entity,
                crm_source_records.c.source_record_id.in_(record_ids),
            )
            .order_by(crm_source_records.c.source_record_id)
            .with_for_update()
        )
        return len(rows.scalars().all())

    async def _insert_or_lose_the_race(
        self,
        record: SourceRecord,
        identity: Identity,
        sync_run_id: uuid.UUID,
        external_batch_id: str | None,
        moment: datetime,
    ) -> dict | None:
        """Chèn dòng danh tính mới, hoặc nhường cho lô đã chèn trước.

        `FOR UPDATE` không khoá được một dòng CHƯA TỒN TẠI, nên khoá hàng không che
        được cuộc đua chèn lần đầu: hai lô cùng thấy `stored is None` và cùng đi
        tới câu INSERT. Thứ chặn nhân bản ở đây là ràng buộc
        `uq_crm_source_records_identity`.

        Để nguyên thì bên thua nhận một `IntegrityError`, và exception đó rơi vào
        cái bẫy chung ở `apply_records` — bản ghi bị ghi nhận thành
        `CONSTRAINT_VIOLATION` và lô thành `failed`. AN TOÀN (không nhân bản) nhưng
        SAI: bên thua có thể là bên mang phiên bản CAO HƠN, và nó bị vứt đi vì lý
        do thời điểm chứ không vì nội dung.

        **`ON CONFLICT DO NOTHING RETURNING id` thay cho try/except.** Đụng độ được
        xử lý bên trong PostgreSQL, nên KHÔNG có exception nào đi qua ranh giới
        SAVEPOINT. Đó không phải chuyện thẩm mỹ: một exception xảy ra trong một
        SAVEPOINT lồng nhau kéo theo cả câu hỏi "session còn dùng được không, và
        đúng tới mức nào" — và câu trả lời phụ thuộc vào phiên bản thư viện chứ
        không phụ thuộc vào mã ở đây. Không sinh ra exception thì không phải trả
        lời câu hỏi đó.

        Đọc kết quả:

        * có dòng trả về  → ta chèn được; danh tính này thật sự chưa từng thấy.
        * không có dòng   → ai đó đang giữ nó. Đọc lại: `_load()` mang `FOR UPDATE`
          nên nó CHỜ transaction kia commit, rồi ta cầm khoá và đi tiếp đường so
          phiên bản bình thường.

        `DO NOTHING` chỉ định đích danh ràng buộc danh tính, không dùng `DO NOTHING`
        trần: dạng trần sẽ nuốt MỌI vi phạm ràng buộc, kể cả những vi phạm thật sự
        cần nổ ra.

        Returns:
            `None` khi chèn thành công. Dòng đang giữ (ĐÃ KHOÁ) khi thua cuộc đua.
        """
        decision: Decision = "tombstone" if record.operation == "delete" else "insert"
        inserted = await self._insert_first_sight(
            record, identity, sync_run_id, external_batch_id, moment, decision
        )
        if inserted:
            return None

        stored = await self._load(identity)
        if stored is None:
            # Không chèn được, mà cũng không có dòng nào. Không có kịch bản hợp lệ
            # nào cho việc này — nuốt nó đi sẽ biến một bất thường thật thành một
            # bản ghi lặng lẽ biến mất.
            raise RuntimeError(
                f"Danh tính '{identity.source_record_id}' vừa đụng độ khi chèn nhưng không đọc lại được. "
                f"Ràng buộc danh tính có thể đã bị đổi."
            )
        return stored

    async def _insert_first_sight(
        self,
        record: SourceRecord,
        identity: Identity,
        sync_run_id: uuid.UUID,
        external_batch_id: str | None,
        moment: datetime,
        decision: Decision,
    ) -> bool:
        """Danh tính chưa từng thấy.

        Lệnh xoá cho một bản ghi chưa biết vẫn được ghi nhận thành tombstone chứ
        không bỏ qua: nếu bỏ, một lô upsert cũ đến sau sẽ tạo ra bản ghi mà hệ
        nguồn đã xoá từ lâu.

        Returns:
            `True` nếu dòng được chèn; `False` nếu một lô song song đã chèn trước.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = (
            pg_insert(crm_source_records)
            .values(
                id=uuid.uuid4(),
                source_system=identity.source_system,
                source_instance_id=identity.source_instance_id,
                source_entity=identity.source_entity,
                source_record_id=identity.source_record_id,
                first_sync_run_id=sync_run_id,
                last_sync_run_id=sync_run_id,
                external_batch_id=external_batch_id,
                source_revision=record.source_revision,
                source_updated_at=record.source_updated_at,
                payload_hash=record.payload_hash,
                state="tombstoned" if decision == "tombstone" else "active",
                last_decision=decision,
                conflict_count=0,
                first_seen_at=moment,
                last_seen_at=moment,
                deleted_at=moment if decision == "tombstone" else None,
            )
            # Chỉ định ĐÍCH DANH ràng buộc danh tính. `DO NOTHING` trần sẽ nuốt mọi
            # vi phạm ràng buộc, kể cả những vi phạm thật sự cần nổ ra.
            .on_conflict_do_nothing(constraint="uq_crm_source_records_identity")
            .returning(crm_source_records.c.id)
        )
        return (await self._session.execute(statement)).one_or_none() is not None

    async def _accept(
        self,
        identity: Identity,
        stored: dict,
        record: SourceRecord,
        sync_run_id: uuid.UUID,
        external_batch_id: str | None,
        moment: datetime,
        decision: Decision,
        comparison: Comparison,
    ) -> DecisionResult:
        """Bản đến mới hơn: cập nhật trạng thái đã chấp nhận.

        Một `upsert` mới hơn ĐƯỢC PHÉP làm sống lại bản ghi đã tombstone — hệ
        nguồn xoá rồi tạo lại là chuyện bình thường, và CRM là nguồn sự thật.
        """
        tombstoned = decision == "tombstone"
        await self._session.execute(
            sa.update(crm_source_records)
            .where(crm_source_records.c.id == stored["id"])
            .values(
                last_sync_run_id=sync_run_id,
                external_batch_id=external_batch_id,
                source_revision=record.source_revision,
                source_updated_at=record.source_updated_at,
                payload_hash=record.payload_hash,
                state="tombstoned" if tombstoned else "active",
                last_decision=decision,
                last_seen_at=moment,
                deleted_at=moment if tombstoned else None,
            )
        )
        return DecisionResult(
            identity=identity,
            decision=decision,
            comparison=comparison,
            reason="Phiên bản đến mới hơn phiên bản đang giữ",
            stored_payload_hash=stored["payload_hash"],
            incoming_payload_hash=record.payload_hash,
        )

    async def _touch(
        self,
        identity: Identity,
        stored: dict,
        sync_run_id: uuid.UUID,
        external_batch_id: str | None,
        moment: datetime,
        *,
        decision: Decision,
        comparison: Comparison,
        reason: str,
        incoming_hash: str,
    ) -> DecisionResult:
        """Không đổi trạng thái đã chấp nhận, chỉ ghi nhận là đã nhìn thấy.

        `last_seen_at` vẫn tiến lên để biết bản ghi còn được hệ nguồn gửi; phiên
        bản, dấu vân và `state` giữ nguyên.
        """
        await self._session.execute(
            sa.update(crm_source_records)
            .where(crm_source_records.c.id == stored["id"])
            .values(
                last_sync_run_id=sync_run_id,
                external_batch_id=external_batch_id,
                last_decision=decision,
                last_seen_at=moment,
            )
        )
        return DecisionResult(
            identity=identity,
            decision=decision,
            comparison=comparison,
            reason=reason,
            stored_payload_hash=stored["payload_hash"],
            incoming_payload_hash=incoming_hash,
        )

    async def _record_conflict(
        self,
        identity: Identity,
        stored: dict,
        sync_run_id: uuid.UUID,
        external_batch_id: str | None,
        moment: datetime,
        record: SourceRecord,
        comparison: Comparison,
    ) -> DecisionResult:
        """Cùng phiên bản nhưng khác nội dung — không có căn cứ nào để xếp thứ tự.

        Giữ nguyên bản đang chấp nhận và ghi lại dấu vân của bản gây đụng độ. Ghi
        đè theo kiểu "bản đến sau thắng" chính là dùng giờ máy nhận thay cho thứ
        tự sự kiện — thứ mà toàn bộ service này tồn tại để tránh.
        """
        await self._session.execute(
            sa.update(crm_source_records)
            .where(crm_source_records.c.id == stored["id"])
            .values(
                last_sync_run_id=sync_run_id,
                external_batch_id=external_batch_id,
                last_decision="conflict",
                last_seen_at=moment,
                conflict_count=crm_source_records.c.conflict_count + 1,
                conflict_payload_hash=record.payload_hash,
                conflict_detected_at=moment,
            )
        )
        log.warning(
            "sync.conflict_detected",
            source_entity=identity.source_entity,
            source_record_id=identity.source_record_id,
            comparison=comparison,
        )
        return DecisionResult(
            identity=identity,
            decision="conflict",
            comparison=comparison,
            reason="Cùng phiên bản nhưng dấu vân payload khác — giữ nguyên bản đang chấp nhận",
            stored_payload_hash=stored["payload_hash"],
            incoming_payload_hash=record.payload_hash,
        )
