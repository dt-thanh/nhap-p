"""status_history_triggers: bắt sự kiện đổi status trên units/deals vào 0028/0029

Revision ID: 0030_status_history_triggers
Revises: 0029_deal_status_history
Create Date: 2026-08-22

**Đây là revision DUY NHẤT đụng vào `units`/`deals` đang có** trong cả nhóm ba
migration này — và nó chỉ THÊM trigger, không `add_column`, không
`alter_column`, không đổi ràng buộc/chỉ mục nào của 0007. `domain_projection.py`
không bị sửa: trigger là AFTER INSERT/UPDATE, chạy SAU khi
`DomainProjector._upsert` (INSERT ... ON CONFLICT DO UPDATE ... RETURNING xmax)
hoàn tất câu lệnh của nó — không thêm SELECT, không đổi race-condition mà thiết
kế `_upsert` đã cố tình tránh.

**Vì sao tách khỏi 0028/0029 thay vì gộp vào table creation.** Bảo vệ
append-only (đã ở 0028/0029) là bất biến NỘI TẠI của bảng lịch sử — sinh/mất
cùng bảng là đúng. Trigger PHÁT SINH ở đây lại là một thay đổi lên bảng
NGƯỜI KHÁC (`units`, `deals`) đã tồn tại từ 0007. Hai loại thay đổi khác bản
chất xứng đáng hai đường lùi khác nhau: lùi 0030 dừng việc BẮT sự kiện mới,
nhưng không xoá một dòng lịch sử nào đã bắt được ở 0028/0029 — tách biệt đúng
với mục đích "rollback không phá dữ liệu" nêu ở migration priority.

**Vì sao KHÔNG suy ra `source` từ `units.source_system`/`deals.source_system`.**
Cột đó phản ánh HỆ NGUỒN của thực thể (`mini_crm`, hoặc một chuỗi fixture như
`crm_real_data_fixture` ở 0021) — không phải một enum đóng phân biệt
"CRM thật" khỏi "seed". Ràng một trigger vào một chuỗi ký tự tồn tại duy nhất
trong một migration khác là một khớp nối ẩn không ai canh giữ. Thay vào đó
dùng biến phiên `app.history_source` — quy ước audit-trigger tiêu chuẩn của
Postgres: bên gọi khai `SET LOCAL app.history_source = '...'` trước khi ghi, và
`COALESCE(current_setting(...), 'manual')` là NHÃN AN TOÀN khi không khai (cùng
nguyên tắc mặc định `source='manual'` mà `project_price_observations` (0027)
đã dùng).

**GIỚI HẠN ĐÃ BIẾT, chưa vá ở đây (xem Risk Assessment).** Không route ghi
`units`/`deals` nào trong repo hiện SET biến phiên đó — nghĩa là MỌI sự kiện do
trigger này phát ra, kể cả từ đồng bộ CRM thật, sẽ mang `source='manual'` cho
tới khi có một dòng `SET LOCAL app.history_source = 'crm_sync'` được thêm vào
đúng một chỗ (biên phiên/giao dịch của job đồng bộ, KHÔNG phải bên trong
`_upsert`). Việc dây đó CỐ Ý không nằm trong revision này: nó không phải thay
đổi schema, và gộp vào đây sẽ vi phạm ràng buộc "không sửa `domain_projection.py`"
của migration này.

**Vì sao `deal_id` luôn NULL ở `unit_status_history` từ trigger này.** Gán một
giao dịch cụ thể cho một lần đổi status của UNIT là suy đoán (join theo thời
gian gần nhất), không phải sự thật quan sát trực tiếp được trong cùng câu lệnh
UPDATE. Để P1 làm bằng một job làm giàu riêng, tách khỏi đường ghi realtime.

Đường lùi: gỡ đúng hai trigger và hai hàm đã tạo. `unit_status_history`/
`deal_status_history` và mọi dòng đã ghi trong đó KHÔNG bị đụng.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030_status_history_triggers"
down_revision: str | None = "0029_deal_status_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNITS_FUNCTION = "units_emit_status_history"
UNITS_TRIGGER = "trg_units_status_history"
DEALS_FUNCTION = "deals_emit_status_history"
DEALS_TRIGGER = "trg_deals_status_history"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {UNITS_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- Không đổi giá trị thật sự thì không phải một sự kiện.
            IF TG_OP = 'UPDATE' AND OLD.status IS NOT DISTINCT FROM NEW.status THEN
                RETURN NULL;
            END IF;

            INSERT INTO unit_status_history (
                id, unit_id, deal_id, old_status, new_status,
                changed_at, source, metadata_json
            ) VALUES (
                gen_random_uuid(),
                NEW.id,
                NULL,
                CASE WHEN TG_OP = 'UPDATE' THEN OLD.status ELSE NULL END,
                NEW.status,
                -- Thời gian hệ nguồn khi có; thời gian phía nhận khi không.
                -- COALESCE ở đây không bịa mốc nào, chỉ chọn mốc THẬT tốt nhất.
                COALESCE(NEW.source_updated_at, NEW.updated_at),
                COALESCE(current_setting('app.history_source', true), 'manual'),
                jsonb_build_object('source_revision', NEW.source_revision, 'tg_op', TG_OP)
            );
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {UNITS_TRIGGER}
            AFTER INSERT OR UPDATE OF status ON units
            FOR EACH ROW EXECUTE FUNCTION {UNITS_FUNCTION}();
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {DEALS_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            prior_holding boolean;
            new_holding boolean;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.status IS NOT DISTINCT FROM NEW.status THEN
                RETURN NULL;
            END IF;

            -- Cùng tập giá trị với HOLDING_STATUSES ở
            -- src/services/domain_projection.py. Nếu tập đó đổi, hàm này phải
            -- đổi theo cùng lúc.
            prior_holding := TG_OP = 'UPDATE' AND OLD.status IN ('reserved', 'sold');
            new_holding := NEW.status IN ('reserved', 'sold');

            INSERT INTO deal_status_history (
                id, deal_id, unit_id, old_status, new_status,
                prior_status_was_holding, new_status_is_holding,
                changed_at, source, metadata_json
            ) VALUES (
                gen_random_uuid(),
                NEW.id,
                NEW.unit_id,
                CASE WHEN TG_OP = 'UPDATE' THEN OLD.status ELSE NULL END,
                NEW.status,
                prior_holding,
                new_holding,
                COALESCE(NEW.source_updated_at, NEW.updated_at),
                COALESCE(current_setting('app.history_source', true), 'manual'),
                jsonb_build_object('source_revision', NEW.source_revision, 'tg_op', TG_OP)
            );
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {DEALS_TRIGGER}
            AFTER INSERT OR UPDATE OF status ON deals
            FOR EACH ROW EXECUTE FUNCTION {DEALS_FUNCTION}();
        """
    )


def downgrade() -> None:
    # Dừng BẮT sự kiện mới. Không xoá dòng nào đã có trong
    # unit_status_history/deal_status_history — hai bảng đó thuộc 0028/0029.
    op.execute(f"DROP TRIGGER IF EXISTS {DEALS_TRIGGER} ON deals")
    op.execute(f"DROP FUNCTION IF EXISTS {DEALS_FUNCTION}()")
    op.execute(f"DROP TRIGGER IF EXISTS {UNITS_TRIGGER} ON units")
    op.execute(f"DROP FUNCTION IF EXISTS {UNITS_FUNCTION}()")
