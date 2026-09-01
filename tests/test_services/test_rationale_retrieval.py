"""Regression coverage for submitted AHP rationale retrieval.

The AHP proposal fixture owns the required canonical project/registry setup;
reusing it keeps this focused test on the retrieval boundary while continuing
to run solely against the isolated ``*_test`` database.
"""

from __future__ import annotations

import copy

import pytest

from src.services import rationale_retrieval
from tests.test_services import test_ahp_ranking_proposal as ahp

factory = ahp.factory
pytestmark = pytest.mark.asyncio


async def test_cross_proposal_retrieval_embeds_and_returns_only_submitted_project_rationales(factory, monkeypatch):
    embedding_calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        embedding_calls.append(texts)
        return [[1.0] + [0.0] * 1535 for _ in texts]

    monkeypatch.setattr("src.services.evidence_extraction.embed_texts", fake_embed)
    advisor_id = await ahp._expert("advisor-rationale-retrieval")
    first = copy.deepcopy(ahp.ZERO_PROJECT_HIERARCHICAL_WEIGHTS)
    first["market"]["market_interest_rate"]["rationale"] = "Lãi suất cần được ưu tiên khi đánh giá thị trường."
    second = copy.deepcopy(ahp.ZERO_PROJECT_HIERARCHICAL_WEIGHTS)
    second["area"]["area_accessibility"]["rationale"] = "Khả năng tiếp cận quyết định thanh khoản phân khu."

    await ahp._submitted_ahp_proposal(advisor_id=advisor_id, hierarchical_weights=first)
    await ahp._submitted_ahp_proposal(advisor_id=advisor_id, hierarchical_weights=second)

    results = await rationale_retrieval.retrieve_rationale_cross_proposals(ahp.PROJECT_ID, "thanh khoản", top_k=10)

    assert len(results) == 2
    assert {result["criterion_key"] for result in results} == {"market_interest_rate", "area_accessibility"}
    assert {result["project_id"] for result in results} == {str(ahp.PROJECT_ID)}
    assert all(result["similarity"] == 1.0 for result in results)
    assert any("Lãi suất" in text for batch in embedding_calls for text in batch)
    assert embedding_calls[-1] == ["thanh khoản"]
