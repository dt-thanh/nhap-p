"""PostgreSQL migration coverage for 0033.

These tests intentionally skip without TEST_DATABASE_URL/DATABASE_URL.  The
revision is database-specific because its invariants use PostgreSQL UUID,
JSONB, partial indexes, and composite foreign keys.
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

PREVIOUS_REVISION = "0032_replay_identity_index"
REVISION = "0033_ranking_evidence_foundation"


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
    name = f"mig33_{uuid.uuid4().hex[:12]}_test"
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
    try:
        yield {"url": scratch_db, "engine": engine}
    finally:
        engine.dispose()


def _constraint_names(conn, table: str) -> set[str]:
    return set(
        conn.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = to_regclass(:table_name)"
            ),
            {"table_name": table},
        ).scalars()
    )


def _seed_project_run_feature(conn):
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO projects (id, name, launch_date, created_at) "
            "VALUES (:id, 'P', '2026-01-01', now())"
        ),
        {"id": project_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO ranking_runs "
            "(id, project_id, trigger, scope_type, scope_ids, status, attempt, units_processed, "
            "units_ranked, units_skipped, error_summary, enqueued_at, started_at, finished_at) "
            "VALUES (:id, :project_id, 'manual', 'project', '{}'::jsonb, 'completed', 1, 0, 0, 0, "
            "'{}'::jsonb, now(), now(), now())"
        ),
        {"id": run_id, "project_id": project_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_definitions "
            "(id, feature_key, feature_version, name, category, grain, value_type, formula_id, "
            "normalization_method, direction, missing_policy, status, definition_metadata, created_at, updated_at) "
            "VALUES (:id, 'test.feature', 'v1', 'Test feature', 'test', 'project', 'numeric', "
            "'formula.v1', 'identity.v1', 'positive', 'skip', 'active', '{}'::jsonb, now(), now())"
        ),
        {"id": definition_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_snapshots "
            "(id, ranking_run_id, project_id, scope_type, cutoff_at, computed_at, feature_set_version, "
            "quality_status, quality_summary) "
            "VALUES (:id, :run_id, :project_id, 'project', now() - interval '1 minute', now(), 'v1', 'ok', '{}')"
        ),
        {"id": snapshot_id, "run_id": run_id, "project_id": project_id},
    )
    return project_id, run_id, definition_id, snapshot_id


def _seed_rankable_unit(conn, project_id):
    area_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, created_at) "
            "VALUES (:id, :project, 'A1', '2PN', 2, 75, 10, now())"
        ),
        {"id": area_id, "project": project_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO units "
            "(id, source_system, source_instance_id, external_unit_id, area_id, unit_code, unit_type, "
            "status, created_at, updated_at) VALUES (:id, 'verification', 'verification', :external_id, "
            ":area, 'A1-01', '2PN', 'available', now(), now())"
        ),
        {"id": unit_id, "external_id": str(unit_id), "area": area_id},
    )
    return area_id, unit_id


def test_upgrade_creates_tables_constraints_and_preserves_legacy_tables(upgraded):
    expected = {
        "ranking_feature_definitions",
        "ranking_config_features",
        "ranking_feature_snapshots",
        "ranking_feature_values",
        "ranking_feature_lineage",
        "ranking_explanations",
    }
    with upgraded["engine"].connect() as conn:
        found = set(
            conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
                ),
                {"tables": list(expected)},
            ).scalars()
        )
        assert found == expected
        assert conn.execute(sa.text("SELECT to_regclass('feature_snapshots')")).scalar() is not None
        assert conn.execute(sa.text("SELECT to_regclass('ranking_scores')")).scalar() is not None
        assert "uq_ranking_feature_definition_version" in _constraint_names(
            conn, "ranking_feature_definitions"
        )
        assert "ck_rfv_typed_value_missing_semantics" in _constraint_names(conn, "ranking_feature_values")
        assert "uq_ranking_explanation_run_unit_feature" in _constraint_names(conn, "ranking_explanations")


def test_project_scope_and_typed_missing_invariants(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        project_id, run_id, definition_id, snapshot_id = _seed_project_run_feature(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_feature_values "
                "(id, snapshot_id, feature_definition_id, project_id, scope_type, value_kind, "
                "normalized_numeric, quality_status) VALUES (:id, :snapshot, :definition, :project, "
                "'project', 'numeric', 0.75, 'ok')"
            ),
            {
                "id": uuid.uuid4(),
                "snapshot": snapshot_id,
                "definition": definition_id,
                "project": project_id,
            },
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_feature_values "
                    "(id, snapshot_id, feature_definition_id, project_id, scope_type, value_kind, "
                    "raw_numeric, quality_status, missing_reason) VALUES (:id, :snapshot, :definition, "
                    ":project, 'project', 'numeric', 1, 'unavailable', 'bad')"
                ),
                {
                    "id": uuid.uuid4(),
                    "snapshot": snapshot_id,
                    "definition": definition_id,
                    "project": project_id,
                },
            )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_feature_values "
                    "(id, snapshot_id, feature_definition_id, project_id, scope_type, value_kind, "
                    "normalized_numeric, quality_status) VALUES (:id, :snapshot, :definition, :project, "
                    "'project', 'numeric', 1.5, 'ok')"
                ),
                {
                    "id": uuid.uuid4(),
                    "snapshot": snapshot_id,
                    "definition": definition_id,
                    "project": project_id,
                },
            )

    assert run_id


def test_explanations_survive_score_delete_reinsert_and_remain_append_only(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        project_id, run_id, definition_id, snapshot_id = _seed_project_run_feature(conn)
        area_id, unit_id = _seed_rankable_unit(conn, project_id)
        config_id = conn.execute(sa.text("SELECT id FROM ranking_configs ORDER BY version DESC LIMIT 1")).scalar_one()
        value_id = uuid.uuid4()
        score_id = uuid.uuid4()
        explanation_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO ranking_feature_values "
                "(id, snapshot_id, feature_definition_id, project_id, scope_type, value_kind, "
                "normalized_numeric, quality_status) VALUES (:id, :snapshot, :definition, :project, "
                "'project', 'numeric', 0.5, 'ok')"
            ),
            {"id": value_id, "snapshot": snapshot_id, "definition": definition_id, "project": project_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ranking_scores "
                "(id, unit_id, area_id, project_id, ranking_run_id, config_version_id, score, "
                "rank_in_area, rank_in_project, weight_coverage, contributions, computed_at) "
                "VALUES (:id, :unit, :area, :project, :run, :config, 0.5, 1, 1, 1, '{}', now())"
            ),
            {
                "id": score_id,
                "unit": unit_id,
                "area": area_id,
                "project": project_id,
                "run": run_id,
                "config": config_id,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO ranking_explanations "
                "(id, ranking_run_id, unit_id, feature_value_id, feature_definition_id, raw_value, "
                "normalized_value, weight, direction, contribution, formula_id, interpretation_code, quality_status) "
                "VALUES (:id, :run, :unit, :value, :definition, '0.5', 0.5, 0.2, 'positive', "
                "0.1, 'formula.v1', 'verification', 'ok')"
            ),
            {
                "id": explanation_id,
                "run": run_id,
                "unit": unit_id,
                "value": value_id,
                "definition": definition_id,
            },
        )

        before = conn.execute(
            sa.text(
                "SELECT ranking_run_id, unit_id, feature_definition_id, feature_value_id, raw_value, "
                "normalized_value, weight, direction, contribution, formula_id, interpretation_code, quality_status "
                "FROM ranking_explanations WHERE id = :id"
            ),
            {"id": explanation_id},
        ).one()

        conn.execute(sa.text("DELETE FROM ranking_scores WHERE id = :id"), {"id": score_id})
        conn.execute(
            sa.text(
                "INSERT INTO ranking_scores "
                "(id, unit_id, area_id, project_id, ranking_run_id, config_version_id, score, "
                "rank_in_area, rank_in_project, weight_coverage, contributions, computed_at) "
                "VALUES (:id, :unit, :area, :project, :run, :config, 0.6, 1, 1, 1, '{}', now())"
            ),
            {
                "id": uuid.uuid4(),
                "unit": unit_id,
                "area": area_id,
                "project": project_id,
                "run": run_id,
                "config": config_id,
            },
        )
        after = conn.execute(
            sa.text(
                "SELECT ranking_run_id, unit_id, feature_definition_id, feature_value_id, raw_value, "
                "normalized_value, weight, direction, contribution, formula_id, interpretation_code, quality_status "
                "FROM ranking_explanations WHERE id = :id"
            ),
            {"id": explanation_id},
        ).one()
        assert after == before

        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            with conn.begin_nested():
                conn.execute(
                    sa.text("UPDATE ranking_explanations SET raw_value = 'changed' WHERE id = :id"),
                    {"id": explanation_id},
                )
        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            with conn.begin_nested():
                conn.execute(
                    sa.text("DELETE FROM ranking_explanations WHERE id = :id"),
                    {"id": explanation_id},
                )

        assert conn.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('ranking_explanations') "
                "AND confrelid = to_regclass('ranking_scores')"
            )
        ).scalar() is None


def test_downgrade_refuses_immutable_evidence_data(upgraded):
    with upgraded["engine"].begin() as conn:
        _seed_project_run_feature(conn)

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "immutable ranking evidence rows exist" in (result.stdout + result.stderr)
