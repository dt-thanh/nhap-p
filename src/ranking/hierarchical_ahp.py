"""Hierarchical AHP (mandatory-scope item 7): pairwise-derived priorities +
CI/CR for the grain-weight matrix (market/project/area/unit) AND each
within-grain criterion matrix (market/project/area — `unit` is never
assertable here, D37: unit stays 100% CRM-computed, matching
`src/services/governance.py::_check_grain_scope_compatibility`).

No new math. This calls `src/ranking/ahp.py`'s existing flat `compute()`
once per matrix — a hierarchical composition is just several independent
flat pairwise comparisons, one per level, assembled into the
`hierarchical_weights` shape `src/services/ranking_config.py::validate_hierarchical_weights()`
already expects. `ahp.py` itself is untouched.

╔══════════════════════════════════════════════════════════════════════════════╗
║  Hàm THUẦN — không I/O, không mạng, không DB, giống ahp.py/engine.py.        ║
╚══════════════════════════════════════════════════════════════════════════════╝

Cố ý KHÔNG import `src.services.ranking_config` (kéo theo `src/db.py`) — kiểm
tra khoá đặc trưng nằm ở tầng API, giống discipline `ahp.py` đã có.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ranking.ahp import AHPError, AHPResult, Judgment, compute, round_weights

GRAIN_WEIGHT_KEYS = ("market", "project", "area", "unit")
WITHIN_GRAIN_LEVELS = ("market", "project", "area")


class HierarchicalAHPError(ValueError):
    """Cùng hình dạng với `AHPError`/`ConfigError`: có `code` để API ánh xạ thẳng ra lỗi."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LevelResult:
    level: str
    result: AHPResult


@dataclass(frozen=True)
class HierarchicalAHPResult:
    levels: dict[str, LevelResult]
    failed_levels: list[str]

    @property
    def all_consistent(self) -> bool:
        return not self.failed_levels


def _compute_level(level: str, criteria: list[str], judgments: list[Judgment]) -> LevelResult:
    try:
        result = compute(criteria, judgments)
    except AHPError as exc:
        raise HierarchicalAHPError(exc.code, f"[{level}] {exc.message}") from exc
    return LevelResult(level=level, result=result)


def compute_hierarchical_ahp(
    *,
    grain_judgments: list[Judgment],
    market_judgments: list[Judgment],
    project_judgments: list[Judgment],
    area_judgments: list[Judgment],
) -> HierarchicalAHPResult:
    """Computes one level for the grain-weight matrix (fixed 4-criteria
    `GRAIN_WEIGHT_KEYS`) and one level for EACH of market/project/area.

    All four levels are REQUIRED, not optional: `validate_hierarchical_weights()`
    itself requires all three grain blocks present and non-empty
    (`HIERARCHICAL_WEIGHTS_KEY_MISSING`/`HIERARCHICAL_GRAIN_EMPTY`) — a
    grain having no PUBLISHED governance value at scoring time is a
    separate, runtime concept (handled by exclusion/renormalization in
    `src/ranking/service.py`), not something the static config schema lets
    a proposal skip.

    Every level is computed independently and ALL results are returned
    together — `failed_levels` lists every level whose CR exceeds its own
    `threshold_for(n)` (mandatory-scope item 6: display all failed levels,
    not just the first)."""
    levels: dict[str, LevelResult] = {
        "grain_weights": _compute_level("grain_weights", list(GRAIN_WEIGHT_KEYS), grain_judgments)
    }
    for level, judgments in (
        ("market", market_judgments),
        ("project", project_judgments),
        ("area", area_judgments),
    ):
        criteria = sorted({j.a for j in judgments} | {j.b for j in judgments})
        levels[level] = _compute_level(level, criteria, judgments)

    failed_levels = [name for name, lvl in levels.items() if not lvl.result.consistent]
    return HierarchicalAHPResult(levels=levels, failed_levels=failed_levels)


def grain_weights_block(result: AHPResult, *, missing_value_policies: dict[str, str]) -> dict:
    """Assembles the `grain_weights` block `validate_hierarchical_weights()`
    expects: `{grain: {weight, missing_value_policy}}` for all four grains.
    No `direction` — a grain-composition weight is not a scored feature."""
    missing = sorted(set(result.weights) - set(missing_value_policies))
    if missing:
        raise HierarchicalAHPError(
            "SPEC_MISSING", f"Thiếu missing_value_policy cho grain {missing} — AHP không suy ra được trường này"
        )
    zero_policy = sorted(key for key in result.weights if missing_value_policies[key] == "zero")
    if zero_policy:
        # Mirrors HIERARCHICAL_GRAIN_ZERO_POLICY_FORBIDDEN (D37) — checked
        # again, authoritatively, by validate_hierarchical_weights() itself;
        # this early check gives a clearer error at assembly time.
        raise HierarchicalAHPError(
            "HIERARCHICAL_GRAIN_ZERO_POLICY_FORBIDDEN",
            f"grain_weights không được dùng missing_value_policy='zero': {zero_policy}",
        )
    rounded = round_weights(result.weights)
    return {
        key: {"weight": float(rounded[key]), "missing_value_policy": missing_value_policies[key]}
        for key in result.weights
    }


def grain_feature_block(result: AHPResult, *, specs: dict[str, dict]) -> dict:
    """Assembles a within-grain feature block: `{feature_key: {weight,
    direction, missing_value_policy}}` — same shape `as_config_weights()`
    builds for the flat path, minus `min_confidence` (not part of the
    hierarchical grain-feature schema)."""
    missing = sorted(set(result.weights) - set(specs))
    if missing:
        raise HierarchicalAHPError(
            "SPEC_MISSING", f"Thiếu direction/missing_value_policy cho {missing} — AHP không suy ra được các trường này"
        )
    rounded = round_weights(result.weights)
    return {
        key: {
            "weight": float(rounded[key]),
            "direction": specs[key]["direction"],
            "missing_value_policy": specs[key]["missing_value_policy"],
            **({"rationale": specs[key]["rationale"]} if "rationale" in specs[key] else {}),
        }
        for key in result.weights
    }


def assemble_hierarchical_weights_block(
    hier: HierarchicalAHPResult,
    *,
    grain_missing_value_policies: dict[str, str],
    market_specs: dict[str, dict],
    project_specs: dict[str, dict],
    area_specs: dict[str, dict],
) -> dict | None:
    """Shared assembly step reused by both `src/api/ahp.py` (admin pairwise
    endpoint) and `src/services/governance.py` (Advisor AHP proposal draft) —
    kept here, not in either caller, because this module is deliberately the
    one place that assembles `hierarchical_ahp`'s own per-level results into
    the `hierarchical_weights` shape; it does NOT call
    `validate_hierarchical_weights()` (this module's own docstring: no
    `src.services.ranking_config`/DB import here) — every caller must run
    that validation itself before treating the result as usable. Returns
    `None`, not a partial block, when any level is inconsistent."""
    if not hier.all_consistent:
        return None
    return {
        "grain_weights": grain_weights_block(
            hier.levels["grain_weights"].result, missing_value_policies=grain_missing_value_policies
        ),
        "market": grain_feature_block(hier.levels["market"].result, specs=market_specs),
        "project": grain_feature_block(hier.levels["project"].result, specs=project_specs),
        "area": grain_feature_block(hier.levels["area"].result, specs=area_specs),
    }
