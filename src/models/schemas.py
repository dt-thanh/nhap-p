from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000, description="Tin nhắn từ user")


class ChatResponse(BaseModel):
    response: str = Field(..., description="Phản hồi từ agent")
    analysis: str = Field(default="", description="Phân tích nội bộ")
    status: str = "completed"
    usage: dict = Field(default_factory=dict)


class PhaseChangeRequest(BaseModel):
    direction: str = Field(pattern="^(next|previous)$")
    confirmed: bool
    actor: str = "Admin"


class PhaseAddRequest(BaseModel):
    kind: str = Field(pattern="^(booking|sale)$")
    confirmed: bool
    actor: str = "Admin"


class ScenarioRunRequest(BaseModel):
    scenario_id: str
    confirmed: bool
    actor: str = "Admin"
    intensity: int = Field(default=40, ge=5, le=100)


class ProposalGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=2000)
    actor: str = "Admin"


class ProposalDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = Field(min_length=2, max_length=500)
    confirmed: bool
    actor: str = "Admin"
    unit_ids: list[str] = Field(default_factory=list)
