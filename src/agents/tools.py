"""Read-only, database-backed analytics tools for the new P-100 Agent."""

from __future__ import annotations

import re
import uuid
from typing import Any

import sqlalchemy as sa

from src.db import get_session_factory
from src.models.tables import areas, deals, projects, ranking_feature_definitions, ranking_scores, units
from src.services import governance


def infer_project_id(question: str) -> str | None:
    match = re.search(r"\b(P[-_ ]?\d{4})\b", question, re.IGNORECASE)
    if match:
        return match.group(1).replace("_", "-").replace(" ", "-").upper()
    if "la pura" in question.casefold():
        return "P-0001"
    return None


def _scope_clause(query, allowed_external_ids: set[str] | None):
    if allowed_external_ids is not None:
        return query.where(projects.c.external_id.in_(allowed_external_ids))
    return query


async def project_catalog(allowed_external_ids: set[str] | None = None) -> list[dict[str, Any]]:
    async with get_session_factory()() as session:
        query = sa.select(projects.c.external_id, projects.c.name, projects.c.status).where(
            projects.c.external_id.is_not(None), projects.c.status == "active"
        ).order_by(projects.c.name)
        query = _scope_clause(query, allowed_external_ids)
        rows = (await session.execute(query)).all()
    return [{"project_id": row.external_id, "name": row.name, "status": row.status} for row in rows]


async def _resolve_project(project_id: str | None, allowed_external_ids: set[str] | None) -> dict[str, Any] | None:
    async with get_session_factory()() as session:
        query = sa.select(projects).where(projects.c.status == "active", projects.c.external_id.is_not(None))
        if project_id:
            query = query.where(sa.or_(projects.c.external_id == project_id, projects.c.name.ilike(project_id)))
        if allowed_external_ids is not None:
            query = query.where(projects.c.external_id.in_(allowed_external_ids))
        row = (await session.execute(query.limit(1))).mappings().first()
    return dict(row) if row else None


async def project_evidence_document_ids(project_uuid: uuid.UUID) -> list[str]:
    """Return only retrieval-ready evidence for one already-resolved project."""
    documents = await governance.list_documents(project_id=project_uuid)
    candidate_ids = [uuid.UUID(str(row["id"])) for row in documents]
    eligible_ids = await governance.list_retrieval_eligible_document_ids(candidate_ids)
    return [str(document_id) for document_id in candidate_ids if document_id in eligible_ids]


def _bucket_terciles(values: dict[str, float]) -> dict[str, str]:
    """Label values relative to the other areas in the same project.

    This is a cross-sectional label, not a forecast.  Ties are resolved by key
    only to keep responses deterministic; a one-area project is ``cao`` because
    there is no lower peer to compare it with.
    """
    if not values:
        return {}
    ordered = sorted(values, key=lambda key: (-values[key], key))
    size = len(ordered)
    labels: dict[str, str] = {}
    for index, key in enumerate(ordered):
        if index < size / 3:
            labels[key] = "cao"
        elif index >= (2 * size) / 3:
            labels[key] = "thấp"
        else:
            labels[key] = "trung_bình"
    return labels


def _top_contribution_factors(
    contributions: dict[str, Any] | None, feature_names: dict[str, str], n: int = 2
) -> list[str]:
    """Turn persisted contribution objects into short, readable factors."""
    if not isinstance(contributions, dict):
        return []
    candidates: list[tuple[str, float]] = []
    for key, value in contributions.items():
        if not isinstance(value, dict):
            continue
        try:
            contribution = float(value.get("contribution"))
        except (TypeError, ValueError):
            continue
        candidates.append((str(key), contribution))
    # Hierarchical disclosures store grain scores and effective weights under
    # ``grains`` rather than repeating flat feature contribution objects.  Use
    # the persisted disclosure values to expose those contributions readably;
    # this does not recompute or alter ranking.
    grains = contributions.get("grains")
    effective_weights = contributions.get("effective_grain_weights")
    if isinstance(grains, dict) and isinstance(effective_weights, dict):
        for grain, value in grains.items():
            if not isinstance(value, dict) or value.get("score") is None:
                continue
            try:
                contribution = float(effective_weights.get(grain, 0)) * float(value["score"])
            except (TypeError, ValueError):
                continue
            candidates.append((f"{grain}_grain", contribution))
    candidates.sort(key=lambda item: (-abs(item[1]), item[0]))
    factors: list[str] = []
    for key, contribution in candidates[: max(0, n)]:
        name = feature_names.get(key, key.replace("_grain", "").capitalize() + " (khối)")
        if contribution > 0:
            direction = "tăng điểm ưu tiên"
        elif contribution < 0:
            direction = "giảm điểm ưu tiên"
        else:
            direction = "không đổi điểm ưu tiên"
        factors.append(f"{name} ({direction})")
    return factors


async def _feature_name_map(session) -> dict[str, str]:
    """Return the newest human label per feature key in one read query."""
    rows = (
        await session.execute(
            sa.select(
                ranking_feature_definitions.c.feature_key,
                ranking_feature_definitions.c.name,
                ranking_feature_definitions.c.updated_at,
            ).order_by(
                ranking_feature_definitions.c.feature_key,
                ranking_feature_definitions.c.updated_at.desc(),
            )
        )
    ).mappings().all()
    result: dict[str, str] = {}
    for row in rows:
        result.setdefault(row["feature_key"], row["name"])
    return result


def _rank_level(position: int, total: int) -> str:
    if total <= 0:
        return "trung_bình"
    if position <= total / 3:
        return "cao"
    if position > (2 * total) / 3:
        return "thấp"
    return "trung_bình"


def _deal_funnel_summary(unit_status: str, latest_deal_status: str | None) -> str:
    if unit_status == "sold" or latest_deal_status == "sold":
        return "Đã bán"
    if unit_status == "reserved" or latest_deal_status == "reserved":
        return "Đang giữ chỗ"
    if unit_status == "available" and latest_deal_status is None:
        return "Còn hàng, chưa có giao dịch"
    return "Chưa xác định trạng thái giao dịch"


async def build_context(
    question: str,
    project_id: str | None = None,
    allowed_external_ids: set[str] | None = None,
    limit: int = 10,
    ascending: bool = False,
    unit_status: str | None = None,
    deal_status: str | None = None,
    focus_unit_ids: list[str] | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    target = project_id or infer_project_id(question)
    project = await _resolve_project(target, allowed_external_ids)
    if not project:
        return {"projects": await project_catalog(allowed_external_ids), "error": "Không tìm thấy dự án trong phạm vi được cấp."}
    project_uuid = uuid.UUID(str(project["id"]))

    async with get_session_factory()() as session:
        area_count = await session.scalar(sa.select(sa.func.count()).select_from(areas).where(areas.c.project_id == project_uuid, areas.c.status == "active")) or 0
        unit_count = await session.scalar(sa.select(sa.func.count()).select_from(units.join(areas, units.c.area_id == areas.c.id)).where(areas.c.project_id == project_uuid, areas.c.status == "active", units.c.deleted_at.is_(None))) or 0
        deal_count = await session.scalar(sa.select(sa.func.count()).select_from(deals.join(units, deals.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id)).where(areas.c.project_id == project_uuid, deals.c.deleted_at.is_(None), units.c.deleted_at.is_(None))) or 0
        available_unit_count = await session.scalar(sa.select(sa.func.count()).select_from(units.join(areas, units.c.area_id == areas.c.id)).where(areas.c.project_id == project_uuid, areas.c.status == "active", units.c.deleted_at.is_(None), units.c.status == "available")) or 0
        deal_status_rows = (await session.execute(
            sa.select(deals.c.status, sa.func.count().label("count"))
            .select_from(deals.join(units, deals.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id))
            .where(areas.c.project_id == project_uuid, deals.c.deleted_at.is_(None), units.c.deleted_at.is_(None))
            .group_by(deals.c.status)
        )).all()
        deal_counts = {row.status: int(row.count) for row in deal_status_rows}

        top_query = sa.select(
            units.c.external_unit_id, units.c.unit_code, units.c.status, areas.c.area_name,
            ranking_scores.c.score, ranking_scores.c.hierarchical_score,
            ranking_scores.c.rank_in_project, ranking_scores.c.contributions,
            ranking_scores.c.hierarchical_contributions, ranking_scores.c.config_version_id,
            sa.select(deals.c.status)
            .where(deals.c.unit_id == units.c.id, deals.c.deleted_at.is_(None))
            .order_by(deals.c.updated_at.desc())
            .limit(1)
            .scalar_subquery()
            .label("latest_deal_status"),
        ).select_from(
            ranking_scores.join(units, ranking_scores.c.unit_id == units.c.id).join(areas, ranking_scores.c.area_id == areas.c.id)
        ).where(ranking_scores.c.project_id == project_uuid, units.c.deleted_at.is_(None)).order_by(
            (ranking_scores.c.hierarchical_score.asc() if ascending else ranking_scores.c.hierarchical_score.desc()).nullslast(),
            (ranking_scores.c.rank_in_project.desc() if ascending else ranking_scores.c.rank_in_project),
        ).limit(limit)
        if unit_status:
            top_query = top_query.where(units.c.status == unit_status)
        if deal_status:
            top_query = top_query.where(
                sa.exists(
                    sa.select(deals.c.id).where(
                        deals.c.unit_id == units.c.id,
                        deals.c.status == deal_status,
                        deals.c.deleted_at.is_(None),
                    )
                )
            )
        if focus_unit_ids:
            top_query = top_query.where(units.c.external_unit_id.in_(focus_unit_ids))
        top_rows = (await session.execute(top_query)).mappings().all()

        area_query = sa.select(
            areas.c.area_name,
            sa.func.count(units.c.id).label("unit_count"),
            sa.func.count(units.c.id).filter(units.c.status == "available").label("available_count"),
            sa.func.count(units.c.id).filter(units.c.status == "sold").label("sold_count"),
            sa.func.count(units.c.id)
            .filter(
                sa.exists(
                    sa.select(deals.c.id).where(
                        deals.c.unit_id == units.c.id,
                        deals.c.status == "reserved",
                        deals.c.deleted_at.is_(None),
                    )
                )
            )
            .label("booking_count"),
            sa.func.avg(sa.func.coalesce(ranking_scores.c.hierarchical_score, ranking_scores.c.score)).label("average_score"),
        ).select_from(areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))).outerjoin(ranking_scores, ranking_scores.c.area_id == areas.c.id)).where(areas.c.project_id == project_uuid, areas.c.status == "active").group_by(areas.c.id, areas.c.area_name).order_by(areas.c.area_name)
        area_rows = (await session.execute(area_query)).mappings().all()
        feature_names = await _feature_name_map(session)

    top: list[dict[str, Any]] = []
    total_returned = len(top_rows)
    for index, r in enumerate(top_rows, 1):
        score_value = r["hierarchical_score"] if r["hierarchical_score"] is not None else r["score"]
        contributions = r["hierarchical_contributions"] or r["contributions"] or {}
        display_rank = r["rank_in_project"] if r["rank_in_project"] is not None else index
        sellability = f"Ưu tiên {_rank_level(index, total_returned)}"
        if r["status"] == "available":
            sellability += " — còn hàng"
        elif r["status"] == "reserved":
            sellability += " — đang giữ chỗ"
        elif r["status"] == "sold":
            sellability += " — đã bán"
        factors = _top_contribution_factors(contributions, feature_names)
        top.append(
            {
                "unit_id": r["external_unit_id"],
                "unit_code": r["unit_code"],
                "status": r["status"],
                "area": r["area_name"],
                "score": round(float(score_value) * 100, 2) if score_value is not None else None,
                "rank": display_rank,
                "score_model": "v3_hierarchical" if r["hierarchical_score"] is not None else "v2_legacy",
                "config_version_id": str(r["config_version_id"]) if r["config_version_id"] else None,
                "contributions": contributions,
                "latest_deal_status": r["latest_deal_status"],
                "demand_label": _rank_level(index, total_returned),
                "deal_funnel_summary": _deal_funnel_summary(r["status"], r["latest_deal_status"]),
                "sellability_label": sellability,
                "reason": f"{sellability}. {factors[0]}." if factors else f"{sellability}.",
                "top_contribution_factors": factors,
            }
        )

    conversion_values: dict[str, float] = {}
    demand_values: dict[str, float] = {}
    for row in area_rows:
        units_in_area = int(row["unit_count"] or 0)
        sold = int(row["sold_count"] or 0)
        booking = int(row["booking_count"] or 0)
        conversion_values[str(row["area_name"])] = sold / units_in_area if units_in_area else 0.0
        demand_values[str(row["area_name"])] = (sold + booking) / units_in_area if units_in_area else 0.0
    conversion_levels = _bucket_terciles(conversion_values)
    demand_levels = _bucket_terciles(demand_values)
    area_items = []
    for row in area_rows:
        area_name = str(row["area_name"])
        conversion_level = conversion_levels.get(area_name, "trung_bình")
        units_in_area = int(row["unit_count"] or 0)
        sold = int(row["sold_count"] or 0)
        booking = int(row["booking_count"] or 0)
        area_items.append(
            {
                "area": row["area_name"],
                "unit_count": row["unit_count"],
                "available_count": row["available_count"],
                "sold_count": row["sold_count"],
                "booking_count": row["booking_count"],
                "conversion_rate": sold / units_in_area if units_in_area else 0.0,
                "demand_rate": (sold + booking) / units_in_area if units_in_area else 0.0,
                "average_score": round(float(row["average_score"]) * 100, 2) if row["average_score"] is not None else None,
                "conversion_level": conversion_level,
                "demand_level": demand_levels.get(area_name, "trung_bình"),
                "narrative": (
                    f"Phân khu {row['area_name']}: {int(row['sold_count'] or 0)}/{int(row['unit_count'] or 0)} đã bán, "
                    f"{int(row['available_count'] or 0)}/{int(row['unit_count'] or 0)} còn hàng, mức chuyển đổi {conversion_level} trong phạm vi dự án."
                ),
            }
        )

    summary = {
        "area_count": area_count,
        "unit_count": unit_count,
        "available_unit_count": available_unit_count,
        "deal_count": deal_count,
        "booking_count": deal_counts.get("reserved", 0),
        "sold_deal_count": deal_counts.get("sold", 0),
    }
    denominator = unit_count or 0
    available_ratio = available_unit_count / denominator if denominator else 0.0
    sold_ratio = summary["sold_deal_count"] / denominator if denominator else 0.0
    booking_ratio = summary["booking_count"] / denominator if denominator else 0.0
    summary["market_posture"] = (
        f"Còn {available_ratio:.0%} hàng, đã bán {sold_ratio:.0%}, đang giữ chỗ {booking_ratio:.0%}."
    )
    return {"project": {"project_id": project["external_id"], "name": project["name"], "internal_id": str(project["id"])}, "summary": summary, "requested_unit_count": limit, "returned_unit_count": len(top), "ranking_order": "lowest_first" if ascending else "highest_first", "top_ranked_units": top, "areas": area_items}
