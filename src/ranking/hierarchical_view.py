"""PR-7: read-only view builder for the hierarchical (M/P/A/U) disclosure.

Reshapes ALREADY-PERSISTED `ranking_scores.hierarchical_score`/
`.hierarchical_contributions` (written by PR-1 through PR-6's post-run step,
`src/ranking/service.py::compute_hierarchical_scores_for_run`) for the API
layer. This module never computes a score, never selects a governance
candidate, never writes anything — every function here is either a pure
dict-reshaping function or a batched SELECT against rows PR-1..PR-6 already
wrote. It must not be confused with `src/ranking/service.py`, which owns
scoring; this file owns DISCLOSURE of scoring's already-written output.

Field names below follow the ACTUAL stored `hierarchical_contributions`
shape (`grains`, `legal_gate`) — see `_build_hierarchical_contributions()`/
`_build_legal_gated_contributions()` in `src/ranking/service.py` — not the
earlier speculative `grain_statuses`/`legal_result` naming sketched in
`docs/ranking/hierarchical_scoring_implementation_plan.md`'s original PR-7
section; code wins over that older prose per this repo's own authority order.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.logging_config import get_logger
from src.models.tables import (
    ranking_evidence_document_features,
    ranking_evidence_documents,
    ranking_feature_justifications,
)

log = get_logger("src.ranking.hierarchical_view")

HIERARCHICAL_PARENT_GRAINS = ("market", "project", "area")
ALL_GRAIN_KEYS = (*HIERARCHICAL_PARENT_GRAINS, "unit")

LEGAL_GATED_DISCLOSURE = (
    "Not ranked on the hierarchical surface because the project is under a HIGH_RISK legal gate."
)
UNIT_ONLY_DISCLOSURE = "Unit-only hierarchical score — Market, Project, and Area context unavailable."
FULL_HIERARCHICAL_DISCLOSURE = "Full hierarchical score — decision support only, not a sales guarantee."


async def _batch_justification_support(
    session: AsyncSession, justification_ids: set[str]
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Two queries total for the ENTIRE page — freshness (`effective_at`/
    `expires_at`, keyed by justification id) and linked evidence documents —
    never one query per unit/grain. Reads the immutable justification row a
    run's own snapshot already resolved (edit-locked after submission,
    `governance.py::_JUSTIFICATION_EDITABLE_STATUSES`); never re-selects a
    'current best' candidate, so a historical run's disclosed freshness can
    never drift from what that run actually used."""
    if not justification_ids:
        return {}, {}
    ids = [uuid.UUID(j) for j in justification_ids]

    freshness_rows = (
        await session.execute(
            sa.select(
                ranking_feature_justifications.c.id,
                ranking_feature_justifications.c.effective_at,
                ranking_feature_justifications.c.expires_at,
            ).where(ranking_feature_justifications.c.id.in_(ids))
        )
    ).mappings().all()
    freshness = {
        str(r["id"]): {"effective_at": r["effective_at"], "expires_at": r["expires_at"]} for r in freshness_rows
    }

    evidence_rows = (
        await session.execute(
            sa.select(
                ranking_evidence_document_features.c.feature_justification_id,
                ranking_evidence_documents.c.id,
                ranking_evidence_documents.c.original_filename,
                ranking_evidence_documents.c.mime_type,
                ranking_evidence_documents.c.object_storage_key,
            )
            .select_from(
                ranking_evidence_document_features.join(
                    ranking_evidence_documents,
                    ranking_evidence_documents.c.id == ranking_evidence_document_features.c.document_id,
                )
            )
            .where(ranking_evidence_document_features.c.feature_justification_id.in_(ids))
        )
    ).mappings().all()
    evidence: dict[str, list[dict]] = {}
    for row in evidence_rows:
        evidence.setdefault(str(row["feature_justification_id"]), []).append(
            {
                "document_id": str(row["id"]),
                "original_filename": row["original_filename"],
                "mime_type": row["mime_type"],
                "object_storage_key": row["object_storage_key"],
            }
        )
    return freshness, evidence


def _evidence_refs_for(justification_ids: list[str], evidence: dict[str, list[dict]]) -> list[dict]:
    """No evidence linked (or the justification id itself is unresolved) ->
    one explicit `unavailable` ref, never a silently dropped/missing citation."""
    refs: list[dict] = []
    for jid in justification_ids:
        docs = evidence.get(jid)
        if not docs:
            refs.append({"status": "unavailable"})
            continue
        refs.extend({"status": "available", **doc} for doc in docs)
    return refs


def _freshness_for(justification_ids: list[str], freshness: dict[str, dict]) -> dict | None:
    if not justification_ids:
        return None
    known = [freshness[j] for j in justification_ids if j in freshness]
    if not known:
        return {"effective_at": None, "expires_at": None, "status": "unavailable"}
    effective_candidates = [f["effective_at"] for f in known if f["effective_at"] is not None]
    expiry_candidates = [f["expires_at"] for f in known if f["expires_at"] is not None]
    return {
        "effective_at": min(effective_candidates) if effective_candidates else None,
        # Soonest expiry is the binding constraint for "when does this grain
        # next need re-verification" when more than one justification feeds it.
        "expires_at": min(expiry_candidates) if expiry_candidates else None,
        "status": "fresh",
    }


def _legal_gate_out(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {
        "status": raw.get("status"),
        "gated": bool(raw.get("gated", False)),
        "reason": raw.get("reason"),
        "note": raw.get("note"),
    }


def _disclosure_for(score_mode: str | None, excluded_grains: dict, legal_gate: dict | None) -> str | None:
    if legal_gate and legal_gate.get("gated"):
        return LEGAL_GATED_DISCLOSURE
    if score_mode == "unit_only":
        return UNIT_ONLY_DISCLOSURE
    if score_mode == "partial_hierarchical":
        named = [
            f"{grain} ({(info or {}).get('reason')})"
            for grain, info in sorted((excluded_grains or {}).items())
        ]
        return "Partial hierarchical score — excluded: " + ", ".join(named) + "." if named else None
    if score_mode == "full_hierarchical":
        return FULL_HIERARCHICAL_DISCLOSURE
    return None


def _justification_ids_in(contributions: dict) -> set[str]:
    ids: set[str] = set()
    grains = contributions.get("grains")
    if isinstance(grains, dict):
        for grain_key in ALL_GRAIN_KEYS:
            meta = grains.get(grain_key)
            if isinstance(meta, dict):
                for jid in meta.get("feature_justification_ids") or []:
                    ids.add(str(jid))
    legal = contributions.get("legal_gate")
    if isinstance(legal, dict) and legal.get("source_justification_id"):
        ids.add(str(legal["source_justification_id"]))
    return ids


def _build_one(
    score: Decimal | None,
    contributions: dict,
    computed_at: datetime | None,
    freshness: dict[str, dict],
    evidence: dict[str, list[dict]],
) -> dict:
    score_mode = contributions.get("score_mode")
    excluded_grains = contributions.get("excluded_grains") or {}
    legal_gate = _legal_gate_out(contributions.get("legal_gate"))
    grains_raw = contributions.get("grains") or {}

    grains_out: dict[str, dict] = {}
    for grain_key in ALL_GRAIN_KEYS:
        meta = grains_raw.get(grain_key)
        if not isinstance(meta, dict):
            continue
        jids = [str(j) for j in (meta.get("feature_justification_ids") or [])]
        is_unit = grain_key == "unit"
        grains_out[grain_key] = {
            "eligible": bool(meta.get("eligible", False)),
            "score": meta.get("score"),
            "coverage": meta.get("coverage"),
            "exclusion_reason": meta.get("exclusion_reason"),
            "freshness": None if is_unit else _freshness_for(jids, freshness),
            "evidence_refs": [] if is_unit else _evidence_refs_for(jids, evidence),
            # PR-5's CRM/expert split — only ever present in the persisted row for
            # the 'area' grain (see _build_hierarchical_contributions' own
            # docstring); None here for every other grain, never a fabricated [].
            "crm_feature_keys": meta.get("crm_feature_keys"),
            "expert_feature_keys": meta.get("expert_feature_keys"),
        }

    return {
        "available": True,
        "reason": None,
        "score": str(score) if score is not None else None,
        "score_mode": score_mode,
        "top_level_weight_coverage": contributions.get("top_level_weight_coverage"),
        "configured_grain_weights": contributions.get("configured_grain_weights"),
        "effective_grain_weights": contributions.get("effective_grain_weights"),
        "eligible_grains": contributions.get("eligible_grains") or [],
        "excluded_grains": excluded_grains,
        "grains": grains_out,
        "legal_gate": legal_gate,
        "comparability_warning": contributions.get("comparability_warning"),
        "cutoff_at": contributions.get("cutoff_at"),
        # `hierarchical_contributions` carries no separately-tracked
        # hierarchical-only timestamp (see module docstring) — the row's own
        # `ranking_scores.computed_at` (legacy persistence time, same run)
        # is the one honest persisted timestamp available for this row.
        "computed_at": computed_at,
        "config_version_id": contributions.get("config_version_id"),
        "disclosure": _disclosure_for(score_mode, excluded_grains, legal_gate),
    }


async def build_hierarchical_units(session: AsyncSession, rows: list[dict]) -> dict[str, dict]:
    """`rows`: mappings with at least `unit_id`, `hierarchical_score`,
    `hierarchical_contributions`, `computed_at` (all read straight off
    `ranking_scores`). Returns `{unit_id_str: <dict matching
    HierarchicalUnitOut>}` for every row, including `available=False` rows.

    Exactly two SELECTs total for the whole page (`_batch_justification_support`
    above) — never one per unit, since Market/Project/Legal are project-wide
    constants and Area is an area-wide constant, so the same handful of
    justification ids repeat across every row.
    """
    justification_ids: set[str] = set()
    parsed: dict[str, tuple[Decimal | None, dict | None, datetime | None]] = {}
    for row in rows:
        unit_id = str(row["unit_id"])
        contributions = row.get("hierarchical_contributions")
        parsed[unit_id] = (row.get("hierarchical_score"), contributions, row.get("computed_at"))
        if isinstance(contributions, dict):
            justification_ids |= _justification_ids_in(contributions)

    freshness, evidence = await _batch_justification_support(session, justification_ids)

    out: dict[str, dict] = {}
    for unit_id, (score, contributions, computed_at) in parsed.items():
        if contributions is None:
            out[unit_id] = {"available": False, "reason": "NOT_COMPUTED"}
            continue
        if not isinstance(contributions, dict):
            log.warning("ranking.hierarchical_view.degraded", unit_id=unit_id, reason="NOT_A_DICT")
            out[unit_id] = {"available": False, "reason": "DEGRADED"}
            continue
        try:
            out[unit_id] = _build_one(score, contributions, computed_at, freshness, evidence)
        except Exception:  # noqa: BLE001 - a malformed persisted row must degrade the response, never 500 it
            log.warning("ranking.hierarchical_view.degraded", unit_id=unit_id, reason="BUILD_ERROR")
            out[unit_id] = {"available": False, "reason": "DEGRADED"}
    return out


def log_hierarchical_read_observability(units_out: dict[str, dict], *, project_id: str, latency_ms: float) -> None:
    """One structured log event per read request (never per unit — that
    would be log spam on a 200-row page), aggregating exactly the rollout
    signals PR-7 asks for. Request correlation (`request_id`) is already
    merged in automatically by `structlog.contextvars` + `src/middleware.py`
    — nothing extra needed here for that.

    Deliberately excluded from every field below: evidence file contents,
    legal rationale/methodology text, raw reviewer identity, and any token
    claim — only counts, modes, and reason CODES are logged, never the
    underlying business content those codes summarize."""
    total = len(units_out)
    score_mode_counts: Counter[str] = Counter()
    unavailable_count = 0
    legal_gated_count = 0
    excluded_reason_counts: Counter[str] = Counter()
    comparability_warning_count = 0
    evidence_unavailable_count = 0
    evidence_available_count = 0
    coverage_values: list[str] = []

    for entry in units_out.values():
        if not entry.get("available"):
            unavailable_count += 1
            continue
        mode = entry.get("score_mode")
        if mode:
            score_mode_counts[mode] += 1
        if mode == "legal_gated":
            legal_gated_count += 1
        for info in (entry.get("excluded_grains") or {}).values():
            reason = (info or {}).get("reason")
            if reason:
                excluded_reason_counts[reason] += 1
        if entry.get("comparability_warning"):
            comparability_warning_count += 1
        coverage = entry.get("top_level_weight_coverage")
        if coverage is not None:
            coverage_values.append(coverage)
        for grain in (entry.get("grains") or {}).values():
            for ref in grain.get("evidence_refs") or []:
                if ref.get("status") == "available":
                    evidence_available_count += 1
                else:
                    evidence_unavailable_count += 1

    log.info(
        "ranking.hierarchical_read.completed",
        project_id=project_id,
        latency_ms=round(latency_ms, 2),
        total_units=total,
        score_mode_counts=dict(score_mode_counts),
        unavailable_count=unavailable_count,
        legal_gated_count=legal_gated_count,
        excluded_grain_reason_counts=dict(excluded_reason_counts),
        comparability_warning_count=comparability_warning_count,
        evidence_available_count=evidence_available_count,
        evidence_unavailable_count=evidence_unavailable_count,
        top_level_weight_coverage_values=coverage_values,
    )
