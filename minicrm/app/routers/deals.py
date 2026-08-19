"""CRUD giao dịch.

Khác căn ở đúng một điểm, và điểm đó là toàn bộ lý do file này tồn tại riêng:
một giao dịch chỉ được TẠO khi căn của nó đã tồn tại cục bộ VÀ đã lên tới backend.
Cả hai điều kiện kiểm trước khi ghi, nên không có request nào rời khỏi máy và
không có bản ghi nào bị commit rồi bỏ rơi. Xem docstring của `app/crud.py`.

    căn không có cục bộ         → 422 UNIT_NOT_FOUND
    căn có nhưng chưa mirrored  → 409 UNIT_NOT_MIRRORED
    căn đã bị tombstone         → 409 UNIT_TOMBSTONED
    căn đang có giao dịch giữ   → 409 UNIT_ALREADY_HELD

Xác thực GHI (D-14): mọi route ghi đòi tối thiểu `pipeline_operator`, phạm vi
phải chứa dự án suy qua `external_unit_id` (`app/scope.py`) — giao dịch không tự
mang tham chiếu dự án/phân khu (§A3.5), nên phạm vi LUÔN đi qua Unit.
"""

from __future__ import annotations

from app import crud, scope
from app.auth import MiniCrmPrincipal, require_role, require_scope
from app.schemas import DealCreate, DealOut, DealPatch, DealWriteOut
from fastapi import APIRouter, Depends, Query, status

router = APIRouter(prefix="/deals", tags=["deals"])


@router.post(
    "", response_model=DealWriteOut, status_code=status.HTTP_201_CREATED, summary="Tạo giao dịch và đẩy sang backend"
)
async def create_deal(
    body: DealCreate, principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator"))
) -> DealWriteOut:
    require_scope(principal, await scope.project_for_deal_ref(body.model_dump()))
    record, sync = await crud.create_deal(body.model_dump())
    return DealWriteOut(record=DealOut(**record), sync=sync.as_dict())


@router.get("", response_model=list[DealOut], summary="Liệt kê giao dịch")
async def list_deals(include_deleted: bool = Query(default=False)) -> list[DealOut]:
    return [DealOut(**row) for row in await crud.list_deals(include_deleted=include_deleted)]


@router.get("/{external_id}", response_model=DealOut, summary="Đọc một giao dịch")
async def get_deal(external_id: str) -> DealOut:
    return DealOut(**await crud.get_deal(external_id))


@router.patch("/{external_id}", response_model=DealWriteOut, summary="Sửa giao dịch, GIỮ mốc lịch sử")
async def update_deal(
    external_id: str,
    body: DealPatch,
    principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator")),
) -> DealWriteOut:
    """Mốc không gửi thì GIỮ NGUYÊN, và vẫn đi trong phong bì.

    `reserved → sold` chỉ cần gửi `{"deal_status": "sold", "sold_at": ...}`:
    `exclude_unset` giữ `reserved_at` khỏi bị ghi đè, và phong bì được dựng lại
    từ dòng đã ghi nên nó mang cả hai mốc. Đó là chốt A4 của hợp đồng, và nó đúng
    do THIẾT KẾ chứ không do ai nhớ ra.
    """
    require_scope(principal, await scope.project_for_existing_deal(external_id))
    record, sync = await crud.update_deal(external_id, body.model_dump(exclude_unset=True))
    return DealWriteOut(record=DealOut(**record), sync=sync.as_dict())


@router.delete("/{external_id}", response_model=DealWriteOut, summary="Xoá mềm giao dịch và gửi lệnh xoá")
async def delete_deal(
    external_id: str, principal: MiniCrmPrincipal = Depends(require_role("pipeline_operator"))
) -> DealWriteOut:
    require_scope(principal, await scope.project_for_existing_deal(external_id))
    record, sync = await crud.delete_deal(external_id)
    return DealWriteOut(record=DealOut(**record), sync=sync.as_dict())
