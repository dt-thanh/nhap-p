"""CRUD căn hộ.

Mã HTTP của thao tác ghi phản ánh KẾT QUẢ CỤC BỘ, không phải kết quả đồng bộ:
201 khi căn được tạo, 200 khi được sửa/xoá mềm — kể cả lúc lần đẩy sau đó hỏng.

Đó không phải là giấu lỗi. Thay đổi cục bộ ĐÃ commit thật, và trả 502 cho một
request đã ghi thành công sẽ khiến người gọi thử lại và tạo ra căn thứ hai cho
cùng một căn hộ. Kết quả đồng bộ nằm ở khối `sync` của body, luôn có mặt, và
`sync.status` nói thẳng `sync_failed`/`sync_pending` khi lô không tới nơi.

Xác thực GHI (D-14): mọi route ghi đòi tối thiểu `pipeline_operator`, phạm vi
phải chứa dự án của căn (qua phân khu, `app/scope.py`). Căn DI SẢN (`area_id IS
NULL`) không suy được dự án — chỉ token phạm vi `ALL` ghi được chúng.
"""

from __future__ import annotations

from app import crud, scope
from app.auth import MiniCrmPrincipal, require_role, require_scope
from app.human_auth import require_resource_visibility
from app.schemas import UnitCreate, UnitOut, UnitPatch, UnitWriteOut
from fastapi import APIRouter, Depends, Query, status

router = APIRouter(prefix="/units", tags=["units"])


@router.post("", response_model=UnitWriteOut, status_code=status.HTTP_201_CREATED, summary="Tạo căn và đẩy sang backend")
async def create_unit(
    body: UnitCreate, principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator"))
) -> UnitWriteOut:
    require_scope(principal, await scope.project_for_unit_ref(body.model_dump()))
    record, sync = await crud.create_unit(body.model_dump())
    return UnitWriteOut(record=UnitOut(**record), sync=sync.as_dict())


@router.get(
    "",
    response_model=list[UnitOut],
    summary="Liệt kê căn",
    dependencies=[Depends(require_resource_visibility)],
)
async def list_units(
    include_deleted: bool = Query(default=False, description="Kèm cả căn đã tombstone"),
) -> list[UnitOut]:
    return [UnitOut(**row) for row in await crud.list_units(include_deleted=include_deleted)]


@router.get(
    "/{external_id}",
    response_model=UnitOut,
    summary="Đọc một căn",
    dependencies=[Depends(require_resource_visibility)],
)
async def get_unit(external_id: str) -> UnitOut:
    return UnitOut(**await crud.get_unit(external_id))


@router.patch("/{external_id}", response_model=UnitWriteOut, summary="Sửa căn, tăng phiên bản, đẩy lại")
async def update_unit(
    external_id: str,
    body: UnitPatch,
    principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator")),
) -> UnitWriteOut:
    require_scope(principal, await scope.project_for_existing_unit(external_id))
    # `exclude_unset` là điểm mấu chốt: nó phân biệt "không gửi khoá này" với
    # "gửi khoá này bằng null". Thiếu nó, một PATCH đổi mỗi trạng thái sẽ ghi
    # NULL đè lên `unit_code` — và cột đó không nhận NULL.
    record, sync = await crud.update_unit(external_id, body.model_dump(exclude_unset=True))
    return UnitWriteOut(record=UnitOut(**record), sync=sync.as_dict())


@router.delete("/{external_id}", response_model=UnitWriteOut, summary="Xoá mềm căn và gửi lệnh xoá")
async def delete_unit(
    external_id: str, principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator"))
) -> UnitWriteOut:
    """Xoá MỀM ở cả hai phía. Lô gửi đi là lệnh `delete` KHÔNG có thân payload.

    Trả 200 kèm bản ghi chứ không phải 204: người gọi cần đọc `sync` để biết
    backend đã tạo tombstone hay chưa, mà 204 thì không có body để đọc.
    """
    require_scope(principal, await scope.project_for_existing_unit(external_id))
    record, sync = await crud.delete_unit(external_id)
    return UnitWriteOut(record=UnitOut(**record), sync=sync.as_dict())
