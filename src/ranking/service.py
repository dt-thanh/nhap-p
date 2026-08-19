"""Orchestrator: đọc DB, gọi `engine.py`, ghi feature_snapshots/ranking_runs/
ranking_scores. Chạy ĐỒNG BỘ bên trong request — không worker, không hàng đợi.
Xem docstring package (`src/ranking/__init__.py`) cho lý do phạm vi bị thu hẹp
so với `docs/ranking/implementation_plan.md`.

Bốn đặc trưng vận hành của config v1 (§5.2), tính trực tiếp từ `units`/`deals`/
`areas` — KHÔNG đọc `sales_records`/`inventory_snapshots`/`absorption_daily`
(những bảng đó thuộc dashboard cũ, xem §6.5 tài liệu kế hoạch: "KHÔNG dùng, ở
mọi mức" cho xếp hạng). Chỉ có bốn đặc trưng này nên KHÔNG bao giờ MISSING với
dữ liệu thật (unit luôn có `status`; area luôn có `total_units`) — nhánh
`missing_value_policy` trong `engine.py` tồn tại vì công thức chung yêu cầu, chứ
không phải vì config v1 kích hoạt nó.
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
FEATURE_VERSION = "v1"


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


async def _area_features(session: AsyncSession, project_id: uuid.UUID) -> dict[uuid.UUID, dict[str, Decimal]]:
    """area_velocity_norm / area_conversion_norm — hằng số trong một phân khu."""
    now = datetime.now(UTC)
    window_start = now - timedelta(days=30)

    alive_deals = (
        sa.select(deals.c.unit_id, deals.c.status, deals.c.sold_at, units.c.area_id, areas.c.total_units)
        .select_from(
            deals.join(units, deals.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id)
        )
        .where(areas.c.project_id == project_id, deals.c.deleted_at.is_(None), units.c.deleted_at.is_(None))
    )
    rows = list((await session.execute(alive_deals)).mappings().all())

    per_area: dict[uuid.UUID, dict] = {}
    for row in rows:
        bucket = per_area.setdefault(
            row["area_id"], {"total_units": row["total_units"], "alive": 0, "sold": 0, "sold_30d": 0}
        )
        bucket["alive"] += 1
        if row["status"] == "sold":
            bucket["sold"] += 1
            if row["sold_at"] is not None and row["sold_at"] >= window_start:
                bucket["sold_30d"] += 1

    result: dict[uuid.UUID, dict[str, Decimal]] = {}
    for area_id, bucket in per_area.items():
        total_units = max(int(bucket["total_units"]), 1)
        velocity = min((Decimal(bucket["sold_30d"]) / total_units) / VELOCITY_SATURATION, Decimal("1"))
        conversion = Decimal(bucket["sold"]) / max(bucket["alive"], 1)
        result[area_id] = {"area_velocity_norm": velocity, "area_conversion_norm": conversion}
    return result


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
) -> list[UnitFeatureInput]:
    inputs: list[UnitFeatureInput] = []
    for row in unit_rows:
        area_vals = area_features.get(row["area_id"], {"area_velocity_norm": Decimal("0"), "area_conversion_norm": Decimal("0")})
        values = {
            "unit_available": Decimal("1") if row["status"] == "available" else Decimal("0"),
            "has_active_deal": Decimal("1") if row["id"] in active_deal_units else Decimal("0"),
            "area_velocity_norm": area_vals["area_velocity_norm"],
            "area_conversion_norm": area_vals["area_conversion_norm"],
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


async def run_ranking(
    project_id: uuid.UUID | str,
    area_id: uuid.UUID | str | None = None,
    *,
    trigger: str = "manual",
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> RankingRunResult:
    """Tính lại xếp hạng cho TOÀN BỘ dự án (luôn phạm vi project — §8.1 tài
    liệu kế hoạch: `rank_in_project` dịch chuyển khi bất kỳ căn nào đổi điểm).

    `area_id`, nếu có, KHÔNG thu hẹp phạm vi tính điểm — nó chỉ chọn phân khu
    nào được nhấn mạnh trong `summary_context` trả về cho agent tư vấn.
    """
    factory = session_factory or get_session_factory()
    project_uuid = uuid.UUID(str(project_id))
    area_uuid = uuid.UUID(str(area_id)) if area_id else None

    async with factory() as session:
        project_exists = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_uuid))
        if project_exists is None:
            raise RankingError("PROJECT_NOT_FOUND", f"Dự án {project_uuid} không tồn tại")

        config_id, config_version, weights, min_coverage = await _active_config(session)

        run_id = uuid.uuid4()
        enqueued_at = datetime.now(UTC)
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
                enqueued_at=enqueued_at,
                started_at=enqueued_at,
            )
        )
        await session.commit()

        try:
            unit_rows = await _project_units(session, project_uuid)
            area_features = await _area_features(session, project_uuid)
            active_deal_units = await _has_active_deal_by_unit(session, project_uuid)
            feature_inputs = _build_feature_inputs(unit_rows, area_features, active_deal_units)

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
    """Vật chất hoá bốn đặc trưng vận hành — chỉ scope `unit`/`area`, đủ cho
    config v1 (không đặc trưng nào của v1 dùng phạm vi `unit_type`)."""
    if not feature_inputs:
        return

    rows: dict[tuple[str, str, str], dict] = {}
    for u in feature_inputs:
        for key in ("unit_available", "has_active_deal"):
            rows[(key, "unit", u.unit_id)] = {"feature_key": key, "scope": "unit", "scope_id": u.unit_id, "value": u.values[key]}
        for key in ("area_velocity_norm", "area_conversion_norm"):
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
            set_={"feature_value": r["value"], "calculated_at": calculated_at, "updated_at": calculated_at},
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
