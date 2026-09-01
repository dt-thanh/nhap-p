"""Publish a `ranking_configs.hierarchical_weights` for the currently published
legacy config, then trigger a recompute for every project.

    python -m scripts.enable_hierarchical_ranking --dry-run
    python -m scripts.enable_hierarchical_ranking --confirm

Context (see `docs/ranking/ranking_consultant.md` D41, and
`pipeline_status.md` 2026-08-27 "Hierarchical Ranking PR-1 through PR-7"):
`ranking_configs.hierarchical_weights` is a nullable, additive JSONB column
that has existed since migration `0037` but has **never been written by any
code path in this repository** — `create_draft()`/`publish()` (and the
`POST /ranking/configs` API they back) accept it as an optional parameter, but
no config has ever actually supplied one. With it NULL,
`compute_hierarchical_scores_for_run()` is a documented, structured no-op
(`HIERARCHICAL_WEIGHTS_ABSENT`) even when `hierarchical_ranking_enabled=True` —
this is the real reason `hierarchical_score` is NULL on every persisted row
today, independent of either feature flag.

This script closes exactly that gap using 100% existing, unmodified
infrastructure (`create_draft`, `publish`, `trigger_ranking_all_projects` —
the same three calls `POST /ranking/configs` + `POST /ranking/configs/{v}/publish`
already make): it copies the currently published config's `weights` /
`min_weight_coverage` verbatim (byte-for-byte — this script never touches
legacy unit-ranking configuration) into a new draft, attaches a
`hierarchical_weights` block, publishes it (which archives the prior version —
`uq_ranking_configs_published` allows exactly one published row, so this never
creates a duplicate active config), and enqueues a recompute for every
project via the exact same call the publish API endpoint makes.

The `hierarchical_weights` values below are not invented by this script: they
are `docs/ranking/ranking_consultant.md`'s own D41-approved "Proposed shape"
example (search that file for "Proposed shape, corrected location"), copied
verbatim, with one deliberate change — the illustrative Area 0.60/0.40
velocity/conversion split is replaced with the *actual* published legacy v2
ratio (0.20/0.20, i.e. 1:1) so a real, already-approved number is used instead
of an illustrative one wherever a real one exists. Market
(`market_interest_rate`/`market_demand`) and Project (`expert_location_score`/
`expert_infrastructure_score`/`expert_financing_score`) have no published
governance assertion for any project as of this writing (grep-verified against
`ranking_feature_values`), so those two grains are structurally valid but will
correctly resolve as `excluded` (documented `exclusion_reason`, never a
fabricated score) until a real Market/Project value assertion is authored,
CEO-approved, and published through the existing governance flow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from src.services.ranking_config import ConfigError, HierarchicalConfigError, create_draft, list_configs, publish
from src.services.ranking_trigger import trigger_ranking_all_projects

# Verbatim from docs/ranking/ranking_consultant.md, D41 "Proposed shape,
# corrected location" — except the Area split, which uses the real published
# v2 ratio (0.20/0.20) in place of that section's illustrative 0.60/0.40.
HIERARCHICAL_WEIGHTS = {
    "grain_weights": {
        "market": {"weight": 0.10, "missing_value_policy": "skip"},
        "project": {"weight": 0.25, "missing_value_policy": "skip"},
        "area": {"weight": 0.25, "missing_value_policy": "skip"},
        "unit": {"weight": 0.40, "missing_value_policy": "skip"},
    },
    "market": {
        "market_interest_rate": {"weight": 0.50, "direction": "negative", "missing_value_policy": "neutral"},
        "market_demand": {"weight": 0.50, "direction": "positive", "missing_value_policy": "neutral"},
    },
    "project": {
        "expert_location_score": {"weight": 0.40, "direction": "positive", "missing_value_policy": "neutral"},
        "expert_infrastructure_score": {"weight": 0.30, "direction": "positive", "missing_value_policy": "neutral"},
        "expert_financing_score": {"weight": 0.30, "direction": "positive", "missing_value_policy": "neutral"},
    },
    "area": {
        "area_velocity_norm": {"weight": 0.50, "direction": "positive", "missing_value_policy": "neutral"},
        "area_conversion_norm": {"weight": 0.50, "direction": "positive", "missing_value_policy": "neutral"},
    },
}

NOTE = (
    "Hierarchical (D41) enablement — copies published v2's `weights`/"
    "min_weight_coverage verbatim, adds hierarchical_weights per "
    "docs/ranking/ranking_consultant.md's D41 proposed shape (Area ratio "
    "corrected to the real published v2 0.20/0.20 split). Market/Project "
    "grains are structurally valid but have no published governance "
    "assertion yet — they resolve as excluded until one exists."
)


def _assert_development() -> None:
    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env and app_env != "development":
        raise SystemExit(f"Refusing to run outside development (APP_ENV={app_env!r})")


async def _current_published() -> dict:
    for row in await list_configs():
        if row["status"] == "published":
            return row
    raise SystemExit("No published ranking_configs row found — nothing to extend.")


async def _dry_run() -> None:
    current = await _current_published()
    print(f"Current published config: v{current['version']} (id={current['id']})")
    print(f"  weights (unchanged, copied verbatim): {json.dumps(current['weights'], sort_keys=True)}")
    print(f"  min_weight_coverage (unchanged): {current['min_weight_coverage']}")
    print("Would create a new draft with the same weights/min_weight_coverage plus:")
    print(f"  hierarchical_weights: {json.dumps(HIERARCHICAL_WEIGHTS, indent=2, sort_keys=True)}")
    print("Then publish it (archiving the current version) and enqueue a recompute for every project.")
    print("No database write performed (--dry-run).")


async def _confirm() -> None:
    current = await _current_published()
    try:
        draft = await create_draft(
            weights=current["weights"],
            min_weight_coverage=float(current["min_weight_coverage"]),
            note=NOTE,
            created_by="ops-hierarchical-enablement",
            copied_from_version=current["version"],
            hierarchical_weights=HIERARCHICAL_WEIGHTS,
        )
    except (ConfigError, HierarchicalConfigError) as exc:
        raise SystemExit(f"REFUSED: {exc.code}: {exc.message}") from exc
    print(f"Draft created: v{draft['version']} (id={draft['id']})")

    published = await publish(version=draft["version"], published_by="ops-hierarchical-enablement")
    print(f"Published: v{published['version']} (archived v{current['version']})")

    reranked = await trigger_ranking_all_projects(trigger="config_change")
    print(f"Recompute enqueued: {reranked}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    mode.add_argument("--confirm", action="store_true", help="the only write path")
    args = parser.parse_args(argv)

    _assert_development()
    asyncio.run(_dry_run() if args.dry_run else _confirm())
    return 0


if __name__ == "__main__":
    sys.exit(main())
