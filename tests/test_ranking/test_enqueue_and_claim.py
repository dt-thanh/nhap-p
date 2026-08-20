"""Trục bất đồng bộ của bộ xếp hạng: xếp hàng có chống dồn, và chiếm run.

Hai bất biến được canh ở đây là hai thứ KHÔNG thể kiểm bằng cách đọc code, vì cả
hai chỉ sai khi có đồng thời:

1. **Chống dồn.** Một lô đồng bộ lớn commit hàng trăm lần trong một phút. Nếu
   mỗi lần sinh một run thì worker chạy hàng trăm lần tính lại giống hệt nhau
   trên cùng dữ liệu. Ràng buộc nằm ở partial unique index
   `uq_ranking_runs_queued_per_project` (0015), không ở tầng ứng dụng — nên test
   phải chạm DB thật, không mock được.

2. **Chiếm run.** Hai worker cùng nhặt một job phải có ĐÚNG một bên thắng. Bên
   thua nhận `RUN_NOT_CLAIMABLE` và kết thúc sạch — đó là trạng thái BÌNH
   THƯỜNG, không phải lỗi: ném lỗi sẽ khiến RQ retry một việc đã có người làm.

Không cần Redis: `enqueue_ranking` và `run_ranking` đều thuần DB. Phần đẩy job
vào RQ nằm ở `src/services/ranking_trigger.py`, tách riêng đúng để test này chạy
được mà không phải dựng hàng đợi.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.tables import ranking_runs
from src.ranking.service import RankingError, enqueue_ranking, run_ranking
from tests.conftest import db_skip_reason
from tests.test_agent_e2e import PROJECT_ID, _insert_config, _insert_dataset

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]


@pytest_asyncio.fixture
async def factory(truncate_all, monkeypatch):
    session_factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    monkeypatch.setattr(
        "src.ranking.service.get_session_factory", lambda: session_factory, raising=False
    )
    await _insert_config(session_factory)
    await _insert_dataset(session_factory)
    return session_factory


async def _run_row(factory, run_id):
    async with factory() as session:
        return (
            await session.execute(sa.select(ranking_runs).where(ranking_runs.c.id == run_id))
        ).mappings().first()


async def _count_runs(factory) -> int:
    async with factory() as session:
        return await session.scalar(sa.select(sa.func.count()).select_from(ranking_runs))


# --- 1. Chống dồn -----------------------------------------------------------


async def test_first_enqueue_creates_a_queued_run(factory):
    run_id, created = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)

    assert created is True
    row = await _run_row(factory, run_id)
    assert row["status"] == "queued"
    assert row["trigger"] == "sync"
    assert row["attempt"] == 0
    # Bộ trọng số CHƯA được chốt lúc xếp hàng: config có thể được publish lại
    # trong lúc job nằm chờ, nên nó chỉ được gán lúc CHIẾM run.
    assert row["config_version_id"] is None
    assert row["started_at"] is None


async def test_second_enqueue_coalesces_into_the_same_run(factory):
    first_id, first_created = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)
    second_id, second_created = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)

    assert first_created is True
    assert second_created is False, "lần thứ hai phải GỘP, không tạo run mới"
    assert second_id == first_id
    assert await _count_runs(factory) == 1


async def test_a_hundred_triggers_still_produce_exactly_one_queued_run(factory):
    """Đúng kịch bản mà quyết định #2 của §1 tồn tại để chặn: 100 lô đồng bộ
    trong một phút → vẫn chỉ 1 lần tính lại."""
    ids = set()
    for _ in range(100):
        run_id, _created = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)
        ids.add(run_id)

    assert len(ids) == 1
    assert await _count_runs(factory) == 1


async def test_coalescing_keeps_the_scope_of_the_latest_trigger_only(factory):
    """Ghi lại một tính chất ĐÃ BIẾT, không phải một mong muốn.

    SQL của §8.3 là `scope_ids = ranking_runs.scope_ids || excluded.scope_ids`.
    `||` của jsonb là hợp nhất NÔNG, nên khoá `area_ids` của lần kích hoạt sau
    ĐÈ LÊN lần trước chứ không hợp nhất hai danh sách.

    Vì sao chấp nhận được, chứ không phải một lỗi đang chờ sửa: mọi lần tính lại
    đều có `scope_type='project'` và tính lại TOÀN dự án (§8.2 — mọi dòng trong
    ma trận cò đều ghi "cả dự án"). `scope_ids` chỉ mang tính truy vết, không
    thu hẹp phạm vi tính toán, nên mất một phân khu ở đây KHÔNG làm sai điểm của
    bất kỳ căn nào. Nếu sau này `scope_ids` được dùng để giới hạn phạm vi thật,
    test này phải ĐỎ — và lúc đó nó là tín hiệu đúng."""
    area_a, area_b = str(uuid.uuid4()), str(uuid.uuid4())
    run_id, _ = await enqueue_ranking(PROJECT_ID, trigger="sync", area_ids=[area_a], session_factory=factory)
    await enqueue_ranking(PROJECT_ID, trigger="sync", area_ids=[area_b], session_factory=factory)

    row = await _run_row(factory, run_id)
    assert row["scope_ids"]["area_ids"] == [area_b]


async def test_a_finished_run_does_not_block_a_new_one(factory):
    """Partial index chỉ áp cho `status='queued'`. Run đã xong KHÔNG được chặn
    lần tính lại tiếp theo — nếu chặn thì dự án đóng băng sau lần chạy đầu."""
    first_id, _ = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)
    await run_ranking(PROJECT_ID, run_id=first_id, session_factory=factory)

    second_id, created = await enqueue_ranking(PROJECT_ID, trigger="manual", session_factory=factory)
    assert created is True
    assert second_id != first_id


# --- 2. Chiếm run -----------------------------------------------------------


async def test_claiming_a_queued_run_moves_it_to_completed(factory):
    run_id, _ = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)

    result = await run_ranking(PROJECT_ID, run_id=run_id, session_factory=factory)

    assert result.run_id == run_id, "phải dùng LẠI run đã xếp hàng, không tạo run thứ hai"
    row = await _run_row(factory, run_id)
    assert row["status"] == "completed"
    assert row["attempt"] == 1, "chiếm một lần thì attempt tăng đúng một"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    assert row["config_version_id"] is not None, "bộ trọng số phải được chốt lúc chiếm"
    assert await _count_runs(factory) == 1


async def test_the_second_worker_to_claim_the_same_run_is_rejected(factory):
    """Cuộc đua đặt ở DB (`UPDATE ... WHERE status IN ('queued','failed')` +
    kiểm `rowcount`), không ở khoá trong bộ nhớ: tiến trình worker có thể chết,
    khoá bộ nhớ không sống sót, còn hàng `queued` thì có."""
    run_id, _ = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)
    await run_ranking(PROJECT_ID, run_id=run_id, session_factory=factory)

    with pytest.raises(RankingError) as exc:
        await run_ranking(PROJECT_ID, run_id=run_id, session_factory=factory)
    assert exc.value.code == "RUN_NOT_CLAIMABLE"


async def test_a_failed_run_can_be_reclaimed(factory):
    """`failed` nằm trong tập chiếm được để RQ `Retry` làm lại đúng run đó, thay
    vì bỏ lại một dòng `failed` vĩnh viễn và sinh run mới mỗi lần thử."""
    run_id, _ = await enqueue_ranking(PROJECT_ID, trigger="sync", session_factory=factory)
    async with factory() as session:
        await session.execute(
            sa.update(ranking_runs)
            .where(ranking_runs.c.id == run_id)
            .values(status="failed", finished_at=sa.func.now())
        )
        await session.commit()

    result = await run_ranking(PROJECT_ID, run_id=run_id, session_factory=factory)

    assert result.run_id == run_id
    row = await _run_row(factory, run_id)
    assert row["status"] == "completed"
    assert row["attempt"] == 1


async def test_claiming_an_unknown_run_is_rejected_not_silently_created(factory):
    with pytest.raises(RankingError) as exc:
        await run_ranking(PROJECT_ID, run_id=uuid.uuid4(), session_factory=factory)
    assert exc.value.code == "RUN_NOT_CLAIMABLE"
    assert await _count_runs(factory) == 0, "không được lặng lẽ tạo run thay thế"


async def test_running_without_a_run_id_still_creates_its_own_run(factory):
    """Đường ĐỒNG BỘ (nút "Tính lại") không đi qua hàng đợi và phải giữ nguyên
    hành vi cũ — 19 test của `POST /ranking/run` dựa vào điều này."""
    result = await run_ranking(PROJECT_ID, session_factory=factory)

    row = await _run_row(factory, result.run_id)
    assert row["status"] == "completed"
    assert row["trigger"] == "manual"
    assert row["attempt"] == 1
