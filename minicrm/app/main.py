"""Ứng dụng Mini CRM.

Phase 4: CRUD căn/giao dịch, sổ gửi đi, và một cò đẩy tự động chạy sau mỗi lần
commit. KHÔNG có giao diện người dùng, không khách hàng, không giá, không PII —
hợp đồng đồng bộ không cần chúng.

`/health` cố ý trả kèm `disclaimer`, và mọi phong bì gửi đi cố ý mang `_comment`
cùng nội dung: bất kỳ ai mở service này lên đều phải thấy ngay rằng đây là hạ tầng
TỔNG HỢP do chính dự án viết, không phải một CRM thật. Đến Phase 4 thì rủi ro này
LỚN HƠN trước chứ không nhỏ đi — một hệ thống có CRUD đầy đủ và một vòng HTTP xanh
trông thuyết phục hơn hẳn một file JSON, mà nó vẫn không chứng minh được gì về
khả năng tương thích với một CRM có thật. Nhãn phải đi cùng sản phẩm, không nằm
trong tài liệu.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from app import contract, crud
from app.config import get_settings
from app.relay import relay_loop
from app import human_auth
from app.routers import areas, auth_routes, deals, outbox, projects, units
from app.schemas import HealthOut
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Khởi động/dừng vòng relay tự động (Phase C.5) cùng vòng đời app.

    Không mở gì khác ở đây — engine/session factory của toàn app vẫn lazy, tạo ở
    lần gọi `get_session_factory()` đầu tiên (bất kể trong hay ngoài lifespan này).
    """
    relay_loop.start()
    try:
        yield
    finally:
        await relay_loop.stop()


app = FastAPI(
    title="Mini CRM (synthetic)",
    description=(
        "Hệ nguồn TỔNG HỢP dùng để kiểm luồng nhận của backend. "
        "Không phải CRM production, không phải nguồn sự thật nghiệp vụ."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# CORS: cookie phiên là `HttpOnly` + cross-origin (UI 5174 → API 8100), nên
# `allow_credentials=True` là BẮT BUỘC — và khi bật nó, trình duyệt TỪ CHỐI
# origin "*". Vì vậy danh sách origin phải tường minh, từ env.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(projects.router)
app.include_router(areas.router)
app.include_router(units.router)
app.include_router(deals.router)
app.include_router(outbox.router)


@app.exception_handler(crud.CrudError)
async def crud_error_handler(request: Request, exc: crud.CrudError) -> JSONResponse:
    """Từ chối nghiệp vụ → mã HTTP của chính nó, kèm `error_code` máy đọc được.

    Mọi lỗi loại này xảy ra TRƯỚC khi ghi và trước khi gửi, nên khi người gọi thấy
    nó thì chắc chắn không có bản ghi nào được tạo và không có request nào rời khỏi
    máy — đó là điều thân phản hồi khẳng định bằng `sent: false`.
    """
    return JSONResponse(
        status_code=exc.http_status,
        content={"error_code": exc.error_code, "message": exc.message, "detail": exc.detail, "sent": False},
    )


@app.exception_handler(human_auth.HumanAuthError)
async def human_auth_error_handler(request: Request, exc: human_auth.HumanAuthError) -> JSONResponse:
    """`HumanAuthError` → mã HTTP CỦA CHÍNH NÓ, không phải 500.

    Lớp lỗi này (app/human_auth.py) đã mang sẵn `status_code` đúng — 401 cho
    thông tin đăng nhập sai, 403 cho thiếu quyền — nhưng KHÔNG có handler nào
    đăng ký, nên FastAPI để nó nổi lên thành 500. Hệ quả cụ thể trước khi có
    handler này: `GET /projects` không kèm xác thực trả **500** thay vì **401**.

    Vì sao đó là lỗi thật chứ không phải chuyện thẩm mỹ:
      · Frontend phân nhánh theo mã: `services/api.ts` chỉ chạy refresh phiên và
        chuyển hướng đăng nhập khi thấy 401. Gặp 500 nó coi là "server hỏng",
        nên người dùng hết phiên sẽ thấy màn hình lỗi thay vì được đưa về trang
        đăng nhập.
      · 500 mặc định KHÔNG kèm `WWW-Authenticate`, nên client tuân thủ chuẩn
        không biết phải xác thực lại bằng cách nào.
      · 5xx là tín hiệu "lỗi phía máy chủ" cho giám sát/cảnh báo. Một người dùng
        gõ sai token không được phép làm nhiễu biểu đồ lỗi hệ thống.
    """
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.message, "error_code": exc.error_code},
        headers=headers,
    )


@app.exception_handler(contract.ContractViolationError)
async def contract_violation_handler(request: Request, exc: contract.ContractViolationError) -> JSONResponse:
    """Mini CRM dựng ra một phong bì sai hợp đồng từ dữ liệu cục bộ HỢP LỆ.

    Đây là lỗi của Mini CRM, không phải của người gọi — mọi trường người dùng nhập
    được đã qua `Literal`/`min_length`/`max_length` ở tầng schema. Nên 500, không
    phải 422: trả 422 sẽ khiến người tích hợp đi sửa một payload không có gì sai.

    Thay đổi cục bộ đã bị ROLLBACK (kiểm hợp đồng nằm trong transaction), nên
    không có bản ghi mồ côi nào ở lại.
    """
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "CONTRACT_VIOLATION",
            "message": "Mini CRM dựng ra phong bì không đúng hợp đồng v1 — lỗi của Mini CRM, không phải của bạn.",
            "violations": exc.violations,
            "sent": False,
        },
    )


@app.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(status="ok", app=settings.app_name, source_instance_id=settings.source_instance_id)
