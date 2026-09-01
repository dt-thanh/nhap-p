"""Coverage for scripts/lapura_preflight.py's host/container execution-context
resolver (`resolve_execution_url`) — the fix for host processes failing to
resolve Docker Compose service hostnames (`db`, `minicrm_db`) that only exist
on the Compose-internal network.

All `docker compose port` calls and container detection are faked here; no
real Docker Compose invocation happens in this suite.
"""

from __future__ import annotations

import subprocess

import pytest

from scripts.lapura_preflight import (
    PreflightError,
    PreflightReport,
    _docker_compose_port,
    check_app_env,
    resolve_execution_url,
)

ABSORPTION_DB_NAMES = frozenset({"absorption", "absorption_dev", "absorption_test"})
RAW_ABSORPTION_URL = "postgresql+asyncpg://app:supersecret@db:5432/absorption"


def _never_run(*args, **kwargs):
    raise AssertionError("docker compose port should not have been invoked in this branch")


def _fake_compose_port(stdout: str = "0.0.0.0:5432", returncode: int = 0, stderr: str = ""):
    def _run(cmd, cwd, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return _run


# --- explicit override ---------------------------------------------------------


def test_explicit_override_used_as_is_without_querying_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", _never_run)
    report = PreflightReport()
    result = resolve_execution_url(
        RAW_ABSORPTION_URL,
        compose_service="db",
        container_port=5432,
        allowed_db_names=ABSORPTION_DB_NAMES,
        label="AbsorpIQ",
        explicit_override=True,
        repo_root=tmp_path,
        report=report,
    )
    assert result == RAW_ABSORPTION_URL
    assert any("explicit environment override" in c for c in report.checks)


def test_explicit_override_still_fails_closed_on_unexpected_database_name(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", _never_run)
    report = PreflightReport()
    with pytest.raises(PreflightError, match="not in the local/dev allowlist"):
        resolve_execution_url(
            "postgresql+asyncpg://app:x@db:5432/some_other_db",
            compose_service="db",
            container_port=5432,
            allowed_db_names=ABSORPTION_DB_NAMES,
            label="AbsorpIQ",
            explicit_override=True,
            repo_root=tmp_path,
            report=report,
        )


# --- container execution --------------------------------------------------------


def test_container_execution_preserves_compose_hostname(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: True)
    monkeypatch.setattr(subprocess, "run", _never_run)
    report = PreflightReport()
    result = resolve_execution_url(
        RAW_ABSORPTION_URL,
        compose_service="db",
        container_port=5432,
        allowed_db_names=ABSORPTION_DB_NAMES,
        label="AbsorpIQ",
        explicit_override=False,
        repo_root=tmp_path,
        report=report,
    )
    assert result == RAW_ABSORPTION_URL
    assert any("container execution context" in c for c in report.checks)


def test_container_execution_fails_closed_on_unexpected_host(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: True)
    monkeypatch.setattr(subprocess, "run", _never_run)
    report = PreflightReport()
    with pytest.raises(PreflightError, match="not in the local/dev allowlist"):
        resolve_execution_url(
            "postgresql+asyncpg://app:x@some-other-host:5432/absorption",
            compose_service="db",
            container_port=5432,
            allowed_db_names=ABSORPTION_DB_NAMES,
            label="AbsorpIQ",
            explicit_override=False,
            repo_root=tmp_path,
            report=report,
        )


# --- host execution --------------------------------------------------------------


def test_host_execution_rewrites_compose_service_host_via_docker_compose_port(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: False)
    monkeypatch.setattr(subprocess, "run", _fake_compose_port("0.0.0.0:5432"))
    report = PreflightReport()
    result = resolve_execution_url(
        RAW_ABSORPTION_URL,
        compose_service="db",
        container_port=5432,
        allowed_db_names=ABSORPTION_DB_NAMES,
        label="AbsorpIQ",
        explicit_override=False,
        repo_root=tmp_path,
        report=report,
    )
    assert "127.0.0.1:5432" in result
    assert "db:5432" not in result
    # The report line must never leak the password, but the returned URL is a
    # real connection string handed straight to create_async_engine() — it
    # MUST still carry the real password, not SQLAlchemy's str(URL) "***" mask
    # (a regression once caught for real: the resolved URL round-tripped with
    # a literal "***" password and every connection failed auth).
    assert "supersecret" not in report.as_text()
    assert "supersecret" in result
    assert "***" not in result
    assert any("resolved Compose service" in c and "127.0.0.1:5432" in c for c in report.checks)


def test_host_execution_uses_a_non_zero_bind_address_verbatim(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: False)
    monkeypatch.setattr(subprocess, "run", _fake_compose_port("127.0.0.1:5434"))
    report = PreflightReport()
    result = resolve_execution_url(
        "postgresql+asyncpg://minicrm:minicrm@minicrm_db:5432/minicrm",
        compose_service="minicrm_db",
        container_port=5432,
        allowed_db_names=frozenset({"minicrm", "minicrm_dev", "minicrm_test"}),
        label="MiniCRM",
        explicit_override=False,
        repo_root=tmp_path,
        report=report,
    )
    assert "127.0.0.1:5434" in result
    assert "minicrm:minicrm@" in result


def test_host_execution_passes_through_a_url_that_is_already_non_compose(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: False)
    monkeypatch.setattr(subprocess, "run", _never_run)
    report = PreflightReport()
    already_host_url = "postgresql+asyncpg://app:x@localhost:5432/absorption"
    result = resolve_execution_url(
        already_host_url,
        compose_service="db",
        container_port=5432,
        allowed_db_names=ABSORPTION_DB_NAMES,
        label="AbsorpIQ",
        explicit_override=False,
        repo_root=tmp_path,
        report=report,
    )
    assert result == already_host_url


def test_host_execution_fails_closed_when_port_mapping_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: False)
    monkeypatch.setattr(subprocess, "run", _fake_compose_port(stdout="", returncode=1, stderr="no such service: db"))
    report = PreflightReport()
    with pytest.raises(PreflightError, match="docker compose up -d"):
        resolve_execution_url(
            RAW_ABSORPTION_URL,
            compose_service="db",
            container_port=5432,
            allowed_db_names=ABSORPTION_DB_NAMES,
            label="AbsorpIQ",
            explicit_override=False,
            repo_root=tmp_path,
            report=report,
        )


def test_host_execution_fails_closed_when_docker_binary_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: False)

    def _raise(*args, **kwargs):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    report = PreflightReport()
    with pytest.raises(PreflightError, match="Could not query"):
        resolve_execution_url(
            RAW_ABSORPTION_URL,
            compose_service="db",
            container_port=5432,
            allowed_db_names=ABSORPTION_DB_NAMES,
            label="AbsorpIQ",
            explicit_override=False,
            repo_root=tmp_path,
            report=report,
        )


def test_host_execution_fails_closed_when_resolved_host_is_not_allowlisted(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.lapura_preflight._running_in_container", lambda: False)
    monkeypatch.setattr(subprocess, "run", _fake_compose_port("203.0.113.9:5432"))
    report = PreflightReport()
    with pytest.raises(PreflightError, match="not in the local/dev allowlist"):
        resolve_execution_url(
            RAW_ABSORPTION_URL,
            compose_service="db",
            container_port=5432,
            allowed_db_names=ABSORPTION_DB_NAMES,
            label="AbsorpIQ",
            explicit_override=False,
            repo_root=tmp_path,
            report=report,
        )


def test_docker_compose_port_parses_host_and_port(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", _fake_compose_port("0.0.0.0:5434"))
    host, port = _docker_compose_port("minicrm_db", 5432, cwd=tmp_path)
    assert (host, port) == ("0.0.0.0", 5434)


def test_docker_compose_port_fails_closed_on_unparseable_output(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", _fake_compose_port("garbage"))
    with pytest.raises(PreflightError, match="Unexpected"):
        _docker_compose_port("db", 5432, cwd=tmp_path)


# --- wrong environment (APP_ENV) --------------------------------------------------


@pytest.mark.asyncio
async def test_check_app_env_fails_closed_for_non_development():
    report = PreflightReport()
    with pytest.raises(PreflightError, match="only \\['development'\\]"):
        await check_app_env("production", report)


@pytest.mark.asyncio
async def test_check_app_env_allows_development():
    report = PreflightReport()
    await check_app_env("development", report)
    assert any("APP_ENV=development" in c for c in report.checks)
