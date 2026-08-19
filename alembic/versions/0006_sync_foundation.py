"""nền đồng bộ CRM → ứng dụng: metadata lô, danh tính bản ghi nguồn, mô hình lỗi

Revision ID: 0006_sync_foundation
Revises: 0005_idempotent_csv_ingestion
Create Date: 2026-08-09

Đây là phần NỀN của luồng đồng bộ một chiều CRM → ứng dụng (S2). Không có bảng
nghiệp vụ nào ở đây: `units` / `deals` thuộc giai đoạn sau, và revision này cố ý
không đụng tới chúng.

Ba nhóm thay đổi:

1. **`upload_files` trở thành bản ghi LÔ dùng chung** cho cả hai đường vào — file
   CSV/Excel (đã có) và payload JSON của CRM (mới). KHÔNG đổi tên bảng: bốn khoá
   ngoại đang trỏ vào nó (`upload_errors`, `sales_records`, `inventory_snapshots`,
   `forecasts`), đổi tên là kéo theo cả mã nguồn lẫn tài liệu mà chẳng được gì.
   Cột thêm vào đều có DEFAULT mô tả đúng dữ liệu cũ (`manual_upload` / `csv` /
   `file_upload` / `full_snapshot`), nên dòng đã có không bị nói sai về nguồn gốc.

   `filename` và `checksum` chuyển sang NULL được: payload đẩy qua API không có
   tên file, và byte của payload không phải danh tính lô — danh tính là
   `(source_system, source_instance_id, external_batch_id)`.

2. **`upload_errors` mang thêm ngữ cảnh của lỗi JSON.** `row_number` phải NULL
   được: một bản ghi trong mảng `records` không có "số dòng", nó có `json_path`.
   Ràng buộc mới đòi mỗi lỗi phải định vị được bằng MỘT trong hai cách, hoặc là
   lỗi mức lô.

3. **`crm_source_records` — mảnh còn thiếu.** Bảng này ánh xạ danh tính bản ghi ở
   CRM sang trạng thái đã chấp nhận ở đây, kèm phiên bản nguồn và dấu vân payload.
   Không có nó thì không thể trả lời "bản đến có mới hơn bản đang giữ không", và
   mọi thứ còn lại của đồng bộ (idempotent, tombstone, conflict) đều không dựng
   được. Danh tính nằm NGOÀI bảng nghiệp vụ để `sales_records` / `areas` và các
   bảng tương lai không phải mang cột của hệ nguồn.

Không xoá dữ liệu: mọi thao tác đều bảo toàn dòng đang có. `downgrade()` gỡ đúng
những gì `upgrade()` thêm.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_sync_foundation"
down_revision: str | None = "0005_idempotent_csv_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

# Trạng thái lô. `partially_completed` là giá trị MỚI: lô JSON có thể vừa ghi
# nhận được vài bản ghi vừa từ chối vài bản ghi khác, và gọi đó là 'completed'
# thì che mất phần hỏng.
BATCH_STATUSES = ("pending", "processing", "completed", "partially_completed", "failed")

# Phân loại lỗi — tách "sai cấu trúc" khỏi "sai dữ liệu" khỏi "đụng độ phiên bản"
# để phía vận hành biết lỗi nào sửa được ở file/payload, lỗi nào phải hỏi hệ nguồn.
ERROR_CATEGORIES = ("transport", "schema", "field", "business", "conflict")

RETRY_STATUSES = ("open", "retrying", "resolved", "permanent")

# Kết quả xử lý một bản ghi nguồn. Đúng một giá trị cho mỗi lần chạm tới.
DECISIONS = ("insert", "update", "skip_stale", "duplicate_noop", "conflict", "tombstone")

MIRROR_STATES = ("active", "tombstoned")

_UPLOAD_FILES_COLUMNS = (
    # Danh tính hệ nguồn. DEFAULT mô tả đúng dòng cũ: chúng đến từ người dùng
    # tải file lên, không đến từ CRM.
    ("source_system", sa.Text(), "'manual_upload'"),
    ("source_instance_id", sa.Text(), "'local'"),
    ("source_entity", sa.Text(), None),
    ("input_format", sa.Text(), "'csv'"),
    ("transport_mode", sa.Text(), "'file_upload'"),
    ("sync_mode", sa.Text(), "'full_snapshot'"),
    ("schema_version", sa.Integer(), "1"),
    ("external_batch_id", sa.Text(), None),
    ("rows_received", sa.Integer(), "0"),
    ("finished_at", TS, None),
    ("last_source_cursor", sa.Text(), None),
    ("error_summary", JSONB, "'{}'::jsonb"),
)

_UPLOAD_ERRORS_COLUMNS = (
    ("error_category", sa.Text(), "'field'"),
    ("json_path", sa.Text(), None),
    ("source_record_id", sa.Text(), None),
    ("record_locator", sa.Text(), None),
    ("field_name", sa.Text(), None),
    ("raw_value_redacted", sa.Text(), None),
    ("retry_status", sa.Text(), "'open'"),
    ("resolved_at", TS, None),
)


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- 1. upload_files: bản ghi lô dùng chung cho file và payload -----------
    for name, type_, default in _UPLOAD_FILES_COLUMNS:
        op.add_column(
            "upload_files",
            sa.Column(
                name, type_, nullable=False if default else True, server_default=sa.text(default) if default else None
            ),
        )

    op.alter_column("upload_files", "filename", nullable=True)
    op.alter_column("upload_files", "checksum", nullable=True)
    op.drop_constraint("ck_upload_files_filename_not_blank", "upload_files", type_="check")
    op.drop_constraint("ck_upload_files_checksum_not_blank", "upload_files", type_="check")
    op.create_check_constraint(
        "ck_upload_files_filename_not_blank", "upload_files", "filename IS NULL OR filename <> ''"
    )
    op.create_check_constraint(
        "ck_upload_files_checksum_not_blank", "upload_files", "checksum IS NULL OR checksum <> ''"
    )

    op.drop_constraint("ck_upload_files_status", "upload_files", type_="check")
    op.create_check_constraint("ck_upload_files_status", "upload_files", _in_list("status", BATCH_STATUSES))

    op.create_check_constraint("ck_upload_files_rows_received_nonnegative", "upload_files", "rows_received >= 0")
    op.create_check_constraint("ck_upload_files_schema_version_positive", "upload_files", "schema_version > 0")
    op.create_check_constraint(
        "ck_upload_files_input_format", "upload_files", _in_list("input_format", ("csv", "xlsx", "json"))
    )
    op.create_check_constraint(
        "ck_upload_files_transport_mode", "upload_files", _in_list("transport_mode", ("file_upload", "api_push"))
    )
    op.create_check_constraint(
        "ck_upload_files_sync_mode", "upload_files", _in_list("sync_mode", ("full_snapshot", "incremental"))
    )

    # Danh tính lô của luồng đồng bộ. Partial: lô tải file không có
    # external_batch_id, và NULL thì không tham gia ràng buộc.
    op.create_index(
        "uq_upload_files_source_batch",
        "upload_files",
        ["source_system", "source_instance_id", "external_batch_id"],
        unique=True,
        postgresql_where=sa.text("external_batch_id IS NOT NULL"),
    )

    # --- 2. upload_errors: định vị được lỗi của cả CSV lẫn JSON ---------------
    for name, type_, default in _UPLOAD_ERRORS_COLUMNS:
        op.add_column(
            "upload_errors",
            sa.Column(
                name, type_, nullable=False if default else True, server_default=sa.text(default) if default else None
            ),
        )

    op.alter_column("upload_errors", "row_number", nullable=True)
    op.drop_constraint("ck_upload_errors_row_number_positive", "upload_errors", type_="check")
    op.create_check_constraint(
        "ck_upload_errors_row_number_positive", "upload_errors", "row_number IS NULL OR row_number > 0"
    )
    op.create_check_constraint(
        "ck_upload_errors_error_category", "upload_errors", _in_list("error_category", ERROR_CATEGORIES)
    )
    op.create_check_constraint(
        "ck_upload_errors_retry_status", "upload_errors", _in_list("retry_status", RETRY_STATUSES)
    )
    # Mỗi lỗi phải chỉ ra được nó nằm ở đâu — trừ lỗi mức lô (transport/schema),
    # loại vốn không thuộc bản ghi nào.
    op.create_check_constraint(
        "ck_upload_errors_locatable",
        "upload_errors",
        "row_number IS NOT NULL OR json_path IS NOT NULL OR error_category IN ('transport', 'schema')",
    )

    # --- 3. crm_source_records: danh tính + phiên bản của bản ghi nguồn -------
    op.create_table(
        "crm_source_records",
        sa.Column("id", UUID, nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_instance_id", sa.Text(), nullable=False),
        sa.Column("source_entity", sa.Text(), nullable=False),
        sa.Column("source_record_id", sa.Text(), nullable=False),
        # Lô đầu tiên và lô gần nhất chạm tới bản ghi này.
        sa.Column("first_sync_run_id", UUID, nullable=False),
        sa.Column("last_sync_run_id", UUID, nullable=False),
        sa.Column("external_batch_id", sa.Text(), nullable=True),
        # Phiên bản nguồn. Ưu tiên `source_revision` (số thứ tự do CRM cấp) vì nó
        # không phụ thuộc đồng hồ; `source_updated_at` là phương án còn lại.
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("source_updated_at", TS, nullable=True),
        # Dấu vân của phần `data`. Cùng phiên bản mà khác dấu vân = đụng độ thật.
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("last_decision", sa.Text(), nullable=False),
        # Đụng độ được GHI LẠI chứ không giải quyết: bên này không ghi ngược về
        # CRM, nên chỉ giữ nguyên trạng thái đã chấp nhận và báo cho người xem.
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("conflict_payload_hash", sa.Text(), nullable=True),
        sa.Column("conflict_detected_at", TS, nullable=True),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("deleted_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crm_source_records"),
        sa.ForeignKeyConstraint(
            ["first_sync_run_id"], ["upload_files.id"], name="fk_crm_source_records_first_sync_run_id"
        ),
        sa.ForeignKeyConstraint(
            ["last_sync_run_id"], ["upload_files.id"], name="fk_crm_source_records_last_sync_run_id"
        ),
        # Danh tính ổn định của một bản ghi ở hệ nguồn.
        sa.UniqueConstraint(
            "source_system",
            "source_instance_id",
            "source_entity",
            "source_record_id",
            name="uq_crm_source_records_identity",
        ),
        sa.CheckConstraint("source_system <> ''", name="ck_crm_source_records_source_system_not_blank"),
        sa.CheckConstraint("source_instance_id <> ''", name="ck_crm_source_records_instance_not_blank"),
        sa.CheckConstraint("source_entity <> ''", name="ck_crm_source_records_entity_not_blank"),
        sa.CheckConstraint("source_record_id <> ''", name="ck_crm_source_records_record_id_not_blank"),
        sa.CheckConstraint("payload_hash <> ''", name="ck_crm_source_records_payload_hash_not_blank"),
        sa.CheckConstraint(_in_list("state", MIRROR_STATES), name="ck_crm_source_records_state"),
        sa.CheckConstraint(_in_list("last_decision", DECISIONS), name="ck_crm_source_records_last_decision"),
        sa.CheckConstraint("conflict_count >= 0", name="ck_crm_source_records_conflict_count_nonnegative"),
        # Tombstone và mốc xoá phải đi cùng nhau, không cái nào đứng một mình.
        sa.CheckConstraint(
            "(state = 'tombstoned') = (deleted_at IS NOT NULL)",
            name="ck_crm_source_records_tombstone_consistent",
        ),
        sa.CheckConstraint("last_seen_at >= first_seen_at", name="ck_crm_source_records_seen_order"),
    )
    op.create_index("ix_crm_source_records_last_sync_run_id", "crm_source_records", ["last_sync_run_id"])
    op.create_index(
        "ix_crm_source_records_entity_last_seen",
        "crm_source_records",
        ["source_entity", sa.text("last_seen_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_source_records_entity_last_seen", table_name="crm_source_records")
    op.drop_index("ix_crm_source_records_last_sync_run_id", table_name="crm_source_records")
    op.drop_table("crm_source_records")

    op.drop_constraint("ck_upload_errors_locatable", "upload_errors", type_="check")
    op.drop_constraint("ck_upload_errors_retry_status", "upload_errors", type_="check")
    op.drop_constraint("ck_upload_errors_error_category", "upload_errors", type_="check")
    op.drop_constraint("ck_upload_errors_row_number_positive", "upload_errors", type_="check")
    # Dòng lỗi của JSON không có row_number; đi lùi thì chúng không còn hợp lệ.
    # Xoá đúng những dòng đó rồi mới siết lại NOT NULL — thà mất bản ghi lỗi
    # (đã có error_summary của lô) còn hơn migration vỡ giữa chừng.
    op.execute("DELETE FROM upload_errors WHERE row_number IS NULL")
    op.alter_column("upload_errors", "row_number", nullable=False)
    op.create_check_constraint("ck_upload_errors_row_number_positive", "upload_errors", "row_number > 0")
    for name, _type, _default in _UPLOAD_ERRORS_COLUMNS:
        op.drop_column("upload_errors", name)

    op.drop_index("uq_upload_files_source_batch", table_name="upload_files")
    op.drop_constraint("ck_upload_files_sync_mode", "upload_files", type_="check")
    op.drop_constraint("ck_upload_files_transport_mode", "upload_files", type_="check")
    op.drop_constraint("ck_upload_files_input_format", "upload_files", type_="check")
    op.drop_constraint("ck_upload_files_schema_version_positive", "upload_files", type_="check")
    op.drop_constraint("ck_upload_files_rows_received_nonnegative", "upload_files", type_="check")

    op.drop_constraint("ck_upload_files_status", "upload_files", type_="check")
    # Trạng thái mới không tồn tại ở lược đồ cũ; quy về 'failed' để CHECK cũ nhận.
    op.execute("UPDATE upload_files SET status = 'failed' WHERE status = 'partially_completed'")
    op.create_check_constraint(
        "ck_upload_files_status",
        "upload_files",
        _in_list("status", ("pending", "processing", "completed", "failed")),
    )

    op.drop_constraint("ck_upload_files_checksum_not_blank", "upload_files", type_="check")
    op.drop_constraint("ck_upload_files_filename_not_blank", "upload_files", type_="check")
    # Lô đẩy qua API không có filename/checksum; lược đồ cũ bắt buộc phải có.
    op.execute("DELETE FROM upload_files WHERE filename IS NULL OR checksum IS NULL")
    op.alter_column("upload_files", "filename", nullable=False)
    op.alter_column("upload_files", "checksum", nullable=False)
    op.create_check_constraint("ck_upload_files_filename_not_blank", "upload_files", "filename <> ''")
    op.create_check_constraint("ck_upload_files_checksum_not_blank", "upload_files", "checksum <> ''")

    for name, _type, _default in _UPLOAD_FILES_COLUMNS:
        op.drop_column("upload_files", name)
