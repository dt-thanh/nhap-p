"""Database-free contract tests for the 0024/0025 forward data corrections."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from pytest import raises

ROOT = Path(__file__).parents[2]


def _load(name: str, filename: str):
    path = ROOT / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


migration = _load(
    "migration_0024_vinhomes_labels_stats",
    "0024_rename_synthetic_labels_vinhomes_stats.py",
)
unit_labels = _load("migration_0025_synthetic_unit_labels", "0025_synthetic_unit_labels.py")


def test_new_revisions_are_linear_and_fit_the_version_column():
    assert migration.revision == "0024_vinhomes_labels_stats"
    assert migration.down_revision == "0023_seed_domain_demo_2026"
    assert unit_labels.revision == "0025_synthetic_unit_labels"
    assert unit_labels.down_revision == migration.revision
    assert len(migration.revision) <= 32
    assert len(unit_labels.revision) <= 32


def test_rename_scope_keeps_provenance_and_removes_demo_from_visible_labels():
    assert migration.SYNTHETIC_SOURCE_SYSTEM == "synthetic_demo"
    assert migration.SYNTHETIC_SOURCE_INSTANCE_ID == "synthetic-demo-2026"
    assert migration.SYNTHETIC_EXTERNAL_PREFIX == "demo26-"
    assert all("demo" not in name.lower() for name in migration.SYNTHETIC_PROJECT_NAMES.values())
    assert migration.VINHOMES_PROJECT_EXTERNAL_IDS == (
        "prj_op1",
        "prj_rvs",
        "prj_smc",
        "prj_tmc",
    )


def test_unit_label_conversion_is_stable_and_does_not_change_external_identity():
    assert unit_labels._visible_unit_code("demo26-p01-a01-u0001") == "P01-A01-0001"
    assert unit_labels._visible_unit_code("demo26-p04-a03-u0027") == "P04-A03-0027"
    assert unit_labels._visible_unit_code("unrelated-unit") is None


def test_supplemental_deal_identity_is_deterministic_and_namespaced():
    first = migration._uid("deal", "stats26-ar_0044-sold-01")
    second = migration._uid("deal", "stats26-ar_0044-sold-01")
    assert first == second
    assert migration.DOMAIN_DEAL_PREFIX == "stats26-"
    assert migration.DOMAIN_SOURCE_SYSTEM == "crm_real_data_fixture"
    assert migration.DOMAIN_SOURCE_INSTANCE_ID == "ai-dev-fixture"


def test_unit_label_helper_rejects_non_namespace_shapes():
    with raises(AttributeError):
        # The migration intentionally accepts only string external IDs from the
        # selected namespace; malformed database rows are not rewritten.
        unit_labels._visible_unit_code(None)
