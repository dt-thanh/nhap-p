"""Read/search immutable rationale chunks for submitted AHP proposals.

Embedding generation is deliberately delegated to
``evidence_extraction.embed_texts`` so evidence and rationale retrieval use one
configured OpenAI client/model.  This module never changes proposal state.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from src.db import get_session_factory
from src.models.tables import ranking_proposal_rationale_chunks, ranking_weight_proposals
from src.services import evidence_extraction


def _out(row: dict, *, similarity: float | None = None, proposal: dict | None = None) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    result: dict[str, Any] = {
        "id": str(row["id"]),
        "proposal_id": str(row["proposal_id"]),
        "criterion_key": row["criterion_key"],
        "grain": row["grain"],
        "weight": str(metadata["weight"]) if metadata.get("weight") is not None else None,
        "rationale": metadata.get("rationale"),
        "chunk_text": row["chunk_text"],
        "created_at": row["created_at"],
    }
    if similarity is not None:
        result["similarity"] = similarity
    if proposal is not None:
        result["project_id"] = str(proposal["project_id"])
        result["proposal_status"] = proposal["status"]
    return result


async def insert_rationale_chunks_in_session(session, *, proposal_id: uuid.UUID, hierarchical_weights: dict) -> int:
    """Embed and insert one immutable retrieval row per non-empty rationale.

    Called only from the locked AHP submit transaction.  A proposal can leave
    draft exactly once, and the unique key is a database backstop against
    duplicate chunks if a caller retries around a transaction boundary.
    """
    records: list[dict] = []
    for grain in ("market", "project", "area"):
        for criterion_key, spec in (hierarchical_weights.get(grain) or {}).items():
            rationale = spec.get("rationale") if isinstance(spec, dict) else None
            if not rationale:
                continue
            weight = spec["weight"]
            records.append(
                {
                    "criterion_key": criterion_key,
                    "grain": grain,
                    "weight": weight,
                    "rationale": rationale,
                    "chunk_text": f"{grain}.{criterion_key} weight={weight}: {rationale}",
                }
            )
    if not records:
        return 0

    embeddings = evidence_extraction.embed_texts([record["chunk_text"] for record in records])
    if len(embeddings) != len(records):
        raise RuntimeError("Embedding service returned an unexpected rationale vector count")

    await session.execute(
        sa.insert(ranking_proposal_rationale_chunks),
        [
            {
                "id": uuid.uuid4(),
                "proposal_id": proposal_id,
                "criterion_key": record["criterion_key"],
                "grain": record["grain"],
                "chunk_text": record["chunk_text"],
                "embedding_model": evidence_extraction.EMBEDDING_MODEL,
                "embedding": embedding,
                "metadata": {"weight": record["weight"], "rationale": record["rationale"]},
            }
            for record, embedding in zip(records, embeddings, strict=True)
        ],
    )
    return len(records)


async def retrieve_rationale_for_proposal(
    proposal_id: uuid.UUID, *, criterion_key: str | None = None, query: str | None = None, top_k: int = 5
) -> list[dict]:
    """Return exact criterion matches, all chunks, or proposal-scoped semantic matches."""
    if criterion_key and query:
        raise ValueError("criterion_key và query không được dùng cùng lúc")
    query_embedding = evidence_extraction.embed_texts([query])[0] if query else None
    async with get_session_factory()() as session:
        if query:
            distance = ranking_proposal_rationale_chunks.c.embedding.cosine_distance(query_embedding).label("distance")
            rows = (
                await session.execute(
                    sa.select(ranking_proposal_rationale_chunks, distance)
                    .where(ranking_proposal_rationale_chunks.c.proposal_id == proposal_id)
                    .order_by(distance, ranking_proposal_rationale_chunks.c.created_at.desc())
                    .limit(top_k)
                )
            ).mappings().all()
            await session.rollback()
            return [_out(row, similarity=float(1 - row["distance"])) for row in rows]

        statement = sa.select(ranking_proposal_rationale_chunks).where(
            ranking_proposal_rationale_chunks.c.proposal_id == proposal_id
        )
        if criterion_key:
            statement = statement.where(ranking_proposal_rationale_chunks.c.criterion_key == criterion_key)
        rows = (await session.execute(statement.order_by(ranking_proposal_rationale_chunks.c.created_at))).mappings().all()
        await session.rollback()
    return [_out(row) for row in rows]


async def retrieve_rationale_cross_proposals(project_id: uuid.UUID, query: str, *, top_k: int = 10) -> list[dict]:
    """Semantic retrieval over submitted-or-later AHP rationale chunks in one project."""
    query_embedding = evidence_extraction.embed_texts([query])[0]
    distance = ranking_proposal_rationale_chunks.c.embedding.cosine_distance(query_embedding).label("distance")
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ranking_proposal_rationale_chunks, ranking_weight_proposals, distance)
                .join(
                    ranking_weight_proposals,
                    ranking_weight_proposals.c.id == ranking_proposal_rationale_chunks.c.proposal_id,
                )
                .where(
                    ranking_weight_proposals.c.project_id == project_id,
                    ranking_weight_proposals.c.proposal_type == "ahp_ranking_proposal",
                )
                .order_by(distance, ranking_proposal_rationale_chunks.c.created_at.desc())
                .limit(top_k)
            )
        ).mappings().all()
        await session.rollback()
    return [
        _out(
            row,
            similarity=float(1 - row["distance"]),
            proposal={"project_id": row["project_id"], "status": row["status"]},
        )
        for row in rows
    ]
