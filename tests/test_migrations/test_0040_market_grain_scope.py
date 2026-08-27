"""PostgreSQL migration coverage for 0040 — PR-4's Market-grain scope
widening on `ranking_feature_snapshots`/`ranking_feature_values`, plus the
seeded Market feature definitions. Scratch database per test, same pattern
as 0033/.../0039's migration tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PostgreSQL is unavailable: TEST_DATABASE_URL/DATABASE_URL is not configured",
)

PREVIOUS_REVISION = "0039_project_value_materialize"
REVISION = "0040_market_grain_scope"

PROJECT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
RUN_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

MARKET_FEATURE_KEYS = ("market_interest_rate", "market_credit_policy", "market_liquidity", "market_demand")


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )


@pytest.fixture
def scratch_db():
    name = f"mig40_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(TEST_DATABASE_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
                {"name": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    result = _alembic(scratch_db, "upgrade", REVISION)
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P', '2026-01-01', now())"),
            {"i": PROJECT_ID},
        )
        conn.execute(
            sa.text("INSERT INTO ranking_runs (id, project_id, trigger, enqueued_at) VALUES (:i, :p, 'manual', now())"),
            {"i": RUN_ID, "p": PROJECT_ID},
        )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _insert_snapshot(conn, *, scope_type: str = "market", **overrides) -> uuid.UUID:
    snapshot_id = uuid.uuid4()
    values = {
        "id": snapshot_id,
        "ranking_run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "scope_type": scope_type,
        "feature_set_version": "hierarchical-market-v1",
        "quality_status": "ok",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_snapshots (id, ranking_run_id, project_id, scope_type, cutoff_at, "
            "computed_at, feature_set_version, quality_status, created_at) "
            "VALUES (:id, :ranking_run_id, :project_id, :scope_type, now(), now(), :feature_set_version, "
            ":quality_status, now())"
        ),
        values,
    )
    return snapshot_id


def _insert_value(conn, snapshot_id, feature_definition_id, *, scope_type: str = "market", **overrides) -> uuid.UUID:
    value_id = uuid.uuid4()
    values = {
        "id": value_id,
        "snapshot_id": snapshot_id,
        "feature_definition_id": feature_definition_id,
        "project_id": PROJECT_ID,
        "scope_type": scope_type,
        "area_id": None,
        "unit_id": None,
        "value_kind": "numeric",
        "normalized_numeric": 0.5,
        "quality_status": "ok",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_values (id, snapshot_id, feature_definition_id, project_id, scope_type, "
            "area_id, unit_id, value_kind, normalized_numeric, quality_status, created_at) "
            "VALUES (:id, :snapshot_id, :feature_definition_id, :project_id, :scope_type, "
            ":area_id, :unit_id, :value_kind, :normalized_numeric, :quality_status, now())"
        ),
        values,
    )
    return value_id


# --- Seed data -----------------------------------------------------------------


def test_all_four_market_feature_definitions_are_seeded(upgraded):
    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT feature_key, grain, value_type, definition_metadata FROM ranking_feature_definitions "
                "WHERE grain = 'market' ORDER BY feature_key"
            )
        ).mappings().all()
    assert {r["feature_key"] for r in rows} == set(MARKET_FEATURE_KEYS)
    for row in rows:
        assert row["grain"] == "market"
        assert row["value_type"] == "numeric"
    by_key = {r["feature_key"]: r for r in rows}
    assert by_key["market_interest_rate"]["definition_metadata"]["max_shelf_life_days"] == 30
    for key in ("market_credit_policy", "market_liquidity", "market_demand"):
        assert by_key[key]["definition_metadata"]["max_shelf_life_days"] == 90


# --- Existing Project-scope rows/behavior unaffected --------------------------


def test_existing_project_scope_snapshot_and_value_still_insert(upgraded):
    feature_id = uuid.uuid4()
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ranking_feature_definitions (id, feature_key, feature_version, name, category, "
                "grain, value_type, formula_id, normalization_method, direction, missing_policy, status, "
                "created_at, updated_at) "
                "VALUES (:i, 'expert_location_score', 'v1', 'Location', 'expert', 'project', 'numeric', 'f', "
                "'identity', 'positive', 'skip', 'active', now(), now())"
            ),
            {"i": feature_id},
        )
        snapshot_id = _insert_snapshot(conn, scope_type="project", feature_set_version="hierarchical-project-v1")
        _insert_value(conn, snapshot_id, feature_id, scope_type="project")
    with upgraded["engine"].connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_values WHERE scope_type = 'project'")
        ).scalar()
    assert count == 1


# --- New scope_type accepted, shape constraints unchanged ---------------------


def test_market_scope_snapshot_and_value_insert_cleanly(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'market_interest_rate'")
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
        _insert_value(conn, snapshot_id, feature_id)
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT scope_type, area_id FROM ranking_feature_snapshots WHERE id = :i"), {"i": snapshot_id}
        ).mappings().first()
    assert row["scope_type"] == "market"
    assert row["area_id"] is None


def test_market_scope_still_requires_null_area_and_unit(upgraded):
    """`ck_rfv_project_scope_shape` (0033, untouched by 0040) — Market is
    denormalized per-project, same shape as Project, not widened for area/unit."""
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'market_demand'")
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_value(conn, snapshot_id, feature_id, unit_id=uuid.uuid4())


def test_area_scope_is_still_rejected(upgraded):
    """PR-4 widens to `('project', 'market')` only — `'area'` remains out of
    scope until PR-5."""
    with upgraded["engine"].begin() as conn:
        snapshot_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_feature_snapshots (id, ranking_run_id, project_id, scope_type, "
                    "cutoff_at, computed_at, feature_set_version, quality_status, created_at) "
                    "VALUES (:id, :run, :project, 'area', now(), now(), 'v', 'ok', now())"
                ),
                {"id": snapshot_id, "run": RUN_ID, "project": PROJECT_ID},
            )


# --- Downgrade -----------------------------------------------------------------


def test_downgrade_refuses_when_market_snapshot_rows_exist(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'market_liquidity'")
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
        _insert_value(conn, snapshot_id, feature_id)

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "Refusing to downgrade 0040" in result.stderr


def test_downgrade_succeeds_with_only_the_migrations_own_seed(upgraded):
    """No real Market usage yet (only this migration's own 4 seed rows) —
    downgrade must succeed and remove exactly those 4 rows."""
    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    with engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'market'")
        ).scalar()
    assert count == 0
    engine.dispose()
