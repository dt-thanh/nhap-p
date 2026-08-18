"""Router vận hành: trạng thái lineage miền cho giám sát bên ngoài.

Đây là đầu dò cho người TRỰC HỆ THỐNG, không phải cho người dùng và cũng không
phải cho CRM. Nó trả về danh sách `project_id` kèm mức độ lạc hậu của lineage —
đủ để dựng lại bức tranh nội bộ của hệ thống, nên nó được bảo vệ riêng.

**Ba lựa chọn thiết kế, mỗi cái đều có lý do đi kèm.**

1. **Token vận hành riêng, không dùng khoá API của CRM.** Khoá đồng bộ được cấp
   cho MỘT `source_instance_id` và tồn tại để hệ nguồn ghi dữ liệu vào. Dùng nó
   để đọc trạng thái toàn hệ thống sẽ cấp cho mọi hệ nguồn một cái nhìn xuyên
   suốt mà chúng không có lý do gì để có.

2. **Chưa cấu hình token = ĐÓNG (503), không phải mở.** Mặc định mở là cách mọi
   endpoint nội bộ bị lộ: nó chạy được ở máy dev, không ai đặt biến môi trường,
   và nó lên thẳng môi trường thật ở trạng thái công khai.

3. **CHỈ ĐỌC — `repair=False`.** Một `GET` không được đổi trạng thái hệ thống.
   Việc vá do job định kỳ làm; công cụ giám sát nào cũng có thể gọi endpoint này
   nhiều lần một phút, và mỗi lần như thế không được biến thành một loạt job.

Endpoint LUÔN trả HTTP 200 khi đã xác thực, kể cả khi có dự án lạc hậu: mã lỗi
HTTP dành cho lỗi của chính endpoint. Trả 503 khi phát hiện lạc hậu sẽ khiến mọi
công cụ uptime báo "API sập" trong khi API hoàn toàn khoẻ.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException

from src.config import get_settings
from src.logging_config import get_logger
from src.models.schemas import DomainRecomputeAuditOut, StaleProjectOut
from src.services.domain_recompute_audit import find_stale

router = APIRouter(tags=["ops"])
log = get_logger("src.api.ops")


def require_ops_token(token: str | None) -> None:
    """Token vận hành. `compare_digest` để so sánh không rò rỉ theo thời gian.

    Dùng chung cho mọi endpoint vận hành (đầu dò lineage ở 8A, lịch sử chạy song
    song ở 8D). Một chỗ duy nhất quyết định "ai được đọc dữ liệu nội bộ" — hai
    bản sao của quy tắc này sẽ lệch nhau, và bản lỏng hơn là bản sẽ tồn tại.
    """
    settings = get_settings()

    if not settings.ops_endpoint_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Endpoint vận hành chưa được cấu hình (OPS_API_TOKEN rỗng).",
                "error_code": "OPS_ENDPOINT_DISABLED",
            },
        )

    expected = settings.ops_api_token.get_secret_value()
    if not token or not secrets.compare_digest(token, expected):
        # Không phân biệt "thiếu token" với "sai token" trong message: sự khác
        # biệt đó chỉ có ích cho người đang dò.
        raise HTTPException(
            status_code=401,
            detail={"message": "Token vận hành không hợp lệ", "error_code": "INVALID_OPS_TOKEN"},
            headers={"WWW-Authenticate": "X-Ops-Token"},
        )


@router.get(
    "/ops/domain-recompute",
    response_model=DomainRecomputeAuditOut,
    summary="Có dự án nào thiếu lần tính lại lineage miền không (đầu dò giám sát)",
)
async def domain_recompute_status(
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
) -> DomainRecomputeAuditOut:
    """Đọc trạng thái lineage miền. CHỈ ĐỌC, không xếp hàng gì.

    `stale_projects > 0` nghĩa là có lô đồng bộ đã commit mà lần tính lại không
    chạy — xem `docs/crm/domain_recompute_operations.md`. Ở Phase 8 điều đó chưa
    ảnh hưởng tới người dùng vì chưa dự án nào đọc lineage miền; sau khi cắt sang
    thì nó là số liệu sai đang hiển thị.
    """
    require_ops_token(x_ops_token)

    stale = await find_stale()

    return DomainRecomputeAuditOut(
        status="degraded" if stale else "ok",
        stale_projects=len(stale),
        projects=[
            StaleProjectOut(
                project_id=str(project.project_id),
                project_name=project.project_name,
                last_applied_sync_at=project.last_applied_sync_at,
                last_domain_computed_at=project.last_domain_computed_at,
                applied_runs=project.applied_runs,
                never_computed=project.never_computed,
            )
            for project in stale
        ],
        checked_at=datetime.now(UTC),
    )
