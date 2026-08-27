"""`validate_hierarchical_weights()` — isolated from legacy `validate_weights()`
(D41): a separate function, a separate error type, no shared vocabulary gate.
No DB needed — pure structural validation over a dict, same style as
`test_survey_and_config.py`'s `validate_weights()` tests.
"""

from __future__ import annotations

import copy

import pytest

from src.services.ranking_config import (
    HierarchicalConfigError,
    validate_hierarchical_weights,
    validate_weights,
)

VALID = {
    "market": {
        "market_interest_rate": {"weight": 0.5, "direction": "negative", "missing_value_policy": "neutral"},
        "market_demand": {"weight": 0.5, "direction": "positive", "missing_value_policy": "neutral"},
    },
    "project": {
        "expert_location_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"},
    },
    "area": {
        "area_velocity_norm": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"},
    },
    "grain_weights": {
        "market": {"weight": 0.10, "missing_value_policy": "skip"},
        "project": {"weight": 0.25, "missing_value_policy": "skip"},
        "area": {"weight": 0.25, "missing_value_policy": "skip"},
        "unit": {"weight": 0.40, "missing_value_policy": "skip"},
    },
}


def _copy() -> dict:
    return copy.deepcopy(VALID)


def test_valid_hierarchical_weights_passes():
    validate_hierarchical_weights(_copy())  # no raise


def test_empty_is_rejected():
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights({})
    assert exc.value.code == "HIERARCHICAL_WEIGHTS_EMPTY"


def test_unit_block_is_forbidden():
    """PR-1's mandatory compatibility rule: `U` is read exclusively from the
    persisted legacy `ranking_scores.score` — a `"unit"` feature-weight block
    inside `hierarchical_weights` would be a second, competing unit-weight
    vector, and must be rejected, not silently ignored."""
    bad = _copy()
    bad["unit"] = {"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}}
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_WEIGHTS_UNIT_BLOCK_FORBIDDEN"


@pytest.mark.parametrize("missing_key", ["market", "project", "area", "grain_weights"])
def test_missing_required_top_level_key_is_rejected(missing_key):
    bad = _copy()
    del bad[missing_key]
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_WEIGHTS_KEY_MISSING"


@pytest.mark.parametrize("grain", ["market", "project", "area"])
def test_grain_feature_weights_must_sum_to_one(grain):
    bad = _copy()
    first_key = next(iter(bad[grain]))
    bad[grain][first_key]["weight"] = 0.999999
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_WEIGHT_SUM"


def test_grain_feature_invalid_direction_is_rejected():
    bad = _copy()
    first_key = next(iter(bad["market"]))
    bad["market"][first_key]["direction"] = "upwards"
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_DIRECTION_INVALID"


def test_grain_feature_negative_weight_is_rejected():
    bad = _copy()
    first_key = next(iter(bad["market"]))
    bad["market"][first_key]["weight"] = -0.1
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_WEIGHT_NEGATIVE"


def test_grain_weights_must_have_exactly_the_four_keys():
    bad = _copy()
    del bad["grain_weights"]["area"]
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_GRAIN_WEIGHTS_KEYS"


def test_grain_weights_extra_key_is_rejected():
    bad = _copy()
    bad["grain_weights"]["developer"] = {"weight": 0.0, "missing_value_policy": "skip"}
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_GRAIN_WEIGHTS_KEYS"


def test_grain_weights_must_sum_to_one():
    bad = _copy()
    bad["grain_weights"]["unit"]["weight"] = 0.30  # total now 0.90
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_GRAIN_WEIGHT_SUM"


def test_grain_weights_zero_missing_policy_is_forbidden():
    """D37: an excluded grain must leave the composition (renormalize), never
    be scored as a flat 0 — `'zero'` would silently penalize a unit for a
    parent grain nobody has published yet."""
    bad = _copy()
    bad["grain_weights"]["market"]["missing_value_policy"] = "zero"
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_GRAIN_ZERO_POLICY_FORBIDDEN"


def test_grain_weights_negative_weight_is_rejected():
    bad = _copy()
    bad["grain_weights"]["market"]["weight"] = -0.05
    with pytest.raises(HierarchicalConfigError) as exc:
        validate_hierarchical_weights(bad)
    assert exc.value.code == "HIERARCHICAL_GRAIN_WEIGHT_NEGATIVE"


# --- Isolation from legacy validate_weights() --------------------------------


def test_nested_shape_is_rejected_by_legacy_validate_weights():
    """Documents the observed legacy behavior D41 relies on: if the nested
    shape were ever handed to `validate_weights()` (it never is, in this
    module's own code), it is rejected — `'market'`/`'project'`/`'area'`/
    `'grain_weights'` are not registered feature keys."""
    from src.services.ranking_config import ConfigError

    with pytest.raises(ConfigError) as exc:
        validate_weights(VALID)
    assert exc.value.code == "UNKNOWN_FEATURE"
