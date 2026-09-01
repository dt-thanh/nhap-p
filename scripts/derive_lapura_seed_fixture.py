"""Derive a dedicated, reviewable, immutable La Pura seed fixture from the
read-only source CSVs in `data/Real_estate/data/`, plus a separate per-run
manifest recording source-row -> fixture-key lineage.

    python -m scripts.derive_lapura_seed_fixture --dry-run
    python -m scripts.derive_lapura_seed_fixture --write-fixture --batch-id <id>

This script NEVER touches a database and NEVER calls an HTTP API — it only
reads the source CSVs (read-only, SHA-256-verified) and writes two local
files: `scripts/fixtures/lapura_normalized_seed_v1.json` (the fixture, in the
exact shape `scripts/seed_mini_crm_from_json.py` already consumes — see that
script's own module docstring for the shape) and
`scripts/fixtures/manifests/lapura_seed_manifest_<batch_id>.json` (Pass-1 of
the manifest: source-row -> fixture `external_key`; Pass-2, filling in the
REAL MiniCRM/AbsorpIQ-assigned ids, only exists after an actual seed run).

Deliberately additive-only and namespaced: every produced `external_key` is
prefixed `la-pura`, distinct from every existing project's fixture keys in
`docs/mini_crm_seed.json` (never touched by this script). The MiniCRM-side
`external_id` (e.g. `P-0001`) is server-assigned at seed time and is NOT
chosen here — this script has no opinion on it, which is what makes the
existing "P-0001 already belongs to another project" collision moot: nothing
here ever asks MiniCRM to use a specific `external_id`.

The fixture is treated as IMMUTABLE once written: re-deriving from the same,
unchanged source CSVs must reproduce byte-identical content (`--write-fixture`
is idempotent); re-deriving from CHANGED source CSVs refuses to silently
overwrite an already-written fixture (see `_write_fixture_or_refuse`).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "data" / "Real_estate" / "data"
OUT_FIXTURE = REPO_ROOT / "scripts" / "fixtures" / "lapura_normalized_seed_v1.json"
OUT_MANIFEST_DIR = REPO_ROOT / "scripts" / "fixtures" / "manifests"

SOURCE_FILES = (
    "crm_projects_import.csv",
    "crm_areas_import.csv",
    "crm_units_import.csv",
    "crm_deals_sold_import.csv",
    "crm_deals_reserved_import.csv",
    "lapura_unit_attributes_import.csv",
)

SOURCE_SYSTEM = "lapura_ahp_prep"
PROJECT_EXTERNAL_KEY = "prj-la-pura"


class FixtureBuildError(RuntimeError):
    """A validation failure while deriving the fixture — never a partial write."""


def _read_csv(name: str) -> tuple[list[dict[str, str]], str]:
    path = SOURCE_DIR / name
    if not path.exists():
        try:
            shown = path.relative_to(REPO_ROOT)
        except ValueError:
            shown = path
        raise FixtureBuildError(f"Source file not found: {shown}")
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    return rows, sha256


def _area_key(source_external_id: str) -> str:
    return f"area-la-pura-{source_external_id.lower()}"


def _unit_key(source_external_id: str) -> str:
    return f"unit-la-pura-{source_external_id.lower()}"


def _deal_key(source_external_id: str) -> str:
    return f"deal-la-pura-{source_external_id.lower()}"


def _to_float(value: str) -> float:
    return float(value)


def _to_int(value: str) -> int:
    return int(float(value))


def _to_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


def load_source() -> tuple[dict[str, tuple[list[dict[str, str]], str]], list[str]]:
    """Reads every source file. Returns (per-file rows+sha256, ordered file list)."""
    data: dict[str, tuple[list[dict[str, str]], str]] = {}
    for name in SOURCE_FILES:
        data[name] = _read_csv(name)
    return data, list(SOURCE_FILES)


def validate(data: dict[str, tuple[list[dict[str, str]], str]]) -> list[str]:
    """Independent re-verification of the source data's own structural claims
    (`relationship_validation.csv`'s 19 checks) — re-derived here from the raw
    CSVs, not trusted from that report. Returns a list of human-readable
    problems; empty means clean. Never raises — the caller decides whether to
    abort.
    """
    problems: list[str] = []
    projects = data["crm_projects_import.csv"][0]
    areas = data["crm_areas_import.csv"][0]
    units = data["crm_units_import.csv"][0]
    sold = data["crm_deals_sold_import.csv"][0]
    reserved = data["crm_deals_reserved_import.csv"][0]
    attrs = data["lapura_unit_attributes_import.csv"][0]

    if len(projects) != 1:
        problems.append(f"expected exactly 1 project, found {len(projects)}")
    if len(areas) != 24:
        problems.append(f"expected exactly 24 areas, found {len(areas)}")
    if len(units) != 392:
        problems.append(f"expected exactly 392 units, found {len(units)}")

    project_ids = {p["id"] for p in projects}
    area_ids = {a["id"] for a in areas}
    unit_external_ids = {u["external_id"] for u in units}

    for a in areas:
        if a["project_id"] not in project_ids:
            problems.append(f"area {a['external_id']}: project_id {a['project_id']} does not resolve")
    for u in units:
        if u["area_id"] not in area_ids:
            problems.append(f"unit {u['external_id']}: area_id {u['area_id']} does not resolve")
    for d in sold + reserved:
        if d["external_unit_id"] not in unit_external_ids:
            problems.append(f"deal {d['external_id']}: external_unit_id {d['external_unit_id']} does not resolve")
    for a in attrs:
        if a["unit_external_id"] not in unit_external_ids:
            problems.append(f"unit_attributes {a['unit_external_id']}: does not resolve to a unit")

    if len({u["external_id"] for u in units}) != len(units):
        problems.append("duplicate unit external_id")
    if len({u["unit_code"] for u in units}) != len(units):
        problems.append("duplicate unit_code")
    if len({a["external_id"] for a in areas}) != len(areas):
        problems.append("duplicate area external_id")
    all_deal_ext_ids = [d["external_id"] for d in sold + reserved]
    if len(set(all_deal_ext_ids)) != len(all_deal_ext_ids):
        problems.append("duplicate deal external_id")

    sold_unit_ids = {d["external_unit_id"] for d in sold}
    reserved_unit_ids = {d["external_unit_id"] for d in reserved}
    overlap = sold_unit_ids & reserved_unit_ids
    if overlap:
        problems.append(f"units with both a sold and a reserved deal: {sorted(overlap)}")

    by_status: dict[str, list[str]] = {}
    for u in units:
        by_status.setdefault(u["unit_status"], []).append(u["external_id"])
    for uid in by_status.get("sold", []):
        if uid not in sold_unit_ids:
            problems.append(f"unit {uid}: status=sold but no sold deal")
    for uid in by_status.get("reserved", []):
        if uid not in reserved_unit_ids:
            problems.append(f"unit {uid}: status=reserved but no reserved deal")
    for uid in by_status.get("available", []):
        if uid in sold_unit_ids or uid in reserved_unit_ids:
            problems.append(f"unit {uid}: status=available but carries a deal")

    # Numeric-range re-checks on the enrichment source, matching this table's
    # own DB CHECK constraints (0043) so a reject here means the DB would also
    # reject it, never a surprise at insert time.
    for a in attrs:
        uid = a["unit_external_id"]
        floor = _to_int(a["floor"]) if a.get("floor") else None
        if floor is not None and not (1 <= floor <= 60):
            problems.append(f"unit {uid}: floor {floor} out of [1,60]")
        gross = _to_float(a["gross_area_sqm"]) if a.get("gross_area_sqm") else None
        net = _to_float(a["net_area_sqm"]) if a.get("net_area_sqm") else None
        if gross is not None and gross <= 0:
            problems.append(f"unit {uid}: gross_area_sqm <= 0")
        if net is not None and net <= 0:
            problems.append(f"unit {uid}: net_area_sqm <= 0")
        if gross is not None and net is not None and net > gross:
            problems.append(f"unit {uid}: net_area_sqm > gross_area_sqm")
        std_price = _to_float(a["standard_price_vnd"]) if a.get("standard_price_vnd") else None
        loan_price = _to_float(a["loan_price_vnd"]) if a.get("loan_price_vnd") else None
        if std_price is not None and std_price <= 0:
            problems.append(f"unit {uid}: standard_price_vnd <= 0")
        if loan_price is not None and loan_price <= 0:
            problems.append(f"unit {uid}: loan_price_vnd <= 0")
        eff = _to_float(a["area_efficiency_ratio"]) if a.get("area_efficiency_ratio") else None
        if eff is not None and not (0 <= eff <= 1):
            problems.append(f"unit {uid}: area_efficiency_ratio out of [0,1]")
        premium = _to_float(a["loan_premium_pct"]) if a.get("loan_premium_pct") else None
        if premium is not None and premium < 0:
            problems.append(f"unit {uid}: loan_premium_pct < 0")
        band = a.get("floor_band")
        if band and band not in ("low", "mid", "high"):
            problems.append(f"unit {uid}: floor_band {band!r} not in low/mid/high")

    return problems


def build_fixture(data: dict[str, tuple[list[dict[str, str]], str]]) -> dict[str, Any]:
    projects = data["crm_projects_import.csv"][0]
    areas = data["crm_areas_import.csv"][0]
    units = data["crm_units_import.csv"][0]
    sold = data["crm_deals_sold_import.csv"][0]
    reserved = data["crm_deals_reserved_import.csv"][0]
    attrs = {a["unit_external_id"]: a for a in data["lapura_unit_attributes_import.csv"][0]}

    project = projects[0]
    fixture_projects = [
        {
            "external_key": PROJECT_EXTERNAL_KEY,
            "operation": "upsert",
            "name": project["name"],
            "launch_date": project["launch_date"],
        }
    ]

    fixture_areas = [
        {
            "external_key": _area_key(a["external_id"]),
            "operation": "upsert",
            "project_external_key": PROJECT_EXTERNAL_KEY,
            "area_name": a["area_name"],
            "unit_type": a["unit_type"],
            "bedrooms": int(a["bedrooms"]),
            "area_sqm": float(a["area_sqm"]),
            "total_units": int(a["total_units"]),
        }
        for a in areas
    ]

    area_ext_by_id = {a["id"]: a["external_id"] for a in areas}

    fixture_units = []
    for u in units:
        entry: dict[str, Any] = {
            "external_key": _unit_key(u["external_id"]),
            "operation": "upsert",
            "area_external_key": _area_key(area_ext_by_id[u["area_id"]]),
            "unit_code": u["unit_code"],
            "unit_status": u["unit_status"],
        }
        attr = attrs.get(u["external_id"])
        if attr and attr.get("standard_price_vnd"):
            entry["listing_price"] = float(attr["standard_price_vnd"])
        fixture_units.append(entry)

    fixture_deals = []
    for d in sold:
        fixture_deals.append(
            {
                "external_key": _deal_key(d["external_id"]),
                "operation": "upsert",
                "unit_external_key": _unit_key(d["external_unit_id"]),
                "deal_status": "sold",
                "reserved_at": d.get("reserved_at") or None,
                "sold_at": d.get("sold_at") or None,
            }
        )
    for d in reserved:
        fixture_deals.append(
            {
                "external_key": _deal_key(d["external_id"]),
                "operation": "upsert",
                "unit_external_key": _unit_key(d["external_unit_id"]),
                "deal_status": "reserved",
                "reserved_at": d.get("reserved_at") or None,
            }
        )

    fixture_enrichment = []
    for u in units:
        attr = attrs.get(u["external_id"])
        if attr is None:
            continue
        is_synthetic = (
            attr.get("physical_features_origin", "").startswith("synthetic")
            or attr.get("agency_name_origin", "") == "synthetic"
            or attr.get("data_profile", "") != "production"
        )
        fixture_enrichment.append(
            {
                "unit_external_key": _unit_key(u["external_id"]),
                "source_row_key": attr["unit_external_id"],
                "subdivision": attr.get("subdivision") or None,
                "subdivision_raw": attr.get("subdivision_raw") or None,
                "tower": attr.get("tower") or None,
                "floor": _to_int(attr["floor"]) if attr.get("floor") else None,
                "unit_number": attr.get("unit_number") or None,
                "bedrooms": _to_int(attr["bedrooms"]) if attr.get("bedrooms") else None,
                "bathrooms": _to_int(attr["bathrooms"]) if attr.get("bathrooms") else None,
                "gross_area_sqm": _to_float(attr["gross_area_sqm"]) if attr.get("gross_area_sqm") else None,
                "net_area_sqm": _to_float(attr["net_area_sqm"]) if attr.get("net_area_sqm") else None,
                "standard_price_vnd": _to_float(attr["standard_price_vnd"])
                if attr.get("standard_price_vnd")
                else None,
                "loan_price_vnd": _to_float(attr["loan_price_vnd"]) if attr.get("loan_price_vnd") else None,
                "stacking_price_million_vnd": _to_float(attr["stacking_price_million_vnd"])
                if attr.get("stacking_price_million_vnd")
                else None,
                "agency_name": attr.get("agency_name") or None,
                "price_per_sqm_gross_vnd": _to_float(attr["price_per_sqm_gross_vnd"])
                if attr.get("price_per_sqm_gross_vnd")
                else None,
                "price_per_sqm_net_vnd": _to_float(attr["price_per_sqm_net_vnd"])
                if attr.get("price_per_sqm_net_vnd")
                else None,
                "area_efficiency_ratio": _to_float(attr["area_efficiency_ratio"])
                if attr.get("area_efficiency_ratio")
                else None,
                "loan_premium_pct": _to_float(attr["loan_premium_pct"]) if attr.get("loan_premium_pct") else None,
                "floor_band": attr.get("floor_band") or None,
                "direction": attr.get("direction") or None,
                "balcony_direction": attr.get("balcony_direction") or None,
                "view": attr.get("view") or None,
                "corner_unit_proxy": _to_bool(attr["corner_unit_proxy"]) if attr.get("corner_unit_proxy") else None,
                "physical_features_origin": attr.get("physical_features_origin") or None,
                "agency_name_origin": attr.get("agency_name_origin") or None,
                "data_profile": attr.get("data_profile") or None,
                "is_synthetic": is_synthetic,
                "source_system": SOURCE_SYSTEM,
                "source_file": "lapura_unit_attributes_import.csv",
            }
        )

    return {
        "_meta": {
            "generator": "scripts/derive_lapura_seed_fixture.py",
            "project_external_key": PROJECT_EXTERNAL_KEY,
        },
        "projects": fixture_projects,
        "areas": fixture_areas,
        "units": fixture_units,
        "deals": fixture_deals,
        "unit_enrichment": fixture_enrichment,
    }


def build_manifest(
    data: dict[str, tuple[list[dict[str, str]], str]], fixture: dict[str, Any], *, batch_id: str
) -> dict[str, Any]:
    entities = []
    for p in fixture["projects"]:
        entities.append({"kind": "project", "source_row_key": data["crm_projects_import.csv"][0][0]["external_id"], "fixture_external_key": p["external_key"]})
    for a, source in zip(fixture["areas"], data["crm_areas_import.csv"][0], strict=True):
        entities.append({"kind": "area", "source_row_key": source["external_id"], "fixture_external_key": a["external_key"]})
    for u, source in zip(fixture["units"], data["crm_units_import.csv"][0], strict=True):
        entities.append({"kind": "unit", "source_row_key": source["external_id"], "fixture_external_key": u["external_key"]})
    deal_sources = data["crm_deals_sold_import.csv"][0] + data["crm_deals_reserved_import.csv"][0]
    for d, source in zip(fixture["deals"], deal_sources, strict=True):
        entities.append({"kind": "deal", "source_row_key": source["external_id"], "fixture_external_key": d["external_key"]})

    return {
        "batch_id": batch_id,
        "created_at": datetime.now(UTC).isoformat(),
        "pass": 1,
        "source_files": [{"name": name, "sha256": sha} for name, (_, sha) in data.items()],
        "fixture_path": str(OUT_FIXTURE.relative_to(REPO_ROOT)),
        "counts": {k: len(fixture[k]) for k in ("projects", "areas", "units", "deals", "unit_enrichment")},
        "entities": entities,
        "real_ids": None,
    }


def _write_fixture_or_refuse(fixture: dict[str, Any]) -> str:
    new_content = json.dumps(fixture, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if OUT_FIXTURE.exists():
        existing = OUT_FIXTURE.read_text(encoding="utf-8")
        existing_no_meta = json.dumps({k: v for k, v in json.loads(existing).items() if k != "_meta"}, sort_keys=True)
        new_no_meta = json.dumps({k: v for k, v in fixture.items() if k != "_meta"}, sort_keys=True)
        if existing_no_meta == new_no_meta:
            return "unchanged"
        raise FixtureBuildError(
            f"{OUT_FIXTURE.relative_to(REPO_ROOT)} already exists with DIFFERENT content. "
            "This fixture is immutable once written — remove or rename it explicitly before "
            "re-deriving, so the change is a reviewable diff, not a silent overwrite."
        )
    OUT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FIXTURE.write_text(new_content, encoding="utf-8")
    return "written"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate and print counts; write nothing.")
    mode.add_argument(
        "--write-fixture", action="store_true", help="Write the fixture + Pass-1 manifest; zero DB/API writes."
    )
    parser.add_argument("--batch-id", default=None, help="Required with --write-fixture.")
    args = parser.parse_args()

    if args.write_fixture and not args.batch_id:
        parser.error("--write-fixture requires --batch-id")

    try:
        data, file_order = load_source()
        problems = validate(data)
        fixture = build_fixture(data)
    except FixtureBuildError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    print("=== Source files (SHA-256) ===")
    for name in file_order:
        _, sha = data[name]
        print(f"  {name}: {sha}")

    print("\n=== Validation ===")
    if problems:
        print(f"  {len(problems)} problem(s) found:")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  clean — 0 problems")

    print("\n=== Fixture counts ===")
    for key in ("projects", "areas", "units", "deals", "unit_enrichment"):
        print(f"  {key}: {len(fixture[key])}")
    sold_n = sum(1 for d in fixture["deals"] if d["deal_status"] == "sold")
    reserved_n = sum(1 for d in fixture["deals"] if d["deal_status"] == "reserved")
    print(f"  deals breakdown: {sold_n} sold, {reserved_n} reserved")

    if problems:
        print("\nREFUSED: validation problems found, see above. No files written.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n--dry-run: no files written.")
        return 0

    batch_id = args.batch_id
    status = _write_fixture_or_refuse(fixture)
    print(f"\nFixture {OUT_FIXTURE.relative_to(REPO_ROOT)}: {status}")

    manifest = build_manifest(data, fixture, batch_id=batch_id)
    OUT_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_MANIFEST_DIR / f"lapura_seed_manifest_{batch_id}.json"
    if manifest_path.exists():
        print(f"REFUSED: manifest {manifest_path.relative_to(REPO_ROOT)} already exists for this batch-id.", file=sys.stderr)
        return 1
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Manifest {manifest_path.relative_to(REPO_ROOT)}: written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
