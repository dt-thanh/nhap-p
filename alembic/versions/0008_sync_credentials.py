"""xác thực máy-với-máy cho luồng đồng bộ: khoá API băm, buộc vào một source_instance_id

Revision ID: 0008_sync_credentials
Revises: 0007_s3_domain_model
Create Date: 2026-08-09

Bảng đầu tiên của Phase 3, và cố ý đi TRƯỚC `sync_payloads` (0009): thứ tự hai
migration này phản chiếu thứ tự của một request thật — xác thực xong mới được
phép lưu bất cứ thứ gì. Dựng bảng lưu payload trước bảng xác thực sẽ tạo ra một
khoảng thời gian mà schema cho phép nhận dữ liệu chưa xác thực; khoảng đó không
cần phải tồn tại.

**Chỉ lưu HASH, không bao giờ lưu khoá.** `key_hash` là SHA-256 của khoá thô.
Chọn SHA-256 chứ không phải bcrypt/argon2 vì khoá API là chuỗi ngẫu nhiên 32 byte
do máy sinh, không phải mật khẩu người đặt: nó không có entropy thấp để mà cần
làm chậm phép thử, và đường xác thực này nằm trong mọi request nên chi phí phải
là hằng số nhỏ. Với bí mật ngẫu nhiên đủ dài, tấn công từ điển không áp dụng.

**`key_prefix` là 8 ký tự đầu của khoá thô**, lưu nguyên văn và có index. Nó chỉ
để TRA CỨU và để người vận hành nhận ra khoá nào trong log — không đủ để dựng lại
khoá (còn 24 byte ngẫu nhiên nữa). Không có nó thì mỗi lần xác thực phải quét
toàn bảng và so hash từng dòng.

**Buộc credential vào đúng một `source_instance_id`** là ranh giới cô lập thật sự
của hệ thống: một khoá bị lộ chỉ ghi được vào đúng instance của nó, không với
sang instance khác. Ràng buộc này nằm ở cột NOT NULL chứ không ở tầng ứng dụng,
nên không có đường vòng nào.

`revoked_at` là xoá mềm: khoá bị thu hồi phải còn nhìn thấy được để truy vết
những lô đã nhận bằng nó. Xoá cứng sẽ làm mồ côi lịch sử.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_sync_credentials"
down_revision: str | None = "0007_s3_domain_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

# Độ dài hex của SHA-256. Ghim bằng CHECK để một hash bị cắt cụt hay một chuỗi
# rỗng không lọt vào bảng và âm thầm khớp với mọi thứ.
SHA256_HEX_LENGTH = 64

# Số ký tự đầu khoá dùng để tra cứu.
KEY_PREFIX_LENGTH = 8


def upgrade() -> None:
    op.create_table(
        "sync_credentials",
        sa.Column("id", UUID, nullable=False),
        # --- Ai được dùng khoá này ---
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_instance_id", sa.Text(), nullable=False),
        # --- Bí mật ---
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        # --- Vòng đời ---
        sa.Column("label", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("last_used_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sync_credentials"),
        # Hash là duy nhất toàn cục: hai dòng cùng hash nghĩa là cùng một khoá được
        # cấp hai lần, và lúc đó "khoá này thuộc instance nào" không còn câu trả lời.
        sa.UniqueConstraint("key_hash", name="uq_sync_credentials_key_hash"),
        sa.CheckConstraint("source_system <> ''", name="ck_sync_credentials_source_system_not_blank"),
        sa.CheckConstraint("source_instance_id <> ''", name="ck_sync_credentials_source_instance_not_blank"),
        sa.CheckConstraint(
            f"length(key_hash) = {SHA256_HEX_LENGTH}",
            name="ck_sync_credentials_key_hash_length",
        ),
        sa.CheckConstraint(
            f"length(key_prefix) = {KEY_PREFIX_LENGTH}",
            name="ck_sync_credentials_key_prefix_length",
        ),
        # Khoá hết hạn trước khi được tạo là vô nghĩa; chặn ở DB để không cần tin
        # vào việc mọi đường ghi đều nhớ kiểm.
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_sync_credentials_expires_after_created",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_sync_credentials_revoked_after_created",
        ),
    )

    # Đường tra cứu của mỗi lần xác thực. KHÔNG unique: xoay khoá là cấp khoá mới
    # rồi mới thu hồi khoá cũ, nên hai khoá còn sống có thể trùng 8 ký tự đầu —
    # hiếm, nhưng unique ở đây sẽ biến việc đó thành lỗi 500 lúc cấp khoá.
    op.create_index("ix_sync_credentials_key_prefix", "sync_credentials", ["key_prefix"])
    op.create_index(
        "ix_sync_credentials_instance",
        "sync_credentials",
        ["source_instance_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    # Không có dữ liệu nào khác tham chiếu tới bảng này, nên gỡ bỏ là đối xứng.
    # LƯU Ý VẬN HÀNH: gỡ bảng này XOÁ MỌI KHOÁ ĐÃ CẤP. Khoá không khôi phục được
    # từ hash, nên sau khi downgrade phải cấp lại khoá mới cho mọi hệ nguồn.
    op.drop_index("ix_sync_credentials_instance", table_name="sync_credentials")
    op.drop_index("ix_sync_credentials_key_prefix", table_name="sync_credentials")
    op.drop_table("sync_credentials")
