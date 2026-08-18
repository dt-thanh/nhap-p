"""Migration 0015 — ranking_runs (chỉ-thêm) và ranking_scores (trạng thái hiện tại).

Bốn thứ được canh kỹ nhất:

1. **Chốt chống dồn.** `uq_ranking_runs_queued_per_project` là thứ duy nhất ngăn
   một trăm lô đồng bộ trong một phút sinh ra một trăm lần xếp hạng toàn dự án.
   Nó phải chặn theo TỪNG dự án, không phải toàn cục — test 3 và 4 là một cặp.
2. **Trạng thái và mốc thời gian không mâu thuẫn.** Một run `completed` mà thiếu
   `finished_at` là một run không đo được thời lượng, và cổng cắt sang sau này
   đọc chính những con số đó.
3. **Một điểm hiện hành cho mỗi căn.** `ranking_scores` là TRẠNG THÁI, không phải
   lịch sử; hai dòng cho một căn nghĩa là đường đọc phải chọn, và nó sẽ chọn sai.
4. **Bản chiếu Core khớp schema thật.** `src/models/tables.py` là hình chiếu của
   migration; hai bên lệch nhau thì mã ứng dụng gọi tên cột không tồn tại và lỗi
   chỉ nổ lúc chạy.

Chạy trên DATABASE DÙNG MỘT LẦN (`mig15_<hex>_test`), tạo và huỷ trong từng test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from src.models.tables import feature_snapshots, ranking_configs, ranking_runs, ranking_scores

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)

PREVIOUS_REVISION = "0014_ranking_foundation"
REVISION = "0015_ranking_results"

PROJECT_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
OTHER_PROJECT_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")
AREA_ID = uuid.UUID("99999999-9999-4999-8999-999999999999")
UNIT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"


@pytest.fixture
def scratch_db():
    name = f"mig15_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(TEST_DATABASE_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"), {"n": name}
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    """DB đã lên 0015, kèm hai dự án, một phân khu và một căn để gắn khoá ngoại."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.begin() as conn:
        for pid, name in ((PROJECT_ID, "SYNTH-P1"), (OTHER_PROJECT_ID, "SYNTH-P2")):
            conn.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, :n, :d, now())"),
                {"i": pid, "n": name, "d": "2026-01-01"},
            )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:i, :p, 'A1', '2PN', 2, 75, 100, now())"
            ),
            {"i": AREA_ID, "p": PROJECT_ID},
        )
        conn.execute(
            sa.text(
                "INSERT INTO units (id, source_system, source_instance_id, external_unit_id, area_id, unit_code, "
                "unit_type, status, created_at, updated_at) "
                "VALUES (:i, 'mini_crm', 'synthetic', 'U-0001', :a, 'A1-01', '2PN', 'available', now(), now())"
            ),
            {"i": UNIT_ID, "a": AREA_ID},
        )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _config_id(conn) -> uuid.UUID:
    """Config v1 do 0014 seed."""
    return conn.execute(sa.text("SELECT id FROM ranking_configs WHERE version = 1")).scalar_one()


def _run(conn, **overrides):
    values = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "sync_run_id": None,
        "trigger": "manual",
        "status": "queued",
        "units_processed": 0,
        "units_ranked": 0,
        "units_skipped": 0,
        "started_at": None,
        "finished_at": None,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_runs (id, project_id, sync_run_id, trigger, scope_type, scope_ids, "
            "config_version_id, status, attempt, units_processed, units_ranked, units_skipped, error_summary, "
            "enqueued_at, started_at, finished_at) "
            "VALUES (:id, :project_id, :sync_run_id, :trigger, 'project', '{}'::jsonb, NULL, :status, 0, "
            ":units_processed, :units_ranked, :units_skipped, '{}'::jsonb, now(), :started_at, :finished_at)"
        ),
        values,
    )
    return values["id"]


def _score(conn, run_id, config_id, **overrides):
    values = {
        "id": uuid.uuid4(),
        "unit_id": UNIT_ID,
        "area_id": AREA_ID,
        "project_id": PROJECT_ID,
        "ranking_run_id": run_id,
        "config_version_id": config_id,
        "score": 0.75,
        "rank_in_area": 1,
        "rank_in_project": 1,
        "weight_coverage": 1.0,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_scores (id, unit_id, area_id, project_id, ranking_run_id, config_version_id, "
            "score, rank_in_area, rank_in_project, weight_coverage, contributions, feature_freshness_at, "
            "computed_at) VALUES (:id, :unit_id, :area_id, :project_id, :ranking_run_id, :config_version_id, "
            ":score, :rank_in_area, :rank_in_project, :weight_coverage, '{}'::jsonb, now(), now())"
        ),
        values,
    )
    return values["id"]


# --- 1/2/12. Lên, xuống, và thứ tự hạ cấp -----------------------------------


def test_upgrade_from_0014_creates_both_tables(upgraded):
    with upgraded["engine"].connect() as conn:
        found = set(
            conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('ranking_runs', 'ranking_scores')"
                )
            ).scalars()
        )
    assert found == {"ranking_runs", "ranking_scores"}


def test_downgrade_to_0014_removes_both_tables_and_keeps_0014_tables(upgraded):
    """Thứ tự hạ cấp: `ranking_scores` TRƯỚC `ranking_runs` (có khoá ngoại).

    Đảo lại thì lệnh vỡ giữa chừng. Hạ cấp chạy sạch chính là bằng chứng thứ tự
    đúng — và hai bảng của 0014 phải còn nguyên.
    """
    upgraded["engine"].dispose()
    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            gone = list(
                conn.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_name IN ('ranking_runs', 'ranking_scores')"
                    )
                ).scalars()
            )
            kept = set(
                conn.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_name IN ('feature_snapshots', 'ranking_configs')"
                    )
                ).scalars()
            )
            revision = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()

    assert gone == []
    assert kept == {"feature_snapshots", "ranking_configs"}
    assert revision == PREVIOUS_REVISION


# --- 3/4. Chốt chống dồn -----------------------------------------------------


def test_two_queued_runs_for_one_project_are_rejected(upgraded):
    """Đây là thứ ngăn hàng đợi bị lụt: xếp hạng lại luôn ở phạm vi TOÀN dự án."""
    with upgraded["engine"].begin() as conn:
        _run(conn)

    with pytest.raises(IntegrityError, match="uq_ranking_runs_queued_per_project"):
        with upgraded["engine"].begin() as conn:
            _run(conn)


def test_two_queued_runs_for_different_projects_are_allowed(upgraded):
    """Chốt phải theo TỪNG dự án. Chặn toàn cục sẽ khiến hai dự án phải xếp hàng
    sau nhau mà không có lý do gì."""
    with upgraded["engine"].begin() as conn:
        _run(conn, project_id=PROJECT_ID)
        _run(conn, project_id=OTHER_PROJECT_ID)

    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM ranking_runs WHERE status='queued'")).scalar_one() == 2


def test_a_finished_run_frees_the_queue_slot(upgraded):
    """Partial unique chỉ áp cho `queued`: run đã kết thúc không chặn run sau."""
    with upgraded["engine"].begin() as conn:
        run_id = _run(conn)
        conn.execute(
            sa.text("UPDATE ranking_runs SET status='completed', started_at=now(), finished_at=now() WHERE id=:i"),
            {"i": run_id},
        )
        _run(conn)

    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM ranking_runs")).scalar_one() == 2


# --- 8/9. Ràng buộc trạng thái của ranking_runs ------------------------------


def test_terminal_status_without_finished_at_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_ranking_runs_finished_by_status"):
        with upgraded["engine"].begin() as conn:
            _run(conn, status="completed", started_at=None, finished_at=None)


def test_queued_status_with_finished_at_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_ranking_runs_finished_by_status"):
        with upgraded["engine"].begin() as conn:
            _run(conn, status="queued", finished_at=sa.text("now()").text)


def test_unknown_trigger_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_ranking_runs_trigger"):
        with upgraded["engine"].begin() as conn:
            _run(conn, trigger="webhook")


def test_scope_type_other_than_project_is_rejected(upgraded):
    """Phạm vi phân khu KHÔNG giữ được `rank_in_project` đúng — cột chỉ nhận 'project'."""
    with pytest.raises(IntegrityError, match="ck_ranking_runs_scope_type"):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_runs (id, project_id, trigger, scope_type, scope_ids, status, "
                    "attempt, units_processed, units_ranked, units_skipped, error_summary, enqueued_at) "
                    "VALUES (:i, :p, 'manual', 'area', '{}'::jsonb, 'queued', 0, 0, 0, 0, '{}'::jsonb, now())"
                ),
                {"i": uuid.uuid4(), "p": PROJECT_ID},
            )


def test_counts_that_exceed_processed_are_rejected(upgraded):
    """Bộ đếm sai là thứ cổng cắt sang sau này sẽ đọc."""
    with pytest.raises(IntegrityError, match="ck_ranking_runs_counts_consistent"):
        with upgraded["engine"].begin() as conn:
            _run(
                conn,
                status="completed",
                started_at=sa.text("now()").text,
                finished_at=sa.text("now()").text,
                units_processed=5,
                units_ranked=4,
                units_skipped=3,
            )


# --- 5/6/7. Ràng buộc của ranking_scores ------------------------------------


def test_duplicate_unit_score_is_rejected(upgraded):
    """`ranking_scores` là TRẠNG THÁI HIỆN TẠI: đúng một dòng cho mỗi căn."""
    with upgraded["engine"].begin() as conn:
        config_id = _config_id(conn)
        run_id = _run(conn)
        _score(conn, run_id, config_id)

    with pytest.raises(IntegrityError, match="uq_ranking_scores_unit"):
        with upgraded["engine"].begin() as conn:
            config_id = _config_id(conn)
            run_id = conn.execute(sa.text("SELECT id FROM ranking_runs LIMIT 1")).scalar_one()
            _score(conn, run_id, config_id, rank_in_area=2, rank_in_project=2)


@pytest.mark.parametrize("bad_score", [1.5, -0.1])
def test_score_out_of_range_is_rejected(upgraded, bad_score):
    with pytest.raises(IntegrityError, match="ck_ranking_scores_score_range"):
        with upgraded["engine"].begin() as conn:
            config_id = _config_id(conn)
            run_id = _run(conn)
            _score(conn, run_id, config_id, score=bad_score)


@pytest.mark.parametrize(
    ("field", "constraint"),
    [
        ("rank_in_area", "ck_ranking_scores_rank_in_area_positive"),
        ("rank_in_project", "ck_ranking_scores_rank_in_project_positive"),
    ],
)
def test_non_positive_rank_is_rejected(upgraded, field, constraint):
    """Thứ hạng đếm từ 1. Hạng 0 nghĩa là bộ tính đã lệch một đơn vị ở đâu đó."""
    with pytest.raises(IntegrityError, match=constraint):
        with upgraded["engine"].begin() as conn:
            config_id = _config_id(conn)
            run_id = _run(conn)
            _score(conn, run_id, config_id, **{field: 0})


def test_weight_coverage_out_of_range_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_ranking_scores_coverage_range"):
        with upgraded["engine"].begin() as conn:
            config_id = _config_id(conn)
            run_id = _run(conn)
            _score(conn, run_id, config_id, weight_coverage=1.2)


# --- 10/11. Khoá ngoại và cascade -------------------------------------------


def test_score_pointing_at_a_missing_run_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="fk_ranking_scores_ranking_run_id"):
        with upgraded["engine"].begin() as conn:
            config_id = _config_id(conn)
            _score(conn, uuid.uuid4(), config_id)


def test_deleting_a_project_cascades_to_ranking_rows(upgraded):
    """Xoá dự án phải mang theo mọi dòng dẫn xuất của nó.

    `units` KHÔNG cascade từ `projects` (khoá ngoại đi qua `areas`, và
    `fk_areas_project_id` không cascade), nên phải dọn `units` và `areas` bằng tay
    trước — đúng như test cascade của 0013 đã phải làm.
    """
    with upgraded["engine"].begin() as conn:
        config_id = _config_id(conn)
        run_id = _run(conn)
        _score(conn, run_id, config_id)
        conn.execute(
            sa.text(
                "INSERT INTO feature_snapshots (id, project_id, feature_key, scope, scope_id, feature_value, "
                "source, feature_version, calculated_at, created_at, updated_at) "
                "VALUES (:i, :p, 'unit_available', 'unit', :s, 1.0, 'operational', 'v1', now(), now(), now())"
            ),
            {"i": uuid.uuid4(), "p": PROJECT_ID, "s": str(UNIT_ID)},
        )

    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM ranking_scores WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM units WHERE area_id = :a"), {"a": AREA_ID})
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM ranking_runs")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM feature_snapshots")).scalar_one() == 0


def test_deleting_a_unit_cascades_to_its_score(upgraded):
    """Một dòng điểm cho một căn không còn tồn tại là dữ liệu vô nghĩa.

    Trên thực tế nhánh này gần như không chạy: `units` bị xoá MỀM (`deleted_at`),
    không xoá cứng.
    """
    with upgraded["engine"].begin() as conn:
        config_id = _config_id(conn)
        run_id = _run(conn)
        _score(conn, run_id, config_id)

    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM units WHERE id = :i"), {"i": UNIT_ID})

    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM ranking_scores")).scalar_one() == 0


def test_sync_run_deletion_sets_null_instead_of_removing_the_run(upgraded):
    """SET NULL, không CASCADE: dọn `upload_files` cũ không được xoá lịch sử xếp hạng."""
    file_id = uuid.uuid4()
    with upgraded["engine"].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO upload_files (id, project_id, filename, checksum, status, rows_ok, rows_failed, "
                "uploaded_at, source_system, source_instance_id, input_format, transport_mode, sync_mode, "
                "schema_version, rows_received, error_summary) "
                "VALUES (:i, :p, 'f.csv', 'sum1', 'completed', 1, 0, now(), 'mini_crm', 'synthetic', 'json', "
                "'api_push', 'incremental', 1, 1, '{}'::jsonb)"
            ),
            {"i": file_id, "p": PROJECT_ID},
        )
        run_id = _run(conn, sync_run_id=file_id)

    with upgraded["engine"].begin() as conn:
        conn.execute(sa.text("DELETE FROM upload_files WHERE id = :i"), {"i": file_id})

    with upgraded["engine"].connect() as conn:
        row = conn.execute(sa.text("SELECT sync_run_id FROM ranking_runs WHERE id = :i"), {"i": run_id}).one()
    assert row[0] is None, "run phải sống sót, chỉ mất liên kết ngược"


# --- 13. Bảng cũ không bị đụng ----------------------------------------------


def test_legacy_tables_are_untouched_by_this_migration(upgraded):
    legacy = ("projects", "areas", "units", "deals", "absorption_daily", "sales_records", "inventory_snapshots")
    query = sa.text(
        "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = ANY(:t) ORDER BY table_name, column_name"
    )

    with upgraded["engine"].connect() as conn:
        before = conn.execute(query, {"t": list(legacy)}).all()
    upgraded["engine"].dispose()

    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            after = conn.execute(query, {"t": list(legacy)}).all()
    finally:
        engine.dispose()

    assert before == after, "0015 hoặc đường lùi của nó đã chạm vào một bảng nghiệp vụ cũ"


# --- 14. Bản chiếu Core khớp schema thật ------------------------------------


def test_core_table_definitions_match_the_migrated_schema(upgraded):
    """`src/models/tables.py` là HÌNH CHIẾU của migration, không phải nguồn sự thật.

    Hai bên lệch nhau thì mã ứng dụng gọi tên cột không tồn tại, và lỗi chỉ nổ lúc
    chạy — thường là ở đường ghi, thường là trên dữ liệu thật. Đối chiếu tên cột
    và tính nullable cho cả bốn bảng mới.
    """
    tables = (feature_snapshots, ranking_configs, ranking_runs, ranking_scores)

    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = ANY(:t)"
            ),
            {"t": [t.name for t in tables]},
        ).all()

    actual: dict[str, dict[str, bool]] = {}
    for table_name, column_name, is_nullable in rows:
        actual.setdefault(table_name, {})[column_name] = is_nullable == "YES"

    for table in tables:
        assert table.name in actual, f"{table.name} không tồn tại trong schema đã migrate"
        expected_columns = {c.name: c.nullable for c in table.columns}
        assert expected_columns.keys() == actual[table.name].keys(), (
            f"{table.name}: tập cột của bản chiếu Core lệch khỏi migration"
        )
        for name, nullable in expected_columns.items():
            assert nullable == actual[table.name][name], (
                f"{table.name}.{name}: bản chiếu Core khai nullable={nullable}, "
                f"schema thật là nullable={actual[table.name][name]}"
            )
