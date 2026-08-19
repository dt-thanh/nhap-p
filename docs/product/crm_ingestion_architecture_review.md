# CRM → Canonical Ingestion — Architecture Review

**Type:** read-only repository analysis + architecture recommendation
**Repo state inspected:** working tree at `HEAD` = `be4580c`, Alembic head `0004_cover_image_public_id`
**Date:** 2026-08-08
**Scope:** MVP 1 ingestion boundary only. MVP 2 / MVP 3 are analysed for dependencies but not redesigned.

Every claim below cites a file, migration revision, constraint name, or test name. Documentation was not treated as evidence.

---

## 1. Executive conclusion

**Yes — CSV + JSON is a reasonable MVP integration boundary**, but the repository's current ingestion layer cannot implement it without schema changes, and the blocking problems are not about file formats. They are about **idempotency and identity**.

Four findings drive every recommendation in this document:

1. **The current pipeline has no record-level idempotency.** `ImportService._insert_rows()` issues plain `sa.insert(target)` (`src/services/import_records.py:256,261`) with no `ON CONFLICT`. Cross-batch duplicates are caught only by the database unique constraints, which raise `IntegrityError` and **roll back the entire batch**. This is asserted as intended behaviour by `tests/test_services/test_import_records.py::test_business_key_violation_rolls_back_everything`. A CRM that exports overlapping windows — the normal case for incremental sync — will fail every batch after the first.

2. **Deduplication is anchored on file checksum, which is the wrong key for CRM sync.** `uq_upload_files_project_checksum` (revision `0001_initial_schema`) plus `_find_duplicate()` (`src/api/files.py:62-70`) returns HTTP 409 `DUPLICATE_FILE` when the same bytes arrive twice. For a sync protocol, replaying an unchanged snapshot must be an idempotent **no-op**, not an error. Architectural constraint 6 ("replaying the same input must not create duplicate business records") is satisfied today only by refusing the replay outright.

3. **There is no source-identity table.** Nothing in any migration maps a CRM record ID to a canonical row. `sales_records.external_record_id` is the closest thing, but it is scoped to `(area_id, sold_date, external_record_id)` (`uq_sales_area_date_external_id`) — a *business* key, not a *source* key, and it exists only on `sales_records`. Constraint 4 (traceability to source system / source record ID / sync run / source version) is **not satisfiable** with the current schema.

4. **The CRM business entities do not exist.** `customers`, `customer_interactions`, `units`, `deals` appear in **no migration and no model**. Verified: `grep -rln "customers|deals|interactions" src/ alembic/ --include=*.py` matches only `src/services/excel_parser.py` (substring hits on `units_sold`). The only sales-shaped canonical tables are `sales_records` (daily aggregate: `area_id`, `sold_date`, `units_sold`) and `inventory_snapshots` (daily aggregate: `area_id`, `snapshot_date`, `units_remaining`). **The current canonical model is area/day aggregates, not per-unit or per-deal records.**

**Recommended integration contract: Option B — JSON is the canonical external contract, CSV is a compatibility adapter.**

**Recommended verdicts:**

| Table | Verdict |
|---|---|
| `upload_files` | **GENERALIZE** (additive columns + relax three constraints; defer rename to `sync_runs`) |
| `upload_errors` | **GENERALIZE** (additive columns + relax `row_number` NOT NULL) |
| `sales_records` / `inventory_snapshots` | **KEEP** unchanged as derived aggregates; stop treating them as the sync target |
| new `crm_source_records` | **ADD** — the missing identity/idempotency map |

**Cost estimate:** one additive Alembic revision (`0005`), one new service, ~2 modified services, no destructive migration, no data loss, no FK rewiring.

---

## 2. Current repository reality

### 2.1 Migrations

Four revisions, linear, head `0004_cover_image_public_id`:

| Revision | `down_revision` | Content |
|---|---|---|
| `0001_initial_schema` | — | 21 tables + indexes |
| `0002_project_area_approval` | `0001_initial_schema` | Adds `status`, `headline`, `introduce`, `cover_image_url`, `created_by`, `reviewed_by`, `reviewed_at`, `review_reason` to `projects` and `areas` |
| `0003_content_column_defaults` | `0002_project_area_approval` | `SET DEFAULT ''` on `headline` / `introduce` |
| `0004_cover_image_public_id` | `0003_content_column_defaults` | Adds `cover_image_public_id` to `projects` and `areas` |

### 2.2 Implementation status by concern

| Concern | Status | Evidence |
|---|---|---|
| File upload (multipart) | **Implemented** | `POST /api/v1/files/upload` — `src/api/files.py:145-250` |
| File storage + SHA-256 | **Implemented** | `FileUploadService.save()` — `src/services/file_upload.py:76-85` |
| Excel/CSV parsing | **Implemented** | `ExcelParserService` — `src/services/excel_parser.py`; 56 tests |
| Row-level validation | **Implemented** | `_check_constraints()`, `_CONVERTERS` — `src/services/excel_parser.py:399,313-357` |
| Canonical write (single transaction) | **Implemented** | `ImportService.load()` — `src/services/import_records.py:135-173` |
| Batch-level error threshold | **Implemented** | `import_error_threshold` default `0.5` — `src/config.py:63`; `ImportRejectedError` |
| Error persistence by row/column | **Implemented** | `upload_errors`; `GET /files/{id}/errors`, `/errors.csv` |
| Absorption recompute | **Implemented** | `AbsorptionCalculatorService.recompute()` — `src/services/absorption.py:97-131` |
| Dashboard read APIs | **Implemented** | `GET /api/v1/absorption`, `/absorption/summary` — `src/api/dashboard.py:196-241` |
| Background worker | **Implemented** | Redis + RQ; `INGEST_QUEUE="ingest"` — `src/task_queue.py:14` |
| **Record-level idempotent upsert** | **Missing** | No `ON CONFLICT` anywhere in `src/services/import_records.py` |
| **Source-record identity map** | **Missing** | No table maps CRM id → canonical id |
| **JSON input** | **Missing** | `ALLOWED_SUFFIXES = CALAMINE_SUFFIXES \| CSV_SUFFIXES` — `src/services/file_upload.py:25`; `.json` not accepted |
| **CRM entities (`customers`, `units`, `deals`, `customer_interactions`)** | **Missing** | Absent from all migrations and `src/models/tables.py` |
| **Update/correction handling** | **Missing** | Import is insert-only; no `UPDATE` path for previously-loaded records |
| **Soft delete / tombstones** | **Missing** | No `deleted_at` on any ingested table |
| Forecasting | **Planned only** | `src/jobs/forecast.py` is a stub: `# TODO (MVP 2)`, returns `processed = 0` |
| LangGraph agent | **Planned only** | `src/agents/` contains `example_node.py`, `example_tool.py` |
| Auth / RBAC | **Missing** | No login route, no JWT middleware; confirmed by `pipeline_status.md` Known Issues |
| Audit logging | **Planned only** | `audit_logs` table exists (`0001`); no `AuditLogService` in `src/` |

### 2.3 Contradictions found (documentation vs. code)

| # | Contradiction | Reality |
|---|---|---|
| K1 | SRS §2.3 defines roles `sales_staff` / `sales_manager` / `viewer` | `0001_initial_schema` enforces `ck_users_role`: `role IN ('admin','manager','analyst')`. **The schema cannot store the documented roles.** |
| K2 | SRS §5.2 (as revised earlier this session) specifies Sales Staff typing data into an **in-app** mini CRM | This brief specifies the mini CRM as an **external, enterprise-owned** system feeding CSV/JSON. These are different products. See §14. |
| K3 | SRS §5.2 previously described the parser as `pandas`/`openpyxl` | `src/services/excel_parser.py` uses `python-calamine`; `openpyxl` is a write-side fallback only |
| K4 | `absorption_daily.data_quality_status` CHECK allows `'error'` | `src/services/absorption.py:156,328` only ever emits `'ok'` / `'warning'` |
| K5 | `src/models/tables.py` is described as the model layer | It mirrors **only 8 of 21 tables** (144 lines). `scripts/seed_dev.py` reflects the other 13 from the live database rather than declaring them. |

### 2.4 The canonical model is aggregate, not transactional

This is the single most consequential fact for CRM integration and it is easy to miss:

```
sales_records        (area_id, sold_date, units_sold, external_record_id, source_row_hash, file_id)
inventory_snapshots  (area_id, snapshot_date, units_remaining, snapshot_type, source_row_hash, file_id)
absorption_daily     (area_id, stat_date, units_sold, velocity_7d, velocity_30d, ...)
```

There is no `unit_id` anywhere in the schema. `areas.total_units` is a scalar count. A CRM that exports **per-unit and per-deal records** does not map onto this model without either (a) aggregating in the ingestion layer, or (b) adding `units` and `deals` tables. This choice is the main open decision — see §14, D1.

---

## 3. Recommended integration boundary

### Q1 — Is CSV + JSON a reasonable MVP boundary?

**Yes, with one qualification.** It is reasonable *as a transport boundary*. It is not sufficient *as an integration contract*: the contract must also specify identity, versioning, and conflict policy. A CSV/JSON boundary without a stable `source_record_id` and a `source_updated_at` reduces to "re-import everything and hope", which the current code cannot do (§1, finding 1).

Accepting files rather than opening a live database connection to the CRM is correct for this MVP: it keeps the CRM's schema out of this system's coupling surface and satisfies constraint 3.

### Q2/Q3 — Which inputs, and which is canonical?

**Recommendation: Option B.**

- **JSON is the canonical external contract**, delivered as either a batch file upload or an API payload — the same envelope either way.
- **CSV is a compatibility adapter**, accepted for the `units` and `deals` entities only, mapped into the same canonical record shape before validation.

Justification grounded in this repository:

1. **The internal pipeline already converges both formats on one record shape.** `ExcelParserService.parse_to_csv()` (`src/services/excel_parser.py:701-745`) writes a normalized staging file whose columns are canonical field names, and `ImportService` reads only that staging file. Adding a JSON adapter means adding one producer of the same staging shape — the validation, threshold, and write path are unchanged. This is the cheapest possible extension.
2. **CSV has no type system, and the code pays for it.** `_build_record()` re-casts every staging value because "đi qua CSV thì mọi giá trị đều thành chuỗi" (`src/services/import_records.py:275-279`). JSON carries `null` vs `""`, numbers, booleans, and RFC-3339 timestamps natively — which matters for `source_updated_at`, the field the entire conflict policy depends on.
3. **Interactions and deals are nested and optional-heavy.** Representing an interaction with a nullable `next_follow_up_at` and a free-text note in CSV requires quoting and null-sentinel conventions that produce exactly the ambiguity class the validator cannot distinguish from real data.
4. **CSV cannot be dropped.** The repo has 56 passing parser tests and a working CSV path; a real-estate sales team will hand over spreadsheets regardless of what the CRM supports. Keeping CSV as an adapter preserves that investment at zero cost.

Rejected alternatives:
- **Option A (equal support)** — doubles the contract surface: two schema-version mechanisms, two error-locator models, two idempotency stories. Violates "smallest viable design".
- **Option C (CSV first, JSON later)** — would force `source_updated_at` and `source_record_id` through a stringly-typed channel, and the retrofit would break the CSV contract already given to the customer.

### Q4 — Minimum viable synchronization model

**Incremental upsert keyed on source identity, with periodic full reconciliation.**

| Aspect | Recommendation |
|---|---|
| Default mode | `incremental` — CRM sends records changed since `last_source_cursor` |
| Reconciliation mode | `full_snapshot` — complete entity export, run weekly; records absent from a full snapshot are tombstoned (`deleted_at` set), never hard-deleted |
| Identity | `(source_system, source_entity, source_record_id)` |
| Version | `source_updated_at` (RFC 3339, timezone-required) |
| Conflict policy | **Last-writer-wins by source clock.** Apply an incoming record only when `incoming.source_updated_at > stored.source_updated_at`. Equal timestamp with different `content_hash` → reject as `SOURCE_VERSION_CONFLICT`, do not guess. |
| Ordering | Within a batch, sort by `source_updated_at` ascending before applying, so an out-of-order batch converges |
| Cadence | Daily is sufficient — NFR-P3 already only requires < 24 h freshness |
| Deletes | Tombstone only. A tombstoned deal is excluded from absorption on the next recompute |

Frequency does not need to be configurable in MVP 1. One scheduled daily incremental plus a manual trigger matches the existing scheduler (`src/scheduler.py`, `forecast_cron`).

---

## 4. Recommended CRM → canonical field mapping

**Legend.** *Owner:* `S` = source-owned (CRM is authoritative; never edited in this platform) · `P` = platform-owned (generated/derived here). *Abs:* affects absorption calculation. *PII:* personally identifiable — must never reach an LLM (constraint 10). *Tier:* `REQ` required for ingestion · `REC` recommended · `OPT` future · `REJ` must not be accepted.

Canonical targets marked **(new)** do not exist in the repository today.

### A. Project

| CRM field | Canonical | Type | Req | Example | Normalization | Validation | Dedup role | Owner | Abs | PII | Tier / reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `source_project_id` | `crm_source_records.source_record_id` **(new)** | text | ✔ | `PRJ-001` | trim | non-blank, ≤128 | **Identity key** | S | ✖ | ✖ | REQ — without it no project can be re-synced |
| `project_name` | `projects.name` | varchar | ✔ | `Vinhomes Ocean Park` | trim, collapse spaces | non-blank, ≤255 | none (`projects` has **no** UNIQUE on `name` — verified `0001`) | S | ✖ | ✖ | REQ |
| `project_status` | `projects.status` | varchar | ✖ | `active` | lowercase | map to `ck_projects_status` set: `pending`/`active`/`rejected`/`archived` | none | S | ✖ | ✖ | REC — unmapped values must be rejected, not coerced |
| `launch_date` | `projects.launch_date` | date | ✔ | `2025-03-01` | ISO-8601 | valid date, not > today+5y | none | S | ✖ | ✖ | REQ — `NOT NULL` in `0001` |

### B. Area / block

| CRM field | Canonical | Type | Req | Example | Normalization | Validation | Dedup role | Owner | Abs | PII | Tier / reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `source_area_id` | `crm_source_records.source_record_id` **(new)** | text | ✔ | `AR-12` | trim | non-blank | **Identity key** | S | ✖ | ✖ | REQ |
| `project_id` | resolved → `areas.project_id` | uuid | ✔ | `PRJ-001` | resolve via `crm_source_records` | referenced project must exist and be `active` (mirrors `PROJECT_NOT_ACTIVE`, `src/services/projects.py`) | none | S | ✖ | ✖ | REQ |
| `area_name` | `areas.area_name` | varchar | ✔ | `Sapphire 1` | trim | non-blank (`ck_areas_area_name_not_blank`) | part of `uq_areas_project_name_unit_type` | S | ✖ | ✖ | REQ |
| `unit_type` | `areas.unit_type` | varchar | ✔ | `2PN` | trim, uppercase | non-blank (`ck_areas_unit_type_not_blank`) | part of `uq_areas_project_name_unit_type` | S | ✔ (grain) | ✖ | REQ |
| `total_units` | `areas.total_units` | int | ✔ | `120` | — | `>= 0` (`ck_areas_total_units_nonnegative`) | none | S | **✔ denominator** | ✖ | REQ — absorption rate denominator |
| `bedrooms` | `areas.bedrooms` | int | ✔ | `2` | — | `>= 0` | none | S | ✖ | ✖ | REQ — `NOT NULL` |
| `area_sqm` | `areas.area_sqm` | numeric | ✔ | `68.50` | decimal, 2dp | `> 0` (`ck_areas_area_sqm_positive`) | none | S | ✖ | ✖ | REQ — `NOT NULL` |

### C. Unit / apartment

**No canonical table exists.** All targets below are new. See §14, D1.

| CRM field | Canonical | Type | Req | Example | Normalization | Validation | Dedup role | Owner | Abs | PII | Tier / reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `source_unit_id` | `units.source_record_id` **(new)** | text | ✔ | `U-A1-1203` | trim | non-blank | **Identity key** | S | ✖ | ✖ | REQ |
| `project_id` | derived via `area_id` | — | ✖ | — | — | must agree with area's project | none | P | ✖ | ✖ | REJ as a stored column — functionally dependent on `areas.project_id`; storing it invites divergence |
| `area_id` | `units.area_id` **(new)** | uuid | ✔ | `AR-12` | resolve | area must exist | none | S | **✔ grain** | ✖ | REQ |
| `unit_code` | `units.unit_code` **(new)** | varchar | ✔ | `A1-1203` | trim, uppercase | non-blank | **`UNIQUE (area_id, unit_code)`** — natural-key fallback | S | ✖ | ✖ | REQ |
| `unit_type` | `units.unit_type` **(new)** | varchar | ✔ | `2PN` | uppercase | non-blank | none | S | ✔ (filter) | ✖ | REQ |
| `bedrooms` | `units.bedrooms` **(new)** | int | ✖ | `2` | — | `>= 0` | none | S | ✖ | ✖ | REC |
| `area_sqm` | `units.area_sqm` **(new)** | numeric | ✖ | `68.50` | 2dp | `> 0` | none | S | ✖ | ✖ | REC |
| `price` | `units.price` **(new)** | numeric(14,2) | ✖ | `3250000000` | strip separators; **currency must be fixed by contract (VND)** | `>= 0` | none | S | ✖ | ✖ | OPT — not needed for any absorption metric; accept only if the envelope declares currency |
| `inventory_status` | `units.status` **(new)** | varchar | ✔ | `available` | lowercase | `available`/`reserved`/`sold`/`blocked`; unmapped → reject | none | S | **✔ remaining inventory** | ✖ | REQ |
| `source_updated_at` | `crm_source_records.source_updated_at` **(new)** | timestamptz | ✔ | `2026-08-07T09:12:00+07:00` | to UTC | tz required; not in future beyond skew | **Conflict resolver** | S | ✖ | ✖ | REQ |

### D. Customer / lead

| CRM field | Canonical | Type | Req | Example | Normalization | Validation | Dedup role | Owner | Abs | PII | Tier / reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `source_customer_id` | `customers.source_record_id` **(new)** | text | ✔ | `C-9001` | trim | non-blank | **Identity key — authoritative** | S | ✖ | ✖ | REQ |
| `full_name` | `customers.full_name` **(new)** | varchar | ✔ | `Nguyễn Văn A` | trim, collapse spaces | non-blank | none | S | ✖ | **✔** | REQ |
| `phone` | `customers.phone` + `phone_normalized` **(new)** | varchar | ✖ | `+84 901 234 567` | strip non-digits; `+84`/`84` → `0`; must match `^0\d{8,10}$` | format check | **Secondary dedup** — `UNIQUE (project_id, phone_normalized) WHERE NOT NULL` | S | ✖ | **✔** | REC |
| `email` | `customers.email` + `email_normalized` **(new)** | varchar | ✖ | `a@example.com` | trim, lowercase | RFC-5322-lite | **Secondary dedup** | S | ✖ | **✔** | REC |
| `budget_min` / `budget_max` | `customers.budget_min` / `budget_max` **(new)** | numeric | ✖ | `2.8e9` / `3.5e9` | strip separators | `>= 0`; `max >= min` | none | S | ✖ | ✖ | OPT — no absorption metric consumes it |
| `preferred_area_id` | `customers.preferred_area_id` **(new)** | uuid | ✖ | `AR-12` | resolve | area must exist | none | S | ✖ | ✖ | OPT |
| `preferred_unit_type` | `customers.preferred_unit_type` **(new)** | varchar | ✖ | `2PN` | uppercase | — | none | S | ✖ | ✖ | OPT |
| `customer_status` | `customers.status` **(new)** | varchar | ✖ | `active` | lowercase | `new`/`active`/`inactive` | none | S | ✖ | ✖ | REC |
| `source_updated_at` | `crm_source_records.source_updated_at` **(new)** | timestamptz | ✔ | — | to UTC | tz required | **Conflict resolver** | S | ✖ | ✖ | REQ |
| *`national_id`, `date_of_birth`, `address`, `bank_account`* | — | — | — | — | — | — | — | — | ✖ | **✔** | **REJ** — no absorption or CRM-integration purpose in this MVP; accepting them expands the PII blast radius for zero analytic value |

> **Dedup precedence.** `source_customer_id` is authoritative. Phone/email uniqueness is a *secondary guard* that must **warn**, not reject, when it disagrees with the source id — two CRM customers legitimately sharing a household phone is a real case, and silently merging them corrupts deal attribution. Emit `DUPLICATE_CONTACT` as a `warning`-severity validation error and keep both records.

### E. Customer interaction

| CRM field | Canonical | Type | Req | Example | Normalization | Validation | Dedup role | Owner | Abs | PII | Tier / reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `source_interaction_id` | `customer_interactions.source_record_id` **(new)** | text | ✔ | `I-5501` | trim | non-blank | **Identity key** | S | ✖ | ✖ | REQ (if entity synced) |
| `customer_id` | `customer_interactions.customer_id` **(new)** | uuid | ✔ | `C-9001` | resolve | customer must exist | none | S | ✖ | ✖ | REQ |
| `staff_id` | `customer_interactions.staff_id` **(new)** | uuid | ✔ | `S-14` | resolve via user map | staff must exist | none | S | ✖ | ✖ | REQ |
| `interaction_type` | `customer_interactions.interaction_type` **(new)** | varchar | ✔ | `call` | lowercase | `call`/`meeting`/`site_visit`/`message`/`email`/`note` | none | S | ✖ | ✖ | REQ |
| `interaction_at` | `customer_interactions.interaction_at` **(new)** | timestamptz | ✔ | `2026-08-06T14:00:00+07:00` | to UTC | tz required, not future | none | S | ✖ | ✖ | REQ |
| `next_follow_up_at` | `customer_interactions.next_follow_up_at` **(new)** | timestamptz | ✖ | — | to UTC | `>= interaction_at` | none | S | ✖ | ✖ | REC |
| `note` | `customer_interactions.note` **(new)** | text | ✖ | `Khách quan tâm căn góc` | trim | ≤4000 chars | none | S | ✖ | **✔** | REC — **free text may contain names/phones; treat as PII, never send to an LLM, never echo into `upload_errors.message`** |

> **Interactions contribute nothing to absorption.** Sync them only if the CRM dashboard requirement is confirmed. If the goal is absorption analytics alone, defer this entity entirely — it is the largest PII surface for the least analytic value.

### F/G. Booking / reservation and deal / sale

Bookings and deals should be **one entity with a status**, not two tables. The CRM's `reserved_at` and `sold_at` are stage timestamps of the same commercial object; splitting them creates a join that must be kept consistent for no benefit.

| CRM field | Canonical | Type | Req | Example | Normalization | Validation | Dedup role | Owner | Abs | PII | Tier / reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `source_deal_id` | `deals.source_record_id` **(new)** | text | ✔ | `D-3310` | trim | non-blank | **Identity key** | S | **✔** | ✖ | REQ |
| `customer_id` | `deals.customer_id` **(new)** | uuid | ✔ | `C-9001` | resolve | must exist | none | S | ✖ | ✖ | REQ |
| `unit_id` | `deals.unit_id` **(new)** | uuid | ✔ | `U-A1-1203` | resolve | must exist | `UNIQUE (unit_id) WHERE status IN ('reserved','sold') AND deleted_at IS NULL` | S | **✔** | ✖ | REQ |
| `assigned_to` | `deals.assigned_to` **(new)** | uuid | ✖ | `S-14` | resolve | must exist | none | S | ✖ | ✖ | REC |
| `deal_status` | `deals.status` **(new)** | varchar | ✔ | `sold` | lowercase | 8-value set (§6); unmapped → **reject the record**, never default | none | S | **✔ decisive** | ✖ | REQ |
| `reserved_at` | `deals.reserved_at` **(new)** | timestamptz | cond. | — | to UTC | required when `status='reserved'` or `'sold'` | none | S | ✔ (reserved count) | ✖ | REQ |
| `sold_at` | `deals.sold_at` **(new)** | timestamptz | cond. | `2026-08-05T10:30:00+07:00` | to UTC | **required when `status='sold'`**; `>= reserved_at` | none | S | **✔ the sold-day key** | ✖ | REQ |
| `lost_at` | `deals.lost_at` **(new)** | timestamptz | cond. | — | to UTC | required when `status IN ('lost','cancelled')` | none | S | ✔ (removes from count) | ✖ | REQ |
| `loss_reason` | `deals.loss_reason` **(new)** | text | ✖ | `Khách đổi ý` | trim | ≤500 | none | S | ✖ | ✖ | REC — free text, redact in error records |
| `sale_price` | `deals.sale_price` **(new)** | numeric(14,2) | ✖ | `3180000000` | strip separators | `>= 0` | none | S | ✖ | ✖ | OPT — **no absorption metric uses it**; accept only with a declared currency |
| `source_updated_at` | `crm_source_records.source_updated_at` **(new)** | timestamptz | ✔ | — | to UTC | tz required | **Conflict resolver** | S | ✖ | ✖ | REQ |
| *`commission`, `contract_no`, `payment_schedule`* | — | — | — | — | — | — | — | — | ✖ | ✖ | **REJ** — contracts and payments are explicitly out of scope |

### H. Inventory status

Not a separate entity. Inventory state is `units.status` (C) with `deals` (F/G) as the driver. A separate `inventory_status` feed would create a second source of truth for the same fact, violating constraint 1. **REJ as an independent entity.**

`inventory_snapshots` (existing) should be **KEPT but not written by the CRM sync** — see §6.

### I. Sales user / assignment

| CRM field | Canonical | Type | Req | Example | Normalization | Validation | Dedup role | Owner | Abs | PII | Tier / reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `source_staff_id` | `crm_source_records` → `users.id` | text | ✔ | `S-14` | trim | non-blank | **Identity key** | S | ✖ | ✖ | REQ if deals carry `assigned_to` |
| `full_name` | `users.full_name` | varchar | ✔ | `Trần B` | trim | `ck_users_full_name_not_blank` | none | S | ✖ | ✔ (employee) | REQ |
| `email` | `users.email` | varchar | ✔ | `b@corp.vn` | lowercase | `uq_users_email` | Secondary key | S | ✖ | ✔ | REQ |
| `role` | `users.role` | varchar | ✔ | `analyst` | lowercase | **`ck_users_role` allows only `admin`/`manager`/`analyst`** | none | S | ✖ | ✖ | REQ — see contradiction K1 |
| `password_hash` | — | — | — | — | — | — | — | — | ✖ | ✔ | **REJ** — never accept credentials from an external system. `users.password_hash` is `NOT NULL`; sync must write a non-loginable sentinel, exactly as `scripts/seed_dev.py` already does |

### J. Source synchronization metadata

Platform-owned (`P`) throughout; none is PII; none affects absorption directly.

| Field | Canonical | Purpose |
|---|---|---|
| `source_system` | `crm_source_records.source_system`, `upload_files.source_system` **(new col)** | Namespaces identity so a second source cannot collide |
| `source_entity` | `crm_source_records.source_entity` **(new)** | `project`/`area`/`unit`/`customer`/`interaction`/`deal`/`staff` |
| `source_record_id` | `crm_source_records.source_record_id` **(new)** | The CRM primary key |
| `source_updated_at` | `crm_source_records.source_updated_at` **(new)** | Conflict resolution clock |
| `content_hash` | `crm_source_records.content_hash` **(new)** | Detects "same version, different content" → conflict; also makes unchanged replays a cheap no-op |
| `external_batch_id` | `upload_files.external_batch_id` **(new col)** | Batch-level idempotency key |
| `schema_version` | `upload_files.schema_version` **(new col)** | Lets the contract evolve without breaking replay of old batches |

---

## 5. Minimum required CRM payload

The smallest payload that produces a correct absorption dashboard. Everything else is deferrable.

**Required entities: 3.** `area`, `unit`, `deal`.

| Entity | Required fields |
|---|---|
| `area` | `source_area_id`, `source_project_id`, `area_name`, `unit_type`, `bedrooms`, `area_sqm`, `total_units` |
| `unit` | `source_unit_id`, `source_area_id`, `unit_code`, `unit_type`, `inventory_status`, `source_updated_at` |
| `deal` | `source_deal_id`, `source_unit_id`, `deal_status`, `sold_at` (when `sold`), `reserved_at` (when `reserved`/`sold`), `lost_at` (when `lost`/`cancelled`), `source_updated_at` |

Plus `project` (`source_project_id`, `project_name`, `launch_date`) as a one-time bootstrap — projects change rarely enough that a manual `POST /api/v1/projects` is acceptable for the pilot.

**Not required for absorption:** `customer`, `customer_interaction`, `staff`, `price`, `sale_price`, `budget_*`, `preferred_*`.

> **This is the strongest cost-reduction recommendation in this document.** Deferring `customer` and `customer_interaction` removes the entire PII surface from MVP 1 ingestion: no phone/email normalization, no `DUPLICATE_CONTACT` policy, no PII redaction rules in error records, no LLM-exposure risk to audit. Absorption analytics is unaffected — `deals.customer_id` can be `NULL` in MVP 1 and backfilled later. Add customers only when a CRM-facing screen is actually committed.

---

## 6. Absorption-required fields and rules

### 6.1 Field requirements per metric

| Metric | Required fields | Currently computable? |
|---|---|---|
| Sold units per day | `deals.status`, `deals.sold_at`, `deals.unit_id` → `units.area_id` | **No** — no `deals` table. Today `sales_records.units_sold` is an imported aggregate. |
| Remaining inventory | `units.status` + `areas.total_units` | **No** — no `units` table. Today `inventory_snapshots.units_remaining` is imported, not derived. |
| Reserved units | `deals.status='reserved'` | **No** |
| Absorption rate | sold ÷ `areas.total_units` | Partially — `areas.total_units` exists |
| Velocity 7d / 30d | daily sold series | **Yes** — `AbsorptionCalculatorService._build_rows()`, `src/services/absorption.py:133` |
| Data freshness | `absorption_daily.computed_at` | **Yes** — `MAX(computed_at)` → `summary.updated_at`, `src/services/absorption.py:300` |
| Historical corrections | `deals.source_updated_at` + full recompute | Mechanism exists (`recompute()` deletes and rewrites, `absorption.py:124-128`); the input data does not |
| Cancelled / lost deals | `deals.status`, `deals.lost_at`, `deals.deleted_at` | **No** |

### 6.2 Deal-state semantics

**Only `sold` contributes to the sold-unit count.** I have no reason to recommend otherwise and do not.

| State | Sold count | Remaining inventory | Reserved count | Notes |
|---|---|---|---|---|
| `lead` | ✖ | no effect | ✖ | Pipeline only |
| `qualified` | ✖ | no effect | ✖ | |
| `interested` | ✖ | no effect | ✖ | |
| `viewing` | ✖ | no effect | ✖ | |
| `reserved` | ✖ | **excluded from available** | ✔ | Held, not sold. Reporting `reserved` as sold overstates absorption and produces a forecast that predicts sellout too early. |
| `sold` | **✔ on `date(sold_at)`** | **decrements** | ✖ | The only state that counts |
| `lost` | ✖ | returns to available | ✖ | |
| `cancelled` | ✖ | returns to available | ✖ | Treat identically to `lost`; distinguish only in reporting. If the CRM emits both, map both to a terminal-negative state and keep the raw value in `crm_source_records` for audit. |

**Reserved is deliberately not stored per day.** Reservation is a *current* state; without a CRM-side reservation event log, historical reservation levels cannot be reconstructed. Report reserved counts only as an as-of-now figure from `units.status`, never as a time series. Fabricating a reserved history from current state is the kind of silent inaccuracy that destroys trust in the dashboard.

### 6.3 Correction handling

All five cases resolve through one mechanism: **the canonical row is upserted to the incoming version, then `AbsorptionCalculatorService.recompute(project_id)` runs for the affected project.** Because `recompute()` deletes and fully rewrites `absorption_daily` for the project's areas (`src/services/absorption.py:124-128`), corrections propagate to history automatically — this existing design is well suited to CRM sync and should not be changed.

| Correction | Handling |
|---|---|
| `reserved` → `sold` | Upsert deal; `sold_at` now set; unit `reserved` → `sold`. Recompute adds 1 to `date(sold_at)`. |
| `reserved` → `lost` | Upsert; unit returns to `available`. No sold-count change (it never counted). Remaining inventory increases. |
| `sold` → `cancelled` / corrected | Upsert; unit returns to `available`. Recompute **removes** the unit from the day it was previously counted, and remaining inventory for every subsequent day rises by 1. Historical figures change — this is correct, and the dashboard must show `updated_at` so users understand which computation they are reading. |
| Unit `available` → `blocked` | Upsert `units.status`. Excluded from `total_units` for absorption-rate purposes, so the rate is not diluted by stock that was never for sale. |
| CRM record deleted / deactivated | **Tombstone: set `deleted_at`, never hard-delete.** Recompute filters `WHERE deleted_at IS NULL`. Hard deletion would destroy the audit trail required by constraint 4 and make the correction unexplainable after the fact. |

**One caveat that must be stated plainly:** `sold_at` corrections rewrite history silently. A weekly snapshot of `absorption_daily` — or at minimum retaining `computed_at` per row, which the schema already does — is the cheapest way to answer "why did last month's number change?". Recommend storing the recompute trigger (`sync_run_id`) alongside; see §10.

---

## 7. `upload_files` assessment

### Q1 — What does it currently represent?

A **single uploaded spreadsheet file** and the outcome of parsing it. Columns (revision `0001_initial_schema`, mirrored at `src/models/tables.py:49-61`):

`id`, `project_id`, `uploaded_by` (NULL), `filename`, `checksum`, `status`, `rows_ok`, `rows_failed`, `uploaded_at`.

Constraints: `pk_upload_files`, `fk_upload_files_project_id`, `fk_upload_files_uploaded_by`, `uq_upload_files_project_checksum`, `ck_upload_files_filename_not_blank`, `ck_upload_files_checksum_not_blank`, `ck_upload_files_status` (`pending`/`processing`/`completed`/`failed`), `ck_upload_files_rows_ok_nonnegative`, `ck_upload_files_rows_failed_nonnegative`.

### Q2 — Too tightly coupled to Excel/CSV?

**Yes, in three specific ways** — and only three, which is why generalization is cheap:

1. `filename` is `NOT NULL` **and** `<> ''` — an API-pushed JSON payload has no filename.
2. `checksum` is `NOT NULL` **and** `<> ''` **and** participates in `uq_upload_files_project_checksum` — this makes byte-identity the batch identity.
3. There is no column for `source_system`, `input_format`, `sync_mode`, or a source cursor.

Notably, the table is **not** coupled to Excel in its *shape*: `rows_ok` / `rows_failed` / `status` describe any batch. Nothing must be dropped.

### Q3 — Can it represent JSON input?

**Structurally yes; legally no** under the current constraints. A JSON API payload would have to invent a synthetic `filename` and a `checksum` — carrying a lie in the schema. Both constraints must be relaxed.

### Q4 — Is the name still accurate?

**No.** Once payloads arrive over an API, "upload_files" describes neither the transport nor the unit of work. The accurate concept is a **synchronization run**. However, see §12: renaming has a real, enumerable cost and is not required for MVP 1.

### Q5–Q8 — Field disposition

| Field | Disposition | Rationale |
|---|---|---|
| `id` | **Keep** | PK; target of 4 FKs |
| `project_id` | **Keep** | Sync scope; already indexed via FK |
| `uploaded_by` | **Keep**, rename later to `created_by` | Currently always NULL (no auth). For machine-initiated sync it stays NULL, which is correct. |
| `filename` | **Keep, relax to NULLable** | Meaningful for file transport, meaningless for API transport |
| `checksum` | **Keep, relax to NULLable; drop it from the uniqueness key** | Still valuable as a *duplicate-payload hint* and for debugging; must stop being the identity |
| `status` | **Keep, extend CHECK** | Add `partially_completed` — currently a batch with some rejected rows is recorded as `completed` (`src/services/import_records.py:168`), which hides partial failure from `GET /files` |
| `rows_ok` / `rows_failed` | **Keep, rename later** to `rows_accepted` / `rows_rejected` | Semantics already correct |
| `uploaded_at` | **Keep, rename later** to `started_at` | Add a separate `finished_at` |
| — | **Add** `source_system`, `source_entity`, `input_format`, `transport_mode`, `sync_mode`, `external_batch_id`, `schema_version`, `rows_received`, `finished_at`, `last_source_cursor`, `error_summary` | See §10 |
| — | **Deprecate nothing** | No column is harmful; all are either useful or nullable |

### Q9 — Is checksum deduplication sufficient?

**No — and it is actively wrong for sync.** Three failure modes, all reachable today:

1. **False positive.** An unchanged incremental export replayed after a network retry is byte-identical → `409 DUPLICATE_FILE` (`src/api/files.py:204-214`). A correct sync protocol must treat this as a successful no-op. Verified by `tests/test_api/test_files.py::test_duplicate_checksum_returns_409_and_removes_the_file`.
2. **False negative.** Two exports with overlapping date windows have different bytes → both pass the checksum gate → the second hits `uq_sales_area_date_external_id` → `IntegrityError` → **entire batch rolled back** (`tests/test_services/test_import_records.py::test_business_key_violation_rolls_back_everything`). This violates constraint 9: one duplicate record destroys every valid record in the batch.
3. **Stuck state.** `IntegrityError` is not caught in `src/jobs/parse_upload.py` (it handles only `ImportRejectedError`, `ExcelParseError`, `OSError` — lines 147, 160, 173). The transaction that set `status='processing'` (`src/services/import_records.py:136-140`) rolls back with everything else, so the row reverts to `pending` while still occupying `uq_upload_files_project_checksum`. **The batch is now permanently stuck: it will never progress, and the identical corrected file can never be re-uploaded.** This is a live defect, not a hypothetical.

### Q10 — What should the idempotency key be?

Two levels, both required:

| Level | Key | Behaviour on repeat |
|---|---|---|
| Batch | `(source_system, external_batch_id)` | Return the original run's result; do not reprocess |
| Record | `(source_system, source_entity, source_record_id)` + `source_updated_at` comparison | Upsert if newer; skip if same or older; conflict if same timestamp with different `content_hash` |

`checksum` is demoted to a diagnostic hint, not a constraint.

---

## 8. `upload_errors` assessment

Current definition (`0001_initial_schema`; mirror at `src/models/tables.py:63-72`):

`id`, `file_id` (FK, NOT NULL), `row_number` (int, NOT NULL, `> 0`), `column_name` (NULL), `error_code`, `message`, `created_at`. Index `ix_upload_errors_file_id_row_number`.

| Q | Answer |
|---|---|
| 1. Name still appropriate? | **No** — it describes transport, not the concern. The rows are validation findings. |
| 2. Rename to? | **`validation_errors`** — preferred over `sync_errors`, because the same table must serve manual file uploads that are not "sync" at all. Defer the rename (§12). |
| 3. Attach to what? | **All three, at different columns:** `sync_run_id` (batch, NOT NULL), `source_record_id` (source identity, NULL for transport/schema errors), `record_locator` (position within input). One FK is not enough — a JSON envelope error has a run but no record. |
| 4. Needed to replay/fix? | `sync_run_id`, `source_entity`, `source_record_id`, precise locator (`row_number` **or** `json_path`), `field_name`, `error_category`, `error_code`, human message, and a **redacted** value. Nothing else. |
| 5. JSON nested-field error? | `json_path` using RFC 9535 syntax: `$.records[12].deal.sold_at`. `row_number` NULL. |
| 6. CSV row/column error? | `row_number` + `field_name` (already supported as `column_name`). `json_path` NULL. |
| 7. Retain after successful retry? | **Yes — retain, and mark.** Set `resolved_at` and `retry_status='resolved'`. Deleting them destroys the evidence that a data-quality problem occurred, which is exactly what a pilot needs to measure. |
| 8. Track retries? | `retry_status` enum (`open`/`retrying`/`resolved`/`permanent`) + `resolved_at`. A retry *count* is not needed in MVP 1 — the sync run history already provides it. |

**Blocking constraint:** `row_number` is `NOT NULL` with `ck_upload_errors_row_number_positive` (`row_number > 0`). **A JSON payload error cannot be recorded at all** without relaxing this. This is the single hardest schema blocker for JSON support.

**PII rule (constraint 10).** `raw_value_redacted` must store a redacted form only — e.g. `0901****67`, `n***@example.com`, or `<redacted:note>` — never the raw value. Free-text `note` and `loss_reason` must never be echoed. Justification for the exception: a validation error is useless if the operator cannot tell which value was wrong; a masked value preserves diagnosability without duplicating PII into a table with different retention and access rules than `customers`.

---

## 9. Recommended sync / input architecture

```
CRM export or API push
        │
        ▼
[Input adapter]  ── file upload  →  stored artifact (uploads/)
                └─ API payload   →  stored artifact (uploads/)     ← EXISTS (FileUploadService)
        │
        ▼
[Sync-run tracker]  create sync run, batch idempotency check       ← PARTIAL (upload_files)
        │
        ▼
[Parser]  CSV adapter | JSON parser → canonical staging records    ← CSV EXISTS / JSON MISSING
        │
        ▼
[Schema validator]  envelope, types, required fields               ← PARTIAL (per-field only)
        │
        ▼
[Normalizer]  trim/case/phone/date-to-UTC, enum mapping            ← PARTIAL (_CONVERTERS)
        │
        ▼
[Identity & idempotency service]  resolve source id → canonical id ← MISSING
        │        compare source_updated_at, decide insert/update/skip/conflict
        ▼
[Canonical writer]  upsert in one transaction per batch            ← EXISTS but INSERT-only
        │
        ▼
[Validation-error tracker]  categorized errors                     ← PARTIAL (upload_errors)
        │
        ▼
[Absorption recalculation trigger]  recompute(project_id)          ← EXISTS
        │
        ▼
[Retry mechanism]  re-run rejected records only                    ← MISSING
```

| Component | Status | Evidence |
|---|---|---|
| Input adapter | **Exists**, file-only | `src/services/file_upload.py`; `ALLOWED_SUFFIXES` excludes `.json` (line 25) |
| Parser (CSV/Excel) | **Exists** | `src/services/excel_parser.py`, 56 tests |
| Parser (JSON) | **Missing** | — |
| Schema validator | **Partial** | Per-field via `ColumnSpec` / `_check_constraints`; no envelope validation, no schema versioning |
| Normalizer | **Partial** | `_strip_accents`, `_normalize_header`, `_to_date`, `_to_decimal`; no phone/email normalization, no enum mapping |
| Dedup / idempotency | **Missing** | Only in-file `seen_hashes` (`import_records.py:219,228`) and DB unique constraints |
| Canonical writer | **Exists, insert-only** | `_insert_rows()` — no `ON CONFLICT` |
| Sync-run tracker | **Partial** | `upload_files` covers file batches only |
| Validation-error tracker | **Partial** | `upload_errors`; no category, no JSON locator, no retry state |
| Absorption recompute | **Exists** | `AbsorptionCalculatorService.recompute()` |
| Retry mechanism | **Missing** | `errors.csv` download is a manual workaround (`src/api/files.py:328`) |
| Worker infrastructure | **Exists** | Redis + RQ, `INGEST_QUEUE` (`src/task_queue.py:14`) |

**Error taxonomy (constraint 8).** Five categories, each with a distinct disposition:

| Category | Example | Disposition |
|---|---|---|
| `transport` | unreadable file, oversize, empty | Fail whole run |
| `schema` | missing envelope field, unknown `record_type`, bad `schema_version` | Fail whole run |
| `field` | bad date, negative units, unmapped enum | Reject record, continue batch |
| `business` | area not found, `sold_at` before `reserved_at` | Reject record, continue batch |
| `conflict` | same `source_updated_at`, different `content_hash` | Reject record, continue batch, flag for human review |

Only `transport` and `schema` fail the whole run. The existing `import_error_threshold` (default `0.5`, `src/config.py:63`) remains the explicit batch-rejection policy that constraint 9 permits.

---

## 10. Proposed schema changes

One additive revision, `0005_crm_sync_ingestion`. **No drops, no renames, no data loss.**

### 10.1 Generalize `upload_files`

Every field justified; nothing added by convention.

| Change | Field | Purpose |
|---|---|---|
| ADD | `source_system` text NOT NULL DEFAULT `'manual_upload'` | Namespaces identity; the default backfills existing rows truthfully |
| ADD | `source_entity` text NULL | Which entity this run carried (`deal`, `unit`, …); NULL for multi-entity envelopes |
| ADD | `input_format` text NOT NULL DEFAULT `'csv'` — CHECK `IN ('csv','xlsx','json')` | Selects the parser; makes the run self-describing for replay |
| ADD | `transport_mode` text NOT NULL DEFAULT `'file_upload'` — CHECK `IN ('file_upload','api_push')` | Distinguishes a human upload from a machine sync; drives whether `filename` is expected |
| ADD | `sync_mode` text NOT NULL DEFAULT `'full_snapshot'` — CHECK `IN ('full_snapshot','incremental')` | Determines whether absent records are tombstoned |
| ADD | `schema_version` text NOT NULL DEFAULT `'1.0'` | Lets the contract evolve without breaking replay of stored artifacts |
| ADD | `external_batch_id` text NULL | Batch idempotency key from the CRM |
| ADD | `rows_received` int NOT NULL DEFAULT 0 | Today only `rows_ok` + `rows_failed` are stored; records *skipped* as unchanged are invisible, so acceptance rate cannot be computed |
| ADD | `finished_at` timestamptz NULL | `uploaded_at` is a start time; run duration is currently underivable |
| ADD | `last_source_cursor` text NULL | High-water mark for the next incremental run |
| ADD | `error_summary` jsonb NOT NULL DEFAULT `'{}'` | Per-category counts, so `GET /files` shows failure shape without a second query |
| ADD | UNIQUE `(source_system, external_batch_id) WHERE external_batch_id IS NOT NULL` | The real batch idempotency key |
| ALTER | `filename` → NULLable; drop `ck_upload_files_filename_not_blank` | API pushes have no filename |
| ALTER | `checksum` → NULLable; drop `ck_upload_files_checksum_not_blank` | Same |
| **DROP** | `uq_upload_files_project_checksum` | **The one genuinely blocking constraint.** Replaced by the batch key above. Re-add as a non-unique index for diagnostics. |
| ALTER | `ck_upload_files_status` → add `'partially_completed'` | Distinguishes "all rows loaded" from "some rows rejected" |

> **This is the only constraint removal recommended in this document.** It is not destructive: no data is lost, and the anti-duplicate guarantee moves to a key that is correct for sync. Existing rows keep their checksums.

Fields listed in the brief but **not recommended**: none omitted, except that `created_by` is not added — `uploaded_by` already exists and serves the purpose; adding a second actor column would create ambiguity.

### 10.2 Generalize `upload_errors`

| Change | Field | Purpose |
|---|---|---|
| ADD | `source_entity` text NULL | Which entity the failing record belongs to |
| ADD | `source_record_id` text NULL | Lets an operator find the record in the CRM |
| ADD | `record_locator` text NULL | Human-readable position (`"records[12]"`, `"sheet1!A14"`) for the error report |
| ADD | `json_path` text NULL | RFC 9535 path for JSON errors |
| ADD | `field_name` text NULL | Format-neutral alias; `column_name` retained for CSV compatibility |
| ADD | `error_category` text NOT NULL DEFAULT `'field'` — CHECK `IN ('transport','schema','field','business','conflict')` | Constraint 8 |
| ADD | `raw_value_redacted` text NULL | Diagnosability without PII duplication (§8) |
| ADD | `retry_status` text NOT NULL DEFAULT `'open'` — CHECK `IN ('open','retrying','resolved','permanent')` | Retry tracking |
| ADD | `resolved_at` timestamptz NULL | Retention with resolution marking |
| ALTER | `row_number` → NULLable; drop `ck_upload_errors_row_number_positive` | **Required for JSON.** Replace with CHECK `(row_number IS NULL OR row_number > 0)` |
| ADD | CHECK `(row_number IS NOT NULL OR json_path IS NOT NULL OR error_category IN ('transport','schema'))` | Every record-level error must be locatable |

`file_id` is **kept as-is** — it already means "the run this error belongs to". Rename to `sync_run_id` only when the parent table is renamed.

### 10.3 New table `crm_source_records`

The missing piece. Satisfies constraints 4, 5, 6, 7.

| Column | Type | Purpose |
|---|---|---|
| `id` | uuid PK | |
| `source_system` | text NOT NULL | Identity namespace |
| `source_entity` | text NOT NULL | `project`/`area`/`unit`/`customer`/`interaction`/`deal`/`staff` |
| `source_record_id` | text NOT NULL | CRM primary key |
| `canonical_table` | text NOT NULL | Which table the record landed in |
| `canonical_id` | uuid NOT NULL | The canonical row |
| `source_updated_at` | timestamptz NULL | Conflict resolution clock (NULL when the CRM provides none) |
| `content_hash` | text NOT NULL | Unchanged-replay detection and same-timestamp conflict detection |
| `first_sync_run_id` | uuid NOT NULL FK → `upload_files.id` | Provenance |
| `last_sync_run_id` | uuid NOT NULL FK → `upload_files.id` | Most recent touch |
| `first_seen_at` / `last_seen_at` | timestamptz NOT NULL | Freshness per record |
| `deleted_at` | timestamptz NULL | Tombstone |

- UNIQUE `(source_system, source_entity, source_record_id)` — **the record-level idempotency key**
- UNIQUE `(canonical_table, canonical_id)` — one canonical row has at most one source identity
- Index `(last_sync_run_id)`, `(source_entity, last_seen_at)`

This design deliberately keeps source identity **out of** the business tables, so `sales_records` / `areas` / future `deals` stay clean and a second source system can be added later without schema change.

### 10.4 CRM business tables

`units`, `deals` (± `customers`, `customer_interactions`) must be created before per-deal absorption is possible. Their columns are specified in `SRS.md` §5.2.8 as revised earlier in this session. **This document does not restate or alter them** — but note the design conflict in §14, D1: that SRS section models them as in-app data entry, whereas this brief models them as CRM-sourced. The column set is largely the same; the *ownership* is not.

---

## 11. API and payload recommendation

### Contract: JSON canonical, CSV adapter

| Aspect | Specification |
|---|---|
| Transport modes | `file_upload` (multipart, existing route) and `api_push` (JSON body) |
| Formats | `application/json` (canonical); `text/csv`, `.xlsx` (adapter, entities `unit` and `deal` only) |
| Batch identifier | `batch_id` in envelope → `upload_files.external_batch_id` |
| Source system id | `source_system` in envelope |
| Schema version | `schema_version` in envelope; unknown major version → `422 UNSUPPORTED_SCHEMA_VERSION` |
| Record type | `entity` in envelope; one entity per batch (keeps ordering and error attribution simple) |
| Idempotency | Repeat `(source_system, batch_id)` → `200` with the original run's summary, no reprocessing |
| Error response | `202` with `sync_run_id`; per-record errors retrieved via `GET /api/v1/sync-runs/{id}/errors` — mirrors the existing `GET /files/{id}/errors` contract |
| Retry | Re-send only rejected records under a new `batch_id`; accepted records are skipped as unchanged |
| Full vs incremental | `sync_mode` in envelope; `full_snapshot` tombstones absent records for that entity + project |
| Conflict resolution | Last-writer-wins by `source_updated_at`; equal timestamp + differing `content_hash` → `conflict` error, record rejected, human review |
| Freshness expectation | Daily; `GET /absorption/summary.updated_at` already surfaces it |

### JSON envelope (illustrative — not an implementation)

```json
{
  "schema_version": "1.0",
  "source_system": "mini_crm",
  "batch_id": "2026-08-08T02:00:00Z#deal#001",
  "entity": "deal",
  "sync_mode": "incremental",
  "cursor": { "since": "2026-08-07T02:00:00Z", "until": "2026-08-08T02:00:00Z" },
  "project_ref": { "source_project_id": "PRJ-001" },
  "records": [
    {
      "source_deal_id": "D-3310",
      "source_unit_id": "U-A1-1203",
      "deal_status": "sold",
      "reserved_at": "2026-07-28T09:00:00+07:00",
      "sold_at": "2026-08-05T10:30:00+07:00",
      "source_updated_at": "2026-08-07T16:20:00+07:00"
    },
    {
      "source_deal_id": "D-3311",
      "source_unit_id": "U-A1-1204",
      "deal_status": "cancelled",
      "reserved_at": "2026-07-30T09:00:00+07:00",
      "lost_at": "2026-08-06T11:00:00+07:00",
      "loss_reason": "Khách đổi ý",
      "source_updated_at": "2026-08-07T16:21:00+07:00"
    }
  ]
}
```

### CSV adapter (illustrative)

The adapter maps flat rows into the same canonical record shape; envelope fields move to form fields on the existing multipart route (`entity`, `sync_mode`, `batch_id`, `schema_version`), so `POST /api/v1/files/upload` needs no new route — only new form parameters.

```csv
source_deal_id,source_unit_id,deal_status,reserved_at,sold_at,lost_at,loss_reason,source_updated_at
D-3310,U-A1-1203,sold,2026-07-28T09:00:00+07:00,2026-08-05T10:30:00+07:00,,,2026-08-07T16:20:00+07:00
```

Both formats converge before validation: the JSON parser and the CSV adapter each emit the staging record shape that `ImportService` already consumes (`src/services/excel_parser.py:701-745`). **One validator, one writer, one error model.**

---

## 12. Migration impact

### 12.1 Inventory of upload coupling

| Coupling | Location | Impact of generalization |
|---|---|---|
| FK `fk_upload_errors_file_id` | `upload_errors.file_id` NOT NULL | None — table kept |
| FK `fk_sales_records_file_id` | `sales_records.file_id` NOT NULL | None — table kept |
| FK `fk_inventory_snapshots_file_id` | `inventory_snapshots.file_id` NOT NULL | None — table kept |
| FK `fk_forecasts_file_id` | `forecasts.file_id` NOT NULL | None now; see D2 in §14 |
| Index `ix_forecasts_file_id` | `0001` | None |
| Index `ix_upload_errors_file_id_row_number` | `0001` | None |
| Core mirrors | `src/models/tables.py:49-72` | Add columns |
| API | `src/api/files.py` (whole module) | Add form params; add JSON route |
| Schemas | `src/models/schemas.py:22-88` (`DB_STATUS_TO_API`, `UploadAccepted`, `FileStatus`, `FileSummary`) | Extend for `partially_completed` |
| Service | `src/services/import_records.py` | Add upsert path |
| Job | `src/jobs/parse_upload.py` | Add `IntegrityError` handling |
| Tests | 21 in `test_files.py`, 26 in `test_import_records.py` | Additive; existing assertions hold except the checksum-409 test |
| Docs | `SRS.md` §5.2/§6/§9, `PRD.md`, `pipeline_status.md` | Text only |

### 12.2 Rename safety

Renaming `upload_files` → `sync_runs` in PostgreSQL is **data-preserving**: `ALTER TABLE ... RENAME TO` carries indexes, constraints, and inbound FKs automatically; only constraint *names* remain stale (`fk_sales_records_file_id` would still be so named). No data is lost.

**But it is not free**, and not needed for MVP 1: it touches `src/models/tables.py`, `src/api/files.py`, `src/services/import_records.py`, `src/models/schemas.py`, ~47 tests, and four documents. **Recommendation: defer.** Do the additive revision now; rename in a later revision once the file-upload route is retired, at which point the constraint names should be renamed in the same migration.

### 12.3 What must NOT be done

- **Do not drop `sales_records` or `inventory_snapshots`.** They hold the only sales data the system has, and `AbsorptionCalculatorService` reads `sales_records` directly (`src/services/absorption.py:112-122`). If per-deal ingestion is adopted, run both in parallel and cut over after `absorption_daily` matches.
- **Do not make `forecasts.file_id` nullable in this revision.** `forecasts` has zero rows and `src/jobs/forecast.py` is a stub, so nothing breaks today. Changing it is an MVP 2 decision (§14, D2).
- **Do not hard-delete tombstoned records.**

---

## 13. Implementation plan

| Area | Current state | Recommended change | Priority | Migration? | Code impact |
|---|---|---|---|---|---|
| Database schema | File-oriented; no source identity | Generalize `upload_files` + `upload_errors`; add `crm_source_records` | **P0** | **Yes** — `0005` additive | New table + 23 columns |
| Alembic | 4 revisions, head `0004` | One revision `0005_crm_sync_ingestion` with working `downgrade()` | **P0** | Yes | 1 new file |
| Models | `src/models/tables.py` mirrors 8/21 tables | Add new columns; add `crm_source_records` mirror | **P0** | No | ~60 lines |
| Pydantic schemas | `UploadAccepted`, `FileStatus`, `FileSummary` | Add `SyncEnvelope`, `SyncRunSummary`; extend status enum | **P0** | No | ~120 lines |
| API routes | `POST /files/upload` only | Add form params; add `POST /api/v1/sync/{entity}`; add `GET /sync-runs/{id}`, `/errors` | **P0** | No | `src/api/files.py` + new module |
| Services | `ImportService` insert-only | Add `SourceIdentityService` (resolve/upsert/conflict); convert writer to `ON CONFLICT DO UPDATE` | **P0** | No | 1 new service, 1 rewritten method |
| Services | No JSON parser | Add `JsonPayloadParser` emitting the existing staging shape | **P0** | No | ~200 lines |
| Workers | `run_parse_upload` handles 3 exception types | Catch `IntegrityError`; write terminal `failed` status **outside** the rolled-back transaction | **P0** — fixes the stuck-batch defect | No | `src/jobs/parse_upload.py` |
| Absorption | Reads `sales_records` | Once `deals` exists, read `deals` + `units`; add `units_remaining` to `absorption_daily` | **P1** | Yes | `src/services/absorption.py` |
| Tests | 329 passing | Add: replay is a no-op; overlapping batch does not roll back; `source_updated_at` conflict; tombstone excluded from recompute; JSON error has `json_path`, NULL `row_number` | **P0** | No | ~25 tests |
| Documentation | SRS §5.2 contradicts this brief | Resolve D1 (§14) before writing anything further | **P0** | No | SRS/PRD |
| Security | No auth; PII not yet present | Defer `customer` entity (§5); add PII redaction helper before it lands | **P0** | No | Small module |
| Observability | Structured logs exist (`structlog`) | Log per-run counts by error category; expose `rows_received/accepted/rejected` on `GET /sync-runs` | **P1** | No | Small |

**Sequencing.** Fix the stuck-batch defect and add `ON CONFLICT` **first** — they are prerequisites for any sync, and both are small. `crm_source_records` second. JSON parser third. CRM business tables only after D1 is decided.

---

## 14. Risks and unresolved decisions

| # | Decision / risk | Why it matters | Recommendation |
|---|---|---|---|
| **D1** | **`SRS.md` §5.2 (revised earlier this session) specifies an in-app mini CRM with Sales Staff typing data directly; this brief specifies an external enterprise-owned mini CRM feeding CSV/JSON.** These are different products with different ownership models. | Determines whether `customers`/`units`/`deals` are platform-owned (editable in-app) or source-owned (read-only mirrors). Nearly every recommendation above branches on it. **This is the blocking decision.** | Decide before writing code. If the CRM is external, `SRS.md` §5.2 must be re-revised — the two cannot both be MVP 1. |
| **D2** | `forecasts.file_id` is `NOT NULL` FK → `upload_files` (`fk_forecasts_file_id`, `0001`), and SRS §5.7.7 builds the audit chain `APPROVAL → PROPOSAL → SUGGESTION → FORECAST → UPLOAD_FILE`. | Under sync, a forecast derives from *many* runs, not one file. | **Cross-version dependency — MVP 2 concern, listed separately per constraint 13.** `forecasts.data_cutoff_date` already exists and is the better anchor. No action in MVP 1: `forecasts` is empty and its job is a stub. |
| **D3** | Canonical grain: keep area/day aggregates (`sales_records`) or move to per-unit/per-deal? | Per-deal enables reserved counts, per-unit inventory, and correct corrections. Aggregates cannot express "this specific sale was cancelled". | Move to per-deal — but run both in parallel and cut over only when `absorption_daily` matches. |
| **D4** | `ck_users_role` allows only `admin`/`manager`/`analyst`; docs claim `sales_staff`/`sales_manager`/`viewer`. | CRM staff sync will fail the CHECK. | Decide the role vocabulary before syncing staff. Requires a migration either way. |
| **D5** | Does the mini CRM emit `source_updated_at`? | Without it there is **no conflict policy** — only "last batch wins", which corrupts data on out-of-order delivery. | **Confirm with the CRM owner first.** If unavailable, fall back to `content_hash`-only change detection and document that out-of-order batches are unresolvable. |
| **D6** | Does the CRM emit tombstones, or must deletion be inferred from full snapshots? | Inference requires periodic full snapshots; without either, deleted deals inflate absorption forever. | Require one of the two. This is a hard requirement, not a preference. |
| **D7** | Timezone of `sold_at` and the project's business day boundary. | `date(sold_at)` determines which day a sale counts in. UTC vs Asia/Ho_Chi_Minh shifts sales across day boundaries — visible in a 7-day velocity. | Require timezone-aware timestamps in the contract; fix the reporting timezone per project. |
| **D8** | Currency for `price` / `sale_price`. | Neither field is used by any absorption metric today. | Reject both from MVP 1. Add later with an explicit currency field. |
| **R1** | Absorption history changes silently when the CRM corrects a past deal. | Users will ask why last month's number moved. | Surface `updated_at` prominently (already returned by `/absorption/summary`); consider storing `sync_run_id` on `absorption_daily`. |
| **R2** | `import_error_threshold` default `0.5` means a batch that is 49% bad still loads. | Reasonable for hand-made spreadsheets; too permissive for a machine-generated feed. | Lower to ~0.02 for `transport_mode='api_push'`; keep `0.5` for human uploads. |
| **R3** | 4 NOT NULL FKs point at `upload_files`. | Constrains future refactoring. | Additive generalization avoids the issue entirely; revisit at rename time. |
| **A1** | *Assumption:* the mini CRM can emit a stable per-entity primary key. | Every recommendation depends on it. | **Verify first.** If IDs are unstable, fall back to natural keys (`(area_id, unit_code)` for units) and accept that deletion detection becomes unreliable. |
| **A2** | *Assumption:* pilot scope is one project. | Multi-project sync would need `project_ref` resolution per record rather than per batch. | Envelope-level `project_ref` is sufficient for the pilot. |

---

## 15. Final recommendation per table

| Table | Verdict | Action | Migration |
|---|---|---|---|
| **`upload_files`** | **GENERALIZE** | Add 11 columns; relax `filename` / `checksum` to NULLable; **drop `uq_upload_files_project_checksum`** (the one blocking constraint); add UNIQUE `(source_system, external_batch_id)`; extend status CHECK. Defer rename to `sync_runs`. | `0005`, additive + 3 constraint relaxations. No data loss. |
| **`upload_errors`** | **GENERALIZE** | Add 9 columns; relax `row_number` to NULLable (**required for JSON**); add `error_category` CHECK; add a locator-presence CHECK. Defer rename to `validation_errors`. | `0005`, additive + 1 relaxation. No data loss. |
| **`crm_source_records`** | **ADD (new)** | The source-identity and idempotency map. Without it, constraints 4, 5, 6, and 7 are unsatisfiable. | `0005`, new table. |
| **`sales_records`** | **KEEP unchanged** | Retain as-is. Under per-deal ingestion it becomes a derived aggregate, not a sync target. Do not drop — it holds all existing sales data and `AbsorptionCalculatorService` reads it directly. | None |
| **`inventory_snapshots`** | **KEEP unchanged** | Same reasoning. Live inventory moves to `units.status`; snapshots remain useful as a historical series. | None |
| **`absorption_daily`** | **KEEP; extend later** | Add `units_remaining` when per-unit inventory lands (P1, not P0). | Later revision |
| **`forecasts`** | **KEEP unchanged** | `file_id` NOT NULL is an MVP 2 problem (D2). Table is empty; the forecast job is a stub. | None in MVP 1 |
| **`projects` / `areas`** | **KEEP unchanged** | Already suitable as CRM sync targets. | None |
| **`units` / `deals` / `customers` / `customer_interactions`** | **CREATE — pending D1** | Required for per-deal absorption. Do not create until the in-app-vs-external CRM question is settled. | Later revision |

---

*This document is analysis only. No source file, migration, schema, test, or existing document was modified in producing it.*
