"""ImportService — nạp file staging vào PostgreSQL trong MỘT transaction.

Đây là mảnh còn thiếu giữa parser và database. Parser dừng ở chỗ tạo ra bản ghi
sạch; service này nhận file staging rồi làm nốt phần mà parser cố tình không làm
vì phải chạm DB:

1. Phân giải `area_name` (+ `unit_type`) → `areas.id`, vì bảng đích chỉ có
   `area_id` chứ không có tên phân khu.
2. Chèn dòng hợp lệ vào bảng đích theo lô.
3. Ghi lỗi theo dòng vào `upload_errors` — cả lỗi của parser lẫn lỗi phát sinh
   lúc nạp (không tra được phân khu, dòng trùng).
4. Cập nhật `rows_ok` / `rows_failed` / `status` của `upload_files`.

Bản ghi `upload_files` do TẦNG API tạo trước khi enqueue (SRS §5.2), service này
chỉ cập nhật. Nhờ vậy client có `file_id` để poll ngay từ lúc upload, không phải
chờ worker chạy xong.

**Nạp lặp lại được (0005).** Chống trùng nằm ở KHOÁ NGHIỆP VỤ của bảng đích, không
nằm ở checksum của file: `INSERT ... ON CONFLICT` nên nạp lại cùng một lô là thao
tác không-làm-gì, và hai lô có phần dữ liệu chồng lấn chỉ bỏ qua phần chồng lấn
thay vì làm vỡ cả lô. Bản ghi chỉ bị ghi đè khi `source_updated_at` của bản đến
thực sự mới hơn — xem `_upsert_stmt`.

TOÀN BỘ nằm trong một transaction. Vỡ ở bước nào thì không còn `upload_files`
mồ côi hay dữ liệu nạp một nửa — SRS §5.2 yêu cầu "ghi dữ liệu trong một
transaction, rollback toàn bộ nếu tỷ lệ lỗi vượt ngưỡng". Lưu ý hệ quả: lệnh
`UPDATE upload_files SET status='processing'` cũng nằm trong transaction đó, nên
khi rollback thì bản ghi quay về 'pending'. Việc đưa nó sang trạng thái kết thúc
là của `src/jobs/parse_upload.py`, chạy ở một transaction KHÁC sau khi đã rollback.

Nếu tỷ lệ lỗi vượt `error_threshold` thì cả lô bị từ chối: `ImportRejectedError`
được ném ra để transaction rollback, không nạp một phần.
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import TARGET_TABLES, upload_errors, upload_files
from src.models.tables import areas as areas_table
from src.services.excel_parser import _CONVERTERS, STAGING_ROW_NUMBER, ParseError, TableTemplate

log = get_logger("src.services.import_records")

# Số dòng mỗi lần executemany. Đủ lớn để không tốn round-trip, đủ nhỏ để câu
# lệnh không phình bộ nhớ với file 20 MB.
INSERT_BATCH = 1000

# Trần số bản ghi upload_errors ghi xuống DB cho một file. Parser đã chặn ở
# MAX_ERRORS; đây là chốt chặn thứ hai để một file rác không bơm hàng trăm
# nghìn dòng vào upload_errors.
MAX_PERSISTED_ERRORS = 1000

# Cột không bao giờ bị ghi đè khi một dòng đã tồn tại: `id` là danh tính của bản
# ghi (mọi khoá ngoại trỏ vào nó), `created_at` là lúc nó xuất hiện lần đầu.
IMMUTABLE_ON_UPDATE = frozenset({"id", "created_at"})


def _upsert_stmt(target: sa.Table, template: TableTemplate):
    """Dựng `INSERT ... ON CONFLICT` cho bảng đích của một template.

    Template không khai `conflict_constraint` thì trả INSERT thường — hành vi cũ,
    trùng là vỡ cả lô.

    Có khai thì:

    * `versioned_by = None` → **DO NOTHING**. Dòng đã có được giữ nguyên.
    * `versioned_by = <cột>` → **DO UPDATE** nhưng CHỈ khi bản đến thực sự mới hơn:

          excluded.<cột> IS NOT NULL
          AND (target.<cột> IS NULL OR excluded.<cột> > target.<cột>)

      Đọc ngược lại ba trường hợp NULL cho rõ:

      - Bản đến không có mốc  → **không ghi đè**. Không biết nó mới hay cũ thì
        giữ nguyên thứ đang có là lựa chọn an toàn duy nhất.
      - Bản đang có không mốc, bản đến có → **ghi đè**. Dòng cũ không mang phiên
        bản nào để mà so; một bản ghi có phiên bản là thông tin tốt hơn hẳn.
      - Cả hai đều không mốc → **không ghi đè**, nên nạp lại cùng một file là
        thao tác không-làm-gì.

      Mốc bằng nhau cũng không ghi đè: `>` chứ không phải `>=`.
    """
    stmt = pg_insert(target)
    if template.conflict_constraint is None:
        return sa.insert(target)

    if template.versioned_by is None:
        return stmt.on_conflict_do_nothing(constraint=template.conflict_constraint)

    version = template.versioned_by
    incoming = stmt.excluded[version]
    stored = target.c[version]

    return stmt.on_conflict_do_update(
        constraint=template.conflict_constraint,
        set_={
            name: stmt.excluded[name]
            for name in target.c.keys()
            if name not in IMMUTABLE_ON_UPDATE and name not in template.conflict_columns
        },
        where=sa.and_(
            incoming.isnot(None),
            sa.or_(stored.is_(None), incoming > stored),
        ),
    )


class ImportRejectedError(Exception):
    """Tỷ lệ lỗi vượt ngưỡng — từ chối cả lô thay vì nạp một phần.

    Ném ra để `session.begin()` rollback. Job bắt lại và ghi trạng thái failed.
    """

    def __init__(self, rows_failed: int, rows_total: int, threshold: float) -> None:
        self.rows_failed = rows_failed
        self.rows_total = rows_total
        self.threshold = threshold
        super().__init__(f"Tỷ lệ lỗi {rows_failed}/{rows_total} vượt ngưỡng {threshold:.0%} — từ chối cả lô")


@dataclass(slots=True)
class ImportResult:
    """Kết quả nạp. `status` khớp `ck_upload_files_status`, trừ 'duplicate'."""

    status: str  # completed | failed | duplicate
    file_id: str | None = None
    rows_inserted: int = 0
    rows_failed: int = 0
    errors_persisted: int = 0
    duplicate_of: str | None = None


@dataclass(slots=True)
class _AreaIndex:
    """Tra `areas.id` theo tên phân khu, dựng sẵn một lần cho cả file."""

    by_name_and_type: dict[tuple[str, str], uuid.UUID] = field(default_factory=dict)
    by_name: dict[str, list[uuid.UUID]] = field(default_factory=dict)

    def resolve(self, area_name: str, unit_type: str | None) -> tuple[uuid.UUID | None, str | None]:
        """Trả `(area_id, error_code)`. Chỉ một trong hai có giá trị."""
        if unit_type:
            found = self.by_name_and_type.get((area_name, unit_type))
            return (found, None) if found else (None, "AREA_NOT_FOUND")

        candidates = self.by_name.get(area_name, [])
        if not candidates:
            return None, "AREA_NOT_FOUND"
        if len(candidates) > 1:
            # Cùng tên phân khu nhưng nhiều loại căn. `areas` không có UNIQUE nào
            # để tự phân định, nên bắt người dùng ghi rõ cột "Loại căn" thay vì
            # đoán bừa rồi gán số bán sang nhầm loại căn.
            return None, "AREA_AMBIGUOUS"
        return candidates[0], None


class ImportService:
    """Nạp một file staging vào DB. Không giữ trạng thái giữa các lần gọi."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def load(
        self,
        staging_path: Path,
        template: TableTemplate,
        *,
        file_id: uuid.UUID | str,
        project_id: uuid.UUID | str,
        parse_errors: list[ParseError],
        rows_failed_parse: int,
        error_threshold: float | None = None,
    ) -> ImportResult:
        """Nạp file staging vào bảng đích trong một transaction.

        Args:
            file_id: bản ghi `upload_files` do API tạo sẵn (status='pending').
            error_threshold: tỷ lệ lỗi tối đa chấp nhận được (0..1). None = lấy
                từ Settings. Vượt ngưỡng thì ném `ImportRejectedError`.
        """
        project_uuid = uuid.UUID(str(project_id))
        file_uuid = uuid.UUID(str(file_id))
        threshold = get_settings().import_error_threshold if error_threshold is None else error_threshold
        target = TARGET_TABLES[template.target_table]

        async with self._session_factory() as session:
            async with session.begin():  # ranh giới transaction duy nhất
                now = datetime.now(UTC)
                await session.execute(
                    sa.update(upload_files).where(upload_files.c.id == file_uuid).values(status="processing")
                )
                # Chạy lại một lô đã hỏng phải cho ra trạng thái sạch, không phải
                # chồng lỗi cũ lên lỗi mới: `/files/{id}/errors` sẽ lẫn lộn giữa
                # hai lần chạy. Nằm trong cùng transaction nên nếu lần này cũng
                # hỏng thì lỗi cũ được rollback trả lại nguyên vẹn.
                await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id == file_uuid))

                area_index = None
                if "area_name" in template.lookup_names:
                    area_index = await self._load_area_index(session, project_uuid)

                rows_inserted, load_errors = await self._insert_rows(
                    session,
                    staging_path,
                    template,
                    target,
                    file_id=file_uuid,
                    project_id=project_uuid,
                    area_index=area_index,
                    now=now,
                )

                rows_failed = rows_failed_parse + len(load_errors)
                rows_total = rows_inserted + rows_failed

                # Ngưỡng lỗi (SRS §5.2): quá nhiều dòng hỏng thì file coi như sai
                # template chứ không phải vài dòng gõ nhầm — nạp một phần lúc này
                # để lại dữ liệu nửa vời mà người dùng khó gỡ hơn là làm lại.
                if rows_total and rows_failed / rows_total > threshold:
                    raise ImportRejectedError(rows_failed, rows_total, threshold)

                errors_persisted = await self._insert_errors(session, file_uuid, [*parse_errors, *load_errors], now)

                status = "failed" if rows_inserted == 0 and rows_failed > 0 else "completed"
                await session.execute(
                    sa.update(upload_files)
                    .where(upload_files.c.id == file_uuid)
                    .values(status=status, rows_ok=rows_inserted, rows_failed=rows_failed)
                )

        log.info(
            "import.finished",
            template=template.name,
            target_table=template.target_table,
            status=status,
            rows_inserted=rows_inserted,
            rows_failed=rows_failed,
        )
        return ImportResult(
            status=status,
            file_id=str(file_uuid),
            rows_inserted=rows_inserted,
            rows_failed=rows_failed,
            errors_persisted=errors_persisted,
        )

    async def _load_area_index(self, session: AsyncSession, project_id: uuid.UUID) -> _AreaIndex:
        """Nạp toàn bộ phân khu của dự án một lần, thay vì tra DB theo từng dòng."""
        rows = await session.execute(
            sa.select(areas_table.c.id, areas_table.c.area_name, areas_table.c.unit_type).where(
                areas_table.c.project_id == project_id
            )
        )
        index = _AreaIndex()
        for area_id, area_name, unit_type in rows:
            index.by_name_and_type[(area_name, unit_type)] = area_id
            index.by_name.setdefault(area_name, []).append(area_id)
        return index

    async def _insert_rows(
        self,
        session: AsyncSession,
        staging_path: Path,
        template: TableTemplate,
        target: sa.Table,
        *,
        file_id: uuid.UUID,
        project_id: uuid.UUID,
        area_index: _AreaIndex | None,
        now: datetime,
    ) -> tuple[int, list[ParseError]]:
        """Đọc staging theo dòng, dựng bản ghi DB, upsert theo lô.

        `inserted` đếm số dòng ĐƯỢC NHẬN để ghi, không phải số dòng thực sự đổi
        trong bảng: một dòng nạp lại mà bản đang có đã mới hơn vẫn tính là nhận
        thành công (nó đã được xử lý đúng, chỉ là cố ý không ghi đè). Đây cũng là
        con số vào `upload_files.rows_ok`, nên đọc là "số dòng của file đã qua
        được tầng ghi", không phải "số dòng mới trong bảng".
        """
        converters = {spec.name: _CONVERTERS[spec.kind] for spec in template.columns}
        statement = _upsert_stmt(target, template)
        conflict_columns = template.conflict_columns
        errors: list[ParseError] = []
        seen_hashes: set[str] = set()
        seen_keys: set[tuple] = set()
        batch: list[dict[str, Any]] = []
        inserted = 0

        with staging_path.open("r", encoding="utf-8", newline="") as handle:
            for staged in csv.DictReader(handle):
                row_number = int(staged[STAGING_ROW_NUMBER])
                row_hash = staged.get("source_row_hash") or ""

                if row_hash and row_hash in seen_hashes:
                    # Trùng y hệt NGAY TRONG một file (mọi cột giống nhau).
                    errors.append(
                        ParseError(row_number, None, "DUPLICATE_ROW", "Dòng trùng với một dòng trước trong file")
                    )
                    continue
                if row_hash:
                    seen_hashes.add(row_hash)

                record = self._build_record(staged, template, converters, file_id, project_id, now)

                if area_index is not None:
                    area_id, error_code = area_index.resolve(staged["area_name"], staged.get("unit_type") or None)
                    if error_code:
                        errors.append(
                            ParseError(
                                row_number,
                                "area_name",
                                error_code,
                                f"Không xác định được phân khu '{staged['area_name']}' trong dự án",
                            )
                        )
                        continue
                    record["area_id"] = area_id

                # Trùng KHOÁ NGHIỆP VỤ trong cùng một file — khác với trùng hash ở
                # trên: hai dòng cùng khoá nhưng khác nội dung (sửa số liệu ngay
                # trong một file) có hash khác nhau nên lọt qua chốt trên.
                # Phải chặn ở đây vì Postgres không cho một lệnh ON CONFLICT
                # DO UPDATE chạm hai lần vào cùng một dòng; để lọt xuống DB thì cả
                # lô vỡ. Giữ dòng ĐẦU, giống quy ước của chốt hash.
                if conflict_columns:
                    key = tuple(record.get(name) for name in conflict_columns)
                    if key in seen_keys:
                        errors.append(
                            ParseError(
                                row_number,
                                None,
                                "DUPLICATE_KEY",
                                "Dòng trùng khoá nghiệp vụ với một dòng trước trong file",
                            )
                        )
                        continue
                    seen_keys.add(key)

                batch.append(record)
                if len(batch) >= INSERT_BATCH:
                    await session.execute(statement, batch)
                    inserted += len(batch)
                    batch = []

        if batch:
            await session.execute(statement, batch)
            inserted += len(batch)

        return inserted, errors

    def _build_record(
        self,
        staged: dict[str, str],
        template: TableTemplate,
        converters: dict[str, Any],
        file_id: uuid.UUID,
        project_id: uuid.UUID,
        now: datetime,
    ) -> dict[str, Any]:
        """Đổi một dòng staging (toàn chuỗi) thành bản ghi đúng kiểu của bảng đích.

        Phải ép kiểu lại vì đi qua CSV thì mọi giá trị đều thành chuỗi. Dùng đúng
        bộ converter của parser nên quy tắc không bị lệch làm hai bản.
        """
        record: dict[str, Any] = {"id": uuid.uuid4(), "created_at": now}

        for name in template.column_names:
            raw = staged.get(name, "")
            record[name] = converters[name](raw) if raw != "" else None

        if template.emits_row_hash:
            record["source_row_hash"] = staged["source_row_hash"]

        if template.target_table == "areas":
            record["project_id"] = project_id
        else:
            record["file_id"] = file_id
        return record

    async def _insert_errors(
        self,
        session: AsyncSession,
        file_id: uuid.UUID,
        errors: list[ParseError],
        now: datetime,
    ) -> int:
        if not errors:
            return 0
        capped = errors[:MAX_PERSISTED_ERRORS]
        await session.execute(
            sa.insert(upload_errors),
            [{"id": uuid.uuid4(), "created_at": now, **e.as_record(file_id)} for e in capped],
        )
        return len(capped)
