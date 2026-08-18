"""Migration 0022 — lưu trữ `ranking_configs` v1, phát hành v2.

Bốn thứ được canh:

1. **Đúng MỘT config `published`, và đó là v2.** Partial unique index
   `uq_ranking_configs_published` (0014) chỉ chặn được hai dòng `published`;
   nó KHÔNG chặn được trường hợp migration quên phát hành v2 sau khi đã lưu trữ
   v1 — lúc đó `_active_config` ném `NO_ACTIVE_CONFIG` và toàn bộ bộ xếp hạng
   chết lặng. Đó là kiểu hỏng test này tồn tại để bắt.
2. **v1 KHÔNG bị xoá.** `ranking_configs` là bảng CHỈ-THÊM: mọi dòng
   `ranking_scores` cũ trỏ vào `config_version_id` của v1, xoá nó là làm mọi lần
   chạy cũ không giải thích lại được.
3. **Bộ trọng số v2 đúng bất biến của 0014**: tổng bằng 1.0, không mang đặc
   trưng khảo sát/bị chặn, và KHÔNG còn `has_active_deal` (lý do: docstring của
   revision — tương quan -1.0 với `unit_available`).
4. **Downgrade đối xứng**: v2 biến mất, v1 trở lại `published`.

Chạy trên DATABASE DÙNG MỘT LẦN (`mig22_<hex>_test`), tạo và huỷ trong từng test.
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

PREVIOUS_REVISION = "0021_seed_ai_crm_fixture_deals"
REVISION = "0022_ranking_config_v2"

V2_EXPECTED_WEIGHTS = {
    "unit_available": 0.35,
    "unit_demand_norm": 0.25,
    "area_velocity_norm": 0.20,
    "area_conversion_norm": 0.20,
}

# Không đặc trưng nào trong nhóm này được phép có mặt: chưa có bộ tổng hợp nào
# sản xuất chúng, nên mọi giá trị sẽ MISSING (`skip`), coverage tụt dưới ngưỡng,
# và MỌI căn bị bỏ qua — đúng cảnh báo mà 0014 đã ghi cho v1.
FORBIDDEN = {"view_quality", "natural_light", "privacy", "noise_level", "days_on_market", "price"}


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} thất bại:\n{result.stdout}\n{result.stderr}"
    return result.stdout


@pytest.fixture
def scratch_db():
    name = f"mig22_{uuid.uuid4().hex[:12]}_test"
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
    try:
        yield {"engine": engine, "url": scratch_db}
    finally:
        engine.dispose()


def _configs(engine) -> dict[int, dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT version, status, weights, min_weight_coverage, "
                "       published_at IS NOT NULL AS has_published_at, "
                "       archived_at IS NOT NULL AS has_archived_at, note "
                "FROM ranking_configs"
            )
        ).mappings().all()
    return {r["version"]: dict(r) for r in rows}


# --- 1. Đúng một config published, và đó là v2 -------------------------------


def test_exactly_one_published_config_and_it_is_v2(upgraded):
    """Lưu trữ v1 mà quên phát hành v2 sẽ không vi phạm ràng buộc nào — nó chỉ
    làm `_active_config` ném NO_ACTIVE_CONFIG và bộ xếp hạng chết lặng."""
    configs = _configs(upgraded["engine"])
    published = [v for v, r in configs.items() if r["status"] == "published"]
    assert published == [2], f"phải có ĐÚNG v2 đang phát hành, thấy {published!r}"


def test_v1_is_archived_not_deleted(upgraded):
    """`ranking_configs` là CHỈ-THÊM: `ranking_scores` cũ trỏ vào v1."""
    configs = _configs(upgraded["engine"])
    assert 1 in configs, "v1 đã bị XOÁ — mọi ranking_scores cũ mất đường giải thích"
    assert configs[1]["status"] == "archived"
    assert configs[1]["has_archived_at"] is True


def test_archiving_v1_preserves_its_original_publish_time_in_the_note(upgraded):
    """`ck_ranking_configs_published_stamp` là ĐẲNG THỨC, nên lưu trữ BẮT BUỘC
    xoá `published_at`. Mốc gốc được chép sang `note` để không biến mất — xem
    docstring revision, mục MẤT MÁT ĐÃ BIẾT."""
    v1 = _configs(upgraded["engine"])[1]
    assert v1["has_published_at"] is False, "ràng buộc đẳng thức buộc published_at về NULL"
    assert "Lưu trữ bởi 0022" in v1["note"]
    assert "phát hành gốc lúc" in v1["note"]


# --- 2. Bộ trọng số v2 -------------------------------------------------------


def test_v2_weights_match_the_documented_formula(upgraded):
    weights = _configs(upgraded["engine"])[2]["weights"]
    assert {k: entry["weight"] for k, entry in weights.items()} == V2_EXPECTED_WEIGHTS


def test_v2_weights_sum_to_one(upgraded):
    """Cùng bất biến mà 0014 đặt cho v1: `coverage` được so với
    `min_weight_coverage` như một phân số của TỔNG trọng số."""
    weights = _configs(upgraded["engine"])[2]["weights"]
    total = sum(entry["weight"] for entry in weights.values())
    assert abs(total - 1.0) < 1e-9, f"tổng trọng số phải bằng 1.0, đang là {total}"


def test_v2_drops_has_active_deal(upgraded):
    """Tương quan ĐÚNG -1.0 với `unit_available` (bất biến do 0021 cưỡng chế):
    giữ lại nó là chi 20% ngân sách trọng số cho một bản sao."""
    weights = _configs(upgraded["engine"])[2]["weights"]
    assert "has_active_deal" not in weights


def test_v2_carries_no_survey_or_blocked_feature(upgraded):
    weights = _configs(upgraded["engine"])[2]["weights"]
    assert not (set(weights) & FORBIDDEN), "đặc trưng khảo sát/bị chặn sẽ làm MỌI căn bị bỏ qua"


def test_v2_every_entry_is_structurally_valid(upgraded):
    weights = _configs(upgraded["engine"])[2]["weights"]
    for key, entry in weights.items():
        assert entry["direction"] in ("positive", "negative"), key
        assert entry["missing_value_policy"] in ("skip", "zero", "neutral"), key
        assert 0.0 <= entry["min_confidence"] <= 1.0, key


def test_no_v2_feature_uses_skip_so_no_unit_can_fall_below_coverage(upgraded):
    """Mọi đặc trưng v2 dùng `zero` hoặc `neutral`, nên trọng số LUÔN vào mẫu số
    và `coverage` luôn bằng 1.0 — không căn nào bị bỏ qua vì thiếu dữ liệu.
    Nếu ai đó đổi một đặc trưng sang `skip`, `min_weight_coverage = 0.5` sẽ bắt
    đầu loại căn một cách âm thầm."""
    config = _configs(upgraded["engine"])[2]
    assert all(e["missing_value_policy"] != "skip" for e in config["weights"].values())
    assert float(config["min_weight_coverage"]) == 0.5


# --- 3. Downgrade ------------------------------------------------------------


def test_downgrade_restores_v1_as_the_published_config(upgraded):
    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    configs = _configs(upgraded["engine"])
    assert 2 not in configs, "v2 phải biến mất hoàn toàn"
    assert configs[1]["status"] == "published"
    assert configs[1]["has_published_at"] is True
    assert configs[1]["has_archived_at"] is False


def test_upgrade_is_repeatable_after_a_downgrade(upgraded):
    """Vòng lùi-rồi-tiến không được nhân bản v2 (`uq_ranking_configs_version`
    sẽ chặn, nhưng chặn bằng lỗi migration thì đã muộn cho môi trường thật)."""
    _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    _alembic(upgraded["url"], "upgrade", REVISION)
    configs = _configs(upgraded["engine"])
    assert [v for v, r in configs.items() if r["status"] == "published"] == [2]
    assert configs[1]["status"] == "archived"


# --- 4. Migration TỪ CHỐI chạy khi trạng thái đầu vào không như mong đợi ------


def test_upgrade_refuses_when_a_config_other_than_v1_is_published(scratch_db):
    """Ai đó đã đổi trọng số bằng đường khác. Lưu trữ nó ở đây là xoá một quyết
    định không phải của revision này — migration phải DỪNG, không đoán."""
    _alembic(scratch_db, "upgrade", PREVIOUS_REVISION)
    engine = sa.create_engine(_sync_url(scratch_db))
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE ranking_configs SET status='archived', archived_at=now(), published_at=NULL"))
        conn.execute(
            sa.text(
                "INSERT INTO ranking_configs (id, version, status, weights, min_weight_coverage, note, "
                " created_by, created_at, published_by, published_at) "
                "VALUES (gen_random_uuid(), 99, 'published', '{\"unit_available\": {\"weight\": 1.0}}'::jsonb, "
                "        0.5, 'hand-published', 'a_human', now(), 'a_human', now())"
            )
        )
    engine.dispose()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", REVISION],
        env={**os.environ, "DATABASE_URL": scratch_db},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "migration phải TỪ CHỐI chạy, không im lặng ghi đè"
    assert "0022_ranking_config_v2" in result.stderr
