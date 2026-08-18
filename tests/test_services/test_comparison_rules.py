"""Phân loại chênh lệch cũ ↔ miền: mọi loại, và mặc định CHẶN.

Bộ luật này quyết định thứ nghiêm trọng nhất trong cả Phase 8 — cái gì được coi là
"giải thích được" và cái gì chặn việc cắt sang. Nên nó được kiểm ở mức hàm thuần,
không cần database: đầu vào là một dòng `calculator_comparisons`, đầu ra là một
phán quyết, và giữa hai thứ đó không có gì khác chen vào được.

Test quan trọng nhất trong file này là `test_an_unrecognised_metric_blocks`: bộ
luật phải TỪ CHỐI thứ nó không hiểu, chứ không được lặng lẽ cho qua.
"""

from __future__ import annotations

import uuid

import pytest

from src.services.comparison_rules import (
    ACCEPTED_CLASSES,
    ALL_CLASSES,
    BLOCKING_CLASSES,
    CLASS_ANOMALY,
    CLASS_APPROXIMATION,
    CLASS_CAPABILITY_GAIN,
    CLASS_COVERAGE,
    CLASS_DEFINITION_DRIFT,
    CLASS_UNEXPLAINED,
    VERDICT_ACCEPTED,
    VERDICT_BLOCKED,
    VERDICT_CLEAN,
    VERDICT_NO_DATA,
    classify,
    summarise,
)

PROJECT_ID = uuid.UUID("b4c5d6e7-f8a9-4b7c-8d8e-f0123456ac4b")


def _row(**overrides) -> dict:
    """Một dòng so sánh 'khoẻ mạnh': hai bên đều có dữ liệu, không chênh gì."""
    row = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "legacy_has_data": True,
        "domain_has_data": True,
        "legacy_units_sold": 4,
        "legacy_units_remaining": 6,
        "domain_units_sold": 4,
        "domain_units_remaining": 6,
        "domain_units_reserved": 0,
        "differences": [],
        "anomalies": [],
    }
    row.update(overrides)
    return row


def _diff(metric, legacy, domain, **extra) -> dict:
    delta = None if legacy is None or domain is None else domain - legacy
    return {"metric": metric, "legacy": legacy, "domain": domain, "delta": delta, **extra}


def _classes(verdict) -> list[str]:
    return [d.classification for d in verdict.differences]


# === Tính nhất quán của chính bộ luật =========================================


def test_every_class_is_either_blocking_or_accepted():
    assert BLOCKING_CLASSES | ACCEPTED_CLASSES == set(ALL_CLASSES)
    assert not (BLOCKING_CLASSES & ACCEPTED_CLASSES)


def test_the_blocking_set_is_the_one_that_is_listed_explicitly():
    """Thêm một loại mới mà quên xếp chỗ thì nó phải tự động là BLOCKER.

    `ACCEPTED_CLASSES` được suy ra bằng phép trừ chính vì thế: mặc định phải là
    chặn, không phải tha.
    """
    assert BLOCKING_CLASSES == {CLASS_DEFINITION_DRIFT, CLASS_ANOMALY, CLASS_UNEXPLAINED}


def test_classification_is_deterministic():
    """Cùng đầu vào, cùng đầu ra — không phụ thuộc thứ tự chạy hay trạng thái nào."""
    row = _row(differences=[_diff("units_sold", 4, 9)], domain_units_sold=9)

    first, second = classify(row), classify(row)

    assert first.as_dict() == second.as_dict()


# === coverage =================================================================


def test_no_domain_data_is_coverage_not_drift():
    row = _row(
        domain_has_data=False,
        domain_units_sold=None,
        domain_units_remaining=None,
        domain_units_reserved=None,
        differences=[_diff("units_sold", 4, 0)],
    )

    verdict = classify(row)

    assert _classes(verdict) == [CLASS_COVERAGE]
    assert verdict.verdict == VERDICT_NO_DATA


def test_no_legacy_data_is_coverage():
    row = _row(
        legacy_has_data=False,
        legacy_units_sold=None,
        legacy_units_remaining=None,
        differences=[_diff("units_sold", 0, 4)],
    )

    assert _classes(classify(row)) == [CLASS_COVERAGE]


def test_coverage_wins_over_every_other_rule():
    """Thiếu một bên thì MỌI chênh lệch đều là hệ quả của việc thiếu đó — kể cả
    chênh ở `units_sold`, thứ bình thường là blocker."""
    row = _row(
        domain_has_data=False,
        domain_units_sold=None,
        domain_units_remaining=None,
        domain_units_reserved=None,
        differences=[_diff("units_sold", 4, 0), _diff("units_remaining", 6, 0), _diff("gi_do_la", 1, 2)],
    )

    assert set(_classes(classify(row))) == {CLASS_COVERAGE}
    assert classify(row).blockers == []


# === capability_gain ==========================================================


def test_units_reserved_with_no_legacy_counterpart_is_a_capability_gain():
    """Bộ tính cũ không có khái niệm giữ chỗ — `legacy=None` nghĩa là "không đo
    được", không phải "bằng 0"."""
    row = _row(
        domain_units_reserved=3,
        differences=[_diff("units_reserved", None, 3, note="bộ tính cũ không tính được số căn đang giữ chỗ")],
    )

    verdict = classify(row)

    assert _classes(verdict) == [CLASS_CAPABILITY_GAIN]
    assert verdict.blockers == []
    assert verdict.verdict == VERDICT_ACCEPTED


def test_a_capability_gain_alone_still_counts_as_cutover_evidence():
    row = _row(domain_units_reserved=3, differences=[_diff("units_reserved", None, 3)])

    assert classify(row).is_cutover_evidence is True


def test_units_reserved_with_a_legacy_value_is_not_a_free_pass():
    """Nếu bên cũ CÓ giá trị cho chỉ số này thì lời giải thích "không đo được"
    không còn đúng, và chênh lệch phải bị soi như mọi chênh lệch khác."""
    row = _row(domain_units_reserved=3, differences=[_diff("units_reserved", 1, 3)])

    assert _classes(classify(row)) == [CLASS_UNEXPLAINED]


# === approximation ============================================================


def test_remaining_gap_equal_to_reserved_is_the_known_approximation():
    """Bộ tính miền trừ số căn đang giữ chỗ khỏi tồn kho; bộ cũ thì không."""
    row = _row(domain_units_reserved=2, domain_units_remaining=4, differences=[_diff("units_remaining", 6, 4)])

    verdict = classify(row)

    assert _classes(verdict) == [CLASS_APPROXIMATION]
    assert verdict.blockers == []
    assert "2 căn đang giữ chỗ" in verdict.differences[0].reason


@pytest.mark.parametrize("domain_remaining", [3, 5])
def test_a_remaining_gap_that_is_not_exactly_the_reserved_count_blocks(domain_remaining):
    """Lệch một đơn vị so với con số giữ chỗ là một hiện tượng KHÁC, và nó không
    được mượn lời giải thích này."""
    row = _row(
        domain_units_reserved=2,
        domain_units_remaining=domain_remaining,
        differences=[_diff("units_remaining", 6, domain_remaining)],
    )

    assert _classes(classify(row)) == [CLASS_UNEXPLAINED]


def test_a_remaining_gap_with_no_reservations_at_all_blocks():
    """Không có căn nào đang giữ chỗ thì xấp xỉ giữ chỗ không giải thích được gì."""
    row = _row(domain_units_reserved=0, domain_units_remaining=4, differences=[_diff("units_remaining", 6, 4)])

    assert _classes(classify(row)) == [CLASS_UNEXPLAINED]


def test_a_reversed_remaining_gap_blocks():
    """Bộ tính miền báo tồn kho NHIỀU HƠN bộ cũ — ngược chiều với xấp xỉ giữ chỗ,
    nên đó là chuyện khác."""
    row = _row(domain_units_reserved=2, domain_units_remaining=8, differences=[_diff("units_remaining", 6, 8)])

    assert _classes(classify(row)) == [CLASS_UNEXPLAINED]


# === definition_drift =========================================================


def test_a_units_sold_gap_is_definition_drift_and_blocks():
    row = _row(domain_units_sold=9, differences=[_diff("units_sold", 4, 9)])

    verdict = classify(row)

    assert _classes(verdict) == [CLASS_DEFINITION_DRIFT]
    assert verdict.verdict == VERDICT_BLOCKED
    assert verdict.is_cutover_evidence is False


def test_units_sold_has_zero_tolerance():
    """Một căn đã bán là sự kiện đếm được. Lệch 1 cũng chặn."""
    row = _row(domain_units_sold=5, differences=[_diff("units_sold", 4, 5)])

    assert classify(row).verdict == VERDICT_BLOCKED


# === anomaly ==================================================================


def test_an_anomaly_blocks_even_when_every_metric_agrees():
    """Hai bên khớp số nhưng dữ liệu nguồn tự mâu thuẫn — bất thường không tự khỏi
    khi cắt sang, nó theo sang."""
    row = _row(anomalies=[{"code": "DEAL_ON_DELETED_UNIT", "external_deal_id": "D-1"}])

    verdict = classify(row)

    assert [a.classification for a in verdict.anomalies] == [CLASS_ANOMALY]
    assert verdict.verdict == VERDICT_BLOCKED
    assert verdict.is_cutover_evidence is False


def test_the_anomaly_code_survives_into_the_verdict():
    row = _row(anomalies=[{"code": "HELD_EXCEEDS_STOCK", "area_id": "x"}])

    assert classify(row).anomalies[0].metric == "HELD_EXCEEDS_STOCK"


def test_an_anomaly_without_a_code_still_blocks():
    row = _row(anomalies=[{"khong_co_code": 1}])

    assert classify(row).verdict == VERDICT_BLOCKED


# === unexplained: mặc định phải là CHẶN =======================================


def test_an_unrecognised_metric_blocks():
    """Test quan trọng nhất của file này.

    Bộ luật phải TỪ CHỐI thứ nó không hiểu. Một chỉ số mới được thêm vào bộ so
    sánh mà quên cập nhật luật ở đây sẽ hiện ra thành blocker — chứ không lặng lẽ
    được coi là chấp nhận được.
    """
    row = _row(differences=[_diff("mot_chi_so_moi_toanh", 1, 2)])

    verdict = classify(row)

    assert _classes(verdict) == [CLASS_UNEXPLAINED]
    assert verdict.verdict == VERDICT_BLOCKED


def test_a_mixed_batch_keeps_each_difference_in_its_own_class():
    row = _row(
        domain_units_sold=9,
        domain_units_reserved=2,
        domain_units_remaining=4,
        differences=[
            _diff("units_reserved", None, 2),
            _diff("units_remaining", 6, 4),
            _diff("units_sold", 4, 9),
            _diff("cai_gi_do", 0, 1),
        ],
    )

    verdict = classify(row)

    assert _classes(verdict) == [
        CLASS_CAPABILITY_GAIN,
        CLASS_APPROXIMATION,
        CLASS_DEFINITION_DRIFT,
        CLASS_UNEXPLAINED,
    ]
    assert len(verdict.blockers) == 2


# === Phán quyết chung =========================================================


def test_no_differences_is_clean():
    verdict = classify(_row())

    assert verdict.verdict == VERDICT_CLEAN
    assert verdict.is_cutover_evidence is True


def test_explainable_differences_are_accepted_evidence():
    row = _row(domain_units_reserved=2, domain_units_remaining=4, differences=[_diff("units_remaining", 6, 4)])

    verdict = classify(row)

    assert verdict.verdict == VERDICT_ACCEPTED
    assert verdict.is_cutover_evidence is True


def test_a_vacuous_match_is_never_cutover_evidence():
    """Yêu cầu trung tâm: hai bên cùng bằng 0 vì cùng không có gì — `matches` có
    thể là true, nhưng nó không chứng minh gì."""
    row = _row(
        domain_has_data=False,
        domain_units_sold=None,
        domain_units_remaining=None,
        domain_units_reserved=None,
        legacy_units_sold=0,
        legacy_units_remaining=0,
        differences=[],
    )

    verdict = classify(row)

    assert verdict.differences == []
    assert verdict.verdict == VERDICT_NO_DATA
    assert verdict.is_cutover_evidence is False, "dòng thiếu dữ liệu miền KHÔNG BAO GIỜ là bằng chứng"


def test_a_row_with_no_data_on_either_side_is_not_evidence():
    row = _row(
        legacy_has_data=False,
        domain_has_data=False,
        legacy_units_sold=None,
        legacy_units_remaining=None,
        domain_units_sold=None,
        domain_units_remaining=None,
        domain_units_reserved=None,
    )

    assert classify(row).is_cutover_evidence is False


def test_the_verdict_explains_itself():
    """Phán quyết phải nói ĐƯỢC VÌ SAO, nếu không nó chỉ là một chữ để tranh cãi."""
    verdict = classify(
        _row(domain_has_data=False, domain_units_sold=None, domain_units_remaining=None, domain_units_reserved=None)
    )

    assert any("KHÔNG CÓ dữ liệu" in reason for reason in verdict.reasons)


def test_counts_by_class_covers_every_class():
    verdict = classify(_row(differences=[_diff("units_sold", 4, 9)], domain_units_sold=9))

    assert set(verdict.counts_by_class()) == set(ALL_CLASSES)
    assert verdict.counts_by_class()[CLASS_DEFINITION_DRIFT] == 1


# === Tổng hợp nhiều lần đo ====================================================


def test_summarise_counts_evidence_not_matches():
    """`cutover_evidence_count` khác `matches` đúng ở chỗ nguy hiểm nhất."""
    verdicts = [
        classify(_row()),  # sạch, có dữ liệu -> bằng chứng
        classify(
            _row(
                domain_has_data=False,
                domain_units_sold=None,
                domain_units_remaining=None,
                domain_units_reserved=None,
            )
        ),  # "khớp" rỗng tuếch -> KHÔNG phải bằng chứng
        classify(_row(domain_units_sold=9, differences=[_diff("units_sold", 4, 9)])),  # chặn
    ]

    summary = summarise(verdicts)

    assert summary["comparisons"] == 3
    assert summary["cutover_evidence_count"] == 1
    assert summary["blocked_count"] == 1
    assert summary["no_data_count"] == 1


def test_summarise_reports_which_classes_block():
    summary = summarise([classify(_row())])

    assert set(summary["blocking_classes"]) == BLOCKING_CLASSES


def test_summarise_of_nothing_is_not_evidence_of_anything():
    summary = summarise([])

    assert summary["comparisons"] == 0
    assert summary["cutover_evidence_count"] == 0
