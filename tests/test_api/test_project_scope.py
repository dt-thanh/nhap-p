"""Phân quyền ĐỌC theo phạm vi dự án (Phase E) — trên PostgreSQL THẬT.

Trước Phase E, `GET /projects`, `GET /areas`, `GET /inventory`, `GET /deals`
hoàn toàn MỞ (không `Depends` nào) — xem docstring cũ của `src/api/dashboard.py`
và `src/api/inventory.py`. File này chứng minh: (1) route ĐỌC giờ đòi tối thiểu
`business_viewer`, (2) phạm vi dự án cưỡng chế ở TẦNG TRUY VẤN cho cả liệt kê
KHÔNG lọc (`GET /projects`) lẫn liệt kê/CHI TIẾT ĐàlỌC (`GET /areas?...`,
`GET /projects/{id}`), (3) không route nào rò rỉ dữ liệu ngoài phạm vi qua JOIN
(Area → Project, Inventory/Deal → Area → Project).

Chèn thẳng bằng SQL (giống `test_catalog.py`) — Project/Area chỉ TẠO qua
ingestion từ Phase D, HTTP không còn đường tạo nào.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, projects, upload_errors, upload_files

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _skip_reason(url: str | None) -> str:
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' — chạy `bash scripts/test_db.sh`"
    return ""


_SKIP = _skip_reason(TEST_DATABASE_URL)
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

VIEWER_A_TOKEN = "scope-viewer-a"  # noqa: S105
OPERATOR_A_TOKEN = "scope-operator-a"  # noqa: S105
ADMIN_NO_ALL_TOKEN = "scope-admin-no-all"  # noqa: S105
ADMIN_ALL_TOKEN = "scope-admin-all"  # noqa: S105
EMPTY_SCOPE_TOKEN = "scope-empty"  # noqa: S105

PROJECT_A_EXTERNAL = "SCOPE-P-A"
PROJECT_B_EXTERNAL = "SCOPE-P-B"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def dashboard_tokens(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("DASHBOARD_BUSINESS_VIEWER_TOKEN", VIEWER_A_TOKEN)
    monkeypatch.setenv("DASHBOARD_PIPELINE_OPERATOR_TOKEN", OPERATOR_A_TOKEN)
    # Hai token 'admin' KHÁC NHAU để tách rời "vai trò admin" khỏi "phạm vi ALL"
    # — FROZEN §A7.2: không có nhánh mã "nếu admin thì bỏ qua kiểm phạm vi".
    # pydantic Settings chỉ có MỘT trường `dashboard_admin_token`; test hai tình
    # huống admin bằng cách đổi giá trị token đó + map phạm vi tương ứng ở TỪNG
    # test cần nó, xem `test_admin_without_all_cannot_cross_projects`.
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", ADMIN_ALL_TOKEN)
    monkeypatch.setenv(
        "DASHBOARD_PROJECT_SCOPE",
        json.dumps(
            {
                VIEWER_A_TOKEN: [PROJECT_A_EXTERNAL],
                OPERATOR_A_TOKEN: [PROJECT_A_EXTERNAL],
                ADMIN_ALL_TOKEN: "ALL",
                EMPTY_SCOPE_TOKEN: [],
            }
        ),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory, monkeypatch):
    monkeypatch.setattr("src.api.dashboard.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.api.inventory.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.api.sync.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.services.absorption.get_session_factory", lambda: session_factory)

    async with session_factory() as session:
        async with session.begin():
            for table in (upload_errors, upload_files, areas, projects):
                await session.execute(sa.delete(table))
    yield


async def _insert_project(session_factory, *, external_id: str | None, name: str) -> uuid.UUID:
    now = datetime.now(UTC)
    project_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(projects).values(
                    id=project_id,
                    name=name,
                    launch_date=date(2026, 1, 1),
                    created_at=now,
                    updated_at=now,
                    status="active",
                    headline="",
                    introduce="",
                    external_id=external_id,
                    source_system="mini_crm" if external_id else None,
                    source_instance_id="mini-crm-dev" if external_id else None,
                    source_revision=1 if external_id else None,
                )
            )
    return project_id


async def _insert_area(session_factory, project_id: uuid.UUID, *, external_id: str, name: str) -> uuid.UUID:
    now = datetime.now(UTC)
    area_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(areas).values(
                    id=area_id,
                    project_id=project_id,
                    area_name=name,
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=Decimal("60"),
                    total_units=10,
                    created_at=now,
                    updated_at=now,
                    status="active",
                    headline="",
                    introduce="",
                    external_id=external_id,
                    source_system="mini_crm",
                    source_instance_id="mini-crm-dev",
                    source_revision=1,
                )
            )
    return area_id


@pytest_asyncio.fixture
async def two_projects(session_factory):
    project_a = await _insert_project(session_factory, external_id=PROJECT_A_EXTERNAL, name="Project A")
    project_b = await _insert_project(session_factory, external_id=PROJECT_B_EXTERNAL, name="Project B")
    area_a = await _insert_area(session_factory, project_a, external_id="SCOPE-A-A1", name="A1")
    area_b = await _insert_area(session_factory, project_b, external_id="SCOPE-A-B1", name="B1")
    return {"project_a": project_a, "project_b": project_b, "area_a": area_a, "area_b": area_b}


# --- 401/403 cơ bản -----------------------------------------------------------


async def _get_without_auth_header(client, url: str):
    """`headers={}` KHÔNG xoá header mặc định của `client` (httpx MERGE, không
    THAY THẾ, header rỗng không ghi đè gì) — request vẫn mang
    `DASHBOARD_AUTH_HEADER` của fixture `client`. Xây request thủ công rồi xoá
    hẳn `Authorization` là cách DUY NHẤT thật sự gửi request không có nó."""
    request = client.build_request("GET", url)
    del request.headers["authorization"]
    return await client.send(request)


async def test_no_token_is_401(client, two_projects):
    response = await _get_without_auth_header(client, "/api/v1/projects")
    assert response.status_code == 401


async def test_development_bypass_reads_real_projects_without_a_token(client, two_projects, monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    get_settings.cache_clear()
    try:
        response = await _get_without_auth_header(client, "/api/v1/projects")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    rows = response.json()
    assert {row["external_id"] for row in rows} == {PROJECT_A_EXTERNAL, PROJECT_B_EXTERNAL}
    assert {row["name"] for row in rows} == {"Project A", "Project B"}


async def test_invalid_token_is_401(client, two_projects):
    response = await client.get("/api/v1/projects", headers=_headers("not-a-real-token"))
    assert response.status_code == 401


# --- Viewer: đọc trong phạm vi, KHÔNG đọc được ngoài phạm vi -------------------


async def test_viewer_can_read_project_a(client, two_projects):
    response = await client.get(f"/api/v1/projects/{PROJECT_A_EXTERNAL}", headers=_headers(VIEWER_A_TOKEN))
    assert response.status_code == 200
    assert response.json()["external_id"] == PROJECT_A_EXTERNAL


async def test_viewer_cannot_read_project_b(client, two_projects):
    response = await client.get(f"/api/v1/projects/{PROJECT_B_EXTERNAL}", headers=_headers(VIEWER_A_TOKEN))
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


async def test_viewer_project_list_only_shows_project_a(client, two_projects):
    response = await client.get("/api/v1/projects", headers=_headers(VIEWER_A_TOKEN))
    assert response.status_code == 200
    rows = response.json()
    external_ids = {row["external_id"] for row in rows}
    assert external_ids == {PROJECT_A_EXTERNAL}
    assert rows[0]["status"] == "active"


async def test_scope_applies_through_the_area_join(client, two_projects):
    """Area không tự mang external_id dự án trong response filter — phạm vi
    phải cưỡng chế qua JOIN sang Project, không phải qua route-level check."""
    ok = await client.get(
        "/api/v1/areas", params={"external_project_id": PROJECT_A_EXTERNAL}, headers=_headers(VIEWER_A_TOKEN)
    )
    assert ok.status_code == 200

    blocked = await client.get(
        "/api/v1/areas", params={"external_project_id": PROJECT_B_EXTERNAL}, headers=_headers(VIEWER_A_TOKEN)
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


async def test_scope_cannot_be_bypassed_with_the_internal_uuid(client, two_projects):
    """Cùng một chốt phải áp dụng dù gọi bằng UUID nội bộ hay external_id — hai
    tham số chỉ là hai HÌNH DẠNG của cùng một câu hỏi phạm vi."""
    response = await client.get(
        "/api/v1/areas", params={"project_id": str(two_projects["project_b"])}, headers=_headers(VIEWER_A_TOKEN)
    )
    assert response.status_code == 403


async def test_scope_applies_to_area_detail_via_join(client, two_projects):
    ok = await client.get("/api/v1/areas/SCOPE-A-A1", headers=_headers(VIEWER_A_TOKEN))
    assert ok.status_code == 200

    blocked = await client.get("/api/v1/areas/SCOPE-A-B1", headers=_headers(VIEWER_A_TOKEN))
    assert blocked.status_code == 403


async def test_scope_applies_to_inventory(client, two_projects):
    ok = await client.get(
        "/api/v1/inventory", params={"external_project_id": PROJECT_A_EXTERNAL}, headers=_headers(VIEWER_A_TOKEN)
    )
    assert ok.status_code == 200

    blocked = await client.get(
        "/api/v1/inventory", params={"external_project_id": PROJECT_B_EXTERNAL}, headers=_headers(VIEWER_A_TOKEN)
    )
    assert blocked.status_code == 403


async def test_scope_applies_to_deals(client, two_projects):
    ok = await client.get(
        "/api/v1/deals", params={"external_project_id": PROJECT_A_EXTERNAL}, headers=_headers(VIEWER_A_TOKEN)
    )
    assert ok.status_code == 200

    blocked = await client.get(
        "/api/v1/deals", params={"external_project_id": PROJECT_B_EXTERNAL}, headers=_headers(VIEWER_A_TOKEN)
    )
    assert blocked.status_code == 403


# --- Operator: cùng phạm vi áp dụng cho hành động vận hành ---------------------


async def test_operator_can_operate_within_scope(client, two_projects):
    response = await client.get(
        "/api/v1/sync-runs", params={"source": "no-match"}, headers=_headers(OPERATOR_A_TOKEN)
    )
    assert response.status_code == 200


async def test_operator_reprocess_outside_scope_is_403(client, two_projects, session_factory):
    """Một lô gắn với Project B — operator chỉ có phạm vi Project A không đọc
    được payload của nó."""
    now = datetime.now(UTC)
    run_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=run_id,
                    project_id=two_projects["project_b"],
                    status="completed",
                    rows_ok=0,
                    rows_failed=0,
                    uploaded_at=now,
                    source_system="mini_crm",
                    source_instance_id="mini-crm-dev",
                    source_entity="units",
                    input_format="json",
                    transport_mode="api_push",
                    sync_mode="incremental",
                    schema_version=1,
                    rows_received=0,
                    error_summary={},
                )
            )
    response = await client.get(f"/api/v1/sync-runs/{run_id}/payload", headers=_headers(OPERATOR_A_TOKEN))
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"


# --- Admin: không có "admin thì bỏ qua" ----------------------------------------


async def test_admin_without_all_cannot_cross_projects(client, two_projects, monkeypatch):
    """`admin` KHÔNG mặc định xuyên dự án — chỉ khi được cấp `ALL` tường minh.
    Đổi chính token admin đang cấu hình sang một token phạm vi HẸP để kiểm."""
    from src.config import get_settings

    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", ADMIN_NO_ALL_TOKEN)
    monkeypatch.setenv(
        "DASHBOARD_PROJECT_SCOPE",
        json.dumps({ADMIN_NO_ALL_TOKEN: [PROJECT_A_EXTERNAL]}),
    )
    get_settings.cache_clear()
    try:
        blocked = await client.get(
            f"/api/v1/projects/{PROJECT_B_EXTERNAL}", headers=_headers(ADMIN_NO_ALL_TOKEN)
        )
        assert blocked.status_code == 403
        allowed = await client.get(
            f"/api/v1/projects/{PROJECT_A_EXTERNAL}", headers=_headers(ADMIN_NO_ALL_TOKEN)
        )
        assert allowed.status_code == 200
    finally:
        get_settings.cache_clear()


async def test_admin_with_explicit_all_can_cross_projects(client, two_projects):
    a = await client.get(f"/api/v1/projects/{PROJECT_A_EXTERNAL}", headers=_headers(ADMIN_ALL_TOKEN))
    b = await client.get(f"/api/v1/projects/{PROJECT_B_EXTERNAL}", headers=_headers(ADMIN_ALL_TOKEN))
    assert a.status_code == 200
    assert b.status_code == 200


async def test_empty_scope_grants_no_project_access(client, two_projects, monkeypatch):
    """Token hợp lệ (xác thực được vai trò), phạm vi RỖNG tường minh → không đọc
    được dự án nào, kể cả liệt kê không lọc."""
    from src.config import get_settings

    monkeypatch.setenv("DASHBOARD_BUSINESS_VIEWER_TOKEN", EMPTY_SCOPE_TOKEN)
    monkeypatch.setenv("DASHBOARD_PROJECT_SCOPE", json.dumps({EMPTY_SCOPE_TOKEN: []}))
    get_settings.cache_clear()
    try:
        listing = await client.get("/api/v1/projects", headers=_headers(EMPTY_SCOPE_TOKEN))
        assert listing.status_code == 200
        assert listing.json() == []

        detail = await client.get(f"/api/v1/projects/{PROJECT_A_EXTERNAL}", headers=_headers(EMPTY_SCOPE_TOKEN))
        assert detail.status_code == 403
    finally:
        get_settings.cache_clear()


# --- Không rò rỉ qua trạng thái legacy (external_id NULL) ----------------------


async def test_legacy_project_without_external_id_is_invisible_to_narrow_scope(client, session_factory):
    """Dự án DI SẢN (`external_id IS NULL`, trước Phase D) không thể được cấp
    phạm vi tường minh (không có external_id để liệt kê) — chỉ ALL thấy được."""
    legacy_id = await _insert_project(session_factory, external_id=None, name="Legacy")

    narrow = await client.get("/api/v1/projects", headers=_headers(VIEWER_A_TOKEN))
    assert str(legacy_id) not in [row["project_id"] for row in narrow.json()]

    wide = await client.get("/api/v1/projects", headers=_headers(ADMIN_ALL_TOKEN))
    assert str(legacy_id) in [row["project_id"] for row in wide.json()]


# --- /me/permissions: chỉ để hiển thị -------------------------------------------


async def test_me_permissions_reports_role_and_scope(client, two_projects):
    response = await client.get("/api/v1/me/permissions", headers=_headers(VIEWER_A_TOKEN))
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "business_viewer"
    assert body["project_scope"] == [PROJECT_A_EXTERNAL]


async def test_me_permissions_reports_all(client, two_projects):
    response = await client.get("/api/v1/me/permissions", headers=_headers(ADMIN_ALL_TOKEN))
    assert response.json()["project_scope"] == "ALL"


# --- Liệt kê bảng định tuyến: không route dữ liệu dự án nào bị sót -------------


async def test_route_enumeration_every_project_scoped_route_requires_a_principal(two_projects):
    """Chứng minh KHÔNG route nào trong danh sách tối thiểu của Phase E còn mở —
    gọi THẬT không kèm token và đòi 401, thay vì dò cấu trúc nội bộ của
    Starlette (route table hiện đại bọc router con qua `_IncludedRouter`, không
    còn phẳng hoá `path` kèm tiền tố — kiểm hành vi qua HTTP bền hơn hẳn việc dò
    một chi tiết triển khai có thể đổi giữa các bản Starlette).

    Client RIÊNG, không dùng fixture `client` dùng chung của conftest — fixture
    đó cố tình gắn sẵn header admin mặc định cho MỌI test khác, và một request
    "không kèm token" phải THẬT SỰ không mang gì, không phải một header rỗng
    ghi đè không xong lên header mặc định của client.
    """
    from httpx import ASGITransport, AsyncClient

    from src.main import app

    must_require_auth = [
        "/api/v1/projects",
        f"/api/v1/projects/{PROJECT_A_EXTERNAL}",
        f"/api/v1/areas?external_project_id={PROJECT_A_EXTERNAL}",
        "/api/v1/areas/SCOPE-A-A1",
        f"/api/v1/inventory?external_project_id={PROJECT_A_EXTERNAL}",
        f"/api/v1/deals?external_project_id={PROJECT_A_EXTERNAL}",
        "/api/v1/sync-runs",
        "/api/v1/sync-errors",
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        for path in must_require_auth:
            response = await anon.get(path)
            assert response.status_code == 401, f"GET {path} không đòi xác thực (trả {response.status_code})"
