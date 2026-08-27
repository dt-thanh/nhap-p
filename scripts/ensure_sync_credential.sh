#!/usr/bin/env bash
# Ensures exactly one active Mini CRM sync credential exists AND that
# .dev-secrets/minicrm_sync_api_key, .env, and minicrm/.env all agree on it.
# Idempotent: reuses an existing valid credential (bootstrap_dev.py's own
# "existing" branch) rather than rotating. Called from dev-reset.sh (after
# migrations, before `up -d api/minicrm`) and dev-up.sh. Requires: db + api
# image already built/migrated, docker compose reachable from cwd.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
SECRET_FILE="$REPO_ROOT/.dev-secrets/minicrm_sync_api_key"
POSTGRES_USER="${POSTGRES_USER:-app}"
POSTGRES_DB="${POSTGRES_DB:-absorption}"

info() { echo "[ensure-sync-credential] $*"; }
die() { echo "[ensure-sync-credential] LỖI: $*" >&2; exit 1; }

info "bootstrap_dev: đảm bảo đúng một credential active (issue nếu chưa có, giữ nguyên nếu đã hợp lệ)..."
docker compose run --rm --no-deps -e RUN_MIGRATIONS=false api python -m scripts.bootstrap_dev \
    --no-seed --credential-output-file /app/.dev-secrets/minicrm_sync_api_key \
    || die "bootstrap_dev thất bại — xem log ở trên."

[ -s "$SECRET_FILE" ] || die "$SECRET_FILE rỗng/không tồn tại sau bootstrap_dev."

KEY="$(cat "$SECRET_FILE")"
for env_file in .env minicrm/.env; do
    grep -q '^MINICRM_SYNC_API_KEY=' "$env_file" 2>/dev/null \
        || die "$env_file thiếu đúng một dòng MINICRM_SYNC_API_KEY= — chạy scripts/bootstrap_env.sh trước."
    sed -i "s|^MINICRM_SYNC_API_KEY=.*|MINICRM_SYNC_API_KEY=${KEY}|" "$env_file"
done
info "đã đồng bộ .env + minicrm/.env từ $SECRET_FILE (giá trị KHÔNG in ra)."

active="$(docker compose exec -T db psql -X -tAc \
    "SELECT count(*) FROM sync_credentials WHERE source_system='mini_crm' AND source_instance_id='mini-crm-dev' AND revoked_at IS NULL" \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" | tr -d '[:space:]')"
[ "$active" = "1" ] || die "sau bootstrap: $active active sync_credentials row(s), kỳ vọng đúng 1."
info "OK: đúng 1 active sync_credentials row, khớp prefix $(head -c 8 "$SECRET_FILE")."
