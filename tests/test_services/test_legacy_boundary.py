"""Ranh giới của đường nạp file cũ: Excel/CSV CHỈ được ghi vào ba bảng tổng hợp.

File Excel/CSV hiện có là dữ liệu TỔNG HỢP — mỗi dòng là một con số đếm theo
(phân khu, ngày), không phải một căn hộ hay một giao dịch. Nó không mang
`unit_code`, không mang `external_unit_id`/`external_deal_id`, không mang
`reserved_at`/`sold_at`/`lost_at`. Vì vậy không có cách nào dựng lại từng căn và
từng giao dịch từ nó mà không BỊA ra danh tính.

Bộ test này khoá ranh giới đó lại theo hai hướng độc lập:

1. **Cấu trúc** — đọc chính `TEMPLATES` và mã nguồn tầng nạp: không template nào
   trỏ vào `units`/`deals`, không field nào mang danh tính căn/giao dịch.
2. **Hành vi** — chạy thật cả ba template qua job nạp rồi đếm lại TOÀN BỘ bảng
   trong schema: `units`, `deals`, `crm_source_records` phải không nhúc nhích.

Kiểm cấu trúc bắt được lỗi sớm và không cần DB; kiểm hành vi bắt được đường ghi
gián tiếp mà đọc mã nguồn có thể bỏ sót. Thiếu vế nào cũng chưa đủ.
"""

import csv
import os
import uuid
from pathlib import Path

import pytest

from src.services.excel_parser import TEMPLATES

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

# Dự án riêng của module này. Dùng chung UUID với module khác thì fixture dọn dẹp
# của module đó sẽ xoá mất dữ liệu ở giữa chừng test này.
PROJECT_ID = uuid.UUID("b7c1d2e3-4f56-4789-a0b1-c2d3e4f50011")

# Ba bảng đích hợp lệ của đường nạp file. Danh sách này là HỢP ĐỒNG, không phải
# ảnh chụp: thêm được một bảng vào đây phải là quyết định có chủ đích.
LEGACY_TARGET_TABLES = frozenset({"sales_records", "inventory_snapshots", "areas"})

# Bảng do CRM tương lai sở hữu. Đường nạp file không bao giờ được chạm vào.
CRM_OWNED_TABLES = frozenset({"units", "deals", "crm_source_records"})

# Tên field mang danh tính từng căn / từng giao dịch. File tổng hợp không có
# thông tin này, nên template nào khai báo chúng là đang bịa.
UNIT_AND_DEAL_IDENTITY_FIELDS = frozenset(
    {
        "unit_id",
        "unit_code",
        "external_unit_id",
        "deal_id",
        "external_deal_id",
        "reserved_at",
        "sold_at",
        "lost_at",
        "deal_status",
        "unit_status",
    }
)


# --- 1. Kiểm cấu trúc: không cần database -----------------------------------


def test_templates_target_exactly_the_three_legacy_tables():
    """Tập bảng đích của TEMPLATES phải KHỚP CHÍNH XÁC ba bảng tổng hợp.

    Dùng `==` chứ không phải `<=`: thêm một bảng đích mới mà không sửa test là
    việc phải được nhìn thấy, kể cả khi bảng đó vô hại.
    """
    targets = {template.target_table for template in TEMPLATES.values()}

    assert targets == LEGACY_TARGET_TABLES, (
        f"template đang ghi vào bảng ngoài hợp đồng: {sorted(targets - LEGACY_TARGET_TABLES)}"
    )


def test_no_template_targets_a_crm_owned_table():
    """Không template nào được trỏ vào units/deals/crm_source_records."""
    for name, template in TEMPLATES.items():
        assert template.target_table not in CRM_OWNED_TABLES, (
            f"template '{name}' ghi vào bảng của CRM: {template.target_table}"
        )


@pytest.mark.parametrize("template_name", sorted(TEMPLATES))
def test_no_template_field_carries_unit_or_deal_identity(template_name):
    """File tổng hợp không mang danh tính căn/giao dịch, template cũng không được khai."""
    template = TEMPLATES[template_name]
    offending = set(template.field_names) & UNIT_AND_DEAL_IDENTITY_FIELDS

    assert not offending, f"template '{template_name}' khai field danh tính căn/giao dịch: {sorted(offending)}"


def test_sales_template_carries_a_count_not_a_unit():
    """`units_sold` là số ĐẾM. Nó không phải và không thể là một căn cụ thể.

    Test này ghim lại lý do vì sao không tồn tại đường chuyển tổng hợp → units:
    dòng file mang một con số, mà một con số thì không có danh tính để soi gương.
    """
    sales = TEMPLATES["sales"]
    spec = {c.name: c for c in sales.columns}

    assert "units_sold" in spec
    assert spec["units_sold"].kind == "int", "units_sold phải là số đếm"
    assert not (set(spec) & UNIT_AND_DEAL_IDENTITY_FIELDS)


INGESTION_MODULES = (
    Path("src") / "services" / "excel_parser.py",
    Path("src") / "services" / "import_records.py",
    Path("src") / "jobs" / "parse_upload.py",
)


def _ingestion_sources():
    root = Path(__file__).resolve().parents[2]
    return [(path.name, (root / path).read_text(encoding="utf-8")) for path in INGESTION_MODULES]


@pytest.mark.parametrize("module_name, source", _ingestion_sources(), ids=lambda v: v if isinstance(v, str) and len(v) < 100 else "")
def test_ingestion_module_never_imports_a_crm_owned_table(module_name, source):
    """Tầng nạp file không được import bảng `units`/`deals` từ models.

    Dùng AST chứ không tìm chuỗi con: `units` là chuỗi con của `units_sold`,
    `units_remaining`, `total_units` — tìm thô sẽ báo động giả liên tục rồi bị
    tắt đi, và lúc đó test coi như không tồn tại.
    """
    import ast

    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.models"):
            imported.update(alias.name for alias in node.names)

    offending = imported & CRM_OWNED_TABLES
    assert not offending, f"{module_name} import bảng của CRM: {sorted(offending)}"


@pytest.mark.parametrize("module_name, source", _ingestion_sources(), ids=lambda v: v if isinstance(v, str) and len(v) < 100 else "")
def test_ingestion_module_has_no_raw_sql_against_a_crm_owned_table(module_name, source):
    """Không có câu SQL thô nào trong tầng nạp file chạm tới bảng của CRM.

    Kiểm import ở trên không thấy được `sa.text("INSERT INTO units ...")`, nên
    cần thêm vế này. Chỉ soi chuỗi CÓ từ khoá SQL: docstring giải thích vì sao
    không ghi vào units/deals là hợp lệ và không được tính là vi phạm.
    """
    import ast
    import re

    sql_keywords = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|SELECT)\b", re.IGNORECASE)
    table_pattern = re.compile(rf"\b({'|'.join(sorted(CRM_OWNED_TABLES))})\b")

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        if not sql_keywords.search(text):
            continue
        found = table_pattern.findall(text)
        assert not found, f"{module_name} có SQL thô chạm bảng của CRM {sorted(set(found))}: {text[:120]!r}"


# --- 2. Kiểm hành vi: chạy thật rồi đếm lại toàn schema ----------------------


def _engine():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return async_sessionmaker(create_async_engine(TEST_DATABASE_URL, poolclass=NullPool))


def _count_every_table() -> dict[str, int]:
    """Đếm số dòng của MỌI bảng trong schema public.

    Không liệt kê tay danh sách bảng: bảng mới thêm sau này cũng tự động được
    canh, và đó chính là loại rò rỉ mà test này cần bắt.
    """
    import asyncio

    import sqlalchemy as sa

    async def run():
        factory = _engine()
        async with factory() as session:
            names = (
                await session.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
                        "ORDER BY tablename"
                    )
                )
            ).scalars()
            counts = {}
            for name in names:
                counts[name] = await session.scalar(sa.text(f'SELECT count(*) FROM "{name}"'))
            return counts

    return asyncio.run(run())


def _seed(area_names=("A1",)):
    """Dựng project + phân khu + ba bản ghi lô, KHÔNG đụng dữ liệu của module khác.

    Trả về dict template → file_id. Các bản ghi `upload_files` được tạo TRƯỚC khi
    chụp ảnh số dòng, nên biến động của bảng đó không lẫn vào phép so sánh.
    """
    import asyncio
    from datetime import UTC, date, datetime

    import sqlalchemy as sa

    from src.models.tables import areas, upload_files

    file_ids = {name: uuid.uuid4() for name in ("sales", "inventory", "areas")}

    async def seed():
        factory = _engine()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO projects (id, name, launch_date, created_at) "
                        "VALUES (:i, :n, :d, :t) ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "i": PROJECT_ID,
                        "n": "BOUNDARY-FIXTURE",
                        "d": date(2026, 1, 1),
                        "t": datetime.now(UTC),
                    },
                )
                for area_name in area_names:
                    await session.execute(
                        sa.insert(areas).values(
                            id=uuid.uuid4(),
                            project_id=PROJECT_ID,
                            area_name=area_name,
                            unit_type="2PN",
                            bedrooms=2,
                            area_sqm=75,
                            total_units=100,
                            created_at=datetime.now(UTC),
                        )
                    )
                for template, file_id in file_ids.items():
                    await session.execute(
                        sa.insert(upload_files).values(
                            id=file_id,
                            project_id=PROJECT_ID,
                            uploaded_by=None,
                            filename=f"{template}.csv",
                            checksum=f"boundary-{template}",
                            status="pending",
                            rows_ok=0,
                            rows_failed=0,
                            uploaded_at=datetime.now(UTC),
                        )
                    )

    asyncio.run(seed())
    return {k: str(v) for k, v in file_ids.items()}


def _cleanup():
    """Xoá đúng dữ liệu của module này, theo thứ tự khoá ngoại."""
    import asyncio

    import sqlalchemy as sa

    from src.models.tables import absorption_daily, areas, inventory_snapshots, sales_records, upload_errors

    async def run():
        factory = _engine()
        async with factory() as session:
            async with session.begin():
                area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID).scalar_subquery()
                file_ids = sa.text("SELECT id FROM upload_files WHERE project_id = :p").bindparams(p=PROJECT_ID)
                await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(file_ids)))
                for table in (absorption_daily, sales_records, inventory_snapshots):
                    await session.execute(sa.delete(table).where(table.c.area_id.in_(area_ids)))
                await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
                await session.execute(sa.text("DELETE FROM upload_files WHERE project_id = :p"), {"p": PROJECT_ID})
                await session.execute(sa.text("DELETE FROM projects WHERE id = :p"), {"p": PROJECT_ID})

    asyncio.run(run())


@pytest.fixture
def db_env(monkeypatch):
    """Trỏ engine ứng dụng vào DB test, dọn dữ liệu của module sau khi xong."""
    import src.db as db_module
    from src.config import get_settings

    caches = (db_module.get_engine, db_module.get_session_factory, get_settings)

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in caches:
        cached.cache_clear()
    try:
        yield
        _cleanup()
    finally:
        for cached in caches:
            cached.cache_clear()


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_all_three_templates_write_only_to_the_legacy_aggregate_tables(tmp_path, db_env):
    """Chạy CẢ BA template rồi đếm lại toàn schema.

    Đây là bằng chứng hành vi cho ranh giới: bảng nào tăng dòng phải nằm trong
    danh sách cho phép, và ba bảng của CRM phải đứng yên tuyệt đối.
    """
    from src.jobs.parse_upload import run_parse_upload

    file_ids = _seed()
    before = _count_every_table()

    sales = _write_csv(
        tmp_path / "sales.csv",
        [
            ["Phân khu", "Loại căn", "Ngày bán", "Số căn bán", "Mã bản ghi"],
            ["A1", "2PN", "2026-03-02", "3", "BND-S1"],
        ],
    )
    inventory = _write_csv(
        tmp_path / "inventory.csv",
        [
            ["Phân khu", "Loại căn", "Ngày chốt", "Tồn kho", "Loại chốt"],
            ["A1", "2PN", "2026-03-02", "97", "closing"],
        ],
    )
    catalog = _write_csv(
        tmp_path / "areas.csv",
        [
            ["Phân khu", "Loại căn", "Số phòng ngủ", "Diện tích", "Tổng số căn"],
            ["A2", "3PN", "3", "95.5", "60"],
        ],
    )

    for template, path in (("sales", sales), ("inventory", inventory), ("areas", catalog)):
        result = run_parse_upload(
            str(path),
            template,
            file_id=file_ids[template],
            project_id=str(PROJECT_ID),
            original_filename=path.name,
        )
        assert result["status"] == "done", f"template '{template}' không nạp được: {result}"
        assert result["persisted"] is True
        assert result["rows_inserted"] == 1, f"template '{template}' không ghi được dòng nào"

    after = _count_every_table()
    delta = {name: after[name] - before[name] for name in after if after[name] != before[name]}

    # Ba bảng của CRM phải KHÔNG nhúc nhích. So sánh theo delta chứ không theo
    # tổng: module test khác chạy trước có thể đã để lại dòng trong units/deals,
    # và điều đó không liên quan gì tới đường nạp file.
    for table in sorted(CRM_OWNED_TABLES):
        assert after[table] == before[table], (
            f"đường nạp file đã ghi {after[table] - before[table]} dòng vào '{table}' — vi phạm ranh giới"
        )

    # Bảng được phép biến động: ba bảng đích, cộng bảng dẫn xuất và bảng lô.
    allowed_to_change = LEGACY_TARGET_TABLES | {"absorption_daily", "upload_files", "upload_errors"}
    unexpected = set(delta) - allowed_to_change
    assert not unexpected, f"bảng ngoài dự kiến bị thay đổi: {sorted(unexpected)} (delta={delta})"

    # Và ba bảng đích thì phải THỰC SỰ nhận được dòng — nếu không, test ở trên
    # sẽ xanh một cách vô nghĩa vì chẳng có gì được ghi cả.
    for table in sorted(LEGACY_TARGET_TABLES):
        assert delta.get(table, 0) >= 1, f"'{table}' không nhận được dòng nào; test ranh giới trở nên rỗng"


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="Cần TEST_DATABASE_URL trỏ vào Postgres thật")
def test_legacy_calculator_writes_absorption_without_touching_crm_tables(tmp_path, db_env):
    """Bộ tính cũ chạy sau khi nạp cũng không được chạm vào units/deals."""
    import asyncio

    import sqlalchemy as sa

    from src.jobs.parse_upload import run_parse_upload
    from src.models.tables import absorption_daily, areas

    file_ids = _seed()
    before = _count_every_table()

    path = _write_csv(
        tmp_path / "sales.csv",
        [
            ["Phân khu", "Loại căn", "Ngày bán", "Số căn bán", "Mã bản ghi"],
            ["A1", "2PN", "2026-03-02", "2", "BND-C1"],
            ["A1", "2PN", "2026-03-04", "1", "BND-C2"],
        ],
    )
    result = run_parse_upload(
        str(path), "sales", file_id=file_ids["sales"], project_id=str(PROJECT_ID), original_filename="sales.csv"
    )
    assert result["rows_inserted"] == 2

    async def read_absorption():
        factory = _engine()
        async with factory() as session:
            area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID).scalar_subquery()
            return (
                (
                    await session.execute(
                        sa.select(absorption_daily)
                        .where(absorption_daily.c.area_id.in_(area_ids))
                        .order_by(absorption_daily.c.stat_date)
                    )
                )
                .mappings()
                .all()
            )

    rows = asyncio.run(read_absorption())
    assert rows, "bộ tính cũ không sinh ra dòng absorption_daily nào"

    # Bộ tính cũ đọc `sales_records`, không biết gì về từng căn — nên cột tồn kho
    # theo căn phải để NULL chứ không được điền 0.
    assert all(row["units_remaining"] is None for row in rows), (
        "bộ tính cũ điền units_remaining — nó không có dữ liệu từng căn để tính số đó"
    )

    after = _count_every_table()
    for table in sorted(CRM_OWNED_TABLES):
        assert after[table] == before[table], f"bộ tính cũ đã ghi vào '{table}'"
