"""Give standalone evidence documents durable project ownership.

Historical evidence is deliberately left NULL/unscoped: this revision never
guesses a project from a filename, content, uploader, or proposal history.
The append-only evidence table is not updated by this migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0047_evidence_project_scope"
down_revision: str | None = "0046_feature_rubrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("ranking_evidence_documents", sa.Column("project_id", UUID, nullable=True))
    op.add_column("ranking_evidence_documents", sa.Column("area_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_red_project_id", "ranking_evidence_documents", "projects", ["project_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_foreign_key("fk_red_area_id", "ranking_evidence_documents", "areas", ["area_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_red_project_id", "ranking_evidence_documents", ["project_id"])
    op.create_index("ix_red_area_id", "ranking_evidence_documents", ["area_id"])


def downgrade() -> None:
    op.drop_index("ix_red_area_id", table_name="ranking_evidence_documents")
    op.drop_index("ix_red_project_id", table_name="ranking_evidence_documents")
    op.drop_constraint("fk_red_area_id", "ranking_evidence_documents", type_="foreignkey")
    op.drop_constraint("fk_red_project_id", "ranking_evidence_documents", type_="foreignkey")
    op.drop_column("ranking_evidence_documents", "area_id")
    op.drop_column("ranking_evidence_documents", "project_id")
