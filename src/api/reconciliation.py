"""Router đối soát: chạy một lượt kiểm tra, đọc kết quả và các phát hiện.

Ba endpoint, cùng hình dạng với router `sync`. Chạy NGAY trong request vì đối
soát là các truy vấn tổng hợp trên phạm vi một dự án — ở quy mô pilot là mili
giây, và thêm hàng đợi chỉ thêm một trạng thái trung gian để sai.

**Ghi đè chốt an toàn là thao tác CÓ CHỦ ĐÍCH.** Nó nằm ở một tham số riêng,
mặc định tắt, và mỗi lần dùng đều để lại một finding ghi rõ `overridden: true`.
Không có cấu hình nào bật nó vĩnh viễn — một cờ như thế sẽ được bật một lần rồi
ở đó mãi, và chốt coi như không tồn tại.
"""

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Body, Header, HTTPException, Query

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import (
    ReconciliationFindingList,
    ReconciliationFindingOut,
    ReconciliationRunOut,
)
from src.models.tables import projects, reconciliation_findings, reconciliation_runs
from src.services.dashboard_auth import audit, authenticate_dashboard, resolve_scope_project_ids
from src.services.dashboard_auth import role_level as _role_level
from src.services.reconciliation import ReconciliationService
from src.services.sync_credentials import CredentialError, SyncCredentialService

router = APIRouter(tags=["reconciliation"])
log = get_logger("src.api.reconciliation")

MAX_FINDINGS_PER_PAGE = 500

_CREDENTIAL_STATUS = {
    "MISSING_API_KEY": 401,
    "INVALID_API_KEY": 401,
    "EXPIRED_API_KEY": 401,
    "REVOKED_API_KEY": 401,
    "INSTANCE_MISMATCH": 403,
}


async def _authenticate(api_key: str | None, claimed_instance: str | None):
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
            headers={"WWW-Authenticate": "X-API-Key"},
        ) from exc


async def _authorize_write(
    x_api_key: str | None, authorization: str | None, project_id: uuid.UUID, source_instance_id: str
) -> None:
    """Chạy một lượt đối soát cần MỘT trong hai: khoá CRM đúng phạm vi (đường vốn
    có, hệ nguồn tự kiểm chính nó), HOẶC vai trò vận hành `pipeline_operator` trở
    lên với dự án nằm trong phạm vi token (đường mới, người dùng Keycloak thật) —
    hai đường CỘNG, không đường nào bắt buộc. Không đường nào đọc/ghi TRƯỚC khi
    tự thân nó cho qua.
    """
    if authorization is not None:
        principal = await authenticate_dashboard(authorization)
        if _role_level(principal.role) < _role_level("pipeline_operator"):
            raise HTTPException(
                status_code=403,
                detail={
                    "message": f"Vai trò '{principal.role}' không đủ quyền chạy đối soát",
                    "error_code": "INSUFFICIENT_ROLE",
                },
            )
        async with get_session_factory()() as session:
            scope_ids = await resolve_scope_project_ids(session, principal)
        if scope_ids != "ALL" and project_id not in scope_ids:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
                    "error_code": "PROJECT_OUT_OF_SCOPE",
                },
            )
        audit("reconciliation_run", principal, project_id=str(project_id))
        return

    await _authenticate(x_api_key, source_instance_id)


async def _load_run(run_uuid: uuid.UUID) -> dict:
    """Tra `reconciliation_runs`, 404 nếu không có. Dùng cho CẢ HAI route đọc
    dưới đây, để `findings` cũng 404 đúng như `run_detail` thay vì âm thầm trả
    danh sách rỗng cho một `run_id` không tồn tại."""
    async with get_session_factory()() as session:
        row = (
            (await session.execute(sa.select(reconciliation_runs).where(reconciliation_runs.c.id == run_uuid)))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"message": "Không tìm thấy lượt đối soát", "error_code": "RECONCILIATION_RUN_NOT_FOUND"},
        )
    return dict(row)


async def _authorize_read(x_api_key: str | None, authorization: str | None, run: dict) -> None:
    """Đọc kết quả đối soát — cùng mô hình xác thực KÉP với `_authorize_write`:
    khoá CRM đúng `source_instance_id` của lượt chạy đó (đường VỐN CÓ), HOẶC vai
    trò dashboard bất kỳ (`business_viewer` trở lên — đây là ĐỌC, không cần
    `pipeline_operator`) với dự án của lượt chạy nằm trong phạm vi token. Không
    đường nào dựa vào việc `run_id` (UUID) khó đoán để coi là đủ an toàn — một
    UUID sở hữu được không phải là một quyết định cấp quyền."""
    if authorization is not None:
        principal = await authenticate_dashboard(authorization)
        async with get_session_factory()() as session:
            scope_ids = await resolve_scope_project_ids(session, principal)
        if scope_ids != "ALL" and run["project_id"] not in scope_ids:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "Thao tác nằm ngoài phạm vi dự án được cấp cho token này",
                    "error_code": "PROJECT_OUT_OF_SCOPE",
                },
            )
        return

    await _authenticate(x_api_key, run["source_instance_id"])


@router.post(
    "/reconciliation/runs",
    response_model=ReconciliationRunOut,
    summary="Chạy một lượt đối soát cho một dự án",
)
async def start_reconciliation(
    payload: dict = Body(...),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ReconciliationRunOut:
    """Chạy đối soát nội bộ, hoặc đối soát theo ảnh chụp.

    `scope='internal'` chứng minh bản sao TỰ NHẤT QUÁN. Nó KHÔNG chứng minh bản
    sao khớp với CRM — điều đó cần `scope='source'` và một CRM có thật.
    """
    project_id = payload.get("project_id")
    source_instance_id = payload.get("source_instance_id")
    if not project_id or not source_instance_id:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "'project_id' và 'source_instance_id' là bắt buộc",
                "error_code": "MISSING_RECONCILIATION_SCOPE",
            },
        )

    try:
        project_uuid = uuid.UUID(str(project_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "'project_id' phải là UUID hợp lệ", "error_code": "INVALID_PROJECT_ID"},
        ) from exc

    await _authorize_write(x_api_key, authorization, project_uuid, source_instance_id)

    async with get_session_factory()() as session:
        exists = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_uuid))
    if exists is None:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Dự án '{project_uuid}' không tồn tại", "error_code": "UNKNOWN_PROJECT"},
        )

    scope = payload.get("scope", "internal")
    if scope not in ("internal", "snapshot"):
        # `source` bị chặn ở đây một cách CÓ CHỦ ĐÍCH: chưa có CRM nào để so, và
        # cho phép chạy nó sẽ tạo ra một kết quả "đạt" không chứng minh điều gì.
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "scope phải là 'internal' hoặc 'snapshot'. 'source' chưa dùng được: chưa có Mini CRM để đối chiếu."
                ),
                "error_code": "UNSUPPORTED_RECONCILIATION_SCOPE",
            },
        )

    result = await ReconciliationService().run(
        project_id=project_uuid,
        source_system=payload.get("source_system", "mini_crm"),
        source_instance_id=source_instance_id,
        scope=scope,
        snapshot_id=payload.get("snapshot_id"),
        snapshot_entity=payload.get("snapshot_entity", "units"),
        override_safety_gate=bool(payload.get("override_safety_gate", False)),
    )

    return ReconciliationRunOut(
        reconciliation_run_id=result.reconciliation_run_id,
        scope=result.scope,
        status=result.status,
        passed=result.passed,
        error_count=result.error_count,
        warning_count=result.warning_count,
        info_count=result.info_count,
        checks_run=result.checks_run,
        tombstoned=result.tombstoned,
        summary=result.summary,
    )


@router.get(
    "/reconciliation/runs/{run_id}",
    response_model=ReconciliationRunOut,
    summary="Kết quả một lượt đối soát",
)
async def reconciliation_run_detail(
    run_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ReconciliationRunOut:
    run_uuid = _parse_uuid(run_id)
    row = await _load_run(run_uuid)
    await _authorize_read(x_api_key, authorization, row)

    return ReconciliationRunOut(
        reconciliation_run_id=str(row["id"]),
        scope=row["scope"],
        status=row["status"],
        passed=row["passed"],
        error_count=row["error_count"],
        warning_count=row["warning_count"],
        info_count=row["info_count"],
        checks_run=row["checks_run"],
        tombstoned=(row["summary"] or {}).get("tombstoned", 0),
        summary=row["summary"] or {},
    )


@router.get(
    "/reconciliation/runs/{run_id}/findings",
    response_model=ReconciliationFindingList,
    summary="Các phát hiện của một lượt đối soát",
)
async def reconciliation_findings_list(
    run_id: str,
    severity: str | None = Query(default=None, description="Lọc theo mức độ"),
    check_code: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=MAX_FINDINGS_PER_PAGE),
    offset: int = Query(default=0, ge=0),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ReconciliationFindingList:
    run_uuid = _parse_uuid(run_id)
    run = await _load_run(run_uuid)
    await _authorize_read(x_api_key, authorization, run)

    condition = [reconciliation_findings.c.reconciliation_run_id == run_uuid]
    if severity is not None:
        condition.append(reconciliation_findings.c.severity == severity)
    if check_code is not None:
        condition.append(reconciliation_findings.c.check_code == check_code)

    async with get_session_factory()() as session:
        total = await session.scalar(sa.select(sa.func.count()).select_from(reconciliation_findings).where(*condition))
        rows = (
            (
                await session.execute(
                    sa.select(reconciliation_findings)
                    .where(*condition)
                    # Sắp tất định: thiếu khoá phụ thì hai finding cùng thời điểm
                    # có thể đổi chỗ giữa hai trang.
                    .order_by(
                        reconciliation_findings.c.severity,
                        reconciliation_findings.c.check_code,
                        reconciliation_findings.c.external_id,
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .mappings()
            .all()
        )

    return ReconciliationFindingList(
        reconciliation_run_id=run_id,
        findings=[
            ReconciliationFindingOut(
                check_code=row["check_code"],
                severity=row["severity"],
                entity=row["entity"],
                external_id=row["external_id"],
                message=row["message"],
                details=row["details"] or {},
                created_at=row["created_at"],
            )
            for row in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "id phải là UUID hợp lệ", "error_code": "INVALID_RECONCILIATION_RUN_ID"},
        ) from exc
