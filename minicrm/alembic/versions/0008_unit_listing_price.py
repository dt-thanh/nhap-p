"""add crm_units.listing_price — unit-level listing/official price

Revision ID: 0008_unit_listing_price
Revises: 0007_active_password_or_keycloak
Create Date: 2026-08-23

Mini CRM trở thành NGUỒN của giá niêm yết cấp căn — trước migration này, không
cột nào ở Mini CRM lẫn AbsorpIQ chở được giá (xem
`docs/crm/minicrm_absorpiq_canonical_sync_contract.md`, mục "Missing Fields").

**Vì sao `crm_units`, không phải `crm_deals`.** Đây là giá NIÊM YẾT/CHÍNH THỨC
của một căn — cùng khái niệm với `project_price_observations.official_price`
phía AbsorpIQ ("Giá niêm yết CHÍNH THỨC. KHÔNG phải giá giao dịch thực", xem
`alembic/versions/0027_project_price_observations.py`). Giá GIAO DỊCH THỰC
(`crm_deals.transaction_price`) là một quyết định sản phẩm RIÊNG, chưa được
đưa ra — cố tình KHÔNG thêm ở đây (xem tài liệu trên).

**Vì sao NUMERIC(18, 2), không phải Integer/Float.** Khớp CHÍNH XÁC kiểu cột
`project_price_observations.official_price` phía nhận — tránh sai số dấu phẩy
động khi hai hệ thống trao đổi cùng một con số tiền tệ.

**Vì sao nullable, không backfill.** Đơn thuần CỘNG THÊM: 100% căn hiện có giữ
`listing_price = NULL` ("chưa biết giá") sau migration — không có nguồn nào để
suy ra giá cho các căn đã tồn tại, và bịa một giá trị mặc định sẽ biến một ô
trống trung thực thành một con số sai có thẩm quyền (cùng nguyên tắc với
0027). Không đơn vị tiền tệ nào được thêm: kho lược đồ hiện tại (kể cả
`project_price_observations`) không có quy ước tiền tệ nào cả — thêm một quy
ước MỚI ở đây sẽ là một quyết định sản phẩm ngoài phạm vi migration này.

Đường lùi đối xứng hoàn toàn: không bảng nào khác tham chiếu cột này.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_unit_listing_price"
down_revision = "0007_active_password_or_keycloak"
branch_labels = None
depends_on = None

TABLE = "crm_units"
COLUMN = "listing_price"
CHECK = "ck_crm_units_listing_price_positive"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Numeric(18, 2), nullable=True))
    # `> 0`, không `>= 0`: giá bằng 0 không phải một giá thật — "chưa biết giá"
    # đã có NULL để diễn đạt, khớp ràng buộc `ck_price_obs_price_positive`
    # phía AbsorpIQ (0027).
    op.create_check_constraint(CHECK, TABLE, f"{COLUMN} IS NULL OR {COLUMN} > 0")


def downgrade() -> None:
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
