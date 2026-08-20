"""Phase B — B3/B4/B5: Unit/Deal scoped theo phân cấp, và quy tắc cha-con.

Ba nhóm:

1. **Unit tham chiếu Area/Project** — cả hai đường tham chiếu
   (`external_area_id`, và `area_name`+`unit_type` kế thừa), từ chối tham chiếu
   sai, và di chuyển Area chỉ trong cùng một Project.
2. **Deal vẫn đúng như cũ** — B4 không đổi gì về CẤU TRÚC (Deal không mang tham
   chiếu Project/Area, phạm vi của nó SUY RA qua Unit theo định nghĩa), nên phần
   này CHỦ YẾU là hồi quy: xác nhận hành vi hiện có không bị Phase B ảnh hưởng.
3. **Xoá/lưu trữ cha-con** — không cascade, cha có con sống thì bị từ chối, kèm
   đủ thông tin để người gọi biết con nào đang chặn.

`backend` (FakeBackend) có mặt vì Unit/Deal VẪN đẩy sang backend qua outbox —
khác Project/Area (Phase B, chưa có đường đẩy nào).
"""

from __future__ import annotations

import sqlalchemy as sa
from app import relay as relay_module
from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_AUTH_HEADER, sync_url

PROJECT = {"name": "Khu do thi Ben Xanh", "launch_date": "2026-06-01"}


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


def _relay():
    import asyncio

    return asyncio.run(relay_module.relay_tick())


def _project(client) -> str:
    return client.post("/projects", json=PROJECT).json()["record"]["external_id"]


def _area(client, project_id: str, **overrides) -> dict:
    body = {
        "external_project_id": project_id,
        "area_name": "A1",
        "unit_type": "2PN",
        "bedrooms": 2,
        "area_sqm": 68.5,
        "total_units": 120,
    }
    body.update(overrides)
    return client.post("/areas", json=body).json()["record"]


def _rows(url, table):
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY created_at")).mappings()]
    finally:
        engine.dispose()


# --- 1. Unit tham chiếu Area/Project ----------------------------------------


def test_valid_project_and_area_are_accepted(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        response = client.post(
            "/units",
            json={"external_area_id": area["external_id"], "unit_code": "A1-01-01", "unit_status": "available"},
        )
    _relay()

    assert response.status_code == 201
    body = response.json()["record"]
    assert body["area_name"] == "A1"
    assert body["unit_type"] == "2PN"
    unit_envelope = next(envelope for envelope in backend.envelopes if envelope["records"][0]["entity"] == "unit")
    assert unit_envelope["schema_version"] == 2
    assert unit_envelope["project_ref"] == {"external_project_id": project_id}
    assert unit_envelope["records"][0]["payload"]["area_ref"] == {
        "external_area_id": area["external_id"]
    }


def test_missing_area_is_rejected(crm_app, backend):
    with _client() as client:
        response = client.post(
            "/units", json={"external_area_id": "A-9999", "unit_code": "X-01", "unit_status": "available"}
        )
    assert response.status_code == 422
    assert response.json()["error_code"] == "AREA_NOT_FOUND"
    assert backend.requests == [], "từ chối cục bộ — không request nào rời khỏi máy"


def test_creating_a_unit_leaves_no_row_when_the_area_is_missing(crm_app, backend):
    with _client() as client:
        client.post("/units", json={"external_area_id": "A-9999", "unit_code": "X-01", "unit_status": "available"})
    assert _rows(crm_app, "crm_units") == []


def test_legacy_area_name_and_unit_type_resolve_to_the_seeded_bootstrap_area(crm_app, backend):
    """Đường kế thừa (khớp `conftest.TEST_AREA_NAME`/`TEST_UNIT_TYPE`, seed sẵn
    bởi `_seed_bootstrap_hierarchy`) vẫn hoạt động — đây LÀ phép hồi quy chính
    của B3 cho toàn bộ `test_crud_units.py`."""
    from tests.conftest import TEST_AREA_NAME, TEST_UNIT_TYPE

    with _client() as client:
        response = client.post(
            "/units",
            json={"area_name": TEST_AREA_NAME, "unit_type": TEST_UNIT_TYPE, "unit_code": "B1-01-01", "unit_status": "available"},
        )
    assert response.status_code == 201
    rows = _rows(crm_app, "crm_units")
    assert rows[0]["area_id"] is not None, "đường kế thừa vẫn phải gắn area_id — không còn 'mồ côi' từ Phase B trở đi"


def test_legacy_area_name_with_no_local_match_is_rejected(crm_app, backend):
    with _client() as client:
        response = client.post(
            "/units", json={"area_name": "Không tồn tại", "unit_type": "1PN", "unit_code": "Z-01", "unit_status": "available"}
        )
    assert response.status_code == 422
    assert response.json()["error_code"] == "AREA_NOT_FOUND"


def test_legacy_area_name_matching_two_projects_is_ambiguous(crm_app, backend):
    """Hai dự án có phân khu CÙNG (area_name, unit_type) — khoá tự nhiên chỉ duy
    nhất TRONG một dự án (§A1.2), nên tra bằng tên KHÔNG xác định được ý người
    gọi. Từ chối thay vì đoán."""
    with _client() as client:
        p1 = _project(client)
        p2 = client.post("/projects", json={**PROJECT, "name": "Dự án khác"}).json()["record"]["external_id"]
        _area(client, p1, area_name="TRÙNG", unit_type="3PN")
        _area(client, p2, area_name="TRÙNG", unit_type="3PN")

        response = client.post(
            "/units", json={"area_name": "TRÙNG", "unit_type": "3PN", "unit_code": "Q-01", "unit_status": "available"}
        )
    assert response.status_code == 422
    assert response.json()["error_code"] == "AMBIGUOUS_AREA_REFERENCE"


def test_both_area_reference_shapes_at_once_is_rejected_by_the_schema(crm_app):
    with _client() as client:
        response = client.post(
            "/units",
            json={
                "external_area_id": "A-0001",
                "area_name": "A1",
                "unit_type": "2PN",
                "unit_code": "X-01",
                "unit_status": "available",
            },
        )
    assert response.status_code == 422


def test_creating_into_an_archived_area_is_rejected(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        client.delete(f"/areas/{area['external_id']}")
        response = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "X-01", "unit_status": "available"}
        )
    assert response.status_code == 409
    assert response.json()["error_code"] == "PARENT_ARCHIVED"


def test_moving_a_unit_within_the_same_project_is_accepted(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area_a = _area(client, project_id, area_name="A1")
        area_b = _area(client, project_id, area_name="A2")
        unit = client.post(
            "/units", json={"external_area_id": area_a["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]

        response = client.patch(f"/units/{unit['external_id']}", json={"external_area_id": area_b["external_id"]})

    assert response.status_code == 200
    body = response.json()["record"]
    assert body["area_name"] == "A2"
    assert body["unit_type"] == "2PN"
    assert body["source_revision"] == 2


def test_moving_a_unit_across_projects_is_rejected(crm_app, backend):
    with _client() as client:
        p1 = _project(client)
        p2 = client.post("/projects", json={**PROJECT, "name": "Dự án khác"}).json()["record"]["external_id"]
        area_p1 = _area(client, p1)
        area_p2 = _area(client, p2)
        unit = client.post(
            "/units", json={"external_area_id": area_p1["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]

        response = client.patch(f"/units/{unit['external_id']}", json={"external_area_id": area_p2["external_id"]})

    assert response.status_code == 409
    assert response.json()["error_code"] == "AREA_CROSS_PROJECT_MOVE"
    # Trạng thái CỤC BỘ không đổi sau một lần từ chối.
    unchanged = _client().__enter__().get(f"/units/{unit['external_id']}").json()
    assert unchanged["area_name"] == "A1"
    assert unchanged["source_revision"] == 1


def test_a_legacy_unit_with_no_prior_area_accepts_any_area_as_a_first_assignment(crm_app, backend):
    """Căn di sản (`area_id IS NULL`, tạo trước Phase B) không có 'dự án hiện
    tại' để so — gắn phân khu LẦN ĐẦU cho nó không phải là một cuộc di chuyển
    xuyên dự án, nó là một sự gán ban đầu."""
    engine = sa.create_engine(sync_url(crm_app))
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO crm_units (id, external_id, area_id, area_name, unit_type, unit_code, unit_status, "
                    "source_revision, created_at, updated_at) VALUES (gen_random_uuid(), 'LEGACY-1', NULL, "
                    "'Di sản', 'Cũ', 'LEG-01', 'available', 1, now(), now())"
                )
            )
    finally:
        engine.dispose()

    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        response = client.patch("/units/LEGACY-1", json={"external_area_id": area["external_id"]})

    assert response.status_code == 200
    body = response.json()["record"]
    assert body["area_name"] == "A1"


def test_updating_unrelated_fields_refreshes_the_denormalized_area_snapshot(crm_app, backend):
    """Không đổi phân khu, nhưng MỌI lần ghi vẫn làm tươi `area_name`/`unit_type`
    từ `crm_areas` — nếu phân khu bị đổi tên, lần ghi TIẾP THEO trên căn phải
    thấy tên mới, không phải bản sao đã cũ."""
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        unit = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]

        client.patch(f"/areas/{area['external_id']}", json={"area_name": "A1-doi-ten"})
        response = client.patch(f"/units/{unit['external_id']}", json={"unit_status": "reserved"})

    assert response.status_code == 200
    assert response.json()["record"]["area_name"] == "A1-doi-ten"


def test_existing_unit_regression_id_sequence_and_tombstone_still_work(crm_app, backend):
    """Hồi quy: mọi bảo đảm đã có (thứ tự id, không tái sử dụng sau tombstone,
    dấu mirrored) vẫn đúng dưới đường tạo MỚI (external_area_id)."""
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        first = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]
        assert first["external_id"] == "U-0001"
    _relay()
    with _client() as client:
        assert client.get(f"/units/{first['external_id']}").json()["mirrored_revision"] == 1

        client.delete(f"/units/{first['external_id']}")
        reborn_code_ok = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        )
    assert reborn_code_ok.status_code == 201
    assert reborn_code_ok.json()["record"]["external_id"] == "U-0002"


# --- 2. Deal — hồi quy có chủ đích -------------------------------------------


def test_deal_creation_is_unaffected_by_the_units_area_or_project(crm_app, backend):
    """B4: Deal không mang tham chiếu Project/Area — phạm vi của nó SUY RA qua
    Unit theo định nghĩa (phase_a_domain_freeze.md §A3.5). Không có gì để
    'cross-project' kiểm ở tầng Deal: Deal luôn đúng phạm vi của Unit nó trỏ
    tới, vì nó không có phạm vi nào khác để lệch sang."""
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        unit = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]
    _relay()
    with _client() as client:
        response = client.post("/deals", json={"external_unit_id": unit["external_id"], "deal_status": "lead"})

    assert response.status_code == 201


def test_deal_creation_still_rejects_a_missing_unit(crm_app, backend):
    with _client() as client:
        response = client.post("/deals", json={"external_unit_id": "U-9999", "deal_status": "lead"})
    assert response.status_code == 422
    assert response.json()["error_code"] == "UNIT_NOT_FOUND"


def test_deal_unit_reassignment_validates_the_project_chain(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        other_area = _area(client, project_id, area_name="A2", unit_type="3PN")
        other_project = _project(client)
        foreign_area = _area(client, other_project, area_name="B1", unit_type="2PN")
        unit = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]
        same_project_unit = client.post(
            "/units", json={"external_area_id": other_area["external_id"], "unit_code": "U-02", "unit_status": "available"}
        ).json()["record"]
        foreign_unit = client.post(
            "/units", json={"external_area_id": foreign_area["external_id"], "unit_code": "U-03", "unit_status": "available"}
        ).json()["record"]
    _relay()
    with _client() as client:
        deal = client.post("/deals", json={"external_unit_id": unit["external_id"], "deal_status": "lead"}).json()["record"]
        same_project = client.patch(
            f"/deals/{deal['external_id']}", json={"external_unit_id": same_project_unit["external_id"]}
        )
        foreign = client.patch(
            f"/deals/{deal['external_id']}", json={"external_unit_id": foreign_unit["external_id"]}
        )

    assert same_project.status_code == 200
    assert foreign.status_code == 409
    assert foreign.json()["error_code"] == "DEAL_PROJECT_MISMATCH"


# --- 3. Xoá/lưu trữ cha-con --------------------------------------------------


def test_project_with_a_live_area_cannot_be_archived(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        response = client.delete(f"/projects/{project_id}")

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "PARENT_HAS_LIVE_CHILDREN"
    assert body["detail"]["parent_entity"] == "project"
    assert body["detail"]["parent_external_id"] == project_id
    assert body["detail"]["child_entity"] == "area"
    assert area["external_id"] in body["detail"]["child_external_ids"]


def test_project_can_be_archived_once_its_area_is_archived(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        client.delete(f"/areas/{area['external_id']}")
        response = client.delete(f"/projects/{project_id}")

    assert response.status_code == 200
    assert response.json()["record"]["status"] == "archived"


def test_area_with_a_live_unit_cannot_be_archived(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        unit = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]

        response = client.delete(f"/areas/{area['external_id']}")

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "PARENT_HAS_LIVE_CHILDREN"
    assert body["detail"]["child_entity"] == "unit"
    assert unit["external_id"] in body["detail"]["child_external_ids"]


def test_area_can_be_archived_once_its_unit_is_deleted(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        unit = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]
        client.delete(f"/units/{unit['external_id']}")

        response = client.delete(f"/areas/{area['external_id']}")

    assert response.status_code == 200


def test_unit_with_a_live_deal_cannot_be_deleted(crm_app, backend):
    """D-3 (`phase_a_domain_freeze.md` §A1.8) — CHỐT nhưng gắn cờ cần chủ dự án
    xác nhận vì đây là THAY ĐỔI so với hành vi trước Phase B. Test này khẳng
    định QUY TẮC ĐÃ ĐÓNG BĂNG; nếu D-3 bị đảo ngược, test này là nơi cần sửa."""
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        unit = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]
    _relay()
    with _client() as client:
        deal = client.post("/deals", json={"external_unit_id": unit["external_id"], "deal_status": "lead"}).json()[
            "record"
        ]

        response = client.delete(f"/units/{unit['external_id']}")

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "PARENT_HAS_LIVE_CHILDREN"
    assert body["detail"]["parent_entity"] == "unit"
    assert body["detail"]["child_entity"] == "deal"
    assert deal["external_id"] in body["detail"]["child_external_ids"]


def test_unit_can_be_deleted_once_its_deal_is_deleted(crm_app, backend):
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        unit = client.post(
            "/units", json={"external_area_id": area["external_id"], "unit_code": "U-01", "unit_status": "available"}
        ).json()["record"]
    _relay()
    with _client() as client:
        deal = client.post("/deals", json={"external_unit_id": unit["external_id"], "deal_status": "lead"}).json()[
            "record"
        ]
        client.delete(f"/deals/{deal['external_id']}")

        response = client.delete(f"/units/{unit['external_id']}")

    assert response.status_code == 200


def test_archiving_a_child_never_touches_the_parents_state(crm_app, backend):
    """Không cascade THEO HƯỚNG NGƯỢC LẠI cũng đúng: archive một Area không được
    phép đổi bất cứ gì ở Project cha của nó."""
    with _client() as client:
        project_id = _project(client)
        area = _area(client, project_id)
        before = client.get(f"/projects/{project_id}").json()
        client.delete(f"/areas/{area['external_id']}")
        after = client.get(f"/projects/{project_id}").json()

    assert before == after, "archive con không được đổi source_revision hay bất kỳ trường nào của cha"
