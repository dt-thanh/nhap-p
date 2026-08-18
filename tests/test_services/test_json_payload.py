"""Test `JsonPayloadParser` — phong bì JSON của CRM.

Không cần DB: parser thuần là đọc + kiểm tra, cùng vai trò với
`ExcelParserService` ở luồng CSV.

Ranh giới quan trọng nhất mà file này chốt: sai ở PHONG BÌ thì hỏng cả lô
(`EnvelopeError`), sai ở MỘT bản ghi thì chỉ bản ghi đó hỏng và phần còn lại vẫn
đi tiếp.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.services.json_payload import (
    SUPPORTED_ENTITIES,
    EnvelopeError,
    JsonPayloadParser,
    payload_fingerprint,
    redact,
)

PROJECT_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


@pytest.fixture
def parser():
    return JsonPayloadParser()


def _envelope(**overrides):
    payload = {
        "source_system": "mini_crm",
        "source_instance_id": "crm-project-a",
        "source_entity": "units",
        "schema_version": 1,
        "external_batch_id": "batch-2026-08-09-001",
        "project_id": PROJECT_ID,
        "source_cursor": "cursor-123",
        "records": [
            {
                "source_record_id": "UNIT-001",
                "operation": "upsert",
                "source_updated_at": "2026-08-09T00:00:00Z",
                "data": {"unit_code": "A1-1203", "status": "available"},
            }
        ],
    }
    payload.update(overrides)
    return payload


# --- Phong bì hợp lệ --------------------------------------------------------


def test_valid_envelope_is_accepted(parser):
    envelope = parser.parse(_envelope(), entity_from_route="units")

    assert envelope.source_system == "mini_crm"
    assert envelope.source_instance_id == "crm-project-a"
    assert envelope.source_entity == "units"
    assert envelope.external_batch_id == "batch-2026-08-09-001"
    assert envelope.source_cursor == "cursor-123"
    assert envelope.records_received == 1
    assert envelope.errors == []

    record = envelope.records[0]
    assert record.source_record_id == "UNIT-001"
    assert record.operation == "upsert"
    assert record.source_updated_at == datetime(2026, 8, 9, tzinfo=UTC)
    assert record.json_path == "$.records[0]"
    assert record.payload_hash
    assert record.data == {"unit_code": "A1-1203", "status": "available"}


def test_sync_mode_defaults_to_incremental(parser):
    assert parser.parse(_envelope()).sync_mode == "incremental"
    # `full_snapshot` BẮT BUỘC kèm khối `snapshot` (Phase 5): không có nó thì lô
    # tự nhận là ảnh chụp nhưng không trả lời được "đủ mảnh chưa" và "phạm vi tới
    # đâu", nên chốt an toàn xoá không có gì để dựa vào.
    full = parser.parse(
        _envelope(
            sync_mode="full_snapshot",
            snapshot={
                "snapshot_id": "SNAP-T1",
                "chunk_index": 0,
                "chunk_total": 1,
                "snapshot_complete": True,
                "scope": {"entities": ["unit"]},
            },
        )
    )
    assert full.sync_mode == "full_snapshot"
    assert full.snapshot_id == "SNAP-T1"


def test_full_snapshot_without_snapshot_metadata_is_rejected(parser):
    """Lô khai là ảnh chụp mà không mang metadata ảnh chụp thì vô nghĩa."""
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(sync_mode="full_snapshot"))
    assert exc.value.error_code == "MISSING_SNAPSHOT_METADATA"


def test_incremental_batch_may_not_carry_snapshot_metadata(parser):
    """Hai chế độ suy ra xoá theo hai cách khác nhau; nhận cả hai là mơ hồ."""
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(
            _envelope(
                snapshot={
                    "snapshot_id": "SNAP-T1",
                    "chunk_index": 0,
                    "chunk_total": 1,
                    "snapshot_complete": True,
                    "scope": {"entities": ["unit"]},
                }
            )
        )
    assert exc.value.error_code == "SNAPSHOT_ON_INCREMENTAL"


def test_snapshot_without_explicit_scope_is_rejected(parser):
    """'Mọi thứ không có trong lô này' là tập không có biên nếu không khai biên."""
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(
            _envelope(
                sync_mode="full_snapshot",
                snapshot={"snapshot_id": "S", "chunk_index": 0, "chunk_total": 1, "snapshot_complete": True},
            )
        )
    assert exc.value.error_code == "MISSING_SNAPSHOT_SCOPE"


def test_empty_record_list_is_valid(parser):
    envelope = parser.parse(_envelope(records=[]))
    assert (envelope.records, envelope.errors, envelope.records_received) == ([], [], 0)


# --- Phong bì sai → hỏng cả lô ----------------------------------------------


@pytest.mark.parametrize("missing", ["source_system", "source_instance_id", "source_entity", "external_batch_id"])
def test_missing_required_envelope_field_is_rejected(parser, missing):
    payload = _envelope()
    del payload[missing]

    with pytest.raises(EnvelopeError) as exc:
        parser.parse(payload)

    assert exc.value.error_code == "INVALID_ENVELOPE"
    assert exc.value.json_path == f"$.{missing}"


def test_blank_envelope_field_is_rejected(parser):
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(source_system="   "))
    assert exc.value.error_code == "INVALID_ENVELOPE"


def test_non_object_body_is_rejected(parser):
    with pytest.raises(EnvelopeError) as exc:
        parser.parse([1, 2, 3])
    assert exc.value.error_code == "INVALID_ENVELOPE"


def test_unsupported_schema_version_is_rejected(parser):
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(schema_version=99))

    assert exc.value.error_code == "UNSUPPORTED_SCHEMA_VERSION"
    assert exc.value.json_path == "$.schema_version"
    assert "99" in exc.value.message


def test_non_integer_schema_version_is_rejected(parser):
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(schema_version="1"))
    assert exc.value.error_code == "INVALID_SCHEMA_VERSION"


def test_unsupported_entity_is_rejected(parser):
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(source_entity="customers"))

    assert exc.value.error_code == "UNSUPPORTED_ENTITY"
    assert exc.value.json_path == "$.source_entity"
    for supported in SUPPORTED_ENTITIES:
        assert supported in exc.value.message


def test_route_entity_must_match_envelope(parser):
    """Đường dẫn nói một đằng, phong bì nói một nẻo → từ chối, không chọn bên nào."""
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(source_entity="units"), entity_from_route="deals")
    assert exc.value.error_code == "ENTITY_MISMATCH"


def test_unsupported_sync_mode_is_rejected(parser):
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(sync_mode="delta"))
    assert exc.value.error_code == "UNSUPPORTED_SYNC_MODE"


def test_records_must_be_a_list(parser):
    with pytest.raises(EnvelopeError) as exc:
        parser.parse(_envelope(records={"source_record_id": "U-1"}))
    assert exc.value.error_code == "INVALID_ENVELOPE"


# --- Bản ghi sai → chỉ bản ghi đó hỏng --------------------------------------


def test_bad_record_does_not_kill_the_good_ones(parser):
    envelope = parser.parse(
        _envelope(
            records=[
                {"source_record_id": "UNIT-1", "source_updated_at": "2026-08-09T00:00:00Z", "data": {}},
                {"operation": "upsert", "data": {}},  # thiếu source_record_id
                {"source_record_id": "UNIT-3", "source_updated_at": "2026-08-09T00:00:00Z", "data": {}},
            ]
        )
    )

    assert [r.source_record_id for r in envelope.records] == ["UNIT-1", "UNIT-3"]
    assert len(envelope.errors) == 1
    assert envelope.errors[0].error_code == "MISSING_SOURCE_RECORD_ID"
    assert envelope.errors[0].json_path == "$.records[1].source_record_id"
    assert envelope.records_received == 3


def test_record_errors_carry_json_path_and_no_row_number(parser):
    """Lỗi JSON định vị bằng json_path; `row_number` để NULL (0006 cho phép)."""
    envelope = parser.parse(_envelope(records=[{"source_record_id": "U-1", "data": "khong-phai-object"}]))

    error = envelope.errors[0]
    assert error.json_path == "$.records[0].data"
    row = error.as_record("file-uuid")
    assert row["row_number"] is None
    assert row["json_path"] == "$.records[0].data"
    assert row["record_locator"] == "$.records[0].data"
    assert row["error_category"] == "schema"
    assert row["retry_status"] == "open"


def test_unknown_operation_is_rejected_not_defaulted(parser):
    """Không mặc định về 'upsert': đoán sai chiều một lệnh xoá là làm sống lại dữ liệu đã bỏ."""
    envelope = parser.parse(_envelope(records=[{"source_record_id": "U-1", "operation": "merge", "data": {}}]))

    assert envelope.records == []
    assert envelope.errors[0].error_code == "UNSUPPORTED_OPERATION"
    assert envelope.errors[0].json_path == "$.records[0].operation"


def test_operation_defaults_to_upsert_when_absent(parser):
    envelope = parser.parse(
        _envelope(records=[{"source_record_id": "U-1", "source_updated_at": "2026-08-09T00:00:00Z"}])
    )
    assert envelope.records[0].operation == "upsert"


def test_naive_timestamp_is_rejected(parser):
    """Mốc không múi giờ bị từ chối — không im lặng coi là UTC."""
    envelope = parser.parse(
        _envelope(records=[{"source_record_id": "U-1", "source_updated_at": "2026-08-09T00:00:00", "data": {}}])
    )

    assert envelope.records == []
    assert envelope.errors[0].error_code == "INVALID_TIMESTAMP"
    assert "múi giờ" in envelope.errors[0].message


def test_offset_timestamp_is_preserved(parser):
    envelope = parser.parse(
        _envelope(records=[{"source_record_id": "U-1", "source_updated_at": "2026-08-09T07:00:00+07:00", "data": {}}])
    )
    assert envelope.records[0].source_updated_at == datetime(2026, 8, 9, tzinfo=UTC)


def test_upsert_without_any_version_is_rejected(parser):
    """Không có phiên bản thì không so được thứ tự — từ chối ngay ở cửa."""
    envelope = parser.parse(_envelope(records=[{"source_record_id": "U-1", "data": {}}]))

    assert envelope.records == []
    assert envelope.errors[0].error_code == "MISSING_SOURCE_VERSION"


def test_delete_without_any_version_is_also_rejected(parser):
    """Lệnh xoá cũng phải mang phiên bản.

    Không có phiên bản thì không phân biệt được "xoá mới" với "lệnh xoá cũ đến
    muộn". Áp nhầm một lệnh xoá cũ lên bản ghi vừa được tạo lại là mất dữ liệu
    một cách im lặng — không có lỗi nào nổ ra, chỉ là bản ghi biến mất.
    """
    envelope = parser.parse(_envelope(records=[{"source_record_id": "U-1", "operation": "delete"}]))

    assert envelope.records == []
    assert envelope.errors[0].error_code == "MISSING_SOURCE_VERSION"


def test_payload_hash_is_never_used_to_order_records(parser):
    """Dấu vân payload KHÔNG được cứu một bản ghi thiếu phiên bản.

    Hash là hàm băm nên không đơn điệu: nó nói được "giống hay khác", không nói
    được "trước hay sau". Bản ghi có `data` phong phú đến đâu mà thiếu phiên bản
    thì vẫn phải bị từ chối.
    """
    rich = {"source_record_id": "U-9", "data": {"unit_code": "A1-01", "status": "sold", "gia": 123456789}}

    envelope = parser.parse(_envelope(records=[rich]))

    assert envelope.records == []
    assert envelope.errors[0].error_code == "MISSING_SOURCE_VERSION"


def test_delete_does_not_require_data(parser):
    envelope = parser.parse(
        _envelope(records=[{"source_record_id": "U-1", "operation": "delete", "source_revision": 5}])
    )

    assert envelope.records[0].operation == "delete"
    assert envelope.records[0].source_revision == 5
    assert envelope.errors == []


@pytest.mark.parametrize("revision", ["5", -1, True, 1.5])
def test_invalid_revision_is_rejected(parser, revision):
    envelope = parser.parse(
        _envelope(records=[{"source_record_id": "U-1", "operation": "delete", "source_revision": revision}])
    )
    assert envelope.errors[0].error_code == "INVALID_REVISION"


def test_duplicate_source_record_id_inside_one_batch_keeps_the_first(parser):
    envelope = parser.parse(
        _envelope(
            records=[
                {"source_record_id": "U-1", "source_revision": 1, "data": {"v": 1}},
                {"source_record_id": "U-1", "source_revision": 2, "data": {"v": 2}},
            ]
        )
    )

    assert [r.source_revision for r in envelope.records] == [1]
    assert envelope.errors[0].error_code == "DUPLICATE_SOURCE_RECORD_ID"
    assert envelope.errors[0].json_path == "$.records[1]"


# --- Dấu vân payload --------------------------------------------------------


def test_fingerprint_ignores_key_order():
    """CRM đổi thứ tự serialize không được biến mọi bản ghi thành 'khác nội dung'."""
    assert payload_fingerprint("upsert", {"a": 1, "b": 2}) == payload_fingerprint("upsert", {"b": 2, "a": 1})


def test_fingerprint_changes_with_content_and_operation():
    assert payload_fingerprint("upsert", {"a": 1}) != payload_fingerprint("upsert", {"a": 2})
    # Xoá và ghi ở cùng phiên bản là hai ý định khác nhau.
    assert payload_fingerprint("delete", {}) != payload_fingerprint("upsert", {})


# --- Che dữ liệu nhạy cảm ---------------------------------------------------


@pytest.mark.parametrize("key", ["phone", "email", "full_name", "note", "password", "api_key"])
def test_sensitive_keys_are_never_echoed(key):
    assert redact("0901234567", key) == f"<đã ẩn: {key}>"
    assert "0901234567" not in redact("0901234567", key)


def test_long_values_are_truncated():
    assert len(redact("x" * 500, "unit_code")) <= 32


def test_redact_handles_none_and_numbers():
    assert redact(None) == "<null>"
    assert redact(42, "bedrooms") == "42"
