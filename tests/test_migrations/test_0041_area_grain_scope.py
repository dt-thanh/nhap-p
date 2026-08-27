"""PostgreSQL migration coverage for 0041 — PR-5's Area-grain scope
widening on `ranking_feature_snapshots`/`ranking_feature_values` (the first
scope with REAL per-area identity, unlike Project/Market's denormalized
per-project shape), plus the seeded expert Area feature definitions. Scratch
database per test, same pattern as 0033/.../0040's migration tests.
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

PREVIOUS_REVISION = "0040_market_grain_scope"
REVISION = "0041_area_grain_scope"

PROJECT_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OTHER_PROJECT_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
RUN_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
AREA_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
AREA_ID_2 = uuid.UUID("22222222-2222-4222-8222-222222222222")

AREA_FEATURE_KEYS = ("area_accessibility", "area_current_infrastructure", "area_future_infrastructure")


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
    name = f"mig41_{uuid.uuid4().hex[:12]}_test"
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
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'P2', '2026-01-01', now())"),
            {"i": OTHER_PROJECT_ID},
        )
        conn.execute(
            sa.text("INSERT INTO ranking_runs (id, project_id, trigger, enqueued_at) VALUES (:i, :p, 'manual', now())"),
            {"i": RUN_ID, "p": PROJECT_ID},
        )
        for area_id, project_id, area_name in (
            (AREA_ID, PROJECT_ID, "A1"),
            (AREA_ID_2, PROJECT_ID, "A2"),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                    "created_at, updated_at) VALUES (:i, :p, :n, 'apt', 2, 50, 10, now(), now())"
                ),
                {"i": area_id, "p": project_id, "n": area_name},
            )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _insert_snapshot(conn, *, scope_type: str = "area", area_id=AREA_ID, **overrides) -> uuid.UUID:
    snapshot_id = uuid.uuid4()
    values = {
        "id": snapshot_id,
        "ranking_run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "scope_type": scope_type,
        "area_id": area_id,
        "feature_set_version": "hierarchical-area-v1",
        "quality_status": "ok",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_snapshots (id, ranking_run_id, project_id, scope_type, area_id, "
            "cutoff_at, computed_at, feature_set_version, quality_status, created_at) "
            "VALUES (:id, :ranking_run_id, :project_id, :scope_type, :area_id, now(), now(), "
            ":feature_set_version, :quality_status, now())"
        ),
        values,
    )
    return snapshot_id


def _insert_value(conn, snapshot_id, feature_definition_id, *, scope_type: str = "area", area_id=AREA_ID, **overrides) -> uuid.UUID:
    value_id = uuid.uuid4()
    values = {
        "id": value_id,
        "snapshot_id": snapshot_id,
        "feature_definition_id": feature_definition_id,
        "project_id": PROJECT_ID,
        "scope_type": scope_type,
        "area_id": area_id,
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


def test_all_three_area_feature_definitions_are_seeded(upgraded):
    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT feature_key, grain, value_type, direction, definition_metadata "
                "FROM ranking_feature_definitions WHERE grain = 'area' ORDER BY feature_key"
            )
        ).mappings().all()
    assert {r["feature_key"] for r in rows} == set(AREA_FEATURE_KEYS)
    for row in rows:
        assert row["grain"] == "area"
        assert row["value_type"] == "numeric"
        assert row["direction"] == "positive"
        assert row["definition_metadata"] == {}


def test_no_feature_definition_exists_for_crm_owned_area_keys(upgraded):
    """`area_velocity_norm`/`area_conversion_norm` stay legacy-operational —
    0041 must never seed a `ranking_feature_definitions` row for either."""
    with upgraded["engine"].connect() as conn:
        count = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM ranking_feature_definitions "
                "WHERE feature_key IN ('area_velocity_norm', 'area_conversion_norm')"
            )
        ).scalar()
    assert count == 0


# --- Existing Project/Market-scope rows remain valid, unaffected --------------


def test_existing_market_scope_snapshot_and_value_still_insert(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'market_interest_rate'")
        ).scalar()
        snapshot_id = _insert_snapshot(
            conn, scope_type="market", area_id=None, feature_set_version="hierarchical-market-v1"
        )
        _insert_value(conn, snapshot_id, feature_id, scope_type="market", area_id=None)
    with upgraded["engine"].connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_values WHERE scope_type = 'market'")
        ).scalar()
    assert count == 1


def test_project_scope_still_permits_only_one_snapshot_per_run(upgraded):
    """`uq_rfs_run_project_scope_no_area` — Project/Market's one-row-per-
    (run, project, scope) guarantee must be UNCHANGED by 0041's area_id
    addition (the exact regression the partial-index design exists to
    prevent, see the migration's own docstring)."""
    with upgraded["engine"].begin() as conn:
        _insert_snapshot(conn, scope_type="project", area_id=None, feature_set_version="hierarchical-project-v1")
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_snapshot(conn, scope_type="project", area_id=None, feature_set_version="hierarchical-project-v1")


# --- New scope_type accepted, area identity enforced --------------------------


def test_area_scope_snapshot_and_value_insert_cleanly(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'area_accessibility'")
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
        _insert_value(conn, snapshot_id, feature_id)
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT scope_type, area_id FROM ranking_feature_snapshots WHERE id = :i"), {"i": snapshot_id}
        ).mappings().first()
    assert row["scope_type"] == "area"
    assert row["area_id"] == AREA_ID


def test_area_scope_requires_area_id(upgraded):
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_snapshot(conn, area_id=None)


def test_area_scope_rejects_unit_id(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'area_current_infrastructure'")
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_value(conn, snapshot_id, feature_id, unit_id=uuid.uuid4())


def test_two_different_areas_in_the_same_run_project_each_get_their_own_snapshot(upgraded):
    """`uq_rfs_run_project_area_scope` — the whole point of 0041's schema
    change: Area, unlike Project/Market, needs one snapshot PER AREA."""
    with upgraded["engine"].begin() as conn:
        _insert_snapshot(conn, area_id=AREA_ID)
        _insert_snapshot(conn, area_id=AREA_ID_2)
    with upgraded["engine"].connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_snapshots WHERE scope_type = 'area'")
        ).scalar()
    assert count == 2


def test_same_area_in_the_same_run_project_cannot_get_a_second_snapshot(upgraded):
    with upgraded["engine"].begin() as conn:
        _insert_snapshot(conn, area_id=AREA_ID)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_snapshot(conn, area_id=AREA_ID)


def test_value_area_id_must_match_its_own_snapshots_area_id(upgraded):
    """The widened composite FK (`snapshot_id, project_id, scope_type,
    area_id`) — a value cannot claim a different area than its own
    snapshot; there is no same-table CHECK that could express this."""
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'area_future_infrastructure'")
        ).scalar()
        snapshot_id = _insert_snapshot(conn, area_id=AREA_ID)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_value(conn, snapshot_id, feature_id, area_id=AREA_ID_2)


# --- Downgrade -----------------------------------------------------------------


def test_downgrade_refuses_when_area_snapshot_rows_exist(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = 'area_accessibility'")
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
        _insert_value(conn, snapshot_id, feature_id)

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "Refusing to downgrade 0041" in result.stderr


def test_downgrade_succeeds_with_only_the_migrations_own_seed(upgraded):
    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    with engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'area'")
        ).scalar()
        market_defs = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'market'")
        ).scalar()
    assert count == 0
    assert market_defs == 4, "downgrading 0041 must not touch 0040's own Market seed"
    engine.dispose()
