"""PR-6: enable the minimum auditable Legal workflow (D27/D38) — one
Project-scope categorical feature (`project_legal_status`), reusing the
existing typed feature store and PR-2 value-mode governance path end to end.

Revision ID: 0042_legal_assertion_gate
Revises: 0041_area_grain_scope
Create Date: 2026-08-27

Two additive changes, both narrower than PR-4/PR-5's own (no new area/unit
shape to reconcile — Legal, like Project/Market, is denormalized per-project,
`area_id`/`unit_id` always NULL):

1. **A fourth `ranking_feature_snapshots`/`ranking_feature_values.scope_type`
   value, `'legal'`.** Legal is deliberately NOT folded into the existing
   `scope_type='project'` snapshot Project's own M/P features already use:
   its only consumer is the hierarchical gate (never `engine.score_unit()`'s
   weighted inputs), and giving it its own scope_type keeps its snapshot row,
   value row, and lineage row structurally isolated from Project's numeric
   factors — the same "one row per its own concern" discipline `0040`
   (Market) already established, not a new pattern. The `ck_rfs_scope_shape`/
   `ck_rfv_scope_shape` CHECKs (`0041`) widen their `area_id IS NULL` branch
   to also cover `'legal'`; the existing partial unique index
   `uq_rfs_run_project_scope_no_area` (`ranking_run_id, project_id,
   scope_type` WHERE `area_id IS NULL`, `0041`) already covers ANY scope_type
   with a NULL `area_id` — Legal gets its one-snapshot-per-run-per-project
   guarantee for free, no new index needed. The composite FK
   (`ranking_feature_values.snapshot_id/project_id/scope_type/area_id` ->
   `ranking_feature_snapshots.id/project_id/scope_type/area_id`, `0041`)
   needs no change either — both sides already widen by scope_type value,
   not by column.

2. **Seeds one Project-grain, categorical `ranking_feature_definitions` row:
   `project_legal_status`.** `value_type='categorical'` and `grain='project'`
   already exist as allowed values since `0033`/`0038` — no CHECK widening
   needed there. Vocabulary (`HIGH_RISK`/`NOT_HIGH_RISK`/`UNKNOWN` — the
   minimal D40-safe set the gate needs, NOT the broader D40 vocabulary
   decision, which stays PENDING) is recorded in
   `definition_metadata.allowed_categorical_values`, re-validated at write
   time by `src/services/governance.py` (`upsert_justification()` and
   `validate_value_assertion_for_materialization()`), the same
   metadata-driven-policy pattern `0040`'s Market shelf-life already
   established (`_MARKET_MAX_SHELF_LIFE_DAYS`/`_max_shelf_life_days()`) —
   deliberately NOT a table-wide `categorical_value` CHECK: `categorical_value`
   is a column shared by every present-and-future categorical feature
   definition, and a blanket CHECK restricting it to Legal's three-value
   vocabulary would silently misapply Legal's vocabulary to any other
   categorical feature added later. A CHECK conditional on
   `feature_key='project_legal_status'` cannot be expressed as a single-table
   Postgres CHECK (it requires joining to `ranking_feature_definitions`) —
   `ranking_consultant.md` line ~3220 already names this exact tension as
   part of D40, unresolved by this migration on purpose.

   `direction`/`missing_policy` are set to a neutral placeholder
   (`'neutral'`/`'skip'`) and are never read: Legal is never a member of any
   `hierarchical_weights` grain's feature-weight map and never reaches
   `engine.score_unit()`/`engine.oriented()` — see
   `src/ranking/service.py::compute_hierarchical_scores_for_run()`'s Legal
   gate, which reads this feature's materialized value directly, never
   through the weighted-mean path Project/Market/Area's own features use.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042_legal_assertion_gate"
down_revision: str | None = "0041_area_grain_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB

LEGAL_FEATURE_KEY = "project_legal_status"
LEGAL_ALLOWED_CATEGORICAL_VALUES = ("HIGH_RISK", "NOT_HIGH_RISK", "UNKNOWN")


def _has_rows(table: str, where: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {where})")).scalar())


def upgrade() -> None:
    # --- 1. Widen scope_type CHECKs to admit 'legal' ---
    op.drop_constraint("ck_rfs_scope_type_allowed", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_type_allowed",
        "ranking_feature_snapshots",
        "scope_type IN ('project', 'market', 'area', 'legal')",
    )
    op.drop_constraint("ck_rfv_scope_type_allowed", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_type_allowed",
        "ranking_feature_values",
        "scope_type IN ('project', 'market', 'area', 'legal')",
    )

    # --- 2. Widen the area_id/unit_id shape CHECKs: 'legal' behaves exactly
    # like 'project'/'market' (denormalized per-project, no area/unit) ---
    op.drop_constraint("ck_rfs_scope_shape", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_shape",
        "ranking_feature_snapshots",
        "(scope_type IN ('project', 'market', 'legal') AND area_id IS NULL) "
        "OR (scope_type = 'area' AND area_id IS NOT NULL)",
    )
    op.drop_constraint("ck_rfv_scope_shape", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_shape",
        "ranking_feature_values",
        "(scope_type IN ('project', 'market', 'legal') AND area_id IS NULL AND unit_id IS NULL) "
        "OR (scope_type = 'area' AND area_id IS NOT NULL AND unit_id IS NULL)",
    )

    # --- 3. Seed the one Legal feature definition ---
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
                "feature_key": LEGAL_FEATURE_KEY,
                "feature_version": "v1",
                "name": "Project legal status",
                "category": "legal",
                "grain": "project",
                "value_type": "categorical",
                "formula_id": "expert_value_assertion",
                "normalization_method": "none",
                "direction": "neutral",
                "missing_policy": "skip",
                "status": "active",
                "definition_metadata": {"allowed_categorical_values": list(LEGAL_ALLOWED_CATEGORICAL_VALUES)},
                "created_by": "0042_legal_assertion_gate",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    if _has_rows("ranking_feature_snapshots", "scope_type = 'legal'"):
        raise RuntimeError("Refusing to downgrade 0042: legal-scope ranking_feature_snapshots rows exist")
    if _has_rows("ranking_feature_values", "scope_type = 'legal'"):
        raise RuntimeError("Refusing to downgrade 0042: legal-scope ranking_feature_values rows exist")
    if _has_rows(
        "ranking_feature_definitions",
        f"feature_key = '{LEGAL_FEATURE_KEY}' AND created_by <> '0042_legal_assertion_gate'",
    ):
        raise RuntimeError(
            "Refusing to downgrade 0042: a project_legal_status ranking_feature_definitions row "
            "exists beyond this migration's own seed"
        )

    op.execute(sa.text(f"DELETE FROM ranking_feature_definitions WHERE feature_key = '{LEGAL_FEATURE_KEY}'"))

    op.drop_constraint("ck_rfv_scope_shape", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_shape",
        "ranking_feature_values",
        "(scope_type IN ('project', 'market') AND area_id IS NULL AND unit_id IS NULL) "
        "OR (scope_type = 'area' AND area_id IS NOT NULL AND unit_id IS NULL)",
    )
    op.drop_constraint("ck_rfs_scope_shape", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_shape",
        "ranking_feature_snapshots",
        "(scope_type IN ('project', 'market') AND area_id IS NULL) "
        "OR (scope_type = 'area' AND area_id IS NOT NULL)",
    )

    op.drop_constraint("ck_rfv_scope_type_allowed", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_type_allowed", "ranking_feature_values", "scope_type IN ('project', 'market', 'area')"
    )
    op.drop_constraint("ck_rfs_scope_type_allowed", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_type_allowed", "ranking_feature_snapshots", "scope_type IN ('project', 'market', 'area')"
    )
