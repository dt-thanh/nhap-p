"""Auditable reconciliation for durable ranking-run intents.

This is deliberately a service-layer operation, not a SQL maintenance script.
It accepts only a specific run ID, locks that row, and fails closed whenever RQ
still reports a valid queued/scheduled/started backing job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from rq.exceptions import NoSuchJobError
from rq.job import Job

from src.config import get_settings
from src.db import get_session_factory
from src.models.tables import ranking_config_audit_events, ranking_runs, ranking_weight_proposals
from src.task_queue import get_redis


def _now() -> datetime:
    return datetime.now(UTC)


def queue_job_state(job_id: str) -> str:
    """Return RQ state without exposing queue implementation to callers.

    A Redis outage is deliberately *not* treated as a missing job.  Recovery
    must fail closed rather than turn a healthy queued run into a failure just
    because the control-plane lookup was unavailable.
    """
    try:
        return str(Job.fetch(job_id, connection=get_redis()).get_status())
    except NoSuchJobError:
        return "missing"
    except Exception:  # noqa: BLE001 - operational lookup must fail closed
        return "unavailable"


async def reconcile_stuck_ranking_run(
    *,
    run_id: uuid.UUID,
    actor_identity_subject: str,
    actor_expert_id: uuid.UUID | None = None,
    reason: str,
    stale_after: timedelta | None = None,
    session_factory=None,
) -> dict[str, Any]:
    """Mark only a stale, demonstrably non-executable queued intent failed.

    It is idempotent: a terminal/healthy run is returned unchanged.  The caller
    must authorize project scope before invoking this function; the immutable
    audit record captures who made the operational determination and why.
    """
    normalized_reason = reason.strip()
    if len(normalized_reason) < 10:
        raise ValueError("RECOVERY_REASON_REQUIRED")
    factory = session_factory or get_session_factory()
    cutoff = _now() - (stale_after or timedelta(seconds=get_settings().ranking_run_stale_seconds))
    async with factory() as session:
        run = (
            await session.execute(
                sa.select(ranking_runs).where(ranking_runs.c.id == run_id).with_for_update()
            )
        ).mappings().first()
        if run is None:
            raise LookupError("RANKING_RUN_NOT_FOUND")
        run = dict(run)
        if run["status"] not in {"queued", "deferred"}:
            await session.rollback()
            return {"changed": False, "reason_code": "RUN_NOT_RECONCILABLE", "run": run}

        if run["status"] == "deferred":
            blocking = await session.scalar(
                sa.select(ranking_runs.c.id).where(
                    ranking_runs.c.project_id == run["project_id"],
                    ranking_runs.c.status.in_(("queued", "running")),
                )
            )
            if blocking is not None:
                await session.rollback()
                return {"changed": False, "reason_code": "AWAITING_VALID_PRIOR_RUN", "run": run}

            # A prior worker can die after it commits its terminal state and
            # before its post-commit promotion.  In that case this is not a
            # failed intent: promote the same immutable AHP run exactly once
            # and let the normal dispatcher attach an RQ job after commit.
            await session.execute(
                sa.update(ranking_runs)
                .where(ranking_runs.c.id == run_id, ranking_runs.c.status == "deferred")
                .values(status="queued", error_summary={})
            )
            proposal_id = await session.scalar(
                sa.select(ranking_weight_proposals.c.id).where(
                    ranking_weight_proposals.c.applied_ranking_run_id == run_id
                )
            )
            if proposal_id is not None:
                await session.execute(
                    sa.update(ranking_weight_proposals)
                    .where(
                        ranking_weight_proposals.c.id == proposal_id,
                        ranking_weight_proposals.c.ahp_application_status == "awaiting_prior_run",
                    )
                    .values(ahp_application_status="queued", updated_at=_now())
                )
            await session.execute(
                sa.insert(ranking_config_audit_events).values(
                    id=uuid.uuid4(),
                    ranking_config_id=run["config_version_id"],
                    proposal_id=proposal_id,
                    ranking_run_id=run_id,
                    actor_expert_id=actor_expert_id,
                    actor_identity_subject=actor_identity_subject,
                    event_type="ranking_run_deferred_promoted",
                    before_status="deferred",
                    after_status="queued",
                    before_state={"rq_job_id": None},
                    after_state={"recovery_reason": normalized_reason},
                    created_at=_now(),
                )
            )
            await session.commit()
            from src.ranking.service import dispatch_persisted_ranking_run

            dispatched = await dispatch_persisted_ranking_run(
                project_id=run["project_id"],
                run_id=run_id,
                trigger=run["trigger"],
                session_factory=factory,
            )
            run.update(status="queued", error_summary={})
            return {
                "changed": True,
                "reason_code": "DEFERRED_RUN_PROMOTED" if dispatched else "DEFERRED_RUN_DISPATCH_FAILED",
                "run": run,
            }

        queue_state = queue_job_state(run["rq_job_id"]) if run.get("rq_job_id") else "missing"
        if queue_state == "unavailable":
            await session.rollback()
            return {"changed": False, "reason_code": "RQ_STATE_UNAVAILABLE", "queue_state": queue_state, "run": run}
        if queue_state in {"queued", "scheduled", "started", "deferred"}:
            await session.rollback()
            return {"changed": False, "reason_code": "RQ_JOB_STILL_LIVE", "queue_state": queue_state, "run": run}
        if run["enqueued_at"] > cutoff:
            await session.rollback()
            return {"changed": False, "reason_code": "RUN_NOT_STALE", "queue_state": queue_state, "run": run}

        failure_code = "RQ_JOB_DISAPPEARED" if queue_state == "missing" else "RQ_JOB_TERMINAL"
        failure = {
            "code": failure_code,
            "queue_state": queue_state,
            "reconciled_at": _now().isoformat(),
            "recovery_reason": normalized_reason,
        }
        await session.execute(
            sa.update(ranking_runs)
            .where(ranking_runs.c.id == run_id, ranking_runs.c.status.in_(("queued", "deferred")))
            .values(status="failed", error_summary=failure, finished_at=_now())
        )
        proposal_id = await session.scalar(
            sa.select(ranking_weight_proposals.c.id).where(ranking_weight_proposals.c.applied_ranking_run_id == run_id)
        )
        if proposal_id is not None:
            await session.execute(
                sa.update(ranking_weight_proposals)
                .where(
                    ranking_weight_proposals.c.id == proposal_id,
                    ranking_weight_proposals.c.ahp_application_status.in_(
                        ("pending", "awaiting_prior_run", "queued", "running")
                    ),
                )
                .values(ahp_application_status="failed", updated_at=_now())
            )
        await session.execute(
            sa.insert(ranking_config_audit_events).values(
                id=uuid.uuid4(),
                ranking_config_id=run["config_version_id"],
                proposal_id=proposal_id,
                ranking_run_id=run_id,
                actor_expert_id=actor_expert_id,
                actor_identity_subject=actor_identity_subject,
                event_type="ranking_run_reconciled_failed",
                before_status=run["status"],
                after_status="failed",
                before_state={"rq_job_id": run.get("rq_job_id")},
                after_state=failure,
                created_at=_now(),
            )
        )
        await session.commit()
        run.update(status="failed", error_summary=failure, finished_at=_now())
        return {"changed": True, "reason_code": failure_code, "run": run}
