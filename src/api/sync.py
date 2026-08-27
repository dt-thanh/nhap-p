"""Router đồng bộ CRM → ứng dụng (S2).

Ba endpoint, cùng hình dạng với router `files` đã có: một đường ghi nhận lô, một
đường tra trạng thái để client poll, một đường tra lỗi tách riêng để payload
polling không kéo theo hàng trăm dòng lỗi.

Đồng bộ chạy NGAY trong request, không đẩy qua hàng đợi: payload JSON đã nằm sẵn
trong bộ nhớ (khác file 20 MB phải parse), và một lô CRM ở quy mô pilot là vài
trăm bản ghi. Thêm hàng đợi ở đây chỉ thêm một trạng thái trung gian để sai.

Router cố tình mỏng: kiểm tra request, gọi service, ánh xạ `error_code` sang mã
HTTP. Toàn bộ quyết định nghiệp vụ nằm ở `src/services/`.

**Bốn cổng, theo đúng thứ tự này** (Phase 3):

1. **Xác thực** — khoá API băm, buộc vào một `source_instance_id`.
2. **Kích thước** — đo trên byte thô, chặn TRƯỚC khi parse JSON.
3. **Hợp đồng** — JSON Schema 2020-12, chỉ hỏi về hình dạng.
4. **Nghiệp vụ** — `JsonPayloadParser` + `SyncRunService`.

Thứ tự không hoán đổi được. Parse trước rồi mới đo kích thước nghĩa là đã cấp
phát bộ nhớ cho cây JSON của một body 50 MB trước khi kịp từ chối nó. Kiểm hợp
đồng trước khi xác thực nghĩa là một người gọi chưa có danh tính vẫn khiến máy
chủ làm việc.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import (
    SyncErrorEntryOut,
    SyncErrorList,
    SyncPayloadOut,
    SyncRecordError,
    SyncRunAccepted,
    SyncRunDetail,
    SyncRunErrorList,
    SyncRunList,
    SyncRunSummary,
)
from src.models.tables import upload_errors, upload_files
from src.services.contract_adapter import adapt, adapt_v2, is_contract_v1, is_contract_v2
from src.services.contract_validation import ContractSchemaUnavailableError, ContractValidator
from src.services.contract_validation_v2 import ContractSchemaUnavailableError as ContractSchemaUnavailableErrorV2
from src.services.contract_validation_v2 import ContractValidatorV2
from src.services.dashboard_auth import (
    DashboardPrincipal,
    audit,
    authenticate_dashboard,
    require_role,
    resolve_scope_project_ids,
)
from src.services.dashboard_auth import role_level as _role_level
from src.services.json_payload import EnvelopeError, JsonPayloadParser
from src.services.sync_credentials import CredentialError, SyncCredentialService
from src.services.sync_payloads import PayloadTooLargeError, SyncPayloadService, measure
from src.services.sync_runs import SyncRejectedError, SyncRunService

router = APIRouter(tags=["sync"])
log = get_logger("src.api.sync")

MAX_ERRORS_PER_PAGE = 500
MAX_SYNC_RUNS_PER_PAGE = 200

# Phase 5.5 P0 (Bước 2/3): mặt đọc vận hành đòi vai trò TỐI THIỂU `pipeline_operator`.
# Người xem doanh nghiệp (`business_viewer`) không chạm được nội bộ pipeline —
# xem bảng quyền tối thiểu trong `docs/roadmap.md` Phase 5.5.
require_operator = require_role("pipeline_operator")

# Xác thực hỏng thì trả mã nào. 401 = không chứng minh được danh tính; 403 = danh
# tính hợp lệ nhưng không được phép ghi vào phạm vi này. Gộp cả hai vào 401 sẽ
# khiến người tích hợp đi sửa nhầm chỗ.
# Chạy lại hỏng ở mức nào thì trả mã nào. 404 = không có lô; 409 = có lô nhưng
# trạng thái của nó không cho chạy lại; 422 = có lô, trạng thái đúng, nhưng payload
# không dùng được.
_REPROCESS_STATUS = {
    "SYNC_RUN_NOT_FOUND": 404,
    "RUN_NOT_REPROCESSABLE": 409,
    "PAYLOAD_NOT_RETAINED": 422,
    "RETAINED_PAYLOAD_UNPARSEABLE": 422,
}

_CREDENTIAL_STATUS = {
    "MISSING_API_KEY": 401,
    "INVALID_API_KEY": 401,
    "EXPIRED_API_KEY": 401,
    "REVOKED_API_KEY": 401,
    "INSTANCE_MISMATCH": 403,
}

# Phong bì sai ở mức nào thì trả mã nào. Mặc định 422 — dữ liệu người gửi sai,
# sửa rồi gửi lại được.
_ENVELOPE_STATUS = {
    "UNSUPPORTED_ENTITY": 404,
    "ENTITY_MISMATCH": 409,
    "UNSUPPORTED_SCHEMA_VERSION": 422,
    "UNKNOWN_PROJECT": 422,
    # Phase D (v2) — từ chối CẢ PHONG BÌ, ném từ `SyncRunService._resolve_project`.
    # `PARENT_ARCHIVED`/`AREA_NATURAL_KEY_CONFLICT`/`PARENT_HAS_LIVE_CHILDREN` là
    # lỗi TỪNG BẢN GHI (`ProjectionError`, ghi vào `upload_errors`, KHÔNG đi qua
    # bảng này — lô vẫn 202/200 với `rows_failed>0`).
    "PROJECT_NOT_FOUND": 422,
}


def _decode_json(raw_body: bytes) -> dict:
    """Byte → JSON. Ném ValueError để phía gọi trả 400 có cấu trúc.

    Đọc body dưới dạng byte (thay vì để FastAPI parse qua `Body(...)`) là điều
    kiện để cổng kích thước chặn được TRƯỚC khi cây JSON được dựng.
    """
    import json

    if not raw_body:
        raise ValueError("body rỗng")
    return json.loads(raw_body)


async def _authenticate(api_key: str | None, claimed_instance: str | None):
    """Xác thực khoá API và buộc nó vào `source_instance_id` mà lô tự khai.

    Xác thực là BẮT BUỘC cho mọi lô đồng bộ. Không có cờ tắt: một cờ như thế sẽ
    có ngày được bật nhầm ở môi trường thật, và lúc đó không ai biết lô nào đã
    vào bằng đường không xác thực.
    """
    try:
        async with get_session_factory()() as session:
            async with session.begin():
                return await SyncCredentialService().authenticate(
                    session, api_key=api_key or "", claimed_instance_id=claimed_instance
                )
    except CredentialError as exc:
        raise HTTPException(
            status_code=_CREDENTIAL_STATUS.get(exc.error_code, 401),
            detail={"message": exc.message, "error_code": exc.error_code},
            # Chuẩn HTTP: 401 phải kèm WWW-Authenticate để client biết cách thử lại.
            headers={"WWW-Authenticate": "X-API-Key"},
        ) from exc


async def _authorize_reprocess(
    x_api_key: str | None, authorization: str | None, run, *, confirm: bool
) -> None:
    """Cho qua nếu KHOÁ CRM đúng phạm vi (đường vốn có), HOẶC vai trò vận hành đủ
    (đường mới, Phase 5.5 P0) — hai đường CỘNG, không đường nào bắt buộc.

    Ưu tiên thử `Authorization` trước nếu có mặt: một request mang cả hai header
    (hiếm, nhưng có thể do proxy/gateway thêm vào) được xử lý theo đường có chủ
    đích rõ ràng hơn — người gọi đã CHỌN gửi token vai trò.
    """
    if authorization is not None:
        principal = await authenticate_dashboard(authorization)
        if _role_level(principal.role) < _role_level("pipeline_operator"):
            raise HTTPException(
                status_code=403,
                detail={
                    "message": f"Vai trò '{principal.role}' không đủ quyền chạy lại lô",
                    "error_code": "INSUFFICIENT_ROLE",
                },
            )
        if not confirm:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Chạy lại lô qua vai trò vận hành cần xác nhận rõ ràng: gửi kèm confirm=true",
                    "error_code": "CONFIRMATION_REQUIRED",
                },
            )
        audit("reprocess", principal, sync_run_id=str(run["id"]), external_batch_id=run["external_batch_id"])
        return

    await _authenticate(x_api_key, run["source_instance_id"])


async def _authorize_sync_run_read(x_api_key: str | None, authorization: str | None, run) -> None:
    """Đọc trạng thái/lỗi MỘT lô — cùng mô hình xác thực KÉP với
    `_authorize_reprocess`: khoá CRM đúng `source_instance_id` của lô (đường
    VỐN CÓ, hệ nguồn tự poll lô của chính nó), HOẶC vai trò vận hành
    (`business_viewer` trở lên — đây là ĐỌC, không cần `pipeline_operator` như
    reprocess) với dự án của lô nằm trong phạm vi token. Không đường nào dựa
    vào việc `sync_run_id` (UUID) khó đoán để coi là đủ an toàn — hai route này
    từng hoàn toàn không xác thực.
    """
    if authorization is not None:
        principal = await authenticate_dashboard(authorization)
        async with get_session_factory()() as session:
            scope_ids = await resolve_scope_project_ids(session, principal)
        if scope_ids != "ALL" and (run["project_id"] is None or run["project_id"] not in scope_ids):
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
                    "error_code": "PROJECT_OUT_OF_SCOPE",
                },
            )
        return

    await _authenticate(x_api_key, run["source_instance_id"])


def _parse_sync_run_id(sync_run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(sync_run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "sync_run_id phải là UUID hợp lệ", "error_code": "INVALID_SYNC_RUN_ID"},
        ) from exc


async def _fetch_run(sync_run_id: uuid.UUID):
    async with get_session_factory()() as session:
        row = (
            (await session.execute(sa.select(upload_files).where(upload_files.c.id == sync_run_id)))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"message": "Không tìm thấy lô đồng bộ", "error_code": "SYNC_RUN_NOT_FOUND"},
        )
    return row


@router.post(
    "/sync/{entity}",
    response_model=SyncRunAccepted,
    summary="Nhận một lô JSON từ CRM và xử lý danh tính bản ghi",
)
async def start_sync(
    entity: str,
    request: Request,
    response: Response,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> SyncRunAccepted:
    """Ghi nhận lô, phân giải danh tính từng bản ghi, trả trạng thái lô.

    Gửi lại đúng `external_batch_id` đã xử lý → trả lô cũ kèm `replayed=true` và
    HTTP 200, không xử lý lại. Lô mới → 202.

    Body được đọc dưới dạng BYTE trước rồi mới parse, để cổng kích thước chặn
    được trước khi cấp phát bộ nhớ cho cây JSON.
    """
    # --- Cổng 2: kích thước, đo trên byte thô -------------------------------
    raw_body = await request.body()
    try:
        payload = _decode_json(raw_body)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": "Body không phải JSON hợp lệ", "error_code": "MALFORMED_JSON", "json_path": "$"},
        ) from exc

    try:
        raw = measure(payload, raw_body=raw_body, content_type=request.headers.get("content-type"))
    except PayloadTooLargeError as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "message": exc.message,
                "error_code": exc.error_code,
                "limit_bytes": exc.limit_bytes,
                "size_bytes": exc.size_bytes,
            },
        ) from exc

    # --- Cổng 1: xác thực ---------------------------------------------------
    # Instance mà lô TỰ KHAI; khoá phải thuộc đúng instance đó.
    claimed_instance = payload.get("source_instance_id") if isinstance(payload, dict) else None
    caller = await _authenticate(x_api_key, claimed_instance)

    # --- Cổng 3: hình dạng theo hợp đồng ------------------------------------
    # Áp cho CẢ payload v1 lẫn v2 — phương ngữ S2 cũ đi thẳng xuống tầng nghiệp vụ
    # như trước (xem docstring `src/services/contract_adapter.py`). `is_contract_v2`
    # kiểm TRƯỚC `is_contract_v1`: cả hai hợp đồng đều bắt buộc `project_ref`, chỉ
    # `schema_version` phân biệt được, và v2 phải được nhận ra ĐÚNG là v2 — kiểm
    # sai thứ tự sẽ khiến một payload v2 lọt vào bộ kiểm v1 và bị từ chối sai lý do.
    if is_contract_v2(payload):
        try:
            violations = ContractValidatorV2().validate(payload)
        except ContractSchemaUnavailableErrorV2 as exc:
            log.error("sync.contract_v2.schema_unavailable", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail={"message": "Schema hợp đồng v2 không nạp được", "error_code": "CONTRACT_SCHEMA_UNAVAILABLE"},
            ) from exc

        if violations:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"Payload không đúng hợp đồng v2: {len(violations)} vi phạm",
                    "error_code": "CONTRACT_VALIDATION_FAILED",
                    "errors": [
                        {
                            "error_category": v.error_category,
                            "error_code": v.error_code,
                            "json_path": v.json_path,
                            "field_name": v.field_name,
                            "message": v.message,
                        }
                        for v in violations
                    ],
                },
            )
        envelope_input = adapt_v2(payload, entity_from_route=entity)
    elif is_contract_v1(payload):
        try:
            violations = ContractValidator().validate(payload)
        except ContractSchemaUnavailableError as exc:
            # Thiếu schema là lỗi CẤU HÌNH của hệ thống này, không phải lỗi người
            # gửi. Trả 500 chứ không phải 422 — 422 sẽ khiến người tích hợp đi sửa
            # payload vốn không có gì sai.
            log.error("sync.contract.schema_unavailable", error=str(exc))
            raise HTTPException(
                status_code=500,
                detail={"message": "Schema hợp đồng không nạp được", "error_code": "CONTRACT_SCHEMA_UNAVAILABLE"},
            ) from exc

        if violations:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": f"Payload không đúng hợp đồng v1: {len(violations)} vi phạm",
                    "error_code": "CONTRACT_VALIDATION_FAILED",
                    "errors": [
                        {
                            "error_category": v.error_category,
                            "error_code": v.error_code,
                            "json_path": v.json_path,
                            "field_name": v.field_name,
                            "message": v.message,
                        }
                        for v in violations
                    ],
                },
            )
        envelope_input = adapt(payload, entity_from_route=entity)
    else:
        envelope_input = payload

    # --- Cổng 4: nghiệp vụ --------------------------------------------------
    try:
        envelope = JsonPayloadParser().parse(envelope_input, entity_from_route=entity)
    except EnvelopeError as exc:
        raise HTTPException(
            status_code=_ENVELOPE_STATUS.get(exc.error_code, 422),
            detail={"message": exc.message, "error_code": exc.error_code, "json_path": exc.json_path},
        ) from exc

    try:
        result = await SyncRunService().run(
            envelope,
            raw_payload=raw,
            credential_id=caller.credential_id if caller else None,
        )
    except SyncRejectedError as exc:
        raise HTTPException(
            status_code=_ENVELOPE_STATUS.get(exc.error_code, 422),
            detail={"message": exc.message, "error_code": exc.error_code, "json_path": exc.json_path},
        ) from exc

    response.status_code = status.HTTP_200_OK if result.replayed else status.HTTP_202_ACCEPTED
    return SyncRunAccepted(
        sync_run_id=result.sync_run_id,
        status=result.status,
        replayed=result.replayed,
        rows_received=result.rows_received,
        rows_ok=result.rows_ok,
        rows_failed=result.rows_failed,
        decisions=result.decisions,
        projections=result.projections,
    )


@router.post(
    "/sync-runs/{sync_run_id}/reprocess",
    response_model=SyncRunAccepted,
    summary="Chạy lại một lô đã hỏng, từ payload thô đã giữ",
)
async def reprocess_sync_run(
    sync_run_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    confirm: bool = Query(
        default=False,
        description="Bắt buộc =true khi gọi bằng vai trò vận hành (Bearer token) — chặn bấm nhầm",
    ),
) -> SyncRunAccepted:
    """Chạy lại lô hỏng sau khi nguyên nhân đã được sửa.

    Không nhận body: đầu vào là payload thô ĐÃ LƯU của chính lô đó. Cho gửi kèm
    payload mới sẽ biến "chạy lại" thành "ghi đè", và lúc đó không ai còn biết lô
    này rốt cuộc chứa gì.

    **Hai đường xác thực CỘNG THÊM nhau (Phase 5.5 P0), không thay thế nhau:**

    1. `X-API-Key` thuộc đúng `source_instance_id` của lô — đường VỐN CÓ (Phase
       3), để chính hệ nguồn tự chạy lại lô của nó. KHÔNG đổi hành vi.
    2. `Authorization: Bearer <token vai trò>` với vai trò `pipeline_operator`
       trở lên — đường MỚI, để người trực vận hành chạy lại lô thay hệ nguồn khi
       cần. Đòi thêm `confirm=true` (chặn bấm nhầm) và để lại một dòng audit log
       — hệ nguồn tự gọi qua đường (1) thì không cần, vì đó là một lệnh gọi máy
       đã có sẵn khoá đúng phạm vi, không phải một cú bấm nút của người vận hành.

    Thiếu CẢ HAI → 401.
    """
    run_uuid = _parse_sync_run_id(sync_run_id)
    run = await _fetch_run(run_uuid)

    await _authorize_reprocess(x_api_key, authorization, run, confirm=confirm)

    try:
        result = await SyncRunService().reprocess(run_uuid)
    except SyncRejectedError as exc:
        raise HTTPException(
            status_code=_REPROCESS_STATUS.get(exc.error_code, 422),
            detail={"message": exc.message, "error_code": exc.error_code, "json_path": exc.json_path},
        ) from exc

    return SyncRunAccepted(
        sync_run_id=result.sync_run_id,
        status=result.status,
        replayed=False,
        rows_received=result.rows_received,
        rows_ok=result.rows_ok,
        rows_failed=result.rows_failed,
        decisions=result.decisions,
        projections=result.projections,
    )


@router.get(
    "/sync-runs/{sync_run_id}",
    response_model=SyncRunDetail,
    summary="Trạng thái một lô đồng bộ (client poll đường này)",
)
async def sync_run_detail(
    sync_run_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> SyncRunDetail:
    """Đọc từ `upload_files` — nguồn duy nhất giữ trạng thái lô cho cả hai đường vào."""
    row = await _fetch_run(_parse_sync_run_id(sync_run_id))
    await _authorize_sync_run_read(x_api_key, authorization, row)
    summary = row["error_summary"] or {}

    return SyncRunDetail(
        sync_run_id=str(row["id"]),
        project_id=str(row["project_id"]),
        source_system=row["source_system"],
        source_instance_id=row["source_instance_id"],
        source_entity=row["source_entity"],
        external_batch_id=row["external_batch_id"],
        input_format=row["input_format"],
        transport_mode=row["transport_mode"],
        sync_mode=row["sync_mode"],
        schema_version=row["schema_version"],
        status=row["status"],
        rows_received=row["rows_received"],
        rows_ok=row["rows_ok"],
        rows_failed=row["rows_failed"],
        decisions=summary.get("decisions", {}),
        projections=summary.get("projections", {}),
        started_at=row["uploaded_at"],
        finished_at=row["finished_at"],
        last_source_cursor=row["last_source_cursor"],
        error_summary=summary,
    )


@router.get(
    "/sync-runs/{sync_run_id}/errors",
    response_model=SyncRunErrorList,
    summary="Lỗi của một lô đồng bộ, kèm json_path để định vị trong phong bì",
)
async def sync_run_errors(
    sync_run_id: str,
    limit: int = Query(default=100, ge=1, le=MAX_ERRORS_PER_PAGE),
    offset: int = Query(default=0, ge=0),
    error_category: str | None = Query(default=None, description="Lọc theo nhóm lỗi"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> SyncRunErrorList:
    """Tách khỏi `/sync-runs/{id}` để đường polling không tải theo danh sách lỗi.

    Sắp theo `created_at` rồi `json_path` để phân trang ổn định — thiếu khoá phụ
    thì hai lỗi cùng thời điểm có thể đổi chỗ giữa hai trang.
    """
    run_uuid = _parse_sync_run_id(sync_run_id)
    run = await _fetch_run(run_uuid)
    await _authorize_sync_run_read(x_api_key, authorization, run)

    condition = [upload_errors.c.file_id == run_uuid]
    if error_category is not None:
        condition.append(upload_errors.c.error_category == error_category)

    async with get_session_factory()() as session:
        total = await session.scalar(sa.select(sa.func.count()).select_from(upload_errors).where(*condition))
        rows = (
            (
                await session.execute(
                    sa.select(upload_errors)
                    .where(*condition)
                    .order_by(upload_errors.c.created_at, upload_errors.c.json_path)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .mappings()
            .all()
        )

    return SyncRunErrorList(
        sync_run_id=sync_run_id,
        errors=[
            SyncRecordError(
                error_category=row["error_category"],
                error_code=row["error_code"],
                message=row["message"],
                json_path=row["json_path"],
                row_number=row["row_number"],
                source_record_id=row["source_record_id"],
                field_name=row["field_name"],
                raw_value_redacted=row["raw_value_redacted"],
                retry_status=row["retry_status"],
                created_at=row["created_at"],
            )
            for row in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


# --- Phase 5.5 P0 — Step 4: mặt đọc vận hành --------------------------------
#
# Ba đường MỚI dưới đây khác `sync_run_detail`/`sync_run_errors` ở TRÊN một điểm
# cốt lõi: hai đường trên là chỗ CHÍNH HỆ NGUỒN đã gửi lô poll lại kết quả của
# CHÍNH NÓ (không xác thực bằng vai trò — giữ nguyên, xem `_authorize_reprocess`
# để biết vì sao không đổi). Ba đường dưới đây là MẶT TỔNG QUAN cho người TRỰC
# vận hành — nhìn xuyên nhiều lô/nhiều hệ nguồn — nên đòi vai trò
# `pipeline_operator` trở lên (`require_operator`).


@router.get(
    "/sync-runs",
    response_model=SyncRunList,
    summary="Tổng quan pipeline: danh sách lô đồng bộ CRM (người trực vận hành)",
)
async def list_sync_runs(
    principal: DashboardPrincipal = Depends(require_operator),
    source: str | None = Query(default=None, description="Lọc theo source_system"),
    status_filter: str | None = Query(default=None, alias="status", description="Lọc theo trạng thái lô"),
    external_batch_id: str | None = Query(default=None, description="Lọc theo mã lô do hệ nguồn cấp"),
    date_from: datetime | None = Query(default=None, alias="from", description="uploaded_at >= mốc này"),
    date_to: datetime | None = Query(default=None, alias="to", description="uploaded_at <= mốc này"),
    order: str = Query(default="desc", pattern="^(asc|desc)$", description="Sắp theo uploaded_at"),
    limit: int = Query(default=50, ge=1, le=MAX_SYNC_RUNS_PER_PAGE),
    offset: int = Query(default=0, ge=0),
) -> SyncRunList:
    """CHỈ lô đồng bộ CRM (`transport_mode='api_push'`) — file tải tay xem ở
    `GET /files?transport_mode=file_upload`. Đây là "mặt tổng quan pipeline"
    đúng nghĩa: nhiều hệ nguồn, nhiều lô, một chỗ nhìn.
    """
    condition = [upload_files.c.transport_mode == "api_push"]
    if source is not None:
        condition.append(upload_files.c.source_system == source)
    if status_filter is not None:
        condition.append(upload_files.c.status == status_filter)
    if external_batch_id is not None:
        condition.append(upload_files.c.external_batch_id == external_batch_id)
    if date_from is not None:
        condition.append(upload_files.c.uploaded_at >= date_from)
    if date_to is not None:
        condition.append(upload_files.c.uploaded_at <= date_to)

    async with get_session_factory()() as session:
        scope_ids = await resolve_scope_project_ids(session, principal)
    if scope_ids != "ALL":
        # Lô KHÔNG gắn dự án nào (`project_id IS NULL` — ví dụ lô TẠO dự án) chỉ
        # hiện với phạm vi ALL, cùng nguyên tắc "không suy được dự án thì không
        # đoán phạm vi" áp dụng xuyên suốt Phase E.
        condition.append(upload_files.c.project_id.in_(scope_ids) if scope_ids else sa.false())

    order_col = upload_files.c.uploaded_at.asc() if order == "asc" else upload_files.c.uploaded_at.desc()

    async with get_session_factory()() as session:
        total = await session.scalar(sa.select(sa.func.count()).select_from(upload_files).where(*condition))
        rows = (
            (
                await session.execute(
                    sa.select(upload_files).where(*condition).order_by(order_col).limit(limit).offset(offset)
                )
            )
            .mappings()
            .all()
        )

    audit("list_sync_runs", principal, count=len(rows))
    return SyncRunList(
        items=[
            SyncRunSummary(
                sync_run_id=str(row["id"]),
                project_id=str(row["project_id"]),
                source_system=row["source_system"],
                source_instance_id=row["source_instance_id"],
                source_entity=row["source_entity"],
                external_batch_id=row["external_batch_id"],
                status=row["status"],
                rows_received=row["rows_received"],
                rows_ok=row["rows_ok"],
                rows_failed=row["rows_failed"],
                started_at=row["uploaded_at"],
                finished_at=row["finished_at"],
            )
            for row in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/sync-errors",
    response_model=SyncErrorList,
    summary="Tra lỗi XUYÊN LÔ (người trực vận hành) — khác /sync-runs/{id}/errors vốn chỉ một lô",
)
async def list_sync_errors(
    principal: DashboardPrincipal = Depends(require_operator),
    source: str | None = Query(default=None, description="Lọc theo source_system của lô chứa lỗi"),
    source_entity: str | None = Query(default=None, alias="entity", description="Lọc theo thực thể"),
    error_code: str | None = Query(default=None),
    error_category: str | None = Query(default=None),
    external_batch_id: str | None = Query(default=None, alias="batch"),
    date_from: datetime | None = Query(default=None, alias="from", description="created_at >= mốc này"),
    date_to: datetime | None = Query(default=None, alias="to", description="created_at <= mốc này"),
    limit: int = Query(default=100, ge=1, le=MAX_ERRORS_PER_PAGE),
    offset: int = Query(default=0, ge=0),
) -> SyncErrorList:
    """JOIN sang `upload_files` để lọc theo `source`/`entity`/`batch` — bản thân
    `upload_errors` không giữ ba trường đó (chúng thuộc về LÔ, không phải lỗi)."""
    condition = []
    if source is not None:
        condition.append(upload_files.c.source_system == source)
    if source_entity is not None:
        condition.append(upload_files.c.source_entity == source_entity)
    if external_batch_id is not None:
        condition.append(upload_files.c.external_batch_id == external_batch_id)
    if error_code is not None:
        condition.append(upload_errors.c.error_code == error_code)
    if error_category is not None:
        condition.append(upload_errors.c.error_category == error_category)
    if date_from is not None:
        condition.append(upload_errors.c.created_at >= date_from)
    if date_to is not None:
        condition.append(upload_errors.c.created_at <= date_to)

    async with get_session_factory()() as session:
        scope_ids = await resolve_scope_project_ids(session, principal)
    if scope_ids != "ALL":
        condition.append(upload_files.c.project_id.in_(scope_ids) if scope_ids else sa.false())

    joined = upload_errors.join(upload_files, upload_errors.c.file_id == upload_files.c.id)
    count_query = sa.select(sa.func.count()).select_from(joined)
    rows_query = (
        sa.select(
            upload_errors,
            upload_files.c.source_system,
            upload_files.c.source_entity,
            upload_files.c.external_batch_id,
        )
        .select_from(joined)
        .order_by(upload_errors.c.created_at.desc(), upload_errors.c.json_path)
        .limit(limit)
        .offset(offset)
    )
    if condition:
        count_query = count_query.where(*condition)
        rows_query = rows_query.where(*condition)

    async with get_session_factory()() as session:
        total = await session.scalar(count_query)
        rows = (await session.execute(rows_query)).mappings().all()

    audit("list_sync_errors", principal, count=len(rows))
    return SyncErrorList(
        errors=[
            SyncErrorEntryOut(
                sync_run_id=str(row["file_id"]),
                error_category=row["error_category"],
                error_code=row["error_code"],
                message=row["message"],
                json_path=row["json_path"],
                row_number=row["row_number"],
                source_record_id=row["source_record_id"],
                field_name=row["field_name"],
                raw_value_redacted=row["raw_value_redacted"],
                retry_status=row["retry_status"],
                created_at=row["created_at"],
                external_batch_id=row["external_batch_id"],
                source_system=row["source_system"],
                source_entity=row["source_entity"],
            )
            for row in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/sync-runs/{sync_run_id}/payload",
    response_model=SyncPayloadOut,
    summary="Payload thô của một lô — CHE mặc định (redacted); raw cần vai trò + điều kiện",
)
async def sync_run_payload(
    sync_run_id: str,
    view: str = Query(default="redacted", pattern="^(redacted|raw)$"),
    confirm: bool = Query(
        default=False, description="Bắt buộc =true để pipeline_operator xem NGUYÊN VĂN (view=raw)"
    ),
    principal: DashboardPrincipal = Depends(require_operator),
) -> SyncPayloadOut:
    """Bảng quyền tối thiểu (Phase 5.5 P0):

    - `business_viewer`: không tới được đây (`require_operator` đã chặn ở tầng
      dependency — 403 trước khi vào hàm này).
    - `pipeline_operator`: `view=redacted` luôn được; `view=raw` CHỈ khi
      `confirm=true` — xem nguyên văn là hành động có chủ đích, không phải mặc định.
    - `admin`: cả hai `view` không cần `confirm`.
    """
    run_uuid = _parse_sync_run_id(sync_run_id)
    run = await _fetch_run(run_uuid)

    async with get_session_factory()() as session:
        scope_ids = await resolve_scope_project_ids(session, principal)
    if scope_ids != "ALL" and run["project_id"] not in scope_ids:
        audit("scope_denied", principal, sync_run_id=sync_run_id)
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
                "error_code": "PROJECT_OUT_OF_SCOPE",
            },
        )

    if view == "raw" and principal.role == "pipeline_operator" and not confirm:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Xem payload NGUYÊN VĂN cần xác nhận rõ ràng: gửi kèm confirm=true",
                "error_code": "RAW_PAYLOAD_CONFIRMATION_REQUIRED",
            },
        )

    async with get_session_factory()() as session:
        stored = await SyncPayloadService().fetch(session, run_uuid)

    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={"message": "Lô này không giữ payload thô", "error_code": "PAYLOAD_NOT_RETAINED"},
        )

    audit("view_payload", principal, sync_run_id=sync_run_id, view=view)
    return SyncPayloadOut(
        sync_run_id=sync_run_id,
        view=view,
        payload_sha256=stored["payload_sha256"],
        payload_bytes=stored["payload_bytes"],
        record_count=stored["record_count"],
        received_at=stored["received_at"],
        payload=stored["payload"] if view == "raw" else None,
    )
