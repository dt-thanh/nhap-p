"""Phép canh cho những gì Phase A đã ĐÓNG BĂNG.

**Mô hình sở hữu (đợt (g)): HỆ NGUỒN LÀ NGUỒN SỰ THẬT.** Mini CRM sở hữu cả bốn
tầng Project → Area → Unit → Deal; backend là bản sao CHỈ ĐỌC. Đây là bản sửa đổi
của mô hình trước (đợt (f): backend sở hữu Project/Area qua quy trình đề xuất–
duyệt) — xem `docs/crm/phase_a_domain_freeze.md` §S để đọc quyết định cũ.

Bốn nhóm, và nhóm thứ ba vẫn là nhóm dễ bị hiểu nhầm nhất:

1.  **v1 bất biến.** Ghim vào một giá trị SHA-256 TUYỆT ĐỐI — không đổi qua cả
    hai đợt sửa đổi v2, vì v1 không liên quan gì đến việc ai sở hữu Project/Area
    ở v2.

2.  **v2 đúng như đã đóng băng.** Schema hợp lệ, hai bản byte-identical, BỐN thực
    thể (kể cả `project`), `area_ref` CHỈ MỘT hình dạng, `area_payload` với năm
    trường bắt buộc CÓ THẨM QUYỀN (không còn tiền tố `proposed_`), phong bì hợp
    lệ/hỏng, hai phiên bản loại trừ lẫn nhau.

3.  **Ranh giới Phase A: runtime KHÔNG ĐỔI.** Runtime vẫn chỉ biết v1, vẫn chỉ
    biết `units`/`deals`, vẫn chưa có `project_scope`, và — MỚI ở đợt này — bốn
    đường ghi Project/Area của ingestion vẫn NGUYÊN VẸN,
    vì gỡ chúng là việc của Phase D, không phải Phase A.

    **Khi Phase D/E bắt đầu, chính những test ở nhóm 3 sẽ ĐỎ. Đó là tín hiệu
    ĐÚNG, không phải hồi quy.**

4.  **Ma trận phân quyền nhất quán** — và giờ khẳng định thêm: KHÔNG vai trò con
    người nào ghi được BẤT KỲ thực thể nghiệp vụ nào ở backend, kể cả admin, kể
    cả Project/Area.

Không có test runtime CRUD nào ở đây. Những gì cố tình chưa kiểm được liệt kê ở
`docs/crm/phase_a_domain_freeze.md` §A8.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_CONTRACTS = REPO_ROOT / "src" / "contracts"
MINICRM_CONTRACTS = REPO_ROOT / "minicrm" / "contracts"
FIXTURES = REPO_ROOT / "docs" / "crm" / "fixtures"
AUTHZ_MATRIX = REPO_ROOT / "docs" / "crm" / "authorization_matrix.json"

# Giá trị chốt ở Phase A, 2026-08-12, và KHÔNG ĐỔI qua bản sửa đổi (g) — v1 không
# liên quan tới việc ai sở hữu Project/Area ở v2. Sửa con số ở đây để làm test
# xanh là đúng thứ phép canh này sinh ra để ngăn.
V1_SHA256 = "e15fd9c5e685923fcf3f537c7dba4e900632ae7d6723df654e35b55efb49a92a"

# Giá trị của v2 SAU bản sửa đổi (g). ĐỔI so với đợt (f) — đúng như mong đợi, vì
# v2 còn là dự thảo, được phép sửa cho tới khi Phase D bật nó.
V2_SHA256 = "9620614a46536515fabeae1e9ba1e032c30deb02a74656e11818b1951fe10efb"

# Fixture v2 THUỘC đợt (g). Bộ cũ (18–26 của đợt (f), mô hình đề xuất–duyệt) đã bị
# XOÁ khỏi đĩa cùng lúc với việc thay thế mô hình sở hữu — không giữ song song hai
# bộ fixture cho hai mô hình đã bị một trong hai thay thế hoàn toàn.
V2_VALID_FIXTURES = [
    "18_v2_project_created",
    "19_v2_full_hierarchy_ordered",
    "20_v2_delete_reverse_order",
    "21_v2_partial_updates_each_tier",
    "22_v2_project_ref_by_backend_uuid",
]
V2_SCHEMA_INVALID_FIXTURES = [
    "23_v2_area_without_planning_fields",
    "24_v2_area_ref_by_name_removed_in_v2",
    "25_v2_project_payload_with_parent_ref",
    "26_v2_delete_carrying_payload",
    "27_v2_launch_date_with_timezone",
]
# Hợp lệ theo SCHEMA, sai theo quy tắc NGHIỆP VỤ — JSON Schema không diễn đạt được.
V2_BUSINESS_INVALID_FIXTURES = [
    "28_v2_child_before_parent",
    "29_v2_project_record_mismatches_envelope",
]


def _sha256(path: Path) -> str:
    content = path.read_bytes()
    normalized = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture(name: str) -> dict:
    return _load(FIXTURES / f"{name}.json")


@pytest.fixture(scope="module")
def v2_validator() -> Draft202012Validator:
    return Draft202012Validator(_load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json"), format_checker=FormatChecker())


@pytest.fixture(scope="module")
def v1_validator() -> Draft202012Validator:
    return Draft202012Validator(_load(BACKEND_CONTRACTS / "crm_sync_v1.schema.json"), format_checker=FormatChecker())


# --- 1. v1 bất biến ---------------------------------------------------------


def test_the_v1_contract_still_hashes_to_the_value_frozen_in_phase_a():
    assert _sha256(BACKEND_CONTRACTS / "crm_sync_v1.schema.json") == V1_SHA256, (
        "Hợp đồng v1 đã bị sửa. v1 là BẤT BIẾN và KHÔNG liên quan tới việc ai sở hữu "
        "Project/Area ở v2 — thay đổi cần thiết phải đi vào v2, không đi vào v1."
    )


def test_both_copies_of_v1_are_still_byte_identical():
    assert _sha256(MINICRM_CONTRACTS / "crm_sync_v1.schema.json") == V1_SHA256


def test_v1_still_declares_only_units_and_deals():
    """v2 (dù ở mô hình sở hữu nào) KHÔNG được phép rò ngược vào v1."""
    schema = _load(BACKEND_CONTRACTS / "crm_sync_v1.schema.json")
    assert schema["$defs"]["record"]["properties"]["entity"]["enum"] == ["unit", "deal"]
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "area_payload" not in schema["$defs"]
    assert "project_payload" not in schema["$defs"]


# --- 2. v2 đúng như đã đóng băng (mô hình: hệ nguồn sở hữu cả bốn tầng) -----


def test_the_v2_schema_is_a_valid_2020_12_schema():
    Draft202012Validator.check_schema(_load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json"))


def test_the_v2_schema_matches_the_hash_frozen_for_this_revision():
    assert _sha256(BACKEND_CONTRACTS / "crm_sync_v2.schema.json") == V2_SHA256, (
        "v2 đã bị sửa mà không cập nhật V2_SHA256 ở đây. Nếu đây là một sửa đổi có "
        "chủ đích, cập nhật hằng số này VÀ ghi lại lý do ở sync_contract_v2_draft.md."
    )


def test_both_copies_of_v2_are_byte_identical():
    """Cùng lý do với v1: Mini CRM có image riêng, `src/` không tồn tại trong đó."""
    assert _sha256(BACKEND_CONTRACTS / "crm_sync_v2.schema.json") == _sha256(
        MINICRM_CONTRACTS / "crm_sync_v2.schema.json"
    ), "Hai bản sao v2 đã lệch nhau. Chép bản src/ đè sang minicrm/; đừng sửa bản sao."


def test_v2_declares_all_four_hierarchy_tiers_as_sync_entities():
    """Khác biệt NỀN TẢNG so với v1: ở v2 cả bốn tầng đều đi qua đường đồng bộ,
    vì hệ nguồn sở hữu cả bốn — không chỉ unit/deal."""
    schema = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")
    assert schema["$defs"]["record"]["properties"]["entity"]["enum"] == [
        "project",
        "area",
        "unit",
        "deal",
    ]
    assert schema["properties"]["schema_version"]["const"] == 2


def test_v2_project_ref_prefers_the_external_id_shape():
    """Hình dạng CHUẨN đổi sang external_project_id: hệ nguồn không thể biết UUID
    nội bộ của một dự án chính nó vừa tạo. {project_id} giữ lại CHỈ để tương thích
    cài đặt đã cấu hình ánh xạ sẵn."""
    shapes = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")["$defs"]["project_ref"]["oneOf"]
    assert shapes[0]["required"] == ["external_project_id"]
    assert {tuple(s["required"]) for s in shapes} == {("external_project_id",), ("project_id",)}


def test_v2_area_ref_accepts_only_the_stable_external_id_shape():
    """Hai hình dạng của v1 ({area_id} UUID nội bộ; {area_name,unit_type} khoá tự
    nhiên) BỊ BỎ ở v2, không giữ song song. Lý do: {area_id} đòi hệ nguồn biết UUID
    nội bộ của phía nhận cho một thứ CHÍNH NÓ sở hữu; {area_name,unit_type} đứt
    ngay lần đổi tên đầu vì area_name giờ do hệ nguồn sở hữu và sửa được."""
    area_ref = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")["$defs"]["area_ref"]
    assert area_ref["required"] == ["external_area_id"]
    assert set(area_ref["properties"]) == {"external_area_id"}
    assert "oneOf" not in area_ref, "area_ref không còn nhiều hình dạng ở v2 — chỉ một"


def test_v2_project_payload_is_narrow_and_authoritative():
    """Chỉ hai trường, cả hai CÓ THẨM QUYỀN. Cột backend-local (headline,
    cover_image_url, absorption_calculator, …) KHÔNG nằm trong hợp đồng."""
    props = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")["$defs"]["project_payload"]["properties"]
    assert set(props) == {"name", "launch_date"}
    for backend_local in ("headline", "introduce", "cover_image_url", "absorption_calculator"):
        assert backend_local not in props


def test_v2_project_launch_date_is_a_calendar_date_not_a_timestamp():
    """Ngày mở bán là một sự kiện thương mại theo lịch địa phương, không phải một
    mốc có múi giờ — gắn offset sẽ khiến cùng một ngày hiển thị lệch nhau."""
    field = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")["$defs"]["project_payload"]["properties"][
        "launch_date"
    ]
    assert field["format"] == "date"


def test_v2_area_payload_requires_all_five_planning_fields_with_authority():
    """Khác biệt lớn nhất so với bản nháp trước: KHÔNG còn tiền tố `proposed_`,
    KHÔNG còn bước duyệt. Cả năm trường bắt buộc VÀ có thẩm quyền — phía nhận ghi
    thẳng vào bản sao."""
    payload = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")["$defs"]["area_payload"]
    assert set(payload["required"]) == {"area_name", "unit_type", "bedrooms", "area_sqm", "total_units"}
    props = payload["properties"]
    for proposed_name in ("proposed_total_units", "proposed_bedrooms", "proposed_area_sqm"):
        assert proposed_name not in props, "tiền tố proposed_ thuộc mô hình đề xuất–duyệt ĐÃ BỊ THAY THẾ"
    for authoritative_name in ("total_units", "bedrooms", "area_sqm"):
        assert authoritative_name in props


def test_v2_deal_payload_still_carries_no_project_or_area_reference():
    """Không đổi qua cả hai đợt: hai đường tới cùng một sự thật thì có ngày lệch
    nhau (phase_a_domain_freeze.md §A3.5)."""
    props = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")["$defs"]["deal_payload"]["properties"]
    assert "project_ref" not in props
    assert "area_ref" not in props
    assert "external_unit_id" in props


def test_v2_project_payload_has_no_parent_reference():
    """Project là gốc của phân cấp — không có cha để tham chiếu."""
    props = _load(BACKEND_CONTRACTS / "crm_sync_v2.schema.json")["$defs"]["project_payload"]["properties"]
    assert "project_ref" not in props
    assert "parent_ref" not in props


@pytest.mark.parametrize("fixture", V2_VALID_FIXTURES)
def test_a_frozen_valid_v2_envelope_passes_the_v2_schema(v2_validator, fixture):
    errors = [e.message for e in v2_validator.iter_errors(_fixture(fixture))]
    assert errors == [], f"{fixture} lẽ ra phải hợp lệ: {errors}"


@pytest.mark.parametrize("fixture", V2_SCHEMA_INVALID_FIXTURES)
def test_a_frozen_invalid_v2_envelope_is_rejected_by_the_v2_schema(v2_validator, fixture):
    assert list(v2_validator.iter_errors(_fixture(fixture))), f"{fixture} lẽ ra phải bị chặn ở tầng schema"


def test_record_ordering_is_contractual_but_not_expressible_in_json_schema(v2_validator):
    """Ranh giới quan trọng nhất của bộ test này.

    `28_v2_child_before_parent.json` HỢP LỆ theo schema nhưng SAI theo hợp đồng:
    căn đứng trước phân khu của nó. JSON Schema không diễn đạt được thứ tự phần
    tử theo `entity`, nên tầng nghiệp vụ của phía nhận phải bắt (Phase D).
    """
    envelope = _fixture("28_v2_child_before_parent")
    assert list(v2_validator.iter_errors(envelope)) == [], "fixture này phải hợp lệ VỀ SCHEMA"

    order = [r["entity"] for r in envelope["records"]]
    rank = {"project": 0, "area": 1, "unit": 2, "deal": 3}
    assert order != sorted(order, key=lambda e: rank[e]), "fixture này phải SAI thứ tự — đó là lý do nó tồn tại"


def test_project_record_identity_must_match_envelope_but_schema_cannot_express_that():
    """`29_v2_project_record_mismatches_envelope.json`: bản ghi `project` có
    `external_id` KHÁC `project_ref.external_project_id` của phong bì. Luật
    §A3.5 là luật nghiệp vụ; JSON Schema thuần không so được hai giá trị ở hai
    vị trí khác nhau trong cùng tài liệu."""
    envelope = _fixture("29_v2_project_record_mismatches_envelope")
    project_records = [r for r in envelope["records"] if r["entity"] == "project"]
    assert project_records, "fixture này phải chứa một bản ghi project"
    assert project_records[0]["external_id"] != envelope["project_ref"]["external_project_id"], (
        "fixture này phải KHÔNG khớp — đó là lý do nó tồn tại"
    )


def test_the_two_contract_versions_are_mutually_exclusive(v1_validator, v2_validator):
    """`schema_version` là `const`, nên nâng cấp không bao giờ xảy ra NGẦM."""
    v2_envelope = _fixture("18_v2_project_created")
    v1_envelope = _fixture("01_units_incremental")

    assert list(v2_validator.iter_errors(v2_envelope)) == []
    assert list(v1_validator.iter_errors(v1_envelope)) == []
    assert list(v1_validator.iter_errors(v2_envelope)), "phong bì v2 KHÔNG được hợp lệ theo schema v1"
    assert list(v2_validator.iter_errors(v1_envelope)), "phong bì v1 KHÔNG được hợp lệ theo schema v2"


def test_every_v2_fixture_uses_the_synthetic_naming_convention():
    """`docs/crm/fixtures/README.md` — tiền tố là thứ khiến một câu truy vấn duy
    nhất phân biệt được dữ liệu kiểm thử với dữ liệu vận hành."""
    for name in V2_VALID_FIXTURES + V2_SCHEMA_INVALID_FIXTURES + V2_BUSINESS_INVALID_FIXTURES:
        envelope = _fixture(name)
        assert envelope["source_instance_id"].startswith("synthetic-"), name
        assert envelope["external_batch_id"].startswith("SYNTH-BATCH-"), name
        for record in envelope["records"]:
            assert record["external_id"].startswith("SYNTH-"), f"{name}: {record['external_id']}"


def test_no_leftover_fixtures_from_the_superseded_proposal_approval_model():
    """Đợt (f) đóng băng một mô hình sở hữu KHÁC (backend sở hữu Project/Area,
    Mini CRM chỉ đề xuất). Đợt (g) thay thế nó — fixture của mô hình cũ (tên
    fixture chứa `_v2_area_proposal`, `_v2_mixed_tier_ordered`, hoặc bất kỳ
    fixture nào có `proposed_total_units`) không được sót lại trên đĩa, vì giữ
    song song hai bộ cho một quyết định đã bị thay thế sẽ khiến không ai biết bộ
    nào đang có hiệu lực."""
    stale_names = {"18_v2_area_proposal", "19_v2_mixed_tier_ordered", "21_v2_area_ref_by_stable_id"}
    on_disk = {p.stem for p in FIXTURES.glob("*.json")}
    assert not (stale_names & on_disk), f"fixture của mô hình đã bị thay thế còn sót: {stale_names & on_disk}"
    for path in FIXTURES.glob("*_v2_*.json"):
        assert "proposed_" not in path.read_text(encoding="utf-8"), (
            f"{path.name} mang tiền tố `proposed_` — thuộc mô hình đề xuất–duyệt ĐÃ BỊ THAY THẾ"
        )


# --- 3. Ranh giới Phase A: runtime KHÔNG ĐỔI --------------------------------


def test_the_runtime_now_accepts_v1_and_v2():
    """ĐÃ ĐỎ ĐÚNG NHƯ DỰ BÁO khi Phase D bắt đầu — cập nhật baseline, không xoá test.

    Phase D (`REQUIRED IN PHASE D — NOT IMPLEMENTED NOW` ở phase_a_domain_freeze.md
    §A6) bật v2 tường minh: `SUPPORTED_SCHEMA_VERSIONS` đổi từ `{1}` sang `{1, 2}`.
    v1 KHÔNG bị rút khỏi tập — hệ nguồn v1 tiếp tục hoạt động vô thời hạn dưới mô
    hình sở hữu cũ của nó, đúng như §6.2 của sync_contract_v2_draft.md đã hứa.
    """
    from src.services.json_payload import SUPPORTED_SCHEMA_VERSIONS

    assert SUPPORTED_SCHEMA_VERSIONS == frozenset({1, 2}), (
        "SUPPORTED_SCHEMA_VERSIONS lệch khỏi {1, 2} — nếu v1 bị rút khỏi tập, đó là "
        "một thay đổi phá vỡ (§6.3, cần entry pipeline_status.md riêng, không phải "
        "tác dụng phụ của việc khác)."
    )


def test_the_runtime_now_knows_all_four_hierarchy_entities():
    """ĐÃ ĐỎ ĐÚNG NHƯ DỰ BÁO khi Phase D bắt đầu — `projects`/`areas` giờ là thực
    thể đồng bộ RUNTIME, khớp với những gì hợp đồng đã đóng băng ở Phase A."""
    from src.services.json_payload import SUPPORTED_ENTITIES

    assert SUPPORTED_ENTITIES == frozenset({"units", "deals", "projects", "areas"})


def test_the_backend_validator_still_loads_the_v1_schema_only():
    """ĐỎ KHI PHASE D BẮT ĐẦU."""
    from src.services import contract_validation

    assert contract_validation.SCHEMA_PATH.name == "crm_sync_v1.schema.json"
    assert contract_validation.load_schema()["properties"]["schema_version"]["const"] == 1


def test_the_dashboard_principal_now_has_a_project_scope():
    """ĐÃ ĐỎ ĐÚNG NHƯ DỰ BÁO khi Phase E bắt đầu — cập nhật baseline, không xoá test.

    §A7.4 mục 1: `project_scope` được thêm vào `DashboardPrincipal`
    (`src/services/dashboard_auth.py`) — TĨNH, gắn vào token, mặc định tập
    RỖNG (fail-closed, không phải `ALL`).
    """
    from src.services.dashboard_auth import DashboardPrincipal

    assert set(DashboardPrincipal.__dataclass_fields__) == {"role", "project_scope"}
    assert DashboardPrincipal(role="business_viewer").project_scope == frozenset(), (
        "phạm vi mặc định phải RỖNG (fail-closed) khi không truyền tường minh"
    )


def test_the_legacy_backend_write_service_is_removed():
    """Project/Area writes are ingestion-owned; no legacy service may remain."""
    from pathlib import Path

    assert not Path("src/services/projects.py").exists()


# --- 4. Dữ liệu chính sách phân quyền nhất quán -----------------------------


def test_the_authorization_matrix_is_marked_not_implemented():
    matrix = _load(AUTHZ_MATRIX)
    assert matrix["status"] == "PROPOSED — NOT IMPLEMENTED"
    assert matrix["implemented_by"] == "Phase E"


def test_the_authorization_matrix_covers_every_frozen_role():
    from src.services.dashboard_auth import _ROLE_LEVEL

    matrix = _load(AUTHZ_MATRIX)
    assert set(matrix["roles"]) == set(_ROLE_LEVEL)
    for role, spec in matrix["roles"].items():
        assert spec["level"] == _ROLE_LEVEL[role], f"cấp của '{role}' lệch với runtime"


def test_every_matrix_row_declares_all_three_roles():
    matrix = _load(AUTHZ_MATRIX)
    roles = set(matrix["roles"])
    for row in matrix["matrix"]:
        missing = roles - set(row)
        assert not missing, f"hành động '{row['action']}' thiếu vai trò: {missing}"
        assert isinstance(row["project_scope_enforced"], bool)


def test_the_matrix_is_monotonic_in_role_level():
    """Vai trò cao hơn không bao giờ được ÍT quyền hơn vai trò thấp hơn."""
    matrix = _load(AUTHZ_MATRIX)
    order = sorted(matrix["roles"], key=lambda r: matrix["roles"][r]["level"])
    for row in matrix["matrix"]:
        granted = [row[r] for r in order]
        assert granted == sorted(granted), f"hành động '{row['action']}' không đơn điệu: {dict(zip(order, granted))}"


def test_no_human_role_may_write_any_business_entity():
    """MỞ RỘNG ở đợt (g): không chỉ Unit/Deal — giờ CẢ Project VÀ Area cũng
    không vai trò con người nào ghi được, kể cả admin. Backend là bản sao chỉ
    đọc cho cả bốn tầng; đường ghi duy nhất là tầng chiếu đồng bộ."""
    matrix = _load(AUTHZ_MATRIX)
    rows = {row["action"]: row for row in matrix["matrix"]}
    for action in ("create_update_project", "create_update_area", "create_update_unit", "create_update_deal"):
        assert not any(rows[action][role] for role in matrix["roles"]), (
            f"'{action}' được cấp cho một vai trò con người — backend không còn là bản sao chỉ đọc"
        )


def test_no_role_is_granted_cross_project_access():
    matrix = _load(AUTHZ_MATRIX)
    row = next(r for r in matrix["matrix"] if r["action"] == "cross_project_access")
    assert not any(row[role] for role in matrix["roles"])
    assert row["project_scope_enforced"] is True


def test_the_project_scope_policy_is_static_and_fails_closed():
    scope = _load(AUTHZ_MATRIX)["project_scope"]
    assert scope["kind"] == "static"
    assert scope["fail_closed"] is True
    assert scope["enforcement_layer"] == "query"
    assert scope["dynamic_deferred"]["decision"] == "HOÃN"


def test_out_of_scope_is_forbidden_not_not_found():
    codes = _load(AUTHZ_MATRIX)["error_semantics"]
    assert codes["out_of_project_scope"]["http"] == 403
    assert codes["auth_not_configured"]["http"] == 503


def test_mini_crm_write_authorization_is_flagged_as_a_new_open_risk():
    """MỚI ở đợt (g): Mini CRM giờ là hệ thống bản ghi có đường ghi từ FE, nhưng
    không có xác thực nào. Đây là DECISION REQUIRED và Phase A không cài đặt gì
    cho nó — test này canh rằng rủi ro được GHI LẠI, không bị lặng lẽ bỏ qua."""
    matrix = _load(AUTHZ_MATRIX)
    section = matrix["mini_crm_write_authorization"]
    assert section["status"] == "DECISION REQUIRED"
    assert section["not_implemented_in_phase_a"] is True
