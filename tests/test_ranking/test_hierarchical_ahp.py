"""Hierarchical AHP (mandatory-scope item 7) — `src/ranking/hierarchical_ahp.py`
(hàm thuần) + `POST /ranking/ahp/hierarchical-weights`.

Same "no DB needed" discipline as `tests/test_ranking/test_ahp.py`: the math
module is pure, the endpoint writes nothing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.ranking.ahp import Judgment
from src.ranking.hierarchical_ahp import (
    HierarchicalAHPError,
    compute_hierarchical_ahp,
    grain_feature_block,
    grain_weights_block,
)
from src.services.ranking_config import validate_hierarchical_weights
from tests.conftest import DASHBOARD_AUTH_HEADER, DASHBOARD_VIEWER_TOKEN

API = "/api/v1/ranking/ahp/hierarchical-weights"

GRAIN_KEYS = ["market", "project", "area", "unit"]

# All-equal judgments (every pair = 1) is trivially, perfectly consistent
# (CR = 0) — a clean way to exercise the "everything passes" path without
# hand-deriving a non-trivial consistent matrix.
EQUAL_GRAIN_JUDGMENTS = [Judgment(a, b, Decimal("1")) for i, a in enumerate(GRAIN_KEYS) for b in GRAIN_KEYS[i + 1 :]]

MARKET_FEATURES = ["market_interest_rate", "market_demand"]
EQUAL_MARKET_JUDGMENTS = [Judgment(MARKET_FEATURES[0], MARKET_FEATURES[1], Decimal("1"))]

PROJECT_FEATURES = ["expert_location_score", "expert_infrastructure_score"]
EQUAL_PROJECT_JUDGMENTS = [Judgment(PROJECT_FEATURES[0], PROJECT_FEATURES[1], Decimal("1"))]

AREA_FEATURES = ["area_velocity_norm", "area_conversion_norm", "area_accessibility"]
EQUAL_AREA_JUDGMENTS = [
    Judgment(AREA_FEATURES[0], AREA_FEATURES[1], Decimal("1")),
    Judgment(AREA_FEATURES[0], AREA_FEATURES[2], Decimal("1")),
    Judgment(AREA_FEATURES[1], AREA_FEATURES[2], Decimal("1")),
]

# Cyclic (maximally inconsistent) 3-criteria judgments: a>>b, b>>c, but a==c.
INCONSISTENT_AREA_JUDGMENTS = [
    Judgment(AREA_FEATURES[0], AREA_FEATURES[1], Decimal("9")),
    Judgment(AREA_FEATURES[1], AREA_FEATURES[2], Decimal("9")),
    Judgment(AREA_FEATURES[0], AREA_FEATURES[2], Decimal("1")),
]

MARKET_SPECS = {
    "market_interest_rate": {"direction": "negative", "missing_value_policy": "neutral"},
    "market_demand": {"direction": "positive", "missing_value_policy": "neutral"},
}
PROJECT_SPECS = {
    "expert_location_score": {"direction": "positive", "missing_value_policy": "neutral"},
    "expert_infrastructure_score": {"direction": "positive", "missing_value_policy": "neutral"},
}
AREA_SPECS = {k: {"direction": "positive", "missing_value_policy": "neutral"} for k in AREA_FEATURES}


def _judgments_json(judgments: list[Judgment]) -> list[dict]:
    return [{"a": j.a, "b": j.b, "value": float(j.value)} for j in judgments]


def _grain_payload(judgments: list[Judgment], specs: dict) -> dict:
    return {"judgments": _judgments_json(judgments), "feature_specs": specs}


def _payload(*, area_judgments: list[Judgment] = EQUAL_AREA_JUDGMENTS) -> dict:
    return {
        "grain_judgments": _judgments_json(EQUAL_GRAIN_JUDGMENTS),
        "grain_missing_value_policies": {"market": "skip", "project": "skip", "area": "skip", "unit": "skip"},
        "market": _grain_payload(EQUAL_MARKET_JUDGMENTS, MARKET_SPECS),
        "project": _grain_payload(EQUAL_PROJECT_JUDGMENTS, PROJECT_SPECS),
        "area": _grain_payload(area_judgments, AREA_SPECS),
    }


# --- Pure module -------------------------------------------------------------


def test_all_four_levels_are_computed():
    result = compute_hierarchical_ahp(
        grain_judgments=EQUAL_GRAIN_JUDGMENTS,
        market_judgments=EQUAL_MARKET_JUDGMENTS,
        project_judgments=EQUAL_PROJECT_JUDGMENTS,
        area_judgments=EQUAL_AREA_JUDGMENTS,
    )
    assert set(result.levels) == {"grain_weights", "market", "project", "area"}
    assert result.all_consistent


def test_inconsistent_level_is_flagged_but_still_computed():
    result = compute_hierarchical_ahp(
        grain_judgments=EQUAL_GRAIN_JUDGMENTS,
        market_judgments=EQUAL_MARKET_JUDGMENTS,
        project_judgments=EQUAL_PROJECT_JUDGMENTS,
        area_judgments=INCONSISTENT_AREA_JUDGMENTS,
    )
    assert result.failed_levels == ["area"]
    assert not result.all_consistent
    assert result.levels["area"].result.consistency_ratio > result.levels["area"].result.threshold


def test_grain_weights_block_rejects_zero_missing_value_policy():
    result = compute_hierarchical_ahp(
        grain_judgments=EQUAL_GRAIN_JUDGMENTS,
        market_judgments=EQUAL_MARKET_JUDGMENTS,
        project_judgments=EQUAL_PROJECT_JUDGMENTS,
        area_judgments=EQUAL_AREA_JUDGMENTS,
    ).levels["grain_weights"].result
    with pytest.raises(HierarchicalAHPError) as exc:
        grain_weights_block(
            result, missing_value_policies={"market": "skip", "project": "skip", "area": "skip", "unit": "zero"}
        )
    assert exc.value.code == "HIERARCHICAL_GRAIN_ZERO_POLICY_FORBIDDEN"


def test_grain_weights_block_rejects_missing_spec():
    result = compute_hierarchical_ahp(
        grain_judgments=EQUAL_GRAIN_JUDGMENTS,
        market_judgments=EQUAL_MARKET_JUDGMENTS,
        project_judgments=EQUAL_PROJECT_JUDGMENTS,
        area_judgments=EQUAL_AREA_JUDGMENTS,
    ).levels["grain_weights"].result
    with pytest.raises(HierarchicalAHPError) as exc:
        grain_weights_block(result, missing_value_policies={"market": "skip"})
    assert exc.value.code == "SPEC_MISSING"


def test_assembled_block_validates_against_the_real_hierarchical_validator():
    result = compute_hierarchical_ahp(
        grain_judgments=EQUAL_GRAIN_JUDGMENTS,
        market_judgments=EQUAL_MARKET_JUDGMENTS,
        project_judgments=EQUAL_PROJECT_JUDGMENTS,
        area_judgments=EQUAL_AREA_JUDGMENTS,
    )
    block = {
        "grain_weights": grain_weights_block(
            result.levels["grain_weights"].result,
            missing_value_policies={"market": "skip", "project": "skip", "area": "skip", "unit": "skip"},
        ),
        "market": grain_feature_block(result.levels["market"].result, specs=MARKET_SPECS),
        "project": grain_feature_block(result.levels["project"].result, specs=PROJECT_SPECS),
        "area": grain_feature_block(result.levels["area"].result, specs=AREA_SPECS),
    }
    validate_hierarchical_weights(block)  # must not raise


# --- API ----------------------------------------------------------------------


async def _client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", headers=DASHBOARD_AUTH_HEADER)


@pytest.mark.asyncio
async def test_endpoint_returns_assembled_hierarchical_weights_when_all_consistent():
    async with await _client() as client:
        response = await client.post(API, json=_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["all_consistent"] is True
    assert body["failed_levels"] == []
    assert body["hierarchical_weights"] is not None
    assert set(body["hierarchical_weights"]["grain_weights"]) == {"market", "project", "area", "unit"}
    assert set(body["hierarchical_weights"]["market"]) == set(MARKET_FEATURES)
    assert set(body["hierarchical_weights"]["project"]) == set(PROJECT_FEATURES)
    assert set(body["hierarchical_weights"]["area"]) == set(AREA_FEATURES)


@pytest.mark.asyncio
async def test_endpoint_blocks_hierarchical_weights_when_one_level_fails_but_reports_all_levels():
    async with await _client() as client:
        response = await client.post(API, json=_payload(area_judgments=INCONSISTENT_AREA_JUDGMENTS))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["all_consistent"] is False
    assert body["failed_levels"] == ["area"]
    assert body["hierarchical_weights"] is None
    # Every level (including the three consistent ones) is still reported.
    assert {level["level"] for level in body["levels"]} == {"grain_weights", "market", "project", "area"}


@pytest.mark.asyncio
async def test_endpoint_requires_admin_role():
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {DASHBOARD_VIEWER_TOKEN}"}
    ) as client:
        response = await client.post(API, json=_payload())
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_endpoint_rejects_wrong_grain_judgment_count():
    async with await _client() as client:
        payload = _payload()
        payload["grain_judgments"] = [{"a": "market", "b": "project", "value": 1.0}]  # only 1 of 6 required
        response = await client.post(API, json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "JUDGMENTS_INCOMPLETE"


@pytest.mark.asyncio
async def test_endpoint_requires_all_three_grain_blocks():
    async with await _client() as client:
        payload = _payload()
        del payload["project"]
        response = await client.post(API, json=payload)
    assert response.status_code == 422
