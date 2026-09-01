"""Attach existing lifecycle-ready evidence to an Advisor proposal immutably.

Evidence documents are append-only and can be reused by more than one draft.
The association therefore belongs in its own append-only table; mutating
``ranking_evidence_documents.proposal_id`` would rewrite historical metadata
and would make a document unusable by a later proposal.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0052_proposal_evidence_links"
down_revision: str | None = "0051_add_project_criterion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "ranking_proposal_evidence_links",
        sa.Column("proposal_id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("linked_by_expert_id", UUID, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("proposal_id", "document_id", name="pk_ranking_proposal_evidence_links"),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["ranking_weight_proposals.id"],
            name="fk_rpel_proposal_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["ranking_evidence_documents.id"],
            name="fk_rpel_document_id", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_expert_id"], ["expert_profiles.id"],
            name="fk_rpel_linked_by_expert_id", ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_rpel_document_id", "ranking_proposal_evidence_links", ["document_id"])
    op.execute(
        sa.text(
            "CREATE TRIGGER ranking_proposal_evidence_links_append_only_guard "
            "BEFORE UPDATE OR DELETE ON ranking_proposal_evidence_links "
            "FOR EACH ROW EXECUTE FUNCTION ranking_governance_append_only_guard()"
        )
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM ranking_proposal_evidence_links")).scalar()
    if count:
        raise RuntimeError(
            f"Refusing to downgrade 0052: {count} immutable proposal-evidence link(s) exist; "
            "do not discard governance audit data."
        )
    op.drop_index("ix_rpel_document_id", table_name="ranking_proposal_evidence_links")
    op.drop_table("ranking_proposal_evidence_links")
