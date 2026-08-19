"""Phase 6 mở đầu: agent_recommendations — đề xuất tư vấn của AI Agent, chờ duyệt

Revision ID: 0018_agent_recommendations
Revises: 0017_hierarchy_projection
Create Date: 2026-08-13

Một bảng, thuần CỘNG THÊM. Không bảng nào đang có bị sửa, không cột nào bị đổi.

═══════════════════════════════════════════════════════════════════════════════
 PHASE 6 BẮT ĐẦU TỪ ĐÂY — `tests/test_ranking_boundary.py` được viết lại theo
 hiện thực mới trong cùng đợt này (xem pipeline_status.md đợt tương ứng).
 `docs/ranking/implementation_plan.md` mô tả một động cơ xếp hạng đầy đủ (worker
 RQ, ma trận cò kích hoạt, endpoint khảo sát) — migration này và
 `src/ranking/` KHÔNG dựng lại toàn bộ kiến trúc đó. Nó dựng đúng một lát cắt
 dọc tối thiểu: một service `run_ranking()` chạy ĐỒNG BỘ bên trong request
 `POST /api/v1/agent/recommendations` (không hàng đợi, không worker nền), dùng
 ĐÚNG công thức tính điểm ở §10.1 của tài liệu trên và ĐÚNG bốn đặc trưng vận
 hành của config v1 (§5.2). Phần còn lại (worker RQ, cò kích hoạt sau sync, API
 đọc xếp hạng độc lập, endpoint khảo sát) vẫn CHƯA làm — ghi rõ ở đây để không
 ai đọc lướt rồi tưởng đã đủ.
═══════════════════════════════════════════════════════════════════════════════

**Vì sao `agent_recommendations` chứ không phải một cột status trên
`ranking_runs`.** Một lần xếp hạng có thể sinh ra NHIỀU đề xuất tư vấn khác nhau
theo thời gian (hỏi lại, hỏi phạm vi phân khu khác) — vòng đời "duyệt/từ chối"
là của TỪNG đề xuất, không phải của lần tính điểm đứng sau nó. Gắn `status` vào
`ranking_runs` sẽ buộc một lần chạy chỉ có đúng một quyết định duyệt.

**`status` khởi tạo LUÔN `'pending_approval'`, cưỡng chế ở tầng ứng dụng
(`src/api/agent.py`), không có server_default nào khác được chấp nhận cho INSERT
đầu tiên.** Đây là bước duyệt mà `AGENTS.md` yêu cầu cứng: "Every recommendation
this agent produces must pass through a human-in-the-loop approval step before
it is treated as final." Không có đường ghi thẳng `'approved'`.

**`ranking_run_id` KHÔNG có FOREIGN KEY tới `ranking_runs`.** `ranking_scores`/
`ranking_runs` đã có ràng buộc riêng của Phase 2 (0015); thêm FK từ bảng MỚI
này vào đó là an toàn về mặt dữ liệu nhưng không bắt buộc cho lát cắt tối thiểu
hiện tại, và một FK sai hướng (ví dụ trỏ nhầm) sẽ chặn migrate thay vì chặn ở
tầng ứng dụng nơi lỗi dễ thấy hơn. Ghi nhận ở đây để revision sau có thể thêm
nếu cần.

**`decided_by`/`decided_at`/`decision_reason` cùng NULL hay cùng có giá trị.**
Không dùng CHECK constraint cho bất biến này — tầng ứng dụng (`approve`/`reject`)
là nơi duy nhất ghi ba cột này, cùng một câu UPDATE.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_agent_recommendations"
down_revision: str | None = "0017_hierarchy_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

RECOMMENDATION_STATUSES = ("pending_approval", "approved", "rejected")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "agent_recommendations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("area_id", UUID, nullable=True),
        sa.Column("ranking_run_id", UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending_approval'")),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("recommended_actions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("generated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_recommendations"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_agent_recommendations_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["area_id"],
            ["areas.id"],
            name="fk_agent_recommendations_area_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in_list("status", RECOMMENDATION_STATUSES), name="ck_agent_recommendations_status"),
        sa.CheckConstraint("summary <> ''", name="ck_agent_recommendations_summary_not_blank"),
        sa.CheckConstraint(
            "(status = 'pending_approval' AND decided_by IS NULL AND decided_at IS NULL) OR "
            "(status IN ('approved', 'rejected') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_agent_recommendations_decision_consistency",
        ),
    )
    op.create_index(
        "ix_agent_recommendations_project_id",
        "agent_recommendations",
        ["project_id", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_recommendations_project_id", table_name="agent_recommendations")
    op.drop_table("agent_recommendations")
