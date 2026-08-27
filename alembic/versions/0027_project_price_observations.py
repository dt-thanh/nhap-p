"""project_price_observations: quan trắc giá niêm yết, có khoảng hiệu lực

Revision ID: 0027_project_price_observations
Revises: 0026_cloudinary_cover_images
Create Date: 2026-08-21

Thuần CỘNG THÊM. `units`, `deals`, `areas`, `projects`, `absorption_daily` và
toàn bộ nhóm bảng xếp hạng/agent giữ nguyên từng byte — không cột nào bị sửa,
không dòng nào bị đụng.

**Vì sao là bảng riêng, không phải cột trên `units`.** `units` là bản sao MỘT
CHIỀU của Mini CRM (0007): mọi trường nghiệp vụ do CRM sở hữu, ứng dụng chỉ sở
hữu `deleted_at` và các mốc ghi nhận. Hợp đồng đồng bộ CẤM trường giá —
`src/contracts/crm_sync_v2.schema.json` đặt `additionalProperties: false` và bốn
test cưỡng chế điều đó (`minicrm/tests/test_contract_copy.py::
test_no_envelope_field_can_carry_customer_or_price_data` và ba test cùng họ).
Giá vì thế đi đường vào THỨ HAI, tách bạch — cùng mô hình mà đặc trưng khảo sát
đã dùng (`src/services/survey_features.py`).

**Vì sao UUID chứ không phải Integer.** `units.id` là `postgresql.UUID`. Khoá
ngoại Integer trỏ vào UUID không tạo được; và toàn bộ 23 bảng hiện có đều dùng
UUID làm khoá chính. Một bảng dùng Integer sẽ là ngoại lệ duy nhất trong repo.

**Vì sao có `effective_from`/`effective_to` chứ không phải một cột giá.** Giá đổi
theo đợt mở bán. Một cột đơn chỉ giữ được giá CUỐI CÙNG và xoá sạch lịch sử —
nghĩa là không tính được `price_change_90d`, và mọi so sánh hồi cố đều dùng giá
hôm nay cho sự kiện hôm qua. `effective_to IS NULL` = giá đang áp dụng.

**Không backfill.** Chưa có nguồn giá nào trong repo: `data/discount_policies.json`
không gắn với căn hay giao dịch nào. Bảng được tạo RỖNG. Điền số giả ở đây sẽ
biến một ô trống trung thực thành một con số sai có thẩm quyền.

Đường lùi đối xứng hoàn toàn: không bảng nào tham chiếu tới bảng này ở revision
này, nên `downgrade()` chỉ việc gỡ nó. Cái mất khi lùi: toàn bộ quan trắc giá đã
nhập tay — chúng KHÔNG dựng lại được từ `units`/`deals`, vì không nguồn nào
trong hệ thống sinh ra chúng.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027_project_price_observations"
down_revision: str | None = "0026_cloudinary_cover_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

TABLE = "project_price_observations"
INDEX_UNIT = "ix_price_obs_unit_id"
INDEX_CURRENT = "ix_price_obs_unit_current"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID, nullable=False),
        sa.Column("unit_id", UUID, nullable=False),
        # Giá niêm yết CHÍNH THỨC. KHÔNG phải giá giao dịch thực — chênh lệch
        # giữa hai con số chính là chiết khấu, và gộp chúng làm sai mọi phân
        # tích biên. Giá giao dịch thực là một `price_kind` khác, ở revision sau.
        sa.Column("official_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        # NULL = đang áp dụng. KHÁC với một ngày trong quá khứ (đã hết hiệu lực).
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_project_price_observations"),
        # RESTRICT chứ không CASCADE: `units` dùng xoá MỀM (`deleted_at`), nên
        # một dòng `units` biến mất là chuyện bất thường — không được lặng lẽ
        # kéo theo lịch sử giá.
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name="fk_price_obs_unit_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("official_price > 0", name="ck_price_obs_price_positive"),
        sa.CheckConstraint("source <> ''", name="ck_price_obs_source_not_blank"),
        # Khoảng hiệu lực không được ngược chiều.
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_price_obs_effective_range",
        ),
    )

    # Đường đọc chính: nạp lịch sử giá của một căn trong một lượt.
    op.create_index(INDEX_UNIT, TABLE, ["unit_id"])

    # ĐÚNG MỘT giá đang áp dụng mỗi căn. Partial unique — cùng ý tưởng với
    # `uq_deals_active_per_unit` (0007) và `uq_ranking_configs_published` (0014).
    # Lịch sử không bị chặn: mọi dòng đã hết hiệu lực nằm ngoài tập này.
    op.create_index(
        INDEX_CURRENT,
        TABLE,
        ["unit_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )


def downgrade() -> None:
    # LƯU Ý VẬN HÀNH: gỡ bảng này XOÁ toàn bộ quan trắc giá đã nhập tay. Không
    # có nguồn nào trong hệ thống dựng lại được chúng — hợp đồng đồng bộ CẤM
    # trường giá, nên Mini CRM không bao giờ gửi lại. Dữ liệu nghiệp vụ
    # (`units`, `deals`, `areas`, `absorption_daily`) KHÔNG bị ảnh hưởng.
    op.drop_index(INDEX_CURRENT, table_name=TABLE)
    op.drop_index(INDEX_UNIT, table_name=TABLE)
    op.drop_table(TABLE)
