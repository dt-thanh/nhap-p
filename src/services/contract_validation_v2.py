"""Kiểm payload đồng bộ v2 theo JSON Schema, TRƯỚC khi chạm nghiệp vụ.

Song song với `contract_validation.py` (v1), cùng lý do tách tầng: sai hình dạng
là lỗi người TÍCH HỢP, sai nghiệp vụ là lỗi DỮ LIỆU. Xem docstring module đó.

**Vì sao một file RIÊNG thay vì tham số hoá `ContractValidator` theo phiên bản.**
v1 là BẤT BIẾN (`docs/crm/sync_contract_v1_draft.md` — "FROZEN, byte-identical").
Tham số hoá sẽ buộc `ContractValidator` (v1) phải chạm vào để nhận thêm một nhánh
rẽ mà nó không cần — rủi ro không cần thiết cho một hợp đồng đã đóng băng. Một
file riêng nghĩa là sửa v2 không bao giờ đi ngang qua đường của v1.

Schema: `src/contracts/crm_sync_v2.schema.json`, bản sao byte-identical ở phía hệ
nguồn tham chiếu (đường dẫn tương ứng bên đó) — cùng quy tắc băm với v1, xem
`docs/crm/sync_contract_v2_draft.md` §7.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.logging_config import get_logger

log = get_logger("src.services.contract_validation_v2")

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "crm_sync_v2.schema.json"

MAX_SCHEMA_ERRORS = 50


@dataclass(frozen=True, slots=True)
class SchemaViolation:
    """Một vi phạm hình dạng — cùng hình dạng với `contract_validation.SchemaViolation`."""

    json_path: str
    error_code: str
    message: str
    field_name: str | None = None
    error_category: str = "schema"


def _json_path(absolute_path: Any) -> str:
    parts = ["$"]
    for item in absolute_path:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(f".{item}")
    return "".join(parts)


class ContractSchemaUnavailableError(Exception):
    """Không nạp được schema v2. Lỗi cấu hình của hệ thống, không phải lỗi payload."""


@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractSchemaUnavailableError(f"Không tìm thấy schema hợp đồng v2 tại {SCHEMA_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise ContractSchemaUnavailableError(f"Schema hợp đồng v2 không phải JSON hợp lệ: {exc}") from exc


class ContractValidatorV2:
    """Kiểm một payload theo JSON Schema 2020-12 của hợp đồng v2."""

    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self._schema = schema if schema is not None else load_schema()

    def validate(self, payload: Any) -> list[SchemaViolation]:
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(self._schema)

        violations: list[SchemaViolation] = []
        for error in validator.iter_errors(payload):
            path = _json_path(error.absolute_path)
            code = f"SCHEMA_{str(error.validator).upper()}"
            field = error.path[-1] if error.path and not isinstance(error.path[-1], int) else None
            violations.append(
                SchemaViolation(
                    json_path=path,
                    error_code=code,
                    message=error.message,
                    field_name=str(field) if field is not None else None,
                )
            )

        violations.sort(key=lambda v: (v.json_path, v.message))
        return violations[:MAX_SCHEMA_ERRORS]

    def is_valid(self, payload: Any) -> bool:
        return not self.validate(payload)
