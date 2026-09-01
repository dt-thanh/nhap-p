from __future__ import annotations

from scripts.derive_lapura_seed_fixture import build_fixture, load_source
from scripts.seed_lapura import _api_requests_preview, _read_env_var


def test_api_requests_preview_covers_every_fixture_row():
    data, _ = load_source()
    fixture = build_fixture(data)
    preview = _api_requests_preview(fixture)
    expected = len(fixture["projects"]) + len(fixture["areas"]) + len(fixture["units"]) + len(fixture["deals"])
    assert len(preview) == expected
    assert preview[0].startswith("POST /projects")
    assert any(line.startswith("POST /areas") for line in preview)
    assert any(line.startswith("POST /units") for line in preview)
    assert any(line.startswith("POST /deals") for line in preview)


def test_read_env_var_prefers_process_env_over_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MY_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("MY_KEY", "from-process-env")
    assert _read_env_var(env_file, "MY_KEY") == "from-process-env"


def test_read_env_var_falls_back_to_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MY_KEY=from-file\n", encoding="utf-8")
    monkeypatch.delenv("MY_KEY", raising=False)
    assert _read_env_var(env_file, "MY_KEY") == "from-file"


def test_read_env_var_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    assert _read_env_var(tmp_path / "does-not-exist.env", "MY_KEY") is None
