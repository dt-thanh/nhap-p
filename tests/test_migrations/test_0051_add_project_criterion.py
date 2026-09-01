"""Migration contract for the governed weighted Project criterion."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL/DATABASE_URL is not configured")

REVISION = "0051_add_project_criterion"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture
def upgraded():
    name = f"mig51_{uuid.uuid4().hex[:12]}_test"
    db_url = _with_database(TEST_DATABASE_URL, name)
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", REVISION],
            env={**os.environ, "DATABASE_URL": db_url},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        engine = sa.create_engine(_sync_url(db_url))
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"), {"name": name}
            )
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_project_design_score_is_active_expert_numeric_with_a_versioned_rubric(upgraded):
    with upgraded.connect() as connection:
        feature = connection.execute(sa.text(
            "SELECT grain, category, value_type, direction, missing_policy, status, normalization_method "
            "FROM ranking_feature_definitions WHERE feature_key = 'project_design_score'"
        )).mappings().one()
        bands = connection.execute(sa.text(
            "SELECT b.band_value FROM ranking_feature_rubric_bands b "
            "JOIN ranking_feature_rubrics r ON r.id = b.rubric_id "
            "JOIN ranking_feature_definitions d ON d.id = r.feature_definition_id "
            "WHERE d.feature_key = 'project_design_score' ORDER BY b.display_order"
        )).scalars().all()
    assert feature == {
        "grain": "project", "category": "expert", "value_type": "numeric", "direction": "positive",
        "missing_policy": "neutral", "status": "active", "normalization_method": "rubric_band",
    }
    assert [float(value) for value in bands] == [0.0, 0.25, 0.5, 0.75, 1.0]
