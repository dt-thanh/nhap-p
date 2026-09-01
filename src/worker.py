"""RQ worker — chạy job dự báo tách khỏi tiến trình API.

Khởi động: python -m src.worker
"""

from rq import Worker

from src.logging_config import configure_logging, get_logger
from src.task_queue import FORECAST_QUEUE, INGEST_QUEUE, get_redis

configure_logging("worker")
log = get_logger("src.worker")

# RQ duyệt hàng đợi theo đúng thứ tự này: `ingest` đứng trước để job parse
# (người dùng đang ngồi chờ trên UploadPage) không phải xếp sau forecast.
QUEUES = [INGEST_QUEUE, FORECAST_QUEUE]


def main() -> None:
    # KHÔNG log redis_url — DSN có thể chứa mật khẩu.
    # Compose runs exactly one `worker` service.  It therefore owns RQ's
    # scheduler as well: retries configured with `Retry(interval=[...])` move
    # through RQ's scheduled registry only when one worker runs with_scheduler.
    # Do not add a second worker with this flag without moving scheduler
    # ownership to a dedicated, singleton service.
    log.info("worker.started", queues=QUEUES, rq_scheduler_owner=True)
    Worker(QUEUES, connection=get_redis()).work(with_scheduler=True)


if __name__ == "__main__":
    main()
