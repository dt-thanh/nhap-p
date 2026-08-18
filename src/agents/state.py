from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """State schema cho LangGraph agent.

    Mỗi node đọc và ghi vào state này.
    total=False cho phép tất cả fields là optional.
    """

    query: str
    context: str
    analysis: str
    response: str
    error: str
    metadata: dict

    # --- Phase 6: tư vấn dựa trên xếp hạng (src/agents/nodes/ranking_node.py) --
    # Người gọi (src/api/agent.py) truyền `query` = summary_context của
    # `run_ranking()`, cộng ba trường dưới đây làm ngữ cảnh có cấu trúc — không
    # bắt LLM tự đọc lại một khối text lớn để suy ra số liệu.
    project_id: str
    area_id: str | None
    ranking_scores: list[dict]
    absorption: dict
    # Đầu ra CÓ CẤU TRÚC của respond_node — đây là thứ được lưu vào
    # `agent_recommendations`, KHÔNG phải `response` (giữ `response` cho luồng
    # chat cũ, tránh hai đường dùng chung một trường rồi giẫm lên nhau).
    summary: str
    recommended_actions: list[dict]
