from fastapi import APIRouter, HTTPException

from src.logging_config import get_logger, new_error_id
from src.models.schemas import (
    ChatRequest,
    ChatResponse,
    PhaseAddRequest,
    PhaseChangeRequest,
    ProposalDecisionRequest,
    ProposalGenerateRequest,
    ScenarioRunRequest,
)
from src.services.gemini import GeminiServiceError, ask_gemini
from src.services.market import market_repository

router = APIRouter()
log = get_logger("src.api.routes")


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat với AI agent."""
    try:
        context = market_repository.snapshot()
        context["active_units"] = context["active_units"][:30]
        context["open_proposals"] = [p for p in market_repository.proposals if p["status"] == "open"][:3]
        response, usage = await ask_gemini(request.message, context)
        return ChatResponse(response=response, analysis="", status="completed", usage=usage)
    except GeminiServiceError as e:
        raise HTTPException(status_code=e.status_code, detail={"message": e.user_message, "code": e.code}) from e
    except Exception as e:
        error_id = new_error_id()
        log.error("chat.failed", error_id=error_id, error_type=type(e).__name__, exc_info=e)
        raise HTTPException(status_code=500, detail={"message": "Agent error", "error_id": error_id}) from e


@router.get("/status")
async def agent_status():
    return {"status": "ready", "agent": "Gemini Market Agent", "provider": "gemini"}


@router.get("/market/dashboard")
async def market_dashboard():
    return market_repository.snapshot()


@router.get("/market/units")
async def market_units():
    return {"items": market_repository.units, "total": len(market_repository.units), "data_mode": "simulation"}


@router.get("/market/policies")
async def market_policies():
    return market_repository.policy


@router.post("/market/phase")
async def change_phase(request: PhaseChangeRequest):
    try:
        return market_repository.change_phase(request.direction, request.confirmed, request.actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "message": "Cần Admin xác nhận thay đổi giai đoạn."}) from exc


@router.post("/market/phases")
async def add_phase(request: PhaseAddRequest):
    try:
        return market_repository.add_phase(request.kind, request.confirmed, request.actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "message": "Cần Admin xác nhận thêm giai đoạn."}) from exc


@router.get("/market/scenarios")
async def scenarios():
    return {"items": market_repository.scenarios, "current_phase": market_repository.snapshot()["phase"]}


@router.post("/market/scenarios/run")
async def run_scenario(request: ScenarioRunRequest):
    try:
        return market_repository.run_scenario(request.scenario_id, request.intensity, request.confirmed, request.actor)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "message": "Kịch bản không thể chạy trong giai đoạn hiện tại."}) from exc


@router.get("/market/proposals")
async def proposals():
    return {"items": market_repository.proposals}


@router.post("/market/proposals/generate")
async def generate_proposal(request: ProposalGenerateRequest):
    return market_repository.generate_proposal(request.prompt, request.actor)


@router.post("/market/proposals/{proposal_id}/decision")
async def decide_proposal(proposal_id: str, request: ProposalDecisionRequest):
    try:
        return market_repository.decide(proposal_id, request.decision, request.reason, request.confirmed, request.actor, request.unit_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Proposal not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc), "message": "Đề xuất cần Admin xác nhận."}) from exc


@router.get("/market/audit")
async def audit_logs():
    return {"items": market_repository.logs}
