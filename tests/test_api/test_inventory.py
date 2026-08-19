"""API tồn kho S3, tóm tắt hấp thụ mở rộng, và chạy song song hai bộ tính.

Chạy qua ASGI in-process với `client` fixture của tests/conftest.py, giống
tests/test_api/test_sync.py.

Không có tầng xác thực trong mã nguồn (MVP 3 mới làm — SRS §2.4); test chốt lại
hiện trạng đó thay vì giả vờ có auth.
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
    crm_source_records,
    deals,
    inventory_snapshots,
    sales_records,
    sync_payloads,
    units,
    upload_errors,
    upload_files,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _refuses_to_wipe(url: str | None) -> str:
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' vì tên không kết thúc bằng '_test'."
    return ""


_SKIP_REASON = _refuses_to_wipe(TEST_DATABASE_URL)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or ""),
]

PROJECT_ID = uuid.UUID("e1f2a3b4-c5d6-4789-9012-3456789abcde")
INSTANCE = "crm-project-a"
SOLD_AT = "2026-08-05T10:00:00Z"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def db_env(session_factory, monkeypatch):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id))))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(crm_source_records))
        await session.execute(sa.delete(absorption_daily).where(absorption_daily.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(sales_records).where(sales_records.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(inventory_snapshots).where(inventory_snapshots.c.area_id.in_(area_ids)))
        # Từ 0010, fk_sync_payloads_sync_run_id là RESTRICT: phải xoá payload
        # TRƯỚC khi xoá lô. Đó chính là điểm của ràng buộc — xoá lịch sử
        # payload phải là hành động cố ý.
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Inv', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            await session.execute(
                sa.insert(areas),
                [
                    {
                        "id": uuid.uuid4(),
                        "project_id": PROJECT_ID,
                        "area_name": name,
                        "unit_type": "2PN",
                        "bedrooms": 2,
                        "area_sqm": 75,
                        "total_units": 50,
                        "created_at": datetime.now(UTC),
                    }
                    for name in ("A1", "A2")
                ],
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


@pytest_asyncio.fixture
async def api_key(session_factory, db_env):
    """Khoá thật cho instance mà `_sync()` khai.

    Từ Phase 3 `POST /sync/{entity}` bắt buộc xác thực, nên module này — vốn dựng
    dữ liệu bằng chính đường đồng bộ — phải có khoá.
    """
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            issued = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=INSTANCE, label="inventory test"
            )
    yield issued.api_key
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.text("DELETE FROM sync_credentials WHERE id = :i"), {"i": issued.credential_id})


@pytest_asyncio.fixture
async def client(api_key):
    """Che fixture `client` của conftest bằng một client mang sẵn khoá hợp lệ.

    Kèm cả header dashboard (Phase E) — file này gọi `/inventory`/`/deals`, giờ
    đòi tối thiểu `business_viewer` VÀ phạm vi dự án; `X-API-Key` (đường `/sync`)
    và `Authorization: Bearer` (đường đọc dashboard) là HAI cơ chế xác thực khác
    nhau, cùng cần thiết để test này thấy được lô nó vừa gửi.
    """
    from httpx import ASGITransport, AsyncClient

    from src.main import app
    from tests.conftest import DASHBOARD_AUTH_HEADER

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": api_key, **DASHBOARD_AUTH_HEADER},
    ) as authorized:
        yield authorized


_BATCH = {"n": 0}


def _batch() -> str:
    _BATCH["n"] += 1
    return f"inv-{uuid.uuid4().hex[:8]}-{_BATCH['n']}"


async def _sync(client, entity, records):
    payload = {
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "source_entity": entity,
        "schema_version": 1,
        "external_batch_id": _batch(),
        "project_id": str(PROJECT_ID),
        "records": records,
    }
    return await client.post(f"/api/v1/sync/{entity}", json=payload)


def _unit(record_id, *, code, status="available", area="A1", revision=1):
    return {
        "source_record_id": record_id,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"area_name": area, "unit_type": "2PN", "unit_code": code, "status": status},
    }


def _deal(record_id, *, unit, status, revision=1):
    data = {"external_unit_id": unit, "status": status}
    if status == "reserved":
        data["reserved_at"] = "2026-08-01T00:00:00Z"
    elif status == "sold":
        data["reserved_at"] = "2026-08-01T00:00:00Z"
        data["sold_at"] = SOLD_AT
    return {"source_record_id": record_id, "operation": "upsert", "source_revision": revision, "data": data}


async def _seed_stock(client):
    """2 căn A1 (1 bán, 1 giữ chỗ), 1 căn A2 còn trống, 1 căn A1 blocked."""
    await _sync(
        client,
        "units",
        [
            _unit("U-1", code="A1-01"),
            _unit("U-2", code="A1-02"),
            _unit("U-3", code="A1-03", status="blocked"),
            _unit("U-4", code="A2-01", area="A2"),
        ],
    )
    await _sync(
        client,
        "deals",
        [_deal("D-1", unit="U-1", status="sold"), _deal("D-2", unit="U-2", status="reserved")],
    )


# --- GET /inventory ---------------------------------------------------------


async def test_inventory_returns_counts_derived_from_units_and_deals(client):
    await _seed_stock(client)

    response = await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["calculator"] == "domain_units_deals"
    assert body["totals"]["units_sold"] == 1
    assert body["totals"]["units_reserved"] == 1
    assert body["totals"]["units_blocked"] == 1
    assert body["totals"]["total_units"] == 3, "căn blocked nằm ngoài quỹ bán được"
    assert body["totals"]["units_remaining"] == 1
    assert body["anomalies"] == []

    by_area = {row["area_name"]: row for row in body["areas"]}
    assert by_area["A1"]["units_sold"] == 1
    assert by_area["A1"]["units_reserved"] == 1
    assert by_area["A2"]["units_remaining"] == 1


async def test_inventory_filters_by_area(client):
    await _seed_stock(client)
    areas_body = (await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}")).json()["areas"]
    a2_id = next(row["area_id"] for row in areas_body if row["area_name"] == "A2")

    response = await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}&area_id={a2_id}")

    body = response.json()
    assert [row["area_name"] for row in body["areas"]] == ["A2"]
    assert body["totals"]["units_sold"] == 0
    assert body["totals"]["units_remaining"] == 1


async def test_inventory_units_are_opt_in(client):
    await _seed_stock(client)

    without = (await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}")).json()
    assert without["units"] == [], "danh sách căn phải là tuỳ chọn"

    with_units = (await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}&include_units=true")).json()
    codes = [row["unit_code"] for row in with_units["units"]]
    assert codes == sorted(codes), "thứ tự phải tất định để phân trang ổn định"
    assert len(codes) == 4


async def test_inventory_filters_units_by_unit_and_deal_status(client):
    await _seed_stock(client)

    blocked = (
        await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}&include_units=true&unit_status=blocked")
    ).json()
    assert [row["unit_code"] for row in blocked["units"]] == ["A1-03"]

    reserved = (
        await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}&include_units=true&deal_status=reserved")
    ).json()
    assert [row["unit_code"] for row in reserved["units"]] == ["A1-02"]
    assert reserved["units"][0]["active_deal_status"] == "reserved"


async def test_inventory_hides_tombstoned_units_unless_asked(client):
    await _seed_stock(client)
    await _sync(client, "units", [{"source_record_id": "U-4", "operation": "delete", "source_revision": 99}])

    hidden = (await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}&include_units=true")).json()
    assert "A2-01" not in [row["unit_code"] for row in hidden["units"]]

    shown = (
        await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}&include_units=true&include_deleted=true")
    ).json()
    deleted = next(row for row in shown["units"] if row["unit_code"] == "A2-01")
    assert deleted["deleted_at"] is not None


async def test_inventory_paginates(client):
    await _seed_stock(client)
    page = (await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}&include_units=true&limit=2&offset=0")).json()
    assert len(page["units"]) == 2


async def test_inventory_404_and_422(client):
    assert (await client.get(f"/api/v1/inventory?project_id={uuid.uuid4()}")).status_code == 404

    bad = await client.get("/api/v1/inventory?project_id=khong-phai-uuid")
    assert bad.status_code == 422
    assert bad.json()["detail"]["error_code"] == "INVALID_UUID"


async def test_inventory_surfaces_invalid_relationships(client):
    """Giao dịch treo trên căn đã xoá phải hiện ra, không bị đếm im lặng."""
    await _seed_stock(client)
    await _sync(client, "units", [{"source_record_id": "U-1", "operation": "delete", "source_revision": 99}])

    body = (await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}")).json()

    assert body["totals"]["units_sold"] == 0
    assert [a["code"] for a in body["anomalies"]] == ["DEAL_ON_DELETED_UNIT"]


# --- /absorption/summary mở rộng --------------------------------------------


async def test_summary_keeps_existing_fields_and_defaults_to_legacy(client):
    """Trường cũ còn nguyên; bộ tính mặc định vẫn là bộ cũ — KHÔNG tự cắt sang."""
    await _seed_stock(client)

    body = (await client.get(f"/api/v1/absorption/summary?project_id={PROJECT_ID}")).json()

    assert set(body) >= {"units_remaining", "units_sold", "avg_velocity_30d", "updated_at"}
    assert body["calculator"] == "legacy_aggregate"
    # Bộ tính cũ đọc `sales_records` (rỗng) — số của bản sao CRM KHÔNG rò sang.
    assert body["units_sold"] == 0
    assert body["units_reserved"] is None, "dữ liệu tổng hợp không dựng lại được số giữ chỗ; NULL chứ không phải 0"


async def test_summary_can_be_asked_for_the_domain_calculator(client):
    await _seed_stock(client)

    body = (
        await client.get(f"/api/v1/absorption/summary?project_id={PROJECT_ID}&calculator=domain_units_deals")
    ).json()

    assert body["calculator"] == "domain_units_deals"
    assert body["units_sold"] == 1
    assert body["units_reserved"] == 1
    assert body["units_remaining"] == 1


async def test_summary_rejects_an_unknown_calculator(client):
    response = await client.get(f"/api/v1/absorption/summary?project_id={PROJECT_ID}&calculator=khong-ton-tai")

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "UNKNOWN_CALCULATOR"


# --- Chạy song song ---------------------------------------------------------


async def test_parallel_run_reports_differences_and_names_the_production_calculator(client):
    await _seed_stock(client)

    body = (await client.get(f"/api/v1/absorption/parallel-run?project_id={PROJECT_ID}")).json()

    assert body["matches"] is False
    assert body["legacy_units_sold"] == 0
    assert body["domain_units_sold"] == 1
    assert body["domain_units_reserved"] == 1
    assert {d["metric"] for d in body["differences"]} >= {"units_sold", "units_reserved"}
    # Điểm mấu chốt: chạy song song KHÔNG cắt sang.
    assert body["production_calculator"] == "legacy_aggregate"


async def test_parallel_run_does_not_change_what_the_dashboard_reads(client):
    await _seed_stock(client)
    before = (await client.get(f"/api/v1/absorption/summary?project_id={PROJECT_ID}")).json()

    await client.get(f"/api/v1/absorption/parallel-run?project_id={PROJECT_ID}")

    after = (await client.get(f"/api/v1/absorption/summary?project_id={PROJECT_ID}")).json()
    assert after == before


async def test_parallel_run_404_for_unknown_project(client):
    assert (await client.get(f"/api/v1/absorption/parallel-run?project_id={uuid.uuid4()}")).status_code == 404


# --- Không phá API đã có ----------------------------------------------------


async def test_sync_and_csv_upload_routes_still_work(client):
    """S3 không đụng vào API của S1/S2."""
    response = await _sync(client, "units", [_unit("U-9", code="A1-09")])
    assert response.status_code == 202
    assert response.json()["projections"]["inserted"] == 1

    detail = await client.get(f"/api/v1/sync-runs/{response.json()['sync_run_id']}")
    assert detail.status_code == 200
    assert detail.json()["source_entity"] == "units"

    # 422 vì thiếu form field, KHÔNG phải 404 — route CSV vẫn được đăng ký.
    assert (await client.post("/api/v1/files/upload")).status_code == 422


async def test_unknown_deal_status_does_not_produce_business_results(client):
    """Trạng thái lạ: lô không thành công, bảng nghiệp vụ không đổi, lỗi có cấu trúc."""
    await _sync(client, "units", [_unit("U-1", code="A1-01")])
    response = await _sync(client, "deals", [_deal("D-1", unit="U-1", status="khong-ton-tai")])

    body = response.json()
    assert body["status"] == "failed"
    assert body["projections"]["rejected"] == 1
    assert body["projections"]["inserted"] == 0

    errors = (await client.get(f"/api/v1/sync-runs/{body['sync_run_id']}/errors")).json()["errors"]
    assert errors[0]["error_code"] == "UNKNOWN_DEAL_STATUS"
    assert errors[0]["json_path"] == "$.records[0].data.status"
    assert errors[0]["field_name"] == "status"

    inventory = (await client.get(f"/api/v1/inventory?project_id={PROJECT_ID}")).json()
    assert inventory["totals"]["units_sold"] == 0
    assert inventory["totals"]["units_reserved"] == 0
