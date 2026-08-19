"""chạy song song: lịch sử so sánh giữa bộ tính cũ và bộ tính miền

Revision ID: 0013_calculator_comparisons
Revises: 0012_calculator_provenance
Create Date: 2026-08-10

CHỈ CỘNG THÊM. Không cột nào, ràng buộc nào, chỉ mục nào của các revision trước
bị sửa hay xoá. `absorption_daily`, `projects`, `units`, `deals` và toàn bộ bảng
đồng bộ không bị chạm tới.

**Vì sao cần một bảng.** `ParallelRunComparator` (Phase 6) đã so được hai bộ tính,
nhưng nó tính xong rồi trả về — không lưu gì. Nghĩa là so sánh của hôm qua đã mất.
Điều kiện cắt sang không phải "hôm nay hai bên khớp" mà là "hai bên khớp liên tục
trong N ngày", và một xu hướng thì không đọc được từ một lần đo.

**Đây là DỮ LIỆU QUAN SÁT DẪN XUẤT.** Không con số nào ở đây được dùng để tính ra
thứ gì khác. Không ai đọc nó ở Phase 8; nó tồn tại để trả lời một câu hỏi duy
nhất, sau này: "cắt sang bộ tính mới thì số có đổi không, đổi ở đâu, và có ổn định
không?"

## Ba quyết định về hình dạng bảng

**1. Chỉ số của bộ tính để NULL khi KHÔNG CÓ DỮ LIỆU, không để 0.**

Đây là điểm dễ sai nhất của cả bảng. Một dự án chưa có `units`/`deals` nào sẽ
khiến bộ tính miền ra `units_sold = 0`. Nếu bên cũ cũng 0 thì `matches = true` —
một cái "khớp" RỖNG TUẾCH: hai bên khớp nhau vì cả hai đều không có gì để nói.
Mười bốn ngày như thế trông y hệt mười bốn ngày chạy song song thành công.

Nên `domain_has_data` được lưu TƯỜNG MINH, và khi nó `false` thì ba cột chỉ số
của bộ tính miền là NULL. NULL đọc được là "không biết"; 0 đọc được là "biết, và
bằng không". Ràng buộc CHECK giữ hai thứ đó không bao giờ lẫn nhau. Bên cũ có cặp
cột đối xứng vì đúng lý do đó.

**2. View `calculator_comparisons_gate`.**

Yêu cầu của Phase 8D: dòng `domain_has_data = false` **không bao giờ** được tính
vào cổng chạy song song 14 ngày sau này. Một quy ước ghi trong tài liệu sẽ được
suy diễn lại (và suy diễn sai) khi cổng đó thực sự được viết ở 8G. Nên việc loại
trừ nằm ở DATABASE:

    SELECT ... FROM calculator_comparisons WHERE domain_has_data

Cổng đọc view, không đọc bảng. Muốn tính nhầm dòng rỗng vào thì phải cố ý đi vòng.

**3. `matches` có ràng buộc CHECK ràng với phần chi tiết.**

`matches` là cột mà cổng cắt sang sẽ đọc. Nó không bao giờ được mâu thuẫn với
`difference_count`/`anomaly_count` mà nó tóm tắt — cùng kỷ luật với
`ck_reconciliation_runs_passed_requires_no_errors` ở 0011.

## Khoá ngoại: CASCADE, khác `sync_payloads`

`sync_payloads.sync_run_id` đã được đổi sang RESTRICT ở 0010 vì payload thô là
BẰNG CHỨNG, mất là không dựng lại được. Dòng ở đây thì ngược lại: chúng là quan
sát DẪN XUẤT về một dự án. Dự án không còn thì chúng không mô tả gì nữa, và chặn
việc xoá dự án chỉ vì chúng là đánh đổi sai chiều.

## Gỡ bỏ

`alembic downgrade 0012` xoá view và bảng này, hết. Không bảng nào khác tham chiếu
tới nó, không dòng nào ở nơi khác phải dọn trước — khác hẳn downgrade của 0012,
nơi phải xoá dòng lineage miền trước khi khôi phục chỉ mục hẹp. Mất bảng này chỉ
mất lịch sử quan sát.

## Dữ liệu tổng hợp không phải bằng chứng cắt sang

Mọi dòng sinh ra ở Phase 8 đều bắt nguồn từ fixture TỔNG HỢP. Một dòng
`matches = true` đọc tách khỏi ngữ cảnh KHÔNG chứng minh gì về Mini CRM — Mini CRM
chưa tồn tại. Xem `docs/crm/parallel_run.md`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_calculator_comparisons"
down_revision: str | None = "0012_calculator_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "calculator_comparisons"
INDEX = "ix_calculator_comparisons_project_compared"
GATE_VIEW = "calculator_comparisons_gate"

# Cổng chạy song song CHỈ được đọc view này. Điều kiện lọc nằm ở đây, một chỗ
# duy nhất, thay vì nằm trong đầu người viết truy vấn ở 8G.
GATE_VIEW_SQL = f"""
CREATE VIEW {GATE_VIEW} AS
SELECT *
FROM {TABLE}
WHERE domain_has_data
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("compared_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        # NULL = không có dữ liệu để tính, KHÁC với 0 = tính ra bằng không.
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
        sa.Column("differences", postgresql.JSONB(), nullable=False),
        sa.Column("anomalies", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_calculator_comparisons_project_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'manual')",
            name="ck_calculator_comparisons_trigger",
        ),
        # `matches` không bao giờ được mâu thuẫn với phần chi tiết nó tóm tắt.
        sa.CheckConstraint(
            "matches = (difference_count = 0 AND anomaly_count = 0)",
            name="ck_calculator_comparisons_matches_consistent",
        ),
        sa.CheckConstraint(
            "difference_count >= 0 AND anomaly_count >= 0",
            name="ck_calculator_comparisons_counts_non_negative",
        ),
        # "Không có dữ liệu" và "có dữ liệu, giá trị 0" phải phân biệt được, và
        # không được phép rơi vào trạng thái lửng lơ ở giữa.
        sa.CheckConstraint(
            "(domain_has_data AND domain_units_sold IS NOT NULL "
            "AND domain_units_remaining IS NOT NULL AND domain_units_reserved IS NOT NULL) "
            "OR (NOT domain_has_data AND domain_units_sold IS NULL "
            "AND domain_units_remaining IS NULL AND domain_units_reserved IS NULL)",
            name="ck_calculator_comparisons_domain_nulls_match_flag",
        ),
        sa.CheckConstraint(
            "(legacy_has_data AND legacy_units_sold IS NOT NULL AND legacy_units_remaining IS NOT NULL) "
            "OR (NOT legacy_has_data AND legacy_units_sold IS NULL AND legacy_units_remaining IS NULL)",
            name="ck_calculator_comparisons_legacy_nulls_match_flag",
        ),
    )

    # `compared_at DESC` vì mọi câu hỏi về bảng này đều hỏi về gần đây: "lần so
    # gần nhất", "14 ngày qua". Không có chỉ mục thì mỗi lần đọc là một lần quét
    # toàn bảng, và bảng này chỉ dài thêm chứ không ngắn đi.
    op.create_index(INDEX, TABLE, ["project_id", sa.text("compared_at DESC")])

    op.execute(GATE_VIEW_SQL)


def downgrade() -> None:
    # View trước, bảng sau: view phụ thuộc vào bảng.
    op.execute(f"DROP VIEW IF EXISTS {GATE_VIEW}")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
