"""Phát lại `sync_payloads` thành `unit_status_history`/`deal_status_history`.

    python -m scripts.backfill_status_history                       # tất cả source_instance_id
    python -m scripts.backfill_status_history --dry-run
    python -m scripts.backfill_status_history --source-instance-id crm-a
    python -m scripts.backfill_status_history --batch-size 500

KHÔNG phải migration: script này re-runnable, và kết quả phụ thuộc chính sách
lưu giữ của `sync_payloads` tại thời điểm chạy — 0027/0028 đặt tiền lệ "không
backfill trong migration", đây là nơi việc đó thực sự xảy ra, tách bạch.

**Xác minh cửa sổ lưu giữ TRƯỚC khi chạy thật:**

    SELECT min(received_at), max(received_at), count(*) FROM sync_payloads;

`0010_sync_payload_retention` viết chính sách xoá 90 ngày, nhưng
`grep -rn "retention|purge" src/services/sync_payloads.py` không khớp gì —
CHƯA có code nào thực thi chính sách đó. Nghĩa là payload có thể còn đầy đủ,
nhưng đừng GIẢ ĐỊNH — chạy câu SELECT trên trước, không đoán số.

## Giới hạn đã biết (không phải điều xin lỗi, mà là điều phải đọc trước khi dùng
## số ra từ backfill này)

1. **Gấp khúc lặp lại (incremental collapse).** `sync_mode='incremental'` chỉ
   mang bản ghi ĐÃ ĐỔI kể từ lần đồng bộ trước. Hai lần chuyển trạng thái thật
   giữa hai lượt đồng bộ liên tiếp gộp thành MỘT cạnh trong nhật ký phát lại.
   Mọi `cancellation_adjusted_absorption` tính từ dữ liệu backfill là CẬN DƯỚI,
   không phải số đếm đầy đủ — gắn nhãn `source='backfill_replay'` để tầng đọc
   biết mà diễn giải đúng.
2. **Cắt cụt biên trái (left censoring).** Trước `min(received_at)`, không có
   bằng chứng nào. Bất kỳ cutoff nào mở cửa sổ trước mốc đó phải trả
   `insufficient_history`, không phải một con số.
3. **Payload `partial` thiếu trường status bị BỎ QUA, không suy đoán.**
   `payload_completeness='partial'` nghĩa là trường vắng mặt = giữ nguyên giá
   trị cũ — không phải "không đổi status", mà là "không nói gì về status".
4. **Dữ liệu seed (0021, 0023) bị loại.** `units`/`deals` do seed migration tạo
   thẳng, không qua payload nào — không có cách nào backfill chúng, và không
   nên: chúng không phải quan sát CRM thật.
5. **Bản ghi không giải quyết được danh tính bị bỏ qua, có log cảnh báo.** Một
   `external_unit_id`/`external_deal_id` không khớp `units`/`deals` hiện có
   (đã bị xoá cứng, hoặc chưa từng đồng bộ) không có chỗ để treo sự kiện — FK
   sẽ từ chối nếu cố ghi, nên script tự lọc trước và đếm riêng.

**Idempotent.** `INSERT ... ON CONFLICT (unit_id, changed_at, new_status)
WHERE source='backfill_replay' DO NOTHING` — khoá theo `0032_replay_identity_index`.
Chạy lại toàn bộ script nhiều lần trên cùng payload cho ra đúng một bản ghi mỗi
sự kiện, không nhân đôi.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_engine, get_session_factory
from src.logging_config import get_logger
from src.models.tables import deal_status_history, deals, sync_payloads, unit_status_history, units
from src.services.domain_projection import DEAL_STATUS_ALIASES, DEAL_STATUSES, UNIT_STATUSES

log = get_logger("scripts.backfill_status_history")

SOURCE = "backfill_replay"
DEFAULT_BATCH_SIZE = 500


@dataclass(slots=True)
class ReplayStats:
    """Đếm cho MỘT source_instance_id. In ra cuối lượt chạy, không ghi vào DB."""

    unit_events_inserted: int = 0
    deal_events_inserted: int = 0
    skipped_partial: int = 0
    skipped_unresolved_identity: int = 0
    skipped_delete_operation: int = 0
    records_seen: int = 0

    def merged(self, other: "ReplayStats") -> "ReplayStats":
        return ReplayStats(
            unit_events_inserted=self.unit_events_inserted + other.unit_events_inserted,
            deal_events_inserted=self.deal_events_inserted + other.deal_events_inserted,
            skipped_partial=self.skipped_partial + other.skipped_partial,
            skipped_unresolved_identity=self.skipped_unresolved_identity + other.skipped_unresolved_identity,
            skipped_delete_operation=self.skipped_delete_operation + other.skipped_delete_operation,
            records_seen=self.records_seen + other.records_seen,
        )


async def _distinct_source_instance_ids(session: AsyncSession) -> list[str]:
    rows = (
        await session.execute(
            sa.select(sa.distinct(sync_payloads.c.payload["source_instance_id"].astext))
        )
    ).scalars().all()
    return sorted(value for value in rows if value)


async def _load_identity_maps(
    session: AsyncSession, source_instance_id: str
) -> tuple[dict[str, uuid.UUID], dict[str, uuid.UUID], dict[uuid.UUID, uuid.UUID]]:
    """external_unit_id -> unit id; external_deal_id -> deal id; deal id -> unit id.

    Nạp một lần cho cả source_instance_id, KHÔNG tra từng bản ghi — hàng nghìn
    bản ghi tra DB từng dòng là chi phí không cần thiết khi danh tính không đổi
    trong lúc phát lại.
    """
    unit_rows = (
        await session.execute(
            sa.select(units.c.external_unit_id, units.c.id).where(
                units.c.source_instance_id == source_instance_id
            )
        )
    ).all()
    unit_map = {external_id: unit_id for external_id, unit_id in unit_rows}

    deal_rows = (
        await session.execute(
            sa.select(deals.c.external_deal_id, deals.c.id, deals.c.unit_id).where(
                deals.c.source_instance_id == source_instance_id
            )
        )
    ).all()
    deal_map = {external_id: deal_id for external_id, deal_id, _ in deal_rows}
    deal_unit_map = {deal_id: unit_id for _, deal_id, unit_id in deal_rows}
    return unit_map, deal_map, deal_unit_map


async def _ordered_payloads(session: AsyncSession, source_instance_id: str):
    """Payload của một source_instance_id, đúng thứ tự `(received_at, id)` —
    thứ tự DUY NHẤT khiến "trạng thái thấy lần cuối" có nghĩa qua nhiều lượt
    đồng bộ incremental."""
    rows = await session.execute(
        sa.select(sync_payloads.c.id, sync_payloads.c.payload, sync_payloads.c.received_at)
        .where(sync_payloads.c.payload["source_instance_id"].astext == source_instance_id)
        .order_by(sync_payloads.c.received_at, sync_payloads.c.id)
    )
    return rows.all()


def _resolve_changed_at(source_updated_at: str | None, received_at: datetime) -> tuple[datetime, str]:
    """Mốc thời gian NGHIỆP VỤ khi hệ nguồn cho biết; mốc PHÍA NHẬN khi không.

    `time_basis` được ghi vào metadata để tầng đọc sau này biết độ tin cậy của
    mốc: 'source_updated_at' là thời gian THẬT của sự kiện; 'received_at' là
    thời gian đồng bộ, có thể trễ hơn sự kiện thật một khoảng bất kỳ.
    """
    if source_updated_at:
        try:
            return datetime.fromisoformat(source_updated_at), "source_updated_at"
        except ValueError:
            pass
    return received_at, "received_at"


def _unit_event(
    *, unit_id: uuid.UUID, old_status: str | None, new_status: str, changed_at: datetime,
    time_basis: str, payload_id: uuid.UUID,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"payload_id": str(payload_id), "time_basis": time_basis}
    if old_status is None:
        metadata["boundary"] = "left_censored"
    return {
        "id": uuid.uuid4(),
        "unit_id": unit_id,
        "deal_id": None,
        "old_status": old_status,
        "new_status": new_status,
        "changed_at": changed_at,
        "source": SOURCE,
        "metadata_json": metadata,
    }


def _deal_event(
    *, deal_id: uuid.UUID, unit_id: uuid.UUID, old_status: str | None, new_status: str,
    changed_at: datetime, time_basis: str, payload_id: uuid.UUID,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"payload_id": str(payload_id), "time_basis": time_basis}
    if old_status is None:
        metadata["boundary"] = "left_censored"
    holding = {"reserved", "sold"}
    return {
        "id": uuid.uuid4(),
        "deal_id": deal_id,
        "unit_id": unit_id,
        "old_status": old_status,
        "new_status": new_status,
        "prior_status_was_holding": old_status in holding,
        "new_status_is_holding": new_status in holding,
        "changed_at": changed_at,
        "source": SOURCE,
        "metadata_json": metadata,
    }


async def _flush_units(session: AsyncSession, rows: list[dict[str, Any]], *, dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        return len(rows)
    # `.returning(id)` thay vì đọc `result.rowcount`: driver asyncpg không đáng
    # tin cho rowcount của một INSERT nhiều dòng qua executemany (có thể trả
    # -1/không xác định). Postgres chỉ RETURNING đúng những dòng THỰC SỰ được
    # chèn — ON CONFLICT DO NOTHING loại các dòng trùng khỏi tập trả về, nên
    # đếm số dòng trả về là con số chính xác duy nhất, không phụ thuộc driver.
    stmt = pg_insert(unit_status_history).on_conflict_do_nothing(
        index_elements=["unit_id", "changed_at", "new_status"],
        index_where=sa.text("source = 'backfill_replay'"),
    ).returning(unit_status_history.c.id)
    result = await session.execute(stmt, rows)
    return len(result.all())


async def _flush_deals(session: AsyncSession, rows: list[dict[str, Any]], *, dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        return len(rows)
    stmt = pg_insert(deal_status_history).on_conflict_do_nothing(
        index_elements=["deal_id", "changed_at", "new_status"],
        index_where=sa.text("source = 'backfill_replay'"),
    ).returning(deal_status_history.c.id)
    result = await session.execute(stmt, rows)
    return len(result.all())


async def replay_source_instance(
    session: AsyncSession, source_instance_id: str, *, dry_run: bool, batch_size: int
) -> ReplayStats:
    stats = ReplayStats()
    unit_map, deal_map, deal_unit_map = await _load_identity_maps(session, source_instance_id)

    last_unit_status: dict[uuid.UUID, str] = {}
    last_deal_status: dict[uuid.UUID, str] = {}
    pending_units: list[dict[str, Any]] = []
    pending_deals: list[dict[str, Any]] = []

    async def flush() -> None:
        stats.unit_events_inserted += await _flush_units(session, pending_units, dry_run=dry_run)
        stats.deal_events_inserted += await _flush_deals(session, pending_deals, dry_run=dry_run)
        pending_units.clear()
        pending_deals.clear()

    for payload_id, payload, received_at in await _ordered_payloads(session, source_instance_id):
        for record in payload.get("records", []):
            stats.records_seen += 1
            entity = record.get("entity")
            if entity not in ("units", "deals"):
                continue
            if record.get("operation") == "delete":
                # Tombstone — xoá mềm phía nhận, KHÔNG phải một chuyển trạng
                # thái. Không tự suy ra status mới từ một thao tác xoá.
                stats.skipped_delete_operation += 1
                continue

            completeness = record.get("payload_completeness", "full")
            body = record.get("payload") or {}
            external_id = record.get("external_id")
            changed_at, time_basis = _resolve_changed_at(record.get("source_updated_at"), received_at)

            if entity == "units":
                new_status = body.get("unit_status")
                if new_status is None:
                    if completeness == "partial":
                        stats.skipped_partial += 1
                        log.warning(
                            "backfill.unit.partial_missing_status",
                            source_instance_id=source_instance_id,
                            external_id=external_id,
                            payload_id=str(payload_id),
                        )
                    continue
                if new_status not in UNIT_STATUSES:
                    continue
                unit_id = unit_map.get(external_id)
                if unit_id is None:
                    stats.skipped_unresolved_identity += 1
                    continue
                old_status = last_unit_status.get(unit_id)
                if old_status == new_status:
                    continue  # không phải một sự kiện — ck_ush_actual_change sẽ từ chối nó
                pending_units.append(
                    _unit_event(
                        unit_id=unit_id, old_status=old_status, new_status=new_status,
                        changed_at=changed_at, time_basis=time_basis, payload_id=payload_id,
                    )
                )
                last_unit_status[unit_id] = new_status
            else:  # entity == "deals"
                raw_status = body.get("deal_status")
                if raw_status is None:
                    if completeness == "partial":
                        stats.skipped_partial += 1
                        log.warning(
                            "backfill.deal.partial_missing_status",
                            source_instance_id=source_instance_id,
                            external_id=external_id,
                            payload_id=str(payload_id),
                        )
                    continue
                new_status = DEAL_STATUS_ALIASES.get(raw_status.lower(), raw_status.lower())
                if new_status not in DEAL_STATUSES:
                    continue
                deal_id = deal_map.get(external_id)
                if deal_id is None:
                    stats.skipped_unresolved_identity += 1
                    continue
                unit_id = deal_unit_map.get(deal_id)
                if unit_id is None:
                    stats.skipped_unresolved_identity += 1
                    continue
                old_status = last_deal_status.get(deal_id)
                if old_status == new_status:
                    continue
                pending_deals.append(
                    _deal_event(
                        deal_id=deal_id, unit_id=unit_id, old_status=old_status, new_status=new_status,
                        changed_at=changed_at, time_basis=time_basis, payload_id=payload_id,
                    )
                )
                last_deal_status[deal_id] = new_status

            if len(pending_units) + len(pending_deals) >= batch_size:
                await flush()

    await flush()
    return stats


async def run(*, dry_run: bool, source_instance_id: str | None, batch_size: int) -> ReplayStats:
    session_factory = get_session_factory()
    total = ReplayStats()
    async with session_factory() as session:
        floor_row = await session.execute(sa.select(sa.func.min(sync_payloads.c.received_at)))
        floor_ts = floor_row.scalar()
        if floor_ts is None:
            log.warning("backfill.no_payloads")
            return total

        targets = [source_instance_id] if source_instance_id else await _distinct_source_instance_ids(session)
        for instance_id in targets:
            log.info("backfill.instance.start", source_instance_id=instance_id, dry_run=dry_run)
            # `dry_run` không cần rollback thủ công: `_flush_units`/`_flush_deals`
            # đã bỏ qua INSERT hoàn toàn khi dry_run=True (chỉ đếm), nên
            # transaction ở đây không bao giờ mang gì để phải cuộn lại.
            async with session.begin():
                instance_stats = await replay_source_instance(
                    session, instance_id, dry_run=dry_run, batch_size=batch_size
                )
            total = total.merged(instance_stats)
            log.info(
                "backfill.instance.done",
                source_instance_id=instance_id,
                unit_events_inserted=instance_stats.unit_events_inserted,
                deal_events_inserted=instance_stats.deal_events_inserted,
                skipped_partial=instance_stats.skipped_partial,
                skipped_unresolved_identity=instance_stats.skipped_unresolved_identity,
                skipped_delete_operation=instance_stats.skipped_delete_operation,
            )
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="chỉ đếm, không ghi (rollback cuối transaction)")
    parser.add_argument("--source-instance-id", default=None, help="chỉ phát lại một source_instance_id")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="số sự kiện mỗi lần flush")
    args = parser.parse_args(argv)

    async def _once() -> ReplayStats:
        try:
            return await run(
                dry_run=args.dry_run, source_instance_id=args.source_instance_id, batch_size=args.batch_size
            )
        finally:
            await get_engine().dispose()

    stats = asyncio.run(_once())
    log.info(
        "backfill.total",
        dry_run=args.dry_run,
        records_seen=stats.records_seen,
        unit_events_inserted=stats.unit_events_inserted,
        deal_events_inserted=stats.deal_events_inserted,
        skipped_partial=stats.skipped_partial,
        skipped_unresolved_identity=stats.skipped_unresolved_identity,
        skipped_delete_operation=stats.skipped_delete_operation,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
