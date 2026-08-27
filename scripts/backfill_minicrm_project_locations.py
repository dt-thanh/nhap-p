"""Backfill Mini CRM project locations from an Address CSV.

The command is dry-run by default.  It only writes when ``--apply`` is given,
and it refuses non-development/local database targets.

    python -m scripts.backfill_minicrm_project_locations --csv PATH
    python -m scripts.backfill_minicrm_project_locations --csv PATH --apply
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import make_url

_SPACE_RE = re.compile(r"\s+")
_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "minicrm_db"})
_ALLOWED_DATABASES = frozenset({"minicrm", "minicrm_dev"})


class BackfillError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedAddress:
    row_number: int
    project_name: str
    location: str


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    updates: tuple[tuple[Any, str], ...]
    report: dict[str, Any]


def normalize_text(value: str) -> str:
    """Collapse whitespace and remove only a final punctuation dot."""
    return _SPACE_RE.sub(" ", value.strip()).rstrip(".").strip()


def normalize_project_name(value: str | None) -> str:
    if value is None:
        return ""
    return _strip_project_prefix(normalize_text(value)).casefold()


def _strip_project_prefix(value: str) -> str:
    folded = value.casefold()
    prefix = "dự án"
    if folded == prefix:
        return ""
    if folded.startswith(prefix) and len(value) > len(prefix) and value[len(prefix)].isspace():
        return value[len(prefix):].strip()
    return value


def parse_address(value: str | None, row_number: int = 0) -> ParsedAddress | None:
    if not value or "," not in value:
        return None
    raw_name, raw_location = value.split(",", 1)
    project_name = _strip_project_prefix(normalize_text(raw_name))
    location = normalize_text(raw_location)
    if not project_name or not location:
        return None
    return ParsedAddress(row_number, project_name, location)


def read_addresses(path: Path) -> tuple[list[ParsedAddress], list[int]]:
    parsed: list[ParsedAddress] = []
    malformed: list[int] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "Address" not in reader.fieldnames:
            raise BackfillError("CSV must contain an Address column")
        for row_number, row in enumerate(reader, start=2):
            item = parse_address(row.get("Address"), row_number)
            if item is None:
                malformed.append(row_number)
            else:
                parsed.append(item)
    return parsed, malformed


def _select_location(items: list[ParsedAddress]) -> tuple[str | None, bool, list[str]]:
    counts = Counter(item.location for item in items)
    if not counts:
        return None, False, []
    ordered = counts.most_common()
    top_count = ordered[0][1]
    tied = [location for location, count in ordered if count == top_count]
    # Equal-frequency alternatives are ambiguous: there is no evidence-based
    # winner, so leave the value unset and retain every alternative in the report.
    if len(tied) > 1:
        return None, True, sorted(counts)
    return tied[0], len(counts) > 1, sorted(counts)


def build_plan(
    parsed: Iterable[ParsedAddress],
    existing_projects: Iterable[dict[str, Any]],
    *,
    overwrite: bool = False,
) -> BackfillPlan:
    grouped: dict[str, list[ParsedAddress]] = defaultdict(list)
    parsed_list = list(parsed)
    existing_list = list(existing_projects)
    for item in parsed_list:
        grouped[normalize_project_name(item.project_name)].append(item)

    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for project in existing_list:
        by_name[normalize_project_name(project["name"])].append(project)

    updates: list[tuple[Any, str]] = []
    conflicts: list[dict[str, Any]] = []
    unmatched: list[str] = []
    ambiguous: list[str] = []
    existing_skips: list[str] = []
    longer_existing_skips: list[str] = []
    unchanged: list[str] = []

    for key, items in sorted(grouped.items()):
        matches = by_name.get(key, [])
        if not matches:
            unmatched.append(items[0].project_name)
            continue
        if len(matches) != 1:
            ambiguous.append(items[0].project_name)
            continue

        location, has_conflict, alternatives = _select_location(items)
        if has_conflict:
            conflicts.append(
                {
                    "project": matches[0]["name"],
                    "alternatives": alternatives,
                    "selected": location,
                    "rows": len(items),
                }
            )
        if location is None:
            continue

        current = matches[0].get("location")
        if current and not overwrite:
            if normalize_text(current) == location:
                unchanged.append(matches[0]["name"])
            else:
                existing_skips.append(matches[0]["name"])
            continue
        if current == location:
            unchanged.append(matches[0]["name"])
            continue
        if current and overwrite and len(location) < len(normalize_text(current)):
            longer_existing_skips.append(matches[0]["name"])
            continue
        updates.append((matches[0]["id"], location))

    matched_projects = len(grouped) - len(unmatched) - len(ambiguous)
    report = {
        "csv_rows": len(parsed_list),
        "dataset_projects": len(grouped),
        "unique_projects_after_normalization": len(grouped),
        "projects_in_crm_projects": len(existing_list),
        "matched_projects": matched_projects,
        "unmatched_projects": len(unmatched),
        "ambiguous_projects": len(ambiguous),
        "location_conflict_projects": len(conflicts),
        "updates": len(updates),
        "projects_updated": len(updates),
        "unchanged": len(unchanged),
        "skipped_existing_location": len(existing_skips),
        "skipped_longer_existing_location": len(longer_existing_skips),
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "conflicts": conflicts,
        "overwrite": overwrite,
    }
    return BackfillPlan(tuple(updates), report)


def _guard_runtime() -> tuple[str, str]:
    try:
        from app.config import get_settings
    except ModuleNotFoundError:
        from minicrm.app.config import get_settings

    settings = get_settings()
    if settings.app_env != "development":
        raise BackfillError(f"refusing database write: APP_ENV must be development, got {settings.app_env!r}")
    url = make_url(settings.database_dsn)
    host = url.host or ""
    database = url.database or ""
    if host not in _ALLOWED_HOSTS or (database not in _ALLOWED_DATABASES and not database.endswith("_test")):
        raise BackfillError("refusing database write: target is not an approved local Mini CRM database")
    if url.port not in (None, 5432, 5434):
        raise BackfillError("refusing database write: target port is not local development PostgreSQL")
    return host, database


async def _load_projects() -> list[dict[str, Any]]:
    try:
        from app.db import get_session_factory
        from app.models import crm_projects
    except ModuleNotFoundError:
        from minicrm.app.db import get_session_factory
        from minicrm.app.models import crm_projects

    async with get_session_factory()() as session:
        rows = (await session.execute(sa.select(crm_projects))).mappings().all()
    return [dict(row) for row in rows]


async def _apply_updates(updates: Iterable[tuple[Any, str]]) -> int:
    try:
        from app.db import get_session_factory
        from app.models import crm_projects
    except ModuleNotFoundError:
        from minicrm.app.db import get_session_factory
        from minicrm.app.models import crm_projects

    updates = tuple(updates)
    async with get_session_factory()() as session:
        async with session.begin():
            for project_id, location in updates:
                await session.execute(
                    sa.update(crm_projects)
                    .where(crm_projects.c.id == project_id)
                    .values(location=location)
                )
    return len(updates)


async def run(args: argparse.Namespace) -> int:
    if args.overwrite and not args.apply:
        raise BackfillError("--overwrite requires --apply")
    csv_path = Path(args.csv)
    if not csv_path.is_file():
        raise BackfillError(f"CSV not found: {csv_path}")
    host, database = _guard_runtime()
    parsed, malformed = read_addresses(csv_path)
    existing_projects = await _load_projects()
    plan = build_plan(parsed, existing_projects, overwrite=args.overwrite)
    report = {
        "database": database,
        "host": host,
        "csv": str(csv_path),
        "total_dataset_rows": len(parsed) + len(malformed),
        "malformed_rows": malformed,
        "skipped_rows": len(malformed),
        "skipped_row_reasons": {"unparseable_address": len(malformed)},
        **plan.report,
        "applied": False,
    }
    if args.apply:
        report["projects_updated"] = await _apply_updates(plan.updates)
        report["applied"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=None, help="Path to the source CSV (or VINHOMES_CSV_PATH)")
    parser.add_argument("--apply", action="store_true", help="Write the planned locations")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing existing non-empty locations")
    args = parser.parse_args()
    args.csv = args.csv or os.environ.get("VINHOMES_CSV_PATH")
    if not args.csv:
        parser.error("--csv or VINHOMES_CSV_PATH is required")
    try:
        return asyncio.run(run(args))
    except BackfillError as exc:
        print(f"backfill refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
