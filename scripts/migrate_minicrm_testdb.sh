#!/usr/bin/env bash
# Tạo (nếu chưa có) và migrate `minicrm_checkpoint1_test` — database TEST riêng
# của Mini CRM, KHÔNG phải database dev/production
# (`ck_..._test` — xem quy ước từ chối ghi vào DB không kết thúc bằng `_test`
# ở `minicrm/tests/conftest.py::db_skip_reason`). An toàn chạy lại nhiều lần:
# tạo DB có kiểm tồn tại trước, `alembic upgrade head` không làm gì nếu đã ở
# head. Gọi từ `make testdb`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker compose exec -T minicrm_db psql -U minicrm -tc \
    "SELECT 1 FROM pg_database WHERE datname='minicrm_checkpoint1_test'" \
    | grep -q 1 || docker compose exec -T minicrm_db psql -U minicrm -c \
    "CREATE DATABASE minicrm_checkpoint1_test;"

cd "$REPO_ROOT/minicrm"
MINICRM_DATABASE_URL="postgresql+asyncpg://minicrm:minicrm@localhost:5434/minicrm_checkpoint1_test" \
    python3 -m alembic upgrade head
