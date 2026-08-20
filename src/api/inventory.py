"""Router tồn kho theo từng căn (S3) và chạy song song hai bộ tính.

Đọc từ bản sao CRM (`units` + `deals`), khác hẳn `/absorption/summary` vốn đọc dữ
liệu tổng hợp cũ. Hai đường tồn tại song song có chủ đích: đây là giai đoạn ĐỐI
CHIẾU, chưa cắt sang.

Router mỏng như các router khác: kiểm tra tham số, gọi service, ánh xạ lỗi.

Phase E: `GET /inventory` và `GET /deals` giờ đòi tối thiểu vai trò
`business_viewer` VÀ phạm vi dự án (`src/services/dashboard_auth.py`) — hai
route này trước đây MỞ hoàn toàn (không `Depends` nào), đúng như
`docs/roadmap.md` R-05 đã ghi nhận là rủi ro đang chờ Phase E đóng lại.
`/absorption/parallel-run` CHƯA được scope ở đợt này — nằm ngoài danh sách route
"tối thiểu" của Phase E.
"""

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import (
    DealList,
    DealOut,
    InventoryAreaOut,
    InventoryOut,
    InventoryUnitOut,
    ParallelRunOut,
)
from src.models.tables import areas as areas_table
from src.models.tables import deals, projects, units
from src.services.dashboard_auth import DashboardPrincipal, require_project_in_scope, require_role
from src.services.domain_absorption import (
    CALCULATOR_DOMAIN,
    DomainAbsorptionCalculatorService,
    ParallelRunComparator,
)

router = APIRouter(tags=["inventory"])
require_viewer = require_role("business_viewer")
log = get_logger("src.api.inventory")

MAX_UNITS_PER_PAGE = 500

# Dashboard đang đọc bộ tính NÀO. Hằng số này là điểm cắt sang duy nhất; đổi nó
# là một quyết định tường minh, không phải hệ quả phụ của việc chạy song song.
PRODUCTION_CALCULATOR = CALCULATOR_DOMAIN


def _uuid_or_422(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": f"{field} phải là UUID hợp lệ", "error_code": "INVALID_UUID"},
        ) from exc


async def _require_project(project_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        found = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_id))
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"message": "Không tìm thấy dự án", "error_code": "PROJECT_NOT_FOUND"},
        )


async def _resolve_project_scope(
    project_id: str | None, external_project_id: str | None
) -> tuple[uuid.UUID, str | None]:
    """Đúng MỘT trong hai — UUID nội bộ hoặc `external_id` Mini CRM (Phase D).
    Cùng quy tắc với `src/api/dashboard.py::_resolve_project_scope`, cùng lý do
    trả kèm `external_id`: Phase E cần nó để kiểm phạm vi."""
    if (project_id is None) == (external_project_id is None):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Cần ĐÚNG MỘT trong hai: project_id hoặc external_project_id",
                "error_code": "AMBIGUOUS_PROJECT_SCOPE",
            },
        )
    if external_project_id is not None:
        async with get_session_factory()() as session:
            found = await session.scalar(
                sa.select(projects.c.id).where(projects.c.external_id == external_project_id)
            )
        if found is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Không tìm thấy dự án '{external_project_id}'",
                    "error_code": "PROJECT_NOT_FOUND",
                },
            )
        return found, external_project_id

    project_uuid = _uuid_or_422(project_id, "project_id")
    async with get_session_factory()() as session:
        found_external_id = await session.scalar(
            sa.select(projects.c.external_id).where(projects.c.id == project_uuid)
        )
    return project_uuid, found_external_id


async def _resolve_area_scope(area_id: str | None, external_area_id: str | None) -> uuid.UUID | None:
    """Lọc phân khu — TUỲ CHỌN (khác dự án, phân khu không bắt buộc phải chỉ
    định). Cả hai cùng có mặt là mơ hồ, bị từ chối; cả hai cùng vắng nghĩa là
    "mọi phân khu trong dự án"."""
    if area_id is not None and external_area_id is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Chỉ được truyền MỘT trong hai: area_id hoặc external_area_id",
                "error_code": "AMBIGUOUS_AREA_SCOPE",
            },
        )
    if area_id is not None:
        return _uuid_or_422(area_id, "area_id")
    if external_area_id is None:
        return None

    async with get_session_factory()() as session:
        found = await session.scalar(sa.select(areas_table.c.id).where(areas_table.c.external_id == external_area_id))
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Không tìm thấy phân khu '{external_area_id}'", "error_code": "AREA_NOT_FOUND"},
        )
    return found


@router.get(
    "/inventory",
    response_model=InventoryOut,
    summary="Tồn kho theo từng căn, suy ra từ bản sao CRM",
)
async def inventory(
    project_id: str | None = Query(default=None, description="UUID dự án"),
    external_project_id: str | None = Query(
        default=None, description="external_id dự án ở Mini CRM — thay thế cho project_id (Phase D)"
    ),
    area_id: str | None = Query(default=None, description="Lọc theo phân khu (UUID nội bộ)"),
    external_area_id: str | None = Query(
        default=None, description="external_id phân khu ở Mini CRM — thay thế cho area_id (Phase D)"
    ),
    unit_status: str | None = Query(default=None, description="available | reserved | sold | blocked"),
    deal_status: str | None = Query(default=None, description="Lọc căn theo trạng thái giao dịch đang giữ"),
    include_deleted: bool = Query(default=False, description="Kèm cả bản ghi đã tombstone"),
    include_units: bool = Query(default=False, description="Trả kèm danh sách từng căn"),
    limit: int = Query(default=100, ge=1, le=MAX_UNITS_PER_PAGE),
    offset: int = Query(default=0, ge=0),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> InventoryOut:
    """Tổng hợp theo phân khu, kèm danh sách căn khi được yêu cầu.

    Mặc định KHÔNG trả danh sách căn: một dự án có hàng nghìn căn, và phần lớn màn
    hình chỉ cần con số cộng dồn.
    """
    project_uuid, project_external_id = await _resolve_project_scope(project_id, external_project_id)
    require_project_in_scope(principal, project_external_id)
    await _require_project(project_uuid)

    result = await DomainAbsorptionCalculatorService().compute(project_uuid)

    area_filter = await _resolve_area_scope(area_id, external_area_id)
    rows = [row for row in result.inventory if area_filter is None or row.area_id == area_filter]

    totals = None
    if rows:
        totals = InventoryAreaOut(
            area_id=str(project_uuid),
            area_name="(toàn dự án)",
            unit_type="*",
            total_units=sum(r.total_units for r in rows),
            units_sold=sum(r.units_sold for r in rows),
            units_reserved=sum(r.units_reserved for r in rows),
            units_remaining=sum(r.units_remaining for r in rows),
            available_remaining_units=sum(r.units_remaining for r in rows),
            units_blocked=sum(r.units_blocked for r in rows),
        )

    unit_rows: list[InventoryUnitOut] = []
    if include_units:
        unit_rows = await _load_units(
            project_uuid,
            area_filter,
            unit_status=unit_status,
            deal_status=deal_status,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
        )

    return InventoryOut(
        project_id=str(project_uuid),
        calculator=CALCULATOR_DOMAIN,
        totals=totals,
        areas=[
            InventoryAreaOut(
                area_id=str(row.area_id),
                area_name=row.area_name,
                unit_type=row.unit_type,
                total_units=row.total_units,
                units_sold=row.units_sold,
                units_reserved=row.units_reserved,
                units_remaining=row.units_remaining,
                available_remaining_units=row.units_remaining,
                units_blocked=row.units_blocked,
            )
            for row in rows
        ],
        units=unit_rows,
        anomalies=result.anomalies,
    )


async def _load_units(
    project_id: uuid.UUID,
    area_filter: uuid.UUID | None,
    *,
    unit_status: str | None,
    deal_status: str | None,
    include_deleted: bool,
    limit: int,
    offset: int,
) -> list[InventoryUnitOut]:
    """Danh sách căn kèm trạng thái giao dịch đang giữ (nếu có).

    LEFT JOIN sang giao dịch đang giữ: một căn không có giao dịch nào vẫn phải xuất
    hiện, nếu không thì danh sách chỉ còn căn đã bán/đã giữ.
    """
    held = (
        sa.select(deals.c.unit_id, deals.c.status)
        .where(deals.c.deleted_at.is_(None), deals.c.status.in_(("reserved", "sold")))
        .subquery()
    )

    condition = [areas_table.c.project_id == project_id]
    if area_filter is not None:
        condition.append(units.c.area_id == area_filter)
    if unit_status is not None:
        condition.append(units.c.status == unit_status)
    if not include_deleted:
        condition.append(units.c.deleted_at.is_(None))
    if deal_status is not None:
        condition.append(held.c.status == deal_status)

    query = (
        sa.select(
            units.c.id,
            units.c.external_unit_id,
            units.c.area_id,
            units.c.unit_code,
            units.c.unit_type,
            units.c.status,
            units.c.deleted_at,
            held.c.status.label("active_deal_status"),
        )
        .select_from(
            units.join(areas_table, units.c.area_id == areas_table.c.id).join(
                held, held.c.unit_id == units.c.id, isouter=True
            )
        )
        .where(*condition)
        .order_by(units.c.unit_code)  # thứ tự tất định cho phân trang
        .limit(limit)
        .offset(offset)
    )

    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()

    return [
        InventoryUnitOut(
            unit_id=str(row["id"]),
            external_unit_id=row["external_unit_id"],
            area_id=str(row["area_id"]),
            unit_code=row["unit_code"],
            unit_type=row["unit_type"],
            status=row["status"],
            active_deal_status=row["active_deal_status"],
            deleted_at=row["deleted_at"],
        )
        for row in rows
    ]


@router.get(
    "/absorption/parallel-run",
    response_model=ParallelRunOut,
    summary="Chạy cả hai bộ tính và báo chênh lệch — KHÔNG đổi số liệu sản xuất",
)
async def parallel_run(
    project_id: str = Query(..., description="UUID dự án"),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> ParallelRunOut:
    """Đối chiếu bộ tính cũ và bộ tính mới trên cùng một dự án.

    Thuần đọc. Không ghi `absorption_daily`, không đổi đường đọc của dashboard.
    `production_calculator` trong kết quả cho biết dashboard đang thực sự đọc bộ
    nào. Dashboard hiện đọc domain analytics; legacy chỉ còn để đối chiếu.
    """
    project_uuid, project_external_id = await _resolve_project_scope(project_id, None)
    await _require_project(project_uuid)
    require_project_in_scope(principal, project_external_id)

    report = await ParallelRunComparator().compare(project_uuid)
    return ParallelRunOut(
        project_id=str(report.project_id),
        matches=report.matches,
        legacy_units_sold=report.legacy_units_sold,
        domain_units_sold=report.domain_units_sold,
        legacy_units_remaining=report.legacy_units_remaining,
        domain_units_remaining=report.domain_units_remaining,
        domain_units_reserved=report.domain_units_reserved,
        differences=report.differences,
        anomalies=report.anomalies,
        production_calculator=PRODUCTION_CALCULATOR,
    )


@router.get(
    "/deals",
    response_model=DealList,
    summary="Giao dịch theo từng căn, suy ra từ bản sao CRM (Phase 5.5 P0 — Step 4)",
)
async def list_deals(
    project_id: str | None = Query(default=None, description="UUID dự án"),
    external_project_id: str | None = Query(
        default=None, description="external_id dự án ở Mini CRM — thay thế cho project_id (Phase D)"
    ),
    area_id: str | None = Query(default=None, description="Lọc theo phân khu (UUID nội bộ)"),
    external_area_id: str | None = Query(
        default=None, description="external_id phân khu ở Mini CRM — thay thế cho area_id (Phase D)"
    ),
    unit_id: str | None = Query(default=None, description="Lọc theo căn"),
    deal_status: str | None = Query(default=None, alias="status", description="Lọc theo trạng thái đã ánh xạ"),
    include_deleted: bool = Query(default=False, description="Kèm cả giao dịch đã tombstone"),
    limit: int = Query(default=100, ge=1, le=MAX_UNITS_PER_PAGE),
    offset: int = Query(default=0, ge=0),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> DealList:
    """Cùng quy ước với `GET /inventory`: đọc thẳng `deals`/`units`, KHÔNG qua dữ
    liệu tổng hợp cũ. Đòi tối thiểu `business_viewer` VÀ phạm vi dự án (Phase E)
    — trước đây mở hoàn toàn, xem docstring module.
    """
    project_uuid, project_external_id = await _resolve_project_scope(project_id, external_project_id)
    require_project_in_scope(principal, project_external_id)
    await _require_project(project_uuid)
    area_uuid = await _resolve_area_scope(area_id, external_area_id)
    unit_uuid = _uuid_or_422(unit_id, "unit_id") if unit_id else None

    condition = [areas_table.c.project_id == project_uuid]
    if area_uuid is not None:
        condition.append(units.c.area_id == area_uuid)
    if unit_uuid is not None:
        condition.append(deals.c.unit_id == unit_uuid)
    if deal_status is not None:
        condition.append(deals.c.status == deal_status)
    if not include_deleted:
        condition.append(deals.c.deleted_at.is_(None))

    joined = deals.join(units, deals.c.unit_id == units.c.id).join(areas_table, units.c.area_id == areas_table.c.id)
    query = (
        sa.select(deals)
        .select_from(joined)
        .where(*condition)
        .order_by(deals.c.updated_at.desc(), deals.c.id)
        .limit(limit)
        .offset(offset)
    )
    count_query = sa.select(sa.func.count()).select_from(joined).where(*condition)

    async with get_session_factory()() as session:
        total = await session.scalar(count_query)
        rows = (await session.execute(query)).mappings().all()

    return DealList(
        project_id=str(project_uuid),
        items=[
            DealOut(
                deal_id=str(row["id"]),
                external_deal_id=row["external_deal_id"],
                unit_id=str(row["unit_id"]),
                status=row["status"],
                source_status=row["source_status"],
                reserved_at=row["reserved_at"],
                sold_at=row["sold_at"],
                lost_at=row["lost_at"],
                source_revision=row["source_revision"],
                updated_at=row["updated_at"],
                deleted_at=row["deleted_at"],
            )
            for row in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )
