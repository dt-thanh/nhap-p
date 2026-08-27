"""Additive pgvector chunk store for ranking_evidence_documents (§21.4).

Also adds `ranking_evidence_extraction_attempts`, an append-only status log —
NOT a rename of `ranking_evidence_documents.extraction_status` (0034). That
column cannot be the real status: `ranking_evidence_documents` is one of the
four tables 0034 put under `ranking_governance_append_only_guard`
(UPDATE/DELETE unconditionally raise), so `_set_extraction_status(...)` as
described in §21.3/§21.5 would fail against the real schema — it assumes a
mutation the existing, already-tested 0034 trigger forbids. The column is left
in place (still `'not_requested'` at insert, per 0034's default) as a
historical "status at registration time" field; current status is the latest
row in the new log, same pattern this repo already uses for
`unit_status_history`/`deal_status_history` (0028-0030) and
`ranking_config_audit_events` (0034) instead of mutating a status column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0035_evidence_document_chunks"
down_revision: str | None = "0034_expert_ranking_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

# text-embedding-3-small (D16, docs/ranking/ranking_consultant.md §21.4). A
# future model change requires a new migration to ALTER the column — the
# dimension is not parameterized because pgvector requires a fixed width.
EMBEDDING_DIMENSIONS = 1536


def _has_rows(table: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar())


def upgrade() -> None:
    # Not present today (0033/0034 predate this). Requires the pgvector
    # extension files to be available in the running Postgres image —
    # docker-compose.yml's `db` service was switched to `pgvector/pgvector:pg15`
    # for this reason.
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    op.create_table(
        "ranking_evidence_document_chunks",
        sa.Column("id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        # Pinned per row, not global, so a future embedding-model change
        # doesn't silently mix incompatible vectors (R17, §21.11).
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_evidence_document_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ranking_evidence_documents.id"],
            name="fk_redc_document_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_redc_document_chunk"),
        sa.CheckConstraint("content <> ''", name="ck_redc_content_not_blank"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_redc_chunk_index_nonnegative"),
        sa.CheckConstraint("token_count > 0", name="ck_redc_token_count_positive"),
    )
    op.create_index("ix_redc_document_id", "ranking_evidence_document_chunks", ["document_id"])

    # HNSW over cosine distance: the doc store is "dozens of documents, not
    # millions" (§13) — an index is cheap insurance, not a scaling necessity yet.
    op.execute(
        sa.text(
            "CREATE INDEX ix_redc_embedding_hnsw ON ranking_evidence_document_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    )

    # Reuses the append-only guard function 0034 already created — no new
    # trigger function. Same discipline as every other table in this
    # subsystem: a re-extraction produces a NEW document row (distinct
    # sha256_checksum) via ranking_evidence_documents, never an UPDATE of
    # chunk content in place.
    op.execute(
        sa.text(
            "CREATE TRIGGER ranking_evidence_document_chunks_append_only_guard "
            "BEFORE UPDATE OR DELETE ON ranking_evidence_document_chunks "
            "FOR EACH ROW EXECUTE FUNCTION ranking_governance_append_only_guard()"
        )
    )

    op.create_table(
        "ranking_evidence_extraction_attempts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_evidence_extraction_attempts"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ranking_evidence_documents.id"],
            name="fk_reea_document_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'not_supported')",
            name="ck_reea_status",
        ),
    )
    op.create_index(
        "ix_reea_document_created",
        "ranking_evidence_extraction_attempts",
        ["document_id", sa.text("created_at DESC")],
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER ranking_evidence_extraction_attempts_append_only_guard "
            "BEFORE UPDATE OR DELETE ON ranking_evidence_extraction_attempts "
            "FOR EACH ROW EXECUTE FUNCTION ranking_governance_append_only_guard()"
        )
    )


def downgrade() -> None:
    # Same populated-rows guard as 0033/0034's downgrade().
    for table in ("ranking_evidence_document_chunks", "ranking_evidence_extraction_attempts"):
        if _has_rows(table):
            raise RuntimeError(f"Refusing to downgrade 0035: {table} has rows")
    op.execute(
        sa.text(
            "DROP TRIGGER ranking_evidence_extraction_attempts_append_only_guard "
            "ON ranking_evidence_extraction_attempts"
        )
    )
    op.drop_table("ranking_evidence_extraction_attempts")
    op.execute(
        sa.text(
            "DROP TRIGGER ranking_evidence_document_chunks_append_only_guard "
            "ON ranking_evidence_document_chunks"
        )
    )
    op.drop_table("ranking_evidence_document_chunks")
    # CREATE EXTENSION is left in place — other tables may come to depend on
    # it, and DROP EXTENSION here could silently break something this
    # migration doesn't own.
