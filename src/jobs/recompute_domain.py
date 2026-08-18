"""Job tính lại chuỗi hấp thụ của bộ tính MIỀN sau khi một lô đồng bộ đổi bản sao.

Chạy ngoài request cycle vì cùng lý do với `parse_upload`: tính lại là việc tốn
CPU và I/O, giữ trong request sẽ chiếm event loop của uvicorn. Khác một điểm quan
trọng — lô đồng bộ đã COMMIT trước khi job này được xếp hàng, nên job hỏng KHÔNG
làm mất dữ liệu nghiệp vụ; nó chỉ khiến lineage dẫn xuất lạc hậu.

**Chỉ ghi lineage `domain_units_deals`.** Dòng `legacy_aggregate` — thứ dashboard
đang thực sự phục vụ — không bao giờ bị đọc, sửa hay xoá ở đây. Bảo vệ nằm ở
phạm vi câu DELETE trong `persist()` (xem 0012 và Phase 6).

**Idempotent theo thiết kế.** `persist()` xoá-rồi-ghi trong một transaction, giới
hạn đúng lineage và đúng phạm vi phân khu. Chạy lại cùng tham số cho ra nội dung
y hệt; chỉ `computation_id` và `computed_at` đổi. Nhờ vậy RQ retry an toàn và
script phục hồi có thể xếp lại hàng thoải mái mà không sợ nhân đôi.

**Phạm vi phân khu** được phía gọi truyền vào và đã được thu thập TRƯỚC khi lọc
bỏ bản ghi tombstone — xem `SyncRunService._affected_area_ids`. Một căn bị xoá
mềm vẫn làm đổi số học tồn kho của phân khu chứa nó; thu thập sau khi lọc thì một
phân khu vừa mất hết căn sẽ không bao giờ được tính lại nữa.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from rq import get_current_job

from src.logging_config import get_logger, job_id_var
from src.services.domain_absorption import DomainAbsorptionCalculatorService

log = get_logger("src.jobs.recompute_domain")


def run_domain_recompute(
    project_id: str,
    *,
    area_ids: list[str] | None = None,
    sync_run_id: str | None = None,
) -> dict[str, Any]:
    """Tính lại và ghi lineage miền cho một dự án, giới hạn ở các phân khu đã đổi.

    Args:
        project_id: UUID dự án, dạng CHUỖI. RQ tuần tự hoá tham số qua ranh giới
            tiến trình; truyền `uuid.UUID` vẫn chạy nhưng buộc payload hàng đợi
            phụ thuộc vào một kiểu Python mà không đổi lại được gì.
        area_ids: các phân khu đã đổi. None/rỗng = tính lại cả dự án.
        sync_run_id: chỉ để đối chiếu log với lô đồng bộ đã kích hoạt job này.

    Returns:
        dict cùng hình dạng với `run_parse_upload` để kết quả job đồng nhất.
    """
    current = get_current_job()
    token = job_id_var.set(current.id if current else None)
    started = time.perf_counter()

    log.info(
        "domain.recompute.started",
        project_id=project_id,
        sync_run_id=sync_run_id,
        areas=len(area_ids) if area_ids else None,
    )

    try:
        scope = [uuid.UUID(str(value)) for value in area_ids] if area_ids else None
        rows = asyncio.run(_recompute(project_id, scope))

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "domain.recompute.finished",
            project_id=project_id,
            sync_run_id=sync_run_id,
            rows=rows,
            areas=len(scope) if scope else None,
            duration_ms=duration_ms,
        )
        return {
            "status": "done",
            "project_id": project_id,
            "sync_run_id": sync_run_id,
            "area_ids": [str(a) for a in scope] if scope else None,
            "rows": rows,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        # Ném lại để RQ đánh dấu job failed và chạy retry đã cấu hình. KHÔNG nuốt:
        # lineage miền lạc hậu mà không ai biết là đúng thứ script phục hồi sinh
        # ra để phát hiện, nhưng job hỏng thì phải hiện ra ngay ở hàng đợi.
        log.error(
            "domain.recompute.failed",
            project_id=project_id,
            sync_run_id=sync_run_id,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        raise
    finally:
        job_id_var.reset(token)


async def _recompute(project_id: str, area_ids: list[uuid.UUID] | None) -> int:
    """Tính rồi ghi. Tách ra để phần async gọn trong một `asyncio.run`."""
    service = DomainAbsorptionCalculatorService()
    result = await service.compute(project_id, area_ids=area_ids)
    return await service.persist(result, area_ids=area_ids)
