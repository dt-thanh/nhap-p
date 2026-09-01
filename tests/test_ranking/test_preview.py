"""`src/ranking/preview.py` — read-only "Bản xem trước — chưa được công bố".

Reuses the exact 5-unit fixture (`PROJECT_ID`/`UNIT_IDS`/`SEED_WEIGHTS`,
legacy scores u1=0.59/u2=0.84/u3=0.59/u4=0.24) already proven correct in
`tests/test_agent_e2e.py`, rather than building a second dataset for the
same numbers.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.tables import ranking_configs, ranking_scores
from src.ranking.preview import preview_flat_weights
from src.ranking.service import RankingError, run_ranking
from src.services.ranking_config import ConfigError
from tests.conftest import db_skip_reason
from tests.ranking_fixture import PROJECT_ID, SEED_WEIGHTS, UNIT_IDS, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]


@pytest_asyncio.fixture
async def factory(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    for target in ("src.ranking.service.get_session_factory", "src.ranking.preview.get_session_factory"):
        monkeypatch.setattr(target, lambda f=factory: f, raising=False)
    await _insert_dataset(factory)
    async with factory() as session:
        await session.execute(
            sa.insert(ranking_configs).values(
                id=uuid.uuid4(),
                version=1,
                status="published",
                weights=SEED_WEIGHTS,
                min_weight_coverage=Decimal("0.5"),
                note="seed",
                created_by="test",
                created_at=sa.func.now(),
                published_at=sa.func.now(),
            )
        )
        await session.commit()
    await run_ranking(PROJECT_ID, session_factory=factory)
    return factory


async def test_preview_writes_nothing(factory):
    async with factory() as session:
        runs_before = await session.scalar(sa.text("SELECT count(*) FROM ranking_runs"))
        scores_before = await session.scalar(sa.select(sa.func.count()).select_from(ranking_scores))

    candidate = {
        "unit_available": {"weight": 0.5, "direction": "positive", "missing_value_policy": "zero"},
        "unit_demand_norm": {"weight": 0.5, "direction": "positive", "missing_value_policy": "zero"},
    }
    await preview_flat_weights(PROJECT_ID, weights=candidate, min_weight_coverage=Decimal("0.5"), session_factory=factory)

    async with factory() as session:
        runs_after = await session.scalar(sa.text("SELECT count(*) FROM ranking_runs"))
        scores_after = await session.scalar(sa.select(sa.func.count()).select_from(ranking_scores))
    assert runs_after == runs_before
    assert scores_after == scores_before


async def test_preview_computes_real_scores_and_deltas_against_the_current_published_config(factory):
    candidate = {
        "unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "zero"},
    }
    result = await preview_flat_weights(PROJECT_ID, weights=candidate, min_weight_coverage=Decimal("0.5"), session_factory=factory)

    assert result.current_config_version == 1
    assert result.sample_size == 5
    by_unit = {r.unit_id: r for r in result.results}
    u1 = by_unit[str(UNIT_IDS["u1"])]
    assert u1.current_score == "0.5900"  # legacy fixture's known-correct score
    # candidate weights are pure unit_available -> preview must differ from current
    assert u1.preview_score != u1.current_score
    assert u1.score_delta is not None
    assert Decimal(u1.preview_score) - Decimal(u1.current_score) == Decimal(u1.score_delta)


async def test_preview_rejects_an_invalid_candidate_weights_shape(factory):
    with pytest.raises(ConfigError) as exc:
        await preview_flat_weights(
            PROJECT_ID,
            weights={"unit_available": {"weight": 1.5, "direction": "positive", "missing_value_policy": "zero"}},
            min_weight_coverage=Decimal("0.5"),
            session_factory=factory,
        )
    assert exc.value.code == "WEIGHT_SUM"


async def test_preview_unknown_project_raises(factory):
    import uuid

    with pytest.raises(RankingError) as exc:
        await preview_flat_weights(
            uuid.uuid4(),
            weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "zero"}},
            min_weight_coverage=Decimal("0.5"),
            session_factory=factory,
        )
    assert exc.value.code == "PROJECT_NOT_FOUND"


async def test_top_gainers_and_losers_are_sorted_by_score_delta(factory):
    candidate = {
        "unit_demand_norm": {"weight": 1.0, "direction": "positive", "missing_value_policy": "zero"},
    }
    result = await preview_flat_weights(PROJECT_ID, weights=candidate, min_weight_coverage=Decimal("0.5"), session_factory=factory)
    deltas = [Decimal(r.score_delta) for r in result.top_gainers]
    assert deltas == sorted(deltas, reverse=True)
    loser_deltas = [Decimal(r.score_delta) for r in result.top_losers]
    assert loser_deltas == sorted(loser_deltas)
