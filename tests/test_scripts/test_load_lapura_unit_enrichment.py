from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from scripts.lapura_manifest import apply_real_ids
from scripts.load_lapura_unit_enrichment import LoadError, apply_inserts, plan_inserts
from src.models.tables import ranking_configs, unit_enrichment_attributes
from tests.conftest import db_skip_reason
from tests.ranking_fixture import SEED_WEIGHTS, UNIT_IDS, _insert_config, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")


def _fixture():
    return {
        "unit_enrichment": [
            {
                "unit_external_key": "unit-la-pura-u-0001",
                "source_row_key": "U-0001",
                "subdivision": "Zenia",
                "subdivision_raw": "Zenia",
                "tower": "B3",
                "floor": 12,
                "unit_number": "05",
                "bedrooms": 2,
                "bathrooms": 2,
                "gross_area_sqm": 60.75,
                "net_area_sqm": 55.0,
                "standard_price_vnd": 3500000000.0,
                "loan_price_vnd": 3843000000.0,
                "stacking_price_million_vnd": 3500.0,
                "agency_name": "VNL",
                "price_per_sqm_gross_vnd": 57613168.7,
                "price_per_sqm_net_vnd": 63636363.6,
                "area_efficiency_ratio": 0.905,
                "loan_premium_pct": 9.8,
                "floor_band": "high",
                "direction": "Đông",
                "balcony_direction": "Nam",
                "view": "Hồ bơi",
                "corner_unit_proxy": False,
                "physical_features_origin": "synthetic_v1_tower_stack_floor_band",
                "agency_name_origin": "synthetic",
                "data_profile": "demo",
                "is_synthetic": True,
                "source_system": "lapura_ahp_prep",
                "source_file": "lapura_unit_attributes_import.csv",
            }
        ]
    }


def _manifest_pass1():
    return {
        "batch_id": "b1",
        "pass": 1,
        "source_files": [{"name": "lapura_unit_attributes_import.csv", "sha256": "deadbeef"}],
        "entities": [
            {"kind": "unit", "source_row_key": "U-0001", "fixture_external_key": "unit-la-pura-u-0001"},
        ],
    }


def test_plan_inserts_refuses_when_manifest_is_pass_1():
    with pytest.raises(LoadError, match="not Pass-2 complete"):
        plan_inserts(_fixture(), _manifest_pass1())


def test_plan_inserts_produces_the_expected_row_shape():
    manifest = apply_real_ids(
        _manifest_pass1(), {"unit-la-pura-u-0001": {"real_external_id": "U-0099", "real_id": "11111111-1111-4111-8111-111111111111"}}
    )
    rows = plan_inserts(_fixture(), manifest)
    assert len(rows) == 1
    row = rows[0]
    assert row["unit_id"] == "11111111-1111-4111-8111-111111111111"
    assert row["source_file_sha256"] == "deadbeef"
    assert row["import_batch_id"] == "b1"
    assert "unit_external_key" not in row
    assert row["is_synthetic"] is True


def test_plan_inserts_refuses_when_a_unit_has_no_real_id():
    manifest = apply_real_ids(_manifest_pass1(), {"unit-la-pura-u-0001": {"real_external_id": None, "real_id": "x"}})
    manifest["entities"][0]["real_id"] = None  # simulate a still-missing id despite pass=2 bookkeeping
    with pytest.raises(LoadError, match="not Pass-2 complete"):
        plan_inserts(_fixture(), manifest)


@pytest.mark.asyncio
async def test_apply_inserts_writes_a_row_joined_to_the_real_unit(truncate_all):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    await _insert_config(factory)
    await _insert_dataset(factory)

    unit_id = UNIT_IDS["u1"]
    manifest = apply_real_ids(
        _manifest_pass1(), {"unit-la-pura-u-0001": {"real_external_id": "U-0099", "real_id": str(unit_id)}}
    )
    rows = plan_inserts(_fixture(), manifest)

    n = await apply_inserts(factory, rows)
    assert n == 1

    async with factory() as session:
        stored = (
            await session.execute(
                sa.select(unit_enrichment_attributes).where(unit_enrichment_attributes.c.unit_id == unit_id)
            )
        ).mappings().first()
    assert stored is not None
    assert stored["subdivision"] == "Zenia"
    assert stored["is_synthetic"] is True
    assert stored["import_batch_id"] == "b1"


@pytest.mark.asyncio
async def test_apply_inserts_refuses_on_unregistered_config_collision(truncate_all):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    await _insert_config(factory)
    await _insert_dataset(factory)

    async with factory() as session:
        colliding_weights = {
            **SEED_WEIGHTS,
            "floor_band": {"weight": "0.1", "direction": "positive", "missing_value_policy": "skip", "min_confidence": "0"},
        }
        await session.execute(sa.update(ranking_configs).values(weights=colliding_weights))
        await session.commit()

    unit_id = UNIT_IDS["u1"]
    manifest = apply_real_ids(
        _manifest_pass1(), {"unit-la-pura-u-0001": {"real_external_id": "U-0099", "real_id": str(unit_id)}}
    )
    rows = plan_inserts(_fixture(), manifest)

    with pytest.raises(Exception, match="floor_band"):
        await apply_inserts(factory, rows)

    async with factory() as session:
        count = (await session.execute(sa.select(sa.func.count()).select_from(unit_enrichment_attributes))).scalar()
    assert count == 0
