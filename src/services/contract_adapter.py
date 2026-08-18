"""Chuyển payload theo hợp đồng v1 sang phong bì nội bộ mà `JsonPayloadParser` đọc.

**Vì sao tồn tại module này.** Hợp đồng v1 (`docs/crm/sync_contract_v1_draft.md`)
được chốt ở Phase 2 với bộ tên KHÁI NIỆM: `external_id`, `payload`, `project_ref`,
`area_ref`. Đường ghi nội bộ có từ S2 lại dùng bộ tên khác cho cùng những khái
niệm đó: `source_record_id`, `data`, `project_id`, và phân khu nằm phẳng trong
`data`. Hai bộ tên, một tập khái niệm.

Có ba cách xử lý, và cách thứ ba là cách được chọn:

1. Đổi hợp đồng theo mã nguồn — hỏng mất thứ vừa được duyệt, và biến hợp đồng
   thành thứ chạy theo cài đặt thay vì ngược lại.
2. Đổi mã nguồn theo hợp đồng — phải viết lại `JsonPayloadParser` và toàn bộ test
   của S2 cùng lúc, tức là trộn việc đổi tên với việc thêm tính năng.
3. **Một tầng chuyển đổi mỏng, tách riêng, có test riêng** — hợp đồng giữ nguyên
   bộ tên đã duyệt, mã nguồn giữ nguyên bộ tên đang chạy, và chỗ nối giữa hai bên
   nằm gọn trong một file đọc hết được trong một lượt.

Đây là NỢ KỸ THUẬT có chủ đích, không phải thiết kế cuối cùng: khi Mini CRM có
thật và đường S2 cũ không còn ai gọi, tầng này nên biến mất bằng cách đổi tên ở
`JsonPayloadParser`. Ghi lại ở đây để lần sau không ai phải đoán vì sao có hai bộ
tên.

═══════════════════════════════════════════════════════════════════════════════
 TRẠNG THÁI HAI PHƯƠNG NGỮ
═══════════════════════════════════════════════════════════════════════════════

 **Hợp đồng v1 là hợp đồng CHÍNH THỨC DUY NHẤT** của Mini CRM tương lai.
 `docs/crm/sync_contract_v1_draft.md` + `src/contracts/crm_sync_v1.schema.json`.

 **Phương ngữ S2 (`source_record_id` / `data` / `project_id`) ĐÃ LỖI THỜI
 (DEPRECATED).** Nó chỉ còn tồn tại để bộ test hồi quy có sẵn tiếp tục chạy.

   * KHÔNG thêm tính năng mới cho phương ngữ S2.
   * KHÔNG mở rộng trường mới ở phương ngữ S2.
   * Tính năng mới CHỈ đi qua hợp đồng v1.
   * Hệ nguồn mới PHẢI dùng hợp đồng v1.

 Gỡ bỏ khi: Mini CRM chạy trên hợp đồng v1 và không còn lô nào tới bằng phương
 ngữ S2 (`upload_files.transport_mode = 'api_push'` mà không có `project_ref`).
═══════════════════════════════════════════════════════════════════════════════

**Phân biệt hai phương ngữ** bằng sự có mặt của `project_ref`: hợp đồng v1 bắt
buộc có nó, phương ngữ S2 không bao giờ có. Dùng một khoá BẮT BUỘC làm dấu hiệu
chứ không dùng khoá tuỳ chọn — khoá tuỳ chọn vắng mặt sẽ khiến payload bị hiểu
nhầm sang phương ngữ kia.
"""

from __future__ import annotations

from typing import Any

# Trạng thái căn của hợp đồng nằm ở `payload.unit_status`; tầng chiếu nội bộ đọc
# `data.status`. Cùng khái niệm, khác tên.
_UNIT_STATUS_KEY = "unit_status"
_DEAL_STATUS_KEY = "deal_status"


def is_contract_v1(payload: Any) -> bool:
    """Payload này có phải hợp đồng v1 không.

    `project_ref` là trường BẮT BUỘC của CẢ hợp đồng v1 LẪN v2 (Phase D) và không
    tồn tại trong phương ngữ S2, nên riêng nó không còn đủ để phân biệt — phải
    cộng thêm `schema_version == 1`. Đây là chốt chặn MỚI (Phase D), nhưng KHÔNG
    đổi hành vi cho một payload v1 THẬT: `schema_version` là trường bắt buộc của
    hợp đồng v1 và schema của nó khoá cứng `const: 1`, nên mọi payload v1 hợp lệ
    trước giờ vẫn khớp điều kiện dưới đây y hệt như trước.
    """
    return isinstance(payload, dict) and "project_ref" in payload and payload.get("schema_version") == 1


def is_contract_v2(payload: Any) -> bool:
    """Payload này có phải hợp đồng v2 không (Phase D — `schema_version == 2`).

    Cùng cách phân biệt với `is_contract_v1`, khác đúng hằng số phiên bản.
    """
    return isinstance(payload, dict) and "project_ref" in payload and payload.get("schema_version") == 2


def _project_id(project_ref: dict[str, Any]) -> str | None:
    """Lấy UUID dự án. `project_code` chưa tra được ở giai đoạn này."""
    return project_ref.get("project_id")


def _flatten_area_ref(area_ref: Any) -> dict[str, Any]:
    """`area_ref` lồng nhau → các khoá phẳng mà tầng chiếu nội bộ đang đọc."""
    if not isinstance(area_ref, dict):
        return {}
    if "area_id" in area_ref:
        return {"area_id": area_ref["area_id"]}
    return {
        "area_name": area_ref.get("area_name"),
        "unit_type": area_ref.get("unit_type"),
    }


def _adapt_unit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    data.update(_flatten_area_ref(payload.get("area_ref")))
    if "unit_code" in payload:
        data["unit_code"] = payload["unit_code"]
    if _UNIT_STATUS_KEY in payload:
        data["status"] = payload[_UNIT_STATUS_KEY]
    return data


def _adapt_deal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if "external_unit_id" in payload:
        data["external_unit_id"] = payload["external_unit_id"]
    if _DEAL_STATUS_KEY in payload:
        data["status"] = payload[_DEAL_STATUS_KEY]
    for moment in ("reserved_at", "sold_at", "lost_at"):
        if moment in payload:
            data[moment] = payload[moment]
    return data


def adapt(payload: dict[str, Any], *, entity_from_route: str | None = None) -> dict[str, Any]:
    """Hợp đồng v1 → phong bì nội bộ.

    KHÔNG kiểm tính hợp lệ: `ContractValidator` đã chạy trước và payload tới đây
    là đúng hình dạng. Trộn kiểm tra vào chuyển đổi sẽ tạo ra hai chỗ cùng quyết
    định "payload này có hợp lệ không", và hai chỗ đó sẽ lệch nhau.
    """
    records_in = payload.get("records") or []

    # `entity` nằm ở từng bản ghi trong hợp đồng, nhưng phong bì nội bộ mang một
    # `source_entity` cho cả lô. Lấy của bản ghi đầu; lô trộn hai loại entity đã
    # bị chặn từ trước bởi chính route (`/sync/{entity}`).
    entity = entity_from_route
    if entity is None and records_in:
        first = records_in[0]
        entity = f"{first.get('entity')}s" if isinstance(first, dict) and first.get("entity") else None

    records_out = []
    for record in records_in:
        if not isinstance(record, dict):
            continue
        inner = record.get("payload")
        record_entity = record.get("entity")

        data: dict[str, Any] = {}
        if isinstance(inner, dict):
            data = _adapt_unit_payload(inner) if record_entity == "unit" else _adapt_deal_payload(inner)

        adapted: dict[str, Any] = {
            "source_record_id": record.get("external_id"),
            "operation": record.get("operation"),
            "data": data,
        }
        # `payload_completeness` quyết định ý nghĩa của MỌI trường vắng mặt trong
        # `data`, nên nó phải đi cùng bản ghi qua đây. Chỉ đưa vào khi hệ nguồn có
        # khai — tầng dưới tự mặc định 'full', và đặt giá trị hộ ở đây sẽ làm mất
        # phân biệt giữa "khai full" với "không khai gì".
        if record.get("payload_completeness") is not None:
            adapted["payload_completeness"] = record["payload_completeness"]
        # Chỉ đưa vào khi CÓ: đặt None sẽ biến "không khai phiên bản" thành "khai
        # phiên bản rỗng", và tầng dưới phân biệt hai thứ đó.
        if record.get("source_revision") is not None:
            adapted["source_revision"] = record["source_revision"]
        if record.get("source_updated_at") is not None:
            adapted["source_updated_at"] = record["source_updated_at"]

        records_out.append(adapted)

    envelope: dict[str, Any] = {
        "source_system": payload.get("source_system"),
        "source_instance_id": payload.get("source_instance_id"),
        "source_entity": entity,
        "schema_version": payload.get("schema_version"),
        "external_batch_id": payload.get("external_batch_id"),
        "project_id": _project_id(payload.get("project_ref") or {}),
        "sync_mode": payload.get("sync_mode"),
        "records": records_out,
    }
    # Khối `snapshot` đi thẳng qua, KHÔNG đổi hình dạng: hợp đồng v1 và tầng dưới
    # dùng chung đúng bộ tên cho phần này. Trước Phase 5 nó bị bỏ ở đây, nên
    # `sync_mode='full_snapshot'` được nhận rồi cư xử y hệt `incremental` — chốt
    # an toàn xoá không có dữ liệu nào để dựa vào.
    if payload.get("snapshot") is not None:
        envelope["snapshot"] = payload["snapshot"]
    return envelope


# ═══════════════════════════════════════════════════════════════════════════
# Hợp đồng v2 (Phase D) — Mini CRM là NGUỒN SỰ THẬT cho CẢ BỐN tầng
# `docs/crm/sync_contract_v2_draft.md`. Bốn entity mới trên đường ghi:
# project/area/unit/deal — so với v1 chỉ có unit/deal.
# ═══════════════════════════════════════════════════════════════════════════


def _project_ref_v2(project_ref: dict[str, Any]) -> tuple[str | None, str | None]:
    """`(project_id, external_project_id)` — ĐÚNG MỘT trong hai khác `None`,
    theo `oneOf` của `$defs/project_ref` v2 (đã được `ContractValidatorV2` kiểm
    hình dạng trước khi tới đây)."""
    if "project_id" in project_ref:
        return project_ref.get("project_id"), None
    return None, project_ref.get("external_project_id")


def _flatten_area_ref_v2(area_ref: Any) -> dict[str, Any]:
    """v2 CHỈ chấp nhận `{external_area_id}` — không còn hai hình dạng của v1
    (§4 sync_contract_v2_draft.md)."""
    if not isinstance(area_ref, dict):
        return {}
    return {"external_area_id": area_ref.get("external_area_id")}


def _adapt_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if "name" in payload:
        data["name"] = payload["name"]
    if "launch_date" in payload:
        data["launch_date"] = payload["launch_date"]
    return data


def _adapt_area_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key in ("area_name", "unit_type", "bedrooms", "area_sqm", "total_units"):
        if key in payload:
            data[key] = payload[key]
    return data


def _adapt_unit_payload_v2(payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    data.update(_flatten_area_ref_v2(payload.get("area_ref")))
    if "unit_code" in payload:
        data["unit_code"] = payload["unit_code"]
    if _UNIT_STATUS_KEY in payload:
        data["status"] = payload[_UNIT_STATUS_KEY]
    return data


_V2_ADAPTERS = {
    "project": _adapt_project_payload,
    "area": _adapt_area_payload,
    "unit": _adapt_unit_payload_v2,
    "deal": _adapt_deal_payload,
}


def adapt_v2(payload: dict[str, Any], *, entity_from_route: str | None = None) -> dict[str, Any]:
    """Hợp đồng v2 → phong bì nội bộ. Cùng cấu trúc với `adapt()` (v1), khác ở
    chỗ có BỐN entity thay vì hai, và `project_ref` có thể là `external_project_id`.

    KHÔNG kiểm tính hợp lệ — `ContractValidatorV2` đã chạy trước (xem `src/api/sync.py`).
    """
    records_in = payload.get("records") or []

    entity = entity_from_route
    if entity is None and records_in:
        first = records_in[0]
        entity = f"{first.get('entity')}s" if isinstance(first, dict) and first.get("entity") else None

    records_out = []
    for record in records_in:
        if not isinstance(record, dict):
            continue
        inner = record.get("payload")
        record_entity = record.get("entity")
        adapter = _V2_ADAPTERS.get(record_entity)

        data: dict[str, Any] = {}
        if isinstance(inner, dict) and adapter is not None:
            data = adapter(inner)

        adapted: dict[str, Any] = {
            "source_record_id": record.get("external_id"),
            "operation": record.get("operation"),
            "data": data,
        }
        if record.get("payload_completeness") is not None:
            adapted["payload_completeness"] = record["payload_completeness"]
        if record.get("source_revision") is not None:
            adapted["source_revision"] = record["source_revision"]
        if record.get("source_updated_at") is not None:
            adapted["source_updated_at"] = record["source_updated_at"]

        records_out.append(adapted)

    project_id, external_project_id = _project_ref_v2(payload.get("project_ref") or {})

    envelope: dict[str, Any] = {
        "source_system": payload.get("source_system"),
        "source_instance_id": payload.get("source_instance_id"),
        "source_entity": entity,
        "schema_version": payload.get("schema_version"),
        "external_batch_id": payload.get("external_batch_id"),
        "project_id": project_id,
        "external_project_id": external_project_id,
        "sync_mode": payload.get("sync_mode"),
        "records": records_out,
    }
    if payload.get("snapshot") is not None:
        envelope["snapshot"] = payload["snapshot"]
    return envelope
