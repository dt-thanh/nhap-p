"""Persist the acknowledgement attached to new CEO review decisions.

This is additive and nullable by design: immutable historical review rows
remain NULL rather than being inferred or rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0048_review_evidence_ack"
down_revision: str | None = "0047_evidence_project_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ranking_proposal_reviews",
        sa.Column("evidence_review_acknowledged", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ranking_proposal_reviews", "evidence_review_acknowledged")
