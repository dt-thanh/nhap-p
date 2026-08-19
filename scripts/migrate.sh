#!/usr/bin/env bash
# Đổi schema theo đúng thứ tự: SAO LƯU → MIGRATE → XÁC MINH.
#
#   bash scripts/migrate.sh                    # lên revision mới nhất
#   bash scripts/migrate.sh 0013_calculator_comparisons
#   TARGET_ENV=production bash scripts/migrate.sh 0013_...
#
# Vì sao có script này thay vì `alembic upgrade head`:
#
#   Ở Phase 8D, revision 0013 được áp dụng lên database dev bởi entrypoint của
#   container (RUN_MIGRATIONS=true) ngay khi `docker compose up api` chạy — TRƯỚC
#   khi bản sao lưu được tạo. Không mất gì, vì 0013 thuần cộng thêm và đường lùi
#   đã được kiểm. Nhưng thứ tự đã sai, và thứ tự chính là toàn bộ giá trị của quy
#   trình: một bản sao lưu lấy SAU khi migrate không cứu được gì.
#
# Script này làm cho thứ tự đó không lệch được: không sao lưu được thì không
# migrate. Xem docs/runbooks/migrations.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TARGET="${1:-head}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
DB_SERVICE="${DB_SERVICE:-db}"

die() { echo "" >&2; echo "[migrate] LỖI: $*" >&2; exit 1; }
info() { echo "[migrate] $*"; }

# ---- 1. Nạp .env (KHÔNG `source`: xem ghi chú trong scripts/test_db.sh) ------
[ -f .env ] || die "không thấy .env ở $REPO_ROOT"
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in '' | '#'*) continue ;; esac
    case "$line" in *=*) export "${line?}" ;; esac
done < .env

PG_USER="${POSTGRES_USER:-app}"
PG_DB="${POSTGRES_DB:-absorption}"

# ---- 2. Cho biết đang đứng ở đâu -------------------------------------------
info "database : ${PG_DB} (user ${PG_USER})"
info "app_env  : ${APP_ENV:-development}"
info "target   : ${TARGET}"

CURRENT="$(docker compose exec -T "$DB_SERVICE" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT version_num FROM alembic_version" 2>/dev/null | tr -d '[:space:]')"
info "revision : ${CURRENT:-<chưa có>}"

# Production phải xác nhận bằng tay. Không có cờ --yes: một cờ như thế sẽ nằm sẵn
# trong lịch sử shell của người tiếp theo.
if [ "${APP_ENV:-development}" = "production" ]; then
    echo ""
    echo "*** ĐÂY LÀ MÔI TRƯỜNG SẢN XUẤT ***"
    printf "Gõ đúng tên database (%s) để tiếp tục: " "$PG_DB"
    read -r CONFIRM
    [ "$CONFIRM" = "$PG_DB" ] || die "không khớp — dừng lại."
fi

# ---- 3. SAO LƯU trước, luôn luôn -------------------------------------------
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="${BACKUP_DIR}/pre_${TARGET}_${STAMP}.dump"

info "đang sao lưu -> ${BACKUP}"
docker compose exec -T "$DB_SERVICE" pg_dump -U "$PG_USER" -d "$PG_DB" --format=custom > "$BACKUP" \
    || die "pg_dump thất bại — KHÔNG migrate."

# Kiểm bản sao lưu ĐỌC ĐƯỢC, không chỉ kiểm nó tồn tại. Một file 0 byte cũng
# "tồn tại", và người ta chỉ phát hiện ra điều đó vào đúng lúc cần phục hồi.
[ -s "$BACKUP" ] || die "bản sao lưu rỗng — KHÔNG migrate."

# CHÉP file vào container rồi mới `pg_restore --list`. KHÔNG đưa qua `/dev/stdin`:
# archive định dạng custom cần SEEK được, mà một pipe thì không — `pg_restore` sẽ
# báo "did not find magic string in file header" cho một bản sao lưu HOÀN TOÀN
# HỢP LỆ. Bước kiểm sai kiểu đó còn tệ hơn không kiểm: nó dạy người vận hành bỏ
# qua cảnh báo.
VERIFY_PATH="/tmp/verify_$(basename "$BACKUP")"
docker compose cp "$BACKUP" "${DB_SERVICE}:${VERIFY_PATH}" >/dev/null \
    || die "không chép được bản sao lưu vào container để kiểm."
entries="$(docker compose exec -T "$DB_SERVICE" pg_restore --list "$VERIFY_PATH" 2>/dev/null | grep -c 'TABLE DATA' || true)"
docker compose exec -T "$DB_SERVICE" rm -f "$VERIFY_PATH" >/dev/null 2>&1 || true
[ "${entries:-0}" -gt 0 ] || die "bản sao lưu không đọc được (0 bảng dữ liệu) — KHÔNG migrate."
info "sao lưu hợp lệ ($(wc -c < "$BACKUP") byte, ${entries} bảng có dữ liệu)"

# ---- 4. Migrate -------------------------------------------------------------
info "alembic upgrade ${TARGET}"
docker compose exec -T api alembic upgrade "$TARGET" || die "alembic upgrade thất bại. Bản sao lưu: ${BACKUP}"

# ---- 5. Xác minh ------------------------------------------------------------
NEW="$(docker compose exec -T "$DB_SERVICE" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT version_num FROM alembic_version" | tr -d '[:space:]')"
info "revision sau khi migrate: ${NEW}"
[ -n "$NEW" ] || die "không đọc được alembic_version sau khi migrate."

info "kiểm dữ liệu so với baseline (nếu có)..."
BASELINE="docs/baselines/dev_${NEW%%_*}.json"
if [ -f "$BASELINE" ]; then
    python -m scripts.baseline_dev_data --compare "$BASELINE" || true
else
    info "chưa có ${BASELINE} — tạo baseline mới sau khi kiểm bằng mắt."
fi

echo ""
info "XONG. Bản sao lưu trước khi migrate: ${BACKUP}"
info "Lùi lại: docker compose exec api alembic downgrade <revision trước>"
