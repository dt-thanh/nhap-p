"""Router dashboard MVP 1 (SRS §5.2) + đọc/sửa nội dung hiển thị dự án / phân khu.

Bốn endpoint đọc gốc: dự án, phân khu, chuỗi hấp thụ, thẻ tổng hợp.
`absorption_daily` không ghi ở đây — worker tính lại sau mỗi lần nạp.

═══════════════════════════════════════════════════════════════════════════
 PHASE D (§D7/§D8): KHÔNG còn POST /projects, POST /areas
═══════════════════════════════════════════════════════════════════════════

Hai route TẠO đã bị XOÁ — Mini CRM là nguồn sự thật cho Project/Area từ Phase D
(`src/services/domain_projection.py`); tạo qua ingestion (`POST /sync/{entity}`),
không qua đây nữa. `PATCH /projects/{id}`/`PATCH /areas/{id}` VẪN CÒN nhưng bị
THU HẸP xuống `headline`/`introduce` — xem `src/services/projects.py`.

MỚI (§D8, đọc theo `external_id` — danh tính Mini CRM, khác UUID nội bộ):
`GET /projects/{external_id}`, `GET /areas/{external_id}`. `GET /projects`/
`GET /areas` (đã có, theo UUID nội bộ) giữ nguyên hành vi, chỉ THÊM hai trường
`external_id`/`source_revision` vào response — cộng thêm, không phá phía đọc cũ.

═══════════════════════════════════════════════════════════════════════════
 PHASE E (§A7): phạm vi dự án cho route ĐỌC
═══════════════════════════════════════════════════════════════════════════

`GET /projects`, `GET /projects/{external_id}`, `GET /areas`,
`GET /areas/{external_id}` và các endpoint absorption giờ đòi tối thiểu vai trò
`business_viewer` (`require_role`, `src/services/dashboard_auth.py`) VÀ phạm vi
dự án — cưỡng chế ở TẦNG TRUY VẤN (`resolve_scope_project_ids` cho liệt kê
không lọc, `require_project_in_scope` cho các route đã suy ra một dự án cụ thể).
Endpoint absorption suy phạm vi qua `area_id`/`project_id` trước khi đọc domain.
"""

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import (
    AbsorptionPointOut,
    AbsorptionSeries,
    AbsorptionSummaryOut,
    AreaDetail,
    AreaOut,
    AreaUpdate,
    ImageDetail,
    MePermissionsOut,
    ProjectDetail,
    ProjectSummary,
    ProjectUpdate,
)
from src.models.tables import areas as areas_table
from src.models.tables import projects, upload_files
from src.services.absorption import AreaService
from src.services.dashboard_auth import (
    DashboardPrincipal,
    require_project_in_scope,
    require_role,
    resolve_scope_project_ids,
)
from src.services.domain_absorption import (
    CALCULATOR_DOMAIN,
    CALCULATOR_LEGACY,
    DomainSalesAnalyticsService,
)
from src.services.images import (
    AREA_OWNER,
    PROJECT_OWNER,
    ImageOwner,
    ImageRejectedError,
    ImageService,
)
from src.services.projects import CatalogRejectedError, ProjectService, as_dict

require_viewer = require_role("business_viewer")

router = APIRouter(tags=["dashboard"])
log = get_logger("src.api.dashboard")

# error_code của service → mã HTTP. Router giữ ánh xạ này để service không phải
# biết gì về HTTP, giống cách `src/api/files.py` xử lý UploadRejectedError.
#
# `PROJECT_NOT_ACTIVE`/`DUPLICATE_AREA` (của `create_project`/`create_area` cũ)
# KHÔNG còn ở đây — hai lỗi đó thuộc về đường TẠO đã bị xoá ở Phase D (§D7).
_CATALOG_STATUS = {
    "PROJECT_NOT_FOUND": 404,
    "AREA_NOT_FOUND": 404,
    "NO_CHANGES": 422,
    "FIELD_NOT_EDITABLE": 422,
}


def _catalog_http_error(exc: CatalogRejectedError) -> HTTPException:
    return HTTPException(
        status_code=_CATALOG_STATUS.get(exc.error_code, 422),
        detail={"message": exc.message, "error_code": exc.error_code},
    )


# Phase 5.5 P0 (F-2, Bước 1B). Hai trạng thái kết thúc KHÔNG có bản ghi hỏng nào
# — đụng độ được ghi nhận vẫn là "thành công" theo nghĩa "backend có bằng chứng
# đã nhận và xử lý xong lô", khác hẳn "thất bại"/"dở dang". Xem sync_runs.py 5A.
_SUCCESSFUL_SYNC_STATUSES = ("completed", "completed_with_conflicts")


async def _sync_freshness(project_id: uuid.UUID) -> tuple[object, object, str | None]:
    """Bằng chứng đồng bộ CRM THẬT của backend — KHÔNG phải đồng hồ trình duyệt.

    Ba câu hỏi khác nhau, đọc từ CÙNG bảng `upload_files` (`transport_mode=
    'api_push'`, lô file tải tay không tính):

    - `last_attempted_sync`: lô CRM gần nhất được GHI NHẬN, dù kết quả ra sao —
      lấy `uploaded_at` vì lô `pending`/`processing` chưa có `finished_at`.
    - `last_successful_sync`: lô CRM gần nhất KẾT THÚC không có bản ghi hỏng nào
      — lấy `finished_at` (mốc backend THỰC SỰ xử lý xong, khác `uploaded_at` là
      mốc NHẬN).
    - `last_sync_status`: trạng thái của lần gần nhất — để frontend biết lần đó
      thất bại hay không MÀ KHÔNG PHẢI suy từ việc so hai timestamp trên.

    Trả `(None, None, None)` khi dự án CHƯA TỪNG có lô CRM nào — "chưa từng đồng
    bộ" phải phân biệt được với "có đồng bộ nhưng backend không biết khi nào".
    """
    async with get_session_factory()() as session:
        latest = (
            (
                await session.execute(
                    sa.select(upload_files.c.uploaded_at, upload_files.c.status)
                    .where(upload_files.c.project_id == project_id, upload_files.c.transport_mode == "api_push")
                    .order_by(upload_files.c.uploaded_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if latest is None:
            return None, None, None

        last_success = await session.scalar(
            sa.select(sa.func.max(upload_files.c.finished_at)).where(
                upload_files.c.project_id == project_id,
                upload_files.c.transport_mode == "api_push",
                upload_files.c.status.in_(_SUCCESSFUL_SYNC_STATUSES),
            )
        )

    return last_success, latest["uploaded_at"], latest["status"]


def _uuid_or_422(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": f"{field} phải là UUID hợp lệ", "error_code": "INVALID_UUID"},
        ) from exc


async def _resolve_project_scope(project_id: str | None, external_project_id: str | None) -> tuple[uuid.UUID, str | None]:
    """Đúng MỘT trong hai tham số phạm vi — UUID nội bộ hoặc `external_id` Mini
    CRM (Phase D). Cả hai cùng có/cùng vắng đều bị từ chối tường minh, không đoán.

    Trả kèm `external_id` của dự án (dù truy vấn bằng hình dạng nào) — Phase E
    cần nó để kiểm phạm vi; `None` là dự án DI SẢN chưa có danh tính nguồn.
    """
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


async def _resolve_analytics_scope(
    project_id: uuid.UUID | None,
    area_id: uuid.UUID | None,
    principal: DashboardPrincipal,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Resolve and authorize the canonical analytics scope before querying data."""
    async with get_session_factory()() as session:
        if area_id is not None:
            row = (
                await session.execute(
                    sa.select(areas_table.c.project_id, projects.c.external_id)
                    .select_from(areas_table.join(projects, areas_table.c.project_id == projects.c.id))
                    .where(areas_table.c.id == area_id)
                )
            ).one_or_none()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail={"message": "Không tìm thấy phân khu", "error_code": "AREA_NOT_FOUND"},
                )
            if project_id is not None and row.project_id != project_id:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "Phân khu không thuộc dự án", "error_code": "AREA_PROJECT_MISMATCH"},
                )
            require_project_in_scope(principal, row.external_id)
            return row.project_id, area_id

        if project_id is None:
            raise HTTPException(
                status_code=422,
                detail={"message": "Cần project_id hoặc area_id để xác định phạm vi", "error_code": "MISSING_SCOPE"},
            )
        external_id = await session.scalar(sa.select(projects.c.external_id).where(projects.c.id == project_id))
        if external_id is None:
            raise HTTPException(
                status_code=404,
                detail={"message": "Không tìm thấy dự án", "error_code": "PROJECT_NOT_FOUND"},
            )
        require_project_in_scope(principal, external_id)
        return project_id, None


@router.get(
    "/me/permissions",
    response_model=MePermissionsOut,
    summary="Vai trò + phạm vi dự án của token hiện tại — CHỈ để FE hiển thị",
)
async def me_permissions(principal: DashboardPrincipal = Depends(require_viewer)) -> MePermissionsOut:
    scope = "ALL" if principal.project_scope == "ALL" else sorted(principal.project_scope)
    return MePermissionsOut(role=principal.role, project_scope=scope)


@router.get("/projects", response_model=list[ProjectSummary], summary="Danh sách dự án")
async def list_projects(principal: DashboardPrincipal = Depends(require_viewer)) -> list[ProjectSummary]:
    """Frontend cần để chọn phạm vi upload và dashboard. Chỉ trả dự án trong
    phạm vi của `principal` — dự án DI SẢN (`external_id IS NULL`) chỉ hiện với
    phạm vi `ALL` (`external_id IN (...)` không bao giờ khớp NULL)."""
    async with get_session_factory()() as session:
        scope_ids = await resolve_scope_project_ids(session, principal)
        query = sa.select(projects).order_by(projects.c.name)
        if scope_ids != "ALL":
            if not scope_ids:
                return []
            query = query.where(projects.c.id.in_(scope_ids))
        rows = (await session.execute(query)).all()
    return [
        ProjectSummary(
            project_id=str(row.id),
            name=row.name,
            launch_date=row.launch_date,
            status=row.status,
            headline=row.headline,
            introduce=row.introduce,
            cover_image_url=row.cover_image_url,
            external_id=row.external_id,
            source_revision=row.source_revision,
        )
        for row in rows
    ]


@router.get(
    "/projects/{external_id}",
    response_model=ProjectDetail,
    summary="Một dự án theo external_id (danh tính Mini CRM) — Phase D",
)
async def get_project_by_external_id(
    external_id: str, principal: DashboardPrincipal = Depends(require_viewer)
) -> ProjectDetail:
    """Đọc theo `external_id`, KHÔNG theo UUID nội bộ — dùng khi phía gọi chỉ
    biết danh tính Mini CRM (ví dụ theo dõi một dự án cụ thể xuyên đồng bộ).

    Không lọc theo `source_instance_id`: `external_id` hiện tại chỉ duy nhất
    trong phạm vi MỘT instance nguồn (Mini CRM), và hệ thống mới có đúng một
    instance được cấu hình — mở rộng khi có instance thứ hai.

    Kiểm phạm vi TRƯỚC KHI kiểm tồn tại: một dự án ngoài phạm vi trả 403, không
    phải 404 — 404 sẽ nói dối rằng dự án không tồn tại.
    """
    require_project_in_scope(principal, external_id)
    async with get_session_factory()() as session:
        row = (
            await session.execute(sa.select(projects).where(projects.c.external_id == external_id))
        ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Không tìm thấy dự án '{external_id}'", "error_code": "PROJECT_NOT_FOUND"},
        )
    return ProjectDetail(
        project_id=str(row.id),
        name=row.name,
        launch_date=row.launch_date,
        status=row.status,
        headline=row.headline,
        introduce=row.introduce,
        cover_image_url=row.cover_image_url,
        created_at=row.created_at,
        external_id=row.external_id,
        source_revision=row.source_revision,
    )


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectDetail,
    summary="Sửa thông tin dự án",
)
async def update_project(project_id: str, payload: ProjectUpdate) -> ProjectDetail:
    """Chỉ ghi những trường có mặt trong body — trường vắng mặt giữ nguyên."""
    changes = payload.model_dump(exclude_unset=True)
    try:
        record = await ProjectService().update_project(_uuid_or_422(project_id, "project_id"), changes)
    except CatalogRejectedError as exc:
        raise _catalog_http_error(exc) from exc
    return ProjectDetail(**as_dict(record))


@router.patch(
    "/areas/{area_id}",
    response_model=AreaDetail,
    summary="Sửa thông tin phân khu",
)
async def update_area(area_id: str, payload: AreaUpdate) -> AreaDetail:
    """Không đổi được `project_id` lẫn `status` — hai trường đó không có trong body."""
    changes = payload.model_dump(exclude_unset=True)
    try:
        record = await ProjectService().update_area(_uuid_or_422(area_id, "area_id"), changes)
    except CatalogRejectedError as exc:
        raise _catalog_http_error(exc) from exc
    return AreaDetail(**as_dict(record))


@router.get("/areas", response_model=list[AreaOut], summary="Phân khu kèm tồn kho hiện tại")
async def list_areas(
    project_id: str | None = Query(default=None, description="UUID dự án"),
    external_project_id: str | None = Query(
        default=None, description="external_id dự án ở Mini CRM — thay thế cho project_id (Phase D)"
    ),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> list[AreaOut]:
    """SRS §5.2: danh sách phân khu / loại căn kèm tồn kho mới nhất.

    Đúng MỘT trong hai tham số phạm vi phải có mặt — cùng nguyên tắc với
    `project_ref` của hợp đồng đồng bộ: mơ hồ thì từ chối, không đoán.
    """
    project_uuid, project_external_id = await _resolve_project_scope(project_id, external_project_id)
    require_project_in_scope(principal, project_external_id)
    areas = await AreaService().list_areas(project_uuid)
    return [
        AreaOut(
            area_id=area.area_id,
            area_name=area.area_name,
            unit_type=area.unit_type,
            bedrooms=area.bedrooms,
            area_sqm=area.area_sqm,
            total_units=area.total_units,
            units_remaining=area.units_remaining,
            snapshot_date=area.snapshot_date,
            headline=area.headline,
            introduce=area.introduce,
            cover_image_url=area.cover_image_url,
            external_id=area.external_id,
            source_revision=area.source_revision,
        )
        for area in areas
    ]


@router.get(
    "/areas/{external_id}",
    response_model=AreaDetail,
    summary="Một phân khu theo external_id (danh tính Mini CRM) — Phase D",
)
async def get_area_by_external_id(
    external_id: str, principal: DashboardPrincipal = Depends(require_viewer)
) -> AreaDetail:
    """Suy `external_id` của DỰ ÁN qua JOIN trước khi trả lời — một phân khu suy
    ra dự án qua tham chiếu, không tự mang danh tính dự án (§A7.3: đường JOIN
    không phải cửa hậu qua mặt phạm vi)."""
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(areas_table, projects.c.external_id.label("project_external_id"))
                .select_from(areas_table.join(projects, areas_table.c.project_id == projects.c.id))
                .where(areas_table.c.external_id == external_id)
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Không tìm thấy phân khu '{external_id}'", "error_code": "AREA_NOT_FOUND"},
        )
    require_project_in_scope(principal, row.project_external_id)
    return AreaDetail(
        area_id=str(row.id),
        project_id=str(row.project_id),
        area_name=row.area_name,
        unit_type=row.unit_type,
        bedrooms=row.bedrooms,
        area_sqm=row.area_sqm,
        total_units=row.total_units,
        status=row.status,
        headline=row.headline,
        introduce=row.introduce,
        cover_image_url=row.cover_image_url,
        created_at=row.created_at,
        external_id=row.external_id,
        source_revision=row.source_revision,
    )


@router.get("/absorption", response_model=AbsorptionSeries, summary="Chuỗi tốc độ hấp thụ theo dữ liệu căn hộ/giao dịch")
async def absorption_series(
    area_id: str | None = Query(default=None, description="UUID phân khu; bỏ trống để tính toàn dự án"),
    project_id: str | None = Query(default=None, description="UUID dự án khi tính toàn dự án hoặc để kiểm scope"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    year: int | None = Query(default=None, ge=1900, le=3000),
    unit_type: str | None = Query(default=None),
    granularity: str | None = Query(default=None, pattern="^(day|week|month)$"),
    calculator: str = Query(default=CALCULATOR_DOMAIN),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> AbsorptionSeries:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail={"message": "from phải trước hoặc bằng to", "error_code": "INVALID_DATE_RANGE"},
        )
    if year is not None and (date_from is not None or date_to is not None):
        raise HTTPException(
            status_code=422,
            detail={"message": "year không thể dùng cùng from/to", "error_code": "AMBIGUOUS_DATE_RANGE"},
        )

    area_uuid = _uuid_or_422(area_id, "area_id") if area_id else None
    project_uuid = _uuid_or_422(project_id, "project_id") if project_id else None
    project_uuid, area_uuid = await _resolve_analytics_scope(project_uuid, area_uuid, principal)

    if calculator == CALCULATOR_DOMAIN:
        try:
            result = await DomainSalesAnalyticsService().trend(
                project_uuid,
                area_id=area_uuid,
                unit_type=unit_type,
                date_from=date_from,
                date_to=date_to,
                year=year,
                granularity=granularity,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "error_code": "INVALID_ANALYTICS_RANGE"},
            ) from exc
        return AbsorptionSeries(
            area_id=str(area_uuid) if area_uuid else None,
            granularity=result.granularity,
            points=[
                AbsorptionPointOut(
                    stat_date=point.stat_date,
                    units_sold=point.units_sold,
                    velocity_7d=point.velocity_7d,
                    velocity_30d=point.velocity_30d,
                    is_observed=point.is_observed,
                    data_quality_status=point.data_quality_status,
                    period_start=point.stat_date,
                    period_end=point.period_end,
                    period_granularity=point.granularity,
                    cumulative_sold=point.cumulative_sold,
                    sell_through=point.sell_through,
                )
                for point in result.points
            ],
            data_source=CALCULATOR_DOMAIN,
            data_status=result.data_status,
            message=result.message,
            earliest_sale_date=result.earliest_sale_date,
            latest_sale_date=result.latest_sale_date,
            available_years=result.available_years,
        )

    if calculator != CALCULATOR_LEGACY:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Bộ tính '{calculator}' không tồn tại", "error_code": "UNKNOWN_CALCULATOR"},
        )
    if area_uuid is None:
        raise HTTPException(
            status_code=422,
            detail={"message": "Legacy absorption cần area_id", "error_code": "MISSING_AREA_ID"},
        )
    legacy_granularity = granularity or "day"
    if legacy_granularity not in {"day", "week"}:
        raise HTTPException(
            status_code=422,
            detail={"message": "Legacy absorption chỉ hỗ trợ day hoặc week", "error_code": "UNSUPPORTED_GRANULARITY"},
        )
    points = await AreaService().absorption_series(
        area_uuid,
        date_from=date_from,
        date_to=date_to,
        granularity=legacy_granularity,
    )
    return AbsorptionSeries(
        area_id=str(area_uuid),
        granularity=legacy_granularity,
        points=[
            AbsorptionPointOut(
                stat_date=point.stat_date,
                units_sold=point.units_sold,
                velocity_7d=point.velocity_7d,
                velocity_30d=point.velocity_30d,
                is_observed=point.is_observed,
                data_quality_status=point.data_quality_status,
            )
            for point in points
        ],
        data_source=CALCULATOR_LEGACY,
        data_status="ready" if points else "no_data",
        message=None if points else "Không có dữ liệu trong khoảng thời gian đã chọn.",
    )


@router.get(
    "/absorption/summary",
    response_model=AbsorptionSummaryOut,
    summary="Tổng hợp dashboard theo project/area scope",
)
async def absorption_summary(
    project_id: str = Query(..., description="UUID dự án"),
    area_id: str | None = Query(default=None, description="UUID phân khu; bỏ trống để tổng hợp toàn dự án"),
    calculator: str = Query(
        default=CALCULATOR_DOMAIN,
        description="domain_units_deals (đường dashboard) | legacy_aggregate (tương thích có chỉ định)",
    ),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> AbsorptionSummaryOut:
    """Trả KPI cùng một scope với trend: dự án hoặc phân khu đã chọn.

    Mặc định dùng bộ tính domain đọc `units`/`deals`; `legacy_aggregate` chỉ còn
    là compatibility path khi caller chỉ định tường minh. Dashboard frontend
    không sử dụng đường legacy và không trộn hai nguồn trong cùng metric.
    """
    project_uuid = _uuid_or_422(project_id, "project_id")
    area_uuid = _uuid_or_422(area_id, "area_id") if area_id else None
    project_uuid, area_uuid = await _resolve_analytics_scope(project_uuid, area_uuid, principal)
    last_successful_sync, last_attempted_sync, last_sync_status = await _sync_freshness(project_uuid)

    if calculator == CALCULATOR_DOMAIN:
        result = await DomainSalesAnalyticsService().summary(
            project_uuid,
            area_id=area_uuid,
        )
        return AbsorptionSummaryOut(
            units_remaining=result.units_remaining,
            units_sold=result.units_sold,
            units_reserved=result.units_reserved,
            available_remaining_units=max(result.units_remaining - result.units_reserved, 0),
            avg_velocity_30d=result.velocity_30d,
            total_units=result.total_units,
            sell_through=result.sell_through,
            velocity_7d=result.velocity_7d,
            velocity_30d=result.velocity_30d,
            estimated_weeks_to_sell_out=result.estimated_weeks_to_sell_out,
            updated_at=result.updated_at,
            calculator=CALCULATOR_DOMAIN,
            last_successful_sync=last_successful_sync,
            last_attempted_sync=last_attempted_sync,
            last_sync_status=last_sync_status,
            data_source=CALCULATOR_DOMAIN,
            data_status=result.data_status,
            message=result.message,
            earliest_sale_date=result.earliest_sale_date,
            latest_sale_date=result.latest_sale_date,
            available_years=result.available_years,
            velocity_unit="units_per_week",
        )

    if calculator != CALCULATOR_LEGACY:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Bộ tính '{calculator}' không tồn tại", "error_code": "UNKNOWN_CALCULATOR"},
        )

    summary = await AreaService().summary(project_uuid, area_id=area_uuid)
    metrics = _dashboard_metric_fields(
        total_units=summary.total_units,
        units_sold=summary.units_sold,
        units_remaining=summary.units_remaining,
        velocity_30d=summary.velocity_30d,
    )
    return AbsorptionSummaryOut(
        units_remaining=summary.units_remaining,
        units_sold=summary.units_sold,
        avg_velocity_30d=summary.avg_velocity_30d,
        total_units=summary.total_units,
        sell_through=metrics["sell_through"],
        velocity_7d=summary.velocity_7d,
        velocity_30d=summary.velocity_30d,
        estimated_weeks_to_sell_out=metrics["estimated_weeks_to_sell_out"],
        updated_at=summary.updated_at,
        # Dữ liệu tổng hợp không dựng lại được số căn đang giữ chỗ — NULL nói đúng
        # điều đó, còn 0 sẽ bị đọc thành "không có căn nào đang giữ".
        units_reserved=None,
        available_remaining_units=None,
        calculator=CALCULATOR_LEGACY,
        last_successful_sync=last_successful_sync,
        last_attempted_sync=last_attempted_sync,
        last_sync_status=last_sync_status,
        data_source=CALCULATOR_LEGACY,
        data_status="ready" if summary.units_sold > 0 else ("no_units" if summary.total_units == 0 else "no_data"),
        message=None if summary.units_sold > 0 else "Không có dữ liệu trong khoảng thời gian đã chọn.",
        velocity_unit="units_per_day",
    )


def _dashboard_metric_fields(*, total_units, units_sold, units_remaining, velocity_30d):
    """Calculate only derived metrics whose source values are available.

    Stored rolling velocities are in units/day. Forecast time is expressed in
    weeks, so the denominator is the 30-day velocity converted to units/week.
    """
    total = Decimal(str(total_units)) if total_units is not None else None
    sold = Decimal(str(units_sold)) if units_sold is not None else None
    remaining = Decimal(str(units_remaining)) if units_remaining is not None else None
    velocity = Decimal(str(velocity_30d)) if velocity_30d is not None else None

    if total is not None and total < 0:
        total = None
    if sold is not None and sold < 0:
        sold = None
    if remaining is not None and remaining < 0:
        remaining = None

    sell_through = (sold / total * Decimal("100")) if total is not None and total > 0 and sold is not None else None
    if remaining is None:
        estimated = None
    elif remaining <= 0:
        estimated = Decimal("0")
    elif velocity is None or velocity <= 0:
        estimated = None
    else:
        estimated = (remaining / (velocity * Decimal("7"))).quantize(Decimal("0.0001"))
    return {"sell_through": sell_through, "estimated_weeks_to_sell_out": estimated}


# --- Ảnh bìa dự án / phân khu ----------------------------------------------
# Bốn thao tác × hai thực thể, nhưng chỉ có MỘT bộ handler: route của area và
# project chỉ khác nhau ở `ImageOwner` truyền vào. Nhân đôi ra tám hàm sẽ tạo tám
# chỗ để lệch nhau khi quy tắc đổi.

_IMAGE_STATUS = {
    "PROJECT_NOT_FOUND": 404,
    "AREA_NOT_FOUND": 404,
    "IMAGE_NOT_FOUND": 404,
    "IMAGE_ALREADY_EXISTS": 409,
    "UNSUPPORTED_IMAGE_FORMAT": 415,
    "IMAGE_TOO_LARGE": 413,
    "EMPTY_IMAGE": 422,
    # Cấu hình thiếu hoặc Cloudinary hỏng là sự cố hạ tầng, không phải lỗi của
    # người dùng — trả 503 để client biết thử lại chứ không sửa request.
    "STORAGE_NOT_CONFIGURED": 503,
    "STORAGE_UPLOAD_FAILED": 502,
    "STORAGE_DELETE_FAILED": 502,
}


def _image_http_error(exc: ImageRejectedError) -> HTTPException:
    return HTTPException(
        status_code=_IMAGE_STATUS.get(exc.error_code, 422),
        detail={"message": exc.message, "error_code": exc.error_code},
    )


async def _get_image(owner: ImageOwner, owner_id: str) -> ImageDetail:
    try:
        record = await ImageService().get(owner, _uuid_or_422(owner_id, f"{owner.kind}_id"))
    except ImageRejectedError as exc:
        raise _image_http_error(exc) from exc
    return ImageDetail(owner_id=record.owner_id, url=record.url, public_id=record.public_id)


async def _put_image(owner: ImageOwner, owner_id: str, file: UploadFile, *, replace: bool) -> ImageDetail:
    owner_uuid = _uuid_or_422(owner_id, f"{owner.kind}_id")
    service = ImageService()
    action = service.replace if replace else service.create
    try:
        record = await action(owner, owner_uuid, file, file.filename or "")
    except ImageRejectedError as exc:
        raise _image_http_error(exc) from exc
    return ImageDetail(owner_id=record.owner_id, url=record.url, public_id=record.public_id)


async def _delete_image(owner: ImageOwner, owner_id: str) -> Response:
    try:
        await ImageService().delete(owner, _uuid_or_422(owner_id, f"{owner.kind}_id"))
    except ImageRejectedError as exc:
        raise _image_http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/image", response_model=ImageDetail, summary="Xem ảnh bìa dự án")
async def get_project_image(project_id: str) -> ImageDetail:
    return await _get_image(PROJECT_OWNER, project_id)


@router.post(
    "/projects/{project_id}/image",
    response_model=ImageDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Tải ảnh bìa dự án (chưa có ảnh)",
)
async def create_project_image(project_id: str, file: UploadFile = File(...)) -> ImageDetail:
    return await _put_image(PROJECT_OWNER, project_id, file, replace=False)


@router.put("/projects/{project_id}/image", response_model=ImageDetail, summary="Thay ảnh bìa dự án")
async def replace_project_image(project_id: str, file: UploadFile = File(...)) -> ImageDetail:
    return await _put_image(PROJECT_OWNER, project_id, file, replace=True)


@router.delete(
    "/projects/{project_id}/image",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá ảnh bìa dự án",
)
async def delete_project_image(project_id: str) -> Response:
    return await _delete_image(PROJECT_OWNER, project_id)


@router.get("/areas/{area_id}/image", response_model=ImageDetail, summary="Xem ảnh bìa phân khu")
async def get_area_image(area_id: str) -> ImageDetail:
    return await _get_image(AREA_OWNER, area_id)


@router.post(
    "/areas/{area_id}/image",
    response_model=ImageDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Tải ảnh bìa phân khu (chưa có ảnh)",
)
async def create_area_image(area_id: str, file: UploadFile = File(...)) -> ImageDetail:
    return await _put_image(AREA_OWNER, area_id, file, replace=False)


@router.put("/areas/{area_id}/image", response_model=ImageDetail, summary="Thay ảnh bìa phân khu")
async def replace_area_image(area_id: str, file: UploadFile = File(...)) -> ImageDetail:
    return await _put_image(AREA_OWNER, area_id, file, replace=True)


@router.delete(
    "/areas/{area_id}/image",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá ảnh bìa phân khu",
)
async def delete_area_image(area_id: str) -> Response:
    return await _delete_image(AREA_OWNER, area_id)
