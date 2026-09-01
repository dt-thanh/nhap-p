"""Router ĐỌC kết quả xếp hạng căn (Phase 6), + một cò tính lại tường minh.

`docs/ranking/implementation_plan.md` §11.4 xếp "endpoint đọc xếp hạng độc lập"
vào nhóm CAN DEFER, và `tests/test_ranking_boundary.py` ghi nó là phần CHƯA làm.
Đợt này làm — vì không có nó thì bảng xếp hạng chỉ tồn tại trong database và
trong prompt gửi cho LLM, không có đường nào tới màn hình của đội bán hàng.

╔══════════════════════════════════════════════════════════════════════════════╗
║  Router này KHÔNG mở thêm lối ghi nào vào bốn bảng xếp hạng.                 ║
╚══════════════════════════════════════════════════════════════════════════════╝

`GET` chỉ đọc. `POST /ranking/run` có làm database đổi, nhưng nó KHÔNG tự viết
câu INSERT/UPDATE nào — nó gọi `src.ranking.service.run_ranking`, đúng và chỉ
đúng cái writer mà `tests/test_ranking_boundary.py` cho phép. Ranh giới "một nơi
ghi duy nhất" vì thế còn nguyên; xem docstring test đó.

Vì sao `POST` cần vai trò cao hơn `GET`: tính lại thay thế TOÀN BỘ
`ranking_scores` của dự án (`_persist_scores` xoá-rồi-chèn). Đó là một thao tác
ghi trên dữ liệu mà người khác đang đọc, không phải một lần làm mới bộ nhớ đệm.

Đường này CỐ Ý tách khỏi `POST /agent/recommendations`: endpoint kia cũng chạy
xếp hạng, nhưng nó còn TẠO một `agent_recommendations` chờ duyệt. Muốn xem bảng
xếp hạng mà buộc phải đẻ ra một đề xuất chờ người duyệt là buộc quy trình phê
duyệt của AGENTS.md phải chạy vì một lý do không liên quan.
"""

import json
import math
import time
import uuid
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.schemas import (
    HierarchicalUnitOut,
    ProjectRankingAreaOut,
    ProjectRankingReportChatOut,
    ProjectRankingReportChatRequest,
    ProjectRankingReportOut,
    ProjectRankingReportProjectOut,
    ProjectRankingReportUnitOut,
    RankedUnitOut,
    RankingConfigDraftIn,
    RankingConfigOut,
    RankingConfigPublishIn,
    RankingConfigPublishOut,
    RankingContributionOut,
    RankingOut,
    RankingPreviewIn,
    RankingPreviewOut,
    RankingRunOut,
    SurveyFeatureBatchIn,
    SurveyFeatureBatchOut,
    UnitPreviewDeltaOut,
    UnitRankingCriterionOut,
    UnitRankingReportOut,
    UnitRankingReportUnitOut,
)
from src.models.tables import areas as areas_table
from src.models.tables import (
    projects,
    ranking_configs,
    ranking_runs,
    ranking_scores,
    ranking_weight_proposals,
    unit_enrichment_attributes,
    units,
)
from src.ranking.bands import DISCLAIMER, as_percent, band_for
from src.ranking.engine import UnitScore, rank_scores
from src.ranking.hierarchical_view import build_hierarchical_units, log_hierarchical_read_observability
from src.ranking.preview import preview_flat_weights
from src.ranking.service import RankingError, run_ranking
from src.services.ai import AIServiceError, generate_content
from src.services.dashboard_auth import (
    DashboardPrincipal,
    require_governance_authoring,
    require_project_in_scope,
    require_role,
)
from src.services.ranking_config import (
    ConfigError,
    HierarchicalConfigError,
    create_draft,
    list_configs,
    publish,
    rollback_to,
)
from src.services.ranking_trigger import trigger_ranking, trigger_ranking_all_projects
from src.services.survey_features import SurveyError, parse_items, upsert_survey_features

router = APIRouter(tags=["ranking"])
require_viewer = require_role("business_viewer")
require_operator = require_role("pipeline_operator")
require_admin = require_role("admin")
require_preview = require_governance_authoring()
MAX_UNITS_PER_PAGE = 200
BANDS = ("high", "medium", "low")
log = get_logger("src.api.ranking")


async def _resolve_project(external_project_id: str) -> tuple[uuid.UUID, str]:
    async with get_session_factory()() as session:
        found = await session.scalar(
            sa.select(projects.c.id).where(projects.c.external_id == external_project_id)
        )
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Không tìm thấy dự án '{external_project_id}'",
                "error_code": "PROJECT_NOT_FOUND",
            },
        )
    return found, external_project_id


async def _resolve_area(external_area_id: str | None, project_id: uuid.UUID | None = None) -> uuid.UUID | None:
    if external_area_id is None:
        return None
    async with get_session_factory()() as session:
        query = sa.select(areas_table.c.id).where(areas_table.c.external_id == external_area_id)
        if project_id is not None:
            query = query.where(areas_table.c.project_id == project_id)
        found = await session.scalar(query)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"Không tìm thấy phân khu '{external_area_id}'", "error_code": "AREA_NOT_FOUND"},
        )
    return found


def _contributions(raw: dict) -> list[RankingContributionOut]:
    """`ranking_scores.contributions` là jsonb {feature_key: {...}} — trải phẳng
    thành danh sách CÓ THỨ TỰ (đóng góp giảm dần) để giao diện không phải tự sắp
    xếp lại, và để hai màn hình khác nhau không hiện hai thứ tự khác nhau."""
    items: list[RankingContributionOut] = []
    for key, entry in (raw or {}).items():
        if not isinstance(entry, dict):
            continue
        items.append(
            RankingContributionOut(
                feature_key=key,
                value=entry.get("value"),
                weight=str(entry.get("weight", "0")),
                direction=str(entry.get("direction", "positive")),
                contribution=str(entry.get("contribution", "0")),
                source=str(entry.get("source", "resolved")),
            )
        )
    items.sort(key=lambda c: (-_to_float(c.contribution), c.feature_key))
    return items


def _hierarchical_out(raw: dict | None) -> HierarchicalUnitOut | None:
    """`raw` is `None` when the read flag is off (no query ran at all) —
    this function does not decide the flag, it only shapes whatever
    `build_hierarchical_units()` already produced (or nothing)."""
    if raw is None:
        return None
    return HierarchicalUnitOut(**raw)


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _effective_score(row: dict) -> Decimal | None:
    """Ranking v3 display helper: `hierarchical_score` when present, else the
    same legacy `score` — never a new computation, just what already drove
    `rank_in_project`/`rank_in_area` for this row (see `_ranking_formula`)."""
    return row["hierarchical_score"] if row["hierarchical_score"] is not None else row["score"]


def _ranking_formula(rows: list[dict]) -> str:
    """`"v3_hierarchical"` iff the PERSISTED `rank_in_project` actually
    differs from what pure-legacy scoring alone would produce for these same
    rows — derived from data, not from the current `ranking_v3_composite_enabled`
    value, which may have changed since this run computed its ranks (a stale
    flag read would mislabel an older run). Reuses the same `rank_scores()`
    tie-break (`unit.created_at`) `_apply_v3_composite_ranks` and
    `preview.py`'s own legacy baseline use, so a tie never produces a false
    "v3" mismatch purely from tie-break drift.
    """
    if not rows:
        return "v2_legacy"
    legacy_scores = [
        UnitScore(
            unit_id=str(row["unit_id"]),
            area_id=str(row["area_id"]),
            score=row["score"],
            coverage=Decimal("0"),
            contributions={},
            skipped=row["score"] is None,
            skip_reason=None,
            tie_break_created_at=row["unit_created_at"],
        )
        for row in rows
    ]
    legacy_rank_by_unit = {s.unit_id: s.rank_in_project for s in rank_scores(legacy_scores)}
    for row in rows:
        if legacy_rank_by_unit.get(str(row["unit_id"])) != row["rank_in_project"]:
            return "v3_hierarchical"
    return "v2_legacy"


async def _ahp_pending_status(session, project_id: uuid.UUID) -> str | None:
    """Additive, honest "AHP approved but not visible in the ranking yet"
    signal — the project's most recently approved AHP proposal's own
    `ahp_application_status`, when it's not yet terminal. Never a new
    `RankingOut.state`/`reason` value; NULL in every other case."""
    status = await session.scalar(
        sa.select(ranking_weight_proposals.c.ahp_application_status)
        .where(
            ranking_weight_proposals.c.project_id == project_id,
            ranking_weight_proposals.c.proposal_type == "ahp_ranking_proposal",
            ranking_weight_proposals.c.ahp_application_status.in_(
                ("pending", "awaiting_prior_run", "queued", "running")
            ),
        )
        .order_by(ranking_weight_proposals.c.updated_at.desc())
        .limit(1)
    )
    return status


@router.get(
    "/ranking",
    response_model=RankingOut,
    summary="Kết quả xếp hạng căn đang lưu của một dự án",
)
async def get_ranking(
    external_project_id: str = Query(..., description="external_id dự án ở Mini CRM"),
    external_area_id: str | None = Query(default=None, description="Lọc theo phân khu"),
    band: str | None = Query(default=None, description="high | medium | low"),
    unit_status: str | None = Query(
        default=None, description="Lọc theo trạng thái căn, ví dụ `available` để chỉ xem hàng còn bán được"
    ),
    sort_by: str = Query(default="legacy_rank", description="legacy_rank | hierarchical_score"),
    limit: int = Query(default=50, ge=1, le=MAX_UNITS_PER_PAGE),
    offset: int = Query(default=0, ge=0),
    principal: DashboardPrincipal = Depends(require_viewer),
) -> RankingOut:
    """Đọc `ranking_scores` — KHÔNG tính lại.

    Tính lại ngay trong một `GET` sẽ khiến mỗi lần mở trang lại thay thế toàn bộ
    điểm của dự án, và hai người mở cùng lúc sẽ ghi đè lẫn nhau. Muốn số mới thì
    gọi `POST /ranking/run` một cách tường minh.

    `band` được lọc TRONG PYTHON chứ không phải bằng SQL: ngưỡng mức nằm ở
    `src/ranking/bands.py` và là hàm thuần. Viết lại ngưỡng đó thành `WHERE score
    >= 0.66` trong câu SQL là tạo ra bản sao thứ hai của cùng một quy tắc, và
    hai bản sao sẽ lệch nhau đúng vào lần đầu ai đó chỉnh ngưỡng.
    """
    project_uuid, project_external_id = await _resolve_project(external_project_id)
    require_project_in_scope(principal, project_external_id)

    if band is not None and band not in BANDS:
        raise HTTPException(
            status_code=422,
            detail={"message": f"band phải thuộc {BANDS}", "error_code": "INVALID_BAND"},
        )

    if sort_by not in {"legacy_rank", "hierarchical_score"}:
        raise HTTPException(
            status_code=422,
            detail={"message": "sort_by phải là legacy_rank hoặc hierarchical_score", "error_code": "INVALID_SORT"},
        )

    area_uuid = await _resolve_area(external_area_id, project_uuid)

    async with get_session_factory()() as session:
        # Metadata phải được đọc độc lập với bảng điểm đã lọc. Nếu dùng dòng
        # đầu của `rows`, một run hoàn tất nhưng không có score (hoặc một filter
        # không khớp score nào) sẽ bị nói nhầm là chưa từng chạy.
        score_meta = (
            await session.execute(
                sa.select(
                    ranking_scores.c.ranking_run_id,
                    ranking_scores.c.computed_at,
                    ranking_runs.c.units_processed,
                    ranking_runs.c.units_ranked,
                    ranking_runs.c.units_skipped,
                    ranking_configs.c.version,
                )
                .select_from(
                    ranking_scores.join(ranking_runs, ranking_scores.c.ranking_run_id == ranking_runs.c.id).join(
                        ranking_configs, ranking_runs.c.config_version_id == ranking_configs.c.id
                    )
                )
                .where(ranking_scores.c.project_id == project_uuid)
                .order_by(ranking_scores.c.computed_at.desc())
                .limit(1)
            )
        ).mappings().first()

        # A completed zero-score run intentionally leaves `ranking_scores`
        # empty. Its append-only run row is therefore the only authoritative
        # evidence that ranking did happen and why it returned no candidates.
        completed_run_meta = None
        if score_meta is None:
            completed_run_meta = (
                await session.execute(
                    sa.select(
                        ranking_runs.c.id.label("ranking_run_id"),
                        ranking_runs.c.finished_at.label("computed_at"),
                        ranking_runs.c.units_processed,
                        ranking_runs.c.units_ranked,
                        ranking_runs.c.units_skipped,
                        ranking_configs.c.version,
                    )
                    .select_from(
                        ranking_runs.join(
                            ranking_configs, ranking_runs.c.config_version_id == ranking_configs.c.id
                        )
                    )
                    .where(ranking_runs.c.project_id == project_uuid, ranking_runs.c.status == "completed")
                    .order_by(ranking_runs.c.finished_at.desc(), ranking_runs.c.enqueued_at.desc())
                    .limit(1)
                )
            ).mappings().first()

        # No completed score/run exists yet.  The newest durable open/failed
        # run is the authoritative progress state; clients must never infer
        # success from a proposal ID or fabricate scores while it is pending.
        lifecycle_run_meta = None
        if score_meta is None and completed_run_meta is None:
            lifecycle_run_meta = (
                await session.execute(
                    sa.select(
                        ranking_runs.c.id.label("ranking_run_id"),
                        ranking_runs.c.status.label("run_status"),
                        ranking_runs.c.enqueued_at,
                        ranking_runs.c.error_summary,
                    )
                    .where(
                        ranking_runs.c.project_id == project_uuid,
                        ranking_runs.c.status.in_(("deferred", "queued", "running", "failed")),
                    )
                    .order_by(ranking_runs.c.enqueued_at.desc())
                    .limit(1)
                )
            ).mappings().first()

        query = (
            sa.select(
                ranking_scores.c.unit_id,
                ranking_scores.c.area_id,
                ranking_scores.c.score,
                ranking_scores.c.rank_in_project,
                ranking_scores.c.rank_in_area,
                ranking_scores.c.weight_coverage,
                ranking_scores.c.contributions,
                ranking_scores.c.computed_at,
                ranking_scores.c.ranking_run_id,
                ranking_scores.c.hierarchical_score,
                ranking_scores.c.hierarchical_contributions,
                units.c.external_unit_id,
                units.c.unit_code,
                units.c.unit_type,
                units.c.status.label("unit_status"),
                units.c.created_at.label("unit_created_at"),
                areas_table.c.area_name,
                unit_enrichment_attributes.c.floor,
                unit_enrichment_attributes.c.direction.label("orientation"),
                unit_enrichment_attributes.c.gross_area_sqm,
                unit_enrichment_attributes.c.standard_price_vnd,
            )
            .select_from(
                ranking_scores.join(units, ranking_scores.c.unit_id == units.c.id)
                .join(areas_table, ranking_scores.c.area_id == areas_table.c.id)
                .outerjoin(unit_enrichment_attributes, unit_enrichment_attributes.c.unit_id == units.c.id)
            )
            .where(ranking_scores.c.project_id == project_uuid)
            .order_by(ranking_scores.c.rank_in_project)
        )
        if area_uuid is not None:
            query = query.where(ranking_scores.c.area_id == area_uuid)
        if unit_status is not None:
            query = query.where(units.c.status == unit_status)

        rows = list((await session.execute(query)).mappings().all())

        # PR-7: read-only hierarchical disclosure, gated by its own kill
        # switch — independent of `hierarchical_ranking_enabled` (which
        # gates whether the post-run COMPUTE step ever wrote these columns
        # at all). When off, no extra query runs and every item's
        # `hierarchical` field stays `None` — byte-for-byte the same shape
        # legacy clients already handle, per this route's own compatibility
        # contract.
        hierarchical_by_unit: dict[str, dict] = {}
        if get_settings().hierarchical_read_enabled:
            started_at = time.monotonic()
            try:
                hierarchical_by_unit = await build_hierarchical_units(session, rows)
            except Exception:  # noqa: BLE001 - a read-disclosure failure must never break the legacy response
                log.error(
                    "ranking.hierarchical_read.failed",
                    project_id=str(project_uuid),
                    latency_ms=round((time.monotonic() - started_at) * 1000, 2),
                )
                hierarchical_by_unit = {}
            else:
                log_hierarchical_read_observability(
                    hierarchical_by_unit,
                    project_id=str(project_uuid),
                    latency_ms=(time.monotonic() - started_at) * 1000,
                )

        hierarchical_rank_by_unit: dict[str, int] = {}
        if get_settings().hierarchical_read_enabled:
            rank_query = (
                sa.select(ranking_scores.c.area_id, ranking_scores.c.hierarchical_score)
                .select_from(ranking_scores.join(units, ranking_scores.c.unit_id == units.c.id))
                .where(
                    ranking_scores.c.project_id == project_uuid,
                    ranking_scores.c.hierarchical_score.is_not(None),
                    units.c.deleted_at.is_(None),
                )
            )
            if area_uuid is not None:
                rank_query = rank_query.where(ranking_scores.c.area_id == area_uuid)
            rank_rows = (await session.execute(rank_query)).mappings().all()
            scores_by_area: dict[str, set[Decimal]] = {}
            for rank_row in rank_rows:
                scores_by_area.setdefault(str(rank_row["area_id"]), set()).add(rank_row["hierarchical_score"])
            rank_by_area_score = {
                area_id: {score: index + 1 for index, score in enumerate(sorted(scores, reverse=True))}
                for area_id, scores in scores_by_area.items()
            }
            hierarchical_rank_by_unit = {
                str(row["unit_id"]): rank_by_area_score[str(row["area_id"])][row["hierarchical_score"]]
                for row in rows
                if row["hierarchical_score"] is not None
            }
            if sort_by == "hierarchical_score":
                rows.sort(
                    key=lambda row: (
                        row["hierarchical_score"] is None,
                        -(row["hierarchical_score"] or Decimal("0")),
                        row["unit_code"],
                    )
                )

        run_meta = score_meta or completed_run_meta
        ahp_pending_status = await _ahp_pending_status(session, project_uuid)

    ranking_formula = _ranking_formula(rows)

    # Mức được tính MỘT lần ở đây rồi dùng lại cho cả bộ lọc lẫn phần đếm, để
    # con số trên chip lọc và số dòng thực tế không bao giờ lệch nhau.
    banded = [(row, band_for(row["score"])) for row in rows]
    band_counts = {name: sum(1 for _, b in banded if b == name) for name in BANDS}

    matched = [(row, b) for row, b in banded if band is None or b == band]
    page = matched[offset : offset + limit]

    state = "ready"
    reason = None
    if run_meta is None:
        if lifecycle_run_meta is None:
            state = "not_run"
            reason = "RANKING_NOT_RUN"
        elif lifecycle_run_meta["run_status"] in ("deferred", "queued"):
            state = "queued"
            reason = "RANKING_QUEUED"
        elif lifecycle_run_meta["run_status"] == "running":
            state = "running"
            reason = "RANKING_RUNNING"
        else:
            state = "failed"
            reason = "RANKING_FAILED"
    elif run_meta["units_processed"] == 0:
        state = "insufficient_data"
        reason = "NO_LIVE_UNITS"
    elif run_meta["units_ranked"] == 0:
        state = "insufficient_data"
        reason = "NO_UNITS_MET_COVERAGE"

    return RankingOut(
        project_id=str(project_uuid),
        external_project_id=project_external_id,
        ranking_run_id=str(run_meta["ranking_run_id"]) if run_meta else (
            str(lifecycle_run_meta["ranking_run_id"]) if lifecycle_run_meta else None
        ),
        state=state,
        reason=reason,
        computed_at=run_meta["computed_at"] if run_meta else None,
        config_version=run_meta["version"] if run_meta else None,
        units_ranked=run_meta["units_ranked"] if run_meta else 0,
        units_skipped=run_meta["units_skipped"] if run_meta else 0,
        band_counts=band_counts,
        items=[
            RankedUnitOut(
                unit_id=str(row["unit_id"]),
                external_unit_id=row["external_unit_id"],
                unit_code=row["unit_code"],
                unit_type=row["unit_type"],
                unit_status=row["unit_status"],
                area_id=str(row["area_id"]),
                area_name=row["area_name"],
                score=str(row["score"]),
                score_percent=as_percent(row["score"]),
                band=row_band,
                rank_in_project=row["rank_in_project"],
                rank_in_area=row["rank_in_area"],
                hierarchical_rank_in_area=hierarchical_rank_by_unit.get(str(row["unit_id"])),
                floor=row["floor"],
                orientation=row["orientation"],
                area_sqm=row["gross_area_sqm"],
                price_vnd=row["standard_price_vnd"],
                weight_coverage=str(row["weight_coverage"]),
                contributions=_contributions(row["contributions"]),
                hierarchical=_hierarchical_out(hierarchical_by_unit.get(str(row["unit_id"]))),
                effective_score=str(_effective_score(row)) if _effective_score(row) is not None else None,
                effective_score_percent=as_percent(_effective_score(row)),
            )
            for row, row_band in page
        ],
        total=len(matched),
        limit=limit,
        offset=offset,
        disclaimer=DISCLAIMER,
        ranking_formula=ranking_formula,
        ahp_pending_status=ahp_pending_status,
    )


def _report_hierarchical(value: HierarchicalUnitOut | None) -> HierarchicalUnitOut | None:
    """Strip internal storage paths from the report-specific evidence view.

    The underlying PR-7 response still has a backwards-compatible field for
    this metadata, but a project report must expose only document identifiers
    and permitted display metadata.
    """
    if value is None:
        return None
    payload = value.model_dump()
    for grain in (payload.get("grains") or {}).values():
        for evidence_ref in grain.get("evidence_refs") or []:
            evidence_ref.pop("object_storage_key", None)
    return HierarchicalUnitOut.model_validate(payload)


# Market/Project are 100% Expert-owned by grain contract (docs/ranking/
# ranking_consultant.md — no CRM feature is ever registered under either
# grain); unlike Area, their persisted contributions carry no per-feature
# crm/expert split, so eligibility of the whole grain is the only available
# signal for "did an Expert criterion contribute here."
_EXPERT_ONLY_GRAINS = ("market", "project")


def _derive_hierarchy_disclosure(
    unit_results: list[ProjectRankingReportUnitOut], hierarchical_weights: dict | None
) -> dict:
    """Backend-only derivation of "is this report CRM-only or Expert-enriched"
    from already-persisted contributions plus the run's own published config —
    never recomputed, never a new persisted column. `hierarchical_weights` is
    None both when the read flag is off and when the active config genuinely
    has no hierarchical_weights — either way there is nothing published to
    disclose, so both collapse to the same honest `not_published` state."""
    if not hierarchical_weights:
        return {
            "hierarchy_status": "not_published",
            "expert_criteria_applied": [],
            "score_mode_counts": {},
            "representative_eligible_grains": [],
            "representative_excluded_grains": {},
            "representative_effective_grain_weights": None,
        }

    expert_criteria: set[str] = set()
    score_mode_counts: dict[str, int] = {}
    representative_eligible: list[str] = []
    representative_excluded: dict = {}
    representative_effective: dict | None = None
    representative_set = False

    for unit in unit_results:
        hierarchical = unit.hierarchical
        if not hierarchical or not hierarchical.available:
            continue
        if hierarchical.score_mode:
            score_mode_counts[hierarchical.score_mode] = score_mode_counts.get(hierarchical.score_mode, 0) + 1
        if not representative_set:
            representative_eligible = list(hierarchical.eligible_grains or [])
            representative_excluded = dict(hierarchical.excluded_grains or {})
            representative_effective = (
                dict(hierarchical.effective_grain_weights) if hierarchical.effective_grain_weights else None
            )
            representative_set = True
        for grain in _EXPERT_ONLY_GRAINS:
            if grain in (hierarchical.eligible_grains or []):
                expert_criteria.update((hierarchical_weights.get(grain) or {}).keys())
        area_grain = (hierarchical.grains or {}).get("area")
        if area_grain is not None and area_grain.expert_feature_keys:
            expert_criteria.update(area_grain.expert_feature_keys)

    return {
        "hierarchy_status": "expert_enriched" if expert_criteria else "crm_only",
        "expert_criteria_applied": sorted(expert_criteria),
        "score_mode_counts": score_mode_counts,
        "representative_eligible_grains": representative_eligible,
        "representative_excluded_grains": representative_excluded,
        "representative_effective_grain_weights": representative_effective,
    }


async def _build_project_ranking_report(
    external_project_id: str, principal: DashboardPrincipal
) -> ProjectRankingReportOut:
    """Build a report from persisted rows only; this function never computes.

    Keeping the report atop `get_ranking` preserves its established scope,
    authorization, feature-flag, and read-only behavior instead of creating a
    second query with subtly different ranking semantics.
    """
    project_uuid, canonical_external_id = await _resolve_project(external_project_id)
    require_project_in_scope(principal, canonical_external_id)
    ranking = await get_ranking(
        external_project_id=canonical_external_id,
        external_area_id=None,
        band=None,
        unit_status=None,
        sort_by="hierarchical_score",
        limit=MAX_UNITS_PER_PAGE,
        offset=0,
        principal=principal,
    )
    async with get_session_factory()() as session:
        project_row = (
            await session.execute(
                sa.select(projects.c.name, projects.c.status, projects.c.source_revision).where(projects.c.id == project_uuid)
            )
        ).mappings().one()
        area_rows = list(
            (
                await session.execute(
                    sa.select(
                        areas_table.c.id,
                        areas_table.c.external_id,
                        areas_table.c.area_name,
                        sa.func.count(units.c.id).label("apartment_count"),
                        sa.func.count(ranking_scores.c.hierarchical_score).label("scored_apartment_count"),
                        sa.func.avg(ranking_scores.c.hierarchical_score).label("average_ahp_score"),
                    )
                    .select_from(
                        areas_table.outerjoin(
                            units,
                            sa.and_(units.c.area_id == areas_table.c.id, units.c.deleted_at.is_(None)),
                        ).outerjoin(
                            ranking_scores,
                            sa.and_(
                                ranking_scores.c.unit_id == units.c.id,
                                ranking_scores.c.project_id == project_uuid,
                            ),
                        )
                    )
                    .where(areas_table.c.project_id == project_uuid)
                    .group_by(
                        areas_table.c.id,
                        areas_table.c.external_id,
                        areas_table.c.area_name,
                    )
                    .order_by(areas_table.c.area_name)
                )
            ).mappings().all()
        )
        hierarchical_weights = None
        if ranking.config_version is not None:
            hierarchical_weights = await session.scalar(
                sa.select(ranking_configs.c.hierarchical_weights).where(
                    ranking_configs.c.version == ranking.config_version
                )
            )

    feature_enabled = get_settings().hierarchical_read_enabled
    unit_results = [
        ProjectRankingReportUnitOut(
            unit_id=item.unit_id,
            unit_code=item.unit_code,
            unit_type=item.unit_type,
            unit_status=item.unit_status,
            area_id=item.area_id,
            area_name=item.area_name,
            hierarchical=_report_hierarchical(item.hierarchical),
        )
        for item in ranking.items
    ]
    persisted_results = sum(1 for item in unit_results if item.hierarchical and item.hierarchical.available)
    hierarchy_disclosure = _derive_hierarchy_disclosure(
        unit_results, hierarchical_weights if feature_enabled else None
    )
    areas_out = [
        ProjectRankingAreaOut(
            area_id=str(row["id"]),
            external_id=row["external_id"],
            name=row["area_name"],
            apartment_count=row["apartment_count"],
            scored_apartment_count=row["scored_apartment_count"] if feature_enabled else 0,
            average_ahp_score=row["average_ahp_score"] if feature_enabled else None,
        )
        for row in area_rows
    ]
    persisted_scores = sum(area.scored_apartment_count for area in areas_out)

    if not feature_enabled:
        state, reason = "feature_disabled", "HIERARCHICAL_READ_DISABLED"
    elif ranking.state == "not_run":
        state, reason = "not_run", ranking.reason or "RANKING_NOT_RUN"
    elif ranking.state != "ready":
        state, reason = "unavailable", ranking.reason or "RANKING_UNAVAILABLE"
    elif persisted_scores == 0:
        state, reason = "no_scored_units", "NO_PERSISTED_HIERARCHICAL_SCORES"
    else:
        state, reason = "ready", None

    return ProjectRankingReportOut(
        state=state,
        reason=reason,
        project=ProjectRankingReportProjectOut(
            external_id=canonical_external_id,
            project_id=str(project_uuid),
            name=project_row["name"],
            status=project_row["status"],
            source_revision=project_row["source_revision"],
        ),
        ranking_run_id=ranking.ranking_run_id,
        config_version=ranking.config_version,
        computed_at=ranking.computed_at,
        units_ranked=ranking.units_ranked,
        units_skipped=ranking.units_skipped,
        ranking_formula=ranking.ranking_formula,
        unit_results=unit_results,
        areas=areas_out,
        total_unit_results=ranking.total,
        results_truncated=ranking.total > len(unit_results),
        persisted_hierarchical_results=persisted_results,
        persisted_hierarchical_scores=persisted_scores,
        provenance={
            "read_only": True,
            "score_level": "unit",
            "score_storage": "ranking_scores.hierarchical_score",
            "contribution_storage": "ranking_scores.hierarchical_contributions",
            "run_storage": "ranking_runs",
            "config_storage": "ranking_configs",
            "freshness_policy": "No approved project-level freshness policy is configured.",
        },
        hierarchy_status=hierarchy_disclosure["hierarchy_status"],
        expert_criteria_applied=hierarchy_disclosure["expert_criteria_applied"],
        score_mode_counts=hierarchy_disclosure["score_mode_counts"],
        representative_eligible_grains=hierarchy_disclosure["representative_eligible_grains"],
        representative_excluded_grains=hierarchy_disclosure["representative_excluded_grains"],
        representative_effective_grain_weights=hierarchy_disclosure["representative_effective_grain_weights"],
    )


@router.get(
    "/ranking/projects/{external_project_id}/report",
    response_model=ProjectRankingReportOut,
    summary="Báo cáo AHP/hierarchical theo căn của một dự án (chỉ đọc)",
)
async def get_project_ranking_report(
    external_project_id: str,
    principal: DashboardPrincipal = Depends(require_viewer),
) -> ProjectRankingReportOut:
    """Expose only persisted unit-level hierarchical results.

    There is deliberately no project score in this response: an aggregation
    policy has not been approved. `GET` remains a pure read and cannot call
    `run_ranking` or any sync/seed path.
    """
    return await _build_project_ranking_report(external_project_id, principal)


def _decimal(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _unit_report_criteria(row: dict) -> list[UnitRankingCriterionOut]:
    """Flatten persisted hierarchy grains and unit-feature contributions.

    Parent grains remain aggregates because their per-feature values are not
    persisted in `hierarchical_contributions`. Unit features are expanded from
    the same run's `ranking_scores.contributions`; their weights are scaled by
    the persisted effective Unit-grain weight so all returned contributions
    remain on the hierarchical total-score scale.
    """
    hierarchical = row.get("hierarchical_contributions")
    if not isinstance(hierarchical, dict):
        return []
    effective = hierarchical.get("effective_grain_weights") or {}
    grains = hierarchical.get("grains") or {}
    criteria: list[UnitRankingCriterionOut] = []

    for grain in ("market", "project", "area"):
        meta = grains.get(grain)
        if not isinstance(meta, dict) or not meta.get("eligible") or meta.get("score") is None:
            continue
        weight = _decimal(effective.get(grain))
        normalized = _decimal(meta.get("score"))
        criteria.append(
            UnitRankingCriterionOut(
                name=grain,
                grain=grain,
                weight=weight,
                normalized_score=normalized,
                contribution=weight * normalized,
            )
        )

    unit_weight = _decimal(effective.get("unit"))
    unit_meta = grains.get("unit") if isinstance(grains.get("unit"), dict) else {}
    unit_coverage = _decimal(unit_meta.get("coverage") or row.get("weight_coverage"))
    expanded_unit_features = 0
    for feature_key, raw in (row.get("contributions") or {}).items():
        if not isinstance(raw, dict) or raw.get("source") != "resolved" or raw.get("value") is None:
            continue
        feature_weight = _decimal(raw.get("weight"))
        normalized = _decimal(raw.get("value"))
        effective_feature_weight = unit_weight * feature_weight / unit_coverage if unit_coverage else Decimal("0")
        criteria.append(
            UnitRankingCriterionOut(
                name=feature_key,
                grain="unit",
                weight=effective_feature_weight,
                normalized_score=normalized,
                contribution=effective_feature_weight * normalized,
            )
        )
        expanded_unit_features += 1

    if not expanded_unit_features and unit_meta.get("score") is not None:
        normalized = _decimal(unit_meta.get("score"))
        criteria.append(
            UnitRankingCriterionOut(
                name="unit",
                grain="unit",
                weight=unit_weight,
                normalized_score=normalized,
                contribution=unit_weight * normalized,
            )
        )
    return sorted(criteria, key=lambda item: (-item.contribution, item.name))


def _build_unit_explanation(
    unit_code: str,
    rank: int | None,
    total_ranked: int,
    criteria: list[UnitRankingCriterionOut],
) -> str | None:
    """Generate one deterministic sentence from the returned breakdown."""
    if rank is None or total_ranked <= 0 or not criteria:
        return None
    high_cutoff = max(1, math.ceil(total_ranked / 3))
    low_cutoff = math.ceil(total_ranked * 2 / 3)
    position = "cao" if rank <= high_cutoff else "thấp" if rank > low_cutoff else "trung bình"
    strongest = max(criteria, key=lambda item: item.contribution)
    weakest = min(criteria, key=lambda item: (item.normalized_score, item.contribution))
    return (
        f"Căn {unit_code} thuộc nhóm xếp hạng {position} (#{rank}/{total_ranked}); "
        f"{strongest.name} đóng góp nhiều nhất ({strongest.contribution:.3f}), "
        f"còn {weakest.name} có điểm chuẩn hóa thấp nhất ({weakest.normalized_score:.3f})."
    )


@router.get(
    "/ranking/projects/{external_project_id}/areas/{external_area_id}/units/{external_unit_id}/report",
    response_model=UnitRankingReportOut,
    summary="Báo cáo AHP đã lưu của một căn trong phân khu (chỉ đọc)",
)
async def get_unit_ranking_report(
    external_project_id: str,
    external_area_id: str,
    external_unit_id: str,
    principal: DashboardPrincipal = Depends(require_viewer),
) -> UnitRankingReportOut:
    project_uuid, canonical_external_id = await _resolve_project(external_project_id)
    require_project_in_scope(principal, canonical_external_id)
    area_uuid = await _resolve_area(external_area_id, project_uuid)

    async with get_session_factory()() as session:
        joined = (
            units.join(areas_table, units.c.area_id == areas_table.c.id)
            .join(projects, areas_table.c.project_id == projects.c.id)
            .outerjoin(ranking_scores, ranking_scores.c.unit_id == units.c.id)
            .outerjoin(ranking_runs, ranking_scores.c.ranking_run_id == ranking_runs.c.id)
            .outerjoin(ranking_configs, ranking_scores.c.config_version_id == ranking_configs.c.id)
            .outerjoin(unit_enrichment_attributes, unit_enrichment_attributes.c.unit_id == units.c.id)
        )
        row = (
            await session.execute(
                sa.select(
                    projects.c.name.label("project_name"),
                    projects.c.status.label("project_status"),
                    projects.c.source_revision.label("project_source_revision"),
                    areas_table.c.area_name,
                    areas_table.c.external_id.label("area_external_id"),
                    units.c.id.label("unit_id"),
                    units.c.external_unit_id,
                    units.c.unit_code,
                    units.c.unit_type,
                    units.c.status.label("unit_status"),
                    ranking_scores.c.ranking_run_id,
                    ranking_scores.c.score,
                    ranking_scores.c.weight_coverage,
                    ranking_scores.c.contributions,
                    ranking_scores.c.hierarchical_score,
                    ranking_scores.c.hierarchical_contributions,
                    ranking_scores.c.computed_at,
                    ranking_configs.c.version.label("config_version"),
                    unit_enrichment_attributes.c.floor,
                    unit_enrichment_attributes.c.direction.label("orientation"),
                    unit_enrichment_attributes.c.gross_area_sqm,
                    unit_enrichment_attributes.c.standard_price_vnd,
                    unit_enrichment_attributes.c.view,
                )
                .select_from(joined)
                .where(
                    projects.c.id == project_uuid,
                    areas_table.c.id == area_uuid,
                    units.c.external_unit_id == external_unit_id,
                    units.c.deleted_at.is_(None),
                )
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"message": "Không tìm thấy căn trong phân khu đã chọn", "error_code": "UNIT_NOT_FOUND"},
            )

        area_stats = (
            await session.execute(
                sa.select(
                    sa.func.count(units.c.id).label("apartment_count"),
                    sa.func.count(ranking_scores.c.hierarchical_score).label("scored_count"),
                    sa.func.avg(ranking_scores.c.hierarchical_score).label("average_score"),
                )
                .select_from(
                    units.outerjoin(ranking_scores, ranking_scores.c.unit_id == units.c.id)
                )
                .where(units.c.area_id == area_uuid, units.c.deleted_at.is_(None))
            )
        ).mappings().one()

        rank_meta = None
        if get_settings().hierarchical_read_enabled and row["hierarchical_score"] is not None:
            ranked = (
                sa.select(
                    ranking_scores.c.unit_id,
                    sa.func.dense_rank()
                    .over(order_by=ranking_scores.c.hierarchical_score.desc())
                    .label("rank"),
                    sa.func.count().over().label("total_ranked"),
                )
                .select_from(ranking_scores.join(units, ranking_scores.c.unit_id == units.c.id))
                .where(
                    ranking_scores.c.project_id == project_uuid,
                    ranking_scores.c.area_id == area_uuid,
                    ranking_scores.c.hierarchical_score.is_not(None),
                    units.c.deleted_at.is_(None),
                )
                .subquery()
            )
            rank_meta = (
                await session.execute(
                    sa.select(ranked.c.rank, ranked.c.total_ranked).where(ranked.c.unit_id == row["unit_id"])
                )
            ).mappings().one_or_none()

        hierarchy = None
        if get_settings().hierarchical_read_enabled and row["hierarchical_contributions"] is not None:
            hierarchy_map = await build_hierarchical_units(session, [row])
            hierarchy = _report_hierarchical(_hierarchical_out(hierarchy_map.get(str(row["unit_id"]))))

        completed_run_exists = bool(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(ranking_runs)
                .where(ranking_runs.c.project_id == project_uuid, ranking_runs.c.status == "completed")
            )
        )

    feature_enabled = get_settings().hierarchical_read_enabled
    criteria = _unit_report_criteria(row) if feature_enabled and row["hierarchical_score"] is not None else []
    rank = int(rank_meta["rank"]) if rank_meta else None
    total_ranked = int(rank_meta["total_ranked"]) if rank_meta else 0
    if not feature_enabled:
        state, reason = "feature_disabled", "HIERARCHICAL_READ_DISABLED"
    elif not completed_run_exists:
        state, reason = "not_run", "RANKING_NOT_RUN"
    elif row["hierarchical_contributions"] is None:
        state, reason = "not_computed", "HIERARCHICAL_NOT_COMPUTED"
    elif row["hierarchical_score"] is None:
        state, reason = "legal_gated", "HIGH_RISK_LEGAL_GATE"
    else:
        state, reason = "ready", None

    return UnitRankingReportOut(
        state=state,
        reason=reason,
        project=ProjectRankingReportProjectOut(
            external_id=canonical_external_id,
            project_id=str(project_uuid),
            name=row["project_name"],
            status=row["project_status"],
            source_revision=row["project_source_revision"],
        ),
        area=ProjectRankingAreaOut(
            area_id=str(area_uuid),
            external_id=row["area_external_id"],
            name=row["area_name"],
            apartment_count=area_stats["apartment_count"],
            scored_apartment_count=area_stats["scored_count"] if feature_enabled else 0,
            average_ahp_score=area_stats["average_score"] if feature_enabled else None,
        ),
        apartment=UnitRankingReportUnitOut(
            apartment_id=row["external_unit_id"],
            internal_unit_id=str(row["unit_id"]),
            code=row["unit_code"],
            unit_type=row["unit_type"],
            status=row["unit_status"],
            floor=row["floor"],
            orientation=row["orientation"],
            area_sqm=row["gross_area_sqm"],
            price_vnd=row["standard_price_vnd"],
            view=row["view"],
        ),
        ranking_run_id=str(row["ranking_run_id"]) if row["ranking_run_id"] else None,
        config_version=row["config_version"],
        computed_at=row["computed_at"],
        total_score=row["hierarchical_score"] if state == "ready" else None,
        rank=rank,
        ranked_apartments_in_area=total_ranked,
        criteria=criteria,
        explanation=_build_unit_explanation(row["unit_code"], rank, total_ranked, criteria),
        hierarchical=hierarchy,
    )


@router.post(
    "/ranking/projects/{external_project_id}/report/chat",
    response_model=ProjectRankingReportChatOut,
    summary="Hỏi đáp bị khóa vào báo cáo AHP theo căn hiện hành",
)
async def chat_about_project_ranking_report(
    external_project_id: str,
    request: ProjectRankingReportChatRequest,
    principal: DashboardPrincipal = Depends(require_viewer),
) -> ProjectRankingReportChatOut:
    """Answer from one server-loaded report snapshot, never inferred intent.

    The requested external ID and current run are validated server-side. User
    text cannot select another project because this route neither calls the
    general chat project-inference helper nor grants the model database tools.
    """
    report = await _build_project_ranking_report(external_project_id, principal)
    if request.ranking_run_id is not None and request.ranking_run_id != report.ranking_run_id:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Báo cáo đã thay đổi; hãy tải lại trước khi tiếp tục hỏi.",
                "error_code": "REPORT_CONTEXT_STALE",
            },
        )
    if report.state != "ready":
        return ProjectRankingReportChatOut(
            response="Thông tin này chưa có trong báo cáo xếp hạng AHP theo căn hiện tại.",
            status="unavailable",
            project_external_id=report.project.external_id,
            ranking_run_id=report.ranking_run_id,
            sources=[{"type": "project_ranking_report", "state": report.state, "reason": report.reason}],
        )

    context = report.model_dump(mode="json")
    prompt = (
        "You are a report-grounded assistant. Answer in Vietnamese using ONLY the JSON REPORT_CONTEXT below. "
        "Do not infer facts, call tools, change project context, or calculate any project-level aggregate score. "
        "If the requested fact is absent, reply exactly that it is unavailable in the current report. "
        "Do not expose internal storage keys.\n\n"
        f"REPORT_CONTEXT:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"USER_QUESTION:\n{request.message}"
    )
    try:
        response, _usage = await generate_content(prompt, max_output_tokens=500, thinking_budget=0)
    except AIServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"message": exc.user_message, "code": exc.code}) from exc

    return ProjectRankingReportChatOut(
        response=response,
        project_external_id=report.project.external_id,
        ranking_run_id=report.ranking_run_id,
        sources=[
            {
                "type": "project_ranking_report",
                "external_project_id": report.project.external_id,
                "ranking_run_id": report.ranking_run_id,
                "score_level": "unit",
            }
        ],
    )


@router.post(
    "/ranking/run",
    response_model=RankingOut,
    status_code=200,
    summary="Tính lại xếp hạng cho một dự án",
)
async def post_ranking_run(
    external_project_id: str = Query(..., description="external_id dự án ở Mini CRM"),
    external_area_id: str | None = Query(default=None, description="Phân khu được nhấn mạnh trong ngữ cảnh agent"),
    principal: DashboardPrincipal = Depends(require_operator),
) -> RankingOut:
    """Chạy lại `src.ranking.service.run_ranking`, rồi trả về đúng hình dạng của
    `GET /ranking` để giao diện không phải gọi hai lần liên tiếp.

    KHÔNG tạo `agent_recommendations`. Bước phê duyệt người của AGENTS.md gắn với
    KHUYẾN NGHỊ, không gắn với việc tính lại một bảng điểm tất định — bắt một
    phép tính lại phải qua vòng duyệt sẽ làm loãng chính vòng duyệt đó.
    """
    project_uuid, project_external_id = await _resolve_project(external_project_id)
    require_project_in_scope(principal, project_external_id)
    area_uuid = await _resolve_area(external_area_id)

    try:
        await run_ranking(project_uuid, area_uuid, trigger="manual")
    except RankingError as exc:
        # NO_ACTIVE_CONFIG là lỗi CẤU HÌNH của hệ thống, không phải lỗi của
        # người gọi — 503 để giao diện nói "chưa có cấu hình xếp hạng" thay vì
        # đổ lỗi cho tham số người dùng vừa nhập.
        status = 503 if exc.code == "NO_ACTIVE_CONFIG" else 404
        raise HTTPException(status_code=status, detail={"message": exc.message, "error_code": exc.code}) from exc

    return await get_ranking(
        external_project_id=project_external_id,
        external_area_id=external_area_id,
        band=None,
        unit_status=None,
        sort_by="legacy_rank",
        limit=50,
        offset=0,
        principal=principal,
    )


def _run_out(row, *, coalesced: bool = False) -> RankingRunOut:
    return RankingRunOut(
        run_id=str(row["id"]),
        project_id=str(row["project_id"]),
        status=row["status"],
        trigger=row["trigger"],
        attempt=row["attempt"],
        scope_ids=row["scope_ids"] or {},
        units_processed=row["units_processed"],
        units_ranked=row["units_ranked"],
        units_skipped=row["units_skipped"],
        enqueued_at=row["enqueued_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_summary=row["error_summary"] or {},
        coalesced=coalesced,
    )


@router.post(
    "/ranking/runs",
    response_model=RankingRunOut,
    status_code=202,
    summary="Xếp một lần tính lại vào hàng đợi (bất đồng bộ)",
)
async def post_ranking_run_async(
    external_project_id: str = Query(..., description="external_id dự án ở Mini CRM"),
    principal: DashboardPrincipal = Depends(require_operator),
) -> RankingRunOut:
    """202, KHÔNG phải 200: công việc mới được NHẬN, chưa xong.

    Khác `POST /ranking/run` ở chỗ nào: endpoint kia chạy đồng bộ và trả luôn
    bảng điểm mới (nút "Tính lại" — người dùng đang ngồi chờ, ~260ms). Endpoint
    này chỉ xếp hàng, dành cho lúc không ai chờ và có thể có nhiều lời gọi dồn
    lại.

    `coalesced=true` nghĩa là đã có một run đang chờ và lời gọi này nhập vào đó
    thay vì tạo run mới — đúng thiết kế chống dồn của §8.3, không phải lỗi. Khi
    đó KHÔNG có job thứ hai nào được đẩy vào RQ.
    """
    project_uuid, project_external_id = await _resolve_project(external_project_id)
    require_project_in_scope(principal, project_external_id)

    run_id, enqueued_job = await trigger_ranking(project_uuid, trigger="manual")
    if run_id is None:
        raise HTTPException(
            status_code=503,
            detail={"message": "Không xếp hàng được lần tính lại", "error_code": "RANKING_ENQUEUE_FAILED"},
        )

    async with get_session_factory()() as session:
        row = (
            await session.execute(sa.select(ranking_runs).where(ranking_runs.c.id == run_id))
        ).mappings().first()
    return _run_out(row, coalesced=not enqueued_job)


@router.get(
    "/ranking/runs/{run_id}",
    response_model=RankingRunOut,
    summary="Trạng thái một lần chạy xếp hạng",
)
async def get_ranking_run(
    run_id: str,
    principal: DashboardPrincipal = Depends(require_viewer),
) -> RankingRunOut:
    """Đường poll cho client đã xếp hàng bằng `POST /ranking/runs`.

    Phạm vi được kiểm SAU khi đọc run: `ranking_runs` chỉ có `project_id` nội
    bộ, phải tra ngược ra `external_id` mới so được với phạm vi của token.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"message": "run_id phải là UUID hợp lệ", "error_code": "INVALID_UUID"}
        ) from exc

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ranking_runs, projects.c.external_id)
                .select_from(ranking_runs.join(projects, ranking_runs.c.project_id == projects.c.id))
                .where(ranking_runs.c.id == run_uuid)
            )
        ).mappings().first()

    if row is None:
        raise HTTPException(
            status_code=404, detail={"message": f"Không tìm thấy lần chạy {run_id}", "error_code": "RUN_NOT_FOUND"}
        )
    require_project_in_scope(principal, row["external_id"])
    return _run_out(row)


# --- Đặc trưng khảo sát (đường NHẬP) ----------------------------------------


@router.post(
    "/ranking/features/survey",
    response_model=SurveyFeatureBatchOut,
    status_code=202,
    summary="Nạp đặc trưng khảo sát và xếp hàng tính lại",
)
async def post_survey_features(
    payload: SurveyFeatureBatchIn,
    principal: DashboardPrincipal = Depends(require_operator),
) -> SurveyFeatureBatchOut:
    """Đường vào cho `view_quality` / `natural_light` / `privacy` / `noise_level`.

    202 vì việc chưa xong khi response trả về: dữ liệu đã ghi, nhưng lần tính
    lại (§8.2 — khảo sát mức nào cũng kéo theo tính lại CẢ dự án) mới chỉ được
    xếp hàng.

    Vai trò `pipeline_operator`: đây là ghi dữ liệu vào mô hình, không phải đọc.
    """
    project_uuid, project_external_id = await _resolve_project(payload.external_project_id)
    require_project_in_scope(principal, project_external_id)

    try:
        items = parse_items([item.model_dump() for item in payload.items])
        counts = await upsert_survey_features(project_id=project_uuid, items=items)
    except SurveyError as exc:
        # 422 cho mọi lỗi kiểm dữ liệu, 404 riêng cho thứ không tồn tại — hai
        # loại này cần hai cách xử lý khác nhau ở phía người nhập.
        status = 404 if exc.code.endswith("_NOT_IN_PROJECT") or exc.code == "PROJECT_NOT_FOUND" else 422
        raise HTTPException(status_code=status, detail={"message": exc.message, "error_code": exc.code}) from exc

    run_id, _enqueued = await trigger_ranking(project_uuid, trigger="survey_snapshot")
    return SurveyFeatureBatchOut(
        project_id=str(project_uuid),
        ranking_run_id=str(run_id) if run_id else None,
        **counts,
    )


# --- Quản trị ranking_configs -----------------------------------------------


def _config_out(row: dict) -> RankingConfigOut:
    return RankingConfigOut(
        id=str(row["id"]),
        version=row["version"],
        status=row["status"],
        weights=row["weights"],
        min_weight_coverage=str(row["min_weight_coverage"]),
        note=row["note"] or "",
        copied_from_version=row["copied_from_version"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        published_by=row["published_by"],
        published_at=row["published_at"],
        archived_at=row["archived_at"],
        hierarchical_weights=row["hierarchical_weights"],
    )


def _config_http(exc: ConfigError | HierarchicalConfigError) -> HTTPException:
    status = (
        404
        if exc.code == "CONFIG_NOT_FOUND"
        else 409
        if exc.code in ("ALREADY_PUBLISHED", "PROPOSAL_NOT_APPROVED")
        else 422
    )
    return HTTPException(status_code=status, detail={"message": exc.message, "error_code": exc.code})


@router.get("/ranking/configs", response_model=list[RankingConfigOut], summary="Toàn bộ lịch sử config")
async def get_ranking_configs(
    principal: DashboardPrincipal = Depends(require_viewer),
) -> list[RankingConfigOut]:
    """Không lọc theo dự án: `ranking_configs` là TOÀN CỤC, một bộ trọng số áp
    cho mọi dự án. Vì thế chỉ cần vai trò, không cần phạm vi dự án."""
    return [_config_out(row) for row in await list_configs()]


@router.post(
    "/ranking/configs",
    response_model=RankingConfigOut,
    status_code=201,
    summary="Soạn một bản nháp config mới",
)
async def post_ranking_config_draft(
    payload: RankingConfigDraftIn,
    principal: DashboardPrincipal = Depends(require_admin),
) -> RankingConfigOut:
    """Tạo `draft` — chưa ảnh hưởng lần chạy nào cho tới khi được publish.

    `admin`, cao hơn cả `POST /ranking/run`: bộ trọng số quyết định thứ hạng của
    MỌI căn ở MỌI dự án, còn tính lại chỉ áp dụng lại bộ trọng số đang có.
    """
    try:
        row = await create_draft(
            weights=payload.weights,
            min_weight_coverage=payload.min_weight_coverage,
            note=payload.note,
            created_by=payload.created_by,
            copied_from_version=payload.copied_from_version,
            hierarchical_weights=payload.hierarchical_weights,
        )
    except (ConfigError, HierarchicalConfigError) as exc:
        raise _config_http(exc) from exc
    return _config_out(row)


@router.post(
    "/ranking/configs/{version}/publish",
    response_model=RankingConfigPublishOut,
    summary="Phát hành một config và xếp hàng tính lại MỌI dự án",
)
async def post_ranking_config_publish(
    version: int,
    payload: RankingConfigPublishIn,
    principal: DashboardPrincipal = Depends(require_admin),
) -> RankingConfigPublishOut:
    """Lưu trữ config đang phát hành, phát hành `version`, rồi xếp hàng tính lại.

    Xếp hàng nằm NGOÀI `ranking_config.publish()` có chủ đích: module đó không
    được biết gì về Redis/RQ, cùng kỷ luật với `src/ranking/service.py`.

    Nếu bước xếp hàng hỏng thì config VẪN đã được phát hành — và đó là hành vi
    đúng: bộ trọng số mới là sự thật mới, còn bảng điểm lạc hậu là chuyện sửa
    được bằng một lần bấm "Tính lại". Cuộn ngược một lần publish chỉ vì Redis
    chết sẽ để hệ thống ở trạng thái khó hiểu hơn nhiều.
    """
    try:
        row = await publish(version=version, published_by=payload.published_by)
    except ConfigError as exc:
        raise _config_http(exc) from exc

    reranked = await trigger_ranking_all_projects(trigger="config_change")
    return RankingConfigPublishOut(config=_config_out(row), reranked=reranked)


@router.post(
    "/ranking/configs/{version}/rollback",
    response_model=RankingConfigPublishOut,
    summary="Quay lại trọng số của một version cũ",
)
async def post_ranking_config_rollback(
    version: int,
    payload: RankingConfigPublishIn,
    principal: DashboardPrincipal = Depends(require_admin),
) -> RankingConfigPublishOut:
    """Rollback là CHÉP trọng số cũ sang một version MỚI rồi phát hành nó, không
    phải sửa lịch sử. Version cũ giữ nguyên `archived`, dòng mới mang
    `copied_from_version` để truy được nguồn."""
    try:
        row = await rollback_to(version=version, created_by=payload.published_by)
    except ConfigError as exc:
        raise _config_http(exc) from exc

    reranked = await trigger_ranking_all_projects(trigger="config_change")
    return RankingConfigPublishOut(config=_config_out(row), reranked=reranked)


@router.post(
    "/ranking/projects/{external_project_id}/preview",
    response_model=RankingPreviewOut,
    summary="Xem trước điểm số theo bộ trọng số CHƯA công bố (chỉ đọc, không ghi gì)",
)
async def post_ranking_preview(
    external_project_id: str,
    payload: RankingPreviewIn,
    principal: DashboardPrincipal = Depends(require_preview),
) -> RankingPreviewOut:
    """Read-only sandbox: scores `payload.weights` against this project's
    REAL, currently-persisted feature data (`src/ranking/preview.py`), then
    diffs against the currently PUBLISHED config's real persisted scores.
    Writes nothing — never call this expecting it to affect `ranking_scores`/
    `ranking_runs`/`ranking_configs`. Legacy flat weights only; a hierarchical
    grain preview is not offered (see `preview.py`'s module docstring for why)."""
    project_uuid, canonical_external_id = await _resolve_project(external_project_id)
    require_project_in_scope(principal, canonical_external_id)
    try:
        result = await preview_flat_weights(
            project_uuid, weights=payload.weights, min_weight_coverage=Decimal(str(payload.min_weight_coverage))
        )
    except ConfigError as exc:
        raise _config_http(exc) from exc
    except RankingError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "PROJECT_NOT_FOUND" else 422,
            detail={"message": str(exc), "error_code": exc.code},
        ) from exc
    return RankingPreviewOut(
        project_id=result.project_id,
        current_config_version=result.current_config_version,
        sample_size=result.sample_size,
        units_scored=result.units_scored,
        units_skipped=result.units_skipped,
        results=[UnitPreviewDeltaOut(**vars(r)) for r in result.results],
        top_gainers=[UnitPreviewDeltaOut(**vars(r)) for r in result.top_gainers],
        top_losers=[UnitPreviewDeltaOut(**vars(r)) for r in result.top_losers],
        generated_at=result.generated_at,
    )
