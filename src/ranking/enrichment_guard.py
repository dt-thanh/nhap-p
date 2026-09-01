"""Guard against `unit_enrichment_attributes` (0043) becoming a ranking
input by accident.

That table is contextual/reference data — nothing in `src/ranking/` reads it
(see `tests/test_ranking/test_unit_enrichment_not_authoritative.py`'s
structural check, which fails the build if that ever stops being true). This
module is the one real, exercised call site for the rule stated in that
migration's docstring: a column here may only become a scored ranking
feature after (1) explicit feature registration
(`ranking_feature_definitions`), (2) an approved, evidence-backed value
assertion or justification (the existing PR-2 governance path), and (3) an
explicit `ranking_configs.weights` opt-in referencing that feature key.

The only caller today is `scripts/load_lapura_unit_enrichment.py`, right
before it inserts rows: if the *currently published* ranking config already
has a weight key that collides with one of this table's scoreable-shaped
columns, and that key has no matching, active `ranking_feature_definitions`
row, the load refuses. An unregistered collision is far more likely to be an
accidental naming clash than a deliberate, governed promotion, so failing
closed here is the safe default; a deliberate promotion registers the
feature first and the collision stops being a collision.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tables import ranking_configs, ranking_feature_definitions

# Every unit_enrichment_attributes column that carries a numeric/categorical
# value shaped like something a ranking config *could* name as a feature key.
# Deliberately excludes pure lineage/provenance/identity columns (source_*,
# import_batch_id, is_synthetic, data_profile, *_origin, timestamps, id,
# unit_id) — those could never plausibly collide with a scoring weight key.
ENRICHMENT_SOURCED_FEATURE_KEYS = frozenset(
    {
        "subdivision",
        "tower",
        "floor",
        "unit_number",
        "bedrooms",
        "bathrooms",
        "gross_area_sqm",
        "net_area_sqm",
        "standard_price_vnd",
        "loan_price_vnd",
        "stacking_price_million_vnd",
        "agency_name",
        "price_per_sqm_gross_vnd",
        "price_per_sqm_net_vnd",
        "area_efficiency_ratio",
        "loan_premium_pct",
        "floor_band",
        "direction",
        "balcony_direction",
        "view",
        "corner_unit_proxy",
    }
)


class EnrichmentGuardError(RuntimeError):
    """An enrichment column collides with an active ranking-config weight key
    that has no matching, active, registered feature definition."""


async def ensure_enrichment_keys_not_in_active_config(session: AsyncSession) -> None:
    published = (
        await session.execute(sa.select(ranking_configs.c.id, ranking_configs.c.weights).where(
            ranking_configs.c.status == "published"
        ))
    ).mappings().first()
    if published is None:
        return  # no active config at all -> nothing to collide with

    weight_keys = set((published["weights"] or {}).keys())
    colliding = weight_keys & ENRICHMENT_SOURCED_FEATURE_KEYS
    if not colliding:
        return

    registered = set(
        (
            await session.execute(
                sa.select(ranking_feature_definitions.c.feature_key).where(
                    ranking_feature_definitions.c.feature_key.in_(colliding),
                    ranking_feature_definitions.c.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    unregistered = colliding - registered
    if unregistered:
        raise EnrichmentGuardError(
            "Refusing to load unit_enrichment_attributes: the published ranking config "
            f"(id={published['id']}) already weights {sorted(unregistered)}, which collide with "
            "unit_enrichment_attributes column names, and none of them has a matching active "
            "ranking_feature_definitions row. If this is a deliberate, governed promotion of an "
            "enrichment attribute to a scored feature, register it in ranking_feature_definitions "
            "first (D40's promotion path) — this guard exists to catch accidental naming "
            "collisions, not to block deliberate ones."
        )
