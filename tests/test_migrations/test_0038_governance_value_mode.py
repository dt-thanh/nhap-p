"""PostgreSQL migration coverage for 0038 — PR-2's governance value-mode
schema widening. Scratch database per test, same pattern as 0033/0034/0037's
migration tests.
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

PREVIOUS_REVISION = "0037_hierarchical_scoring_pr1"
REVISION = "0038_governance_value_mode"

PROJECT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
FEATURE_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
EXPERT_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")


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
    name = f"mig38_{uuid.uuid4().hex[:12]}_test"
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
                "VALUES (:i, 'market_interest_rate', 'v1', 'IR', 'expert', 'market', 'numeric', 'f', 'identity', "
                "'positive', 'skip', 'active', now(), now())"
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
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _insert_proposal(conn, **overrides) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    values = {
        "id": proposal_id,
        "base_config_id": None,
        "scope_type": "market",
        "project_id": PROJECT_ID,
        "area_id": None,
        "status": "draft",
        "created_by_expert_id": EXPERT_ID,
        "assertion_kind": "value",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_weight_proposals (id, base_config_id, scope_type, project_id, area_id, status, "
            "created_by_expert_id, created_at, updated_at, assertion_kind) "
            "VALUES (:id, :base_config_id, :scope_type, :project_id, :area_id, :status, :created_by_expert_id, "
            "now(), now(), :assertion_kind)"
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
        "normalized_numeric": 0.5,
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


# --- Existing weight-mode rows unaffected ------------------------------------


def test_existing_weight_mode_shape_still_inserts_cleanly(upgraded):
    """A pre-0038-shaped weight-mode row (base_config_id set, scope 'project',
    proposed_weight set, every new column at its default/NULL) inserts with no
    error — proves the new CHECKs don't reject what already worked."""
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ranking_configs (id, version, status, weights, min_weight_coverage, note, "
                "created_by, created_at) VALUES (:i, 999, 'draft', "
                "'{\"a\": {\"weight\": 1.0, \"direction\": \"positive\", \"missing_value_policy\": \"skip\"}}'::jsonb, "
                "0.5, '', 'test', now())"
            ),
            {"i": (base_id := uuid.uuid4())},
        )
        proposal_id = _insert_proposal(
            conn, base_config_id=base_id, scope_type="project", assertion_kind="weight"
        )
        _insert_justification(conn, proposal_id, proposed_weight=0.9, normalized_numeric=None, assertion_kind="weight")

    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT assertion_kind FROM ranking_weight_proposals WHERE id = :i"), {"i": proposal_id}
        ).mappings().first()
    assert row["assertion_kind"] == "weight"


def test_default_assertion_kind_is_weight(upgraded):
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ranking_configs (id, version, status, weights, min_weight_coverage, note, "
                "created_by, created_at) VALUES (:i, 998, 'draft', "
                "'{\"a\": {\"weight\": 1.0, \"direction\": \"positive\", \"missing_value_policy\": \"skip\"}}'::jsonb, "
                "0.5, '', 'test', now())"
            ),
            {"i": (base_id := uuid.uuid4())},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ranking_weight_proposals (id, base_config_id, scope_type, project_id, status, "
                "created_by_expert_id, created_at, updated_at) "
                "VALUES (:id, :base_config_id, 'project', :project_id, 'draft', :expert_id, now(), now())"
            ),
            {"id": (proposal_id := uuid.uuid4()), "base_config_id": base_id, "project_id": PROJECT_ID, "expert_id": EXPERT_ID},
        )
    with upgraded["engine"].connect() as conn:
        row = conn.execute(
            sa.text("SELECT assertion_kind FROM ranking_weight_proposals WHERE id = :i"), {"i": proposal_id}
        ).mappings().first()
    assert row["assertion_kind"] == "weight"


# --- Scope/shape CHECKs -------------------------------------------------------


def test_area_scope_without_area_id_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rwp_scope_shape"):
        with upgraded["engine"].begin() as conn:
            _insert_proposal(conn, scope_type="area", area_id=None)


def test_market_scope_with_area_id_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rwp_scope_shape"):
        with upgraded["engine"].begin() as conn:
            _insert_proposal(conn, scope_type="market", area_id=uuid.uuid4())


def test_value_mode_with_base_config_id_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rwp_assertion_kind_config_shape"):
        with upgraded["engine"].begin() as conn:
            _insert_proposal(conn, assertion_kind="value", base_config_id=uuid.uuid4())


def test_weight_mode_without_base_config_id_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rwp_assertion_kind_config_shape"):
        with upgraded["engine"].begin() as conn:
            _insert_proposal(conn, assertion_kind="weight", scope_type="project", base_config_id=None)


# --- Weight XOR value on justifications ---------------------------------------


def test_weight_and_value_both_populated_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rfj_assertion_mode_xor"):
        with upgraded["engine"].begin() as conn:
            proposal_id = _insert_proposal(conn)
            _insert_justification(
                conn, proposal_id, assertion_kind="value", proposed_weight=0.5, normalized_numeric=0.5
            )


def test_value_mode_with_proposed_weight_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rfj_assertion_mode_xor"):
        with upgraded["engine"].begin() as conn:
            proposal_id = _insert_proposal(conn)
            _insert_justification(conn, proposal_id, assertion_kind="value", proposed_weight=0.5)


def test_weight_mode_without_proposed_weight_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rfj_assertion_mode_xor"):
        with upgraded["engine"].begin() as conn:
            with_base = uuid.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_configs (id, version, status, weights, min_weight_coverage, note, "
                    "created_by, created_at) VALUES (:i, 997, 'draft', "
                    "'{\"a\": {\"weight\": 1.0, \"direction\": \"positive\", \"missing_value_policy\": \"skip\"}}'::jsonb, "
                    "0.5, '', 'test', now())"
                ),
                {"i": with_base},
            )
            proposal_id = _insert_proposal(
                conn, assertion_kind="weight", scope_type="project", base_config_id=with_base
            )
            _insert_justification(conn, proposal_id, assertion_kind="weight", proposed_weight=None, normalized_numeric=None)


def test_normalized_numeric_out_of_range_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_rfj_normalized_range"):
        with upgraded["engine"].begin() as conn:
            proposal_id = _insert_proposal(conn)
            _insert_justification(conn, proposal_id, normalized_numeric=1.5)


def test_market_grain_feature_definition_can_be_registered(upgraded):
    with upgraded["engine"].connect() as conn:
        grain = conn.execute(
            sa.text("SELECT grain FROM ranking_feature_definitions WHERE id = :i"), {"i": FEATURE_ID}
        ).scalar_one()
    assert grain == "market"


# --- Downgrade safety ----------------------------------------------------------


def test_downgrade_refuses_when_value_mode_rows_exist(upgraded):
    with upgraded["engine"].begin() as conn:
        proposal_id = _insert_proposal(conn)
        _insert_justification(conn, proposal_id)

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", PREVIOUS_REVISION],
        env={**os.environ, "DATABASE_URL": upgraded["url"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Refusing to downgrade 0038" in (result.stdout + result.stderr)


def test_downgrade_succeeds_when_only_weight_mode_rows_exist(upgraded):
    # The shared `upgraded` fixture seeds one market-grain feature definition
    # for the market-scope tests above; this test is specifically about
    # value-mode PROPOSAL/JUSTIFICATION rows, so clear that one row first —
    # otherwise the downgrade's OWN (correct) market-grain guard fires instead.
    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM ranking_feature_definitions WHERE id = :i"), {"i": FEATURE_ID})

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", PREVIOUS_REVISION],
        env={**os.environ, "DATABASE_URL": upgraded["url"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    with upgraded["engine"].connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'ranking_feature_justifications'"
                )
            )
        }
    assert "assertion_kind" not in columns
    assert "normalized_numeric" not in columns
