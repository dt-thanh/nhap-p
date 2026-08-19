"""nguồn gốc bộ tính: absorption_daily mang nhãn calculator, hai lineage sống chung

Revision ID: 0012_calculator_provenance
Revises: 0011_reconciliation
Create Date: 2026-08-09

`absorption_daily` là bảng DẪN XUẤT: nó được tính lại từ bảng gốc chứ không phải
nhập vào. Cho tới nay chỉ có MỘT bộ tính ghi vào nó, nên không dòng nào cần khai
mình đến từ đâu. Từ đây có hai, và một dòng dẫn xuất không nói được ai sinh ra nó
là một dòng không kiểm chứng được.

**Ba cột mới, và vì sao từng cột.**

* `calculator` — nhãn nguồn gốc. NOT NULL với DEFAULT `'legacy_aggregate'`, nên
  360 dòng đang có tự nhận đúng nhãn mà không cần câu UPDATE nào. Không có cột
  này thì hai bộ tính ghi đè lẫn nhau mà không ai biết.
* `units_reserved` — CHO PHÉP NULL, có chủ đích. Bộ tính cũ đọc `sales_records`,
  vốn chỉ có số căn ĐÃ BÁN theo ngày; nó không có cách nào biết số căn đang giữ
  chỗ. Điền `0` sẽ là nói sai — "không có căn nào giữ chỗ" khác hẳn "bộ tính này
  không tính được số đó". Cùng lý do với `units_remaining` ở 0007.
* `computation_id` — một UUID cho mỗi LẦN tính; mọi dòng của cùng lần tính mang
  cùng giá trị. Nhờ đó truy được một dòng thuộc lần chạy nào, và phát hiện được
  một lần ghi dở dang (hai computation_id lẫn lộn trong cùng lineage).

**Nới khoá duy nhất.** `(area_id, stat_date)` → `(area_id, stat_date, calculator)`.
Khoá cũ cho phép ĐÚNG MỘT dòng mỗi ngày mỗi phân khu, nên hai lineage không thể
cùng tồn tại. Nới ra là điều kiện CẦN, không phải điều kiện đủ: thứ thực sự ngăn
hai bộ tính xoá của nhau là phạm vi câu DELETE ở tầng ứng dụng, còn khoá này chỉ
làm việc sống chung trở nên khả thi.

**`projects.absorption_calculator`** là công tắc cắt sang, theo từng dự án
(quyết định 7). Mặc định `'legacy_aggregate'` nên hành vi hiện tại không đổi cho
bất kỳ dự án nào — Phase 6 KHÔNG cắt sang gì cả.

**Thứ tự trong `upgrade()` là bắt buộc**: thêm và điền `calculator` TRƯỚC khi đổi
khoá duy nhất. Đổi khoá trước sẽ dựng index trên một cột còn NULL.

**Đường lùi có một điểm cần nói rõ.** Khôi phục khoá hẹp `(area_id, stat_date)`
sẽ THẤT BẠI nếu cả hai lineage đang tồn tại, vì lúc đó mỗi (phân khu, ngày) có
hai dòng. Nên `downgrade()` xoá các dòng `domain_units_deals` TRƯỚC rồi mới dựng
lại khoá hẹp. Việc này phá dữ liệu, nhưng phá đúng thứ có thể dựng lại được:
dòng domain là dẫn xuất hoàn toàn từ `units`/`deals`, còn dòng legacy — thứ
dashboard đang thực sự đọc — không bị đụng tới. Lựa chọn còn lại là không có
đường lùi nào, và như thế tệ hơn.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_calculator_provenance"
down_revision: str | None = "0011_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

CALCULATOR_LEGACY = "legacy_aggregate"
CALCULATOR_DOMAIN = "domain_units_deals"
CALCULATORS = (CALCULATOR_LEGACY, CALCULATOR_DOMAIN)

NARROW_UNIQUE = "uq_absorption_daily_area_id_stat_date"
WIDE_UNIQUE = "uq_absorption_daily_area_date_calculator"


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- 1. Nhãn nguồn gốc trên bảng dẫn xuất --------------------------------
    # DEFAULT làm luôn việc backfill: mọi dòng đang có đều do bộ tính cũ sinh ra.
    op.add_column(
        "absorption_daily",
        sa.Column("calculator", sa.Text(), nullable=False, server_default=sa.text(f"'{CALCULATOR_LEGACY}'")),
    )
    # NULL = "bộ tính sinh ra dòng này không tính được số đó", không phải "bằng 0".
    op.add_column("absorption_daily", sa.Column("units_reserved", sa.Integer(), nullable=True))
    op.add_column("absorption_daily", sa.Column("computation_id", UUID, nullable=True))

    op.create_check_constraint(
        "ck_absorption_daily_calculator", "absorption_daily", _in_list("calculator", CALCULATORS)
    )
    op.create_check_constraint(
        "ck_absorption_daily_units_reserved_nonnegative",
        "absorption_daily",
        "units_reserved IS NULL OR units_reserved >= 0",
    )

    # --- 2. Nới khoá duy nhất, SAU khi cột calculator đã có giá trị ----------
    # `uq_absorption_daily_area_id_stat_date` là một INDEX unique, KHÔNG phải
    # UNIQUE CONSTRAINT — 0001 tạo nó bằng `create_index(unique=True)`. Dùng
    # `drop_constraint()` ở đây sẽ hỏng ngay lúc chạy. Giữ đúng cùng loại đối
    # tượng cho khoá mới để đường lùi cũng đối xứng.
    op.drop_index(NARROW_UNIQUE, table_name="absorption_daily")
    op.create_index(WIDE_UNIQUE, "absorption_daily", ["area_id", "stat_date", "calculator"], unique=True)

    # Đường đọc của dashboard: lọc theo lineage rồi sắp theo ngày.
    op.create_index(
        "ix_absorption_daily_area_calculator_date",
        "absorption_daily",
        ["area_id", "calculator", "stat_date"],
    )

    # --- 3. Công tắc cắt sang, theo từng dự án ------------------------------
    op.add_column(
        "projects",
        sa.Column(
            "absorption_calculator",
            sa.Text(),
            nullable=False,
            server_default=sa.text(f"'{CALCULATOR_LEGACY}'"),
        ),
    )
    op.create_check_constraint(
        "ck_projects_absorption_calculator", "projects", _in_list("absorption_calculator", CALCULATORS)
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_absorption_calculator", "projects", type_="check")
    op.drop_column("projects", "absorption_calculator")

    op.drop_index("ix_absorption_daily_area_calculator_date", table_name="absorption_daily")

    # BẮT BUỘC đi trước việc dựng lại khoá hẹp: còn hai lineage thì mỗi
    # (phân khu, ngày) có hai dòng và khoá hẹp không tạo được.
    #
    # Chỉ xoá dòng DẪN XUẤT của bộ tính miền. Chúng dựng lại được hoàn toàn từ
    # `units`/`deals`. Dòng `legacy_aggregate` — thứ dashboard đang đọc — không
    # bị đụng tới.
    op.execute(f"DELETE FROM absorption_daily WHERE calculator = '{CALCULATOR_DOMAIN}'")

    op.drop_index(WIDE_UNIQUE, table_name="absorption_daily")
    op.create_index(NARROW_UNIQUE, "absorption_daily", ["area_id", "stat_date"], unique=True)

    op.drop_constraint("ck_absorption_daily_units_reserved_nonnegative", "absorption_daily", type_="check")
    op.drop_constraint("ck_absorption_daily_calculator", "absorption_daily", type_="check")
    op.drop_column("absorption_daily", "computation_id")
    op.drop_column("absorption_daily", "units_reserved")
    op.drop_column("absorption_daily", "calculator")
