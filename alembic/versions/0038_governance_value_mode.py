"""PR-2: governance-side value-mode assertions (D37/D38/D41 — hierarchical
factor-value drafting, evidence, and CEO approval).

Revision ID: 0038_governance_value_mode
Revises: 0037_hierarchical_scoring_pr1
Create Date: 2026-08-27

Three additive, backward-compatible changes, all scoped to the existing
governance/evidence-catalog tables (`0033`/`0034`) — no parallel workflow
table, no change to `ranking_configs`/`ranking_scores`/`ranking_runs`, no
change to the four Phase 2 ranking tables or PR-1's three hierarchical
columns.

1. `ranking_feature_definitions.grain` widened to permit `'market'` — needed
   before any market-grain feature definition can be registered at all
   (`docs/ranking/hierarchical_scoring_implementation_plan.md` §2.4/§4.1).

2. `ranking_weight_proposals` — `scope_type`/`area_id` widened from
   project-only to `project | area | market`, and a new `assertion_kind`
   column (`weight` default, `value` new) with `base_config_id` relaxed to
   nullable (a value assertion has no associated `ranking_configs` draft at
   all — it never touches that table).

3. `ranking_feature_justifications` — `proposed_weight` relaxed to nullable
   (weight XOR value, enforced by `ck_rfj_assertion_mode_xor`, not by column
   nullability alone) plus the value-assertion payload columns (`raw_numeric`,
   `normalized_numeric`, `categorical_value`, `effective_at`, `expires_at`,
   `external_source_citation`, `author_subject`).

4. `ranking_proposal_reviews` — two new nullable columns, `reviewer_subject`
   and `reviewer_is_ceo`, populated only for value-mode reviews (server-
   derived from the verified OIDC principal, never the request body) so a
   later re-verification (`validate_value_assertion_for_materialization()`,
   PR-3's readiness guard) never needs a live Keycloak call to confirm who
   approved what — the append-only table already IS the audit trail.

**Preserves weight-proposal behavior exactly.** Every existing row gets
`assertion_kind = 'weight'` (the `DEFAULT`) and every new column `NULL` — the
XOR CHECK's first branch matches every existing row with no data migration.
`upsert_justification()`/`create_proposal()`/`submit_review()`/`mark_published()`
[`src/services/governance.py`] are *extended*, not modified: when
`assertion_kind == "weight"` (the default), each runs exactly the code path
it runs today, byte-for-byte — see `tests/test_services/test_governance.py`'s
existing 13 tests, unmodified, plus new value-mode tests.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038_governance_value_mode"
down_revision: str | None = "0037_hierarchical_scoring_pr1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)


def _has_rows(table: str, where: str | None = None) -> bool:
    bind = op.get_bind()
    clause = f"WHERE {where}" if where else ""
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} {clause})")).scalar())


def upgrade() -> None:
    # --- 1. Market grain -----------------------------------------------------
    op.drop_constraint("ck_rfd_grain", "ranking_feature_definitions", type_="check")
    op.create_check_constraint(
        "ck_rfd_grain",
        "ranking_feature_definitions",
        "grain IN ('project', 'area', 'project_area', 'unit', 'market')",
    )

    # --- 2. ranking_weight_proposals: scope widening + assertion_kind --------
    op.drop_constraint("ck_rwp_scope_type_project", "ranking_weight_proposals", type_="check")
    op.create_check_constraint(
        "ck_rwp_scope_type_allowed",
        "ranking_weight_proposals",
        "scope_type IN ('project', 'area', 'market')",
    )
    op.drop_constraint("ck_rwp_project_scope_no_area", "ranking_weight_proposals", type_="check")
    op.create_check_constraint(
        "ck_rwp_scope_shape",
        "ranking_weight_proposals",
        "(scope_type IN ('project', 'market') AND area_id IS NULL) "
        "OR (scope_type = 'area' AND area_id IS NOT NULL)",
    )

    op.add_column(
        "ranking_weight_proposals",
        sa.Column("assertion_kind", sa.Text(), nullable=False, server_default=sa.text("'weight'")),
    )
    op.create_check_constraint(
        "ck_rwp_assertion_kind", "ranking_weight_proposals", "assertion_kind IN ('weight', 'value')"
    )
    op.alter_column("ranking_weight_proposals", "base_config_id", nullable=True)
    op.create_check_constraint(
        "ck_rwp_assertion_kind_config_shape",
        "ranking_weight_proposals",
        "(assertion_kind = 'weight' AND base_config_id IS NOT NULL) "
        "OR (assertion_kind = 'value' AND base_config_id IS NULL AND proposed_config_id IS NULL)",
    )

    # --- 3. ranking_feature_justifications: weight XOR value ------------------
    op.alter_column("ranking_feature_justifications", "proposed_weight", nullable=True)
    op.add_column(
        "ranking_feature_justifications",
        sa.Column("assertion_kind", sa.Text(), nullable=False, server_default=sa.text("'weight'")),
    )
    op.create_check_constraint(
        "ck_rfj_assertion_kind", "ranking_feature_justifications", "assertion_kind IN ('weight', 'value')"
    )
    op.add_column("ranking_feature_justifications", sa.Column("raw_numeric", sa.Numeric(24, 10), nullable=True))
    op.add_column(
        "ranking_feature_justifications", sa.Column("normalized_numeric", sa.Numeric(12, 8), nullable=True)
    )
    op.create_check_constraint(
        "ck_rfj_normalized_range",
        "ranking_feature_justifications",
        "normalized_numeric IS NULL OR (normalized_numeric >= 0 AND normalized_numeric <= 1)",
    )
    op.add_column("ranking_feature_justifications", sa.Column("categorical_value", sa.Text(), nullable=True))
    op.add_column("ranking_feature_justifications", sa.Column("effective_at", TS, nullable=True))
    op.add_column("ranking_feature_justifications", sa.Column("expires_at", TS, nullable=True))
    op.add_column(
        "ranking_feature_justifications", sa.Column("external_source_citation", sa.Text(), nullable=True)
    )
    # Server-derived author identity (`principal.subject`), stored directly so
    # PR-3's materialization guard never needs a join back through
    # `expert_profiles` to answer "who verifiably authored this" — weight-mode
    # rows leave this NULL, `created_by_expert_id` remains their only author
    # reference, unchanged.
    op.add_column("ranking_feature_justifications", sa.Column("author_subject", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_rfj_assertion_mode_xor",
        "ranking_feature_justifications",
        "(assertion_kind = 'weight' AND proposed_weight IS NOT NULL "
        " AND raw_numeric IS NULL AND normalized_numeric IS NULL AND categorical_value IS NULL "
        " AND effective_at IS NULL AND expires_at IS NULL AND external_source_citation IS NULL)"
        " OR "
        "(assertion_kind = 'value' AND proposed_weight IS NULL AND previous_weight IS NULL)",
    )

    # --- 4. ranking_proposal_reviews: server-derived CEO identity ------------
    # Additive nullable columns on an append-only table (0034's guard trigger
    # already blocks UPDATE/DELETE) -- weight-mode reviews leave both NULL,
    # `reviewer_expert_id` (existing) remains their only reviewer reference.
    op.add_column("ranking_proposal_reviews", sa.Column("reviewer_subject", sa.Text(), nullable=True))
    op.add_column("ranking_proposal_reviews", sa.Column("reviewer_is_ceo", sa.Boolean(), nullable=True))


def downgrade() -> None:
    if _has_rows("ranking_weight_proposals", "assertion_kind = 'value'"):
        raise RuntimeError(
            "Refusing to downgrade 0038: value-mode ranking_weight_proposals rows exist "
            "(would silently lose scope_type='area'/'market' and assertion_kind data)"
        )
    if _has_rows("ranking_feature_justifications", "assertion_kind = 'value'"):
        raise RuntimeError(
            "Refusing to downgrade 0038: value-mode ranking_feature_justifications rows exist"
        )
    if _has_rows("ranking_feature_definitions", "grain = 'market'"):
        raise RuntimeError("Refusing to downgrade 0038: market-grain ranking_feature_definitions rows exist")

    op.drop_column("ranking_proposal_reviews", "reviewer_is_ceo")
    op.drop_column("ranking_proposal_reviews", "reviewer_subject")

    op.drop_constraint("ck_rfj_assertion_mode_xor", "ranking_feature_justifications", type_="check")
    op.drop_column("ranking_feature_justifications", "author_subject")
    op.drop_column("ranking_feature_justifications", "external_source_citation")
    op.drop_column("ranking_feature_justifications", "expires_at")
    op.drop_column("ranking_feature_justifications", "effective_at")
    op.drop_column("ranking_feature_justifications", "categorical_value")
    op.drop_constraint("ck_rfj_normalized_range", "ranking_feature_justifications", type_="check")
    op.drop_column("ranking_feature_justifications", "normalized_numeric")
    op.drop_column("ranking_feature_justifications", "raw_numeric")
    op.drop_constraint("ck_rfj_assertion_kind", "ranking_feature_justifications", type_="check")
    op.drop_column("ranking_feature_justifications", "assertion_kind")
    op.alter_column("ranking_feature_justifications", "proposed_weight", nullable=False)

    op.drop_constraint("ck_rwp_assertion_kind_config_shape", "ranking_weight_proposals", type_="check")
    op.alter_column("ranking_weight_proposals", "base_config_id", nullable=False)
    op.drop_constraint("ck_rwp_assertion_kind", "ranking_weight_proposals", type_="check")
    op.drop_column("ranking_weight_proposals", "assertion_kind")

    op.drop_constraint("ck_rwp_scope_shape", "ranking_weight_proposals", type_="check")
    op.create_check_constraint("ck_rwp_project_scope_no_area", "ranking_weight_proposals", "area_id IS NULL")
    op.drop_constraint("ck_rwp_scope_type_allowed", "ranking_weight_proposals", type_="check")
    op.create_check_constraint(
        "ck_rwp_scope_type_project", "ranking_weight_proposals", "scope_type = 'project'"
    )

    op.drop_constraint("ck_rfd_grain", "ranking_feature_definitions", type_="check")
    op.create_check_constraint(
        "ck_rfd_grain",
        "ranking_feature_definitions",
        "grain IN ('project', 'area', 'project_area', 'unit')",
    )
