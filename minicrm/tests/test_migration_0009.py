"""Migration 0009: nullable source-local project location."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.conftest import db_url, run_alembic, sync_url, with_database

MINICRM_ROOT = Path(__file__).resolve().parents[1]
MINICRM_DB_URL = db_url()
REVISION = "0009_project_location"
PREVIOUS = "0008_unit_listing_price"

pytestmark = pytest.mark.skipif(
    not MINICRM_DB_URL,
    reason="Không có MINICRM_TEST_DATABASE_URL/MINICRM_DATABASE_URL — bỏ qua test cần DB thật",
)


@pytest.fixture
def scratch_db():
    name = f"mc0009_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(sync_url(with_database(MINICRM_DB_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield with_database(MINICRM_DB_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name})
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    run_alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(sync_url(scratch_db))
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def test_upgrade_adds_nullable_location_without_losing_existing_rows(upgraded):
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO crm_projects (id, external_id, name, launch_date, status, source_revision, "
                "created_at, updated_at) VALUES (:id, 'P-KEEP', 'Giữ nguyên', '2026-01-01', 'active', 1, now(), now())"
            ),
            {"id": uuid.uuid4()},
        )
        row = conn.execute(sa.text("SELECT name, location FROM crm_projects WHERE external_id = 'P-KEEP'")).one()
    assert row.name == "Giữ nguyên"
    assert row.location is None


def test_location_is_nullable_and_revision_is_head(upgraded):
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
        column = conn.execute(
            sa.text(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name='crm_projects' AND column_name='location'"
            )
        ).one()
    assert (column.data_type, column.is_nullable) == ("text", "YES")


def test_downgrade_removes_only_location_and_upgrade_is_reversible(upgraded):
    upgraded["engine"].dispose()
    run_alembic(upgraded["url"], "downgrade", PREVIOUS)
    run_alembic(upgraded["url"], "upgrade", REVISION)
    engine = sa.create_engine(sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
            columns = set(
                conn.execute(
                    sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='crm_projects'")
                ).scalars()
            )
    finally:
        engine.dispose()
    assert "location" in columns
    assert {"id", "external_id", "name", "launch_date", "status"} <= columns


def test_migration_declares_the_expected_parent():
    text = (MINICRM_ROOT / "alembic" / "versions" / "0009_project_location.py").read_text(encoding="utf-8")
    assert f'down_revision = "{PREVIOUS}"' in text
