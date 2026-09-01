"""Migration contract for the immutable CEO evidence acknowledgement."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required for isolated migration tests")

PREVIOUS_REVISION = "0047_evidence_project_scope"
REVISION = "0048_review_evidence_ack"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture
def upgraded():
    name = f"mig48_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = _with_database(TEST_DATABASE_URL, name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", REVISION],
            env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        engine = sa.create_engine(_sync_url(url))
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"), {"name": name})
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_acknowledgement_is_additive_nullable_and_has_no_default(upgraded):
    column = next(item for item in sa.inspect(upgraded).get_columns("ranking_proposal_reviews") if item["name"] == "evidence_review_acknowledged")
    assert column["nullable"] is True
    assert column["default"] is None
