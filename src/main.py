from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import router
from src.config import get_settings
from src.logging_config import configure_logging, get_logger, new_error_id, request_id_var
from src.middleware import RequestContextMiddleware

configure_logging("api")
log = get_logger("src.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("app.startup", app=settings.app_name)
    yield
    log.info("app.shutdown")


app = FastAPI(
    title="AI20K Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log đầy đủ ở server, chỉ trả error_id cho client.

    Không trả str(exc): message của driver DB / SDK LLM có thể chứa DSN,
    một phần API key hoặc thông tin hạ tầng.
    """
    error_id = new_error_id()
    log.error(
        "request.unhandled_error",
        error_id=error_id,
        error_type=type(exc).__name__,
        path=request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_id": error_id,
            "request_id": request_id_var.get(),
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
