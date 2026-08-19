"""nạp CSV lặp lại được: bỏ UNIQUE checksum, thêm source_updated_at

Revision ID: 0005_idempotent_csv_ingestion
Revises: 0004_cover_image_public_id
Create Date: 2026-08-08

Ba thay đổi, cùng phục vụ một mục tiêu: nạp lại cùng một file phải là thao tác
KHÔNG-LÀM-GÌ, không phải một lỗi và cũng không phải một lô hỏng.

1. **Bỏ `uq_upload_files_project_checksum`.** Ràng buộc này lấy BYTE của file làm
   danh tính của lô. Hệ quả: gửi lại đúng file đó (retry mạng, chạy lại job) bị
   trả 409 thay vì được bỏ qua êm; và khi một lô hỏng giữa chừng, bản ghi hỏng
   vẫn giữ chỗ checksum nên file đã sửa KHÔNG nạp lại được nữa. Chống trùng dữ
   liệu vốn đã nằm ở khoá nghiệp vụ của từng bảng đích
   (`uq_sales_area_date_external_id`, `uq_inventory_area_date_type`), là chỗ đúng
   để nó nằm. Thay bằng index THƯỜNG để tra cứu "file này đã nạp chưa" vẫn nhanh.

2/3. **`source_updated_at` cho `sales_records` và `inventory_snapshots`.** Không
   có cột này thì không có cách nào phân biệt "bản ghi đến sau" với "bản ghi mới
   hơn", nên mọi lần nạp lại sẽ hoặc ghi đè mù quáng hoặc bị chặn hoàn toàn. Cột
   NULL được: dữ liệu nạp trước bản migration này không có phiên bản, và bịa ra
   một mốc thời gian mặc định là nói dối về nguồn — quy tắc so sánh ở
   `ImportService` xử lý NULL tường minh thay vì dựa vào default.

Không xoá dữ liệu: cả ba thao tác đều bảo toàn mọi dòng đang có.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_idempotent_csv_ingestion"
down_revision: str | None = "0004_cover_image_public_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.TIMESTAMP(timezone=True)

# Bảng đích nhận cột phiên bản. `areas` KHÔNG có: danh mục phân khu không đến
# theo lô có phiên bản, nạp lại chỉ cần bỏ qua (xem AREAS_TEMPLATE).
VERSIONED_TABLES = ("sales_records", "inventory_snapshots")


def upgrade() -> None:
    op.drop_constraint("uq_upload_files_project_checksum", "upload_files", type_="unique")
    op.create_index("ix_upload_files_project_id_checksum", "upload_files", ["project_id", "checksum"])

    for table in VERSIONED_TABLES:
        op.add_column(table, sa.Column("source_updated_at", TS, nullable=True))


def downgrade() -> None:
    for table in VERSIONED_TABLES:
        op.drop_column(table, "source_updated_at")

    op.drop_index("ix_upload_files_project_id_checksum", table_name="upload_files")
    # Đi lùi được chỉ khi dữ liệu hiện tại còn thoả ràng buộc cũ. Sau khi nạp lại
    # nhiều lần trên cùng một dự án, hai dòng có thể trùng (project_id, checksum)
    # và lệnh này sẽ vỡ — đúng như mong đợi: ràng buộc cũ không còn mô tả được dữ
    # liệu mới, phải dọn trùng trước khi lùi.
    op.create_unique_constraint(
        "uq_upload_files_project_checksum",
        "upload_files",
        ["project_id", "checksum"],
    )
