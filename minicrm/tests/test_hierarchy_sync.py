"""Phase C — phong bì v2, thứ tự phân cấp, và kiểm hợp đồng v2 (`contract_v2`).

KHÔNG cần database thật: mọi test ở đây gọi thẳng `sync_client.build_*` và
`contract_v2.validate`/`assert_valid`, cùng khuôn với `test_contract_copy.py` và
`test_sync_client.py` (v1) — chứng minh Mini CRM DỰNG đúng phong bì gì, không phải
backend nhận nó ra sao (backend còn CHƯA nhận — xem docstring `sync_client.py` mục
v2 và `crud._capture_v2`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app import contract_v2, sync_client

PROJECT = {"external_id": "P-0001", "name": "Khu do thi Ben Xanh", "launch_date": "2026-06-01", "source_revision": 1}
AREA = {
    "external_id": "A-0001",
    "area_name": "A1",
    "unit_type": "2PN",
    "bedrooms": 2,
    "area_sqm": 68.5,
    "total_units": 120,
    "source_revision": 1,
}
UNIT = {
    "external_id": "U-0101",
    "external_area_id": "A-0001",
    "unit_code": "A1-01-01",
    "unit_status": "available",
    "source_revision": 1,
}
DEAL = {
    "external_id": "D-0101",
    "external_unit_id": "U-0101",
    "deal_status": "reserved",
    "reserved_at": "2026-08-10T09:00:00+07:00",
    "sold_at": None,
    "lost_at": None,
    "source_revision": 1,
}


# --- Phiên bản/ranh giới -------------------------------------------------------


def test_v2_schema_version_is_2_and_v1_is_unaffected():
    assert contract_v2.SCHEMA_VERSION == 2
    from app import contract

    assert contract.SCHEMA_VERSION == 1


def test_entity_path_maps_every_v2_capture_tag_to_its_correct_v2_route():
    """Phase C (bản gốc): `ENTITY_PATH` (v1, gửi ngay) và `V2_CAPTURE_ENTITIES`
    (v2, CHỈ LƯU) tách rời — v2 chưa có đường gửi nào để map tới.

    Phase C.5: vòng relay tự động cần `ENTITY_PATH` biết CẢ SÁU nhãn outbox để
    `deliver()` (gọi lại từ `relay.py`) map đúng URL — bốn nhãn v2 giờ CÓ MẶT
    trong `ENTITY_PATH`, CÓ CHỦ ĐÍCH. Bất biến còn giữ nguyên không phải "tách
    rời" nữa, mà là "mỗi nhãn trỏ ĐÚNG route của chính nó, không nhãn nào lẫn
    route của nhãn khác" — nhãn v1 giữ nguyên ý nghĩa, nhãn v2 trỏ đúng bốn route
    v2 mà Phase D đã mở.
    """
    assert sync_client.ENTITY_PATH["units"] == "units"
    assert sync_client.ENTITY_PATH["deals"] == "deals"
    assert sync_client.ENTITY_PATH["units_v2"] == "units"
    assert sync_client.ENTITY_PATH["deals_v2"] == "deals"
    assert sync_client.ENTITY_PATH["projects"] == "projects"
    assert sync_client.ENTITY_PATH["areas"] == "areas"
    assert set(sync_client.V2_CAPTURE_ENTITIES) <= set(sync_client.ENTITY_PATH), (
        "vòng relay (Phase C.5) cần map được cả bốn nhãn v2 — thiếu một nhãn nào "
        "thì deliver() ném ValueError ngay khi relay cố gửi dòng đó"
    )


# --- Dựng phong bì mỗi tầng ----------------------------------------------------


def test_build_project_envelope_upsert_shape():
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="mc-v2-projects-1")
    assert envelope["schema_version"] == 2
    assert envelope["project_ref"] == {"external_project_id": "P-0001"}
    assert envelope["records"] == [
        {
            "entity": "project",
            "operation": "upsert",
            "external_id": "P-0001",
            "source_revision": 1,
            "payload": {"name": "Khu do thi Ben Xanh", "launch_date": "2026-06-01"},
        }
    ]


def test_build_project_envelope_delete_has_no_payload():
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="mc-v2-projects-2", operation="delete")
    assert envelope["records"][0]["operation"] == "delete"
    assert "payload" not in envelope["records"][0]


def test_build_area_envelope_carries_all_five_authoritative_fields():
    envelope = sync_client.build_area_envelope([AREA], batch_id="mc-v2-areas-1", external_project_id="P-0001")
    payload = envelope["records"][0]["payload"]
    assert payload == {"area_name": "A1", "unit_type": "2PN", "bedrooms": 2, "area_sqm": 68.5, "total_units": 120}
    assert envelope["project_ref"] == {"external_project_id": "P-0001"}


def test_build_unit_envelope_v2_uses_external_area_id_not_name():
    envelope = sync_client.build_unit_envelope_v2([UNIT], batch_id="mc-v2-units-1", external_project_id="P-0001")
    payload = envelope["records"][0]["payload"]
    assert payload["area_ref"] == {"external_area_id": "A-0001"}
    assert set(payload) == {"area_ref", "unit_code", "unit_status"}


def test_build_deal_envelope_v2_has_no_project_or_area_ref():
    envelope = sync_client.build_deal_envelope_v2([DEAL], batch_id="mc-v2-deals-1", external_project_id="P-0001")
    payload = envelope["records"][0]["payload"]
    assert set(payload) == {"external_unit_id", "deal_status", "reserved_at", "sold_at", "lost_at"}
    assert "project_ref" not in payload and "area_ref" not in payload


def test_v1_unit_envelope_is_byte_identical_in_shape_after_v2_additions():
    """v2 builders được thêm CẠNH v1, không thay nó. `build_unit_envelope` (v1)
    vẫn dùng `area_name`/`unit_type`, không đụng `external_area_id`."""
    v1_unit = {
        "external_id": "U-0101",
        "area_name": "A1",
        "unit_type": "2PN",
        "unit_code": "A1-01-01",
        "unit_status": "available",
        "source_revision": 1,
    }
    envelope = sync_client.build_unit_envelope([v1_unit], batch_id="mc-units-1")
    assert envelope["schema_version"] == 1
    assert envelope["records"][0]["payload"]["area_ref"] == {"area_name": "A1", "unit_type": "2PN"}


# --- Thứ tự phân cấp (§A5.2) ---------------------------------------------------


def test_order_hierarchy_records_upsert_is_project_area_unit_deal():
    records = [
        {"entity": "deal", "operation": "upsert"},
        {"entity": "project", "operation": "upsert"},
        {"entity": "unit", "operation": "upsert"},
        {"entity": "area", "operation": "upsert"},
    ]
    ordered = sync_client.order_hierarchy_records(records, operation="upsert")
    assert [r["entity"] for r in ordered] == ["project", "area", "unit", "deal"]


def test_order_hierarchy_records_delete_is_reversed():
    records = [
        {"entity": "project", "operation": "delete"},
        {"entity": "unit", "operation": "delete"},
        {"entity": "deal", "operation": "delete"},
        {"entity": "area", "operation": "delete"},
    ]
    ordered = sync_client.order_hierarchy_records(records, operation="delete")
    assert [r["entity"] for r in ordered] == ["deal", "unit", "area", "project"]


def test_order_hierarchy_records_is_stable_within_a_tier():
    records = [
        {"entity": "area", "operation": "upsert", "external_id": "A-0002"},
        {"entity": "area", "operation": "upsert", "external_id": "A-0001"},
    ]
    ordered = sync_client.order_hierarchy_records(records, operation="upsert")
    assert [r["external_id"] for r in ordered] == ["A-0002", "A-0001"]


def test_order_hierarchy_records_rejects_unknown_entity():
    with pytest.raises(ValueError, match="entity không hợp lệ"):
        sync_client.order_hierarchy_records([{"entity": "customer", "operation": "upsert"}], operation="upsert")


def test_build_hierarchy_envelope_full_batch_matches_fixture_19_order():
    """Mô phỏng `docs/crm/fixtures/19_v2_full_hierarchy_ordered.json`: một lô trộn
    bốn tầng, ĐÚNG thứ tự project → area → unit → deal, dù build theo thứ tự khác."""
    project_env = sync_client.build_project_envelope([PROJECT], batch_id="p")
    area_env = sync_client.build_area_envelope([AREA], batch_id="a", external_project_id="P-0001")
    unit_env = sync_client.build_unit_envelope_v2([UNIT], batch_id="u", external_project_id="P-0001")
    deal_env = sync_client.build_deal_envelope_v2([DEAL], batch_id="d", external_project_id="P-0001")

    mixed = sync_client.build_hierarchy_envelope(
        deal_env["records"] + unit_env["records"] + project_env["records"] + area_env["records"],
        batch_id="mc-v2-mixed-1",
        external_project_id="P-0001",
    )
    assert [r["entity"] for r in mixed["records"]] == ["project", "area", "unit", "deal"]


def test_build_hierarchy_envelope_rejects_mixed_operations():
    with pytest.raises(ValueError, match="không trộn"):
        sync_client.build_hierarchy_envelope(
            [{"entity": "project", "operation": "upsert"}, {"entity": "area", "operation": "delete"}],
            batch_id="mc-v2-mixed-2",
            external_project_id="P-0001",
        )


def test_build_hierarchy_envelope_rejects_empty_records():
    with pytest.raises(ValueError, match="không có bản ghi"):
        sync_client.build_hierarchy_envelope([], batch_id="mc-v2-mixed-3", external_project_id="P-0001")


# --- contract_v2: schema + nghiệp vụ -------------------------------------------


def test_valid_full_hierarchy_envelope_passes_contract_v2():
    project_env = sync_client.build_project_envelope([PROJECT], batch_id="p")
    area_env = sync_client.build_area_envelope([AREA], batch_id="a", external_project_id="P-0001")
    unit_env = sync_client.build_unit_envelope_v2([UNIT], batch_id="u", external_project_id="P-0001")
    deal_env = sync_client.build_deal_envelope_v2([DEAL], batch_id="d", external_project_id="P-0001")
    mixed = sync_client.build_hierarchy_envelope(
        project_env["records"] + area_env["records"] + unit_env["records"] + deal_env["records"],
        batch_id="mc-v2-valid-1",
        external_project_id="P-0001",
    )
    assert contract_v2.validate(mixed) == []
    contract_v2.assert_valid(mixed)  # không ném


def test_area_payload_missing_planning_fields_is_rejected():
    """Mô phỏng fixture 23: thiếu trường kế hoạch bắt buộc."""
    envelope = sync_client.build_area_envelope([AREA], batch_id="a", external_project_id="P-0001")
    del envelope["records"][0]["payload"]["total_units"]
    problems = contract_v2.validate(envelope)
    assert any("total_units" in p for p in problems)


def test_area_ref_by_name_is_removed_in_v2():
    """Mô phỏng fixture 24: v2 KHÔNG chấp nhận `{area_name, unit_type}`."""
    envelope = sync_client.build_unit_envelope_v2([UNIT], batch_id="u", external_project_id="P-0001")
    envelope["records"][0]["payload"]["area_ref"] = {"area_name": "A1", "unit_type": "2PN"}
    assert contract_v2.validate(envelope) != []


def test_project_payload_with_parent_ref_is_rejected():
    """Mô phỏng fixture 25: Project là gốc, không mang `project_ref` riêng trong payload."""
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="p")
    envelope["records"][0]["payload"]["project_ref"] = {"external_project_id": "P-0001"}
    assert contract_v2.validate(envelope) != []


def test_delete_operation_carrying_payload_is_rejected():
    """Mô phỏng fixture 26."""
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="p", operation="delete")
    envelope["records"][0]["payload"] = {"name": "x", "launch_date": "2026-01-01"}
    assert contract_v2.validate(envelope) != []


def test_launch_date_with_timezone_offset_is_rejected():
    """Mô phỏng fixture 27: `launch_date` là NGÀY LỊCH, không phải mốc có múi giờ."""
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="p")
    envelope["records"][0]["payload"]["launch_date"] = "2026-06-01T00:00:00+07:00"
    assert contract_v2.validate(envelope) != []


def test_child_before_parent_in_the_same_batch_is_rejected():
    """Mô phỏng fixture 28: căn đứng TRƯỚC phân khu của nó trong cùng một lô —
    hợp lệ theo schema, sai theo §A5.2 (kiểm ở `contract_v2._order_violations`)."""
    unit_env = sync_client.build_unit_envelope_v2([UNIT], batch_id="u", external_project_id="P-0001")
    area_env = sync_client.build_area_envelope([AREA], batch_id="a", external_project_id="P-0001")
    out_of_order = {**area_env, "external_batch_id": "mc-v2-oop-1", "records": unit_env["records"] + area_env["records"]}
    problems = contract_v2.validate(out_of_order)
    assert any("SAI thứ tự" in p for p in problems)


def test_project_record_mismatching_envelope_ref_is_rejected():
    """Mô phỏng fixture 29: `external_id` của bản ghi `project` khác `project_ref`."""
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="p")
    envelope["project_ref"] = {"external_project_id": "P-9999"}
    problems = contract_v2.validate(envelope)
    assert any("PROJECT_REF_MISMATCH" in p for p in problems)


def test_empty_records_batch_is_rejected():
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="p")
    envelope["records"] = []
    problems = contract_v2.validate(envelope)
    assert any("rỗng" in p for p in problems)


def test_unknown_schema_version_is_rejected_by_schema():
    envelope = sync_client.build_project_envelope([PROJECT], batch_id="p")
    envelope["schema_version"] = 1
    assert contract_v2.validate(envelope) != []


def test_assert_valid_raises_contract_v2_violation_error_with_details():
    envelope = sync_client.build_area_envelope([AREA], batch_id="a", external_project_id="P-0001")
    del envelope["records"][0]["payload"]["bedrooms"]
    with pytest.raises(contract_v2.ContractV2ViolationError) as excinfo:
        contract_v2.assert_valid(envelope)
    assert excinfo.value.violations


# --- Hai bản sao schema v2 song hành (src/ và minicrm/) ------------------------


def test_v2_schema_copies_are_byte_identical():
    backend_schema = Path(contract_v2.CONTRACT_ROOT).parents[1] / "src" / "contracts" / "crm_sync_v2.schema.json"
    assert contract_v2.schema_sha256() == hashlib.sha256(backend_schema.read_bytes()).hexdigest()
