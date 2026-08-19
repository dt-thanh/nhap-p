"""SyncRunService — vòng đời một lô đồng bộ JSON.

Ghép ba mảnh đã có thành một đường chạy: `JsonPayloadParser` đọc phong bì,
`SourceIdentityService` quyết định số phận từng bản ghi, `upload_files` /
`upload_errors` ghi lại kết quả. Bảng lô KHÔNG đổi tên — xem 0006.

**Ranh giới transaction.** Toàn bộ phần xử lý bản ghi nằm trong MỘT
`session.begin()`: quyết định danh tính, dòng lỗi, và trạng thái kết thúc của lô
cùng commit hoặc cùng rollback. Vỡ giữa chừng thì trạng thái được ghi ở một
transaction KHÁC (`_finalize_failure`) — đúng bài học của S1: lệnh đặt
`status='processing'` nằm trong transaction bị rollback nên lô quay về `pending`
và treo ở đó mãi.

**Idempotent theo lô.** Danh tính lô là `(source_system, source_instance_id,
external_batch_id)`. Gửi lại đúng lô đó trả về lô cũ kèm trạng thái cũ, không xử
lý lại lần nữa — retry mạng không được phép nhân đôi bất cứ thứ gì.

Tầng này CÓ ghi bảng nghiệp vụ. `units` và `deals` tồn tại từ revision
`0007_s3_domain_model`; `DomainProjector` ghi vào chúng bên trong CÙNG transaction
với quyết định danh tính, mỗi bản ghi bọc trong một SAVEPOINT riêng (xem
`apply_records`). Đổi ranh giới transaction ở đây là đổi bảo đảm "quyết định và
bản chiếu cùng sống hoặc cùng chết".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from rq import Retry
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import deals, projects, units, upload_errors, upload_files
from src.services.domain_projection import DomainProjector, ProjectionError
from src.services.history_guard import guard_history, log_explicit_clears, merge_record, read_mirror
from src.services.json_payload import EnvelopeError, JsonPayloadParser, RecordError, SyncEnvelope
from src.services.source_identity import APPLIED_DECISIONS, Identity, SourceIdentityService
from src.services.sync_payloads import RawPayload, SyncPayloadService
from src.task_queue import INGEST_QUEUE, get_queue

log = get_logger("src.services.sync_runs")

# Trần số dòng lỗi ghi xuống DB cho một lô — cùng lý do với MAX_PERSISTED_ERRORS
# của luồng CSV.
MAX_PERSISTED_ERRORS = 1000

DECISION_KEYS = ("insert", "update", "skip_stale", "duplicate_noop", "conflict", "tombstone")

# Bảng nghiệp vụ đã đổi thế nào — tách khỏi DECISION_KEYS vì hai câu hỏi khác
# nhau: quyết định nói bản đến có được chấp nhận không, còn đây nói bản sao đổi ra sao.
PROJECTION_KEYS = ("inserted", "updated", "tombstoned", "untouched", "rejected")

# Ba hành động THỰC SỰ đổi bản sao. `untouched` và `rejected` không đổi gì nên
# không kích hoạt tính lại — xếp hàng theo `decisions` sẽ sai, vì `skip_stale`,
# `duplicate_noop` và `conflict` đều là quyết định hợp lệ mà không ghi gì cả.
MIRROR_CHANGING_ACTIONS = ("inserted", "updated", "tombstoned")

# Trạng thái kết thúc mà một lô còn chạy lại được. `completed` không có gì để
# sửa; `pending`/`processing` có thể đang chạy dở, chồng lên sẽ có hai tiến
# trình cùng ghi một lô.
REPROCESSABLE_STATUSES = frozenset({"failed", "partially_completed"})


class SyncRejectedError(Exception):
    """Lô bị từ chối trước khi xử lý bản ghi nào (phong bì sai, dự án không có)."""

    def __init__(self, error_code: str, message: str, *, json_path: str = "$") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.json_path = json_path


@dataclass(slots=True)
class SyncRunResult:
    """Kết quả một lần gọi API đồng bộ."""

    sync_run_id: str
    status: str
    replayed: bool = False
    rows_received: int = 0
    rows_ok: int = 0
    rows_failed: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    projections: dict[str, int] = field(default_factory=dict)


def _empty_decisions() -> dict[str, int]:
    return dict.fromkeys(DECISION_KEYS, 0)


def _empty_projections() -> dict[str, int]:
    return dict.fromkeys(PROJECTION_KEYS, 0)


@dataclass(slots=True)
class RecordOutcomes:
    """Kết quả chạy hết các bản ghi của một phong bì, trong một session cho sẵn."""

    decisions: dict[str, int] = field(default_factory=_empty_decisions)
    projections: dict[str, int] = field(default_factory=_empty_projections)
    errors: list[RecordError] = field(default_factory=list)
    # bảng → id các dòng vừa đổi; dùng để suy ra phân khu cần tính lại.
    touched: dict[str, set[uuid.UUID]] = field(default_factory=dict)
    history_timestamps_cleared: int = 0


async def apply_records(
    session: AsyncSession,
    envelope: SyncEnvelope,
    *,
    sync_run_id: uuid.UUID,
    project_id: uuid.UUID,
    now: datetime,
    preserve_dropped: bool,
) -> RecordOutcomes:
    """Chạy hết bản ghi của một phong bì trong session (và transaction) CỦA BÊN GỌI.

    Tách khỏi `SyncRunService._process` để bộ kiểm phù hợp
    (`scripts/conformance_check.py`) chạy **đúng đường này**, không phải một bản
    sao. Một bộ kiểm đi đường riêng sẽ trôi khỏi đường thật đúng vào lúc nó cần
    chính xác nhất — lần đầu có payload CRM thật.

    Khác biệt duy nhất giữa hai phía gọi là **ai sở hữu transaction**: luồng đồng
    bộ commit, bộ kiểm luôn rollback. Không có cờ `dry_run` nào ở đây, và cố ý
    như vậy: một cờ như thế chỉ cách một lần đặt sai giá trị là biến luồng ghi
    thật thành no-op im lặng.

    KHÔNG ghi `upload_files`/`upload_errors` — lỗi được trả về cho bên gọi tự xử.
    """
    result = RecordOutcomes()
    identity_service = SourceIdentityService(session)
    projector = DomainProjector(session)

    identities = [
        Identity(
            source_system=envelope.source_system,
            source_instance_id=envelope.source_instance_id,
            source_entity=envelope.source_entity,
            source_record_id=record.source_record_id,
        )
        for record in envelope.records
    ]

    # KHOÁ TRƯỚC, theo thứ tự tất định. `_load()` cũng khoá dòng của riêng nó khi
    # tới lượt, nhưng nếu CHỈ có đường đó thì thứ tự khoá bằng thứ tự bản ghi
    # trong phong bì — và hai lô chứa cùng hai bản ghi theo hai thứ tự ngược nhau
    # sẽ khoá chéo nhau rồi deadlock. Hợp đồng không quy định thứ tự bản ghi, nên
    # không được trông cậy vào việc hai hệ nguồn tình cờ xếp giống nhau.
    #
    # Bước này KHÔNG đổi thứ tự XỬ LÝ: vòng lặp dưới vẫn chạy theo đúng thứ tự
    # phong bì, nên `json_path` và thứ tự lỗi trả về giữ nguyên.
    #
    # Nằm NGOÀI SAVEPOINT của từng bản ghi, và phải thế: một SAVEPOINT bị cuộn lại
    # sẽ nhả những khoá lấy được bên trong nó, và bản ghi kế tiếp sẽ chạy mà không
    # còn được bảo vệ.
    await identity_service.lock_identities(identities)

    for original, identity in zip(envelope.records, identities, strict=True):
        record = original
        # SAVEPOINT quanh từng bản ghi. Quyết định danh tính và việc chiếu xuống
        # bảng nghiệp vụ phải CÙNG sống hoặc CÙNG chết: nếu chiếu hỏng mà quyết
        # định vẫn nằm lại thì `crm_source_records` sẽ nói "đã chấp nhận" trong
        # khi bản sao chưa có gì — và lần gửi sau sẽ bị bỏ qua vì "đã có rồi".
        try:
            async with session.begin_nested():
                # Chốt A4 (Phase 8B). Đọc bản sao TRƯỚC khi ghi, hợp nhất bản ghi
                # partial rồi băm lại theo trạng thái cuối — dấu vân phải mô tả
                # kết quả, không phải cách viết. Xem `src/services/history_guard.py`.
                mirror = await read_mirror(
                    session,
                    entity=envelope.source_entity,
                    source_instance_id=envelope.source_instance_id,
                    source_record_id=record.source_record_id,
                )
                record = merge_record(record, mirror, entity=envelope.source_entity)

                outcome = await identity_service.apply(
                    record,
                    identity=identity,
                    sync_run_id=sync_run_id,
                    external_batch_id=envelope.external_batch_id,
                    now=now,
                )

                # SAU quyết định phiên bản: một bản ghi cũ hay trùng vốn không ghi
                # gì, từ chối nó vì đánh rơi lịch sử là báo lỗi cho một thao tác
                # không hề xảy ra.
                record = guard_history(
                    record,
                    mirror,
                    entity=envelope.source_entity,
                    decision=outcome.decision,
                    preserve=preserve_dropped,
                )
                result.history_timestamps_cleared += len(
                    log_explicit_clears(record, mirror, entity=envelope.source_entity, decision=outcome.decision)
                )

                projection = await projector.project(
                    record,
                    outcome,
                    source_entity=envelope.source_entity,
                    source_system=envelope.source_system,
                    source_instance_id=envelope.source_instance_id,
                    project_id=project_id,
                    now=now,
                )
        except ProjectionError as exc:
            result.projections["rejected"] += 1
            result.errors.append(
                RecordError(
                    json_path=exc.json_path,
                    error_code=exc.error_code,
                    message=exc.message,
                    error_category=exc.error_category,
                    source_record_id=exc.source_record_id,
                    field_name=exc.field_name,
                    raw_value_redacted=exc.raw_value_redacted,
                )
            )
            continue
        except IntegrityError as exc:
            # Ràng buộc DB chặn bản ghi này (hay gặp nhất: `uq_deals_active_per_unit`
            # — căn đã có giao dịch đang giữ). SAVEPOINT đã rollback nên các bản
            # ghi khác không bị kéo theo. KHÔNG trả str(exc): nó kèm cả câu SQL và
            # tham số.
            constraint = _constraint_name(exc)
            result.projections["rejected"] += 1
            result.errors.append(
                RecordError(
                    json_path=record.json_path,
                    error_code="CONSTRAINT_VIOLATION",
                    message=(
                        f"Bản ghi vi phạm ràng buộc '{constraint}'"
                        if constraint
                        else "Bản ghi vi phạm ràng buộc của cơ sở dữ liệu"
                    ),
                    error_category="business",
                    source_record_id=record.source_record_id,
                )
            )
            continue

        result.decisions[outcome.decision] += 1
        result.projections[projection.action] += 1

        # Ghi lại dòng vừa CHẠM để suy ra phân khu bị ảnh hưởng. Chỉ ba hành động
        # này thực sự đổi bản sao; `untouched` không đổi gì nên không có gì để
        # tính lại.
        if projection.action in MIRROR_CHANGING_ACTIONS and projection.row_id is not None:
            result.touched.setdefault(projection.table, set()).add(projection.row_id)

        # Đụng độ không phải lỗi định dạng, nhưng người vận hành phải thấy được nó
        # ở cùng chỗ với lỗi khác, nếu không nó chìm mất.
        if outcome.decision == "conflict":
            result.errors.append(
                RecordError(
                    json_path=record.json_path,
                    error_code="VERSION_CONFLICT",
                    message=outcome.reason,
                    error_category="conflict",
                    source_record_id=record.source_record_id,
                )
            )

    return result


class SyncRunService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    async def run(
        self,
        envelope: SyncEnvelope,
        *,
        raw_payload: RawPayload | None = None,
        credential_id: uuid.UUID | None = None,
    ) -> SyncRunResult:
        """Tạo (hoặc nhận lại) lô rồi xử lý toàn bộ bản ghi trong phong bì.

        `raw_payload` được giữ lại nguyên văn cùng transaction tạo lô, nên không
        tồn tại trạng thái "có lô mà không có payload". Lô chạy lại KHÔNG ghi
        payload lần nữa — `uq_sync_payloads_run` chặn, và bản đã lưu mới là bản
        gốc đáng giữ.
        """
        project_uuid = await self._resolve_project(envelope)

        existing = await self._find_existing_run(envelope)
        if existing is not None:
            log.info(
                "sync.run.replayed",
                sync_run_id=str(existing["id"]),
                external_batch_id=envelope.external_batch_id,
            )
            summary = existing["error_summary"] or {}
            return SyncRunResult(
                sync_run_id=str(existing["id"]),
                status=existing["status"],
                replayed=True,
                rows_received=existing["rows_received"],
                rows_ok=existing["rows_ok"],
                rows_failed=existing["rows_failed"],
                decisions=summary.get("decisions", _empty_decisions()),
                projections=summary.get("projections", _empty_projections()),
            )

        sync_run_id = await self._create_run(
            envelope, project_uuid, raw_payload=raw_payload, credential_id=credential_id
        )

        try:
            return await self._process(envelope, sync_run_id, project_uuid)
        except Exception:
            # Transaction xử lý đã rollback khi thoát khỏi `session.begin()`; ghi
            # trạng thái kết thúc ở transaction riêng để lô không treo `pending`.
            # Bắt rộng ở ĐÚNG chỗ này là có chủ đích và exception được ném lại
            # nguyên vẹn — mục đích duy nhất là không bỏ lại lô nửa chừng.
            await self._finalize_failure(sync_run_id)
            raise

    async def reprocess(self, sync_run_id: uuid.UUID) -> SyncRunResult:
        """Chạy lại một lô đã hỏng, từ chính payload thô đã giữ.

        Đây là thứ mà việc lưu payload ở Phase 3 mở ra: một lô hỏng vì nguyên
        nhân đã được sửa (phân khu chưa tạo, căn chưa gửi, ràng buộc DB) có thể
        chạy lại NGUYÊN VĂN mà không cần xin hệ nguồn gửi lại — hệ nguồn có thể
        đã đổi dữ liệu, và lúc đó ta không còn dựng lại được chuyện đã xảy ra.

        An toàn khi gọi nhiều lần: idempotency mức BẢN GHI
        (`crm_source_records`) khiến bản ghi đã áp dụng thành `duplicate_noop`,
        nên chạy lại chỉ đụng tới phần trước đó bị từ chối.

        Chạy lại DÙNG LẠI đúng `sync_run_id` cũ chứ không tạo lô mới: danh tính lô
        là `(source_system, source_instance_id, external_batch_id)` do hệ nguồn
        đặt, và cùng một lô của hệ nguồn phải là một dòng ở đây. Tạo lô mới sẽ
        khiến một lần gửi của CRM hiện ra thành hai lần.

        Chỉ chạy lại lô ở trạng thái kết thúc HỎNG. Lô `completed` không có gì để
        sửa, và lô `pending`/`processing` có thể đang chạy dở — chạy chồng lên sẽ
        cho hai tiến trình cùng ghi một lô.
        """
        run = await self._load_run(sync_run_id)
        if run is None:
            raise SyncRejectedError("SYNC_RUN_NOT_FOUND", f"Không tìm thấy lô '{sync_run_id}'")

        if run["status"] not in REPROCESSABLE_STATUSES:
            raise SyncRejectedError(
                "RUN_NOT_REPROCESSABLE",
                f"Chỉ chạy lại được lô ở trạng thái {', '.join(sorted(REPROCESSABLE_STATUSES))}; "
                f"lô này đang '{run['status']}'",
            )

        stored = await self._load_payload(sync_run_id)
        if stored is None:
            raise SyncRejectedError(
                "PAYLOAD_NOT_RETAINED",
                "Lô này không giữ payload thô nên không chạy lại được. "
                "Hệ nguồn phải gửi lại lô với cùng external_batch_id.",
            )

        envelope = self._rebuild_envelope(stored["payload"], run["source_entity"])

        # Dọn lỗi của lần chạy trước: chúng mô tả một lần chạy đã kết thúc, và để
        # lại thì lỗi cũ chồng lên lỗi mới, không ai phân biệt được lần nào.
        await self._clear_errors(sync_run_id)

        log.info(
            "sync.run.reprocess.started",
            sync_run_id=str(sync_run_id),
            external_batch_id=run["external_batch_id"],
            previous_status=run["status"],
        )

        rerun_project_id = uuid.UUID(str(run["project_id"])) if run["project_id"] is not None else None
        try:
            return await self._process(envelope, sync_run_id, rerun_project_id)
        except Exception:
            await self._finalize_failure(sync_run_id)
            raise

    def _rebuild_envelope(self, payload: dict, source_entity: str | None) -> SyncEnvelope:
        """Payload thô đã lưu → phong bì, qua đúng đường mà lô gốc đã đi."""
        from src.services.contract_adapter import adapt, is_contract_v1

        raw = adapt(payload, entity_from_route=source_entity) if is_contract_v1(payload) else payload
        try:
            return JsonPayloadParser().parse(raw, entity_from_route=source_entity)
        except EnvelopeError as exc:
            # Payload đã qua cổng hợp đồng lúc nhận, nên tới đây mà vỡ nghĩa là
            # bản lưu bị hỏng hoặc luật đã đổi — cả hai đều cần người xem, không
            # phải thứ nuốt đi.
            raise SyncRejectedError(
                "RETAINED_PAYLOAD_UNPARSEABLE",
                f"Payload đã lưu không dựng lại được phong bì: {exc.message}",
                json_path=exc.json_path,
            ) from exc

    async def _load_run(self, sync_run_id: uuid.UUID) -> dict | None:
        async with self._session_factory() as session:
            row = (
                (await session.execute(sa.select(upload_files).where(upload_files.c.id == sync_run_id)))
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    async def _load_payload(self, sync_run_id: uuid.UUID) -> dict | None:
        async with self._session_factory() as session:
            return await SyncPayloadService().fetch(session, sync_run_id)

    async def _clear_errors(self, sync_run_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id == sync_run_id))

    # --- Vòng đời lô -------------------------------------------------------

    def _parse_project_id(self, raw: str) -> uuid.UUID:
        try:
            return uuid.UUID(raw)
        except ValueError as exc:
            raise SyncRejectedError(
                "INVALID_PROJECT_ID", "'project_id' phải là UUID hợp lệ", json_path="$.project_id"
            ) from exc

    async def _require_project(self, project_id: uuid.UUID) -> None:
        """Lô phải thuộc một dự án có thật — `upload_files.project_id` là NOT NULL."""
        async with self._session_factory() as session:
            found = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_id))
        if found is None:
            raise SyncRejectedError("UNKNOWN_PROJECT", f"Dự án '{project_id}' không tồn tại", json_path="$.project_id")

    async def _resolve_project(self, envelope: SyncEnvelope) -> uuid.UUID | None:
        """Suy ra UUID dự án của lô — chốt cho CẢ v1 lẫn v2.

        v1 (và hình dạng tương thích `{project_id}` của v2): `envelope.project_id`
        LUÔN có mặt (đã kiểm ở `JsonPayloadParser`) — hành vi KHÔNG đổi, dự án
        PHẢI tồn tại trước.

        v2 hình dạng CHUẨN (`external_project_id`): tra `(source_instance_id,
        external_id)`. Tra thấy → dùng UUID đó, dự án PHẢI tồn tại trước (giống
        v1). Tra KHÔNG thấy:
          - lô `source_entity == "projects"` → trả `None`. Dự án đang được TẠO
            trong CHÍNH lô này (§A5.3 "Ngoại lệ đã đóng băng": lô chứa bản ghi
            `entity='project'` khớp `project_ref` thì được coi là hợp lệ) — việc
            xác nhận bản ghi đó CÓ THẬT khớp `external_id` là việc của tầng chiếu
            (`DomainProjector._project_project`), không phải ở đây.
          - lô khác (areas/units/deals) → `PROJECT_NOT_FOUND`: "Thiếu Project ...
            TỪ CHỐI CẢ PHONG BÌ" (§A5.3).
        """
        if envelope.project_id is not None:
            project_uuid = self._parse_project_id(envelope.project_id)
            await self._require_project(project_uuid)
            return project_uuid

        if envelope.external_project_id is None:
            # Chỉ hợp lệ khi `source_entity == "projects"` — `JsonPayloadParser`
            # đã cho qua đúng MỘT trường hợp CẢ HAI cùng vắng: lô tạo dự án mà
            # bản ghi `project` tự đủ danh tính, không cần `project_ref` tra
            # ngược. Không có gì để tra ở đây.
            return None

        async with self._session_factory() as session:
            found = await session.scalar(
                sa.select(projects.c.id).where(
                    projects.c.source_instance_id == envelope.source_instance_id,
                    projects.c.external_id == envelope.external_project_id,
                )
            )
        if found is not None:
            return found
        if envelope.source_entity == "projects":
            return None
        raise SyncRejectedError(
            "PROJECT_NOT_FOUND",
            f"Dự án '{envelope.external_project_id}' chưa được đồng bộ — gửi bản ghi 'project' trước, "
            f"hoặc kèm nó trong cùng lô (đúng thứ tự: project → area → unit → deal).",
            json_path="$.project_ref",
        )

    async def _find_existing_run(self, envelope: SyncEnvelope) -> dict | None:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        sa.select(upload_files).where(
                            upload_files.c.source_system == envelope.source_system,
                            upload_files.c.source_instance_id == envelope.source_instance_id,
                            upload_files.c.external_batch_id == envelope.external_batch_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row is not None else None

    async def _create_run(
        self,
        envelope: SyncEnvelope,
        project_id: uuid.UUID | None,
        *,
        raw_payload: RawPayload | None = None,
        credential_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Tạo bản ghi lô ở `pending`, kèm payload thô, commit ngay.

        Nằm NGOÀI transaction xử lý: nếu xử lý vỡ và rollback, bản ghi lô vẫn còn
        để `_finalize_failure` đánh dấu `failed` — không có nó thì lô biến mất và
        client không tra được gì.

        Payload thô ghi trong CÙNG transaction này chứ không phải sau: một lô đã
        tồn tại mà payload chưa kịp lưu là một lô không chạy lại được, và sự cố
        thường xảy ra đúng vào lúc ta cần chạy lại nhất.
        """
        sync_run_id = uuid.uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.insert(upload_files).values(
                        id=sync_run_id,
                        project_id=project_id,
                        uploaded_by=None,
                        filename=None,  # payload đẩy qua API không có tên file
                        checksum=None,  # byte của payload không phải danh tính lô
                        status="pending",
                        rows_ok=0,
                        rows_failed=0,
                        uploaded_at=datetime.now(UTC),
                        source_system=envelope.source_system,
                        source_instance_id=envelope.source_instance_id,
                        source_entity=envelope.source_entity,
                        input_format="json",
                        transport_mode="api_push",
                        sync_mode=envelope.sync_mode,
                        schema_version=envelope.schema_version,
                        external_batch_id=envelope.external_batch_id,
                        rows_received=envelope.records_received,
                        last_source_cursor=envelope.source_cursor,
                        error_summary={},
                        # Metadata ảnh chụp (0011). NULL với lô tăng dần.
                        snapshot_id=envelope.snapshot_id,
                        chunk_index=envelope.chunk_index,
                        chunk_total=envelope.chunk_total,
                        snapshot_complete=envelope.snapshot_complete,
                        snapshot_scope=envelope.snapshot_scope,
                    )
                )
                if raw_payload is not None:
                    await SyncPayloadService().store(
                        session,
                        sync_run_id=sync_run_id,
                        raw=raw_payload,
                        credential_id=credential_id,
                    )
        return sync_run_id

    async def _process(
        self, envelope: SyncEnvelope, sync_run_id: uuid.UUID, project_id: uuid.UUID | None
    ) -> SyncRunResult:
        errors: list[RecordError] = list(envelope.errors)
        affected_areas: list[uuid.UUID] = []
        preserve_dropped = get_settings().sync_preserve_dropped_timestamps

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.update(upload_files).where(upload_files.c.id == sync_run_id).values(status="processing")
                )

                outcomes = await apply_records(
                    session,
                    envelope,
                    sync_run_id=sync_run_id,
                    project_id=project_id,
                    now=datetime.now(UTC),
                    preserve_dropped=preserve_dropped,
                )
                decisions = outcomes.decisions
                projections = outcomes.projections
                touched = outcomes.touched
                cleared = outcomes.history_timestamps_cleared
                errors.extend(outcomes.errors)
                now = datetime.now(UTC)

                applied = sum(decisions[key] for key in APPLIED_DECISIONS)
                processed = len(envelope.records) - projections["rejected"]
                rejected = envelope.records_received - processed

                await self._persist_errors(session, sync_run_id, errors, now)

                status = self._terminal_status(processed=processed, rejected=rejected, conflicts=decisions["conflict"])
                summary = {
                    "decisions": decisions,
                    "projections": projections,
                    "errors": self._error_breakdown(errors),
                    "applied": applied,
                    # Ngoài `projections` một cách CÓ CHỦ ĐÍCH: ba khoá
                    # inserted/updated/tombstoned là điều kiện xếp hàng tính lại
                    # (Phase 7), thêm một khoá vào đó sẽ đổi số học của một thứ
                    # chẳng liên quan.
                    "history_timestamps_cleared": cleared,
                }
                await session.execute(
                    sa.update(upload_files)
                    .where(upload_files.c.id == sync_run_id)
                    .values(
                        status=status,
                        rows_ok=processed,
                        rows_failed=rejected,
                        finished_at=datetime.now(UTC),
                        error_summary=summary,
                    )
                )

                # Giải phân khu TRONG transaction (còn session), nhưng xếp hàng
                # job SAU khi commit — worker chạy sớm hơn commit sẽ đọc phải
                # trạng thái cũ.
                affected_areas = await self._affected_area_ids(session, touched)

        log.info(
            "sync.run.finished",
            sync_run_id=str(sync_run_id),
            source_entity=envelope.source_entity,
            status=status,
            rows_received=envelope.records_received,
            **decisions,
        )

        # Lô đã COMMIT tới đây. Chỉ xếp hàng khi bản sao THỰC SỰ đổi, VÀ khi lô có
        # một dự án thật để tính lại (`project_id`) — một lô `source_entity=
        # 'projects'` không có unit/deal nào để chạm tới, KHÔNG có gì để tính lại.
        # Với lô TẠO dự án (`project_id is None` lúc bắt đầu), `_resolve_project`
        # chỉ cho phép `source_entity == 'projects'`, nên `project_id is None` ở
        # đây LUÔN đồng nghĩa "không có unit/deal nào bị chạm" — bỏ qua an toàn,
        # không phải một trường hợp bị bỏ sót.
        if project_id is not None and sum(projections[key] for key in MIRROR_CHANGING_ACTIONS) > 0:
            self._enqueue_domain_recompute(project_id=project_id, sync_run_id=sync_run_id, area_ids=affected_areas)
        return SyncRunResult(
            sync_run_id=str(sync_run_id),
            status=status,
            rows_received=envelope.records_received,
            rows_ok=processed,
            rows_failed=rejected,
            decisions=decisions,
            projections=projections,
        )

    async def _affected_area_ids(self, session: AsyncSession, touched: dict[str, set[uuid.UUID]]) -> list[uuid.UUID]:
        """Phân khu của những dòng vừa đổi, để tính lại đúng phạm vi đó.

        **KHÔNG lọc `deleted_at`.** Một căn vừa bị tombstone vẫn làm đổi số học
        tồn kho của phân khu chứa nó; bỏ nó ra khỏi phạm vi thì một phân khu vừa
        mất hết căn sẽ không bao giờ được tính lại, và tồn kho hiển thị mãi số
        của những căn đã biến mất.
        """
        area_ids: set[uuid.UUID] = set()

        unit_ids = touched.get("units")
        if unit_ids:
            area_ids.update(
                (await session.execute(sa.select(units.c.area_id).where(units.c.id.in_(unit_ids)))).scalars()
            )

        deal_ids = touched.get("deals")
        if deal_ids:
            area_ids.update(
                (
                    await session.execute(
                        sa.select(units.c.area_id)
                        .select_from(deals.join(units, deals.c.unit_id == units.c.id))
                        .where(deals.c.id.in_(deal_ids))
                    )
                ).scalars()
            )

        return sorted(area_ids, key=str)

    def _enqueue_domain_recompute(
        self, *, project_id: uuid.UUID, sync_run_id: uuid.UUID, area_ids: list[uuid.UUID]
    ) -> None:
        """Xếp hàng tính lại lineage miền. Hỏng thì GHI LOG rồi đi tiếp.

        Lô đồng bộ đã commit trước khi hàm này chạy. Ném lỗi ở đây sẽ báo cho hệ
        nguồn rằng dữ liệu bị từ chối trong khi nó đã được nhận, và mời họ gửi
        lại một lô đã áp dụng xong.

        Cái giá là lineage miền lạc hậu tới lần đồng bộ sau. Chấp nhận được ở
        Phase 7 vì CHƯA CÓ AI ĐỌC nó — mọi dự án vẫn ở `legacy_aggregate`. Khi
        cắt sang thì khoảng trống này thành số liệu sai, nên phải có cơ chế phát
        hiện trước đó: xem `scripts/requeue_missing_domain_recompute.py` và
        `docs/crm/domain_recompute_operations.md`.
        """
        try:
            job = get_queue(INGEST_QUEUE).enqueue(
                "src.jobs.recompute_domain.run_domain_recompute",
                project_id=str(project_id),
                area_ids=[str(area_id) for area_id in area_ids] or None,
                sync_run_id=str(sync_run_id),
                retry=Retry(max=3, interval=[10, 30, 60]),
            )
        except Exception as exc:
            log.error(
                "domain.recompute.enqueue_failed",
                sync_run_id=str(sync_run_id),
                project_id=str(project_id),
                area_ids=[str(area_id) for area_id in area_ids],
                error_type=type(exc).__name__,
                exc_info=exc,
            )
            return

        log.info(
            "domain.recompute.enqueued",
            sync_run_id=str(sync_run_id),
            project_id=str(project_id),
            job_id=job.id,
            areas=len(area_ids),
        )

    def _terminal_status(self, *, processed: int, rejected: int, conflicts: int) -> str:
        """Trạng thái kết thúc, chỉ dựa trên số liệu đếm được thật.

        Phase 5.5 P0 (5A). Bản CŨ coi đụng độ là "chặn" ngang hàng với bản ghi
        hỏng thật: `blocked = rejected + conflicts`, và một lô MỘT bản ghi mà bản
        ghi đó là `conflict` (không có bản ghi hỏng nào khác) rơi vào nhánh
        `processed - conflicts == 0` → `failed`. Đó là lỗi ngữ nghĩa: đụng độ là
        một QUYẾT ĐỊNH HỢP LỆ đã được ghi nhận (không mất dữ liệu, không phải lỗi
        hệ thống), không phải một bản ghi bị từ chối.

        Quy tắc mới, chỉ nhìn vào HAI đại lượng THỰC SỰ khác nghĩa nhau —
        `rejected` (bản ghi hỏng, KHÔNG có quyết định nào) và `processed` (mọi
        bản ghi CÓ quyết định, kể cả `conflict`/`skip_stale`/`duplicate_noop`):

          - `rejected == 0, conflicts == 0`  → `completed`               (sạch)
          - `rejected == 0, conflicts  > 0`  → `completed_with_conflicts` (đụng
            độ được ghi nhận, KHÔNG có bản ghi nào hỏng)
          - `rejected  > 0, processed  > 0`  → `partially_completed`     (trộn)
          - `rejected  > 0, processed == 0`  → `failed`                  (TOÀN
            BỘ bản ghi đều hỏng — đây là định nghĩa đúng của "thất bại toàn phần")

        `replayed` (gửi lại đúng batch id cũ) là một khái niệm KHÁC, đã tách
        riêng ở `SyncRunResult.replayed` — không trộn vào trạng thái này.
        """
        if rejected == 0 and conflicts == 0:
            return "completed"
        if rejected == 0:
            return "completed_with_conflicts"
        if processed > 0:
            return "partially_completed"
        return "failed"

    def _error_breakdown(self, errors: list[RecordError]) -> dict[str, int]:
        breakdown: dict[str, int] = {}
        for error in errors:
            breakdown[error.error_category] = breakdown.get(error.error_category, 0) + 1
        return breakdown

    async def _persist_errors(
        self, session: AsyncSession, sync_run_id: uuid.UUID, errors: list[RecordError], now: datetime
    ) -> None:
        if not errors:
            return
        capped = errors[:MAX_PERSISTED_ERRORS]
        await session.execute(
            sa.insert(upload_errors),
            [{"id": uuid.uuid4(), "created_at": now, **e.as_record(str(sync_run_id))} for e in capped],
        )

    async def _finalize_failure(self, sync_run_id: uuid.UUID) -> None:
        """Đóng lô ở `failed` bằng một transaction riêng.

        Không ném tiếp nếu chính bước này hỏng: exception gốc mới là thứ cần nổi
        lên, che nó đi bằng lỗi thứ hai sẽ làm mất manh mối.
        """
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        sa.update(upload_files)
                        .where(upload_files.c.id == sync_run_id)
                        .values(status="failed", finished_at=datetime.now(UTC))
                    )
        except Exception as exc:
            log.error("sync.run.finalize_failed", error_type=type(exc).__name__, exc_info=exc)


def _constraint_name(exc: IntegrityError) -> str | None:
    """Moi TÊN ràng buộc bị vi phạm, không lấy gì khác.

    Cùng cách với `src/jobs/parse_upload.py`: SQLAlchemy bọc lỗi asyncpg hai lớp,
    `constraint_name` chỉ có ở exception gốc của driver.
    """
    candidate = getattr(exc, "orig", None)
    for _ in range(3):
        if candidate is None:
            return None
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
        candidate = getattr(candidate, "__cause__", None)
    return None
