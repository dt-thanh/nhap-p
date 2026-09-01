"""0043's `unit_enrichment_attributes` is contextual/reference data, never a
ranking input, by construction (see that migration's own docstring and
`src/ranking/enrichment_guard.py`). Two independent proofs:

1. Structural — the scoring code has no query path to the table at all.
2. Functional — seeding a row for a unit does not change that unit's score.

Plus direct coverage of `enrichment_guard.py`'s one real call site.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.tables import ranking_configs, ranking_feature_definitions, unit_enrichment_attributes
from src.ranking.enrichment_guard import EnrichmentGuardError, ensure_enrichment_keys_not_in_active_config
from src.ranking.service import run_ranking
from tests.conftest import db_skip_reason
from tests.ranking_fixture import PROJECT_ID, SEED_WEIGHTS, UNIT_IDS, _insert_config, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- 1. Structural: the scoring code never queries this table -----------------


def test_engine_never_references_unit_enrichment_attributes():
    text = (REPO_ROOT / "src" / "ranking" / "engine.py").read_text(encoding="utf-8")
    assert "unit_enrichment_attributes" not in text


def test_service_never_references_unit_enrichment_attributes():
    text = (REPO_ROOT / "src" / "ranking" / "service.py").read_text(encoding="utf-8")
    assert "unit_enrichment_attributes" not in text


# --- 2. Functional: presence of enrichment rows does not change scores --------


@pytest_asyncio.fixture
async def factory(truncate_all):
    engine = truncate_all
    f = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_config(f)
    await _insert_dataset(f)
    return f


async def _score_of(factory, unit_id: uuid.UUID) -> str:
    await run_ranking(PROJECT_ID, session_factory=factory)
    async with factory() as session:
        row = (
            await session.execute(
                sa.text("SELECT score FROM ranking_scores WHERE unit_id = :u"), {"u": unit_id}
            )
        ).first()
    assert row is not None
    return str(row[0])


@pytest.mark.asyncio
async def test_seeding_enrichment_row_does_not_change_the_units_score(factory):
    unit_id = UNIT_IDS["u1"]
    baseline = await _score_of(factory, unit_id)

    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(unit_enrichment_attributes).values(
                id=uuid.uuid4(),
                unit_id=unit_id,
                subdivision="Zenia",
                floor=12,
                loan_premium_pct="9.8",
                area_efficiency_ratio="0.91",
                floor_band="high",
                is_synthetic=True,
                data_profile="demo",
                source_system="test",
                source_file="test.csv",
                source_file_sha256="0" * 64,
                source_row_key="row-1",
                import_batch_id="batch-1",
                imported_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    after = await _score_of(factory, unit_id)
    assert after == baseline


# --- 3. enrichment_guard.py: the one real call site ----------------------------


@pytest.mark.asyncio
async def test_guard_passes_when_no_published_config(factory):
    async with factory() as session:
        await session.execute(sa.delete(ranking_configs))
        await session.commit()
        await ensure_enrichment_keys_not_in_active_config(session)  # must not raise


@pytest.mark.asyncio
async def test_guard_passes_when_no_collision(factory):
    async with factory() as session:
        await ensure_enrichment_keys_not_in_active_config(session)  # SEED_WEIGHTS keys don't collide


@pytest.mark.asyncio
async def test_guard_raises_on_unregistered_collision(factory):
    async with factory() as session:
        colliding_weights = {**SEED_WEIGHTS, "floor_band": {
            "weight": "0.1", "direction": "positive", "missing_value_policy": "skip", "min_confidence": "0"
        }}
        await session.execute(sa.update(ranking_configs).values(weights=colliding_weights))
        await session.commit()

        with pytest.raises(EnrichmentGuardError, match="floor_band"):
            await ensure_enrichment_keys_not_in_active_config(session)


@pytest.mark.asyncio
async def test_guard_allows_a_registered_deliberate_promotion(factory):
    async with factory() as session:
        colliding_weights = {**SEED_WEIGHTS, "floor_band": {
            "weight": "0.1", "direction": "positive", "missing_value_policy": "skip", "min_confidence": "0"
        }}
        await session.execute(sa.update(ranking_configs).values(weights=colliding_weights))
        now = datetime.now(UTC)
        await session.execute(
            sa.insert(ranking_feature_definitions).values(
                id=uuid.uuid4(),
                feature_key="floor_band",
                feature_version="v1",
                name="Floor band",
                category="physical",
                grain="unit",
                value_type="categorical",
                formula_id="promoted_enrichment",
                normalization_method="none",
                direction="positive",
                missing_policy="skip",
                status="active",
                definition_metadata={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

        await ensure_enrichment_keys_not_in_active_config(session)  # must not raise
