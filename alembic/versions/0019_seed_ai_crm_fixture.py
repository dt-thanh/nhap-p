"""DEV-only AI/CRM data fixture, derived from crm_real_data.json

Revision ID: 0019_seed_ai_crm_fixture
Revises: 0018_agent_recommendations
Create Date: 2026-08-14

A DATA migration, not a schema migration — no table/column/constraint is
added, altered, or dropped. It upserts rows into `projects`/`areas`/`units`
(plus `upload_files`/`upload_errors`/`sales_records`/`inventory_snapshots`/
`absorption_daily` to make the dashboard/absorption read paths show something
real) so the AI team has realistic CRM-shaped data to develop against without
depending on Mini CRM being online.

╔══════════════════════════════════════════════════════════════════════════╗
║  Mini CRM is the canonical owner of Project/Area/Unit/Deal. This fixture  ║
║  does NOT prove the Mini CRM → outbox → relay → Backend sync pipeline     ║
║  works — that pipeline is debugged and tested completely separately (see ║
║  `scripts/seed_mini_crm_from_json.py`, `tests/test_api/test_sync_auth.py`)║
║  This revision writes DIRECTLY to Backend tables via SQLAlchemy Core,    ║
║  the same sanctioned dev-seed path `scripts/seed_dev.py` already uses —  ║
║  `ProjectService.create_project`/etc. were narrowed to ingestion-only    ║
║  writers at Phase D, so there is no service-layer path for this anyway.  ║
╚══════════════════════════════════════════════════════════════════════════╝

Fixture identity (the ONLY thing `downgrade()` keys off, and how every row
here is told apart from real Mini CRM-synced data or hand-entered rows):

    source_system      = "crm_real_data_fixture"
    source_instance_id  = "ai-dev-fixture"

Obviously distinct from any real Mini CRM instance id (`mini-crm-dev`) — the
real sync pipeline can never produce or match this value, so this fixture
cannot collide with, or be mistaken for, real ingested data.

Mapping logic lives in `scripts/_seed_ai_crm_fixture_core.py` (shared with the
CLI form `scripts/seed_backend_from_json.py`) — see that module's docstring
for why a migration here imports from `scripts/` (no prior migration does;
`prepend_sys_path = .` in alembic.ini makes it resolve, confirmed live via
`docker compose exec api python -c "import scripts.seed_mini_crm_from_json"`).

Reads the COMMITTED, repo-sized `scripts/fixtures/ingestion_seed.json` (~400 KB) — never
the raw `crm_real_data.json` export (~1 MB, not part of this repo, machine-
local). Fails loudly (`SeedError`, migration aborts) if that file is missing
or fails its own structural validation — see `_seed_ai_crm_fixture_core.load_seed`.

Idempotent: every row's primary key is `uuid5(NS_INGESTION_SEED, "<kind>:<id>")`
— deterministic from the JSON's own ids — and every insert is
`ON CONFLICT (id) DO UPDATE`. Running `upgrade()` twice produces the same row
counts, not duplicates.

`downgrade()` deletes ONLY rows carrying the fixture identity above (or, for
tables with no source_system/instance column of their own — `absorption_daily`,
`sales_records`, `inventory_snapshots`, `upload_errors` — rows transitively
scoped to fixture-owned `areas`/`upload_files` via FK), computed fresh from
the database at downgrade time. It never touches a row without that marker,
so it cannot delete real Mini CRM-synced or hand-entered data even if this
revision is downgraded long after other data has been added.

Explicitly NOT touched by this revision, and why:
- `deals` — zero deal-level records exist in the source; none fabricated.
- `ranking_configs`/`ranking_runs`/`ranking_scores`/`feature_snapshots` —
  Phase 6 tables; `tests/test_ranking_boundary.py` restricts writes to
  `src/ranking/service.py` only. The source's simulated score/rank/band/
  contributions fields (`scoring_meta.method == "simulated_placeholder"`, NOT
  real model output) live in `docs/ai_fixtures/simulated_ranking_fixture.json`
  instead — a plain JSON hand-off, never loaded by this migration.
- `zones` (18 records in the source) — no zone/tower table exists in this
  schema; zone context is already folded into `area_name` by the source data.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from scripts._seed_ai_crm_fixture_core import SeedError, build_downgrade_statements, build_upserts, load_seed

revision: str = "0019_seed_ai_crm_fixture"
down_revision: str | None = "0018_agent_recommendations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    try:
        data = load_seed()
        plan = build_upserts(data)
    except SeedError as exc:
        raise RuntimeError(f"0019_seed_ai_crm_fixture: {exc}") from exc

    bind = op.get_bind()
    for _table_name, stmt in plan.statements:
        bind.execute(stmt)

    print("=== 0019_seed_ai_crm_fixture: mapping report ===")
    for name, n in plan.counts.items():
        print(f"  [{name}] {n} upserted")


def downgrade() -> None:
    bind = op.get_bind()
    for stmt in build_downgrade_statements():
        bind.execute(stmt)
