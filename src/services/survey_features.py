"""Nạp đặc trưng KHẢO SÁT vào `feature_snapshots` từ bộ tổng hợp bên ngoài.

Bốn đặc trưng của §5.2 mà không truy vấn nào suy ra được — chúng đến từ khảo sát
thực địa: `view_quality`, `natural_light`, `privacy`, `noise_level`. 0014 đã dựng
sẵn chỗ cho chúng (`source='survey_external'`, `confidence`, `sample_count`, phạm
vi `unit`/`area`/`unit_type`) rồi CỐ Ý để trống đường nhập, vì lúc đó chưa có bộ
tổng hợp nào.

╔══════════════════════════════════════════════════════════════════════════════╗
║  Đây là nơi ghi thứ hai — và cuối cùng — vào `feature_snapshots`.            ║
╚══════════════════════════════════════════════════════════════════════════════╝

Nơi kia là `src/ranking/service.py`, ghi đặc trưng VẬN HÀNH. Hai đường không bao
giờ chạm nhau vì `SURVEY_FEATURES` và đặc trưng vận hành là hai tập rời, và
module này TỪ CHỐI mọi khoá ngoài tập của nó. Không có phép kiểm đó thì một lời
gọi API có thể ghi đè `unit_available` bằng một con số do người nhập, và bảng
xếp hạng sẽ nói dối mà không lỗi nào bật lên — `uq_feature_snapshots_identity`
chỉ có một dòng cho mỗi `(project, key, scope, scope_id)`, ai ghi sau thì thắng.

**`calculated_at` quyết định ai thắng, không phải ai ghi sau.** Cùng điều kiện
upsert với đường vận hành: `WHERE excluded.calculated_at > feature_snapshots.
calculated_at`. Một lô khảo sát cũ gửi lại (retry mạng, nạp lại file) KHÔNG được
đè lên số liệu mới hơn.

**`confidence` không bắt buộc ở schema nhưng bắt buộc ở đây.** Config v1/v2 đặt
`min_confidence` 0.5–0.6 cho nhóm này; một dòng khảo sát không có confidence sẽ
lọt qua mọi ngưỡng và được coi là chắc chắn như dữ liệu vận hành. Đó không phải
điều bất kỳ ai nhập liệu muốn nói.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import areas, feature_snapshots, projects, units

log = get_logger("src.services.survey_features")

SURVEY_FEATURES = frozenset({"view_quality", "natural_light", "privacy", "noise_level"})
SCOPES = ("unit", "area", "unit_type")
FEATURE_VERSION = "survey_v1"

# Trần một lô. Không phải giới hạn kỹ thuật — nó giữ cho một lời gọi hỏng chỉ
# hỏng một phần nhỏ, và giữ transaction đủ ngắn để không khoá bảng lâu.
MAX_ITEMS_PER_BATCH = 1000


class SurveyError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SurveyItem:
    feature_key: str
    scope: str
    scope_id: str
    value: Decimal
    confidence: Decimal
    sample_count: int | None


def parse_items(raw_items: list[dict]) -> list[SurveyItem]:
    """Kiểm TOÀN BỘ lô trước khi chạm database.

    Kiểm từng dòng rồi ghi ngay sẽ để lại một lô ghi DỞ khi dòng thứ 40 sai — và
    người nhập không có cách nào biết 39 dòng đầu đã vào hay chưa.
    """
    if not raw_items:
        raise SurveyError("EMPTY_BATCH", "Lô không có dòng nào")
    if len(raw_items) > MAX_ITEMS_PER_BATCH:
        raise SurveyError("BATCH_TOO_LARGE", f"Tối đa {MAX_ITEMS_PER_BATCH} dòng mỗi lô")

    items: list[SurveyItem] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_items):
        where = f"items[{index}]"
        key = str(raw.get("feature_key", ""))
        if key not in SURVEY_FEATURES:
            raise SurveyError(
                "FEATURE_NOT_SURVEY",
                (
                    f"{where}: '{key}' không phải đặc trưng khảo sát. Chỉ nhận {sorted(SURVEY_FEATURES)} — "
                    "đặc trưng vận hành do bộ xếp hạng tự tính, ghi đè chúng từ đây sẽ làm bảng "
                    "xếp hạng nói dối."
                ),
            )
        scope = str(raw.get("scope", ""))
        if scope not in SCOPES:
            raise SurveyError("SCOPE_INVALID", f"{where}: scope phải thuộc {SCOPES}")
        scope_id = str(raw.get("scope_id", "")).strip()
        if not scope_id:
            raise SurveyError("SCOPE_ID_REQUIRED", f"{where}: scope_id không được rỗng")

        identity = (key, scope, scope_id)
        if identity in seen:
            # Hai dòng cùng danh tính trong MỘT lô: dòng sau sẽ đè dòng trước một
            # cách âm thầm, và không ai biết giá trị nào đã thắng.
            raise SurveyError("DUPLICATE_IN_BATCH", f"{where}: trùng ({key}, {scope}, {scope_id}) trong cùng lô")
        seen.add(identity)

        try:
            value = Decimal(str(raw["value"]))
            confidence = Decimal(str(raw["confidence"]))
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise SurveyError("VALUE_INVALID", f"{where}: value và confidence phải là số") from exc
        if not Decimal("0") <= value <= Decimal("1"):
            raise SurveyError(
                "VALUE_RANGE",
                f"{where}: value phải trong [0,1] — bộ tổng hợp phải chuẩn hoá TRƯỚC khi gửi (§4.2)",
            )
        if not Decimal("0") <= confidence <= Decimal("1"):
            raise SurveyError("CONFIDENCE_RANGE", f"{where}: confidence phải trong [0,1]")

        sample_count = raw.get("sample_count")
        if sample_count is not None:
            sample_count = int(sample_count)
            if sample_count < 0:
                raise SurveyError("SAMPLE_COUNT_NEGATIVE", f"{where}: sample_count không được âm")

        items.append(
            SurveyItem(
                feature_key=key,
                scope=scope,
                scope_id=scope_id,
                value=value,
                confidence=confidence,
                sample_count=sample_count,
            )
        )
    return items


async def _assert_scope_ids_exist(session, project_id: uuid.UUID, items: list[SurveyItem]) -> None:
    """`feature_snapshots.scope_id` là TEXT, không có khoá ngoại nào cưỡng chế —
    0014 ghi rõ đó là đánh đổi có chủ đích để chứa được cả UUID lẫn `unit_type`.
    Cái giá là tầng ứng dụng phải tự giữ, và đây là chỗ giữ.

    Không kiểm thì một lỗi gõ trong scope_id sẽ tạo ra một dòng đặc trưng mà
    không căn nào đọc tới — im lặng, không lỗi, và người nhập tưởng đã xong.
    """
    unit_ids, area_ids, unit_types = set(), set(), set()
    for item in items:
        if item.scope == "unit":
            unit_ids.add(item.scope_id)
        elif item.scope == "area":
            area_ids.add(item.scope_id)
        else:
            unit_types.add(item.scope_id)

    def _as_uuids(values: set[str], scope: str) -> list[uuid.UUID]:
        out = []
        for value in values:
            try:
                out.append(uuid.UUID(value))
            except ValueError as exc:
                raise SurveyError("SCOPE_ID_NOT_UUID", f"scope='{scope}' cần scope_id là UUID, gặp '{value}'") from exc
        return out

    if unit_ids:
        wanted = _as_uuids(unit_ids, "unit")
        found = set(
            (
                await session.execute(
                    sa.select(units.c.id)
                    .select_from(units.join(areas, units.c.area_id == areas.c.id))
                    .where(units.c.id.in_(wanted), areas.c.project_id == project_id, units.c.deleted_at.is_(None))
                )
            ).scalars()
        )
        missing = sorted(str(u) for u in wanted if u not in found)
        if missing:
            raise SurveyError("UNIT_NOT_IN_PROJECT", f"Căn không thuộc dự án này (hoặc đã xoá): {missing}")

    if area_ids:
        wanted = _as_uuids(area_ids, "area")
        found = set(
            (
                await session.execute(
                    sa.select(areas.c.id).where(areas.c.id.in_(wanted), areas.c.project_id == project_id)
                )
            ).scalars()
        )
        missing = sorted(str(a) for a in wanted if a not in found)
        if missing:
            raise SurveyError("AREA_NOT_IN_PROJECT", f"Phân khu không thuộc dự án này: {missing}")

    if unit_types:
        found = set(
            (
                await session.execute(
                    sa.select(units.c.unit_type)
                    .select_from(units.join(areas, units.c.area_id == areas.c.id))
                    .where(areas.c.project_id == project_id, units.c.deleted_at.is_(None))
                    .distinct()
                )
            ).scalars()
        )
        missing = sorted(unit_types - found)
        if missing:
            raise SurveyError("UNIT_TYPE_NOT_IN_PROJECT", f"Loại căn không có trong dự án này: {missing}")


async def upsert_survey_features(
    *, project_id: uuid.UUID, items: list[SurveyItem], calculated_at: datetime | None = None
) -> dict[str, int]:
    """Ghi một lô đặc trưng khảo sát. Trả số dòng đã ghi và số dòng bị bỏ vì cũ."""
    calculated_at = calculated_at or datetime.now(UTC)
    written = 0

    async with get_session_factory()() as session:
        project_exists = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_id))
        if project_exists is None:
            raise SurveyError("PROJECT_NOT_FOUND", f"Dự án {project_id} không tồn tại")
        await _assert_scope_ids_exist(session, project_id, items)

        stmt = pg_insert(feature_snapshots)
        for item in items:
            values = dict(
                id=uuid.uuid4(),
                project_id=project_id,
                feature_key=item.feature_key,
                scope=item.scope,
                scope_id=item.scope_id,
                feature_value=item.value,
                sample_count=item.sample_count,
                confidence=item.confidence,
                source="survey_external",
                feature_version=FEATURE_VERSION,
                calculated_at=calculated_at,
                created_at=calculated_at,
                updated_at=calculated_at,
            )
            upsert = stmt.values(**values).on_conflict_do_update(
                index_elements=["project_id", "feature_key", "scope", "scope_id"],
                set_={
                    "feature_value": item.value,
                    "sample_count": item.sample_count,
                    "confidence": item.confidence,
                    "source": "survey_external",
                    "feature_version": FEATURE_VERSION,
                    "calculated_at": calculated_at,
                    "updated_at": calculated_at,
                },
                where=stmt.excluded.calculated_at > feature_snapshots.c.calculated_at,
            )
            result = await session.execute(upsert)
            written += result.rowcount or 0
        await session.commit()
        # Không SELECT lại sau commit ở đây, nhưng `_assert_scope_ids_exist` đã
        # chạy nhiều SELECT TRƯỚC đó trong cùng session — commit ở trên đã đóng
        # transaction chứa chúng, nên không còn gì treo lại.

    skipped = len(items) - written
    log.info(
        "survey.features.upserted",
        project_id=str(project_id),
        written=written,
        skipped_stale=skipped,
    )
    return {"received": len(items), "written": written, "skipped_stale": skipped}
