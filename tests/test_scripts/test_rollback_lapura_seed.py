from __future__ import annotations

import pytest

from scripts.lapura_manifest import apply_real_ids
from scripts.rollback_lapura_seed import RollbackError, build_plan


def _manifest_pass1():
    return {
        "batch_id": "b1",
        "pass": 1,
        "entities": [
            {"kind": "project", "source_row_key": "P-0001", "fixture_external_key": "prj-la-pura"},
            {"kind": "area", "source_row_key": "A-0001", "fixture_external_key": "area-la-pura-a-0001"},
            {"kind": "unit", "source_row_key": "U-0001", "fixture_external_key": "unit-la-pura-u-0001"},
            {"kind": "deal", "source_row_key": "D-0001", "fixture_external_key": "deal-la-pura-d-0001"},
        ],
    }


def test_build_plan_refuses_on_pass_1_manifest():
    with pytest.raises(RollbackError, match="not Pass-2 complete"):
        build_plan(_manifest_pass1())


def test_build_plan_orders_children_before_parents_and_scopes_ids():
    manifest = apply_real_ids(
        _manifest_pass1(),
        {
            "prj-la-pura": {"real_external_id": "P-0099", "real_id": "p-uuid"},
            "area-la-pura-a-0001": {"real_external_id": "A-0099", "real_id": "a-uuid"},
            "unit-la-pura-u-0001": {"real_external_id": "U-0099", "real_id": "u-uuid"},
            "deal-la-pura-d-0001": {"real_external_id": "D-0099", "real_id": "d-uuid"},
        },
    )
    plan = build_plan(manifest)
    tables_in_order = [step["table"] for step in plan]
    assert tables_in_order == ["unit_enrichment_attributes", "deals", "units", "areas", "projects"]

    by_table = {step["table"]: step for step in plan}
    assert by_table["projects"]["ids"] == ["p-uuid"]
    assert by_table["areas"]["ids"] == ["a-uuid"]
    assert by_table["units"]["ids"] == ["u-uuid"]
    assert by_table["deals"]["ids"] == ["d-uuid"]
    # unit_enrichment_attributes is scoped by unit_id, not its own id
    assert by_table["unit_enrichment_attributes"]["column"] == "unit_id"
    assert by_table["unit_enrichment_attributes"]["ids"] == ["u-uuid"]
