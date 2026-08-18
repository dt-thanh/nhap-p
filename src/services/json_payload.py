"""JsonPayloadParser — phong bì JSON của CRM → bản ghi staging đã chuẩn hoá.

Đây là đường vào thứ hai của luồng nạp, song song với `ExcelParserService`. Hai
bên khác nhau ở cách ĐỌC, giống nhau ở cái ĐƯA RA: một danh sách bản ghi đã ép
kiểu cộng một danh sách lỗi định vị được. Nhờ vậy tầng lỗi (`upload_errors`) và
tầng quyết định (`SourceIdentityService`) dùng chung cho cả hai.

Parser này CỐ Ý không biết gì về bảng nghiệp vụ. Nó không tra `area_id`, không
ghi DB, và không cần `units` / `deals` tồn tại. Phần `data` của mỗi bản ghi được
giữ nguyên dạng dict và chỉ được băm để so sánh phiên bản — tầng ghi của giai
đoạn sau sẽ là nơi diễn giải nó.

Nguyên tắc từ chối: sai ở PHONG BÌ thì hỏng cả lô (không biết lô này là gì, xử lý
tiếp là đoán mò). Sai ở MỘT bản ghi thì chỉ bản ghi đó hỏng, phần còn lại vẫn đi
tiếp — giống hệt cách file CSV được xử lý.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Phiên bản phong bì mà bản cài đặt này hiểu. Gửi số khác → từ chối rõ ràng,
# không cố đoán ý.
#
# Phase D: {1, 2}. v1 giữ nguyên hành vi TRỌN VẸN — mọi thứ dưới đây vẫn đúng cho
# nó, không đổi một dòng. v2 (Mini CRM là nguồn sự thật cho CẢ BỐN tầng, xem
# `docs/crm/sync_contract_v2_draft.md`) đi qua `contract_adapter.adapt_v2()` rồi
# vào ĐÚNG cùng một `JsonPayloadParser` này — hai phiên bản khác hình dạng phong
# bì gốc, nhưng chung một tầng chuẩn hoá bản ghi kể từ đây.
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})

# Thực thể được phép đồng bộ. `units` / `deals` tồn tại từ `0007_s3_domain_model`.
# `projects` / `areas` THÊM ở Phase D (0017) — Mini CRM sở hữu cả bốn tầng
# (Phase A đảo ngược mô hình sở hữu, đợt (g)); `DomainProjector` là nơi ghi
# xuống — xem `SyncRunService.apply_records`.
SUPPORTED_ENTITIES = frozenset({"units", "deals", "projects", "areas"})

SUPPORTED_OPERATIONS = frozenset({"upsert", "delete"})

# Bản ghi khai nó mang TRẠNG THÁI ĐẦY ĐỦ hay chỉ phần đã đổi (hợp đồng v1 §4.3).
#
# Không suy ra được từ nội dung: một payload thiếu `reserved_at` trông y hệt nhau
# dù hệ nguồn "không theo dõi trường đó" hay "đã xoá nó" hay "chỉ gửi phần đổi".
# Vắng mặt KHÔNG phải một khẳng định, nên bên gửi phải tự khai.
#
# Mặc định `full` để hệ nguồn không khai gì giữ nguyên ngữ nghĩa nghiêm ngặt cũ —
# mặc định `partial` sẽ âm thầm biến mọi payload cũ thành "giữ nguyên hết".
COMPLETENESS_FULL = "full"
COMPLETENESS_PARTIAL = "partial"
SUPPORTED_COMPLETENESS = frozenset({COMPLETENESS_FULL, COMPLETENESS_PARTIAL})

SUPPORTED_SYNC_MODES = frozenset({"full_snapshot", "incremental"})

# Trần số lỗi bản ghi giữ lại cho một lô — cùng lý do với MAX_ERRORS của parser
# Excel: một payload rác không được phép bơm hàng trăm nghìn dòng vào upload_errors.
MAX_RECORD_ERRORS = 1000

# Khoá bị coi là nhạy cảm ở BẤT KỲ đâu trong `data`. Giá trị của chúng không bao
# giờ được đưa vào thông báo lỗi (xem `redact`).
SENSITIVE_KEYS = frozenset(
    {
        "phone",
        "email",
        "full_name",
        "name",
        "note",
        "notes",
        "address",
        "national_id",
        "password",
        "password_hash",
        "token",
        "secret",
        "api_key",
    }
)


class EnvelopeError(Exception):
    """Phong bì sai → cả lô bị từ chối, không bản ghi nào được xử lý."""

    def __init__(self, error_code: str, message: str, *, json_path: str = "$") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.json_path = json_path


@dataclass(frozen=True, slots=True)
class RecordError:
    """Lỗi của MỘT bản ghi trong `records`, định vị bằng `json_path`.

    Không có `row_number`: mảng JSON không có khái niệm dòng. Cột đó trong
    `upload_errors` để NULL (0006 cho phép), `json_path` thay thế vai trò định vị.
    """

    json_path: str
    error_code: str
    message: str
    error_category: str = "field"
    source_record_id: str | None = None
    field_name: str | None = None
    raw_value_redacted: str | None = None

    def as_record(self, file_id: str) -> dict[str, Any]:
        """Đổi sang đúng bộ cột `upload_errors` (0006)."""
        return {
            "file_id": file_id,
            "row_number": None,
            "column_name": self.field_name,
            "error_code": self.error_code,
            "message": self.message,
            "error_category": self.error_category,
            "json_path": self.json_path,
            "source_record_id": self.source_record_id,
            "record_locator": self.json_path,
            "field_name": self.field_name,
            "raw_value_redacted": self.raw_value_redacted,
            "retry_status": "open",
        }


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Một bản ghi nguồn đã chuẩn hoá, sẵn sàng cho `SourceIdentityService`."""

    source_record_id: str
    operation: str
    json_path: str
    payload_hash: str
    source_updated_at: datetime | None = None
    source_revision: int | None = None
    data: dict[str, Any] = field(default_factory=dict)
    # `full` (mặc định) hay `partial`. Quyết định ý nghĩa của một trường VẮNG MẶT
    # — xem `src/services/history_guard.py`.
    payload_completeness: str = COMPLETENESS_FULL


@dataclass(slots=True)
class SyncEnvelope:
    """Phong bì đã kiểm tra xong phần bao ngoài."""

    source_system: str
    source_instance_id: str
    source_entity: str
    schema_version: int
    external_batch_id: str
    # v1: LUÔN có, là UUID nội bộ của backend — hành vi KHÔNG đổi.
    # v2: một trong hai — `external_project_id` (hình dạng CHUẨN, dự án do Mini
    # CRM sở hữu nên không biết UUID của phía nhận) hoặc `project_id` (hình dạng
    # tương thích). Lô `source_entity='projects'` được phép có CẢ HAI đều rỗng ở
    # bước này nếu dự án đang được TẠO trong chính lô đó — `SyncRunService` tự
    # quyết định, không phải tầng phong bì.
    project_id: str | None = None
    external_project_id: str | None = None
    sync_mode: str = "incremental"
    source_cursor: str | None = None
    # Metadata ảnh chụp (hợp đồng v1 §10). Chỉ có mặt khi sync_mode='full_snapshot';
    # lô incremental để None — điền giá trị giả vào đây sẽ khiến chốt an toàn xoá
    # tưởng mình đang xử lý một ảnh chụp trong khi không phải.
    snapshot_id: str | None = None
    chunk_index: int | None = None
    chunk_total: int | None = None
    snapshot_complete: bool | None = None
    snapshot_scope: dict[str, Any] | None = None
    records: list[SourceRecord] = field(default_factory=list)
    errors: list[RecordError] = field(default_factory=list)
    records_received: int = 0


def redact(value: Any, key: str | None = None) -> str:
    """Rút gọn một giá trị để đưa vào thông báo lỗi mà không lộ nội dung.

    Khoá nhạy cảm → thay hẳn bằng nhãn. Giá trị khác → cắt ngắn. Không bao giờ
    trả nguyên văn một chuỗi dài: thông báo lỗi đi vào bảng có chính sách lưu trữ
    và quyền truy cập khác với dữ liệu gốc.
    """
    if key is not None and key.strip().lower() in SENSITIVE_KEYS:
        return f"<đã ẩn: {key}>"
    if value is None:
        return "<null>"
    if isinstance(value, bool | int | float):
        return str(value)
    text = str(value)
    return text if len(text) <= 32 else f"{text[:29]}..."


# Các trường THỰC SỰ được chiếu xuống bảng nghiệp vụ, theo từng loại thực thể.
# Phải khớp đúng những gì `DomainProjector._project_unit` / `_project_deal` đọc —
# `tests/test_services/test_mapped_fields.py` giữ hai bên không lệch nhau.
#
# Đây là nền của "băm theo trường đã ánh xạ". Băm cả payload sẽ khiến một trường
# mà ta KHÔNG lưu (ghi chú, giá, người phụ trách…) đổi giá trị là sinh ra đụng độ
# giả: cùng phiên bản, khác dấu vân, trong khi mọi thứ ta quan tâm đều y nguyên.
# Đụng độ giả không vô hại — nó giữ nguyên bản cũ và bắt người vận hành đi xử lý
# một mâu thuẫn không tồn tại, nên chẳng mấy chốc sẽ bị bỏ qua theo thói quen.
MAPPED_FIELDS: dict[str, tuple[str, ...]] = {
    "units": ("area_id", "external_area_id", "area_name", "unit_type", "unit_code", "status"),
    "deals": ("external_unit_id", "status", "reserved_at", "sold_at", "lost_at"),
    # Phase D — v2. `project`/`area` không mang tham chiếu cha trong `data`:
    # `project` là gốc; `area` suy dự án qua `project_id` của phong bì (§A3.5),
    # không qua một trường trong `data` của chính nó.
    "projects": ("name", "launch_date"),
    "areas": ("area_name", "unit_type", "bedrooms", "area_sqm", "total_units"),
}


def mapped_view(entity: str | None, data: Any) -> Any:
    """Phần dữ liệu thực sự được lưu, dùng để băm và để đối soát.

    Thực thể không nhận ra → trả nguyên `data`. Thà băm thừa còn hơn băm thiếu:
    băm thiếu sẽ bỏ sót thay đổi thật và im lặng coi hai bản khác nhau là một.

    **Trường có giá trị `None` bị loại khỏi khung nhìn**, giống hệt trường vắng
    mặt. Dấu vân phải là hàm của TRẠNG THÁI CUỐI CÙNG, không phải của cách viết:
    `{"reserved_at": null}` và bỏ hẳn khoá đó cùng dẫn tới một dòng có
    `reserved_at IS NULL`, nên chúng phải băm ra cùng một giá trị. Không loại thì
    hai cách viết cùng nghĩa sẽ sinh đụng độ giả ở cùng phiên bản — và người vận
    hành đi xử lý một mâu thuẫn không tồn tại.

    Việc PHÂN BIỆT vắng mặt với null tường minh vẫn còn nguyên, nhưng nó thuộc về
    tầng khác: `history_guard` đọc thẳng `data`, không đọc dấu vân. Dấu vân trả
    lời "bằng hay khác", còn "ý định gì" là câu hỏi của tầng chốt.
    """
    fields = MAPPED_FIELDS.get(entity or "")
    if fields is None or not isinstance(data, dict):
        return data
    return {key: data[key] for key in fields if key in data and data[key] is not None}


def payload_fingerprint(operation: str, data: Any, *, entity: str | None = None) -> str:
    """Dấu vân ổn định của một bản ghi nguồn, tính trên các TRƯỜNG ĐÃ ÁNH XẠ.

    Băm trên JSON đã sắp khoá nên thứ tự khoá trong payload không làm đổi kết quả
    — nếu không, CRM đổi thứ tự serialize là mọi bản ghi bỗng thành "khác nội
    dung" và sinh đụng độ giả.

    `entity` quyết định tập trường được băm (xem `MAPPED_FIELDS`). Bỏ trống thì
    băm toàn bộ `data` — hành vi cũ, giữ lại cho các đường gọi chưa biết thực thể.

    `operation` nằm trong dấu vân: một lệnh xoá và một lệnh ghi ở CÙNG phiên bản
    là hai ý định khác nhau, phải phân biệt được thì mới phát hiện ra đụng độ.

    KHÔNG dùng dấu vân này để xếp thứ tự thời gian. Nó chỉ trả lời BẰNG hay KHÁC
    — xem `docs/crm/sync_contract_v1_draft.md` mục 5.1.

    blake2b: chỉ cần chống trùng, không cần thuộc tính mật mã — giống `row_hash`
    của parser Excel.
    """
    subject = mapped_view(entity, data)
    canonical = json.dumps(subject, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.blake2b(f"{operation}\x1e{canonical}".encode(), digest_size=16).hexdigest()


def _require_text(payload: dict, key: str, *, error_code: str | None = None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EnvelopeError(
            error_code or "INVALID_ENVELOPE",
            f"Trường '{key}' bắt buộc và phải là chuỗi khác rỗng",
            json_path=f"$.{key}",
        )
    return value.strip()


def _parse_timestamp(raw: Any, json_path: str) -> datetime:
    """Mốc phiên bản của bản ghi — BẮT BUỘC có múi giờ.

    Giống `_to_timestamp` của parser Excel và vì đúng lý do đó: một mốc trần
    không nói được nó là giờ nào, mà đây lại chính là căn cứ để quyết định bản
    nào mới hơn. Đoán bừa là UTC sẽ âm thầm đảo thứ tự.
    """
    if isinstance(raw, datetime):
        parsed = raw
    else:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("phải là chuỗi ISO-8601 khác rỗng")
        try:
            parsed = datetime.fromisoformat(raw.strip())
        except ValueError as exc:
            raise ValueError(f"'{raw}' không phải mốc ISO-8601 (ví dụ 2026-08-09T00:00:00Z)") from exc

    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(f"'{raw}' thiếu múi giờ — phải ghi kèm offset hoặc hậu tố Z")
    _ = json_path
    return parsed


class JsonPayloadParser:
    """Đọc phong bì JSON, trả `SyncEnvelope` gồm bản ghi hợp lệ và lỗi theo bản ghi."""

    def __init__(self, *, max_record_errors: int = MAX_RECORD_ERRORS) -> None:
        self.max_record_errors = max_record_errors

    def parse(self, payload: Any, *, entity_from_route: str | None = None) -> SyncEnvelope:
        """Kiểm tra phong bì rồi chuẩn hoá từng bản ghi.

        Args:
            payload: thân request đã giải mã JSON.
            entity_from_route: `{entity}` trên đường dẫn. Có mặt thì phải KHỚP
                `source_entity` trong thân — lệch nhau là mơ hồ, không đoán.

        Raises:
            EnvelopeError: phong bì sai ở mức không xử lý tiếp được.
        """
        if not isinstance(payload, dict):
            raise EnvelopeError("INVALID_ENVELOPE", "Thân request phải là một đối tượng JSON")

        source_system = _require_text(payload, "source_system")
        source_instance_id = _require_text(payload, "source_instance_id")
        source_entity = _require_text(payload, "source_entity")
        external_batch_id = _require_text(payload, "external_batch_id")

        # v1: 'project_id' luôn bắt buộc — hành vi KHÔNG đổi (giữ nguyên
        # `_require_text`, ném đúng lỗi cũ khi thiếu).
        # v2: chấp nhận 'project_id' HOẶC 'external_project_id'; thiếu CẢ HAI thì
        # từ chối, TRỪ lô 'projects' (dự án có thể đang được TẠO trong chính lô
        # này — `SyncRunService` là nơi quyết định, không phải ở đây).
        raw_project_id = payload.get("project_id")
        raw_external_project_id = payload.get("external_project_id")
        project_id = raw_project_id.strip() if isinstance(raw_project_id, str) and raw_project_id.strip() else None
        external_project_id = (
            raw_external_project_id.strip()
            if isinstance(raw_external_project_id, str) and raw_external_project_id.strip()
            else None
        )
        if project_id is None and external_project_id is None and source_entity != "projects":
            raise EnvelopeError(
                "MISSING_ENVELOPE_PROJECT_REF",
                "Phong bì phải mang 'project_id' hoặc 'external_project_id'",
                json_path="$.project_id",
            )

        if entity_from_route is not None and entity_from_route != source_entity:
            raise EnvelopeError(
                "ENTITY_MISMATCH",
                f"Đường dẫn khai '{entity_from_route}' nhưng phong bì khai '{source_entity}'",
                json_path="$.source_entity",
            )
        if source_entity not in SUPPORTED_ENTITIES:
            raise EnvelopeError(
                "UNSUPPORTED_ENTITY",
                f"Thực thể '{source_entity}' không được hỗ trợ. Chấp nhận: {', '.join(sorted(SUPPORTED_ENTITIES))}",
                json_path="$.source_entity",
            )

        schema_version = payload.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise EnvelopeError(
                "INVALID_SCHEMA_VERSION",
                "Trường 'schema_version' bắt buộc và phải là số nguyên",
                json_path="$.schema_version",
            )
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise EnvelopeError(
                "UNSUPPORTED_SCHEMA_VERSION",
                f"Phiên bản phong bì {schema_version} không được hỗ trợ. "
                f"Chấp nhận: {', '.join(str(v) for v in sorted(SUPPORTED_SCHEMA_VERSIONS))}",
                json_path="$.schema_version",
            )

        sync_mode = payload.get("sync_mode", "incremental")
        if sync_mode not in SUPPORTED_SYNC_MODES:
            raise EnvelopeError(
                "UNSUPPORTED_SYNC_MODE",
                f"'sync_mode' phải là một trong: {', '.join(sorted(SUPPORTED_SYNC_MODES))}",
                json_path="$.sync_mode",
            )

        source_cursor = payload.get("source_cursor")
        if source_cursor is not None and not isinstance(source_cursor, str):
            raise EnvelopeError("INVALID_ENVELOPE", "'source_cursor' phải là chuỗi", json_path="$.source_cursor")

        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raise EnvelopeError("INVALID_ENVELOPE", "Trường 'records' bắt buộc và phải là mảng", json_path="$.records")

        snapshot = self._parse_snapshot(payload, sync_mode)

        envelope = SyncEnvelope(
            source_system=source_system,
            source_instance_id=source_instance_id,
            source_entity=source_entity,
            schema_version=schema_version,
            external_batch_id=external_batch_id,
            project_id=project_id,
            external_project_id=external_project_id,
            sync_mode=sync_mode,
            source_cursor=source_cursor,
            records_received=len(raw_records),
            **snapshot,
        )

        seen: set[str] = set()
        for index, raw in enumerate(raw_records):
            path = f"$.records[{index}]"
            record, error = self._parse_record(raw, path, entity=source_entity)
            if error is not None:
                self._collect(envelope, error)
                continue
            assert record is not None
            # Trùng danh tính NGAY TRONG một lô: giữ bản ghi đầu. Xử lý cả hai sẽ
            # khiến quyết định thứ hai so với chính kết quả của quyết định thứ
            # nhất, tức là kết quả phụ thuộc thứ tự đọc.
            if record.source_record_id in seen:
                self._collect(
                    envelope,
                    RecordError(
                        json_path=path,
                        error_code="DUPLICATE_SOURCE_RECORD_ID",
                        message="Bản ghi trùng danh tính với một bản ghi trước trong cùng lô",
                        error_category="business",
                        source_record_id=record.source_record_id,
                    ),
                )
                continue
            seen.add(record.source_record_id)
            envelope.records.append(record)

        return envelope

    def _collect(self, envelope: SyncEnvelope, error: RecordError) -> None:
        if len(envelope.errors) < self.max_record_errors:
            envelope.errors.append(error)

    def _parse_snapshot(self, payload: dict, sync_mode: str) -> dict[str, Any]:
        """Đọc khối `snapshot` của hợp đồng v1.

        Hai chiều đều bị chặn, và cả hai đều quan trọng:

        * `full_snapshot` mà THIẾU khối snapshot → từ chối. Không có nó thì không
          trả lời được "đã đủ mảnh chưa" và "phạm vi tới đâu", nên chốt an toàn
          xoá không có gì để dựa vào — mà im lặng bỏ qua sẽ khiến một lô tự nhận
          là ảnh chụp được xử lý như lô tăng dần.
        * `incremental` mà CÓ khối snapshot → từ chối. Hai chế độ suy ra xoá theo
          hai cách khác nhau; nhận cả hai cùng lúc là mơ hồ.

        Hình dạng bên trong đã được `ContractValidator` kiểm trước đó; ở đây chỉ
        lấy giá trị ra, không kiểm lại.
        """
        snapshot = payload.get("snapshot")

        if sync_mode == "full_snapshot":
            if not isinstance(snapshot, dict):
                raise EnvelopeError(
                    "MISSING_SNAPSHOT_METADATA",
                    "sync_mode='full_snapshot' bắt buộc có khối 'snapshot'",
                    json_path="$.snapshot",
                )
            scope = snapshot.get("scope")
            if not isinstance(scope, dict) or not scope.get("entities"):
                raise EnvelopeError(
                    "MISSING_SNAPSHOT_SCOPE",
                    "Ảnh chụp phải khai rõ 'scope.entities' — 'mọi thứ không có trong lô này' "
                    "là tập không có biên nếu không nói rõ biên ở đâu",
                    json_path="$.snapshot.scope",
                )
            return {
                "snapshot_id": snapshot.get("snapshot_id"),
                "chunk_index": snapshot.get("chunk_index"),
                "chunk_total": snapshot.get("chunk_total"),
                "snapshot_complete": snapshot.get("snapshot_complete"),
                "snapshot_scope": scope,
            }

        if snapshot is not None:
            raise EnvelopeError(
                "SNAPSHOT_ON_INCREMENTAL",
                "Lô 'incremental' không được mang khối 'snapshot'",
                json_path="$.snapshot",
            )
        return {}

    def _parse_record(
        self, raw: Any, path: str, *, entity: str | None = None
    ) -> tuple[SourceRecord | None, RecordError | None]:
        if not isinstance(raw, dict):
            return None, RecordError(
                json_path=path,
                error_code="INVALID_RECORD",
                message="Mỗi phần tử của 'records' phải là một đối tượng JSON",
                error_category="schema",
            )

        source_record_id = raw.get("source_record_id")
        if not isinstance(source_record_id, str) or not source_record_id.strip():
            return None, RecordError(
                json_path=f"{path}.source_record_id",
                error_code="MISSING_SOURCE_RECORD_ID",
                message="Bản ghi phải có 'source_record_id' là chuỗi khác rỗng",
                error_category="schema",
                field_name="source_record_id",
            )
        source_record_id = source_record_id.strip()

        operation = raw.get("operation", "upsert")
        if operation not in SUPPORTED_OPERATIONS:
            # KHÔNG mặc định về 'upsert' khi gặp giá trị lạ: đoán sai chiều của
            # một lệnh xoá là làm sống lại bản ghi CRM đã bỏ.
            return None, RecordError(
                json_path=f"{path}.operation",
                error_code="UNSUPPORTED_OPERATION",
                message=f"'operation' phải là một trong: {', '.join(sorted(SUPPORTED_OPERATIONS))}",
                error_category="schema",
                field_name="operation",
                source_record_id=source_record_id,
                raw_value_redacted=redact(operation, "operation"),
            )

        source_updated_at = None
        if raw.get("source_updated_at") is not None:
            try:
                source_updated_at = _parse_timestamp(raw["source_updated_at"], f"{path}.source_updated_at")
            except ValueError as exc:
                return None, RecordError(
                    json_path=f"{path}.source_updated_at",
                    error_code="INVALID_TIMESTAMP",
                    message=f"Trường 'source_updated_at': {exc}",
                    error_category="field",
                    field_name="source_updated_at",
                    source_record_id=source_record_id,
                )

        source_revision = raw.get("source_revision")
        if source_revision is not None:
            if not isinstance(source_revision, int) or isinstance(source_revision, bool) or source_revision < 0:
                return None, RecordError(
                    json_path=f"{path}.source_revision",
                    error_code="INVALID_REVISION",
                    message="'source_revision' phải là số nguyên không âm",
                    error_category="field",
                    field_name="source_revision",
                    source_record_id=source_record_id,
                    raw_value_redacted=redact(source_revision, "source_revision"),
                )

        completeness = raw.get("payload_completeness", COMPLETENESS_FULL)
        if completeness not in SUPPORTED_COMPLETENESS:
            # KHÔNG mặc định về 'full' khi gặp giá trị lạ. Đoán sai chiều ở đây là
            # đoán sai ý nghĩa của MỌI trường vắng mặt trong bản ghi.
            return None, RecordError(
                json_path=f"{path}.payload_completeness",
                error_code="UNSUPPORTED_PAYLOAD_COMPLETENESS",
                message=f"'payload_completeness' phải là một trong: {', '.join(sorted(SUPPORTED_COMPLETENESS))}",
                error_category="schema",
                field_name="payload_completeness",
                source_record_id=source_record_id,
                raw_value_redacted=redact(completeness, "payload_completeness"),
            )

        data = raw.get("data", {})
        if not isinstance(data, dict):
            return None, RecordError(
                json_path=f"{path}.data",
                error_code="INVALID_DATA",
                message="'data' phải là một đối tượng JSON",
                error_category="schema",
                field_name="data",
                source_record_id=source_record_id,
            )

        # MỌI bản ghi phải mang phiên bản, kể cả lệnh xoá. Lệnh xoá không cần
        # `data`, nhưng không có phiên bản thì không phân biệt được "xoá mới" với
        # "lệnh xoá cũ đến muộn" — và áp nhầm lệnh xoá cũ lên một bản ghi vừa
        # được tạo lại là mất dữ liệu im lặng.
        #
        # Phía nhận KHÔNG được suy ra thứ tự từ thứ nó tự tính: `payload_hash` là
        # hàm băm nên không đơn điệu, và giờ máy nhận không nói gì về thứ tự sự
        # kiện ở hệ nguồn. Chỉ hệ nguồn mới nói được cái gì mới hơn; không nói thì
        # từ chối, không đoán.
        if source_updated_at is None and source_revision is None:
            return None, RecordError(
                json_path=path,
                error_code="MISSING_SOURCE_VERSION",
                message="Bản ghi phải có 'source_revision' hoặc 'source_updated_at' để so sánh phiên bản",
                error_category="business",
                source_record_id=source_record_id,
            )

        return (
            SourceRecord(
                source_record_id=source_record_id,
                operation=operation,
                json_path=path,
                payload_hash=payload_fingerprint(operation, data, entity=entity),
                source_updated_at=source_updated_at,
                source_revision=source_revision,
                data=data,
                payload_completeness=completeness,
            ),
            None,
        )
