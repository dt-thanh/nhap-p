"""mô hình miền S3: units, deals, và cột tồn kho cho absorption_daily

Revision ID: 0007_s3_domain_model
Revises: 0006_sync_foundation
Create Date: 2026-08-09

Hai bảng nghiệp vụ mà nền đồng bộ (0006) đã chuẩn bị chỗ cho. Cả hai là BẢN SAO
một chiều của CRM: CRM sở hữu mọi trường nghiệp vụ, ứng dụng này chỉ sở hữu
metadata soi gương (`deleted_at`, mốc ghi nhận) và kết quả tính toán.

**Không có `units.project_id`.** Dự án đã suy ra được qua `areas.project_id`;
thêm cột nữa là mở đường cho hai giá trị mâu thuẫn về cùng một sự thật. Truy vấn
theo dự án đi qua `areas` — đúng như `AbsorptionCalculatorService` hiện có.

**Danh tính** dùng `(source_instance_id, external_unit_id)` /
`(source_instance_id, external_deal_id)`: `source_instance_id` là định danh KẾT
NỐI tới một hệ nguồn cụ thể, nên nó đã đủ tách các nguồn khỏi nhau. Danh tính đầy
đủ bốn phần vẫn nằm ở `crm_source_records` (0006) và nối được với hai bảng này
qua chính cặp cột trên.

**Ràng buộc một giao dịch đang giữ mỗi căn** là partial unique trên
`status IN ('reserved','sold') AND deleted_at IS NULL`. Nó KHÔNG chặn lịch sử:
một căn có thể có nhiều giao dịch `lost` và nhiều giao dịch ở giai đoạn đầu
(`lead`…`viewing`); chỉ trạng thái ĐANG GIỮ mới là duy nhất. Huỷ một giao dịch
đưa nó ra khỏi tập đang giữ nên luồng huỷ vẫn chạy được.

`absorption_daily.units_remaining` để **NULL được**: bộ tính cũ (đọc
`sales_records`) không biết tồn kho theo từng căn nên không ghi cột này, và điền
`0` cho những dòng đó là nói sai. NULL đọc là "bộ tính sinh ra dòng này không
tính được số đó".

Không xoá dữ liệu ở cả hai chiều: `downgrade()` chỉ gỡ đúng những gì `upgrade()`
thêm, và hai bảng mới chưa có ai tham chiếu tới.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_s3_domain_model"
down_revision: str | None = "0006_sync_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

# Trạng thái căn — đúng tập của SRS §5.2.8.
UNIT_STATUSES = ("available", "reserved", "sold", "blocked")

# Trạng thái giao dịch — đúng tập của SRS §5.2.8. `cancelled` KHÔNG nằm ở đây:
# hệ nguồn gửi `cancelled` thì tầng chiếu ánh xạ về `lost` và giữ lại giá trị gốc
# ở `source_status`. Xem `src/services/domain_projection.py`.
DEAL_STATUSES = ("lead", "qualified", "interested", "viewing", "reserved", "sold", "lost")

# Chỉ hai trạng thái này GIỮ căn.
HOLDING_STATUSES = ("reserved", "sold")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "units",
        sa.Column("id", UUID, nullable=False),
        # --- Danh tính ở hệ nguồn (CRM sở hữu) ---
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_instance_id", sa.Text(), nullable=False),
        sa.Column("external_unit_id", sa.Text(), nullable=False),
        # --- Trường nghiệp vụ (CRM sở hữu) ---
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("unit_code", sa.Text(), nullable=False),
        sa.Column("unit_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        # --- Phiên bản nguồn, để tầng chiếu bỏ qua bản cũ ---
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("source_updated_at", TS, nullable=True),
        # --- Metadata soi gương (ứng dụng sở hữu) ---
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_units"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_units_area_id"),
        sa.UniqueConstraint("source_instance_id", "external_unit_id", name="uq_units_source_identity"),
        sa.CheckConstraint("source_system <> ''", name="ck_units_source_system_not_blank"),
        sa.CheckConstraint("source_instance_id <> ''", name="ck_units_source_instance_not_blank"),
        sa.CheckConstraint("external_unit_id <> ''", name="ck_units_external_id_not_blank"),
        sa.CheckConstraint("unit_code <> ''", name="ck_units_unit_code_not_blank"),
        sa.CheckConstraint("unit_type <> ''", name="ck_units_unit_type_not_blank"),
        sa.CheckConstraint(_in_list("status", UNIT_STATUSES), name="ck_units_status"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_units_updated_after_created"),
    )
    # Mã căn duy nhất trong phân khu — khoá tự nhiên của repo. Chỉ áp cho căn còn
    # sống: một căn đã xoá không được chặn CRM tạo lại cùng mã.
    op.create_index(
        "uq_units_area_unit_code",
        "units",
        ["area_id", "unit_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_units_area_id_status", "units", ["area_id", "status"])

    op.create_table(
        "deals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_instance_id", sa.Text(), nullable=False),
        sa.Column("external_deal_id", sa.Text(), nullable=False),
        sa.Column("unit_id", UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        # Giá trị trạng thái NGUYÊN VĂN của hệ nguồn. Giữ lại vì tầng chiếu có ánh
        # xạ alias (`cancelled` → `lost`); không lưu thì không truy được vì sao một
        # giao dịch lại mang trạng thái này.
        sa.Column("source_status", sa.Text(), nullable=False),
        sa.Column("reserved_at", TS, nullable=True),
        sa.Column("sold_at", TS, nullable=True),
        sa.Column("lost_at", TS, nullable=True),
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("source_updated_at", TS, nullable=True),
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_deals"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_deals_unit_id"),
        sa.UniqueConstraint("source_instance_id", "external_deal_id", name="uq_deals_source_identity"),
        sa.CheckConstraint("source_system <> ''", name="ck_deals_source_system_not_blank"),
        sa.CheckConstraint("source_instance_id <> ''", name="ck_deals_source_instance_not_blank"),
        sa.CheckConstraint("external_deal_id <> ''", name="ck_deals_external_id_not_blank"),
        sa.CheckConstraint("source_status <> ''", name="ck_deals_source_status_not_blank"),
        sa.CheckConstraint(_in_list("status", DEAL_STATUSES), name="ck_deals_status"),
        # Trạng thái không được mâu thuẫn với mốc thời gian đi kèm.
        sa.CheckConstraint(
            "status <> 'sold' OR sold_at IS NOT NULL",
            name="ck_deals_sold_requires_sold_at",
        ),
        sa.CheckConstraint(
            "status <> 'reserved' OR reserved_at IS NOT NULL",
            name="ck_deals_reserved_requires_reserved_at",
        ),
        sa.CheckConstraint(
            "status <> 'lost' OR lost_at IS NOT NULL",
            name="ck_deals_lost_requires_lost_at",
        ),
        sa.CheckConstraint(
            "sold_at IS NULL OR reserved_at IS NULL OR sold_at >= reserved_at",
            name="ck_deals_sold_after_reserved",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_deals_updated_after_created"),
    )
    # MỘT giao dịch đang giữ mỗi căn. Lịch sử không bị chặn: `lost` và các giai
    # đoạn đầu nằm ngoài tập này, giao dịch đã xoá cũng vậy.
    op.create_index(
        "uq_deals_active_per_unit",
        "deals",
        ["unit_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({', '.join(repr(v) for v in HOLDING_STATUSES)}) AND deleted_at IS NULL"),
    )
    op.create_index("ix_deals_unit_id", "deals", ["unit_id"])
    op.create_index(
        "ix_deals_sold_at",
        "deals",
        ["sold_at"],
        postgresql_where=sa.text("status = 'sold' AND deleted_at IS NULL"),
    )

    # Tồn kho cuối ngày. NULL = bộ tính sinh ra dòng này không tính được số đó
    # (bộ tính cũ đọc `sales_records`, không biết gì về từng căn).
    op.add_column("absorption_daily", sa.Column("units_remaining", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_absorption_daily_units_remaining_nonnegative",
        "absorption_daily",
        "units_remaining IS NULL OR units_remaining >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_absorption_daily_units_remaining_nonnegative", "absorption_daily", type_="check")
    op.drop_column("absorption_daily", "units_remaining")

    op.drop_index("ix_deals_sold_at", table_name="deals")
    op.drop_index("ix_deals_unit_id", table_name="deals")
    op.drop_index("uq_deals_active_per_unit", table_name="deals")
    op.drop_table("deals")

    op.drop_index("ix_units_area_id_status", table_name="units")
    op.drop_index("uq_units_area_unit_code", table_name="units")
    op.drop_table("units")
