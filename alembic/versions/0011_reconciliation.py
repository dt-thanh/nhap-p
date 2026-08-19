"""đối soát: reconciliation_runs, reconciliation_findings, và metadata ảnh chụp

Revision ID: 0011_reconciliation
Revises: 0010_sync_payload_retention
Create Date: 2026-08-09

Ba nhóm thay đổi, đều CỘNG THÊM — không sửa, không xoá gì của giai đoạn trước.

**1. `reconciliation_runs`** — mỗi lần chạy đối soát là một dòng. Có bảng riêng
chứ không nhét vào log vì kết quả đối soát là thứ phải TRA CỨU LẠI được: điều
kiện cắt sang bộ tính mới (quyết định 8) là ba lần chạy thành công liên tiếp trên
dữ liệu CRM thật, và "ba lần liên tiếp" chỉ đếm được nếu từng lần được lưu.

**2. `reconciliation_findings`** — từng phát hiện, ở dạng MÁY ĐỌC ĐƯỢC. Mỗi dòng
mang `check_code`, `severity`, và `details` (JSONB) đủ để dựng lại vấn đề mà
không phải mở log. Một báo cáo đối soát chỉ nói "có 3 chênh lệch" là vô dụng:
người đọc cần biết chênh ở ĐÂU, và máy cần so được hai lần chạy với nhau.

`passed` được suy ra từ `error_count = 0`, và cột đó có ràng buộc CHECK để hai
giá trị không bao giờ mâu thuẫn nhau. Không để tầng ứng dụng tự nhớ quy tắc này:
"đạt" là kết luận quan trọng nhất của cả bảng.

**3. Metadata ảnh chụp trên `upload_files`.** Hợp đồng v1 đã định nghĩa
`snapshot_id`, `chunk_index`, `chunk_total`, `snapshot_complete`, `scope` từ
Phase 2, nhưng đường nạp CHƯA BAO GIỜ giữ lại chúng — adapter bỏ nguyên khối
`snapshot`, parser không có trường tương ứng, và không cột nào lưu. Hệ quả:
`sync_mode=full_snapshot` được nhận rồi cư xử y hệt `incremental`.

Không có mấy cột này thì chốt an toàn xoá theo ảnh chụp KHÔNG THỂ tồn tại: nó
phải trả lời "đã nhận đủ mảnh chưa" và "phạm vi ảnh chụp là gì", mà cả hai câu
đều không có dữ liệu để trả lời. Đây là hiện thực hoá thứ hợp đồng đã hứa, không
phải mở rộng hợp đồng.

Chọn đặt cột trên `upload_files` thay vì dựng bảng `sync_snapshots` riêng: mỗi lô
CHÍNH LÀ một mảnh, nên quan hệ là 1-1 và một bảng nữa chỉ thêm một phép join.
Trạng thái đủ/thiếu mảnh của cả ảnh chụp suy ra được bằng cách gom nhóm theo
`snapshot_id` — xem `SnapshotGate.completeness()`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_reconciliation"
down_revision: str | None = "0010_sync_payload_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)
JSONB = postgresql.JSONB

# Phạm vi một lần chạy đối soát.
#   internal      — chỉ dùng DB của chính ta: bản sao có tự nhất quán không.
#   snapshot      — so với một ảnh chụp đầy đủ do hệ nguồn gửi.
#   source        — so với số liệu hệ nguồn công bố. CHƯA DÙNG ĐƯỢC: chưa có CRM.
RECONCILIATION_SCOPES = ("internal", "snapshot", "source")

RECONCILIATION_STATUSES = ("running", "completed", "failed")

# `error` chặn việc "đạt"; `warning` phải kèm chi tiết nhưng không chặn; `info`
# là số liệu nền để đọc báo cáo.
SEVERITIES = ("info", "warning", "error")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- 1. Lần chạy đối soát ------------------------------------------------
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=True),
        sa.Column("source_instance_id", sa.Text(), nullable=True),
        # Ảnh chụp được đối soát, nếu scope = 'snapshot'.
        sa.Column("snapshot_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        # `passed` là kết luận; ràng buộc bên dưới buộc nó khớp `error_count`.
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("info_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("checks_run", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_runs"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_reconciliation_runs_project_id"),
        sa.CheckConstraint(_in_list("scope", RECONCILIATION_SCOPES), name="ck_reconciliation_runs_scope"),
        sa.CheckConstraint(_in_list("status", RECONCILIATION_STATUSES), name="ck_reconciliation_runs_status"),
        sa.CheckConstraint("error_count >= 0", name="ck_reconciliation_runs_error_count_nonnegative"),
        sa.CheckConstraint("warning_count >= 0", name="ck_reconciliation_runs_warning_count_nonnegative"),
        # Quy tắc "đạt" nằm ở DB chứ không chỉ ở tầng ứng dụng: một lần chạy có
        # lỗi mà vẫn ghi passed=true là kiểu sai lệch âm thầm nguy hiểm nhất ở
        # đây — nó sẽ được dùng làm bằng chứng để cắt sang bộ tính mới.
        sa.CheckConstraint(
            "(passed AND error_count = 0) OR (NOT passed AND error_count >= 0)",
            name="ck_reconciliation_runs_passed_requires_no_errors",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_reconciliation_runs_finished_after_started",
        ),
        # Chỉ scope='snapshot' mới được mang snapshot_id.
        sa.CheckConstraint(
            "scope = 'snapshot' OR snapshot_id IS NULL",
            name="ck_reconciliation_runs_snapshot_id_scope",
        ),
    )
    op.create_index(
        "ix_reconciliation_runs_project_started",
        "reconciliation_runs",
        ["project_id", "started_at"],
    )

    # --- 2. Phát hiện, dạng máy đọc được -------------------------------------
    op.create_table(
        "reconciliation_findings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("reconciliation_run_id", UUID, nullable=False),
        sa.Column("check_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        # Thực thể và danh tính liên quan — để lọc và để lần ra bản ghi cụ thể.
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("area_id", UUID, nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        # Chi tiết đủ để dựng lại vấn đề mà không cần mở log. BẮT BUỘC không rỗng
        # với severity='warning' — xem CHECK bên dưới.
        sa.Column("details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_findings"),
        sa.ForeignKeyConstraint(
            ["reconciliation_run_id"],
            ["reconciliation_runs.id"],
            name="fk_reconciliation_findings_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in_list("severity", SEVERITIES), name="ck_reconciliation_findings_severity"),
        sa.CheckConstraint("check_code <> ''", name="ck_reconciliation_findings_check_code_not_blank"),
        sa.CheckConstraint("message <> ''", name="ck_reconciliation_findings_message_not_blank"),
        # "Cảnh báo phải kèm chi tiết" là yêu cầu nghiệp vụ, nên đặt ở DB. Cảnh
        # báo trống nghĩa là ai đó phải đi tìm lại từ đầu, và thường là không tìm.
        sa.CheckConstraint(
            "severity <> 'warning' OR details <> '{}'::jsonb",
            name="ck_reconciliation_findings_warning_needs_details",
        ),
    )
    op.create_index(
        "ix_reconciliation_findings_run_severity",
        "reconciliation_findings",
        ["reconciliation_run_id", "severity"],
    )
    op.create_index("ix_reconciliation_findings_check_code", "reconciliation_findings", ["check_code"])

    # --- 3. Metadata ảnh chụp trên bảng lô -----------------------------------
    # Hợp đồng v1 đã định nghĩa những trường này từ Phase 2; tới đây chúng mới
    # thực sự được giữ lại. Tất cả đều NULL được: lô `incremental` không có ảnh
    # chụp, và điền giá trị giả cho chúng sẽ là nói sai.
    op.add_column("upload_files", sa.Column("snapshot_id", sa.Text(), nullable=True))
    op.add_column("upload_files", sa.Column("chunk_index", sa.Integer(), nullable=True))
    op.add_column("upload_files", sa.Column("chunk_total", sa.Integer(), nullable=True))
    op.add_column("upload_files", sa.Column("snapshot_complete", sa.Boolean(), nullable=True))
    op.add_column("upload_files", sa.Column("snapshot_scope", JSONB, nullable=True))

    op.create_check_constraint(
        "ck_upload_files_chunk_index_nonnegative",
        "upload_files",
        "chunk_index IS NULL OR chunk_index >= 0",
    )
    op.create_check_constraint(
        "ck_upload_files_chunk_total_positive",
        "upload_files",
        "chunk_total IS NULL OR chunk_total > 0",
    )
    # Mảnh phải nằm trong tổng số mảnh. `chunk_index` đếm từ 0.
    op.create_check_constraint(
        "ck_upload_files_chunk_index_within_total",
        "upload_files",
        "chunk_index IS NULL OR chunk_total IS NULL OR chunk_index < chunk_total",
    )
    # Có ảnh chụp thì phải có đủ bộ ba; thiếu một mảnh thông tin là không dùng được.
    op.create_check_constraint(
        "ck_upload_files_snapshot_fields_together",
        "upload_files",
        "snapshot_id IS NULL "
        "OR (chunk_index IS NOT NULL AND chunk_total IS NOT NULL AND snapshot_complete IS NOT NULL)",
    )

    # Một mảnh của một ảnh chụp chỉ được nhận đúng một lần trong cùng một nguồn.
    # Nhận hai lần cùng chunk_index sẽ làm phép đếm "đủ mảnh chưa" sai theo hướng
    # nguy hiểm: tưởng đủ trong khi còn thiếu, rồi xoá nhầm.
    op.create_index(
        "uq_upload_files_snapshot_chunk",
        "upload_files",
        ["source_system", "source_instance_id", "snapshot_id", "chunk_index"],
        unique=True,
        postgresql_where=sa.text("snapshot_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Đối xứng hoàn toàn. LƯU Ý VẬN HÀNH: gỡ hai bảng này XOÁ TOÀN BỘ LỊCH SỬ ĐỐI
    # SOÁT, tức là mất bằng chứng cho điều kiện cắt sang bộ tính mới (ba lần chạy
    # thành công liên tiếp). Dữ liệu nghiệp vụ không bị ảnh hưởng.
    op.drop_index("uq_upload_files_snapshot_chunk", table_name="upload_files")
    for name in (
        "ck_upload_files_snapshot_fields_together",
        "ck_upload_files_chunk_index_within_total",
        "ck_upload_files_chunk_total_positive",
        "ck_upload_files_chunk_index_nonnegative",
    ):
        op.drop_constraint(name, "upload_files", type_="check")
    for column in ("snapshot_scope", "snapshot_complete", "chunk_total", "chunk_index", "snapshot_id"):
        op.drop_column("upload_files", column)

    op.drop_index("ix_reconciliation_findings_check_code", table_name="reconciliation_findings")
    op.drop_index("ix_reconciliation_findings_run_severity", table_name="reconciliation_findings")
    op.drop_table("reconciliation_findings")

    op.drop_index("ix_reconciliation_runs_project_started", table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
