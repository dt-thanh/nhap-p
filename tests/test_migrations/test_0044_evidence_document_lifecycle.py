"""PostgreSQL migration coverage for 0044 evidence document lifecycle events
(mandatory-scope item 4 — document archive/delete)."""

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

PREVIOUS_REVISION = "0043_unit_enrichment_attributes"
REVISION = "0044_evidence_document_lifecycle"


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
    name = f"mig44_{uuid.uuid4().hex[:12]}_test"
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


def _seed_document(conn) -> tuple[uuid.UUID, uuid.UUID]:
    expert_id = uuid.uuid4()
    document_id = uuid.uuid4()
    conn.execute(
        sa.text("INSERT INTO expert_profiles (id, identity_subject, status) VALUES (:id, 'subject-1', 'active')"),
        {"id": expert_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO ranking_evidence_documents "
            "(id, uploaded_by_expert_id, original_filename, mime_type, object_storage_key, "
            "sha256_checksum, file_size_bytes, extraction_status) "
            "VALUES (:id, :expert, 'evidence.pdf', 'application/pdf', 'ranking/evidence/one.pdf', :checksum, 10, 'pending')"
        ),
        {"id": document_id, "expert": expert_id, "checksum": "a" * 64},
    )
    return document_id, expert_id


def test_upgrade_creates_lifecycle_table_and_index(upgraded):
    engine = upgraded["engine"]
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'ranking_evidence_document_lifecycle_events'"
                )
            ).scalar()
            == "ranking_evidence_document_lifecycle_events"
        )
        assert (
            conn.execute(
                sa.text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_redle_document_created'")
            ).scalar()
            == 1
        )


def test_event_type_check_constraint_rejects_unknown_value(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        document_id, expert_id = _seed_document(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_evidence_document_lifecycle_events "
                    "(id, document_id, event_type, actor_expert_id) "
                    "VALUES (:id, :document_id, 'bogus', :actor)"
                ),
                {"id": uuid.uuid4(), "document_id": document_id, "actor": expert_id},
            )


def test_archived_event_insert_and_reason_stored(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        document_id, expert_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_document_lifecycle_events "
                "(id, document_id, event_type, actor_expert_id, reason) "
                "VALUES (:id, :document_id, 'archived', :actor, 'superseded by a newer report')"
            ),
            {"id": uuid.uuid4(), "document_id": document_id, "actor": expert_id},
        )
        row = conn.execute(
            sa.text(
                "SELECT event_type, reason FROM ranking_evidence_document_lifecycle_events "
                "WHERE document_id = :document_id"
            ),
            {"document_id": document_id},
        ).one()
        assert row.event_type == "archived"
        assert row.reason == "superseded by a newer report"


def test_append_only_guard_blocks_update_and_delete(upgraded):
    engine = upgraded["engine"]
    event_id = uuid.uuid4()
    with engine.begin() as conn:
        document_id, expert_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_document_lifecycle_events "
                "(id, document_id, event_type, actor_expert_id) "
                "VALUES (:id, :document_id, 'archived', :actor)"
            ),
            {"id": event_id, "document_id": document_id, "actor": expert_id},
        )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE ranking_evidence_document_lifecycle_events SET event_type = 'deleted' WHERE id = :id"
                ),
                {"id": event_id},
            )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM ranking_evidence_document_lifecycle_events WHERE id = :id"),
                {"id": event_id},
            )


def test_downgrade_refuses_populated_table(upgraded):
    with upgraded["engine"].begin() as conn:
        document_id, expert_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_document_lifecycle_events "
                "(id, document_id, event_type, actor_expert_id) "
                "VALUES (:id, :document_id, 'archived', :actor)"
            ),
            {"id": uuid.uuid4(), "document_id": document_id, "actor": expert_id},
        )

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "ranking_evidence_document_lifecycle_events has rows" in (result.stdout + result.stderr)


def test_downgrade_succeeds_when_empty(upgraded):
    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode == 0, result.stderr
