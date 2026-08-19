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
`GET /areas/{external_id}` giờ đòi tối thiểu vai trò `business_viewer`
(`require_role`, `src/services/dashboard_auth.py`) VÀ phạm vi dự án — cưỡng chế
ở TẦNG TRUY VẤN (`resolve_scope_project_ids` cho liệt kê không lọc,
`require_project_in_scope` cho các route đã suy ra một dự án cụ thể), không chỉ
ở tầng route. `/absorption`, `/absorption/summary`, ảnh bìa CHƯA được scope ở
đợt này — nằm ngoài danh sách route "tối thiểu" của Phase E, xem
pipeline_status.md.
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
    DomainAbsorptionCalculatorService,
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


@router.get("/absorption", response_model=AbsorptionSeries, summary="Chuỗi tốc độ hấp thụ")
async def absorption_series(
    area_id: str = Query(..., description="UUID phân khu"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    granularity: str = Query(default="day", pattern="^(day|week)$"),
) -> AbsorptionSeries:
    """SRS §5.2: chuỗi theo `area_id`, `from`, `to`, `granularity=day|week`."""
    points = await AreaService().absorption_series(
        _uuid_or_422(area_id, "area_id"),
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
    )
    return AbsorptionSeries(
        area_id=area_id,
        granularity=granularity,
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
    )


@router.get(
    "/absorption/summary",
    response_model=AbsorptionSummaryOut,
    summary="Tổng hợp toàn dự án cho thẻ số liệu",
)
async def absorption_summary(
    project_id: str = Query(..., description="UUID dự án"),
    calculator: str = Query(
        default="legacy_aggregate",
        description="legacy_aggregate (mặc định, đường sản xuất) | domain_units_deals (đối chiếu S3)",
    ),
) -> AbsorptionSummaryOut:
    """SRS §5.2: tồn kho, đã bán, tốc độ trung bình 30 ngày.

    Mặc định vẫn là bộ tính cũ đọc dữ liệu tổng hợp — KHÔNG tự động cắt sang bộ
    tính mới. `calculator=domain_units_deals` là đường ĐỐI CHIẾU, tính từ
    `units`/`deals` và trả thêm `units_reserved` (thứ dữ liệu tổng hợp không dựng
    lại được). Bộ tính mới không ghi gì xuống `absorption_daily`.
    """
    project_uuid = _uuid_or_422(project_id, "project_id")
    last_successful_sync, last_attempted_sync, last_sync_status = await _sync_freshness(project_uuid)

    if calculator == CALCULATOR_DOMAIN:
        result = await DomainAbsorptionCalculatorService().compute(project_uuid)
        newest = max((point.stat_date for point in result.points), default=None)
        return AbsorptionSummaryOut(
            units_remaining=result.units_remaining,
            units_sold=result.units_sold,
            units_reserved=result.units_reserved,
            # Bộ tính mới đếm theo từng căn, không suy ra vận tốc trung bình dự án
            # theo cách của bộ cũ; để 0 thay vì bịa một con số không so được.
            avg_velocity_30d=Decimal("0"),
            updated_at=result.computed_at if newest else None,
            calculator=CALCULATOR_DOMAIN,
            last_successful_sync=last_successful_sync,
            last_attempted_sync=last_attempted_sync,
            last_sync_status=last_sync_status,
        )

    if calculator != CALCULATOR_LEGACY:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Bộ tính '{calculator}' không tồn tại", "error_code": "UNKNOWN_CALCULATOR"},
        )

    summary = await AreaService().summary(project_uuid)
    return AbsorptionSummaryOut(
        units_remaining=summary.units_remaining,
        units_sold=summary.units_sold,
        avg_velocity_30d=summary.avg_velocity_30d,
        updated_at=summary.updated_at,
        # Dữ liệu tổng hợp không dựng lại được số căn đang giữ chỗ — NULL nói đúng
        # điều đó, còn 0 sẽ bị đọc thành "không có căn nào đang giữ".
        units_reserved=None,
        calculator=CALCULATOR_LEGACY,
        last_successful_sync=last_successful_sync,
        last_attempted_sync=last_attempted_sync,
        last_sync_status=last_sync_status,
    )


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
