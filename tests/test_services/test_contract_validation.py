"""Kiểm hợp đồng theo JSON Schema, và trạng thái thật của 12 fixture tổng hợp.

Bộ test này canh HAI thứ, và cả hai đều dễ mục ruỗng nếu không có test:

1. **Bộ kiểm hoạt động đúng** — bắt được sai hình dạng, và KHÔNG bắt nhầm những
   thứ thuộc tầng nghiệp vụ.
2. **Từng fixture nằm đúng phía nó phải nằm.** Fixture đường tốt phải hợp lệ;
   fixture cố ý sai phải sai ĐÚNG chỗ đã định. Không có test này thì một fixture
   âm thầm trở nên hợp lệ (hoặc bất hợp lệ) mà không ai biết, và kịch bản nó đại
   diện coi như biến mất khỏi bộ kiểm thử.

Không cần database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.contract_validation import (
    ContractSchemaUnavailableError,
    ContractValidator,
    load_schema,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "docs" / "crm" / "fixtures"

# Fixture đúng hợp đồng ở mức HÌNH DẠNG. Vài cái trong số này cố ý sai ở mức
# NGHIỆP VỤ (phân khu không có, trạng thái lạ, giao dịch trước căn) — và đó chính
# là điều cần khẳng định: bộ kiểm hình dạng không được lấn sang việc của nghiệp vụ.
SCHEMA_VALID_FIXTURES = [
    "01_units_incremental",
    "02_deals_incremental",
    "03_replay_same_batch",
    "04_stale_update",
    "05_same_version_conflict",
    "06_explicit_delete",
    "07_snapshot_complete",
    "08_snapshot_incomplete",
    "09_deal_before_unit",
    "10_unknown_area",
    "11_unknown_status",
    # Phase 8B — chốt A4. Cả bốn ĐÚNG hình dạng: điều chúng kiểm nằm ở tầng
    # nghiệp vụ (đánh rơi lịch sử, xoá tường minh, hợp nhất partial), nên cổng
    # hình dạng phải cho chúng đi qua.
    "13_deal_history_preserved",
    "14_deal_history_dropped",
    "15_deal_history_cleared",
    "16_deal_partial_update",
    "17_partial_without_base",
]

# Fixture cố ý sai ngay ở mức hình dạng.
SCHEMA_INVALID_FIXTURES = ["12_naive_timestamp"]


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _fixture_stems() -> set[str]:
    return {path.stem for path in FIXTURE_DIR.glob("*.json")}


def _fixtures_with_schema_version(version: int) -> set[str]:
    """Phân vùng theo NỘI DUNG file, không theo tên file.

    Phân vùng theo tên (ví dụ tiền tố `_v2_`) sẽ sai ngay lần đầu ai đó đặt tên
    khác quy ước, và cái sai đó lại im lặng — đúng thứ hai phép canh này chặn.
    """
    return {name for name in _fixture_stems() if _fixture(name).get("schema_version") == version}


def _fixture_names_referenced_by(module_path: Path) -> str:
    return module_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def validator() -> ContractValidator:
    return ContractValidator()


def test_schema_file_loads_and_is_draft_2020_12():
    schema = load_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == 1


def test_schema_lives_inside_src_so_it_ships_with_the_image():
    """Schema phải nằm trong `src/`, không phải `docs/`.

    `.dockerignore` loại `docs/` khỏi image. Để schema ở đó thì runtime không nạp
    được và MỌI lô đồng bộ trả 500 — đây là lỗi đã thực sự xảy ra khi chạy thử
    trình mô phỏng lần đầu ở Phase 3, chứ không phải mối lo giả định.
    """
    from src.services.contract_validation import SCHEMA_PATH

    repo_root = Path(__file__).resolve().parents[2]
    assert SCHEMA_PATH.exists(), f"không tìm thấy schema tại {SCHEMA_PATH}"
    assert SCHEMA_PATH.is_relative_to(repo_root / "src"), (
        f"schema nằm ngoài src/ ({SCHEMA_PATH}) — nó sẽ không được đóng gói vào image"
    )


def test_dockerignore_does_not_exclude_the_schema_directory():
    """Chốt lại vế còn lại: `src/contracts/` không được lọt vào .dockerignore."""
    repo_root = Path(__file__).resolve().parents[2]
    patterns = [
        line.strip()
        for line in (repo_root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for pattern in patterns:
        assert not pattern.rstrip("/").endswith("src"), f".dockerignore loại mất src/: {pattern!r}"
        assert "contracts" not in pattern, f".dockerignore loại mất thư mục schema: {pattern!r}"


def test_every_v1_fixture_on_disk_is_covered_by_this_test_module():
    """Thêm fixture mà quên khai ở đây thì test này đỏ.

    Không có nó, một fixture mới sẽ không được bộ kiểm nào ngó tới và sự vắng mặt
    đó im lặng tuyệt đối.

    **Phase A — phép canh được PHÂN VÙNG THEO PHIÊN BẢN, không bị nới lỏng.**
    Module này là bộ kiểm của hợp đồng **v1**, và nó chỉ nhận trách nhiệm với
    fixture v1. Fixture v2 (`schema_version: 2`) thuộc về
    `test_phase_a_contract_freeze.py`, và test kế bên khẳng định chúng THẬT SỰ
    được module đó phủ. Nhét fixture v2 vào `SCHEMA_INVALID_FIXTURES` sẽ xanh
    nhưng nói sai: chúng không phải phong bì hỏng, chúng là phong bì của một
    phiên bản khác — và hai phiên bản LOẠI TRỪ LẪN NHAU theo thiết kế.

    Bảo đảm tổng thể KHÔNG đổi: mọi fixture trên đĩa vẫn phải được đúng một bộ
    kiểm nhận trách nhiệm.
    """
    declared = set(SCHEMA_VALID_FIXTURES) | set(SCHEMA_INVALID_FIXTURES)
    assert _fixtures_with_schema_version(1) == declared, (
        f"fixture v1 chưa khai: {sorted(_fixtures_with_schema_version(1) - declared)}"
    )


def test_every_non_v1_fixture_on_disk_is_covered_by_the_phase_a_freeze_module():
    """Vế còn lại của phép canh: không fixture nào rơi vào khoảng trống giữa hai
    module.

    Đọc DANH SÁCH THẬT trong module kia thay vì chép tay lại — chép tay thì hai
    danh sách sẽ lệch nhau, đúng loại hỏng mà cả hai test này sinh ra để chặn.
    """
    covered = _fixture_names_referenced_by(
        Path(__file__).resolve().parent / "test_phase_a_contract_freeze.py"
    )
    for name in sorted(set(_fixture_stems()) - _fixtures_with_schema_version(1)):
        assert name in covered, f"fixture '{name}' không được bộ kiểm nào nhận trách nhiệm"


@pytest.mark.parametrize("name", SCHEMA_VALID_FIXTURES)
def test_fixture_matches_the_contract_shape(validator, name):
    violations = validator.validate(_fixture(name))
    assert violations == [], f"{name} lẽ ra đúng hình dạng: {[v.message for v in violations]}"


@pytest.mark.parametrize("name", SCHEMA_INVALID_FIXTURES)
def test_fixture_violates_the_contract_shape(validator, name):
    assert validator.validate(_fixture(name)), f"{name} lẽ ra phải sai hình dạng"


def test_naive_timestamp_is_rejected_at_the_schema_layer(validator):
    """Timestamp thiếu offset múi giờ bị chặn ngay từ hình dạng.

    Đoán múi giờ hộ hệ nguồn có thể đẩy một giao dịch sang ngày khác, mà ngày là
    khoá phân nhóm của toàn bộ chuỗi hấp thụ.
    """
    violations = validator.validate(_fixture("12_naive_timestamp"))
    paths = {v.json_path for v in violations}
    assert "$.records[0].source_updated_at" in paths


@pytest.mark.parametrize("name", ["09_deal_before_unit", "10_unknown_area", "11_unknown_status"])
def test_business_errors_are_not_caught_by_the_shape_layer(validator, name):
    """Sai nghiệp vụ phải ĐI QUA được cổng hình dạng.

    Hai loại lỗi cần hai câu trả lời khác nhau: sai hình dạng là lỗi của người
    TÍCH HỢP, sai nghiệp vụ là lỗi của DỮ LIỆU. Gộp lại thì người nhận báo lỗi
    không biết phải sửa ở đâu.
    """
    assert validator.validate(_fixture(name)) == []


# --- Hành vi của bộ kiểm ----------------------------------------------------


def test_missing_required_envelope_field_is_reported_with_its_path(validator):
    payload = _fixture("01_units_incremental")
    del payload["external_batch_id"]

    violations = validator.validate(payload)

    assert violations
    assert any(v.error_code == "SCHEMA_REQUIRED" for v in violations)


def test_unknown_envelope_field_is_rejected(validator):
    """`additionalProperties: false` — trường lạ là dấu hiệu adapter hiểu sai hợp
    đồng, không phải thứ nên bỏ qua im lặng."""
    payload = _fixture("01_units_incremental")
    payload["khong_co_truong_nay"] = 1

    assert any(v.error_code == "SCHEMA_ADDITIONALPROPERTIES" for v in validator.validate(payload))


def test_record_without_any_version_is_rejected(validator):
    """Bản ghi không mang phiên bản không xếp thứ tự được — hợp đồng mục 5."""
    payload = _fixture("01_units_incremental")
    payload["records"] = [
        {
            "entity": "unit",
            "operation": "upsert",
            "external_id": "SYNTH-U-9999",
            "payload": {
                "area_ref": {"area_name": "A1", "unit_type": "2PN"},
                "unit_code": "A1-09-09",
                "unit_status": "available",
            },
        }
    ]

    assert validator.validate(payload), "bản ghi không có source_revision lẫn source_updated_at phải bị từ chối"


def test_delete_record_must_also_carry_a_version(validator):
    """Lệnh xoá cũng phải mang phiên bản.

    Không có phiên bản thì không phân biệt được "xoá mới" với "lệnh xoá cũ đến
    muộn", và áp nhầm lệnh cũ lên bản ghi vừa tạo lại là mất dữ liệu im lặng.
    """
    payload = _fixture("06_explicit_delete")
    del payload["records"][0]["source_revision"]

    assert validator.validate(payload)


def test_delete_record_must_not_carry_a_payload(validator):
    payload = _fixture("06_explicit_delete")
    payload["records"][0]["payload"] = {"external_unit_id": "SYNTH-U-0002", "deal_status": "lost"}

    assert validator.validate(payload)


def test_snapshot_metadata_is_required_for_full_snapshot(validator):
    payload = _fixture("07_snapshot_complete")
    del payload["snapshot"]

    assert validator.validate(payload)


def test_snapshot_metadata_is_forbidden_for_incremental(validator):
    """Ảnh chụp kèm lô tăng dần là mâu thuẫn: một bên suy ra xoá, một bên không."""
    payload = _fixture("01_units_incremental")
    payload["snapshot"] = {
        "snapshot_id": "SYNTH-SNAP-X",
        "chunk_index": 0,
        "chunk_total": 1,
        "snapshot_complete": True,
        "scope": {"entities": ["unit"]},
    }

    assert validator.validate(payload)


def test_unknown_schema_version_is_rejected(validator):
    payload = _fixture("01_units_incremental")
    payload["schema_version"] = 99

    assert any(v.error_code == "SCHEMA_CONST" for v in validator.validate(payload))


def test_violations_are_ordered_deterministically(validator):
    """Cùng payload hỏng phải cho ra cùng thứ tự lỗi ở mọi lần chạy.

    `jsonschema` không đảm bảo thứ tự; không sắp thì test dựa vào lỗi đầu tiên sẽ
    chớp tắt và bị coi là "test hay hỏng vặt" rồi bị tắt đi.
    """
    payload = _fixture("01_units_incremental")
    del payload["external_batch_id"]
    payload["schema_version"] = 99

    runs = [[(v.json_path, v.message) for v in validator.validate(payload)] for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_comment_field_is_allowed_so_fixtures_can_label_themselves(validator):
    """Nhãn "đây là dữ liệu tổng hợp" phải nằm TRONG payload.

    Nhãn để ngoài file sẽ có ngày rời khỏi file.
    """
    payload = _fixture("01_units_incremental")
    assert "_comment" in payload
    assert validator.validate(payload) == []


def test_missing_schema_file_is_a_system_error_not_a_payload_error(tmp_path, monkeypatch):
    """Thiếu file schema là lỗi CẤU HÌNH, không phải lỗi của người gửi."""
    import src.services.contract_validation as module

    load_schema.cache_clear()
    monkeypatch.setattr(module, "SCHEMA_PATH", tmp_path / "khong-co.json")
    try:
        with pytest.raises(ContractSchemaUnavailableError):
            module.load_schema()
    finally:
        load_schema.cache_clear()


# --- Trình mô phỏng cục bộ --------------------------------------------------


def test_simulator_refuses_to_send_a_non_synthetic_payload(tmp_path, monkeypatch):
    """Chốt an toàn: chỉ gửi được payload đã dán nhãn tổng hợp.

    Nếu ai đó thả một payload thật vào thư mục fixture, công cụ phải từ chối chứ
    không lặng lẽ gửi nó tới endpoint.
    """
    import json as json_module

    import scripts.sync_simulator as simulator

    payload = _fixture("01_units_incremental")
    payload["source_instance_id"] = "mini-crm-prod"  # KHÔNG có tiền tố synthetic-
    (tmp_path / "that.json").write_text(json_module.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(simulator, "FIXTURE_DIR", tmp_path)

    with pytest.raises(SystemExit) as exc:
        simulator.cmd_send("that", "http://localhost:8000", "afsk_bat-ky", "units")

    assert "synthetic-" in str(exc.value)


def test_simulator_lists_every_fixture(capsys):
    import scripts.sync_simulator as simulator

    assert simulator.cmd_list() == 0
    printed = capsys.readouterr().out
    for name in SCHEMA_VALID_FIXTURES + SCHEMA_INVALID_FIXTURES:
        assert name in printed


def test_simulator_banner_marks_output_as_synthetic(capsys):
    """Mọi đầu ra của công cụ phải tự nói nó không phải dữ liệu thật."""
    import scripts.sync_simulator as simulator

    simulator.cmd_list()
    printed = capsys.readouterr().out

    assert "KHÔNG PHẢI CRM THẬT" in printed
    assert "KHÔNG PHẢI NGUỒN SỰ THẬT NGHIỆP VỤ" in printed


def test_every_fixture_points_at_the_synthetic_project():
    """Fixture chỉ được trỏ vào dự án tổng hợp, không vào dự án thật nào.

    Danh tính dự án là hạng mục CHẶN trước kích hoạt (hợp đồng mục 3.1); trong
    lúc chờ, fixture phải neo vào đúng một UUID tổng hợp đã biết.
    """
    import scripts.sync_simulator as simulator

    for name in SCHEMA_VALID_FIXTURES + SCHEMA_INVALID_FIXTURES:
        ref = _fixture(name)["project_ref"]
        assert ref == {"project_id": simulator.SYNTHETIC_PROJECT_ID}, f"{name} trỏ sai dự án: {ref}"


# --- Phase 8B: khai báo độ đầy đủ của payload --------------------------------


def _deal_record(payload: dict, **extra) -> dict:
    return {
        "entity": "deal",
        "operation": "upsert",
        "external_id": "SYNTH-D-0001",
        "source_revision": 9,
        "payload": payload,
        **extra,
    }


def _envelope(record: dict) -> dict:
    base = _fixture("02_deals_incremental")
    return {**base, "records": [record]}


def test_explicit_null_timestamp_is_allowed_by_the_shape_layer(validator):
    """Hợp đồng phải CHỞ ĐƯỢC null tường minh.

    Không có nó thì cách duy nhất để hệ nguồn xoá một mốc là bỏ hẳn khoá đó — tức
    là đúng thứ chốt A4 sắp từ chối. Cấm cả hai đường là biến lịch sử thành bất
    biến và đẩy hệ nguồn sang xoá-rồi-tạo-lại.
    """
    envelope = _envelope(
        _deal_record(
            {
                "external_unit_id": "SYNTH-U-0002",
                "deal_status": "lost",
                "reserved_at": None,
                "lost_at": "2026-08-09T08:00:00+07:00",
            }
        )
    )

    assert validator.validate(envelope) == []


def test_a_full_record_still_requires_its_core_fields(validator):
    envelope = _envelope(_deal_record({"external_unit_id": "SYNTH-U-0002"}))

    codes = {v.error_code for v in validator.validate(envelope)}
    assert codes, "bản ghi full thiếu deal_status phải sai hình dạng"


def test_a_partial_record_may_omit_the_core_fields(validator):
    """Ở partial, trường vắng mặt nghĩa là GIỮ NGUYÊN — đòi hỏi nó có mặt là mâu
    thuẫn với chính ngữ nghĩa partial."""
    envelope = _envelope(_deal_record({"sold_at": "2026-08-09T09:00:00+07:00"}, payload_completeness="partial"))

    assert validator.validate(envelope) == []


def test_an_empty_partial_payload_is_rejected(validator):
    """Một bản ghi partial rỗng không nói gì cả; nhận nó là nhận một lệnh ghi không
    có nội dung."""
    envelope = _envelope(_deal_record({}, payload_completeness="partial"))

    assert validator.validate(envelope)


def test_an_unknown_completeness_value_is_rejected_by_the_shape_layer(validator):
    envelope = _envelope(
        _deal_record(
            {"external_unit_id": "SYNTH-U-0002", "deal_status": "reserved", "reserved_at": "2026-08-01T09:30:00+07:00"},
            payload_completeness="mostly",
        )
    )

    assert validator.validate(envelope)


def test_omitting_completeness_keeps_the_strict_full_shape(validator):
    """Mặc định phải là 'full': mặc định 'partial' sẽ âm thầm biến mọi payload cũ
    thành 'giữ nguyên hết'."""
    envelope = _envelope(_deal_record({"sold_at": "2026-08-09T09:00:00+07:00"}))

    assert validator.validate(envelope), "không khai completeness thì phải bị soi như bản ghi full"


def test_a_partial_unit_payload_is_accepted(validator):
    base = _fixture("01_units_incremental")
    envelope = {
        **base,
        "records": [
            {
                "entity": "unit",
                "operation": "upsert",
                "external_id": "SYNTH-U-0002",
                "source_revision": 9,
                "payload_completeness": "partial",
                "payload": {"unit_status": "sold"},
            }
        ],
    }

    assert validator.validate(envelope) == []


def test_completeness_survives_the_v1_adapter():
    """Khai báo phải đi cùng bản ghi xuống tầng dưới.

    Rơi mất ở adapter thì mọi bản ghi partial lặng lẽ bị soi như bản ghi full, và
    chốt A4 sẽ từ chối chính những bản ghi hợp lệ.
    """
    from src.services.contract_adapter import adapt

    adapted = adapt(_fixture("16_deal_partial_update"), entity_from_route="deals")

    assert adapted["records"][0]["payload_completeness"] == "partial"


def test_the_adapter_leaves_completeness_out_when_the_source_says_nothing():
    """Không khai thì không được đặt hộ: tầng dưới phải phân biệt được 'khai full'
    với 'không khai gì'."""
    from src.services.contract_adapter import adapt

    adapted = adapt(_fixture("02_deals_incremental"), entity_from_route="deals")

    assert "payload_completeness" not in adapted["records"][0]


def test_the_adapter_preserves_an_explicit_null_timestamp():
    """Nếu adapter bỏ khoá null đi thì 'xoá tường minh' biến thành 'vắng mặt', và
    hai ý định trái ngược nhau gộp làm một."""
    from src.services.contract_adapter import adapt

    adapted = adapt(_fixture("15_deal_history_cleared"), entity_from_route="deals")
    data = adapted["records"][0]["data"]

    assert "reserved_at" in data
    assert data["reserved_at"] is None
