"""AbsorptionCalculatorService + AreaService (SRS §5.2).

Đây là nửa sau của MVP 1: nạp xong dữ liệu thì phải xem được tốc độ hấp thụ trên
dashboard, nếu không thì việc nạp chẳng để làm gì.

`absorption_daily` là bảng TỔNG HỢP, tính lại từ `sales_records` chứ không phải
nguồn sự thật. Vì vậy `recompute()` xoá sạch rồi ghi lại theo phạm vi dự án:
nạp bổ sung một file cũ có thể làm đổi vận tốc của những ngày đã tính trước đó,
nên cập nhật tăng dần sẽ sai một cách âm thầm.

Quy ước tính (SRS §5.2 không định nghĩa, đây là lựa chọn của bản cài đặt này):

- `units_sold`  — tổng số căn bán trong ngày, từ `sales_records`.
- `velocity_7d` — trung bình cộng `units_sold` trong 7 ngày gần nhất tính cả ngày
  hiện tại; `velocity_30d` tương tự với 30 ngày. Trung bình trượt chứ không phải
  tổng, để con số đọc được là "mỗi ngày bán bao nhiêu căn".
- `is_observed`  — False cho ngày được ĐIỀN BÙ (không có giao dịch nào). Phải
  điền bù thì trung bình trượt mới đúng: bỏ trống ngày không bán sẽ khiến mẫu số
  co lại và vận tốc bị thổi lên.
- `data_quality_status` — 'warning' khi cửa sổ 30 ngày chưa đủ dữ liệu lịch sử
  (những ngày đầu chuỗi), 'ok' khi đã đủ. Người đọc biết con số đầu chuỗi chưa
  đáng tin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import absorption_daily, areas, inventory_snapshots, projects, sales_records
from src.services.calculators import CALCULATOR_LEGACY, DEFAULT_CALCULATOR

log = get_logger("src.services.absorption")

VELOCITY_WINDOWS = (7, 30)
LONG_WINDOW = 30


@dataclass(slots=True)
class AreaInventory:
    """Một phân khu kèm tồn kho mới nhất (SRS §5.2 — AreaService)."""

    area_id: str
    area_name: str
    unit_type: str
    bedrooms: int
    area_sqm: Decimal
    total_units: int
    units_remaining: int | None
    snapshot_date: date | None
    headline: str
    introduce: str
    cover_image_url: str | None
    # Phase D — None với phân khu di sản (tạo trước Phase D).
    external_id: str | None = None
    source_revision: int | None = None


@dataclass(slots=True)
class AbsorptionPoint:
    stat_date: date
    units_sold: int
    velocity_7d: Decimal
    velocity_30d: Decimal
    is_observed: bool
    data_quality_status: str


@dataclass(slots=True)
class AbsorptionSummary:
    units_remaining: int | None
    units_sold: int
    avg_velocity_30d: Decimal | None
    updated_at: datetime | None
    total_units: int | None = None
    velocity_7d: Decimal | None = None
    velocity_30d: Decimal | None = None


def _rolling_mean(daily: list[int], index: int, window: int) -> Decimal:
    """Trung bình `units_sold` của `window` ngày gần nhất, tính cả ngày `index`.

    Mẫu số là số ngày THỰC SỰ có trong cửa sổ, nên những ngày đầu chuỗi không bị
    chia cho 7/30 và tụt xuống gần 0 một cách vô lý.
    """
    start = max(0, index - window + 1)
    span = daily[start : index + 1]
    return (Decimal(sum(span)) / Decimal(len(span))).quantize(Decimal("0.0001"))


class AbsorptionCalculatorService:
    """Tính `absorption_daily` từ `sales_records` cho một dự án."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def recompute(self, project_id: uuid.UUID | str) -> int:
        """Tính lại toàn bộ chuỗi hấp thụ của dự án. Trả về số dòng đã ghi.

        Chỉ đụng tới lineage `legacy_aggregate`. Xem chú thích ở câu DELETE.
        """
        project_uuid = uuid.UUID(str(project_id))
        computed_at = datetime.now(UTC)
        # Một UUID cho MỘT lần tính; mọi dòng của lần này mang cùng giá trị. Nhờ
        # đó truy được dòng thuộc lần chạy nào, và một lần ghi dở dang lộ ra dưới
        # dạng hai computation_id lẫn lộn trong cùng lineage.
        computation_id = uuid.uuid4()

        async with self._session_factory() as session:
            async with session.begin():
                area_ids = list(
                    (await session.execute(sa.select(areas.c.id).where(areas.c.project_id == project_uuid))).scalars()
                )
                if not area_ids:
                    return 0

                sales = (
                    await session.execute(
                        sa.select(
                            sales_records.c.area_id,
                            sales_records.c.sold_date,
                            sa.func.sum(sales_records.c.units_sold).label("units_sold"),
                        )
                        .where(sales_records.c.area_id.in_(area_ids))
                        .group_by(sales_records.c.area_id, sales_records.c.sold_date)
                    )
                ).all()

                # Tính lại từ đầu: nạp thêm một file có ngày cũ sẽ làm đổi vận tốc
                # của các ngày đã ghi, cập nhật tăng dần sẽ để lại số sai.
                #
                # Phạm vi `calculator` là thứ THỰC SỰ ngăn hai bộ tính xoá của
                # nhau — khoá duy nhất mở rộng ở 0012 chỉ làm việc sống chung trở
                # nên khả thi. Thiếu điều kiện này thì mỗi lần nạp file Excel sẽ
                # âm thầm xoá sạch chuỗi do bộ tính miền sinh ra.
                await session.execute(
                    sa.delete(absorption_daily).where(
                        absorption_daily.c.area_id.in_(area_ids),
                        absorption_daily.c.calculator == CALCULATOR_LEGACY,
                    )
                )

                rows = self._build_rows(sales, computed_at, computation_id)
                if rows:
                    await session.execute(sa.insert(absorption_daily), rows)

        log.info("absorption.recomputed", project_id=str(project_uuid), rows=len(rows))
        return len(rows)

    def _build_rows(self, sales, computed_at: datetime, computation_id: uuid.UUID) -> list[dict]:
        """Dựng chuỗi ngày liên tục cho từng phân khu rồi tính vận tốc trượt."""
        by_area: dict[uuid.UUID, dict[date, int]] = {}
        for area_id, sold_date, units_sold in sales:
            by_area.setdefault(area_id, {})[sold_date] = int(units_sold)

        rows: list[dict] = []
        for area_id, sold_by_date in by_area.items():
            first, last = min(sold_by_date), max(sold_by_date)
            span = (last - first).days + 1
            dates = [first + timedelta(days=offset) for offset in range(span)]
            daily = [sold_by_date.get(day, 0) for day in dates]

            for index, day in enumerate(dates):
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "area_id": area_id,
                        "stat_date": day,
                        "units_sold": daily[index],
                        "velocity_7d": _rolling_mean(daily, index, VELOCITY_WINDOWS[0]),
                        "velocity_30d": _rolling_mean(daily, index, VELOCITY_WINDOWS[1]),
                        # Chưa đủ 30 ngày lịch sử thì vận tốc dài hạn còn nhiễu.
                        "data_quality_status": "ok" if index + 1 >= LONG_WINDOW else "warning",
                        "is_observed": day in sold_by_date,
                        "computed_at": computed_at,
                        # Nguồn gốc (0012). `units_reserved` để NULL: bộ tính này
                        # đọc `sales_records`, vốn chỉ có số căn ĐÃ BÁN theo ngày
                        # và không biết gì về căn đang giữ chỗ. Điền 0 sẽ là nói
                        # "không có căn nào giữ chỗ", khác hẳn "không tính được".
                        "calculator": CALCULATOR_LEGACY,
                        "units_reserved": None,
                        "computation_id": computation_id,
                    }
                )
        return rows


class AreaService:
    """Đọc phân khu kèm tồn kho mới nhất (SRS §5.2 — AreaService)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def list_areas(self, project_id: uuid.UUID | str) -> list[AreaInventory]:
        """Phân khu của dự án, join bản chốt tồn kho mới nhất của từng phân khu."""
        project_uuid = uuid.UUID(str(project_id))

        # DISTINCT ON là cách rẻ nhất của Postgres để lấy "bản ghi mới nhất mỗi
        # nhóm"; index ix_inventory_snapshots_area_id_snapshot_date phục vụ đúng
        # thứ tự này nên không phải sort thêm.
        latest = (
            sa.select(
                inventory_snapshots.c.area_id,
                inventory_snapshots.c.units_remaining,
                inventory_snapshots.c.snapshot_date,
            )
            .distinct(inventory_snapshots.c.area_id)
            .order_by(inventory_snapshots.c.area_id, inventory_snapshots.c.snapshot_date.desc())
            .subquery()
        )

        query = (
            sa.select(
                areas.c.id,
                areas.c.area_name,
                areas.c.unit_type,
                areas.c.bedrooms,
                areas.c.area_sqm,
                areas.c.total_units,
                areas.c.headline,
                areas.c.introduce,
                areas.c.cover_image_url,
                areas.c.external_id,
                areas.c.source_revision,
                latest.c.units_remaining,
                latest.c.snapshot_date,
            )
            .select_from(areas.outerjoin(latest, latest.c.area_id == areas.c.id))
            .where(areas.c.project_id == project_uuid)
            .order_by(areas.c.area_name, areas.c.unit_type)
        )

        async with self._session_factory() as session:
            rows = (await session.execute(query)).all()

        return [
            AreaInventory(
                area_id=str(row.id),
                area_name=row.area_name,
                unit_type=row.unit_type,
                bedrooms=row.bedrooms,
                area_sqm=row.area_sqm,
                total_units=row.total_units,
                units_remaining=row.units_remaining,
                snapshot_date=row.snapshot_date,
                headline=row.headline,
                introduce=row.introduce,
                cover_image_url=row.cover_image_url,
                external_id=row.external_id,
                source_revision=row.source_revision,
            )
            for row in rows
        ]

    async def _calculator_for_project(self, session: AsyncSession, project_id: uuid.UUID) -> str:
        """Lineage mà dự án này đang đọc. Không có dự án → mặc định.

        Giải MỘT LẦN cho mỗi request rồi truyền xuống, thay vì để mỗi truy vấn tự
        đặt mặc định: một mặc định nằm ở ba chỗ là ba chỗ để quên, và quên ở đây
        nghĩa là dashboard cộng gộp hai lineage rồi hiển thị số gấp đôi.
        """
        selected = await session.scalar(sa.select(projects.c.absorption_calculator).where(projects.c.id == project_id))
        return selected or DEFAULT_CALCULATOR

    async def _calculator_for_area(self, session: AsyncSession, area_id: uuid.UUID) -> str:
        """Lineage của dự án chứa phân khu này."""
        selected = await session.scalar(
            sa.select(projects.c.absorption_calculator)
            .select_from(areas.join(projects, areas.c.project_id == projects.c.id))
            .where(areas.c.id == area_id)
        )
        return selected or DEFAULT_CALCULATOR

    async def absorption_series(
        self,
        area_id: uuid.UUID | str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        granularity: str = "day",
    ) -> list[AbsorptionPoint]:
        """Chuỗi tốc độ hấp thụ của một phân khu.

        `granularity='week'` gộp theo tuần ISO: cộng `units_sold`, còn vận tốc lấy
        giá trị của ngày CUỐI tuần vì nó vốn đã là trung bình trượt — cộng dồn
        trung bình lại lần nữa sẽ ra một con số không có nghĩa.
        """
        area_uuid = uuid.UUID(str(area_id))

        async with self._session_factory() as session:
            # Đọc ĐÚNG MỘT lineage. Không lọc thì mỗi ngày sẽ có hai dòng và
            # chuỗi hiển thị ra gấp đôi số thật.
            calculator = await self._calculator_for_area(session, area_uuid)

            query = sa.select(absorption_daily).where(
                absorption_daily.c.area_id == area_uuid,
                absorption_daily.c.calculator == calculator,
            )
            if date_from is not None:
                query = query.where(absorption_daily.c.stat_date >= date_from)
            if date_to is not None:
                query = query.where(absorption_daily.c.stat_date <= date_to)

            rows = (await session.execute(query.order_by(absorption_daily.c.stat_date))).all()

        points = [
            AbsorptionPoint(
                stat_date=row.stat_date,
                units_sold=row.units_sold,
                velocity_7d=row.velocity_7d,
                velocity_30d=row.velocity_30d,
                is_observed=row.is_observed,
                data_quality_status=row.data_quality_status,
            )
            for row in rows
        ]
        return _group_by_week(points) if granularity == "week" else points

    async def summary(self, project_id: uuid.UUID | str, *, area_id: uuid.UUID | str | None = None) -> AbsorptionSummary:
        """Tổng hợp đúng phạm vi dự án hoặc phân khu cho dashboard."""
        project_uuid = uuid.UUID(str(project_id))
        area_uuid = uuid.UUID(str(area_id)) if area_id is not None else None

        async with self._session_factory() as session:
            area_query = sa.select(areas.c.id, areas.c.total_units).where(areas.c.project_id == project_uuid)
            if area_uuid is not None:
                area_query = area_query.where(areas.c.id == area_uuid)
            area_rows = (await session.execute(area_query)).all()
            area_ids = [row.id for row in area_rows]
            total_units = sum(int(row.total_units or 0) for row in area_rows)
            if not area_ids:
                return AbsorptionSummary(0, 0, None, None, total_units=0, velocity_7d=None, velocity_30d=None)

            units_sold = await session.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(sales_records.c.units_sold), 0)).where(
                    sales_records.c.area_id.in_(area_ids)
                )
            )
            latest = (
                sa.select(inventory_snapshots.c.area_id, inventory_snapshots.c.units_remaining)
                .distinct(inventory_snapshots.c.area_id)
                .where(inventory_snapshots.c.area_id.in_(area_ids))
                .order_by(inventory_snapshots.c.area_id, inventory_snapshots.c.snapshot_date.desc())
                .subquery()
            )
            units_remaining = await session.scalar(sa.select(sa.func.sum(latest.c.units_remaining)))
            # Vận tốc trung bình toàn dự án = trung bình velocity_30d của các bản
            # ghi MỚI NHẤT mỗi phân khu, không phải trung bình của cả lịch sử.
            # CÙNG lineage với `absorption_series`. Hai đường đọc lệch nhau thì
            # biểu đồ và thẻ số liệu trên cùng một màn hình sẽ nói hai điều khác
            # nhau, và không ai biết bên nào đúng.
            calculator = await self._calculator_for_project(session, project_uuid)

            newest = (
                sa.select(
                    absorption_daily.c.area_id,
                    absorption_daily.c.velocity_7d,
                    absorption_daily.c.velocity_30d,
                )
                .distinct(absorption_daily.c.area_id)
                .where(
                    absorption_daily.c.area_id.in_(area_ids),
                    absorption_daily.c.calculator == calculator,
                )
                .order_by(absorption_daily.c.area_id, absorption_daily.c.stat_date.desc())
                .subquery()
            )
            velocity_7d = await session.scalar(sa.select(sa.func.avg(newest.c.velocity_7d)))
            velocity_30d = await session.scalar(sa.select(sa.func.avg(newest.c.velocity_30d)))
            updated_at = await session.scalar(
                sa.select(sa.func.max(absorption_daily.c.computed_at)).where(
                    absorption_daily.c.area_id.in_(area_ids),
                    absorption_daily.c.calculator == calculator,
                )
            )

        return AbsorptionSummary(
            units_remaining=int(units_remaining) if units_remaining is not None else None,
            units_sold=int(units_sold or 0),
            avg_velocity_30d=Decimal(velocity_30d).quantize(Decimal("0.0001")) if velocity_30d is not None else None,
            updated_at=updated_at,
            total_units=total_units,
            velocity_7d=Decimal(velocity_7d).quantize(Decimal("0.0001")) if velocity_7d is not None else None,
            velocity_30d=Decimal(velocity_30d).quantize(Decimal("0.0001")) if velocity_30d is not None else None,
        )


def _group_by_week(points: list[AbsorptionPoint]) -> list[AbsorptionPoint]:
    """Gộp theo tuần ISO, mốc là ngày cuối cùng có trong tuần đó."""
    buckets: dict[tuple[int, int], list[AbsorptionPoint]] = {}
    for point in points:
        iso = point.stat_date.isocalendar()
        buckets.setdefault((iso.year, iso.week), []).append(point)

    weekly = []
    for _, group in sorted(buckets.items()):
        last = group[-1]
        weekly.append(
            AbsorptionPoint(
                stat_date=last.stat_date,
                units_sold=sum(p.units_sold for p in group),
                velocity_7d=last.velocity_7d,
                velocity_30d=last.velocity_30d,
                is_observed=any(p.is_observed for p in group),
                data_quality_status="warning" if any(p.data_quality_status == "warning" for p in group) else "ok",
            )
        )
    return weekly
