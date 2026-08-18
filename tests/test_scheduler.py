"""Scheduler đăng ký đúng job, vào đúng hàng đợi, theo đúng cron.

Đây là loại mã không ai nhận ra là hỏng: nếu job kiểm không được đăng ký, mọi thứ
khác vẫn chạy bình thường, không có lỗi nào, và lưới an toàn đơn giản là không
tồn tại. Nên nó được kiểm bằng cách đọc thẳng danh sách job của APScheduler.

Scheduler KHÔNG được khởi động ở đây — `BlockingScheduler.start()` chiếm luôn
thread. Chỉ dựng và đọc.
"""

from __future__ import annotations

import pytest

# APScheduler chỉ có trong image (requirements.txt), không có trong .venv cục bộ.
# Bỏ qua thay vì làm hỏng cả lượt thu thập — nhưng file này PHẢI được chạy trong
# container, nếu không thì nó không kiểm gì cả.
pytest.importorskip("apscheduler", reason="apscheduler chỉ có trong image — chạy file này trong container")

from src.scheduler import (  # noqa: E402
    build_scheduler,
    enqueue_domain_recompute_audit,
    enqueue_parallel_run_capture,
)


class FakeJob:
    id = "fake-audit-job"


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, func_name, **kwargs):
        self.calls.append({"func": func_name, **kwargs})
        return FakeJob()


@pytest.fixture(autouse=True)
def fresh_settings():
    from src.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_every_job_is_registered_by_default():
    scheduler = build_scheduler()

    ids = {job.id for job in scheduler.get_jobs()}
    assert ids == {"daily_forecast", "domain_recompute_audit", "parallel_run_capture"}


def test_audit_job_can_be_switched_off(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("DOMAIN_RECOMPUTE_AUDIT_ENABLED", "false")
    get_settings.cache_clear()

    scheduler = build_scheduler()

    ids = {job.id for job in scheduler.get_jobs()}
    assert "domain_recompute_audit" not in ids
    assert "daily_forecast" in ids


def test_audit_cron_comes_from_configuration(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("DOMAIN_RECOMPUTE_AUDIT_CRON", "*/30 * * * *")
    get_settings.cache_clear()

    scheduler = build_scheduler()

    job = scheduler.get_job("domain_recompute_audit")
    assert "minute='*/30'" in str(job.trigger)


def test_missed_runs_collapse_into_one():
    """Worker chết vài tiếng rồi sống lại không được bắn một loạt lần kiểm cùng lúc."""
    scheduler = build_scheduler()

    job = scheduler.get_job("domain_recompute_audit")
    assert job.coalesce is True
    assert job.max_instances == 1


def test_audit_goes_to_the_ingest_queue(monkeypatch):
    """Không phải hàng đợi forecast: một câu SQL không được xếp sau job Prophet."""
    fake = FakeQueue()
    names: list[str] = []

    def capture(name=None):
        names.append(name)
        return fake

    monkeypatch.setattr("src.scheduler.get_queue", capture)

    enqueue_domain_recompute_audit()

    assert names == ["ingest"]
    assert fake.calls[0]["func"] == "src.jobs.domain_recompute_audit.run_domain_recompute_audit"


def test_repair_flag_is_passed_to_the_job(monkeypatch):
    from src.config import get_settings

    fake = FakeQueue()
    monkeypatch.setattr("src.scheduler.get_queue", lambda name=None: fake)
    monkeypatch.setenv("DOMAIN_RECOMPUTE_AUDIT_REPAIR", "false")
    get_settings.cache_clear()

    enqueue_domain_recompute_audit()

    assert fake.calls[0]["repair"] is False


def test_scheduler_never_touches_the_database(monkeypatch):
    """Tiến trình scheduler chỉ đẩy job. Chạm DB ở đây sẽ thêm một lý do để nó chết."""
    import src.db as db_module

    def explode():
        raise AssertionError("scheduler không được mở kết nối database")

    monkeypatch.setattr(db_module, "get_engine", explode)
    monkeypatch.setattr("src.scheduler.get_queue", lambda name=None: FakeQueue())

    build_scheduler()
    enqueue_domain_recompute_audit()


# --- Chạy song song (Phase 8D) ------------------------------------------------


def test_parallel_run_capture_can_be_switched_off(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("PARALLEL_RUN_CAPTURE_ENABLED", "false")
    get_settings.cache_clear()

    ids = {job.id for job in build_scheduler().get_jobs()}
    assert "parallel_run_capture" not in ids


def test_parallel_run_cron_comes_from_configuration(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("PARALLEL_RUN_CAPTURE_CRON", "0 5 * * *")
    get_settings.cache_clear()

    job = build_scheduler().get_job("parallel_run_capture")
    assert "hour='5'" in str(job.trigger)


def test_parallel_run_missed_runs_collapse_into_one():
    job = build_scheduler().get_job("parallel_run_capture")

    assert job.coalesce is True
    assert job.max_instances == 1


def test_parallel_run_capture_goes_to_the_ingest_queue(monkeypatch):
    fake = FakeQueue()
    names: list[str] = []

    def capture(name=None):
        names.append(name)
        return fake

    monkeypatch.setattr("src.scheduler.get_queue", capture)

    enqueue_parallel_run_capture()

    assert names == ["ingest"]
    assert fake.calls[0]["func"] == "src.jobs.parallel_run.run_parallel_run_capture"


def test_scheduled_capture_covers_every_project(monkeypatch):
    """`project_id=None` = mọi dự án. Đặt một dự án cụ thể ở đây sẽ khiến lịch sử
    chạy song song chỉ có một dự án, và không ai nhận ra."""
    fake = FakeQueue()
    monkeypatch.setattr("src.scheduler.get_queue", lambda name=None: fake)

    enqueue_parallel_run_capture()

    assert fake.calls[0]["project_id"] is None
    assert fake.calls[0]["trigger"] == "schedule"
