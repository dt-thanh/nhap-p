from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

from src.db import get_session_factory
from src.models.tables import agent_recommendations, areas, deals, projects, ranking_scores, units

POLICY_FILE = Path(__file__).resolve().parents[2] / "data" / "discount_policies.json"
DATA_MODE = "database"


def _phase_snapshot() -> dict:
    return {"id": "db_snapshot", "label": "Database snapshot", "kind": "database", "release": None}


def _status_label(status: str | None) -> str:
    labels = {
        "available": "Available",
        "reserved": "Reserved",
        "sold": "Sold",
        "blocked": "Blocked",
        "lost": "Lost",
        "cancelled": "Cancelled",
    }
    return labels.get((status or "").lower(), status or "Unknown")


def _market_status(unit_status: str | None, deal_status: str | None) -> str:
    if deal_status in {"sold", "reserved"}:
        return deal_status
    return (unit_status or "unknown").lower()


def _score_percent(score) -> int | None:
    if score is None:
        return None
    return round(float(score) * 100)


def _uuid_or_none(value: str | None):
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _unit_payload(row: dict) -> dict:
    market_status = _market_status(row.get("unit_status"), row.get("deal_status"))
    return {
        "id": row["unit_code"],
        "unit_uuid": str(row["unit_id"]),
        "area_id": str(row["area_id"]),
        "area_external_id": row.get("area_external_id"),
        "tower": row["area_name"],
        "floor": None,
        "type": row["unit_type"],
        "area": float(row["area_sqm"]) if row.get("area_sqm") is not None else None,
        "view": None,
        "price": None,
        "score": _score_percent(row.get("score")),
        "rank_in_project": row.get("rank_in_project"),
        "rank_in_area": row.get("rank_in_area"),
        "trend": None,
        "status": _status_label(market_status),
        "source_status": row.get("unit_status"),
        "deal_status": row.get("deal_status"),
        "release": None,
        "phase_id": "db_snapshot",
    }


class DatabaseMarketRepository:
    """Read-only market view backed by PostgreSQL, not generated demo data."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or get_session_factory()
        self.policy = self._load_policy()
        self.scenarios = [
            {
                "id": "db_snapshot_only",
                "name": "Database snapshot only",
                "phase": "database",
                "description": "Market data is read from PostgreSQL; scenario mutation is disabled.",
            }
        ]
        self.logs: list[dict] = []

    async def snapshot(self, project_id: str | None = None, *, allowed_external_ids=None) -> dict:
        project = await self._load_project(project_id, allowed_external_ids=allowed_external_ids)
        if project is None:
            return self._empty_snapshot()

        active_units = await self.units_for_project(project["id"])
        metrics = self._metrics(active_units)
        phase = _phase_snapshot()
        return {
            "project": self._project_payload(project),
            "phase": phase,
            "phase_index": 0,
            "phases": [phase],
            "active_units": active_units,
            "policy": self.policy,
            "metrics": metrics,
        }

    async def units(self, project_id: str | None = None, *, allowed_external_ids=None) -> dict:
        project = await self._load_project(project_id, allowed_external_ids=allowed_external_ids)
        if project is None:
            return {"items": [], "total": 0, "data_mode": DATA_MODE, "project": None}
        items = await self.units_for_project(project["id"])
        return {"items": items, "total": len(items), "data_mode": DATA_MODE, "project": self._project_payload(project)}

    async def units_for_project(self, project_id) -> list[dict]:
        deal_status = (
            sa.select(
                deals.c.unit_id,
                sa.func.max(
                    sa.case((deals.c.status == "sold", 3), (deals.c.status == "reserved", 2), else_=0)
                ).label("deal_priority"),
            )
            .where(deals.c.deleted_at.is_(None), deals.c.status.in_(("reserved", "sold")))
            .group_by(deals.c.unit_id)
            .subquery()
        )
        deal_status_text = sa.case(
            (deal_status.c.deal_priority == 3, "sold"),
            (deal_status.c.deal_priority == 2, "reserved"),
            else_=None,
        ).label("deal_status")
        query = (
            sa.select(
                units.c.id.label("unit_id"),
                units.c.unit_code,
                units.c.unit_type,
                units.c.status.label("unit_status"),
                units.c.area_id,
                areas.c.external_id.label("area_external_id"),
                areas.c.area_name,
                areas.c.area_sqm,
                ranking_scores.c.score,
                ranking_scores.c.rank_in_project,
                ranking_scores.c.rank_in_area,
                deal_status_text,
            )
            .select_from(
                units.join(areas, units.c.area_id == areas.c.id)
                .outerjoin(ranking_scores, ranking_scores.c.unit_id == units.c.id)
                .outerjoin(deal_status, deal_status.c.unit_id == units.c.id)
            )
            .where(areas.c.project_id == project_id, units.c.deleted_at.is_(None))
            .order_by(
                sa.asc(ranking_scores.c.rank_in_project).nulls_last(),
                areas.c.area_name,
                units.c.unit_code,
            )
        )
        async with self._session_factory() as session:
            rows = (await session.execute(query)).mappings().all()
        return [_unit_payload(dict(row)) for row in rows]

    async def proposals(self, project_id: str | None = None, *, allowed_external_ids=None) -> dict:
        project = await self._load_project(project_id, allowed_external_ids=allowed_external_ids)
        if project is None:
            return {"items": [], "project": None}
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        sa.select(agent_recommendations)
                        .where(agent_recommendations.c.project_id == project["id"])
                        .order_by(agent_recommendations.c.generated_at.desc())
                        .limit(20)
                    )
                )
                .mappings()
                .all()
            )
        return {"items": [self._recommendation_to_proposal(dict(row), project) for row in rows], "project": self._project_payload(project)}

    async def generate_proposal(self, prompt: str, actor: str, project_id: str | None = None, *, allowed_external_ids=None) -> dict:
        snapshot = await self.snapshot(project_id, allowed_external_ids=allowed_external_ids)
        candidates = [u for u in snapshot["active_units"] if u["status"] == "Available"][:8]
        return {
            "id": f"db-preview-{uuid4().hex[:8]}",
            "status": "pending_approval_required",
            "action_type": "agent_recommendation",
            "title": "Create DB-backed agent recommendation",
            "summary": "Use POST /api/v1/agent/recommendations so ranking is recomputed from DB and stored pending approval.",
            "admin_prompt": prompt,
            "recommended_unit_ids": [u["id"] for u in candidates],
            "discount_percent": None,
            "target_release": None,
            "evidence": [
                f"Data mode: {snapshot['project']['data_mode']}",
                f"Project: {snapshot['project']['id']}",
                "Final recommendation must be created and approved through the HITL agent workflow.",
            ],
            "created_by": actor,
            "created_at": datetime.now(UTC).isoformat(),
        }

    def policies(self) -> dict:
        return self.policy

    async def current_phase(self) -> dict:
        return _phase_snapshot()

    def change_phase(self, direction, confirmed, actor):
        if not confirmed:
            raise ValueError("confirmation_required")
        raise ValueError("db_source_read_only")

    def add_phase(self, kind, confirmed, actor):
        if not confirmed:
            raise ValueError("confirmation_required")
        raise ValueError("db_source_read_only")

    async def run_scenario(self, scenario_id, intensity, confirmed, actor):
        if not confirmed:
            raise ValueError("confirmation_required")
        raise ValueError("db_source_read_only")

    async def decide(self, proposal_id, decision, reason, confirmed, actor, unit_ids=None):
        if not confirmed:
            raise ValueError("confirmation_required")
        raise ValueError("use_agent_recommendation_approval")

    async def _load_project(self, project_id: str | None = None, *, allowed_external_ids=None) -> dict | None:
        live_unit_counts = (
            sa.select(areas.c.project_id.label("project_id"), sa.func.count(units.c.id).label("live_units"))
            .select_from(areas.outerjoin(units, sa.and_(units.c.area_id == areas.c.id, units.c.deleted_at.is_(None))))
            .group_by(areas.c.project_id)
            .subquery()
        )
        query = sa.select(
            projects.c.id,
            projects.c.external_id,
            projects.c.name,
            projects.c.source_system,
            projects.c.source_instance_id,
            projects.c.source_revision,
            projects.c.source_updated_at,
            projects.c.updated_at,
            sa.func.coalesce(live_unit_counts.c.live_units, 0).label("live_units"),
        ).select_from(projects.outerjoin(live_unit_counts, live_unit_counts.c.project_id == projects.c.id))

        internal_uuid = _uuid_or_none(project_id)
        if project_id:
            query = query.where(projects.c.id == internal_uuid) if internal_uuid else query.where(projects.c.external_id == project_id)
        else:
            query = query.where(projects.c.status == "active", projects.c.external_id.isnot(None))
            if allowed_external_ids not in (None, "ALL"):
                if not allowed_external_ids:
                    return None
                query = query.where(projects.c.external_id.in_(allowed_external_ids))
            query = query.order_by(
                sa.func.coalesce(live_unit_counts.c.live_units, 0).desc(),
                projects.c.source_updated_at.desc().nulls_last(),
                projects.c.updated_at.desc(),
                projects.c.name,
            )
        query = query.limit(1)

        async with self._session_factory() as session:
            row = (await session.execute(query)).mappings().first()
        return dict(row) if row else None

    def _project_payload(self, project: dict) -> dict:
        return {
            "id": project.get("external_id") or str(project["id"]),
            "uuid": str(project["id"]),
            "name": project["name"],
            "data_mode": DATA_MODE,
            "source_system": project.get("source_system"),
            "source_instance_id": project.get("source_instance_id"),
            "source_revision": project.get("source_revision"),
            "source_updated_at": project.get("source_updated_at").isoformat() if project.get("source_updated_at") else None,
            "live_units": int(project.get("live_units") or 0),
        }

    def _empty_snapshot(self) -> dict:
        phase = _phase_snapshot()
        return {
            "project": {"id": None, "name": None, "data_mode": DATA_MODE, "live_units": 0},
            "phase": phase,
            "phase_index": 0,
            "phases": [phase],
            "active_units": [],
            "policy": self.policy,
            "metrics": self._metrics([]),
        }

    def _metrics(self, active_units: list[dict]) -> dict:
        total = len(active_units)
        sold = sum(u["status"] == "Sold" for u in active_units)
        reserved = sum(u["status"] == "Reserved" for u in active_units)
        blocked = sum(u["status"] == "Blocked" for u in active_units)
        available = sum(u["status"] == "Available" for u in active_units)
        scored = [u["score"] for u in active_units if u.get("score") is not None]
        return {
            "active_total": total,
            "booking": reserved,
            "available": available,
            "locked": blocked,
            "transacted": sold,
            "avg_signal": round(sum(scored) / len(scored)) if scored else None,
            "conversion_rate": round(sold / total * 100) if total else 0,
            "booking_rate": round(reserved / total * 100) if total else 0,
        }

    def _recommendation_to_proposal(self, row: dict, project: dict) -> dict:
        return {
            "id": str(row["id"]),
            "status": row["status"],
            "action_type": "agent_recommendation",
            "title": "DB-backed AI recommendation",
            "summary": row["summary"],
            "recommended_actions": row.get("recommended_actions") or [],
            "recommended_unit_ids": [],
            "discount_percent": None,
            "target_release": None,
            "evidence": [
                f"project_id={project.get('external_id') or project['id']}",
                f"ranking_run_id={row['ranking_run_id']}",
                "Stored in agent_recommendations and subject to human approval.",
            ],
            "created_at": row["generated_at"].isoformat() if row.get("generated_at") else None,
        }

    def _load_policy(self):
        if POLICY_FILE.exists():
            return json.loads(POLICY_FILE.read_text(encoding="utf-8-sig"))
        return {"project_id": None, "version": 1, "rules": [], "approved_changes": []}


market_repository = DatabaseMarketRepository()
