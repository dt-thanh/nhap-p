"""Phase 6: hai node của luồng tư vấn dựa trên xếp hạng.

`analyze_node` không gọi LLM; nó chỉ định dạng lại `ranking_scores`/`absorption`
đã có sẵn trong state thành một khối ngữ cảnh gọn cho prompt. `respond_node`
là nơi duy nhất gọi LLM. Đầu ra luôn là đề xuất chờ duyệt, không phải quyết định
cuối cùng.
"""

from __future__ import annotations

import json
import time

from src.agents.state import AgentState
from src.logging_config import get_logger
from src.services.ai import AIServiceError
from src.services.llm import get_llm, log_llm_call

log = get_logger("src.agents.nodes.ranking_node")

PROMPT_TEMPLATE = "ranking_recommendation_v2"

_SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích bán hàng bất động sản. Dựa ĐÚNG vào dữ liệu xếp hạng và "
    "hấp thụ được cung cấp; không bịa số liệu, không đề cập căn không có trong danh "
    "sách. Đây là một ĐỀ XUẤT CHỜ NGƯỜI DUYỆT, không phải quyết định cuối cùng; không "
    "tuyên bố đã thực hiện hành động nào. Tổng số căn đã bán và còn lại chỉ mô tả "
    "quy mô/trạng thái dữ liệu, không tự chứng minh nhu cầu cao. Điểm ranking là mức "
    "ưu tiên tương đối, không phải xác suất bán hay lợi nhuận. Không đề xuất giảm giá, "
    "khuyến mãi hoặc cam kết lợi nhuận khi context không có giá, chi phí và biên lợi "
    "nhuận. Hành động mặc định là ưu tiên tiếp cận, xác minh nhu cầu và đo chuyển đổi. "
    "Trả lời DUY NHẤT một object JSON đúng khuôn:\n"
    '{"summary": "<2-4 câu tiếng Việt>", '
    '"recommended_actions": [{"unit_id": "<id lấy đúng từ dữ liệu>", "action": "<ngắn gọn>", "reason": "<vì sao>"}]}'
)


def _usage_from_result(result) -> dict | None:
    metadata = getattr(result, "response_metadata", None)
    if isinstance(metadata, dict):
        usage = metadata.get("usage") or metadata.get("usage_metadata")
        if isinstance(usage, dict):
            return usage
    return None


async def analyze_node(state: AgentState) -> dict:
    """Định dạng ranking_scores + absorption thành ngữ cảnh cho prompt."""
    ranking_context = state.get("query", "")
    absorption = state.get("absorption") or {}

    absorption_lines = [f"{key}: {value}" for key, value in absorption.items() if value is not None]
    analysis = ranking_context
    if absorption_lines:
        analysis += "\n\nHấp thụ dự án hiện tại:\n" + "\n".join(f"- {line}" for line in absorption_lines)

    ranking_scores = state.get("ranking_scores") or []
    if ranking_scores:
        analysis += "\n\nCăn xếp hạng và bằng chứng:\n" + json.dumps(ranking_scores, ensure_ascii=False)

    if not (state.get("ranking_scores") or ranking_context.strip()):
        return {"error": "NO_RANKING_DATA", "analysis": analysis}

    return {"analysis": analysis}


async def respond_node(state: AgentState) -> dict:
    """Gọi LLM và trả `summary` + `recommended_actions` có cấu trúc."""
    error = state.get("error")
    if error:
        return {"summary": f"Không tạo được đề xuất: {error}", "recommended_actions": []}

    analysis = state.get("analysis", "")
    prompt = f"{_SYSTEM_PROMPT}\n\nDữ liệu:\n{analysis}"

    llm = get_llm()
    started = time.perf_counter()
    try:
        result = await llm.ainvoke(prompt)
    except AIServiceError as exc:
        log_llm_call(
            prompt_template=PROMPT_TEMPLATE,
            model=getattr(llm, "model_name", "unknown"),
            latency_ms=(time.perf_counter() - started) * 1000,
            status="error",
            output=exc.code,
        )
        log.warning("ranking_node.llm_service_failed", error_code=exc.code, status_code=exc.status_code)
        return {
            "summary": f"AI agent không tạo được phân tích tự động: {exc.user_message} ({exc.code}).",
            "recommended_actions": [],
        }
    except Exception as exc:
        log_llm_call(
            prompt_template=PROMPT_TEMPLATE,
            model=getattr(llm, "model_name", "unknown"),
            latency_ms=(time.perf_counter() - started) * 1000,
            status="error",
            output=str(exc),
        )
        log.warning("ranking_node.llm_failed", error_type=type(exc).__name__)
        return {
            "summary": "AI agent không tạo được phân tích tự động lúc này (lỗi gọi LLM). Cần xem lại thủ công.",
            "recommended_actions": [],
        }

    content = getattr(result, "content", str(result))
    log_llm_call(
        prompt_template=PROMPT_TEMPLATE,
        model=getattr(llm, "model_name", "unknown"),
        latency_ms=(time.perf_counter() - started) * 1000,
        status="ok",
        usage=_usage_from_result(result),
        output=content,
    )

    summary, actions = _parse_structured_output(content)
    return {"summary": summary, "recommended_actions": actions}


def _parse_structured_output(content: str) -> tuple[str, list[dict]]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        summary = str(data.get("summary", "")).strip()
        actions = data.get("recommended_actions", [])
        if not isinstance(actions, list):
            actions = []
        actions = [
            {"unit_id": str(a.get("unit_id", "")), "action": str(a.get("action", "")), "reason": str(a.get("reason", ""))}
            for a in actions
            if isinstance(a, dict) and a.get("unit_id")
        ]
        if summary:
            return summary, actions
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return content.strip(), []
