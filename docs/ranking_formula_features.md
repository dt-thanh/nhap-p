# Ranking / Absorption Score — Formula, Features, Weights

Traced from code on 2026-08-26. Every claim below is backed by a file:line reference; nothing here is inferred beyond what the cited code proves.

## 1. Where it's calculated

| Concern | File |
|---|---|
| Orchestration (DB read → engine call → DB write) | [src/ranking/service.py](../src/ranking/service.py) |
| Pure scoring formula | [src/ranking/engine.py](../src/ranking/engine.py) |
| Score → band (`high`/`medium`/`low`) presentation layer | [src/ranking/bands.py](../src/ranking/bands.py) |
| Weight derivation via AHP (pairwise comparison) | [src/ranking/ahp.py](../src/ranking/ahp.py) |
| `ranking_configs` schema | [src/models/tables.py:544](../src/models/tables.py#L544) |
| Config draft/publish (only writer of `ranking_configs`) | [src/services/ranking_config.py](../src/services/ranking_config.py) |
| Weight-change governance workflow (writer of proposal tables) | [src/services/governance.py](../src/services/governance.py) |
| Currently published weight set (data migration) | [alembic/versions/0022_ranking_config_v2.py](../alembic/versions/0022_ranking_config_v2.py) |

Core scoring function, `src/ranking/engine.py:69`:

```python
def score_unit(
    unit: UnitFeatureInput,
    weights: list[FeatureWeight],
    min_weight_coverage: Decimal,
) -> UnitScore:
    numerator = Decimal("0")
    denominator = Decimal("0")
    for w in weights:
        raw_value = unit.values.get(w.key)
        is_missing = raw_value is None or (confidence is not None and confidence < w.min_confidence)
        if is_missing:
            if w.missing_value_policy == "skip":
                continue
            resolved_value = Decimal("0") if w.missing_value_policy == "zero" else Decimal("0.5")
        else:
            resolved_value = raw_value
        contribution = w.weight * oriented(resolved_value, w.direction)
        numerator += contribution
        denominator += w.weight
    coverage = denominator
    if coverage < min_weight_coverage:
        return UnitScore(score=None, skipped=True, skip_reason="coverage_below_threshold", ...)
    score = (numerator / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return UnitScore(score=score, skipped=False, ...)
```

Direction, `src/ranking/engine.py:61`:

```python
def oriented(value: Decimal, direction: Direction) -> Decimal:
    if direction == "positive":
        return value
    if direction == "negative":
        return Decimal("1") - value
```

## 2. Formula

For a unit with resolved feature values $v_i$, per-feature weights $w_i$ and directions $d_i \in \{\text{positive}, \text{negative}\}$:

$$
\text{oriented}(v_i, d_i) = \begin{cases} v_i & d_i = \text{positive} \\ 1 - v_i & d_i = \text{negative} \end{cases}
$$

$$
\text{numerator} = \sum_{i \in \text{computed}} w_i \cdot \text{oriented}(v_i, d_i)
\qquad
\text{denominator} = \sum_{i \in \text{computed}} w_i
$$

$$
\text{coverage} = \text{denominator}
$$

$$
\text{score} =
\begin{cases}
\varnothing \text{ (unit skipped, no rank)} & \text{coverage} < \text{min\_weight\_coverage} \\[4pt]
\text{round}\!\left(\dfrac{\text{numerator}}{\text{denominator}},\ 4\right) & \text{otherwise}
\end{cases}
$$

A feature is excluded from both sums ("not computed") only when its `missing_value_policy` is `skip` and the value is missing. For `zero`/`neutral` policies, a missing value is *resolved* to `0` or `0.5` respectively and **does** count toward numerator and denominator — it does not reduce coverage. `min_weight_coverage` currently published: `0.5` (`src/models/tables.py:551` default; `0.5` in the v2 migration row, `alembic/versions/0022_ranking_config_v2.py:136`).

Rounding: `ROUND_HALF_UP` to 4 decimal places (`src/ranking/engine.py:123`), i.e. `Decimal.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)`.

Ranking (`src/ranking/engine.py:136`, `rank_scores`): sort key is `(-score, tie_break_created_at, unit_id)` — score descending, then earliest `created_at` wins ties, then `unit_id` as a final deterministic tiebreak. `rank_in_project` is assigned across all ranked (non-skipped) units in a project; `rank_in_area` is assigned within each `area_id` bucket independently. Skipped units get `rank_in_area = rank_in_project = None` and are excluded from both orderings.

Presentation-only band mapping (`src/ranking/bands.py:40`, not part of the score itself):

$$
\text{band}(s) = \begin{cases} \varnothing & s = \varnothing \\ \text{high} & s \ge 0.66 \\ \text{medium} & 0.33 \le s < 0.66 \\ \text{low} & s < 0.33 \end{cases}
$$

Thresholds are absolute, not percentile-based — deliberately, per the module docstring (`src/ranking/bands.py:13`), so a unit's band cannot shift merely because other units in its area changed.

## 3. Features

Built in `src/ranking/service.py:217` (`_build_feature_inputs`), sourced from `units`/`deals`/`areas` only — explicitly **not** from `sales_records`/`inventory_snapshots`/`absorption_daily` (`src/ranking/service.py:6-8`, called out as legacy dashboard tables out of scope for ranking).

| Feature key | Type | Source | Formula / derivation | Direction | Missing policy (v2) |
|---|---|---|---|---|---|
| `unit_available` | boolean (0/1) | `units.status` | `1` if `status == "available"` else `0` | positive | `zero` |
| `unit_demand_norm` | numeric [0,1] | `deals` (funnel statuses `lead`/`qualified`/`interested`/`viewing`) on the unit itself | `min(count_of_funnel_deals_on_unit / 3, 1)` — saturates at 3 concurrent funnel deals (`DEMAND_SATURATION`, `src/ranking/service.py:76`) | positive | `zero` |
| `area_velocity_norm` | numeric [0,1] | `deals` sold in the unit's `area` in the trailing 30 days, over live (non-deleted, mirrored) units in that area | `min((sold_in_last_30d / live_units_in_area) / 0.20, 1)` — `VELOCITY_SATURATION = 0.20` (`src/ranking/service.py:71`); area absent from the map entirely (no live deals yet) is treated as **missing**, not zero | positive | `neutral` |
| `area_conversion_norm` | numeric [0,1] | `deals` in the unit's `area` | `sold_deals_in_area / max(alive_deals_in_area, 1)` | positive | `neutral` |
| `has_active_deal` | boolean (0/1) | `deals.status in ('reserved','sold')` on the unit | still computed by the service (`src/ranking/service.py:237`) for rollback compatibility, but **excluded from the published v2 weight set** — see §4 | — | — |

Notes proven by code, not inferred:
- `area_velocity_norm`'s denominator is the count of *live, already-synced* units per area, not `areas.total_units` (the planned inventory figure) — the service module's docstring documents this as a dimensional-error fix measured against production data (`src/ranking/service.py:14-23`).
- A missing area feature (area has zero live deals) is deliberately left absent from the returned dict rather than defaulted to `0`, so the `neutral` policy in the engine actually triggers (`src/ranking/service.py:25-30`, `146-153`).
- `unit_type` scope was considered and rejected as a feature dimension: 0/58 areas in the measured dataset have more than one `unit_type`, so it would be redundant with `area` scope (`src/ranking/service.py:41-43`).

## 4. Weights

Read from `ranking_configs.weights` (JSONB), the single row where `status = 'published'` (`src/ranking/service.py:107-123`, `_active_config`). Schema: `src/models/tables.py:544`.

**Currently published (v2, `ranking_configs.version = 2`)** — from the data migration `alembic/versions/0022_ranking_config_v2.py:85-90`:

| Feature | Weight | Direction | Missing policy | Editable via governance? |
|---|---|---|---|---|
| `unit_available` | 0.35 | positive | zero | yes |
| `unit_demand_norm` | 0.25 | positive | zero | yes |
| `area_velocity_norm` | 0.20 | positive | neutral | yes |
| `area_conversion_norm` | 0.20 | positive | neutral | yes |
| **Sum** | **1.00** | | | |

`min_weight_coverage = 0.5` for this config (a unit needs at least half the weight budget resolved by non-skipped features to receive a score at all).

**Global vs per-project.** `ranking_configs` carries no `project_id` column (`src/models/tables.py:544-559`) — one published config applies to every project. There is no per-project override in the schema or in `_active_config`.

**Prior version (v1, archived).** Per the v2 migration's own docstring (`alembic/versions/0022_ranking_config_v2.py:16-19`), v1 (from `0014`) used `unit_available: 0.50`, `has_active_deal: 0.20`, plus `area_velocity_norm`/`area_conversion_norm` at lower weights (`area_conversion_norm` raised 0.10 → 0.20 in v2). v1 was archived because `unit_available` and `has_active_deal` were measured to be perfectly anti-correlated (a filter condition masquerading as a priority signal, contributing a constant +0.70 to every score and defeating the `bands.py` thresholds) — measured on 1,991 real ranked units (`alembic/versions/0022_ranking_config_v2.py:12-38`).

**Where weight *magnitudes* can come from.** `src/ranking/ahp.py` implements an optional second path: instead of an engineer hand-picking weights, an expert supplies pairwise comparisons (Saaty 1–9 scale) and weights are derived via Row Geometric Mean Method, with a Consistency Ratio computed to flag self-contradictory judgments (`src/ranking/ahp.py:35-86`). AHP determines magnitude only — `direction` and `missing_value_policy` are supplied separately and never inferred (`src/ranking/ahp.py:346-358`, `as_config_weights`). This is orthogonal to whether a weight set is "v1" or "v2" — the module's own docstring stresses these are two different version axes (`src/ranking/ahp.py:8-16`).

**User-editable via governance proposals — yes**, gated by a hard approval workflow (§5).

## 5. Data flow: user weight edit → published ranking

```
┌─────────────────────────────────────────────────────────────────────────┐
│ FRONTEND                                                                  │
│                                                                            │
│ ConsultantEvidencePage.jsx                                                │
│   FeatureWeightSlider (one per feature key, range 0–1, step 0.01)         │
│     onChange → setWeights({...current, [key]: {...current[key], weight}})│
│                                                                            │
│   "Submit" handler (ConsultantEvidencePage.jsx:97):                       │
│     createRankingConfigDraft({ weights, min_weight_coverage, note,        │
│                                 created_by, copied_from_version })         │
│       → POST /ranking/configs         (endpoints.js:69)                   │
│                                                                            │
│     createGovernanceProposal({ base_config_id, proposed_config_id, ... }) │
│       → POST /governance/proposals    (endpoints.js:515)                  │
└───────────────────────────────┬───────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ BACKEND                                                                   │
│                                                                            │
│ src/services/ranking_config.py::create_draft                             │
│   → INSERT ranking_configs (status='draft')   [sole writer of this table]│
│                                                                            │
│ src/services/governance.py::create_proposal                              │
│   → INSERT ranking_weight_proposals (status='draft')                     │
│                                                                            │
│ ranking_weight_proposals.status state machine (governance.py:18-31,      │
│ CHECK constraint enforced in 0034):                                       │
│                                                                            │
│   draft --submit_proposal()--> submitted                                 │
│           (POST /governance/proposals/{id}/submit)                       │
│                                                                            │
│   submitted --submit_review(decision='approved')--> approved             │
│           (POST /governance/proposals/{id}/reviews)                      │
│           [human-in-the-loop gate — AGENTS.md hard requirement applies   │
│            to this review just as to agent recommendations]              │
│                                                                            │
│   approved --publish_proposal()--> published                             │
│           (POST /governance/proposals/{id}/publish, api/governance.py)   │
│           → mark_published() in governance.py SELECTs to confirm the     │
│             linked ranking_configs row is already published; it never    │
│             UPDATEs ranking_configs itself (governance.py:10-16)         │
│                                                                            │
│ The actual publish of ranking_configs still only ever happens via:       │
│   src/services/ranking_config.py::publish(version=..., published_by=...) │
│   → archives the previously published row, sets this version 'published' │
│     (partial-unique-index enforced: exactly one published row at a time) │
└───────────────────────────────┬───────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ RANKING                                                                   │
│                                                                            │
│ src/ranking/service.py::run_ranking                                      │
│   _active_config() reads the ONE published ranking_configs row           │
│   → weights flow into engine.score_unit() for every unit in the project  │
│   → ranking_scores rewritten (delete-then-insert per project)            │
└─────────────────────────────────────────────────────────────────────────┘
```

Key invariant proven by code: **no code path lets a slider edit reach `ranking_scores` without passing through the `draft → submitted → approved → published` state machine** — `ranking_config.py::publish` is the only writer of `ranking_configs.status='published'`, and `governance.py` explicitly refuses to duplicate that write path (its own docstring: "opening a second write path just to save one API call trades a real invariant for a small convenience," `src/services/governance.py:10-16`).
