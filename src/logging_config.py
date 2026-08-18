"""Cấu hình logging dùng chung cho api / worker / scheduler.

Nguyên tắc:
- Production: JSON một dòng, level INFO. Development: console màu, level tuỳ LOG_LEVEL.
- `redact_processor` là lưới an toàn cuối cùng — quét mọi field (kể cả log của
  uvicorn/rq/apscheduler) trước khi render, không bao giờ để lọt secret/PII.
- request_id / job_id lấy từ contextvars nên mọi logger trong cùng một request
  hoặc một job tự động mang định danh, không phải truyền tay.
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

from src.config import get_settings

# ---------------------------------------------------------------- context vars

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def new_error_id() -> str:
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------- redaction

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI-style / generic API key
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "«redacted:api_key»"),
    # Authorization: Bearer <token>
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE), r"\1«redacted»"),
    # JWT
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}"), "«redacted:jwt»"),
    # Credential trong URL: scheme://user:pass@host — user có thể rỗng (redis://:pass@)
    (re.compile(r"(://)([^:/@\s]*):([^@\s]+)@"), r"\1«redacted»:«redacted»@"),
    # key=value dạng secret
    (
        re.compile(r"\b(password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*\S+", re.IGNORECASE),
        r"\1=«redacted»",
    ),
]

# Field bị thay thế hoàn toàn khi xuất hiện dưới dạng key của dict / kwargs.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "token_hash",
        "jti",
        "authorization",
        "cookie",
        "set-cookie",
        "jwt_secret",
        "secret",
        "api_key",
        "llm_api_key",
        "openai_api_key",
        "database_url",
        "redis_url",
        "dsn",
        # PII
        "email",
        "phone",
        "full_name",
        "address",
        "customer_id",
        # Nội dung AI
        "prompt",
        "completion",
        "messages",
    }
)

_REDACTED = "«redacted»"
_MAX_DEPTH = 6


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return value
    if isinstance(value, str):
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {k: (_REDACTED if str(k).lower() in _SENSITIVE_KEYS else _scrub(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return type(value)(_scrub(v, depth + 1) for v in value)
    return value


def redact_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    """Lưới an toàn cuối: quét toàn bộ event_dict trước khi render."""
    return _scrub(event_dict)


def context_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    """Gắn request_id / job_id từ contextvars nếu đang ở trong ngữ cảnh."""
    if (rid := request_id_var.get()) is not None:
        event_dict.setdefault("request_id", rid)
    if (jid := job_id_var.get()) is not None:
        event_dict.setdefault("job_id", jid)
    return event_dict


# --------------------------------------------------------------- configuration

_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    context_processor,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.UnicodeDecoder(),
]

_configured = False


def configure_logging(service: str) -> None:
    """Cấu hình logging cho một tiến trình. Gọi một lần khi khởi động."""
    global _configured

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    use_console = settings.log_format == "console"

    renderer: Any = structlog.dev.ConsoleRenderer(colors=True) if use_console else structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *_SHARED_PROCESSORS,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Log từ thư viện bên thứ ba (uvicorn, rq, apscheduler) đi qua chain này
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_processor,  # LUÔN ngay trước renderer
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Giảm nhiễu từ thư viện; ở production chỉ giữ WARNING trở lên.
    third_party_level = logging.INFO if settings.app_env == "development" else logging.WARNING
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(third_party_level)

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service, env=settings.app_env)

    _configured = True


def get_logger(name: str) -> Any:
    """Lấy logger đã bind. An toàn khi gọi trước configure_logging()."""
    return structlog.get_logger(name)
