"""Đối soát và chốt an toàn xoá theo ảnh chụp, chạy trên database thật.

Mọi test ở đây dựng dữ liệu bằng CHÍNH đường đồng bộ (HTTP + hợp đồng v1), không
cắm thẳng vào bảng — trừ những chỗ cố ý tạo ra trạng thái hỏng mà đường đồng bộ
không thể tạo ra (giao dịch mồ côi, bản sao lệch bản ghi nguồn). Nếu dựng bằng
INSERT tay ở mọi chỗ, bộ test sẽ chứng minh các truy vấn đối soát chạy được, chứ
không chứng minh chúng phát hiện được vấn đề THẬT của luồng thật.

Phủ đúng danh sách Phase 5 yêu cầu:

| Kịch bản | Test |
|---|---|
| Đối soát sạch thì ĐẠT | `test_clean_state_passes` |
| Thiếu bản ghi | `test_missing_record_in_mirror_is_an_error` |
| Thừa bản ghi | `test_extra_record_in_mirror_is_an_error` |
| Bản ghi cũ | `test_stale_record_after_complete_snapshot_is_a_warning` |
| Giao dịch mồ côi | `test_orphan_deal_is_an_error` |
| Trùng giao dịch đang giữ | `test_duplicate_active_deal_is_an_error` |
| Lệch bản sao ↔ nguồn, hai chiều | `test_version_mismatch_*`, `test_tombstone_state_mismatch_*` |
| Bản ghi bị từ chối | `test_rejected_records_are_an_error` |
| Ảnh chụp đầy đủ | `test_complete_snapshot_tombstones_within_the_gate` |
| Thiếu mảnh ảnh chụp | `test_missing_chunk_never_triggers_deletion` |
| Xoá nhỏ hợp lệ | `test_small_legitimate_deletion_is_applied` |
| Ảnh chụp cắt cụt vượt chốt | `test_truncated_snapshot_exceeding_the_gate_deletes_nothing` |
| Ghi đè của con người | `test_human_override_applies_the_deletion` |
| Không ghi ngoài bảng đối soát | `test_reconciliation_writes_nothing_outside_its_own_tables` |
| Kết quả lặp lại được | `test_reconciliation_is_repeatable` |
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.main import app
from src.models.tables import (
    areas,
    crm_source_records,
    deals,
    reconciliation_findings,
    reconciliation_runs,
    sync_credentials,
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

PROJECT_ID = uuid.UUID("d4e5f6a7-b8c9-4012-8345-6789abcdef01")
INSTANCE = "synthetic-recon-crm"
SOURCE_SYSTEM = "mini_crm"

RECON_URL = "/api/v1/reconciliation/runs"
UNITS_URL = "/api/v1/sync/units"
DEALS_URL = "/api/v1/sync/deals"


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
        recon = sa.select(reconciliation_runs.c.id).where(reconciliation_runs.c.project_id == PROJECT_ID)
        await session.execute(
            sa.delete(reconciliation_findings).where(reconciliation_findings.c.reconciliation_run_id.in_(recon))
        )
        await session.execute(sa.delete(reconciliation_runs).where(reconciliation_runs.c.project_id == PROJECT_ID))
        await session.execute(
            sa.delete(deals).where(deals.c.unit_id.in_(sa.select(units.c.id).where(units.c.area_id.in_(area_ids))))
        )
        await session.execute(sa.delete(deals).where(deals.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(units).where(units.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id == INSTANCE))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'RECON', :d, :ts)"),
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
                        "total_units": 100,
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
async def issued(session_factory):
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            return await SyncCredentialService().issue(
                session, source_system=SOURCE_SYSTEM, source_instance_id=INSTANCE, label="recon test"
            )


@pytest_asyncio.fixture
async def client(issued):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": issued.api_key}
    ) as authorized:
        yield authorized


# --- Trợ giúp dựng payload hợp đồng v1 --------------------------------------


def _unit(external_id, *, code, status="available", area="A1", revision=1):
    return {
        "entity": "unit",
        "operation": "upsert",
        "external_id": external_id,
        "source_revision": revision,
        "payload": {
            "area_ref": {"area_name": area, "unit_type": "2PN"},
            "unit_code": code,
            "unit_status": status,
        },
    }


def _deal(external_id, *, unit_external_id, status="reserved", revision=1):
    payload = {"external_unit_id": unit_external_id, "deal_status": status}
    if status == "reserved":
        payload["reserved_at"] = "2026-08-01T09:00:00+07:00"
    elif status == "sold":
        payload["reserved_at"] = "2026-07-20T09:00:00+07:00"
        payload["sold_at"] = "2026-08-02T09:00:00+07:00"
    return {
        "entity": "deal",
        "operation": "upsert",
        "external_id": external_id,
        "source_revision": revision,
        "payload": payload,
    }


def _envelope(records, *, batch, snapshot=None, sync_mode="incremental"):
    payload = {
        "_comment": "FIXTURE TỔNG HỢP — KHÔNG PHẢI DỮ LIỆU CRM THẬT.",
        "schema_version": 1,
        "source_system": SOURCE_SYSTEM,
        "source_instance_id": INSTANCE,
        "external_batch_id": batch,
        "sync_mode": sync_mode,
        "project_ref": {"project_id": str(PROJECT_ID)},
        "source_extracted_at": "2026-08-09T02:00:00+07:00",
        "records": records,
    }
    if snapshot is not None:
        payload["snapshot"] = snapshot
    return payload


def _snapshot(snapshot_id, *, index=0, total=1, complete=True, areas_in_scope=None):
    scope = {"entities": ["unit"]}
    if areas_in_scope is not None:
        scope["area_refs"] = [{"area_name": name, "unit_type": "2PN"} for name in areas_in_scope]
    return {
        "snapshot_id": snapshot_id,
        "chunk_index": index,
        "chunk_total": total,
        "snapshot_complete": complete,
        "scope": scope,
    }


async def _seed_units(client, count=4, prefix="SYNTH-U"):
    records = [_unit(f"{prefix}-{i:03d}", code=f"A1-{i:03d}") for i in range(1, count + 1)]
    response = await client.post(UNITS_URL, json=_envelope(records, batch=f"SYNTH-BATCH-{prefix}"))
    assert response.status_code == 202, response.text
    return records


async def _reconcile(client, **overrides):
    body = {"project_id": str(PROJECT_ID), "source_instance_id": INSTANCE, "source_system": SOURCE_SYSTEM}
    body.update(overrides)
    response = await client.post(RECON_URL, json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def _findings(client, run_id, **params):
    response = await client.get(f"{RECON_URL}/{run_id}/findings", params=params)
    assert response.status_code == 200, response.text
    return response.json()["findings"]


def _codes(findings) -> set[str]:
    return {f["check_code"] for f in findings}


# --- 1. Trạng thái sạch ------------------------------------------------------


async def test_clean_state_passes(client):
    """Bản sao nhất quán → ĐẠT, không có finding mức error."""
    await _seed_units(client)

    result = await _reconcile(client)

    assert result["passed"] is True
    assert result["error_count"] == 0
    assert result["status"] == "completed"
    assert result["checks_run"] >= 9


async def test_result_states_what_it_proves(client):
    """Kết quả phải tự khai giới hạn của nó: không so với CRM thật."""
    await _seed_units(client)
    result = await _reconcile(client)

    assert "chưa có CRM" in result["summary"]["proves"]


async def test_source_scope_is_refused_while_no_crm_exists(client):
    """`scope='source'` bị chặn: chưa có gì để đối chiếu.

    Cho chạy nó sẽ sinh ra một kết quả "đạt" không chứng minh điều gì, và kết quả
    đó sẽ được dùng làm bằng chứng cắt sang bộ tính mới.
    """
    response = await client.post(
        RECON_URL, json={"project_id": str(PROJECT_ID), "source_instance_id": INSTANCE, "scope": "source"}
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "UNSUPPORTED_RECONCILIATION_SCOPE"


# --- 2. Thiếu / thừa bản ghi -------------------------------------------------


async def test_missing_record_in_mirror_is_an_error(client, session_factory):
    """Bản ghi nguồn active nhưng bản sao không có → error."""
    await _seed_units(client)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.delete(units).where(
                    units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-002"
                )
            )

    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"], severity="error")

    assert result["passed"] is False
    assert "MISSING_IN_MIRROR" in _codes(findings)
    assert any(f["external_id"] == "SYNTH-U-002" for f in findings)


async def test_extra_record_in_mirror_is_an_error(client, session_factory):
    """Bản sao có căn mà không có bản ghi nguồn active → error (dữ liệu ma)."""
    await _seed_units(client)

    async with session_factory() as session:
        async with session.begin():
            area_id = await session.scalar(
                sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID, areas.c.area_name == "A1")
            )
            await session.execute(
                sa.insert(units).values(
                    id=uuid.uuid4(),
                    source_system=SOURCE_SYSTEM,
                    source_instance_id=INSTANCE,
                    external_unit_id="SYNTH-U-GHOST",
                    area_id=area_id,
                    unit_code="A1-GHOST",
                    unit_type="2PN",
                    status="available",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )

    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"], severity="error")

    assert result["passed"] is False
    assert "EXTRA_IN_MIRROR" in _codes(findings)
    assert any(f["external_id"] == "SYNTH-U-GHOST" for f in findings)


# --- 3. Lệch bản sao ↔ bản ghi nguồn, hai chiều ------------------------------


async def test_version_mismatch_between_mirror_and_source_record_is_an_error(client, session_factory):
    await _seed_units(client)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(units)
                .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-001")
                .values(source_revision=999)
            )

    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"], severity="error")

    assert "MIRROR_SOURCE_VERSION_MISMATCH" in _codes(findings)
    detail = next(f for f in findings if f["check_code"] == "MIRROR_SOURCE_VERSION_MISMATCH")
    assert detail["details"]["mirror_revision"] == 999
    assert detail["details"]["source_revision"] == 1


async def test_tombstone_state_mismatch_source_tombstoned_mirror_live(client, session_factory):
    """Chiều 1: nguồn đã tombstone, bản sao vẫn sống."""
    await _seed_units(client)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(crm_source_records)
                .where(
                    crm_source_records.c.source_instance_id == INSTANCE,
                    crm_source_records.c.source_record_id == "SYNTH-U-001",
                )
                # `ck_crm_source_records_tombstone_consistent` buộc hai trường này
                # đi cùng nhau — ràng buộc đang làm đúng việc của nó.
                .values(state="tombstoned", deleted_at=datetime.now(UTC))
            )

    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"], severity="error")

    assert result["passed"] is False
    assert {"TOMBSTONE_STATE_MISMATCH", "TOMBSTONE_NOT_APPLIED"} & _codes(findings)


async def test_tombstone_state_mismatch_mirror_tombstoned_source_active(client, session_factory):
    """Chiều 2: bản sao đã xoá mềm, nguồn vẫn active."""
    await _seed_units(client)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(units)
                .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-001")
                .values(deleted_at=datetime.now(UTC))
            )

    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"], severity="error")

    assert result["passed"] is False
    assert {"TOMBSTONE_STATE_MISMATCH", "MISSING_IN_MIRROR"} & _codes(findings)


# --- 4. Giao dịch mồ côi và trùng lặp ---------------------------------------


async def test_orphan_deal_is_an_error(client, session_factory):
    """Giao dịch trỏ tới căn không tồn tại."""
    await _seed_units(client, count=2)
    await client.post(
        DEALS_URL,
        json=_envelope([_deal("SYNTH-D-001", unit_external_id="SYNTH-U-001")], batch="SYNTH-BATCH-D1"),
    )

    # `fk_deals_unit_id` khiến giao dịch mồ côi THẬT SỰ không tạo ra được khi
    # khoá ngoại còn nguyên. Gỡ tạm để chứng minh check phát hiện được — nó là
    # lớp phòng thủ thứ hai cho trường hợp khoá ngoại bị gỡ hoặc dữ liệu vào bằng
    # đường khác, chứ không phải thứ xảy ra hằng ngày.
    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.text("ALTER TABLE deals DROP CONSTRAINT fk_deals_unit_id"))
            await session.execute(
                sa.update(deals)
                .where(deals.c.source_instance_id == INSTANCE, deals.c.external_deal_id == "SYNTH-D-001")
                .values(unit_id=uuid.uuid4())
            )

    try:
        result = await _reconcile(client)
        findings = await _findings(client, result["reconciliation_run_id"], severity="error")

        assert result["passed"] is False
        orphan = [f for f in findings if f["check_code"] == "ORPHAN_DEAL"]
        assert orphan
        assert orphan[0]["details"]["reason"] == "unit_missing"
    finally:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.delete(deals).where(
                        deals.c.source_instance_id == INSTANCE, deals.c.external_deal_id == "SYNTH-D-001"
                    )
                )
                await session.execute(
                    sa.text(
                        "ALTER TABLE deals ADD CONSTRAINT fk_deals_unit_id FOREIGN KEY (unit_id) REFERENCES units (id)"
                    )
                )


async def test_deal_attached_to_a_tombstoned_unit_is_an_error(client, session_factory):
    """Giao dịch còn sống gắn vào căn đã tombstone vẫn chiếm chỗ trong tồn kho."""
    await _seed_units(client, count=2)
    await client.post(
        DEALS_URL,
        json=_envelope([_deal("SYNTH-D-002", unit_external_id="SYNTH-U-001")], batch="SYNTH-BATCH-D2"),
    )

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(units)
                .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-001")
                .values(deleted_at=datetime.now(UTC))
            )

    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"], severity="error")

    orphan = [f for f in findings if f["check_code"] == "ORPHAN_DEAL"]
    assert orphan
    assert orphan[0]["details"]["reason"] == "unit_tombstoned"


async def test_duplicate_active_deal_is_an_error(client, session_factory):
    """Hai giao dịch cùng giữ một căn — tồn kho sẽ bị đếm hụt."""
    await _seed_units(client, count=2)
    await client.post(
        DEALS_URL,
        json=_envelope([_deal("SYNTH-D-003", unit_external_id="SYNTH-U-001")], batch="SYNTH-BATCH-D3"),
    )

    async with session_factory() as session:
        async with session.begin():
            unit_id = await session.scalar(
                sa.select(units.c.id).where(
                    units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-001"
                )
            )
            # `uq_deals_active_per_unit` chặn đường thường; cấy trực tiếp để kiểm
            # rằng đối soát vẫn phát hiện nếu ràng buộc bị gỡ hoặc dữ liệu vào
            # bằng đường khác.
            await session.execute(sa.text("ALTER TABLE deals DISABLE TRIGGER ALL"))
            await session.execute(sa.text("DROP INDEX IF EXISTS uq_deals_active_per_unit"))
            await session.execute(
                sa.insert(deals).values(
                    id=uuid.uuid4(),
                    source_system=SOURCE_SYSTEM,
                    source_instance_id=INSTANCE,
                    external_deal_id="SYNTH-D-DUP",
                    unit_id=unit_id,
                    status="reserved",
                    source_status="reserved",
                    reserved_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.execute(sa.text("ALTER TABLE deals ENABLE TRIGGER ALL"))

    try:
        result = await _reconcile(client)
        findings = await _findings(client, result["reconciliation_run_id"], severity="error")

        assert result["passed"] is False
        assert "DUPLICATE_ACTIVE_DEAL" in _codes(findings)
    finally:
        # Xoá bản cấy TRƯỚC rồi mới dựng lại index: index unique không tạo được
        # khi dữ liệu đang vi phạm chính nó.
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.delete(deals).where(
                        deals.c.source_instance_id == INSTANCE, deals.c.external_deal_id == "SYNTH-D-DUP"
                    )
                )
                await session.execute(
                    sa.text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_deals_active_per_unit ON deals (unit_id) "
                        "WHERE status IN ('reserved', 'sold') AND deleted_at IS NULL"
                    )
                )


# --- 5. Căn thiếu phân khu, bản ghi bị từ chối ------------------------------


async def test_unit_missing_area_is_an_error(client, session_factory):
    await _seed_units(client, count=2)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(sa.text("ALTER TABLE units DROP CONSTRAINT fk_units_area_id"))
            await session.execute(
                sa.update(units)
                .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-001")
                .values(area_id=uuid.uuid4())
            )

    try:
        result = await _reconcile(client)
        findings = await _findings(client, result["reconciliation_run_id"], severity="error")
        assert "UNIT_MISSING_AREA" in _codes(findings)
    finally:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.delete(units).where(
                        units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-001"
                    )
                )
                await session.execute(
                    sa.text(
                        "ALTER TABLE units ADD CONSTRAINT fk_units_area_id FOREIGN KEY (area_id) REFERENCES areas (id)"
                    )
                )


async def test_rejected_records_are_an_error(client):
    """Lô có bản ghi bị từ chối = dữ liệu chưa vào được bản sao."""
    await client.post(
        DEALS_URL,
        json=_envelope([_deal("SYNTH-D-ORPH", unit_external_id="SYNTH-U-KHONG-CO")], batch="SYNTH-BATCH-REJ"),
    )

    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"], severity="error")

    assert result["passed"] is False
    rejected = [f for f in findings if f["check_code"] == "REJECTED_RECORDS"]
    assert rejected
    assert rejected[0]["details"]["rows_failed"] == 1
    assert rejected[0]["details"]["by_category"]


# --- 6. Ảnh chụp: đủ mảnh, thiếu mảnh, chốt an toàn -------------------------


async def test_complete_snapshot_tombstones_within_the_gate(client, session_factory):
    """Ảnh chụp đủ mảnh, tập vắng mặt nhỏ → tombstone đúng phần vắng mặt."""
    await _seed_units(client, count=4)

    # Ảnh chụp chỉ còn 3/4 căn — SYNTH-U-004 vắng mặt.
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit(f"SYNTH-U-{i:03d}", code=f"A1-{i:03d}", revision=2) for i in (1, 2, 3)],
            batch="SYNTH-SNAP-BATCH-1",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-001"),
        ),
    )

    result = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-001", snapshot_entity="units")

    assert result["tombstoned"] == 1
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    sa.select(units).where(
                        units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-004"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["deleted_at"] is not None


async def test_small_legitimate_deletion_is_applied(client):
    """Xoá nhỏ, nằm dưới sàn 25 → được áp dụng bình thường."""
    await _seed_units(client, count=10)

    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit(f"SYNTH-U-{i:03d}", code=f"A1-{i:03d}", revision=2) for i in range(1, 9)],
            batch="SYNTH-SNAP-BATCH-2",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-002"),
        ),
    )

    result = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-002")

    assert result["tombstoned"] == 2


async def test_missing_chunk_never_triggers_deletion(client, session_factory):
    """Ảnh chụp thiếu mảnh → KHÔNG tombstone gì cả.

    Đây là bất biến quan trọng nhất của chốt: ảnh chụp dở dang trông y hệt một
    đợt xoá lớn, và hai thứ đó không phân biệt được từ dữ liệu.
    """
    await _seed_units(client, count=6)

    # Khai 3 mảnh nhưng chỉ gửi 1, và chưa chốt.
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit("SYNTH-U-001", code="A1-001", revision=2)],
            batch="SYNTH-SNAP-BATCH-3",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-003", index=0, total=3, complete=False),
        ),
    )

    result = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-003")

    assert result["tombstoned"] == 0
    findings = await _findings(client, result["reconciliation_run_id"])
    completeness = next(f for f in findings if f["check_code"] == "SNAPSHOT_COMPLETENESS")
    assert completeness["severity"] == "warning"
    assert completeness["details"]["missing_chunks"] == [1, 2]

    async with session_factory() as session:
        live = await session.scalar(
            sa.select(sa.func.count())
            .select_from(units)
            .where(units.c.source_instance_id == INSTANCE, units.c.deleted_at.is_(None))
        )
    assert live == 6, "ảnh chụp thiếu mảnh đã xoá mất bản ghi"


async def test_complete_flag_alone_is_not_enough(client):
    """Có cờ kết thúc nhưng thiếu mảnh → vẫn không được xoá."""
    await _seed_units(client, count=6)

    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit("SYNTH-U-001", code="A1-001", revision=2)],
            batch="SYNTH-SNAP-BATCH-4",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-004", index=0, total=2, complete=True),
        ),
    )

    result = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-004")

    assert result["tombstoned"] == 0


async def test_truncated_snapshot_exceeding_the_gate_deletes_nothing(client, session_factory):
    """Ảnh chụp đủ mảnh nhưng cắt cụt nội dung → vượt chốt → không xoá gì."""
    await _seed_units(client, count=40)

    # Ảnh chụp chỉ mang 2/40 căn: 38 vắng mặt, vượt max(5% của 40 = 2, 25) = 25.
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit(f"SYNTH-U-{i:03d}", code=f"A1-{i:03d}", revision=2) for i in (1, 2)],
            batch="SYNTH-SNAP-BATCH-5",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-005"),
        ),
    )

    result = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-005")

    assert result["tombstoned"] == 0
    assert result["passed"] is False, "vượt chốt trên một ảnh chụp đầy đủ là lỗi"

    findings = await _findings(client, result["reconciliation_run_id"])
    blocked = next(f for f in findings if f["check_code"] == "SNAPSHOT_SAFETY_GATE_BLOCKED")
    assert blocked["severity"] == "error"
    assert blocked["details"]["absent_count"] == 38
    assert blocked["details"]["limit"] == 25
    assert blocked["details"]["overridden"] is False

    async with session_factory() as session:
        live = await session.scalar(
            sa.select(sa.func.count())
            .select_from(units)
            .where(units.c.source_instance_id == INSTANCE, units.c.deleted_at.is_(None))
        )
    assert live == 40, "ảnh chụp cắt cụt đã xoá mất bản ghi"


async def test_human_override_applies_the_deletion(client, session_factory):
    """Ghi đè tường minh của người vận hành là lối thoát duy nhất, và nó được ghi lại."""
    await _seed_units(client, count=40)
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit(f"SYNTH-U-{i:03d}", code=f"A1-{i:03d}", revision=2) for i in (1, 2)],
            batch="SYNTH-SNAP-BATCH-6",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-006"),
        ),
    )

    blocked = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-006")
    assert blocked["tombstoned"] == 0

    overridden = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-006", override_safety_gate=True)

    assert overridden["tombstoned"] == 38
    findings = await _findings(client, overridden["reconciliation_run_id"])
    applied = next(f for f in findings if f["check_code"] == "SNAPSHOT_TOMBSTONED")
    assert applied["details"]["overridden"] is True, "việc ghi đè phải được ghi lại"

    async with session_factory() as session:
        live = await session.scalar(
            sa.select(sa.func.count())
            .select_from(units)
            .where(units.c.source_instance_id == INSTANCE, units.c.deleted_at.is_(None))
        )
    assert live == 2


async def test_stale_record_after_complete_snapshot_is_a_warning(client):
    """Bản ghi vắng mặt trong ảnh chụp đầy đủ → cảnh báo KÈM CHI TIẾT."""
    await _seed_units(client, count=4)
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit(f"SYNTH-U-{i:03d}", code=f"A1-{i:03d}", revision=2) for i in (1, 2, 3)],
            batch="SYNTH-SNAP-BATCH-7",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-007"),
        ),
    )

    result = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-007")
    findings = await _findings(client, result["reconciliation_run_id"], severity="warning")

    stale = next(f for f in findings if f["check_code"] == "STALE_AFTER_SNAPSHOT")
    assert stale["details"], "cảnh báo bắt buộc phải kèm chi tiết"
    assert stale["details"]["absent_count"] == 1
    assert "SYNTH-U-004" in stale["details"]["absent_sample"]


async def test_snapshot_scope_limits_the_absent_set(client, session_factory):
    """Ảnh chụp của MỘT phân khu không được xoá căn ở phân khu khác."""
    await _seed_units(client, count=3)
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit("SYNTH-U-A2", code="A2-001", area="A2")],
            batch="SYNTH-BATCH-A2",
        ),
    )

    # Ảnh chụp chỉ phủ A1 và mang 3/3 căn A1 → không có gì vắng mặt trong phạm vi.
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit(f"SYNTH-U-{i:03d}", code=f"A1-{i:03d}", revision=2) for i in (1, 2, 3)],
            batch="SYNTH-SNAP-BATCH-8",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-008", areas_in_scope=["A1"]),
        ),
    )

    result = await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-008")

    assert result["tombstoned"] == 0
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    sa.select(units).where(
                        units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-A2"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["deleted_at"] is None, "ảnh chụp của A1 đã xoá căn ở A2"


# --- 7. Tính chất của bản thân việc đối soát --------------------------------


async def test_reconciliation_writes_nothing_outside_its_own_tables(client, session_factory):
    """Đối soát nội bộ CHỈ ĐỌC: mọi bảng nghiệp vụ phải nguyên vẹn."""
    await _seed_units(client, count=3)
    await client.post(
        DEALS_URL,
        json=_envelope([_deal("SYNTH-D-RO", unit_external_id="SYNTH-U-001")], batch="SYNTH-BATCH-RO"),
    )

    async def snapshot_counts():
        async with session_factory() as session:
            out = {}
            for name in ("units", "deals", "crm_source_records", "upload_files", "upload_errors", "sync_payloads"):
                out[name] = await session.scalar(sa.text(f"SELECT count(*) FROM {name}"))
            out["units_digest"] = await session.scalar(
                sa.text("SELECT coalesce(md5(string_agg(u::text, '|' ORDER BY u::text)), 'EMPTY') FROM units u")
            )
            out["deals_digest"] = await session.scalar(
                sa.text("SELECT coalesce(md5(string_agg(d::text, '|' ORDER BY d::text)), 'EMPTY') FROM deals d")
            )
            return out

    before = await snapshot_counts()
    await _reconcile(client)
    after = await snapshot_counts()

    assert before == after, "đối soát nội bộ đã ghi vào bảng nghiệp vụ"


async def test_snapshot_reconciliation_only_touches_deleted_at(client, session_factory):
    """Ngoại lệ duy nhất được phép: đặt `deleted_at` cho tập vắng mặt.

    Không được đụng tới bất kỳ trường nghiệp vụ nào khác — CRM sở hữu chúng.
    """
    await _seed_units(client, count=4)
    await client.post(
        UNITS_URL,
        json=_envelope(
            [_unit(f"SYNTH-U-{i:03d}", code=f"A1-{i:03d}", revision=2) for i in (1, 2, 3)],
            batch="SYNTH-SNAP-BATCH-9",
            sync_mode="full_snapshot",
            snapshot=_snapshot("SYNTH-SNAP-009"),
        ),
    )

    async with session_factory() as session:
        before = (
            (
                await session.execute(
                    sa.select(units).where(
                        units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-004"
                    )
                )
            )
            .mappings()
            .one()
        )

    await _reconcile(client, scope="snapshot", snapshot_id="SYNTH-SNAP-009")

    async with session_factory() as session:
        after = (
            (
                await session.execute(
                    sa.select(units).where(
                        units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-004"
                    )
                )
            )
            .mappings()
            .one()
        )

    changed = {k for k in before if before[k] != after[k]}
    assert changed <= {"deleted_at", "updated_at"}, f"tombstone đã đổi thêm trường: {sorted(changed)}"
    assert after["status"] == before["status"]
    assert after["unit_code"] == before["unit_code"]


async def test_reconciliation_is_repeatable(client):
    """Chạy hai lần trên cùng dữ liệu phải cho cùng kết luận và cùng tập finding."""
    await _seed_units(client, count=3)

    first = await _reconcile(client)
    second = await _reconcile(client)

    assert first["passed"] == second["passed"]
    assert first["error_count"] == second["error_count"]
    assert first["summary"]["by_check"] == second["summary"]["by_check"]
    assert first["reconciliation_run_id"] != second["reconciliation_run_id"], "mỗi lần chạy là một dòng riêng"


async def test_failed_run_is_recorded_as_not_passed(client, session_factory):
    """`passed` và `error_count` không bao giờ được mâu thuẫn — DB canh việc này."""
    await _seed_units(client, count=2)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.delete(units).where(
                    units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "SYNTH-U-001"
                )
            )

    result = await _reconcile(client)

    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    sa.select(reconciliation_runs).where(
                        reconciliation_runs.c.id == uuid.UUID(result["reconciliation_run_id"])
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["passed"] is False
    assert row["error_count"] > 0
    assert row["status"] == "failed"


async def test_findings_are_machine_readable(client):
    """Mỗi finding phải mang đủ dữ kiện để dựng lại vấn đề, không cần mở log."""
    await _seed_units(client, count=3)
    result = await _reconcile(client)
    findings = await _findings(client, result["reconciliation_run_id"])

    counts = next(f for f in findings if f["check_code"] == "ENTITY_COUNTS")
    assert counts["details"]["units"]["live"] == 3

    scoped = next(f for f in findings if f["check_code"] == "COUNTS_BY_SCOPE")
    assert scoped["details"]["units_by_area_status"]
    assert scoped["details"]["project_id"] == str(PROJECT_ID)


async def test_reconciliation_requires_a_key_for_its_instance(session_factory):
    """Không mượn được danh nghĩa hệ nguồn khác để chạy đối soát."""
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            stranger = await SyncCredentialService().issue(
                session, source_system=SOURCE_SYSTEM, source_instance_id="synthetic-recon-stranger", label="x"
            )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": stranger.api_key}
    ) as other:
        response = await other.post(RECON_URL, json={"project_id": str(PROJECT_ID), "source_instance_id": INSTANCE})

    assert response.status_code == 403

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM sync_credentials WHERE source_instance_id = 'synthetic-recon-stranger'")
            )
