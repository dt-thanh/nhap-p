"""Middleware gắn request_id và log mỗi request đã hoàn tất."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.logging_config import get_logger, new_request_id, request_id_var

log = get_logger("src.api")

# Không log healthcheck: Docker gọi 30s/lần, chỉ làm ngập log.
_SKIP_PATHS = frozenset({"/health"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Sinh (hoặc nhận) X-Request-ID, đưa vào contextvar, log request completed."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # KHÔNG reset ở đây: exception handler của ServerErrorMiddleware chạy
            # NGOÀI middleware này, cần request_id còn trong context để log tương quan.
            # Mỗi request là một asyncio task riêng nên contextvar không rò sang request khác.
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id

        if request.url.path not in _SKIP_PATHS:
            log.info(
                "request.completed",
                method=request.method,
                # Chỉ path, KHÔNG query string (có thể chứa token ở WS handshake)
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )

        request_id_var.reset(token)
        return response
