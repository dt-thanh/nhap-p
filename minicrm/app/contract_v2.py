"""Kiểm phong bì theo hợp đồng v2 (DỰ THẢO) TRƯỚC khi nó được LƯU vào outbox.

**Khác `contract.py` (v1) ở một điểm cốt lõi: module này kiểm trước khi LƯU, không
phải trước khi GỬI.** Phase C không gửi phong bì v2 đi đâu cả — phía nhận vẫn giữ
`SUPPORTED_SCHEMA_VERSIONS = {1}` (`src/services/json_payload.py:28`, chưa đổi) và
không có `POST /sync/projects`/`POST /sync/areas` nào tồn tại để nhận nó (`REQUIRED
IN PHASE D`). Vì thế bộ kiểm ở đây tồn tại để đảm bảo một thứ hẹp hơn nhưng vẫn
quan trọng: không dòng `crm_outbox` nào chứa một phong bì mà chính schema v2 (đã
đóng băng ở Phase A) sẽ từ chối — nếu không, "ý định đã ký" mà Phase C hứa hẹn cho
Phase D chỉ là rác câm.

Cùng lý do song hành bản sao schema như `contract.py`: xem docstring module đó.
`tests/test_contract_copy.py` mở rộng sang so khớp SHA-256 của bản sao v2 giữa hai
cây triển khai.
"""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "contracts"
SCHEMA_PATH = CONTRACT_ROOT / "crm_sync_v2.schema.json"

SCHEMA_VERSION = 2

# Thứ tự tầng đã đóng băng — `phase_a_domain_freeze.md` §A5.2.
HIERARCHY_TIER_UPSERT = ("project", "area", "unit", "deal")
HIERARCHY_TIER_DELETE = tuple(reversed(HIERARCHY_TIER_UPSERT))


class ContractV2ViolationError(Exception):
    """Phong bì v2 không đúng hợp đồng. KHÔNG lưu vào outbox.

    Mang cả danh sách vi phạm, cùng quy ước với `contract.ContractViolationError`.
    """

    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"Phong bì vi phạm hợp đồng v2 ({len(violations)} lỗi): {'; '.join(violations[:5])}")
        self.violations = violations


class ContractSchemaUnavailableError(Exception):
    """Không nạp được bản sao schema v2. Lỗi CẤU HÌNH của Mini CRM, không phải lỗi dữ liệu."""


@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractSchemaUnavailableError(f"Không tìm thấy bản sao schema v2 tại {SCHEMA_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise ContractSchemaUnavailableError(f"Bản sao schema v2 không phải JSON hợp lệ: {exc}") from exc


def schema_sha256() -> str:
    """Băm BYTE THÔ — cùng lý do với `contract.schema_sha256`."""
    return hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


def _schema_violations(envelope: Any) -> list[str]:
    from jsonschema import Draft202012Validator, FormatChecker

    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(envelope), key=lambda e: (list(e.absolute_path), e.message))
    return [f"$.{'.'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors]


def _order_violations(records: list[Any]) -> list[str]:
    """§A5.2: thứ tự `records[]` LÀ MỘT PHẦN CỦA HỢP ĐỒNG.

    Phía nhận (khi Phase D bật v2) sẽ xử lý tuần tự và KHÔNG sắp lại hộ — nên Mini
    CRM (producer) phải tự đảm bảo thứ tự TRƯỚC khi phong bì rời khỏi máy, và bộ
    kiểm này là nơi khẳng định đã làm đúng trước khi ghi xuống `crm_outbox`.

    Chỉ kiểm những bản ghi hợp lệ về `entity`/`operation` — bản ghi sai hai trường
    đó đã bị `_schema_violations` bắt riêng.
    """
    problems: list[str] = []
    last_rank: int | None = None
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        entity = record.get("entity")
        operation = record.get("operation")
        order = HIERARCHY_TIER_UPSERT if operation == "upsert" else HIERARCHY_TIER_DELETE
        if entity not in order:
            continue
        rank = order.index(entity)
        if last_rank is not None and rank < last_rank:
            problems.append(
                f"$.records[{index}]: entity '{entity}' đứng SAI thứ tự — "
                f"{operation} phải theo {' → '.join(order)}, không được sắp lại"
            )
        last_rank = rank
    return problems


def _project_ref_violations(envelope: dict[str, Any]) -> list[str]:
    """§A3.5: bản ghi `entity='project'` PHẢI khớp `project_ref.external_project_id`.

    JSON Schema không diễn đạt được quan hệ giữa hai nhánh khác nhau của tài liệu
    (`project_ref` ở gốc, `external_id` trong `records[]`) — luật tầng nghiệp vụ,
    canh bởi fixture `29_v2_project_record_mismatches_envelope.json`.
    """
    project_ref = envelope.get("project_ref")
    if not isinstance(project_ref, dict):
        return []
    expected = project_ref.get("external_project_id")
    if expected is None:
        return []
    problems: list[str] = []
    for index, record in enumerate(envelope.get("records") or []):
        if not isinstance(record, dict) or record.get("entity") != "project":
            continue
        if record.get("external_id") != expected:
            problems.append(
                f"$.records[{index}].external_id: '{record.get('external_id')}' khác "
                f"project_ref.external_project_id ('{expected}') — PROJECT_REF_MISMATCH"
            )
    return problems


def _business_violations(envelope: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    records = envelope.get("records")
    if isinstance(records, list):
        if not records:
            problems.append("$.records: lô rỗng — không có gì để lưu")
        problems.extend(_order_violations(records))
    problems.extend(_project_ref_violations(envelope))
    return problems


def validate(envelope: Any) -> list[str]:
    """Trả danh sách vi phạm. Rỗng = phong bì lưu được vào outbox."""
    problems = _schema_violations(envelope)
    if isinstance(envelope, dict):
        problems.extend(_business_violations(envelope))
    return problems


def assert_valid(envelope: Any) -> None:
    """Ném `ContractV2ViolationError` nếu phong bì không đúng hợp đồng v2.

    Gọi TRONG transaction của thao tác CRUD, cùng quy ước với `contract.assert_valid`
    — phong bì hỏng thì rollback luôn thay đổi cục bộ.
    """
    problems = validate(envelope)
    if problems:
        raise ContractV2ViolationError(problems)
