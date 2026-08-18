"""Test CRUD ảnh bìa dự án / phân khu trên PostgreSQL THẬT, Cloudinary giả lập.

DB thật vì điều cần kiểm là hai cột `cover_image_url` / `cover_image_public_id`
có được ghi và xoá đúng cặp không. Cloudinary thì thay bằng client giả: gọi mạng
thật sẽ chậm, cần khoá thật, và không tái hiện được các ca HỎNG (upload lỗi, xoá
lỗi) — mà đó mới là phần dễ để lại file mồ côi.

Chạy: `bash scripts/test_db.sh` hoặc đặt TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
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
from src.services.images import (
    AREA_OWNER,
    PROJECT_OWNER,
    ImageRejectedError,
    ImageService,
)

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

PROJECT_ID = uuid.UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301")
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


class FakeCloudinary:
    """Client giả: ghi lại lời gọi, và cho phép ép hỏng đúng chỗ cần kiểm."""

    def __init__(self):
        self.uploaded: list[str] = []
        self.destroyed: list[str] = []
        self.fail_upload = False
        self.fail_destroy = False
        self.destroy_result = "ok"

    def upload(self, data: bytes, *, public_id: str) -> tuple[str, str]:
        if self.fail_upload:
            raise RuntimeError("cloudinary down")
        # Cloudinary trả public_id có kèm tiền tố thư mục; ghi lại đúng giá trị
        # đó để so sánh với danh sách đã xoá không bị lệch.
        stored = f"folder/{public_id}"
        self.uploaded.append(stored)
        return f"https://res.cloudinary.com/demo/{public_id}.png", stored

    def destroy(self, public_id: str) -> None:
        if self.fail_destroy:
            raise ImageRejectedError("STORAGE_DELETE_FAILED", "Cloudinary không xoá được ảnh")
        if self.destroy_result not in ("ok", "not found"):
            raise ImageRejectedError("STORAGE_DELETE_FAILED", "Cloudinary không xoá được ảnh")
        self.destroyed.append(public_id)


class FakeUpload:
    """Thay cho fastapi.UploadFile — chỉ cần `read`."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async with session_factory() as session:
        async with session.begin():
            # Xoá theo chiều ngược khoá ngoại: upload_files/sales_records còn
            # tham chiếu tới projects/areas thì DELETE FROM projects sẽ vỡ.
            for table in (
                upload_errors,
                absorption_daily,
                sales_records,
                inventory_snapshots,
                areas,
                upload_files,
            ):
                await session.execute(sa.delete(table))
            await session.execute(sa.text("DELETE FROM projects"))
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Pilot', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
    yield


@pytest_asyncio.fixture
async def area_id(session_factory):
    new_id = uuid.uuid4()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(areas).values(
                    id=new_id,
                    project_id=PROJECT_ID,
                    area_name="A1",
                    unit_type="2PN",
                    bedrooms=2,
                    area_sqm=75,
                    total_units=100,
                    created_at=datetime.now(UTC),
                )
            )
    return new_id


@pytest.fixture
def cloud():
    return FakeCloudinary()


@pytest.fixture
def service(session_factory, cloud):
    return ImageService(session_factory, client=cloud)


async def _columns(session_factory, table, row_id):
    async with session_factory() as session:
        return (
            await session.execute(
                sa.select(table.c.cover_image_url, table.c.cover_image_public_id).where(table.c.id == row_id)
            )
        ).one()


# Chạy MỌI ca cho cả dự án lẫn phân khu — hai thực thể phải hành xử giống hệt nhau.
@pytest.fixture(params=["project", "area"])
def owner_case(request, area_id):
    if request.param == "project":
        return PROJECT_OWNER, PROJECT_ID, projects
    return AREA_OWNER, area_id, areas


# --- Tạo --------------------------------------------------------------------


async def test_create_stores_url_and_public_id(service, session_factory, cloud, owner_case):
    owner, row_id, table = owner_case

    record = await service.create(owner, row_id, FakeUpload(PNG), "anh.png")

    assert record.url.startswith("https://res.cloudinary.com/")
    assert record.public_id
    row = await _columns(session_factory, table, row_id)
    assert row.cover_image_url == record.url
    assert row.cover_image_public_id == record.public_id
    assert len(cloud.uploaded) == 1


async def test_create_twice_is_rejected_as_duplicate(service, cloud, owner_case):
    """Mỗi bản ghi tối đa một ảnh — POST lần hai phải từ chối, không âm thầm ghi đè."""
    owner, row_id, _ = owner_case
    await service.create(owner, row_id, FakeUpload(PNG), "a.png")

    with pytest.raises(ImageRejectedError) as exc:
        await service.create(owner, row_id, FakeUpload(PNG), "b.png")

    assert exc.value.error_code == "IMAGE_ALREADY_EXISTS"
    assert len(cloud.uploaded) == 1  # không upload thêm


async def test_create_for_missing_owner_is_not_found(service, cloud, owner_case):
    owner, _, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.create(owner, uuid.uuid4(), FakeUpload(PNG), "a.png")

    assert exc.value.error_code == owner.not_found_code
    assert cloud.uploaded == []  # không đụng Cloudinary khi bản ghi không tồn tại


# --- Kiểm tra đầu vào -------------------------------------------------------


@pytest.mark.parametrize("filename", ["tai-lieu.pdf", "anh.svg", "khong-co-duoi", ""])
async def test_unsupported_format_is_rejected(service, cloud, owner_case, filename):
    owner, row_id, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.create(owner, row_id, FakeUpload(PNG), filename)

    assert exc.value.error_code == "UNSUPPORTED_IMAGE_FORMAT"
    assert cloud.uploaded == []


async def test_empty_file_is_rejected(service, cloud, owner_case):
    owner, row_id, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.create(owner, row_id, FakeUpload(b""), "a.png")

    assert exc.value.error_code == "EMPTY_IMAGE"
    assert cloud.uploaded == []


async def test_oversized_file_is_rejected(service, cloud, owner_case, monkeypatch):
    """Chặn NGAY trong lúc đọc, không nạp hết rồi mới đo."""
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "image_max_size", 100)
    owner, row_id, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.create(owner, row_id, FakeUpload(b"x" * 5000), "a.png")

    assert exc.value.error_code == "IMAGE_TOO_LARGE"
    assert cloud.uploaded == []


async def test_invalid_uuid_is_rejected(service, owner_case):
    owner, _, _ = owner_case

    with pytest.raises(ValueError):
        await service.create(owner, "khong-phai-uuid", FakeUpload(PNG), "a.png")


# --- Xem --------------------------------------------------------------------


async def test_get_returns_current_image(service, owner_case):
    owner, row_id, _ = owner_case
    created = await service.create(owner, row_id, FakeUpload(PNG), "a.png")

    fetched = await service.get(owner, row_id)

    assert (fetched.url, fetched.public_id) == (created.url, created.public_id)


async def test_get_without_image_is_not_found(service, owner_case):
    owner, row_id, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.get(owner, row_id)

    assert exc.value.error_code == "IMAGE_NOT_FOUND"


async def test_get_for_missing_owner_is_not_found(service, owner_case):
    owner, _, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.get(owner, uuid.uuid4())

    assert exc.value.error_code == owner.not_found_code


# --- Thay thế ---------------------------------------------------------------


async def test_replace_updates_columns(service, session_factory, owner_case):
    owner, row_id, table = owner_case
    await service.create(owner, row_id, FakeUpload(PNG), "cu.png")

    replaced = await service.replace(owner, row_id, FakeUpload(PNG), "moi.png")

    row = await _columns(session_factory, table, row_id)
    assert row.cover_image_public_id == replaced.public_id


async def test_replace_without_existing_image_is_allowed(service, owner_case):
    """PUT mang nghĩa "đặt thành", nên không đòi phải có ảnh từ trước."""
    owner, row_id, _ = owner_case

    record = await service.replace(owner, row_id, FakeUpload(PNG), "a.png")

    assert record.public_id


async def test_replace_reuses_public_id_so_no_orphan_is_left(service, cloud, owner_case):
    """public_id cố định theo thực thể: ảnh mới ghi đè đúng chỗ, không sinh rác."""
    owner, row_id, _ = owner_case
    first = await service.create(owner, row_id, FakeUpload(PNG), "a.png")

    second = await service.replace(owner, row_id, FakeUpload(PNG), "b.png")

    assert first.public_id == second.public_id
    assert cloud.destroyed == []  # cùng public_id thì không phải xoá riêng


# --- Xoá --------------------------------------------------------------------


async def test_delete_clears_both_columns_and_removes_from_storage(service, session_factory, cloud, owner_case):
    owner, row_id, table = owner_case
    created = await service.create(owner, row_id, FakeUpload(PNG), "a.png")

    await service.delete(owner, row_id)

    row = await _columns(session_factory, table, row_id)
    assert row.cover_image_url is None
    assert row.cover_image_public_id is None
    assert cloud.destroyed == [created.public_id]


async def test_delete_without_image_is_not_found(service, cloud, owner_case):
    owner, row_id, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.delete(owner, row_id)

    assert exc.value.error_code == "IMAGE_NOT_FOUND"
    assert cloud.destroyed == []


async def test_delete_for_missing_owner_is_not_found(service, owner_case):
    owner, _, _ = owner_case

    with pytest.raises(ImageRejectedError) as exc:
        await service.delete(owner, uuid.uuid4())

    assert exc.value.error_code == owner.not_found_code


# --- Cloudinary hỏng: giữ DB và kho ảnh đồng bộ ------------------------------


async def test_upload_failure_leaves_database_untouched(service, session_factory, cloud, owner_case):
    owner, row_id, table = owner_case
    cloud.fail_upload = True

    with pytest.raises(ImageRejectedError) as exc:
        await service.create(owner, row_id, FakeUpload(PNG), "a.png")

    assert exc.value.error_code == "STORAGE_UPLOAD_FAILED"
    row = await _columns(session_factory, table, row_id)
    assert row.cover_image_url is None
    assert row.cover_image_public_id is None


async def test_replacement_failure_keeps_the_old_image(service, session_factory, cloud, owner_case):
    """Upload ảnh mới hỏng thì bản ghi phải vẫn trỏ vào ảnh CŨ, không thành rỗng."""
    owner, row_id, table = owner_case
    first = await service.create(owner, row_id, FakeUpload(PNG), "cu.png")
    cloud.fail_upload = True

    with pytest.raises(ImageRejectedError):
        await service.replace(owner, row_id, FakeUpload(PNG), "moi.png")

    row = await _columns(session_factory, table, row_id)
    assert row.cover_image_public_id == first.public_id
    assert row.cover_image_url == first.url


async def test_delete_failure_keeps_the_reference(service, session_factory, cloud, owner_case):
    """Cloudinary xoá hỏng thì KHÔNG xoá tham chiếu — mất public_id là mất luôn
    đường dọn ảnh, để lại file mồ côi vĩnh viễn."""
    owner, row_id, table = owner_case
    created = await service.create(owner, row_id, FakeUpload(PNG), "a.png")
    cloud.fail_destroy = True

    with pytest.raises(ImageRejectedError) as exc:
        await service.delete(owner, row_id)

    assert exc.value.error_code == "STORAGE_DELETE_FAILED"
    row = await _columns(session_factory, table, row_id)
    assert row.cover_image_public_id == created.public_id


async def test_already_missing_on_storage_is_treated_as_deleted(service, session_factory, cloud, owner_case):
    """Ảnh đã bị xoá tay trên Cloudinary vẫn phải dọn được tham chiếu."""
    owner, row_id, table = owner_case
    await service.create(owner, row_id, FakeUpload(PNG), "a.png")
    cloud.destroy_result = "not found"

    await service.delete(owner, row_id)

    row = await _columns(session_factory, table, row_id)
    assert row.cover_image_public_id is None


async def test_database_failure_after_upload_removes_the_orphan(session_factory, cloud):
    """Ghi DB hỏng sau khi đã upload → phải xoá ảnh vừa đưa lên, không để mồ côi."""

    class BrokenFactory:
        """Cho phép đọc, nhưng mọi phiên GHI đều hỏng."""

        def __init__(self, real):
            self._real = real
            self.writes = 0

        def __call__(self):
            self.writes += 1
            # Lần gọi 1 = đọc bản ghi, lần 2 = ghi -> ép hỏng đúng lần ghi.
            if self.writes >= 2:
                raise RuntimeError("mất kết nối DB")
            return self._real()

    service = ImageService(BrokenFactory(session_factory), client=cloud)

    with pytest.raises(RuntimeError):
        await service.create(PROJECT_OWNER, PROJECT_ID, FakeUpload(PNG), "a.png")

    assert cloud.uploaded, "phải upload trước khi ghi DB"
    assert cloud.destroyed == cloud.uploaded, "ảnh mồ côi phải bị xoá"


# --- Chưa cấu hình Cloudinary -----------------------------------------------


async def test_missing_configuration_is_reported_clearly(session_factory, monkeypatch, owner_case):
    """Thiếu biến môi trường → lỗi nói rõ, không phải traceback của SDK."""
    from src.config import get_settings

    # Ép rỗng tường minh: .env của máy dev có thể đã có sẵn biến này, không nên
    # để test xanh chỉ vì tình cờ chưa cấu hình.
    monkeypatch.setattr(get_settings(), "cloudinary_cloud_name", "")
    owner, row_id, _ = owner_case
    service = ImageService(session_factory)  # không truyền client giả

    with pytest.raises(ImageRejectedError) as exc:
        await service.create(owner, row_id, FakeUpload(PNG), "a.png")

    assert exc.value.error_code == "STORAGE_NOT_CONFIGURED"


# --- Tầng HTTP: mã trạng thái và ánh xạ lỗi ---------------------------------
# Các test trên gọi thẳng service. Nhóm này đi qua router thật để chốt phần mà
# service không biết: mã HTTP, dạng body multipart, và trường `file` bắt buộc.


@pytest.fixture
def http(monkeypatch, session_factory, cloud):
    """Ghim router dùng DB test và Cloudinary giả.

    Phải patch CẢ `get_session_factory`: các endpoint ĐỌC (`GET /projects`,
    `GET /areas`) tự mở session ngay trong router, không đi qua ImageService.
    Không patch thì chúng đọc database dev và kết quả phụ thuộc dữ liệu sót ở đó.
    """
    import src.api.dashboard as dashboard

    monkeypatch.setattr(dashboard, "ImageService", lambda: ImageService(session_factory, client=cloud))
    monkeypatch.setattr(dashboard, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.services.absorption.get_session_factory", lambda: session_factory)
    return cloud


def _url(kind: str, owner_id) -> str:
    table = "projects" if kind == "project" else "areas"
    return f"/api/v1/{table}/{owner_id}/image"


@pytest.mark.parametrize("kind", ["project", "area"])
async def test_http_missing_file_field_is_422(client, http, kind, area_id):
    """Không gửi phần `file` → FastAPI chặn ở validate, không chạm Cloudinary."""
    owner_id = PROJECT_ID if kind == "project" else area_id

    response = await client.post(_url(kind, owner_id))

    assert response.status_code == 422
    assert http.uploaded == []


@pytest.mark.parametrize("kind", ["project", "area"])
async def test_http_full_lifecycle_status_codes(client, http, kind, area_id):
    """201 tạo → 200 xem → 409 tạo lại → 200 thay → 204 xoá → 404 xem lại."""
    owner_id = PROJECT_ID if kind == "project" else area_id
    url = _url(kind, owner_id)
    files = {"file": ("a.png", PNG, "image/png")}

    created = await client.post(url, files=files)
    assert created.status_code == 201, created.text
    assert created.json()["public_id"]

    assert (await client.get(url)).status_code == 200

    duplicate = await client.post(url, files={"file": ("b.png", PNG, "image/png")})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["error_code"] == "IMAGE_ALREADY_EXISTS"

    assert (await client.put(url, files={"file": ("c.png", PNG, "image/png")})).status_code == 200
    assert (await client.delete(url)).status_code == 204

    gone = await client.get(url)
    assert gone.status_code == 404
    assert gone.json()["detail"]["error_code"] == "IMAGE_NOT_FOUND"


@pytest.mark.parametrize("kind", ["project", "area"])
async def test_http_unsupported_type_is_415(client, http, kind, area_id):
    owner_id = PROJECT_ID if kind == "project" else area_id

    response = await client.post(_url(kind, owner_id), files={"file": ("tai-lieu.pdf", b"%PDF-1.4", "application/pdf")})

    assert response.status_code == 415
    assert response.json()["detail"]["error_code"] == "UNSUPPORTED_IMAGE_FORMAT"


@pytest.mark.parametrize("kind", ["project", "area"])
async def test_http_missing_owner_is_404(client, http, kind):
    response = await client.post(_url(kind, uuid.uuid4()), files={"file": ("a.png", PNG, "image/png")})

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] in ("PROJECT_NOT_FOUND", "AREA_NOT_FOUND")


@pytest.mark.parametrize("kind", ["project", "area"])
async def test_http_malformed_uuid_is_422(client, http, kind):
    response = await client.get(_url(kind, "khong-phai-uuid"))

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "INVALID_UUID"


async def test_http_upload_failure_is_502(client, http, area_id):
    """Cloudinary hỏng là sự cố hạ tầng → 502, không phải lỗi request của client."""
    http.fail_upload = True

    response = await client.post(_url("project", PROJECT_ID), files={"file": ("a.png", PNG, "image/png")})

    assert response.status_code == 502
    assert response.json()["detail"]["error_code"] == "STORAGE_UPLOAD_FAILED"


async def test_http_delete_failure_is_502(client, http, area_id):
    await client.post(_url("project", PROJECT_ID), files={"file": ("a.png", PNG, "image/png")})
    http.fail_destroy = True

    response = await client.delete(_url("project", PROJECT_ID))

    assert response.status_code == 502
    assert response.json()["detail"]["error_code"] == "STORAGE_DELETE_FAILED"


async def test_http_empty_file_is_422(client, http):
    response = await client.post(_url("project", PROJECT_ID), files={"file": ("a.png", b"", "image/png")})

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "EMPTY_IMAGE"


async def test_cover_image_url_appears_in_the_list_endpoint(client, http, session_factory):
    """Người dùng "xem ảnh hiện tại" ngay trên danh sách, không phải gọi thêm."""
    await client.post(_url("project", PROJECT_ID), files={"file": ("a.png", PNG, "image/png")})

    response = await client.get("/api/v1/projects")

    assert response.status_code == 200, response.text
    listed = response.json()
    ids = [p["project_id"] for p in listed]
    assert str(PROJECT_ID) in ids, f"GET /projects trả {ids}, thiếu {PROJECT_ID}"
    row = [p for p in listed if p["project_id"] == str(PROJECT_ID)][0]
    assert row["cover_image_url"].startswith("https://")
