"""Load the `unit_enrichment` rows from a written La Pura fixture into
`unit_enrichment_attributes`, once the real seed (MiniCRM API -> outbox ->
AbsorpIQ sync) has completed and the manifest's Pass 2 has the real AbsorpIQ
`units.id` for every unit.

    python -m scripts.load_lapura_unit_enrichment --dry-run --manifest <path>
    python -m scripts.load_lapura_unit_enrichment --apply --confirm-write --manifest <path>

Never runs `run_ranking()`, never reads/writes `ranking_scores` or
`ranking_configs` other than the one guard check
(`src.ranking.enrichment_guard.ensure_enrichment_keys_not_in_active_config`)
this module calls before every real insert.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scripts.lapura_manifest import ManifestError, is_pass_2_complete, load_manifest, real_id_by_fixture_key
from src.models.tables import unit_enrichment_attributes
from src.ranking.enrichment_guard import ensure_enrichment_keys_not_in_active_config

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "scripts" / "fixtures" / "lapura_normalized_seed_v1.json"


class LoadError(RuntimeError):
    pass


def load_fixture(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LoadError(f"Fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def plan_inserts(fixture: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure function: fixture + Pass-2 manifest -> the exact row values that
    would be inserted. No DB access — this is what `--dry-run` prints.
    """
    if not is_pass_2_complete(manifest):
        raise LoadError(
            "Manifest is not Pass-2 complete (missing real ids for at least one entity) — "
            "the real seed run must finish first. This is exactly the 'partial prior import' "
            "state --mode resume exists to detect."
        )
    unit_real_ids = real_id_by_fixture_key(manifest, "unit")
    batch_id = manifest["batch_id"]
    sha_by_file = {f["name"]: f["sha256"] for f in manifest["source_files"]}

    rows = []
    for entry in fixture["unit_enrichment"]:
        real_unit_id = unit_real_ids.get(entry["unit_external_key"])
        if real_unit_id is None:
            raise LoadError(f"No real AbsorpIQ unit id for {entry['unit_external_key']!r} in the manifest.")
        row = {k: v for k, v in entry.items() if k != "unit_external_key"}
        row["unit_id"] = real_unit_id
        row["source_file_sha256"] = sha_by_file.get(row["source_file"], "")
        row["import_batch_id"] = batch_id
        rows.append(row)
    return rows


async def apply_inserts(session_factory: async_sessionmaker[AsyncSession], rows: list[dict[str, Any]]) -> int:
    async with session_factory() as session:
        await ensure_enrichment_keys_not_in_active_config(session)
        now = datetime.now(UTC)
        for row in rows:
            await session.execute(
                sa.insert(unit_enrichment_attributes).values(
                    id=uuid.uuid4(),
                    unit_id=uuid.UUID(row["unit_id"]) if isinstance(row["unit_id"], str) else row["unit_id"],
                    imported_at=now,
                    created_at=now,
                    updated_at=now,
                    **{k: v for k, v in row.items() if k != "unit_id"},
                )
            )
        await session.commit()
    return len(rows)


async def _main_async(args: argparse.Namespace) -> int:
    fixture = load_fixture(Path(args.fixture))
    try:
        manifest = load_manifest(Path(args.manifest))
    except ManifestError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    try:
        rows = plan_inserts(fixture, manifest)
    except LoadError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    print(f"=== {len(rows)} unit_enrichment_attributes row(s) planned ===")
    for row in rows[:5]:
        print(f"  unit_id={row['unit_id']} subdivision={row.get('subdivision')} is_synthetic={row['is_synthetic']}")
    if len(rows) > 5:
        print(f"  ... and {len(rows) - 5} more")

    if args.dry_run:
        print("\n--dry-run: zero DB writes.")
        return 0

    if not args.confirm_write:
        print("REFUSED: --apply requires --confirm-write.", file=sys.stderr)
        return 1

    from src.config import get_settings
    from src.db import get_engine

    settings = get_settings()
    if settings.app_env != "development":
        print(f"REFUSED: APP_ENV={settings.app_env!r}, only 'development' is permitted.", file=sys.stderr)
        return 1

    engine = get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        n = await apply_inserts(factory, rows)
    except Exception as exc:  # noqa: BLE001 - report and stop, never a silent partial load
        print(f"REFUSED / FAILED mid-load: {exc}", file=sys.stderr)
        return 1
    print(f"\nInserted {n} unit_enrichment_attributes rows.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--manifest", required=True, help="Path to the Pass-2 manifest for this batch.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
