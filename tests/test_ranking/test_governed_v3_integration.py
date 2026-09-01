"""One real database-backed governed Ranking V3 lifecycle.

This deliberately uses the migrated PostgreSQL test database and the real
governance publication/materialization path.  The broader hierarchy suite
covers each gate independently; this test proves a two-area, multi-unit,
all-criteria happy path and preserves the earlier run's immutable rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.tables import areas, ranking_runs, ranking_scores, ranking_weight_proposals, units
from src.ranking.service import compute_hierarchical_scores_for_run, run_ranking
from tests.conftest import db_skip_reason
from tests.ranking_fixture import AREA_ID, PROJECT_ID, _insert_dataset
from tests.test_ranking.test_hierarchical_scoring import (
    _insert_config,
    _publish_area_value_assertion,
    _publish_market_value_assertion,
    _publish_project_value_assertion,
)

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

SECOND_AREA_ID = uuid.uuid4()
SECOND_UNIT_IDS = {"u6": uuid.uuid4(), "u7": uuid.uuid4()}


@pytest_asyncio.fixture
async def factory(truncate_all, monkeypatch):
    session_factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    monkeypatch.setattr("src.ranking.service.get_session_factory", lambda: session_factory, raising=False)
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type(
            "_S", (), {"hierarchical_ranking_enabled": False, "ranking_v3_composite_enabled": False}
        )(),
    )
    return session_factory


async def _insert_second_area(factory):
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(areas).values(
                id=SECOND_AREA_ID,
                project_id=PROJECT_ID,
                area_name="Tower B",
                unit_type="2PN",
                bedrooms=2,
                area_sqm=Decimal("60"),
                total_units=4,
                created_at=now,
                external_id="A-AGENT-TEST-2",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        for key, unit_id in SECOND_UNIT_IDS.items():
            await session.execute(
                sa.insert(units).values(
                    id=unit_id,
                    source_system="mini_crm",
                    source_instance_id="test",
                    external_unit_id=key,
                    area_id=SECOND_AREA_ID,
                    unit_code=key,
                    unit_type="2PN",
                    status="available",
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()


async def _rows_for_run(factory, run_id):
    async with factory() as session:
        rows = (
            await session.execute(
                sa.select(ranking_scores).where(ranking_scores.c.ranking_run_id == run_id)
            )
        ).mappings().all()
        await session.rollback()
    return {row["unit_id"]: dict(row) for row in rows}


async def _run_metadata(factory, run_id):
    async with factory() as session:
        row = (
            await session.execute(sa.select(ranking_runs).where(ranking_runs.c.id == run_id))
        ).mappings().one()
        await session.rollback()
    return dict(row)


async def test_two_area_full_governed_v3_lifecycle_is_database_backed(factory, monkeypatch):
    weights = {
        "market": {
            "market_interest_rate": {"weight": 0.2, "direction": "negative", "missing_value_policy": "skip"},
            "market_demand": {"weight": 0.3, "direction": "positive", "missing_value_policy": "skip"},
            "market_credit_policy": {"weight": 0.5, "direction": "positive", "missing_value_policy": "skip"},
        },
        "project": {
            "project_design_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"},
        },
        "area": {
            "area_accessibility": {"weight": 0.2, "direction": "positive", "missing_value_policy": "skip"},
            "area_current_infrastructure": {"weight": 0.3, "direction": "positive", "missing_value_policy": "skip"},
            "area_future_infrastructure": {"weight": 0.5, "direction": "positive", "missing_value_policy": "skip"},
        },
        "grain_weights": {
            "market": {"weight": 0.10, "missing_value_policy": "skip"},
            "project": {"weight": 0.25, "missing_value_policy": "skip"},
            "area": {"weight": 0.25, "missing_value_policy": "skip"},
            "unit": {"weight": 0.40, "missing_value_policy": "skip"},
        },
    }
    await _insert_config(factory, hierarchical_weights=weights)
    await _insert_dataset(factory)
    await _insert_second_area(factory)

    baseline = await run_ranking(PROJECT_ID, session_factory=factory)
    baseline_rows = await _rows_for_run(factory, baseline.run_id)
    baseline_run = await _run_metadata(factory, baseline.run_id)
    assert len(baseline_rows) == 7
    assert all(row["hierarchical_score"] is None for row in baseline_rows.values())

    await _publish_project_value_assertion(factory, normalized_value="0.75")
    for key, value in (("market_interest_rate", "0.50"), ("market_demand", "0.75"), ("market_credit_policy", "1.00")):
        await _publish_market_value_assertion(factory, feature_key=key, normalized_value=value)
    for area_id in (AREA_ID, SECOND_AREA_ID):
        for key, value in (
            ("area_accessibility", "0.75"),
            ("area_current_infrastructure", "0.50"),
            ("area_future_infrastructure", "1.00"),
        ):
            await _publish_area_value_assertion(factory, area_id=area_id, feature_key=key, normalized_value=value)

    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type(
            "_S", (), {"hierarchical_ranking_enabled": True, "ranking_v3_composite_enabled": False}
        )(),
    )
    applied = await run_ranking(PROJECT_ID, session_factory=factory)
    result = await compute_hierarchical_scores_for_run(
        PROJECT_ID, applied.run_id, applied.config_version_id, session_factory=factory
    )
    assert result.written == 7
    rows = await _rows_for_run(factory, applied.run_id)
    assert len(rows) == 7
    for row in rows.values():
        assert row["hierarchical_score"] is not None
        disclosure = row["hierarchical_contributions"]
        assert disclosure["score_mode"] == "full_hierarchical"
        assert set(disclosure["eligible_grains"]) == {"market", "project", "area"}
        assert disclosure["excluded_grains"] == {}
        assert len(disclosure["grains"]["market"]["feature_value_ids"]) == 3
        assert len(disclosure["grains"]["project"]["feature_value_ids"]) == 1
        assert len(disclosure["grains"]["area"]["feature_value_ids"]) == 3

    # `ranking_runs` is the append-only immutable history; `ranking_scores` is
    # the documented current-project materialization and may be replaced by a
    # later run.  Prove the historical run itself was not rewritten.
    baseline_after = await _run_metadata(factory, baseline.run_id)
    assert baseline_after["status"] == baseline_run["status"] == "completed"
    assert baseline_after["finished_at"] == baseline_run["finished_at"]
    assert baseline_after["config_version_id"] == baseline_run["config_version_id"]


@pytest.mark.parametrize("composite_enabled", [False, True])
async def test_one_area_all_expert_values_are_scoped_per_area_and_flagged_comparably(
    factory, monkeypatch, composite_enabled
):
    """One fully governed Area contributes only to its own units.

    This is intentionally separate from the all-Areas happy path above: Area A
    receives all three expert values while Area B receives none, proving the
    resolver is per-area rather than run-wide.  The same fixture is exercised
    with Ranking V3 composite ranks disabled and enabled.
    """
    weights = {
        "market": {
            "market_interest_rate": {"weight": 1.0, "direction": "negative", "missing_value_policy": "skip"},
        },
        "project": {
            "project_design_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"},
        },
        "area": {
            "area_accessibility": {"weight": 0.2, "direction": "positive", "missing_value_policy": "skip"},
            "area_current_infrastructure": {"weight": 0.3, "direction": "positive", "missing_value_policy": "skip"},
            "area_future_infrastructure": {"weight": 0.5, "direction": "positive", "missing_value_policy": "skip"},
        },
        "grain_weights": {
            "market": {"weight": 0.10, "missing_value_policy": "skip"},
            "project": {"weight": 0.25, "missing_value_policy": "skip"},
            "area": {"weight": 0.25, "missing_value_policy": "skip"},
            "unit": {"weight": 0.40, "missing_value_policy": "skip"},
        },
    }
    await _insert_config(factory, hierarchical_weights=weights)
    await _insert_dataset(factory)
    await _insert_second_area(factory)

    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type(
            "_S", (), {"hierarchical_ranking_enabled": True, "ranking_v3_composite_enabled": composite_enabled}
        )(),
    )
    baseline = await run_ranking(PROJECT_ID, session_factory=factory)
    baseline_meta = await _run_metadata(factory, baseline.run_id)

    for feature_key, value in (
        ("area_accessibility", "0.75"),
        ("area_current_infrastructure", "0.50"),
        ("area_future_infrastructure", "1.00"),
    ):
        await _publish_area_value_assertion(
            factory, area_id=AREA_ID, feature_key=feature_key, normalized_value=value
        )

    async with factory() as session:
        published = (
            await session.execute(
                sa.select(ranking_weight_proposals.c.id, ranking_weight_proposals.c.area_id)
                .where(
                    ranking_weight_proposals.c.project_id == PROJECT_ID,
                    ranking_weight_proposals.c.area_id == AREA_ID,
                    ranking_weight_proposals.c.scope_type == "area",
                    ranking_weight_proposals.c.assertion_kind == "value",
                    ranking_weight_proposals.c.status == "published",
                )
            )
        ).all()
        await session.rollback()
    assert len(published) == 3
    assert all(row.area_id == AREA_ID for row in published)

    applied = await run_ranking(PROJECT_ID, session_factory=factory)
    await compute_hierarchical_scores_for_run(
        PROJECT_ID, applied.run_id, applied.config_version_id, session_factory=factory
    )
    rows = await _rows_for_run(factory, applied.run_id)
    area_a_rows = [row for row in rows.values() if row["area_id"] == AREA_ID]
    area_b_rows = [row for row in rows.values() if row["area_id"] == SECOND_AREA_ID]
    assert len(area_a_rows) >= 2
    assert len(area_b_rows) >= 2

    for row in area_a_rows:
        disclosure = row["hierarchical_contributions"]
        assert disclosure["score_mode"] == "partial_hierarchical"
        assert disclosure["grains"]["area"]["eligible"] is True
        assert disclosure["grains"]["area"]["exclusion_reason"] is None
        assert set(disclosure["grains"]["area"]["expert_feature_keys"]) == {
            "area_accessibility",
            "area_current_infrastructure",
            "area_future_infrastructure",
        }
        assert len(disclosure["grains"]["area"]["feature_value_ids"]) == 3
        assert Decimal(disclosure["grains"]["area"]["score"]) > 0
        assert "area" in disclosure["effective_grain_weights"]
        assert row["hierarchical_score"] is not None

    area_a_value_ids = {
        value_id
        for row in area_a_rows
        for value_id in row["hierarchical_contributions"]["grains"]["area"]["feature_value_ids"]
    }
    for row in area_b_rows:
        disclosure = row["hierarchical_contributions"]
        assert disclosure["grains"]["area"]["eligible"] is False
        assert disclosure["grains"]["area"]["exclusion_reason"] == "NO_PUBLISHED_AREA_EXPERT_VALUE"
        assert "area" not in disclosure["effective_grain_weights"]
        assert not (area_a_value_ids & set(disclosure["grains"]["area"].get("feature_value_ids", [])))
        assert row["hierarchical_score"] is not None

    assert all(row["hierarchical_contributions"]["comparability_warning"] for row in rows.values())
    after_baseline = await _run_metadata(factory, baseline.run_id)
    assert after_baseline["status"] == baseline_meta["status"] == "completed"
    assert after_baseline["finished_at"] == baseline_meta["finished_at"]
    assert after_baseline["config_version_id"] == baseline_meta["config_version_id"]
