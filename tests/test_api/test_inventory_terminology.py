"""Contract tests for the explicit remaining-inventory terminology."""

from src.models.schemas import AbsorptionSummaryOut, InventoryAreaOut


def test_summary_distinguishes_total_remaining_from_immediately_available() -> None:
    summary = AbsorptionSummaryOut(
        total_units=268,
        units_sold=156,
        units_remaining=112,
        units_reserved=15,
        available_remaining_units=97,
    )

    assert summary.units_remaining == 112
    assert summary.available_remaining_units == 97
    assert summary.units_reserved == 15
    assert summary.units_remaining == summary.available_remaining_units + summary.units_reserved


def test_legacy_summary_keeps_unavailable_breakdown_explicitly_null() -> None:
    summary = AbsorptionSummaryOut(total_units=100, units_sold=40, units_remaining=60)

    assert summary.units_reserved is None
    assert summary.available_remaining_units is None


def test_inventory_alias_preserves_existing_available_units_semantics() -> None:
    area = InventoryAreaOut(
        area_id="area-1",
        area_name="North Tower",
        unit_type="2PN",
        total_units=268,
        units_sold=156,
        units_reserved=15,
        units_remaining=97,
        available_remaining_units=97,
        units_blocked=8,
    )

    assert area.available_remaining_units == area.units_remaining
    assert area.total_units - area.units_sold - area.units_reserved == area.available_remaining_units
