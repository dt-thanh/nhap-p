"""Ghi lại lịch sử so sánh hai bộ tính — chạy song song mà không cắt sang.

`ParallelRunComparator` (Phase 6) đã trả lời được "hôm nay hai bộ tính có khớp
không". Nó **không** được sửa ở đây: mọi phép tính vẫn của nó, module này chỉ
thêm phần LƯU LẠI.

Vì câu hỏi thật không phải "hôm nay có khớp không" mà là **"có khớp ổn định
không"**, và một xu hướng thì không đọc được từ một lần đo. Không lưu thì so sánh
của hôm qua đã mất.

## Chỉ THÊM, không bao giờ SỬA

Không hàm nào trong module này phát ra `UPDATE` hay `DELETE`. Lịch sử là bằng
chứng; một lần đo bị ghi đè là một lần đo biến mất, và thứ biến mất bao giờ cũng
là thứ khó chịu nhất — lần khớp hụt mà ai đó vừa "chạy lại cho chắc".

## Phân biệt "bằng không" với "không có gì"

Một dự án chưa có `units`/`deals` khiến bộ tính miền ra `units_sold = 0`. Nếu bên
cũ cũng 0 thì hai bên "khớp" — một cái khớp RỖNG TUẾCH. Mười bốn ngày như thế
trông y hệt mười bốn ngày chạy song song thành công, và đó đúng là thứ sẽ được
dùng để quyết định cắt sang.

Nên `*_has_data` được xác định bằng hai câu đếm ĐỘC LẬP với bộ so sánh (đếm thẳng
dữ liệu nguồn), và khi `false` thì các cột chỉ số tương ứng ghi NULL chứ không ghi
0. Cổng chạy song song sau này đọc view `calculator_comparisons_gate`, nơi những
dòng đó đã bị loại sẵn ở tầng database.

## So sánh là TUẦN TỰ, không phải một ảnh chụp nhất quán

`compare()` gọi bộ tính cũ rồi gọi bộ tính miền, ở hai truy vấn khác nhau, không
nằm trong một transaction `REPEATABLE READ`. Nếu có một lô đồng bộ commit đúng
giữa hai lời gọi, hai bên sẽ đọc hai trạng thái khác nhau và sinh ra một chênh
lệch KHÔNG CÓ THẬT.

Ở quy mô hiện tại cửa sổ đó là vài mili giây và không ai ghi vào lúc 03:30 sáng,
nên đây là đánh đổi chấp nhận được — nhưng nó có thật, và nó là lời giải thích
đầu tiên cần loại trừ khi một chênh lệch lẻ loi xuất hiện rồi biến mất ở lần đo
sau. Cách sửa (nếu cần) là bọc `compare()` trong một transaction
`REPEATABLE READ`; chưa làm vì chưa có bằng chứng cần.

## Dữ liệu tổng hợp không phải bằng chứng cắt sang

Mọi dòng sinh ra ở Phase 8 đều bắt nguồn từ fixture tổng hợp. `matches = true`
đọc tách khỏi ngữ cảnh KHÔNG chứng minh gì về Mini CRM — Mini CRM chưa tồn tại.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import (
    areas,
    calculator_comparisons,
    inventory_snapshots,
    projects,
    sales_records,
    units,
)
from src.services.comparison_rules import ComparisonVerdict, classify
from src.services.domain_absorption import ParallelRunComparator

log = get_logger("src.services.parallel_run")

TRIGGER_SCHEDULE = "schedule"
TRIGGER_MANUAL = "manual"
SUPPORTED_TRIGGERS = frozenset({TRIGGER_SCHEDULE, TRIGGER_MANUAL})

# View chỉ chứa dòng `domain_has_data = true`. Cổng chạy song song ở 8G PHẢI đọc
# view này, không đọc thẳng bảng — xem migration 0013.
GATE_VIEW = "calculator_comparisons_gate"


class UnknownProjectError(Exception):
    """Không có dự án nào để so — bịa ra một dòng so sánh là bịa ra dữ liệu."""


@dataclass(frozen=True, slots=True)
class CaptureResult:
    comparison_id: uuid.UUID
    project_id: uuid.UUID
    compared_at: datetime
    matches: bool
    legacy_has_data: bool
    domain_has_data: bool
    difference_count: int
    anomaly_count: int


class ParallelRunCaptureService:
    """Chạy `ParallelRunComparator` rồi ghi kết quả xuống `calculator_comparisons`.

    KHÔNG ghi `absorption_daily`. Bộ so sánh dùng `compute()` (tính trong bộ nhớ),
    không bao giờ dùng `persist()` — đó là toàn bộ lý do chạy song song không đụng
    tới lineage nào.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def capture(self, project_id: uuid.UUID | str, *, trigger: str = TRIGGER_MANUAL) -> CaptureResult:
        if trigger not in SUPPORTED_TRIGGERS:
            raise ValueError(f"trigger phải là một trong: {', '.join(sorted(SUPPORTED_TRIGGERS))}")

        project_uuid = uuid.UUID(str(project_id))
        async with self._session_factory() as session:
            if await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_uuid)) is None:
                raise UnknownProjectError(f"Dự án '{project_uuid}' không tồn tại")
            legacy_has_data = await self._legacy_has_data(session, project_uuid)
            domain_has_data = await self._domain_has_data(session, project_uuid)

        report = await ParallelRunComparator(self._session_factory).compare(project_uuid)

        now = datetime.now(UTC)
        comparison_id = uuid.uuid4()
        values = {
            "id": comparison_id,
            "project_id": project_uuid,
            "compared_at": now,
            "trigger": trigger,
            # NULL khi không có gì để tính. Ghi 0 ở đây sẽ khiến một dự án rỗng
            # trông như một dự án đã được đo và cho ra kết quả bằng không.
            "legacy_units_sold": report.legacy_units_sold if legacy_has_data else None,
            "legacy_units_remaining": report.legacy_units_remaining if legacy_has_data else None,
            "domain_units_sold": report.domain_units_sold if domain_has_data else None,
            "domain_units_remaining": report.domain_units_remaining if domain_has_data else None,
            "domain_units_reserved": report.domain_units_reserved if domain_has_data else None,
            "legacy_has_data": legacy_has_data,
            "domain_has_data": domain_has_data,
            "matches": report.matches,
            "difference_count": len(report.differences),
            "anomaly_count": len(report.anomalies),
            "differences": report.differences,
            "anomalies": report.anomalies,
            "created_at": now,
        }

        async with self._session_factory() as session:
            async with session.begin():
                # INSERT, luôn luôn. Không upsert, không "ghi đè lần đo hôm nay":
                # lịch sử là bằng chứng.
                await session.execute(sa.insert(calculator_comparisons).values(**values))

        log.info(
            "parallel_run.captured",
            comparison_id=str(comparison_id),
            project_id=str(project_uuid),
            trigger=trigger,
            matches=report.matches,
            legacy_has_data=legacy_has_data,
            domain_has_data=domain_has_data,
            differences=len(report.differences),
            anomalies=len(report.anomalies),
        )
        return CaptureResult(
            comparison_id=comparison_id,
            project_id=project_uuid,
            compared_at=now,
            matches=report.matches,
            legacy_has_data=legacy_has_data,
            domain_has_data=domain_has_data,
            difference_count=len(report.differences),
            anomaly_count=len(report.anomalies),
        )

    async def capture_all(self, *, trigger: str = TRIGGER_SCHEDULE) -> list[CaptureResult]:
        """So mọi dự án. Một dự án hỏng KHÔNG kéo theo phần còn lại.

        Lần chạy theo lịch phải cho ra kết quả của những dự án còn lại kể cả khi
        một dự án có dữ liệu vỡ — dừng cả lượt vì một dự án là mất luôn quan sát
        của tất cả.
        """
        async with self._session_factory() as session:
            project_ids = list((await session.execute(sa.select(projects.c.id).order_by(projects.c.id))).scalars())

        captured: list[CaptureResult] = []
        for project_id in project_ids:
            try:
                captured.append(await self.capture(project_id, trigger=trigger))
            except Exception as exc:
                log.error(
                    "parallel_run.capture_failed",
                    project_id=str(project_id),
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )
        return captured

    async def verdicts(
        self, project_id: uuid.UUID | str, *, limit: int = 50, gate_only: bool = False
    ) -> list[ComparisonVerdict]:
        """Lịch sử đã PHÂN LOẠI theo bộ quy tắc hiện hành (Phase 8E).

        Phân loại được tính lại mỗi lần đọc chứ không lưu xuống bảng: bộ quy tắc
        còn được siết trước khi cắt sang, và nhãn đóng băng theo luật cũ sẽ khiến
        lịch sử cũ đọc theo một luật còn cổng cắt sang đọc theo luật khác.
        """
        return [classify(row) for row in await self.history(project_id, limit=limit, gate_only=gate_only)]

    async def fetch(self, comparison_id: uuid.UUID) -> dict | None:
        """Một dòng theo id.

        Phía API dùng cái này thay vì "lấy dòng mới nhất": hai lần ghi đồng thời
        thì "mới nhất" có thể là dòng của người khác, và người gọi sẽ nhận về kết
        quả không phải của mình.
        """
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        sa.select(calculator_comparisons).where(calculator_comparisons.c.id == comparison_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    async def history(self, project_id: uuid.UUID | str, *, limit: int = 50, gate_only: bool = False) -> list[dict]:
        """Lịch sử so sánh, mới nhất trước.

        `gate_only=True` đọc view `calculator_comparisons_gate` — dòng
        `domain_has_data = false` đã bị loại ở tầng database, nên cổng chạy song
        song không thể vô tình đếm một cái "khớp" rỗng tuếch vào 14 ngày của nó.
        """
        project_uuid = uuid.UUID(str(project_id))
        source = sa.text(
            f"SELECT * FROM {GATE_VIEW if gate_only else 'calculator_comparisons'} "
            "WHERE project_id = :p ORDER BY compared_at DESC LIMIT :n"
        )
        async with self._session_factory() as session:
            rows = (await session.execute(source, {"p": project_uuid, "n": limit})).mappings().all()
        return [dict(row) for row in rows]

    # --- Có dữ liệu hay không ------------------------------------------------

    async def _legacy_has_data(self, session: AsyncSession, project_id: uuid.UUID) -> bool:
        """Bộ tính CŨ đọc `sales_records` + `inventory_snapshots`. Không có dòng
        nào ở cả hai nghĩa là nó không có gì để tính."""
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == project_id)
        for table in (sales_records, inventory_snapshots):
            found = await session.scalar(sa.select(table.c.id).where(table.c.area_id.in_(area_ids)).limit(1))
            if found is not None:
                return True
        return False

    async def _domain_has_data(self, session: AsyncSession, project_id: uuid.UUID) -> bool:
        """Bộ tính MIỀN đọc `units`/`deals`. Không căn nào (kể cả căn đã xoá mềm)
        thì nó không có gì để tính.

        Đếm ĐỘC LẬP với bộ so sánh, chứ không suy ra từ kết quả trả về: suy ra từ
        "tổng bằng 0" chính là cái nhầm lẫn mà cột này sinh ra để tránh.
        """
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == project_id)
        found = await session.scalar(sa.select(units.c.id).where(units.c.area_id.in_(area_ids)).limit(1))
        return found is not None
