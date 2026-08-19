"""giữ payload thô của mỗi lô đồng bộ, tách khỏi bảng trạng thái lô

Revision ID: 0009_sync_payloads
Revises: 0008_sync_credentials
Create Date: 2026-08-09

Đi SAU `sync_credentials` (0008) vì thứ tự hai migration phản chiếu thứ tự một
request: xác thực trước, lưu payload sau. Xem docstring của 0008.

**Vì sao tách bảng riêng thay vì thêm một cột JSONB vào `upload_files`.**
`upload_files` là bảng TRẠNG THÁI: nó bị đọc ở mọi lần client poll `/sync-runs/{id}`
và bị cập nhật nhiều lần trong một lô. Payload thô thì lớn (tới 5 MB), chỉ ghi
đúng một lần, và hầu như không bao giờ đọc lại — chỉ khi cần chạy lại hoặc điều
tra sự cố. Nhét chung sẽ khiến mọi lần poll kéo theo megabyte JSON qua mạng và
làm phình TOAST của bảng nóng nhất luồng này.

**Payload thô là thứ khiến "chạy lại" (replay) có thật.** Không giữ nó thì một lô
xử lý sai chỉ còn cách xin CRM gửi lại — mà CRM có thể đã đổi dữ liệu, và lúc đó
không ai dựng lại được chuyện gì đã xảy ra. Giữ nguyên văn byte nhận được, chưa
qua chuẩn hoá, là điều kiện để `payload_sha256` còn ý nghĩa kiểm toàn vẹn.

**`payload_sha256` KHÔNG phải phiên bản.** Nó chỉ dùng để phát hiện payload lưu
bị hỏng và để nhận ra hai lô có nội dung giống hệt nhau. Thứ tự thời gian chỉ
đến từ `source_revision` / `source_updated_at` của từng bản ghi — xem
`docs/crm/sync_contract_v1_draft.md` mục 5.1.

Lưu `payload` dưới dạng JSONB chứ không phải text: nó cho phép truy vấn chẩn đoán
(`payload -> 'records' -> 0`) mà không phải parse lại ở tầng ứng dụng, và
PostgreSQL đã nén sẵn qua TOAST.

Đánh đổi kèm theo, và cách xử lý: JSONB không giữ thứ tự khoá và bỏ khoá trùng
lặp. Nên `payload_sha256` được tính trên dạng CHUẨN HOÁ (`sort_keys`, không
khoảng trắng) chứ không trên byte gốc — chỉ dạng chuẩn hoá mới tái lập được từ
JSONB đọc ra, và một cột hash không kiểm lại được thì vô dụng đúng vào lúc cần
nó. `payload_bytes` thì ngược lại: đo trên byte gốc, vì nó trả lời câu hỏi về
đường truyền chứ không phải về toàn vẹn.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_sync_payloads"
down_revision: str | None = "0008_sync_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

SHA256_HEX_LENGTH = 64


def upgrade() -> None:
    op.create_table(
        "sync_payloads",
        sa.Column("id", UUID, nullable=False),
        # Lô mà payload này thuộc về. `upload_files` là bảng trạng thái lô dùng
        # chung cho cả đường file lẫn đường API (0006).
        sa.Column("sync_run_id", UUID, nullable=False),
        # --- Nội dung thô ---
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # --- Bối cảnh nhận ---
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("received_at", TS, nullable=False),
        # Khoá đã dùng để gửi lô này. NULL = lô đến qua đường không cần xác thực
        # (đường nạp file cũ, hoặc test nội bộ). ON DELETE SET NULL: thu hồi khoá
        # không được phép xoá lịch sử payload.
        sa.Column("credential_id", UUID, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sync_payloads"),
        sa.ForeignKeyConstraint(
            ["sync_run_id"], ["upload_files.id"], name="fk_sync_payloads_sync_run_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["sync_credentials.id"], name="fk_sync_payloads_credential_id", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            f"length(payload_sha256) = {SHA256_HEX_LENGTH}",
            name="ck_sync_payloads_sha256_length",
        ),
        sa.CheckConstraint("payload_bytes > 0", name="ck_sync_payloads_bytes_positive"),
        sa.CheckConstraint("record_count >= 0", name="ck_sync_payloads_record_count_nonnegative"),
    )

    # Một lô giữ ĐÚNG một payload thô. Gửi lại cùng `external_batch_id` được trả
    # kết quả cũ chứ không ghi thêm payload, nên hai dòng cho cùng một lô nghĩa là
    # tầng idempotency đã hỏng — chặn ở DB để lỗi đó lộ ra ngay.
    op.create_index("uq_sync_payloads_run", "sync_payloads", ["sync_run_id"], unique=True)

    # Tra theo nội dung: nhận ra hai lô khác nhau mang cùng nội dung. Chỉ để chẩn
    # đoán, KHÔNG dùng để suy ra thứ tự.
    op.create_index("ix_sync_payloads_sha256", "sync_payloads", ["payload_sha256"])


def downgrade() -> None:
    # LƯU Ý VẬN HÀNH: gỡ bảng này XOÁ TOÀN BỘ payload thô đã giữ, tức là mất khả
    # năng chạy lại các lô cũ. Trạng thái lô ở `upload_files` và danh tính bản ghi
    # ở `crm_source_records` vẫn còn, nên dữ liệu đã chiếu không mất — nhưng dấu
    # vết để dựng lại "CRM đã gửi đúng cái gì" thì mất hẳn.
    #
    # Sau khi bảng này có dữ liệu thật, đường lùi ĐÚNG là phục hồi từ bản dump,
    # không phải `alembic downgrade`.
    op.drop_index("ix_sync_payloads_sha256", table_name="sync_payloads")
    op.drop_index("uq_sync_payloads_run", table_name="sync_payloads")
    op.drop_table("sync_payloads")
