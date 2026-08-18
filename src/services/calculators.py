"""Tên hai bộ tính, ở MỘT chỗ duy nhất.

Tách khỏi `domain_absorption.py` để `absorption.py` (bộ tính cũ) dùng được mà
không phải import module của bộ tính mới — hai bộ tính không được biết về nhau,
đó chính là điều kiện để chúng sống chung mà không can thiệp lẫn nhau.

Giá trị phải khớp `ck_absorption_daily_calculator` và
`ck_projects_absorption_calculator` của migration 0012.
"""

from __future__ import annotations

from typing import Final

CALCULATOR_LEGACY: Final = "legacy_aggregate"
CALCULATOR_DOMAIN: Final = "domain_units_deals"

CALCULATORS: Final = (CALCULATOR_LEGACY, CALCULATOR_DOMAIN)

# Bộ tính mà dashboard đọc khi dự án không khai gì khác. Phase 6 KHÔNG cắt sang:
# mọi dự án vẫn ở đây.
DEFAULT_CALCULATOR: Final = CALCULATOR_LEGACY
