"""Alembic environment — lấy DATABASE_URL từ Settings, chạy bằng driver đồng bộ."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic chạy đồng bộ: đổi asyncpg -> psycopg2 để tái dùng cùng một DATABASE_URL.
sync_url = (
    get_settings()
    .database_dsn.replace("+asyncpg", "")
    .replace("postgresql://", "postgresql+psycopg2://")
)
config.set_main_option("sqlalchemy.url", sync_url)

# Khi có model SQLAlchemy, import Base và gán vào đây để bật autogenerate.
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
