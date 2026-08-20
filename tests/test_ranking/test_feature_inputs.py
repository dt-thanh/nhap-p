"""`_build_feature_inputs` — hàm THUẦN, không cần DB.

Đợt hiệu chỉnh v2 sửa ba thứ ở tầng dựng đặc trưng, và cả ba đều là loại lỗi
KHÔNG làm test nào đỏ, không ném ngoại lệ, không ghi log — chúng chỉ âm thầm
cho ra một con số sai. File này giữ chúng lại:

1. Phân khu KHÔNG có deal nào phải để MISSING (`None`), để `engine.py` áp
   `missing_value_policy = "neutral"` mà config khai báo. Bản trước điền sẵn
   `Decimal("0")`, biến "chưa biết" thành "bán tệ nhất có thể".
2. `unit_demand_norm` phải bão hoà ở `DEMAND_SATURATION`, không vượt 1.0 —
   `feature_snapshots.feature_value` có CHECK `>= 0 AND <= 1`.
3. `has_active_deal` vẫn phải được tính dù config v2 không dùng: `ranking_configs`
   là bảng CHỈ-THÊM có thể rollback về v1, và ngừng tính khoá này sẽ biến một
   lần rollback hợp lệ thành "đặc trưng MISSING".
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from src.ranking.service import DEMAND_SATURATION, _build_feature_inputs

AREA_WITH_DEALS = uuid.uuid4()
AREA_WITHOUT_DEALS = uuid.uuid4()

AREA_FEATURES = {
    AREA_WITH_DEALS: {
        "area_velocity_norm": Decimal("0.4"),
        "area_conversion_norm": Decimal("0.25"),
    }
}


def _unit(unit_id: uuid.UUID, area_id: uuid.UUID, status: str = "available") -> dict:
    return {"id": unit_id, "area_id": area_id, "status": status, "created_at": "2026-01-01"}


def test_area_without_any_deal_leaves_area_features_missing():
    """Giá trị `None` là cách DUY NHẤT để engine áp `neutral`. Điền 0 ở đây sẽ
    chấm một phân khu vừa đồng bộ như phân khu bán tệ nhất dự án."""
    unit_id = uuid.uuid4()
    [got] = _build_feature_inputs([_unit(unit_id, AREA_WITHOUT_DEALS)], AREA_FEATURES, set(), {})

    assert got.values["area_velocity_norm"] is None
    assert got.values["area_conversion_norm"] is None


def test_area_with_deals_passes_its_computed_values_through():
    unit_id = uuid.uuid4()
    [got] = _build_feature_inputs([_unit(unit_id, AREA_WITH_DEALS)], AREA_FEATURES, set(), {})

    assert got.values["area_velocity_norm"] == Decimal("0.4")
    assert got.values["area_conversion_norm"] == Decimal("0.25")


def test_unit_without_funnel_deals_has_zero_demand_not_missing():
    """"Không ai đang quan tâm" là một sự thật ĐO ĐƯỢC, khác hẳn "chưa biết" —
    nên nó là 0, không phải None."""
    unit_id = uuid.uuid4()
    [got] = _build_feature_inputs([_unit(unit_id, AREA_WITH_DEALS)], AREA_FEATURES, set(), {})

    assert got.values["unit_demand_norm"] == Decimal("0")


def test_demand_scales_linearly_below_saturation():
    unit_id = uuid.uuid4()
    [got] = _build_feature_inputs([_unit(unit_id, AREA_WITH_DEALS)], AREA_FEATURES, set(), {unit_id: 1})

    assert got.values["unit_demand_norm"] == Decimal("1") / DEMAND_SATURATION


def test_demand_saturates_at_one_and_never_exceeds_it():
    """`feature_snapshots.feature_value` có CHECK `>= 0 AND <= 1`: một căn đông
    khách bất thường phải bão hoà, không được làm migration ném IntegrityError."""
    unit_id = uuid.uuid4()
    for funnel_count in (int(DEMAND_SATURATION), int(DEMAND_SATURATION) + 5, 99):
        [got] = _build_feature_inputs(
            [_unit(unit_id, AREA_WITH_DEALS)], AREA_FEATURES, set(), {unit_id: funnel_count}
        )
        assert got.values["unit_demand_norm"] == Decimal("1")


def test_has_active_deal_is_still_computed_for_rollback_to_v1():
    """v2 không dùng khoá này, nhưng `ranking_configs` là CHỈ-THÊM và rollback
    về trọng số v1 là một thao tác hợp lệ."""
    held_id, free_id = uuid.uuid4(), uuid.uuid4()
    rows = [_unit(held_id, AREA_WITH_DEALS, "reserved"), _unit(free_id, AREA_WITH_DEALS)]

    held, free = _build_feature_inputs(rows, AREA_FEATURES, {held_id}, {})

    assert held.values["has_active_deal"] == Decimal("1")
    assert free.values["has_active_deal"] == Decimal("0")


def test_unit_available_is_one_only_for_available_status():
    ids = {status: uuid.uuid4() for status in ("available", "reserved", "sold", "blocked")}
    rows = [_unit(uid, AREA_WITH_DEALS, status) for status, uid in ids.items()]

    got = {i.unit_id: i.values["unit_available"] for i in _build_feature_inputs(rows, AREA_FEATURES, set(), {})}

    assert got[str(ids["available"])] == Decimal("1")
    assert got[str(ids["reserved"])] == Decimal("0")
    assert got[str(ids["sold"])] == Decimal("0")
    assert got[str(ids["blocked"])] == Decimal("0")
