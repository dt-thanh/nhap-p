#!/usr/bin/env bash
# Clear only AbsorpIQ business data, then rebuild it through MiniCRM CRUD/API.
#
#   ./scripts/dev-reseed-from-minicrm.sh --yes
#
# This intentionally does not reset either database, Keycloak, volumes, users,
# settings, or sync credentials.  MiniCRM is the source of truth; the
# --refresh-existing flag causes the existing fixture records to be PATCHed via
# the real MiniCRM API so the normal transactional outbox/relay path emits a
# fresh event after the destination clear.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

confirmed=false
for arg in "$@"; do
    case "$arg" in
        --yes) confirmed=true ;;
        *) echo "[dev-reseed] unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if [ "$confirmed" != true ]; then
    cat <<'EOF'
[dev-reseed] no writes performed. Planned steps:
  1. build the api image so the checked-in clear script is used;
  2. run `alembic upgrade head` without changing migration files;
  3. validate and clear only allowlisted AbsorpIQ business tables;
  4. PATCH the existing MiniCRM fixture through its HTTP API with
     `--refresh-existing`, then wait for outbox delivery and projections.

Re-run with: ./scripts/dev-reseed-from-minicrm.sh --yes
EOF
    exit 0
fi

app_env=""
if [ -f .env ]; then
    app_env="$(awk -F= '$1 == "APP_ENV" {print substr($0, index($0, "=") + 1); exit}' .env)"
fi
[ "$app_env" = "development" ] || {
    echo "[dev-reseed] refused: APP_ENV must be exactly development." >&2
    exit 1
}

command -v docker >/dev/null 2>&1 || {
    echo "[dev-reseed] docker is required." >&2
    exit 1
}
docker compose version >/dev/null 2>&1 || {
    echo "[dev-reseed] Docker Compose v2 is required." >&2
    exit 1
}

echo "[dev-reseed] building api image..."
docker compose build api

echo "[dev-reseed] verifying/applying Alembic head..."
docker compose run --rm -e RUN_MIGRATIONS=false api alembic upgrade head

echo "[dev-reseed] clearing AbsorpIQ business data (preserved tables are checked by the script)..."
docker compose run --rm -e RUN_MIGRATIONS=false api python -m scripts.clear_absorpiq_data --yes

if [ -x .venv/bin/python ]; then
    python_bin=".venv/bin/python"
else
    python_bin="python3"
fi

echo "[dev-reseed] reseeding through MiniCRM HTTP CRUD and waiting for relay/projections..."
seed_args=(--refresh-existing)
if grep -Eq '^DASHBOARD_ADMIN_TOKEN=[^[:space:]]+' .env 2>/dev/null; then
    echo "[dev-reseed] backend projection verification enabled."
else
    echo "[dev-reseed] DASHBOARD_ADMIN_TOKEN is not configured; using MiniCRM/outbox sync without Phase D API verification."
    seed_args+=(--skip-verify)
fi
"$python_bin" -m scripts.seed_mini_crm_from_json "${seed_args[@]}"

echo "[dev-reseed] complete: AbsorpIQ was rebuilt from MiniCRM through the existing outbox/relay path."
