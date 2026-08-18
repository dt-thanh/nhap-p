import asyncio
from types import SimpleNamespace

import httpx

from src.config import get_settings


class AIServiceError(RuntimeError):
    def __init__(self, code, user_message, status_code=502):
        super().__init__(code)
        self.code = code
        self.user_message = user_message
        self.status_code = status_code


def _extract_text(data: dict) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    chunks: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "".join(chunks).strip()


def _usage(data: dict) -> dict:
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    return {
        **usage,
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
    }


async def generate_content(prompt: str, *, max_output_tokens: int | None = None, thinking_budget: int | None = None):
    settings = get_settings()
    key = settings.resolved_llm_api_key
    if not key:
        raise AIServiceError("API_KEY_MISSING", "GPT chưa được cấu hình API key.", 503)

    payload = {
        "model": settings.resolved_llm_model,
        "input": prompt,
        "temperature": settings.llm_temperature,
        "store": False,
    }
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    if thinking_budget is not None and settings.resolved_llm_model.startswith(("gpt-5", "o")):
        effort = "low" if thinking_budget == 0 else "medium"
        payload["reasoning"] = {"effort": effort}

    for attempt in range(2):
        try:
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt < 1:
                await asyncio.sleep(2**attempt)
                continue
            raise AIServiceError("NETWORK_ERROR", "Không thể kết nối GPT. Vui lòng thử lại.", 503) from exc

        if response.status_code in {429, 500, 502, 503, 504} and attempt < 1:
            await asyncio.sleep(2**attempt)
            continue
        if response.status_code == 401:
            raise AIServiceError("INVALID_API_KEY", "API key GPT không hợp lệ hoặc đã bị thu hồi.", 401)
        if response.status_code == 403:
            raise AIServiceError("PERMISSION_DENIED", "API key GPT không có quyền sử dụng model đã chọn.", 403)
        if response.status_code == 429:
            raise AIServiceError(
                "RESOURCE_EXHAUSTED",
                "GPT đã hết hạn mức request/token. Vui lòng chờ rồi thử lại hoặc kiểm tra quota.",
                429,
            )
        if response.status_code in {500, 502, 503, 504}:
            raise AIServiceError("SERVICE_UNAVAILABLE", "GPT đang tạm thời quá tải. Vui lòng thử lại.", 503)
        if response.status_code >= 400:
            raise AIServiceError("LLM_ERROR", "GPT không thể xử lý yêu cầu lúc này.", response.status_code)

        data = response.json()
        status = data.get("status")
        if status == "incomplete":
            raise AIServiceError("TOKEN_LIMIT", "GPT đã dùng hết token đầu ra. Hãy rút gọn dữ liệu đầu vào.", 422)
        text = _extract_text(data)
        if not text:
            raise AIServiceError("EMPTY_RESPONSE", "GPT không trả về nội dung phù hợp.")
        return text, _usage(data)

    raise AIServiceError("LLM_ERROR", "GPT tạm thời không khả dụng.", 503)


class OpenAIChatLLM:
    """Small adapter with the `ainvoke()` shape used by LangGraph nodes/tests."""

    def __init__(self, model_name: str):
        self.model_name = model_name

    async def ainvoke(self, prompt: str):
        text, usage = await generate_content(prompt)
        return SimpleNamespace(content=text, response_metadata={"usage": usage})
