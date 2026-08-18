"""`SyncRunService._terminal_status` — Phase 5.5 P0 (5A).

Thuần, không chạm DB: `_terminal_status` chỉ đếm ba số nguyên. Bug cũ coi đụng độ
ngang hàng với bản ghi hỏng (`blocked = rejected + conflicts`), nên một lô MỘT
bản ghi mà bản ghi đó là `conflict` (không có gì khác hỏng) báo `failed` — trong
khi đụng độ là một quyết định ĐÃ ghi nhận, không phải mất dữ liệu.
"""

from src.services.sync_runs import SyncRunService

status = SyncRunService()._terminal_status


def test_all_clean_is_completed():
    assert status(processed=3, rejected=0, conflicts=0) == "completed"


def test_a_single_record_pure_conflict_batch_is_not_failed():
    """Bug đã sửa: trước đây `processed=1, rejected=0, conflicts=1` → 'failed'."""
    assert status(processed=1, rejected=0, conflicts=1) == "completed_with_conflicts"


def test_conflicts_with_no_rejects_is_completed_with_conflicts_at_any_size():
    assert status(processed=5, rejected=0, conflicts=5) == "completed_with_conflicts"


def test_a_mix_of_rejects_and_anything_processed_is_partial():
    assert status(processed=1, rejected=1, conflicts=0) == "partially_completed"


def test_rejects_plus_only_conflicts_processed_is_still_partial_not_failed():
    """Đụng độ KHÔNG được tính là 'không đi qua được' khi xét thất bại toàn phần."""
    assert status(processed=1, rejected=1, conflicts=1) == "partially_completed"


def test_total_failure_requires_zero_processed():
    assert status(processed=0, rejected=2, conflicts=0) == "failed"


def test_stale_skip_alone_is_completed_not_a_special_status():
    """skip_stale nằm trong `processed`, không phải `rejected` — không phải lỗi."""
    assert status(processed=4, rejected=0, conflicts=0) == "completed"
