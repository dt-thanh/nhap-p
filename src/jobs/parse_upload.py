"""Job parse file upload (SRS §5.2 — upload xong thì "chạy parse nền").

Chạy ngoài request cycle vì parse là việc tốn CPU: giữ trong request sẽ chiếm
event loop của uvicorn và khiến các request khác chờ theo.

Ghi kết quả ra CSV staging thay vì trả bản ghi qua Redis: file 20 MB có thể ra
hàng trăm nghìn dòng, nhét vào job result sẽ phình Redis. Staging CSV cũng chính
là đầu vào nhanh nhất cho `COPY` khi tầng DB được cài đặt.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from rq import get_current_job
from sqlalchemy.exc import IntegrityError

from src.logging_config import get_logger, job_id_var
from src.services.absorption import AbsorptionCalculatorService
from src.services.excel_parser import TEMPLATES, ExcelParseError, ExcelParserService
from src.services.import_records import ImportRejectedError, ImportResult, ImportService

log = get_logger("src.jobs.parse_upload")

# Số lỗi trả kèm job result. Toàn bộ lỗi vẫn được đếm ở rows_failed; đây chỉ là
# phần hiển thị để job result không phình trong Redis.
MAX_REPORTED_ERRORS = 200


def run_parse_upload(
    file_path: str,
    template: str,
    *,
    file_id: str | None = None,
    project_id: str | None = None,
    checksum: str = "",
    original_filename: str = "",
) -> dict:
    """Parse một file đã upload rồi nạp vào PostgreSQL.

    Args:
        file_path: đường dẫn file trên volume `uploads` dùng chung với API.
        template: "sales" | "inventory" | "areas" (khoá của `TEMPLATES`).
        file_id: UUID bản ghi `upload_files` do API tạo sẵn. Đi cùng project_id.
        project_id: UUID dự án. API luôn gửi. Nếu None thì CHỈ parse, không ghi
            DB — giữ đường chạy này để test parser mà không cần dựng Postgres.
        checksum: SHA-256 do FileUploadService tính, dùng để chống trùng file.
        original_filename: tên file người dùng đặt, lưu vào `upload_files`.

    Returns:
        dict có `status` = "done" | "failed".

        "failed" CHỈ dành cho file hỏng ở mức cấu trúc — sai định dạng, file
        rỗng, không đọc nổi, template không tồn tại. Trả dict thay vì ném
        exception để job không bị RQ đánh dấu lỗi hệ thống.

        Thiếu cột bắt buộc hay sai dữ liệu KHÔNG rơi vào đây: file vẫn parse
        hết, `status` là "done" với rows_ok/rows_failed và danh sách lỗi theo
        dòng. Ngưỡng bao nhiêu lỗi thì từ chối cả lô là việc của
        ValidationService (SRS §5.2), không phải của parser.
    """
    current = get_current_job()
    token = job_id_var.set(current.id if current else None)
    started = time.perf_counter()

    log.info("parse.job.started", template=template, checksum=checksum)

    source = Path(file_path)
    staging_path = source.with_suffix(".staging.csv")

    try:
        table_template = TEMPLATES.get(template)
        if table_template is None:
            raise ExcelParseError(
                "UNKNOWN_TEMPLATE",
                f"Template '{template}' không tồn tại. Chấp nhận: {', '.join(sorted(TEMPLATES))}",
            )

        result = ExcelParserService().parse_to_csv(source, table_template, staging_path)

        imported: ImportResult | None = None
        if project_id is not None and file_id is not None:
            # RQ worker chạy đồng bộ nên phải tự mở event loop. Engine dùng
            # NullPool (xem src/db.py) để mỗi asyncio.run() không dính connection
            # của loop trước.
            imported = asyncio.run(
                ImportService().load(
                    staging_path,
                    table_template,
                    file_id=file_id,
                    project_id=project_id,
                    parse_errors=result.errors,
                    rows_failed_parse=result.rows_failed,
                )
            )

            # Nạp xong thì chuỗi hấp thụ cũ đã lạc hậu. Tính lại ngay ở đây để
            # dashboard phản ánh đúng file vừa nạp, không phải chờ job đêm.
            asyncio.run(AbsorptionCalculatorService().recompute(project_id))

            # Dữ liệu đã nằm trong DB nên hai file trên đĩa hết vai trò. Không dọn
            # thì volume `uploads` dùng chung giữa api và worker phình mãi.
            # Chỉ dọn khi ĐÃ ghi DB: đường parse-only còn cần file để soi.
            _discard(source)
            _discard(staging_path)

        duration_ms = round((time.perf_counter() - started) * 1000, 1)

        log.info(
            "parse.job.finished",
            template=template,
            rows_ok=result.rows_ok,
            rows_failed=result.rows_failed,
            rows_inserted=imported.rows_inserted if imported else None,
            duration_ms=duration_ms,
        )
        return {
            "status": "done",
            "template": template,
            "original_filename": original_filename,
            "checksum": checksum,
            "rows_total": result.rows_total,
            # Khi đã ghi DB thì con số phải lấy từ tầng ghi, không lấy của parser:
            # parser đếm dòng qua được validate, còn tầng ghi mới biết dòng nào
            # thực sự vào bảng (phân khu không tra được, dòng trùng đều rớt thêm).
            # Trộn hai nguồn sẽ ra rows_ok + rows_failed > rows_total.
            "rows_ok": imported.rows_inserted if imported else result.rows_ok,
            "rows_failed": imported.rows_failed if imported else result.rows_failed,
            "file_id": imported.file_id if imported else None,
            "rows_inserted": imported.rows_inserted if imported else 0,
            "persisted": imported is not None,
            "errors": [
                {
                    "row_number": e.row_number,
                    "column_name": e.column_name,
                    "error_code": e.error_code,
                    "message": e.message,
                }
                for e in result.errors[:MAX_REPORTED_ERRORS]
            ],
            "errors_truncated": result.errors_truncated or len(result.errors) > MAX_REPORTED_ERRORS,
            "staging_path": str(staging_path),
            "duration_ms": duration_ms,
        }

    except ImportRejectedError as exc:
        log.warning("parse.job.threshold_exceeded", template=template, rows_failed=exc.rows_failed)
        return _failed(
            "ERROR_RATE_EXCEEDED",
            str(exc),
            staging_path,
            started,
            template,
            original_filename,
            checksum,
            file_id=file_id,
        )

    except ExcelParseError as exc:
        log.warning("parse.job.rejected", template=template, error_code=exc.error_code)
        return _failed(
            exc.error_code,
            exc.message,
            staging_path,
            started,
            template,
            original_filename,
            checksum,
            file_id=file_id,
        )

    except IntegrityError as exc:
        # Ràng buộc DB chặn cả lô. `ImportService.load()` mở transaction bằng
        # `session.begin()`, nên tới đây transaction ĐÃ rollback xong — kể cả
        # lệnh đặt status='processing'. Bản ghi vì vậy đang ở 'pending'; không
        # đánh dấu tiếp thì nó treo ở đó vĩnh viễn và người dùng poll `/status`
        # không bao giờ thấy kết thúc. `_mark_file_failed` mở session RIÊNG nên
        # nó không dính transaction vừa hỏng.
        #
        # KHÔNG trả str(exc): message của SQLAlchemy kèm nguyên câu SQL và toàn
        # bộ tham số của lô — tức là cả dữ liệu người dùng. Chỉ lấy TÊN ràng buộc
        # từ exception gốc của driver, đủ để người vận hành biết chỗ hỏng.
        constraint = _constraint_name(exc)
        log.error(
            "parse.job.constraint_violation",
            template=template,
            constraint=constraint,
            error_type=type(exc.orig).__name__ if exc.orig is not None else type(exc).__name__,
            exc_info=exc,
        )
        return _failed(
            "CONSTRAINT_VIOLATION",
            (
                f"Lô bị từ chối: dữ liệu vi phạm ràng buộc '{constraint}'"
                if constraint
                else "Lô bị từ chối: dữ liệu vi phạm ràng buộc của cơ sở dữ liệu"
            ),
            staging_path,
            started,
            template,
            original_filename,
            checksum,
            file_id=file_id,
        )

    except OSError as exc:
        # File không đọc được — hay gặp nhất khi volume `uploads` chưa được gắn
        # chung giữa api và worker, lúc đó worker nhìn vào đường dẫn rỗng.
        # KHÔNG trả str(exc) ra ngoài: message của OS có kèm đường dẫn nội bộ.
        log.error(
            "parse.job.unreadable",
            template=template,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return _failed(
            "FILE_UNREADABLE",
            "Không đọc được file đã upload",
            staging_path,
            started,
            template,
            original_filename,
            checksum,
            file_id=file_id,
        )

    finally:
        job_id_var.reset(token)


def _constraint_name(exc: IntegrityError) -> str | None:
    """Moi TÊN ràng buộc bị vi phạm ra khỏi exception, không lấy gì khác.

    SQLAlchemy bọc lỗi asyncpg hai lớp: `exc.orig` là lớp giả DBAPI của dialect,
    còn `__cause__` của nó mới là exception gốc của asyncpg — nơi duy nhất có
    `constraint_name`. Trả None nếu không lần ra được; phía gọi có sẵn câu thông
    báo chung, thà thiếu chi tiết còn hơn ném nguyên `str(exc)` kèm SQL và tham số.
    """
    candidate = getattr(exc, "orig", None)
    for _ in range(3):  # orig → __cause__ → __cause__; đủ sâu, không lặp vô hạn
        if candidate is None:
            return None
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
        candidate = getattr(candidate, "__cause__", None)
    return None


def _discard(path: Path) -> None:
    """Xoá file, nuốt lỗi I/O.

    Dọn rác không được phép làm hỏng một lần nạp đã thành công — dữ liệu đã
    commit rồi, ném lỗi ở đây chỉ khiến RQ đánh dấu job failed một cách sai lệch.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("parse.job.cleanup_failed", error_type=type(exc).__name__)


def _failed(
    error_code: str,
    message: str,
    staging_path: Path,
    started: float,
    template: str,
    original_filename: str,
    checksum: str,
    *,
    file_id: str | None = None,
) -> dict:
    """Kết quả thất bại mức file. Dọn staging và đánh dấu `upload_files` failed.

    Không đánh dấu thì bản ghi treo ở 'pending' mãi và người dùng poll `/status`
    không bao giờ thấy kết thúc.
    """
    staging_path.unlink(missing_ok=True)
    if file_id is not None:
        asyncio.run(_mark_file_failed(file_id, error_code, message))
    return {
        "status": "failed",
        "template": template,
        "original_filename": original_filename,
        "checksum": checksum,
        "error_code": error_code,
        "message": message,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }


async def _mark_file_failed(file_id: str, error_code: str, message: str) -> None:
    """Ghi trạng thái failed + một dòng `upload_errors` mức file.

    `upload_errors.column_name` để NULL là quy ước đánh dấu "lỗi cả file, không
    thuộc cột nào" — `/status` dựa vào đó để trả error_code cho người dùng.
    """
    import sqlalchemy as sa

    from src.db import get_session_factory
    from src.models.tables import upload_errors, upload_files

    now = datetime.now(UTC)
    try:
        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(
                    sa.update(upload_files).where(upload_files.c.id == uuid.UUID(file_id)).values(status="failed")
                )
                await session.execute(
                    sa.insert(upload_errors).values(
                        id=uuid.uuid4(),
                        file_id=uuid.UUID(file_id),
                        row_number=1,
                        column_name=None,
                        error_code=error_code,
                        message=message,
                        created_at=now,
                    )
                )
    except Exception as exc:  # DB có thể không với tới được; đừng che lỗi gốc
        log.error("parse.job.mark_failed_error", error_type=type(exc).__name__, exc_info=exc)
