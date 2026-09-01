"""Read-only AI Agent API backed by P-100 ranking data."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.agents import memory
from src.agents.graph import answer
from src.models.schemas import ChatResponse
from src.services.dashboard_auth import DashboardPrincipal, require_role

router = APIRouter(prefix="/agent", tags=["agent"])
require_viewer = require_role("business_viewer")


class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=100)


def _scope(principal: DashboardPrincipal) -> set[str] | None:
    return None if principal.project_scope == "ALL" else set(principal.project_scope)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: AgentChatRequest,
    project_id: str | None = Query(default=None, description="external project ID, for example P-0001"),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> ChatResponse:
    session_id = request.session_id or memory.new_session_id()
    result = await answer(request.message, project_id, _scope(principal), memory.history(session_id))
    memory.append(session_id, request.message, result["answer"])
    context: dict[str, Any] = result.get("context", {})
    project = context.get("project") or {}
    evidence = context.get("evidence_answer") or {}
    evidence_sources = [
        {
            "type": "project_evidence",
            "marker": citation.get("marker"),
            "document_id": citation.get("document_id"),
            "document_title": citation.get("document_title"),
            "page": citation.get("page"),
            "citation_type": citation.get("citation_type"),
        }
        for citation in evidence.get("citations", [])
    ]
    return ChatResponse(
        response=result["answer"],
        status="completed",
        tool_calls=["read_only_project_analytics"] + (["project_evidence_rag"] if evidence else []),
        sources=[
            {
                "type": "p100_database",
                "project_id": project.get("project_id"),
                "formula": "ranking_scores snapshot",
                "ranking_model": "Hierarchical AHP/RGMM v3",
            }
        ] + evidence_sources,
        resolved_project_id=project.get("project_id"),
        session_id=session_id,
    )


@router.get("/status")
async def status(principal: DashboardPrincipal = Depends(require_viewer)):
    return {"status": "ready", "agent": "AbsorpIQ read-only Agent", "provider": "openai-compatible", "model": "configured"}


@router.get("/sessions/{session_id}")
async def session_history(session_id: str, principal: DashboardPrincipal = Depends(require_viewer)):
    """Restore a conversation after the Agent page is unmounted by navigation."""
    try:
        return {"session_id": session_id, "messages": memory.history(session_id)}
    except ValueError:
        return {"session_id": session_id, "messages": []}
