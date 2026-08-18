import csv
import os
import uuid

import pytest

from src.jobs.parse_upload import MAX_REPORTED_ERRORS, run_parse_upload


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


@pytest.fixture
def sales_file(tmp_path):
    return _write_csv(
        tmp_path / "sales.csv",
        [
            ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
            ["A1", "2026-01-05", "3", "TX-1"],
            ["A2", "khong-phai-ngay", "1", "TX-2"],
        ],
    )


def test_job_returns_counts_and_staging_file(sales_file, tmp_path):
    result = run_parse_upload(str(sales_file), "sales", checksum="abc", original_filename="sales.csv")

    assert result["status"] == "done"
    assert (result["rows_total"], result["rows_ok"], result["rows_failed"]) == (2, 1, 1)
    assert result["checksum"] == "abc"
    assert (tmp_path / "sales.staging.csv").exists()


def test_job_reports_row_errors_for_the_errors_endpoint(sales_file):
    result = run_parse_upload(str(sales_file), "sales")

    assert result["errors"][0]["row_number"] == 3
    assert result["errors"][0]["error_code"] == "INVALID_DATE"
    assert result["errors"][0].keys() == {"row_number", "column_name", "error_code", "message"}


def test_missing_column_is_reported_as_row_errors_not_file_failure(tmp_path):
    """Thiếu cột bắt buộc: job vẫn chạy xong, lỗi đi vào danh sách upload_errors."""
    bad_file = _write_csv(tmp_path / "bad.csv", [["Phân khu", "Ngày bán"], ["A1", "2026-01-05"]])
    result = run_parse_upload(str(bad_file), "sales")

    assert result["status"] == "done"
    assert (result["rows_ok"], result["rows_failed"]) == (0, 1)
    assert {e["error_code"] for e in result["errors"]} == {"MISSING_COLUMN"}
    assert {e["column_name"] for e in result["errors"]} == {"units_sold", "external_record_id"}


def test_structurally_unreadable_file_fails_the_whole_job(tmp_path):
    """Chỉ lỗi cấu trúc mới giết cả file — ở đây là định dạng không hỗ trợ."""
    bad_file = tmp_path / "bao-cao.pdf"
    bad_file.write_bytes(b"%PDF-1.4")
    result = run_parse_upload(str(bad_file), "sales")

    assert result["status"] == "failed"
    assert result["error_code"] == "UNSUPPORTED_FORMAT"


def test_empty_file_fails_the_whole_job(tmp_path):
    empty_file = _write_csv(tmp_path / "empty.csv", [])
    result = run_parse_upload(str(empty_file), "sales")

    assert result["status"] == "failed"
    assert result["error_code"] == "EMPTY_FILE"


def test_unknown_template_returns_failed(sales_file):
    result = run_parse_upload(str(sales_file), "khong-ton-tai")

    assert result["status"] == "failed"
    assert result["error_code"] == "UNKNOWN_TEMPLATE"


def test_missing_file_returns_failed_without_leaking_path(tmp_path):
    """Volume uploads chưa gắn chung giữa api/worker → phải báo lỗi sạch, không crash."""
    missing_file = tmp_path / "khong-co.csv"
    result = run_parse_upload(str(missing_file), "sales")

    assert result["status"] == "failed"
    assert result["error_code"] == "FILE_UNREADABLE"
    assert "khong-co.csv" not in result["message"]


def test_undecodable_csv_fails_cleanly_and_leaves_no_staging(tmp_path):
    """UnicodeDecodeError là ValueError, không phải OSError: nếu không bọc ở parser
    thì nó lọt qua mọi handler, thoát ra ngoài job và bỏ lại file staging dở dang.
    """
    bad_file = tmp_path / "utf16.csv"
    bad_file.write_bytes("Phân khu,Ngày bán,Số căn bán,Mã bản ghi\r\nA1,2026-01-05,3,TX-1\r\n".encode("utf-16-le"))

    result = run_parse_upload(str(bad_file), "sales")

    assert result["status"] == "failed"
    assert result["error_code"] == "UNSUPPORTED_ENCODING"
    assert not (tmp_path / "utf16.staging.csv").exists()


def test_utf16_csv_with_bom_parses_through_the_job(tmp_path):
    good_file = tmp_path / "utf16bom.csv"
    good_file.write_bytes("Phân khu,Ngày bán,Số căn bán,Mã bản ghi\r\nA1,2026-01-05,3,TX-1\r\n".encode("utf-16"))

    result = run_parse_upload(str(good_file), "sales")

    assert result["status"] == "done"
    assert (result["rows_ok"], result["rows_failed"]) == (1, 0)


def test_inventory_job_covers_aliases_and_constraints(tmp_path):
    inventory_file = _write_csv(
        tmp_path / "inventory.csv",
        [
            ["Phân khu", "Ngày chốt", "Tồn kho", "Loại chốt"],
            ["A1", "2026-01-05", "10", "manual"],
            ["A2", "2026-01-06", "-1", "manual"],
            ["A3", "2026-01-07", "5", "invalid"],
        ],
    )

    result = run_parse_upload(str(inventory_file), "inventory")

    assert result["status"] == "done"
    assert (result["rows_total"], result["rows_ok"], result["rows_failed"]) == (3, 1, 2)
    assert {e["error_code"] for e in result["errors"]} == {"VALUE_OUT_OF_RANGE", "INVALID_CHOICE"}
    assert any(e["row_number"] == 3 and e["column_name"] == "units_remaining" for e in result["errors"])
    assert any(e["row_number"] == 4 and e["column_name"] == "snapshot_type" for e in result["errors"])


def test_areas_job_ignores_extra_columns_and_writes_staging(tmp_path):
    areas_file = _write_csv(
        tmp_path / "areas.csv",
        [
            ["Phân khu", "Loại căn hộ", "Số phòng ngủ", "Diện tích", "Tổng số căn", "Ghi chú"],
            ["A1", "Studio", "1", "75.5", "10", "ignored"],
            ["A2", "2PN", "0", "0", "8", "ignored"],
        ],
    )

    result = run_parse_upload(str(areas_file), "areas")

    assert result["status"] == "done"
    assert (result["rows_total"], result["rows_ok"], result["rows_failed"]) == (2, 1, 1)
    assert any(e["column_name"] == "area_sqm" and e["error_code"] == "VALUE_OUT_OF_RANGE" for e in result["errors"])

    with open(result["staging_path"], encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["row_number", "area_name", "unit_type", "bedrooms", "area_sqm", "total_units"]
    assert len(rows) == 2
    assert rows[1][0] == "2"  # dòng 2 trên file gốc
    assert rows[1][1] == "A1"
    assert rows[1][2] == "Studio"
    assert "ignored" not in ",".join(rows[1])


def test_job_truncates_errors_when_too_many(tmp_path):
    rows = [["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"]]
    for i in range(MAX_REPORTED_ERRORS + 20):
        rows.append(["A1", "bad-date", "bad-int", f"TX-{i}"])

    bad_file = _write_csv(tmp_path / "many_errors.csv", rows)
    result = run_parse_upload(str(bad_file), "sales")

    assert result["status"] == "done"
    assert result["rows_total"] == MAX_REPORTED_ERRORS + 20
    assert result["rows_ok"] == 0
    assert result["rows_failed"] == MAX_REPORTED_ERRORS + 20
    assert len(result["errors"]) == MAX_REPORTED_ERRORS
    assert result["errors_truncated"] is True


def test_blank_rows_are_skipped(tmp_path):
    blank_rows_file = _write_csv(
        tmp_path / "blank_rows.csv",
        [
            ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
            ["A1", "2026-01-05", "3", "TX-1"],
            [],
            ["   ", "", "", ""],
            ["A2", "2026-01-06", "1", "TX-2"],
        ],
    )

    result = run_parse_upload(str(blank_rows_file), "sales")

    assert result["status"] == "done"
    assert (result["rows_total"], result["rows_ok"], result["rows_failed"]) == (2, 2, 0)
    assert result["errors"] == []


# --- Nạp DB (cần Postgres thật; xem tests/test_services/test_import_records.py) ---

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
PROJECT_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_job_without_project_id_parses_but_does_not_persist(sales_file):
    """Đường parse-only: không có project_id thì không chạm DB."""
    result = run_parse_upload(str(sales_file), "sales")

    assert result["persisted"] is False
    assert result["file_id"] is None
    assert result["rows_ok"] == 1  # số của parser


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_job_counts_add_up_when_persisted(tmp_path, monkeypatch):
    """rows_ok + rows_failed phải bằng rows_total — không được trộn số của
    parser với số của tầng ghi.
    """
    import src.db as db_module

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    db_module.get_engine.cache_clear()
    db_module.get_session_factory.cache_clear()
    from src.config import get_settings

    get_settings.cache_clear()

    file_id = _seed_project_and_area()

    upload = _write_csv(
        tmp_path / "sales.csv",
        [
            ["Phân khu", "Loại căn", "Ngày bán", "Số căn bán", "Mã bản ghi"],
            ["A1", "2PN", "2026-01-05", "3", "TX-1"],
            ["KHONG-CO", "2PN", "2026-01-06", "4", "TX-2"],
        ],
    )
    result = run_parse_upload(str(upload), "sales", file_id=file_id, project_id=PROJECT_ID, original_filename="s.csv")

    assert result["persisted"] is True
    assert result["rows_inserted"] == 1
    assert result["rows_ok"] + result["rows_failed"] == result["rows_total"]

    db_module.get_engine.cache_clear()
    db_module.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _seed_project_and_area(checksum="chk-seed"):
    """Dựng project + area + upload_files tối thiểu để job có chỗ mà tra khoá ngoại.

    `upload_files` do tầng API tạo trong đời thật; ở đây test đóng vai API.
    Trả về file_id để truyền xuống job.
    """
    import asyncio
    from datetime import UTC, date, datetime

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from src.models.tables import (  # noqa: F401
        absorption_daily,
        areas,
        sales_records,
        upload_errors,
        upload_files,
    )

    async def seed():
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        factory = async_sessionmaker(engine)
        async with factory() as session:
            async with session.begin():
                for table in (upload_errors, absorption_daily, sales_records, areas, upload_files):
                    await session.execute(sa.delete(table))
                await session.execute(sa.text("DELETE FROM projects"))
                await session.execute(
                    sa.text("INSERT INTO projects (id,name,launch_date,created_at) VALUES (:i,'P',:d,:t)"),
                    {"i": uuid.UUID(PROJECT_ID), "d": date(2026, 1, 1), "t": datetime.now(UTC)},
                )
                await session.execute(
                    sa.insert(areas),
                    [
                        {
                            "id": uuid.uuid4(),
                            "project_id": uuid.UUID(PROJECT_ID),
                            "area_name": "A1",
                            "unit_type": "2PN",
                            "bedrooms": 2,
                            "area_sqm": 75,
                            "total_units": 100,
                            "created_at": datetime.now(UTC),
                        }
                    ],
                )
                await session.execute(
                    sa.insert(upload_files).values(
                        id=file_id,
                        project_id=uuid.UUID(PROJECT_ID),
                        uploaded_by=None,
                        filename="s.csv",
                        checksum=checksum,
                        status="pending",
                        rows_ok=0,
                        rows_failed=0,
                        uploaded_at=datetime.now(UTC),
                    )
                )
        await engine.dispose()

    file_id = uuid.uuid4()
    asyncio.run(seed())
    return str(file_id)


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_successful_import_deletes_upload_and_staging(tmp_path, monkeypatch):
    """Nạp xong thì xoá cả file gốc lẫn staging — volume uploads không phình mãi."""
    import src.db as db_module

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    db_module.get_engine.cache_clear()
    db_module.get_session_factory.cache_clear()
    from src.config import get_settings

    get_settings.cache_clear()
    file_id = _seed_project_and_area()

    upload = _write_csv(
        tmp_path / "sales.csv",
        [
            ["Phân khu", "Loại căn", "Ngày bán", "Số căn bán", "Mã bản ghi"],
            ["A1", "2PN", "2026-01-05", "3", "TX-1"],
        ],
    )
    staging = tmp_path / "sales.staging.csv"

    result = run_parse_upload(str(upload), "sales", file_id=file_id, project_id=PROJECT_ID, original_filename="s.csv")

    assert result["rows_inserted"] == 1
    assert not upload.exists(), "file gốc phải bị xoá sau khi nạp thành công"
    assert not staging.exists(), "staging phải bị xoá sau khi nạp thành công"

    db_module.get_engine.cache_clear()
    db_module.get_session_factory.cache_clear()
    get_settings.cache_clear()


def test_parse_only_run_keeps_files_for_inspection(sales_file, tmp_path):
    """Không có project_id (chưa ghi DB) thì GIỮ file lại để còn soi được."""
    run_parse_upload(str(sales_file), "sales")

    assert sales_file.exists()
    assert (tmp_path / "sales.staging.csv").exists()


# --- Lô hỏng phải kết thúc ở 'failed', không treo ở 'pending' (0005) ---------
#
# Lỗi production: `ImportService.load()` mở transaction bằng `session.begin()`,
# và lệnh đặt status='processing' nằm TRONG transaction đó. IntegrityError không
# được bắt ở job → transaction rollback → bản ghi quay về 'pending' → treo vĩnh
# viễn, người dùng poll `/status` không bao giờ thấy kết thúc.


def _db():
    """Session factory trỏ vào DB test, dùng cho phần assert của các test dưới."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return async_sessionmaker(create_async_engine(TEST_DATABASE_URL, poolclass=NullPool))


def _read_file_row(file_id):
    """Đọc trạng thái lô + các lỗi kèm theo, sau khi job đã chạy xong."""
    import asyncio

    import sqlalchemy as sa

    from src.models.tables import upload_errors, upload_files

    async def read():
        factory = _db()
        async with factory() as session:
            row = (
                (await session.execute(sa.select(upload_files).where(upload_files.c.id == uuid.UUID(file_id))))
                .mappings()
                .one()
            )
            errors = (
                (await session.execute(sa.select(upload_errors).where(upload_errors.c.file_id == uuid.UUID(file_id))))
                .mappings()
                .all()
            )
            return dict(row), [dict(e) for e in errors]

    return asyncio.run(read())


def _plant_hash_collision(area_name="A1", unit_type="2PN"):
    """Cấy sẵn một dòng sales chiếm trước `source_row_hash` mà file sắp nạp sẽ sinh ra.

    Khoá nghiệp vụ KHÁC nhau nên `ON CONFLICT` (nhắm uq_sales_area_date_external_id)
    không nuốt được; đụng `uq_sales_area_source_row_hash` → IntegrityError thật,
    đúng đường mà production sẽ gặp. Không mock gì.
    """
    import asyncio
    from datetime import UTC, date, datetime

    import sqlalchemy as sa

    from src.models.tables import areas, sales_records, upload_files
    from src.services.excel_parser import row_hash

    # Đúng thứ tự field của SALES_TEMPLATE, gồm cả source_updated_at (None).
    clashing = row_hash("sales", [area_name, unit_type, date(2026, 1, 5), 3, "TX-1", None])

    async def plant():
        factory = _db()
        async with factory() as session:
            async with session.begin():
                area_id = await session.scalar(sa.select(areas.c.id).where(areas.c.area_name == area_name))
                # `sales_records.file_id` là NOT NULL; dùng chính lô vừa seed.
                batch_id = await session.scalar(sa.select(upload_files.c.id).limit(1))
                await session.execute(
                    sa.insert(sales_records).values(
                        id=uuid.uuid4(),
                        area_id=area_id,
                        file_id=batch_id,
                        sold_date=date(2026, 1, 9),
                        units_sold=1,
                        external_record_id="TX-PLANTED",
                        source_row_hash=clashing,
                        created_at=datetime.now(UTC),
                    )
                )

    asyncio.run(plant())


@pytest.fixture
def db_env(monkeypatch):
    """Trỏ engine của ứng dụng vào DB test rồi trả lại nguyên trạng sau test."""
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()
    yield
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


def _sales_upload(tmp_path, name="sales.csv"):
    return _write_csv(
        tmp_path / name,
        [
            ["Phân khu", "Loại căn", "Ngày bán", "Số căn bán", "Mã bản ghi"],
            ["A1", "2PN", "2026-01-05", "3", "TX-1"],
        ],
    )


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_constraint_violation_leaves_the_batch_failed_not_pending(tmp_path, db_env):
    """Ràng buộc DB chặn cả lô → lô phải kết thúc ở 'failed'."""
    import sqlalchemy as sa

    from src.models.tables import sales_records

    file_id = _seed_project_and_area()
    _plant_hash_collision()

    result = run_parse_upload(
        str(_sales_upload(tmp_path)), "sales", file_id=file_id, project_id=PROJECT_ID, original_filename="s.csv"
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "CONSTRAINT_VIOLATION"

    row, errors = _read_file_row(file_id)
    assert row["status"] == "failed", "lô treo ở 'pending' — đúng lỗi production đang sửa"
    assert row["status"] != "completed"
    assert [e["error_code"] for e in errors] == ["CONSTRAINT_VIOLATION"]
    assert errors[0]["column_name"] is None  # quy ước "lỗi cả file"

    # Không nạp một nửa: chỉ còn đúng dòng đã cấy sẵn.
    import asyncio

    async def count():
        factory = _db()
        async with factory() as session:
            return await session.scalar(sa.select(sa.func.count()).select_from(sales_records))

    assert asyncio.run(count()) == 1


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_failure_message_leaks_no_sql_or_row_values(tmp_path, db_env):
    """Thông báo lỗi chỉ được nêu TÊN ràng buộc, không kèm SQL hay dữ liệu người dùng."""
    file_id = _seed_project_and_area()
    _plant_hash_collision()

    result = run_parse_upload(
        str(_sales_upload(tmp_path)), "sales", file_id=file_id, project_id=PROJECT_ID, original_filename="s.csv"
    )

    _, errors = _read_file_row(file_id)
    message = result["message"] + " " + errors[0]["message"]

    assert "uq_sales_area_source_row_hash" in message  # đủ để người vận hành lần ra
    for leaked in ("INSERT INTO", "SELECT", "$1", "TX-1", "TX-PLANTED", "asyncpg", "Traceback"):
        assert leaked not in message, f"thông báo lỗi để lọt '{leaked}'"


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_failed_batch_can_be_retried_after_the_cause_is_removed(tmp_path, db_env):
    """Chạy lại đúng lô đó sau khi gỡ nguyên nhân → 'completed', không nhân đôi dữ liệu."""
    import asyncio

    import sqlalchemy as sa

    from src.models.tables import sales_records

    file_id = _seed_project_and_area()
    _plant_hash_collision()
    upload = _sales_upload(tmp_path)

    first = run_parse_upload(str(upload), "sales", file_id=file_id, project_id=PROJECT_ID, original_filename="s.csv")
    assert first["status"] == "failed"
    assert upload.exists(), "lô hỏng phải giữ lại file gốc để còn chạy lại"

    async def remove_cause():
        factory = _db()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    sa.delete(sales_records).where(sales_records.c.external_record_id == "TX-PLANTED")
                )

    asyncio.run(remove_cause())

    second = run_parse_upload(str(upload), "sales", file_id=file_id, project_id=PROJECT_ID, original_filename="s.csv")

    assert second["status"] == "done"
    assert second["rows_inserted"] == 1

    row, errors = _read_file_row(file_id)
    assert row["status"] == "completed"
    assert row["rows_ok"] == 1
    assert row["rows_failed"] == 0
    assert errors == [], "lỗi của lần chạy hỏng trước phải được dọn, không chồng lên lần mới"


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_replaying_a_successful_upload_through_the_job_adds_no_rows(tmp_path, db_env):
    """Chạy lại nguyên đường job với cùng dữ liệu → không thêm dòng nào."""
    import asyncio

    import sqlalchemy as sa

    from src.models.tables import sales_records

    file_id = _seed_project_and_area()

    first = run_parse_upload(
        str(_sales_upload(tmp_path, "a.csv")),
        "sales",
        file_id=file_id,
        project_id=PROJECT_ID,
        original_filename="a.csv",
    )
    assert first["status"] == "done"

    # Lô thứ hai = lần upload mới của cùng nội dung (API tạo bản ghi khác).
    second_file_id = _create_second_batch(file_id)
    second = run_parse_upload(
        str(_sales_upload(tmp_path, "b.csv")),
        "sales",
        file_id=second_file_id,
        project_id=PROJECT_ID,
        original_filename="b.csv",
    )

    assert second["status"] == "done"

    async def rows():
        factory = _db()
        async with factory() as session:
            return (
                (await session.execute(sa.select(sales_records.c.external_record_id, sales_records.c.units_sold)))
                .mappings()
                .all()
            )

    assert [dict(r) for r in asyncio.run(rows())] == [{"external_record_id": "TX-1", "units_sold": 3}]

    row, _ = _read_file_row(second_file_id)
    assert row["status"] == "completed"


def _create_second_batch(existing_file_id):
    """Thêm một bản ghi `upload_files` nữa cho cùng dự án — API vẫn làm vậy mỗi lần upload."""
    import asyncio
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from src.models.tables import upload_files

    new_id = uuid.uuid4()

    async def create():
        factory = _db()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    sa.insert(upload_files).values(
                        id=new_id,
                        project_id=uuid.UUID(PROJECT_ID),
                        uploaded_by=None,
                        filename="b.csv",
                        checksum="chk-seed",  # CÙNG checksum: 0005 không còn chặn
                        status="pending",
                        rows_ok=0,
                        rows_failed=0,
                        uploaded_at=datetime.now(UTC),
                    )
                )

    asyncio.run(create())
    return str(new_id)
