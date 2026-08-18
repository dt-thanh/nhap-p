"""Job kiểm định kỳ: cái gì là "kết quả" và cái gì là "job hỏng".

Ranh giới này quan trọng vì RQ retry theo exception. `stale_projects > 0` là KẾT
QUẢ CHẨN ĐOÁN — chạy lại không đổi được gì, nên nó không được ném. Còn database
không với tới được là JOB HỎNG — một lần kiểm âm thầm không chạy tệ hơn không có
lần kiểm nào, vì nó tạo cảm giác đang được canh.

Job là hàm ĐỒNG BỘ (RQ gọi nó qua ranh giới tiến trình) nhưng bên trong dùng
`asyncio.run`, nên test async phải gọi nó trong một thread khác — giống hệt
`test_recompute_domain.py`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest

from src.jobs.domain_recompute_audit import run_domain_recompute_audit
from src.services.domain_recompute_audit import AuditResult, StaleProject

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

PROJECT_ID = uuid.UUID("f6071829-3a4b-4c5d-9e6f-789abcdeb301")


def _stale() -> StaleProject:
    return StaleProject(
        project_id=PROJECT_ID,
        project_name="AUDIT-JOB",
        last_applied_sync_at="2026-08-09T00:00:00+00:00",
        last_domain_computed_at=None,
        applied_runs=2,
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
    return await asyncio.to_thread(lambda: run_domain_recompute_audit(**kwargs))


async def test_clean_audit_returns_done_with_zero(monkeypatch):
    with patch("src.jobs.domain_recompute_audit.audit", return_value=AuditResult()) as mocked:
        mocked.return_value = AuditResult()
        result = await _run()

    assert result["status"] == "done"
    assert result["stale_projects"] == 0
    assert result["project_ids"] == []


async def test_stale_is_a_result_not_a_failure():
    """RQ retry theo exception; chạy lại một lần kiểm không đổi được kết quả."""
    outcome = AuditResult(stale=[_stale()], repaired_job_ids=["job-1"])

    with patch("src.jobs.domain_recompute_audit.audit", return_value=outcome):
        result = await _run()

    assert result["status"] == "done"
    assert result["stale_projects"] == 1
    assert result["project_ids"] == [str(PROJECT_ID)]
    assert result["repaired"] == 1
    assert result["job_ids"] == ["job-1"]


async def test_repair_error_is_carried_in_the_result():
    outcome = AuditResult(stale=[_stale()], repair_error="ConnectionError")

    with patch("src.jobs.domain_recompute_audit.audit", return_value=outcome):
        result = await _run()

    assert result["status"] == "done"
    assert result["repaired"] == 0
    assert result["repair_error"] == "ConnectionError"


async def test_repair_flag_is_forwarded():
    with patch("src.jobs.domain_recompute_audit.audit", return_value=AuditResult()) as mocked:
        await _run(repair=False)

    assert mocked.call_args.kwargs["repair"] is False


async def test_a_broken_audit_raises_so_rq_shows_it(caplog):
    """Database không với tới được là job hỏng thật — phải hiện ở failed registry."""
    with patch("src.jobs.domain_recompute_audit.audit", side_effect=OSError("db unreachable")):
        with caplog.at_level("ERROR"):
            with pytest.raises(OSError):
                await _run()

    assert "domain.recompute.audit_failed" in caplog.text


async def test_runs_against_the_real_database():
    """Không mock gì: câu SQL phải chạy được trên schema thật."""
    result = await _run(repair=False)

    assert result["status"] == "done"
    assert isinstance(result["stale_projects"], int)
    assert result["duration_ms"] >= 0
