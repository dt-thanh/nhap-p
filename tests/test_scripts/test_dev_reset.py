from pathlib import Path


ROOT = Path(__file__).parents[2]
RESET_SCRIPT = ROOT / "scripts/dev-reset.sh"
MINICRM_SQL = ROOT / "scripts/dev-hard-reset-minicrm.sql"
ABSORPIQ_SQL = ROOT / "scripts/dev-hard-reset-absorpiq.sql"


def _truncate_body(sql: str) -> str:
    return sql.split("TRUNCATE TABLE", 1)[1].split(";", 1)[0]


def test_reset_is_data_only_and_requires_explicit_confirmation():
    script = RESET_SCRIPT.read_text()

    assert "--yes" in script
    assert "docker compose stop" in script
    assert "docker compose down -v" not in script
    assert "docker compose up -d db minicrm_db redis" in script
    assert "alembic upgrade head" in script
    assert "APP_ENV" in script and "development" in script
    assert "sync_credentials" in script
    assert ".venv/bin/python" in script
    assert '"$seed_python" -m scripts.seed_mini_crm_from_json --skip-verify' in script


def test_sync_credential_handoff_is_restored_after_reset():
    script = RESET_SCRIPT.read_text()
    reset_end = script.index("scripts/dev-hard-reset-absorpiq.sql")
    credential_handoff = script.index("bash scripts/ensure_sync_credential.sh")

    assert credential_handoff > reset_end
    assert "Đảm bảo đúng một sync_credentials active" in script
    assert "MINICRM_SYNC_API_KEY" not in script, "credential phải đi qua handoff script/secrets, không hard-code ở reset"


def test_sql_resets_preserve_migration_history_and_sync_credential():
    minicrm = MINICRM_SQL.read_text()
    absorpiq = ABSORPIQ_SQL.read_text()

    assert "alembic_version" not in _truncate_body(minicrm)
    assert "alembic_version" not in _truncate_body(absorpiq)
    assert "sync_credentials" not in _truncate_body(absorpiq)
    assert "ranking_configs" not in _truncate_body(absorpiq)

    executable_sql = "\n".join(
        line for line in (minicrm + "\n" + absorpiq).splitlines() if not line.lstrip().startswith("--")
    )
    assert " CASCADE" not in executable_sql.upper()


def test_current_classified_tables_are_explicitly_present():
    minicrm = MINICRM_SQL.read_text()
    absorpiq = ABSORPIQ_SQL.read_text()

    for table in (
        "crm_projects",
        "crm_areas",
        "crm_units",
        "crm_deals",
        "crm_outbox",
        "crm_users",
        "crm_auth_sessions",
        "crm_auth_invites",
        "crm_password_reset_tokens",
    ):
        assert table in minicrm

    for table in (
        "projects",
        "areas",
        "units",
        "deals",
        "crm_source_records",
        "sync_payloads",
        "upload_files",
        "forecast_jobs",
        "ranking_runs",
        "ranking_scores",
        "users",
        "settings",
        "sync_credentials",
    ):
        assert table in absorpiq
