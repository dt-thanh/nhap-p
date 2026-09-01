"""Database-backed checks for the semantic Agent context contract.

Run with ``bash scripts/test_db.sh`` so the shared ``*_test`` database is used.
"""

from __future__ import annotations

import json
import re

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.agents.graph import answer
from src.agents.tools import build_context
from src.ranking.service import run_ranking
from tests.conftest import db_skip_reason
from tests.ranking_fixture import PROJECT_ID, _insert_config, _insert_dataset


_SKIP = db_skip_reason()
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")]
PROJECT = "P-AGENT-TEST-1"


@pytest_asyncio.fixture
async def semantic_context(truncate_all, monkeypatch):
    factory = async_sessionmaker(truncate_all, expire_on_commit=False)
    monkeypatch.setattr("src.agents.tools.get_session_factory", lambda factory=factory: factory)
    await _insert_config(factory)
    await _insert_dataset(factory)
    await run_ranking(PROJECT_ID, session_factory=factory)
    return await build_context("Tình hình dự án", project_id=PROJECT)


def assert_no_hallucinated_numbers(text: str, context: dict) -> None:
    serialized = json.dumps(context, ensure_ascii=False, default=str)
    for token in re.findall(r"\d+(?:[.,]\d+)?%?", text):
        assert token in serialized, f"numeric token {token!r} is absent from DATA_CONTEXT"


async def _ask(question: str) -> dict:
    return await answer(question, project_id=PROJECT)


async def test_context_contains_area_and_unit_explanations(semantic_context):
    area = semantic_context["areas"][0]
    unit = semantic_context["top_ranked_units"][0]
    assert {"booking_count", "conversion_level", "demand_level", "narrative"} <= area.keys()
    assert {"demand_label", "deal_funnel_summary", "sellability_label", "reason", "top_contribution_factors"} <= unit.keys()
    assert unit["score_model"] == "v2_legacy"
    assert semantic_context["summary"]["market_posture"]


@pytest.mark.parametrize(
    ("question", "required"),
    [
        ("Tuần này nên ưu tiên bán căn nào?", "Top căn nên ưu tiên"),
        ("Vì sao phân khu A bán tốt hơn phân khu B?", "Phân khu"),
        ("Phân khu nào có nhu cầu cao nhưng chuyển đổi thấp?", "nhu cầu"),
        ("Vì sao căn U-0001 xếp hạng cao hơn U-0002?", "U-0001"),
        ("Căn U-0003 có bán được không?", "Ưu tiên"),
        ("Dự báo doanh số tháng sau thế nào?", "chưa được triển khai"),
        ("Tình hình dự án P-AGENT-TEST-1 thế nào?", "Tóm tắt tình hình dự án"),
        ("So sánh dự án A và dự án B", "hãy thử"),
        ("Phân khu nào cần theo dõi gấp?", "Phân khu cần ưu tiên"),
        ("Căn U-9999 (không tồn tại) thế nào?", "không tìm thấy"),
        ("Top 5 căn tại phân khu Tower A", "Top căn nên ưu tiên"),
        ("Điểm của căn U-0001 là bao nhiêu?", "U-0001"),
    ],
)
async def test_semantic_question_answers_are_context_grounded(question, required, semantic_context):
    result = await _ask(question)
    assert required.casefold() in result["answer"].casefold()
    assert_no_hallucinated_numbers(result["answer"], result["context"])
    assert "sẽ bán được" not in result["answer"].casefold()
    assert "chắc chắn" not in result["answer"].casefold()


async def test_compare_and_explain_use_requested_units(semantic_context):
    comparison = await _ask("Vì sao căn U-0001 xếp hạng cao hơn U-0002?")
    assert "u1" in comparison["answer"].casefold()
    assert "u2" in comparison["answer"].casefold()
    explanation = await _ask("Vì sao căn U-0001 được ưu tiên?")
    assert "U-0001" in explanation["answer"]
    assert "lý do" in explanation["answer"].casefold()
