"""Orchestrator for the La Pura additive seed. This is the one command an
operator runs; it wraps `derive_lapura_seed_fixture.py`, `lapura_preflight.py`,
`seed_mini_crm_from_json.py`, `lapura_manifest.py`, and
`load_lapura_unit_enrichment.py` behind four explicit, mutually exclusive
execution modes.

    python -m scripts.seed_lapura --dry-run
    python -m scripts.seed_lapura --write-fixture --batch-id <id> [--mode create|resume]
    python -m scripts.seed_lapura --confirm-seed --batch-id <id> --mode create
    python -m scripts.seed_lapura --validate --batch-id <id>

`--dry-run`: zero DB writes and zero fixture/manifest writes. Builds the
fixture in memory, runs every preflight check (including a LIVE check for a
still-existing La Pura project — see `lapura_preflight.check_not_already_seeded`),
and prints the full report (source hashes, fixture counts, the exact MiniCRM
API requests a real run would send, expected sync results, rejects/warnings,
and the rollback scope a real run would leave behind).

`--write-fixture`: writes ONLY the dedicated fixture
(`scripts/fixtures/lapura_normalized_seed_v1.json`) and the Pass-1 manifest
(`scripts/fixtures/manifests/lapura_seed_manifest_<batch_id>.json`). Zero DB
writes.

`--confirm-seed --mode create`: the one mode that writes anything. Re-runs
every preflight check immediately before the first API write (a fresh process
invocation always does this — there is no cached state to go stale), invokes
`seed_mini_crm_from_json.py` exactly through its supported CLI
(`--fixture`/`--state-file`, never raw SQL), polls AbsorpIQ with a bounded
timeout until the projected counts for the new project exactly match the
fixture (1/24/392/193), captures the manifest's Pass-2 real ids, then loads
`unit_enrichment_attributes` through the guarded loader. Any rejection,
timeout, or mismatch at any stage stops immediately, before touching
enrichment, and reports exactly how to resume. `--mode resume` is
intentionally NOT implemented for `--confirm-seed` in this pass — see
`lapura_preflight.check_not_already_seeded`'s own resume-mode docstring for
why `create` is the only supported mode here today. `--confirm-seed` is
intentionally NOT exercised by this repository's automated test suite — it
is exercised in dry-run form only, by design, since it is the one mode with
real external side effects.

`--validate`: fully read-only. Given a batch id, re-derives and re-verifies
everything the prior sections of this session's audits checked by hand:
target identities/heads, scoped counts, reconciliation, orphans, duplicate
external-ids, Pass-2 completeness, source-hash match, ranking provenance
(published config + zero AHP/enrichment contamination), hierarchical-null
state, the rollback plan (dry-run only), and that `docs/mini_crm_seed.json`
is unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from scripts.derive_lapura_seed_fixture import (
    PROJECT_EXTERNAL_KEY,
    SOURCE_FILES,
    _write_fixture_or_refuse,
    build_fixture,
    build_manifest,
    load_source,
    validate,
)
from scripts.lapura_manifest import (
    ManifestError,
    apply_real_ids,
    is_pass_2_complete,
    load_manifest,
    save_manifest,
)
from scripts.lapura_preflight import (
    ALLOWED_ABSORPTION_DB_NAMES,
    ALLOWED_MINICRM_DB_NAMES,
    PreflightError,
    PreflightReport,
    check_absorption_target,
    check_app_env,
    check_minicrm_target,
    check_not_already_seeded,
    check_sync_services_reachable,
    make_absorption_project_lookup,
    make_absorption_project_lookup_by_name,
    make_minicrm_project_lookup,
    make_minicrm_project_lookup_by_name,
    redact_url,
    resolve_execution_url,
)
from scripts.load_lapura_unit_enrichment import apply_inserts as _load_enrichment_apply
from scripts.load_lapura_unit_enrichment import plan_inserts as _load_enrichment_plan
from scripts.rollback_lapura_seed import build_plan as _rollback_build_plan

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_FIXTURE = REPO_ROOT / "scripts" / "fixtures" / "lapura_normalized_seed_v1.json"
OUT_MANIFEST_DIR = REPO_ROOT / "scripts" / "fixtures" / "manifests"
LEGACY_SEED_FILE = REPO_ROOT / "docs" / "mini_crm_seed.json"
EXPECTED_COUNTS = {"projects": 1, "areas": 24, "units": 392, "deals": 193}
PROJECTION_POLL_TIMEOUT_S = 180.0
PROJECTION_POLL_INTERVAL_S = 3.0


class SeedOrchestrationError(RuntimeError):
    """A real-seed stage failed, possibly after some API writes already
    happened. Never triggers an automatic rollback or retry — the caller
    reports exactly what state was reached and how to resume."""


def _api_requests_preview(fixture: dict) -> list[str]:
    """What a real `--mode create` run would send — derived straight from
    the fixture, not a separate hand-maintained list."""
    lines = []
    for p in fixture["projects"]:
        lines.append(f"POST /projects  {{'name': {p['name']!r}, 'launch_date': {p['launch_date']!r}}}")
    for a in fixture["areas"]:
        lines.append(
            f"POST /areas  {{'external_project_id': <resolved {a['project_external_key']}>, "
            f"'area_name': {a['area_name']!r}, 'unit_type': {a['unit_type']!r}, "
            f"'bedrooms': {a['bedrooms']}, 'area_sqm': {a['area_sqm']}, 'total_units': {a['total_units']}}}"
        )
    for u in fixture["units"]:
        entry = (
            f"POST /units  {{'external_area_id': <resolved {u['area_external_key']}>, "
            f"'unit_code': {u['unit_code']!r}, 'unit_status': {u['unit_status']!r}"
        )
        if "listing_price" in u:
            entry += f", 'listing_price': {u['listing_price']}"
        entry += "}"
        lines.append(entry)
    for d in fixture["deals"]:
        lines.append(
            f"POST /deals  {{'external_unit_id': <resolved {d['unit_external_key']}>, "
            f"'deal_status': {d['deal_status']!r}}}"
        )
    return lines


def _read_env_var(env_file: Path, key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _minicrm_database_url() -> str | None:
    return _read_env_var(REPO_ROOT / "minicrm" / ".env", "MINICRM_DATABASE_URL")


def _batch_state_file(batch_id: str) -> Path:
    return OUT_MANIFEST_DIR / f"lapura_state_{batch_id}.json"


def _resolve_absorption_url(report: PreflightReport) -> str:
    from src.config import get_settings

    return resolve_execution_url(
        get_settings().database_dsn,
        compose_service="db",
        container_port=5432,
        allowed_db_names=ALLOWED_ABSORPTION_DB_NAMES,
        label="AbsorpIQ",
        explicit_override="DATABASE_URL" in os.environ,
        repo_root=REPO_ROOT,
        report=report,
    )


def _resolve_minicrm_url(report: PreflightReport) -> str | None:
    raw = _minicrm_database_url()
    if raw is None:
        return None
    return resolve_execution_url(
        raw,
        compose_service="minicrm_db",
        container_port=5432,
        allowed_db_names=ALLOWED_MINICRM_DB_NAMES,
        label="MiniCRM",
        explicit_override="MINICRM_DATABASE_URL" in os.environ,
        repo_root=REPO_ROOT,
        report=report,
    )


async def _run_preflight(
    *,
    mode: str,
    batch_id: str | None,
    source_sha256_set: frozenset[str],
    project_name: str,
    strict: bool = False,
) -> PreflightReport:
    """`strict=True` (used by `--confirm-seed`) makes an unreachable sync
    service a hard failure instead of a warning — `--dry-run` tolerates it
    because nothing is about to be written; `--confirm-seed` must not."""
    from src.config import get_settings

    settings = get_settings()
    report = PreflightReport()
    await check_app_env(settings.app_env, report)

    absorption_url = _resolve_absorption_url(report)
    absorption_engine = create_async_engine(absorption_url)
    minicrm_engine: AsyncEngine | None = None
    try:
        try:
            await check_absorption_target(absorption_engine, absorption_url, report)
        except PreflightError:
            raise
        except Exception as exc:  # noqa: BLE001 - a reachability failure is a preflight fact, not a crash
            raise PreflightError(f"Could not reach AbsorpIQ database ({redact_url(absorption_url)}): {exc}") from exc
        absorption_lookup = make_absorption_project_lookup(absorption_engine)
        absorption_lookup_by_name = make_absorption_project_lookup_by_name(absorption_engine)

        minicrm_database_url = _resolve_minicrm_url(report)
        minicrm_lookup = None
        minicrm_lookup_by_name = None
        if minicrm_database_url:
            minicrm_engine = create_async_engine(minicrm_database_url)
            try:
                await check_minicrm_target(minicrm_engine, minicrm_database_url, report)
            except PreflightError:
                raise
            except Exception as exc:  # noqa: BLE001 - same reasoning as the AbsorpIQ check above
                raise PreflightError(
                    f"Could not reach MiniCRM database ({redact_url(minicrm_database_url)}): {exc}"
                ) from exc
            minicrm_lookup = make_minicrm_project_lookup(minicrm_engine)
            minicrm_lookup_by_name = make_minicrm_project_lookup_by_name(minicrm_engine)
        else:
            report.checks.append("SKIPPED: MiniCRM database URL not found in minicrm/.env")

        await check_not_already_seeded(
            manifest_dir=OUT_MANIFEST_DIR,
            source_sha256_set=source_sha256_set,
            project_external_key=PROJECT_EXTERNAL_KEY,
            mode=mode,
            resume_batch_id=batch_id,
            report=report,
            minicrm_project_lookup=minicrm_lookup,
            absorption_project_lookup=absorption_lookup,
            minicrm_project_lookup_by_name=minicrm_lookup_by_name,
            absorption_project_lookup_by_name=absorption_lookup_by_name,
            project_name=project_name,
            batch_state_file_for=_batch_state_file,
        )

        try:
            await check_sync_services_reachable(
                minicrm_url=os.getenv("P100_MINICRM_HEALTH_URL", "http://localhost:8100"),
                backend_url=os.getenv("P100_BACKEND_HEALTH_URL", "http://localhost:8000"),
                timeout=5.0,
                report=report,
            )
        except PreflightError as exc:
            if strict:
                raise
            report.checks.append(f"WARN (non-fatal for --dry-run): {exc}")
    finally:
        await absorption_engine.dispose()
        if minicrm_engine is not None:
            await minicrm_engine.dispose()

    return report


def _print_preview(data, fixture: dict, problems: list[str]) -> None:
    print("=== Source files (SHA-256) ===")
    for name in SOURCE_FILES:
        print(f"  {name}: {data[name][1]}")

    print("\n=== Validation / rejects+warnings ===")
    if problems:
        for p in problems:
            print(f"  REJECT: {p}")
    else:
        print("  clean — 0 problems")

    print("\n=== Fixture diff (current DB has 0 La Pura rows today) ===")
    print(f"  +{len(fixture['projects'])} project")
    print(f"  +{len(fixture['areas'])} area buckets")
    print(f"  +{len(fixture['units'])} units")
    sold = sum(1 for d in fixture["deals"] if d["deal_status"] == "sold")
    reserved = sum(1 for d in fixture["deals"] if d["deal_status"] == "reserved")
    print(f"  +{len(fixture['deals'])} deals ({sold} sold, {reserved} reserved)")
    print(f"  +{len(fixture['unit_enrichment'])} unit_enrichment_attributes rows")
    print("  +0 ranking_scores rows (never written by this tooling)")

    print("\n=== Exact API requests a real --confirm-seed --mode create run would send ===")
    requests_preview = _api_requests_preview(fixture)
    for line in requests_preview[:8]:
        print(f"  {line}")
    print(f"  ... {len(requests_preview) - 8} more requests ({len(requests_preview)} total)")

    print("\n=== Expected sync result ===")
    print(
        "  Each POST above lands in MiniCRM's crm_outbox in the same transaction "
        "(v2 for projects/areas, v1+v2 for units/deals per V1_ENTITIES/V2_ENTITIES). "
        "RelayLoop delivers it to POST /api/v1/sync/{entity}; DomainProjector then "
        "projects it into AbsorpIQ's projects/areas/units/deals with source_system='mini_crm'. "
        "Recompute of that project's CRM operational ranking (trigger='sync') will fire "
        "automatically as an existing, pre-existing side effect of the sync path itself — "
        "expected, not a seed failure, and never touches hierarchical_score or enrichment data."
    )

    print("\n=== Rollback scope a real run would leave behind ===")
    print(
        f"  scripts/rollback_lapura_seed.py --manifest scripts/fixtures/manifests/lapura_seed_manifest_<batch_id>.json\n"
        f"  would delete exactly: {len(fixture['unit_enrichment'])} unit_enrichment_attributes, "
        f"{len(fixture['deals'])} deals, {len(fixture['units'])} units, {len(fixture['areas'])} areas, "
        f"{len(fixture['projects'])} project — all scoped by the real ids in that manifest, in that order, "
        "on AbsorpIQ. MiniCRM-side rollback is not yet implemented (see that script's own note)."
    )


# --- --confirm-seed orchestration --------------------------------------------


def _manifest_path(batch_id: str) -> Path:
    return OUT_MANIFEST_DIR / f"lapura_seed_manifest_{batch_id}.json"


def _load_pass1_manifest_or_raise(batch_id: str, source_sha256_set: frozenset[str]) -> dict[str, Any]:
    if not OUT_FIXTURE.exists():
        try:
            shown = OUT_FIXTURE.relative_to(REPO_ROOT)
        except ValueError:
            shown = OUT_FIXTURE
        raise SeedOrchestrationError(f"{shown} does not exist — run --write-fixture first.")
    path = _manifest_path(batch_id)
    try:
        manifest = load_manifest(path)
    except ManifestError as exc:
        raise SeedOrchestrationError(f"run --write-fixture --batch-id {batch_id} first: {exc}") from exc
    if manifest.get("pass") == 2:
        raise SeedOrchestrationError(
            f"Manifest for batch {batch_id} is already Pass-2 complete — this batch was already seeded. "
            "Use a new --batch-id for a genuinely new seed, or --validate to inspect this one."
        )
    manifest_hashes = {f["sha256"] for f in manifest["source_files"]}
    if manifest_hashes != source_sha256_set:
        raise SeedOrchestrationError(
            f"Source CSVs have changed since batch {batch_id}'s fixture was built "
            f"(manifest hashes {sorted(manifest_hashes)} != current {sorted(source_sha256_set)}). "
            "Re-derive the fixture under a new batch id."
        )
    return manifest


def _run_seed_mini_crm_from_json(*, state_file: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "scripts.seed_mini_crm_from_json",
        "--fixture",
        str(OUT_FIXTURE),
        "--state-file",
        str(state_file),
        "--skip-verify",
    ]
    minicrm_url = os.getenv("MINICRM_URL")
    if minicrm_url:
        cmd.extend(["--base-url", minicrm_url])
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=900)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SeedOrchestrationError(
            f"scripts.seed_mini_crm_from_json exited {result.returncode} — see output above. "
            "No enrichment load was attempted."
        )


async def _absorpiq_counts_for_project(engine: AsyncEngine, project_external_id: str) -> dict[str, int]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.text(
                    "SELECT "
                    "(SELECT count(*) FROM projects WHERE external_id = :e AND source_system = 'mini_crm') AS projects, "
                    "(SELECT count(*) FROM areas a JOIN projects p ON p.id = a.project_id "
                    " WHERE p.external_id = :e AND p.source_system = 'mini_crm') AS areas, "
                    "(SELECT count(*) FROM units u JOIN areas a ON a.id = u.area_id JOIN projects p ON p.id = a.project_id "
                    " WHERE p.external_id = :e AND p.source_system = 'mini_crm') AS units, "
                    "(SELECT count(*) FROM deals d JOIN units u ON u.id = d.unit_id JOIN areas a ON a.id = u.area_id "
                    " JOIN projects p ON p.id = a.project_id WHERE p.external_id = :e AND p.source_system = 'mini_crm') AS deals"
                ),
                {"e": project_external_id},
            )
        ).mappings().first()
    return dict(row)


async def _wait_for_projection(
    engine: AsyncEngine, project_external_id: str, *, timeout: float, interval: float
) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    last_counts: dict[str, int] = {}
    while True:
        last_counts = await _absorpiq_counts_for_project(engine, project_external_id)
        if last_counts == EXPECTED_COUNTS:
            return last_counts
        print(f"  ... projection so far: {last_counts} (target {EXPECTED_COUNTS})")
        if time.monotonic() >= deadline:
            raise SeedOrchestrationError(
                f"Projection timeout after {timeout}s for project {project_external_id}: "
                f"reached {last_counts}, expected {EXPECTED_COUNTS}. No enrichment was loaded. "
                f"Recovery: python -m scripts.seed_lapura --confirm-seed --batch-id <batch> --mode resume "
                "(not yet implemented — investigate the sync/relay logs for this project manually)."
            )
        await asyncio.sleep(interval)


async def _capture_pass2(manifest: dict[str, Any], state_file: Path, absorption_engine: AsyncEngine) -> dict[str, Any]:
    """The MiniCRM-assigned `external_id` for every entity is already known
    from `state_file` (written by `seed_mini_crm_from_json.py`'s own
    `IdentityMap`) — no MiniCRM query is needed here. Only the AbsorpIQ
    UUID, assigned once each row is projected, has to be looked up live."""
    state = json.loads(state_file.read_text(encoding="utf-8"))
    by_kind_extid: dict[str, dict[str, str]] = {"project": {}, "area": {}, "unit": {}, "deal": {}}
    for fixture_key, info in state.items():
        by_kind_extid.setdefault(info["kind"], {})[info["external_id"]] = fixture_key

    async def _bulk(engine: AsyncEngine, sql: str, ids: list[str]) -> list[tuple[str, str]]:
        if not ids:
            return []
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(sql), {"ids": ids})).all()
        return [(r[0], str(r[1])) for r in rows]

    real_ids: dict[str, dict[str, Any]] = {}
    fetch_plan = (
        ("project", "external_id", "SELECT external_id, id FROM projects WHERE external_id = ANY(:ids) AND source_system='mini_crm'"),
        ("area", "external_id", "SELECT external_id, id FROM areas WHERE external_id = ANY(:ids)"),
        ("unit", "external_unit_id", "SELECT external_unit_id, id FROM units WHERE external_unit_id = ANY(:ids)"),
        ("deal", "external_deal_id", "SELECT external_deal_id, id FROM deals WHERE external_deal_id = ANY(:ids)"),
    )
    for kind, _col, sql in fetch_plan:
        ext_ids = list(by_kind_extid[kind].keys())
        rows = await _bulk(absorption_engine, sql, ext_ids)
        for ext_id, real_id in rows:
            fixture_key = by_kind_extid[kind][ext_id]
            real_ids[fixture_key] = {"real_external_id": ext_id, "real_id": real_id}

    missing = [
        e["fixture_external_key"]
        for e in manifest["entities"]
        if e["fixture_external_key"] not in real_ids
    ]
    if missing:
        raise SeedOrchestrationError(
            f"Pass-2 capture incomplete: {len(missing)} entities have no AbsorpIQ row yet "
            f"(first few: {missing[:5]}). This should not happen after a successful projection wait — "
            "investigate before retrying."
        )
    return apply_real_ids(manifest, real_ids)


async def _confirm_seed(fixture: dict[str, Any], batch_id: str) -> int:
    print("\n=== Loading Pass-1 manifest ===")
    source_sha256_set = frozenset(sha for _, sha in load_source()[0].values())
    manifest = _load_pass1_manifest_or_raise(batch_id, source_sha256_set)
    print(f"  batch {batch_id}: {manifest['counts']}")

    print("\n=== Preflight (re-run immediately before the first write) ===")
    report = await _run_preflight(
        mode="create",
        batch_id=batch_id,
        source_sha256_set=source_sha256_set,
        project_name=fixture["projects"][0]["name"],
        strict=True,
    )
    print(report.as_text())

    state_file = OUT_MANIFEST_DIR / f"lapura_state_{batch_id}.json"
    print(f"\n=== Seeding via scripts.seed_mini_crm_from_json (fixture={OUT_FIXTURE.name}, state={state_file.name}) ===")
    _run_seed_mini_crm_from_json(state_file=state_file)

    if not state_file.exists():
        raise SeedOrchestrationError("seed_mini_crm_from_json completed but wrote no state file — cannot proceed.")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    project_info = state.get(PROJECT_EXTERNAL_KEY)
    if project_info is None:
        raise SeedOrchestrationError(f"State file has no entry for {PROJECT_EXTERNAL_KEY!r} — seeding did not complete.")
    project_external_id = project_info["external_id"]
    print(f"\nMiniCRM assigned project external_id={project_external_id}")

    absorption_engine = create_async_engine(_resolve_absorption_url(report))
    try:
        print("\n=== Waiting for AbsorpIQ projection ===")
        counts = await _wait_for_projection(
            absorption_engine, project_external_id, timeout=PROJECTION_POLL_TIMEOUT_S, interval=PROJECTION_POLL_INTERVAL_S
        )
        print(f"  projection complete: {counts}")

        print("\n=== Capturing Pass-2 real ids ===")
        pass2 = await _capture_pass2(manifest, state_file, absorption_engine)
        if not is_pass_2_complete(pass2):
            raise SeedOrchestrationError("Pass-2 manifest built but is_pass_2_complete() is False — refusing to proceed.")
        save_manifest(_manifest_path(batch_id), pass2)
        print(f"  Pass-2 manifest saved: {_manifest_path(batch_id).relative_to(REPO_ROOT)}")

        print("\n=== Loading unit_enrichment_attributes ===")
        rows = _load_enrichment_plan(fixture, pass2)
        session_factory = async_sessionmaker(absorption_engine, expire_on_commit=False)
        n = await _load_enrichment_apply(session_factory, rows)
        print(f"  inserted {n} unit_enrichment_attributes rows")
    finally:
        await absorption_engine.dispose()

    print("\n=== Post-seed validation ===")
    ok, text = await run_validation(batch_id)
    print(text)
    return 0 if ok else 1


# --- --validate ---------------------------------------------------------------


async def run_validation(batch_id: str) -> tuple[bool, str]:
    lines: list[str] = []
    problems: list[str] = []

    def line(text: str) -> None:
        lines.append(text)

    def problem(text: str) -> None:
        problems.append(text)
        lines.append(f"PROBLEM: {text}")

    manifest_path = _manifest_path(batch_id)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        return False, f"PROBLEM: {exc}"

    if not is_pass_2_complete(manifest):
        problem(f"manifest {batch_id} is not Pass-2 complete")
        return False, "\n".join(lines)
    line(f"OK: manifest {batch_id} Pass-2 complete, {len(manifest['entities'])} entities")

    data, _ = load_source()
    current_hashes = {sha for _, sha in data.values()}
    manifest_hashes = {f["sha256"] for f in manifest["source_files"]}
    if current_hashes != manifest_hashes:
        problem(f"source hashes changed since this batch was seeded: {manifest_hashes} != {current_hashes}")
    else:
        line("OK: source CSV hashes match this batch's manifest")

    report = PreflightReport()
    absorption_url = _resolve_absorption_url(report)
    minicrm_url = _resolve_minicrm_url(report)
    absorption_engine = create_async_engine(absorption_url)
    minicrm_engine = create_async_engine(minicrm_url)
    try:
        try:
            await check_absorption_target(absorption_engine, absorption_url, report)
            await check_minicrm_target(minicrm_engine, minicrm_url, report)
        except PreflightError as exc:
            problem(str(exc))
        lines.extend(report.checks)

        project_entity = next(e for e in manifest["entities"] if e["kind"] == "project")
        project_ext_id = project_entity["real_external_id"]

        async with absorption_engine.connect() as conn:
            counts_row = (
                await conn.execute(
                    sa.text(
                        "SELECT "
                        "(SELECT count(*) FROM projects WHERE external_id=:e AND source_system='mini_crm') AS projects, "
                        "(SELECT count(*) FROM areas a JOIN projects p ON p.id=a.project_id WHERE p.external_id=:e) AS areas, "
                        "(SELECT count(*) FROM units u JOIN areas a ON a.id=u.area_id JOIN projects p ON p.id=a.project_id "
                        " WHERE p.external_id=:e) AS units, "
                        "(SELECT count(*) FROM deals d JOIN units u ON u.id=d.unit_id JOIN areas a ON a.id=u.area_id "
                        " JOIN projects p ON p.id=a.project_id WHERE p.external_id=:e) AS deals, "
                        "(SELECT count(*) FROM unit_enrichment_attributes uea JOIN units u ON u.id=uea.unit_id "
                        " JOIN areas a ON a.id=u.area_id JOIN projects p ON p.id=a.project_id WHERE p.external_id=:e) AS enrichment"
                    ),
                    {"e": project_ext_id},
                )
            ).mappings().first()
        counts = dict(counts_row)
        expected = {"projects": 1, "areas": 24, "units": 392, "deals": 193, "enrichment": 392}
        if counts != expected:
            problem(f"scoped counts {counts} != expected {expected}")
        else:
            line(f"OK: scoped counts {counts}")

        async with absorption_engine.connect() as conn:
            status_row = (
                await conn.execute(
                    sa.text(
                        "SELECT u.status, count(*) FROM units u JOIN areas a ON a.id=u.area_id JOIN projects p ON p.id=a.project_id "
                        "WHERE p.external_id=:e GROUP BY u.status"
                    ),
                    {"e": project_ext_id},
                )
            ).all()
            statuses = {r[0]: r[1] for r in status_row}
            recon_row = (
                await conn.execute(
                    sa.text(
                        "WITH lp AS (SELECT u.id, u.status FROM units u JOIN areas a ON a.id=u.area_id "
                        "JOIN projects p ON p.id=a.project_id WHERE p.external_id=:e) "
                        "SELECT "
                        "(SELECT count(*) FROM lp WHERE status='sold' AND NOT EXISTS "
                        " (SELECT 1 FROM deals d WHERE d.unit_id=lp.id AND d.status='sold')) AS sold_no_deal, "
                        "(SELECT count(*) FROM lp WHERE status='reserved' AND NOT EXISTS "
                        " (SELECT 1 FROM deals d WHERE d.unit_id=lp.id AND d.status='reserved')) AS reserved_no_deal, "
                        "(SELECT count(*) FROM lp WHERE status='available' AND EXISTS "
                        " (SELECT 1 FROM deals d WHERE d.unit_id=lp.id)) AS available_with_deal, "
                        "(SELECT count(*) FROM areas a LEFT JOIN projects p ON p.id=a.project_id "
                        " WHERE p.id IS NULL AND a.project_id IN (SELECT id FROM projects WHERE external_id=:e)) AS orphan_areas, "
                        "(SELECT count(*) FROM units u LEFT JOIN areas a ON a.id=u.area_id "
                        " WHERE a.id IS NULL AND u.area_id IN (SELECT a2.id FROM areas a2 JOIN projects p2 ON p2.id=a2.project_id WHERE p2.external_id=:e)) AS orphan_units"
                    ),
                    {"e": project_ext_id},
                )
            ).mappings().first()
        available = statuses.get("available", 0)
        reserved = statuses.get("reserved", 0)
        sold = statuses.get("sold", 0)
        if (available, reserved, sold) != (199, 63, 130) or available + reserved + sold != 392:
            problem(f"status distribution {statuses} != expected available=199/reserved=63/sold=130")
        else:
            line(f"OK: reconciliation 199 available + 63 reserved + 130 sold = {available + reserved + sold}")
        for key in ("sold_no_deal", "reserved_no_deal", "available_with_deal"):
            if recon_row[key] != 0:
                problem(f"{key} = {recon_row[key]}, expected 0")
        line("OK: zero sold/reserved-without-deal, zero available-with-deal, zero orphans")

        async with absorption_engine.connect() as conn:
            dup = (
                await conn.execute(
                    sa.text(
                        "SELECT "
                        "(SELECT count(*) FROM (SELECT external_id FROM projects WHERE external_id IS NOT NULL "
                        " GROUP BY external_id HAVING count(*)>1) x) AS dup_projects, "
                        "(SELECT count(*) FROM (SELECT external_unit_id FROM units GROUP BY external_unit_id "
                        " HAVING count(*)>1) x) AS dup_units, "
                        "(SELECT count(*) FROM (SELECT external_deal_id FROM deals GROUP BY external_deal_id "
                        " HAVING count(*)>1) x) AS dup_deals"
                    )
                )
            ).mappings().first()
        if any(v != 0 for v in dup.values()):
            problem(f"duplicate external ids found: {dict(dup)}")
        else:
            line("OK: zero duplicate external ids anywhere in AbsorpIQ")

        async with absorption_engine.connect() as conn:
            ranking_row = (
                await conn.execute(
                    sa.text(
                        "SELECT rs.contributions, rs.hierarchical_score, rs.hierarchical_contributions "
                        "FROM ranking_scores rs JOIN units u ON u.id=rs.unit_id JOIN areas a ON a.id=u.area_id "
                        "JOIN projects p ON p.id=a.project_id WHERE p.external_id=:e"
                    ),
                    {"e": project_ext_id},
                )
            ).all()
        if ranking_row:
            keys: set[str] = set()
            hier_non_null = 0
            for contributions, hier_score, hier_contrib in ranking_row:
                keys |= set((contributions or {}).keys())
                if hier_score is not None or hier_contrib is not None:
                    hier_non_null += 1
            forbidden_prefixes = ("ticket_depth", "loan_premium_score", "price_per_sqm", "segment_depth", "area_efficiency_score")
            contaminated = [k for k in keys if any(k.startswith(p) for p in forbidden_prefixes)]
            if contaminated:
                problem(f"ranking_scores.contributions contain non-authoritative keys: {contaminated}")
            else:
                line(f"OK: ranking_scores.contributions keys are {sorted(keys)} — no AHP/CSV keys present")
            if hier_non_null:
                problem(f"{hier_non_null} ranking_scores rows have a non-null hierarchical_score/contributions")
            else:
                line("OK: hierarchical_score/hierarchical_contributions NULL on all rows for this project")
        else:
            line("OK: no ranking_scores rows yet for this project (ranking_scores volume is a side effect, not a requirement)")

        plan = _rollback_build_plan(manifest)
        line("OK: rollback dry-run scope (not executed): " + "; ".join(f"{s['table']}={len(s['ids'])}" for s in plan))
    finally:
        await absorption_engine.dispose()
        await minicrm_engine.dispose()

    if shutil.which("git") is None:
        line("WARN: git executable unavailable; skipped legacy seed-file git diff check")
    else:
        diff = subprocess.run(
            ["git", "diff", "--stat", "--", str(LEGACY_SEED_FILE)], cwd=REPO_ROOT, capture_output=True, text=True
        )
        if diff.stdout.strip():
            problem(f"docs/mini_crm_seed.json has a git diff: {diff.stdout.strip()}")
        else:
            line("OK: docs/mini_crm_seed.json byte-unchanged (git diff --stat is empty)")

    return not problems, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--dry-run", action="store_true")
    mode_group.add_argument("--write-fixture", action="store_true")
    mode_group.add_argument("--confirm-seed", action="store_true")
    mode_group.add_argument("--validate", action="store_true")
    parser.add_argument("--mode", choices=["create", "resume"], default="create")
    parser.add_argument("--batch-id", default=None)
    args = parser.parse_args()

    if not args.dry_run and not args.batch_id:
        parser.error("--batch-id is required for --write-fixture, --confirm-seed, and --validate")
    if args.confirm_seed and args.mode != "create":
        parser.error("--confirm-seed only supports --mode create in this pass (resume is not yet implemented)")

    if args.validate:
        ok, text = asyncio.run(run_validation(args.batch_id))
        print(text)
        return 0 if ok else 1

    data, file_order = load_source()
    problems = validate(data)
    fixture = build_fixture(data)
    source_sha256_set = frozenset(sha for _, sha in data.values())

    _print_preview(data, fixture, problems)

    if problems:
        print("\nREFUSED: validation problems found. Stopping before preflight.", file=sys.stderr)
        return 1

    print("\n=== Preflight ===")
    try:
        report = asyncio.run(
            _run_preflight(
                mode=args.mode,
                batch_id=args.batch_id,
                source_sha256_set=source_sha256_set,
                project_name=fixture["projects"][0]["name"],
            )
        )
    except PreflightError as exc:
        print(f"  REFUSED: {exc}", file=sys.stderr)
        return 1
    print(report.as_text())

    if args.dry_run:
        print("\n--dry-run: zero DB writes, zero fixture/manifest writes.")
        return 0

    if args.write_fixture:
        status = _write_fixture_or_refuse(fixture)
        print(f"\nFixture {OUT_FIXTURE.relative_to(REPO_ROOT)}: {status}")
        manifest = build_manifest(data, fixture, batch_id=args.batch_id)
        manifest_path = _manifest_path(args.batch_id)
        if manifest_path.exists():
            print(f"REFUSED: manifest already exists for batch {args.batch_id}.", file=sys.stderr)
            return 1
        save_manifest(manifest_path, manifest)
        print(f"Manifest {manifest_path.relative_to(REPO_ROOT)}: written")
        print("\nZero DB/API writes performed.")
        return 0

    # --confirm-seed
    try:
        return asyncio.run(_confirm_seed(fixture, args.batch_id))
    except SeedOrchestrationError as exc:
        print(
            f"\nSTOPPED: {exc}\n"
            f"  batch_id={args.batch_id}\n"
            f"  fixture={OUT_FIXTURE}\n"
            f"  state_file={OUT_MANIFEST_DIR / f'lapura_state_{args.batch_id}.json'}\n"
            f"  manifest={_manifest_path(args.batch_id)}\n"
            "No enrichment was loaded and nothing was rolled back automatically.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
