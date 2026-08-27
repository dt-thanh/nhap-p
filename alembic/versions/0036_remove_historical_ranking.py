"""Remove the retired historical-ranking materialized inventory table.

Revision ID: 0036_remove_historical_ranking
Revises: 0035_evidence_document_chunks
Create Date: 2026-08-26

The historical project-ranking implementation was removed from the application.
``unit_inventory_daily`` was its only exclusively consumed materialized table;
the append-only status-history tables remain because CRM sync, replay/backfill,
and operational audit paths still use them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0036_remove_historical_ranking"
down_revision: str | None = "0035_evidence_document_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
TABLE = "unit_inventory_daily"
INDEX_AREA_DATE = "uq_unit_inventory_daily_area_stat_date"
INDEX_STAT_DATE = "ix_unit_inventory_daily_stat_date"


def upgrade() -> None:
    """Drop the retired materialized table and its owned indexes/constraints."""
    op.drop_index(INDEX_STAT_DATE, table_name=TABLE)
    op.drop_index(INDEX_AREA_DATE, table_name=TABLE)
    op.drop_table(TABLE)


def downgrade() -> None:
    """Restore the table shape; rows removed by upgrade cannot be recovered."""
    op.create_table(
        TABLE,
        sa.Column("id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("sellable_units", sa.Integer(), nullable=False),
        sa.Column("blocked_units", sa.Integer(), nullable=False),
        sa.Column("live_reserved_units", sa.Integer(), nullable=False),
        sa.Column("live_sold_units", sa.Integer(), nullable=False),
        sa.Column("rebuilt_from_log_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_unit_inventory_daily"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_unit_inventory_daily_area_id"),
        sa.CheckConstraint("sellable_units >= 0", name="ck_uid_sellable_nonnegative"),
        sa.CheckConstraint("blocked_units >= 0", name="ck_uid_blocked_nonnegative"),
        sa.CheckConstraint("live_reserved_units >= 0", name="ck_uid_reserved_nonnegative"),
        sa.CheckConstraint("live_sold_units >= 0", name="ck_uid_sold_nonnegative"),
    )
    op.create_index(INDEX_AREA_DATE, TABLE, ["area_id", "stat_date"], unique=True)
    op.create_index(INDEX_STAT_DATE, TABLE, ["stat_date"])
