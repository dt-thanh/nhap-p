"""unit_status_history: nhật ký append-only cho các lần đổi status của units

Revision ID: 0028_unit_status_history
Revises: 0027_project_price_observations
Create Date: 2026-08-22

Thuần CỘNG THÊM. `units`, `deals`, `areas`, `projects`, `absorption_daily` và
toàn bộ nhóm bảng xếp hạng/agent/giá quan trắc giữ nguyên từng byte — không cột
nào bị sửa, không dòng nào bị đụng.

**Vì sao cần bảng này.** `absorption_daily` là DELETE-AND-REINSERT theo
`calculator`/phạm vi (xem `pipeline_status.md`, Stage 5): nó không giữ lịch sử
tồn kho theo ngày, chỉ giữ giá trị mới nhất áp cho MỌI `stat_date` đã tính lại.
Không bảng nào trong hệ thống hiện trả lời được "phân khu X có bao nhiêu căn
sellable tại đúng ngày Y trong quá khứ" — đó là điều kiện cần của
`absorption_rate_30d/90d` tại một cutoff lịch sử bất kỳ. Bảng này ghi lại từng
lần `units.status` đổi, để trạng thái tại một ngày bất kỳ suy ra được bằng cách
gấp (fold) nhật ký, không phải đọc một cột snapshot có thể lệch.

**Vì sao KHÔNG có cột lưu sẵn `sellable_inventory_at_period_start`.** Con số đó
là một hàm của `(area, cutoff)` trên nhật ký này, không phải một giá trị đứng
yên. Lưu sẵn nó sẽ hỏng ở mỗi lần CRM sửa muộn (late-arriving correction), và
không sửa lại được nếu không biết chính xác cutoff nào đã từng ghi.

**Vì sao append-only, ép bằng REVOKE + trigger, không phải quy ước ở code.**
Một nhật ký sự kiện mà ai đó UPDATE được thì không còn là nhật ký sự kiện — nó
biến thành một bảng trạng thái thứ hai, và hai bảng trạng thái không đồng bộ là
đúng loại lỗi nhật ký này sinh ra để tránh. REVOKE chặn vai ứng dụng; trigger
chặn cả một phiên superuser bảo trì gõ nhầm bảng.

**Vì sao trigger PHÁT SINH sự kiện (từ `units`) nằm ở revision KHÁC (0030), còn
trigger BẢO VỆ append-only nằm NGAY ở đây.** Bảo vệ append-only là bất biến nội
tại của chính bảng này — nó sinh và mất cùng vòng đời với bảng, nên
`downgrade()` ở đây gỡ cả hai cùng lúc, đúng và an toàn. Trigger phát sinh lại
gắn vào bảng `units` đã tồn tại từ 0007 — tách nó sang 0030 để lùi (downgrade)
việc BẮT sự kiện không kéo theo xoá NHỮNG sự kiện đã bắt được, như phần tóm tắt
migration priority đã nêu.

**Vì sao FK là CASCADE, không phải RESTRICT.** Bản nháp đầu của revision này
dùng RESTRICT, theo đúng lý lẽ của `fk_price_obs_unit_id` (0027): "units xoá
mềm, nên một dòng biến mất thật là bất thường". Lý lẽ đó SAI cho CẶP bảng này —
kiểm bằng test thật lộ ra ngay: `scripts/_seed_ai_crm_fixture_core.py` (0019) và
`scripts/seed_domain_demo_2026.py` (0023) đều XOÁ CỨNG rồi ghi lại `units`/
`deals` của chính fixture chúng sở hữu, mỗi lần `alembic upgrade head` chạy lại
từ đầu — và khoảng hai mươi module ở `tests/` cũng dọn dẹp bằng
`DELETE FROM units`/`DELETE FROM deals` giữa các test. RESTRICT biến MỌI lần
xoá đó thành một lỗi khoá ngoại, dù nội dung dòng dữ liệu không hề mâu thuẫn:
khi chính `units`/`deals` đã biến mất, lịch sử của một đơn vị không còn tồn tại
cũng không còn ý nghĩa gì để giữ RESTRICT lại. CASCADE ở đây không phải một xoá
âm thầm kiểu 0027 cảnh báo — đó là kết luận cụ thể áp cho một bảng KHÔNG có
nguồn dữ liệu con nào cần bảo vệ khỏi cha của nó, khác hẳn hoàn cảnh dẫn tới
RESTRICT của giá niêm yết.

**Vì sao `deal_id` NULLABLE.** CRM có thể đổi `units.status` không qua một giao
dịch nào (ví dụ: admin chuyển một căn `available -> blocked`). NULL ở đây nghĩa
là "không có giao dịch nào đứng sau", khác với "không biết" — trigger phát sinh
ở 0030 luôn ghi NULL cho cột này (gán một giao dịch là suy đoán, không phải sự
thật quan sát được, nên để P1 làm bằng một join riêng).

**Vì sao `old_status` NULLABLE.** NULL nghĩa là dòng khai sinh (unit mới), HOẶC
biên trái bị cắt cụt (left-censored) khi backfill phát lại từ `sync_payloads`
không có gì để so sánh. `metadata_json->>'boundary'` phân biệt hai trường hợp.

**Vì sao có cả `changed_at` lẫn `recorded_at`.** `changed_at` là thời gian
NGHIỆP VỤ — CRM có thể gửi muộn. `recorded_at` là đồng hồ PHÍA NHẬN, không bao
giờ bị lùi. `changed_at < recorded_at` là bình thường (lịch sử đến muộn), không
phải dấu hiệu hỏng dữ liệu.

**Không backfill ở đây.** Giống nguyên tắc của 0027: migration KHÔNG bịa dữ
liệu lịch sử. Phát lại từ `sync_payloads` là việc của `scripts/`, ghi
`source='backfill_replay'`, chạy sau khi migration này lên `head`.

Đường lùi: `downgrade()` gỡ trigger bảo vệ, hai hàm, hai chỉ mục, rồi bảng. Cái
mất khi lùi: toàn bộ nhật ký đổi status đã ghi được — chúng KHÔNG dựng lại được
từ `units` (chỉ giữ status hiện tại), chỉ dựng lại một phần từ `sync_payloads`
trong cửa sổ lưu giữ còn lại, và với giới hạn "gấp khúc" đã nêu ở
`docs/ranking/ranking_consultant.md`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028_unit_status_history"
down_revision: str | None = "0027_project_price_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
TS = sa.TIMESTAMP(timezone=True)

TABLE = "unit_status_history"
INDEX_UNIT_CHANGED = "ix_ush_unit_changed_at"
INDEX_CHANGED = "ix_ush_changed_at"
INDEX_DEAL = "ix_ush_deal_id"
GUARD_FUNCTION = "unit_status_history_append_only"
GUARD_TRIGGER = "trg_ush_append_only"

# Cùng tập giá trị với `ck_units_status` (0007) / `UNIT_STATUSES` ở
# `src/services/domain_projection.py`. Nếu tập đó đổi, ràng buộc ở đây PHẢI đổi
# theo cùng lúc — cố ý ràng hai nơi lại với nhau, không tách rời cho "tiện".
UNIT_STATUSES = ("available", "reserved", "sold", "blocked")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID, nullable=False),
        sa.Column("unit_id", UUID, nullable=False),
        sa.Column("deal_id", UUID, nullable=True),
        sa.Column("old_status", sa.Text(), nullable=True),
        sa.Column("new_status", sa.Text(), nullable=False),
        sa.Column("changed_at", TS, nullable=False),
        sa.Column("recorded_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id", name="pk_unit_status_history"),
        # CASCADE, không RESTRICT — xem lý do ở docstring module. Xoá cứng
        # units/deals là một luồng THẬT trong repo này (seed idempotent + dọn
        # dẹp test), không phải một thao tác bất thường cần chặn lại.
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_ush_unit_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], name="fk_ush_deal_id", ondelete="CASCADE"),
        sa.CheckConstraint(
            "new_status IN ('available', 'reserved', 'sold', 'blocked')",
            name="ck_ush_new_status",
        ),
        sa.CheckConstraint(
            "old_status IS NULL OR old_status IN ('available', 'reserved', 'sold', 'blocked')",
            name="ck_ush_old_status",
        ),
        # Một chuyển trạng thái không đổi gì không phải một sự kiện — ghi nó sẽ
        # thổi phồng mọi phép đếm chuyển trạng thái ở tầng đọc.
        sa.CheckConstraint(
            "old_status IS NULL OR old_status <> new_status",
            name="ck_ush_actual_change",
        ),
        sa.CheckConstraint(
            "source IN ('crm_sync', 'backfill_replay', 'seed', 'manual')",
            name="ck_ush_source",
        ),
        sa.CheckConstraint("jsonb_typeof(metadata_json) = 'object'", name="ck_ush_metadata_object"),
    )

    # Đường đọc chính: phát lại dòng thời gian của một căn, hoặc gấp cả một
    # phân khu tại một cutoff. DESC vì "trạng thái tại thời điểm T" quét ngược.
    op.create_index(INDEX_UNIT_CHANGED, TABLE, ["unit_id", sa.text("changed_at DESC")])
    op.create_index(INDEX_CHANGED, TABLE, ["changed_at"])
    op.create_index(INDEX_DEAL, TABLE, ["deal_id"], postgresql_where=sa.text("deal_id IS NOT NULL"))

    # Append-only, ép ở hai lớp: REVOKE chặn vai ứng dụng bình thường; trigger
    # chặn cả một phiên chạy bằng vai sở hữu bảng (superuser bảo trì).
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON "{TABLE}" FROM PUBLIC')
    op.execute(
        f"""
        CREATE FUNCTION {GUARD_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- pg_trigger_depth() > 1 nghĩa là câu DELETE này không phải do
            -- client gõ trực tiếp, mà do ON DELETE CASCADE của
            -- fk_ush_unit_id/fk_ush_deal_id thực thi khi units/deals bị xoá
            -- cứng (0019/0023 tái tạo fixture, và ~20 module test dọn dẹp).
            -- Chặn trường hợp đó sẽ biến CASCADE thành vô dụng: câu DELETE
            -- lồng bên trong vẫn kích trigger này như một DELETE bình thường.
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
    # LƯU Ý VẬN HÀNH: gỡ bảng này XOÁ toàn bộ nhật ký đổi status của units đã
    # ghi. `units` chỉ giữ status HIỆN TẠI nên không dựng lại được từ đó; phần
    # dựng lại được từ `sync_payloads` bị giới hạn bởi cửa sổ lưu giữ và không
    # phân biệt được nhiều lần đổi giữa hai lượt đồng bộ liên tiếp (xem mục
    # "Backfill" ở `docs/ranking/ranking_consultant.md`).
    op.execute(f"DROP TRIGGER IF EXISTS {GUARD_TRIGGER} ON \"{TABLE}\"")
    op.execute(f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}()")
    op.drop_index(INDEX_DEAL, table_name=TABLE)
    op.drop_index(INDEX_CHANGED, table_name=TABLE)
    op.drop_index(INDEX_UNIT_CHANGED, table_name=TABLE)
    op.drop_table(TABLE)
