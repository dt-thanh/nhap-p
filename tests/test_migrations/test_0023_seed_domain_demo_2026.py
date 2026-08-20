"""Static, database-free checks for the synthetic 2026 Alembic revision."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).parents[2] / "alembic" / "versions" / "0023_seed_domain_demo_2026.py"
)
spec = importlib.util.spec_from_file_location("migration_0023_seed_domain_demo_2026", MIGRATION_PATH)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)


def test_revision_is_a_single_child_of_verified_head():
    assert migration.revision == "0023_seed_domain_demo_2026"
    assert migration.down_revision == "0022_ranking_config_v2"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_plan_has_deterministic_domain_counts_and_namespace():
    first = migration._plan_rows()
    second = migration._plan_rows()
    assert first == second
    assert {key: len(rows) for key, rows in first.items()} == {
        "projects": 4,
        "areas": 12,
        "units": 1062,
        "deals": 630,
    }
    all_rows = [row for rows in first.values() for row in rows]
    assert {row["source_system"] for row in all_rows} == {migration.SOURCE_SYSTEM}
    assert {row["source_instance_id"] for row in all_rows} == {migration.SOURCE_INSTANCE_ID}
    assert {row["absorption_calculator"] for row in first["projects"]} == {
        migration.DOMAIN_CALCULATOR
    }


def test_plan_respects_domain_status_and_date_invariants():
    rows = migration._plan_rows()
    units_by_id = {row["id"]: row for row in rows["units"]}
    holding_units = [
        row["unit_id"] for row in rows["deals"] if row["status"] in {"reserved", "sold"}
    ]
    assert len(holding_units) == len(set(holding_units))
    assert all(
        row["status"] in {"available", "reserved", "sold", "blocked"}
        for row in rows["units"]
    )
    assert all(
        row["status"] in {"lead", "qualified", "interested", "viewing", "reserved", "sold", "lost"}
        for row in rows["deals"]
    )
    assert all(row["unit_id"] in units_by_id for row in rows["deals"])
    assert all(
        row["sold_at"] is not None and row["sold_at"].year == 2026
        for row in rows["deals"]
        if row["status"] == "sold"
    )
    assert all(
        row["reserved_at"] is not None
        for row in rows["deals"]
        if row["status"] == "reserved"
    )
    assert all(
        row["lost_at"] is not None for row in rows["deals"] if row["status"] == "lost"
    )
    assert all(
        row["updated_at"].date() <= migration.DEMO_AS_OF_DATE for row in rows["deals"]
    )


def test_every_area_has_sellable_and_sold_inventory():
    rows = migration._plan_rows()
    units_by_area: dict[object, list[dict]] = {}
    for row in rows["units"]:
        units_by_area.setdefault(row["area_id"], []).append(row)
    sold_unit_ids = {row["unit_id"] for row in rows["deals"] if row["status"] == "sold"}
    deal_counts = Counter(row["status"] for row in rows["deals"])
    assert deal_counts["sold"] == 423
    assert deal_counts["reserved"] == 64
    assert deal_counts["lost"] == 35
    assert sum(deal_counts[status] for status in {"lead", "qualified", "interested", "viewing"}) == 108
    for area_units in units_by_area.values():
        sellable = [row for row in area_units if row["status"] != "blocked"]
        sold = [row for row in sellable if row["id"] in sold_unit_ids]
        assert sellable
        assert sold
        assert len(sold) <= len(sellable)


def test_target_gate_fails_closed_for_production_and_non_test_database(monkeypatch):
    class FakeResult:
        def scalar_one(self):
            return "synthetic_demo_test"

    class FakeBind:
        def execute(self, _statement):
            return FakeResult()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SEED_ENVIRONMENT", "test")
    with pytest.raises(RuntimeError, match="production-like"):
        migration._assert_safe_target(FakeBind())

    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(RuntimeError, match="end with _test"):
        migration._assert_safe_target(
            type(
                "NonTestBind",
                (),
                {
                    "execute": lambda _self, _statement: type(
                        "R", (), {"scalar_one": lambda _self: "demo"}
                    )()
                },
            )()
        )
