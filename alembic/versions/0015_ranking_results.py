"""kết quả xếp hạng: ranking_runs (chỉ-thêm) và ranking_scores (trạng thái hiện tại)

Revision ID: 0015_ranking_results
Revises: 0014_ranking_foundation
Create Date: 2026-08-11

Hai bảng, thuần CỘNG THÊM. Không bảng nào đang có bị sửa.

**Hai bảng, hai vòng đời khác hẳn nhau — và đó là điểm chính.**

`ranking_runs` là LỊCH SỬ VẬN HÀNH: chỉ-thêm, giữ mãi. Nó trả lời "lần chạy nào,
lúc nào, do cái gì kích hoạt, kết quả ra sao". Xoá một dòng ở đây là xoá bằng
chứng.

`ranking_scores` là TRẠNG THÁI HIỆN TẠI: đúng một dòng cho mỗi căn, ghi lại bằng
xoá-rồi-chèn theo phạm vi DỰ ÁN ở tầng ứng dụng (Phase 3+, không phải revision
này). Xoá-rồi-chèn chứ không upsert vì một căn vừa bị tombstone, hoặc vừa rơi
xuống dưới `min_weight_coverage`, phải BIẾN MẤT khỏi mô hình đọc — upsert sẽ để
lại một dòng ma mang thứ hạng của lần chạy trước. Cùng ý tưởng với
`DomainAbsorptionCalculatorService.persist()`.

**`uq_ranking_runs_queued_per_project` là chốt chống dồn.** Xếp hạng lại luôn ở
phạm vi TOÀN DỰ ÁN (vì `rank_in_project` dịch chuyển khi bất kỳ căn nào đổi
điểm), nên một trăm lô đồng bộ trong một phút mà sinh một trăm lần tính lại là
lụt hàng đợi để đổi lấy đúng một kết quả. Partial unique index buộc tối đa MỘT
run đang chờ cho mỗi dự án; bên xếp hàng dùng `ON CONFLICT DO UPDATE` để GỘP
phạm vi vào run đang chờ thay vì tạo run thứ hai. Ràng buộc nằm ở DB chứ không ở
trí nhớ người viết truy vấn.

**Vì sao `scope_type` chỉ nhận `'project'`.** Cột tồn tại để câu truy vấn sau này
không phải đoán, nhưng tập giá trị bị khoá lại đúng một phần tử: xếp hạng theo
phạm vi phân khu KHÔNG giữ được `rank_in_project` đúng, và một cột cho phép giá
trị mà hệ thống chưa xử lý được là một cột mời người ta dùng sai. Nới ra là việc
của một revision sau, khi có mã thật đứng sau nó.

**`sync_run_id` dùng `ON DELETE SET NULL`, không phải CASCADE.** Một lần dọn dẹp
`upload_files` cũ không được phép xoá lịch sử xếp hạng; mất liên kết ngược về lô
CRM là chấp nhận được, mất cả dòng thì không. Cùng lý do với
`sync_payloads.credential_id` (0009).

**`ranking_scores.unit_id` dùng `ON DELETE CASCADE`.** Ở đây thì ngược lại: một
dòng điểm cho một căn không còn tồn tại là dữ liệu vô nghĩa, không phải lịch sử.
Trên thực tế nhánh này gần như không bao giờ chạy — `units` bị xoá MỀM
(`deleted_at`), không xoá cứng.

**KHÔNG có UNIQUE trên `(project_id, rank_in_project)`.** Thứ hạng là duy nhất
trong một lần chạy hoàn chỉnh, nhưng lúc chèn lại cả dự án thì trạng thái trung
gian vi phạm nó, và một ràng buộc phải `DEFERRABLE` mới sống được chỉ để chặn
một lỗi mà chính tầng ghi đã không thể tạo ra. Cái giá không đáng.

Đường lùi: `ranking_scores` TRƯỚC, `ranking_runs` SAU — thứ tự bắt buộc, vì
`ranking_scores.ranking_run_id` trỏ vào `ranking_runs`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_ranking_results"
down_revision: str | None = "0014_ranking_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

RUN_TRIGGERS = ("sync", "config_change", "survey_snapshot", "manual", "audit_repair")
RUN_SCOPE_TYPES = ("project",)
RUN_OPEN_STATUSES = ("queued", "running")
RUN_TERMINAL_STATUSES = ("completed", "partially_completed", "failed", "skipped_stale")
RUN_STATUSES = RUN_OPEN_STATUSES + RUN_TERMINAL_STATUSES


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- 1. Vòng đời một lần xếp hạng — CHỈ-THÊM ----------------------------
    op.create_table(
        "ranking_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        # Nối một thứ hạng ngược về lô CRM đã gây ra nó. NULL với trigger khác.
        sa.Column("sync_run_id", UUID, nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False, server_default=sa.text("'project'")),
        # {"unit_ids": [...], "area_ids": [...]} — CHỈ để kiểm toán. Công việc
        # luôn là toàn dự án; dùng cái này để thu hẹp phạm vi sẽ làm hỏng
        # rank_in_project.
        sa.Column("scope_ids", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # NULL cho tới khi worker claim run và đọc được config đang phát hành.
        sa.Column("config_version_id", UUID, nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("units_processed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("units_ranked", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("units_skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enqueued_at", TS, nullable=False),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_runs"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_ranking_runs_project_id", ondelete="CASCADE"
        ),
        # SET NULL, không CASCADE: dọn `upload_files` cũ không được xoá lịch sử
        # xếp hạng.
        sa.ForeignKeyConstraint(
            ["sync_run_id"], ["upload_files.id"], name="fk_ranking_runs_sync_run_id", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["config_version_id"], ["ranking_configs.id"], name="fk_ranking_runs_config_version_id"
        ),
        sa.CheckConstraint(_in_list("trigger", RUN_TRIGGERS), name="ck_ranking_runs_trigger"),
        sa.CheckConstraint(_in_list("scope_type", RUN_SCOPE_TYPES), name="ck_ranking_runs_scope_type"),
        sa.CheckConstraint(_in_list("status", RUN_STATUSES), name="ck_ranking_runs_status"),
        sa.CheckConstraint("attempt >= 0", name="ck_ranking_runs_attempt_nonnegative"),
        sa.CheckConstraint("units_processed >= 0", name="ck_ranking_runs_processed_nonnegative"),
        sa.CheckConstraint("units_ranked >= 0", name="ck_ranking_runs_ranked_nonnegative"),
        sa.CheckConstraint("units_skipped >= 0", name="ck_ranking_runs_skipped_nonnegative"),
        # Xếp hạng + bỏ qua không được vượt quá số đã xử lý. Vượt nghĩa là bộ đếm
        # sai, và bộ đếm sai là thứ cổng cắt sang sau này sẽ đọc.
        sa.CheckConstraint(
            "units_ranked + units_skipped <= units_processed",
            name="ck_ranking_runs_counts_consistent",
        ),
        # Trạng thái mở thì chưa kết thúc; trạng thái kết thúc thì phải có mốc.
        # Soi gương `ck_forecast_jobs_finished_at_by_status` (0001).
        sa.CheckConstraint(
            f"({_in_list('status', RUN_OPEN_STATUSES)} AND finished_at IS NULL) "
            f"OR ({_in_list('status', RUN_TERMINAL_STATUSES)} AND finished_at IS NOT NULL)",
            name="ck_ranking_runs_finished_by_status",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_ranking_runs_time_order",
        ),
    )
    # CHỐNG DỒN: tối đa MỘT run đang chờ mỗi dự án — xem docstring module.
    op.create_index(
        "uq_ranking_runs_queued_per_project",
        "ranking_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'queued'"),
    )
    # "Lần chạy gần nhất của dự án này" là câu hỏi thường xuyên nhất về bảng này.
    op.create_index(
        "ix_ranking_runs_project_enqueued",
        "ranking_runs",
        ["project_id", sa.text("enqueued_at DESC")],
    )
    op.create_index("ix_ranking_runs_sync_run_id", "ranking_runs", ["sync_run_id"])

    # --- 2. Điểm + thứ hạng — TRẠNG THÁI HIỆN TẠI ---------------------------
    op.create_table(
        "ranking_scores",
        sa.Column("id", UUID, nullable=False),
        sa.Column("unit_id", UUID, nullable=False),
        # Phi chuẩn hoá cho đường đọc: mọi truy vấn xếp hạng lọc theo dự án hoặc
        # phân khu, và bắt chúng join qua `units` mỗi lần là trả giá cho một sự
        # thật không bao giờ đổi (một căn không chuyển dự án).
        sa.Column("area_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        # Nguồn gốc ĐẦY ĐỦ trên MỖI dòng: lần chạy nào, bộ trọng số nào.
        sa.Column("ranking_run_id", UUID, nullable=False),
        sa.Column("config_version_id", UUID, nullable=False),
        sa.Column("score", sa.Numeric(6, 4), nullable=False),
        sa.Column("rank_in_area", sa.Integer(), nullable=False),
        sa.Column("rank_in_project", sa.Integer(), nullable=False),
        # Phần trọng số THỰC SỰ có mặt. Điểm 0.8 dựng từ 50% trọng số không cùng
        # nghĩa với điểm 0.8 dựng từ 100%, và đường đọc phải phân biệt được.
        sa.Column("weight_coverage", sa.Numeric(5, 4), nullable=False),
        # {feature_key: {value, weight, direction, contribution, source, resolved_from}}
        # Giải thích được mà không phải dựng lại toàn bộ phép tính.
        sa.Column("contributions", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # min(calculated_at) của các đặc trưng đã dùng — sàn độ tươi của dòng này.
        sa.Column("feature_freshness_at", TS, nullable=True),
        # = ranking_runs.started_at. Một lần đọc đồng hồ cho cả lần chạy, nên mọi
        # dòng của cùng lần chạy so sánh được với nhau — cùng mẫu với
        # `absorption_daily.computed_at` + `computation_id` (0012).
        sa.Column("computed_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ranking_scores"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_ranking_scores_unit_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_ranking_scores_area_id"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_ranking_scores_project_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["ranking_run_id"], ["ranking_runs.id"], name="fk_ranking_scores_ranking_run_id"),
        sa.ForeignKeyConstraint(
            ["config_version_id"], ["ranking_configs.id"], name="fk_ranking_scores_config_version_id"
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_ranking_scores_score_range"),
        sa.CheckConstraint("rank_in_area > 0", name="ck_ranking_scores_rank_in_area_positive"),
        sa.CheckConstraint("rank_in_project > 0", name="ck_ranking_scores_rank_in_project_positive"),
        sa.CheckConstraint(
            "weight_coverage >= 0 AND weight_coverage <= 1",
            name="ck_ranking_scores_coverage_range",
        ),
    )
    # ĐÚNG MỘT điểm hiện hành cho mỗi căn. Đây là thứ biến bảng này thành "trạng
    # thái hiện tại" thay vì "lịch sử".
    op.create_index("uq_ranking_scores_unit", "ranking_scores", ["unit_id"], unique=True)
    # Hai đường đọc: bảng xếp hạng toàn dự án, và bảng xếp hạng trong một phân khu.
    op.create_index("ix_ranking_scores_project_rank", "ranking_scores", ["project_id", "rank_in_project"])
    op.create_index("ix_ranking_scores_area_rank", "ranking_scores", ["area_id", "rank_in_area"])


def downgrade() -> None:
    # THỨ TỰ BẮT BUỘC: `ranking_scores` trỏ vào `ranking_runs`, nên nó phải đi
    # trước. Đảo lại thì lệnh vỡ giữa chừng.
    #
    # LƯU Ý VẬN HÀNH: mất toàn bộ thứ hạng và lịch sử vận hành xếp hạng. Cả hai
    # dựng lại được bằng cách chạy lại job (điểm là dẫn xuất hoàn toàn từ
    # `units`/`deals`/`feature_snapshots` + config), nhưng lịch sử "lần chạy nào
    # xảy ra lúc nào" thì mất hẳn. Dữ liệu nghiệp vụ KHÔNG bị ảnh hưởng.
    op.drop_index("ix_ranking_scores_area_rank", table_name="ranking_scores")
    op.drop_index("ix_ranking_scores_project_rank", table_name="ranking_scores")
    op.drop_index("uq_ranking_scores_unit", table_name="ranking_scores")
    op.drop_table("ranking_scores")

    op.drop_index("ix_ranking_runs_sync_run_id", table_name="ranking_runs")
    op.drop_index("ix_ranking_runs_project_enqueued", table_name="ranking_runs")
    op.drop_index("uq_ranking_runs_queued_per_project", table_name="ranking_runs")
    op.drop_table("ranking_runs")
