"""Bộ kiểm phù hợp: một payload có đi qua được đường đồng bộ không — mà KHÔNG ghi gì.

## Nó trả lời câu hỏi gì

"Payload mà Mini CRM (tương lai) sinh ra có thoả hợp đồng v1 không, và nếu đưa
vào thì chuyện gì xảy ra?" — trả lời được **trước khi** cấp khoá API cho ai, và
trước khi một dòng nào được ghi.

## Nó KHÔNG trả lời câu hỏi gì

Chạy sạch trên fixture tổng hợp **không chứng minh** tương thích với CRM nào cả.
Fixture do chính ta viết, theo đúng cách ta hiểu hợp đồng; chúng chứng minh BỘ
KIỂM chạy đúng, không chứng minh hệ nguồn nào đó gửi đúng. Chỉ payload THẬT mới
trả lời được, và Mini CRM chưa tồn tại.

## Bốn cổng, đúng thứ tự của endpoint thật

    1. kích thước  → `sync_payloads.measure`
    2. hợp đồng    → `ContractValidator` (JSON Schema 2020-12)
    3. phong bì    → `contract_adapter.adapt` + `JsonPayloadParser`
    4. bản ghi     → `sync_runs.apply_records` (danh tính, phiên bản, chốt A4,
                     tra dự án/phân khu/căn, ràng buộc DB)

Cổng 4 chạy **đúng hàm** mà luồng đồng bộ thật chạy — không phải bản sao. Một bộ
kiểm đi đường riêng sẽ trôi khỏi đường thật đúng vào lúc nó cần chính xác nhất:
lần đầu có payload CRM thật.

**Xác thực (cổng 1 của endpoint) không áp dụng ở đây** và điều đó được ghi rõ
trong báo cáo, chứ không im lặng bỏ qua: kiểm một tệp trên đĩa không có người
gọi nào để xác thực.

## Vì sao không có cờ `dry_run` trên endpoint

Một cờ boolean trên đường ghi thật chỉ cách một lần đặt sai giá trị là biến toàn
bộ luồng nạp thành no-op im lặng. Ở đây, thứ phân biệt "kiểm" với "ghi" không
phải một cờ mà là **quyền sở hữu transaction**: bộ kiểm mở transaction và LUÔN
rollback nó trong `finally`.

Và vì "luôn rollback" là một lời hứa dễ vỡ, nó được kiểm chứng bằng số: số dòng
của mọi bảng nghiệp vụ và bảng đồng bộ được đếm trước và sau, ở một session
KHÁC. Lệch một dòng là báo cáo hỏng ngay, chứ không phải phát hiện sau.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from src.config import get_settings
from src.db import get_session_factory
from src.logging_config import get_logger
from src.models.tables import projects, upload_files
from src.services.contract_adapter import adapt, is_contract_v1
from src.services.contract_validation import ContractSchemaUnavailableError, ContractValidator
from src.services.json_payload import EnvelopeError, JsonPayloadParser
from src.services.sync_payloads import PayloadTooLargeError, measure
from src.services.sync_runs import RecordOutcomes, apply_records

log = get_logger("src.services.conformance")

GATE_SIZE = "size"
GATE_CONTRACT = "contract"
GATE_ENVELOPE = "envelope"
GATE_RECORDS = "records"
GATE_ORDER = (GATE_SIZE, GATE_CONTRACT, GATE_ENVELOPE, GATE_RECORDS)

# Bảng phải KHÔNG đổi số dòng sau một lần kiểm. Gồm cả bảng nghiệp vụ lẫn bảng
# đồng bộ: một bộ kiểm để lại `crm_source_records` cũng tệ như một bộ kiểm để lại
# `deals` — lần gửi thật sau đó sẽ thành `duplicate_noop` và không ghi gì.
GUARDED_TABLES = (
    "units",
    "deals",
    "crm_source_records",
    "upload_files",
    "upload_errors",
    "sync_payloads",
    "absorption_daily",
    "areas",
    "projects",
)


class ProductionRefusalError(Exception):
    """Từ chối chạy vì đích không phải môi trường phi sản xuất."""


@dataclass(frozen=True, slots=True)
class Violation:
    """Một vi phạm, cùng hình dạng với lỗi mà endpoint thật trả về."""

    gate: str
    error_category: str
    error_code: str
    message: str
    json_path: str | None = None
    field_name: str | None = None
    source_record_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "error_category": self.error_category,
            "error_code": self.error_code,
            "message": self.message,
            "json_path": self.json_path,
            "field_name": self.field_name,
            "source_record_id": self.source_record_id,
        }


@dataclass(slots=True)
class ConformanceReport:
    source: str
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    gates_run: list[str] = field(default_factory=list)
    gates_passed: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    # Chuyện gì SẼ xảy ra nếu payload này được nạp thật. Đọc từ transaction đã
    # rollback, nên đây là dự đoán có căn cứ, không phải phỏng đoán.
    decisions: dict[str, int] = field(default_factory=dict)
    projections: dict[str, int] = field(default_factory=dict)
    records_received: int = 0
    size_bytes: int = 0
    dialect: str | None = None
    project_id: str | None = None
    database_untouched: bool | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def conforms(self) -> bool:
        """Đạt = không vi phạm nào VÀ database không suy suyển.

        `database_untouched is None` (chưa chạy tới cổng 4) không được tính là
        đạt phần đó — nhưng cũng không được tính là hỏng, nên nó không vào đây.
        """
        return not self.violations and self.database_untouched is not False

    def add(self, violation: Violation) -> None:
        self.violations.append(violation)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "checked_at": self.checked_at,
            "conforms": self.conforms,
            "dialect": self.dialect,
            "project_id": self.project_id,
            "size_bytes": self.size_bytes,
            "records_received": self.records_received,
            "gates_run": self.gates_run,
            "gates_passed": self.gates_passed,
            "violations": [v.as_dict() for v in self.violations],
            "would_apply": {"decisions": self.decisions, "projections": self.projections},
            "database_untouched": self.database_untouched,
            "notes": self.notes,
            # Đi kèm MỌI báo cáo, kể cả báo cáo đạt. Một dòng "conforms: true" bị
            # tách khỏi ngữ cảnh sẽ được đọc thành "đã tương thích với CRM".
            "disclaimer": (
                "Kết quả này nói payload ĐÃ CHO có thoả hợp đồng v1 hay không. "
                "Nó KHÔNG chứng minh khả năng tương thích với bất kỳ Mini CRM nào; "
                "Mini CRM chưa tồn tại và không dữ liệu thật nào từng đi qua hệ thống này."
            ),
        }


def assert_not_production() -> None:
    """Chặn ngay từ đầu nếu đích là môi trường sản xuất.

    Không có cờ ghi đè. Một cờ ghi đè sẽ được dùng đúng một lần "cho nhanh", và
    lần đó là lần duy nhất cần đến việc từ chối.
    """
    settings = get_settings()
    if settings.app_env == "production":
        raise ProductionRefusalError(
            "APP_ENV=production — bộ kiểm phù hợp không chạy trên môi trường sản xuất. "
            "Nó mở transaction ghi rồi rollback; đúng về lý thuyết, nhưng không phải thứ "
            "để thử trên dữ liệu thật."
        )


async def _table_counts() -> dict[str, int]:
    """Đếm số dòng ở một session RIÊNG, ngoài transaction của lần kiểm."""
    counts: dict[str, int] = {}
    async with get_session_factory()() as session:
        for table in GUARDED_TABLES:
            counts[table] = int(await session.scalar(sa.text(f"SELECT count(*) FROM {table}")) or 0)
    return counts


async def check_payload(raw_body: bytes, *, source: str = "<stdin>") -> ConformanceReport:
    """Chạy bốn cổng trên một payload thô. KHÔNG ghi gì.

    Args:
        raw_body: byte NGUYÊN VĂN của tệp. Đo kích thước trên byte thô chứ không
            trên dạng đã parse — giống hệt endpoint, vì đó mới là thứ đi qua dây.
        source: tên hiển thị trong báo cáo.
    """
    assert_not_production()
    report = ConformanceReport(source=source)
    report.notes.append(
        "Cổng xác thực khoá API KHÔNG áp dụng khi kiểm tệp trên đĩa — không có người gọi nào để xác thực."
    )

    # --- Giải mã JSON ---------------------------------------------------------
    try:
        payload = json.loads(raw_body) if raw_body else None
    except ValueError as exc:
        report.add(
            Violation(
                gate=GATE_SIZE,
                error_category="schema",
                error_code="MALFORMED_JSON",
                message=f"Tệp không phải JSON hợp lệ: {exc}",
                json_path="$",
            )
        )
        return report
    if not isinstance(payload, dict):
        report.add(
            Violation(
                gate=GATE_SIZE,
                error_category="schema",
                error_code="INVALID_ENVELOPE",
                message="Thân payload phải là một đối tượng JSON",
                json_path="$",
            )
        )
        return report

    # --- Cổng 1: kích thước ---------------------------------------------------
    report.gates_run.append(GATE_SIZE)
    try:
        raw = measure(payload, raw_body=raw_body, content_type="application/json")
        report.size_bytes = raw.size_bytes
        report.gates_passed.append(GATE_SIZE)
    except PayloadTooLargeError as exc:
        report.size_bytes = exc.size_bytes
        report.add(
            Violation(
                gate=GATE_SIZE,
                error_category="schema",
                error_code=exc.error_code,
                message=f"{exc.message} (giới hạn {exc.limit_bytes} byte)",
                json_path="$",
            )
        )
        # Payload quá lớn thì endpoint thật dừng ở đây — bộ kiểm cũng vậy, nếu
        # không nó sẽ báo "chỉ sai kích thước" cho một payload chưa ai soi tiếp.
        return report

    # --- Cổng 2: hợp đồng -----------------------------------------------------
    report.gates_run.append(GATE_CONTRACT)
    report.dialect = "v1" if is_contract_v1(payload) else "s2-legacy"
    if report.dialect == "v1":
        try:
            violations = ContractValidator().validate(payload)
        except ContractSchemaUnavailableError as exc:
            report.add(
                Violation(
                    gate=GATE_CONTRACT,
                    error_category="schema",
                    error_code="CONTRACT_SCHEMA_UNAVAILABLE",
                    message=f"Không nạp được schema hợp đồng: {exc}",
                )
            )
            return report

        for violation in violations:
            report.add(
                Violation(
                    gate=GATE_CONTRACT,
                    error_category=violation.error_category,
                    error_code=violation.error_code,
                    message=violation.message,
                    json_path=violation.json_path,
                    field_name=violation.field_name,
                )
            )
        if violations:
            return report
        envelope_input = adapt(payload)
    else:
        report.notes.append(
            "Payload dùng phương ngữ S2 CŨ (đã deprecated) — nó bỏ qua cổng hợp đồng. "
            "Mini CRM tương lai phải dùng hợp đồng v1."
        )
        envelope_input = payload
    report.gates_passed.append(GATE_CONTRACT)

    # --- Cổng 3: phong bì -----------------------------------------------------
    report.gates_run.append(GATE_ENVELOPE)
    try:
        envelope = JsonPayloadParser().parse(envelope_input)
    except EnvelopeError as exc:
        report.add(
            Violation(
                gate=GATE_ENVELOPE,
                error_category="schema",
                error_code=exc.error_code,
                message=exc.message,
                json_path=exc.json_path,
            )
        )
        return report

    report.records_received = envelope.records_received
    report.project_id = envelope.project_id
    for record_error in envelope.errors:
        report.add(
            Violation(
                gate=GATE_ENVELOPE,
                error_category=record_error.error_category,
                error_code=record_error.error_code,
                message=record_error.message,
                json_path=record_error.json_path,
                field_name=record_error.field_name,
                source_record_id=record_error.source_record_id,
            )
        )
    if not envelope.errors:
        report.gates_passed.append(GATE_ENVELOPE)

    # --- Cổng 4: bản ghi (danh tính, phiên bản, tra dự án/phân khu) -----------
    report.gates_run.append(GATE_RECORDS)
    # Vì mọi thứ đều rollback, KHÔNG có trạng thái nào tích luỹ giữa các lần kiểm:
    # mỗi payload được soi trên database ĐANG CÓ, không phải trên kết quả của
    # payload trước. Một chuỗi phụ thuộc lẫn nhau (căn ở lô 1, giao dịch ở lô 2)
    # sẽ báo `UNKNOWN_UNIT_REFERENCE` ở lô 2 — đó là kết quả ĐÚNG, không phải lỗi
    # công cụ. Muốn kiểm cả chuỗi thì phải nạp thật ở môi trường phi sản xuất.
    report.notes.append(
        "Cổng 4 soi payload trên trạng thái database HIỆN TẠI. Không có gì tích luỹ giữa các lần kiểm "
        "(mọi thứ đều rollback), nên một lô phụ thuộc vào lô trước sẽ báo thiếu tham chiếu — đó là kết quả đúng."
    )
    try:
        project_uuid = uuid.UUID(envelope.project_id)
    except ValueError:
        report.add(
            Violation(
                gate=GATE_RECORDS,
                error_category="business",
                error_code="INVALID_PROJECT_ID",
                message="'project_id' phải là UUID hợp lệ",
                json_path="$.project_id",
            )
        )
        return report

    before = await _table_counts()
    outcomes = await _apply_and_rollback(envelope, project_uuid, report)
    after = await _table_counts()

    drift = {name: (before[name], after[name]) for name in GUARDED_TABLES if before[name] != after[name]}
    report.database_untouched = not drift
    if drift:
        # Đây là lỗi của BỘ KIỂM, không phải của payload — nhưng nó phải hiện ra ở
        # cùng chỗ, vì hậu quả (dữ liệu lạ nằm lại trong database) nghiêm trọng hơn
        # mọi vi phạm hợp đồng mà báo cáo này có thể tìm ra.
        report.add(
            Violation(
                gate=GATE_RECORDS,
                error_category="internal",
                error_code="CONFORMANCE_LEFT_WRITES",
                message=f"Lần kiểm đã làm đổi số dòng: {drift}. Đây là LỖI của bộ kiểm, phải sửa trước khi tin nó.",
            )
        )
        log.error("conformance.left_writes", drift=drift)

    if outcomes is not None:
        report.decisions = outcomes.decisions
        report.projections = outcomes.projections
        for record_error in outcomes.errors:
            report.add(
                Violation(
                    gate=GATE_RECORDS,
                    error_category=record_error.error_category,
                    error_code=record_error.error_code,
                    message=record_error.message,
                    json_path=record_error.json_path,
                    field_name=record_error.field_name,
                    source_record_id=record_error.source_record_id,
                )
            )
        if not outcomes.errors and not drift:
            report.gates_passed.append(GATE_RECORDS)

    return report


async def _apply_and_rollback(envelope, project_uuid: uuid.UUID, report: ConformanceReport) -> RecordOutcomes | None:
    """Chạy các bản ghi trong một transaction rồi LUÔN rollback nó.

    `finally` chứ không phải nhánh thành công: một exception ở giữa cũng phải để
    lại database y nguyên. Đây là toàn bộ cơ chế "không ghi gì" của bộ kiểm — nên
    nó nằm gọn trong một hàm, không rải ra nhiều chỗ.
    """
    settings = get_settings()
    async with get_session_factory()() as session:
        transaction = await session.begin()
        try:
            exists = await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_uuid))
            if exists is None:
                report.add(
                    Violation(
                        gate=GATE_RECORDS,
                        error_category="business",
                        error_code="UNKNOWN_PROJECT",
                        message=(
                            f"Dự án '{project_uuid}' không tồn tại. Danh tính dự án là hạng mục CHẶN "
                            f"trước kích hoạt — xem hợp đồng mục 3.1."
                        ),
                        json_path="$.project_id",
                    )
                )
                return None

            # Lô đã xử lý rồi thì endpoint THẬT trả kết quả cũ và không làm gì
            # thêm (idempotency mức lô). Đó không phải vi phạm, nhưng người đọc
            # báo cáo cần biết — nếu không, họ sẽ chờ đợi những thay đổi mà lần
            # gửi thật sẽ không thực hiện.
            replayed = await session.scalar(
                sa.select(upload_files.c.id).where(
                    upload_files.c.source_system == envelope.source_system,
                    upload_files.c.source_instance_id == envelope.source_instance_id,
                    upload_files.c.external_batch_id == envelope.external_batch_id,
                )
            )
            if replayed is not None:
                report.notes.append(
                    f"external_batch_id '{envelope.external_batch_id}' ĐÃ được xử lý (lô {replayed}). "
                    f"Gửi thật sẽ trả lại kết quả cũ và KHÔNG xử lý lại. Phần dưới đây mô tả chuyện gì "
                    f"sẽ xảy ra nếu đây là một lô mới."
                )

            # `crm_source_records.first_sync_run_id` có khoá ngoại tới
            # `upload_files`, nên phải có một dòng lô thì tầng danh tính mới chạy
            # được. Dòng này sống đúng trong transaction sắp bị rollback — và việc
            # nó CẦN tồn tại chính là bằng chứng bộ kiểm đang đi qua ràng buộc
            # thật, không phải một bản mô phỏng dễ dãi.
            sync_run_id = uuid.uuid4()
            now = datetime.now(UTC)
            await session.execute(
                sa.insert(upload_files).values(
                    id=sync_run_id,
                    project_id=project_uuid,
                    status="processing",
                    rows_ok=0,
                    rows_failed=0,
                    uploaded_at=now,
                    source_system=envelope.source_system,
                    source_instance_id=envelope.source_instance_id,
                    source_entity=envelope.source_entity,
                    input_format="json",
                    transport_mode="api_push",
                    sync_mode=envelope.sync_mode,
                    schema_version=envelope.schema_version,
                    external_batch_id=f"conformance-{sync_run_id}",
                    rows_received=envelope.records_received,
                    error_summary={},
                )
            )

            return await apply_records(
                session,
                envelope,
                sync_run_id=sync_run_id,
                project_id=project_uuid,
                now=now,
                preserve_dropped=settings.sync_preserve_dropped_timestamps,
            )
        finally:
            await transaction.rollback()
