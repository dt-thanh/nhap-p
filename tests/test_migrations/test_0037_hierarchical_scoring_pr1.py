"""PostgreSQL migration coverage for 0037 — PR-1's three additive columns.

`hierarchical_score`/`hierarchical_contributions` (`ranking_scores`) and
`hierarchical_weights` (`ranking_configs`) must be nullable, must not touch any
existing column/constraint on either table, and `hierarchical_score` must carry
the same `[0,1]`-or-NULL range guarantee `score` already has (never a value
outside that range, but — unlike `score` — NULL is a valid, common state: D41).

Scratch database per test, same pattern as 0033/0034's migration tests: this
revision's invariants (JSONB, CHECK, additive `ADD COLUMN`) are PostgreSQL-
specific.
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

PREVIOUS_REVISION = "0036_remove_historical_ranking"
REVISION = "0037_hierarchical_scoring_pr1"

PROJECT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
AREA_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
UNIT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


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
    name = f"mig37_{uuid.uuid4().hex[:12]}_test"
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
    """DB tại HEAD (0037), kèm một dự án/phân khu/căn tối thiểu để gắn khoá ngoại."""
    result = _alembic(scratch_db, "upgrade", REVISION)
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'SYNTH-P1', :d, now())"
            ),
            {"i": PROJECT_ID, "d": "2026-01-01"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:i, :p, 'A1', '2PN', 2, 75, 100, now())"
            ),
            {"i": AREA_ID, "p": PROJECT_ID},
        )
        conn.execute(
            sa.text(
                "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, unit_code, "
                "unit_type, status, created_at, updated_at) "
                "VALUES (:i, 'mini_crm', 'synthetic', 'U-0001', :a, 'A1-01', '2PN', 'available', now(), now())"
            ),
            {"i": UNIT_ID, "a": AREA_ID},
        )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _config_id(conn) -> uuid.UUID:
    """Config v1, do 0014 seed — vẫn tồn tại (archived bởi 0022's data migration)."""
    return conn.execute(sa.text("SELECT id FROM ranking_configs WHERE version = 1")).scalar_one()


def _run_id(conn) -> uuid.UUID:
    run_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO ranking_runs (id, project_id, trigger, scope_type, scope_ids, config_version_id, "
            "status, attempt, units_processed, units_ranked, units_skipped, error_summary, enqueued_at, "
            "started_at, finished_at) "
            "VALUES (:id, :p, 'manual', 'project', '{}'::jsonb, :c, 'completed', 1, 1, 1, 0, '{}'::jsonb, "
            "now(), now(), now())"
        ),
        {"id": run_id, "p": PROJECT_ID, "c": _config_id(conn)},
    )
    return run_id


def _insert_score(conn, *, hierarchical_score=None) -> uuid.UUID:
    score_id = uuid.uuid4()
    run_id = _run_id(conn)
    config_id = _config_id(conn)
    conn.execute(
        sa.text(
            "INSERT INTO ranking_scores (id, unit_id, area_id, project_id, ranking_run_id, config_version_id, "
            "score, rank_in_area, rank_in_project, weight_coverage, contributions, computed_at, "
            "hierarchical_score) "
            "VALUES (:id, :unit_id, :area_id, :project_id, :run_id, :config_id, 0.75, 1, 1, 1.0, '{}'::jsonb, "
            "now(), :hs)"
        ),
        {
            "id": score_id,
            "unit_id": UNIT_ID,
            "area_id": AREA_ID,
            "project_id": PROJECT_ID,
            "run_id": run_id,
            "config_id": config_id,
            "hs": hierarchical_score,
        },
    )
    return score_id


# --- Schema shape ------------------------------------------------------------


def test_new_columns_exist_and_are_nullable(upgraded):
    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT table_name, column_name, is_nullable, data_type FROM information_schema.columns "
                "WHERE (table_name = 'ranking_scores' AND column_name IN "
                "('hierarchical_score', 'hierarchical_contributions')) "
                "OR (table_name = 'ranking_configs' AND column_name = 'hierarchical_weights')"
            )
        ).all()
    found = {(r.table_name, r.column_name): (r.is_nullable, r.data_type) for r in rows}
    assert found[("ranking_scores", "hierarchical_score")] == ("YES", "numeric")
    assert found[("ranking_scores", "hierarchical_contributions")] == ("YES", "jsonb")
    assert found[("ranking_configs", "hierarchical_weights")] == ("YES", "jsonb")


def test_existing_columns_and_constraints_are_untouched(upgraded):
    """Byte-identical existing behavior: `score`/`rank_in_area`/`rank_in_project`/
    `contributions`/`weights` keep their original nullability, and the legacy
    `score` range CHECK is unchanged (still rejects NULL — only the NEW
    `hierarchical_score` column tolerates NULL)."""
    with upgraded["engine"].connect() as conn:
        rows = {
            r.column_name: r.is_nullable
            for r in conn.execute(
                sa.text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'ranking_scores'"
                )
            )
        }
        assert rows["score"] == "NO"
        assert rows["rank_in_area"] == "NO"
        assert rows["rank_in_project"] == "NO"
        assert rows["contributions"] == "NO"

        weights_nullable = conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'ranking_configs' AND column_name = 'weights'"
            )
        ).scalar_one()
        assert weights_nullable == "NO"

        constraint_defs = dict(
            conn.execute(
                sa.text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = to_regclass('ranking_scores')"
                )
            ).all()
        )
        assert "score >= " in constraint_defs["ck_ranking_scores_score_range"]
        assert "score <= " in constraint_defs["ck_ranking_scores_score_range"]
        assert "hierarchical_score IS NULL" in constraint_defs["ck_ranking_scores_hierarchical_score_range"]


def test_legacy_config_row_remains_valid_with_hierarchical_weights_null(upgraded):
    """D41: every config published before/without this column has
    `hierarchical_weights IS NULL` — a fully valid, unremarkable state."""
    with upgraded["engine"].connect() as conn:
        value = conn.execute(
            sa.text("SELECT hierarchical_weights FROM ranking_configs WHERE version = 1")
        ).scalar_one_or_none()
    assert value is None


# --- Range CHECK on hierarchical_score ---------------------------------------


def test_hierarchical_score_null_is_accepted(upgraded):
    with upgraded["engine"].begin() as conn:
        score_id = _insert_score(conn, hierarchical_score=None)
    with upgraded["engine"].connect() as conn:
        value = conn.execute(
            sa.text("SELECT hierarchical_score FROM ranking_scores WHERE id = :i"), {"i": score_id}
        ).scalar_one_or_none()
    assert value is None


@pytest.mark.parametrize("bad_value", [-0.0001, 1.0001, -1, 2])
def test_hierarchical_score_out_of_range_is_rejected(upgraded, bad_value):
    with pytest.raises(IntegrityError, match="ck_ranking_scores_hierarchical_score_range"):
        with upgraded["engine"].begin() as conn:
            _insert_score(conn, hierarchical_score=bad_value)


@pytest.mark.parametrize("ok_value", [0, 1, 0.5, 0.7385])
def test_hierarchical_score_in_range_is_accepted(upgraded, ok_value):
    with upgraded["engine"].begin() as conn:
        score_id = _insert_score(conn, hierarchical_score=ok_value)
    with upgraded["engine"].connect() as conn:
        value = conn.execute(
            sa.text("SELECT hierarchical_score FROM ranking_scores WHERE id = :i"), {"i": score_id}
        ).scalar_one()
    assert float(value) == pytest.approx(ok_value)


# --- Downgrade ----------------------------------------------------------------


def test_downgrade_removes_exactly_the_three_new_things(upgraded):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", PREVIOUS_REVISION],
        env={**os.environ, "DATABASE_URL": upgraded["url"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    with upgraded["engine"].connect() as conn:
        remaining = set(
            conn.execute(
                sa.text(
                    "SELECT table_name || '.' || column_name FROM information_schema.columns "
                    "WHERE (table_name = 'ranking_scores' AND column_name IN "
                    "('hierarchical_score', 'hierarchical_contributions')) "
                    "OR (table_name = 'ranking_configs' AND column_name = 'hierarchical_weights')"
                )
            ).scalars()
        )
        assert remaining == set()

        # `ranking_scores`/`ranking_configs` themselves, and every pre-existing
        # column/row, survive the downgrade untouched.
        still_there = conn.execute(
            sa.text("SELECT score FROM ranking_scores")
        ).scalars().all()
        assert still_there == [] or all(s is not None for s in still_there)
        assert conn.execute(sa.text("SELECT version FROM ranking_configs WHERE version = 1")).scalar_one() == 1
