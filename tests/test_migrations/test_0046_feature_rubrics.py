"""PostgreSQL migration coverage for 0046 — versioned evidence-to-value
rubrics for qualitative feature assertions."""

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

PREVIOUS_REVISION = "0045_lifecycle_audit_events"
REVISION = "0046_feature_rubrics"

MVP_FEATURE_KEYS = (
    "market_interest_rate",
    "market_demand",
    "market_credit_policy",
    "area_accessibility",
    "area_current_infrastructure",
    "area_future_infrastructure",
)


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
    name = f"mig46_{uuid.uuid4().hex[:12]}_test"
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


def test_all_six_mvp_features_have_exactly_one_seeded_rubric_with_five_bands(upgraded):
    engine = upgraded["engine"]
    with engine.connect() as conn:
        for feature_key in MVP_FEATURE_KEYS:
            rubric_count = conn.execute(
                sa.text(
                    "SELECT count(*) FROM ranking_feature_rubrics r "
                    "JOIN ranking_feature_definitions d ON d.id = r.feature_definition_id "
                    "WHERE d.feature_key = :key"
                ),
                {"key": feature_key},
            ).scalar_one()
            assert rubric_count == 1, feature_key

            band_rows = conn.execute(
                sa.text(
                    "SELECT b.band_value, b.display_order FROM ranking_feature_rubric_bands b "
                    "JOIN ranking_feature_rubrics r ON r.id = b.rubric_id "
                    "JOIN ranking_feature_definitions d ON d.id = r.feature_definition_id "
                    "WHERE d.feature_key = :key ORDER BY b.display_order"
                ),
                {"key": feature_key},
            ).all()
            assert [float(row[0]) for row in band_rows] == [0.0, 0.25, 0.5, 0.75, 1.0], feature_key
            assert [row[1] for row in band_rows] == [0, 1, 2, 3, 4], feature_key


def test_market_liquidity_deliberately_has_no_rubric(upgraded):
    engine = upgraded["engine"]
    with engine.connect() as conn:
        count = conn.execute(
            sa.text(
                "SELECT count(*) FROM ranking_feature_rubrics r "
                "JOIN ranking_feature_definitions d ON d.id = r.feature_definition_id "
                "WHERE d.feature_key = 'market_liquidity'"
            )
        ).scalar_one()
    assert count == 0


def test_rubric_bands_are_append_only(upgraded):
    engine = upgraded["engine"]
    with engine.connect() as conn:
        rubric_id, band_id = conn.execute(
            sa.text(
                "SELECT r.id, b.id FROM ranking_feature_rubrics r "
                "JOIN ranking_feature_rubric_bands b ON b.rubric_id = r.id LIMIT 1"
            )
        ).first()
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE ranking_feature_rubric_bands SET label = 'x' WHERE id = :id"), {"id": band_id})
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM ranking_feature_rubrics WHERE id = :id"), {"id": rubric_id})


def test_duplicate_band_value_within_a_rubric_rejected(upgraded):
    engine = upgraded["engine"]
    with engine.connect() as conn:
        rubric_id = conn.execute(sa.text("SELECT id FROM ranking_feature_rubrics LIMIT 1")).scalar_one()
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_feature_rubric_bands (id, rubric_id, band_value, label, evidence_requirement, display_order) "
                    "VALUES (:id, :rubric_id, 0.25, 'dup', 'dup', 99)"
                ),
                {"id": uuid.uuid4(), "rubric_id": rubric_id},
            )


def test_out_of_range_band_value_rejected(upgraded):
    engine = upgraded["engine"]
    with engine.connect() as conn:
        rubric_id = conn.execute(sa.text("SELECT id FROM ranking_feature_rubrics LIMIT 1")).scalar_one()
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_feature_rubric_bands (id, rubric_id, band_value, label, evidence_requirement, display_order) "
                    "VALUES (:id, :rubric_id, 1.5, 'bad', 'bad', 99)"
                ),
                {"id": uuid.uuid4(), "rubric_id": rubric_id},
            )


def test_justification_rubric_pair_check_constraint(upgraded):
    """`(rubric_id IS NULL) = (rubric_band_value IS NULL)` — cannot set one
    without the other. Exercised directly against the real constraint using
    a minimal, otherwise-valid weight-mode-shaped row."""
    engine = upgraded["engine"]
    with engine.connect() as conn:
        expert_id = conn.execute(
            sa.text(
                "INSERT INTO expert_profiles (id, identity_subject, status) VALUES (:id, 'x', 'active') RETURNING id"
            ),
            {"id": uuid.uuid4()},
        ).scalar_one()
        project_id = conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, launch_date, created_at, updated_at, absorption_calculator, "
                "external_id, source_system, source_instance_id) "
                "VALUES (:id, 'x', '2026-01-01', now(), now(), 'legacy_aggregate', 'P-RUBRIC-TEST', 'mini_crm', 'test') RETURNING id"
            ),
            {"id": uuid.uuid4()},
        ).scalar_one()
        conn.commit()
        next_version = (conn.execute(sa.text("SELECT coalesce(max(version), 0) + 1 FROM ranking_configs")).scalar_one())
        config_id = conn.execute(
            sa.text(
                "INSERT INTO ranking_configs (id, version, status, weights, created_by, created_at) "
                "VALUES (:id, :version, 'draft', "
                "'{\"unit_available\": {\"weight\": 1.0, \"direction\": \"positive\", \"missing_value_policy\": \"skip\"}}'::jsonb, "
                "'test', now()) RETURNING id"
            ),
            {"id": uuid.uuid4(), "version": next_version},
        ).scalar_one()
        conn.commit()
        proposal_id = conn.execute(
            sa.text(
                "INSERT INTO ranking_weight_proposals (id, base_config_id, scope_type, project_id, status, "
                "created_by_expert_id, created_at, updated_at, assertion_kind) "
                "VALUES (:id, :config_id, 'project', :project_id, 'draft', :expert_id, now(), now(), 'weight') RETURNING id"
            ),
            {"id": uuid.uuid4(), "config_id": config_id, "project_id": project_id, "expert_id": expert_id},
        ).scalar_one()
        feature_id = conn.execute(sa.text("SELECT id FROM ranking_feature_definitions LIMIT 1")).scalar_one()
        conn.commit()

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_feature_justifications "
                    "(id, proposal_id, feature_definition_id, proposed_weight, rationale, methodology, "
                    "evidence_summary, expected_effect, confidence, limitations, created_by_expert_id, "
                    "created_at, updated_at, assertion_kind, rubric_id, rubric_band_value) "
                    "VALUES (:id, :proposal_id, :feature_id, 0.5, 'r', 'm', 'e', 'increase', 'medium', 'l', "
                    ":expert_id, now(), now(), 'weight', NULL, 0.5)"
                ),
                {"id": uuid.uuid4(), "proposal_id": proposal_id, "feature_id": feature_id, "expert_id": expert_id},
            )


def test_downgrade_refuses_when_a_justification_references_a_rubric(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        expert_id = conn.execute(
            sa.text(
                "INSERT INTO expert_profiles (id, identity_subject, status) VALUES (:id, 'y', 'active') RETURNING id"
            ),
            {"id": uuid.uuid4()},
        ).scalar_one()
        project_id = conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, launch_date, created_at, updated_at, absorption_calculator, "
                "external_id, source_system, source_instance_id) "
                "VALUES (:id, 'y', '2026-01-01', now(), now(), 'legacy_aggregate', 'P-RUBRIC-TEST-2', 'mini_crm', 'test') RETURNING id"
            ),
            {"id": uuid.uuid4()},
        ).scalar_one()
        proposal_id = conn.execute(
            sa.text(
                "INSERT INTO ranking_weight_proposals (id, scope_type, project_id, status, created_by_expert_id, "
                "created_at, updated_at, assertion_kind) "
                "VALUES (:id, 'project', :project_id, 'draft', :expert_id, now(), now(), 'value') RETURNING id"
            ),
            {"id": uuid.uuid4(), "project_id": project_id, "expert_id": expert_id},
        ).scalar_one()
        rubric_id, feature_id = conn.execute(
            sa.text("SELECT id, feature_definition_id FROM ranking_feature_rubrics LIMIT 1")
        ).first()
        conn.execute(
            sa.text(
                "INSERT INTO ranking_feature_justifications "
                "(id, proposal_id, feature_definition_id, rationale, methodology, evidence_summary, "
                "expected_effect, confidence, limitations, created_by_expert_id, created_at, updated_at, "
                "assertion_kind, normalized_numeric, rubric_id, rubric_band_value) "
                "VALUES (:id, :proposal_id, :feature_id, 'r', 'm', 'e', 'increase', 'medium', 'l', :expert_id, "
                "now(), now(), 'value', 0.5, :rubric_id, 0.5)"
            ),
            {"id": uuid.uuid4(), "proposal_id": proposal_id, "feature_id": feature_id, "expert_id": expert_id, "rubric_id": rubric_id},
        )

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "reference a rubric" in (result.stdout + result.stderr)
