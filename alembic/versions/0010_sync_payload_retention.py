"""bảo vệ payload thô: đổi ON DELETE CASCADE thành RESTRICT trên sync_payloads

Revision ID: 0010_sync_payload_retention
Revises: 0009_sync_payloads
Create Date: 2026-08-09

`0009` gắn `sync_payloads.sync_run_id` với `ON DELETE CASCADE`. Ý định lúc đó
đúng — không để lại payload mồ côi — nhưng hệ quả thì sai theo hướng nguy hiểm:

    DELETE FROM upload_files WHERE ...;   -- xoá luôn payload thô, im lặng

Payload thô là hiện vật DUY NHẤT cho phép chạy lại một lô và dựng lại được "hệ
nguồn đã thực sự gửi cái gì". Một câu lệnh dọn dẹp gõ vội, một script bảo trì,
một lần xoá nhầm phạm vi — tất cả đều xoá sạch nó mà không báo gì. Mất dữ liệu
im lặng là loại mất tệ nhất, vì không ai biết để mà khôi phục.

RESTRICT đảo lại mặc định: muốn xoá lô thì phải xoá payload TRƯỚC, một cách có
chủ đích. Không cấm được việc xoá — chỉ bắt nó phải là hành động cố ý.

**Chính sách lưu giữ thay cho xoá dây chuyền.** Dọn dẹp theo tuổi được thực hiện
bằng cách xoá THẲNG trên `sync_payloads`, không đụng tới `upload_files`:

    DELETE FROM sync_payloads WHERE received_at < now() - interval '90 days';

Bất đối xứng này là có chủ đích: payload thì to và chỉ cần trong một cửa sổ điều
tra, còn metadata lô (`upload_files`) thì nhỏ và là lịch sử vận hành — nó nên
sống mãi. Xoá payload cũ vẫn để lại đầy đủ dấu vết lô đã chạy, chỉ mất khả năng
chạy lại lô quá hạn.

`ix_sync_payloads_received_at` phục vụ đúng câu truy vấn dọn dẹp trên; không có
nó thì mỗi lần dọn phải quét toàn bảng.

Không đụng tới `credential_id`: `ON DELETE SET NULL` ở đó vẫn đúng — thu hồi khoá
được phép xoá liên kết, nhưng không được xoá payload.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_sync_payload_retention"
down_revision: str | None = "0009_sync_payloads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_sync_payloads_sync_run_id", "sync_payloads", type_="foreignkey")
    op.create_foreign_key(
        "fk_sync_payloads_sync_run_id",
        "sync_payloads",
        "upload_files",
        ["sync_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Chỉ mục cho truy vấn dọn dẹp theo tuổi.
    op.create_index("ix_sync_payloads_received_at", "sync_payloads", ["received_at"])


def downgrade() -> None:
    # Quay lại CASCADE. LƯU Ý: sau bước này, xoá một dòng `upload_files` sẽ lại
    # âm thầm xoá payload thô đi kèm.
    op.drop_index("ix_sync_payloads_received_at", table_name="sync_payloads")
    op.drop_constraint("fk_sync_payloads_sync_run_id", "sync_payloads", type_="foreignkey")
    op.create_foreign_key(
        "fk_sync_payloads_sync_run_id",
        "sync_payloads",
        "upload_files",
        ["sync_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
