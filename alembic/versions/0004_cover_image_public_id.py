"""cover_image_public_id cho projects và areas

Revision ID: 0004_cover_image_public_id
Revises: 0003_content_column_defaults
Create Date: 2026-08-06

`cover_image_url` (0002) đủ để HIỂN THỊ ảnh nhưng không đủ để XOÁ nó: API xoá của
Cloudinary nhận `public_id`, không nhận URL. Suy ngược public_id từ URL là cách
làm dễ vỡ — URL còn kèm version, transformation, đuôi file, và đổi theo cấu hình
delivery. Lưu thẳng public_id là đường duy nhất chắc chắn dọn được file, tránh
để lại ảnh mồ côi trên Cloudinary khi bản ghi DB đã bị xoá.

Nullable: bản ghi chưa có ảnh thì cả hai cột đều NULL. Hai cột luôn được ghi và
xoá cùng nhau trong một transaction (xem `src/services/images.py`).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_cover_image_public_id"
down_revision: str | None = "0003_content_column_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("projects", "areas")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("cover_image_public_id", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "cover_image_public_id")
