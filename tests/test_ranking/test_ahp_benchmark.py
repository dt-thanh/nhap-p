"""`eval/ahp_benchmark.py` — bộ chạy benchmark công thức xếp hạng V2.

Test quan trọng nhất file này là `test_harness_reproduces_the_exported_production_ranking`:
nó chứng minh bộ chạy tái tạo ĐÚNG bảng xếp hạng mà pipeline thật đã sinh ra.
Một benchmark không có bảo đảm đó thì chỉ đang đo chính nó.

Chính test đó đã bắt được lỗi thật: bản đầu gộp cả 200 căn của BA dự án vào một
rổ, trong khi `run_ranking` chạy theo từng dự án. Điểm khớp 200/200 nhưng thứ
hạng lệch 195/200 — và mọi chỉ số so sánh dựng trên thứ hạng đó đều vô nghĩa.
"""

from __future__ import annotations

import csv
from decimal import Decimal

from eval.ahp_benchmark import (
    DATASET,
    FEATURES,
    V1_WEIGHTS,
    agg,
    entropy_weights,
    equal_weights,
    kendall_tau,
    load_units,
    orderings,
    overlap_at,
    perturb,
    rank_maps,
    rank_with,
    spearman,
    weighted,
)
from src.ranking.bands import band_for

# --- Trung thực với pipeline thật --------------------------------------------


def test_harness_reproduces_the_exported_production_ranking():
    """Chấm lại bằng trọng số V1 phải ra ĐÚNG điểm/hạng/mức đã xuất.

    Bảng trong `units_ranking.csv` do pipeline thật sinh ra dưới config v2 —
    cùng bộ trọng số V1 ở đây. Khớp tuyệt đối nghĩa là benchmark đo hệ thống
    thật chứ không đo một bản dựng lại gần giống.
    """
    exported = {row["unit_id"]: row for row in csv.DictReader(DATASET.open(encoding="utf-8"))}
    by_project, _ = load_units()
    mine = {s.unit_id: s for group in rank_with(V1_WEIGHTS, by_project).values() for s in group}

    assert len(mine) == len(exported) == 200
    for unit_id, score in mine.items():
        assert score.score == Decimal(exported[unit_id]["score"]), f"lệch điểm ở {unit_id}"
        assert score.rank_in_project == int(exported[unit_id]["rank_in_project"]), f"lệch hạng ở {unit_id}"
        assert band_for(score.score) == exported[unit_id]["band"], f"lệch mức ở {unit_id}"


def test_ranking_is_scoped_per_project_not_pooled():
    """Hồi quy: mỗi dự án phải có thứ hạng chạy 1..N của RIÊNG nó.

    Gộp chung sẽ cho một dãy 1..200 duy nhất — bảng xếp hạng chưa từng tồn tại
    trong hệ thống, và "top-10" khi đó là top-10 của ba dự án trộn lẫn.
    """
    by_project, _ = load_units()
    assert len(by_project) == 3
    for project, ranks in rank_maps(rank_with(V1_WEIGHTS, by_project)).items():
        assert sorted(ranks.values()) == list(range(1, len(ranks) + 1)), f"{project} không chạy 1..N"


# --- Chỉ số so sánh ----------------------------------------------------------


def _perm(order: list[str]) -> dict[str, int]:
    return {key: i + 1 for i, key in enumerate(order)}


def test_correlations_are_one_for_identical_and_minus_one_for_reversed():
    forward = _perm(["a", "b", "c", "d", "e"])
    backward = _perm(["e", "d", "c", "b", "a"])
    assert spearman(forward, forward) == 1.0
    assert kendall_tau(forward, forward) == 1.0
    assert spearman(forward, backward) == -1.0
    assert kendall_tau(forward, backward) == -1.0


def test_kendall_counts_a_single_adjacent_swap():
    """Đổi chỗ một cặp kề trong 4 phần tử: 5 thuận / 1 nghịch trên 6 cặp."""
    assert kendall_tau(_perm(["a", "b", "c", "d"]), _perm(["b", "a", "c", "d"])) == (5 - 1) / 6


def test_overlap_at_is_intersection_over_k_not_jaccard():
    """Trùng 9/10 phải ra 0.90. Jaccard cho 0.82 và người đọc sẽ hiểu nhầm."""
    a = [f"u{i}" for i in range(20)]
    b = ["zzz"] + [f"u{i}" for i in range(20)]
    assert overlap_at(a, b, 10) == 0.9
    assert overlap_at(a, a, 10) == 1.0
    assert overlap_at(a, [f"v{i}" for i in range(20)], 10) == 0.0


def test_agg_weights_projects_by_unit_count_not_evenly():
    """Dự án 106 căn phải nặng hơn dự án 28 căn — trung bình cộng thường sẽ sai."""
    left = {"big": _perm(list("abcdefghij")), "small": _perm(list("xy"))}
    right = {"big": _perm(list("abcdefghij")), "small": _perm(list("yx"))}
    # big: ρ=+1 (10 căn), small: ρ=-1 (2 căn) -> (10 - 2) / 12
    assert agg(spearman, left, right) == (10 * 1.0 + 2 * -1.0) / 12
    assert weighted([(1.0, 10), (-1.0, 2)]) == (10 - 2) / 12


# --- Bộ trọng số -------------------------------------------------------------


def test_every_scheme_sums_to_exactly_one():
    by_project, _ = load_units()
    for weights in (V1_WEIGHTS, equal_weights(), entropy_weights(by_project)):
        assert sum(weights.values()) == Decimal("1.0000")


def test_entropy_gives_near_zero_weight_to_a_near_constant_feature():
    """`area_conversion_norm` gần như không đổi giữa các căn, nên theo entropy nó
    hầu như không phân biệt được gì — đó chính là điều phương pháp này phải nói."""
    weights = entropy_weights(load_units()[0])
    assert weights["area_conversion_norm"] < weights["unit_demand_norm"]
    assert weights["area_conversion_norm"] < Decimal("0.05")


def test_perturb_raises_the_target_and_renormalises():
    bumped = perturb(V1_WEIGHTS, "unit_available", Decimal("0.20"))
    assert sum(bumped.values()) == Decimal("1.0000")
    assert bumped["unit_available"] > V1_WEIGHTS["unit_available"]
    for key in FEATURES:
        if key != "unit_available":
            assert bumped[key] < V1_WEIGHTS[key], "chuẩn hoá phải kéo các trọng số còn lại xuống"


def test_orderings_drop_skipped_units_and_stay_deterministic():
    by_project, _ = load_units()
    first = orderings(rank_with(V1_WEIGHTS, by_project))
    second = orderings(rank_with(V1_WEIGHTS, by_project))
    assert first == second
    assert all(len(order) > 0 for order in first.values())
