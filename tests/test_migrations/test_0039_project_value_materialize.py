"""PostgreSQL migration coverage for 0039 — PR-3's Project-grain
materialization link (`ranking_feature_values.source_justification_id`).
Scratch database per test, same pattern as 0033/0034/0037/0038's migration
tests.
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

PREVIOUS_REVISION = "0038_governance_value_mode"
REVISION = "0039_project_value_materialize"

PROJECT_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
FEATURE_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")
EXPERT_ID = uuid.UUID("99999999-9999-4999-8999-999999999999")
RUN_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


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
    name = f"mig39_{uuid.uuid4().hex[:12]}_test"
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
            sa.text(
                "INSERT INTO ranking_feature_definitions (id, feature_key, feature_version, name, category, "
                "grain, value_type, formula_id, normalization_method, direction, missing_policy, status, "
                "created_at, updated_at) "
                "VALUES (:i, 'expert_location_score', 'v1', 'Location', 'expert', 'project', 'numeric', 'f', "
                "'identity', 'positive', 'skip', 'active', now(), now())"
            ),
            {"i": FEATURE_ID},
        )
        conn.execute(
            sa.text(
                "INSERT INTO expert_profiles (id, identity_subject, status, created_at, updated_at) "
                "VALUES (:i, 'analyst@example.com', 'active', now(), now())"
            ),
            {"i": EXPERT_ID},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ranking_runs (id, project_id, trigger, enqueued_at) "
                "VALUES (:i, :p, 'manual', now())"
            ),
            {"i": RUN_ID, "p": PROJECT_ID},
        )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _insert_proposal(conn, **overrides) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    values = {
        "id": proposal_id,
        "base_config_id": None,
        "scope_type": "project",
        "project_id": PROJECT_ID,
        "area_id": None,
        "status": "published",
        "created_by_expert_id": EXPERT_ID,
        "assertion_kind": "value",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_weight_proposals (id, base_config_id, scope_type, project_id, area_id, status, "
            "created_by_expert_id, created_at, updated_at, assertion_kind, published_at, approved_at, submitted_at) "
            "VALUES (:id, :base_config_id, :scope_type, :project_id, :area_id, :status, :created_by_expert_id, "
            "now(), now(), :assertion_kind, now(), now(), now())"
        ),
        values,
    )
    return proposal_id


def _insert_justification(conn, proposal_id, **overrides) -> uuid.UUID:
    justification_id = uuid.uuid4()
    values = {
        "id": justification_id,
        "proposal_id": proposal_id,
        "feature_definition_id": FEATURE_ID,
        "proposed_weight": None,
        "rationale": "r",
        "methodology": "m",
        "evidence_summary": "e",
        "expected_effect": "increase",
        "confidence": "medium",
        "limitations": "l",
        "created_by_expert_id": EXPERT_ID,
        "assertion_kind": "value",
        "normalized_numeric": 0.8,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_justifications (id, proposal_id, feature_definition_id, proposed_weight, "
            "rationale, methodology, evidence_summary, expected_effect, confidence, limitations, "
            "created_by_expert_id, created_at, updated_at, assertion_kind, normalized_numeric) "
            "VALUES (:id, :proposal_id, :feature_definition_id, :proposed_weight, :rationale, :methodology, "
            ":evidence_summary, :expected_effect, :confidence, :limitations, :created_by_expert_id, now(), now(), "
            ":assertion_kind, :normalized_numeric)"
        ),
        values,
    )
    return justification_id


def _insert_snapshot(conn, **overrides) -> uuid.UUID:
    snapshot_id = uuid.uuid4()
    values = {
        "id": snapshot_id,
        "ranking_run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "scope_type": "project",
        "cutoff_at": None,
        "computed_at": None,
        "feature_set_version": "hierarchical-project-v1",
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


def _insert_value(conn, snapshot_id, **overrides) -> uuid.UUID:
    value_id = uuid.uuid4()
    values = {
        "id": value_id,
        "snapshot_id": snapshot_id,
        "feature_definition_id": FEATURE_ID,
        "project_id": PROJECT_ID,
        "scope_type": "project",
        "value_kind": "numeric",
        "normalized_numeric": 0.8,
        "quality_status": "ok",
        "source_justification_id": None,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_feature_values (id, snapshot_id, feature_definition_id, project_id, scope_type, "
            "value_kind, normalized_numeric, quality_status, created_at, source_justification_id) "
            "VALUES (:id, :snapshot_id, :feature_definition_id, :project_id, :scope_type, :value_kind, "
            ":normalized_numeric, :quality_status, now(), :source_justification_id)"
        ),
        values,
    )
    return value_id


# --- Existing PR-2 rows remain valid ------------------------------------------


def test_existing_value_mode_proposal_and_justification_still_insert(upgraded):
    with upgraded["engine"].begin() as conn:
        proposal_id = _insert_proposal(conn)
        _insert_justification(conn, proposal_id)
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT assertion_kind FROM ranking_weight_proposals WHERE id = :i"), {"i": proposal_id}
        ).mappings().first()
    assert row["assertion_kind"] == "value"


# --- New column: shape, nullability, FK ---------------------------------------


def test_source_justification_id_column_exists_and_is_nullable(upgraded):
    other_feature_id = uuid.uuid4()
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ranking_feature_definitions (id, feature_key, feature_version, name, category, "
                "grain, value_type, formula_id, normalization_method, direction, missing_policy, status, "
                "created_at, updated_at) "
                "VALUES (:i, 'expert_financing_score', 'v1', 'Financing', 'expert', 'project', 'numeric', 'f', "
                "'identity', 'positive', 'skip', 'active', now(), now())"
            ),
            {"i": other_feature_id},
        )
        proposal_id = _insert_proposal(conn)
        justification_id = _insert_justification(conn, proposal_id)
        snapshot_id = _insert_snapshot(conn)
        # Nullable: a materialized value need not carry the link (defensive,
        # not expected in practice — PR-3's writer always sets it). A
        # DIFFERENT feature_definition_id than the linked one below, so both
        # rows coexist under `uq_ranking_feature_value_scope` (0033).
        _insert_value(conn, snapshot_id, feature_definition_id=other_feature_id, source_justification_id=None)
        # FK: a valid justification id is accepted.
        _insert_value(conn, snapshot_id, feature_definition_id=FEATURE_ID, source_justification_id=justification_id)
    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text("SELECT source_justification_id FROM ranking_feature_values WHERE snapshot_id = :s"),
            {"s": snapshot_id},
        ).fetchall()
    assert {r[0] for r in rows} == {None, justification_id}


def test_source_justification_id_rejects_a_nonexistent_justification(upgraded):
    with upgraded["engine"].begin() as conn:
        snapshot_id = _insert_snapshot(conn)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_value(conn, snapshot_id, source_justification_id=uuid.uuid4())


# --- Pre-existing 0033 constraints still hold in the PR-3 context -------------


def test_idempotency_constraint_rejects_a_duplicate_value_for_the_same_snapshot_and_feature(upgraded):
    """`uq_ranking_feature_value_scope` (0033) — PR-3's idempotency guarantee
    rests on this pre-existing constraint, not a new one; this proves it is
    still exactly what materialize_published_feature_value() relies on."""
    with upgraded["engine"].begin() as conn:
        proposal_id = _insert_proposal(conn)
        justification_id = _insert_justification(conn, proposal_id)
        snapshot_id = _insert_snapshot(conn)
        _insert_value(conn, snapshot_id, source_justification_id=justification_id)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_value(conn, snapshot_id, source_justification_id=justification_id)


def test_normalized_numeric_outside_zero_one_is_rejected(upgraded):
    with upgraded["engine"].begin() as conn:
        snapshot_id = _insert_snapshot(conn)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_value(conn, snapshot_id, normalized_numeric=1.5)


def test_non_project_scope_is_rejected(upgraded):
    """`ck_rfv_scope_type_project` (0033, unchanged by 0039) — PR-3 never
    widens this; Market/Area materialization is explicitly out of scope."""
    with upgraded["engine"].begin() as conn:
        snapshot_id = _insert_snapshot(conn)
    with pytest.raises(IntegrityError):
        with upgraded["engine"].begin() as conn:
            _insert_value(conn, snapshot_id, scope_type="market")


# --- Downgrade -----------------------------------------------------------------


def test_downgrade_refuses_when_a_materialized_link_exists(upgraded):
    with upgraded["engine"].begin() as conn:
        proposal_id = _insert_proposal(conn)
        justification_id = _insert_justification(conn, proposal_id)
        snapshot_id = _insert_snapshot(conn)
        _insert_value(conn, snapshot_id, source_justification_id=justification_id)

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "Refusing to downgrade 0039" in result.stderr


def test_downgrade_succeeds_when_no_materialized_link_exists(upgraded):
    with upgraded["engine"].begin() as conn:
        snapshot_id = _insert_snapshot(conn)
        _insert_value(conn, snapshot_id, source_justification_id=None)

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode == 0, result.stderr
