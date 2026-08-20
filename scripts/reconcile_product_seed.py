"""Đối chiếu seed/demo data của Product DB với danh tính nguồn Mini CRM (CP1).

    python -m scripts.reconcile_product_seed --dry-run          # MẶC ĐỊNH
    python -m scripts.reconcile_product_seed --dry-run --json
    python -m scripts.reconcile_product_seed --apply --confirm  # mới thực sự ghi

**Script này MẶC ĐỊNH không ghi gì.** `--apply` một mình cũng chưa đủ — phải kèm
`--confirm`. Lý do: nó chạm vào dữ liệu vận hành mà `ranking`/`forecast` đang
tham chiếu, và một lần chạy nhầm trên database thật không rollback lại được bằng
cách chạy lại. Hai cờ là một ma sát CÓ CHỦ ĐÍCH.

Ngay cả ở chế độ `--apply`, script chỉ làm MỘT việc: gắn cờ `needs_review` vào
báo cáo. Nó **không** xoá bản ghi, **không** tự gộp hai dự án nghi trùng, và
**không** bịa `external_id` cho bản ghi di sản. Gộp thực thể là quyết định
nghiệp vụ — máy không đoán thay được, xem `docs/integration/canonical_ids.md` §4.

Phân loại đầu ra (khớp §4 của tài liệu trên):

  owned_by_minicrm  — `source_system='mini_crm'`, có `external_id`: đồng bộ quản lý.
  legacy_no_identity — `external_id IS NULL`: nằm ngoài phạm vi đồng bộ.
  duplicate_suspect  — một bản ghi di sản trùng TÊN với một bản ghi Mini CRM.
  foreign_instance   — `external_id` trùng nhưng `source_instance_id` khác.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from src.db import get_session_factory
from src.models.tables import areas, projects, units

_TABLES = {"projects": projects, "areas": areas, "units": units}


@dataclass
class Report:
    owned_by_minicrm: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    legacy_no_identity: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    duplicate_suspect: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    foreign_instance: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))

    def as_dict(self) -> dict[str, Any]:
        return {
            "owned_by_minicrm": {k: v for k, v in self.owned_by_minicrm.items()},
            "legacy_no_identity": {k: v for k, v in self.legacy_no_identity.items()},
            "duplicate_suspect": {k: v for k, v in self.duplicate_suspect.items()},
            "foreign_instance": {k: v for k, v in self.foreign_instance.items()},
        }

    @property
    def needs_review(self) -> bool:
        return bool(
            any(self.duplicate_suspect.values()) or any(self.foreign_instance.values())
        )


async def audit(expected_instance: str | None) -> Report:
    report = Report()
    factory = get_session_factory()
    async with factory() as session:
        for name, table in _TABLES.items():
            cols = [table.c.id, table.c.external_id, table.c.source_system, table.c.source_instance_id]
            has_name = "name" in table.c
            if has_name:
                cols.append(table.c.name)
            rows = (await session.execute(sa.select(*cols))).mappings().all()

            named_minicrm: dict[str, str] = {}
            legacy_named: list[tuple[str, str]] = []

            for row in rows:
                ext = row["external_id"]
                label = row.get("name") if has_name else None
                if ext is None:
                    report.legacy_no_identity[name].append(str(row["id"]))
                    if label:
                        legacy_named.append((str(row["id"]), label))
                    continue

                report.owned_by_minicrm[name].append(ext)
                if label:
                    named_minicrm[label] = ext

                if (
                    expected_instance
                    and row["source_instance_id"]
                    and row["source_instance_id"] != expected_instance
                ):
                    report.foreign_instance[name].append(
                        {
                            "id": str(row["id"]),
                            "external_id": ext,
                            "source_instance_id": row["source_instance_id"],
                        }
                    )

            # Nghi trùng: bản ghi di sản mang ĐÚNG cái tên mà một bản ghi Mini CRM
            # cũng mang. Đây là tín hiệu CẢNH BÁO, không phải bằng chứng — khớp
            # bằng tên chính là thứ tài liệu cấm dùng để LIÊN KẾT. Ở đây nó chỉ
            # dùng để CHỈ CHỖ cho con người xem, không để tự động gộp.
            for legacy_id, label in legacy_named:
                if label in named_minicrm:
                    report.duplicate_suspect[name].append(
                        {
                            "legacy_id": legacy_id,
                            "name": label,
                            "minicrm_external_id": named_minicrm[label],
                            "action": "CẦN NGƯỜI QUYẾT ĐỊNH: gộp hay giữ riêng",
                        }
                    )
    return report


def render(report: Report) -> str:
    lines = ["=== ĐỐI CHIẾU SEED DATA CỦA PRODUCT DB ===", ""]
    for table in _TABLES:
        lines.append(f"[{table}]")
        lines.append(f"  Mini CRM sở hữu       : {len(report.owned_by_minicrm.get(table, []))}")
        lines.append(f"  Di sản (không danh tính): {len(report.legacy_no_identity.get(table, []))}")
        dup = report.duplicate_suspect.get(table, [])
        foreign = report.foreign_instance.get(table, [])
        lines.append(f"  Nghi trùng             : {len(dup)}")
        lines.append(f"  Sai source_instance_id : {len(foreign)}")
        for item in dup:
            lines.append(f"    ! '{item['name']}' — di sản {item['legacy_id']} vs {item['minicrm_external_id']}")
        for item in foreign:
            lines.append(f"    ! {item['external_id']} mang instance {item['source_instance_id']}")
        lines.append("")
    lines.append(
        "CẦN NGƯỜI XEM LẠI." if report.needs_review else "Không phát hiện xung đột danh tính."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true", help="Cần kèm --confirm.")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--instance", default=None, help="source_instance_id kỳ vọng.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.apply and not args.confirm:
        print("TỪ CHỐI: --apply phải đi kèm --confirm.", file=sys.stderr)
        return 2

    report = asyncio.run(audit(args.instance))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2) if args.json else render(report))

    if args.apply and args.confirm:
        # Cố ý KHÔNG có nhánh ghi. Mọi hành động khắc phục (gộp, gán external_id,
        # xoá) đều là quyết định nghiệp vụ một-lần, phải chạy có người xem qua
        # migration Alembic tường minh — không phải một script tự động chạy lại
        # được. Giữ cờ ở đây để giao diện dòng lệnh ổn định khi migration đó ra đời.
        print("\n--apply: chưa có hành động ghi nào được cài đặt (theo thiết kế).")
        print("Xem docs/integration/canonical_ids.md §4 để biết cách xử lý bằng tay.")

    return 1 if report.needs_review else 0


if __name__ == "__main__":
    raise SystemExit(main())
