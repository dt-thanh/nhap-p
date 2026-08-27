"""PR-3: link materialized Project-grain feature values back to the
CEO-approved value assertion that produced them.

Revision ID: 0039_project_value_materialize
Revises: 0038_governance_value_mode
Create Date: 2026-08-27

One additive, nullable column — `ranking_feature_values.source_justification_id`
— plus a lookup index. No other schema change is needed for PR-3's
Project-grain-only scope:

* `ranking_feature_values`/`ranking_feature_snapshots` are ALREADY scoped to
  `scope_type = 'project'` exclusively (`ck_rfv_scope_type_project`,
  `ck_rfs_scope_type_project`, `0033`) — Project is not a new grain for these
  two tables, it is the *only* grain they have ever permitted. PR-3 writes
  nothing that a pre-PR-3 row shape could not already hold, except this one
  provenance link.
* Idempotency ("materializing the same approved assertion revision twice
  never duplicates a value") is already provided by the existing
  `uq_ranking_feature_value_scope (snapshot_id, feature_definition_id,
  scope_type, project_id)` unique constraint (`0033`) — a get-or-create read
  before every insert, backed by that constraint as the race-safety net, is
  sufficient; no new uniqueness constraint is added here.
* Immutability is already enforced by `0033`'s
  `ranking_evidence_append_only_guard` trigger on `ranking_feature_values`
  (blocks UPDATE/DELETE unconditionally) — PR-3's materializer only ever
  INSERTs, consistent with that trigger, and needs no new one.

`source_justification_id` is deliberately NOT unique: the same CEO-approved
justification is expected to be copied into a NEW `ranking_feature_values`
row for every subsequent ranking run's snapshot that selects it (§5.2 of
`docs/ranking/hierarchical_scoring_implementation_plan.md`: "a COPY, not a
live view") — only `(snapshot_id, feature_definition_id, scope_type,
project_id)` needs to be unique, and already is.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0039_project_value_materialize"
down_revision: str | None = "0038_governance_value_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _has_rows(table: str, where: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {where})")).scalar())


def upgrade() -> None:
    op.add_column(
        "ranking_feature_values",
        sa.Column("source_justification_id", UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_rfv_source_justification_id",
        "ranking_feature_values",
        "ranking_feature_justifications",
        ["source_justification_id"],
        ["id"],
    )
    op.create_index(
        "ix_rfv_source_justification_id",
        "ranking_feature_values",
        ["source_justification_id"],
    )


def downgrade() -> None:
    if _has_rows("ranking_feature_values", "source_justification_id IS NOT NULL"):
        raise RuntimeError(
            "Refusing to downgrade 0039: materialized ranking_feature_values rows carry "
            "a source_justification_id link — dropping the column would destroy the "
            "provenance chain back to the CEO-approved assertion that produced them"
        )
    op.drop_index("ix_rfv_source_justification_id", table_name="ranking_feature_values")
    op.drop_constraint("fk_rfv_source_justification_id", "ranking_feature_values", type_="foreignkey")
    op.drop_column("ranking_feature_values", "source_justification_id")
