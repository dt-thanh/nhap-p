#!/usr/bin/env bash
# Fails loudly if MINICRM_SYNC_API_KEY drifts between the Compose secret
# file (source of truth), .env, minicrm/.env, and the active DB credential.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SECRET_FILE=".dev-secrets/minicrm_sync_api_key"
[ -f "$SECRET_FILE" ] || { echo "MISSING: $SECRET_FILE"; exit 1; }

file_prefix="$(head -c 8 "$SECRET_FILE")"
env_prefix="$(grep '^MINICRM_SYNC_API_KEY=' .env | cut -d= -f2- | head -c 8)"
crm_env_prefix="$(grep '^MINICRM_SYNC_API_KEY=' minicrm/.env | cut -d= -f2- | head -c 8)"
db_prefix="$(docker compose exec -T db psql -U app -d absorption -tA -c \
  "SELECT key_prefix FROM sync_credentials WHERE revoked_at IS NULL ORDER BY created_at DESC LIMIT 1;" | tr -d '[:space:]')"
active_count="$(docker compose exec -T db psql -U app -d absorption -tA -c \
  "SELECT count(*) FROM sync_credentials WHERE revoked_at IS NULL;" | tr -d '[:space:]')"

fail=0
if [ "$active_count" != "1" ]; then
  echo "DRIFT: sync_credentials has $active_count active row(s), expected exactly 1"
  fail=1
fi
for pair in "env:$env_prefix" "minicrm/.env:$crm_env_prefix" "db(active):$db_prefix"; do
  name="${pair%%:*}"; val="${pair#*:}"
  if [ "$val" != "$file_prefix" ]; then
    echo "DRIFT: $name=$val != secret_file=$file_prefix"
    fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "OK: all sources agree (prefix $file_prefix), exactly 1 active sync_credentials row"
else
  echo "Run: KEY=\$(cat $SECRET_FILE); sed -i \"s|^MINICRM_SYNC_API_KEY=.*|MINICRM_SYNC_API_KEY=\${KEY}|\" .env minicrm/.env"
  exit 1
fi
