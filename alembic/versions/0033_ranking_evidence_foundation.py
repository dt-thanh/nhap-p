"""Additive ranking feature catalog, run snapshots, lineage, and explanations.

This revision deliberately does not alter the existing mutable ``feature_snapshots``
compatibility table, ranking configuration JSON, or current-state ranking scores.
The new tables are empty on upgrade and are populated only by a future application
workflow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033_ranking_evidence_foundation"
down_revision: str | None = "0032_replay_identity_index"
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
        "ranking_feature_definitions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("feature_key", sa.Text(), nullable=False),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("grain", sa.Text(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("formula_id", sa.Text(), nullable=False),
        sa.Column("normalization_method", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("missing_policy", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("definition_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_feature_definitions"),
        sa.UniqueConstraint("feature_key", "feature_version", name="uq_ranking_feature_definition_version"),
        sa.CheckConstraint("feature_key <> ''", name="ck_rfd_feature_key_not_blank"),
        sa.CheckConstraint("feature_version <> ''", name="ck_rfd_feature_version_not_blank"),
        sa.CheckConstraint("name <> ''", name="ck_rfd_name_not_blank"),
        sa.CheckConstraint("category <> ''", name="ck_rfd_category_not_blank"),
        sa.CheckConstraint(
            "grain IN ('project', 'area', 'project_area', 'unit')",
            name="ck_rfd_grain",
        ),
        sa.CheckConstraint(
            "value_type IN ('numeric', 'boolean', 'categorical')",
            name="ck_rfd_value_type",
        ),
        sa.CheckConstraint(
            "direction IN ('positive', 'negative', 'neutral')",
            name="ck_rfd_direction",
        ),
        sa.CheckConstraint(
            "missing_policy IN ('skip', 'zero', 'neutral', 'block')",
            name="ck_rfd_missing_policy",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="ck_rfd_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition_metadata) = 'object'",
            name="ck_rfd_metadata_object",
        ),
    )
    op.create_index("ix_rfd_category_status", "ranking_feature_definitions", ["category", "status"])
    op.create_index(
        "uq_rfd_active_feature_key",
        "ranking_feature_definitions",
        ["feature_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "ranking_config_features",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ranking_config_id", UUID, nullable=False),
        sa.Column("feature_definition_id", UUID, nullable=False),
        sa.Column("weight", sa.Numeric(12, 8), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("policy_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_config_features"),
        sa.ForeignKeyConstraint(
            ["ranking_config_id"],
            ["ranking_configs.id"],
            name="fk_rcf_ranking_config_id",
        ),
        sa.ForeignKeyConstraint(
            ["feature_definition_id"],
            ["ranking_feature_definitions.id"],
            name="fk_rcf_feature_definition_id",
        ),
        sa.UniqueConstraint(
            "ranking_config_id",
            "feature_definition_id",
            name="uq_ranking_config_feature",
        ),
        sa.CheckConstraint("weight >= 0 AND weight <= 1", name="ck_rcf_weight_range"),
        sa.CheckConstraint("jsonb_typeof(policy_metadata) = 'object'", name="ck_rcf_policy_object"),
    )
    op.create_index("ix_rcf_config_id", "ranking_config_features", ["ranking_config_id"])
    op.create_index("ix_rcf_feature_definition_id", "ranking_config_features", ["feature_definition_id"])

    op.create_table(
        "ranking_feature_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ranking_run_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False, server_default=sa.text("'project'")),
        sa.Column("area_id", UUID, nullable=True),
        sa.Column("cutoff_at", TS, nullable=False),
        sa.Column("computed_at", TS, nullable=False),
        sa.Column("feature_set_version", sa.Text(), nullable=False),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("quality_summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_feature_snapshots"),
        sa.ForeignKeyConstraint(
            ["ranking_run_id"], ["ranking_runs.id"], name="fk_rfs_ranking_run_id"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_rfs_project_id"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_rfs_area_id"),
        sa.UniqueConstraint(
            "ranking_run_id",
            "project_id",
            "scope_type",
            name="uq_ranking_feature_snapshot_run_project_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "scope_type",
            name="uq_ranking_feature_snapshot_id_project_scope",
        ),
        sa.CheckConstraint("scope_type = 'project'", name="ck_rfs_scope_type_project"),
        sa.CheckConstraint("area_id IS NULL", name="ck_rfs_project_scope_no_area"),
        sa.CheckConstraint("cutoff_at <= computed_at", name="ck_rfs_cutoff_before_computed"),
        sa.CheckConstraint(
            "quality_status IN ('ok', 'warning', 'insufficient_data', 'blocked')",
            name="ck_rfs_quality_status",
        ),
        sa.CheckConstraint("feature_set_version <> ''", name="ck_rfs_feature_set_version_not_blank"),
        sa.CheckConstraint(
            "jsonb_typeof(quality_summary) = 'object'",
            name="ck_rfs_quality_summary_object",
        ),
    )
    op.create_index("ix_rfs_run_id", "ranking_feature_snapshots", ["ranking_run_id"])
    op.create_index(
        "ix_rfs_project_scope_cutoff",
        "ranking_feature_snapshots",
        ["project_id", "scope_type", sa.text("cutoff_at DESC")],
    )

    op.create_table(
        "ranking_feature_values",
        sa.Column("id", UUID, nullable=False),
        sa.Column("snapshot_id", UUID, nullable=False),
        sa.Column("feature_definition_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False, server_default=sa.text("'project'")),
        sa.Column("area_id", UUID, nullable=True),
        sa.Column("unit_id", UUID, nullable=True),
        sa.Column("value_kind", sa.Text(), nullable=False),
        sa.Column("raw_numeric", sa.Numeric(24, 10), nullable=True),
        sa.Column("normalized_numeric", sa.Numeric(12, 8), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("categorical_value", sa.Text(), nullable=True),
        sa.Column("missing_reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("observed_at", TS, nullable=True),
        sa.Column("source_updated_at", TS, nullable=True),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_feature_values"),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "project_id", "scope_type"],
            [
                "ranking_feature_snapshots.id",
                "ranking_feature_snapshots.project_id",
                "ranking_feature_snapshots.scope_type",
            ],
            name="fk_rfv_snapshot_project_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["feature_definition_id"],
            ["ranking_feature_definitions.id"],
            name="fk_rfv_feature_definition_id",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_rfv_project_id"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_rfv_area_id"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_rfv_unit_id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "feature_definition_id",
            "scope_type",
            "project_id",
            name="uq_ranking_feature_value_scope",
        ),
        sa.CheckConstraint("scope_type = 'project'", name="ck_rfv_scope_type_project"),
        sa.CheckConstraint("area_id IS NULL AND unit_id IS NULL", name="ck_rfv_project_scope_shape"),
        sa.CheckConstraint(
            "value_kind IN ('numeric', 'boolean', 'categorical', 'missing')",
            name="ck_rfv_value_kind",
        ),
        sa.CheckConstraint(
            "quality_status IN ('ok', 'warning', 'insufficient_data', 'unavailable', 'unknown', 'stale', 'blocked')",
            name="ck_rfv_quality_status",
        ),
        sa.CheckConstraint(
            "normalized_numeric IS NULL OR (normalized_numeric >= 0 AND normalized_numeric <= 1)",
            name="ck_rfv_normalized_range",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_rfv_confidence_range",
        ),
        sa.CheckConstraint("sample_count IS NULL OR sample_count >= 0", name="ck_rfv_sample_count_nonnegative"),
        sa.CheckConstraint(
            "(quality_status IN ('ok', 'warning') AND missing_reason IS NULL AND "
            "((value_kind = 'numeric' AND (raw_numeric IS NOT NULL OR normalized_numeric IS NOT NULL) "
            "AND boolean_value IS NULL AND categorical_value IS NULL) OR "
            "(value_kind = 'boolean' AND boolean_value IS NOT NULL AND raw_numeric IS NULL "
            "AND normalized_numeric IS NULL AND categorical_value IS NULL) OR "
            "(value_kind = 'categorical' AND categorical_value IS NOT NULL AND categorical_value <> '' "
            "AND raw_numeric IS NULL AND normalized_numeric IS NULL AND boolean_value IS NULL))) OR "
            "(quality_status IN ('insufficient_data', 'unavailable', 'unknown', 'stale', 'blocked') "
            "AND value_kind = 'missing' AND raw_numeric IS NULL AND normalized_numeric IS NULL "
            "AND boolean_value IS NULL AND categorical_value IS NULL AND missing_reason IS NOT NULL "
            "AND missing_reason <> '')",
            name="ck_rfv_typed_value_missing_semantics",
        ),
    )
    op.create_index("ix_rfv_snapshot_id", "ranking_feature_values", ["snapshot_id"])
    op.create_index("ix_rfv_project_quality", "ranking_feature_values", ["project_id", "quality_status"])
    op.create_index("ix_rfv_unit_id", "ranking_feature_values", ["unit_id"])

    op.create_table(
        "ranking_feature_lineage",
        sa.Column("id", UUID, nullable=False),
        sa.Column("feature_value_id", UUID, nullable=False),
        sa.Column("source_relation", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=True),
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("source_event_at", TS, nullable=True),
        sa.Column("source_locator", sa.Text(), nullable=False),
        sa.Column("source_checksum", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_feature_lineage"),
        sa.ForeignKeyConstraint(
            ["feature_value_id"],
            ["ranking_feature_values.id"],
            name="fk_rfl_feature_value_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "feature_value_id",
            "source_relation",
            "source_locator",
            name="uq_ranking_feature_lineage_source",
        ),
        sa.CheckConstraint("source_relation <> ''", name="ck_rfl_source_relation_not_blank"),
        sa.CheckConstraint("source_locator <> ''", name="ck_rfl_source_locator_not_blank"),
        sa.CheckConstraint(
            "source_checksum IS NULL OR source_checksum <> ''",
            name="ck_rfl_source_checksum_not_blank",
        ),
    )
    op.create_index("ix_rfl_source_relation_locator", "ranking_feature_lineage", ["source_relation", "source_locator"])

    op.create_table(
        "ranking_explanations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ranking_run_id", UUID, nullable=False),
        sa.Column("unit_id", UUID, nullable=False),
        sa.Column("feature_value_id", UUID, nullable=False),
        sa.Column("feature_definition_id", UUID, nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("normalized_value", sa.Numeric(12, 8), nullable=True),
        sa.Column("weight", sa.Numeric(12, 8), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("contribution", sa.Numeric(18, 10), nullable=False),
        sa.Column("formula_id", sa.Text(), nullable=False),
        sa.Column("interpretation_code", sa.Text(), nullable=False),
        sa.Column("missing_reason", sa.Text(), nullable=True),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_explanations"),
        sa.ForeignKeyConstraint(["ranking_run_id"], ["ranking_runs.id"], name="fk_re_ranking_run_id"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_re_unit_id"),
        sa.ForeignKeyConstraint(
            ["feature_value_id"],
            ["ranking_feature_values.id"],
            name="fk_re_feature_value_id",
        ),
        sa.ForeignKeyConstraint(
            ["feature_definition_id"],
            ["ranking_feature_definitions.id"],
            name="fk_re_feature_definition_id",
        ),
        sa.UniqueConstraint(
            "ranking_run_id",
            "unit_id",
            "feature_definition_id",
            name="uq_ranking_explanation_run_unit_feature",
        ),
        sa.CheckConstraint(
            "normalized_value IS NULL OR (normalized_value >= 0 AND normalized_value <= 1)",
            name="ck_re_normalized_range",
        ),
        sa.CheckConstraint("weight >= 0 AND weight <= 1", name="ck_re_weight_range"),
        sa.CheckConstraint(
            "direction IN ('positive', 'negative', 'neutral')",
            name="ck_re_direction",
        ),
        sa.CheckConstraint("formula_id <> ''", name="ck_re_formula_id_not_blank"),
        sa.CheckConstraint("interpretation_code <> ''", name="ck_re_interpretation_not_blank"),
        sa.CheckConstraint(
            "quality_status IN ('ok', 'warning', 'insufficient_data', 'unavailable', 'unknown', 'stale', 'blocked')",
            name="ck_re_quality_status",
        ),
        sa.CheckConstraint(
            "missing_reason IS NULL OR missing_reason <> ''",
            name="ck_re_missing_reason_not_blank",
        ),
    )
    op.create_index("ix_re_run_unit", "ranking_explanations", ["ranking_run_id", "unit_id"])
    op.create_index("ix_re_feature_value", "ranking_explanations", ["feature_value_id"])

    op.execute(
        sa.text(
            """
            CREATE FUNCTION ranking_evidence_append_only_guard()
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
        "ranking_feature_snapshots",
        "ranking_feature_values",
        "ranking_feature_lineage",
        "ranking_explanations",
    ):
        op.execute(
            sa.text(
                f"CREATE TRIGGER {table}_append_only_guard "
                f"BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION ranking_evidence_append_only_guard()"
            )
        )


def downgrade() -> None:
    tables = (
        "ranking_explanations",
        "ranking_feature_lineage",
        "ranking_feature_values",
        "ranking_feature_snapshots",
        "ranking_config_features",
        "ranking_feature_definitions",
    )
    populated = [table for table in tables if _has_rows(table)]
    if populated:
        raise RuntimeError(
            "Refusing to downgrade 0033: immutable ranking evidence rows exist in "
            + ", ".join(populated)
        )

    for table in (
        "ranking_feature_snapshots",
        "ranking_feature_values",
        "ranking_feature_lineage",
        "ranking_explanations",
    ):
        op.execute(sa.text(f"DROP TRIGGER {table}_append_only_guard ON {table}"))
    op.execute(sa.text("DROP FUNCTION ranking_evidence_append_only_guard()"))

    op.drop_table("ranking_explanations")
    op.drop_table("ranking_feature_lineage")
    op.drop_table("ranking_feature_values")
    op.drop_table("ranking_feature_snapshots")
    op.drop_table("ranking_config_features")
    op.drop_table("ranking_feature_definitions")
