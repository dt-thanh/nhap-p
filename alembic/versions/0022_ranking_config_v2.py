"""ranking config v2: tách ĐIỀU KIỆN LỌC khỏi TÍN HIỆU ƯU TIÊN

Revision ID: 0022_ranking_config_v2
Revises: 0021_seed_ai_crm_fixture_deals
Create Date: 2026-08-16

DATA migration, không phải schema migration — không bảng/cột/ràng buộc nào bị
thêm, sửa, hay gỡ. Nó lưu trữ config v1 và phát hành v2 vào `ranking_configs`,
đúng con đường mà 0014 đã dựng sẵn (bảng CHỈ-THÊM; sửa trọng số của một dòng đã
`published` là làm mọi dòng `ranking_scores` cũ trỏ tới một config đã đổi nghĩa).

╔══════════════════════════════════════════════════════════════════════════════╗
║  VÌ SAO v1 KHÔNG DÙNG ĐƯỢC — số đo trên DB thật, 1.991 căn đã xếp hạng        ║
╚══════════════════════════════════════════════════════════════════════════════╝

    trạng thái   n      khoảng điểm        high   medium   low
    available   1261   0.7000 – 0.7592     1261      0       0
    reserved     365   0.0000 – 0.0592        0      0     365
    sold         365   0.0143 – 0.0592        0      0     365

1. **`band` chỉ là tên gọi khác của `units.status`.** Không căn nào chạm được
   `medium`; hai ngưỡng 0.33/0.66 của `src/ranking/bands.py` không với tới được.

2. **70% ngân sách trọng số không sinh ra thứ tự nào.** `unit_available` (0.50)
   và `has_active_deal` (0.20) tương quan ĐÚNG BẰNG -1.0 — chỉ 2 trong 4 ô có
   dữ liệu (available=1/deal=0: 1261 căn; available=0/deal=1: 730 căn). Đó không
   phải trùng hợp: 0021 CƯỠNG CHẾ bất biến "căn bị giữ ⟺ có deal đang giữ", nên
   `has_active_deal` là bản sao của `unit_available`. Cả hai lại là ĐIỀU KIỆN
   LỌC chứ không phải TÍN HIỆU ƯU TIÊN: khi đã lọc về tập căn bán được thì
   chúng là HẰNG SỐ — không đóng góp gì cho thứ tự, chỉ cộng thêm +0.70 cố định
   vào mọi điểm và làm hỏng mọi ngưỡng tuyệt đối đặt phía sau (`bands.py`, và
   `low_score_threshold = 0.5` của `src/agents/advisory_tools.py` — ngưỡng đó
   hiện KHÔNG BAO GIỜ khớp với 1.261 căn `available`).

3. **`rank_in_area` không mang tín hiệu nào.** Trong MỌI phân khu, các căn
   `available` có ĐÚNG 1 giá trị điểm phân biệt (58 phân khu → 58 giá trị), nên
   thứ hạng trong phân khu hoàn toàn do tie-break `created_at` (thứ tự soi
   gương) quyết định.

╔══════════════════════════════════════════════════════════════════════════════╗
║  v2                                                                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

`has_active_deal` bị BỎ khỏi bộ trọng số (không phải khỏi mã: `service.py` vẫn
tính nó, để rollback về v1 không biến nó thành MISSING). Bất biến mà nó từng
canh vẫn cần được canh — nhưng chỗ đúng cho một bất biến là một luật đối soát,
không phải 20% ngân sách trọng số của bộ xếp hạng.

`unit_demand_norm` VÀO: số deal đang trong phễu (`lead`/`qualified`/
`interested`/`viewing`) của chính căn đó, bão hoà ở 3. Đây là tín hiệu mức CĂN
duy nhất mà dữ liệu hiện có đỡ được, và là thứ duy nhất phá được thế hoà trong
phân khu ở mục 3. Phạm vi `unit_type` đã cân nhắc và LOẠI: 0/58 phân khu có
nhiều hơn một `unit_type`, nên nó sẽ trùng hoàn toàn với phạm vi `area`.

Đặc trưng khảo sát (`view_quality`…) VẪN chưa vào, đúng lý do 0014 đã ghi: chưa
có bộ tổng hợp nào sản xuất chúng.

Kèm theo (ở `src/ranking/service.py`, không ở đây): mẫu số của
`area_velocity_norm` đổi từ `areas.total_units` (quỹ hàng THEO KẾ HOẠCH) sang số
căn còn sống ĐÃ SOI GƯƠNG — sửa một lỗi thứ nguyên đã ép đặc trưng này nằm bẹp ở
trung bình 0.0099 (đo lại sau khi sửa: 0.1905).

MẤT MÁT ĐÃ BIẾT, ghi ở đây để không ai tưởng là lỗi: ràng buộc
`ck_ranking_configs_published_stamp` viết `(status='published') = (published_at
IS NOT NULL)` — ĐẲNG THỨC, không phải kéo theo. Nên lưu trữ một config BẮT BUỘC
phải xoá `published_at` của nó. Mốc phát hành gốc của v1 vì thế được chép sang
`note` trước khi bị xoá. Sửa ràng buộc thành dạng kéo theo là việc của một
revision khác, không gộp vào đây.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_ranking_config_v2"
down_revision: str | None = "0021_seed_ai_crm_fixture_deals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tổng trọng số PHẢI bằng 1.0 — cùng bất biến mà 0014 đặt ra cho v1.
V2_WEIGHTS = """{
  "unit_available":       {"weight": 0.35, "direction": "positive", "missing_value_policy": "zero",    "min_confidence": 0.0},
  "unit_demand_norm":     {"weight": 0.25, "direction": "positive", "missing_value_policy": "zero",    "min_confidence": 0.0},
  "area_velocity_norm":   {"weight": 0.20, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0.0},
  "area_conversion_norm": {"weight": 0.20, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0.0}
}"""

V2_NOTE = (
    "Config v2 — tách điều kiện lọc khỏi tín hiệu ưu tiên. Bỏ `has_active_deal` "
    "(tương quan -1.0 với `unit_available`, bất biến do 0021 cưỡng chế); hạ "
    "`unit_available` 0.50 → 0.35; thêm `unit_demand_norm` (deal đang trong phễu "
    "của chính căn, bão hoà 3) — tín hiệu mức căn duy nhất dữ liệu hiện có đỡ "
    "được; nâng `area_conversion_norm` 0.10 → 0.20. Xem docstring "
    "alembic/versions/0022_ranking_config_v2.py cho số đo."
)


def upgrade() -> None:
    bind = op.get_bind()

    published = bind.execute(
        sa.text("SELECT version, published_at FROM ranking_configs WHERE status = 'published'")
    ).mappings().all()
    if len(published) != 1 or published[0]["version"] != 1:
        # Không đoán: một config khác v1 đang phát hành nghĩa là ai đó đã đổi
        # trọng số bằng đường khác, và lưu trữ nó ở đây sẽ xoá quyết định đó.
        raise RuntimeError(
            "0022_ranking_config_v2: chờ ĐÚNG một config v1 đang 'published', "
            f"thấy {[(r['version']) for r in published]!r}. Dừng để không ghi đè "
            "một quyết định trọng số không phải của revision này."
        )

    # `published_at` BẮT BUỘC về NULL khi lưu trữ (ràng buộc là ĐẲNG THỨC — xem
    # docstring). Chép mốc gốc sang `note` để nó không biến mất khỏi lịch sử.
    op.execute(
        sa.text(
            "UPDATE ranking_configs SET "
            "  status = 'archived', "
            "  archived_at = now(), "
            "  published_at = NULL, "
            "  note = note || ' [Lưu trữ bởi 0022; phát hành gốc lúc ' "
            "                || COALESCE(published_at::text, 'không rõ') || '.]' "
            "WHERE version = 1 AND status = 'published'"
        )
    )

    op.execute(
        sa.text(
            "INSERT INTO ranking_configs "
            "(id, version, status, weights, min_weight_coverage, note, "
            " created_by, created_at, published_by, published_at) "
            "VALUES (gen_random_uuid(), 2, 'published', CAST(:weights AS jsonb), 0.5, :note, "
            "        'migration_0022', now(), 'migration_0022', now())"
        ).bindparams(weights=V2_WEIGHTS, note=V2_NOTE)
    )

    print("=== 0022_ranking_config_v2: v1 -> archived, v2 -> published ===")


def downgrade() -> None:
    # Thứ tự BẮT BUỘC: gỡ v2 khỏi tập `published` TRƯỚC, vì
    # `uq_ranking_configs_published` là partial unique index (kiểm tra ngay ở
    # từng câu lệnh, không hoãn tới cuối transaction được).
    op.execute(sa.text("DELETE FROM ranking_configs WHERE version = 2 AND created_by = 'migration_0022'"))
    op.execute(
        sa.text(
            "UPDATE ranking_configs SET "
            "  status = 'published', "
            "  archived_at = NULL, "
            "  published_at = now() "
            "WHERE version = 1 AND status = 'archived'"
        )
    )
