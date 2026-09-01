"""Dependency-neutral RQ dispatch for durable ``ranking_runs`` intents.

This module intentionally knows neither governance nor the ranking engine.  It
is shared by the trigger surface and the ranking worker completion handoff,
which prevents a service-to-trigger import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rq import Retry

from src.logging_config import get_logger
from src.task_queue import INGEST_QUEUE, get_queue

log = get_logger("src.services.ranking_dispatch")
RANKING_QUEUE = INGEST_QUEUE


@dataclass(frozen=True)
class RankingDispatchResult:
    enqueued: bool
    job_id: str | None = None
    error_code: str | None = None


def dispatch_ranking_run(*, project_id: str, run_id: str, trigger: str) -> RankingDispatchResult:
    """Enqueue one already-persisted run intent without mutating the database."""
    try:
        job: Any = get_queue(RANKING_QUEUE).enqueue(
            "src.jobs.rank_project.rank_project",
            project_id=project_id,
            run_id=run_id,
            trigger=trigger,
            retry=Retry(max=3, interval=[10, 30, 60]),
        )
    except Exception as exc:  # Redis is outside the SQL transaction.
        log.error(
            "ranking.dispatch.failed",
            project_id=project_id,
            run_id=run_id,
            trigger=trigger,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return RankingDispatchResult(enqueued=False, error_code="RQ_DISPATCH_FAILED")
    return RankingDispatchResult(enqueued=True, job_id=str(job.id))
