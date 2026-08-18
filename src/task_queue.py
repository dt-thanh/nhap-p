"""Kết nối Redis và hàng đợi job dùng chung cho api / worker / scheduler."""

from functools import lru_cache

from redis import Redis
from rq import Queue

from src.config import get_settings

FORECAST_QUEUE = "forecast"
# Hàng đợi riêng cho parse file upload. Không dùng chung với `forecast` vì job
# Prophet chạy hàng phút, mà UploadPage chỉ poll trạng thái trong 2 phút rồi
# timeout (SRS §5.2) — parse xếp sau một forecast là coi như hỏng luồng upload.
INGEST_QUEUE = "ingest"


@lru_cache
def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_dsn)


def get_queue(name: str = FORECAST_QUEUE) -> Queue:
    return Queue(name, connection=get_redis())
