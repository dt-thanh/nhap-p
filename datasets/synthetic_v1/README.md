# `datasets/synthetic_v1`

Synthetic, fully relational CSV dataset for local development against the
AbsorptionForecast backend schema at Alembic head `0026_cloudinary_cover_images`.

**Every row is fabricated from the schema.** No live, dev, staging, or production
database was read, queried, or exported to build it. There are no real names,
emails, phone numbers, addresses, credentials, tokens, or secrets — identities are
deterministic strings in a reserved namespace.

- Generator: `scripts/generate_synthetic_dataset.py`
- Reference date: `2026-08-18` (UTC anchor for all relative timestamps)
- Tables: **9**, rows: **432**
- Regenerate: `python -m scripts.generate_synthetic_dataset --reference-date YYYY-MM-DD`

## Conventions

| Aspect | Value |
|---|---|
| Encoding | UTF-8, `\n` line endings, header row per file |
| NULL marker | `\N` (PostgreSQL `COPY` default) — an empty field is a literal empty string |
| Timestamps | ISO-8601 UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| Dates | `YYYY-MM-DD` |
| IDs | UUIDv5, deterministic — same input always yields the same UUID |
| Source namespace | `source_system=synthetic_csv`, `source_instance_id=synthetic-csv-v1` |
| External ID prefix | `syn1-` |

The namespace is deliberately disjoint from the ones existing seed migrations own
(`ai-dev-fixture` in 0019, `synthetic-demo-2026` in 0023, `mini-crm-dev` for the
relay), so this dataset can be loaded into an already-migrated database without
colliding on `uq_units_source_identity` and friends.

## Entity counts

| Table | Rows |
|---|---|
| `projects` | 3 |
| `areas` | 9 |
| `upload_files` | 5 |
| `upload_errors` | 5 |
| `crm_source_records` | 7 |
| `units` | 200 |
| `deals` | 131 |
| `sales_records` | 36 |
| `inventory_snapshots` | 36 |

## Load order

See `load_order.txt`. FK-safe order:

```
1. projects
2. areas
3. upload_files
4. upload_errors
5. crm_source_records
6. units
7. deals
8. sales_records
9. inventory_snapshots
```

Example (psql, disposable database only):

```sql
\copy projects FROM 'projects.csv' WITH (FORMAT csv, HEADER true, NULL '\N');
```

## Shape of the data

Three projects exercising different lineages:

- **`syn1-P-001` Harbor Crest Residences** — `domain_units_deals`. Four areas spanning
  a hot area (saturated 30-day velocity, units with 3+ funnel deals), a moderate one,
  a slow one with zero recent sales, and one with **no deals at all** so the ranking
  engine's `neutral` missing-policy path is exercised.
- **`syn1-P-002` Willow Park Gardens** — `legacy_aggregate`. Carries both lineages:
  `units`/`deals` *and* `sales_records`/`inventory_snapshots` ingested through
  `upload_files`, so the parallel-run comparison has two real sides to diff.
- **`syn1-P-003` Quarry Fields Commons** — edge cases: a blocked-heavy area, and one
  **legacy area** with `external_id`/`source_*` NULL (a pre-Phase-D row, still valid
  and still rankable).

Deals span ~120 days so the absorption series is long enough to leave
`data_quality_status='warning'` and reach `'ok'` (the domain calculator marks the
first 30 points `warning`). `crm_source_records` carries one row per sync decision
(`insert`/`update`/`skip_stale`/`duplicate_noop`/`conflict`/`tombstone`) and both
mirror states, and `upload_errors` covers four error categories and four retry
statuses.

## Intended use

1. Load into a **disposable** database (never a shared or dev volume without a
   deliberate decision).
2. Run absorption recompute to populate `absorption_daily` — the dataset ships the
   *inputs*, not the computed output.
3. Run ranking (`POST /api/v1/ranking/run`, or `src.ranking.service.run_ranking`) to
   populate `feature_snapshots` / `ranking_runs` / `ranking_scores`.
4. Only then generate advisory rows through `POST /api/v1/agent/recommendations`,
   which needs a real `ranking_run_id`.

`validation_report.md` records the ranking bands this dataset actually produces,
computed with the real `src.ranking.engine` scorer.

## Excluded tables and why

| Table(s) | Reason |
|---|---|
| `alembic_version` | Alembic bookkeeping. |
| `users`, `user_areas`, `refresh_tokens`, `audit_logs`, `settings` | Dead orphan island — zero application references. Backend auth is static env tokens, so no user rows exist at runtime. `created_by`/`reviewed_by`/`uploaded_by` are therefore NULL. |
| `forecasts`, `forecast_jobs`, `forecast_points`, `alerts`, `explanations`, `llm_calls`, `suggestions`, `proposals`, `approvals` | Deprecated forecast FK island. `run_daily_forecast` is a stub that computes nothing; fabricating forecast rows would invent model output that no code produced. |
| `sync_credentials` | Stores credential hashes. Never synthesised. |
| `sync_payloads` | Sync internals — raw envelope bodies, plus an FK to `sync_credentials`. |
| `absorption_daily`, `calculator_comparisons` | Computed outputs of the absorption calculators. Shipping them would let stale numbers contradict the inputs. |
| `feature_snapshots`, `ranking_runs`, `ranking_scores` | Computed outputs of the ranking service, which is the single sanctioned writer (`tests/test_ranking_boundary.py`). |
| `ranking_configs` | Already seeded by migrations 0014 (v1) and 0022 (v2). A CSV row would violate `uq_ranking_configs_version`. |
| `reconciliation_runs`, `reconciliation_findings` | Outputs of a reconciliation run. |
| `agent_recommendations`, `agent_executions`, `sales_campaigns`, `sales_campaign_units` | Require a real `ranking_run_id` (NOT NULL FK to a generated table). Including them would break the guarantee that every child row's parent exists in this CSV set. |
