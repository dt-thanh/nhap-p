"""CRUD Mini CRM: dãy sinh external_id, dấu vết đã soi gương, và sổ gửi đi đầy đủ

Revision ID: 0002_minicrm_crud
Revises: 0001_minicrm_initial
Create Date: 2026-08-11

Phase 4 biến Mini CRM từ "một cái vỏ có schema" thành một hệ nguồn thật sự ghi
được. Ba nhóm thay đổi, mỗi nhóm giải đúng một vấn đề mà Phase 3 để lại:

**1. Dãy (SEQUENCE) sinh `external_id`.** Hợp đồng nói `external_id` bền vững
trọn đời và KHÔNG BAO GIỜ dùng lại, kể cả sau tombstone (giả định A1). Sinh nó
bằng `max(external_id) + 1` sẽ tái sử dụng id ngay khi bản ghi cuối bị xoá cứng,
và sẽ đua nhau khi có hai request cùng lúc. Một SEQUENCE của PostgreSQL không lùi
và không cấp trùng kể cả khi transaction gọi nó bị rollback — chính cái "lỗ" mà
người ta hay coi là nhược điểm lại là thứ ta cần ở đây: id bị bỏ phí thì không sao,
id bị dùng lại thì hỏng vĩnh viễn.

**2. Dấu vết đã soi gương (`mirrored_*`).** Phase 4 đòi: một giao dịch KHÔNG được
gửi đi nếu căn của nó chưa lên tới backend. Muốn cưỡng chế điều đó thì phải biết
căn nào đã lên tới nơi — mà `crm_outbox` chỉ biết theo LÔ, không biết theo bản
ghi. Ba cột này là câu trả lời ở mức bản ghi.

`mirrored_revision` giữ số phiên bản CAO NHẤT đã được backend nhận, không phải
phiên bản mới nhất ở đây. Hai số bằng nhau = đã đồng bộ; `mirrored_revision` thấp
hơn = có thay đổi cục bộ chưa lên tới nơi. Nhìn một dòng là biết, không phải suy
từ sổ gửi đi.

**3. Sổ gửi đi đầy đủ hơn.** `attempts` để một lần gửi lại tường minh còn để lại
vết (nếu không, gửi lại 5 lần trông y hệt gửi 1 lần); `last_error` để lỗi TRUYỀN
TẢI — thứ không có mã HTTP nào — vẫn đọc được; `replay_of` để một lô phát lại bản
cũ truy được về lô gốc mà nó sao chép.

`replay_of` cần thiết vì phát lại bản CŨ bắt buộc phải mang `external_batch_id`
MỚI: gửi lại đúng batch id cũ thì backend trả kết quả đã lưu (`replayed=true`) và
không bao giờ chạy tới nhánh so phiên bản. Hai thao tác nghe giống nhau nhưng kiểm
hai thứ khác hẳn — một cái kiểm tính bất biến của lô, một cái kiểm thứ tự phiên
bản — nên chúng phải phân biệt được ở mức dữ liệu, không chỉ trong đầu người vận hành.

KHÔNG có khách hàng, hợp đồng, PII, giá, hoa hồng, thanh toán, nhân viên bán.
Phase 4 không nới phạm vi đó một milimet nào.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_minicrm_crud"
down_revision: str | None = "0001_minicrm_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.TIMESTAMP(timezone=True)

# Một dãy cho mỗi thực thể. Dùng CHUNG một dãy sẽ khiến `U-0001` và `D-0002` cùng
# tồn tại — đọc log thấy số nhảy cách quãng và không ai biết vì sao.
UNIT_SEQUENCE = "crm_unit_external_seq"
DEAL_SEQUENCE = "crm_deal_external_seq"


def upgrade() -> None:
    # --- 1. Dãy sinh external_id -------------------------------------------
    # KHÔNG gắn vào cột nào (không phải `SERIAL`/`IDENTITY`): `external_id` là
    # chuỗi có tiền tố (`U-0001`), nên phần số được lấy ra rồi ĐỊNH DẠNG ở tầng
    # ứng dụng. Gắn dãy vào cột sẽ buộc cột phải là số và mất luôn tiền tố — thứ
    # duy nhất khiến nhìn một id là biết nó thuộc thực thể nào.
    op.execute(f"CREATE SEQUENCE {UNIT_SEQUENCE} AS BIGINT START WITH 1 INCREMENT BY 1 NO CYCLE")
    op.execute(f"CREATE SEQUENCE {DEAL_SEQUENCE} AS BIGINT START WITH 1 INCREMENT BY 1 NO CYCLE")

    # --- 2. Dấu vết đã soi gương -------------------------------------------
    for table in ("crm_units", "crm_deals"):
        op.add_column(table, sa.Column("mirrored_at", TS, nullable=True))
        op.add_column(table, sa.Column("mirrored_revision", sa.BigInteger(), nullable=True))
        op.add_column(table, sa.Column("last_sync_batch_id", sa.Text(), nullable=True))
        # NULL = chưa từng lên tới backend. `0` sẽ là một lời nói dối: nó trông
        # như "đã đồng bộ ở phiên bản 0", mà phiên bản 0 không tồn tại ở đây
        # (`ck_*_revision_positive` đòi > 0).
        op.create_check_constraint(
            f"ck_{table}_mirrored_revision_positive",
            table,
            "mirrored_revision IS NULL OR mirrored_revision > 0",
        )
        # Hai cột phải cùng có hoặc cùng không: một mốc thời gian không kèm phiên
        # bản không nói được ĐIỀU GÌ đã lên tới nơi.
        op.create_check_constraint(
            f"ck_{table}_mirrored_pair",
            table,
            "(mirrored_at IS NULL) = (mirrored_revision IS NULL)",
        )

    # Tra "còn gì chưa lên tới backend" là câu hỏi vận hành thường xuyên nhất.
    op.create_index(
        "ix_crm_units_unmirrored",
        "crm_units",
        ["external_id"],
        postgresql_where=sa.text("mirrored_revision IS NULL OR mirrored_revision < source_revision"),
    )
    op.create_index(
        "ix_crm_deals_unmirrored",
        "crm_deals",
        ["external_id"],
        postgresql_where=sa.text("mirrored_revision IS NULL OR mirrored_revision < source_revision"),
    )

    # --- 3. Sổ gửi đi -------------------------------------------------------
    op.add_column("crm_outbox", sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("crm_outbox", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("crm_outbox", sa.Column("replay_of", sa.Text(), nullable=True))
    op.create_check_constraint("ck_crm_outbox_attempts_non_negative", "crm_outbox", "attempts >= 0")
    op.create_check_constraint("ck_crm_outbox_replay_of_not_blank", "crm_outbox", "replay_of IS NULL OR replay_of <> ''")
    # Một lô phát lại KHÔNG được trỏ về chính nó: như thế thì nó vừa là bản gốc
    # vừa là bản sao, và chuỗi truy ngược thành một vòng lặp.
    op.create_check_constraint(
        "ck_crm_outbox_replay_of_not_self", "crm_outbox", "replay_of IS NULL OR replay_of <> external_batch_id"
    )


def downgrade() -> None:
    op.drop_constraint("ck_crm_outbox_replay_of_not_self", "crm_outbox", type_="check")
    op.drop_constraint("ck_crm_outbox_replay_of_not_blank", "crm_outbox", type_="check")
    op.drop_constraint("ck_crm_outbox_attempts_non_negative", "crm_outbox", type_="check")
    op.drop_column("crm_outbox", "replay_of")
    op.drop_column("crm_outbox", "last_error")
    op.drop_column("crm_outbox", "attempts")

    op.drop_index("ix_crm_deals_unmirrored", table_name="crm_deals")
    op.drop_index("ix_crm_units_unmirrored", table_name="crm_units")

    for table in ("crm_units", "crm_deals"):
        op.drop_constraint(f"ck_{table}_mirrored_pair", table, type_="check")
        op.drop_constraint(f"ck_{table}_mirrored_revision_positive", table, type_="check")
        op.drop_column(table, "last_sync_batch_id")
        op.drop_column(table, "mirrored_revision")
        op.drop_column(table, "mirrored_at")

    # LƯU Ý VẬN HÀNH: gỡ dãy là mất con trỏ id. Chạy `upgrade` lại sẽ tạo dãy mới
    # bắt đầu từ 1 và CẤP LẠI những `external_id` đã dùng — phá vỡ giả định A1.
    # Đừng downgrade một Mini CRM đang mang dữ liệu thật; nếu buộc phải, hãy
    # `setval` dãy về quá giá trị lớn nhất đã cấp ngay sau khi upgrade lại.
    op.execute(f"DROP SEQUENCE IF EXISTS {DEAL_SEQUENCE}")
    op.execute(f"DROP SEQUENCE IF EXISTS {UNIT_SEQUENCE}")
