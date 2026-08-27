"""PR-1: additive columns for the hierarchical ranking output (parallel to legacy).

Revision ID: 0037_hierarchical_scoring_pr1
Revises: 0036_remove_historical_ranking
Create Date: 2026-08-27

Three additive, nullable columns — no existing column, index, or constraint is
touched. This is D29 (`ranking_scores.hierarchical_score`), D37/S9
(`ranking_scores.hierarchical_contributions`), and D41/S10
(`ranking_configs.hierarchical_weights`), per
`docs/ranking/ranking_consultant.md` §24.6/§24.7 and
`docs/ranking/hierarchical_scoring_implementation_plan.md` §2.

`ranking_scores.score`/`rank_in_area`/`rank_in_project`/`contributions` and
`ranking_configs.weights` are byte-identical before and after this migration:
every existing row simply gets `NULL` in the three new columns, which is a
valid, unremarkable state (D41) — the hierarchical post-run step reads `NULL`
`hierarchical_weights` as "not configured" and no-ops, exactly as if this
migration had not run.

`hierarchical_score` gets the same `[0,1]` CHECK `ranking_scores.score`
already has (`ck_ranking_scores_score_range`, 0015), widened to also allow
`NULL` — a HIGH_RISK-gated or not-yet-hierarchically-scored unit has no
hierarchical score, not a zero one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037_hierarchical_scoring_pr1"
down_revision: str | None = "0036_remove_historical_ranking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB


def upgrade() -> None:
    op.add_column("ranking_scores", sa.Column("hierarchical_score", sa.Numeric(6, 4), nullable=True))
    op.add_column(
        "ranking_scores",
        sa.Column("hierarchical_contributions", JSONB, nullable=True),
    )
    op.create_check_constraint(
        "ck_ranking_scores_hierarchical_score_range",
        "ranking_scores",
        "hierarchical_score IS NULL OR (hierarchical_score >= 0 AND hierarchical_score <= 1)",
    )
    op.add_column("ranking_configs", sa.Column("hierarchical_weights", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("ranking_configs", "hierarchical_weights")
    op.drop_constraint("ck_ranking_scores_hierarchical_score_range", "ranking_scores", type_="check")
    op.drop_column("ranking_scores", "hierarchical_contributions")
    op.drop_column("ranking_scores", "hierarchical_score")
