"""Derive committed, repo-sized fixture files from the raw `crm_real_data.json`
export — a ~1 MB file that is NOT part of this repository (dev-machine-local).

    python -m scripts.derive_ingestion_seed_from_crm_real_data --source /path/to/crm_real_data.json

Writes exactly two files, both small and committed:

    scripts/fixtures/ingestion_seed.json           (consumed by scripts/seed_backend_from_json.py
                                                      and alembic/versions/0019_seed_ai_crm_fixture.py)
    docs/ai_fixtures/simulated_ranking_fixture.json (NOT consumed by any migration/seed —
                                                      a hand-off artifact for the AI team only)

Why this script exists instead of embedding `crm_real_data.json` directly in a
migration: no migration in this repo reads an external file (checked — zero
precedent), and the raw export is 1 MB and lives outside the repo. This script
is the ONE place that ever reads the raw export; everything downstream (the
Alembic revision, the CLI seed script, their tests) depends only on the small
committed JSON this script produces, so they work identically for every
teammate regardless of whether they have the raw export on their machine.

Split rationale (`scripts/fixtures/ingestion_seed.json` vs the AI fixtures file):
`crm_real_data.json.scoring_meta.method == "simulated_placeholder"` — the
`score`/`score_raw`/`band`/`rank`/`contributions`/`scored` fields on each unit
in `ranking_by_area` are explicitly NOT model output. They must never reach
`ranking_scores`/`ranking_configs`/`ranking_runs`/`feature_snapshots` (Phase 6
tables — `tests/test_ranking_boundary.py` restricts writes to those four
tables to `src/ranking/service.py` only) and must never be mistaken for
evidence of a running ranking model. They are therefore kept in a separate
file, outside the seed path entirely, labeled `provenance: simulated_fixture`.

What is intentionally dropped, and why (see `_meta.excluded` in the output):
- `zones` (18 records): Backend's `areas` table has no zone/tower parent
  column (project_id only) — inventing one would be a schema change this
  script has no business making. The source data already folds zone context
  into `area_name` (e.g. "Sapphire 1 - Studio"), so nothing is lost, just not
  separately queryable as its own row.
- `data_source` (`real`/`estimated`, on zones/areas): no supported column on
  `projects`/`areas` for this. Preserved here, in `_meta.data_source_breakdown`
  and per-area in `dash_areas[].data_source` (informational only — the seed
  script does not read that key), not invented as a new DB column.
- Deal-level data: none exists in the source at all (no top-level "deals"
  key). `units[].status` (`available`/`reserved`) already carries the only
  transactional signal present; no `deals` rows are fabricated from it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
# `scripts/fixtures/`, KHÔNG `docs/`: `.dockerignore` loại toàn bộ `docs/` khỏi
# build context của image backend, và `docs/` không nằm trong `volumes:` sống
# của nó — migration 0019 (chạy TRONG container) sẽ không thấy file nếu nó nằm
# ở `docs/`. `scripts/` thì được COPY vào image lúc build. Xem
# `scripts/_seed_ai_crm_fixture_core.py::SEED_FILE` để biết chi tiết đã xác minh.
OUT_INGESTION_SEED = REPO_ROOT / "scripts" / "fixtures" / "ingestion_seed.json"
# Ngược lại, KHÔNG cần Docker thấy — không migration/script containerized nào
# đọc file này, nó chỉ là artefact bàn giao cho đội AI (đọc trên host hoặc qua
# tool riêng của họ). Giữ ở `docs/` cho đúng vị trí "tài liệu/bàn giao".
OUT_RANKING_FIXTURE = REPO_ROOT / "docs" / "ai_fixtures" / "simulated_ranking_fixture.json"

REQUIRED_TOP_LEVEL_KEYS = {
    "scoring_meta",
    "projects",
    "zones",
    "areas",
    "dash_areas",
    "dash_trend_by_area",
    "ranking_by_area",
    "files",
    "sample_errors",
}


class DerivationError(RuntimeError):
    pass


def _load_source(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise DerivationError(
            f"Không thấy {path}. Script này CHỈ đọc file này một lần để dựng lại hai fixture "
            "nhỏ đã commit — không tự bịa dữ liệu. Truyền đúng đường dẫn qua --source."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DerivationError(f"{path} không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(data, dict):
        raise DerivationError(f"{path}: gốc phải là object, nhận {type(data).__name__}")
    missing = REQUIRED_TOP_LEVEL_KEYS - data.keys()
    if missing:
        raise DerivationError(f"{path} thiếu khoá bắt buộc: {sorted(missing)}")
    return data


def _validate(data: dict[str, Any]) -> None:
    """Kiểm cục bộ, KHÔNG một dòng nào được ghi trước khi qua hết đây."""
    errors: list[str] = []

    project_ids = {p["id"] for p in data["projects"]}
    area_by_id = {a["id"]: a for a in data["areas"]}
    dash_area_by_id = {a["id"]: a for a in data["dash_areas"]}
    file_ids = {f["id"] for f in data["files"]}

    if len(project_ids) != len(data["projects"]):
        errors.append("projects: id trùng lặp")
    if len(area_by_id) != len(data["areas"]):
        errors.append("areas: id trùng lặp")

    required_area_fields = {"unit_type", "bedrooms", "area_sqm", "total_units", "project_id"}
    for a in data["areas"]:
        missing = required_area_fields - a.keys()
        if missing or any(a.get(k) is None for k in required_area_fields):
            errors.append(f"areas[id={a.get('id')}]: thiếu/NULL trường bắt buộc {sorted(missing) or list(required_area_fields)}")
        if a.get("project_id") not in project_ids:
            errors.append(f"areas[id={a.get('id')}]: project_id '{a.get('project_id')}' không tồn tại trong 'projects'")
        tu, rem = a.get("total_units"), a.get("units_remaining")
        if tu is not None and rem is not None and not (tu >= rem >= 0):
            errors.append(f"areas[id={a.get('id')}]: vi phạm total_units >= units_remaining >= 0 ({tu} >= {rem})")

    missing_join = set(dash_area_by_id) - set(area_by_id)
    if missing_join:
        errors.append(f"dash_areas tham chiếu id không có trong 'areas': {sorted(missing_join)}")

    for a in data["dash_areas"]:
        sold, remaining, total = a.get("sold"), a.get("remaining"), a.get("total_units")
        if sold is not None and remaining is not None and total is not None and sold + remaining != total:
            errors.append(f"dash_areas[id={a.get('id')}]: sold({sold}) + remaining({remaining}) != total_units({total})")

    bad_trend_refs = set(data["dash_trend_by_area"]) - set(area_by_id)
    if bad_trend_refs:
        errors.append(f"dash_trend_by_area tham chiếu area id không tồn tại: {sorted(bad_trend_refs)}")

    bad_ranking_refs = set(data["ranking_by_area"]) - set(area_by_id)
    if bad_ranking_refs:
        errors.append(f"ranking_by_area tham chiếu area id không tồn tại: {sorted(bad_ranking_refs)}")

    unit_ids_seen: set[str] = set()
    for area_id, units in data["ranking_by_area"].items():
        codes_in_area = Counter(u["unit_code"] for u in units)
        dup_codes = [c for c, n in codes_in_area.items() if n > 1]
        if dup_codes:
            errors.append(f"ranking_by_area[{area_id}]: unit_code trùng trong CÙNG area: {dup_codes}")
        for u in units:
            uid = u.get("unit_id")
            if not uid:
                errors.append(f"ranking_by_area[{area_id}]: một bản ghi thiếu 'unit_id'")
                continue
            if uid in unit_ids_seen:
                errors.append(f"ranking_by_area: unit_id trùng lặp TOÀN CỤC '{uid}'")
            unit_ids_seen.add(uid)
            if u.get("status") not in ("available", "reserved", "sold", "blocked"):
                errors.append(f"unit '{uid}': status '{u.get('status')}' không khớp UNIT_STATUSES của schema Backend")

    for e in data["sample_errors"]:
        if e.get("file_id") not in file_ids:
            errors.append(f"sample_errors[id={e.get('id')}]: file_id '{e.get('file_id')}' không có trong 'files'")

    if errors:
        raise DerivationError("Kiểm cục bộ thất bại (" + str(len(errors)) + " lỗi):\n  " + "\n  ".join(errors))


def _build_ingestion_seed(data: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    dash_by_id = {a["id"]: a for a in data["dash_areas"]}
    dash_areas_out = []
    for a in data["areas"]:
        dash = dash_by_id[a["id"]]
        dash_areas_out.append(
            {
                "id": a["id"],
                "project_id": a["project_id"],
                "name": a["area_name"],
                "unit_type": a["unit_type"],
                "bedrooms": a["bedrooms"],
                "area_sqm": a["area_sqm"],
                "total_units": a["total_units"],
                "sold": dash.get("sold"),
                "remaining": dash.get("remaining"),
                # Thông tin, KHÔNG được scripts/seed_backend_from_json.py đọc —
                # giữ lại để dò lại nguồn khi cần, không phải để ghi xuống DB.
                "zone_id": a.get("zone_id"),
                "data_source": a.get("data_source"),
            }
        )

    units_out = []
    for area_id, units in data["ranking_by_area"].items():
        for u in units:
            units_out.append(
                {
                    "id": u["unit_id"],
                    "area_id": area_id,
                    "unit_code": u["unit_code"],
                    "unit_type": u["unit_type"],
                    "status": u["status"],
                }
            )

    data_source_counts = Counter(a.get("data_source") for a in data["areas"])

    return {
        "_meta": {
            "derived_from": "crm_real_data.json (external, not committed — see scripts/derive_ingestion_seed_from_crm_real_data.py)",
            "derived_at": datetime.now(UTC).isoformat(),
            "counts": {
                "projects": len(data["projects"]),
                "areas": len(dash_areas_out),
                "units": len(units_out),
                "trend_points": sum(len(v) for v in data["dash_trend_by_area"].values()),
                "files": len(data["files"]),
                "sample_errors": len(data["sample_errors"]),
            },
            "data_source_breakdown_areas": dict(data_source_counts),
            "excluded": {
                "zones": f"{len(data['zones'])} records — no zone/tower table in Backend schema; "
                "zone context already folded into area_name by the source (see docstring of the derive script)",
                "deals": "0 records in source — no deal-level data exists; not fabricated",
                "ranking_fields (score/score_raw/band/rank/contributions/scored)": "written to "
                "docs/ai_fixtures/simulated_ranking_fixture.json instead — NEVER written to authoritative "
                "ranking tables (scoring_meta.method == 'simulated_placeholder')",
            },
        },
        "projects": [
            {"id": p["id"], "name": p["name"], "location": p.get("location"), "status": p.get("status")}
            for p in data["projects"]
        ],
        "dash_areas": dash_areas_out,
        "trend_by_area": data["dash_trend_by_area"],
        "units": units_out,
        "files": data["files"],
        "sample_errors": data["sample_errors"],
    }


def _build_ranking_fixture(data: dict[str, Any]) -> dict[str, Any]:
    units_out = []
    for area_id, units in data["ranking_by_area"].items():
        for u in units:
            units_out.append(
                {
                    "external_unit_id": u["unit_id"],
                    "area_id": area_id,
                    "unit_code": u["unit_code"],
                    "unit_type": u["unit_type"],
                    "area_sqm": u.get("area_sqm"),
                    "status": u["status"],
                    "scored": u["scored"],
                    "score": u["score"],
                    "score_raw": u["score_raw"],
                    "band": u["band"],
                    "rank": u["rank"],
                    "contributions": u["contributions"],
                }
            )
    return {
        "provenance": "simulated_fixture",
        "warning": (
            "score/score_raw/band/rank/contributions/scored are SIMULATED PLACEHOLDERS, not output of a "
            "running ranking model. Do NOT use for model evaluation, do NOT write into ranking_scores/"
            "ranking_configs/ranking_runs/feature_snapshots. Join on external_unit_id against "
            "units.external_unit_id (source_instance_id='ai-dev-fixture') for real unit identity."
        ),
        "scoring_meta": data["scoring_meta"],
        "generated_at": datetime.now(UTC).isoformat(),
        "unit_count": len(units_out),
        "units": units_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Đường dẫn tới crm_real_data.json (bên ngoài repo)")
    args = parser.parse_args()

    try:
        data = _load_source(Path(args.source))
        _validate(data)
    except DerivationError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1

    ingestion_seed = _build_ingestion_seed(data, source_path=Path(args.source))
    ranking_fixture = _build_ranking_fixture(data)

    OUT_INGESTION_SEED.parent.mkdir(parents=True, exist_ok=True)
    OUT_INGESTION_SEED.write_text(json.dumps(ingestion_seed, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")

    OUT_RANKING_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    OUT_RANKING_FIXTURE.write_text(json.dumps(ranking_fixture, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")

    print("=== Mapping report ===")
    for k, v in ingestion_seed["_meta"]["counts"].items():
        print(f"  {k:15s} {v}")
    print(f"  data_source (areas)  {ingestion_seed['_meta']['data_source_breakdown_areas']}")
    print("\nExcluded (documented, not silently dropped):")
    for k, v in ingestion_seed["_meta"]["excluded"].items():
        print(f"  - {k}: {v}")
    print(f"\nWrote {OUT_INGESTION_SEED.relative_to(REPO_ROOT)}")
    print(f"Wrote {OUT_RANKING_FIXTURE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
