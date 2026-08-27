"""PR-5: widen the Project/Market feature-store scope to also permit Area,
give Area snapshots real per-area identity (unlike Project/Market, which are
denormalized per-project with no area granularity), and seed the three
registered expert Area feature definitions.

Revision ID: 0041_area_grain_scope
Revises: 0040_market_grain_scope
Create Date: 2026-08-27

Three additive/coupled changes:

1. `ranking_feature_snapshots.scope_type`/`ranking_feature_values.scope_type`
   CHECKs widen from `IN ('project', 'market')` to
   `IN ('project', 'market', 'area')`.

2. **Area needs real per-area identity — Project/Market do not.** Every
   existing scope so far is denormalized per-project (`area_id` always
   NULL), so `ck_rfs_project_scope_no_area` (`area_id IS NULL`) and
   `ck_rfv_project_scope_shape` (`area_id IS NULL AND unit_id IS NULL`) were
   unconditional. Area is a real per-area grain (`docs/ranking/
   hierarchical_scoring_implementation_plan.md §3.0`/`ranking_consultant.md
   §24.4`: "Every Area value must include project_id and area_id"), so both
   CHECKs become scope-conditional (`ck_rfs_scope_shape`/`ck_rfv_scope_shape`
   below) — `area_id IS NULL` for `project`/`market`, `area_id IS NOT NULL`
   for `area`; `unit_id` stays unconditionally NULL for all three (no
   scope here is unit-grain).

   This forces a matching, genuinely additive change to snapshot identity:
   `uq_ranking_feature_snapshot_run_project_scope (ranking_run_id,
   project_id, scope_type)` is the exact schema conflict the PR-5 task
   itself anticipates ("If current snapshot uniqueness is only one row per
   (ranking_run_id, project_id, scope_type), do not silently create an
   invalid second Area snapshot for each area"). Naively adding `area_id` to
   that one constraint would silently BREAK Project/Market's existing
   one-row guarantee: a plain SQL UNIQUE constraint treats every NULL as
   distinct from every other NULL, so two `scope_type='project'` snapshot
   rows (both `area_id IS NULL`) would no longer collide. The correct
   additive fix is two PARTIAL unique indexes instead of one constraint:
     - `uq_rfs_run_project_scope_no_area` — unchanged one-row-per-
       (run, project, scope) guarantee, restricted to `area_id IS NULL`
       (Project/Market, exactly today's behavior).
     - `uq_rfs_run_project_area_scope` — one row per
       (run, project, scope, area_id), restricted to `area_id IS NOT NULL`
       (Area only — every area gets its OWN immutable snapshot).

   The composite FK target `uq_ranking_feature_snapshot_id_project_scope
   (id, project_id, scope_type)` widens to include `area_id` — safe
   unconditionally (no partial index needed) since `id` alone is already
   globally unique; this is purely there to let
   `ranking_feature_values.fk_rfv_snapshot_project_scope` widen to the same
   four columns. Under Postgres's default `MATCH SIMPLE` composite-FK
   semantics, a child row with `area_id IS NULL` (Project/Market) is
   entirely exempt from FK enforcement on that column, so this changes
   nothing for Project/Market; a child row with `area_id IS NOT NULL`
   (Area) now gets a real DB-level guarantee that its `area_id` matches its
   own snapshot's `area_id` — impossible to express as a same-table CHECK,
   since it is a cross-table equality.

3. Seeds three Area-grain, expert-owned `ranking_feature_definitions` rows
   (`area_accessibility`, `area_current_infrastructure`,
   `area_future_infrastructure`) — the ONLY value-mode-assertable Area
   feature keys (`ranking_consultant.md §24.5`). The two CRM-owned Area
   keys (`area_velocity_norm`/`area_conversion_norm`) deliberately get NO
   `ranking_feature_definitions` row: they are legacy operational features
   (`src/ranking/service.py::_area_features()`, `src/services/
   ranking_config.py::OPERATIONAL_FEATURES`), never governance-authored,
   and `src/services/governance.py::upsert_justification()` already
   requires an existing `feature_definition_id` — the absence of a
   definition row is itself what makes an expert value-mode assertion for
   either CRM-owned key impossible (defense-in-depth for this is added at
   the service layer too, see `governance.py`).

   `ranking_feature_definitions.grain` already permits `'area'` since
   `0038` (PR-2). `ranking_weight_proposals`/`ranking_feature_justifications`
   already permit `scope_type='area'` + `area_id` since `0038` (PR-2) —
   no change needed there. `ranking_feature_lineage` has no grain/scope
   CHECK at all (verified, `0033`) — nothing to widen.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0041_area_grain_scope"
down_revision: str | None = "0040_market_grain_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB

AREA_FEATURE_DEFINITIONS = (
    {"feature_key": "area_accessibility", "name": "Area accessibility"},
    {"feature_key": "area_current_infrastructure", "name": "Area current infrastructure"},
    {"feature_key": "area_future_infrastructure", "name": "Area future infrastructure"},
)


def _has_rows(table: str, where: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {where})")).scalar())


def upgrade() -> None:
    # --- 1. Drop the child FK before touching its parent unique constraint ---
    op.drop_constraint("fk_rfv_snapshot_project_scope", "ranking_feature_values", type_="foreignkey")

    # --- 2. Widen the FK target to include area_id (unconditionally safe: `id`
    # alone is already globally unique, so this adds no new dedup semantics) ---
    op.drop_constraint(
        "uq_ranking_feature_snapshot_id_project_scope", "ranking_feature_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_ranking_feature_snapshot_id_project_scope",
        "ranking_feature_snapshots",
        ["id", "project_id", "scope_type", "area_id"],
    )
    op.create_foreign_key(
        "fk_rfv_snapshot_project_scope",
        "ranking_feature_values",
        "ranking_feature_snapshots",
        ["snapshot_id", "project_id", "scope_type", "area_id"],
        ["id", "project_id", "scope_type", "area_id"],
        ondelete="CASCADE",
    )

    # --- 3. Replace the single run/project/scope uniqueness guarantee with
    # two partial indexes so Project/Market's one-row invariant and Area's
    # one-row-per-area invariant can both hold (see module docstring) ---
    op.drop_constraint(
        "uq_ranking_feature_snapshot_run_project_scope", "ranking_feature_snapshots", type_="unique"
    )
    op.create_index(
        "uq_rfs_run_project_scope_no_area",
        "ranking_feature_snapshots",
        ["ranking_run_id", "project_id", "scope_type"],
        unique=True,
        postgresql_where=sa.text("area_id IS NULL"),
    )
    op.create_index(
        "uq_rfs_run_project_area_scope",
        "ranking_feature_snapshots",
        ["ranking_run_id", "project_id", "scope_type", "area_id"],
        unique=True,
        postgresql_where=sa.text("area_id IS NOT NULL"),
    )

    # --- 4. Widen scope_type CHECKs to admit 'area' ---
    op.drop_constraint("ck_rfs_scope_type_allowed", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_type_allowed",
        "ranking_feature_snapshots",
        "scope_type IN ('project', 'market', 'area')",
    )
    op.drop_constraint("ck_rfv_scope_type_allowed", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_type_allowed",
        "ranking_feature_values",
        "scope_type IN ('project', 'market', 'area')",
    )

    # --- 5. Make the area_id/unit_id shape CHECKs scope-conditional ---
    op.drop_constraint("ck_rfs_project_scope_no_area", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_shape",
        "ranking_feature_snapshots",
        "(scope_type IN ('project', 'market') AND area_id IS NULL) "
        "OR (scope_type = 'area' AND area_id IS NOT NULL)",
    )
    op.drop_constraint("ck_rfv_project_scope_shape", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_shape",
        "ranking_feature_values",
        "(scope_type IN ('project', 'market') AND area_id IS NULL AND unit_id IS NULL) "
        "OR (scope_type = 'area' AND area_id IS NOT NULL AND unit_id IS NULL)",
    )

    # --- 6. Seed the three expert Area feature definitions ---
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
                "category": "area",
                "grain": "area",
                "value_type": "numeric",
                "formula_id": "expert_value_assertion",
                "normalization_method": "identity",
                "direction": "positive",
                "missing_policy": "skip",
                "status": "active",
                "definition_metadata": {},
                "created_by": "0041_area_grain_scope",
                "created_at": now,
                "updated_at": now,
            }
            for spec in AREA_FEATURE_DEFINITIONS
        ],
    )


def downgrade() -> None:
    if _has_rows("ranking_feature_snapshots", "scope_type = 'area'"):
        raise RuntimeError("Refusing to downgrade 0041: area-scope ranking_feature_snapshots rows exist")
    if _has_rows("ranking_feature_values", "scope_type = 'area'"):
        raise RuntimeError("Refusing to downgrade 0041: area-scope ranking_feature_values rows exist")
    if _has_rows(
        "ranking_feature_definitions",
        "grain = 'area' AND created_by <> '0041_area_grain_scope'",
    ):
        raise RuntimeError(
            "Refusing to downgrade 0041: area-grain ranking_feature_definitions rows exist "
            "beyond this migration's own seed"
        )

    op.execute(sa.text("DELETE FROM ranking_feature_definitions WHERE created_by = '0041_area_grain_scope'"))

    op.drop_constraint("ck_rfv_scope_shape", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_project_scope_shape", "ranking_feature_values", "area_id IS NULL AND unit_id IS NULL"
    )
    op.drop_constraint("ck_rfs_scope_shape", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint("ck_rfs_project_scope_no_area", "ranking_feature_snapshots", "area_id IS NULL")

    op.drop_constraint("ck_rfv_scope_type_allowed", "ranking_feature_values", type_="check")
    op.create_check_constraint(
        "ck_rfv_scope_type_allowed", "ranking_feature_values", "scope_type IN ('project', 'market')"
    )
    op.drop_constraint("ck_rfs_scope_type_allowed", "ranking_feature_snapshots", type_="check")
    op.create_check_constraint(
        "ck_rfs_scope_type_allowed", "ranking_feature_snapshots", "scope_type IN ('project', 'market')"
    )

    op.drop_index("uq_rfs_run_project_area_scope", table_name="ranking_feature_snapshots")
    op.drop_index("uq_rfs_run_project_scope_no_area", table_name="ranking_feature_snapshots")
    op.create_unique_constraint(
        "uq_ranking_feature_snapshot_run_project_scope",
        "ranking_feature_snapshots",
        ["ranking_run_id", "project_id", "scope_type"],
    )

    op.drop_constraint("fk_rfv_snapshot_project_scope", "ranking_feature_values", type_="foreignkey")
    op.drop_constraint(
        "uq_ranking_feature_snapshot_id_project_scope", "ranking_feature_snapshots", type_="unique"
    )
    op.create_unique_constraint(
        "uq_ranking_feature_snapshot_id_project_scope",
        "ranking_feature_snapshots",
        ["id", "project_id", "scope_type"],
    )
    op.create_foreign_key(
        "fk_rfv_snapshot_project_scope",
        "ranking_feature_values",
        "ranking_feature_snapshots",
        ["snapshot_id", "project_id", "scope_type"],
        ["id", "project_id", "scope_type"],
        ondelete="CASCADE",
    )
