"""Đẩy một lô sang backend qua HTTP. Đây là ĐIỂM TIẾP XÚC DUY NHẤT giữa hai hệ thống.

**Một chiều, và chỉ một chiều.** Mini CRM đẩy; backend nhận. Không có đường nào từ
backend gọi ngược vào đây, và không được thêm vào — mọi phase trước đã giữ ràng
buộc đó, và nó là thứ khiến "Mini CRM chưa tồn tại" vẫn là một câu đúng về mặt kỹ
thuật cho tới đúng lúc này.

**Module này KHÔNG import gì từ `src/`.** Nó không dùng model, validator, service
hay tầng database của backend. Nó dựng một dict, kiểm bằng BẢN SAO schema của
chính mình (`app/contract.py`), ký bằng `X-API-Key`, và POST. Nếu payload sai hợp
đồng thì backend TỪ CHỐI nó — và đó chính là điều đáng kiểm.

**Không retry ngầm.** Một lần đẩy, một kết quả, ghi lại nguyên văn. Retry mù sẽ
nhân bản `external_batch_id` hoặc che mất một lỗi hợp đồng thật sau vài lần thử
lại. Phase 4 thêm nút gửi lại TƯỜNG MINH (`POST /outbox/{id}/resend`), dùng lại
đúng batch id cũ — đó là phép thử idempotency, không phải một cơ chế phục hồi.

═══════════════════════════════════════════════════════════════════════════════
 THỨ TỰ BẮT BUỘC (Phase 4): GHI → COMMIT → GỬI
═══════════════════════════════════════════════════════════════════════════════

    mở transaction
      ├─ ghi/sửa bản ghi, TĂNG source_revision
      ├─ dựng phong bì TỪ TRẠNG THÁI ĐÃ GHI
      ├─ kiểm hợp đồng (hỏng ⇒ rollback, KHÔNG gửi gì)
      └─ ghi dòng crm_outbox (http_status = NULL)
    COMMIT                     ←── không một byte nào rời khỏi máy trước điểm này
    POST  → ghi http_status + response vào đúng dòng outbox đó

`enqueue()` nhận session của PHÍA GỌI chính vì thế: dòng outbox phải nằm trong
CÙNG transaction với thay đổi nghiệp vụ. Tách ra hai transaction sẽ tạo hai lỗ
hổng đối xứng — commit dữ liệu rồi chết trước khi ghi outbox (thay đổi tồn tại mà
không ai biết phải gửi), hoặc ghi outbox rồi rollback dữ liệu (gửi đi một trạng
thái chưa từng tồn tại). Một transaction thì cả hai đều không xảy ra được.

Gửi SAU commit, không phải trong transaction, cũng vì một lý do cụ thể: gửi trong
transaction nghĩa là backend có thể đã nhận và đã chiếu xong một bản ghi mà giây
sau đó Mini CRM rollback — và không có cách nào lấy lại.

Cái giá phải trả: một lần gửi hỏng SAU commit để lại thay đổi cục bộ đã commit mà
backend chưa thấy. Đó là đánh đổi có chủ đích, và nó nhìn thấy được: dòng outbox
còn đó, `mirrored_revision` thấp hơn `source_revision`, và phản hồi CRUD trả
`sync_failed`/`sync_pending`. Không có trạng thái nào bị giấu.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa
from app import contract, contract_v2
from app.config import get_settings
from app.db import get_session_factory
from app.models import crm_outbox
from sqlalchemy.ext.asyncio import AsyncSession

# Hợp đồng v1. Backend từ chối thẳng giá trị khác, không đoán.
SCHEMA_VERSION = contract.SCHEMA_VERSION

# Thực thể → phần đường dẫn. Backend đòi số NHIỀU trên URL và số ÍT trong bản ghi
# — hai từ vựng khác nhau cho cùng một khái niệm, nên chúng nằm cạnh nhau ở đây
# thay vì rải rác trong mã.
#
# Bốn dòng dưới (`units_v2`/`deals_v2`/`projects`/`areas`) là entity NHÃN OUTBOX
# v2 (xem `V2_CAPTURE_ENTITIES`), map sang đúng URL v2 mà Phase D đã mở
# (`POST /sync/{projects,areas,units,deals}`). Có mặt Ở ĐÂY (không phải một map
# riêng) để `deliver()`/`push()` xử lý cả sáu nhãn outbox bằng ĐÚNG một đường mã —
# Phase C.5 (relay tự động) gọi lại chính `deliver()` này cho dòng v2, không dựng
# đường gửi riêng.
ENTITY_PATH = {
    "units": "units",
    "deals": "deals",
    "units_v2": "units",
    "deals_v2": "deals",
    "projects": "projects",
    "areas": "areas",
}
ENTITY_RECORD = {"units": "unit", "deals": "deal"}

SYNTHETIC_NOTICE = (
    "SYNTHETIC — Mini CRM do chính dự án này viết, KHÔNG phải CRM của khách hàng. "
    "Lô này không chứng minh khả năng tương thích với bất kỳ hệ nguồn thật nào."
)


class SyncPushError(Exception):
    """Đẩy lô thất bại. Mang theo đủ ngữ cảnh để biết hỏng ở đâu.

    `status_code is None` nghĩa là KHÔNG BIẾT backend đã nhận hay chưa (timeout,
    không nối được) — khác hẳn với "backend đã trả lời và đã từ chối".
    """

    def __init__(
        self,
        message: str,
        *,
        batch_id: str,
        status_code: int | None = None,
        detail: Any = None,
        outcome: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.batch_id = batch_id
        self.status_code = status_code
        self.detail = detail
        self._outcome = outcome

    @property
    def outcome(self) -> str:
        """`sync_failed` = chắc chắn KHÔNG có tác dụng gì ở backend. `sync_pending` = không biết.

        Hai nhánh dẫn tới `sync_failed`: backend đã trả lời và từ chối, hoặc kết
        nối chưa bao giờ mở được nên không một byte nào rời khỏi máy. `sync_pending`
        dành riêng cho trường hợp thật sự mơ hồ — request đã đi nhưng không có
        phản hồi.
        """
        if self._outcome is not None:
            return self._outcome
        return "sync_failed" if self.status_code is not None else "sync_pending"


@dataclass(frozen=True, slots=True)
class PushResult:
    """Kết quả một lần đẩy — đúng những gì backend trả về, không diễn giải thêm."""

    batch_id: str
    status_code: int
    body: dict[str, Any]

    @property
    def accepted(self) -> bool:
        """202 = lô mới được nhận. 200 = lô đã xử lý rồi, trả kết quả cũ (replay)."""
        return self.status_code in (200, 202)

    @property
    def replayed(self) -> bool:
        return bool(self.body.get("replayed"))

    @property
    def sync_run_id(self) -> str | None:
        return self.body.get("sync_run_id")

    @property
    def decisions(self) -> dict[str, int]:
        return self.body.get("decisions") or {}

    @property
    def projections(self) -> dict[str, int]:
        return self.body.get("projections") or {}


def new_batch_id(entity: str) -> str:
    """Danh tính lô, do Mini CRM đặt.

    Gửi LẠI cùng giá trị này = gửi lại cùng lô, và backend trả kết quả cũ thay vì
    xử lý lần hai. Đó là lý do nó phải sinh ở đây một lần rồi được LƯU, chứ không
    sinh lại mỗi lần gửi.
    """
    return f"mc-{entity}-{uuid.uuid4()}"


def http_client(timeout: float) -> httpx.AsyncClient:
    """Điểm DUY NHẤT tạo client HTTP đi ra ngoài.

    Tồn tại như một hàm riêng để test vá được đúng một chỗ. Vá thẳng
    `httpx.AsyncClient` sẽ đụng vào chính module httpx — mà `TestClient` của
    Starlette cũng dựng trên httpx, nên một bản vá quá rộng sẽ chặn nhầm cả đường
    gọi vào ứng dụng đang được test.
    """
    return httpx.AsyncClient(timeout=timeout)


def _now_iso() -> str:
    """ISO-8601 KÈM offset múi giờ.

    Backend từ chối mốc trần (`$defs/timestamp` đòi offset hoặc hậu tố Z) và nó
    đúng khi làm thế: một mốc không có múi giờ không nói được nó là giờ nào, mà
    đây lại là căn cứ xếp thứ tự sự kiện.
    """
    return datetime.now(UTC).isoformat()


def iso(value: Any) -> str | None:
    """datetime của DB → chuỗi ISO có offset. `None` đi qua nguyên vẹn.

    `None` KHÔNG bị bỏ đi ở đây: với bản ghi `full`, `null` tường minh là một
    khẳng định ("mốc này không còn"), còn VẮNG MẶT lại bị backend từ chối bằng
    `HISTORY_TIMESTAMP_DROPPED` nếu bản sao đang giữ giá trị. Hai thứ khác nhau,
    và Mini CRM luôn muốn nói cái thứ nhất.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # Không tự gán UTC: một mốc trần trong DB nghĩa là cột bị khai sai
            # kiểu, và đoán múi giờ hộ sẽ chôn lỗi đó xuống một tầng nữa.
            raise ValueError(f"mốc thời gian không có múi giờ: {value!r} — cột phải là TIMESTAMPTZ")
        return value.isoformat()
    return str(value)


def _envelope(records: list[dict[str, Any]], *, batch_id: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "_comment": SYNTHETIC_NOTICE,
        "schema_version": SCHEMA_VERSION,
        "source_system": settings.source_system,
        "source_instance_id": settings.source_instance_id,
        "external_batch_id": batch_id,
        "sync_mode": "incremental",
        "project_ref": {"project_id": settings.project_id},
        "source_extracted_at": _now_iso(),
        "records": records,
    }


def build_unit_envelope(units: list[dict[str, Any]], *, batch_id: str) -> dict[str, Any]:
    """Dựng phong bì hợp đồng v1 cho một lô căn.

    `area_ref` dùng dạng `{area_name, unit_type}` — dạng ỔN ĐỊNH. Hợp đồng cũng
    cho phép `{area_id}`, nhưng một `source_instance_id` phải dùng ĐÚNG MỘT hình
    dạng vĩnh viễn: dấu vân payload được tính trên tập trường đã ánh xạ, nên đổi
    hình dạng giữa hai lô cho cùng một căn ở cùng revision sẽ sinh ra hai dấu vân
    khác nhau và backend báo `conflict` giả.

    Mỗi phần tử `units` cần: `external_id`, `area_name`, `unit_type`, `unit_code`,
    `unit_status`, `source_revision`.
    """
    return _envelope(
        [
            {
                "entity": ENTITY_RECORD["units"],
                "operation": "upsert",
                "external_id": unit["external_id"],
                "source_revision": unit["source_revision"],
                "payload": {
                    "area_ref": {"area_name": unit["area_name"], "unit_type": unit["unit_type"]},
                    "unit_code": unit["unit_code"],
                    "unit_status": unit["unit_status"],
                },
            }
            for unit in units
        ],
        batch_id=batch_id,
    )


def build_deal_envelope(deals: list[dict[str, Any]], *, batch_id: str) -> dict[str, Any]:
    """Dựng phong bì hợp đồng v1 cho một lô giao dịch.

    **CẢ BA mốc lịch sử luôn có mặt**, kể cả khi giá trị là `null`. Đây là điểm
    then chốt của chốt A4 nhìn từ phía NGUỒN.

    Bản ghi này là `full` (không khai `payload_completeness` ⇒ backend hiểu là
    `full`), nghĩa là nó tự nhận mang TRẠNG THÁI ĐẦY ĐỦ. Với bản ghi full, backend
    coi một mốc VẮNG MẶT trong khi bản sao đang giữ giá trị là đánh rơi lịch sử và
    TỪ CHỐI (`HISTORY_TIMESTAMP_DROPPED`). Gửi `null` tường minh thì lại là một
    khẳng định hợp lệ: "mốc này không còn".

    Vì phong bì được dựng TỪ DÒNG ĐÃ GHI trong `crm_deals`, một giao dịch chuyển
    `reserved → sold` mang theo `reserved_at` cũ một cách tự nhiên — không cần ai
    nhớ phải chép nó sang. Đó là lý do phong bì được dựng từ trạng thái đã lưu
    chứ không từ thân request PATCH.
    """
    return _envelope(
        [
            {
                "entity": ENTITY_RECORD["deals"],
                "operation": "upsert",
                "external_id": deal["external_id"],
                "source_revision": deal["source_revision"],
                "payload": {
                    "external_unit_id": deal["external_unit_id"],
                    "deal_status": deal["deal_status"],
                    "reserved_at": iso(deal.get("reserved_at")),
                    "sold_at": iso(deal.get("sold_at")),
                    "lost_at": iso(deal.get("lost_at")),
                },
            }
            for deal in deals
        ],
        batch_id=batch_id,
    )


def build_delete_envelope(entity: str, records: list[dict[str, Any]], *, batch_id: str) -> dict[str, Any]:
    """Phong bì xoá: KHÔNG có thân payload, nhưng VẪN mang phiên bản.

    Hợp đồng CẤM `payload` khi `operation=delete` (khối `if/then/else` của
    `$defs/record`), và đồng thời ĐÒI mọi bản ghi phải mang phiên bản — kể cả lệnh
    xoá. Thiếu phiên bản thì backend không xếp được thứ tự giữa lệnh xoá và một
    lô upsert đến muộn, nên nó từ chối bằng `MISSING_SOURCE_VERSION`.
    """
    return _envelope(
        [
            {
                "entity": ENTITY_RECORD[entity],
                "operation": "delete",
                "external_id": record["external_id"],
                "source_revision": record["source_revision"],
            }
            for record in records
        ],
        batch_id=batch_id,
    )


SYNTHETIC_NOTICE_V2 = (
    "SYNTHETIC — phong bì v2. Ghi TRONG transaction nghiệp vụ (Phase C), gửi bởi "
    "vòng relay tự động SAU commit (Phase C.5) — không phải request CRUD gửi nó "
    "trực tiếp. Xem docs/crm/sync_contract_v2_draft.md."
)

# Thực thể outbox v2. `units_v2`/`deals_v2` mang hậu tố để KHÔNG BAO GIỜ lẫn với
# dòng v1 "units"/"deals" (được `deliver()` gửi thật) khi tra `crm_outbox` theo
# `entity` — hai dòng cho cùng một lần ghi Unit/Deal là CÓ CHỦ ĐÍCH (xem
# `crud._capture_v2`), và lẫn hai loại vào nhau sẽ khiến `_mark_mirrored`/`resend`
# hiểu nhầm một ý định CHƯA GỬI thành một lô ĐÃ gửi.
V2_CAPTURE_ENTITIES = frozenset({"projects", "areas", "units_v2", "deals_v2"})
HIERARCHY_RECORD_ENTITY = {"projects": "project", "areas": "area", "units_v2": "unit", "deals_v2": "deal"}


def new_hierarchy_batch_id(entity: str) -> str:
    """Danh tính lô v2. Tiền tố `mc-v2-` tách hẳn khỏi `new_batch_id()` (v1) —
    cùng lý do tách `entity` ở trên: không được lẫn hai loại lô khi tra sổ."""
    return f"mc-v2-{entity}-{uuid.uuid4()}"


def order_hierarchy_records(records: list[dict[str, Any]], *, operation: str) -> list[dict[str, Any]]:
    """Sắp `records` theo ĐÚNG thứ tự tầng của §A5.2 (`contract_v2.HIERARCHY_TIER_*`),
    ỔN ĐỊNH trong cùng một tầng.

    Đây là nơi Mini CRM (producer) tự đảm bảo thứ tự TRƯỚC khi phong bì rời khỏi
    máy — phía nhận không sắp lại hộ (§A5.2), nên nếu producer không tự sắp thì
    không ai sắp cả. Bản ghi mang `entity` không thuộc bốn tầng bị từ chối thẳng,
    không đoán, không lặng lẽ bỏ qua.
    """
    order = contract_v2.HIERARCHY_TIER_UPSERT if operation == "upsert" else contract_v2.HIERARCHY_TIER_DELETE
    rank = {name: index for index, name in enumerate(order)}
    for record in records:
        if record.get("entity") not in rank:
            raise ValueError(f"bản ghi v2 có entity không hợp lệ cho lô '{operation}': {record.get('entity')!r}")
    return sorted(records, key=lambda record: rank[record["entity"]])


def _hierarchy_envelope(
    records: list[dict[str, Any]], *, batch_id: str, external_project_id: str
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "_comment": SYNTHETIC_NOTICE_V2,
        "schema_version": contract_v2.SCHEMA_VERSION,
        "source_system": settings.source_system,
        "source_instance_id": settings.source_instance_id,
        "external_batch_id": batch_id,
        "sync_mode": "incremental",
        "project_ref": {"external_project_id": external_project_id},
        "source_extracted_at": _now_iso(),
        "records": records,
    }


def build_project_envelope(
    projects: list[dict[str, Any]], *, batch_id: str, operation: str = "upsert"
) -> dict[str, Any]:
    """Phong bì v2 cho một lô dự án. Dự án là GỐC của phân cấp — `project_ref` của
    chính phong bì này LÀ `external_id` của dự án (§A3.5), không tham chiếu ai."""
    records = [
        {
            "entity": "project",
            "operation": operation,
            "external_id": project["external_id"],
            "source_revision": project["source_revision"],
            **(
                {}
                if operation == "delete"
                else {"payload": {"name": project["name"], "launch_date": str(project["launch_date"])}}
            ),
        }
        for project in projects
    ]
    return _hierarchy_envelope(
        order_hierarchy_records(records, operation=operation),
        batch_id=batch_id,
        external_project_id=projects[0]["external_id"],
    )


def build_area_envelope(
    areas: list[dict[str, Any]], *, batch_id: str, external_project_id: str, operation: str = "upsert"
) -> dict[str, Any]:
    """Phong bì v2 cho một lô phân khu. NĂM trường kế hoạch đều bắt buộc và có
    thẩm quyền ở `upsert` (§5.2 sync_contract_v2_draft.md) — không tiền tố
    `proposed_`, không bước duyệt."""
    records = [
        {
            "entity": "area",
            "operation": operation,
            "external_id": area["external_id"],
            "source_revision": area["source_revision"],
            **(
                {}
                if operation == "delete"
                else {
                    "payload": {
                        "area_name": area["area_name"],
                        "unit_type": area["unit_type"],
                        "bedrooms": area["bedrooms"],
                        "area_sqm": float(area["area_sqm"]),
                        "total_units": area["total_units"],
                    }
                }
            ),
        }
        for area in areas
    ]
    return _hierarchy_envelope(
        order_hierarchy_records(records, operation=operation),
        batch_id=batch_id,
        external_project_id=external_project_id,
    )


def build_unit_envelope_v2(
    units: list[dict[str, Any]], *, batch_id: str, external_project_id: str, operation: str = "upsert"
) -> dict[str, Any]:
    """Phong bì v2 cho một lô căn. Khác `build_unit_envelope` (v1) đúng một chỗ:
    `area_ref` mang `{external_area_id}` — v2 KHÔNG chấp nhận hình dạng
    `{area_name, unit_type}` của v1 (§4 sync_contract_v2_draft.md). Mỗi phần tử
    `units` cần thêm `external_area_id` so với phiên bản v1.
    """
    records = [
        {
            "entity": "unit",
            "operation": operation,
            "external_id": unit["external_id"],
            "source_revision": unit["source_revision"],
            **(
                {}
                if operation == "delete"
                else {
                    "payload": {
                        "area_ref": {"external_area_id": unit["external_area_id"]},
                        "unit_code": unit["unit_code"],
                        "unit_status": unit["unit_status"],
                    }
                }
            ),
        }
        for unit in units
    ]
    return _hierarchy_envelope(
        order_hierarchy_records(records, operation=operation),
        batch_id=batch_id,
        external_project_id=external_project_id,
    )


def build_deal_envelope_v2(
    deals: list[dict[str, Any]], *, batch_id: str, external_project_id: str, operation: str = "upsert"
) -> dict[str, Any]:
    """Phong bì v2 cho một lô giao dịch. `deal_payload` KHÔNG đổi so với v1 — giao
    dịch không mang `project_ref`/`area_ref` riêng ở CẢ HAI phiên bản (§A3.5):
    dự án/phân khu của nó suy qua `external_unit_id`, không lặp lại ở đây.
    """
    records = [
        {
            "entity": "deal",
            "operation": operation,
            "external_id": deal["external_id"],
            "source_revision": deal["source_revision"],
            **(
                {}
                if operation == "delete"
                else {
                    "payload": {
                        "external_unit_id": deal["external_unit_id"],
                        "deal_status": deal["deal_status"],
                        "reserved_at": iso(deal.get("reserved_at")),
                        "sold_at": iso(deal.get("sold_at")),
                        "lost_at": iso(deal.get("lost_at")),
                    }
                }
            ),
        }
        for deal in deals
    ]
    return _hierarchy_envelope(
        order_hierarchy_records(records, operation=operation),
        batch_id=batch_id,
        external_project_id=external_project_id,
    )


def build_hierarchy_envelope(
    records: list[dict[str, Any]], *, batch_id: str, external_project_id: str
) -> dict[str, Any]:
    """Ghép nhiều bản ghi ĐÃ DỰNG (lấy từ `records` của các phong bì đơn-tầng ở
    trên) thành MỘT phong bì trộn tầng, đúng thứ tự §A5.2.

    Phase C không tự động gộp lô nào — mỗi lần ghi vẫn tạo một phong bì riêng, một
    tầng (xem `crud._capture_v2`). Hàm này tồn tại cho Phase D (hoặc test) cần dựng
    một lô trộn thật để chứng minh thứ tự, giống fixture `19`/`20`/`21`.
    """
    if not records:
        raise ValueError("build_hierarchy_envelope: không có bản ghi nào để ghép")
    operations = {record["operation"] for record in records}
    if len(operations) > 1:
        raise ValueError("build_hierarchy_envelope: không trộn 'upsert' và 'delete' trong cùng một lô")
    (operation,) = operations
    return _hierarchy_envelope(
        order_hierarchy_records(records, operation=operation),
        batch_id=batch_id,
        external_project_id=external_project_id,
    )


class SyncClient:
    """POST một phong bì sang backend, và ghi lại nguyên văn cả hai chiều."""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    # --- Ghi sổ gửi đi ------------------------------------------------------

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        batch_id: str,
        entity: str,
        payload: dict[str, Any],
        replay_of: str | None = None,
    ) -> uuid.UUID:
        """Ghi dòng outbox bằng session CỦA PHÍA GỌI — không commit, không mở transaction.

        Dùng session của phía gọi là điều kiện để dòng outbox và thay đổi nghiệp
        vụ cùng sống hoặc cùng chết. Xem sơ đồ ở docstring module.
        """
        row_id = uuid.uuid4()
        await session.execute(
            sa.insert(crm_outbox).values(
                id=row_id,
                external_batch_id=batch_id,
                entity=entity,
                payload=payload,
                http_status=None,
                response=None,
                sent_at=None,
                created_at=datetime.now(UTC),
                attempts=0,
                last_error=None,
                replay_of=replay_of,
            )
        )
        return row_id

    async def _enqueue_standalone(self, *, batch_id: str, entity: str, payload: dict[str, Any]) -> uuid.UUID:
        async with self._session_factory() as session:
            async with session.begin():
                return await self.enqueue(session, batch_id=batch_id, entity=entity, payload=payload)

    async def _record_response(self, row_id: uuid.UUID, *, status_code: int, body: Any) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.update(crm_outbox)
                    .where(crm_outbox.c.id == row_id)
                    .values(
                        http_status=status_code,
                        response=body,
                        sent_at=datetime.now(UTC),
                        attempts=crm_outbox.c.attempts + 1,
                        last_error=None,
                    )
                )

    async def _record_transport_error(self, row_id: uuid.UUID, message: str) -> None:
        """Lỗi truyền tải: đếm lần thử và ghi lý do, nhưng KHÔNG chạm `http_status`.

        `http_status` giữ NULL vì ta thật sự không biết. Ghi một mã bịa ra (500,
        0, -1) vào đó là khẳng định một điều không ai kiểm chứng được — lô có thể
        đã tới nơi và đã được xử lý xong. `sent_at` cũng giữ NULL: nó có nghĩa là
        "đã có phản hồi lúc này", không phải "đã thử lúc này".
        """
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.update(crm_outbox)
                    .where(crm_outbox.c.id == row_id)
                    .values(attempts=crm_outbox.c.attempts + 1, last_error=message[:2000])
                )

    # --- Gửi ---------------------------------------------------------------

    async def deliver(
        self, row_id: uuid.UUID, envelope: dict[str, Any], *, entity: str, client: httpx.AsyncClient | None = None
    ) -> PushResult:
        """POST một phong bì mà dòng outbox của nó ĐÃ được ghi và commit.

        Ném `SyncPushError` khi không đi tới đích hoặc khi backend từ chối. Phía
        gọi bắt lỗi này và vẫn trả về kết quả CRUD cục bộ — thay đổi đã commit,
        và giấu nó đi vì lần gửi hỏng sẽ tệ hơn nhiều lần.
        """
        settings = get_settings()
        if entity not in ENTITY_PATH:
            raise ValueError(f"entity phải là một trong: {', '.join(sorted(ENTITY_PATH))}")

        batch_id = envelope["external_batch_id"]
        url = f"{settings.sync_base_url.rstrip('/')}/api/v1/sync/{ENTITY_PATH[entity]}"
        headers = {"X-API-Key": settings.sync_api_key_value, "Content-Type": "application/json"}

        owns_client = client is None
        client = client or http_client(settings.sync_timeout_seconds)
        try:
            response = await client.post(url, json=envelope, headers=headers)
        except httpx.HTTPError as exc:
            # KHÔNG mở được kết nối ⇒ không một byte nào rời khỏi máy ⇒ lô CHẮC
            # CHẮN chưa tới backend. Đó là `sync_failed`, không phải `sync_pending`:
            # gộp nó vào "không biết" sẽ khiến người vận hành ngồi chờ một kết quả
            # không bao giờ có, thay vì gửi lại ngay.
            never_left = isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
            if never_left:
                message = f"Không mở được kết nối tới backend cho lô '{batch_id}' — lô CHƯA rời khỏi máy."
                outcome = "sync_failed"
            elif isinstance(exc, httpx.TimeoutException):
                # Request ĐÃ đi nhưng không có phản hồi. Đây mới là trường hợp
                # thật sự mơ hồ: lô có thể đã tới nơi và đã được xử lý xong.
                message = (
                    f"Hết thời gian chờ khi đẩy lô '{batch_id}' — KHÔNG biết backend đã nhận hay chưa. "
                    f"Dòng outbox giữ http_status=NULL."
                )
                outcome = "sync_pending"
            else:
                message = f"Không gọi được backend cho lô '{batch_id}': {type(exc).__name__}"
                outcome = "sync_pending"
            await self._record_transport_error(row_id, message)
            raise SyncPushError(message, batch_id=batch_id, outcome=outcome) from exc
        finally:
            if owns_client:
                await client.aclose()

        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}

        await self._record_response(row_id, status_code=response.status_code, body=body)

        if response.status_code not in (200, 202):
            raise SyncPushError(
                f"Backend từ chối lô '{batch_id}' với HTTP {response.status_code}",
                batch_id=batch_id,
                status_code=response.status_code,
                detail=body,
            )

        return PushResult(batch_id=batch_id, status_code=response.status_code, body=body)

    async def push(
        self, envelope: dict[str, Any], *, entity: str, client: httpx.AsyncClient | None = None
    ) -> PushResult:
        """Ghi outbox (transaction riêng) rồi gửi.

        Đường ĐỘC LẬP, dùng khi không có thay đổi nghiệp vụ nào đi kèm: gửi lại
        một lô cũ, phát lại một bản cũ, hay một lần đẩy thủ công. Thao tác CRUD
        thì KHÔNG đi đường này — nó dùng `enqueue()` trong chính transaction của
        mình rồi `deliver()` sau commit.
        """
        if entity not in ENTITY_PATH:
            raise ValueError(f"entity phải là một trong: {', '.join(sorted(ENTITY_PATH))}")
        row_id = await self._enqueue_standalone(
            batch_id=envelope["external_batch_id"], entity=entity, payload=envelope
        )
        return await self.deliver(row_id, envelope, entity=entity, client=client)

    async def push_units(self, units: list[dict[str, Any]], *, client: httpx.AsyncClient | None = None) -> PushResult:
        """Tiện ích: dựng phong bì cho một lô căn rồi đẩy."""
        batch_id = new_batch_id("units")
        envelope = build_unit_envelope(units, batch_id=batch_id)
        return await self.push(envelope, entity="units", client=client)
