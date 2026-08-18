"""Lần kiểm định kỳ: phát hiện, báo động, và vá.

Ba tính chất được kiểm ở đây, theo thứ tự quan trọng giảm dần:

1. **Báo động phát ra kể cả khi vá thành công.** Đây là tính chất dễ mất nhất khi
   ai đó "dọn log cho đỡ ồn", và mất nó thì một đường `enqueue` hỏng vĩnh viễn
   trông y hệt một hệ thống khoẻ mạnh.
2. **Service không dispose engine dùng chung.** Ở CLI thì vô hại; trong worker
   sống lâu thì nó cắt kết nối của job khác — một lỗi chỉ hiện ra dưới tải.
3. **Vá hỏng không làm hỏng lần kiểm.** Redis chết đúng lúc này nghĩa là chính
   đường xếp hàng đang là nguyên nhân; nuốt lỗi và báo cáo còn hơn ném ra.

Test chạy trên DB thật vì phần lớn logic nằm trong một câu SQL.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from src.services.domain_recompute_audit import audit, find_stale

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

PROJECT_ID = uuid.UUID("f6071829-3a4b-4c5d-9e6f-789abcdeb101")
AREA_ID = uuid.UUID("f6071829-3a4b-4c5d-9e6f-789abcdeb102")
INSTANCE = "synthetic-audit-crm"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id


class FakeQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def enqueue(self, func_name, **kwargs):
        if self.fail:
            raise ConnectionError("Redis không với tới được")
        self.calls.append({"func": func_name, **kwargs})
        return FakeJob(f"fake-{len(self.calls)}")


@pytest.fixture
def engine():
    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))
    yield engine
    engine.dispose()


@pytest.fixture
def queue(monkeypatch):
    fake = FakeQueue()
    monkeypatch.setattr("src.task_queue.get_queue", lambda name=None: fake)
    return fake


@pytest.fixture(autouse=True)
def seeded(engine, monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    def wipe(conn):
        conn.execute(sa.text("DELETE FROM absorption_daily WHERE area_id = :a"), {"a": AREA_ID})
        conn.execute(sa.text("DELETE FROM upload_files WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with engine.begin() as conn:
        wipe(conn)
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'AUDIT', :d, now())"),
            {"p": PROJECT_ID, "d": "2026-01-01"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:a, :p, 'A1', '2PN', 2, 75, 50, now())"
            ),
            {"a": AREA_ID, "p": PROJECT_ID},
        )
    yield
    with engine.begin() as conn:
        wipe(conn)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


def _applied_run(conn, *, finished_at, inserted=1):
    conn.execute(
        sa.text(
            "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, finished_at, "
            "transport_mode, source_instance_id, error_summary) "
            "VALUES (gen_random_uuid(), :p, 'completed', 1, 0, now(), :f, 'api_push', :i, CAST(:summary AS jsonb))"
        ),
        {
            "p": PROJECT_ID,
            "f": finished_at,
            "i": INSTANCE,
            "summary": json.dumps({"projections": {"inserted": inserted, "updated": 0, "tombstoned": 0}}),
        },
    )


def _domain_row(conn, *, computed_at):
    conn.execute(
        sa.text(
            "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, velocity_7d, velocity_30d, "
            "data_quality_status, is_observed, computed_at, calculator, units_reserved, computation_id) "
            "VALUES (gen_random_uuid(), :a, '2026-03-01', 1, 1.0, 1.0, 'ok', true, :c, "
            "'domain_units_deals', 0, gen_random_uuid())"
        ),
        {"a": AREA_ID, "c": computed_at},
    )


def _ours(result):
    return [p for p in result.stale if p.project_id == PROJECT_ID]


# --- Báo động ----------------------------------------------------------------


async def test_alert_fires_even_when_the_repair_succeeds(engine, queue, caplog):
    """Tính chất quan trọng nhất của module này.

    Vá im lặng sẽ khiến một đường `enqueue` hỏng vĩnh viễn trông như một hệ thống
    khoẻ mạnh: mỗi lô đều lỡ, mỗi lần kiểm đều vá, không ai biết.
    """
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    with caplog.at_level("ERROR"):
        result = await audit(repair=True)

    assert _ours(result)
    assert result.repaired_job_ids, "phải có job được xếp lại"
    assert "domain.recompute.audit_stale" in caplog.text


async def test_alert_carries_the_project_ids(engine, queue, caplog):
    """Người trực phải xếp lại được bằng tay chỉ từ dòng log."""
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    with caplog.at_level("ERROR"):
        await audit(repair=True)

    assert str(PROJECT_ID) in caplog.text


async def test_clean_audit_does_not_alert(engine, queue, caplog):
    now = datetime.now(UTC)
    with engine.begin() as conn:
        _applied_run(conn, finished_at=now - timedelta(minutes=5))
        _domain_row(conn, computed_at=now)

    with caplog.at_level("ERROR"):
        result = await audit(repair=True)

    assert not _ours(result)
    assert "domain.recompute.audit_stale" not in caplog.text


# --- Vá ----------------------------------------------------------------------


async def test_repair_enqueues_whole_project_scope(engine, queue):
    """Đã mất dấu job cũ thì không biết phân khu nào từng đổi — tính lại cả dự án."""
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    await audit(repair=True)

    ours = [c for c in queue.calls if c["project_id"] == str(PROJECT_ID)]
    assert len(ours) == 1
    assert ours[0]["func"] == "src.jobs.recompute_domain.run_domain_recompute"
    assert ours[0]["area_ids"] is None


async def test_repair_disabled_reports_without_enqueueing(engine, queue, caplog):
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    with caplog.at_level("ERROR"):
        result = await audit(repair=False)

    assert _ours(result)
    assert result.repaired_job_ids == []
    assert queue.calls == []
    # Báo động vẫn phải phát — chế độ chỉ-báo-cáo không phải chế độ im lặng.
    assert "domain.recompute.audit_stale" in caplog.text


async def test_enqueue_failure_is_reported_not_raised(engine, monkeypatch, caplog):
    """Redis chết đúng lúc này nghĩa là chính đường xếp hàng là nguyên nhân."""
    monkeypatch.setattr("src.task_queue.get_queue", lambda name=None: FakeQueue(fail=True))
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    with caplog.at_level("ERROR"):
        result = await audit(repair=True)

    assert _ours(result), "lần kiểm vẫn giữ giá trị chẩn đoán"
    assert result.repair_error == "ConnectionError"
    assert "domain.recompute.audit_repair_failed" in caplog.text


async def test_second_audit_after_recompute_is_clean(engine, queue):
    """Vá xong phải im — công cụ báo động mãi là công cụ bị bỏ qua."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        _applied_run(conn, finished_at=now)
    assert _ours(await audit(repair=False))

    with engine.begin() as conn:
        _domain_row(conn, computed_at=now + timedelta(seconds=1))

    assert not _ours(await audit(repair=False))


# --- Vòng đời engine ---------------------------------------------------------


async def test_find_stale_leaves_the_shared_engine_usable(engine):
    """Không dispose: engine có lru_cache nên nó là của DÙNG CHUNG.

    Trong worker sống lâu, dispose sẽ cắt kết nối của job khác — SQLAlchemy dựng
    lại pool nên không lỗi ngay, và cái giá trả bằng những lỗi lác đác không truy
    được. Kiểm bằng cách gọi hai lần rồi dùng lại chính engine đó.
    """
    from src.db import get_engine

    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    await find_stale()
    await find_stale()

    shared = get_engine()
    async with shared.connect() as conn:
        assert await conn.scalar(sa.select(sa.literal(1))) == 1


async def test_audit_writes_nothing(engine, queue):
    """Lần kiểm là CHỈ ĐỌC — nó không được chạm lineage nào."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        _applied_run(conn, finished_at=now)
        _domain_row(conn, computed_at=now - timedelta(hours=1))

    with engine.begin() as conn:
        before = conn.execute(
            sa.text("SELECT count(*), max(computed_at) FROM absorption_daily WHERE area_id = :a"), {"a": AREA_ID}
        ).one()

    await audit(repair=True)

    with engine.begin() as conn:
        after = conn.execute(
            sa.text("SELECT count(*), max(computed_at) FROM absorption_daily WHERE area_id = :a"), {"a": AREA_ID}
        ).one()

    assert before == after
