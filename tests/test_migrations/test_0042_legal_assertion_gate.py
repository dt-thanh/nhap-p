"""PostgreSQL migration coverage for 0042 — PR-6's Legal-scope widening on
`ranking_feature_snapshots`/`ranking_feature_values` (a fourth `scope_type`,
`'legal'`, denormalized per-project like Project/Market — no new area/unit
shape to reconcile), plus the seeded `project_legal_status` categorical
feature definition. Scratch database per test, same pattern as
0033/.../0041's migration tests.
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

PREVIOUS_REVISION = "0041_area_grain_scope"
REVISION = "0042_legal_assertion_gate"

PROJECT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")

LEGAL_FEATURE_KEY = "project_legal_status"


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
    name = f"mig42_{uuid.uuid4().hex[:12]}_test"
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


def _insert_snapshot(conn, *, scope_type: str = "legal", **overrides) -> uuid.UUID:
    snapshot_id = uuid.uuid4()
    values = {
        "id": snapshot_id,
        "ranking_run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "scope_type": scope_type,
        "area_id": None,
        "feature_set_version": "hierarchical-legal-v1",
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


def _insert_categorical_value(conn, snapshot_id, feature_definition_id, *, categorical_value="HIGH_RISK", **overrides) -> uuid.UUID:
    value_id = uuid.uuid4()
    values = {
        "id": value_id,
        "snapshot_id": snapshot_id,
        "feature_definition_id": feature_definition_id,
        "project_id": PROJECT_ID,
        "scope_type": "legal",
        "area_id": None,
        "unit_id": None,
        "value_kind": "categorical",
        "categorical_value": categorical_value,
        "quality_status": "ok",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_values (id, snapshot_id, feature_definition_id, project_id, scope_type, "
            "area_id, unit_id, value_kind, categorical_value, quality_status, created_at) "
            "VALUES (:id, :snapshot_id, :feature_definition_id, :project_id, :scope_type, "
            ":area_id, :unit_id, :value_kind, :categorical_value, :quality_status, now())"
        ),
        values,
    )
    return value_id


# --- Seed data -----------------------------------------------------------------


def test_project_legal_status_definition_is_seeded_categorical_project_shape(upgraded):
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT grain, value_type, category, definition_metadata "
                "FROM ranking_feature_definitions WHERE feature_key = :k"
            ),
            {"k": LEGAL_FEATURE_KEY},
        ).mappings().first()
    assert row is not None
    assert row["grain"] == "project"
    assert row["value_type"] == "categorical"
    assert row["category"] == "legal"
    assert set(row["definition_metadata"]["allowed_categorical_values"]) == {
        "HIGH_RISK",
        "NOT_HIGH_RISK",
        "UNKNOWN",
    }


def test_project_market_area_definitions_remain_valid(upgraded):
    with upgraded["engine"].connect() as conn:
        market_defs = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'market'")
        ).scalar()
        area_defs = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'area'")
        ).scalar()
    assert market_defs == 4
    assert area_defs == 3


# --- New scope_type accepted, categorical value shape enforced ----------------


def test_legal_scope_snapshot_and_categorical_value_insert_cleanly(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = :k"), {"k": LEGAL_FEATURE_KEY}
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
        _insert_categorical_value(conn, snapshot_id, feature_id, categorical_value="HIGH_RISK")
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT scope_type, area_id FROM ranking_feature_snapshots WHERE id = :i"), {"i": snapshot_id}
        ).mappings().first()
    assert row["scope_type"] == "legal"
    assert row["area_id"] is None


def test_legal_scope_rejects_an_area_id(upgraded):
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_snapshot(conn, area_id=uuid.uuid4())


def test_legal_scope_value_rejects_a_unit_id(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = :k"), {"k": LEGAL_FEATURE_KEY}
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_categorical_value(conn, snapshot_id, feature_id, unit_id=uuid.uuid4())


def test_legal_scope_still_permits_only_one_snapshot_per_run(upgraded):
    """`uq_rfs_run_project_scope_no_area` already covers ANY scope_type with
    a NULL `area_id` — Legal gets its one-row-per-(run, project) guarantee
    for free, the exact claim 0042's own docstring makes."""
    with upgraded["engine"].begin() as conn:
        _insert_snapshot(conn)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_snapshot(conn)


def test_project_scope_snapshot_still_permitted_independently_of_legal(upgraded):
    with upgraded["engine"].begin() as conn:
        _insert_snapshot(conn, scope_type="project", feature_set_version="hierarchical-project-v1")
        _insert_snapshot(conn, scope_type="legal")
    with upgraded["engine"].connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_snapshots WHERE ranking_run_id = :r"), {"r": RUN_ID}
        ).scalar()
    assert count == 2


def test_invalid_categorical_value_is_not_rejected_by_a_db_check(upgraded):
    """Deliberate, documented design choice (0042's docstring): the three-
    value vocabulary is enforced by `src/services/governance.py` at write
    time (`definition_metadata.allowed_categorical_values`), not by a
    table-wide `categorical_value` CHECK — a value outside the vocabulary
    inserted directly via SQL (bypassing governance entirely) is NOT
    expected to be blocked at the schema level. This test documents that
    boundary rather than asserting a DB guarantee that does not exist."""
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = :k"), {"k": LEGAL_FEATURE_KEY}
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
        _insert_categorical_value(conn, snapshot_id, feature_id, categorical_value="SOMETHING_ELSE")
    with upgraded["engine"].connect() as conn:
        value = conn.execute(
            sa.text("SELECT categorical_value FROM ranking_feature_values WHERE snapshot_id = :s"),
            {"s": snapshot_id},
        ).scalar()
    assert value == "SOMETHING_ELSE"


# --- Downgrade -----------------------------------------------------------------


def test_downgrade_refuses_when_legal_snapshot_rows_exist(upgraded):
    with upgraded["engine"].begin() as conn:
        feature_id = conn.execute(
            sa.text("SELECT id FROM ranking_feature_definitions WHERE feature_key = :k"), {"k": LEGAL_FEATURE_KEY}
        ).scalar()
        snapshot_id = _insert_snapshot(conn)
        _insert_categorical_value(conn, snapshot_id, feature_id)

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "Refusing to downgrade 0042" in result.stderr


def test_downgrade_succeeds_with_only_the_migrations_own_seed(upgraded):
    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    with engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE feature_key = :k"),
            {"k": LEGAL_FEATURE_KEY},
        ).scalar()
        area_defs = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'area'")
        ).scalar()
    assert count == 0
    assert area_defs == 3, "downgrading 0042 must not touch 0041's own Area seed"
    engine.dispose()


def test_re_upgrade_after_downgrade_reseeds_exactly_one_legal_definition(upgraded):
    down = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert down.returncode == 0, down.stderr
    up = _alembic(upgraded["url"], "upgrade", REVISION)
    assert up.returncode == 0, up.stderr

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    with engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE feature_key = :k"),
            {"k": LEGAL_FEATURE_KEY},
        ).scalar()
    assert count == 1
    engine.dispose()
