from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.agent import router as agent_router
from src.api.ahp import router as ahp_router
from src.api.dashboard import router as dashboard_router
from src.api.files import router as files_router
from src.api.governance import router as governance_router
from src.api.inventory import router as inventory_router
from src.api.ops import router as ops_router
from src.api.parallel_run import router as parallel_run_router
from src.api.ranking import router as ranking_router
from src.api.reconciliation import router as reconciliation_router
from src.api.routes import router
from src.api.sync import router as sync_router
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
app.include_router(files_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")
app.include_router(sync_router, prefix="/api/v1")
app.include_router(inventory_router, prefix="/api/v1")
app.include_router(reconciliation_router, prefix="/api/v1")
app.include_router(ops_router, prefix="/api/v1")
app.include_router(parallel_run_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(ranking_router, prefix="/api/v1")
app.include_router(ahp_router, prefix="/api/v1")
app.include_router(governance_router, prefix="/api/v1")

# SSO qua Keycloak (realm p100, cùng chung với Mini CRM).
from src.api.auth import router as auth_router  # noqa: E402

app.include_router(auth_router, prefix="/api/v1")


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
