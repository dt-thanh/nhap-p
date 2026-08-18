"""Chiếu bản ghi nguồn đã được chấp nhận vào `units` / `deals`.

Tầng này nằm SAU `SourceIdentityService` và cố ý không lặp lại logic phiên bản
của nó. Phân công rõ ràng:

* `SourceIdentityService` trả lời "bản đến này có được chấp nhận không" —
  insert / update / skip_stale / duplicate_noop / conflict / tombstone.
* `DomainProjector` trả lời "nếu được chấp nhận thì bảng nghiệp vụ đổi thế nào".

Nhờ vậy chỉ có MỘT chỗ so phiên bản. Quyết định `skip_stale`, `duplicate_noop` và
`conflict` **không chạm** vào bảng nghiệp vụ: bản cũ không được ghi đè, nạp lại y
hệt không tốn một lệnh ghi nào, và đụng độ thì trạng thái đã chấp nhận giữ nguyên.

Đồng bộ MỘT CHIỀU: mọi trường ở đây do CRM sở hữu. Không có đường nào trong ứng
dụng ghi vào chúng ngoài tầng chiếu này.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.models.tables import areas, deals, projects, units
from src.services.json_payload import SourceRecord, redact
from src.services.source_identity import DecisionResult

log = get_logger("src.services.domain_projection")

# Thực thể → bảng đích. Thực thể ngoài bảng này không được chiếu; `JsonPayloadParser`
# đã chặn từ phong bì, đây là chốt thứ hai để một entity mới không lặng lẽ rơi vào
# bảng sai.
#
# Phase D: THÊM "projects"/"areas" — Mini CRM là nguồn sự thật cho CẢ BỐN tầng.
ENTITY_TABLES = {"units": "units", "deals": "deals", "projects": "projects", "areas": "areas"}

# Trần số con còn sống liệt kê trong một lỗi PARENT_HAS_LIVE_CHILDREN — cùng chốt
# với hệ nguồn (`_MAX_LISTED_CHILDREN` phía đó), giữ đối xứng hai phía của cùng
# một hợp đồng.
_MAX_LISTED_CHILDREN = 20

UNIT_STATUSES = frozenset({"available", "reserved", "sold", "blocked"})

# Trạng thái giao dịch chuẩn của repo (SRS §5.2.8).
DEAL_STATUSES = frozenset({"lead", "qualified", "interested", "viewing", "reserved", "sold", "lost"})

# Ánh xạ tên trạng thái của hệ nguồn về tên chuẩn. CHỈ những alias được liệt kê ở
# đây mới được nhận; mọi giá trị lạ khác bị TỪ CHỐI chứ không quy về một mặc định.
#
# `cancelled`: repo không có trạng thái này (SRS §5.2.8). Về mặt hấp thụ nó giống
# hệt `lost` — căn quay lại quỹ hàng và không tính là đã bán — nên ánh xạ về `lost`
# và giữ nguyên chữ gốc ở `deals.source_status` để còn truy được.
DEAL_STATUS_ALIASES = {"cancelled": "lost", "canceled": "lost"}

# Trạng thái GIỮ căn — khớp partial unique `uq_deals_active_per_unit`.
HOLDING_STATUSES = frozenset({"reserved", "sold"})


class ProjectionError(Exception):
    """Bản ghi được chấp nhận về phiên bản nhưng nội dung không chiếu được.

    Mang sẵn ngữ cảnh để ghi thẳng vào `upload_errors` (0006): mã lỗi, đường dẫn
    JSON, mã bản ghi nguồn.
    """

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        json_path: str,
        source_record_id: str,
        field_name: str | None = None,
        raw_value_redacted: str | None = None,
        error_category: str = "business",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.json_path = json_path
        self.source_record_id = source_record_id
        self.field_name = field_name
        self.raw_value_redacted = raw_value_redacted
        self.error_category = error_category


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """Bảng nghiệp vụ đã đổi thế nào sau một bản ghi nguồn."""

    table: str
    action: str  # inserted | updated | tombstoned | untouched
    row_id: uuid.UUID | None = None


def _require_text(data: dict[str, Any], key: str, record: SourceRecord) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(
            "MISSING_FIELD",
            f"Trường '{key}' bắt buộc và phải là chuỗi khác rỗng",
            json_path=f"{record.json_path}.data.{key}",
            source_record_id=record.source_record_id,
            field_name=key,
            error_category="field",
        )
    return value.strip()


def _require_date(data: dict[str, Any], key: str, record: SourceRecord):
    """`launch_date` là NGÀY LỊCH (không múi giờ) — khác `_optional_timestamp`.

    Chấp nhận cả `date` (đã parse) lẫn chuỗi `YYYY-MM-DD`. Một chuỗi mang offset
    múi giờ (`2026-06-01T00:00:00+07:00`) bị TỪ CHỐI: `date.fromisoformat` không
    đọc được dạng đó, và đó là điều muốn — `launch_date` là sự kiện thương mại
    theo lịch địa phương, gắn offset vào sẽ khiến cùng một ngày hiển thị lệch
    nhau ở hai múi giờ (§5.1 sync_contract_v2_draft.md).
    """
    from datetime import date as _date

    raw = data.get(key)
    if isinstance(raw, _date):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return _date.fromisoformat(raw.strip())
        except ValueError:
            pass
    raise ProjectionError(
        "MISSING_FIELD" if raw is None else "INVALID_DATE",
        f"Trường '{key}' bắt buộc và phải là ngày dạng YYYY-MM-DD",
        json_path=f"{record.json_path}.data.{key}",
        source_record_id=record.source_record_id,
        field_name=key,
        error_category="field",
    )


def _optional_timestamp(data: dict[str, Any], key: str, record: SourceRecord) -> datetime | None:
    raw = data.get(key)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        parsed = raw
    else:
        try:
            parsed = datetime.fromisoformat(str(raw).strip())
        except ValueError as exc:
            raise ProjectionError(
                "INVALID_TIMESTAMP",
                f"Trường '{key}' không phải mốc ISO-8601",
                json_path=f"{record.json_path}.data.{key}",
                source_record_id=record.source_record_id,
                field_name=key,
                error_category="field",
            ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ProjectionError(
            "INVALID_TIMESTAMP",
            f"Trường '{key}' thiếu múi giờ",
            json_path=f"{record.json_path}.data.{key}",
            source_record_id=record.source_record_id,
            field_name=key,
            error_category="field",
        )
    return parsed


def normalize_deal_status(raw: Any, record: SourceRecord) -> tuple[str, str]:
    """Trả `(trạng thái chuẩn, trạng thái nguyên văn)`.

    Không có mặc định. Một giá trị lạ quy về `reserved`/`available`/`sold` sẽ âm
    thầm làm sai mọi con số hấp thụ phía sau, nên ở đây nó bị từ chối thẳng.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ProjectionError(
            "MISSING_FIELD",
            "Trường 'status' bắt buộc và phải là chuỗi khác rỗng",
            json_path=f"{record.json_path}.data.status",
            source_record_id=record.source_record_id,
            field_name="status",
            error_category="field",
        )

    source_status = raw.strip()
    canonical = DEAL_STATUS_ALIASES.get(source_status.lower(), source_status.lower())
    if canonical not in DEAL_STATUSES:
        raise ProjectionError(
            "UNKNOWN_DEAL_STATUS",
            f"Trạng thái giao dịch '{source_status}' không nằm trong tập cho phép: {', '.join(sorted(DEAL_STATUSES))}",
            json_path=f"{record.json_path}.data.status",
            source_record_id=record.source_record_id,
            field_name="status",
            raw_value_redacted=redact(source_status, "status"),
        )
    return canonical, source_status


class DomainProjector:
    """Ghi bản ghi nguồn đã chấp nhận vào `units` / `deals`.

    Nhận `AsyncSession` từ bên gọi: việc chiếu phải nằm cùng transaction với quyết
    định danh tính, nếu không thì `crm_source_records` có thể nói "đã chấp nhận"
    trong khi bảng nghiệp vụ chưa đổi.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def project(
        self,
        record: SourceRecord,
        outcome: DecisionResult,
        *,
        source_entity: str,
        source_system: str,
        source_instance_id: str,
        project_id: uuid.UUID | None,
        now: datetime,
    ) -> ProjectionResult:
        """Áp quyết định của `SourceIdentityService` lên bảng nghiệp vụ.

        `project_id` là `None` ĐÚNG MỘT trường hợp: `source_entity == "projects"`
        và dự án đang được TẠO trong chính lô này (chưa có UUID nào tồn tại trước
        đó) — hai nhánh `projects` dưới đây không dùng tham số này.
        """
        table = ENTITY_TABLES.get(source_entity)
        if table is None:
            raise ProjectionError(
                "UNSUPPORTED_ENTITY_MAPPING",
                f"Không có bảng nghiệp vụ nào nhận thực thể '{source_entity}'",
                json_path=record.json_path,
                source_record_id=record.source_record_id,
                error_category="schema",
            )

        # Ba quyết định này KHÔNG chạm bảng nghiệp vụ: bản cũ không ghi đè, nạp
        # lại y hệt là không-làm-gì, đụng độ thì giữ nguyên bản đã chấp nhận.
        if outcome.decision in ("skip_stale", "duplicate_noop", "conflict"):
            return ProjectionResult(table=table, action="untouched")

        if source_entity == "projects":
            if outcome.decision == "tombstone":
                return await self._archive_project(record, source_instance_id, now)
            return await self._project_project(record, source_system, source_instance_id, now)

        if source_entity == "areas":
            if outcome.decision == "tombstone":
                return await self._archive_area(record, source_instance_id, now)
            assert project_id is not None, "area luôn cần project_id đã resolve — xem SyncRunService"
            return await self._project_area(record, source_system, source_instance_id, project_id, now)

        if outcome.decision == "tombstone":
            return await self._tombstone(record, table, source_instance_id, now)

        assert project_id is not None, "unit/deal luôn cần project_id đã resolve — xem SyncRunService"
        if source_entity == "units":
            return await self._project_unit(record, source_system, source_instance_id, project_id, now)
        return await self._project_deal(record, source_system, source_instance_id, now)

    # --- units --------------------------------------------------------------

    async def _resolve_area(
        self, data: dict[str, Any], record: SourceRecord, project_id: uuid.UUID
    ) -> tuple[uuid.UUID, str]:
        """Trả `(area_id, unit_type)` của phân khu đích.

        Hợp đồng v1 cho phép `area_ref` ở HAI hình dạng (`$defs/area_ref` là một
        `oneOf`): `{area_id}` hoặc `{area_name, unit_type}`. Trước Phase 1, tầng
        này chỉ đọc được hình dạng thứ hai, nên một payload hợp lệ theo hợp đồng
        vẫn bị từ chối với `MISSING_FIELD` trên một trường mà nó KHÔNG ĐƯỢC PHÉP
        mang. Một hệ nguồn dùng `area_id` sẽ mất toàn bộ bản ghi căn ngay lô đầu.

        **`area_id` thắng khi cả hai cùng có.** Nó là danh tính trực tiếp; cặp
        `area_name`/`unit_type` chỉ là khoá tự nhiên suy ra. Tình huống này chỉ
        xuất hiện sau khi hợp nhất bản ghi partial — xem ghi chú dưới.

        **Tra không thấy thì TỪ CHỐI, không rơi về `area_name`.** Hệ nguồn đã chỉ
        đích danh một phân khu; đoán tiếp bằng tên là gắn căn vào một chỗ họ không
        hề chỉ định, và không có lỗi nào phát ra để ai biết.

        **Luôn giới hạn theo `project_id`.** Thiếu điều kiện đó, một hệ nguồn gắn
        được căn của mình vào phân khu của dự án khác chỉ bằng cách đoán UUID.

        Ghi chú về bản ghi partial: `history_guard._read_unit_mirror()` CỐ Ý không
        trả `area_id`. Nhờ vậy một bản ghi partial chuyển phân khu bằng
        `area_name`/`unit_type` không bị hợp nhất với `area_id` CŨ rồi mất lệnh
        chuyển một cách im lặng. Đừng thêm `area_id` vào đó.
        """
        # v2: `{external_area_id}` — DUY NHẤT hình dạng v2 chấp nhận (§4
        # sync_contract_v2_draft.md). Tra theo (project_id, external_id): phân
        # khu đã được Phase D chiếu từ chính hệ nguồn này, scoped đúng dự án như
        # hai nhánh còn lại.
        raw_external_area_id = data.get("external_area_id")
        if raw_external_area_id is not None:
            row = (
                await self._session.execute(
                    sa.select(areas.c.id, areas.c.unit_type).where(
                        areas.c.project_id == project_id,
                        areas.c.external_id == raw_external_area_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise ProjectionError(
                    "UNKNOWN_AREA",
                    f"Không tìm thấy phân khu '{raw_external_area_id}' trong dự án",
                    json_path=f"{record.json_path}.data.external_area_id",
                    source_record_id=record.source_record_id,
                    field_name="external_area_id",
                )
            return row.id, row.unit_type

        raw_area_id = data.get("area_id")
        if raw_area_id is not None:
            try:
                area_uuid = uuid.UUID(str(raw_area_id))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ProjectionError(
                    "INVALID_AREA_REF",
                    "Trường 'area_id' phải là UUID hợp lệ",
                    json_path=f"{record.json_path}.data.area_id",
                    source_record_id=record.source_record_id,
                    field_name="area_id",
                    raw_value_redacted=redact(raw_area_id, "area_id"),
                    error_category="field",
                ) from exc

            row = (
                await self._session.execute(
                    sa.select(areas.c.id, areas.c.unit_type).where(
                        areas.c.id == area_uuid,
                        areas.c.project_id == project_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise ProjectionError(
                    "UNKNOWN_AREA",
                    f"Không tìm thấy phân khu '{area_uuid}' trong dự án",
                    json_path=f"{record.json_path}.data.area_id",
                    source_record_id=record.source_record_id,
                    field_name="area_id",
                )
            return row.id, row.unit_type

        # `areas` không có mã ngoài, nên căn trỏ về phân khu bằng đúng khoá tự
        # nhiên mà luồng CSV đang dùng: (project_id, area_name, unit_type).
        area_name = _require_text(data, "area_name", record)
        unit_type = _require_text(data, "unit_type", record)
        row = (
            await self._session.execute(
                sa.select(areas.c.id, areas.c.unit_type).where(
                    areas.c.project_id == project_id,
                    areas.c.area_name == area_name,
                    areas.c.unit_type == unit_type,
                )
            )
        ).one_or_none()
        if row is None:
            raise ProjectionError(
                "UNKNOWN_AREA",
                f"Không tìm thấy phân khu '{area_name}' / '{unit_type}' trong dự án",
                json_path=f"{record.json_path}.data.area_name",
                source_record_id=record.source_record_id,
                field_name="area_name",
            )
        return row.id, row.unit_type

    async def _project_unit(
        self,
        record: SourceRecord,
        source_system: str,
        source_instance_id: str,
        project_id: uuid.UUID,
        now: datetime,
    ) -> ProjectionResult:
        data = record.data
        unit_code = _require_text(data, "unit_code", record)
        status = _require_text(data, "status", record).lower()

        if status not in UNIT_STATUSES:
            raise ProjectionError(
                "UNKNOWN_UNIT_STATUS",
                f"Trạng thái căn '{status}' không nằm trong tập cho phép: {', '.join(sorted(UNIT_STATUSES))}",
                json_path=f"{record.json_path}.data.status",
                source_record_id=record.source_record_id,
                field_name="status",
                raw_value_redacted=redact(status, "status"),
            )

        # `unit_type` lấy từ DÒNG PHÂN KHU đã tra được, không lấy từ payload.
        # Hai lý do: nhánh `area_id` không mang `unit_type` (hợp đồng v1 đóng
        # `additionalProperties`, không có chỗ để gửi), và ở nhánh `area_name`
        # thì `uq_areas_project_name_unit_type` đã bảo đảm hai giá trị bằng nhau
        # — nên đọc từ phân khu là tương đương, mà chỉ còn MỘT nguồn.
        area_id, unit_type = await self._resolve_area(data, record, project_id)

        values = {
            "source_system": source_system,
            "source_instance_id": source_instance_id,
            "external_unit_id": record.source_record_id,
            "area_id": area_id,
            "unit_code": unit_code,
            "unit_type": unit_type,
            "status": status,
            "source_revision": record.source_revision,
            "source_updated_at": record.source_updated_at,
            # Bản đến mới hơn được phép làm sống lại căn đã xoá — hệ nguồn xoá rồi
            # tạo lại là chuyện bình thường và CRM là nguồn sự thật.
            "deleted_at": None,
            "updated_at": now,
        }
        return await self._upsert(units, values, now, key_column="external_unit_id")

    # --- deals --------------------------------------------------------------

    async def _project_deal(
        self, record: SourceRecord, source_system: str, source_instance_id: str, now: datetime
    ) -> ProjectionResult:
        data = record.data
        external_unit_id = _require_text(data, "external_unit_id", record)
        status, source_status = normalize_deal_status(data.get("status"), record)
        instance = source_instance_id

        unit_id = await self._session.scalar(
            sa.select(units.c.id).where(
                units.c.source_instance_id == instance,
                units.c.external_unit_id == external_unit_id,
            )
        )
        if unit_id is None:
            # Quan hệ giao dịch → căn không hợp lệ. Từ chối tường minh thay vì tạo
            # một căn rỗng: bịa ra căn là bịa ra quỹ hàng, và mọi tỷ lệ hấp thụ
            # phía sau sẽ sai theo.
            raise ProjectionError(
                "UNKNOWN_UNIT_REFERENCE",
                f"Giao dịch trỏ tới căn '{external_unit_id}' chưa có trong bản sao",
                json_path=f"{record.json_path}.data.external_unit_id",
                source_record_id=record.source_record_id,
                field_name="external_unit_id",
            )

        values = {
            "source_system": source_system,
            "source_instance_id": instance,
            "external_deal_id": record.source_record_id,
            "unit_id": unit_id,
            "status": status,
            "source_status": source_status,
            "reserved_at": _optional_timestamp(data, "reserved_at", record),
            "sold_at": _optional_timestamp(data, "sold_at", record),
            "lost_at": _optional_timestamp(data, "lost_at", record),
            "source_revision": record.source_revision,
            "source_updated_at": record.source_updated_at,
            "deleted_at": None,
            "updated_at": now,
        }
        self._check_status_dates(values, record)
        return await self._upsert(deals, values, now, key_column="external_deal_id")

    def _check_status_dates(self, values: dict[str, Any], record: SourceRecord) -> None:
        """Bắt mâu thuẫn trạng thái ↔ mốc thời gian ở tầng ứng dụng.

        DB cũng có CHECK cho việc này, nhưng để nó nổ ở tầng DB thì cả lô rollback
        và người dùng chỉ thấy một lỗi ràng buộc không quy được về bản ghi nào.
        """
        required = {"sold": "sold_at", "reserved": "reserved_at", "lost": "lost_at"}
        field = required.get(values["status"])
        if field and values[field] is None:
            raise ProjectionError(
                "MISSING_STATUS_DATE",
                f"Trạng thái '{values['status']}' bắt buộc phải có '{field}'",
                json_path=f"{record.json_path}.data.{field}",
                source_record_id=record.source_record_id,
                field_name=field,
            )

    # --- projects (Phase D) ---------------------------------------------------

    async def _project_project(
        self, record: SourceRecord, source_system: str, source_instance_id: str, now: datetime
    ) -> ProjectionResult:
        """Ghi/tạo dự án. Dự án là GỐC — không có cha để tra, không có
        `PARENT_ARCHIVED` nào áp cho nó."""
        data = record.data
        name = _require_text(data, "name", record)
        launch_date = _require_date(data, "launch_date", record)

        values = {
            "external_id": record.source_record_id,
            "source_system": source_system,
            "source_instance_id": source_instance_id,
            "name": name,
            "launch_date": launch_date,
            "status": "active",
            "source_revision": record.source_revision,
            "source_updated_at": record.source_updated_at,
            "updated_at": now,
        }
        return await self._upsert(
            projects, values, now, key_column="external_id", constraint="uq_projects_source_identity"
        )

    async def _archive_project(self, record: SourceRecord, source_instance_id: str, now: datetime) -> ProjectionResult:
        """Lưu trữ dự án — KHÔNG xoá vật lý, `projects` không có `deleted_at`
        (§A1.9 phase_a_domain_freeze.md). Từ chối nếu còn Area SỐNG
        (`status='active'`) thuộc dự án — KHÔNG cascade (§A1.8/§A4.4)."""
        project_row = (
            await self._session.execute(
                sa.select(projects.c.id).where(
                    projects.c.source_instance_id == source_instance_id,
                    projects.c.external_id == record.source_record_id,
                )
            )
        ).one_or_none()
        if project_row is None:
            # Hệ nguồn xoá một dự án mà bản sao chưa từng có — `crm_source_records`
            # đã ghi tombstone (cùng lý do với `_tombstone` của units/deals).
            return ProjectionResult(table="projects", action="untouched")

        live_areas = (
            (
                await self._session.execute(
                    sa.select(areas.c.external_id)
                    .where(areas.c.project_id == project_row.id, areas.c.status == "active")
                    .order_by(areas.c.external_id)
                    .limit(_MAX_LISTED_CHILDREN + 1)
                )
            )
            .scalars()
            .all()
        )
        if live_areas:
            raise ProjectionError(
                "PARENT_HAS_LIVE_CHILDREN",
                f"Dự án '{record.source_record_id}' còn "
                f"{len(live_areas)}{'+' if len(live_areas) > _MAX_LISTED_CHILDREN else ''} phân khu đang hoạt động",
                json_path=record.json_path,
                source_record_id=record.source_record_id,
                error_category="business",
            )

        await self._session.execute(
            sa.update(projects)
            .where(projects.c.id == project_row.id)
            .values(status="archived", updated_at=sa.func.greatest(now, projects.c.created_at))
        )
        return ProjectionResult(table="projects", action="tombstoned", row_id=project_row.id)

    # --- areas (Phase D) -------------------------------------------------------

    async def _project_area(
        self,
        record: SourceRecord,
        source_system: str,
        source_instance_id: str,
        project_id: uuid.UUID,
        now: datetime,
    ) -> ProjectionResult:
        """Ghi/tạo phân khu, scoped ĐÚNG dự án của phong bì.

        `PARENT_ARCHIVED` nếu dự án đã lưu trữ (§A4.4 mục 2: "UPSERT một con vào
        cha đã archive → TỪ CHỐI"). Khoá tự nhiên `uq_areas_project_name_unit_type`
        (0001) vẫn còn nguyên — đổi tên trùng một cặp đã có trong CÙNG dự án bị
        DB chặn, ánh xạ sang `AREA_NATURAL_KEY_CONFLICT` bên dưới.
        """
        data = record.data
        area_name = _require_text(data, "area_name", record)
        unit_type = _require_text(data, "unit_type", record)
        bedrooms = data.get("bedrooms")
        area_sqm = data.get("area_sqm")
        total_units = data.get("total_units")
        for key, value in (("bedrooms", bedrooms), ("area_sqm", area_sqm), ("total_units", total_units)):
            if value is None:
                raise ProjectionError(
                    "MISSING_FIELD",
                    f"Trường '{key}' bắt buộc — cả năm trường của phân khu đều có thẩm quyền, không có mặc định",
                    json_path=f"{record.json_path}.data.{key}",
                    source_record_id=record.source_record_id,
                    field_name=key,
                    error_category="field",
                )

        project_status = await self._session.scalar(sa.select(projects.c.status).where(projects.c.id == project_id))
        if project_status == "archived":
            raise ProjectionError(
                "PARENT_ARCHIVED",
                f"Dự án của phân khu '{record.source_record_id}' đã lưu trữ — không nhận upsert cho con của nó",
                json_path=record.json_path,
                source_record_id=record.source_record_id,
                error_category="business",
            )

        # Phân khu KHÔNG được ĐỔI dự án (§A1.6 phase_a_domain_freeze.md,
        # `AREA_CROSS_PROJECT_MOVE`). Mini CRM tự nó không có đường nào gửi lệnh
        # này (`AreaPatch` không có `external_project_id`), nhưng backend là
        # MẢNH GHÉP MIRROR nên vẫn phải tự đứng vững — một hệ nguồn hỏng hoặc một
        # payload bị sửa tay không được phép âm thầm CHUYỂN phân khu bằng cách
        # upsert nó dưới một `project_ref` khác. `_upsert()` dùng `ON CONFLICT ...
        # DO UPDATE` ghi đè MỌI cột kể cả `project_id`; không kiểm trước ở đây thì
        # nó sẽ làm đúng điều bị cấm.
        current_project_id = await self._session.scalar(
            sa.select(areas.c.project_id).where(
                areas.c.source_instance_id == source_instance_id, areas.c.external_id == record.source_record_id
            )
        )
        if current_project_id is not None and current_project_id != project_id:
            raise ProjectionError(
                "AREA_CROSS_PROJECT_MOVE",
                f"Phân khu '{record.source_record_id}' đã thuộc một dự án khác — không được chuyển dự án",
                json_path=record.json_path,
                source_record_id=record.source_record_id,
                error_category="business",
            )

        values = {
            "external_id": record.source_record_id,
            "source_system": source_system,
            "source_instance_id": source_instance_id,
            "project_id": project_id,
            "area_name": area_name,
            "unit_type": unit_type,
            "bedrooms": bedrooms,
            "area_sqm": area_sqm,
            "total_units": total_units,
            "status": "active",
            "source_revision": record.source_revision,
            "source_updated_at": record.source_updated_at,
            "updated_at": now,
        }
        try:
            return await self._upsert(
                areas, values, now, key_column="external_id", constraint="uq_areas_source_identity"
            )
        except IntegrityError as exc:
            if "uq_areas_project_name_unit_type" in str(exc.orig):
                raise ProjectionError(
                    "AREA_NATURAL_KEY_CONFLICT",
                    f"Dự án đã có một phân khu khác mang ('{area_name}', '{unit_type}')",
                    json_path=record.json_path,
                    source_record_id=record.source_record_id,
                    error_category="business",
                ) from exc
            raise

    async def _archive_area(self, record: SourceRecord, source_instance_id: str, now: datetime) -> ProjectionResult:
        """Lưu trữ phân khu — KHÔNG xoá vật lý. Từ chối nếu còn Unit SỐNG
        (`deleted_at IS NULL`) thuộc phân khu — KHÔNG cascade, cùng lý do với
        `_archive_project`."""
        area_row = (
            await self._session.execute(
                sa.select(areas.c.id).where(
                    areas.c.source_instance_id == source_instance_id,
                    areas.c.external_id == record.source_record_id,
                )
            )
        ).one_or_none()
        if area_row is None:
            return ProjectionResult(table="areas", action="untouched")

        live_units = (
            (
                await self._session.execute(
                    sa.select(units.c.external_unit_id)
                    .where(units.c.area_id == area_row.id, units.c.deleted_at.is_(None))
                    .order_by(units.c.external_unit_id)
                    .limit(_MAX_LISTED_CHILDREN + 1)
                )
            )
            .scalars()
            .all()
        )
        if live_units:
            raise ProjectionError(
                "PARENT_HAS_LIVE_CHILDREN",
                f"Phân khu '{record.source_record_id}' còn "
                f"{len(live_units)}{'+' if len(live_units) > _MAX_LISTED_CHILDREN else ''} căn đang hoạt động",
                json_path=record.json_path,
                source_record_id=record.source_record_id,
                error_category="business",
            )

        await self._session.execute(
            sa.update(areas)
            .where(areas.c.id == area_row.id)
            .values(status="archived", updated_at=sa.func.greatest(now, areas.c.created_at))
        )
        return ProjectionResult(table="areas", action="tombstoned", row_id=area_row.id)

    # --- Ghi chung ----------------------------------------------------------

    async def _upsert(
        self, table: sa.Table, values: dict[str, Any], now: datetime, *, key_column: str, constraint: str | None = None
    ) -> ProjectionResult:
        """Upsert theo danh tính nguồn `(source_instance_id, <key_column>)`.

        Đi qua `ON CONFLICT` chứ không đọc-rồi-ghi: hai lô của cùng một hệ nguồn
        chạy song song thì đọc-rồi-ghi có cửa sổ đua, còn ràng buộc UNIQUE thì
        không.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(table).values(id=uuid.uuid4(), created_at=now, **values)
        if constraint is None:
            constraint = "uq_units_source_identity" if table is units else "uq_deals_source_identity"
        assignments = {name: stmt.excluded[name] for name in values}
        # `updated_at` KHÔNG được lùi trước `created_at` của dòng đang có.
        #
        # `now` được chốt một lần cho cả lô, nên hai lô song song mang hai mốc lệch
        # nhau vài mili giây theo chiều bất kỳ. Nếu lô CHÈN dòng có mốc muộn hơn lô
        # CẬP NHẬT nó, nhánh DO UPDATE sẽ ghi `updated_at < created_at` và
        # `ck_units_updated_after_created` nổ — biến một lô hợp lệ thành một dòng
        # lỗi vì lý do đồng hồ, không phải vì nội dung.
        #
        # `GREATEST` không bịa ra thời gian: nó chỉ từ chối ghi một thứ tự bất khả.
        # Cả hai mốc đều là mốc của PHÍA NHẬN; thứ tự sự kiện ở hệ nguồn vẫn hoàn
        # toàn do `source_revision` quyết định và không đọc từ đây.
        assignments["updated_at"] = sa.func.greatest(stmt.excluded.updated_at, table.c.created_at)
        stmt = stmt.on_conflict_do_update(constraint=constraint, set_=assignments).returning(
            table.c.id, sa.column("xmax", sa.Text).label("xmax")
        )

        row = (await self._session.execute(stmt)).one()
        # xmax = 0 nghĩa là dòng vừa được CHÈN, khác 0 là vừa được cập nhật. Đây là
        # cách duy nhất phân biệt hai nhánh của ON CONFLICT trong một lệnh.
        inserted = str(row.xmax) == "0"
        _ = key_column
        return ProjectionResult(table=table.name, action="inserted" if inserted else "updated", row_id=row.id)

    async def _tombstone(
        self, record: SourceRecord, table_name: str, source_instance_id: str, now: datetime
    ) -> ProjectionResult:
        """Đánh dấu đã xoá, KHÔNG xoá dòng.

        Xoá cứng là mất luôn vết đã từng tồn tại, và mọi số liệu lịch sử tính từ
        nó thành không giải thích được.
        """
        table = units if table_name == "units" else deals
        key = units.c.external_unit_id if table_name == "units" else deals.c.external_deal_id
        instance = source_instance_id

        result = await self._session.execute(
            sa.update(table)
            .where(table.c.source_instance_id == instance, key == record.source_record_id)
            # Cùng lý do với `_upsert`: `updated_at` không được lùi trước
            # `created_at` khi hai lô song song mang hai mốc lệch nhau.
            .values(deleted_at=now, updated_at=sa.func.greatest(now, table.c.created_at))
            .returning(table.c.id)
        )
        row = result.one_or_none()
        if row is None:
            # Hệ nguồn xoá một bản ghi mà bản sao chưa từng có. `crm_source_records`
            # đã ghi tombstone, nên lô upsert cũ đến sau vẫn bị chặn — không cần
            # tạo một dòng nghiệp vụ rỗng chỉ để đánh dấu nó đã chết.
            return ProjectionResult(table=table_name, action="untouched")
        return ProjectionResult(table=table_name, action="tombstoned", row_id=row.id)
