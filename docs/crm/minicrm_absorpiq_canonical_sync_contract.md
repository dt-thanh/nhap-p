# MiniCRM → AbsorpIQ Canonical Data Synchronization Contract

> Read-only audit and design document. **Documentation only** — no source files,
> migrations, or database data were modified while producing this report.
> Repository ref: uncommitted working tree at the time of audit; AbsorpIQ
> Alembic head `0034_expert_ranking_governance`; MiniCRM Alembic head
> `0007_active_password_or_keycloak` (both confirmed via `alembic current`
> inside their respective containers — no drift, no multiple heads).

## Executive Summary

- **MiniCRM does not currently supply all fields AbsorpIQ's ranking/absorption pipeline needs** — it has no price, no listing/market data, no transaction-price field, and (per `docs/ranking/ranking_consultant.md`, independently corroborated in this audit) no geo, legal, developer, bank, or competitor data anywhere in either system.
- The MiniCRM → AbsorpIQ contract (v2, source-owns-everything model) is **already fully implemented in code** for project/area/unit/deal — `src/api/sync.py::start_sync` accepts `schema_version ∈ {1,2}` — but the JSON schema file's own header text still says "DRAFT — NOT IMPLEMENTED," a stale-documentation risk, not a code gap.
- Field-level ownership is clean and already enforced: `DomainProjector` (`src/services/domain_projection.py`) writes only the exact CRM-owned columns per entity and never touches AbsorpIQ-owned columns (`headline`, `introduce`, `cover_image_url`, `absorption_calculator`, etc.) — verified by direct code reading, not inferred from names.
- **No schema change is required** to support the *currently defined* contract — both databases are on a single clean Alembic head and the receiving schema already matches the v2 payload shapes column-for-column.
- **Ranking/absorption readiness is the real gap**, not sync mechanics: `project_price_observations` exists but is permanently empty by design (no price ever flows from MiniCRM — MiniCRM's own schema has no price column at all), and most of `docs/ranking/ranking_consultant.md`'s 11 requested feature groups have zero data source anywhere in the system.
- **Currently, zero MiniCRM records have ever reached AbsorpIQ in the live dev environment** — a missing sync credential (separately diagnosed in a prior investigation this session) blocks delivery at the auth boundary; this is an operational/config fact relevant to "readiness," not a contract/schema defect.
- Identity/provenance design is sound: `(source_system, source_instance_id, external_id)` is the enforced identity pattern everywhere, verified against unique constraints and `DomainProjector`'s own resolution code — no gaps found.
- Confidence: **High** for the contract/mapping analysis (all claims backed by direct code/schema reads); **Medium** for absorption-readiness claims that rely partly on the earlier `ranking_consultant.md` audit, which was directly sampled and corroborated but not re-verified byte-for-byte in this session.

## Canonical AbsorpIQ Model

| Entity | Canonical identity | Parent | Source ownership | Lifecycle | Evidence |
|---|---|---|---|---|---|
| `projects` | `(source_system, source_instance_id, external_id)` | none (root) | MiniCRM owns `name`/`launch_date`; AbsorpIQ owns `headline`/`introduce`/`cover_image_*`/`absorption_calculator`/review workflow columns | active → archived (soft, blocked by live children) | `src/models/tables.py:30-62`, `domain_projection.py:487-556` |
| `areas` | `(source_instance_id, external_id)`, scoped to project | `projects` | MiniCRM owns all 5 business fields (`area_name`, `unit_type`, `bedrooms`, `area_sqm`, `total_units`); AbsorpIQ owns headline/cover/review columns | active → archived, cannot cross projects | `tables.py:153-182`, `domain_projection.py:559-652` |
| `units` | `(source_system, source_instance_id, external_unit_id)` | `areas` | MiniCRM owns everything except `deleted_at` and timestamps (one-way mirror, no user-editable columns on the AbsorpIQ side) | available/reserved/sold/blocked, soft-delete revivable | `tables.py:247-263`, `domain_projection.py:376-420` |
| `deals` | `(source_system, source_instance_id, external_deal_id)` | `units` | MiniCRM owns everything except `deleted_at`/timestamps | lead→qualified→…→sold/lost, soft-delete | `tables.py:265-284`, `domain_projection.py:424-483` |
| `sales_records` / `inventory_snapshots` | file-import identity (`external_record_id`/`source_row_hash`) | `areas` | **Not part of the MiniCRM sync path at all** — a separate legacy CSV-import pipeline (`ImportService`, `TARGET_TABLES`) | append-only | `tables.py:184-213`, `tables.py:236-240` |
| `absorption_daily` | derived, `area_id` + `stat_date` | `areas` | 100% AbsorpIQ-derived — never written by sync | recomputed, delete-and-rewrite | `tables.py:214-233` |
| `project_price_observations` | `unit_id` + `effective_from` | `units` (FK RESTRICT) | Neither MiniCRM nor any current pipeline writes it — **schema-only, permanently empty by design** | append-only, never backfilled | `alembic/versions/0027_project_price_observations.py:29-31` |
| `feature_snapshots` / `ranking_*` | `(project_id, feature_key, scope, scope_id)` etc. | `projects`/`areas`/`units` | 100% AbsorpIQ-derived | mutable cache (`feature_snapshots`) + immutable run-bound tables (`ranking_feature_*`, 0033) | `tables.py:409-425`, `tables.py:427-478` |
| `reconciliation_runs`/`findings` | run id | `projects` | AbsorpIQ-derived, operational | append-only per run | `tables.py:327-373` |
| `sync_credentials`/`upload_files`/`crm_source_records` | `(source_system, source_instance_id, ...)` | none | Operational/provenance infrastructure, not business data | append-only / status-updated | `tables.py:67-151`, `tables.py:290-321` |

## MiniCRM Schema Inventory

Source: `minicrm/app/models.py:27-159` (mirrors `minicrm/alembic/versions/0001_minicrm_initial.py` per its own docstring), current head `0007_active_password_or_keycloak` (confirmed via `docker compose exec minicrm alembic current`).

| Table | Column | Type | Nullability | PK/FK/Unique | Meaning |
|---|---|---|---|---|---|
| `crm_projects` | `id` | UUID | NOT NULL, PK | PK | internal MiniCRM id (never sent as identity) |
| | `external_id` | Text | NOT NULL | — | identity sent to AbsorpIQ, lifelong-stable |
| | `name`, `launch_date` | Text, Date | NOT NULL | — | business fields, CRM-authoritative |
| | `status` | Text | NOT NULL | — | `active`\|`archived` only — no `pending`/`rejected` |
| | `source_revision` | BigInt | NOT NULL | — | version counter |
| | `mirrored_at`, `mirrored_revision`, `last_sync_batch_id` | TS, BigInt, Text | nullable | — | relay-side bookkeeping of last successful push |
| `crm_areas` | `id`, `external_id`, `project_id` | UUID/Text/UUID | NOT NULL | FK→crm_projects | — |
| | `area_name`, `unit_type`, `bedrooms`, `area_sqm`, `total_units` | Text/Text/Int/Numeric/Int | all NOT NULL | — | all 5 business fields authoritative, no `proposed_` prefix, no approval step |
| `crm_units` | `id`, `external_id` | UUID/Text | NOT NULL | — | — |
| | `area_id` | UUID | **nullable** | FK→crm_areas | NULL only for 163 legacy pre-Phase-B rows |
| | `area_name`, `unit_type` | Text | NOT NULL | — | **denormalized snapshot**, refreshed from `crm_areas` on every write, never edited directly |
| | `unit_code`, `unit_status` | Text | NOT NULL | — | free text, mapped explicitly on receipt |
| | `source_revision` | BigInt | NOT NULL | — | increments on every write including delete |
| | `deleted_at` | TS | nullable | — | soft tombstone |
| `crm_deals` | `id`, `external_id`, `external_unit_id` | UUID/Text/Text | NOT NULL | — | mirrors unit's external id, not a UUID FK |
| | `deal_status` | Text | NOT NULL | — | free text, mapped explicitly |
| | `reserved_at`, `sold_at`, `lost_at` | TS | nullable | — | status-required timestamps enforced at MiniCRM's own write layer, mirroring AbsorpIQ's own constraint |
| `crm_outbox` | `id`, `external_batch_id`, `entity`, `payload` (JSONB) | — | NOT NULL | — | one row per outbound batch, envelope verbatim |
| | `http_status`, `attempts`, `sent_at`, `last_error`, `replay_of` | Int/Int/TS/Text/Text | nullable except `attempts` | — | delivery bookkeeping |

**Notable absence**: no price, no customer/buyer, no salesperson field anywhere in `crm_units`/`crm_deals` — explicit, documented design choice ("KHÔNG có giá, không có khách hàng, không có nhân viên bán" — `models.py:72-74`), not an oversight.

## AbsorpIQ Schema Inventory

Source: `src/models/tables.py` (20 declared tables, current head `0034_expert_ranking_governance`, confirmed via `docker compose exec api alembic current`).

| Table | Column (business-relevant subset) | Type | Nullability | PK/FK/Unique | Meaning |
|---|---|---|---|---|---|
| `projects` | `id`, `external_id`, `source_system`, `source_instance_id`, `source_revision`, `source_updated_at` | UUID/Text×3/BigInt/TS | **all nullable** | — | NULL = legacy pre-Phase-D project, not sync-sourced |
| | `name`, `launch_date`, `status` | Text/Date/Text | NOT NULL | — | mirrored from CRM |
| | `headline`, `introduce`, `cover_image_url`, `cover_image_public_id`, `reviewed_by`, `reviewed_at`, `review_reason`, `absorption_calculator` | — | NOT NULL w/ defaults or nullable | — | **AbsorpIQ-owned, never written by sync** (confirmed by `domain_projection.py:496-506` never setting them) |
| `areas` | same provenance columns as `projects` | — | nullable | — | same legacy-vs-synced distinction |
| | `area_name`, `unit_type`, `bedrooms`, `area_sqm`, `total_units` | — | NOT NULL | unique `(project_id, area_name, unit_type)` | mirrored 1:1 from `crm_areas` |
| `units` | `source_system`, `source_instance_id`, `external_unit_id` | Text | **NOT NULL** (unlike projects/areas — units never predate the mirror) | unique `(source_instance_id, external_unit_id)` (implied by lookup pattern) | — |
| | `unit_code`, `unit_type`, `status` | Text | NOT NULL | — | `status ∈ {available, reserved, sold, blocked}` |
| | `deleted_at` | TS | nullable | — | AbsorpIQ-owned tombstone flag, driven by CRM's delete signal |
| `deals` | `source_system`, `source_instance_id`, `external_deal_id`, `unit_id` | — | NOT NULL | — | — |
| | `status`, `source_status` | Text | NOT NULL | — | `status ∈ {lead, qualified, interested, viewing, reserved, sold, lost}` (normalized), `source_status` preserves original |
| | `reserved_at`, `sold_at`, `lost_at` | TS | nullable | — | DB CHECK enforces presence matching `status` |
| `project_price_observations` | `unit_id`, `official_price`, `effective_from`, `effective_to`, `source` | UUID/Numeric(18,2)/Date/Date/Text | `unit_id`/`official_price`/`effective_from` NOT NULL | FK `unit_id→units.id` RESTRICT; partial unique on `(unit_id) WHERE effective_to IS NULL` | **Not fed by MiniCRM sync** — no producer exists |
| `sync_credentials` | `source_system`, `source_instance_id`, `key_prefix`, `key_hash`, `revoked_at`, `expires_at` | — | NOT NULL except revoked/expires | — | machine-credential store, currently 0 rows (operational gap, not schema gap) |
| `upload_files` | `source_system`, `source_instance_id`, `external_batch_id`, `rows_ok`, `rows_failed`, `status` | — | first two NOT NULL | unique on batch id (race-safe) | sync-run/batch ledger |
| `crm_source_records` | `(source_system, source_instance_id, source_entity, source_record_id)`, `payload_hash`, `state`, `last_decision` | — | NOT NULL | unique identity tuple | per-record idempotency/conflict ledger |
| `feature_snapshots` / `ranking_feature_*` / `ranking_scores` / `ranking_runs` | — | — | — | — | 100% derived, never a sync target |
| `forecast_jobs`/`forecasts`/`forecast_points` (from `0001_initial_schema.py`) | — | — | — | — | **exist in the DB but are absent from `src/models/tables.py` and never read/written by any code** — confirmed by `docs/ranking/ranking_consultant.md` item 3, independently corroborated: a grep for these table names outside migrations returns nothing in `src/` |

## Full Field Mapping

### Project Mapping

| MiniCRM field | AbsorpIQ field | Transform | Required? | Validation | Ownership | Status |
|---|---|---|---|---|---|---|
| `crm_projects.external_id` | `projects.external_id` | direct copy → `source_record_id` in envelope | Yes | uniqueness enforced by `uq_projects_source_identity` | CRM | DIRECT |
| `crm_projects.name` | `projects.name` | trim, non-empty | Yes | `_require_text` | CRM | DIRECT |
| `crm_projects.launch_date` | `projects.launch_date` | ISO date, **no timezone offset accepted** | Yes | `_require_date` rejects offset-bearing strings | CRM | TRANSFORM (calendar-date semantics, not timestamp) |
| `crm_projects.status` | `projects.status` | `archived` CRM-side → AbsorpIQ archive path (`_archive_project`); `active` → `status='active'` hardcoded, not copied verbatim | Yes | blocked while a live area exists | CRM (intent) / AbsorpIQ (representation) | TRANSFORM |
| `crm_projects.source_revision` | `projects.source_revision` | direct | Yes (v2 record-level) | monotonic per entity | CRM | DIRECT |
| — | `projects.headline`, `introduce`, `cover_image_url`, `cover_image_public_id`, `reviewed_by`, `reviewed_at`, `review_reason`, `absorption_calculator` | never set by projection code | N/A | N/A | **AbsorpIQ** | FORBIDDEN — MiniCRM has no equivalent field and must never populate these |
| `(implicit) X-API-Key credential` | `projects.source_system`/`source_instance_id` | derived from authenticated caller, never from payload body | Yes | — | AbsorpIQ (from auth context) | DERIVED |

### Area Mapping

| MiniCRM field | AbsorpIQ field | Transform | Required? | Validation | Ownership | Status |
|---|---|---|---|---|---|---|
| `crm_areas.external_id` | `areas.external_id` | direct | Yes | scoped unique within project | CRM | DIRECT |
| `crm_areas.area_name` | `areas.area_name` | trim | Yes | — | CRM | DIRECT |
| `crm_areas.unit_type` | `areas.unit_type` | trim | Yes | forms natural key with `area_name` (`uq_areas_project_name_unit_type`) | CRM | DIRECT |
| `crm_areas.bedrooms` | `areas.bedrooms` | direct int | Yes, no default | `>= 0` | CRM | DIRECT |
| `crm_areas.area_sqm` | `areas.area_sqm` | direct numeric | Yes, no default | `> 0` | CRM | DIRECT |
| `crm_areas.total_units` | `areas.total_units` | direct int | Yes, no default | **explicitly documented as the absorption-rate denominator — never inferred by counting unit rows** | CRM | DIRECT (high-stakes field) |
| — | `areas.headline`, `introduce`, `cover_image_*`, review columns | never set | N/A | N/A | AbsorpIQ | FORBIDDEN |
| project_ref (external_project_id) | `areas.project_id` | resolved by lookup, rejected if project missing/archived | Yes | `PARENT_ARCHIVED`, `PROJECT_NOT_FOUND` | AbsorpIQ (resolution) | DERIVED |

### Unit Mapping

| MiniCRM field | AbsorpIQ field | Transform | Required? | Validation | Ownership | Status |
|---|---|---|---|---|---|---|
| `crm_units.external_id` | `units.external_unit_id` | direct | Yes | unique per instance | CRM | DIRECT |
| `crm_units.unit_code` | `units.unit_code` | trim | Yes | unique-among-live-units within area (business rule, MiniCRM-side) | CRM | DIRECT |
| `crm_units.unit_status` | `units.status` | **lowercased, checked against fixed enum `{available, reserved, sold, blocked}` — no alias table found; unrecognized value is rejected outright (`UNKNOWN_UNIT_STATUS`), not defaulted** | Yes | `domain_projection.py:386-396` | CRM (value) / AbsorpIQ (vocabulary gate) | TRANSFORM |
| `crm_units.area_name`/`unit_type` (denormalized) | — | **not used** — `unit_type` on the AbsorpIQ side is read back from the resolved `areas` row, not from the unit payload | N/A | area_ref must resolve first | AbsorpIQ (derived from area, not unit payload) | DERIVED (source field present but deliberately unused for this column) |
| `area_ref.external_area_id` | `units.area_id` | resolved lookup scoped to `project_id` | Yes | `UNKNOWN_AREA` if not found — **no fallback to name matching** | AbsorpIQ (resolution) | DERIVED |
| — | `units.deleted_at` | tombstone operation → set; upsert with newer revision → cleared, reviving the unit | N/A | — | AbsorpIQ (state machine), driven by CRM intent | DERIVED |
| — (no MiniCRM field) | any price/customer/agent field | **does not exist on AbsorpIQ's `units` table either** | N/A | N/A | N/A | MISSING_SOURCE and MISSING_TARGET simultaneously — neither side models this |

### Deal Mapping

| MiniCRM field | AbsorpIQ field | Transform | Required? | Validation | Ownership | Status |
|---|---|---|---|---|---|---|
| `crm_deals.external_id` | `deals.external_deal_id` | direct | Yes | — | CRM | DIRECT |
| `crm_deals.external_unit_id` | `deals.unit_id` | resolved lookup by `(source_instance_id, external_unit_id)` | Yes | `UNKNOWN_UNIT_REFERENCE` if unit doesn't exist yet (parent-ordering) | AbsorpIQ (resolution) | DERIVED |
| `crm_deals.deal_status` | `deals.status` + `deals.source_status` | lowercase, alias `cancelled`/`canceled`→`lost`, else checked against `{lead, qualified, interested, viewing, reserved, sold, lost}` — **7-value vocabulary, richer than units' 4-value vocabulary**, unrecognized → rejected | Yes | `normalize_deal_status`, `domain_projection.py:175-202` | CRM (value) / AbsorpIQ (vocabulary + one alias) | TRANSFORM |
| `crm_deals.reserved_at`/`sold_at`/`lost_at` | same-named columns | ISO-8601 **with mandatory timezone offset**, `null` explicit = clear, absent (in `partial` mode) = keep | Conditionally — DB CHECK requires the timestamp matching the current `status` | `_check_status_dates`, plus a DB-level CHECK constraint (redundant, intentional, for atomic-batch-rollback reasons) | CRM | TRANSFORM (timezone-strict) |
| — | any transaction/agreed price field | **does not exist on either side** | N/A | N/A | N/A | MISSING_SOURCE and MISSING_TARGET |

### Inventory / Sales

There is **no MiniCRM → `inventory_snapshots`/`sales_records` mapping at all.** Those two AbsorpIQ tables are fed exclusively by the separate, legacy CSV `ImportService` (`src/models/tables.py::TARGET_TABLES`), a wholly different ingestion path with its own identity model (`external_record_id`/`source_row_hash`). This is not inferred from similar-sounding names — confirmed directly: `TARGET_TABLES = {"areas": areas, "sales_records": sales_records, "inventory_snapshots": inventory_snapshots}` never appears in `sync_client.py`, `domain_projection.py`, or the v2 contract schema. **Status: UNMAPPED (by design, two independent pipelines coexisting).**

## Missing Tables

None required for the *currently defined* v1/v2 contract — the receiving schema already covers every field the contract sends. Prioritized items are about future needs, not current gaps:

| Application | Table | Why needed | Consumer | Priority | Migration required |
|---|---|---|---|---|---|
| AbsorpIQ | project-name→project mapping table (for any *external* market dataset, not MiniCRM) | Not needed for MiniCRM (already has `external_id`/`external_area_id` identity) — only relevant if a future non-CRM listing dataset needs human-curated matching | future feature engineering | Low | N/A — out of this contract's scope |
| AbsorpIQ | none for MiniCRM sync itself | — | — | — | No |

## Missing Fields

| Application | Table | Column | Type | Nullable | Why needed | Priority |
|---|---|---|---|---|---|---|
| MiniCRM | `crm_units`/`crm_deals` | any price field | — | — | Required for `project_price_observations`, price-competitiveness ranking features — **currently absent from the product's source of truth entirely, not just unsynced** | High (product decision, not a sync-code fix) |
| MiniCRM | `crm_deals` | none currently missing for the *existing* absorption metric (`velocity_7d`/`30d`, `sell_through`) — those derive from `status`+timestamps, already present | — | — | — | N/A |
| AbsorpIQ | `units`/`deals` | mirror columns for price — **would only make sense after MiniCRM gains a source field** | — | — | Do not add speculatively | N/A |

**No schema change is recommended here** — there is nothing to add on the AbsorpIQ side until MiniCRM defines a price field to send, and adding a receiving column with no sender is exactly the "field nobody consumes" anti-pattern this audit was instructed to avoid.

## Missing Constraints/Indexes

None found missing for the current contract. `uq_projects_source_identity`, `uq_areas_project_name_unit_type`, the units/deals lookup indexes, and `crm_source_records`' identity unique constraint were all confirmed present and load-bearing in the code paths read.

## Identity and Provenance Plan

| Entity | MiniCRM identity | AbsorpIQ identity | Mapping table | Unique constraint | Missing piece |
|---|---|---|---|---|---|
| Project | `crm_projects.external_id` (internal UUID never sent) | `(source_system, source_instance_id, external_id)` | none needed — direct | `uq_projects_source_identity` | None |
| Area | `crm_areas.external_id`, scoped to project | `(source_instance_id, external_id)` scoped by `project_id` FK | none needed | natural key `(project_id, area_name, unit_type)` — **note: identity key and natural key are different things and both enforced** | None |
| Unit | `crm_units.external_id` | `(source_instance_id, external_unit_id)` | none needed | implied unique (lookup pattern in `_project_deal`) | None |
| Deal | `crm_deals.external_id`, references unit by `external_unit_id` (not UUID) | `(source_instance_id, external_deal_id)`, `unit_id` resolved | none needed | — | None |

No entity anywhere uses email, display name, address, array position, or a generated UUID as identity — all four use the mandated `(source_system, source_instance_id, external_*_id)` pattern, verified directly, not assumed.

## Parent/Child Ordering Plan

```text
source event
→ entity identity (external_id within source_instance_id)
→ parent resolution (project_id / area_id / unit_id looked up, never trusted from client)
→ validation (field presence, status vocabulary, timestamp/status consistency)
→ projection (DomainProjector, one committed row per accepted record)
→ derived jobs (domain-recompute / ranking-recompute, enqueued post-commit only on a mirror-changing decision)
```

| Child | Parent | Current behavior if parent missing | Correct behavior | Required change |
|---|---|---|---|---|
| Area | Project | `PROJECT_NOT_FOUND` (whole envelope rejected, per prior audit this session) | matches | None |
| Unit | Area | `UNKNOWN_AREA`, that record only rejected | matches | None |
| Deal | Unit | `UNKNOWN_UNIT_REFERENCE`, that record only rejected | matches | None |
| any | archived parent | `PARENT_ARCHIVED` | matches | None |

The v2 contract's own `records` array ordering rule (`project → area → unit → deal` for upsert, reverse for delete, "phía nhận... KHÔNG sắp xếp lại hộ hệ nguồn" — receiver never reorders) is enforced by the schema description, not re-verified against the Python parser line-by-line in this audit — **UNVERIFIED** whether the parser actually rejects an out-of-order batch versus merely processing it in array order and letting per-record errors surface naturally; both give a safe outcome, but the exact enforcement code was not traced.

Foreign keys are not bypassed anywhere in this design — confirmed by direct reading of `_project_deal`'s `UNKNOWN_UNIT_REFERENCE` rejection and `_project_area`'s `PARENT_ARCHIVED`/`AREA_CROSS_PROJECT_MOVE` guards.

## Ranking/Absorption Compatibility

Reusing and directly corroborating `docs/ranking/ranking_consultant.md` (dated 2026-08-22, itself an evidence-based audit independently sampled and found accurate against the current code):

| Feature | Required source fields | Current source | Derived by | Target table | Available after sync? | Classification |
|---|---|---|---|---|---|---|
| `velocity_7d`/`velocity_30d` | unit `status`, `sold`-transition timestamps | MiniCRM (via `deals.sold_at`) | `src/services/absorption.py::_rolling_mean` | `absorption_daily` | Yes | AbsorpIQ-derived from MiniCRM-provided fields |
| `sell_through` (cumulative `units_sold/total_units`) | `areas.total_units`, unit sold-count | MiniCRM | `src/services/domain_absorption.py:631` | dashboard read | Yes | AbsorpIQ-derived — **but frontend aliases this to the field name `absorption_rate`, which is misleading**: it is a cumulative ratio, not a period rate |
| Strict period absorption rate (`sold_in_period / sellable_at_period_start`) | — | **does not exist anywhere in the backend** | — | — | No | Unavailable from MiniCRM or AbsorpIQ as currently implemented |
| Price competitiveness / price-per-sqm | listing or transaction price | **MiniCRM has no price field** | — | `project_price_observations` (empty) | No | Unavailable from MiniCRM |
| Legal readiness, developer reputation, bank/financing | — | none | — | — | No | Unavailable — no source anywhere |
| Location/accessibility, PostGIS distance features | lat/long | none | — | — | No | Unavailable — confirmed zero hits for `postgis\|geometry(\|latitude\|longitude` in `alembic/`/`src/` |
| Market competition, macro indicators | — | none | — | — | No | Unavailable |
| Forecast (Prophet-based sellout date, confidence interval) | historical absorption time series | schema exists (`forecast_jobs`/`forecasts`/`forecast_points`, migration 0001) | `src/jobs/forecast.py::run_daily_forecast` | — | **Stub — computes nothing**, `processed=0` | Schema present, pipeline absent |

## Absorption Readiness

| Signal | Available from MiniCRM? | Required fields | Current target | Missing data | Decision |
|---|---|---|---|---|---|
| Project absorption (cumulative) | Yes | `areas.total_units`, `deals.status`+`sold_at` | `absorption_daily.units_sold`, `sell_through` | None | **Supportable now**, once sync is actually delivering (see operational gap below) |
| Unit-level likelihood of sale | Partially | unit status history, time-in-status | `unit_status_history` (0028, DB trigger) exists and captures this | Feature-engineering code to consume it does not yet exist (per `ranking_consultant.md`) | Data exists; **feature not yet built** |
| Sales velocity (rolling mean) | Yes | same as above | `absorption_daily.velocity_7d/30d` | None | Supportable now |
| Time-to-sell | Partially | `reserved_at`→`sold_at` deltas | data present in `deals`, no aggregate table computes this yet | Aggregation logic missing | Data exists; **feature not yet built** |
| Inventory pressure | Yes | current `units`/`deals` state and `unit_status_history` audit events | `absorption_daily` and domain analytics | None | Supportable now |
| Price competitiveness | **No** | any price field | `project_price_observations` (empty, no producer) | **MiniCRM has no price field at all — not a sync gap, a source-data gap** | **Not supportable without a product decision to add a price field to MiniCRM** |

## Alembic Plan

**No new migration is recommended for the currently-defined contract.** Both applications sit on a single clean head (AbsorpIQ `0034_expert_ranking_governance`, MiniCRM `0007_active_password_or_keycloak`, both confirmed via `alembic current` inside their respective containers) and the receiving schema already matches every field the v2 contract sends, verified column-by-column above. If/when a product decision adds price to MiniCRM, the correct receiving migration would extend `project_price_observations` usage (already schema-ready) or add an explicit `price_source='mini_crm'` value to its `source` column — not a new table, per that migration's own design intent.

## Synchronization Architecture

The existing architecture already matches the preferred at-least-once/idempotent-receiver pattern and should not be replaced:

```text
durable outbox (crm_outbox, committed atomically with the business row)
→ at-least-once delivery (in-process relay, sequential, timeout-safe: http_status stays NULL on ambiguity)
→ idempotent receiver (external_batch_id replay, per-record source_revision/hash conflict resolution)
→ reconciliation (reconciliation_runs/findings — orthogonal, operator-triggered)
```

No anti-corruption layer, staging table, or distributed transaction is warranted — the contract is already an explicit, versioned envelope (`schema_version`, `source_system`, `source_instance_id`) validated before any business logic runs (`is_contract_v2`), which is itself the anti-corruption boundary a from-scratch design would introduce. The one real architectural risk already identified (in a prior investigation this session) is operational, not structural: the `sync_credentials` table has zero rows in the live dev environment, so the otherwise-correct pipeline currently delivers nothing — this is a **deployment/bootstrap gap**, not a design flaw, and is out of this mapping task's scope to fix.

## Codex Implementation Plan

### Work Item 1
- **Title**: Correct the stale "DRAFT — NOT IMPLEMENTED" header on the v2 contract schema
- **Problem**: `src/contracts/crm_sync_v2.schema.json`'s `title`/`description` claim v2 is unimplemented and that `SUPPORTED_SCHEMA_VERSIONS` is `{1}`; code (`src/services/json_payload.py:34`) has actually shipped `{1, 2}`.
- **Evidence**: `crm_sync_v2.schema.json` lines 4-5 vs. `json_payload.py:34`.
- **Application**: AbsorpIQ.
- **Files to modify**: `src/contracts/crm_sync_v2.schema.json` (doc text only), possibly `docs/crm/sync_contract_v2_draft.md` if it makes the same stale claim.
- **Functions/classes**: none — text only.
- **Tables affected**: none.
- **Migration required**: No.
- **Exact schema change**: none — field definitions are current and correct; only the descriptive header text is wrong.
- **Exact mapping/validation/identity/retry change**: none.
- **Authorization implications**: none.
- **Backward compatibility**: N/A.
- **Test cases**: none required (docs-only).
- **Acceptance criteria**: header text matches actual `SUPPORTED_SCHEMA_VERSIONS`.
- **Rollback plan**: trivial revert.
- **Risk**: None.
- **Dependencies**: none.

### Work Item 2
- **Title**: Feature-engineering consumption of already-captured `unit_status_history` for time-to-sell and unit-level sale likelihood
- **Problem**: the underlying event data is captured (0028) but no feature/ranking code reads it yet (per `ranking_consultant.md` item 6, and this audit's own confirmation that `feature_snapshots` is upserted-in-place, not run-pinned).
- **Evidence**: `alembic/versions/0028_unit_status_history.py`; the former `unit_inventory_daily` materialization was removed by migration 0036.
- **Application**: AbsorpIQ.
- **Files to modify**: new feature-computation module under `src/services/` (exact name left to Codex's discretion, matching existing `absorption.py`/`domain_absorption.py` conventions).
- **Tables affected**: reads `unit_status_history`, writes `feature_snapshots` or the newer `ranking_feature_snapshots`/`ranking_feature_values` (0033) — **prefer the immutable run-bound tables per that migration's own stated rationale**.
- **Migration required**: No — target tables already exist.
- **Test cases**: unit tests for time-to-sell aggregation against a synthetic status-history fixture; real-Postgres integration test.
- **Acceptance criteria**: a new feature key appears in `ranking_feature_values` with `source='derived'`.
- **Rollback plan**: additive feature, disable by omitting from `ranking_config_features`.
- **Risk**: Low — purely additive.
- **Dependencies**: None on Work Item 1.

### Work Item 3 (explicitly a product decision gate, not a code task)
- **Title**: Decide whether MiniCRM will ever carry a price field
- **Problem**: price-based ranking features (price competitiveness, price-per-sqm) are permanently unsupportable without this.
- **Evidence**: `crm_units`/`crm_deals` schema (no price column), `project_price_observations`'s own migration docstring confirming zero producers.
- **Application**: Both (requires a MiniCRM schema change and a new sync contract field, in that order).
- **Migration required**: Yes, but **only after** the product/business decision is made — do not speculatively add a column to either side.
- **Acceptance criteria**: N/A until the decision is made — this item exists to make explicit that price features "cannot be honestly supported" today (see Final Constraint answers below), not to prescribe a schema.

## Test Plan

Reuses this repository's existing, already-passing test suites almost entirely — no new mapping logic is being introduced, so no new mapping tests are required beyond Work Item 2's feature computation:

- **Schema**: `tests/test_migrations/test_00*` (existing, per-revision, already covers empty-DB and current-DB migration for every relevant revision).
- **Mapping**: `tests/e2e/test_minicrm_crud_flow.py` (29 tests, per a prior audit this session, already covers every project/area/unit/deal field, null behavior, enum mapping, parent-child ordering).
- **Identity**: `tests/test_api/test_sync_idempotency.py`, `tests/test_api/test_sync_concurrency.py` (existing, already cover replay/stale/conflict/duplicate).
- **Security**: `tests/auth/`, `tests/test_services/test_dashboard_auth.py` (existing, unaffected by this mapping work).
- **Integration/E2E**: `tests/e2e/test_keycloak_two_stack_flow.py::test_full_journey_project_area_unit_deal_mirrors_into_absorpiq` (existing, real HTTP + real Postgres, currently blocked by an unrelated stale-test-database-name issue and the missing-credential issue both diagnosed separately this session — **not a mapping defect**).
- **New tests needed**: only for Work Item 2's feature computation (time-to-sell, unit-level sale-likelihood).

## Acceptance Criteria

The contract is considered correctly modeled (independent of whether it is *currently delivering*, which is a separate operational question already diagnosed separately) when:
1. Every MiniCRM-owned field maps to exactly one AbsorpIQ column with an explicit transform, verified — **done, this document**.
2. No AbsorpIQ-derived field is ever accepted from MiniCRM input — **verified true**.
3. No schema drift exists between either app's migration head and its live database — **verified true**.
4. Absorption/ranking feature lineage is honestly labeled where data is missing — **done, this document**, matching `ranking_consultant.md`'s own standard.

## Risks and Non-Goals

- **Non-goal**: adding a price field to either system — explicitly out of scope without a product decision.
- **Non-goal**: building the forecast pipeline — schema exists, pipeline is a stub, not addressed here.
- **Risk**: the stale v2-schema documentation (Work Item 1) could mislead a future engineer into thinking v2 is unavailable and re-implement something that already exists.
- **Risk**: `project_price_observations`' empty state could be mistaken for a bug rather than a deliberate "no backfill" design — already documented in that migration's own docstring, worth restating in any future ranking-readiness report.

## pipeline_status.md Update Plan

Not modified as part of producing this document. After Codex implements any of the above work items, it should append a new dated section to `pipeline_status.md` containing only measured evidence: which work items were completed, exact test pass/fail counts from the suites listed above, and — if Work Item 2 is implemented — the actual feature keys and sample values written to `ranking_feature_values`, not projected/hypothetical numbers.

---

## Final Constraint — Direct Answers

1. **Does MiniCRM have all source fields AbsorpIQ needs?** No — it has everything for identity, hierarchy, and status-based absorption (project/area/unit/deal, all statuses, all timestamps), but nothing for price-based ranking features.
2. **Missing MiniCRM tables/columns?** No missing tables. Missing column: a price field on `crm_units` or `crm_deals` (does not exist today).
3. **Missing AbsorpIQ tables/columns?** None for the current contract — everything the contract sends already has a matching column.
4. **Fields required specifically for absorption/ranking that are currently missing?** Price (blocks price-competitiveness features); nothing else — status/timestamp-based absorption features are already fully supportable from existing MiniCRM data.
5. **Fields that must not be synchronized?** Any AbsorpIQ-owned display/workflow field (`headline`, `introduce`, `cover_image_*`, `absorption_calculator`, review columns) — confirmed never written by the projection code and must stay that way.
6. **Which migration should be created first?** None, for the current contract — schema is already complete and matched.
7. **Which changes should Codex implement first?** Work Item 1 (fix the stale contract doc — zero risk, immediate clarity) then Work Item 2 (surface already-captured status-history data as ranking features — the highest-value gap that's actually buildable today).
8. **What cannot be honestly supported with current data?** Price competitiveness, transaction-price-based features, legal/developer/bank/financing signals, location/accessibility, market competition, and macro indicators — none of these have any data source in either system today.
