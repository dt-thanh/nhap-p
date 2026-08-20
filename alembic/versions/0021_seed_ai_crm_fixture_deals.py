"""DEV-only AI/CRM deal fixture, derived from the seeded units + legacy trend

Revision ID: 0021_seed_ai_crm_fixture_deals
Revises: 0020_agent_advisory_execution
Create Date: 2026-08-15

A DATA migration, not a schema migration — no table/column/constraint is added,
altered, or dropped. It fills the ONE gap that migration 0019 deliberately left
open ("`deals` — zero deal-level records exist in the source; none fabricated")
and that everything downstream is starving on:

  * `GET /inventory` totals read `deals`, so ĐÃ BÁN / ĐANG GIỮ were stuck at 0
    while the unit list showed 41 `reserved` units — one page, two answers.
  * `src/ranking/service.py` computes 3 of its 4 features from `deals`
    (`has_active_deal`, `area_velocity_norm`, `area_conversion_norm`). With
    zero deals the whole engine collapsed to exactly TWO distinct scores
    (0.7000 = available, 0.2000 = reserved) — a restatement of `units.status`.
  * The LangGraph advisory agent reads that ranking, so it could only ever
    paraphrase "this unit is empty".

╔══════════════════════════════════════════════════════════════════════════╗
║  This is DEV/AI fixture data, NOT proof of the Mini CRM sync pipeline.    ║
║  It writes DIRECTLY to Backend tables via SQLAlchemy Core — the same      ║
║  sanctioned dev-seed path 0019 uses. It does NOT exercise                 ║
║  Mini CRM → crm_outbox → relay → DomainProjector, which is debugged and   ║
║  tested completely separately.                                           ║
╚══════════════════════════════════════════════════════════════════════════╝

Fixture identity — identical to 0019, so the two seed together and downgrade
together, and neither can be confused with real Mini CRM-synced rows:

    source_system      = "crm_real_data_fixture"
    source_instance_id = "ai-dev-fixture"

`mini-crm-dev` (the real instance id) can never produce or match these values.

NOT DERIVED FROM A JSON FILE — and that is deliberate, twice over:

 1. Nothing is invented. Every deal is projected from data ALREADY in the
    database: the `units` rows 0019 seeded, and the weekly `units_sold` shape
    of the `legacy_aggregate` lineage in `absorption_daily` (itself derived
    from the source's real `trend_by_area`). Which areas sell faster, and in
    which weeks, is the source's own signal — this migration only materializes
    it per-unit, scaled to the unit supply that actually exists.
 2. No `scripts/` import. 0019 imports `scripts._seed_ai_crm_fixture_core`,
    but `docker-compose.yml` bind-mounts only `./src`, `./alembic`, `./data`,
    `./uploads` — `scripts/` is baked into the image at build time. Editing a
    seed module without rebuilding therefore crash-loops the `api` container on
    `alembic upgrade head` (observed live: ModuleNotFoundError → exit 1 →
    restart, port 8000 never opens). Reading state through `op.get_bind()`
    keeps this revision inside the bind-mounted `alembic/` tree and immune to
    that whole failure class.

SUPPLY CONSTRAINT, stated plainly: the fixture materialized ~10% of each area's
planned `total_units` (e.g. ar_0007 declares 3360 units, 40 rows exist). The
legacy trend sells 144 units over 12 weeks in that area — more than exist. So
deals are allocated PROPORTIONALLY, preserving the relative intensity between
areas (the part ranking and the agent actually consume) rather than the
absolute counts, which the unit supply cannot honour. See `_sold_target`.

Idempotent: id = `uuid5(NS_INGESTION_SEED, "deal:<external_deal_id>")` and every
insert is `ON CONFLICT (id) DO UPDATE`. Re-running produces the same counts.

`downgrade()` restores every unit this revision flipped to 'sold' back to
'available' (identified through the fixture deals themselves, recomputed from
the database), THEN deletes only rows carrying the fixture identity above. It
never touches a row without that marker.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "0021_seed_ai_crm_fixture_deals"
down_revision: str | None = "0020_agent_advisory_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same namespace and identity as 0019 — see module docstring.
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
    """Split `total` across buckets by `weights` (largest-remainder).

    Preserves the weekly SHAPE of the legacy trend: a week that sold twice as
    many units in the source gets roughly twice as many fixture deals.

    `salt` breaks remainder TIES deterministically but without positional bias.
    The legacy trend is near-flat per area, so a naive stable sort hands every
    remainder to the earliest weeks — which front-loads all sales into the
    oldest dates and leaves `area_velocity_norm`'s trailing-30-day window
    empty, i.e. exactly the dead feature this revision exists to revive.
    """
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
    """How many available units of this area become sold.

    `intensity` = the area's 12-week trend absorption (trend_sold / total_units)
    — the source's own measure of how fast this area moves. Scaled against the
    hottest area so relative ordering survives the supply shortfall.
    """
    if available <= 0 or max_intensity <= 0:
        return 0
    share = MAX_SOLD_SHARE * (intensity / max_intensity)
    keep = max(2, round(available * MIN_AVAILABLE_SHARE))
    return max(0, min(round(available * share), available - keep))


def _plan(bind) -> tuple[list[dict], list[uuid.UUID], dict[str, int]]:
    """Build every deal row from state already in the database."""
    now = datetime.now(UTC)

    areas = bind.execute(
        sa.text(
            """
            SELECT a.id, a.external_id, a.total_units
            FROM areas a
            WHERE a.external_id IS NOT NULL
            ORDER BY a.external_id
            """
        )
    ).mappings().all()

    # `status` here is the PRE-MIGRATION status: a unit this revision already
    # flipped to 'sold' is read back as 'available'. Without that rewind the
    # plan would be a function of state this same migration mutates, and a
    # second `upgrade()` would sell a fresh cohort out of the units the first
    # one left available — breaking the idempotency contract 0019 established.
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

    # Relative intensity across areas — the signal the ranking engine consumes.
    intensity: dict[uuid.UUID, float] = {}
    for area in areas:
        weeks = trend_by_area.get(area["id"], [])
        total_units = max(int(area["total_units"] or 0), 1)
        intensity[area["id"]] = sum(int(w["units_sold"]) for w in weeks) / total_units
    max_intensity = max(intensity.values(), default=0.0)

    deals: list[dict] = []
    sold_unit_ids: list[uuid.UUID] = []
    counts = {"sold": 0, "reserved": 0, "lost": 0, "funnel": 0}

    for area in areas:
        area_id = area["id"]
        units = units_by_area.get(area_id, [])
        if not units:
            continue
        weeks = trend_by_area.get(area_id, [])
        available = [u for u in units if u["status"] == "available"]
        reserved = [u for u in units if u["status"] == "reserved"]

        # --- reserved: one holding deal per already-reserved unit -----------
        # This is what closes the "41 units held, 0 deals" contradiction.
        for u in reserved:
            ext = f"D-{u['external_unit_id']}-01"
            reserved_at = now - timedelta(days=_n(f"resv:{ext}", 3, 75), hours=_n(f"rh:{ext}", 0, 23))
            deals.append(
                _row(ext, u["id"], "reserved", "reserved", reserved_at=reserved_at, now=now)
            )
            counts["reserved"] += 1

        # --- sold: proportional share, spread by the legacy weekly shape ----
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
                sold_at = datetime.combine(
                    week["stat_date"], datetime.min.time(), tzinfo=UTC
                ) + timedelta(hours=_n(f"sh:{ext}", 8, 18), minutes=_n(f"sm:{ext}", 0, 59))
                reserved_at = sold_at - timedelta(days=_n(f"sr:{ext}", 7, 45))
                deals.append(
                    _row(ext, u["id"], "sold", "sold", reserved_at=reserved_at, sold_at=sold_at, now=now)
                )
                sold_unit_ids.append(u["id"])
                emitted += 1
                counts["sold"] += 1

        # --- lost: history on units that went back on the market -----------
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

        # --- early funnel: non-holding, so they never occupy a unit ---------
        for i in range(0, len(rest), FUNNEL_EVERY):
            u = rest[i]
            ext = f"D-{u['external_unit_id']}-03"
            status = FUNNEL_STATUSES[_n(f"fs:{ext}", 0, len(FUNNEL_STATUSES) - 1)]
            deals.append(_row(ext, u["id"], status, status, now=now))
            counts["funnel"] += 1

    return deals, sold_unit_ids, counts


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
) -> dict:
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


def upgrade() -> None:
    bind = op.get_bind()
    deals, sold_unit_ids, counts = _plan(bind)

    if not deals:
        raise RuntimeError(
            "0021_seed_ai_crm_fixture_deals: no fixture units found "
            f"(source_instance_id={SOURCE_INSTANCE_ID!r}). Apply 0019 first."
        )

    for row in deals:
        bind.execute(UPSERT, row)

    # `units.status` mirrors the deals — otherwise the inventory table and the
    # totals cards go on disagreeing, just in the opposite direction.
    if sold_unit_ids:
        # GREATEST(clock_timestamp(), created_at), not now(): `now()` is the
        # TRANSACTION start time, and on a fresh database this revision can run
        # inside the same transaction as 0019 — whose `units.created_at` came
        # from a later Python-side clock read. `now()` then lands BEFORE
        # created_at and `ck_units_updated_after_created` fires. Caught on a
        # from-scratch test database; the dev database never hit it because
        # there 0019 had committed hours earlier.
        bind.execute(
            sa.text(
                "UPDATE units SET status='sold', "
                "updated_at = GREATEST(clock_timestamp(), created_at) "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": sold_unit_ids},
        )

    print("=== 0021_seed_ai_crm_fixture_deals: mapping report ===")
    for name, n in counts.items():
        print(f"  [{name}] {n} deals")
    print(f"  [units→sold] {len(sold_unit_ids)} units flipped available→sold")
    print(f"  [total] {len(deals)} deals upserted")


def downgrade() -> None:
    bind = op.get_bind()

    # Order matters: identify the units through the fixture deals BEFORE
    # deleting those deals, or there is nothing left to identify them by.
    bind.execute(
        sa.text(
            """
            UPDATE units SET status='available',
                             updated_at = GREATEST(clock_timestamp(), created_at)
            WHERE status = 'sold'
              AND id IN (
                  SELECT unit_id FROM deals
                  WHERE source_system = :sys AND source_instance_id = :inst
                    AND status = 'sold'
              )
            """
        ),
        {"sys": SOURCE_SYSTEM, "inst": SOURCE_INSTANCE_ID},
    )
    bind.execute(
        sa.text("DELETE FROM deals WHERE source_system = :sys AND source_instance_id = :inst"),
        {"sys": SOURCE_SYSTEM, "inst": SOURCE_INSTANCE_ID},
    )
