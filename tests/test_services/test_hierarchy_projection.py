"""Phase D — chiếu Project/Area vào bản sao, và v2 chấp nhận được ở runtime.

Chạy qua ĐÚNG đường thật: `JsonPayloadParser` → `SyncRunService` →
`SourceIdentityService` → `DomainProjector`, cùng khuôn với
`tests/test_services/test_domain_projection.py` (Unit/Deal) — file đó KHÔNG bị
sửa một dòng nào ở Phase D, và đứng nguyên là bằng chứng hồi quy Unit/Deal.

Chạy: TEST_TARGET=tests/test_services/test_hierarchy_projection.py bash scripts/test_db.sh
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import (
    areas,
    crm_source_records,
    deals,
    project_price_observations,
    projects,
    units,
    upload_errors,
    upload_files,
)
from src.services.json_payload import JsonPayloadParser
from src.services.sync_runs import SyncRejectedError, SyncRunService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


def _refuses_to_wipe(url: str | None) -> str:
    if not url:
        return "Không có TEST_DATABASE_URL/DATABASE_URL — bỏ qua test cần DB thật"
    name = urlsplit(url).path.lstrip("/")
    if not name.endswith("_test"):
        return f"Từ chối xoá dữ liệu trên database '{name}' vì tên không kết thúc bằng '_test'."
    return ""


_SKIP_REASON = _refuses_to_wipe(TEST_DATABASE_URL)

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "")]

# `source_instance_id` RIÊNG của module — không đụng dữ liệu của
# test_domain_projection.py (INSTANCE="crm-project-a") hay test_sync_concurrency.py.
INSTANCE = "phase-d-hierarchy"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async def wipe(session):
        await session.execute(
            sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id == INSTANCE)
        )
        runs = sa.select(upload_files.c.id).where(upload_files.c.source_instance_id == INSTANCE)
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.source_instance_id == INSTANCE))
        # units/deals/areas/projects của instance này — JOIN qua source_instance_id
        # trực tiếp trên mỗi bảng (cả bốn đều mang cột đó).
        unit_ids = sa.select(units.c.id).where(units.c.source_instance_id == INSTANCE)
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(unit_ids)))
        # `project_price_observations.unit_id` là FK RESTRICT (0027) — phải dọn
        # TRƯỚC `units`, không thì DELETE units bên dưới nổ khoá ngoại (0008).
        await session.execute(sa.delete(project_price_observations).where(project_price_observations.c.unit_id.in_(unit_ids)))
        await session.execute(sa.delete(units).where(units.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(areas).where(areas.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(projects).where(projects.c.source_instance_id == INSTANCE))

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)


# --- Helper -------------------------------------------------------------------

_BATCH = {"n": 0}


def _next_batch() -> str:
    _BATCH["n"] += 1
    return f"phase-d-{uuid.uuid4().hex[:8]}-{_BATCH['n']}"


async def _sync(
    session_factory,
    entity,
    records,
    *,
    batch=None,
    external_project_id=None,
    project_id=None,
    schema_version=2,
):
    payload = {
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "source_entity": entity,
        "schema_version": schema_version,
        "external_batch_id": batch or _next_batch(),
        "records": records,
    }
    if project_id is not None:
        payload["project_id"] = str(project_id)
    if external_project_id is not None:
        payload["external_project_id"] = external_project_id
    envelope = JsonPayloadParser().parse(payload)
    return await SyncRunService(session_factory).run(envelope)


def _project(record_id="P-1", *, name="Khu do thi Ben Xanh", launch_date="2026-06-01", revision=1):
    return {
        "source_record_id": record_id,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"name": name, "launch_date": launch_date},
    }


def _area(record_id="A-1", *, name="A1", unit_type="2PN", bedrooms=2, sqm=68.5, total=120, revision=1):
    return {
        "source_record_id": record_id,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"area_name": name, "unit_type": unit_type, "bedrooms": bedrooms, "area_sqm": sqm, "total_units": total},
    }


_NO_PRICE = object()


def _unit_v2(
    record_id="U-1", *, external_area_id="A-1", code="A1-01", status="available", revision=1, listing_price=_NO_PRICE
):
    data = {"external_area_id": external_area_id, "unit_code": code, "status": status}
    # Sentinel, không `None` mặc định: `listing_price=None` (tường minh) và
    # KHÔNG truyền tham số này chút nào là hai thứ khác nhau ở phía nhận (0008)
    # — "vắng mặt" phải thật sự vắng mặt trong `data`, không phải một khoá
    # mang giá trị `None`.
    if listing_price is not _NO_PRICE:
        data["listing_price"] = listing_price
    return {
        "source_record_id": record_id,
        "operation": "upsert",
        "source_revision": revision,
        "data": data,
    }


def _deal(record_id="D-1", *, unit="U-1", status="reserved", revision=1, **stamps):
    data = {"external_unit_id": unit, "status": status}
    if status == "reserved":
        data.setdefault("reserved_at", "2026-08-10T09:00:00Z")
    data.update(stamps)
    return {"source_record_id": record_id, "operation": "upsert", "source_revision": revision, "data": data}


def _delete(record_id, *, revision=99):
    return {"source_record_id": record_id, "operation": "delete", "source_revision": revision}


async def _project_row(session_factory, external_id="P-1") -> dict | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                sa.select(projects).where(
                    projects.c.source_instance_id == INSTANCE, projects.c.external_id == external_id
                )
            )
        ).mappings().one_or_none()
    return dict(row) if row is not None else None


async def _area_row(session_factory, external_id="A-1") -> dict | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                sa.select(areas).where(areas.c.source_instance_id == INSTANCE, areas.c.external_id == external_id)
            )
        ).mappings().one_or_none()
    return dict(row) if row is not None else None


async def _unit_row(session_factory, external_id="U-1") -> dict | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                sa.select(units).where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == external_id)
            )
        ).mappings().one_or_none()
    return dict(row) if row is not None else None


async def _price_rows(session_factory, unit_external_id="U-1") -> list[dict]:
    """Mọi dòng `project_price_observations` của một căn, cũ → mới (0008)."""
    unit = await _unit_row(session_factory, unit_external_id)
    if unit is None:
        return []
    async with session_factory() as session:
        rows = (
            await session.execute(
                sa.select(project_price_observations)
                .where(project_price_observations.c.unit_id == unit["id"])
                .order_by(project_price_observations.c.effective_from.asc())
            )
        ).mappings().all()
    return [dict(r) for r in rows]


async def _seed_project(session_factory, record_id="P-1", **kwargs):
    result = await _sync(session_factory, "projects", [_project(record_id, **kwargs)], external_project_id=record_id)
    assert result.status == "completed", result
    return await _project_row(session_factory, record_id)


async def _seed_project_and_area(session_factory, project_id="P-1", area_id="A-1", **area_kwargs):
    await _seed_project(session_factory, project_id)
    result = await _sync(session_factory, "areas", [_area(area_id, **area_kwargs)], external_project_id=project_id)
    assert result.status == "completed", result
    return await _area_row(session_factory, area_id)


# ═══════════════════════════════════════════════════════════════════════════
# D1 — v2 chấp nhận được ở runtime
# ═══════════════════════════════════════════════════════════════════════════


def test_supported_schema_versions_is_one_and_two():
    from src.services.json_payload import SUPPORTED_SCHEMA_VERSIONS

    assert SUPPORTED_SCHEMA_VERSIONS == frozenset({1, 2})


def test_unknown_schema_version_is_rejected():
    from src.services.json_payload import EnvelopeError, JsonPayloadParser

    payload = {
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "source_entity": "projects",
        "schema_version": 3,
        "external_batch_id": "b-1",
        "external_project_id": "P-1",
        "records": [],
    }
    with pytest.raises(EnvelopeError) as exc:
        JsonPayloadParser().parse(payload)
    assert exc.value.error_code == "UNSUPPORTED_SCHEMA_VERSION"


def test_v1_unit_payload_still_works_byte_identical_shape(session_factory):
    """v1 KHÔNG đổi — cùng shape `area_name`/`unit_type` cũ, `schema_version=1`,
    `project_id` UUID bắt buộc như trước Phase D."""
    import asyncio

    async def run():
        proj = await _seed_project(session_factory, "P-V1")
        await _sync(session_factory, "areas", [_area("A-V1")], external_project_id="P-V1")
        area = await _area_row(session_factory, "A-V1")

        result = await _sync(
            session_factory,
            "units",
            [
                {
                    "source_record_id": "U-V1",
                    "operation": "upsert",
                    "source_revision": 1,
                    "data": {"area_name": area["area_name"], "unit_type": area["unit_type"], "unit_code": "X-01", "status": "available"},
                }
            ],
            project_id=proj["id"],
            schema_version=1,
        )
        assert result.status == "completed"

    asyncio.run(run())


def test_contract_v2_schema_files_are_byte_identical_across_trees():
    import hashlib
    from pathlib import Path

    backend = Path("src/contracts/crm_sync_v2.schema.json").resolve()
    minicrm = Path("minicrm/contracts/crm_sync_v2.schema.json").resolve()
    assert hashlib.sha256(backend.read_bytes()).hexdigest() == hashlib.sha256(minicrm.read_bytes()).hexdigest()


def test_invalid_v2_payload_is_rejected_by_the_shape_validator():
    from src.services.contract_validation_v2 import ContractValidatorV2

    envelope = {
        "schema_version": 2,
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "external_batch_id": "b-1",
        "sync_mode": "incremental",
        "project_ref": {"external_project_id": "P-1"},
        "source_extracted_at": "2026-08-13T00:00:00Z",
        "records": [
            {
                "entity": "area",
                "operation": "upsert",
                "external_id": "A-1",
                "source_revision": 1,
                "payload": {"area_name": "A1", "unit_type": "2PN"},  # thiếu 3 trường bắt buộc
            }
        ],
    }
    violations = ContractValidatorV2().validate(envelope)
    assert violations


def test_valid_v2_project_area_unit_deal_payloads_are_accepted_by_the_shape_validator():
    from src.services.contract_validation_v2 import ContractValidatorV2

    for entity, payload in (
        ("project", {"name": "X", "launch_date": "2026-01-01"}),
        ("area", {"area_name": "A1", "unit_type": "2PN", "bedrooms": 2, "area_sqm": 68.5, "total_units": 120}),
        ("unit", {"area_ref": {"external_area_id": "A-1"}, "unit_code": "A1-01", "unit_status": "available"}),
        ("deal", {"external_unit_id": "U-1", "deal_status": "lead"}),
    ):
        envelope = {
            "schema_version": 2,
            "source_system": "mini_crm",
            "source_instance_id": INSTANCE,
            "external_batch_id": "b-1",
            "sync_mode": "incremental",
            "project_ref": {"external_project_id": "P-1"},
            "source_extracted_at": "2026-08-13T00:00:00Z",
            "records": [{"entity": entity, "operation": "upsert", "external_id": "X-1", "source_revision": 1, "payload": payload}],
        }
        violations = ContractValidatorV2().validate(envelope)
        assert violations == [], f"{entity}: {violations}"


def _unit_shape_envelope(payload):
    return {
        "schema_version": 2,
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "external_batch_id": "b-1",
        "sync_mode": "incremental",
        "project_ref": {"external_project_id": "P-1"},
        "source_extracted_at": "2026-08-13T00:00:00Z",
        "records": [{"entity": "unit", "operation": "upsert", "external_id": "U-1", "source_revision": 1, "payload": payload}],
    }


@pytest.mark.parametrize("listing_price", [8_600_000_000, 1, 0.5, None])
def test_a_unit_payload_with_a_valid_or_null_listing_price_is_accepted_by_the_shape_validator(listing_price):
    from src.services.contract_validation_v2 import ContractValidatorV2

    payload = {"area_ref": {"external_area_id": "A-1"}, "unit_code": "A1-01", "unit_status": "available"}
    payload["listing_price"] = listing_price
    violations = ContractValidatorV2().validate(_unit_shape_envelope(payload))
    assert violations == [], violations


@pytest.mark.parametrize("listing_price", [0, -1, -8_600_000_000])
def test_a_zero_or_negative_listing_price_is_rejected_by_the_shape_validator(listing_price):
    from src.services.contract_validation_v2 import ContractValidatorV2

    payload = {
        "area_ref": {"external_area_id": "A-1"},
        "unit_code": "A1-01",
        "unit_status": "available",
        "listing_price": listing_price,
    }
    violations = ContractValidatorV2().validate(_unit_shape_envelope(payload))
    assert violations


def test_a_unit_payload_omitting_listing_price_is_still_accepted():
    """Trường TUỲ CHỌN — một hệ nguồn không theo dõi giá vẫn phải hợp lệ."""
    from src.services.contract_validation_v2 import ContractValidatorV2

    payload = {"area_ref": {"external_area_id": "A-1"}, "unit_code": "A1-01", "unit_status": "available"}
    violations = ContractValidatorV2().validate(_unit_shape_envelope(payload))
    assert violations == []


def test_an_unrecognized_price_field_name_is_still_rejected():
    """`additionalProperties: false` vẫn nguyên vẹn — 0008 mở đúng MỘT khoá mới,
    không nới lỏng cổng chặn trường lạ nói chung."""
    from src.services.contract_validation_v2 import ContractValidatorV2

    payload = {
        "area_ref": {"external_area_id": "A-1"},
        "unit_code": "A1-01",
        "unit_status": "available",
        "price_vnd": 1,  # tên KHÔNG khớp `listing_price`
    }
    violations = ContractValidatorV2().validate(_unit_shape_envelope(payload))
    assert violations


# ═══════════════════════════════════════════════════════════════════════════
# D3 — Project projection
# ═══════════════════════════════════════════════════════════════════════════


async def test_new_project_is_inserted(session_factory):
    row = await _seed_project(session_factory, "P-1", name="Khu do thi Ben Xanh")

    assert row is not None
    assert row["name"] == "Khu do thi Ben Xanh"
    assert row["status"] == "active"
    assert row["source_instance_id"] == INSTANCE
    assert row["source_revision"] == 1


async def test_project_update_bumps_revision_and_changes_name(session_factory):
    await _seed_project(session_factory, "P-1", name="Cũ", revision=1)
    result = await _sync(session_factory, "projects", [_project("P-1", name="Mới", revision=2)], external_project_id="P-1")

    assert result.projections["updated"] == 1
    row = await _project_row(session_factory, "P-1")
    assert row["name"] == "Mới"
    assert row["source_revision"] == 2


async def test_project_delete_archives_not_physically_deletes(session_factory):
    await _seed_project(session_factory, "P-1")
    result = await _sync(session_factory, "projects", [_delete("P-1", revision=2)], external_project_id="P-1")

    assert result.projections["tombstoned"] == 1
    row = await _project_row(session_factory, "P-1")
    assert row is not None, "dự án phải VẪN CÒN dòng — không xoá vật lý"
    assert row["status"] == "archived"


async def test_project_lower_revision_is_skip_stale(session_factory):
    await _seed_project(session_factory, "P-1", name="Giữ nguyên", revision=5)
    result = await _sync(session_factory, "projects", [_project("P-1", name="Không được ghi", revision=2)], external_project_id="P-1")

    assert result.decisions["skip_stale"] == 1
    row = await _project_row(session_factory, "P-1")
    assert row["name"] == "Giữ nguyên"
    assert row["source_revision"] == 5


async def test_project_same_revision_same_payload_is_duplicate_noop(session_factory):
    await _seed_project(session_factory, "P-1", name="X", revision=1)
    result = await _sync(session_factory, "projects", [_project("P-1", name="X", revision=1)], external_project_id="P-1")

    assert result.decisions["duplicate_noop"] == 1


async def test_project_same_revision_different_payload_is_conflict(session_factory):
    await _seed_project(session_factory, "P-1", name="Bản gốc", revision=1)
    result = await _sync(session_factory, "projects", [_project("P-1", name="Khác", revision=1)], external_project_id="P-1")

    assert result.decisions["conflict"] == 1
    row = await _project_row(session_factory, "P-1")
    assert row["name"] == "Bản gốc", "đụng độ giữ bản cũ, không tự chọn bên thắng"


async def test_project_archive_with_a_live_area_is_rejected(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")

    result = await _sync(session_factory, "projects", [_delete("P-1", revision=2)], external_project_id="P-1")

    # Lô MỘT bản ghi, bản ghi đó bị từ chối → processed=0 → 'failed' (đúng định
    # nghĩa của _terminal_status: "TOÀN BỘ bản ghi đều hỏng").
    assert result.status == "failed"
    assert result.projections["rejected"] == 1
    row = await _project_row(session_factory, "P-1")
    assert row["status"] == "active", "archive bị từ chối — dự án vẫn active"


async def test_project_archive_after_area_archived_succeeds(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(session_factory, "areas", [_delete("A-1", revision=2)], external_project_id="P-1")

    result = await _sync(session_factory, "projects", [_delete("P-1", revision=2)], external_project_id="P-1")

    assert result.projections["tombstoned"] == 1
    row = await _project_row(session_factory, "P-1")
    assert row["status"] == "archived"


async def test_project_revival_after_archive_is_accepted(session_factory):
    await _seed_project(session_factory, "P-1", revision=1)
    await _sync(session_factory, "projects", [_delete("P-1", revision=2)], external_project_id="P-1")

    result = await _sync(session_factory, "projects", [_project("P-1", name="Sống lại", revision=3)], external_project_id="P-1")

    assert result.projections["updated"] == 1
    row = await _project_row(session_factory, "P-1")
    assert row["status"] == "active"
    assert row["name"] == "Sống lại"


async def test_project_missing_planning_field_is_rejected_not_fabricated(session_factory):
    """`DomainProjector` không tự bịa `launch_date` — thiếu thì từ chối bản ghi."""
    record = {"source_record_id": "P-BAD", "operation": "upsert", "source_revision": 1, "data": {"name": "X"}}
    result = await _sync(session_factory, "projects", [record], external_project_id="P-BAD")

    assert result.projections["rejected"] == 1
    assert await _project_row(session_factory, "P-BAD") is None


# ═══════════════════════════════════════════════════════════════════════════
# D4 — Area projection
# ═══════════════════════════════════════════════════════════════════════════


async def test_area_requires_an_existing_mirrored_project(session_factory):
    """Thiếu Project → TỪ CHỐI CẢ PHONG BÌ (§A5.3), không tạo phân khu mồ côi."""
    with pytest.raises(SyncRejectedError) as exc:
        await _sync(session_factory, "areas", [_area("A-1")], external_project_id="P-KHONG-CO")
    assert exc.value.error_code == "PROJECT_NOT_FOUND"
    assert await _area_row(session_factory, "A-1") is None


async def test_new_area_is_inserted_under_its_project(session_factory):
    row = await _seed_project_and_area(session_factory, "P-1", "A-1", name="A1", total=120)

    assert row is not None
    assert row["area_name"] == "A1"
    assert row["total_units"] == 120
    assert row["status"] == "active"


async def test_area_update_bumps_revision(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1", total=100, revision=1)
    result = await _sync(session_factory, "areas", [_area("A-1", total=130, revision=2)], external_project_id="P-1")

    assert result.projections["updated"] == 1
    row = await _area_row(session_factory, "A-1")
    assert row["total_units"] == 130
    assert row["source_revision"] == 2


async def test_area_delete_archives_not_physically_deletes(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    result = await _sync(session_factory, "areas", [_delete("A-1", revision=2)], external_project_id="P-1")

    assert result.projections["tombstoned"] == 1
    row = await _area_row(session_factory, "A-1")
    assert row is not None
    assert row["status"] == "archived"


async def test_area_cannot_move_between_projects(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _seed_project(session_factory, "P-2")

    result = await _sync(session_factory, "areas", [_area("A-1", revision=2)], external_project_id="P-2")

    assert result.projections["rejected"] == 1
    row = await _area_row(session_factory, "A-1")
    project_1 = await _project_row(session_factory, "P-1")
    assert row["project_id"] == project_1["id"], "phân khu KHÔNG được chuyển dự án"


async def test_area_upsert_into_archived_project_is_rejected(session_factory):
    await _seed_project(session_factory, "P-1")
    await _sync(session_factory, "projects", [_delete("P-1", revision=2)], external_project_id="P-1")

    result = await _sync(session_factory, "areas", [_area("A-1")], external_project_id="P-1")

    assert result.projections["rejected"] == 1
    assert await _area_row(session_factory, "A-1") is None


async def test_area_archive_with_a_live_unit_is_rejected(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(session_factory, "units", [_unit_v2("U-1", external_area_id="A-1")], external_project_id="P-1")

    result = await _sync(session_factory, "areas", [_delete("A-1", revision=2)], external_project_id="P-1")

    assert result.projections["rejected"] == 1
    row = await _area_row(session_factory, "A-1")
    assert row["status"] == "active"


async def test_area_archive_does_not_cascade_to_units(session_factory):
    """Archive Area KHÔNG tự tombstone Unit của nó — không cascade (§A1.8)."""
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(session_factory, "units", [_unit_v2("U-1", external_area_id="A-1")], external_project_id="P-1")
    await _sync(session_factory, "units", [_delete("U-1", revision=2)], external_project_id="P-1")  # xoá unit trước

    result = await _sync(session_factory, "areas", [_delete("A-1", revision=2)], external_project_id="P-1")
    assert result.projections["tombstoned"] == 1  # giờ archive được vì unit đã chết

    async with session_factory() as session:
        unit_deleted_at = await session.scalar(
            sa.select(units.c.deleted_at).where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "U-1")
        )
    assert unit_deleted_at is not None  # do CHÍNH lô units xoá, không phải do archive area


async def test_area_stale_duplicate_conflict_match_project_semantics(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1", total=100, revision=5)

    stale = await _sync(session_factory, "areas", [_area("A-1", total=999, revision=2)], external_project_id="P-1")
    assert stale.decisions["skip_stale"] == 1

    dup = await _sync(session_factory, "areas", [_area("A-1", total=100, revision=5)], external_project_id="P-1")
    assert dup.decisions["duplicate_noop"] == 1

    conflict = await _sync(session_factory, "areas", [_area("A-1", total=777, revision=5)], external_project_id="P-1")
    assert conflict.decisions["conflict"] == 1
    row = await _area_row(session_factory, "A-1")
    assert row["total_units"] == 100


# ═══════════════════════════════════════════════════════════════════════════
# D5 — Unit/Deal scoped projection (v2 area_ref) — Unit/Deal v1 giữ nguyên,
# xem test_domain_projection.py, KHÔNG sửa ở Phase D.
# ═══════════════════════════════════════════════════════════════════════════


async def test_unit_v2_resolves_area_by_external_area_id(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1", name="A1", unit_type="2PN")
    result = await _sync(session_factory, "units", [_unit_v2("U-1", external_area_id="A-1")], external_project_id="P-1")

    assert result.projections["inserted"] == 1
    async with session_factory() as session:
        row = (
            await session.execute(
                sa.select(units).where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "U-1")
            )
        ).mappings().one()
    # `units` KHÔNG có cột `area_name` (đó là cột của `crm_units` bên Mini CRM) —
    # backend chỉ giữ `area_id` (FK) + `unit_type` (đã đúc lại từ phân khu đã tra).
    assert row["unit_type"] == "2PN"
    area = await _area_row(session_factory, "A-1")
    assert row["area_id"] == area["id"]


async def test_unit_v2_rejects_cross_project_area_reference(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _seed_project(session_factory, "P-2")

    result = await _sync(session_factory, "units", [_unit_v2("U-1", external_area_id="A-1")], external_project_id="P-2")

    assert result.projections["rejected"] == 1


async def test_unit_v2_missing_area_rejects_only_that_record(session_factory):
    await _seed_project(session_factory, "P-1")
    result = await _sync(session_factory, "units", [_unit_v2("U-1", external_area_id="A-KHONG-CO")], external_project_id="P-1")

    assert result.projections["rejected"] == 1
    assert result.status == "failed"  # lô một bản ghi, bản ghi đó hỏng


# ═══════════════════════════════════════════════════════════════════════════
# Giá niêm yết cấp căn (0008, 2026-08-23)
# ═══════════════════════════════════════════════════════════════════════════


async def test_unit_with_a_listing_price_creates_one_price_observation(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    result = await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=8_600_000_000)],
        external_project_id="P-1",
    )
    assert result.projections["inserted"] == 1

    rows = await _price_rows(session_factory, "U-1")
    assert len(rows) == 1
    assert rows[0]["official_price"] == Decimal("8600000000.00")
    assert rows[0]["effective_to"] is None
    assert rows[0]["source"] == "mini_crm"


async def test_a_unit_created_without_listing_price_gets_no_observation(session_factory):
    """Vắng mặt = hệ nguồn không nói gì về giá — không suy diễn, không tạo dòng."""
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    result = await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1")], external_project_id="P-1"
    )
    assert result.projections["inserted"] == 1
    assert await _price_rows(session_factory, "U-1") == []


async def test_a_changed_price_closes_the_old_observation_and_opens_a_new_one(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=8_000_000_000, revision=1)],
        external_project_id="P-1",
    )
    result = await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=8_500_000_000, revision=2)],
        external_project_id="P-1",
    )
    assert result.projections["updated"] == 1

    rows = await _price_rows(session_factory, "U-1")
    assert len(rows) == 2
    assert rows[0]["official_price"] == Decimal("8000000000.00")
    assert rows[0]["effective_to"] is not None
    assert rows[1]["official_price"] == Decimal("8500000000.00")
    assert rows[1]["effective_to"] is None


async def test_resending_the_same_price_creates_no_duplicate_observation(session_factory):
    """Idempotent: gửi lại lô cũ (hoặc một lô mới mang cùng giá) không tạo dòng
    trùng — đúng yêu cầu 'no duplicate price observations on replay'."""
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=8_000_000_000, revision=1)],
        external_project_id="P-1",
    )
    result = await _sync(
        session_factory,
        "units",
        # Trạng thái khác (status), nhưng CÙNG giá — cùng một lô update thật, không
        # phải một lần gửi lại y hệt.
        [_unit_v2("U-1", external_area_id="A-1", status="blocked", listing_price=8_000_000_000, revision=2)],
        external_project_id="P-1",
    )
    assert result.projections["updated"] == 1

    rows = await _price_rows(session_factory, "U-1")
    assert len(rows) == 1
    assert rows[0]["effective_to"] is None


async def test_an_explicit_null_price_closes_the_observation_without_opening_a_new_one(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=8_000_000_000, revision=1)],
        external_project_id="P-1",
    )
    result = await _sync(
        session_factory,
        "units",
        [_unit_v2("U-1", external_area_id="A-1", listing_price=None, revision=2)],
        external_project_id="P-1",
    )
    assert result.projections["updated"] == 1

    rows = await _price_rows(session_factory, "U-1")
    assert len(rows) == 1
    assert rows[0]["effective_to"] is not None


async def test_a_later_update_that_omits_price_does_not_touch_the_stored_observation(session_factory):
    """`listing_price` KHÔNG nằm trong chốt A4 (`history_guard.HISTORY_FIELDS`) —
    một cập nhật CHỈ đổi trạng thái, không nhắc tới giá, không được coi là 'đánh
    rơi' giá đang lưu."""
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=8_000_000_000, revision=1)],
        external_project_id="P-1",
    )
    result = await _sync(
        session_factory,
        "units",
        [_unit_v2("U-1", external_area_id="A-1", status="reserved", revision=2)],  # không nhắc listing_price
        external_project_id="P-1",
    )
    assert result.projections["updated"] == 1

    rows = await _price_rows(session_factory, "U-1")
    assert len(rows) == 1
    assert rows[0]["official_price"] == Decimal("8000000000.00")
    assert rows[0]["effective_to"] is None


async def test_a_stale_revision_cannot_overwrite_a_newer_price(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=8_000_000_000, revision=1)],
        external_project_id="P-1",
    )
    await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=9_000_000_000, revision=3)],
        external_project_id="P-1",
    )
    # Bản đến mang revision=2 — CŨ hơn bản đã áp (3) — phải bị skip_stale và
    # không được chạm vào giá đang hiệu lực.
    result = await _sync(
        session_factory, "units", [_unit_v2("U-1", external_area_id="A-1", listing_price=1, revision=2)],
        external_project_id="P-1",
    )
    assert result.decisions.get("skip_stale") == 1

    # Hai dòng đã có TRƯỚC lần gửi stale (rev=1 đóng ở rev=3): gửi rev=2 sau đó
    # không được thêm dòng thứ ba, và dòng đang hiệu lực vẫn phải là giá của
    # rev=3 (9 tỷ), không phải giá bịa ở rev=2 (1 đồng).
    rows = await _price_rows(session_factory, "U-1")
    assert len(rows) == 2
    assert rows[-1]["official_price"] == Decimal("9000000000.00")
    assert rows[-1]["effective_to"] is None


async def test_deal_v2_scopes_through_unit(session_factory):
    await _seed_project_and_area(session_factory, "P-1", "A-1")
    await _sync(session_factory, "units", [_unit_v2("U-1", external_area_id="A-1")], external_project_id="P-1")

    result = await _sync(session_factory, "deals", [_deal("D-1", unit="U-1")], external_project_id="P-1")

    assert result.projections["inserted"] == 1


async def test_deal_v2_rejects_unknown_unit(session_factory):
    await _seed_project(session_factory, "P-1")
    result = await _sync(session_factory, "deals", [_deal("D-1", unit="U-KHONG-CO")], external_project_id="P-1")

    assert result.projections["rejected"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# D6 — Concurrency: cùng cơ chế Phase 5 (SourceIdentityService), áp cho
# Project/Area. Phase 5 tự đã kiểm Unit/Deal đầy đủ (test_sync_concurrency.py,
# KHÔNG sửa ở Phase D) — ở đây chỉ cần chứng minh Project/Area đi qua CÙNG khoá
# `SELECT ... FOR UPDATE`, không tái tạo toàn bộ harness.
# ═══════════════════════════════════════════════════════════════════════════


async def test_concurrent_project_revisions_the_highest_wins_no_duplicate(session_factory):
    import asyncio

    await _seed_project(session_factory, "P-RACE", name="Ban đầu", revision=1)

    results = await asyncio.wait_for(
        asyncio.gather(
            _sync(session_factory, "projects", [_project("P-RACE", name="Revision 3", revision=3)], external_project_id="P-RACE"),
            _sync(session_factory, "projects", [_project("P-RACE", name="Revision 2", revision=2)], external_project_id="P-RACE"),
        ),
        timeout=30,
    )

    assert all(r.status in ("completed", "completed_with_conflicts") for r in results)
    row = await _project_row(session_factory, "P-RACE")
    assert row["name"] == "Revision 3"
    assert row["source_revision"] == 3

    async with session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(projects)
            .where(projects.c.source_instance_id == INSTANCE, projects.c.external_id == "P-RACE")
        )
    assert count == 1, "không được có dòng project trùng"


async def test_concurrent_area_revisions_the_highest_wins_no_duplicate(session_factory):
    import asyncio

    await _seed_project_and_area(session_factory, "P-1", "A-RACE", total=100, revision=1)

    results = await asyncio.wait_for(
        asyncio.gather(
            _sync(session_factory, "areas", [_area("A-RACE", total=300, revision=3)], external_project_id="P-1"),
            _sync(session_factory, "areas", [_area("A-RACE", total=200, revision=2)], external_project_id="P-1"),
        ),
        timeout=30,
    )

    assert all(r.status in ("completed", "completed_with_conflicts") for r in results)
    row = await _area_row(session_factory, "A-RACE")
    assert row["total_units"] == 300
    assert row["source_revision"] == 3

    async with session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(areas)
            .where(areas.c.source_instance_id == INSTANCE, areas.c.external_id == "A-RACE")
        )
    assert count == 1


async def test_no_deadlock_for_a_crossed_multi_record_area_batch(session_factory):
    """Hai lô mang CÙNG hai bản ghi Area theo hai thứ tự NGƯỢC NHAU — cùng kịch
    bản `test_sync_concurrency.py::crossed_multi_record_batches` đã chứng minh
    không deadlock cho Unit, áp cho Area qua ĐÚNG CÙNG `lock_identities()`.

    Seed trước (giống fixture gốc): `lock_identities()` chỉ khoá được dòng
    `crm_source_records` ĐÃ TỒN TẠI — một lô mà CẢ HAI bản ghi cùng là lần-đầu-
    thấy (chưa seed) đi qua đường `ON CONFLICT DO NOTHING` không tất định thứ
    tự giữa các bản ghi, và đó là một giới hạn ĐÃ CÓ TỪ TRƯỚC của chính cơ chế
    Phase 5 — áp dụng như nhau cho MỌI thực thể, không phải hồi quy của Phase D.
    Bảo đảm đã có (và được kiểm ở đây) là cho lô CẬP NHẬT chéo thứ tự, đúng như
    Phase 5 đã chứng minh cho Unit.
    """
    import asyncio

    await _seed_project(session_factory, "P-MULTI")
    # `name`/`unit_type` PHẢI khác nhau — hai phân khu cùng cặp đó trong CÙNG dự
    # án vi phạm `uq_areas_project_name_unit_type` (0001), không liên quan gì
    # tới race đang kiểm ở đây.
    await _sync(
        session_factory,
        "areas",
        [_area("A-MULTI-1", name="Toa 1", revision=1), _area("A-MULTI-2", name="Toa 2", revision=1)],
        external_project_id="P-MULTI",
    )

    results = await asyncio.wait_for(
        asyncio.gather(
            _sync(
                session_factory,
                "areas",
                [_area("A-MULTI-1", name="Toa 1", revision=2), _area("A-MULTI-2", name="Toa 2", revision=2)],
                external_project_id="P-MULTI",
            ),
            _sync(
                session_factory,
                "areas",
                [_area("A-MULTI-2", name="Toa 2", revision=3), _area("A-MULTI-1", name="Toa 1", revision=3)],
                external_project_id="P-MULTI",
            ),
        ),
        timeout=30,
    )
    assert all(r.status in ("completed", "completed_with_conflicts") for r in results)
    for external_id in ("A-MULTI-1", "A-MULTI-2"):
        row = await _area_row(session_factory, external_id)
        assert row["source_revision"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# D7 — Bảo vệ đường ghi di sản: không route/service nào (ngoài ingestion) còn
# mutate được Project/Area CANONICAL.
# ═══════════════════════════════════════════════════════════════════════════


def test_no_public_route_can_create_a_project_or_area_outside_ingestion():
    """Liệt kê MỌI route đã đăng ký — không route POST nào ngoài `/sync/{entity}`
    còn ghi được vào `projects`/`areas`."""
    from src.main import app

    creating_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "").rstrip("/") in ("/api/v1/projects", "/api/v1/areas")
        and "POST" in getattr(route, "methods", set())
    ]
    assert creating_routes == [], f"Vẫn còn route TẠO Project/Area ngoài ingestion: {creating_routes}"


def test_no_project_or_area_write_service_remains():
    from pathlib import Path

    assert not Path("src/services/projects.py").exists()
