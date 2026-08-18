"""Scheduler — enqueue job định kỳ (forecast hằng ngày, kiểm lineage miền hằng giờ).

Tiến trình này CHỈ đẩy job vào hàng đợi, không tự chạy việc gì. Nhờ vậy nó không
cần kết nối database, và một job chạy lâu không chặn cron của job khác.
Khởi động: python -m src.scheduler
"""

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_settings
from src.logging_config import configure_logging, get_logger
from src.task_queue import INGEST_QUEUE, get_queue

configure_logging("scheduler")
log = get_logger("src.scheduler")


def enqueue_daily_forecast() -> None:
    job = get_queue().enqueue("src.jobs.forecast.run_daily_forecast", trigger_type="schedule")
    log.info("scheduler.enqueued", job_id=job.id)


def enqueue_domain_recompute_audit() -> None:
    """Kiểm cửa sổ sự cố giữa COMMIT và enqueue — xem Phase 8A.

    Vào `INGEST_QUEUE` chứ không phải hàng đợi forecast: lần kiểm là một câu SQL,
    xếp sau một job Prophet chạy hàng phút sẽ biến chu kỳ hằng giờ thành "khi nào
    Prophet xong thì tính".
    """
    settings = get_settings()
    job = get_queue(INGEST_QUEUE).enqueue(
        "src.jobs.domain_recompute_audit.run_domain_recompute_audit",
        repair=settings.domain_recompute_audit_repair,
    )
    log.info("scheduler.audit_enqueued", job_id=job.id, repair=settings.domain_recompute_audit_repair)


def enqueue_parallel_run_capture() -> None:
    """Ghi lại một lần so sánh hai bộ tính cho MỌI dự án — xem Phase 8D.

    Chỉ QUAN SÁT: job không ghi `absorption_daily` và không đổi
    `projects.absorption_calculator`. Cùng hàng đợi với lần kiểm ở 8A, cùng lý do.
    """
    job = get_queue(INGEST_QUEUE).enqueue(
        "src.jobs.parallel_run.run_parallel_run_capture",
        project_id=None,
        trigger="schedule",
    )
    log.info("scheduler.parallel_run_enqueued", job_id=job.id)


def build_scheduler() -> BlockingScheduler:
    """Dựng scheduler đã đăng ký đủ job. Tách khỏi `main()` để test được."""
    settings = get_settings()
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    scheduler.add_job(
        enqueue_daily_forecast,
        CronTrigger.from_crontab(settings.forecast_cron),
        id="daily_forecast",
        replace_existing=True,
    )

    if settings.domain_recompute_audit_enabled:
        scheduler.add_job(
            enqueue_domain_recompute_audit,
            CronTrigger.from_crontab(settings.domain_recompute_audit_cron),
            id="domain_recompute_audit",
            replace_existing=True,
            # Worker chết vài tiếng rồi sống lại không nên khiến APScheduler bắn
            # một loạt lần kiểm bị lỡ cùng lúc — một lần là đủ, kết quả như nhau.
            coalesce=True,
            max_instances=1,
        )
    else:
        log.warning("scheduler.audit_disabled")

    if settings.parallel_run_capture_enabled:
        scheduler.add_job(
            enqueue_parallel_run_capture,
            CronTrigger.from_crontab(settings.parallel_run_capture_cron),
            id="parallel_run_capture",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
    else:
        log.warning("scheduler.parallel_run_disabled")

    return scheduler


def main() -> None:
    settings = get_settings()
    scheduler = build_scheduler()
    log.info(
        "scheduler.started",
        cron=settings.forecast_cron,
        audit_cron=settings.domain_recompute_audit_cron if settings.domain_recompute_audit_enabled else None,
        timezone=settings.scheduler_timezone,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
