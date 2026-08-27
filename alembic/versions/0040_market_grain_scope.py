"""PR-4: widen the Project-only feature-store scope to also permit Market,
and seed the four registered Market feature definitions with their 30/90-day
shelf-life metadata.

Revision ID: 0040_market_grain_scope
Revises: 0039_project_value_materialize
Create Date: 2026-08-27

Two additive changes:

1. `ranking_feature_snapshots.scope_type`/`ranking_feature_values.scope_type`
   CHECKs widen from `= 'project'` to `IN ('project', 'market')`. This is the
   ONLY schema change these two tables need for Market — `area_id`/`unit_id`
   shape stays unconditionally NULL for both scope types (Market, like
   Project, is denormalized per-project with no area/unit granularity, D39
   PENDING — `docs/ranking/hierarchical_scoring_implementation_plan.md
   §3.0`), so `ck_rfs_project_scope_no_area`/`ck_rfv_project_scope_shape` are
   untouched. The composite FK
   (`ranking_feature_values.snapshot_id/project_id/scope_type` ->
   `ranking_feature_snapshots.id/project_id/scope_type`) needs no change
   either — both sides widen identically.

   `ranking_feature_definitions.grain` already permits `'market'` since
   `0038` (PR-2) — no change needed there.
   `ranking_weight_proposals`/`ranking_feature_justifications` already
   permit `scope_type='market'` since `0038` (PR-2) — no change needed
   there either. `ranking_feature_lineage` has no grain/scope CHECK at all
   (verified, `0033`) — nothing to widen.

2. Seeds four Market-grain `ranking_feature_definitions` rows
   (`market_interest_rate`, `market_credit_policy`, `market_liquidity`,
   `market_demand`), each carrying `definition_metadata.max_shelf_life_days`
   (30 for interest rate, 90 for the other three — `ranking_consultant.md
   §24.5`'s already-decided shelf-life figures, the same numbers
   `src/services/governance.py::_MARKET_MAX_SHELF_LIFE_DAYS`/
   `_MARKET_DEFAULT_MAX_SHELF_LIFE_DAYS` already encode as a Python-side
   fallback for submission-time validation). This is the "additive
   migration/data-seed path" the task requires — no arbitrary Market
   feature key is registered, only these four.

   `direction`/`normalization_method` are a reasonable, disclosed modeling
   default (not a tracked D-decision): interest rate is `negative` (higher
   rate is worse for absorption), credit policy/liquidity/demand are
   `positive` (looser/higher is better) — consistent with how every other
   normalized `[0,1]` "higher raw score is better after orientation" feature
   in this system is already modeled (`engine.py::oriented()`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040_market_grain_scope"
down_revision: str | None = "0039_project_value_materialize"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB

MARKET_FEATURE_DEFINITIONS = (
    {
        "feature_key": "market_interest_rate",
        "name": "Market interest rate",
        "direction": "negative",
        "max_shelf_life_days": 30,
    },
    {
        "feature_key": "market_credit_policy",
        "name": "Market credit policy",
        "direction": "positive",
        "max_shelf_life_days": 90,
    },
    {
        "feature_key": "market_liquidity",
        "name": "Market liquidity",
        "direction": "positive",
        "max_shelf_life_days": 90,
    },
    {
        "feature_key": "market_demand",
        "name": "Market demand",
        "direction": "positive",
        "max_shelf_life_days": 90,
    },
)


def _has_rows(table: str, where: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {where})")).scalar())


def upgrade() -> None:
    op.drop_constraint("ck_rfs_scope_type_project", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_type_allowed",
        "ranking_feature_snapshots",
        "scope_type IN ('project', 'market')",
    )

    op.drop_constraint("ck_rfv_scope_type_project", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_type_allowed",
        "ranking_feature_values",
        "scope_type IN ('project', 'market')",
    )

    now = datetime.now(UTC)
    feature_definitions = sa.table(
        "ranking_feature_definitions",
        sa.column("id", UUID),
        sa.column("feature_key", sa.Text()),
        sa.column("feature_version", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("grain", sa.Text()),
        sa.column("value_type", sa.Text()),
        sa.column("formula_id", sa.Text()),
        sa.column("normalization_method", sa.Text()),
        sa.column("direction", sa.Text()),
        sa.column("missing_policy", sa.Text()),
        sa.column("status", sa.Text()),
        sa.column("definition_metadata", JSONB),
        sa.column("created_by", sa.Text()),
        sa.column("created_at", sa.TIMESTAMP(timezone=True)),
        sa.column("updated_at", sa.TIMESTAMP(timezone=True)),
    )
    op.bulk_insert(
        feature_definitions,
        [
            {
                "id": uuid.uuid4(),
                "feature_key": spec["feature_key"],
                "feature_version": "v1",
                "name": spec["name"],
                "category": "market",
                "grain": "market",
                "value_type": "numeric",
                "formula_id": "expert_value_assertion",
                "normalization_method": "identity",
                "direction": spec["direction"],
                "missing_policy": "skip",
                "status": "active",
                "definition_metadata": {"max_shelf_life_days": spec["max_shelf_life_days"]},
                "created_by": "0040_market_grain_scope",
                "created_at": now,
                "updated_at": now,
            }
            for spec in MARKET_FEATURE_DEFINITIONS
        ],
    )


def downgrade() -> None:
    if _has_rows("ranking_feature_snapshots", "scope_type = 'market'"):
        raise RuntimeError("Refusing to downgrade 0040: market-scope ranking_feature_snapshots rows exist")
    if _has_rows("ranking_feature_values", "scope_type = 'market'"):
        raise RuntimeError("Refusing to downgrade 0040: market-scope ranking_feature_values rows exist")
    if _has_rows(
        "ranking_feature_definitions",
        "grain = 'market' AND created_by <> '0040_market_grain_scope'",
    ):
        raise RuntimeError(
            "Refusing to downgrade 0040: market-grain ranking_feature_definitions rows exist "
            "beyond this migration's own seed"
        )

    op.execute(sa.text("DELETE FROM ranking_feature_definitions WHERE created_by = '0040_market_grain_scope'"))

    op.drop_constraint("ck_rfv_scope_type_allowed", "ranking_feature_values", type_="check")
    op.create_check_constraint("ck_rfv_scope_type_project", "ranking_feature_values", "scope_type = 'project'")

    op.drop_constraint("ck_rfs_scope_type_allowed", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint("ck_rfs_scope_type_project", "ranking_feature_snapshots", "scope_type = 'project'")
