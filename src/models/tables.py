"""Bảng SQLAlchemy Core cho luồng nạp dữ liệu.

CHỈ khai báo những bảng mà tầng ứng dụng thực sự chạm tới (hiện là 22), không
phải toàn bộ bảng của migration — khai thừa thì phải bảo trì thừa.

Dùng Core (`sa.Table`) chứ không dùng ORM: luồng này chỉ ghi hàng loạt rồi thôi,
không cần identity map, lazy loading hay dirty tracking. `insert()` + executemany
của Core là đường ngắn nhất và nhanh nhất tới đích.

Định nghĩa ở đây phải khớp các revision trong `alembic/versions/`. Migration là
nguồn sự thật; file này chỉ là hình chiếu của nó để code gọi tên cột. Bốn bảng
xếp hạng được đối chiếu với schema thật ở
`tests/test_migrations/test_0015_ranking_results.py::test_core_table_definitions_match_the_migrated_schema`.
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

metadata = sa.MetaData()

# API kiểm tra project_id có thật trước khi nhận file, và liệt kê dự án cho
# frontend chọn. Project/Area được ghi bởi ingestion; dashboard chỉ đọc ảnh và
# dữ liệu đã chiếu.
projects = sa.Table(
    "projects",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("launch_date", sa.Date(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    # Workflow duyệt (0002). Chưa có code nào đọc, khai ở đây để bản chiếu không
    # lệch khỏi schema khi tầng duyệt được cài đặt.
    sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'active'")),
    # Nội dung hiển thị (0002). NOT NULL, default '' từ 0003.
    sa.Column("headline", sa.String(255), nullable=False, server_default=sa.text("''")),
    sa.Column("introduce", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("cover_image_url", sa.Text(), nullable=True),
    # public_id của Cloudinary — cần để XOÁ ảnh, URL không đủ (xem 0004).
    sa.Column("cover_image_public_id", sa.Text(), nullable=True),
    sa.Column("created_by", UUID, nullable=True),
    sa.Column("reviewed_by", UUID, nullable=True),
    sa.Column("reviewed_at", TS, nullable=True),
    sa.Column("review_reason", sa.Text(), nullable=True),
    # Công tắc cắt sang bộ tính, theo TỪNG dự án (0012). Mặc định
    # 'legacy_aggregate' nên Phase 6 không đổi hành vi của dự án nào.
    sa.Column("absorption_calculator", sa.Text(), nullable=False),
    # --- Phase D (0017): danh tính nguồn — Mini CRM là TÁC GIẢ từ đây, backend
    # chỉ SOI GƯƠNG. NULL ở cả năm cột dưới = dự án di sản, tạo TRƯỚC Phase D —
    # không bịa danh tính nguồn cho chúng.
    sa.Column("external_id", sa.Text(), nullable=True),
    sa.Column("source_system", sa.Text(), nullable=True),
    sa.Column("source_instance_id", sa.Text(), nullable=True),
    sa.Column("source_revision", sa.BigInteger(), nullable=True),
    sa.Column("source_updated_at", TS, nullable=True),
    sa.Column("updated_at", TS, nullable=False),
)

# Bản ghi LÔ, dùng chung cho cả hai đường vào: tải file (CSV/Excel) và payload
# JSON đẩy qua API đồng bộ. Không đổi tên thành `sync_runs` — bốn khoá ngoại đang
# trỏ vào bảng này (xem 0006_sync_foundation).
upload_files = sa.Table(
    "upload_files",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    # NULLABLE từ 0017: một lô `source_entity='projects'` TẠO dự án — chưa có UUID
    # nào để ghi tới khi lô xử lý xong. Ba loại lô còn lại (areas/units/deals) vẫn
    # đòi project_id ở TẦNG ỨNG DỤNG (`SyncRunService`), hành vi không đổi.
    sa.Column("project_id", UUID, nullable=True),
    sa.Column("uploaded_by", UUID, nullable=True),  # NULL ở MVP 1 — chưa có auth
    # NULL với lô đẩy qua API: payload không có tên file, và byte của nó không
    # phải danh tính lô (0006).
    sa.Column("filename", sa.String(), nullable=True),
    sa.Column("checksum", sa.String(), nullable=True),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("rows_ok", sa.Integer(), nullable=False),
    sa.Column("rows_failed", sa.Integer(), nullable=False),
    sa.Column("uploaded_at", TS, nullable=False),
    # 0006 — metadata nguồn của lô.
    sa.Column("source_system", sa.Text(), nullable=False),
    sa.Column("source_instance_id", sa.Text(), nullable=False),
    sa.Column("source_entity", sa.Text(), nullable=True),
    sa.Column("input_format", sa.Text(), nullable=False),
    sa.Column("transport_mode", sa.Text(), nullable=False),
    sa.Column("sync_mode", sa.Text(), nullable=False),
    sa.Column("schema_version", sa.Integer(), nullable=False),
    sa.Column("external_batch_id", sa.Text(), nullable=True),
    sa.Column("rows_received", sa.Integer(), nullable=False),
    sa.Column("finished_at", TS, nullable=True),
    sa.Column("last_source_cursor", sa.Text(), nullable=True),
    sa.Column("error_summary", JSONB, nullable=False),
    # Metadata ảnh chụp (0011). NULL với lô tăng dần — xem migration.
    sa.Column("snapshot_id", sa.Text(), nullable=True),
    sa.Column("chunk_index", sa.Integer(), nullable=True),
    sa.Column("chunk_total", sa.Integer(), nullable=True),
    sa.Column("snapshot_complete", sa.Boolean(), nullable=True),
    sa.Column("snapshot_scope", JSONB, nullable=True),
)

upload_errors = sa.Table(
    "upload_errors",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("file_id", UUID, nullable=False),
    # NULL với lỗi của bản ghi JSON — chúng định vị bằng `json_path` (0006).
    sa.Column("row_number", sa.Integer(), nullable=True),
    sa.Column("column_name", sa.String(), nullable=True),
    sa.Column("error_code", sa.String(), nullable=False),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    # 0006 — ngữ cảnh đủ để sửa rồi gửi lại đúng bản ghi hỏng.
    sa.Column("error_category", sa.Text(), nullable=False),
    sa.Column("json_path", sa.Text(), nullable=True),
    sa.Column("source_record_id", sa.Text(), nullable=True),
    sa.Column("record_locator", sa.Text(), nullable=True),
    sa.Column("field_name", sa.Text(), nullable=True),
    sa.Column("raw_value_redacted", sa.Text(), nullable=True),
    sa.Column("retry_status", sa.Text(), nullable=False),
    sa.Column("resolved_at", TS, nullable=True),
)

# Ánh xạ danh tính bản ghi ở CRM → trạng thái đã chấp nhận ở đây. Cố ý nằm NGOÀI
# các bảng nghiệp vụ để chúng không phải mang cột của hệ nguồn (0006).
crm_source_records = sa.Table(
    "crm_source_records",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("source_system", sa.Text(), nullable=False),
    sa.Column("source_instance_id", sa.Text(), nullable=False),
    sa.Column("source_entity", sa.Text(), nullable=False),
    sa.Column("source_record_id", sa.Text(), nullable=False),
    sa.Column("first_sync_run_id", UUID, nullable=False),
    sa.Column("last_sync_run_id", UUID, nullable=False),
    sa.Column("external_batch_id", sa.Text(), nullable=True),
    sa.Column("source_revision", sa.BigInteger(), nullable=True),
    sa.Column("source_updated_at", TS, nullable=True),
    sa.Column("payload_hash", sa.Text(), nullable=False),
    sa.Column("state", sa.Text(), nullable=False),
    sa.Column("last_decision", sa.Text(), nullable=False),
    sa.Column("conflict_count", sa.Integer(), nullable=False),
    sa.Column("conflict_payload_hash", sa.Text(), nullable=True),
    sa.Column("conflict_detected_at", TS, nullable=True),
    sa.Column("first_seen_at", TS, nullable=False),
    sa.Column("last_seen_at", TS, nullable=False),
    sa.Column("deleted_at", TS, nullable=True),
)

areas = sa.Table(
    "areas",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("area_name", sa.String(), nullable=False),
    sa.Column("unit_type", sa.String(), nullable=False),
    sa.Column("bedrooms", sa.Integer(), nullable=False),
    sa.Column("area_sqm", sa.Numeric(), nullable=False),
    sa.Column("total_units", sa.Integer(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    # Workflow duyệt + nội dung hiển thị (0002/0003) — xem ghi chú ở `projects`.
    sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'active'")),
    sa.Column("headline", sa.String(255), nullable=False, server_default=sa.text("''")),
    sa.Column("introduce", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("cover_image_url", sa.Text(), nullable=True),
    # public_id của Cloudinary — cần để XOÁ ảnh, URL không đủ (xem 0004).
    sa.Column("cover_image_public_id", sa.Text(), nullable=True),
    sa.Column("created_by", UUID, nullable=True),
    sa.Column("reviewed_by", UUID, nullable=True),
    sa.Column("reviewed_at", TS, nullable=True),
    sa.Column("review_reason", sa.Text(), nullable=True),
    # --- Phase D (0017): danh tính nguồn — xem ghi chú ở `projects`.
    sa.Column("external_id", sa.Text(), nullable=True),
    sa.Column("source_system", sa.Text(), nullable=True),
    sa.Column("source_instance_id", sa.Text(), nullable=True),
    sa.Column("source_revision", sa.BigInteger(), nullable=True),
    sa.Column("source_updated_at", TS, nullable=True),
    sa.Column("updated_at", TS, nullable=False),
)

sales_records = sa.Table(
    "sales_records",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("area_id", UUID, nullable=False),
    sa.Column("file_id", UUID, nullable=False),
    sa.Column("sold_date", sa.Date(), nullable=False),
    sa.Column("units_sold", sa.Integer(), nullable=False),
    sa.Column("external_record_id", sa.String(), nullable=False),
    sa.Column("source_row_hash", sa.String(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    # 0005: phiên bản của bản ghi ở hệ nguồn. NULL = nguồn không cho biết.
    sa.Column("source_updated_at", TS, nullable=True),
)

inventory_snapshots = sa.Table(
    "inventory_snapshots",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("area_id", UUID, nullable=False),
    sa.Column("file_id", UUID, nullable=False),
    sa.Column("snapshot_date", sa.Date(), nullable=False),
    sa.Column("units_remaining", sa.Integer(), nullable=False),
    sa.Column("snapshot_type", sa.String(), nullable=False),
    sa.Column("source_row_hash", sa.String(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    # 0005: phiên bản của bản ghi ở hệ nguồn. NULL = nguồn không cho biết.
    sa.Column("source_updated_at", TS, nullable=True),
)

absorption_daily = sa.Table(
    "absorption_daily",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("area_id", UUID, nullable=False),
    sa.Column("stat_date", sa.Date(), nullable=False),
    sa.Column("units_sold", sa.Integer(), nullable=False),
    sa.Column("velocity_7d", sa.Numeric(), nullable=False),
    sa.Column("velocity_30d", sa.Numeric(), nullable=False),
    sa.Column("data_quality_status", sa.String(), nullable=False),
    sa.Column("is_observed", sa.Boolean(), nullable=False),
    sa.Column("computed_at", TS, nullable=False),
    # 0007: NULL = bộ tính sinh ra dòng này không tính được tồn kho theo từng căn.
    sa.Column("units_remaining", sa.Integer(), nullable=True),
    # Nguồn gốc (0012). `units_reserved` NULL = bộ tính sinh ra dòng này không
    # tính được số đó, KHÁC với "bằng 0".
    sa.Column("calculator", sa.Text(), nullable=False),
    sa.Column("units_reserved", sa.Integer(), nullable=True),
    sa.Column("computation_id", UUID, nullable=True),
)

# Tên bảng đích của template (`TableTemplate.target_table`) → đối tượng Table.
TARGET_TABLES = {
    "areas": areas,
    "sales_records": sales_records,
    "inventory_snapshots": inventory_snapshots,
}

# --- Mô hình miền S3 (0007) -------------------------------------------------
# Bản sao MỘT CHIỀU của CRM. Mọi trường nghiệp vụ do CRM sở hữu; ứng dụng chỉ sở
# hữu `deleted_at` và các mốc ghi nhận. Không có `project_id`: dự án suy ra qua
# `areas.project_id`.

units = sa.Table(
    "units",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("source_system", sa.Text(), nullable=False),
    sa.Column("source_instance_id", sa.Text(), nullable=False),
    sa.Column("external_unit_id", sa.Text(), nullable=False),
    sa.Column("area_id", UUID, nullable=False),
    sa.Column("unit_code", sa.Text(), nullable=False),
    sa.Column("unit_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("source_revision", sa.BigInteger(), nullable=True),
    sa.Column("source_updated_at", TS, nullable=True),
    sa.Column("deleted_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

deals = sa.Table(
    "deals",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("source_system", sa.Text(), nullable=False),
    sa.Column("source_instance_id", sa.Text(), nullable=False),
    sa.Column("external_deal_id", sa.Text(), nullable=False),
    sa.Column("unit_id", UUID, nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    # Trạng thái nguyên văn của hệ nguồn, giữ lại vì có ánh xạ alias.
    sa.Column("source_status", sa.Text(), nullable=False),
    sa.Column("reserved_at", TS, nullable=True),
    sa.Column("sold_at", TS, nullable=True),
    sa.Column("lost_at", TS, nullable=True),
    sa.Column("source_revision", sa.BigInteger(), nullable=True),
    sa.Column("source_updated_at", TS, nullable=True),
    sa.Column("deleted_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

# --- Phase 3: nền xác thực và lưu payload thô -------------------------------

# Khoá API máy-với-máy (0008). CHỈ chứa hash — khoá thô không tồn tại ở đâu trong
# hệ thống sau khi được trả về cho người cấp đúng một lần.
sync_credentials = sa.Table(
    "sync_credentials",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("source_system", sa.Text(), nullable=False),
    # Ranh giới cô lập: khoá này chỉ ghi được vào đúng instance này.
    sa.Column("source_instance_id", sa.Text(), nullable=False),
    sa.Column("key_prefix", sa.Text(), nullable=False),
    sa.Column("key_hash", sa.Text(), nullable=False),
    sa.Column("label", sa.Text(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("expires_at", TS, nullable=True),
    sa.Column("revoked_at", TS, nullable=True),
    sa.Column("last_used_at", TS, nullable=True),
)

# Payload thô của mỗi lô (0009). Tách khỏi `upload_files` để đường polling trạng
# thái không kéo theo megabyte JSON — xem docstring migration.
sync_payloads = sa.Table(
    "sync_payloads",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("sync_run_id", UUID, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    # Băm trên BYTE GỐC, không phải trên JSONB đọc ra: JSONB không giữ thứ tự khoá.
    sa.Column("payload_sha256", sa.Text(), nullable=False),
    sa.Column("payload_bytes", sa.Integer(), nullable=False),
    sa.Column("record_count", sa.Integer(), nullable=False),
    sa.Column("content_type", sa.Text(), nullable=True),
    sa.Column("received_at", TS, nullable=False),
    sa.Column("credential_id", UUID, nullable=True),
)


# --- Phase 5: đối soát -------------------------------------------------------

# Mỗi lần chạy đối soát. `passed` được DB ràng buộc phải khớp `error_count = 0`.
reconciliation_runs = sa.Table(
    "reconciliation_runs",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("source_system", sa.Text(), nullable=True),
    sa.Column("source_instance_id", sa.Text(), nullable=True),
    sa.Column("snapshot_id", sa.Text(), nullable=True),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("passed", sa.Boolean(), nullable=False),
    sa.Column("error_count", sa.Integer(), nullable=False),
    sa.Column("warning_count", sa.Integer(), nullable=False),
    sa.Column("info_count", sa.Integer(), nullable=False),
    sa.Column("checks_run", sa.Integer(), nullable=False),
    sa.Column("summary", JSONB, nullable=False),
    sa.Column("started_at", TS, nullable=False),
    sa.Column("finished_at", TS, nullable=True),
)

# Từng phát hiện, dạng máy đọc được. `details` bắt buộc không rỗng khi severity
# là 'warning' — ràng buộc nằm ở DB.
reconciliation_findings = sa.Table(
    "reconciliation_findings",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("reconciliation_run_id", UUID, nullable=False),
    sa.Column("check_code", sa.Text(), nullable=False),
    sa.Column("severity", sa.Text(), nullable=False),
    sa.Column("entity", sa.Text(), nullable=True),
    sa.Column("external_id", sa.Text(), nullable=True),
    sa.Column("area_id", UUID, nullable=True),
    sa.Column("message", sa.Text(), nullable=False),
    sa.Column("details", JSONB, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)


# Lịch sử so sánh hai bộ tính (0013). DỮ LIỆU QUAN SÁT DẪN XUẤT — không con số nào
# ở đây được dùng để tính ra thứ gì khác, và không đường đọc nào của dashboard
# chạm tới nó.
#
# Cột chỉ số để NULL khi KHÔNG CÓ dữ liệu, chứ không để 0: một dự án chưa có
# units/deals sẽ ra 0 ở cả hai bên và trông như "khớp", trong khi thực ra không
# bên nào có gì để nói. `*_has_data` phân biệt hai trạng thái đó, và ràng buộc
# CHECK ở DB giữ chúng không lẫn nhau.
calculator_comparisons = sa.Table(
    "calculator_comparisons",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("compared_at", TS, nullable=False),
    sa.Column("trigger", sa.Text(), nullable=False),
    sa.Column("legacy_units_sold", sa.Integer(), nullable=True),
    sa.Column("legacy_units_remaining", sa.Integer(), nullable=True),
    sa.Column("domain_units_sold", sa.Integer(), nullable=True),
    sa.Column("domain_units_remaining", sa.Integer(), nullable=True),
    sa.Column("domain_units_reserved", sa.Integer(), nullable=True),
    sa.Column("legacy_has_data", sa.Boolean(), nullable=False),
    sa.Column("domain_has_data", sa.Boolean(), nullable=False),
    sa.Column("matches", sa.Boolean(), nullable=False),
    sa.Column("difference_count", sa.Integer(), nullable=False),
    sa.Column("anomaly_count", sa.Integer(), nullable=False),
    sa.Column("differences", JSONB, nullable=False),
    sa.Column("anomalies", JSONB, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)


# --- Tầng dữ liệu xếp hạng (0014, 0015) --------------------------------------
# CHỈ là bản chiếu schema. Phase 2 không có mã tính điểm, không có worker, không
# có API — bốn bảng này tồn tại để Phase 3+ có chỗ ghi vào.

# Giá trị đặc trưng ĐÃ CHUẨN HOÁ về [0,1]. TRẠNG THÁI HIỆN TẠI, không phải lịch
# sử: một giá trị lỗi thời bị ghi đè, không đánh dấu xoá.
#
# `scope_id` là TEXT chứ không phải UUID vì nó phải chứa được hai loại giá trị:
# uuid dạng chuỗi (phạm vi `unit`/`area`) và chuỗi `unit_type` nguyên văn. Cái giá
# là không khoá ngoại nào cưỡng chế được nó.
#
# `project_id` nằm trong khoá danh tính vì phạm vi `unit_type` chỉ là một chuỗi:
# không có nó, `view_quality` của loại '2PN' ở hai dự án khác nhau là CÙNG một dòng.
feature_snapshots = sa.Table(
    "feature_snapshots",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("feature_key", sa.Text(), nullable=False),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("scope_id", sa.Text(), nullable=False),
    sa.Column("feature_value", sa.Numeric(6, 4), nullable=False),
    sa.Column("sample_count", sa.Integer(), nullable=True),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
    sa.Column("source", sa.Text(), nullable=False),
    sa.Column("feature_version", sa.Text(), nullable=False),
    sa.Column("calculated_at", TS, nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

# --- 0033: immutable run-bound ranking evidence -----------------------------
# These tables are additive.  The legacy `feature_snapshots` above remains the
# mutable compatibility cache used by the current ranking implementation.
ranking_feature_definitions = sa.Table(
    "ranking_feature_definitions",
    metadata,
    sa.Column("id", UUID, primary_key=True),
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
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("definition_metadata", JSONB, nullable=False),
    sa.Column("created_by", sa.Text(), nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

ranking_config_features = sa.Table(
    "ranking_config_features",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("ranking_config_id", UUID, nullable=False),
    sa.Column("feature_definition_id", UUID, nullable=False),
    sa.Column("weight", sa.Numeric(12, 8), nullable=False),
    sa.Column("required", sa.Boolean(), nullable=False),
    sa.Column("policy_metadata", JSONB, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

ranking_feature_snapshots = sa.Table(
    "ranking_feature_snapshots",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("ranking_run_id", UUID, nullable=False),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("scope_type", sa.Text(), nullable=False),
    sa.Column("area_id", UUID, nullable=True),
    sa.Column("cutoff_at", TS, nullable=False),
    sa.Column("computed_at", TS, nullable=False),
    sa.Column("feature_set_version", sa.Text(), nullable=False),
    sa.Column("quality_status", sa.Text(), nullable=False),
    sa.Column("quality_summary", JSONB, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

ranking_feature_values = sa.Table(
    "ranking_feature_values",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("snapshot_id", UUID, nullable=False),
    sa.Column("feature_definition_id", UUID, nullable=False),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("scope_type", sa.Text(), nullable=False),
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
    sa.Column("created_at", TS, nullable=False),
    # PR-3 (0039): provenance link back to the CEO-approved value assertion
    # that produced this row. NULL for any future non-governance-sourced
    # feature value (none exist yet — every row PR-3 writes sets this).
    sa.Column("source_justification_id", UUID, nullable=True),
)

ranking_feature_lineage = sa.Table(
    "ranking_feature_lineage",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("feature_value_id", UUID, nullable=False),
    sa.Column("source_relation", sa.Text(), nullable=False),
    sa.Column("source_record_id", sa.Text(), nullable=True),
    sa.Column("source_revision", sa.BigInteger(), nullable=True),
    sa.Column("source_event_at", TS, nullable=True),
    sa.Column("source_locator", sa.Text(), nullable=False),
    sa.Column("source_checksum", sa.Text(), nullable=True),
    sa.Column("created_at", TS, nullable=False),
)

ranking_explanations = sa.Table(
    "ranking_explanations",
    metadata,
    sa.Column("id", UUID, primary_key=True),
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
    sa.Column("created_at", TS, nullable=False),
)

# Trọng số xếp hạng, có version. CHỈ-THÊM: `weights` của một dòng đã `published`
# không bao giờ được UPDATE — sửa tại chỗ khiến mọi `ranking_scores` cũ trỏ tới
# một config đã đổi nghĩa. Rollback là chép sang một version MỚI.
#
# Đúng MỘT dòng `published`, cưỡng chế bằng partial unique index ở 0014.
ranking_configs = sa.Table(
    "ranking_configs",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
    sa.Column("weights", JSONB, nullable=False),
    sa.Column("min_weight_coverage", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0.5")),
    sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("copied_from_version", sa.Integer(), nullable=True),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("published_by", sa.Text(), nullable=True),
    sa.Column("published_at", TS, nullable=True),
    sa.Column("archived_at", TS, nullable=True),
    # D41/S10 (0037): nested per-grain hierarchical composition config, read
    # ONLY by the additive post-run hierarchical step. NULL = hierarchical
    # scoring not configured for this config version — `weights` above (the
    # legacy flat feature-weight map `_active_config()`/`validate_weights()`
    # read) is untouched and remains the exclusive legacy unit-ranking input.
    sa.Column("hierarchical_weights", JSONB, nullable=True),
)

# Vòng đời một lần xếp hạng. CHỈ-THÊM, giữ mãi — đây là lịch sử vận hành.
#
# `scope_ids` CHỈ để kiểm toán ("lô nào gây ra lần chạy này"). Công việc luôn ở
# phạm vi TOÀN DỰ ÁN, vì `rank_in_project` dịch chuyển khi bất kỳ căn nào đổi điểm.
ranking_runs = sa.Table(
    "ranking_runs",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("sync_run_id", UUID, nullable=True),
    sa.Column("trigger", sa.Text(), nullable=False),
    sa.Column("scope_type", sa.Text(), nullable=False, server_default=sa.text("'project'")),
    sa.Column("scope_ids", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("config_version_id", UUID, nullable=True),
    sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
    sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("units_processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("units_ranked", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("units_skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("error_summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("enqueued_at", TS, nullable=False),
    sa.Column("started_at", TS, nullable=True),
    sa.Column("finished_at", TS, nullable=True),
)

# Điểm + thứ hạng hiện hành. TRẠNG THÁI HIỆN TẠI: đúng một dòng mỗi căn, ghi lại
# bằng xoá-rồi-chèn theo phạm vi dự án (tầng ứng dụng, Phase 3+).
#
# Xoá-rồi-chèn chứ không upsert: một căn vừa bị tombstone hoặc rơi dưới ngưỡng
# phủ trọng số phải BIẾN MẤT, không được để lại một dòng ma mang thứ hạng cũ.
ranking_scores = sa.Table(
    "ranking_scores",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("unit_id", UUID, nullable=False),
    sa.Column("area_id", UUID, nullable=False),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("ranking_run_id", UUID, nullable=False),
    sa.Column("config_version_id", UUID, nullable=False),
    sa.Column("score", sa.Numeric(6, 4), nullable=False),
    sa.Column("rank_in_area", sa.Integer(), nullable=False),
    sa.Column("rank_in_project", sa.Integer(), nullable=False),
    sa.Column("weight_coverage", sa.Numeric(5, 4), nullable=False),
    sa.Column("contributions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("feature_freshness_at", TS, nullable=True),
    sa.Column("computed_at", TS, nullable=False),
    # D29/S8 + D37/S9 (0037): the hierarchical (M/P/A/U) output, additive and
    # parallel to `score`/`contributions` above — never read or written by
    # the legacy per-unit scoring path. NULL = not yet hierarchically scored,
    # or HIGH_RISK legal-gated (§24.4.5) — never a zero/defaulted value.
    sa.Column("hierarchical_score", sa.Numeric(6, 4), nullable=True),
    sa.Column("hierarchical_contributions", JSONB, nullable=True),
)

# --- Phase 6 (0018): đề xuất tư vấn của AI Agent, chờ duyệt --------------------
#
# `AGENTS.md` là yêu cầu cứng: "Every recommendation this agent produces must
# pass through a human-in-the-loop approval step before it is treated as final."
# Bảng này LÀ bước duyệt đó — status khởi tạo LUÔN 'pending_approval', không có
# đường nào set thẳng 'approved' ngoài `POST /agent/recommendations/{id}/approve`.
#
# `ranking_run_id` trỏ về đúng lần xếp hạng đã sinh ra đề xuất — bằng chứng có
# thể truy lại, không phải một đoạn text LLM đứng một mình.
agent_recommendations = sa.Table(
    "agent_recommendations",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("area_id", UUID, nullable=True),
    sa.Column("ranking_run_id", UUID, nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending_approval'")),
    sa.Column("summary", sa.Text(), nullable=False),
    sa.Column("recommended_actions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("model", sa.Text(), nullable=True),
    sa.Column("decided_by", sa.Text(), nullable=True),
    sa.Column("decided_at", TS, nullable=True),
    sa.Column("decision_reason", sa.Text(), nullable=True),
    sa.Column("generated_at", TS, nullable=False),
    sa.Column("action_type", sa.Text(), nullable=True),
    # 0020: structured action data consumed only by an allow-listed executor.
    sa.Column("action_payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("risk_level", sa.Text(), nullable=False, server_default=sa.text("'low'")),
    sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
    sa.Column("execution_status", sa.Text(), nullable=False, server_default=sa.text("'not_started'")),
    sa.Column("executed_by", sa.Text(), nullable=True),
    sa.Column("executed_at", TS, nullable=True),
    sa.Column("execution_result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
)


# Application-owned execution results. Never use these tables to mutate
# units/deals: those entities are owned by Mini CRM.
sales_campaigns = sa.Table(
    "sales_campaigns",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("recommendation_id", UUID, nullable=False),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("area_id", UUID, nullable=True),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

sales_campaign_units = sa.Table(
    "sales_campaign_units",
    metadata,
    sa.Column("campaign_id", UUID, primary_key=True),
    sa.Column("unit_id", UUID, primary_key=True),
    sa.Column("priority", sa.Integer(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
)

agent_executions = sa.Table(
    "agent_executions",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("recommendation_id", UUID, nullable=False),
    sa.Column("action_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("actor", sa.Text(), nullable=False),
    sa.Column("result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("error", sa.Text(), nullable=True),
    sa.Column("started_at", TS, nullable=False),
    sa.Column("finished_at", TS, nullable=True),
)

# --- 0034: expert-governed ranking configuration ----------------------------
expert_profiles = sa.Table(
    "expert_profiles",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("user_id", UUID, nullable=True),
    sa.Column("identity_subject", sa.Text(), nullable=False),
    sa.Column("organization", sa.Text(), nullable=True),
    sa.Column("title", sa.Text(), nullable=True),
    sa.Column("expertise_summary", sa.Text(), nullable=True),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
)

ranking_weight_proposals = sa.Table(
    "ranking_weight_proposals",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("base_config_id", UUID, nullable=True),
    sa.Column("proposed_config_id", UUID, nullable=True),
    sa.Column("scope_type", sa.Text(), nullable=False),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("area_id", UUID, nullable=True),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("created_by_expert_id", UUID, nullable=False),
    sa.Column("submitted_at", TS, nullable=True),
    sa.Column("approved_at", TS, nullable=True),
    sa.Column("published_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
    # PR-2/D38 (0038): 'weight' (default, existing rows) | 'value' (new).
    sa.Column("assertion_kind", sa.Text(), nullable=False),
)

ranking_feature_justifications = sa.Table(
    "ranking_feature_justifications",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("proposal_id", UUID, nullable=False),
    sa.Column("feature_definition_id", UUID, nullable=False),
    sa.Column("previous_weight", sa.Numeric(12, 8), nullable=True),
    sa.Column("proposed_weight", sa.Numeric(12, 8), nullable=True),
    sa.Column("rationale", sa.Text(), nullable=False),
    sa.Column("methodology", sa.Text(), nullable=False),
    sa.Column("evidence_summary", sa.Text(), nullable=False),
    sa.Column("expected_effect", sa.Text(), nullable=False),
    sa.Column("confidence", sa.Text(), nullable=False),
    sa.Column("limitations", sa.Text(), nullable=False),
    sa.Column("created_by_expert_id", UUID, nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
    # PR-2/D38 (0038) — value-mode assertion payload, XOR with proposed_weight
    # above (`ck_rfj_assertion_mode_xor`). NULL for every weight-mode row.
    sa.Column("assertion_kind", sa.Text(), nullable=False),
    sa.Column("raw_numeric", sa.Numeric(24, 10), nullable=True),
    sa.Column("normalized_numeric", sa.Numeric(12, 8), nullable=True),
    sa.Column("categorical_value", sa.Text(), nullable=True),
    sa.Column("effective_at", TS, nullable=True),
    sa.Column("expires_at", TS, nullable=True),
    sa.Column("external_source_citation", sa.Text(), nullable=True),
    sa.Column("author_subject", sa.Text(), nullable=True),
)

ranking_evidence_documents = sa.Table(
    "ranking_evidence_documents",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("proposal_id", UUID, nullable=True),
    sa.Column("uploaded_by_expert_id", UUID, nullable=False),
    sa.Column("original_filename", sa.Text(), nullable=False),
    sa.Column("mime_type", sa.Text(), nullable=False),
    sa.Column("object_storage_key", sa.Text(), nullable=False),
    sa.Column("sha256_checksum", sa.Text(), nullable=False),
    sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
    sa.Column("extraction_status", sa.Text(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

ranking_evidence_document_features = sa.Table(
    "ranking_evidence_document_features",
    metadata,
    sa.Column("document_id", UUID, primary_key=True),
    sa.Column("feature_justification_id", UUID, primary_key=True),
)

ranking_evidence_document_chunks = sa.Table(
    "ranking_evidence_document_chunks",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("document_id", UUID, nullable=False),
    sa.Column("chunk_index", sa.Integer(), nullable=False),
    sa.Column("page_number", sa.Integer(), nullable=True),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("token_count", sa.Integer(), nullable=False),
    sa.Column("embedding_model", sa.Text(), nullable=False),
    sa.Column("embedding", Vector(1536), nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

ranking_evidence_extraction_attempts = sa.Table(
    "ranking_evidence_extraction_attempts",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("document_id", UUID, nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("error_summary", sa.Text(), nullable=True),
    sa.Column("created_at", TS, nullable=False),
)

ranking_proposal_reviews = sa.Table(
    "ranking_proposal_reviews",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("proposal_id", UUID, nullable=False),
    sa.Column("reviewer_expert_id", UUID, nullable=False),
    sa.Column("decision", sa.Text(), nullable=False),
    sa.Column("comment", sa.Text(), nullable=False),
    sa.Column("decided_at", TS, nullable=False),
    # PR-2/D38 (0038) — server-derived reviewer identity, value-mode reviews
    # only. NULL for every weight-mode review (unchanged behavior).
    sa.Column("reviewer_subject", sa.Text(), nullable=True),
    sa.Column("reviewer_is_ceo", sa.Boolean(), nullable=True),
)

ranking_config_audit_events = sa.Table(
    "ranking_config_audit_events",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("ranking_config_id", UUID, nullable=True),
    sa.Column("proposal_id", UUID, nullable=True),
    sa.Column("actor_expert_id", UUID, nullable=True),
    sa.Column("actor_identity_subject", sa.Text(), nullable=False),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("before_status", sa.Text(), nullable=True),
    sa.Column("after_status", sa.Text(), nullable=True),
    sa.Column("before_state", JSONB, nullable=False),
    sa.Column("after_state", JSONB, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)


# --- 0027: quan trắc giá niêm yết -------------------------------------------
#
# Đường vào THỨ HAI cho giá, tách khỏi luồng đồng bộ CRM. Hợp đồng
# `src/contracts/crm_sync_v2.schema.json` CẤM trường giá (`additionalProperties:
# false`, có test ở hệ nguồn cưỡng chế), nên `units` không bao giờ mang giá và
# bảng này không bao giờ được ghi bởi `DomainProjector`.
#
# `official_price` là giá NIÊM YẾT, không phải giá giao dịch thực.
# `effective_to IS NULL` = giá đang áp dụng; partial unique index
# `ix_price_obs_unit_current` cưỡng chế đúng một dòng như thế mỗi căn.
project_price_observations = sa.Table(
    "project_price_observations",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("unit_id", UUID, nullable=False),
    sa.Column("official_price", sa.Numeric(18, 2), nullable=False),
    sa.Column("effective_from", sa.Date(), nullable=False),
    sa.Column("effective_to", sa.Date(), nullable=True),
    sa.Column("source", sa.String(50), nullable=False, server_default=sa.text("'manual'")),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
)

# --- Nhật ký status append-only (0028/0029/0030) ----------------------------
# Ghi bởi trigger DB (`units_emit_status_history`/`deals_emit_status_history`),
# KHÔNG bởi tầng ứng dụng — khai ở đây chỉ để đọc, không phải để INSERT thủ
# công. UPDATE/DELETE bị chặn ở DB (REVOKE + trigger bảo vệ), không lặp lại ở
# Core vì SQLAlchemy không thực thi được ràng buộc phía server.

unit_status_history = sa.Table(
    "unit_status_history",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("unit_id", UUID, nullable=False),
    sa.Column("deal_id", UUID, nullable=True),
    sa.Column("old_status", sa.Text(), nullable=True),
    sa.Column("new_status", sa.Text(), nullable=False),
    sa.Column("changed_at", TS, nullable=False),
    sa.Column("recorded_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("source", sa.Text(), nullable=False),
    sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
)

deal_status_history = sa.Table(
    "deal_status_history",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("deal_id", UUID, nullable=False),
    sa.Column("unit_id", UUID, nullable=False),
    sa.Column("old_status", sa.Text(), nullable=True),
    sa.Column("new_status", sa.Text(), nullable=False),
    sa.Column("prior_status_was_holding", sa.Boolean(), nullable=False),
    sa.Column("new_status_is_holding", sa.Boolean(), nullable=False),
    sa.Column("changed_at", TS, nullable=False),
    sa.Column("recorded_at", TS, nullable=False, server_default=sa.func.now()),
    sa.Column("source", sa.Text(), nullable=False),
    sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
)
