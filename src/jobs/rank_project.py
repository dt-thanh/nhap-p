"""RQ job: chạy một lần xếp hạng đã được xếp hàng, ngoài request cycle.

Trước đợt này mọi lần tính lại đều chạy ĐỒNG BỘ trong request. Ổn với nút "Tính
lại" thủ công (~260ms, người dùng đang ngồi chờ), nhưng KHÔNG ổn với cò tự động:
một lô đồng bộ lớn commit hàng trăm lần trong một phút, và không ai muốn request
đồng bộ của Mini CRM phải chờ xếp hạng xong mới trả về.

Ranh giới của job này rất hẹp — nó KHÔNG tự quyết định gì:

* Không tự tạo `ranking_runs`. Dòng `queued` do `enqueue_ranking` tạo TRƯỚC khi
  job được đẩy vào RQ, nên hàng đợi RQ mất sạch cũng không làm mất dấu vết: run
  vẫn nằm ở `queued` trong DB và `scripts/` có thể xếp lại.
* Không tự chọn config. `run_ranking` chốt bộ trọng số lúc CHIẾM run.
* Không nuốt lỗi. RQ `Retry` lo việc thử lại; `run_ranking` đã tự ghi
  `status='failed'` + `error_summary` trước khi ném lại.

`RUN_NOT_CLAIMABLE` là trường hợp BÌNH THƯỜNG, không phải lỗi: một worker khác
đã nhặt đúng run này. Job trả về `claimed=False` và kết thúc sạch — ném lỗi ở
đây sẽ khiến RQ thử lại một việc đã có người làm.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from rq import get_current_job

from src.logging_config import get_logger, job_id_var
from src.ranking.service import RankingError, run_ranking

log = get_logger("src.jobs.rank_project")


def rank_project(project_id: str, *, run_id: str, trigger: str = "sync") -> dict[str, Any]:
    """Chạy lại xếp hạng cho một dự án. Điểm vào của RQ (đồng bộ, RQ không async)."""
    job = get_current_job()
    if job is not None:
        job_id_var.set(job.id)
    started = time.perf_counter()

    try:
        result = asyncio.run(
            run_ranking(uuid.UUID(project_id), trigger=trigger, run_id=uuid.UUID(run_id))
        )
    except RankingError as exc:
        if exc.code == "RUN_NOT_CLAIMABLE":
            log.info("ranking.job.already_claimed", project_id=project_id, run_id=run_id)
            return {"project_id": project_id, "run_id": run_id, "claimed": False}
        log.error(
            "ranking.job.failed",
            project_id=project_id,
            run_id=run_id,
            error_code=exc.code,
            error_message=exc.message,
        )
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    log.info(
        "ranking.job.completed",
        project_id=project_id,
        run_id=run_id,
        units_processed=result.units_processed,
        units_ranked=result.units_ranked,
        units_skipped=result.units_skipped,
        duration_ms=duration_ms,
    )
    return {
        "project_id": project_id,
        "run_id": run_id,
        "claimed": True,
        "units_processed": result.units_processed,
        "units_ranked": result.units_ranked,
        "units_skipped": result.units_skipped,
        "duration_ms": duration_ms,
    }
