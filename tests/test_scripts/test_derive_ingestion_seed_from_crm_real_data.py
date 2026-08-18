"""Test thuần cho `scripts/derive_ingestion_seed_from_crm_real_data.py` — kiểm
cục bộ (`_validate`) và hai bản dựng đầu ra (`_build_ingestion_seed`,
`_build_ranking_fixture`). Không cần DB, không cần file `crm_real_data.json`
thật — dùng một bộ dữ liệu tối giản, đúng hình dạng."""

from __future__ import annotations

import copy

import pytest

from scripts.derive_ingestion_seed_from_crm_real_data import (
    DerivationError,
    _build_ingestion_seed,
    _build_ranking_fixture,
    _validate,
)

VALID = {
    "scoring_meta": {"method": "simulated_placeholder", "note": "not real"},
    "projects": [{"id": "prj_a", "name": "Project A", "location": "Hà Nội", "status": "selling", "total_units": 10, "zone_count": 1, "sold_pct": 60.0}],
    "zones": [{"id": "zn_1", "name": "Zone 1", "project_id": "prj_a", "status": "selling", "total_units": 10, "units_remaining": 4, "data_source": "real", "source": "x"}],
    "areas": [
        {
            "id": "ar_1",
            "project_id": "prj_a",
            "zone_id": "zn_1",
            "area_name": "Zone 1 - Studio",
            "unit_type": "Studio",
            "bedrooms": 0,
            "area_sqm": 30,
            "total_units": 10,
            "units_remaining": 4,
            "data_source": "real",
        }
    ],
    "dash_areas": [
        {"id": "ar_1", "name": "Zone 1 - Studio", "total_units": 10, "sold": 6, "remaining": 4, "absorption_rate": 0.6, "status": "on_track", "latest_data": "2026-05-01", "velocity": None}
    ],
    "dash_trend_by_area": {"ar_1": [{"date": "2026-05-01", "units_sold": 2, "cumulative_sold": 6, "absorption_rate": 0.6}]},
    "ranking_by_area": {
        "ar_1": [
            {
                "unit_id": "ar_1_u001",
                "unit_code": "S-01",
                "unit_type": "Studio",
                "area_sqm": 30,
                "status": "available",
                "scored": True,
                "score": 70,
                "score_raw": 0.7,
                "band": "B",
                "rank": 1,
                "contributions": {"a": 0.5},
            }
        ]
    },
    "files": [{"id": "f1", "filename": "x.csv", "status": "completed", "rows_ok": 1, "rows_failed": 0, "uploaded_at": "2026-05-01T00:00:00Z"}],
    "sample_errors": [{"id": 1, "file_id": "f1", "row_number": 1, "column_name": "x", "error_code": "E", "message": "m"}],
}


def test_valid_data_passes():
    _validate(copy.deepcopy(VALID))  # should not raise


def test_duplicate_area_id_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["areas"].append(dict(bad["areas"][0]))
    with pytest.raises(DerivationError, match="trùng lặp"):
        _validate(bad)


def test_area_with_unknown_project_id_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["areas"][0]["project_id"] = "no-such-project"
    with pytest.raises(DerivationError, match="project_id"):
        _validate(bad)


def test_area_missing_required_field_is_rejected():
    bad = copy.deepcopy(VALID)
    del bad["areas"][0]["bedrooms"]
    with pytest.raises(DerivationError, match="bedrooms"):
        _validate(bad)


def test_total_units_less_than_remaining_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["areas"][0]["units_remaining"] = 999
    with pytest.raises(DerivationError, match="total_units"):
        _validate(bad)


def test_sold_plus_remaining_not_equal_total_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["dash_areas"][0]["remaining"] = 999
    with pytest.raises(DerivationError, match="sold"):
        _validate(bad)


def test_dash_trend_referencing_unknown_area_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["dash_trend_by_area"]["ar_ghost"] = []
    with pytest.raises(DerivationError, match="dash_trend_by_area"):
        _validate(bad)


def test_ranking_by_area_referencing_unknown_area_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["ranking_by_area"]["ar_ghost"] = []
    with pytest.raises(DerivationError, match="ranking_by_area"):
        _validate(bad)


def test_duplicate_unit_code_within_same_area_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["ranking_by_area"]["ar_1"].append(dict(bad["ranking_by_area"]["ar_1"][0]))
    bad["ranking_by_area"]["ar_1"][1]["unit_id"] = "ar_1_u002"  # unit_id khác, unit_code TRÙNG
    with pytest.raises(DerivationError, match="unit_code trùng"):
        _validate(bad)


def test_duplicate_unit_id_across_areas_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["areas"].append({**bad["areas"][0], "id": "ar_2"})
    bad["dash_areas"].append({**bad["dash_areas"][0], "id": "ar_2"})
    bad["ranking_by_area"]["ar_2"] = [dict(bad["ranking_by_area"]["ar_1"][0])]  # unit_id trùng ar_1_u001
    with pytest.raises(DerivationError, match="trùng lặp TOÀN CỤC"):
        _validate(bad)


def test_unknown_unit_status_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["ranking_by_area"]["ar_1"][0]["status"] = "not_a_real_status"
    with pytest.raises(DerivationError, match="UNIT_STATUSES"):
        _validate(bad)


def test_sample_error_referencing_unknown_file_is_rejected():
    bad = copy.deepcopy(VALID)
    bad["sample_errors"][0]["file_id"] = "no-such-file"
    with pytest.raises(DerivationError, match="file_id"):
        _validate(bad)


# --- output shape ------------------------------------------------------------


def test_build_ingestion_seed_joins_area_fields_and_documents_exclusions():
    out = _build_ingestion_seed(copy.deepcopy(VALID), source_path=None)
    area = out["dash_areas"][0]
    assert area["unit_type"] == "Studio"
    assert area["sold"] == 6 and area["remaining"] == 4
    assert "zones" in out["_meta"]["excluded"]
    assert "deals" in out["_meta"]["excluded"]
    assert out["_meta"]["counts"]["units"] == 1


def test_build_ingestion_seed_units_carry_only_supported_columns():
    out = _build_ingestion_seed(copy.deepcopy(VALID), source_path=None)
    unit = out["units"][0]
    assert set(unit.keys()) == {"id", "area_id", "unit_code", "unit_type", "status"}
    assert "score" not in unit and "contributions" not in unit


def test_build_ranking_fixture_is_labeled_simulated_and_carries_score_fields():
    out = _build_ranking_fixture(copy.deepcopy(VALID))
    assert out["provenance"] == "simulated_fixture"
    assert "not" in out["warning"].lower() or "NOT" in out["warning"]
    unit = out["units"][0]
    assert unit["external_unit_id"] == "ar_1_u001"
    assert unit["score"] == 70
    assert unit["contributions"] == {"a": 0.5}
