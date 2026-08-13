"""Job dự báo hằng ngày (SRS §5.3 — ForecastJobRunner).

Hiện là điểm nối hạ tầng: xác nhận worker nhận và chạy được job.
Logic Prophet / LangGraph / cảnh báo sẽ cài đặt ở MVP 2.
"""

import time

from rq import get_current_job

from src.logging_config import get_logger, job_id_var

log = get_logger("src.jobs.forecast")


def run_daily_forecast(trigger_type: str = "schedule", area_ids: list[str] | None = None) -> dict:
    """Chạy dự báo cho các phân khu.

    Args:
        trigger_type: "schedule" (scheduler 02:00) hoặc "manual" (POST /api/forecasts/run).
        area_ids: giới hạn phạm vi phân khu; None = toàn bộ.
    """
    current = get_current_job()
    token = job_id_var.set(current.id if current else None)
    started = time.perf_counter()

    log.info(
        "forecast.job.started",
        trigger_type=trigger_type,
        areas_total=len(area_ids) if area_ids else None,
    )

    # TODO (MVP 2): đọc absorption_daily → Prophet → sellout_date + CI 90%
    #               → LangGraph/LLM giải thích → alerts → NOTIFY forecast_progress
    processed = 0
    failed = 0

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    log.info(
        "forecast.job.finished",
        trigger_type=trigger_type,
        areas_total=processed,
        areas_failed=failed,
        duration_ms=duration_ms,
    )
    job_id_var.reset(token)

    return {
        "trigger_type": trigger_type,
        "areas_total": processed,
        "areas_failed": failed,
        "duration_ms": duration_ms,
    }
