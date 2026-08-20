"""Populate operator-supplied Cloudinary cover URLs for known catalog rows.

Revision ID: 0026_cloudinary_cover_images
Revises: 7022f5bfa250

This is a data-only migration.  It does not create rows, change schema, or
touch ingestion/sync-owned fields.  Before applying it in a development
database, replace selected ``None`` values below with public Cloudinary
delivery URLs copied by the operator.

The migration updates only rows that already exist and is safe to re-run: a
filled mapping entry deterministically sets only ``cover_image_url`` for the
matching ``external_id``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.models.tables import areas, projects

revision: str = "0026_cloudinary_cover_images"
down_revision: str | None = "7022f5bfa250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Paste the direct URL copied from Cloudinary between the quotes.  Do not paste
# an upload preset, API key, or signed credential here.
PROJECT_IMAGES: dict[str, str | None] = {
    # "prj_op1": "https://res.cloudinary.com/<cloud>/image/upload/projects/op1.jpg",
    # "prj_smc": "https://res.cloudinary.com/<cloud>/image/upload/projects/smart-city.jpg",
    # "prj_tmc": "https://res.cloudinary.com/<cloud>/image/upload/projects/times-city.jpg",
    # "prj_rvs": "https://res.cloudinary.com/<cloud>/image/upload/projects/riverside.jpg",
}

# Add one entry per area that has a cover image.  The keys are
# ``areas.external_id`` values, for example ``ar_0001``.
AREA_IMAGES: dict[str, str | None] = {
    # "ar_0001": "https://res.cloudinary.com/<cloud>/image/upload/areas/ar_0001.jpg",
    # "ar_0002": "https://res.cloudinary.com/<cloud>/image/upload/areas/ar_0002.jpg",
}


def _apply_cover_urls(bind: sa.Connection, table: sa.Table, mapping: dict[str, str | None]) -> None:
    for external_id, image_url in mapping.items():
        if image_url is None:
            continue

        url = image_url.strip()
        if not url.startswith("https://res.cloudinary.com/"):
            raise ValueError(f"Cloudinary URL required for {external_id}")

        result = bind.execute(
            table.update()
            .where(table.c.external_id == external_id)
            .values(cover_image_url=url)
        )
        if result.rowcount == 0:
            print(f"0026: skipped missing {table.name}.external_id={external_id}")


def upgrade() -> None:
    bind = op.get_bind()
    _apply_cover_urls(bind, projects, PROJECT_IMAGES)
    _apply_cover_urls(bind, areas, AREA_IMAGES)


def downgrade() -> None:
    # Do not clear operator-managed image URLs during downgrade.  A later
    # migration or manual update may have replaced a value set here.
    pass
