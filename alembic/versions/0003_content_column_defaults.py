"""default rỗng cho headline/introduce của projects và areas

Revision ID: 0003_content_column_defaults
Revises: 0002_project_area_approval
Create Date: 2026-08-06

0002 thêm `headline` và `introduce` là NOT NULL nhưng KHÔNG có server_default.
Hệ quả: mọi câu INSERT không liệt kê hai cột đó đều vỡ với NotNullViolation —
gồm cả `ImportService` khi nạp template `areas` và toàn bộ fixture test đang tạo
project/phân khu. Đo được 42 lỗi + 2 test hỏng trước khi có revision này.

Cách sửa là thêm DEFAULT '' chứ không sửa 0002, vì 0002 đã chạy trên database
dev: sửa một migration đã áp dụng thì DB cũ và DB dựng mới sẽ trôi khỏi nhau.

Chuỗi rỗng là giá trị trung tính nhất — hai cột này là nội dung hiển thị, chưa
có ràng buộc nào cấm để trống. Nếu về sau nghiệp vụ bắt buộc phải có nội dung
thì thêm CHECK <> '' trong một revision riêng, kèm bước điền dữ liệu cho các
dòng cũ.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_content_column_defaults"
down_revision: str | None = "0002_project_area_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("projects", "areas")
COLUMNS = ("headline", "introduce")


def upgrade() -> None:
    for table in TABLES:
        for column in COLUMNS:
            op.alter_column(table, column, server_default=sa.text("''"))


def downgrade() -> None:
    for table in TABLES:
        for column in COLUMNS:
            op.alter_column(table, column, server_default=None)
