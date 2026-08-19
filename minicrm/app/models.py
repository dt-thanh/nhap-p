"""Bảng SQLAlchemy Core của Mini CRM.

Tiền tố `crm_` trên MỌI bảng. Không phải để tránh trùng tên — hai database vốn đã
tách hẳn nhau — mà để khi đọc log, đọc dump hay đọc một câu truy vấn chẩn đoán,
không ai phải dừng lại tự hỏi mình đang nhìn bảng của hệ nào.

Bản chiếu của `minicrm/alembic/versions/0001_minicrm_initial.py`. Migration là
nguồn sự thật; file này chỉ để code gọi tên cột.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

metadata = sa.MetaData()

# Dự án. Mini CRM là NGUỒN SỰ THẬT (Phase B, đảo ngược từ mô hình "backend sở hữu"
# — xem docs/crm/phase_a_domain_freeze.md §S). Backend chỉ soi gương qua đường
# nhận; bảng này KHÔNG có cột nào cho việc đó (mirrored_*) tới khi Phase C nối
# outbox — ba cột đó có mặt SẴN, cùng khuôn với crm_units/crm_deals, để Phase C
# không cần thêm một migration riêng chỉ để có chúng.
crm_projects = sa.Table(
    "crm_projects",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    # Danh tính gửi sang backend (từ Phase C). BỀN VỮNG TRỌN ĐỜI, không bao giờ
    # dùng lại — giả định A1 của hợp đồng, áp dụng cho CẢ BỐN tầng ở v2.
    sa.Column("external_id", sa.Text(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("launch_date", sa.Date(), nullable=False),
    # 'active' | 'archived'. KHÔNG có 'pending'/'rejected' — không có quy trình
    # duyệt nào ở Mini CRM cho dự án của chính nó.
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("source_revision", sa.BigInteger(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
    sa.Column("mirrored_at", TS, nullable=True),
    sa.Column("mirrored_revision", sa.BigInteger(), nullable=True),
    sa.Column("last_sync_batch_id", sa.Text(), nullable=True),
)

# Phân khu. Mini CRM sở hữu CẢ NĂM trường nghiệp vụ, kể cả ba trường kế hoạch
# (bedrooms/area_sqm/total_units) — KHÔNG có tiền tố `proposed_`, KHÔNG có bước
# duyệt. Xem phase_a_domain_freeze.md §A1.2.
crm_areas = sa.Table(
    "crm_areas",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("external_id", sa.Text(), nullable=False),
    sa.Column("project_id", UUID, nullable=False),
    sa.Column("area_name", sa.Text(), nullable=False),
    sa.Column("unit_type", sa.Text(), nullable=False),
    sa.Column("bedrooms", sa.Integer(), nullable=False),
    sa.Column("area_sqm", sa.Numeric(), nullable=False),
    # MẪU SỐ của tỷ lệ hấp thụ. Số KẾ HOẠCH do Mini CRM công bố — KHÔNG BAO GIỜ
    # suy ra bằng cách đếm crm_units.
    sa.Column("total_units", sa.Integer(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("source_revision", sa.BigInteger(), nullable=False),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
    sa.Column("mirrored_at", TS, nullable=True),
    sa.Column("mirrored_revision", sa.BigInteger(), nullable=True),
    sa.Column("last_sync_batch_id", sa.Text(), nullable=True),
)

# Căn hộ theo cách Mini CRM hiểu. KHÔNG có giá, không có khách hàng, không có
# nhân viên bán — hợp đồng đồng bộ v1 không cần chúng, và thêm vào là mời PII vào
# một hệ thống chưa có tầng bảo vệ nào.
crm_units = sa.Table(
    "crm_units",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    # Danh tính gửi sang backend. BỀN VỮNG TRỌN ĐỜI, không bao giờ dùng lại —
    # kể cả sau khi tombstone (giả định A1 của hợp đồng).
    sa.Column("external_id", sa.Text(), nullable=False),
    # Tham chiếu CHUẨN tới crm_areas (Phase B). NULLABLE: 163 căn tạo TRƯỚC
    # Phase B không có phân khu cục bộ nào để trỏ vào — di sản, không bị đụng
    # tới. Căn tạo/sửa TỪ Phase B trở đi LUÔN có area_id.
    sa.Column("area_id", UUID, nullable=True),
    # BẢN SAO đã chốt tại lần ghi gần nhất — GIỮ NGUYÊN để không đổi hình dạng
    # phong bì v1 (`area_ref: {area_name, unit_type}`) và để 163 dòng di sản vẫn
    # đọc được. Với căn có `area_id`, hai cột này được LÀM TƯƠI từ `crm_areas`
    # ở MỌI lần ghi (create/update/delete) — xem `crud._resolve_area_reference`.
    # KHÔNG BAO GIỜ sửa trực tiếp qua API; nguồn sự thật là `crm_areas`.
    sa.Column("area_name", sa.Text(), nullable=False),
    sa.Column("unit_type", sa.Text(), nullable=False),
    sa.Column("unit_code", sa.Text(), nullable=False),
    sa.Column("unit_status", sa.Text(), nullable=False),
    # Tăng MỖI lần ghi, kể cả xoá. Đây là căn cứ duy nhất backend dùng để xếp thứ
    # tự; không dùng đồng hồ.
    sa.Column("source_revision", sa.BigInteger(), nullable=False),
    sa.Column("deleted_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
    # Phiên bản CAO NHẤT mà backend đã nhận. NULL = chưa bao giờ lên tới nơi.
    # `mirrored_revision < source_revision` = có thay đổi cục bộ chưa đồng bộ.
    sa.Column("mirrored_at", TS, nullable=True),
    sa.Column("mirrored_revision", sa.BigInteger(), nullable=True),
    sa.Column("last_sync_batch_id", sa.Text(), nullable=True),
)

# Giao dịch. Ràng buộc SOI GƯƠNG đúng ràng buộc của backend (0007) để Mini CRM
# không bao giờ sinh ra payload mà backend chắc chắn từ chối — bắt lỗi ở đây rẻ
# hơn nhiều so với bắt nó ở đầu kia của một request HTTP.
crm_deals = sa.Table(
    "crm_deals",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("external_id", sa.Text(), nullable=False),
    sa.Column("external_unit_id", sa.Text(), nullable=False),
    sa.Column("deal_status", sa.Text(), nullable=False),
    sa.Column("reserved_at", TS, nullable=True),
    sa.Column("sold_at", TS, nullable=True),
    sa.Column("lost_at", TS, nullable=True),
    sa.Column("source_revision", sa.BigInteger(), nullable=False),
    sa.Column("deleted_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.Column("updated_at", TS, nullable=False),
    sa.Column("mirrored_at", TS, nullable=True),
    sa.Column("mirrored_revision", sa.BigInteger(), nullable=True),
    sa.Column("last_sync_batch_id", sa.Text(), nullable=True),
)

# Nhật ký gửi đi, giữ NGUYÊN VĂN payload và phản hồi.
#
# Đây là thứ khiến hai kịch bản của Phase 4 demo được bằng thao tác thật thay vì
# bằng lời: gửi lại đúng lô cũ (cùng `external_batch_id` ⇒ backend trả
# `replayed=true`), và gửi một bản cũ (revision thấp hơn ⇒ `skip_stale`). Không
# giữ payload thì cả hai chỉ còn là mô tả.
crm_outbox = sa.Table(
    "crm_outbox",
    metadata,
    sa.Column("id", UUID, primary_key=True),
    sa.Column("external_batch_id", sa.Text(), nullable=False),
    sa.Column("entity", sa.Text(), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    # NULL cho tới khi có phản hồi. Một dòng `http_status IS NULL` nghĩa là lô đã
    # được ghi nhận nhưng chưa biết kết quả — timeout rơi vào đúng trạng thái này,
    # và đó là thông tin, không phải lỗi.
    sa.Column("http_status", sa.Integer(), nullable=True),
    sa.Column("response", JSONB, nullable=True),
    sa.Column("sent_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False),
    # Gửi lại TƯỜNG MINH vẫn phải để lại vết: không có cột này, gửi lại năm lần
    # trông y hệt gửi một lần.
    sa.Column("attempts", sa.Integer(), nullable=False),
    # Lỗi TRUYỀN TẢI (timeout, không nối được) không có mã HTTP nào để ghi.
    sa.Column("last_error", sa.Text(), nullable=True),
    # Lô này là bản phát lại của lô nào. Phát lại bản CŨ buộc phải mang batch id
    # MỚI (xem docstring migration 0002), nên không có cột này thì không truy
    # ngược được về lô gốc.
    sa.Column("replay_of", sa.Text(), nullable=True),
)

# --- Dãy sinh `external_id` (migration 0002, 0003) --------------------------
# Không bao giờ lùi, không bao giờ cấp trùng, kể cả khi transaction gọi nó bị
# rollback. Id bị bỏ phí thì không sao; id bị dùng lại thì hỏng vĩnh viễn.
UNIT_SEQUENCE = "crm_unit_external_seq"
DEAL_SEQUENCE = "crm_deal_external_seq"
PROJECT_SEQUENCE = "crm_project_external_seq"
AREA_SEQUENCE = "crm_area_external_seq"
