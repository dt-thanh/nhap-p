"""Phase 6: đề xuất tư vấn dựa trên xếp hạng, CHỜ DUYỆT trước khi coi là cuối
cùng — `AGENTS.md` yêu cầu cứng, không tuỳ chọn.

Ba route: tạo đề xuất (chạy xếp hạng + gọi agent), đọc một đề xuất, duyệt/từ
chối. Không có route nào set thẳng `status='approved'` ngoài `/approve`.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException

from src.agents.graph import agent as langgraph_agent
from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import (
    ApprovalRequest,
    ExecutionRequest,
    ExecutionResponse,
    RecommendationRequest,
    RecommendationResponse,
    RecommendedAction,
)
from src.models.tables import (
    agent_executions,
    agent_recommendations,
    areas,
    projects,
    sales_campaign_units,
    sales_campaigns,
    units,
)
from src.ranking.service import RankingError, run_ranking
from src.services.absorption import AreaService
from src.services.dashboard_auth import DashboardPrincipal, require_project_in_scope, require_role

router = APIRouter(prefix="/agent", tags=["agent"])
log = get_logger("src.api.agent")

require_viewer = require_role("business_viewer")
# Duyệt/từ chối là một QUYẾT ĐỊNH ghi, không phải đọc — đòi vai trò cao hơn mức
# chỉ-xem. Khớp mô hình ba tầng đã có ở `dashboard_auth`; bước duyệt mà
# `AGENTS.md` coi là yêu cầu cứng không thể đứng cùng mức quyền với xem dashboard.
require_approver = require_role("pipeline_operator")
require_executor = require_role("admin")

_RANKING_ERROR_STATUS = {"NO_ACTIVE_CONFIG": 503, "PROJECT_NOT_FOUND": 404}


_FEATURE_LABELS = {
    'unit_available': 'căn đang sẵn sàng bán',
    'unit_demand_norm': 'mức quan tâm ở cấp căn',
    'area_velocity_norm': 'tốc độ bán của phân khu',
    'area_conversion_norm': 'khả năng chuyển đổi của phân khu',
}


def _as_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')


def _signal_coverage(contributions: dict) -> Decimal:
    '''Tỷ trọng trọng số dùng tín hiệu thật, không tính giá trị điền mặc định.'''
    total = sum((_as_decimal(item.get('weight')) for item in contributions.values()), Decimal('0'))
    if total <= 0:
        return Decimal('0')
    resolved = sum(
        (
            _as_decimal(item.get('weight'))
            for item in contributions.values()
            if item.get('source') == 'resolved'
        ),
        Decimal('0'),
    )
    return (resolved / total).quantize(Decimal('0.0001'))


def _top_driver(contributions: dict) -> str:
    resolved = [
        (key, _as_decimal(item.get('contribution')))
        for key, item in contributions.items()
        if item.get('source') == 'resolved'
    ]
    if not resolved:
        return 'chưa có tín hiệu nổi trội'
    key, _ = max(resolved, key=lambda item: item[1])
    return _FEATURE_LABELS.get(key, key)


def _proposal_quality(selected: list[dict]) -> tuple[str, float | None, int]:
    '''Đánh giá rủi ro dùng ranking và độ phủ tín hiệu; không phải xác suất bán.'''
    if not selected:
        return 'high', None, 0
    coverages = [_as_decimal(item['signal_coverage']) for item in selected]
    average_coverage = sum(coverages, Decimal('0')) / Decimal(len(coverages))
    distinct_scores = len({item['score'] for item in selected})
    top_score = max(_as_decimal(item['score']) for item in selected)
    top_ties = sum(1 for item in selected if _as_decimal(item['score']) == top_score)
    if average_coverage < Decimal('0.50') or distinct_scores == 1:
        risk = 'high'
    elif average_coverage < Decimal('0.80') or top_ties > max(2, len(selected) // 2):
        risk = 'medium'
    else:
        risk = 'low'
    return risk, float(average_coverage.quantize(Decimal('0.0001'))), top_ties


def _build_business_summary(
    *,
    project_name: str,
    absorption,
    status_counts: dict[str, int],
    config_version: int,
    selected: list[dict],
    top_ties: int,
) -> str:
    available = status_counts.get('available', 0)
    reserved = status_counts.get('reserved', 0)
    sold_live = status_counts.get('sold', 0)
    total_live = sum(status_counts.values())
    score_values = [_as_decimal(item['score']) for item in selected]
    score_range = (
        f'{min(score_values):.4f}–{max(score_values):.4f}' if score_values else 'không có căn đủ điều kiện'
    )
    area_counts = Counter(item['area_name'] for item in selected)
    area_note = 'chưa có căn đủ điều kiện'
    if area_counts:
        area_name, count = area_counts.most_common(1)[0]
        area_note = f'{count}/{len(selected)} căn thuộc {area_name}'
    tie_note = (
        f'Có {top_ties} căn cùng điểm cao nhất; thứ tự giữa các căn đồng điểm chỉ là quy tắc phá hòa.'
        if top_ties > 1
        else 'Căn đứng đầu không đồng điểm với căn kế tiếp trong nhóm đề xuất.'
    )
    live_note = (
        f'Bản ghi căn đang hoạt động gồm **{available} căn sẵn sàng bán**, '
        f'**{reserved} căn giữ chỗ** và **{sold_live} căn đã bán** (tổng {total_live}).'
    )
    aggregate_note = (
        f'Chuỗi tổng hợp bán hàng/tồn kho ghi nhận **{absorption.units_sold} căn đã bán** và '
        f'**{absorption.units_remaining} căn còn lại**. Hai số này thuộc nguồn tổng hợp lịch sử, '
        'không được dùng riêng để kết luận nhu cầu cao hay thấp.'
    )
    return '\n'.join(
        [
            '## Thực trạng hiện tại',
            f'- **{project_name}:** {live_note}',
            f'- {aggregate_note}',
            f'- Ranking config v{config_version} chọn **{len(selected)} căn đang sẵn sàng bán**; dải điểm {score_range}.',
            '',
            '## Điểm đáng lưu ý',
            f'- Mức tập trung của danh sách: {area_note}.',
            f'- {tie_note}',
            '- Điểm ranking là **mức ưu tiên tương đối**, không phải xác suất bán, doanh thu hay biên lợi nhuận.',
            '- Database hiện chưa có đủ giá bán, chi phí ưu đãi và biên lợi nhuận để khuyến nghị giảm giá hoặc khẳng định tối đa hóa lợi nhuận.',
            '',
            '## Kế hoạch đề xuất',
            f'1. Sau khi được duyệt, tạo danh sách ưu tiên cho {len(selected)} căn bên dưới; chưa đổi giá, trạng thái căn hay dữ liệu CRM.',
            '2. Giao đội bán hàng xác minh nhu cầu và ghi nhận các mốc liên hệ → quan tâm → xem nhà → giữ chỗ trong 7 ngày.',
            '3. So sánh tỷ lệ chuyển đổi của nhóm ưu tiên với nhóm đối chứng; chỉ mở rộng chiến dịch khi kết quả tốt hơn và dữ liệu đủ tin cậy.',
            '4. Mọi thay đổi chính sách giá/ưu đãi phải là một đề xuất riêng, có dữ liệu biên lợi nhuận và người có thẩm quyền phê duyệt.',
        ]
    )


def _uuid_or_422(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail={"message": f"{field} phải là UUID hợp lệ", "error_code": "INVALID_UUID"}
        ) from exc


async def _resolve_project(external_project_id: str) -> uuid.UUID:
    async with get_session_factory()() as session:
        found = await session.scalar(sa.select(projects.c.id).where(projects.c.external_id == external_project_id))
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Không tìm thấy dự án '{external_project_id}'", "error_code": "PROJECT_NOT_FOUND"},
        )
    return found


async def _resolve_area(project_uuid: uuid.UUID, external_area_id: str | None) -> uuid.UUID | None:
    if external_area_id is None:
        return None
    async with get_session_factory()() as session:
        found = await session.scalar(
            sa.select(areas.c.id).where(areas.c.project_id == project_uuid, areas.c.external_id == external_area_id)
        )
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Không tìm thấy phân khu '{external_area_id}' trong dự án này",
                "error_code": "AREA_NOT_FOUND",
            },
        )
    return found


@router.post("/recommendations", status_code=202, response_model=RecommendationResponse)
async def create_recommendation(
    request: RecommendationRequest, principal: DashboardPrincipal = Depends(require_viewer)
) -> RecommendationResponse:
    require_project_in_scope(principal, request.project_id)
    project_uuid = await _resolve_project(request.project_id)
    area_uuid = await _resolve_area(project_uuid, request.area_id)

    try:
        run = await run_ranking(project_uuid, area_uuid, trigger="manual")
    except RankingError as exc:
        raise HTTPException(
            status_code=_RANKING_ERROR_STATUS.get(exc.code, 500),
            detail={"message": exc.message, "error_code": exc.code},
        ) from exc

    absorption = await AreaService().summary(project_uuid)
    absorption_context = {
        "units_remaining": absorption.units_remaining,
        "units_sold": absorption.units_sold,
        "avg_velocity_30d": str(absorption.avg_velocity_30d) if absorption.avg_velocity_30d is not None else None,
    }
    ranking_scores_context = [
        {
            "unit_id": s.unit_id,
            "area_id": s.area_id,
            "score": str(s.score),
            "rank_in_project": s.rank_in_project,
            "rank_in_area": s.rank_in_area,
            'coverage': str(s.coverage),
            'signal_coverage': str(_signal_coverage(s.contributions)),
            'contributions': s.contributions,
        }
        for s in sorted((s for s in run.scores if not s.skipped), key=lambda s: s.rank_in_project)[:20]
    ]

    graph_result = await langgraph_agent.ainvoke(
        {
            "query": run.summary_context,
            "project_id": str(project_uuid),
            "area_id": str(area_uuid) if area_uuid else None,
            "ranking_scores": ranking_scores_context,
            "absorption": absorption_context,
        }
    )
    summary = graph_result.get("summary") or "AI agent không tạo được nội dung."
    recommended_actions = graph_result.get("recommended_actions") or []

    ranked_ids = [uuid.UUID(item["unit_id"]) for item in ranking_scores_context]
    candidate_query = sa.select(units.c.id, units.c.unit_code).where(
        units.c.id.in_(ranked_ids), units.c.status == "available", units.c.deleted_at.is_(None)
    )
    if area_uuid is not None:
        candidate_query = candidate_query.where(units.c.area_id == area_uuid)
    async with get_session_factory()() as session:
        candidate_rows = (await session.execute(candidate_query)).all()
    candidate_by_id = {str(row.id): row.unit_code for row in candidate_rows}
    selected = [item for item in ranking_scores_context if item["unit_id"] in candidate_by_id][:10]
    action_payload = {
        "campaign_name": f"Ưu tiên bán hàng {request.project_id}",
        "unit_ids": [item["unit_id"] for item in selected],
    }
    evidence = [
        {
            "unit_id": item["unit_id"],
            "unit_code": candidate_by_id[item["unit_id"]],
            "rank": item["rank_in_project"],
            "score": item["score"],
        }
        for item in selected
    ]
    if not recommended_actions:
        recommended_actions = [
            {
                "unit_id": item["unit_code"],
                "action": "Đưa vào chiến dịch ưu tiên",
                "reason": f"Hạng {item['rank']} theo ranking hiện tại",
            }
            for item in evidence
        ]

    # Khóa đầu ra hiển thị vào dữ liệu đã kiểm chứng. LLM không được tự chọn UUID,
    # tự suy diễn nhu cầu, hoặc tự đề xuất giảm giá từ hai số tổng tồn/đã bán.
    detail_query = (
        sa.select(units.c.id, units.c.unit_code, units.c.unit_type, areas.c.area_name)
        .select_from(units.join(areas, units.c.area_id == areas.c.id))
        .where(units.c.id.in_([uuid.UUID(item['unit_id']) for item in selected]))
    )
    async with get_session_factory()() as session:
        detail_rows = (await session.execute(detail_query)).all()
        project_name = await session.scalar(sa.select(projects.c.name).where(projects.c.id == project_uuid))
        status_rows = (
            await session.execute(
                sa.select(units.c.status, sa.func.count())
                .select_from(units.join(areas, units.c.area_id == areas.c.id))
                .where(areas.c.project_id == project_uuid, units.c.deleted_at.is_(None))
                .group_by(units.c.status)
            )
        ).all()
    details_by_id = {str(row.id): row for row in detail_rows}
    selected = [
        {
            **item,
            'unit_code': details_by_id[item['unit_id']].unit_code,
            'unit_type': details_by_id[item['unit_id']].unit_type,
            'area_name': details_by_id[item['unit_id']].area_name,
        }
        for item in selected
        if item['unit_id'] in details_by_id
    ]
    risk_level, confidence, top_ties = _proposal_quality(selected)
    status_counts = {str(status): int(count) for status, count in status_rows}
    summary = _build_business_summary(
        project_name=project_name or request.project_id,
        absorption=absorption,
        status_counts=status_counts,
        config_version=run.config_version,
        selected=selected,
        top_ties=top_ties,
    )
    evidence = [
        {
            'unit_id': item['unit_id'],
            'unit_code': item['unit_code'],
            'area_name': item['area_name'],
            'unit_type': item['unit_type'],
            'rank': item['rank_in_project'],
            'score': item['score'],
            'signal_coverage': item['signal_coverage'],
            'top_driver': _top_driver(item['contributions']),
        }
        for item in selected
    ]
    recommended_actions = [
        {
            'unit_id': item['unit_code'],
            'action': 'Ưu tiên tiếp cận và xác minh nhu cầu',
            'reason': (
                f'Hạng {item["rank"]}, điểm {item["score"]} tại {item["area_name"]}; '
                f'tín hiệu đóng góp chính: {item["top_driver"]}.'
            ),
        }
        for item in evidence
    ]

    rec_id = uuid.uuid4()
    generated_at = datetime.now(UTC)
    async with get_session_factory()() as session:
        await session.execute(
            sa.insert(agent_recommendations).values(
                id=rec_id,
                project_id=project_uuid,
                area_id=area_uuid,
                ranking_run_id=run.run_id,
                status="pending_approval",
                summary=summary,
                recommended_actions=recommended_actions,
                model=get_settings().resolved_llm_model,
                generated_at=generated_at,
                action_type="CREATE_PRIORITY_CAMPAIGN",
                action_payload=action_payload,
                evidence=evidence,
                risk_level=risk_level,
                confidence=str(confidence) if confidence is not None else None,
            )
        )
        await session.commit()

    log.info("agent.recommendation.created", recommendation_id=str(rec_id), project_id=str(project_uuid))
    return RecommendationResponse(
        recommendation_id=str(rec_id),
        project_id=request.project_id,
        area_id=request.area_id,
        status="pending_approval",
        ranking_run_id=str(run.run_id),
        summary=summary,
        recommended_actions=[RecommendedAction(**a) for a in recommended_actions],
        generated_at=generated_at,
        action_type="CREATE_PRIORITY_CAMPAIGN",
        action_payload=action_payload,
        evidence=evidence,
        risk_level=risk_level,
        confidence=confidence,
    )


async def _load_recommendation(rec_uuid: uuid.UUID) -> tuple[dict, str | None, str | None]:
    async with get_session_factory()() as session:
        row = (
            (await session.execute(sa.select(agent_recommendations).where(agent_recommendations.c.id == rec_uuid)))
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"message": "Không tìm thấy đề xuất", "error_code": "RECOMMENDATION_NOT_FOUND"},
            )
        project_external_id = await session.scalar(
            sa.select(projects.c.external_id).where(projects.c.id == row["project_id"])
        )
        area_external_id = None
        if row["area_id"] is not None:
            area_external_id = await session.scalar(sa.select(areas.c.external_id).where(areas.c.id == row["area_id"]))
    return dict(row), project_external_id, area_external_id


def _to_response(row: dict, project_external_id: str | None, area_external_id: str | None) -> RecommendationResponse:
    return RecommendationResponse(
        recommendation_id=str(row["id"]),
        project_id=project_external_id or str(row["project_id"]),
        area_id=area_external_id,
        status=row["status"],
        ranking_run_id=str(row["ranking_run_id"]),
        summary=row["summary"],
        recommended_actions=[RecommendedAction(**a) for a in (row["recommended_actions"] or [])],
        generated_at=row["generated_at"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        decision_reason=row["decision_reason"],
        action_type=row.get("action_type"),
        action_payload=row.get("action_payload") or {},
        evidence=row.get("evidence") or [],
        risk_level=row.get("risk_level") or "low",
        confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
        execution_status=row.get("execution_status") or "not_started",
        executed_by=row.get("executed_by"),
        executed_at=row.get("executed_at"),
        execution_result=row.get("execution_result") or {},
    )


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def list_recommendations(
    project_id: str, limit: int = 20, principal: DashboardPrincipal = Depends(require_viewer)
) -> list[RecommendationResponse]:
    require_project_in_scope(principal, project_id)
    project_uuid = await _resolve_project(project_id)
    async with get_session_factory()() as session:
        rows = (
            (
                await session.execute(
                    sa.select(agent_recommendations)
                    .where(agent_recommendations.c.project_id == project_uuid)
                    .order_by(agent_recommendations.c.generated_at.desc())
                    .limit(min(max(limit, 1), 100))
                )
            )
            .mappings()
            .all()
        )
    return [_to_response(dict(row), project_id, None) for row in rows]


@router.get("/recommendations/{rec_id}", response_model=RecommendationResponse)
async def get_recommendation(
    rec_id: str, principal: DashboardPrincipal = Depends(require_viewer)
) -> RecommendationResponse:
    rec_uuid = _uuid_or_422(rec_id, "rec_id")
    row, project_external_id, area_external_id = await _load_recommendation(rec_uuid)
    require_project_in_scope(principal, project_external_id)
    return _to_response(row, project_external_id, area_external_id)


async def _decide(
    rec_id: str, new_status: str, request: ApprovalRequest, principal: DashboardPrincipal
) -> RecommendationResponse:
    rec_uuid = _uuid_or_422(rec_id, "rec_id")
    row, project_external_id, area_external_id = await _load_recommendation(rec_uuid)
    require_project_in_scope(principal, project_external_id)

    if row["status"] != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Đề xuất đã ở trạng thái '{row['status']}', không thể đổi lại",
                "error_code": "ALREADY_DECIDED",
            },
        )

    decided_at = datetime.now(UTC)
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(agent_recommendations)
            .where(agent_recommendations.c.id == rec_uuid)
            .values(status=new_status, decided_by=request.actor, decided_at=decided_at, decision_reason=request.reason)
        )
        await session.commit()

    log.info("agent.recommendation.decided", recommendation_id=str(rec_uuid), status=new_status, actor=request.actor)
    row = {
        **row,
        "status": new_status,
        "decided_by": request.actor,
        "decided_at": decided_at,
        "decision_reason": request.reason,
    }
    return _to_response(row, project_external_id, area_external_id)


@router.post("/recommendations/{rec_id}/approve", response_model=RecommendationResponse)
async def approve_recommendation(
    rec_id: str, request: ApprovalRequest, principal: DashboardPrincipal = Depends(require_approver)
) -> RecommendationResponse:
    return await _decide(rec_id, "approved", request, principal)


@router.post("/recommendations/{rec_id}/reject", response_model=RecommendationResponse)
async def reject_recommendation(
    rec_id: str, request: ApprovalRequest, principal: DashboardPrincipal = Depends(require_approver)
) -> RecommendationResponse:
    return await _decide(rec_id, "rejected", request, principal)


@router.post("/recommendations/{rec_id}/execute", response_model=ExecutionResponse)
async def execute_recommendation(
    rec_id: str, request: ExecutionRequest, principal: DashboardPrincipal = Depends(require_executor)
) -> ExecutionResponse:
    """Execute one approved, allow-listed action exactly once."""
    if not request.confirmed:
        raise HTTPException(
            status_code=409,
            detail={"message": "Cần xác nhận riêng trước khi thực thi", "error_code": "CONFIRMATION_REQUIRED"},
        )
    rec_uuid = _uuid_or_422(rec_id, "rec_id")
    now = datetime.now(UTC)
    execution_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    async with get_session_factory()() as session:
        async with session.begin():
            row = (
                (
                    await session.execute(
                        sa.select(agent_recommendations).where(agent_recommendations.c.id == rec_uuid).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail={"message": "Không tìm thấy đề xuất", "error_code": "RECOMMENDATION_NOT_FOUND"},
                )
            project_external_id = await session.scalar(
                sa.select(projects.c.external_id).where(projects.c.id == row["project_id"])
            )
            require_project_in_scope(principal, project_external_id)
            if row["status"] != "approved":
                raise HTTPException(
                    status_code=409,
                    detail={"message": "Đề xuất phải được duyệt trước khi thực thi", "error_code": "APPROVAL_REQUIRED"},
                )
            if row["execution_status"] == "executed":
                raise HTTPException(
                    status_code=409, detail={"message": "Đề xuất đã được thực thi", "error_code": "ALREADY_EXECUTED"}
                )
            if row["action_type"] != "CREATE_PRIORITY_CAMPAIGN":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Loại hành động chưa được executor cho phép",
                        "error_code": "ACTION_NOT_ALLOWED",
                    },
                )

            payload = row["action_payload"] or {}
            try:
                target_ids = [uuid.UUID(value) for value in payload.get("unit_ids", [])]
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "Payload đề xuất không hợp lệ", "error_code": "INVALID_ACTION_PAYLOAD"},
                ) from exc
            if not target_ids or len(target_ids) > 50:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "Chiến dịch cần từ 1 đến 50 căn", "error_code": "INVALID_TARGET_COUNT"},
                )
            eligible = set(
                (
                    await session.execute(
                        sa.select(units.c.id)
                        .select_from(units.join(areas, units.c.area_id == areas.c.id))
                        .where(
                            units.c.id.in_(target_ids),
                            areas.c.project_id == row["project_id"],
                            units.c.status == "available",
                            units.c.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if eligible != set(target_ids):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Một hoặc nhiều căn đã thay đổi; cần tạo và duyệt lại đề xuất",
                        "error_code": "TARGETS_CHANGED",
                    },
                )

            await session.execute(
                sa.insert(agent_executions).values(
                    id=execution_id,
                    recommendation_id=rec_uuid,
                    action_type=row["action_type"],
                    status="executing",
                    actor=request.actor,
                    result={},
                    started_at=now,
                )
            )
            await session.execute(
                sa.insert(sales_campaigns).values(
                    id=campaign_id,
                    recommendation_id=rec_uuid,
                    project_id=row["project_id"],
                    area_id=row["area_id"],
                    name=str(payload.get("campaign_name") or "Chiến dịch ưu tiên bán"),
                    status="active",
                    created_by=request.actor,
                    created_at=now,
                )
            )
            await session.execute(
                sa.insert(sales_campaign_units),
                [
                    {
                        "campaign_id": campaign_id,
                        "unit_id": unit_id,
                        "priority": index,
                        "reason": "Approved AI ranking recommendation",
                    }
                    for index, unit_id in enumerate(target_ids, start=1)
                ],
            )
            result = {"campaign_id": str(campaign_id), "unit_count": len(target_ids), "status": "active"}
            await session.execute(
                sa.update(agent_executions)
                .where(agent_executions.c.id == execution_id)
                .values(
                    status="executed",
                    result=result,
                    finished_at=now,
                )
            )
            await session.execute(
                sa.update(agent_recommendations)
                .where(agent_recommendations.c.id == rec_uuid)
                .values(
                    execution_status="executed",
                    executed_by=request.actor,
                    executed_at=now,
                    execution_result=result,
                )
            )
    log.info(
        "agent.recommendation.executed",
        recommendation_id=str(rec_uuid),
        execution_id=str(execution_id),
        actor=request.actor,
    )
    return ExecutionResponse(
        recommendation_id=str(rec_uuid),
        execution_id=str(execution_id),
        action_type="CREATE_PRIORITY_CAMPAIGN",
        status="executed",
        result=result,
        executed_by=request.actor,
        executed_at=now,
    )
