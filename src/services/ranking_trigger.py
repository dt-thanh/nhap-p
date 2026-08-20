"""Cò xếp hạng: xếp hàng một lần tính lại, chống dồn, rồi đẩy job vào RQ.

Module này là chỗ DUY NHẤT ghép hai nửa lại với nhau:

    enqueue_ranking()   ghi dòng `queued` vào DB, gộp nếu đã có lần chờ (§8.3)
    get_queue().enqueue()   đẩy job cho worker nhặt

Tách khỏi `src/ranking/service.py` có chủ đích: service không được biết gì về
Redis/RQ. Nó là tầng tính toán thuần DB, và mọi test của nó chạy được mà không
cần dựng Redis. Ngược lại, module này KHÔNG tự viết câu SQL nào vào bốn bảng xếp
hạng — nó gọi `enqueue_ranking`, đúng writer mà `tests/test_ranking_boundary.py`
cho phép.

**Quy tắc một job cho một run.** `enqueue_ranking` trả `created=False` khi đã có
run đang chờ. Khi đó KHÔNG được đẩy job thứ hai: hai job trỏ cùng một `run_id`
nghĩa là worker thứ hai chiếm hụt (`RUN_NOT_CLAIMABLE`) và ăn một lần retry vô
ích. Gộp là mục đích của partial unique index, không phải tác dụng phụ.

**Hỏng thì ghi log rồi đi tiếp.** Cùng lý do với `_enqueue_domain_recompute`
(xem `src/services/sync_runs.py`): lô đồng bộ đã COMMIT trước khi cò chạy. Ném
lỗi ở đây là báo cho hệ nguồn rằng dữ liệu bị từ chối trong khi nó đã được nhận.
Cái giá là bảng xếp hạng lạc hậu tới lần kích hoạt sau — chấp nhận được vì dòng
`queued` VẪN nằm trong DB kể cả khi Redis chết, nên nó xếp lại được.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from rq import Retry

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import projects
from src.ranking.service import enqueue_ranking
from src.task_queue import INGEST_QUEUE, get_queue

log = get_logger("src.services.ranking_trigger")

# Cùng hàng đợi với `recompute_domain`: hai việc này luôn đi cặp sau một lô đồng
# bộ, và xếp chúng vào hai hàng khác nhau chỉ tạo ra khả năng chúng chạy lệch
# nhau xa mà không đem lại gì.
RANKING_QUEUE = INGEST_QUEUE


async def trigger_ranking(
    project_id: uuid.UUID | str,
    *,
    trigger: str,
    sync_run_id: uuid.UUID | str | None = None,
    area_ids: list[str] | None = None,
    session_factory=None,
) -> tuple[uuid.UUID | None, bool]:
    """Xếp hàng + đẩy job. Trả `(run_id, enqueued_job)`.

    `enqueued_job=False` với `run_id` không None nghĩa là đã gộp vào một run
    đang chờ — đúng như thiết kế, không phải lỗi.

    `session_factory` — người gọi PHẢI truyền pool của chính mình khi cò chạy
    bên trong một request đang giữ connection (đường đồng bộ:
    `SyncRunService`). Để trống thì hàm này mở pool TOÀN CỤC riêng, tức là hai
    pool cùng chạm `ranking_runs` trong một request — và hai pool tranh khoá
    trên cùng một bảng là công thức deadlock, không phải chuyện lý thuyết: nó
    ĐÃ xảy ra ngay lần đầu chạy chung bộ test đồng bộ với bộ test xếp hạng.
    """
    try:
        run_id, created = await enqueue_ranking(
            project_id,
            trigger=trigger,
            sync_run_id=sync_run_id,
            area_ids=area_ids,
            session_factory=session_factory,
        )
    except Exception as exc:
        log.error(
            "ranking.trigger.enqueue_row_failed",
            project_id=str(project_id),
            trigger=trigger,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return None, False

    if not created:
        log.info("ranking.trigger.coalesced", project_id=str(project_id), run_id=str(run_id), trigger=trigger)
        return run_id, False

    try:
        get_queue(RANKING_QUEUE).enqueue(
            "src.jobs.rank_project.rank_project",
            project_id=str(project_id),
            run_id=str(run_id),
            trigger=trigger,
            retry=Retry(max=3, interval=[10, 30, 60]),
        )
    except Exception as exc:
        # Dòng `queued` ĐÃ commit và vẫn nằm đó. Đây là lý do thứ tự phải là
        # "ghi DB trước, đẩy RQ sau": Redis chết thì việc cần làm vẫn còn dấu
        # vết để xếp lại; đẩy RQ trước thì một job mồ côi trỏ vào run không tồn tại.
        log.error(
            "ranking.trigger.enqueue_job_failed",
            project_id=str(project_id),
            run_id=str(run_id),
            trigger=trigger,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return run_id, False

    log.info("ranking.trigger.enqueued", project_id=str(project_id), run_id=str(run_id), trigger=trigger)
    return run_id, True


async def trigger_ranking_all_projects(*, trigger: str) -> dict[str, int]:
    """Xếp hàng tính lại cho MỌI dự án — dùng khi publish/rollback config.

    §8.2: đổi config là thay đổi TOÀN CỤC. Bộ trọng số mới làm mọi điểm cũ mất
    hiệu lực ở mọi dự án cùng lúc, nên phải một job cho mỗi dự án chứ không phải
    một job khổng lồ: `ranking_scores` được xoá-rồi-chèn THEO DỰ ÁN, và gộp mọi
    dự án vào một transaction sẽ khoá cả bảng trong lúc chạy.

    Không dừng ở dự án đầu tiên gặp lỗi. `trigger_ranking` đã tự nuốt lỗi của
    từng dự án, nên một dự án hỏng không kéo theo những dự án còn lại — bỏ sót
    một dự án còn hơn bỏ sót tất cả.
    """
    async with get_session_factory()() as session:
        project_ids = list((await session.execute(sa.select(projects.c.id))).scalars().all())

    counts = {"projects": len(project_ids), "enqueued": 0, "coalesced": 0, "failed": 0}
    for project_id in project_ids:
        run_id, enqueued_job = await trigger_ranking(project_id, trigger=trigger)
        if run_id is None:
            counts["failed"] += 1
        elif enqueued_job:
            counts["enqueued"] += 1
        else:
            counts["coalesced"] += 1

    log.info("ranking.trigger.all_projects", trigger=trigger, **counts)
    return counts
