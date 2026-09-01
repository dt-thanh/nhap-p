"""Generic, reusable per-unit enrichment attributes (contextual/reference data).

Revision ID: 0043_unit_enrichment_attributes
Revises: 0042_legal_assertion_gate
Create Date: 2026-08-28

Purely additive: one new table, no existing table touched. Built for the
La Pura AHP-processed dataset seed (`datasets/processed_data/`,
`data/Real_estate/data/lapura_unit_attributes_import.csv`), but the table
itself is NOT project-specific — any future project's per-unit enrichment
data (subdivision, floor, view, pricing, ...) can reuse the same shape, keyed
only by `unit_id`.

**MVP canonical-snapshot design, not a versioned/multi-source store.**
`unit_id` carries a UNIQUE constraint: one current enrichment row per unit.
The source data this seed reads is frozen/SHA-256-verified read-only, so
there is nothing to version yet. If a future project needs multiple
enrichment sources per unit, or a history of revisions over time, that is a
NEW migration (drop the uniqueness, add a `source_priority`/`is_current`
column) — deliberately not built here, since nothing in this repository
needs it yet.

**Contextual/reference data only — NOT wired into ranking.** No code in
`src/ranking/` reads this table (verified by
`tests/test_ranking/test_unit_enrichment_not_authoritative.py`'s structural
grep check). `engine.score_unit()`/`run_ranking()`/
`compute_hierarchical_scores_for_run()` have no query path to it. Promoting
any column here to a scored ranking feature requires, in order: (1) a
`ranking_feature_definitions` row (explicit feature registration), (2) a
published, CEO-approved value assertion or evidence-backed justification
(the existing PR-2 governance path), and (3) an explicit
`ranking_configs.weights` opt-in referencing that feature key. See
`src/ranking/enrichment_guard.py` for the runtime check that enforces this
before any future loader may insert rows whose column names collide with an
active config's weight keys without that governance trail.

`is_synthetic` is a fast-queryable rollup of `physical_features_origin`/
`agency_name_origin`/`data_profile` — those three keep the full-fidelity,
per-group provenance text the source data already carries; `is_synthetic`
exists only so a query/guard doesn't need to know those columns' vocabulary.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0043_unit_enrichment_attributes"
down_revision: str | None = "0042_legal_assertion_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.TIMESTAMP(timezone=True)


def _has_rows(table: str) -> bool:
    bind = op.get_bind()
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar())


def upgrade() -> None:
    op.create_table(
        "unit_enrichment_attributes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("unit_id", UUID, nullable=False),
        # --- descriptive attributes, all optional: a source may supply a subset ---
        sa.Column("subdivision", sa.Text(), nullable=True),
        sa.Column("subdivision_raw", sa.Text(), nullable=True),
        sa.Column("tower", sa.Text(), nullable=True),
        sa.Column("floor", sa.Integer(), nullable=True),
        sa.Column("unit_number", sa.Text(), nullable=True),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("bathrooms", sa.Integer(), nullable=True),
        sa.Column("gross_area_sqm", sa.Numeric(10, 2), nullable=True),
        sa.Column("net_area_sqm", sa.Numeric(10, 2), nullable=True),
        sa.Column("standard_price_vnd", sa.Numeric(18, 2), nullable=True),
        sa.Column("loan_price_vnd", sa.Numeric(18, 2), nullable=True),
        sa.Column("stacking_price_million_vnd", sa.Numeric(14, 3), nullable=True),
        sa.Column("agency_name", sa.Text(), nullable=True),
        sa.Column("price_per_sqm_gross_vnd", sa.Numeric(18, 2), nullable=True),
        sa.Column("price_per_sqm_net_vnd", sa.Numeric(18, 2), nullable=True),
        sa.Column("area_efficiency_ratio", sa.Numeric(6, 4), nullable=True),
        sa.Column("loan_premium_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("floor_band", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("balcony_direction", sa.Text(), nullable=True),
        sa.Column("view", sa.Text(), nullable=True),
        sa.Column("corner_unit_proxy", sa.Boolean(), nullable=True),
        # --- provenance / synthetic-data disclosure ---
        sa.Column("physical_features_origin", sa.Text(), nullable=True),
        sa.Column("agency_name_origin", sa.Text(), nullable=True),
        sa.Column("data_profile", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # --- import lineage: required on every row, regardless of source ---
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_file_sha256", sa.Text(), nullable=False),
        sa.Column("source_row_key", sa.Text(), nullable=False),
        sa.Column("import_batch_id", sa.Text(), nullable=False),
        sa.Column("imported_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_unit_enrichment_attributes"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_uea_unit_id", ondelete="CASCADE"),
        sa.UniqueConstraint("unit_id", name="uq_uea_unit_id"),
        sa.CheckConstraint("source_system <> ''", name="ck_uea_source_system_not_blank"),
        sa.CheckConstraint("source_file <> ''", name="ck_uea_source_file_not_blank"),
        sa.CheckConstraint("source_row_key <> ''", name="ck_uea_source_row_key_not_blank"),
        sa.CheckConstraint("import_batch_id <> ''", name="ck_uea_import_batch_id_not_blank"),
        sa.CheckConstraint(
            "floor IS NULL OR floor BETWEEN 1 AND 60",
            name="ck_uea_floor_range",
        ),
        sa.CheckConstraint("gross_area_sqm IS NULL OR gross_area_sqm > 0", name="ck_uea_gross_area_positive"),
        sa.CheckConstraint("net_area_sqm IS NULL OR net_area_sqm > 0", name="ck_uea_net_area_positive"),
        sa.CheckConstraint(
            "net_area_sqm IS NULL OR gross_area_sqm IS NULL OR net_area_sqm <= gross_area_sqm",
            name="ck_uea_net_le_gross",
        ),
        sa.CheckConstraint(
            "standard_price_vnd IS NULL OR standard_price_vnd > 0", name="ck_uea_standard_price_positive"
        ),
        sa.CheckConstraint("loan_price_vnd IS NULL OR loan_price_vnd > 0", name="ck_uea_loan_price_positive"),
        sa.CheckConstraint(
            "area_efficiency_ratio IS NULL OR area_efficiency_ratio BETWEEN 0 AND 1",
            name="ck_uea_efficiency_ratio_range",
        ),
        sa.CheckConstraint(
            "loan_premium_pct IS NULL OR loan_premium_pct >= 0", name="ck_uea_loan_premium_nonneg"
        ),
        sa.CheckConstraint(
            "floor_band IS NULL OR floor_band IN ('low', 'mid', 'high')", name="ck_uea_floor_band_allowed"
        ),
    )
    op.create_index("ix_uea_import_batch_id", "unit_enrichment_attributes", ["import_batch_id"])
    op.create_index("ix_uea_source_system", "unit_enrichment_attributes", ["source_system"])


def downgrade() -> None:
    if _has_rows("unit_enrichment_attributes"):
        raise RuntimeError(
            "Refusing to downgrade 0043: unit_enrichment_attributes has rows. "
            "Run the targeted rollback (scripts/rollback_lapura_seed.py) first."
        )
    op.drop_index("ix_uea_source_system", table_name="unit_enrichment_attributes")
    op.drop_index("ix_uea_import_batch_id", table_name="unit_enrichment_attributes")
    op.drop_table("unit_enrichment_attributes")
