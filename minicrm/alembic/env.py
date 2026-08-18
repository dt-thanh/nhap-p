"""Alembic environment của MINI CRM — hoàn toàn tách khỏi cây Alembic của backend.

Ba khác biệt so với `alembic/env.py` ở gốc repo, và cả ba đều cố ý:

1. **Đọc `MINICRM_DATABASE_URL`, không đọc `DATABASE_URL`.** Đây là ranh giới
   quan trọng nhất của cả file. Đọc nhầm biến nghĩa là chạy migration của Mini CRM
   lên database của backend, ghi revision `0001_minicrm_initial` vào bảng
   `alembic_version` của backend, và trộn hai lịch sử làm một. Không có đường lùi
   sạch cho việc đó.
2. **KHÔNG import `src.config`.** Cây Alembic của backend làm thế; cây này không
   được phép, vì `minicrm/` không import `src/`.
3. **`script_location` riêng, thư mục `versions/` riêng, bảng `alembic_version`
   nằm trong database riêng.** Ba thứ đó cộng lại là "lịch sử migration độc lập".
"""

from logging.config import fileConfig

from app.config import get_settings
from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic chạy đồng bộ: đổi asyncpg -> psycopg2 để dùng chung một DSN với ứng dụng.
sync_url = (
    get_settings().database_dsn.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")
)
config.set_main_option("sqlalchemy.url", sync_url)

# Autogenerate TẮT, giống backend: mọi migration viết tay. Autogenerate đọc ra
# thứ nó thấy, không đọc ra thứ ta định làm.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
