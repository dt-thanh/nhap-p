"""Ảnh chụp số dòng + checksum của mọi bảng, để so sánh trước/sau một thay đổi.

    python -m scripts.baseline_dev_data                    # in JSON ra stdout
    python -m scripts.baseline_dev_data --write docs/baselines/dev_0007.json
    python -m scripts.baseline_dev_data --compare docs/baselines/dev_0007.json

Vì sao cần: giai đoạn tới sẽ thêm bảng, thêm cột và thêm một bộ tính thứ hai
chạy song song với bộ tính cũ. Câu hỏi "đường nạp file cũ có bị động vào không"
chỉ trả lời được nếu có một mốc so sánh chụp TRƯỚC khi thay đổi. `--compare`
biến câu hỏi đó thành một lệnh chạy được, thay vì một niềm tin.

Ba quyết định đáng giải thích:

1. **Checksum tính trên tập cột được CHỌN, không phải `row::text`.** Thêm một cột
   mới (kể cả cột NULL toàn bộ) sẽ đổi `row::text` của mọi dòng, khiến baseline
   báo động giả sau mỗi migration cộng thêm. Ghi kèm danh sách cột vào file để
   lần so sánh sau dùng đúng tập cột đó, nên "thêm cột" hiện ra thành một thay
   đổi SCHEMA rõ ràng chứ không giả trang thành thay đổi DỮ LIỆU.

2. **Sắp xếp tất định trước khi băm.** `string_agg` không có ORDER BY thì thứ tự
   do kế hoạch truy vấn quyết định, và cùng một dữ liệu sẽ cho hai checksum khác
   nhau giữa hai lần chạy. Sắp theo chính chuỗi giá trị nên không cần bảng nào
   phải có cột id.

3. **Không tự kết nối bằng DSN rời.** Dùng `src.db.get_engine()` để script luôn
   trỏ đúng database mà ứng dụng đang trỏ, kể cả khi DATABASE_URL bị ghi đè.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from src.db import get_engine

# Bảng ghi trạng thái migration, không phải dữ liệu nghiệp vụ. Nó ĐƯỢC PHÉP đổi
# khi chạy migration nên để trong baseline sẽ gây nhiễu; revision được ghi riêng
# ở phần `meta` bên dưới.
EXCLUDED_TABLES = frozenset({"alembic_version"})


async def _table_names(conn: sa.Connection) -> list[str]:
    rows = await conn.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))
    return [name for (name,) in rows if name not in EXCLUDED_TABLES]


async def _column_names(conn: sa.Connection, table: str) -> list[str]:
    rows = await conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t ORDER BY column_name"
        ),
        {"t": table},
    )
    return [name for (name,) in rows]


async def _snapshot_table(conn: sa.Connection, table: str, columns: list[str] | None = None) -> dict[str, Any]:
    """Số dòng + checksum của một bảng, trên tập cột chỉ định (mặc định: tất cả).

    `columns` được truyền vào khi so sánh với một baseline cũ: dùng lại đúng tập
    cột của lần chụp trước thì cột mới thêm sẽ không làm lệch checksum.
    """
    available = await _column_names(conn, table)
    if columns is None:
        columns = available

    missing = [c for c in columns if c not in available]
    if missing:
        return {"rows": None, "checksum": None, "columns": columns, "error": f"thiếu cột: {missing}"}

    quoted = ", ".join(f'"{c}"' for c in columns)
    row_expr = f"ROW({quoted})::text"
    count, checksum = (
        await conn.execute(
            sa.text(
                f"SELECT count(*), coalesce(md5(string_agg({row_expr}, chr(10) ORDER BY {row_expr})), "  # noqa: S608
                f"'EMPTY') FROM \"{table}\""
            )
        )
    ).one()

    return {"rows": count, "checksum": checksum, "columns": columns}


async def snapshot(columns_by_table: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Ảnh chụp toàn schema. Trả về dict tuần tự hoá được sang JSON."""
    engine = get_engine()
    async with engine.connect() as conn:
        revision = await conn.scalar(sa.text("SELECT version_num FROM alembic_version"))
        database = await conn.scalar(sa.text("SELECT current_database()"))

        tables = {}
        for table in await _table_names(conn):
            wanted = columns_by_table.get(table) if columns_by_table else None
            tables[table] = await _snapshot_table(conn, table, wanted)

    await engine.dispose()
    return {
        "meta": {
            "database": database,
            "alembic_revision": revision,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        "tables": tables,
    }


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Danh sách khác biệt giữa hai ảnh chụp. Rỗng = giống hệt."""
    findings: list[str] = []

    old_rev = baseline["meta"]["alembic_revision"]
    new_rev = current["meta"]["alembic_revision"]
    if old_rev != new_rev:
        findings.append(f"alembic revision: {old_rev} -> {new_rev}")

    old_tables, new_tables = baseline["tables"], current["tables"]

    for table in sorted(set(new_tables) - set(old_tables)):
        findings.append(f"bảng MỚI: {table} ({new_tables[table]['rows']} dòng)")
    for table in sorted(set(old_tables) - set(new_tables)):
        findings.append(f"bảng BỊ XOÁ: {table}")

    for table in sorted(set(old_tables) & set(new_tables)):
        old, new = old_tables[table], new_tables[table]
        if new.get("error"):
            findings.append(f"{table}: {new['error']}")
            continue
        if old["rows"] != new["rows"]:
            findings.append(f"{table}: số dòng {old['rows']} -> {new['rows']}")
        elif old["checksum"] != new["checksum"]:
            findings.append(f"{table}: số dòng không đổi ({new['rows']}) nhưng NỘI DUNG đã đổi")

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", metavar="PATH", help="ghi ảnh chụp ra file JSON")
    parser.add_argument("--compare", metavar="PATH", help="so với một ảnh chụp đã lưu; khác nhau thì trả mã 1")
    args = parser.parse_args(argv)

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        # Dùng lại ĐÚNG tập cột của baseline: cột mới thêm bởi migration không
        # được phép giả trang thành thay đổi dữ liệu.
        columns_by_table = {
            name: entry["columns"] for name, entry in baseline["tables"].items() if entry.get("columns")
        }
        current = asyncio.run(snapshot(columns_by_table))

        findings = compare(baseline, current)
        if not findings:
            print(f"KHỚP — {len(current['tables'])} bảng giống hệt baseline {args.compare}")
            return 0
        print(f"KHÁC BIỆT so với {args.compare}:")
        for line in findings:
            print(f"  - {line}")
        return 1

    result = asyncio.run(snapshot())
    payload = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)

    if args.write:
        path = Path(args.write)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        total = sum(t["rows"] or 0 for t in result["tables"].values())
        print(
            f"đã ghi {path} — {len(result['tables'])} bảng, {total} dòng, revision {result['meta']['alembic_revision']}"
        )
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
