"""Cò xếp hạng: sau lô đồng bộ, và sau khi đổi config (§8.2).

Redis được thay bằng một hàng đợi giả — test này canh **quyết định có xếp hàng
hay không**, không canh RQ. Ranh giới đó cũng chính là lý do
`src/services/ranking_trigger.py` tách khỏi `src/ranking/service.py`.

Ba bất biến:

1. **Một job cho một run.** Gộp mà vẫn đẩy job thứ hai thì worker thứ hai chiếm
   hụt và ăn một lần retry vô ích.
2. **Lô KHÔNG đổi gì thì KHÔNG xếp hàng.** Dòng cuối ma trận §8.2: chỉ có
   `duplicate_noop`/`skip_stale`/`conflict` ⇒ không tính lại. Xếp hàng theo số
   bản ghi NHẬN ĐƯỢC thay vì số dòng THỰC SỰ ĐỔI sẽ khiến mỗi lần hệ nguồn gửi
   lại đúng lô cũ lại kéo theo một lần tính lại toàn dự án.
3. **Cò không bao giờ làm hỏng lô đã commit.** Redis chết là chuyện của xếp
   hạng, không phải lý do để báo cho hệ nguồn rằng dữ liệu bị từ chối.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.tables import projects, ranking_configs, ranking_runs
from src.services.ranking_trigger import trigger_ranking, trigger_ranking_all_projects
from tests.conftest import db_skip_reason

PROJECT_ID = uuid.uuid4()

_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]


class FakeQueue:
    """Ghi lại lời gọi enqueue thay vì chạm Redis."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.explode = False

    def enqueue(self, func: str, **kwargs):
        if self.explode:
            raise ConnectionError("Redis không với tới được")
        self.calls.append({"func": func, **kwargs})
        return type("Job", (), {"id": f"job-{len(self.calls)}"})()


async def _insert_project_and_config(factory) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            sa.insert(projects).values(
                id=PROJECT_ID,
                name="Trigger Test Project",
                launch_date=date(2026, 1, 1),
                created_at=now,
                updated_at=now,
                absorption_calculator="legacy_aggregate",
                external_id=f"P-TRIGGER-{uuid.uuid4().hex[:8]}",
                source_system="mini_crm",
                source_instance_id="test",
            )
        )
        await session.execute(
            sa.insert(ranking_configs).values(
                id=uuid.uuid4(),
                version=1,
                status="published",
                weights={"unit_available": {"weight": 1.0, "direction": "positive", "missing_value_policy": "skip"}},
                min_weight_coverage=0.5,
                note="trigger test",
                created_by="test",
                created_at=now,
                published_by="test",
                published_at=now,
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def wired(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    queue = FakeQueue()
    for target in ("src.ranking.service.get_session_factory", "src.services.ranking_trigger.get_session_factory"):
        monkeypatch.setattr(target, lambda f=factory: f, raising=False)
    monkeypatch.setattr("src.services.ranking_dispatch.get_queue", lambda *_a, **_k: queue, raising=False)

    await _insert_project_and_config(factory)
    return {"factory": factory, "queue": queue}


async def _queued_rows(factory) -> list:
    async with factory() as session:
        return list(
            (await session.execute(sa.select(ranking_runs).where(ranking_runs.c.status == "queued")))
            .mappings()
            .all()
        )


# --- 1. Một job cho một run -------------------------------------------------


async def test_first_trigger_enqueues_exactly_one_job(wired):
    run_id, enqueued = await trigger_ranking(PROJECT_ID, trigger="sync")

    assert enqueued is True
    assert len(wired["queue"].calls) == 1
    call = wired["queue"].calls[0]
    assert call["func"] == "src.jobs.rank_project.rank_project"
    assert call["run_id"] == str(run_id)
    assert call["trigger"] == "sync"


async def test_a_coalesced_trigger_does_not_push_a_second_job(wired):
    """Gộp mà vẫn đẩy job thì worker thứ hai chiếm hụt — một retry vô ích."""
    first_id, _ = await trigger_ranking(PROJECT_ID, trigger="sync")
    second_id, enqueued = await trigger_ranking(PROJECT_ID, trigger="sync")

    assert second_id == first_id
    assert enqueued is False
    assert len(wired["queue"].calls) == 1, "chỉ ĐÚNG một job cho một run"
    assert len(await _queued_rows(wired["factory"])) == 1


async def test_fifty_triggers_produce_one_run_and_one_job(wired):
    for _ in range(50):
        await trigger_ranking(PROJECT_ID, trigger="sync")

    assert len(await _queued_rows(wired["factory"])) == 1
    assert len(wired["queue"].calls) == 1


# --- 2. Redis chết KHÔNG làm mất việc cần làm -------------------------------


async def test_a_dead_queue_still_leaves_the_queued_row_behind(wired):
    """Thứ tự "ghi DB trước, đẩy RQ sau" tồn tại để có đúng tính chất này: Redis
    chết thì việc cần làm vẫn còn dấu vết trong DB và xếp lại được. Làm ngược
    lại sẽ sinh job mồ côi trỏ vào một run chưa tồn tại."""
    wired["queue"].explode = True

    run_id, enqueued = await trigger_ranking(PROJECT_ID, trigger="sync")

    assert enqueued is False
    assert run_id is not None, "vẫn phải trả run_id — công việc đã được ghi nhận"
    rows = await _queued_rows(wired["factory"])
    assert len(rows) == 1
    assert rows[0]["id"] == run_id


async def test_a_dead_queue_never_raises_into_the_caller(wired):
    """Lô đồng bộ đã COMMIT trước khi cò chạy. Ném lỗi ở đây là báo cho hệ nguồn
    rằng dữ liệu bị từ chối trong khi nó đã được nhận."""
    wired["queue"].explode = True
    await trigger_ranking(PROJECT_ID, trigger="sync")  # không được ném


# --- 3. Cò sau khi đổi config: MỌI dự án ------------------------------------


async def test_config_change_enqueues_every_project(wired):
    """§8.2: publish config là thay đổi TOÀN CỤC — mọi điểm cũ ở mọi dự án đều
    mất hiệu lực, nên một job cho MỖI dự án."""
    counts = await trigger_ranking_all_projects(trigger="config_change")

    assert counts["projects"] >= 1
    assert counts["enqueued"] == counts["projects"]
    assert counts["failed"] == 0
    assert len(wired["queue"].calls) == counts["projects"]
    assert all(call["trigger"] == "config_change" for call in wired["queue"].calls)


async def test_config_change_coalesces_with_a_run_already_waiting(wired):
    """Một dự án đang có run chờ (từ lô đồng bộ) không được sinh run thứ hai chỉ
    vì config vừa đổi — cùng một lần tính lại phục vụ được cả hai lý do."""
    await trigger_ranking(PROJECT_ID, trigger="sync")
    counts = await trigger_ranking_all_projects(trigger="config_change")

    assert counts["coalesced"] >= 1
    assert len(await _queued_rows(wired["factory"])) == counts["projects"]


async def test_one_broken_project_does_not_stop_the_others(wired, monkeypatch):
    """Bỏ sót một dự án còn hơn bỏ sót tất cả."""
    calls = {"n": 0}
    real = trigger_ranking

    async def flaky(project_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, False  # dự án đầu "hỏng"
        return await real(project_id, **kwargs)

    monkeypatch.setattr("src.services.ranking_trigger.trigger_ranking", flaky, raising=False)
    counts = await trigger_ranking_all_projects(trigger="config_change")

    assert counts["failed"] == 1
    assert counts["projects"] == counts["failed"] + counts["enqueued"] + counts["coalesced"]
