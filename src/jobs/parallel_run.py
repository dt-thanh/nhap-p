"""Job định kỳ: ghi lại một lần so sánh hai bộ tính cho từng dự án.

Cùng khuôn với `domain_recompute_audit` (Phase 8A): scheduler CHỈ đẩy job vào
hàng đợi, worker chạy. Vào `INGEST_QUEUE` chứ không phải hàng đợi forecast —
xếp một lần đo sau job Prophet chạy hàng phút sẽ biến "so hằng ngày" thành "so
khi nào Prophet xong".

Job này **không ghi `absorption_daily`**. Nó gọi `compute()` (tính trong bộ nhớ),
không bao giờ gọi `persist()`; kết quả duy nhất được ghi là một dòng quan sát ở
`calculator_comparisons`.

Một dự án hỏng không kéo theo phần còn lại — xem `capture_all`. Job chỉ ném khi
chính lần chạy không khởi động được (database không tới được), vì lúc đó nó phải
hiện ra ở failed registry của RQ chứ không âm thầm coi như đã đo.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from rq import get_current_job

from src.logging_config import get_logger, job_id_var
from src.services.parallel_run import TRIGGER_SCHEDULE, ParallelRunCaptureService

log = get_logger("src.jobs.parallel_run")


def run_parallel_run_capture(project_id: str | None = None, *, trigger: str = TRIGGER_SCHEDULE) -> dict[str, Any]:
    """Ghi một lần so sánh cho một dự án, hoặc cho TẤT CẢ nếu `project_id=None`.

    Args:
        project_id: UUID dạng CHUỖI (RQ tuần tự hoá qua ranh giới tiến trình).
            None = mọi dự án.
        trigger: 'schedule' hay 'manual' — lưu xuống để đọc lịch sử còn phân biệt
            được một chuỗi đo tự động với một loạt lần chạy tay.
    """
    current = get_current_job()
    token = job_id_var.set(current.id if current else None)
    started = time.perf_counter()

    try:
        service = ParallelRunCaptureService()
        if project_id is None:
            results = asyncio.run(service.capture_all(trigger=trigger))
        else:
            results = [asyncio.run(service.capture(project_id, trigger=trigger))]

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info(
            "parallel_run.job_finished",
            captured=len(results),
            trigger=trigger,
            duration_ms=duration_ms,
        )
        return {
            "status": "done",
            "captured": len(results),
            "comparison_ids": [str(r.comparison_id) for r in results],
            "mismatched": sum(1 for r in results if not r.matches),
            "without_domain_data": sum(1 for r in results if not r.domain_has_data),
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        log.error("parallel_run.job_failed", error_type=type(exc).__name__, exc_info=exc)
        raise
    finally:
        job_id_var.reset(token)
