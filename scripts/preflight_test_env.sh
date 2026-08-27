#!/usr/bin/env bash
# Read-only, non-destructive preflight for DB-backed test runs.
#
# Checks host disk, Docker disk usage, and Postgres/Redis service health
# BEFORE any long test/migration run starts — added after a release-hardening
# incident where the host filesystem hit 100% full mid-sweep, crashing the
# `db` container (`FATAL: could not write lock file "postmaster.pid": No
# space left on device`) and orphaning rows that later broke an unrelated
# test file's fixture.
#
# This script NEVER deletes, prunes, or modifies anything — on insufficient
# space it fails fast with the observed/required numbers and remediation
# guidance, and starts no test/migration. Freeing space (including deciding
# whether to `docker system prune`/`docker volume prune`) is an explicit,
# separate, human decision — never automatic here.
#
#   bash scripts/preflight_test_env.sh            # human-readable
#   bash scripts/preflight_test_env.sh --quiet     # only prints on failure
#
# Threshold is configurable, not hardcoded: MIN_FREE_DISK_MB (default 2048 —
# conservative; `scripts/test_db.sh`'s own migration+test workload has been
# observed to need a few hundred MB of Postgres WAL/temp-file churn, and this
# leaves headroom).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MIN_FREE_DISK_MB="${MIN_FREE_DISK_MB:-2048}"
DB_SERVICE="${DB_SERVICE:-db}"
REDIS_SERVICE="${REDIS_SERVICE:-redis}"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

FAIL=0
_ok()   { [ "$QUIET" -eq 1 ] || echo "[preflight] OK: $*"; }
_warn() { echo "[preflight] WARN: $*" >&2; }
_fail() { echo "[preflight] FAIL: $*" >&2; FAIL=1; }

# --- 1. Host disk ------------------------------------------------------------
# `df --output=avail` in 1K blocks -> MB. Read-only.
read -r avail_kb < <(df --output=avail -k / | tail -1)
avail_mb=$((avail_kb / 1024))
avail_pct="$(df -h / | tail -1 | awk '{print $5}')"
if [ "$avail_mb" -lt "$MIN_FREE_DISK_MB" ]; then
    _fail "host filesystem '/' has ${avail_mb}MB free (used ${avail_pct}), below the required ${MIN_FREE_DISK_MB}MB threshold."
    echo "  Remediation: free space before running DB tests/migrations. This script will not do it for you —" >&2
    echo "  check 'docker system df' for reclaimable image/volume space and decide with the repo owner whether" >&2
    echo "  'docker system prune'/'docker volume prune' (destructive, out of this preflight's scope) is safe here." >&2
else
    _ok "host disk: ${avail_mb}MB free (used ${avail_pct}), threshold ${MIN_FREE_DISK_MB}MB"
fi

# --- 2. Docker disk usage (report only, never pruned here) ------------------
if command -v docker >/dev/null 2>&1; then
    docker_df="$(docker system df --format '{{.Type}}: {{.Size}} total, {{.Reclaimable}} reclaimable' 2>/dev/null || true)"
    if [ -n "$docker_df" ]; then
        [ "$QUIET" -eq 1 ] || { echo "[preflight] Docker disk usage (informational only, nothing pruned):"; echo "$docker_df" | sed 's/^/  /'; }
    else
        _warn "could not read 'docker system df' — Docker daemon may be unavailable."
    fi
else
    _fail "docker not found on PATH."
fi

# --- 3. Postgres service health ----------------------------------------------
if command -v docker >/dev/null 2>&1; then
    if docker compose ps "$DB_SERVICE" 2>/dev/null | grep -q "$DB_SERVICE"; then
        if docker compose exec -T "$DB_SERVICE" pg_isready >/dev/null 2>&1; then
            _ok "Postgres service '$DB_SERVICE' is up and accepting connections."
        else
            _fail "Postgres service '$DB_SERVICE' is running but not accepting connections (pg_isready failed)."
        fi
    else
        _warn "Postgres service '$DB_SERVICE' is not running yet — 'scripts/test_db.sh' will start it."
    fi
fi

# --- 4. Redis service health (only relevant for auth/session tests) ---------
if command -v docker >/dev/null 2>&1; then
    if docker compose ps "$REDIS_SERVICE" 2>/dev/null | grep -q "$REDIS_SERVICE"; then
        if docker compose exec -T "$REDIS_SERVICE" redis-cli ping 2>/dev/null | grep -qi pong; then
            _ok "Redis service '$REDIS_SERVICE' is up and responding to PING."
        else
            _warn "Redis service '$REDIS_SERVICE' is running but did not answer PING — session/CEO-auth tests that need it may fail."
        fi
    else
        _warn "Redis service '$REDIS_SERVICE' is not running — start it with 'docker compose up -d $REDIS_SERVICE' if running tests/auth/."
    fi
fi

# --- 5. Alembic/DB connectivity (only if a test URL is already configured) --
url="${TEST_DATABASE_URL:-${DATABASE_URL:-}}"
if [ -n "$url" ]; then
    if .venv/bin/python -m alembic current >/dev/null 2>&1; then
        _ok "Alembic can reach the configured database and read its revision."
    else
        _warn "Alembic could not read the configured database's current revision (may not exist yet — 'scripts/test_db.sh' creates it)."
    fi
fi

if [ "$FAIL" -eq 1 ]; then
    echo "[preflight] BLOCKED: one or more checks above failed. No test/migration was started." >&2
    exit 1
fi
[ "$QUIET" -eq 1 ] || echo "[preflight] all checks passed."
exit 0
