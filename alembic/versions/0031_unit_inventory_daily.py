"""unit_inventory_daily: gấp nhật ký status theo ngày, mỗi phân khu (P1)

Revision ID: 0031_unit_inventory_daily
Revises: 0030_status_history_triggers
Create Date: 2026-08-22

**P1 — không chặn absorption chuẩn.** Mọi con số ở bảng này ĐỀU suy ra được từ
`unit_status_history` (0028) bằng cách gấp (fold) nhật ký tới một cutoff. Bảng
này thuần là VẬT CHẤT HOÁ HIỆU NĂNG — tránh quét toàn bộ `unit_status_history`
mỗi lần đọc dashboard. Không nên vật chất hoá trước khi 0028/0030 đã chạy đủ
lâu để có nhật ký thật để gấp (khuyến nghị: chờ ít nhất 30 ngày capture, xem
migration priority ở `docs/ranking/ranking_consultant.md`).

**Vì sao KHÔNG phải delete-and-reinsert như `absorption_daily`.** `absorption_daily`
(0001) xoá-và-ghi-lại toàn bộ vì bộ tính của nó luôn tính lại từ trạng thái
HIỆN TẠI của `units`/`deals` — không có khái niệm "giá trị của ngày hôm qua đã
chốt". Bảng này thì khác: `stat_date` trong quá khứ là một SỰ KIỆN ĐÃ CHỐT,
gấp từ nhật ký append-only — ghi lại một `stat_date` cũ phải là UPSERT có kiểm
tra, không phải xoá sạch rồi build lại mù mờ, vì một lần chạy lỗi không được
phép âm thầm ghi đè một ngày đã đúng trước đó chỉ vì phạm vi ngày chạy khác đi.

**Vì sao bốn cột đếm riêng (`sellable_units`, `blocked_units`,
`live_reserved_units`, `live_sold_units`) thay vì một cột `sellable_units`
duy nhất.** Quy tắc sellable ở `src/services/domain_absorption.py` là
`sellable = total - blocked`, và `units_remaining = sellable - sold - reserved`
(units đang giữ). Lưu từng số hạng riêng khiến một thay đổi quy tắc (ví dụ:
`blocked` tách thành hai loại) hiện rõ ở cột nào lệch, thay vì một tổng đã gộp
không truy ngược được.

**Vì sao `rebuilt_from_log_at` NOT NULL.** Chứng minh mỗi dòng là kết quả của
một lần gấp nhật ký tại một thời điểm biết trước, không phải một giá trị được
sửa tay — cùng tinh thần với `computed_at`/`computation_id` của `absorption_daily`.

Thuần CỘNG THÊM: không đụng `units`, `deals`, `areas`, `absorption_daily`,
`unit_status_history`, `deal_status_history`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0031_unit_inventory_daily"
down_revision: str | None = "0030_status_history_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

TABLE = "unit_inventory_daily"
INDEX_AREA_DATE = "uq_unit_inventory_daily_area_stat_date"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("sellable_units", sa.Integer(), nullable=False),
        sa.Column("blocked_units", sa.Integer(), nullable=False),
        sa.Column("live_reserved_units", sa.Integer(), nullable=False),
        sa.Column("live_sold_units", sa.Integer(), nullable=False),
        sa.Column("rebuilt_from_log_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_unit_inventory_daily"),
        # Cùng lựa chọn với fk_absorption_daily_area_id (0001): không CASCADE,
        # areas dùng nghiệp vụ archive/soft-state riêng, không hard-delete.
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_unit_inventory_daily_area_id"),
        sa.CheckConstraint("sellable_units >= 0", name="ck_uid_sellable_nonnegative"),
        sa.CheckConstraint("blocked_units >= 0", name="ck_uid_blocked_nonnegative"),
        sa.CheckConstraint("live_reserved_units >= 0", name="ck_uid_reserved_nonnegative"),
        sa.CheckConstraint("live_sold_units >= 0", name="ck_uid_sold_nonnegative"),
    )
    op.create_index(INDEX_AREA_DATE, TABLE, ["area_id", "stat_date"], unique=True)
    # Đường đọc phụ: dựng chuỗi thời gian của một phân khu mà không cần WHERE area_id
    # riêng lẻ mỗi ngày (đã có nhờ index unique ở trên phủ prefix area_id, nhưng
    # thêm chỉ mục riêng trên stat_date phục vụ quét theo ngày trên nhiều phân khu).
    op.create_index("ix_unit_inventory_daily_stat_date", TABLE, ["stat_date"])


def downgrade() -> None:
    # Bảng này thuần vật chất hoá — mọi dòng dựng lại được 100% từ
    # unit_status_history bằng cách gấp lại nhật ký. Không có cảnh báo mất dữ
    # liệu nào cần nêu ở đây, khác với 0028/0029.
    op.drop_index("ix_unit_inventory_daily_stat_date", table_name=TABLE)
    op.drop_index(INDEX_AREA_DATE, table_name=TABLE)
    op.drop_table(TABLE)
