"""Đẩy tự động các dòng `crm_outbox` còn "cần gửi" (Phase C.5).

Bài toán trước C.5: đẩy đồng bộ (ngay trong request, ngay sau commit — xem
`sync_client.py`/`crud.deliver()`) là nguyên thuỷ Phase 4 và VẪN giữ nguyên ở đây,
không đổi — nó cho phản hồi CRUD biết ngay `sent`/`sync_failed` mà không cần chờ
một vòng nền. Cái thiếu là SAU một lần hỏng, không ai thử lại nếu người vận hành
không tự tay bấm `/outbox/{id}/resend`; và với bốn thực thể v2 (`projects`/`areas`/
`units_v2`/`deals_v2`), Phase C chỉ GHI outbox — Phase C trước đây không có đường
gửi nào cả (backend chưa nhận v2). Module này thêm đúng MỘT vòng nền, đọc lại
CHÍNH những dòng outbox đã có — không ghi dòng mới, không đổi payload, không dựng
đường gửi riêng — và gọi lại ĐÚNG `crud.deliver()` mà đường đồng bộ v1 đã dùng.

Đủ điều kiện relay lại một dòng: `http_status IS NULL` (chưa từng có phản hồi —
lỗi truyền tải, hoặc một phong bì v2 chưa từng được gửi) HOẶC `http_status >= 500`
(backend lỗi phía nó, không phải lỗi hợp đồng). Một dòng có `http_status` 2xx hoặc
4xx là ngõ cụt vĩnh viễn: 2xx nghĩa là đã tới nơi, 4xx nghĩa là backend đã NHÌN
đúng payload này và từ chối nó — payload không đổi khi gửi lại (nguyên văn, từ cột
`payload`) nên gửi lại chỉ tái tạo đúng lỗi cũ. Đây chính là ranh giới "retryable:
mạng/5xx/timeout — non-retryable: 4xx" của đặc tả Phase C.5, thực thi bằng CÁCH
CHỌN dòng chứ không phải bằng một bộ đếm lần thử riêng — nên không cần một cột mới
nào, `attempts`/`http_status`/`last_error` đã có từ Phase 4 là đủ.

KHÔNG khoá hàng ở tầng DB (không `FOR UPDATE SKIP LOCKED`): vòng nền chỉ chạy ĐƠN
LUỒNG trong một tiến trình — một `asyncio.Task`, xử lý từng dòng TUẦN TỰ (`await`
xong dòng này, ghi outbox, đóng dấu mirrored, rồi mới sang dòng kế) — nên hai lần
gửi cùng một dòng trong CÙNG một tiến trình là không thể xảy ra bằng cấu trúc, không
cần khoá. Đây là giới hạn ĐÃ BIẾT, ghi lại tường minh: nếu Mini CRM chạy nhiều tiến
trình song song sau này, khoá hàng thật (`FOR UPDATE SKIP LOCKED`) sẽ cần thêm.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
import sqlalchemy as sa
from app import crud, sync_client
from app.config import get_settings
from app.db import get_session_factory
from app.models import crm_areas, crm_deals, crm_outbox, crm_projects, crm_units

logger = logging.getLogger("minicrm.relay")

# Nhãn outbox → bảng nghiệp vụ để đóng dấu mirrored sau khi gửi thành công. Bốn
# nhãn v2 trỏ vào ĐÚNG bảng vật lý mà v1 dùng cho unit/deal — `units_v2`/`deals_v2`
# chỉ khác NHÃN OUTBOX (cố ý, xem `sync_client.V2_CAPTURE_ENTITIES`), không khác
# bảng lưu.
_TABLE_FOR_ENTITY: dict[str, sa.Table] = {
    "units": crm_units,
    "deals": crm_deals,
    "units_v2": crm_units,
    "deals_v2": crm_deals,
    "projects": crm_projects,
    "areas": crm_areas,
}


@dataclass(frozen=True, slots=True)
class RelayRowResult:
    external_batch_id: str
    entity: str
    status: str
    http_status: int | None


@dataclass(frozen=True, slots=True)
class RelayTickResult:
    picked_up: int
    delivered: int
    failed: int
    rows: list[RelayRowResult] = field(default_factory=list)


def _pending_query(limit: int) -> Any:
    return (
        sa.select(crm_outbox)
        .where(sa.or_(crm_outbox.c.http_status.is_(None), crm_outbox.c.http_status >= 500))
        .order_by(crm_outbox.c.created_at.asc())
        .limit(limit)
    )


async def relay_tick(*, limit: int | None = None, client: httpx.AsyncClient | None = None) -> RelayTickResult:
    """Một lượt: tìm tối đa `limit` dòng đủ điều kiện, gửi TUẦN TỰ, trả kết quả.

    Gọi lại `crud.deliver()` — CÙNG hàm mà đường đồng bộ v1 gọi ngay sau commit —
    nên một dòng được relay ghi outbox và đóng dấu mirrored theo ĐÚNG quy tắc đã
    có (kể cả `GREATEST` chống lùi `mirrored_revision`), không có logic mirror thứ
    hai nào sống song song.
    """
    settings = get_settings()
    batch_limit = limit if limit is not None else settings.relay_batch_size

    async with get_session_factory()() as session:
        rows = (await session.execute(_pending_query(batch_limit))).mappings().all()

    results: list[RelayRowResult] = []
    delivered = 0
    failed = 0
    owns_client = client is None
    # `sync_client.http_client()`, không `httpx.AsyncClient(...)` trực tiếp — đây
    # là ĐIỂM DUY NHẤT test vá được (xem docstring `http_client()`); tạo client
    # thẳng ở đây sẽ đi vòng qua bản vá và bắn request thật ra ngoài trong test.
    http = client or sync_client.http_client(settings.sync_timeout_seconds)
    try:
        for row in rows:
            outcome = await crud.deliver(
                row["id"],
                row["payload"],
                entity=row["entity"],
                table=_TABLE_FOR_ENTITY.get(row["entity"]),
                client=http,
            )
            if outcome.status in ("synced", "replayed"):
                delivered += 1
            else:
                failed += 1
            results.append(
                RelayRowResult(
                    external_batch_id=row["external_batch_id"],
                    entity=row["entity"],
                    status=outcome.status,
                    http_status=outcome.http_status,
                )
            )
    finally:
        if owns_client:
            await http.aclose()

    return RelayTickResult(picked_up=len(rows), delivered=delivered, failed=failed, rows=results)


class RelayLoop:
    """Vòng nền: ngủ, chạy một lượt, lặp lại. Sống trong vòng đời của app (lifespan).

    Ngủ TRƯỚC lượt đầu tiên, không tick ngay lúc khởi động — chủ đích: một số test
    dùng `with TestClient(app) as c:` (kích hoạt lifespan) TRƯỚC khi trỏ biến môi
    trường vào database test (xem `tests/test_health.py`); một truy vấn DB ngay
    tại startup sẽ ném lỗi kết nối không liên quan gì tới thứ test đó đang kiểm.
    Một chu kỳ ngủ đủ dài (mặc định 5s) để mọi `TestClient` ngắn hạn thoát ra
    trước khi lượt đầu tiên chạm DB; container thật (E2E) chờ đúng một chu kỳ rồi
    tự relay, không cần thao tác gì thêm.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _run(self, interval: float) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except TimeoutError:
                pass
            try:
                await relay_tick()
            except Exception:  # pragma: no cover - một lượt hỏng không được giết vòng lặp
                logger.exception("relay tick thất bại — vòng lặp tiếp tục ở lượt kế")

    def start(self) -> None:
        settings = get_settings()
        if not settings.relay_enabled or self._task is not None:
            return
        # `Event()` MỚI mỗi lần start, không tái dùng cái cũ đã `clear()`: asyncio
        # gắn một `Event`/`Task` vào VÒNG LẶP đang chạy khi nó được `await` lần
        # đầu. Vòng đời app (test dùng `with TestClient(app) as c:`) có thể
        # start()/stop() nhiều lần trên NHIỀU vòng lặp khác nhau (mỗi lần vào/ra
        # context là một vòng lặp mới) — tái dùng object cũ ném
        # "Event is bound to a different event loop" ở lần start() thứ hai.
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(settings.relay_interval_seconds))

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        task.cancel()
        # `RuntimeError`/`CancelledError` ở đây là artefact CỦA HẠ TẦNG TEST, không
        # phải một lỗi cần biết: `TestClient(app)` dùng như context manager mở một
        # portal (luồng + vòng lặp asyncio RIÊNG) MỖI LẦN vào `with`, rồi huỷ nó khi
        # thoát ra — với một singleton sống XUYÊN NHIỀU `with` liên tiếp (`relay_loop`
        # ở module này), `Task` đôi khi bị huỷ portal trước khi kịp `await` sạch,
        # ném "await wasn't used with future". Best-effort dừng: nuốt lỗi, không để
        # nó làm hỏng việc dọn dẹp app/test khác — sản phẩm thật chỉ có MỘT vòng lặp
        # asyncio suốt vòng đời container, không rơi vào tình huống này.
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await task


relay_loop = RelayLoop()
