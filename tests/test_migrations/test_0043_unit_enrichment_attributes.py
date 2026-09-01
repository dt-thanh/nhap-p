"""PostgreSQL migration coverage for 0043 — the generic, reusable
`unit_enrichment_attributes` table. Scratch database per test, same pattern
as 0033/.../0042's migration tests.
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

from src.models.tables import unit_enrichment_attributes

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PostgreSQL is unavailable: TEST_DATABASE_URL/DATABASE_URL is not configured",
)

REVISION = "0043_unit_enrichment_attributes"

PROJECT_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
AREA_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
UNIT_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")


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
    name = f"mig43_{uuid.uuid4().hex[:12]}_test"
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
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "status, created_at, updated_at) "
                "VALUES (:i, :p, 'A', 'T', 1, 10, 1, 'active', now(), now())"
            ),
            {"i": AREA_ID, "p": PROJECT_ID},
        )
        conn.execute(
            sa.text(
                "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, unit_code, "
                "unit_type, status, created_at, updated_at) "
                "VALUES (:i, 'test', 'test', 'U-1', :a, 'U-1', 'T', 'available', now(), now())"
            ),
            {"i": UNIT_ID, "a": AREA_ID},
        )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _insert_row(conn, *, unit_id=UNIT_ID, **overrides) -> uuid.UUID:
    row_id = uuid.uuid4()
    values = {
        "id": row_id,
        "unit_id": unit_id,
        "source_system": "test",
        "source_file": "test.csv",
        "source_file_sha256": "0" * 64,
        "source_row_key": "row-1",
        "import_batch_id": "batch-1",
        **overrides,
    }
    cols = ", ".join(values.keys())
    placeholders = ", ".join(f":{k}" for k in values)
    conn.execute(
        sa.text(f"INSERT INTO unit_enrichment_attributes ({cols}) VALUES ({placeholders})"),
        values,
    )
    return row_id


def test_insert_minimal_row_succeeds(upgraded):
    with upgraded["engine"].begin() as conn:
        _insert_row(conn)


def test_unit_id_is_unique(upgraded):
    with upgraded["engine"].begin() as conn:
        _insert_row(conn)
    with upgraded["engine"].begin() as conn, pytest.raises(IntegrityError):
        _insert_row(conn)


def test_fk_to_units_enforced(upgraded):
    with upgraded["engine"].begin() as conn, pytest.raises(IntegrityError):
        _insert_row(conn, unit_id=uuid.uuid4())


def test_deleting_unit_cascades(upgraded):
    with upgraded["engine"].begin() as conn:
        row_id = _insert_row(conn)
    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM units WHERE id = :i"), {"i": UNIT_ID})
    with upgraded["engine"].connect() as conn:
        remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM unit_enrichment_attributes WHERE id = :i"), {"i": row_id}
        ).scalar()
    assert remaining == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"floor": 0},
        {"floor": 61},
        {"gross_area_sqm": 0},
        {"net_area_sqm": -1},
        {"net_area_sqm": 100, "gross_area_sqm": 50},
        {"standard_price_vnd": -1},
        {"loan_price_vnd": 0},
        {"area_efficiency_ratio": 1.5},
        {"loan_premium_pct": -0.1},
        {"floor_band": "bogus"},
    ],
)
def test_range_and_enum_checks_reject_bad_values(upgraded, overrides):
    with upgraded["engine"].begin() as conn, pytest.raises(IntegrityError):
        _insert_row(conn, **overrides)


@pytest.mark.parametrize("blank_col", ["source_system", "source_file", "source_row_key", "import_batch_id"])
def test_lineage_columns_reject_blank(upgraded, blank_col):
    with upgraded["engine"].begin() as conn, pytest.raises(IntegrityError):
        _insert_row(conn, **{blank_col: ""})


def test_downgrade_refuses_when_rows_exist(upgraded):
    with upgraded["engine"].begin() as conn:
        _insert_row(conn)
    result = _alembic(upgraded["url"], "downgrade", "0042_legal_assertion_gate")
    assert result.returncode != 0
    assert "Refusing to downgrade 0043" in result.stderr


def test_downgrade_succeeds_when_empty(upgraded):
    result = _alembic(upgraded["url"], "downgrade", "0042_legal_assertion_gate")
    assert result.returncode == 0, result.stderr
    with upgraded["engine"].connect() as conn:
        exists = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'unit_enrichment_attributes')"
            )
        ).scalar()
    assert exists is False


def test_core_table_definition_matches_the_migrated_schema(upgraded):
    """`src/models/tables.py::unit_enrichment_attributes` is a projection of
    this migration, not the source of truth. A mismatch means application
    code calls a column name that doesn't exist — same discipline as
    `test_0015_ranking_results.py::test_core_table_definitions_match_the_migrated_schema`.
    """
    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'unit_enrichment_attributes'"
            )
        ).all()
    actual = {name: (is_nullable == "YES") for name, is_nullable in rows}
    expected = {c.name: c.nullable for c in unit_enrichment_attributes.columns}
    assert expected.keys() == actual.keys(), "unit_enrichment_attributes: Core projection column set diverges"
    for name, nullable in expected.items():
        assert nullable == actual[name], f"unit_enrichment_attributes.{name}: nullable mismatch"
