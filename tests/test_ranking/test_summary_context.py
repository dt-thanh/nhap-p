"""`_build_summary_context` (src/ranking/service.py) — hàm THUẦN, không cần DB:
nhận `UnitScore` dựng tay, chỉ định dạng chuỗi. Bổ sung sau khi cổng
`band_for`/`as_percent`/`DISCLAIMER` từ `feature/NguyenDucDat/ranking-engine`
(xem `pipeline_status.md` đợt hoà giải 2026-08-15).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from src.ranking.engine import UnitScore
from src.ranking.service import _build_summary_context

PROJECT_ID = uuid.uuid4()
AREA_ID = str(uuid.uuid4())


def _score(unit_id: str, score: Decimal, rank_in_project: int, rank_in_area: int) -> UnitScore:
    return UnitScore(
        unit_id=unit_id,
        area_id=AREA_ID,
        score=score,
        coverage=Decimal("1"),
        contributions={},
        skipped=False,
        skip_reason=None,
        rank_in_project=rank_in_project,
        rank_in_area=rank_in_area,
    )


def test_summary_includes_band_label_and_percent_per_unit():
    ranked = [_score("u1", Decimal("0.9000"), 1, 1), _score("u2", Decimal("0.2000"), 2, 2)]
    text = _build_summary_context(PROJECT_ID, None, ranked, config_version=1)
    assert "mức=high (90.0%)" in text
    assert "mức=low (20.0%)" in text


def test_summary_always_appends_the_disclaimer():
    from src.ranking.bands import DISCLAIMER

    text = _build_summary_context(PROJECT_ID, None, [], config_version=1)
    assert DISCLAIMER in text


def test_summary_with_no_ranked_units_still_has_disclaimer_and_placeholder():
    text = _build_summary_context(PROJECT_ID, None, [], config_version=1)
    assert "không có căn nào đạt ngưỡng coverage" in text
