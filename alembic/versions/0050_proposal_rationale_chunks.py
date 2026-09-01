"""Add searchable, immutable rationale chunks for submitted AHP proposals.

Rationales remain optional JSON fields in the frozen proposal snapshot.  This
table is a derived retrieval projection created once at submission time, never
while an Advisor is editing a draft; historical proposals intentionally receive
no backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0050_proposal_rationale_chunks"
down_revision: str | None = "0049_ahp_ranking_proposal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TS = sa.TIMESTAMP(timezone=True)
EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.create_table(
        "ranking_proposal_rationale_chunks",
        sa.Column("id", UUID, nullable=False),
        sa.Column("proposal_id", UUID, nullable=False),
        sa.Column("criterion_key", sa.Text(), nullable=False),
        sa.Column("grain", sa.Text(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_proposal_rationale_chunks"),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ranking_weight_proposals.id"],
            name="fk_rprc_proposal_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("proposal_id", "grain", "criterion_key", name="uq_rprc_proposal_grain_criterion"),
        sa.CheckConstraint("criterion_key <> ''", name="ck_rprc_criterion_not_blank"),
        sa.CheckConstraint("grain IN ('market', 'project', 'area')", name="ck_rprc_grain"),
        sa.CheckConstraint("chunk_text <> ''", name="ck_rprc_chunk_text_not_blank"),
    )
    op.create_index("idx_rationale_chunks_proposal", "ranking_proposal_rationale_chunks", ["proposal_id"])
    op.execute(
        sa.text(
            "CREATE INDEX idx_rationale_chunks_embedding ON ranking_proposal_rationale_chunks "
            "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM ranking_proposal_rationale_chunks")).scalar()
    if count:
        raise RuntimeError(
            f"Refusing to downgrade 0050: {count} rationale chunk(s) exist; downgrading would discard audit retrieval data."
        )
    op.drop_index("idx_rationale_chunks_embedding", table_name="ranking_proposal_rationale_chunks")
    op.drop_index("idx_rationale_chunks_proposal", table_name="ranking_proposal_rationale_chunks")
    op.drop_table("ranking_proposal_rationale_chunks")
