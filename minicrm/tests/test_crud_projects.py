"""CRUD dự án (Phase B — Mini CRM là TÁC GIẢ, không chỉ người tham chiếu).

KHÔNG có `sync` trong phản hồi: Phase B không tạo dòng outbox nào cho Project —
đó là việc của Phase C. Những test ở đây chỉ chứng minh phía CỤC BỘ: bản ghi được
ghi đúng, phiên bản tăng đúng, `external_id` bất biến và không tái sử dụng, và
quy tắc lưu trữ/cha-con.

`backend` (FakeBackend) không cần ở đây vì không có gì được đẩy đi.
"""

from __future__ import annotations

import sqlalchemy as sa
from app.main import app
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_AUTH_HEADER, sync_url

PROJECT = {"name": "Khu do thi Ben Xanh", "launch_date": "2026-06-01"}


def _client():
    return TestClient(app, headers=ADMIN_AUTH_HEADER)


def _rows(url, table):
    engine = sa.create_engine(sync_url(url))
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(sa.text(f"SELECT * FROM {table} ORDER BY created_at")).mappings()]
    finally:
        engine.dispose()


# --- Tạo -----------------------------------------------------------------


def test_create_writes_the_local_row(crm_app):
    with _client() as client:
        response = client.post("/projects", json=PROJECT)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"record"}, "Project KHÔNG có `sync` — Phase B không tạo outbox"
    assert body["record"]["name"] == "Khu do thi Ben Xanh"
    assert body["record"]["status"] == "active"
    assert body["record"]["source_revision"] == 1
    assert body["record"]["external_id"]
    assert body["record"]["location"] is None

    rows = _rows(crm_app, "crm_projects")
    # Dòng seed BOOTSTRAP (conftest) + dòng vừa tạo.
    assert len(rows) == 2


def test_create_and_update_expose_optional_location(crm_app):
    with _client() as client:
        created = client.post(
            "/projects",
            json={**PROJECT, "location": "  Xã Long Hưng, Văn Giang, Hưng Yên  "},
        ).json()["record"]
        assert created["location"] == "Xã Long Hưng, Văn Giang, Hưng Yên"

        updated = client.patch(
            f"/projects/{created['external_id']}",
            json={"location": "Đường Đại Dương, Văn Giang, Hưng Yên"},
        ).json()["record"]

    assert updated["location"] == "Đường Đại Dương, Văn Giang, Hưng Yên"


def test_location_is_optional_and_round_trips_through_create_update_read(crm_app):
    with _client() as client:
        created = client.post(
            "/projects",
            json={**PROJECT, "location": "Phường Tây Mỗ, Nam Từ Liêm, Hà Nội"},
        ).json()["record"]
        assert created["location"] == "Phường Tây Mỗ, Nam Từ Liêm, Hà Nội"

        updated = client.patch(
            f"/projects/{created['external_id']}",
            json={"location": "Đường Đại lộ Thăng Long, Phường Mễ Trì, Hà Nội"},
        ).json()["record"]
        fetched = client.get(f"/projects/{created['external_id']}").json()

    assert updated["location"] == "Đường Đại lộ Thăng Long, Phường Mễ Trì, Hà Nội"
    assert fetched["location"] == updated["location"]
    assert updated["source_revision"] == 2


def test_location_may_be_null_for_legacy_compatible_create(crm_app):
    with _client() as client:
        record = client.post("/projects", json=PROJECT).json()["record"]
    assert record["location"] is None


def test_external_ids_come_from_a_sequence_and_are_zero_padded(crm_app):
    with _client() as client:
        first = client.post("/projects", json=PROJECT).json()["record"]["external_id"]
        second = client.post("/projects", json={**PROJECT, "name": "Dự án 2"}).json()["record"]["external_id"]
    assert (first, second) == ("P-0001", "P-0002")


def test_unknown_fields_are_rejected(crm_app):
    with _client() as client:
        response = client.post("/projects", json={**PROJECT, "absorption_calculator": "domain_units_deals"})
    assert response.status_code == 422, (
        "`absorption_calculator` là cột BACKEND-LOCAL (phase_a_domain_freeze.md §A0.1) — "
        "hợp đồng của Mini CRM không chở nó, và schema phải từ chối, không im lặng bỏ qua."
    )


def test_missing_required_field_is_rejected(crm_app):
    with _client() as client:
        response = client.post("/projects", json={"name": "Thiếu ngày mở bán"})
    assert response.status_code == 422


def test_timestamps_are_timezone_aware(crm_app):
    with _client() as client:
        body = client.post("/projects", json=PROJECT).json()["record"]
    for field in ("created_at", "updated_at"):
        assert "+" in body[field] or body[field].endswith("Z"), f"{field} thiếu offset múi giờ: {body[field]!r}"


# --- Đọc/Liệt kê -----------------------------------------------------------


def test_list_and_get(crm_app):
    with _client() as client:
        created = client.post("/projects", json=PROJECT).json()["record"]
        listed = client.get("/projects").json()
        fetched = client.get(f"/projects/{created['external_id']}").json()

    assert any(p["external_id"] == created["external_id"] for p in listed)
    assert fetched == created


def test_reading_an_unknown_project_returns_404(crm_app):
    with _client() as client:
        assert client.get("/projects/P-9999").status_code == 404


# --- Sửa -------------------------------------------------------------------


def test_update_increments_the_revision(crm_app):
    with _client() as client:
        created = client.post("/projects", json=PROJECT).json()["record"]
        response = client.patch(f"/projects/{created['external_id']}", json={"name": "Tên mới"})

    assert response.status_code == 200
    body = response.json()["record"]
    assert body["source_revision"] == 2
    assert body["name"] == "Tên mới"
    # Trường KHÔNG gửi vẫn giữ nguyên.
    assert body["launch_date"] == PROJECT["launch_date"]


def test_an_empty_patch_is_rejected(crm_app):
    with _client() as client:
        created = client.post("/projects", json=PROJECT).json()["record"]
        response = client.patch(f"/projects/{created['external_id']}", json={})
    assert response.status_code == 422
    assert response.json()["error_code"] == "EMPTY_PATCH"


def test_patching_an_unknown_project_returns_404(crm_app):
    with _client() as client:
        assert client.patch("/projects/P-9999", json={"name": "x"}).status_code == 404


# --- Lưu trữ / bất biến external_id -----------------------------------------


def test_archive_sets_status_and_does_not_physically_delete(crm_app):
    with _client() as client:
        created = client.post("/projects", json=PROJECT).json()["record"]
        response = client.delete(f"/projects/{created['external_id']}")

    assert response.status_code == 200
    body = response.json()["record"]
    assert body["status"] == "archived"
    assert body["source_revision"] == 2

    rows = _rows(crm_app, "crm_projects")
    # Dòng vẫn còn trong bảng — archive, không xoá.
    assert any(r["external_id"] == created["external_id"] for r in rows)


def test_archiving_twice_is_rejected(crm_app):
    with _client() as client:
        created = client.post("/projects", json=PROJECT).json()["record"]
        client.delete(f"/projects/{created['external_id']}")
        response = client.delete(f"/projects/{created['external_id']}")
    assert response.status_code == 409
    assert response.json()["error_code"] == "ALREADY_ARCHIVED"


def test_patching_an_archived_project_is_rejected(crm_app):
    with _client() as client:
        created = client.post("/projects", json=PROJECT).json()["record"]
        client.delete(f"/projects/{created['external_id']}")
        response = client.patch(f"/projects/{created['external_id']}", json={"name": "x"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "RECORD_ARCHIVED"


def test_external_id_is_never_reissued_after_archive(crm_app):
    """Giả định A1, áp dụng cho Project ở Phase B y hệt Unit/Deal (Phase 4):
    danh tính bền vững trọn đời, dãy không lùi kể cả sau khi lưu trữ."""
    with _client() as client:
        first = client.post("/projects", json=PROJECT).json()["record"]["external_id"]
        client.delete(f"/projects/{first}")
        second = client.post("/projects", json=PROJECT).json()["record"]["external_id"]
    assert first != second
    assert second == "P-0002"


def test_external_id_uniqueness_is_enforced_at_the_database_layer(crm_app):
    """Mini CRM chỉ có MỘT `source_instance_id` (chính nó) — "cô lập theo
    source_instance_id" ở Phase A nói về CÁC CÀI ĐẶT KHÁC NHAU, không áp dụng
    trong nội bộ một database. Ràng buộc thật cần kiểm ở đây là: `external_id`
    duy nhất TRONG cài đặt này, không phân biệt còn sống hay đã lưu trữ.
    """
    from sqlalchemy.exc import IntegrityError

    engine = sa.create_engine(sync_url(crm_app))
    try:
        with engine.begin() as conn:
            existing = conn.execute(sa.text("SELECT external_id FROM crm_projects LIMIT 1")).scalar_one()
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO crm_projects (id, external_id, name, launch_date, status, source_revision, "
                        "created_at, updated_at) VALUES (gen_random_uuid(), :ext, 'trùng', '2026-01-01', 'active', "
                        "1, now(), now())"
                    ),
                    {"ext": existing},
                )
            raised = False
        except IntegrityError:
            raised = True
        assert raised, "uq_crm_projects_external_id lẽ ra phải chặn external_id trùng"
    finally:
        engine.dispose()
