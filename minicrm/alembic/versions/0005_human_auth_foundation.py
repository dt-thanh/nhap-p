"""Additive Mini CRM human-identity foundation.

This migration is intentionally separate from the Backend auth schema. It stores
only password/token hashes and lifecycle metadata; no endpoint or credential is
enabled by the migration itself.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_human_auth_foundation"
down_revision: str | None = "0004_outbox_hierarchy_entities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

USER_STATUSES = ("invited", "active", "disabled")
CRM_ROLES = ("business_viewer", "pipeline_operator", "admin")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.create_table(
        "crm_users",
        sa.Column("id", UUID, nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        # Invited users have no password yet; active users are constrained below
        # to have a non-null Argon2id hash by the lifecycle service.
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'invited'")),
        sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'business_viewer'")),
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("disabled_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crm_users"),
        sa.UniqueConstraint("login", name="uq_crm_users_login"),
        sa.CheckConstraint("login <> ''", name="ck_crm_users_login_not_blank"),
        sa.CheckConstraint(_in_list("status", USER_STATUSES), name="ck_crm_users_status"),
        sa.CheckConstraint(_in_list("role", CRM_ROLES), name="ck_crm_users_role"),
        sa.CheckConstraint("auth_version > 0", name="ck_crm_users_auth_version_positive"),
        sa.CheckConstraint(
            "status <> 'active' OR password_hash IS NOT NULL",
            name="ck_crm_users_active_has_password",
        ),
        sa.CheckConstraint(
            "status <> 'disabled' OR disabled_at IS NOT NULL",
            name="ck_crm_users_disabled_has_timestamp",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_crm_users_updated_after_created"),
    )

    op.create_table(
        "crm_auth_sessions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("family_id", UUID, nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("replaced_by", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_crm_auth_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["crm_users.id"], name="fk_crm_auth_sessions_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["replaced_by"], ["crm_auth_sessions.id"], name="fk_crm_auth_sessions_replaced_by", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("refresh_token_hash", name="uq_crm_auth_sessions_refresh_token_hash"),
        sa.CheckConstraint("refresh_token_hash <> ''", name="ck_crm_auth_sessions_hash_not_blank"),
        sa.CheckConstraint("expires_at > created_at", name="ck_crm_auth_sessions_expires_after_created"),
        sa.CheckConstraint("last_seen_at >= created_at", name="ck_crm_auth_sessions_last_seen_after_created"),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="ck_crm_auth_sessions_revoked_after_created"),
        sa.CheckConstraint("replaced_by IS NULL OR replaced_by <> id", name="ck_crm_auth_sessions_not_self_replaced"),
    )
    op.create_index("ix_crm_auth_sessions_user_state", "crm_auth_sessions", ["user_id", "revoked_at", "expires_at"])
    op.create_index("ix_crm_auth_sessions_family_state", "crm_auth_sessions", ["family_id", "revoked_at"])
    op.create_index("ix_crm_auth_sessions_expires_at", "crm_auth_sessions", ["expires_at"])

    op.create_table(
        "crm_auth_invites",
        sa.Column("id", UUID, nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("invite_token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("accepted_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_crm_auth_invites"),
        sa.UniqueConstraint("invite_token_hash", name="uq_crm_auth_invites_token_hash"),
        sa.CheckConstraint("login <> ''", name="ck_crm_auth_invites_login_not_blank"),
        sa.CheckConstraint(_in_list("role", CRM_ROLES), name="ck_crm_auth_invites_role"),
        sa.CheckConstraint("invite_token_hash <> ''", name="ck_crm_auth_invites_hash_not_blank"),
        sa.CheckConstraint("expires_at > created_at", name="ck_crm_auth_invites_expires_after_created"),
        sa.CheckConstraint("accepted_at IS NULL OR accepted_at >= created_at", name="ck_crm_auth_invites_accepted_after_created"),
    )
    op.create_index("ix_crm_auth_invites_login", "crm_auth_invites", ["login"])
    op.create_index("ix_crm_auth_invites_expiry", "crm_auth_invites", ["expires_at", "accepted_at"])

    op.create_table(
        "crm_password_reset_tokens",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_crm_password_reset_tokens"),
        sa.ForeignKeyConstraint(["user_id"], ["crm_users.id"], name="fk_crm_password_reset_tokens_user_id", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_crm_password_reset_tokens_token_hash"),
        sa.CheckConstraint("token_hash <> ''", name="ck_crm_password_reset_tokens_hash_not_blank"),
        sa.CheckConstraint("expires_at > created_at", name="ck_crm_password_reset_tokens_expires_after_created"),
        sa.CheckConstraint("used_at IS NULL OR used_at >= created_at", name="ck_crm_password_reset_tokens_used_after_created"),
    )
    op.create_index("ix_crm_password_reset_tokens_user_expiry", "crm_password_reset_tokens", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_crm_password_reset_tokens_user_expiry", table_name="crm_password_reset_tokens")
    op.drop_table("crm_password_reset_tokens")
    op.drop_index("ix_crm_auth_invites_expiry", table_name="crm_auth_invites")
    op.drop_index("ix_crm_auth_invites_login", table_name="crm_auth_invites")
    op.drop_table("crm_auth_invites")
    op.drop_index("ix_crm_auth_sessions_expires_at", table_name="crm_auth_sessions")
    op.drop_index("ix_crm_auth_sessions_family_state", table_name="crm_auth_sessions")
    op.drop_index("ix_crm_auth_sessions_user_state", table_name="crm_auth_sessions")
    op.drop_table("crm_auth_sessions")
    op.drop_table("crm_users")
