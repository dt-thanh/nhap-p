"""Hàm THUẦN `src/ranking/bands.py` — không cần DB.

Cổng vào từ `feature/NguyenDucDat/ranking-engine` (xem `pipeline_status.md`
đợt hoà giải 2026-08-15) — chỉ lớp trình bày (band/percent), không schema.
"""

from __future__ import annotations

from decimal import Decimal

from src.ranking.bands import DISCLAIMER, as_percent, band_for


def test_none_score_has_no_band():
    assert band_for(None) is None


def test_score_at_or_above_high_threshold_is_high():
    assert band_for(Decimal("0.66")) == "high"
    assert band_for(Decimal("1.0000")) == "high"


def test_score_at_or_above_medium_threshold_is_medium():
    assert band_for(Decimal("0.33")) == "medium"
    assert band_for(Decimal("0.6599")) == "medium"


def test_score_below_medium_threshold_is_low():
    assert band_for(Decimal("0")) == "low"
    assert band_for(Decimal("0.3299")) == "low"


def test_none_score_has_no_percent():
    assert as_percent(None) is None


def test_percent_conversion_rounds_to_one_decimal():
    assert as_percent(Decimal("0.6666")) == 66.7
    assert as_percent(Decimal("1.0000")) == 100.0
    assert as_percent(Decimal("0")) == 0.0


def test_disclaimer_is_a_nonempty_fixed_string():
    assert isinstance(DISCLAIMER, str)
    assert DISCLAIMER.strip()
