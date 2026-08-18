import csv
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from src.services.excel_parser import (
    AREAS_TEMPLATE,
    INVENTORY_TEMPLATE,
    SALES_TEMPLATE,
    TEMPLATES,
    ExcelParseError,
    ExcelParserService,
    _sniff_encoding,
    detect_format,
    row_hash,
)

SALES_ROWS = [
    ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
    ["A1", date(2026, 1, 5), 3, "TX-001"],
    ["A2", date(2026, 1, 6), 0, "TX-002"],
]


@pytest.fixture
def parser():
    return ExcelParserService()


def _write_xlsx(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow([v.isoformat() if isinstance(v, date) else v for v in row])
    return path


def test_detect_format(tmp_path):
    assert detect_format(tmp_path / "a.xlsx") == "calamine"
    assert detect_format(tmp_path / "a.xlsb") == "calamine"
    assert detect_format(tmp_path / "a.csv") == "csv"
    with pytest.raises(ExcelParseError) as exc:
        detect_format(tmp_path / "a.pdf")
    assert exc.value.error_code == "UNSUPPORTED_FORMAT"


def test_parse_xlsx_maps_vietnamese_headers(parser, tmp_path):
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", SALES_ROWS), SALES_TEMPLATE)

    assert (result.rows_ok, result.rows_failed) == (2, 0)
    assert result.records[0]["area_name"] == "A1"
    assert result.records[0]["sold_date"] == date(2026, 1, 5)
    assert result.records[0]["units_sold"] == 3
    assert result.records[0]["external_record_id"] == "TX-001"
    assert result.records[0]["source_row_hash"]


def test_csv_and_xlsx_produce_identical_records(parser, tmp_path):
    """Cùng dữ liệu, hai engine khác nhau phải ra cùng bản ghi — kể cả row hash."""
    from_xlsx = parser.parse(_write_xlsx(tmp_path / "s.xlsx", SALES_ROWS), SALES_TEMPLATE)
    from_csv = parser.parse(_write_csv(tmp_path / "s.csv", SALES_ROWS), SALES_TEMPLATE)

    assert from_xlsx.records == from_csv.records


def test_headers_match_without_accents_and_case(parser, tmp_path):
    rows = [["PHAN KHU", "ngay_ban", "So Can Ban", "MA BAN GHI"], ["A1", date(2026, 1, 5), 2, "TX-1"]]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["external_record_id"] == "TX-1"
    assert result.records[0]["unit_type"] is None  # lookup optional vắng mặt


def test_missing_required_column_reports_errors_without_killing_file(parser, tmp_path):
    """Thiếu cột bắt buộc là lỗi dữ liệu → upload_errors, KHÔNG ném lỗi cả file."""
    rows = [["Phân khu", "Ngày bán"], ["A1", date(2026, 1, 5)], ["A2", date(2026, 1, 6)]]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 0
    assert result.rows_total == 2  # vẫn đếm được file có bao nhiêu dòng
    assert result.rows_failed == 2
    assert {e.column_name for e in result.errors} == {"units_sold", "external_record_id"}
    assert all(e.error_code == "MISSING_COLUMN" for e in result.errors)


def test_missing_column_reported_once_not_per_row(parser, tmp_path):
    """1000 dòng thiếu cùng một cột chỉ sinh 1 lỗi, không phải 1000 lỗi giống nhau."""
    rows = [["Phân khu", "Ngày bán", "Mã bản ghi"]] + [["A1", "2026-01-05", "TX"]] * 1000
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_failed == 1000
    assert len(result.errors) == 1
    assert result.errors[0].column_name == "units_sold"
    assert result.errors[0].row_number == 1  # neo vào dòng header


def test_missing_column_reported_even_when_file_has_no_data_rows(parser, tmp_path):
    rows = [["Phân khu", "Ngày bán"]]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_total == 0
    assert {e.error_code for e in result.errors} == {"MISSING_COLUMN"}


def test_empty_file_raises(parser, tmp_path):
    with pytest.raises(ExcelParseError) as exc:
        parser.parse(_write_csv(tmp_path / "s.csv", []), SALES_TEMPLATE)
    assert exc.value.error_code == "EMPTY_FILE"


def test_row_errors_carry_excel_row_number(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
        ["A1", date(2026, 1, 5), 3, "TX-1"],
        ["A2", "khong-phai-ngay", 1, "TX-2"],  # dòng 3 trên Excel
        ["A3", date(2026, 1, 7), 2.5, "TX-3"],  # dòng 4: không phải số nguyên
        [None, date(2026, 1, 8), 1, "TX-4"],  # dòng 5: thiếu phân khu
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert (result.rows_total, result.rows_ok, result.rows_failed) == (4, 1, 3)
    by_row = {e.row_number: e for e in result.errors}
    assert by_row[3].error_code == "INVALID_DATE"
    assert by_row[3].column_name == "sold_date"
    assert by_row[4].error_code == "INVALID_INT"
    assert by_row[5].error_code == "EMPTY_VALUE"


def test_error_maps_to_upload_errors_columns(parser, tmp_path):
    rows = [["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"], ["A1", "xx", 1, "TX-1"]]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.errors[0].as_record("file-uuid").keys() == {
        "file_id",
        "row_number",
        "column_name",
        "error_code",
        "message",
    }


def test_blank_rows_are_skipped(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
        ["A1", date(2026, 1, 5), 3, "TX-1"],
        ["", "", "", ""],
        ["A2", date(2026, 1, 6), 1, "TX-2"],
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert (result.rows_total, result.rows_ok, result.rows_failed) == (2, 2, 0)


def test_excel_float_ints_and_area_codes_are_normalized(parser, tmp_path):
    """Excel lưu số nguyên dưới dạng float — 3.0 phải ra 3, mã phân khu 12.0 ra '12'."""
    rows = [["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"], [12.0, date(2026, 1, 5), 3.0, "TX-1"]]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.records[0]["area_name"] == "12"
    assert result.records[0]["units_sold"] == 3


def test_inventory_template_applies_default_snapshot_type(parser, tmp_path):
    rows = [["Phân khu", "Ngày chốt", "Tồn kho"], ["A1", date(2026, 1, 5), 40]]
    result = parser.parse(_write_xlsx(tmp_path / "i.xlsx", rows), INVENTORY_TEMPLATE)

    assert result.records[0]["units_remaining"] == 40
    assert result.records[0]["snapshot_type"] == "manual"  # khớp CHECK constraint


def test_row_hash_is_stable_and_distinct():
    assert row_hash("sales", ["A1", date(2026, 1, 5), 3]) == row_hash("sales", ["A1", date(2026, 1, 5), 3])
    assert row_hash("sales", ["A1", date(2026, 1, 5), 3]) != row_hash("sales", ["A1", date(2026, 1, 5), 4])
    # Cùng giá trị nhưng khác template thì khác hash.
    assert row_hash("sales", ["A1"]) != row_hash("inventory", ["A1"])


def test_max_errors_is_capped(tmp_path):
    rows = [["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"]] + [["A1", "xx", 1, "TX-1"]] * 50
    result = ExcelParserService(max_errors=10).parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_failed == 50  # vẫn đếm đủ
    assert len(result.errors) == 10  # nhưng không giữ hết trong RAM
    assert result.errors_truncated is True


def test_parse_to_csv_writes_copy_ready_staging(parser, tmp_path):
    staging = tmp_path / "staging" / "sales.csv"
    result = parser.parse_to_csv(_write_xlsx(tmp_path / "s.xlsx", SALES_ROWS), SALES_TEMPLATE, staging)

    assert result.rows_ok == 2
    assert result.records == []  # đường low-memory: không giữ bản ghi

    with staging.open(encoding="utf-8") as handle:
        staged = list(csv.DictReader(handle))
    assert [r["area_name"] for r in staged] == ["A1", "A2"]
    assert staged[0]["sold_date"] == "2026-01-05"
    assert staged[0]["source_row_hash"]


def test_iter_records_streams_without_materializing(parser, tmp_path):
    stream = parser.iter_records(_write_xlsx(tmp_path / "s.xlsx", SALES_ROWS), SALES_TEMPLATE)
    row_number, record, errors = next(stream)

    assert (row_number, errors) == (2, [])  # header là dòng 1
    assert record["area_name"] == "A1"


# --- Neo vào 0001_initial_schema -------------------------------------------
# Các test dưới đây tồn tại để migration đổi mà parser không đổi thì đỏ ngay.


def test_external_record_id_is_required(parser, tmp_path):
    """sales_records.external_record_id là NOT NULL + CHECK <> '' → file phải có."""
    rows = [["Phân khu", "Ngày bán", "Số căn bán"], ["A1", date(2026, 1, 5), 3]]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 0
    assert result.errors[0].column_name == "external_record_id"
    assert result.errors[0].error_code == "MISSING_COLUMN"


def test_blank_external_record_id_is_row_error(parser, tmp_path):
    """Ô trống sẽ vi phạm CHECK <> '' — phải chặn theo dòng, không đẩy xuống DB."""
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
        ["A1", date(2026, 1, 5), 3, ""],
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_failed == 1
    assert result.errors[0].column_name == "external_record_id"
    assert result.errors[0].error_code == "EMPTY_VALUE"


@pytest.mark.parametrize("units", [-1, -50])
def test_negative_units_sold_is_row_error(parser, tmp_path, units):
    """ck_sales_records_units_sold_nonnegative."""
    rows = [["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"], ["A1", date(2026, 1, 5), units, "TX-1"]]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_failed == 1
    assert result.errors[0].error_code == "VALUE_OUT_OF_RANGE"
    assert result.errors[0].column_name == "units_sold"


def test_snapshot_type_must_match_check_constraint(parser, tmp_path):
    """ck_inventory_snapshots_snapshot_type IN ('opening','closing','manual','derived')."""
    rows = [
        ["Phân khu", "Ngày chốt", "Tồn kho", "Loại chốt"],
        ["A1", date(2026, 1, 5), 10, "opening"],
        ["A2", date(2026, 1, 5), 10, "linh-tinh"],
    ]
    result = parser.parse(_write_csv(tmp_path / "i.csv", rows), INVENTORY_TEMPLATE)

    assert (result.rows_ok, result.rows_failed) == (1, 1)
    assert result.records[0]["snapshot_type"] == "opening"
    assert result.errors[0].error_code == "INVALID_CHOICE"
    assert "opening" in result.errors[0].message


def test_negative_units_remaining_is_row_error(parser, tmp_path):
    """ck_inventory_snapshots_units_remaining_nonnegative."""
    rows = [["Phân khu", "Ngày chốt", "Tồn kho"], ["A1", date(2026, 1, 5), -3]]
    result = parser.parse(_write_csv(tmp_path / "i.csv", rows), INVENTORY_TEMPLATE)

    assert result.errors[0].error_code == "VALUE_OUT_OF_RANGE"


def test_areas_template_parses_apartment_master_data(parser, tmp_path):
    rows = [
        ["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"],
        ["A1", "2PN-A", 2, "75.5", 120],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "a.xlsx", rows), AREAS_TEMPLATE)

    record = result.records[0]
    assert record["area_name"] == "A1"
    assert record["unit_type"] == "2PN-A"
    assert record["bedrooms"] == 2
    assert record["total_units"] == 120
    assert record["area_sqm"] == Decimal("75.5")
    assert isinstance(record["area_sqm"], Decimal)  # cột sa.Numeric, không dùng float


def test_areas_records_have_no_source_row_hash(parser, tmp_path):
    """Bảng `areas` không có cột source_row_hash — không được bịa ra."""
    rows = [["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"], ["A1", "2PN", 2, 75, 10]]
    result = parser.parse(_write_xlsx(tmp_path / "a.xlsx", rows), AREAS_TEMPLATE)

    assert "source_row_hash" not in result.records[0]


def test_area_sqm_must_be_strictly_positive(parser, tmp_path):
    """ck_areas_area_sqm_positive dùng '>' chứ không phải '>=' — 0 phải bị chặn."""
    rows = [["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"], ["A1", "2PN", 2, 0, 10]]
    result = parser.parse(_write_csv(tmp_path / "a.csv", rows), AREAS_TEMPLATE)

    assert result.errors[0].error_code == "VALUE_OUT_OF_RANGE"
    assert result.errors[0].column_name == "area_sqm"


def test_decimal_accepts_vietnamese_comma(parser, tmp_path):
    rows = [["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"], ["A1", "2PN", 2, "75,5", 10]]
    result = parser.parse(_write_csv(tmp_path / "a.csv", rows), AREAS_TEMPLATE)

    assert result.records[0]["area_sqm"] == Decimal("75.5")


def test_templates_declare_their_target_table():
    assert SALES_TEMPLATE.target_table == "sales_records"
    assert INVENTORY_TEMPLATE.target_table == "inventory_snapshots"
    assert AREAS_TEMPLATE.target_table == "areas"


def test_lookup_fields_are_separated_from_real_columns():
    """area_name/unit_type KHÔNG phải cột của sales_records — chúng tra ra area_id."""
    assert SALES_TEMPLATE.lookup_names == ("area_name", "unit_type")
    assert SALES_TEMPLATE.column_names == ("sold_date", "units_sold", "external_record_id", "source_updated_at")
    # areas thì mọi field đều là cột thật.
    assert AREAS_TEMPLATE.lookup_names == ()


def test_template_columns_exist_in_alembic_schema():
    """Chốt chặn cuối: mọi column_name phải có thật trong migration.

    Quét TẤT CẢ revision, không chỉ 0001: cột thêm về sau bằng `op.add_column`
    (ví dụ `source_updated_at` ở 0005) cũng là cột hợp lệ của bảng đích.
    """
    revisions = sorted(Path("alembic/versions").glob("[0-9]*.py"))
    assert revisions, "không tìm thấy migration nào"
    sources = [path.read_text(encoding="utf-8") for path in revisions]

    for template in TEMPLATES.values():
        declared: set[str] = set()
        for migration in sources:
            created = re.search(rf'op\.create_table\(\s*"{template.target_table}",(.*?)\n    \)\n', migration, re.S)
            if created:
                declared |= set(re.findall(r'sa\.Column\("(\w+)"', created.group(1)))
            declared |= set(
                re.findall(rf'op\.add_column\(\s*"{template.target_table}",\s*sa\.Column\("(\w+)"', migration)
            )
            # 0005 thêm cột theo vòng lặp trên một hằng số danh sách bảng.
            for name in re.findall(r'op\.add_column\(table, sa\.Column\("(\w+)"', migration):
                tables = re.search(r"^VERSIONED_TABLES = \((.*?)\)", migration, re.M)
                if tables and f'"{template.target_table}"' in tables.group(1):
                    declared.add(name)

        for column in template.column_names:
            assert column in declared, f"{template.name}: '{column}' không có trong {template.target_table}"
        if template.emits_row_hash:
            assert "source_row_hash" in declared


# --- Whitelist: bản ghi chỉ chứa cột DB cần ---------------------------------


def test_extra_columns_in_file_are_dropped(parser, tmp_path):
    """File thật của sale luôn có cột thừa — không cột nào được lọt vào bản ghi."""
    rows = [
        [
            "Phân khu",
            "Ghi chú",  # cột thừa
            "Ngày bán",
            "Người bán",  # cột thừa
            "Số căn bán",
            "Hoa hồng",  # cột thừa
            "Mã bản ghi",
            "Cột nội bộ",  # cột thừa
        ],
        ["A1", "khách VIP", date(2026, 1, 5), "Nguyễn A", 3, 15000000, "TX-1", "xyz"],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert set(result.records[0]) == {
        "area_name",
        "unit_type",
        "sold_date",
        "units_sold",
        "external_record_id",
        "source_updated_at",
        "source_row_hash",
    }
    # Không giá trị nào của cột thừa bị mang theo.
    values = str(result.records[0].values())
    for leaked in ("khách VIP", "Nguyễn A", "15000000", "xyz"):
        assert leaked not in values


def test_record_keys_are_exactly_the_template_contract(parser, tmp_path):
    """Khoá của bản ghi = field template (+ source_row_hash). Không dư, không thiếu."""
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", SALES_ROWS), SALES_TEMPLATE)
    expected = set(SALES_TEMPLATE.field_names) | {"source_row_hash"}

    assert set(result.records[0]) == expected
    assert set(SALES_TEMPLATE.column_names) <= expected  # đủ cột của bảng đích


def test_staging_csv_carries_no_extra_columns(parser, tmp_path):
    rows = [
        ["Phân khu", "Ghi chú", "Ngày bán", "Số căn bán", "Mã bản ghi"],
        ["A1", "nội bộ", date(2026, 1, 5), 3, "TX-1"],
    ]
    staging = tmp_path / "out.csv"
    parser.parse_to_csv(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE, staging)

    with staging.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert "Ghi chú" not in reader.fieldnames
        assert "nội bộ" not in str(next(reader).values())


def test_duplicate_header_keeps_first_and_ignores_rest(parser, tmp_path):
    """Excel export hay lặp tên cột; lấy cột đầu, không để cột sau ghi đè."""
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi", "Số căn bán"],
        ["A1", date(2026, 1, 5), 3, "TX-1", 999],
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.records[0]["units_sold"] == 3


def test_areas_records_contain_only_real_columns(parser, tmp_path):
    rows = [
        ["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn", "Ghi chú"],
        ["A1", "2PN", 2, 75, 10, "bỏ qua"],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "a.xlsx", rows), AREAS_TEMPLATE)

    assert set(result.records[0]) == set(AREAS_TEMPLATE.column_names)


def test_extra_columns_are_dropped_and_never_reach_records(parser, tmp_path):
    rows = [
        [
            "Phân khu",
            "Ghi chú",
            "Ngày bán",
            "Người bán",
            "Số căn bán",
            "Hoa hồng",
            "Mã bản ghi",
            "Cột nội bộ",
        ],
        ["A1", "khách VIP", date(2026, 1, 5), "Nguyễn A", 3, 15000000, "TX-1", "xyz"],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert set(result.records[0]) == {
        "area_name",
        "unit_type",
        "sold_date",
        "units_sold",
        "external_record_id",
        "source_updated_at",
        "source_row_hash",
    }
    leaked_values = str(result.records[0].values())
    for leaked in ("khách VIP", "Nguyễn A", "15000000", "xyz"):
        assert leaked not in leaked_values


def test_missing_required_columns_create_upload_errors(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày bán"],
        ["A1", date(2026, 1, 5)],
        ["A2", date(2026, 1, 6)],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 0
    assert result.rows_failed == 2
    assert result.rows_total == 2
    assert {e.column_name for e in result.errors} == {"units_sold", "external_record_id"}
    assert all(e.error_code == "MISSING_COLUMN" for e in result.errors)
    assert all(e.row_number == 1 for e in result.errors)


def test_multiple_missing_columns_emit_one_error_each(parser, tmp_path):
    rows = [["Phân khu"]]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_total == 0
    assert len(result.errors) == 3
    assert {e.column_name for e in result.errors} == {
        "sold_date",
        "units_sold",
        "external_record_id",
    }
    assert all(e.error_code == "MISSING_COLUMN" for e in result.errors)
    assert all(e.row_number == 1 for e in result.errors)


def test_extra_and_missing_columns_together(parser, tmp_path):
    rows = [
        [
            "Phân khu",
            "Ghi chú",
            "Ngày bán",
            "Số căn bán",
            "Mã bản ghi",
            "Cột thừa 1",
            "Cột thừa 2",
        ],
        ["A1", "note", date(2026, 1, 5), 3, "TX-1", "x", "y"],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert set(result.records[0]) == {
        "area_name",
        "unit_type",
        "sold_date",
        "units_sold",
        "external_record_id",
        "source_updated_at",
        "source_row_hash",
    }
    assert "Ghi chú" not in str(result.records[0].keys())
    assert "note" not in str(result.records[0].values())


def test_duplicate_header_with_extra_same_name_keeps_first_real_column(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi", "Số căn bán"],
        ["A1", date(2026, 1, 5), 3, "TX-1", 999],
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["units_sold"] == 3


def test_headers_with_extra_whitespace_and_newlines_are_normalized(parser, tmp_path):
    rows = [
        ["  Phân khu \n", "Ngày  bán", "Số   căn bán", "Mã   bản   ghi"],
        ["A1", date(2026, 1, 5), 3, "TX-1"],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["area_name"] == "A1"
    assert result.records[0]["external_record_id"] == "TX-1"


def test_whitespace_only_required_values_are_treated_as_empty(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
        ["A1", date(2026, 1, 5), 3, "   "],
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_failed == 1
    assert result.errors[0].column_name == "external_record_id"
    assert result.errors[0].error_code == "EMPTY_VALUE"


def test_snapshot_type_is_case_insensitive(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày chốt", "Tồn kho", "Loại chốt"],
        ["A1", date(2026, 1, 5), 10, "Manual"],
    ]
    result = parser.parse(_write_csv(tmp_path / "i.csv", rows), INVENTORY_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["snapshot_type"] == "manual"


def test_area_sqm_accepts_locale_decimal_format(parser, tmp_path):
    rows = [
        ["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"],
        ["A1", "2PN-A", 2, "1.234,56", 10],
    ]
    result = parser.parse(_write_csv(tmp_path / "a.csv", rows), AREAS_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["area_sqm"] == Decimal("1234.56")


# --- Chốt chặn cho hai sửa đổi ở trên ---------------------------------------
# Hai case chính đã có test_snapshot_type_is_case_insensitive và
# test_area_sqm_accepts_locale_decimal_format ở trên. Hai test dưới chỉ canh
# phần rủi ro còn lại: chuẩn hoá không được nới lỏng validate, và nhánh tách
# dấu phân cách viết lại không được đổi kết quả của các định dạng đang chạy.


def test_snapshot_type_normalization_does_not_widen_allowed_values(parser, tmp_path):
    rows = [["Phân khu", "Ngày chốt", "Tồn kho", "Loại chốt"], ["A1", "2026-01-05", 10, "Linh-Tinh"]]
    result = parser.parse(_write_csv(tmp_path / "i.csv", rows), INVENTORY_TEMPLATE)

    assert result.rows_failed == 1
    assert result.errors[0].error_code == "INVALID_CHOICE"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.56", "1234.56"),  # US: phẩy nghìn, chấm thập phân
        ("75,5", "75.5"),  # một dấu phẩy = thập phân
        ("1.234", "1.234"),  # chỉ có chấm → giữ nguyên, KHÔNG coi là phân cách nghìn
        ("1,234,567", "1234567"),  # nhiều dấu phẩy = phân cách nghìn
    ],
)
def test_area_sqm_formats_that_already_worked_are_unchanged(parser, tmp_path, raw, expected):
    rows = [["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"], ["A1", "2PN", 2, raw, 10]]
    result = parser.parse(_write_csv(tmp_path / "a.csv", rows), AREAS_TEMPLATE)

    assert result.rows_ok == 1, result.errors
    assert result.records[0]["area_sqm"] == Decimal(expected)


def test_headers_with_whitespace_newlines_and_mixed_case_are_normalized(parser, tmp_path):
    rows = [
        ["  Phân khu \n", "Ngày\tbán", "Số   căn   bán", "Mã   bản   ghi"],
        ["A1", date(2026, 1, 5), 3, "TX-1"],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "s.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["area_name"] == "A1"
    assert result.records[0]["external_record_id"] == "TX-1"


def test_duplicate_header_with_weird_spacing_keeps_first_column(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi", "Số căn bán  "],
        ["A1", date(2026, 1, 5), 3, "TX-1", 999],
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["units_sold"] == 3


def test_whitespace_only_required_value_is_treated_as_empty(parser, tmp_path):
    rows = [
        ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
        ["A1", date(2026, 1, 5), 3, "   "],
    ]
    result = parser.parse(_write_csv(tmp_path / "s.csv", rows), SALES_TEMPLATE)

    assert result.rows_failed == 1
    assert result.errors[0].column_name == "external_record_id"
    assert result.errors[0].error_code == "EMPTY_VALUE"


def test_locale_decimal_current_supported_formats_still_work(parser, tmp_path):
    rows = [
        ["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"],
        ["A1", "2PN-A", 2, "75,5", 10],
        ["A2", "2PN-B", 2, "1,234.56", 20],
    ]
    result = parser.parse(_write_csv(tmp_path / "a.csv", rows), AREAS_TEMPLATE)

    assert result.rows_ok == 2
    assert result.records[0]["area_sqm"] == Decimal("75.5")
    assert result.records[1]["area_sqm"] == Decimal("1234.56")


# --- Test Enhancement ---------------------------------------


def test_utf16_csv_with_bom_is_readable(parser, tmp_path):
    path = tmp_path / "sales_utf16.csv"
    content = "Phân khu,Ngày bán,Số căn bán,Mã bản ghi\nA1,2026-01-05,3,TX-1\n"
    path.write_text(content, encoding="utf-16")
    result = parser.parse(path, SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["area_name"] == "A1"


def test_locale_decimal_with_thousand_space_is_parsed(parser, tmp_path):
    rows = [
        ["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"],
        ["A1", "2PN-A", 2, "1 234,56", 10],
    ]
    result = parser.parse(_write_csv(tmp_path / "a.csv", rows), AREAS_TEMPLATE)

    assert result.rows_ok == 1
    assert result.records[0]["area_sqm"] == Decimal("1234.56")


def test_messy_real_world_file_with_extra_and_missing_fields(parser, tmp_path):
    rows = [
        ["Phân khu", "Ghi chú", "Ngày bán", "Số căn bán", "Mã bản ghi", "Cột nội bộ"],
        ["A1", "note", date(2026, 1, 5), 3, "TX-1", "x"],
        ["A2", "note2", date(2026, 1, 6), "", "TX-2", "y"],
    ]
    result = parser.parse(_write_xlsx(tmp_path / "sales.xlsx", rows), SALES_TEMPLATE)

    assert result.rows_ok == 1
    assert result.rows_failed == 1
    assert result.errors[0].column_name == "units_sold"
    assert result.errors[0].error_code == "EMPTY_VALUE"
    assert "Ghi chú" not in str(result.records[0].keys())


# --- Encoding của đường đọc CSV ---------------------------------------------


def _write_csv_bytes(path, rows, encoding):
    """Ghi CSV bằng encoding chỉ định (kể cả loại có BOM) để test đường giải mã."""
    text = "\r\n".join(",".join(str(c) for c in row) for row in rows) + "\r\n"
    path.write_bytes(text.encode(encoding))
    return path


ENCODED_ROWS = [
    ["Phân khu", "Ngày bán", "Số căn bán", "Mã bản ghi"],
    ["A1", "2026-01-05", 3, "TX-1"],
]


@pytest.mark.parametrize(
    "encoding",
    [
        "utf-8",  # không BOM — đường chạy cũ, phải y hệt trước
        "utf-8-sig",  # BOM UTF-8 Excel hay chèn
        "utf-16",  # BOM UTF-16 (Excel 'CSV UTF-16')
        "utf-16-le",  # sẽ được ghi kèm BOM ở dưới
        "utf-16-be",
        "utf-32",
    ],
)
def test_csv_encodings_with_bom_are_decoded(parser, tmp_path, encoding):
    """Sniff theo BOM: UTF-8 giữ nguyên hành vi, UTF-16/32 đọc được thay vì nổ."""
    # utf-16-le/be không tự ghi BOM nên thêm tay, mô phỏng file Excel xuất ra.
    if encoding in ("utf-16-le", "utf-16-be"):
        bom = "﻿"
        text = bom + "\r\n".join(",".join(str(c) for c in row) for row in ENCODED_ROWS) + "\r\n"
        path = tmp_path / f"{encoding}.csv"
        path.write_bytes(text.encode(encoding))
    else:
        path = _write_csv_bytes(tmp_path / f"{encoding}.csv", ENCODED_ROWS, encoding)

    result = parser.parse(path, SALES_TEMPLATE)

    assert (result.rows_ok, result.rows_failed) == (1, 0), result.errors
    assert result.records[0]["area_name"] == "A1"
    assert result.records[0]["external_record_id"] == "TX-1"


def test_utf32_bom_is_not_mistaken_for_utf16(parser, tmp_path):
    """BOM UTF-32 LE mở đầu bằng đúng BOM UTF-16 LE — thứ tự xét phải đúng."""
    path = _write_csv_bytes(tmp_path / "u32.csv", ENCODED_ROWS, "utf-32-le")
    # utf-32-le không tự ghi BOM; ghép tay để có đủ 4 byte ff fe 00 00.
    path.write_bytes(b"\xff\xfe\x00\x00" + path.read_bytes())

    assert _sniff_encoding(path) == "utf-32"
    assert parser.parse(path, SALES_TEMPLATE).rows_ok == 1


def test_undecodable_csv_raises_structural_error(parser, tmp_path):
    """UTF-16 KHÔNG BOM: không sniff được → phải báo lỗi rõ, không nổ UnicodeDecodeError."""
    path = tmp_path / "no-bom.csv"
    path.write_bytes("\r\n".join(",".join(str(c) for c in r) for r in ENCODED_ROWS).encode("utf-16-le"))

    with pytest.raises(ExcelParseError) as exc:
        parser.parse(path, SALES_TEMPLATE)

    assert exc.value.error_code == "UNSUPPORTED_ENCODING"
    assert "CSV UTF-8" in exc.value.message


def test_sniff_defaults_to_utf8_sig_without_bom(tmp_path):
    path = _write_csv_bytes(tmp_path / "plain.csv", ENCODED_ROWS, "utf-8")

    assert _sniff_encoding(path) == "utf-8-sig"
