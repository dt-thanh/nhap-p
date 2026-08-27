"""Fail-closed tests for the local AbsorpIQ business-data clear command."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import clear_absorpiq_data as clear_script


def _settings(*, app_env: str = "development", dsn: str = "postgresql+asyncpg://app:pw@db:5432/absorption"):
    return SimpleNamespace(app_env=app_env, database_dsn=dsn)


def test_classification_preserves_auth_schema_and_sync_credential():
    assert set(clear_script.PRESERVED_TABLES).isdisjoint(clear_script.BUSINESS_TABLES)
    assert "alembic_version" in clear_script.PRESERVED_TABLES
    assert "users" in clear_script.PRESERVED_TABLES
    assert "refresh_tokens" in clear_script.PRESERVED_TABLES
    assert "settings" in clear_script.PRESERVED_TABLES
    assert "sync_credentials" in clear_script.PRESERVED_TABLES
    assert "ranking_configs" in clear_script.PRESERVED_TABLES
    assert "crm_source_records" in clear_script.BUSINESS_TABLES
    assert "sync_payloads" in clear_script.BUSINESS_TABLES


def test_runtime_guard_accepts_only_local_development(monkeypatch):
    monkeypatch.setattr(clear_script, "get_settings", lambda: _settings())
    assert clear_script._guard_runtime() == ("db", "absorption")


@pytest.mark.parametrize(
    "settings, message",
    [
        (_settings(app_env="production"), "APP_ENV"),
        (_settings(app_env="staging"), "APP_ENV"),
        (_settings(dsn="postgresql+asyncpg://app:pw@shared-db:5432/absorption"), "host"),
        (_settings(dsn="postgresql+asyncpg://app:pw@db:5432/production"), "name"),
    ],
)
def test_runtime_guard_fails_closed(monkeypatch, settings, message):
    monkeypatch.setattr(clear_script, "get_settings", lambda: settings)
    with pytest.raises(clear_script.ClearDataError, match=message):
        clear_script._guard_runtime()


def test_truncate_statement_has_no_cascade():
    statement = "TRUNCATE TABLE " + clear_script._quote_table_names(clear_script.BUSINESS_TABLES) + " RESTART IDENTITY"
    assert "CASCADE" not in statement.upper()
    assert "RESTART IDENTITY" in statement


def test_cli_requires_explicit_yes_for_writes(monkeypatch, capsys):
    calls: list[bool] = []

    async def fake_clear(*, confirm: bool):
        calls.append(confirm)
        return {
            "host": "db",
            "database": "absorption",
            "business_before": {"projects": 2},
            "business_after": {"projects": 0},
            "preserved": {name: 1 for name in clear_script.PRESERVED_TABLES},
            "confirmed": confirm,
        }

    monkeypatch.setattr(clear_script, "clear", fake_clear)
    assert clear_script.main([]) == 0
    assert calls == [False]
    assert "No writes performed" in capsys.readouterr().out

    assert clear_script.main(["--yes"]) == 0
    assert calls == [False, True]
