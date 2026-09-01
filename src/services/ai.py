"""OpenAI-compatible LLM adapter used only for Agent narration."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from src.config import get_settings


class AIServiceError(RuntimeError):
    def __init__(self, code: str, user_message: str, status_code: int = 502):
        super().__init__(code)
        self.code = code
        self.user_message = user_message
        self.status_code = status_code


async def generate_content(
    prompt: str,
    *,
    system_prompt: str = "",
    max_output_tokens: int = 800,
    temperature: float = 0.2,
) -> tuple[str, dict[str, Any]]:
    """Call any OpenAI-compatible `/chat/completions` provider."""
    settings = get_settings()
    key = settings.resolved_llm_api_key
    if not key:
        raise AIServiceError("API_KEY_MISSING", "Chưa cấu hình LLM API key.", 503)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": settings.resolved_llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
                response = await client.post(
                    settings.llm_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            raise AIServiceError("NETWORK_ERROR", "Không thể kết nối LLM.", 503) from exc

        if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
            await asyncio.sleep(1)
            continue
        if response.status_code == 401:
            raise AIServiceError("INVALID_API_KEY", "LLM API key không hợp lệ hoặc đã bị thu hồi.", 401)
        if response.status_code == 403:
            raise AIServiceError("PERMISSION_DENIED", "API key không có quyền dùng model đã chọn.", 403)
        if response.status_code == 429:
            raise AIServiceError("RESOURCE_EXHAUSTED", "LLM đang hết hạn mức request/token.", 429)
        if response.status_code >= 400:
            raise AIServiceError("LLM_ERROR", "LLM không thể xử lý yêu cầu lúc này.", response.status_code)

        data = response.json()
        choices = data.get("choices") or []
        content = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(content, str) or not content.strip():
            raise AIServiceError("EMPTY_RESPONSE", "LLM không trả về nội dung phù hợp.")
        return content.strip(), data.get("usage") or {}

    raise AIServiceError("LLM_ERROR", "LLM tạm thời không khả dụng.", 503)
