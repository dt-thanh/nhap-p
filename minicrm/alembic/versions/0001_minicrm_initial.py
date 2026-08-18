"""schema ban đầu của Mini CRM: crm_units, crm_deals, crm_outbox

Revision ID: 0001_minicrm_initial
Revises:
Create Date: 2026-08-11

**Đây là GỐC của một cây Alembic RIÊNG.** `down_revision = None`. Nó không nối
vào `0001_initial_schema` của backend và không bao giờ được nối — hai lịch sử
migration chạy trên hai database khác nhau, và bảng `alembic_version` của mỗi bên
chỉ biết revision của bên đó.

**Phạm vi tối thiểu, có chủ đích.** Ba bảng, đủ để sinh ra payload `units` và
`deals` hợp lệ theo hợp đồng v1. KHÔNG có khách hàng, hợp đồng, PII, giá, hoa
hồng, lịch thanh toán, nhân viên bán. Hợp đồng đồng bộ không yêu cầu chúng; thêm
vào là mời dữ liệu cá nhân vào một hệ thống chưa có tầng bảo vệ nào, để đổi lấy
đúng con số không.

**Ràng buộc SOI GƯƠNG ràng buộc của backend** (`0007_s3_domain_model`): cùng tập
trạng thái, cùng quy tắc trạng thái ↔ mốc thời gian, cùng ràng buộc một giao dịch
đang giữ mỗi căn. Không phải để trang trí — mục đích là Mini CRM KHÔNG THỂ sinh ra
một payload mà backend chắc chắn từ chối. Bắt lỗi tại nguồn rẻ hơn nhiều so với
bắt nó ở đầu kia của một request HTTP, và nó giữ cho các lô bị từ chối ở backend
mang ý nghĩa thật: một lô đỏ nghĩa là hợp đồng có vấn đề, không phải Mini CRM cẩu thả.

Một điểm KHÁC backend, và nó quan trọng: `crm_units.source_revision` là NOT NULL
với `CHECK > 0`. Ở backend, `source_revision` được phép NULL (hệ nguồn có thể chỉ
gửi `source_updated_at`). Ở đây thì không: Mini CRM là hệ nguồn, nên nó PHẢI cấp
được số phiên bản đơn điệu. Cho phép NULL ở đây là tự cho phép mình vi phạm giả
định A2 của chính hợp đồng mình đang gửi.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_minicrm_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

# Khớp `UNIT_STATUSES` của backend (0007). Backend KHÔNG có bảng alias cho unit —
# nó chỉ hạ chữ thường rồi đòi thuộc tập này — nên Mini CRM phải phát đúng bốn giá
# trị này, không phải từ vựng tiếng Việt.
UNIT_STATUSES = ("available", "reserved", "sold", "blocked")

# Khớp `DEAL_STATUSES` của backend (0007). `cancelled` KHÔNG có ở đây: backend ánh
# xạ nó về `lost` và giữ chữ gốc ở `source_status`, nhưng Mini CRM là hệ nguồn nên
# nó phát thẳng từ vựng chuẩn thay vì dựa vào bảng alias phía nhận.
DEAL_STATUSES = ("lead", "qualified", "interested", "viewing", "reserved", "sold", "lost")

HOLDING_STATUSES = ("reserved", "sold")
OUTBOX_ENTITIES = ("units", "deals")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- Căn hộ -------------------------------------------------------------
    op.create_table(
        "crm_units",
        sa.Column("id", UUID, nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("area_name", sa.Text(), nullable=False),
        sa.Column("unit_type", sa.Text(), nullable=False),
        sa.Column("unit_code", sa.Text(), nullable=False),
        sa.Column("unit_status", sa.Text(), nullable=False),
        sa.Column("source_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_crm_units"),
        # Danh tính gửi sang backend. Duy nhất TOÀN CỤC, kể cả với căn đã xoá:
        # `external_id` bền vững trọn đời và không bao giờ dùng lại (giả định A1).
        # Tombstone KHÔNG giải phóng id.
        sa.UniqueConstraint("external_id", name="uq_crm_units_external_id"),
        sa.CheckConstraint("external_id <> ''", name="ck_crm_units_external_id_not_blank"),
        sa.CheckConstraint("area_name <> ''", name="ck_crm_units_area_name_not_blank"),
        sa.CheckConstraint("unit_type <> ''", name="ck_crm_units_unit_type_not_blank"),
        sa.CheckConstraint("unit_code <> ''", name="ck_crm_units_unit_code_not_blank"),
        sa.CheckConstraint(_in_list("unit_status", UNIT_STATUSES), name="ck_crm_units_status"),
        sa.CheckConstraint("source_revision > 0", name="ck_crm_units_revision_positive"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_crm_units_updated_after_created"),
    )
    # Mã căn duy nhất trong một phân khu, CHỈ xét căn còn sống — soi gương
    # `uq_units_area_unit_code` của backend. Một căn đã xoá không được chặn việc
    # tạo lại cùng mã.
    op.create_index(
        "uq_crm_units_live_code",
        "crm_units",
        ["area_name", "unit_type", "unit_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- Giao dịch ----------------------------------------------------------
    op.create_table(
        "crm_deals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("external_unit_id", sa.Text(), nullable=False),
        sa.Column("deal_status", sa.Text(), nullable=False),
        sa.Column("reserved_at", TS, nullable=True),
        sa.Column("sold_at", TS, nullable=True),
        sa.Column("lost_at", TS, nullable=True),
        sa.Column("source_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("deleted_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_crm_deals"),
        sa.UniqueConstraint("external_id", name="uq_crm_deals_external_id"),
        # Giao dịch phải trỏ tới một căn CÓ THẬT trong chính Mini CRM. Backend
        # cũng từ chối giao dịch mồ côi (`UNKNOWN_UNIT_REFERENCE`), nhưng chặn ở
        # đây nghĩa là payload sai không bao giờ rời khỏi máy.
        sa.ForeignKeyConstraint(
            ["external_unit_id"], ["crm_units.external_id"], name="fk_crm_deals_external_unit_id"
        ),
        sa.CheckConstraint("external_id <> ''", name="ck_crm_deals_external_id_not_blank"),
        sa.CheckConstraint(_in_list("deal_status", DEAL_STATUSES), name="ck_crm_deals_status"),
        sa.CheckConstraint("source_revision > 0", name="ck_crm_deals_revision_positive"),
        # Trạng thái ↔ mốc thời gian, soi gương backend (0007). Đây cũng là chốt
        # A4 nhìn từ phía nguồn: một giao dịch `sold` mà không có `sold_at` không
        # tồn tại được ở đây, nên nó không gửi đi được.
        sa.CheckConstraint(
            "deal_status <> 'reserved' OR reserved_at IS NOT NULL",
            name="ck_crm_deals_reserved_requires_reserved_at",
        ),
        sa.CheckConstraint(
            "deal_status <> 'sold' OR sold_at IS NOT NULL",
            name="ck_crm_deals_sold_requires_sold_at",
        ),
        sa.CheckConstraint(
            "deal_status <> 'lost' OR lost_at IS NOT NULL",
            name="ck_crm_deals_lost_requires_lost_at",
        ),
        sa.CheckConstraint(
            "sold_at IS NULL OR reserved_at IS NULL OR sold_at >= reserved_at",
            name="ck_crm_deals_sold_after_reserved",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_crm_deals_updated_after_created"),
    )
    # MỘT giao dịch đang giữ mỗi căn — soi gương `uq_deals_active_per_unit`.
    # Lịch sử không bị chặn: `lost` và các giai đoạn đầu nằm ngoài tập đang giữ.
    op.create_index(
        "uq_crm_deals_holding_per_unit",
        "crm_deals",
        ["external_unit_id"],
        unique=True,
        postgresql_where=sa.text(
            f"deal_status IN ({', '.join(repr(v) for v in HOLDING_STATUSES)}) AND deleted_at IS NULL"
        ),
    )

    # --- Nhật ký gửi đi -----------------------------------------------------
    op.create_table(
        "crm_outbox",
        sa.Column("id", UUID, nullable=False),
        sa.Column("external_batch_id", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=False),
        # NGUYÊN VĂN phong bì đã gửi. Đây là thứ khiến "gửi lại đúng lô cũ" thành
        # một thao tác thật ở Phase 4 thay vì một lời mô tả.
        sa.Column("payload", JSONB, nullable=False),
        # NULL = đã ghi nhận, CHƯA biết kết quả. Timeout rơi vào đúng ô này, và đó
        # là thông tin chứ không phải lỗi: lô có thể đã tới nơi.
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response", JSONB, nullable=True),
        sa.Column("sent_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_crm_outbox"),
        # Danh tính lô là duy nhất: gửi lại phải DÙNG LẠI dòng này, không tạo dòng
        # thứ hai — nếu không thì "gửi lại cùng lô" ở phía Mini CRM lại thành hai
        # lô khác nhau, và phép thử idempotency mất ý nghĩa.
        sa.UniqueConstraint("external_batch_id", name="uq_crm_outbox_batch_id"),
        sa.CheckConstraint("external_batch_id <> ''", name="ck_crm_outbox_batch_id_not_blank"),
        sa.CheckConstraint(_in_list("entity", OUTBOX_ENTITIES), name="ck_crm_outbox_entity"),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status < 600)",
            name="ck_crm_outbox_http_status_range",
        ),
    )
    op.create_index("ix_crm_outbox_created_at", "crm_outbox", [sa.text("created_at DESC")])


def downgrade() -> None:
    # Thứ tự: `crm_deals` trước `crm_units` (khoá ngoại). `crm_outbox` độc lập.
    #
    # LƯU Ý VẬN HÀNH: gỡ những bảng này xoá toàn bộ dữ liệu Mini CRM và mọi payload
    # đã gửi. Database của backend KHÔNG bị ảnh hưởng — hai hệ thống không dùng
    # chung một database, và đó chính là điểm của toàn bộ thiết kế này.
    op.drop_index("ix_crm_outbox_created_at", table_name="crm_outbox")
    op.drop_table("crm_outbox")

    op.drop_index("uq_crm_deals_holding_per_unit", table_name="crm_deals")
    op.drop_table("crm_deals")

    op.drop_index("uq_crm_units_live_code", table_name="crm_units")
    op.drop_table("crm_units")
