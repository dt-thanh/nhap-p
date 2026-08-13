"""Scheduler — enqueue job dự báo hằng ngày lúc 02:00 (SRS §2.4).

Tiến trình này CHỈ đẩy job vào hàng đợi, không tự chạy forecast.
Khởi động: python -m src.scheduler
"""

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import get_settings
from src.logging_config import configure_logging, get_logger
from src.task_queue import get_queue

configure_logging("scheduler")
log = get_logger("src.scheduler")


def enqueue_daily_forecast() -> None:
    job = get_queue().enqueue("src.jobs.forecast.run_daily_forecast", trigger_type="schedule")
    log.info("scheduler.enqueued", job_id=job.id)


def main() -> None:
    settings = get_settings()
    scheduler = BlockingScheduler(timezone=settings.scheduler_timezone)
    scheduler.add_job(
        enqueue_daily_forecast,
        CronTrigger.from_crontab(settings.forecast_cron),
        id="daily_forecast",
        replace_existing=True,
    )
    log.info(
        "scheduler.started",
        cron=settings.forecast_cron,
        timezone=settings.scheduler_timezone,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
