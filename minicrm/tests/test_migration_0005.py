"""Migration 0005 checks; database tests require an isolated Postgres URL."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from tests.conftest import _assert_target_database, db_url, run_alembic, sync_url, target_only_mode, with_database

MINICRM_ROOT = Path(__file__).resolve().parents[1]
MINICRM_DB_URL = db_url()
REVISION = "0005_human_auth_foundation"
PREVIOUS = "0004_outbox_hierarchy_entities"
TABLES = {"crm_users", "crm_auth_sessions", "crm_auth_invites", "crm_password_reset_tokens"}

pytestmark = pytest.mark.skipif(
    not MINICRM_DB_URL,
    reason="Không có MINICRM_TEST_DATABASE_URL/MINICRM_DATABASE_URL — bỏ qua test cần DB thật",
)


@pytest.fixture
def upgraded_db():
    if target_only_mode():
        _assert_target_database(MINICRM_DB_URL)
        run_alembic(MINICRM_DB_URL, "upgrade", REVISION)
        engine = sa.create_engine(sync_url(MINICRM_DB_URL))
        try:
            yield MINICRM_DB_URL, engine
        finally:
            engine.dispose()
        return

    name = f"mc0005_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(sync_url(with_database(MINICRM_DB_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = with_database(MINICRM_DB_URL, name)
    try:
        run_alembic(url, "upgrade", REVISION)
        engine = sa.create_engine(sync_url(url))
        yield url, engine
        engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name})
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_migration_creates_additive_auth_tables(upgraded_db):
    _, engine = upgraded_db
    inspector = sa.inspect(engine)
    assert TABLES <= set(inspector.get_table_names())
    assert {"id", "login", "password_hash", "status", "role", "auth_version"} <= {
        column["name"] for column in inspector.get_columns("crm_users")
    }
    assert {"id", "user_id", "family_id", "refresh_token_hash", "replaced_by"} <= {
        column["name"] for column in inspector.get_columns("crm_auth_sessions")
    }


def test_migration_has_expected_indexes_and_foreign_keys(upgraded_db):
    _, engine = upgraded_db
    inspector = sa.inspect(engine)
    for table, index in (
        ("crm_auth_sessions", "ix_crm_auth_sessions_family_state"),
        ("crm_auth_sessions", "ix_crm_auth_sessions_user_state"),
        ("crm_password_reset_tokens", "ix_crm_password_reset_tokens_user_expiry"),
    ):
        assert index in {item["name"] for item in inspector.get_indexes(table)}
    session_fks = inspector.get_foreign_keys("crm_auth_sessions")
    reset_fks = inspector.get_foreign_keys("crm_password_reset_tokens")
    assert any(fk["referred_table"] == "crm_users" for fk in session_fks)
    assert any(fk["referred_table"] == "crm_users" for fk in reset_fks)


def test_unique_login_and_token_hashes_are_database_enforced(upgraded_db):
    _, engine = upgraded_db
    with engine.begin() as conn:
        user_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO crm_users (id, login, status, role, auth_version, created_at, updated_at) "
                "VALUES (:id, :login, 'invited', 'business_viewer', 1, now(), now())"
            ),
            {"id": user_id, "login": "person@example.test"},
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO crm_users (id, login, status, role, auth_version, created_at, updated_at) "
                    "VALUES (:id, :login, 'invited', 'business_viewer', 1, now(), now())"
                ),
                {"id": uuid.uuid4(), "login": "person@example.test"},
            )


def test_downgrade_removes_only_checkpoint_tables(upgraded_db):
    url, engine = upgraded_db
    engine.dispose()
    run_alembic(url, "downgrade", PREVIOUS)
    check = sa.create_engine(sync_url(url))
    try:
        assert not (TABLES & set(sa.inspect(check).get_table_names()))
        assert sa.inspect(check).has_table("crm_outbox")
    finally:
        check.dispose()
