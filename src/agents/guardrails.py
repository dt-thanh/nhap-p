"""Input/output guardrails inspired by the F:/Agent DeepSeek harness."""
from __future__ import annotations

import json
import re
from typing import Any
MAX_INPUT_CHARS = 2000
INJECTION_MARKERS = ("ignore previous", "reveal system prompt", "bỏ qua mọi hướng dẫn", "tiết lộ api key")
def validate_request(question: str, intent: str, arguments: dict[str, Any]) -> str | None:
    if not question.strip() or len(question) > MAX_INPUT_CHARS: return f"Câu hỏi phải có từ 1 đến {MAX_INPUT_CHARS} ký tự."
    if any(marker in question.casefold() for marker in INJECTION_MARKERS): return "Câu hỏi bị từ chối vì yêu cầu can thiệp vào hướng dẫn hệ thống."
    for key in ("unit_id", "first", "second"):
        if key in arguments and not re.fullmatch(r"U-\d{4}", arguments[key], re.I): return f"Mã căn không hợp lệ: {arguments[key]}"
    return None
def validate_llm_output(answer: str, context: dict[str, Any]) -> str | None:
    if not answer.strip(): return "LLM trả về câu trả lời rỗng."
    # The narrator may answer a focused question with only the relevant rows;
    # requiring every top-10 ID incorrectly forced valid answers to fallback.
    known = [x.get("unit_id") for x in context.get("top_ranked_units", []) if x.get("unit_id")]
    if known and not any(unit_id in answer for unit_id in known): return "LLM không nêu căn hộ nào từ dữ liệu được cung cấp."
    serialized_context = json.dumps(context, ensure_ascii=False, default=str)
    for token in re.findall(r"\d+(?:[.,]\d+)?%?", answer):
        if token not in serialized_context:
            return f"LLM sử dụng số liệu không có trong dữ liệu được cung cấp: {token}"
    return None
