"""Băm theo TRƯỜNG ĐÃ ÁNH XẠ: chỉ những gì ta thật sự lưu mới tính vào dấu vân.

Vì sao điều này quan trọng. Trước Phase 4, dấu vân băm cả `data`. Hệ nguồn gửi
kèm một trường ta không lưu — ghi chú, giá, người phụ trách — và chỉ trường đó
đổi, thì kết quả là: cùng phiên bản, khác dấu vân → **đụng độ giả**. Đụng độ
không phải chuyện nhỏ: nó giữ nguyên bản cũ và đẩy một việc cho người vận hành.
Vài lần như thế thì cảnh báo đụng độ bị bỏ qua theo thói quen, và lúc đó đụng độ
THẬT cũng chìm theo.

Chiều ngược lại nguy hiểm hơn và cũng được canh ở đây: bỏ sót một trường ĐANG
được lưu ra khỏi `MAPPED_FIELDS` sẽ khiến thay đổi thật trở nên vô hình — hai bản
khác nhau bị coi là một, và bản mới lặng lẽ không được ghi.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.services.json_payload import MAPPED_FIELDS, mapped_view, payload_fingerprint


def _unit_data(**overrides):
    data = {"area_name": "A1", "unit_type": "2PN", "unit_code": "A1-01", "status": "available"}
    data.update(overrides)
    return data


def _deal_data(**overrides):
    data = {"external_unit_id": "U-1", "status": "reserved", "reserved_at": "2026-08-01T00:00:00+07:00"}
    data.update(overrides)
    return data


# --- Trường ngoài ánh xạ không được đổi dấu vân -----------------------------


@pytest.mark.parametrize(
    "entity, base",
    [("units", _unit_data()), ("deals", _deal_data())],
)
def test_unmapped_fields_do_not_change_the_fingerprint(entity, base):
    """Trường ta không lưu thì đổi kiểu gì cũng không sinh đụng độ."""
    with_extra = {**base, "ghi_chu": "khách hẹn xem lại", "gia": 3_200_000_000, "nguoi_phu_trach": "NV-07"}

    assert payload_fingerprint("upsert", base, entity=entity) == payload_fingerprint(
        "upsert", with_extra, entity=entity
    )


def test_changing_only_an_unmapped_field_is_a_duplicate_not_a_conflict():
    """Đây chính là kịch bản sinh đụng độ giả trước Phase 4."""
    before = {**_unit_data(), "ghi_chu": "gọi lại thứ hai"}
    after = {**_unit_data(), "ghi_chu": "gọi lại thứ sáu"}

    assert payload_fingerprint("upsert", before, entity="units") == payload_fingerprint("upsert", after, entity="units")


# --- Trường trong ánh xạ PHẢI đổi dấu vân -----------------------------------


@pytest.mark.parametrize("field", ["area_name", "unit_type", "unit_code", "status"])
def test_every_mapped_unit_field_changes_the_fingerprint(field):
    """Bỏ sót một trường đang được lưu sẽ khiến thay đổi thật trở nên vô hình."""
    base = _unit_data()
    changed = _unit_data(**{field: "GIA-TRI-KHAC"})

    assert payload_fingerprint("upsert", base, entity="units") != payload_fingerprint("upsert", changed, entity="units")


@pytest.mark.parametrize("field", ["external_unit_id", "status", "reserved_at", "sold_at", "lost_at"])
def test_every_mapped_deal_field_changes_the_fingerprint(field):
    base = _deal_data()
    changed = _deal_data(**{field: "2099-01-01T00:00:00+07:00"})

    assert payload_fingerprint("upsert", base, entity="deals") != payload_fingerprint("upsert", changed, entity="deals")


def test_removing_a_mapped_field_changes_the_fingerprint():
    """Thiếu trường khác với trường có giá trị — cả hai đều là thay đổi thật."""
    base = _unit_data()
    without_status = {k: v for k, v in base.items() if k != "status"}

    assert payload_fingerprint("upsert", base, entity="units") != payload_fingerprint(
        "upsert", without_status, entity="units"
    )


# --- Tính chất chung của dấu vân --------------------------------------------


def test_operation_is_part_of_the_fingerprint():
    """Xoá và ghi ở CÙNG phiên bản là hai ý định khác nhau."""
    data = _unit_data()
    assert payload_fingerprint("upsert", data, entity="units") != payload_fingerprint("delete", data, entity="units")


def test_key_order_does_not_change_the_fingerprint():
    reordered = dict(reversed(list(_unit_data().items())))
    assert payload_fingerprint("upsert", _unit_data(), entity="units") == payload_fingerprint(
        "upsert", reordered, entity="units"
    )


def test_unknown_entity_falls_back_to_hashing_everything():
    """Không biết thực thể thì băm thừa còn hơn băm thiếu.

    Băm thiếu bỏ sót thay đổi thật; băm thừa chỉ sinh đụng độ thừa — sai theo
    hướng an toàn hơn hẳn.
    """
    base = _unit_data()
    with_extra = {**base, "truong_la": 1}

    assert payload_fingerprint("upsert", base) != payload_fingerprint("upsert", with_extra)
    assert payload_fingerprint("upsert", base, entity="khong-biet-la-gi") != payload_fingerprint(
        "upsert", with_extra, entity="khong-biet-la-gi"
    )


def test_mapped_view_keeps_only_mapped_keys():
    view = mapped_view("units", {**_unit_data(), "gia": 1, "ghi_chu": "x"})
    assert set(view) == {"area_name", "unit_type", "unit_code", "status"}


def test_mapped_view_tolerates_non_dict_data():
    assert mapped_view("units", None) is None
    assert mapped_view("units", "chuoi") == "chuoi"


# --- MAPPED_FIELDS phải khớp tầng chiếu -------------------------------------


def _projector_reads(function_name: str) -> set[str]:
    """Các khoá `data` mà một hàm chiếu thực sự đọc, moi ra bằng AST.

    Đọc mã nguồn thay vì chép tay danh sách: chép tay sẽ lệch, và lệch ở đây
    nghĩa là dấu vân bỏ sót một trường đang được lưu.
    """
    source = (Path(__file__).resolve().parents[2] / "src" / "services" / "domain_projection.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == function_name
    )

    keys: set[str] = set()
    for node in ast.walk(target):
        # `_require_text(data, "unit_code", record)` / `_optional_timestamp(data, "sold_at", record)`
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"_require_text", "_optional_timestamp"} and len(node.args) >= 2:
                if isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                    keys.add(node.args[1].value)
        # `data.get("status")`
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "data"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(node.args[0].value)
    return keys


def test_unit_mapped_fields_cover_everything_the_projector_reads():
    read = _projector_reads("_project_unit")
    declared = set(MAPPED_FIELDS["units"])

    missing = read - declared
    assert not missing, f"tầng chiếu đọc {sorted(missing)} nhưng MAPPED_FIELDS['units'] không khai — dấu vân bỏ sót"


def test_deal_mapped_fields_cover_everything_the_projector_reads():
    read = _projector_reads("_project_deal")
    declared = set(MAPPED_FIELDS["deals"])

    missing = read - declared
    assert not missing, f"tầng chiếu đọc {sorted(missing)} nhưng MAPPED_FIELDS['deals'] không khai — dấu vân bỏ sót"
