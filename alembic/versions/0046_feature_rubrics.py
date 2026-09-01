"""Versioned evidence-to-value rubrics for qualitative (value-mode) feature
assertions — the genuinely-missing layer identified by a live audit of this
session: every numeric value-mode assertion today (`ranking_feature_justifications
.normalized_numeric`) is a raw, free-form Decimal in [0,1] with no graded,
named-meaning scale anywhere in the codebase or design docs (grep-verified
across src/ and docs/ranking/ before writing this migration).

Two new append-only tables (same `ranking_governance_append_only_guard`
trigger every other governance table already uses — a rubric version, once
created, is never edited in place; a revision is a NEW version):

- `ranking_feature_rubrics` — one row per (feature_definition_id, version).
  "Current" rubric for a feature = the row with the highest `rubric_version`
  for that feature — same "latest row wins" pattern already used for
  `ranking_evidence_extraction_attempts`/`ranking_evidence_document_lifecycle_events`,
  so no mutable `status` column is needed or possible.
- `ranking_feature_rubric_bands` — exactly five rows per rubric, at the fixed
  canonical band values 0.00/0.25/0.50/0.75/1.00 (matching the band structure
  the owning mission specified), each with a label and an evidence
  requirement string.

`ranking_feature_justifications` gains two new nullable columns:
`rubric_id` (which rubric VERSION the expert graded against) and
`rubric_band_value` (which of that rubric's five band values they selected).
Both NULL for weight-mode rows and for any value-mode assertion on a feature
that does not require a rubric. `ck_rfj_rubric_pair` enforces both-or-neither.
`src/services/governance.py::upsert_justification()` is the only place that
writes them, and it always DERIVES `normalized_numeric` from the selected
band server-side — a client can select a band, never dictate an arbitrary
numeric value once a rubric is in play.

Seed data: real, versioned (v1), evidence-gated rubrics for the six
already-registered MVP qualitative features (`market_interest_rate`,
`market_demand`, `market_credit_policy`, `area_accessibility`,
`area_current_infrastructure`, `area_future_infrastructure`) — the exact set
this session's task named as the recommended default MVP. Explicitly
DISCLOSED, not silently assumed authoritative: this is a first, reasonable
default policy (mirroring the same honesty already established for
`scripts/enable_hierarchical_ranking.py`'s illustrative grain weights) — the
band text has not been business-approved. `RUBRIC_REQUIRED_FEATURE_KEYS` in
`src/services/governance.py` is the single source of truth for which keys
require a rubric at submission time; `market_liquidity` (the 7th registered
Market feature) is deliberately NOT in that set or seeded here, staying
free-form numeric, per this session's explicit scope decision.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0046_feature_rubrics"
down_revision: str | None = "0045_lifecycle_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)

SEEDED_BY = "0046_feature_rubrics"

BAND_VALUES = (Decimal("0.00"), Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))

# feature_key -> (rubric title context) -> 5 (label, evidence_requirement) pairs,
# in band order 0.00 -> 1.00. Vietnamese, evidence-gated, disclosed-default text.
RUBRIC_BANDS: dict[str, tuple[tuple[str, str], ...]] = {
    "market_interest_rate": (
        ("Bất lợi rõ rệt — lãi suất tăng, không có tín hiệu ưu đãi",
         "Công văn/thông báo tăng lãi suất từ ngân hàng hoặc NHNN"),
        ("Ổn định ở mức cao — chưa có chính sách ưu đãi nào",
         "Bảng lãi suất công bố hiện hành của ngân hàng thương mại"),
        ("Trung bình — có một số ưu đãi ngắn hạn",
         "Chương trình ưu đãi lãi suất có thời hạn dưới 12 tháng"),
        ("Thuận lợi — lãi suất giảm rõ rệt hoặc gói ưu đãi dài hạn",
         "Quyết định giảm lãi suất điều hành hoặc gói vay ưu đãi trên 12 tháng"),
        ("Rất thuận lợi — mức thấp lịch sử, nhiều ngân hàng cạnh tranh",
         "Dữ liệu so sánh lãi suất liên ngân hàng kèm báo cáo thị trường"),
    ),
    "market_demand": (
        ("Không có tín hiệu nhu cầu đáng tin cậy / đang suy giảm",
         "Báo cáo giao dịch cho thấy xu hướng giảm, không có nguồn đối chứng"),
        ("Tín hiệu yếu, chưa được xác nhận",
         "Một nguồn duy nhất, chưa qua đối chiếu"),
        ("Ổn định, ở mức trung bình",
         "Báo cáo thị trường quý gần nhất, một nguồn có uy tín"),
        ("Tăng trưởng nhu cầu đã được xác thực",
         "Từ hai nguồn độc lập trở lên cùng xác nhận xu hướng tăng"),
        ("Nhu cầu rất mạnh, xác thực bởi nhiều nguồn độc lập",
         "Từ ba nguồn độc lập trở lên, bao gồm ít nhất một báo cáo chính thức"),
    ),
    "market_credit_policy": (
        ("Bất lợi rõ rệt — siết tín dụng bất động sản",
         "Văn bản/thông tư siết room tín dụng hoặc điều kiện vay bất động sản"),
        ("Thận trọng — điều kiện vay hiện hành chặt hơn trung bình",
         "Bảng điều kiện vay công bố của ngân hàng thương mại"),
        ("Trung tính — không có thay đổi đáng kể",
         "Không phát hiện văn bản/chính sách thay đổi trong kỳ đánh giá"),
        ("Nới lỏng — điều kiện vay được nới, room tín dụng tăng",
         "Văn bản nới room tín dụng hoặc hạ điều kiện vay từ NHNN/ngân hàng"),
        ("Rất thuận lợi — chính sách hỗ trợ tín dụng bất động sản rõ rệt",
         "Chính sách hỗ trợ chính thức (lãi suất/room) áp dụng trực tiếp cho phân khúc"),
    ),
    "area_accessibility": (
        ("Kết nối kém, không có tuyến giao thông chính gần khu vực",
         "Không tìm thấy tuyến đường/hạ tầng kết nối chính trong bán kính khảo sát"),
        ("Kết nối hạn chế, phụ thuộc một tuyến duy nhất",
         "Bản đồ/khảo sát thực địa cho thấy một tuyến kết nối duy nhất"),
        ("Kết nối trung bình, có tuyến chính nhưng chưa thuận tiện",
         "Tuyến đường chính hiện hữu, thời gian di chuyển trung bình tới trung tâm"),
        ("Kết nối tốt, nhiều tuyến giao thông thuận tiện",
         "Từ hai tuyến kết nối chính trở lên, có xác nhận thời gian di chuyển thực tế"),
        ("Kết nối rất tốt, đầu mối giao thông đa dạng",
         "Xác nhận bằng khảo sát thực địa hoặc báo cáo quy hoạch giao thông chính thức"),
    ),
    "area_current_infrastructure": (
        ("Hạ tầng thiếu/xuống cấp, không có tiện ích thiết yếu gần khu vực",
         "Khảo sát thực địa hoặc ảnh vệ tinh không cho thấy tiện ích thiết yếu"),
        ("Hạ tầng tối thiểu, thiếu một số tiện ích thiết yếu",
         "Khảo sát thực địa xác nhận thiếu ít nhất một tiện ích thiết yếu (y tế/giáo dục/chợ)"),
        ("Hạ tầng đầy đủ ở mức cơ bản",
         "Khảo sát thực địa xác nhận đủ tiện ích thiết yếu trong bán kính hợp lý"),
        ("Hạ tầng tốt, có tiện ích thương mại/dịch vụ đa dạng",
         "Khảo sát thực địa hoặc báo cáo khu vực xác nhận tiện ích thương mại đa dạng"),
        ("Hạ tầng hoàn thiện, tiện ích cao cấp/đầy đủ mọi mặt",
         "Báo cáo khu vực chính thức hoặc khảo sát thực địa chi tiết xác nhận"),
    ),
    "area_future_infrastructure": (
        ("Không có bằng chứng quy hoạch hạ tầng tương lai đáng tin cậy",
         "Không tìm thấy quy hoạch/chủ trương nào; hoặc có bằng chứng bất lợi"),
        ("Mới ở mức đề xuất/chủ trương ban đầu",
         "Văn bản đề xuất hoặc chủ trương chính sách chưa được phê duyệt"),
        ("Đã có quy hoạch/kế hoạch được phê duyệt chính thức",
         "Quyết định phê duyệt quy hoạch hoặc kế hoạch đầu tư công đã ban hành"),
        ("Đang triển khai — có hợp đồng thi công/mốc thực hiện xác nhận",
         "Hợp đồng thi công, giấy phép xây dựng, hoặc mốc tiến độ đã xác nhận"),
        ("Đã vận hành/hoàn thành, có lợi ích tiếp cận quan sát được",
         "Nghiệm thu/vận hành chính thức, xác nhận bằng khảo sát thực địa"),
    ),
}


def _has_rows(table: str, where: str | None = None) -> bool:
    bind = op.get_bind()
    clause = f"WHERE {where}" if where else ""
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} {clause})")).scalar())


def upgrade() -> None:
    op.create_table(
        "ranking_feature_rubrics",
        sa.Column("id", UUID, nullable=False),
        sa.Column("feature_definition_id", UUID, nullable=False),
        sa.Column("rubric_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_feature_rubrics"),
        sa.ForeignKeyConstraint(
            ["feature_definition_id"], ["ranking_feature_definitions.id"], name="fk_rfr_feature_definition_id"
        ),
        sa.UniqueConstraint("feature_definition_id", "rubric_version", name="uq_rfr_feature_version"),
        sa.CheckConstraint("rubric_version > 0", name="ck_rfr_version_positive"),
        sa.CheckConstraint("created_by <> ''", name="ck_rfr_created_by_not_blank"),
    )
    op.create_index("ix_rfr_feature_definition_id", "ranking_feature_rubrics", ["feature_definition_id"])
    op.execute(
        sa.text(
            "CREATE TRIGGER ranking_feature_rubrics_append_only_guard "
            "BEFORE UPDATE OR DELETE ON ranking_feature_rubrics "
            "FOR EACH ROW EXECUTE FUNCTION ranking_governance_append_only_guard()"
        )
    )

    op.create_table(
        "ranking_feature_rubric_bands",
        sa.Column("id", UUID, nullable=False),
        sa.Column("rubric_id", UUID, nullable=False),
        sa.Column("band_value", sa.Numeric(5, 4), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("evidence_requirement", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_feature_rubric_bands"),
        sa.ForeignKeyConstraint(
            ["rubric_id"], ["ranking_feature_rubrics.id"], name="fk_rfrb_rubric_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("rubric_id", "band_value", name="uq_rfrb_rubric_band_value"),
        sa.UniqueConstraint("rubric_id", "display_order", name="uq_rfrb_rubric_display_order"),
        sa.CheckConstraint("band_value >= 0 AND band_value <= 1", name="ck_rfrb_band_value_range"),
        sa.CheckConstraint("label <> ''", name="ck_rfrb_label_not_blank"),
        sa.CheckConstraint("evidence_requirement <> ''", name="ck_rfrb_evidence_requirement_not_blank"),
    )
    op.create_index("ix_rfrb_rubric_id", "ranking_feature_rubric_bands", ["rubric_id"])
    op.execute(
        sa.text(
            "CREATE TRIGGER ranking_feature_rubric_bands_append_only_guard "
            "BEFORE UPDATE OR DELETE ON ranking_feature_rubric_bands "
            "FOR EACH ROW EXECUTE FUNCTION ranking_governance_append_only_guard()"
        )
    )

    op.add_column("ranking_feature_justifications", sa.Column("rubric_id", UUID, nullable=True))
    op.add_column("ranking_feature_justifications", sa.Column("rubric_band_value", sa.Numeric(5, 4), nullable=True))
    op.create_foreign_key(
        "fk_rfj_rubric_id", "ranking_feature_justifications", "ranking_feature_rubrics", ["rubric_id"], ["id"]
    )
    op.create_check_constraint(
        "ck_rfj_rubric_pair",
        "ranking_feature_justifications",
        "(rubric_id IS NULL) = (rubric_band_value IS NULL)",
    )

    now = datetime.now(UTC)
    bind = op.get_bind()
    rubrics_table = sa.table(
        "ranking_feature_rubrics",
        sa.column("id", UUID),
        sa.column("feature_definition_id", UUID),
        sa.column("rubric_version", sa.Integer()),
        sa.column("created_by", sa.Text()),
        sa.column("created_at", TS),
    )
    bands_table = sa.table(
        "ranking_feature_rubric_bands",
        sa.column("id", UUID),
        sa.column("rubric_id", UUID),
        sa.column("band_value", sa.Numeric(5, 4)),
        sa.column("label", sa.Text()),
        sa.column("evidence_requirement", sa.Text()),
        sa.column("display_order", sa.Integer()),
    )
    for feature_key, bands in RUBRIC_BANDS.items():
        feature_id = bind.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = :key"), {"key": feature_key}
        ).scalar()
        if feature_id is None:
            raise RuntimeError(
                f"0046_feature_rubrics: expected ranking_feature_definitions row for '{feature_key}' "
                "(seeded by 0040/0041) to already exist — it does not"
            )
        rubric_id = uuid.uuid4()
        op.bulk_insert(
            rubrics_table,
            [{"id": rubric_id, "feature_definition_id": feature_id, "rubric_version": 1, "created_by": SEEDED_BY, "created_at": now}],
        )
        op.bulk_insert(
            bands_table,
            [
                {
                    "id": uuid.uuid4(),
                    "rubric_id": rubric_id,
                    "band_value": band_value,
                    "label": label,
                    "evidence_requirement": evidence_requirement,
                    "display_order": index,
                }
                for index, (band_value, (label, evidence_requirement)) in enumerate(zip(BAND_VALUES, bands))
            ],
        )


def downgrade() -> None:
    if _has_rows("ranking_feature_justifications", "rubric_id IS NOT NULL"):
        raise RuntimeError("Refusing to downgrade 0046: ranking_feature_justifications rows reference a rubric")
    if _has_rows("ranking_feature_rubrics", f"created_by <> '{SEEDED_BY}'"):
        raise RuntimeError("Refusing to downgrade 0046: ranking_feature_rubrics rows beyond this migration's own seed exist")

    op.drop_constraint("ck_rfj_rubric_pair", "ranking_feature_justifications", type_="check")
    op.drop_constraint("fk_rfj_rubric_id", "ranking_feature_justifications", type_="foreignkey")
    op.drop_column("ranking_feature_justifications", "rubric_band_value")
    op.drop_column("ranking_feature_justifications", "rubric_id")

    op.execute(sa.text("DROP TRIGGER ranking_feature_rubric_bands_append_only_guard ON ranking_feature_rubric_bands"))
    op.drop_table("ranking_feature_rubric_bands")
    op.execute(sa.text("DROP TRIGGER ranking_feature_rubrics_append_only_guard ON ranking_feature_rubrics"))
    op.drop_table("ranking_feature_rubrics")
