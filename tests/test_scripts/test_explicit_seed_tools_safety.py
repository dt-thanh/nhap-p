"""Proves the explicit-seed-tools-only invariant (2026-08-28): fixture/demo
generation happens ONLY through explicit, confirmed, dev-only commands, never
as a hidden or automatic write, and no tool can create a domain row with
`source_system IS NULL` (untraceable).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(module: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os

    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- 4/5. Explicit, confirmed, dev-only — bare/ambiguous invocations refuse -


def test_seed_legacy_fixture_requires_dry_run_or_confirm_seed():
    result = _run_cli("scripts.seed_legacy_fixture")
    assert result.returncode != 0
    assert "--dry-run" in result.stderr or "--confirm-seed" in result.stderr or "required" in result.stderr.lower()


def test_seed_legacy_fixture_dry_run_performs_zero_writes_and_never_opens_a_socket():
    """`--dry-run` must never require a DB connection at all — confirmed by
    running with no DATABASE_URL/host reachable, and it must still succeed."""
    result = _run_cli(
        "scripts.seed_legacy_fixture",
        "--dry-run",
        env={"DATABASE_URL": "postgresql+asyncpg://nobody:nobody@203.0.113.1:1/does-not-exist"},
    )
    assert result.returncode == 0, result.stderr
    assert "NON-AUTHORITATIVE" in result.stdout
    assert "source_system='crm_real_data_fixture'" in result.stdout


def test_seed_domain_demo_2026_requires_dry_run_or_confirm_seed():
    result = _run_cli("scripts.seed_domain_demo_2026")
    assert result.returncode != 0
    assert "required" in result.stderr.lower()


def test_seed_domain_demo_2026_dry_run_performs_zero_writes():
    result = _run_cli(
        "scripts.seed_domain_demo_2026",
        "--dry-run",
        env={"APP_ENV": "development"},
    )
    assert result.returncode == 0, result.stderr
    assert "mode: dry-run" in result.stdout
    assert "NON-AUTHORITATIVE" in result.stdout


def test_seed_dev_confirmation_gate_refuses_without_confirm_seed():
    from scripts.seed_dev import _assert_development_confirmed

    with pytest.raises(RuntimeError, match="confirm-seed"):
        _assert_development_confirmed(confirmed=False)
    # No exception with confirmed=True and no production-like APP_ENV set.
    _assert_development_confirmed(confirmed=True)


def test_seed_dev_confirmation_gate_refuses_outside_development(monkeypatch):
    from scripts.seed_dev import _assert_development_confirmed

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="development"):
        _assert_development_confirmed(confirmed=True)


def test_sync_simulator_seed_project_refuses_without_confirm_seed():
    from scripts.sync_simulator import cmd_seed_project

    with pytest.raises(SystemExit, match="confirm-seed"):
        cmd_seed_project(confirmed=False)


def test_sync_simulator_seed_project_refuses_outside_development(monkeypatch):
    from scripts.sync_simulator import cmd_seed_project

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(SystemExit, match="development"):
        cmd_seed_project(confirmed=True)


# --- 5. Refusal on production/staging or non-allowlisted DB targets --------


def test_seed_domain_demo_2026_target_gate_still_refuses_production_and_minicrm():
    from scripts.seed_domain_demo_2026 import _target_metadata

    with pytest.raises(RuntimeError, match="production-like"):
        _target_metadata(
            "postgresql+asyncpg://app:app@db:5432/absorption",
            classification="development",
            app_environment="production",
        )
    with pytest.raises(RuntimeError, match="production-like or belongs to Mini CRM"):
        _target_metadata(
            "postgresql+asyncpg://app:app@db:5432/minicrm",
            classification="development",
            app_environment="development",
        )


def test_dry_run_lineage_cleanup_refuses_an_unapproved_lineage():
    from scripts.dry_run_lineage_cleanup import main as cleanup_main

    assert cleanup_main(["--source-system", "something_else", "--source-instance-id", "whatever"]) == 1


# --- 6. No tool can create a domain row with source_system IS NULL ----------


def test_seed_dev_stamps_lineage_on_every_project_and_area_row():
    """Structural, not just behavioral: every dict `seed_dev.py` builds for
    `projects`/`areas` must carry a non-null `source_system` key — grepping
    the built rows directly, not just trusting the docstring."""
    from scripts.seed_dev import build_dataset

    dataset = dict(build_dataset())
    for table_name in ("projects", "areas"):
        rows = dataset[table_name]
        assert rows, f"{table_name} dataset must be non-empty for this test to mean anything"
        for row in rows:
            assert row.get("source_system"), f"{table_name} row {row.get('id')} has no source_system — untraceable"
            assert row.get("source_instance_id"), f"{table_name} row {row.get('id')} has no source_instance_id"
    # And it must be a lineage that can never collide with a real Mini CRM
    # row or any other known fixture's identity.
    all_systems = {row["source_system"] for row in dataset["projects"]} | {row["source_system"] for row in dataset["areas"]}
    assert all_systems == {"seed_dev_fixture"}
    assert "mini_crm" not in all_systems


def test_sync_simulator_seed_project_sql_stamps_lineage():
    """Reads the source of `cmd_seed_project` and confirms its INSERT
    statements include `source_system`/`source_instance_id` columns —
    structural guard against a future edit silently dropping the stamp."""
    source = (REPO_ROOT / "scripts" / "sync_simulator.py").read_text(encoding="utf-8")
    insert_projects = source[source.index("INSERT INTO projects") : source.index("INSERT INTO projects") + 300]
    assert "source_system" in insert_projects
    assert "source_instance_id" in insert_projects


def test_seed_domain_demo_2026_plan_never_produces_null_source_system():
    """The explicit demo CLI's own plan-builder — direct proof, not
    inference — every row it would ever write carries lineage. (The legacy
    fixture's equivalent proof is a live-DB check, since `build_upserts()`
    returns compiled `Executable`s rather than raw dicts — see
    `tests/test_migrations/test_0019_seed_ai_crm_fixture.py::
    test_fixture_units_are_scoped_to_fixture_source_instance` and
    `tests/test_migrations/test_domain_seed_neutralized.py`, both of which
    assert `SELECT DISTINCT source_system, source_instance_id` is exactly the
    fixture identity, never NULL, against a real database.)"""
    from datetime import date

    from scripts.seed_domain_demo_2026 import SeedConfig, build_plan

    demo_plan = build_plan(SeedConfig(date(2026, 8, 16), date(2025, 8, 16), date(2026, 8, 16)))
    for row in (*demo_plan.projects, *demo_plan.areas, *demo_plan.units, *demo_plan.deals):
        assert row.get("source_system"), "a synthetic-demo row must never have a blank/null source_system"
