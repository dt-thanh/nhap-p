"""Remove an invalid uniqueness index from the append-only attempt log."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055_drop_pending_attempt_index"
down_revision: str | None = "0054_evidence_failure_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_reea_one_pending_document"))


def downgrade() -> None:
    # No safe equivalent exists for an append-only log: historical pending
    # rows would make a partial unique index reject every later retry.
    pass
