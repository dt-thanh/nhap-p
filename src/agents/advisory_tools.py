"""Read-only database tools used by the advisory chat agent.

The language model receives only tool results selected for the question. It has
no database connection and cannot generate or execute SQL.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
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
from src.services import evidence_extraction, governance
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


# Business users should not need the technical term ranking.
RANKING_TERMS += ('nen ban', 'nen tu van', 'goi truoc', 'tap trung nguon luc')
RANKING_TERMS += ('phan bo doi sales', 'quy hang nao', 'can thuc day')
RANKING_TERMS += ('ban cham',)
AREA_RISK_TERMS += ('quy hang can thuc day', 'can can thiep', 'can ho tro ban', 'ban cham')
INVENTORY_TERMS += ('nhieu hang', 'quy can')
COVERAGE_TERMS += ('du du lieu', 'do tin cay', 'du de ra quyet dinh')


def _mentions_any(message: str, terms: tuple[str, ...]) -> bool:
    normalized = message.casefold()
    normalized_ascii = _normalize_project_text(message)
    return any(term in normalized or _normalize_project_text(term) in normalized_ascii for term in terms)


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


def _normalize_project_text(value: str) -> str:
    '''Normalize Vietnamese project names for accent-insensitive lookup.'''
    decomposed = unicodedata.normalize('NFKD', str(value).casefold()).replace('đ', 'd')
    without_marks = ''.join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', without_marks).strip()


def _project_aliases(external_id: str, name: str) -> set[str]:
    '''Return conservative aliases, e.g. Ocean Park for Vinhomes Ocean Park 1.'''
    aliases = {_normalize_project_text(external_id), _normalize_project_text(name)}
    tokens = _normalize_project_text(name).split()
    if len(tokens) >= 3 and tokens[0] in {'vinhomes', 'masterise', 'masteri'}:
        aliases.add(' '.join(tokens[1:]))
        tokens = tokens[1:]
    if len(tokens) >= 3 and tokens[-1].isdigit():
        aliases.add(' '.join(tokens[:-1]))
    return {alias for alias in aliases if len(alias) >= 4}


def _project_mentions_from_rows(message: str, rows) -> list[tuple[dict, int]]:
    normalized = f' {_normalize_project_text(message)} '
    matches: list[tuple[dict, int]] = []
    for row in rows:
        aliases = _project_aliases(str(row['external_id']), str(row['name']))
        matched = [alias for alias in aliases if f' {alias} ' in normalized]
        if matched:
            matches.append((row, max(len(alias) for alias in matched)))
    return matches


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
    query = sa.select(projects.c.external_id, projects.c.name).where(
        projects.c.status == "active", projects.c.external_id.isnot(None)
    )
    scope_clause = _scope_filter(allowed_external_ids)
    if scope_clause is not None:
        query = query.where(scope_clause)
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
    matches = _project_mentions_from_rows(message, rows)
    if not matches:
        return None
    best_score = max(score for _, score in matches)
    best = [row for row, score in matches if score == best_score]
    return str(best[0]["external_id"]) if len(best) == 1 else None


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
            ranking_scores.c.weight_coverage,
            ranking_scores.c.contributions,
            ranking_scores.c.computed_at,
            ranking_configs.c.version.label('config_version'),
            ranking_configs.c.weights.label('config_weights'),
        )
        .select_from(
            units.join(areas, units.c.area_id == areas.c.id).join(
                ranking_scores, ranking_scores.c.unit_id == units.c.id
            ).join(ranking_configs, ranking_configs.c.id == ranking_scores.c.config_version_id)
        )
        .where(areas.c.project_id == project["id"], units.c.deleted_at.is_(None), units.c.status == "available")
        .order_by(ranking_scores.c.rank_in_project)
        .limit(limit)
    )
    score_stats_query = (
        sa.select(
            sa.func.count(sa.distinct(ranking_scores.c.score)).label('distinct_scores'),
            sa.func.min(ranking_scores.c.score).label('min_score'),
            sa.func.max(ranking_scores.c.score).label('max_score'),
            sa.func.max(ranking_scores.c.computed_at).label('computed_at'),
        )
        .select_from(
            units.join(areas, units.c.area_id == areas.c.id).join(
                ranking_scores, ranking_scores.c.unit_id == units.c.id
            )
        )
        .where(
            areas.c.project_id == project['id'],
            units.c.deleted_at.is_(None),
            units.c.status == 'available',
        )
    )
    published_version_query = (
        sa.select(ranking_configs.c.version)
        .where(ranking_configs.c.status == 'published')
        .limit(1)
    )
    async with get_session_factory()() as session:
        rows = (await session.execute(query)).mappings().all()
        score_stats = (await session.execute(score_stats_query)).mappings().one()
        published_config_version = await session.scalar(published_version_query)
    ranking_meta = {
        'project_id': project['external_id'],
        'project_name': project['name'],
        'objective': (
            'Ưu tiên căn còn bán được dựa trên tín hiệu nhu cầu ở cấp căn và sức bán của phân khu; '
            'đây là xếp hạng vận hành tất định theo snapshot, không phải dự báo hay cam kết doanh số.'
        ),
        'config_version': rows[0]['config_version'] if rows else None,
        'config_weights': rows[0]['config_weights'] if rows else None,
        'published_config_version': published_config_version,
        'requires_recompute': bool(
            rows
            and published_config_version is not None
            and rows[0]['config_version'] != published_config_version
        ),
        'score_distribution': {
            'distinct_scores': int(score_stats['distinct_scores'] or 0),
            'min_score': float(score_stats['min_score']) if score_stats['min_score'] is not None else None,
            'max_score': float(score_stats['max_score']) if score_stats['max_score'] is not None else None,
            'computed_at': score_stats['computed_at'].isoformat() if score_stats['computed_at'] else None,
            'warning': (
                'Toàn bộ căn available đang đồng điểm; thứ hạng không thể hiện khác biệt ưu tiên đo được.'
                if int(score_stats['distinct_scores'] or 0) <= 1
                else None
            ),
        },
        'item_explanations': {
            str(row['id']): {
                'weight_coverage': float(row['weight_coverage']),
                'contributions': row['contributions'],
            }
            for row in rows
        },
    }
    return {
        **ranking_meta,
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
    # Scores are stored at four decimals; <= 0.3299 is equivalent to the
    # presentation band's strict score < 0.33 boundary.
    low_score_threshold = 0.3299
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
            sa.func.count(sa.distinct(units.c.id)).label("unit_count"),
            sa.func.count(sa.distinct(units.c.id)).filter(units.c.status == "available").label("available"),
            sa.func.count(sa.distinct(units.c.id)).filter(units.c.status == "reserved").label("reserved_units"),
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


# --- Governance evidence retrieval (§21.7-§21.8) ---------------------------------
#
# Separate from the sales-advisory flow above and NOT part of
# ALLOWED_ADVISORY_TOOLS' deterministic tool plan: these back the
# expert-governance "explain this weight change" reviewer panel (§21.9), a
# different consumer entirely. They never compute a score or choose a weight
# — same "agent synthesizes the explanation" role this file's other functions
# play for ranking, applied here to one expert justification instead.


async def get_feature_evidence(feature_justification_id: str) -> list[dict]:
    """SQL-only lookup of which documents are linked to a justification —
    embedding+search happens in `retrieve_and_validate`. Returns `[]`, never
    raises, for an unlinked or malformed id — the caller (prompt assembly)
    must render that as "no evidence uploaded", never omit the feature
    silently (hard constraint 15, §12.2)."""
    try:
        justification_uuid = uuid.UUID(feature_justification_id)
    except ValueError:
        return []
    return await governance.list_documents_for_justification(justification_uuid)


async def validate_evidence(chunk: dict, claim_project_id: str, claim_cutoff: datetime) -> bool:
    """Entity + time checks a justification-linked chunk can actually be
    checked against (§12.5, narrowed per §21.5): the chunk's document must
    belong to a proposal scoped to `claim_project_id`, and must have entered
    the system at or before `claim_cutoff`. Numeric-consistency (comparing
    prose to a specific SQL value) is deferred to the caller of the LLM
    output — it needs the claim's own number, which this function doesn't have.

    Takes the chunk ROW as returned by `evidence_extraction.search_similar_chunks`
    (already carries `document_id`), not a bare chunk id — avoids a second
    round trip for information the caller already has.
    """
    document = await evidence_extraction.get_document(chunk["document_id"])
    if document is None:
        return False
    if document["proposal_id"] is None:
        # Standalone upload, not linked to any proposal — cannot confirm it
        # scopes to claim_project_id. Fail closed rather than guess.
        return False
    proposal = await governance.get_proposal(document["proposal_id"])
    if str(proposal["project_id"]) != str(claim_project_id):
        return False
    if document["created_at"] > claim_cutoff:
        return False
    return True


async def retrieve_and_validate(
    feature_justification_id: str,
    claim_project_id: str,
    claim_cutoff: datetime,
    top_k: int = 5,
) -> list[dict]:
    """§21.7. Vector search restricted to documents already linked to THIS
    justification — never a corpus-wide query (R19, §21.11). Returns `[]`
    when nothing survives validation; the caller must render that as
    insufficient evidence, never a paraphrase (§12.5 "Sufficiency")."""
    try:
        justification_uuid = uuid.UUID(feature_justification_id)
    except ValueError:
        return []
    justification = await governance.get_justification(justification_uuid)
    if justification is None:
        return []

    document_ids = [doc["id"] for doc in await get_feature_evidence(feature_justification_id)]
    if not document_ids:
        return []

    query_vector = evidence_extraction.embed_texts([justification["evidence_summary"]])[0]
    # Over-fetch: validation below discards candidates that fail the checks.
    candidates = await evidence_extraction.search_similar_chunks(document_ids, query_vector, top_k=top_k * 4)

    validated: list[dict] = []
    for chunk in candidates:
        if await validate_evidence(chunk, claim_project_id, claim_cutoff):
            validated.append(chunk)
        if len(validated) >= top_k:
            break
    return validated


_EXPLANATION_SYSTEM_PROMPT = (
    "Bạn là trợ lý giải thích thay đổi trọng số xếp hạng. Bạn KHÔNG chọn trọng số, "
    "KHÔNG tính điểm, KHÔNG phê duyệt — chuyên gia đã làm việc đó ở "
    "ranking_feature_justifications; bạn chỉ diễn giải input đã có kèm bằng chứng "
    "được cung cấp. Mỗi câu chứa một con số hoặc một khẳng định thực tế PHẢI có "
    "đúng một trích dẫn ngay sau, dạng [J] cho justification hoặc [D#:p#] cho đoạn "
    "trích tài liệu thứ # ở trang #. KHÔNG bịa trích dẫn, KHÔNG trích đoạn ngoài "
    "danh sách bằng chứng đã được xác thực dưới đây. Nếu một feature không có bằng "
    "chứng nào được xác thực, nói rõ 'KHÔNG ĐỦ DỮ LIỆU' cho feature đó — không diễn "
    "giải khiên cưỡng thành có. Đây là bản GIẢI THÍCH một đề xuất đang CHỜ DUYỆT, "
    "không phải một trọng số đã có hiệu lực. Trả lời DUY NHẤT một object JSON:\n"
    '{"explanation": "<đoạn văn, mỗi câu số liệu có [J] hoặc [D#:p#]>", '
    '"citations": [{"marker": "D1:p3", "document_id": "<uuid>", "page": 3, "quote": "<nguyên văn đoạn trích>"}], '
    '"insufficient_evidence_features": ["<feature_key nếu có>"]}'
)


def _render_validated_chunks_block(chunks: list[dict], document_index: dict[str, int]) -> str:
    lines = []
    for chunk in chunks:
        marker_index = document_index[str(chunk["document_id"])]
        page = f":p{chunk['page_number']}" if chunk["page_number"] is not None else ""
        lines.append(f"[D{marker_index}{page}] {chunk['content']}")
    return "\n".join(lines)


def _parse_explanation_output(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        payload = json.loads(cleaned.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


async def generate_justification_explanation(
    feature_justification_id: str,
    claim_project_id: str,
    claim_cutoff: datetime,
    *,
    feature_key: str,
    top_k: int = 5,
) -> dict:
    """§21.8. Synthesizes prose from the expert's own justification fields
    plus retrieved-and-validated evidence chunks — never generates a number
    or a citation outside that validated set. Returns
    `insufficient_evidence_features=[feature_key]` (never a confident-sounding
    paraphrase) when nothing survives `retrieve_and_validate`, matching the
    same anti-fabrication discipline as `render_advisory_answer` below."""
    try:
        justification_uuid = uuid.UUID(feature_justification_id)
    except ValueError:
        return {"error": "INVALID_JUSTIFICATION_ID"}
    justification = await governance.get_justification(justification_uuid)
    if justification is None:
        return {"error": "JUSTIFICATION_NOT_FOUND"}

    validated_chunks = await retrieve_and_validate(
        feature_justification_id, claim_project_id, claim_cutoff, top_k=top_k
    )
    if not validated_chunks:
        return {"explanation": None, "citations": [], "insufficient_evidence_features": [feature_key]}

    document_ids = sorted({str(chunk["document_id"]) for chunk in validated_chunks})
    document_index = {doc_id: index + 1 for index, doc_id in enumerate(document_ids)}
    chunks_block = _render_validated_chunks_block(validated_chunks, document_index)

    user_prompt = (
        f"Feature: {feature_key} | Trọng số đề xuất: {justification['proposed_weight']} "
        f"(trước đó: {justification['previous_weight']})\n"
        f"Rationale (chuyên gia viết): {justification['rationale']}\n"
        f"Methodology: {justification['methodology']}\n"
        f"Evidence summary (chuyên gia viết): {justification['evidence_summary']}\n"
        f"Expected effect: {justification['expected_effect']} | Confidence: {justification['confidence']}\n"
        f"Limitations: {justification['limitations']}\n\n"
        "Bằng chứng đã xác thực (entity/date/geography/numeric-consistency đã qua §12.5):\n"
        f"{chunks_block}"
    )

    text, _usage = await generate_content(f"{_EXPLANATION_SYSTEM_PROMPT}\n\n{user_prompt}")
    payload = _parse_explanation_output(text)
    if payload is None:
        return {
            "explanation": None,
            "citations": [],
            "insufficient_evidence_features": [feature_key],
            "error": "LLM_OUTPUT_NOT_JSON",
        }
    return payload


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
    selected_project_name = (context.get('project') or {}).get('project_name')
    synthesis_prompt += f'''
CRITICAL OUTPUT RULES:
- Answer the user's exact business question in the first 1-2 sentences. Omit unrelated project overview.
- The only allowed project name is: {selected_project_name or project_id or 'none selected'}.
- When ranking data exists, explain priority from config_version, config_weights and contributions in TOOL_RESULTS.
- If score_distribution has one distinct score, say the units are tied and do not claim measured priority differences.
- If requires_recompute is true, say stored scores use an older config and do not present them as current priorities.
- Treat ranking as deterministic operational ordering, never as probability, forecast, or sales guarantee.
- Give at most three concrete actions, each tied to evidence in TOOL_RESULTS.
'''
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
