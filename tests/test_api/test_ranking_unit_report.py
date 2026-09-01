from decimal import Decimal

from src.api.ranking import _build_unit_explanation, _unit_report_criteria


def _persisted_row(unit_value: str) -> dict:
    return {
        "weight_coverage": Decimal("1"),
        "contributions": {
            "unit_available": {
                "value": unit_value,
                "weight": "0.6",
                "contribution": str(Decimal(unit_value) * Decimal("0.6")),
                "source": "resolved",
            },
            "unit_demand_norm": {
                "value": "0.25",
                "weight": "0.4",
                "contribution": "0.1",
                "source": "resolved",
            },
        },
        "hierarchical_contributions": {
            "effective_grain_weights": {"unit": "0.5", "area": "0.5"},
            "grains": {
                "unit": {"eligible": True, "score": unit_value, "coverage": "1"},
                "area": {"eligible": True, "score": "0.8", "coverage": "1"},
            },
        },
    }


def test_high_rank_explanation_uses_strongest_persisted_contribution():
    criteria = _unit_report_criteria(_persisted_row("1"))
    explanation = _build_unit_explanation("A1.06.05", 1, 12, criteria)

    assert "nhóm xếp hạng cao (#1/12)" in explanation
    assert "area đóng góp nhiều nhất" in explanation
    assert sum(item.contribution for item in criteria) == Decimal("0.750")


def test_low_rank_explanation_uses_lowest_normalized_persisted_criterion():
    criteria = _unit_report_criteria(_persisted_row("0.2"))
    explanation = _build_unit_explanation("B2.19.05", 12, 12, criteria)

    assert "nhóm xếp hạng thấp (#12/12)" in explanation
    assert "unit_available có điểm chuẩn hóa thấp nhất" in explanation
