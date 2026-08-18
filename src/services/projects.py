"""ProjectService — SỬA nội dung hiển thị của dự án/phân khu. KHÔNG còn TẠO.

═══════════════════════════════════════════════════════════════════════════
 PHASE D — THU HẸP (§D7): Project/Area CHỈ được TẠO/SỬA qua ingestion
═══════════════════════════════════════════════════════════════════════════

Từ Phase D, Mini CRM là NGUỒN SỰ THẬT cho `Project`/`Area` (đóng băng ở Phase A,
hiện thực ở Mini CRM Phase B/C, backend soi gương từ Phase D —
`src/services/domain_projection.py::_project_project`/`_project_area`).
Backend KHÔNG còn đường TẠO nào cho hai thực thể này ngoài đường đồng bộ:

    `create_project`/`create_area` — ĐÃ XOÁ khỏi service này.

`update_project`/`update_area` VẪN CÒN, nhưng bị THU HẸP: `PROJECT_EDITABLE`/
`AREA_EDITABLE` giờ CHỈ còn `headline`/`introduce` — nội dung hiển thị mà backend
SỞ HỮU RIÊNG (`phase_a_domain_freeze.md` §A2.3: "cột phía nhận đang có mà không
nằm trong hợp đồng ... là chú thích cục bộ của phía nhận"), KHÔNG phải dữ liệu
nghiệp vụ CANONICAL. `name`/`launch_date` (Project) và `area_name`/`unit_type`/
`bedrooms`/`area_sqm`/`total_units` (Area) — CANONICAL, do Mini CRM sở hữu — đã
bị RÚT khỏi hai tập trên, nên PATCH gửi kèm chúng bị `_reject_unknown_fields`
từ chối với `FIELD_NOT_EDITABLE`, giống hệt cách `project_id`/`status` đã bị
chặn từ trước Phase D.

Đây là đường ghi DUY NHẤT còn lại cho `projects`/`areas` mà KHÔNG đi qua ingestion
— và nó được PHÉP tồn tại chính vì `headline`/`introduce` không phải business
write: `docs/roadmap.md`/D7 chỉ cấm mutation CANONICAL, không cấm sửa chú thích
hiển thị của chính phía nhận.

Test route/service enumeration proving no path can mutate canonical mirror data:
`tests/test_services/test_hierarchy_projection.py::test_no_write_path_can_mutate_canonical_project_or_area_fields`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import areas, projects

log = get_logger("src.services.projects")

# Trạng thái đọc được — không còn nghĩa "vừa tạo" (không còn đường TẠO ở service
# này), chỉ còn dùng trong `AreaRecord`/`ProjectRecord` khi đọc lại sau PATCH.
INITIAL_STATUS = "active"

# PATCH chỉ được sửa đúng những trường này — Phase D THU HẸP còn lại đúng nội
# dung hiển thị mà backend SỞ HỮU RIÊNG (`headline`/`introduce`, §A2.3). MỌI
# trường CANONICAL (`name`/`launch_date`/`area_name`/`unit_type`/`bedrooms`/
# `area_sqm`/`total_units`) đã bị RÚT — Mini CRM sở hữu chúng, chỉ ingestion mới
# được ghi. `project_id`/`status` vẫn không có mặt — cùng lý do cũ.
PROJECT_EDITABLE = frozenset({"headline", "introduce"})
AREA_EDITABLE = frozenset({"headline", "introduce"})


class CatalogRejectedError(Exception):
    """Yêu cầu tạo bị từ chối vì lý do nghiệp vụ, không phải sự cố hệ thống."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(slots=True)
class ProjectRecord:
    project_id: str
    name: str
    launch_date: date
    status: str
    headline: str
    introduce: str
    cover_image_url: str | None
    created_at: datetime
    # Phase D — None với dự án di sản (tạo trước Phase D).
    external_id: str | None = None
    source_revision: int | None = None


@dataclass(slots=True)
class AreaRecord:
    area_id: str
    project_id: str
    area_name: str
    unit_type: str
    bedrooms: int
    area_sqm: Decimal
    total_units: int
    status: str
    headline: str
    introduce: str
    cover_image_url: str | None
    created_at: datetime
    external_id: str | None = None
    source_revision: int | None = None


class ProjectService:
    """Sửa nội dung hiển thị của dự án/phân khu. Không giữ trạng thái giữa các
    lần gọi. KHÔNG còn phương thức tạo — xem docstring module."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def update_project(self, project_id: uuid.UUID | str, changes: dict[str, Any]) -> ProjectRecord:
        """Cập nhật một dự án. `changes` chỉ chứa trường người dùng thực sự gửi."""
        project_uuid = uuid.UUID(str(project_id))
        _reject_unknown_fields(changes, PROJECT_EDITABLE)

        async with self._session_factory() as session:
            async with session.begin():
                row = (await session.execute(sa.select(projects).where(projects.c.id == project_uuid))).one_or_none()
                if row is None:
                    raise CatalogRejectedError("PROJECT_NOT_FOUND", f"Dự án '{project_uuid}' không tồn tại")

                await session.execute(sa.update(projects).where(projects.c.id == project_uuid).values(**changes))
                updated = (await session.execute(sa.select(projects).where(projects.c.id == project_uuid))).one()

        log.info("project.updated", project_id=str(project_uuid), fields=sorted(changes))
        return ProjectRecord(
            project_id=str(updated.id),
            name=updated.name,
            launch_date=updated.launch_date,
            status=updated.status,
            headline=updated.headline,
            introduce=updated.introduce,
            cover_image_url=updated.cover_image_url,
            created_at=updated.created_at,
            external_id=updated.external_id,
            source_revision=updated.source_revision,
        )

    async def update_area(self, area_id: uuid.UUID | str, changes: dict[str, Any]) -> AreaRecord:
        """Cập nhật nội dung hiển thị của một phân khu.

        Không còn try/except quanh `UQ_AREA`: `AREA_EDITABLE` chỉ còn
        `headline`/`introduce`, không trường nào trong đó tham gia ràng buộc
        `uq_areas_project_name_unit_type` — đường đó không còn TỚI ĐƯỢC nữa từ
        service này (xem docstring module).
        """
        area_uuid = uuid.UUID(str(area_id))
        _reject_unknown_fields(changes, AREA_EDITABLE)

        async with self._session_factory() as session:
            async with session.begin():
                row = (await session.execute(sa.select(areas).where(areas.c.id == area_uuid))).one_or_none()
                if row is None:
                    raise CatalogRejectedError("AREA_NOT_FOUND", f"Phân khu '{area_uuid}' không tồn tại")

                await session.execute(sa.update(areas).where(areas.c.id == area_uuid).values(**changes))
                updated = (await session.execute(sa.select(areas).where(areas.c.id == area_uuid))).one()

        log.info("area.updated", area_id=str(area_uuid), fields=sorted(changes))
        return AreaRecord(
            area_id=str(updated.id),
            project_id=str(updated.project_id),
            area_name=updated.area_name,
            unit_type=updated.unit_type,
            bedrooms=updated.bedrooms,
            area_sqm=updated.area_sqm,
            total_units=updated.total_units,
            status=updated.status,
            headline=updated.headline,
            introduce=updated.introduce,
            cover_image_url=updated.cover_image_url,
            created_at=updated.created_at,
            external_id=updated.external_id,
            source_revision=updated.source_revision,
        )


def _reject_unknown_fields(changes: dict[str, Any], allowed: frozenset[str]) -> None:
    """Chốt chặn cuối cùng cho danh sách trường được sửa.

    Schema pydantic đã lọc rồi, nhưng service còn được gọi trực tiếp từ test và
    code khác — kiểm ở đây để `project_id`/`status` không bao giờ lọt vào UPDATE.
    """
    if not changes:
        raise CatalogRejectedError("NO_CHANGES", "Cần ít nhất một trường để cập nhật")
    unknown = set(changes) - allowed
    if unknown:
        raise CatalogRejectedError("FIELD_NOT_EDITABLE", f"Không được sửa: {', '.join(sorted(unknown))}")


def as_dict(record: Any) -> dict[str, Any]:
    """Đổi dataclass kết quả thành dict cho response model."""
    return {field: getattr(record, field) for field in record.__slots__}
