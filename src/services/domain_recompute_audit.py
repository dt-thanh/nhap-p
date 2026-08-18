"""Kiểm tra định kỳ: lô đồng bộ nào đã commit mà thiếu lần tính lại lineage miền.

Đây là lưới an toàn cho cửa sổ sự cố giữa `COMMIT` và `enqueue` — xem
`docs/crm/domain_recompute_operations.md` mục 2. Không cột nào trong database ghi
lại rằng còn nợ một lần tính lại, nên việc phát hiện phải suy ra từ dấu vết đã có.

Logic nằm ở tầng service (chứ không nằm trong script như Phase 7) vì bây giờ có
HAI phía gọi: script CLI chạy một lần rồi thoát, và job định kỳ chạy trong tiến
trình worker sống lâu. Hai môi trường đó khác nhau ở đúng một điểm quan trọng:

    **Module này KHÔNG BAO GIỜ gọi `engine.dispose()`.**

`get_engine()` có `lru_cache`, nên engine là của DÙNG CHUNG. Ở CLI, dispose là vô
hại vì tiến trình chết ngay sau đó. Trong worker thì dispose sẽ đóng pool mà job
khác đang dùng — SQLAlchemy dựng lại pool nên không lỗi ngay, nhưng mọi kết nối
đang mở bị cắt và cái giá đó trả bằng những lỗi lác đác không tài nào truy được.
Vòng đời engine là việc của tiến trình, không phải của service.

**Sửa xong vẫn báo động.** `audit(repair=True)` xếp lại hàng cho những dự án lạc
hậu, nhưng cảnh báo vẫn phát ra kể cả khi sửa thành công. Sửa im lặng sẽ khiến
một đường `enqueue` hỏng vĩnh viễn trông y như một hệ thống khoẻ mạnh: mỗi lô đều
lỡ, mỗi lần kiểm đều vá, và không ai biết cửa sổ sự cố đã thành trạng thái thường
trực.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa

from src.db import get_engine
from src.logging_config import get_logger
from src.services.calculators import CALCULATOR_DOMAIN

log = get_logger("src.services.domain_recompute_audit")

# Trạng thái lô đã KẾT THÚC. Lô đang chạy chưa nợ gì cả.
TERMINAL_STATUSES = ("completed", "partially_completed")


@dataclass(frozen=True, slots=True)
class StaleProject:
    """Một dự án có lô đã áp dụng thay đổi nhưng lineage miền chưa theo kịp."""

    project_id: uuid.UUID
    project_name: str
    last_applied_sync_at: str
    last_domain_computed_at: str | None
    applied_runs: int

    @property
    def never_computed(self) -> bool:
        return self.last_domain_computed_at is None


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Kết quả một lần kiểm — dùng chung cho CLI, job định kỳ và endpoint ops."""

    stale: list[StaleProject] = field(default_factory=list)
    repaired_job_ids: list[str] = field(default_factory=list)
    repair_error: str | None = None

    @property
    def is_clean(self) -> bool:
        return not self.stale


# `projections` được ghi vào `error_summary` bởi SyncRunService. Ba khoá này là
# ba hành động thực sự đổi bản sao — giống hệt điều kiện xếp hàng, nên công cụ
# phát hiện và tầng xếp hàng không thể lệch nhau về định nghĩa "đã đổi".
_APPLIED_EXPR = """
    coalesce((uf.error_summary -> 'projections' ->> 'inserted')::int, 0)
  + coalesce((uf.error_summary -> 'projections' ->> 'updated')::int, 0)
  + coalesce((uf.error_summary -> 'projections' ->> 'tombstoned')::int, 0)
"""

STALE_QUERY = sa.text(f"""
WITH applied AS (
    SELECT uf.project_id,
           max(uf.finished_at) AS last_applied_at,
           count(*)            AS applied_runs
    FROM upload_files uf
    WHERE uf.transport_mode = 'api_push'
      AND uf.status = ANY(:terminal)
      AND uf.finished_at IS NOT NULL
      AND ({_APPLIED_EXPR}) > 0
    GROUP BY uf.project_id
),
domain AS (
    SELECT a.project_id, max(ad.computed_at) AS last_computed_at
    FROM absorption_daily ad
    JOIN areas a ON a.id = ad.area_id
    WHERE ad.calculator = :calculator
    GROUP BY a.project_id
)
SELECT p.id, p.name, applied.last_applied_at, domain.last_computed_at, applied.applied_runs
FROM applied
JOIN projects p ON p.id = applied.project_id
LEFT JOIN domain ON domain.project_id = applied.project_id
WHERE domain.last_computed_at IS NULL
   OR domain.last_computed_at < applied.last_applied_at
ORDER BY p.name
""")


async def find_stale() -> list[StaleProject]:
    """Dự án nào có lô đã áp dụng thay đổi mà lineage miền chưa theo kịp.

    CHỈ ĐỌC. Không dispose engine dùng chung — xem docstring module.
    """
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(STALE_QUERY, {"terminal": list(TERMINAL_STATUSES), "calculator": CALCULATOR_DOMAIN})
        ).all()

    return [
        StaleProject(
            project_id=row[0],
            project_name=row[1],
            last_applied_sync_at=row[2].isoformat(),
            last_domain_computed_at=row[3].isoformat() if row[3] is not None else None,
            applied_runs=row[4],
        )
        for row in rows
    ]


def enqueue_recompute(stale: list[StaleProject]) -> list[str]:
    """Xếp lại hàng cho từng dự án lạc hậu, phạm vi TOÀN dự án.

    Cố ý không thu hẹp phạm vi: đã mất dấu job cũ thì cũng không biết chắc những
    phân khu nào từng bị ảnh hưởng. Tính lại cả dự án tốn hơn nhưng đúng, và job
    là idempotent nên không có rủi ro nào ngoài thời gian chạy.
    """
    from src.task_queue import INGEST_QUEUE, get_queue

    queue = get_queue(INGEST_QUEUE)
    job_ids = []
    for project in stale:
        job = queue.enqueue(
            "src.jobs.recompute_domain.run_domain_recompute",
            project_id=str(project.project_id),
            area_ids=None,
            sync_run_id=None,
        )
        job_ids.append(job.id)
    return job_ids


async def audit(*, repair: bool = True) -> AuditResult:
    """Kiểm, (tuỳ chọn) sửa, và LUÔN báo động nếu có gì lạc hậu.

    Thứ tự cố ý: báo động trước, sửa sau. Nếu bước xếp hàng chính là thứ đang
    hỏng thì lần sửa cũng sẽ hỏng, và ta vẫn phải có bản ghi rằng đã phát hiện.
    """
    stale = await find_stale()

    if not stale:
        log.info("domain.recompute.audit_clean")
        return AuditResult()

    # Mức `error`: đây là tín hiệu cảnh báo chính. Kèm project_id để người trực
    # xếp lại hàng bằng tay được ngay mà không cần tra thêm.
    log.error(
        "domain.recompute.audit_stale",
        stale_projects=len(stale),
        project_ids=[str(p.project_id) for p in stale],
        never_computed=[str(p.project_id) for p in stale if p.never_computed],
    )

    if not repair:
        return AuditResult(stale=stale)

    try:
        job_ids = enqueue_recompute(stale)
    except Exception as exc:
        # Redis hỏng lúc này nghĩa là chính đường xếp hàng đang là nguyên nhân.
        # Không ném lại: lần kiểm vẫn có giá trị chẩn đoán, và cảnh báo ở trên đã
        # phát. Ném lại chỉ biến một cảnh báo rõ ràng thành một job failed.
        log.error(
            "domain.recompute.audit_repair_failed",
            stale_projects=len(stale),
            project_ids=[str(p.project_id) for p in stale],
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return AuditResult(stale=stale, repair_error=type(exc).__name__)

    log.info("domain.recompute.audit_repaired", stale_projects=len(stale), job_ids=job_ids)
    return AuditResult(stale=stale, repaired_job_ids=job_ids)
