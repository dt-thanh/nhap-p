"""Phát hiện — và xếp lại hàng — những lô đồng bộ đã commit mà chưa được tính lại.

    python -m scripts.requeue_missing_domain_recompute            # chỉ báo cáo
    python -m scripts.requeue_missing_domain_recompute --enqueue  # xếp lại hàng

Từ Phase 8A, toàn bộ logic phát hiện nằm ở `src/services/domain_recompute_audit`
và được job định kỳ dùng chung. Script này còn tồn tại vì hai việc mà job không
làm được: chạy theo yêu cầu lúc đang xử lý sự cố, và trả về MÃ THOÁT để dùng
trong cổng kiểm tra trước khi cắt sang.

**Cửa sổ sự cố mà công cụ này bù đắp.** `SyncRunService` commit lô đồng bộ TRƯỚC,
rồi mới xếp hàng job tính lại. Giữa hai bước đó có một khoảng: tiến trình chết,
Redis mất kết nối, container bị OOM. Khi ấy `units`/`deals` đã đổi nhưng không có
job nào được xếp, và **không cột nào trong database ghi lại rằng còn nợ một lần
tính lại** — nên hệ thống không tự phát hiện được.

Đảo thứ tự không cứu được: xếp hàng trước khi commit thì worker có thể chạy trước
khi dữ liệu tồn tại, và tính ra lineage dựa trên trạng thái cũ. Một hàng đợi giao
dịch (outbox) sẽ đóng được cửa sổ này, nhưng đó là một bảng nữa với vòng đời
riêng; ở quy mô hiện tại, PHÁT HIỆN rẻ hơn PHÒNG NGỪA — miễn là việc phát hiện
thực sự được chạy. Từ Phase 8A thì nó ĐƯỢC chạy, hằng giờ, qua scheduler.

**An toàn khi chạy lại.** Job tính lại là idempotent (xoá-rồi-ghi, giới hạn đúng
lineage và phạm vi), nên xếp lại hàng nhiều lần không nhân đôi gì.

**YÊU CẦU TRƯỚC KHI CẮT SANG.** Ở Phase 7–8, lineage miền chưa ai đọc nên lạc hậu
là vô hại. Ngay khi một dự án chuyển sang `domain_units_deals`, khoảng trống này
biến thành SỐ LIỆU SAI hiển thị cho người dùng. Xem
`docs/crm/domain_recompute_operations.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.db import get_engine
from src.services.domain_recompute_audit import StaleProject, enqueue_recompute, find_stale


async def _find_stale_once() -> list[StaleProject]:
    """Chạy một lần rồi đóng pool.

    Dispose ở ĐÂY chứ không phải trong service: engine có `lru_cache` nên nó dùng
    chung: ở tiến trình một-lần-rồi-thoát này dispose là đúng, còn trong worker
    sống lâu thì nó sẽ cắt kết nối của job khác. Vòng đời engine là việc của tiến
    trình.
    """
    try:
        return await find_stale()
    finally:
        await get_engine().dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--enqueue", action="store_true", help="xếp lại hàng, không chỉ báo cáo")
    args = parser.parse_args(argv)

    stale = asyncio.run(_find_stale_once())

    if not stale:
        print("SẠCH — không dự án nào có lô đã áp dụng mà thiếu lần tính lại.")
        return 0

    print(f"LẠC HẬU: {len(stale)} dự án\n")
    for project in stale:
        state = "CHƯA TÍNH LẦN NÀO" if project.never_computed else f"miền tính lúc {project.last_domain_computed_at}"
        print(f"  {project.project_name} ({project.project_id})")
        print(f"      lô áp dụng gần nhất : {project.last_applied_sync_at} ({project.applied_runs} lô)")
        print(f"      lineage miền        : {state}")

    if not args.enqueue:
        print("\nChạy lại với --enqueue để xếp hàng tính lại.")
        # Mã thoát khác 0 để dùng được trong kiểm tra tự động trước khi cắt sang.
        return 1

    job_ids = enqueue_recompute(stale)
    print(f"\nĐã xếp {len(job_ids)} job tính lại: {', '.join(job_ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
