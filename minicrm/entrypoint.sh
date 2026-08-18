#!/bin/sh
# Entrypoint của Mini CRM.
#
# `MINICRM_RUN_MIGRATIONS` — tiền tố riêng, KHÔNG phải `RUN_MIGRATIONS`. Hai hệ
# thống chạy trong cùng một mạng Compose và có thể cùng đọc một file `.env`; dùng
# chung tên biến nghĩa là bật migrate cho hệ này sẽ bật luôn cho hệ kia.
#
# Chỉ dùng ở dev, cùng lý do đã ghi ở `docker/entrypoint.sh` của backend: migrate
# tự động lúc khởi động chạy TRƯỚC khi ai kịp sao lưu, và gắn việc đổi schema vào
# việc khởi động lại tiến trình.
set -e

if [ "${MINICRM_RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[minicrm] alembic upgrade head..."
    alembic -c alembic.ini upgrade head
fi

exec "$@"
