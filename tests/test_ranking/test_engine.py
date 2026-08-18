"""Hàm THUẦN `src/ranking/engine.py` — công thức §10.1 tài liệu kế hoạch. Không
cần DB: đầu vào là `UnitFeatureInput` dựng tay.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from src.ranking.engine import FeatureWeight, UnitFeatureInput, rank_scores, score_unit

WEIGHTS = [
    FeatureWeight(key="a", weight=Decimal("0.5"), direction="positive", missing_value_policy="zero"),
    FeatureWeight(key="b", weight=Decimal("0.5"), direction="negative", missing_value_policy="zero"),
]


def _t(offset_seconds: int = 0):
    return datetime(2026, 1, 1, tzinfo=UTC).timestamp() + offset_seconds


def _unit(unit_id: str, area_id: str, values: dict, created_at=0) -> UnitFeatureInput:
    return UnitFeatureInput(unit_id=unit_id, area_id=area_id, tie_break_created_at=created_at, values=values)


def test_positive_direction_uses_value_as_is():
    u = _unit("u1", "a1", {"a": Decimal("1"), "b": Decimal("0")})
    result = score_unit(u, WEIGHTS, Decimal("0.5"))
    # a: 0.5 * 1 = 0.5 ; b (negative): 0.5 * (1-0) = 0.5 -> numerator=1.0, denom=1.0
    assert result.score == Decimal("1.0000")
    assert result.skipped is False


def test_negative_direction_inverts_value():
    u = _unit("u1", "a1", {"a": Decimal("0"), "b": Decimal("1")})
    result = score_unit(u, WEIGHTS, Decimal("0.5"))
    # a: 0.5*0=0 ; b negative: 0.5*(1-1)=0 -> score 0
    assert result.score == Decimal("0.0000")


def test_missing_with_zero_policy_contributes_zero_but_counts_weight():
    weights = [FeatureWeight(key="a", weight=Decimal("1"), direction="positive", missing_value_policy="zero")]
    u = _unit("u1", "a1", {"a": None})
    result = score_unit(u, weights, Decimal("0.5"))
    assert result.skipped is False
    assert result.score == Decimal("0.0000")
    assert result.coverage == Decimal("1")


def test_missing_with_neutral_policy_contributes_half():
    weights = [FeatureWeight(key="a", weight=Decimal("1"), direction="positive", missing_value_policy="neutral")]
    u = _unit("u1", "a1", {"a": None})
    result = score_unit(u, weights, Decimal("0.5"))
    assert result.score == Decimal("0.5000")


def test_missing_with_skip_policy_removes_weight_from_denominator():
    weights = [
        FeatureWeight(key="a", weight=Decimal("0.5"), direction="positive", missing_value_policy="skip"),
        FeatureWeight(key="b", weight=Decimal("0.5"), direction="positive", missing_value_policy="zero"),
    ]
    u = _unit("u1", "a1", {"a": None, "b": Decimal("1")})
    result = score_unit(u, weights, Decimal("0.4"))
    # a skipped entirely: denom = 0.5, numerator = 0.5*1 = 0.5 -> score 1.0, coverage 0.5
    assert result.skipped is False
    assert result.coverage == Decimal("0.5")
    assert result.score == Decimal("1.0000")


def test_coverage_below_threshold_skips_the_unit_entirely():
    weights = [
        FeatureWeight(key="a", weight=Decimal("0.3"), direction="positive", missing_value_policy="skip"),
        FeatureWeight(key="b", weight=Decimal("0.7"), direction="positive", missing_value_policy="skip"),
    ]
    u = _unit("u1", "a1", {"a": Decimal("1"), "b": None})
    # only 'a' counts -> coverage 0.3 < min 0.5 -> skipped, no score
    result = score_unit(u, weights, Decimal("0.5"))
    assert result.skipped is True
    assert result.score is None
    assert result.skip_reason == "coverage_below_threshold"


def test_confidence_below_min_confidence_is_treated_as_missing():
    weights = [
        FeatureWeight(key="a", weight=Decimal("1"), direction="positive", missing_value_policy="zero", min_confidence=Decimal("0.6")),
    ]
    u = UnitFeatureInput(
        unit_id="u1", area_id="a1", tie_break_created_at=0,
        values={"a": Decimal("1")}, confidences={"a": Decimal("0.3")},
    )
    result = score_unit(u, weights, Decimal("0.5"))
    assert result.score == Decimal("0.0000")  # treated as missing -> zero policy


def test_score_rounds_to_four_decimal_places_half_up():
    weights = [
        FeatureWeight(key="a", weight=Decimal("1"), direction="positive", missing_value_policy="zero"),
    ]
    u = _unit("u1", "a1", {"a": Decimal("0.123456")})
    result = score_unit(u, weights, Decimal("0.5"))
    assert result.score == Decimal("0.1235")


def test_rank_scores_orders_by_score_desc_then_created_at_asc_then_unit_id_asc():
    scored = [
        score_unit(_unit("u2", "a1", {"a": Decimal("1"), "b": Decimal("0")}, created_at=5), WEIGHTS, Decimal("0.5")),
        score_unit(_unit("u1", "a1", {"a": Decimal("1"), "b": Decimal("0")}, created_at=1), WEIGHTS, Decimal("0.5")),
        score_unit(_unit("u3", "a1", {"a": Decimal("0"), "b": Decimal("1")}, created_at=0), WEIGHTS, Decimal("0.5")),
    ]
    ranked = rank_scores(scored)
    ordered_ids = [s.unit_id for s in sorted(ranked, key=lambda s: s.rank_in_project)]
    # u1 and u2 tie at score 1.0 -> tie-break by created_at ASC -> u1 (created_at=1) before u2 (created_at=5)
    assert ordered_ids == ["u1", "u2", "u3"]


def test_rank_scores_assigns_rank_in_area_independently_per_area():
    high = score_unit(_unit("u1", "area-a", {"a": Decimal("1"), "b": Decimal("0")}), WEIGHTS, Decimal("0.5"))
    low_same_area = score_unit(_unit("u2", "area-a", {"a": Decimal("0"), "b": Decimal("1")}), WEIGHTS, Decimal("0.5"))
    high_other_area = score_unit(_unit("u3", "area-b", {"a": Decimal("1"), "b": Decimal("0")}), WEIGHTS, Decimal("0.5"))

    ranked = {s.unit_id: s for s in rank_scores([high, low_same_area, high_other_area])}

    assert ranked["u1"].rank_in_area == 1
    assert ranked["u2"].rank_in_area == 2
    # u3 is alone in area-b -> rank_in_area 1 even though it ties u1 project-wide
    assert ranked["u3"].rank_in_area == 1
    # project-wide: u1 and u3 tie at score 1.0 -> tie-break by unit_id ASC (both created_at=0)
    assert ranked["u1"].rank_in_project == 1
    assert ranked["u3"].rank_in_project == 2
    assert ranked["u2"].rank_in_project == 3


def test_rank_scores_excludes_skipped_units_from_ranking():
    weights = [FeatureWeight(key="a", weight=Decimal("1"), direction="positive", missing_value_policy="skip")]
    kept = score_unit(_unit("u1", "a1", {"a": Decimal("1")}), weights, Decimal("0.5"))
    skipped = score_unit(_unit("u2", "a1", {"a": None}), weights, Decimal("0.5"))

    ranked = rank_scores([kept, skipped])
    by_id = {s.unit_id: s for s in ranked}

    assert by_id["u1"].rank_in_project == 1
    assert by_id["u2"].rank_in_project is None
    assert by_id["u2"].skipped is True
