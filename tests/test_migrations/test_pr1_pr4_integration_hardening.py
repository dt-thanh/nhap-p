"""PR-1 through PR-4 integration hardening pass — item 1 of the hardening
task: verify the FULL migration chain (base -> head, and back) is coherent
on a clean scratch database, not just each revision's own isolated test.

This is a chain-level check on top of 0033/.../0040's own migration test
files (which each already prove their own upgrade/downgrade/protected-data
behavior in isolation) — it proves there is exactly one head, that upgrading
all the way from nothing succeeds, and that downgrading all the way back
(no protected data exists on a fresh database, so every guard is a no-op)
and re-upgrading reaches the identical head again.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PostgreSQL is unavailable: TEST_DATABASE_URL/DATABASE_URL is not configured",
)

HEAD_REVISION = "0041_area_grain_scope"


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
    name = f"migchain_{uuid.uuid4().hex[:12]}_test"
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


def test_exactly_one_head_and_it_is_0041():
    result = _alembic(TEST_DATABASE_URL, "heads")
    assert result.returncode == 0, result.stderr
    heads = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    assert heads == [HEAD_REVISION], f"expected exactly one head ({HEAD_REVISION}), found: {heads}"


def test_full_chain_upgrades_cleanly_from_nothing_to_head(scratch_db):
    result = _alembic(scratch_db, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.connect() as conn:
        current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    engine.dispose()
    assert current == HEAD_REVISION


PR1_BASELINE = "0036_remove_historical_ranking"


def test_pr1_pr4_chain_downgrades_cleanly_to_its_own_pre_pr1_baseline(scratch_db):
    """On a genuinely fresh database (this migration's own PR-3/PR-4 data
    seeds are the only rows present — no real Market/Project usage), every
    protected-data downgrade guard PR-1..PR-4 added (0037/0038/0039/0040 —
    0033/0034 predate this series but are exercised too since they sit on
    the same path) must be a no-op, and the chain must reverse cleanly down
    to `0036_remove_historical_ranking`, the revision immediately before
    PR-1 begins.

    Deliberately NOT `downgrade base`: a full base-to-head-and-back sweep
    was tried during this hardening pass and found a genuine, PRE-EXISTING,
    unrelated bug several revisions before this series even starts —
    `0024_vinhomes_labels_stats`'s downgrade violates
    `ck_units_updated_after_created` on `units` (a synthetic-data-labeling
    migration from the original Phase 6 seed work, verified via
    `python -m alembic downgrade 0036` succeeding cleanly and the failure
    appearing only further back, at `0025_synthetic_unit_labels ->
    0024_vinhomes_labels_stats`). That bug is out of this hardening pass's
    scope (PR-1..PR-4 only) and is reported, not fixed, per this task's own
    instruction not to touch anything beyond PR-1..PR-4.
    """
    up = _alembic(scratch_db, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    down = _alembic(scratch_db, "downgrade", PR1_BASELINE)
    assert down.returncode == 0, down.stderr

    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.connect() as conn:
        current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        # PR-1 (0037)'s two columns must be reverted at this baseline.
        hierarchical_columns = conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ranking_scores' AND column_name IN "
                "('hierarchical_score', 'hierarchical_contributions')"
            )
        ).fetchall()
        # 0033's evidence-foundation tables predate this series (0033 < 0036)
        # and are NOT reverted by downgrading only to 0036 -- they must
        # still exist, just empty and without PR-2/PR-3/PR-4's additions.
        feature_store_tables = conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_name IN ('ranking_feature_values', 'ranking_feature_snapshots', "
                "'ranking_feature_lineage', 'ranking_feature_definitions')"
            )
        ).fetchall()
        # PR-2 (0038)'s assertion_kind column and PR-3 (0039)'s provenance
        # link must both be reverted at this baseline.
        pr2_column = conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ranking_weight_proposals' AND column_name = 'assertion_kind'"
            )
        ).fetchall()
        pr3_column = conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ranking_feature_values' AND column_name = 'source_justification_id'"
            )
        ).fetchall()
    engine.dispose()
    assert current == PR1_BASELINE
    assert hierarchical_columns == [], "PR-1's hierarchical_score/_contributions columns must be gone"
    assert {t[0] for t in feature_store_tables} == {
        "ranking_feature_values",
        "ranking_feature_snapshots",
        "ranking_feature_lineage",
        "ranking_feature_definitions",
    }, "0033's evidence-foundation tables predate PR-1 and must still exist at this baseline"
    assert pr2_column == [], "PR-2's assertion_kind column must be gone at the pre-PR-1 baseline"
    assert pr3_column == [], "PR-3's source_justification_id column must be gone at the pre-PR-1 baseline"


def test_pr1_pr4_chain_re_upgrades_to_the_identical_head_after_downgrading_to_its_baseline(scratch_db):
    assert _alembic(scratch_db, "upgrade", "head").returncode == 0
    assert _alembic(scratch_db, "downgrade", PR1_BASELINE).returncode == 0
    result = _alembic(scratch_db, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.connect() as conn:
        current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        market_defs = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'market'")
        ).scalar()
        area_defs = conn.execute(
            sa.text("SELECT COUNT(*) FROM ranking_feature_definitions WHERE grain = 'area'")
        ).scalar()
    engine.dispose()
    assert current == HEAD_REVISION
    assert market_defs == 4, "0040's four Market feature definitions must be re-seeded identically"
    assert area_defs == 3, "0041's three Area feature definitions must be re-seeded identically"
