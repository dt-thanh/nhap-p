"""Make extraction failures durable and pending attempts unique per document.

The extraction-attempt table is append-only: terminal state is represented by
an additional attempt row, never by mutating an historical row.  ``error_code``
is nullable so historical rows are not rewritten.  Concurrency is serialized
with a transaction-scoped advisory lock because an append-only status log
cannot use a partial unique index: its historical ``pending`` rows remain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054_evidence_failure_state"
down_revision: str | None = "0053_ranking_run_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ranking_evidence_extraction_attempts",
        sa.Column("error_code", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ranking_evidence_extraction_attempts", "error_code")
