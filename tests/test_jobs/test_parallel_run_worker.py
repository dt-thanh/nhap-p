"""Worker RQ THẬT tiêu thụ job ghi lịch sử so sánh, từ một hàng đợi RIÊNG.

Tách khỏi `test_parallel_run_job.py` vì worker của RQ cài signal handler, mà
`signal` chỉ dùng được ở MAIN THREAD — nên file này toàn test ĐỒNG BỘ và đọc DB
bằng engine psycopg2.

Gọi thẳng hàm KHÔNG chứng minh được ba thứ mà chỉ đường worker mới chứng minh:
tên hàm phân giải được từ chuỗi, tham số tuần tự hoá qua Redis, và module import
được trong tiến trình worker. Cả ba đều hỏng theo kiểu chỉ lộ ra ở production.

Hàng đợi RIÊNG (`test-parallel-run-…`), **không bao giờ** dùng `ingest`:
container `absorptionforecast-worker-1` đang nghe `ingest` và sẽ tranh mất job —
và `ingest` là hàng đợi dùng chung, không phải sân chơi của test.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
)

PROJECT_ID = uuid.UUID("f2a3b4c5-d6e7-4859-8a6b-cdef01234a2f")
AREA_ID = uuid.UUID("f2a3b4c5-d6e7-4859-8a6b-cdef01234a30")
INSTANCE = "synthetic-parallel-worker"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


@pytest.fixture
def engine():
    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def seeded(engine, monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    def wipe(conn):
        conn.execute(sa.text("DELETE FROM calculator_comparisons WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM deals WHERE source_instance_id = :i"), {"i": INSTANCE})
        conn.execute(sa.text("DELETE FROM units WHERE source_instance_id = :i"), {"i": INSTANCE})
        conn.execute(sa.text("DELETE FROM absorption_daily WHERE area_id = :a"), {"a": AREA_ID})
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with engine.begin() as conn:
        wipe(conn)
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'PRWORKER', :d, now())"),
            {"p": PROJECT_ID, "d": "2026-01-01"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:a, :p, 'A1', '2PN', 2, 75, 50, now())"
            ),
            {"a": AREA_ID, "p": PROJECT_ID},
        )
        for index in range(2):
            unit_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, "
                    "unit_code, unit_type, status, created_at, updated_at) "
                    "VALUES (:i, 'mini_crm', :inst, :ext, :a, :code, '2PN', 'sold', now(), now())"
                ),
                {"i": unit_id, "inst": INSTANCE, "ext": f"PRW-U-{index}", "a": AREA_ID, "code": f"PRW-{index}"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, "
                    "status, source_status, reserved_at, sold_at, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'mini_crm', :inst, :ext, :u, 'sold', 'sold', :res, :sold, "
                    "now(), now())"
                ),
                {
                    "inst": INSTANCE,
                    "ext": f"PRW-D-{index}",
                    "u": unit_id,
                    "res": datetime(2026, 2, 20, tzinfo=UTC),
                    "sold": datetime(2026, 3, 1 + index, tzinfo=UTC),
                },
            )
    yield
    with engine.begin() as conn:
        wipe(conn)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


def _redis_available() -> bool:
    try:
        import redis

        redis.Redis.from_url(REDIS_URL, socket_connect_timeout=2).ping()
        return True
    except Exception:
        return False


def _absorption_digest(conn) -> list[tuple]:
    return [
        tuple(row)
        for row in conn.execute(
            sa.text(
                "SELECT area_id, stat_date, units_sold, units_remaining, units_reserved, velocity_7d, "
                "velocity_30d, data_quality_status, is_observed, calculator, computation_id "
                "FROM absorption_daily ORDER BY area_id, stat_date, calculator"
            )
        ).all()
    ]


def _run_on_a_dedicated_queue(engine, **kwargs):
    import redis
    from rq import Queue, SimpleWorker

    connection = redis.Redis.from_url(REDIS_URL)
    queue = Queue(f"test-parallel-run-{uuid.uuid4().hex[:8]}", connection=connection)
    try:
        job = queue.enqueue("src.jobs.parallel_run.run_parallel_run_capture", **kwargs)
        SimpleWorker([queue], connection=connection).work(burst=True)
        job.refresh()
        return job
    finally:
        queue.delete(delete_jobs=True)


@pytest.mark.skipif(not _redis_available(), reason="Cần Redis để chạy worker thật")
def test_a_real_rq_worker_captures_a_comparison(engine):
    job = _run_on_a_dedicated_queue(engine, project_id=str(PROJECT_ID), trigger="schedule")

    assert job.get_status() == "finished", f"job hỏng: {job.latest_result()}"
    assert job.return_value()["captured"] == 1

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT trigger, domain_has_data FROM calculator_comparisons WHERE project_id = :p"),
            {"p": PROJECT_ID},
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == "schedule"
    assert rows[0][1] is True


@pytest.mark.skipif(not _redis_available(), reason="Cần Redis để chạy worker thật")
def test_the_worker_path_does_not_touch_absorption_daily(engine):
    """Chạy song song phải QUAN SÁT, không được ghi lineage nào — kiểm qua đúng
    đường mà scheduler sẽ dùng, bằng dấu vân toàn dòng."""
    with engine.begin() as conn:
        for calculator, reserved in (("legacy_aggregate", None), ("domain_units_deals", 1)):
            conn.execute(
                sa.text(
                    "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, units_remaining, "
                    "units_reserved, velocity_7d, velocity_30d, data_quality_status, is_observed, computed_at, "
                    "calculator, computation_id) VALUES (gen_random_uuid(), :a, '2026-03-01', 2, 48, :r, 1.0, "
                    "1.0, 'ok', true, now(), :c, gen_random_uuid())"
                ),
                {"a": AREA_ID, "c": calculator, "r": reserved},
            )
    with engine.connect() as conn:
        before = _absorption_digest(conn)
    assert before, "phải có dòng thật để so, nếu không test này không chứng minh gì"

    job = _run_on_a_dedicated_queue(engine, project_id=str(PROJECT_ID), trigger="schedule")

    assert job.get_status() == "finished"
    with engine.connect() as conn:
        assert _absorption_digest(conn) == before


@pytest.mark.skipif(not _redis_available(), reason="Cần Redis để chạy worker thật")
def test_the_worker_path_leaves_the_calculator_flag_alone(engine):
    _run_on_a_dedicated_queue(engine, project_id=str(PROJECT_ID), trigger="schedule")

    with engine.connect() as conn:
        flags = set(conn.execute(sa.text("SELECT DISTINCT absorption_calculator FROM projects")).scalars())
    assert flags == {"legacy_aggregate"}
