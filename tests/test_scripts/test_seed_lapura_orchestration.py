"""Coverage for scripts/seed_lapura.py's --confirm-seed orchestration helpers.

`_confirm_seed` itself (real API/DB writes) is intentionally not exercised
here or anywhere in this suite — only its constituent, independently
testable pieces are covered: Pass-1 manifest loading/guard checks, the
AbsorpIQ projection-count query and poll loop, and Pass-2 capture.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from scripts.derive_lapura_seed_fixture import build_fixture, load_source
from scripts.lapura_manifest import save_manifest
from scripts.seed_lapura import (
    EXPECTED_COUNTS,
    SeedOrchestrationError,
    _absorpiq_counts_for_project,
    _capture_pass2,
    _load_pass1_manifest_or_raise,
    _manifest_path,
    _wait_for_projection,
)
from tests.conftest import db_skip_reason
from tests.ranking_fixture import AREA_ID, PROJECT_ID, _insert_config, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")


def _pass1_manifest(*, batch_id="b1", sha256_set=None):
    return {
        "batch_id": batch_id,
        "pass": 1,
        "source_files": [{"name": "f.csv", "sha256": s} for s in (sha256_set or {"aaa"})],
        "entities": [{"kind": "project", "source_row_key": "P-0001", "fixture_external_key": "prj-la-pura"}],
        "counts": {"projects": 1, "areas": 24, "units": 392, "deals": 193, "unit_enrichment": 392},
    }


def test_load_pass1_manifest_refuses_when_fixture_missing(tmp_path, monkeypatch):
    import scripts.seed_lapura as mod

    monkeypatch.setattr(mod, "OUT_FIXTURE", tmp_path / "does-not-exist.json")
    with pytest.raises(SeedOrchestrationError, match="run --write-fixture first"):
        _load_pass1_manifest_or_raise("b1", frozenset({"aaa"}))


def test_load_pass1_manifest_refuses_when_manifest_missing(tmp_path, monkeypatch):
    import scripts.seed_lapura as mod

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "OUT_FIXTURE", fixture_path)
    monkeypatch.setattr(mod, "OUT_MANIFEST_DIR", tmp_path / "manifests")
    with pytest.raises(SeedOrchestrationError, match="run --write-fixture"):
        _load_pass1_manifest_or_raise("b1", frozenset({"aaa"}))


def test_load_pass1_manifest_refuses_when_already_pass2(tmp_path, monkeypatch):
    import scripts.seed_lapura as mod

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text("{}", encoding="utf-8")
    manifest_dir = tmp_path / "manifests"
    monkeypatch.setattr(mod, "OUT_FIXTURE", fixture_path)
    monkeypatch.setattr(mod, "OUT_MANIFEST_DIR", manifest_dir)
    manifest = _pass1_manifest(batch_id="b1")
    manifest["entities"][0]["real_external_id"] = "P-0099"
    manifest["entities"][0]["real_id"] = "uuid-1"
    manifest["pass"] = 2
    save_manifest(manifest_dir / "lapura_seed_manifest_b1.json", manifest)
    with pytest.raises(SeedOrchestrationError, match="already seeded"):
        _load_pass1_manifest_or_raise("b1", frozenset({"aaa"}))


def test_load_pass1_manifest_refuses_when_source_hash_changed(tmp_path, monkeypatch):
    import scripts.seed_lapura as mod

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text("{}", encoding="utf-8")
    manifest_dir = tmp_path / "manifests"
    monkeypatch.setattr(mod, "OUT_FIXTURE", fixture_path)
    monkeypatch.setattr(mod, "OUT_MANIFEST_DIR", manifest_dir)
    save_manifest(manifest_dir / "lapura_seed_manifest_b1.json", _pass1_manifest(batch_id="b1", sha256_set={"aaa"}))
    with pytest.raises(SeedOrchestrationError, match="Source CSVs have changed"):
        _load_pass1_manifest_or_raise("b1", frozenset({"different-hash"}))


def test_load_pass1_manifest_succeeds_for_a_genuine_pass1_manifest(tmp_path, monkeypatch):
    import scripts.seed_lapura as mod

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text("{}", encoding="utf-8")
    manifest_dir = tmp_path / "manifests"
    monkeypatch.setattr(mod, "OUT_FIXTURE", fixture_path)
    monkeypatch.setattr(mod, "OUT_MANIFEST_DIR", manifest_dir)
    save_manifest(manifest_dir / "lapura_seed_manifest_b1.json", _pass1_manifest(batch_id="b1", sha256_set={"aaa"}))
    manifest = _load_pass1_manifest_or_raise("b1", frozenset({"aaa"}))
    assert manifest["batch_id"] == "b1"


@pytest.mark.asyncio
async def test_absorpiq_counts_for_project_matches_the_seeded_dataset(truncate_all):
    engine = truncate_all
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_config(factory)
    await _insert_dataset(factory)

    async with factory() as session:
        await session.execute(
            sa.text("UPDATE projects SET external_id = 'P-TEST-1', source_system = 'mini_crm' WHERE id = :p"),
            {"p": PROJECT_ID},
        )
        await session.commit()

    counts = await _absorpiq_counts_for_project(engine, "P-TEST-1")
    assert counts["projects"] == 1
    assert counts["areas"] == 1
    assert counts["units"] == 5
    assert counts["deals"] >= 0


@pytest.mark.asyncio
async def test_wait_for_projection_returns_immediately_once_counts_match(truncate_all):
    engine = truncate_all
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_config(factory)
    await _insert_dataset(factory)
    async with factory() as session:
        await session.execute(
            sa.text("UPDATE projects SET external_id = 'P-TEST-2', source_system = 'mini_crm' WHERE id = :p"),
            {"p": PROJECT_ID},
        )
        await session.commit()

    import scripts.seed_lapura as mod

    # Match against whatever this fixture actually produces (deal count is an
    # implementation detail of _insert_dataset's funnel rows, not fixed at 0)
    # rather than assuming a number — the point of this test is "returns as
    # soon as the live counts equal EXPECTED_COUNTS", not a specific number.
    actual = await _absorpiq_counts_for_project(engine, "P-TEST-2")
    orig = mod.EXPECTED_COUNTS
    mod.EXPECTED_COUNTS = actual
    try:
        counts = await _wait_for_projection(engine, "P-TEST-2", timeout=5.0, interval=0.1)
    finally:
        mod.EXPECTED_COUNTS = orig
    assert counts == actual


@pytest.mark.asyncio
async def test_wait_for_projection_times_out_when_counts_never_match(truncate_all):
    engine = truncate_all
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_config(factory)
    await _insert_dataset(factory)
    async with factory() as session:
        await session.execute(
            sa.text("UPDATE projects SET external_id = 'P-TEST-3', source_system = 'mini_crm' WHERE id = :p"),
            {"p": PROJECT_ID},
        )
        await session.commit()

    with pytest.raises(SeedOrchestrationError, match="Projection timeout"):
        await _wait_for_projection(engine, "P-TEST-3", timeout=0.3, interval=0.1)


@pytest.mark.asyncio
async def test_capture_pass2_fills_every_entity_from_live_ids(tmp_path, truncate_all):
    engine = truncate_all
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _insert_config(factory)
    await _insert_dataset(factory)
    async with factory() as session:
        await session.execute(
            sa.text("UPDATE projects SET external_id = 'P-TEST-4', source_system = 'mini_crm' WHERE id = :p"),
            {"p": PROJECT_ID},
        )
        await session.execute(
            sa.text("UPDATE areas SET external_id = 'A-TEST-4' WHERE id = :a"),
            {"a": AREA_ID},
        )
        await session.commit()

    manifest = {
        "batch_id": "b1",
        "pass": 1,
        "source_files": [],
        "entities": [
            {"kind": "project", "source_row_key": "P-0001", "fixture_external_key": "prj-la-pura"},
            {"kind": "area", "source_row_key": "A-0001", "fixture_external_key": "area-la-pura-a-0001"},
        ],
    }
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "prj-la-pura": {"kind": "project", "external_id": "P-TEST-4"},
                "area-la-pura-a-0001": {"kind": "area", "external_id": "A-TEST-4"},
            }
        ),
        encoding="utf-8",
    )

    pass2 = await _capture_pass2(manifest, state_file, engine)
    real_ids = {e["fixture_external_key"]: e["real_id"] for e in pass2["entities"]}
    assert real_ids["prj-la-pura"] == str(PROJECT_ID)
    assert real_ids["area-la-pura-a-0001"] == str(AREA_ID)
    assert pass2["pass"] == 2


@pytest.mark.asyncio
async def test_capture_pass2_raises_when_an_entity_never_projected(tmp_path, truncate_all):
    engine = truncate_all
    await _insert_config(async_sessionmaker(engine, expire_on_commit=False))

    manifest = {
        "batch_id": "b1",
        "pass": 1,
        "source_files": [],
        "entities": [{"kind": "project", "source_row_key": "P-0001", "fixture_external_key": "prj-la-pura"}],
    }
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"prj-la-pura": {"kind": "project", "external_id": "P-NEVER-PROJECTED"}}), encoding="utf-8"
    )

    with pytest.raises(SeedOrchestrationError, match="Pass-2 capture incomplete"):
        await _capture_pass2(manifest, state_file, engine)


def test_manifest_path_uses_the_batch_id():
    assert _manifest_path("lapura-20260101-001").name == "lapura_seed_manifest_lapura-20260101-001.json"


def test_expected_counts_matches_the_approved_dataset():
    data, _ = load_source()
    fixture = build_fixture(data)
    assert EXPECTED_COUNTS == {
        "projects": len(fixture["projects"]),
        "areas": len(fixture["areas"]),
        "units": len(fixture["units"]),
        "deals": len(fixture["deals"]),
    }
