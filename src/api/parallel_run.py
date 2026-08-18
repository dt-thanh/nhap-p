"""Router chạy song song: ghi một lần so sánh, và đọc lại lịch sử so sánh.

Đây là công cụ PHÂN TÍCH cho người trực, không phải đường đọc của dashboard.
Dashboard vẫn phục vụ lineage `legacy_aggregate` như cũ và không biết gì về bảng
này — đó chính là điều kiện để chạy song song mà không cắt sang.

**Bảo vệ bằng `X-Ops-Token`**, dùng chung với đầu dò ở Phase 8A. Cùng lý do: đây
là dữ liệu nội bộ theo từng dự án, còn khoá đồng bộ của CRM được cấp cho một
`source_instance_id` để GHI dữ liệu vào — dùng nó để đọc phân tích nội bộ là cấp
sai quyền cho sai bên. Chưa cấu hình token thì endpoint ĐÓNG (503).

`POST` là động từ ghi duy nhất, và thứ nó ghi là MỘT dòng quan sát. Nó không chạm
`absorption_daily`, không đổi `projects.absorption_calculator`, không xếp hàng gì.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, HTTPException, Query

from src.api.ops import require_ops_token
from src.logging_config import get_logger
from src.models.schemas import (
    CalculatorComparisonList,
    CalculatorComparisonOut,
    ComparisonVerdictList,
    ComparisonVerdictOut,
)
from src.services.comparison_rules import summarise
from src.services.parallel_run import (
    TRIGGER_MANUAL,
    ParallelRunCaptureService,
    UnknownProjectError,
)

router = APIRouter(tags=["parallel-run"])
log = get_logger("src.api.parallel_run")

MAX_HISTORY_PER_PAGE = 500


def _project_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": "project_id phải là UUID hợp lệ", "error_code": "INVALID_PROJECT_ID"},
        ) from exc


@router.post(
    "/parallel-run/{project_id}",
    response_model=CalculatorComparisonOut,
    summary="Chạy hai bộ tính rồi GHI LẠI kết quả so sánh (không đổi lineage nào)",
)
async def capture_comparison(
    project_id: str,
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
) -> CalculatorComparisonOut:
    """So ngay bây giờ và lưu kết quả.

    Ghi đúng MỘT dòng vào `calculator_comparisons`. Không dòng `absorption_daily`
    nào bị đọc để ghi đè, không cờ bộ tính nào bị đổi.
    """
    require_ops_token(x_ops_token)
    project_uuid = _project_uuid(project_id)

    service = ParallelRunCaptureService()
    try:
        result = await service.capture(project_uuid, trigger=TRIGGER_MANUAL)
    except UnknownProjectError as exc:
        raise HTTPException(
            status_code=404,
            detail={"message": str(exc), "error_code": "UNKNOWN_PROJECT"},
        ) from exc

    # Đọc lại ĐÚNG dòng vừa ghi theo id, không phải "dòng mới nhất": hai lần ghi
    # đồng thời thì "mới nhất" có thể là dòng của người khác.
    row = await service.fetch(result.comparison_id)
    assert row is not None  # vừa ghi xong trong cùng tiến trình
    return _as_out(row)


@router.get(
    "/parallel-run/{project_id}",
    response_model=CalculatorComparisonList,
    summary="Lịch sử so sánh của một dự án, mới nhất trước",
)
async def comparison_history(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=MAX_HISTORY_PER_PAGE),
    gate_eligible_only: bool = Query(
        default=False,
        description=(
            "Chỉ lấy dòng có domain_has_data=true (view calculator_comparisons_gate). "
            "Cổng chạy song song sau này BẮT BUỘC dùng chế độ này."
        ),
    ),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
) -> CalculatorComparisonList:
    """Đọc lịch sử. CHỈ ĐỌC — không so lại, không ghi gì."""
    require_ops_token(x_ops_token)
    project_uuid = _project_uuid(project_id)

    rows = await ParallelRunCaptureService().history(project_uuid, limit=limit, gate_only=gate_eligible_only)

    return CalculatorComparisonList(
        project_id=str(project_uuid),
        comparisons=[_as_out(row) for row in rows],
        total=len(rows),
        gate_eligible_only=gate_eligible_only,
    )


@router.get(
    "/parallel-run/{project_id}/verdicts",
    response_model=ComparisonVerdictList,
    summary="Lịch sử so sánh đã PHÂN LOẠI theo bộ quy tắc chênh lệch (8E)",
)
async def comparison_verdicts(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=MAX_HISTORY_PER_PAGE),
    gate_eligible_only: bool = Query(
        default=False,
        description="Chỉ lấy dòng có domain_has_data=true (view calculator_comparisons_gate).",
    ),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
) -> ComparisonVerdictList:
    """Áp bộ quy tắc phân loại lên lịch sử. CHỈ ĐỌC, không so lại, không ghi gì.

    Phân loại tính lại mỗi lần đọc — xem `src/services/comparison_rules.py`. Nhờ
    vậy toàn bộ lịch sử luôn được đọc theo CÙNG một bộ luật: bộ luật hiện hành.
    """
    require_ops_token(x_ops_token)
    project_uuid = _project_uuid(project_id)

    verdicts = await ParallelRunCaptureService().verdicts(project_uuid, limit=limit, gate_only=gate_eligible_only)

    return ComparisonVerdictList(
        project_id=str(project_uuid),
        verdicts=[ComparisonVerdictOut(**verdict.as_dict()) for verdict in verdicts],
        summary=summarise(verdicts),
        gate_eligible_only=gate_eligible_only,
    )


def _as_out(row: dict) -> CalculatorComparisonOut:
    return CalculatorComparisonOut(
        comparison_id=str(row["id"]),
        project_id=str(row["project_id"]),
        compared_at=row["compared_at"],
        trigger=row["trigger"],
        legacy_units_sold=row["legacy_units_sold"],
        legacy_units_remaining=row["legacy_units_remaining"],
        domain_units_sold=row["domain_units_sold"],
        domain_units_remaining=row["domain_units_remaining"],
        domain_units_reserved=row["domain_units_reserved"],
        legacy_has_data=row["legacy_has_data"],
        domain_has_data=row["domain_has_data"],
        matches=row["matches"],
        difference_count=row["difference_count"],
        anomaly_count=row["anomaly_count"],
        differences=row["differences"] or [],
        anomalies=row["anomalies"] or [],
    )
