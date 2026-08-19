#!/bin/sh
# Entrypoint chung cho api / worker / scheduler.
# Chỉ container api đặt RUN_MIGRATIONS=true để tránh nhiều tiến trình cùng migrate.
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    # CHẶN Ở MÔI TRƯỜNG SẢN XUẤT.
    #
    # Migrate tự động lúc khởi động là tiện ở dev và NGUY HIỂM ở production, vì ba
    # lý do độc lập với nhau:
    #
    #   1. Nó chạy TRƯỚC khi ai kịp sao lưu. Ở Phase 8D, `docker compose up api`
    #      đã áp dụng revision 0013 lên database dev trước khi bản sao lưu được
    #      tạo — đúng thứ tự ngược với quy trình đã cam kết ở Phase 3/5/6.
    #   2. Nó gắn việc đổi schema vào việc khởi động lại tiến trình. Một lần
    #      restart vì OOM sẽ thành một lần migrate không ai định làm.
    #   3. `alembic upgrade head` chạy tới revision MỚI NHẤT trong image, chứ
    #      không tới revision mà người triển khai đang nghĩ tới.
    #
    # Ở production, dùng `bash scripts/migrate.sh` — nó buộc thứ tự
    # sao lưu → migrate → xác minh. Xem docs/runbooks/migrations.md.
    if [ "${APP_ENV:-development}" = "production" ]; then
        echo "[entrypoint] TỪ CHỐI: RUN_MIGRATIONS=true nhưng APP_ENV=production." >&2
        echo "[entrypoint] Migrate tự động lúc khởi động bị cấm ở production." >&2
        echo "[entrypoint] Chạy 'bash scripts/migrate.sh' rồi khởi động lại với RUN_MIGRATIONS=false." >&2
        exit 1
    fi
    echo "[entrypoint] Running alembic upgrade head..."
    alembic upgrade head
fi

exec "$@"
