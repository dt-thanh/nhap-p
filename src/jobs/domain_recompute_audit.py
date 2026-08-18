"""Job định kỳ: phát hiện và vá những lô đồng bộ thiếu lần tính lại lineage miền.

Scheduler chỉ ĐẨY job này vào hàng đợi, y như `enqueue_daily_forecast` đã làm với
forecast — bản thân scheduler không chạm database. Nhờ vậy tiến trình scheduler
vẫn mỏng, và một lần kiểm chạy lâu không chặn cron của job khác.

Job đi vào `INGEST_QUEUE` chứ không phải `FORECAST_QUEUE`: lần kiểm là một câu
SQL, xếp nó sau một job Prophet chạy hàng phút sẽ biến chu kỳ kiểm hằng giờ thành
"khi nào Prophet xong thì tính".

Job này KHÔNG tự tính lại. Nó chỉ xếp hàng `run_domain_recompute` — cùng một job,
cùng retry, cùng đường đi với luồng đồng bộ bình thường. Có một đường tính lại thứ
hai chỉ dùng khi phục hồi nghĩa là có một đường ít được chạy nhất lại là đường
được tin cậy nhất lúc sự cố.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from rq import get_current_job

from src.logging_config import get_logger, job_id_var
from src.services.domain_recompute_audit import audit

log = get_logger("src.jobs.domain_recompute_audit")


def run_domain_recompute_audit(*, repair: bool = True) -> dict[str, Any]:
    """Chạy một lần kiểm lineage miền.

    Args:
        repair: True thì xếp lại hàng cho những dự án lạc hậu. Cảnh báo phát ra
            trong CẢ HAI trường hợp — xem docstring `domain_recompute_audit`.

    Returns:
        dict cùng hình dạng với các job khác để kết quả job đồng nhất.
    """
    current = get_current_job()
    token = job_id_var.set(current.id if current else None)
    started = time.perf_counter()

    try:
        result = asyncio.run(audit(repair=repair))
        duration_ms = round((time.perf_counter() - started) * 1000, 1)

        return {
            # `stale` là kết quả chẩn đoán, KHÔNG phải lỗi của job: job đã làm
            # đúng việc của nó. Đánh job này thành failed sẽ khiến RQ retry một
            # lần kiểm mà kết quả không phụ thuộc vào việc chạy lại.
            "status": "done",
            "stale_projects": len(result.stale),
            "project_ids": [str(p.project_id) for p in result.stale],
            "repaired": len(result.repaired_job_ids),
            "job_ids": result.repaired_job_ids,
            "repair_error": result.repair_error,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        # Chỉ tới đây khi chính lần KIỂM hỏng (database không tới được). Ném lại
        # để job hiện ra ở failed registry — một lần kiểm âm thầm không chạy tệ
        # hơn không có lần kiểm nào, vì nó tạo cảm giác đang được canh.
        log.error(
            "domain.recompute.audit_failed",
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        raise
    finally:
        job_id_var.reset(token)
