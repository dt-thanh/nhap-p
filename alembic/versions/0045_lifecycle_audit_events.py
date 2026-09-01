"""Widen `ranking_config_audit_events.event_type` CHECK to include the new
document lifecycle actions (mandatory-scope item 4/8): `archived`, `deleted`,
`restored`. `0034`'s original list (`created, submitted, reviewed, approved,
rejected, published, rolled_back`) predates the document-archive/delete
feature — `governance.py::_write_lifecycle_event()` writes one of the three
new values into this table (when the document has a `proposal_id` to audit
against), and without this migration that INSERT fails
`ck_rcae_event_type` outright (`CheckViolationError`, caught live by this
pass's own test run before this migration existed).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045_lifecycle_audit_events"
down_revision: str | None = "0044_evidence_document_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_EVENT_TYPES = ("created", "submitted", "reviewed", "approved", "rejected", "published", "rolled_back")
_NEW_EVENT_TYPES = (*_OLD_EVENT_TYPES, "archived", "deleted", "restored")


def upgrade() -> None:
    op.drop_constraint("ck_rcae_event_type", "ranking_config_audit_events", type_="check")
    op.create_check_constraint(
        "ck_rcae_event_type",
        "ranking_config_audit_events",
        "event_type IN (" + ", ".join(f"'{v}'" for v in _NEW_EVENT_TYPES) + ")",
    )


def downgrade() -> None:
    bind = op.get_bind()
    offending = bind.execute(
        sa.text(
            "SELECT count(*) FROM ranking_config_audit_events WHERE event_type IN ('archived', 'deleted', 'restored')"
        )
    ).scalar()
    if offending:
        raise RuntimeError(
            f"Refusing to downgrade 0045: {offending} ranking_config_audit_events row(s) use a "
            "document-lifecycle event_type the narrower CHECK would reject"
        )
    op.drop_constraint("ck_rcae_event_type", "ranking_config_audit_events", type_="check")
    op.create_check_constraint(
        "ck_rcae_event_type",
        "ranking_config_audit_events",
        "event_type IN (" + ", ".join(f"'{v}'" for v in _OLD_EVENT_TYPES) + ")",
    )
