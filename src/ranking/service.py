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

from src.db import get_session_factory
from src.models.tables import (
    areas,
    deals,
    feature_snapshots,
    projects,
    ranking_configs,
    ranking_runs,
    ranking_scores,
    units,
)
from src.ranking.bands import DISCLAIMER, as_percent, band_for
from src.ranking.engine import FeatureWeight, UnitFeatureInput, UnitScore, rank_scores, score_unit

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
