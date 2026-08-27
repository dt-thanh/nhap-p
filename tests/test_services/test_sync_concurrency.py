"""Hai lô đồng bộ chạy SONG SONG cho CÙNG một bản ghi nguồn.

Chạy:  TEST_TARGET=tests/test_services/test_sync_concurrency.py bash scripts/test_db.sh

## Khiếm khuyết mà file này sinh ra để canh

`SourceIdentityService` là chỗ DUY NHẤT so phiên bản, và toàn bộ tính đúng đắn của
đường nhận dựa vào nó. Trước bản vá khoá hàng, `_load()` đọc bằng một câu `SELECT`
thường, nên trình tự **đọc → so → ghi** không được tuần tự hoá:

    đang giữ: revision 5
    lô A (revision 7)   đọc 5  →  "newer"  →  update
    lô B (revision 6)   đọc 5  →  "newer"  →  update      ← lẽ ra phải skip_stale
    lô nào COMMIT SAU thì thắng, kể cả khi nó mang phiên bản THẤP hơn

Điều tệ nhất không phải là mất một bản cập nhật, mà là **không ai biết đã mất**:
cả hai lô trả `202`, `rows_failed = 0`, `conflict_count = 0`. Cơ chế phát hiện đụng
độ cũng không cứu được, vì nó dựa trên việc ĐỌC ĐƯỢC bản đang giữ — mà ở đây cả hai
đọc trúng cùng một bản đã cũ.

Bản vá: `SELECT ... FOR UPDATE` ở `_load()`, khoá trước theo thứ tự tất định ở
`lock_identities()`, và một SAVEPOINT riêng cho cuộc đua chèn lần đầu.

## Cách các phép thử này ghim cuộc đua

Bộ test TRƯỚC bản vá dùng một `asyncio.Barrier` đặt ngay sau `_load()`: cả hai lô
đọc xong rồi mới có lô nào được ghi. Cách đó **không dùng lại được sau khi có khoá**
— lô thứ nhất giữ khoá rồi đứng chờ ở barrier, lô thứ hai chặn ở tầng database và
không bao giờ tới được barrier. Hai bên chờ nhau vĩnh viễn. (Chuyện này đã xảy ra
thật trong lúc vá, và nó là một cách hay để thấy khoá đang hoạt động.)

Cách ghim mới đi theo đúng ngữ nghĩa của khoá:

    lô A lấy khoá  →  phát tín hiệu  →  GIỮ khoá một nhịp  →  chiếu  →  COMMIT
    lô B chỉ khởi động sau tín hiệu  →  CHẶN ở database    →  chạy tiếp khi A xong

Nhờ vậy thứ tự là tất định, và bản thân việc lô B phải CHỜ trở thành một điều
khẳng định được: thời gian chạy của nó không thể ngắn hơn khoảng A giữ khoá.

## Ranh giới phase

File này KHÔNG chạm tới xếp hạng. Nó có một khẳng định rằng `ranking_runs` và
`ranking_scores` vẫn rỗng sau mọi cuộc đua — đó là ranh giới Phase 6 nhìn từ đây.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.tables import areas, crm_source_records, deals, units, upload_errors, upload_files
from src.services.json_payload import JsonPayloadParser
from src.services.source_identity import Identity, SourceIdentityService
from src.services.sync_runs import SyncRunService

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

# UUID RIÊNG của module này. Dùng chung với module khác thì hai phần dọn dẹp giẫm
# lên nhau và `DELETE FROM projects` nổ khoá ngoại.
PROJECT_ID = uuid.UUID("3f1b0d52-6c8e-4a71-9d3c-5e0f2a7b4c19")
SOURCE_SYSTEM = "mini_crm"
INSTANCE = "crm-concurrency"
UNIT_ID = "UNIT-RACE"
OTHER_UNIT_ID = "UNIT-RACE-2"

SEED_REVISION = 5
NEWER_REVISION = 7
OLDER_REVISION = 6

# Tên task là cách nhận ra lô nào đang chạy từ BÊN TRONG service, mà không phải đổi
# chữ ký của nó. Bản vá trong test vì thế chỉ thêm `await` — không đổi truy vấn,
# không đổi quyết định.
HOLDER_TASK_NAME = "lock-holder"
HOLD_SECONDS = 0.6


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(session_factory):
    async def wipe(session):
        runs = sa.select(upload_files.c.id).where(upload_files.c.project_id == PROJECT_ID)
        area_ids = sa.select(areas.c.id).where(areas.c.project_id == PROJECT_ID)
        unit_ids = sa.select(units.c.id).where(units.c.area_id.in_(area_ids))
        await session.execute(sa.delete(deals).where(deals.c.unit_id.in_(unit_ids)))
        await session.execute(sa.delete(units).where(units.c.area_id.in_(area_ids)))
        await session.execute(
            sa.delete(crm_source_records).where(crm_source_records.c.source_instance_id == INSTANCE)
        )
        await session.execute(sa.delete(upload_errors).where(upload_errors.c.file_id.in_(runs)))
        await session.execute(sa.delete(upload_files).where(upload_files.c.project_id == PROJECT_ID))
        await session.execute(sa.delete(areas).where(areas.c.project_id == PROJECT_ID))
        await session.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": PROJECT_ID})

    async with session_factory() as session:
        async with session.begin():
            await wipe(session)
            await session.execute(
                sa.text("INSERT INTO projects (id, name, launch_date, created_at) VALUES (:id, 'Race', :d, :ts)"),
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
                        "total_units": 100,
                        "created_at": datetime.now(UTC),
                    }
                ],
            )
    yield
    async with session_factory() as session:
        async with session.begin():
            await wipe(session)


# --- Dựng phong bì ------------------------------------------------------------


def _unit_record(external_id: str, revision: int, status: str, *, unit_code: str | None = None):
    return {
        "source_record_id": external_id,
        "operation": "upsert",
        "source_revision": revision,
        "data": {
            "area_name": "A1",
            "unit_type": "2PN",
            "unit_code": unit_code or f"A1-{external_id}",
            "status": status,
        },
    }


def _delete_record(external_id: str, revision: int):
    return {"source_record_id": external_id, "operation": "delete", "source_revision": revision}


def _envelope(records, *, batch: str):
    return JsonPayloadParser().parse(
        {
            "source_system": SOURCE_SYSTEM,
            "source_instance_id": INSTANCE,
            "source_entity": "units",
            "schema_version": 1,
            "external_batch_id": batch,
            "project_id": str(PROJECT_ID),
            "records": records,
        }
    )


def _one(revision: int, status: str, *, batch: str, external_id: str = UNIT_ID):
    return _envelope([_unit_record(external_id, revision, status)], batch=batch)


# --- Đọc trạng thái -----------------------------------------------------------


async def _unit_row(session_factory, external_id: str = UNIT_ID) -> dict:
    async with session_factory() as session:
        row = (
            (await session.execute(sa.select(units).where(units.c.external_unit_id == external_id)))
            .mappings()
            .one()
        )
    return dict(row)


async def _identity_row(session_factory, external_id: str = UNIT_ID) -> dict:
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    sa.select(crm_source_records).where(crm_source_records.c.source_record_id == external_id)
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def _count(session_factory, table, *conditions) -> int:
    async with session_factory() as session:
        return await session.scalar(sa.select(sa.func.count()).select_from(table).where(*conditions))


# --- Bộ ghim cuộc đua ---------------------------------------------------------


def _hold_the_lock(monkeypatch, signal: asyncio.Event, hold: float = HOLD_SECONDS):
    """Cho lô mang tên `HOLDER_TASK_NAME` GIỮ khoá một nhịp sau khi lấy được.

    Vá vào `lock_identities` — chỗ khoá được lấy — nên trong suốt nhịp giữ, mọi lô
    khác chạm tới cùng danh tính sẽ chặn ở tầng database. Bản vá chỉ thêm `await`;
    nó không đổi truy vấn nào và không đổi quyết định nào.
    """
    original = SourceIdentityService.lock_identities

    async def lock_then_hold(self, identities):
        locked = await original(self, identities)
        task = asyncio.current_task()
        if task is not None and task.get_name() == HOLDER_TASK_NAME:
            signal.set()
            await asyncio.sleep(hold)
        return locked

    monkeypatch.setattr(SourceIdentityService, "lock_identities", lock_then_hold)


async def _race(session_factory, monkeypatch, holder_envelope, waiter_envelope):
    """Chạy hai lô với thứ tự đã GHIM: `holder` lấy khoá trước, `waiter` phải chờ.

    Trả về kết quả hai lô và thời gian `waiter` đã chờ — con số đó là bằng chứng
    rằng khoá thật sự chặn, chứ không phải hai lô tình cờ không chồng lấn.
    """
    lock_taken = asyncio.Event()
    _hold_the_lock(monkeypatch, lock_taken)

    async def holder():
        return await SyncRunService(session_factory).run(holder_envelope)

    waited: dict[str, float] = {}

    async def waiter():
        await lock_taken.wait()
        started = time.monotonic()
        result = await SyncRunService(session_factory).run(waiter_envelope)
        waited["seconds"] = time.monotonic() - started
        return result

    holder_task = asyncio.create_task(holder(), name=HOLDER_TASK_NAME)
    waiter_task = asyncio.create_task(waiter(), name="waiter")
    holder_result, waiter_result = await asyncio.wait_for(
        asyncio.gather(holder_task, waiter_task), timeout=60
    )
    monkeypatch.undo()
    return {"holder": holder_result, "waiter": waiter_result, "waited": waited["seconds"]}


@pytest_asyncio.fixture
async def seeded(session_factory):
    """Bản ghi nền ở revision 5, nạp tuần tự như một lô bình thường."""
    await SyncRunService(session_factory).run(_one(SEED_REVISION, "available", batch="seed"))


# ═══════════════════════════════════════════════════════════════════════════
#  1. Hồi quy chính: lô CŨ chạy sau lô MỚI
# ═══════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def newer_then_older(session_factory, monkeypatch, seeded):
    """Revision 7 lấy khoá trước; revision 6 phải chờ rồi mới quyết định.

    Đây chính là hình dạng đã làm hỏng dữ liệu trước bản vá: trước đây revision 6
    đọc trúng revision 5 và ghi đè revision 7.
    """
    outcome = await _race(
        session_factory,
        monkeypatch,
        _one(NEWER_REVISION, "sold", batch=f"race-{NEWER_REVISION}"),
        _one(OLDER_REVISION, "blocked", batch=f"race-{OLDER_REVISION}"),
    )
    outcome["unit"] = await _unit_row(session_factory)
    outcome["identity"] = await _identity_row(session_factory)
    return outcome


async def test_the_waiting_batch_really_blocks_on_the_row_lock(newer_then_older):
    """Bằng chứng rằng khoá CHẶN, không phải hai lô tình cờ không chồng lấn.

    Lô chờ không thể chạy xong nhanh hơn khoảng lô kia giữ khoá. Thiếu khẳng định
    này, mọi test còn lại vẫn xanh trên một hệ thống hoàn toàn không khoá gì — chỉ
    cần lô thứ hai tình cờ chạy sau.
    """
    assert newer_then_older["waited"] >= HOLD_SECONDS * 0.8, (
        f"lô thứ hai chỉ mất {newer_then_older['waited']:.3f}s — nó KHÔNG chờ khoá"
    )


async def test_the_older_revision_is_skipped_not_applied(newer_then_older):
    """HỒI QUY CỦA KHIẾM KHUYẾT. Trước bản vá cả hai lô đều ra `update`."""
    assert newer_then_older["holder"].decisions["update"] == 1
    assert newer_then_older["waiter"].decisions["skip_stale"] == 1
    assert newer_then_older["waiter"].decisions["update"] == 0


async def test_the_older_revision_cannot_overwrite_the_newer_one(newer_then_older):
    assert newer_then_older["unit"]["source_revision"] == NEWER_REVISION
    assert newer_then_older["unit"]["status"] == "sold"


async def test_the_final_source_revision_is_the_highest_accepted_one(newer_then_older):
    assert newer_then_older["identity"]["source_revision"] == NEWER_REVISION
    assert newer_then_older["identity"]["last_decision"] == "skip_stale", (
        "quyết định CUỐI phải là quyết định của lô cuối — bản ghi vẫn ở revision 7"
    )


async def test_the_stale_decision_is_visible_to_the_source_system(newer_then_older):
    """Bỏ qua một lô KHÔNG được im lặng: hệ nguồn phải đọc ra được điều đó."""
    waiter = newer_then_older["waiter"]
    assert waiter.status == "completed"
    assert waiter.decisions["skip_stale"] == 1
    assert waiter.projections["untouched"] == 1
    assert waiter.projections["updated"] == 0


async def test_nothing_is_reported_as_an_error_and_nothing_is_lost_silently(newer_then_older):
    """Không lô nào hỏng, và không lô nào âm thầm biến mất: một lô ghi, một lô
    được ghi nhận là cũ. Cả hai đều có trong sổ."""
    for result in (newer_then_older["holder"], newer_then_older["waiter"]):
        assert result.rows_failed == 0
        assert result.rows_received == 1
        assert result.rows_ok == 1
    assert newer_then_older["identity"]["conflict_count"] == 0


async def test_the_mirror_and_the_identity_ledger_agree(newer_then_older):
    assert newer_then_older["unit"]["source_revision"] == newer_then_older["identity"]["source_revision"]


async def test_the_race_creates_no_duplicate_rows(newer_then_older, session_factory):
    assert await _count(session_factory, units, units.c.external_unit_id == UNIT_ID) == 1
    assert (
        await _count(session_factory, crm_source_records, crm_source_records.c.source_record_id == UNIT_ID) == 1
    )


# ═══════════════════════════════════════════════════════════════════════════
#  2. Chiều ngược lại: lô MỚI chạy sau lô CŨ
# ═══════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def older_then_newer(session_factory, monkeypatch, seeded):
    outcome = await _race(
        session_factory,
        monkeypatch,
        _one(OLDER_REVISION, "blocked", batch=f"race-{OLDER_REVISION}"),
        _one(NEWER_REVISION, "sold", batch=f"race-{NEWER_REVISION}"),
    )
    outcome["unit"] = await _unit_row(session_factory)
    outcome["identity"] = await _identity_row(session_factory)
    return outcome


async def test_a_newer_revision_arriving_second_is_applied(older_then_newer):
    """Khoá KHÔNG được biến thành "ai tới trước thắng" — nó chỉ tuần tự hoá phép so."""
    assert older_then_newer["holder"].decisions["update"] == 1
    assert older_then_newer["waiter"].decisions["update"] == 1
    assert older_then_newer["unit"]["source_revision"] == NEWER_REVISION
    assert older_then_newer["unit"]["status"] == "sold"
    assert older_then_newer["identity"]["source_revision"] == NEWER_REVISION


# ═══════════════════════════════════════════════════════════════════════════
#  3. Cùng phiên bản
# ═══════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def same_revision_same_payload(session_factory, monkeypatch, seeded):
    outcome = await _race(
        session_factory,
        monkeypatch,
        _one(NEWER_REVISION, "sold", batch="same-a"),
        _one(NEWER_REVISION, "sold", batch="same-b"),
    )
    outcome["unit"] = await _unit_row(session_factory)
    outcome["identity"] = await _identity_row(session_factory)
    return outcome


async def test_the_same_revision_with_the_same_payload_is_a_duplicate_noop(same_revision_same_payload):
    """Nạp lại y hệt không tốn một lệnh ghi nào, kể cả khi hai lô chạy song song."""
    assert same_revision_same_payload["holder"].decisions["update"] == 1
    assert same_revision_same_payload["waiter"].decisions["duplicate_noop"] == 1
    assert same_revision_same_payload["waiter"].projections["untouched"] == 1
    assert same_revision_same_payload["unit"]["source_revision"] == NEWER_REVISION
    assert same_revision_same_payload["identity"]["conflict_count"] == 0


@pytest_asyncio.fixture
async def same_revision_different_payload(session_factory, monkeypatch, seeded):
    outcome = await _race(
        session_factory,
        monkeypatch,
        _one(NEWER_REVISION, "sold", batch="conflict-a"),
        _one(NEWER_REVISION, "blocked", batch="conflict-b"),
    )
    outcome["unit"] = await _unit_row(session_factory)
    outcome["identity"] = await _identity_row(session_factory)
    return outcome


async def test_the_same_revision_with_a_different_payload_is_a_conflict(same_revision_different_payload):
    """Cùng phiên bản, khác nội dung ⇒ không có căn cứ xếp thứ tự ⇒ GIỮ NGUYÊN bản
    đã chấp nhận và ghi nhận đụng độ. Đây là chỗ khiếm khuyết cũ nguy hiểm nhất:
    trước bản vá hai lô không nhìn thấy nhau nên đụng độ KHÔNG BAO GIỜ được phát hiện.
    """
    assert same_revision_different_payload["holder"].decisions["update"] == 1
    assert same_revision_different_payload["waiter"].decisions["conflict"] == 1
    assert same_revision_different_payload["waiter"].projections["untouched"] == 1


async def test_the_conflict_is_recorded_and_not_silently_overwritten(same_revision_different_payload):
    identity = same_revision_different_payload["identity"]
    assert identity["conflict_count"] == 1
    assert identity["conflict_payload_hash"]
    assert identity["conflict_detected_at"] is not None
    # Bản đã chấp nhận GIỮ NGUYÊN — lô sau không ghi đè.
    assert same_revision_different_payload["unit"]["status"] == "sold"
    assert same_revision_different_payload["unit"]["source_revision"] == NEWER_REVISION


# ═══════════════════════════════════════════════════════════════════════════
#  4. Tombstone song song
# ═══════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def delete_then_older_update(session_factory, monkeypatch, seeded):
    """Xoá ở revision 7 chạy trước, cập nhật ở revision 6 chạy sau."""
    outcome = await _race(
        session_factory,
        monkeypatch,
        _envelope([_delete_record(UNIT_ID, NEWER_REVISION)], batch="tomb-a"),
        _one(OLDER_REVISION, "blocked", batch="tomb-b"),
    )
    outcome["unit"] = await _unit_row(session_factory)
    outcome["identity"] = await _identity_row(session_factory)
    return outcome


async def test_an_older_update_cannot_resurrect_a_newer_tombstone(delete_then_older_update):
    """Ngữ nghĩa tombstone giữ nguyên dưới đồng thời: bản đến CŨ hơn không có
    quyền đụng vào trạng thái đang giữ, bất kể lệnh của nó là gì."""
    assert delete_then_older_update["holder"].decisions["tombstone"] == 1
    assert delete_then_older_update["waiter"].decisions["skip_stale"] == 1
    assert delete_then_older_update["unit"]["deleted_at"] is not None
    assert delete_then_older_update["identity"]["state"] == "tombstoned"


@pytest_asyncio.fixture
async def older_update_then_delete(session_factory, monkeypatch, seeded):
    """Cập nhật ở revision 6 chạy trước, xoá ở revision 7 chạy sau."""
    outcome = await _race(
        session_factory,
        monkeypatch,
        _one(OLDER_REVISION, "blocked", batch="tomb-c"),
        _envelope([_delete_record(UNIT_ID, NEWER_REVISION)], batch="tomb-d"),
    )
    outcome["unit"] = await _unit_row(session_factory)
    outcome["identity"] = await _identity_row(session_factory)
    return outcome


async def test_a_newer_delete_arriving_second_still_tombstones(older_update_then_delete):
    assert older_update_then_delete["holder"].decisions["update"] == 1
    assert older_update_then_delete["waiter"].decisions["tombstone"] == 1
    assert older_update_then_delete["unit"]["deleted_at"] is not None
    assert older_update_then_delete["identity"]["state"] == "tombstoned"
    assert older_update_then_delete["identity"]["source_revision"] == NEWER_REVISION


# ═══════════════════════════════════════════════════════════════════════════
#  5. Cuộc đua CHÈN LẦN ĐẦU
# ═══════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def first_insert_race(session_factory, monkeypatch):
    """Hai lô cùng chèn một danh tính CHƯA TỪNG THẤY.

    `FOR UPDATE` không khoá được một dòng chưa tồn tại, nên khoá hàng không che
    được cuộc đua này — thứ chặn nhân bản là ràng buộc UNIQUE. Barrier đặt ngay
    trước câu INSERT để cả hai chắc chắn đã đi qua `_load()` và cùng thấy `None`.

    Ai thắng cuộc chèn là do bộ lập lịch, và khẳng định ở dưới cố ý KHÔNG phụ
    thuộc vào điều đó: dù ai thắng, bên thua đọc lại và so phiên bản, nên trạng
    thái cuối phải là phiên bản CAO NHẤT.
    """
    barrier = asyncio.Barrier(2)
    original_insert = SourceIdentityService._insert_first_sight

    async def insert_together(self, *args, **kwargs):
        await barrier.wait()
        return await original_insert(self, *args, **kwargs)

    monkeypatch.setattr(SourceIdentityService, "_insert_first_sight", insert_together)

    async def run(revision: int, status: str):
        return await SyncRunService(session_factory).run(
            _one(revision, status, batch=f"first-{revision}", external_id=OTHER_UNIT_ID)
        )

    results = await asyncio.wait_for(asyncio.gather(run(9, "sold"), run(8, "blocked")), timeout=60)
    monkeypatch.undo()
    return {
        "high": results[0],
        "low": results[1],
        "unit": await _unit_row(session_factory, OTHER_UNIT_ID),
        "identity": await _identity_row(session_factory, OTHER_UNIT_ID),
    }


async def test_the_first_insert_race_creates_no_duplicate_identity(first_insert_race, session_factory):
    assert (
        await _count(
            session_factory, crm_source_records, crm_source_records.c.source_record_id == OTHER_UNIT_ID
        )
        == 1
    )
    assert await _count(session_factory, units, units.c.external_unit_id == OTHER_UNIT_ID) == 1


async def test_the_first_insert_race_ends_on_the_highest_revision(first_insert_race):
    """Khẳng định KHÔNG phụ thuộc vào ai thắng cuộc chèn.

    Thắng: bên kia đọc lại, thấy 9, và 8 < 9 ⇒ skip_stale.
    Thua:  ta đọc lại, thấy 8, và 9 > 8 ⇒ update.
    Cả hai đường đều dừng ở revision 9.
    """
    assert first_insert_race["identity"]["source_revision"] == 9
    assert first_insert_race["unit"]["source_revision"] == 9
    assert first_insert_race["unit"]["status"] == "sold"


async def test_neither_batch_in_the_first_insert_race_is_reported_as_an_error(first_insert_race):
    """Trước khi có SAVEPOINT quanh câu INSERT, bên thua nhận một vi phạm ràng buộc
    làm rơi SAVEPOINT của bản ghi và biến nó thành một dòng LỖI — an toàn nhưng
    sai, vì nó có thể là bên mang phiên bản cao hơn."""
    for label in ("high", "low"):
        result = first_insert_race[label]
        assert result.status == "completed", (
            f"{label}: status={result.status} decisions={result.decisions} "
            f"projections={result.projections} rows_failed={result.rows_failed}"
        )
        assert result.rows_failed == 0
        assert result.projections["rejected"] == 0


async def test_the_first_insert_race_leaves_the_two_tables_consistent(first_insert_race):
    assert first_insert_race["unit"]["source_revision"] == first_insert_race["identity"]["source_revision"]


# ═══════════════════════════════════════════════════════════════════════════
#  6. Nhiều bản ghi trong một lô — thứ tự khoá tất định
# ═══════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def crossed_multi_record_batches(session_factory):
    """Hai lô mang CÙNG hai bản ghi theo hai thứ tự NGƯỢC NHAU.

    Không có `lock_identities()`, mỗi lô sẽ khoá theo thứ tự bản ghi trong phong
    bì: lô A khoá X rồi chờ Y, lô B khoá Y rồi chờ X — deadlock, và PostgreSQL sẽ
    huỷ một trong hai. Hợp đồng KHÔNG quy định thứ tự bản ghi, nên đây không phải
    một tình huống bịa ra.

    Không ghim thời điểm ở đây: điều đang kiểm là hai lô cùng CHẠY XONG, và một
    bản vá ghim thời điểm sẽ làm mất đúng tính đồng thời cần có.
    """
    await SyncRunService(session_factory).run(
        _envelope(
            [_unit_record(UNIT_ID, 1, "available"), _unit_record(OTHER_UNIT_ID, 1, "available")],
            batch="multi-seed",
        )
    )

    forward = _envelope(
        [_unit_record(UNIT_ID, 2, "reserved"), _unit_record(OTHER_UNIT_ID, 2, "reserved")],
        batch="multi-forward",
    )
    reverse = _envelope(
        [_unit_record(OTHER_UNIT_ID, 3, "sold"), _unit_record(UNIT_ID, 3, "sold")],
        batch="multi-reverse",
    )

    results = await asyncio.wait_for(
        asyncio.gather(
            SyncRunService(session_factory).run(forward),
            SyncRunService(session_factory).run(reverse),
        ),
        timeout=60,
    )
    return {
        "forward": results[0],
        "reverse": results[1],
        "units": {
            UNIT_ID: await _unit_row(session_factory, UNIT_ID),
            OTHER_UNIT_ID: await _unit_row(session_factory, OTHER_UNIT_ID),
        },
        "identities": {
            UNIT_ID: await _identity_row(session_factory, UNIT_ID),
            OTHER_UNIT_ID: await _identity_row(session_factory, OTHER_UNIT_ID),
        },
    }


async def test_crossed_multi_record_batches_do_not_deadlock(crossed_multi_record_batches):
    """Cả hai lô chạy xong. Deadlock sẽ hiện ra thành một lô hỏng, hoặc thành
    `asyncio.wait_for` hết giờ."""
    for result in (crossed_multi_record_batches["forward"], crossed_multi_record_batches["reverse"]):
        assert result.status == "completed"
        assert result.rows_failed == 0
        assert result.rows_received == 2


async def test_crossed_multi_record_batches_end_on_the_highest_revision(crossed_multi_record_batches):
    for external_id in (UNIT_ID, OTHER_UNIT_ID):
        assert crossed_multi_record_batches["units"][external_id]["source_revision"] == 3
        assert crossed_multi_record_batches["units"][external_id]["status"] == "sold"
        assert crossed_multi_record_batches["identities"][external_id]["source_revision"] == 3


async def test_the_lock_preamble_locks_every_existing_identity_in_the_batch(session_factory):
    """`lock_identities()` phải THẬT SỰ khoá, và khoá đúng số dòng đang có.

    Trả về số dòng đã khoá chính vì thế: một bản vá trong tương lai vô hiệu hoá
    bước này sẽ làm test này đỏ ngay, thay vì để deadlock xuất hiện ngẫu nhiên ở
    môi trường thật.
    """
    await SyncRunService(session_factory).run(
        _envelope(
            [_unit_record(UNIT_ID, 1, "available"), _unit_record(OTHER_UNIT_ID, 1, "available")],
            batch="lock-seed",
        )
    )

    envelope = _envelope(
        [_unit_record(OTHER_UNIT_ID, 2, "sold"), _unit_record("UNIT-NEVER-SEEN", 2, "sold")],
        batch="lock-probe",
    )
    to_lock = [
        Identity(
            source_system=SOURCE_SYSTEM,
            source_instance_id=INSTANCE,
            source_entity="units",
            source_record_id=record.source_record_id,
        )
        for record in envelope.records
    ]

    async with session_factory() as session:
        async with session.begin():
            locked = await SourceIdentityService(session).lock_identities(to_lock)

    # Hai danh tính được yêu cầu, nhưng chỉ MỘT tồn tại — danh tính chưa từng thấy
    # không có dòng để khoá, và đó là lý do cuộc đua chèn lần đầu cần cơ chế riêng.
    assert locked == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Ranh giới Phase 6
# ═══════════════════════════════════════════════════════════════════════════


async def test_no_ranking_row_is_created_by_any_of_these_races(newer_then_older, session_factory):
    """`src/services/ranking_trigger.py` (§8.3) giờ XẾP HÀNG một lần chạy xếp
    hạng sau mỗi lô đồng bộ — Phase 6 ĐÃ bắt đầu từ khi test này được viết. Bản
    vá đồng thời ở trên vẫn không được để lại HAI dòng `ranking_runs` (đúng luật
    "một job cho một run" — các lần đồng bộ chồng lấn phải GỘP thành một lần chờ,
    không nhân đôi), và chắc chắn KHÔNG được tự tính ra điểm xếp hạng nào — tính
    toán thật là việc của worker (RQ), không xảy ra đồng bộ trong test này."""
    async with session_factory() as session:
        runs = await session.scalar(sa.text("SELECT count(*) FROM ranking_runs"))
        scores = await session.scalar(sa.text("SELECT count(*) FROM ranking_scores"))
    assert runs <= 1, "các lần đồng bộ chồng lấn phải GỘP vào một lần chờ xếp hạng, không nhân đôi"
    assert scores == 0, "tính điểm xếp hạng là việc của worker — không được xảy ra đồng bộ trong sync"
