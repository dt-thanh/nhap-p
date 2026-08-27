"""deal_status_history: nhật ký append-only cho các lần đổi status của deals

Revision ID: 0029_deal_status_history
Revises: 0028_unit_status_history
Create Date: 2026-08-22

Thuần CỘNG THÊM, cùng nguyên tắc với 0028. `units`, `deals`, `areas`, `projects`
và toàn bộ bảng hiện có giữ nguyên từng byte.

**Vì sao KHÔNG đủ chỉ dùng `unit_status_history`.** `UNIT_STATUSES` (0007) —
`{available, reserved, sold, blocked}` — không có giá trị `lost`. Một giao dịch
bị huỷ chỉ hiện ở PHÍA UNIT như `reserved -> available`, giống hệt một admin mở
khoá thủ công. Không có nhật ký RIÊNG của `deals.status`, không cách nào phân
biệt "huỷ giao dịch" khỏi "gỡ chặn hành chính" — nghĩa là
`cancellation_adjusted_absorption` và `net_absorption` không tính được, dù đã
có 0028.

**Vì sao có `unit_id`, không chỉ `deal_id`.** `deals.unit_id` không đổi trong
suốt vòng đời một giao dịch (không migration nào cho phép sửa nó), nên chép lại
ở đây là khử chuẩn hoá AN TOÀN — đổi lại là truy vấn "nhật ký giao dịch của một
căn" không cần join `deals` mỗi lần đọc.

**Vì sao có `prior_status_was_holding`/`new_status_is_holding`, tính sẵn ở
trigger, không tính lại ở tầng đọc.** `DEAL_STATUSES` trộn cả trạng thái phễu
bán hàng (`lead`, `qualified`, `interested`, `viewing` — CHƯA từng giữ tồn kho)
với trạng thái giữ tồn kho thật (`reserved`, `sold` — xem `HOLDING_STATUSES` ở
`src/services/domain_projection.py`). Một lead bị đánh mất KHÔNG phải một huỷ
giao dịch — tính nhầm sẽ làm sai `net_absorption` một cách âm thầm. Cờ được
trigger tính tại đúng thời điểm ghi sự kiện, dùng đúng tập `HOLDING_STATUSES`
hiện hành — nếu tập đó đổi ở `domain_projection.py`, trigger phát sinh (0030)
phải đổi theo, và ràng buộc CHECK ở đây giữ nguyên (nó chỉ kiểm kiểu boolean,
không kiểm logic nghiệp vụ).

**Vì sao KHÔNG có cột `lost_reason_code`.** Chưa có phân loại lý do huỷ nào
được xác nhận nghiệp vụ (xem mục "Stop conditions" ở
`docs/ranking/ranking_consultant.md`, câu hỏi Q2). Thêm một cột mở mà không có
danh sách đóng sẽ được điền không nhất quán ngay từ ngày đầu. `metadata_json`
vẫn giữ được `source_status` gốc của hệ nguồn cho tới khi phân loại được chốt.

Đường lùi đối xứng với 0028: mất nhật ký đổi status của deals đã ghi, không dựng
lại được từ `deals` (chỉ giữ status hiện tại).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029_deal_status_history"
down_revision: str | None = "0028_unit_status_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TS = sa.TIMESTAMP(timezone=True)

TABLE = "deal_status_history"
INDEX_UNIT_CHANGED = "ix_dsh_unit_changed_at"
INDEX_DEAL_CHANGED = "ix_dsh_deal_changed_at"
INDEX_CHANGED = "ix_dsh_changed_at"
GUARD_FUNCTION = "deal_status_history_append_only"
GUARD_TRIGGER = "trg_dsh_append_only"

# Cùng tập giá trị với `ck_deals_status` (0007) / `DEAL_STATUSES` ở
# `src/services/domain_projection.py`. Ràng hai nơi lại với nhau có chủ đích.
DEAL_STATUSES = ("lead", "qualified", "interested", "viewing", "reserved", "sold", "lost")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID, nullable=False),
        sa.Column("deal_id", UUID, nullable=False),
        sa.Column("unit_id", UUID, nullable=False),
        sa.Column("old_status", sa.Text(), nullable=True),
        sa.Column("new_status", sa.Text(), nullable=False),
        # Tính tại thời điểm ghi sự kiện — xem lý do ở docstring module.
        sa.Column("prior_status_was_holding", sa.Boolean(), nullable=False),
        sa.Column("new_status_is_holding", sa.Boolean(), nullable=False),
        sa.Column("changed_at", TS, nullable=False),
        sa.Column("recorded_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id", name="pk_deal_status_history"),
        # CASCADE, không RESTRICT — cùng lý do với 0028 (xem docstring module
        # của 0028_unit_status_history): xoá cứng units/deals là một luồng
        # THẬT trong repo này (seed idempotent 0019/0023 + dọn dẹp test).
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], name="fk_dsh_deal_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_dsh_unit_id", ondelete="CASCADE"),
        sa.CheckConstraint(
            "new_status IN ('lead', 'qualified', 'interested', 'viewing', 'reserved', 'sold', 'lost')",
            name="ck_dsh_new_status",
        ),
        sa.CheckConstraint(
            "old_status IS NULL OR "
            "old_status IN ('lead', 'qualified', 'interested', 'viewing', 'reserved', 'sold', 'lost')",
            name="ck_dsh_old_status",
        ),
        sa.CheckConstraint(
            "old_status IS NULL OR old_status <> new_status",
            name="ck_dsh_actual_change",
        ),
        sa.CheckConstraint(
            "source IN ('crm_sync', 'backfill_replay', 'seed', 'manual')",
            name="ck_dsh_source",
        ),
        sa.CheckConstraint("jsonb_typeof(metadata_json) = 'object'", name="ck_dsh_metadata_object"),
    )

    op.create_index(INDEX_UNIT_CHANGED, TABLE, ["unit_id", sa.text("changed_at DESC")])
    op.create_index(INDEX_DEAL_CHANGED, TABLE, ["deal_id", sa.text("changed_at DESC")])
    op.create_index(INDEX_CHANGED, TABLE, ["changed_at"])

    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON "{TABLE}" FROM PUBLIC')
    op.execute(
        f"""
        CREATE FUNCTION {GUARD_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- pg_trigger_depth() > 1 = xoá do CASCADE của fk_dsh_deal_id/
            -- fk_dsh_unit_id, không phải client gõ trực tiếp. Xem chú thích
            -- cùng chỗ ở unit_status_history (0028).
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION
                '{TABLE} is append-only: % rejected', TG_OP
                USING HINT = 'Correct history by appending a compensating event, not by editing an existing row.';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {GUARD_TRIGGER}
            BEFORE UPDATE OR DELETE ON "{TABLE}"
            FOR EACH ROW EXECUTE FUNCTION {GUARD_FUNCTION}();
        """
    )


def downgrade() -> None:
    # Cùng cảnh báo vận hành với 0028: xoá bảng này xoá luôn nhật ký huỷ/chuyển
    # giao dịch đã ghi, không dựng lại được từ `deals` (chỉ giữ status hiện tại).
    op.execute(f"DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON \"{TABLE}\"")
    op.execute(f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}()")
    op.drop_index(INDEX_CHANGED, table_name=TABLE)
    op.drop_index(INDEX_DEAL_CHANGED, table_name=TABLE)
    op.drop_index(INDEX_UNIT_CHANGED, table_name=TABLE)
    op.drop_table(TABLE)
