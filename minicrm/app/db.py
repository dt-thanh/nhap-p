"""Engine và session async cho Mini CRM.

NullPool vì cùng lý do với `src/db.py` của backend: tiến trình có thể mở nhiều
event loop khác nhau (test, lệnh CLI), và connection của asyncpg gắn chặt với loop
tạo ra nó.

Engine này CHỈ trỏ vào database của Mini CRM. Nó không biết gì về database của
backend, và không có đường nào để biết.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine dùng chung. KHÔNG log DSN — trong đó có mật khẩu."""
    return create_async_engine(get_settings().database_dsn, poolclass=NullPool)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)
