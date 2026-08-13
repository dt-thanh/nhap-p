from typing import Any

from langchain_openai import ChatOpenAI

from src.config import get_settings
from src.logging_config import get_logger

log = get_logger("src.services.llm")


def get_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.resolved_llm_model,
        api_key=settings.resolved_llm_api_key,
        temperature=settings.llm_temperature,
    )


def log_llm_call(
    *,
    prompt_template: str,
    model: str,
    latency_ms: float,
    status: str,
    usage: Any = None,
    retry_count: int = 0,
    output: str = "",
) -> None:
    """Ghi metadata một lượt gọi LLM (SRS FR-020, NFR-L6).

    KHÔNG log prompt/completion đầy đủ. Ở development có thể bật
    LLM_LOG_SNIPPET=true để thêm 200 ký tự đầu output — vẫn đi qua
    redact_processor trước khi render.
    """
    settings = get_settings()

    fields: dict[str, Any] = {
        "prompt_template": prompt_template,
        "model": model,
        "latency_ms": round(latency_ms, 1),
        "status": status,
        "retry_count": retry_count,
        "output_len": len(output),
    }
    if usage is not None:
        fields["prompt_tokens"] = getattr(usage, "prompt_tokens", None) or (
            usage.get("prompt_tokens") if isinstance(usage, dict) else None
        )
        fields["completion_tokens"] = getattr(usage, "completion_tokens", None) or (
            usage.get("completion_tokens") if isinstance(usage, dict) else None
        )
    if settings.llm_log_snippet and settings.app_env == "development":
        fields["output_snippet"] = output[:200]

    if status == "ok":
        log.info("llm.call", **fields)
    else:
        log.warning("llm.call", **fields)
