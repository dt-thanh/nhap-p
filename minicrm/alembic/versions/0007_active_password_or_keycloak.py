"""allow active users linked to keycloak_sub without a local password

Revision ID: 0007_active_password_or_keycloak
Revises: 0006_jit_keycloak_sub
Create Date: 2026-08-23

``ck_crm_users_active_has_password`` (migration 0005) yêu cầu mọi user
``active`` phải có ``password_hash``. Ràng buộc này viết trước khi JIT
provisioning (0006) tồn tại: user OIDC hợp lệ không có password local
(Keycloak giữ), nên INSERT ``status='active'`` + ``password_hash=NULL`` cho
user Keycloak bị constraint cũ chặn. Nới constraint: active hợp lệ nếu có
password HOẶC có keycloak_sub.
"""
from __future__ import annotations

from alembic import op

revision = "0007_active_password_or_keycloak"
down_revision = "0006_jit_keycloak_sub"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_crm_users_active_has_password", "crm_users", type_="check")
    op.create_check_constraint(
        "ck_crm_users_active_has_password",
        "crm_users",
        "status <> 'active' OR password_hash IS NOT NULL OR keycloak_sub IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_crm_users_active_has_password", "crm_users", type_="check")
    op.create_check_constraint(
        "ck_crm_users_active_has_password",
        "crm_users",
        "status <> 'active' OR password_hash IS NOT NULL",
    )
