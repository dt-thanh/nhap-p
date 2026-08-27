"""add keycloak_sub for JIT provisioning

Revision ID: 0006_jit_keycloak_sub
Revises: 0005_human_auth_foundation
Create Date: 2026-08-23

Thêm cột ``keycloak_sub`` để liên kết user Mini CRM với danh tính Keycloak
(claim ``sub``, ổn định theo user trong realm). Cột nullable + UNIQUE có filter
(WHERE NOT NULL) để không phá các dòng cũ (user legacy chưa có Keycloak).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_jit_keycloak_sub"
down_revision = "0005_human_auth_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_users",
        sa.Column("keycloak_sub", sa.Text(), nullable=True),
    )
    # Partial unique index: mỗi Keycloak sub chỉ map tới 1 user CRM, nhưng nhiều
    # user legacy (sub=NULL) vẫn cùng tồn tại được.
    op.create_index(
        "uq_crm_users_keycloak_sub",
        "crm_users",
        ["keycloak_sub"],
        unique=True,
        postgresql_where=sa.text("keycloak_sub IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_crm_users_keycloak_sub", table_name="crm_users")
    op.drop_column("crm_users", "keycloak_sub")
