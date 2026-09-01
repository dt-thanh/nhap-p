"""Archive/delete lifecycle for evidence documents (mandatory-scope item 4).

`ranking_evidence_documents` is one of the four tables `0034` put under
`ranking_governance_append_only_guard` (UPDATE/DELETE unconditionally raise),
same reason `0035` added `ranking_evidence_extraction_attempts` as a separate
append-only status log instead of mutating a status column: an
`archived_at`/`deleted_at` column on `ranking_evidence_documents` itself is
NOT AN OPTION — no `UPDATE` can ever reach it. This migration follows the
exact same, already-proven pattern: a new append-only event log,
`ranking_evidence_document_lifecycle_events`, where "current lifecycle state"
is the latest row for a document (`active` if no row exists yet). Every
retrieval path added/adjusted alongside this migration (in
`src/services/governance.py`/`evidence_extraction.py`) must join against this
table's latest row, never trust a document's mere existence.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0044_evidence_document_lifecycle"
down_revision: str | None = "0043_unit_enrichment_attributes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)


def _has_rows(table: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar())


def upgrade() -> None:
    op.create_table(
        "ranking_evidence_document_lifecycle_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_expert_id", UUID, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_evidence_document_lifecycle_events"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ranking_evidence_documents.id"],
            name="fk_redle_document_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_expert_id"],
            ["expert_profiles.id"],
            name="fk_redle_actor_expert_id",
        ),
        sa.CheckConstraint(
            "event_type IN ('archived', 'deleted', 'restored')",
            name="ck_redle_event_type",
        ),
    )
    op.create_index(
        "ix_redle_document_created",
        "ranking_evidence_document_lifecycle_events",
        ["document_id", sa.text("created_at DESC")],
    )
    # Reuses the append-only guard function 0034 already created — this
    # table's whole point is an immutable event history, same as
    # `ranking_evidence_extraction_attempts` (0035).
    op.execute(
        sa.text(
            "CREATE TRIGGER ranking_evidence_document_lifecycle_events_append_only_guard "
            "BEFORE UPDATE OR DELETE ON ranking_evidence_document_lifecycle_events "
            "FOR EACH ROW EXECUTE FUNCTION ranking_governance_append_only_guard()"
        )
    )


def downgrade() -> None:
    if _has_rows("ranking_evidence_document_lifecycle_events"):
        raise RuntimeError("Refusing to downgrade 0044: ranking_evidence_document_lifecycle_events has rows")
    op.execute(
        sa.text(
            "DROP TRIGGER ranking_evidence_document_lifecycle_events_append_only_guard "
            "ON ranking_evidence_document_lifecycle_events"
        )
    )
    op.drop_table("ranking_evidence_document_lifecycle_events")
