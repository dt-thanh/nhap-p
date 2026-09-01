"""Regression coverage for durable project ownership of standalone evidence."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="PostgreSQL is unavailable")

PREVIOUS_REVISION = "0046_feature_rubrics"
REVISION = "0047_evidence_project_scope"


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
def upgraded():
    name = f"mig47_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = _with_database(TEST_DATABASE_URL, name)
    try:
        result = _alembic(url, "upgrade", REVISION)
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


def test_document_project_and_area_associations_are_nullable_and_indexed(upgraded):
    inspector = sa.inspect(upgraded)
    columns = {column["name"]: column for column in inspector.get_columns("ranking_evidence_documents")}
    assert columns["project_id"]["nullable"] is True
    assert columns["area_id"]["nullable"] is True
    foreign_keys = {fk["name"]: fk for fk in inspector.get_foreign_keys("ranking_evidence_documents")}
    assert foreign_keys["fk_red_project_id"]["referred_table"] == "projects"
    assert foreign_keys["fk_red_area_id"]["referred_table"] == "areas"
    indexes = {index["name"] for index in inspector.get_indexes("ranking_evidence_documents")}
    assert {"ix_red_project_id", "ix_red_area_id"}.issubset(indexes)


def test_upgrade_does_not_backfill_or_guess_historical_document_scope(upgraded):
    with upgraded.connect() as conn:
        # The migration is additive only.  No update/backfill statement is
        # allowed, so pre-existing (or future audit-only) documents preserve
        # NULL scope until an explicit audited association is designed.
        assert conn.execute(
            sa.text("SELECT count(*) FROM ranking_evidence_documents WHERE project_id IS NOT NULL OR area_id IS NOT NULL")
        ).scalar_one() == 0
