"""Pure tests for the synthetic 2026 domain seed; no database connection."""

from datetime import date

import pytest

from scripts.seed_domain_demo_2026 import (
    AREAS,
    DOMAIN_CALCULATOR,
    SOURCE_INSTANCE_ID,
    SOURCE_SYSTEM,
    SeedConfig,
    _target_metadata,
    build_plan,
)

CONFIG = SeedConfig(date(2026, 8, 16), date(2025, 8, 16), date(2026, 8, 16))


def test_plan_is_deterministic_for_the_same_configuration():
    first = build_plan(CONFIG)
    second = build_plan(CONFIG)
    assert first.projects == second.projects
    assert first.areas == second.areas
    assert first.units == second.units
    assert first.deals == second.deals


def test_every_area_has_2026_domain_sales_and_valid_inventory():
    plan = build_plan(CONFIG)
    assert {row["external_id"] for row in plan.areas} == {spec.external_id for spec in AREAS}
    units_by_area = {}
    for row in plan.units:
        units_by_area.setdefault(row["area_id"], []).append(row)
    sold_by_unit = {row["unit_id"] for row in plan.deals if row["status"] == "sold"}
    assert sold_by_unit
    for area in plan.areas:
        area_units = units_by_area[area["id"]]
        sellable = sum(row["status"] != "blocked" for row in area_units)
        sold = sum(row["id"] in sold_by_unit for row in area_units)
        assert sold <= sellable
        assert sold > 0
    assert all(row["sold_at"].year == 2026 for row in plan.deals if row["status"] == "sold")
    assert all(row["updated_at"].date() <= CONFIG.as_of_date for row in plan.deals)


def test_each_unit_has_at_most_one_live_holding_deal():
    plan = build_plan(CONFIG)
    holding = [(row["unit_id"], row["status"]) for row in plan.deals if row["status"] in {"reserved", "sold"}]
    assert len(holding) == len({unit_id for unit_id, _status in holding})


def test_seed_rows_are_domain_owned_and_select_domain_calculator():
    plan = build_plan(CONFIG)
    assert {row["source_system"] for row in plan.projects + plan.areas + plan.units + plan.deals} == {SOURCE_SYSTEM}
    assert {row["source_instance_id"] for row in plan.projects + plan.areas + plan.units + plan.deals} == {SOURCE_INSTANCE_ID}
    assert {row["absorption_calculator"] for row in plan.projects} == {DOMAIN_CALCULATOR}


def test_area_filter_keeps_only_its_parent_project():
    plan = build_plan(CONFIG, area_filter=AREAS[0].external_id)
    assert len(plan.areas) == 1
    assert len(plan.projects) == 1
    assert plan.areas[0]["project_id"] == plan.projects[0]["id"]


def test_target_gate_rejects_unclassified_and_production_like_targets():
    with pytest.raises(RuntimeError, match="explicitly"):
        _target_metadata("postgresql://app:secret@localhost:5432/demo")
    with pytest.raises(RuntimeError, match="production-like"):
        _target_metadata("postgresql://app:secret@localhost:5432/absorption_prod", classification="development")


def test_test_classification_requires_test_suffix():
    import os

    previous = os.environ.get("SEED_ENVIRONMENT")
    os.environ["SEED_ENVIRONMENT"] = "test"
    try:
        with pytest.raises(RuntimeError, match="_test"):
            _target_metadata("postgresql://app:secret@localhost:5432/demo")
    finally:
        if previous is None:
            os.environ.pop("SEED_ENVIRONMENT", None)
        else:
            os.environ["SEED_ENVIRONMENT"] = previous


def test_production_app_environment_cannot_be_overridden_by_seed_classification():
    with pytest.raises(RuntimeError, match="APP_ENV"):
        _target_metadata(
            "postgresql://app:secret@localhost:5432/synthetic_demo_test",
            classification="test",
            app_environment="production",
        )
