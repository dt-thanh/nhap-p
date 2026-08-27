"""PostgreSQL migration coverage for 0035 evidence document chunks (§21.4)."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PostgreSQL is unavailable: TEST_DATABASE_URL/DATABASE_URL is not configured",
)

PREVIOUS_REVISION = "0034_expert_ranking_governance"
REVISION = "0035_evidence_document_chunks"


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg2://")


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )


@pytest.fixture
def scratch_db():
    name = f"mig35_{uuid.uuid4().hex[:12]}_test"
    admin = sa.create_engine(_sync_url(_with_database(TEST_DATABASE_URL, "postgres")), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(TEST_DATABASE_URL, name)
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
                {"name": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def upgraded(scratch_db):
    result = _alembic(scratch_db, "upgrade", REVISION)
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(_sync_url(scratch_db))
    try:
        yield {"url": scratch_db, "engine": engine}
    finally:
        engine.dispose()


def _seed_document(conn) -> uuid.UUID:
    expert_id = uuid.uuid4()
    document_id = uuid.uuid4()
    conn.execute(
        sa.text("INSERT INTO expert_profiles (id, identity_subject, status) VALUES (:id, 'subject-1', 'active')"),
        {"id": expert_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO ranking_evidence_documents "
            "(id, uploaded_by_expert_id, original_filename, mime_type, object_storage_key, "
            "sha256_checksum, file_size_bytes, extraction_status) "
            "VALUES (:id, :expert, 'evidence.pdf', 'application/pdf', 'ranking/evidence/one.pdf', :checksum, 10, 'pending')"
        ),
        {"id": document_id, "expert": expert_id, "checksum": "a" * 64},
    )
    return document_id


def test_upgrade_creates_chunk_table_extension_and_index(upgraded):
    engine = upgraded["engine"]
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
            == 1
        )
        for table_name in ("ranking_evidence_document_chunks", "ranking_evidence_extraction_attempts"):
            assert (
                conn.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = :table_name"
                    ),
                    {"table_name": table_name},
                ).scalar()
                == table_name
            )
        for index_name in ("ix_redc_document_id", "ix_redc_embedding_hnsw", "ix_reea_document_created"):
            assert (
                conn.execute(
                    sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :name"), {"name": index_name}
                ).scalar()
                == 1
            )
        assert (
            conn.execute(
                sa.text(
                    "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('ranking_evidence_document_chunks') "
                    "AND conname = 'uq_redc_document_chunk'"
                )
            ).scalar()
            == 1
        )


def test_chunk_insert_and_cosine_distance_query(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        document_id = _seed_document(conn)
        vector_literal = "[" + ",".join(["0.001"] * 1536) + "]"
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_document_chunks "
                "(id, document_id, chunk_index, page_number, content, token_count, embedding_model, embedding) "
                "VALUES (:id, :document_id, 0, 1, 'the project sold 12 units in July', 8, "
                "'text-embedding-3-small', :embedding)"
            ),
            {"id": uuid.uuid4(), "document_id": document_id, "embedding": vector_literal},
        )
        distance = conn.execute(
            sa.text(
                "SELECT embedding <=> :query FROM ranking_evidence_document_chunks WHERE document_id = :document_id"
            ),
            {"query": vector_literal, "document_id": document_id},
        ).scalar_one()
        assert distance == pytest.approx(0.0, abs=1e-6)


def test_chunk_content_and_token_count_constraints(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        document_id = _seed_document(conn)
    vector_literal = "[" + ",".join(["0.001"] * 1536) + "]"
    common = {
        "document_id": document_id,
        "embedding": vector_literal,
    }
    for overrides in (
        {"content": "", "token_count": 5, "chunk_index": 0},
        {"content": "ok", "token_count": 0, "chunk_index": 1},
        {"content": "ok", "token_count": 5, "chunk_index": -1},
    ):
        values = {**common, **overrides, "id": uuid.uuid4()}
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO ranking_evidence_document_chunks "
                        "(id, document_id, chunk_index, content, token_count, embedding_model, embedding) "
                        "VALUES (:id, :document_id, :chunk_index, :content, :token_count, "
                        "'text-embedding-3-small', :embedding)"
                    ),
                    values,
                )


def test_duplicate_chunk_index_for_same_document_rejected(upgraded):
    engine = upgraded["engine"]
    vector_literal = "[" + ",".join(["0.001"] * 1536) + "]"
    with engine.begin() as conn:
        document_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_document_chunks "
                "(id, document_id, chunk_index, content, token_count, embedding_model, embedding) "
                "VALUES (:id, :document_id, 0, 'first', 5, 'text-embedding-3-small', :embedding)"
            ),
            {"id": uuid.uuid4(), "document_id": document_id, "embedding": vector_literal},
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_evidence_document_chunks "
                    "(id, document_id, chunk_index, content, token_count, embedding_model, embedding) "
                    "VALUES (:id, :document_id, 0, 'second', 5, 'text-embedding-3-small', :embedding)"
                ),
                {"id": uuid.uuid4(), "document_id": document_id, "embedding": vector_literal},
            )


def test_append_only_guard_blocks_update_and_delete(upgraded):
    engine = upgraded["engine"]
    vector_literal = "[" + ",".join(["0.001"] * 1536) + "]"
    chunk_id = uuid.uuid4()
    with engine.begin() as conn:
        document_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_document_chunks "
                "(id, document_id, chunk_index, content, token_count, embedding_model, embedding) "
                "VALUES (:id, :document_id, 0, 'first', 5, 'text-embedding-3-small', :embedding)"
            ),
            {"id": chunk_id, "document_id": document_id, "embedding": vector_literal},
        )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE ranking_evidence_document_chunks SET content = 'edited' WHERE id = :id"),
                {"id": chunk_id},
            )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM ranking_evidence_document_chunks WHERE id = :id"),
                {"id": chunk_id},
            )


def test_extraction_attempt_insert_and_append_only_guard(upgraded):
    engine = upgraded["engine"]
    attempt_id = uuid.uuid4()
    with engine.begin() as conn:
        document_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_extraction_attempts (id, document_id, status) "
                "VALUES (:id, :document_id, 'pending')"
            ),
            {"id": attempt_id, "document_id": document_id},
        )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE ranking_evidence_extraction_attempts SET status = 'succeeded' WHERE id = :id"),
                {"id": attempt_id},
            )
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM ranking_evidence_extraction_attempts WHERE id = :id"),
                {"id": attempt_id},
            )


def test_extraction_attempt_status_check_constraint(upgraded):
    engine = upgraded["engine"]
    with engine.begin() as conn:
        document_id = _seed_document(conn)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ranking_evidence_extraction_attempts (id, document_id, status) "
                    "VALUES (:id, :document_id, 'bogus_status')"
                ),
                {"id": uuid.uuid4(), "document_id": document_id},
            )


def test_extraction_attempt_downgrade_refuses_populated_table(upgraded):
    with upgraded["engine"].begin() as conn:
        document_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_extraction_attempts (id, document_id, status) "
                "VALUES (:id, :document_id, 'pending')"
            ),
            {"id": uuid.uuid4(), "document_id": document_id},
        )

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "ranking_evidence_extraction_attempts has rows" in (result.stdout + result.stderr)


def test_chunk_downgrade_refuses_populated_table(upgraded):
    vector_literal = "[" + ",".join(["0.001"] * 1536) + "]"
    with upgraded["engine"].begin() as conn:
        document_id = _seed_document(conn)
        conn.execute(
            sa.text(
                "INSERT INTO ranking_evidence_document_chunks "
                "(id, document_id, chunk_index, content, token_count, embedding_model, embedding) "
                "VALUES (:id, :document_id, 0, 'first', 5, 'text-embedding-3-small', :embedding)"
            ),
            {"id": uuid.uuid4(), "document_id": document_id, "embedding": vector_literal},
        )

    result = _alembic(upgraded["url"], "downgrade", PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "ranking_evidence_document_chunks has rows" in (result.stdout + result.stderr)
