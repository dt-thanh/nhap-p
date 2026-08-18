"""Suy ra dự án MỤC TIÊU của một lần ghi TRƯỚC khi ghi, để kiểm phạm vi (D-14).

Chỉ ĐỌC — không có quy tắc nghiệp vụ nào ở đây, chỉ tra cứu. Quy tắc thật (dự án
không tồn tại, dự án đã lưu trữ, phân khu sai dự án, ...) vẫn ở `crud.py`, chạy
SAU khi qua được cổng phạm vi này — cổng phạm vi chỉ trả lời "ai được thấy/chạm
dự án nào", không thay cho một điều kiện nghiệp vụ nào; một giá trị trả về từ đây
có thể trỏ vào một dự án/phân khu/căn KHÔNG tồn tại, `crud.py` sẽ từ chối nó bằng
mã lỗi đúng của chính nó như hôm nay.

Trả `None` khi không suy ra được dự án (căn/giao dịch DI SẢN không có phân khu cục
bộ, hoặc tham chiếu trong body rỗng/không hợp lệ) — `auth.require_scope` coi `None`
là "chỉ phạm vi ALL mới đủ", vì gán một phạm vi cụ thể cho một bản ghi không xác
định được dự án là ĐOÁN, đúng thứ toàn bộ Mini CRM/backend đều từ chối làm (§A0).
"""

from __future__ import annotations

import sqlalchemy as sa
from app.db import get_session_factory
from app.models import crm_areas, crm_deals, crm_projects, crm_units


async def project_for_area_external_id(external_area_id: str) -> str | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(crm_projects.c.external_id)
            .select_from(crm_areas.join(crm_projects, crm_areas.c.project_id == crm_projects.c.id))
            .where(crm_areas.c.external_id == external_area_id)
        )


async def project_for_existing_unit(unit_external_id: str) -> str | None:
    """`None` nếu căn không tồn tại HOẶC tồn tại nhưng `area_id IS NULL` (di sản)
    — cả hai trường hợp đều không có một dự án CỤ THỂ nào để trao phạm vi."""
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(crm_projects.c.external_id)
            .select_from(
                crm_units.join(crm_areas, crm_units.c.area_id == crm_areas.c.id).join(
                    crm_projects, crm_areas.c.project_id == crm_projects.c.id
                )
            )
            .where(crm_units.c.external_id == unit_external_id)
        )


async def project_for_existing_deal(deal_external_id: str) -> str | None:
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(crm_projects.c.external_id)
            .select_from(
                crm_deals.join(crm_units, crm_deals.c.external_unit_id == crm_units.c.external_id)
                .join(crm_areas, crm_units.c.area_id == crm_areas.c.id)
                .join(crm_projects, crm_areas.c.project_id == crm_projects.c.id)
            )
            .where(crm_deals.c.external_id == deal_external_id)
        )


async def project_for_unit_ref(data: dict) -> str | None:
    """Tạo Unit MỚI: suy dự án từ tham chiếu phân khu trong body. Chỉ hỗ trợ
    `external_area_id` — hình dạng ổn định (§A3.5); hình dạng cũ `area_name` +
    `unit_type` không mang đủ thông tin để tra phạm vi an toàn (tên có thể trùng
    giữa nhiều dự án), nên bị coi là `None` (⇒ đòi ALL) thay vì đoán."""
    external_area_id = data.get("external_area_id")
    if not external_area_id:
        return None
    return await project_for_area_external_id(external_area_id)


async def project_for_deal_ref(data: dict) -> str | None:
    external_unit_id = data.get("external_unit_id")
    if not external_unit_id:
        return None
    return await project_for_existing_unit(external_unit_id)
