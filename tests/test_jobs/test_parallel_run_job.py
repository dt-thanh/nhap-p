"""Job ghi lịch sử so sánh: kết quả trả về, và ranh giới "hỏng" của nó.

Job là hàm ĐỒNG BỘ (RQ gọi qua ranh giới tiến trình) nhưng bên trong dùng
`asyncio.run`, nên test async phải gọi nó ở thread khác — giống
`test_recompute_domain.py` và `test_domain_recompute_audit_job.py`.

Đường worker THẬT được kiểm riêng ở `test_parallel_run_worker.py`: RQ cài signal
handler nên nó phải chạy ở main thread, tức là một module đồng bộ.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.jobs.parallel_run import run_parallel_run_capture
from src.services.parallel_run import CaptureResult

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

PROJECT_ID = uuid.UUID("e1f2a3b4-c5d6-4748-895a-bcdef0123f1e")


def _result(*, matches=True, domain_has_data=True) -> CaptureResult:
    from datetime import UTC, datetime

    return CaptureResult(
        comparison_id=uuid.uuid4(),
        project_id=PROJECT_ID,
        compared_at=datetime.now(UTC),
        matches=matches,
        legacy_has_data=True,
        domain_has_data=domain_has_data,
        difference_count=0 if matches else 2,
        anomaly_count=0,
    )


@pytest.fixture(autouse=True)
def db_env(monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()
    yield
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


async def _run(**kwargs):
    return await asyncio.to_thread(lambda: run_parallel_run_capture(**kwargs))


async def test_capturing_one_project_reports_its_comparison_id():
    outcome = _result()
    with patch("src.jobs.parallel_run.ParallelRunCaptureService") as service:
        service.return_value.capture = AsyncMock(return_value=outcome)
        result = await _run(project_id=str(PROJECT_ID))

    assert result["status"] == "done"
    assert result["captured"] == 1
    assert result["comparison_ids"] == [str(outcome.comparison_id)]


async def test_no_project_id_captures_everything():
    with patch("src.jobs.parallel_run.ParallelRunCaptureService") as service:
        service.return_value.capture_all = AsyncMock(return_value=[_result(), _result()])
        result = await _run()

    assert result["captured"] == 2
    service.return_value.capture_all.assert_awaited_once()


async def test_mismatches_are_summarised_not_raised():
    """Hai bộ tính lệch nhau là KẾT QUẢ, không phải job hỏng — chạy lại không đổi
    được gì, và đánh nó thành failed sẽ khiến RQ retry một phép đo."""
    with patch("src.jobs.parallel_run.ParallelRunCaptureService") as service:
        service.return_value.capture_all = AsyncMock(return_value=[_result(matches=False), _result()])
        result = await _run()

    assert result["status"] == "done"
    assert result["mismatched"] == 1


async def test_projects_without_domain_data_are_counted_separately():
    """Số này là tín hiệu: chạy song song mà phần lớn dự án chưa có dữ liệu miền
    thì "khớp" chẳng chứng minh gì."""
    with patch("src.jobs.parallel_run.ParallelRunCaptureService") as service:
        service.return_value.capture_all = AsyncMock(return_value=[_result(domain_has_data=False), _result()])
        result = await _run()

    assert result["without_domain_data"] == 1


async def test_the_trigger_is_forwarded():
    with patch("src.jobs.parallel_run.ParallelRunCaptureService") as service:
        service.return_value.capture_all = AsyncMock(return_value=[])
        await _run(trigger="schedule")

    assert service.return_value.capture_all.call_args.kwargs["trigger"] == "schedule"


async def test_a_broken_run_raises_so_rq_shows_it(caplog):
    """Database không tới được là job hỏng thật — phải hiện ở failed registry, chứ
    không âm thầm coi như đã đo."""
    with patch("src.jobs.parallel_run.ParallelRunCaptureService", side_effect=OSError("db unreachable")):
        with caplog.at_level("ERROR"):
            with pytest.raises(OSError):
                await _run()

    assert "parallel_run.job_failed" in caplog.text


async def test_it_runs_against_the_real_database():
    """Không mock gì: đường thật phải chạy được trên schema thật."""
    result = await _run()

    assert result["status"] == "done"
    assert isinstance(result["captured"], int)
    assert result["duration_ms"] >= 0
