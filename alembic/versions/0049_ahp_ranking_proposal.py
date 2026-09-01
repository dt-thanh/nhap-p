"""Classify proposals (qualitative_analysis | ahp_ranking_proposal) and carry
the frozen AHP package + post-approval application status for the new kind.

Every existing row (regardless of its `assertion_kind`) is `qualitative_analysis`
by explicit backfill, not a guess: no historical row ever went through the new
governed Advisor-authored-AHP-package -> CEO-approval-triggers-publish-and-run
flow this revision introduces, so none of them may be silently reclassified as
`ahp_ranking_proposal`. The three AHP-only columns stay NULL for every
`qualitative_analysis` row, enforced by a CHECK, not by convention.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0049_ahp_ranking_proposal"
down_revision: str | None = "0048_review_evidence_ack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.add_column(
        "ranking_weight_proposals",
        sa.Column("proposal_type", sa.Text(), nullable=False, server_default="qualitative_analysis"),
    )
    op.alter_column("ranking_weight_proposals", "proposal_type", server_default=None)
    op.add_column("ranking_weight_proposals", sa.Column("proposed_hierarchy_snapshot", JSONB, nullable=True))
    op.add_column("ranking_weight_proposals", sa.Column("ahp_application_status", sa.Text(), nullable=True))
    op.add_column("ranking_weight_proposals", sa.Column("applied_ranking_run_id", UUID, nullable=True))
    op.add_column("ranking_runs", sa.Column("ahp_proposal_id", UUID, nullable=True))

    op.create_foreign_key(
        "fk_rwp_applied_ranking_run_id",
        "ranking_weight_proposals",
        "ranking_runs",
        ["applied_ranking_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ranking_runs_ahp_proposal_id",
        "ranking_runs",
        "ranking_weight_proposals",
        ["ahp_proposal_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_ranking_runs_ahp_proposal_id",
        "ranking_runs",
        ["ahp_proposal_id"],
        unique=True,
        postgresql_where=sa.text("ahp_proposal_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_rwp_proposal_type_valid",
        "ranking_weight_proposals",
        "proposal_type IN ('qualitative_analysis', 'ahp_ranking_proposal')",
    )
    op.create_check_constraint(
        "ck_rwp_ahp_application_status_valid",
        "ranking_weight_proposals",
        "ahp_application_status IS NULL OR ahp_application_status IN ('pending', 'applied', 'failed')",
    )
    op.create_check_constraint(
        "ck_rwp_ahp_fields_only_for_ahp_type",
        "ranking_weight_proposals",
        "proposal_type = 'ahp_ranking_proposal' OR ("
        "proposed_hierarchy_snapshot IS NULL AND ahp_application_status IS NULL "
        "AND applied_ranking_run_id IS NULL)",
    )


def downgrade() -> None:
    conn = op.get_bind()
    non_default = conn.execute(
        sa.text("SELECT count(*) FROM ranking_weight_proposals WHERE proposal_type <> 'qualitative_analysis'")
    ).scalar()
    if non_default:
        raise RuntimeError(
            f"Refusing to downgrade 0049: {non_default} ranking_weight_proposals row(s) are "
            "'ahp_ranking_proposal' — downgrading would silently drop their frozen hierarchy "
            "snapshot and application status. Resolve or archive those proposals first."
        )
    op.drop_constraint("ck_rwp_ahp_fields_only_for_ahp_type", "ranking_weight_proposals", type_="check")
    op.drop_constraint("ck_rwp_ahp_application_status_valid", "ranking_weight_proposals", type_="check")
    op.drop_constraint("ck_rwp_proposal_type_valid", "ranking_weight_proposals", type_="check")
    op.drop_constraint("fk_rwp_applied_ranking_run_id", "ranking_weight_proposals", type_="foreignkey")
    op.drop_index("uq_ranking_runs_ahp_proposal_id", table_name="ranking_runs")
    op.drop_constraint("fk_ranking_runs_ahp_proposal_id", "ranking_runs", type_="foreignkey")
    op.drop_column("ranking_runs", "ahp_proposal_id")
    op.drop_column("ranking_weight_proposals", "applied_ranking_run_id")
    op.drop_column("ranking_weight_proposals", "ahp_application_status")
    op.drop_column("ranking_weight_proposals", "proposed_hierarchy_snapshot")
    op.drop_column("ranking_weight_proposals", "proposal_type")
