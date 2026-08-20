# Analytics exports — `datasets/synthetic_v1/exports`

Produced by `scripts/export_synthetic_analytics.py` (read-only SELECTs) from a throwaway
PostgreSQL container loaded with `datasets/synthetic_v1` and then run through the real
absorption and ranking pipelines. Scope is pinned to `source_instance_id='synthetic-csv-v1'`,
so the migration-seeded fixtures (0019/0021/0023) that share these tables are excluded.

| File | Rows | Columns | sha256 (short) |
|---|---|---|---|
| `units_ranking.csv` | 200 | 69 | `c17a889fdd39ec12` |
| `leads_deals_agents.csv` | 131 | 36 | `e3c4ccfef826b45a` |
| `absorption_forecasting.csv` | 542 | 32 | `bbe2c87ac6f2fb75` |
| `ranking_scores.csv` | 200 | 13 | `d4addfb7ed6280c5` |
| `feature_dictionary.csv` | 180 | 6 | `4eac7e16dc9b7012` |

## Column reference

Full detail (every column: dtype, source table, calculation logic) is in `feature_dictionary.csv`.
Summary by file:

### `units_ranking.csv` — 69 columns

Source tables: `areas`, `projects`, `ranking_configs`, `ranking_scores`, `units`.

| Column | Logic |
|---|---|
| `is_available` | 1 when units.status='available' (the unit_available feature's raw form) |
| `has_active_deal` | 1 when the unit has a live reserved or sold deal |
| `funnel_deals_total` | count of live deals in (lead, qualified, interested, viewing) |
| `score_percent` | src.ranking.bands.as_percent: score*100 rounded to 1dp |
| `band` | src.ranking.bands.band_for: high>=0.66, medium>=0.33, else low |
| `was_skipped` | always 0: ranking_scores stores only units that passed min_weight_coverage |
| `feat_unit_available_value` | 1.0 if available else 0.0 |
| `feat_unit_demand_norm_value` | min(funnel_deals_total / DEMAND_SATURATION=3, 1) |
| `feat_area_velocity_norm_value` | min((area sold in 30d / live mirrored units) / VELOCITY_SATURATION=0.20, 1) |
| `feat_area_conversion_norm_value` | area sold deals / area live deals |
| `features_missing_count` | count of features whose contribution source is missing_defaulted/missing_skipped |

**Requested but absent from the schema (7)** — not emitted as empty columns, because that
would imply the system tracks them:

| Column | Why it does not exist |
|---|---|
| `bathrooms` | no bathroom column on units or areas |
| `price` | no price column anywhere in the schema; Mini CRM states it carries no prices |
| `price_per_sqm` | derived from price, which does not exist |
| `floor` | no floor column on units |
| `orientation` | no orientation column on units |
| `view` | no view column; `view_quality` exists only as a survey feature key with no data |
| `confidence` | feature_snapshots.confidence exists but the ranking pipeline never populates it (0/616 rows non-null) |

### `leads_deals_agents.csv` — 36 columns

Source tables: `areas`, `deals`, `projects`, `units`.

| Column | Logic |
|---|---|
| `funnel_stage` | derived from deals.status: funnel|holding|closed_won|closed_lost |
| `deals_on_same_unit` | count of deals sharing this unit - proxy for interaction volume |
| `days_reserved_to_sold` | sold_at - reserved_at in days, NULL when either is absent |

**Requested but absent from the schema (16)** — not emitted as empty columns, because that
would imply the system tracks them:

| Column | Why it does not exist |
|---|---|
| `lead_id` | no lead entity; a lead is a deals row with status='lead' |
| `customer_id` | no customer table exists in either service |
| `lead_source` | not modelled |
| `budget` | not modelled |
| `financing_status` | not modelled |
| `preferred_project` | requires a customer entity, which does not exist |
| `preferred_area` | requires a customer entity, which does not exist |
| `preferred_unit_type` | requires a customer entity, which does not exist |
| `preferred_bedrooms` | requires a customer entity, which does not exist |
| `preferred_price_range` | requires a customer entity and prices; neither exists |
| `contact_timestamps` | no contact/interaction table |
| `next_follow_up_at` | not modelled |
| `viewing_appointment` | only deals.status='viewing' exists; no appointment record |
| `interaction_counts` | no interaction table; `deals_on_same_unit` is the nearest available proxy |
| `notes` | no free-text field on deals; Mini CRM carries no PII by design |
| `assigned_sales_agent` | not modelled; `agent_*` tables are the AI advisory agent, not sales staff |

### `absorption_forecasting.csv` — 32 columns

Source tables: `absorption_daily`, `areas`, `projects`.

| Column | Logic |
|---|---|
| `is_active_lineage` | 1 when this row's calculator equals projects.absorption_calculator |
| `live_units_mirrored` | count of non-deleted units in the area, as of export |
| `area_conversion_norm` | area sold deals / area live deals, same definition as the ranking feature |

**Requested but absent from the schema (7)** — not emitted as empty columns, because that
would imply the system tracks them:

| Column | Why it does not exist |
|---|---|
| `unit_id` | absorption_daily is area-level; it has no unit dimension |
| `released_units` | 'released' is not a unit status (available/reserved/sold/blocked) |
| `price_metrics` | no price column anywhere |
| `weekly_absorption` | not persisted; only daily + rolling 7d/30d exist |
| `monthly_absorption` | not persisted; only daily + rolling 7d/30d exist |
| `inventory_aging` | units carry no status-change timestamp, so time-in-status cannot be derived |
| `forecast_target` | forecasting is a stub (src/jobs/forecast.py returns zero rows); no forecast output exists to export |

### `ranking_scores.csv` — 13 columns

Source tables: `areas`, `projects`, `ranking_scores`, `units`.

