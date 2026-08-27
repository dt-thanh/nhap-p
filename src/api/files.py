"""Router upload file Excel/CSV (SRS §5.2 — F1 Nạp dữ liệu).

Router cố tình mỏng: kiểm tra request, gọi service, map kết quả sang response.
Toàn bộ việc đọc bảng tính nằm ở `src.services.excel_parser`, việc lưu file ở
`src.services.file_upload`, và parse thật sự chạy ở worker qua `src.jobs`.

Parse KHÔNG chạy trong request: file 20 MB mất vài giây CPU, giữ trong request
sẽ chẹn event loop của uvicorn. Upload trả 202 + file_id, frontend poll `/status`.

API tạo bản ghi `upload_files` NGAY trong request (SRS §5.2: "khởi tạo bản ghi
upload_files, chạy parse nền") vì hai lý do:
  1. Client có `file_id` để poll ngay, không phải chờ worker chạy xong.
  2. Trùng file trả được lỗi đồng bộ 409, thay vì để người dùng chờ rồi mới biết.

Trạng thái và lỗi đọc từ DB chứ không từ job result của RQ: job result hết hạn
sau 500 giây (result_ttl mặc định), còn bản ghi DB thì còn mãi.

**Phạm vi/quyền (đóng lại lỗ hổng MVP1).** Router này từng hoàn toàn không có
tầng xác thực nào — mọi route nhận request vô danh. Cùng cơ chế `dashboard_auth`
đã dùng ở `ranking.py`/`sync.py`/`dashboard.py`, không dựng khung mới:

  * `POST /upload` cần `pipeline_operator` trở lên, và `project_id` gửi lên
    phải nằm trong phạm vi dự án của token — nạp file vào một dự án ngoài
    phạm vi là một đường ghi dữ liệu, không phải đọc.
  * `GET /files`, `/{file_id}/status`, `/{file_id}/errors` cần `business_viewer`
    trở lên; danh sách bị lọc theo phạm vi, và ba route theo `file_id` đọc
    `upload_files.project_id` của CHÍNH bản ghi đó rồi mới trả dữ liệu — tra
    trước rồi mới lọc theo phạm vi, không suy phạm vi từ tham số client tự khai.
  * `GET /{file_id}/errors.csv` cần `pipeline_operator` trở lên: xuất hàng loạt
    dễ rò rỉ dữ liệu hơn một trang JSON phân trang.
"""

import csv
import io
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from src.db import get_session_factory
from src.logging_config import get_logger, new_error_id
from src.models.schemas import (
    DB_STATUS_TO_API,
    FileErrorList,
    FileList,
    FileStatus,
    FileSummary,
    UploadAccepted,
)
from src.models.tables import projects, upload_errors, upload_files
from src.services.dashboard_auth import DashboardPrincipal, require_role, resolve_scope_project_ids
from src.services.excel_parser import TEMPLATES
from src.services.file_upload import FileUploadService, UploadRejectedError, validate_suffix
from src.task_queue import INGEST_QUEUE, get_queue

router = APIRouter(prefix="/files", tags=["files"])
log = get_logger("src.api.files")

require_viewer = require_role("business_viewer")
require_operator = require_role("pipeline_operator")


async def _ensure_project_in_scope(principal: DashboardPrincipal, project_id: uuid.UUID | None) -> None:
    """403 `PROJECT_OUT_OF_SCOPE` nếu `project_id` không nằm trong phạm vi token.

    Dùng UUID nội bộ trực tiếp (giống `sync.py::sync_run_payload`) — bảng
    `upload_files` không giữ `external_id`, chỉ giữ `project_id` nội bộ.
    `project_id=None` (lô CRM không gắn dự án nào, ví dụ lô TẠO dự án) chỉ lọt
    qua với phạm vi `ALL` — cùng nguyên tắc "không suy được dự án thì không đoán
    phạm vi" đã dùng ở `list_sync_runs`.
    """
    async with get_session_factory()() as session:
        scope_ids = await resolve_scope_project_ids(session, principal)
    if scope_ids == "ALL":
        return
    if project_id is None or project_id not in scope_ids:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
                "error_code": "PROJECT_OUT_OF_SCOPE",
            },
        )

# Số lỗi trả tối đa cho một lần gọi /errors. Bản CSV không bị chặn vì mục đích
# của nó là tải hết về để sửa.
MAX_ERRORS_PER_PAGE = 500

# Khớp `ck_upload_files_transport_mode` (0006). "unknown" KHÔNG phải giá trị cột
# thật — cột NOT NULL với CHECK chỉ hai giá trị này — mà là giá trị API trả về
# nếu một dòng nào đó (dữ liệu cũ, thao tác tay) mang giá trị ngoài hai cái này.
_TRANSPORT_MODES = ("file_upload", "api_push")


async def _project_exists(project_id: uuid.UUID) -> bool:
    """Kiểm tra dự án có thật trước khi nhận file.

    Không có bước này thì `fk_upload_files_project_id` mới nổ ở worker, và người
    dùng chỉ thấy job failed không rõ lý do thay vì một lỗi 422 nói thẳng.
    """
    async with get_session_factory()() as session:
        found = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_id))
    return found is not None


async def _find_duplicate(project_id: uuid.UUID, checksum: str) -> uuid.UUID | None:
    """Tìm file đã nạp cùng (project_id, checksum) — khớp uq_upload_files_project_checksum."""
    async with get_session_factory()() as session:
        return await session.scalar(
            sa.select(upload_files.c.id).where(
                upload_files.c.project_id == project_id,
                upload_files.c.checksum == checksum,
            )
        )


async def _create_upload_file(*, project_id: uuid.UUID, filename: str, checksum: str) -> uuid.UUID:
    """Tạo bản ghi `upload_files` ở trạng thái pending, trả về id."""
    file_id = uuid.uuid4()
    async with get_session_factory()() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=file_id,
                    project_id=project_id,
                    uploaded_by=None,  # MVP 1 chưa có auth
                    filename=filename,
                    checksum=checksum,
                    status="pending",
                    rows_ok=0,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                )
            )
    return file_id


async def _delete_upload_file(file_id: uuid.UUID) -> None:
    """Gỡ bản ghi vừa tạo khi không enqueue được — không để lại file treo pending."""
    async with get_session_factory()() as session:
        async with session.begin():
            await session.execute(sa.delete(upload_files).where(upload_files.c.id == file_id))


async def _fetch_upload_file(file_id: uuid.UUID):
    """Lấy bản ghi `upload_files`, 404 nếu không có."""
    async with get_session_factory()() as session:
        row = (await session.execute(sa.select(upload_files).where(upload_files.c.id == file_id))).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"message": "Không tìm thấy file", "file_id": str(file_id)},
        )
    return row


def _parse_file_id(file_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(file_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "file_id phải là UUID hợp lệ", "error_code": "INVALID_FILE_ID"},
        ) from exc


async def _load_errors(file_id: uuid.UUID, limit: int | None) -> tuple[list[dict], int]:
    """Đọc lỗi theo dòng, kèm tổng số lỗi đã lưu (để client biết mình có bị cắt)."""
    async with get_session_factory()() as session:
        total = await session.scalar(
            sa.select(sa.func.count()).select_from(upload_errors).where(upload_errors.c.file_id == file_id)
        )
        query = (
            sa.select(
                upload_errors.c.row_number,
                upload_errors.c.column_name,
                upload_errors.c.error_code,
                upload_errors.c.message,
            )
            .where(upload_errors.c.file_id == file_id)
            .order_by(upload_errors.c.row_number)
        )
        if limit is not None:
            query = query.limit(limit)
        rows = (await session.execute(query)).mappings().all()
    return [dict(row) for row in rows], total or 0


@router.post(
    "/upload",
    response_model=UploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload Excel/CSV, khởi tạo upload_files, chạy parse nền",
)
async def upload_file(
    file: UploadFile = File(..., description="File Excel/CSV theo template"),
    template: str = Form(..., description="sales | inventory | areas"),
    project_id: str = Form(..., description="UUID dự án mà file này thuộc về"),
    principal: DashboardPrincipal = Depends(require_operator),
) -> UploadAccepted:
    """Nhận file, tạo `upload_files`, đẩy job parse cho worker."""
    # `upload_files.project_id` và `areas.project_id` đều NOT NULL, mà thông tin
    # này không nằm trong file — nó thuộc về phạm vi upload nên client phải gửi.
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "project_id phải là UUID hợp lệ", "error_code": "INVALID_PROJECT_ID"},
        ) from exc

    if template not in TEMPLATES:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Template '{template}' không hợp lệ",
                "allowed": sorted(TEMPLATES),
            },
        )

    if not await _project_exists(project_uuid):
        raise HTTPException(
            status_code=422,
            detail={"message": f"Dự án '{project_id}' không tồn tại", "error_code": "UNKNOWN_PROJECT"},
        )

    # Dự án có thật rồi mới kiểm phạm vi: một dự án ngoài phạm vi vẫn phải trả
    # lời "không tồn tại" hay "ngoài phạm vi" đúng thứ tự đó — tồn tại là một sự
    # thật khách quan, phạm vi là một sự thật về DANH TÍNH người gọi.
    await _ensure_project_in_scope(principal, project_uuid)

    # Chặn sai định dạng TRƯỚC khi đọc body — không tốn băng thông và I/O đĩa.
    try:
        validate_suffix(file.filename or "")
    except UploadRejectedError as exc:
        raise HTTPException(
            status_code=415,
            detail={"message": exc.message, "error_code": exc.error_code},
        ) from exc

    service = FileUploadService()
    try:
        stored = await service.save(file, file.filename or "")
    except UploadRejectedError as exc:
        # 413 cho file quá cỡ, 422 cho file rỗng — đều là lỗi người dùng sửa được.
        code = 413 if exc.error_code == "FILE_TOO_LARGE" else 422
        raise HTTPException(
            status_code=code,
            detail={"message": exc.message, "error_code": exc.error_code},
        ) from exc

    # Checksum chỉ biết sau khi đọc xong file, nên chống trùng nằm ở đây chứ
    # không chặn được sớm hơn.
    duplicate = await _find_duplicate(project_uuid, stored.checksum)
    if duplicate is not None:
        stored.path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "File này đã được nạp cho dự án (trùng checksum)",
                "error_code": "DUPLICATE_FILE",
                "duplicate_of": str(duplicate),
            },
        )

    file_id = await _create_upload_file(
        project_id=project_uuid, filename=stored.original_filename, checksum=stored.checksum
    )

    try:
        job = get_queue(INGEST_QUEUE).enqueue(
            "src.jobs.parse_upload.run_parse_upload",
            file_path=str(stored.path),
            template=template,
            file_id=str(file_id),
            project_id=project_id,
            checksum=stored.checksum,
            original_filename=stored.original_filename,
        )
    except Exception as exc:
        # Redis chết thì không ai parse file này — gỡ cả file lẫn bản ghi, nếu
        # không sẽ còn một dòng treo pending vĩnh viễn và khoá luôn checksum đó.
        stored.path.unlink(missing_ok=True)
        await _delete_upload_file(file_id)
        error_id = new_error_id()
        log.error("upload.enqueue_failed", error_id=error_id, error_type=type(exc).__name__, exc_info=exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "Không đẩy được job parse", "error_id": error_id},
        ) from exc

    log.info("upload.accepted", file_id=str(file_id), job_id=job.id, template=template)
    return UploadAccepted(
        file_id=str(file_id),
        job_id=job.id,
        filename=stored.original_filename,
        checksum=stored.checksum,
        size_bytes=stored.size,
        template=template,
    )


@router.get("", response_model=FileList, summary="Lịch sử upload, mới nhất trước")
async def list_files(
    project_id: str | None = Query(default=None, description="Lọc theo dự án"),
    transport_mode: str | None = Query(
        default=None,
        description="Lọc theo loại lô: file_upload (tải file thật) | api_push (lô đồng bộ CRM)",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> FileList:
    """SRS §5.2: lịch sử upload, phân trang theo `uploaded_at`.

    Phase 5.5 P0 (F-1): KHÔNG lọc theo `transport_mode` mặc định — giữ tương
    thích ngược với client cũ. Nhưng mỗi dòng trả kèm `transport_mode` tường
    minh, nên client MỚI (frontend đã cập nhật) tự lọc được bằng tham số này
    thay vì đoán qua việc `filename` có mặt hay không.
    """
    if transport_mode is not None and transport_mode not in _TRANSPORT_MODES:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"transport_mode phải là một trong {_TRANSPORT_MODES}",
                "error_code": "INVALID_TRANSPORT_MODE",
            },
        )

    query = sa.select(upload_files).order_by(upload_files.c.uploaded_at.desc())
    if project_id is not None:
        project_uuid = _parse_file_id(project_id)
        await _ensure_project_in_scope(principal, project_uuid)
        query = query.where(upload_files.c.project_id == project_uuid)
    if transport_mode is not None:
        query = query.where(upload_files.c.transport_mode == transport_mode)

    async with get_session_factory()() as session:
        if project_id is None:
            # Không xin lọc theo dự án cụ thể: liệt kê phải tự thu hẹp về đúng
            # phạm vi của token, không phải trả về mọi dự án rồi trông cậy
            # client tự lọc — cùng nguyên tắc `list_sync_runs` (src/api/sync.py).
            scope_ids = await resolve_scope_project_ids(session, principal)
            if scope_ids != "ALL":
                query = query.where(upload_files.c.project_id.in_(scope_ids) if scope_ids else sa.false())
        rows = (await session.execute(query.limit(limit).offset(offset))).all()

    return FileList(
        items=[
            FileSummary(
                file_id=str(row.id),
                filename=row.filename,
                transport_mode=row.transport_mode if row.transport_mode in _TRANSPORT_MODES else "unknown",
                status=DB_STATUS_TO_API.get(row.status, "pending"),
                rows_ok=row.rows_ok,
                rows_failed=row.rows_failed,
                uploaded_at=row.uploaded_at,
                external_batch_id=row.external_batch_id,
                source_system=row.source_system,
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{file_id}/status",
    response_model=FileStatus,
    summary="Trạng thái parse (frontend poll 3 giây/lần)",
)
async def file_status(file_id: str, principal: DashboardPrincipal = Depends(require_viewer)) -> FileStatus:
    """Đọc từ `upload_files`, không từ job result — trạng thái không bốc hơi sau 500s."""
    file_uuid = _parse_file_id(file_id)
    row = await _fetch_upload_file(file_uuid)
    await _ensure_project_in_scope(principal, row.project_id)

    error_code = None
    message = None
    # Hỏng ở mức file thì lỗi được ghi vào upload_errors với column_name NULL.
    # Chỉ tra khi thật sự thất bại, để đường polling bình thường chỉ tốn 1 query.
    if row.status == "failed" and row.rows_ok == 0:
        errors, _ = await _load_errors(file_uuid, limit=1)
        if errors and errors[0]["column_name"] is None:
            error_code = errors[0]["error_code"]
            message = errors[0]["message"]

    return FileStatus(
        file_id=file_id,
        status=DB_STATUS_TO_API.get(row.status, "pending"),
        rows_total=row.rows_ok + row.rows_failed,
        rows_ok=row.rows_ok,
        rows_failed=row.rows_failed,
        error_code=error_code,
        message=message,
    )


@router.get(
    "/{file_id}/errors",
    response_model=FileErrorList,
    summary="Lỗi validate theo dòng và tên cột",
)
async def file_errors(file_id: str, principal: DashboardPrincipal = Depends(require_viewer)) -> FileErrorList:
    """Tách khỏi `/status` để payload polling không kéo theo hàng trăm lỗi."""
    file_uuid = _parse_file_id(file_id)
    row = await _fetch_upload_file(file_uuid)
    await _ensure_project_in_scope(principal, row.project_id)
    errors, total = await _load_errors(file_uuid, limit=MAX_ERRORS_PER_PAGE)
    return FileErrorList(file_id=file_id, errors=errors, total=total)


@router.get(
    "/{file_id}/errors.csv",
    response_class=StreamingResponse,
    summary="Tải toàn bộ lỗi dạng CSV để sửa rồi nạp lại",
)
async def file_errors_csv(
    file_id: str, principal: DashboardPrincipal = Depends(require_operator)
) -> StreamingResponse:
    """Xuất CSV không giới hạn số dòng — mục đích là sửa hàng loạt ngoài Excel.

    Cần `pipeline_operator` trở lên: xuất hàng loạt không phân trang là mặt rò
    rỉ dữ liệu rộng hơn `/errors` (JSON, có `limit`).

    Ghi BOM UTF-8: thiếu nó thì Excel trên Windows đọc tiếng Việt ra ký tự lạ.
    """
    file_uuid = _parse_file_id(file_id)
    row = await _fetch_upload_file(file_uuid)
    await _ensure_project_in_scope(principal, row.project_id)
    errors, _ = await _load_errors(file_uuid, limit=None)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["row_number", "column_name", "error_code", "message"])
    for error in errors:
        writer.writerow([error["row_number"], error["column_name"] or "", error["error_code"], error["message"]])

    filename = f"loi-{row.filename.rsplit('.', 1)[0]}.csv"
    return StreamingResponse(
        iter(["﻿" + buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
