from __future__ import annotations

import pytest

from scripts.lapura_manifest import ManifestError, apply_real_ids, is_pass_2_complete, real_id_by_fixture_key


def _manifest():
    return {
        "batch_id": "b1",
        "pass": 1,
        "entities": [
            {"kind": "project", "source_row_key": "P-0001", "fixture_external_key": "prj-la-pura"},
            {"kind": "unit", "source_row_key": "U-0001", "fixture_external_key": "unit-la-pura-u-0001"},
        ],
    }


def test_apply_real_ids_fills_every_entity():
    m = apply_real_ids(
        _manifest(),
        {
            "prj-la-pura": {"real_external_id": "P-0099", "real_id": "uuid-project"},
            "unit-la-pura-u-0001": {"real_external_id": "U-0099", "real_id": "uuid-unit"},
        },
    )
    assert m["pass"] == 2
    assert m["entities"][0]["real_id"] == "uuid-project"
    assert m["entities"][1]["real_external_id"] == "U-0099"
    assert is_pass_2_complete(m)


def test_apply_real_ids_refuses_partial_fill():
    with pytest.raises(ManifestError, match="missing real ids"):
        apply_real_ids(_manifest(), {"prj-la-pura": {"real_external_id": "P-0099", "real_id": "uuid-project"}})


def test_is_pass_2_complete_false_for_pass_1():
    assert is_pass_2_complete(_manifest()) is False


def test_real_id_by_fixture_key_filters_by_kind():
    m = apply_real_ids(
        _manifest(),
        {
            "prj-la-pura": {"real_external_id": "P-0099", "real_id": "uuid-project"},
            "unit-la-pura-u-0001": {"real_external_id": "U-0099", "real_id": "uuid-unit"},
        },
    )
    assert real_id_by_fixture_key(m, "unit") == {"unit-la-pura-u-0001": "uuid-unit"}
    assert real_id_by_fixture_key(m, "project") == {"prj-la-pura": "uuid-project"}
    assert real_id_by_fixture_key(m, "deal") == {}
