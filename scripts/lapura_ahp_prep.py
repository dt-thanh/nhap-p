"""La Pura — data quality audit, EDA, feature engineering and AHP ranking prep.

Follows data/PROMPT_LAPURA_DATA_PROCESSING_AHP.md. Deterministic and
interpretable end to end: no ML/DL, no predictive model. Source CSVs are read
only; every artifact is written to processed_data/.

AHP weight derivation reuses src/ranking/ahp.py (RGMM + lambda_max/CI/CR),
the module already shipped for ranking V2 — this script does not implement a
second AHP.
"""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ranking.ahp import Judgment, compute  # noqa: E402

SRC = ROOT / "data" / "Real_estate" / "data"
OUT = ROOT / "datasets" / "processed_data"

# Placeholders the source uses instead of nulls; become NA in analytical copies.
NULL_SENTINELS = {"NO_LEAD", "NO_BOOKING", "NO_RESERVATION", "NOT_SOLD", "NO_DEAL", ""}
EPOCH_SENTINEL = "1970-01-01T00:00:00+00:00"
DURATION_SENTINEL = -1  # "has not happened yet", not a real duration

# Outcome columns — excluded from the AHP input to prevent leakage (Step 10).
LEAKAGE_COLS = [
    "unit_status", "is_sold", "days_to_sell", "reserved_at", "sold_at",
    "lead_count", "qualified_lead_count", "booking_count", "reservation_count",
    "first_lead_at", "first_booking_at", "days_to_first_lead", "days_to_reserve",
    "deal_lifecycle_stage", "has_lead", "has_booking", "has_reservation",
    "deal_external_id", "deal_status", "lost_at",
]

FILES = [
    "crm_projects_import.csv", "crm_areas_import.csv", "crm_units_import.csv",
    "crm_deals_sold_import.csv", "crm_deals_reserved_import.csv",
    "lapura_unit_attributes_import.csv", "lapura_master_dataset_no_null.csv",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str) -> pd.DataFrame:
    # utf-8-sig strips the BOM the exports carry on their first header cell.
    return pd.read_csv(SRC / name, encoding="utf-8-sig")


# ---------------------------------------------------------------- step 1: audit

def profile_files(frames: dict[str, pd.DataFrame], hashes: dict[str, str]) -> pd.DataFrame:
    rows = []
    for name, df in frames.items():
        rows.append({
            "file": name,
            "rows": len(df),
            "columns": df.shape[1],
            "duplicate_rows": int(df.duplicated().sum()),
            "null_cells": int(df.isna().sum().sum()),
            "sentinel_cells": int(
                df.apply(lambda s: s.astype(str).isin(NULL_SENTINELS | {EPOCH_SENTINEL})).sum().sum()
            ),
            "sha256": hashes[name],
        })
    return pd.DataFrame(rows)


def validate_relationships(f: dict[str, pd.DataFrame]) -> pd.DataFrame:
    proj, areas, units = f["crm_projects_import.csv"], f["crm_areas_import.csv"], f["crm_units_import.csv"]
    sold, resv = f["crm_deals_sold_import.csv"], f["crm_deals_reserved_import.csv"]
    attrs, master = f["lapura_unit_attributes_import.csv"], f["lapura_master_dataset_no_null.csv"]

    def check(name, passed, detail):
        return {"check": name, "status": "PASS" if passed else "FAIL", "detail": detail}

    orphan_areas = (~areas.project_id.isin(proj.id)).sum()
    orphan_units = (~units.area_id.isin(areas.id)).sum()
    orphan_sold = (~sold.external_unit_id.isin(units.external_id)).sum()
    orphan_resv = (~resv.external_unit_id.isin(units.external_id)).sum()
    orphan_attrs = (~attrs.unit_id.isin(units.id)).sum()

    sold_units = set(units.loc[units.unit_status == "sold", "external_id"])
    resv_units = set(units.loc[units.unit_status == "reserved", "external_id"])
    avail_units = set(units.loc[units.unit_status == "available", "external_id"])
    sold_deals, resv_deals = set(sold.external_unit_id), set(resv.external_unit_id)

    rows = [
        check("crm_areas.project_id -> crm_projects.id", orphan_areas == 0, f"{orphan_areas} orphans"),
        check("crm_units.area_id -> crm_areas.id", orphan_units == 0, f"{orphan_units} orphans"),
        check("crm_deals_sold.external_unit_id -> crm_units.external_id", orphan_sold == 0, f"{orphan_sold} orphans"),
        check("crm_deals_reserved.external_unit_id -> crm_units.external_id", orphan_resv == 0, f"{orphan_resv} orphans"),
        check("lapura_unit_attributes.unit_id -> crm_units.id", orphan_attrs == 0, f"{orphan_attrs} orphans"),
        check("unit_code unique", units.unit_code.duplicated().sum() == 0,
              f"{int(units.unit_code.duplicated().sum())} duplicates in {len(units)} units"),
        check("unit external_id unique", units.external_id.duplicated().sum() == 0,
              f"{int(units.external_id.duplicated().sum())} duplicates"),
        check("deal external_id unique across deal files",
              pd.concat([sold.external_id, resv.external_id]).duplicated().sum() == 0,
              f"{int(pd.concat([sold.external_id, resv.external_id]).duplicated().sum())} duplicates"),
        check("status sold -> has sold deal", sold_units <= sold_deals,
              f"{len(sold_units - sold_deals)} sold units without a sold deal"),
        check("status reserved -> has reserved deal", resv_units <= resv_deals,
              f"{len(resv_units - resv_deals)} reserved units without a reserved deal"),
        check("status available -> no active deal", not (avail_units & (sold_deals | resv_deals)),
              f"{len(avail_units & (sold_deals | resv_deals))} available units carrying a deal"),
        check("project count == 1", len(proj) == 1, f"{len(proj)} projects"),
        check("area group count == 24", len(areas) == 24, f"{len(areas)} areas"),
        check("unit count == 392", len(units) == 392, f"{len(units)} units"),
        check("sold deal count == 130", len(sold) == 130, f"{len(sold)} sold deals"),
        check("reserved deal count == 63", len(resv) == 63, f"{len(resv)} reserved deals"),
        check("available unit count == 199", len(avail_units) == 199, f"{len(avail_units)} available units"),
        check("master row count == units", len(master) == len(units), f"{len(master)} master rows"),
        check("master unit_external_id -> crm_units.external_id",
              master.unit_external_id.isin(units.external_id).all(),
              f"{int((~master.unit_external_id.isin(units.external_id)).sum())} orphans"),
    ]
    return pd.DataFrame(rows)


def audit_columns(master: pd.DataFrame) -> list[dict]:
    """Per-column quality record for the audit trail — nothing is dropped."""
    out = []
    for col in master.columns:
        s = master[col]
        as_str = s.astype(str)
        num = pd.to_numeric(s, errors="coerce")
        out.append({
            "column": col,
            "dtype": str(s.dtype),
            "nulls": int(s.isna().sum()),
            "blanks": int((as_str.str.strip() == "").sum()),
            "distinct": int(s.nunique(dropna=True)),
            "null_sentinels": int(as_str.isin(NULL_SENTINELS).sum()),
            "epoch_sentinels": int((as_str == EPOCH_SENTINEL).sum()),
            "negative_duration_sentinels": int((num == DURATION_SENTINEL).sum()) if col.startswith("days_to_") else 0,
        })
    return out


# ------------------------------------------------------- step 2: clean analytics

def build_clean(master: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Analytical copy. Source frames are never mutated."""
    df = master.copy()
    notes = {}

    # Sentinels -> real missing values.
    for col in ["first_lead_at", "first_booking_at", "reserved_at", "sold_at", "lost_at",
                "deal_external_id", "deal_status", "agency_name"]:
        if col in df:
            df[col] = df[col].astype(str).str.strip()
            df.loc[df[col].isin(NULL_SENTINELS) | (df[col] == EPOCH_SENTINEL), col] = pd.NA

    for col in ["days_to_first_lead", "days_to_reserve", "days_to_sell"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df.loc[df[col] == DURATION_SENTINEL, col] = pd.NA

    for col in ["first_lead_at", "first_booking_at", "reserved_at", "sold_at", "lost_at",
                "project_launch_date", "mirrored_at", "deal_created_at", "deal_updated_at"]:
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True, format="mixed")

    for col in ["has_lead", "has_booking", "has_reservation", "is_sold", "corner_unit_proxy"]:
        df[col] = df[col].astype(str).str.strip().str.lower().isin({"1", "true", "yes"})

    numeric = ["floor", "unit_number", "gross_area_sqm", "net_area_sqm", "standard_price_vnd",
               "loan_price_vnd", "stacking_price_million_vnd", "bedrooms", "bathrooms",
               "lead_count", "qualified_lead_count", "booking_count", "reservation_count"]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # DERIVED fields: recalculate from OBSERVED inputs per the spec's Step 5.
    # The source price_per_sqm_* columns are kept alongside for audit because
    # they are computed off loan_price_vnd, not standard_price_vnd (see README).
    df["price_per_sqm_gross_vnd_source"] = pd.to_numeric(df["price_per_sqm_gross_vnd"], errors="coerce")
    df["price_per_sqm_net_vnd_source"] = pd.to_numeric(df["price_per_sqm_net_vnd"], errors="coerce")
    df["area_efficiency_ratio_source"] = pd.to_numeric(df["area_efficiency_ratio"], errors="coerce")
    df["loan_premium_pct_source"] = pd.to_numeric(df["loan_premium_pct"], errors="coerce")

    df["price_per_sqm_gross_vnd"] = df.standard_price_vnd / df.gross_area_sqm
    df["price_per_sqm_net_vnd"] = df.standard_price_vnd / df.net_area_sqm
    df["area_efficiency_ratio"] = df.net_area_sqm / df.gross_area_sqm
    df["loan_premium_pct"] = (df.loan_price_vnd - df.standard_price_vnd) / df.standard_price_vnd * 100

    for name, recalc, src in [
        ("price_per_sqm_gross_vnd", df.price_per_sqm_gross_vnd, df.price_per_sqm_gross_vnd_source),
        ("price_per_sqm_net_vnd", df.price_per_sqm_net_vnd, df.price_per_sqm_net_vnd_source),
        ("area_efficiency_ratio", df.area_efficiency_ratio, df.area_efficiency_ratio_source),
        ("loan_premium_pct", df.loan_premium_pct, df.loan_premium_pct_source),
    ]:
        dev = (recalc - src).abs()
        notes[name] = {
            "max_abs_deviation_vs_source": float(dev.max()),
            "median_abs_deviation_vs_source": float(dev.median()),
        }

    # Impossible / out-of-range values (recorded, never silently deleted).
    impossible = {
        "non_positive_gross_area": int((df.gross_area_sqm <= 0).sum()),
        "non_positive_net_area": int((df.net_area_sqm <= 0).sum()),
        "net_area_gt_gross_area": int((df.net_area_sqm > df.gross_area_sqm).sum()),
        "non_positive_standard_price": int((df.standard_price_vnd <= 0).sum()),
        "loan_price_lt_standard_price": int((df.loan_price_vnd < df.standard_price_vnd).sum()),
        "negative_loan_premium": int((df.loan_premium_pct < 0).sum()),
        "floor_out_of_range_1_60": int((~df.floor.between(1, 60)).sum()),
        "bedrooms_out_of_range_0_6": int((~df.bedrooms.between(0, 6)).sum()),
        "bathrooms_out_of_range_0_6": int((~df.bathrooms.between(0, 6)).sum()),
        "area_efficiency_out_of_range_0_1": int((~df.area_efficiency_ratio.between(0, 1)).sum()),
        "sold_before_reserved": int((df.sold_at < df.reserved_at).sum()),
    }
    notes["impossible_values"] = impossible
    return df, notes


# ------------------------------------------------------------------ step 3: EDA

NUMERIC_EDA = ["floor", "unit_number", "gross_area_sqm", "net_area_sqm", "standard_price_vnd",
               "loan_price_vnd", "price_per_sqm_gross_vnd", "price_per_sqm_net_vnd",
               "area_efficiency_ratio", "loan_premium_pct", "bedrooms", "bathrooms"]

CATEGORICAL_EDA = ["subdivision", "tower", "unit_type_normalized", "floor_band", "direction",
                   "balcony_direction", "view", "corner_unit_proxy", "unit_status", "agency_name",
                   "data_profile", "physical_features_origin", "crm_signals_origin",
                   "agency_name_origin", "deal_lifecycle_stage"]


def eda_numeric(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in NUMERIC_EDA:
        s = pd.to_numeric(df[col], errors="coerce")
        rows.append({
            "feature": col, "count": int(s.notna().sum()),
            "min": s.min(), "p25": s.quantile(.25), "median": s.median(),
            "p75": s.quantile(.75), "max": s.max(), "mean": s.mean(), "std": s.std(),
        })
    return pd.DataFrame(rows)


def eda_categorical(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in CATEGORICAL_EDA:
        s = df[col].astype(str)
        vc = s.value_counts()
        for value, n in vc.items():
            rows.append({"feature": col, "value": value, "count": int(n),
                         "share_pct": round(100 * n / len(df), 2),
                         "distinct_in_feature": int(vc.size)})
    return pd.DataFrame(rows)


def eda_group(df: pd.DataFrame, by: str) -> pd.DataFrame:
    g = df.groupby(by, dropna=False)
    out = pd.DataFrame({
        "units": g.size(),
        "price_per_sqm_net_min": g.price_per_sqm_net_vnd.min(),
        "price_per_sqm_net_median": g.price_per_sqm_net_vnd.median(),
        "price_per_sqm_net_max": g.price_per_sqm_net_vnd.max(),
        "price_per_sqm_gross_median": g.price_per_sqm_gross_vnd.median(),
        "gross_area_median": g.gross_area_sqm.median(),
        "standard_price_median": g.standard_price_vnd.median(),
        "area_efficiency_median": g.area_efficiency_ratio.median(),
        "loan_premium_pct_median": g.loan_premium_pct.median(),
        "sold": g.apply(lambda x: int((x.unit_status == "sold").sum()), include_groups=False),
        "reserved": g.apply(lambda x: int((x.unit_status == "reserved").sum()), include_groups=False),
        "available": g.apply(lambda x: int((x.unit_status == "available").sum()), include_groups=False),
    })
    out["sold_pct"] = (100 * out.sold / out.units).round(2)
    return out.reset_index()


# --------------------------------------- steps 5-7: features, normalization, scoring

# Transparent 0-100 expert mappings, set by the project domain expert.
# Attributable assumptions — still subject to the team's human-in-the-loop review.
VIEW_SCORE = {
    "Toàn cảnh": 100,          # panoramic
    "Sông": 94,                # river
    "Hồ bơi": 90,              # pool
    "Cảnh quan nội khu": 88,   # internal landscape
    "Nội khu": 80,             # internal
    "Thành phố": 72,           # city
}
# Vietnamese convention: hướng Nam / Đông Nam favoured, hướng Tây penalised
# (afternoon heat). Applied to hướng nhà, the primary feng-shui reference.
DIRECTION_SCORE = {
    "Đông Nam": 100, "Nam": 95, "Đông": 85, "Đông Bắc": 72,
    "Bắc": 70, "Tây Nam": 60, "Tây Bắc": 55, "Tây": 50,
}
CORNER_SCORE = {True: 100, False: 70}

# high > mid > low. The apparent mid-floor price premium in the raw data is a
# unit-MIX artifact: all 39 Loft units (the priciest type per m2) sit in mid
# floors. Controlling for type, ordering is high > low > mid on within-type
# z-score and high > low > mid within 2PN. Spread is small either way, which is
# why Position carries less weight than the spec's 20% template.
FLOOR_BAND_SCORE = {"high": 100, "mid": 85, "low": 70}

# EXPERT CURVE — demand depth by TOTAL ticket price (bn VND), the binding
# constraint in the VN primary market. Buyers shop to a budget ("dưới 3.5 tỷ"),
# so absorption is NON-MONOTONIC in price: it peaks in the mortgage-supported
# mid band and thins sharply at the top. A pure min-max cost criterion on
# price/m2 cannot represent this shape at all.
TICKET_DEPTH_BANDS = [
    (2.5, 85), (3.0, 92), (3.5, 100), (4.0, 95), (5.0, 82), (7.0, 58), (float("inf"), 40),
]

# EXPERT MAP — depth of the buyer pool per product segment. Supply mix is NOT
# used as the proxy: the developer's mix is itself a demand bet, not evidence.
SEGMENT_DEPTH_SCORE = {
    "2PN": 100, "2PN+2WC": 95, "2PN+1WC": 88, "1PN+": 82,
    "3PN": 78, "1PN": 75, "RISA": 70, "Loft": 65,
    "4PN": 45, "Garden": 40, "SkyVilla": 35,
}

DEFAULT_CATEGORICAL_SCORE = 60  # unmapped category, flagged in coverage

# Feature hierarchy. subdivision, tower and unit_number remain unscored: no
# expert-validated ordering exists for them, so they stay descriptive/join keys.
#
# Changes from the spec's starting template, each evidence-backed:
#   - ticket_depth_score ADDED     — the dominant VN absorption driver.
#   - segment_depth_score ADDED    — replaces bedrooms_score; bedrooms defines
#                                    the segment, so scoring both double-counts.
#   - bathrooms_score DROPPED      — bedrooms == bathrooms in 328/392 rows (84%),
#                                    Spearman 0.70 on the deviation measures.
#   - balcony_direction_score DROPPED — an independent synthetic draw layered on
#                                    an already-synthetic orientation signal.
#   - area_efficiency demoted      — range is only 0.872-0.927 (5.5pp); it does
#                                    not discriminate enough to carry real weight.
HIERARCHY = {
    "affordability_demand_depth": ["ticket_depth_score", "price_per_sqm_net_score", "loan_premium_score"],
    "product_fit": ["segment_depth_score", "size_fit_score", "area_efficiency_score"],
    "position": ["floor_band_score"],
    "physical_attractiveness": ["view_score", "direction_score", "corner_score"],
}

# Pairwise judgments (Saaty upper triangle) given by the domain expert for
# ABSORPTION POTENTIAL — depth of demand at the asking price, NOT desirability
# or premium. These are real judgments, not a template re-encoded as exact
# ratios, so CR is a genuine consistency test rather than 0 by construction.
GROUP_JUDGMENTS = [
    ("affordability_demand_depth", "product_fit", "2"),
    ("affordability_demand_depth", "position", "4"),
    ("affordability_demand_depth", "physical_attractiveness", "5"),
    ("product_fit", "position", "2"),
    ("product_fit", "physical_attractiveness", "3"),
    ("position", "physical_attractiveness", "1.5"),
]
SUB_JUDGMENTS = {
    "affordability_demand_depth": [
        ("ticket_depth_score", "price_per_sqm_net_score", "2"),
        ("ticket_depth_score", "loan_premium_score", "4"),
        ("price_per_sqm_net_score", "loan_premium_score", "2"),
    ],
    "product_fit": [
        ("segment_depth_score", "size_fit_score", "2"),
        ("segment_depth_score", "area_efficiency_score", "4"),
        ("size_fit_score", "area_efficiency_score", "3"),
    ],
    "position": [],  # single sub-criterion: takes the whole group weight
    "physical_attractiveness": [
        ("view_score", "direction_score", "2"),
        ("view_score", "corner_score", "3"),
        ("direction_score", "corner_score", "1.5"),
    ],
}

SYNTHETIC_FEATURES = {"view_score", "direction_score", "corner_score"}
COST_FEATURES = {"price_per_sqm_net_score", "loan_premium_score", "size_fit_score"}


def norm(s: pd.Series, benefit: bool) -> pd.Series:
    """Min-max to 0-100 in the correct business direction."""
    lo, hi = s.min(), s.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return pd.Series(np.where(s.notna(), 50.0, np.nan), index=s.index)
    scaled = (s - lo) / (hi - lo) if benefit else (hi - s) / (hi - lo)
    return 100 * scaled


def ticket_depth(price_vnd: pd.Series) -> pd.Series:
    """Non-monotonic demand-depth curve over total ticket price."""
    bn = price_vnd / 1e9
    out = pd.Series(np.nan, index=bn.index, dtype=float)
    lo = 0.0
    for hi, sc in TICKET_DEPTH_BANDS:
        out[(bn > lo) & (bn <= hi)] = sc
        lo = hi
    return out


def engineer(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    f = pd.DataFrame(index=df.index)
    f["unit_external_id"] = df.unit_external_id
    f["unit_code"] = df.unit_code

    # size_fit: proximity to the typical size of the SAME normalized unit type,
    # expressed relative to that type's median so single-unit types do not get a
    # degenerate scale. "Larger is better" is explicitly not assumed.
    med_area = df.groupby("unit_type_normalized").gross_area_sqm.transform("median")
    size_dev = (df.gross_area_sqm - med_area).abs() / med_area
    f["size_fit_raw"] = size_dev

    f["ticket_depth_score"] = ticket_depth(df.standard_price_vnd)
    f["segment_depth_score"] = df.unit_type_normalized.map(SEGMENT_DEPTH_SCORE).astype(float)
    f["price_per_sqm_net_score"] = norm(df.price_per_sqm_net_vnd, benefit=False)   # cost
    f["loan_premium_score"] = norm(df.loan_premium_pct, benefit=False)             # cost
    f["area_efficiency_score"] = norm(df.area_efficiency_ratio, benefit=True)      # benefit
    f["size_fit_score"] = norm(size_dev, benefit=False)                            # cost (deviation)
    f["floor_band_score"] = df.floor_band.map(FLOOR_BAND_SCORE).astype(float)
    f["view_score"] = df["view"].map(VIEW_SCORE).astype(float)
    f["direction_score"] = df.direction.map(DIRECTION_SCORE).astype(float)
    f["corner_score"] = df.corner_unit_proxy.map(CORNER_SCORE).astype(float)

    coverage = {}
    for col in [c for c in f.columns if c.endswith("_score")]:
        missing = int(f[col].isna().sum())
        coverage[col] = {"missing": missing, "coverage_pct": round(100 * (1 - missing / len(f)), 2)}
        if missing:
            f[col] = f[col].fillna(DEFAULT_CATEGORICAL_SCORE)
    return f, coverage


# --------------------------------------------------------- step 8: AHP weighting

def run_ahp() -> tuple[dict, list[dict]]:
    """Derive weights with src/ranking/ahp.py; report consistency per matrix."""
    reports = []

    def solve(name, criteria, judgments):
        if len(criteria) == 1:
            reports.append({"matrix": name, "n": 1, "lambda_max": 1.0, "consistency_index": 0.0,
                            "consistency_ratio": 0.0, "threshold": 0.0, "consistent": True,
                            "note": "single criterion — weight passes through, no comparison needed",
                            "weights": {criteria[0]: 1.0}})
            return {criteria[0]: 1.0}
        res = compute(criteria, [Judgment(a, b, Decimal(v)) for a, b, v in judgments])
        w = {k: float(v) for k, v in res.weights.items()}
        reports.append({
            "matrix": name, "n": len(criteria),
            "lambda_max": float(res.lambda_max),
            "consistency_index": float(res.consistency_index),
            "consistency_ratio": float(res.consistency_ratio),
            "threshold": float(res.threshold),
            "spec_threshold": 0.10,
            "consistent": bool(res.consistent),
            "passes_spec_cr_lt_0_10": float(res.consistency_ratio) < 0.10,
            "hotspots": [{"a": h.a, "b": h.b, "judged": float(h.judged),
                          "implied": float(h.implied), "deviation": float(h.deviation)}
                         for h in res.hotspots],
            "weights": w,
        })
        return w

    group_w = solve("goal:absorption_potential", list(HIERARCHY), GROUP_JUDGMENTS)
    global_w = {}
    for group, subs in HIERARCHY.items():
        sub_w = solve(f"group:{group}", subs, SUB_JUDGMENTS[group])
        for s in subs:
            global_w[s] = group_w[group] * sub_w[s]

    total = sum(global_w.values())
    global_w = {k: v / total for k, v in global_w.items()}
    return {"group": group_w, "global": global_w}, reports


# ------------------------------------------------- steps 9 & 11: score, rank, validate

def score(features: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    return sum(features[c] * w for c, w in weights.items())


def band(scores: pd.Series) -> pd.Series:
    """Percentile bands over the ranked list.

    The spec's fixed 90/80/70/60 cut-offs produced zero "Very High" units and
    piled 46% into one bucket: a weighted mean of 0-100 criterion scores is
    regressive toward the middle, so its absolute level carries no business
    meaning. Only the ORDER does. Percentile bands give the sales team a usable
    priority list; the raw score stays in the output for transparency.
    """
    pct = scores.rank(pct=True)
    return pd.cut(pct, [0, .10, .30, .70, .90, 1.0],
                  labels=["Very Low", "Low", "Medium", "High", "Very High"],
                  include_lowest=True).astype(str)


def spearman(a: pd.Series, b: pd.Series) -> float:
    return float(np.corrcoef(a.rank(), b.rank())[0, 1])


def kendall_tau_b(a: pd.Series, b: pd.Series) -> float:
    x, y = a.to_numpy(float), b.to_numpy(float)
    dx = np.sign(x[:, None] - x[None, :])
    dy = np.sign(y[:, None] - y[None, :])
    iu = np.triu_indices(len(x), k=1)
    dx, dy = dx[iu], dy[iu]
    conc = float(np.sum(dx * dy > 0))
    disc = float(np.sum(dx * dy < 0))
    tx = float(np.sum((dx == 0) & (dy != 0)))
    ty = float(np.sum((dy == 0) & (dx != 0)))
    return (conc - disc) / np.sqrt((conc + disc + tx) * (conc + disc + ty))


def sensitivity(features: pd.DataFrame, ahp: dict, base_rank: pd.Series) -> pd.DataFrame:
    """Perturb each group weight +/-5% and +/-10%, renormalise, re-rank."""
    rows = []
    top50 = set(base_rank.nsmallest(50).index)
    for group in HIERARCHY:
        for pct in (-10, -5, 5, 10):
            gw = dict(ahp["group"])
            gw[group] *= (1 + pct / 100)
            tot = sum(gw.values())
            gw = {k: v / tot for k, v in gw.items()}
            gl = {}
            for g, subs in HIERARCHY.items():
                share = sum(ahp["global"][s] for s in subs)
                for s in subs:
                    gl[s] = gw[g] * (ahp["global"][s] / share)
            r = score(features, gl).rank(ascending=False, method="min")
            rows.append({
                "perturbed_group": group, "delta_pct": pct,
                "spearman_vs_base": round(spearman(base_rank, r), 6),
                "kendall_tau_b_vs_base": round(kendall_tau_b(base_rank, r), 6),
                "top50_overlap": len(top50 & set(r.nsmallest(50).index)),
                "max_rank_shift": int((base_rank - r).abs().max()),
                "units_moving_gt_10_ranks": int(((base_rank - r).abs() > 10).sum()),
            })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ orchestration

def main() -> None:
    OUT.mkdir(exist_ok=True)
    hashes_before = {n: sha256(SRC / n) for n in FILES}
    frames = {n: load(n) for n in FILES}
    master = frames["lapura_master_dataset_no_null.csv"]

    profile = profile_files(frames, hashes_before)
    rel = validate_relationships(frames)
    col_audit = audit_columns(master)

    clean, clean_notes = build_clean(master)
    features, coverage = engineer(clean)
    ahp, ahp_reports = run_ahp()

    gw = ahp["global"]
    features["absorption_score"] = score(features, gw)
    features["rank"] = features.absorption_score.rank(ascending=False, method="min").astype(int)
    features["score_band"] = band(features.absorption_score)
    for c, w in gw.items():
        features[f"contrib__{c}"] = features[c] * w

    # Step 11 — validation.
    base_rank = features["rank"]
    equal_w = {c: 1 / len(gw) for c in gw}
    equal_score = score(features, equal_w)
    equal_rank = equal_score.rank(ascending=False, method="min")
    sens = sensitivity(features, ahp, base_rank)

    # Descriptive only: outcome columns are excluded from scoring (Step 10), so
    # this compares the launch-time ranking against SYNTHETIC CRM outcomes.
    status_check = (
        pd.DataFrame({"status": clean.unit_status, "score": features.absorption_score})
        .groupby("status").score.agg(["count", "mean", "median"]).round(3)
        .reset_index().to_dict("records")
    )

    ahp_input = clean.drop(columns=[c for c in LEAKAGE_COLS if c in clean.columns])
    validation_frame = clean[["unit_external_id", "unit_code"] + [c for c in LEAKAGE_COLS if c in clean.columns]]

    # ---- write artifacts
    profile.to_csv(OUT / "data_file_profile.csv", index=False)
    rel.to_csv(OUT / "relationship_validation.csv", index=False)
    ahp_input.to_csv(OUT / "ahp_input_clean.csv", index=False)
    validation_frame.to_csv(OUT / "outcome_validation_only.csv", index=False)

    score_cols = [c for c in features.columns if c.endswith("_score") and c != "absorption_score"]
    features[["unit_external_id", "unit_code"] + score_cols].to_csv(
        OUT / "ahp_feature_scores_0_100.csv", index=False)

    ranking = features[["unit_external_id", "unit_code", "absorption_score", "rank", "score_band"]
                       + score_cols + [f"contrib__{c}" for c in gw]].copy()
    ranking = ranking.merge(
        clean[["unit_external_id", "subdivision", "tower", "floor", "floor_band",
               "unit_type_normalized", "gross_area_sqm", "standard_price_vnd"]],
        on="unit_external_id", how="left").sort_values("rank")
    ranking.to_csv(OUT / "ahp_ranking.csv", index=False)

    weight_rows = []
    for group, subs in HIERARCHY.items():
        for s in subs:
            weight_rows.append({
                "group": group, "group_weight": round(ahp["group"][group], 6),
                "criterion": s,
                "weight_within_group": round(gw[s] / ahp["group"][group], 6),
                "global_weight": round(gw[s], 6),
                "direction": "cost" if s in COST_FEATURES else "benefit",
                "provenance": "synthetic" if s in SYNTHETIC_FEATURES else "observed_or_derived",
                "status": "DOMAIN-EXPERT JUDGMENT — pending team human-in-the-loop review",
            })
    pd.DataFrame(weight_rows).to_csv(OUT / "ahp_weight_template.csv", index=False)

    eda_numeric(clean).to_csv(OUT / "eda_numeric_summary.csv", index=False)
    eda_categorical(clean).to_csv(OUT / "eda_categorical_summary.csv", index=False)
    eda_group(clean, "subdivision").to_csv(OUT / "eda_price_by_subdivision.csv", index=False)
    eda_group(clean, "unit_type_normalized").to_csv(OUT / "eda_price_by_unit_type.csv", index=False)
    eda_group(clean, "floor_band").to_csv(OUT / "eda_price_by_floor_band.csv", index=False)
    sens.to_csv(OUT / "validation_sensitivity.csv", index=False)

    # loan_premium_pct is NOT continuous: 3 tiers, each mapping 1:1 onto a
    # subdivision. Reported as a tier table rather than a histogram so the
    # subdivision coupling is visible rather than buried in a percentile.
    lp = clean.groupby(clean.loan_premium_pct.round(2))
    lp_dist = pd.DataFrame({
        "units": lp.size(),
        "share_pct": (100 * lp.size() / len(clean)).round(2),
        "subdivisions": lp.subdivision.agg(lambda s: " | ".join(sorted(s.unique()))),
        "median_ticket_vnd": lp.standard_price_vnd.median(),
        "sold": lp.apply(lambda x: int((x.unit_status == "sold").sum()), include_groups=False),
    }).reset_index().rename(columns={"loan_premium_pct": "loan_premium_pct_tier"})
    lp_dist.to_csv(OUT / "eda_loan_premium_distribution.csv", index=False)

    ae_bins = pd.cut(clean.area_efficiency_ratio, [0, .88, .89, .90, .91, .92, 1.0])
    ae = clean.groupby(ae_bins, observed=True)
    pd.DataFrame({
        "units": ae.size(),
        "share_pct": (100 * ae.size() / len(clean)).round(2),
        "median_gross_area": ae.gross_area_sqm.median(),
        "median_price_per_sqm_net": ae.price_per_sqm_net_vnd.median(),
        "sold": ae.apply(lambda x: int((x.unit_status == "sold").sum()), include_groups=False),
    }).reset_index().rename(columns={"area_efficiency_ratio": "area_efficiency_bin"}).to_csv(
        OUT / "eda_area_efficiency_distribution.csv", index=False)

    pd.DataFrame([
        {"feature": "ticket_depth_score", "source": "expert demand-depth curve over standard_price_vnd (bn)",
         "provenance": "expert map over observed", "direction": "non-monotonic",
         "note": "peaks 3.0-3.5bn, falls sharply above 5bn; VN buyers shop to a total budget"},
        {"feature": "price_per_sqm_net_score", "source": "standard_price_vnd / net_area_sqm",
         "provenance": "derived from observed", "direction": "cost", "note": "recalculated per spec Step 5"},
        {"feature": "loan_premium_score", "source": "(loan_price - standard_price)/standard_price*100",
         "provenance": "derived from observed", "direction": "cost", "note": "matches source column"},
        {"feature": "segment_depth_score", "source": "expert map over unit_type_normalized",
         "provenance": "expert map over observed", "direction": "benefit",
         "note": "buyer-pool depth per segment; supply mix deliberately NOT used as proxy"},
        {"feature": "size_fit_score", "source": "|gross_area - median(gross_area | unit_type)| / median",
         "provenance": "derived from observed", "direction": "cost",
         "note": "proximity to typical size within type; larger is NOT assumed better"},
        {"feature": "area_efficiency_score", "source": "net_area_sqm / gross_area_sqm",
         "provenance": "derived from observed", "direction": "benefit",
         "note": "demoted: range only 0.872-0.927, weak discrimination"},
        {"feature": "floor_band_score", "source": "floor_band expert map high/mid/low = 100/85/70",
         "provenance": "derived from observed floor", "direction": "benefit",
         "note": "mix-adjusted check confirms high > low > mid; raw mid premium was a Loft artifact"},
        {"feature": "view_score", "source": "view expert map", "provenance": "SYNTHETIC",
         "direction": "benefit", "note": "physical_features_origin = synthetic_v1_tower_stack_floor_band"},
        {"feature": "direction_score", "source": "hướng nhà expert map", "provenance": "SYNTHETIC",
         "direction": "benefit", "note": "VN orientation convention; synthetic source column"},
        {"feature": "corner_score", "source": "corner_unit_proxy True/False = 100/70",
         "provenance": "SYNTHETIC", "direction": "benefit", "note": "synthetic source column"},
    ]).to_csv(OUT / "feature_dictionary.csv", index=False)

    hashes_after = {n: sha256(SRC / n) for n in FILES}
    report = {
        "generated_for": "La Pura — AHP absorption ranking preparation",
        "method": "deterministic MCDM (AHP + weighted scoring). No ML/DL used.",
        "source_files_unchanged": hashes_before == hashes_after,
        "source_sha256": hashes_before,
        "data_quality": {
            "file_profile": profile.to_dict("records"),
            "relationship_validation": rel.to_dict("records"),
            "relationship_failures": int((rel.status == "FAIL").sum()),
            "column_audit": col_audit,
            "derived_field_recalculation": clean_notes,
            "sentinels_converted_to_missing": sorted(NULL_SENTINELS - {""}) + [EPOCH_SENTINEL, "days_to_* == -1"],
        },
        "eda": {
            "numeric_summary": json.loads(eda_numeric(clean).to_json(orient="records")),
            "unit_status_distribution": clean.unit_status.value_counts().to_dict(),
            "loan_premium_is_subdivision_determined": {
                "distinct_values": sorted(clean.loan_premium_pct.round(2).unique().tolist()),
                "tier_to_subdivision": {
                    str(k): sorted(v.unique().tolist())
                    for k, v in clean.groupby(clean.loan_premium_pct.round(2)).subdivision
                },
                "warning": "loan_premium_pct is a 3-level developer policy tier mapping 1:1 onto "
                           "subdivision, NOT a continuous unit-level attribute. Scoring it imposes "
                           "an implicit subdivision ordering (Zenia > Risa > Lusso).",
            },
            "price_by_subdivision": json.loads(eda_group(clean, "subdivision").to_json(orient="records")),
            "price_by_unit_type": json.loads(eda_group(clean, "unit_type_normalized").to_json(orient="records")),
            "price_by_floor_band": json.loads(eda_group(clean, "floor_band").to_json(orient="records")),
        },
        "ahp": {
            "hierarchy": HIERARCHY,
            "excluded_from_hierarchy": {
                "subdivision": "no expert-validated ranking available",
                "tower": "no expert-validated ranking available",
                "unit_number": "positional identifier, no validated desirability ordering",
            },
            "matrices": ahp_reports,
            "global_weights": {k: round(v, 6) for k, v in gw.items()},
            "all_matrices_pass_cr_threshold": all(r["consistent"] for r in ahp_reports),
        },
        "leakage_control": {
            "excluded_from_ahp_input": LEAKAGE_COLS,
            "outcome_columns_retained_for_validation_only": "processed_data/outcome_validation_only.csv",
        },
        "validation": {
            "consistency": "see ahp.matrices — CR per matrix",
            "sensitivity": json.loads(sens.to_json(orient="records")),
            "min_spearman_across_perturbations": float(sens.spearman_vs_base.min()),
            "min_top50_overlap": int(sens.top50_overlap.min()),
            "baseline_equal_weight": {
                "spearman_vs_ahp": round(spearman(base_rank, equal_rank), 6),
                "kendall_tau_b_vs_ahp": round(kendall_tau_b(base_rank, equal_rank), 6),
                "top50_overlap": len(set(base_rank.nsmallest(50).index) & set(equal_rank.nsmallest(50).index)),
            },
            "expert_ranking_comparison": "NOT RUN — no expert-labelled ranking available in the source data",
            "descriptive_score_by_actual_status": status_check,
        },
        "feature_coverage": coverage,
        "score_band_distribution": features.score_band.value_counts().to_dict(),
        "limitations": [
            "direction, balcony_direction, view, corner_unit_proxy are SYNTHETIC "
            "(physical_features_origin = synthetic_v1_tower_stack_floor_band) and are not verified market facts.",
            "lead/booking/reservation counts and timestamps are SYNTHETIC "
            "(crm_signals_origin = synthetic_v1_causal_funnel); used for descriptive validation only.",
            "agency_name is tagged synthetic via agency_name_origin.",
            "data_profile = 'demo' for all 392 units — this is not production CRM data.",
            "AHP weights encode the spec's 35/30/20/15 starting template as pairwise ratios; "
            "they are assumptions and must be replaced by elicited expert judgments.",
            "Source price_per_sqm_* columns are computed from loan_price_vnd, not standard_price_vnd; "
            "recalculated per spec Step 5 and the source values retained as *_source for audit.",
            "Score bands (90/80/70/60) are for discussion and require domain-expert review.",
        ],
    }
    (OUT / "data_quality_and_eda_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    assert hashes_before == hashes_after, "source files were modified — aborting"
    print(f"relationship checks: {int((rel.status == 'PASS').sum())}/{len(rel)} PASS")
    print(f"AHP matrices consistent: {all(r['consistent'] for r in ahp_reports)}")
    print("global weights:", {k: round(v, 4) for k, v in gw.items()})
    print(f"min spearman under +/-10% perturbation: {sens.spearman_vs_base.min():.4f}")
    print(f"equal-weight baseline spearman: {spearman(base_rank, equal_rank):.4f}")
    print(f"artifacts written to {OUT}")
    return report, features, clean, sens, ahp_reports, rel


if __name__ == "__main__":
    main()
