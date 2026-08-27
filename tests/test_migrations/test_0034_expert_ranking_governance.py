"""PostgreSQL migration coverage for 0034 expert governance."""

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

PREVIOUS_REVISION = "0033_ranking_evidence_foundation"
REVISION = "0034_expert_ranking_governance"


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
    name = f"mig34_{uuid.uuid4().hex[:12]}_test"
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


def test_upgrade_creates_governance_tables_and_constraints(upgraded):
    expected = {
        "expert_profiles",
        "ranking_weight_proposals",
        "ranking_feature_justifications",
        "ranking_evidence_documents",
        "ranking_evidence_document_features",
        "ranking_proposal_reviews",
        "ranking_config_audit_events",
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
        for table, constraint in (
            ("ranking_feature_justifications", "uq_ranking_feature_justification_proposal_feature"),
            ("ranking_evidence_documents", "uq_red_object_storage_key"),
            ("ranking_proposal_reviews", "uq_ranking_proposal_review_reviewer"),
        ):
            assert conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass(:table_name) "
                    "AND conname = :constraint_name"
                ),
                {"table_name": table, "constraint_name": constraint},
            ).scalar_one() == 1


def test_evidence_document_mime_checksum_and_size_constraints(upgraded):
    engine = upgraded["engine"]
    project_id = uuid.uuid4()
    expert_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, launch_date, created_at) "
                "VALUES (:id, 'P', '2026-01-01', now())"
            ),
            {"id": project_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO expert_profiles (id, identity_subject, status) "
                "VALUES (:id, 'subject-1', 'active')"
            ),
            {"id": expert_id},
        )

    common = {
        "id": uuid.uuid4(),
        "expert": expert_id,
        "filename": "evidence.pdf",
        "mime": "application/pdf",
        "key": "ranking/evidence/one.pdf",
        "checksum": "a" * 64,
    }
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_documents "
                "(id, uploaded_by_expert_id, original_filename, mime_type, object_storage_key, "
                "sha256_checksum, file_size_bytes, extraction_status) "
                "VALUES (:id, :expert, :filename, :mime, :key, :checksum, 10, 'not_requested')"
            ),
            common,
        )

    for overrides in (
        {"mime": "application/octet-stream", "key": "ranking/evidence/bad-mime"},
        {"checksum": "bad", "key": "ranking/evidence/bad-checksum"},
    ):
        values = {**common, **overrides, "id": uuid.uuid4()}
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO ranking_evidence_documents "
                        "(id, uploaded_by_expert_id, original_filename, mime_type, object_storage_key, "
                        "sha256_checksum, file_size_bytes, extraction_status) "
                        "VALUES (:id, :expert, :filename, :mime, :key, :checksum, 10, 'not_requested')"
                    ),
                    values,
                )


def test_governance_downgrade_refuses_immutable_rows(upgraded):
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO expert_profiles (id, identity_subject, status) "
                "VALUES (:id, 'subject-downgrade', 'active')"
            ),
            {"id": uuid.uuid4()},
        )

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "immutable expert-governance rows exist" in (result.stdout + result.stderr)
