"""Engine và session async cho tầng ghi PostgreSQL.

Chỉ có ngần này là đủ cho luồng nạp dữ liệu: một engine, một session factory.
Chưa cần base class ORM hay unit-of-work — tầng insert dùng SQLAlchemy Core
(xem `src/models/tables.py`) nên không cần identity map.

NullPool là lựa chọn có chủ đích, không phải mặc định cho tiện: worker RQ chạy
mỗi job trong một event loop mới qua `asyncio.run()`. Connection của asyncpg gắn
chặt với loop tạo ra nó, nên nếu giữ pool thì job thứ hai sẽ lấy phải connection
thuộc loop đã đóng và nổ `Event loop is closed`. NullPool mở connection mới mỗi
lần checkout, đúng với nhịp "một job = một lần nạp file".
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine dùng chung. KHÔNG log DSN — trong đó có mật khẩu."""
    return create_async_engine(get_settings().database_dsn, poolclass=NullPool)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)
