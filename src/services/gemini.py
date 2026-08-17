import asyncio
import httpx
from src.config import get_settings

class GeminiServiceError(RuntimeError):
    def __init__(self, code, user_message, status_code=502):
        super().__init__(code)
        self.code, self.user_message, self.status_code = code, user_message, status_code

async def ask_gemini(message, market_context):
    settings = get_settings()
    key = settings.resolved_llm_api_key
    if not key:
        raise GeminiServiceError("API_KEY_MISSING", "Gemini chưa được cấu hình API key.", 503)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.resolved_llm_model}:generateContent"
    prompt = (
        "Bạn là trợ lý phân tích bán hàng bất động sản. Trả lời tiếng Việt, ngắn gọn và dựa đúng dữ liệu. "
        "Không tuyên bố đã thay đổi giá, chiết khấu hay chính sách. Đề xuất nhạy cảm luôn cần Admin phê duyệt.\n"
        f"Dữ liệu hiện tại: {market_context}\nCâu hỏi: {message}"
    )
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": settings.gemini_max_output_tokens, "thinkingConfig": {"thinkingBudget": settings.gemini_thinking_budget}}}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, params={"key": key}, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            raise GeminiServiceError("NETWORK_ERROR", "Không thể kết nối Gemini. Vui lòng thử lại.", 503) from exc
        if response.status_code in {429, 503} and attempt < 2:
            await asyncio.sleep(2 ** attempt)
            continue
        if response.status_code == 429:
            raise GeminiServiceError("RESOURCE_EXHAUSTED", "Gemini đã hết hạn mức request/token. Vui lòng chờ rồi thử lại hoặc kiểm tra quota.", 429)
        if response.status_code == 403:
            raise GeminiServiceError("PERMISSION_DENIED", "API key Gemini không có quyền sử dụng model đã chọn.", 403)
        if response.status_code >= 400:
            raise GeminiServiceError("GEMINI_ERROR", "Gemini không thể xử lý yêu cầu lúc này.")
        data = response.json()
        candidate = (data.get("candidates") or [{}])[0]
        finish = candidate.get("finishReason", "")
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if finish in {"MAX_TOKENS", "TOKEN_LIMIT"}:
            raise GeminiServiceError("TOKEN_LIMIT", "Gemini đã dùng hết token đầu ra. Hãy đặt câu hỏi ngắn hơn.", 422)
        if not text:
            raise GeminiServiceError("EMPTY_RESPONSE", "Gemini không trả về nội dung phù hợp.")
        return text, data.get("usageMetadata", {})
    raise GeminiServiceError("GEMINI_ERROR", "Gemini tạm thời không khả dụng.", 503)
