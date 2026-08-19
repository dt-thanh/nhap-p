"""Structured AI advisory actions and audited execution.

Revision ID: 0020_agent_advisory_execution
Revises: 0019_seed_ai_crm_fixture
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0020_agent_advisory_execution"
down_revision: str | None = "0019_seed_ai_crm_fixture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("agent_recommendations", sa.Column("action_type", sa.Text(), nullable=True))
    op.add_column(
        "agent_recommendations",
        sa.Column("action_payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "agent_recommendations", sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    )
    op.add_column(
        "agent_recommendations", sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'low'"))
    )
    op.add_column("agent_recommendations", sa.Column("confidence", sa.Numeric(5, 4), nullable=True))
    op.add_column(
        "agent_recommendations",
        sa.Column("execution_status", sa.Text(), nullable=False, server_default=sa.text("'not_started'")),
    )
    op.add_column("agent_recommendations", sa.Column("executed_by", sa.Text(), nullable=True))
    op.add_column("agent_recommendations", sa.Column("executed_at", TS, nullable=True))
    op.add_column(
        "agent_recommendations",
        sa.Column("execution_result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_check_constraint("ck_agent_rec_risk", "agent_recommendations", "risk_level IN ('low', 'medium', 'high')")
    op.create_check_constraint(
        "ck_agent_rec_execution",
        "agent_recommendations",
        "execution_status IN ('not_started', 'executing', 'executed', 'failed')",
    )

    op.create_table(
        "sales_campaigns",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("area_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sales_campaigns"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["agent_recommendations.id"], name="fk_campaign_recommendation"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_campaign_project"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_campaign_area"),
        sa.UniqueConstraint("recommendation_id", name="uq_campaign_recommendation"),
        sa.CheckConstraint("name <> ''", name="ck_campaign_name"),
        sa.CheckConstraint("status IN ('active', 'completed', 'cancelled')", name="ck_campaign_status"),
    )
    op.create_index("ix_sales_campaigns_project_created", "sales_campaigns", ["project_id", "created_at"])
    op.create_table(
        "sales_campaign_units",
        sa.Column("campaign_id", UUID(as_uuid=True), nullable=False),
        sa.Column("unit_id", UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.PrimaryKeyConstraint("campaign_id", "unit_id", name="pk_sales_campaign_units"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["sales_campaigns.id"], name="fk_campaign_unit_campaign", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_campaign_unit_unit"),
        sa.CheckConstraint("priority > 0", name="ck_campaign_unit_priority"),
    )
    op.create_table(
        "agent_executions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_executions"),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["agent_recommendations.id"], name="fk_execution_recommendation"
        ),
        sa.UniqueConstraint("recommendation_id", name="uq_execution_recommendation"),
        sa.CheckConstraint("status IN ('executing', 'executed', 'failed')", name="ck_execution_status"),
        sa.CheckConstraint("actor <> ''", name="ck_execution_actor"),
    )


def downgrade() -> None:
    op.drop_table("agent_executions")
    op.drop_table("sales_campaign_units")
    op.drop_index("ix_sales_campaigns_project_created", table_name="sales_campaigns")
    op.drop_table("sales_campaigns")
    op.drop_constraint("ck_agent_rec_execution", "agent_recommendations", type_="check")
    op.drop_constraint("ck_agent_rec_risk", "agent_recommendations", type_="check")
    for column in (
        "execution_result",
        "executed_at",
        "executed_by",
        "execution_status",
        "confidence",
        "risk_level",
        "evidence",
        "action_payload",
        "action_type",
    ):
        op.drop_column("agent_recommendations", column)
