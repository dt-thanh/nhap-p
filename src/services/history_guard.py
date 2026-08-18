"""Chốt A4: một trường VẮNG MẶT không được phép âm thầm xoá lịch sử.

## Vấn đề

Giả định A4 của hợp đồng nói: khi CRM chuyển một giao dịch `reserved → sold`,
payload phải VẪN mang `reserved_at` cũ. Nếu không, ngày đặt cọc mất vĩnh viễn và
đường cong hấp thụ theo lịch sử không dựng lại được.

Trước Phase 8B, việc đó xảy ra **im lặng**: `_optional_timestamp` đọc
`data.get(key)` nên vắng mặt và null tường minh cùng ra `None`, còn `_upsert` ghi
đè MỌI cột đã ánh xạ qua `ON CONFLICT DO UPDATE`. Một payload `sold` quên
`reserved_at` sẽ ghi `NULL` đè lên ngày đặt cọc đang có, không lỗi, không cảnh báo.

## Ba trạng thái của một trường

    VẮNG MẶT       khoá không có trong payload      → ý nghĩa phụ thuộc completeness
    NULL TƯỜNG MINH khoá có, giá trị null           → hệ nguồn khẳng định "không còn"
    CÓ GIÁ TRỊ     khoá có, giá trị khác null       → đặt bằng giá trị này

Vắng mặt KHÔNG phải một khẳng định. Một hệ nguồn không theo dõi trường đó và một
hệ nguồn vừa xoá nó tạo ra payload GIỐNG HỆT NHAU. Chỉ null tường minh mới phân
biệt được, nên hợp đồng phải chở được nó — và bên gửi phải khai mình đang gửi
kiểu gì (`payload_completeness`).

## Quy tắc

| completeness | trạng thái trường | bản sao đang có | kết quả |
|---|---|---|---|
| full | vắng mặt | CÓ giá trị | **TỪ CHỐI `HISTORY_TIMESTAMP_DROPPED`** |
| full | vắng mặt | trống | nhận, vẫn trống (không mất gì) |
| full | null | bất kỳ | xoá có chủ đích, ghi log `warning` |
| partial | vắng mặt | bất kỳ | **GIỮ NGUYÊN** giá trị cũ |
| partial | null | bất kỳ | xoá |
| cả hai | null trên trường KHÔNG nullable | — | từ chối `NULL_NOT_ALLOWED` |
| partial | không có dòng bản sao nào | — | từ chối `PARTIAL_UPDATE_WITHOUT_BASE` |

**Từ chối chứ không âm thầm giữ lại.** Tự động chép giá trị cũ sang sẽ BỊA ra
lịch sử trong trường hợp CRM cố ý xoá nó, và tệ hơn: nó che đúng cái giới hạn của
CRM mà A4 sinh ra để phơi bày. A4 là giả định CHẶN; hỏng thì phải hỏng to.

**Null tường minh được chấp nhận.** Đó là một khẳng định của hệ nguồn. Từ chối nó
thì một lần đặt cọc nhập nhầm sẽ không bao giờ sửa được, và hệ nguồn sẽ xoay sang
xoá-rồi-tạo-lại — phá luôn tính bền vững của `external_id` (giả định A1).

## Thứ tự chạy

    đọc bản sao → hợp nhất (merge) → BĂM → quyết định phiên bản → CHỐT → chiếu

Hợp nhất phải chạy TRƯỚC khi băm: dấu vân của một bản ghi partial phải mô tả
trạng thái CUỐI CÙNG, nếu không thì hai hệ nguồn diễn đạt cùng một kết quả (một
bên gửi đủ, một bên gửi phần đổi) sẽ ra hai dấu vân khác nhau và sinh đụng độ giả.

Chốt phải chạy SAU quyết định phiên bản: một bản ghi CŨ (`skip_stale`) hay một bản
trùng (`duplicate_noop`) vốn đã không được ghi, từ chối nó vì đánh rơi lịch sử là
báo lỗi cho một thao tác không hề xảy ra. Trên thực tế chốt chỉ đụng tới `update`
và `update` làm sống lại bản ghi đã tombstone — `insert` thì chưa có gì để mất.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.models.tables import areas, deals, projects, units
from src.services.domain_projection import ProjectionError
from src.services.json_payload import COMPLETENESS_PARTIAL, SourceRecord, payload_fingerprint

log = get_logger("src.services.history_guard")

# Mốc lịch sử được chốt bảo vệ. Ba mốc, đối xứng — không mốc nào là trường hợp
# đặc biệt, vì mất `lost_at` cũng làm hỏng lịch sử y như mất `reserved_at`.
HISTORY_FIELDS: dict[str, tuple[str, ...]] = {
    "deals": ("reserved_at", "sold_at", "lost_at"),
    "units": (),
}

# Trường đã ánh xạ mà cột đích KHÔNG nhận NULL. Null tường minh ở đây là vô nghĩa
# chứ không phải "xoá": không có dòng nào tồn tại được mà thiếu chúng.
#
# `area_id` có mặt từ Phase 1: hợp đồng v1 chặn `{"area_id": null}` ngay ở JSON
# Schema, nhưng phương ngữ S2 cũ đi thẳng xuống tầng này mà không qua schema —
# nên đây là chốt thứ hai cho đúng cái đường vòng đó.
NON_NULLABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "units": ("area_id", "area_name", "unit_type", "unit_code", "status"),
    "deals": ("external_unit_id", "status"),
}

# Trường ánh xạ → cột giữ giá trị đó ở bản sao. Chỉ những trường THỰC SỰ chép
# ngược lại được mới có mặt.
#
# `area_id` của `units` CỐ Ý KHÔNG nằm ở đây, và đây không phải chuyện bỏ sót.
# `merge_record()` chép mọi khoá của bản sao mà bản ghi partial không mang. Nếu
# bản sao trả `area_id`, một bản ghi partial chuyển phân khu bằng
# `area_name`/`unit_type` sẽ được hợp nhất với `area_id` CŨ — mà `area_id` lại
# thắng ở `DomainProjector._resolve_area` — nên lệnh chuyển biến mất KHÔNG một
# lỗi nào. Đừng thêm nó vào.
#
# `deals.status` lấy từ cột `source_status` chứ không phải `status`: phong bì nội
# bộ mang trạng thái NGUYÊN VĂN của hệ nguồn, còn `deals.status` là bản đã chuẩn
# hoá. Chép nhầm cột sẽ ghi giá trị chuẩn hoá đè lên chữ gốc và mất luôn vết.
_DEAL_MIRROR_COLUMNS = {
    "external_unit_id": None,  # xử lý riêng: phải join sang `units`
    "status": "source_status",
    "reserved_at": "reserved_at",
    "sold_at": "sold_at",
    "lost_at": "lost_at",
}


@dataclass(frozen=True, slots=True)
class MirrorSnapshot:
    """Giá trị các trường đã ánh xạ mà bản sao ĐANG giữ, đọc trước khi ghi.

    `exists=False` nghĩa là chưa từng có dòng nào cho danh tính này — khác hẳn
    với "có dòng nhưng mọi trường đều null", và hai thứ đó dẫn tới hai quyết định
    khác nhau ở chế độ partial.
    """

    entity: str
    exists: bool = False
    tombstoned: bool = False
    values: dict[str, Any] = field(default_factory=dict)

    def stored(self, key: str) -> Any:
        return self.values.get(key)

    def has_value(self, key: str) -> bool:
        return self.values.get(key) is not None


async def read_mirror(
    session: AsyncSession, *, entity: str, source_instance_id: str, source_record_id: str
) -> MirrorSnapshot:
    """Đọc trạng thái hiện tại của bản sao cho một danh tính nguồn.

    KHÔNG lọc `deleted_at`: một bản ghi đã tombstone vẫn giữ nguyên giá trị của
    nó — xoá mềm tồn tại chính vì thế. Bản đến làm nó sống lại mà đánh rơi lịch sử
    thì vẫn là mất lịch sử.
    """
    if entity == "units":
        return await _read_unit_mirror(session, source_instance_id, source_record_id)
    if entity == "deals":
        return await _read_deal_mirror(session, source_instance_id, source_record_id)
    if entity == "projects":
        return await _read_project_mirror(session, source_instance_id, source_record_id)
    if entity == "areas":
        return await _read_area_mirror(session, source_instance_id, source_record_id)
    return MirrorSnapshot(entity=entity)


async def _read_unit_mirror(session: AsyncSession, instance: str, external_unit_id: str) -> MirrorSnapshot:
    row = (
        (
            await session.execute(
                sa.select(
                    units.c.unit_code,
                    units.c.unit_type,
                    units.c.status,
                    units.c.deleted_at,
                    areas.c.area_name,
                )
                .select_from(units.join(areas, units.c.area_id == areas.c.id))
                .where(
                    units.c.source_instance_id == instance,
                    units.c.external_unit_id == external_unit_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return MirrorSnapshot(entity="units")
    return MirrorSnapshot(
        entity="units",
        exists=True,
        tombstoned=row["deleted_at"] is not None,
        values={
            "area_name": row["area_name"],
            "unit_type": row["unit_type"],
            "unit_code": row["unit_code"],
            "status": row["status"],
        },
    )


async def _read_deal_mirror(session: AsyncSession, instance: str, external_deal_id: str) -> MirrorSnapshot:
    row = (
        (
            await session.execute(
                sa.select(
                    deals.c.source_status,
                    deals.c.reserved_at,
                    deals.c.sold_at,
                    deals.c.lost_at,
                    deals.c.deleted_at,
                    units.c.external_unit_id,
                )
                .select_from(deals.join(units, deals.c.unit_id == units.c.id))
                .where(
                    deals.c.source_instance_id == instance,
                    deals.c.external_deal_id == external_deal_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return MirrorSnapshot(entity="deals")
    return MirrorSnapshot(
        entity="deals",
        exists=True,
        tombstoned=row["deleted_at"] is not None,
        values={
            "external_unit_id": row["external_unit_id"],
            "status": row["source_status"],
            "reserved_at": row["reserved_at"],
            "sold_at": row["sold_at"],
            "lost_at": row["lost_at"],
        },
    )


async def _read_project_mirror(session: AsyncSession, instance: str, external_project_id: str) -> MirrorSnapshot:
    """Phase D. Không có mốc lịch sử nào cần bảo vệ ở đây (`HISTORY_FIELDS` không
    có 'projects') — hai trường `name`/`launch_date` đều BẮT BUỘC trên một bản
    ghi `full` (chốt ở `contract_v2`/`DomainProjector._project_project`), nên
    không có chỗ nào để đánh rơi. Mirror này CHỈ phục vụ hợp nhất bản ghi
    `partial` — xem `merge_record`.
    """
    row = (
        (
            await session.execute(
                sa.select(projects.c.name, projects.c.launch_date, projects.c.status).where(
                    projects.c.source_instance_id == instance,
                    projects.c.external_id == external_project_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return MirrorSnapshot(entity="projects")
    return MirrorSnapshot(
        entity="projects",
        exists=True,
        tombstoned=row["status"] == "archived",
        values={"name": row["name"], "launch_date": row["launch_date"]},
    )


async def _read_area_mirror(session: AsyncSession, instance: str, external_area_id: str) -> MirrorSnapshot:
    """Phase D — cùng lý do với `_read_project_mirror`. Cả năm trường của phân
    khu đều BẮT BUỘC trên một bản ghi `full`, nên mirror này chỉ phục vụ hợp
    nhất `partial`."""
    row = (
        (
            await session.execute(
                sa.select(
                    areas.c.area_name,
                    areas.c.unit_type,
                    areas.c.bedrooms,
                    areas.c.area_sqm,
                    areas.c.total_units,
                    areas.c.status,
                ).where(
                    areas.c.source_instance_id == instance,
                    areas.c.external_id == external_area_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return MirrorSnapshot(entity="areas")
    return MirrorSnapshot(
        entity="areas",
        exists=True,
        tombstoned=row["status"] == "archived",
        values={
            "area_name": row["area_name"],
            "unit_type": row["unit_type"],
            "bedrooms": row["bedrooms"],
            "area_sqm": row["area_sqm"],
            "total_units": row["total_units"],
        },
    )


def merge_record(record: SourceRecord, mirror: MirrorSnapshot, *, entity: str) -> SourceRecord:
    """Hợp nhất bản ghi partial với bản sao, rồi BĂM LẠI theo trạng thái cuối.

    Bản ghi `full` đi qua không đổi: dữ liệu nó mang ĐÃ là trạng thái cuối cùng,
    và băm lại sẽ làm đổi mọi dấu vân đã lưu mà chẳng được gì.

    Raises:
        ProjectionError: partial mà chưa có dòng bản sao nào, hoặc null tường minh
            trên một trường không nhận NULL.
    """
    _reject_null_on_non_nullable(record, entity)

    if record.payload_completeness != COMPLETENESS_PARTIAL:
        return record

    if not mirror.exists:
        # Không có gì để vá. Bịa nốt phần thiếu chính là loại sai lầm mà cả hợp
        # đồng này được viết ra để tránh.
        raise ProjectionError(
            "PARTIAL_UPDATE_WITHOUT_BASE",
            "Bản ghi partial nhưng bản sao chưa có bản ghi nào cho danh tính này. "
            "Gửi một bản ghi đầy đủ (payload_completeness='full') trước.",
            json_path=record.json_path,
            source_record_id=record.source_record_id,
        )

    merged = dict(record.data)
    carried = []
    for key, stored in mirror.values.items():
        if key not in merged:
            merged[key] = stored
            carried.append(key)

    if not carried:
        return record

    log.info(
        "sync.partial_merge",
        source_record_id=record.source_record_id,
        entity=entity,
        carried=carried,
    )
    return SourceRecord(
        source_record_id=record.source_record_id,
        operation=record.operation,
        json_path=record.json_path,
        # Băm lại theo TRẠNG THÁI CUỐI: nếu không, cùng một kết quả gửi bằng hai
        # cách sẽ ra hai dấu vân và sinh đụng độ giả ở cùng phiên bản.
        payload_hash=payload_fingerprint(record.operation, merged, entity=entity),
        source_updated_at=record.source_updated_at,
        source_revision=record.source_revision,
        data=merged,
        payload_completeness=record.payload_completeness,
    )


def guard_history(
    record: SourceRecord,
    mirror: MirrorSnapshot,
    *,
    entity: str,
    decision: str,
    preserve: bool = False,
) -> SourceRecord:
    """Chặn (hoặc, ở chế độ thoát hiểm, vá) việc đánh rơi mốc lịch sử.

    Chỉ chạy với quyết định THỰC SỰ GHI. `skip_stale`/`duplicate_noop`/`conflict`
    không chạm bản sao nên không có gì để mất; `insert` thì chưa có gì tồn tại.

    Args:
        decision: quyết định của `SourceIdentityService`.
        preserve: chế độ thoát hiểm `SYNC_PRESERVE_DROPPED_TIMESTAMPS`. Bật thì
            chép giá trị cũ sang thay vì từ chối. Đây là một quyết định được ghi
            nhận, không phải mặc định — mọi con số tính ra dưới cờ này là xấp xỉ.

    Raises:
        ProjectionError: HISTORY_TIMESTAMP_DROPPED.
    """
    fields = HISTORY_FIELDS.get(entity, ())
    if not fields or decision != "update" or not mirror.exists:
        return record

    # Bản ghi partial coi vắng mặt là "giữ nguyên", và `merge_record` đã chép giá
    # trị cũ vào rồi — không còn gì để đánh rơi.
    if record.payload_completeness == COMPLETENESS_PARTIAL:
        return record

    dropped = [key for key in fields if key not in record.data and mirror.has_value(key)]
    if not dropped:
        return record

    if not preserve:
        key = dropped[0]
        raise ProjectionError(
            "HISTORY_TIMESTAMP_DROPPED",
            f"Bản ghi đầy đủ không mang '{key}' trong khi bản sao đang giữ một giá trị "
            f"({_readable(mirror.stored(key))}). Vắng mặt không phải lệnh xoá: gửi lại giá trị cũ, "
            f"hoặc gửi '{key}': null nếu thực sự muốn xoá.",
            # Trỏ vào ĐỐI TƯỢNG payload chứ không phải vào trường: một trường vắng
            # mặt không có đường dẫn nào trong tài liệu, và chỉ vào một đường dẫn
            # không tồn tại sẽ khiến người tích hợp đi tìm thứ chưa từng ở đó.
            json_path=f"{record.json_path}.data",
            source_record_id=record.source_record_id,
            field_name=key,
        )

    # Chế độ thoát hiểm. Dấu vân KHÔNG được tính lại: nó mô tả thứ hệ nguồn đã
    # gửi, và giữ nguyên nó là điều kiện để lần gửi lại y hệt vẫn là
    # `duplicate_noop`. Cái giá: bản sao giữ một giá trị mà dấu vân không mô tả —
    # thêm một lý do nữa để cờ này phải làm hỏng cổng cắt sang.
    patched = dict(record.data)
    for key in dropped:
        patched[key] = mirror.stored(key)
    log.warning(
        "sync.history_timestamp_preserved",
        source_record_id=record.source_record_id,
        entity=entity,
        fields=dropped,
        note="SYNC_PRESERVE_DROPPED_TIMESTAMPS đang BẬT — số liệu hấp thụ là xấp xỉ",
    )
    return SourceRecord(
        source_record_id=record.source_record_id,
        operation=record.operation,
        json_path=record.json_path,
        payload_hash=record.payload_hash,
        source_updated_at=record.source_updated_at,
        source_revision=record.source_revision,
        data=patched,
        payload_completeness=record.payload_completeness,
    )


def log_explicit_clears(record: SourceRecord, mirror: MirrorSnapshot, *, entity: str, decision: str) -> list[str]:
    """Ghi lại những mốc bị xoá CÓ CHỦ ĐÍCH bằng null tường minh.

    Không phải lỗi — hệ nguồn có quyền sửa sai. Nhưng nó xoá dữ liệu lịch sử, nên
    phải để lại vết ở mức `warning`: không có vết thì một CRM hỏng đang phát null
    hàng loạt sẽ bào mòn lịch sử mà không ai thấy gì.

    Returns:
        Tên các trường vừa bị xoá — phía gọi đếm vào tổng kết của lô.
    """
    fields = HISTORY_FIELDS.get(entity, ())
    if not fields or decision != "update" or not mirror.exists:
        return []

    cleared = [key for key in fields if key in record.data and record.data[key] is None and mirror.has_value(key)]
    if cleared:
        log.warning(
            "sync.history_timestamp_cleared",
            source_record_id=record.source_record_id,
            entity=entity,
            fields=cleared,
        )
    return cleared


def _reject_null_on_non_nullable(record: SourceRecord, entity: str) -> None:
    for key in NON_NULLABLE_FIELDS.get(entity, ()):
        if key in record.data and record.data[key] is None:
            raise ProjectionError(
                "NULL_NOT_ALLOWED",
                f"Trường '{key}' không nhận giá trị null. null nghĩa là 'xoá giá trị', "
                f"mà không dòng nào tồn tại được khi thiếu trường này.",
                json_path=f"{record.json_path}.data.{key}",
                source_record_id=record.source_record_id,
                field_name=key,
                error_category="field",
            )


def _readable(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)
