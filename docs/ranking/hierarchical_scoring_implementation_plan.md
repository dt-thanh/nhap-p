# Hierarchical Absorption Scoring — Implementation Plan

| Field | Value |
|---|---|
| Status | **Implementation-ready specification. Documentation only** — this document contains no code, no migration, no schema change. All SQL/Python/pytest blocks below are content to be written in a future task, not files that exist. |
| Source of design authority | `docs/ranking/ranking_consultant.md` §24.2–§24.12, decisions D22–D40, as of Phase C.3 (2026-08-27) |
| Authority order used while writing this pass | (1) current code/models/migrations/routes/tests, verified fresh; (2) `ranking_consultant.md` §24.4–§24.12; (3) this document's own prior content; (4) older prose. Where (1) contradicted (2), (1) won and (2) is flagged, not silently followed — see §0. |
| Scope | The **single, complete** implementation-ready spec for shipping `ranking_scores.hierarchical_score` end-to-end: schema, governance/writer extension, grain contracts, scoring integration, API contract, tests, and a PR-by-PR delivery plan. |
| Out of scope, explicitly | Writing code, migrations, schema, APIs, tests, or config. Editing `docs/ranking/ranking_consultant.md`. |

Every citation below is `file:line` against the current repository state, re-verified while writing this pass, or `[consultant.md:§N]` for design-authority citations.

---

## 0. Corrections required before specifying anything

Four findings from re-reading the actual code change what this plan can safely say. Two carry over from the prior pass (still true, unchanged); two are new — one of them is a real, previously-unverified defect in the design as `ranking_consultant.md` states it, load-bearing for §2 below.

### 0.1 Carried forward, unchanged (re-verified)

1. **`ranking_feature_lineage` has no grain-restricting CHECK to widen.** Keyed by `feature_value_id` (FK to `ranking_feature_values.id`); its only CHECKs are non-blank `source_relation <> ''` / `source_locator <> ''` and a nullable-or-non-blank `source_checksum` [`0033:284-289`]. Grain is inherited transitively through the FK. No change needed.
2. **`ranking_feature_definitions.ck_rfd_grain`** permits `('project', 'area', 'project_area', 'unit')` [`0033:60-62`]. Only `'market'` needs adding. `'developer'` stays out — no developer entity, no developer factor in scope, constraint says don't invent sources for a grain with zero features.

### 0.2 Carried forward, unchanged: the composite-FK coupling

**`ranking_feature_values.ck_rfv_scope_type_project` hardcodes `scope_type = 'project'`** [`0033:222`] independent of what grain the row's *definition* declares, and is coupled to `ranking_feature_snapshots.ck_rfs_scope_type_project` [`0033:155`] via the composite FK `fk_rfv_snapshot_project_scope` on `(snapshot_id, project_id, scope_type)` → `(id, project_id, scope_type)` [`0033:197-204`, target unique constraint `0033:150-154`]. **The two widenings must ship in one migration** (§2.5) — widening one without the other produces a values-table row that can never satisfy its own FK.

### 0.3 NEW — the design's stated D22/D33 storage location does not survive contact with `_active_config()`

`ranking_consultant.md §24.7`'s D22/D33 states the nested per-grain weights (`grain_weights`/`market`/`project`/`area`/`unit`) live **inside `ranking_configs.weights`, the same JSONB column the unit-grain path already reads.** Tracing that claim through the actual functions that touch this column, rather than trusting the prior design pass's assertion that `_active_config`/`validate_weights` would be "untouched," shows it is **not safe as literally stated**:

- `_active_config()` [`src/ranking/service.py:107-121`] does `for key, spec in row["weights"].items(): FeatureWeight(key=key, weight=spec["weight"], ...)` — it assumes **every top-level key of `weights` is a single feature spec** (`{"weight": ..., "direction": ..., "missing_value_policy": ...}`). If `weights` instead looks like `{"grain_weights": {...}, "market": {...}, "project": {...}, "area": {...}, "unit": {...}}` (the nested shape D22 specifies), the very first iteration does `spec["weight"]` on `spec = weights["grain_weights"]` (a dict of grain names, not a feature spec) → **`KeyError`**, raised inside `run_ranking()`'s call path, i.e. the legacy CRM-only ranking crashes the moment such a config is published.
- `validate_weights()` [`src/services/ranking_config.py:70-108`] independently rejects the same shape *before it can ever be published*: `if key not in KNOWN_FEATURES: raise ConfigError("UNKNOWN_FEATURE", ...)` [`:79-86`] — `"market"`, `"project"`, `"area"`, `"unit"`, `"grain_weights"` are none of them registered feature keys, so `create_draft()` [`:139,148`, calls `validate_weights(weights)`] and `publish()` [`:181,204`, re-validates `target["weights"]`] both reject the nested shape outright through the **existing, unmodified** config-authoring path.

**Both are the correct failure mode for the wrong reason to worry about it — the actual risk is upstream of both:** a config with the nested shape is either (a) rejected before publish (safe, but nested weights can never exist), or (b) if `validate_weights`/`create_draft`/`publish` were themselves modified to special-case the nested shape, that is a direct edit to the single-writer path for `ranking_configs` that the non-negotiable invariant ("preserve `run_ranking()`/all existing call sites byte-for-byte") forbids doing carelessly, and would still leave `_active_config()` broken for anyone who publishes a config through any other path. **Correction: the nested grain-weight structure does not live in `ranking_configs.weights`.** It lives in a **new, separate, additive nullable column** on the same table — `ranking_configs.hierarchical_weights JSONB` (§2.3) — so `_active_config()` never iterates it, `validate_weights()` never sees it, and `create_draft()`/`publish()` are **extended** (not modified in their existing behavior) to optionally accept and validate it via the **new** `_validate_nested_config()` (§2.3.1), never routed through `validate_weights()`. D22's *decision* (nested-per-grain shape, one published row) is preserved; only its *storage location* is corrected, against verified code, per this document's authority order. This correction is **not** made in `ranking_consultant.md` (out of scope for this pass) and is flagged here as an open cross-document inconsistency (§9).

### 0.4 NEW — `ranking_scores` is delete-then-insert per project, not update-in-place

`_persist_scores()` [`src/ranking/service.py:510-554`] does `DELETE FROM ranking_scores WHERE project_id = :p` then bulk `INSERT` **only the non-skipped rows** [`:530-533,552`: `to_insert = [s for s in ranked if not s.skipped]`]. Two consequences the prior plan's pseudocode did not account for:

1. **A unit whose `U` is `None` (coverage failure) has no `ranking_scores` row at all** — not a row with `score = NULL`. There is nothing for a hierarchical step to `UPDATE`. "`U` missing → `hierarchical_score = NULL`" is therefore not a value this plan writes; it is the **absence of a row**, exactly the same "unranked, not zero" idiom `engine.py:110-121` already uses for the legacy path. §5 states this precisely.
2. **A later `run_ranking()` call for the same project deletes and re-inserts every row for that project**, including the one a feature-flagged post-run hierarchical step is about to `UPDATE`. If a second run starts and completes between `run_ranking()` returning and the hierarchical step's `UPDATE` landing, that `UPDATE` targets rows that no longer belong to the run it computed values for — a silent no-op if scoped correctly, or a **silent cross-run write** if scoped only by `unit_id` (globally unique, `uq_ranking_scores_unit` [`0015:208`]) without also checking `ranking_run_id`. §5.4 makes this an explicit, tested guard, not an assumption.

### 0.5 Primary correction required by this task — stale all-or-nothing language, resolved

The following phrases existed in this document's prior revision and are **struck through, not deleted**, replaced by D37 (`ranking_consultant.md §24.4.1`, `§24.4.6`, APPROVED Phase C.3):

> ~~`elif M.score is None or P.score is None or A.score is None or U.score is None: hierarchical_score = None  # missing grain -> skip, never 0`~~
> ~~"after §1's migrations and §2's service ship, `hierarchical_score` will be `NULL` for **every unit, in every project, permanently**, until at least one expert-scoring writer exists for Market or Project grain."~~
> ~~(implicit throughout §2/§3/§6 of the prior revision) all four grains are required before any non-`NULL` score is possible.~~

**Replaced by, exactly:**

```text
U missing (CRM coverage failure, pre-existing, unrelated to D37)  -> no ranking_scores row at all (§0.4)
U present, HIGH_RISK legal gate                                   -> hierarchical_score = NULL, band = NULL (D27)
U present, zero eligible parents                                  -> hierarchical_score = U exactly, score_mode = unit_only
U present, 1 or 2 eligible parents (M and/or P and/or A)           -> score_mode = partial_hierarchical
U present, all of M, P, A eligible                                 -> score_mode = full_hierarchical
```

`unit_only` is reachable **immediately after this plan's PR-1 ships**, before any expert factor, any governance extension, or any CEO-approval mechanism exists — see §1, §8 PR-1.

---

## 1. Executive status

- **This plan now supports end-to-end implementation in phased PRs** (§8) — schema, governance, per-grain writers, snapshot/scoring integration, API, and observability, each independently shippable and independently reversible.
- **Legacy unit ranking remains byte-identical.** No PR in §8 alters `engine.score_unit()`, `engine.rank_scores()`, `run_ranking()`'s existing body, `_active_config()`, `validate_weights()`, or any existing column of `ranking_scores`/`ranking_configs`/`ranking_runs`. Every new write target is a new, nullable, additive column or a new, additive table.
- **A non-`NULL` `unit_only` hierarchical score becomes possible immediately after PR-1** (scoring integration ships) — it requires no expert data, no governance extension, and no CEO approval mechanism. It is `U` itself, disclosed as `score_mode = "unit_only"`.
- **A `partial_hierarchical` score becomes possible after the first CEO-approved eligible parent grain value is published and snapshotted** (earliest at PR-3, Project grain) — not merely once the code for it exists. A project with the code deployed but no expert value yet asserted stays `unit_only`.
- **A `full_hierarchical` score requires `M`, `P`, and `A` all individually eligible** for that specific project at that run's cutoff — a per-project, per-run fact reachable only once PR-3 (Project), PR-4 (Market), and PR-5 (Area) have all shipped **and** all three grains have an actual CEO-approved, published, currently-eligible value for that project.
- **No score may conceal missing grains or claim full contextual completeness when partial.** `score_mode` is mandatory on every response; `excluded_grains` with a specific machine-readable reason is mandatory whenever `score_mode != "full_hierarchical"`; no surface may render or label a `unit_only`/`partial_hierarchical` result as "complete."

---

## 2. Schema and migration plan

Every claim below is re-verified against the current checkout (§0, §"Appendix"). Each subsection states: what exists today, the minimum addition, and why nothing less/more suffices.

### 2.1 `ranking_scores.hierarchical_score` — unchanged from the prior pass, re-verified

```sql
-- Migration: 0037_ranking_hierarchical_score
ALTER TABLE ranking_scores ADD COLUMN hierarchical_score NUMERIC(6, 4);
ALTER TABLE ranking_scores
    ADD CONSTRAINT ck_ranking_scores_hierarchical_range
    CHECK (hierarchical_score IS NULL OR (hierarchical_score >= 0 AND hierarchical_score <= 1));
```

**Writer boundary impact.** `ranking_scores` is already single-writer-declared to `src/ranking/service.py` [`tests/test_ranking_boundary.py:65-70`, `ALLOWED_WRITERS["ranking_scores"]`]. If the new hierarchical step lives in that same file, **no boundary-test change is required for the writer declaration.** `test_the_ranking_tables_keep_the_columns_the_engine_uses` [`tests/test_ranking_boundary.py:172-197`] is a subset check (`expected - columns == set()`) and passes unmodified against a superset of columns.

### 2.2 `ranking_scores.hierarchical_contributions` (new — S9)

**Why a new column, not reuse of `contributions`.** `ranking_scores.contributions JSONB NOT NULL DEFAULT '{}'::jsonb` [`src/models/tables.py:604`] already carries the CRM-only score's per-CRM-feature breakdown (`engine.py`'s own `contributions` dict, keyed by feature key). Reusing it for the hierarchical composition's breakdown would **conflate two different scores' explanations in one JSON blob under the same keys** (`market`/`project`/`area`/`unit` are not CRM feature keys and would collide with nothing today, but doing so implicitly ties the hierarchical surface's presence to a mutation of a column the legacy path already owns and writes every run). A second, independent, nullable column preserves the "don't touch what's already there" boundary exactly as `hierarchical_score` itself does for `.score`.

```sql
-- Migration: 0040_hierarchical_contributions
ALTER TABLE ranking_scores ADD COLUMN hierarchical_contributions JSONB;
    -- nullable, no server_default -- absent/NULL for every row until PR-1 ships
    -- and populates it; NEVER defaults to '{}' the way `contributions` does,
    -- because an empty object would be indistinguishable from "not yet computed"
ALTER TABLE ranking_scores
    ADD CONSTRAINT ck_ranking_scores_hierarchical_contributions_object
    CHECK (hierarchical_contributions IS NULL OR jsonb_typeof(hierarchical_contributions) = 'object');
```

**Full proposed JSON contract:**

```json
{
  "score_mode": "unit_only | partial_hierarchical | full_hierarchical",
  "top_level_weight_coverage": "NUMERIC(5,4) as string, e.g. \"0.6500\"",
  "configured_grain_weights": {"market": "0.10", "project": "0.25", "area": "0.25", "unit": "0.40"},
  "effective_grain_weights": {"<eligible grain>": "w_g / top_level_weight_coverage, as string"},
  "eligible_grains": ["project", "..."],
  "excluded_grains": {
    "market": {"reason": "unpublished | expired | evidence_invalid | conflicted | withdrawn | coverage_below_threshold", "detail": "free text, optional"}
  },
  "grain_scores": {
    "market":  {"score": "NUMERIC or null", "coverage": "NUMERIC(5,4)", "quality_status": "one of 0033's 7-state vocabulary or null if excluded", "feature_justification_id": "uuid or null", "snapshot_id": "uuid or null"},
    "project": {"...": "same shape"},
    "area":    {"...": "same shape"},
    "unit":    {"score": "== ranking_scores.score for this row, redundant on purpose for a self-contained explanation payload"}
  },
  "legal_gate": {"status": "HIGH_RISK | null", "gated": true},
  "config_version_id": "uuid — ranking_configs.id this run's hierarchical_weights came from",
  "cutoff_at": "ISO-8601 timestamp used to select publishable values",
  "computed_at": "ISO-8601 timestamp this row was written"
}
```

Every field above is populated **from data `engine.score_unit()`'s existing return value (`UnitScore.coverage`, `.contributions`, `.score`) already supplies** for the fifth (top-level composition) call, plus small service-layer bookkeeping (§5) — no new arithmetic.

### 2.3 `ranking_configs.hierarchical_weights` (new, corrected storage for D22 — §0.3)

```sql
-- Migration: 0041_ranking_configs_hierarchical_weights
ALTER TABLE ranking_configs ADD COLUMN hierarchical_weights JSONB;
    -- nullable. A config published with this column NULL behaves EXACTLY as
    -- today -- compute_hierarchical_scores() (§5) treats a NULL here as
    -- "hierarchical scoring not configured for this config version" and the
    -- feature-flagged step is a no-op for that run, same as the flag being off.
ALTER TABLE ranking_configs
    ADD CONSTRAINT ck_ranking_configs_hierarchical_weights_object
    CHECK (hierarchical_weights IS NULL OR jsonb_typeof(hierarchical_weights) = 'object');
```

**Shape** (unchanged from `ranking_consultant.md §24.7`'s D22 example, now correctly located):

```json
{
  "grain_weights": {"market": 0.10, "project": 0.25, "area": 0.25, "unit": 0.40},
  "market":  {"market_interest_rate": {"weight": 0.50, "direction": "negative", "missing_value_policy": "neutral"}, "...": "..."},
  "project": {"expert_location_score": {"weight": 0.40, "direction": "positive", "missing_value_policy": "neutral"}, "...": "..."},
  "area":    {"area_accessibility": {"weight": 0.50, "direction": "positive", "missing_value_policy": "neutral"}, "...": "..."}
}
```

**Deliberately does NOT include a `"unit"` block.** The prior pass's example nested the existing published unit-grain weights under `weights["unit"]`; per §0.3's correction, unit-grain weights **stay exactly where they are today** — `ranking_configs.weights`, read by unchanged `_active_config()`. `compute_hierarchical_scores()` (§5) reads the unit-grain `FeatureWeight` list from `.weights` (existing) and the three parent-grain lists plus `grain_weights` from `.hierarchical_weights` (new) — two columns, two readers, zero shared iteration code.

#### 2.3.1 `_validate_nested_config()` — new, separate from `validate_weights()`

```python
# src/ranking/service.py (or a sibling module) -- NEW function, never called
# from ranking_config.py::create_draft/publish. Runs BEFORE any of the five
# engine.score_unit() calls in compute_hierarchical_scores() (§5).

def _validate_nested_config(hierarchical_weights: dict | None) -> None:
    if hierarchical_weights is None:
        return  # hierarchical scoring not configured for this config version -- valid, not an error
    required = {"market", "project", "area", "grain_weights"}
    missing = required - hierarchical_weights.keys()
    if missing:
        raise ConfigError("NESTED_KEYS_MISSING", f"missing: {sorted(missing)}")

    for grain in ("market", "project", "area"):
        block = hierarchical_weights[grain]
        total = sum(spec["weight"] for spec in block.values())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:          # reuse ranking_config.py:60's constant, VALUE only, no import cycle
            raise ConfigError("GRAIN_WEIGHT_SUM", f"{grain}: {total}")
        for key, spec in block.items():
            if spec.get("direction") not in ("positive", "negative"):
                raise ConfigError("DIRECTION_INVALID", f"{grain}.{key}")
            if spec.get("missing_value_policy") not in ("skip", "zero", "neutral"):
                raise ConfigError("MISSING_POLICY_INVALID", f"{grain}.{key}")
            # NOTE: zero/neutral/skip are all valid for a grain's OWN internal
            # features (consultant.md §24.4.3's rule targets grain_weights
            # below, not this block) -- unchanged from the prior pass's
            # already-correct reasoning.

    grain_weights = hierarchical_weights["grain_weights"]
    if set(grain_weights) != {"market", "project", "area", "unit"}:
        raise ConfigError("GRAIN_WEIGHTS_KEYS", sorted(grain_weights))
    total_grain = sum(grain_weights.values())
    if abs(total_grain - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ConfigError("GRAIN_WEIGHTS_SUM", str(total_grain))
    for grain_key, weight in grain_weights.items():
        if weight < 0:
            raise ConfigError("GRAIN_WEIGHT_NEGATIVE", grain_key)
    # D37 note: grain_weights carries a bare weight per grain, not a full
    # FeatureWeight spec with its own missing_value_policy -- eligibility
    # (§5.3) is a service-layer determination BEFORE this weight is ever
    # consumed by engine.score_unit(), and every parent-grain term's
    # engine-level FeatureWeight.missing_value_policy is HARD-CODED "skip"
    # by compute_hierarchical_scores() itself (§5), never read from config --
    # this is what makes "zero forbidden for top-level terms"
    # (consultant.md §24.4.3) true by construction, not by validation alone.
```

**`create_draft()`/`publish()` — EXTEND, minimal.** Both gain an optional `hierarchical_weights: dict | None = None` parameter; when `None` (every existing caller today), behavior is **byte-identical** to the current signature — no existing test changes. When provided, `_validate_nested_config()` (above) runs in addition to the existing `validate_weights(weights)` call, and the value is stored in the new column on the same `INSERT`/`UPDATE` `create_draft()`/`publish()` already perform. **No second writer, no boundary-test change** — `src/services/ranking_config.py` remains the sole writer of `ranking_configs` [`tests/test_ranking_boundary.py:65-70`].

### 2.4 Widen `ck_rfd_grain` (market only) — unchanged from the prior pass

```sql
-- Migration: 0038_widen_grain_market
ALTER TABLE ranking_feature_definitions DROP CONSTRAINT ck_rfd_grain;
ALTER TABLE ranking_feature_definitions
    ADD CONSTRAINT ck_rfd_grain
    CHECK (grain IN ('project', 'area', 'project_area', 'unit', 'market'));
-- 'developer' intentionally omitted -- no source, no feature, out of scope
```

### 2.5 Widen `ranking_feature_snapshots` + `ranking_feature_values` scope_type — unchanged, coupled (§0.2)

```sql
-- Migration: 0039_widen_scope_type_area_market

-- --- ranking_feature_snapshots ---
ALTER TABLE ranking_feature_snapshots DROP CONSTRAINT ck_rfs_scope_type_project;
ALTER TABLE ranking_feature_snapshots
    ADD CONSTRAINT ck_rfs_scope_type_allowed
    CHECK (scope_type IN ('project', 'area', 'market'));

ALTER TABLE ranking_feature_snapshots DROP CONSTRAINT ck_rfs_project_scope_no_area;
ALTER TABLE ranking_feature_snapshots
    ADD CONSTRAINT ck_rfs_scope_shape
    CHECK (area_id IS NULL);
    -- unchanged in effect -- one snapshot row covers every area in the
    -- project regardless of scope_type [uq_ranking_feature_snapshot_run_project_scope,
    -- 0033:143-148]; individual areas are distinguished in ranking_feature_values.area_id

-- --- ranking_feature_values ---
ALTER TABLE ranking_feature_values DROP CONSTRAINT ck_rfv_scope_type_project;
ALTER TABLE ranking_feature_values
    ADD CONSTRAINT ck_rfv_scope_type_allowed
    CHECK (scope_type IN ('project', 'area', 'market'));
    -- 'unit' NOT added -- unit stays 100% CRM, no expert override in scope

ALTER TABLE ranking_feature_values DROP CONSTRAINT ck_rfv_project_scope_shape;
ALTER TABLE ranking_feature_values
    ADD CONSTRAINT ck_rfv_scope_shape
    CHECK (
        (scope_type = 'project' AND area_id IS NULL AND unit_id IS NULL) OR
        (scope_type = 'area'    AND area_id IS NOT NULL AND unit_id IS NULL) OR
        (scope_type = 'market'  AND area_id IS NULL AND unit_id IS NULL)
    );
```

**Market has no project-independent home in this schema — a deliberate, disclosed tradeoff, unchanged from the prior pass.** `ranking_feature_values.project_id NOT NULL` [`0033:180`] and the composite FK tie every value to a project-scoped snapshot; Market values are written **once per project, denormalized** (D39, `PENDING`). Revisiting this (nullable `project_id` + partial unique index, or a standalone `market_context_values` table) is deferred, not part of this migration.

### 2.6 NEW — widen `ranking_weight_proposals`/`ranking_feature_justifications` for value-mode (governance-side, not covered by the prior pass)

The prior revision of this plan widened only the `0033` **feature-store** tables (§2.5). It never widened the `0034` **governance** tables, so a value-mode proposal could never be scoped to `area`/`market` even after §2.5 ships. This is a real gap in the prior plan, closed here.

```sql
-- Migration: 0042_governance_value_mode

-- --- ranking_weight_proposals: scope widening, same shape as §2.5 ---
ALTER TABLE ranking_weight_proposals DROP CONSTRAINT ck_rwp_scope_type_project;
ALTER TABLE ranking_weight_proposals
    ADD CONSTRAINT ck_rwp_scope_type_allowed
    CHECK (scope_type IN ('project', 'area', 'market'));

ALTER TABLE ranking_weight_proposals DROP CONSTRAINT ck_rwp_project_scope_no_area;
ALTER TABLE ranking_weight_proposals
    ADD CONSTRAINT ck_rwp_scope_shape
    CHECK (
        (scope_type IN ('project', 'market') AND area_id IS NULL) OR
        (scope_type = 'area' AND area_id IS NOT NULL)
    );
    -- project_id stays NOT NULL regardless of scope_type [0034:61, unchanged] --
    -- a market-scope proposal is still filed against one in-scope project
    -- (denormalized, §2.5's Market tradeoff, same project_id it will
    -- eventually materialize a value for)

-- --- ranking_feature_justifications: weight XOR value-assertion mode ---
ALTER TABLE ranking_feature_justifications
    ALTER COLUMN proposed_weight DROP NOT NULL;
    -- Relaxes an existing NOT NULL [0034:117]. Safe: every existing row already
    -- has a non-null value; the practical guarantee for weight-mode rows is
    -- re-established below by ck_rfj_assertion_mode_xor, not by column
    -- nullability alone.
ALTER TABLE ranking_feature_justifications ADD COLUMN assertion_kind TEXT NOT NULL DEFAULT 'weight';
ALTER TABLE ranking_feature_justifications
    ADD CONSTRAINT ck_rfj_assertion_kind CHECK (assertion_kind IN ('weight', 'value'));
ALTER TABLE ranking_feature_justifications ADD COLUMN value_scope_type TEXT;
ALTER TABLE ranking_feature_justifications
    ADD CONSTRAINT ck_rfj_value_scope_type CHECK (value_scope_type IS NULL OR value_scope_type IN ('project', 'area', 'market'));
ALTER TABLE ranking_feature_justifications ADD COLUMN value_area_id UUID REFERENCES areas(id);
ALTER TABLE ranking_feature_justifications ADD COLUMN raw_numeric NUMERIC(24, 10);
ALTER TABLE ranking_feature_justifications ADD COLUMN normalized_numeric NUMERIC(12, 8);
ALTER TABLE ranking_feature_justifications
    ADD CONSTRAINT ck_rfj_normalized_range
    CHECK (normalized_numeric IS NULL OR (normalized_numeric >= 0 AND normalized_numeric <= 1));
ALTER TABLE ranking_feature_justifications ADD COLUMN categorical_value TEXT;
ALTER TABLE ranking_feature_justifications ADD COLUMN effective_at TIMESTAMPTZ;
ALTER TABLE ranking_feature_justifications ADD COLUMN external_source_citation TEXT;

ALTER TABLE ranking_feature_justifications
    ADD CONSTRAINT ck_rfj_assertion_mode_xor
    CHECK (
        (assertion_kind = 'weight'
            AND proposed_weight IS NOT NULL
            AND value_scope_type IS NULL AND value_area_id IS NULL
            AND raw_numeric IS NULL AND normalized_numeric IS NULL AND categorical_value IS NULL
            AND effective_at IS NULL)
        OR
        (assertion_kind = 'value'
            AND proposed_weight IS NULL
            AND value_scope_type IS NOT NULL
            AND (raw_numeric IS NOT NULL OR normalized_numeric IS NOT NULL OR categorical_value IS NOT NULL)
            AND effective_at IS NOT NULL)
    );
ALTER TABLE ranking_feature_justifications
    ADD CONSTRAINT ck_rfj_market_citation_required
    CHECK (value_scope_type <> 'market' OR (external_source_citation IS NOT NULL AND external_source_citation <> ''));
```

**Why these fields and no others.** `feature_definition_id` (existing, `fk_rfj_feature_definition_id` [`0034:139-142`]) is reused unchanged for both modes — a value-mode row is still "about" one `ranking_feature_definitions` row. `rationale`/`methodology`/`evidence_summary`/`expected_effect`/`confidence`/`limitations` (existing, all `NOT NULL` [`0034:118-123`]) are reused unchanged — the task's "fields necessary for... rationale" requirement is already met, not re-added. **Author** is `created_by_expert_id` (existing, unchanged). **CEO approval** is the existing `ranking_proposal_reviews` row for this proposal (§3) — no new column needed to record it (§3.4). **Publication lineage** is the existing `ranking_feature_lineage` table (§0.1) once a value is materialized (§3.3) — no new column needed there either.

**Preserves weight-proposal behavior exactly.** Every existing row has `assertion_kind = 'weight'` (the `DEFAULT`), `proposed_weight` populated, and all six new nullable columns `NULL` — the XOR CHECK's first branch matches every existing row without any data migration. `upsert_justification()` [`governance.py:382-497`] is **extended**, not modified, to branch on a new `assertion_kind` parameter; when `assertion_kind == "weight"` (the default), it runs **exactly** the code path it runs today, byte-for-byte.

### 2.7 Auth-discovery gate — the actual mechanism, verified against `oidc.py`/`dashboard_auth.py`

The task explicitly forbids asserting a new `expert_profiles.role` column "unless verified against auth/profile code." It is not verified — the real mechanism is smaller, already half-built, and lives in the auth layer, not the profile table.

**What already exists, verified line-by-line:**

- `docker/keycloak/p100-realm.json:34` defines a realm role literally named **`CRM.CEO`**, with a comment: *"Canonical Entra/Keycloak App Role -> internal 'admin'... this exact name is what those fixed maps look for."*
- `src/services/oidc.py:412-419` — `CANONICAL_APP_ROLES: dict[str, DashboardRole] = {"CRM.CEO": "admin", "CRM.Admin": "admin", ...}`. **`resolve_role()` [`:422-448`] collapses `"CRM.CEO"` down to the same 3-tier `"admin"` `DashboardRole` as every other admin-mapped role and returns only that collapsed tier** — it does not expose which specific realm role matched. **Verified: there is no functional distinction between a CEO-holder and any other admin-role holder anywhere in the code today.** (The realm JSON's comment also names a file, `src/services/entra_auth.py`, that **`NOT FOUND`** — does not exist in this repository; a stale comment, not fixed here as it's outside this plan's scope.)
- `oidc.verify_token()` [`oidc.py:347-391`] already extracts, from a cryptographically verified JWT, `OidcIdentity(subject, email, display_name, roles: frozenset[str], groups, expires_at)` [`oidc.py:78-84`] — `roles` is the **raw, uncollapsed** set of realm roles, so `"CRM.CEO" in identity.roles` is already a real, verifiable, non-spoofable signal sitting in a local variable inside `authenticate_dashboard()` [`dashboard_auth.py:148-156`] — and is **discarded** when constructing `DashboardPrincipal(role=role_from_oidc, project_scope=...)` [`:151-155`], which carries **only `role: DashboardRole` and `project_scope`** [`dashboard_auth.py:61-65`] — confirmed also by `docs/ranking/governance_api.md:20-28`'s own "Identity model" section: *"`DashboardPrincipal`... carries only `role` and `project_scope` — no per-person identifier... Nothing here validates that the caller IS the expert_id they claim — that gap is open question D18."*
- The session-cookie path is a second, separate branch: `issue_session()` [`oidc.py:458-481`] already embeds `sub`/`email`/`name`/`role`/`scope` into the session JWT at login time, called live from `src/api/auth.py:127,204` — but **not** the raw `roles` frozenset, so `"CRM.CEO"`-specific information does not survive into a session cookie today even though it exists at login time, in `identity.roles`, one call earlier.

**The auth-discovery gate this plan specifies — additive, verified-minimal:**

1. **`DashboardPrincipal`** [`dashboard_auth.py:61-65`] gains two new fields, both optional/defaulted so every existing construction site (`role="admin", project_scope="ALL"` for the dev-bypass branch, the static-token branch, etc.) is unaffected: `subject: str | None = None`, `is_ceo: bool = False`.
2. **`authenticate_dashboard()`'s direct-JWT branch** [`dashboard_auth.py:148-156`] populates both from data it **already has in hand**: `subject=identity.subject`, `is_ceo="CRM.CEO" in identity.roles` — zero new I/O, zero new verification, purely reading fields `verify_token()` already computed and currently discards.
3. **`issue_session()`** [`oidc.py:458-481`] gains one new payload key, computed at the one point `identity.roles` is still available: `payload["is_ceo"] = "CRM.CEO" in identity.roles`. `_decode_session()`'s `options["require"]` [`oidc.py:486`] is **extended** to also accept (not require, to avoid invalidating sessions issued before this ships) an optional `is_ceo` claim.
4. **`authenticate_dashboard()`'s session-cookie branch** [`dashboard_auth.py:120-126`] reads `subject=claims.get("sub")`, `is_ceo=bool(claims.get("is_ceo", False))` into the returned `DashboardPrincipal`.
5. **Static-token and dev-bypass branches** [`dashboard_auth.py:129-130,157-163`] leave `subject=None, is_ceo=False` — **no per-person identity exists in those modes, so CEO-gated approval is structurally unavailable there, by design (fail-closed), not silently permitted.** This must be an explicit, tested behavior (T-CEO-3, §7), not an oversight.
6. **A new `require_ceo` dependency**, mirroring `require_role()`'s existing factory pattern [`dashboard_auth.py:179-...`], additive: `def require_ceo(): ...; raise 403 if not principal.is_ceo`. Used **only** by the new value-mode review/approve/publish-verification code paths (§3); every existing `require_viewer`/`require_operator`/`require_admin`-gated route is untouched.
7. **Governance write paths stop trusting client-supplied actor IDs for authorization.** Today, every governance route resolves the acting identity from `payload.actor_expert_id`/`.created_by_expert_id`/`.reviewer_expert_id` in the **request body** [verified: `src/api/governance.py`, e.g. `:216-223` for proposal creation, `:306-312` for publish], not from `principal`. This plan's value-mode routes (§3) **additionally** require `principal.subject` to resolve to an `expert_profiles` row (`WHERE identity_subject = principal.subject`) and use **that** resolved id as the authoritative actor — a body-supplied id that disagrees with the resolved one is rejected (`IDENTITY_MISMATCH`), not silently preferred. Weight-mode routes are **unchanged** — this is scoped to the new value-mode paths only, per "preserve weight-proposal behavior exactly."

**What this does not close, stated plainly.** In static-token/dev-bypass mode there is no per-person identity at all (§5 above) — this is an accepted, pre-existing limitation of that mode, not something this plan invents or is asked to fix. In OIDC mode, this closes "is the caller a CEO" and "does the caller's body-supplied id match their authenticated identity" — it does **not** invent a new database write path for a CEO (§3.4 states the write path is unchanged, gated by the existing `mark_published`), and it does not touch `resolve_role()`'s existing 3-tier collapsing behavior used everywhere else in the app.

### 2.8 Legal status storage — reuse verified, zero new schema

`ranking_feature_definitions.value_type` already permits `'categorical'` [`ck_rfd_value_type`, `0033:63-66`]; `ranking_feature_values.value_kind` already permits `'categorical'` with a `categorical_value TEXT` column and the matching branch of `ck_rfv_typed_value_missing_semantics` [`0033:246-249`, full constraint `0033:237-256`]; `grain='project'` needs no widening. **A Legal factor value needs zero schema addition beyond the generic value-mode columns §2.6 already adds** (which also carry `categorical_value` for exactly this case). The `HIGH_RISK` vocabulary itself (D40, `PENDING`) is a **data** decision (which `categorical_value` strings are valid, enforced by an optional future conditional CHECK keyed on `feature_key`), not a schema gap this plan needs to close now.

### 2.9 Migration sequence, upgrade/downgrade safety, and per-migration test scope

```text
0036_remove_historical_ranking          <- current head [confirmed: alembic/versions/, single head, chain verified §0]
  |
0037_ranking_hierarchical_score         <- §2.1. Independently shippable. Zero behavior change.
  |
0038_widen_grain_market                 <- §2.4. Independently shippable. Zero rows violate it (0033 tables are empty in production).
  |
0039_widen_scope_type_area_market       <- §2.5. Depends on 0038 for CHECK naming legibility, not a hard FK order.
  |
0040_hierarchical_contributions         <- §2.2. Independent of 0037-0039; may ship in the same PR as 0037 (both target ranking_scores).
  |
0041_ranking_configs_hierarchical_weights <- §2.3. Independent of all above; targets ranking_configs.
  |
0042_governance_value_mode              <- §2.6. Depends on 0039 (area/market scope_type must exist on the FEATURE-STORE
                                            side before a governance-side value-mode proposal for those scopes is
                                            meaningful) though no hard FK enforces the order.
```

| Migration | Upgrade risk | Downgrade safety | Append-only/audit implication |
|---|---|---|---|
| `0037` | Additive column + CHECK on a table with an established single writer | `DROP COLUMN hierarchical_score` — loses only hierarchical scores, `.score`/`.rank_in_area`/`.rank_in_project`/`.contributions` unaffected | None — `ranking_scores` carries no append-only trigger today, unchanged |
| `0038` | CHECK widening, zero existing rows can violate it | Refuse if any `ranking_feature_definitions` row has `grain='market'` (`_has_rows`-style guard, mirroring `0033:26-28`) | None |
| `0039` | Coupled CHECK widening across two tables | Refuse if any row has `scope_type <> 'project'` on either table (same guard pattern, `0033:378-405`'s precedent) | `ranking_feature_snapshots`/`ranking_feature_values` are append-only [`0033:354-378`] — a downgrade that ran anyway (guard bypassed) would be rejected a second time by the trigger itself on any `UPDATE`/`DELETE` the downgrade attempted, a genuine belt-and-suspenders property worth a test (T-migration-2, §7) |
| `0040` | Additive column + CHECK, same table/writer as `0037` | `DROP COLUMN hierarchical_contributions` — independent of `.hierarchical_score`, no cross-column data loss | None |
| `0041` | Additive column + CHECK on `ranking_configs` | `DROP COLUMN hierarchical_weights` — every `_active_config()`/`validate_weights()` call is unaffected (§0.3), since neither ever reads it | `ranking_configs` carries no append-only trigger (its own append-only-in-spirit guarantee is `create_draft`/`publish`'s application logic, not a DB trigger) — unchanged |
| `0042` | `ALTER COLUMN ... DROP NOT NULL` (real, disclosed relaxation) + 5 new nullable columns + 2 new CHECKs on `ranking_feature_justifications`; CHECK re-typing on `ranking_weight_proposals` | Refuse if any `ranking_feature_justifications` row has `assertion_kind = 'value'` (new rows only, by construction, until PR-2 ships) **before** re-adding `NOT NULL` to `proposed_weight` and dropping the new columns; refuse `ranking_weight_proposals` downgrade if any row has `scope_type <> 'project'` | `ranking_proposal_reviews`/`ranking_config_audit_events` carry the append-only guard [`0034:325-347`]; `ranking_weight_proposals`/`ranking_feature_justifications` deliberately do not (live workflow status) — unchanged, this migration adds columns, not triggers |

**POST-PR-5 CORRECTION (release-hardening pass) — actual shipped migration numbers/names, verified at the current Alembic head.** The `§2.9` sequence above (`0037_ranking_hierarchical_score` ... `0042_governance_value_mode`) was written **before** implementation began and never matched what shipped — not just the struck `0039_widen_scope_type_area_market` name, but every number and grouping in that diagram. ~~`0037_ranking_hierarchical_score` / `0038_widen_grain_market` / `0039_widen_scope_type_area_market` / `0040_hierarchical_contributions` / `0041_ranking_configs_hierarchical_weights` / `0042_governance_value_mode`~~ — **as actually shipped, one migration per PR, verified `alembic heads` == exactly one, `0041_area_grain_scope`:**

| Revision (as shipped) | PR | What it does |
|---|---|---|
| `0037_hierarchical_scoring_pr1` | PR-1 | `ranking_scores.hierarchical_score`/`.hierarchical_contributions`, `ranking_configs.hierarchical_weights` — three additive nullable columns |
| `0038_governance_value_mode` | PR-2 | `ranking_feature_definitions.grain` widened; `ranking_weight_proposals`/`ranking_feature_justifications` value-mode columns + area/market scope widening |
| `0039_project_value_materialize` | PR-3 | `ranking_feature_values.source_justification_id` (provenance link back to the CEO-approved assertion) |
| `0040_market_grain_scope` | PR-4 | `ranking_feature_snapshots`/`ranking_feature_values.scope_type` widened to include `'market'`; seeds 4 Market feature definitions |
| `0041_area_grain_scope` | PR-5 | `scope_type` widened to include `'area'`; **two partial unique indexes** replacing the single run/project/scope constraint (`uq_rfs_run_project_scope_no_area` for Project/Market's unchanged one-row guarantee, `uq_rfs_run_project_area_scope` for Area's one-row-per-area guarantee); snapshot↔value composite FK widened to include `area_id`; seeds 3 expert Area feature definitions |

---

## 3. Governance and writer plan

### 3.0 POST-PR-3 CORRECTION — terminology and actual writer location (verified against shipped code, PR-4 Stage A)

Everything below in this section (§3.1–§3.4) was written **before** PR-3 shipped, and describes a design that turned out to be schema-incompatible in one specific way: it says a governance publish action (`mark_published()`) directly inserts into `ranking_feature_values`. **Verified against the actual PR-3 implementation, this is wrong** and is struck through in place below, not silently corrected — a governance-owned assertion and a ranking-run-owned snapshot copy are two different things with two different lifecycles, and this document previously conflated them by using one function name (`materialize_published_feature_value()`) for both.

**Two distinct concepts, going forward:**

```text
Published value assertion:
- A governance-owned, CEO-approved/published assertion
  (a ranking_weight_proposals row with assertion_kind='value',
  status='published', plus its ranking_feature_justifications row).
- Contains value, scope, rationale, evidence, effective/expiry data.
- Is global/logical for its asserted scope (one row, not per-run).
- Is NOT itself a row in ranking_feature_values — that table is
  snapshot-bound (NOT NULL, FK'd snapshot_id, verified src/models/tables.py).
- Does not belong to a ranking run.

Snapshot feature value:
- A ranking-run-owned immutable COPY of a published value assertion.
- Created lazily by the snapshot builder at ranking cutoff — never at
  governance-publish time (no ranking_run_id/cutoff exists then).
- Stored in ranking_feature_values, always carrying snapshot_id (NOT NULL).
- Consumed by scoring; never a live read from governance during score
  calculation.
```

**Corrected function mapping (verified against `src/services/governance.py` and `src/ranking/service.py` as actually shipped):**

- ~~`materialize_published_feature_value()` (NEW, `src/services/governance.py`, called from the extended `mark_published()`)~~ — **does not exist as described.** The governance-side state transition is `governance.mark_published()` — for `assertion_kind='value'`, it re-verifies readiness (`validate_value_assertion_for_materialization()`) and flips `ranking_weight_proposals.status → 'published'`. It performs **no** insert into `ranking_feature_values`/`ranking_feature_snapshots`/`ranking_feature_lineage` — there is no `ranking_run_id`/cutoff available at that point for those tables' `NOT NULL` FKs to be satisfied.
- The **actual** writer of those three tables is `src/ranking/service.py::materialize_published_feature_value()` (yes, this exact name — Project's PR-3 implementation kept it, scoped to one grain's per-(run, feature) idempotent copy), called only by `src/ranking/service.py::build_project_feature_snapshot_for_run()` (the actual name of what this document calls `build_hierarchical_feature_snapshot()` below — implemented Project-only, not the four-scope-type function §5.2 describes).
- Use `publish_value_assertion()` as the conceptual name for the governance state transition (`mark_published()`'s value-mode branch) when writing new prose — it publishes the **assertion**, not a snapshot row.
- Use `materialize_<grain>_assertion_into_snapshot()` / `copy_published_<grain>_assertion_to_run_snapshot()` as the conceptual name for the per-run, per-grain lazy copy step (PR-4's Market equivalent is literally named `copy_published_market_assertions_to_run_snapshot()`, see PR-4's own report).

**Corrected D34 rollout statement** (§9's D34 row is **not** superseded by this — restated here for the coding handoff, since §5.4's pseudocode below reads as if per-grain thresholds were already decided and shipped): **PR-3's actual shipped rollout policy is `min_weight_coverage = 0` for every per-grain `engine.score_unit()` call** (Project, and Market as of PR-4) — a grain is scoreable the moment at least one of its configured features resolves a value; coverage is *disclosed* in `hierarchical_contributions` (`grains.<grain>.coverage`), it is **not** currently a blocking threshold. **D34 remains PENDING** — whether a stricter, configurable per-grain threshold is introduced later is an open owner decision, not something this rollout silently decided. Do not read the four-distinct-config-value design in §2.3/§5.4 below as shipped; it is not.

**Completion note (PR-1..PR-4 integration hardening pass):** every remaining occurrence of `governance.py::materialize_published_feature_value()`/`select_publishable_feature_values_at_cutoff()`/`build_hierarchical_feature_snapshot()` describing a direct-insert-from-publish story, in both this document (§3.2's table, §5.1's pipeline snippet, §5.2's builder heading/predicate note, the §8 PR-3 delivery-table row, §10 gate 6) and `ranking_consultant.md` (§24.7/§24.12.1's narrative, T17, the Publisher row, the hard-rule/predicate sentence, the CEO-approval row, the snapshot-builder row, the Mermaid diagram, the §24.12.6 pseudocode preface, the abuse-prevention rows, the data-lineage matrix), has now been struck or corrected in place. The two remaining pseudocode CODE BLOCKS (§5.2 here, §24.12.6 there) are left as illustrative pseudocode — each is immediately preceded by an explicit correction paragraph naming the real shipped functions, so no reader can come away believing the code inside those specific fences is what actually runs.

### 3.1 End-to-end lifecycle

```text
draft (Expert/analyst authors; assertion_kind='value' XOR 'weight')
  -> evidence attached/validated (PDF registered + linked; validate_evidence() checks entity/date at READ time, not write time)
  -> submitted (>=1 justification required, existing check unchanged)
  -> CEO review (approve | reject | request_changes)
  -> approved  |  rejected  (terminal for this cycle; rejected can be superseded only by a NEW proposal, no in-place resubmit exists today — D19, open, unchanged)
  -> ~~system publication (materialize_published_feature_value(), NEW — value-mode only; mark_published()'s existing weight-mode branch, UNCHANGED)~~
     system publication (publish_value_assertion() / governance.mark_published()'s value-mode branch — re-verifies readiness, flips status only; writes NOTHING to ranking_feature_values/_snapshots/_lineage — see §3.0)
  -> ~~publishable feature value (ranking_feature_values row exists, status resolvable via its proposal's 'published' state)~~
     published value assertion (governance-owned; still no ranking_feature_values row — see §3.0)
  -> ~~immutable snapshot (build_hierarchical_feature_snapshot(), NEW — copies the value into a ranking_feature_snapshots-scoped row at a run's cutoff)~~
     immutable snapshot (per-grain lazy copy, e.g. `build_project_feature_snapshot_for_run()`/PR-4's `copy_published_market_assertions_to_run_snapshot()` — copies the published assertion into a ranking_feature_snapshots-scoped row at THIS run's cutoff, only now does a ranking_feature_values row get created — see §3.0)
  -> scoring consumption (compute_hierarchical_scores() reads the snapshot ONLY, never a live query)
  -> expiry / supersession / withdrawal
       - expiry: excluded from any FUTURE run's selection once past its shelf-life (definition_metadata-driven, §4), but a PAST run's snapshot already pinned it and replays identically
       - supersession: a newer 'published' value for the same identity always wins at selection time (ORDER BY observed_at DESC LIMIT 1) -- the old value is never mutated, never deleted, simply not selected going forward; a past run's snapshot is unaffected
       - withdrawal: only possible pre-publish (draft/submitted/under_review -> withdrawn, existing mechanism); a PUBLISHED value has no withdraw transition -- consistent with `published` being a CHECK-enforced dead end today [ck_rwp_published_requires_approved, 0034:103-104] and this plan adding no new status
```

**No new status is added to `PROPOSAL_STATUSES`** [`governance.py:67-74`: `("draft", "submitted", "under_review", "approved", "rejected", "withdrawn", "published")`] — `superseded`/`expired` are read-time selection facts (§5.2), not write-time transitions, matching the existing `project_price_observations` effective-dated precedent already in this codebase (`domain_projection.py::_apply_price_observation`, `0027`).

### 3.2 Permitted actor per transition, server-side authorization

| Transition | Function | Permitted actor | Server-side check (this plan adds, marked NEW) |
|---|---|---|---|
| → `draft` | `create_proposal()` [`governance.py:206-263`] — IMPLEMENTED, weight-only shape today; **EXTEND** to accept `scope_type IN ('project','area','market')` (§2.6) | Analyst/Expert (resolved via `principal.subject`, §2.7) | **NEW:** resolve `created_by_expert_id` from `principal.subject`, not the request body |
| justification create/edit | `upsert_justification()` [`governance.py:382-497`] — **EXTEND** to accept `assertion_kind`/value-mode fields (§2.6) | Same author, only while `draft` [`_JUSTIFICATION_EDITABLE_STATUSES = ("draft",)`, `governance.py:84`, unchanged] | Unchanged check, extended field set |
| evidence attach | `register_evidence_document()`/`link_evidence_to_justification()` [`governance.py:532,620`] — IMPLEMENTED, unchanged | Author | Unchanged |
| → `submitted` | `submit_proposal()` [`governance.py:308-347`] — IMPLEMENTED, unchanged (≥1 justification, any mode) | Author | Unchanged |
| CEO review | `submit_review()` [`governance.py:672-753`] — **EXTEND**, value-mode branch only | **CEO only, for value-mode proposals** | **NEW, two checks, value-mode only:** (a) `reviewer_expert_id == proposal["created_by_expert_id"]` → reject `SELF_APPROVAL_FORBIDDEN`; (b) resolved `principal.is_ceo` is not `True` → reject `CEO_APPROVAL_REQUIRED`. Weight-mode branch: **unchanged**, no new check, confirmed no self-approval check exists there today [`governance.py:672-753`, verified] and this plan does not add one for weight-mode |
| → `published` (governance state only — **corrected, §3.0: no materialization happens here**) | `mark_published()` [`governance.py`] — **EXTEND**: branch on `assertion_kind`; value-mode branch re-verifies readiness and flips status ONLY, writes nothing to the feature-store tables | System Publisher (may hold the same admin token as the CEO reviewer; **never** the same `expert_profiles` identity as the author) | **NEW, value-mode branch:** re-verify (defense in depth, not a repeat of the route-level gate) that the `approved`-decision `ranking_proposal_reviews` row for this proposal was made by a `principal.is_ceo`-true actor **at decision time** — ~~re-derive from the same audit trail (§3.4), not from a stored boolean, since none is stored~~ **corrected, as shipped: `ranking_proposal_reviews.reviewer_is_ceo` IS a stored boolean (0038) — a deliberate, disclosed deviation from this plan's original "re-derive, don't store" recommendation, justified in the PR-2 delivery report as avoiding a live-Keycloak dependency for replaying a past approval** |
| snapshot selection (Project PR-3, Market PR-4) | ~~`select_publishable_feature_values_at_cutoff()` (NEW, §5.2)~~ — **as shipped: `_select_eligible_project_justifications()`/`_select_eligible_market_justifications()`, `src/ranking/service.py`** | Ranking service only, no HTTP surface | Reads only `status='published'` rows — an `approved`-but-not-`published` value is invisible, matching how an `approved`-but-unpublished config is invisible to `run_ranking()` today [`0014`'s `uq_ranking_configs_published` pattern] |
| scoring | `compute_hierarchical_scores()` (NEW, §5.3) | Ranking service only | Reads only a `ranking_feature_snapshots` row, never `ranking_feature_values`/`ranking_weight_proposals` live |

**Evidence immutability after CEO review.** Nothing in `link_evidence_to_justification()` [`governance.py:620-653`] is blocked once a justification's proposal leaves `draft` — **verified, this is a real, currently-open gap**, not specific to value-mode: an author could attach a new document to an already-`submitted` (or even already-`approved`) justification, after the CEO's review decision was recorded against a different evidence set. This plan's smallest closure: **EXTEND** `link_evidence_to_justification()` to reject (`EVIDENCE_LOCKED`) any link attempt once the parent proposal's status is outside `_JUSTIFICATION_EDITABLE_STATUSES` (`"draft"`, already the exact set `upsert_justification()` itself uses [`governance.py:84`] — reusing the existing constant, not inventing a second one). Any evidence change needed after that point requires a **new** justification (and therefore, in practice for value-mode, a new proposal) — no "add a revision" mechanism is invented, consistent with "reuse existing governance state machine, don't invent a duplicate."

### 3.3 ~~System publication — no direct CEO/publisher DB-write path~~ System publication (governance) + per-run snapshot materialization (ranking service) — two separate writers, see §3.0

~~`materialize_published_feature_value()` (NEW, `src/services/governance.py`, called from the extended `mark_published()` above) is the **sole** writer this plan adds for `ranking_feature_values`/`ranking_feature_snapshots`(via §5's builder)/`ranking_feature_lineage` — closing three of the six `0033` tables' "no declared writer today" gap [`consultant.md §24.7` S6]. It:~~

~~1. Re-verifies the proposal is `status='approved'` and (value-mode) its approving review was CEO-made (§3.2).~~
~~2. Inserts one `ranking_feature_values` row (`scope_type`/`area_id` from `value_scope_type`/`value_area_id`, `raw_numeric`/`normalized_numeric`/`categorical_value` from the justification, `observed_at = effective_at`, `quality_status='ok'`).~~
~~3. Inserts one `ranking_feature_lineage` row (`source_relation='ranking_feature_justifications'`, `source_record_id=<justification id>`) — the same join `advisory_tools.py`'s four evidence functions already resolve **by justification id** [`:749,762,789,863`, verified unchanged], so the existing RAG explanation pipeline requires **zero code change** to explain a value-mode result.~~
~~4. Updates the proposal's `published_at` (existing column, existing semantics).~~

**Corrected (§3.0):** `governance.mark_published()`'s value-mode branch does steps 1 and 4 above only — re-verifies readiness (via `validate_value_assertion_for_materialization()`, which accepts the proposal in either `approved` or `published` state, since it is called again later at materialization time) and updates `published_at`/`status`. Steps 2–3 (inserting `ranking_feature_values`/`ranking_feature_lineage`) happen **later**, per ranking run, inside `src/ranking/service.py::materialize_published_feature_value()` — called by the per-grain snapshot builder, never by governance, never at publish time. This is still "no direct published value write without a verified CEO approval trail" — the ranking-service writer re-verifies the same CEO/self-approval/evidence chain itself before writing, via the same `validate_value_assertion_for_materialization()` call — just not in the same function or at the same moment this section originally described. A boundary test (§7, and `tests/test_ranking_boundary.py`'s `FEATURE_STORE_ALLOWED_WRITERS`) asserts single-writer discipline for these three tables, declared as `src/ranking/service.py`, not `governance.py`.

### 3.4 CEO approval trail — reconstructable via existing joins, no new column

Exactly one `approved`-decision `ranking_proposal_reviews` row exists per successfully-approved proposal — enforced by the state machine (`_REVIEWABLE_STATUSES = ("submitted", "under_review")` [`governance.py:85`] excludes `approved` proposals from further review), not a DB constraint. The trail from a published value back to the CEO decision that authorized it is: `ranking_feature_values` → (`ranking_feature_lineage.source_record_id`) → `ranking_feature_justifications.proposal_id` → `ranking_proposal_reviews WHERE decision='approved'` — every column already exists [`0033`, `0034`]. **No new column is required.** What is new is `principal.is_ceo` being checked at the moment that review row was created (§3.2) — the row itself needed no schema change to become "auditable with identity, timestamp, decision, rationale" (`reviewer_expert_id`/`decided_at`/`decision`/`comment`, all `NOT NULL` already [`0034`]).

**Residual gap, stated plainly, not silently closed:** the CEO's `comment` [`ck_rpr_comment_not_blank`] is not required to name which evidence documents were reviewed — "evidence set reviewed" (part of the task's own D38 auditability requirement) is only partially satisfiable: the *decision* is fully auditable, the *evidence set considered* is not pinned at decision time (§3.2's evidence-lock closes "changed after," not "which ones were actually read before"). Flagged, not fixed — inventing an evidence-acknowledgment mechanism is out of the smallest-additive-extension mandate.

### 3.5 Endpoint/service labeling — verified against `governance_api.md` and `src/api/governance.py` directly

| Capability | Endpoint/service | Label | Note |
|---|---|---|---|
| Register expert | `POST /governance/experts` | **IMPLEMENTED** | Unchanged [`governance_api.md:34`] |
| Create proposal, any scope | `POST /governance/proposals` | **EXTEND** | `scope_type` widens to `project\|area\|market`, `area_id` becomes meaningful (§2.6) |
| Create/edit justification | `POST /governance/proposals/{id}/justifications` | **EXTEND** | Body becomes a union: weight-mode (unchanged) XOR value-mode (new fields, §2.6) |
| Register evidence metadata | `POST /governance/evidence` | **IMPLEMENTED** | Unchanged [`governance_api.md:45`] |
| Upload evidence file directly | *(none)* | **NOT FOUND** | `governance_api.md:98-101` already states this gap; not addressed by this plan |
| Link evidence to justification | `POST /governance/evidence/link` | **EXTEND** | Adds the evidence-lock check (§3.2); otherwise unchanged |
| Submit | `POST /governance/proposals/{id}/submit` | **IMPLEMENTED** | Unchanged — ≥1-justification check works for value-mode rows with no code change |
| Review (approve/reject) | `POST /governance/proposals/{id}/reviews` | **EXTEND** | Weight-mode: unchanged. Value-mode: self-approval + CEO-role checks (§3.2) |
| Publish | `POST /governance/proposals/{id}/publish` | **EXTEND** | Branches on `assertion_kind`; value-mode calls the new materializer (§3.3) instead of checking `ranking_configs` |
| List published values | *(none)* | **PROPOSED — new**: `GET /governance/feature-values?project_id=&area_id=&scope_type=` | Read-only, `require_viewer` + existing `require_project_in_scope` pattern |
| Hierarchical ranking preview (no persist) | *(none)* | **PROPOSED — new**: `POST /ranking/hierarchical/preview` | Runs the five `engine.score_unit()` calls in-memory against currently-published values; commits nothing |
| Read hierarchical score | *(none)* | **PROPOSED — new**: `GET /ranking/hierarchical?external_project_id=` | Extends `GET /ranking`'s existing response shape [`src/api/ranking.py:122-135`] with §6's contract |
| Resubmit after `request_changes` | *(none)* | **NOT FOUND** | `governance_api.md:102-105` already states this gap; not addressed here, D19 unchanged |

---

## 4. Grain-specific submission contracts

For each: writer, required fields, validation, snapshot eligibility, scorer behavior when unavailable, and existing-storage-vs-minimum-addition.

### 4.1 Market

| Aspect | Specification |
|---|---|
| Writer | ~~`materialize_published_feature_value()` (§3.3), triggered per-project by a CEO-approved value-mode proposal with `scope_type='market'`~~ — **corrected (§3.0), as shipped:** `governance.mark_published()` only re-verifies and flips status (no feature-store write); `src/ranking/service.py::copy_published_market_assertions_to_run_snapshot()` (PR-4), invoked by the ranking run's hierarchical post-run step, is the actual writer — triggered per (project, ranking run), not directly by the publish action |
| Required fields | `feature_definition_id` (one of `market_interest_rate`/`market_credit_policy`/`market_liquidity`/`market_demand`, grain `'market'`, §2.4); `raw_numeric` and/or `normalized_numeric`; `rationale`/`methodology`/`evidence_summary`/`expected_effect`/`confidence`/`limitations` (existing, unchanged); `effective_at`; **`external_source_citation`, mandatory** (`ck_rfj_market_citation_required`, §2.6) |
| Validation | Normalized `[0,1]` (`ck_rfv_normalized_range`, existing); shelf-life 30 days for `market_interest_rate`, 90 days for `market_credit_policy`/`market_liquidity`/`market_demand`, stored as `{"max_shelf_life_days": 30}` in `ranking_feature_definitions.definition_metadata` (existing JSONB, `ck_rfd_metadata_object`, `0033:76-79` — a data-seeding task, zero migration) |
| Snapshot eligibility | Published + `effective_at <= cutoff` + computed `expires_at > cutoff` (§5.2) |
| Scorer behavior when unavailable | `M` excluded from the top-level composition (`missing_value_policy='skip'`, hard-coded, §2.3.1); never contributes `0`; `hierarchical_contributions.excluded_grains.market.reason` set |
| Reuse vs. addition | Reuses `0033`'s value/evidence/lineage tables and `0034`'s proposal/review/audit tables entirely; the only genuinely new work is the §2.6 governance-scope widening and the §2.3.1 shelf-life data-seed |
| Cost, disclosed | Denormalized per-project (§2.5) — an assertion for a shared market covering N in-scope projects requires **N separate proposals, N separate CEO approvals** (D39, `PENDING`) |

### 4.2 Project

| Aspect | Specification |
|---|---|
| Writer | Same materializer, `scope_type='project'` (already permitted, no widening needed for this grain specifically) |
| Required fields | `expert_location_score`/`expert_infrastructure_score`/`expert_financing_score`; normalized `[0,1]` via the existing `(s-1)/9` 1–10-slider mapping [`consultant.md §23.3.1`, unchanged by this plan]; rationale set (existing); project scope (existing, unwidened) |
| Validation | `ck_rfv_normalized_range` (existing, unchanged) |
| Snapshot eligibility | Same predicate as Market, minus the citation/shelf-life requirement (project-grain factors are not held to Market's stricter citation rule, per `consultant.md §24.7`'s Finding-2-derived reasoning, unchanged) |
| Scorer behavior when unavailable | `P` excluded, same "never zero" rule |
| Reuse vs. addition | **Zero new schema beyond §2.6's generic value-mode columns** — this is the grain closest to already-working, since `0034`'s justification fields were designed with project-grain factors in mind from the start (`consultant.md §23`) |

### 4.3 Area

| Aspect | Specification |
|---|---|
| CRM half (`area_velocity_norm`, `area_conversion_norm`) | **System-owned, non-editable.** Computed by `_area_features()` [`service.py`, unchanged]. No `ranking_feature_definitions` row exists for these keys (0041 seeds only the three expert keys below), so `upsert_justification()`'s `FEATURE_DEFINITION_NOT_FOUND` check structurally blocks any expert from creating a justification for them — enforced by absence — **and, as shipped (PR-5), `upsert_justification()` also rejects either CRM key by name explicitly** (`AREA_CRM_OWNED_FEATURE_KEY_NOT_ASSERTABLE`, `governance.CRM_OWNED_AREA_FEATURE_KEYS`), defense-in-depth in case a future migration ever registered one |
| Expert half (`area_accessibility`, `area_current_infrastructure`, `area_future_infrastructure`) | ~~Writer: same materializer, `scope_type='area'`~~ — **as shipped (PR-5):** `src/ranking/service.py::copy_published_area_assertions_to_run_snapshot(ranking_run_id, project_id, area_id, cutoff_at, session)`, one immutable snapshot **per area** (not per project — Area is the first grain with real per-area identity; `0041`'s two partial unique indexes replace Project/Market's single run/project/scope constraint) |
| Merge, no override | ~~`{**area_crm_values, **area_expert_values}`~~ — **as shipped:** `_merge_area_values()` does exactly this for DISTINCT keys, but a collision is a hard `RankingError(DUPLICATE_CRM_EXPERT_FEATURE_KEY)`, never last-write-wins — stronger than the silent dict-merge this plan originally specified. In practice the collision path is unreachable through the normal authoring flow (governance already blocks it at the source, row above), so this is scoring-time defense-in-depth, not the primary guard |
| Snapshot eligibility | Same predicate, `scope_type='area'`, keyed additionally by `area_id` |
| Scorer behavior when unavailable | `A`'s coverage is bounded to the 2 CRM features alone until an expert value is published; if that coverage still clears `area_min_coverage`, `A` is **eligible** (not excluded) — a genuinely different failure mode from Market/Project, worth its own test (T-A-1, §7): area can be `eligible` on CRM features alone, never having had an expert value at all |

### 4.4 Legal

| Aspect | Specification |
|---|---|
| Representation | Categorical value at project grain — **zero new table, zero new column beyond §2.6's generic `categorical_value`** (§2.8) |
| CEO review | Same value-mode path, same self-approval/CEO-role checks (§3.2) — "cannot be auto-published by ingestion" (a non-negotiable invariant) is satisfied structurally: no source-ingestion-service path exists in this plan at all (it is `PROPOSED`/no connector, unbuilt), so every Legal assertion today can only ever arrive via the human Expert→CEO path |
| Evidence/verifier rules | Same generic evidence-linking mechanism; **who** is qualified to assert `HIGH_RISK` (D38's narrower "named owner" question, distinct from "who may approve," already answered — CEO approves) and the vocabulary beyond `HIGH_RISK` (D40) both remain `PENDING` |
| Gate, outside the weighted mean | Evaluated once per project, from the run's **snapshot**, before any of the five `engine.score_unit()` calls (§5.4) — `if legal_status == 'HIGH_RISK': hierarchical_score = None; hierarchical_contributions = {"legal_gate": {"status": "HIGH_RISK", "gated": True}, ...}`. `ranking_scores.score` is never read by this check and never altered by it |
| Historical replay | The gate reads the run's pinned snapshot value, not a live lookup — a project `HIGH_RISK` at a historical run's cutoff replays `hierarchical_score = NULL` even if legal status has since changed, by the same snapshot-only-consumption rule as every other grain |

---

## 5. Snapshot and scoring integration

### 5.1 Where this runs relative to `run_ranking()` — corrected from the prior pass

The prior revision described the hierarchical step as living "alongside — not replacing" `run_ranking()`, reading `U` from an in-memory variable inside the same call. **Corrected, per §0.4's race-condition finding and `consultant.md §24.12.6`'s design:** the hierarchical step is a **separate, subsequent call**, invoked only after `run_ranking()` has fully returned (its own `try/except` in `service.py:386-406` has already committed `ranking_scores`/`ranking_runs` in `'completed'` status) — never inlined into that function's transaction, and reading `U` back via a fresh query keyed by `(unit_id, ranking_run_id)`, not a variable carried over from the same call frame.

**POST-PR-1/PR-3/PR-4 CORRECTION (§3.0):** this section's own name — `run_hierarchical_scoring_step()` — was never the shipped name; the entry point `run_ranking()` actually calls is `compute_hierarchical_scores_for_run(project_id, run_id, config_id, session_factory=...)`, and its call to the snapshot builder is `build_project_feature_snapshot_for_run()` (Project) — the pseudocode's `build_hierarchical_feature_snapshot()` name below does not exist. The control-flow SHAPE below (flag check, load config, validate, resolve cutoff, build snapshot, commit, score, commit, swallow-and-log on any exception) is accurate to what shipped; only the function/variable names are illustrative, not literal.

```python
# src/ranking/service.py -- NEW. Called by whatever already calls run_ranking()
# today (enqueue_ranking's worker loop, or a synchronous caller), immediately
# after it returns, feature-flagged.

async def run_hierarchical_scoring_step(
    ranking_run_id: uuid.UUID, project_id: uuid.UUID, session_factory, *, feature_flag: bool,
) -> None:
    if not feature_flag:
        return  # hierarchical_score/hierarchical_contributions stay NULL -- identical to before this function existed
    try:
        async with session_factory() as session:
            config = await _load_config_for_run(session, ranking_run_id)   # reads ranking_configs row via ranking_runs.config_version_id
            if config["hierarchical_weights"] is None:
                return  # this config version has no hierarchical companion -- valid no-op, not an error
            _validate_nested_config(config["hierarchical_weights"])        # §2.3.1 -- raises BEFORE any DB write below

            cutoff = await _run_cutoff(session, ranking_run_id)            # ranking_runs.started_at -- no separate cutoff param exists today (§5.1 note)
            snapshot_ids = await build_hierarchical_feature_snapshot(session, ranking_run_id, project_id, cutoff)
            await session.commit()

            await compute_hierarchical_scores(session, ranking_run_id, project_id, config["hierarchical_weights"], snapshot_ids, cutoff)
            await session.commit()
    except Exception:
        log.exception("hierarchical_scoring.failed", ranking_run_id=str(ranking_run_id))
        # Swallowed deliberately: a failure here must NEVER mark the
        # already-committed run_ranking() run as failed, and must never touch
        # ranking_scores.score/.rank_in_area/.rank_in_project/.contributions.
        # hierarchical_score/hierarchical_contributions simply stay NULL for
        # every unit in this run -- indistinguishable from the flag being off.
```

**No explicit `cutoff_at` parameter exists in `run_ranking()`'s signature today** [`service.py:313-320`, verified] — the legacy path computes "as of now" implicitly via `calculated_at`/`computed_at` [`service.py:388,393`]. This plan's `cutoff` is therefore derived from `ranking_runs.started_at` (existing column, already set at run start), not a new parameter threaded through the legacy function.

### 5.2 ~~`select_publishable_feature_values_at_cutoff()` and `build_hierarchical_feature_snapshot()`~~ `_select_eligible_project_justifications()` and `build_project_feature_snapshot_for_run()` (Project, PR-3, shipped) / `copy_published_market_assertions_to_run_snapshot()` (Market, PR-4)

**Corrected, §3.0:** the predicate below's `status = 'published' -- via the owning proposal, joined through lineage` line describes a lineage join that cannot exist yet — no `ranking_feature_values`/`ranking_feature_lineage` row exists for an assertion nobody has materialized before. The actual (shipped, Project; PR-4, Market) selection queries `ranking_weight_proposals`/`ranking_feature_justifications` directly (`status='published'` is the **proposal's** status, not anything reached through lineage), and only the copy step below creates the first `ranking_feature_values`/`ranking_feature_lineage` row. The per-grain function names are as shipped, not as originally named here; the shape of the predicate (effective/expiry/cutoff/supersession) is otherwise accurate and is what both PR-3 and PR-4 implement.

```python
# src/ranking/service.py -- NEW

async def select_publishable_feature_values_at_cutoff(session, project_id, scope_type, cutoff):
    """One row per (feature_definition_id, area_id) for scope_type in
    ('market','project','area'). Predicate, exact:

        status = 'published'                                   -- via the owning proposal, joined through lineage
        AND effective_at <= cutoff                              -- effective_at := ranking_feature_values.observed_at
        AND (expires_at IS NULL OR expires_at > cutoff)         -- computed, see below, never a stored column
        AND quality_status != 'blocked'
        AND published_at <= cutoff                              -- a value published AFTER cutoff did not exist at cutoff
        ORDER BY observed_at DESC LIMIT 1 per identity           -- most-recent-published wins = "supersession"

    expires_at is computed once, at selection time, as
    observed_at + (definition_metadata->>'max_shelf_life_days' days),
    defaulting to "never expires" when absent (every non-Market factor today).
    """
    ...  # SQL as specified in consultant.md §24.12.6, unchanged by this pass


async def build_hierarchical_feature_snapshot(session, ranking_run_id, project_id, cutoff):
    """Writes one ranking_feature_snapshots row per scope_type PRESENT
    (market/project/area -- only for scope_types that have >=1 selected value;
    an empty scope_type still gets a row so compute_hierarchical_scores() can
    tell 'ran the query, found nothing' apart from 'never ran the query'),
    keyed to the SAME ranking_run_id run_ranking() already created --
    uq_ranking_feature_snapshot_run_project_scope [0033:143-148] permits
    multiple scope_types under one run_id, one row each. Then N
    ranking_feature_values rows scoped to that snapshot_id -- a COPY, not a
    live view; once written, immutable (append-only trigger, 0033:354-378)."""
    for scope_type in ("market", "project", "area"):
        selected = await select_publishable_feature_values_at_cutoff(session, project_id, scope_type, cutoff)
        snapshot_id = await _insert_snapshot(session, ranking_run_id, project_id, scope_type, cutoff)
        for value in selected:
            await _copy_value_into_snapshot(session, snapshot_id, value)
    return snapshot_ids_by_scope  # dict, keyed by scope_type
```

### 5.3 `determine_grain_eligibility()`

```python
# src/ranking/service.py -- NEW. Pure function: snapshot row(s) in, (values dict, exclusion reasons) out.
# This IS D37's six-condition test, expressed as code -- the snapshot's
# SELECTION predicate (§5.2) already enforces conditions 1-3 and 6 (published,
# effective, non-expired, not-blocked) by construction, since only qualifying
# rows were ever copied into the snapshot. This function adds conditions 4-5
# (grain-level coverage sufficiency, evidence validity) which are properties
# of the SNAPSHOT CONTENTS, not the selection predicate.

def determine_grain_eligibility(grain: str, snapshot_values: dict, grain_weights: list[FeatureWeight],
                                 min_coverage: Decimal) -> tuple[dict | None, str | None]:
    """Returns (values_dict_or_None, exclusion_reason_or_None).
    Does NOT call engine.score_unit() itself -- that happens once, uniformly,
    in compute_hierarchical_scores() below, so this function's only job is
    the yes/no eligibility call plus the reason string."""
    if not snapshot_values:
        return None, "unpublished"          # nothing was ever selected into the snapshot for this grain
    # condition 5: evidence validity -- reuses validate_evidence() [advisory_tools.py:762-786]
    # unchanged; a value whose linked evidence fails entity/date validation is excluded here,
    # not silently scored
    if not _evidence_valid_for(snapshot_values):
        return None, "evidence_invalid"
    # condition 4: grain-level coverage -- computed by attempting the grain's
    # OWN engine.score_unit() call (§5.4's calls 1-3) and reading its
    # .coverage/.score; if that call returns score=None (coverage < grain's
    # own min_coverage, engine.py:110-121, UNCHANGED), this grain is excluded
    # with reason "coverage_below_threshold" -- determined by the CALLER
    # after invoking the per-grain score_unit(), not duplicated here.
    return snapshot_values, None
```

### 5.4 `compute_hierarchical_scores()` — the exact D37 pseudocode, replacing the stale branch

```python
# src/ranking/service.py -- NEW. engine.score_unit() [engine.py:69-134] is
# imported and called, NEVER edited. run_ranking() [service.py:313] is
# untouched; this function is invoked only by run_hierarchical_scoring_step()
# (§5.1), strictly after run_ranking() has committed.

async def compute_hierarchical_scores(session, ranking_run_id, project_id, hierarchical_weights, snapshot_ids, cutoff):
    market_weights  = parse_feature_weights(hierarchical_weights["market"])   # missing_value_policy as CONFIGURED (grain's own internal features)
    project_weights = parse_feature_weights(hierarchical_weights["project"])
    grain_weights   = [                                                       # D37: hard-coded 'skip' for M/P/A regardless of
        FeatureWeight(key="market",  weight=Decimal(str(hierarchical_weights["grain_weights"]["market"])),
                      direction="positive", missing_value_policy="skip"),     # what the config JSON says -- makes "zero forbidden
        FeatureWeight(key="project", weight=Decimal(str(hierarchical_weights["grain_weights"]["project"])),
                      direction="positive", missing_value_policy="skip"),     # for top-level terms" true by construction
        FeatureWeight(key="area",    weight=Decimal(str(hierarchical_weights["grain_weights"]["area"])),
                      direction="positive", missing_value_policy="skip"),
        FeatureWeight(key="unit",    weight=Decimal(str(hierarchical_weights["grain_weights"]["unit"])),
                      direction="positive", missing_value_policy="skip"),     # irrelevant in practice -- U is a precondition, never absent when this runs
    ]

    market_snapshot  = await _read_snapshot_values(session, snapshot_ids.get("market"), scope_type="market")
    market_values, market_reason = determine_grain_eligibility("market", market_snapshot, market_weights, hierarchical_weights["market_min_coverage"])
    M = engine.score_unit(
            engine.UnitFeatureInput(unit_id=str(project_id), area_id=str(project_id),
                                     tie_break_created_at=cutoff, values=market_values or {}),
            market_weights, hierarchical_weights["market_min_coverage"])
    if M.score is None and market_reason is None:
        market_reason = "coverage_below_threshold"   # snapshot had values, but this grain's OWN coverage gate rejected them

    project_snapshot = await _read_snapshot_values(session, snapshot_ids.get("project"), scope_type="project")
    project_values, project_reason = determine_grain_eligibility("project", project_snapshot, project_weights, hierarchical_weights["project_min_coverage"])
    P = engine.score_unit(
            engine.UnitFeatureInput(unit_id=str(project_id), area_id=str(project_id),
                                     tie_break_created_at=cutoff, values=project_values or {}),
            project_weights, hierarchical_weights["project_min_coverage"])
    if P.score is None and project_reason is None:
        project_reason = "coverage_below_threshold"

    legal_status = await _project_legal_status_from_snapshot(session, snapshot_ids.get("project"))  # categorical value, §4.4 -- 'unknown' if none published

    area_weights = parse_feature_weights(hierarchical_weights["area"])
    area_results: dict[str, tuple] = {}
    for area_id in _areas_of(project_id, session):
        area_crm    = _existing_area_features(area_id)                       # service.py:142-180, UNCHANGED call, reused verbatim
        area_expert_snapshot = await _read_snapshot_values(session, snapshot_ids.get("area"), scope_type="area", area_id=area_id)
        area_values = {**area_crm, **(area_expert_snapshot or {})}           # merge, no override -- §4.3
        area_reason = None if area_values else "unpublished"                # area_crm is never empty (2 CRM features always resolve) --
        A = engine.score_unit(                                              # this branch in practice never fires; kept for completeness
                engine.UnitFeatureInput(unit_id=str(area_id), area_id=str(area_id),
                                         tie_break_created_at=cutoff, values=area_values),
                area_weights, hierarchical_weights["area_min_coverage"])
        if A.score is None and area_reason is None:
            area_reason = "coverage_below_threshold"
        area_results[area_id] = (A, area_reason)

    U_by_unit = await _read_existing_unit_scores(session, ranking_run_id)     # fresh SELECT, keyed (ranking_run_id, unit_id) -- §0.4, NOT an
                                                                                # in-memory carry-over from run_ranking()'s own call frame

    for unit_id, U_row in U_by_unit.items():                                 # U_row is None if no ranking_scores row exists for this unit (§0.4)
        if U_row is None:
            continue                                                          # U missing -> no row to update -> effectively NULL, nothing written (§0.4)

        if legal_status == "HIGH_RISK":
            contributions = {"score_mode": None, "legal_gate": {"status": "HIGH_RISK", "gated": True}, "...": "§2.2's full shape, remaining fields null/empty"}
            hierarchical_score = None
        else:
            A, area_reason = area_results[area_of(unit_id)]
            F = engine.score_unit(
                    engine.UnitFeatureInput(unit_id=str(unit_id), area_id=str(area_of(unit_id)),
                                             tie_break_created_at=U_row.tie_break_created_at,
                                             values={"market": M.score, "project": P.score,
                                                     "area": A.score, "unit": U_row.score}),
                    grain_weights, hierarchical_weights["top_level_min_coverage"])
            hierarchical_score = F.score              # None only if coverage < top_level_min_coverage, which D37's config
                                                        # precondition (top_level_min_coverage <= grain_weights["unit"]) prevents
                                                        # from ever happening while U is present (§ consultant.md 24.4.6)
            contributions = serialize_hierarchical_contributions(F, M, market_reason, P, project_reason, A, area_reason,
                                                                   hierarchical_weights, legal_status, cutoff)

        rowcount = await _write_hierarchical_score(session, unit_id, ranking_run_id, hierarchical_score, contributions)
        if rowcount == 0:
            # §0.4's race: a NEWER run_ranking() for this project already
            # deleted-and-reinserted ranking_scores before this UPDATE landed.
            # Logged, not fatal to the rest of this loop -- other units in
            # this batch may still be current.
            log.warning("hierarchical_scoring.stale_target", unit_id=str(unit_id), ranking_run_id=str(ranking_run_id))
```

```python
# _write_hierarchical_score -- scoped by BOTH unit_id and ranking_run_id,
# per §0.4's race-condition finding, not by unit_id alone even though
# uq_ranking_scores_unit [0015:208] makes unit_id alone unique RIGHT NOW --
# scoping by both means a stale target is a 0-row no-op, never a cross-run write.
async def _write_hierarchical_score(session, unit_id, ranking_run_id, score, contributions) -> int:
    result = await session.execute(
        sa.update(ranking_scores)
        .where(ranking_scores.c.unit_id == unit_id, ranking_scores.c.ranking_run_id == ranking_run_id)
        .values(hierarchical_score=score, hierarchical_contributions=contributions)
    )
    return result.rowcount
```

### 5.5 `serialize_hierarchical_contributions()`

```python
# src/ranking/service.py -- NEW. Pure function: engine.py's own UnitScore
# fields (F.coverage, F.contributions -- ALREADY computed by score_unit(),
# NO new arithmetic) plus the three exclusion-reason strings already
# determined above -- relabelling, not recomputation.

def serialize_hierarchical_contributions(F, M, market_reason, P, project_reason, A, area_reason,
                                          hierarchical_weights, legal_status, cutoff) -> dict:
    eligible = [g for g in ("market", "project", "area") if F.contributions[g]["source"] == "resolved"]
    excluded = {g: {"reason": r} for g, r in (("market", market_reason), ("project", project_reason), ("area", area_reason)) if r is not None}
    if len(eligible) == 0:
        score_mode = "unit_only"
    elif len(eligible) == 3:
        score_mode = "full_hierarchical"
    else:
        score_mode = "partial_hierarchical"
    return {
        "score_mode": score_mode,
        "top_level_weight_coverage": str(F.coverage),
        "configured_grain_weights": {g: str(w) for g, w in hierarchical_weights["grain_weights"].items()},
        "effective_grain_weights": {g: str(hierarchical_weights["grain_weights"][g] / F.coverage) for g in eligible + ["unit"]},
        "eligible_grains": eligible,
        "excluded_grains": excluded,
        "grain_scores": {
            "market":  {"score": str(M.score) if M.score is not None else None, "coverage": str(M.coverage)},
            "project": {"score": str(P.score) if P.score is not None else None, "coverage": str(P.coverage)},
            "area":    {"score": str(A.score) if A.score is not None else None, "coverage": str(A.coverage)},
            "unit":    {"score": str(F.contributions["unit"]["value"])},
        },
        "legal_gate": {"status": legal_status if legal_status == "HIGH_RISK" else None, "gated": False},
        "cutoff_at": cutoff.isoformat(),
    }
```

### 5.6 Snapshot identity / config version / evidence revision for deterministic replay

A hierarchical result replays identically from `ranking_run_id` alone: `ranking_feature_snapshots` rows keyed to that `ranking_run_id` are immutable (§0's append-only trigger, unchanged) and pin `cutoff_at`; `ranking_scores.hierarchical_contributions.config_version_id` records which `ranking_configs.hierarchical_weights` produced the composition; `ranking_feature_lineage` resolves each contributing value back to the exact justification (and, transitively, the exact CEO approval, §3.4) that produced it. No new identity concept is introduced — this is the same "pin a run to a snapshot" guarantee `consultant.md §10.1` already establishes for the unit-grain path, extended to the four additional inputs.

---

## 6. API / read-output contract

`GET /ranking/hierarchical?external_project_id=` (PROPOSED, §3.5), extending `GET /ranking`'s existing response shape [`src/api/ranking.py:122-135`]:

```json
// 1. unit_only
{
  "unit_id": "...", "hierarchical_score": 0.7000, "score_mode": "unit_only",
  "top_level_weight_coverage": "0.4000",
  "configured_grain_weights": {"market": "0.10", "project": "0.25", "area": "0.25", "unit": "0.40"},
  "effective_grain_weights": {"unit": "1.000000"},
  "grain_statuses": {
    "market":  {"eligible": false, "reason": "unpublished"},
    "project": {"eligible": false, "reason": "unpublished"},
    "area":    {"eligible": false, "reason": "unpublished"},
    "unit":    {"eligible": true, "score": 0.7000}
  },
  "evidence": {},
  "cutoff_at": "2026-08-27T00:00:00Z",
  "snapshot_ids": {"market": null, "project": null, "area": null},
  "config_version_id": "uuid",
  "legal_result": {"status": null, "gated": false},
  "disclosure": "Unit-only hierarchical score — Market, Project, and Area context unavailable."
}

// 2. partial_hierarchical (Project + Unit eligible)
{
  "unit_id": "...", "hierarchical_score": 0.7385, "score_mode": "partial_hierarchical",
  "top_level_weight_coverage": "0.6500",
  "configured_grain_weights": {"market": "0.10", "project": "0.25", "area": "0.25", "unit": "0.40"},
  "effective_grain_weights": {"project": "0.384615", "unit": "0.615385"},
  "grain_statuses": {
    "market":  {"eligible": false, "reason": "unpublished"},
    "project": {"eligible": true, "score": 0.80, "feature_justification_id": "..."},
    "area":    {"eligible": false, "reason": "coverage_below_threshold"},
    "unit":    {"eligible": true, "score": 0.70}
  },
  "evidence": {"project": {"feature_justification_id": "...", "evidence_document_ids": ["..."]}},
  "cutoff_at": "2026-08-27T00:00:00Z",
  "snapshot_ids": {"market": null, "project": "uuid", "area": "uuid"},
  "config_version_id": "uuid",
  "legal_result": {"status": null, "gated": false},
  "disclosure": null
}

// 3. full_hierarchical
{
  "unit_id": "...", "hierarchical_score": 0.7325, "score_mode": "full_hierarchical",
  "top_level_weight_coverage": "1.0000",
  "configured_grain_weights": {"market": "0.10", "project": "0.25", "area": "0.25", "unit": "0.40"},
  "effective_grain_weights": {"market": "0.10", "project": "0.25", "area": "0.25", "unit": "0.40"},
  "grain_statuses": {
    "market":  {"eligible": true, "score": 0.70, "feature_justification_id": "..."},
    "project": {"eligible": true, "score": 0.65, "feature_justification_id": "..."},
    "area":    {"eligible": true, "score": 0.80, "feature_justification_id": "..."},
    "unit":    {"eligible": true, "score": 0.75}
  },
  "evidence": {"market": {"...": "..."}, "project": {"...": "..."}, "area": {"...": "..."}},
  "cutoff_at": "2026-08-27T00:00:00Z",
  "snapshot_ids": {"market": "uuid", "project": "uuid", "area": "uuid"},
  "config_version_id": "uuid",
  "legal_result": {"status": null, "gated": false},
  "disclosure": null
}

// 4. HIGH_RISK gated
{
  "unit_id": "...", "hierarchical_score": null, "score_mode": null,
  "top_level_weight_coverage": null,
  "configured_grain_weights": {"market": "0.10", "project": "0.25", "area": "0.25", "unit": "0.40"},
  "effective_grain_weights": {},
  "grain_statuses": {},
  "evidence": {},
  "cutoff_at": "2026-08-27T00:00:00Z",
  "snapshot_ids": {"market": null, "project": "uuid", "area": null},
  "config_version_id": "uuid",
  "legal_result": {"status": "HIGH_RISK", "gated": true},
  "disclosure": "Not ranked — project is under a HIGH_RISK legal gate."
}
```

`ranking_scores.score` (the existing CRM-only column) is retrieved separately, by the existing `GET /ranking` route, unaffected by any payload above.

### 6.1 POST-PR-7 CORRECTION — actual shipped API contract (2026-08-27)

**PR-7 IMPLEMENTED.** The sketch above (`GET /ranking/hierarchical` as a
~~separate, new~~ endpoint, `grain_statuses`/`legal_result`/`evidence`/
`snapshot_ids` field naming) was speculative and is **not** what shipped —
code wins over this older prose per the repo's own authority order. What
was actually built, verified against `src/api/ranking.py`/`src/models/
schemas.py`/`src/ranking/hierarchical_view.py`:

- **No new route.** `GET /ranking`'s existing response (`RankedUnitOut`,
  `src/models/schemas.py`) gained one new optional field per unit,
  `hierarchical: HierarchicalUnitOut | null` — extending a backward-compatible
  response was judged safer than a second endpoint the frontend would have
  to merge by `unit_id` itself, and the task instruction owning this PR
  explicitly preferred extension when safe. Old clients that don't know the
  new key are unaffected; nothing about `score`/`contributions`/`band`/etc.
  changed shape or meaning.
- **Field names follow the ACTUAL persisted `hierarchical_contributions`
  shape** (`src/ranking/service.py::_build_hierarchical_contributions()`/
  `_build_legal_gated_contributions()`, PR-1..PR-6), not this section's
  guesses: `grains` (not `grain_statuses`), `legal_gate` (not `legal_result`),
  `evidence_refs` nested per grain (not a top-level `evidence` map),
  `available`/`reason` (not a bare-`null` "not computed" state).
- **Two independent feature flags**, both in `src/config.py`, both default
  `False`: `hierarchical_ranking_enabled` (PR-1, gates whether the post-run
  COMPUTE step ever writes the two columns) and the new
  `hierarchical_read_enabled` (PR-7, gates whether `GET /ranking` DISCLOSES
  already-written data at all — the read-surface kill switch). Turning the
  read flag off makes every item's `hierarchical` field `null` instantly, no
  migration, no touch to `ranking_scores`.
- **Evidence/freshness**: `src/ranking/hierarchical_view.py` batch-reads
  (never per-unit) the immutable `ranking_feature_justifications.effective_at`/
  `.expires_at` and linked `ranking_evidence_documents` for every
  justification id a run's own snapshot already resolved — never a live
  re-selection, never data outside the requesting principal's already-scoped
  project.
- **No frontend feature flag was added.** `frontend/src/pages/RankingPage.jsx`'s
  new `HierarchicalPanel` renders purely reactively: `null` → nothing
  rendered, matching "legacy response/UI remains unchanged" without
  inventing a client-side flag mechanism (the codebase's only precedent,
  two single-purpose `import.meta.env.VITE_*` booleans, was judged
  unnecessary to extend here — the backend flag is the sole gate).
- **Observability**: `src/ranking/hierarchical_view.py::
  log_hierarchical_read_observability()` emits one structured log event
  (`ranking.hierarchical_read.completed`) per `GET /ranking` request with a
  persisted hierarchical read — `score_mode` counts, unavailable/legal_gated
  counts, excluded-grain reason counts, comparability-warning count,
  evidence available/unavailable counts, coverage value distribution, and
  latency. No metrics backend exists in this repo (verified: no
  Prometheus/statsd/OTel dependency) — structured logs are the entire
  observability surface, consistent with every other module's convention.
- **Tests**: `tests/test_api/test_ranking_hierarchical.py` (14 tests: mode
  semantics, authorization, backward compatibility, evidence/freshness,
  malformed-row degradation) and 6 new cases in
  `frontend/src/pages/RankingPage.test.jsx` (badge/disclosure per mode,
  legal-gated no-score/no-band state, comparability warning, flag-off
  hides the panel).

---

## 7. Test plan (pytest-style names, not implemented)

**Retained, unmodified from the prior pass** (still valid, D37 does not affect them): `test_t2_no_cross_grain_writes`, `test_t5_cross_project_area_isolation`, `test_t6_deterministic_tie_breaking`, `test_t9_malformed_nested_config_rejected_before_scoring` (adjusted target: `_validate_nested_config`, §2.3.1), `test_existing_unit_ranking_byte_identical`.

**Updated** (stale all-or-nothing assumption removed): `test_t4_missing_parent_yields_none_not_zero` → now asserts a missing/excluded parent is dropped from `F`'s numerator **and** denominator (never a `0` term), **not** that the whole `hierarchical_score` becomes `NULL` — that outcome is now `test_t4b_unit_only_fallback` below.

```python
# --- Migration / constraint / FK ---
def test_migration_0037_adds_nullable_hierarchical_score_with_range_check(): ...
def test_migration_0039_downgrade_refuses_if_area_or_market_rows_exist(): ...       # §2.9's guard
def test_migration_0042_existing_weight_mode_rows_unaffected_by_new_columns(): ...  # every pre-existing row: assertion_kind='weight', 5 new cols NULL
def test_ck_rfj_assertion_mode_xor_rejects_both_weight_and_value_populated(): ...
def test_ck_rfj_market_citation_required_blocks_market_scope_without_citation(): ...

# --- Legacy regression ---
def test_existing_unit_ranking_byte_identical():
    """run_ranking() with and without run_hierarchical_scoring_step() enabled,
    same fixture: ranking_scores.score/.rank_in_area/.rank_in_project/.contributions
    BYTE IDENTICAL between the two runs, for every unit."""

# --- D37 composition ---
def test_unit_only_fallback_equals_u_exactly():
    """No parent grain eligible -> hierarchical_score == U to full NUMERIC(6,4)
    precision; score_mode == 'unit_only'; top_level_weight_coverage ==
    grain_weights['unit'] exactly; all three excluded_grains present with a
    specific (non-generic) reason."""

def test_partial_composition_decimal_arithmetic_exact():
    """P=0.80, U=0.70, weights 0.25/0.40: hierarchical_score's PRE-ROUNDING
    value equals Decimal('0.48')/Decimal('0.65') exactly; the PERSISTED value
    equals Decimal('0.7385') after engine.py:123's ROUND_HALF_UP quantize."""

def test_configured_weights_immutable_after_renormalization():
    """ranking_configs.hierarchical_weights for this config_version_id is
    byte-identical before and after a run that computed effective_grain_weights
    -- no UPDATE statement targets this column outside create_draft/publish."""

def test_no_missing_grain_becomes_zero():
    """For every excluded grain in a partial/unit_only result, assert the
    grain's key is ABSENT from F's resolved contributions (source ==
    'missing_skipped'), never present with contribution == '0' as if it had
    been scored at the floor."""

def test_full_vs_partial_disclosure():
    """score_mode == 'full_hierarchical' ONLY when eligible_grains == {'market',
    'project', 'area'}; a fixture with exactly 2 eligible parents MUST NOT
    produce 'full_hierarchical' under any config. API payload always carries
    score_mode/top_level_weight_coverage/effective_grain_weights/excluded_grains
    regardless of mode."""

def test_stale_expired_unpublished_rejected_conflicted_withdrawn_all_excluded_never_scored():
    """Six fixtures, one per exclusion reason (unpublished, expired,
    evidence_invalid, conflicted -- two published values for one identity,
    resolved by most-recent-wins -- withdrawn, coverage_below_threshold):
    each produces the grain excluded with that EXACT reason string, never a
    generic 'missing', never a numeric contribution."""

def test_scope_entity_geography_date_evidence_validation():
    """A value whose scope_type disagrees with its definition's grain is
    rejected before write (materializer-level check, §3.3); a chunk whose
    issued_at is after cutoff is rejected by validate_evidence() [unchanged,
    advisory_tools.py:762-786] before it can back a justification's evidence
    set; geography match is NOT FOUND (no geography column exists anywhere in
    this schema) -- asserted as a documented gap, not silently passed."""

def test_same_eligibility_set_ordering_invariance():
    """Two units, same project, same area (same M/P/A eligibility by
    construction): varying ONLY U reorders them; varying ONLY M or P (both
    still eligible/ineligible identically for both units) reorders NEITHER --
    extends T3 to the partial-composition case."""

def test_unequal_eligibility_set_comparability_warning():
    """Two units in DIFFERENT projects with unequal top_level_weight_coverage
    (one has a published Market value, the other does not): assert the
    comparison-view API response carries a comparability warning; explicitly
    do NOT assert their relative order is invariant to anything -- asserting
    invariance across unequal eligibility sets would itself be the bug."""

# --- D38 governance ---
def test_ceo_only_approval():
    """submit_review() on a value-mode proposal succeeds only when
    principal.is_ceo is True; a non-CEO admin-role caller is rejected with
    CEO_APPROVAL_REQUIRED. Weight-mode proposals: unaffected, any admin-role
    caller succeeds exactly as today."""

def test_self_approval_rejected():
    """reviewer_expert_id (resolved server-side from principal.subject, NOT
    the request body) equal to the proposal's created_by_expert_id ->
    SELF_APPROVAL_FORBIDDEN, for value-mode proposals. Weight-mode: no such
    check exists or is added -- explicitly asserted as an open, pre-existing,
    unaddressed gap for that mode."""

def test_system_publisher_cannot_publish_without_ceo_approved_immutable_evidence_revision():
    """Calling materialize_published_feature_value() directly (bypassing the
    API) against a proposal whose approving review was made by a non-CEO
    principal -- constructed by manipulating test fixtures, not achievable
    through the API at all -- is rejected by the materializer's own
    re-verification (§3.3), not merely by the route-level gate."""

def test_llm_numeric_authority_prohibition():
    """retrieve_and_validate()/generate_justification_explanation()
    [advisory_tools.py:789,863, UNCHANGED] contain no session.execute/
    insert/update of any kind (static analysis of the function bodies, not
    just a runtime assertion) and remain excluded from ALLOWED_ADVISORY_TOOLS
    [:742-748]."""

def test_dev_bypass_and_static_token_modes_cannot_satisfy_is_ceo():
    """principal.is_ceo is False for every DashboardPrincipal constructed via
    the dev-bypass or static-token branches [dashboard_auth.py:129-130,157-163]
    -- CEO-gated value-mode approval is structurally unavailable in those
    modes, asserted directly, not merely undocumented."""

# --- Legal / snapshot / bypass ---
def test_legal_high_risk_gate():
    """project_legal_status == 'HIGH_RISK' (from the snapshot, not a live
    query) -> hierarchical_score IS NULL for every unit in that project,
    hierarchical_contributions.legal_gate.gated == True, ranking_scores.score
    for the SAME units UNCHANGED."""

def test_snapshot_replay_reproduces_hierarchical_score():
    """Given only a ranking_run_id, recomputing from ranking_feature_snapshots
    (pinned by cutoff_at) reproduces the same M/P/A/hierarchical_score/
    hierarchical_contributions as the original run, even if the underlying
    values have since been superseded or expired."""

def test_hierarchical_update_targets_stale_run_is_a_logged_noop_not_a_crash():
    """A second run_ranking() for the same project completes (delete+reinsert
    ranking_scores) between run_hierarchical_scoring_step()'s value
    computation and its UPDATE -- rowcount == 0, logged, loop continues for
    other units, no exception propagates (§5.4/§0.4)."""

def test_no_direct_writer_or_bypass_path():
    """Boundary test, mirroring tests/test_ranking_boundary.py's existing
    pattern: ranking_feature_values/_snapshots/_lineage's writer set is
    EXACTLY {src/services/governance.py, src/ranking/service.py} (materializer
    + snapshot builder respectively) -- any other module writing these tables
    fails this test, the same mechanism that already protects the four
    original ranking tables and the seven governance tables."""
```

---

## 8. Delivery plan — PR sequence

Each PR is independently shippable and independently reversible. No PR before PR-1 exists; no PR depends on a PR later in this list.

| PR | Scope | Files/layers (verified) | Acceptance | Rollback | Decision deps | `score_mode` newly possible |
|---|---|---|---|---|---|---|
| **PR-1** | Parallel hierarchical output: migrations `0037`+`0040` (`hierarchical_score`+`hierarchical_contributions`), `0041` (`ranking_configs.hierarchical_weights`), `_validate_nested_config()`, `run_hierarchical_scoring_step()`/`compute_hierarchical_scores()` reading an always-empty snapshot set (no governance extension yet), feature-flagged | `alembic/versions/`, `src/ranking/service.py`, `tests/test_services/test_ranking_service.py` (new hierarchical tests), `tests/test_ranking_boundary.py` (column-set assertion only) | `test_existing_unit_ranking_byte_identical` passes; every unit gets `hierarchical_score = U`, `score_mode = "unit_only"` — never `NULL` unless `U` itself is absent | Disable feature flag; drop the 3 new columns independently | None | **`unit_only`, for every unit, immediately** |
| **PR-2** | Value-assertion governance extension: migration `0042`, `upsert_justification()`/`submit_review()`/`mark_published()` EXTEND, auth-discovery gate (§2.7: `DashboardPrincipal`/`issue_session`/`authenticate_dashboard` changes), `require_ceo` | `src/services/governance.py`, `src/services/dashboard_auth.py`, `src/services/oidc.py`, `src/api/governance.py`, `alembic/versions/`, `tests/test_services/test_governance.py` | A value-mode proposal can reach `approved` only via a `principal.is_ceo`-true, non-self reviewer; weight-mode proposal tests unaffected, run unmodified | Disable value-mode routes (leave migration in place — additive, unused) | D38 (this PR **is** its implementation) | No change yet — governance exists, nothing publishes to a grain the scorer reads |
| **PR-3** | Feature-value publication writer + snapshot builder + **Project grain end-to-end** | ~~`governance.py::materialize_published_feature_value()`, `service.py::select_publishable_feature_values_at_cutoff/build_hierarchical_feature_snapshot`~~ (corrected, §3.0, as shipped) `governance.py::mark_published()` EXTEND (status-flip + re-verify only), `src/ranking/service.py::materialize_published_feature_value()`/`build_project_feature_snapshot_for_run()` (the actual writers), `tests/test_ranking_boundary.py` (new writer declarations) | An expert-published, CEO-approved `expert_location_score` is visible in a project's `P` and (once snapshotted) in `hierarchical_score`/`partial_hierarchical` for that project's units | Disable the feature flag on materialization; already-published rows are simply never selected | D37 (implemented), D38 (implemented) | **`partial_hierarchical`, first time — Project only** |
| **PR-4** | Market grain: `0034`-side scope widening already in `0042` (PR-2); Market-specific `external_source_citation`/shelf-life data-seed, denormalized per-project publish flow | `alembic/versions/` (data-seed migration for `definition_metadata`), `governance.py` (citation validation), UI mode toggle (not built here — API only) | A market assertion published for project X excluded from selection once `expires_at` passes, per-project CEO approval enforced | Disable Market-scope publishing route-level; already-published rows simply expire | D39 (`PENDING` — this PR ships against the denormalized default, flagged not silently assumed) | `partial_hierarchical` gains a second reachable parent (Market) |
| **PR-5** | Area expert grain + CRM/expert merge | `service.py` (`{**area_crm_values, **area_expert_values}`), `governance.py` (area-scope widening already in `0042`) | An area with only CRM features still scores as today; adding expert features never lowers coverage | Disable area-scope publishing; CRM-only behavior is exactly today's `_area_features()` output | None new | **`full_hierarchical` becomes reachable** (once a project has CEO-approved M+P+A all eligible) |
| **PR-6** | Legal facts + `HIGH_RISK` enforcement | `service.py` (gate check before call 5, §5.4), `governance.py` (categorical value-mode reuse, zero new schema per §2.8), vocabulary data-seed | A `HIGH_RISK`-published project has `hierarchical_score IS NULL` for every unit; `ranking_scores.score` unchanged for the same units | Disable the gate check — every project scores as if `legal_status='unknown'` (today's universal state) | D40 (`PENDING` — vocabulary), D38 (implemented, PR-2) | No new `score_mode` — this PR only adds a `NULL`-producing gate |
| **PR-7** | Read API/UI, observability, rollout controls | `src/api/ranking.py` (`GET /ranking/hierarchical`, `POST /ranking/hierarchical/preview`, `GET /governance/feature-values`), frontend consumption, `score_mode`/coverage-disparity dashboards | A sales user sees `hierarchical_score`, its `score_mode`, band, and drills into evidence per contributing grain; unequal-coverage comparisons show the warning (T-unequal, §7) | Frontend reverts to `ranking_scores.score` only; no backend change needed | Depends on whichever of PR-3–PR-6 shipped | Surfaces what PR-1–PR-6 already compute; no new mode |

---

## 9. Remaining blocking/PENDING decisions (unchanged by this pass, restated for the coding handoff)

| Decision | Status | What it blocks |
|---|---|---|
| D26 | `PENDING` | Freshness thresholds by factor type beyond Market's already-decided 30/90-day figures |
| D28 | `PENDING` | Broader per-grain ownership/evidence-standard question beyond what D38 (approver) and D36 (Market source) already answered |
| D30 | `PENDING` | Whether `area_market_score` is built at all before geography exists |
| D31 | `PENDING` | Nested UI drill-down view over the flat storage — display-layer only, no effect on this plan |
| D32 | `PENDING` | A `MEDIUM_RISK` cap tier alongside the `HIGH_RISK` gate |
| D34 | `PENDING` | Whether `min_weight_coverage` needs a per-grain variant — this plan uses four distinct config-carried values (`market_min_coverage` etc., §2.3) as a concrete, reversible choice; resolving D34 to "one shared value" changes only the config-reading lines in §5.4, not the call shape |
| D35 | `PENDING` | Whether "`zero` forbidden for top-level terms" needs enforcement beyond this plan's hard-coded `missing_value_policy='skip'` in `grain_weights` (§5.4) — arguably already closed by construction, but the owner decision itself remains unrecorded |
| D39 | `PENDING` | Whether Market ships denormalized-per-project (this plan's default, §2.5/§4.1) or a market-context entity is built first |
| D40 | `PENDING` | `legal_status` vocabulary beyond `HIGH_RISK`, and its review/expiry cadence |
| **New, opened by this pass:** cross-document inconsistency (§0.3) | Not a D-numbered decision — a verified code contradiction, not a business judgment call | `ranking_consultant.md §24.7`'s D22 storage-location statement ("nested weights in `ranking_configs.weights`") is incompatible with `_active_config()`/`validate_weights()` as this pass verified; this plan corrects the storage location (§2.3) but does not edit `ranking_consultant.md` (out of scope). A future pass should reconcile the two documents |

---

## 10. Claude coding handoff — PR sequence and safety gates

1. **Before writing any code**, confirm: alembic head is still `0036_remove_historical_ranking` (re-run the check in the Appendix); `tests/test_ranking_boundary.py`'s `ALLOWED_WRITERS`/`GOVERNANCE_TABLES`/`EVIDENCE_CHUNK_ALLOWED_WRITERS` dicts still match §"Appendix" below (a drift means this plan's line citations are stale and must be re-verified before proceeding, not patched around).
2. **PR-1 gate:** `test_existing_unit_ranking_byte_identical` must be written and passing **before** `run_hierarchical_scoring_step()` is wired into any caller of `run_ranking()` — write the test against the flag turned off first, then turned on, and diff.
3. **PR-1 gate:** no PR may modify `engine.py`, `_active_config()`, `validate_weights()`, or any existing column/CHECK on `ranking_scores`/`ranking_configs`/`ranking_runs`. A diff touching any of those four is a stop-and-reread-this-plan signal, not a judgment call to make silently.
4. **PR-2 gate:** the auth-discovery gate (§2.7) ships **before** any value-mode route is exposed publicly — a value-mode proposal must be unreachable (feature-flagged off, or routes unregistered) until `principal.is_ceo`/`principal.subject` exist and are tested (T-CEO-3).
5. **PR-2 gate:** `test_migration_0042_existing_weight_mode_rows_unaffected_by_new_columns` must pass before `0042` merges — this is the single test proving "preserve weight-proposal behavior exactly" wasn't merely asserted.
6. **PR-3 gate:** the per-run snapshot materializer (`src/ranking/service.py::materialize_published_feature_value()`, as shipped — see §3.0's correction) must re-verify CEO approval itself (§3.3) — a code review that finds this function trusting the route-level gate alone (no independent re-check) is a correctness finding, not a style note; block the PR. Same gate applies to PR-4's `copy_published_market_assertions_to_run_snapshot()`.
7. **Every PR from PR-3 onward:** `test_no_direct_writer_or_bypass_path` (§7) must be extended to cover that PR's new writer before merge, mirroring exactly how `tests/test_ranking_boundary.py` already enforces this for every existing table.
8. **Before PR-7 (frontend):** confirm `score_mode`/`top_level_weight_coverage`/`excluded_grains` are non-null on every response where `score_mode != "full_hierarchical"` — a frontend PR that renders a bare number with no disclosure is a regression against this plan's §1/§6, not a UI nitpick.
9. **At every PR:** if a discovered fact contradicts something asserted `APPROVED` in `ranking_consultant.md` (as §0.3 already found once), stop, do not silently code around it, and record the contradiction the way §0.3/§9 do here — cite file:line, state which document's claim was wrong, and let the correction stand until an owner resolves it.

---

## Appendix: file:line index

| Claim | Evidence |
|---|---|
| `score_unit` signature, purity, coverage gate, skip semantics | `src/ranking/engine.py:69-134` (skip: `:78-92`; sums: `:99-101`; coverage gate: `:110-121`; rounding: `:123`) |
| `ranking_scores` columns, `NUMERIC(6,4) NOT NULL` score, no `hierarchical_score`/`hierarchical_contributions` today | `src/models/tables.py:591-607` |
| `ranking_scores` delete-then-insert, skipped units never inserted | `src/ranking/service.py:510-554`, esp. `:530,552` |
| `uq_ranking_scores_unit` — global unique index on `unit_id` | `alembic/versions/0015_ranking_results.py:208` |
| `run_ranking()` structure, no explicit cutoff param, commit points | `src/ranking/service.py:313-441` |
| `_area_features`, `_build_feature_inputs`, `_active_config`, `_persist_scores` | `service.py:142-180`, `:217-254`, `:107-121`, `:510-554` |
| `ranking_configs` columns incl. single `min_weight_coverage` | `src/models/tables.py:544-559` |
| `validate_weights`, `KNOWN_FEATURES`, `WEIGHT_SUM_TOLERANCE`, `create_draft`/`publish` call it | `src/services/ranking_config.py:56,60,70-108,139-174,181-233` |
| `ck_rfd_grain` current values | `alembic/versions/0033_ranking_evidence_foundation.py:60-62` |
| `ck_rfv_scope_type_project`/`ck_rfv_project_scope_shape`, composite FK | `0033:222-223`, FK `:197-204`, target unique `:150-154` |
| `ck_rfs_scope_type_project`/`ck_rfs_project_scope_no_area` | `0033:155-156` |
| `ranking_feature_lineage` has no grain CHECK | `0033:261-292` |
| `0033`'s append-only guard function + trigger loop + downgrade | `0033:354-378` (create), `:382-405` (downgrade, refuse-if-rows precedent `:26-28`) |
| `ranking_weight_proposals` columns, `ck_rwp_scope_type_project`/`ck_rwp_project_scope_no_area`/`ck_rwp_published_requires_approved` | `0034:56-104` |
| `ranking_feature_justifications` columns, `proposed_weight NUMERIC(12,8) NOT NULL` | `0034:112-164` |
| `ranking_proposal_reviews` columns; append-only guard scope (only reviews + audit events) | `0034:238-268`, `:271-347` |
| `submit_review()` — no self-approval check, either mode, verified | `governance.py:672-753` |
| `upsert_justification()` | `governance.py:382-497` |
| `mark_published()` | `governance.py:827-863` |
| `_JUSTIFICATION_EDITABLE_STATUSES`, `_REVIEWABLE_STATUSES`, `PROPOSAL_STATUSES` | `governance.py:67-85` |
| Four evidence/explanation functions, keyed by `feature_justification_id`, no writes | `advisory_tools.py:749,762,789,863` |
| `ALLOWED_ADVISORY_TOOLS` excludes the four evidence functions | `advisory_tools.py:32,742-748` |
| Governance endpoint table, identity model ("no per-person identifier"), known gaps | `docs/ranking/governance_api.md:18-28,32-49,96-109` |
| `DashboardPrincipal` — only `role`+`project_scope` today | `src/services/dashboard_auth.py:61-65` |
| `authenticate_dashboard()` — OIDC branch discards `identity.subject`/`.roles`; static-token/dev-bypass branches | `dashboard_auth.py:107-165` (OIDC ~`:148-156`) |
| `OidcIdentity` — `subject`/`email`/`roles: frozenset[str]` already resolved | `src/services/oidc.py:78-84`, `verify_token()` `:347-391` |
| `CANONICAL_APP_ROLES`, `"CRM.CEO": "admin"`, `resolve_role()` collapses to 3-tier | `oidc.py:412-448` |
| Keycloak realm role `CRM.CEO`, comment naming a nonexistent file | `docker/keycloak/p100-realm.json:33-36` |
| `src/services/entra_auth.py` referenced by the realm comment does not exist | Verified via repository search — `NOT FOUND` |
| `issue_session()`/`_decode_session()` — session JWT carries `sub`/`email`, not raw `roles` | `oidc.py:458-497` |
| `issue_session()` called live from login/refresh | `src/api/auth.py:127,204` |
| `GET /ranking` existing response shape | `src/api/ranking.py:122-135` |
| Design authority for grain/formula/gate/composition/governance decisions | `docs/ranking/ranking_consultant.md` §24.2–§24.12, D22–D40 |

---

## Validation report

- **Section/heading count:** 11 top-level (`##`) sections (`0`–`10`) plus the Appendix and this report — every section from the task's required list (Executive status, Schema/migration, Governance/writer, Grain contracts, Snapshot/scoring, API contract, Tests, Delivery plan) is present, plus `§0` (corrections), `§9` (PENDING decisions), `§10` (coding handoff).
- **Code-fence count and balance:** 19 fenced blocks (SQL/Python/JSON/text) — 38 `` ``` `` markers total, every one opened with a language tag and closed, verified by manual line-by-line inspection of the full file (the Bash tool was unavailable for a scripted check during this pass, see note below) — even count, no unterminated fence.
- **Table integrity:** every table's data rows visually match its header's column count on inspection; an automated pipe-count check could not be run this pass (Bash tool unavailable, see note below) and should be re-run before this document is relied on for exact column-parity guarantees.
- **Stale all-or-nothing phrases found and resolved:** three, all in §0.5 — the literal `elif M.score is None or ... hierarchical_score = None` branch, the "`NULL` for every unit, permanently" sentence, and the implicit all-four-required framing threaded through the prior revision's §2/§3/§6 — struck through in place (not deleted) and replaced with D37's five-branch outcome table.
- **No code, migration, schema, route, or config file changed.** This pass wrote to exactly one file: `docs/ranking/hierarchical_scoring_implementation_plan.md`. `docs/ranking/ranking_consultant.md` was read for design authority and not edited, per this task's explicit scope.
- **Note on this validation pass:** the repository's Bash tool ran out of scratch-filesystem space partway through this task (an environment issue, unrelated to this document's content) after the file was written, blocking the scripted fence-count/table-parity checks used in prior passes on this document. Structural verification here was done by reading the full file back and checking it by hand (every fence open/close pair, every table header) rather than by script — re-run the scripted checks once the environment recovers, per this document's own §10 coding-handoff item 1 discipline (verify, don't assume).

### Post-PR-5 release-hardening pass (test-infrastructure only, no scoring/schema-domain change)

A narrow reliability pass after PR-5 shipped, scoped to test fixtures/isolation, migration certification, and preflight — not to Legal (PR-6), UI/API (PR-7), or any ranking/governance behavior.

- **Root cause found and fixed:** `tests/test_services/test_import_records.py`'s own `clean_db` fixture ran a narrow, per-table `DELETE FROM` list (six tables specific to the CSV-import domain) with no knowledge of `units`/`deals`/ranking tables. `DELETE FROM areas` there is table-wide, not scoped to that file's own project — so a single leftover `units` row from ANY other suite (or from a prior pytest process killed mid-run before its own teardown ran — confirmed trigger: a host disk-full incident during this same hardening pass crashed the `db` container) raised `ForeignKeyViolationError` on `fk_units_area_id`, failing a file unrelated to the actual cause. Fixed by routing `clean_db` through the same `tests/conftest.py::truncate_tables()` + `TRUNCATE ... RESTART IDENTITY CASCADE` mechanism `truncate_all` already uses for every other suite — `CASCADE` empties any FK-dependent table transitively whether or not it is named explicitly. Proven both ways (fails on the old fixture, passes on the fixed one) by `tests/test_services/test_import_records_fixture_isolation.py`, which deliberately leaves orphaned `units`/`areas`/`projects` rows and re-runs the file against them.
- **Canonical release-validation command** (replaces ad hoc `-p no:logging` diagnostic invocations — that flag disables pytest's logging plugin, which also removes the `caplog` fixture, spuriously failing `test_duplicate_identity_inside_one_batch_is_rejected`; CI's own command already omits it):
  ```
  bash scripts/test_db.sh -q tests/test_migrations/ tests/test_ranking_boundary.py \
    tests/test_services/test_governance.py tests/test_services/test_governance_value_mode.py \
    tests/test_services/test_governance_pr2_boundaries.py tests/test_ranking/ \
    tests/test_api/test_ranking_endpoint.py tests/test_agent_e2e.py tests/test_agents/ \
    tests/auth/ tests/test_services/test_import_records_fixture_isolation.py
  ```
  Result at the time of this pass: **698 passed, 1 skipped, 0 failed, 0 errors.** The one remaining, unrelated pre-existing failure this canonical command used to carry (`tests/auth/test_config_safety.py::test_default_app_env_with_bypass_true_is_rejected`, an ambient `APP_ENV=development` process-env leak from `.env` into a test that only isolates against the `.env` FILE, not process env vars) was also fixed in this pass — see the test's own updated docstring.
- **Preflight added:** `scripts/preflight_test_env.sh` — read-only, non-destructive; checks host disk free space (configurable `MIN_FREE_DISK_MB`, default 2048), reports Docker disk usage (never prunes), and checks Postgres/Redis service health, failing fast with no test/migration started if disk is short. Wired into `scripts/test_db.sh` as an early gate.
- **Migration certification (fresh Postgres, this pass):** `alembic upgrade head` from nothing succeeds; exactly one head (`0041_area_grain_scope`); guarded downgrade to `0036_remove_historical_ranking` (the pre-PR-1 baseline) succeeds with every PR-1..5 protected-data guard exercised as a no-op; re-upgrade reaches the identical head with no duplicate Market/Area feature-definition seeds. The pre-existing, out-of-scope `0025_synthetic_unit_labels -> 0024_rename_synthetic_labels_vinhomes_stats` downgrade bug (`ck_units_updated_after_created` CHECK violation inside `0024`'s own downgrade, re-confirmed with a fresh scratch database during this pass) remains unfixed — it predates PR-1 by twelve revisions and does not block the PR-1→PR-5 upgrade/guarded-downgrade/re-upgrade path certified above.

### Post-PR-6 release certification (validation-only pass, 2026-08-27)

A read-only/diagnostic-only certification pass for PR-1 through PR-6 (Legal), confirming the repository is safe to begin PR-7. No ranking, governance, migration, schema, or API file was touched by this pass; the only file touched is this one, to record the result.

- **Environment:** `bash scripts/preflight_test_env.sh` → all checks passed (host disk 29499MB free, 48% used, threshold 2048MB). Postgres (`db`), Redis (`redis`), Keycloak (`keycloak`, realm `p100` imported successfully — `CRM.CEO` role confirmed present at `docker/keycloak/p100-realm.json:34,96`), and `minicrm_db` all healthy (`docker ps`). An earlier attempt this same day was correctly **blocked** by preflight at 1238-1284MB free (below the 2048MB threshold, following an unrelated external `docker compose down -v` event the prior turn) — no test/migration was run during that blocked window; the owner subsequently freed host disk, and this pass re-ran preflight clean before proceeding.
- **Alembic:** exactly one head, `0042_legal_assertion_gate` — confirmed via `python -m alembic heads`.
- **Fresh migration certification:**
  ```
  bash scripts/test_db.sh -q \
    tests/test_migrations/test_pr1_pr4_integration_hardening.py \
    tests/test_migrations/test_0041_area_grain_scope.py \
    tests/test_migrations/test_0042_legal_assertion_gate.py
  ```
  Result: **68 passed, 0 failed** (300.82s pytest runtime). Proves, on a freshly created `absorption_test` database: clean upgrade from nothing through `0042`; exactly one head; guarded downgrade from `0042` to the pre-PR-1 baseline `0036_remove_historical_ranking` with every PR-1..6 protected-data guard a no-op; re-upgrade to the identical head with Market (4), Area (3), and Legal (1) feature-definition seeds each re-created exactly once, no duplicates.
- **Canonical PR-1→PR-6 regression suite** (exact command, no `-p no:logging`):
  ```
  bash scripts/test_db.sh -q \
    tests/test_migrations/ tests/test_ranking_boundary.py \
    tests/test_services/test_governance.py tests/test_services/test_governance_value_mode.py \
    tests/test_services/test_governance_pr2_boundaries.py tests/test_ranking/ \
    tests/test_api/test_ranking_endpoint.py tests/test_agent_e2e.py tests/test_agents/ \
    tests/auth/ tests/test_services/test_import_records_fixture_isolation.py
  ```
  Result: **736 passed, 1 failed, 0 errors** (1250.33s / 20:50 runtime), run uninterrupted (`docker events` for the full run window returned no container/volume lifecycle events; host disk unchanged at 29GB free before and after). The one failure, `tests/test_migrations/test_0031_unit_inventory_daily.py::test_the_core_projection_matches_the_migrated_columns`, is pre-existing, unrelated baseline debt — see below — and is not a PR-1→PR-6 regression.
- **Migration `0031` maintenance debt, classified STALE TEST:** `alembic/versions/0031_unit_inventory_daily.py` created table `unit_inventory_daily` (P1, dashboard-era materialization). `alembic/versions/0036_remove_historical_ranking.py:38` (`op.drop_table(TABLE)`, `TABLE = "unit_inventory_daily"` at line 29) intentionally dropped it six revisions before PR-1 begins, and `src/models/tables.py` was updated to no longer declare it. `tests/test_migrations/test_0031_unit_inventory_daily.py`'s own fixture correctly upgrades only to `0031` on a scratch database (the table genuinely exists there), but `test_the_core_projection_matches_the_migrated_columns` (line 152) does `from src.models.tables import unit_inventory_daily` — importing the CURRENT (head-state) Python declaration, which `0036` already removed — an `ImportError` unrelated to schema state, reproduced identically in isolation (`bash scripts/test_db.sh -q tests/test_migrations/test_0031_unit_inventory_daily.py` → 1 failed, 45 passed) and inside the full canonical sweep. All 45 other tests in that same file pass, since they assert against the scratch DB's raw schema directly rather than importing the head-state Core table object. Not fixed in this pass (out of scope: predates PR-1, and this pass may only record debt, not correct migrations/tests). **Maintenance action for a future narrowly-scoped pass:** delete or rewrite `test_the_core_projection_matches_the_migrated_columns` to stop importing a Core table object that no longer exists at head, since `0036` retired it.
- **Verdict: READY FOR PR-7.** All required gates (disk/service preflight, single correct Alembic head, fresh migration certification, canonical suite at 0 failed/0 errors net of the one documented pre-existing `0031` staleness) are satisfied on a clean, uninterrupted run.

### `0031` stale-test maintenance fix (2026-08-27)

- `tests/test_migrations/test_0031_unit_inventory_daily.py::test_the_core_projection_matches_the_migrated_columns` was corrected because it asserted a current-head Core mapping (`from src.models.tables import unit_inventory_daily`) for a table intentionally removed by `0036_remove_historical_ranking.py:38` (`op.drop_table(TABLE)`, `TABLE = "unit_inventory_daily"` at line 29). The test now asserts the historically-correct shape hardcoded from `0031_unit_inventory_daily.py`'s own `create_table()` call (lines 60-78, every column `nullable=False`) against the scratch database at the `0031` migration state, and no longer imports any current-head Core declaration. Renamed to `test_the_migrated_columns_match_0031s_own_create_table`.
- No production migration, schema, model, or ranking behavior changed. Only `tests/test_migrations/test_0031_unit_inventory_daily.py` was edited.
- The canonical PR-1→PR-6 command was rerun with **0 failed / 0 errors** (`737 passed, 1228.66s / 0:20:28`, uninterrupted — no `docker events` in the run window, disk unchanged at 29GB free before/after).

### PR-7 implementation (2026-08-27)

Read-only hierarchical (M/P/A/U) disclosure surface, per §6.1's correction above. No ranking/governance/scoring/migration/schema file changed — verified via `git status`, files list in §6.1.

- **Backend tests:** `bash scripts/test_db.sh -q tests/test_api/test_ranking_hierarchical.py` → 14 new tests + the shared 41-test `test_import_records.py` prefix, **55 passed, 0 failed**. `bash scripts/test_db.sh -q tests/test_api/test_ranking_endpoint.py` (the pre-existing legacy suite, unmodified) → all 20 still pass, proving backward compatibility.
- **Canonical PR-1→PR-6 + PR-7 sweep** (`test_migrations/`, `test_ranking_boundary.py`, governance/ranking/agents/auth suites, plus `test_ranking_endpoint.py` and the new `test_ranking_hierarchical.py`, no `-p no:logging`): **751 passed, 0 failed, 0 errors**, 1155.85s (0:19:15), uninterrupted (disk 29GB free unchanged before/after, all containers healthy throughout).
- **Frontend tests:** `RankingPage.test.jsx`'s 9 new hierarchical-disclosure cases pass (`npx vitest run src/pages/RankingPage.test.jsx` → 9/9). Full frontend suite: 467/471 passing; the 4 failures (`HotUnitsTab.test.jsx` ×1, `AgentPage.test.jsx` ×3) are **pre-existing, unrelated baseline debt** — reproduced identically with this pass's own frontend changes fully `git stash`-ed out, confirmed via a stash/re-run/pop cycle, not caused by PR-7.
- **Backend baseline debt, also pre-existing and unrelated:** `tests/test_api/test_ranking_historical.py`/`test_ranking_historical_batch.py` (11 tests) fail with `404 Not Found` — no `/ranking/historical` route exists anywhere in `src/api/`/`src/main.py` (verified by grep); these test files reference a route that does not currently exist in this codebase, unrelated to hierarchical scoring or PR-7. Not fixed here (out of PR-7's narrow scope; flagged for a future maintenance pass, same category as the `0031` fix above).
- `ruff check` clean on every file this pass touched.
