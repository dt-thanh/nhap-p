"""Test tầng ghi DB (`ImportService`) trên PostgreSQL THẬT, schema từ migration thật.

Không mock DB: điểm mấu chốt của tầng này là ràng buộc NOT NULL / CHECK / UNIQUE
và ranh giới transaction — mock đi thì chẳng còn gì để kiểm tra.

Cách chạy (không cần bước thủ công nào ngoài .env + Docker Compose):

    bash scripts/test_db.sh

Script dựng service `db`, chờ sẵn sàng, tạo database "<POSTGRES_DB>_test", chạy
migration, rồi gọi pytest với TEST_DATABASE_URL đã trỏ đúng chỗ.

`pytest` trần sẽ skip cả file nếu không có TEST_DATABASE_URL (hoặc DATABASE_URL).
Xem `_refuses_to_wipe`: chỉ chấp nhận database có tên kết thúc bằng `_test`.

LƯU Ý khi migration đổi: database test không tự nâng cấp vì Alembic thấy nó đã ở
head. Phải xoá rồi để scripts/test_db.sh dựng lại:

    docker compose exec db psql -U "$POSTGRES_USER" -d postgres \\
        -c 'DROP DATABASE "<POSTGRES_DB>_test"'
"""

from __future__ import annotations

import asyncio
import csv
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import (
    areas,
    inventory_snapshots,
    sales_records,
    upload_errors,
    upload_files,
)
from src.services.excel_parser import AREAS_TEMPLATE, INVENTORY_TEMPLATE, SALES_TEMPLATE, ParseError
from src.services.import_records import (
    MAX_PERSISTED_ERRORS,
    ImportRejectedError,
    ImportResult,
    ImportService,
)

# TEST_DATABASE_URL là biến chính; nếu không có thì lùi về DATABASE_URL để ai đã
# export sẵn một DB đúng thì chạy được ngay, không phải khai thêm biến.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _refuses_to_wipe(url: str | None) -> str:
    """Chặn chạy test huỷ dữ liệu lên database KHÔNG phải database test.

    `clean_db` xoá sạch bảng trước mỗi test, trong đó có DELETE FROM projects.
    Trỏ nhầm vào DB dev/production là mất dữ liệu thật, không khôi phục được.
    Quy ước: tên database phải kết thúc bằng `_test`.
    """
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"

    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return (
            f"Từ chối xoá dữ liệu trên database '{name}' vì tên không kết thúc bằng '_test'. "
            "Chạy `bash scripts/test_db.sh` để dùng database test riêng."
        )
    return ""


_SKIP_REASON = _refuses_to_wipe(TEST_DATABASE_URL)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or ""),
]

PROJECT_ID = uuid.UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301")

SALES_COLUMNS = [
    "row_number",
    "area_name",
    "unit_type",
    "sold_date",
    "units_sold",
    "external_record_id",
    "source_row_hash",
]
INVENTORY_COLUMNS = [
    "row_number",
    "area_name",
    "unit_type",
    "snapshot_date",
    "units_remaining",
    "snapshot_type",
    "source_row_hash",
]
AREAS_COLUMNS = ["row_number", "area_name", "unit_type", "bedrooms", "area_sqm", "total_units"]


# --- Kết nối và dọn dẹp -----------------------------------------------------


@pytest_asyncio.fixture
async def session_factory():
    """NullPool: mỗi test mở connection mới, không giữ connection qua event loop."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    """Mỗi test bắt đầu từ DB sạch nên chạy lại nhiều lần vẫn ra kết quả như nhau.

    Release-hardening pass (post-PR-5): trước đây hàm này tự liệt kê một danh
    sách `DELETE FROM` hẹp (chỉ sáu bảng thuộc riêng luồng nạp file), không
    biết gì về `units`/`deals`/bảng xếp hạng — những bảng KHÔNG thuộc phạm vi
    file này nhưng vẫn FK tới `areas`. `DELETE FROM areas` khi đó là xoá TOÀN
    BỘ bảng (không giới hạn theo `PROJECT_ID` của riêng test này), nên một
    hàng `units` còn sót (từ một suite khác, hoặc từ một tiến trình pytest bị
    ngắt giữa chừng trước khi fixture dọn dẹp của NÓ kịp chạy — đã xảy ra thật:
    máy chủ hết đĩa giữa một lượt test, Postgres crash, và lượt chạy `pytest`
    kế tiếp gặp `ForeignKeyViolationError` ngay ở fixture này) sẽ làm
    `DELETE FROM areas` nổ khoá ngoại `fk_units_area_id`, và lỗi hiện ra ở một
    file hoàn toàn không liên quan tới nguyên nhân thật.

    Fix: dùng lại `truncate_tables()` — MỘT nguồn liệt kê bảng duy nhất mà
    `tests/conftest.py::truncate_all` đã dùng cho các module khác — với
    `TRUNCATE ... RESTART IDENTITY CASCADE`. `CASCADE` ở đây dọn luôn mọi bảng
    FK-phụ thuộc dù không được liệt kê tường minh (`units`/`deals`/
    `ranking_scores`/...), nên fixture này không còn cần BIẾT về những bảng đó
    để vẫn an toàn trước chúng — đúng tinh thần "test cleanup, không phải mô
    hình dữ liệu sản phẩm" (không đụng constraint/FK nào, không CASCADE trong
    migration/schema thật). Dọn CẢ HAI đầu (trước và sau mỗi test), cùng kỷ
    luật `truncate_all`: một suite khác chạy NGAY SAU file này cũng không thấy
    sót lại gì.
    """
    from tests.conftest import truncate_tables

    statement = sa.text(
        "TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in truncate_tables()) + " RESTART IDENTITY CASCADE"
    )

    async def _reset() -> None:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(statement)
                await session.execute(
                    sa.text(
                        "INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Pilot', :d, :ts)"
                    ),
                    {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
                )

    await _reset()
    yield
    async with session_factory() as session:
        async with session.begin():
            await session.execute(statement)


# --- Helper -----------------------------------------------------------------


async def _seed_areas(session_factory, *specs: tuple[str, str], project_id: uuid.UUID = PROJECT_ID) -> dict:
    """Tạo phân khu từ các cặp (area_name, unit_type). Trả map → areas.id."""
    ids = {spec: uuid.uuid4() for spec in specs}
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(areas),
                [
                    {
                        "id": area_id,
                        "project_id": project_id,
                        "area_name": area_name,
                        "unit_type": unit_type,
                        "bedrooms": 2,
                        "area_sqm": 75,
                        "total_units": 100,
                        "created_at": datetime.now(UTC),
                    }
                    for (area_name, unit_type), area_id in ids.items()
                ],
            )
    return ids


@pytest_asyncio.fixture
async def seeded_areas(session_factory):
    """Hai phân khu để sales/inventory có cái mà tra `area_id`."""
    return await _seed_areas(session_factory, ("A1", "2PN"), ("A2", "3PN"))


def _staging(tmp_path, columns, rows, name="staging.csv") -> Path:
    """Ghi một file staging đúng định dạng mà `parse_to_csv` sinh ra."""
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    return path


async def _create_upload_file(session_factory, *, project_id=PROJECT_ID, filename="s.csv", checksum=None):
    """Tạo bản ghi `upload_files` như tầng API vẫn làm trước khi enqueue."""
    file_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=file_id,
                    project_id=project_id,
                    uploaded_by=None,
                    filename=filename,
                    checksum=checksum or f"chk-{uuid.uuid4()}",
                    status="pending",
                    rows_ok=0,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                )
            )
    return file_id


async def _load(session_factory, staging, template=SALES_TEMPLATE, **overrides) -> ImportResult:
    """Gọi ImportService với mặc định hợp lý; test chỉ khai phần nó quan tâm.

    Bản ghi `upload_files` do tầng API tạo trong đời thật, nên helper này tạo
    sẵn rồi truyền `file_id` xuống — giống hệt đường chạy production.
    """
    project_id = overrides.pop("project_id", PROJECT_ID)
    file_id = overrides.pop(
        "file_id",
        None,
    ) or await _create_upload_file(
        session_factory,
        project_id=project_id,
        filename=overrides.pop("filename", "s.csv"),
        checksum=overrides.pop("checksum", None),
    )
    overrides.pop("filename", None)
    overrides.pop("checksum", None)
    kwargs = {
        "file_id": file_id,
        "project_id": project_id,
        "parse_errors": [],
        "rows_failed_parse": 0,
        # Tắt ngưỡng lỗi cho mặc định: đa số test ở đây soi hành vi THEO DÒNG,
        # bật ngưỡng lên thì cả lô bị từ chối trước khi nhìn thấy hành vi đó.
        # Ngưỡng có test riêng ở mục "Ngưỡng tỷ lệ lỗi".
        "error_threshold": 1.0,
        **overrides,
    }
    return await ImportService(session_factory).load(staging, template, **kwargs)


async def _count(session_factory, table) -> int:
    async with session_factory() as session:
        return await session.scalar(sa.select(sa.func.count()).select_from(table))


async def _assert_no_data_persisted(session_factory) -> None:
    """Sau rollback, không còn dữ liệu nạp dở hay lỗi nào.

    `upload_files` VẪN CÒN: bản ghi đó do tầng API tạo trước khi enqueue, nằm
    ngoài transaction của service nên rollback không cuốn theo. Job sẽ cập nhật
    nó sang 'failed'.
    """
    assert await _count(session_factory, sales_records) == 0, "còn dữ liệu nạp một nửa"
    assert await _count(session_factory, upload_errors) == 0, "còn upload_errors mồ côi"


# --- Chèn dữ liệu hợp lệ ----------------------------------------------------


async def test_sales_rows_are_inserted_with_resolved_area_id(session_factory, seeded_areas, tmp_path):
    """Dòng hợp lệ vào `sales_records`, `area_name` được đổi thành `area_id`."""
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"],
            [3, "A2", "3PN", "2026-01-06", 5, "TX-2", "h2"],
        ],
    )

    result = await _load(session_factory, staging)

    assert result.status == "completed"
    assert result.rows_inserted == 2

    async with session_factory() as session:
        rows = (await session.execute(sa.select(sales_records).order_by(sales_records.c.sold_date))).all()

    assert len(rows) == 2
    assert rows[0].sold_date == date(2026, 1, 5)
    assert rows[0].units_sold == 3
    assert rows[0].external_record_id == "TX-1"
    assert rows[0].area_id == seeded_areas[("A1", "2PN")]
    assert rows[0].file_id == uuid.UUID(result.file_id)


async def test_inventory_rows_are_inserted(session_factory, seeded_areas, tmp_path):
    """Template inventory ghi vào `inventory_snapshots`, giữ nguyên snapshot_type."""
    staging = _staging(
        tmp_path,
        INVENTORY_COLUMNS,
        [[2, "A1", "2PN", "2026-01-05", 40, "opening", "i1"]],
    )

    result = await _load(session_factory, staging, INVENTORY_TEMPLATE)

    assert result.rows_inserted == 1
    async with session_factory() as session:
        row = (await session.execute(sa.select(inventory_snapshots))).one()
    assert row.units_remaining == 40
    assert row.snapshot_type == "opening"


async def test_areas_rows_are_inserted_with_project_id(session_factory, tmp_path):
    """Template areas ghi thẳng vào `areas`; `project_id` lấy từ phạm vi upload."""
    staging = _staging(tmp_path, AREAS_COLUMNS, [[2, "B1", "Studio", 1, "45.5", 30]])

    result = await _load(session_factory, staging, AREAS_TEMPLATE)

    assert result.rows_inserted == 1
    async with session_factory() as session:
        row = (await session.execute(sa.select(areas).where(areas.c.area_name == "B1"))).one()
    assert row.project_id == PROJECT_ID
    assert row.bedrooms == 1
    assert float(row.area_sqm) == 45.5  # Numeric giữ đúng giá trị, không qua float


# --- upload_files: số đếm, trạng thái, metadata -----------------------------


async def test_upload_files_records_counts_status_and_metadata(session_factory, seeded_areas, tmp_path):
    """Một dòng `upload_files` được tạo với đủ số đếm, trạng thái và thời điểm."""
    before = datetime.now(UTC)
    staging = _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"]])

    result = await _load(session_factory, staging, filename="sales.csv", checksum="c1")
    after = datetime.now(UTC)

    async with session_factory() as session:
        upload = (await session.execute(sa.select(upload_files))).one()
        sales_row = (await session.execute(sa.select(sales_records))).one()

    assert upload.status == "completed"
    assert (upload.rows_ok, upload.rows_failed) == (1, 0)
    assert upload.filename == "sales.csv"
    assert upload.checksum == "c1"
    assert upload.project_id == PROJECT_ID
    assert upload.uploaded_by is None  # MVP 1 chưa có auth
    assert str(upload.id) == result.file_id
    assert before <= upload.uploaded_at <= after
    assert isinstance(sales_row.id, uuid.UUID)
    assert before <= sales_row.created_at <= after


async def test_upload_file_is_marked_failed_when_no_row_is_inserted(session_factory, seeded_areas, tmp_path):
    """Không dòng nào vào được mà vẫn có lỗi → status `failed` (khớp CHECK)."""
    staging = _staging(tmp_path, SALES_COLUMNS, [[2, "KHONG-CO", "2PN", "2026-01-05", 3, "TX-1", "h1"]])

    result = await _load(session_factory, staging)

    assert result.status == "failed"
    async with session_factory() as session:
        upload = (await session.execute(sa.select(upload_files))).one()
    assert upload.status == "failed"
    assert (upload.rows_ok, upload.rows_failed) == (0, 1)


async def test_empty_staging_file_completes_with_zero_rows(session_factory, tmp_path):
    """File staging rỗng → `completed` với 0 dòng, KHÔNG phải `failed`.

    Ghi lại hành vi hiện tại: `failed` chỉ khi có lỗi mà không dòng nào vào được.
    Không lỗi, không dòng nào (parser đã loại hết ở tầng trên) là hợp lệ.
    """
    staging = _staging(tmp_path, SALES_COLUMNS, [])

    result = await _load(session_factory, staging)

    assert result.status == "completed"
    assert (result.rows_inserted, result.rows_failed) == (0, 0)
    async with session_factory() as session:
        upload = (await session.execute(sa.select(upload_files))).one()
    assert upload.status == "completed"


# --- upload_errors ----------------------------------------------------------


async def test_parse_errors_are_persisted_with_row_and_column(session_factory, seeded_areas, tmp_path):
    """Lỗi do parser phát hiện được ghi vào `upload_errors`, gắn đúng file_id."""
    staging = _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"]])

    result = await _load(
        session_factory,
        staging,
        parse_errors=[ParseError(5, "sold_date", "INVALID_DATE", "sai định dạng ngày")],
        rows_failed_parse=1,
    )

    assert result.errors_persisted == 1
    async with session_factory() as session:
        error = (await session.execute(sa.select(upload_errors))).one()
    assert error.row_number == 5
    assert error.column_name == "sold_date"
    assert error.error_code == "INVALID_DATE"
    assert error.file_id == uuid.UUID(result.file_id)


async def test_parse_and_load_errors_are_counted_together(session_factory, seeded_areas, tmp_path):
    """`rows_failed` gộp lỗi của parser và lỗi phát sinh lúc nạp."""
    staging = _staging(tmp_path, SALES_COLUMNS, [[2, "KHONG-CO", "2PN", "2026-01-05", 3, "TX-1", "h1"]])

    result = await _load(
        session_factory,
        staging,
        parse_errors=[ParseError(5, "sold_date", "INVALID_DATE", "sai ngày")],
        rows_failed_parse=1,
    )

    assert result.status == "failed"
    assert (result.rows_inserted, result.rows_failed, result.errors_persisted) == (0, 2, 2)
    async with session_factory() as session:
        errors = (await session.execute(sa.select(upload_errors))).all()
    assert {row.error_code for row in errors} == {"INVALID_DATE", "AREA_NOT_FOUND"}


async def test_persisted_errors_are_capped_but_counts_are_not(session_factory, tmp_path):
    """Số lỗi ghi xuống DB bị chặn ở `MAX_PERSISTED_ERRORS`; `rows_failed` vẫn đủ."""
    staging = _staging(tmp_path, SALES_COLUMNS, [])
    total = MAX_PERSISTED_ERRORS + 10
    parse_errors = [ParseError(index + 2, "sold_date", "INVALID_DATE", "sai ngày") for index in range(total)]

    result = await _load(session_factory, staging, parse_errors=parse_errors, rows_failed_parse=total)

    assert result.rows_failed == total
    assert result.errors_persisted == MAX_PERSISTED_ERRORS
    assert await _count(session_factory, upload_errors) == MAX_PERSISTED_ERRORS

    async with session_factory() as session:
        upload = (await session.execute(sa.select(upload_files))).one()
    assert (upload.rows_ok, upload.rows_failed) == (0, total)


# --- Phân giải phân khu -----------------------------------------------------


@pytest.mark.parametrize(
    ("template", "columns", "row"),
    [
        (SALES_TEMPLATE, SALES_COLUMNS, [3, "KHONG-CO", "2PN", "2026-01-05", 3, "TX-1", "h1"]),
        (INVENTORY_TEMPLATE, INVENTORY_COLUMNS, [3, "KHONG-CO", "2PN", "2026-01-05", 40, "opening", "i1"]),
    ],
    ids=["sales", "inventory"],
)
async def test_unknown_area_becomes_row_error(session_factory, seeded_areas, tmp_path, template, columns, row):
    """Phân khu không tồn tại → lỗi theo dòng, không làm vỡ cả lần nạp."""
    staging = _staging(tmp_path, columns, [row])

    result = await _load(session_factory, staging, template)

    assert result.rows_inserted == 0
    assert result.rows_failed == 1
    async with session_factory() as session:
        error = (await session.execute(sa.select(upload_errors))).one()
    assert error.error_code == "AREA_NOT_FOUND"
    assert error.column_name == "area_name"
    assert error.row_number == 3  # đúng dòng trên file gốc


async def test_good_rows_still_load_when_another_row_has_unknown_area(session_factory, seeded_areas, tmp_path):
    """Một dòng hỏng không kéo theo dòng tốt — đây là nạp một phần, không phải all-or-nothing."""
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"],
            [3, "KHONG-CO", "2PN", "2026-01-06", 5, "TX-2", "h2"],
        ],
    )

    result = await _load(session_factory, staging)

    assert (result.rows_inserted, result.rows_failed) == (1, 1)
    assert await _count(session_factory, sales_records) == 1


@pytest.mark.parametrize(
    ("template", "columns", "row"),
    [
        (SALES_TEMPLATE, SALES_COLUMNS, [2, "DUP", "", "2026-01-05", 3, "TX-1", "h1"]),
        (INVENTORY_TEMPLATE, INVENTORY_COLUMNS, [2, "DUP", "", "2026-01-05", 40, "opening", "i1"]),
    ],
    ids=["sales", "inventory"],
)
async def test_ambiguous_area_name_is_reported_not_guessed(session_factory, tmp_path, template, columns, row):
    """Cùng tên phân khu ở hai loại căn, file không ghi rõ loại → phải báo lỗi.

    Đoán bừa sẽ gán số bán sang nhầm loại căn — sai lệch im lặng, rất khó phát hiện.
    """
    await _seed_areas(session_factory, ("DUP", "2PN"), ("DUP", "3PN"))
    staging = _staging(tmp_path, columns, [row])

    result = await _load(session_factory, staging, template)

    assert result.rows_inserted == 0
    async with session_factory() as session:
        error = (await session.execute(sa.select(upload_errors))).one()
    assert error.error_code == "AREA_AMBIGUOUS"


async def test_schema_prevents_two_areas_with_same_name_and_unit_type(session_factory):
    """`uq_areas_project_name_unit_type` loại bỏ nhập nhằng ở mức (tên, loại căn).

    Nhờ ràng buộc này, khi file CÓ ghi `unit_type` thì việc tra `area_id` luôn
    xác định — chỉ thiếu `unit_type` mới sinh AREA_AMBIGUOUS.

    Chèn thẳng chứ không qua `_seed_areas`: helper đó gom spec vào dict nên hai
    spec giống hệt nhau sẽ bị gộp làm một, không tạo ra được tình huống trùng.
    """
    row = {
        "project_id": PROJECT_ID,
        "area_name": "SAME",
        "unit_type": "2PN",
        "bedrooms": 2,
        "area_sqm": 75,
        "total_units": 100,
        "created_at": datetime.now(UTC),
    }

    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.insert(areas),
                    [{"id": uuid.uuid4(), **row}, {"id": uuid.uuid4(), **row}],
                )


# --- Chống trùng ------------------------------------------------------------


async def test_schema_allows_two_files_with_the_same_checksum(session_factory, seeded_areas, tmp_path):
    """0005 bỏ `uq_upload_files_project_checksum`: byte của file KHÔNG còn là danh tính lô.

    Lý do đổi: ràng buộc cũ khiến một lô hỏng giữ chỗ checksum vĩnh viễn, nên
    file đã sửa không nạp lại được, và mọi lần gửi lại đều là lỗi thay vì được
    bỏ qua êm. Chống trùng DỮ LIỆU chuyển hẳn về khoá nghiệp vụ của bảng đích —
    đó mới là chỗ nó thuộc về, và test dưới đây chốt rằng nó vẫn hiệu lực.
    """
    staging = _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"]])

    await _load(session_factory, staging, checksum="same")
    await _load(session_factory, staging, checksum="same")

    assert await _count(session_factory, upload_files) == 2, "hai lô cùng checksum phải cùng tồn tại"
    assert await _count(session_factory, sales_records) == 1, "nhưng dữ liệu thì không được nhân đôi"


async def test_same_checksum_in_another_project_is_allowed(session_factory):
    """Hai dự án có thể nhận cùng một file — ràng buộc chỉ phạm vi trong dự án."""
    other_project = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Other', :d, :ts)"),
                {"id": other_project, "d": date(2026, 2, 1), "ts": datetime.now(UTC)},
            )

    await _create_upload_file(session_factory, checksum="shared")
    await _create_upload_file(session_factory, project_id=other_project, checksum="shared")

    assert await _count(session_factory, upload_files) == 2


async def test_duplicate_row_hash_within_file_is_skipped(session_factory, seeded_areas, tmp_path):
    """Hai dòng cùng `source_row_hash` trong một file → dòng sau thành lỗi."""
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 3, "TX-1", "same-hash"],
            [3, "A1", "2PN", "2026-01-06", 4, "TX-2", "same-hash"],
        ],
    )

    result = await _load(session_factory, staging)

    assert result.rows_inserted == 1
    async with session_factory() as session:
        error = (await session.execute(sa.select(upload_errors))).one()
    assert error.error_code == "DUPLICATE_ROW"
    assert error.row_number == 3


# --- Ranh giới transaction --------------------------------------------------


async def test_check_violation_rolls_back_everything(session_factory, seeded_areas, tmp_path):
    """Vi phạm CHECK giữa chừng → không còn upload_files mồ côi hay dữ liệu một nửa."""
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"],
            [3, "A2", "3PN", "2026-01-06", -5, "TX-2", "h2"],  # ck_..._units_sold_nonnegative
        ],
    )

    with pytest.raises(IntegrityError):
        await _load(session_factory, staging)

    await _assert_no_data_persisted(session_factory)


async def test_business_key_duplicate_no_longer_rolls_back_the_batch(session_factory, seeded_areas, tmp_path):
    """Hai dòng trùng (area_id, sold_date, external_record_id) trong một file.

    TRƯỚC 0005 ca này ném IntegrityError và cuốn theo cả lô — chính là lỗi đang
    sửa. Giờ dòng sau thành lỗi theo dòng, dòng đầu vẫn vào bảng.
    """
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 3, "TX-SAME", "hash-a"],
            [3, "A1", "2PN", "2026-01-05", 4, "TX-SAME", "hash-b"],
        ],
    )

    result = await _load(session_factory, staging)

    assert await _count(session_factory, sales_records) == 1
    assert (result.rows_inserted, result.rows_failed, result.status) == (1, 1, "completed")


async def test_unabsorbable_constraint_violation_still_rolls_back_everything(session_factory, seeded_areas, tmp_path):
    """Vi phạm mà upsert KHÔNG nuốt được vẫn phải rollback sạch.

    `ON CONFLICT` chỉ nhắm đúng MỘT ràng buộc. `uq_sales_area_source_row_hash`
    nằm ngoài tầm với: hai dòng KHÁC khoá nghiệp vụ mà trùng hash, nằm ở hai lô
    khác nhau (chốt hash chỉ soi trong phạm vi một file) vẫn làm vỡ cả lô — và đó
    là hành vi đúng: không nạp một nửa. Job chuyển lô sang 'failed'
    (xem tests/test_jobs/test_parse_upload.py).
    """
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", "same-hash"]], name="one.csv"),
    )
    clashing = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-06", 4, "TX-2", "same-hash"],  # khoá khác, hash trùng lô trước
            [3, "A1", "2PN", "2026-01-07", 5, "TX-3", "other-hash"],  # dòng lành, phải bị cuốn theo
        ],
        name="two.csv",
    )

    with pytest.raises(IntegrityError):
        await _load(session_factory, clashing)

    # Lô một vẫn nguyên; lô hai không để lại gì.
    assert await _count(session_factory, sales_records) == 1
    assert await _count(session_factory, upload_errors) == 0


async def test_violation_in_a_later_batch_still_rolls_back_earlier_batches(session_factory, seeded_areas, tmp_path):
    """Lô đầu đã executemany xong vẫn phải bị cuốn theo khi lô sau vỡ.

    Vượt `INSERT_BATCH` (1000) để chắc chắn có ít nhất hai lô.
    """
    rows = [[index + 2, "A1", "2PN", "2026-01-05", 1, f"TX-{index}", f"h-{index}"] for index in range(1000)]
    rows.append([1002, "A1", "2PN", "2026-01-06", -1, "TX-BAD", "bad-hash"])
    staging = _staging(tmp_path, SALES_COLUMNS, rows)

    with pytest.raises(IntegrityError):
        await _load(session_factory, staging)

    await _assert_no_data_persisted(session_factory)


async def test_unknown_project_rolls_back(session_factory, tmp_path):
    """project_id không có thật → khoá ngoại chặn, không để lại gì."""
    staging = _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"]])

    with pytest.raises(IntegrityError):
        await _load(session_factory, staging, project_id=uuid.uuid4())

    await _assert_no_data_persisted(session_factory)


async def test_rollback_leaves_no_upload_errors(session_factory, seeded_areas, tmp_path):
    """Rollback cuốn theo cả `upload_errors` — không có lỗi mồ côi không thuộc file nào."""
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"],
            [3, "A1", "2PN", "2026-01-06", -5, "TX-2", "h2"],
        ],
    )

    with pytest.raises(IntegrityError):
        await _load(
            session_factory,
            staging,
            parse_errors=[ParseError(10, "sold_date", "INVALID_DATE", "sai ngày")],
            rows_failed_parse=1,
        )

    await _assert_no_data_persisted(session_factory)


def _bad_staging_missing_file(tmp_path):
    return tmp_path / "khong-ton-tai.csv"


def _bad_staging_wrong_header(tmp_path):
    return _staging(tmp_path, ["cot_la"], [["gia tri"]])


def _bad_staging_unconvertible_value(tmp_path):
    return _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "khong-phai-ngay", 3, "TX-1", "h1"]])


@pytest.mark.parametrize(
    ("make_staging", "expected_error"),
    [
        (_bad_staging_missing_file, FileNotFoundError),
        (_bad_staging_wrong_header, KeyError),
        (_bad_staging_unconvertible_value, ValueError),
    ],
    ids=["missing_file", "wrong_header", "unconvertible_value"],
)
async def test_unreadable_staging_file_rolls_back(
    session_factory, seeded_areas, tmp_path, make_staging, expected_error
):
    """File staging hỏng → ném lỗi và không ghi gì.

    Những trường hợp này lẽ ra parser đã chặn; đây là chốt chặn để một file
    staging hỏng không tạo ra `upload_files` treo ở trạng thái `processing`.
    """
    with pytest.raises(expected_error):
        await _load(session_factory, make_staging(tmp_path))

    await _assert_no_data_persisted(session_factory)


async def test_concurrent_imports_of_same_file_leave_exactly_one_row(session_factory, seeded_areas, tmp_path):
    """Hai lần nạp SONG SONG cùng một file: cả hai xong xuôi, dữ liệu vẫn một dòng.

    Sau 0005 không còn bên nào "thua" ở tầng file — bất biến cần giữ đã chuyển
    xuống bảng đích. Một bên có thể vướng deadlock/serialization khi hai
    transaction cùng upsert một dòng; điều KHÔNG được phép xảy ra là hai dòng
    nghiệp vụ trùng nhau.
    """
    staging = _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"]])

    async def run():
        return await _load(session_factory, staging, checksum="concurrent")

    results = await asyncio.gather(run(), run(), return_exceptions=True)

    assert await _count(session_factory, sales_records) == 1, "upsert đồng thời đã tạo dòng trùng"
    completed = [r for r in results if isinstance(r, ImportResult) and r.status == "completed"]
    assert completed, "không lần nạp nào hoàn tất"
    for outcome in results:
        assert not isinstance(outcome, BaseException) or isinstance(outcome, IntegrityError), (
            f"lỗi ngoài dự kiến khi nạp song song: {outcome!r}"
        )


# --- Chưa cài đặt -----------------------------------------------------------


async def test_error_rate_above_threshold_rejects_the_whole_batch(session_factory, seeded_areas, tmp_path):
    """SRS §5.2: quá ngưỡng tỷ lệ lỗi thì từ chối cả lô, không nạp một phần.

    4/5 dòng hỏng = 80% > ngưỡng 50%, nên dòng tốt duy nhất cũng không được vào.
    """
    rows = [[2, "A1", "2PN", "2026-01-05", 1, "TX-1", "h1"]]
    rows += [[i, "KHONG-CO", "2PN", "2026-01-05", 1, f"TX-{i}", f"h{i}"] for i in range(3, 7)]
    staging = _staging(tmp_path, SALES_COLUMNS, rows)

    with pytest.raises(ImportRejectedError) as exc:
        await _load(session_factory, staging, error_threshold=0.5)

    assert exc.value.rows_failed == 4
    assert exc.value.rows_total == 5
    await _assert_no_data_persisted(session_factory)


async def test_error_rate_at_or_below_threshold_still_loads(session_factory, seeded_areas, tmp_path):
    """Đúng bằng ngưỡng thì vẫn nạp — so sánh là `>` chứ không phải `>=`."""
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 1, "TX-1", "h1"],
            [3, "KHONG-CO", "2PN", "2026-01-05", 1, "TX-2", "h2"],
        ],
    )

    result = await _load(session_factory, staging, error_threshold=0.5)

    assert result.rows_inserted == 1
    assert result.rows_failed == 1


async def test_threshold_defaults_to_settings(session_factory, seeded_areas, tmp_path):
    """Không truyền error_threshold thì lấy Settings.import_error_threshold (0.5)."""
    from src.config import get_settings

    assert get_settings().import_error_threshold == 0.5

    rows = [[i, "KHONG-CO", "2PN", "2026-01-05", 1, f"TX-{i}", f"h{i}"] for i in range(2, 6)]
    staging = _staging(tmp_path, SALES_COLUMNS, rows)
    file_id = await _create_upload_file(session_factory)

    with pytest.raises(ImportRejectedError):
        await ImportService(session_factory).load(
            staging,
            SALES_TEMPLATE,
            file_id=file_id,
            project_id=PROJECT_ID,
            parse_errors=[],
            rows_failed_parse=0,
        )


# --- Nạp lặp lại được: upsert theo khoá nghiệp vụ (0005) ---------------------
#
# Trước 0005, nạp lại một dòng đã có làm vỡ CẢ LÔ: `sa.insert()` trần đụng
# `uq_sales_area_date_external_id` → IntegrityError → rollback tất cả. Cụm test
# dưới đây chốt hành vi mới: trùng thì bỏ qua hoặc ghi đè theo phiên bản, dữ liệu
# hợp lệ trong cùng lô không bị kéo theo.

SALES_COLUMNS_TS = SALES_COLUMNS[:-1] + ["source_updated_at", "source_row_hash"]
INVENTORY_COLUMNS_TS = INVENTORY_COLUMNS[:-1] + ["source_updated_at", "source_row_hash"]

T1 = "2026-02-01T10:00:00+07:00"
T2 = "2026-02-02T10:00:00+07:00"  # mới hơn T1


async def _sales_rows(session_factory) -> list[dict]:
    """Mọi dòng sales, sắp theo mã bản ghi — để so cả giá trị chứ không chỉ đếm."""
    async with session_factory() as session:
        rows = await session.execute(
            sa.select(
                sales_records.c.external_record_id,
                sales_records.c.units_sold,
                sales_records.c.sold_date,
                sales_records.c.source_updated_at,
                sales_records.c.file_id,
                sales_records.c.id,
                sales_records.c.created_at,
            ).order_by(sales_records.c.external_record_id)
        )
        return [dict(r) for r in rows.mappings().all()]


async def test_replaying_the_same_file_adds_no_rows(session_factory, seeded_areas, tmp_path):
    """Nạp lại y hệt = không-làm-gì. Không dòng mới, không lỗi, không ghi đè."""
    rows = [
        [2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"],
        [3, "A1", "2PN", "2026-01-06", 4, "TX-2", "h2"],
    ]

    first = await _load(session_factory, _staging(tmp_path, SALES_COLUMNS, rows, name="a.csv"))
    before = await _sales_rows(session_factory)

    second = await _load(session_factory, _staging(tmp_path, SALES_COLUMNS, rows, name="b.csv"))
    after = await _sales_rows(session_factory)

    assert (first.rows_inserted, first.rows_failed) == (2, 0)
    # rows_ok đếm dòng ĐƯỢC NHẬN, không phải dòng mới trong bảng.
    assert (second.rows_inserted, second.rows_failed) == (2, 0)
    assert second.status == "completed"
    assert await _count(session_factory, sales_records) == 2
    assert after == before, "nạp lại đã đổi dữ liệu dù không có phiên bản mới hơn"
    assert await _count(session_factory, upload_errors) == 0


async def test_overlapping_batches_do_not_duplicate(session_factory, seeded_areas, tmp_path):
    """Hai lô chồng lấn: phần chung bỏ qua, phần mới vẫn vào."""
    first = _staging(
        tmp_path,
        SALES_COLUMNS,
        [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", "h1"], [3, "A1", "2PN", "2026-01-06", 4, "TX-2", "h2"]],
        name="first.csv",
    )
    overlapping = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-06", 4, "TX-2", "h2"],  # đã có
            [3, "A1", "2PN", "2026-01-07", 5, "TX-3", "h3"],  # mới
        ],
        name="second.csv",
    )

    await _load(session_factory, first)
    result = await _load(session_factory, overlapping)

    rows = await _sales_rows(session_factory)
    assert [r["external_record_id"] for r in rows] == ["TX-1", "TX-2", "TX-3"]
    assert result.status == "completed"
    assert result.rows_failed == 0
    assert await _count(session_factory, upload_errors) == 0


async def test_duplicate_key_inside_a_file_does_not_roll_back_the_batch(session_factory, seeded_areas, tmp_path):
    """Hai dòng cùng khoá nghiệp vụ trong MỘT file: dòng sau thành lỗi theo dòng.

    Đây là ca mà Postgres không cho phép — một lệnh ON CONFLICT DO UPDATE không
    được chạm hai lần vào cùng một dòng. Chặn trước khi xuống DB, giữ dòng đầu.
    """
    staging = _staging(
        tmp_path,
        SALES_COLUMNS,
        [
            [2, "A1", "2PN", "2026-01-05", 3, "TX-DUP", "h1"],
            [3, "A1", "2PN", "2026-01-05", 9, "TX-DUP", "h2"],  # cùng khoá, khác nội dung
            [4, "A1", "2PN", "2026-01-07", 5, "TX-OK", "h3"],
        ],
    )

    result = await _load(session_factory, staging)

    rows = await _sales_rows(session_factory)
    assert [r["external_record_id"] for r in rows] == ["TX-DUP", "TX-OK"]
    assert [r["units_sold"] for r in rows] == [3, 5], "phải giữ dòng ĐẦU của khoá trùng"
    assert result.rows_inserted == 2
    assert result.rows_failed == 1
    assert result.status == "completed", "một dòng trùng không được làm hỏng cả lô"

    async with session_factory() as session:
        errors = (
            (await session.execute(sa.select(upload_errors.c.row_number, upload_errors.c.error_code))).mappings().all()
        )
    assert [(e["row_number"], e["error_code"]) for e in errors] == [(3, "DUPLICATE_KEY")]


async def test_newer_source_updated_at_overwrites(session_factory, seeded_areas, tmp_path):
    """Bản đến mới hơn → ghi đè giá trị, giữ nguyên id và created_at."""
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", T1, "h1"]], name="v1.csv"),
    )
    before = (await _sales_rows(session_factory))[0]

    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 9, "TX-1", T2, "h2"]], name="v2.csv"),
    )
    after = (await _sales_rows(session_factory))[0]

    assert await _count(session_factory, sales_records) == 1
    assert after["units_sold"] == 9
    assert after["source_updated_at"] == datetime.fromisoformat(T2)
    assert after["id"] == before["id"], "upsert không được thay danh tính bản ghi"
    assert after["created_at"] == before["created_at"], "created_at là lúc xuất hiện lần đầu"
    assert after["file_id"] != before["file_id"], "lineage phải trỏ vào lô vừa chạm tới"


async def test_older_source_updated_at_is_ignored(session_factory, seeded_areas, tmp_path):
    """Nạp lô cũ sau lô mới → dữ liệu mới được giữ nguyên."""
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 9, "TX-1", T2, "h2"]], name="new.csv"),
    )
    result = await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", T1, "h1"]], name="old.csv"),
    )
    row = (await _sales_rows(session_factory))[0]

    assert row["units_sold"] == 9, "lô cũ đã ghi đè lên dữ liệu mới"
    assert row["source_updated_at"] == datetime.fromisoformat(T2)
    assert result.rows_inserted == 1, "dòng vẫn được NHẬN, chỉ là cố ý không áp dụng"
    assert result.rows_failed == 0
    assert result.status == "completed"


async def test_equal_source_updated_at_does_not_overwrite(session_factory, seeded_areas, tmp_path):
    """Mốc bằng nhau → không ghi đè (điều kiện là `>`, không phải `>=`)."""
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 3, "TX-1", T1, "h1"]], name="a.csv"),
    )
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 99, "TX-1", T1, "h2"]], name="b.csv"),
    )

    row = (await _sales_rows(session_factory))[0]
    assert row["units_sold"] == 3
    assert await _count(session_factory, sales_records) == 1


async def test_null_source_updated_at_never_overwrites(session_factory, seeded_areas, tmp_path):
    """File không có cột phiên bản không được ghi đè bản ghi đã có mốc."""
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 9, "TX-1", T2, "h2"]], name="v.csv"),
    )
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 1, "TX-1", "h1"]], name="novers.csv"),
    )

    row = (await _sales_rows(session_factory))[0]
    assert row["units_sold"] == 9, "bản không có phiên bản đã ghi đè bản có phiên bản"
    assert row["source_updated_at"] == datetime.fromisoformat(T2)


async def test_timestamped_row_supersedes_an_untimed_row(session_factory, seeded_areas, tmp_path):
    """Dòng đang có KHÔNG mốc, bản đến CÓ mốc → ghi đè.

    Dòng cũ không mang phiên bản nào để so; một bản ghi có phiên bản là thông tin
    tốt hơn hẳn. Đây là chiều duy nhất mà NULL bị thay.
    """
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS, [[2, "A1", "2PN", "2026-01-05", 1, "TX-1", "h1"]], name="legacy.csv"),
    )
    await _load(
        session_factory,
        _staging(tmp_path, SALES_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 7, "TX-1", T1, "h2"]], name="v.csv"),
    )

    row = (await _sales_rows(session_factory))[0]
    assert row["units_sold"] == 7
    assert row["source_updated_at"] == datetime.fromisoformat(T1)


async def test_inventory_replay_and_versioning(session_factory, seeded_areas, tmp_path):
    """`inventory_snapshots` cũng nằm trong luồng nạp — cùng hợp đồng như sales."""
    base = [2, "A1", "2PN", "2026-01-05", 40, "manual"]

    await _load(
        session_factory,
        _staging(tmp_path, INVENTORY_COLUMNS_TS, [[*base, T1, "h1"]], name="i1.csv"),
        template=INVENTORY_TEMPLATE,
    )
    # Nạp lại y hệt → không thêm dòng.
    await _load(
        session_factory,
        _staging(tmp_path, INVENTORY_COLUMNS_TS, [[*base, T1, "h1"]], name="i2.csv"),
        template=INVENTORY_TEMPLATE,
    )
    assert await _count(session_factory, inventory_snapshots) == 1

    # Mốc mới hơn → cập nhật tồn kho tại chỗ.
    await _load(
        session_factory,
        _staging(
            tmp_path, INVENTORY_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 25, "manual", T2, "h2"]], name="i3.csv"
        ),
        template=INVENTORY_TEMPLATE,
    )
    async with session_factory() as session:
        remaining = await session.scalar(sa.select(inventory_snapshots.c.units_remaining))
    assert remaining == 25
    assert await _count(session_factory, inventory_snapshots) == 1

    # Mốc cũ hơn → giữ nguyên.
    await _load(
        session_factory,
        _staging(
            tmp_path, INVENTORY_COLUMNS_TS, [[2, "A1", "2PN", "2026-01-05", 99, "manual", T1, "h3"]], name="i4.csv"
        ),
        template=INVENTORY_TEMPLATE,
    )
    async with session_factory() as session:
        remaining = await session.scalar(sa.select(inventory_snapshots.c.units_remaining))
    assert remaining == 25


async def test_replaying_areas_catalog_changes_nothing(session_factory, tmp_path):
    """`areas` không có phiên bản → trùng thì BỎ QUA, không ghi đè `total_units`.

    total_units là mẫu số của tỷ lệ hấp thụ; để một file cũ lặng lẽ đổi nó là
    cách âm thầm nhất làm sai mọi con số phía sau.
    """
    rows = [[2, "B1", "1PN", 1, "45.5", 80]]
    await _load(session_factory, _staging(tmp_path, AREAS_COLUMNS, rows, name="a1.csv"), template=AREAS_TEMPLATE)

    changed = [[2, "B1", "1PN", 1, "45.5", 999]]
    result = await _load(
        session_factory, _staging(tmp_path, AREAS_COLUMNS, changed, name="a2.csv"), template=AREAS_TEMPLATE
    )

    async with session_factory() as session:
        total = await session.scalar(sa.select(areas.c.total_units).where(areas.c.area_name == "B1"))
    assert total == 80
    assert await _count(session_factory, areas) == 1
    assert result.status == "completed"
