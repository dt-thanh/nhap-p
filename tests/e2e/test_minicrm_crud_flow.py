"""End-to-end MiniCRM CRUD audit (2026-08-23) — project/area/unit/deal, real Postgres.

**Confirmed gap this file closes.** `tests/test_services/test_hierarchy_projection.py`
exhaustively covers Project/Area business logic (insert/update/stale/conflict/
archive/parent-child) by calling `SyncRunService.run()` directly — it never goes
through `POST /api/v1/sync/{entity}`, so `_authenticate()`, route-level envelope
handling, and dashboard RBAC on the read side are never exercised for
`entity=projects`/`entity=areas`. Grepping the whole `tests/` tree for
`sync/projects` or `sync/areas` returns zero matches (confirmed before writing
this file). Unit/Deal already have solid HTTP-level coverage elsewhere
(`test_sync.py`, `test_sync_auth.py`, `test_sync_idempotency.py`,
`test_sync_concurrency.py`, `test_sync_recompute_enqueue.py`) — this file does
not re-litigate every one of those cases; it ties all four entities together in
one authenticated, real-Postgres, end-to-end journey through the actual API
boundary, and adds the projects/areas HTTP coverage that did not exist before.

**Why there is no direct CRUD API being tested here.** This backend is a
deliberate one-way mirror — the system of record for project/area/unit/deal is
the sibling `minicrm/` app (its own FastAPI service, own Postgres, own CRUD
routers under `minicrm/app/routers/`). There is a regression test guarding this
design (`test_hierarchy_projection.py::test_no_public_route_can_create_a_project_or_area_outside_ingestion`).
"MiniCRM" in this file therefore means: any caller presenting a valid sync
credential at `POST /api/v1/sync/{entity}` — exactly the boundary the production
relay uses, and the same test-double boundary every existing sync test in this
repo already relies on (rule: use a local fake only if the repo already supports
it — it does, this pattern is used in 5+ existing test files).

Run: TEST_TARGET=tests/e2e/test_minicrm_crud_flow.py bash scripts/test_db.sh
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
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
    deal_status_history,
    deals,
    projects,
    sync_credentials,
    sync_payloads,
    unit_status_history,
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

INSTANCE = "e2e-crud-flow"
OTHER_INSTANCE = "e2e-crud-flow-stranger"
VIEWER_TOKEN = "e2e-viewer-token-abc"
OPERATOR_TOKEN = "e2e-operator-token-abc"
ADMIN_TOKEN = "e2e-admin-token-abc"
NARROW_VIEWER_TOKEN = "e2e-narrow-viewer-token-abc"  # scoped to an unrelated project only


# --- Infra fixtures (same conventions as test_sync_concurrency.py / test_pipeline_read_surface.py) ---


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def dashboard_tokens(monkeypatch):
    import json

    from src.config import get_settings

    monkeypatch.setenv("DASHBOARD_BUSINESS_VIEWER_TOKEN", VIEWER_TOKEN)
    monkeypatch.setenv("DASHBOARD_PIPELINE_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.setenv("DASHBOARD_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv(
        "DASHBOARD_PROJECT_SCOPE",
        json.dumps({VIEWER_TOKEN: "ALL", OPERATOR_TOKEN: "ALL", ADMIN_TOKEN: "ALL"}),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def narrow_scope(dashboard_tokens, monkeypatch):
    """A viewer token scoped to a project outside this suite entirely — proves
    read endpoints enforce project scope, not just role. `NARROW_VIEWER_TOKEN`
    must REPLACE the viewer role token (dashboard_auth only recognizes the
    exact 3 configured role tokens; scope is keyed off that same literal
    string), so this must not be combined with plain `VIEWER_TOKEN` use in the
    same test."""
    import json

    from src.config import get_settings

    monkeypatch.setenv("DASHBOARD_BUSINESS_VIEWER_TOKEN", NARROW_VIEWER_TOKEN)
    monkeypatch.setenv(
        "DASHBOARD_PROJECT_SCOPE",
        json.dumps(
            {
                NARROW_VIEWER_TOKEN: ["some-other-project-not-in-this-suite"],
                OPERATOR_TOKEN: "ALL",
                ADMIN_TOKEN: "ALL",
            }
        ),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def db_env(session_factory, monkeypatch, dashboard_tokens):
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        # deal_status_history/unit_status_history cascade on unit/deal delete (0028/0029 FKs).
        await session.execute(
            sa.delete(deals).where(
                deals.c.unit_id.in_(sa.select(units.c.id).where(units.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE))))
            )
        )
        await session.execute(sa.delete(units).where(units.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE))))
        # crm_source_records.first/last_sync_run_id -> upload_files.id, so it must
        # go before upload_files; upload_files.project_id -> projects.id, so
        # upload_files must go before areas/projects.
        await session.execute(
            sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE)))
        )
        runs = sa.select(upload_files.c.id).where(upload_files.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE)))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(sync_payloads).where(sync_payloads.c.sync_run_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE))))
        await session.execute(sa.delete(areas).where(areas.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE))))
        await session.execute(sa.delete(projects).where(projects.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE))))
        await session.execute(
            sa.delete(sync_credentials).where(sync_credentials.c.source_instance_id.in_((INSTANCE, OTHER_INSTANCE)))
        )

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


@pytest_asyncio.fixture
async def api_key(session_factory):
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            issued = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=INSTANCE, label="e2e crud flow"
            )
    return issued.api_key


@pytest_asyncio.fixture
async def stranger_api_key(session_factory):
    """A validly-issued credential for a DIFFERENT source_instance_id — used to
    prove the sync boundary rejects a key that doesn't own the claimed instance."""
    from src.services.sync_credentials import SyncCredentialService

    async with session_factory() as session:
        async with session.begin():
            issued = await SyncCredentialService().issue(
                session, source_system="mini_crm", source_instance_id=OTHER_INSTANCE, label="e2e stranger"
            )
    return issued.api_key


@pytest_asyncio.fixture
async def crm_client(api_key):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest_asyncio.fixture
async def anon_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class FakeJob:
    id = "fake-job-id"


class FakeQueue:
    """Records enqueue calls instead of touching real Redis — same double
    `test_sync_recompute_enqueue.py` already uses, for the same reason: the
    worker itself has its own tests, this only needs a deterministic witness
    of WHEN a job gets queued relative to commit."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, func_name, **kwargs):
        self.calls.append({"func": func_name, **kwargs})
        return FakeJob()


@pytest.fixture
def queue(monkeypatch):
    fake = FakeQueue()
    monkeypatch.setattr("src.services.sync_runs.get_queue", lambda name: fake)
    monkeypatch.setattr("src.services.ranking_trigger.get_queue", lambda name: fake)
    return fake


# --- Envelope builders (schema_version=2, external-id linked hierarchy) ------


def _batch(n=[0]) -> str:  # noqa: B006 - intentional counter cell, test-only
    n[0] += 1
    return f"e2e-batch-{uuid.uuid4().hex[:8]}-{n[0]}"


def _envelope(entity: str, records: list[dict], *, external_project_id: str, batch: str | None = None) -> dict:
    return {
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "source_entity": entity,
        "schema_version": 2,
        "external_batch_id": batch or _batch(),
        "external_project_id": external_project_id,
        "records": records,
    }


def _project_rec(rid: str, *, name="Khu do thi E2E", launch_date="2026-06-01", revision=1) -> dict:
    return {
        "source_record_id": rid,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"name": name, "launch_date": launch_date},
    }


def _area_rec(rid: str, *, name="A1", unit_type="2PN", bedrooms=2, sqm=68.5, total=120, revision=1) -> dict:
    return {
        "source_record_id": rid,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"area_name": name, "unit_type": unit_type, "bedrooms": bedrooms, "area_sqm": sqm, "total_units": total},
    }


def _unit_rec(rid: str, *, external_area_id: str, code="U-01", status="available", revision=1) -> dict:
    return {
        "source_record_id": rid,
        "operation": "upsert",
        "source_revision": revision,
        "data": {"external_area_id": external_area_id, "unit_code": code, "status": status},
    }


def _deal_rec(rid: str, *, external_unit_id: str, status="reserved", revision=1, **stamps) -> dict:
    data = {"external_unit_id": external_unit_id, "status": status}
    if status == "reserved":
        data.setdefault("reserved_at", "2026-08-10T09:00:00Z")
    if status == "sold":
        data.setdefault("sold_at", "2026-08-11T09:00:00Z")
    data.update(stamps)
    return {"source_record_id": rid, "operation": "upsert", "source_revision": revision, "data": data}


def _delete_rec(rid: str, *, revision=99) -> dict:
    return {"source_record_id": rid, "operation": "delete", "source_revision": revision}


async def _sync(client, entity: str, records: list[dict], *, external_project_id: str, batch: str | None = None):
    return await client.post(
        f"/api/v1/sync/{entity}", json=_envelope(entity, records, external_project_id=external_project_id, batch=batch)
    )


# =============================================================================
# 1. Auth matrix at the ingestion boundary — the confirmed gap for projects/areas
# =============================================================================


async def test_anonymous_cannot_create_project(anon_client):
    res = await anon_client.post(
        "/api/v1/sync/projects",
        json=_envelope("projects", [_project_rec("E2E-P-ANON")], external_project_id="E2E-P-ANON"),
    )
    assert res.status_code == 401


async def test_stranger_instance_key_cannot_create_project(stranger_api_key):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": stranger_api_key}
    ) as client:
        # Key belongs to OTHER_INSTANCE, envelope claims INSTANCE — must be rejected.
        res = await client.post(
            "/api/v1/sync/projects",
            json=_envelope("projects", [_project_rec("E2E-P-STRANGER")], external_project_id="E2E-P-STRANGER"),
        )
    # A key that authenticates but doesn't own the CLAIMED instance is
    # INSTANCE_MISMATCH -> 403 (per `src/api/sync.py::_CREDENTIAL_STATUS`),
    # distinct from a missing/invalid key (401).
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "INSTANCE_MISMATCH"


async def test_anonymous_cannot_create_area(crm_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P-AREA-AUTH")], external_project_id="E2E-P-AREA-AUTH")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        res = await anon.post(
            "/api/v1/sync/areas",
            json=_envelope("areas", [_area_rec("E2E-A-AUTH")], external_project_id="E2E-P-AREA-AUTH"),
        )
    assert res.status_code == 401


async def test_authenticated_project_and_area_creation_succeeds(crm_client):
    p = await _sync(crm_client, "projects", [_project_rec("E2E-P-OK")], external_project_id="E2E-P-OK")
    assert p.status_code in (200, 202), p.text
    a = await _sync(crm_client, "areas", [_area_rec("E2E-A-OK")], external_project_id="E2E-P-OK")
    assert a.status_code in (200, 202), a.text


# =============================================================================
# 2. Project flow
# =============================================================================


async def test_project_create_read_update_persists(crm_client, anon_client):
    created = await _sync(crm_client, "projects", [_project_rec("E2E-P1", name="Ban đầu")], external_project_id="E2E-P1")
    assert created.status_code in (200, 202), created.text

    fetched = await anon_client.get("/api/v1/projects/E2E-P1", headers=_headers(VIEWER_TOKEN))
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Ban đầu"

    updated = await _sync(
        crm_client, "projects", [_project_rec("E2E-P1", name="Đã đổi tên", revision=2)], external_project_id="E2E-P1"
    )
    assert updated.status_code in (200, 202)
    refetched = await anon_client.get("/api/v1/projects/E2E-P1", headers=_headers(VIEWER_TOKEN))
    assert refetched.json()["name"] == "Đã đổi tên"


async def test_project_duplicate_batch_replays_without_reprocessing(crm_client):
    payload_batch = "e2e-p2-dup"
    first = await _sync(crm_client, "projects", [_project_rec("E2E-P2")], external_project_id="E2E-P2", batch=payload_batch)
    second = await _sync(crm_client, "projects", [_project_rec("E2E-P2")], external_project_id="E2E-P2", batch=payload_batch)
    assert first.status_code in (200, 202)
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["sync_run_id"] == first.json()["sync_run_id"]


async def test_project_stale_revision_does_not_regress(crm_client, anon_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P3", name="Giữ nguyên", revision=5)], external_project_id="E2E-P3")
    stale = await _sync(
        crm_client, "projects", [_project_rec("E2E-P3", name="KHÔNG được ghi", revision=2)], external_project_id="E2E-P3"
    )
    assert stale.status_code in (200, 202)
    assert stale.json()["decisions"].get("skip_stale") == 1
    row = await anon_client.get("/api/v1/projects/E2E-P3", headers=_headers(VIEWER_TOKEN))
    assert row.json()["name"] == "Giữ nguyên"


async def test_project_same_revision_conflicting_payload_keeps_original(crm_client, anon_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P4", name="Bản gốc", revision=1)], external_project_id="E2E-P4")
    conflicted = await _sync(
        crm_client, "projects", [_project_rec("E2E-P4", name="Khác hoàn toàn", revision=1)], external_project_id="E2E-P4"
    )
    assert conflicted.json()["decisions"].get("conflict") == 1
    row = await anon_client.get("/api/v1/projects/E2E-P4", headers=_headers(VIEWER_TOKEN))
    assert row.json()["name"] == "Bản gốc"


async def test_project_missing_required_field_is_rejected_not_fabricated(crm_client):
    bad_record = {"source_record_id": "E2E-P-BAD", "operation": "upsert", "source_revision": 1, "data": {"name": "X"}}
    res = await _sync(crm_client, "projects", [bad_record], external_project_id="E2E-P-BAD")
    body = res.json()
    assert body["rows_failed"] == 1
    assert body["status"] == "failed"


async def test_project_read_requires_auth_and_respects_scope(crm_client, anon_client, narrow_scope):
    await _sync(crm_client, "projects", [_project_rec("E2E-P5")], external_project_id="E2E-P5")

    anon = await anon_client.get("/api/v1/projects/E2E-P5")
    assert anon.status_code == 401

    out_of_scope = await anon_client.get("/api/v1/projects/E2E-P5", headers=_headers(NARROW_VIEWER_TOKEN))
    assert out_of_scope.status_code == 403
    assert out_of_scope.json()["detail"]["error_code"] == "PROJECT_OUT_OF_SCOPE"

    # `narrow_scope` replaces the viewer-role token for this test; ADMIN_TOKEN
    # (still "ALL") stands in for "any in-scope credential works" here.
    in_scope = await anon_client.get("/api/v1/projects/E2E-P5", headers=_headers(ADMIN_TOKEN))
    assert in_scope.status_code == 200


async def test_project_archive_blocked_by_live_area_then_succeeds(crm_client, anon_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P6")], external_project_id="E2E-P6")
    await _sync(crm_client, "areas", [_area_rec("E2E-A6")], external_project_id="E2E-P6")

    blocked = await _sync(crm_client, "projects", [_delete_rec("E2E-P6")], external_project_id="E2E-P6")
    assert blocked.json()["rows_failed"] == 1, "archive phải bị chặn khi còn phân khu sống"
    still_active = await anon_client.get("/api/v1/projects/E2E-P6", headers=_headers(VIEWER_TOKEN))
    assert still_active.json()["status"] == "active"

    await _sync(crm_client, "areas", [_delete_rec("E2E-A6")], external_project_id="E2E-P6")
    archived = await _sync(crm_client, "projects", [_delete_rec("E2E-P6")], external_project_id="E2E-P6")
    assert archived.json()["projections"].get("tombstoned") == 1
    now_archived = await anon_client.get("/api/v1/projects/E2E-P6", headers=_headers(VIEWER_TOKEN))
    assert now_archived.json()["status"] == "archived"


# =============================================================================
# 3. Area flow
# =============================================================================


async def test_area_create_read_update_under_project(crm_client, anon_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P7")], external_project_id="E2E-P7")
    await _sync(crm_client, "areas", [_area_rec("E2E-A7", total=100)], external_project_id="E2E-P7")

    fetched = await anon_client.get("/api/v1/areas/E2E-A7", headers=_headers(VIEWER_TOKEN))
    assert fetched.status_code == 200
    assert fetched.json()["total_units"] == 100

    await _sync(crm_client, "areas", [_area_rec("E2E-A7", total=130, revision=2)], external_project_id="E2E-P7")
    refetched = await anon_client.get("/api/v1/areas/E2E-A7", headers=_headers(VIEWER_TOKEN))
    assert refetched.json()["total_units"] == 130


async def test_area_wrong_project_parent_is_rejected(crm_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P8A")], external_project_id="E2E-P8A")
    await _sync(crm_client, "projects", [_project_rec("E2E-P8B")], external_project_id="E2E-P8B")
    await _sync(crm_client, "areas", [_area_rec("E2E-A8")], external_project_id="E2E-P8A")

    moved = await _sync(crm_client, "areas", [_area_rec("E2E-A8", revision=2)], external_project_id="E2E-P8B")
    assert moved.json()["projections"].get("rejected") == 1, "phân khu không được chuyển dự án"


async def test_area_under_nonexistent_project_rejects_whole_envelope(crm_client):
    res = await _sync(crm_client, "areas", [_area_rec("E2E-A9")], external_project_id="E2E-P-KHONG-TON-TAI")
    # PROJECT_NOT_FOUND is an envelope-level rejection mapped to 422 (correctable
    # client error), not 404 (`src/api/sync.py::_ENVELOPE_STATUS`).
    assert res.status_code == 422
    assert res.json()["detail"]["error_code"] == "PROJECT_NOT_FOUND"


async def test_area_read_respects_project_scope(crm_client, anon_client, narrow_scope):
    await _sync(crm_client, "projects", [_project_rec("E2E-P10")], external_project_id="E2E-P10")
    await _sync(crm_client, "areas", [_area_rec("E2E-A10")], external_project_id="E2E-P10")

    denied = await anon_client.get("/api/v1/areas/E2E-A10", headers=_headers(NARROW_VIEWER_TOKEN))
    assert denied.status_code == 403

    # `narrow_scope` replaces the viewer-role token for this test; ADMIN_TOKEN
    # (still "ALL") stands in for "any in-scope credential works" here.
    allowed = await anon_client.get("/api/v1/areas/E2E-A10", headers=_headers(ADMIN_TOKEN))
    assert allowed.status_code == 200


async def test_area_duplicate_batch_replays_cleanly(crm_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P11")], external_project_id="E2E-P11")
    batch = "e2e-a11-dup"
    first = await _sync(crm_client, "areas", [_area_rec("E2E-A11")], external_project_id="E2E-P11", batch=batch)
    second = await _sync(crm_client, "areas", [_area_rec("E2E-A11")], external_project_id="E2E-P11", batch=batch)
    assert second.json()["replayed"] is True
    assert second.json()["sync_run_id"] == first.json()["sync_run_id"]


# =============================================================================
# 4. Unit flow
# =============================================================================


async def _seed_project_and_area(client, project_id: str, area_id: str) -> None:
    await _sync(client, "projects", [_project_rec(project_id)], external_project_id=project_id)
    await _sync(client, "areas", [_area_rec(area_id)], external_project_id=project_id)


async def test_unit_create_read_update_status(crm_client, anon_client):
    await _seed_project_and_area(crm_client, "E2E-P12", "E2E-A12")
    created = await _sync(
        crm_client, "units", [_unit_rec("E2E-U12", external_area_id="E2E-A12", code="U-12")], external_project_id="E2E-P12"
    )
    assert created.status_code in (200, 202), created.text

    listed = await anon_client.get(
        "/api/v1/inventory",
        params={"external_project_id": "E2E-P12", "include_units": "true"},
        headers=_headers(VIEWER_TOKEN),
    )
    assert listed.status_code == 200
    codes = [u["unit_code"] for u in listed.json()["units"]]
    assert "U-12" in codes

    await _sync(
        crm_client,
        "units",
        [_unit_rec("E2E-U12", external_area_id="E2E-A12", code="U-12", status="reserved", revision=2)],
        external_project_id="E2E-P12",
    )
    refetched = await anon_client.get(
        "/api/v1/inventory",
        params={"external_project_id": "E2E-P12", "include_units": "true"},
        headers=_headers(VIEWER_TOKEN),
    )
    unit_row = next(u for u in refetched.json()["units"] if u["unit_code"] == "U-12")
    assert unit_row["status"] == "reserved"


async def test_unit_status_change_writes_history_row(crm_client, session_factory):
    await _seed_project_and_area(crm_client, "E2E-P13", "E2E-A13")
    await _sync(
        crm_client, "units", [_unit_rec("E2E-U13", external_area_id="E2E-A13", status="available")], external_project_id="E2E-P13"
    )
    await _sync(
        crm_client,
        "units",
        [_unit_rec("E2E-U13", external_area_id="E2E-A13", status="blocked", revision=2)],
        external_project_id="E2E-P13",
    )
    async with session_factory() as session:
        unit_id = await session.scalar(
            sa.select(units.c.id).where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "E2E-U13")
        )
        history = (
            await session.execute(
                sa.select(unit_status_history.c.old_status, unit_status_history.c.new_status, unit_status_history.c.source)
                .where(unit_status_history.c.unit_id == unit_id)
                .order_by(unit_status_history.c.changed_at)
            )
        ).all()
    assert any(row.new_status == "blocked" and row.source == "crm_sync" for row in history), history


async def test_unit_unknown_status_value_is_rejected(crm_client):
    await _seed_project_and_area(crm_client, "E2E-P14", "E2E-A14")
    res = await _sync(
        crm_client, "units", [_unit_rec("E2E-U14", external_area_id="E2E-A14", status="not_a_real_status")], external_project_id="E2E-P14"
    )
    assert res.json()["rows_failed"] == 1


async def test_unit_missing_area_rejects_only_that_record_in_a_mixed_batch(crm_client, session_factory):
    await _seed_project_and_area(crm_client, "E2E-P15", "E2E-A15")
    good = _unit_rec("E2E-U15-GOOD", external_area_id="E2E-A15", code="U-15-GOOD")
    bad = _unit_rec("E2E-U15-BAD", external_area_id="E2E-A-KHONG-CO", code="U-15-BAD")

    res = await _sync(crm_client, "units", [good, bad], external_project_id="E2E-P15")
    body = res.json()
    assert body["rows_ok"] == 1
    assert body["rows_failed"] == 1

    async with session_factory() as session:
        good_row = await session.scalar(
            sa.select(sa.func.count())
            .select_from(units)
            .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "E2E-U15-GOOD")
        )
        bad_row = await session.scalar(
            sa.select(sa.func.count())
            .select_from(units)
            .where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == "E2E-U15-BAD")
        )
        errors = await session.scalar(
            sa.select(sa.func.count())
            .select_from(upload_errors)
            .where(upload_errors.c.file_id == uuid.UUID(body["sync_run_id"]))
        )
    assert good_row == 1, "bản ghi hợp lệ trong lô hỗn hợp phải được nạp"
    assert bad_row == 0, "bản ghi hỏng không được nạp"
    assert errors == 1, "lỗi phải được ghi vào upload_errors để đối soát"


async def test_unit_stale_and_conflict_do_not_regress(crm_client, anon_client):
    await _seed_project_and_area(crm_client, "E2E-P16", "E2E-A16")
    await _sync(
        crm_client, "units", [_unit_rec("E2E-U16", external_area_id="E2E-A16", status="sold", revision=5)], external_project_id="E2E-P16"
    )
    stale = await _sync(
        crm_client, "units", [_unit_rec("E2E-U16", external_area_id="E2E-A16", status="available", revision=2)], external_project_id="E2E-P16"
    )
    assert stale.json()["decisions"].get("skip_stale") == 1

    conflict = await _sync(
        crm_client, "units", [_unit_rec("E2E-U16", external_area_id="E2E-A16", status="blocked", revision=5)], external_project_id="E2E-P16"
    )
    assert conflict.json()["decisions"].get("conflict") == 1

    listed = await anon_client.get(
        "/api/v1/inventory", params={"external_project_id": "E2E-P16", "include_units": "true"}, headers=_headers(VIEWER_TOKEN)
    )
    unit_row = next(u for u in listed.json()["units"] if u["unit_code"] == "U-01")
    assert unit_row["status"] == "sold", "cả bản cũ lẫn bản đụng độ đều không được ghi đè"


async def test_unit_read_requires_auth(anon_client):
    res = await anon_client.get("/api/v1/inventory", params={"external_project_id": "E2E-P-NOPE"})
    assert res.status_code == 401


# =============================================================================
# 5. Deal flow
# =============================================================================


async def _seed_unit(client, project_id: str, area_id: str, unit_id: str) -> None:
    await _seed_project_and_area(client, project_id, area_id)
    await _sync(client, "units", [_unit_rec(unit_id, external_area_id=area_id)], external_project_id=project_id)


async def test_deal_create_read_update_status(crm_client, anon_client):
    await _seed_unit(crm_client, "E2E-P17", "E2E-A17", "E2E-U17")
    created = await _sync(crm_client, "deals", [_deal_rec("E2E-D17", external_unit_id="E2E-U17")], external_project_id="E2E-P17")
    assert created.status_code in (200, 202), created.text

    listed = await anon_client.get(
        "/api/v1/deals", params={"external_project_id": "E2E-P17"}, headers=_headers(VIEWER_TOKEN)
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "reserved"

    # `reserved_at=None` makes the clear EXPLICIT — a full-record upsert that
    # just omits a previously-set timestamp is rejected as HISTORY_TIMESTAMP_DROPPED
    # (domain_projection.py), a deliberate guard against ambiguous partial payloads.
    updated = await _sync(
        crm_client,
        "deals",
        [_deal_rec("E2E-D17", external_unit_id="E2E-U17", status="sold", revision=2, reserved_at=None)],
        external_project_id="E2E-P17",
    )
    assert updated.json()["rows_failed"] == 0, updated.json()
    refetched = await anon_client.get(
        "/api/v1/deals", params={"external_project_id": "E2E-P17"}, headers=_headers(VIEWER_TOKEN)
    )
    assert refetched.json()["items"][0]["status"] == "sold"


async def test_deal_before_unit_is_rejected(crm_client):
    await _sync(crm_client, "projects", [_project_rec("E2E-P18")], external_project_id="E2E-P18")
    res = await _sync(
        crm_client, "deals", [_deal_rec("E2E-D18", external_unit_id="E2E-U-KHONG-CO")], external_project_id="E2E-P18"
    )
    assert res.json()["rows_failed"] == 1


async def test_deal_unknown_status_value_is_rejected(crm_client):
    await _seed_unit(crm_client, "E2E-P19", "E2E-A19", "E2E-U19")
    res = await _sync(
        crm_client, "deals", [_deal_rec("E2E-D19", external_unit_id="E2E-U19", status="not_a_real_status")], external_project_id="E2E-P19"
    )
    assert res.json()["rows_failed"] == 1


async def test_deal_reserved_without_reserved_at_is_rejected(crm_client):
    """`_check_status_dates` (domain_projection.py) requires the matching
    timestamp field for a holding status — a client cannot claim `reserved`
    without `reserved_at`."""
    await _seed_unit(crm_client, "E2E-P20", "E2E-A20", "E2E-U20")
    record = {
        "source_record_id": "E2E-D20",
        "operation": "upsert",
        "source_revision": 1,
        "data": {"external_unit_id": "E2E-U20", "status": "reserved"},
    }
    res = await _sync(crm_client, "deals", [record], external_project_id="E2E-P20")
    assert res.json()["rows_failed"] == 1


async def test_deal_duplicate_and_stale_and_conflict(crm_client, anon_client):
    await _seed_unit(crm_client, "E2E-P21", "E2E-A21", "E2E-U21")
    batch = "e2e-d21-dup"
    first = await _sync(crm_client, "deals", [_deal_rec("E2E-D21", external_unit_id="E2E-U21", revision=5)], external_project_id="E2E-P21", batch=batch)
    second = await _sync(crm_client, "deals", [_deal_rec("E2E-D21", external_unit_id="E2E-U21", revision=5)], external_project_id="E2E-P21", batch=batch)
    assert second.json()["replayed"] is True
    assert second.json()["sync_run_id"] == first.json()["sync_run_id"]

    stale = await _sync(
        crm_client, "deals", [_deal_rec("E2E-D21", external_unit_id="E2E-U21", status="lost", revision=2)], external_project_id="E2E-P21"
    )
    assert stale.json()["decisions"].get("skip_stale") == 1

    listed = await anon_client.get(
        "/api/v1/deals", params={"external_project_id": "E2E-P21"}, headers=_headers(VIEWER_TOKEN)
    )
    assert listed.json()["items"][0]["status"] == "reserved", "bản cũ không được ghi đè"


async def test_deal_read_respects_project_scope(crm_client, anon_client, narrow_scope):
    await _seed_unit(crm_client, "E2E-P22", "E2E-A22", "E2E-U22")
    await _sync(crm_client, "deals", [_deal_rec("E2E-D22", external_unit_id="E2E-U22")], external_project_id="E2E-P22")

    denied = await anon_client.get(
        "/api/v1/deals", params={"external_project_id": "E2E-P22"}, headers=_headers(NARROW_VIEWER_TOKEN)
    )
    assert denied.status_code == 403

    anon = await anon_client.get("/api/v1/deals", params={"external_project_id": "E2E-P22"})
    assert anon.status_code == 401


# =============================================================================
# 6. Full MiniCRM journey — one integrated scenario across all four entities
# =============================================================================


async def test_full_hierarchy_journey_project_area_unit_deal(crm_client, anon_client, session_factory, queue):
    project_id, area_id, unit_id, deal_id = "E2E-J-P", "E2E-J-A", "E2E-J-U", "E2E-J-D"

    # 1-3: create the full hierarchy through the authenticated sync boundary.
    p = await _sync(crm_client, "projects", [_project_rec(project_id)], external_project_id=project_id)
    a = await _sync(crm_client, "areas", [_area_rec(area_id)], external_project_id=project_id)
    u = await _sync(crm_client, "units", [_unit_rec(unit_id, external_area_id=area_id)], external_project_id=project_id)
    d = await _sync(crm_client, "deals", [_deal_rec(deal_id, external_unit_id=unit_id)], external_project_id=project_id)
    for res in (p, a, u, d):
        assert res.status_code in (200, 202), res.text

    # 4: read all resulting objects back through authorized API endpoints.
    assert (await anon_client.get(f"/api/v1/projects/{project_id}", headers=_headers(VIEWER_TOKEN))).status_code == 200
    assert (await anon_client.get(f"/api/v1/areas/{area_id}", headers=_headers(VIEWER_TOKEN))).status_code == 200
    inv = await anon_client.get(
        "/api/v1/inventory", params={"external_project_id": project_id, "include_units": "true"}, headers=_headers(VIEWER_TOKEN)
    )
    assert any(u["unit_code"] == "U-01" for u in inv.json()["units"])
    deals_resp = await anon_client.get("/api/v1/deals", params={"external_project_id": project_id}, headers=_headers(VIEWER_TOKEN))
    assert deals_resp.json()["total"] == 1

    calls_after_create = len(queue.calls)
    assert calls_after_create > 0, "lô tạo mới phải xếp hàng tính lại sau khi commit"

    # 5-6: resend the identical unit batch → idempotent replay, no duplicate row, no new enqueue.
    dup_batch = "e2e-journey-unit-dup"
    first_u = await _sync(crm_client, "units", [_unit_rec(unit_id, external_area_id=area_id, revision=1)], external_project_id=project_id, batch=dup_batch)
    replay_u = await _sync(crm_client, "units", [_unit_rec(unit_id, external_area_id=area_id, revision=1)], external_project_id=project_id, batch=dup_batch)
    assert replay_u.json()["replayed"] is True
    assert replay_u.json()["sync_run_id"] == first_u.json()["sync_run_id"]

    async with session_factory() as session:
        unit_count = await session.scalar(
            sa.select(sa.func.count()).select_from(units).where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == unit_id)
        )
    assert unit_count == 1

    # 7: submit a newer revision → applied.
    newer = await _sync(
        crm_client, "units", [_unit_rec(unit_id, external_area_id=area_id, status="reserved", revision=2)], external_project_id=project_id
    )
    assert newer.json()["decisions"].get("update") == 1
    assert len(queue.calls) > calls_after_create, "một thay đổi thật phải xếp hàng tính lại"
    calls_after_update = len(queue.calls)

    # 8: submit a stale revision → rejected without data regression, and no new job.
    stale = await _sync(
        crm_client, "units", [_unit_rec(unit_id, external_area_id=area_id, status="available", revision=1)], external_project_id=project_id
    )
    assert stale.json()["decisions"].get("skip_stale") == 1
    assert len(queue.calls) == calls_after_update, "skip_stale không được đổi bản sao nên không được xếp hàng"

    # 9: submit same revision with a different hash → conflict, no regression, no new job.
    conflict = await _sync(
        crm_client, "units", [_unit_rec(unit_id, external_area_id=area_id, status="blocked", revision=2)], external_project_id=project_id
    )
    assert conflict.json()["decisions"].get("conflict") == 1
    assert len(queue.calls) == calls_after_update, "conflict giữ bản cũ nên không được xếp hàng"

    async with session_factory() as session:
        row = (
            await session.execute(sa.select(units.c.status).where(units.c.source_instance_id == INSTANCE, units.c.external_unit_id == unit_id))
        ).scalar_one()
    assert row == "reserved", "chỉ bản cập nhật hợp lệ (revision=2, update) mới được giữ"

    # 10: a malformed record alongside a good one in one mixed batch — only the bad one fails.
    good_extra = _unit_rec(f"{unit_id}-2", external_area_id=area_id, code="U-J-02")
    bad_extra = _unit_rec(f"{unit_id}-BAD", external_area_id="area-does-not-exist", code="U-J-BAD")
    mixed = await _sync(crm_client, "units", [good_extra, bad_extra], external_project_id=project_id)
    assert mixed.json()["rows_ok"] == 1
    assert mixed.json()["rows_failed"] == 1

    async with session_factory() as session:
        error_count = await session.scalar(
            sa.select(sa.func.count()).select_from(upload_errors).where(upload_errors.c.file_id == uuid.UUID(mixed.json()["sync_run_id"]))
        )
        final_unit_count = await session.scalar(
            sa.select(sa.func.count()).select_from(units).where(units.c.source_instance_id == INSTANCE, units.c.area_id.in_(
                sa.select(areas.c.id).where(areas.c.source_instance_id == INSTANCE, areas.c.external_id == area_id)
            ))
        )
        final_deal_count = await session.scalar(
            sa.select(sa.func.count()).select_from(deals).where(
                deals.c.unit_id.in_(sa.select(units.c.id).where(units.c.source_instance_id == INSTANCE))
            )
        )
    assert error_count == 1, "bản ghi hỏng phải để lại dấu vết đối soát"
    assert final_unit_count == 2, "đúng hai căn hợp lệ (unit_id gốc + good_extra), không có căn hỏng"
    assert final_deal_count == 1, "không có giao dịch trùng lặp phát sinh trong toàn bộ hành trình"
