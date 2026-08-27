"""status_history_replay_identity: khoá UNIQUE cho backfill idempotent

Revision ID: 0032_replay_identity_index
Revises: 0031_unit_inventory_daily
Create Date: 2026-08-22

Thuần CỘNG THÊM — hai UNIQUE INDEX, không cột nào, bảng nào khác bị đụng.

**Vì sao cần revision này.** `scripts/backfill_status_history.py` phát lại từ
`sync_payloads` và phải re-run được vô hạn lần mà không nhân đôi dòng — yêu cầu
đó chỉ thực hiện được bằng `INSERT ... ON CONFLICT (...) DO NOTHING`. Postgres
đòi một UNIQUE/EXCLUSION constraint khớp ĐÚNG danh sách cột trong `ON CONFLICT`
để trọng tài xung đột; không có nó thì câu lệnh NỔ ngay ở lần chạy đầu, không
phải lần chạy thứ hai. 0028/0029 chỉ tạo chỉ mục THƯỜNG (`ix_ush_unit_changed_at`
v.v., phục vụ đọc), không phải UNIQUE — thiếu sót này chỉ lộ ra khi viết
`ON CONFLICT DO NOTHING` thật, không lộ ra khi đọc lại DDL bằng mắt.

**Vì sao khoá theo `(unit_id, changed_at, new_status, source)` chứ không theo
`id`.** `id` do backfill tự sinh (`gen_random_uuid()`) mỗi lần chạy — hai lần
chạy sinh hai `id` khác nhau cho CÙNG một sự kiện, nên khoá theo `id` không bao
giờ trùng và `DO NOTHING` sẽ không có tác dụng gì. Bốn cột này là NHẬN DẠNG
NGHIỆP VỤ của một sự kiện: cùng căn/giao dịch, cùng mốc thời gian nguồn, cùng
giá trị mới, cùng nguồn ghi — hai dòng khớp cả bốn gần như chắc chắn là CÙNG một
sự kiện được phát lại hai lần, không phải hai sự kiện thật trùng hợp.

**Vì sao là UNIQUE INDEX RIÊNG PHẦN (`WHERE source = 'backfill_replay'`), không
phải UNIQUE toàn bảng.** Bản nháp đầu ràng cả bốn cột `(unit_id, changed_at,
new_status, source)` không điều kiện — nhưng trigger phát sinh (0030) cũng
INSERT thẳng vào đúng hai bảng này, trong CÙNG transaction với
`DomainProjector._upsert`, và KHÔNG dùng `ON CONFLICT`. Một UNIQUE áp cho cả
dòng `source='crm_sync'`/`'manual'` nghĩa là bất kỳ trùng khớp nào ở đường sống
(dù cực hiếm — ví dụ hệ nguồn trả `source_updated_at` cùng một mốc cho hai lần
đổi thật) sẽ làm INSERT của trigger NỔ, và kéo sập nguyên transaction đồng bộ
đang chạy. Đó là cái giá không được phép trả chỉ để có `ON CONFLICT DO NOTHING`
cho MỘT script chạy tay. Phạm vi `WHERE source = 'backfill_replay'` giữ UNIQUE
này chỉ tác động đúng dòng do `scripts/backfill_status_history.py` tự ghi — dòng
của trigger (0030) không nằm trong phạm vi của chỉ mục này, nên không thể vỡ vì
nó.

Đường lùi: gỡ đúng hai UNIQUE INDEX đã tạo. Không dữ liệu nào bị mất — cả hai
bảng và toàn bộ dòng trong đó giữ nguyên.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_replay_identity_index"
down_revision: str | None = "0031_unit_inventory_daily"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIT_INDEX = "uq_ush_replay_identity"
DEAL_INDEX = "uq_dsh_replay_identity"
REPLAY_SOURCE = "backfill_replay"


def upgrade() -> None:
    op.create_index(
        UNIT_INDEX,
        "unit_status_history",
        ["unit_id", "changed_at", "new_status"],
        unique=True,
        postgresql_where=sa.text(f"source = '{REPLAY_SOURCE}'"),
    )
    op.create_index(
        DEAL_INDEX,
        "deal_status_history",
        ["deal_id", "changed_at", "new_status"],
        unique=True,
        postgresql_where=sa.text(f"source = '{REPLAY_SOURCE}'"),
    )


def downgrade() -> None:
    op.drop_index(DEAL_INDEX, table_name="deal_status_history")
    op.drop_index(UNIT_INDEX, table_name="unit_status_history")
