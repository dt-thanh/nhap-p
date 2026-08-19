"""Kiểm một payload có thoả hợp đồng đồng bộ v1 không — KHÔNG ghi một dòng nào.

    python -m scripts.conformance_check payload.json
    python -m scripts.conformance_check payload.json --json
    python -m scripts.conformance_check docs/crm/fixtures/*.json --json-out report.json
    cat payload.json | python -m scripts.conformance_check -

**Nhận tệp KHÔNG phải dữ liệu tổng hợp.** Khác `sync_simulator.py` — công cụ đó
từ chối gửi bất cứ thứ gì không mang tiền tố `synthetic-`, vì nó GỬI THẬT. Công
cụ này không gửi đi đâu cả và không commit gì, nên nó nhận được payload thật của
một Mini CRM tương lai. Đó chính là lý do nó tồn tại.

**Mã thoát**

    0  đạt
    1  có vi phạm
    2  không đọc được tệp / sai cách dùng
    3  TỪ CHỐI CHẠY (đích là môi trường sản xuất)

Mã thoát 1 dùng được trong CI hoặc trong cổng kiểm tra trước khi cắt sang.

**Nó chứng minh gì.** Rằng payload ĐÃ CHO đi qua được (hoặc không đi qua được)
bốn cổng của endpoint đồng bộ. Chạy sạch trên fixture tổng hợp **không** chứng
minh tương thích với Mini CRM nào — fixture do chính ta viết. Câu hỏi đó chỉ có
payload thật mới trả lời được, và Mini CRM chưa tồn tại.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from src.db import get_engine
from src.services.conformance import ConformanceReport, ProductionRefusalError, check_payload

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3

BANNER = "*** BỘ KIỂM CHỈ ĐỌC — KHÔNG GHI, KHÔNG GỬI ĐI ĐÂU, KHÔNG COMMIT ***"


def _read(source: str) -> bytes:
    if source == "-":
        return sys.stdin.buffer.read()
    return Path(source).read_bytes()


def render(report: ConformanceReport) -> str:
    """Bản cho người đọc. Vi phạm đứng TRƯỚC phần tổng kết.

    Người chạy công cụ này đang tìm câu trả lời "sai ở đâu"; bắt họ cuộn qua một
    bảng số liệu trước mới thấy lỗi là đặt sai thứ tự ưu tiên.
    """
    lines = [BANNER, "", f"Tệp   : {report.source}", f"Lúc   : {report.checked_at}"]
    if report.dialect:
        lines.append(f"Dialect: {report.dialect}")
    if report.project_id:
        lines.append(f"Dự án : {report.project_id}")
    lines.append(f"Kích thước: {report.size_bytes} byte, {report.records_received} bản ghi")
    lines.append("")

    if report.violations:
        lines.append(f"VI PHẠM: {len(report.violations)}")
        for violation in report.violations:
            where = violation.json_path or "$"
            lines.append(f"  [{violation.gate}] {violation.error_code}  {where}")
            lines.append(f"      {violation.message}")
            if violation.source_record_id:
                lines.append(f"      bản ghi: {violation.source_record_id}")
        lines.append("")

    passed = ", ".join(report.gates_passed) or "(không cổng nào)"
    lines.append(f"Cổng đã chạy : {', '.join(report.gates_run) or '(không)'}")
    lines.append(f"Cổng đã qua  : {passed}")

    if report.decisions:
        lines.append("")
        lines.append("Nếu nạp thật thì sẽ:")
        lines.append("  quyết định : " + ", ".join(f"{k}={v}" for k, v in report.decisions.items() if v))
        lines.append("  bản sao    : " + ", ".join(f"{k}={v}" for k, v in report.projections.items() if v))

    if report.database_untouched is not None:
        state = "KHÔNG ĐỔI (đã kiểm bằng số dòng)" if report.database_untouched else "ĐÃ BỊ ĐỔI — LỖI CỦA BỘ KIỂM"
        lines.append(f"Database     : {state}")

    for note in report.notes:
        lines.append(f"Lưu ý        : {note}")

    lines.append("")
    lines.append("ĐẠT" if report.conforms else "KHÔNG ĐẠT")
    lines.append("")
    lines.append(
        "Kết quả này KHÔNG chứng minh tương thích với Mini CRM nào — Mini CRM chưa tồn tại,\n"
        "và fixture tổng hợp do chính hệ thống này viết ra."
    )
    return "\n".join(lines)


async def _run(sources: list[str]) -> list[ConformanceReport]:
    reports = []
    try:
        for source in sources:
            reports.append(await check_payload(_read(source), source=source))
    finally:
        # Tiến trình một-lần-rồi-thoát: đóng pool ở đây, không phải trong service
        # (service còn được gọi từ tiến trình sống lâu).
        await get_engine().dispose()
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("payload", nargs="+", help="tệp JSON cần kiểm ('-' để đọc stdin)")
    parser.add_argument("--json", action="store_true", help="chỉ in JSON ra stdout (dùng để pipe)")
    parser.add_argument("--json-out", metavar="FILE", help="ghi báo cáo JSON ra tệp, vẫn in bản cho người đọc")
    args = parser.parse_args(argv)

    try:
        reports = asyncio.run(_run(args.payload))
    except ProductionRefusalError as exc:
        print(f"TỪ CHỐI: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, ValueError) as exc:
        print(f"Không đọc được payload: {exc}", file=sys.stderr)
        return EXIT_USAGE

    machine = [report.as_dict() for report in reports]

    if args.json:
        print(json.dumps(machine if len(machine) > 1 else machine[0], ensure_ascii=False, indent=2))
    else:
        print(("\n\n" + "-" * 72 + "\n\n").join(render(report) for report in reports))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(machine if len(machine) > 1 else machine[0], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return EXIT_OK if all(report.conforms for report in reports) else EXIT_VIOLATIONS


if __name__ == "__main__":
    sys.exit(main())
