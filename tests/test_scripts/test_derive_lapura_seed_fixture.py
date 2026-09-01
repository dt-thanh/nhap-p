"""Unit tests for scripts/derive_lapura_seed_fixture.py — pure functions over
the real, committed source CSVs (`data/Real_estate/data/`), no DB/HTTP.
"""

from __future__ import annotations

import copy

import pytest

from scripts.derive_lapura_seed_fixture import (
    FixtureBuildError,
    build_fixture,
    build_manifest,
    load_source,
    validate,
)


@pytest.fixture(scope="module")
def data():
    data, _ = load_source()
    return data


@pytest.fixture(scope="module")
def fixture(data):
    return build_fixture(data)


def test_validate_reports_no_problems_on_the_real_source(data):
    assert validate(data) == []


def test_fixture_counts_match_the_known_good_spec(fixture):
    assert len(fixture["projects"]) == 1
    assert len(fixture["areas"]) == 24
    assert len(fixture["units"]) == 392
    assert len(fixture["deals"]) == 193
    assert len(fixture["unit_enrichment"]) == 392
    sold = [d for d in fixture["deals"] if d["deal_status"] == "sold"]
    reserved = [d for d in fixture["deals"] if d["deal_status"] == "reserved"]
    assert len(sold) == 130
    assert len(reserved) == 63
    available = [u for u in fixture["units"] if u["unit_status"] == "available"]
    assert len(available) == 199


def test_all_external_keys_are_namespaced_and_unique(fixture):
    all_keys = (
        [p["external_key"] for p in fixture["projects"]]
        + [a["external_key"] for a in fixture["areas"]]
        + [u["external_key"] for u in fixture["units"]]
        + [d["external_key"] for d in fixture["deals"]]
    )
    assert len(all_keys) == len(set(all_keys)), "duplicate external_key within the fixture"
    assert all(k.startswith(("prj-la-pura", "area-la-pura", "unit-la-pura", "deal-la-pura")) for k in all_keys)


def test_every_area_references_the_one_project(fixture):
    project_key = fixture["projects"][0]["external_key"]
    assert all(a["project_external_key"] == project_key for a in fixture["areas"])


def test_every_unit_references_a_declared_area(fixture):
    area_keys = {a["external_key"] for a in fixture["areas"]}
    assert all(u["area_external_key"] in area_keys for u in fixture["units"])


def test_every_deal_references_a_declared_unit(fixture):
    unit_keys = {u["external_key"] for u in fixture["units"]}
    assert all(d["unit_external_key"] in unit_keys for d in fixture["deals"])


def test_every_enrichment_row_references_a_declared_unit(fixture):
    unit_keys = {u["external_key"] for u in fixture["units"]}
    assert all(e["unit_external_key"] in unit_keys for e in fixture["unit_enrichment"])


def test_enrichment_rows_are_flagged_synthetic(fixture):
    # This dataset's own README states every row is `data_profile='demo'` and
    # every physical feature is synthetic — the fixture must carry that
    # forward, never silently drop it.
    assert all(e["is_synthetic"] is True for e in fixture["unit_enrichment"])


def test_validate_catches_an_injected_fk_break(data):
    broken = copy.deepcopy(data)
    areas_rows, sha = broken["crm_areas_import.csv"]
    areas_rows[0] = {**areas_rows[0], "project_id": "does-not-exist"}
    broken["crm_areas_import.csv"] = (areas_rows, sha)
    problems = validate(broken)
    assert any("project_id" in p for p in problems)


def test_validate_catches_a_duplicate_unit_code(data):
    broken = copy.deepcopy(data)
    units_rows, sha = broken["crm_units_import.csv"]
    units_rows[1] = {**units_rows[1], "unit_code": units_rows[0]["unit_code"]}
    broken["crm_units_import.csv"] = (units_rows, sha)
    problems = validate(broken)
    assert any("duplicate unit_code" in p for p in problems)


def test_manifest_records_one_entity_per_fixture_row(data, fixture):
    manifest = build_manifest(data, fixture, batch_id="test-batch")
    assert manifest["batch_id"] == "test-batch"
    assert manifest["counts"] == {
        "projects": 1,
        "areas": 24,
        "units": 392,
        "deals": 193,
        "unit_enrichment": 392,
    }
    assert len(manifest["entities"]) == 1 + 24 + 392 + 193
    assert manifest["real_ids"] is None
    assert len(manifest["source_files"]) == 6


def test_load_source_missing_file_raises(tmp_path, monkeypatch):
    import scripts.derive_lapura_seed_fixture as mod

    monkeypatch.setattr(mod, "SOURCE_DIR", tmp_path)
    with pytest.raises(FixtureBuildError, match="not found"):
        mod.load_source()
