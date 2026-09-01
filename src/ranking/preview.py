"""Read-only ranking preview — "Bản xem trước — chưa được công bố".

Computes what scores a CANDIDATE `weights` config would produce against the
project's real, currently-persisted feature data (the exact same
`_project_units`/`_area_features`/`_has_active_deal_by_unit`/
`_funnel_deal_counts`/`_build_feature_inputs` + `score_unit()` pipeline
`run_ranking()` itself uses), then diffs it against the CURRENTLY PUBLISHED
config's real persisted `ranking_scores` — without writing anything.

Zero write verbs in this file (grep-verified: no `insert(`/`update(`/`delete(`),
matching the same read-only discipline `src/ranking/hierarchical_view.py`
already established for PR-7. This module never touches `ranking_scores`,
`ranking_runs`, or `ranking_configs` — a preview is not a run.

Deliberately scoped to the LEGACY flat `weights` composition only (not a
hierarchical grain preview): hierarchical scores are snapshot-bound to a real
`ranking_run_id` by design (D33/D37 — Market/Project/Area feature values are
copied into an immutable per-run snapshot at cutoff), so there is no
sandbox-safe way to preview a hierarchical composition without either
creating a real run or duplicating the entire snapshot-selection machinery
read-only. This is a disclosed, real limitation, not a gap silently designed
around — see `docs/ranking/ranking_consultant.md` and `pipeline_status.md`'s
Expert Analysis entry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.db import get_session_factory
from src.models.tables import projects, ranking_configs, ranking_scores, units
from src.ranking.engine import FeatureWeight, UnitScore, rank_scores, score_unit
from src.ranking.service import (
    RankingError,
    _area_features,
    _build_feature_inputs,
    _funnel_deal_counts,
    _has_active_deal_by_unit,
    _project_units,
)
from src.services.ranking_config import ConfigError, validate_weights


@dataclass(frozen=True, slots=True)
class UnitPreviewDelta:
    unit_id: str
    unit_code: str
    external_unit_id: str
    area_id: str
    current_score: str | None
    current_rank: int | None
    preview_score: str | None
    preview_rank: int | None
    score_delta: str | None
    rank_delta: int | None
    top_contributor: str | None
    skipped: bool


@dataclass(frozen=True, slots=True)
class PreviewResult:
    project_id: str
    current_config_version: int | None
    sample_size: int
    units_scored: int
    units_skipped: int
    results: list[UnitPreviewDelta]
    top_gainers: list[UnitPreviewDelta]
    top_losers: list[UnitPreviewDelta]
    generated_at: datetime


def _parse_weights(weights: dict) -> list[FeatureWeight]:
    return [
        FeatureWeight(
            key=key,
            weight=Decimal(str(spec["weight"])),
            direction=spec["direction"],
            missing_value_policy=spec["missing_value_policy"],
            min_confidence=Decimal(str(spec.get("min_confidence", 0))),
        )
        for key, spec in weights.items()
    ]


def _top_contributor(score: UnitScore) -> str | None:
    resolved = {
        key: Decimal(spec["contribution"])
        for key, spec in score.contributions.items()
        if spec.get("source") == "resolved"
    }
    if not resolved:
        return None
    return max(resolved, key=resolved.get)


async def preview_flat_weights(
    project_id: uuid.UUID,
    *,
    weights: dict,
    min_weight_coverage: Decimal,
    session_factory: async_sessionmaker | None = None,
) -> PreviewResult:
    """Raises `ConfigError` (invalid candidate weights, reusing the exact
    validator `create_draft()`/`publish()` already enforce) or `RankingError`
    ("PROJECT_NOT_FOUND") — never silently returns a hollow preview."""
    validate_weights(weights)
    if not 0 < float(min_weight_coverage) <= 1:
        raise ConfigError("COVERAGE_RANGE", "min_weight_coverage phải trong (0, 1]")

    factory = session_factory or get_session_factory()
    async with factory() as session:
        if await session.scalar(sa.select(projects.c.id).where(projects.c.id == project_id)) is None:
            await session.rollback()
            raise RankingError("PROJECT_NOT_FOUND", f"Dự án {project_id} không tồn tại")

        unit_rows = await _project_units(session, project_id)
        live_units_by_area: dict[uuid.UUID, int] = {}
        for row in unit_rows:
            live_units_by_area[row["area_id"]] = live_units_by_area.get(row["area_id"], 0) + 1
        area_features = await _area_features(session, project_id, live_units_by_area)
        active_deal_units = await _has_active_deal_by_unit(session, project_id)
        funnel_counts = await _funnel_deal_counts(session, project_id)
        feature_inputs = _build_feature_inputs(unit_rows, area_features, active_deal_units, funnel_counts)

        current_rows = (
            await session.execute(
                sa.select(ranking_scores.c.unit_id, ranking_scores.c.score).where(
                    ranking_scores.c.project_id == project_id
                )
            )
        ).mappings().all()
        # `units.area_id`/`.created_at` for BOTH today's feature_inputs and
        # whatever units the last real run persisted — the two sets can
        # differ (a unit sold/added since) and the legacy re-rank below
        # needs area_id + the same tie-break value the original run used.
        needed_unit_ids = {u.unit_id for u in feature_inputs} | {row["unit_id"] for row in current_rows}
        display_rows = (
            await session.execute(
                sa.select(units.c.id, units.c.unit_code, units.c.external_unit_id, units.c.area_id, units.c.created_at)
                .where(units.c.id.in_(needed_unit_ids))
            )
        ).mappings().all()
        display_by_unit = {str(row["id"]): row for row in display_rows}
        current_config_version = await session.scalar(
            sa.select(ranking_configs.c.version).where(ranking_configs.c.status == "published")
        )
        await session.rollback()

    current_by_unit = {str(row["unit_id"]): row for row in current_rows}

    # Ranking v3 (`ranking_v3_composite_enabled`) may make the PERSISTED
    # `ranking_scores.rank_in_project` reflect the hierarchical composite,
    # not the legacy score alone. This preview is deliberately legacy-only
    # (see module docstring), so its "current" baseline must be re-derived
    # locally from the persisted legacy `score` values, never read off the
    # persisted rank column — otherwise `rank_delta` would be confounded by
    # hierarchical-grain influence the candidate legacy weights never touch.
    def _current_legacy_score(unit_id: str, row: dict) -> UnitScore:
        display = display_by_unit.get(unit_id)
        return UnitScore(
            unit_id=unit_id,
            area_id=str(display["area_id"]) if display else "",
            score=Decimal(str(row["score"])) if row["score"] is not None else None,
            coverage=Decimal("0"),
            contributions={},
            skipped=row["score"] is None,
            skip_reason=None,
            tie_break_created_at=display["created_at"] if display else None,
        )

    current_legacy_rank_by_unit = {
        s.unit_id: s.rank_in_project
        for s in rank_scores([_current_legacy_score(unit_id, row) for unit_id, row in current_by_unit.items()])
    }

    candidate = _parse_weights(weights)
    scored = rank_scores([score_unit(u, candidate, min_weight_coverage) for u in feature_inputs])

    results: list[UnitPreviewDelta] = []
    for score in scored:
        unit_key = str(score.unit_id)
        display = display_by_unit.get(unit_key)
        current = current_by_unit.get(unit_key)
        current_score = Decimal(str(current["score"])) if current and current["score"] is not None else None
        current_rank = current_legacy_rank_by_unit.get(unit_key)
        preview_score = score.score
        preview_rank = score.rank_in_project
        results.append(
            UnitPreviewDelta(
                unit_id=unit_key,
                unit_code=display["unit_code"] if display else "",
                external_unit_id=display["external_unit_id"] if display else "",
                area_id=str(score.area_id),
                current_score=str(current_score) if current_score is not None else None,
                current_rank=current_rank,
                preview_score=str(preview_score) if preview_score is not None else None,
                preview_rank=preview_rank,
                score_delta=str(preview_score - current_score) if preview_score is not None and current_score is not None else None,
                rank_delta=(current_rank - preview_rank) if current_rank is not None and preview_rank is not None else None,
                top_contributor=_top_contributor(score),
                skipped=score.skipped,
            )
        )

    with_delta = [r for r in results if r.score_delta is not None]
    gainers = sorted(with_delta, key=lambda r: Decimal(r.score_delta), reverse=True)[:5]
    losers = sorted(with_delta, key=lambda r: Decimal(r.score_delta))[:5]

    return PreviewResult(
        project_id=str(project_id),
        current_config_version=current_config_version,
        sample_size=len(feature_inputs),
        units_scored=sum(1 for s in scored if not s.skipped),
        units_skipped=sum(1 for s in scored if s.skipped),
        results=results,
        top_gainers=gainers,
        top_losers=losers,
        generated_at=datetime.now(UTC),
    )
