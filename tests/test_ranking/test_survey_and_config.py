"""Hai đường NHẬP do người tạo: đặc trưng khảo sát, và bộ trọng số.

Khác hẳn phần còn lại của bộ xếp hạng — mọi thứ kia đều là kết quả tính toán tất
định, còn hai bảng này nhận dữ liệu mà không mô hình nào kiểm chứng được. Nên
phép kiểm phải nằm ở đường vào, và đó là thứ file này canh.

Kiểu hỏng tệ nhất được canh kỹ nhất, chính là kiểu 0014 đã ghi lại: **một config
trông đầy đủ nhưng cho ra bảng RỖNG.** Nó xảy ra khi config mang một đặc trưng
không ai sản xuất — mọi giá trị MISSING, `skip` loại nó khỏi mẫu số, `coverage`
tụt dưới ngưỡng, MỌI căn bị bỏ qua. Hệ thống chạy sạch, không lỗi, không thứ
hạng. Không có test nào ở đây thì lỗi đó chỉ lộ ra khi ai đó mở màn hình xếp
hạng và thấy trống.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.tables import areas, feature_snapshots, units
from src.services.ranking_config import (
    ConfigError,
    create_draft,
    list_configs,
    publish,
    rollback_to,
    validate_weights,
)
from src.services.survey_features import SurveyError, parse_items, upsert_survey_features
from tests.conftest import db_skip_reason
from tests.test_agent_e2e import AREA_ID, PROJECT_ID, UNIT_IDS, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

OPERATIONAL_V2 = {
    "unit_available": {"weight": 0.35, "direction": "positive", "missing_value_policy": "zero", "min_confidence": 0},
    "unit_demand_norm": {"weight": 0.25, "direction": "positive", "missing_value_policy": "zero", "min_confidence": 0},
    "area_velocity_norm": {
        "weight": 0.20, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0,
    },
    "area_conversion_norm": {
        "weight": 0.20, "direction": "positive", "missing_value_policy": "neutral", "min_confidence": 0,
    },
}


def _with_survey(policy: str, key: str = "view_quality") -> dict:
    """v2 nhưng nhường 0.10 trọng số cho một đặc trưng khảo sát."""
    weights = {k: dict(v) for k, v in OPERATIONAL_V2.items()}
    weights["unit_available"]["weight"] = 0.25
    weights[key] = {"weight": 0.10, "direction": "positive", "missing_value_policy": policy, "min_confidence": 0.6}
    return weights


@pytest_asyncio.fixture
async def db(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    for target in (
        "src.services.ranking_config.get_session_factory",
        "src.services.survey_features.get_session_factory",
    ):
        monkeypatch.setattr(target, lambda f=factory: f, raising=False)
    await _insert_dataset(factory)
    return factory


async def _load_survey(db, key="view_quality", scope="area", scope_id=None, value="0.8", confidence="0.7"):
    items = parse_items(
        [
            {
                "feature_key": key,
                "scope": scope,
                "scope_id": scope_id or str(AREA_ID),
                "value": value,
                "confidence": confidence,
            }
        ]
    )
    return await upsert_survey_features(project_id=PROJECT_ID, items=items)


# ============ VÒNG 3: đặc trưng khảo sát ====================================


def test_operational_feature_keys_are_rejected_at_the_door():
    """`uq_feature_snapshots_identity` chỉ giữ MỘT dòng cho mỗi
    (project, key, scope, scope_id) — ai ghi sau thì thắng. Không chặn ở đây thì
    một lời gọi API ghi đè được `unit_available` bằng số do người nhập, và bảng
    xếp hạng nói dối mà không lỗi nào bật lên."""
    for key in ("unit_available", "area_velocity_norm", "unit_demand_norm"):
        with pytest.raises(SurveyError) as exc:
            parse_items([{"feature_key": key, "scope": "area", "scope_id": str(AREA_ID), "value": 1, "confidence": 1}])
        assert exc.value.code == "FEATURE_NOT_SURVEY"


def test_the_whole_batch_is_validated_before_anything_is_written():
    """Kiểm từng dòng rồi ghi ngay sẽ để lại một lô ghi DỞ khi dòng sau sai — và
    người nhập không có cách nào biết những dòng trước đã vào hay chưa."""
    with pytest.raises(SurveyError) as exc:
        parse_items(
            [
                {"feature_key": "view_quality", "scope": "area", "scope_id": str(AREA_ID), "value": 0.5, "confidence": 0.9},
                {"feature_key": "view_quality", "scope": "area", "scope_id": str(AREA_ID), "value": 9, "confidence": 0.9},
            ]
        )
    assert exc.value.code in ("VALUE_RANGE", "DUPLICATE_IN_BATCH")


def test_duplicate_identity_inside_one_batch_is_rejected(caplog):
    """Hai dòng cùng danh tính: dòng sau đè dòng trước âm thầm, không ai biết
    giá trị nào đã thắng."""
    with pytest.raises(SurveyError) as exc:
        parse_items(
            [
                {"feature_key": "view_quality", "scope": "area", "scope_id": str(AREA_ID), "value": 0.2, "confidence": 0.9},
                {"feature_key": "view_quality", "scope": "area", "scope_id": str(AREA_ID), "value": 0.8, "confidence": 0.9},
            ]
        )
    assert exc.value.code == "DUPLICATE_IN_BATCH"


def test_value_outside_zero_one_is_rejected():
    """§4.2: chuẩn hoá là việc của bộ tổng hợp, TRƯỚC khi gửi. Chuẩn hoá ở tầng
    đọc sẽ khiến hai bộ tổng hợp cho ra hai thang đo trên cùng một cột."""
    with pytest.raises(SurveyError) as exc:
        parse_items([{"feature_key": "privacy", "scope": "area", "scope_id": str(AREA_ID), "value": 42, "confidence": 1}])
    assert exc.value.code == "VALUE_RANGE"


async def test_a_scope_id_outside_the_project_is_rejected(db):
    """`scope_id` là TEXT không có khoá ngoại (0014, đánh đổi có chủ đích). Cái
    giá là tầng ứng dụng phải tự giữ — không giữ thì một lỗi gõ tạo ra dòng đặc
    trưng mà không căn nào đọc tới, im lặng."""
    items = parse_items(
        [{"feature_key": "privacy", "scope": "area", "scope_id": str(uuid.uuid4()), "value": 0.5, "confidence": 0.9}]
    )
    with pytest.raises(SurveyError) as exc:
        await upsert_survey_features(project_id=PROJECT_ID, items=items)
    assert exc.value.code == "AREA_NOT_IN_PROJECT"


async def test_survey_rows_land_with_the_survey_source_marker(db):
    await _load_survey(db)

    async with db() as session:
        row = (
            await session.execute(
                sa.select(feature_snapshots).where(feature_snapshots.c.feature_key == "view_quality")
            )
        ).mappings().first()
    assert row["source"] == "survey_external"
    assert row["confidence"] == Decimal("0.7000")
    assert row["feature_value"] == Decimal("0.8000")


async def test_an_older_snapshot_never_overwrites_a_newer_one(db):
    """Cùng điều kiện upsert với đường vận hành. Một lô cũ gửi lại (retry mạng,
    nạp lại file) không được đè lên số liệu mới hơn."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    fresh = parse_items(
        [{"feature_key": "privacy", "scope": "area", "scope_id": str(AREA_ID), "value": 0.9, "confidence": 0.9}]
    )
    await upsert_survey_features(project_id=PROJECT_ID, items=fresh, calculated_at=now)

    stale = parse_items(
        [{"feature_key": "privacy", "scope": "area", "scope_id": str(AREA_ID), "value": 0.1, "confidence": 0.9}]
    )
    counts = await upsert_survey_features(
        project_id=PROJECT_ID, items=stale, calculated_at=now - timedelta(hours=1)
    )

    assert counts["written"] == 0
    assert counts["skipped_stale"] == 1
    async with db() as session:
        value = await session.scalar(
            sa.select(feature_snapshots.c.feature_value).where(feature_snapshots.c.feature_key == "privacy")
        )
    assert value == Decimal("0.9000"), "giá trị MỚI phải còn nguyên"


async def test_unit_scope_and_unit_type_scope_both_work(db):
    """Ba phạm vi của 0014 đều phải dùng được — `unit_type` là lý do `scope_id`
    không thể là UUID."""
    await _load_survey(db, key="natural_light", scope="unit", scope_id=str(UNIT_IDS["u1"]))
    await _load_survey(db, key="noise_level", scope="unit_type", scope_id="2PN")

    async with db() as session:
        scopes = set(
            (await session.execute(sa.select(feature_snapshots.c.scope).distinct())).scalars().all()
        )
    assert {"unit", "unit_type"} <= scopes


# ============ VÒNG 4: quản trị config =======================================


def test_weights_must_sum_to_one():
    bad = {k: dict(v) for k, v in OPERATIONAL_V2.items()}
    bad["unit_available"]["weight"] = 0.90
    with pytest.raises(ConfigError) as exc:
        validate_weights(bad)
    assert exc.value.code == "WEIGHT_SUM"


def test_an_unknown_feature_key_is_rejected():
    """Khoá không ai tính sẽ luôn MISSING: với `skip` nó kéo coverage xuống, với
    `zero` nó âm thầm cho mọi căn cùng một điểm trên phần trọng số đó. Cả hai đều
    là bảng xếp hạng sai mà không lỗi nào bật lên."""
    bad = {k: dict(v) for k, v in OPERATIONAL_V2.items()}
    bad["unit_available"]["weight"] = 0.25
    bad["price_per_sqm"] = {"weight": 0.10, "direction": "positive", "missing_value_policy": "zero", "min_confidence": 0}
    with pytest.raises(ConfigError) as exc:
        validate_weights(bad)
    assert exc.value.code == "UNKNOWN_FEATURE"


def test_invalid_direction_or_policy_is_rejected():
    bad = {k: dict(v) for k, v in OPERATIONAL_V2.items()}
    bad["unit_available"]["direction"] = "upwards"
    with pytest.raises(ConfigError) as exc:
        validate_weights(bad)
    assert exc.value.code == "DIRECTION_INVALID"


async def test_a_draft_does_not_affect_the_published_config(db):
    await create_draft(weights=OPERATIONAL_V2, min_weight_coverage=0.5, note="nháp", created_by="dat")

    configs = await list_configs()
    assert [c["status"] for c in configs] == ["draft"], "chưa publish thì chưa có gì đang phát hành"


async def test_publishing_archives_the_previous_one_and_keeps_its_timestamp(db):
    """0023 đổi ràng buộc từ đẳng thức sang kéo theo đúng để mốc phát hành gốc
    KHÔNG bị xoá khi lưu trữ. Trước đó, mỗi lần publish là một lần mất dữ liệu
    kiểm toán."""
    first = await create_draft(weights=OPERATIONAL_V2, min_weight_coverage=0.5, note="v1", created_by="dat")
    await publish(version=first["version"], published_by="dat")
    second = await create_draft(weights=OPERATIONAL_V2, min_weight_coverage=0.5, note="v2", created_by="dat")
    await publish(version=second["version"], published_by="dat")

    by_version = {c["version"]: c for c in await list_configs()}
    assert by_version[first["version"]]["status"] == "archived"
    assert by_version[first["version"]]["published_at"] is not None, "mốc phát hành gốc phải còn"
    assert by_version[first["version"]]["archived_at"] is not None
    assert by_version[second["version"]]["status"] == "published"


async def test_only_one_config_is_published_at_a_time(db):
    for _ in range(3):
        draft = await create_draft(weights=OPERATIONAL_V2, min_weight_coverage=0.5, note="x", created_by="dat")
        await publish(version=draft["version"], published_by="dat")

    published = [c for c in await list_configs() if c["status"] == "published"]
    assert len(published) == 1


async def test_publishing_the_same_version_twice_is_rejected(db):
    draft = await create_draft(weights=OPERATIONAL_V2, min_weight_coverage=0.5, note="x", created_by="dat")
    await publish(version=draft["version"], published_by="dat")

    with pytest.raises(ConfigError) as exc:
        await publish(version=draft["version"], published_by="dat")
    assert exc.value.code == "ALREADY_PUBLISHED"


# --- Cái guard quan trọng nhất ----------------------------------------------


async def test_publishing_a_starving_survey_feature_is_refused(db):
    """ĐÚNG kiểu hỏng mà 0014 ghi lại: config trông đầy đủ, hệ thống chạy sạch,
    và bảng xếp hạng RỖNG. Chặn ở lúc publish là chặn ở nơi duy nhất còn kịp."""
    draft = await create_draft(
        weights=_with_survey("skip"), min_weight_coverage=0.5, note="có view_quality", created_by="dat"
    )

    with pytest.raises(ConfigError) as exc:
        await publish(version=draft["version"], published_by="dat")
    assert exc.value.code == "SURVEY_FEATURE_HAS_NO_DATA"


async def test_the_same_config_publishes_once_the_survey_data_exists(db):
    """Guard chặn vì THIẾU DỮ LIỆU, không phải vì ghét đặc trưng khảo sát."""
    draft = await create_draft(
        weights=_with_survey("skip"), min_weight_coverage=0.5, note="có view_quality", created_by="dat"
    )
    await _load_survey(db, key="view_quality")

    row = await publish(version=draft["version"], published_by="dat")
    assert row["status"] == "published"


async def test_a_neutral_policy_survey_feature_publishes_without_data(db):
    """`neutral` không loại căn nào — thiếu dữ liệu chỉ gán 0.5. Guard chỉ áp cho
    `skip`, đúng chỗ có nguy cơ làm rỗng bảng."""
    draft = await create_draft(
        weights=_with_survey("neutral"), min_weight_coverage=0.5, note="neutral", created_by="dat"
    )
    row = await publish(version=draft["version"], published_by="dat")
    assert row["status"] == "published"


async def test_rollback_copies_the_old_weights_into_a_new_version(db):
    """Rollback KHÔNG sửa lịch sử: version cũ giữ nguyên `archived`, dòng mới
    mang `copied_from_version` để truy được nguồn."""
    first = await create_draft(weights=OPERATIONAL_V2, min_weight_coverage=0.5, note="gốc", created_by="dat")
    await publish(version=first["version"], published_by="dat")
    second = await create_draft(weights=_with_survey("neutral"), min_weight_coverage=0.5, note="thử", created_by="dat")
    await publish(version=second["version"], published_by="dat")

    restored = await rollback_to(version=first["version"], created_by="dat")

    assert restored["version"] > second["version"], "rollback tạo version MỚI"
    assert restored["copied_from_version"] == first["version"]
    assert restored["weights"] == OPERATIONAL_V2
    by_version = {c["version"]: c for c in await list_configs()}
    assert by_version[first["version"]]["status"] == "archived", "lịch sử không bị viết lại"


async def test_survey_features_do_not_collide_with_operational_ones(db):
    """Hai writer, một bảng. Đường vận hành ghi `source='operational'`, đường
    khảo sát ghi `source='survey_external'`, và hai tập khoá là rời nhau — nên
    không đường nào đè lên đường kia."""
    from src.ranking.service import run_ranking

    await _load_survey(db, key="view_quality")
    draft = await create_draft(weights=OPERATIONAL_V2, min_weight_coverage=0.5, note="v", created_by="dat")
    await publish(version=draft["version"], published_by="dat")
    # `run_ranking` nhận thẳng `session_factory`; gán đè
    # `ranking_service.get_session_factory` ở phạm vi module sẽ RÒ RỈ sang mọi
    # test chạy sau và gây deadlock ở bước dọn dẹp.
    await run_ranking(PROJECT_ID, session_factory=db)

    async with db() as session:
        rows = (
            await session.execute(sa.select(feature_snapshots.c.feature_key, feature_snapshots.c.source))
        ).mappings().all()
    by_key = {r["feature_key"]: r["source"] for r in rows}
    assert by_key["view_quality"] == "survey_external", "lần chạy vận hành KHÔNG được đụng dòng khảo sát"
    assert by_key["unit_available"] == "operational"


async def test_unit_scope_id_must_belong_to_the_project(db):
    """Căn của dự án khác lọt vào là dữ liệu khảo sát gán nhầm dự án."""
    async with db() as session:
        other_project = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO projects (id, name, launch_date, created_at, updated_at, status, "
                "absorption_calculator) VALUES (:i, 'KHAC', '2026-01-01', now(), now(), 'active', 'legacy_aggregate')"
            ),
            {"i": other_project},
        )
        other_area = uuid.uuid4()
        await session.execute(
            sa.insert(areas).values(
                id=other_area, project_id=other_project, area_name="X", unit_type="2PN", bedrooms=2,
                area_sqm=Decimal("60"), total_units=1, created_at=sa.func.now(),
            )
        )
        other_unit = uuid.uuid4()
        await session.execute(
            sa.insert(units).values(
                id=other_unit, source_system="mini_crm", source_instance_id="test", external_unit_id="x1",
                area_id=other_area, unit_code="x1", unit_type="2PN", status="available",
                created_at=sa.func.now(), updated_at=sa.func.now(),
            )
        )
        await session.commit()

    items = parse_items(
        [{"feature_key": "privacy", "scope": "unit", "scope_id": str(other_unit), "value": 0.5, "confidence": 0.9}]
    )
    with pytest.raises(SurveyError) as exc:
        await upsert_survey_features(project_id=PROJECT_ID, items=items)
    assert exc.value.code == "UNIT_NOT_IN_PROJECT"
