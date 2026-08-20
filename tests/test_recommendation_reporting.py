from types import SimpleNamespace

from src.api.agent import _build_business_summary, _proposal_quality


def _item(score: str, *, area: str = 'Tower A', coverage: str = '1.0000') -> dict:
    return {'score': score, 'area_name': area, 'signal_coverage': coverage}


def test_quality_is_not_a_fixed_85_percent_and_detects_indistinguishable_scores():
    risk, coverage, top_ties = _proposal_quality([_item('0.7000'), _item('0.7000')])

    assert risk == 'high'
    assert coverage == 1.0
    assert top_ties == 2


def test_quality_uses_resolved_signal_coverage_and_score_separation():
    risk, coverage, top_ties = _proposal_quality(
        [_item('0.8400', coverage='0.9000'), _item('0.5900', coverage='0.7000')]
    )

    assert risk == 'low'
    assert coverage == 0.8
    assert top_ties == 1


def test_business_summary_separates_facts_cautions_and_plan():
    summary = _build_business_summary(
        project_name='Ocean Park 1',
        absorption=SimpleNamespace(units_sold=18_314, units_remaining=2_786),
        status_counts={'available': 342, 'reserved': 152, 'sold': 96},
        config_version=2,
        selected=[_item('0.8400'), _item('0.5900')],
        top_ties=1,
    )

    assert '## Thực trạng hiện tại' in summary
    assert '## Điểm đáng lưu ý' in summary
    assert '## Kế hoạch đề xuất' in summary
    assert '18314 căn đã bán' in summary
    assert '2786 căn còn lại' in summary
    assert 'không được dùng riêng để kết luận nhu cầu cao hay thấp' in summary
    assert 'không phải xác suất bán, doanh thu hay biên lợi nhuận' in summary
    assert 'Mọi thay đổi chính sách giá/ưu đãi' in summary
