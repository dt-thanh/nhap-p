"""Generate + validate the synthetic CSV dataset in `datasets/synthetic_v1/`.

    python -m scripts.generate_synthetic_dataset [--reference-date 2026-08-18] [--out datasets/synthetic_v1]

Standalone and side-effect free outside the output directory: it NEVER opens a
database connection, never reads a live/dev/staging dataset, and never writes to
any table. Every row is derived from the schema itself — `src/models/tables.py`
for exact column names, `alembic/versions/*` for the CHECK/UNIQUE/FK rules the
values must satisfy.

Why a CSV generator rather than another seed migration: `0019`/`0021`/`0023`
already seed demo rows *inside* migrations (see `pipeline_status.md` §7.15), so a
freshly migrated database is not empty. This dataset therefore lives in its own
reserved source namespace (`SOURCE_INSTANCE_ID` below) that is disjoint from
`ai-dev-fixture` (0019) and `synthetic-demo-2026` (0023), so the two can coexist
without violating `uq_{units,deals}_source_identity` /
`uq_{projects,areas}_source_identity`.

Scoring proof: the ranking numbers in `validation_report.md` are produced by
importing the REAL scorer (`src.ranking.engine.score_unit` / `rank_scores` and
`src.ranking.bands.band_for`), which is a pure function with no I/O — asserted by
`tests/test_ranking_boundary.py::test_ranking_engine_is_a_pure_function_no_db_no_network`.
Only the feature-derivation step is ported here, because the real one
(`src/ranking/service.py::_area_features` / `_funnel_deal_counts`) is SQL and
needs a database; the port follows those functions' documented formulas exactly,
including `VELOCITY_SATURATION`/`DEMAND_SATURATION`, which are imported from the
service module rather than retyped.

NULL convention: `\\N` (the PostgreSQL `COPY` default). An empty field is a
literal empty string, which several NOT NULL text columns legitimately use
(`projects.headline`, `areas.introduce`, ...). Load with
`COPY <t> (<cols>) FROM '<f>' WITH (FORMAT csv, HEADER true, NULL '\\N')`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.models.tables import (
    areas as t_areas,
)
from src.models.tables import (
    crm_source_records as t_crm_source_records,
)
from src.models.tables import (
    deals as t_deals,
)
from src.models.tables import (
    inventory_snapshots as t_inventory_snapshots,
)
from src.models.tables import (
    projects as t_projects,
)
from src.models.tables import (
    sales_records as t_sales_records,
)
from src.models.tables import (
    units as t_units,
)
from src.models.tables import (
    upload_errors as t_upload_errors,
)
from src.models.tables import (
    upload_files as t_upload_files,
)
from src.ranking.bands import band_for
from src.ranking.engine import FeatureWeight, UnitFeatureInput, rank_scores, score_unit
from src.ranking.service import DEMAND_SATURATION, FUNNEL_STATUSES, VELOCITY_SATURATION

# --- Reserved identity namespace --------------------------------------------
# Disjoint from every namespace an existing seed already owns, so this dataset
# can be loaded next to them: 0019 uses ("crm_real_data_fixture", "ai-dev-fixture"),
# 0023 uses ("synthetic_demo", "synthetic-demo-2026"), the relay uses
# ("mini_crm", "mini-crm-dev").
SOURCE_SYSTEM = "synthetic_csv"
SOURCE_INSTANCE_ID = "synthetic-csv-v1"
EXT = "syn1"
UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://absorptionforecast.local/datasets/synthetic_v1")

NULL = "\\N"
SEED = 20260818

# Published ranking config v2 (alembic 0022_ranking_config_v2). Mirrored here to
# score the dataset offline; the loader does NOT ship a ranking_configs.csv,
# because migrations 0014/0022 already insert v1+v2 and `uq_ranking_configs_version`
# would reject a duplicate.
V2_WEIGHTS = [
    FeatureWeight(key="unit_available", weight=Decimal("0.35"), direction="positive", missing_value_policy="zero"),
    FeatureWeight(key="unit_demand_norm", weight=Decimal("0.25"), direction="positive", missing_value_policy="zero"),
    FeatureWeight(key="area_velocity_norm", weight=Decimal("0.20"), direction="positive", missing_value_policy="neutral"),
    FeatureWeight(key="area_conversion_norm", weight=Decimal("0.20"), direction="positive", missing_value_policy="neutral"),
]
V2_MIN_COVERAGE = Decimal("0.5")


def det_uuid(*parts: str) -> str:
    return str(uuid.uuid5(UUID_NS, "|".join(parts)))


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- Recipes -----------------------------------------------------------------
# One recipe per area. The numbers are chosen so the four ranking features land
# in ranges that produce all three bands; `validation_report.md` reports the
# scores actually computed, not these intentions.


@dataclass
class AreaRecipe:
    key: str
    area_name: str
    code_prefix: str  # unit_code stem, unique within the area
    unit_type: str
    bedrooms: int
    area_sqm: str
    total_units: int
    n_units: int
    n_sold: int
    n_sold_recent: int  # subset of n_sold with sold_at inside the 30d window
    n_reserved: int
    n_blocked: int
    n_lost: int
    funnel_plan: list[int]  # funnel deals per available unit, longest first
    legacy: bool = False  # NULL source identity (pre-Phase-D area)
    legacy_csv: bool = False  # also emit sales_records / inventory_snapshots
    note: str = ""


@dataclass
class ProjectRecipe:
    key: str
    name: str
    calculator: str
    headline: str
    introduce: str
    areas: list[AreaRecipe] = field(default_factory=list)


RECIPES = [
    ProjectRecipe(
        key="P-001",
        name="Harbor Crest Residences",
        calculator="domain_units_deals",
        headline="Waterfront apartments with staged handover",
        introduce="Synthetic flagship project exercising the domain_units_deals calculator end to end.",
        areas=[
            AreaRecipe(
                key="A-001", code_prefix="HC1", area_name="Harbor Crest 1", unit_type="2BR", bedrooms=2, area_sqm="68.50",
                total_units=120, n_units=40, n_sold=14, n_sold_recent=9, n_reserved=3, n_blocked=0, n_lost=3,
                funnel_plan=[5, 4, 3, 3, 3, 2, 2, 1, 1, 1],
                note="HOT: saturated 30d velocity, several units with 3+ funnel deals -> high band",
            ),
            AreaRecipe(
                key="A-002", code_prefix="HC2", area_name="Harbor Crest 2", unit_type="3BR", bedrooms=3, area_sqm="94.00",
                total_units=90, n_units=30, n_sold=6, n_sold_recent=3, n_reserved=2, n_blocked=0, n_lost=4,
                funnel_plan=[2, 2, 1, 1, 1, 1],
                note="MODERATE: mid velocity/conversion -> medium band",
            ),
            AreaRecipe(
                key="A-003", code_prefix="HC3", area_name="Harbor Crest 3", unit_type="1BR", bedrooms=1, area_sqm="45.20",
                total_units=80, n_units=24, n_sold=2, n_sold_recent=0, n_reserved=1, n_blocked=1, n_lost=8,
                funnel_plan=[1, 1],
                note="SLOW: zero 30d sales -> velocity 0, low conversion",
            ),
            AreaRecipe(
                key="A-004", code_prefix="HC4", area_name="Harbor Crest 4", unit_type="Studio", bedrooms=0, area_sqm="32.00",
                total_units=60, n_units=12, n_sold=0, n_sold_recent=0, n_reserved=0, n_blocked=2, n_lost=0,
                funnel_plan=[],
                note="EMPTY: no deals at all -> area features MISSING -> neutral(0.5) policy exercised",
            ),
        ],
    ),
    ProjectRecipe(
        key="P-002",
        name="Willow Park Gardens",
        calculator="legacy_aggregate",
        headline="Garden low-rise, phased release",
        introduce="Synthetic project on the legacy_aggregate calculator, with CSV-ingested sales history.",
        areas=[
            AreaRecipe(
                key="A-005", code_prefix="WPA", area_name="Willow Park A", unit_type="2BR", bedrooms=2, area_sqm="70.00",
                total_units=100, n_units=30, n_sold=9, n_sold_recent=5, n_reserved=2, n_blocked=0, n_lost=3,
                funnel_plan=[3, 2, 2, 1, 1], legacy_csv=True,
                note="Dual lineage: units/deals AND sales_records/inventory_snapshots -> parallel-run comparison",
            ),
            AreaRecipe(
                key="A-006", code_prefix="WPB", area_name="Willow Park B", unit_type="3BR", bedrooms=3, area_sqm="98.75",
                total_units=70, n_units=20, n_sold=4, n_sold_recent=2, n_reserved=1, n_blocked=1, n_lost=2,
                funnel_plan=[2, 1, 1], legacy_csv=True,
                note="Dual lineage, smaller sample",
            ),
            AreaRecipe(
                key="A-007", code_prefix="WPC", area_name="Willow Park C", unit_type="1BR", bedrooms=1, area_sqm="47.00",
                total_units=50, n_units=16, n_sold=3, n_sold_recent=1, n_reserved=0, n_blocked=0, n_lost=1,
                funnel_plan=[1, 1], legacy_csv=True,
                note="Legacy CSV only lineage plus a thin domain lineage",
            ),
        ],
    ),
    ProjectRecipe(
        key="P-003",
        name="Quarry Fields Commons",
        calculator="domain_units_deals",
        headline="Hillside commons, limited release",
        introduce="Synthetic edge-case project: blocked inventory and one pre-Phase-D legacy area.",
        areas=[
            AreaRecipe(
                key="A-008", code_prefix="QFN", area_name="Quarry Fields North", unit_type="2BR", bedrooms=2, area_sqm="66.00",
                total_units=40, n_units=18, n_sold=3, n_sold_recent=2, n_reserved=1, n_blocked=4, n_lost=2,
                funnel_plan=[3, 1],
                note="Blocked-heavy inventory -> unit_available=0 cluster",
            ),
            AreaRecipe(
                key="A-009", code_prefix="QFL", area_name="Quarry Fields Legacy", unit_type="2BR", bedrooms=2, area_sqm="64.00",
                total_units=30, n_units=10, n_sold=1, n_sold_recent=0, n_reserved=0, n_blocked=0, n_lost=1,
                funnel_plan=[1], legacy=True,
                note="LEGACY AREA: external_id/source_* NULL (pre-Phase-D), still rankable",
            ),
        ],
    ),
]


class Builder:
    """Builds every table's rows for one reference date. Pure in-memory."""

    def __init__(self, reference: datetime) -> None:
        self.ref = reference
        self.rng = random.Random(SEED)
        self.rows: dict[str, list[dict]] = {name: [] for name in TABLE_ORDER}
        self.deal_seq = 0
        # side tables used by the offline scorer
        self.units_by_area: dict[str, list[dict]] = {}
        self.deals_by_unit: dict[str, list[dict]] = {}
        self.area_project: dict[str, str] = {}

    # -- helpers
    def _ts(self, days_ago: float) -> datetime:
        return self.ref - timedelta(days=days_ago)

    def _next_deal_ext(self) -> str:
        self.deal_seq += 1
        return f"{EXT}-D-{self.deal_seq:06d}"

    # -- builders
    def build(self) -> None:
        batches = self._build_upload_files()
        for p in RECIPES:
            project_id = det_uuid("project", p.key)
            created = self._ts(400)
            self.rows["projects"].append(
                {
                    "id": project_id,
                    "name": p.name,
                    "launch_date": (self.ref - timedelta(days=380)).date().isoformat(),
                    "created_at": iso(created),
                    "status": "active",
                    "headline": p.headline,
                    "introduce": p.introduce,
                    "cover_image_url": NULL,
                    "cover_image_public_id": NULL,
                    # FK -> users.id (0002). `users` is excluded from this dataset,
                    # so these stay NULL rather than inventing an actor row.
                    "created_by": NULL,
                    "reviewed_by": NULL,
                    "reviewed_at": NULL,
                    "review_reason": NULL,
                    "absorption_calculator": p.calculator,
                    "external_id": f"{EXT}-{p.key}",
                    "source_system": SOURCE_SYSTEM,
                    "source_instance_id": SOURCE_INSTANCE_ID,
                    "source_revision": 3,
                    "source_updated_at": iso(self._ts(30)),
                    "updated_at": iso(self._ts(30)),
                }
            )
            for a in p.areas:
                self._build_area(p, a, project_id, batches)

    def _build_upload_files(self) -> dict[str, str]:
        """Batch ledger. Parent of sales_records/inventory_snapshots/upload_errors
        and of crm_source_records' two sync-run FKs."""
        specs = [
            ("csv-p002-sales", "P-002", "willow_park_sales_2026Q2.csv", "csv", "file_upload", "incremental",
             "completed", "sales", 240, 240, 0, None),
            ("csv-p002-inventory", "P-002", "willow_park_inventory_2026Q2.csv", "csv", "file_upload", "incremental",
             "completed", "inventory", 66, 66, 0, None),
            ("api-p001-units", "P-001", None, "json", "api_push", "incremental",
             "completed", "units", 106, 106, 0, f"{EXT}-batch-units-0001"),
            ("api-p001-deals", "P-001", None, "json", "api_push", "incremental",
             "partially_completed", "deals", 60, 57, 3, f"{EXT}-batch-deals-0001"),
            ("api-p003-units", "P-003", None, "json", "api_push", "full_snapshot",
             "failed", "units", 28, 0, 28, f"{EXT}-batch-units-0002"),
        ]
        out: dict[str, str] = {}
        for key, pkey, filename, fmt, transport, mode, status, entity, received, ok, failed, batch in specs:
            fid = det_uuid("upload_file", key)
            out[key] = fid
            uploaded = self._ts(45)
            finished = None if status == "pending" else uploaded + timedelta(minutes=4)
            summary = {} if failed == 0 else {"rejected": failed, "reason": "contract_validation"}
            self.rows["upload_files"].append(
                {
                    "id": fid,
                    "project_id": det_uuid("project", pkey),
                    # FK -> users.id; excluded table, so NULL (matches MVP-1 behaviour).
                    "uploaded_by": NULL,
                    "filename": filename if filename else NULL,
                    # uq_upload_files_project_checksum: unique per project, NULL for api_push.
                    "checksum": sha256_text(f"{key}|{filename}") if filename else NULL,
                    "status": status,
                    "rows_ok": ok,
                    "rows_failed": failed,
                    "uploaded_at": iso(uploaded),
                    "source_system": SOURCE_SYSTEM,
                    "source_instance_id": SOURCE_INSTANCE_ID,
                    "source_entity": entity,
                    "input_format": fmt,
                    "transport_mode": transport,
                    "sync_mode": mode,
                    "schema_version": 1,
                    "external_batch_id": batch if batch else NULL,
                    "rows_received": received,
                    "finished_at": iso(finished) if finished else NULL,
                    "last_source_cursor": NULL,
                    "error_summary": json.dumps(summary, separators=(",", ":")),
                    "snapshot_id": f"{EXT}-snap-0001" if mode == "full_snapshot" else NULL,
                    # 0-based: ck_upload_files_chunk_index_within_total requires chunk_index < chunk_total
                    "chunk_index": 0 if mode == "full_snapshot" else NULL,
                    "chunk_total": 1 if mode == "full_snapshot" else NULL,
                    "snapshot_complete": "true" if mode == "full_snapshot" else NULL,
                    "snapshot_scope": json.dumps({"entity": entity}, separators=(",", ":"))
                    if mode == "full_snapshot"
                    else NULL,
                }
            )
        self._build_upload_errors(out)
        self._build_crm_source_records(out)
        return out

    def _build_upload_errors(self, batches: dict[str, str]) -> None:
        """ck_upload_errors_locator: row_number IS NOT NULL OR json_path IS NOT NULL
        OR error_category IN ('transport','schema'). row_number must be > 0."""
        specs = [
            ("e1", "api-p001-deals", None, "$.records[7].unit_ref", "business", "UNKNOWN_UNIT_REF",
             "Deal references a unit that is not mirrored yet", "open", f"{EXT}-D-ORPHAN-1"),
            ("e2", "api-p001-deals", None, "$.records[19].deal_status", "field", "INVALID_ENUM",
             "deal_status 'negotiating' is not in the accepted vocabulary", "permanent", f"{EXT}-D-ORPHAN-2"),
            ("e3", "api-p001-deals", None, "$.records[41].sold_at", "business", "HISTORY_TIMESTAMP_DROPPED",
             "sold_at absent on a record that previously carried one", "resolved", f"{EXT}-D-ORPHAN-3"),
            ("e4", "api-p003-units", None, None, "transport", "UPSTREAM_TIMEOUT",
             "Upstream closed the connection before the batch finished", "retrying", NULL),
            ("e5", "csv-p002-sales", 118, None, "field", "UNPARSEABLE_DATE",
             "sold_date '31/02/2026' is not a valid calendar date", "permanent", NULL),
        ]
        for key, batch_key, row_no, jpath, category, code, message, retry, rec_id in specs:
            self.rows["upload_errors"].append(
                {
                    "id": det_uuid("upload_error", key),
                    "file_id": batches[batch_key],
                    "row_number": row_no if row_no is not None else NULL,
                    "column_name": NULL,
                    "error_code": code,
                    "message": message,
                    "created_at": iso(self._ts(45)),
                    "error_category": category,
                    "json_path": jpath if jpath else NULL,
                    "source_record_id": rec_id,
                    "record_locator": NULL,
                    "field_name": NULL,
                    "raw_value_redacted": NULL,
                    "retry_status": retry,
                    "resolved_at": iso(self._ts(44)) if retry == "resolved" else NULL,
                }
            )

    def _build_crm_source_records(self, batches: dict[str, str]) -> None:
        """Idempotency / reconciliation edge cases: one row per DECISIONS value
        plus both MIRROR_STATES, so the sync decision surface has real data."""
        first = batches["api-p001-units"]
        last = batches["api-p001-deals"]
        specs = [
            ("insert", "units", f"{EXT}-U-000001", "active", "insert", 0),
            ("update", "units", f"{EXT}-U-000002", "active", "update", 0),
            ("skip_stale", "units", f"{EXT}-U-000003", "active", "skip_stale", 0),
            ("duplicate_noop", "units", f"{EXT}-U-000004", "active", "duplicate_noop", 0),
            ("conflict", "units", f"{EXT}-U-000005", "active", "conflict", 2),
            ("tombstone", "units", f"{EXT}-U-000006", "tombstoned", "tombstone", 0),
            ("deal_insert", "deals", f"{EXT}-D-000001", "active", "insert", 0),
        ]
        for key, entity, rec_id, state, decision, conflicts in specs:
            seen_first = self._ts(60)
            seen_last = self._ts(30)
            self.rows["crm_source_records"].append(
                {
                    "id": det_uuid("crm_source_record", key),
                    "source_system": SOURCE_SYSTEM,
                    "source_instance_id": SOURCE_INSTANCE_ID,
                    "source_entity": entity,
                    "source_record_id": rec_id,
                    "first_sync_run_id": first,
                    "last_sync_run_id": last,
                    "external_batch_id": f"{EXT}-batch-{entity}-0001",
                    "source_revision": 4 if decision != "skip_stale" else 2,
                    "source_updated_at": iso(seen_last),
                    "payload_hash": sha256_text(f"{rec_id}|{decision}"),
                    "state": state,
                    "last_decision": decision,
                    "conflict_count": conflicts,
                    "conflict_payload_hash": sha256_text(f"{rec_id}|conflict") if conflicts else NULL,
                    "conflict_detected_at": iso(seen_last) if conflicts else NULL,
                    "first_seen_at": iso(seen_first),
                    "last_seen_at": iso(seen_last),
                    "deleted_at": iso(seen_last) if state == "tombstoned" else NULL,
                }
            )

    def _build_area(self, p: ProjectRecipe, a: AreaRecipe, project_id: str, batches: dict[str, str]) -> None:
        area_id = det_uuid("area", a.key)
        self.area_project[area_id] = project_id
        created = self._ts(390)
        self.rows["areas"].append(
            {
                "id": area_id,
                "project_id": project_id,
                "area_name": a.area_name,
                "unit_type": a.unit_type,
                "bedrooms": a.bedrooms,
                "area_sqm": a.area_sqm,
                "total_units": a.total_units,
                "created_at": iso(created),
                "status": "active",
                "headline": "",
                "introduce": "",
                "cover_image_url": NULL,
                "cover_image_public_id": NULL,
                "created_by": NULL,
                "reviewed_by": NULL,
                "reviewed_at": NULL,
                "review_reason": NULL,
                # Legacy area: pre-Phase-D, no source identity. NULLs do not collide
                # under uq_areas_source_identity (Postgres treats NULL as distinct).
                "external_id": NULL if a.legacy else f"{EXT}-{a.key}",
                "source_system": NULL if a.legacy else SOURCE_SYSTEM,
                "source_instance_id": NULL if a.legacy else SOURCE_INSTANCE_ID,
                "source_revision": NULL if a.legacy else 2,
                "source_updated_at": NULL if a.legacy else iso(self._ts(30)),
                "updated_at": iso(self._ts(30)),
            }
        )

        unit_ids = self._build_units(a, area_id)
        self._build_deals(a, area_id, unit_ids)
        if a.legacy_csv:
            self._build_legacy_csv(a, area_id, batches)

    def _build_units(self, a: AreaRecipe, area_id: str) -> list[dict]:
        """Status mix is decided here so deals can be made consistent with it:
        `sold` units get exactly one sold deal, `reserved` exactly one reserved
        deal (uq_deals_active_per_unit allows at most one of either), `blocked`
        gets none, `available` may carry any number of funnel/lost deals."""
        statuses: list[str] = (
            ["sold"] * a.n_sold + ["reserved"] * a.n_reserved + ["blocked"] * a.n_blocked
        )
        statuses += ["available"] * (a.n_units - len(statuses))
        assert len(statuses) == a.n_units, f"{a.key}: unit status plan overflows n_units"

        out = []
        for i, status in enumerate(statuses, start=1):
            ext = f"{EXT}-U-{a.key}-{i:03d}"
            uid = det_uuid("unit", ext)
            created = self._ts(370 - i * 0.5)
            row = {
                "id": uid,
                "source_system": SOURCE_SYSTEM,
                "source_instance_id": SOURCE_INSTANCE_ID,
                "external_unit_id": ext,
                "area_id": area_id,
                # uq_units_area_unit_code (partial, deleted_at IS NULL)
                "unit_code": f"{a.code_prefix}-{i:03d}",
                "unit_type": a.unit_type,
                "status": status,
                "source_revision": 2,
                "source_updated_at": iso(self._ts(30)),
                "deleted_at": NULL,
                "created_at": iso(created),
                "updated_at": iso(self._ts(30)),
            }
            self.rows["units"].append(row)
            out.append(row)
        self.units_by_area[area_id] = out
        return out

    def _build_deals(self, a: AreaRecipe, area_id: str, unit_rows: list[dict]) -> None:
        sold_units = [u for u in unit_rows if u["status"] == "sold"]
        reserved_units = [u for u in unit_rows if u["status"] == "reserved"]
        available_units = [u for u in unit_rows if u["status"] == "available"]

        def add(unit: dict, status: str, source_status: str, *, reserved_at=None, sold_at=None, lost_at=None,
                created_days: float = 90.0) -> None:
            ext = self._next_deal_ext()
            created = self._ts(created_days)
            row = {
                "id": det_uuid("deal", ext),
                "source_system": SOURCE_SYSTEM,
                "source_instance_id": SOURCE_INSTANCE_ID,
                "external_deal_id": ext,
                "unit_id": unit["id"],
                "status": status,
                "source_status": source_status,
                "reserved_at": iso(reserved_at) if reserved_at else NULL,
                "sold_at": iso(sold_at) if sold_at else NULL,
                "lost_at": iso(lost_at) if lost_at else NULL,
                "source_revision": 2,
                "source_updated_at": iso(self._ts(min(created_days, 20))),
                "deleted_at": NULL,
                "created_at": iso(created),
                "updated_at": iso(self._ts(min(created_days, 20))),
            }
            self.rows["deals"].append(row)
            self.deals_by_unit.setdefault(unit["id"], []).append(row)

        # sold: n_sold_recent inside the 30d window, the rest spread to ~120d back
        # so absorption_daily builds >=30 points and reaches data_quality 'ok'.
        for i, unit in enumerate(sold_units):
            if i < a.n_sold_recent:
                days = 3 + i * 3  # 3..~29 days ago
            else:
                days = 35 + (i - a.n_sold_recent) * 9  # 35..~120 days ago
            sold_at = self._ts(days)
            reserved_at = sold_at - timedelta(days=12)
            add(unit, "sold", "sold", reserved_at=reserved_at, sold_at=sold_at, created_days=days + 30)

        for i, unit in enumerate(reserved_units):
            reserved_at = self._ts(10 + i * 5)
            add(unit, "reserved", "reserved", reserved_at=reserved_at, created_days=25 + i * 5)

        # funnel deals drive unit_demand_norm = min(count / DEMAND_SATURATION, 1)
        funnel_cycle = list(FUNNEL_STATUSES)
        for idx, count in enumerate(a.funnel_plan):
            if idx >= len(available_units):
                break
            unit = available_units[idx]
            for k in range(count):
                status = funnel_cycle[k % len(funnel_cycle)]
                add(unit, status, status, created_days=20 + k * 4)

        # lost deals: history that must NOT count as demand and must NOT trip
        # uq_deals_active_per_unit. Spread across available units.
        for i in range(a.n_lost):
            if not available_units:
                break
            unit = available_units[(i * 3 + 1) % len(available_units)]
            lost_at = self._ts(40 + i * 7)
            add(unit, "lost", "cancelled", lost_at=lost_at, created_days=80 + i * 7)

    def _build_legacy_csv(self, a: AreaRecipe, area_id: str, batches: dict[str, str]) -> None:
        """sales_records + inventory_snapshots — the legacy_aggregate calculator's
        inputs, independent of units/deals. Gives the parallel-run comparison two
        real lineages to diff."""
        sales_file = batches["csv-p002-sales"]
        inv_file = batches["csv-p002-inventory"]
        remaining = a.total_units

        for w in range(12):  # 12 weekly points -> ~84 days of history
            sold_date = (self.ref - timedelta(days=84 - w * 7)).date()
            units_sold = 1 + (self.rng.randrange(0, 4) if w % 3 else 0)
            remaining = max(remaining - units_sold, 0)
            ext_record = f"{EXT}-SR-{a.key}-{w:02d}"
            self.rows["sales_records"].append(
                {
                    "id": det_uuid("sales_record", ext_record),
                    "area_id": area_id,
                    "file_id": sales_file,
                    "sold_date": sold_date.isoformat(),
                    "units_sold": units_sold,
                    # uq_sales_area_date_external_id + uq_sales_area_source_row_hash
                    "external_record_id": ext_record,
                    "source_row_hash": sha256_text(f"{ext_record}|{sold_date}|{units_sold}"),
                    "created_at": iso(self._ts(45)),
                    "source_updated_at": iso(self._ts(45)),
                }
            )
            # uq_inventory_area_date_type: one row per (area, date, type)
            self.rows["inventory_snapshots"].append(
                {
                    "id": det_uuid("inventory_snapshot", f"{ext_record}-closing"),
                    "area_id": area_id,
                    "file_id": inv_file,
                    "snapshot_date": sold_date.isoformat(),
                    "units_remaining": remaining,
                    "snapshot_type": "closing",
                    "source_row_hash": sha256_text(f"{ext_record}|closing|{remaining}"),
                    "created_at": iso(self._ts(45)),
                    "source_updated_at": iso(self._ts(45)),
                }
            )


# --- Offline ranking, using the REAL engine ----------------------------------


def derive_features(builder: Builder) -> dict[str, list[UnitFeatureInput]]:
    """Port of `src/ranking/service.py` feature derivation (the original is SQL).

    Mirrors `_area_features` (30d sold window, denominator = live mirrored units,
    absent area => MISSING not 0) and `_funnel_deal_counts` (0 is a measured
    fact, not missing), and reuses that module's saturation constants.
    """
    window_start = builder.ref - timedelta(days=30)
    by_project: dict[str, list[UnitFeatureInput]] = {}

    for area_id, unit_rows in builder.units_by_area.items():
        project_id = builder.area_project[area_id]
        live_units = len(unit_rows)

        alive = 0
        sold = 0
        sold_30d = 0
        for u in unit_rows:
            for d in builder.deals_by_unit.get(u["id"], []):
                alive += 1
                if d["status"] == "sold":
                    sold += 1
                    if datetime.strptime(d["sold_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) >= window_start:
                        sold_30d += 1

        if alive == 0:
            velocity = None
            conversion = None
        else:
            velocity = min((Decimal(sold_30d) / max(live_units, 1)) / VELOCITY_SATURATION, Decimal("1"))
            conversion = Decimal(sold) / max(alive, 1)

        for u in unit_rows:
            funnel = sum(
                1 for d in builder.deals_by_unit.get(u["id"], []) if d["status"] in FUNNEL_STATUSES
            )
            values = {
                "unit_available": Decimal("1") if u["status"] == "available" else Decimal("0"),
                "unit_demand_norm": min(Decimal(funnel) / DEMAND_SATURATION, Decimal("1")),
                "area_velocity_norm": velocity,
                "area_conversion_norm": conversion,
            }
            by_project.setdefault(project_id, []).append(
                UnitFeatureInput(
                    unit_id=u["id"],
                    area_id=area_id,
                    tie_break_created_at=u["created_at"],
                    values=values,
                )
            )
    return by_project


def score_dataset(builder: Builder) -> dict:
    per_project = derive_features(builder)
    report: dict = {"projects": {}, "band_totals": {"high": 0, "medium": 0, "low": 0}, "skipped": 0}
    for project_id, inputs in per_project.items():
        scored = [score_unit(u, V2_WEIGHTS, V2_MIN_COVERAGE) for u in inputs]
        ranked = rank_scores(scored)
        bands = {"high": 0, "medium": 0, "low": 0}
        skipped = 0
        for s in ranked:
            if s.skipped:
                skipped += 1
                continue
            bands[band_for(s.score)] += 1
        kept = [s for s in ranked if not s.skipped]
        report["projects"][project_id] = {
            "units_processed": len(ranked),
            "units_ranked": len(kept),
            "units_skipped": skipped,
            "bands": bands,
            "score_min": str(min((s.score for s in kept), default="")),
            "score_max": str(max((s.score for s in kept), default="")),
            "top": [
                {"unit_id": s.unit_id, "area_id": s.area_id, "score": str(s.score),
                 "band": band_for(s.score), "rank_in_project": s.rank_in_project}
                for s in sorted(kept, key=lambda x: x.rank_in_project)[:5]
            ],
        }
        for k in bands:
            report["band_totals"][k] += bands[k]
        report["skipped"] += skipped

    # per-area feature values, for the coverage proof table
    feats = []
    for area_id, unit_rows in builder.units_by_area.items():
        sample = next(
            (i for lst in per_project.values() for i in lst if i.area_id == area_id), None
        )
        if sample is None:
            continue
        v = sample.values
        demands = sorted(
            {
                str(
                    min(
                        Decimal(
                            sum(1 for d in builder.deals_by_unit.get(u["id"], []) if d["status"] in FUNNEL_STATUSES)
                        )
                        / DEMAND_SATURATION,
                        Decimal("1"),
                    )
                )
                for u in unit_rows
            }
        )
        feats.append(
            {
                "area_id": area_id,
                "units": len(unit_rows),
                "available": sum(1 for u in unit_rows if u["status"] == "available"),
                "area_velocity_norm": "MISSING" if v["area_velocity_norm"] is None else str(v["area_velocity_norm"]),
                "area_conversion_norm": "MISSING"
                if v["area_conversion_norm"] is None
                else str(v["area_conversion_norm"]),
                "unit_demand_norm_values": ", ".join(demands),
            }
        )
    report["area_features"] = feats
    return report


def absorption_coverage(builder: Builder) -> dict:
    """What the two absorption calculators will see. Reports the inputs' shape —
    it does NOT compute absorption_daily, which is the calculator's job."""
    ref = builder.ref.date()
    sold_dates = [
        datetime.strptime(d["sold_at"], "%Y-%m-%dT%H:%M:%SZ").date()
        for d in builder.rows["deals"]
        if d["status"] == "sold" and d["deleted_at"] == NULL
    ]
    sold_dates.sort()
    span = (sold_dates[-1] - sold_dates[0]).days if sold_dates else 0
    sr = builder.rows["sales_records"]
    sr_dates = sorted(date.fromisoformat(r["sold_date"]) for r in sr)

    area_of = {u["id"]: u["area_id"] for u in builder.rows["units"]}
    project_of_area = builder.area_project
    domain_projects = {
        project_of_area[area_of[d["unit_id"]]]
        for d in builder.rows["deals"]
        if d["status"] == "sold" and d["deleted_at"] == NULL
    }
    legacy_projects = {project_of_area[r["area_id"]] for r in sr}

    return {
        "domain": {
            "sold_deals": len(sold_dates),
            "first": sold_dates[0].isoformat() if sold_dates else None,
            "last": sold_dates[-1].isoformat() if sold_dates else None,
            "span_days": span,
            "series_points": span + 1 if sold_dates else 0,
            "points_reaching_ok": max(span + 1 - 30, 0),
            "sold_last_7d": sum(1 for d in sold_dates if (ref - d).days <= 7),
            "sold_last_30d": sum(1 for d in sold_dates if (ref - d).days <= 30),
        },
        "legacy": {
            "sales_records": len(sr),
            "inventory_snapshots": len(builder.rows["inventory_snapshots"]),
            "first": sr_dates[0].isoformat() if sr_dates else None,
            "last": sr_dates[-1].isoformat() if sr_dates else None,
            "units_sold_total": sum(int(r["units_sold"]) for r in sr),
        },
        "dual_lineage_projects": len(domain_projects & legacy_projects),
    }


# --- Validation --------------------------------------------------------------

TABLES = {
    "projects": t_projects,
    "areas": t_areas,
    "upload_files": t_upload_files,
    "upload_errors": t_upload_errors,
    "crm_source_records": t_crm_source_records,
    "units": t_units,
    "deals": t_deals,
    "sales_records": t_sales_records,
    "inventory_snapshots": t_inventory_snapshots,
}
TABLE_ORDER = list(TABLES)

FKS = [
    ("areas", "project_id", "projects", "id"),
    ("upload_files", "project_id", "projects", "id"),
    ("upload_errors", "file_id", "upload_files", "id"),
    ("crm_source_records", "first_sync_run_id", "upload_files", "id"),
    ("crm_source_records", "last_sync_run_id", "upload_files", "id"),
    ("units", "area_id", "areas", "id"),
    ("deals", "unit_id", "units", "id"),
    ("sales_records", "area_id", "areas", "id"),
    ("sales_records", "file_id", "upload_files", "id"),
    ("inventory_snapshots", "area_id", "areas", "id"),
    ("inventory_snapshots", "file_id", "upload_files", "id"),
]

ENUMS = {
    ("projects", "status"): ("pending", "active", "rejected", "archived"),
    ("areas", "status"): ("pending", "active", "rejected", "archived"),
    ("projects", "absorption_calculator"): ("legacy_aggregate", "domain_units_deals"),
    ("upload_files", "status"): ("pending", "processing", "completed", "partially_completed", "failed"),
    ("upload_files", "input_format"): ("csv", "xlsx", "json"),
    ("upload_files", "transport_mode"): ("file_upload", "api_push"),
    ("upload_files", "sync_mode"): ("full_snapshot", "incremental"),
    ("upload_errors", "error_category"): ("transport", "schema", "field", "business", "conflict"),
    ("upload_errors", "retry_status"): ("open", "retrying", "resolved", "permanent"),
    ("crm_source_records", "state"): ("active", "tombstoned"),
    ("crm_source_records", "last_decision"): (
        "insert", "update", "skip_stale", "duplicate_noop", "conflict", "tombstone",
    ),
    ("units", "status"): ("available", "reserved", "sold", "blocked"),
    ("deals", "status"): ("lead", "qualified", "interested", "viewing", "reserved", "sold", "lost"),
    ("inventory_snapshots", "snapshot_type"): ("opening", "closing", "manual", "derived"),
}

UNIQUES = [
    ("projects", ("source_instance_id", "external_id"), "uq_projects_source_identity"),
    ("areas", ("source_instance_id", "external_id"), "uq_areas_source_identity"),
    ("areas", ("project_id", "area_name", "unit_type"), "uq_areas_project_name_unit_type"),
    ("upload_files", ("project_id", "checksum"), "uq_upload_files_project_checksum"),
    ("crm_source_records",
     ("source_system", "source_instance_id", "source_entity", "source_record_id"),
     "uq_crm_source_records_identity"),
    ("units", ("source_instance_id", "external_unit_id"), "uq_units_source_identity"),
    ("units", ("area_id", "unit_code"), "uq_units_area_unit_code (partial: deleted_at IS NULL)"),
    ("deals", ("source_instance_id", "external_deal_id"), "uq_deals_source_identity"),
    ("sales_records", ("area_id", "sold_date", "external_record_id"), "uq_sales_area_date_external_id"),
    ("sales_records", ("area_id", "source_row_hash"), "uq_sales_area_source_row_hash"),
    ("inventory_snapshots", ("area_id", "snapshot_date", "snapshot_type"), "uq_inventory_area_date_type"),
    ("inventory_snapshots", ("area_id", "source_row_hash"), "uq_inventory_area_source_row_hash"),
]


def validate(rows: dict[str, list[dict]]) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    # 1. headers match the Core projection exactly
    for name, table in TABLES.items():
        expected = [c.name for c in table.columns]
        actual = list(rows[name][0].keys()) if rows[name] else []
        add(f"header/{name}", expected == actual,
            "matches src.models.tables" if expected == actual else f"expected {expected}, got {actual}")

    # 2. primary keys unique + non-null
    for name in TABLE_ORDER:
        pk = ["campaign_id", "unit_id"] if name == "sales_campaign_units" else ["id"]
        seen = set()
        dupes = 0
        nulls = 0
        for r in rows[name]:
            key = tuple(r[c] for c in pk)
            if any(v == NULL for v in key):
                nulls += 1
            if key in seen:
                dupes += 1
            seen.add(key)
        add(f"pk/{name}", dupes == 0 and nulls == 0, f"{len(rows[name])} rows, {dupes} duplicate, {nulls} null")

    # 3. FK integrity within the generated set
    for child, col, parent, pcol in FKS:
        parent_keys = {r[pcol] for r in rows[parent]}
        missing = [r[col] for r in rows[child] if r[col] != NULL and r[col] not in parent_keys]
        add(f"fk/{child}.{col}->{parent}.{pcol}", not missing,
            f"{len(rows[child])} rows, {len(missing)} orphan")

    # 4. NOT NULL columns carry no NULL marker
    for name, table in TABLES.items():
        bad = []
        for c in table.columns:
            if c.nullable:
                continue
            n = sum(1 for r in rows[name] if r[c.name] == NULL)
            if n:
                bad.append(f"{c.name}={n}")
        add(f"notnull/{name}", not bad, "all NOT NULL columns populated" if not bad else ", ".join(bad))

    # 5. enum / CHECK vocabularies
    for (name, col), allowed in ENUMS.items():
        bad = sorted({r[col] for r in rows[name] if r[col] != NULL and r[col] not in allowed})
        add(f"enum/{name}.{col}", not bad, f"allowed={allowed}" if not bad else f"invalid={bad}")

    # 6. unique constraints (NULL never collides, matching Postgres)
    for name, cols, label in UNIQUES:
        seen = set()
        dupes = []
        for r in rows[name]:
            key = tuple(r[c] for c in cols)
            if any(v == NULL for v in key):
                continue
            if key in seen:
                dupes.append(key)
            seen.add(key)
        add(f"unique/{label}", not dupes, f"{len(seen)} distinct keys" if not dupes else f"dupes={dupes[:3]}")

    # 7. partial unique: at most one reserved/sold deal per live unit
    holding: dict[str, int] = {}
    for r in rows["deals"]:
        if r["status"] in ("reserved", "sold") and r["deleted_at"] == NULL:
            holding[r["unit_id"]] = holding.get(r["unit_id"], 0) + 1
    over = {k: v for k, v in holding.items() if v > 1}
    add("unique/uq_deals_active_per_unit", not over, f"{len(holding)} units hold a deal, {len(over)} violate")

    # 8. timestamp ordering CHECKs
    def parse(v: str) -> datetime | None:
        return None if v == NULL else datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")

    for name in ("units", "deals"):
        bad = sum(1 for r in rows[name] if parse(r["updated_at"]) < parse(r["created_at"]))
        add(f"ts/{name}.updated_at>=created_at", bad == 0, f"{bad} violations")
    bad = sum(
        1 for r in rows["deals"]
        if r["sold_at"] != NULL and r["reserved_at"] != NULL and parse(r["sold_at"]) < parse(r["reserved_at"])
    )
    add("ts/deals.sold_at>=reserved_at", bad == 0, f"{bad} violations")
    bad = sum(
        1 for r in rows["crm_source_records"] if parse(r["last_seen_at"]) < parse(r["first_seen_at"])
    )
    add("ts/crm_source_records.last_seen_at>=first_seen_at", bad == 0, f"{bad} violations")

    # 9. status/timestamp coherence CHECKs on deals
    for status, col in (("sold", "sold_at"), ("reserved", "reserved_at"), ("lost", "lost_at")):
        bad = sum(1 for r in rows["deals"] if r["status"] == status and r[col] == NULL)
        add(f"check/deals.{status}_requires_{col}", bad == 0, f"{bad} violations")

    # 10. upload_files snapshot/chunk CHECKs (found by loading into PostgreSQL,
    #     not by reading the migration - ck_upload_files_chunk_index_within_total
    #     makes chunk_index 0-based)
    uf = rows["upload_files"]
    bad = sum(
        1 for r in uf
        if r["chunk_index"] != NULL and r["chunk_total"] != NULL and int(r["chunk_index"]) >= int(r["chunk_total"])
    )
    add("check/upload_files_chunk_index_within_total", bad == 0, f"{bad} violations")
    bad = sum(1 for r in uf if r["chunk_index"] != NULL and int(r["chunk_index"]) < 0)
    add("check/upload_files_chunk_index_nonnegative", bad == 0, f"{bad} violations")
    bad = sum(1 for r in uf if r["chunk_total"] != NULL and int(r["chunk_total"]) <= 0)
    add("check/upload_files_chunk_total_positive", bad == 0, f"{bad} violations")
    bad = sum(
        1 for r in uf
        if r["snapshot_id"] != NULL
        and NULL in (r["chunk_index"], r["chunk_total"], r["snapshot_complete"])
    )
    add("check/upload_files_snapshot_fields_together", bad == 0, f"{bad} violations")

    # 10. upload_errors locator CHECK
    bad = sum(
        1 for r in rows["upload_errors"]
        if r["row_number"] == NULL and r["json_path"] == NULL and r["error_category"] not in ("transport", "schema")
    )
    add("check/upload_errors_locator", bad == 0, f"{bad} violations")

    # 11. unit status vs deal coherence (application-level, not a DB CHECK)
    by_unit: dict[str, list[str]] = {}
    for r in rows["deals"]:
        by_unit.setdefault(r["unit_id"], []).append(r["status"])
    bad = []
    for u in rows["units"]:
        st = by_unit.get(u["id"], [])
        if u["status"] == "sold" and "sold" not in st:
            bad.append(u["external_unit_id"])
        if u["status"] == "reserved" and "reserved" not in st:
            bad.append(u["external_unit_id"])
        if u["status"] == "blocked" and st:
            bad.append(u["external_unit_id"])
    add("coherence/unit_status_matches_deals", not bad, f"{len(bad)} mismatches")

    return checks


# --- Artifact writers --------------------------------------------------------


def write_csvs(out: Path, rows: dict[str, list[dict]]) -> dict[str, dict]:
    meta = {}
    for name, table in TABLES.items():
        cols = [c.name for c in table.columns]
        path = out / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            for r in rows[name]:
                w.writerow(r)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        meta[name] = {"rows": len(rows[name]), "columns": cols, "sha256": digest}
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference-date", default="2026-08-18", help="UTC anchor date (YYYY-MM-DD)")
    ap.add_argument("--out", default="datasets/synthetic_v1")
    args = ap.parse_args()

    ref = datetime.combine(date.fromisoformat(args.reference_date), datetime.min.time(), tzinfo=UTC)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    builder = Builder(ref)
    builder.build()
    checks = validate(builder.rows)
    ranking = score_dataset(builder)
    ranking["absorption"] = absorption_coverage(builder)
    meta = write_csvs(out, builder.rows)

    (out / "load_order.txt").write_text(
        "\n".join(
            [
                "# FK-safe load order. NULL marker is \\N.",
                "# COPY <table> (<cols>) FROM '<file>' WITH (FORMAT csv, HEADER true, NULL '\\N');",
                "",
                *TABLE_ORDER,
                "",
            ]
        ),
        encoding="utf-8",
    )

    manifest = {
        "dataset": "synthetic_v1",
        "generated_at": iso(datetime.now(UTC)),
        "reference_date": args.reference_date,
        "alembic_head": "0026_cloudinary_cover_images",
        "generator": "scripts/generate_synthetic_dataset.py",
        "null_marker": "\\N",
        "timestamp_format": "ISO-8601 UTC (%Y-%m-%dT%H:%M:%SZ)",
        "source_namespace": {"source_system": SOURCE_SYSTEM, "source_instance_id": SOURCE_INSTANCE_ID},
        "uuid_namespace": str(UUID_NS),
        "source_files_inspected": [
            "src/models/tables.py",
            "src/ranking/engine.py",
            "src/ranking/service.py",
            "src/ranking/bands.py",
            "src/services/domain_absorption.py",
            "alembic/versions/0001_initial_schema.py",
            "alembic/versions/0006_sync_foundation.py",
            "alembic/versions/0007_s3_domain_model.py",
            "alembic/versions/0014_ranking_foundation.py",
            "alembic/versions/0015_ranking_results.py",
            "alembic/versions/0017_hierarchy_projection.py",
            "alembic/versions/0022_ranking_config_v2.py",
        ],
        "load_order": TABLE_ORDER,
        "tables": meta,
        "primary_keys": {name: ["id"] for name in TABLE_ORDER},
        "foreign_keys": [
            {"child": c, "column": col, "parent": p, "references": pc} for c, col, p, pc in FKS
        ],
        "validation": {
            "checks_run": len(checks),
            "failed": [c for c in checks if c["status"] == "FAIL"],
        },
        "ranking_preview": ranking["band_totals"],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    _write_validation_report(out, checks, ranking, meta)
    _write_readme(out, meta, args.reference_date)

    failed = [c for c in checks if c["status"] == "FAIL"]
    print(f"tables={len(meta)} rows={sum(m['rows'] for m in meta.values())} checks={len(checks)} failed={len(failed)}")
    for c in failed:
        print(f"  FAIL {c['check']}: {c['detail']}")
    print(f"bands={ranking['band_totals']} skipped={ranking['skipped']}")
    return 1 if failed else 0


def _write_validation_report(out: Path, checks: list[dict], ranking: dict, meta: dict) -> None:
    lines = [
        "# Validation report — `datasets/synthetic_v1`",
        "",
        f"Generated by `scripts/generate_synthetic_dataset.py`. Checks run: **{len(checks)}**, "
        f"failed: **{len([c for c in checks if c['status'] == 'FAIL'])}**.",
        "",
        "No database was contacted to produce this report. Structural checks run against the",
        "in-memory rows before they are written; ranking numbers come from the real scorer",
        "(`src.ranking.engine`, a pure function) applied to the same rows.",
        "",
        "## Constraint checks",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for c in checks:
        lines.append(f"| `{c['check']}` | {c['status']} | {c['detail']} |")

    lines += [
        "",
        "## Row counts",
        "",
        "| Table | Rows |",
        "|---|---|",
        *[f"| `{k}` | {v['rows']} |" for k, v in meta.items()],
        f"| **total** | **{sum(v['rows'] for v in meta.values())}** |",
        "",
        "## Ranking scenario coverage",
        "",
        "Scored with the published v2 weights (`alembic/versions/0022_ranking_config_v2.py`):",
        "`unit_available` 0.35, `unit_demand_norm` 0.25, `area_velocity_norm` 0.20,",
        "`area_conversion_norm` 0.20, `min_weight_coverage` 0.5 — all `direction: positive`.",
        "",
        "| Project | Units | Ranked | Skipped | high | medium | low | score min | score max |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for pid, r in ranking["projects"].items():
        b = r["bands"]
        lines.append(
            f"| `{pid[:8]}…` | {r['units_processed']} | {r['units_ranked']} | {r['units_skipped']} | "
            f"{b['high']} | {b['medium']} | {b['low']} | {r['score_min']} | {r['score_max']} |"
        )
    t = ranking["band_totals"]
    lines += [
        f"| **all** | | | {ranking['skipped']} | **{t['high']}** | **{t['medium']}** | **{t['low']}** | | |",
        "",
        "## Per-area feature values (drives the bands above)",
        "",
        "| Area | Units | Available | `area_velocity_norm` | `area_conversion_norm` | `unit_demand_norm` values |",
        "|---|---|---|---|---|---|",
    ]
    for f in ranking["area_features"]:
        lines.append(
            f"| `{f['area_id'][:8]}…` | {f['units']} | {f['available']} | {f['area_velocity_norm']} | "
            f"{f['area_conversion_norm']} | {f['unit_demand_norm_values']} |"
        )

    a = ranking["absorption"]
    d, lg = a["domain"], a["legacy"]
    lines += [
        "",
        "## Absorption scenario coverage",
        "",
        "The dataset ships absorption *inputs*; `absorption_daily` is produced by the calculators.",
        "",
        "| Lineage | Coverage |",
        "|---|---|",
        f"| `domain_units_deals` (from `units`/`deals`) | {d['sold_deals']} sold deals, "
        f"{d['first']} .. {d['last']} ({d['span_days']} days) |",
        f"| → series length | {d['series_points']} daily points; the calculator marks the first 30 "
        f"`warning`, so **{d['points_reaching_ok']}** reach `data_quality_status='ok'` |",
        f"| → `velocity_7d` input | {d['sold_last_7d']} sales in the last 7 days |",
        f"| → `velocity_30d` input | {d['sold_last_30d']} sales in the last 30 days |",
        f"| `legacy_aggregate` (from `sales_records`) | {lg['sales_records']} sales_records + "
        f"{lg['inventory_snapshots']} inventory_snapshots, {lg['first']} .. {lg['last']}, "
        f"{lg['units_sold_total']} units sold |",
        f"| parallel-run comparison | **{a['dual_lineage_projects']}** project carries BOTH lineages, "
        "so `calculator_comparisons` has two real sides to diff |",
        "",
        "`MISSING` means the area has no live deals at all, so `_area_features` omits it and the",
        "engine applies the config's `neutral` policy (0.5) — the behaviour",
        "`src/ranking/service.py` docstring item 2 was written to preserve.",
        "",
        "## Limitations",
        "",
        "- **Coverage gate is never triggered.** Published v2 uses only `zero`/`neutral` missing",
        "  policies, so `coverage` is always 1.0 >= 0.5 and no unit is ever skipped. A skipped-unit",
        "  fixture is impossible without publishing a config that uses the `skip` policy.",
        "- **`area_velocity_norm` decays with wall-clock time.** It counts deals sold in the last 30",
        "  days *at run time*. Regenerate with `--reference-date` before demos, or velocity drifts to 0.",
        "- **Absorption output is not shipped.** `absorption_daily` is a computed table; this dataset",
        "  provides its inputs. Run the recompute path to populate it (see README).",
        "- **No advisory rows.** `agent_recommendations.ranking_run_id` is NOT NULL and points at",
        "  `ranking_runs`, a generated table — a hand-written recommendation would reference a",
        "  computation that never ran. Generate them after ranking, via the API.",
        "- **Structural validation only.** The rows were not loaded into PostgreSQL, so server-side",
        "  CHECK/UNIQUE/FK enforcement has not been exercised; the checks above re-implement those",
        "  rules from the migration definitions.",
        "",
    ]
    (out / "validation_report.md").write_text("\n".join(lines), encoding="utf-8")


def _write_readme(out: Path, meta: dict, reference_date: str) -> None:
    total = sum(v["rows"] for v in meta.values())
    text = f"""# `datasets/synthetic_v1`

Synthetic, fully relational CSV dataset for local development against the
AbsorptionForecast backend schema at Alembic head `0026_cloudinary_cover_images`.

**Every row is fabricated from the schema.** No live, dev, staging, or production
database was read, queried, or exported to build it. There are no real names,
emails, phone numbers, addresses, credentials, tokens, or secrets — identities are
deterministic strings in a reserved namespace.

- Generator: `scripts/generate_synthetic_dataset.py`
- Reference date: `{reference_date}` (UTC anchor for all relative timestamps)
- Tables: **{len(meta)}**, rows: **{total}**
- Regenerate: `python -m scripts.generate_synthetic_dataset --reference-date YYYY-MM-DD`

## Conventions

| Aspect | Value |
|---|---|
| Encoding | UTF-8, `\\n` line endings, header row per file |
| NULL marker | `\\N` (PostgreSQL `COPY` default) — an empty field is a literal empty string |
| Timestamps | ISO-8601 UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| Dates | `YYYY-MM-DD` |
| IDs | UUIDv5, deterministic — same input always yields the same UUID |
| Source namespace | `source_system={SOURCE_SYSTEM}`, `source_instance_id={SOURCE_INSTANCE_ID}` |
| External ID prefix | `{EXT}-` |

The namespace is deliberately disjoint from the ones existing seed migrations own
(`ai-dev-fixture` in 0019, `synthetic-demo-2026` in 0023, `mini-crm-dev` for the
relay), so this dataset can be loaded into an already-migrated database without
colliding on `uq_units_source_identity` and friends.

## Entity counts

| Table | Rows |
|---|---|
{chr(10).join(f"| `{k}` | {v['rows']} |" for k, v in meta.items())}

## Load order

See `load_order.txt`. FK-safe order:

```
{chr(10).join(f"{i + 1}. {name}" for i, name in enumerate(TABLE_ORDER))}
```

Example (psql, disposable database only):

```sql
\\copy projects FROM 'projects.csv' WITH (FORMAT csv, HEADER true, NULL '\\N');
```

## Shape of the data

Three projects exercising different lineages:

- **`{EXT}-P-001` Harbor Crest Residences** — `domain_units_deals`. Four areas spanning
  a hot area (saturated 30-day velocity, units with 3+ funnel deals), a moderate one,
  a slow one with zero recent sales, and one with **no deals at all** so the ranking
  engine's `neutral` missing-policy path is exercised.
- **`{EXT}-P-002` Willow Park Gardens** — `legacy_aggregate`. Carries both lineages:
  `units`/`deals` *and* `sales_records`/`inventory_snapshots` ingested through
  `upload_files`, so the parallel-run comparison has two real sides to diff.
- **`{EXT}-P-003` Quarry Fields Commons** — edge cases: a blocked-heavy area, and one
  **legacy area** with `external_id`/`source_*` NULL (a pre-Phase-D row, still valid
  and still rankable).

Deals span ~120 days so the absorption series is long enough to leave
`data_quality_status='warning'` and reach `'ok'` (the domain calculator marks the
first 30 points `warning`). `crm_source_records` carries one row per sync decision
(`insert`/`update`/`skip_stale`/`duplicate_noop`/`conflict`/`tombstone`) and both
mirror states, and `upload_errors` covers four error categories and four retry
statuses.

## Intended use

1. Load into a **disposable** database (never a shared or dev volume without a
   deliberate decision).
2. Run absorption recompute to populate `absorption_daily` — the dataset ships the
   *inputs*, not the computed output.
3. Run ranking (`POST /api/v1/ranking/run`, or `src.ranking.service.run_ranking`) to
   populate `feature_snapshots` / `ranking_runs` / `ranking_scores`.
4. Only then generate advisory rows through `POST /api/v1/agent/recommendations`,
   which needs a real `ranking_run_id`.

`validation_report.md` records the ranking bands this dataset actually produces,
computed with the real `src.ranking.engine` scorer.

## Excluded tables and why

| Table(s) | Reason |
|---|---|
| `alembic_version` | Alembic bookkeeping. |
| `users`, `user_areas`, `refresh_tokens`, `audit_logs`, `settings` | Dead orphan island — zero application references. Backend auth is static env tokens, so no user rows exist at runtime. `created_by`/`reviewed_by`/`uploaded_by` are therefore NULL. |
| `forecasts`, `forecast_jobs`, `forecast_points`, `alerts`, `explanations`, `llm_calls`, `suggestions`, `proposals`, `approvals` | Deprecated forecast FK island. `run_daily_forecast` is a stub that computes nothing; fabricating forecast rows would invent model output that no code produced. |
| `sync_credentials` | Stores credential hashes. Never synthesised. |
| `sync_payloads` | Sync internals — raw envelope bodies, plus an FK to `sync_credentials`. |
| `absorption_daily`, `calculator_comparisons` | Computed outputs of the absorption calculators. Shipping them would let stale numbers contradict the inputs. |
| `feature_snapshots`, `ranking_runs`, `ranking_scores` | Computed outputs of the ranking service, which is the single sanctioned writer (`tests/test_ranking_boundary.py`). |
| `ranking_configs` | Already seeded by migrations 0014 (v1) and 0022 (v2). A CSV row would violate `uq_ranking_configs_version`. |
| `reconciliation_runs`, `reconciliation_findings` | Outputs of a reconciliation run. |
| `agent_recommendations`, `agent_executions`, `sales_campaigns`, `sales_campaign_units` | Require a real `ranking_run_id` (NOT NULL FK to a generated table). Including them would break the guarantee that every child row's parent exists in this CSV set. |
"""
    (out / "README.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
