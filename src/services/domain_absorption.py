"""Bộ tính hấp thụ đọc từ `units` / `deals` (S3), và bộ so sánh song song.

Khác bộ tính cũ (`src/services/absorption.py`) ở NGUỒN chứ không ở công thức:
bên kia cộng `sales_records.units_sold` theo ngày, bên này đếm từng căn qua giao
dịch. Nhờ đó mới có được số căn đang giữ chỗ và tồn kho theo từng căn — hai thứ
mà dữ liệu tổng hợp không dựng lại được.

`compute()` trả kết quả trong bộ nhớ và không ghi gì; muốn ghi phải gọi `persist()`
một cách tường minh. Dashboard domain analytics đọc trực tiếp `units`/`deals`
qua `DomainSalesAnalyticsService`; `absorption_daily` chỉ còn là projection
phục vụ các đường tương thích và đối chiếu.

Quy tắc đếm:

* Chỉ giao dịch `sold` còn sống mới tính là ĐÃ BÁN, vào ngày `date(sold_at)`.
* Chỉ giao dịch `reserved` còn sống mới tính là ĐANG GIỮ CHỖ.
* `lost` (gồm cả `cancelled` đã ánh xạ) không tính gì cả — căn quay lại quỹ hàng.
* Căn `blocked` nằm NGOÀI quỹ hàng: không phải tồn kho có thể bán, nên không làm
  loãng tỷ lệ hấp thụ.
* Bản ghi có `deleted_at` bị loại khỏi mọi phép đếm.
* Ràng buộc `uq_deals_active_per_unit` bảo đảm một căn không thể vừa `reserved`
  vừa `sold`, nên không có đường nào đếm một căn hai lần.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import absorption_daily, areas, deals, units
from src.services.absorption import LONG_WINDOW, VELOCITY_WINDOWS, AreaService, _rolling_mean
from src.services.calculators import CALCULATOR_DOMAIN as _CALCULATOR_DOMAIN
from src.services.calculators import CALCULATOR_LEGACY as _CALCULATOR_LEGACY

log = get_logger("src.services.domain_absorption")

# Tên bộ tính sống ở `src/services/calculators.py` — một chỗ duy nhất, dùng
# chung với bộ tính cũ mà không bắt nó import module này.
CALCULATOR_LEGACY = _CALCULATOR_LEGACY
CALCULATOR_DOMAIN = _CALCULATOR_DOMAIN

# Trạng thái căn nằm ngoài quỹ hàng bán được.
OUT_OF_STOCK_STATUSES = ("blocked",)


@dataclass(frozen=True, slots=True)
class AreaInventoryCounts:
    """Ảnh chụp tồn kho HIỆN TẠI của một phân khu."""

    area_id: uuid.UUID
    area_name: str
    unit_type: str
    total_units: int
    units_sold: int
    units_reserved: int
    units_remaining: int
    units_blocked: int


@dataclass(frozen=True, slots=True)
class DomainAbsorptionPoint:
    """Một ngày trong chuỗi hấp thụ, tính từ giao dịch."""

    area_id: uuid.UUID
    stat_date: date
    units_sold: int
    units_remaining: int
    # Số căn đang giữ chỗ. GIỚI HẠN ĐÃ BIẾT: đây là con số HIỆN TẠI, lặp lại cho
    # mọi ngày trong chuỗi, vì `deals` không có nhật ký sự kiện để dựng lại trạng
    # thái giữ chỗ theo từng ngày (quyết định 3 — hoãn `unit_status_events`).
    # Cùng xấp xỉ mà `units_remaining` đang dùng, không phải một xấp xỉ mới.
    units_reserved: int
    velocity_7d: Decimal
    velocity_30d: Decimal
    is_observed: bool
    data_quality_status: str


@dataclass(slots=True)
class DomainAbsorptionResult:
    """Kết quả một lần tính, CHƯA ghi xuống đâu cả."""

    project_id: uuid.UUID
    calculator: str = CALCULATOR_DOMAIN
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    points: list[DomainAbsorptionPoint] = field(default_factory=list)
    inventory: list[AreaInventoryCounts] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)

    @property
    def units_sold(self) -> int:
        return sum(area.units_sold for area in self.inventory)

    @property
    def units_reserved(self) -> int:
        return sum(area.units_reserved for area in self.inventory)

    @property
    def units_remaining(self) -> int:
        return sum(area.units_remaining for area in self.inventory)


class DomainAbsorptionCalculatorService:
    """Tính hấp thụ từ `units` + `deals`. Không ghi gì trừ khi được yêu cầu."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def compute(
        self, project_id: uuid.UUID | str, *, area_ids: Sequence[uuid.UUID] | None = None
    ) -> DomainAbsorptionResult:
        """Tính toàn bộ chuỗi + tồn kho hiện tại của một dự án.

        Thuần đọc. Gọi bao nhiêu lần cũng cho cùng kết quả trên cùng dữ liệu —
        tính tất định là điều kiện để đem so với bộ tính cũ.

        `area_ids` thu hẹp phạm vi về đúng những phân khu vừa đổi. Một lô đồng bộ
        chạm một phân khu không có lý do gì phải tính lại cả dự án, và tính thừa
        thì `persist()` sẽ xoá-rồi-ghi lại nguyên vẹn những phân khu không đổi —
        vô hại nhưng lãng phí, và làm `computation_id` của chúng đổi vô cớ.

        None = toàn dự án. Danh sách rỗng cũng là toàn dự án: "không thu hẹp
        được" phải cho ra kết quả ĐẦY ĐỦ, không phải kết quả rỗng.
        """
        project_uuid = uuid.UUID(str(project_id))
        result = DomainAbsorptionResult(project_id=project_uuid)
        wanted = {uuid.UUID(str(value)) for value in area_ids} if area_ids else None

        async with self._session_factory() as session:
            area_query = (
                sa.select(areas.c.id, areas.c.area_name, areas.c.unit_type)
                .where(areas.c.project_id == project_uuid)
                .order_by(areas.c.area_name, areas.c.unit_type)
            )
            if wanted:
                area_query = area_query.where(areas.c.id.in_(sorted(wanted, key=str)))
            area_rows = ((await session.execute(area_query)).mappings()).all()
            if not area_rows:
                return result

            area_ids = [row["id"] for row in area_rows]
            live_units = sa.and_(units.c.area_id.in_(area_ids), units.c.deleted_at.is_(None))
            live_deals = deals.c.deleted_at.is_(None)

            # --- Tồn kho hiện tại theo phân khu ---
            stock = {
                row["area_id"]: row
                for row in (
                    (
                        await session.execute(
                            sa.select(
                                units.c.area_id,
                                sa.func.count().label("total"),
                                sa.func.count().filter(units.c.status.in_(OUT_OF_STOCK_STATUSES)).label("blocked"),
                            )
                            .where(live_units)
                            .group_by(units.c.area_id)
                        )
                    )
                    .mappings()
                    .all()
                )
            }

            # Đếm theo GIAO DỊCH đang giữ, không theo `units.status`: giao dịch là
            # sự kiện có mốc thời gian, còn `units.status` chỉ là ảnh chụp hiện tại.
            held = {
                (row["area_id"], row["status"]): row["n"]
                for row in (
                    (
                        await session.execute(
                            sa.select(units.c.area_id, deals.c.status, sa.func.count(sa.distinct(deals.c.unit_id)).label("n"))
                            .select_from(deals.join(units, deals.c.unit_id == units.c.id))
                            .where(live_units, live_deals, deals.c.status.in_(("reserved", "sold")))
                            .group_by(units.c.area_id, deals.c.status)
                        )
                    )
                    .mappings()
                    .all()
                )
            }

            for row in area_rows:
                area_id = row["id"]
                counts = stock.get(area_id, {"total": 0, "blocked": 0})
                total = int(counts["total"])
                blocked = int(counts["blocked"])
                sold = int(held.get((area_id, "sold"), 0))
                reserved = int(held.get((area_id, "reserved"), 0))
                sellable = total - blocked
                result.inventory.append(
                    AreaInventoryCounts(
                        area_id=area_id,
                        area_name=row["area_name"],
                        unit_type=row["unit_type"],
                        total_units=sellable,
                        units_sold=sold,
                        units_reserved=reserved,
                        units_remaining=max(sellable - sold - reserved, 0),
                        units_blocked=blocked,
                    )
                )
                if sellable - sold - reserved < 0:
                    # Nhiều giao dịch đang giữ hơn số căn bán được — dữ liệu nguồn
                    # mâu thuẫn. Báo ra, KHÔNG kẹp im lặng rồi coi như bình thường.
                    result.anomalies.append(
                        {
                            "code": "HELD_EXCEEDS_STOCK",
                            "area_id": str(area_id),
                            "sellable": sellable,
                            "sold": sold,
                            "reserved": reserved,
                        }
                    )

            # --- Chuỗi bán theo ngày ---
            effective_sold = DomainSalesAnalyticsService._effective_sold(date.today())
            sold_rows = (
                await session.execute(
                    sa.select(
                        units.c.area_id,
                        effective_sold.c.sale_date.label("stat_date"),
                        sa.func.count().label("units_sold"),
                    )
                    .select_from(effective_sold.join(units, effective_sold.c.unit_id == units.c.id))
                    .where(live_units, effective_sold.c.sale_date <= date.today())
                    .group_by(units.c.area_id, effective_sold.c.sale_date)
                )
            ).all()

            await self._detect_orphans(session, result)

        result.points = self._build_points(sold_rows, result.inventory)
        return result

    async def _detect_orphans(self, session: AsyncSession, result: DomainAbsorptionResult) -> None:
        """Giao dịch còn sống trỏ vào căn đã xoá — quan hệ không hợp lệ.

        Khoá ngoại vẫn thoả (dòng căn còn đó), nhưng về nghiệp vụ thì giao dịch
        này treo lơ lửng. Không đếm vào đâu cả, và phải hiện ra để còn sửa ở CRM.
        """
        rows = (
            (
                await session.execute(
                    sa.select(deals.c.external_deal_id, deals.c.status)
                    .select_from(deals.join(units, deals.c.unit_id == units.c.id))
                    .where(
                        deals.c.deleted_at.is_(None),
                        units.c.deleted_at.isnot(None),
                        deals.c.status.in_(("reserved", "sold")),
                    )
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            result.anomalies.append(
                {
                    "code": "DEAL_ON_DELETED_UNIT",
                    "external_deal_id": row["external_deal_id"],
                    "status": row["status"],
                }
            )

    def _build_points(self, sold_rows, inventory: list[AreaInventoryCounts]) -> list[DomainAbsorptionPoint]:
        """Dựng chuỗi ngày liên tục cho từng phân khu, kèm tồn kho cuối ngày.

        Cùng công thức vận tốc với bộ tính cũ (trung bình trượt 7/30 ngày, ngày
        không bán vẫn có mặt với 0) để hai bên so được với nhau.
        """
        sellable = {area.area_id: area.total_units for area in inventory}
        reserved_now = {area.area_id: area.units_reserved for area in inventory}

        by_area: dict[uuid.UUID, dict[date, int]] = {}
        for area_id, stat_date, count in sold_rows:
            by_area.setdefault(area_id, {})[stat_date] = int(count)

        points: list[DomainAbsorptionPoint] = []
        for area_id in sorted(by_area, key=str):  # thứ tự tất định
            sold_by_date = by_area[area_id]
            first, last = min(sold_by_date), max(sold_by_date)
            days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
            daily = [sold_by_date.get(day, 0) for day in days]

            cumulative = 0
            total = sellable.get(area_id, 0)
            held = reserved_now.get(area_id, 0)
            for index, day in enumerate(days):
                cumulative += daily[index]
                points.append(
                    DomainAbsorptionPoint(
                        area_id=area_id,
                        stat_date=day,
                        units_sold=daily[index],
                        # Tồn kho cuối ngày = quỹ bán được − luỹ kế đã bán − đang
                        # giữ chỗ HIỆN TẠI. Giữ chỗ không có nhật ký sự kiện nên
                        # không dựng lại được theo ngày; đây là xấp xỉ đã biết,
                        # ghi rõ ở pipeline_status.md.
                        units_remaining=max(total - cumulative - held, 0),
                        units_reserved=held,
                        velocity_7d=_rolling_mean(daily, index, VELOCITY_WINDOWS[0]),
                        velocity_30d=_rolling_mean(daily, index, VELOCITY_WINDOWS[1]),
                        is_observed=day in sold_by_date,
                        data_quality_status="ok" if index + 1 >= LONG_WINDOW else "warning",
                    )
                )
        return points

    async def persist(self, result: DomainAbsorptionResult, *, area_ids: Sequence[uuid.UUID] | None = None) -> int:
        """Ghi kết quả xuống `absorption_daily`, CHỈ trong lineage của bộ tính miền.

        Tách hẳn khỏi `compute()` để chạy song song không bao giờ vô tình đổi số
        mà dashboard đang đọc.

        **Đường gọi ở production**: `src/jobs/recompute_domain.py::run_domain_recompute`,
        được `SyncRunService` xếp hàng sau mỗi lô đồng bộ làm đổi bản sao, và được
        `scripts/requeue_missing_domain_recompute.py` / job kiểm định kỳ xếp lại
        khi phát hiện lineage lạc hậu. Nó GHI THẬT vào `absorption_daily`, nhưng
        chỉ trong lineage `domain_units_deals` — dòng `legacy_aggregate` mà
        dashboard đang đọc không bao giờ bị đụng tới.

        Idempotent: xoá-rồi-ghi trong MỘT transaction, phạm vi đúng lineage này.
        Chạy lại trên cùng đầu vào cho ra nội dung y hệt — chỉ `computation_id`
        và `computed_at` đổi, và đó chính là thứ giúp truy vết từng lần chạy.
        Chọn xoá-rồi-ghi thay vì upsert vì một ngày không còn dữ liệu thì phải
        BIẾN MẤT; upsert sẽ để nó nằm lại mãi.
        """
        # Phạm vi xoá lấy từ `area_ids` khi được truyền vào, KHÔNG chỉ từ points.
        # Một phân khu vừa bị tombstone hết căn sẽ không sinh point nào; nếu phạm
        # vi chỉ suy từ points thì dòng cũ của nó nằm lại vĩnh viễn và tồn kho
        # hiển thị mãi số của những căn đã biến mất.
        scope = (
            sorted({uuid.UUID(str(value)) for value in area_ids}, key=str)
            if area_ids
            else sorted({point.area_id for point in result.points}, key=str)
        )
        if not scope:
            return 0
        computation_id = uuid.uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                # Phạm vi lineage: không bao giờ đụng tới dòng của bộ tính cũ —
                # đó là dòng dashboard đang thực sự đọc.
                await session.execute(
                    sa.delete(absorption_daily).where(
                        absorption_daily.c.area_id.in_(scope),
                        absorption_daily.c.calculator == CALCULATOR_DOMAIN,
                    )
                )
                if not result.points:
                    # Phạm vi không còn dữ liệu: xoá là kết quả đúng và đủ.
                    log.info("absorption.domain.cleared", project_id=str(result.project_id), areas=len(scope))
                    return 0
                await session.execute(
                    sa.insert(absorption_daily),
                    [
                        {
                            "id": uuid.uuid4(),
                            "area_id": point.area_id,
                            "stat_date": point.stat_date,
                            "units_sold": point.units_sold,
                            "units_remaining": point.units_remaining,
                            "velocity_7d": point.velocity_7d,
                            "velocity_30d": point.velocity_30d,
                            "data_quality_status": point.data_quality_status,
                            "is_observed": point.is_observed,
                            "computed_at": result.computed_at,
                            # Nguồn gốc (0012). Bộ tính này ĐỌC ĐƯỢC số căn giữ
                            # chỗ từ `deals`, nên `units_reserved` có giá trị thật
                            # thay vì NULL như bộ tính cũ.
                            "calculator": CALCULATOR_DOMAIN,
                            "units_reserved": point.units_reserved,
                            "computation_id": computation_id,
                        }
                        for point in result.points
                    ],
                )
        log.info("absorption.domain.persisted", project_id=str(result.project_id), rows=len(result.points))
        return len(result.points)


@dataclass(slots=True)
class ComparisonReport:
    """Chênh lệch giữa bộ tính cũ và bộ tính mới cho cùng một dự án."""

    project_id: uuid.UUID
    legacy_units_sold: int = 0
    domain_units_sold: int = 0
    legacy_units_remaining: int | None = 0
    domain_units_remaining: int = 0
    domain_units_reserved: int = 0
    differences: list[dict] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        return not self.differences and not self.anomalies


class ParallelRunComparator:
    """Chạy cả hai bộ tính trên cùng một dự án rồi báo chênh lệch.

    KHÔNG ghi gì và KHÔNG đổi đường đọc của dashboard. Mục đích duy nhất là trả
    lời câu hỏi "cắt sang bộ tính mới thì số có đổi không, đổi ở đâu" trước khi ai
    đó ra quyết định cắt.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def compare(self, project_id: uuid.UUID | str) -> ComparisonReport:
        project_uuid = uuid.UUID(str(project_id))
        report = ComparisonReport(project_id=project_uuid)

        legacy = await AreaService(self._session_factory).summary(project_uuid)
        domain = await DomainAbsorptionCalculatorService(self._session_factory).compute(project_uuid)

        report.legacy_units_sold = legacy.units_sold
        report.domain_units_sold = domain.units_sold
        report.legacy_units_remaining = legacy.units_remaining
        report.domain_units_remaining = domain.units_remaining
        report.domain_units_reserved = domain.units_reserved
        report.anomalies = list(domain.anomalies)

        for metric, legacy_value, domain_value in (
            ("units_sold", legacy.units_sold, domain.units_sold),
            ("units_remaining", legacy.units_remaining, domain.units_remaining),
        ):
            if legacy_value is None and not domain_value:
                # Bộ tính cũ "không biết" (không có phân khu/bản chốt tồn kho
                # nào) VÀ bộ tính mới cũng không có gì để báo (0/rỗng) — cả hai
                # cùng nói "không có dữ liệu", không phải một chênh lệch.
                continue
            if legacy_value != domain_value:
                delta = (
                    domain_value - legacy_value
                    if isinstance(legacy_value, int) and isinstance(domain_value, int)
                    else None
                )
                report.differences.append(
                    {
                        "metric": metric,
                        "legacy": legacy_value,
                        "domain": domain_value,
                        "delta": delta,
                    }
                )

        # Bộ tính cũ không có khái niệm giữ chỗ. Chênh ở đây không phải "sai" mà
        # là năng lực mới — báo riêng để đừng ai đọc nhầm thành lỗi số liệu.
        if domain.units_reserved:
            report.differences.append(
                {
                    "metric": "units_reserved",
                    "legacy": None,
                    "domain": domain.units_reserved,
                    "delta": None,
                    "note": "bộ tính cũ không tính được số căn đang giữ chỗ",
                }
            )

        log.info(
            "absorption.parallel_run",
            project_id=str(project_uuid),
            matches=report.matches,
            differences=len(report.differences),
            anomalies=len(report.anomalies),
        )
        return report


@dataclass(frozen=True, slots=True)
class DomainAnalyticsSummary:
    """KPI và metadata được tính trực tiếp từ bản sao `units`/`deals`."""

    total_units: int
    units_sold: int
    units_remaining: int
    units_reserved: int
    sell_through: Decimal | None
    velocity_7d: Decimal | None
    velocity_30d: Decimal | None
    estimated_weeks_to_sell_out: Decimal | None
    updated_at: datetime | None
    earliest_sale_date: date | None
    latest_sale_date: date | None
    available_years: list[int]
    data_status: str
    message: str | None


@dataclass(frozen=True, slots=True)
class DomainAnalyticsPoint:
    stat_date: date
    period_end: date
    granularity: str
    units_sold: int
    cumulative_sold: int
    sell_through: Decimal | None
    velocity_7d: Decimal | None
    velocity_30d: Decimal | None
    is_observed: bool
    data_quality_status: str


@dataclass(frozen=True, slots=True)
class DomainAnalyticsTrend:
    area_id: uuid.UUID | None
    granularity: str
    points: list[DomainAnalyticsPoint]
    earliest_sale_date: date | None
    latest_sale_date: date | None
    available_years: list[int]
    data_status: str
    message: str | None


class DomainSalesAnalyticsService:
    """Read-only analytics over the canonical Project → Area → Unit → Deal graph.

    The source tables are a CRM mirror.  A live ``sold`` deal is the effective
    sale for a unit; old/lost rows remain audit history but cannot create a
    second sale.  The query deliberately ranks live sold rows per unit so a
    malformed historical mirror cannot double-count a unit in KPI or trend data.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    @staticmethod
    def _effective_sold(as_of: date):
        sale_date = sa.cast(deals.c.sold_at, sa.Date).label("sale_date")
        rank = sa.func.row_number().over(
            partition_by=deals.c.unit_id,
            order_by=[
                deals.c.sold_at.desc(),
                deals.c.source_revision.desc().nullslast(),
                deals.c.source_updated_at.desc().nullslast(),
                deals.c.updated_at.desc(),
                deals.c.id.desc(),
            ],
        ).label("deal_rank")
        ranked = (
            sa.select(deals.c.unit_id, deals.c.sold_at, sale_date, rank)
            .where(
                deals.c.deleted_at.is_(None),
                deals.c.status == "sold",
                deals.c.sold_at.isnot(None),
                sa.cast(deals.c.sold_at, sa.Date) <= as_of,
            )
            .cte("ranked_domain_sold")
        )
        return sa.select(
            ranked.c.unit_id,
            ranked.c.sold_at,
            ranked.c.sale_date,
        ).where(ranked.c.deal_rank == 1).cte("effective_domain_sold")

    @staticmethod
    def _scope_join(project_id: uuid.UUID, area_id: uuid.UUID | None, unit_type: str | None):
        join = units.join(areas, units.c.area_id == areas.c.id)
        conditions = [
            areas.c.project_id == project_id,
            units.c.deleted_at.is_(None),
            ~units.c.status.in_(OUT_OF_STOCK_STATUSES),
        ]
        if area_id is not None:
            conditions.append(units.c.area_id == area_id)
        if unit_type is not None:
            conditions.append(areas.c.unit_type == unit_type)
        return join, conditions

    async def summary(
        self,
        project_id: uuid.UUID | str,
        *,
        area_id: uuid.UUID | str | None = None,
        unit_type: str | None = None,
        as_of: date | None = None,
    ) -> DomainAnalyticsSummary:
        project_uuid = uuid.UUID(str(project_id))
        area_uuid = uuid.UUID(str(area_id)) if area_id is not None else None
        today = as_of or date.today()
        effective = self._effective_sold(today)
        scope_join, scope = self._scope_join(project_uuid, area_uuid, unit_type)
        sold_join = effective.join(units, effective.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id)
        sold_scope = [*scope, effective.c.unit_id == units.c.id]

        async with self._session_factory() as session:
            total_units = int(await session.scalar(sa.select(sa.func.count()).select_from(scope_join).where(*scope)) or 0)
            units_sold = int(
                await session.scalar(sa.select(sa.func.count()).select_from(sold_join).where(*sold_scope)) or 0
            )
            units_reserved = int(
                await session.scalar(
                    sa.select(sa.func.count(sa.distinct(deals.c.unit_id)))
                    .select_from(deals.join(scope_join, deals.c.unit_id == units.c.id))
                    .where(*scope, deals.c.deleted_at.is_(None), deals.c.status == "reserved")
                )
                or 0
            )
            earliest, latest = (
                await session.execute(
                    sa.select(sa.func.min(effective.c.sale_date), sa.func.max(effective.c.sale_date))
                    .select_from(sold_join)
                    .where(*sold_scope)
                )
            ).one()
            year_rows = await session.execute(
                sa.select(sa.extract("year", effective.c.sale_date).label("year"))
                .select_from(sold_join)
                .where(*sold_scope)
                .distinct()
                .order_by(sa.extract("year", effective.c.sale_date))
            )
            available_years = [int(row.year) for row in year_rows]
            recent = (
                await session.execute(
                    sa.select(
                        sa.func.count().filter(effective.c.sale_date >= today - timedelta(days=6)).label("seven"),
                        sa.func.count().filter(effective.c.sale_date >= today - timedelta(days=29)).label("thirty"),
                    )
                    .select_from(sold_join)
                    .where(*sold_scope)
                )
            ).one()
            updated_at = await session.scalar(
                sa.select(sa.func.max(units.c.updated_at)).select_from(scope_join).where(*scope)
            )

        remaining = max(total_units - units_sold, 0)
        sell_through = _percent(units_sold, total_units)
        velocity_7d = Decimal(int(recent.seven or 0)) if units_sold else None
        velocity_30d_raw = (Decimal(int(recent.thirty or 0)) * Decimal(7) / Decimal(30)) if units_sold else None
        # `estimated` dùng giá trị CHƯA làm tròn — làm tròn velocity_30d trước
        # khi chia sẽ khuếch đại sai số vào số tuần ước tính. Trường trả về
        # `velocity_30d` làm tròn RIÊNG, chỉ để hiển thị.
        estimated = _weeks_to_sell_out(remaining, velocity_30d_raw)
        velocity_30d = velocity_30d_raw.quantize(Decimal("0.0001")) if velocity_30d_raw is not None else None
        if total_units == 0:
            data_status, message = "no_units", "Phạm vi chưa có căn hộ."
        elif units_sold == 0:
            data_status, message = "no_data", "Phân khu này chưa có dữ liệu giao dịch."
        else:
            data_status, message = "ready", None
        return DomainAnalyticsSummary(
            total_units=total_units,
            units_sold=units_sold,
            units_remaining=remaining,
            units_reserved=units_reserved,
            sell_through=sell_through,
            velocity_7d=velocity_7d,
            velocity_30d=velocity_30d,
            estimated_weeks_to_sell_out=estimated,
            updated_at=updated_at,
            earliest_sale_date=earliest,
            latest_sale_date=latest,
            available_years=available_years,
            data_status=data_status,
            message=message,
        )

    async def trend(
        self,
        project_id: uuid.UUID | str,
        *,
        area_id: uuid.UUID | str | None = None,
        unit_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        year: int | None = None,
        granularity: str | None = None,
        as_of: date | None = None,
    ) -> DomainAnalyticsTrend:
        project_uuid = uuid.UUID(str(project_id))
        area_uuid = uuid.UUID(str(area_id)) if area_id is not None else None
        today = as_of or date.today()
        summary = await self.summary(project_uuid, area_id=area_uuid, unit_type=unit_type, as_of=today)
        start, end = _resolve_window(
            summary.earliest_sale_date,
            today,
            date_from=date_from,
            date_to=date_to,
            year=year,
        )
        chosen = granularity or _default_granularity(start, end)
        if chosen not in {"day", "week", "month"}:
            raise ValueError("granularity must be day, week, or month")
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValueError("from must be before or equal to to")

        effective = self._effective_sold(today)
        scope_join, scope = self._scope_join(project_uuid, area_uuid, unit_type)
        sold_join = effective.join(units, effective.c.unit_id == units.c.id).join(areas, units.c.area_id == areas.c.id)
        sold_scope = [*scope, effective.c.unit_id == units.c.id]
        period_expr = _period_expression(effective, chosen)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    sa.select(period_expr.label("period_start"), sa.func.count().label("units_sold"))
                    .select_from(sold_join)
                    .where(*sold_scope, effective.c.sale_date >= start, effective.c.sale_date <= end)
                    .group_by(period_expr)
                    .order_by(period_expr)
                )
            ).all()
            baseline = int(
                await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(sold_join)
                    .where(*sold_scope, effective.c.sale_date < start)
                )
                or 0
            )
            daily_counts: dict[date, int] = {}
            if chosen == "day":
                moving_start = max(summary.earliest_sale_date or start, start - timedelta(days=29))
                daily_rows = (
                    await session.execute(
                        sa.select(effective.c.sale_date, sa.func.count().label("units_sold"))
                        .select_from(sold_join)
                        .where(*sold_scope, effective.c.sale_date >= moving_start, effective.c.sale_date <= end)
                        .group_by(effective.c.sale_date)
                    )
                ).all()
                daily_counts = {row.sale_date: int(row.units_sold) for row in daily_rows}

        counts = {row.period_start: int(row.units_sold) for row in rows}
        if not counts:
            message = (
                "Phân khu này chưa có dữ liệu giao dịch."
                if summary.latest_sale_date is None
                else "Không có dữ liệu giao dịch trong khoảng thời gian đã chọn."
            )
            return DomainAnalyticsTrend(
                area_id=area_uuid,
                granularity=chosen,
                points=[],
                earliest_sale_date=summary.earliest_sale_date,
                latest_sale_date=summary.latest_sale_date,
                available_years=summary.available_years,
                data_status="no_units" if summary.total_units == 0 else "no_data",
                message=message,
            )

        periods = _periods(start, end, chosen)
        cumulative = baseline
        points: list[DomainAnalyticsPoint] = []
        for period_start, period_end in periods:
            sold = counts.get(period_start, 0)
            cumulative += sold
            if chosen == "day":
                v7 = _moving_average(daily_counts, period_start, 7, summary.earliest_sale_date)
                v30 = _moving_average(daily_counts, period_start, 30, summary.earliest_sale_date)
            else:
                v7 = v30 = None
            points.append(
                DomainAnalyticsPoint(
                    stat_date=period_start,
                    period_end=period_end,
                    granularity=chosen,
                    units_sold=sold,
                    cumulative_sold=cumulative,
                    sell_through=_percent(cumulative, summary.total_units),
                    velocity_7d=v7,
                    velocity_30d=v30,
                    is_observed=sold > 0,
                    data_quality_status="ok" if summary.earliest_sale_date and (period_start - summary.earliest_sale_date).days >= 29 else "warning",
                )
            )
        return DomainAnalyticsTrend(
            area_id=area_uuid,
            granularity=chosen,
            points=points,
            earliest_sale_date=summary.earliest_sale_date,
            latest_sale_date=summary.latest_sale_date,
            available_years=summary.available_years,
            data_status="ready",
            message=None,
        )


def _percent(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator) * Decimal(100)).quantize(Decimal("0.0001"))


def _weeks_to_sell_out(remaining: int, velocity_30d: Decimal | None) -> Decimal | None:
    if remaining <= 0:
        return Decimal(0)
    if velocity_30d is None or velocity_30d <= 0:
        return None
    return (Decimal(remaining) / velocity_30d).quantize(Decimal("0.0001"))


def _resolve_window(
    earliest: date | None,
    today: date,
    *,
    date_from: date | None,
    date_to: date | None,
    year: int | None,
) -> tuple[date, date]:
    if year is not None and (date_from is not None or date_to is not None):
        raise ValueError("year cannot be combined with from/to")
    if year is not None:
        return date(year, 1, 1), min(date(year, 12, 31), today)
    if (date_from is None) != (date_to is None):
        raise ValueError("from and to must be provided together")
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise ValueError("from must be before or equal to to")
        return date_from, date_to
    return earliest or today, today


def _default_granularity(date_from: date, date_to: date) -> str:
    return "month" if (date_to - date_from).days > 90 else "day"


def _period_expression(effective, granularity: str):
    if granularity == "day":
        return effective.c.sale_date
    if granularity == "week":
        return sa.cast(sa.func.date_trunc("week", effective.c.sold_at), sa.Date)
    return sa.cast(sa.func.date_trunc("month", effective.c.sold_at), sa.Date)


def _periods(date_from: date, date_to: date, granularity: str) -> list[tuple[date, date]]:
    if granularity == "day":
        return [(date_from + timedelta(days=i), date_from + timedelta(days=i)) for i in range((date_to - date_from).days + 1)]
    if granularity == "week":
        cursor = date_from - timedelta(days=date_from.weekday())
        last = date_to - timedelta(days=date_to.weekday())
        step = timedelta(days=7)
    else:
        cursor = date(date_from.year, date_from.month, 1)
        last = date(date_to.year, date_to.month, 1)
        step = None
    result = []
    while cursor <= last:
        if granularity == "week":
            period_end = cursor + timedelta(days=6)
        else:
            next_month = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
            period_end = next_month - timedelta(days=1)
        result.append((cursor, period_end))
        cursor = cursor + step if step else next_month
    return result


def _moving_average(counts: dict[date, int], day: date, window: int, first_available: date | None) -> Decimal:
    start = max(first_available or day, day - timedelta(days=window - 1))
    values = [counts.get(start + timedelta(days=i), 0) for i in range((day - start).days + 1)]
    return (Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.0001"))
