"""Export analytics CSVs from a database already loaded with `datasets/synthetic_v1`.

    DATABASE_URL=postgresql+asyncpg://... python -m scripts.export_synthetic_analytics \
        [--namespace synthetic-csv-v1] [--out datasets/synthetic_v1/exports]

Read-only against the database: every statement is a SELECT. Run the absorption
and ranking pipelines first — this script exports what they produced, it does not
compute business metrics of its own beyond row-level joins, counts, and the
presentation-layer band (`src.ranking.bands.band_for`, the same function the API
uses so the exported band cannot drift from the one users see).

Scope is pinned to one `source_instance_id` so the migration-seeded fixtures
(0019/0021/0023), which share these tables, never leak into the export.

**Columns that do not exist are not invented.** The backend schema has no price,
floor, orientation, bathroom, customer, lead, budget, financing, sales-agent,
follow-up, contact, or note columns, and no forecast output (the forecast job is
a stub). Rather than emit all-NULL columns that would imply those fields are
tracked, every requested-but-absent field is listed in `feature_dictionary.csv`
with `status=ABSENT` and the reason.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

from src.db import get_session_factory
from src.ranking.bands import as_percent, band_for

FEATURE_KEYS = ("unit_available", "unit_demand_norm", "area_velocity_norm", "area_conversion_norm")
FUNNEL = ("lead", "qualified", "interested", "viewing")


def _w(path: Path, rows: list[dict], columns: list[str]) -> int:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})
    return len(rows)


async def export_units_ranking(session, ns: str) -> tuple[list[dict], list[str]]:
    """One row per ranked unit: raw source values, per-feature normalized values,
    per-feature contributions, missingness flags, score/band/rank, freshness."""
    sql = sa.text("""
        SELECT u.id AS unit_id, u.external_unit_id, u.unit_code, u.unit_type, u.status AS unit_status,
               u.created_at AS unit_created_at, u.updated_at AS unit_updated_at,
               u.source_updated_at AS unit_source_updated_at, u.deleted_at AS unit_deleted_at,
               a.id AS area_id, a.external_id AS area_external_id, a.area_name,
               a.bedrooms AS area_bedrooms, a.area_sqm AS area_sqm, a.total_units AS area_total_units_planned,
               p.id AS project_id, p.external_id AS project_external_id, p.name AS project_name,
               p.absorption_calculator,
               rs.score, rs.rank_in_area, rs.rank_in_project, rs.weight_coverage, rs.contributions,
               rs.computed_at AS ranking_computed_at, rs.feature_freshness_at,
               rr.id AS ranking_run_id, rr.trigger AS ranking_trigger, rr.finished_at AS ranking_run_finished_at,
               rc.version AS ranking_config_version, rc.min_weight_coverage
        FROM ranking_scores rs
        JOIN units u ON u.id = rs.unit_id
        JOIN areas a ON a.id = rs.area_id
        JOIN projects p ON p.id = rs.project_id
        JOIN ranking_runs rr ON rr.id = rs.ranking_run_id
        JOIN ranking_configs rc ON rc.id = rs.config_version_id
        WHERE p.source_instance_id = :ns
        ORDER BY p.external_id, rs.rank_in_project
    """)
    scored = (await session.execute(sql, {"ns": ns})).mappings().all()

    counts = (await session.execute(sa.text("""
        SELECT d.unit_id, d.status, count(*) AS n
        FROM deals d JOIN units u ON u.id = d.unit_id
        JOIN areas a ON a.id = u.area_id JOIN projects p ON p.id = a.project_id
        WHERE p.source_instance_id = :ns AND d.deleted_at IS NULL
        GROUP BY d.unit_id, d.status
    """), {"ns": ns})).mappings().all()
    per_unit: dict[str, dict[str, int]] = {}
    for c in counts:
        per_unit.setdefault(str(c["unit_id"]), {})[c["status"]] = int(c["n"])

    rows = []
    for r in scored:
        uid = str(r["unit_id"])
        st = per_unit.get(uid, {})
        contrib = r["contributions"] or {}
        funnel_total = sum(st.get(s, 0) for s in FUNNEL)
        row = {
            # identity
            "unit_id": uid,
            "external_unit_id": r["external_unit_id"],
            "unit_code": r["unit_code"],
            "project_id": str(r["project_id"]),
            "project_external_id": r["project_external_id"],
            "project_name": r["project_name"],
            "area_id": str(r["area_id"]),
            "area_external_id": r["area_external_id"],
            "area_name": r["area_name"],
            # raw source attributes (unit level)
            "unit_status": r["unit_status"],
            "unit_type": r["unit_type"],
            # NOTE: area-level, not unit-level - the schema has no per-unit size/bedrooms
            "area_bedrooms": r["area_bedrooms"],
            "area_sqm": r["area_sqm"],
            "area_total_units_planned": r["area_total_units_planned"],
            "absorption_calculator": r["absorption_calculator"],
            # raw deal signals
            "is_available": int(r["unit_status"] == "available"),
            "has_active_deal": int(bool(st.get("reserved", 0) or st.get("sold", 0))),
            "deals_total": sum(st.values()),
            "funnel_deals_total": funnel_total,
            "deals_lead": st.get("lead", 0),
            "deals_qualified": st.get("qualified", 0),
            "deals_interested": st.get("interested", 0),
            "deals_viewing": st.get("viewing", 0),
            "deals_reserved": st.get("reserved", 0),
            "deals_sold": st.get("sold", 0),
            "deals_lost": st.get("lost", 0),
            # scoring outputs
            "score": r["score"],
            "score_percent": as_percent(r["score"]),
            "band": band_for(r["score"]),
            "rank_in_area": r["rank_in_area"],
            "rank_in_project": r["rank_in_project"],
            "weight_coverage": r["weight_coverage"],
            "min_weight_coverage": r["min_weight_coverage"],
            "was_skipped": 0,  # ranking_scores only stores scored units
            "ranking_config_version": r["ranking_config_version"],
            "ranking_run_id": str(r["ranking_run_id"]),
            "ranking_trigger": r["ranking_trigger"],
            # freshness
            "ranking_computed_at": r["ranking_computed_at"],
            "feature_freshness_at": r["feature_freshness_at"],
            "ranking_run_finished_at": r["ranking_run_finished_at"],
            "unit_created_at": r["unit_created_at"],
            "unit_updated_at": r["unit_updated_at"],
            "unit_source_updated_at": r["unit_source_updated_at"],
        }
        # per-feature: normalized value, weight, contribution, provenance, missing flag
        n_missing = 0
        for k in FEATURE_KEYS:
            e = contrib.get(k, {})
            src = e.get("source")
            missing = int(src in ("missing_defaulted", "missing_skipped"))
            n_missing += missing
            row[f"feat_{k}_value"] = e.get("value")
            row[f"feat_{k}_weight"] = e.get("weight")
            row[f"feat_{k}_direction"] = e.get("direction")
            row[f"feat_{k}_contribution"] = e.get("contribution")
            row[f"feat_{k}_source"] = src
            row[f"feat_{k}_is_missing"] = missing
        row["features_missing_count"] = n_missing
        row["features_total_count"] = len(FEATURE_KEYS)
        rows.append(row)

    cols = list(rows[0].keys()) if rows else []
    return rows, cols


async def export_leads_deals(session, ns: str) -> tuple[list[dict], list[str]]:
    """One row per deal. This is the closest thing the schema has to a lead/CRM
    record: there is no customer, lead, contact, or sales-agent entity anywhere
    (Mini CRM states it carries no customers, no prices, no PII by design)."""
    sql = sa.text("""
        SELECT d.id AS deal_id, d.external_deal_id, d.status AS deal_status,
               d.source_status AS deal_source_status,
               d.reserved_at, d.sold_at, d.lost_at,
               d.created_at AS deal_created_at, d.updated_at AS deal_updated_at,
               d.source_updated_at AS deal_source_updated_at, d.source_revision,
               d.deleted_at AS deal_deleted_at,
               u.id AS unit_id, u.external_unit_id, u.unit_code, u.unit_type, u.status AS unit_status,
               a.id AS area_id, a.external_id AS area_external_id, a.area_name, a.bedrooms AS area_bedrooms,
               a.area_sqm,
               p.id AS project_id, p.external_id AS project_external_id, p.name AS project_name
        FROM deals d
        JOIN units u ON u.id = d.unit_id
        JOIN areas a ON a.id = u.area_id
        JOIN projects p ON p.id = a.project_id
        WHERE p.source_instance_id = :ns
        ORDER BY p.external_id, a.area_name, d.external_deal_id
    """)
    src = (await session.execute(sql, {"ns": ns})).mappings().all()

    # interaction proxy: how many deals share this unit
    per_unit: dict[str, int] = {}
    for r in src:
        per_unit[str(r["unit_id"])] = per_unit.get(str(r["unit_id"]), 0) + 1

    stage = {s: "funnel" for s in FUNNEL}
    stage.update({"reserved": "holding", "sold": "closed_won", "lost": "closed_lost"})

    rows = []
    for r in src:
        d = dict(r)
        uid = str(r["unit_id"])
        last_activity = r["deal_source_updated_at"] or r["deal_updated_at"]
        rows.append(
            {
                "deal_id": str(r["deal_id"]),
                "external_deal_id": r["external_deal_id"],
                "unit_id": uid,
                "external_unit_id": r["external_unit_id"],
                "unit_code": r["unit_code"],
                "unit_type": r["unit_type"],
                "unit_status": r["unit_status"],
                "area_id": str(r["area_id"]),
                "area_external_id": r["area_external_id"],
                "area_name": r["area_name"],
                "area_bedrooms": r["area_bedrooms"],
                "area_sqm": r["area_sqm"],
                "project_id": str(r["project_id"]),
                "project_external_id": r["project_external_id"],
                "project_name": r["project_name"],
                # status / stage
                "deal_status": r["deal_status"],
                "deal_source_status": r["deal_source_status"],
                "funnel_stage": stage.get(r["deal_status"], "unknown"),
                "is_funnel": int(r["deal_status"] in FUNNEL),
                "is_qualified_or_beyond": int(
                    r["deal_status"] in ("qualified", "interested", "viewing", "reserved", "sold")
                ),
                "is_viewing": int(r["deal_status"] == "viewing"),
                "is_holding": int(r["deal_status"] in ("reserved", "sold")),
                "is_closed_won": int(r["deal_status"] == "sold"),
                "is_closed_lost": int(r["deal_status"] == "lost"),
                # timestamps that DO exist
                "reserved_at": r["reserved_at"],
                "sold_at": r["sold_at"],
                "lost_at": r["lost_at"],
                "deal_created_at": r["deal_created_at"],
                "deal_updated_at": r["deal_updated_at"],
                "deal_source_updated_at": r["deal_source_updated_at"],
                "last_activity_at": last_activity,
                "deal_deleted_at": r["deal_deleted_at"],
                "is_deleted": int(r["deal_deleted_at"] is not None),
                "source_revision": r["source_revision"],
                # derived
                "deals_on_same_unit": per_unit[uid],
                "days_reserved_to_sold": (
                    (r["sold_at"] - r["reserved_at"]).days
                    if r["sold_at"] and r["reserved_at"]
                    else None
                ),
                "age_days_at_export": None,  # filled by caller-independent calc below
            }
        )
        del d
    cols = list(rows[0].keys()) if rows else []
    return rows, cols


async def export_absorption(session, ns: str) -> tuple[list[dict], list[str]]:
    """One row per (area, stat_date, calculator) from absorption_daily — the
    computed output of both calculators — enriched with area/project context and
    the current inventory mix. Dual lineage is preserved: an area on both
    calculators appears once per calculator, never merged."""
    sql = sa.text("""
        SELECT ad.stat_date, ad.calculator, ad.units_sold, ad.velocity_7d, ad.velocity_30d,
               ad.units_remaining, ad.units_reserved, ad.data_quality_status, ad.is_observed,
               ad.computed_at,
               a.id AS area_id, a.external_id AS area_external_id, a.area_name,
               a.bedrooms AS area_bedrooms, a.area_sqm, a.total_units AS area_total_units_planned,
               p.id AS project_id, p.external_id AS project_external_id, p.name AS project_name,
               p.absorption_calculator AS project_active_calculator, p.launch_date
        FROM absorption_daily ad
        JOIN areas a ON a.id = ad.area_id
        JOIN projects p ON p.id = a.project_id
        WHERE p.source_instance_id = :ns
        ORDER BY p.external_id, a.area_name, ad.calculator, ad.stat_date
    """)
    src = (await session.execute(sql, {"ns": ns})).mappings().all()

    mix = (await session.execute(sa.text("""
        SELECT u.area_id, u.status, count(*) AS n
        FROM units u JOIN areas a ON a.id = u.area_id JOIN projects p ON p.id = a.project_id
        WHERE p.source_instance_id = :ns AND u.deleted_at IS NULL
        GROUP BY u.area_id, u.status
    """), {"ns": ns})).mappings().all()
    per_area: dict[str, dict[str, int]] = {}
    for m in mix:
        per_area.setdefault(str(m["area_id"]), {})[m["status"]] = int(m["n"])

    conv = (await session.execute(sa.text("""
        SELECT u.area_id,
               count(*) FILTER (WHERE d.status = 'sold') AS sold,
               count(*) AS alive
        FROM deals d JOIN units u ON u.id = d.unit_id
        JOIN areas a ON a.id = u.area_id JOIN projects p ON p.id = a.project_id
        WHERE p.source_instance_id = :ns AND d.deleted_at IS NULL AND u.deleted_at IS NULL
        GROUP BY u.area_id
    """), {"ns": ns})).mappings().all()
    per_area_conv = {str(c["area_id"]): (int(c["sold"]), int(c["alive"])) for c in conv}

    rows = []
    for r in src:
        aid = str(r["area_id"])
        m = per_area.get(aid, {})
        live = sum(m.values())
        sold_d, alive_d = per_area_conv.get(aid, (0, 0))
        rows.append(
            {
                "snapshot_date": r["stat_date"],
                "calculator": r["calculator"],
                "project_id": str(r["project_id"]),
                "project_external_id": r["project_external_id"],
                "project_name": r["project_name"],
                "project_active_calculator": r["project_active_calculator"],
                "is_active_lineage": int(r["calculator"] == r["project_active_calculator"]),
                "launch_date": r["launch_date"],
                "area_id": aid,
                "area_external_id": r["area_external_id"],
                "area_name": r["area_name"],
                "area_bedrooms": r["area_bedrooms"],
                "area_sqm": r["area_sqm"],
                "area_total_units_planned": r["area_total_units_planned"],
                # computed absorption (raw, as persisted)
                "units_sold": r["units_sold"],
                "velocity_7d": r["velocity_7d"],
                "velocity_30d": r["velocity_30d"],
                "units_remaining": r["units_remaining"],
                "units_reserved": r["units_reserved"],
                "data_quality_status": r["data_quality_status"],
                "is_observed": int(bool(r["is_observed"])),
                "computed_at": r["computed_at"],
                "units_remaining_is_missing": int(r["units_remaining"] is None),
                "units_reserved_is_missing": int(r["units_reserved"] is None),
                # current inventory mix (as-of export, area level)
                "live_units_mirrored": live,
                "units_available_now": m.get("available", 0),
                "units_reserved_now": m.get("reserved", 0),
                "units_sold_now": m.get("sold", 0),
                "units_blocked_now": m.get("blocked", 0),
                # conversion, same definition the ranking service uses
                "area_deals_sold": sold_d,
                "area_deals_alive": alive_d,
                "area_conversion_norm": (Decimal(sold_d) / alive_d) if alive_d else None,
            }
        )
    cols = list(rows[0].keys()) if rows else []
    return rows, cols


async def export_ranking_scores(session, ns: str) -> tuple[list[dict], list[str]]:
    """Raw `ranking_scores` rows, verbatim, for anyone who wants the table as-is."""
    sql = sa.text("""
        SELECT rs.id, rs.unit_id, rs.area_id, rs.project_id, rs.ranking_run_id, rs.config_version_id,
               rs.score, rs.rank_in_area, rs.rank_in_project, rs.weight_coverage,
               rs.contributions, rs.feature_freshness_at, rs.computed_at
        FROM ranking_scores rs JOIN projects p ON p.id = rs.project_id
        WHERE p.source_instance_id = :ns
        ORDER BY rs.project_id, rs.rank_in_project
    """)
    rows = []
    for r in (await session.execute(sql, {"ns": ns})).mappings().all():
        d = {k: r[k] for k in r.keys()}
        d["contributions"] = json.dumps(d["contributions"], separators=(",", ":"), sort_keys=True)
        rows.append(d)
    cols = list(rows[0].keys()) if rows else []
    return rows, cols


# Requested fields with no column anywhere in the schema. Listed explicitly so the
# absence is documented rather than hidden behind an all-NULL column.
ABSENT = [
    ("units_ranking.csv", "bathrooms", "no bathroom column on units or areas"),
    ("units_ranking.csv", "price", "no price column anywhere in the schema; Mini CRM states it carries no prices"),
    ("units_ranking.csv", "price_per_sqm", "derived from price, which does not exist"),
    ("units_ranking.csv", "floor", "no floor column on units"),
    ("units_ranking.csv", "orientation", "no orientation column on units"),
    ("units_ranking.csv", "view", "no view column; `view_quality` exists only as a survey feature key with no data"),
    ("units_ranking.csv", "confidence",
     "feature_snapshots.confidence exists but the ranking pipeline never populates it (0/616 rows non-null)"),
    ("leads_deals_agents.csv", "lead_id", "no lead entity; a lead is a deals row with status='lead'"),
    ("leads_deals_agents.csv", "customer_id", "no customer table exists in either service"),
    ("leads_deals_agents.csv", "lead_source", "not modelled"),
    ("leads_deals_agents.csv", "budget", "not modelled"),
    ("leads_deals_agents.csv", "financing_status", "not modelled"),
    ("leads_deals_agents.csv", "preferred_project", "requires a customer entity, which does not exist"),
    ("leads_deals_agents.csv", "preferred_area", "requires a customer entity, which does not exist"),
    ("leads_deals_agents.csv", "preferred_unit_type", "requires a customer entity, which does not exist"),
    ("leads_deals_agents.csv", "preferred_bedrooms", "requires a customer entity, which does not exist"),
    ("leads_deals_agents.csv", "preferred_price_range", "requires a customer entity and prices; neither exists"),
    ("leads_deals_agents.csv", "contact_timestamps", "no contact/interaction table"),
    ("leads_deals_agents.csv", "next_follow_up_at", "not modelled"),
    ("leads_deals_agents.csv", "viewing_appointment", "only deals.status='viewing' exists; no appointment record"),
    ("leads_deals_agents.csv", "interaction_counts",
     "no interaction table; `deals_on_same_unit` is the nearest available proxy"),
    ("leads_deals_agents.csv", "notes", "no free-text field on deals; Mini CRM carries no PII by design"),
    ("leads_deals_agents.csv", "assigned_sales_agent",
     "not modelled; `agent_*` tables are the AI advisory agent, not sales staff"),
    ("absorption_forecasting.csv", "unit_id", "absorption_daily is area-level; it has no unit dimension"),
    ("absorption_forecasting.csv", "released_units", "'released' is not a unit status (available/reserved/sold/blocked)"),
    ("absorption_forecasting.csv", "price_metrics", "no price column anywhere"),
    ("absorption_forecasting.csv", "weekly_absorption", "not persisted; only daily + rolling 7d/30d exist"),
    ("absorption_forecasting.csv", "monthly_absorption", "not persisted; only daily + rolling 7d/30d exist"),
    ("absorption_forecasting.csv", "inventory_aging",
     "units carry no status-change timestamp, so time-in-status cannot be derived"),
    ("absorption_forecasting.csv", "forecast_target",
     "forecasting is a stub (src/jobs/forecast.py returns zero rows); no forecast output exists to export"),
]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--namespace", default="synthetic-csv-v1")
    ap.add_argument("--out", default="datasets/synthetic_v1/exports")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sf = get_session_factory()
    async with sf() as session:
        ur, ur_c = await export_units_ranking(session, args.namespace)
        ld, ld_c = await export_leads_deals(session, args.namespace)
        ab, ab_c = await export_absorption(session, args.namespace)
        rs, rs_c = await export_ranking_scores(session, args.namespace)

    for r in ld:
        r.pop("age_days_at_export", None)
    ld_c = [c for c in ld_c if c != "age_days_at_export"]

    written = {
        "units_ranking.csv": _w(out / "units_ranking.csv", ur, ur_c),
        "leads_deals_agents.csv": _w(out / "leads_deals_agents.csv", ld, ld_c),
        "absorption_forecasting.csv": _w(out / "absorption_forecasting.csv", ab, ab_c),
        "ranking_scores.csv": _w(out / "ranking_scores.csv", rs, rs_c),
    }

    dict_rows = []
    for fname, cols, sample in (
        ("units_ranking.csv", ur_c, ur), ("leads_deals_agents.csv", ld_c, ld),
        ("absorption_forecasting.csv", ab_c, ab), ("ranking_scores.csv", rs_c, rs),
    ):
        for c in cols:
            val = next((r[c] for r in sample if r.get(c) is not None), None)
            dict_rows.append(
                {
                    "file": fname,
                    "column": c,
                    "status": "PRESENT",
                    "dtype": type(val).__name__ if val is not None else "null",
                    "source": SOURCES.get(c, ""),
                    "logic": LOGIC.get(c, ""),
                }
            )
    for fname, col, reason in ABSENT:
        dict_rows.append(
            {"file": fname, "column": col, "status": "ABSENT", "dtype": "", "source": "", "logic": reason}
        )
    written["feature_dictionary.csv"] = _w(
        out / "feature_dictionary.csv", dict_rows, ["file", "column", "status", "dtype", "source", "logic"]
    )

    for k, v in written.items():
        print(f"{k}: {v} rows")
    print(f"absent-but-requested columns documented: {len(ABSENT)}")
    return 0


SOURCES = {
    "unit_id": "units.id", "external_unit_id": "units.external_unit_id", "unit_code": "units.unit_code",
    "unit_type": "units.unit_type", "unit_status": "units.status", "area_id": "areas.id",
    "area_name": "areas.area_name", "area_bedrooms": "areas.bedrooms", "area_sqm": "areas.area_sqm",
    "area_total_units_planned": "areas.total_units", "project_id": "projects.id",
    "project_name": "projects.name", "absorption_calculator": "projects.absorption_calculator",
    "score": "ranking_scores.score", "rank_in_area": "ranking_scores.rank_in_area",
    "rank_in_project": "ranking_scores.rank_in_project", "weight_coverage": "ranking_scores.weight_coverage",
    "ranking_run_id": "ranking_scores.ranking_run_id", "ranking_config_version": "ranking_configs.version",
    "min_weight_coverage": "ranking_configs.min_weight_coverage",
    "ranking_computed_at": "ranking_scores.computed_at",
    "feature_freshness_at": "ranking_scores.feature_freshness_at",
    "contributions": "ranking_scores.contributions",
    "deal_id": "deals.id", "deal_status": "deals.status", "deal_source_status": "deals.source_status",
    "reserved_at": "deals.reserved_at", "sold_at": "deals.sold_at", "lost_at": "deals.lost_at",
    "snapshot_date": "absorption_daily.stat_date", "calculator": "absorption_daily.calculator",
    "units_sold": "absorption_daily.units_sold", "velocity_7d": "absorption_daily.velocity_7d",
    "velocity_30d": "absorption_daily.velocity_30d", "units_remaining": "absorption_daily.units_remaining",
    "units_reserved": "absorption_daily.units_reserved",
    "data_quality_status": "absorption_daily.data_quality_status",
}
LOGIC = {
    "band": "src.ranking.bands.band_for: high>=0.66, medium>=0.33, else low",
    "score_percent": "src.ranking.bands.as_percent: score*100 rounded to 1dp",
    "is_available": "1 when units.status='available' (the unit_available feature's raw form)",
    "has_active_deal": "1 when the unit has a live reserved or sold deal",
    "funnel_deals_total": "count of live deals in (lead, qualified, interested, viewing)",
    "feat_unit_demand_norm_value": "min(funnel_deals_total / DEMAND_SATURATION=3, 1)",
    "feat_area_velocity_norm_value": "min((area sold in 30d / live mirrored units) / VELOCITY_SATURATION=0.20, 1)",
    "feat_area_conversion_norm_value": "area sold deals / area live deals",
    "feat_unit_available_value": "1.0 if available else 0.0",
    "features_missing_count": "count of features whose contribution source is missing_defaulted/missing_skipped",
    "was_skipped": "always 0: ranking_scores stores only units that passed min_weight_coverage",
    "funnel_stage": "derived from deals.status: funnel|holding|closed_won|closed_lost",
    "deals_on_same_unit": "count of deals sharing this unit - proxy for interaction volume",
    "days_reserved_to_sold": "sold_at - reserved_at in days, NULL when either is absent",
    "is_active_lineage": "1 when this row's calculator equals projects.absorption_calculator",
    "area_conversion_norm": "area sold deals / area live deals, same definition as the ranking feature",
    "live_units_mirrored": "count of non-deleted units in the area, as of export",
}

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
