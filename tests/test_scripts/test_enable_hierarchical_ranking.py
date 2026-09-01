"""`scripts/enable_hierarchical_ranking.py` — the one-time admin action that
attaches `hierarchical_weights` to a fresh, migration-seeded `ranking_configs`
row (which only ever has `hierarchical_weights IS NULL` — no migration or
sync path ever writes it).

This regression exists because of a real incident, not speculatively: a
`docker compose` volume wipe (external to any code in this repo) destroyed a
previously-published `hierarchical_weights` config and its recomputed scores,
silently reverting the live environment back to the exact "config v2, 0
scored apartments, NO_PERSISTED_HIERARCHICAL_SCORES" state this script exists
to fix. The script is data, not schema — it must be safe and correct to run
again from that exact reverted state, and it must never depend on a specific
version number (only on "whichever config is currently published"), since a
fresh migration-seeded database always starts that published version back at
v2.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from scripts.enable_hierarchical_ranking import HIERARCHICAL_WEIGHTS, _confirm, _dry_run
from src.models.tables import ranking_configs
from src.services.ranking_config import create_draft, list_configs, publish
from tests.conftest import db_skip_reason

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

BASELINE_WEIGHTS = {
    "unit_available": {"weight": 0.35, "direction": "positive", "missing_value_policy": "zero", "min_confidence": 0},
    "unit_demand_norm": {"weight": 0.25, "direction": "positive", "missing_value_policy": "zero", "min_confidence": 0},
    "area_velocity_norm": {
        "weight": 0.20, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0,
    },
    "area_conversion_norm": {
        "weight": 0.20, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0,
    },
}


@pytest_asyncio.fixture
async def db(truncate_all, monkeypatch):
    """A fresh-DB-like baseline: exactly one published config, no
    `hierarchical_weights` — reproducing the state a genuinely fresh volume
    (or a wiped one) always starts from, not a specific version number."""
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    monkeypatch.setattr("src.services.ranking_config.get_session_factory", lambda f=factory: f, raising=False)
    draft = await create_draft(weights=BASELINE_WEIGHTS, min_weight_coverage=0.5, note="baseline", created_by="seed")
    await publish(version=draft["version"], published_by="seed")
    return factory


@pytest_asyncio.fixture
def no_recompute(monkeypatch):
    """Recompute itself (RQ/Redis enqueue) is exercised by other tests
    (`trigger_ranking_all_projects`'s own suite) — this script's job is only
    to call it, not to re-prove its internals, and Redis is not guaranteed to
    be reachable in every test environment."""
    calls = []

    async def _stub(*, trigger):
        calls.append(trigger)
        return {"projects": 0, "enqueued": 0, "coalesced": 0, "failed": 0}

    monkeypatch.setattr("scripts.enable_hierarchical_ranking.trigger_ranking_all_projects", _stub)
    return calls


async def test_dry_run_writes_nothing(db):
    before = await list_configs()
    await _dry_run()
    after = await list_configs()
    assert before == after


async def test_confirm_publishes_a_new_version_with_hierarchical_weights_and_triggers_recompute(db, no_recompute):
    before = next(c for c in await list_configs() if c["status"] == "published")

    await _confirm()

    configs = await list_configs()
    published = [c for c in configs if c["status"] == "published"]
    assert len(published) == 1, "publishing must never leave more than one active config"
    new = published[0]
    assert new["version"] == before["version"] + 1
    assert new["hierarchical_weights"] == HIERARCHICAL_WEIGHTS
    assert new["weights"] == before["weights"], "legacy weights must be copied verbatim, never altered"
    assert new["min_weight_coverage"] == before["min_weight_coverage"]

    archived = next(c for c in configs if c["version"] == before["version"])
    assert archived["status"] == "archived"

    assert no_recompute == ["config_change"], "must trigger exactly one all-projects recompute"


async def test_confirm_works_from_a_bare_migration_seeded_state_not_tied_to_any_specific_version_number(
    db, no_recompute
):
    """The exact scenario this test guards: a fresh/reverted database always
    republishes its baseline config as a NEW version number (whatever `db`
    happened to seed it as here), never literally "v2" — the script must key
    off "currently published", never a hardcoded version."""
    async with db() as session:
        version = await session.scalar(
            sa.select(ranking_configs.c.version).where(ranking_configs.c.status == "published")
        )
    assert version != 2, "test setup sanity check: this run's baseline is NOT v2, proving no hardcoded assumption"

    await _confirm()

    published = next(c for c in await list_configs() if c["status"] == "published")
    assert published["hierarchical_weights"] == HIERARCHICAL_WEIGHTS
    assert published["version"] == version + 1


def test_refuses_outside_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    from scripts.enable_hierarchical_ranking import main

    with pytest.raises(SystemExit):
        main(["--confirm"])
