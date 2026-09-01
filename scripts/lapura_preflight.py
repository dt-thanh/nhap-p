"""Preflight checks for the La Pura seed — imported by
`scripts/seed_lapura.py`, never run implicitly by anything else.

Every check here is read-only. Nothing in this module writes to a database
or calls a mutating HTTP endpoint.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

ProjectLookup = Callable[[str], Awaitable[dict[str, Any] | None]]
BatchStateFileLookup = Callable[[str], Path]

ALLOWED_APP_ENVS = frozenset({"development"})
ALLOWED_HOSTS = frozenset({"db", "minicrm_db", "localhost", "127.0.0.1", "::1"})
ALLOWED_ABSORPTION_DB_NAMES = frozenset({"absorption", "absorption_dev", "absorption_test"})
ALLOWED_MINICRM_DB_NAMES = frozenset({"minicrm", "minicrm_dev", "minicrm_test"})
DOCKER_COMPOSE_PORT_TIMEOUT_S = 10.0


class PreflightError(RuntimeError):
    """A fail-closed preflight check. Never caught silently — the caller
    must stop and report it."""


@dataclass
class PreflightReport:
    checks: list[str] = field(default_factory=list)

    def ok(self, message: str) -> None:
        self.checks.append(f"OK: {message}")

    def as_text(self) -> str:
        return "\n".join(self.checks)


def redact_url(url: str) -> str:
    """Host/database/port only — password and username are never shown."""
    parsed = make_url(url)
    return f"{parsed.drivername}://***:***@{parsed.host}:{parsed.port}/{parsed.database}"


def _guard_host_and_db(url: str, *, allowed_db_names: frozenset[str], label: str) -> tuple[str, str]:
    parsed = make_url(url)
    host = parsed.host or ""
    database = parsed.database or ""
    if host not in ALLOWED_HOSTS:
        raise PreflightError(f"{label}: host {host!r} is not in the local/dev allowlist.")
    if database not in allowed_db_names:
        raise PreflightError(f"{label}: database {database!r} is not in the local/dev allowlist.")
    return host, database


def _running_in_container() -> bool:
    """Standard, dependency-free container heuristic. Deliberately not a
    bespoke env var — nothing in this repo's compose files sets one, and
    inventing a new signal just for this check would be one more thing to
    keep in sync between `docker-compose.yml` and this module."""
    return Path("/.dockerenv").exists()


def _docker_compose_port(service: str, container_port: int, *, cwd: Path) -> tuple[str, int]:
    """`docker compose port` only queries the already-running stack's
    published port mapping. It starts, stops, and builds nothing."""
    try:
        result = subprocess.run(
            ["docker", "compose", "port", service, str(container_port)],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=DOCKER_COMPOSE_PORT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(
            f"Could not query `docker compose port {service} {container_port}`: {exc}. "
            "Is Docker Compose installed and is the stack up (`docker compose up -d`)?"
        ) from exc
    output = result.stdout.strip()
    if result.returncode != 0 or not output:
        raise PreflightError(
            f"`docker compose port {service} {container_port}` returned no mapping "
            f"(exit {result.returncode}): {result.stderr.strip() or '(empty output)'}. "
            f"Is the `{service}` service up? Run `docker compose up -d {service}` first."
        )
    host, _, port_s = output.rpartition(":")
    if not host or not port_s.isdigit():
        raise PreflightError(f"Unexpected `docker compose port {service} {container_port}` output: {output!r}")
    return host, int(port_s)


def resolve_execution_url(
    raw_url: str,
    *,
    compose_service: str,
    container_port: int,
    allowed_db_names: frozenset[str],
    label: str,
    explicit_override: bool,
    repo_root: Path,
    report: PreflightReport,
) -> str:
    """Makes `raw_url` (as configured in `.env` / `minicrm/.env`, which
    hardcodes Docker Compose service hostnames like `db`/`minicrm_db`) usable
    from wherever this process is actually running:

      - explicit env var override -> used as-is (still allowlist-guarded).
      - running inside a container -> used as-is; `db`/`minicrm_db` already
        resolve on the Compose network.
      - running on the host and the URL points at a Compose service hostname
        -> rewritten to the real published host port via
        `docker compose port`, never guessed or hardcoded.
      - running on the host but the URL already points somewhere else
        (already localhost, a remote dev box, etc.) -> used as-is, still
        allowlist-guarded.

    Fails closed (raises PreflightError) on any unavailable port mapping,
    unexpected resulting host, or unexpected database name. Never rewrites
    outside of these explicit, logged branches.
    """
    parsed = make_url(raw_url)
    host = parsed.host or ""

    if explicit_override:
        _guard_host_and_db(raw_url, allowed_db_names=allowed_db_names, label=f"{label} (explicit env override)")
        report.ok(f"{label}: explicit environment override in effect, using {redact_url(raw_url)} unchanged")
        return raw_url

    if _running_in_container():
        _guard_host_and_db(raw_url, allowed_db_names=allowed_db_names, label=f"{label} (container execution)")
        report.ok(f"{label}: container execution context, using {redact_url(raw_url)} unchanged")
        return raw_url

    if host != compose_service:
        _guard_host_and_db(
            raw_url, allowed_db_names=allowed_db_names, label=f"{label} (host execution, non-Compose host)"
        )
        report.ok(
            f"{label}: host execution, URL host {host!r} is not a Compose service name, "
            f"using {redact_url(raw_url)} unchanged"
        )
        return raw_url

    mapped_host, mapped_port = _docker_compose_port(compose_service, container_port, cwd=repo_root)
    resolved_host = "127.0.0.1" if mapped_host in ("0.0.0.0", "") else mapped_host
    # `str(URL)`/`URL.__str__` masks the password (renders literal "***") —
    # fine for display, fatal for an actual connection string. This is the
    # real DSN handed to create_async_engine(), so the password must survive.
    resolved_url = parsed.set(host=resolved_host, port=mapped_port).render_as_string(hide_password=False)
    _guard_host_and_db(resolved_url, allowed_db_names=allowed_db_names, label=f"{label} (host execution, resolved)")
    report.ok(
        f"{label}: host execution — resolved Compose service {compose_service!r}:{container_port} to "
        f"{resolved_host}:{mapped_port} via `docker compose port`; effective target {redact_url(resolved_url)}"
    )
    return resolved_url


async def check_app_env(app_env: str, report: PreflightReport) -> None:
    if app_env not in ALLOWED_APP_ENVS:
        raise PreflightError(f"APP_ENV={app_env!r}; only {sorted(ALLOWED_APP_ENVS)} is permitted for this seed.")
    report.ok(f"APP_ENV={app_env} is in the allowed set")


async def check_absorption_target(engine: AsyncEngine, database_url: str, report: PreflightReport) -> str:
    _guard_host_and_db(database_url, allowed_db_names=ALLOWED_ABSORPTION_DB_NAMES, label="AbsorpIQ")
    async with engine.connect() as conn:
        revisions = (await conn.execute(sa.text('SELECT version_num FROM "alembic_version"'))).scalars().all()
    if len(revisions) != 1:
        raise PreflightError(f"AbsorpIQ alembic_version has {len(revisions)} rows, expected exactly 1.")
    report.ok(f"AbsorpIQ target={redact_url(database_url)}, alembic head={revisions[0]}")
    return revisions[0]


async def check_minicrm_target(engine: AsyncEngine, database_url: str, report: PreflightReport) -> str:
    _guard_host_and_db(database_url, allowed_db_names=ALLOWED_MINICRM_DB_NAMES, label="MiniCRM")
    async with engine.connect() as conn:
        revisions = (await conn.execute(sa.text('SELECT version_num FROM "alembic_version"'))).scalars().all()
    if len(revisions) != 1:
        raise PreflightError(f"MiniCRM alembic_version has {len(revisions)} rows, expected exactly 1.")
    report.ok(f"MiniCRM target={redact_url(database_url)}, alembic head={revisions[0]}")
    return revisions[0]


def find_prior_batches(manifest_dir: Path) -> list[dict[str, Any]]:
    if not manifest_dir.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(manifest_dir.glob("lapura_seed_manifest_*.json"))]


def make_minicrm_project_lookup(engine: AsyncEngine) -> ProjectLookup:
    """Real, live lookup: does a `crm_projects` row with this external_id
    still exist in the target this preflight already verified? Bound to a
    specific engine so a manifest from a completed-but-since-wiped batch is
    checked against the CURRENT target, never trusted from the file alone."""

    async def _lookup(external_id: str) -> dict[str, Any] | None:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text("SELECT id, external_id, name FROM crm_projects WHERE external_id = :e"),
                    {"e": external_id},
                )
            ).mappings().first()
            return dict(row) if row else None

    return _lookup


def make_absorption_project_lookup(engine: AsyncEngine) -> ProjectLookup:
    """Same idea, on the AbsorpIQ side — supplementary evidence only (a
    project can legitimately exist in MiniCRM slightly ahead of its AbsorpIQ
    projection finishing), never the sole liveness signal."""

    async def _lookup(external_id: str) -> dict[str, Any] | None:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text(
                        "SELECT id, external_id, name FROM projects "
                        "WHERE external_id = :e AND source_system = 'mini_crm'"
                    ),
                    {"e": external_id},
                )
            ).mappings().first()
            return dict(row) if row else None

    return _lookup


def make_minicrm_project_lookup_by_name(engine: AsyncEngine) -> ProjectLookup:
    """Supplementary lookup by project NAME, not external_id — the only
    handle available for a batch whose Pass-2 never completed (no captured
    real external_id exists yet to look up). MiniCRM does not enforce name
    uniqueness, so a hit here is a red flag worth investigating, never proof
    of identity by itself — it is only ever used to move a classification
    from `ready_to_create` to `partial_or_ambiguous`, never the reverse."""

    async def _lookup(name: str) -> dict[str, Any] | None:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text("SELECT id, external_id, name FROM crm_projects WHERE name = :n"),
                    {"n": name},
                )
            ).mappings().first()
            return dict(row) if row else None

    return _lookup


def make_absorption_project_lookup_by_name(engine: AsyncEngine) -> ProjectLookup:
    """Same idea, on the AbsorpIQ side — see `make_minicrm_project_lookup_by_name`."""

    async def _lookup(name: str) -> dict[str, Any] | None:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text("SELECT id, external_id, name FROM projects WHERE name = :n AND source_system = 'mini_crm'"),
                    {"n": name},
                )
            ).mappings().first()
            return dict(row) if row else None

    return _lookup


async def _classify_incomplete_batch(
    m: dict[str, Any],
    *,
    batch_state_file_for: BatchStateFileLookup | None,
    project_name: str | None,
    minicrm_project_lookup_by_name: ProjectLookup | None,
    absorption_project_lookup_by_name: ProjectLookup | None,
) -> tuple[bool, str]:
    """Classifies a manifest whose Pass-2 was never completed. Returns
    `(True, reason)` for `ready_to_create`, `(False, reason)` for
    `partial_or_ambiguous`.

    A freshly `--write-fixture`d Pass-1 manifest has an incomplete Pass-2 by
    definition, before the first API call ever happens — that alone must
    never be read as evidence a seed started (see module docstring / the
    fix this function exists for). The only things that count as evidence
    are: a non-empty batch state file (only ever written by
    `seed_mini_crm_from_json.py` after a successful API response), or a live
    DB record matching the project's name despite no such write being
    recorded — each checked explicitly, never inferred from Pass-2 alone.
    """
    batch_id = m["batch_id"]

    if batch_state_file_for is None:
        return False, (
            f"batch {batch_id}: no batch-state-file lookup was provided to verify whether a seed attempt "
            "already started — refusing to guess. Investigate manually, then use `--mode resume "
            f"--batch-id {batch_id}` if a prior attempt is confirmed in progress."
        )

    state_path = batch_state_file_for(batch_id)
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return False, (
                f"batch {batch_id}: state file {state_path} exists but could not be read ({exc}) — refusing "
                "to guess whether a seed attempt already started."
            )
        if state:
            return False, (
                f"batch {batch_id}: state file {state_path} already has {len(state)} recorded entrie(s) but "
                "Pass-2 was never completed — a previous --confirm-seed attempt appears to have started and "
                f"not finished. Investigate, then use `--mode resume --batch-id {batch_id}` to continue it "
                "(never a fresh --mode create for this same batch)."
            )

    if project_name is None or minicrm_project_lookup_by_name is None:
        return False, (
            f"batch {batch_id}: state file shows no recorded write, but no project name / MiniCRM by-name "
            "lookup was provided to verify there is no live record despite that — refusing to guess."
        )

    try:
        live_by_name = await minicrm_project_lookup_by_name(project_name)
    except Exception as exc:  # noqa: BLE001 - a lookup failure must fail closed, never be swallowed
        return False, (
            f"batch {batch_id}: MiniCRM by-name check for {project_name!r} failed: {exc}. Refusing to "
            "guess whether a seed attempt already started."
        )
    if live_by_name is not None:
        return False, (
            f"batch {batch_id}: state file shows no recorded write, but a live MiniCRM project named "
            f"{project_name!r} (external_id={live_by_name.get('external_id')!r}) already exists — "
            "conflicting evidence. Investigate before choosing --mode create or --mode resume."
        )

    if absorption_project_lookup_by_name is not None:
        try:
            live_absorpiq_by_name = await absorption_project_lookup_by_name(project_name)
        except Exception as exc:  # noqa: BLE001 - same fail-closed rule as the MiniCRM check above
            return False, (
                f"batch {batch_id}: AbsorpIQ by-name check for {project_name!r} failed: {exc}. Refusing "
                "to guess whether a seed attempt already started."
            )
        if live_absorpiq_by_name is not None:
            return False, (
                f"batch {batch_id}: state file shows no recorded write, and no matching live MiniCRM "
                f"project, but a live AbsorpIQ project named {project_name!r} "
                f"(id={live_absorpiq_by_name.get('id')!r}) already exists — conflicting evidence. "
                "Investigate before choosing --mode create or --mode resume."
            )

    return True, (
        f"batch {batch_id}: Pass-1 manifest with no recorded API write (state file absent/empty) and no "
        "matching live record in MiniCRM/AbsorpIQ by name — ready_to_create."
    )


async def check_not_already_seeded(
    *,
    manifest_dir: Path,
    source_sha256_set: frozenset[str],
    project_external_key: str,
    mode: str,
    resume_batch_id: str | None,
    report: PreflightReport,
    minicrm_project_lookup: ProjectLookup | None = None,
    absorption_project_lookup: ProjectLookup | None = None,
    minicrm_project_lookup_by_name: ProjectLookup | None = None,
    absorption_project_lookup_by_name: ProjectLookup | None = None,
    project_name: str | None = None,
    batch_state_file_for: BatchStateFileLookup | None = None,
) -> dict[str, Any] | None:
    """Mirrors this same source dataset (by SHA-256 set) + the same project
    natural key against every manifest this tooling has ever written.

    A host-file manifest match is NEVER, by itself, proof that the dataset
    is still live — a Docker volume wipe destroys the database but not this
    file. For `--mode create`, every matching manifest is classified
    independently:

      - Pass-2 complete, live match found -> `already_live`: refuse, name
        the exact existing batch/project.
      - Pass-2 complete, the manifest's own captured project no longer
        exists live -> `stale_after_database_reset`: allow create.
      - Pass-2 recorded but its captured ids are internally inconsistent
        (e.g. only one of real_external_id/real_id present) ->
        `partial_or_ambiguous`: refuse.
      - Pass-2 incomplete (the normal, expected state right after
        `--write-fixture`, before any API call) -> classified by
        `_classify_incomplete_batch`: `ready_to_create` when there is no
        recorded API write (batch state file absent/empty) and no live
        record matching the project's name in either database;
        `partial_or_ambiguous` otherwise (non-empty state file, or a live
        record found despite no recorded write, or a lookup failure) — never
        assumed `partial_or_ambiguous` from Pass-2 incompleteness alone.

    Never deletes, rewrites, or mutates any manifest — stale ones are only
    reported, left on disk for audit.
    """
    prior = find_prior_batches(manifest_dir)
    matching = [
        m
        for m in prior
        if {f["sha256"] for f in m["source_files"]} == source_sha256_set
        and any(e["fixture_external_key"] == project_external_key for e in m["entities"] if e["kind"] == "project")
    ]

    if mode == "create":
        if not matching:
            report.ok("no prior matching batch found — --mode create is safe")
            return None
        if minicrm_project_lookup is None:
            raise PreflightError(
                f"{len(matching)} prior matching manifest(s) found for {project_external_key} but no live "
                "MiniCRM lookup was provided to verify whether they are still live — refusing to guess."
            )

        live_matches: list[str] = []
        stale_batches: list[str] = []
        ready_batches: list[str] = []
        ambiguous: list[str] = []
        for m in matching:
            project_entities = [
                e for e in m["entities"] if e["kind"] == "project" and e["fixture_external_key"] == project_external_key
            ]
            if len(project_entities) != 1:
                raise PreflightError(
                    f"manifest {m['batch_id']}: expected exactly one project entity for "
                    f"{project_external_key}, found {len(project_entities)} — refusing to guess."
                )
            entity = project_entities[0]
            real_external_id = entity.get("real_external_id")
            real_id = entity.get("real_id")
            recorded_pass2 = m.get("pass") == 2
            ids_complete = bool(real_external_id) and bool(real_id)

            if recorded_pass2 != ids_complete:
                # Internally inconsistent manifest: pass=2 with a missing id, or
                # a captured id with pass!=2. Never seen from this tooling's own
                # normal lifecycle — could mean hand-edited or corrupted state.
                ambiguous.append(
                    f"batch {m['batch_id']}: inconsistent manifest state (pass={m.get('pass')!r}, "
                    f"real_external_id={real_external_id!r}, real_id={real_id!r}) — refusing to guess. "
                    f"Investigate manually, then use `--mode resume --batch-id {m['batch_id']}` once resolved."
                )
                continue

            if not ids_complete:
                # Pass-2 genuinely incomplete — the normal state right after
                # --write-fixture, before the first API call. Never fail
                # closed on this alone; classify using real evidence instead.
                ready, reason = await _classify_incomplete_batch(
                    m,
                    batch_state_file_for=batch_state_file_for,
                    project_name=project_name,
                    minicrm_project_lookup_by_name=minicrm_project_lookup_by_name,
                    absorption_project_lookup_by_name=absorption_project_lookup_by_name,
                )
                if ready:
                    ready_batches.append(m["batch_id"])
                    report.checks.append(f"ready_to_create: {reason}")
                else:
                    ambiguous.append(reason)
                continue

            try:
                live_row = await minicrm_project_lookup(real_external_id)
            except Exception as exc:  # noqa: BLE001 - a lookup failure must fail closed, never be swallowed
                raise PreflightError(
                    f"manifest {m['batch_id']}: MiniCRM liveness lookup for {real_external_id!r} failed: {exc}. "
                    "Refusing to guess whether the project still exists."
                ) from exc

            if live_row is None:
                stale_batches.append(m["batch_id"])
                report.checks.append(
                    f"stale_after_database_reset: batch {m['batch_id']} (project {real_external_id}) no "
                    "longer exists in the live MiniCRM target — treated as safe to recreate; manifest left "
                    "on disk, unmodified."
                )
                continue

            if live_row.get("external_id") != real_external_id:
                raise PreflightError(
                    f"manifest {m['batch_id']}: MiniCRM lookup for {real_external_id!r} returned a mismatched "
                    f"identity ({live_row!r}) — refusing to guess."
                )

            absorpiq_note = ""
            if absorption_project_lookup is not None:
                try:
                    absorpiq_row = await absorption_project_lookup(real_external_id)
                except Exception as exc:  # noqa: BLE001 - same fail-closed rule as the MiniCRM lookup
                    raise PreflightError(
                        f"manifest {m['batch_id']}: AbsorpIQ liveness lookup for {real_external_id!r} "
                        f"failed: {exc}."
                    ) from exc
                absorpiq_note = (
                    f", also projected into AbsorpIQ as {absorpiq_row['id']}"
                    if absorpiq_row
                    else ", NOT YET projected into AbsorpIQ"
                )
            live_matches.append(
                f"batch {m['batch_id']} -> MiniCRM {live_row['external_id']} ({live_row['name']!r}){absorpiq_note}"
            )

        if live_matches:
            raise PreflightError(
                f"Refusing --mode create: this exact dataset + project ({project_external_key}) is still "
                f"live (already_live): {'; '.join(live_matches)}. Use --mode resume with the original batch "
                "manifest, or confirm this is genuinely a second, independent seed and change the project "
                "natural key."
            )

        if ambiguous:
            raise PreflightError(
                f"Refusing --mode create: partial_or_ambiguous state detected — {'; '.join(ambiguous)}"
            )

        report.ok(
            f"no LIVE prior batch found — {len(stale_batches)} historical manifest(s) classified "
            f"stale_after_database_reset ({stale_batches}), {len(ready_batches)} manifest(s) classified "
            f"ready_to_create ({ready_batches}) — --mode create is safe"
        )
        return None

    if mode == "resume":
        if not resume_batch_id:
            raise PreflightError("--mode resume requires --batch-id pointing at the original batch manifest.")
        exact = [m for m in matching if m["batch_id"] == resume_batch_id]
        if not exact:
            raise PreflightError(
                f"--mode resume: no prior manifest matches both batch-id={resume_batch_id!r} and this exact "
                "dataset + project. Refusing to resume against a manifest that doesn't match — that would risk "
                "creating a second La Pura project instead of completing the first."
            )
        report.ok(f"resuming approved batch {resume_batch_id}")
        return exact[0]

    raise PreflightError(f"Unknown --mode {mode!r}; must be 'create' or 'resume'.")


async def check_sync_services_reachable(
    *, minicrm_url: str, backend_url: str, timeout: float, report: PreflightReport
) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        for name, url in (("MiniCRM", minicrm_url), ("AbsorpIQ backend", backend_url)):
            try:
                resp = await client.get(f"{url}/health")
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise PreflightError(f"{name} at {url} did not respond healthy: {exc}") from exc
    report.ok(f"MiniCRM ({minicrm_url}) and AbsorpIQ backend ({backend_url}) both healthy")
