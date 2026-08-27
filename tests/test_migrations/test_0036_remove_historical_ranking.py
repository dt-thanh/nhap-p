"""Static contract for removal of the retired historical-ranking schema."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic/versions/0036_remove_historical_ranking.py"


def test_removal_migration_targets_only_the_retired_materialized_table():
    source = MIGRATION.read_text()

    assert 'revision: str = "0036_remove_historical_ranking"' in source
    assert 'down_revision: str | None = "0035_evidence_document_chunks"' in source
    assert 'TABLE = "unit_inventory_daily"' in source
    assert 'op.drop_table(TABLE)' in source
    assert 'op.drop_index(INDEX_AREA_DATE, table_name=TABLE)' in source
    assert 'op.drop_index(INDEX_STAT_DATE, table_name=TABLE)' in source
    assert 'unit_status_history' not in source
    assert 'deal_status_history' not in source
