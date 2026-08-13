#!/bin/sh
# Entrypoint chung cho api / worker / scheduler.
# Chỉ container api đặt RUN_MIGRATIONS=true để tránh nhiều tiến trình cùng migrate.
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[entrypoint] Running alembic upgrade head..."
    alembic upgrade head
fi

exec "$@"
