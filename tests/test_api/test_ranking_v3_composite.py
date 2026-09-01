"""Ranking v3 (`ranking_v3_composite_enabled`) — makes the already-computed
`hierarchical_score` drive `rank_in_project`/`rank_in_area` instead of staying
a parallel, display-only column. See `pipeline_status.md` for the full design.

Self-contained by design, same reason as
`tests/test_api/test_ranking_report_hierarchy_disclosure.py`: the shared
`tests/test_agent_e2e.py` fixture ten other files import from has been
deleted (a real, pre-existing, unrelated regression) — this file builds its
own minimal project/area/unit/config/run/score rows directly instead of
depending on it.

Three layers are tested independently, on purpose:
1. `_apply_v3_composite_ranks()` (service layer) — does it correctly persist
   v3-composite ranks, and correctly refuse to when ineligible?
2. `GET /ranking`'s `_ranking_formula()`/`_effective_score()` (read layer) —
   given whatever is already persisted, does the API honestly report which
   formula produced it, without depending on the CURRENT (possibly stale)
   flag value?
3. `preview_flat_weights()` (a pre-existing, unrelated legacy what-if tool)
   — does its own "current" baseline stay pure-legacy even when the
   persisted `rank_in_project` is v3-derived?
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.main import app
from src.models.tables import (
    areas,
    expert_profiles,
    projects,
    ranking_configs,
    ranking_runs,
    ranking_scores,
    ranking_weight_proposals,
    units,
)
from src.ranking.engine import UnitScore
from src.ranking.service import HierarchicalRunResult, _apply_v3_composite_ranks
from tests.conftest import DASHBOARD_ADMIN_TOKEN, db_skip_reason

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

PROJECT_ID = uuid.uuid4()
AREA_ID = uuid.uuid4()
UNIT_IDS = [uuid.uuid4() for _ in range(3)]  # u1 legacy-best, u2 hierarchical-best, u3 legal-gated/no-hier
CONFIG_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
EXTERNAL_PROJECT_ID = "P-V3-COMPOSITE-TEST"
FLAT_WEIGHTS = {"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}}
HIERARCHICAL_WEIGHTS = {
    "market": {"market_interest_rate": {"weight": 1.0, "direction": "negative", "missing_value_policy": "neutral"}},
    "project": {"project_design_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}},
    "area": {"area_accessibility": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}},
    "grain_weights": {
        "market": {"weight": 0.25, "missing_value_policy": "skip"},
        "project": {"weight": 0.25, "missing_value_policy": "skip"},
        "area": {"weight": 0.25, "missing_value_policy": "skip"},
        "unit": {"weight": 0.25, "missing_value_policy": "skip"},
    },
}


@pytest_asyncio.fixture
async def factory(truncate_all):
    return async_sessionmaker(truncate_all, expire_on_commit=False)


async def _seed_base(factory, *, hierarchical_weights: dict | None) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(projects).values(
                id=PROJECT_ID, name="Ranking v3 Test", launch_date=date(2026, 1, 1),
                created_at=now, updated_at=now, absorption_calculator="legacy_aggregate",
                external_id=EXTERNAL_PROJECT_ID, source_system="mini_crm", source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(areas).values(
                id=AREA_ID, project_id=PROJECT_ID, area_name="Test Area", unit_type="apartment",
                bedrooms=2, area_sqm=Decimal("70"), total_units=3, created_at=now, updated_at=now,
            )
        )
        for index, unit_id in enumerate(UNIT_IDS):
            await session.execute(
                sa.insert(units).values(
                    id=unit_id, source_system="mini_crm", source_instance_id="test",
                    external_unit_id=f"U-{index}", area_id=AREA_ID, unit_code=f"U-{index}",
                    unit_type="2PN", status="available", created_at=now, updated_at=now,
                )
            )
        await session.execute(
            sa.insert(ranking_configs).values(
                id=CONFIG_ID, version=2, status="published", weights=FLAT_WEIGHTS,
                hierarchical_weights=hierarchical_weights, min_weight_coverage=Decimal("0.5"),
                note="test", created_by="test", created_at=now, published_by="test", published_at=now,
            )
        )
        await session.execute(
            sa.insert(ranking_runs).values(
                id=RUN_ID, project_id=PROJECT_ID, trigger="config_change", scope_type="project",
                status="completed", config_version_id=CONFIG_ID, units_processed=3, units_ranked=3,
                units_skipped=0, enqueued_at=now, started_at=now, finished_at=now,
            )
        )
        await session.commit()


async def _seed_legacy_scores(factory, *, hierarchical_by_unit: dict[str, Decimal | None] | None) -> None:
    """Legacy-only ranking: u1 (0.90) > u2 (0.50) > u3 (0.10) — plain
    `score`-driven `rank_in_project`, exactly what `_persist_scores()` alone
    would have produced. `hierarchical_by_unit` optionally seeds
    `hierarchical_score` (simulating `compute_hierarchical_scores_for_run`
    having already run) without touching rank_in_project/area — the real
    invariant `_apply_v3_composite_ranks` is meant to act on.
    """
    now = datetime.now(UTC)
    legacy_scores = {UNIT_IDS[0]: Decimal("0.9000"), UNIT_IDS[1]: Decimal("0.5000"), UNIT_IDS[2]: Decimal("0.1000")}
    async with factory() as session:
        for rank, unit_id in enumerate(UNIT_IDS, start=1):
            hier = (hierarchical_by_unit or {}).get(str(unit_id))
            await session.execute(
                sa.insert(ranking_scores).values(
                    id=uuid.uuid4(), unit_id=unit_id, area_id=AREA_ID, project_id=PROJECT_ID,
                    ranking_run_id=RUN_ID, config_version_id=CONFIG_ID, score=legacy_scores[unit_id],
                    rank_in_area=rank, rank_in_project=rank, weight_coverage=Decimal("1.0"),
                    contributions={"unit_available": {"source": "resolved", "value": str(legacy_scores[unit_id]), "weight": "1.0"}},
                    hierarchical_score=hier, hierarchical_contributions={"schema_version": 1} if hier is not None else None,
                    computed_at=now,
                )
            )
        await session.commit()


def _ranked_unit_scores() -> list[UnitScore]:
    """The legacy `ranked` list `run_ranking()` would have built for the same
    3-unit fixture `_seed_legacy_scores` persists — matching ranks 1/2/3."""
    return [
        UnitScore(unit_id=str(UNIT_IDS[0]), area_id=str(AREA_ID), score=Decimal("0.9000"), coverage=Decimal("1"),
                   contributions={}, skipped=False, skip_reason=None, tie_break_created_at=0,
                   rank_in_project=1, rank_in_area=1),
        UnitScore(unit_id=str(UNIT_IDS[1]), area_id=str(AREA_ID), score=Decimal("0.5000"), coverage=Decimal("1"),
                   contributions={}, skipped=False, skip_reason=None, tie_break_created_at=0,
                   rank_in_project=2, rank_in_area=2),
        UnitScore(unit_id=str(UNIT_IDS[2]), area_id=str(AREA_ID), score=Decimal("0.1000"), coverage=Decimal("1"),
                   contributions={}, skipped=False, skip_reason=None, tie_break_created_at=0,
                   rank_in_project=3, rank_in_area=3),
    ]


# --- 1. _apply_v3_composite_ranks() (service layer) -------------------------


async def test_apply_v3_composite_ranks_reorders_when_eligible(factory, monkeypatch):
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("_S", (), {"ranking_v3_composite_enabled": True})(),
    )
    await _seed_base(factory, hierarchical_weights=HIERARCHICAL_WEIGHTS)
    # u3 (legacy-worst, 0.10) is the hierarchical-best (0.95); u1 falls to last.
    hierarchical_by_unit = {str(UNIT_IDS[0]): Decimal("0.20"), str(UNIT_IDS[1]): Decimal("0.50"), str(UNIT_IDS[2]): Decimal("0.95")}
    await _seed_legacy_scores(factory, hierarchical_by_unit=hierarchical_by_unit)

    reranked = await _apply_v3_composite_ranks(
        factory, project_id=PROJECT_ID, run_id=RUN_ID, ranked=_ranked_unit_scores(),
        hier_result=HierarchicalRunResult(RUN_ID, PROJECT_ID, CONFIG_ID, hierarchical_weights_present=True, written=3),
    )

    assert reranked is not None
    by_id = {s.unit_id: s for s in reranked}
    assert by_id[str(UNIT_IDS[2])].rank_in_project == 1
    assert by_id[str(UNIT_IDS[1])].rank_in_project == 2
    assert by_id[str(UNIT_IDS[0])].rank_in_project == 3

    async with factory() as session:
        rows = (
            await session.execute(
                sa.select(ranking_scores.c.unit_id, ranking_scores.c.rank_in_project, ranking_scores.c.score)
                .where(ranking_scores.c.ranking_run_id == RUN_ID)
            )
        ).mappings().all()
        await session.rollback()
    persisted = {str(row["unit_id"]): row for row in rows}
    assert persisted[str(UNIT_IDS[2])]["rank_in_project"] == 1
    # `score` (legacy) must be byte-identical — only ranks changed.
    assert persisted[str(UNIT_IDS[0])]["score"] == Decimal("0.9000")
    assert persisted[str(UNIT_IDS[2])]["score"] == Decimal("0.1000")


async def test_apply_v3_composite_ranks_noop_when_flag_off(factory, monkeypatch):
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("_S", (), {"ranking_v3_composite_enabled": False})(),
    )
    await _seed_base(factory, hierarchical_weights=HIERARCHICAL_WEIGHTS)
    hierarchical_by_unit = {str(u): Decimal("0.50") for u in UNIT_IDS}
    await _seed_legacy_scores(factory, hierarchical_by_unit=hierarchical_by_unit)

    result = await _apply_v3_composite_ranks(
        factory, project_id=PROJECT_ID, run_id=RUN_ID, ranked=_ranked_unit_scores(),
        hier_result=HierarchicalRunResult(RUN_ID, PROJECT_ID, CONFIG_ID, hierarchical_weights_present=True, written=3),
    )

    assert result is None


async def test_apply_v3_composite_ranks_noop_when_config_has_no_hierarchical_weights(factory, monkeypatch):
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("_S", (), {"ranking_v3_composite_enabled": True})(),
    )
    await _seed_base(factory, hierarchical_weights=None)
    await _seed_legacy_scores(factory, hierarchical_by_unit=None)

    result = await _apply_v3_composite_ranks(
        factory, project_id=PROJECT_ID, run_id=RUN_ID, ranked=_ranked_unit_scores(),
        hier_result=HierarchicalRunResult(RUN_ID, PROJECT_ID, CONFIG_ID, hierarchical_weights_present=False),
    )

    assert result is None


async def test_apply_v3_composite_ranks_noop_when_legal_gate_nulled_every_unit(factory, monkeypatch):
    """`written` alone is NOT a safe eligibility signal — a project-wide legal
    gate still increments `written` while persisting `hierarchical_score=NULL`
    for every unit. Must stay legacy-only (this is the documented invariant
    `test_high_risk_never_changes_legacy_score_ranks_or_legacy_contributions`
    already covers end-to-end when that suite is collectible)."""
    monkeypatch.setattr(
        "src.ranking.service.get_settings",
        lambda: type("_S", (), {"ranking_v3_composite_enabled": True})(),
    )
    await _seed_base(factory, hierarchical_weights=HIERARCHICAL_WEIGHTS)
    await _seed_legacy_scores(factory, hierarchical_by_unit=None)  # every hierarchical_score stays NULL

    result = await _apply_v3_composite_ranks(
        factory, project_id=PROJECT_ID, run_id=RUN_ID, ranked=_ranked_unit_scores(),
        hier_result=HierarchicalRunResult(RUN_ID, PROJECT_ID, CONFIG_ID, hierarchical_weights_present=True, written=3, attempted=3),
    )

    assert result is None
    async with factory() as session:
        ranks = (
            await session.execute(
                sa.select(ranking_scores.c.unit_id, ranking_scores.c.rank_in_project).where(
                    ranking_scores.c.ranking_run_id == RUN_ID
                )
            )
        ).mappings().all()
        await session.rollback()
    persisted = {str(row["unit_id"]): row["rank_in_project"] for row in ranks}
    assert persisted[str(UNIT_IDS[0])] == 1 and persisted[str(UNIT_IDS[2])] == 3  # untouched legacy order


# --- 2. GET /ranking's ranking_formula / effective_score (read layer) -------


@pytest_asyncio.fixture
async def http(factory, monkeypatch):
    for target in ("src.api.ranking.get_session_factory",):
        monkeypatch.setattr(target, lambda factory=factory: factory, raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.factory = factory  # type: ignore[attr-defined]
        yield client


ADMIN_HEADER = {"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}
RANKING_API = f"/api/v1/ranking?external_project_id={EXTERNAL_PROJECT_ID}"


async def test_get_ranking_reports_v2_legacy_when_persisted_ranks_match_legacy_order(http):
    await _seed_base(http.factory, hierarchical_weights=None)
    await _seed_legacy_scores(http.factory, hierarchical_by_unit=None)

    body = (await http.get(RANKING_API, headers=ADMIN_HEADER)).json()

    assert body["ranking_formula"] == "v2_legacy"
    assert body["ahp_pending_status"] is None
    for item in body["items"]:
        assert item["effective_score"] == item["score"]


async def test_get_ranking_reports_v3_hierarchical_when_persisted_ranks_diverge_from_legacy_order(http):
    await _seed_base(http.factory, hierarchical_weights=HIERARCHICAL_WEIGHTS)
    # Persist ranks as if `_apply_v3_composite_ranks` already ran: u3 (legacy
    # worst) is now rank 1, matching a real hierarchical composite — this
    # simulates the post-re-rank DB state directly, decoupled from the
    # service-layer test above.
    now = datetime.now(UTC)
    async with http.factory() as session:
        v3_ranks = {UNIT_IDS[0]: 3, UNIT_IDS[1]: 2, UNIT_IDS[2]: 1}
        legacy_scores = {UNIT_IDS[0]: Decimal("0.9000"), UNIT_IDS[1]: Decimal("0.5000"), UNIT_IDS[2]: Decimal("0.1000")}
        hier_scores = {UNIT_IDS[0]: Decimal("0.20"), UNIT_IDS[1]: Decimal("0.50"), UNIT_IDS[2]: Decimal("0.95")}
        for unit_id in UNIT_IDS:
            await session.execute(
                sa.insert(ranking_scores).values(
                    id=uuid.uuid4(), unit_id=unit_id, area_id=AREA_ID, project_id=PROJECT_ID,
                    ranking_run_id=RUN_ID, config_version_id=CONFIG_ID, score=legacy_scores[unit_id],
                    rank_in_area=v3_ranks[unit_id], rank_in_project=v3_ranks[unit_id], weight_coverage=Decimal("1.0"),
                    contributions={"unit_available": {"source": "resolved", "value": str(legacy_scores[unit_id]), "weight": "1.0"}},
                    hierarchical_score=hier_scores[unit_id], hierarchical_contributions={"schema_version": 1},
                    computed_at=now,
                )
            )
        await session.commit()

    body = (await http.get(RANKING_API, headers=ADMIN_HEADER)).json()

    assert body["ranking_formula"] == "v3_hierarchical"
    by_unit = {item["unit_id"]: item for item in body["items"]}
    # `score`/`band` stay pure legacy regardless (u1's legacy score is still the highest)...
    assert Decimal(by_unit[str(UNIT_IDS[0])]["score"]) == Decimal("0.9000")
    # ...but `effective_score` and the actual `rank_in_project` reflect the hierarchical composite.
    assert by_unit[str(UNIT_IDS[2])]["effective_score"] == "0.9500"
    assert by_unit[str(UNIT_IDS[2])]["rank_in_project"] == 1
    assert by_unit[str(UNIT_IDS[0])]["rank_in_project"] == 3


async def test_get_ranking_reports_ahp_pending_status_when_proposal_not_yet_applied(http):
    await _seed_base(http.factory, hierarchical_weights=None)
    await _seed_legacy_scores(http.factory, hierarchical_by_unit=None)
    now = datetime.now(UTC)
    expert_id = uuid.uuid4()
    async with http.factory() as session:
        await session.execute(
            sa.insert(expert_profiles).values(
                id=expert_id, identity_subject="ahp-pending-test", status="active", created_at=now, updated_at=now,
            )
        )
        await session.execute(
            # `ck_rwp_assertion_kind_config_shape`: assertion_kind='weight' requires
            # a real base_config_id — reuse the published config this fixture
            # already seeds (`_seed_base`), matching how a real AHP proposal
            # always resolves it server-side from the currently published config.
            sa.insert(ranking_weight_proposals).values(
                id=uuid.uuid4(), base_config_id=CONFIG_ID, proposed_config_id=None, scope_type="project",
                project_id=PROJECT_ID, area_id=None, status="approved", created_by_expert_id=expert_id,
                created_at=now, updated_at=now, assertion_kind="weight", proposal_type="ahp_ranking_proposal",
                proposed_hierarchy_snapshot=None, ahp_application_status="awaiting_prior_run",
                applied_ranking_run_id=None,
            )
        )
        await session.commit()

    body = (await http.get(RANKING_API, headers=ADMIN_HEADER)).json()

    assert body["ahp_pending_status"] == "awaiting_prior_run"
    # Must never invent a new `state`/`reason` value — the existing enum is untouched.
    assert body["state"] == "ready"


# --- 3. preview.py's legacy-only baseline stays correct ----------------------


async def test_preview_baseline_stays_pure_legacy_even_when_persisted_rank_is_v3_derived(factory):
    from src.ranking.preview import preview_flat_weights

    await _seed_base(factory, hierarchical_weights=HIERARCHICAL_WEIGHTS)
    # Persisted rank_in_project is v3-derived (u3 is rank 1 despite the lowest legacy score).
    now = datetime.now(UTC)
    async with factory() as session:
        v3_ranks = {UNIT_IDS[0]: 3, UNIT_IDS[1]: 2, UNIT_IDS[2]: 1}
        legacy_scores = {UNIT_IDS[0]: Decimal("0.9000"), UNIT_IDS[1]: Decimal("0.5000"), UNIT_IDS[2]: Decimal("0.1000")}
        for unit_id in UNIT_IDS:
            await session.execute(
                sa.insert(ranking_scores).values(
                    id=uuid.uuid4(), unit_id=unit_id, area_id=AREA_ID, project_id=PROJECT_ID,
                    ranking_run_id=RUN_ID, config_version_id=CONFIG_ID, score=legacy_scores[unit_id],
                    rank_in_area=v3_ranks[unit_id], rank_in_project=v3_ranks[unit_id], weight_coverage=Decimal("1.0"),
                    contributions={}, hierarchical_score=Decimal("0.5"), hierarchical_contributions={"schema_version": 1},
                    computed_at=now,
                )
            )
        await session.commit()

    result = await preview_flat_weights(
        PROJECT_ID, weights=FLAT_WEIGHTS, min_weight_coverage=Decimal("0.5"), session_factory=factory
    )

    by_unit = {r.unit_id: r for r in result.results}
    # The re-derived "current" baseline must reflect PURE LEGACY order
    # (u1=1/u2=2/u3=3), never the persisted v3 order (u3=1/u2=2/u1=3).
    assert by_unit[str(UNIT_IDS[0])].current_rank == 1
    assert by_unit[str(UNIT_IDS[1])].current_rank == 2
    assert by_unit[str(UNIT_IDS[2])].current_rank == 3
