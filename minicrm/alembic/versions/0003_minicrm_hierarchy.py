"""Phân cấp Dự án/Phân khu: crm_projects, crm_areas, và crm_units.area_id

Revision ID: 0003_minicrm_hierarchy
Revises: 0002_minicrm_crud
Create Date: 2026-08-13

Phase B thực thi đảo ngược sở hữu đã đóng băng ở Phase A (đợt (g)):
`docs/crm/phase_a_domain_freeze.md`. Mini CRM trở thành TÁC GIẢ của Project và
Area, không chỉ Unit và Deal. KHÔNG có quy trình đề xuất/duyệt — cả năm trường
của Area (kể cả ba trường kế hoạch `bedrooms`/`area_sqm`/`total_units`) là BẮT
BUỘC và CÓ THẨM QUYỀN ngay từ dòng đầu tiên.

═══════════════════════════════════════════════════════════════════════════════
 BỐN QUYẾT ĐỊNH THIẾT KẾ, VÀ VÌ SAO
═══════════════════════════════════════════════════════════════════════════════

**1. `crm_units.area_id` NULLABLE, không NOT NULL.** Database Mini CRM ĐANG CHẠY
mang dữ liệu thật (163 căn tại thời điểm viết migration này), tạo TRƯỚC khi
`crm_areas` tồn tại. Chúng không có phân khu cục bộ nào để trỏ vào, và Phase A
cấm suy luận số liệu kế hoạch (`total_units` v.v.) bằng cách đếm hoặc bịa —
backfill một `crm_areas` giả cho chúng sẽ vi phạm đúng nguyên tắc đó. Vì vậy 163
dòng này ở lại với `area_id = NULL`: DI SẢN, không bị đụng tới, vẫn đọc/sửa được
bình thường (trừ việc chuyển sang phân khu khác — không có "phân khu hiện tại"
nào để so sánh "cùng dự án"). Căn tạo MỚI từ Phase B trở đi LUÔN có `area_id`
(cưỡng chế ở tầng ứng dụng, `app/crud.py`, không phải CHECK — CHECK không phân
biệt được "tạo trước Phase B" với "tạo sau mà quên gắn phân khu").

**2. `crm_units.area_name`/`unit_type` được GIỮ NGUYÊN, không đổi tên, không đổi
kiểu.** Hai lý do cộng dồn: (a) hình dạng phong bì v1 gửi lên backend
(`area_ref: {area_name, unit_type}`) là BẤT BIẾN — không được sửa ở Phase B; giữ
hai cột này nguyên trạng nghĩa là `app/crud.py::_unit_record()` không phải viết
lại. (b) 163 dòng di sản không có gì khác để mô tả phân khu của chúng — xoá hai
cột này sẽ xoá luôn thông tin duy nhất còn lại về chúng. Với căn có `area_id`,
ứng dụng LÀM TƯƠI hai cột này từ `crm_areas` ở mọi lần ghi (snapshot-tại-thời-
điểm-ghi, cùng triết lý với "phong bì dựng từ dòng đã ghi" đã có trong
`app/crud.py`) — nên chúng không bao giờ trôi xa khỏi Area quá một lần ghi.

**3. `uq_crm_units_live_code` (cũ, trên `area_name, unit_type, unit_code`) GIỮ
NGUYÊN, KHÔNG thay bằng ràng buộc trên `area_id`.** Đổi sang `area_id` sẽ khiến
163 dòng `area_id IS NULL` cùng rơi vào một "phân khu NULL" và có thể trùng mã
căn với nhau theo ràng buộc mới — chặt hơn ý nghĩa cũ một cách không chủ đích.
Giữ ràng buộc cũ nguyên vẹn là lựa chọn AN TOÀN HƠN, không phải lựa chọn ĐÚNG
HOÀN HẢO — vì với căn CÓ `area_id`, hai cột `area_name`/`unit_type` bây giờ là
BẢN SAO (không phải nguồn sự thật), nên về lý thuyết ràng buộc "đúng" phải nằm
trên `(area_id, unit_code)`. Đây là NỢ KỸ THUẬT được ghi lại tường minh, không
phải bị bỏ sót — xem `docs/crm/phase_a_domain_freeze.md` mục nợ kỹ thuật Phase B
(nếu chưa có, xem `pipeline_status.md` đợt Phase B) và cân nhắc dọn lại khi
`area_id` đã NOT NULL cho mọi dòng (sau một đợt di trú dữ liệu di sản).

**4. `crm_projects`/`crm_areas` mang sẵn ba cột `mirrored_*`, dù Phase B KHÔNG
tạo dòng outbox nào cho chúng.** Cùng khuôn với `crm_units`/`crm_deals`
(migration 0002), để Phase C (nối outbox cho phân cấp) không cần thêm một
migration riêng chỉ để có ba cột này. Chúng NULL cho tới khi Phase C bắt đầu ghi
vào chúng — không có ý nghĩa nào ở Phase B.

═══════════════════════════════════════════════════════════════════════════════

KHÔNG đổi `crm_deals` — Deal không có tham chiếu Project/Area riêng (Phase A
§A3.5: suy qua Unit, không lặp tham chiếu). KHÔNG đổi cây Alembic của BACKEND —
đầu cây bên đó giữ nguyên, không migration nào ở đây chạm tới database của
backend (hai cây tách biệt hoàn toàn, `minicrm/alembic/env.py` không
`import src.config`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_minicrm_hierarchy"
down_revision: str | None = "0002_minicrm_crud"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

# Trạng thái sống/lưu trữ của Project và Area. KHÔNG có 'pending'/'rejected':
# không có quy trình duyệt nào ở Mini CRM cho dữ liệu của chính nó — khác hẳn
# `ck_projects_status`/`ck_areas_status` của BACKEND (migration 0002 của backend),
# vốn có bốn giá trị cho một quy trình duyệt đã bị BỎ ở mô hình sở hữu mới
# (xem phase_a_domain_freeze.md §S-2). Hai bảng KHÁC NHAU, hai vòng đời KHÁC NHAU.
LIFECYCLE_STATUSES = ("active", "archived")

PROJECT_SEQUENCE = "crm_project_external_seq"
AREA_SEQUENCE = "crm_area_external_seq"


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- 1. Dãy sinh external_id, cùng khuôn với 0002 -----------------------
    op.execute(f"CREATE SEQUENCE {PROJECT_SEQUENCE} AS BIGINT START WITH 1 INCREMENT BY 1 NO CYCLE")
    op.execute(f"CREATE SEQUENCE {AREA_SEQUENCE} AS BIGINT START WITH 1 INCREMENT BY 1 NO CYCLE")

    # --- 2. crm_projects ------------------------------------------------------
    op.create_table(
        "crm_projects",
        sa.Column("id", UUID, nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("launch_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("source_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("mirrored_at", TS, nullable=True),
        sa.Column("mirrored_revision", sa.BigInteger(), nullable=True),
        sa.Column("last_sync_batch_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crm_projects"),
        sa.UniqueConstraint("external_id", name="uq_crm_projects_external_id"),
        sa.CheckConstraint("external_id <> ''", name="ck_crm_projects_external_id_not_blank"),
        sa.CheckConstraint("name <> ''", name="ck_crm_projects_name_not_blank"),
        sa.CheckConstraint(_in_list("status", LIFECYCLE_STATUSES), name="ck_crm_projects_status"),
        sa.CheckConstraint("source_revision > 0", name="ck_crm_projects_revision_positive"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_crm_projects_updated_after_created"),
        sa.CheckConstraint(
            "mirrored_revision IS NULL OR mirrored_revision > 0",
            name="ck_crm_projects_mirrored_revision_positive",
        ),
        sa.CheckConstraint(
            "(mirrored_at IS NULL) = (mirrored_revision IS NULL)",
            name="ck_crm_projects_mirrored_pair",
        ),
    )

    # --- 3. crm_areas -----------------------------------------------------
    op.create_table(
        "crm_areas",
        sa.Column("id", UUID, nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("area_name", sa.Text(), nullable=False),
        sa.Column("unit_type", sa.Text(), nullable=False),
        sa.Column("bedrooms", sa.Integer(), nullable=False),
        sa.Column("area_sqm", sa.Numeric(), nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("source_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("mirrored_at", TS, nullable=True),
        sa.Column("mirrored_revision", sa.BigInteger(), nullable=True),
        sa.Column("last_sync_batch_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_crm_areas"),
        sa.UniqueConstraint("external_id", name="uq_crm_areas_external_id"),
        # Soi gương `uq_areas_project_name_unit_type` của backend: trong một dự
        # án, mỗi (tên phân khu, loại căn) chỉ tồn tại một lần.
        sa.UniqueConstraint(
            "project_id", "area_name", "unit_type", name="uq_crm_areas_project_name_unit_type"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["crm_projects.id"], name="fk_crm_areas_project_id"),
        sa.CheckConstraint("external_id <> ''", name="ck_crm_areas_external_id_not_blank"),
        sa.CheckConstraint("area_name <> ''", name="ck_crm_areas_area_name_not_blank"),
        sa.CheckConstraint("unit_type <> ''", name="ck_crm_areas_unit_type_not_blank"),
        sa.CheckConstraint("bedrooms >= 0", name="ck_crm_areas_bedrooms_nonnegative"),
        sa.CheckConstraint("area_sqm > 0", name="ck_crm_areas_area_sqm_positive"),
        sa.CheckConstraint("total_units >= 0", name="ck_crm_areas_total_units_nonnegative"),
        sa.CheckConstraint(_in_list("status", LIFECYCLE_STATUSES), name="ck_crm_areas_status"),
        sa.CheckConstraint("source_revision > 0", name="ck_crm_areas_revision_positive"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_crm_areas_updated_after_created"),
        sa.CheckConstraint(
            "mirrored_revision IS NULL OR mirrored_revision > 0",
            name="ck_crm_areas_mirrored_revision_positive",
        ),
        sa.CheckConstraint(
            "(mirrored_at IS NULL) = (mirrored_revision IS NULL)",
            name="ck_crm_areas_mirrored_pair",
        ),
    )
    op.create_index("ix_crm_areas_project_id", "crm_areas", ["project_id"])

    # --- 4. crm_units.area_id — NULLABLE, xem docstring mục 1 ---------------
    op.add_column("crm_units", sa.Column("area_id", UUID, nullable=True))
    op.create_foreign_key("fk_crm_units_area_id", "crm_units", "crm_areas", ["area_id"], ["id"])
    op.create_index("ix_crm_units_area_id", "crm_units", ["area_id"])


def downgrade() -> None:
    op.drop_index("ix_crm_units_area_id", table_name="crm_units")
    op.drop_constraint("fk_crm_units_area_id", "crm_units", type_="foreignkey")
    op.drop_column("crm_units", "area_id")

    op.drop_index("ix_crm_areas_project_id", table_name="crm_areas")
    op.drop_table("crm_areas")

    op.drop_table("crm_projects")

    # LƯU Ý VẬN HÀNH: giống 0002 — gỡ dãy là mất con trỏ id. Đừng downgrade một
    # Mini CRM đang mang dữ liệu Project/Area thật.
    op.execute(f"DROP SEQUENCE IF EXISTS {AREA_SEQUENCE}")
    op.execute(f"DROP SEQUENCE IF EXISTS {PROJECT_SEQUENCE}")
