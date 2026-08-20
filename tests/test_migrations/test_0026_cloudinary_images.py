"""Contract checks for the operator-managed Cloudinary image data migration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "0026_cloudinary_cover_images.py"


spec = importlib.util.spec_from_file_location("migration_0026_cloudinary_images", MIGRATION_PATH)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)


def test_revision_is_after_current_merge_head():
    assert migration.revision == "0026_cloudinary_cover_images"
    assert migration.down_revision == "7022f5bfa250"


def test_operator_mappings_allow_only_public_cloudinary_urls_or_unset_values():
    for mapping in (migration.PROJECT_IMAGES, migration.AREA_IMAGES):
        assert all(
            image_url is None or image_url.startswith("https://res.cloudinary.com/")
            for image_url in mapping.values()
        )
