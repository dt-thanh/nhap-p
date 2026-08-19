"""nền xếp hạng: feature_snapshots, ranking_configs, và config v1 vận hành

Revision ID: 0014_ranking_foundation
Revises: 0013_calculator_comparisons
Create Date: 2026-08-11

Hai bảng, thuần CỘNG THÊM. Không bảng nào đang có bị sửa, không cột nào bị đổi,
không dòng nào bị đụng. `units`, `deals`, `areas`, `projects`, `absorption_daily`
và toàn bộ nhóm bảng tổng hợp cũ giữ nguyên từng byte.

**Vì sao `feature_snapshots` mang `project_id`.** Phạm vi `unit_type` chỉ là một
CHUỖI (`units.unit_type`, ví dụ `'2PN'`), không phải khoá ngoại. Không có
`project_id` trong khoá danh tính thì đặc trưng `view_quality` của loại `2PN` ở
dự án A sẽ ghi đè lên chính nó ở dự án B — hai dự án khác nhau, cùng một dòng.
Phạm vi `unit`/`area` không gặp chuyện này vì UUID vốn đã duy nhất toàn cục;
nhưng khoá danh tính phải đúng cho CẢ BA phạm vi, không chỉ hai.

**Vì sao `scope_id` là TEXT chứ không phải UUID.** Nó phải chứa được hai loại giá
trị khác nhau: UUID dạng chuỗi (phạm vi `unit`/`area`) và chuỗi `unit_type`
nguyên văn. Ép kiểu UUID là loại bỏ phạm vi thứ ba. Cái giá: không có khoá ngoại
nào cưỡng chế được `scope_id`, nên tầng ứng dụng phải tự giữ. Đánh đổi có chủ
đích, ghi ở đây để lần sau không ai "sửa" nó thành UUID.

**`feature_snapshots` là TRẠNG THÁI HIỆN TẠI, không phải lịch sử.** Một đặc trưng
lỗi thời được GHI ĐÈ, không được đánh dấu xoá. Không có bảng lịch sử đặc trưng ở
giai đoạn này: chưa có ai đọc nó, và một bảng chỉ-để-sau là một bảng phải bảo trì
mà không đổi lại được gì. Tầng ghi sẽ dùng
`ON CONFLICT ... WHERE excluded.calculated_at > feature_snapshots.calculated_at`
để một ảnh chụp CŨ không bao giờ đè lên ảnh chụp mới hơn — ràng buộc đó thuộc về
câu lệnh, không phải schema, nên nó không xuất hiện ở đây.

**`ranking_configs` là CHỈ-THÊM.** `weights` của một dòng đã `published` không bao
giờ được UPDATE. Sửa tại chỗ sẽ khiến mọi dòng `ranking_scores` cũ trỏ tới một
config đã đổi nghĩa, và không lần chạy nào giải thích lại được. Rollback là CHÉP
trọng số cũ sang một `version` MỚI (`copied_from_version`), không phải sửa lịch sử
— cùng nguyên tắc với `calculator_comparisons` (0013) và `reconciliation_findings`
(0011).

**Đúng MỘT config đang phát hành**, cưỡng chế bằng partial unique index trên
`status WHERE status = 'published'` — cùng ý tưởng với `uq_deals_active_per_unit`
(0007). Để tầng ứng dụng tự nhớ quy tắc này là để nó bị quên đúng vào lúc hai
config cùng sống và không ai biết bộ tính đang đọc cái nào.

**Config v1 CHỈ có đặc trưng VẬN HÀNH.** Bốn đặc trưng, tổng trọng số 1.0, tất cả
suy được từ `units`/`deals`/`areas` đang có. Đặc trưng khảo sát (`view_quality`,
`natural_light`, `privacy`, `noise_level`) KHÔNG có mặt, và đây không phải chuyện
bỏ sót: chưa có bộ tổng hợp nào sản xuất chúng, nên mọi giá trị sẽ MISSING, chính
sách `skip` sẽ loại chúng khỏi mẫu số, `coverage` tụt xuống dưới
`min_weight_coverage`, và **mọi căn bị bỏ qua — không thứ hạng nào được sinh ra**.
Một config trông đầy đủ mà cho ra bảng rỗng là kiểu hỏng tệ nhất ở đây. Đặc trưng
khảo sát vào ở version 2, khi đã có dữ liệu thật để chúng đọc.

`days_on_market` cũng KHÔNG có mặt: nó cần `units.listed_at`, một trường chưa tồn
tại. `units.created_at` là lúc BẮT ĐẦU SOI GƯƠNG, không phải lúc mở bán — dùng nó
là bịa ra ý nghĩa cho một con số.

Đường lùi đối xứng hoàn toàn: không bảng nào khác tham chiếu tới hai bảng này ở
revision này, nên `downgrade()` chỉ việc gỡ chúng. Cái mất: toàn bộ lịch sử config
và mọi giá trị đặc trưng khảo sát đã nhập. Dữ liệu nghiệp vụ không bị đụng tới.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_ranking_foundation"
down_revision: str | None = "0013_calculator_comparisons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

FEATURE_SCOPES = ("unit", "area", "unit_type")
FEATURE_SOURCES = ("operational", "survey_external")
CONFIG_STATUSES = ("draft", "published", "archived")

# Config v1 — CHỈ đặc trưng vận hành. Xem docstring module về việc vì sao đặc
# trưng khảo sát không có ở đây. Tổng trọng số PHẢI bằng 1.0.
SEED_WEIGHTS = """{
  "unit_available":       {"weight": 0.50, "direction": "positive", "missing_value_policy": "zero",    "min_confidence": 0.0},
  "has_active_deal":      {"weight": 0.20, "direction": "negative", "missing_value_policy": "zero",    "min_confidence": 0.0},
  "area_velocity_norm":   {"weight": 0.20, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0.0},
  "area_conversion_norm": {"weight": 0.10, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0.0}
}"""


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- 1. Giá trị đặc trưng đã chuẩn hoá -----------------------------------
    op.create_table(
        "feature_snapshots",
        sa.Column("id", UUID, nullable=False),
        # Cô lập theo dự án. Bắt buộc cho phạm vi `unit_type` — xem docstring.
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("feature_key", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        # TEXT, không phải UUID: phải chứa được cả uuid dạng chuỗi lẫn chuỗi
        # unit_type nguyên văn.
        sa.Column("scope_id", sa.Text(), nullable=False),
        # MỌI đặc trưng đã chuẩn hoá về [0,1] TRƯỚC khi vào đây. Chuẩn hoá ở tầng
        # đọc sẽ khiến hai bộ tính khác nhau cho ra hai thang đo khác nhau trên
        # cùng một cột.
        sa.Column("feature_value", sa.Numeric(6, 4), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("feature_version", sa.Text(), nullable=False),
        # Mốc ĐỘ TƯƠI của giá trị, và là căn cứ để một ảnh chụp cũ không đè lên
        # ảnh chụp mới hơn.
        sa.Column("calculated_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feature_snapshots"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_feature_snapshots_project_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("feature_key <> ''", name="ck_feature_snapshots_key_not_blank"),
        sa.CheckConstraint(_in_list("scope", FEATURE_SCOPES), name="ck_feature_snapshots_scope"),
        sa.CheckConstraint("scope_id <> ''", name="ck_feature_snapshots_scope_id_not_blank"),
        sa.CheckConstraint(
            "feature_value >= 0 AND feature_value <= 1",
            name="ck_feature_snapshots_value_range",
        ),
        sa.CheckConstraint(
            "sample_count IS NULL OR sample_count >= 0",
            name="ck_feature_snapshots_sample_count_nonnegative",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_feature_snapshots_confidence_range",
        ),
        sa.CheckConstraint(_in_list("source", FEATURE_SOURCES), name="ck_feature_snapshots_source"),
        sa.CheckConstraint("feature_version <> ''", name="ck_feature_snapshots_version_not_blank"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_feature_snapshots_updated_after_created"),
    )
    # Khoá upsert. `project_id` đứng đầu vì mọi truy vấn đều lọc theo dự án trước.
    op.create_index(
        "uq_feature_snapshots_identity",
        "feature_snapshots",
        ["project_id", "feature_key", "scope", "scope_id"],
        unique=True,
    )
    # Đường đọc của job xếp hạng: nạp mọi đặc trưng của một phạm vi trong một lượt.
    op.create_index(
        "ix_feature_snapshots_project_scope",
        "feature_snapshots",
        ["project_id", "scope", "scope_id"],
    )

    # --- 2. Trọng số xếp hạng, có version -----------------------------------
    op.create_table(
        "ranking_configs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        # {feature_key: {weight, direction, missing_value_policy, min_confidence}}
        sa.Column("weights", JSONB, nullable=False),
        # Dưới ngưỡng này thì căn bị BỎ QUA thay vì nhận một điểm dựng từ quá ít
        # đặc trưng. Nằm ở config chứ không phải hằng số trong mã: nó là quyết
        # định nghiệp vụ, và nó phải đổi được cùng lúc với bộ trọng số.
        sa.Column("min_weight_coverage", sa.Numeric(5, 4), nullable=False, server_default=sa.text("0.5")),
        sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")),
        # Dấu vết rollback: version này chép trọng số từ version nào.
        sa.Column("copied_from_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("published_by", sa.Text(), nullable=True),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("archived_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_configs"),
        sa.UniqueConstraint("version", name="uq_ranking_configs_version"),
        sa.CheckConstraint("version > 0", name="ck_ranking_configs_version_positive"),
        sa.CheckConstraint(_in_list("status", CONFIG_STATUSES), name="ck_ranking_configs_status"),
        sa.CheckConstraint("weights <> '{}'::jsonb", name="ck_ranking_configs_weights_not_empty"),
        sa.CheckConstraint(
            "min_weight_coverage > 0 AND min_weight_coverage <= 1",
            name="ck_ranking_configs_coverage_range",
        ),
        sa.CheckConstraint("created_by <> ''", name="ck_ranking_configs_created_by_not_blank"),
        # Trạng thái và mốc thời gian không được mâu thuẫn nhau. Một dòng
        # `published` mà không có `published_at` là một dòng không truy được thời
        # điểm — và `ranking_scores` trỏ vào nó thì cũng không giải thích được.
        sa.CheckConstraint(
            "(status = 'published') = (published_at IS NOT NULL)",
            name="ck_ranking_configs_published_stamp",
        ),
        sa.CheckConstraint(
            "(status = 'archived') = (archived_at IS NOT NULL)",
            name="ck_ranking_configs_archived_stamp",
        ),
    )
    # ĐÚNG MỘT config đang phát hành. Partial unique: nhiều `draft` và nhiều
    # `archived` vẫn hợp lệ, chỉ `published` là duy nhất.
    op.create_index(
        "uq_ranking_configs_published",
        "ranking_configs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )

    # --- 3. Config v1 -------------------------------------------------------
    # Seed trong migration chứ không ở script riêng: không có config nào đang
    # phát hành thì job xếp hạng không chạy được, nên "database đã migrate" phải
    # đồng nghĩa với "hệ thống dùng được".
    op.execute(
        sa.text(
            "INSERT INTO ranking_configs "
            "(id, version, status, weights, min_weight_coverage, note, "
            " created_by, created_at, published_by, published_at) "
            "VALUES (gen_random_uuid(), 1, 'published', CAST(:weights AS jsonb), 0.5, :note, "
            "        'migration_0014', now(), 'migration_0014', now())"
        ).bindparams(
            weights=SEED_WEIGHTS,
            note=(
                "Config v1 — CHỈ đặc trưng vận hành, tổng trọng số 1.0. "
                "Đặc trưng khảo sát vào ở v2 khi có bộ tổng hợp thật; đưa vào bây giờ sẽ "
                "khiến coverage tụt dưới ngưỡng và MỌI căn bị bỏ qua."
            ),
        )
    )


def downgrade() -> None:
    # LƯU Ý VẬN HÀNH: gỡ hai bảng này XOÁ toàn bộ lịch sử config xếp hạng và mọi
    # giá trị đặc trưng khảo sát đã nhập từ bên ngoài. Đặc trưng VẬN HÀNH dựng lại
    # được từ `units`/`deals`; đặc trưng KHẢO SÁT thì không — chúng chỉ có một
    # đường vào là bộ tổng hợp bên ngoài.
    #
    # Dữ liệu nghiệp vụ (`units`, `deals`, `areas`, `absorption_daily`, nhóm bảng
    # tổng hợp cũ) KHÔNG bị ảnh hưởng.
    op.drop_index("uq_ranking_configs_published", table_name="ranking_configs")
    op.drop_table("ranking_configs")

    op.drop_index("ix_feature_snapshots_project_scope", table_name="feature_snapshots")
    op.drop_index("uq_feature_snapshots_identity", table_name="feature_snapshots")
    op.drop_table("feature_snapshots")
