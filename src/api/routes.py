from fastapi import APIRouter, HTTPException

from src.agents.graph import agent
from src.logging_config import get_logger, new_error_id
from src.models.schemas import ChatRequest, ChatResponse

router = APIRouter()
log = get_logger("src.api.routes")


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat với AI agent."""
    try:
        result = await agent.ainvoke({"query": request.message})
        return ChatResponse(
            response=result.get("response", ""),
            analysis=result.get("analysis", ""),
        )
    except Exception as e:
        # Không trả str(e) cho client — có thể chứa DSN / mảnh API key.
        error_id = new_error_id()
        log.error(
            "chat.failed",
            error_id=error_id,
            error_type=type(e).__name__,
            exc_info=e,
        )
        raise HTTPException(
            status_code=500,
            detail={"message": "Agent error", "error_id": error_id},
        ) from e


@router.get("/status")
async def agent_status():
    """Kiểm tra trạng thái agent."""
    return {"status": "ready", "agent": "LangGraph Agent v1.0"}
