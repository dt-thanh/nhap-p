from fastapi import APIRouter, Depends, HTTPException, Query

from src.logging_config import get_logger
from src.models.schemas import (
    PhaseAddRequest,
    PhaseChangeRequest,
    ProposalDecisionRequest,
    ProposalGenerateRequest,
    ScenarioRunRequest,
)
from src.services.dashboard_auth import DashboardPrincipal, require_project_in_scope, require_role
from src.services.market import market_repository

router = APIRouter()
log = get_logger("src.api.routes")
require_viewer = require_role("business_viewer")


def _allowed_external_ids(principal: DashboardPrincipal):
    return None if principal.project_scope == "ALL" else principal.project_scope


def _enforce_requested_project_scope(principal: DashboardPrincipal, project_id: str | None) -> None:
    if project_id:
        require_project_in_scope(principal, project_id)


@router.get("/market/dashboard")
async def market_dashboard(
    project_id: str | None = Query(default=None, description="Project external_id or internal UUID"),
    principal: DashboardPrincipal = Depends(require_viewer),
):
    _enforce_requested_project_scope(principal, project_id)
    return await market_repository.snapshot(project_id, allowed_external_ids=_allowed_external_ids(principal))


@router.get("/market/units")
async def market_units(
    project_id: str | None = Query(default=None, description="Project external_id or internal UUID"),
    principal: DashboardPrincipal = Depends(require_viewer),
):
    _enforce_requested_project_scope(principal, project_id)
    return await market_repository.units(project_id, allowed_external_ids=_allowed_external_ids(principal))


@router.get("/market/policies")
async def market_policies(principal: DashboardPrincipal = Depends(require_viewer)):
    return market_repository.policies()


@router.post("/market/phase")
async def change_phase(request: PhaseChangeRequest, principal: DashboardPrincipal = Depends(require_viewer)):
    try:
        return market_repository.change_phase(request.direction, request.confirmed, request.actor)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": str(exc), "message": "Market phase changes are not written by the backend DB view."},
        ) from exc


@router.post("/market/phases")
async def add_phase(request: PhaseAddRequest, principal: DashboardPrincipal = Depends(require_viewer)):
    try:
        return market_repository.add_phase(request.kind, request.confirmed, request.actor)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": str(exc),
                "message": "Market phases must come from a real source table, not local simulation.",
            },
        ) from exc


@router.get("/market/scenarios")
async def scenarios(principal: DashboardPrincipal = Depends(require_viewer)):
    return {"items": market_repository.scenarios, "current_phase": await market_repository.current_phase()}


@router.post("/market/scenarios/run")
async def run_scenario(request: ScenarioRunRequest, principal: DashboardPrincipal = Depends(require_viewer)):
    try:
        return await market_repository.run_scenario(
            request.scenario_id, request.intensity, request.confirmed, request.actor
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": str(exc),
                "message": "Scenario mutation is disabled because market data is database-backed.",
            },
        ) from exc


@router.get("/market/proposals")
async def proposals(
    project_id: str | None = Query(default=None, description="Project external_id or internal UUID"),
    principal: DashboardPrincipal = Depends(require_viewer),
):
    _enforce_requested_project_scope(principal, project_id)
    return await market_repository.proposals(project_id, allowed_external_ids=_allowed_external_ids(principal))


@router.post("/market/proposals/generate")
async def generate_proposal(
    request: ProposalGenerateRequest,
    project_id: str | None = Query(default=None, description="Project external_id or internal UUID"),
    principal: DashboardPrincipal = Depends(require_viewer),
):
    _enforce_requested_project_scope(principal, project_id)
    return await market_repository.generate_proposal(
        request.prompt, request.actor, project_id, allowed_external_ids=_allowed_external_ids(principal)
    )


@router.post("/market/proposals/{proposal_id}/decision")
async def decide_proposal(
    proposal_id: str, request: ProposalDecisionRequest, principal: DashboardPrincipal = Depends(require_viewer)
):
    try:
        return await market_repository.decide(
            proposal_id, request.decision, request.reason, request.confirmed, request.actor, request.unit_ids
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Proposal not found") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": str(exc), "message": "Use /agent/recommendations/{id}/approve for HITL decisions."},
        ) from exc

