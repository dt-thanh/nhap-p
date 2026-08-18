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
    log.info("worker.started", queues=QUEUES)
    Worker(QUEUES, connection=get_redis()).work(with_scheduler=False)


if __name__ == "__main__":
    main()
