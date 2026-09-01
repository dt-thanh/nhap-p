"""`GET /ranking/projects/{id}/report` — the CRM-only vs. Expert-enriched
`hierarchy_status`/`expert_criteria_applied` disclosure added to make the UI
truthfully distinguish flat-v2-only, published-CRM-only, and
published-Expert-enriched hierarchical rankings (business decision: v3
hierarchical = CRM base criteria + eligible governed Expert criteria, never a
v2+v3 blend).

Self-contained by design: `tests/test_agent_e2e.py` (the shared 5-unit
fixture `tests/test_ranking/test_hierarchical_scoring.py`,
`tests/test_api/test_ranking_hierarchical.py`, and eight other files import
from) has been deleted from this repository by an unrelated, concurrent
change since this session's own earlier successful runs of those suites —
confirmed via `git log -- tests/test_agent_e2e.py` (last touched by a prior
commit, no longer present) and `ls`. That is a real, pre-existing regression
outside this task's scope (restoring a widely-shared fixture module used by
ten files is a separate undertaking, not attempted here) — this file avoids
it entirely by building its own minimal project/area/unit/config/run/score
rows directly, so the NEW code this task adds (`_derive_hierarchy_disclosure`
in `src/api/ranking.py`, the `crm_feature_keys`/`expert_feature_keys` fields
in `src/ranking/hierarchical_view.py`, and the new `ProjectRankingReportOut`
fields) gets real, isolated `_test`-DB proof independent of that breakage.

Hand-built `hierarchical_contributions` payloads below mirror the *exact*
persisted shape `_build_hierarchical_contributions()`
(`src/ranking/service.py`) already produces and that
`tests/test_ranking/test_hierarchical_scoring.py` already extensively proves
correct — this file does not re-test the scoring engine itself, only the
NEW disclosure layer built on top of whatever the engine legitimately
persists.
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
from src.models.tables import areas, projects, ranking_configs, ranking_runs, ranking_scores, units
from tests.conftest import DASHBOARD_ADMIN_TOKEN, db_skip_reason

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

PROJECT_ID = uuid.uuid4()
AREA_ID = uuid.uuid4()
UNIT_ID = uuid.uuid4()
CONFIG_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
EXTERNAL_PROJECT_ID = "P-HIER-DISCLOSURE-TEST"
REPORT_API = f"/api/v1/ranking/projects/{EXTERNAL_PROJECT_ID}/report"
ADMIN_HEADER = {"Authorization": f"Bearer {DASHBOARD_ADMIN_TOKEN}"}

FLAT_WEIGHTS = {"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}}

# Same shape as tests/test_ranking/test_hierarchical_scoring.py's own
# VALID_HIERARCHICAL_WEIGHTS: Area's sole configured criterion is an
# Expert-only key (never CRM), so "eligible" for Area in this fixture always
# means a real Expert value was used — never a CRM fallback silently doing
# the same job.
HIERARCHICAL_WEIGHTS = {
    "market": {"market_interest_rate": {"weight": 1.0, "direction": "negative", "missing_value_policy": "neutral"}},
    "project": {"expert_location_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}},
    "area": {"area_accessibility": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}},
    "grain_weights": {
        "market": {"weight": 0.10, "missing_value_policy": "skip"},
        "project": {"weight": 0.25, "missing_value_policy": "skip"},
        "area": {"weight": 0.25, "missing_value_policy": "skip"},
        "unit": {"weight": 0.40, "missing_value_policy": "skip"},
    },
}

_EXCLUDED_MARKET_PROJECT = {
    "market": {"reason": "NO_PUBLISHED_MARKET_VALUE"},
    "project": {"reason": "NO_PUBLISHED_PROJECT_VALUE"},
}


def _unit_only_contributions(config_id: uuid.UUID) -> dict:
    """No Expert value published anywhere — the "no Advisor document/
    assessment exists yet" scenario. Ranking must still work from CRM
    (Unit-only hierarchical score), never a fabricated Expert contribution."""
    excluded = {**_EXCLUDED_MARKET_PROJECT, "area": {"reason": "NO_PUBLISHED_AREA_EXPERT_VALUE"}}
    return {
        "schema_version": 1,
        "score_mode": "unit_only",
        "configured_grain_weights": HIERARCHICAL_WEIGHTS["grain_weights"],
        "effective_grain_weights": {"unit": "1.000000"},
        "top_level_weight_coverage": "0.4",
        "eligible_grains": [],
        "excluded_grains": excluded,
        "grains": {
            "market": {"eligible": False, "score": None, "coverage": None, "exclusion_reason": "NO_PUBLISHED_MARKET_VALUE"},
            "project": {"eligible": False, "score": None, "coverage": None, "exclusion_reason": "NO_PUBLISHED_PROJECT_VALUE"},
            "area": {"eligible": False, "score": None, "coverage": None, "exclusion_reason": "NO_PUBLISHED_AREA_EXPERT_VALUE"},
            "unit": {"eligible": True, "score": "0.5900", "coverage": "1.0", "exclusion_reason": None},
        },
        "snapshot_id": None,
        "config_version_id": str(config_id),
        "cutoff_at": datetime.now(UTC).isoformat(),
        "legal_gate": None,
        "comparability_warning": None,
        "disclosure": "Unit-only hierarchical score — Market, Project, and Area context unavailable.",
    }


def _expert_enriched_contributions(config_id: uuid.UUID) -> dict:
    """A real, CEO-approved, published, effective `area_accessibility` value
    resolved for this unit's area — Market/Project remain unpublished and
    stay excluded, proving only the actually-eligible criterion is disclosed,
    never all three at once."""
    return {
        "schema_version": 1,
        "score_mode": "partial_hierarchical",
        "configured_grain_weights": HIERARCHICAL_WEIGHTS["grain_weights"],
        "effective_grain_weights": {"unit": "0.615385", "area": "0.384615"},
        "top_level_weight_coverage": "0.65",
        "eligible_grains": ["area"],
        "excluded_grains": dict(_EXCLUDED_MARKET_PROJECT),
        "grains": {
            "market": {"eligible": False, "score": None, "coverage": None, "exclusion_reason": "NO_PUBLISHED_MARKET_VALUE"},
            "project": {"eligible": False, "score": None, "coverage": None, "exclusion_reason": "NO_PUBLISHED_PROJECT_VALUE"},
            "area": {
                "eligible": True, "score": "0.7000", "coverage": "1.0", "exclusion_reason": None,
                "snapshot_id": str(uuid.uuid4()), "feature_value_ids": [], "feature_justification_ids": [],
                "crm_feature_keys": [], "expert_feature_keys": ["area_accessibility"],
            },
            "unit": {"eligible": True, "score": "0.5900", "coverage": "1.0", "exclusion_reason": None},
        },
        "snapshot_id": None,
        "config_version_id": str(config_id),
        "cutoff_at": datetime.now(UTC).isoformat(),
        "legal_gate": None,
        "comparability_warning": None,
        "disclosure": "Partial hierarchical score — excluded: market (NO_PUBLISHED_MARKET_VALUE), project (NO_PUBLISHED_PROJECT_VALUE).",
    }


@pytest_asyncio.fixture
async def http(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    for target in ("src.api.ranking.get_session_factory",):
        monkeypatch.setattr(target, lambda factory=factory: factory, raising=False)
    monkeypatch.setattr(
        "src.api.ranking.get_settings",
        lambda: type("_S", (), {"hierarchical_read_enabled": True})(),
    )

    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(projects).values(
                id=PROJECT_ID, name="Hierarchy Disclosure Test", launch_date=date(2026, 1, 1),
                created_at=now, updated_at=now, absorption_calculator="legacy_aggregate",
                external_id=EXTERNAL_PROJECT_ID, source_system="mini_crm", source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(areas).values(
                id=AREA_ID, project_id=PROJECT_ID, area_name="Test Area", unit_type="apartment",
                bedrooms=2, area_sqm=Decimal("70"), total_units=1, created_at=now, updated_at=now,
            )
        )
        await session.execute(
            sa.insert(units).values(
                id=UNIT_ID, source_system="mini_crm", source_instance_id="test", external_unit_id="U-1",
                area_id=AREA_ID, unit_code="U-1", unit_type="2PN", status="available",
                created_at=now, updated_at=now,
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.session_factory = factory  # type: ignore[attr-defined]
        yield client


async def _seed_config_run_and_score(
    factory, *, hierarchical_weights: dict | None, hierarchical_contributions: dict | None
) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
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
                status="completed", config_version_id=CONFIG_ID, units_processed=1, units_ranked=1,
                units_skipped=0, enqueued_at=now, started_at=now, finished_at=now,
            )
        )
        await session.execute(
            sa.insert(ranking_scores).values(
                id=uuid.uuid4(), unit_id=UNIT_ID, area_id=AREA_ID, project_id=PROJECT_ID,
                ranking_run_id=RUN_ID, config_version_id=CONFIG_ID, score=Decimal("0.5900"),
                rank_in_area=1, rank_in_project=1, weight_coverage=Decimal("1.0"),
                contributions={"unit_available": {"source": "resolved", "value": "0.5900", "weight": "1.0"}},
                hierarchical_score=Decimal("0.5900") if hierarchical_contributions else None,
                hierarchical_contributions=hierarchical_contributions,
                computed_at=now,
            )
        )
        await session.commit()


async def test_crm_only_when_no_expert_value_is_published(http):
    await _seed_config_run_and_score(
        http.session_factory,
        hierarchical_weights=HIERARCHICAL_WEIGHTS,
        hierarchical_contributions=_unit_only_contributions(CONFIG_ID),
    )
    body = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
    assert body["state"] == "ready"
    assert body["hierarchy_status"] == "crm_only"
    assert body["expert_criteria_applied"] == []
    assert body["score_mode_counts"] == {"unit_only": 1}
    assert set(body["representative_excluded_grains"]) == {"market", "project", "area"}


async def test_expert_enriched_once_a_published_effective_area_value_resolves(http):
    await _seed_config_run_and_score(
        http.session_factory,
        hierarchical_weights=HIERARCHICAL_WEIGHTS,
        hierarchical_contributions=_expert_enriched_contributions(CONFIG_ID),
    )
    body = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
    assert body["state"] == "ready"
    assert body["hierarchy_status"] == "expert_enriched"
    assert body["expert_criteria_applied"] == ["area_accessibility"]
    assert body["score_mode_counts"] == {"partial_hierarchical": 1}
    assert set(body["representative_excluded_grains"]) == {"market", "project"}
    unit = body["unit_results"][0]
    area_grain = unit["hierarchical"]["grains"]["area"]
    assert area_grain["expert_feature_keys"] == ["area_accessibility"]
    assert area_grain["crm_feature_keys"] == []


async def test_not_published_when_active_config_has_no_hierarchical_weights(http):
    """The exact reported screenshot scenario: flat v2 active,
    `hierarchical_weights IS NULL`. Must disclose `not_published`, never
    crash, never claim an AHP failure or fabricate a score."""
    await _seed_config_run_and_score(
        http.session_factory, hierarchical_weights=None, hierarchical_contributions=None
    )
    body = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
    assert body["hierarchy_status"] == "not_published"
    assert body["expert_criteria_applied"] == []
    assert body["score_mode_counts"] == {}
    # Flat v2's own score is untouched and still real — never hidden, never summed.
    unit = body["unit_results"][0]
    assert unit["hierarchical"]["available"] is False
    assert unit["hierarchical"]["reason"] == "NOT_COMPUTED"


async def test_feature_flag_off_also_reports_not_published_not_a_separate_new_state(http):
    """`hierarchical_read_enabled=False` and `hierarchical_weights IS NULL`
    are two different root causes that must collapse to the same honest
    `not_published` disclosure — the mission's own instruction that both
    "flat v2 active" scenarios show identical CRM-first messaging."""

    import src.api.ranking as ranking_module

    original = ranking_module.get_settings
    ranking_module.get_settings = lambda: type("_S", (), {"hierarchical_read_enabled": False})()
    try:
        await _seed_config_run_and_score(
            http.session_factory,
            hierarchical_weights=HIERARCHICAL_WEIGHTS,
            hierarchical_contributions=_expert_enriched_contributions(CONFIG_ID),
        )
        body = (await http.get(REPORT_API, headers=ADMIN_HEADER)).json()
        assert body["state"] == "feature_disabled"
        assert body["hierarchy_status"] == "not_published"
        assert body["expert_criteria_applied"] == []
    finally:
        ranking_module.get_settings = original
