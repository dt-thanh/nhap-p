"""Quản trị `ranking_configs`: soạn bản nháp, phát hành, lưu trữ.

Trước đợt này đổi trọng số CHỈ đi qua migration (0014 rồi 0022). Đúng cho hai
lần đầu — bộ trọng số khởi tạo là một quyết định kỹ thuật. Nhưng hiệu chỉnh
trọng số theo mùa bán hàng là quyết định NGHIỆP VỤ, và bắt nó phải qua một lần
deploy nghĩa là nó sẽ không bao giờ được làm.

╔══════════════════════════════════════════════════════════════════════════════╗
║  Đây là nơi ghi DUY NHẤT vào `ranking_configs`.                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

`tests/test_ranking_boundary.py` khai báo điều đó theo BẢNG. `ranking_scores` và
`ranking_runs` vẫn chỉ có `src/ranking/service.py` ghi — hai bảng đó chứa kết quả
mô hình, còn bảng này chứa đầu vào do người soạn.

**CHỈ-THÊM, không sửa tại chỗ.** `ranking_scores.config_version_id` là khoá ngoại
trỏ vào đây. Sửa `weights` của một dòng đã `published` sẽ khiến mọi điểm cũ trỏ
tới một config đã đổi nghĩa, và không lần chạy nào giải thích lại được. Rollback
vì thế là CHÉP trọng số cũ sang một version MỚI (`copied_from_version`), không
phải sửa lịch sử.

**Vì sao validate chặt trước khi publish.** 0014 đã ghi lại kiểu hỏng tệ nhất ở
đây: một config trông đầy đủ nhưng cho ra bảng RỖNG. Nó xảy ra khi config mang
một đặc trưng mà không bộ tính nào sản xuất — mọi giá trị MISSING, chính sách
`skip` loại nó khỏi mẫu số, `coverage` tụt dưới `min_weight_coverage`, và MỌI căn
bị bỏ qua. Hệ thống chạy sạch, không lỗi, không thứ hạng nào. Bốn phép kiểm dưới
đây tồn tại để lỗi đó không bao giờ tới được trạng thái `published`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import feature_snapshots, ranking_configs

log = get_logger("src.services.ranking_config")

DIRECTIONS = ("positive", "negative")
MISSING_POLICIES = ("skip", "zero", "neutral")

# Đặc trưng VẬN HÀNH — `src/ranking/service.py` luôn tính được, không bao giờ MISSING.
OPERATIONAL_FEATURES = frozenset(
    {"unit_available", "has_active_deal", "unit_demand_norm", "area_velocity_norm", "area_conversion_norm"}
)

# Đặc trưng KHẢO SÁT — chỉ có giá trị khi bộ tổng hợp ngoài đã nạp qua
# `src/services/survey_features.py`. Xem `SURVEY_FEATURES` ở module đó.
SURVEY_FEATURES = frozenset({"view_quality", "natural_light", "privacy", "noise_level"})

KNOWN_FEATURES = OPERATIONAL_FEATURES | SURVEY_FEATURES

# Sai số cho phép khi cộng trọng số. Trọng số đi vào JSON dưới dạng float, nên
# 0.35 + 0.25 + 0.20 + 0.20 không cho ra đúng 1.0 ở nhị phân.
WEIGHT_SUM_TOLERANCE = 1e-9


class ConfigError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_weights(weights: dict) -> None:
    """Bốn phép kiểm, tất cả đều chặn kiểu hỏng "chạy sạch mà không có thứ hạng"."""
    if not isinstance(weights, dict) or not weights:
        raise ConfigError("WEIGHTS_EMPTY", "weights không được rỗng")

    total = 0.0
    for key, spec in weights.items():
        if not isinstance(spec, dict):
            raise ConfigError("WEIGHT_SPEC_INVALID", f"'{key}' phải là một object")
        if key not in KNOWN_FEATURES:
            # KHÔNG nới thành cảnh báo. Một khoá không ai tính sẽ luôn MISSING;
            # với `skip` nó kéo coverage xuống, với `zero` nó âm thầm cho mọi căn
            # cùng một điểm 0 trên phần trọng số đó — cả hai đều là bảng xếp hạng
            # sai mà không có lỗi nào bật lên.
            raise ConfigError(
                "UNKNOWN_FEATURE",
                f"'{key}' không phải đặc trưng nào hệ thống tính được. Đã biết: {sorted(KNOWN_FEATURES)}",
            )
        try:
            weight = float(spec["weight"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError("WEIGHT_INVALID", f"'{key}'.weight phải là số") from exc
        if weight < 0:
            raise ConfigError("WEIGHT_NEGATIVE", f"'{key}'.weight không được âm")
        total += weight

        if spec.get("direction") not in DIRECTIONS:
            raise ConfigError("DIRECTION_INVALID", f"'{key}'.direction phải thuộc {DIRECTIONS}")
        if spec.get("missing_value_policy") not in MISSING_POLICIES:
            raise ConfigError(
                "MISSING_POLICY_INVALID", f"'{key}'.missing_value_policy phải thuộc {MISSING_POLICIES}"
            )
        confidence = float(spec.get("min_confidence", 0) or 0)
        if not 0.0 <= confidence <= 1.0:
            raise ConfigError("MIN_CONFIDENCE_RANGE", f"'{key}'.min_confidence phải trong [0, 1]")

    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ConfigError("WEIGHT_SUM", f"tổng trọng số phải bằng 1.0, đang là {total}")


async def _survey_features_with_data(session, feature_keys: set[str]) -> set[str]:
    if not feature_keys:
        return set()
    rows = await session.execute(
        sa.select(feature_snapshots.c.feature_key)
        .where(
            feature_snapshots.c.feature_key.in_(feature_keys),
            feature_snapshots.c.source == "survey_external",
        )
        .distinct()
    )
    return set(rows.scalars().all())


async def list_configs() -> list[dict]:
    async with get_session_factory()() as session:
        rows = await session.execute(sa.select(ranking_configs).order_by(ranking_configs.c.version.desc()))
        out = [dict(row) for row in rows.mappings().all()]
        # ĐÓNG transaction tường minh. Một SELECT cũng mở transaction; thoát
        # context mà không kết thúc nó sẽ trả connection về pool ở trạng thái
        # "idle in transaction", và nó giữ khoá chia sẻ trên `ranking_configs`.
        # Hệ quả thật, không phải chuyện của riêng test: một connection như vậy
        # chặn VACUUM và chặn mọi DDL trên bảng đó cho tới khi pool tái sử dụng
        # nó. (Lộ ra ở đây vì bước dọn dẹp của test chạy TRUNCATE ngay sau và
        # deadlock.)
        await session.rollback()
        return out


async def create_draft(
    *,
    weights: dict,
    min_weight_coverage: float,
    note: str,
    created_by: str,
    copied_from_version: int | None = None,
) -> dict:
    """Soạn một version MỚI ở trạng thái `draft`. Chưa ảnh hưởng lần chạy nào."""
    validate_weights(weights)
    if not 0 < float(min_weight_coverage) <= 1:
        raise ConfigError("COVERAGE_RANGE", "min_weight_coverage phải trong (0, 1]")
    if not created_by.strip():
        raise ConfigError("CREATED_BY_REQUIRED", "created_by không được rỗng")

    async with get_session_factory()() as session:
        highest = await session.scalar(sa.select(sa.func.max(ranking_configs.c.version)))
        version = int(highest or 0) + 1
        config_id = uuid.uuid4()
        await session.execute(
            sa.insert(ranking_configs).values(
                id=config_id,
                version=version,
                status="draft",
                weights=weights,
                min_weight_coverage=Decimal(str(min_weight_coverage)),
                note=note,
                copied_from_version=copied_from_version,
                created_by=created_by.strip(),
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        row = (
            await session.execute(sa.select(ranking_configs).where(ranking_configs.c.id == config_id))
        ).mappings().first()
        await session.rollback()  # đóng transaction ĐỌC mở ra bởi SELECT sau commit

    log.info("ranking.config.draft_created", version=version, created_by=created_by)
    return dict(row)


async def publish(*, version: int, published_by: str) -> dict:
    """Lưu trữ config đang phát hành rồi phát hành `version`.

    Thứ tự BẮT BUỘC là lưu trữ trước: `uq_ranking_configs_published` là partial
    unique index, kiểm ngay ở từng câu lệnh chứ không hoãn tới cuối transaction.

    Người gọi phải tự xếp hàng tính lại sau đó (`trigger_ranking_all_projects`).
    Cố ý KHÔNG gọi ở đây: module này không được biết gì về Redis/RQ, cùng lý do
    `src/ranking/service.py` không biết.
    """
    if not published_by.strip():
        raise ConfigError("PUBLISHED_BY_REQUIRED", "published_by không được rỗng")

    async with get_session_factory()() as session:
        target = (
            await session.execute(sa.select(ranking_configs).where(ranking_configs.c.version == version))
        ).mappings().first()
        if target is None:
            raise ConfigError("CONFIG_NOT_FOUND", f"Không có config version {version}")
        if target["status"] == "published":
            raise ConfigError("ALREADY_PUBLISHED", f"Config v{version} đang được phát hành")

        validate_weights(target["weights"])

        # Đặc trưng khảo sát chỉ được phát hành khi ĐÃ CÓ dữ liệu, và chỉ khi
        # chính sách thiếu là `skip`. Với `zero`/`neutral` thì thiếu dữ liệu
        # không loại căn nào — nó chỉ gán một giá trị mặc định, chấp nhận được.
        risky = {
            key
            for key, spec in target["weights"].items()
            if key in SURVEY_FEATURES and spec.get("missing_value_policy") == "skip"
        }
        have_data = await _survey_features_with_data(session, risky)
        starving = sorted(risky - have_data)
        if starving:
            raise ConfigError(
                "SURVEY_FEATURE_HAS_NO_DATA",
                (
                    f"Đặc trưng khảo sát {starving} dùng chính sách 'skip' nhưng CHƯA có dữ liệu nào. "
                    "Phát hành sẽ làm coverage tụt dưới ngưỡng và MỌI căn bị bỏ qua — hệ thống chạy "
                    "sạch mà không sinh thứ hạng nào. Nạp dữ liệu khảo sát trước, hoặc đổi chính "
                    "sách sang 'neutral'."
                ),
            )

        now = datetime.now(UTC)
        # `published_at` được GIỮ LẠI khi lưu trữ — 0023 đã đổi ràng buộc từ
        # đẳng thức sang kéo theo đúng để mốc phát hành gốc không bị xoá.
        await session.execute(
            sa.update(ranking_configs)
            .where(ranking_configs.c.status == "published")
            .values(status="archived", archived_at=now)
        )
        await session.execute(
            sa.update(ranking_configs)
            .where(ranking_configs.c.version == version)
            .values(status="published", published_by=published_by.strip(), published_at=now, archived_at=None)
        )
        await session.commit()
        row = (
            await session.execute(sa.select(ranking_configs).where(ranking_configs.c.version == version))
        ).mappings().first()
        await session.rollback()  # đóng transaction ĐỌC mở ra bởi SELECT sau commit

    log.info("ranking.config.published", version=version, published_by=published_by)
    return dict(row)


async def rollback_to(*, version: int, created_by: str) -> dict:
    """Quay lại trọng số của một version cũ bằng cách CHÉP nó sang version mới.

    Không sửa lịch sử: version cũ giữ nguyên trạng thái `archived`, và dòng mới
    mang `copied_from_version` để truy được nó chép từ đâu — cùng nguyên tắc với
    `calculator_comparisons` (0013) và `reconciliation_findings` (0011).
    """
    async with get_session_factory()() as session:
        source = (
            await session.execute(sa.select(ranking_configs).where(ranking_configs.c.version == version))
        ).mappings().first()
        await session.rollback()
    if source is None:
        raise ConfigError("CONFIG_NOT_FOUND", f"Không có config version {version}")

    draft = await create_draft(
        weights=source["weights"],
        min_weight_coverage=float(source["min_weight_coverage"]),
        note=f"Rollback: chép trọng số từ v{version}.",
        created_by=created_by,
        copied_from_version=version,
    )
    return await publish(version=draft["version"], published_by=created_by)
