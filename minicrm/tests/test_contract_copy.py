"""Bản sao schema của Mini CRM, và phép canh cho nó khỏi trôi khỏi bản gốc.

`src/services/contract_validation.py` nói đúng rằng hai bản sao sẽ lệch nhau và
lúc đó không ai biết bản nào là hợp đồng thật. Mini CRM vẫn phải giữ một bản sao
(image riêng, build context riêng, `src/` không tồn tại trong image đó — xem
docstring `app/contract.py`), nên rủi ro đó phải được ĐÓNG bằng một phép kiểm chứ
không bằng một lời hứa.

Phép kiểm là băm SHA-256 của BYTE THÔ. Không phải so cây JSON đã parse: hai file
khác nhau về khoảng trắng hay thứ tự khoá là hai FILE khác nhau, và một trong hai
sẽ được ai đó sửa mà quên bản kia.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app import contract

MINICRM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MINICRM_ROOT.parent
BACKEND_SCHEMA = REPO_ROOT / "src" / "contracts" / "crm_sync_v1.schema.json"

PROJECT_ID = "40fa9c11-4e0f-589c-80a4-9bcb1f889960"


def _envelope(records, **overrides):
    base = {
        "schema_version": 1,
        "source_system": "mini_crm",
        "source_instance_id": "mini-crm-dev",
        "external_batch_id": "mc-units-test",
        "sync_mode": "incremental",
        "project_ref": {"project_id": PROJECT_ID},
        "source_extracted_at": "2026-08-11T10:00:00+00:00",
        "records": records,
    }
    base.update(overrides)
    return base


def _unit_record(**overrides):
    record = {
        "entity": "unit",
        "operation": "upsert",
        "external_id": "U-0001",
        "source_revision": 1,
        "payload": {
            "area_ref": {"area_name": "DEMO Toà B1", "unit_type": "Căn hộ"},
            "unit_code": "B1-01-01",
            "unit_status": "available",
        },
    }
    record.update(overrides)
    return record


def _deal_record(**overrides):
    record = {
        "entity": "deal",
        "operation": "upsert",
        "external_id": "D-0001",
        "source_revision": 1,
        "payload": {
            "external_unit_id": "U-0001",
            "deal_status": "reserved",
            "reserved_at": "2026-08-01T09:00:00+00:00",
            "sold_at": None,
            "lost_at": None,
        },
    }
    record.update(overrides)
    return record


# --- 14. Bản sao khớp bản gốc ------------------------------------------------


def test_the_copied_schema_is_byte_identical_to_the_backend_schema():
    """PHÉP CANH CHÍNH. Hỏng test này = hợp đồng đã tách làm hai bản khác nhau.

    Sửa một trong hai file mà không sửa file kia thì đây là chỗ duy nhất phát
    hiện ra — không có cơ chế nào khác nối hai file này với nhau.
    """
    if not BACKEND_SCHEMA.exists():
        # Trong image của Mini CRM thì `src/` không tồn tại, và đó là ĐÚNG thiết
        # kế. Phép canh này thuộc về máy build/dev, nơi cả hai file cùng có mặt.
        pytest.skip(f"Không có {BACKEND_SCHEMA} — chỉ chạy được ở repo, không chạy trong image Mini CRM")

    backend_hash = hashlib.sha256(BACKEND_SCHEMA.read_bytes()).hexdigest()
    assert contract.schema_sha256() == backend_hash, (
        "minicrm/contracts/crm_sync_v1.schema.json đã trôi khỏi src/contracts/crm_sync_v1.schema.json. "
        "Chép lại bản của backend đè lên bản sao; đừng sửa bản sao."
    )


def test_the_copy_is_the_v1_contract_and_nothing_else():
    schema = contract.load_schema()
    assert schema["$id"].endswith("crm_sync_v1.schema.json")
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["additionalProperties"] is False


def test_contract_module_never_reads_the_backend_schema_path():
    """Bản sao phải được nạp từ `minicrm/contracts/`, không phải từ `src/`.

    Nạp từ `src/` sẽ khiến test ở máy dev xanh trong khi container hỏng — `src/`
    không có trong image của Mini CRM.
    """
    assert contract.SCHEMA_PATH == MINICRM_ROOT / "contracts" / "crm_sync_v1.schema.json"
    assert contract.SCHEMA_PATH.exists()
    source = (MINICRM_ROOT / "app" / "contract.py").read_text(encoding="utf-8")
    assert "parents[1] / \"contracts\"" in source


# --- 13/19. Phong bì hợp lệ đi qua ------------------------------------------


def test_a_well_formed_unit_envelope_passes():
    assert contract.validate(_envelope([_unit_record()]), entity="units") == []


def test_a_well_formed_deal_envelope_passes():
    assert contract.validate(_envelope([_deal_record()]), entity="deals") == []


def test_a_delete_record_without_a_payload_passes():
    record = {"entity": "unit", "operation": "delete", "external_id": "U-0001", "source_revision": 2}
    assert contract.validate(_envelope([record]), entity="units") == []


# --- Từng hạng mục kiểm bắt buộc --------------------------------------------


def test_a_delete_record_carrying_a_payload_is_rejected():
    """Hợp đồng CẤM `payload` khi `operation=delete`."""
    record = {
        "entity": "unit",
        "operation": "delete",
        "external_id": "U-0001",
        "source_revision": 2,
        "payload": {"unit_status": "blocked"},
    }
    problems = contract.validate(_envelope([record]), entity="units")
    assert problems
    assert any("payload" in p for p in problems)


def test_a_record_without_any_version_is_rejected():
    """Mọi bản ghi phải mang phiên bản, kể cả lệnh xoá — nếu không backend trả
    `MISSING_SOURCE_VERSION`."""
    record = _unit_record()
    del record["source_revision"]
    problems = contract.validate(_envelope([record]), entity="units")
    assert any("source_revision" in p for p in problems)


def test_entity_must_match_the_route():
    """Bản ghi `deal` gửi lên `/sync/units` ⇒ backend trả 409 ENTITY_MISMATCH."""
    problems = contract.validate(_envelope([_deal_record()]), entity="units")
    assert any("không khớp đường dẫn" in p for p in problems)


def test_a_non_uuid_project_ref_is_rejected():
    envelope = _envelope([_unit_record()], project_ref={"project_id": "DEMO Căn hộ Bến Xanh"})
    problems = contract.validate(envelope, entity="units")
    assert any("project_id" in p for p in problems)


def test_a_naive_timestamp_is_rejected():
    """Mốc không có offset múi giờ không nói được nó là giờ nào, mà đây lại là
    căn cứ xếp thứ tự sự kiện."""
    envelope = _envelope([_unit_record()], source_extracted_at="2026-08-11T10:00:00")
    assert contract.validate(envelope, entity="units")


def test_an_unknown_field_is_rejected():
    """`additionalProperties: false` — trường lạ bị chặn ở nguồn.

    Đây là chốt chống rò rỉ quan trọng nhất: nếu Mini CRM có ngày mọc thêm giá
    hay tên khách, hợp đồng sẽ không chở chúng đi được.
    """
    record = _unit_record()
    record["payload"]["price_vnd"] = 3_200_000_000
    assert contract.validate(_envelope([record]), entity="units")


def test_an_unknown_unit_status_is_rejected_before_sending():
    """Backend KHÔNG có bảng alias cho căn — nó chỉ nhận đúng bốn giá trị chuẩn.

    Schema để `unit_status` mở (hợp đồng nói đó là "giá trị nguyên văn của hệ
    nguồn"), nên chốt này chỉ có ở tầng nghiệp vụ của Mini CRM.
    """
    record = _unit_record()
    record["payload"]["unit_status"] = "con_trong"
    problems = contract.validate(_envelope([record]), entity="units")
    assert any("unit_status" in p for p in problems)


def test_an_unknown_deal_status_is_rejected_before_sending():
    record = _deal_record()
    record["payload"]["deal_status"] = "da_dat_coc"
    problems = contract.validate(_envelope([record]), entity="deals")
    assert any("deal_status" in p for p in problems)


@pytest.mark.parametrize(
    ("status", "missing"),
    [("reserved", "reserved_at"), ("sold", "sold_at"), ("lost", "lost_at")],
)
def test_a_status_without_its_history_timestamp_is_rejected(status, missing):
    record = _deal_record()
    record["payload"]["deal_status"] = status
    record["payload"]["reserved_at"] = None
    record["payload"]["sold_at"] = None
    record["payload"]["lost_at"] = None
    problems = contract.validate(_envelope([record]), entity="deals")
    assert any(missing in p for p in problems)


def test_sold_before_reserved_is_rejected():
    record = _deal_record()
    record["payload"]["deal_status"] = "sold"
    record["payload"]["reserved_at"] = "2026-08-05T09:00:00+00:00"
    record["payload"]["sold_at"] = "2026-08-01T09:00:00+00:00"
    problems = contract.validate(_envelope([record]), entity="deals")
    assert any("sớm hơn reserved_at" in p for p in problems)


def test_an_empty_batch_is_rejected():
    """Hợp đồng cho phép `minItems: 0`; Mini CRM thì không GỬI lô rỗng — nó tiêu
    một batch id và tạo một sync_run trống ở phía nhận, không được gì."""
    assert contract.validate(_envelope([]), entity="units")


def test_a_batch_beyond_the_contract_ceiling_is_rejected():
    records = [_unit_record(external_id=f"U-{i:04d}") for i in range(contract.MAX_RECORDS_PER_BATCH + 1)]
    problems = contract.validate(_envelope(records), entity="units")
    assert any("vượt trần" in p or "maxItems" in p or "too long" in p for p in problems)


def test_a_duplicate_external_id_inside_one_batch_is_rejected():
    """Backend cũng chặn (`DUPLICATE_SOURCE_RECORD_ID`), nhưng lô sẽ bị từ chối
    cả cụm — chặn ở đây thì lỗi quy được về đúng bản ghi."""
    problems = contract.validate(_envelope([_unit_record(), _unit_record()]), entity="units")
    assert any("hai lần" in p for p in problems)


def test_assert_valid_raises_with_every_violation_listed():
    record = _unit_record()
    record["payload"]["unit_status"] = "con_trong"
    record["payload"]["price_vnd"] = 1
    with pytest.raises(contract.ContractViolationError) as exc:
        contract.assert_valid(_envelope([record]), entity="units")
    assert len(exc.value.violations) >= 2


def test_no_envelope_field_can_carry_customer_or_price_data():
    """Đọc THẲNG từ schema: tập trường của payload là đóng và không có chỗ nào
    để nhét PII vào."""
    schema = contract.load_schema()
    allowed = set(schema["$defs"]["unit_payload"]["properties"]) | set(schema["$defs"]["deal_payload"]["properties"])
    blob = json.dumps(sorted(allowed)).lower()
    for forbidden in ("price", "customer", "phone", "email", "commission", "salesperson", "contract"):
        assert forbidden not in blob


# --- Phase 5: phép canh phải THẬT SỰ bắt được trôi ---------------------------


def _hash_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_parity_check_detects_a_drifted_copy(tmp_path, monkeypatch):
    """KIỂM CHÍNH PHÉP KIỂM.

    Một test so hai file mà chưa bao giờ thấy chúng KHÁC nhau thì chưa chứng minh
    được nó phát hiện được sự khác nhau. Ở đây bản sao bị làm trôi thật — một ký
    tự — rồi khẳng định phép so gãy.

    Bản sao THẬT không bị đụng tới: bản đã sửa nằm trong `tmp_path`, và
    `contract.SCHEMA_PATH` được trỏ sang đó rồi trả lại. Sửa file thật rồi khôi
    phục sẽ để lại một cửa sổ mà một lần Ctrl-C đúng lúc làm hỏng hợp đồng.
    """
    if not BACKEND_SCHEMA.exists():
        pytest.skip(f"Không có {BACKEND_SCHEMA} — phép canh chỉ chạy ở repo")

    real_path = contract.SCHEMA_PATH
    original = real_path.read_text(encoding="utf-8")
    drifted = tmp_path / "crm_sync_v1.schema.json"
    # Đổi ĐÚNG một chỗ, và là một chỗ có ý nghĩa: nới `maxItems` của lô. Một thay
    # đổi kiểu này sẽ không làm test nào khác đỏ — nó chỉ khiến Mini CRM chấp nhận
    # gửi đi một lô mà backend từ chối. Đúng loại trôi mà phép canh sinh ra để bắt.
    drifted.write_text(original.replace('"maxItems": 5000', '"maxItems": 6000'), encoding="utf-8")
    assert drifted.read_text(encoding="utf-8") != original, "bản làm trôi phải KHÁC bản gốc"

    monkeypatch.setattr(contract, "SCHEMA_PATH", drifted)
    contract.load_schema.cache_clear()
    try:
        assert contract.schema_sha256() != _hash_of(BACKEND_SCHEMA), (
            "phép so SHA-256 KHÔNG phát hiện được bản sao đã trôi — phép canh vô dụng"
        )
        assert contract.load_schema()["properties"]["records"]["maxItems"] == 6000
    finally:
        # Gỡ bản vá NGAY tại đây, không đợi teardown: hai khẳng định cuối phải nói
        # về bản THẬT, mà `schema_sha256()` thì đọc `contract.SCHEMA_PATH`.
        monkeypatch.undo()
        contract.load_schema.cache_clear()

    # Và bản THẬT vẫn nguyên vẹn sau khi test chạy xong.
    assert real_path.read_text(encoding="utf-8") == original
    assert contract.schema_sha256() == _hash_of(BACKEND_SCHEMA)


def test_the_parity_check_is_not_satisfied_by_a_semantically_equal_reformat(tmp_path, monkeypatch):
    """Định dạng lại cũng phải bị bắt, và đó là chủ đích.

    Băm cây JSON đã parse sẽ coi hai file khác nhau về khoảng trắng là giống nhau.
    Nhưng lúc đó không ai còn biết bản nào là bản đã được duyệt, và lần sửa nội
    dung tiếp theo sẽ trôi vào giữa hai bản định dạng khác nhau mà không ai thấy.
    """
    if not BACKEND_SCHEMA.exists():
        pytest.skip(f"Không có {BACKEND_SCHEMA} — phép canh chỉ chạy ở repo")

    reformatted = tmp_path / "crm_sync_v1.schema.json"
    reformatted.write_text(json.dumps(contract.load_schema(), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(contract, "SCHEMA_PATH", reformatted)
    contract.load_schema.cache_clear()
    try:
        assert contract.schema_sha256() != _hash_of(BACKEND_SCHEMA)
    finally:
        contract.load_schema.cache_clear()


def test_the_two_sides_agree_that_a_valid_envelope_is_valid(tmp_path):
    """Kiểm HAI CHIỀU: cùng một phong bì đi qua CẢ HAI bộ kiểm.

    Mini CRM kiểm bằng bản sao của nó; backend kiểm bằng bản của nó. Chỉ kiểm một
    phía là chấp nhận lời khai của phía đó — và với payload hợp lệ thì đúng phía
    Mini CRM là phía không được phép tự chấm điểm mình.
    """
    if not BACKEND_SCHEMA.exists():
        pytest.skip(f"Không có {BACKEND_SCHEMA} — cần cả hai bản schema")

    from jsonschema import Draft202012Validator

    envelope = _envelope([_unit_record()])
    assert contract.validate(envelope, entity="units") == [], "phía Mini CRM từ chối một phong bì hợp lệ"

    backend_schema = json.loads(BACKEND_SCHEMA.read_text(encoding="utf-8"))
    backend_errors = list(Draft202012Validator(backend_schema).iter_errors(envelope))
    assert backend_errors == [], f"phía backend từ chối cùng phong bì đó: {[e.message for e in backend_errors]}"


@pytest.mark.parametrize(
    ("mutate", "label"),
    [
        (lambda e: e.update({"schema_version": 2}), "schema_version lạ"),
        (lambda e: e["records"][0].update({"operation": "merge"}), "operation lạ"),
        (lambda e: e["records"][0]["payload"].update({"price_vnd": 1}), "trường ngoài hợp đồng"),
        (lambda e: e.update({"sync_mode": "streaming"}), "sync_mode lạ"),
        (lambda e: e["records"][0]["payload"]["area_ref"].update({"area_id": "x"}), "area_ref lai hai dạng"),
    ],
)
def test_both_sides_reject_the_same_malformed_envelope(mutate, label):
    """Và chiều ngược lại: payload hỏng phải bị CẢ HAI phía từ chối.

    Nếu chỉ một phía bắt được, thì phía kia đang chạy trên một hợp đồng khác — và
    phép so SHA-256 sẽ không thấy gì, vì hai file vẫn giống nhau còn hành vi thì
    không.
    """
    if not BACKEND_SCHEMA.exists():
        pytest.skip(f"Không có {BACKEND_SCHEMA} — cần cả hai bản schema")

    from jsonschema import Draft202012Validator

    envelope = _envelope([_unit_record()])
    mutate(envelope)

    assert contract.validate(envelope, entity="units"), f"phía Mini CRM bỏ lọt: {label}"
    backend_schema = json.loads(BACKEND_SCHEMA.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(backend_schema).iter_errors(envelope)), f"phía backend bỏ lọt: {label}"
