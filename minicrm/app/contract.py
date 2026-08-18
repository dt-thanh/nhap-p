"""Kiểm phong bì theo hợp đồng v1 TRƯỚC khi nó rời khỏi máy.

**Vì sao Mini CRM giữ một BẢN SAO của schema thay vì import bản của backend.**

`src/services/contract_validation.py` nói rất đúng rằng hai bản sao sẽ lệch nhau
và lúc đó không ai biết bản nào là hợp đồng thật. Nhưng cách chữa của nó — "chỉ
có một bản, ai cần thì import" — chỉ áp dụng được cho những thứ chạy trong CÙNG
một cây triển khai. Mini CRM không phải như vậy: nó có image riêng, build context
riêng (`context: ./minicrm`), và `src/` không tồn tại trong image đó. Import bản
của backend nghĩa là ghép hai cây triển khai lại làm một — đúng thứ mà toàn bộ
Phase 3 dựng lên để tránh.

Và có một lý do nặng hơn: nếu Mini CRM kiểm bằng chính đối tượng schema mà backend
kiểm, thì phép kiểm hợp đồng ở phía nhận không còn nói lên điều gì. Mini CRM sẽ
luôn tự cho mình là đúng vì nó đang hỏi đúng cái người sẽ chấm điểm nó. Một hệ
nguồn THẬT cũng chỉ có một bản sao của schema — chép qua email, qua repo khác,
qua một trang tài liệu — nên bản sao ở đây mô phỏng đúng tình huống thật.

**Cách chữa cho rủi ro lệch bản: SO KHỚP BẰNG SHA-256, ở tầng test.**
`tests/test_contract_copy.py` băm cả hai file và HỎNG nếu chúng khác nhau. Bản sao
vì thế không thể lặng lẽ trôi khỏi bản gốc — nó chỉ trôi được cùng với một test đỏ.

**Bộ kiểm ở đây NGHIÊM hơn schema, và điều đó được phép.**

JSON Schema chỉ nói về HÌNH DẠNG. Nó không biết `unit_status` phải thuộc bốn giá
trị nào (hợp đồng cố ý để mở: `unit_status` là "giá trị nguyên văn của hệ nguồn",
và phía nhận ánh xạ qua bảng tường minh). Nhưng Mini CRM BIẾT bảng đó — nó soi
gương `UNIT_STATUSES`/`DEAL_STATUSES` của backend từ migration 0001 — nên nó chặn
được ở nguồn thay vì để backend từ chối sau một vòng HTTP.

Ranh giới phải nói rõ: **backend vẫn là căn cứ tương thích CUỐI CÙNG.** Bộ kiểm ở
đây chỉ có quyền TỪ CHỐI SỚM. Nó không bao giờ được dùng làm bằng chứng rằng một
payload sẽ được backend chấp nhận — chỉ phản hồi HTTP thật mới nói được điều đó.
"""

from __future__ import annotations

import functools
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "contracts"
SCHEMA_PATH = CONTRACT_ROOT / "crm_sync_v1.schema.json"

SCHEMA_VERSION = 1
MAX_RECORDS_PER_BATCH = 5000

# Soi gương `UNIT_STATUSES` của backend (`src/services/domain_projection.py`).
# Backend KHÔNG có bảng alias cho căn — nó hạ chữ thường rồi đòi thuộc tập này.
UNIT_STATUSES = frozenset({"available", "reserved", "sold", "blocked"})

# Soi gương `DEAL_STATUSES`. `cancelled` KHÔNG có ở đây: backend ánh xạ nó về
# `lost`, nhưng Mini CRM là hệ nguồn nên nó phát thẳng từ vựng chuẩn thay vì dựa
# vào bảng alias của phía nhận.
DEAL_STATUSES = frozenset({"lead", "qualified", "interested", "viewing", "reserved", "sold", "lost"})

# Trạng thái ⇒ mốc thời gian BẮT BUỘC. Soi gương `_check_status_dates` của backend
# và `ck_crm_deals_*_requires_*` của migration 0001.
STATUS_REQUIRES_TIMESTAMP = {"reserved": "reserved_at", "sold": "sold_at", "lost": "lost_at"}

# Thực thể trên URL ⇒ giá trị `entity` trong từng bản ghi. Backend đòi số NHIỀU
# trên đường dẫn và số ÍT trong bản ghi, và nó TỪ CHỐI khi hai bên lệch nhau
# (`ENTITY_MISMATCH`, HTTP 409).
ROUTE_ENTITY = {"units": "unit", "deals": "deal"}


class ContractViolationError(Exception):
    """Phong bì không đúng hợp đồng. KHÔNG gửi đi.

    Mang cả danh sách vi phạm chứ không chỉ cái đầu tiên: người sửa payload cần
    thấy hết trong một lượt, giống hệt cách backend trả về.
    """

    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"Phong bì vi phạm hợp đồng v1 ({len(violations)} lỗi): {'; '.join(violations[:5])}")
        self.violations = violations


class ContractSchemaUnavailableError(Exception):
    """Không nạp được bản sao schema. Lỗi CẤU HÌNH của Mini CRM, không phải lỗi dữ liệu."""


@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractSchemaUnavailableError(f"Không tìm thấy bản sao schema tại {SCHEMA_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise ContractSchemaUnavailableError(f"Bản sao schema không phải JSON hợp lệ: {exc}") from exc


def schema_sha256() -> str:
    """Băm BYTE THÔ của file, không băm cây JSON đã parse.

    Băm cây đã parse sẽ coi hai file khác nhau về khoảng trắng, thứ tự khoá hay
    ký tự thoát là giống nhau — mà chúng là hai file khác nhau, và mục đích của
    phép băm này là phát hiện đúng chuyện đó.
    """
    return hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


def _schema_violations(envelope: Any) -> list[str]:
    from jsonschema import Draft202012Validator, FormatChecker

    # `FormatChecker` được BẬT ở đây nhưng KHÔNG bật ở backend. Chênh lệch này là
    # có chủ đích và chỉ theo một chiều an toàn: Mini CRM nghiêm hơn phía nhận,
    # nên nó không bao giờ chặn nhầm một payload mà backend sẽ chấp nhận... vì
    # nó chặn ÍT hơn thì mới nguy hiểm, chứ chặn nhiều hơn chỉ khiến một payload
    # đáng ngờ không rời khỏi máy. Backend vẫn là căn cứ cuối cùng.
    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(envelope), key=lambda e: (list(e.absolute_path), e.message))
    return [f"$.{'.'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors]


def _business_violations(envelope: dict[str, Any], *, entity: str) -> list[str]:
    """Những luật mà JSON Schema KHÔNG diễn đạt được.

    Ba nhóm: nhất quán giữa đường dẫn và bản ghi, từ vựng trạng thái, và quy tắc
    trạng thái ↔ mốc lịch sử.
    """
    problems: list[str] = []

    expected_entity = ROUTE_ENTITY.get(entity)
    if expected_entity is None:
        return [f"entity '{entity}' không phải một đường đồng bộ hợp lệ"]

    # `project_id` phải là UUID THẬT. Schema khai `format: uuid`, nhưng format chỉ
    # được cưỡng chế khi có FormatChecker và đúng thư viện đi kèm — kiểm tường
    # minh ở đây thì không phụ thuộc vào cấu hình của thư viện nữa.
    project_id = (envelope.get("project_ref") or {}).get("project_id")
    try:
        uuid.UUID(str(project_id))
    except (AttributeError, TypeError, ValueError):
        problems.append(f"$.project_ref.project_id: '{project_id}' không phải UUID hợp lệ")

    records = envelope.get("records") or []
    if not records:
        # Hợp đồng cho phép lô rỗng (`minItems: 0`); Mini CRM thì không GỬI lô
        # rỗng. Một lô rỗng vẫn tiêu một `external_batch_id` và tạo một sync_run
        # trống ở phía nhận — nhiễu thuần tuý.
        problems.append("$.records: lô rỗng — không có gì để gửi")
    if len(records) > MAX_RECORDS_PER_BATCH:
        problems.append(f"$.records: {len(records)} bản ghi, vượt trần {MAX_RECORDS_PER_BATCH}")

    seen: set[str] = set()
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        if not isinstance(record, dict):
            problems.append(f"{path}: bản ghi phải là object")
            continue

        if record.get("entity") != expected_entity:
            problems.append(
                f"{path}.entity: '{record.get('entity')}' không khớp đường dẫn /sync/{entity} "
                f"(backend trả 409 ENTITY_MISMATCH)"
            )

        external_id = record.get("external_id")
        if external_id in seen:
            problems.append(f"{path}.external_id: '{external_id}' xuất hiện hai lần trong cùng một lô")
        seen.add(external_id)

        # Mọi bản ghi PHẢI mang phiên bản, kể cả lệnh xoá. Mini CRM luôn dùng
        # `source_revision` (bộ đếm, không phụ thuộc đồng hồ) — xem giả định A2.
        revision = record.get("source_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            problems.append(f"{path}.source_revision: phải là số nguyên ≥ 1, nhận '{revision}'")

        operation = record.get("operation")
        payload = record.get("payload")
        if operation == "delete":
            if payload is not None:
                problems.append(f"{path}.payload: lệnh xoá KHÔNG mang thân payload")
            continue
        if not isinstance(payload, dict):
            problems.append(f"{path}.payload: lệnh upsert bắt buộc có payload")
            continue

        problems.extend(
            _unit_payload_violations(payload, path)
            if expected_entity == "unit"
            else _deal_payload_violations(payload, path)
        )

    return problems


def _unit_payload_violations(payload: dict[str, Any], path: str) -> list[str]:
    status = payload.get("unit_status")
    if status not in UNIT_STATUSES:
        return [
            f"{path}.payload.unit_status: '{status}' không nằm trong tập backend chấp nhận "
            f"({', '.join(sorted(UNIT_STATUSES))})"
        ]
    return []


def _deal_payload_violations(payload: dict[str, Any], path: str) -> list[str]:
    problems: list[str] = []
    status = payload.get("deal_status")
    if status not in DEAL_STATUSES:
        problems.append(
            f"{path}.payload.deal_status: '{status}' không nằm trong tập backend chấp nhận "
            f"({', '.join(sorted(DEAL_STATUSES))})"
        )
        return problems

    required = STATUS_REQUIRES_TIMESTAMP.get(status)
    if required and payload.get(required) is None:
        problems.append(f"{path}.payload.{required}: trạng thái '{status}' bắt buộc phải có mốc này")

    reserved_at = payload.get("reserved_at")
    sold_at = payload.get("sold_at")
    if reserved_at and sold_at and str(sold_at) < str(reserved_at):
        # So sánh chuỗi là đủ và ĐÚNG ở đây: cả hai đã qua `$defs/timestamp` nên
        # cùng dạng ISO-8601 có offset. Không parse thành datetime vì việc đó
        # thêm một chỗ nữa để hiểu sai múi giờ, mà kết luận thì không đổi.
        problems.append(f"{path}.payload: sold_at ({sold_at}) sớm hơn reserved_at ({reserved_at})")
    return problems


def validate(envelope: Any, *, entity: str) -> list[str]:
    """Trả danh sách vi phạm. Rỗng = phong bì gửi đi được."""
    problems = _schema_violations(envelope)
    if isinstance(envelope, dict):
        problems.extend(_business_violations(envelope, entity=entity))
    return problems


def assert_valid(envelope: Any, *, entity: str) -> None:
    """Ném `ContractViolationError` nếu phong bì không đúng hợp đồng.

    Gọi hàm này BÊN TRONG transaction của thao tác CRUD. Một phong bì hỏng khi đó
    làm rollback luôn cả thay đổi cục bộ, nên không bao giờ tồn tại một bản ghi đã
    commit mà Mini CRM không diễn đạt nổi thành payload hợp lệ.
    """
    problems = validate(envelope, entity=entity)
    if problems:
        raise ContractViolationError(problems)
