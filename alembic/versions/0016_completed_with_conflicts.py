"""nới ck_upload_files_status: thêm 'completed_with_conflicts'

Revision ID: 0016_completed_with_conflicts
Revises: 0015_ranking_results
Create Date: 2026-08-12

Phase 5.5 P0 (Bước 5A). `SyncRunService._terminal_status` coi đụng độ là bản
ghi HỎNG (`blocked = rejected + conflicts`) — một lô một bản ghi mà bản ghi đó
là `conflict` (không có gì khác hỏng) báo `status='failed'`, dù không mất dữ
liệu và không có lỗi hệ thống nào. Sửa lại yêu cầu một trạng thái kết thúc THỨ
NĂM để phân biệt "có đụng độ nhưng không có bản ghi nào hỏng" khỏi cả `completed`
lẫn `failed`/`partially_completed` — xem `src/services/sync_runs.py`.

Migration NÀY LÀ BẮT BUỘC, không phải tuỳ chọn: `status` được ghi qua CHECK
constraint `ck_upload_files_status`, giới hạn đúng năm giá trị cũ. Không nới nó
thì `UPDATE upload_files SET status='completed_with_conflicts'` NỔ NGAY ở lô đầu
tiên có đụng độ mà không có bản ghi hỏng nào khác — đã kiểm chứng bằng
`CheckViolationError` thật khi chạy `tests/test_api/test_sync_idempotency.py`.

CHỈ nới constraint — không đổi cột, không đổi bảng khác, không cần backfill: dữ
liệu hiện có chỉ mang năm giá trị cũ, vẫn hợp lệ với danh sách mới (siêu tập).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_completed_with_conflicts"
down_revision: str | None = "0015_ranking_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = ("pending", "processing", "completed", "partially_completed", "failed")
NEW_STATUSES = ("pending", "processing", "completed", "completed_with_conflicts", "partially_completed", "failed")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.drop_constraint("ck_upload_files_status", "upload_files", type_="check")
    op.create_check_constraint("ck_upload_files_status", "upload_files", _in_list("status", NEW_STATUSES))


def downgrade() -> None:
    # An toàn CÓ ĐIỀU KIỆN: lùi về constraint cũ sẽ NỔ nếu còn dòng nào mang giá
    # trị 'completed_with_conflicts'. Không tự ý xoá/đổi dữ liệu ở downgrade —
    # đó là quyết định của người vận hành, không phải của migration.
    op.drop_constraint("ck_upload_files_status", "upload_files", type_="check")
    op.create_check_constraint("ck_upload_files_status", "upload_files", _in_list("status", OLD_STATUSES))
