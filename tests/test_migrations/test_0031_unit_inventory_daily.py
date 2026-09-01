"""Migration 0031 (unit_inventory_daily, P1) — tiến, lùi, và ràng buộc.

Bảng thuần vật chất hoá: không trigger, không append-only guard — chỉ kiểm
hình dạng bảng và ràng buộc duy nhất mỗi (area_id, stat_date).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)

PREVIOUS_REVISION = "0030_status_history_triggers"
REVISION = "0031_unit_inventory_daily"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def scratch_db():
    name = f"mig31_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(TEST_DATABASE_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name}
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    project_id, area_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', '2026-01-01', now())"),
            {"i": project_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:i, :p, 'A1', '2PN', 2, 75, 100, now())"
            ),
            {"i": area_id, "p": project_id},
        )
    try:
        yield {"url": scratch_db, "engine": engine, "area_id": area_id}
    finally:
        engine.dispose()


def _insert_row(conn, area_id, *, stat_date="2026-02-01", sellable=10, blocked=1, reserved=2, sold=3):
    row_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO unit_inventory_daily (id, area_id, stat_date, sellable_units, blocked_units, "
            "live_reserved_units, live_sold_units, rebuilt_from_log_at) "
            "VALUES (:i, :a, :d, :se, :bl, :re, :so, now())"
        ),
        {"i": row_id, "a": area_id, "d": stat_date, "se": sellable, "bl": blocked, "re": reserved, "so": sold},
    )
    return row_id


def test_upgrade_then_downgrade_then_upgrade(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('unit_inventory_daily')")).scalar() is not None
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == REVISION

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('unit_inventory_daily')")).scalar() is None
            for legacy in ("unit_status_history", "deal_status_history", "areas"):
                assert conn.execute(sa.text(f"SELECT to_regclass('{legacy}')")).scalar() is not None

        _alembic(scratch_db, "upgrade", REVISION)
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT to_regclass('unit_inventory_daily')")).scalar() is not None
    finally:
        engine.dispose()


def test_one_row_per_area_per_day(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        _insert_row(conn, area_id, stat_date="2026-02-01")
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_row(conn, area_id, stat_date="2026-02-01")
    assert "uq_unit_inventory_daily_area_stat_date" in str(exc.value)


def test_negative_counts_are_rejected(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with engine.begin() as conn:
            _insert_row(conn, area_id, sellable=-1)
    assert "ck_uid_sellable_nonnegative" in str(exc.value)


def test_same_area_different_day_is_allowed(upgraded):
    engine, area_id = upgraded["engine"], upgraded["area_id"]
    with engine.begin() as conn:
        _insert_row(conn, area_id, stat_date="2026-02-01")
        _insert_row(conn, area_id, stat_date="2026-02-02")
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM unit_inventory_daily WHERE area_id = :a"), {"a": area_id}
        ).scalar() == 2


# This test must not import the current-head Core declaration because
# unit_inventory_daily was intentionally retired by 0036_remove_historical_ranking.
# Historical migration tests verify the schema at the migration revision, not
# whether retired tables remain mapped at current head.
def test_the_migrated_columns_match_0031s_own_create_table(upgraded):
    """Expected shape is hardcoded from `0031_unit_inventory_daily.py`'s own
    `create_table()` call (`alembic/versions/0031_unit_inventory_daily.py:60-78`,
    every column `nullable=False`) — a historical fact about this one
    migration, not a projection of whatever `src/models/tables.py` currently
    declares. `0036_remove_historical_ranking.py:38` drops this table from
    HEAD (predating PR-1 by six revisions); the fixture above upgrades only
    through `0031` on a scratch database, so the table still genuinely exists
    at the point this test inspects it."""
    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'unit_inventory_daily'"
            )
        ).all()
    actual = {name: (is_nullable == "YES") for name, is_nullable in rows}
    expected = {
        "id": False,
        "area_id": False,
        "stat_date": False,
        "sellable_units": False,
        "blocked_units": False,
        "live_reserved_units": False,
        "live_sold_units": False,
        "rebuilt_from_log_at": False,
    }
    assert actual == expected
