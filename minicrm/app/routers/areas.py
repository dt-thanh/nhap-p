"""CRUD phân khu, scoped theo dự án.

Mini CRM là TÁC GIẢ của cả năm trường nghiệp vụ, kể cả ba trường kế hoạch
(`bedrooms`/`area_sqm`/`total_units`) — KHÔNG có tiền tố `proposed_`, KHÔNG có
bước duyệt (khác hẳn mô hình đã bị thay thế, xem
`docs/crm/phase_a_domain_freeze.md` §S-2).

    dự án không tồn tại cục bộ      → 422 PROJECT_NOT_FOUND
    dự án đã lưu trữ                → 409 PARENT_ARCHIVED
    trùng (area_name, unit_type)
      trong cùng dự án              → 409 AREA_NATURAL_KEY_CONFLICT

`external_project_id` KHÔNG có trong `AreaPatch` — phân khu không đổi dự án
(bất biến bằng cách vắng mặt ở tầng schema, §A1.6).

Xác thực GHI (D-14): mọi route ghi đòi tối thiểu `pipeline_operator`, và phạm vi
phải chứa đúng dự án của phân khu (hoặc `ALL`) — TẠO suy dự án từ
`external_project_id` trong body, SỬA/LƯU TRỮ suy từ dự án CỦA phân khu đã tồn
tại (`app/scope.py`).
"""

from __future__ import annotations

from app import crud, scope
from app.auth import MiniCrmPrincipal, require_role, require_scope
from app.schemas import AreaCreate, AreaOut, AreaPatch, AreaWriteOut
from fastapi import APIRouter, Depends, Query, status

router = APIRouter(prefix="/areas", tags=["areas"])


@router.post("", response_model=AreaWriteOut, status_code=status.HTTP_201_CREATED, summary="Tạo phân khu dưới một dự án")
async def create_area(
    body: AreaCreate, principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator"))
) -> AreaWriteOut:
    require_scope(principal, body.external_project_id)
    record = await crud.create_area(body.model_dump())
    return AreaWriteOut(record=AreaOut(**record))


@router.get("", response_model=list[AreaOut], summary="Liệt kê phân khu")
async def list_areas(
    external_project_id: str | None = Query(default=None, description="Lọc theo dự án"),
    include_archived: bool = Query(default=False, description="Kèm cả phân khu đã lưu trữ"),
) -> list[AreaOut]:
    rows = await crud.list_areas(external_project_id=external_project_id, include_archived=include_archived)
    return [AreaOut(**row) for row in rows]


@router.get("/{external_id}", response_model=AreaOut, summary="Đọc một phân khu")
async def get_area(external_id: str) -> AreaOut:
    return AreaOut(**await crud.get_area(external_id))


@router.patch("/{external_id}", response_model=AreaWriteOut, summary="Sửa phân khu, tăng phiên bản")
async def update_area(
    external_id: str,
    body: AreaPatch,
    principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator")),
) -> AreaWriteOut:
    require_scope(principal, await scope.project_for_area_external_id(external_id))
    record = await crud.update_area(external_id, body.model_dump(exclude_unset=True))
    return AreaWriteOut(record=AreaOut(**record))


@router.delete("/{external_id}", response_model=AreaWriteOut, summary="Lưu trữ phân khu")
async def archive_area(
    external_id: str, principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator"))
) -> AreaWriteOut:
    """Lưu trữ, không xoá. Từ chối (409 `PARENT_HAS_LIVE_CHILDREN`) nếu còn căn
    đang hoạt động thuộc phân khu này — không cascade."""
    require_scope(principal, await scope.project_for_area_external_id(external_id))
    record = await crud.archive_area(external_id)
    return AreaWriteOut(record=AreaOut(**record))
