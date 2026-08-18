"""Kiểm payload đồng bộ theo JSON Schema của hợp đồng, TRƯỚC khi chạm nghiệp vụ.

Đây là cổng thứ hai, sau xác thực. Nó trả lời đúng một câu: payload này có đúng
HÌNH DẠNG mà hợp đồng mô tả không. Nó cố tình KHÔNG biết gì về phân khu, trạng
thái hợp lệ hay thứ tự phiên bản — những thứ đó là nghiệp vụ và thuộc về
`JsonPayloadParser`/`SyncRunService`.

Tách làm hai tầng vì hai loại lỗi cần hai câu trả lời khác nhau: sai hình dạng là
lỗi của người TÍCH HỢP (sửa code adapter), còn sai nghiệp vụ là lỗi của DỮ LIỆU
(sửa bản ghi trong CRM). Trộn chúng lại sẽ khiến người nhận báo lỗi không biết
phải sửa ở đâu.

**Schema được nạp từ file, không nhúng vào mã.** `src/contracts/crm_sync_v1.schema.json`
là bản DUY NHẤT, và cũng chính là bản giao cho người xây Mini CRM (Phase 10). Hai
bản sao sẽ lệch nhau, và lúc đó không ai biết bản nào là hợp đồng thật.

Nó nằm trong `src/` chứ không phải `docs/` vì một lý do rất cụ thể: `.dockerignore`
loại `docs/` khỏi image, nên đặt ở đó thì runtime không đọc được và mọi lô đồng bộ
trả 500 — đúng lỗi đã gặp khi chạy thử trình mô phỏng lần đầu. File mà tiến trình
phải nạp lúc chạy là TÀI NGUYÊN, không phải tài liệu, dù nó cũng được đọc như tài liệu.

**Lỗi được sắp xếp tất định.** `jsonschema` không đảm bảo thứ tự lỗi giữa các lần
chạy; không sắp thì cùng một payload hỏng sẽ cho ra thứ tự lỗi khác nhau, và test
sẽ chớp tắt. Sắp theo `json_path` rồi tới thông báo.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.logging_config import get_logger

log = get_logger("src.services.contract_validation")

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "crm_sync_v1.schema.json"

# Trần số lỗi hình dạng trả về. Một payload sai cấu trúc gốc có thể sinh ra hàng
# nghìn lỗi con; trả hết chỉ làm phản hồi phình mà không thêm thông tin nào.
MAX_SCHEMA_ERRORS = 50


class ContractSchemaUnavailableError(Exception):
    """Không nạp được schema. Đây là lỗi cấu hình của hệ thống, không phải lỗi payload."""


@dataclass(frozen=True, slots=True)
class SchemaViolation:
    """Một vi phạm hình dạng, đã ở sẵn dạng ghi được vào `upload_errors`."""

    json_path: str
    error_code: str
    message: str
    field_name: str | None = None

    # Luôn là 'schema': tầng này chỉ nói về hình dạng.
    error_category: str = "schema"


@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Nạp và cache schema hợp đồng.

    Cache vì file không đổi trong một tiến trình, và đọc lại đĩa cho mỗi request
    là chi phí thuần tuý lãng phí.
    """
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractSchemaUnavailableError(f"Không tìm thấy schema hợp đồng tại {SCHEMA_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise ContractSchemaUnavailableError(f"Schema hợp đồng không phải JSON hợp lệ: {exc}") from exc


def _json_path(absolute_path: Any) -> str:
    """Đường dẫn của jsonschema (deque) → chuỗi JSONPath đọc được.

    `$.records[3].payload.unit_status` định vị được ngay trong payload gốc; danh
    sách `['records', 3, 'payload']` thì không.
    """
    parts = ["$"]
    for item in absolute_path:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(f".{item}")
    return "".join(parts)


class ContractValidator:
    """Kiểm một payload theo JSON Schema 2020-12 của hợp đồng v1."""

    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self._schema = schema if schema is not None else load_schema()

    def validate(self, payload: Any) -> list[SchemaViolation]:
        """Trả danh sách vi phạm. Rỗng = payload đúng hình dạng hợp đồng.

        KHÔNG ném exception khi payload sai: sai hình dạng là kết quả bình thường
        của một cổng kiểm, và phía gọi cần cả danh sách để trả về một lần, không
        phải lỗi đầu tiên.
        """
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(self._schema)

        violations: list[SchemaViolation] = []
        for error in validator.iter_errors(payload):
            path = _json_path(error.absolute_path)
            # `validator` là từ khoá schema đã bị vi phạm ('required', 'enum',
            # 'pattern'…). Dùng nó làm error_code cho người tích hợp biết luật nào
            # bị phạm, thay vì một mã chung chung.
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
