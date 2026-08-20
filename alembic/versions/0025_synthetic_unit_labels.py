"""Correct visible unit labels for the namespace seeded by revision 0023.

Revision ID: 0025_synthetic_unit_labels
Revises: 0024_vinhomes_labels_stats

The 0024 migration preserved the external IDs but its first-pass parser did not
match the actual ``demo26-p01-a01-u0001`` shape.  This forward-only correction
updates only namespace-owned ``unit_code`` values; it does not alter IDs,
statuses, deals, or any source records.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from src.models.tables import units

revision: str = "0025_synthetic_unit_labels"
down_revision: str | None = "0024_vinhomes_labels_stats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_SYSTEM = "synthetic_demo"
SOURCE_INSTANCE_ID = "synthetic-demo-2026"
EXTERNAL_PREFIX = "demo26-"
AS_OF_TIMESTAMP = datetime(2026, 8, 16, 18, tzinfo=UTC)


def _safe_target(bind: sa.Connection) -> None:
    app_environment = os.getenv("APP_ENV", "").strip().lower()
    if not app_environment:
        try:
            from src.config import get_settings

            app_environment = get_settings().app_env.strip().lower()
        except Exception:  # pragma: no cover
            app_environment = ""
    if app_environment in {"production", "staging"}:
        raise RuntimeError("0025 refuses to run in a production-like APP_ENV")
    database_name = str(bind.execute(sa.text("SELECT current_database()")).scalar_one()).lower()
    if any(marker in database_name for marker in ("prod", "production", "live", "minicrm")):
        raise RuntimeError("0025 refuses a production-like or Mini CRM database")


def _visible_unit_code(external_unit_id: str) -> str | None:
    pieces = external_unit_id.removeprefix(EXTERNAL_PREFIX).split("-")
    if len(pieces) == 3 and pieces[-1].startswith("u"):
        return "-".join((*pieces[:2], pieces[2][1:])).upper()
    return None


def upgrade() -> None:
    bind = op.get_bind()
    _safe_target(bind)
    rows = bind.execute(
        sa.select(units.c.id, units.c.external_unit_id).where(
            units.c.source_system == SOURCE_SYSTEM,
            units.c.source_instance_id == SOURCE_INSTANCE_ID,
            units.c.external_unit_id.like(f"{EXTERNAL_PREFIX}%"),
        )
    ).all()
    for unit_id, external_unit_id in rows:
        code = _visible_unit_code(external_unit_id)
        if code:
            bind.execute(
                units.update().where(units.c.id == unit_id).values(
                    unit_code=code,
                    updated_at=AS_OF_TIMESTAMP,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    _safe_target(bind)
    bind.execute(
        units.update()
        .where(
            units.c.source_system == SOURCE_SYSTEM,
            units.c.source_instance_id == SOURCE_INSTANCE_ID,
            units.c.external_unit_id.like(f"{EXTERNAL_PREFIX}%"),
        )
        .values(unit_code=sa.func.upper(units.c.external_unit_id), updated_at=AS_OF_TIMESTAMP)
    )
