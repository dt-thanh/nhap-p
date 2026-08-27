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

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from src.db import get_session_factory
from src.models.schemas import (
    RankedUnitOut,
    RankingConfigDraftIn,
    RankingConfigOut,
    RankingConfigPublishIn,
    RankingConfigPublishOut,
    RankingContributionOut,
    RankingOut,
    RankingRunOut,
    SurveyFeatureBatchIn,
    SurveyFeatureBatchOut,
)
from src.models.tables import areas as areas_table
from src.models.tables import projects, ranking_configs, ranking_runs, ranking_scores, units
from src.ranking.bands import DISCLAIMER, as_percent, band_for
from src.ranking.service import RankingError, run_ranking
from src.services.dashboard_auth import DashboardPrincipal, require_project_in_scope, require_role
from src.services.ranking_config import ConfigError, create_draft, list_configs, publish, rollback_to
from src.services.ranking_trigger import trigger_ranking, trigger_ranking_all_projects
from src.services.survey_features import SurveyError, parse_items, upsert_survey_features

router = APIRouter(tags=["ranking"])
require_viewer = require_role("business_viewer")
require_operator = require_role("pipeline_operator")
require_admin = require_role("admin")
MAX_UNITS_PER_PAGE = 200
BANDS = ("high", "medium", "low")


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


async def _resolve_area(external_area_id: str | None) -> uuid.UUID | None:
    if external_area_id is None:
        return None
    async with get_session_factory()() as session:
        found = await session.scalar(
            sa.select(areas_table.c.id).where(areas_table.c.external_id == external_area_id)
        )
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


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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

    area_uuid = await _resolve_area(external_area_id)

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
                units.c.unit_code,
                units.c.unit_type,
                units.c.status.label("unit_status"),
                areas_table.c.area_name,
            )
            .select_from(
                ranking_scores.join(units, ranking_scores.c.unit_id == units.c.id).join(
                    areas_table, ranking_scores.c.area_id == areas_table.c.id
                )
            )
            .where(ranking_scores.c.project_id == project_uuid)
            .order_by(ranking_scores.c.rank_in_project)
        )
        if area_uuid is not None:
            query = query.where(ranking_scores.c.area_id == area_uuid)
        if unit_status is not None:
            query = query.where(units.c.status == unit_status)

        rows = list((await session.execute(query)).mappings().all())

        run_meta = score_meta or completed_run_meta

    # Mức được tính MỘT lần ở đây rồi dùng lại cho cả bộ lọc lẫn phần đếm, để
    # con số trên chip lọc và số dòng thực tế không bao giờ lệch nhau.
    banded = [(row, band_for(row["score"])) for row in rows]
    band_counts = {name: sum(1 for _, b in banded if b == name) for name in BANDS}

    matched = [(row, b) for row, b in banded if band is None or b == band]
    page = matched[offset : offset + limit]

    state = "ready"
    reason = None
    if run_meta is None:
        state = "not_run"
        reason = "RANKING_NOT_RUN"
    elif run_meta["units_processed"] == 0:
        state = "insufficient_data"
        reason = "NO_LIVE_UNITS"
    elif run_meta["units_ranked"] == 0:
        state = "insufficient_data"
        reason = "NO_UNITS_MET_COVERAGE"

    return RankingOut(
        project_id=str(project_uuid),
        external_project_id=project_external_id,
        ranking_run_id=str(run_meta["ranking_run_id"]) if run_meta else None,
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
                weight_coverage=str(row["weight_coverage"]),
                contributions=_contributions(row["contributions"]),
            )
            for row, row_band in page
        ],
        total=len(matched),
        limit=limit,
        offset=offset,
        disclaimer=DISCLAIMER,
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
    )


def _config_http(exc: ConfigError) -> HTTPException:
    status = 404 if exc.code == "CONFIG_NOT_FOUND" else 409 if exc.code == "ALREADY_PUBLISHED" else 422
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
        )
    except ConfigError as exc:
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
