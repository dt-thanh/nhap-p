"""Migration 0013 — bảng lịch sử so sánh hai bộ tính, và view cổng.

Hai điều được canh kỹ nhất:

1. **Hạ cấp chỉ xoá đúng bảng mới.** Khác 0012 (nơi hạ cấp phải phá dữ liệu có
   chủ đích), 0013 thuần cộng thêm — nên hạ cấp mà làm suy suyển `absorption_daily`
   là dấu hiệu migration đang chạm thứ nó không được chạm.
2. **Ràng buộc CHECK phân biệt "không có dữ liệu" với "bằng không".** Đây là cả
   lý do bảng này có cặp cột `*_has_data`; nếu DB cho phép trạng thái lửng lơ
   (`has_data=false` mà chỉ số vẫn là 0) thì cột cờ trở thành trang trí.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)

PREVIOUS_REVISION = "0012_calculator_provenance"
REVISION = "0013_calculator_comparisons"

PROJECT_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
AREA_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")


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
    name = f"mig13_{uuid.uuid4().hex[:12]}_test"
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
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, 'SYNTH-P', :d, now())"),
            {"i": PROJECT_ID, "d": "2026-01-01"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO areas (id, project_id, area_name, unit_type, bedrooms, area_sqm, total_units, "
                "created_at) VALUES (:i, :p, 'A1', '2PN', 2, 75, 100, now())"
            ),
            {"i": AREA_ID, "p": PROJECT_ID},
        )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _insert(conn, **overrides):
    values = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "trigger": "manual",
        "legacy_units_sold": 1,
        "legacy_units_remaining": 2,
        "domain_units_sold": 1,
        "domain_units_remaining": 2,
        "domain_units_reserved": 0,
        "legacy_has_data": True,
        "domain_has_data": True,
        "matches": True,
        "difference_count": 0,
        "anomaly_count": 0,
        "differences": "[]",
        "anomalies": "[]",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO calculator_comparisons (id, project_id, compared_at, trigger, legacy_units_sold, "
            "legacy_units_remaining, domain_units_sold, domain_units_remaining, domain_units_reserved, "
            "legacy_has_data, domain_has_data, matches, difference_count, anomaly_count, differences, "
            "anomalies, created_at) VALUES (:id, :project_id, now(), :trigger, :legacy_units_sold, "
            ":legacy_units_remaining, :domain_units_sold, :domain_units_remaining, :domain_units_reserved, "
            ":legacy_has_data, :domain_has_data, :matches, :difference_count, :anomaly_count, "
            "CAST(:differences AS jsonb), CAST(:anomalies AS jsonb), now())"
        ),
        values,
    )
    return values["id"]


def _absorption_digest(conn) -> list[tuple]:
    """Toàn bộ nội dung `absorption_daily`, từng cột một.

    Đếm dòng là không đủ: một migration ghi đè giá trị mà giữ nguyên số dòng sẽ
    lọt qua, và đó đúng là kiểu hỏng khó tìm nhất.
    """
    return [
        tuple(row)
        for row in conn.execute(
            sa.text(
                "SELECT area_id, stat_date, units_sold, units_remaining, units_reserved, velocity_7d, "
                "velocity_30d, data_quality_status, is_observed, calculator, computation_id "
                "FROM absorption_daily ORDER BY area_id, stat_date, calculator"
            )
        ).all()
    ]


def _seed_both_lineages(conn):
    for calculator, reserved in (("legacy_aggregate", None), ("domain_units_deals", 3)):
        conn.execute(
            sa.text(
                "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, units_remaining, "
                "units_reserved, velocity_7d, velocity_30d, data_quality_status, is_observed, computed_at, "
                "calculator, computation_id) VALUES (gen_random_uuid(), :a, '2026-03-01', 5, 20, :r, 1.0, 1.0, "
                "'ok', true, now(), :c, gen_random_uuid())"
            ),
            {"a": AREA_ID, "c": calculator, "r": reserved},
        )


# --- Tiến / lùi / tiến lại ---------------------------------------------------


def test_upgrade_creates_the_table_index_and_view(upgraded):
    with upgraded["engine"].connect() as conn:
        assert conn.scalar(sa.text("SELECT to_regclass('calculator_comparisons')")) is not None
        assert conn.scalar(sa.text("SELECT to_regclass('calculator_comparisons_gate')")) is not None
        indexes = set(
            conn.execute(sa.text("SELECT indexname FROM pg_indexes WHERE tablename='calculator_comparisons'")).scalars()
        )
    assert "ix_calculator_comparisons_project_compared" in indexes


def test_upgrade_touches_no_existing_table(upgraded):
    """0013 thuần cộng thêm — không cột nào của bảng cũ được đổi."""
    with upgraded["engine"].connect() as conn:
        absorption_cols = set(
            conn.execute(
                sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='absorption_daily'")
            ).scalars()
        )
        project_cols = set(
            conn.execute(
                sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='projects'")
            ).scalars()
        )

    assert {"calculator", "units_reserved", "computation_id"} <= absorption_cols
    assert "absorption_calculator" in project_cols


def test_downgrade_removes_only_the_new_objects(upgraded):
    with upgraded["engine"].begin() as conn:
        _seed_both_lineages(conn)
        _insert(conn)
    with upgraded["engine"].connect() as conn:
        before = _absorption_digest(conn)

    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)

    with upgraded["engine"].connect() as conn:
        assert conn.scalar(sa.text("SELECT to_regclass('calculator_comparisons')")) is None
        assert conn.scalar(sa.text("SELECT to_regclass('calculator_comparisons_gate')")) is None
        assert _absorption_digest(conn) == before, "hạ cấp 0013 không được chạm absorption_daily"
    assert before, "phải có dữ liệu thật để so, nếu không test này không chứng minh gì"


def test_upgrade_downgrade_upgrade_is_clean(upgraded):
    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    _alembic(upgraded["url"], "upgrade", REVISION)

    with upgraded["engine"].connect() as conn:
        assert conn.scalar(sa.text("SELECT to_regclass('calculator_comparisons')")) is not None


# --- Ràng buộc ---------------------------------------------------------------


def test_matches_cannot_contradict_the_detail_it_summarises(upgraded):
    """`matches` là cột cổng cắt sang sẽ đọc — nó không được nói khác phần chi tiết."""
    with upgraded["engine"].begin() as conn, pytest.raises(Exception) as exc:
        _insert(conn, matches=True, difference_count=2)
    assert "ck_calculator_comparisons_matches_consistent" in str(exc.value)


def test_an_unknown_trigger_is_rejected(upgraded):
    with upgraded["engine"].begin() as conn, pytest.raises(Exception) as exc:
        _insert(conn, trigger="linh-tinh")
    assert "ck_calculator_comparisons_trigger" in str(exc.value)


def test_domain_has_data_false_forbids_domain_metrics(upgraded):
    """Chính là bất biến "không có dữ liệu KHÁC bằng không"."""
    with upgraded["engine"].begin() as conn, pytest.raises(Exception) as exc:
        _insert(conn, domain_has_data=False, domain_units_sold=0, domain_units_remaining=0, domain_units_reserved=0)
    assert "ck_calculator_comparisons_domain_nulls_match_flag" in str(exc.value)


def test_domain_has_data_true_requires_domain_metrics(upgraded):
    with upgraded["engine"].begin() as conn, pytest.raises(Exception) as exc:
        _insert(conn, domain_has_data=True, domain_units_sold=None)
    assert "ck_calculator_comparisons_domain_nulls_match_flag" in str(exc.value)


def test_legacy_flag_has_the_same_rule(upgraded):
    with upgraded["engine"].begin() as conn, pytest.raises(Exception) as exc:
        _insert(conn, legacy_has_data=False, legacy_units_sold=0, legacy_units_remaining=0)
    assert "ck_calculator_comparisons_legacy_nulls_match_flag" in str(exc.value)


def test_a_row_with_no_data_on_either_side_is_storable(upgraded):
    """Trạng thái "chưa có gì để so" phải GHI ĐƯỢC — nó là quan sát có giá trị,
    chỉ là không được tính vào cổng."""
    with upgraded["engine"].begin() as conn:
        _insert(
            conn,
            legacy_has_data=False,
            legacy_units_sold=None,
            legacy_units_remaining=None,
            domain_has_data=False,
            domain_units_sold=None,
            domain_units_remaining=None,
            domain_units_reserved=None,
        )
    with upgraded["engine"].connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM calculator_comparisons")) == 1


def test_negative_counts_are_rejected(upgraded):
    with upgraded["engine"].begin() as conn, pytest.raises(Exception) as exc:
        _insert(conn, difference_count=-1, matches=False)
    assert "ck_calculator_comparisons_counts_non_negative" in str(exc.value)


def test_deleting_a_project_cascades(upgraded):
    """Quan sát dẫn xuất: dự án không còn thì chúng không mô tả gì nữa. Khác
    `sync_payloads` (RESTRICT), nơi payload thô là bằng chứng phải giữ."""
    with upgraded["engine"].begin() as conn:
        _insert(conn)
        # `fk_areas_project_id` KHÔNG cascade, nên phải dọn phân khu trước. Điều
        # đó không liên quan tới 0013 — nó chỉ cho thấy dự án không bị xoá dễ dãi.
        conn.execute(sa.text("DELETE FROM areas WHERE project_id = :p"), {"p": PROJECT_ID})
        conn.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})
    with upgraded["engine"].connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM calculator_comparisons")) == 0


# --- View cổng ----------------------------------------------------------------


def test_the_gate_view_hides_rows_without_domain_data(upgraded):
    """Yêu cầu trung tâm của 8D: dòng rỗng KHÔNG BAO GIỜ được đếm vào cổng 14 ngày.

    Việc loại trừ nằm ở database, không nằm trong đầu người viết truy vấn sau này.
    """
    with upgraded["engine"].begin() as conn:
        kept = _insert(conn)
        _insert(
            conn,
            domain_has_data=False,
            domain_units_sold=None,
            domain_units_remaining=None,
            domain_units_reserved=None,
        )

    with upgraded["engine"].connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM calculator_comparisons")) == 2
        visible = conn.execute(sa.text("SELECT id FROM calculator_comparisons_gate")).scalars().all()

    assert visible == [kept]


def test_the_gate_view_keeps_every_column(upgraded):
    with upgraded["engine"].connect() as conn:
        table_cols = set(
            conn.execute(
                sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='calculator_comparisons'")
            ).scalars()
        )
        view_cols = set(
            conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='calculator_comparisons_gate'"
                )
            ).scalars()
        )
    assert table_cols == view_cols


def test_history_is_append_only_at_the_storage_level(upgraded):
    """Không có ràng buộc UNIQUE nào trên `project_id`: nhiều lần đo cho cùng một
    dự án phải cùng tồn tại, nếu không thì không có lịch sử nào để đọc."""
    with upgraded["engine"].begin() as conn:
        _insert(conn)
        _insert(conn)
        _insert(conn, differences=json.dumps([{"metric": "units_sold"}]), difference_count=1, matches=False)

    with upgraded["engine"].connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM calculator_comparisons")) == 3
