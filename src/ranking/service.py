"""Orchestrator: đọc DB, gọi `engine.py`, ghi feature_snapshots/ranking_runs/
ranking_scores. Chạy ĐỒNG BỘ bên trong request — không worker, không hàng đợi.
Xem docstring package (`src/ranking/__init__.py`) cho lý do phạm vi bị thu hẹp
so với `docs/ranking/implementation_plan.md`.

Đặc trưng vận hành, tính trực tiếp từ `units`/`deals`/`areas` — KHÔNG đọc
`sales_records`/`inventory_snapshots`/`absorption_daily` (những bảng đó thuộc
dashboard cũ, xem §6.5 tài liệu kế hoạch: "KHÔNG dùng, ở mọi mức" cho xếp hạng).

╔══════════════════════════════════════════════════════════════════════════════╗
║  Ba sửa chữa của đợt hiệu chỉnh v2 (đo trên DB thật, 1.991 căn đã xếp hạng)  ║
╚══════════════════════════════════════════════════════════════════════════════╝

1. **Mẫu số của `area_velocity_norm`.** §5.2 viết `/ max(areas.total_units, 1)`.
   `areas.total_units` là quỹ hàng THEO KẾ HOẠCH của phân khu (1.800–3.360),
   trong khi số căn thực sự được soi gương chỉ là 40/phân khu. Tử số (`deal sold
   30 ngày`) chỉ đếm được trên tập ĐÃ soi gương, nên tử và mẫu lấy từ hai tập
   khác nhau — đó là lỗi thứ nguyên, không phải lựa chọn hiệu chỉnh. Đo được:
   đặc trưng này nằm bẹp ở trung bình 0.0099 / lớn nhất 0.0654, tức 20% ngân
   sách trọng số sinh ra ~1% khoảng giá trị của nó. Đổi mẫu số sang SỐ CĂN CÒN
   SỐNG ĐÃ SOI GƯƠNG của phân khu đưa nó về trung bình 0.1905 / lớn nhất 0.6250.
   Cách này còn tự nhất quán khi bản sao chưa đầy đủ: cả tử lẫn mẫu cùng rút từ
   một tập.

2. **Chính sách `neutral` chưa bao giờ chạy.** Bản trước điền sẵn `Decimal("0")`
   cho phân khu không có deal nào, nên `engine.py` thấy một giá trị CÓ MẶT bằng
   0 và không bao giờ áp `missing_value_policy = "neutral"` mà chính config khai
   báo. Hệ quả: một phân khu vừa đồng bộ, chưa có deal nào, bị chấm điểm như
   phân khu bán TỆ NHẤT thay vì "chưa biết". Nay để MISSING đúng nghĩa (`None`)
   và giao lại cho engine quyết định — đúng §10.1.

3. **`unit_demand_norm` — đặc trưng mức CĂN duy nhất mà dữ liệu hiện có đỡ được.**
   v1 chỉ có đặc trưng mức `unit` là `unit_available`/`has_active_deal`, mà hai
   cái đó cộng lại chính là `units.status`. Đo được: trong MỌI phân khu, các căn
   `available` có ĐÚNG 1 giá trị điểm phân biệt (58 phân khu → 58 giá trị) — tức
   `rank_in_area` hoàn toàn do tie-break `created_at` (thứ tự soi gương) quyết
   định, không mang tín hiệu nào. Đếm deal ĐANG TRONG PHỄU của chính căn đó
   (`lead`/`qualified`/`interested`/`viewing`) là tín hiệu mức căn có thật, suy
   được từ `deals` đang có, không cần thêm cột nào.

   Phạm vi `unit_type` đã được cân nhắc và LOẠI: 0/58 phân khu có nhiều hơn một
   `unit_type` (phân khu vốn đã tách sẵn theo loại — "Sapphire 2 - 2PN"), nên
   đặc trưng phạm vi `unit_type` sẽ trùng hoàn toàn với phạm vi `area`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import (
    areas,
    deals,
    feature_snapshots,
    projects,
    ranking_configs,
    ranking_feature_definitions,
    ranking_feature_justifications,
    ranking_feature_lineage,
    ranking_feature_snapshots,
    ranking_feature_values,
    ranking_runs,
    ranking_scores,
    ranking_weight_proposals,
    units,
)
from src.ranking.bands import DISCLAIMER, as_percent, band_for
from src.ranking.engine import FeatureWeight, UnitFeatureInput, UnitScore, rank_scores, score_unit
from src.services import governance
from src.services.ranking_config import GRAIN_WEIGHT_KEYS, HierarchicalConfigError, validate_hierarchical_weights

log = get_logger("src.ranking.service")

VELOCITY_SATURATION = Decimal("0.20")  # §5.2 — hằng số chuẩn hoá, KHÔNG đưa vào config

# Bão hoà của `unit_demand_norm`: 3 bên quan tâm cùng lúc trên MỘT căn đã là mức
# "nóng" nhất mà đội bán hàng xử lý được. Cùng kỷ luật với VELOCITY_SATURATION —
# hằng số chuẩn hoá nằm trong mã, KHÔNG nằm trong config (§5.2).
DEMAND_SATURATION = Decimal("3")

# Deal đang trong phễu: đã có người quan tâm nhưng CHƯA giữ căn. `reserved`/`sold`
# cố ý không nằm ở đây — chúng là trạng thái GIỮ, đã được `unit_available` phản
# ánh, và đếm chúng vào "nhu cầu" sẽ thưởng điểm cho căn không còn bán được nữa.
FUNNEL_STATUSES = ("lead", "qualified", "interested", "viewing")

FEATURE_VERSION = "v2"

# D37's three parent grains — always independently eligible/excluded, never `U`
# (mandatory, §24.4.1). `GRAIN_WEIGHT_KEYS` (all four, incl. `unit`) comes from
# `src.services.ranking_config` since it is also the `hierarchical_weights`
# validator's vocabulary.
HIERARCHICAL_PARENT_GRAINS = ("market", "project", "area")

# D37/§24.4.6 project-grain snapshot-selection outcome, in priority order —
# see `_project_exclusion_reason()`.
PROJECT_NO_PUBLISHED_VALUE = "NO_PUBLISHED_PROJECT_VALUE"
PROJECT_VALUE_NOT_EFFECTIVE = "PROJECT_VALUE_NOT_EFFECTIVE"
PROJECT_VALUE_EXPIRED = "PROJECT_VALUE_EXPIRED"
PROJECT_EVIDENCE_INVALID = "PROJECT_EVIDENCE_INVALID"
PROJECT_COVERAGE_BELOW_THRESHOLD = "PROJECT_COVERAGE_BELOW_THRESHOLD"

PROJECT_FEATURE_SET_VERSION = "hierarchical-project-v1"

# PR-4: same shape as Project's exclusion-reason family, plus a Market-only
# freshness reason (30/90-day shelf life, `ranking_consultant.md §24.5`).
MARKET_NO_PUBLISHED_VALUE = "NO_PUBLISHED_MARKET_VALUE"
MARKET_VALUE_NOT_EFFECTIVE = "MARKET_VALUE_NOT_EFFECTIVE"
MARKET_VALUE_EXPIRED = "MARKET_VALUE_EXPIRED"
MARKET_EVIDENCE_INVALID = "MARKET_EVIDENCE_INVALID"
MARKET_COVERAGE_BELOW_THRESHOLD = "MARKET_COVERAGE_BELOW_THRESHOLD"

MARKET_FEATURE_SET_VERSION = "hierarchical-market-v1"
MARKET_DEFAULT_MAX_SHELF_LIFE_DAYS = 90

# PR-5: Area, unlike Project/Market, is fed by TWO independent sources — CRM
# (`area_velocity_norm`/`area_conversion_norm`, computed live, no writer/
# governance/snapshot at all — same operational path the legacy engine
# already uses) and expert (the three governance-authored keys below, each
# copied into its own per-area immutable snapshot at cutoff, same pattern as
# Project/Market). `governance.CRM_OWNED_AREA_FEATURE_KEYS` is the single
# source of truth for which keys are CRM-owned — reused here, not
# redeclared, so the no-override boundary can never drift between the two
# modules.
AREA_FEATURE_SET_VERSION = "hierarchical-area-v1"

# Exclusion-reason family — deliberately NOT a reuse of Project/Market's
# four-stage shape: Area has two independent sources, so "nothing resolved"
# must distinguish "CRM had nothing for this area" from "no expert value
# survived the same four-stage governance check Project/Market already use".
NO_PUBLISHED_AREA_EXPERT_VALUE = "NO_PUBLISHED_AREA_EXPERT_VALUE"
AREA_EXPERT_VALUE_NOT_EFFECTIVE = "AREA_EXPERT_VALUE_NOT_EFFECTIVE"
AREA_EXPERT_VALUE_EXPIRED = "AREA_EXPERT_VALUE_EXPIRED"
AREA_EXPERT_EVIDENCE_INVALID = "AREA_EXPERT_EVIDENCE_INVALID"
AREA_CRM_FEATURES_UNAVAILABLE = "AREA_CRM_FEATURES_UNAVAILABLE"
NO_AREA_FEATURES_RESOLVED = "NO_AREA_FEATURES_RESOLVED"
# D34 rollout policy (`min_weight_coverage=0` for every per-grain
# `engine.score_unit()` call, exactly like Project/Market) makes this
# structurally unreachable today — defined for the same reason
# `PROJECT_COVERAGE_BELOW_THRESHOLD`/`MARKET_COVERAGE_BELOW_THRESHOLD` are:
# a documented, present-but-currently-dead branch of the D34 decision, not a
# fabricated code path.
AREA_COVERAGE_BELOW_THRESHOLD = "AREA_COVERAGE_BELOW_THRESHOLD"
# Defense-in-depth only (`_select_eligible_area_justifications()` already
# filters by both `project_id` AND `area_id`, so a mismatch cannot reach
# `materialize_published_feature_value()` through the normal selection path)
# — same role `PROJECT_MISMATCH` plays in that function today.
AREA_SCOPE_PROJECT_MISMATCH = "AREA_SCOPE_PROJECT_MISMATCH"
DUPLICATE_CRM_EXPERT_FEATURE_KEY = "DUPLICATE_CRM_EXPERT_FEATURE_KEY"

AREA_COMPARABILITY_WARNING = (
    "Area eligibility/coverage differs across areas in this project's run — "
    "scores in different areas are not directly comparable (T18, §24.4.4)."
)


class RankingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class RankingRunResult:
    run_id: uuid.UUID
    project_id: uuid.UUID
    config_version_id: uuid.UUID
    config_version: int
    units_processed: int
    units_ranked: int
    units_skipped: int
    finished_at: datetime
    scores: list[UnitScore] = field(default_factory=list)
    summary_context: str = ""


async def _active_config(session: AsyncSession) -> tuple[uuid.UUID, int, list[FeatureWeight], Decimal]:
    row = (
        await session.execute(sa.select(ranking_configs).where(ranking_configs.c.status == "published"))
    ).mappings().first()
    if row is None:
        raise RankingError("NO_ACTIVE_CONFIG", "Không có ranking_configs nào ở trạng thái 'published'")
    weights = [
        FeatureWeight(
            key=key,
            weight=Decimal(str(spec["weight"])),
            direction=spec["direction"],
            missing_value_policy=spec["missing_value_policy"],
            min_confidence=Decimal(str(spec.get("min_confidence", 0))),
        )
        for key, spec in row["weights"].items()
    ]
    return row["id"], row["version"], weights, Decimal(str(row["min_weight_coverage"]))


async def _project_units(session: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    """Mọi căn còn sống (`deleted_at IS NULL`) của dự án, kèm phân khu."""
    query = (
        sa.select(
            units.c.id,
            units.c.area_id,
            units.c.status,
            units.c.created_at,
            areas.c.total_units,
        )
        .select_from(units.join(areas, units.c.area_id == areas.c.id))
        .where(areas.c.project_id == project_id, units.c.deleted_at.is_(None))
    )
    return list((await session.execute(query)).mappings().all())


async def _area_features(
    session: AsyncSession, project_id: uuid.UUID, live_units_by_area: dict[uuid.UUID, int]
) -> dict[uuid.UUID, dict[str, Decimal]]:
    """area_velocity_norm / area_conversion_norm — hằng số trong một phân khu.

    Phân khu KHÔNG có deal nào còn sống sẽ KHÔNG có mặt trong kết quả trả về.
    Đó là chủ đích: "chưa có deal nào" là THIẾU DỮ LIỆU, không phải "bán tệ".
    Người gọi phải để nó MISSING để `engine.py` áp `neutral` — xem mục 2 của
    docstring module.

    Mẫu số của velocity là `live_units_by_area` (số căn CÒN SỐNG đã soi gương),
    KHÔNG phải `areas.total_units` — xem mục 1 của docstring module.
    """
    window_start = datetime.now(UTC) - timedelta(days=30)

    alive_deals = (
        sa.select(deals.c.status, deals.c.sold_at, units.c.area_id)
        .select_from(
            deals.join(units, deals.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id)
        )
        .where(areas.c.project_id == project_id, deals.c.deleted_at.is_(None), units.c.deleted_at.is_(None))
    )
    rows = list((await session.execute(alive_deals)).mappings().all())

    per_area: dict[uuid.UUID, dict] = {}
    for row in rows:
        bucket = per_area.setdefault(row["area_id"], {"alive": 0, "sold": 0, "sold_30d": 0})
        bucket["alive"] += 1
        if row["status"] == "sold":
            bucket["sold"] += 1
            if row["sold_at"] is not None and row["sold_at"] >= window_start:
                bucket["sold_30d"] += 1

    result: dict[uuid.UUID, dict[str, Decimal]] = {}
    for area_id, bucket in per_area.items():
        inventory = max(live_units_by_area.get(area_id, 0), 1)
        velocity = min((Decimal(bucket["sold_30d"]) / inventory) / VELOCITY_SATURATION, Decimal("1"))
        conversion = Decimal(bucket["sold"]) / max(bucket["alive"], 1)
        result[area_id] = {"area_velocity_norm": velocity, "area_conversion_norm": conversion}
    return result


async def _funnel_deal_counts(session: AsyncSession, project_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Số deal ĐANG TRONG PHỄU còn sống của từng căn — nguyên liệu thô của
    `unit_demand_norm`. Căn không có deal phễu nào không xuất hiện ở đây; người
    gọi đọc nó là 0 (KHÔNG phải missing: "không ai đang quan tâm" là một sự thật
    đo được, khác hẳn "chưa biết")."""
    query = (
        sa.select(deals.c.unit_id, sa.func.count().label("n"))
        .select_from(deals.join(units, deals.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id))
        .where(
            areas.c.project_id == project_id,
            deals.c.deleted_at.is_(None),
            units.c.deleted_at.is_(None),
            deals.c.status.in_(FUNNEL_STATUSES),
        )
        .group_by(deals.c.unit_id)
    )
    return {row["unit_id"]: int(row["n"]) for row in (await session.execute(query)).mappings().all()}


async def _has_active_deal_by_unit(session: AsyncSession, project_id: uuid.UUID) -> set[uuid.UUID]:
    query = (
        sa.select(deals.c.unit_id)
        .select_from(deals.join(units, deals.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id))
        .where(
            areas.c.project_id == project_id,
            deals.c.deleted_at.is_(None),
            deals.c.status.in_(("reserved", "sold")),
        )
        .distinct()
    )
    return set((await session.execute(query)).scalars().all())


def _build_feature_inputs(
    unit_rows: list[dict],
    area_features: dict[uuid.UUID, dict[str, Decimal]],
    active_deal_units: set[uuid.UUID],
    funnel_counts: dict[uuid.UUID, int],
) -> list[UnitFeatureInput]:
    """Dựng giá trị đặc trưng cho MỌI khoá mà bất kỳ config nào có thể hỏi tới.

    Cố ý dựng cả `has_active_deal` dù config v2 không còn dùng: `_active_config`
    đọc trọng số từ DB, mà `ranking_configs` là bảng CHỈ-THÊM có thể rollback về
    v1 bằng cách phát hành lại trọng số cũ. Ngừng tính khoá này ở đây sẽ khiến
    một lần rollback hợp lệ biến thành "đặc trưng MISSING" — sai âm thầm.
    """
    inputs: list[UnitFeatureInput] = []
    for row in unit_rows:
        # KHÔNG có default 0: phân khu vắng mặt là MISSING thật, để engine áp
        # `neutral` đúng như config khai báo (mục 2 của docstring module).
        area_vals = area_features.get(row["area_id"], {})
        values = {
            "unit_available": Decimal("1") if row["status"] == "available" else Decimal("0"),
            "has_active_deal": Decimal("1") if row["id"] in active_deal_units else Decimal("0"),
            "unit_demand_norm": min(
                Decimal(funnel_counts.get(row["id"], 0)) / DEMAND_SATURATION, Decimal("1")
            ),
            "area_velocity_norm": area_vals.get("area_velocity_norm"),
            "area_conversion_norm": area_vals.get("area_conversion_norm"),
        }
        inputs.append(
            UnitFeatureInput(
                unit_id=str(row["id"]),
                area_id=str(row["area_id"]),
                tie_break_created_at=row["created_at"],
                values=values,
            )
        )
    return inputs


async def enqueue_ranking(
    project_id: uuid.UUID | str,
    *,
    trigger: str,
    sync_run_id: uuid.UUID | str | None = None,
    area_ids: list[str] | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[uuid.UUID, bool]:
    """Xếp một lần tính lại vào hàng đợi, GỘP với lần đang chờ nếu có.

    Đây là SQL của §8.3, không phải một biến thể: `INSERT ... ON CONFLICT
    (project_id) WHERE status = 'queued' DO UPDATE`, dựa trên partial unique
    index `uq_ranking_runs_queued_per_project` mà 0015 đã tạo sẵn.

    **Vì sao chống dồn phải nằm ở DB.** Một lô đồng bộ lớn có thể commit hàng
    trăm lần trong một phút, mỗi lần đều muốn tính lại cả dự án. Gộp bằng biến
    trong tiến trình API sẽ hỏng ngay khi có hai tiến trình API — mà compose đã
    chạy `api` + `worker` + `scheduler` cùng lúc. Ràng buộc ở index thì đúng
    bất kể bao nhiêu tiến trình.

    Trả `(run_id, created)`. `created=False` nghĩa là đã có run đang chờ và lời
    gọi này chỉ nhập phạm vi vào đó — **người gọi KHÔNG được đẩy job thứ hai vào
    RQ**, nếu không một run sẽ bị hai worker cùng nhặt.

    `config_version_id` cố ý để NULL lúc xếp hàng: config có thể được publish
    lại trong lúc job nằm chờ, nên bộ trọng số chỉ được chốt lúc CHIẾM run
    (`run_ranking(run_id=...)`).
    """
    factory = session_factory or get_session_factory()
    project_uuid = uuid.UUID(str(project_id))
    scope_ids = {"area_ids": sorted(area_ids)} if area_ids else {}

    stmt = pg_insert(ranking_runs).values(
        id=uuid.uuid4(),
        project_id=project_uuid,
        sync_run_id=uuid.UUID(str(sync_run_id)) if sync_run_id else None,
        trigger=trigger,
        scope_type="project",
        scope_ids=scope_ids,
        config_version_id=None,
        status="queued",
        attempt=0,
        enqueued_at=datetime.now(UTC),
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=[ranking_runs.c.project_id],
        index_where=sa.text("status = 'queued'"),
        # Gộp phạm vi: hai lô chạm hai phân khu khác nhau thì run đang chờ phải
        # mang CẢ HAI. `||` của jsonb là hợp nhất nông, đủ cho một khoá `area_ids`.
        set_={"scope_ids": ranking_runs.c.scope_ids.op("||")(stmt.excluded.scope_ids)},
    ).returning(ranking_runs.c.id, sa.text("(xmax = 0) AS created"))

    async with factory() as session:
        row = (await session.execute(upsert)).first()
        await session.commit()
    return row[0], bool(row[1])


async def run_ranking(
    project_id: uuid.UUID | str,
    area_id: uuid.UUID | str | None = None,
    *,
    trigger: str = "manual",
    run_id: uuid.UUID | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> RankingRunResult:
    """Tính lại xếp hạng cho TOÀN BỘ dự án (luôn phạm vi project — §8.1 tài
    liệu kế hoạch: `rank_in_project` dịch chuyển khi bất kỳ căn nào đổi điểm).

    `area_id`, nếu có, KHÔNG thu hẹp phạm vi tính điểm — nó chỉ chọn phân khu
    nào được nhấn mạnh trong `summary_context` trả về cho agent tư vấn.

    `run_id` — CHIẾM một `ranking_runs` đã ở trạng thái `queued` (đường worker,
    xem `enqueue_ranking`) thay vì tự tạo một dòng mới. Không truyền thì hàm
    này tự tạo run và chạy ngay, đúng như đường đồng bộ vẫn làm.

    Việc chiếm là `UPDATE ... WHERE status IN ('queued','failed')` và kiểm số
    dòng bị ảnh hưởng: hai worker cùng nhặt một job thì chỉ MỘT bên thắng, bên
    kia thấy 0 dòng và bỏ cuộc. Đặt cuộc đua ở DB chứ không ở khoá trong bộ nhớ
    — tiến trình worker có thể chết giữa chừng, khoá bộ nhớ thì không sống sót
    còn hàng `queued` thì có.
    """
    factory = session_factory or get_session_factory()
    project_uuid = uuid.UUID(str(project_id))
    area_uuid = uuid.UUID(str(area_id)) if area_id else None

    async with factory() as session:
        project_exists = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_uuid))
        if project_exists is None:
            raise RankingError("PROJECT_NOT_FOUND", f"Dự án {project_uuid} không tồn tại")

        config_id, config_version, weights, min_coverage = await _active_config(session)

        started_at = datetime.now(UTC)
        if run_id is None:
            run_id = uuid.uuid4()
            await session.execute(
                sa.insert(ranking_runs).values(
                    id=run_id,
                    project_id=project_uuid,
                    sync_run_id=None,
                    trigger=trigger,
                    scope_type="project",
                    scope_ids={"area_id": str(area_uuid)} if area_uuid else {},
                    config_version_id=config_id,
                    status="running",
                    attempt=1,
                    enqueued_at=started_at,
                    started_at=started_at,
                )
            )
        else:
            claimed = await session.execute(
                sa.update(ranking_runs)
                .where(
                    ranking_runs.c.id == run_id,
                    ranking_runs.c.status.in_(("queued", "failed")),
                )
                .values(
                    status="running",
                    started_at=started_at,
                    finished_at=None,
                    # `config_version_id` được gán LẠI lúc chiếm, không giữ giá
                    # trị lúc xếp hàng: config có thể đã được publish lại trong
                    # lúc job nằm chờ, và điểm sinh ra phải trỏ về đúng bộ trọng
                    # số ĐÃ DÙNG để tính nó.
                    config_version_id=config_id,
                    attempt=ranking_runs.c.attempt + 1,
                )
            )
            if claimed.rowcount == 0:
                await session.rollback()
                raise RankingError(
                    "RUN_NOT_CLAIMABLE",
                    f"Run {run_id} không ở trạng thái nhận được (queued/failed) — worker khác đã nhận",
                )
        await session.commit()

        try:
            unit_rows = await _project_units(session, project_uuid)
            live_units_by_area: dict[uuid.UUID, int] = {}
            for row in unit_rows:
                live_units_by_area[row["area_id"]] = live_units_by_area.get(row["area_id"], 0) + 1
            area_features = await _area_features(session, project_uuid, live_units_by_area)
            active_deal_units = await _has_active_deal_by_unit(session, project_uuid)
            funnel_counts = await _funnel_deal_counts(session, project_uuid)
            feature_inputs = _build_feature_inputs(unit_rows, area_features, active_deal_units, funnel_counts)

            calculated_at = datetime.now(UTC)
            await _persist_feature_snapshots(session, project_uuid, feature_inputs, calculated_at)
            await session.commit()

            scored = [score_unit(u, weights, min_coverage) for u in feature_inputs]
            ranked = rank_scores(scored)
            units_ranked = sum(1 for s in ranked if not s.skipped)
            units_skipped = sum(1 for s in ranked if s.skipped)

            await _persist_scores(session, project_uuid, run_id, config_id, ranked, calculated_at)
            await session.commit()

            finished_at = datetime.now(UTC)
            await session.execute(
                sa.update(ranking_runs)
                .where(ranking_runs.c.id == run_id)
                .values(
                    status="completed",
                    units_processed=len(unit_rows),
                    units_ranked=units_ranked,
                    units_skipped=units_skipped,
                    finished_at=finished_at,
                )
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await session.execute(
                sa.update(ranking_runs)
                .where(ranking_runs.c.id == run_id)
                .values(status="failed", error_summary={"message": str(exc)}, finished_at=datetime.now(UTC))
            )
            await session.commit()
            raise

        # PR-1 hierarchical step — post-run, feature-flagged, STRICTLY after the
        # legacy commit above (D33/D41, §24.7.1). Never inlined into the try/
        # except above: a hierarchical failure must never mark this already-
        # completed legacy run as 'failed', and never touches `score`/
        # `rank_in_area`/`rank_in_project`/legacy `contributions`.
        if get_settings().hierarchical_ranking_enabled:
            try:
                await compute_hierarchical_scores_for_run(
                    project_uuid, run_id, config_id, session_factory=factory
                )
            except Exception as exc:  # noqa: BLE001 - best-effort, legacy result already committed
                log.error(
                    "ranking.hierarchical.step_failed",
                    run_id=str(run_id),
                    project_id=str(project_uuid),
                    error=str(exc),
                )

        summary_context = _build_summary_context(project_uuid, area_uuid, ranked, config_version)
        return RankingRunResult(
            run_id=run_id,
            project_id=project_uuid,
            config_version_id=config_id,
            config_version=config_version,
            units_processed=len(unit_rows),
            units_ranked=units_ranked,
            units_skipped=units_skipped,
            finished_at=finished_at,
            scores=ranked,
            summary_context=summary_context,
        )


async def _persist_feature_snapshots(
    session: AsyncSession, project_id: uuid.UUID, feature_inputs: list[UnitFeatureInput], calculated_at: datetime
) -> None:
    """Vật chất hoá đặc trưng vận hành — chỉ scope `unit`/`area` (không đặc
    trưng nào đang dùng phạm vi `unit_type`; xem mục 3 của docstring module về
    việc vì sao phạm vi đó bị loại chứ không phải bị quên).

    Đặc trưng MISSING (phân khu chưa có deal nào) KHÔNG được ghi:
    `feature_snapshots.feature_value` là NOT NULL, và ghi 0 vào đó sẽ vật chất
    hoá đúng cái nói dối mà mục 2 vừa gỡ bỏ."""
    if not feature_inputs:
        return

    rows: dict[tuple[str, str, str], dict] = {}
    for u in feature_inputs:
        for key in ("unit_available", "has_active_deal", "unit_demand_norm"):
            rows[(key, "unit", u.unit_id)] = {"feature_key": key, "scope": "unit", "scope_id": u.unit_id, "value": u.values[key]}
        for key in ("area_velocity_norm", "area_conversion_norm"):
            if u.values[key] is None:
                continue
            rows[(key, "area", u.area_id)] = {"feature_key": key, "scope": "area", "scope_id": u.area_id, "value": u.values[key]}

    stmt = pg_insert(feature_snapshots)
    for r in rows.values():
        values = dict(
            id=uuid.uuid4(),
            project_id=project_id,
            feature_key=r["feature_key"],
            scope=r["scope"],
            scope_id=r["scope_id"],
            feature_value=r["value"],
            sample_count=None,
            confidence=None,
            source="operational",
            feature_version=FEATURE_VERSION,
            calculated_at=calculated_at,
            created_at=calculated_at,
            updated_at=calculated_at,
        )
        upsert = stmt.values(**values).on_conflict_do_update(
            index_elements=["project_id", "feature_key", "scope", "scope_id"],
            # `feature_version` PHẢI nằm trong `set_`. Thiếu nó, một dòng đã tồn
            # tại từ trước sẽ nhận giá trị MỚI nhưng giữ nhãn phiên bản CŨ —
            # `feature_snapshots` khi đó nói dối đúng vào cột dùng để truy xem
            # giá trị này do bộ đặc trưng nào sinh ra. Lỗi này chỉ lộ ra khi
            # `FEATURE_VERSION` đổi lần đầu (v1 → v2 ở đợt này).
            set_={
                "feature_value": r["value"],
                "feature_version": FEATURE_VERSION,
                "calculated_at": calculated_at,
                "updated_at": calculated_at,
            },
            where=stmt.excluded.calculated_at > feature_snapshots.c.calculated_at,
        )
        await session.execute(upsert)


async def _persist_scores(
    session: AsyncSession,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    config_id: uuid.UUID,
    ranked: list[UnitScore],
    computed_at: datetime,
) -> None:
    """T6 — một transaction: chống ghi đè bởi một run mới hơn, rồi xoá-và-chèn.

    Guard chống ghi đè giữ nguyên ý §10.1 (`SELECT max(computed_at) ... WHERE
    project_id`) nhưng KHÔNG áp `run_status='skipped_stale'` đầy đủ — lát cắt
    đồng bộ này không có hai run chạy song song thật sự (không worker), guard ở
    đây chỉ chặn trường hợp lý thuyết hai request trùng thời điểm.
    """
    latest = await session.scalar(
        sa.select(sa.func.max(ranking_scores.c.computed_at)).where(ranking_scores.c.project_id == project_id)
    )
    if latest is not None and latest >= computed_at:
        return

    await session.execute(sa.delete(ranking_scores).where(ranking_scores.c.project_id == project_id))

    to_insert = [s for s in ranked if not s.skipped]
    if not to_insert:
        return

    await session.execute(
        sa.insert(ranking_scores),
        [
            {
                "id": uuid.uuid4(),
                "unit_id": uuid.UUID(s.unit_id),
                "area_id": uuid.UUID(s.area_id),
                "project_id": project_id,
                "ranking_run_id": run_id,
                "config_version_id": config_id,
                "score": s.score,
                "rank_in_area": s.rank_in_area,
                "rank_in_project": s.rank_in_project,
                "weight_coverage": s.coverage,
                "contributions": s.contributions,
                "feature_freshness_at": computed_at,
                "computed_at": computed_at,
            }
            for s in to_insert
        ],
    )


@dataclass
class HierarchicalRunResult:
    """Structured, testable summary of one post-run hierarchical pass — not
    persisted anywhere, only returned/logged."""

    run_id: uuid.UUID
    project_id: uuid.UUID
    config_version_id: uuid.UUID
    hierarchical_weights_present: bool
    attempted: int = 0
    written: int = 0
    no_op_absent: int = 0
    race_skipped: int = 0


def _parse_grain_feature_weights(grain_weights: dict) -> list[FeatureWeight]:
    """`hierarchical_weights["grain_weights"]` -> the four `FeatureWeight`s for
    the fifth (top-level) `engine.score_unit()` call. `direction` is always
    `'positive'` — a grain score is already oriented in `[0,1]`, "higher is
    better" (§24.4.1); it is not part of the `grain_weights` JSON shape."""
    return [
        FeatureWeight(
            key=key,
            weight=Decimal(str(grain_weights[key]["weight"])),
            direction="positive",
            missing_value_policy=grain_weights[key]["missing_value_policy"],
        )
        for key in GRAIN_WEIGHT_KEYS
    ]


def _legal_status_for_project(project_id: uuid.UUID) -> str:
    """D27's HIGH_RISK gate (§24.4.5) — no legal-status source or seam exists
    anywhere in this repository yet (verified: no `legal_status`/`HIGH_RISK`
    reference outside `docs/`). This stub keeps the gate's *shape* in
    `compute_hierarchical_scores_for_run` ready for a real source, without
    fabricating one — it always returns `NOT_AVAILABLE`, never `HIGH_RISK`,
    so the legal-gate branch below is structurally present but unreachable in
    PR-1 (T-legal-gate is a documented, skipped test — see test suite)."""
    return "NOT_AVAILABLE"


@dataclass
class ProjectFeatureSnapshot:
    """Result of `build_project_feature_snapshot_for_run()` — one project's
    pinned, immutable Project-grain feature values for one ranking run."""

    snapshot_id: uuid.UUID
    values: dict[str, Decimal | None]
    feature_value_ids: list[str]
    feature_justification_ids: list[str]
    candidate_count: int


@dataclass
class MarketFeatureSnapshot:
    """Result of `copy_published_market_assertions_to_run_snapshot()` — one
    project's pinned, immutable Market-grain feature values for one ranking
    run. Same shape as `ProjectFeatureSnapshot`, kept as a separate type
    (not reused) so Project's PR-3 type/behavior stays untouched."""

    snapshot_id: uuid.UUID
    values: dict[str, Decimal | None]
    feature_value_ids: list[str]
    feature_justification_ids: list[str]
    candidate_count: int


@dataclass
class AreaFeatureSnapshot:
    """Result of `copy_published_area_assertions_to_run_snapshot()` — ONE
    area's pinned, immutable EXPERT-only Area feature values for one ranking
    run (CRM values are never snapshotted — see `_area_features()`'s own
    docstring and `compute_hierarchical_scores_for_run()`'s CRM block; they
    are recomputed live, exactly like the legacy engine already does, since
    they have no governance writer to snapshot from). `values` therefore
    only ever contains the three expert keys, never the two CRM keys."""

    snapshot_id: uuid.UUID
    values: dict[str, Decimal | None]
    feature_value_ids: list[str]
    feature_justification_ids: list[str]
    candidate_count: int


async def _select_eligible_project_justifications(
    session: AsyncSession, project_id: uuid.UUID, cutoff_at: datetime
) -> list[dict]:
    """`select_publishable_feature_values_at_cutoff()` (§5.2), Project-scope
    only — reads the GOVERNANCE tables directly (no `ranking_feature_values`
    row exists yet for an assertion nobody has materialized before), since
    'published for feature consumption' is `ranking_weight_proposals.status`,
    not a feature-store row (§3.2: "an approved-but-not-published value is
    invisible, matching how an approved-but-unpublished config is invisible
    to `run_ranking()` today").

    One row per `feature_definition_id` — "newest effective, then newest
    published, then stable id wins" (deterministic supersession, §5.2/§3.1).
    """
    stmt = (
        sa.select(
            ranking_feature_justifications.c.id.label("justification_id"),
            ranking_feature_justifications.c.feature_definition_id,
            ranking_feature_definitions.c.feature_key,
        )
        .select_from(
            ranking_feature_justifications.join(
                ranking_weight_proposals,
                ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
            ).join(
                ranking_feature_definitions,
                ranking_feature_definitions.c.id == ranking_feature_justifications.c.feature_definition_id,
            )
        )
        .where(
            ranking_feature_justifications.c.assertion_kind == "value",
            ranking_weight_proposals.c.assertion_kind == "value",
            ranking_weight_proposals.c.scope_type == "project",
            ranking_weight_proposals.c.project_id == project_id,
            ranking_weight_proposals.c.status == "published",
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            sa.or_(
                ranking_feature_justifications.c.effective_at.is_(None),
                ranking_feature_justifications.c.effective_at <= cutoff_at,
            ),
            sa.or_(
                ranking_feature_justifications.c.expires_at.is_(None),
                ranking_feature_justifications.c.expires_at > cutoff_at,
            ),
        )
        .order_by(
            ranking_feature_justifications.c.feature_definition_id,
            sa.desc(ranking_feature_justifications.c.effective_at),
            sa.desc(ranking_weight_proposals.c.published_at),
            ranking_feature_justifications.c.id,
        )
    )
    rows = (await session.execute(stmt)).mappings().all()

    winners: dict[uuid.UUID, dict] = {}
    for row in rows:
        # `rows` is already ordered best-first per feature_definition_id —
        # keep only the first (winning) row per feature.
        winners.setdefault(row["feature_definition_id"], dict(row))
    return list(winners.values())


async def _project_exclusion_reason(session: AsyncSession, project_id: uuid.UUID, cutoff_at: datetime) -> str:
    """Diagnostic-only (never gates anything itself) — called after selection
    already found zero usable Project values, to say *why*, using the same
    three-stage predicate as `_select_eligible_project_justifications()`."""

    def _exists(*clauses) -> sa.Select:
        return (
            sa.select(sa.literal(1))
            .select_from(
                ranking_feature_justifications.join(
                    ranking_weight_proposals,
                    ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
                )
            )
            .where(
                ranking_feature_justifications.c.assertion_kind == "value",
                ranking_weight_proposals.c.assertion_kind == "value",
                ranking_weight_proposals.c.scope_type == "project",
                ranking_weight_proposals.c.project_id == project_id,
                ranking_weight_proposals.c.status == "published",
                *clauses,
            )
            .limit(1)
        )

    any_published = await session.scalar(_exists())
    if not any_published:
        return PROJECT_NO_PUBLISHED_VALUE

    any_effective = await session.scalar(
        _exists(
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            sa.or_(
                ranking_feature_justifications.c.effective_at.is_(None),
                ranking_feature_justifications.c.effective_at <= cutoff_at,
            ),
        )
    )
    if not any_effective:
        return PROJECT_VALUE_NOT_EFFECTIVE

    any_not_expired = await session.scalar(
        _exists(
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            sa.or_(
                ranking_feature_justifications.c.effective_at.is_(None),
                ranking_feature_justifications.c.effective_at <= cutoff_at,
            ),
            sa.or_(
                ranking_feature_justifications.c.expires_at.is_(None),
                ranking_feature_justifications.c.expires_at > cutoff_at,
            ),
        )
    )
    if not any_not_expired:
        return PROJECT_VALUE_EXPIRED

    # Published, effective, non-expired rows exist but every one of them
    # failed `materialize_published_feature_value()`'s defense-in-depth
    # re-check (evidence missing, self-approval, grain/scope mismatch, etc.).
    return PROJECT_EVIDENCE_INVALID


# --- PR-4: Market grain --------------------------------------------------

# Market's shelf-life SQL expression, shared by selection and the diagnostic
# below — computed at read/replay time from the definition's OWN
# `definition_metadata.max_shelf_life_days` (never a Python-side per-feature-
# key map here; that map lives once, in `governance._MARKET_MAX_SHELF_LIFE_DAYS`,
# read at submission time — this is the read-time mirror of the same policy,
# via the row `governance` already seeded/validated against, not a second
# source of truth). "No fallback to current time": every comparison below is
# against `cutoff_at`, the ranking run's own pinned cutoff, never `now()`.
_MARKET_EFFECTIVE_EXPIRY_SQL = sa.func.coalesce(
    ranking_feature_justifications.c.expires_at,
    ranking_feature_justifications.c.effective_at
    + sa.func.make_interval(
        0,
        0,
        0,
        sa.func.coalesce(
            sa.cast(ranking_feature_definitions.c.definition_metadata["max_shelf_life_days"].astext, sa.Integer),
            MARKET_DEFAULT_MAX_SHELF_LIFE_DAYS,
        ),
    ),
)


async def _select_eligible_market_justifications(
    session: AsyncSession, project_id: uuid.UUID, cutoff_at: datetime
) -> list[dict]:
    """Market-scope equivalent of `_select_eligible_project_justifications()`
    — same predicate shape, plus the one genuine Market-only difference:
    when a justification's own `expires_at` is NULL, effective expiry is
    DERIVED as `effective_at + feature_definition.definition_metadata.max_shelf_life_days`
    (§24.5's 30/90-day policy), never "never expires" the way Project's
    (non-shelf-lived) factors default. `governance._validate_market_submission()`
    already guarantees `effective_at` is set for any market justification
    that reached `submitted`/`published` — the `effective_at IS NULL` branch
    below is defensive, not expected to fire.
    """
    stmt = (
        sa.select(
            ranking_feature_justifications.c.id.label("justification_id"),
            ranking_feature_justifications.c.feature_definition_id,
            ranking_feature_definitions.c.feature_key,
        )
        .select_from(
            ranking_feature_justifications.join(
                ranking_weight_proposals,
                ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
            ).join(
                ranking_feature_definitions,
                ranking_feature_definitions.c.id == ranking_feature_justifications.c.feature_definition_id,
            )
        )
        .where(
            ranking_feature_justifications.c.assertion_kind == "value",
            ranking_weight_proposals.c.assertion_kind == "value",
            ranking_weight_proposals.c.scope_type == "market",
            ranking_weight_proposals.c.project_id == project_id,
            ranking_weight_proposals.c.status == "published",
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            ranking_feature_justifications.c.effective_at.is_not(None),
            ranking_feature_justifications.c.effective_at <= cutoff_at,
            _MARKET_EFFECTIVE_EXPIRY_SQL > cutoff_at,
        )
        .order_by(
            ranking_feature_justifications.c.feature_definition_id,
            sa.desc(ranking_feature_justifications.c.effective_at),
            sa.desc(ranking_weight_proposals.c.published_at),
            ranking_feature_justifications.c.id,
        )
    )
    rows = (await session.execute(stmt)).mappings().all()

    winners: dict[uuid.UUID, dict] = {}
    for row in rows:
        winners.setdefault(row["feature_definition_id"], dict(row))
    return list(winners.values())


async def _market_exclusion_reason(session: AsyncSession, project_id: uuid.UUID, cutoff_at: datetime) -> str:
    """Diagnostic-only (never gates anything itself) — same four-stage shape
    as `_project_exclusion_reason()`, with the derived-expiry expression in
    the "not expired" stage."""

    def _exists(*clauses) -> sa.Select:
        return (
            sa.select(sa.literal(1))
            .select_from(
                ranking_feature_justifications.join(
                    ranking_weight_proposals,
                    ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
                ).join(
                    ranking_feature_definitions,
                    ranking_feature_definitions.c.id == ranking_feature_justifications.c.feature_definition_id,
                )
            )
            .where(
                ranking_feature_justifications.c.assertion_kind == "value",
                ranking_weight_proposals.c.assertion_kind == "value",
                ranking_weight_proposals.c.scope_type == "market",
                ranking_weight_proposals.c.project_id == project_id,
                ranking_weight_proposals.c.status == "published",
                *clauses,
            )
            .limit(1)
        )

    any_published = await session.scalar(_exists())
    if not any_published:
        return MARKET_NO_PUBLISHED_VALUE

    any_effective = await session.scalar(
        _exists(
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            ranking_feature_justifications.c.effective_at.is_not(None),
            ranking_feature_justifications.c.effective_at <= cutoff_at,
        )
    )
    if not any_effective:
        return MARKET_VALUE_NOT_EFFECTIVE

    any_not_expired = await session.scalar(
        _exists(
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            ranking_feature_justifications.c.effective_at.is_not(None),
            ranking_feature_justifications.c.effective_at <= cutoff_at,
            _MARKET_EFFECTIVE_EXPIRY_SQL > cutoff_at,
        )
    )
    if not any_not_expired:
        return MARKET_VALUE_EXPIRED

    return MARKET_EVIDENCE_INVALID


# --- PR-5: Area grain (expert side only — CRM side is `_area_features()`) ----


async def _select_eligible_area_justifications(
    session: AsyncSession, project_id: uuid.UUID, area_id: uuid.UUID, cutoff_at: datetime
) -> list[dict]:
    """Area-scope equivalent of `_select_eligible_project_justifications()`
    — identical predicate shape (no shelf-life derivation; Area follows
    Project's simpler explicit-`expires_at`-only freshness rule, not
    Market's metadata-derived shelf life), plus the one genuine Area-only
    addition: `ranking_weight_proposals.c.area_id == area_id` — a value
    assertion belongs to exactly one area, never a whole project."""
    stmt = (
        sa.select(
            ranking_feature_justifications.c.id.label("justification_id"),
            ranking_feature_justifications.c.feature_definition_id,
            ranking_feature_definitions.c.feature_key,
        )
        .select_from(
            ranking_feature_justifications.join(
                ranking_weight_proposals,
                ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
            ).join(
                ranking_feature_definitions,
                ranking_feature_definitions.c.id == ranking_feature_justifications.c.feature_definition_id,
            )
        )
        .where(
            ranking_feature_justifications.c.assertion_kind == "value",
            ranking_weight_proposals.c.assertion_kind == "value",
            ranking_weight_proposals.c.scope_type == "area",
            ranking_weight_proposals.c.project_id == project_id,
            ranking_weight_proposals.c.area_id == area_id,
            ranking_weight_proposals.c.status == "published",
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            sa.or_(
                ranking_feature_justifications.c.effective_at.is_(None),
                ranking_feature_justifications.c.effective_at <= cutoff_at,
            ),
            sa.or_(
                ranking_feature_justifications.c.expires_at.is_(None),
                ranking_feature_justifications.c.expires_at > cutoff_at,
            ),
        )
        .order_by(
            ranking_feature_justifications.c.feature_definition_id,
            sa.desc(ranking_feature_justifications.c.effective_at),
            sa.desc(ranking_weight_proposals.c.published_at),
            ranking_feature_justifications.c.id,
        )
    )
    rows = (await session.execute(stmt)).mappings().all()

    winners: dict[uuid.UUID, dict] = {}
    for row in rows:
        winners.setdefault(row["feature_definition_id"], dict(row))
    return list(winners.values())


async def _area_expert_exclusion_reason(
    session: AsyncSession, project_id: uuid.UUID, area_id: uuid.UUID, cutoff_at: datetime
) -> str:
    """Diagnostic-only, same four-stage shape as `_project_exclusion_reason()`,
    restricted to this exact `(project_id, area_id)` pair — never gates
    anything itself, called only when Area's expert side has already been
    found empty."""

    def _exists(*clauses) -> sa.Select:
        return (
            sa.select(sa.literal(1))
            .select_from(
                ranking_feature_justifications.join(
                    ranking_weight_proposals,
                    ranking_weight_proposals.c.id == ranking_feature_justifications.c.proposal_id,
                )
            )
            .where(
                ranking_feature_justifications.c.assertion_kind == "value",
                ranking_weight_proposals.c.assertion_kind == "value",
                ranking_weight_proposals.c.scope_type == "area",
                ranking_weight_proposals.c.project_id == project_id,
                ranking_weight_proposals.c.area_id == area_id,
                ranking_weight_proposals.c.status == "published",
                *clauses,
            )
            .limit(1)
        )

    any_published = await session.scalar(_exists())
    if not any_published:
        return NO_PUBLISHED_AREA_EXPERT_VALUE

    any_effective = await session.scalar(
        _exists(
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            sa.or_(
                ranking_feature_justifications.c.effective_at.is_(None),
                ranking_feature_justifications.c.effective_at <= cutoff_at,
            ),
        )
    )
    if not any_effective:
        return AREA_EXPERT_VALUE_NOT_EFFECTIVE

    any_not_expired = await session.scalar(
        _exists(
            ranking_weight_proposals.c.published_at.is_not(None),
            ranking_weight_proposals.c.published_at <= cutoff_at,
            sa.or_(
                ranking_feature_justifications.c.effective_at.is_(None),
                ranking_feature_justifications.c.effective_at <= cutoff_at,
            ),
            sa.or_(
                ranking_feature_justifications.c.expires_at.is_(None),
                ranking_feature_justifications.c.expires_at > cutoff_at,
            ),
        )
    )
    if not any_not_expired:
        return AREA_EXPERT_VALUE_EXPIRED

    return AREA_EXPERT_EVIDENCE_INVALID


async def _area_exclusion_reason(
    session: AsyncSession,
    project_id: uuid.UUID,
    area_id: uuid.UUID,
    cutoff_at: datetime,
    *,
    crm_configured_keys: set[str],
    expert_configured_keys: set[str],
) -> str:
    """Top-level Area exclusion precedence, called only once eligibility has
    already found NEITHER source resolved ANY configured key. Prefers the
    expert-side four-stage diagnostic whenever the area's `hierarchical_
    weights['area']` config asks for at least one expert key at all (it is
    the more actionable of the two — "go publish/approve something" vs. "no
    deal has ever closed here"); falls back to `AREA_CRM_FEATURES_UNAVAILABLE`
    only when the config is CRM-keys-only."""
    if expert_configured_keys:
        return await _area_expert_exclusion_reason(session, project_id, area_id, cutoff_at)
    if crm_configured_keys:
        return AREA_CRM_FEATURES_UNAVAILABLE
    return NO_AREA_FEATURES_RESOLVED


def _merge_area_values(
    crm_values: dict[str, Decimal], expert_values: dict[str, Decimal]
) -> dict[str, Decimal]:
    """Merge-by-distinct-key ONLY (§ no-override invariant, PR-5) — a
    duplicate key between the two maps is a hard error, never last-write-
    wins. In practice this can only happen if some future change lets an
    expert justification target a CRM-owned key (`governance.py` already
    blocks this at the source, at proposal-authoring time) — this is the
    scoring-time backstop."""
    overlap = set(crm_values) & set(expert_values)
    if overlap:
        raise RankingError(
            DUPLICATE_CRM_EXPERT_FEATURE_KEY,
            f"khoá đặc trưng Area trùng giữa CRM và expert: {sorted(overlap)}",
        )
    return {**crm_values, **expert_values}


async def materialize_published_feature_value(
    *,
    feature_justification_id: uuid.UUID,
    ranking_run_id: uuid.UUID,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    cutoff_at: datetime,
    session: AsyncSession,
    expected_scope_type: str = "project",
    area_id: uuid.UUID | None = None,
) -> dict:
    """The single writer for a materialized Project-, Market-, or Area-grain
    feature value — the *only* code path in this repository that inserts into
    `ranking_feature_values`/`ranking_feature_lineage` (see
    `tests/test_ranking_boundary.py`'s writer declaration). Called only by
    `build_project_feature_snapshot_for_run()`/`copy_published_market_assertions_to_run_snapshot()`/
    `copy_published_area_assertions_to_run_snapshot()` below (via the shared
    `_build_grain_feature_snapshot_for_run()` helper) — never a route, never
    `src/services/governance.py`.

    `expected_scope_type` (PR-4: `"project"` default, unchanged call shape
    for PR-3's own callers; Market's snapshot builder passes `"market"`
    explicitly) is the ONE thing that varies between Project/Market — every
    other line is grain-agnostic. This is a deliberate, minimal
    generalization of PR-3's original Project-only function: it does not
    change what PR-3 itself does (still defaults to `"project"`, still
    rejects anything else), it only lets a second, explicit caller ask for a
    different single grain. PR-5's `area_id` (default `None`, unchanged call
    shape for Project/Market) is the ONE further thing Area genuinely needs
    beyond that: a real per-area identity, written onto the value row and
    defensively re-verified against the assertion's OWN `area_id` (the
    justification could only ever have been *selected* for the right area by
    `_select_eligible_area_justifications()`'s own `area_id` filter — this is
    belt-and-suspenders, the same role `PROJECT_MISMATCH` plays above).

    Re-verifies readiness itself (§3.3/§10.6 of the implementation plan: "a
    code review that finds this function trusting the route-level gate alone
    is a correctness finding, not a style note") via PR-2's
    `validate_value_assertion_for_materialization()` — every field written
    below comes from THAT validated data, never from a caller-supplied
    override.

    Idempotent: a pre-existing row for `(snapshot_id, feature_definition_id,
    scope_type, project_id)` — `uq_ranking_feature_value_scope`, 0033 — is
    returned as-is, never duplicated, never updated (the table's own
    append-only trigger would refuse an UPDATE anyway).
    """
    validated = await governance.validate_value_assertion_for_materialization(feature_justification_id)
    justification = validated["justification"]
    proposal = validated["proposal"]
    feature = validated["feature_definition"]

    if proposal["scope_type"] != expected_scope_type:
        raise RankingError(
            "UNEXPECTED_SCOPE_TYPE",
            f"assertion {feature_justification_id} có scope_type='{proposal['scope_type']}', "
            f"nhưng lời gọi này chỉ materialize scope_type='{expected_scope_type}'",
        )
    if uuid.UUID(str(proposal["project_id"])) != uuid.UUID(str(project_id)):
        raise RankingError(
            "PROJECT_MISMATCH", f"assertion {feature_justification_id} thuộc dự án khác {project_id}"
        )
    if expected_scope_type == "area" and uuid.UUID(str(proposal["area_id"])) != uuid.UUID(str(area_id)):
        raise RankingError(
            AREA_SCOPE_PROJECT_MISMATCH,
            f"assertion {feature_justification_id} thuộc phân khu khác {area_id}",
        )
    if feature["value_type"] != "numeric":
        raise RankingError(
            "VALUE_TYPE_NOT_SUPPORTED",
            f"feature_definition {feature['id']} có value_type='{feature['value_type']}' — "
            f"chỉ materialize giá trị numeric cho grain '{expected_scope_type}'",
        )

    existing = (
        await session.execute(
            sa.select(ranking_feature_values).where(
                ranking_feature_values.c.snapshot_id == snapshot_id,
                ranking_feature_values.c.feature_definition_id == feature["id"],
                ranking_feature_values.c.scope_type == expected_scope_type,
                ranking_feature_values.c.project_id == project_id,
                ranking_feature_values.c.area_id == area_id,
            )
        )
    ).mappings().first()
    if existing is not None:
        return {
            "feature_value_id": existing["id"],
            "normalized_numeric": existing["normalized_numeric"],
            "reused": True,
        }

    now = datetime.now(UTC)
    value_id = uuid.uuid4()
    observed_at = justification["effective_at"] or proposal["published_at"]
    try:
        async with session.begin_nested():
            await session.execute(
                sa.insert(ranking_feature_values).values(
                    id=value_id,
                    snapshot_id=snapshot_id,
                    feature_definition_id=feature["id"],
                    project_id=project_id,
                    scope_type=expected_scope_type,
                    area_id=area_id,
                    unit_id=None,
                    value_kind="numeric",
                    raw_numeric=justification["raw_numeric"],
                    normalized_numeric=justification["normalized_numeric"],
                    boolean_value=None,
                    categorical_value=None,
                    missing_reason=None,
                    confidence=None,
                    sample_count=None,
                    observed_at=observed_at,
                    source_updated_at=justification["updated_at"],
                    quality_status="ok",
                    created_at=now,
                    source_justification_id=feature_justification_id,
                )
            )
            await session.execute(
                sa.insert(ranking_feature_lineage).values(
                    id=uuid.uuid4(),
                    feature_value_id=value_id,
                    source_relation="ranking_feature_justifications",
                    source_record_id=str(feature_justification_id),
                    source_revision=int(justification["updated_at"].timestamp()),
                    source_event_at=justification["effective_at"],
                    source_locator=f"ranking_feature_justifications/{feature_justification_id}",
                    source_checksum=None,
                    created_at=now,
                )
            )
    except sa.exc.IntegrityError:
        # Race: another concurrent call materialized the same
        # (snapshot_id, feature_definition_id) pair first — reselect and
        # return that row rather than raising or duplicating.
        existing = (
            await session.execute(
                sa.select(ranking_feature_values).where(
                    ranking_feature_values.c.snapshot_id == snapshot_id,
                    ranking_feature_values.c.feature_definition_id == feature["id"],
                    ranking_feature_values.c.scope_type == expected_scope_type,
                    ranking_feature_values.c.project_id == project_id,
                    ranking_feature_values.c.area_id == area_id,
                )
            )
        ).mappings().first()
        return {
            "feature_value_id": existing["id"],
            "normalized_numeric": existing["normalized_numeric"],
            "reused": True,
        }

    return {"feature_value_id": value_id, "normalized_numeric": justification["normalized_numeric"], "reused": False}


@dataclass
class _GrainSnapshotRows:
    snapshot_id: uuid.UUID
    values: dict[str, Decimal | None]
    feature_value_ids: list[str]
    feature_justification_ids: list[str]
    candidate_count: int


async def _build_grain_feature_snapshot_for_run(
    ranking_run_id: uuid.UUID,
    project_id: uuid.UUID,
    cutoff_at: datetime,
    session: AsyncSession,
    *,
    scope_type: str,
    feature_set_version: str,
    select_candidates,
    area_id: uuid.UUID | None = None,
) -> _GrainSnapshotRows:
    """Shared get-or-create + materialize-loop, extracted once both Project
    (PR-3) and Market (PR-4) needed the identical shape — the only things
    that differ between grains are the eligibility SQL (`select_candidates`)
    and `scope_type`/`feature_set_version`. `materialize_published_feature_value()`
    is always called with `expected_scope_type=scope_type`, so a Market
    candidate can never be materialized as Project or vice versa.

    `area_id` (PR-5, default `None`, unchanged call shape for Project/Market)
    is Area's one further difference: unlike Project/Market (denormalized
    per-project, `area_id` always NULL), Area needs one snapshot PER AREA —
    `0041`'s `uq_rfs_run_project_area_scope` partial index (plus its sibling
    `uq_rfs_run_project_scope_no_area`, which preserves Project/Market's
    original one-row guarantee unchanged) is exactly what makes the
    get-or-create below correct for both shapes at once: passing `area_id`
    scopes the lookup/insert to that one area; leaving it `None` reproduces
    PR-3/PR-4's original per-project behavior byte-for-byte.

    Idempotent per `(ranking_run_id, project_id, scope_type, area_id)` —
    `uq_rfs_run_project_scope_no_area`/`uq_rfs_run_project_area_scope` (0041,
    widening 0033's original `uq_ranking_feature_snapshot_run_project_scope`)
    back a get-or-create: if this run already has a snapshot for this
    scope_type (+ area, if any), its ALREADY-PINNED values are read back and
    returned unchanged (never re-selected from governance) — this is what
    makes historical replay stable even if a NEWER value assertion is
    approved/published after this run's snapshot was built (§5.6: "a past
    run's snapshot is unaffected").

    Does not commit — the caller (`compute_hierarchical_scores_for_run`)
    controls the transaction boundary, exactly like `_persist_feature_snapshots`/
    `_persist_scores` already do for the legacy path.
    """
    existing = (
        await session.execute(
            sa.select(ranking_feature_snapshots.c.id).where(
                ranking_feature_snapshots.c.ranking_run_id == ranking_run_id,
                ranking_feature_snapshots.c.project_id == project_id,
                ranking_feature_snapshots.c.scope_type == scope_type,
                ranking_feature_snapshots.c.area_id == area_id,
            )
        )
    ).first()

    if existing is not None:
        snapshot_id = existing[0]
        rows = (
            await session.execute(
                sa.select(
                    ranking_feature_values.c.id,
                    ranking_feature_values.c.normalized_numeric,
                    ranking_feature_values.c.source_justification_id,
                    ranking_feature_definitions.c.feature_key,
                ).select_from(
                    ranking_feature_values.join(
                        ranking_feature_definitions,
                        ranking_feature_definitions.c.id == ranking_feature_values.c.feature_definition_id,
                    )
                ).where(ranking_feature_values.c.snapshot_id == snapshot_id)
            )
        ).all()
        return _GrainSnapshotRows(
            snapshot_id=snapshot_id,
            values={row.feature_key: row.normalized_numeric for row in rows},
            feature_value_ids=[str(row.id) for row in rows],
            feature_justification_ids=[str(row.source_justification_id) for row in rows if row.source_justification_id],
            candidate_count=len(rows),
        )

    candidates = await select_candidates(session, project_id, cutoff_at)

    now = datetime.now(UTC)
    snapshot_id = uuid.uuid4()
    await session.execute(
        sa.insert(ranking_feature_snapshots).values(
            id=snapshot_id,
            ranking_run_id=ranking_run_id,
            project_id=project_id,
            scope_type=scope_type,
            area_id=area_id,
            cutoff_at=cutoff_at,
            computed_at=now,
            feature_set_version=feature_set_version,
            quality_status="ok" if candidates else "insufficient_data",
            quality_summary={"candidate_count": len(candidates)},
            created_at=now,
        )
    )

    values: dict[str, Decimal | None] = {}
    value_ids: list[str] = []
    justification_ids: list[str] = []
    for candidate in candidates:
        try:
            result = await materialize_published_feature_value(
                feature_justification_id=candidate["justification_id"],
                ranking_run_id=ranking_run_id,
                project_id=project_id,
                snapshot_id=snapshot_id,
                cutoff_at=cutoff_at,
                session=session,
                expected_scope_type=scope_type,
                area_id=area_id,
            )
        except governance.GovernanceError as exc:
            log.warning(
                "ranking.hierarchical.grain_value_excluded",
                scope_type=scope_type,
                justification_id=str(candidate["justification_id"]),
                error_code=exc.code,
                project_id=str(project_id),
                ranking_run_id=str(ranking_run_id),
            )
            continue
        values[candidate["feature_key"]] = result["normalized_numeric"]
        value_ids.append(str(result["feature_value_id"]))
        justification_ids.append(str(candidate["justification_id"]))

    return _GrainSnapshotRows(
        snapshot_id=snapshot_id,
        values=values,
        feature_value_ids=value_ids,
        feature_justification_ids=justification_ids,
        candidate_count=len(candidates),
    )


async def build_project_feature_snapshot_for_run(
    ranking_run_id: uuid.UUID,
    project_id: uuid.UUID,
    cutoff_at: datetime,
    session: AsyncSession,
) -> ProjectFeatureSnapshot:
    """`build_hierarchical_feature_snapshot()` (§5.2), Project-scope only —
    PR-3's original public entry point, unchanged signature/behavior. Now a
    thin wrapper over the shared `_build_grain_feature_snapshot_for_run()`
    helper (PR-4 extraction) — see that function's docstring for the
    get-or-create/idempotency contract, unchanged from PR-3.
    """
    rows = await _build_grain_feature_snapshot_for_run(
        ranking_run_id,
        project_id,
        cutoff_at,
        session,
        scope_type="project",
        feature_set_version=PROJECT_FEATURE_SET_VERSION,
        select_candidates=_select_eligible_project_justifications,
    )
    return ProjectFeatureSnapshot(
        snapshot_id=rows.snapshot_id,
        values=rows.values,
        feature_value_ids=rows.feature_value_ids,
        feature_justification_ids=rows.feature_justification_ids,
        candidate_count=rows.candidate_count,
    )


async def copy_published_market_assertions_to_run_snapshot(
    ranking_run_id: uuid.UUID,
    project_id: uuid.UUID,
    cutoff_at: datetime,
    session: AsyncSession,
) -> MarketFeatureSnapshot:
    """PR-4's Market-grain snapshot builder — the sole writer of
    `ranking_feature_snapshots`/`ranking_feature_values`/`ranking_feature_lineage`
    for `scope_type='market'` (denormalized per-project, D39 PENDING — see
    `docs/ranking/hierarchical_scoring_implementation_plan.md §3.0`). Selects
    eligible published Market assertions via `_select_eligible_market_justifications()`
    (evidence/CEO/freshness re-verified again, per-candidate, inside
    `materialize_published_feature_value(expected_scope_type="market")`), then
    delegates to the same shared get-or-create/materialize-loop Project uses.
    """
    rows = await _build_grain_feature_snapshot_for_run(
        ranking_run_id,
        project_id,
        cutoff_at,
        session,
        scope_type="market",
        feature_set_version=MARKET_FEATURE_SET_VERSION,
        select_candidates=_select_eligible_market_justifications,
    )
    return MarketFeatureSnapshot(
        snapshot_id=rows.snapshot_id,
        values=rows.values,
        feature_value_ids=rows.feature_value_ids,
        feature_justification_ids=rows.feature_justification_ids,
        candidate_count=rows.candidate_count,
    )


async def copy_published_area_assertions_to_run_snapshot(
    ranking_run_id: uuid.UUID,
    project_id: uuid.UUID,
    area_id: uuid.UUID,
    cutoff_at: datetime,
    session: AsyncSession,
) -> AreaFeatureSnapshot:
    """PR-5's Area-grain snapshot builder — the sole writer of
    `ranking_feature_snapshots`/`ranking_feature_values`/`ranking_feature_lineage`
    for `scope_type='area'`. Unlike Project/Market (one snapshot per project
    per run), Area gets ONE snapshot PER AREA per run — `0041`'s
    `uq_rfs_run_project_area_scope` partial index is what makes this safe
    (see that migration's own docstring for why a naive single-constraint
    widening would have silently broken Project/Market's existing
    guarantee).

    Rejects/asserts the project-area relationship BEFORE writing anything:
    an `area_id` that does not belong to `project_id` must never reach
    `_build_grain_feature_snapshot_for_run()` (which would otherwise happily
    create a syntactically-valid but semantically-wrong snapshot for a
    mismatched area). This mirrors `governance.create_proposal()`'s own
    area-project ownership check at authoring time — this is the equivalent
    check at scoring time, since the two moments are independent code paths.

    Selects eligible published Area assertions via
    `_select_eligible_area_justifications()` (evidence/CEO/freshness
    re-verified again, per-candidate, inside
    `materialize_published_feature_value(expected_scope_type="area",
    area_id=area_id)`), then delegates to the same shared get-or-create/
    materialize-loop Project/Market use. Only ever contains the three expert
    Area keys — CRM's `area_velocity_norm`/`area_conversion_norm` are never
    selected here (no `ranking_feature_definitions` row exists for either,
    and `_select_eligible_area_justifications()` only ever returns rows
    joined through that table) and are merged in separately by
    `compute_hierarchical_scores_for_run()`.
    """
    area_project_id = await session.scalar(sa.select(areas.c.project_id).where(areas.c.id == area_id))
    if area_project_id is None:
        raise RankingError("AREA_NOT_FOUND", f"Không có areas {area_id}")
    if uuid.UUID(str(area_project_id)) != uuid.UUID(str(project_id)):
        raise RankingError(
            AREA_SCOPE_PROJECT_MISMATCH, f"area {area_id} không thuộc dự án {project_id}"
        )

    def _select_candidates(session: AsyncSession, project_id: uuid.UUID, cutoff_at: datetime, _area_id=area_id):
        return _select_eligible_area_justifications(session, project_id, _area_id, cutoff_at)

    rows = await _build_grain_feature_snapshot_for_run(
        ranking_run_id,
        project_id,
        cutoff_at,
        session,
        scope_type="area",
        feature_set_version=AREA_FEATURE_SET_VERSION,
        select_candidates=_select_candidates,
        area_id=area_id,
    )
    return AreaFeatureSnapshot(
        snapshot_id=rows.snapshot_id,
        values=rows.values,
        feature_value_ids=rows.feature_value_ids,
        feature_justification_ids=rows.feature_justification_ids,
        candidate_count=rows.candidate_count,
    )


def _parse_feature_weight_spec_map(spec_map: dict) -> list[FeatureWeight]:
    """A grain's OWN feature weights (`hierarchical_weights["project"]`, same
    shape `validate_hierarchical_weights()`/`_validate_hierarchical_grain_features()`
    already enforces) -> `FeatureWeight` list for `engine.score_unit()`. No
    `min_confidence` key exists in this shape (unlike legacy `weights`) —
    the hierarchical grain validator never asks for one, so none is read."""
    return [
        FeatureWeight(
            key=key,
            weight=Decimal(str(spec["weight"])),
            direction=spec["direction"],
            missing_value_policy=spec["missing_value_policy"],
        )
        for key, spec in spec_map.items()
    ]


def _build_hierarchical_contributions(
    f_unit: UnitScore,
    *,
    config_version_id: uuid.UUID,
    configured_grain_weights: dict,
    exclusion_reasons: dict[str, str],
    unit_coverage: Decimal,
    cutoff_at: datetime,
    project_grain: dict | None = None,
    market_grain: dict | None = None,
    area_grain: dict | None = None,
    comparability_warning: str | None = None,
) -> dict:
    """D37's disclosure contract (§24.6's `hierarchical_contributions` table) —
    relabels `f_unit.contributions`/`f_unit.coverage` (`engine.score_unit()`'s
    own, unchanged output), introduces no new scoring.

    `project_grain`/`market_grain`/`area_grain`, when that grain was
    attempted (PR-3/PR-4/PR-5), carry `{"coverage": Decimal, "snapshot_id":
    str, "feature_value_ids": list[str], "feature_justification_ids":
    list[str]}` — `None` for the `unit_only` case (or, per-area, whenever
    that specific area had no resolved value at all), where the grain was
    never attempted in the first place. `area_grain` additionally carries
    `crm_feature_keys`/`expert_feature_keys` (PR-5's CRM-versus-expert
    disclosure, §24.6) — copied through below only when present, so
    Project/Market's entries stay exactly as before.
    """
    grain_meta = {"project": project_grain, "market": market_grain, "area": area_grain}
    eligible_grains = [
        key for key in HIERARCHICAL_PARENT_GRAINS if f_unit.contributions[key]["source"] == "resolved"
    ]
    excluded_grains = {
        key: {"reason": exclusion_reasons[key]} for key in HIERARCHICAL_PARENT_GRAINS if key not in eligible_grains
    }

    if not eligible_grains:
        score_mode = "unit_only"
    elif len(eligible_grains) < len(HIERARCHICAL_PARENT_GRAINS):
        score_mode = "partial_hierarchical"
    else:
        score_mode = "full_hierarchical"

    effective_grain_weights = {}
    if f_unit.coverage:
        for key in (*eligible_grains, "unit"):
            weight = Decimal(f_unit.contributions[key]["weight"])
            effective_grain_weights[key] = str((weight / f_unit.coverage).quantize(Decimal("0.000001")))

    grains: dict[str, dict] = {}
    for key in HIERARCHICAL_PARENT_GRAINS:
        contrib = f_unit.contributions[key]
        eligible = contrib["source"] == "resolved"
        entry = {
            "eligible": eligible,
            "score": contrib["value"],
            "coverage": None,
            "exclusion_reason": exclusion_reasons.get(key) if not eligible else None,
        }
        meta = grain_meta.get(key)
        if meta is not None:
            entry["coverage"] = str(meta["coverage"]) if meta["coverage"] is not None else None
            entry["snapshot_id"] = meta["snapshot_id"]
            entry["feature_value_ids"] = meta["feature_value_ids"]
            entry["feature_justification_ids"] = meta["feature_justification_ids"]
            if "crm_feature_keys" in meta:
                entry["crm_feature_keys"] = meta["crm_feature_keys"]
                entry["expert_feature_keys"] = meta["expert_feature_keys"]
        grains[key] = entry
    grains["unit"] = {
        "eligible": True,
        "score": f_unit.contributions["unit"]["value"],
        "coverage": str(unit_coverage),
        "exclusion_reason": None,
    }

    disclosure = None
    if score_mode == "unit_only":
        disclosure = "Unit-only hierarchical score — Market, Project, and Area context unavailable."

    return {
        "schema_version": 1,
        "score_mode": score_mode,
        "configured_grain_weights": configured_grain_weights,
        "effective_grain_weights": effective_grain_weights,
        "top_level_weight_coverage": str(f_unit.coverage),
        "eligible_grains": eligible_grains,
        "excluded_grains": excluded_grains,
        "grains": grains,
        "snapshot_id": None,
        "config_version_id": str(config_version_id),
        "cutoff_at": cutoff_at.isoformat(),
        "legal_gate": {"status": None, "gated": False},
        "comparability_warning": comparability_warning,
        "disclosure": disclosure,
    }


def _build_legal_gated_contributions(config_version_id: uuid.UUID, configured_grain_weights: dict) -> dict:
    return {
        "schema_version": 1,
        "score_mode": "legal_gated",
        "configured_grain_weights": configured_grain_weights,
        "effective_grain_weights": {},
        "top_level_weight_coverage": None,
        "eligible_grains": [],
        "excluded_grains": {},
        "grains": {},
        "snapshot_id": None,
        "config_version_id": str(config_version_id),
        "legal_gate": {"status": "HIGH_RISK", "gated": True},
        "comparability_warning": None,
        "disclosure": "Not ranked — project is under a HIGH_RISK legal gate (§24.4.5).",
    }


async def compute_hierarchical_scores_for_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    config_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> HierarchicalRunResult:
    """PR-1 post-run hierarchical step (D33/D37/D41; `ranking_consultant.md`
    §24.7.1). Called strictly AFTER `run_ranking()`'s own legacy commit (never
    inlined into it, never sharing its transaction) — reads
    `ranking_configs.hierarchical_weights` (D41) only, never `.weights`, and
    writes only `ranking_scores.hierarchical_score`/`.hierarchical_contributions`,
    scoped by BOTH `ranking_run_id` AND `unit_id`: `uq_ranking_scores_unit`
    (`0015:208`) is global on `unit_id` alone, and `_persist_scores()` above
    deletes-and-reinserts the whole project's rows on every later run, so an
    UPDATE scoped by `unit_id` only could silently land on a *different* run's
    row if a newer `run_ranking()` call raced this one (T20). A 0-row UPDATE
    is a structured logged no-op here, never an exception.

    PR-1 had no Market/Project/Area/Legal data source at all. PR-3 added the
    Project grain end-to-end; PR-4 added Market the same way. PR-5 (this
    revision) adds Area — the one grain that varies PER AREA rather than
    being a project-wide constant like M/P: for every area that has units in
    this project, `_area_features()` supplies the two CRM keys (recomputed
    live from `deals`/`units`, exactly like the legacy engine already does —
    CRM data has no governance writer to snapshot from) and
    `copy_published_area_assertions_to_run_snapshot()` supplies the three
    expert keys (CEO-approved, published, area-scope value assertions,
    copied into that area's own immutable snapshot at cutoff); the two are
    merged by DISTINCT key only (`_merge_area_values()` — a collision is a
    hard error, never last-write-wins) and, when at least one configured
    Area feature resolved from either source, `engine.score_unit()` composes
    `A` exactly like it already does for `M`/`P`. A project with no eligible
    Market, Project, or Area value stays `score_mode="unit_only"`,
    `hierarchical_score` reducing to exactly the persisted legacy `U` —
    byte-identical to PR-1's own guarantee.
    """
    factory = session_factory or get_session_factory()
    log_ctx = {"run_id": str(run_id), "project_id": str(project_id), "config_version_id": str(config_id)}

    async with factory() as session:
        config_row = (
            await session.execute(
                sa.select(ranking_configs.c.hierarchical_weights).where(ranking_configs.c.id == config_id)
            )
        ).first()
        cutoff_at = await session.scalar(
            sa.select(ranking_runs.c.started_at).where(ranking_runs.c.id == run_id)
        )
        await session.rollback()

    nested_weights = config_row[0] if config_row is not None else None
    if nested_weights is None:
        log.info("ranking.hierarchical.no_op", reason="HIERARCHICAL_WEIGHTS_ABSENT", **log_ctx)
        return HierarchicalRunResult(run_id, project_id, config_id, hierarchical_weights_present=False)

    try:
        validate_hierarchical_weights(nested_weights)
    except HierarchicalConfigError as exc:
        log.error(
            "ranking.hierarchical.config_invalid", error_code=exc.code, error_message=exc.message, **log_ctx
        )
        raise

    grain_weights = _parse_grain_feature_weights(nested_weights["grain_weights"])
    configured_grain_weights = {key: nested_weights["grain_weights"][key]["weight"] for key in GRAIN_WEIGHT_KEYS}
    project_weights = _parse_feature_weight_spec_map(nested_weights["project"])
    market_weights = _parse_feature_weight_spec_map(nested_weights["market"])
    area_weights = _parse_feature_weight_spec_map(nested_weights["area"])
    cutoff_at = cutoff_at or datetime.now(UTC)

    async with factory() as session:
        unit_rows = await _project_units(session, project_id)
        legacy_rows = (
            await session.execute(
                sa.select(
                    ranking_scores.c.unit_id,
                    ranking_scores.c.area_id,
                    ranking_scores.c.score,
                    ranking_scores.c.weight_coverage,
                    ranking_scores.c.computed_at,
                ).where(
                    ranking_scores.c.project_id == project_id,
                    ranking_scores.c.ranking_run_id == run_id,
                )
            )
        ).mappings().all()
        await session.rollback()

    legacy_by_unit = {row["unit_id"]: row for row in legacy_rows}

    result = HierarchicalRunResult(run_id, project_id, config_id, hierarchical_weights_present=True)
    legal_status = _legal_status_for_project(project_id)

    # PR-3: build/read the Project-grain snapshot ONCE per project per run —
    # every unit in this project shares the same P, not one snapshot lookup
    # per unit. `min_weight_coverage=0` for the SAME reason PR-1 already uses
    # it for the top-level call (§24.4.6/D34, deferred, not decided here):
    # only called when at least one configured Project feature actually
    # resolved a value, so `score_unit()`'s own division is never by zero.
    async with factory() as session:
        project_snapshot = await build_project_feature_snapshot_for_run(run_id, project_id, cutoff_at, session)
        await session.commit()

    project_configured_keys = {w.key for w in project_weights}
    project_has_resolved_value = any(
        project_snapshot.values.get(key) is not None for key in project_configured_keys
    )
    project_score_unit: UnitScore | None = None
    project_reason: str | None = None
    if project_has_resolved_value:
        project_score_unit = score_unit(
            UnitFeatureInput(
                unit_id=str(project_id),
                area_id=str(project_id),
                tie_break_created_at=cutoff_at,
                values=dict(project_snapshot.values),
            ),
            project_weights,
            Decimal("0"),
        )
    else:
        async with factory() as session:
            project_reason = await _project_exclusion_reason(session, project_id, cutoff_at)
            await session.rollback()

    project_grain_meta = {
        "coverage": project_score_unit.coverage if project_score_unit is not None else None,
        "snapshot_id": str(project_snapshot.snapshot_id),
        "feature_value_ids": project_snapshot.feature_value_ids,
        "feature_justification_ids": project_snapshot.feature_justification_ids,
    }

    # PR-4: Market-grain snapshot, same once-per-project-per-run shape as
    # Project above — same D34-deferral rationale for `min_weight_coverage=0`.
    async with factory() as session:
        market_snapshot = await copy_published_market_assertions_to_run_snapshot(run_id, project_id, cutoff_at, session)
        await session.commit()

    market_configured_keys = {w.key for w in market_weights}
    market_has_resolved_value = any(
        market_snapshot.values.get(key) is not None for key in market_configured_keys
    )
    market_score_unit: UnitScore | None = None
    market_reason: str | None = None
    if market_has_resolved_value:
        market_score_unit = score_unit(
            UnitFeatureInput(
                unit_id=str(project_id),
                area_id=str(project_id),
                tie_break_created_at=cutoff_at,
                values=dict(market_snapshot.values),
            ),
            market_weights,
            Decimal("0"),
        )
    else:
        async with factory() as session:
            market_reason = await _market_exclusion_reason(session, project_id, cutoff_at)
            await session.rollback()

    market_grain_meta = {
        "coverage": market_score_unit.coverage if market_score_unit is not None else None,
        "snapshot_id": str(market_snapshot.snapshot_id),
        "feature_value_ids": market_snapshot.feature_value_ids,
        "feature_justification_ids": market_snapshot.feature_justification_ids,
    }

    # PR-5: Area, unlike Market/Project, varies PER AREA — computed ONCE per
    # area (not per unit), then shared by every unit in that area, exactly
    # the same "shared constant within its scope" discipline Market/Project
    # already use within their own (project-wide) scope.
    live_units_by_area: dict[uuid.UUID, int] = {}
    for row in unit_rows:
        live_units_by_area[row["area_id"]] = live_units_by_area.get(row["area_id"], 0) + 1

    async with factory() as session:
        crm_area_features = await _area_features(session, project_id, live_units_by_area)
        await session.rollback()

    area_configured_keys = {w.key for w in area_weights}
    crm_configured_keys = area_configured_keys & governance.CRM_OWNED_AREA_FEATURE_KEYS
    expert_configured_keys = area_configured_keys - governance.CRM_OWNED_AREA_FEATURE_KEYS

    area_ids = sorted({row["area_id"] for row in unit_rows}, key=str)
    area_results: dict[uuid.UUID, dict] = {}
    for area_uuid in area_ids:
        async with factory() as session:
            area_snapshot = await copy_published_area_assertions_to_run_snapshot(
                run_id, project_id, area_uuid, cutoff_at, session
            )
            await session.commit()

        crm_values = crm_area_features.get(area_uuid, {})
        expert_values = {k: v for k, v in area_snapshot.values.items() if v is not None}
        area_values = _merge_area_values(crm_values, expert_values)

        area_has_resolved_value = any(area_values.get(key) is not None for key in area_configured_keys)
        area_score_unit: UnitScore | None = None
        area_reason: str | None = None
        if area_has_resolved_value:
            area_score_unit = score_unit(
                UnitFeatureInput(
                    unit_id=str(area_uuid),
                    area_id=str(area_uuid),
                    tie_break_created_at=cutoff_at,
                    values=dict(area_values),
                ),
                area_weights,
                Decimal("0"),
            )
        else:
            async with factory() as session:
                area_reason = await _area_exclusion_reason(
                    session,
                    project_id,
                    area_uuid,
                    cutoff_at,
                    crm_configured_keys=crm_configured_keys,
                    expert_configured_keys=expert_configured_keys,
                )
                await session.rollback()

        area_results[area_uuid] = {
            "score_unit": area_score_unit,
            "reason": area_reason,
            "meta": (
                {
                    "coverage": area_score_unit.coverage if area_score_unit is not None else None,
                    "snapshot_id": (
                        str(area_snapshot.snapshot_id) if area_snapshot.candidate_count > 0 else None
                    ),
                    "feature_value_ids": area_snapshot.feature_value_ids,
                    "feature_justification_ids": area_snapshot.feature_justification_ids,
                    # Only keys that actually fed the weighted mean (resolved
                    # AND configured) — a CRM key that resolved but was never
                    # asked for in `hierarchical_weights['area']` did not
                    # contribute to `A` and must not be disclosed as if it did.
                    "crm_feature_keys": sorted(
                        k for k in area_values if k in crm_configured_keys and area_values.get(k) is not None
                    ),
                    "expert_feature_keys": sorted(
                        k for k in area_values if k in expert_configured_keys and area_values.get(k) is not None
                    ),
                }
                if area_score_unit is not None
                else None
            ),
        }

    # T18/§24.4.4: within ONE run, M/P are project-wide constants (every unit
    # shares the same eligibility), but Area genuinely differs by area — if
    # any two areas in this project disagree on Area eligibility, every
    # unit's `top_level_weight_coverage` in this run is not directly
    # comparable across areas, and that must be disclosed, not left silent.
    area_eligibility_diverges = len({area_results[a]["score_unit"] is not None for a in area_ids}) > 1

    async with factory() as session:
        for row in unit_rows:
            unit_id = row["id"]
            legacy_row = legacy_by_unit.get(unit_id)
            if legacy_row is None:
                # D37/§24.4.1, verified against `_persist_scores()` (:530-533,552):
                # a skipped unit has NO `ranking_scores` row at all this run —
                # "U missing" means finding no row, not a None value to branch on.
                result.no_op_absent += 1
                log.info(
                    "ranking.hierarchical.unit_no_op",
                    reason="LEGACY_UNIT_SCORE_ABSENT",
                    unit_id=str(unit_id),
                    **log_ctx,
                )
                continue

            result.attempted += 1

            if legal_status == "HIGH_RISK":
                hierarchical_score = None
                contributions = _build_legal_gated_contributions(config_id, configured_grain_weights)
            else:
                # Market/Project: PR-4/PR-3's real snapshot results, computed
                # once above, shared by every unit in this project. Area:
                # PR-5's real per-area result, computed once above per area,
                # shared by every unit in THAT area (may differ from another
                # area's result within the same project/run).
                area_entry = area_results[row["area_id"]]
                area_score_unit = area_entry["score_unit"]
                exclusion_reasons = {
                    "market": market_reason,
                    "project": project_reason,
                    "area": area_entry["reason"],
                }
                unit_input = UnitFeatureInput(
                    unit_id=str(unit_id),
                    area_id=str(row["area_id"]),
                    tie_break_created_at=legacy_row["computed_at"],
                    values={
                        "market": market_score_unit.score if market_score_unit is not None else None,
                        "project": project_score_unit.score if project_score_unit is not None else None,
                        "area": area_score_unit.score if area_score_unit is not None else None,
                        "unit": legacy_row["score"],
                    },
                )
                # Call 5 (§24.7.1) — the flat top-level composition. Always runs
                # when U exists, regardless of how many parents are eligible;
                # `min_weight_coverage=0` so the unit-only case (U alone, weight
                # grain_weights['unit'] > 0) is never wrongly rejected by the
                # engine's own coverage gate (§24.4.6's documented precondition,
                # satisfied unconditionally here since PR-1 exposes no
                # `top_level_min_coverage` config key).
                f_unit = score_unit(unit_input, grain_weights, Decimal("0"))
                hierarchical_score = f_unit.score
                contributions = _build_hierarchical_contributions(
                    f_unit,
                    config_version_id=config_id,
                    configured_grain_weights=configured_grain_weights,
                    exclusion_reasons=exclusion_reasons,
                    unit_coverage=legacy_row["weight_coverage"],
                    cutoff_at=cutoff_at,
                    project_grain=project_grain_meta if project_score_unit is not None else None,
                    market_grain=market_grain_meta if market_score_unit is not None else None,
                    area_grain=area_entry["meta"],
                    comparability_warning=AREA_COMPARABILITY_WARNING if area_eligibility_diverges else None,
                )

            update = await session.execute(
                sa.update(ranking_scores)
                .where(
                    ranking_scores.c.unit_id == unit_id,
                    ranking_scores.c.ranking_run_id == run_id,
                )
                .values(hierarchical_score=hierarchical_score, hierarchical_contributions=contributions)
            )
            if update.rowcount == 0:
                result.race_skipped += 1
                log.info(
                    "ranking.hierarchical.unit_no_op",
                    reason="RACED_BY_NEWER_RUN",
                    unit_id=str(unit_id),
                    **log_ctx,
                )
            else:
                result.written += 1
        await session.commit()

    log.info(
        "ranking.hierarchical.run_completed",
        attempted=result.attempted,
        written=result.written,
        no_op_absent=result.no_op_absent,
        race_skipped=result.race_skipped,
        **log_ctx,
    )
    return result


def _build_summary_context(
    project_id: uuid.UUID, area_id: uuid.UUID | None, ranked: list[UnitScore], config_version: int
) -> str:
    kept = sorted((s for s in ranked if not s.skipped), key=lambda s: s.rank_in_project)
    if area_id is not None:
        focus = [s for s in kept if s.area_id == str(area_id)]
    else:
        focus = kept
    top = focus[:10]
    lines = [
        f"Dự án {project_id}, config v{config_version}, {len(kept)}/{len(ranked)} căn được xếp hạng"
        + (f", tập trung phân khu {area_id}" if area_id else "") + ".",
        "Top căn theo rank_in_project (unit_id, area_id, score, mức, %, rank_in_project, rank_in_area):",
    ]
    for s in top:
        band = band_for(s.score)
        percent = as_percent(s.score)
        lines.append(
            f"- {s.unit_id} | area={s.area_id} | score={s.score} | mức={band} ({percent}%) "
            f"| #{s.rank_in_project} (area #{s.rank_in_area})"
        )
    if not top:
        lines.append("(không có căn nào đạt ngưỡng coverage)")
    lines.append(f"\n{DISCLAIMER}")
    return "\n".join(lines)
