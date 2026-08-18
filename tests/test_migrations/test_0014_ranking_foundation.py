"""Migration 0014 — feature_snapshots, ranking_configs, và config v1 vận hành.

Bốn thứ được canh kỹ nhất:

1. **Khoá danh tính đặc trưng có `project_id`.** Phạm vi `unit_type` chỉ là một
   chuỗi; thiếu `project_id` thì `view_quality` của loại '2PN' ở hai dự án khác
   nhau là CÙNG một dòng. Test 9 chốt điều ngược lại.
2. **Đúng MỘT config `published`.** Hai config cùng phát hành nghĩa là không ai
   biết bộ tính đang đọc bộ trọng số nào.
3. **Trạng thái và mốc thời gian không mâu thuẫn.** `published` mà thiếu
   `published_at` là một dòng không truy được thời điểm.
4. **Config v1 CHỈ có đặc trưng vận hành, tổng trọng số 1.0.** Nhét đặc trưng khảo
   sát vào v1 sẽ khiến coverage tụt dưới ngưỡng và MỌI căn bị bỏ qua — hệ thống
   chạy sạch mà không sinh ra thứ hạng nào.

Chạy trên DATABASE DÙNG MỘT LẦN (`mig14_<hex>_test`), tạo và huỷ trong từng test.
Không đụng tới database dev, cũng không đụng tới database test dùng chung.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DataError, IntegrityError

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật",
)

PREVIOUS_REVISION = "0013_calculator_comparisons"
REVISION = "0014_ranking_foundation"

PROJECT_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
OTHER_PROJECT_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")

# Bốn đặc trưng vận hành của config v1 — phải khớp SEED_WEIGHTS của migration.
OPERATIONAL_FEATURES = {"unit_available", "has_active_deal", "area_velocity_norm", "area_conversion_norm"}
# Những thứ TUYỆT ĐỐI không được có mặt ở v1.
FORBIDDEN_IN_V1 = {"view_quality", "natural_light", "privacy", "noise_level", "days_on_market", "price"}


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
    name = f"mig14_{uuid.uuid4().hex[:12]}_test"
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
    """Database đã lên 0014, kèm hai dự án để kiểm cô lập theo dự án."""
    _alembic(scratch_db, "upgrade", REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.begin() as conn:
        for pid, name in ((PROJECT_ID, "SYNTH-P1"), (OTHER_PROJECT_ID, "SYNTH-P2")):
            conn.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:i, :n, :d, now())"),
                {"i": pid, "n": name, "d": "2026-01-01"},
            )
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _snapshot(conn, **overrides):
    values = {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "feature_key": "view_quality",
        "scope": "unit",
        "scope_id": str(uuid.uuid4()),
        "feature_value": 0.8,
        "sample_count": 12,
        "confidence": 0.75,
        "source": "survey_external",
        "feature_version": "v1",
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO feature_snapshots (id, project_id, feature_key, scope, scope_id, feature_value, "
            "sample_count, confidence, source, feature_version, calculated_at, created_at, updated_at) "
            "VALUES (:id, :project_id, :feature_key, :scope, :scope_id, :feature_value, :sample_count, "
            ":confidence, :source, :feature_version, now(), now(), now())"
        ),
        values,
    )
    return values


def _config(conn, **overrides):
    values = {
        "id": uuid.uuid4(),
        "version": 2,
        "status": "draft",
        "weights": '{"unit_available": {"weight": 1.0}}',
        "created_by": "test",
        "published_at": None,
        "archived_at": None,
        **overrides,
    }
    conn.execute(
        sa.text(
            "INSERT INTO ranking_configs (id, version, status, weights, min_weight_coverage, note, "
            "created_by, created_at, published_at, archived_at) "
            "VALUES (:id, :version, :status, CAST(:weights AS jsonb), 0.5, '', :created_by, now(), "
            ":published_at, :archived_at)"
        ),
        values,
    )
    return values


# --- 1/2. Lên và xuống -------------------------------------------------------


def test_upgrade_creates_both_tables(upgraded):
    with upgraded["engine"].connect() as conn:
        found = set(
            conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('feature_snapshots', 'ranking_configs')"
                )
            ).scalars()
        )
    assert found == {"feature_snapshots", "ranking_configs"}


def test_downgrade_removes_both_tables(upgraded):
    upgraded["engine"].dispose()
    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)

    engine = sa.create_engine(_sync_url(upgraded["url"]))
    try:
        with engine.connect() as conn:
            found = list(
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

    assert found == []
    assert revision == PREVIOUS_REVISION


# --- 3..8. Ràng buộc của feature_snapshots ----------------------------------


@pytest.mark.parametrize("bad_value", [1.5, 2.0])
def test_feature_value_above_one_is_rejected(upgraded, bad_value):
    """Mọi đặc trưng đã chuẩn hoá về [0,1] TRƯỚC khi vào bảng."""
    with pytest.raises(IntegrityError, match="ck_feature_snapshots_value_range"):
        with upgraded["engine"].begin() as conn:
            _snapshot(conn, feature_value=bad_value)


def test_feature_value_below_zero_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_feature_snapshots_value_range"):
        with upgraded["engine"].begin() as conn:
            _snapshot(conn, feature_value=-0.1)


def test_unknown_scope_is_rejected(upgraded):
    """Phạm vi lạ nghĩa là tầng giải kế thừa sẽ không bao giờ tìm thấy dòng này."""
    with pytest.raises(IntegrityError, match="ck_feature_snapshots_scope"):
        with upgraded["engine"].begin() as conn:
            _snapshot(conn, scope="building")


def test_unknown_source_is_rejected(upgraded):
    """`operational` và `survey_external` là hai đường vào duy nhất."""
    with pytest.raises(IntegrityError, match="ck_feature_snapshots_source"):
        with upgraded["engine"].begin() as conn:
            _snapshot(conn, source="guessed")


def test_confidence_out_of_range_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_feature_snapshots_confidence_range"):
        with upgraded["engine"].begin() as conn:
            _snapshot(conn, confidence=1.4)


def test_duplicate_feature_identity_is_rejected(upgraded):
    """Khoá upsert: một đặc trưng cho một phạm vi chỉ có MỘT giá trị hiện hành."""
    scope_id = str(uuid.uuid4())
    with upgraded["engine"].begin() as conn:
        _snapshot(conn, scope_id=scope_id)

    with pytest.raises(IntegrityError, match="uq_feature_snapshots_identity"):
        with upgraded["engine"].begin() as conn:
            _snapshot(conn, scope_id=scope_id, feature_value=0.2)


# --- 9. Cô lập theo dự án ----------------------------------------------------


def test_same_feature_identity_in_two_projects_is_allowed(upgraded):
    """Đây là LÝ DO `project_id` nằm trong khoá danh tính.

    Phạm vi `unit_type` mang một CHUỖI ('2PN'), không phải UUID. Thiếu `project_id`
    thì đặc trưng của loại 2PN ở dự án A ghi đè lên chính nó ở dự án B — hai dự án
    khác nhau dùng chung một dòng, và không ai phát hiện ra.
    """
    with upgraded["engine"].begin() as conn:
        _snapshot(conn, project_id=PROJECT_ID, scope="unit_type", scope_id="2PN", feature_value=0.9)
        _snapshot(conn, project_id=OTHER_PROJECT_ID, scope="unit_type", scope_id="2PN", feature_value=0.1)

    with upgraded["engine"].connect() as conn:
        rows = dict(
            conn.execute(
                sa.text(
                    "SELECT project_id, feature_value FROM feature_snapshots "
                    "WHERE scope = 'unit_type' AND scope_id = '2PN'"
                )
            ).all()
        )
    assert len(rows) == 2
    assert float(rows[PROJECT_ID]) == 0.9
    assert float(rows[OTHER_PROJECT_ID]) == 0.1


# --- 10/11. Ràng buộc của ranking_configs -----------------------------------


def test_second_published_config_is_rejected(upgraded):
    """Migration đã seed v1 `published`; một v2 `published` nữa là hai nguồn sự thật."""
    with pytest.raises(IntegrityError, match="uq_ranking_configs_published"):
        with upgraded["engine"].begin() as conn:
            _config(conn, version=2, status="published", published_at=sa.text("now()").text)


def test_published_config_without_published_at_is_rejected(upgraded):
    with pytest.raises(IntegrityError, match="ck_ranking_configs_published_stamp"):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_configs (id, version, status, weights, min_weight_coverage, note, "
                    "created_by, created_at) VALUES (:i, 2, 'published', '{\"a\": 1}'::jsonb, 0.5, '', 'test', now())"
                ),
                {"i": uuid.uuid4()},
            )


def test_empty_weights_are_rejected(upgraded):
    """Một config không có trọng số nào không xếp hạng được gì."""
    with pytest.raises(IntegrityError, match="ck_ranking_configs_weights_not_empty"):
        with upgraded["engine"].begin() as conn:
            _config(conn, weights="{}")


def test_coverage_out_of_range_is_rejected(upgraded):
    with pytest.raises((IntegrityError, DataError)):
        with upgraded["engine"].begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_configs (id, version, status, weights, min_weight_coverage, note, "
                    "created_by, created_at) VALUES (:i, 3, 'draft', '{\"a\": 1}'::jsonb, 0, '', 'test', now())"
                ),
                {"i": uuid.uuid4()},
            )


# --- 12/13. Config v1 do migration seed --------------------------------------


def test_seed_creates_exactly_one_published_config(upgraded):
    with upgraded["engine"].connect() as conn:
        rows = conn.execute(
            sa.text("SELECT version, status, created_by, published_by FROM ranking_configs WHERE status='published'")
        ).all()
    assert len(rows) == 1
    version, status, created_by, published_by = rows[0]
    assert (version, status) == (1, "published")
    assert created_by == "migration_0014"
    assert published_by == "migration_0014"


def test_seed_weights_are_operational_only_and_sum_to_one(upgraded):
    """Nếu v1 mang đặc trưng khảo sát, mọi giá trị sẽ MISSING, coverage tụt dưới
    ngưỡng, và MỌI căn bị bỏ qua — hệ thống chạy sạch mà không sinh thứ hạng nào."""
    with upgraded["engine"].connect() as conn:
        weights = conn.execute(sa.text("SELECT weights FROM ranking_configs WHERE version = 1")).scalar_one()

    assert set(weights) == OPERATIONAL_FEATURES
    assert not (set(weights) & FORBIDDEN_IN_V1), "v1 không được mang đặc trưng khảo sát hay đặc trưng bị chặn"

    total = sum(entry["weight"] for entry in weights.values())
    assert abs(total - 1.0) < 1e-9, f"tổng trọng số phải bằng 1.0, đang là {total}"

    for key, entry in weights.items():
        assert entry["direction"] in ("positive", "negative"), key
        assert entry["missing_value_policy"] in ("skip", "zero", "neutral"), key
        assert 0.0 <= entry["min_confidence"] <= 1.0, key


# --- 14. Bảng cũ không bị đụng ----------------------------------------------


def test_legacy_tables_are_untouched_by_this_migration(upgraded):
    """0014 thuần cộng thêm: không cột nào của bảng nghiệp vụ cũ được sửa.

    So cấu trúc TRƯỚC và SAU khi hạ cấp — nếu migration lỡ chạm vào một bảng cũ,
    đường lùi sẽ để lại vết ở đây.
    """
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

    assert before == after, "0014 hoặc đường lùi của nó đã chạm vào một bảng nghiệp vụ cũ"
