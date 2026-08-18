"""CLI của bộ kiểm phù hợp: mã thoát và hai dạng báo cáo.

Mã thoát là phần hợp đồng THẬT của công cụ này — nó sẽ được gọi từ CI và từ cổng
kiểm tra trước khi cắt sang, nơi không ai đọc chữ. Nên chúng được kiểm riêng, và
kiểm cả trường hợp từ chối chạy.

Chạy: TEST_TARGET=tests/test_scripts/test_conformance_check.py bash scripts/test_db.sh
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa

from scripts.conformance_check import EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_VIOLATIONS, main

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _refuses_to_wipe(url: str | None) -> str:
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' vì tên không kết thúc bằng '_test'."
    return ""


_SKIP_REASON = _refuses_to_wipe(TEST_DATABASE_URL)

pytestmark = pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "")

PROJECT_ID = uuid.UUID("c9d0e1f2-a3b4-4526-8738-9abcdef01d9d")
INSTANCE = "crm-conformance-cli"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


@pytest.fixture(autouse=True)
def db_env(monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("APP_ENV", "test")
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    engine = sa.create_engine(_sync_url(TEST_DATABASE_URL))

    def wipe(conn):
        conn.execute(sa.text("DELETE FROM upload_files WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with engine.begin() as conn:
        wipe(conn)
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:p, 'CLI', :d, :ts)"),
            {"p": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (gen_random_uuid(), :p, 'A1', '2PN', 2, 75, 50, now())"
            ),
            {"p": PROJECT_ID},
        )
    yield
    with engine.begin() as conn:
        wipe(conn)
    engine.dispose()
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


def _write(tmp_path: Path, body: dict, name="payload.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(body), encoding="utf-8")
    return str(path)


def _good_payload() -> dict:
    return {
        "schema_version": 1,
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "external_batch_id": f"cli-{uuid.uuid4().hex[:8]}",
        "sync_mode": "incremental",
        "project_ref": {"project_id": str(PROJECT_ID)},
        "source_extracted_at": "2026-08-09T02:00:00+07:00",
        "records": [
            {
                "entity": "unit",
                "operation": "upsert",
                "external_id": "CLI-U-1",
                "source_revision": 1,
                "payload": {
                    "area_ref": {"area_name": "A1", "unit_type": "2PN"},
                    "unit_code": "A1-01",
                    "unit_status": "available",
                },
            }
        ],
    }


# --- Mã thoát -----------------------------------------------------------------


def test_a_conforming_payload_exits_zero(tmp_path, capsys):
    assert main([_write(tmp_path, _good_payload())]) == EXIT_OK
    assert "ĐẠT" in capsys.readouterr().out


def test_a_violating_payload_exits_one(tmp_path, capsys):
    body = _good_payload()
    body["records"][0]["payload"]["unit_status"] = 42

    assert main([_write(tmp_path, body)]) == EXIT_VIOLATIONS
    assert "KHÔNG ĐẠT" in capsys.readouterr().out


def test_a_missing_file_exits_two(tmp_path):
    assert main([str(tmp_path / "khong-ton-tai.json")]) == EXIT_USAGE


def test_it_refuses_production_with_its_own_exit_code(tmp_path, monkeypatch):
    """Mã thoát riêng: "từ chối chạy" không được lẫn với "payload sai"."""
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        assert main([_write(tmp_path, _good_payload())]) == EXIT_REFUSED
    finally:
        get_settings.cache_clear()


def test_one_bad_file_in_a_batch_fails_the_whole_run(tmp_path):
    """Cổng kiểm tra không được đạt chỉ vì phần lớn tệp đạt."""
    good = _write(tmp_path, _good_payload(), "a.json")
    bad_body = _good_payload()
    bad_body["records"][0]["payload"]["unit_status"] = 42
    bad = _write(tmp_path, bad_body, "b.json")

    assert main([good, bad]) == EXIT_VIOLATIONS


# --- Hai dạng báo cáo ---------------------------------------------------------


def test_json_mode_prints_only_parseable_json(tmp_path, capsys):
    """Có `--json` thì stdout phải parse được nguyên vẹn — nếu banner lọt vào,
    mọi đường pipe đều gãy."""
    main(["--json", _write(tmp_path, _good_payload())])

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["conforms"] is True
    assert parsed["would_apply"]["projections"]["inserted"] == 1


def test_json_mode_returns_a_list_for_several_files(tmp_path, capsys):
    main(["--json", _write(tmp_path, _good_payload(), "a.json"), _write(tmp_path, _good_payload(), "b.json")])

    assert len(json.loads(capsys.readouterr().out)) == 2


def test_json_out_writes_a_file_and_still_prints_for_humans(tmp_path, capsys):
    out = tmp_path / "report.json"
    main([_write(tmp_path, _good_payload()), "--json-out", str(out)])

    assert json.loads(out.read_text(encoding="utf-8"))["conforms"] is True
    assert "ĐẠT" in capsys.readouterr().out


def test_the_human_report_puts_violations_before_the_summary(tmp_path, capsys):
    """Người chạy công cụ đang tìm "sai ở đâu"; bắt họ cuộn qua bảng số liệu
    trước là đặt sai thứ tự ưu tiên."""
    body = _good_payload()
    body["records"][0]["payload"]["unit_status"] = 42
    main([_write(tmp_path, body)])

    out = capsys.readouterr().out
    assert out.index("VI PHẠM") < out.index("Cổng đã chạy")


def test_the_human_report_carries_the_read_only_banner(tmp_path, capsys):
    main([_write(tmp_path, _good_payload())])

    assert "CHỈ ĐỌC" in capsys.readouterr().out


def test_every_report_says_it_proves_nothing_about_a_real_crm(tmp_path, capsys):
    main([_write(tmp_path, _good_payload())])
    human = capsys.readouterr().out

    main(["--json", _write(tmp_path, _good_payload())])
    machine = json.loads(capsys.readouterr().out)

    assert "KHÔNG chứng minh" in human
    assert "KHÔNG chứng minh" in machine["disclaimer"]


def test_a_non_synthetic_file_name_is_accepted(tmp_path, capsys):
    """Không có chốt tiền tố `synthetic-` như trình mô phỏng: công cụ này không
    gửi gì đi đâu, nên nó phải nhận được payload thật."""
    body = _good_payload()
    body["source_instance_id"] = "acme-crm-prod-01"
    path = _write(tmp_path, body, "acme_export_2026_08.json")

    assert main([path]) in (EXIT_OK, EXIT_VIOLATIONS), "phải chạy được, không từ chối vì tên tệp"
    assert "acme_export_2026_08.json" in capsys.readouterr().out
