"""Đo kích thước, băm tất định, và chuyển đổi hợp đồng v1 → phong bì nội bộ.

Toàn bộ hàm ở đây là hàm thuần nên không cần database. Phần ghi/đọc DB được kiểm
ở `tests/test_api/test_sync_auth.py` qua đường HTTP thật.
"""

from __future__ import annotations

import pytest

from src.services.contract_adapter import adapt, is_contract_v1
from src.services.sync_payloads import (
    MAX_PAYLOAD_BYTES,
    PayloadTooLargeError,
    canonical_bytes,
    measure,
)


def _payload(records=None):
    return {
        "schema_version": 1,
        "source_system": "mini_crm",
        "source_instance_id": "synthetic-mini-crm",
        "external_batch_id": "SYNTH-BATCH-1",
        "sync_mode": "incremental",
        "project_ref": {"project_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301"},
        "source_extracted_at": "2026-08-09T02:00:00+07:00",
        "records": records if records is not None else [],
    }


# --- Băm tất định -----------------------------------------------------------


def test_canonical_bytes_ignore_key_order():
    """Cùng nội dung, khác thứ tự khoá → cùng byte.

    Đây là điều kiện để hash còn kiểm lại được sau khi payload đi qua JSONB, vốn
    không giữ thứ tự khoá.
    """
    assert canonical_bytes({"a": 1, "b": 2}) == canonical_bytes({"b": 2, "a": 1})


def test_canonical_bytes_ignore_whitespace_differences():
    import json

    payload = {"a": [1, 2], "b": {"c": 3}}
    spaced = json.loads(json.dumps(payload, indent=4))
    assert canonical_bytes(payload) == canonical_bytes(spaced)


def test_canonical_bytes_differ_when_content_differs():
    assert canonical_bytes({"a": 1}) != canonical_bytes({"a": 2})


def test_hash_is_computed_from_canonical_form_not_raw_bytes():
    """Hash phải tái lập được từ nội dung, kể cả khi byte gốc khác nhau.

    Hai body cùng nội dung nhưng khác thứ tự khoá/khoảng trắng phải cho cùng hash
    — nếu không, không lần băm lại nào từ DB khớp được và cột hash thành vô dụng.
    """
    payload = {"b": 2, "a": 1}
    from_raw = measure(payload, raw_body=b'{"b": 2, "a": 1}')
    from_canonical = measure({"a": 1, "b": 2})

    assert from_raw.sha256 == from_canonical.sha256


def test_size_is_measured_on_the_wire_bytes():
    """Kích thước lấy từ body gốc — đó mới là số byte thật đi qua đường truyền."""
    payload = {"a": 1}
    padded_body = b'{\n    "a": 1\n}'

    measured = measure(payload, raw_body=padded_body)

    assert measured.size_bytes == len(padded_body)
    assert measured.size_bytes != len(canonical_bytes(payload))


# --- Trần kích thước --------------------------------------------------------


def test_payload_at_the_limit_is_accepted():
    body = b"x" * MAX_PAYLOAD_BYTES
    assert measure({"records": []}, raw_body=body).size_bytes == MAX_PAYLOAD_BYTES


def test_payload_one_byte_over_the_limit_is_rejected():
    """Kiểm đúng biên: lệch một byte là chỗ lỗi off-by-one hay nấp."""
    body = b"x" * (MAX_PAYLOAD_BYTES + 1)
    with pytest.raises(PayloadTooLargeError) as exc:
        measure({"records": []}, raw_body=body)

    assert exc.value.size_bytes == MAX_PAYLOAD_BYTES + 1
    assert exc.value.limit_bytes == MAX_PAYLOAD_BYTES
    assert exc.value.error_code == "PAYLOAD_TOO_LARGE"


def test_record_count_is_taken_from_the_payload():
    measured = measure(_payload(records=[{"entity": "unit"}, {"entity": "unit"}]))
    assert measured.record_count == 2


def test_record_count_is_zero_when_records_is_not_a_list():
    """Payload méo mó không được làm sập phép đo — cổng hợp đồng mới là chỗ từ chối nó."""
    assert measure({"records": "khong-phai-mang"}).record_count == 0
    assert measure({}).record_count == 0


# --- Phân biệt hai phương ngữ ----------------------------------------------


def test_contract_v1_is_detected_by_project_ref():
    assert is_contract_v1(_payload()) is True


def test_s2_dialect_is_not_mistaken_for_contract_v1():
    """Phương ngữ S2 dùng `project_id` phẳng và không bao giờ có `project_ref`."""
    assert is_contract_v1({"project_id": "abc", "records": []}) is False


@pytest.mark.parametrize("value", [None, [], "chuoi", 42])
def test_non_dict_payloads_are_not_contract_v1(value):
    assert is_contract_v1(value) is False


# --- Chuyển đổi hợp đồng v1 → phong bì nội bộ -------------------------------


def test_adapt_maps_envelope_fields():
    adapted = adapt(_payload(), entity_from_route="units")

    assert adapted["source_system"] == "mini_crm"
    assert adapted["source_instance_id"] == "synthetic-mini-crm"
    assert adapted["external_batch_id"] == "SYNTH-BATCH-1"
    assert adapted["project_id"] == "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    assert adapted["source_entity"] == "units"


def test_adapt_maps_a_unit_record():
    record = {
        "entity": "unit",
        "operation": "upsert",
        "external_id": "SYNTH-U-1",
        "source_revision": 7,
        "payload": {
            "area_ref": {"area_name": "A1", "unit_type": "2PN"},
            "unit_code": "A1-01",
            "unit_status": "available",
        },
    }

    adapted = adapt(_payload([record]), entity_from_route="units")["records"][0]

    assert adapted["source_record_id"] == "SYNTH-U-1"
    assert adapted["operation"] == "upsert"
    assert adapted["source_revision"] == 7
    assert adapted["data"] == {
        "area_name": "A1",
        "unit_type": "2PN",
        "unit_code": "A1-01",
        # `unit_status` của hợp đồng → `status` của phong bì nội bộ.
        "status": "available",
    }


def test_adapt_maps_a_deal_record_with_all_moments():
    record = {
        "entity": "deal",
        "operation": "upsert",
        "external_id": "SYNTH-D-1",
        "source_revision": 2,
        "payload": {
            "external_unit_id": "SYNTH-U-1",
            "deal_status": "sold",
            "reserved_at": "2026-07-20T10:00:00+07:00",
            "sold_at": "2026-08-02T14:15:00+07:00",
        },
    }

    adapted = adapt(_payload([record]), entity_from_route="deals")["records"][0]

    assert adapted["data"]["external_unit_id"] == "SYNTH-U-1"
    assert adapted["data"]["status"] == "sold"
    # Mốc lịch sử phải đi qua nguyên vẹn — quyết định 2 phụ thuộc vào điều này.
    assert adapted["data"]["reserved_at"] == "2026-07-20T10:00:00+07:00"
    assert adapted["data"]["sold_at"] == "2026-08-02T14:15:00+07:00"


def test_adapt_keeps_area_id_when_the_reference_uses_it():
    record = {
        "entity": "unit",
        "operation": "upsert",
        "external_id": "SYNTH-U-2",
        "source_revision": 1,
        "payload": {
            "area_ref": {"area_id": "11111111-2222-3333-4444-555555555555"},
            "unit_code": "A1-02",
            "unit_status": "sold",
        },
    }

    data = adapt(_payload([record]), entity_from_route="units")["records"][0]["data"]

    assert data["area_id"] == "11111111-2222-3333-4444-555555555555"
    assert "area_name" not in data


def test_adapt_omits_version_fields_that_were_not_supplied():
    """Không khai phiên bản KHÁC với khai phiên bản rỗng.

    Đặt `None` sẽ khiến tầng dưới tưởng có trường rồi bỏ qua phép kiểm
    `MISSING_SOURCE_VERSION`.
    """
    record = {
        "entity": "unit",
        "operation": "upsert",
        "external_id": "SYNTH-U-3",
        "source_updated_at": "2026-08-09T00:00:00+07:00",
        "payload": {
            "area_ref": {"area_name": "A1", "unit_type": "2PN"},
            "unit_code": "A1-03",
            "unit_status": "available",
        },
    }

    adapted = adapt(_payload([record]), entity_from_route="units")["records"][0]

    assert "source_revision" not in adapted
    assert adapted["source_updated_at"] == "2026-08-09T00:00:00+07:00"


def test_adapt_carries_delete_operations_without_data():
    record = {"entity": "deal", "operation": "delete", "external_id": "SYNTH-D-9", "source_revision": 9}

    adapted = adapt(_payload([record]), entity_from_route="deals")["records"][0]

    assert adapted["operation"] == "delete"
    assert adapted["source_record_id"] == "SYNTH-D-9"
    assert adapted["data"] == {}


def test_adapt_infers_entity_from_records_when_route_is_absent():
    record = {
        "entity": "unit",
        "operation": "upsert",
        "external_id": "SYNTH-U-4",
        "source_revision": 1,
        "payload": {
            "area_ref": {"area_name": "A1", "unit_type": "2PN"},
            "unit_code": "A1-04",
            "unit_status": "available",
        },
    }

    assert adapt(_payload([record]))["source_entity"] == "units"
