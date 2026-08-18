"""ExcelParserService — đọc Excel/CSV và chuẩn hoá thành bản ghi sẵn sàng ghi DB.

SRS §5.2: `ExcelParserService` đọc Excel/CSV, ánh xạ cột theo template cố định.

Module này làm đúng ba việc, không hơn:

1. Nhận file — nhận dạng định dạng, đọc từng dòng thô.
2. Lọc whitelist — CHỈ giữ cột mà bảng đích cần (`ColumnSpec`), ép kiểu và soi
   CHECK constraint. Cột thừa trong file bị bỏ hẳn, không đi vào bản ghi.
3. Trả bản ghi sạch cho tầng insert, kèm lỗi theo dòng cho `upload_errors`.

Cụ thể là KHÔNG giữ dòng Excel gốc: `raw_row` chỉ sống trong một vòng lặp rồi
bị bỏ. Bản ghi trả ra chỉ có đúng các field khai trong template — file có thêm
cột ghi chú, cột tính tay của sale hay cột nội bộ nào cũng không lọt vào.

Module cố ý KHÔNG import LangGraph/LLM hay tầng DB. Đầu vào là một file, đầu ra
là bản ghi thuần Python neo vào migration 0001_initial_schema. Nhờ vậy parse
chạy được trong worker RQ mà không kéo theo AI core.

Chỉ file hỏng ở mức CẤU TRÚC mới ném `ExcelParseError` và giết cả file: định
dạng không hỗ trợ, file rỗng, không đọc nổi. Thiếu cột bắt buộc hay sai dữ liệu
là lỗi người dùng sửa được → ghi `upload_errors` theo dòng, file vẫn parse hết.

Template → bảng đích:

    sales      → sales_records        (+ area_id tra từ area_name, + file_id)
    inventory  → inventory_snapshots  (+ area_id tra từ area_name, + file_id)
    areas      → areas                (+ project_id do phạm vi upload quyết định)

Bản ghi trả về CHƯA insert thẳng được. Còn thiếu ba nhóm giá trị mà parser
không thể biết, tầng insert phải bù:

1. `id` — UUID sinh lúc insert.
2. Khoá ngoại — `area_id` phân giải từ field lookup (`area_name`, `unit_type`),
   `file_id` từ `upload_files`, `project_id` từ phạm vi upload.
3. `created_at` — thời điểm insert.

`TableTemplate.column_names` liệt kê field ghi thẳng vào bảng đích;
`.lookup_names` liệt kê field chỉ dùng để tra khoá ngoại.

Ranh giới trách nhiệm (SRS §5.2):
- Ở đây: nhận dạng định dạng, ánh xạ cột, ép kiểu, và CHECK constraint ở phạm vi
  MỘT Ô (units_sold >= 0, area_sqm > 0, snapshot_type IN (...)). Bắt tại đây thì
  lỗi quy được về số dòng cho `upload_errors`; để DB bắt thì cả batch INSERT vỡ
  mà không chỉ ra được dòng nào hỏng.
- ValidationService: những gì cần nhìn nhiều dòng hoặc tra DB — phân khu không
  tồn tại, trùng khoá `(area_id, date)`, ngưỡng rollback.

Engine đọc:
- `.xlsx/.xlsm/.xlsb/.xls/.ods` → python-calamine (Rust).
- `.csv/.txt` → module `csv` của stdlib (calamine không đọc CSV).

openpyxl không nằm trên đường chạy nóng — xem ghi chú ở requirements.txt.

Đo trên file 200.000 dòng / 4,7 MB (mỗi phép đo một tiến trình riêng):

    openpyxl read_only, đọc thô     9,19 s    58 MB
    calamine, đọc thô               0,75 s   102 MB   (nhanh hơn 12x)
    parse() — đọc + map + ép kiểu   1,54 s   159 MB
    parse_to_csv() — streaming      1,89 s   102 MB

Đánh đổi cần biết: calamine giải nén và giữ CẢ SHEET trong bộ nhớ Rust, nên tốn
RAM hơn `openpyxl(read_only=True)` — thứ vốn stream thật sự theo dòng. Đổi lại
nhanh hơn 12x. `iter_rows()` chỉ tránh dựng list Python cho toàn bộ dòng, KHÔNG
biến calamine thành lazy reader. Với trần upload 20 MB (SRS §2.4) — cỡ 850k dòng
— đỉnh RAM rơi vào khoảng 400–450 MB; nếu sau này nới trần thì phải đổi engine
hoặc cắt file, không chỉ đổi cách gọi. Chỉ đường CSV mới thật sự stream.
"""

from __future__ import annotations

import csv
import hashlib
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from src.logging_config import get_logger

log = get_logger("src.services.excel_parser")

CALAMINE_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"})
CSV_SUFFIXES = frozenset({".csv", ".txt"})

# Chặn file rác sinh hàng triệu lỗi làm phình RAM worker. Vượt ngưỡng thì dừng sớm:
# file hỏng tới mức này chắc chắn bị ValidationService rollback.
MAX_ERRORS = 1000

# Dòng header trong file. Cũng là `row_number` dùng cho lỗi mức header
# (thiếu cột bắt buộc) — `upload_errors` có CHECK row_number > 0 nên không
# thể dùng 0 để đánh dấu.
HEADER_ROW = 1

# Tên cột số dòng trong file staging. Chỉ tồn tại trong file staging, không phải
# cột của bảng đích và không có trong `records` của `parse()`.
STAGING_ROW_NUMBER = "row_number"

# Định dạng ngày chấp nhận cho CSV (Excel đã trả sẵn kiểu date qua calamine).
# Ưu tiên ISO, sau đó là các kiểu người dùng VN hay gõ tay.
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")

ColumnKind = Literal["str", "int", "decimal", "date", "timestamp"]

# Giá trị hợp lệ của `inventory_snapshots.snapshot_type`, lấy đúng từ CHECK
# `ck_inventory_snapshots_snapshot_type` trong 0001_initial_schema.
SNAPSHOT_TYPES = ("opening", "closing", "manual", "derived")


class ExcelParseError(Exception):
    """File không đọc được ở mức cấu trúc (sai định dạng, thiếu header, sheet rỗng)."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """Một cột của template, neo vào đúng một cột trong 0001_initial_schema.

    `min_value` / `exclusive_min` / `allowed` chép lại CHECK constraint của cột
    tương ứng. Bắt ở đây để lỗi quy được về SỐ DÒNG (ghi `upload_errors`); nếu
    thả xuống DB thì cả batch INSERT vỡ mà không biết dòng nào hỏng.
    """

    name: str
    kind: ColumnKind
    aliases: tuple[str, ...] = ()
    required: bool = True
    default: Any = None
    # True = không phải cột của bảng đích, chỉ dùng để phân giải khoá ngoại.
    lookup: bool = False
    min_value: Any = None
    exclusive_min: bool = False
    allowed: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class TableTemplate:
    """Template cột cố định cho một loại file nạp, gắn với một bảng đích."""

    name: str
    target_table: str
    columns: tuple[ColumnSpec, ...]
    # `areas` không có cột source_row_hash nên template đó không sinh hash.
    emits_row_hash: bool = True

    # --- Nạp lặp lại được (0005) ------------------------------------------
    # `conflict_constraint`: tên UNIQUE của bảng đích dùng làm đích ON CONFLICT.
    #   Neo theo TÊN chứ không theo danh sách cột: tên nằm trong migration nên
    #   đổi ràng buộc mà quên sửa ở đây sẽ vỡ ngay lúc chạy, không âm thầm nạp
    #   trùng.
    # `conflict_columns`: đúng các cột tạo nên ràng buộc đó, dùng để loại trùng
    #   NGAY TRONG một file trước khi gửi xuống DB — Postgres không cho một lệnh
    #   ON CONFLICT DO UPDATE chạm hai lần vào cùng một dòng.
    # `versioned_by`: cột quyết định bản ghi nào mới hơn. None = không có phiên
    #   bản, trùng thì bỏ qua (DO NOTHING) thay vì ghi đè.
    conflict_constraint: str | None = None
    conflict_columns: tuple[str, ...] = ()
    versioned_by: str | None = None

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def column_names(self) -> tuple[str, ...]:
        """Các field ghi thẳng vào `target_table`."""
        return tuple(c.name for c in self.columns if not c.lookup)

    @property
    def lookup_names(self) -> tuple[str, ...]:
        """Các field KHÔNG phải cột của bảng đích — dùng để tra khoá ngoại."""
        return tuple(c.name for c in self.columns if c.lookup)


# `areas` không có UNIQUE nào ngoài PK, nên quy tắc tra một dòng file về đúng
# `areas.id` là quyết định nghiệp vụ của ValidationService, không phải của parser.
# Parser chỉ đưa ra nguyên liệu tra cứu: area_name (+ unit_type nếu file có).
_AREA_NAME = ColumnSpec(
    "area_name",
    "str",
    ("phan khu", "phankhu", "area", "area name", "ten phan khu"),
    lookup=True,
)
# Phiên bản của bản ghi ở hệ nguồn (0005). TUỲ CHỌN: file do người dùng gõ tay
# thường không có cột này, và mặc định NULL là câu trả lời trung thực cho "không
# biết bản ghi này sửa lần cuối lúc nào". Có giá trị thì nó quyết định lần nạp
# sau có được ghi đè lần nạp trước hay không — xem ImportService._upsert_stmt.
_SOURCE_UPDATED_AT = ColumnSpec(
    "source_updated_at",
    "timestamp",
    ("cap nhat luc", "source updated at", "updated at", "thoi diem cap nhat"),
    required=False,
)
_UNIT_TYPE_LOOKUP = ColumnSpec(
    "unit_type",
    "str",
    ("loai can", "loai can ho", "unit type", "type"),
    required=False,
    lookup=True,
)

# → sales_records. area_id/file_id/id/created_at do tầng insert điền, không đọc từ file.
SALES_TEMPLATE = TableTemplate(
    name="sales",
    target_table="sales_records",
    columns=(
        _AREA_NAME,
        _UNIT_TYPE_LOOKUP,
        ColumnSpec("sold_date", "date", ("ngay ban", "ngay", "date", "sold date")),
        ColumnSpec(
            "units_sold",
            "int",
            ("so can ban", "so can", "units", "units sold", "so luong"),
            min_value=0,  # ck_sales_records_units_sold_nonnegative
        ),
        # NOT NULL + ck_sales_records_external_record_id_not_blank: file BẮT BUỘC
        # phải có mã bản ghi, không thể để trống rồi vá ở tầng insert.
        ColumnSpec(
            "external_record_id",
            "str",
            ("ma ban ghi", "ma giao dich", "record id", "external id", "ma gd"),
        ),
        _SOURCE_UPDATED_AT,
    ),
    conflict_constraint="uq_sales_area_date_external_id",
    conflict_columns=("area_id", "sold_date", "external_record_id"),
    versioned_by="source_updated_at",
)

# → inventory_snapshots.
INVENTORY_TEMPLATE = TableTemplate(
    name="inventory",
    target_table="inventory_snapshots",
    columns=(
        _AREA_NAME,
        _UNIT_TYPE_LOOKUP,
        ColumnSpec("snapshot_date", "date", ("ngay chot", "ngay", "date", "snapshot date")),
        ColumnSpec(
            "units_remaining",
            "int",
            ("ton kho", "so can con", "units remaining", "remaining", "con lai"),
            min_value=0,  # ck_inventory_snapshots_units_remaining_nonnegative
        ),
        ColumnSpec(
            "snapshot_type",
            "str",
            ("loai chot", "snapshot type", "kieu chot"),
            required=False,
            default="manual",
            allowed=SNAPSHOT_TYPES,  # ck_inventory_snapshots_snapshot_type
        ),
        _SOURCE_UPDATED_AT,
    ),
    conflict_constraint="uq_inventory_area_date_type",
    conflict_columns=("area_id", "snapshot_date", "snapshot_type"),
    versioned_by="source_updated_at",
)

# → areas: file danh mục phân khu / loại căn hộ. project_id là FK NOT NULL nhưng
# KHÔNG nằm trong file — nó do phạm vi upload quyết định, tầng insert điền vào.
AREAS_TEMPLATE = TableTemplate(
    name="areas",
    target_table="areas",
    emits_row_hash=False,  # bảng `areas` không có cột source_row_hash
    columns=(
        ColumnSpec("area_name", "str", ("phan khu", "phankhu", "area", "area name", "ten phan khu")),
        ColumnSpec("unit_type", "str", ("loai can", "loai can ho", "unit type")),
        ColumnSpec(
            "bedrooms",
            "int",
            ("so phong ngu", "phong ngu", "bedrooms", "pn"),
            min_value=0,  # ck_areas_bedrooms_nonnegative
        ),
        ColumnSpec(
            "area_sqm",
            "decimal",
            ("dien tich", "dien tich m2", "area sqm", "sqm", "m2"),
            min_value=0,
            exclusive_min=True,  # ck_areas_area_sqm_positive: area_sqm > 0
        ),
        ColumnSpec(
            "total_units",
            "int",
            ("tong so can", "tong can", "total units", "so can"),
            min_value=0,  # ck_areas_total_units_nonnegative
        ),
    ),
    # Danh mục phân khu không mang phiên bản: nạp lại cùng một danh mục chỉ cần
    # KHÔNG làm gì. Ghi đè theo lần nạp gần nhất sẽ cho phép một file cũ lặng lẽ
    # đổi `total_units` — mẫu số của tỷ lệ hấp thụ.
    conflict_constraint="uq_areas_project_name_unit_type",
    conflict_columns=("project_id", "area_name", "unit_type"),
    versioned_by=None,
)

TEMPLATES = {t.name: t for t in (SALES_TEMPLATE, INVENTORY_TEMPLATE, AREAS_TEMPLATE)}


@dataclass(frozen=True, slots=True)
class ParseError:
    """Lỗi một ô/một dòng. Field khớp 1–1 cột bảng `upload_errors`."""

    row_number: int
    column_name: str | None
    error_code: str
    message: str

    def as_record(self, file_id: str) -> dict[str, Any]:
        return {
            "file_id": file_id,
            "row_number": self.row_number,
            "column_name": self.column_name,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(slots=True)
class ParseResult:
    """Kết quả parse. `rows_ok`/`rows_failed` ghi thẳng vào `upload_files`."""

    template: str
    records: list[dict[str, Any]] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)
    rows_ok: int = 0
    rows_failed: int = 0
    rows_total: int = 0
    errors_truncated: bool = False


def _strip_accents(text: str) -> str:
    """'Phân khu' → 'phan khu'. Cho phép header tiếng Việt có/không dấu đều khớp."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalize_header(value: Any) -> str:
    if value is None:
        return ""
    text = _strip_accents(str(value)).lower().replace("_", " ")
    return " ".join(text.split())


def _to_int(value: Any) -> int:
    """Excel trả số dạng float (3 → 3.0); chỉ nhận float là số nguyên đúng."""
    if isinstance(value, bool):
        raise ValueError("giá trị boolean không phải số căn")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        as_int = int(value)
        if as_int != value:
            raise ValueError(f"'{value}' không phải số nguyên")
        return as_int
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text:
        raise ValueError("giá trị rỗng")
    return int(text)


def _to_date(value: Any) -> date:
    """calamine trả sẵn date/datetime; CSV trả chuỗi nên phải thử từng format."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("giá trị rỗng")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{text}' không khớp định dạng ngày (chấp nhận: {', '.join(_DATE_FORMATS)})")


def _to_str(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        # Mã phân khu đọc từ Excel ra float ('12' → 12.0) thì cắt đuôi .0.
        return str(int(value))
    text = str(value).strip()
    if not text:
        raise ValueError("giá trị rỗng")
    return text


def _to_decimal(value: Any) -> Decimal:
    """Cho cột `sa.Numeric` (areas.area_sqm). Dùng Decimal chứ không float.

    Diện tích đi vào cột Numeric của Postgres; đi qua float sẽ làm tròn nhị phân
    (75.7 → 75.69999...). Decimal giữ nguyên con số người dùng gõ.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("giá trị boolean không phải số")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))  # qua str để không kéo theo sai số nhị phân

    text = str(value).strip().replace(" ", "")
    if not text:
        raise ValueError("giá trị rỗng")
    # Khi có CẢ dấu chấm lẫn dấu phẩy, dấu ĐỨNG SAU là dấu thập phân:
    #   "1.234,56" (VN/EU) → 1234.56    ·    "1,234.56" (US) → 1234.56
    # Chỉ có dấu phẩy: một dấu là thập phân ("75,5"), nhiều dấu là phân cách
    # nghìn ("1,234,567"). Chỉ có dấu chấm thì giữ nguyên — "1.234" vẫn là 1.234.
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        if last_comma > last_dot:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif last_comma >= 0 and text.count(",") == 1:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' không phải số") from exc


def _to_timestamp(value: Any) -> datetime:
    """Cho cột `source_updated_at` — mốc phiên bản của bản ghi ở hệ nguồn.

    BẮT BUỘC có múi giờ. Một mốc trần ("2026-08-05 10:30") không nói được nó là
    giờ nào; đoán bừa là UTC sẽ làm lệch 7 tiếng so với giờ Việt Nam và âm thầm
    đảo thứ tự "bản nào mới hơn" — đúng thứ mà cột này sinh ra để quyết định.
    Thà từ chối dòng đó và báo rõ còn hơn ghi đè nhầm dữ liệu mới bằng dữ liệu cũ.
    """
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("giá trị rỗng")
        try:
            # fromisoformat của 3.11 đã hiểu hậu tố 'Z'.
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"'{text}' không phải mốc thời gian ISO-8601 (ví dụ: 2026-08-05T10:30:00+07:00)") from exc

    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"'{value}' thiếu múi giờ — phải ghi kèm offset, ví dụ 2026-08-05T10:30:00+07:00")
    return parsed


_CONVERTERS = {
    "str": _to_str,
    "int": _to_int,
    "decimal": _to_decimal,
    "date": _to_date,
    "timestamp": _to_timestamp,
}


def _check_constraints(value: Any, spec: ColumnSpec) -> None:
    """Soi giá trị đã ép kiểu theo CHECK constraint của cột (xem ColumnSpec)."""
    if spec.allowed is not None and value not in spec.allowed:
        raise _ConstraintViolationError(
            "INVALID_CHOICE",
            f"'{value}' không hợp lệ, chỉ nhận: {', '.join(spec.allowed)}",
        )
    if spec.min_value is not None:
        if spec.exclusive_min and value <= spec.min_value:
            raise _ConstraintViolationError("VALUE_OUT_OF_RANGE", f"phải lớn hơn {spec.min_value}, nhận '{value}'")
        if not spec.exclusive_min and value < spec.min_value:
            raise _ConstraintViolationError(
                "VALUE_OUT_OF_RANGE", f"không được nhỏ hơn {spec.min_value}, nhận '{value}'"
            )


class _ConstraintViolationError(Exception):
    """Giá trị đúng kiểu nhưng vi phạm CHECK của cột. Nội bộ module."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def row_hash(template_name: str, values: Sequence[Any]) -> str:
    """Hash ổn định của một dòng → `source_row_hash` (NOT NULL trong schema).

    Hash tính trên giá trị ĐÃ ép kiểu, không phải ô thô: cùng một dòng đọc từ
    .xlsx (số 3) và từ .csv (chuỗi "3") phải ra cùng hash, nếu không việc chống
    trùng sẽ hỏng khi người dùng nạp lại cùng dữ liệu ở định dạng khác.

    blake2b nhanh hơn sha256 rõ rệt trên chuỗi ngắn và ở đây chỉ cần chống trùng,
    không cần thuộc tính mật mã (checksum file mới dùng SHA-256 — SRS §2.4).
    """
    payload = "\x1f".join(v.isoformat() if isinstance(v, date) else str(v) for v in values)
    return hashlib.blake2b(f"{template_name}\x1e{payload}".encode(), digest_size=16).hexdigest()


def detect_format(path: Path) -> Literal["calamine", "csv"]:
    suffix = path.suffix.lower()
    if suffix in CALAMINE_SUFFIXES:
        return "calamine"
    if suffix in CSV_SUFFIXES:
        return "csv"
    raise ExcelParseError(
        "UNSUPPORTED_FORMAT",
        f"Định dạng '{suffix or path.name}' không được hỗ trợ. "
        f"Chấp nhận: {', '.join(sorted(CALAMINE_SUFFIXES | CSV_SUFFIXES))}",
    )


# BOM → encoding. BOM 4 byte PHẢI xét trước BOM 2 byte: b"\xff\xfe\x00\x00"
# (UTF-32 LE) bắt đầu đúng bằng BOM của UTF-16 LE, xét ngược thứ tự sẽ đọc file
# UTF-32 thành UTF-16 mà không báo lỗi gì.
_BOM_ENCODINGS = (
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\xfe\xff", "utf-16"),
    (b"\xff\xfe", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


def _sniff_encoding(path: Path) -> str:
    """Chọn encoding theo BOM; không có BOM thì giữ nguyên mặc định cũ utf-8-sig.

    Chỉ đọc BOM, không đoán mò theo nội dung: đoán sai sẽ sinh dữ liệu hỏng âm
    thầm (mojibake lọt vào `area_name`) — tệ hơn nhiều so với báo lỗi thẳng.
    """
    with path.open("rb") as raw:
        head = raw.read(4)
    return next((enc for bom, enc in _BOM_ENCODINGS if head.startswith(bom)), "utf-8-sig")


def _iter_raw_rows(path: Path, sheet: int | str) -> Iterator[list[Any]]:
    """Sinh từng dòng thô, che khác biệt giữa hai engine.

    CSV stream thật sự theo dòng. Excel thì calamine đã giữ cả sheet trong bộ nhớ
    Rust từ lúc `from_path` — `iter_rows()` chỉ tránh dựng thêm list Python cho
    toàn bộ dòng (xem docstring module).
    """
    if detect_format(path) == "csv":
        # newline="" theo yêu cầu của module csv. Encoding lấy theo BOM; file
        # không BOM vẫn đọc bằng utf-8-sig y như trước.
        with path.open("r", encoding=_sniff_encoding(path), newline="") as handle:
            try:
                yield from csv.reader(handle)
            except UnicodeDecodeError as exc:
                # csv.reader giải mã LƯỜI nên lỗi nổ lúc lặp chứ không phải lúc
                # open(). UnicodeDecodeError là ValueError, không phải OSError,
                # nên nếu không bọc thì nó lọt qua mọi handler của job và thoát ra
                # ngoài: RQ đánh dấu failed mà không có error_code, đồng thời bỏ
                # lại file staging dở dang. Ném ExcelParseError để rơi vào đúng
                # kênh lỗi cấu trúc sẵn có — job tự map sang failed và dọn staging.
                raise ExcelParseError(
                    "UNSUPPORTED_ENCODING",
                    "File CSV is not UTF-8. Open it in Excel, choose 'Save as → CSV UTF-8', and upload again.",
                ) from exc
        return

    # import trễ: worker CSV không cần nạp
    from python_calamine import CalamineError, CalamineWorkbook, PasswordError

    # `CalamineError` kế thừa THẲNG từ Exception — không phải OSError, không phải
    # ValueError — nên nó lọt qua mọi handler của `run_parse_upload` và thoát ra
    # ngoài. Hậu quả đúng bằng lỗi S1 đã sửa cho nhánh CSV: job chết giữa chừng,
    # `upload_files` không ai đánh dấu, lô treo ở 'pending' vĩnh viễn và người
    # dùng poll `/status` không bao giờ thấy kết thúc. Bọc lại thành
    # `ExcelParseError` để rơi vào đúng kênh lỗi cấu trúc mà job đã xử lý sẵn —
    # cùng cách nhánh CSV bọc `UnicodeDecodeError` ở trên.
    try:
        workbook = CalamineWorkbook.from_path(str(path))
    except PasswordError as exc:
        # Tách riêng: file đặt mật khẩu không phải file hỏng, và người dùng sửa
        # được — nói đúng nguyên nhân thì họ không phải đoán.
        raise ExcelParseError(
            "FILE_PASSWORD_PROTECTED",
            "File Excel đang đặt mật khẩu. Gỡ mật khẩu rồi tải lên lại.",
        ) from exc
    except CalamineError as exc:
        # KHÔNG đưa str(exc) ra ngoài: message của calamine có kèm đường dẫn nội bộ.
        raise ExcelParseError(
            "UNREADABLE_WORKBOOK",
            "Không đọc được file Excel — file có thể hỏng hoặc không đúng định dạng. "
            "Mở lại bằng Excel, chọn 'Save as → .xlsx', rồi tải lên lại.",
        ) from exc

    try:
        worksheet = workbook.get_sheet_by_name(sheet) if isinstance(sheet, str) else workbook.get_sheet_by_index(sheet)
        yield from worksheet.iter_rows()
    except CalamineError as exc:
        # Hỏng lộ ra muộn: `from_path` mở được nhưng sheet bên trong mới lỗi.
        raise ExcelParseError(
            "UNREADABLE_WORKBOOK",
            "Không đọc được sheet đầu tiên của file Excel — file có thể hỏng.",
        ) from exc
    finally:
        workbook.close()


def _map_headers(header_row: Sequence[Any], template: TableTemplate) -> tuple[dict[str, int], list[str]]:
    """Ánh xạ tên cột chuẩn → chỉ số cột trong file.

    Đây là bộ lọc whitelist: chỉ cột nào khớp một `ColumnSpec` mới có mặt trong
    `mapping`. Mọi cột thừa trong file (ghi chú, cột nội bộ của sale, cột tính
    tay…) không vào mapping nên KHÔNG bao giờ chạm tới bản ghi.

    Trả về `(mapping, missing)` thay vì ném lỗi: thiếu cột bắt buộc là lỗi dữ
    liệu người dùng sửa được, phải ghi `upload_errors` chứ không giết cả file.
    """
    seen: dict[str, int] = {}
    for index, cell in enumerate(header_row):
        normalized = _normalize_header(cell)
        if normalized and normalized not in seen:
            seen[normalized] = index

    mapping: dict[str, int] = {}
    missing: list[str] = []
    for spec in template.columns:
        candidates = (_normalize_header(spec.name), *spec.aliases)
        index = next((seen[c] for c in candidates if c in seen), None)
        if index is None:
            if spec.required:
                missing.append(spec.name)
        else:
            mapping[spec.name] = index

    return mapping, missing


class ExcelParserService:
    """Đọc file nạp và trả bản ghi đã chuẩn hoá (SRS §5.2).

    Không giữ trạng thái giữa các lần gọi nên dùng lại được trong worker RQ.
    """

    def __init__(self, *, max_errors: int = MAX_ERRORS) -> None:
        self.max_errors = max_errors

    def _collect(self, result: ParseResult, errors: list[ParseError]) -> None:
        """Gom lỗi vào kết quả, chặn ở `max_errors` để file rác không phình RAM.

        Số đếm `rows_failed` vẫn đầy đủ; chỉ danh sách chi tiết mới bị cắt.
        """
        if not errors:
            return
        room = self.max_errors - len(result.errors)
        if room <= 0:
            result.errors_truncated = True
            return
        result.errors.extend(errors[:room])
        if len(errors) > room:
            result.errors_truncated = True

    def iter_records(
        self,
        path: Path,
        template: TableTemplate,
        *,
        sheet: int | str = 0,
    ) -> Iterator[tuple[int, dict[str, Any] | None, list[ParseError]]]:
        """Sinh `(row_number, record, errors)` cho từng dòng dữ liệu.

        Bản ghi không bị tích luỹ: sau khi yield, phía gọi quyết định giữ hay bỏ.
        Phần sheet nằm trong bộ nhớ calamine thì vẫn ở đó cho tới hết vòng lặp
        (xem docstring module) — đây là mức sàn RAM, không phải rò rỉ.

        `record` là None nếu dòng có lỗi ép kiểu. `row_number` đánh số theo dòng
        trong file (header = 1) để khớp `upload_errors.row_number` — đúng số dòng
        người dùng nhìn thấy trên Excel.

        Nếu file thiếu cột bắt buộc, item ĐẦU TIÊN sinh ra có `row_number` bằng
        `HEADER_ROW`: đó là chẩn đoán mức header, không phải một dòng dữ liệu.
        Phía gọi phải bỏ qua nó khi đếm `rows_total` (xem `parse`).
        """
        rows = _iter_raw_rows(path, sheet)
        header_row = next(rows, None)
        if header_row is None:
            raise ExcelParseError("EMPTY_FILE", "File rỗng hoặc sheet đầu tiên không có dữ liệu")

        mapping, missing = _map_headers(header_row, template)

        if missing:
            # Thiếu cột bắt buộc thì mọi dòng đều hỏng, nhưng KHÔNG ném lỗi cả file:
            # báo một lần trên dòng header rồi đếm số dòng hỏng, để người dùng vẫn
            # biết file có bao nhiêu dòng. Báo theo từng dòng sẽ đẻ ra hàng trăm
            # nghìn lỗi giống hệt nhau.
            readable = ", ".join(sorted(_normalize_header(c) for c in header_row if _normalize_header(c)))
            yield (
                HEADER_ROW,
                None,
                [
                    ParseError(
                        HEADER_ROW,
                        name,
                        "MISSING_COLUMN",
                        f"Thiếu cột bắt buộc '{name}'. Cột đọc được: {readable or '(không có)'}",
                    )
                    for name in missing
                ],
            )
            for offset, raw_row in enumerate(rows, start=2):
                if raw_row and not all(cell is None or str(cell).strip() == "" for cell in raw_row):
                    yield offset, None, []
            return

        # Dựng sẵn kế hoạch ép kiểu ngoài vòng lặp: mỗi dòng chỉ còn index + gọi hàm.
        plan = [(spec.name, mapping.get(spec.name), _CONVERTERS[spec.kind], spec) for spec in template.columns]
        max_index = max((i for _, i, _, _ in plan if i is not None), default=-1)
        template_name = template.name
        field_names = template.field_names
        emits_row_hash = template.emits_row_hash

        for offset, raw_row in enumerate(rows, start=2):
            if not raw_row or all(cell is None or str(cell).strip() == "" for cell in raw_row):
                continue  # dòng trống giữa file — bỏ qua, không tính là lỗi

            record: dict[str, Any] = {}
            errors: list[ParseError] = []

            if len(raw_row) <= max_index:
                raw_row = list(raw_row) + [None] * (max_index + 1 - len(raw_row))

            for name, index, convert, spec in plan:
                raw = raw_row[index] if index is not None else None

                if raw is None or (isinstance(raw, str) and not raw.strip()):
                    if spec.required:
                        errors.append(ParseError(offset, name, "EMPTY_VALUE", f"Cột '{name}' bị bỏ trống"))
                    else:
                        record[name] = spec.default
                    continue

                try:
                    value = convert(raw)
                    if spec.allowed is not None and isinstance(value, str):
                        # Cột kiểu enum ("Opening", " manual ") — chuẩn hoá TRƯỚC khi
                        # soi `allowed` và lưu bản đã chuẩn hoá: CHECK của Postgres
                        # so khớp đúng chữ thường, viết hoa sẽ bị từ chối ở INSERT.
                        value = value.strip().lower()
                    _check_constraints(value, spec)
                    record[name] = value
                except (ValueError, TypeError) as exc:
                    errors.append(ParseError(offset, name, f"INVALID_{spec.kind.upper()}", f"Cột '{name}': {exc}"))
                except _ConstraintViolationError as exc:
                    errors.append(ParseError(offset, name, exc.error_code, f"Cột '{name}': {exc.message}"))

            if errors:
                yield offset, None, errors
            else:
                if emits_row_hash:
                    record["source_row_hash"] = row_hash(template_name, [record[n] for n in field_names])
                yield offset, record, []

    def parse(
        self,
        path: Path,
        template: TableTemplate,
        *,
        sheet: int | str = 0,
    ) -> ParseResult:
        """Parse toàn bộ file thành `ParseResult` (bản ghi nằm hết trong RAM).

        Đủ cho giới hạn 20 MB ở SRS §2.4. File lớn hơn thì dùng `parse_to_csv`.
        """
        result = ParseResult(template=template.name)

        for row_number, record, errors in self.iter_records(path, template, sheet=sheet):
            if row_number == HEADER_ROW:  # chẩn đoán header, không phải dòng dữ liệu
                self._collect(result, errors)
                continue

            result.rows_total += 1
            if record is not None:
                result.records.append(record)
                result.rows_ok += 1
                continue

            result.rows_failed += 1
            self._collect(result, errors)

        log.info(
            "excel.parse.finished",
            template=template.name,
            suffix=path.suffix.lower(),
            rows_total=result.rows_total,
            rows_ok=result.rows_ok,
            rows_failed=result.rows_failed,
        )
        return result

    def parse_to_csv(
        self,
        path: Path,
        template: TableTemplate,
        staging_path: Path,
        *,
        sheet: int | str = 0,
    ) -> ParseResult:
        """Parse và ghi thẳng dòng hợp lệ ra CSV staging, không giữ bản ghi trong RAM.

        CSV staging là đầu vào nhanh nhất cho `COPY ... FROM` của PostgreSQL —
        nhanh hơn nhiều so với INSERT từng dòng qua ORM. `ParseResult` trả về có
        `records` rỗng; số liệu đếm và lỗi vẫn đầy đủ để ghi `upload_files`.

        Cột của file staging = `row_number` + field của template (kể cả field
        lookup như `area_name`) + `source_row_hash` nếu bảng đích có cột đó. Đây
        CHƯA phải cột của bảng đích: tầng insert còn phải đổi lookup thành
        `area_id` và thêm `file_id`. Xem `TableTemplate.column_names` /
        `.lookup_names`.

        `row_number` KHÔNG phải cột của bảng đích nào — nó đi kèm để tầng insert
        quy được lỗi phát sinh lúc ghi (phân khu không tồn tại, dòng trùng) về
        đúng dòng người dùng thấy trên Excel, vì `upload_errors.row_number` là
        NOT NULL. `records` do `parse()` trả về KHÔNG có field này.
        """
        result = ParseResult(template=template.name)
        columns = (
            (STAGING_ROW_NUMBER,) + template.field_names + (("source_row_hash",) if template.emits_row_hash else ())
        )

        staging_path.parent.mkdir(parents=True, exist_ok=True)
        with staging_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()

            for row_number, record, errors in self.iter_records(path, template, sheet=sheet):
                if row_number == HEADER_ROW:  # chẩn đoán header, không phải dòng dữ liệu
                    self._collect(result, errors)
                    continue

                result.rows_total += 1
                if record is not None:
                    writer.writerow({STAGING_ROW_NUMBER: row_number, **record})
                    result.rows_ok += 1
                    continue

                result.rows_failed += 1
                self._collect(result, errors)

        log.info(
            "excel.parse.staged",
            template=template.name,
            staging_path=str(staging_path),
            rows_ok=result.rows_ok,
            rows_failed=result.rows_failed,
        )
        return result
