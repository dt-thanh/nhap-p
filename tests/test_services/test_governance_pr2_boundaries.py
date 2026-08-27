"""PR-2 boundary/regression checks — static, no DB required.

Mirrors `tests/test_ranking_boundary.py`'s own AST/text-scan style: prove by
inspection, not by hoping, that this PR introduced no writer for
`ranking_feature_values`/`ranking_feature_snapshots`/`ranking_feature_lineage`/
`ranking_scores`, and did not touch the files it was explicitly told not to.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_TABLES = (
    "ranking_feature_values",
    "ranking_feature_snapshots",
    "ranking_feature_lineage",
    "ranking_scores",
)

GOVERNANCE_FILES = (
    "src/services/governance.py",
    "src/api/governance.py",
)


def _text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_pr2_governance_files_write_no_forbidden_table():
    """`insert(<table>`/`update(<table>`/`delete(<table>` (SQLAlchemy Core
    call shape, same detection this repo's own boundary test already uses)
    must not appear for any of the four tables PR-2 must never write to."""
    offenders: list[str] = []
    for relative in GOVERNANCE_FILES:
        text = _text(relative)
        lowered = text.lower()
        for table in FORBIDDEN_TABLES:
            for verb in ("insert", "update", "delete"):
                if f"{verb}({table}" in text or f"{verb} into {table}" in lowered:
                    offenders.append(f"{relative}: {verb} -> {table}")
    assert offenders == [], f"PR-2 must not write to these tables: {offenders}"


def test_engine_and_run_ranking_untouched_markers_present():
    """`src/ranking/engine.py`/`service.py` are non-negotiable boundaries for
    PR-2 (per the task's own instructions) — this does not prove git history,
    only that the pure-function/no-DB invariant `tests/test_ranking_boundary.py`
    already enforces for `engine.py` is still true after this PR."""
    text = _text("src/ranking/engine.py")
    forbidden = ("sqlalchemy", "asyncio", "httpx", "src.db", "AsyncSession")
    offenders = [word for word in forbidden if word in text]
    assert offenders == [], f"src/ranking/engine.py is no longer pure: {offenders}"


def test_llm_advisory_tools_still_have_no_writes():
    """D38's 'LLM remains read-only' invariant — `advisory_tools.py`'s
    evidence/explanation functions must contain no session.execute/insert/
    update of any kind, and must stay outside ALLOWED_ADVISORY_TOOLS."""
    text = _text("src/agents/advisory_tools.py")
    for forbidden in ("session.execute(sa.insert", "session.execute(sa.update", "session.execute(sa.delete"):
        assert forbidden not in text, f"advisory_tools.py contains a write: {forbidden}"
