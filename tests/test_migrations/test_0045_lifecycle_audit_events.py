"""PostgreSQL migration coverage for 0045 — widening
`ranking_config_audit_events.event_type` CHECK to admit the document
lifecycle actions (`archived`, `deleted`, `restored`)."""

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

PREVIOUS_REVISION = "0044_evidence_document_lifecycle"
REVISION = "0045_lifecycle_audit_events"


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
    name = f"mig45_{uuid.uuid4().hex[:12]}_test"
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


def _any_ranking_config_id(conn) -> uuid.UUID:
    """`0022_ranking_config_v2`'s data migration always seeds at least one
    `ranking_configs` row on a fresh database — reuse it to satisfy
    `ck_rcae_entity_reference` (needs `ranking_config_id` OR `proposal_id`
    non-null) rather than build a whole proposal/project fixture chain just
    for this CHECK-constraint test."""
    row = conn.execute(sa.text("SELECT id FROM ranking_configs LIMIT 1")).first()
    assert row is not None, "expected 0022_ranking_config_v2 to have seeded at least one ranking_configs row"
    return row[0]


def test_lifecycle_event_types_now_accepted(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        config_id = _any_ranking_config_id(conn)
        for event_type in ("archived", "deleted", "restored"):
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_config_audit_events "
                    "(id, ranking_config_id, proposal_id, actor_expert_id, actor_identity_subject, "
                    "event_type, before_status, after_status, before_state, after_state) "
                    "VALUES (:id, :config_id, NULL, NULL, 'x', :event_type, NULL, NULL, '{}', '{}')"
                ),
                {"id": uuid.uuid4(), "config_id": config_id, "event_type": event_type},
            )


def test_original_event_types_still_accepted(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        config_id = _any_ranking_config_id(conn)
        for event_type in ("created", "submitted", "reviewed", "approved", "rejected", "published", "rolled_back"):
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_config_audit_events "
                    "(id, ranking_config_id, proposal_id, actor_expert_id, actor_identity_subject, "
                    "event_type, before_status, after_status, before_state, after_state) "
                    "VALUES (:id, :config_id, NULL, NULL, 'x', :event_type, NULL, NULL, '{}', '{}')"
                ),
                {"id": uuid.uuid4(), "config_id": config_id, "event_type": event_type},
            )


def test_unknown_event_type_still_rejected(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        config_id = _any_ranking_config_id(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_config_audit_events "
                    "(id, ranking_config_id, proposal_id, actor_expert_id, actor_identity_subject, "
                    "event_type, before_status, after_status, before_state, after_state) "
                    "VALUES (:id, :config_id, NULL, NULL, 'x', 'bogus', NULL, NULL, '{}', '{}')"
                ),
                {"id": uuid.uuid4(), "config_id": config_id},
            )


def test_downgrade_succeeds_when_no_lifecycle_rows_exist(upgraded):
    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode == 0, result.stderr


def test_downgrade_refuses_when_lifecycle_rows_exist(upgraded):
    with upgraded["engine"].begin() as conn:
        config_id = _any_ranking_config_id(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_config_audit_events "
                "(id, ranking_config_id, proposal_id, actor_expert_id, actor_identity_subject, "
                "event_type, before_status, after_status, before_state, after_state) "
                "VALUES (:id, :config_id, NULL, NULL, 'x', 'archived', NULL, NULL, '{}', '{}')"
            ),
            {"id": uuid.uuid4(), "config_id": config_id},
        )

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "ranking_config_audit_events row" in (result.stdout + result.stderr)
