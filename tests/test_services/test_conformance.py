"""Bộ kiểm phù hợp: chạy đúng đường thật, và KHÔNG để lại gì.

Tính chất quan trọng nhất ở đây không phải "báo cáo đúng nội dung" mà là **không
ghi gì**. Một bộ kiểm để lại dữ liệu còn tệ hơn không có bộ kiểm: nó bơm bản ghi
lạ vào bản sao, và lần gửi THẬT sau đó sẽ thành `duplicate_noop` — tức là dữ liệu
thật bị bỏ qua vì bản giả đã chiếm chỗ.

Nên phần lớn test dưới đây đếm dòng trước/sau, kể cả ở những đường mà trực giác
nói "chắc chắn không ghi".

Chạy: TEST_TARGET=tests/test_services/test_conformance.py bash scripts/test_db.sh
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, crm_source_records, deals, units, upload_errors, upload_files
from src.services.conformance import (
    GUARDED_TABLES,
    ProductionRefusalError,
    assert_not_production,
    check_payload,
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

PROJECT_ID = uuid.UUID("b8c9d0e1-f2a3-4415-9627-89abcdef0c8c")
INSTANCE = "crm-conformance"


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def db_env(session_factory, monkeypatch):
    """Trỏ `get_session_factory()` (thứ bộ kiểm dùng) vào database test."""
    import src.db as db_module
    from src.config import get_settings

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("APP_ENV", "test")
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()

    async def wipe(session):
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        await session.execute(sa.delete(deals).where(deals.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id == INSTANCE))
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'CONF', :d, :ts)"),
                {"id": PROJECT_ID, "d": date(2026, 1, 1), "ts": datetime.now(UTC)},
            )
            await session.execute(
                sa.insert(areas),
                [
                    {
                        "id": uuid.uuid4(),
                        "project_id": PROJECT_ID,
                        "area_name": "A1",
                        "unit_type": "2PN",
                        "bedrooms": 2,
                        "area_sqm": 75,
                        "total_units": 50,
                        "created_at": datetime.now(UTC),
                    }
                ],
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
    for cached in (db_module.get_engine, db_module.get_session_factory, get_settings):
        cached.cache_clear()


# --- Helper -------------------------------------------------------------------


def _payload(records: list[dict], **overrides) -> bytes:
    body = {
        "schema_version": 1,
        "source_system": "mini_crm",
        "source_instance_id": INSTANCE,
        "external_batch_id": f"conf-{uuid.uuid4().hex[:8]}",
        "sync_mode": "incremental",
        "project_ref": {"project_id": str(PROJECT_ID)},
        "source_extracted_at": "2026-08-09T02:00:00+07:00",
        "records": records,
        **overrides,
    }
    return json.dumps(body).encode()


def _unit(external_id="U-1", *, code="A1-01", status="available", revision=1) -> dict:
    return {
        "entity": "unit",
        "operation": "upsert",
        "external_id": external_id,
        "source_revision": revision,
        "payload": {
            "area_ref": {"area_name": "A1", "unit_type": "2PN"},
            "unit_code": code,
            "unit_status": status,
        },
    }


async def _counts(session_factory) -> dict[str, int]:
    async with session_factory() as session:
        return {
            table: int(await session.scalar(sa.text(f"SELECT count(*) FROM {table}")) or 0) for table in GUARDED_TABLES
        }


def _codes(report) -> set[str]:
    return {v.error_code for v in report.violations}


# === Không ghi gì =============================================================


async def test_a_valid_payload_writes_nothing(session_factory):
    """Tính chất trung tâm của cả công cụ."""
    before = await _counts(session_factory)

    report = await check_payload(_payload([_unit()]), source="t")

    assert report.conforms
    assert await _counts(session_factory) == before
    assert report.database_untouched is True


async def test_the_harness_verifies_the_rollback_itself(session_factory):
    """ "Luôn rollback" là một lời hứa dễ vỡ — báo cáo phải tự kiểm bằng số dòng."""
    report = await check_payload(_payload([_unit()]), source="t")

    assert report.database_untouched is True
    assert report.as_dict()["database_untouched"] is True


async def test_a_rejected_payload_also_writes_nothing(session_factory):
    """Đường lỗi cũng phải sạch: rollback nằm trong `finally`, không phải nhánh
    thành công."""
    before = await _counts(session_factory)

    report = await check_payload(
        _payload([_unit(status="khong-co-trang-thai-nay")]),
        source="t",
    )

    assert not report.conforms
    assert await _counts(session_factory) == before


async def test_running_twice_gives_the_same_answer(session_factory):
    """Không tích luỹ trạng thái: lần hai không được thành `duplicate_noop`."""
    body = _payload([_unit()])

    first = await check_payload(body, source="t")
    second = await check_payload(body, source="t")

    assert first.decisions == second.decisions == {**first.decisions, "insert": 1}


async def test_the_temporary_batch_row_does_not_survive(session_factory):
    """Bộ kiểm phải chèn một dòng `upload_files` để thoả khoá ngoại của
    `crm_source_records` — dòng đó không được sống sót."""
    await check_payload(_payload([_unit()]), source="t")

    async with session_factory() as session:
        leftover = await session.scalar(
            sa.select(sa.func.count()).select_from(upload_files).where(upload_files.c.project_id == PROJECT_ID)
        )
    assert leftover == 0


# === Bốn cổng =================================================================


async def test_malformed_json_stops_at_the_first_gate(session_factory):
    report = await check_payload(b"{khong phai json", source="t")

    assert _codes(report) == {"MALFORMED_JSON"}
    assert report.database_untouched is None, "chưa chạy tới cổng 4 thì không khẳng định gì về database"


async def test_oversize_payload_stops_before_the_schema_gate(session_factory, monkeypatch):
    """Endpoint thật dừng ở cổng kích thước; bộ kiểm phải dừng cùng chỗ, nếu không
    nó báo 'chỉ sai kích thước' cho một payload chưa ai soi tiếp.

    Trần là hằng số module (`MAX_PAYLOAD_BYTES`), không phải cấu hình — hạ nó
    xuống ở đây rẻ hơn nhiều so với dựng một payload 5 MB thật.
    """
    monkeypatch.setattr("src.services.sync_payloads.MAX_PAYLOAD_BYTES", 10)

    report = await check_payload(_payload([_unit()]), source="t")

    assert "PAYLOAD_TOO_LARGE" in _codes(report)
    assert report.gates_run == ["size"], "vượt trần thì không được soi tiếp cổng nào nữa"


async def test_schema_violations_are_reported_with_their_path(session_factory):
    body = json.loads(_payload([_unit()]))
    body["records"][0]["payload"]["unit_status"] = 123
    report = await check_payload(json.dumps(body).encode(), source="t")

    assert not report.conforms
    assert all(v.gate == "contract" for v in report.violations)
    assert any(v.json_path for v in report.violations)


async def test_a_record_without_a_version_is_caught_at_the_envelope_gate(session_factory):
    body = json.loads(_payload([_unit()]))
    del body["records"][0]["source_revision"]
    report = await check_payload(json.dumps(body).encode(), source="t")

    assert not report.conforms


async def test_an_unknown_project_is_reported_not_crashed(session_factory):
    body = json.loads(_payload([_unit()]))
    body["project_ref"] = {"project_id": "11111111-2222-4333-8444-555555555555"}
    report = await check_payload(json.dumps(body).encode(), source="t")

    assert "UNKNOWN_PROJECT" in _codes(report)
    assert report.database_untouched is True


async def test_an_unknown_area_is_reported_at_the_record_gate(session_factory):
    body = json.loads(_payload([_unit()]))
    body["records"][0]["payload"]["area_ref"] = {"area_name": "KHONG-CO", "unit_type": "2PN"}
    report = await check_payload(json.dumps(body).encode(), source="t")

    assert "UNKNOWN_AREA" in _codes(report)
    assert all(v.gate == "records" for v in report.violations)


async def test_a_deal_before_its_unit_is_reported(session_factory):
    """Tra danh tính căn cũng thuộc cổng 4."""
    record = {
        "entity": "deal",
        "operation": "upsert",
        "external_id": "D-1",
        "source_revision": 1,
        "payload": {"external_unit_id": "CHUA-CO", "deal_status": "sold", "sold_at": "2026-08-01T00:00:00+07:00"},
    }
    report = await check_payload(_payload([record]), source="t")

    assert "UNKNOWN_UNIT_REFERENCE" in _codes(report)


# === Báo cáo ==================================================================


async def test_the_report_predicts_what_would_happen(session_factory):
    report = await check_payload(_payload([_unit("U-1"), _unit("U-2", code="A1-02")]), source="t")

    assert report.decisions["insert"] == 2
    assert report.projections["inserted"] == 2
    assert report.records_received == 2


async def test_the_report_is_json_serialisable(session_factory):
    report = await check_payload(_payload([_unit()]), source="t")

    encoded = json.dumps(report.as_dict(), ensure_ascii=False)
    assert json.loads(encoded)["conforms"] is True


async def test_every_report_carries_the_disclaimer(session_factory):
    """Kể cả báo cáo ĐẠT. Một dòng 'conforms: true' bị tách khỏi ngữ cảnh sẽ được
    đọc thành 'đã tương thích với CRM'."""
    passing = await check_payload(_payload([_unit()]), source="t")
    failing = await check_payload(b"{", source="t")

    for report in (passing, failing):
        assert "KHÔNG chứng minh" in report.as_dict()["disclaimer"]


async def test_the_report_says_authentication_was_not_checked(session_factory):
    """Bỏ qua một cổng thì phải NÓI ra, không im lặng."""
    report = await check_payload(_payload([_unit()]), source="t")

    assert any("xác thực" in note for note in report.notes)


async def test_an_already_processed_batch_is_flagged_as_a_replay(session_factory):
    """Endpoint thật sẽ trả kết quả cũ. Không nói ra thì người đọc chờ đợi những
    thay đổi mà lần gửi thật sẽ không thực hiện."""
    batch = f"conf-replay-{uuid.uuid4().hex[:8]}"
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.insert(upload_files).values(
                    id=uuid.uuid4(),
                    project_id=PROJECT_ID,
                    status="completed",
                    rows_ok=1,
                    rows_failed=0,
                    uploaded_at=datetime.now(UTC),
                    source_system="mini_crm",
                    source_instance_id=INSTANCE,
                    source_entity="units",
                    input_format="json",
                    transport_mode="api_push",
                    external_batch_id=batch,
                    rows_received=1,
                    error_summary={},
                )
            )

    report = await check_payload(_payload([_unit()], external_batch_id=batch), source="t")

    assert any("ĐÃ được xử lý" in note for note in report.notes)


async def test_a_non_synthetic_payload_is_accepted_for_checking(session_factory):
    """Khác `sync_simulator.py`: công cụ này không GỬI gì, nên nó phải nhận được
    payload thật của một Mini CRM tương lai. Đó là lý do nó tồn tại."""
    report = await check_payload(
        _payload([_unit()], source_instance_id="acme-crm-production-01"),
        source="payload-that-de-khong-tong-hop.json",
    )

    assert report.gates_run == ["size", "contract", "envelope", "records"]
    assert "synthetic" not in json.dumps(report.as_dict(), ensure_ascii=False).lower()


# === Từ chối chạy trên production =============================================


async def test_it_refuses_to_run_against_production(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(ProductionRefusalError):
            await check_payload(b"{}", source="t")
    finally:
        get_settings.cache_clear()


async def test_the_refusal_happens_before_anything_is_read(monkeypatch):
    """Từ chối phải xảy ra TRƯỚC khi chạm database, không phải giữa chừng."""
    from src.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(ProductionRefusalError):
            assert_not_production()
    finally:
        get_settings.cache_clear()


async def test_development_and_test_environments_are_allowed(monkeypatch):
    from src.config import get_settings

    for env in ("development", "test"):
        monkeypatch.setenv("APP_ENV", env)
        get_settings.cache_clear()
        assert_not_production()
    get_settings.cache_clear()
