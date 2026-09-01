"""DEV-only AI/CRM deal fixture, derived from the seeded units + legacy trend

Revision ID: 0021_seed_ai_crm_fixture_deals
Revises: 0020_agent_advisory_execution
Create Date: 2026-08-15

A DATA migration, not a schema migration — no table/column/constraint is added,
altered, or dropped.

╔══════════════════════════════════════════════════════════════════════════╗
║  `upgrade()` IS NOW A NO-OP (2026-08-28). Alembic must never auto-seed    ║
║  business/domain fixture data on a fresh database — see AGENTS.md's       ║
║  "MiniCRM is the sole owner of Project/Area/Unit/Deal" invariant and the  ║
║  target-invariant audit recorded in `pipeline_status.md`. This revision   ║
║  still exists, still occupies its place in the Alembic graph, and still  ║
║  traverses correctly for both a brand-new database (upgrade does nothing)║
║  and an existing database that already applied the OLD version of this   ║
║  revision (Alembic never re-runs an already-stamped revision, so this    ║
║  edit has ZERO effect on any database that already has this fixture's    ║
║  deal rows — `downgrade()` below is UNCHANGED and still correctly        ║
║  reverses them by source identity for such a database).                  ║
║                                                                            ║
║  The exact same deterministic deal-planning algorithm this revision used ║
║  to run automatically now lives in `scripts/_seed_legacy_fixture_deals_  ║
║  core.py::plan_deals()`, unchanged, reused (not duplicated) by the       ║
║  explicit, confirmed, dev-only CLI:                                      ║
║                                                                            ║
║      python -m scripts.seed_legacy_fixture --dry-run                     ║
║      python -m scripts.seed_legacy_fixture --confirm-seed                ║
║                                                                            ║
║  It writes DIRECTLY to Backend tables via SQLAlchemy Core when invoked   ║
║  through that CLI — the same mechanism this revision always used. It     ║
║  does NOT exercise Mini CRM → crm_outbox → relay → DomainProjector,      ║
║  which is debugged and tested completely separately, and its output is  ║
║  explicitly non-authoritative (never valid for CRM reconciliation or     ║
║  authoritative ranking).                                                 ║
╚══════════════════════════════════════════════════════════════════════════╝

Fixture identity — identical to 0019, so the two seed together (when invoked
explicitly) and downgrade together, and neither can be confused with real
Mini CRM-synced rows:

    source_system      = "crm_real_data_fixture"
    source_instance_id = "ai-dev-fixture"

`mini-crm-dev` (the real instance id) can never produce or match these values.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_seed_ai_crm_fixture_deals"
down_revision: str | None = "0020_agent_advisory_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Unchanged from the original version of this revision — `downgrade()` below
# still keys off these exact values, matching whatever a database that
# already applied the OLD `upgrade()` actually has.
SOURCE_SYSTEM = "crm_real_data_fixture"
SOURCE_INSTANCE_ID = "ai-dev-fixture"


def upgrade() -> None:
    print(
        "=== 0021_seed_ai_crm_fixture_deals: upgrade() is now a no-op — "
        "use `python -m scripts.seed_legacy_fixture --confirm-seed` to seed this "
        "fixture explicitly (never automatic on `alembic upgrade head`). ==="
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Order matters: identify the units through the fixture deals BEFORE
    # deleting those deals, or there is nothing left to identify them by.
    bind.execute(
        sa.text(
            """
            UPDATE units SET status='available',
                             updated_at = GREATEST(clock_timestamp(), created_at)
            WHERE status = 'sold'
              AND id IN (
                  SELECT unit_id FROM deals
                  WHERE source_system = :sys AND source_instance_id = :inst
                    AND status = 'sold'
              )
            """
        ),
        {"sys": SOURCE_SYSTEM, "inst": SOURCE_INSTANCE_ID},
    )
    bind.execute(
        sa.text("DELETE FROM deals WHERE source_system = :sys AND source_instance_id = :inst"),
        {"sys": SOURCE_SYSTEM, "inst": SOURCE_INSTANCE_ID},
    )
