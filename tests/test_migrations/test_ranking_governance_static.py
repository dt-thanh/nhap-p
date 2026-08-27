"""Dependency-free static checks for the approved database-only revisions."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_revision_chain_and_scope_guardrails_are_declared():
    migration_a = (ROOT / "alembic/versions/0033_ranking_evidence_foundation.py").read_text()
    migration_b = (ROOT / "alembic/versions/0034_expert_ranking_governance.py").read_text()
    assert 'revision: str = "0033_ranking_evidence_foundation"' in migration_a
    assert 'down_revision: str | None = "0032_replay_identity_index"' in migration_a
    assert 'revision: str = "0034_expert_ranking_governance"' in migration_b
    assert 'down_revision: str | None = "0033_ranking_evidence_foundation"' in migration_b
    assert "sa.CheckConstraint(\"scope_type = 'project'\"" in migration_a
    assert "sa.CheckConstraint(\"area_id IS NULL\"" in migration_a
    assert "sa.CheckConstraint(\"area_id IS NULL\"" in migration_b


def test_database_only_guardrails_are_visible_in_changed_files():
    migration_a = (ROOT / "alembic/versions/0033_ranking_evidence_foundation.py").read_text()
    migration_b = (ROOT / "alembic/versions/0034_expert_ranking_governance.py").read_text()
    combined = migration_a + migration_b
    assert "ranking_scores.contributions" not in combined
    assert "forecast" not in combined.lower()
    assert "object_storage_key" in combined
    assert "sha256_checksum" in combined
    assert "application/pdf" in combined
    assert "ranking_evidence_append_only_guard" in combined
    assert "ranking_governance_append_only_guard" in combined
    assert "ranking_score_id" not in combined
