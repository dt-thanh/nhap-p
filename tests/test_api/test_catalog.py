"""Test GET/PATCH /projects và /areas trên PostgreSQL THẬT.

Phase D (§D7): `POST /projects`/`POST /areas` đã bị XOÁ — Project/Area CHỈ được
TẠO qua ingestion (`POST /sync/{entity}`, xem `tests/test_services/test_domain_projection.py`
và `tests/test_api/test_sync.py`). File này vì thế KHÔNG còn test tạo qua HTTP:
fixture chèn thẳng bằng SQL, mô phỏng một dòng ĐÃ soi gương (hoặc một dòng DI SẢN,
`external_id IS NULL`, tạo trước Phase D) — đúng hai loại dữ liệu THẬT SỰ tồn tại
trong bảng `projects`/`areas` từ Phase D trở đi.

Không mock DB: điều đáng kiểm tra ở đây là ràng buộc duy nhất
`uq_areas_project_name_unit_type`, CHECK trạng thái và ranh giới transaction —
mock đi thì chỉ còn kiểm tra pydantic.

Mọi khẳng định "đã lưu" đều đọc lại bằng SELECT, không tin vào response.

Chạy: `bash scripts/test_db.sh` hoặc đặt TEST_DATABASE_URL (xem
tests/test_services/test_import_records.py để biết cách bật/skip).
"""

from __future__ import annotations

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

from src.models.tables import (
    absorption_daily,
    areas,
    inventory_snapshots,
    projects,
    sales_records,
    upload_errors,
    upload_files,
)
from src.services.projects import CatalogRejectedError, ProjectService

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

PROJECTS_URL = "/api/v1/projects"
AREAS_URL = "/api/v1/areas"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory, monkeypatch):
    """DB sạch trước mỗi test, và router dùng đúng database test.

    Router gọi ProjectService() không tham số nên nó lấy session factory toàn cục
    từ src.db; trỏ lại về factory của test để không đụng database dev.
    """
    monkeypatch.setattr("src.services.projects.get_session_factory", lambda: session_factory)
    # Các endpoint GET đọc DB trực tiếp trong router, qua get_session_factory của
    # chính module đó — patch riêng, nếu không chúng sẽ đọc database dev.
    monkeypatch.setattr("src.api.dashboard.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.services.absorption.get_session_factory", lambda: session_factory)

    async with session_factory() as session:
        async with session.begin():
            for table in (
                upload_errors,
                absorption_daily,
                sales_records,
                inventory_snapshots,
                areas,
                upload_files,
                projects,
            ):
                await session.execute(sa.delete(table))
    yield


# --- Fixture: chèn thẳng (Phase D — không còn POST để tạo) --------------------


async def _insert_project(session_factory, **overrides) -> dict:
    """Mô phỏng một dòng `projects` ĐÃ CÓ — DI SẢN (`external_id=None`, tạo
    trước Phase D) trừ khi `overrides` khai `external_id`. Phase D CHỈ tạo qua
    ingestion; đây là cách duy nhất còn lại để dựng fixture cho test PATCH/GET.
    """
    now = datetime.now(UTC)
    values = {
        "id": uuid.uuid4(),
        "name": "Pilot Q1",
        "launch_date": date(2026, 1, 1),
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "headline": "",
        "introduce": "",
        "cover_image_url": None,
        "external_id": None,
        "source_system": None,
        "source_instance_id": None,
        "source_revision": None,
        "source_updated_at": None,
        **overrides,
    }
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.insert(projects).values(**values))
    return {
        "project_id": str(values["id"]),
        "name": values["name"],
        "launch_date": values["launch_date"].isoformat(),
        "status": values["status"],
        "headline": values["headline"],
        "introduce": values["introduce"],
        "cover_image_url": values["cover_image_url"],
    }


async def _insert_area(session_factory, project_id: str, **overrides) -> dict:
    now = datetime.now(UTC)
    values = {
        "id": uuid.uuid4(),
        "project_id": uuid.UUID(project_id),
        "area_name": "A1",
        "unit_type": "2PN",
        "bedrooms": 2,
        "area_sqm": Decimal("75.5"),
        "total_units": 100,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "headline": "",
        "introduce": "",
        "cover_image_url": None,
        "external_id": None,
        "source_system": None,
        "source_instance_id": None,
        "source_revision": None,
        "source_updated_at": None,
        **overrides,
    }
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.insert(areas).values(**values))
    return {
        "area_id": str(values["id"]),
        "project_id": str(values["project_id"]),
        "area_name": values["area_name"],
        "unit_type": values["unit_type"],
        "bedrooms": values["bedrooms"],
        "area_sqm": values["area_sqm"],
        "total_units": values["total_units"],
        "status": values["status"],
        "headline": values["headline"],
        "introduce": values["introduce"],
        "cover_image_url": values["cover_image_url"],
    }


async def _count(session_factory, table, **where) -> int:
    query = sa.select(sa.func.count()).select_from(table)
    for column, value in where.items():
        query = query.where(table.c[column] == value)
    async with session_factory() as session:
        return await session.scalar(query)


async def _patch_project(client, project_id, **body):
    return await client.patch(f"{PROJECTS_URL}/{project_id}", json=body)


async def _patch_area(client, area_id, **body):
    return await client.patch(f"{AREAS_URL}/{area_id}", json=body)


# --- Phase D §D7: POST bị XOÁ — không route nào tạo được Project/Area ---------


async def test_post_projects_no_longer_exists(client, session_factory):
    """Route đã bị XOÁ (§D7) — Project chỉ tạo được qua ingestion."""
    response = await client.post(PROJECTS_URL, json={"name": "X", "launch_date": "2026-01-01"})

    assert response.status_code in (404, 405), response.text
    assert await _count(session_factory, projects) == 0


async def test_post_areas_no_longer_exists(client, session_factory):
    """Route đã bị XOÁ (§D7) — Area chỉ tạo được qua ingestion."""
    project = await _insert_project(session_factory)

    response = await client.post(
        AREAS_URL,
        json={
            "project_id": project["project_id"],
            "area_name": "A1",
            "unit_type": "2PN",
            "bedrooms": 2,
            "area_sqm": "75.5",
            "total_units": 100,
        },
    )

    assert response.status_code in (404, 405), response.text
    assert await _count(session_factory, areas) == 0


async def test_project_service_has_no_create_methods(session_factory):
    """Chốt tường minh ở tầng service, không chỉ ở tầng route (§D7)."""
    assert not hasattr(ProjectService, "create_project")
    assert not hasattr(ProjectService, "create_area")


# --- GET /projects, GET /areas — đọc dữ liệu đã có (chèn thẳng) ---------------


async def test_created_project_can_be_read_back_through_the_list_endpoint(client, session_factory):
    inserted = await _insert_project(session_factory, name="Đọc lại được")

    listed = (await client.get(PROJECTS_URL)).json()

    assert [row["project_id"] for row in listed] == [inserted["project_id"]]
    assert listed[0]["name"] == "Đọc lại được"


async def test_get_projects_returns_content_fields(client, session_factory):
    """GET phải trả kèm headline/introduce để form sửa đổ sẵn giá trị cũ."""
    await _insert_project(session_factory, headline="Đợt 1", introduce="Mô tả dự án")

    listed = (await client.get(PROJECTS_URL)).json()

    assert listed[0]["headline"] == "Đợt 1"
    assert listed[0]["introduce"] == "Mô tả dự án"


async def test_get_projects_includes_external_id_and_source_revision(client, session_factory):
    """Phase D — trường MỚI, cộng thêm chứ không phá phía đọc cũ."""
    legacy = await _insert_project(session_factory, name="Di sản")
    mirrored_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(projects).values(
                    id=mirrored_id,
                    name="Đã soi gương",
                    launch_date=date(2026, 1, 1),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    status="active",
                    headline="",
                    introduce="",
                    external_id="P-0001",
                    source_system="mini_crm",
                    source_instance_id="mini-crm-dev",
                    source_revision=3,
                )
            )

    listed = {row["project_id"]: row for row in (await client.get(PROJECTS_URL)).json()}

    assert listed[legacy["project_id"]]["external_id"] is None
    assert listed[legacy["project_id"]]["source_revision"] is None
    assert listed[str(mirrored_id)]["external_id"] == "P-0001"
    assert listed[str(mirrored_id)]["source_revision"] == 3


async def test_get_project_by_external_id(client, session_factory):
    project_uuid = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(projects).values(
                    id=project_uuid,
                    name="Theo external_id",
                    launch_date=date(2026, 1, 1),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    status="active",
                    headline="",
                    introduce="",
                    external_id="P-0042",
                    source_system="mini_crm",
                    source_instance_id="mini-crm-dev",
                    source_revision=1,
                )
            )

    response = await client.get(f"{PROJECTS_URL}/P-0042")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(project_uuid)
    assert body["external_id"] == "P-0042"


async def test_get_project_by_unknown_external_id_is_404(client, session_factory):
    response = await client.get(f"{PROJECTS_URL}/P-KHONG-CO")

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"


async def test_get_areas_returns_content_fields(client, session_factory):
    project = await _insert_project(session_factory)
    await _insert_area(session_factory, project["project_id"], headline="Tháp A", introduce="Mô tả phân khu")

    listed = (await client.get(f"{AREAS_URL}?project_id={project['project_id']}")).json()

    assert listed[0]["headline"] == "Tháp A"
    assert listed[0]["introduce"] == "Mô tả phân khu"


async def test_get_areas_by_external_project_id(client, session_factory):
    """Phase D — lọc theo `external_project_id`, thay thế cho `project_id` UUID."""
    project_uuid = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(projects).values(
                    id=project_uuid,
                    name="X",
                    launch_date=date(2026, 1, 1),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    status="active",
                    headline="",
                    introduce="",
                    external_id="P-0099",
                    source_system="mini_crm",
                    source_instance_id="mini-crm-dev",
                )
            )
    await _insert_area(session_factory, str(project_uuid))

    response = await client.get(f"{AREAS_URL}?external_project_id=P-0099")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1


async def test_get_areas_requires_exactly_one_scope_param(client, session_factory):
    response_neither = await client.get(AREAS_URL)
    response_both = await client.get(f"{AREAS_URL}?project_id={uuid.uuid4()}&external_project_id=P-0001")

    assert response_neither.status_code == 422
    assert response_neither.json()["detail"]["error_code"] == "AMBIGUOUS_PROJECT_SCOPE"
    assert response_both.status_code == 422
    assert response_both.json()["detail"]["error_code"] == "AMBIGUOUS_PROJECT_SCOPE"


async def test_get_area_by_external_id(client, session_factory):
    project = await _insert_project(session_factory)
    area_uuid = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(areas).values(
                    id=area_uuid,
                    project_id=uuid.UUID(project["project_id"]),
                    area_name="A1",
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=Decimal("70"),
                    total_units=100,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    status="active",
                    headline="",
                    introduce="",
                    external_id="A-0001",
                    source_system="mini_crm",
                    source_instance_id="mini-crm-dev",
                    source_revision=1,
                )
            )

    response = await client.get(f"{AREAS_URL}/A-0001")

    assert response.status_code == 200, response.text
    assert response.json()["area_id"] == str(area_uuid)


async def test_get_area_by_unknown_external_id_is_404(client, session_factory):
    response = await client.get(f"{AREAS_URL}/A-KHONG-CO")

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "AREA_NOT_FOUND"


# --- PATCH /projects/{project_id} — CHỈ headline/introduce (§D7) --------------


async def test_update_project_persists_headline_to_postgres(client, session_factory):
    project = await _insert_project(session_factory, headline="Cũ")

    response = await _patch_project(client, project["project_id"], headline="Mới")

    assert response.status_code == 200, response.text
    assert response.json()["headline"] == "Mới"

    async with session_factory() as session:
        row = (
            await session.execute(sa.select(projects).where(projects.c.id == uuid.UUID(project["project_id"])))
        ).one()
    assert row.headline == "Mới"


async def test_update_project_rejects_canonical_business_fields(client, session_factory):
    """`name`/`launch_date` KHÔNG còn sửa được qua PATCH — CANONICAL, Mini CRM sở hữu (§D7)."""
    project = await _insert_project(session_factory, name="Không được đổi")

    response = await _patch_project(client, project["project_id"], name="Tên mới")

    assert response.status_code == 422
    async with session_factory() as session:
        name = await session.scalar(sa.select(projects.c.name).where(projects.c.id == uuid.UUID(project["project_id"])))
    assert name == "Không được đổi"


async def test_update_project_rejects_launch_date(client, session_factory):
    project = await _insert_project(session_factory, launch_date=date(2026, 1, 1))

    response = await _patch_project(client, project["project_id"], launch_date="2030-01-01")

    assert response.status_code == 422
    async with session_factory() as session:
        launch_date = await session.scalar(
            sa.select(projects.c.launch_date).where(projects.c.id == uuid.UUID(project["project_id"]))
        )
    assert launch_date == date(2026, 1, 1)


async def test_partial_update_leaves_other_fields_untouched(client, session_factory):
    """PATCH chỉ ghi trường có mặt trong body — không xoá dữ liệu còn lại."""
    project = await _insert_project(session_factory, headline="Giữ nguyên introduce", introduce="Introduce cũ")

    await _patch_project(client, project["project_id"], headline="Chỉ đổi headline")

    async with session_factory() as session:
        row = (
            await session.execute(sa.select(projects).where(projects.c.id == uuid.UUID(project["project_id"])))
        ).one()
    assert row.headline == "Chỉ đổi headline"
    assert row.introduce == "Introduce cũ"


async def test_update_project_does_not_change_status(client, session_factory):
    """`status` không có trong body PATCH nên không thể sửa qua API."""
    project = await _insert_project(session_factory)

    response = await _patch_project(client, project["project_id"], headline="X", status="archived")

    assert response.status_code == 200
    async with session_factory() as session:
        status_value = await session.scalar(
            sa.select(projects.c.status).where(projects.c.id == uuid.UUID(project["project_id"]))
        )
    assert status_value == "active"


async def test_update_unknown_project_is_404(client, session_factory):
    response = await _patch_project(client, str(uuid.uuid4()), headline="Không có")

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"


async def test_invalid_project_update_no_field_is_422(client, session_factory):
    project = await _insert_project(session_factory)

    response = await _patch_project(client, project["project_id"])

    assert response.status_code == 422


async def test_update_project_with_malformed_uuid_is_422(client, session_factory):
    response = await _patch_project(client, "khong-phai-uuid", headline="X")

    assert response.status_code == 422


async def test_update_mirrored_project_headline_does_not_touch_canonical_fields(client, session_factory):
    """Sửa nội dung hiển thị của một dự án ĐÃ soi gương từ Mini CRM không được
    đụng tới `external_id`/`source_revision`/`name`/`launch_date` của nó."""
    project_uuid = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(projects).values(
                    id=project_uuid,
                    name="Tên do Mini CRM đặt",
                    launch_date=date(2026, 6, 1),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    status="active",
                    headline="",
                    introduce="",
                    external_id="P-0007",
                    source_system="mini_crm",
                    source_instance_id="mini-crm-dev",
                    source_revision=5,
                )
            )

    response = await _patch_project(client, str(project_uuid), headline="Chú thích của backend")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Tên do Mini CRM đặt"
    assert body["external_id"] == "P-0007"
    assert body["source_revision"] == 5


# --- PATCH /areas/{area_id} — CHỈ headline/introduce (§D7) --------------------


async def test_update_area_persists_headline_to_postgres(client, session_factory):
    project = await _insert_project(session_factory)
    area = await _insert_area(session_factory, project["project_id"], headline="Cũ")

    response = await _patch_area(client, area["area_id"], headline="Mới", introduce="Mô tả mới")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["headline"] == "Mới"

    async with session_factory() as session:
        row = (await session.execute(sa.select(areas).where(areas.c.id == uuid.UUID(area["area_id"])))).one()
    assert (row.headline, row.introduce) == ("Mới", "Mô tả mới")


async def test_update_area_rejects_canonical_business_fields(client, session_factory):
    """`area_name`/`unit_type`/`bedrooms`/`area_sqm`/`total_units` KHÔNG còn sửa
    được qua PATCH — CANONICAL, Mini CRM sở hữu (§D7)."""
    project = await _insert_project(session_factory)
    area = await _insert_area(session_factory, project["project_id"], area_name="Không đổi")

    response = await _patch_area(client, area["area_id"], area_name="Tên mới")

    assert response.status_code == 422
    async with session_factory() as session:
        name = await session.scalar(sa.select(areas.c.area_name).where(areas.c.id == uuid.UUID(area["area_id"])))
    assert name == "Không đổi"


@pytest.mark.parametrize(
    "field,value,original",
    [
        ("unit_type", "3PN", "2PN"),
        ("bedrooms", 5, 2),
        ("area_sqm", "99.9", Decimal("75.5")),
        ("total_units", 999, 100),
    ],
)
async def test_update_area_rejects_each_canonical_field(client, session_factory, field, value, original):
    """`AreaUpdate` không còn khai báo các trường này — pydantic (mặc định
    `extra='ignore'`) ÂM THẦM LOẠI chúng khỏi thân request, nên một body CHỈ
    chứa một trường canonical trở thành RỖNG sau khi lọc → `NO_CHANGES` (422),
    không phải `FIELD_NOT_EDITABLE`. Cả hai đều là 422 và đều chứng minh ĐÚNG
    điều cần kiểm: trường canonical không có cách nào lọt xuống UPDATE qua
    đường HTTP. Đường `FIELD_NOT_EDITABLE` tường minh được kiểm riêng, gọi
    thẳng service — xem `test_service_refuses_to_update_a_forbidden_field`.
    """
    project = await _insert_project(session_factory)
    area = await _insert_area(session_factory, project["project_id"])

    response = await _patch_area(client, area["area_id"], **{field: value})

    assert response.status_code == 422
    async with session_factory() as session:
        row = (await session.execute(sa.select(areas).where(areas.c.id == uuid.UUID(area["area_id"])))).one()
    assert getattr(row, field) == original


async def test_update_area_keeps_its_project(client, session_factory):
    """`project_id` không phải trường `AreaUpdate` khai báo — pydantic ÂM THẦM
    LOẠI nó khỏi thân request (mặc định `extra='ignore'`), nên PATCH vẫn chạy
    với phần còn lại của body và quan hệ cha giữ nguyên."""
    project_a = await _insert_project(session_factory, name="Dự án A")
    project_b = await _insert_project(session_factory, name="Dự án B")
    area = await _insert_area(session_factory, project_a["project_id"])

    response = await _patch_area(client, area["area_id"], headline="Đổi", project_id=project_b["project_id"])

    assert response.status_code == 200
    assert response.json()["project_id"] == project_a["project_id"]
    async with session_factory() as session:
        owner = await session.scalar(sa.select(areas.c.project_id).where(areas.c.id == uuid.UUID(area["area_id"])))
    assert owner == uuid.UUID(project_a["project_id"])


async def test_update_area_does_not_change_status(client, session_factory):
    project = await _insert_project(session_factory)
    area = await _insert_area(session_factory, project["project_id"])

    await _patch_area(client, area["area_id"], headline="X", status="archived")

    async with session_factory() as session:
        status_value = await session.scalar(sa.select(areas.c.status).where(areas.c.id == uuid.UUID(area["area_id"])))
    assert status_value == "active"


async def test_update_unknown_area_is_404(client, session_factory):
    response = await _patch_area(client, str(uuid.uuid4()), headline="Không có")

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "AREA_NOT_FOUND"


async def test_invalid_area_update_no_field_is_422(client, session_factory):
    project = await _insert_project(session_factory)
    area = await _insert_area(session_factory, project["project_id"])

    response = await _patch_area(client, area["area_id"])

    assert response.status_code == 422


async def test_service_refuses_to_update_a_forbidden_field(session_factory):
    """Gọi thẳng service: `project_id`/`status`/trường CANONICAL bị chặn kể cả
    khi bỏ qua schema pydantic."""
    project = await _insert_project(session_factory)
    area = await _insert_area(session_factory, project["project_id"])
    service = ProjectService(session_factory)

    with pytest.raises(CatalogRejectedError) as exc:
        await service.update_area(area["area_id"], {"project_id": str(uuid.uuid4())})
    assert exc.value.error_code == "FIELD_NOT_EDITABLE"

    with pytest.raises(CatalogRejectedError) as exc:
        await service.update_area(area["area_id"], {"area_name": "X"})
    assert exc.value.error_code == "FIELD_NOT_EDITABLE"


async def test_headline_longer_than_255_is_422(client, session_factory):
    """`headline` là VARCHAR(255) dưới DB — chặn ở API để không vỡ ở tầng ghi."""
    project = await _insert_project(session_factory)

    response = await _patch_project(client, project["project_id"], headline="x" * 256)

    assert response.status_code == 422


@pytest.mark.parametrize("field", ["headline", "introduce"])
async def test_content_fields_can_be_cleared(client, session_factory, field):
    """Nội dung hiển thị được phép xoá về rỗng."""
    project = await _insert_project(session_factory, headline="Có", introduce="Có")

    response = await _patch_project(client, project["project_id"], **{field: ""})

    assert response.status_code == 200
    assert response.json()[field] == ""


# --- Ràng buộc DB nguyên trạng (FK/CHECK) — vẫn kiểm được không qua service ---


async def test_foreign_key_blocks_an_area_pointing_at_a_missing_project(session_factory):
    """`fk_areas_project_id` vẫn còn nguyên — không phụ thuộc vào việc service
    còn đường tạo hay không."""
    async with session_factory() as session:
        with pytest.raises(Exception):
            async with session.begin():
                await session.execute(
                    sa.insert(areas).values(
                        id=uuid.uuid4(),
                        project_id=uuid.uuid4(),  # không tồn tại
                        area_name="A1",
                        unit_type="2PN",
                        bedrooms=2,
                        area_sqm=Decimal("75"),
                        total_units=10,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                        status="active",
                    )
                )

    assert await _count(session_factory, areas) == 0


async def test_check_constraint_blocks_negative_area_sqm(session_factory):
    """`ck_areas_area_sqm_positive` vẫn còn nguyên."""
    project = await _insert_project(session_factory)

    async with session_factory() as session:
        with pytest.raises(Exception):
            async with session.begin():
                await session.execute(
                    sa.insert(areas).values(
                        id=uuid.uuid4(),
                        project_id=uuid.UUID(project["project_id"]),
                        area_name="A1",
                        unit_type="2PN",
                        bedrooms=2,
                        area_sqm=Decimal("-1"),
                        total_units=10,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                        status="active",
                    )
                )

    assert await _count(session_factory, areas) == 0


async def test_duplicate_natural_key_is_still_blocked_at_db_level(session_factory):
    """`uq_areas_project_name_unit_type` vẫn còn nguyên — Phase D không đổi
    ràng buộc này, chỉ đổi AI được phép ghi vào nó."""
    project = await _insert_project(session_factory)
    await _insert_area(session_factory, project["project_id"], area_name="A1", unit_type="2PN")

    async with session_factory() as session:
        with pytest.raises(Exception):
            async with session.begin():
                await session.execute(
                    sa.insert(areas).values(
                        id=uuid.uuid4(),
                        project_id=uuid.UUID(project["project_id"]),
                        area_name="A1",
                        unit_type="2PN",
                        bedrooms=3,
                        area_sqm=Decimal("80"),
                        total_units=50,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                        status="active",
                    )
                )
