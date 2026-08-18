"""Sổ gửi đi: tra, gửi lại, và phát lại bản cũ.

Ba đường này tồn tại vì cùng một lý do: một lô đã gửi phải KIỂM CHỨNG ĐƯỢC sau
đó, không chỉ được mô tả. Hai kịch bản quan trọng nhất của Phase 4 — "gửi lại
đúng lô cũ" và "phát lại một bản cũ" — chỉ thành thao tác thật khi payload nguyên
văn còn nằm đâu đó để gửi lại.

Phân biệt hai đường, vì tên chúng nghe giống nhau nhưng kiểm hai chốt khác hẳn:

    POST /outbox/{id}/resend    ĐÚNG batch id cũ  → backend trả kết quả đã lưu,
                                replayed=true, không bản ghi nghiệp vụ thứ hai
                                ⇒ kiểm tính bất biến của LÔ

    POST /outbox/replay-stale   batch id MỚI, payload và revision CŨ
                                → backend so phiên bản và bỏ qua: skip_stale
                                ⇒ kiểm thứ tự PHIÊN BẢN

Dùng lại batch id cũ cho đường thứ hai sẽ khiến nó không bao giờ chạm tới tầng so
phiên bản, và phép thử `skip_stale` sẽ luôn "đạt" mà chẳng kiểm gì.

Xác thực GHI (D-14): hai route POST (gửi lại thật, kích hoạt HTTP ra ngoài) đòi
tối thiểu `pipeline_operator` — KHÔNG kiểm phạm vi dự án chi tiết ở đây (một dòng
outbox có thể chứa nhiều bản ghi thuộc nhiều dự án, và cả hai route đều là công
cụ VẬN HÀNH/gỡ lỗi, không phải đường ghi nghiệp vụ chính), đơn giản hoá có chủ
đích, ghi lại trong `pipeline_status.md`. Hai route GET giữ NGUYÊN mở, khớp
"read behavior preserved".
"""

from __future__ import annotations

from app import crud
from app.auth import require_role
from app.schemas import OutboxListOut, OutboxOut, ReplayStaleIn, ResendOut
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/outbox", tags=["outbox"])


def _summary(row: dict) -> OutboxOut:
    """Dòng outbox rút gọn — KHÔNG kèm `payload`/`response`.

    Một lô có thể mang tới 5000 bản ghi. Kèm payload vào danh sách sẽ biến một
    lần tra trạng thái thành vài megabyte JSON, và người vận hành sẽ thôi tra.
    """
    return OutboxOut(
        external_batch_id=row["external_batch_id"],
        entity=row["entity"],
        http_status=row["http_status"],
        sent_at=row["sent_at"],
        created_at=row["created_at"],
        attempts=row["attempts"],
        last_error=row["last_error"],
        replay_of=row["replay_of"],
        record_count=len((row["payload"] or {}).get("records", [])),
    )


@router.get("", response_model=OutboxListOut, summary="Liệt kê lô đã gửi (mới nhất trước)")
async def list_outbox(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    entity: str | None = Query(default=None, description="Lọc theo units/deals"),
) -> OutboxListOut:
    rows, total = await crud.list_outbox(limit=limit, offset=offset, entity=entity)
    return OutboxListOut(items=[_summary(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{external_batch_id}", response_model=OutboxOut, summary="Payload và phản hồi NGUYÊN VĂN của một lô")
async def get_outbox(external_batch_id: str) -> OutboxOut:
    row = await crud.get_outbox(external_batch_id)
    out = _summary(row)
    out.payload = row["payload"]
    out.response = row["response"]
    return out


@router.post(
    "/replay-stale",
    response_model=ResendOut,
    summary="Phát lại một payload CŨ dưới batch id MỚI",
    dependencies=[Depends(require_role("pipeline_operator"))],
)
async def replay_stale(body: ReplayStaleIn | None = None) -> ResendOut:
    """Kỳ vọng: backend trả `skip_stale`, và trạng thái hiện tại KHÔNG bị ghi đè.

    Bỏ trống body thì Mini CRM tự chọn lô mới nhất mà mọi bản ghi trong đó đã bị
    ghi đè bằng phiên bản cao hơn. Lô được chỉ định mà KHÔNG cũ thật thì bị từ
    chối bằng 409 `BATCH_NOT_STALE` — phát lại nó sẽ cho `duplicate_noop` hoặc
    `conflict`, và một phép thử tự khẳng định kết quả mà không kiểm tiền đề thì
    chứng minh được số không.
    """
    source_batch_id, outcome = await crud.replay_stale(body.external_batch_id if body else None)
    return ResendOut(
        source_batch_id=source_batch_id,
        sent_batch_id=outcome.external_batch_id,
        mode="replay_stale",
        sync=outcome.as_dict(),
    )


@router.post(
    "/{external_batch_id}/resend",
    response_model=ResendOut,
    summary="Gửi lại NGUYÊN VĂN lô cũ",
    dependencies=[Depends(require_role("pipeline_operator"))],
)
async def resend(external_batch_id: str) -> ResendOut:
    """Kỳ vọng: backend trả `replayed=true` và KHÔNG tạo bản ghi nghiệp vụ thứ hai.

    Đây cũng là đường phục hồi cho một lần đẩy hỏng: dữ liệu cục bộ đã commit,
    dòng outbox còn nguyên payload, nên gửi lại là một thao tác TƯỜNG MINH do
    người vận hành quyết định — không có vòng lặp retry ngầm nào ở đây.
    """
    outcome = await crud.resend(external_batch_id)
    return ResendOut(
        source_batch_id=external_batch_id,
        sent_batch_id=outcome.external_batch_id,
        mode="resend",
        sync=outcome.as_dict(),
    )
