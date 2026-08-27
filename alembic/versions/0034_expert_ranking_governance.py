"""Additive expert governance and evidence metadata for ranking configs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0034_expert_ranking_governance"
down_revision: str | None = "0033_ranking_evidence_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB


def _has_rows(table: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar())


def upgrade() -> None:
    op.create_table(
        "expert_profiles",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("identity_subject", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("expertise_summary", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_expert_profiles"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_expert_profiles_user_id", ondelete="SET NULL"),
        sa.UniqueConstraint("identity_subject", name="uq_expert_profiles_identity_subject"),
        sa.CheckConstraint("identity_subject <> ''", name="ck_expert_profiles_subject_not_blank"),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'retired')",
            name="ck_expert_profiles_status",
        ),
    )
    op.create_index(
        "uq_expert_profiles_user_id",
        "expert_profiles",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    op.create_table(
        "ranking_weight_proposals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("base_config_id", UUID, nullable=False),
        sa.Column("proposed_config_id", UUID, nullable=True),
        sa.Column("scope_type", sa.Text(), nullable=False, server_default=sa.text("'project'")),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("created_by_expert_id", UUID, nullable=False),
        sa.Column("submitted_at", TS, nullable=True),
        sa.Column("approved_at", TS, nullable=True),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_weight_proposals"),
        sa.ForeignKeyConstraint(
            ["base_config_id"], ["ranking_configs.id"], name="fk_rwp_base_config_id"
        ),
        sa.ForeignKeyConstraint(
            ["proposed_config_id"], ["ranking_configs.id"], name="fk_rwp_proposed_config_id"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_rwp_project_id"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_rwp_area_id"),
        sa.ForeignKeyConstraint(
            ["created_by_expert_id"],
            ["expert_profiles.id"],
            name="fk_rwp_created_by_expert_id",
        ),
        sa.CheckConstraint("scope_type = 'project'", name="ck_rwp_scope_type_project"),
        sa.CheckConstraint("area_id IS NULL", name="ck_rwp_project_scope_no_area"),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'under_review', 'approved', 'rejected', 'withdrawn', 'published')",
            name="ck_rwp_status",
        ),
        sa.CheckConstraint(
            "proposed_config_id IS NULL OR proposed_config_id <> base_config_id",
            name="ck_rwp_distinct_configs",
        ),
        sa.CheckConstraint(
            "submitted_at IS NULL OR submitted_at >= created_at",
            name="ck_rwp_submitted_at_order",
        ),
        sa.CheckConstraint(
            "approved_at IS NULL OR submitted_at IS NOT NULL",
            name="ck_rwp_approved_requires_submitted",
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR approved_at IS NOT NULL",
            name="ck_rwp_published_requires_approved",
        ),
    )
    op.create_index("ix_rwp_project_status", "ranking_weight_proposals", ["project_id", "status"])
    op.create_index("ix_rwp_base_config", "ranking_weight_proposals", ["base_config_id"])

    op.create_table(
        "ranking_feature_justifications",
        sa.Column("id", UUID, nullable=False),
        sa.Column("proposal_id", UUID, nullable=False),
        sa.Column("feature_definition_id", UUID, nullable=False),
        sa.Column("previous_weight", sa.Numeric(12, 8), nullable=True),
        sa.Column("proposed_weight", sa.Numeric(12, 8), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("methodology", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("limitations", sa.Text(), nullable=False),
        sa.Column("created_by_expert_id", UUID, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_feature_justifications"),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ranking_weight_proposals.id"],
            name="fk_rfj_proposal_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["feature_definition_id"],
            ["ranking_feature_definitions.id"],
            name="fk_rfj_feature_definition_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_expert_id"],
            ["expert_profiles.id"],
            name="fk_rfj_created_by_expert_id",
        ),
        sa.UniqueConstraint(
            "proposal_id",
            "feature_definition_id",
            name="uq_ranking_feature_justification_proposal_feature",
        ),
        sa.CheckConstraint(
            "previous_weight IS NULL OR (previous_weight >= 0 AND previous_weight <= 1)",
            name="ck_rfj_previous_weight_range",
        ),
        sa.CheckConstraint("proposed_weight >= 0 AND proposed_weight <= 1", name="ck_rfj_proposed_weight_range"),
        sa.CheckConstraint("rationale <> ''", name="ck_rfj_rationale_not_blank"),
        sa.CheckConstraint("methodology <> ''", name="ck_rfj_methodology_not_blank"),
        sa.CheckConstraint("evidence_summary <> ''", name="ck_rfj_evidence_summary_not_blank"),
        sa.CheckConstraint("limitations <> ''", name="ck_rfj_limitations_not_blank"),
        sa.CheckConstraint(
            "expected_effect IN ('increase', 'decrease', 'neutral', 'context_dependent')",
            name="ck_rfj_expected_effect",
        ),
        sa.CheckConstraint("confidence IN ('low', 'medium', 'high')", name="ck_rfj_confidence"),
    )
    op.create_index("ix_rfj_proposal_id", "ranking_feature_justifications", ["proposal_id"])

    op.create_table(
        "ranking_evidence_documents",
        sa.Column("id", UUID, nullable=False),
        sa.Column("proposal_id", UUID, nullable=True),
        sa.Column("uploaded_by_expert_id", UUID, nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("object_storage_key", sa.Text(), nullable=False),
        sa.Column("sha256_checksum", sa.Text(), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("extraction_status", sa.Text(), nullable=False, server_default=sa.text("'not_requested'")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_evidence_documents"),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ranking_weight_proposals.id"],
            name="fk_red_proposal_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_expert_id"],
            ["expert_profiles.id"],
            name="fk_red_uploaded_by_expert_id",
        ),
        sa.UniqueConstraint("object_storage_key", name="uq_red_object_storage_key"),
        sa.CheckConstraint("original_filename <> ''", name="ck_red_filename_not_blank"),
        sa.CheckConstraint(
            "mime_type IN ('application/pdf', 'text/plain', 'text/markdown')",
            name="ck_red_mime_type",
        ),
        sa.CheckConstraint("object_storage_key <> ''", name="ck_red_storage_key_not_blank"),
        sa.CheckConstraint(
            "sha256_checksum ~ '^[0-9A-Fa-f]{64}$'",
            name="ck_red_sha256_checksum",
        ),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_red_file_size_positive"),
        sa.CheckConstraint(
            "extraction_status IN ('not_requested', 'pending', 'succeeded', 'failed', 'not_supported')",
            name="ck_red_extraction_status",
        ),
    )
    op.create_index("ix_red_proposal_id", "ranking_evidence_documents", ["proposal_id"])

    op.create_table(
        "ranking_evidence_document_features",
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("feature_justification_id", UUID, nullable=False),
        sa.PrimaryKeyConstraint(
            "document_id",
            "feature_justification_id",
            name="pk_ranking_evidence_document_features",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ranking_evidence_documents.id"],
            name="fk_redf_document_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["feature_justification_id"],
            ["ranking_feature_justifications.id"],
            name="fk_redf_feature_justification_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_redf_feature_justification_id",
        "ranking_evidence_document_features",
        ["feature_justification_id"],
    )

    op.create_table(
        "ranking_proposal_reviews",
        sa.Column("id", UUID, nullable=False),
        sa.Column("proposal_id", UUID, nullable=False),
        sa.Column("reviewer_expert_id", UUID, nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_proposal_reviews"),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ranking_weight_proposals.id"],
            name="fk_rpr_proposal_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_expert_id"],
            ["expert_profiles.id"],
            name="fk_rpr_reviewer_expert_id",
        ),
        sa.UniqueConstraint(
            "proposal_id",
            "reviewer_expert_id",
            name="uq_ranking_proposal_review_reviewer",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected', 'request_changes')",
            name="ck_rpr_decision",
        ),
        sa.CheckConstraint("comment <> ''", name="ck_rpr_comment_not_blank"),
    )
    op.create_index("ix_rpr_proposal_id", "ranking_proposal_reviews", ["proposal_id", "decided_at"])

    op.create_table(
        "ranking_config_audit_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ranking_config_id", UUID, nullable=True),
        sa.Column("proposal_id", UUID, nullable=True),
        sa.Column("actor_expert_id", UUID, nullable=True),
        sa.Column("actor_identity_subject", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("before_status", sa.Text(), nullable=True),
        sa.Column("after_status", sa.Text(), nullable=True),
        sa.Column("before_state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("after_state", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_config_audit_events"),
        sa.ForeignKeyConstraint(
            ["ranking_config_id"],
            ["ranking_configs.id"],
            name="fk_rcae_ranking_config_id",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["ranking_weight_proposals.id"],
            name="fk_rcae_proposal_id",
        ),
        sa.ForeignKeyConstraint(
            ["actor_expert_id"],
            ["expert_profiles.id"],
            name="fk_rcae_actor_expert_id",
        ),
        sa.CheckConstraint(
            "ranking_config_id IS NOT NULL OR proposal_id IS NOT NULL",
            name="ck_rcae_entity_reference",
        ),
        sa.CheckConstraint("actor_identity_subject <> ''", name="ck_rcae_actor_subject_not_blank"),
        sa.CheckConstraint(
            "event_type IN ('created', 'submitted', 'reviewed', 'approved', 'rejected', 'published', 'rolled_back')",
            name="ck_rcae_event_type",
        ),
        sa.CheckConstraint("jsonb_typeof(before_state) = 'object'", name="ck_rcae_before_state_object"),
        sa.CheckConstraint("jsonb_typeof(after_state) = 'object'", name="ck_rcae_after_state_object"),
    )
    op.create_index(
        "ix_rcae_config_created",
        "ranking_config_audit_events",
        ["ranking_config_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_rcae_proposal_created",
        "ranking_config_audit_events",
        ["proposal_id", sa.text("created_at DESC")],
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION ranking_governance_append_only_guard()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'table % is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
                    USING ERRCODE = '55000';
            END;
            $$
            """
        )
    )
    for table in (
        "ranking_evidence_documents",
        "ranking_evidence_document_features",
        "ranking_proposal_reviews",
        "ranking_config_audit_events",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER {table}_append_only_guard "
                f"BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION ranking_governance_append_only_guard()"
            )
        )


def downgrade() -> None:
    tables = (
        "ranking_config_audit_events",
        "ranking_proposal_reviews",
        "ranking_evidence_document_features",
        "ranking_evidence_documents",
        "ranking_feature_justifications",
        "ranking_weight_proposals",
        "expert_profiles",
    )
    populated = [table for table in tables if _has_rows(table)]
    if populated:
        raise RuntimeError(
            "Refusing to downgrade 0034: immutable expert-governance rows exist in "
            + ", ".join(populated)
        )

    for table in (
        "ranking_evidence_documents",
        "ranking_evidence_document_features",
        "ranking_proposal_reviews",
        "ranking_config_audit_events",
    ):
        op.execute(sa.text(f"DROP TRIGGER {table}_append_only_guard ON {table}"))
    op.execute(sa.text("DROP FUNCTION ranking_governance_append_only_guard()"))

    op.drop_table("ranking_config_audit_events")
    op.drop_table("ranking_proposal_reviews")
    op.drop_table("ranking_evidence_document_features")
    op.drop_table("ranking_evidence_documents")
    op.drop_table("ranking_feature_justifications")
    op.drop_table("ranking_weight_proposals")
    op.drop_table("expert_profiles")
