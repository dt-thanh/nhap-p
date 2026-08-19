"""initial schema — 21 bảng theo ERD ở SRS §5.6

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("launch_date", sa.Date(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )

    op.create_table(
        "users",
        sa.Column("id", UUID, nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email <> ''", name="ck_users_email_not_blank"),
        sa.CheckConstraint("full_name <> ''", name="ck_users_full_name_not_blank"),
        sa.CheckConstraint("role IN ('admin', 'manager', 'analyst')", name="ck_users_role"),
    )

    op.create_table(
        "settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by", UUID, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_settings"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_settings_updated_by"),
        sa.CheckConstraint('"key" <> \'\'', name="ck_settings_key_not_blank"),
    )

    op.create_table(
        "areas",
        sa.Column("id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("area_name", sa.String(), nullable=False),
        sa.Column("unit_type", sa.String(), nullable=False),
        sa.Column("bedrooms", sa.Integer(), nullable=False),
        sa.Column("area_sqm", sa.Numeric(), nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_areas"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_areas_project_id"),
        # Trong một dự án, mỗi (phân khu, loại căn) chỉ tồn tại một lần.
        sa.UniqueConstraint(
            "project_id",
            "area_name",
            "unit_type",
            name="uq_areas_project_name_unit_type",
        ),
        sa.CheckConstraint("area_name <> ''", name="ck_areas_area_name_not_blank"),
        sa.CheckConstraint("unit_type <> ''", name="ck_areas_unit_type_not_blank"),
        sa.CheckConstraint("bedrooms >= 0", name="ck_areas_bedrooms_nonnegative"),
        sa.CheckConstraint("area_sqm > 0", name="ck_areas_area_sqm_positive"),
        sa.CheckConstraint("total_units >= 0", name="ck_areas_total_units_nonnegative"),
    )

    op.create_table(
        "upload_files",
        sa.Column("id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("uploaded_by", UUID, nullable=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("rows_ok", sa.Integer(), nullable=False),
        sa.Column("rows_failed", sa.Integer(), nullable=False),
        sa.Column("uploaded_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_upload_files"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_upload_files_project_id"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], name="fk_upload_files_uploaded_by"),
        # Cùng một checksum có thể tồn tại hợp lệ ở hai dự án khác nhau, nên
        # phạm vi chống trùng là (project_id, checksum) chứ không phải toàn cục.
        sa.UniqueConstraint("project_id", "checksum", name="uq_upload_files_project_checksum"),
        sa.CheckConstraint("filename <> ''", name="ck_upload_files_filename_not_blank"),
        sa.CheckConstraint("checksum <> ''", name="ck_upload_files_checksum_not_blank"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_upload_files_status",
        ),
        sa.CheckConstraint("rows_ok >= 0", name="ck_upload_files_rows_ok_nonnegative"),
        sa.CheckConstraint("rows_failed >= 0", name="ck_upload_files_rows_failed_nonnegative"),
    )

    op.create_table(
        "upload_errors",
        sa.Column("id", UUID, nullable=False),
        sa.Column("file_id", UUID, nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("column_name", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_upload_errors"),
        sa.ForeignKeyConstraint(["file_id"], ["upload_files.id"], name="fk_upload_errors_file_id"),
        sa.CheckConstraint("row_number > 0", name="ck_upload_errors_row_number_positive"),
        sa.CheckConstraint("error_code <> ''", name="ck_upload_errors_error_code_not_blank"),
        sa.CheckConstraint("message <> ''", name="ck_upload_errors_message_not_blank"),
    )
    op.create_index("ix_upload_errors_file_id_row_number", "upload_errors", ["file_id", "row_number"])

    op.create_table(
        "sales_records",
        sa.Column("id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("file_id", UUID, nullable=False),
        sa.Column("sold_date", sa.Date(), nullable=False),
        sa.Column("units_sold", sa.Integer(), nullable=False),
        sa.Column("external_record_id", sa.String(), nullable=False),
        sa.Column("source_row_hash", sa.String(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sales_records"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_sales_records_area_id"),
        sa.ForeignKeyConstraint(["file_id"], ["upload_files.id"], name="fk_sales_records_file_id"),
        # Chống trùng XUYÊN các lần upload: cùng phân khu + ngày + mã bản ghi
        # chỉ được tồn tại một lần, kể cả khi nạp lại từ file khác.
        sa.UniqueConstraint(
            "area_id",
            "sold_date",
            "external_record_id",
            name="uq_sales_area_date_external_id",
        ),
        # source_row_hash duy nhất TRONG một phân khu, không duy nhất toàn cục.
        sa.UniqueConstraint("area_id", "source_row_hash", name="uq_sales_area_source_row_hash"),
        sa.CheckConstraint("units_sold >= 0", name="ck_sales_records_units_sold_nonnegative"),
        sa.CheckConstraint("external_record_id <> ''", name="ck_sales_records_external_record_id_not_blank"),
        sa.CheckConstraint("source_row_hash <> ''", name="ck_sales_records_source_row_hash_not_blank"),
    )
    op.create_index("ix_sales_records_area_id_sold_date", "sales_records", ["area_id", "sold_date"])

    op.create_table(
        "inventory_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("file_id", UUID, nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("units_remaining", sa.Integer(), nullable=False),
        sa.Column("snapshot_type", sa.String(), nullable=False),
        sa.Column("source_row_hash", sa.String(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_snapshots"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_inventory_snapshots_area_id"),
        sa.ForeignKeyConstraint(["file_id"], ["upload_files.id"], name="fk_inventory_snapshots_file_id"),
        # Mỗi phân khu chỉ có một bản chốt cho mỗi (ngày, loại chốt).
        sa.UniqueConstraint(
            "area_id",
            "snapshot_date",
            "snapshot_type",
            name="uq_inventory_area_date_type",
        ),
        sa.UniqueConstraint("area_id", "source_row_hash", name="uq_inventory_area_source_row_hash"),
        sa.CheckConstraint("units_remaining >= 0", name="ck_inventory_snapshots_units_remaining_nonnegative"),
        sa.CheckConstraint(
            "snapshot_type IN ('opening', 'closing', 'manual', 'derived')",
            name="ck_inventory_snapshots_snapshot_type",
        ),
        sa.CheckConstraint("source_row_hash <> ''", name="ck_inventory_snapshots_source_row_hash_not_blank"),
    )
    op.create_index(
        "ix_inventory_snapshots_area_id_snapshot_date",
        "inventory_snapshots",
        ["area_id", sa.text("snapshot_date DESC")],
    )

    op.create_table(
        "absorption_daily",
        sa.Column("id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("units_sold", sa.Integer(), nullable=False),
        sa.Column("velocity_7d", sa.Numeric(), nullable=False),
        sa.Column("velocity_30d", sa.Numeric(), nullable=False),
        sa.Column("data_quality_status", sa.String(), nullable=False),
        sa.Column("is_observed", sa.Boolean(), nullable=False),
        sa.Column("computed_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_absorption_daily"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_absorption_daily_area_id"),
        sa.CheckConstraint("units_sold >= 0", name="ck_absorption_daily_units_sold_nonnegative"),
        sa.CheckConstraint("velocity_7d >= 0", name="ck_absorption_daily_velocity_7d_nonnegative"),
        sa.CheckConstraint("velocity_30d >= 0", name="ck_absorption_daily_velocity_30d_nonnegative"),
        sa.CheckConstraint(
            "data_quality_status IN ('ok', 'warning', 'error')",
            name="ck_absorption_daily_data_quality_status",
        ),
    )
    op.create_index(
        "uq_absorption_daily_area_id_stat_date",
        "absorption_daily",
        ["area_id", "stat_date"],
        unique=True,
    )

    op.create_table(
        "forecast_jobs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("triggered_by", UUID, nullable=True),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("areas_total", sa.Integer(), nullable=False),
        sa.Column("areas_succeeded", sa.Integer(), nullable=False),
        sa.Column("areas_failed", sa.Integer(), nullable=False),
        sa.Column("error_summary", JSONB, nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_jobs"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_forecast_jobs_project_id"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], name="fk_forecast_jobs_triggered_by"),
        sa.CheckConstraint(
            "trigger_type IN ('manual', 'scheduled')",
            name="ck_forecast_jobs_trigger_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_forecast_jobs_status",
        ),
        sa.CheckConstraint("areas_total >= 0", name="ck_forecast_jobs_areas_total_nonnegative"),
        sa.CheckConstraint("areas_succeeded >= 0", name="ck_forecast_jobs_areas_succeeded_nonnegative"),
        sa.CheckConstraint("areas_failed >= 0", name="ck_forecast_jobs_areas_failed_nonnegative"),
        sa.CheckConstraint(
            "areas_succeeded + areas_failed <= areas_total",
            name="ck_forecast_jobs_area_counts",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_forecast_jobs_finished_at_gte_started_at",
        ),
        sa.CheckConstraint(
            "(status IN ('queued', 'running') AND finished_at IS NULL) "
            "OR (status IN ('completed', 'failed', 'cancelled') AND finished_at IS NOT NULL)",
            name="ck_forecast_jobs_finished_at_by_status",
        ),
    )

    op.create_table(
        "forecasts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("job_id", UUID, nullable=False),
        sa.Column("file_id", UUID, nullable=False),
        sa.Column("data_cutoff_date", sa.Date(), nullable=False),
        sa.Column("run_at", TS, nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("feature_version", sa.String(), nullable=False),
        sa.Column("parameters", JSONB, nullable=False),
        sa.Column("velocity_forecast", sa.Numeric(), nullable=False),
        sa.Column("pred_lower", sa.Numeric(), nullable=False),
        sa.Column("pred_upper", sa.Numeric(), nullable=False),
        sa.Column("interval_level", sa.Numeric(), nullable=False),
        sa.Column("sellout_date", sa.Date(), nullable=True),
        sa.Column("confidence_label", sa.String(), nullable=False),
        sa.Column("mape", sa.Numeric(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_forecasts"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_forecasts_area_id"),
        sa.ForeignKeyConstraint(["job_id"], ["forecast_jobs.id"], name="fk_forecasts_job_id"),
        sa.ForeignKeyConstraint(["file_id"], ["upload_files.id"], name="fk_forecasts_file_id"),
        # Mỗi lần chạy job chỉ sinh đúng một dự báo cho một phân khu.
        sa.UniqueConstraint("job_id", "area_id", name="uq_forecasts_job_area"),
        sa.CheckConstraint("horizon_days > 0", name="ck_forecasts_horizon_days_positive"),
        sa.CheckConstraint("model_name <> ''", name="ck_forecasts_model_name_not_blank"),
        sa.CheckConstraint("model_version <> ''", name="ck_forecasts_model_version_not_blank"),
        sa.CheckConstraint("feature_version <> ''", name="ck_forecasts_feature_version_not_blank"),
        sa.CheckConstraint("velocity_forecast >= 0", name="ck_forecasts_velocity_forecast_nonnegative"),
        sa.CheckConstraint("pred_lower <= pred_upper", name="ck_forecasts_pred_bounds"),
        sa.CheckConstraint(
            "interval_level > 0 AND interval_level < 1",
            name="ck_forecasts_interval_level_range",
        ),
        sa.CheckConstraint(
            "confidence_label IN ('low', 'medium', 'high')",
            name="ck_forecasts_confidence_label",
        ),
        sa.CheckConstraint("mape IS NULL OR mape >= 0", name="ck_forecasts_mape_nonnegative"),
        sa.CheckConstraint(
            "sellout_date IS NULL OR sellout_date >= data_cutoff_date",
            name="ck_forecasts_sellout_date_gte_cutoff",
        ),
    )
    op.create_index("ix_forecasts_area_id_run_at", "forecasts", ["area_id", sa.text("run_at DESC")])
    op.create_index("ix_forecasts_job_id", "forecasts", ["job_id"])
    op.create_index("ix_forecasts_file_id", "forecasts", ["file_id"])

    op.create_table(
        "forecast_points",
        sa.Column("id", UUID, nullable=False),
        sa.Column("forecast_id", UUID, nullable=False),
        sa.Column("ds", sa.Date(), nullable=False),
        sa.Column("yhat", sa.Numeric(), nullable=False),
        sa.Column("yhat_lower", sa.Numeric(), nullable=False),
        sa.Column("yhat_upper", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_points"),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.id"], name="fk_forecast_points_forecast_id"),
        # UNIQUE này tự sinh index trên (forecast_id, ds) — phục vụ được đúng
        # những truy vấn mà index thường trước đây phục vụ, nên KHÔNG tạo thêm
        # ix_forecast_points_forecast_id_ds nữa (sẽ là index trùng lặp).
        sa.UniqueConstraint("forecast_id", "ds", name="uq_forecast_points_forecast_date"),
        sa.CheckConstraint("yhat_lower <= yhat_upper", name="ck_forecast_points_bounds"),
    )

    op.create_table(
        "explanations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("forecast_id", UUID, nullable=False),
        sa.Column("content_vi", sa.Text(), nullable=False),
        sa.Column("key_factors", JSONB, nullable=False),
        sa.Column("assumptions", JSONB, nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("prompt_template_version", sa.String(), nullable=False),
        sa.Column("generated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_explanations"),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.id"], name="fk_explanations_forecast_id"),
        sa.UniqueConstraint("forecast_id", name="uq_explanations_forecast_id"),
        sa.CheckConstraint("content_vi <> ''", name="ck_explanations_content_vi_not_blank"),
        sa.CheckConstraint("model_name <> ''", name="ck_explanations_model_name_not_blank"),
        sa.CheckConstraint(
            "prompt_template_version <> ''",
            name="ck_explanations_prompt_template_version_not_blank",
        ),
    )

    op.create_table(
        "alerts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("forecast_id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("alert_type", sa.String(), nullable=False),
        sa.Column("days_to_sellout", sa.Integer(), nullable=False),
        sa.Column("threshold_days", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("closed_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.id"], name="fk_alerts_forecast_id"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_alerts_area_id"),
        # Một dự báo chỉ sinh một cảnh báo cho mỗi loại cảnh báo.
        sa.UniqueConstraint("forecast_id", "alert_type", name="uq_alerts_forecast_type"),
        sa.CheckConstraint("threshold_days >= 0", name="ck_alerts_threshold_days_nonnegative"),
        sa.CheckConstraint("days_to_sellout >= 0", name="ck_alerts_days_to_sellout_nonnegative"),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_alerts_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'closed', 'dismissed')",
            name="ck_alerts_status",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND closed_at IS NULL) "
            "OR (status IN ('closed', 'dismissed') AND closed_at IS NOT NULL)",
            name="ck_alerts_closed_at_by_status",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= created_at",
            name="ck_alerts_closed_at_gte_created_at",
        ),
    )
    op.create_index(
        "ix_alerts_status_severity",
        "alerts",
        ["status", "severity"],
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "suggestions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("forecast_id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("risk_level", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suggestions"),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.id"], name="fk_suggestions_forecast_id"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_suggestions_area_id"),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="ck_suggestions_risk_level",
        ),
        sa.CheckConstraint("action_type <> ''", name="ck_suggestions_action_type_not_blank"),
        sa.CheckConstraint("rationale <> ''", name="ck_suggestions_rationale_not_blank"),
    )
    op.create_index(
        "ix_suggestions_risk_level_created_at",
        "suggestions",
        ["risk_level", sa.text("created_at DESC")],
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", UUID, nullable=False),
        sa.Column("forecast_id", UUID, nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("prompt_template_version", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_amount", sa.Numeric(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("called_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_llm_calls"),
        sa.ForeignKeyConstraint(["forecast_id"], ["forecasts.id"], name="fk_llm_calls_forecast_id"),
        sa.CheckConstraint("provider <> ''", name="ck_llm_calls_provider_not_blank"),
        sa.CheckConstraint("model_name <> ''", name="ck_llm_calls_model_name_not_blank"),
        sa.CheckConstraint(
            "prompt_template_version <> ''",
            name="ck_llm_calls_prompt_template_version_not_blank",
        ),
        sa.CheckConstraint("prompt_tokens >= 0", name="ck_llm_calls_prompt_tokens_nonnegative"),
        sa.CheckConstraint("completion_tokens >= 0", name="ck_llm_calls_completion_tokens_nonnegative"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_llm_calls_latency_ms_nonnegative"),
        sa.CheckConstraint("cost_amount >= 0", name="ck_llm_calls_cost_amount_nonnegative"),
        sa.CheckConstraint("retry_count >= 0", name="ck_llm_calls_retry_count_nonnegative"),
        sa.CheckConstraint(
            "status IN ('success', 'error', 'timeout')",
            name="ck_llm_calls_status",
        ),
        sa.CheckConstraint(
            "(status = 'success' AND error_code IS NULL) "
            "OR (status IN ('error', 'timeout') AND error_code IS NOT NULL)",
            name="ck_llm_calls_error_code_by_status",
        ),
    )
    op.create_index("ix_llm_calls_called_at", "llm_calls", ["called_at"])

    op.create_table(
        "user_areas",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("assigned_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "area_id", name="pk_user_areas"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_areas_user_id"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_user_areas_area_id"),
    )
    op.create_index("ix_user_areas_user_id", "user_areas", ["user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("replaced_by", UUID, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_refresh_tokens_user_id"),
        sa.ForeignKeyConstraint(["replaced_by"], ["refresh_tokens.id"], name="fk_refresh_tokens_replaced_by"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        sa.CheckConstraint("token_hash <> ''", name="ck_refresh_tokens_token_hash_not_blank"),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_tokens_expires_at_gt_created_at",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_refresh_tokens_revoked_at_gte_created_at",
        ),
        sa.CheckConstraint(
            "replaced_by IS NULL OR replaced_by <> id",
            name="ck_refresh_tokens_not_self_replaced",
        ),
    )
    op.create_index("ix_refresh_tokens_user_id_expires_at", "refresh_tokens", ["user_id", "expires_at"])
    # Rotation: một token chỉ được thay thế bởi đúng một token kế nhiệm.
    # Partial index nên nhiều dòng có replaced_by IS NULL vẫn hợp lệ.
    op.create_index(
        "uq_refresh_tokens_replaced_by",
        "refresh_tokens",
        ["replaced_by"],
        unique=True,
        postgresql_where=sa.text("replaced_by IS NOT NULL"),
    )

    op.create_table(
        "proposals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("suggestion_id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("closed_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_proposals"),
        sa.ForeignKeyConstraint(["suggestion_id"], ["suggestions.id"], name="fk_proposals_suggestion_id"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_proposals_area_id"),
        sa.UniqueConstraint("suggestion_id", name="uq_proposals_suggestion_id"),
        sa.CheckConstraint(
            "status IN ('open', 'approved', 'rejected', 'cancelled')",
            name="ck_proposals_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_proposals_version_positive"),
        sa.CheckConstraint(
            "(status = 'open' AND closed_at IS NULL) "
            "OR (status IN ('approved', 'rejected', 'cancelled') AND closed_at IS NOT NULL)",
            name="ck_proposals_closed_at_by_status",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= created_at",
            name="ck_proposals_closed_at_gte_created_at",
        ),
    )
    op.create_index("ix_proposals_status_created_at", "proposals", ["status", sa.text("created_at DESC")])

    op.create_table(
        "approvals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("proposal_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], name="fk_approvals_proposal_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_approvals_user_id"),
        sa.UniqueConstraint("proposal_id", name="uq_approvals_proposal_id"),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_approvals_decision",
        ),
        sa.CheckConstraint("reason <> ''", name="ck_approvals_reason_not_blank"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("entity_key", sa.String(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("ip_address", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_audit_logs_user_id"),
        sa.CheckConstraint("role <> ''", name="ck_audit_logs_role_not_blank"),
        sa.CheckConstraint("action <> ''", name="ck_audit_logs_action_not_blank"),
        sa.CheckConstraint("entity_type <> ''", name="ck_audit_logs_entity_type_not_blank"),
        sa.CheckConstraint("entity_key <> ''", name="ck_audit_logs_entity_key_not_blank"),
        sa.CheckConstraint("ip_address <> ''", name="ck_audit_logs_ip_address_not_blank"),
        sa.CheckConstraint("user_agent <> ''", name="ck_audit_logs_user_agent_not_blank"),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", [sa.text("created_at DESC")])
    op.create_index("ix_audit_logs_entity_type_entity_id", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_logs_user_id_created_at", "audit_logs", ["user_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_user_id_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type_entity_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_table("approvals")

    op.drop_index("ix_proposals_status_created_at", table_name="proposals")
    op.drop_table("proposals")

    op.drop_index("uq_refresh_tokens_replaced_by", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id_expires_at", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_user_areas_user_id", table_name="user_areas")
    op.drop_table("user_areas")

    op.drop_index("ix_llm_calls_called_at", table_name="llm_calls")
    op.drop_table("llm_calls")

    op.drop_index("ix_suggestions_risk_level_created_at", table_name="suggestions")
    op.drop_table("suggestions")

    op.drop_index("ix_alerts_status_severity", table_name="alerts")
    op.drop_table("alerts")

    op.drop_table("explanations")

    op.drop_table("forecast_points")

    op.drop_index("ix_forecasts_file_id", table_name="forecasts")
    op.drop_index("ix_forecasts_job_id", table_name="forecasts")
    op.drop_index("ix_forecasts_area_id_run_at", table_name="forecasts")
    op.drop_table("forecasts")

    op.drop_table("forecast_jobs")

    op.drop_index("uq_absorption_daily_area_id_stat_date", table_name="absorption_daily")
    op.drop_table("absorption_daily")

    op.drop_index("ix_inventory_snapshots_area_id_snapshot_date", table_name="inventory_snapshots")
    op.drop_table("inventory_snapshots")

    op.drop_index("ix_sales_records_area_id_sold_date", table_name="sales_records")
    op.drop_table("sales_records")

    op.drop_index("ix_upload_errors_file_id_row_number", table_name="upload_errors")
    op.drop_table("upload_errors")

    op.drop_table("upload_files")
    op.drop_table("areas")
    op.drop_table("settings")
    op.drop_table("users")
    op.drop_table("projects")
