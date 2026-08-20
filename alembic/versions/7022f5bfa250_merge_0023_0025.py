"""merge_0023_0025

Revision ID: 7022f5bfa250
Revises: 0023_config_publish_stamp, 0025_synthetic_unit_labels
Create Date: 2026-08-16 13:59:50.821393
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7022f5bfa250'
down_revision: Union[str, None] = ('0023_config_publish_stamp', '0025_synthetic_unit_labels')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
