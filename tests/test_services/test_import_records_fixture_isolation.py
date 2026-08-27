"""Release-hardening regression test (post-PR-5): proves `test_import_records.py`'s
`clean_db` fixture survives leftover `units`/`areas`/`projects` rows left behind
by ANY other suite (ranking/governance) or by a prior pytest process that was
killed before its own teardown ran.

Root cause this guards against: `clean_db` used to run a narrow, per-table
`DELETE FROM` list (six tables specific to the CSV-import domain) that had no
knowledge of `units`/`deals`/ranking tables. `DELETE FROM areas` there is
NOT scoped to this file's own `PROJECT_ID` — it deletes every row in `areas`
— so a single leftover `units` row anywhere (referencing ANY area) raised
`ForeignKeyViolationError: ... violates foreign key constraint "fk_units_area_id"`
the moment `clean_db` ran, failing a file that has nothing to do with the
actual cause. This was observed for real during a release-hardening sweep,
triggered by a host disk-space exhaustion that killed a Postgres-backed test
process mid-run before its own fixture teardown could clean up.

Fixed by routing `clean_db` through the same `tests.conftest.truncate_tables()`
+ `TRUNCATE ... RESTART IDENTITY CASCADE` mechanism `truncate_all` already
uses for other suites — `CASCADE` empties any FK-dependent table transitively
(`units`, `deals`, `ranking_scores`, ...) whether or not it is named in the
list, so this fixture no longer needs to enumerate every table that could
reference `areas`/`projects` to stay safe.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, projects, units

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _skip_reason() -> str:
    if not TEST_DATABASE_URL:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(TEST_DATABASE_URL).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối chạy trên database '{name}' — chạy `bash scripts/test_db.sh`"
    return ""


_SKIP = _skip_reason()
pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")


async def _leave_orphaned_residue() -> None:
    """Simulates exactly the leftover state a crashed/otherwise-uncleaned
    ranking/governance suite would leave: a `units` row still referencing an
    `areas` row, belonging to a project `test_import_records.py` has never
    heard of."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id, area_id, unit_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(projects).values(
                    id=project_id,
                    name="Orphaned residue project",
                    launch_date=date(2026, 1, 1),
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.execute(
                sa.insert(areas).values(
                    id=area_id,
                    project_id=project_id,
                    area_name="Orphan Tower",
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=50,
                    total_units=1,
                    created_at=now,
                )
            )
            await session.execute(
                sa.insert(units).values(
                    id=unit_id,
                    source_system="mini_crm",
                    source_instance_id="orphan-test",
                    external_unit_id=f"orphan-{uuid.uuid4().hex[:8]}",
                    area_id=area_id,
                    unit_code="ORPHAN-1",
                    unit_type="2PN",
                    status="available",
                    created_at=now,
                    updated_at=now,
                )
            )
    await engine.dispose()


def test_clean_db_survives_leftover_units_referencing_areas_from_another_suite():
    asyncio.run(_leave_orphaned_residue())

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_services/test_import_records.py::test_areas_rows_are_inserted_with_project_id",
            "-q",
        ],
        env=os.environ,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "clean_db must survive leftover units/areas/projects rows from another suite, "
        f"not fail with a foreign-key violation:\n{result.stdout}\n{result.stderr}"
    )
    assert "1 passed" in result.stdout, result.stdout


def test_clean_db_actually_removes_the_leftover_residue():
    """The previous test's own subprocess run must have left the DB clean
    again (both ends of `clean_db` truncate) — no poisoning the NEXT suite
    either."""

    async def _count_units() -> int:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.connect() as conn:
            count = await conn.scalar(sa.select(sa.func.count()).select_from(units))
        await engine.dispose()
        return count

    asyncio.run(_leave_orphaned_residue())
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_services/test_import_records.py::test_areas_rows_are_inserted_with_project_id",
            "-q",
        ],
        env=os.environ,
        capture_output=True,
        text=True,
    )
    assert asyncio.run(_count_units()) == 0, "leftover residue must not survive clean_db's own teardown"
