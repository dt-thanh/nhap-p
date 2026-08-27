#!/usr/bin/env bash
# Chạy test tích hợp DB trên PostgreSQL thật, không cần bước thủ công nào.
#
#   bash scripts/test_db.sh                 # chạy test_import_records.py
#   bash scripts/test_db.sh -k duplicate    # tham số thừa được chuyển thẳng cho pytest
#
# Script tự: nạp .env → dựng service `db` của compose → chờ DB sẵn sàng →
# tạo database test riêng → alembic upgrade head → pytest.
#
# QUAN TRỌNG: test dùng database RIÊNG "<POSTGRES_DB>_test", không đụng vào
# database dev. Fixture clean_db xoá sạch bảng trước mỗi test (có DELETE FROM
# projects), chạy nhầm vào DB dev là mất dữ liệu thật.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TEST_TARGET="${TEST_TARGET:-tests/test_services/test_import_records.py}"
DB_SERVICE="${DB_SERVICE:-db}"
READY_TIMEOUT="${READY_TIMEOUT:-60}"

die() {
    echo "" >&2
    echo "[test_db] LỖI: $*" >&2
    exit 1
}

info() { echo "[test_db] $*"; }

# ---- 1. Nạp .env -----------------------------------------------------------
[ -f .env ] || die "không thấy .env ở $REPO_ROOT. Copy từ .env.example rồi điền giá trị."

# KHÔNG dùng `source .env`: bash sẽ THỰC THI nội dung file. Giá trị có khoảng
# trắng như FORECAST_CRON=0 2 * * * bị hiểu thành lệnh `2` với tham số `* * *`,
# và bất kỳ $(...) nào trong .env cũng bị chạy thật. Ở đây chỉ tách KEY=VALUE
# rồi export, không diễn giải gì thêm.
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        '' | '#'*) continue ;;
    esac
    line="${line#export }"
    key="${line%%=*}"
    value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"   # cắt khoảng trắng cuối key
    key="${key#"${key%%[![:space:]]*}"}"   # cắt khoảng trắng đầu key
    # Bỏ qua dòng không phải gán biến hợp lệ (tránh export rác).
    case "$key" in
        '' | *[!A-Za-z0-9_]*) continue ;;
    esac
    # Gỡ một lớp nháy bao ngoài nếu có.
    case "$value" in
        \"*\") value="${value#\"}"; value="${value%\"}" ;;
        \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    export "$key=$value"
done < .env

for var in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB; do
    [ -n "${!var:-}" ] || die ".env thiếu $var (cần cho cả compose lẫn chuỗi kết nối)."
done

command -v docker >/dev/null 2>&1 || die "không tìm thấy docker."
docker compose version >/dev/null 2>&1 || die "không dùng được 'docker compose' (cần Compose v2)."

# ---- 1.5. Preflight: đĩa/Docker/Postgres/Redis, ĐỌC-ONLY, không xoá gì ------
# Thêm sau sự cố release-hardening: host hết đĩa giữa một lượt test làm
# container `db` crash. Chặn SỚM, trước khi dựng service/chạy migration, thay
# vì để `docker compose up`/`alembic upgrade` thất bại nửa chừng với lỗi khó
# đọc. `--quiet`: chỉ in ra khi có FAIL, giữ log của test_db.sh gọn như trước.
if [ -x "$REPO_ROOT/scripts/preflight_test_env.sh" ]; then
    "$REPO_ROOT/scripts/preflight_test_env.sh" --quiet \
        || die "preflight thất bại — xem 'bash scripts/preflight_test_env.sh' để biết chi tiết. Không có test/migration nào đã chạy."
fi

# Ưu tiên python của venv để dùng đúng pytest/sqlalchemy/alembic đã cài.
if [ -x .venv/bin/python ]; then
    PY=".venv/bin/python"
else
    PY="python3"
    command -v python3 >/dev/null 2>&1 || die "không tìm thấy python3 và cũng không có .venv."
fi

# ---- 2. Dựng service db ----------------------------------------------------
info "đang dựng service '$DB_SERVICE'..."
docker compose up -d "$DB_SERVICE" >/dev/null || die "'docker compose up -d $DB_SERVICE' thất bại. Kiểm tra docker-compose.yml."

# ---- 3. Chờ DB sẵn sàng ----------------------------------------------------
# pg_isready chạy TRONG container nên không phụ thuộc client psql ở máy host.
info "chờ PostgreSQL sẵn sàng (tối đa ${READY_TIMEOUT}s)..."
deadline=$((SECONDS + READY_TIMEOUT))
until docker compose exec -T "$DB_SERVICE" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "" >&2
        echo "--- log 30 dòng cuối của service $DB_SERVICE ---" >&2
        docker compose logs --tail 30 "$DB_SERVICE" >&2 || true
        die "DB không sẵn sàng sau ${READY_TIMEOUT}s. Thường do POSTGRES_USER/POSTGRES_DB trong .env không khớp volume pgdata đã tạo trước đó (đổi credential sau khi volume tồn tại thì phải 'docker compose down -v')."
    fi
    sleep 1
done
info "PostgreSQL đã sẵn sàng."

# ---- 4. Database riêng cho test --------------------------------------------
# KHÔNG dùng chung với DB dev: fixture test xoá sạch bảng trước mỗi test.
TEST_DB="${POSTGRES_DB}_test"

# Tên database có chữ hoa nên bắt buộc bọc nháy kép trong SQL.
exists=$(docker compose exec -T "$DB_SERVICE" \
    psql -U "$POSTGRES_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${TEST_DB}'" 2>/dev/null || true)

if [ "$exists" != "1" ]; then
    info "tạo database test '${TEST_DB}'..."
    docker compose exec -T "$DB_SERVICE" \
        psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"${TEST_DB}\"" >/dev/null \
        || die "không tạo được database '${TEST_DB}'. Kiểm tra quyền của user '${POSTGRES_USER}'."
else
    info "database test '${TEST_DB}' đã có."
fi

# ---- 5. Dựng chuỗi kết nối -------------------------------------------------
# Lấy cổng thật mà compose publish, không đoán 5432 — cổng có thể đã đổi.
host_port="$(docker compose port "$DB_SERVICE" 5432 2>/dev/null | tail -1)" \
    || die "service '$DB_SERVICE' không publish cổng 5432."
[ -n "$host_port" ] || die "không đọc được cổng publish của '$DB_SERVICE'. Kiểm tra mục ports: trong docker-compose.yml."
DB_PORT="${host_port##*:}"

# Mật khẩu phải được URL-encode, nếu không ký tự như @ hay # sẽ phá vỡ DSN.
ENC_USER="$("$PY" -c 'import os,urllib.parse as u; print(u.quote(os.environ["POSTGRES_USER"], safe=""))')"
ENC_PASS="$("$PY" -c 'import os,urllib.parse as u; print(u.quote(os.environ["POSTGRES_PASSWORD"], safe=""))')"

TEST_URL="postgresql+asyncpg://${ENC_USER}:${ENC_PASS}@localhost:${DB_PORT}/${TEST_DB}"

# alembic đọc DATABASE_URL qua Settings; ghi đè để migrate ĐÚNG database test.
# Biến môi trường được ưu tiên hơn giá trị trong .env (pydantic-settings).
export DATABASE_URL="$TEST_URL"
export TEST_DATABASE_URL="$TEST_URL"

# ---- 6. Migration ----------------------------------------------------------
info "chạy alembic upgrade head trên '${TEST_DB}'..."
"$PY" -m alembic upgrade head \
    || die "alembic upgrade head thất bại. Kiểm tra credential trong .env và trạng thái service db."

# ---- 7. Test ---------------------------------------------------------------
info "chạy pytest ${TEST_TARGET}"
echo ""
exec "$PY" -m pytest "$TEST_TARGET" -v "$@"
