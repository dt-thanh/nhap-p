"""CRUD phân khu, scoped theo dự án (Phase B).

Khác biệt lớn nhất so với mô hình đã bị thay thế (đợt (f), xem
`docs/crm/phase_a_domain_freeze.md` §S-2): KHÔNG có bước duyệt, KHÔNG có tiền
tố `proposed_`. Cả năm trường — kể cả ba trường kế hoạch `bedrooms`/`area_sqm`/
`total_units` — BẮT BUỘC và CÓ THẨM QUYỀN ngay từ request tạo.
"""

from __future__ import annotations

import sqlalchemy as sa
from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_AUTH_HEADER, sync_url

PROJECT = {"name": "Khu do thi Ben Xanh", "launch_date": "2026-06-01"}


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


def _create_project(client) -> str:
    return client.post("/projects", json=PROJECT).json()["record"]["external_id"]


def _area(external_project_id: str, **overrides) -> dict:
    body = {
        "external_project_id": external_project_id,
        "area_name": "A1",
        "unit_type": "2PN",
        "bedrooms": 2,
        "area_sqm": 68.5,
        "total_units": 120,
    }
    body.update(overrides)
    return body


# --- Tạo -----------------------------------------------------------------


def test_create_under_a_valid_project(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        response = client.post("/areas", json=_area(project_id))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"record"}
    record = body["record"]
    assert record["external_project_id"] == project_id
    assert record["area_name"] == "A1"
    assert record["bedrooms"] == 2
    assert record["area_sqm"] == 68.5
    assert record["total_units"] == 120
    assert record["status"] == "active"
    assert record["source_revision"] == 1


def test_planning_fields_are_authoritative_not_proposed(crm_app):
    """Chốt lại khác biệt so với mô hình đã bị thay thế: không `proposed_*`,
    không cần bước duyệt nào để `total_units` có hiệu lực."""
    with _client() as client:
        project_id = _create_project(client)
        record = client.post("/areas", json=_area(project_id, total_units=250)).json()["record"]
    assert record["total_units"] == 250
    assert record["status"] == "active", "không có trạng thái 'pending' nào cần đi qua"


def test_missing_project_is_rejected(crm_app):
    with _client() as client:
        response = client.post("/areas", json=_area("P-9999"))
    assert response.status_code == 422
    assert response.json()["error_code"] == "PROJECT_NOT_FOUND"

    rows = sa.create_engine(sync_url(crm_app))
    try:
        with rows.connect() as conn:
            count = conn.execute(sa.text("SELECT count(*) FROM crm_areas")).scalar_one()
    finally:
        rows.dispose()
    assert count == 1, "chỉ dòng seed BOOTSTRAP — không dòng mồ côi nào được ghi"


def test_creating_into_an_archived_project_is_rejected(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        client.delete(f"/projects/{project_id}")
        response = client.post("/areas", json=_area(project_id))
    assert response.status_code == 409
    assert response.json()["error_code"] == "PARENT_ARCHIVED"


def test_duplicate_natural_key_in_the_same_project_is_rejected(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        client.post("/areas", json=_area(project_id))
        response = client.post("/areas", json=_area(project_id))
    assert response.status_code == 409
    assert response.json()["error_code"] == "AREA_NATURAL_KEY_CONFLICT"


def test_the_same_natural_key_is_allowed_in_a_different_project(crm_app):
    """Khoá tự nhiên `(project_id, area_name, unit_type)` — scoped theo dự án,
    không toàn cục. Hai dự án khác nhau có phân khu cùng tên là hợp lệ."""
    with _client() as client:
        p1 = _create_project(client)
        p2 = client.post("/projects", json={**PROJECT, "name": "Dự án khác"}).json()["record"]["external_id"]
        r1 = client.post("/areas", json=_area(p1))
        r2 = client.post("/areas", json=_area(p2))
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["record"]["area_name"] == r2.json()["record"]["area_name"] == "A1"


def test_area_without_planning_fields_is_rejected_by_the_schema(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        body = _area(project_id)
        del body["total_units"]
        response = client.post("/areas", json=body)
    assert response.status_code == 422


# --- Liệt kê / đọc -----------------------------------------------------------


def test_list_scoped_by_project(crm_app):
    with _client() as client:
        p1 = _create_project(client)
        p2 = client.post("/projects", json={**PROJECT, "name": "Dự án khác"}).json()["record"]["external_id"]
        client.post("/areas", json=_area(p1, area_name="A1"))
        client.post("/areas", json=_area(p2, area_name="A2"))

        scoped = client.get("/areas", params={"external_project_id": p1}).json()

    assert [a["area_name"] for a in scoped] == ["A1"]


def test_reading_an_unknown_area_returns_404(crm_app):
    with _client() as client:
        assert client.get("/areas/A-9999").status_code == 404


# --- Sửa ---------------------------------------------------------------------


def test_update_increments_the_revision(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        created = client.post("/areas", json=_area(project_id)).json()["record"]
        response = client.patch(f"/areas/{created['external_id']}", json={"total_units": 130})

    assert response.status_code == 200
    body = response.json()["record"]
    assert body["source_revision"] == 2
    assert body["total_units"] == 130
    assert body["area_name"] == "A1", "trường không gửi giữ nguyên"


def test_external_project_id_cannot_be_changed_via_patch(crm_app):
    """Bất biến bằng cách VẮNG MẶT ở `AreaPatch` — phase_a_domain_freeze.md §A1.6:
    phân khu không đổi dự án. Gửi trường này trong PATCH phải bị schema từ chối
    (trường lạ), không phải bị âm thầm bỏ qua."""
    with _client() as client:
        project_id = _create_project(client)
        created = client.post("/areas", json=_area(project_id)).json()["record"]
        response = client.patch(
            f"/areas/{created['external_id']}", json={"external_project_id": "P-9999", "total_units": 1}
        )
    assert response.status_code == 422


def test_renaming_into_a_conflicting_natural_key_is_rejected(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        client.post("/areas", json=_area(project_id, area_name="A1"))
        a2 = client.post("/areas", json=_area(project_id, area_name="A2")).json()["record"]
        response = client.patch(f"/areas/{a2['external_id']}", json={"area_name": "A1"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "AREA_NATURAL_KEY_CONFLICT"


# --- Lưu trữ / cha-con ---------------------------------------------------


def test_archive_sets_status_and_does_not_physically_delete(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        created = client.post("/areas", json=_area(project_id)).json()["record"]
        response = client.delete(f"/areas/{created['external_id']}")

    assert response.status_code == 200
    assert response.json()["record"]["status"] == "archived"


def test_archiving_twice_is_rejected(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        created = client.post("/areas", json=_area(project_id)).json()["record"]
        client.delete(f"/areas/{created['external_id']}")
        response = client.delete(f"/areas/{created['external_id']}")
    assert response.status_code == 409
    assert response.json()["error_code"] == "ALREADY_ARCHIVED"


def test_patching_an_archived_area_is_rejected(crm_app):
    with _client() as client:
        project_id = _create_project(client)
        created = client.post("/areas", json=_area(project_id)).json()["record"]
        client.delete(f"/areas/{created['external_id']}")
        response = client.patch(f"/areas/{created['external_id']}", json={"total_units": 1})
    assert response.status_code == 409
    assert response.json()["error_code"] == "RECORD_ARCHIVED"
