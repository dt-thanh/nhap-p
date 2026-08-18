"""Migration 0012 — nguồn gốc bộ tính, hai lineage sống chung, và đường lùi.

Điểm đáng chú ý nhất của file này là `test_downgrade_with_both_lineages_*`: hạ
cấp khi cả hai lineage đang tồn tại là thao tác PHÁ DỮ LIỆU có chủ đích, và thứ
bị phá phải đúng là phần dựng lại được. Nếu nó lỡ xoá nhầm dòng `legacy_aggregate`
thì dashboard mất sạch số liệu đang phục vụ, nên bất biến đó được kiểm bằng giá
trị từng cột chứ không chỉ bằng số dòng.
"""

from __future__ import annotations

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

PREVIOUS_REVISION = "0011_reconciliation"
REVISION = "0012_calculator_provenance"

LEGACY = "legacy_aggregate"
DOMAIN = "domain_units_deals"

PROJECT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
AREA_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


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
    name = f"mig12_{uuid.uuid4().hex[:12]}_test"
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


def _seed_scope(engine) -> None:
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


def _insert_point(conn, *, stat_date, calculator, units_sold, units_reserved=None, velocity="1.0"):
    conn.execute(
        sa.text(
            "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, velocity_7d, velocity_30d, "
            "data_quality_status, is_observed, computed_at, calculator, units_reserved, computation_id) "
            "VALUES (gen_random_uuid(), :a, :d, :s, :v, :v, 'ok', true, now(), :c, :r, gen_random_uuid())"
        ),
        {"a": AREA_ID, "d": stat_date, "s": units_sold, "v": velocity, "c": calculator, "r": units_reserved},
    )


@pytest.fixture
def upgraded(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    _seed_scope(engine)
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


# --- Tiến / lùi / tiến lại ---------------------------------------------------


def test_upgrade_adds_provenance_columns_and_switch(upgraded):
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


def test_existing_rows_default_to_legacy(scratch_db):
    """Dòng có TRƯỚC migration phải tự nhận nhãn legacy, không cần câu UPDATE nào."""
    _alembic(scratch_db, "upgrade", PREVIOUS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        _seed_scope(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO absorption_daily (id, area_id, stat_date, units_sold, velocity_7d, velocity_30d, "
                    "data_quality_status, is_observed, computed_at) "
                    "VALUES (gen_random_uuid(), :a, '2026-03-01', 3, 1.0, 1.0, 'ok', true, now())"
                ),
                {"a": AREA_ID},
            )

        _alembic(scratch_db, "upgrade", REVISION)

        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT calculator, units_reserved, computation_id FROM absorption_daily")).one()
        assert row[0] == LEGACY
        # Bộ tính cũ không tính được số căn giữ chỗ → NULL, không phải 0.
        assert row[1] is None
        # Dòng có trước migration không thuộc lần chạy nào.
        assert row[2] is None
    finally:
        engine.dispose()


def test_projects_default_to_legacy(upgraded):
    """Phase 6 KHÔNG cắt sang: mọi dự án vẫn đọc bộ tính cũ."""
    with upgraded["engine"].connect() as conn:
        assert conn.execute(sa.text("SELECT absorption_calculator FROM projects")).scalar_one() == LEGACY


def test_downgrade_then_upgrade_round_trip(scratch_db):
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        with engine.connect() as conn:
            cols = set(
                conn.execute(
                    sa.text("SELECT column_name FROM information_schema.columns WHERE table_name='absorption_daily'")
                ).scalars()
            )
            indexes = set(
                conn.execute(sa.text("SELECT indexname FROM pg_indexes WHERE tablename='absorption_daily'")).scalars()
            )
        assert "calculator" not in cols
        assert "uq_absorption_daily_area_id_stat_date" in indexes, "khoá hẹp phải được dựng lại"

        _alembic(scratch_db, "upgrade", REVISION)
        with engine.connect() as conn:
            indexes = set(
                conn.execute(sa.text("SELECT indexname FROM pg_indexes WHERE tablename='absorption_daily'")).scalars()
            )
        assert "uq_absorption_daily_area_date_calculator" in indexes
    finally:
        engine.dispose()


# --- Sống chung --------------------------------------------------------------


def test_both_lineages_may_share_one_area_and_date(upgraded):
    """Đây là thứ khoá hẹp cũ không cho phép, và là điều kiện để chạy song song."""
    with upgraded["engine"].begin() as conn:
        _insert_point(conn, stat_date="2026-03-01", calculator=LEGACY, units_sold=3)
        _insert_point(conn, stat_date="2026-03-01", calculator=DOMAIN, units_sold=9, units_reserved=4)

    with upgraded["engine"].connect() as conn:
        rows = conn.execute(sa.text("SELECT calculator, units_sold FROM absorption_daily ORDER BY calculator")).all()
    assert rows == [(DOMAIN, 9), (LEGACY, 3)]


def test_the_same_lineage_still_cannot_duplicate_a_day(upgraded):
    """Nới khoá KHÔNG được nới lỏng bất biến cũ trong phạm vi một lineage."""
    with upgraded["engine"].begin() as conn:
        _insert_point(conn, stat_date="2026-03-01", calculator=LEGACY, units_sold=3)

    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_point(conn, stat_date="2026-03-01", calculator=LEGACY, units_sold=5)
    assert "uq_absorption_daily_area_date_calculator" in str(exc.value)


def test_unknown_calculator_is_rejected(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_point(conn, stat_date="2026-03-01", calculator="bo-tinh-tuong-tuong", units_sold=1)
    assert "ck_absorption_daily_calculator" in str(exc.value)


def test_unknown_project_calculator_is_rejected(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            conn.execute(sa.text("UPDATE projects SET absorption_calculator = 'khong-co-that'"))
    assert "ck_projects_absorption_calculator" in str(exc.value)


def test_negative_units_reserved_is_rejected(upgraded):
    with pytest.raises(sa.exc.IntegrityError) as exc:
        with upgraded["engine"].begin() as conn:
            _insert_point(conn, stat_date="2026-03-01", calculator=DOMAIN, units_sold=1, units_reserved=-1)
    assert "ck_absorption_daily_units_reserved_nonnegative" in str(exc.value)


# --- Đường lùi khi cả hai lineage đang tồn tại ------------------------------


def test_downgrade_with_both_lineages_removes_only_domain_rows(scratch_db):
    """Hạ cấp phải xoá dòng miền TRƯỚC khi dựng lại khoá hẹp, và CHỈ dòng miền.

    Khoá hẹp `(area_id, stat_date)` không tạo được khi mỗi ngày có hai dòng, nên
    việc xoá là bắt buộc. Thứ bị xoá phải đúng là phần dựng lại được từ
    `units`/`deals`; dòng legacy — thứ dashboard đang phục vụ — phải nguyên vẹn
    tới từng cột.
    """
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        _seed_scope(engine)
        with engine.begin() as conn:
            _insert_point(conn, stat_date="2026-03-01", calculator=LEGACY, units_sold=3, velocity="1.0")
            _insert_point(conn, stat_date="2026-03-02", calculator=LEGACY, units_sold=5, velocity="1.0")
            _insert_point(
                conn, stat_date="2026-03-01", calculator=DOMAIN, units_sold=9, units_reserved=4, velocity="2.0"
            )
            _insert_point(
                conn, stat_date="2026-03-02", calculator=DOMAIN, units_sold=7, units_reserved=2, velocity="2.0"
            )

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.text("SELECT stat_date, units_sold, velocity_30d FROM absorption_daily ORDER BY stat_date")
            ).all()
            narrow = conn.execute(
                sa.text("SELECT count(*) FROM pg_indexes WHERE indexname='uq_absorption_daily_area_id_stat_date'")
            ).scalar_one()

        # Giá trị phải là của LEGACY (3/5, vận tốc 1.0), không phải của miền (9/7, 2.0).
        assert [(r[1], str(r[2])) for r in rows] == [(3, "1.0"), (5, "1.0")]
        assert narrow == 1, "khoá hẹp phải được dựng lại sau khi dọn dòng miền"
    finally:
        engine.dispose()


def test_re_upgrade_after_downgrade_labels_survivors_legacy(scratch_db):
    """Dòng sống sót qua đường lùi là dòng legacy, và phải được gắn lại đúng nhãn."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        _seed_scope(engine)
        with engine.begin() as conn:
            _insert_point(conn, stat_date="2026-03-01", calculator=LEGACY, units_sold=3)
            _insert_point(conn, stat_date="2026-03-01", calculator=DOMAIN, units_sold=9, units_reserved=4)

        _alembic(scratch_db, "downgrade", PREVIOUS_REVISION)
        _alembic(scratch_db, "upgrade", REVISION)

        with engine.connect() as conn:
            rows = conn.execute(sa.text("SELECT calculator, units_sold FROM absorption_daily")).all()
        assert rows == [(LEGACY, 3)]
    finally:
        engine.dispose()
