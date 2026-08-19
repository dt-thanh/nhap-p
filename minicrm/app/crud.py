"""Tầng nghiệp vụ của Mini CRM: ghi cục bộ, rồi đẩy sang backend.

═══════════════════════════════════════════════════════════════════════════════
 MỘT THỨ TỰ, KHÔNG CÓ NGOẠI LỆ
═══════════════════════════════════════════════════════════════════════════════

    transaction:  kiểm điều kiện → ghi bản ghi (revision += 1)
                  → dựng phong bì TỪ DÒNG ĐÃ GHI → kiểm hợp đồng
                  → ghi dòng crm_outbox
    COMMIT
    POST → cập nhật outbox + đóng dấu mirrored

Ba quyết định trong sơ đồ đó đáng nói rõ vì mỗi cái đóng lại một lỗ hổng cụ thể:

**Phong bì dựng từ DÒNG ĐÃ GHI, không từ thân request.** Đây là thứ khiến chốt A4
đúng mà không cần ai nhớ tới nó. `PATCH /deals/D-0001 {"deal_status": "sold",
"sold_at": ...}` chỉ mang hai trường, nhưng phong bì được dựng lại từ dòng
`crm_deals` sau khi ghi, nên `reserved_at` cũ đi kèm một cách tự nhiên. Dựng phong
bì từ thân request thì `reserved_at` biến mất, backend từ chối bằng
`HISTORY_TIMESTAMP_DROPPED`, và ai đó sẽ "sửa" bằng cách khai `partial` — che mất
đúng cái giới hạn mà A4 sinh ra để phơi bày.

**Kiểm hợp đồng nằm TRONG transaction.** Phong bì hỏng ⇒ rollback ⇒ không tồn tại
bản ghi đã commit nào mà Mini CRM không diễn đạt nổi thành payload hợp lệ. Kiểm
sau commit thì sẽ có, và nó sẽ nằm đó vĩnh viễn không gửi được.

**Gửi nằm NGOÀI transaction.** Gửi bên trong nghĩa là backend có thể đã nhận và
chiếu xong một bản ghi mà giây sau Mini CRM rollback — không có cách nào lấy lại.
Cái giá là một lần gửi hỏng để lại thay đổi đã commit chưa lên tới nơi; cái giá đó
được TRẢ BẰNG KHẢ NĂNG NHÌN THẤY, không bằng cách giấu đi: dòng outbox còn nguyên,
`mirrored_revision < source_revision`, và phản hồi trả `sync_failed`/`sync_pending`.

═══════════════════════════════════════════════════════════════════════════════
 GIAO DỊCH CHỈ ĐI SAU CĂN CỦA NÓ
═══════════════════════════════════════════════════════════════════════════════

Backend từ chối giao dịch mồ côi bằng `UNKNOWN_UNIT_REFERENCE`, và nó tra căn theo
`(source_instance_id, external_unit_id)` trong bảng `units` CỦA NÓ. Nên "căn có
thật trong Mini CRM" là chưa đủ — căn phải đã LÊN TỚI backend.

Vì thế cả hai điều kiện được kiểm TRƯỚC KHI ghi bất cứ thứ gì:

    căn không tồn tại cục bộ          → 422, không ghi, không gửi
    căn tồn tại nhưng chưa mirrored   → 409, không ghi, không gửi

Cách khác — cho tạo giao dịch rồi chặn ở bước gửi — nghe có vẻ mềm mại hơn nhưng
tạo ra một loại dữ liệu tồi tệ hơn hẳn: một bản ghi đã commit, KHÔNG có dòng outbox
nào, nên không có gì để `resend`. Đó đúng là "một thay đổi đã commit bị âm thầm
bỏ rơi" mà Phase 4 cấm. Từ chối trước khi ghi thì không có gì bị bỏ rơi cả.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from app import contract, contract_v2, sync_client
from app.db import get_session_factory
from app.models import (
    AREA_SEQUENCE,
    DEAL_SEQUENCE,
    PROJECT_SEQUENCE,
    UNIT_SEQUENCE,
    crm_areas,
    crm_deals,
    crm_outbox,
    crm_projects,
    crm_units,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Số con còn sống tối đa liệt kê trong một lỗi PARENT_HAS_LIVE_CHILDREN. Trần này
# giữ thân lỗi nhỏ và dễ đọc; `child_count` trong `detail` luôn là số THẬT, kể cả
# khi danh sách bị cắt.
_MAX_LISTED_CHILDREN = 20

# Trạng thái GIỮ căn — khớp `uq_crm_deals_holding_per_unit` và
# `uq_deals_active_per_unit` của backend.
HOLDING_STATUSES = frozenset({"reserved", "sold"})


class CrudError(Exception):
    """Thao tác bị từ chối TRƯỚC khi ghi và trước khi gửi.

    `http_status` đi kèm vì hai loại từ chối cần hai mã khác nhau: 422 = dữ liệu
    người gọi sai, 409 = dữ liệu đúng nhưng trạng thái hệ thống chưa cho phép.
    Gộp cả hai vào 400 sẽ khiến người tích hợp đi sửa nhầm chỗ.
    """

    def __init__(self, error_code: str, message: str, *, http_status: int = 422, detail: Any = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status
        self.detail = detail


class RecordNotFoundError(CrudError):
    def __init__(self, entity: str, external_id: str) -> None:
        super().__init__(
            "RECORD_NOT_FOUND", f"Không tìm thấy {entity} '{external_id}'", http_status=404
        )


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Kết quả đẩy — xem `schemas.SyncOut` để biết ý nghĩa từng trạng thái."""

    status: str
    external_batch_id: str
    http_status: int | None = None
    sync_run_id: str | None = None
    decisions: dict[str, int] | None = None
    projections: dict[str, int] | None = None
    error: str | None = None
    detail: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "external_batch_id": self.external_batch_id,
            "http_status": self.http_status,
            "sync_run_id": self.sync_run_id,
            "decisions": self.decisions or {},
            "projections": self.projections or {},
            "error": self.error,
            "detail": self.detail,
        }


# --- Danh tính ---------------------------------------------------------------

_SEQUENCES = {
    "units": (UNIT_SEQUENCE, "U"),
    "deals": (DEAL_SEQUENCE, "D"),
    "projects": (PROJECT_SEQUENCE, "P"),
    "areas": (AREA_SEQUENCE, "A"),
}


async def next_external_id(session: AsyncSession, entity: str) -> str:
    """Cấp một `external_id` mới, VĨNH VIỄN không dùng lại.

    `nextval` của PostgreSQL không lùi kể cả khi transaction gọi nó bị rollback.
    Một vài id bị bỏ phí là cái giá, và nó rẻ: id bỏ phí không gây hậu quả gì, còn
    id dùng lại thì gắn lịch sử của một căn đã xoá vào một căn hoàn toàn khác —
    hỏng vĩnh viễn và không phát hiện được từ phía nhận.

    Định dạng `%04d` chỉ ĐỆM chứ không cắt: qua 9999 thì id dài ra (`U-10000`) và
    vẫn duy nhất, vẫn dưới trần 128 ký tự của hợp đồng.
    """
    sequence, prefix = _SEQUENCES[entity]
    number = await session.scalar(sa.select(sa.func.nextval(sequence)))
    return f"{prefix}-{number:04d}"


# --- Đẩy + đóng dấu ----------------------------------------------------------


async def _mark_mirrored(table: sa.Table, marks: list[tuple[str, int]], batch_id: str) -> None:
    """Đóng dấu "backend đã nhận tới phiên bản này".

    `GREATEST` chứ không gán thẳng: gửi lại một lô CŨ (`resend`, `replay-stale`)
    vẫn trả 2xx, và gán thẳng sẽ kéo `mirrored_revision` LÙI về phiên bản của lô
    cũ — khiến một bản ghi đã đồng bộ đầy đủ đột nhiên trông như đang lạc hậu.
    """
    if not marks:
        return
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            for external_id, revision in marks:
                await session.execute(
                    sa.update(table)
                    .where(table.c.external_id == external_id)
                    .values(
                        mirrored_at=now,
                        mirrored_revision=sa.func.greatest(
                            sa.func.coalesce(table.c.mirrored_revision, 0), revision
                        ),
                        last_sync_batch_id=batch_id,
                    )
                )


async def deliver(
    outbox_id: uuid.UUID,
    envelope: dict[str, Any],
    *,
    entity: str,
    table: sa.Table | None = None,
    client=None,
) -> SyncOutcome:
    """Gửi một phong bì đã commit và diễn giải kết quả. KHÔNG BAO GIỜ ném ra ngoài.

    Không ném vì thao tác cục bộ ĐÃ THÀNH CÔNG và đã commit. Để lỗi đẩy nổi lên
    thành lỗi của request CRUD sẽ nói với người gọi rằng thao tác của họ hỏng —
    và họ sẽ thử lại, tạo ra bản ghi thứ hai cho cùng một sự việc.
    """
    batch_id = envelope["external_batch_id"]
    marks = [
        (record["external_id"], record["source_revision"])
        for record in envelope["records"]
        if "external_id" in record and "source_revision" in record
    ]

    try:
        result = await sync_client.SyncClient().deliver(outbox_id, envelope, entity=entity, client=client)
    except sync_client.SyncPushError as exc:
        return SyncOutcome(
            status=exc.outcome,
            external_batch_id=batch_id,
            http_status=exc.status_code,
            error=exc.message,
            detail=exc.detail,
        )

    if table is not None:
        await _mark_mirrored(table, marks, batch_id)

    return SyncOutcome(
        status="replayed" if result.replayed else "synced",
        external_batch_id=batch_id,
        http_status=result.status_code,
        sync_run_id=result.sync_run_id,
        decisions=result.decisions,
        projections=result.projections,
    )


def _written(row: dict[str, Any], outcome: SyncOutcome) -> dict[str, Any]:
    """Bản ghi ĐÚNG NHƯ lời gọi này đã ghi nó, kèm dấu mirrored của chính lần đẩy này.

    **Vì sao KHÔNG đọc lại dòng sau khi commit.** Đọc lại trả về trạng thái MỚI
    NHẤT, không phải trạng thái mà lời gọi này tạo ra. Hai request PATCH chạy song
    song thì request thứ nhất commit ở revision 2, rồi đọc lại và thấy revision 3
    do request thứ hai vừa ghi — nên nó báo cáo revision 3 trong khi lô nó vừa gửi
    mang revision 2. Lúc đó `record.source_revision` và `sync.external_batch_id`
    trong CÙNG một thân phản hồi nói về hai sự việc khác nhau, và không có gì trong
    phản hồi cho biết điều đó.

    Phase 5 phát hiện đúng lỗi này bằng hai lần PATCH song song: cả hai phản hồi
    cùng khai revision 3.

    Dấu mirrored được suy từ KẾT QUẢ ĐẨY của chính lời gọi này thay vì đọc lại từ
    DB, vì cùng lý do — và vì `_mark_mirrored` dùng `GREATEST`, giá trị trong DB có
    thể đã cao hơn do một lô khác. Con số ở đây trả lời đúng một câu: "lô mang
    revision này đã tới backend chưa".
    """
    record = dict(row)
    if outcome.status in ("synced", "replayed"):
        record["mirrored_at"] = datetime.now(UTC)
        record["mirrored_revision"] = row["source_revision"]
        record["last_sync_batch_id"] = outcome.external_batch_id
    return record


# --- Phase C: bắt giữ ý định v2 (KHÔNG gửi) -----------------------------------
#
# `sync_client.deliver()` chỉ biết "units"/"deals" (v1, đường HTTP thật). Bốn hàm
# `_capture_v2`/`_hierarchy_context_for_area`/`_hierarchy_context_for_unit` dưới
# đây LÀ Phase C: dựng phong bì v2 từ dòng VỪA GHI, kiểm bằng `contract_v2`, rồi
# ghi MỘT dòng `crm_outbox` mới (entity ∈ `sync_client.V2_CAPTURE_ENTITIES`) TRONG
# CÙNG transaction với thay đổi nghiệp vụ — cùng kỷ luật "ghi outbox và ghi nghiệp
# vụ cùng sống hoặc cùng chết" mà `sync_client.py` áp cho v1.
#
# Khác v1 ở ĐÚNG một chỗ, có chủ đích: KHÔNG có bước gửi nào theo sau. Phía nhận
# giữ `SUPPORTED_SCHEMA_VERSIONS = {1}` và không có `POST /sync/projects`/
# `POST /sync/areas` nào tồn tại (`REQUIRED IN PHASE D`) — gửi một phong bì mà
# không có đường nhận, hoặc mà phía nhận chắc chắn từ chối vì phiên bản lạ, không
# phải là "đẩy", chỉ là rác trong log. Dòng outbox này là Ý ĐỊNH ĐÃ KÝ: `http_status`
# giữ NULL vô thời hạn cho tới khi Phase D nối đường gửi thật — không phải "đang
# chờ phản hồi" như một lô v1 timeout, mà là "chưa từng được phép rời khỏi máy".


async def _capture_v2(session: AsyncSession, *, entity: str, batch_id: str, envelope: dict[str, Any]) -> None:
    contract_v2.assert_valid(envelope)
    await sync_client.SyncClient().enqueue(session, batch_id=batch_id, entity=entity, payload=envelope)


def _reject_v2_delivery(row: dict[str, Any]) -> None:
    """Chặn `resend`/`replay_stale` cho một dòng outbox v2 — ranh giới TƯỜNG MINH.

    Không phải một lỗi hệ thống: từ Phase C.5, một dòng v2 được gửi bởi vòng RELAY
    TỰ ĐỘNG (`app/relay.py`), không phải bởi hai đường THỦ CÔNG này. Hai đường này
    (`sync_client.new_batch_id`/`contract.assert_valid` v1) được viết CHỈ cho hình
    dạng v1 — dùng chúng cho một nhãn v2 sẽ hoặc ném lỗi không kiểm soát được, hoặc
    (nếu ai đó "sửa" bằng cách nới `contract`/`new_batch_id` cho v2) âm thầm trùng
    logic với `relay.py`, hai nơi cùng quyết định "dòng này đã gửi chưa". 409 thay
    vì 404/500 vì dòng đó THẬT SỰ tồn tại — chỉ là đường gửi của nó là relay, không
    phải resend/replay-stale.
    """
    if row["entity"] in sync_client.V2_CAPTURE_ENTITIES:
        raise CrudError(
            "V2_DELIVERY_NOT_ENABLED",
            f"Lô '{row['external_batch_id']}' là ý định v2 (thực thể '{row['entity']}'). Nó được gửi bởi "
            f"vòng relay tự động (Phase C.5), không phải qua resend/replay-stale — hai đường đó chỉ hỗ trợ "
            f"hình dạng v1. Dòng còn http_status=NULL nghĩa là relay CHƯA tới lượt nó, không phải nó không "
            f"gửi được — chờ lượt relay kế, hoặc xem GET /outbox/{{id}} để biết trạng thái mới nhất.",
            http_status=409,
        )


async def _hierarchy_context_for_area(session: AsyncSession, area_id: uuid.UUID | None) -> tuple[str, str] | None:
    """`(external_project_id, external_area_id)` cho một `area_id` nội bộ.

    `None` khi `area_id` là NULL — căn di sản trước Phase B (163 dòng, xem
    `models.py::crm_units.area_id`) không có phân khu cục bộ nào để trỏ vào, nên
    không thể dựng `area_ref` cho phong bì v2 mà không bịa dữ liệu (§A0). Những căn
    đó ĐƠN GIẢN không tạo ý định v2 — không phải một lỗi, một biên đã biết.
    """
    if area_id is None:
        return None
    row = (
        await session.execute(
            sa.select(crm_areas.c.external_id, crm_projects.c.external_id.label("external_project_id"))
            .select_from(crm_areas.join(crm_projects, crm_areas.c.project_id == crm_projects.c.id))
            .where(crm_areas.c.id == area_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return row.external_project_id, row.external_id


async def _hierarchy_context_for_unit(session: AsyncSession, external_unit_id: str) -> tuple[str, str] | None:
    """`(external_project_id, external_area_id)` suy qua căn — Deal không tự lưu
    tham chiếu dự án/phân khu (§A3.5: "Deal → Area, Project: KHÔNG trỏ. Suy ra qua
    unit."). `None` khi căn không tồn tại cục bộ HOẶC là căn di sản không phân khu.
    """
    unit = (
        await session.execute(sa.select(crm_units.c.area_id).where(crm_units.c.external_id == external_unit_id))
    ).one_or_none()
    if unit is None:
        return None
    return await _hierarchy_context_for_area(session, unit.area_id)


def _row(mapping) -> dict[str, Any]:
    return dict(mapping)


# --- Dự án ---------------------------------------------------------------
#
# Phase B: Mini CRM là TÁC GIẢ, không chỉ người tham chiếu.
# Phase C: mỗi lần ghi giờ tạo THÊM một dòng outbox v2 (entity="projects"),
# CHỈ LƯU — xem "Phase C: bắt giữ ý định v2" ở trên. Phản hồi CRUD trả về KHÔNG đổi
# hình dạng (`ProjectWriteOut` không có trường `sync`, giữ nguyên hợp đồng Phase B);
# dòng outbox tra được qua `GET /outbox?entity=projects` như mọi dòng khác.


def _project_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": row["external_id"],
        "name": row["name"],
        "launch_date": row["launch_date"],
        "status": row["status"],
        "source_revision": row["source_revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "mirrored_at": row["mirrored_at"],
        "mirrored_revision": row["mirrored_revision"],
        "last_sync_batch_id": row["last_sync_batch_id"],
    }


async def list_projects(*, include_archived: bool = False) -> list[dict[str, Any]]:
    query = sa.select(crm_projects).order_by(crm_projects.c.external_id)
    if not include_archived:
        query = query.where(crm_projects.c.status == "active")
    async with get_session_factory()() as session:
        return [_project_record(_row(r)) for r in (await session.execute(query)).mappings().all()]


async def get_project(external_id: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            (await session.execute(sa.select(crm_projects).where(crm_projects.c.external_id == external_id)))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RecordNotFoundError("dự án", external_id)
    return _project_record(_row(row))


async def create_project(data: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            external_id = await next_external_id(session, "projects")
            row = (
                (
                    await session.execute(
                        sa.insert(crm_projects)
                        .values(
                            id=uuid.uuid4(),
                            external_id=external_id,
                            name=data["name"],
                            launch_date=data["launch_date"],
                            status="active",
                            source_revision=1,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(crm_projects)
                    )
                )
                .mappings()
                .one()
            )
            written = _project_record(_row(row))
            v2_batch_id = sync_client.new_hierarchy_batch_id("projects")
            v2_envelope = sync_client.build_project_envelope([written], batch_id=v2_batch_id)
            await _capture_v2(session, entity="projects", batch_id=v2_batch_id, envelope=v2_envelope)
    return written


async def update_project(external_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if not patch:
        raise CrudError("EMPTY_PATCH", "Body rỗng — không có gì để sửa")

    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            current = (
                (
                    await session.execute(
                        sa.select(crm_projects).where(crm_projects.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("dự án", external_id)
            if current["status"] == "archived":
                raise CrudError(
                    "RECORD_ARCHIVED",
                    f"Dự án '{external_id}' đã lưu trữ — không sửa được. Tạo dự án mới nếu cần.",
                    http_status=409,
                )

            row = (
                (
                    await session.execute(
                        sa.update(crm_projects)
                        .where(crm_projects.c.external_id == external_id)
                        .values(**patch, source_revision=current["source_revision"] + 1, updated_at=now)
                        .returning(crm_projects)
                    )
                )
                .mappings()
                .one()
            )
            written = _project_record(_row(row))
            v2_batch_id = sync_client.new_hierarchy_batch_id("projects")
            v2_envelope = sync_client.build_project_envelope([written], batch_id=v2_batch_id)
            await _capture_v2(session, entity="projects", batch_id=v2_batch_id, envelope=v2_envelope)
    return written


async def archive_project(external_id: str) -> dict[str, Any]:
    """Lưu trữ — KHÔNG BAO GIỜ xoá vật lý (`projects` không có `deleted_at`).

    Từ chối nếu còn Area SỐNG (`status='active'`) thuộc dự án này — KHÔNG cascade.
    Xem `docs/crm/phase_a_domain_freeze.md` §A1.8: một phân khu bị lưu trữ hộ theo
    cha sẽ khiến hệ nguồn tưởng nó vẫn sống và gửi lại nó với revision cao hơn ở
    lần đồng bộ sau — một vòng hồi sinh không hội tụ.
    """
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            current = (
                (
                    await session.execute(
                        sa.select(crm_projects).where(crm_projects.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("dự án", external_id)
            if current["status"] == "archived":
                raise CrudError("ALREADY_ARCHIVED", f"Dự án '{external_id}' đã lưu trữ rồi", http_status=409)

            live_areas = (
                (
                    await session.execute(
                        sa.select(crm_areas.c.external_id)
                        .where(crm_areas.c.project_id == current["id"], crm_areas.c.status == "active")
                        .order_by(crm_areas.c.external_id)
                        .limit(_MAX_LISTED_CHILDREN + 1)
                    )
                )
                .scalars()
                .all()
            )
            if live_areas:
                raise CrudError(
                    "PARENT_HAS_LIVE_CHILDREN",
                    f"Dự án '{external_id}' còn {len(live_areas)}{'+' if len(live_areas) > _MAX_LISTED_CHILDREN else ''} "
                    f"phân khu đang hoạt động — lưu trữ phân khu trước, hoặc archive từng phân khu rồi thử lại.",
                    http_status=409,
                    detail={
                        "parent_entity": "project",
                        "parent_external_id": external_id,
                        "child_entity": "area",
                        "child_external_ids": live_areas[:_MAX_LISTED_CHILDREN],
                    },
                )

            row = (
                (
                    await session.execute(
                        sa.update(crm_projects)
                        .where(crm_projects.c.external_id == external_id)
                        .values(status="archived", source_revision=current["source_revision"] + 1, updated_at=now)
                        .returning(crm_projects)
                    )
                )
                .mappings()
                .one()
            )
            written = _project_record(_row(row))
            v2_batch_id = sync_client.new_hierarchy_batch_id("projects")
            v2_envelope = sync_client.build_project_envelope([written], batch_id=v2_batch_id, operation="delete")
            await _capture_v2(session, entity="projects", batch_id=v2_batch_id, envelope=v2_envelope)
    return written


# --- Phân khu --------------------------------------------------------------


def _area_select():
    """SELECT phân khu JOIN dự án, để mỗi hàng mang sẵn `external_project_id`.

    Phân khu chỉ lưu `project_id` (UUID nội bộ); người gọi bên ngoài chỉ biết
    `external_project_id`. JOIN một lần ở đây rẻ hơn N+1 lần tra riêng cho mỗi
    dòng khi liệt kê.
    """
    return sa.select(
        crm_areas.c.external_id,
        crm_projects.c.external_id.label("external_project_id"),
        crm_areas.c.area_name,
        crm_areas.c.unit_type,
        crm_areas.c.bedrooms,
        crm_areas.c.area_sqm,
        crm_areas.c.total_units,
        crm_areas.c.status,
        crm_areas.c.source_revision,
        crm_areas.c.created_at,
        crm_areas.c.updated_at,
        crm_areas.c.mirrored_at,
        crm_areas.c.mirrored_revision,
        crm_areas.c.last_sync_batch_id,
    ).select_from(crm_areas.join(crm_projects, crm_areas.c.project_id == crm_projects.c.id))


def _area_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": row["external_id"],
        "external_project_id": row["external_project_id"],
        "area_name": row["area_name"],
        "unit_type": row["unit_type"],
        "bedrooms": row["bedrooms"],
        "area_sqm": float(row["area_sqm"]),
        "total_units": row["total_units"],
        "status": row["status"],
        "source_revision": row["source_revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "mirrored_at": row["mirrored_at"],
        "mirrored_revision": row["mirrored_revision"],
        "last_sync_batch_id": row["last_sync_batch_id"],
    }


async def list_areas(*, external_project_id: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
    query = _area_select().order_by(crm_areas.c.external_id)
    if external_project_id is not None:
        query = query.where(crm_projects.c.external_id == external_project_id)
    if not include_archived:
        query = query.where(crm_areas.c.status == "active")
    async with get_session_factory()() as session:
        return [_area_record(_row(r)) for r in (await session.execute(query)).mappings().all()]


async def get_area(external_id: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            (await session.execute(_area_select().where(crm_areas.c.external_id == external_id)))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RecordNotFoundError("phân khu", external_id)
    return _area_record(_row(row))


async def _get_project_by_external_id(session: AsyncSession, external_id: str):
    return (
        (await session.execute(sa.select(crm_projects).where(crm_projects.c.external_id == external_id)))
        .mappings()
        .one_or_none()
    )


_AREA_NATURAL_KEY_CONSTRAINT = "uq_crm_areas_project_name_unit_type"


async def create_area(data: dict[str, Any]) -> dict[str, Any]:
    """Tạo phân khu dưới một dự án ĐÃ CÓ cục bộ.

    Dự án phải TỒN TẠI và KHÔNG lưu trữ — Mini CRM không tự phát minh dự án cho
    một phân khu mồ côi (phase_a_domain_freeze.md §A5.3: "Backend có được TỰ TẠO
    cha không? TUYỆT ĐỐI KHÔNG" — nguyên tắc đó áp dụng ĐỐI XỨNG ở đây: Mini CRM
    là hệ nguồn của CẢ HAI tầng, nên nó cũng không được tự tạo cha cho chính nó).
    """
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            project = await _get_project_by_external_id(session, data["external_project_id"])
            if project is None:
                raise CrudError(
                    "PROJECT_NOT_FOUND",
                    f"Không tìm thấy dự án '{data['external_project_id']}' cục bộ. "
                    f"Tạo dự án trước khi tạo phân khu vào nó.",
                    http_status=422,
                )
            if project["status"] == "archived":
                raise CrudError(
                    "PARENT_ARCHIVED",
                    f"Dự án '{data['external_project_id']}' đã lưu trữ — không tạo phân khu mới vào một dự án đã lưu trữ.",
                    http_status=409,
                )

            external_id = await next_external_id(session, "areas")
            try:
                row = (
                    (
                        await session.execute(
                            sa.insert(crm_areas)
                            .values(
                                id=uuid.uuid4(),
                                external_id=external_id,
                                project_id=project["id"],
                                area_name=data["area_name"],
                                unit_type=data["unit_type"],
                                bedrooms=data["bedrooms"],
                                area_sqm=data["area_sqm"],
                                total_units=data["total_units"],
                                status="active",
                                source_revision=1,
                                created_at=now,
                                updated_at=now,
                            )
                            .returning(crm_areas)
                        )
                    )
                    .mappings()
                    .one()
                )
            except IntegrityError as exc:
                if _AREA_NATURAL_KEY_CONSTRAINT in str(exc.orig):
                    raise CrudError(
                        "AREA_NATURAL_KEY_CONFLICT",
                        f"Dự án '{data['external_project_id']}' đã có phân khu "
                        f"('{data['area_name']}', '{data['unit_type']}').",
                        http_status=409,
                    ) from exc
                raise
            written = _area_record({**_row(row), "external_project_id": project["external_id"]})
            v2_batch_id = sync_client.new_hierarchy_batch_id("areas")
            v2_envelope = sync_client.build_area_envelope(
                [written], batch_id=v2_batch_id, external_project_id=project["external_id"]
            )
            await _capture_v2(session, entity="areas", batch_id=v2_batch_id, envelope=v2_envelope)
    return written


async def _project_external_id_for(session: AsyncSession, project_id: uuid.UUID) -> str:
    value = await session.scalar(sa.select(crm_projects.c.external_id).where(crm_projects.c.id == project_id))
    assert value is not None, "project_id trên crm_areas trỏ tới một dự án không tồn tại — vi phạm FK"
    return value


async def update_area(external_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Sửa phân khu. `external_project_id` KHÔNG BAO GIỜ nằm trong `patch` — bất
    biến bằng cách vắng mặt ở `AreaPatch` (phân khu không đổi dự án, §A1.6)."""
    if not patch:
        raise CrudError("EMPTY_PATCH", "Body rỗng — không có gì để sửa")

    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            current = (
                (
                    await session.execute(
                        sa.select(crm_areas).where(crm_areas.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("phân khu", external_id)
            if current["status"] == "archived":
                raise CrudError(
                    "RECORD_ARCHIVED", f"Phân khu '{external_id}' đã lưu trữ — không sửa được.", http_status=409
                )

            try:
                row = (
                    (
                        await session.execute(
                            sa.update(crm_areas)
                            .where(crm_areas.c.external_id == external_id)
                            .values(**patch, source_revision=current["source_revision"] + 1, updated_at=now)
                            .returning(crm_areas)
                        )
                    )
                    .mappings()
                    .one()
                )
            except IntegrityError as exc:
                if _AREA_NATURAL_KEY_CONSTRAINT in str(exc.orig):
                    raise CrudError(
                        "AREA_NATURAL_KEY_CONFLICT",
                        f"Dự án của phân khu '{external_id}' đã có một phân khu khác mang "
                        f"('{patch.get('area_name', current['area_name'])}', "
                        f"'{patch.get('unit_type', current['unit_type'])}').",
                        http_status=409,
                    ) from exc
                raise

            project_external_id = await _project_external_id_for(session, row["project_id"])
            written = _area_record({**_row(row), "external_project_id": project_external_id})
            v2_batch_id = sync_client.new_hierarchy_batch_id("areas")
            v2_envelope = sync_client.build_area_envelope(
                [written], batch_id=v2_batch_id, external_project_id=project_external_id
            )
            await _capture_v2(session, entity="areas", batch_id=v2_batch_id, envelope=v2_envelope)
    return written


async def archive_area(external_id: str) -> dict[str, Any]:
    """Lưu trữ — KHÔNG BAO GIỜ xoá vật lý. Từ chối nếu còn Unit SỐNG (`deleted_at
    IS NULL`) thuộc phân khu này — KHÔNG cascade, cùng lý do với `archive_project`.
    """
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            current = (
                (
                    await session.execute(
                        sa.select(crm_areas).where(crm_areas.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("phân khu", external_id)
            if current["status"] == "archived":
                raise CrudError("ALREADY_ARCHIVED", f"Phân khu '{external_id}' đã lưu trữ rồi", http_status=409)

            live_units = (
                (
                    await session.execute(
                        sa.select(crm_units.c.external_id)
                        .where(crm_units.c.area_id == current["id"], crm_units.c.deleted_at.is_(None))
                        .order_by(crm_units.c.external_id)
                        .limit(_MAX_LISTED_CHILDREN + 1)
                    )
                )
                .scalars()
                .all()
            )
            if live_units:
                raise CrudError(
                    "PARENT_HAS_LIVE_CHILDREN",
                    f"Phân khu '{external_id}' còn {len(live_units)}{'+' if len(live_units) > _MAX_LISTED_CHILDREN else ''} "
                    f"căn đang hoạt động — xoá/chuyển các căn đó trước.",
                    http_status=409,
                    detail={
                        "parent_entity": "area",
                        "parent_external_id": external_id,
                        "child_entity": "unit",
                        "child_external_ids": live_units[:_MAX_LISTED_CHILDREN],
                    },
                )

            row = (
                (
                    await session.execute(
                        sa.update(crm_areas)
                        .where(crm_areas.c.external_id == external_id)
                        .values(status="archived", source_revision=current["source_revision"] + 1, updated_at=now)
                        .returning(crm_areas)
                    )
                )
                .mappings()
                .one()
            )
            project_external_id = await _project_external_id_for(session, row["project_id"])
            written = _area_record({**_row(row), "external_project_id": project_external_id})
            v2_batch_id = sync_client.new_hierarchy_batch_id("areas")
            v2_envelope = sync_client.build_area_envelope(
                [written], batch_id=v2_batch_id, external_project_id=project_external_id, operation="delete"
            )
            await _capture_v2(session, entity="areas", batch_id=v2_batch_id, envelope=v2_envelope)
    return written


async def _resolve_area_reference(
    session: AsyncSession,
    *,
    external_area_id: str | None = None,
    area_name: str | None = None,
    unit_type: str | None = None,
):
    """Tra một phân khu CÒN HOẠT ĐỘNG, bằng MỘT trong hai cách — xem
    `schemas.UnitCreate` để biết vì sao có hai cách.

    Trả về dòng `crm_areas` (đủ để lấy `id`, `project_id`, `area_name`,
    `unit_type` hiện tại). KHÔNG khoá dòng (`with_for_update`): lời gọi này chỉ
    ĐỌC phân khu để gắn một căn vào nó, không sửa phân khu — khoá ở đây sẽ chỉ
    làm chậm các request Unit đồng thời mà không bảo vệ thêm điều gì.
    """
    if external_area_id is not None:
        row = (
            (await session.execute(sa.select(crm_areas).where(crm_areas.c.external_id == external_area_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CrudError(
                "AREA_NOT_FOUND", f"Không tìm thấy phân khu '{external_area_id}' cục bộ.", http_status=422
            )
    else:
        rows = (
            (
                await session.execute(
                    sa.select(crm_areas).where(
                        crm_areas.c.area_name == area_name,
                        crm_areas.c.unit_type == unit_type,
                        crm_areas.c.status == "active",
                    )
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            raise CrudError(
                "AREA_NOT_FOUND",
                f"Không tìm thấy phân khu còn hoạt động khớp ('{area_name}', '{unit_type}'). "
                f"Tạo phân khu trước, hoặc dùng `external_area_id` nếu đã biết chính xác.",
                http_status=422,
            )
        if len(rows) > 1:
            raise CrudError(
                "AMBIGUOUS_AREA_REFERENCE",
                f"Có {len(rows)} phân khu khớp ('{area_name}', '{unit_type}') ở các dự án khác nhau — "
                f"dùng `external_area_id` để chọn đúng một, thay vì tên.",
                http_status=422,
            )
        row = rows[0]

    if row["status"] == "archived":
        raise CrudError(
            "PARENT_ARCHIVED",
            f"Phân khu '{row['external_id']}' đã lưu trữ — không gắn căn vào một phân khu đã lưu trữ.",
            http_status=409,
        )
    return row


# --- Căn hộ ------------------------------------------------------------------


# Chỉ mục duy nhất TỪNG PHẦN (`WHERE deleted_at IS NULL`), không phải một
# UNIQUE CONSTRAINT — nên không dùng được với `ON CONFLICT ON CONSTRAINT`, và
# tên nó chỉ lộ ra trong thông điệp của `IntegrityError` (đúng cách `create_area`
# đang nhận diện khoá tự nhiên của phân khu). Vì `WHERE deleted_at IS NULL`:
# một căn ĐÃ tombstone TRẢ LẠI mã của nó cho căn mới, và cùng `unit_code` ở HAI
# phân khu khác nhau vẫn hợp lệ (khoá gồm cả `area_name`/`unit_type`).
_UNIT_NATURAL_KEY_CONSTRAINT = "uq_crm_units_live_code"


def _unit_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": row["external_id"],
        "area_name": row["area_name"],
        "unit_type": row["unit_type"],
        "unit_code": row["unit_code"],
        "unit_status": row["unit_status"],
        "source_revision": row["source_revision"],
    }


async def list_units(*, include_deleted: bool = False) -> list[dict[str, Any]]:
    query = sa.select(crm_units).order_by(crm_units.c.external_id)
    if not include_deleted:
        query = query.where(crm_units.c.deleted_at.is_(None))
    async with get_session_factory()() as session:
        return [_row(r) for r in (await session.execute(query)).mappings().all()]


async def get_unit(external_id: str, *, include_deleted: bool = True) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            (await session.execute(sa.select(crm_units).where(crm_units.c.external_id == external_id)))
            .mappings()
            .one_or_none()
        )
    if row is None or (not include_deleted and row["deleted_at"] is not None):
        raise RecordNotFoundError("căn", external_id)
    return _row(row)


async def create_unit(data: dict[str, Any], *, client=None) -> tuple[dict[str, Any], SyncOutcome]:
    """Tạo căn dưới một phân khu ĐÃ CÓ cục bộ (Phase B — B3: harden theo phân cấp).

    `area_id` LUÔN được gắn cho căn tạo TỪ ĐÂY trở đi — không có đường nào bỏ qua
    `_resolve_area_reference`, kể cả đường (area_name, unit_type) kế thừa. 163 căn
    tạo TRƯỚC Phase B có `area_id IS NULL`; đường này không tạo thêm dòng nào như
    vậy nữa.
    """
    now = datetime.now(UTC)
    batch_id = sync_client.new_batch_id("units")

    async with get_session_factory()() as session:
        async with session.begin():
            area = await _resolve_area_reference(
                session,
                external_area_id=data.get("external_area_id"),
                area_name=data.get("area_name"),
                unit_type=data.get("unit_type"),
            )
            external_id = await next_external_id(session, "units")
            try:
                row = (
                    (
                        await session.execute(
                            sa.insert(crm_units)
                            .values(
                                id=uuid.uuid4(),
                                external_id=external_id,
                                area_id=area["id"],
                                # Bản sao TẠI THỜI ĐIỂM GHI — xem docstring
                                # `models.py::crm_units.area_id` và migration 0003
                                # mục 2. Nguồn sự thật là `crm_areas`.
                                area_name=area["area_name"],
                                unit_type=area["unit_type"],
                                unit_code=data["unit_code"],
                                unit_status=data["unit_status"],
                                # Bản ghi mới bắt đầu ở 1, không phải 0: hợp đồng cho
                                # phép `source_revision >= 0`, nhưng dùng 0 làm phiên
                                # bản đầu khiến "chưa mirrored" (NULL/0) và "đã mirrored
                                # ở bản đầu" không phân biệt được ở phía Mini CRM.
                                source_revision=1,
                                deleted_at=None,
                                created_at=now,
                                updated_at=now,
                            )
                            .returning(crm_units)
                        )
                    )
                    .mappings()
                    .one()
                )
            except IntegrityError as exc:
                # Cùng khuôn với `create_area`: chỉ NHẬN DIỆN đúng khoá tự nhiên
                # của căn, mọi IntegrityError khác được ném tiếp NGUYÊN VẸN —
                # gắn nhãn 409 cho một lỗi FK/CHECK sẽ nói với người gọi rằng
                # bản ghi đã tồn tại, trong khi thật ra dữ liệu của họ sai.
                if _UNIT_NATURAL_KEY_CONSTRAINT in str(exc.orig):
                    raise CrudError(
                        "UNIT_NATURAL_KEY_CONFLICT",
                        f"Phân khu '{area['external_id']}' đã có căn còn hiệu lực mang mã "
                        f"'{data['unit_code']}' (khoá tự nhiên: area_name, unit_type, unit_code — "
                        f"chỉ tính căn CHƯA tombstone).",
                        http_status=409,
                    ) from exc
                raise
            written = dict(row)
            envelope = sync_client.build_unit_envelope([_unit_record(written)], batch_id=batch_id)
            contract.assert_valid(envelope, entity="units")
            outbox_id = await sync_client.SyncClient().enqueue(
                session, batch_id=batch_id, entity="units", payload=envelope
            )

            # Phase C: THÊM một ý định v2 (KHÔNG gửi) — `area` đã tra được ở trên
            # nên context có sẵn, không cần truy vấn lại.
            external_project_id = await _project_external_id_for(session, area["project_id"])
            v2_batch_id = sync_client.new_hierarchy_batch_id("units_v2")
            v2_envelope = sync_client.build_unit_envelope_v2(
                [{**_unit_record(written), "external_area_id": area["external_id"]}],
                batch_id=v2_batch_id,
                external_project_id=external_project_id,
            )
            await _capture_v2(session, entity="units_v2", batch_id=v2_batch_id, envelope=v2_envelope)

    outcome = await deliver(outbox_id, envelope, entity="units", table=crm_units, client=client)
    return _written(written, outcome), outcome


async def update_unit(external_id: str, patch: dict[str, Any], *, client=None) -> tuple[dict[str, Any], SyncOutcome]:
    """Sửa căn. Phase B thêm hai điều so với trước:

    1. **Đổi phân khu (`external_area_id` hoặc `area_name`+`unit_type` trong
       patch) chỉ được phép TRONG CÙNG một dự án** — `AREA_CROSS_PROJECT_MOVE`
       nếu không (`phase_a_domain_freeze.md` §A1.7). Căn CHƯA từng có phân khu
       cục bộ (`area_id IS NULL`, di sản trước Phase B) không có "dự án hiện tại"
       để so — gắn phân khu lần đầu cho nó luôn được chấp nhận.
    2. **KHÔNG đổi phân khu** thì `area_name`/`unit_type` vẫn được LÀM TƯƠI từ
       `crm_areas` (nếu căn đã có `area_id`) ở MỌI lần ghi — snapshot-tại-thời-
       điểm-ghi, cùng triết lý với việc dựng phong bì từ dòng đã ghi.
    """
    if not patch:
        raise CrudError("EMPTY_PATCH", "Body rỗng — không có gì để sửa")

    now = datetime.now(UTC)
    batch_id = sync_client.new_batch_id("units")

    # Ba khoá "tham chiếu phân khu" không phải cột của `crm_units` — tách chúng
    # ra TRƯỚC khi patch còn lại được splat thẳng vào `.values(**patch)`.
    external_area_id = patch.pop("external_area_id", None)
    area_name_in = patch.pop("area_name", None)
    unit_type_in = patch.pop("unit_type", None)
    area_move_requested = external_area_id is not None or area_name_in is not None or unit_type_in is not None

    async with get_session_factory()() as session:
        async with session.begin():
            # `FOR UPDATE`: hai lần PATCH song song mà không khoá dòng sẽ cùng đọc
            # revision N và cùng ghi N+1 — hai trạng thái khác nhau gửi đi ở cùng
            # một phiên bản, và backend báo `conflict` cho đúng cái nó thấy.
            current = (
                (
                    await session.execute(
                        sa.select(crm_units).where(crm_units.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("căn", external_id)
            if current["deleted_at"] is not None:
                raise CrudError(
                    "RECORD_TOMBSTONED",
                    f"Căn '{external_id}' đã xoá. `external_id` không bao giờ được dùng lại — "
                    f"tạo một căn mới thay vì hồi sinh id này.",
                    http_status=409,
                )

            if area_move_requested:
                new_area = await _resolve_area_reference(
                    session, external_area_id=external_area_id, area_name=area_name_in, unit_type=unit_type_in
                )
                if current["area_id"] is not None:
                    current_project_id = await session.scalar(
                        sa.select(crm_areas.c.project_id).where(crm_areas.c.id == current["area_id"])
                    )
                    if current_project_id != new_area["project_id"]:
                        raise CrudError(
                            "AREA_CROSS_PROJECT_MOVE",
                            f"Căn '{external_id}' không thể chuyển sang phân khu '{new_area['external_id']}' — "
                            f"phân khu đó thuộc một dự án khác. Chỉ được chuyển phân khu TRONG CÙNG một dự án.",
                            http_status=409,
                        )
                patch["area_id"] = new_area["id"]
                patch["area_name"] = new_area["area_name"]
                patch["unit_type"] = new_area["unit_type"]
            elif current["area_id"] is not None:
                fresh = (
                    await session.execute(
                        sa.select(crm_areas.c.area_name, crm_areas.c.unit_type).where(
                            crm_areas.c.id == current["area_id"]
                        )
                    )
                ).one()
                patch["area_name"] = fresh.area_name
                patch["unit_type"] = fresh.unit_type
            # `current["area_id"] is None` và không đổi phân khu: căn di sản, giữ
            # nguyên `area_name`/`unit_type` cũ — không có `crm_areas` nào để làm
            # tươi từ đó.

            row = (
                (
                    await session.execute(
                        sa.update(crm_units)
                        .where(crm_units.c.external_id == external_id)
                        .values(**patch, source_revision=current["source_revision"] + 1, updated_at=now)
                        .returning(crm_units)
                    )
                )
                .mappings()
                .one()
            )
            written = dict(row)
            envelope = sync_client.build_unit_envelope([_unit_record(written)], batch_id=batch_id)
            contract.assert_valid(envelope, entity="units")
            outbox_id = await sync_client.SyncClient().enqueue(
                session, batch_id=batch_id, entity="units", payload=envelope
            )

            # Phase C: THÊM một ý định v2 (KHÔNG gửi). `None` khi căn không (hoặc
            # chưa từng) có phân khu cục bộ — di sản trước Phase B, xem
            # `_hierarchy_context_for_area`.
            context = await _hierarchy_context_for_area(session, written["area_id"])
            if context is not None:
                external_project_id, external_area_id = context
                v2_batch_id = sync_client.new_hierarchy_batch_id("units_v2")
                v2_envelope = sync_client.build_unit_envelope_v2(
                    [{**_unit_record(written), "external_area_id": external_area_id}],
                    batch_id=v2_batch_id,
                    external_project_id=external_project_id,
                )
                await _capture_v2(session, entity="units_v2", batch_id=v2_batch_id, envelope=v2_envelope)

    outcome = await deliver(outbox_id, envelope, entity="units", table=crm_units, client=client)
    return _written(written, outcome), outcome


async def delete_unit(external_id: str, *, client=None) -> tuple[dict[str, Any], SyncOutcome]:
    """Xoá MỀM cục bộ, và gửi lệnh xoá sang backend.

    Không xoá cứng, ở cả hai phía. Xoá cứng ở Mini CRM là mất luôn vết đã từng
    tồn tại; ở backend thì `_tombstone` chỉ đặt `deleted_at`. `external_id` giữ
    nguyên và không bao giờ được cấp lại — dãy sinh id không lùi.

    **Phase B — D-3 (đã CHỐT ở `phase_a_domain_freeze.md` §A1.8, nhưng gắn cờ
    cần chủ dự án xác nhận vì đây là THAY ĐỔI so với hành vi trước Phase B):**
    từ chối nếu còn Deal SỐNG gắn vào căn này. Không cascade — tombstone hộ giao
    dịch đang sống sẽ khiến hệ nguồn (chính Mini CRM, ở một phiên khác) tưởng nó
    vẫn sống và có thể ghi đè lại.
    """
    now = datetime.now(UTC)
    batch_id = sync_client.new_batch_id("units")

    async with get_session_factory()() as session:
        async with session.begin():
            current = (
                (
                    await session.execute(
                        sa.select(crm_units).where(crm_units.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("căn", external_id)
            if current["deleted_at"] is not None:
                raise CrudError("ALREADY_TOMBSTONED", f"Căn '{external_id}' đã xoá rồi", http_status=409)

            live_deals = (
                (
                    await session.execute(
                        sa.select(crm_deals.c.external_id)
                        .where(crm_deals.c.external_unit_id == external_id, crm_deals.c.deleted_at.is_(None))
                        .order_by(crm_deals.c.external_id)
                        .limit(_MAX_LISTED_CHILDREN + 1)
                    )
                )
                .scalars()
                .all()
            )
            if live_deals:
                raise CrudError(
                    "PARENT_HAS_LIVE_CHILDREN",
                    f"Căn '{external_id}' còn {len(live_deals)}{'+' if len(live_deals) > _MAX_LISTED_CHILDREN else ''} "
                    f"giao dịch đang hoạt động — xoá các giao dịch đó trước.",
                    http_status=409,
                    detail={
                        "parent_entity": "unit",
                        "parent_external_id": external_id,
                        "child_entity": "deal",
                        "child_external_ids": live_deals[:_MAX_LISTED_CHILDREN],
                    },
                )

            revision = current["source_revision"] + 1
            written = dict(
                (
                    await session.execute(
                        sa.update(crm_units)
                        .where(crm_units.c.external_id == external_id)
                        .values(deleted_at=now, source_revision=revision, updated_at=now)
                        .returning(crm_units)
                    )
                )
                .mappings()
                .one()
            )
            envelope = sync_client.build_delete_envelope(
                "units", [{"external_id": external_id, "source_revision": revision}], batch_id=batch_id
            )
            contract.assert_valid(envelope, entity="units")
            outbox_id = await sync_client.SyncClient().enqueue(
                session, batch_id=batch_id, entity="units", payload=envelope
            )

            # Phase C: THÊM một ý định v2 xoá (KHÔNG gửi).
            context = await _hierarchy_context_for_area(session, written["area_id"])
            if context is not None:
                external_project_id, _external_area_id = context
                v2_batch_id = sync_client.new_hierarchy_batch_id("units_v2")
                v2_envelope = sync_client.build_unit_envelope_v2(
                    [{"external_id": external_id, "source_revision": revision}],
                    batch_id=v2_batch_id,
                    external_project_id=external_project_id,
                    operation="delete",
                )
                await _capture_v2(session, entity="units_v2", batch_id=v2_batch_id, envelope=v2_envelope)

    outcome = await deliver(outbox_id, envelope, entity="units", table=crm_units, client=client)
    return _written(written, outcome), outcome


# --- Giao dịch ---------------------------------------------------------------


def _deal_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": row["external_id"],
        "external_unit_id": row["external_unit_id"],
        "deal_status": row["deal_status"],
        "reserved_at": row["reserved_at"],
        "sold_at": row["sold_at"],
        "lost_at": row["lost_at"],
        "source_revision": row["source_revision"],
    }


def _check_deal_state(state: dict[str, Any]) -> None:
    """Kiểm trạng thái ↔ mốc thời gian TRƯỚC khi ghi, không đợi ràng buộc CHECK.

    Ràng buộc CHECK của migration 0001 vẫn là chốt cuối và vẫn đúng. Nhưng để nó
    nổ nghĩa là người gọi nhận về một `IntegrityError` với tên ràng buộc — họ phải
    tự dịch `ck_crm_deals_sold_requires_sold_at` sang tiếng người. Kiểm ở đây trả
    về đúng câu cần đọc, và chuỗi kiểm ở dưới vẫn nguyên vẹn nếu đường này thủng.
    """
    status = state["deal_status"]
    required = contract.STATUS_REQUIRES_TIMESTAMP.get(status)
    if required and state.get(required) is None:
        raise CrudError(
            "MISSING_STATUS_TIMESTAMP",
            f"Trạng thái '{status}' bắt buộc phải có '{required}'. "
            f"Backend cũng từ chối bằng MISSING_STATUS_DATE — chặn ở đây thì payload không rời khỏi máy.",
        )
    reserved_at, sold_at = state.get("reserved_at"), state.get("sold_at")
    if reserved_at and sold_at and sold_at < reserved_at:
        raise CrudError(
            "SOLD_BEFORE_RESERVED",
            f"sold_at ({sold_at.isoformat()}) sớm hơn reserved_at ({reserved_at.isoformat()})",
        )


async def _require_mirrored_unit(session: AsyncSession, external_unit_id: str) -> None:
    """Căn phải TỒN TẠI cục bộ và ĐÃ lên tới backend. Xem docstring module."""
    row = (
        (
            await session.execute(
                sa.select(crm_units.c.deleted_at, crm_units.c.mirrored_revision).where(
                    crm_units.c.external_id == external_unit_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise CrudError(
            "UNIT_NOT_FOUND",
            f"Căn '{external_unit_id}' không tồn tại trong Mini CRM. Tạo căn trước khi tạo giao dịch.",
        )
    if row["deleted_at"] is not None:
        raise CrudError(
            "UNIT_TOMBSTONED",
            f"Căn '{external_unit_id}' đã xoá — không gắn giao dịch mới vào một căn đã chết.",
            http_status=409,
        )
    if row["mirrored_revision"] is None:
        raise CrudError(
            "UNIT_NOT_MIRRORED",
            f"Căn '{external_unit_id}' chưa được backend nhận. Backend tra căn theo "
            f"(source_instance_id, external_unit_id) trong bảng units của nó và sẽ từ chối giao dịch này "
            f"bằng UNKNOWN_UNIT_REFERENCE. Gửi lại lô của căn trước (POST /outbox/{{batch_id}}/resend).",
            http_status=409,
        )


async def _reject_second_holding_deal(session: AsyncSession, external_unit_id: str, status: str, *, exclude: str | None = None) -> None:
    """Một căn chỉ có MỘT giao dịch đang giữ. Soi gương `uq_deals_active_per_unit`.

    Chỉ mục duy nhất từng phần ở migration 0001 cũng cưỡng chế điều này, nhưng nó
    nói bằng tên chỉ mục. Ở đây nói bằng tiếng người, và kèm luôn id của giao dịch
    đang chặn — thứ mà tên ràng buộc không bao giờ nói được.
    """
    if status not in HOLDING_STATUSES:
        return
    condition = [
        crm_deals.c.external_unit_id == external_unit_id,
        crm_deals.c.deal_status.in_(tuple(HOLDING_STATUSES)),
        crm_deals.c.deleted_at.is_(None),
    ]
    if exclude is not None:
        condition.append(crm_deals.c.external_id != exclude)
    holder = await session.scalar(sa.select(crm_deals.c.external_id).where(*condition))
    if holder is not None:
        raise CrudError(
            "UNIT_ALREADY_HELD",
            f"Căn '{external_unit_id}' đang được giữ bởi giao dịch '{holder}'. "
            f"Đóng giao dịch đó (lost) trước khi mở giao dịch giữ mới.",
            http_status=409,
        )


async def list_deals(*, include_deleted: bool = False) -> list[dict[str, Any]]:
    query = sa.select(crm_deals).order_by(crm_deals.c.external_id)
    if not include_deleted:
        query = query.where(crm_deals.c.deleted_at.is_(None))
    async with get_session_factory()() as session:
        return [_row(r) for r in (await session.execute(query)).mappings().all()]


async def get_deal(external_id: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            (await session.execute(sa.select(crm_deals).where(crm_deals.c.external_id == external_id)))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RecordNotFoundError("giao dịch", external_id)
    return _row(row)


async def create_deal(data: dict[str, Any], *, client=None) -> tuple[dict[str, Any], SyncOutcome]:
    now = datetime.now(UTC)
    batch_id = sync_client.new_batch_id("deals")
    _check_deal_state(data)

    async with get_session_factory()() as session:
        async with session.begin():
            await _require_mirrored_unit(session, data["external_unit_id"])
            await _reject_second_holding_deal(session, data["external_unit_id"], data["deal_status"])

            external_id = await next_external_id(session, "deals")
            row = (
                (
                    await session.execute(
                        sa.insert(crm_deals)
                        .values(
                            id=uuid.uuid4(),
                            external_id=external_id,
                            external_unit_id=data["external_unit_id"],
                            deal_status=data["deal_status"],
                            reserved_at=data.get("reserved_at"),
                            sold_at=data.get("sold_at"),
                            lost_at=data.get("lost_at"),
                            source_revision=1,
                            deleted_at=None,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(crm_deals)
                    )
                )
                .mappings()
                .one()
            )
            written = dict(row)
            envelope = sync_client.build_deal_envelope([_deal_record(written)], batch_id=batch_id)
            contract.assert_valid(envelope, entity="deals")
            outbox_id = await sync_client.SyncClient().enqueue(
                session, batch_id=batch_id, entity="deals", payload=envelope
            )

            # Phase C: THÊM một ý định v2 (KHÔNG gửi). Bối cảnh suy qua căn
            # (§A3.5) — `None` khi căn là di sản không phân khu.
            context = await _hierarchy_context_for_unit(session, written["external_unit_id"])
            if context is not None:
                external_project_id, _external_area_id = context
                v2_batch_id = sync_client.new_hierarchy_batch_id("deals_v2")
                v2_envelope = sync_client.build_deal_envelope_v2(
                    [_deal_record(written)], batch_id=v2_batch_id, external_project_id=external_project_id
                )
                await _capture_v2(session, entity="deals_v2", batch_id=v2_batch_id, envelope=v2_envelope)

    outcome = await deliver(outbox_id, envelope, entity="deals", table=crm_deals, client=client)
    return _written(written, outcome), outcome


async def update_deal(external_id: str, patch: dict[str, Any], *, client=None) -> tuple[dict[str, Any], SyncOutcome]:
    """Sửa một giao dịch. Mốc lịch sử KHÔNG gửi thì GIỮ NGUYÊN.

    `patch` chỉ chứa những khoá THẬT SỰ có trong body (router lọc bằng
    `model_fields_set`), nên `PATCH {"deal_status": "sold", "sold_at": ...}` không
    đụng tới `reserved_at`, và phong bì dựng lại từ dòng đã ghi vẫn mang nó. Đó
    chính là kịch bản `reserved → sold` của chốt A4, và nó đúng mà không cần một
    dòng mã nào nói riêng về nó.
    """
    if not patch:
        raise CrudError("EMPTY_PATCH", "Body rỗng — không có gì để sửa")

    now = datetime.now(UTC)
    batch_id = sync_client.new_batch_id("deals")

    async with get_session_factory()() as session:
        async with session.begin():
            current = (
                (
                    await session.execute(
                        sa.select(crm_deals).where(crm_deals.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("giao dịch", external_id)
            if current["deleted_at"] is not None:
                raise CrudError("RECORD_TOMBSTONED", f"Giao dịch '{external_id}' đã xoá", http_status=409)

            merged = {**dict(current), **patch}
            _check_deal_state(merged)
            await _reject_second_holding_deal(
                session, current["external_unit_id"], merged["deal_status"], exclude=external_id
            )

            row = (
                (
                    await session.execute(
                        sa.update(crm_deals)
                        .where(crm_deals.c.external_id == external_id)
                        .values(**patch, source_revision=current["source_revision"] + 1, updated_at=now)
                        .returning(crm_deals)
                    )
                )
                .mappings()
                .one()
            )
            written = dict(row)
            envelope = sync_client.build_deal_envelope([_deal_record(written)], batch_id=batch_id)
            contract.assert_valid(envelope, entity="deals")
            outbox_id = await sync_client.SyncClient().enqueue(
                session, batch_id=batch_id, entity="deals", payload=envelope
            )

            context = await _hierarchy_context_for_unit(session, written["external_unit_id"])
            if context is not None:
                external_project_id, _external_area_id = context
                v2_batch_id = sync_client.new_hierarchy_batch_id("deals_v2")
                v2_envelope = sync_client.build_deal_envelope_v2(
                    [_deal_record(written)], batch_id=v2_batch_id, external_project_id=external_project_id
                )
                await _capture_v2(session, entity="deals_v2", batch_id=v2_batch_id, envelope=v2_envelope)

    outcome = await deliver(outbox_id, envelope, entity="deals", table=crm_deals, client=client)
    return _written(written, outcome), outcome


async def delete_deal(external_id: str, *, client=None) -> tuple[dict[str, Any], SyncOutcome]:
    now = datetime.now(UTC)
    batch_id = sync_client.new_batch_id("deals")

    async with get_session_factory()() as session:
        async with session.begin():
            current = (
                (
                    await session.execute(
                        sa.select(crm_deals).where(crm_deals.c.external_id == external_id).with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise RecordNotFoundError("giao dịch", external_id)
            if current["deleted_at"] is not None:
                raise CrudError("ALREADY_TOMBSTONED", f"Giao dịch '{external_id}' đã xoá rồi", http_status=409)

            revision = current["source_revision"] + 1
            written = dict(
                (
                    await session.execute(
                        sa.update(crm_deals)
                        .where(crm_deals.c.external_id == external_id)
                        .values(deleted_at=now, source_revision=revision, updated_at=now)
                        .returning(crm_deals)
                    )
                )
                .mappings()
                .one()
            )
            envelope = sync_client.build_delete_envelope(
                "deals", [{"external_id": external_id, "source_revision": revision}], batch_id=batch_id
            )
            contract.assert_valid(envelope, entity="deals")
            outbox_id = await sync_client.SyncClient().enqueue(
                session, batch_id=batch_id, entity="deals", payload=envelope
            )

            context = await _hierarchy_context_for_unit(session, written["external_unit_id"])
            if context is not None:
                external_project_id, _external_area_id = context
                v2_batch_id = sync_client.new_hierarchy_batch_id("deals_v2")
                v2_envelope = sync_client.build_deal_envelope_v2(
                    [{"external_id": external_id, "source_revision": revision}],
                    batch_id=v2_batch_id,
                    external_project_id=external_project_id,
                    operation="delete",
                )
                await _capture_v2(session, entity="deals_v2", batch_id=v2_batch_id, envelope=v2_envelope)

    outcome = await deliver(outbox_id, envelope, entity="deals", table=crm_deals, client=client)
    return _written(written, outcome), outcome


# --- Sổ gửi đi ---------------------------------------------------------------

_TABLE_FOR_ENTITY = {"units": crm_units, "deals": crm_deals}


async def list_outbox(*, limit: int = 50, offset: int = 0, entity: str | None = None) -> tuple[list[dict], int]:
    condition = [] if entity is None else [crm_outbox.c.entity == entity]
    async with get_session_factory()() as session:
        total = await session.scalar(sa.select(sa.func.count()).select_from(crm_outbox).where(*condition))
        rows = (
            (
                await session.execute(
                    sa.select(crm_outbox)
                    .where(*condition)
                    .order_by(crm_outbox.c.created_at.desc(), crm_outbox.c.external_batch_id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .mappings()
            .all()
        )
    return [_row(r) for r in rows], (total or 0)


async def get_outbox(batch_id: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            (await session.execute(sa.select(crm_outbox).where(crm_outbox.c.external_batch_id == batch_id)))
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RecordNotFoundError("lô", batch_id)
    return _row(row)


async def resend(batch_id: str, *, client=None) -> SyncOutcome:
    """Gửi lại NGUYÊN VĂN lô cũ, dưới ĐÚNG `external_batch_id` cũ.

    Đây là phép thử idempotency, không phải một cơ chế phục hồi. Backend nhận lại
    một `external_batch_id` đã xử lý thì trả kết quả ĐÃ LƯU kèm `replayed=true` và
    HTTP 200 — nó không chạy lại bất cứ thứ gì, nên không có bản ghi nghiệp vụ thứ
    hai và không có tác nhân nào phía sau bị kích hoạt lần nữa.

    Payload lấy từ cột `payload` chứ KHÔNG dựng lại từ trạng thái hiện tại. Dựng
    lại sẽ gửi đi một phong bì khác dưới danh nghĩa lô cũ, và lúc đó "gửi lại" lặng
    lẽ biến thành "ghi đè" — không ai còn biết lô đó rốt cuộc chứa gì.

    **Phase C — ranh giới v2:** một lô v2 (`entity` ∈ `sync_client.V2_CAPTURE_ENTITIES`)
    bị từ chối TRƯỚC khi chạm `deliver()`. Nó chưa từng có ý định rời khỏi máy —
    xem "Phase C: bắt giữ ý định v2" ở đầu file. Gọi `deliver()` cho nó sẽ hoặc ném
    `ValueError` không kiểm soát được (entity lạ với `sync_client.ENTITY_PATH`),
    hoặc — nếu ai đó lỡ mở rộng `ENTITY_PATH` — âm thầm phụ thuộc vào việc phía
    nhận chấp nhận v2, đúng điều Phase C bị cấm làm.
    """
    row = await get_outbox(batch_id)
    _reject_v2_delivery(row)
    return await deliver(
        row["id"],
        row["payload"],
        entity=row["entity"],
        table=_TABLE_FOR_ENTITY.get(row["entity"]),
        client=client,
    )


def _payload_revisions(payload: dict[str, Any]) -> dict[str, int]:
    return {
        record["external_id"]: record["source_revision"]
        for record in payload.get("records", [])
        if "external_id" in record and "source_revision" in record
    }


async def _is_stale(session: AsyncSession, row: dict[str, Any]) -> bool:
    """Lô này có THẬT SỰ cũ hơn trạng thái hiện tại không.

    Kiểm thật thay vì tin lời gọi: phát lại một lô KHÔNG cũ sẽ không cho
    `skip_stale` — nó cho `duplicate_noop` hoặc `conflict` — và một phép thử tự
    khẳng định kết quả mà không kiểm điều kiện tiền đề thì chứng minh được số không.
    """
    table = _TABLE_FOR_ENTITY.get(row["entity"])
    if table is None:
        return False
    revisions = _payload_revisions(row["payload"])
    if not revisions:
        return False
    for external_id, revision in revisions.items():
        current = await session.scalar(
            sa.select(table.c.source_revision).where(table.c.external_id == external_id)
        )
        if current is None or current <= revision:
            return False
    return True


async def replay_stale(batch_id: str | None = None, *, client=None) -> tuple[str, SyncOutcome]:
    """Phát lại một payload CŨ dưới một `external_batch_id` MỚI.

    **Vì sao batch id phải MỚI.** Gửi lại đúng batch id cũ thì backend nhận ra lô
    đã xử lý và trả kết quả đã lưu (`replayed=true`) — nó không bao giờ chạy tới
    tầng so phiên bản, nên `skip_stale` không thể xuất hiện. Hai thao tác nghe
    giống nhau nhưng kiểm hai chốt hoàn toàn khác: `resend` kiểm tính bất biến của
    LÔ, `replay-stale` kiểm thứ tự PHIÊN BẢN. Chỉ có `external_batch_id` là đổi;
    mọi bản ghi, mọi `source_revision` giữ nguyên nguyên văn.

    `replay_of` ghi lại lô gốc để chuỗi này còn truy ngược được sau đó.
    """
    async with get_session_factory()() as session:
        if batch_id is not None:
            row = (
                (await session.execute(sa.select(crm_outbox).where(crm_outbox.c.external_batch_id == batch_id)))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RecordNotFoundError("lô", batch_id)
            candidate = _row(row)
            _reject_v2_delivery(candidate)
            if not await _is_stale(session, candidate):
                raise CrudError(
                    "BATCH_NOT_STALE",
                    f"Lô '{batch_id}' không cũ hơn trạng thái hiện tại. Phát lại nó sẽ cho "
                    f"duplicate_noop hoặc conflict, KHÔNG phải skip_stale. Sửa bản ghi lên một "
                    f"phiên bản mới hơn trước đã.",
                    http_status=409,
                )
        else:
            candidate = None
            rows = (
                (await session.execute(sa.select(crm_outbox).order_by(crm_outbox.c.created_at.desc())))
                .mappings()
                .all()
            )
            for row in rows:
                if row["replay_of"] is None and await _is_stale(session, _row(row)):
                    candidate = _row(row)
                    break
            if candidate is None:
                raise CrudError(
                    "NO_STALE_BATCH",
                    "Không có lô nào đã cũ hơn trạng thái hiện tại. Sửa một bản ghi trước "
                    "(PATCH) để lô cũ của nó trở thành lô cũ thật sự.",
                    http_status=409,
                )

    source_batch_id = candidate["external_batch_id"]
    replay_batch_id = sync_client.new_batch_id(candidate["entity"])
    envelope = {**candidate["payload"], "external_batch_id": replay_batch_id}
    contract.assert_valid(envelope, entity=candidate["entity"])

    async with get_session_factory()() as session:
        async with session.begin():
            outbox_id = await sync_client.SyncClient().enqueue(
                session,
                batch_id=replay_batch_id,
                entity=candidate["entity"],
                payload=envelope,
                replay_of=source_batch_id,
            )

    # `table=None`: một lô cũ KHÔNG được đóng dấu mirrored. Nó không đưa backend
    # tới phiên bản nào mới cả — backend bỏ qua nó bằng `skip_stale`.
    outcome = await deliver(outbox_id, envelope, entity=candidate["entity"], table=None, client=client)
    return source_batch_id, outcome
