"""Worker RQ THẬT tiêu thụ job tính lại từ hàng đợi.

Tách khỏi `test_recompute_domain.py` vì hai lý do kỹ thuật, cả hai đều bắt buộc:

1. Worker của RQ cài **signal handler**, mà `signal` chỉ dùng được ở MAIN THREAD.
   Chạy nó trong `asyncio.to_thread` (cách các test async gọi job) sẽ nổ
   `ValueError: signal only works in main thread`. Nên file này toàn test ĐỒNG BỘ.
2. Vì đồng bộ, phần đọc DB dùng engine psycopg2 — cùng cách các test migration làm.

Vì sao cần test này khi đã gọi thẳng hàm ở file kia: gọi thẳng KHÔNG chứng minh
được tên hàm phân giải từ chuỗi, tham số tuần tự hoá qua Redis, và module import
được trong tiến trình worker. Đó đúng là ba thứ hỏng khi đổi chữ ký hoặc đổi
đường import — và hỏng theo kiểu chỉ lộ ra ở production.

Hàng đợi RIÊNG (`test-recompute-…`), KHÔNG dùng `ingest`: container
`absorptionforecast-worker-1` đang nghe `ingest` và sẽ tranh mất job.
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

PROJECT_ID = uuid.UUID("c3d4e5f6-0718-492a-b13c-456789abce80")
AREA_ID = uuid.UUID("c3d4e5f6-0718-492a-b13c-456789abce81")
INSTANCE = "synthetic-worker-crm"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


@pytest.fixture
def engine():
    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def seeded(engine, monkeypatch):
    """Một dự án, một phân khu, hai căn đã bán — đủ để sinh chuỗi hấp thụ."""
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    def wipe(conn):
        conn.execute(sa.text("DELETE FROM deals WHERE source_instance_id = :i"), {"i": INSTANCE})
        conn.execute(sa.text("DELETE FROM units WHERE source_instance_id = :i"), {"i": INSTANCE})
        conn.execute(sa.text("DELETE FROM absorption_daily WHERE area_id = :a"), {"a": AREA_ID})
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with engine.begin() as conn:
        wipe(conn)
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'WORKER', :d, now())"),
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
                {"i": unit_id, "inst": INSTANCE, "ext": f"SYNTH-W-{index}", "a": AREA_ID, "code": f"W-{index}"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO deals (id, source_system, source_instance_id, external_deal_id, unit_id, "
                    "status, source_status, reserved_at, sold_at, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'mini_crm', :inst, :ext, :u, 'sold', 'sold', "
                    ":res, :sold, now(), now())"
                ),
                {
                    "inst": INSTANCE,
                    "ext": f"SYNTH-WD-{index}",
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


@pytest.mark.skipif(not _redis_available(), reason="Cần Redis để chạy worker thật")
def test_a_real_rq_worker_consumes_the_job_from_the_queue(engine):
    """Xếp hàng bằng TÊN HÀM dạng chuỗi, để worker thật phân giải và chạy."""
    import redis
    from rq import Queue, SimpleWorker

    connection = redis.Redis.from_url(REDIS_URL)
    # Hàng đợi riêng: container worker của dev đang nghe `ingest`.
    queue = Queue(f"test-recompute-{uuid.uuid4().hex[:8]}", connection=connection)

    try:
        job = queue.enqueue(
            "src.jobs.recompute_domain.run_domain_recompute",
            project_id=str(PROJECT_ID),
            area_ids=[str(AREA_ID)],
            sync_run_id=str(uuid.uuid4()),
        )

        # SimpleWorker: chạy job TRONG tiến trình này, không fork. `burst=True`
        # xử lý hết hàng rồi thoát nên test không treo.
        SimpleWorker([queue], connection=connection).work(burst=True)

        job.refresh()
        assert job.is_finished, f"job không chạy xong: {job.get_status()}"

        payload = job.return_value()
        assert payload["status"] == "done"
        assert payload["rows"] > 0
        assert payload["area_ids"] == [str(AREA_ID)]

        with engine.connect() as conn:
            rows = conn.execute(
                sa.text("SELECT calculator, units_reserved, computation_id FROM absorption_daily WHERE area_id = :a"),
                {"a": AREA_ID},
            ).all()

        assert rows, "worker báo xong nhưng không dòng nào được ghi"
        assert all(r[0] == "domain_units_deals" for r in rows)
        assert all(r[1] is not None for r in rows)
        assert len({r[2] for r in rows}) == 1
    finally:
        queue.delete(delete_jobs=True)
        connection.close()


@pytest.mark.skipif(not _redis_available(), reason="Cần Redis để chạy worker thật")
def test_worker_never_touches_the_legacy_lineage(engine):
    """Cấy sẵn một dòng legacy rồi cho worker chạy: dòng đó phải nguyên vẹn."""
    import redis
    from rq import Queue, SimpleWorker

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, velocity_7d, velocity_30d, "
                "data_quality_status, is_observed, computed_at, calculator) "
                "VALUES (gen_random_uuid(), :a, '2026-03-01', 99, 9.0, 9.0, 'ok', true, now(), 'legacy_aggregate')"
            ),
            {"a": AREA_ID},
        )
        before = conn.execute(
            sa.text(
                "SELECT md5(string_agg(x::text, '|' ORDER BY x::text)) FROM absorption_daily x "
                "WHERE x.area_id = :a AND x.calculator = 'legacy_aggregate'"
            ),
            {"a": AREA_ID},
        ).scalar_one()

    connection = redis.Redis.from_url(REDIS_URL)
    queue = Queue(f"test-recompute-{uuid.uuid4().hex[:8]}", connection=connection)
    try:
        queue.enqueue(
            "src.jobs.recompute_domain.run_domain_recompute",
            project_id=str(PROJECT_ID),
            area_ids=[str(AREA_ID)],
        )
        SimpleWorker([queue], connection=connection).work(burst=True)

        with engine.connect() as conn:
            after = conn.execute(
                sa.text(
                    "SELECT md5(string_agg(x::text, '|' ORDER BY x::text)) FROM absorption_daily x "
                    "WHERE x.area_id = :a AND x.calculator = 'legacy_aggregate'"
                ),
                {"a": AREA_ID},
            ).scalar_one()
        assert after == before, "worker đã sửa dòng của bộ tính cũ"
    finally:
        queue.delete(delete_jobs=True)
        connection.close()
