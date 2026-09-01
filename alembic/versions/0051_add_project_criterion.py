"""Register the one governed, rubric-assessed Project AHP criterion.

``project_design_score`` is an Expert-assessed measure of a project's
documented design quality, functional layout, and resident amenities.  It is
not a legal/compliance classification: ``project_legal_status`` remains a
separate pre-composition gate and is never present in a weighted hierarchy.

The existing rubric subsystem has a fixed, normalized five-band [0, 1] scale;
this revision seeds its v1 rubric with evidence requirements rather than
creating a competing 1--10 scale or a new scoring path.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0051_add_project_criterion"
down_revision: str | None = "0050_proposal_rationale_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
PROJECT_DESIGN_SCORE = "project_design_score"
SEEDED_BY = revision
RUBRIC_BANDS = (
    (Decimal("0.00"), "Thiết kế chưa đáp ứng nhu cầu ở cơ bản", "Hồ sơ thiết kế/thực địa xác nhận bất cập rõ rệt về công năng hoặc tiện ích."),
    (Decimal("0.25"), "Thiết kế cơ bản, tiện ích hạn chế", "Mặt bằng, thông số kỹ thuật hoặc khảo sát cho thấy chỉ đáp ứng nhu cầu tối thiểu."),
    (Decimal("0.50"), "Thiết kế phù hợp tiêu chuẩn phân khúc", "Hồ sơ quy hoạch, mặt bằng và danh mục tiện ích xác nhận mức đáp ứng trung bình."),
    (Decimal("0.75"), "Thiết kế tốt, công năng và tiện ích nổi bật", "Tài liệu chủ đầu tư kèm khảo sát/nguồn độc lập xác nhận lợi thế thiết kế hoặc tiện ích."),
    (Decimal("1.00"), "Thiết kế xuất sắc, khác biệt được kiểm chứng", "Hồ sơ thiết kế, nghiệm thu/khảo sát thực tế và ít nhất một nguồn độc lập xác nhận lợi thế khác biệt."),
)


def upgrade() -> None:
    now = datetime.now(UTC)
    feature_definitions = sa.table(
        "ranking_feature_definitions",
        sa.column("id", UUID), sa.column("feature_key", sa.Text()), sa.column("feature_version", sa.Text()),
        sa.column("name", sa.Text()), sa.column("category", sa.Text()), sa.column("grain", sa.Text()),
        sa.column("value_type", sa.Text()), sa.column("formula_id", sa.Text()), sa.column("normalization_method", sa.Text()),
        sa.column("direction", sa.Text()), sa.column("missing_policy", sa.Text()), sa.column("status", sa.Text()),
        sa.column("definition_metadata", JSONB), sa.column("created_by", sa.Text()),
        sa.column("created_at", sa.TIMESTAMP(timezone=True)), sa.column("updated_at", sa.TIMESTAMP(timezone=True)),
    )
    feature_id = uuid.uuid4()
    op.bulk_insert(feature_definitions, [{
        "id": feature_id, "feature_key": PROJECT_DESIGN_SCORE, "feature_version": "v1",
        "name": "Điểm chất lượng thiết kế dự án", "category": "expert", "grain": "project",
        "value_type": "numeric", "formula_id": "expert_value_assertion", "normalization_method": "rubric_band",
        "direction": "positive", "missing_policy": "neutral", "status": "active",
        "definition_metadata": {"assessment_basis": "design_layout_amenities", "max_shelf_life_days": 90},
        "created_by": SEEDED_BY, "created_at": now, "updated_at": now,
    }])

    rubrics = sa.table(
        "ranking_feature_rubrics", sa.column("id", UUID), sa.column("feature_definition_id", UUID),
        sa.column("rubric_version", sa.Integer()), sa.column("created_by", sa.Text()), sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    )
    rubric_id = uuid.uuid4()
    op.bulk_insert(rubrics, [{"id": rubric_id, "feature_definition_id": feature_id, "rubric_version": 1, "created_by": SEEDED_BY, "created_at": now}])
    bands = sa.table(
        "ranking_feature_rubric_bands", sa.column("id", UUID), sa.column("rubric_id", UUID),
        sa.column("band_value", sa.Numeric(5, 4)), sa.column("label", sa.Text()),
        sa.column("evidence_requirement", sa.Text()), sa.column("display_order", sa.Integer()),
    )
    op.bulk_insert(bands, [
        {"id": uuid.uuid4(), "rubric_id": rubric_id, "band_value": value, "label": label,
         "evidence_requirement": evidence_requirement, "display_order": index}
        for index, (value, label, evidence_requirement) in enumerate(RUBRIC_BANDS)
    ])


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade 0051: seeded rubric and bands are append-only governance records; "
        "create a replacement feature definition instead of deleting audit history."
    )
