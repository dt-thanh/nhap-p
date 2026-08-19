"""project/area approval workflow — thêm cột duyệt cho projects và areas

Revision ID: 0002_project_area_approval
Revises: 0001_initial_schema
Create Date: 2026-08-06

Vì sao là revision MỚI chứ không sửa 0001: 0001 ĐÃ chạy trên database dev
(alembic_version = '0001_initial_schema'). Thêm cột vào một migration đã áp dụng
thì database cũ không bao giờ nhận được cột đó, còn database dựng mới lại có —
hai bên trôi khỏi nhau một cách âm thầm, đúng thứ Alembic sinh ra để tránh.

`server_default` là bắt buộc chứ không phải cho tiện: ADD COLUMN ... NOT NULL mà
không có default sẽ vỡ ngay trên bảng đã có dữ liệu. Có default thì PostgreSQL
điền 'active' cho mọi dòng sẵn có, nên dự án/phân khu đang chạy KHÔNG bị đẩy về
'pending' và mất quyền hiển thị.

Khoá ngoại tới `users` đặt được ở đây vì `users` đã tồn tại từ 0001. Trong 0001
thì không: `projects` được tạo TRƯỚC `users`, khai FK inline ở đó sẽ vỡ.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_project_area_approval"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.dialects.postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

# Cùng một tập trạng thái cho cả hai bảng — khai một chỗ để hai bên không lệch.
STATUS_VALUES = "('pending', 'active', 'rejected', 'archived')"

WORKFLOW_TABLES = ("projects", "areas")


def upgrade() -> None:
    for table in WORKFLOW_TABLES:
        op.add_column(
            table,
            sa.Column(
                "status",
                sa.String(),
                nullable=False,
                # Dòng đã có sẵn được điền 'active', không rơi về 'pending'.
                server_default=sa.text("'active'"),
            ),
        )
        op.add_column(table, sa.Column("headline", sa.String(255), nullable=False))
        op.add_column(table, sa.Column("introduce", sa.Text(), nullable=False))
        op.add_column(table, sa.Column("cover_image_url", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("created_by", UUID, nullable=True))
        op.add_column(table, sa.Column("reviewed_by", UUID, nullable=True))
        op.add_column(table, sa.Column("reviewed_at", TS, nullable=True))
        op.add_column(table, sa.Column("review_reason", sa.Text(), nullable=True))

        op.create_check_constraint(f"ck_{table}_status", table, f"status IN {STATUS_VALUES}")

        # `users` đã có từ 0001 nên đặt FK ở đây là an toàn.
        op.create_foreign_key(f"fk_{table}_created_by", table, "users", ["created_by"], ["id"])
        op.create_foreign_key(f"fk_{table}_reviewed_by", table, "users", ["reviewed_by"], ["id"])


def downgrade() -> None:
    for table in WORKFLOW_TABLES:
        op.drop_constraint(
            f"fk_{table}_reviewed_by",
            table,
            type_="foreignkey",
        )
        op.drop_constraint(
            f"fk_{table}_created_by",
            table,
            type_="foreignkey",
        )
        op.drop_constraint(
            f"ck_{table}_status",
            table,
            type_="check",
        )

        op.drop_column(table, "review_reason")
        op.drop_column(table, "reviewed_at")
        op.drop_column(table, "reviewed_by")
        op.drop_column(table, "created_by")
        op.drop_column(table, "cover_image_url")
        op.drop_column(table, "introduce")
        op.drop_column(table, "headline")
        op.drop_column(table, "status")
