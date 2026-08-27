"""add optional location metadata to crm_projects

The field is nullable so existing Mini CRM rows remain valid. Location is a
Mini CRM-local field for this MVP; the v2 sync contract and backend projection
still intentionally carry only the existing project fields.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_project_location"
down_revision = "0008_unit_listing_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crm_projects", sa.Column("location", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("crm_projects", "location")
