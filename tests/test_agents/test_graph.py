"""Phase 6: `src/agents/graph.py` được nối lại cho luồng tư vấn xếp hạng —
xem `src/agents/nodes/ranking_node.py`. Test cũ ở đây kiểm hành vi generic của
stub `example_node.py` (state.response) — không còn đúng, vì node giờ trả
`summary`/`recommended_actions` có cấu trúc, và KHÔNG bao giờ gọi LLM thật
trong test (`get_llm` được monkeypatch)."""

import pytest

from src.agents.graph import agent


@pytest.mark.asyncio
async def test_agent_produces_a_structured_summary_from_ranking_context(monkeypatch, mock_llm):
    monkeypatch.setattr("src.agents.nodes.ranking_node.get_llm", lambda: mock_llm)

    result = await agent.ainvoke(
        {
            "query": "Dự án X, 3/3 căn được xếp hạng.",
            "project_id": "11111111-1111-1111-1111-111111111111",
            "area_id": None,
            "ranking_scores": [{"unit_id": "u1", "score": "0.85", "rank_in_project": 1}],
            "absorption": {"units_remaining": 10, "units_sold": 5},
        }
    )

    assert "summary" in result
    assert result["summary"]  # LLM trả text thường (mock_llm) -> giữ nguyên văn, không JSON
    assert result["recommended_actions"] == []  # không JSON hợp lệ -> không bịa hành động
    assert "response" not in result  # trường của luồng chat cũ, không dùng ở đây


@pytest.mark.asyncio
async def test_agent_calls_the_llm_with_the_ranking_context_in_the_prompt(monkeypatch, mock_llm):
    monkeypatch.setattr("src.agents.nodes.ranking_node.get_llm", lambda: mock_llm)

    await agent.ainvoke({"query": "Ngữ cảnh xếp hạng đặc trưng ABC123", "ranking_scores": [{"unit_id": "u1"}]})

    prompt = mock_llm.ainvoke.call_args.args[0]
    assert "ABC123" in prompt


@pytest.mark.asyncio
async def test_agent_short_circuits_to_end_when_there_is_no_ranking_data(monkeypatch, mock_llm):
    """Không `query` (context xếp hạng) và không `ranking_scores` -> `analyze_node`
    trả `error`, `should_continue` đi thẳng tới END — LLM KHÔNG được gọi."""
    monkeypatch.setattr("src.agents.nodes.ranking_node.get_llm", lambda: mock_llm)

    result = await agent.ainvoke({})

    assert result.get("error") == "NO_RANKING_DATA"
    mock_llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_agent_returns_valid_json_actions_when_the_llm_provides_them(monkeypatch, mock_llm):
    from unittest.mock import AsyncMock

    mock_llm.ainvoke.return_value = AsyncMock(
        content='{"summary": "Ưu tiên căn u1.", "recommended_actions": [{"unit_id": "u1", "action": "contact", "reason": "điểm cao nhất"}]}'
    )
    monkeypatch.setattr("src.agents.nodes.ranking_node.get_llm", lambda: mock_llm)

    result = await agent.ainvoke({"query": "ngữ cảnh", "ranking_scores": [{"unit_id": "u1"}]})

    assert result["summary"] == "Ưu tiên căn u1."
    assert result["recommended_actions"] == [{"unit_id": "u1", "action": "contact", "reason": "điểm cao nhất"}]


@pytest.mark.asyncio
async def test_agent_surfaces_llm_token_or_quota_errors(monkeypatch):
    from unittest.mock import AsyncMock

    from src.services.ai import AIServiceError

    fake_llm = AsyncMock()
    fake_llm.model_name = "gpt-4o-mini"
    fake_llm.ainvoke.side_effect = AIServiceError(
        "RESOURCE_EXHAUSTED",
        "GPT đã hết hạn mức request/token. Vui lòng chờ rồi thử lại hoặc kiểm tra quota.",
        429,
    )
    monkeypatch.setattr("src.agents.nodes.ranking_node.get_llm", lambda: fake_llm)

    result = await agent.ainvoke({"query": "ngữ cảnh", "ranking_scores": [{"unit_id": "u1"}]})

    assert "RESOURCE_EXHAUSTED" in result["summary"]
    assert "hết hạn mức" in result["summary"]
    assert result["recommended_actions"] == []
