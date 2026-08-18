"""CRUD dự án.

Mini CRM là TÁC GIẢ (Phase B, đảo ngược từ mô hình cũ — xem
`docs/crm/phase_a_domain_freeze.md`). Không có `sync` trong phản hồi: KHÔNG có
dòng outbox nào được tạo ở đợt này — nối vào đường đẩy là việc của Phase C.

`DELETE` LƯU TRỮ (`status='archived'`), không xoá vật lý — `projects` không có
cột `deleted_at`. Từ chối nếu còn phân khu đang hoạt động
(`PARENT_HAS_LIVE_CHILDREN`, 409).

Xác thực GHI (D-14, `app/auth.py`): TẠO dự án đòi vai trò `admin` — một dự án MỚI
không nằm trong phạm vi CÓ SẴN của bất kỳ token nào (chính nó là thứ đang được cấp
phạm vi), nên đây là hành động MỞ RỘNG phạm vi, không phải hành động NẰM TRONG
phạm vi — chỉ `admin` mới làm được. SỬA/LƯU TRỮ một dự án đã tồn tại đòi tối
thiểu `pipeline_operator`, VÀ phạm vi phải chứa đúng dự án đó (hoặc `ALL`).
"""

from __future__ import annotations

from app import crud
from app.auth import MiniCrmPrincipal, require_role, require_scope
from app.human_auth import require_resource_visibility
from app.schemas import ProjectCreate, ProjectOut, ProjectPatch, ProjectWriteOut
from fastapi import APIRouter, Depends, Query, status

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectWriteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo dự án",
    dependencies=[Depends(require_role("admin"))],
)
async def create_project(body: ProjectCreate) -> ProjectWriteOut:
    record = await crud.create_project(body.model_dump())
    return ProjectWriteOut(record=ProjectOut(**record))


@router.get(
    "",
    response_model=list[ProjectOut],
    summary="Liệt kê dự án",
    dependencies=[Depends(require_resource_visibility)],
)
async def list_projects(
    include_archived: bool = Query(default=False, description="Kèm cả dự án đã lưu trữ"),
) -> list[ProjectOut]:
    return [ProjectOut(**row) for row in await crud.list_projects(include_archived=include_archived)]


@router.get(
    "/{external_id}",
    response_model=ProjectOut,
    summary="Đọc một dự án",
    dependencies=[Depends(require_resource_visibility)],
)
async def get_project(external_id: str) -> ProjectOut:
    return ProjectOut(**await crud.get_project(external_id))


@router.patch("/{external_id}", response_model=ProjectWriteOut, summary="Sửa dự án, tăng phiên bản")
async def update_project(
    external_id: str,
    body: ProjectPatch,
    principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator")),
) -> ProjectWriteOut:
    require_scope(principal, external_id)
    record = await crud.update_project(external_id, body.model_dump(exclude_unset=True))
    return ProjectWriteOut(record=ProjectOut(**record))


@router.delete("/{external_id}", response_model=ProjectWriteOut, summary="Lưu trữ dự án")
async def archive_project(
    external_id: str, principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator"))
) -> ProjectWriteOut:
    """Lưu trữ, không xoá. Từ chối (409 `PARENT_HAS_LIVE_CHILDREN`) nếu còn phân
    khu đang hoạt động thuộc dự án này — không cascade."""
    require_scope(principal, external_id)
    record = await crud.archive_project(external_id)
    return ProjectWriteOut(record=ProjectOut(**record))
