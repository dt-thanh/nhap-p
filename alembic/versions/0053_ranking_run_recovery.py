"""Make ranking dispatch/recovery and AHP application progress durable.

`deferred` is an open ranking-run intent: it is persisted only when an AHP
application must wait for already-running/queued project work.  It never has a
worker job until the prior run finishes and the service promotes it to `queued`.
The additive queue-job identifier lets reconciliation prove whether a queued
intent still has a live RQ backing job instead of guessing from age alone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053_ranking_run_recovery"
down_revision: str | None = "0052_proposal_evidence_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_AUDIT_EVENTS = (
    "created", "submitted", "reviewed", "approved", "rejected", "published", "rolled_back", "archived", "deleted", "restored"
)
_RECOVERY_AUDIT_EVENTS = (
    "ahp_application_queued",
    "ahp_application_deferred",
    "ahp_application_retry_requested",
    "ranking_run_reconciled_failed",
    "ranking_run_deferred_promoted",
)


def upgrade() -> None:
    op.add_column("ranking_runs", sa.Column("rq_job_id", sa.Text(), nullable=True))
    op.create_index("ix_ranking_runs_rq_job_id", "ranking_runs", ["rq_job_id"])

    # A recovery may concern a legacy sync run with neither a proposal nor a
    # config binding. Record the real run instead of inventing either parent.
    op.add_column("ranking_config_audit_events", sa.Column("ranking_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_rcae_ranking_run_id", "ranking_config_audit_events", "ranking_runs", ["ranking_run_id"], ["id"]
    )
    op.create_index(
        "ix_rcae_ranking_run_created", "ranking_config_audit_events", ["ranking_run_id", sa.text("created_at DESC")]
    )
    op.drop_constraint("ck_rcae_entity_reference", "ranking_config_audit_events", type_="check")
    op.create_check_constraint(
        "ck_rcae_entity_reference",
        "ranking_config_audit_events",
        "ranking_config_id IS NOT NULL OR proposal_id IS NOT NULL OR ranking_run_id IS NOT NULL",
    )

    # 0015's original constraints only know queued/running as open states.
    # Replace them atomically with the strictly additive `deferred` state.
    op.drop_constraint("ck_ranking_runs_status", "ranking_runs", type_="check")
    op.drop_constraint("ck_ranking_runs_finished_by_status", "ranking_runs", type_="check")
    op.create_check_constraint(
        "ck_ranking_runs_status",
        "ranking_runs",
        "status IN ('deferred', 'queued', 'running', 'completed', 'partially_completed', 'failed', 'skipped_stale')",
    )

    op.drop_constraint("ck_rcae_event_type", "ranking_config_audit_events", type_="check")
    op.create_check_constraint(
        "ck_rcae_event_type",
        "ranking_config_audit_events",
        "event_type IN (" + ", ".join(repr(value) for value in (*_PREVIOUS_AUDIT_EVENTS, *_RECOVERY_AUDIT_EVENTS)) + ")",
    )
    op.create_check_constraint(
        "ck_ranking_runs_finished_by_status",
        "ranking_runs",
        "(status IN ('deferred', 'queued', 'running') AND finished_at IS NULL) "
        "OR (status IN ('completed', 'partially_completed', 'failed', 'skipped_stale') AND finished_at IS NOT NULL)",
    )

    op.drop_constraint("ck_rwp_ahp_application_status_valid", "ranking_weight_proposals", type_="check")
    op.create_check_constraint(
        "ck_rwp_ahp_application_status_valid",
        "ranking_weight_proposals",
        "ahp_application_status IS NULL OR ahp_application_status IN "
        "('pending', 'awaiting_prior_run', 'queued', 'running', 'applied', 'failed')",
    )


def downgrade() -> None:
    conn = op.get_bind()
    deferred = conn.execute(sa.text("SELECT count(*) FROM ranking_runs WHERE status = 'deferred'")).scalar()
    expanded = conn.execute(
        sa.text(
            "SELECT count(*) FROM ranking_weight_proposals "
            "WHERE ahp_application_status IN ('awaiting_prior_run', 'queued', 'running')"
        )
    ).scalar()
    if deferred or expanded:
        raise RuntimeError(
            "Refusing to downgrade 0053 while durable deferred ranking intents or expanded AHP application states exist."
        )
    audit_events = conn.execute(
        sa.text(
            "SELECT count(*) FROM ranking_config_audit_events "
            "WHERE event_type IN ('ahp_application_queued', 'ahp_application_deferred', "
            "'ahp_application_retry_requested', 'ranking_run_reconciled_failed', "
            "'ranking_run_deferred_promoted')"
        )
    ).scalar()
    if audit_events:
        raise RuntimeError("Refusing to downgrade 0053 while recovery audit events exist.")
    run_only_events = conn.execute(
        sa.text(
            "SELECT count(*) FROM ranking_config_audit_events "
            "WHERE ranking_config_id IS NULL AND proposal_id IS NULL AND ranking_run_id IS NOT NULL"
        )
    ).scalar()
    if run_only_events:
        raise RuntimeError("Refusing to downgrade 0053 while run-only audit events exist.")
    op.drop_constraint("ck_rcae_event_type", "ranking_config_audit_events", type_="check")
    op.create_check_constraint(
        "ck_rcae_event_type",
        "ranking_config_audit_events",
        "event_type IN (" + ", ".join(repr(value) for value in _PREVIOUS_AUDIT_EVENTS) + ")",
    )
    op.drop_constraint("ck_rwp_ahp_application_status_valid", "ranking_weight_proposals", type_="check")
    op.create_check_constraint(
        "ck_rwp_ahp_application_status_valid",
        "ranking_weight_proposals",
        "ahp_application_status IS NULL OR ahp_application_status IN ('pending', 'applied', 'failed')",
    )
    op.drop_constraint("ck_ranking_runs_finished_by_status", "ranking_runs", type_="check")
    op.drop_constraint("ck_ranking_runs_status", "ranking_runs", type_="check")
    op.create_check_constraint(
        "ck_ranking_runs_status",
        "ranking_runs",
        "status IN ('queued', 'running', 'completed', 'partially_completed', 'failed', 'skipped_stale')",
    )
    op.create_check_constraint(
        "ck_ranking_runs_finished_by_status",
        "ranking_runs",
        "(status IN ('queued', 'running') AND finished_at IS NULL) "
        "OR (status IN ('completed', 'partially_completed', 'failed', 'skipped_stale') AND finished_at IS NOT NULL)",
    )
    op.drop_constraint("ck_rcae_entity_reference", "ranking_config_audit_events", type_="check")
    op.create_check_constraint(
        "ck_rcae_entity_reference",
        "ranking_config_audit_events",
        "ranking_config_id IS NOT NULL OR proposal_id IS NOT NULL",
    )
    op.drop_index("ix_rcae_ranking_run_created", table_name="ranking_config_audit_events")
    op.drop_constraint("fk_rcae_ranking_run_id", "ranking_config_audit_events", type_="foreignkey")
    op.drop_column("ranking_config_audit_events", "ranking_run_id")
    op.drop_index("ix_ranking_runs_rq_job_id", table_name="ranking_runs")
    op.drop_column("ranking_runs", "rq_job_id")
