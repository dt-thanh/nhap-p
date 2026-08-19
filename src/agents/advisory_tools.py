"""Read-only database tools used by the advisory chat agent.

The language model receives only tool results selected for the question. It has
no database connection and cannot generate or execute SQL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from src.db import get_session_factory
from src.models.tables import (
    absorption_daily,
    areas,
    deals,
    projects,
    ranking_configs,
    ranking_runs,
    ranking_scores,
    units,
)
from src.services.ai import generate_content

ALLOWED_ADVISORY_TOOLS = {
    "portfolio_overview",
    "project_overview",
    "compare_areas",
    "top_ranked_units",
    "unit_mix_overview",
    "area_ranking_risks",
    "inventory_hotspots",
    "ranking_coverage",
    "reservation_pressure",
    "policy_snapshot",
}

POLICY_FILE = Path(__file__).resolve().parents[2] / "data" / "discount_policies.json"

PORTFOLIO_TERMS = (
    "bao nhiêu dự án",
    "danh sách dự án",
    "có mấy dự án",
    "quy mô dự án",
    "so sánh dự án",
)
AREA_TERMS = (
    "so sánh",
    "phân khu",
    "xu hướng",
    "hấp thụ",
    "bán chậm",
    "bán nhanh",
    "rủi ro",
)
RANKING_TERMS = ("ưu tiên", "top", "xếp hạng", "ranking", "điểm", "căn nào", "đẩy", "rủi ro", "tồn kho")
UNIT_MIX_TERMS = ("3pn", "2pn", "1pn", "studio", "phòng ngủ", "loại căn", "bao nhiêu căn")
AREA_RISK_TERMS = ("ranking thấp", "điểm ranking thấp", "nhiều căn còn lại", "cần kiểm tra", "chính sách bán hàng")
INVENTORY_TERMS = ("tồn kho", "còn lại", "còn hàng", "available", "quỹ hàng", "hotspot")
COVERAGE_TERMS = ("coverage", "độ phủ", "thiếu ranking", "chưa có ranking", "chất lượng ranking", "đủ điểm")
RESERVATION_TERMS = ("reserved", "booking", "giữ chỗ", "áp lực giữ chỗ", "deal", "đang giữ")
POLICY_TERMS = ("chính sách", "chiết khấu", "discount", "ưu đãi", "payment support", "hỗ trợ thanh toán")


def _mentions_any(message: str, terms: tuple[str, ...]) -> bool:
    normalized = message.casefold()
    return any(term in normalized for term in terms)


def _deterministic_tool_plan(message: str, project_id: str | None) -> list[str]:
    if project_id is None:
        return ["portfolio_overview"]
    plan = ["project_overview"]
    if _mentions_any(message, AREA_TERMS):
        plan.append("compare_areas")
    if _mentions_any(message, RANKING_TERMS):
        plan.append("top_ranked_units")
    if _mentions_any(message, UNIT_MIX_TERMS):
        plan.append("unit_mix_overview")
    if _mentions_any(message, AREA_RISK_TERMS) or (_mentions_any(message, AREA_TERMS) and _mentions_any(message, RANKING_TERMS)):
        plan.append("area_ranking_risks")
    if _mentions_any(message, INVENTORY_TERMS):
        plan.append("inventory_hotspots")
    if _mentions_any(message, COVERAGE_TERMS):
        plan.append("ranking_coverage")
    if _mentions_any(message, RESERVATION_TERMS):
        plan.append("reservation_pressure")
    if _mentions_any(message, POLICY_TERMS):
        plan.append("policy_snapshot")
    if _mentions_any(message, PORTFOLIO_TERMS):
        plan.insert(0, "portfolio_overview")
    return list(dict.fromkeys(plan))


def _sanitize_tool_plan(tool_names: list[str], message: str, project_id: str | None) -> list[str]:
    if project_id is None:
        return ["portfolio_overview"]
    selected = [name for name in tool_names if name in ALLOWED_ADVISORY_TOOLS]
    if "project_overview" not in selected:
        selected.insert(0, "project_overview")
    if "portfolio_overview" in selected and not _mentions_any(message, PORTFOLIO_TERMS):
        selected = [name for name in selected if name != "portfolio_overview"]
    for required in _deterministic_tool_plan(message, project_id):
        if required not in selected:
            selected.append(required)
    return list(dict.fromkeys(selected))


def _scope_filter(allowed_external_ids):
    if allowed_external_ids in (None, "ALL"):
        return None
    return projects.c.external_id.in_(list(allowed_external_ids))


async def portfolio_overview(allowed_external_ids=None) -> dict:
    unit_counts = (
        sa.select(areas.c.project_id, sa.func.count(units.c.id).label("units"))
        .select_from(areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))))
        .group_by(areas.c.project_id)
        .subquery()
    )
    query = (
        sa.select(
            projects.c.external_id,
            projects.c.name,
            projects.c.source_updated_at,
            sa.func.coalesce(unit_counts.c.units, 0),
        )
        .select_from(projects.outerjoin(unit_counts, unit_counts.c.project_id == projects.c.id))
        .where(projects.c.status == "active", projects.c.external_id.isnot(None))
        .order_by(projects.c.name)
    )
    scope_clause = _scope_filter(allowed_external_ids)
    if scope_clause is not None:
        query = query.where(scope_clause)
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).all()
    return {
        "project_count": len(rows),
        "projects": [
            {
                "project_id": row[0],
                "name": row[1],
                "unit_count": int(row[3]),
                "source_updated_at": row[2].isoformat() if row[2] else None,
            }
            for row in rows
        ],
    }


async def _resolve_project(project_id: str, allowed_external_ids=None):
    query = sa.select(projects.c.id, projects.c.external_id, projects.c.name, projects.c.source_updated_at).where(
        projects.c.external_id == project_id
    )
    scope_clause = _scope_filter(allowed_external_ids)
    if scope_clause is not None:
        query = query.where(scope_clause)
    async with get_session_factory()() as session:
        return (await session.execute(query)).mappings().first()


async def _infer_project_id_from_message(message: str, allowed_external_ids=None) -> str | None:
    normalized = message.casefold()
    query = sa.select(projects.c.external_id, projects.c.name).where(
        projects.c.status == "active", projects.c.external_id.isnot(None)
    )
    scope_clause = _scope_filter(allowed_external_ids)
    if scope_clause is not None:
        query = query.where(scope_clause)
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    matches = [
        row
        for row in rows
        if str(row["external_id"]).casefold() in normalized or str(row["name"]).casefold() in normalized
    ]
    if not matches:
        return None
    best = max(matches, key=lambda row: len(str(row["name"])))
    return str(best["external_id"])


async def project_overview(project_id: str, allowed_external_ids=None) -> dict:
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    effective_status = sa.case(
        (sa.func.bool_or(sa.and_(deals.c.deleted_at.is_(None), deals.c.status == "sold")), "sold"),
        (sa.func.bool_or(sa.and_(deals.c.deleted_at.is_(None), deals.c.status == "reserved")), "reserved"),
        else_=units.c.status,
    )
    per_unit = (
        sa.select(units.c.id.label("unit_id"), effective_status.label("status"))
        .select_from(units.join(areas, units.c.area_id == areas.c.id).outerjoin(deals, deals.c.unit_id == units.c.id))
        .where(areas.c.project_id == project["id"], units.c.deleted_at.is_(None))
        .group_by(units.c.id, units.c.status)
        .subquery()
    )
    query = sa.select(per_unit.c.status, sa.func.count()).group_by(per_unit.c.status)
    async with get_session_factory()() as session:
        counts = {str(status): int(count) for status, count in (await session.execute(query)).all()}
        area_count = await session.scalar(
            sa.select(sa.func.count()).select_from(areas).where(areas.c.project_id == project["id"])
        )
        latest = await session.scalar(
            sa.select(sa.func.max(absorption_daily.c.computed_at))
            .select_from(absorption_daily.join(areas, absorption_daily.c.area_id == areas.c.id))
            .where(areas.c.project_id == project["id"])
        )
    return {
        "project_id": project["external_id"],
        "project_name": project["name"],
        "area_count": int(area_count or 0),
        "unit_count": sum(counts.values()),
        "status_counts": counts,
        "source_updated_at": project["source_updated_at"].isoformat() if project["source_updated_at"] else None,
        "metrics_computed_at": latest.isoformat() if latest else None,
    }


async def compare_areas(project_id: str, allowed_external_ids=None) -> dict:
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    latest = (
        sa.select(absorption_daily.c.area_id, sa.func.max(absorption_daily.c.stat_date).label("max_date"))
        .group_by(absorption_daily.c.area_id)
        .subquery()
    )
    current = absorption_daily.alias("current_absorption")
    query = (
        sa.select(
            areas.c.external_id,
            areas.c.area_name,
            areas.c.unit_type,
            areas.c.bedrooms,
            areas.c.area_sqm,
            sa.func.count(units.c.id).label("unit_count"),
            sa.func.count(units.c.id).filter(units.c.status == "available").label("available"),
            current.c.units_sold,
            current.c.units_remaining,
            current.c.velocity_7d,
            current.c.velocity_30d,
            current.c.data_quality_status,
            current.c.computed_at,
        )
        .select_from(
            areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None)))
            .outerjoin(latest, latest.c.area_id == areas.c.id)
            .outerjoin(current, sa.and_(current.c.area_id == areas.c.id, current.c.stat_date == latest.c.max_date))
        )
        .where(areas.c.project_id == project["id"])
        .group_by(areas.c.id, current.c.id)
        .order_by(sa.desc(current.c.velocity_30d).nulls_last(), areas.c.area_name)
    )
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    return {
        "areas": [
            {
                "area_id": r["external_id"],
                "name": r["area_name"],
                "unit_type": r["unit_type"],
                "bedrooms": r["bedrooms"],
                "area_sqm": float(r["area_sqm"]),
                "unit_count": int(r["unit_count"]),
                "available": int(r["available"]),
                "units_sold": r["units_sold"],
                "units_remaining": r["units_remaining"],
                "velocity_7d": float(r["velocity_7d"]) if r["velocity_7d"] is not None else None,
                "velocity_30d": float(r["velocity_30d"]) if r["velocity_30d"] is not None else None,
                "data_quality": r["data_quality_status"],
                "computed_at": r["computed_at"].isoformat() if r["computed_at"] else None,
            }
            for r in rows
        ]
    }


async def top_ranked_units(project_id: str, allowed_external_ids=None, limit: int = 10) -> dict:
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    query = (
        sa.select(
            units.c.id,
            units.c.unit_code,
            units.c.unit_type,
            units.c.status,
            areas.c.external_id,
            areas.c.area_name,
            ranking_scores.c.score,
            ranking_scores.c.rank_in_project,
            ranking_scores.c.computed_at,
        )
        .select_from(
            units.join(areas, units.c.area_id == areas.c.id).join(
                ranking_scores, ranking_scores.c.unit_id == units.c.id
            )
        )
        .where(areas.c.project_id == project["id"], units.c.deleted_at.is_(None), units.c.status == "available")
        .order_by(ranking_scores.c.rank_in_project)
        .limit(limit)
    )
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    return {
        "items": [
            {
                "unit_uuid": str(r["id"]),
                "unit_code": r["unit_code"],
                "unit_type": r["unit_type"],
                "area_id": r["external_id"],
                "area_name": r["area_name"],
                "score": float(r["score"]),
                "rank": r["rank_in_project"],
                "computed_at": r["computed_at"].isoformat(),
            }
            for r in rows
        ]
    }


async def unit_mix_overview(project_id: str, allowed_external_ids=None) -> dict:
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    query = (
        sa.select(
            areas.c.unit_type,
            areas.c.bedrooms,
            sa.func.count(units.c.id).label("unit_count"),
            sa.func.count(units.c.id).filter(units.c.status == "available").label("available"),
            sa.func.count(units.c.id).filter(units.c.status == "reserved").label("reserved"),
            sa.func.count(units.c.id).filter(units.c.status == "sold").label("sold"),
            sa.func.count(sa.distinct(areas.c.id)).label("area_count"),
        )
        .select_from(areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))))
        .where(areas.c.project_id == project["id"])
        .group_by(areas.c.unit_type, areas.c.bedrooms)
        .order_by(areas.c.bedrooms, areas.c.unit_type)
    )
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    return {
        "project_id": project["external_id"],
        "project_name": project["name"],
        "items": [
            {
                "unit_type": r["unit_type"],
                "bedrooms": r["bedrooms"],
                "unit_count": int(r["unit_count"]),
                "available": int(r["available"]),
                "reserved": int(r["reserved"]),
                "sold": int(r["sold"]),
                "area_count": int(r["area_count"]),
            }
            for r in rows
        ],
    }


async def area_ranking_risks(project_id: str, allowed_external_ids=None, limit: int = 12) -> dict:
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    low_score_threshold = 0.5
    query = (
        sa.select(
            areas.c.external_id,
            areas.c.area_name,
            areas.c.unit_type,
            areas.c.bedrooms,
            sa.func.count(units.c.id).filter(units.c.status == "available").label("available"),
            sa.func.count(ranking_scores.c.id).filter(units.c.status == "available").label("ranked_available"),
            sa.func.avg(ranking_scores.c.score).filter(units.c.status == "available").label("avg_score"),
            sa.func.count(ranking_scores.c.id)
            .filter(sa.and_(units.c.status == "available", ranking_scores.c.score <= low_score_threshold))
            .label("low_score_available"),
            sa.func.min(ranking_scores.c.score).filter(units.c.status == "available").label("min_score"),
            sa.func.max(ranking_scores.c.computed_at).label("computed_at"),
        )
        .select_from(
            areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))).outerjoin(
                ranking_scores, ranking_scores.c.unit_id == units.c.id
            )
        )
        .where(areas.c.project_id == project["id"])
        .group_by(areas.c.id)
        .order_by(
            sa.desc(sa.func.count(ranking_scores.c.id).filter(sa.and_(units.c.status == "available", ranking_scores.c.score <= low_score_threshold))),
            sa.desc(sa.func.count(units.c.id).filter(units.c.status == "available")),
            sa.asc(sa.func.avg(ranking_scores.c.score).filter(units.c.status == "available")).nulls_last(),
            areas.c.area_name,
        )
        .limit(limit)
    )
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    return {
        "project_id": project["external_id"],
        "project_name": project["name"],
        "low_score_threshold": low_score_threshold,
        "areas": [
            {
                "area_id": r["external_id"],
                "name": r["area_name"],
                "unit_type": r["unit_type"],
                "bedrooms": r["bedrooms"],
                "available": int(r["available"]),
                "ranked_available": int(r["ranked_available"]),
                "avg_score": float(r["avg_score"]) if r["avg_score"] is not None else None,
                "low_score_available": int(r["low_score_available"]),
                "min_score": float(r["min_score"]) if r["min_score"] is not None else None,
                "computed_at": r["computed_at"].isoformat() if r["computed_at"] else None,
            }
            for r in rows
        ],
    }


async def inventory_hotspots(project_id: str, allowed_external_ids=None, limit: int = 12) -> dict:
    """Rank areas by remaining sellable inventory and inventory concentration."""
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    query = (
        sa.select(
            areas.c.external_id,
            areas.c.area_name,
            areas.c.unit_type,
            areas.c.bedrooms,
            areas.c.area_sqm,
            sa.func.count(units.c.id).label("unit_count"),
            sa.func.count(units.c.id).filter(units.c.status == "available").label("available"),
            sa.func.count(units.c.id).filter(units.c.status == "reserved").label("reserved"),
            sa.func.count(units.c.id).filter(units.c.status == "sold").label("sold"),
            sa.func.count(units.c.id).filter(units.c.status == "blocked").label("blocked"),
            sa.func.max(units.c.source_updated_at).label("latest_unit_update"),
        )
        .select_from(areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))))
        .where(areas.c.project_id == project["id"])
        .group_by(areas.c.id)
        .order_by(sa.desc(sa.func.count(units.c.id).filter(units.c.status == "available")), areas.c.area_name)
        .limit(limit)
    )
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    items = []
    for r in rows:
        total = int(r["unit_count"] or 0)
        available = int(r["available"] or 0)
        reserved = int(r["reserved"] or 0)
        items.append(
            {
                "area_id": r["external_id"],
                "name": r["area_name"],
                "unit_type": r["unit_type"],
                "bedrooms": r["bedrooms"],
                "area_sqm": float(r["area_sqm"]),
                "unit_count": total,
                "available": available,
                "reserved": reserved,
                "sold": int(r["sold"] or 0),
                "blocked": int(r["blocked"] or 0),
                "available_ratio": round(available / total, 4) if total else None,
                "reserved_ratio": round(reserved / total, 4) if total else None,
                "latest_unit_update": r["latest_unit_update"].isoformat() if r["latest_unit_update"] else None,
            }
        )
    return {"project_id": project["external_id"], "project_name": project["name"], "areas": items}


async def ranking_coverage(project_id: str, allowed_external_ids=None) -> dict:
    """Report whether available inventory has ranking scores and recent runs."""
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    area_query = (
        sa.select(
            areas.c.external_id,
            areas.c.area_name,
            areas.c.unit_type,
            sa.func.count(units.c.id).filter(units.c.status == "available").label("available"),
            sa.func.count(ranking_scores.c.id).filter(units.c.status == "available").label("ranked_available"),
            sa.func.avg(ranking_scores.c.weight_coverage).filter(units.c.status == "available").label("avg_weight_coverage"),
            sa.func.max(ranking_scores.c.computed_at).label("computed_at"),
        )
        .select_from(
            areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))).outerjoin(
                ranking_scores, ranking_scores.c.unit_id == units.c.id
            )
        )
        .where(areas.c.project_id == project["id"])
        .group_by(areas.c.id)
        .order_by(sa.desc(sa.func.count(units.c.id).filter(units.c.status == "available")), areas.c.area_name)
    )
    run_query = (
        sa.select(
            ranking_runs.c.id,
            ranking_runs.c.status,
            ranking_runs.c.trigger,
            ranking_runs.c.units_processed,
            ranking_runs.c.units_ranked,
            ranking_runs.c.units_skipped,
            ranking_runs.c.finished_at,
        )
        .where(ranking_runs.c.project_id == project["id"])
        .order_by(sa.desc(ranking_runs.c.finished_at).nulls_last(), sa.desc(ranking_runs.c.enqueued_at))
        .limit(1)
    )
    config_query = (
        sa.select(ranking_configs.c.version, ranking_configs.c.status, ranking_configs.c.min_weight_coverage)
        .where(ranking_configs.c.status == "published")
        .limit(1)
    )
    async with get_session_factory()() as session:
        area_rows = (await session.execute(area_query)).mappings().all()
        latest_run = (await session.execute(run_query)).mappings().first()
        config = (await session.execute(config_query)).mappings().first()
    total_available = sum(int(r["available"] or 0) for r in area_rows)
    total_ranked = sum(int(r["ranked_available"] or 0) for r in area_rows)
    return {
        "project_id": project["external_id"],
        "project_name": project["name"],
        "available_units": total_available,
        "ranked_available_units": total_ranked,
        "coverage_ratio": round(total_ranked / total_available, 4) if total_available else None,
        "published_config": dict(config) if config else None,
        "latest_run": {**dict(latest_run), "id": str(latest_run["id"])} if latest_run else None,
        "areas": [
            {
                "area_id": r["external_id"],
                "name": r["area_name"],
                "unit_type": r["unit_type"],
                "available": int(r["available"] or 0),
                "ranked_available": int(r["ranked_available"] or 0),
                "coverage_ratio": round(int(r["ranked_available"] or 0) / int(r["available"] or 1), 4)
                if int(r["available"] or 0)
                else None,
                "avg_weight_coverage": float(r["avg_weight_coverage"]) if r["avg_weight_coverage"] is not None else None,
                "computed_at": r["computed_at"].isoformat() if r["computed_at"] else None,
            }
            for r in area_rows
        ],
    }


async def reservation_pressure(project_id: str, allowed_external_ids=None, limit: int = 12) -> dict:
    """Show where available stock is being constrained by active reserved deals."""
    project = await _resolve_project(project_id, allowed_external_ids)
    if project is None:
        return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
    query = (
        sa.select(
            areas.c.external_id,
            areas.c.area_name,
            areas.c.unit_type,
            sa.func.count(units.c.id).label("unit_count"),
            sa.func.count(units.c.id).filter(units.c.status == "available").label("available"),
            sa.func.count(units.c.id).filter(units.c.status == "reserved").label("reserved_units"),
            sa.func.count(deals.c.id).filter(sa.and_(deals.c.status == "reserved", deals.c.deleted_at.is_(None))).label(
                "active_reserved_deals"
            ),
            sa.func.max(deals.c.reserved_at).label("latest_reserved_at"),
        )
        .select_from(
            areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))).outerjoin(
                deals, deals.c.unit_id == units.c.id
            )
        )
        .where(areas.c.project_id == project["id"])
        .group_by(areas.c.id)
        .order_by(
            sa.desc(sa.func.count(deals.c.id).filter(sa.and_(deals.c.status == "reserved", deals.c.deleted_at.is_(None)))),
            sa.desc(sa.func.count(units.c.id).filter(units.c.status == "reserved")),
            areas.c.area_name,
        )
        .limit(limit)
    )
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    return {
        "project_id": project["external_id"],
        "project_name": project["name"],
        "areas": [
            {
                "area_id": r["external_id"],
                "name": r["area_name"],
                "unit_type": r["unit_type"],
                "unit_count": int(r["unit_count"] or 0),
                "available": int(r["available"] or 0),
                "reserved_units": int(r["reserved_units"] or 0),
                "active_reserved_deals": int(r["active_reserved_deals"] or 0),
                "reserved_ratio": round(int(r["reserved_units"] or 0) / int(r["unit_count"] or 1), 4)
                if int(r["unit_count"] or 0)
                else None,
                "latest_reserved_at": r["latest_reserved_at"].isoformat() if r["latest_reserved_at"] else None,
            }
            for r in rows
        ],
    }


async def policy_snapshot(project_id: str | None = None, allowed_external_ids=None) -> dict:
    """Return the current allow-listed sales policy snapshot from the app policy file."""
    if project_id is not None:
        project = await _resolve_project(project_id, allowed_external_ids)
        if project is None:
            return {"error": "PROJECT_NOT_FOUND_OR_OUT_OF_SCOPE"}
        resolved_project = {"project_id": project["external_id"], "project_name": project["name"]}
    else:
        resolved_project = None
    try:
        payload = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": "POLICY_FILE_NOT_FOUND"}
    return {
        "project": resolved_project,
        "version": payload.get("version"),
        "updated_at": payload.get("updated_at"),
        "base_policy": payload.get("base_policy"),
        "rules": payload.get("rules", []),
        "approved_changes": payload.get("approved_changes", []),
        "note": "Policy is read-only in chat; any change must become a pending recommendation and pass human approval.",
    }


async def collect_advisory_context(message: str, project_id: str | None, allowed_external_ids=None):
    """Select and invoke compact read tools based on the user's intent."""
    normalized = message.casefold()
    context: dict = {}
    calls: list[str] = []
    if project_id is None or any(term in normalized for term in PORTFOLIO_TERMS):
        context["portfolio"] = await portfolio_overview(allowed_external_ids)
        calls.append("portfolio_overview")
    if project_id:
        context["project"] = await project_overview(project_id, allowed_external_ids)
        calls.append("project_overview")
        if any(term in normalized for term in AREA_TERMS):
            context["area_comparison"] = await compare_areas(project_id, allowed_external_ids)
            calls.append("compare_areas")
        if any(term in normalized for term in RANKING_TERMS):
            context["ranking"] = await top_ranked_units(project_id, allowed_external_ids)
            calls.append("top_ranked_units")
    as_of = datetime.now(UTC).isoformat()
    sources = [{"tool": call, "source": "PostgreSQL", "as_of": as_of} for call in calls]
    return context, calls, sources


def render_advisory_answer(message: str, context: dict) -> str:
    """Render a grounded Vietnamese answer locally; no DB data leaves the API."""
    lines: list[str] = []
    portfolio = context.get("portfolio")
    if portfolio:
        lines.append(f"## Danh mục dự án\n\nHệ thống hiện theo dõi **{portfolio['project_count']} dự án**.")
        for project in portfolio["projects"]:
            lines.append(f"- **{project['name']}** (`{project['project_id']}`): {project['unit_count']:,} căn")

    project = context.get("project")
    if project and not project.get("error"):
        counts = project["status_counts"]
        lines.append(
            f"## {project['project_name']}\n\n"
            f"- Quy mô đang theo dõi: **{project['unit_count']:,} căn** tại **{project['area_count']} phân khu**\n"
            f"- Available: **{counts.get('available', 0):,}**\n"
            f"- Reserved: **{counts.get('reserved', 0):,}**\n"
            f"- Sold: **{counts.get('sold', 0):,}**\n"
            f"- Blocked: **{counts.get('blocked', 0):,}**"
        )

    comparison = context.get("area_comparison", {}).get("areas", [])
    if comparison:
        lines.append(
            "## So sánh phân khu\n\n| Phân khu | Loại căn | Quy mô | Còn lại | Tốc độ 30 ngày |\n|---|---:|---:|---:|---:|"
        )
        for area in comparison[:12]:
            velocity = "—" if area["velocity_30d"] is None else f"{area['velocity_30d']:.2f}"
            remaining = "—" if area["units_remaining"] is None else f"{area['units_remaining']:,}"
            lines.append(
                f"| {area['name']} | {area['unit_type']} | {area['unit_count']:,} | {remaining} | {velocity} |"
            )
        slow = [a for a in comparison if a["velocity_30d"] is not None]
        if slow:
            weakest = min(slow, key=lambda item: item["velocity_30d"])
            lines.append(
                f"\n**Nhận định:** {weakest['name']} · {weakest['unit_type']} đang có tốc độ 30 ngày thấp nhất "
                f"trong dữ liệu so sánh ({weakest['velocity_30d']:.2f}). Nên kiểm tra tồn kho và chất lượng dữ liệu trước khi tạo chính sách."
            )

    ranking = context.get("ranking", {}).get("items", [])
    if ranking:
        lines.append("## Căn nên ưu tiên\n\n| Hạng | Căn | Phân khu | Điểm |\n|---:|---|---|---:|")
        for item in ranking:
            lines.append(f"| {item['rank']} | {item['unit_code']} | {item['area_name']} | {item['score']:.2%} |")
        lines.append(
            "\nBạn có thể dùng nút **Tạo đề xuất** để đưa danh sách này vào luồng phê duyệt. "
            "Chỉ sau khi Admin duyệt, hệ thống mới cho phép tạo chiến dịch ưu tiên bán."
        )

    if not lines:
        lines.append(
            "Mình chưa có đủ ngữ cảnh dự án để phân tích. Hãy chọn một dự án, sau đó bạn có thể hỏi về "
            "quy mô, tồn kho, so sánh phân khu, xu hướng hấp thụ hoặc danh sách căn ưu tiên."
        )
    return "\n\n".join(lines)


def _parse_tool_plan(text: str) -> list[str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        payload = json.loads(cleaned.strip())
    except (json.JSONDecodeError, TypeError):
        return []
    tools = payload.get("tools", []) if isinstance(payload, dict) else []
    return [name for name in tools if name in ALLOWED_ADVISORY_TOOLS]


async def _execute_tool_plan(tool_names: list[str], project_id: str | None, allowed_external_ids=None):
    context: dict = {}
    calls: list[str] = []
    for name in tool_names:
        if name == "portfolio_overview":
            context["portfolio"] = await portfolio_overview(allowed_external_ids)
        elif name == "project_overview" and project_id:
            context["project"] = await project_overview(project_id, allowed_external_ids)
        elif name == "compare_areas" and project_id:
            context["area_comparison"] = await compare_areas(project_id, allowed_external_ids)
        elif name == "top_ranked_units" and project_id:
            context["ranking"] = await top_ranked_units(project_id, allowed_external_ids)
        elif name == "unit_mix_overview" and project_id:
            context["unit_mix"] = await unit_mix_overview(project_id, allowed_external_ids)
        elif name == "area_ranking_risks" and project_id:
            context["area_ranking_risks"] = await area_ranking_risks(project_id, allowed_external_ids)
        elif name == "inventory_hotspots" and project_id:
            context["inventory_hotspots"] = await inventory_hotspots(project_id, allowed_external_ids)
        elif name == "ranking_coverage" and project_id:
            context["ranking_coverage"] = await ranking_coverage(project_id, allowed_external_ids)
        elif name == "reservation_pressure" and project_id:
            context["reservation_pressure"] = await reservation_pressure(project_id, allowed_external_ids)
        elif name == "policy_snapshot":
            context["policy"] = await policy_snapshot(project_id, allowed_external_ids)
        else:
            continue
        calls.append(name)
    return context, calls


async def run_advisory_agent(message: str, project_id: str | None, allowed_external_ids=None):
    """GPT plans tools, the API executes them, then GPT synthesizes facts."""
    if project_id is None:
        project_id = await _infer_project_id_from_message(message, allowed_external_ids)
    planner_prompt = f"""Bạn là planner của AI Agent bất động sản.
Chỉ trả JSON hợp lệ dạng {{"tools": ["tool_name"]}}; không giải thích.
Tool được phép:
- portfolio_overview: số lượng và danh sách dự án
- project_overview: tổng quan trạng thái căn trong dự án đã chọn
- compare_areas: so sánh quy mô, tồn kho, velocity giữa các phân khu
- top_ranked_units: top căn available theo ranking
- unit_mix_overview: thống kê số căn theo loại căn/phòng ngủ/trạng thái trong dự án
- area_ranking_risks: tìm phân khu còn nhiều căn available nhưng điểm ranking thấp
- inventory_hotspots: phân khu có tồn kho/căn available cao, tỷ lệ available/reserved/sold/blocked
- ranking_coverage: kiểm tra độ phủ ranking cho căn available, latest ranking run và config published
- reservation_pressure: phân khu có nhiều căn/deal đang giữ chỗ, áp lực booking/reserved
- policy_snapshot: chính sách chiết khấu/ưu đãi hiện hành từ file chính sách nội bộ
project_id hiện tại: {project_id or "chưa chọn"}
Nếu chưa chọn project_id thì chỉ được dùng portfolio_overview.
Nếu đã chọn project_id và câu hỏi không yêu cầu danh mục hoặc so sánh dự án, không chọn portfolio_overview.
Với câu hỏi về số căn 1PN/2PN/3PN/loại căn/phòng ngủ, chọn unit_mix_overview.
Với câu hỏi về phân khu nhiều căn còn lại nhưng ranking thấp hoặc cần kiểm tra chính sách bán hàng, chọn area_ranking_risks.
Với câu hỏi về tồn kho/còn hàng/quỹ hàng/hotspot, chọn inventory_hotspots.
Với câu hỏi về độ phủ ranking/chưa có điểm ranking/chất lượng ranking, chọn ranking_coverage.
Với câu hỏi về booking/reserved/giữ chỗ/deal đang giữ, chọn reservation_pressure.
Với câu hỏi về chính sách/chiết khấu/ưu đãi/hỗ trợ thanh toán, chọn policy_snapshot.
Với câu hỏi về rủi ro, tồn kho, bán chậm hoặc quyết định bán hàng, chọn project_overview, compare_areas và top_ranked_units.
Câu hỏi người dùng: {message}
"""
    plan_text, planner_usage = await generate_content(planner_prompt, max_output_tokens=256, thinking_budget=0)
    tool_names = _sanitize_tool_plan(_parse_tool_plan(plan_text), message, project_id)
    if not tool_names:
        # Guardrail khi model không tuân JSON: dùng tool tối thiểu đúng scope;
        # GPT thật vẫn là thành phần tổng hợp câu trả lời cuối.
        tool_names = _deterministic_tool_plan(message, project_id)
    context, calls = await _execute_tool_plan(tool_names, project_id, allowed_external_ids)
    if not calls:
        fallback = ["project_overview"] if project_id else ["portfolio_overview"]
        context, calls = await _execute_tool_plan(fallback, project_id, allowed_external_ids)

    synthesis_prompt = f"""Bạn là AI tư vấn bán hàng bất động sản cho chủ đầu tư.
Trả lời bằng tiếng Việt, Markdown rõ ràng, ưu tiên bảng khi so sánh nhiều phân khu.
Chỉ dùng số liệu có trong TOOL_RESULTS; không bịa giá, doanh thu, xu hướng thị trường, đối thủ cạnh tranh, tiến độ bàn giao, tiện ích hoặc chính sách nếu TOOL_RESULTS không có dữ liệu đó.
Nếu người dùng hỏi trong phạm vi một dự án đang chọn, chỉ phân tích dự án đó. Không tự so sánh với dự án khác, trừ khi câu hỏi yêu cầu so sánh rõ ràng và TOOL_RESULTS có dữ liệu nhiều dự án.
Khi tóm tắt rủi ro bán hàng, chỉ được nêu rủi ro suy ra trực tiếp từ các trường có trong TOOL_RESULTS: tồn kho, trạng thái căn, velocity 7 ngày/30 ngày, ranking, data_quality và thời điểm snapshot. Nếu thiếu dữ liệu thì nói rõ "chưa đủ dữ liệu để kết luận".
Khi TOOL_RESULTS có ranking_coverage, phải phân biệt rõ "ranking thấp" với "chưa có điểm ranking"; không được coi thiếu điểm là điểm thấp.
Khi TOOL_RESULTS có policy, chỉ tóm tắt chính sách hiện hành; không nói đã áp dụng/sửa chính sách nếu chưa có đề xuất được duyệt và thực thi.
Nói rõ dữ liệu thiếu khi không thể kết luận. Không gọi dữ liệu là thời gian thực; dùng 'snapshot hiện tại'.
Không tuyên bố đã thay đổi database. Mọi đề xuất thay đổi phải nói rõ cần phê duyệt và bấm Thực thi.
Không hướng dẫn bỏ qua human-in-the-loop.
Không thêm câu kêu gọi phê duyệt/Thực thi ở cuối nếu người dùng chỉ hỏi phân tích.

Câu hỏi: {message}
Project đang chọn: {project_id or "chưa chọn"}
Tool đã chạy: {json.dumps(calls, ensure_ascii=False)}
TOOL_RESULTS:
{json.dumps(context, ensure_ascii=False, default=str)}
"""
    # Advisory answers are grounded summaries, so disabling hidden thinking and
    # bounding output keeps the interactive request comfortably below the API
    # timeout while preserving enough room for comparison tables.
    response, synthesis_usage = await generate_content(
        synthesis_prompt,
        max_output_tokens=1024,
        thinking_budget=0,
    )
    as_of = datetime.now(UTC).isoformat()
    sources = [{"tool": call, "source": "PostgreSQL", "as_of": as_of} for call in calls]
    usage = {"planner": planner_usage, "synthesis": synthesis_usage}
    return response, calls, sources, usage
