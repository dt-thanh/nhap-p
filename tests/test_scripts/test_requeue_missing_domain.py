"""Phát hiện lô đã commit mà thiếu lần tính lại lineage miền.

Đây là lưới an toàn cho cửa sổ sự cố giữa `COMMIT` và `enqueue` — xem
`docs/crm/domain_recompute_operations.md` mục 2. Không cột nào ghi lại rằng còn
nợ một lần tính lại, nên việc phát hiện phải suy ra từ dấu vết đã có: lô nào đã
áp dụng thay đổi, và lineage miền của dự án đó cũ tới đâu.

Test chạy trên DB thật vì toàn bộ logic nằm trong một câu SQL; mock đi thì không
còn gì để kiểm.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from scripts.requeue_missing_domain_recompute import find_stale

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật"),
]

PROJECT_ID = uuid.UUID("e5f60718-293a-4b4c-9d5e-6789abcdea01")
AREA_ID = uuid.UUID("e5f60718-293a-4b4c-9d5e-6789abcdea02")
INSTANCE = "synthetic-requeue-crm"


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
        conn.execute(sa.text("DELETE FROM absorption_daily WHERE area_id = :a"), {"a": AREA_ID})
        conn.execute(sa.text("DELETE FROM upload_files WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with engine.begin() as conn:
        wipe(conn)
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'REQUEUE', :d, now())"),
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


def _applied_run(conn, *, finished_at, inserted=1, status="completed", transport="api_push"):
    conn.execute(
        sa.text(
            "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, finished_at, "
            "transport_mode, source_instance_id, error_summary) "
            # CAST(... AS jsonb) chứ không phải `:summary::jsonb`: SQLAlchemy không tách
            # được tham số khi nó dính liền toán tử cast `::` của Postgres.
            "VALUES (gen_random_uuid(), :p, :s, 1, 0, now(), :f, :t, :i, CAST(:summary AS jsonb))"
        ),
        {
            "p": PROJECT_ID,
            "s": status,
            "f": finished_at,
            "t": transport,
            "i": INSTANCE,
            "summary": json.dumps(
                {
                    "projections": {
                        "inserted": inserted,
                        "updated": 0,
                        "tombstoned": 0,
                        "untouched": 0,
                        "rejected": 0,
                    }
                }
            ),
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


async def _stale_ids() -> set[uuid.UUID]:
    return {project.project_id for project in await find_stale()}


# --- Phát hiện ---------------------------------------------------------------


async def test_applied_sync_without_any_domain_rows_is_stale(engine):
    """Đúng trường hợp job đầu tiên không bao giờ được xếp hàng."""
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    assert PROJECT_ID in await _stale_ids()


async def test_domain_rows_newer_than_the_sync_are_not_stale(engine):
    now = datetime.now(UTC)
    with engine.begin() as conn:
        _applied_run(conn, finished_at=now - timedelta(minutes=5))
        _domain_row(conn, computed_at=now)

    assert PROJECT_ID not in await _stale_ids()


async def test_domain_rows_older_than_the_sync_are_stale(engine):
    """Lô mới hơn lần tính gần nhất = còn nợ một lần tính lại."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        _domain_row(conn, computed_at=now - timedelta(hours=1))
        _applied_run(conn, finished_at=now)

    assert PROJECT_ID in await _stale_ids()


async def test_a_run_that_changed_nothing_is_not_stale(engine):
    """Lô chỉ có `untouched`/`rejected` không nợ gì cả."""
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC), inserted=0)

    assert PROJECT_ID not in await _stale_ids()


async def test_file_upload_runs_are_ignored(engine):
    """Đường Excel/CSV không sinh dòng miền, nên không bao giờ nợ."""
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC), transport="file_upload")

    assert PROJECT_ID not in await _stale_ids()


async def test_unfinished_runs_are_ignored(engine):
    """Lô đang chạy chưa kết thúc thì chưa nợ gì."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO upload_files (id, project_id, status, rows_ok, rows_failed, uploaded_at, "
                "transport_mode, source_instance_id, error_summary) "
                "VALUES (gen_random_uuid(), :p, 'processing', 0, 0, now(), 'api_push', :i, "
                '\'{"projections": {"inserted": 5}}\'::jsonb)'
            ),
            {"p": PROJECT_ID, "i": INSTANCE},
        )

    assert PROJECT_ID not in await _stale_ids()


async def test_recomputing_clears_the_stale_flag(engine):
    """Sau khi tính lại, công cụ phải im lặng — nếu không nó sẽ bị bỏ qua."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        _applied_run(conn, finished_at=now)
    assert PROJECT_ID in await _stale_ids()

    with engine.begin() as conn:
        _domain_row(conn, computed_at=now + timedelta(seconds=1))

    assert PROJECT_ID not in await _stale_ids()


async def test_report_carries_enough_detail_to_act_on(engine):
    with engine.begin() as conn:
        _applied_run(conn, finished_at=datetime.now(UTC))

    stale = [p for p in await find_stale() if p.project_id == PROJECT_ID]

    assert stale
    project = stale[0]
    assert project.project_name == "REQUEUE"
    assert project.never_computed is True
    assert project.applied_runs == 1
