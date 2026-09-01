from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    project_id: str | None
    history: list[dict[str, str]]
    intent: str
    arguments: dict[str, Any]
    context: dict[str, Any]
    answer: str
    sources: list[dict[str, Any]]
    llm_used: bool
    blocked: bool
    events: list[dict[str, Any]]
