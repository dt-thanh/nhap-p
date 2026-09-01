"""Pure-function coverage of scripts/lapura_preflight.py — no real DB/HTTP.

`check_not_already_seeded` is async and, when a matching manifest exists,
calls the injected `minicrm_project_lookup`/`absorption_project_lookup`
callables to verify liveness — these are faked here with plain async
functions, so every branch of the decision table is covered without a
database.
"""

from __future__ import annotations

import json

import pytest

from scripts.lapura_preflight import (
    PreflightError,
    PreflightReport,
    check_not_already_seeded,
    find_prior_batches,
    redact_url,
)

PROJECT_NAME = "La Pura"


def test_redact_url_hides_credentials():
    shown = redact_url("postgresql+asyncpg://app:supersecret@db:5432/absorption")
    assert "supersecret" not in shown
    assert "app" not in shown
    assert "db" in shown
    assert "absorption" in shown


def _write_manifest(
    manifest_dir,
    *,
    batch_id,
    sha256_set,
    project_key="prj-la-pura",
    pass_num=1,
    real_external_id=None,
    real_id=None,
):
    manifest_dir.mkdir(parents=True, exist_ok=True)
    entity = {"kind": "project", "source_row_key": "P-0001", "fixture_external_key": project_key}
    if pass_num == 2:
        entity["real_external_id"] = real_external_id
        entity["real_id"] = real_id
    manifest = {
        "batch_id": batch_id,
        "pass": pass_num,
        "source_files": [{"name": "f.csv", "sha256": s} for s in sha256_set],
        "entities": [entity],
    }
    (manifest_dir / f"lapura_seed_manifest_{batch_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _lookup(result):
    """A fake async lookup that always returns `result` (a dict or None)."""

    async def _fn(external_id):
        return result

    return _fn


def _never_called():
    async def _fn(external_id):
        raise AssertionError(f"lookup should not have been called for {external_id!r}")

    return _fn


def test_find_prior_batches_empty_dir_is_empty_list():
    import pathlib

    assert find_prior_batches(pathlib.Path("/does-not-exist-xyz")) == []


@pytest.mark.asyncio
async def test_create_mode_passes_with_no_prior_batches(tmp_path):
    report = PreflightReport()
    result = await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id=None,
        report=report,
        minicrm_project_lookup=_never_called(),
    )
    assert result is None
    assert "safe" in report.as_text()


@pytest.mark.asyncio
async def test_create_mode_passes_for_a_different_project_key(tmp_path):
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"}, project_key="prj-other")
    report = PreflightReport()
    await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id=None,
        report=report,
        minicrm_project_lookup=_never_called(),
    )


# --- Decision table: matching manifest + live project exists -----------------


@pytest.mark.asyncio
async def test_create_mode_refuses_when_the_matching_project_is_still_live(tmp_path):
    _write_manifest(
        tmp_path,
        batch_id="batch-1",
        sha256_set={"aaa", "bbb"},
        pass_num=2,
        real_external_id="P-0099",
        real_id="uuid-1",
    )
    with pytest.raises(PreflightError, match="still live"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa", "bbb"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_lookup({"id": "uuid-1", "external_id": "P-0099", "name": "La Pura"}),
        )


@pytest.mark.asyncio
async def test_create_mode_refusal_message_names_the_batch_and_project(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-42", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    with pytest.raises(PreflightError, match="batch-42") as exc_info:
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_lookup({"id": "uuid-1", "external_id": "P-0099", "name": "La Pura"}),
        )
    assert "P-0099" in str(exc_info.value)


@pytest.mark.asyncio
async def test_completed_pass2_live_project_is_classified_already_live(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    with pytest.raises(PreflightError, match="already_live"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_lookup({"id": "uuid-1", "external_id": "P-0099", "name": "La Pura"}),
        )


@pytest.mark.asyncio
async def test_completed_pass2_stale_after_wipe_is_classified_stale_after_database_reset(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    report = PreflightReport()
    result = await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id=None,
        report=report,
        minicrm_project_lookup=_lookup(None),
    )
    assert result is None
    assert "stale_after_database_reset" in report.as_text()


@pytest.mark.asyncio
async def test_live_refusal_notes_absorpiq_projection_when_lookup_provided(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    with pytest.raises(PreflightError, match="also projected into AbsorpIQ as uuid-absorb"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_lookup({"id": "uuid-1", "external_id": "P-0099", "name": "La Pura"}),
            absorption_project_lookup=_lookup({"id": "uuid-absorb", "external_id": "P-0099", "name": "La Pura"}),
        )


@pytest.mark.asyncio
async def test_live_refusal_notes_missing_absorpiq_projection(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    with pytest.raises(PreflightError, match="NOT YET projected into AbsorpIQ"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_lookup({"id": "uuid-1", "external_id": "P-0099", "name": "La Pura"}),
            absorption_project_lookup=_lookup(None),
        )


# --- Decision table: matching manifest + freshly wiped/empty DB --------------


@pytest.mark.asyncio
async def test_create_mode_allows_when_matching_project_no_longer_exists_live(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    report = PreflightReport()
    result = await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id=None,
        report=report,
        minicrm_project_lookup=_lookup(None),
    )
    assert result is None
    assert "stale_after_database_reset" in report.as_text()
    assert "batch-1" in report.as_text()


@pytest.mark.asyncio
async def test_stale_classification_never_deletes_or_rewrites_the_manifest(tmp_path):
    original = _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    manifest_file = tmp_path / "lapura_seed_manifest_batch-1.json"
    before = manifest_file.read_text(encoding="utf-8")
    await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id=None,
        report=PreflightReport(),
        minicrm_project_lookup=_lookup(None),
    )
    assert manifest_file.read_text(encoding="utf-8") == before
    assert json.loads(before) == original


@pytest.mark.asyncio
async def test_two_historical_manifests_both_stale_allows_create_and_lists_both(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0098", real_id="uuid-1"
    )
    _write_manifest(
        tmp_path, batch_id="batch-2", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-2"
    )
    report = PreflightReport()
    result = await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id=None,
        report=report,
        minicrm_project_lookup=_lookup(None),
    )
    assert result is None
    assert "batch-1" in report.as_text()
    assert "batch-2" in report.as_text()


@pytest.mark.asyncio
async def test_two_historical_manifests_one_live_one_stale_refuses(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0098", real_id="uuid-1"
    )
    _write_manifest(
        tmp_path, batch_id="batch-2", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-2"
    )

    async def _lookup_only_0099(external_id):
        return {"id": "uuid-2", "external_id": "P-0099", "name": "La Pura"} if external_id == "P-0099" else None

    with pytest.raises(PreflightError, match="still live"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_lookup_only_0099,
        )


# --- Decision table: incomplete Pass-2 (the actual bug this module exists to
# fix — a freshly `--write-fixture`d Pass-1 manifest, before any API write,
# must never be treated as evidence a seed already started). ------------------


def _no_state_file(tmp_path):
    """A batch_state_file_for callable pointing at a path that never exists —
    the normal case right after --write-fixture, before the first API call."""
    return lambda batch_id: tmp_path / f"lapura_state_{batch_id}_MISSING.json"


def _state_file_with(tmp_path, *, batch_id, content):
    path = tmp_path / f"lapura_state_{batch_id}.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return lambda bid: tmp_path / f"lapura_state_{bid}.json"


@pytest.mark.asyncio
async def test_create_mode_fails_closed_when_no_state_file_lookup_is_wired(tmp_path):
    # Without a way to check the state file, Pass-2-incomplete cannot be
    # proven safe — this must still fail closed (never silently assume ready).
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=1)
    with pytest.raises(PreflightError, match="partial_or_ambiguous"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
        )


@pytest.mark.asyncio
async def test_ready_to_create_for_a_fresh_pass1_manifest_with_no_state_file_and_clean_dbs(tmp_path):
    _write_manifest(tmp_path, batch_id="lapura-20260828-reset-002", sha256_set={"aaa"}, pass_num=1)
    report = PreflightReport()
    result = await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id="lapura-20260828-reset-002",
        report=report,
        minicrm_project_lookup=_never_called(),
        minicrm_project_lookup_by_name=_lookup(None),
        absorption_project_lookup_by_name=_lookup(None),
        project_name=PROJECT_NAME,
        batch_state_file_for=_no_state_file(tmp_path),
    )
    assert result is None
    assert "ready_to_create" in report.as_text()
    assert "lapura-20260828-reset-002" in report.as_text()


@pytest.mark.asyncio
async def test_partial_or_ambiguous_when_state_file_has_a_successful_project_post(tmp_path):
    _write_manifest(tmp_path, batch_id="batch-retry", sha256_set={"aaa"}, pass_num=1)
    state_lookup = _state_file_with(
        tmp_path, batch_id="batch-retry", content={"prj-la-pura": {"kind": "project", "external_id": "P-0029"}}
    )
    with pytest.raises(PreflightError, match="partial_or_ambiguous") as exc_info:
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-retry",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
            minicrm_project_lookup_by_name=_never_called(),
            absorption_project_lookup_by_name=_never_called(),
            project_name=PROJECT_NAME,
            batch_state_file_for=state_lookup,
        )
    assert "batch-retry" in str(exc_info.value)
    assert "--mode resume --batch-id batch-retry" in str(exc_info.value)


@pytest.mark.asyncio
async def test_same_batch_reused_after_a_failed_attempt_is_partial_or_ambiguous(tmp_path):
    # Simulates: --write-fixture, --confirm-seed partially ran (some API
    # writes succeeded, state file recorded them), then crashed before Pass-2
    # capture. Re-running --confirm-seed --mode create with the SAME batch id
    # must refuse, not silently re-attempt a fresh create.
    _write_manifest(tmp_path, batch_id="batch-retry", sha256_set={"aaa"}, pass_num=1)
    state_lookup = _state_file_with(
        tmp_path,
        batch_id="batch-retry",
        content={
            "prj-la-pura": {"kind": "project", "external_id": "P-0029"},
            "area-la-pura-a-0001": {"kind": "area", "external_id": "A-1001"},
        },
    )
    with pytest.raises(PreflightError, match="partial_or_ambiguous"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-retry",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
            minicrm_project_lookup_by_name=_never_called(),
            absorption_project_lookup_by_name=_never_called(),
            project_name=PROJECT_NAME,
            batch_state_file_for=state_lookup,
        )


@pytest.mark.asyncio
async def test_partial_or_ambiguous_when_minicrm_has_a_partial_live_record_by_name(tmp_path):
    # No state file at all, but a live MiniCRM project with this exact name
    # already exists — someone/something wrote it outside (or despite) the
    # recorded state file. Must not be waved through as clean.
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=1)
    with pytest.raises(PreflightError, match="partial_or_ambiguous") as exc_info:
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
            minicrm_project_lookup_by_name=_lookup({"id": "uuid-1", "external_id": "P-0029", "name": PROJECT_NAME}),
            absorption_project_lookup_by_name=_never_called(),
            project_name=PROJECT_NAME,
            batch_state_file_for=_no_state_file(tmp_path),
        )
    assert "conflicting evidence" in str(exc_info.value)


@pytest.mark.asyncio
async def test_partial_or_ambiguous_when_absorpiq_has_a_partial_live_record_by_name(tmp_path):
    # No state file, MiniCRM by-name lookup clean, but AbsorpIQ already has a
    # live project by this name — still conflicting evidence, still refused.
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=1)
    with pytest.raises(PreflightError, match="partial_or_ambiguous") as exc_info:
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
            minicrm_project_lookup_by_name=_lookup(None),
            absorption_project_lookup_by_name=_lookup({"id": "uuid-absorb", "external_id": "P-0029", "name": PROJECT_NAME}),
            project_name=PROJECT_NAME,
            batch_state_file_for=_no_state_file(tmp_path),
        )
    assert "conflicting evidence" in str(exc_info.value)


@pytest.mark.asyncio
async def test_partial_or_ambiguous_when_no_project_name_or_name_lookup_wired(tmp_path):
    # Even with a clean (missing) state file, if there's no way to check the
    # DBs by name, this must still refuse rather than assume clean.
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=1)
    with pytest.raises(PreflightError, match="partial_or_ambiguous"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
            batch_state_file_for=_no_state_file(tmp_path),
        )


@pytest.mark.asyncio
async def test_partial_or_ambiguous_when_state_file_lookup_raises(tmp_path):
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=1)

    def _broken_state_file(batch_id):
        class _UnreadablePath:
            def exists(self):
                return True

            def read_text(self, encoding=None):
                raise OSError("permission denied")

        return _UnreadablePath()

    with pytest.raises(PreflightError, match="partial_or_ambiguous") as exc_info:
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
            project_name=PROJECT_NAME,
            batch_state_file_for=_broken_state_file,
        )
    assert "could not be read" in str(exc_info.value)


@pytest.mark.asyncio
async def test_partial_or_ambiguous_when_minicrm_by_name_lookup_raises(tmp_path):
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=1)

    async def _broken(name):
        raise ConnectionError("connection refused")

    with pytest.raises(PreflightError, match="partial_or_ambiguous") as exc_info:
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
            minicrm_project_lookup_by_name=_broken,
            project_name=PROJECT_NAME,
            batch_state_file_for=_no_state_file(tmp_path),
        )
    assert "failed" in str(exc_info.value)


# --- Decision table: internally inconsistent manifest state ("conflicting
# state evidence") — never seen from this tooling's own lifecycle, but must
# still fail closed rather than guess which half (pass number vs captured
# ids) to trust. -----------------------------------------------------------


@pytest.mark.asyncio
async def test_conflicting_state_evidence_pass2_marked_but_id_missing(tmp_path):
    manifest_dir = tmp_path
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "batch_id": "batch-1",
        "pass": 2,
        "source_files": [{"name": "f.csv", "sha256": "aaa"}],
        "entities": [
            {
                "kind": "project",
                "source_row_key": "P-0001",
                "fixture_external_key": "prj-la-pura",
                "real_external_id": "P-0029",
                # real_id deliberately missing despite pass=2.
            }
        ],
    }
    (manifest_dir / "lapura_seed_manifest_batch-1.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PreflightError, match="partial_or_ambiguous") as exc_info:
        await check_not_already_seeded(
            manifest_dir=manifest_dir,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
        )
    assert "inconsistent manifest state" in str(exc_info.value)


@pytest.mark.asyncio
async def test_conflicting_state_evidence_ids_present_but_pass_not_2(tmp_path):
    manifest_dir = tmp_path
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "batch_id": "batch-1",
        "pass": 1,
        "source_files": [{"name": "f.csv", "sha256": "aaa"}],
        "entities": [
            {
                "kind": "project",
                "source_row_key": "P-0001",
                "fixture_external_key": "prj-la-pura",
                "real_external_id": "P-0029",
                "real_id": "uuid-1",
            }
        ],
    }
    (manifest_dir / "lapura_seed_manifest_batch-1.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PreflightError, match="inconsistent manifest state"):
        await check_not_already_seeded(
            manifest_dir=manifest_dir,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
        )


@pytest.mark.asyncio
async def test_create_mode_fails_closed_when_no_lookup_available_but_a_match_exists(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    with pytest.raises(PreflightError, match="no live MiniCRM lookup"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=None,
        )


# --- Decision table: manifest points to another DB/environment --------------


@pytest.mark.asyncio
async def test_manifest_from_another_environment_is_treated_as_stale_not_live(tmp_path):
    # The lookup is bound to the CURRENT verified target only — a manifest
    # captured while pointed at some other environment simply won't be found
    # there, which is indistinguishable from (and just as safe as) a genuine
    # post-wipe stale batch.
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0007", real_id="uuid-1"
    )
    report = PreflightReport()
    result = await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="create",
        resume_batch_id=None,
        report=report,
        minicrm_project_lookup=_lookup(None),
    )
    assert result is None
    assert "stale_after_database_reset" in report.as_text()


# --- Network/DB lookup failure -----------------------------------------------


@pytest.mark.asyncio
async def test_lookup_failure_fails_closed_not_silently(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )

    async def _broken(external_id):
        raise ConnectionError("connection refused")

    with pytest.raises(PreflightError, match="lookup for 'P-0099' failed"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_broken,
        )


@pytest.mark.asyncio
async def test_absorption_lookup_failure_fails_closed(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )

    async def _broken(external_id):
        raise ConnectionError("connection refused")

    with pytest.raises(PreflightError, match="AbsorpIQ liveness lookup"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_lookup({"id": "uuid-1", "external_id": "P-0099", "name": "La Pura"}),
            absorption_project_lookup=_broken,
        )


# --- Duplicate/mismatched project identity -----------------------------------


@pytest.mark.asyncio
async def test_mismatched_lookup_identity_fails_closed(tmp_path):
    _write_manifest(
        tmp_path, batch_id="batch-1", sha256_set={"aaa"}, pass_num=2, real_external_id="P-0099", real_id="uuid-1"
    )
    with pytest.raises(PreflightError, match="mismatched identity"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            # A buggy/mismatched lookup returning a DIFFERENT external_id than asked for.
            minicrm_project_lookup=_lookup({"id": "uuid-1", "external_id": "P-9999", "name": "Someone Else"}),
        )


@pytest.mark.asyncio
async def test_manifest_with_more_than_one_project_entity_fails_closed(tmp_path):
    manifest_dir = tmp_path
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "batch_id": "batch-1",
        "pass": 2,
        "source_files": [{"name": "f.csv", "sha256": "aaa"}],
        "entities": [
            {
                "kind": "project",
                "source_row_key": "P-0001",
                "fixture_external_key": "prj-la-pura",
                "real_external_id": "P-0099",
                "real_id": "uuid-1",
            },
            {
                "kind": "project",
                "source_row_key": "P-0002",
                "fixture_external_key": "prj-la-pura",
                "real_external_id": "P-0100",
                "real_id": "uuid-2",
            },
        ],
    }
    (manifest_dir / "lapura_seed_manifest_batch-1.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PreflightError, match="expected exactly one project entity"):
        await check_not_already_seeded(
            manifest_dir=manifest_dir,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="create",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
        )


# --- resume mode (unchanged behavior, now under an async function) ----------


@pytest.mark.asyncio
async def test_resume_mode_requires_batch_id(tmp_path):
    with pytest.raises(PreflightError, match="requires --batch-id"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"aaa"}),
            project_external_key="prj-la-pura",
            mode="resume",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
        )


@pytest.mark.asyncio
async def test_resume_mode_succeeds_against_the_exact_matching_batch(tmp_path):
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"})
    report = PreflightReport()
    result = await check_not_already_seeded(
        manifest_dir=tmp_path,
        source_sha256_set=frozenset({"aaa"}),
        project_external_key="prj-la-pura",
        mode="resume",
        resume_batch_id="batch-1",
        report=report,
        minicrm_project_lookup=_never_called(),
    )
    assert result["batch_id"] == "batch-1"


@pytest.mark.asyncio
async def test_resume_mode_refuses_a_batch_id_that_does_not_match_the_dataset(tmp_path):
    _write_manifest(tmp_path, batch_id="batch-1", sha256_set={"aaa"})
    with pytest.raises(PreflightError, match="no prior manifest matches"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset({"different-hash"}),
            project_external_key="prj-la-pura",
            mode="resume",
            resume_batch_id="batch-1",
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
        )


@pytest.mark.asyncio
async def test_unknown_mode_raises(tmp_path):
    with pytest.raises(PreflightError, match="Unknown --mode"):
        await check_not_already_seeded(
            manifest_dir=tmp_path,
            source_sha256_set=frozenset(),
            project_external_key="prj-la-pura",
            mode="bogus",
            resume_batch_id=None,
            report=PreflightReport(),
            minicrm_project_lookup=_never_called(),
        )
