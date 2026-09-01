"""Core (DB-read, statement-building) logic for the legacy AI/CRM fixture's
`deals` — projected from state already written by
`scripts/_seed_ai_crm_fixture_core.py` (the `projects`/`areas`/`units` half of
the same fixture), never from a JSON file of its own.

Extracted from `alembic/versions/0021_seed_ai_crm_fixture_deals.py`, whose
`upgrade()` no longer calls this (Alembic must never auto-seed business data —
see that migration's docstring). The exact same deterministic algorithm now
lives here so `scripts/seed_legacy_fixture.py` (the explicit, confirmed,
dev-only CLI) can reuse it without duplicating it. `downgrade()` in 0021 does
NOT depend on this module — it deletes by `(source_system, source_instance_id)`
identity alone, so it stays correct regardless of how the rows were created
(the old migration path or this new CLI path).

Same fixture identity as `scripts/_seed_ai_crm_fixture_core.py`, deliberately:

    source_system      = "crm_real_data_fixture"
    source_instance_id = "ai-dev-fixture"

Same id namespace too (`NS_INGESTION_SEED`) — unchanged from the original
migration, so this module reconciles (via `ON CONFLICT (id) DO UPDATE`) with
deal rows an existing database may already have from before this change,
rather than creating duplicates.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

NS_INGESTION_SEED = uuid.UUID("8f2b0c1d-6a4e-4b3f-9c7a-2e5d1a9b6f30")
SOURCE_SYSTEM = "crm_real_data_fixture"
SOURCE_INSTANCE_ID = "ai-dev-fixture"

# Share of an area's IN-DATABASE available units the hottest-selling area
# converts to sold. Every other area is scaled down linearly by its own trend
# intensity, so the ORDERING between areas is the source's, not ours.
MAX_SOLD_SHARE = 0.45
# Never sell out the demo: keep at least this share of units available so the
# inventory screen still shows stock and ranking still has candidates to rank.
MIN_AVAILABLE_SHARE = 0.25
# Lost deals per sold deal. Feeds `area_conversion_norm`'s denominator; without
# any, conversion is a vacuous 1.0 everywhere.
LOST_PER_SOLD = 0.35
# One early-funnel deal per N remaining available units.
FUNNEL_EVERY = 3
FUNNEL_STATUSES = ("lead", "qualified", "interested", "viewing")

# `cancelled` is not a repo status; DomainProjector maps it to `lost` and keeps
# the original in `source_status`. Some lost deals carry it so the fixture
# exercises that provenance path instead of pretending it never happens.
CANCELLED_EVERY = 3


def _n(key: str, lo: int, hi: int) -> int:
    """Deterministic value in [lo, hi] — a stable hash, never `random`."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return lo + (int(digest[:12], 16) % (hi - lo + 1))


def _allocate(total: int, weights: list[int], salt: str) -> list[int]:
    """Split `total` across buckets by `weights` (largest-remainder), preserving
    the weekly SHAPE of the legacy trend deterministically."""
    if total <= 0 or not weights:
        return [0] * len(weights)
    weight_sum = sum(weights)
    if weight_sum <= 0:
        base = [total // len(weights)] * len(weights)
        for i in range(total - sum(base)):
            base[i] += 1
        return base
    exact = [total * w / weight_sum for w in weights]
    out = [int(x) for x in exact]
    remainder = total - sum(out)
    order = sorted(
        range(len(weights)),
        key=lambda i: (exact[i] - out[i], weights[i], _n(f"alloc:{salt}:{i}", 0, 999_983)),
        reverse=True,
    )
    for i in order[:remainder]:
        out[i] += 1
    return out


def _sold_target(available: int, intensity: float, max_intensity: float) -> int:
    """How many available units of this area become sold, scaled against the
    hottest area so relative ordering survives the supply shortfall."""
    if available <= 0 or max_intensity <= 0:
        return 0
    share = MAX_SOLD_SHARE * (intensity / max_intensity)
    keep = max(2, round(available * MIN_AVAILABLE_SHARE))
    return max(0, min(round(available * share), available - keep))


def _row(
    external_deal_id: str,
    unit_id: uuid.UUID,
    status: str,
    source_status: str,
    *,
    now: datetime,
    reserved_at: datetime | None = None,
    sold_at: datetime | None = None,
    lost_at: datetime | None = None,
) -> dict[str, Any]:
    latest = max((t for t in (reserved_at, sold_at, lost_at) if t is not None), default=now)
    return {
        "id": uuid.uuid5(NS_INGESTION_SEED, f"deal:{external_deal_id}"),
        "source_system": SOURCE_SYSTEM,
        "source_instance_id": SOURCE_INSTANCE_ID,
        "external_deal_id": external_deal_id,
        "unit_id": unit_id,
        "status": status,
        "source_status": source_status,
        "reserved_at": reserved_at,
        "sold_at": sold_at,
        "lost_at": lost_at,
        "source_revision": 1,
        "source_updated_at": latest,
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
    }


def plan_deals(bind) -> tuple[list[dict[str, Any]], list[uuid.UUID], dict[str, int]]:
    """Build every deal row from state already in the database (the
    `projects`/`areas`/`units` half of this same fixture, plus the legacy
    `absorption_daily` weekly trend). Requires `scripts/_seed_ai_crm_fixture_core`'s
    upserts to have already been applied — raises if no fixture units exist.

    Returns `(deal_rows, sold_unit_ids, counts)`. Pure with respect to `bind`:
    only ever SELECTs; the caller decides when/whether to execute the upsert.
    """
    now = datetime.now(UTC)

    areas_rows = bind.execute(
        sa.text(
            """
            SELECT a.id, a.external_id, a.total_units
            FROM areas a
            WHERE a.external_id IS NOT NULL
            ORDER BY a.external_id
            """
        )
    ).mappings().all()

    units_by_area: dict[uuid.UUID, list[dict]] = {}
    for row in bind.execute(
        sa.text(
            """
            SELECT u.id, u.area_id, u.external_unit_id,
                   CASE
                       WHEN u.status = 'sold' AND EXISTS (
                           SELECT 1 FROM deals d
                           WHERE d.unit_id = u.id
                             AND d.source_system = :sys
                             AND d.source_instance_id = :inst
                             AND d.status = 'sold'
                             AND d.deleted_at IS NULL
                       ) THEN 'available'
                       ELSE u.status
                   END AS status
            FROM units u
            WHERE u.deleted_at IS NULL AND u.source_instance_id = :inst
            ORDER BY u.external_unit_id
            """
        ),
        {"sys": SOURCE_SYSTEM, "inst": SOURCE_INSTANCE_ID},
    ).mappings():
        units_by_area.setdefault(row["area_id"], []).append(dict(row))

    trend_by_area: dict[uuid.UUID, list[dict]] = {}
    for row in bind.execute(
        sa.text(
            """
            SELECT area_id, stat_date, units_sold
            FROM absorption_daily
            WHERE calculator = 'legacy_aggregate'
            ORDER BY area_id, stat_date
            """
        )
    ).mappings():
        trend_by_area.setdefault(row["area_id"], []).append(dict(row))

    intensity: dict[uuid.UUID, float] = {}
    for area in areas_rows:
        weeks = trend_by_area.get(area["id"], [])
        total_units = max(int(area["total_units"] or 0), 1)
        intensity[area["id"]] = sum(int(w["units_sold"]) for w in weeks) / total_units
    max_intensity = max(intensity.values(), default=0.0)

    deals: list[dict] = []
    sold_unit_ids: list[uuid.UUID] = []
    counts = {"sold": 0, "reserved": 0, "lost": 0, "funnel": 0}

    for area in areas_rows:
        area_id = area["id"]
        units = units_by_area.get(area_id, [])
        if not units:
            continue
        weeks = trend_by_area.get(area_id, [])
        available = [u for u in units if u["status"] == "available"]
        reserved = [u for u in units if u["status"] == "reserved"]

        for u in reserved:
            ext = f"D-{u['external_unit_id']}-01"
            reserved_at = now - timedelta(days=_n(f"resv:{ext}", 3, 75), hours=_n(f"rh:{ext}", 0, 23))
            deals.append(_row(ext, u["id"], "reserved", "reserved", reserved_at=reserved_at, now=now))
            counts["reserved"] += 1

        target = _sold_target(len(available), intensity[area_id], max_intensity)
        weights = [int(w["units_sold"]) for w in weeks]
        per_week = _allocate(target, weights, salt=str(area["external_id"]))

        sold_units = available[:target]
        rest = available[target:]
        cursor = 0
        emitted = 0
        for week, count in zip(weeks, per_week, strict=False):
            for _ in range(count):
                if cursor >= len(sold_units):
                    break
                u = sold_units[cursor]
                cursor += 1
                ext = f"D-{u['external_unit_id']}-01"
                sold_at = datetime.combine(week["stat_date"], datetime.min.time(), tzinfo=UTC) + timedelta(
                    hours=_n(f"sh:{ext}", 8, 18), minutes=_n(f"sm:{ext}", 0, 59)
                )
                reserved_at = sold_at - timedelta(days=_n(f"sr:{ext}", 7, 45))
                deals.append(_row(ext, u["id"], "sold", "sold", reserved_at=reserved_at, sold_at=sold_at, now=now))
                sold_unit_ids.append(u["id"])
                emitted += 1
                counts["sold"] += 1

        n_lost = round(emitted * LOST_PER_SOLD)
        n_lost = min(n_lost, len(rest))
        for i in range(n_lost):
            u = rest[i]
            ext = f"D-{u['external_unit_id']}-02"
            lost_at = now - timedelta(days=_n(f"lo:{ext}", 10, 120), hours=_n(f"lh:{ext}", 0, 23))
            src = "cancelled" if i % CANCELLED_EVERY == 0 else "lost"
            deals.append(
                _row(
                    ext,
                    u["id"],
                    "lost",
                    src,
                    reserved_at=lost_at - timedelta(days=_n(f"lr:{ext}", 5, 30)),
                    lost_at=lost_at,
                    now=now,
                )
            )
            counts["lost"] += 1

        for i in range(0, len(rest), FUNNEL_EVERY):
            u = rest[i]
            ext = f"D-{u['external_unit_id']}-03"
            status = FUNNEL_STATUSES[_n(f"fs:{ext}", 0, len(FUNNEL_STATUSES) - 1)]
            deals.append(_row(ext, u["id"], status, status, now=now))
            counts["funnel"] += 1

    return deals, sold_unit_ids, counts


UPSERT = sa.text(
    """
    INSERT INTO deals (
        id, source_system, source_instance_id, external_deal_id, unit_id,
        status, source_status, reserved_at, sold_at, lost_at,
        source_revision, source_updated_at, deleted_at, created_at, updated_at
    ) VALUES (
        :id, :source_system, :source_instance_id, :external_deal_id, :unit_id,
        :status, :source_status, :reserved_at, :sold_at, :lost_at,
        :source_revision, :source_updated_at, :deleted_at, :created_at, :updated_at
    )
    ON CONFLICT (id) DO UPDATE SET
        status            = EXCLUDED.status,
        source_status     = EXCLUDED.source_status,
        reserved_at       = EXCLUDED.reserved_at,
        sold_at           = EXCLUDED.sold_at,
        lost_at           = EXCLUDED.lost_at,
        source_revision   = EXCLUDED.source_revision,
        source_updated_at = EXCLUDED.source_updated_at,
        deleted_at        = NULL,
        updated_at        = GREATEST(EXCLUDED.updated_at, deals.created_at)
    """
)
