"""Test các endpoint ĐỌC trên dữ liệu do `scripts/seed_dev.py` nạp.

Mục đích khác với test_catalog.py: ở đó mỗi test tự dựng vài dòng vừa đủ cho một
nhánh logic. Ở đây câu hỏi là ngược lại — bộ dữ liệu mẫu có thực sự dùng được
không: biểu đồ có đủ điểm chưa, thẻ tổng hợp có ra số chưa, và hình dạng JSON có
đúng thứ mà `frontend/src/api/endpoints.js` đang bóc tách không.

Chạy: `TEST_TARGET=tests/test_api/test_seeded_dashboard.py bash scripts/test_db.sh`
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from scripts.seed_dev import build_dataset, seed
from src.main import app
from tests.conftest import DASHBOARD_AUTH_HEADER, db_skip_reason

# Dọn dẹp dùng chung ở `tests/conftest.py` (Phase 1). Module này vốn đã TRUNCATE
# toàn bộ nên chuyển sang là tương đương về hành vi.
_SKIP = db_skip_reason()

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]

API = "/api/v1"
TABLE_ORDER = [name for name, _ in build_dataset()]


@pytest_asyncio.fixture
async def http(truncate_all, monkeypatch):
    """DB đã seed + client HTTP trỏ vào đúng database test.

    Các router lấy session factory toàn cục từ `src.db`; không patch thì chúng đọc
    database dev và test sẽ pass vì lý do sai.

    `truncate_all` dọn cả trước lẫn sau, nên phần dọn dẹp cuối fixture này đã
    chuyển hết về đó.
    """
    engine = truncate_all
    await seed(reset=False, engine=engine)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    for target in (
        "src.api.dashboard.get_session_factory",
        "src.services.absorption.get_session_factory",
        "src.api.files.get_session_factory",
    ):
        monkeypatch.setattr(target, lambda: factory, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=DASHBOARD_AUTH_HEADER) as client:
        yield client

    # Không dọn ở đây nữa: `truncate_all` đã TRUNCATE lại sau khi fixture này
    # thoát. Trả database về rỗng vẫn là bắt buộc — fixture dọn dẹp của các module
    # khác chỉ biết vài bảng của luồng nạp file, để sót dữ liệu seed là chúng vỡ.


async def _first_project(http) -> dict:
    """Dự án mặc định của giao diện: `activeProjectId()` lấy đúng phần tử đầu."""
    rows = (await http.get(f"{API}/projects")).json()
    return rows[0]


async def _series_by_area(http) -> dict[str, list[dict]]:
    """{area_id: các điểm hấp thụ} cho mọi phân khu của mọi dự án."""
    series: dict[str, list[dict]] = {}
    for project in (await http.get(f"{API}/projects")).json():
        areas = (await http.get(f"{API}/areas", params={"project_id": project["project_id"]})).json()
        for area in areas:
            body = (await http.get(f"{API}/absorption", params={"area_id": area["area_id"], "calculator": "legacy_aggregate"})).json()
            series[area["area_id"]] = body["points"]
    return series


# --------------------------------------------------------------------------
# Hợp đồng với frontend
# --------------------------------------------------------------------------


async def test_list_projects_returns_the_fields_the_frontend_reads(http):
    """`activeProjectId()` lấy `rows[0].project_id`; thiếu khoá này là trang trắng."""
    response = await http.get(f"{API}/projects")
    assert response.status_code == 200

    rows = response.json()
    assert len(rows) >= 4, "Seed phải có đủ dự án để chọn/lọc, không chỉ một"
    for row in rows:
        assert {"project_id", "name", "launch_date", "status"} <= set(row)
        assert row["status"] in ("pending", "active", "rejected", "archived")
    assert all(row["name"].startswith("DEMO ") for row in rows)


async def test_list_areas_returns_the_fields_the_frontend_reads(http):
    """`listAreas()` ánh xạ `area_id` -> `id` và đọc `units_remaining`."""
    project = await _first_project(http)
    response = await http.get(f"{API}/areas", params={"project_id": project["project_id"]})
    assert response.status_code == 200

    rows = response.json()
    assert rows, "Dự án đầu tiên phải có phân khu, nếu không Dashboard rỗng"
    for row in rows:
        assert {"area_id", "area_name", "unit_type", "bedrooms", "area_sqm", "total_units"} <= set(row)


async def test_areas_of_an_unknown_project_is_an_empty_list_not_an_error(http):
    response = await http.get(f"{API}/areas", params={"project_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 200
    assert response.json() == []


async def test_malformed_project_id_is_rejected_with_422(http):
    response = await http.get(f"{API}/areas", params={"project_id": "khong-phai-uuid"})
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Chuỗi thời gian và tổng hợp
# --------------------------------------------------------------------------


async def test_absorption_series_has_enough_points_to_draw(http):
    series = await _series_by_area(http)

    charted = {area_id: points for area_id, points in series.items() if points}
    assert len(charted) >= 3, "Cần nhiều phân khu có dữ liệu để so sánh trên Dashboard"

    for area_id, points in charted.items():
        assert len(points) >= 30, f"{area_id}: chuỗi quá ngắn để vẽ ({len(points)} điểm)"
        for point in points:
            assert {"stat_date", "units_sold", "velocity_7d", "velocity_30d"} <= set(point)
        # Chuỗi phải tăng dần theo ngày, nếu không biểu đồ sẽ vẽ ngoằn ngoèo.
        dates = [p["stat_date"] for p in points]
        assert dates == sorted(dates)


async def test_the_default_project_the_ui_opens_is_not_empty(http):
    """`activeProjectId()` lấy dự án đầu danh sách; dự án đó mà rỗng thì người mở
    demo lên sẽ thấy dashboard trắng và tưởng hệ thống hỏng."""
    project = await _first_project(http)
    areas = (await http.get(f"{API}/areas", params={"project_id": project["project_id"]})).json()
    assert areas

    with_data = 0
    for area in areas:
        body = (await http.get(f"{API}/absorption", params={"area_id": area["area_id"], "calculator": "legacy_aggregate"})).json()
        with_data += bool(body["points"])
    assert with_data >= 1, f"Dự án mặc định '{project['name']}' không có phân khu nào có dữ liệu"


async def test_absorption_series_respects_the_date_filter(http):
    series = await _series_by_area(http)
    area_id = next(aid for aid, points in series.items() if points)

    full = (await http.get(f"{API}/absorption", params={"area_id": area_id, "calculator": "legacy_aggregate"})).json()["points"]
    cutoff = full[len(full) // 2]["stat_date"]

    narrowed = (await http.get(f"{API}/absorption", params={"area_id": area_id, "from": cutoff, "calculator": "legacy_aggregate"})).json()["points"]
    assert narrowed, "Lọc theo ngày không được trả rỗng khi khoảng vẫn còn dữ liệu"
    assert len(narrowed) < len(full)
    assert min(p["stat_date"] for p in narrowed) >= cutoff


async def test_absorption_summary_is_computed_not_null(http):
    project = await _first_project(http)
    response = await http.get(f"{API}/absorption/summary", params={"project_id": project["project_id"], "calculator": "legacy_aggregate"})
    assert response.status_code == 200

    body = response.json()
    assert {"units_remaining", "units_sold", "avg_velocity_30d"} <= set(body)
    assert body["units_sold"] > 0, "Seed phải sinh ra doanh số để thẻ tổng hợp có số thật"
    assert body["units_remaining"] >= 0


async def test_series_contains_both_observed_and_gap_filled_points(http):
    """Nhánh lấp đầy khoảng trống chỉ chạy được nếu dữ liệu có cả hai loại điểm."""
    observed: set[bool] = set()
    quality: set[str] = set()
    for points in (await _series_by_area(http)).values():
        for point in points:
            observed.add(point["is_observed"])
            quality.add(point["data_quality_status"])
    assert observed == {True, False}
    assert {"ok", "warning"} <= quality


# --------------------------------------------------------------------------
# Lịch sử upload
# --------------------------------------------------------------------------


async def _all_files(http) -> list[dict]:
    items: list[dict] = []
    for project in (await http.get(f"{API}/projects")).json():
        body = (await http.get(f"{API}/files", params={"project_id": project["project_id"]})).json()
        items.extend(body["items"])
    return items


async def test_file_history_covers_every_status(http):
    """Trạng thái trả ra là TỪ VỰNG API (`DB_STATUS_TO_API`), không phải từ vựng DB:
    `processing`->`parsing`, `completed`->`done`. Seed phải phủ hết cả bốn."""
    seen: set[str] = set()
    for project in (await http.get(f"{API}/projects")).json():
        items = (await http.get(f"{API}/files", params={"project_id": project["project_id"]})).json()["items"]
        for item in items:
            assert {"file_id", "filename", "status", "rows_ok", "rows_failed"} <= set(item)
        # Mới nhất trước — hợp đồng ghi trong docstring của endpoint.
        uploaded = [item["uploaded_at"] for item in items]
        assert uploaded == sorted(uploaded, reverse=True)
        seen |= {item["status"] for item in items}

    assert seen == {"pending", "parsing", "done", "failed"}


async def test_error_rows_of_a_failed_file_are_readable(http):
    failed = next(item for item in await _all_files(http) if item["status"] == "failed")

    errors = (await http.get(f"{API}/files/{failed['file_id']}/errors")).json()["errors"]
    assert len(errors) == failed["rows_failed"]
    for error in errors:
        assert {"row_number", "column_name", "error_code", "message"} <= set(error)
    # Có lỗi không gắn với cột nào — giao diện phải chịu được column_name rỗng.
    assert any(error["column_name"] is None for error in errors)


async def test_error_csv_download_is_not_empty(http):
    failed = next(item for item in await _all_files(http) if item["status"] == "failed")

    response = await http.get(f"{API}/files/{failed['file_id']}/errors.csv")
    assert response.status_code == 200
    assert len(response.text.strip().splitlines()) > 1


async def test_status_of_an_unknown_file_is_404(http):
    response = await http.get(f"{API}/files/00000000-0000-0000-0000-000000000000/status")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Ảnh bìa
# --------------------------------------------------------------------------


async def test_projects_with_and_without_a_cover_image_are_both_present(http):
    """Có ảnh -> 200, chưa có ảnh -> 404. Seed phải có cả hai để test được nhánh."""
    rows = await http.get(f"{API}/projects")
    codes = set()
    for row in rows.json():
        response = await http.get(f"{API}/projects/{row['project_id']}/image")
        codes.add(response.status_code)
        if response.status_code == 200:
            assert {"url", "public_id"} <= set(response.json())
    assert codes == {200, 404}
