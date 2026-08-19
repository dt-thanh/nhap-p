"""Backend hierarchy projection: danh tính nguồn cho projects/areas, project_id NULLABLE

Revision ID: 0017_hierarchy_projection
Revises: 0016_completed_with_conflicts
Create Date: 2026-08-13

Phase D bật v2: Mini CRM là NGUỒN SỰ THẬT cho Project/Area (đã đóng băng ở Phase A,
hiện thực ở Mini CRM tại Phase B/C). Backend chỉ SOI GƯƠNG. Migration này KHÔNG tạo
bảng mới — `projects`/`areas` đã tồn tại từ 0001; nó chỉ thêm cột DANH TÍNH NGUỒN,
đúng khuôn đã dùng cho `units`/`deals` ở 0007 (`source_system`, `source_instance_id`,
`source_revision`, `source_updated_at`, cộng một ràng buộc UNIQUE danh tính).

═══════════════════════════════════════════════════════════════════════════════
 BA QUYẾT ĐỊNH, VÀ VÌ SAO
═══════════════════════════════════════════════════════════════════════════════

**1. `external_id`/`source_*` NULLABLE, không NOT NULL.** `projects`/`areas` ĐANG
CÓ dữ liệu thật, tạo bằng `ProjectService.create_project`/`create_area` (backend
sở hữu, trước Phase D) — những dòng này không có danh tính nguồn nào để điền, và
Phase A cấm bịa (`docs/crm/phase_a_domain_freeze.md` §A0). Chúng ở lại với
`external_id = NULL`: DI SẢN, vẫn đọc được, không còn ai TẠO MỚI kiểu này nữa sau
Phase D (§D7 — `src/services/projects.py` bị thu hẹp trong cùng đợt này).

**2. `uq_projects_source_identity`/`uq_areas_source_identity` là UNIQUE hai cột
`(source_instance_id, external_id)`, KHÔNG phải partial index.** PostgreSQL coi
NULL luôn KHÁC NULL trong một ràng buộc UNIQUE, nên nhiều dòng di sản cùng mang
`external_id = NULL` không hề vi phạm — không cần cú pháp `WHERE external_id IS
NOT NULL` để đạt cùng hiệu quả. Đây là NGUYÊN XI cách `uq_units_source_identity`
(0007) đã hoạt động.

**3. `upload_files.project_id` chuyển thành NULLABLE.** Một lô `POST /sync/projects`
TẠO dự án — chưa có UUID nào cho nó tới khi lô được xử lý xong. Bắt buộc NOT NULL
sẽ tạo ra vòng luẩn quẩn "cần UUID dự án để ghi lô, cần lô để tạo dự án". Chỉ
đúng MỘT loại lô (source_entity='projects') thực sự dùng tới NULL này —
`SyncRunService` vẫn đòi `project_id` NOT NULL ở TẦNG ỨNG DỤNG cho ba loại lô còn
lại (areas/units/deals), y hệt hành vi trước Phase D.

`updated_at` được thêm cho cả hai bảng (chưa từng có) — cùng lý do và cùng khuôn
với `units.updated_at`/`deals.updated_at`: mốc ghi nhận CUỐI CÙNG của phía nhận,
không phải mốc sự kiện ở hệ nguồn (đó là việc của `source_updated_at`).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_hierarchy_projection"
down_revision: str | None = "0016_completed_with_conflicts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.TIMESTAMP(timezone=True)

HIERARCHY_TABLES = ("projects", "areas")


def upgrade() -> None:
    for table in HIERARCHY_TABLES:
        op.add_column(table, sa.Column("external_id", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("source_system", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("source_instance_id", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("source_revision", sa.BigInteger(), nullable=True))
        op.add_column(table, sa.Column("source_updated_at", TS, nullable=True))
        op.add_column(
            table,
            sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
        )
        op.create_unique_constraint(
            f"uq_{table}_source_identity", table, ["source_instance_id", "external_id"]
        )
        # Cùng CHECK "danh sách MỞ" đã dùng cho `crm_source_records.source_entity`
        # (0006) — không NOT NULL vì di sản không có source_system, nhưng dòng NÀO
        # CÓ thì không được là chuỗi rỗng.
        op.create_check_constraint(
            f"ck_{table}_external_id_not_blank", table, "external_id IS NULL OR external_id <> ''"
        )
        op.create_check_constraint(
            f"ck_{table}_source_system_not_blank", table, "source_system IS NULL OR source_system <> ''"
        )
        op.create_check_constraint(
            f"ck_{table}_source_instance_id_not_blank",
            table,
            "source_instance_id IS NULL OR source_instance_id <> ''",
        )

    op.create_index("ix_areas_project_id", "areas", ["project_id"])

    # `upload_files.project_id` NULLABLE — xem mục 3 ở docstring.
    op.alter_column("upload_files", "project_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("upload_files", "project_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_index("ix_areas_project_id", table_name="areas")

    for table in HIERARCHY_TABLES:
        op.drop_constraint(f"ck_{table}_source_instance_id_not_blank", table, type_="check")
        op.drop_constraint(f"ck_{table}_source_system_not_blank", table, type_="check")
        op.drop_constraint(f"ck_{table}_external_id_not_blank", table, type_="check")
        op.drop_constraint(f"uq_{table}_source_identity", table, type_="unique")
        op.drop_column(table, "updated_at")
        op.drop_column(table, "source_updated_at")
        op.drop_column(table, "source_revision")
        op.drop_column(table, "source_instance_id")
        op.drop_column(table, "source_system")
        op.drop_column(table, "external_id")
