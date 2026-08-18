"""Hàm tính điểm THUẦN — công thức §10.1 `docs/ranking/implementation_plan.md`.

Không I/O, không mạng, không truy cập DB. Nhận vào giá trị đặc trưng ĐÃ ĐƯỢC
GIẢI QUYẾT (service đã xong phần kế thừa unit → area → unit_type), chỉ làm phép
tính: định hướng giá trị, tổng có trọng số, coverage, làm tròn, xếp hạng.

    oriented(v, direction) = v          nếu direction = 'positive'
                           = 1 - v      nếu direction = 'negative'

    numerator   = Σ weight_i × oriented(value_i, direction_i)   (đặc trưng được tính)
    denominator = Σ weight_i                                    (đặc trưng được tính)
    coverage    = denominator
    coverage < min_weight_coverage  → BỎ QUA căn (không có điểm)
    ngược lại                       → score = round(numerator / denominator, 4)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Direction = Literal["positive", "negative"]
MissingPolicy = Literal["skip", "zero", "neutral"]


@dataclass(frozen=True)
class FeatureWeight:
    key: str
    weight: Decimal
    direction: Direction
    missing_value_policy: MissingPolicy
    min_confidence: Decimal = Decimal("0")


@dataclass(frozen=True)
class UnitFeatureInput:
    """Giá trị đặc trưng đã giải quyết cho một căn — trước khi tính điểm."""

    unit_id: str
    area_id: str
    tie_break_created_at: object  # datetime, chỉ dùng để so sánh — không diễn giải ở đây
    values: dict[str, Decimal | None] = field(default_factory=dict)
    confidences: dict[str, Decimal | None] = field(default_factory=dict)


@dataclass(frozen=True)
class UnitScore:
    unit_id: str
    area_id: str
    score: Decimal | None
    coverage: Decimal
    contributions: dict[str, dict]
    skipped: bool
    skip_reason: str | None
    tie_break_created_at: object = None
    rank_in_area: int | None = None
    rank_in_project: int | None = None


def oriented(value: Decimal, direction: Direction) -> Decimal:
    if direction == "positive":
        return value
    if direction == "negative":
        return Decimal("1") - value
    raise ValueError(f"direction không hợp lệ: {direction!r}")


def score_unit(
    unit: UnitFeatureInput,
    weights: list[FeatureWeight],
    min_weight_coverage: Decimal,
) -> UnitScore:
    numerator = Decimal("0")
    denominator = Decimal("0")
    contributions: dict[str, dict] = {}

    for w in weights:
        raw_value = unit.values.get(w.key)
        confidence = unit.confidences.get(w.key)
        is_missing = raw_value is None or (confidence is not None and confidence < w.min_confidence)

        if is_missing:
            if w.missing_value_policy == "skip":
                contributions[w.key] = {
                    "value": None,
                    "weight": str(w.weight),
                    "direction": w.direction,
                    "contribution": "0",
                    "source": "missing_skipped",
                }
                continue
            resolved_value = Decimal("0") if w.missing_value_policy == "zero" else Decimal("0.5")
            source = "missing_defaulted"
        else:
            resolved_value = raw_value
            source = "resolved"

        contribution = w.weight * oriented(resolved_value, w.direction)
        numerator += contribution
        denominator += w.weight
        contributions[w.key] = {
            "value": str(resolved_value),
            "weight": str(w.weight),
            "direction": w.direction,
            "contribution": str(contribution),
            "source": source,
        }

    coverage = denominator
    if coverage < min_weight_coverage:
        return UnitScore(
            unit_id=unit.unit_id,
            area_id=unit.area_id,
            score=None,
            coverage=coverage,
            contributions=contributions,
            skipped=True,
            skip_reason="coverage_below_threshold",
            tie_break_created_at=unit.tie_break_created_at,
        )

    score = (numerator / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return UnitScore(
        unit_id=unit.unit_id,
        area_id=unit.area_id,
        score=score,
        coverage=coverage,
        contributions=contributions,
        skipped=False,
        skip_reason=None,
        tie_break_created_at=unit.tie_break_created_at,
    )


def rank_scores(scores: list[UnitScore]) -> list[UnitScore]:
    """Gán `rank_in_project` (toàn bộ) và `rank_in_area` (trong từng phân khu).

    Phá hoà: score DESC, rồi tie_break_created_at ASC, rồi unit_id ASC — hoàn
    toàn tất định (§10.1). Căn bị `skipped` không được xếp hạng và giữ nguyên
    `rank_in_area`/`rank_in_project = None`.
    """
    ranked = [s for s in scores if not s.skipped]
    skipped = [s for s in scores if s.skipped]

    def sort_key(s: UnitScore):
        return (-s.score, s.tie_break_created_at, s.unit_id)

    project_order = sorted(ranked, key=sort_key)
    project_ranked = [
        UnitScore(**{**s.__dict__, "rank_in_project": i + 1}) for i, s in enumerate(project_order)
    ]

    by_area: dict[str, list[UnitScore]] = {}
    for s in project_ranked:
        by_area.setdefault(s.area_id, []).append(s)

    final: dict[str, UnitScore] = {}
    for area_id, area_scores in by_area.items():
        for i, s in enumerate(sorted(area_scores, key=sort_key)):
            final[s.unit_id] = UnitScore(**{**s.__dict__, "rank_in_area": i + 1})

    return [final[s.unit_id] for s in project_ranked] + skipped
