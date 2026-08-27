#!/usr/bin/env bash
# Idempotent, data-only hard reset for local development databases.
# Preserves volumes, schema, Alembic history, Keycloak data, and .env skeletons.
# Always ends with exactly one active sync_credentials row, in sync with
# .dev-secrets/minicrm_sync_api_key, .env, and minicrm/.env — see
# scripts/ensure_sync_credential.sh. Use --seed only when fixture data is wanted.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
SECRET_DIR="$REPO_ROOT/.dev-secrets"
SECRET_FILE="$SECRET_DIR/minicrm_sync_api_key"
CONFIRMED=false
SEED=false
for arg in "$@"; do
    case "$arg" in
        --yes) CONFIRMED=true ;;
        --seed) SEED=true ;;
        *) echo "[dev-reset] LỖI: tùy chọn không được hỗ trợ: $arg" >&2; exit 2 ;;
    esac
done

die() { echo "" >&2; echo "[dev-reset] LỖI: $*" >&2; exit 1; }
info() { echo "[dev-reset] $*"; }

load_env_file() {
    local env_file="$1" line key value
    [ -f "$env_file" ] || die "không thấy $env_file. Chạy 'bash scripts/bootstrap_env.sh' trước."
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in '' | '#'*) continue ;; esac
        line="${line#export }"; key="${line%%=*}"; value="${line#*=}"
        key="${key%"${key##*[![:space:]]}"}"; key="${key#"${key%%[![:space:]]*}"}"
        case "$key" in '' | *[!A-Za-z0-9_]*) continue ;; esac
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        export "$key=$value"
    done < "$env_file"
}
load_env_file .env

[ "${APP_ENV:-}" = "development" ] || die "APP_ENV phải chính xác là development."
command -v docker >/dev/null 2>&1 || die "không tìm thấy docker."
docker compose version >/dev/null 2>&1 || die "không dùng được docker compose v2."
POSTGRES_DB="${POSTGRES_DB:-absorption}"
POSTGRES_USER="${POSTGRES_USER:-app}"
MINICRM_POSTGRES_DB="${MINICRM_POSTGRES_DB:-minicrm}"
MINICRM_POSTGRES_USER="${MINICRM_POSTGRES_USER:-minicrm}"

extract_url_host() {
    local url="$1" authority host
    authority="${url#*@}"; [ "$authority" != "$url" ] || die "DATABASE_URL thiếu authority."
    authority="${authority%%/*}"; host="${authority%%:*}"
    [ -n "$host" ] || die "DATABASE_URL thiếu host."
    printf '%s' "$host"
}
check_url_target() {
    local label="$1" url="$2" expected_db="$3" expected_service="$4" host db_name
    host="$(extract_url_host "$url")"; db_name="${url##*/}"; db_name="${db_name%%\?*}"
    case "$host" in localhost|127.0.0.1|::1|db|minicrm_db) ;;
        *) die "$label host không nằm trong allowlist local/dev." ;; esac
    case "$db_name" in
        *_dev|*_test) ;;
        "$expected_db") [ "$host" = "$expected_service" ] || die "$label dùng database mặc định với host ngoài Compose." ;;
        *) die "$label database name không nằm trong allowlist local/dev." ;;
    esac
}
check_url_target "AbsorpIQ DATABASE_URL" "${DATABASE_URL:-postgresql://app:app@db:5432/$POSTGRES_DB}" "$POSTGRES_DB" "db"
check_url_target "Mini CRM MINICRM_DATABASE_URL" "${MINICRM_DATABASE_URL:-postgresql://minicrm:minicrm@minicrm_db:5432/$MINICRM_POSTGRES_DB}" "$MINICRM_POSTGRES_DB" "minicrm_db"

if [ "$CONFIRMED" != "true" ]; then
    cat <<EOF

[dev-reset] Không có --yes — chỉ in kế hoạch, KHÔNG dừng service và KHÔNG ghi database:
  - giữ volume/schema/Keycloak/.env/.dev-secrets;
  - chạy alembic upgrade head cho hai app;
  - TRUNCATE các allowlist trong scripts/dev-hard-reset-*.sql;
  - giữ alembic_version, không dùng CASCADE;
  - đảm bảo đúng một sync_credentials active, đồng bộ secret file + .env + minicrm/.env;
  - khởi động lại stack; thêm --seed nếu muốn nạp fixture qua API.

Chạy thật: ./scripts/dev-reset.sh --yes
Nạp fixture: ./scripts/dev-reset.sh --yes --seed
EOF
    exit 0
fi

mkdir -p "$SECRET_DIR"; chmod 700 "$SECRET_DIR"
if [ ! -e "$SECRET_FILE" ]; then (umask 077 && : > "$SECRET_FILE"); fi
chmod 600 "$SECRET_FILE"
docker compose config --quiet || die "docker compose config không hợp lệ."

wait_healthy() {
    local service="$1" timeout="${2:-90}" cid status deadline
    info "chờ '$service' healthy (tối đa ${timeout}s)..."; deadline=$((SECONDS + timeout))
    while true; do
        cid="$(docker compose ps -q "$service" 2>/dev/null || true)"
        if [ -n "$cid" ]; then
            status="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
            [ "$status" = "healthy" ] && { info "'$service' healthy."; return 0; }
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            docker compose logs --tail 30 "$service" >&2 || true
            die "'$service' không healthy sau ${timeout}s."
        fi
        sleep 2
    done
}

info "Giữ nguyên volumes/schema/Keycloak/secrets; dừng service ứng dụng..."
docker compose stop api worker scheduler minicrm frontend crm-frontend >/dev/null 2>&1 || true
info "Khởi động PostgreSQL/Redis nền tảng..."
docker compose up -d db minicrm_db redis
wait_healthy db; wait_healthy minicrm_db; wait_healthy redis
info "Build image để migration/bootstrap dùng source hiện tại..."
docker compose build api minicrm

detect_minicrm_head() {
    local heads head_count head
    heads="$(docker compose run --rm --no-deps -e MINICRM_RUN_MIGRATIONS=false \
        minicrm alembic -c alembic.ini heads)" \
        || die "không đọc được Mini CRM Alembic head."
    head_count="$(printf '%s\n' "$heads" | awk '$NF == "(head)" { count++ } END { print count + 0 }')"
    [ "$head_count" = "1" ] || die "Mini CRM phải có đúng một Alembic head; tìm thấy $head_count."
    head="$(printf '%s\n' "$heads" | awk '$NF == "(head)" { print $(NF - 1); exit }')"
    case "$head" in
        ''|*[!A-Za-z0-9_]*) die "Mini CRM Alembic head không hợp lệ: $head" ;;
    esac
    printf '%s' "$head"
}

minicrm_head_revision="$(detect_minicrm_head)"
info "Mini CRM Alembic head: $minicrm_head_revision"
info "AbsorpIQ: alembic upgrade head..."
docker compose run --rm --no-deps -e RUN_MIGRATIONS=false api alembic upgrade head
info "Mini CRM: alembic upgrade head..."
docker compose run --rm --no-deps -e MINICRM_RUN_MIGRATIONS=false minicrm alembic -c alembic.ini upgrade head

actual_absorpiq_db="$(docker compose exec -T db psql -X -tAc 'SELECT current_database()' -U "$POSTGRES_USER" -d "$POSTGRES_DB" | tr -d '[:space:]')"
actual_minicrm_db="$(docker compose exec -T minicrm_db psql -X -tAc 'SELECT current_database()' -U "$MINICRM_POSTGRES_USER" -d "$MINICRM_POSTGRES_DB" | tr -d '[:space:]')"
[ "$actual_absorpiq_db" = "$POSTGRES_DB" ] || die "database identity AbsorpIQ không khớp cấu hình."
[ "$actual_minicrm_db" = "$MINICRM_POSTGRES_DB" ] || die "database identity Mini CRM không khớp cấu hình."
info "Database identity OK: AbsorpIQ=$actual_absorpiq_db, Mini CRM=$actual_minicrm_db."

info "Resetting Mini CRM..."
docker compose exec -T minicrm_db psql -X -v ON_ERROR_STOP=1 \
    -v "expected_revision=$minicrm_head_revision" \
    -U "$MINICRM_POSTGRES_USER" -d "$MINICRM_POSTGRES_DB" < scripts/dev-hard-reset-minicrm.sql
info "Resetting AbsorpIQ..."
docker compose exec -T db psql -X -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" < scripts/dev-hard-reset-absorpiq.sql

info "Đảm bảo đúng một sync_credentials active, khớp secret file + .env + minicrm/.env..."
bash scripts/ensure_sync_credential.sh

info "Khởi động lại services (không recreate volume)..."
docker compose up -d keycloak api worker scheduler minicrm frontend crm-frontend
wait_healthy keycloak 120; wait_healthy api 90; wait_healthy minicrm 90
if [ "$SEED" = "true" ]; then
    if [ -x .venv/bin/python ]; then
        seed_python=.venv/bin/python
    else
        command -v python3 >/dev/null 2>&1 || die "--seed cần python3 hoặc .venv/bin/python trên host."
        seed_python=python3
    fi
    info "Nạp fixture Mini CRM qua application/API và transactional outbox..."
    "$seed_python" -m scripts.seed_mini_crm_from_json --skip-verify
fi
info "Done. Schema/migrations/config/secrets giữ nguyên; data rows trong allowlist đã xóa."
info "Kiểm tra stack: docker compose ps"
