#!/usr/bin/env bash
# Khởi động bình thường, AN TOÀN — không bao giờ xoá volume, không bao giờ
# xoay credential đang active. Tương phản với `scripts/dev-reset.sh` (HUỶ dữ
# liệu, chỉ dùng khi cố ý reset sạch).
#
#   ./scripts/dev-up.sh
#
# Giả định `.dev-secrets/minicrm_sync_api_key` đã tồn tại từ một lần
# `dev-reset.sh` trước đó — nếu chưa, script dừng lại với hướng dẫn rõ ràng
# thay vì tự ý tạo credential mới (đó là việc của `dev-reset.sh`, có chủ đích
# tách biệt "khởi động" khỏi "cấp phát").

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SECRET_FILE="${REPO_ROOT}/.dev-secrets/minicrm_sync_api_key"

die() { echo "" >&2; echo "[dev-up] LỖI: $*" >&2; exit 1; }
info() { echo "[dev-up] $*"; }

[ -f .env ] || die "không thấy .env ở $REPO_ROOT. Chạy 'bash scripts/bootstrap_env.sh' trước."
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in '' | '#'*) continue ;; esac
    line="${line#export }"
    key="${line%%=*}"
    value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#"${key%%[![:space:]]*}"}"
    case "$key" in '' | *[!A-Za-z0-9_]*) continue ;; esac
    case "$value" in
        \"*\") value="${value#\"}"; value="${value%\"}" ;;
        \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    export "$key=$value"
done < .env

if [ "${APP_ENV:-development}" = "production" ]; then
    die "APP_ENV=production. Script này CHỈ dành cho dev cục bộ."
fi

command -v docker >/dev/null 2>&1 || die "không tìm thấy docker."
docker compose version >/dev/null 2>&1 || die "không dùng được 'docker compose' (cần Compose v2)."

if [ ! -s "$SECRET_FILE" ]; then
    die "Chưa có ${SECRET_FILE} (hoặc rỗng). Đây là dấu hiệu chưa từng chạy
'./scripts/dev-reset.sh --yes' trên máy này (lần đầu setup CŨNG dùng
dev-reset.sh, xem README §2.1). Không tự cấp credential ở đây — dev-up chỉ
khởi động, không cấp phát."
fi

info "docker compose up -d (Compose tự phát hiện secret file không đổi -> KHÔNG xoay/tạo lại minicrm nếu không cần)..."
docker compose up -d

wait_healthy() {
    local service="$1" timeout="${2:-90}" cid status deadline
    info "chờ '$service' healthy (tối đa ${timeout}s)..."
    deadline=$((SECONDS + timeout))
    while true; do
        cid="$(docker compose ps -q "$service" 2>/dev/null || true)"
        if [ -n "$cid" ]; then
            status="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")"
            [ "$status" = "healthy" ] && { info "'$service' healthy."; return 0; }
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            docker compose logs --tail 20 "$service" >&2 || true
            die "'$service' không healthy sau ${timeout}s."
        fi
        sleep 2
    done
}

wait_healthy db
wait_healthy minicrm_db
wait_healthy keycloak 120
wait_healthy api 60
wait_healthy minicrm 60

info "Đảm bảo đúng một sync_credentials active, khớp secret file + .env + minicrm/.env..."
# Dùng chung scripts/ensure_sync_credential.sh với dev-reset.sh (không chỉ
# gọi bootstrap_dev.py trực tiếp): --credential-output-file của bootstrap_dev
# chỉ ghi MỘT trong hai đích (file HOẶC .env, không bao giờ cả hai) — tự nó
# không đồng bộ .env/minicrm/.env, đây chính là nguyên nhân khoá lệch đã gặp.
if ! bash scripts/ensure_sync_credential.sh; then
    die "ensure_sync_credential báo lỗi — kiểm log ở trên. Stack đã 'up' nhưng credential có thể chưa nhất quán."
fi

echo ""
info "XONG. Stack đang chạy, dữ liệu và credential được giữ nguyên."
info "Kiểm trạng thái: docker compose ps"
