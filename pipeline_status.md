# Pipeline Status

## Date

2026-08-15 (b) (mục mới nhất — xem "Đợt 2026-08-15 (b)" ngay dưới đây). Các mục
"Đợt ..." cũ hơn bên dưới được GIỮ NGUYÊN làm lưu trữ lịch sử, không mục nào bị
xoá hay sửa.

---

## Current system flow (verified 2026-08-17)

The backend is under `src/`; there are no `src/pipelines/` or `src/workers/`
directories. RQ jobs in `src/jobs/` run in `src/worker.py`; APScheduler in
`src/scheduler.py` only enqueues jobs. The API is mounted below `/api/v1` by
`src/main.py`.

## Pipeline Flow

### Stage 1: Intake and contract gates
- **Trigger**: `POST /api/v1/sync/{entity}` in `src/api/sync.py::start_sync`, or `POST /api/v1/files/upload` in `src/api/files.py::upload_file`.
- **Input**: JSON envelope from CRM, or Excel/CSV file saved by `src/services/file_upload.py::FileUploadService.save`.
- **Process**: JSON is byte-measured by `src/services/sync_payloads.py::measure`, authenticated, validated by `src/services/contract_validation.py::ContractValidator` / `contract_validation_v2.py::ContractValidatorV2`, adapted by `src/services/contract_adapter.py::adapt` / `adapt_v2`, and parsed by `src/services/json_payload.py::JsonPayloadParser`. File uploads validate project UUID, template, suffix, size, and checksum.
- **Output**: A normalized `SyncEnvelope` for JSON, or an `upload_files` row with `status='pending'` and an RQ job for file parsing.
- **Error handling**: JSON returns 400 for malformed JSON, 413 for oversized payloads, 401/403 for credential failures, 422 for contract/envelope failures, and 500 for unavailable contract schemas. File upload returns 415 for unsupported suffix, 413/422 for file validation, 409 for duplicate checksum, and 503 if enqueue fails; an enqueue failure removes the file and pending row.
- **Status tracking**: `upload_files.uploaded_at` is the intake timestamp; file status begins at `pending`. API status is `202 Accepted` for a new file and `202 Accepted` for a new JSON sync (`200 OK` for replay). No websocket or event-stream status channel exists.

### Stage 2: JSON sync idempotency, source identity, and projection
- **Trigger**: `src/services/sync_runs.py::SyncRunService.run`, called by `start_sync`.
- **Input**: Normalized `SyncEnvelope`; optional retained raw payload and credential ID.
- **Process**: Resolves the project, detects an existing `(source_system, source_instance_id, external_batch_id)`, creates the run, then `apply_records` uses `src/services/source_identity.py::SourceIdentityService`, `src/services/history_guard.py`, and `src/services/domain_projection.py::DomainProjector`. Each record is isolated by a nested transaction and deterministic identity locks. Decisions include insert, update, stale skip, duplicate no-op, conflict, and tombstone.
- **Output**: `upload_files` stores the batch result; `sync_payloads` stores the retained raw payload; `crm_source_records` stores source identity/state; projections write `projects`, `areas`, `units`, and `deals`; row errors write `upload_errors`.
- **Error handling**: Per-record failures are persisted without rolling back valid records. `_terminal_status` returns `completed`, `completed_with_conflicts`, `partially_completed`, or `failed`. An unexpected failure calls `SyncRunService._finalize_failure` in a separate transaction and marks the run failed. `reprocess` only accepts `failed` or `partially_completed` runs with a retained payload; it clears prior errors before retrying.
- **Status tracking**: `upload_files.status`, `rows_received`, `rows_ok`, `rows_failed`, `error_summary`, `uploaded_at`, and `finished_at`. `crm_source_records.last_decision`, `state`, `last_seen_at`, `conflict_detected_at`, and `deleted_at` track identity outcomes. The request returns `202 Accepted` for a new run and `200 OK` for an idempotent replay; polling is `GET /api/v1/sync-runs/{sync_run_id}` (`200 OK`).

### Stage 3: File parse, validation, and relational import
- **Trigger**: RQ job `src.jobs.parse_upload::run_parse_upload`, enqueued by `src/api/files.py::upload_file` on `INGEST_QUEUE`.
- **Input**: Stored upload path plus template `sales`, `inventory`, or `areas`.
- **Process**: `src/services/excel_parser.py::ExcelParserService.parse_to_csv` streams the source into a staging CSV. `src/services/import_records.py::ImportService.load` resolves area names once, validates rows, deduplicates hashes/keys, and performs transactional batch writes.
- **Output**: `areas`, `sales_records`, or `inventory_snapshots`; parse/import errors go to `upload_errors`; `upload_files` is finalized. On successful import, `src/services/absorption.py::AbsorptionCalculatorService.recompute` is called in the same job.
- **Error handling**: Row errors remain attached to the file. `ImportRejectedError` rolls back the import when the configured error threshold is exceeded. Structural parse errors, integrity errors, unreadable files, and unknown templates are converted by `_failed` into `upload_files.status='failed'` plus one persisted error. The upload API itself has no explicit RQ retry setting; the status/error endpoints are the recovery surface.
- **Status tracking**: `upload_files.status` transitions `pending` → `processing` → `completed` or `failed`, with `rows_ok`, `rows_failed`, `finished_at`, and `error_summary`. The upload API returns `202 Accepted`; `GET /api/v1/files/{file_id}/status` returns `200 OK` and is intended for polling.

### Stage 4: Absorption calculation and dashboard output
- **Trigger**: File import calls `AbsorptionCalculatorService.recompute`; JSON sync calls post-commit domain recompute separately. Dashboard reads are `GET /api/v1/absorption` and `GET /api/v1/absorption/summary`.
- **Input**: Legacy calculation reads `sales_records` and `inventory_snapshots`. Domain calculation uses `src/services/domain_absorption.py::DomainAbsorptionCalculatorService.compute` over `units`, `deals`, and `areas`.
- **Process**: `AbsorptionCalculatorService.recompute` rebuilds only `calculator='legacy_aggregate'`. `DomainAbsorptionCalculatorService.persist` rebuilds only `calculator='domain_units_deals'`, scoped to changed areas when available. `src/api/dashboard.py` uses `DomainSalesAnalyticsService` for the default dashboard read and keeps legacy output as an explicit compatibility path.
- **Output**: `absorption_daily` with `stat_date`, `units_sold`, rolling velocities, quality/observation fields, optional inventory/reservation values, calculator, and computation ID. API output contains trend points, summary KPIs, `data_source`, `data_status`, and sync freshness fields.
- **Error handling**: File-job failures mark the upload failed. Domain recompute exceptions are re-raised so RQ marks the job failed and applies its configured retry. Dashboard validation failures return 422; missing data is represented as `no_data`/`no_units` rather than fabricated metrics.
- **Status tracking**: `absorption_daily.computed_at` and `computation_id`; `upload_files.finished_at` for file-driven legacy recompute. HTTP reads return `200 OK`; invalid scope/range/calculator returns `422 Unprocessable Entity`.

### Stage 5: Post-commit domain recompute
- **Trigger**: `SyncRunService._enqueue_domain_recompute` after a committed JSON run with inserted, updated, or tombstoned projections; recovery also comes from `src/jobs/domain_recompute_audit.py::run_domain_recompute_audit`.
- **Input**: Project UUID, affected area UUIDs, and originating sync-run UUID.
- **Process**: `src/jobs/recompute_domain.py::run_domain_recompute` calls `_recompute`, which computes and persists domain absorption. The write is delete-and-reinsert within the selected calculator and area scope, making RQ retry idempotent.
- **Output**: Updated `absorption_daily` rows for `domain_units_deals`; no source tables are changed.
- **Error handling**: Enqueue uses `Retry(max=3, interval=[10, 30, 60])`. If Redis enqueue fails after the sync commit, the error is logged and the sync response remains successful; the stale-lineage audit detects the gap later. Job computation failures are re-raised for RQ retry/failure visibility.
- **Status tracking**: RQ job ID and structured logs `domain.recompute.enqueued`, `.started`, `.finished`, `.failed`; output rows use `computed_at` and `computation_id`. There is no dedicated domain-run table or HTTP job-status endpoint; `GET /api/v1/ops/domain-recompute` reports stale lineage with `200 OK`.

### Stage 6: Reconciliation and lineage audit
- **Trigger**: `POST /api/v1/reconciliation/runs`, scheduled `src/jobs/domain_recompute_audit.py::run_domain_recompute_audit`, or `src/services/domain_recompute_audit.py::audit`.
- **Input**: Project/snapshot scope and source instance; audit reads completed API-push `upload_files` and domain `absorption_daily` timestamps.
- **Process**: `src/services/reconciliation.py::ReconciliationService.run` checks entity/scope counts, external IDs, mirror consistency, duplicate active deals, orphan deals, missing areas, rejected records, tombstones, and snapshot safety. It persists one run and its findings. The domain audit compares latest applied sync `finished_at` with latest domain `computed_at` and can enqueue a full-project recompute.
- **Output**: `reconciliation_runs`, `reconciliation_findings`, and optional domain recompute RQ jobs. `scope='internal'` proves self-consistency; `scope='snapshot'` is snapshot-scoped. `scope='source'` is explicitly rejected because no live CRM source is available.
- **Error handling**: Reconciliation records `failed` when error findings exist; validation/auth/scope errors return 422/401. Audit repair failures are logged as diagnostics and do not hide the stale finding. Database failures make the audit job fail visibly.
- **Status tracking**: `reconciliation_runs.started_at`, `finished_at`, `status`, `passed`, finding counts, and `checks_run`; each finding has `created_at`. Run/detail/findings endpoints return `200 OK`; invalid or unauthorized requests return `401`/`422`/`404` as applicable.

### Stage 7: Parallel legacy/domain comparison
- **Trigger**: `POST /api/v1/parallel-run/{project_id}`, or scheduled `src/jobs/parallel_run.py::run_parallel_run_capture` when enabled by `src/scheduler.py`.
- **Input**: Project UUID; legacy source tables and domain source tables are read independently by `ParallelRunCaptureService` and `ParallelRunComparator`.
- **Process**: `src/services/parallel_run.py::ParallelRunCaptureService.capture` computes both sides, classifies differences with `src/services/comparison_rules.py::classify`, and inserts an append-only observation. It never calls `persist()` for `absorption_daily`.
- **Output**: `calculator_comparisons`; read history and classified verdicts from the same service. No calculator switch or source projection is changed.
- **Error handling**: Manual unknown project returns 404. Scheduled `capture_all` logs an individual project failure and continues; an infrastructure failure is re-raised to the RQ failed registry. No explicit retry is configured on the scheduled capture enqueue.
- **Status tracking**: `calculator_comparisons.compared_at`, `created_at`, `trigger`, `matches`, data-availability flags, difference/anomaly counts, and JSON details. Manual and read endpoints return `200 OK`; operations-token failures are 401/403.

### Stage 8: Ranking configuration and asynchronous ranking
- **Trigger**: `POST /api/v1/ranking/run` for synchronous calculation; `POST /api/v1/ranking/runs`, `POST /api/v1/ranking/features/survey`, and config publish/rollback for queued calculation.
- **Input**: Live `units`/`deals`, `areas`, published `ranking_configs`, and optional survey features.
- **Process**: `src/ranking/service.py::enqueue_ranking` creates/coalesces a DB `ranking_runs` row before Redis enqueue. `src/jobs/rank_project.py::rank_project` calls `run_ranking`, which claims the run, materializes feature snapshots, scores/ranks units with `src/ranking/engine.py`, and replaces current project scores.
- **Output**: `feature_snapshots`, `ranking_scores`, and `ranking_runs`; results are read by `GET /api/v1/ranking` and `GET /api/v1/ranking/runs/{run_id}`.
- **Error handling**: Queue enqueue uses `Retry(max=3, interval=[10, 30, 60])`. DB `queued` state survives Redis failure. A second worker receiving the same run gets `RUN_NOT_CLAIMABLE` and exits cleanly. Calculation failures update `ranking_runs.status='failed'`, `error_summary`, and `finished_at`, then re-raise for RQ retry.
- **Status tracking**: `ranking_runs.status` is `queued` → `running` → `completed` or `failed`; `attempt`, `enqueued_at`, `started_at`, `finished_at`, and processed/ranked/skipped counts provide timing and outcome. Async enqueue returns `202 Accepted`; synchronous and read routes return `200 OK`; invalid inputs return `422`.

### Stage 9: Advisory agent, human approval, and allow-listed execution
- **Trigger**: `POST /api/v1/agent/recommendations` or `POST /api/v1/chat`; approval/execution are explicit follow-on actions.
- **Input**: Ranking output and read-only project/unit/deal context assembled by `src/agents/advisory_tools.py::collect_advisory_context` and `run_advisory_agent`.
- **Process**: `src/api/agent.py::create_recommendation` persists an advisory recommendation; `POST /api/v1/agent/recommendations/{rec_id}/approve` or `/reject` records the human decision; `/execute` runs only the approved allow-listed action and writes application-owned campaign/execution records. `src/services/ai.py::generate_content` calls the LLM with `store=False`.
- **Output**: `agent_recommendations`, then optional `sales_campaigns`, `sales_campaign_units`, and `agent_executions`. Units/deals are not mutated by execution.
- **Error handling**: Recommendations begin `pending_approval`; there is no auto-approval. LLM network/429/5xx errors receive one retry with exponential delay before a typed 503/429/4xx failure. Approval and execution enforce role, scope, state, and action allow-list checks.
- **Status tracking**: `agent_recommendations.generated_at`, `decided_at`, `executed_at`, `status`, and `execution_status`; `agent_executions.started_at`, `finished_at`, `status`, `error`, and `result`; campaign creation uses `created_at`. Create returns `202 Accepted`; reads and successful decisions return `200 OK`; auth/state validation failures use 401/403/404/409/422.

### Stage 10: Forecast scheduler (not a live data pipeline yet)
- **Trigger**: `src/scheduler.py::enqueue_daily_forecast`, scheduled by `settings.forecast_cron` into `FORECAST_QUEUE`.
- **Input**: Optional area IDs; the intended source is `absorption_daily`.
- **Process**: `src/jobs/forecast.py::run_daily_forecast` currently only logs start/finish and returns zero processed areas. Prophet, sell-out confidence intervals, LangGraph explanation, alerts, and progress notification are TODO (MVP 2).
- **Output**: No database rows are written by the current job. Although `forecast_jobs`, `forecasts`, `forecast_points`, `explanations`, and `alerts` exist in `0001_initial_schema`, no current forecast implementation populates them.
- **Error handling**: No forecast calculation or explicit retry logic is currently implemented. **TODO: verify** whether a manual `/api/forecasts/run` endpoint is intended; no such router was found under `src/api/`.
- **Status tracking**: RQ job execution logs only; no forecast status row is produced by the current implementation.

## Database Schema Summary

- `projects`, `areas`: catalog hierarchy and source identity/content fields; areas belong to projects.
- `upload_files`: shared intake/run record for file uploads and JSON API-push syncs; status, counts, source metadata, snapshot metadata, and `finished_at`.
- `upload_errors`: row/JSON-path validation and processing errors linked to `upload_files`, with `created_at`, retry, and resolution fields.
- `crm_source_records`: source-record identity, revision/hash, decision, conflict, and tombstone state.
- `sales_records`, `inventory_snapshots`: legacy file-import facts linked to areas and upload files.
- `units`, `deals`: mirrored domain inventory and deal facts; soft deletion and source revision timestamps are retained.
- `absorption_daily`: derived legacy/domain absorption series, calculator lineage, quality flags, and `computed_at`.
- `sync_credentials`, `sync_payloads`: hashed machine credentials and retained raw sync payloads; payloads are linked one-to-one to runs.
- `reconciliation_runs`, `reconciliation_findings`: reconciliation lifecycle and individual checks/findings.
- `calculator_comparisons`: append-only legacy/domain comparison observations and verdict inputs.
- `feature_snapshots`, `ranking_configs`, `ranking_runs`, `ranking_scores`: ranking features, immutable/versioned weights, run lifecycle, and current unit scores.
- `agent_recommendations`, `sales_campaigns`, `sales_campaign_units`, `agent_executions`: advisory/HITL decision and allow-listed execution records.
- `users`, `settings`, `user_areas`, `refresh_tokens`, `audit_logs`: initial authentication, configuration, user scope, token, and audit tables; no current pipeline stage writes them directly in the inspected flow.
- `forecast_jobs`, `forecasts`, `forecast_points`, `explanations`, `alerts`: forecast-era schema from `alembic/versions/0001_initial_schema.py`; current `src/jobs/forecast.py` does not populate them.
- `suggestions`, `llm_calls`, `proposals`, `approvals`: initial AI/market schema. **TODO: verify** their production ownership; current ranking/advisory pipeline uses the `agent_*` and campaign tables above.

Schema provenance: base tables are created in `alembic/versions/0001_initial_schema.py`; sync identity in `0006_sync_foundation.py`; domain mirror in `0007_s3_domain_model.py`; credentials/payload retention in `0008_sync_credentials.py`, `0009_sync_payloads.py`, and `0010_sync_payload_retention.py`; reconciliation in `0011_reconciliation.py`; calculator provenance/comparisons in `0012_calculator_provenance.py` and `0013_calculator_comparisons.py`; ranking in `0014_ranking_foundation.py` and `0015_ranking_results.py`; conflicts/hierarchy in `0016_completed_with_conflicts.py` and `0017_hierarchy_projection.py`; advisory/execution in `0018_agent_recommendations.py` and `0020_agent_advisory_execution.py`. Later revisions `0019`, `0021`, `0023`, `0024`, and `0025` are fixture/data or data-label changes, not new pipeline stages.

## API Endpoints

- `POST /api/v1/files/upload` — starts Stage 3; `GET /api/v1/files`, `/api/v1/files/{file_id}/status`, `/errors`, and `/errors.csv` monitor its run and errors.
- `POST /api/v1/sync/{entity}` — starts Stage 2; `POST /api/v1/sync-runs/{sync_run_id}/reprocess` retries a retained failed/partial run; `GET /api/v1/sync-runs/{sync_run_id}`, `/errors`, `/api/v1/sync-runs`, `/api/v1/sync-errors`, and `/payload` monitor it.
- `GET /api/v1/absorption` and `GET /api/v1/absorption/summary` — read Stage 4 derived trend/KPI output.
- `POST /api/v1/reconciliation/runs` — starts Stage 6; `GET /api/v1/reconciliation/runs/{run_id}` and `/findings` monitor it.
- `GET /api/v1/ops/domain-recompute` — monitors Stage 5 stale lineage and optional scheduled repair outcome.
- `POST /api/v1/parallel-run/{project_id}` — starts Stage 7 synchronously; `GET /api/v1/parallel-run/{project_id}` and `/verdicts` read its history.
- `POST /api/v1/ranking/run` — runs Stage 8 synchronously; `POST /api/v1/ranking/runs` and `POST /api/v1/ranking/features/survey` enqueue it; `GET /api/v1/ranking` and `GET /api/v1/ranking/runs/{run_id}` read results/status; config draft/publish/rollback routes are `GET/POST /api/v1/ranking/configs` and `POST /api/v1/ranking/configs/{version}/publish|rollback`.
- `POST /api/v1/agent/recommendations` — starts Stage 9; `GET /api/v1/agent/recommendations` and `/{rec_id}` monitor recommendations; `POST /approve`, `/reject`, and `/execute` advance explicit HITL states.
- `POST /api/v1/chat` — read-only advisory response path using the agent context; it does not create a pipeline run or bypass approval.
- `GET /api/v1/projects`, `/projects/{external_id}`, `/areas`, `/areas/{external_id}`, and catalog/dashboard reads expose stored pipeline outputs but do not trigger a pipeline stage.
- No websocket endpoint was found. `GET /api/v1/files/{file_id}/errors.csv` uses `StreamingResponse` only to stream a finite CSV error export.

## Monitoring & Observability

- **Logs**: structured logs from `src/logging_config.py`; request errors are handled in `src/main.py::unhandled_exception_handler` with an `error_id` and request ID; worker/scheduler logs identify queue jobs and durations without logging secrets.
- **Metrics**: no external metrics backend was found. Operational timestamps/counts are stored in `upload_files`, `absorption_daily`, `reconciliation_runs`, `calculator_comparisons`, `ranking_runs`, `ranking_scores`, `agent_recommendations`, and `agent_executions`; job logs include duration/count fields.
- **Queues**: `INGEST_QUEUE` handles parse, domain recompute, lineage audit, parallel capture, and ranking; `FORECAST_QUEUE` handles the current forecast stub. `src/worker.py` prioritizes ingest before forecast.
- **Retries**: domain recompute and ranking enqueue use RQ retry intervals `[10, 30, 60]` with maximum 3 retries; LLM calls retry once after transient network/429/5xx responses; sync reprocess is explicit and payload-backed; file parse has no configured automatic retry.
- **Alerts**: stale domain lineage is logged by `src/services/domain_recompute_audit.py::audit`; reconciliation findings and comparison differences/anomalies are persisted. No external alert/notification sink or websocket progress channel is currently implemented. Forecast alert generation remains TODO (MVP 2).

---

# Đợt 2026-08-15 (b) — Tiếp tục tích hợp frontend/backend: trạng thái dự án, phạm vi upload, transport history

Status: **COMPLETE trong phạm vi frontend/backend integration được duyệt**. Không
commit. Không chạm `sync_credentials`, relay, bất biến ingestion/domain
projection, migration lịch sử, hay secrets.

## Đã làm

- Sửa duy nhất selector của `InventoryPage.test.jsx`: chip lọc dùng
  `getByRole("button", ...)`, assertion badge được scope trong `within(table)`;
  test focused pass **7/7**.
- Giữ `ProjectSummary.status` trong schema + response và thêm assertion API có
  scope: viewer chỉ thấy Project A và response vẫn có `status="active"`.
- Xác nhận `ConnectPanel` + `ChatWidget` đã được mount trong `AppLayout`; nav
  `Danh mục` có route `/catalog`.
- Hoàn thiện phạm vi luồng nạp: `UploadPage` truyền đúng UUID của project đã
  chọn cho upload và history; tab `file_upload`/`api_push` giữ đúng
  `transport_mode`. Không còn fallback âm thầm sang project đầu tiên khi đã có
  ngữ cảnh chọn.
- Import flow truyền project/area đã chọn sang `/import/upload`; giá trị tồn kho
  thiếu hiển thị `N/A`, không suy diễn từ `total_units`.
- `ProjectsPage`, `ImportSelectPage`, `UploadPage`, `AppLayout` có test tích
  hợp focused; `AreaDetailPage` có các tab tồn kho/giao dịch/trạng thái xếp hạng
  chưa khả dụng tường minh; `AuditPage` có unavailable state tường minh.
- `MarketPrototypePage` và hai CSS stub đã bị xoá từ trước; grep xác nhận không
  còn frontend import, chỉ còn các comment lịch sử tham chiếu tên cũ.

## Kiểm chứng

```
cd frontend && npm run build
# pass (Vite 6.4.3)

cd frontend && npx vitest run
# pass — 14 files, 103 tests

.venv/bin/ruff check src tests --exclude src/services/market.py --exclude src/services/gemini.py
# All checks passed!

TEST_TARGET=tests/ bash scripts/test_db.sh --ignore=tests/test_services/test_real_hierarchy_e2e.py -q
# 1282 passed, 1 skipped, 9 warnings
```

Full backend run (không exclude) đạt **1282 passed, 1 skipped, 14 errors**;
toàn bộ 14 errors nằm ở `test_real_hierarchy_e2e.py` và dừng tại
`INVALID_API_KEY` khi relay. Đây là blocker `sync_credentials` đã biết; không
sửa trong đợt này.

Vite proxy verification đọc thành công dữ liệu `ai-dev-fixture`: 4 projects,
project `prj_op1`, inventory response có 16 areas. Mini CRM safe write test có
đánh dấu rõ ràng: tạo + đọc lại `P-0002` qua `/minicrm-api` rồi archive để dọn
dữ liệu; **không chờ backend mirror** vì blocker sync credentials/relay.

## Còn lại / không làm

- Không commit cho tới khi có phê duyệt rõ ràng.
- Không đụng sync credentials hoặc cố làm cho relay/backend mirroring pass trong
  đợt frontend này.
- Repository-wide Ruff vẫn báo 4 lỗi đã có sẵn trong
  `src/services/gemini.py`/`src/services/market.py`; không sửa vì đây là các
  stub/backend ngoài phạm vi integration được duyệt. Ruff trên phần source/test
  trong phạm vi đã sửa mới pass.

---

# Đợt 2026-08-15 (a) — Hoà giải `origin/feature/NguyenDucDat/ranking-engine`: KHÔNG merge git, chỉ cổng lớp trình bày (bands/disclaimer)

Status: **KHÔNG chạy `git merge`** — điều tra bằng chứng cho thấy hai nhánh
không mergeable ở mức nội dung; dừng lại xin quyết định thay vì tự ý phá bất
biến kiến trúc, đúng điều kiện dừng mà chính yêu cầu công việc đã liệt ra.

## Bối cảnh

Yêu cầu: merge `origin/feature/NguyenDucDat/ranking-engine` vào nhánh hiện tại,
ưu tiên dữ liệu/mô hình xếp hạng của nhánh đó "bằng mọi giá", điều chỉnh
ingestion cho khớp nếu xung đột. Trước khi sửa bất kỳ file nào, đọc trước
`pipeline_status.md`, `src/`, `alembic/versions/`, `docker-compose.yml`.

## Bằng chứng: hai nhánh không phải một "git conflict" giải được

- `git diff --stat HEAD origin/feature/NguyenDucDat/ranking-engine`: **244 file
  bị xoá, chỉ 61 file thêm** (−18k dòng ròng). Nhánh kia xoá TOÀN BỘ tầng
  sync/ingestion/domain-projection mà repo này đang có:
  `src/services/domain_projection.py`, `source_identity.py`,
  `sync_credentials.py`, `sync_payloads.py`, `sync_runs.py`,
  `reconciliation.py`, `history_guard.py`, `contract_validation*.py`,
  `dashboard_auth.py`, `excel_parser.py`, `import_records.py`,
  `absorption.py`, `calculators.py`, cùng `src/api/dashboard.py`,
  `files.py`, `inventory.py`, `ops.py`, và ~20 file test tương ứng
  (`test_domain_projection.py`, `test_source_identity.py`,
  `test_sync_credentials.py`, `test_sync_concurrency.py`,
  `test_sync_payloads.py`, ...).
- **`units`/`deals` bị định nghĩa lại không tương thích.** Repo này đã có
  `units`/`deals` (migration `0007_s3_domain_model.py`), khoá theo
  `source_system`/`source_instance_id`/`external_id`, enum trạng thái có
  `blocked`. Migration `0002_operational_units_deals.py` của nhánh kia tạo LẠI
  `units`/`deals` từ đầu, khoá theo `source_id`/`source_version`, enum trạng
  thái có `off_market` thay vì `blocked` — hai schema khác nhau, cùng tên bảng.
- **Trùng revision ID, khác nội dung.** `0014_ranking_foundation`/
  `0015_ranking_results` của nhánh kia dùng ĐÚNG chuỗi revision như bên này,
  nhưng `down_revision` khác (`0002_operational_units_deals` thay vì
  `0013_calculator_comparisons`) và nội dung bảng/lý do thiết kế hoàn toàn
  khác — xác nhận bằng `git diff` trực tiếp hai file.
- **`src/ranking/` bị xây độc lập hai lần** từ có vẻ cùng một tài liệu thiết kế
  (`docs/ranking/implementation_plan.md`) nhưng khác kiến trúc: bên kia có
  `bands.py`/`config_service.py`/`constants.py`/`errors.py`/`features.py`/
  `ordering.py`/`repository.py`/`scoring.py` (896 dòng, service.py chạm DB
  137 dòng); bên này có một `engine.py` THUẦN (không I/O, có test biên canh —
  `tests/test_ranking_boundary.py::test_ranking_engine_is_a_pure_function_no_db_no_network`)
  + `service.py` orchestrator 379 dòng.

Merge "ưu tiên ranking, điều chỉnh ingestion" theo đúng nghĩa đen sẽ xoá tầng
sync/domain-projection và đổi schema `units`/`deals` — vi phạm trực tiếp bất
biến "Mini CRM là chủ sở hữu chính tắc, Backend chỉ là projection read-only"
và điều kiện dừng "ingestion không điều chỉnh được mà không vi phạm bất biến
kiến trúc" mà chính yêu cầu công việc liệt kê. Đã dừng lại, hỏi người dùng
bằng `AskUserQuestion` thay vì tự quyết. Người dùng chọn: **chỉ cổng những
khả năng xếp hạng THẬT SỰ MỚI, giữ nguyên kiến trúc sync/ingestion và schema
`units`/`deals` hiện có — không chạy `git merge` thật.**

## Đã làm (sau khi có quyết định)

So sánh từng file `src/ranking/*.py` của hai bên để tìm phần THẬT SỰ MỚI
(không trùng lặp, không xung đột ngữ nghĩa với công thức đang chạy/đã có test):

- `ordering.py`, `scoring.py`, `features.py`, `config_service.py`,
  `repository.py` của nhánh kia: HOẶC là tập con của những gì `engine.py`/
  `service.py` bên này đã làm (vd. `ordering.py` không có tie-break phụ và
  không tách `rank_in_area`, trong khi `engine.py:rank_scores` đã có cả hai),
  HOẶC dùng công thức nghiệp vụ KHÁC cho cùng feature (`area_velocity_norm`,
  `area_conversion_norm` của họ không có hằng số bão hoà
  `VELOCITY_SATURATION` mà bên này đã cố tình thêm; mẫu số `conversion` của
  họ là "deal đang mở", bên này là "toàn bộ deal từng có") — đây là khác biệt
  THIẾT KẾ nghiệp vụ, KHÔNG phải một khả năng còn thiếu, nên KHÔNG cổng, để
  tránh âm thầm đổi ý nghĩa một KPI đang phục vụ ưu tiên bán hàng thật.
  `config_service.py` + endpoint versioning: nằm ĐÚNG phần
  `tests/test_ranking_boundary.py` đã tự ghi rõ là "CHƯA có" ở lát cắt hiện
  tại (`endpoint khảo sát, endpoint đọc xếp hạng độc lập`) — không tự ý mở
  rộng phạm vi ngoài yêu cầu hoà giải.
- `bands.py` (band_for: high/medium/low theo ngưỡng tuyệt đối; as_percent:
  đổi điểm sang thang 0-100) + hằng `DISCLAIMER`: hàm THUẦN, không đụng DB,
  không đụng schema, KHÔNG có ở bên này — khả năng thật sự mới, rủi ro thấp.
  Cổng nguyên vẹn vào **`src/ranking/bands.py`** (mới), đổi kiểu cho khớp
  `UnitScore.score: Decimal | None` của `engine.py` (bên kia dùng
  `ScoreResult.score` cùng kiểu nên không cần chuyển đổi thêm).
- Gắn `band_for`/`as_percent`/`DISCLAIMER` vào
  **`src/ranking/service.py::_build_summary_context`** — chuỗi ngữ cảnh duy
  nhất đưa vào prompt LLM tư vấn (`src/agents/nodes/ranking_node.py`). Mỗi
  căn trong top-10 giờ có thêm nhãn mức + phần trăm; cuối ngữ cảnh luôn có
  `DISCLAIMER` — củng cố đúng yêu cầu AGENTS.md rằng khuyến nghị của agent
  không phải cam kết kết quả bán hàng, và luôn CHỜ DUYỆT.
- Test mới: `tests/test_ranking/test_bands.py` (7 test, thuần — ngưỡng
  band, None-safety, làm tròn phần trăm, disclaimer khác rỗng),
  `tests/test_ranking/test_summary_context.py` (3 test, thuần — nhãn mức +
  phần trăm xuất hiện đúng chuỗi, disclaimer luôn có mặt kể cả khi rỗng danh
  sách).

## Không làm (và vì sao)

- **Không chạy `git merge`** dưới bất kỳ hình thức nào (kể cả `--no-commit`)
  — không có gì để merge ở mức git khi 244/305 file thay đổi là xoá tầng hạ
  tầng đang chạy thật của repo này.
- **Không đổi `units`/`deals`** hay bất kỳ migration nào (0001-0019 giữ
  nguyên byte).
- **Không cổng `config_service.py`, `repository.py`, endpoint
  `GET /api/v1/ranking/...`** — ngoài phạm vi "cổng khả năng xếp hạng mới",
  đây là TÍNH NĂNG (đã được `test_ranking_boundary.py` tự ghi nhận là chưa
  làm), cần quyết định phạm vi riêng, không phải một phần hoà giải hai
  nhánh.
- **Không đổi công thức `area_velocity_norm`/`area_conversion_norm`** — khác
  biệt thiết kế nghiệp vụ giữa hai bên, không phải lỗi, không tự quyết thay
  đội.

## Kiểm chứng

```
python -m pytest tests/test_ranking/ tests/test_ranking_boundary.py tests/test_agent_e2e.py -q
# 34 passed, 11 skipped (test_agent_e2e cần TEST_DATABASE_URL — bỏ qua đúng quy ước, không phải lỗi)
ruff check src/ranking/bands.py src/ranking/service.py tests/test_ranking/test_bands.py tests/test_ranking/test_summary_context.py
# All checks passed!
TEST_TARGET="tests/" bash scripts/test_db.sh --ignore=tests/test_services/test_real_hierarchy_e2e.py -q
# (xem log đầy đủ ở báo cáo cuối cùng phiên này)
```

## Việc còn lại / theo dõi tiếp

- `NguyenDucDat` cần biết: nhánh `ranking-engine` của bạn không rebase được
  lên `main`/nhánh này bằng git — cần một quyết định cấp đội (schema
  `units`/`deals` nào là chính tắc, có giữ tầng sync/ingestion không) trước
  khi thử merge lại, không phải việc một phiên AI tự quyết.
- `bands.py`/`DISCLAIMER` mới chỉ có mặt trong `summary_context` (đưa vào
  LLM) — chưa có ở bất kỳ response API nào vì `GET /ranking/...` chưa tồn
  tại (đúng phạm vi hiện tại).

---

# Đợt 2026-08-14 (b) — Migration 0019: DEV/AI fixture từ `crm_real_data.json` cho Backend (KHÔNG chứng minh sync Mini CRM)

Status: **COMPLETE cho đúng phạm vi được giao** (seed dev/AI-fixture vào Backend
qua Alembic, cách ly hoàn toàn khỏi pipeline sync Mini CRM thật, KHÔNG chạm bốn
bảng xếp hạng Phase 6).

## Bối cảnh

`crm_real_data.json` (~1 MB) là dữ liệu BĐS thật/ước lượng (4 dự án, 18 zone,
58 phân khu, 1991 căn có mã thật, cộng điểm xếp hạng MÔ PHỎNG) do một phiên làm
việc TRƯỚC (không phải phiên này — thấy qua `.ai-log/session.jsonl`, ts
2026-08-13T23:34) đã dùng để dựng `scripts/seed_mini_crm_from_json.py` và một
bản `scripts/seed_backend_from_json.py` CHƯA hoàn thiện: hằng số danh tính
nguồn sai (`mini_crm_seed`/`ingestion_seed` thay vì một fixture-namespace rõ
ràng), không seed `units`, và `docs/ingestion_seed.json` lúc đó chỉ là dữ liệu
DEMO tổng hợp (`_meta.synthetic_demo_data`), KHÔNG phải dẫn xuất thật từ
`crm_real_data.json`. Đợt này hoàn thiện đúng phần còn thiếu: dẫn xuất thật,
sửa danh tính nguồn, thêm `units`, và bọc thành một Alembic revision idempotent.

`crm_real_data.json` KHÔNG nằm trong repo này (tìm thấy ở hai đường dẫn ngoài
repo, cùng hash) — đúng như `docs/ingestion_seed.json` cũ đã ghi chú.

## Đã làm

1. **`scripts/derive_ingestion_seed_from_crm_real_data.py`** (mới) — đọc
   `crm_real_data.json` MỘT LẦN, kiểm cục bộ (tham chiếu project/area, khoá
   trùng, `total_units >= units_remaining >= 0`, `sold+remaining==total_units`,
   status hợp lệ), rồi dựng HAI file nhỏ đã commit:
   - `scripts/fixtures/ingestion_seed.json` (~425 KB) — projects/areas
     (JOIN `areas[]`+`dash_areas[]`)/units (từ `ranking_by_area`, CHỈ phần
     `unit_id/unit_code/unit_type/status`)/trend/files/sample_errors.
   - `docs/ai_fixtures/simulated_ranking_fixture.json` (~892 KB) — TOÀN BỘ
     `score/score_raw/band/rank/contributions/scored`, `provenance:
     "simulated_fixture"`, cảnh báo nguyên văn `scoring_meta` ("NOT from a
     running ranking model") — KHÔNG migration/script nào đọc file này.
2. **`scripts/_seed_ai_crm_fixture_core.py`** (mới) — logic mapping THUẦN
   (không I/O), dùng CHUNG bởi CLI và Alembic. Danh tính fixture:
   `source_system="crm_real_data_fixture"`, `source_instance_id="ai-dev-fixture"`
   — khác hẳn `mini-crm-dev` thật, không thể trộn với dữ liệu sync thật.
3. **`alembic/versions/0019_seed_ai_crm_fixture.py`** (mới, head hiện tại) —
   DATA migration thuần, gọi `build_upserts()`/`build_downgrade_statements()`
   qua `op.get_bind()` (Alembic chạy đồng bộ). `downgrade()` xoá ĐÚNG dòng mang
   danh tính fixture, tính lại từ chính DB, không phụ thuộc file JSON còn tồn
   tại hay không.
4. **`scripts/seed_backend_from_json.py`** — viết lại thành wrapper CLI mỏng
   quanh core module (trước đó là bản độc lập, danh tính nguồn sai).
5. **`tests/test_ranking_boundary.py`** — cập nhật
   `test_the_backend_alembic_history_is_now_eighteen_linear_revisions` →
   `..._nineteen_...` (18→19 revision) — ĐÚNG tinh thần đã ghi ở docstring gốc
   của chính file này ("ai sửa nó theo hiện thực mới thì đang làm đúng việc").

## Vướng mắc thật gặp phải, đã sửa TRƯỚC KHI kết luận xong (không đoán)

- **`ck_upload_files_status` nổ khi upgrade lần đầu**: `crm_real_data.json`
  dùng trạng thái tự do (`success`/`partial`/`processing`/`failed`) không khớp
  CHECK constraint thật (`pending/processing/completed/completed_with_conflicts/
  partially_completed/failed`). Thêm `FILE_STATUS_MAP` tường minh trong
  `_seed_ai_crm_fixture_core.py` — giá trị lạ làm nổ `SeedError` ngay, không
  đoán. Có test riêng (`test_unmapped_file_status_raises_loudly_instead_of_guessing`).
- **`docs/ingestion_seed.json` KHÔNG BAO GIỜ vào được image/container `api`**:
  `.dockerignore` loại bỏ toàn bộ `docs/`, và `docker-compose.yml`'s
  `backend-base` chỉ mount sống `./src`/`./alembic` — xác nhận SỐNG bằng chính
  lỗi `alembic upgrade head` báo "Không thấy /app/docs/ingestion_seed.json" sau
  khi rebuild image. Sửa: chuyển fixture đã commit sang
  `scripts/fixtures/ingestion_seed.json` (`scripts/` ĐƯỢC `COPY . .` vào image
  lúc build — xác nhận bằng `docker compose exec api python -c "import
  scripts.seed_mini_crm_from_json"`). File bàn giao cho AI team
  (`docs/ai_fixtures/...`) KHÔNG cần sửa vì không migration nào đọc nó.
- **`test_the_backend_alembic_history_is_now_eighteen_linear_revisions` đỏ**:
  đúng tín hiệu dự kiến của chính test đó khi thêm revision mới — cập nhật số
  đếm + tên hàm, không phải hồi quy.

## Provenance và an toàn

- Mọi dòng fixture (`projects`/`areas`/`units`/`upload_files`) mang
  `source_system='crm_real_data_fixture'`, `source_instance_id='ai-dev-fixture'`
  — cách biệt hoàn toàn với `mini-crm-dev` thật (pipeline sync Mini CRM →
  outbox → relay → Backend, xem "Đợt 2026-08-14 (a)" và các đợt trước, VẪN
  đang bị chặn bởi `sync_credentials` rỗng — KHÔNG đụng tới ở đợt này).
  `SourceIdentityService`/`DomainProjector` thật không bao giờ sinh hay khớp
  giá trị `ai-dev-fixture`.
- Idempotent: id = `uuid5(NS_INGESTION_SEED, "<kind>:<json_id>")`, mọi insert
  `ON CONFLICT (id) DO UPDATE`. Xác nhận SỐNG: chạy `alembic upgrade head` rồi
  `python -m scripts.seed_backend_from_json` lần hai trên CÙNG DB dev — số
  dòng không đổi (4/58/1991/6/58/58/696).
- `downgrade()` CHỈ xoá theo danh tính fixture (hoặc theo FK bắc cầu tới
  `areas`/`upload_files` mang danh tính đó cho `absorption_daily`/
  `sales_records`/`inventory_snapshots`/`upload_errors`) — test
  `test_downgrade_removes_only_fixture_rows` chèn một dự án/phân khu KHÔNG-
  phải-fixture TRƯỚC khi upgrade, xác nhận nó còn nguyên SAU downgrade.
- KHÔNG bịa: không `deals` nào được tạo (0 bản ghi cấp-deal trong nguồn), không
  ranking nào ghi vào `ranking_configs/ranking_runs/ranking_scores/
  feature_snapshots` (0 dòng, có test xác nhận), không `zones` nào được vật
  chất hoá thành bảng riêng (không có cột nào để chứa).

## Verification

```
python -m scripts.derive_ingestion_seed_from_crm_real_data --source <crm_real_data.json>
  → 4 projects, 58 areas, 1991 units, 696 trend_points, 5 files, 9 sample_errors

pytest tests/test_scripts/test_seed_ai_crm_fixture_core.py
       tests/test_scripts/test_derive_ingestion_seed_from_crm_real_data.py -q
  → 28 passed

TEST_TARGET="tests/test_migrations/test_0019_seed_ai_crm_fixture.py" bash scripts/test_db.sh
  → 6 passed (upgrade counts, ranking/deals untouched, idempotent double-upsert,
     downgrade scoping, downgrade FK-scoped absorption/upload cleanup)

ruff check <mọi file .py mới/sửa>          → All checks passed! (2 lỗi tự động sửa)

docker compose build api && docker compose up -d --force-recreate --no-deps
  minicrm api worker scheduler              → cả bốn container healthy/started;
  log entrypoint: "[projects] 4 ... [units] 1991 ... [absorption_daily] 696 upserted"

docker compose exec api alembic current/heads → 0019_seed_ai_crm_fixture (head)

TEST_TARGET="tests/" bash scripts/test_db.sh --ignore=tests/test_services/test_real_hierarchy_e2e.py -q
  → 1272 passed, 1 skipped, 0 failed (14 lỗi của test_real_hierarchy_e2e.py bị
     loại trừ CÓ CHỦ ĐÍCH — pre-existing, cùng nguyên nhân sync_credentials rỗng
     đã ghi ở "Đợt 2026-08-14 (a)", KHÔNG liên quan tới đợt này)

DB (docker compose exec db psql):
  projects/areas/units theo (source_system, source_instance_id) fixture → 4/58/1991
  ranking_scores, deals → 0/0

HTTP thật qua DASHBOARD_ADMIN_TOKEN (docker compose exec api còn sống):
  GET /api/v1/projects            → 4 dự án fixture, id = uuid5 tất định khớp `uid()`
  GET /api/v1/areas?project_id=.. → phân khu thật (vd "Ngọc Trai - Biệt thự song lập")
  GET /api/v1/absorption?area_id=ar_0001 → điểm trend thật (units_sold=3, velocity_7d=3, ...)
  GET /api/v1/absorption/summary  → units_remaining=3001, units_sold=17099, calculator=legacy_aggregate
  GET /api/v1/inventory?external_project_id=prj_op1&external_area_id=ar_0001&include_units=true
       → 40 căn thật (unit_code=ST-100, status=available, active_deal_status=None)
```

## Rủi ro còn lại / follow-up

- `sync_credentials` rỗng (chưa cấp khoá — xem "Đợt 2026-08-14 (a)") VẪN chặn
  pipeline sync Mini CRM thật; fixture ở đợt này KHÔNG sửa và KHÔNG che vấn đề
  đó — hai đường hoàn toàn tách biệt theo đúng yêu cầu.
- `docs/ai_fixtures/simulated_ranking_fixture.json` là artefact JSON thuần,
  chưa có endpoint/CLI nào phục vụ nó cho đội AI ngoài đọc file trực tiếp —
  nếu cần một đường đọc HTTP, đó là công việc CHƯA làm ở đợt này.
- `data_source` (real/estimated) chỉ truy được qua `scripts/fixtures/
  ingestion_seed.json._meta`/`dash_areas[].data_source`, KHÔNG qua API — không
  có cột hỗ trợ trên `projects`/`areas` (xem giới hạn đã ghi trong docstring
  `derive_ingestion_seed_from_crm_real_data.py`).

---

# Đợt 2026-08-14 (a) — Sửa `scripts/seed_mini_crm_from_json.py`: ánh xạ danh tính cũ (`IdentityMap`) tự phục hồi khi 404

Status: **COMPLETE cho đúng lỗi được báo (`DependencyError: 'unit-sim-1' cần
'area-sim-a'`). Một vấn đề KHÁC, KHÔNG liên quan, vẫn còn — xem "Rủi ro còn
lại" bên dưới, KHÔNG được coi là đã sửa.**

## Bối cảnh

`pipeline_status.md` (tài liệu này) KHÔNG có mục nào nhắc tới
`scripts/seed_mini_crm_from_json.py`, `IdentityMap`, hay `DependencyError`
trước đợt này — không có gì để đối chiếu/mâu thuẫn, script này chưa từng được
ghi nhận ở đây. Một điểm lạc (không phải lỗi của đợt này): docstring của chính
script ở dòng ~52-57 trỏ tới "`pipeline_status.md` dòng ~3034" cho ngữ cảnh
`MINICRM_PROJECT_ID` — chuỗi đó KHÔNG tồn tại ở bất kỳ đâu trong tài liệu hiện
tại; tham chiếu đó đã cũ/hỏng, không sửa ở đợt này (ngoài phạm vi).

## Root cause

`docs/.mini_crm_seed_state.json` (gitignored, "trạng thái CỦA MÁY ĐANG CHẠY")
còn giữ ánh xạ `prj-sim -> P-0004` và `area-sim-a -> A-0005` từ một lần chạy
TRƯỚC, nhưng Mini CRM đã được migrate lại từ đầu (`0001`-`0004`) — hai bản ghi
đó không còn tồn tại (`GET /projects/P-0004` / `GET /areas/A-0005` → 404 thật,
`crud.get_project`/`crud.get_area` raise `RecordNotFoundError`).

Script KHÔNG có đường tự phục hồi khi một ánh xạ đã biết 404: `seed_projects()`
GET id cũ → 404 → ghi `failed`, KHÔNG điền `project_ids["prj-sim"]`.
`seed_areas()` sau đó suy `external_project_id` qua
`project_ids.get(key) or self.identity.get(key)` — do `project_ids` rỗng, nó
rơi về `self.identity.get(...)`, tức LẠI dùng đúng cái id cũ vừa chứng minh là
hỏng, không hề raise lỗi. `seed_areas()` tới lượt GET `A-0005` cũng 404, ghi
`failed`, không điền `area_ids["area-sim-a"]`. `main()` dựng `area_info` chỉ từ
`area_ids`, nên `area_info["area-sim-a"]` không tồn tại — `seed_units()` cuối
cùng raise đúng `DependencyError` được báo, ở đúng dòng (`seed_units`, dòng
916 tại thời điểm audit).

**Giả thuyết bị loại**: external_key bị lẫn với external_id (không — tách bạch
qua `IdentityMap`); field/case/whitespace mismatch trên `area_name` (không — copy
verbatim); `seed_areas()` trả sai hình dạng mapping (không — `main()` tự ghép
`area_name` từ fixture, không từ response API).

## Files changed

- `scripts/seed_mini_crm_from_json.py`:
  - `IdentityMap.clear(external_key)` (method mới) — xoá MỘT ánh xạ đã hỏng,
    khác `set`/`get`/`all_keys` sẵn có.
  - `MiniCrmSeeder._get_or_none` → đổi tên/hành vi thành `_get_existing(path,
    *, key, kind)`, trả `(response, was_stale)`. 404 ⇒ `identity.clear(key)`,
    in một dòng `[tự phục hồi] ...`, trả `(None, True)` — KHÔNG chặn lần chạy.
    Mọi status khác (401/403/409/422/503/...) vẫn `failed`, chặn như cũ —
    KHÔNG lẫn với staleness.
  - `seed_projects()`, `seed_areas()`, `seed_units()`: nhánh "đã có `existing_id`"
    tái cấu trúc để khi `_get_existing` báo `was_stale=True`, `existing_id` bị
    đặt lại `None` và RƠI XUỐNG đúng nhánh tạo mới sẵn có (bao gồm cả lưới an
    toàn 409 `AREA_NATURAL_KEY_CONFLICT`/dò khoá tự nhiên của area/unit) — không
    nhân đôi logic tạo mới.
  - Cách API/JSON contract KHÔNG đổi; không hard-code `P-0004`/`A-0005`; mọi
    ghi vẫn qua HTTP API, không chạm SQL/`crm_outbox` trực tiếp.
- `tests/test_scripts/test_seed_mini_crm_from_json.py`: thêm 6 test hồi quy
  (mục "MiniCrmSeeder: hồi quy cho ánh xạ danh tính cũ") dùng
  `httpx.MockTransport` giả `/projects`/`/areas`/`/units` — không cần DB thật.

## Tests added/updated

`test_stale_identity_entries_self_heal_and_unit_resolves_to_recreated_area`,
`test_unit_not_created_when_area_upsert_genuinely_fails`,
`test_rerun_after_self_heal_is_idempotent_no_duplicate_writes`,
`test_generated_external_id_is_never_confused_with_the_fixture_external_key`,
`test_dependency_error_names_child_and_missing_parent_before_any_http_call`.
Test đầu tiên chạy TRƯỚC sửa và thất bại đúng như dự đoán
(`AssertionError: project phải tự tạo lại khi ánh xạ cũ 404... assert 'prj-sim'
in {}`) — capture bằng `pytest`, không đoán.

## Commands and results

```
python -m pytest tests/test_scripts/test_seed_mini_crm_from_json.py -q
  → trước sửa: 1 failed, 32 passed
  → sau sửa:   33 passed

ruff check scripts/seed_mini_crm_from_json.py tests/test_scripts/test_seed_mini_crm_from_json.py
  → All checks passed!

python -m pytest tests/ -q
  → 429 passed, 810 skipped, 14 errors
  (14 lỗi ở tests/test_services/test_real_hierarchy_e2e.py — file KHÔNG đụng
  tới ở đợt này, lỗi 401 INVALID_API_KEY trên relay → Backend, PRE-EXISTING,
  cùng nguyên nhân với mục "Rủi ro còn lại" bên dưới)

python -m scripts.seed_mini_crm_from_json
  → Preflight OK, 1 lô outbox đã tồn tại từ trước (ranh giới lần chạy)
  → [tự phục hồi] project 'prj-sim': danh tính cũ 'P-0004' không còn tồn tại (404) — tạo lại.
  → [tự phục hồi] area 'area-sim-a': danh tính cũ 'A-0005' không còn tồn tại (404) — tạo lại.
  → [tự phục hồi] unit 'unit-sim-1': danh tính cũ 'U-0009' không còn tồn tại (404) — tạo lại.
  → project/area/unit: created, 201 OK — KHÔNG còn DependencyError.
  → deal-sim-1: failed (UNIT_NOT_MIRRORED, hết thời gian chờ — vì outbox v2
    của unit là dead letter 401, xem "Rủi ro còn lại")
  → exit code vẫn 1, nhưng KHÔNG phải vì DependencyError — vì INVALID_API_KEY.
```

## Rủi ro còn lại / follow-up

- **KHÔNG PHẢI lỗi của đợt này, KHÔNG sửa ở đây** (ngoài phạm vi được giao, và
  guardrail cấm đổi `.env`/token): mọi lô outbox v2 (`projects`/`areas`/
  `units_v2`) của lần chạy này bị Backend từ chối `401 INVALID_API_KEY` khi
  relay — `Historical dead letters: 1 (projects/401 x1)` đã tồn tại TRƯỚC lần
  chạy này, xác nhận đây là lệch cấu hình `MINICRM_SYNC_API_KEY` ↔
  `sync_credentials` (Backend) có sẵn từ trước, không phải do sửa đổi ở đợt
  này. Cùng nguyên nhân khiến `tests/test_services/test_real_hierarchy_e2e.py`
  (14 test, không đụng ở đợt này) lỗi 401 tương tự. Hệ quả: `deal-sim-1` không
  tạo được (unit không mirror được vì lô của nó dead-letter), và lệnh
  `python -m scripts.seed_mini_crm_from_json` vẫn thoát mã 1 dù `DependencyError`
  đã hết. Cần người vận hành đối soát `MINICRM_SYNC_API_KEY` (`.env` gốc +
  `minicrm/.env`) với dòng CÒN SỐNG trong bảng `sync_credentials` của Backend.
- **Giới hạn đã biết, không mới**: nếu TOÀN BỘ `docs/.mini_crm_seed_state.json`
  bị xoá tay (không phải một entry 404 đơn lẻ), mọi fixture vẫn bị coi là CHƯA
  TỪNG tồn tại và tạo lại — hành vi tài liệu hoá sẵn ở `IdentityMap`, không đổi
  ở đợt này.

---

# Đợt 2026-08-13 (m) — AI Agent integration: xác thực /chat + /market, Phase 6 mở đầu (ranking engine tối thiểu), đề xuất tư vấn CHỜ DUYỆT

Status: **COMPLETE cho phạm vi đã giao. Phase 6 CHÍNH THỨC BẮT ĐẦU ở đợt này**
(quyết định của người vận hành, sau khi được cảnh báo rõ về
`tests/test_ranking_boundary.py` — xem "Quyết định mở Phase 6" bên dưới).

## Bối cảnh: audit trước khi sửa

Trước khi đổi bất kỳ dòng nào, đọc `src/api/routes.py`, `src/agents/`,
`src/services/llm.py`, `src/services/gemini.py`, `src/main.py` và phát hiện:

* **`POST /api/v1/chat` và toàn bộ `GET/POST /api/v1/market/*` (12 route)
  KHÔNG có Depends xác thực nào** — bất kỳ ai gọi được `/api/v1` đều tốn quota
  Gemini và đọc/ghi trạng thái mô phỏng `market_repository` mà không cần chứng
  minh danh tính. Đối lập hẳn với `dashboard.py`, nơi MỌI route đều
  `Depends(require_role(...))`.
* `src/agents/graph.py`/`state.py`/`nodes/example_node.py` là một khung
  LangGraph THẬT nhưng hoàn toàn MỒ CÔI — không router nào import nó,
  `analyze_node`/`respond_node` là `TODO` trả về `f"Phân tích: {query}"`.
* `chatWithAgent` (FE) là hàm DUY NHẤT trong nhóm market/chat đã được NỐI THẬT
  (`api.post("/v1/chat", ...)`) — mọi hàm `getMarket*`/`decideMarket*` khác đã
  bị stub rỗng ở đợt Dashboard Integration (đúng, vì đó là tính năng giá/AI-
  approval bị cấm dựng ở đợt đó).
* `ranking_configs`/`ranking_runs`/`ranking_scores`/`feature_snapshots` (Phase
  2, migration 0014/0015) tồn tại nhưng KHÔNG module nào ghi — được canh bởi
  `tests/test_ranking_boundary.py` (15 test, không cần DB), và
  `docs/ranking/implementation_plan.md` (902 dòng, đã có sẵn trong repo) mô tả
  đầy đủ một động cơ xếp hạng (worker RQ, ma trận cò kích hoạt, endpoint khảo
  sát) CHƯA được cài đặt.

## Quyết định mở Phase 6

Việc thực hiện đề xuất tư vấn dựa trên xếp hạng (yêu cầu ban đầu) đòi
`run_ranking()` ghi vào `ranking_runs`/`ranking_scores` — đúng thứ
`test_ranking_boundary.py` được viết ra để CẤM cho tới khi Phase 6 bắt đầu.
Đây không phải một blocker kỹ thuật đi vòng được — nó là một ranh giới phase có
chủ đích, chính docstring gốc của file test đã nói: "Khi Phase 6 bắt đầu, đúng
những test này phải ĐỎ — đó là tín hiệu chuyển phase, không phải hồi quy... ai
sửa nó theo hiện thực mới thì đang làm đúng việc." Đã dừng lại, báo cáo rõ
blocker này, và người vận hành CHỌN mở Phase 6 ngay (không phải mặc định).
Phạm vi mở ra là **một lát cắt dọc tối thiểu**, KHÔNG phải toàn bộ động cơ của
`docs/ranking/implementation_plan.md` — xem "Còn thiếu so với tài liệu kế
hoạch" bên dưới.

## Đã làm

**A. Vá lỗ hổng xác thực (STEP 1, không đổi logic route).**
`src/api/routes.py` — thêm `Depends(require_role("business_viewer"))` vào cả
12 route (`/chat`, `/status`, 10 route `/market/*`), tái dùng đúng
`require_role`/`DashboardPrincipal` đã có ở `dashboard_auth.py`. Xác nhận trực
tiếp qua ASGI app thật: không có header → `401 MISSING_CREDENTIALS`; có token
viewer → qua được auth, chạm đúng logic gốc (502 từ chính Gemini, không phải
lỗi auth). `tests/test_api/test_routes.py` (7 test, không sửa) vẫn xanh — fixture
`client` dùng chung của `conftest.py` đã tự gắn token admin.

**B. Động cơ xếp hạng tối thiểu (Phase 6 mở đầu).**
`src/ranking/engine.py` (hàm THUẦN, không I/O — công thức §10.1 tài liệu kế
hoạch: oriented value theo direction, numerator/denominator có trọng số,
coverage, skip/zero/neutral, làm tròn 4 chữ số, xếp hạng hai mức
`rank_in_area`/`rank_in_project` với phá hoà tất định score→created_at→id) +
`src/ranking/service.py` (`run_ranking()` — đọc `units`/`deals`/`areas` TRỰC
TIẾP, KHÔNG đọc `sales_records`/`absorption_daily`, tính bốn đặc trưng vận
hành của config v1 seed sẵn ở 0014, vật chất hoá `feature_snapshots`, ghi
`ranking_runs`/`ranking_scores` theo đúng luật xoá-rồi-chèn phạm vi dự án của
Phase 2). Chạy ĐỒNG BỘ trong request — không worker, không hàng đợi.

**C. Bảng + migration mới.** `agent_recommendations` (`src/models/tables.py`,
`alembic/versions/0018_agent_recommendations.py`) — CHECK constraint cưỡng chế
status khởi tạo luôn `pending_approval` và `decided_by`/`decided_at` chỉ có
giá trị cùng lúc với một quyết định. **Đây là bước duyệt người mà `AGENTS.md`
coi là yêu cầu cứng — không có đường ghi thẳng `approved`.**

**D. `src/api/agent.py`** — router `/agent`, 4 route:
`POST /recommendations` (202, `require_viewer` + `require_project_in_scope`,
chạy `run_ranking()` rồi gọi LangGraph agent, lưu `pending_approval`),
`GET /recommendations/{id}`, `POST /recommendations/{id}/approve`,
`POST /recommendations/{id}/reject` (hai route sau đòi
**`require_role("pipeline_operator")`, CAO HƠN mức đọc `business_viewer`** —
quyết định có chủ đích, duyệt là một hành động ghi không thể đứng cùng mức
với xem dashboard).

**E. LangGraph nối lại thật.** `src/agents/nodes/example_node.py` đổi tên
thành `ranking_node.py` (không còn "example" nào ở nội dung); `analyze_node`
định dạng `ranking_scores`/`absorption` (đã đọc DB THẬT ở `src/api/agent.py`,
qua `AreaService().summary()` — hàm dashboard cũ đang dùng, không viết lại);
`respond_node` là NƠI DUY NHẤT gọi `src/services/llm.py`, yêu cầu JSON
`{summary, recommended_actions}`, không parse được thì giữ nguyên văn bản làm
summary và `recommended_actions=[]` — không bịa hành động không có trong output
LLM. `src/agents/state.py` mở rộng thêm field, KHÔNG xoá field cũ.

**F. `requirements.txt` đã khai `langchain-openai>=0.3.0` nhưng chưa từng được
cài trong venv cục bộ** (không ai từng import `src.services.llm` cho tới đợt
này — `src/agents/` mồ côi, `/chat` dùng `gemini.py` gọi httpx trực tiếp). Cài
qua `pip install langchain-openai` — không sửa `requirements.txt` (đã đúng).

**G. FE.** `frontend/src/api/endpoints.js` — 4 hàm mới
(`createRecommendation`/`getRecommendation`/`approveRecommendation`/
`rejectRecommendation`), không đổi hàm cũ. `frontend/src/hooks/
useAgentRecommendation.js` (mới) — khớp quy ước `useAsync` (state phẳng
`{data, loading, error}`), thêm `approve(id, reason, actor)`/`reject(...)`
đòi `actor` tường minh (chưa có đăng nhập cá nhân hoá ở FE để tự suy ra —
bịa một actor mặc định sẽ làm rỗng chính bước duyệt).

**H. `tests/test_ranking_boundary.py` viết lại theo hiện thực mới** (đúng như
docstring gốc yêu cầu khi Phase 6 bắt đầu): ranh giới MỚI — chỉ
`src/ranking/service.py` được ghi vào bốn bảng xếp hạng, `engine.py` vẫn phải
THUẦN, không route nào tạo thẳng `agent_recommendations.status='approved'`,
duyệt đòi vai trò cao hơn đọc, lịch sử migration giờ là 18 (không phải 17).

## Còn thiếu so với `docs/ranking/implementation_plan.md` (biết rõ, không giấu)

Lát cắt này KHÔNG có: worker RQ / hàng đợi tính lại, ma trận cò kích hoạt sau
sync (§8), endpoint khảo sát đặc trưng (`view_quality`/`natural_light`/...),
API đọc xếp hạng độc lập (`GET /ranking/launches/:project_id`), job
`ranking_audit` dọn run kẹt, và semantics `ALREADY_CLAIMED`/`STALE_RESULT` đầy
đủ cho nhiều worker chạy song song thật (guard chống ghi đè hiện tại chỉ so
`computed_at`, đủ cho một request đồng bộ, KHÔNG đủ cho worker phân tán).
`run_ranking()` luôn tính lại TOÀN BỘ dự án mỗi lần gọi (đúng bất biến
`rank_in_project`), không cache giữa hai lần gọi liên tiếp.

## Kiểm chứng — lệnh thật, kết quả thật

```
ruff check src/ tests/                          -> PASS (4 lỗi UP017 tiền-có
                                                    sẵn ở src/services/market.py,
                                                    KHÔNG đụng ở đợt này)
npx vitest run (frontend/)                      -> 9 files, 82 tests, 0 failed
npm run build (frontend/)                       -> built in 2.77s
TEST_TARGET=tests/test_ranking/test_engine.py bash scripts/test_db.sh
                                                 -> 11 passed (hàm thuần, không cần DB)
TEST_TARGET=tests/test_ranking_boundary.py bash scripts/test_db.sh
                                                 -> 13 passed
TEST_TARGET=tests/test_agent_e2e.py bash scripts/test_db.sh
                                                 -> 11 passed (điểm ranking_scores khớp
                                                    CHÍNH XÁC số tính tay từ units/deals
                                                    thật: 0.8500/0.6500/0.1500)
TEST_TARGET=tests bash scripts/test_db.sh (toàn bộ)
                                                 -> 1205 passed, 1 skipped, 14 errors
                                                    (test_real_hierarchy_e2e.py — có
                                                    trước, cần container Mini CRM sống,
                                                    không liên quan đợt này), 0 failed
```

Lần chạy đầu của bộ toàn bộ có 1 FAIL thật
(`tests/test_agents/test_graph.py::test_agent_basic_flow`) — test cũ kiểm
hành vi generic của stub, gãy vì `respond_node` giờ trả `summary` thay vì
`response`. Đã viết lại 4 test khớp hành vi THẬT mới (LLM luôn mock qua
`mock_llm`/`monkeypatch`, không gọi mạng thật), chạy lại toàn bộ: 0 fail.

## Rủi ro / giới hạn còn lại

* `respond_node` khi `get_llm()` (Gemini qua wrapper `ChatOpenAI`, xem
  `src/services/llm.py`) lỗi thật (không phải test) sẽ trả về một summary
  chung chung "không tạo được phân tích" thay vì raise — nhánh này KHÔNG có
  test riêng với lỗi mạng thật (chỉ test với mock trả JSON hợp lệ / không hợp
  lệ), vì gọi Gemini thật trong CI là không tất định.
* `run_ranking()`'s nhánh `except Exception` (đánh dấu run `failed`) không có
  test injecting lỗi giữa chừng — logic đơn giản (rollback + UPDATE status),
  rủi ro thấp nhưng chưa được kiểm trực tiếp.
* Chưa xác nhận thị giác (không có trình duyệt) cho bất kỳ màn hình FE nào
  dùng `useAgentRecommendation` — hook có test đơn vị đầy đủ, chưa có UI nào
  thật sự gọi nó (nằm ngoài phạm vi 5 bước được giao).

---

# Đợt 2026-08-13 (l) — Dashboard Integration: nối dashboard nghiệp vụ theo dự án

Status: **COMPLETE cho phạm vi FE đã giao; một khối hạ tầng KHÔNG LIÊN QUAN bị
phát hiện chặn E2E dữ liệu thật (đã ghi lại, KHÔNG sửa — ngoài phạm vi đợt này)**

Phạm vi: nối `AbsorptionDashboard` (đã dựng từ trước, đủ 6 widget, đúng hệ
thiết kế) vào một route THẬT theo dự án (`/projects/:externalId/dashboard`),
thay bốn hàm API dashboard chưa từng tồn tại bằng bốn hàm THẬT gọi đúng route
backend đã có (không route mới nào), và thêm MỘT chỉ báo mới (hướng vận tốc
7 ngày so 30 ngày). Không đổi gì ở backend (`src/`, `minicrm/` — `git status`
xác nhận rỗng cho cả hai cây trong đợt này).

## Hai lỗi CÓ TRƯỚC, phát hiện lúc bắt tay vào việc (không phải do đợt này gây ra)

**1. `frontend/src/pages/DashboardPage.jsx` có dấu xung đột merge CHƯA GIẢI
QUYẾT còn sống trong file** (`git status` báo `UU`, không `MERGE_HEAD`/
`rebase-merge` nào đang chạy — dấu vết của một `stash pop` xung đột chưa dọn,
`git stash list` còn `stash@{0}: autostash`). `npm run build` xác nhận: gãy
ngay ở dòng 2 (`Unexpected "<<"`). Không nhánh nào của xung đột dùng được:
nhánh "Updated upstream" trỏ `/dashboard` sang `MarketPrototypePage` (mô phỏng
giá/đề xuất AI — đúng loại tính năng bị cấm dựng thêm ở đợt này); nhánh
"Stashed changes" import ba file component đã bị xoá từ một lần tái cấu trúc
trước (`components/SummaryCards.jsx`/`AreaSelector.jsx`/`AbsorptionChart.jsx`
không còn tồn tại). Đã giải quyết — xem "Đã làm" bên dưới.

**2. `AbsorptionDashboard.jsx` (component dashboard THẬT, đã dựng đủ 6 widget
từ trước) gọi bốn hàm API (`getDashboardSummary`, `getDashboardTrend`,
`getDashboardAreas`, `getDataQuality`) CHƯA TỪNG được định nghĩa ở
`api/endpoints.js`.** `mock.js` có sẵn bốn route mock khớp tên
(`/dashboard/summary`, `/dashboard/trend`, `/dashboard/areas`,
`/dashboard/data-quality`) nhưng `USE_MOCK = false`, nên bốn hàm thật chưa bao
giờ được viết — một tính năng dựng dở, đứt ở đúng lớp nối API. Hệ quả: trang
`/projects/:id` (nhúng trực tiếp component này) đã CRASH (`TypeError:
getDashboardSummary is not a function`) trước đợt này.

Cả hai lỗi được phát hiện bằng cách chạy THẬT `npm run build`, không phải đọc
mã suy luận — khớp đúng yêu cầu "không tuyên bố hoàn thành mà không chạy kiểm."

## Đã làm

**A — Giải quyết `DashboardPage.jsx`.** Xoá cả hai nhánh xung đột. `/dashboard`
không còn ngữ cảnh dự án nào để chọn mặc định (dashboard giờ khoá theo MỘT dự
án cụ thể) nên route này giờ chỉ `<Navigate to="/projects" replace />` — không
đoán "dự án đầu tiên" (đúng nguyên tắc đã chốt ở Phase F, chống lại đúng lớp
lỗi `activeProjectId()` cache). Bỏ mục nav "Dashboard" trùng lặp trong
`AppLayout.jsx` (trỏ cùng một điểm đến với "Dự án").

**B — Bốn hàm API dashboard, viết THẬT, không đoán.** Xác minh CHÍNH XÁC hình
dạng phản hồi bằng cách gọi trực tiếp `AbsorptionPointOut(...).model_dump_json()`
qua pydantic thật (không đọc mã suy luận): `Decimal` (`velocity_7d`,
`velocity_30d`, `avg_velocity_30d`) serialize thành CHUỖI trong JSON, không
phải số — cả bốn hàm ép kiểu `Number(...)` tường minh trước khi so sánh/vẽ.
  - `getDashboardSummary` — gọi `/absorption/summary`; `total_units` tính từ
    `Σ AreaOut.total_units` (KHÔNG cộng `units_sold + units_remaining` của
    `/absorption/summary` — hai số đó đọc từ hai bảng riêng dưới bộ tính
    `legacy_aggregate`, xác nhận ở `src/services/absorption.py:334-346`, cộng
    lại không phải tổng số căn thật).
  - `getDashboardTrend` — gọi lại `getAbsorption` đã có; tự tính
    `cumulative_sold` (tổng dồn theo ngày) và `absorption_rate` mỗi điểm
    (`null` nếu không có `areaTotalUnits`, không phải 0 bịa); trả kèm
    `latestVelocity7d`/`latestVelocity30d` của điểm gần nhất cho chỉ báo D.
  - `getDashboardAreas` — MỘT lời gọi `/inventory` cho cả dự án (không N lời
    gọi từng phân khu); `velocity`/`latest_data`/`status` để `null`/`undefined`
    vì backend không có nguồn cho ba trường này ở mức "mọi phân khu trong một
    lời gọi" — hai widget tiêu thụ đã tự hiện "N/A", không bịa số.
  - `getDataQuality` — mốc đồng bộ từ `/absorption/summary`, bất thường từ
    `InventoryOut.anomalies` (`GET /inventory`); ánh xạ `code` (`HELD_EXCEEDS_STOCK`,
    `DEAL_ON_DELETED_UNIT`) sang câu tiếng Việt, KHÔNG nuốt câm lặng một `code`
    lạ (vẫn hiện nguyên `code` nếu gặp loại mới).

**C — Route mới, danh tính đúng quy ước.** `/projects/:externalId/dashboard`
(mới) + đổi `/projects/:id` (UUID nội bộ) thành `/projects/:externalId`
(external_id Mini CRM — cùng quy ước `useProjectScope`/`getProjectByExternalId`
đã dùng, thay vì hai hệ danh tính song song trong một app). Route
`/projects/:id/areas/:areaId` (AreaDetailPage) giữ NGUYÊN — không có lối vào
UI nào trỏ tới nó hôm nay (`grep` xác nhận), và nó dùng `getUnitRanking`
(Phase 6, ngoài phạm vi) nên không đụng tới. `ProjectDashboardPage.jsx` (mới)
xác nhận dự án tồn tại VÀ trong phạm vi (`getProjectByExternalId`) TRƯỚC khi
vẽ dashboard — deep link thẳng vào route vẫn báo đúng 404/403 thay vì một
dashboard rỗng câm lặng.

**D — `useProjectScope` là nguồn sự thật DUY NHẤT cho ngữ cảnh dự án.** Mở
rộng hook nhận tuỳ chọn `{ projectExternalId }`: có giá trị này (route dạng
`/projects/:externalId/dashboard`) → dự án đọc từ ĐÓ, bỏ qua `?project=`;
không truyền gì → giữ NGUYÊN hành vi cũ (query-param, `CatalogPage.jsx` không
đổi gì). `setProjectExternalId` thành no-op có chủ đích ở chế độ path-scoped —
đổi dự án là điều hướng route (`navigate()` ở `AbsorptionDashboard`), hook
không tự biết về router. `AbsorptionDashboard.jsx` viết lại: bỏ state cục bộ
`useState` cho project/area (nguồn gây lệch cũ), dùng `useProjectScope` cho cả
hai; phân khu tự chọn phân khu ĐẦU TIÊN khi vào trang lần đầu và GHI vào URL
(`?area=`) — không phải một mặc định ẩn trong state.

**E — Nối 6 widget đã có vào dữ liệu thật**, đúng đề bài (5 câu hỏi chủ dự án):
`KpiCards` (Q1 + Q2), `AbsorptionTrendChart` (Q1/Q2), `AreaComparison` +
`AreaDetailTable` (Q3/Q4), `DataQualityPanel` (Q5) — KHÔNG sửa hình dạng
props/JSX của bất kỳ widget nào (trừ `KpiCards`, xem F) — chỉ thay nguồn dữ
liệu đổ vào. `FilterToolbar` (không sửa) nhận `{id, name}` đã ánh xạ sẵn từ
`external_id`/`area_name · unit_type`, không tự đọc field lạ.

**F — Chỉ báo hướng vận tốc (Q2).** `utils/velocity.js` (mới) —
`deriveVelocityDirection(v7, v30)`: `v7 > v30` → "increasing", `v7 < v30` →
"decreasing", bằng nhau → "stable", một trong hai `null`/không phải số →
"unknown" — ĐÚNG luật đã giao, không suy đoán thêm ngưỡng "đủ lịch sử" nào
khác ngoài "có hai con số hay không". `KpiCards.jsx` thêm mũi tên +
nhãn cạnh "Avg Velocity" khi có hướng xác định; "unknown" → KHÔNG vẽ gì (không
đưa ra tuyên bố hướng khi thiếu dữ liệu, đúng yêu cầu); không vẽ trong lúc
đang tải (tránh tuyên bố cũ trong lúc làm mới).

**G — Sửa dọc theo đường: hai lỗi khác lộ ra khi build thật chạy xa hơn.**
`ProjectsPage.jsx` điều hướng bằng `p.id` — trường KHÔNG tồn tại trên
`ProjectSummary` (chỉ có `project_id`/`external_id`), nên MỌI thẻ dự án trước
đây điều hướng tới `/projects/undefined`. Sửa: dùng `p.external_id`, vô hiệu
hoá + chú thích thẻ cho dự án di sản (chưa có `external_id`). `ProjectDetailPage.jsx`
import `getProject` — hàm KHÔNG tồn tại (chỉ có `getProjectByExternalId`) —
viết lại toàn bộ trang: bỏ nhúng dashboard trực tiếp, xác nhận dự án qua
`getProjectByExternalId`, thêm CTA "Xem dashboard" duy nhất sang route dashboard
mới, xử lý 404/403 riêng biệt, ảnh bìa lỗi tự ẩn (không icon vỡ, không
placeholder bịa).

**H — Hai khối build gãy KHÔNG LIÊN QUAN, phát hiện SAU khi sửa (A), phải xử lý
để `npm run build` chạy được hết.** Sau khi hết xung đột (A), build tiến xa
hơn và lộ ra: `MarketPrototypePage.jsx` (dùng bởi `/inventory`, `/ai-agent`,
`/audit` — CẢ BA route đó, không route nào của đợt này) import 11 hàm
(`getMarketDashboard`, `generateMarketProposal`, `decideMarketProposal`, ...)
CHƯA TỪNG được định nghĩa; `AreaDetailPage.jsx` import `getUnitRanking` (Phase
6 — xếp hạng khả năng bán) cũng CHƯA TỪNG được định nghĩa. CẢ HAI là lỗi CÓ
TRƯỚC, trước đây bị che bởi việc build gãy sớm hơn ở (A1). Dựng THẬT hai tính
năng đó (mô phỏng giá/đề xuất AI, xếp hạng) bị chỉ thị đợt này CẤM. Xử lý: thêm
12 hàm STUB vào `endpoints.js` — phần ĐỌC trả rỗng (trang gọi đã có
`.catch(() => {})` bao ngoài, tự vào trạng thái rỗng), phần GHI/HÀNH ĐỘNG từ
chối rõ ràng bằng `Error` (không báo thành công lạc quan cho một thao tác chưa
tồn tại). KHÔNG logic nghiệp vụ nào được thêm — chỉ đủ để module resolve khi
build. `/inventory`, `/ai-agent`, `/audit` vẫn ở nguyên trạng thái "chưa nối
API thật" như trước đợt này, chỉ khác là build không còn gãy vì chúng.

## Khối hạ tầng KHÔNG LIÊN QUAN phát hiện lúc kiểm E2E — KHÔNG sửa, ghi lại

Khi tạo dữ liệu thật qua Mini CRM để kiểm E2E có dữ liệu (POST /projects,
/areas, /units thật qua cổng ghi D-14), việc ĐẨY sang backend (cả v1 đồng bộ
lẫn v2 relay) đều trả `401 INVALID_API_KEY` từ backend, dù `MINICRM_SYNC_API_KEY`
trong `.env` khớp NGUYÊN VĂN giữa hai container (`docker compose exec api/minicrm
printenv` xác nhận cùng giá trị). Suy ra: backend không so token bằng biến môi
trường mà tra trong bảng `sync_credentials` — bảng này (cùng `projects`/`areas`
của backend) đang RỖNG (database dev đã bị dọn sạch ở một thời điểm nào đó).
Fix đúng (`scripts/sync_simulator.py --issue-key`) sẽ CẤP MỘT KHOÁ MỚI, đòi cập
nhật `.env` + khởi động lại container `minicrm` — một thay đổi hạ tầng dev
KHÔNG liên quan tới "nối dashboard", nên KHÔNG thực hiện ở đợt này. Dự án test
`P-0001`/`A-0001`/ba căn `U-0001..3` đã tạo Ở MINI CRM (cục bộ, chưa từng tới
backend) — vô hại, để nguyên, không ảnh hưởng gì (backend không biết chúng tồn
tại).

## Files changed

**Mới:**
- `frontend/src/pages/ProjectDashboardPage.jsx`, `.test.jsx`
- `frontend/src/utils/velocity.js`, `.test.js`
- `frontend/src/api/endpoints.dashboard.test.js`
- `frontend/src/components/dashboard/KpiCards.test.jsx`

**Sửa:**
- `frontend/src/pages/DashboardPage.jsx` — giải quyết xung đột, redirect.
- `frontend/src/api/endpoints.js` — bỏ `listProjectZones` trùng khai (một bản
  đã CHẾT, bị bản sau ghi đè im lặng — hợp lệ về cú pháp nhưng gây nhiễu); thêm
  4 hàm dashboard thật + 12 hàm stub (mục H) + import `classifyFreshness`.
- `frontend/src/hooks/useProjectScope.js` — tuỳ chọn `{projectExternalId}`.
- `frontend/src/hooks/useProjectScope.test.jsx` — thêm 4 test chế độ path-scoped.
- `frontend/src/components/dashboard/AbsorptionDashboard.jsx` — viết lại toàn bộ.
- `frontend/src/components/dashboard/KpiCards.jsx` — thêm chỉ báo hướng vận tốc.
- `frontend/src/components/dashboard/DashboardHeader.jsx` — thêm prop `title` tuỳ chọn.
- `frontend/src/pages/ProjectDetailPage.jsx` — viết lại toàn bộ.
- `frontend/src/pages/ProjectsPage.jsx` — sửa điều hướng dùng `external_id`.
- `frontend/src/App.jsx` — route mới, đổi tên tham số route.
- `frontend/src/components/AppLayout.jsx` — bỏ mục nav trùng, sửa điểm đến logo.

**KHÔNG đổi:** toàn bộ `src/` (backend) và `minicrm/` — `git status` xác nhận
rỗng cho cả hai cây trong đợt này. Không migration nào.

## Test Results — lệnh THẬT đã chạy, kết quả THẬT

```
npm run build
→ ✓ built in 4.57s (sau khi giải quyết A + H — trước đó gãy ở A, rồi ở H)

npx vitest run
→ Test Files  8 passed (8)
→ Tests  76 passed (76)      [32 có từ trước + 44 mới của đợt này]
→ 0 failed, 0 skipped

Xác minh HTTP thật qua container thật đang chạy (không mock):
  GET /api/v1/projects (có token)         → 200, []  (backend dev rỗng)
  GET /api/v1/projects/P-0001 (có token)  → 404 PROJECT_NOT_FOUND
  GET /api/v1/projects (không token)      → 401 MISSING_CREDENTIALS
  GET /api/v1/inventory?external_project_id=P-0001 → 404 PROJECT_NOT_FOUND
  Qua proxy dev server (:5173, không phải :8000 thẳng):
    GET /api/v1/projects                  → 200, [] (proxy forward đúng)
    GET /projects/P-0001/dashboard (SPA)  → 200 (route client-side phục vụ được)
    /src/pages/DashboardPage.jsx (source thật đang chạy) → không còn dấu xung đột

.venv/bin/python -c "AbsorptionPointOut(...).model_dump_json()"
→ xác nhận Decimal serialize thành CHUỖI JSON (không phải số) — dùng để viết
  đúng phép ép kiểu trong endpoints.js, không đoán.
```

**KHÔNG chạy được / bị chặn bởi hạ tầng ngoài phạm vi:** kiểm E2E với dữ liệu
THẬT đã chiếu tới backend (`GET /absorption`, `/absorption/summary`,
`/inventory` với số liệu thật khác 404) — chặn bởi lỗ hổng `sync_credentials`
rỗng đã ghi ở mục trên, không phải lỗi của đợt này. Không có trình duyệt/công
cụ chụp màn hình trong bộ công cụ hiện có — không có xác nhận THỊ GIÁC (render
đúng pixel) rằng dashboard hiển thị đẹp; chỉ xác nhận được qua test component
(React Testing Library, DOM thật trong jsdom) + build + HTTP thật.

**Không có bước lint/format riêng** — `package.json` không khai `"lint"` nào
(xác nhận bằng đọc trực tiếp `scripts`), nên không có gì để chạy ở mục đó.

```
passed: 76
failed: 0
skipped: 0
```

## Remaining issues

- Kiểm E2E với dữ liệu THẬT đã tới backend chưa chạy được — chặn bởi
  `sync_credentials` rỗng ở database dev (mục "Khối hạ tầng KHÔNG LIÊN QUAN"
  ở trên). Không phải lỗi của đợt này; cần một quyết định vận hành riêng
  (cấp khoá mới + cập nhật `.env` + khởi động lại `minicrm`) trước khi chạy
  được.
- `MarketPrototypePage.jsx`/`getUnitRanking` vẫn ở trạng thái "chưa nối API
  thật" — chỉ không còn làm gãy build nữa. Không phải phạm vi đợt này (mục H).
- Chưa có xác nhận thị giác (screenshot/trình duyệt thật) — chỉ có DOM test +
  HTTP thật + build.
- `velocity`/`latest_data`/`status` (hot/normal/slow) mỗi phân khu ở
  `AreaComparison`/`AreaDetailTable` để `null`/`undefined` có chủ đích — không
  có nguồn backend cho ba trường này ở mức "một lời gọi cho cả dự án", và
  `status` cần một ngưỡng nghiệp vụ chưa ai chốt (cùng loại quyết định còn
  treo ở `docs/roadmap.md`, "NGƯỠNG ĐỘ TƯƠI").
- `PATCH /projects/{id}`/`PATCH /areas/{id}` (backend) vẫn không có
  `Depends(require_role(...))` nào — quan sát được trong lúc đọc mã, KHÔNG sửa
  (ngoài phạm vi FE của đợt này, thuộc phía backend).

## Phase 6 status

**KHÔNG bắt đầu, không đụng tới.** `getUnitRanking` chỉ là một stub rỗng để
build chạy được (mục H) — không tính điểm, không xếp hạng, không logic nghiệp
vụ nào phía sau nó.

---

# Đợt 2026-08-13 (k) — Phase C.5/D-14/E/F-G: relay tự động, xác thực ghi, phạm vi dự án, FE theo phạm vi

Phase C.5 — Automatic Relay: Status: **COMPLETE**
D-14 — Mini CRM Write Authentication: Status: **COMPLETE**
Phase E — Project-Scoped Authorization: Status: **COMPLETE**
Phase F/G — FE Project/Area Context and Scoped CRUD: Status: **PARTIAL**
Phase 6: **KHÔNG bắt đầu** (đúng như đầu bài — không được phép động tới).

Phạm vi đợt này: bốn khối được giao liên tiếp trong cùng một chỉ thị — biến việc
đẩy dữ liệu từ Mini CRM sang Backend thành TỰ ĐỘNG và BỀN (không còn thao tác tay
"lấy phong bì rồi gửi"), khoá route GHI của Mini CRM sau xác thực vai trò +
phạm vi dự án, khoá route ĐỌC/vận hành của Backend theo CÙNG một mô hình phạm vi,
và nối FE hiện có (`frontend/`) vào cả hai mặt — đọc từ Backend, ghi qua Mini CRM
đã xác thực. Kiến trúc đóng băng được giữ nguyên trong suốt: Mini CRM vẫn là chủ
sở hữu duy nhất của Project/Area/Unit/Deal; Backend vẫn chỉ ingest + chiếu
(projection) chỉ-đọc + phân tích; Backend không có route ghi nghiệp vụ nào mới.

## C.5 — Automatic Relay: kết quả

**Đường gửi.** `minicrm/app/relay.py` (mới) — một `RelayLoop` chạy nền trong
đúng tiến trình FastAPI của Mini CRM (nối qua `lifespan` ở `minicrm/app/main.py`),
tick mỗi `MINICRM_RELAY_INTERVAL_SECONDS` (mặc định 5s, dev 3s), mỗi tick quét
`crm_outbox` lấy các dòng `http_status IS NULL OR http_status >= 500` (còn có thể
thử lại) và gọi LẠI ĐÚNG hàm `crud.deliver()` mà đường đồng bộ v1 vốn dùng lúc
ghi — không có đường gửi thứ hai, không có logic diễn giải kết quả thứ hai.

**Chính sách thử lại.** Mã hoá hoàn toàn bằng việc CHỌN dòng, không có cột đếm
lần thử riêng: 2xx/4xx là chốt (không bao giờ bị chọn lại); NULL/5xx còn thử lại
vô thời hạn tới khi backend hồi phục hoặc route trả 4xx dứt khoát.

**Idempotency.** `external_batch_id` ổn định qua mọi lần thử — relay gửi lại
ĐÚNG phong bì đã ký lúc ghi, không tạo dòng outbox thứ hai cho cùng một lần ghi
gốc. Backend dedup theo batch_id (route ingest cũ, không đổi) nên gửi lại không
tạo chiếu nghiệp vụ trùng.

**Khôi phục sau sự cố.** Backend tắt → ghi cục bộ Mini CRM vẫn commit, dòng
outbox nằm lại `http_status=NULL`; Backend bật lại → tick relay kế tiếp tự gửi,
không cần thao tác tay.

**Giới hạn đã ghi rõ, không phải lỗi:** đảm bảo "không gửi đúp" dựa vào ĐÚNG MỘT
tiến trình `minicrm` (docker-compose chỉ khai một service) xử lý tick tuần tự —
KHÔNG dùng `FOR UPDATE SKIP LOCKED`. Nếu tương lai chạy nhiều bản sao `minicrm`,
việc này phải được làm lại.

**Kết quả E2E thật:** `pytest minicrm/tests/test_real_relay.py -v` → **3 passed**
(ghi rồi CHỈ CHỜ, không gọi `/resend`/`relay_tick()` tay: gửi tự động; outage
backend sống sót cục bộ và tự phục hồi; restart Mini CRM giữa chừng không mất
dòng đang chờ). Container thật, HTTP thật.

## D-14 — Mini CRM Write Authentication: kết quả

**Route được bảo vệ.** Toàn bộ route GHI của `projects`/`areas`/`units`/`deals`
(tạo/sửa/archive) cộng `POST /outbox/replay-stale` và `POST /outbox/{id}/resend`.
`POST /projects` yêu cầu `admin` (không có "dự án cha" để kiểm phạm vi). Mọi route
ghi còn lại yêu cầu tối thiểu `pipeline_operator` VÀ phạm vi dự án đã resolve
(qua `minicrm/app/scope.py`) chứa external_id của dự án bị ảnh hưởng.

**Vai trò.** `business_viewer < pipeline_operator < admin`, ba token TĨNH đọc từ
biến môi trường (`MINICRM_AUTH_*_TOKEN`), KHÔNG có token nào là mặc định — thiếu
cấu hình = mặt ghi ĐÓNG (503), không phải mở.

**Phạm vi dự án.** `ProjectScope = frozenset[str] | Literal["ALL"]`, cấu hình qua
`MINICRM_AUTH_PROJECT_SCOPE` (JSON `{"<token>": ["P-0001"]}` hoặc `{"<token>":
"ALL"}`). Token vắng mặt trong JSON = phạm vi RỖNG dù token hợp lệ — không có
đường vòng admin ngầm định, `"ALL"` phải được cấp TƯỜNG MINH.

**401 vs 403.** Thiếu/sai token → 401. Token hợp lệ nhưng vai trò không đủ HOẶC
dự án ngoài phạm vi → 403 `PROJECT_OUT_OF_SCOPE`. Nhất quán trên mọi route.

**Bí mật.** Không token nào hardcode trong mã nguồn hay log; lỗi 401/403 không lộ
giá trị token thật.

**Kết quả test:** `minicrm/tests/test_auth.py` — 15 passed (local).
`minicrm/tests/test_real_auth.py` — 9 passed (container thật).

## Phase E — Project-Scoped Authorization: kết quả

**Route được khoanh phạm vi.** `GET /projects`, `/projects/{external_id}`,
`/areas`, `/areas/{external_id}`, `/inventory`, `/deals`, `/sync-runs`,
`/sync-errors`, `/sync-runs/{id}/payload`, cộng `GET /me/permissions` (mới, phục
vụ FE hỏi "tôi được thấy gì").

**Mô hình phạm vi.** Giống hệt cấu trúc D-14 nhưng là hệ token RIÊNG của Backend
(`DASHBOARD_*_TOKEN` + `DASHBOARD_PROJECT_SCOPE`, đã có sẵn từ Phase 5.5, Phase E
chỉ thêm phạm vi dự án lên trên vai trò đã có) — HAI mặt phẳng xác thực tách biệt
vật lý, không dùng chung token, chỉ giống nhau về HÌNH DẠNG cấu hình.

**Tầng thực thi.** Lọc phạm vi ở TẦNG TRUY VẤN
(`resolve_scope_project_ids`/`require_project_in_scope` trong
`src/services/dashboard_auth.py`), không phải lọc ở FE và không phải chỉ chặn ở
tầng route: danh sách tự lọc theo `project_id IN (...)`, chi tiết/aggregate/join
(`areas` JOIN `projects` để lấy `project_external_id`) đều kiểm qua CÙNG một hàm.
Truy cập chéo dự án trực tiếp qua ID → 403, không phải 404 (tránh lập lờ có/không
tồn tại). Phạm vi rỗng = không đọc được gì, fail-closed.

**`GET /me/permissions`** trả `{role, project_scope}` — FE dùng để tự quyết định
hiển thị chọn dự án nào mà không cần đoán bằng thử-sai.

**Kết quả test:** `tests/test_api/test_project_scope.py` — 19 test (viewer A đọc
được/không đọc được B; operator thao tác được A; admin không-ALL không vượt được
biên; admin-ALL vượt được; phạm vi rỗng; phạm vi áp cho list/detail/count/
aggregate/join; không lách được qua query param hay external ID; 401/403; liệt kê
route) — nằm trong con số 1190 passed của bộ đầy đủ bên dưới (không chạy tách
biệt trong đợt xác minh cuối, nhưng có trong mọi lần chạy `TEST_TARGET=tests`).

## Phase F/G — FE Project/Area Context and Scoped CRUD: kết quả

Dùng `frontend/` HIỆN CÓ — không dựng admin console thứ hai.

**Project/Area selector.** `frontend/src/hooks/useProjectScope.js` (mới) — đọc
`GET /projects` (đã tự lọc phạm vi ở Backend) và `GET /areas?project=` cho dự án
đang chọn; lưu `project`/`area` vào URL query — deep link phục hồi đúng phạm vi;
đổi dự án tự reset khu vực + dữ liệu đã lọc; KHÔNG có cache `activeProjectId()`
toàn cục nào còn sống sót. `ProjectSelector.jsx`/`ConnectPanel.jsx` (mới) nối vào
`TopBar` của `App.jsx`. `ConnectPanel` là ô dán TOKEN tường minh — hệ thống chưa
có đăng nhập thật, nên component tự khai đúng nó là gì, không giả vờ là luồng
đăng nhập.

**Mục tiêu ghi/đọc.** FE ĐỌC từ Backend (`frontend/src/api/client.js` → `/api`).
FE GHI qua Mini CRM đã xác thực, qua proxy same-origin `/minicrm-api` mới thêm ở
`vite.config.js` (tránh phải mở CORS ở Mini CRM) — KHÔNG route ghi nào chạm bảng
mirror của Backend trực tiếp.

**CatalogPage.jsx** — viết lại: `addProject`/`addArea`/`saveProject`/`saveArea`
gọi Mini CRM trước (trường canonical), chờ phản hồi ĐÃ XÁC NHẬN (không lạc quan
trước), rồi `watchSync()` polling (tối đa 6 lần, cách 1.5s) hiển thị trạng thái
đang đồng bộ/đã tới/lỗi thay vì giả định chiếu đã có ngay.

**401/403.** `client.js` phân biệt và trả thông điệp an toàn, không lộ payload
thô; `ConnectPanel` cho phép dán lại token khi 401.

**KHÔNG làm trong đợt này (nợ tường minh, không phải bỏ sót):**
- Trang Units/Deals theo phạm vi — CHƯA từng tồn tại trước đợt này, không đủ thời
  gian dựng mới trong cùng đợt với ba khối backend còn lại.
- `DashboardPage.jsx` chưa nối lại `useProjectScope` — các route `/absorption`
  nó gọi cố tình KHÔNG bị Phase E khoanh phạm vi (đọc tổng hợp, không phải theo
  dự án), nên việc nối không bắt buộc cho gate của đợt này nhưng vẫn là việc còn
  treo cho phiên sau.

**Kết quả test FE:** `npx vitest run` (frontend/) — **32 passed** (4 file):
`client.test.js` (header xác thực, xử lý 401/403), `freshness.test.js`,
`useProjectScope.test.jsx` (lọc theo quyền, reset khu vực khi đổi dự án, giữ
phạm vi qua URL), `ProjectSelector.test.jsx`. `npm run build` — thành công, không
lỗi TypeScript/bundler.

## Files changed (đợt này)

**Mini CRM — C.5 (relay):**
- `minicrm/app/relay.py` (mới) — vòng relay nền, `RelayLoop`, `relay_tick()`.
- `minicrm/app/main.py` — `lifespan` khởi động/dừng `relay_loop`.
- `minicrm/app/sync_client.py` — `ENTITY_PATH` mở rộng cho v2; sửa thông báo lỗi
  đã lỗi thời (claim "v2 không có đường gửi" không còn đúng).
- `minicrm/app/config.py` — `relay_enabled`/`relay_interval_seconds`/
  `relay_batch_size`.
- `minicrm/app/crud.py` — cập nhật thông báo `_reject_v2_delivery()`.
- `minicrm/tests/test_relay.py`, `minicrm/tests/test_real_relay.py` (mới).

**Mini CRM — D-14 (xác thực ghi):**
- `minicrm/app/auth.py` (mới) — vai trò, phạm vi, `authenticate()`,
  `require_role()`, `require_scope()`.
- `minicrm/app/scope.py` (mới) — resolve phạm vi từ dữ liệu ghi
  (`project_for_unit_ref`, `project_for_deal_ref`, ...).
- `minicrm/app/config.py` — bốn biến `MINICRM_AUTH_*`.
- `minicrm/app/routers/{projects,areas,units,deals,outbox}.py` — gắn
  `Depends(require_role(...))` + kiểm phạm vi trên route ghi.
- `minicrm/tests/test_auth.py`, `minicrm/tests/test_real_auth.py` (mới).
- `minicrm/tests/real_env.py` — token D-14 + `BACKEND_AUTH_HEADER` (đợt xác minh
  cuối, xem "Sự cố phát hiện và xử lý").
- Rà soát lại ~15 file test Mini CRM đã có từ trước để gắn header xác thực.

**Backend — Phase E (phạm vi dự án):**
- `src/config.py` — `dashboard_project_scope`.
- `src/services/dashboard_auth.py` — `ProjectScope`, `project_scope` trên
  `DashboardPrincipal`, `resolve_scope_project_ids()`,
  `require_project_in_scope()`, `scope_permits()`.
- `src/api/dashboard.py` — khoanh phạm vi `list_projects`/`get_project_by_
  external_id`/`list_areas`/`get_area_by_external_id`; route mới
  `GET /me/permissions`.
- `src/api/inventory.py`, `src/api/sync.py` — khoanh phạm vi tồn kho/giao dịch/
  sync-runs/sync-errors/payload.
- `src/models/schemas.py` — `MePermissionsOut`.
- `tests/conftest.py` — token dashboard mặc định cho toàn bộ suite (xem sự cố
  bên dưới); `tests/test_api/test_project_scope.py` (mới, 19 test).
- Rà soát lại ~5 file test backend đã có từ trước để gắn header xác thực.

**Frontend — Phase F/G:**
- `frontend/src/api/client.js`, `frontend/src/api/endpoints.js` — client Mini
  CRM, endpoint theo phạm vi.
- `frontend/src/hooks/useProjectScope.js`, `frontend/src/components/
  {ProjectSelector,ConnectPanel}.jsx` (mới).
- `frontend/src/App.jsx`, `frontend/src/pages/CatalogPage.jsx`,
  `frontend/vite.config.js`.
- `frontend/package.json`, `frontend/vitest.config.js`,
  `frontend/src/test/setup.js` (mới) — khung test.
- Bốn file test mới (32 test, liệt kê ở trên).

**Hạ tầng dùng chung:**
- `docker-compose.yml` — biến relay + auth cho service `minicrm`;
  `VITE_MINICRM_PROXY_TARGET` cho `frontend`.
- `.env` / `.env.example` — token D-14, token Phase E, `DASHBOARD_PROJECT_SCOPE`,
  biến relay.

## Migrations

**KHÔNG migration nào được tạo trong đợt này.** Backend head vẫn
`0017_hierarchy_projection` — đã xác minh lại bằng `alembic heads` ngay trước
khi ghi mục này. Mini CRM head vẫn `0004_outbox_hierarchy_entities` — xác minh
tương tự. Cả bốn khối (C.5/D-14/E/F-G) đều không cần cột/bảng mới: relay đọc lại
`crm_outbox` đã có từ Phase C; xác thực đọc token từ biến môi trường, không từ
DB; phạm vi dự án so khớp `external_id` đã có sẵn ở cả hai hệ. 163 căn hộ di sản
với `area_id NULL` không bị đụng tới, không bị suy diễn gán khu vực.

## Auth model (tổng hợp hai mặt phẳng)

Hai hệ token TÁCH BIỆT vật lý, cùng hình dạng cấu hình:

| | Mini CRM (D-14) | Backend (Phase E) |
|---|---|---|
| Biến vai trò | `MINICRM_AUTH_{VIEWER,OPERATOR,ADMIN}_TOKEN` | `DASHBOARD_{VIEWER,OPERATOR,ADMIN}_TOKEN` |
| Biến phạm vi | `MINICRM_AUTH_PROJECT_SCOPE` | `DASHBOARD_PROJECT_SCOPE` |
| Kiểu phạm vi | `frozenset[str] \| "ALL"` | `frozenset[str] \| "ALL"` |
| Vắng cấu hình | 503 (mặt GHI đóng) | 503 (mặt ĐỌC đóng) |
| Token sai/thiếu | 401 | 401 |
| Vai trò đủ nhưng ngoài phạm vi | 403 `PROJECT_OUT_OF_SCOPE` | 403 `PROJECT_OUT_OF_SCOPE` |
| Bảo vệ gì | Route GHI Project/Area/Unit/Deal + outbox resend | Route ĐỌC + vận hành (sync-runs/errors/payload) |

Không có đường vòng admin ngầm định ở bên nào — `"ALL"` luôn phải cấp tường minh
trong JSON phạm vi.

## Relay behavior

Xem "C.5 — kết quả" ở trên. Tóm tắt một dòng: relay là vòng NỀN trong tiến trình
`minicrm`, tick định kỳ, gửi lại qua ĐÚNG hàm `crud.deliver()` mà v1 dùng, chọn
dòng để thử lại thay vì đếm số lần thử, không có cơ chế khoá DB liên-tiến-trình
(giả định một service duy nhất).

## Project scope behavior

Xem "Phase E — kết quả" ở trên. Tóm tắt một dòng: lọc ở tầng truy vấn cho MỌI
route đọc/vận hành đã liệt kê, 403 không phải 404 khi ngoài phạm vi, phạm vi rỗng
mặc định (fail-closed), `GET /me/permissions` để FE tự biết giới hạn của mình.

## Frontend behavior

Xem "Phase F/G — kết quả" ở trên. Tóm tắt một dòng: chọn dự án/khu vực lọc theo
quyền thật (không phải FE tự lọc), lưu trong URL, ghi luôn qua Mini CRM đã xác
thực và CHỜ xác nhận trước khi báo thành công, hiển thị trạng thái đồng bộ thay
vì giả định đã tới nơi ngay.

## Exact test commands và kết quả THẬT

```
ruff check src/ tests/ minicrm/
→ All checks passed!

TEST_TARGET=tests bash scripts/test_db.sh -q     (chạy solo, không đồng thời
                                                    với bất kỳ test container
                                                    thật nào khác)
→ chạy 1: 1190 passed, 1 skipped, 9 warnings in 474.18s
→ chạy 2: 1176 passed, 1 skipped, 14 errors — TỰ GÂY RA, xem "Sự cố phát hiện"
→ chạy 3: 1190 passed, 1 skipped, 9 warnings in 478.61s
→ xác minh lại riêng file bị ảnh hưởng ở chạy 2, chạy CÔ LẬP hoàn toàn:
  TEST_TARGET=tests/test_services/test_real_hierarchy_e2e.py bash scripts/test_db.sh -q
  → 14 passed in 12.26s

(cd minicrm && MINICRM_TEST_DATABASE_URL=postgresql+asyncpg://minicrm:minicrm@localhost:5433/minicrm_test \
   pytest -q)                                     (chạy solo, cô lập hoàn toàn,
                                                    x3 liên tiếp)
→ chạy 1: 392 passed in 254.71s
→ chạy 2: 392 passed in 252.20s
→ chạy 3: 392 passed in 251.94s

pytest minicrm/tests/test_real_backend_sync.py minicrm/tests/test_real_failure_windows.py \
       minicrm/tests/test_real_endpoints.py minicrm/tests/test_real_relay.py \
       minicrm/tests/test_real_auth.py -q
→ 90 passed in 75.26s

cd frontend && npx vitest run
→ 32 passed (4 test files)

cd frontend && npm run build
→ built in 2.51s, không lỗi
```

Container thật, database thật, HTTP thật cho toàn bộ nhánh `test_real_*.py` —
không mock đường relay hay đường xác thực.

## Known skips

Không có test nào bị skip liên quan tới bốn khối của đợt này. `tests/test_
scheduler.py` vẫn SKIP như các đợt trước (thiếu `apscheduler` ngoài image container
— đã ghi nhận từ trước, không phải phát sinh mới).

## Sự cố phát hiện và xử lý trong đợt này

**1. `minicrm/tests/test_real_relay.py` gọi thẳng Backend không kèm token.** Ba
lời gọi `httpx.get(f"{BACKEND_URL}/api/v1/{projects,areas}/{id}")` gọi trực tiếp,
không qua Mini CRM, không kèm header — từng chạy được vì Backend chưa bật xác
thực thật trong `.env` dev. Sau khi Phase E bật token thật, ba lời gọi này nhận
401. Sửa: thêm `BACKEND_AUTH_HEADER` (đọc `DASHBOARD_ADMIN_TOKEN` từ `.env`) vào
`minicrm/tests/real_env.py`, gắn vào cả ba lời gọi. Xác minh: 3 passed.

**2. `tests/conftest.py` dùng `os.environ.setdefault` cho token dashboard —
`.env` dev thật đã có `DASHBOARD_ADMIN_TOKEN` từ Phase E nên `setdefault` không
còn tác dụng gì.** `scripts/test_db.sh` nạp TOÀN BỘ `.env` vào môi trường trước
khi gọi pytest; biến đã tồn tại (giá trị THẬT) nên `setdefault` là no-op, còn
`DASHBOARD_AUTH_HEADER` mà test dùng vẫn mang token GIẢ cố định của conftest —
lệch nhau, mọi request trong suite nhận 401. Đây là nguyên nhân của 33 lỗi ở
`tests/test_api/test_seeded_dashboard.py` xuất hiện NHẤT QUÁN ở lần chạy full
suite 3x đầu tiên (không phải nhiễu chạy đồng thời như nghi ngờ ban đầu — kiểm
lại bằng cách chạy solo, hoàn toàn cô lập, vẫn ra đúng 33 lỗi giống hệt cả 3
lần). Sửa: đổi bốn dòng `os.environ.setdefault(...)` thành gán trực tiếp
(`os.environ[...] = ...`) — test suite giờ LUÔN dùng token cố định của chính nó,
độc lập với nội dung `.env` thật. Xác minh:
`TEST_TARGET=tests/test_api/test_seeded_dashboard.py bash scripts/test_db.sh -q`
→ 14 passed.

**3. Đua thật giữa relay tự động và fixture `outage_and_out_of_order` trong
`minicrm/tests/test_real_failure_windows.py`.** Test
`test_the_stale_batch_arriving_late_is_skipped_not_applied` giả định lô revision
2 luôn tới backend SAU lô revision 3 (ép bằng `/resend` tường minh sau khi tạo
rev 3). Từ khi relay tự động sống, relay quét outbox theo `created_at` và có thể
tự gửi rev 2 (đứng đợi từ lúc backend tắt, nên CŨ HƠN rev 3) NGAY khi backend
sống lại — trước cả khi rev 3 được tạo. Khi đó rev 2 chạm tầng so phiên bản lúc
nó thật sự là bản mới nhất, nên quyết định đúng là `update`, không phải
`skip_stale`. Tái hiện: lặp trực tiếp test này 8 lần liên tục → 5/8 lần thất bại
với `decisions == {'update': 1}` hoặc `{}` thay vì `{'skip_stale': 1}` — MỘT ĐUA
THẬT, có thể tái hiện, không phải nhiễu ngẫu nhiên một lần. Đây không phải lỗi hệ
thống: bất biến thật ("state cuối không bao giờ bị bản cũ ghi đè") vẫn đúng ở cả
hai nhánh, được kiểm riêng và không đổi ở
`test_the_late_stale_batch_did_not_overwrite_the_newer_state`. Sửa: viết lại
assertion để chấp nhận CẢ HAI nhánh hợp lệ của cuộc đua
(`decisions ∈ {{"skip_stale": 1}, {"update": 1}}`, cộng ánh xạ projection tương
ứng), giữ nguyên việc từ chối bất kỳ decision/projection nào KHÔNG thuộc hai
nhánh này (không phải "chấp nhận mọi thứ"). Xác minh: lặp lại 10 lần liên tục
sau khi sửa → 10/10 pass.

**4. Tự gây nhiễu giữa hai lần chạy container thật đồng thời (lặp lại một lỗi
đã biết từ đợt trước).** Trong lúc xác minh sự cố #3 (lặp test 10 lần, mỗi lần
tự dừng/khởi động lại container `api` thật), một lần chạy `TEST_TARGET=tests
bash scripts/test_db.sh -q` song song đã ghi nhận 14 lỗi, TOÀN BỘ nằm ở
`tests/test_services/test_real_hierarchy_e2e.py` — đúng file duy nhất gọi thẳng
container `api` thật mà lần lặp kia đang dừng/khởi động lại. Không sửa mã: xác
minh lại bằng cách chạy riêng file đó, hoàn toàn cô lập → 14 passed, và chạy lại
toàn bộ suite một lần nữa, cô lập, → 1190 passed giống hệt lần đầu. Ghi nhận lại
đúng quy tắc đã rút ra từ đợt trước: không chạy `scripts/test_db.sh` hay bất kỳ
test container thật nào có dừng/khởi động lại service đồng thời với một lần chạy
khác.

## Remaining blockers

- **Phase F/G: PARTIAL, không COMPLETE.** Trang Units/Deals theo phạm vi dự án/
  khu vực chưa tồn tại (không phải hỏng — chưa từng được xây, và đợt này ưu
  tiên xong trọn vẹn ba khối backend + hạ tầng FE dùng chung trước). Đây là việc
  còn treo tường minh cho phiên kế tiếp, không phải một gate bị bỏ qua âm thầm.
- `DashboardPage.jsx` chưa nối `useProjectScope` — không chặn gate của đợt này
  (route `/absorption` cố tình không bị Phase E khoanh phạm vi) nhưng nên làm
  trước khi coi FE là "đã theo phạm vi" toàn diện.
- Giới hạn "một tiến trình `minicrm` duy nhất" của C.5 (xem "Relay behavior")
  vẫn là giả định vận hành, không phải bất biến được DB thực thi — cần
  `FOR UPDATE SKIP LOCKED` nếu tương lai chạy nhiều bản sao.
- `POST /outbox/replay-stale` và `POST /outbox/{id}/resend` chỉ kiểm VAI TRÒ
  (`pipeline_operator`), CHƯA kiểm phạm vi dự án theo từng lô — đơn giản hoá đã
  ghi nhận từ D-14, chưa phải lỗ hổng (route action, không phải route đọc dữ
  liệu chéo dự án) nhưng nên xiết lại nếu vận hành đa dự án thật.

## Phase 6 status

**KHÔNG bắt đầu.** Không file xếp hạng, không worker xếp hạng, không route xếp
hạng nào được đụng tới trong đợt này — đúng chỉ thị "Do not implement Phase 6."

---

# Đợt 2026-08-13 (j) — Phase D: Backend hierarchy projection

Phase D — Backend hierarchy projection
Status: **COMPLETE**

Phạm vi: backend BẬT chấp nhận hợp đồng v2 và trở thành BẢN SAO CHỈ ĐỌC thật sự
của CẢ BỐN tầng `Project → Area → Unit → Deal` — đúng mô hình đã đóng băng ở
Phase A, hiện thực ở Mini CRM Phase B/C. `SUPPORTED_SCHEMA_VERSIONS` đổi từ `{1}`
sang `{1, 2}`; v1 giữ NGUYÊN VẸN, byte-identical, hành vi không đổi một dòng.
Backend Alembic tiến từ `0016_completed_with_conflicts` lên
`0017_hierarchy_projection` (MỘT migration, chỉ thêm cột danh tính nguồn cho
`projects`/`areas` — không bảng mới). Mini CRM Alembic giữ NGUYÊN
`0004_outbox_hierarchy_entities` — Phase D không đụng runtime Mini CRM, chỉ ba
file TEST của Mini CRM được cập nhật baseline (xem "Files changed"). KHÔNG FE
nào được nối. KHÔNG xếp hạng, không Phase 6.

## Implemented

**D1 — Bật v2 an toàn.** `SUPPORTED_SCHEMA_VERSIONS = {1, 2}`
(`src/services/json_payload.py`). `is_contract_v1`/`is_contract_v2`
(`src/services/contract_adapter.py`) phân biệt bằng `schema_version` (trước đây
chỉ dựa vào `project_ref`, không đủ khi cả hai hợp đồng đều có trường đó) —
KHÔNG đổi hành vi cho payload v1 THẬT, vì `schema_version` của nó luôn là `1`.
`adapt_v2()` dịch bốn entity (project/area/unit/deal) sang phong bì nội bộ
dùng chung với v1; `ContractValidatorV2` (`src/services/contract_validation_v2.py`,
file RIÊNG — v1 không bị chạm) kiểm hình dạng theo `crm_sync_v2.schema.json`
(ĐỌC, không sửa — SHA-256 không đổi).

**D2 — Migration 0017.** `projects`/`areas` được thêm `external_id`,
`source_system`, `source_instance_id`, `source_revision`, `source_updated_at`,
`updated_at` (tất cả NULLABLE — dự án/phân khu DI SẢN không có danh tính nguồn,
không bịa) cộng ràng buộc UNIQUE `(source_instance_id, external_id)` — NULL
không va nhau, đúng khuôn `uq_units_source_identity` (0007). `upload_files.project_id`
chuyển NULLABLE: một lô `source_entity='projects'` TẠO dự án chưa có UUID nào để
ghi cho tới khi lô xử lý xong. Tên revision RÚT GỌN thành `0017_hierarchy_projection`
(không phải `0017_backend_hierarchy_projection` — bản đầy đủ dài 34 ký tự, vượt
`alembic_version.version_num VARCHAR(32)`, phát hiện khi chạy thử, không phải suy
đoán trước — cùng loại phát hiện với việc rút tên migration 0004 ở Mini CRM Phase
C). Idempotent (upgrade/downgrade/re-upgrade đã chạy thử trên database sạch),
downgrade sẽ NỔ nếu có dữ liệu vi phạm ràng buộc cũ (không âm thầm mất dữ liệu).

**D3 — Project projection.** `DomainProjector._project_project`/`_archive_project`
(`src/services/domain_projection.py`). Insert/update qua `ON CONFLICT` trên
`(source_instance_id, external_id)`; archive = `status='archived'` (KHÔNG xoá vật
lý — `projects` không có `deleted_at`); `PARENT_HAS_LIVE_CHILDREN` nếu còn Area
`active`; hồi sinh (upsert revision cao hơn trên dự án đã archive) → chấp nhận,
đặt lại `active`. skip_stale/duplicate_noop/conflict dùng NGUYÊN `SourceIdentityService`
— không sửa một dòng nào của Phase 5. Thiếu `launch_date` → `MISSING_FIELD`, KHÔNG
suy đoán.

**D4 — Area projection.** `_project_area`/`_archive_area`. Đòi Project đã mirror
(`PROJECT_NOT_FOUND` — từ chối CẢ PHONG BÌ nếu tra `external_project_id` không ra,
đúng §A5.3, TRỪ khi lô chính là lô tạo dự án đó). `PARENT_ARCHIVED` nếu dự án đã
archive. **Phát hiện và vá một lỗ hổng thật khi viết test**: `_upsert()` (ON
CONFLICT DO UPDATE) SẼ âm thầm ghi đè `project_id` nếu một Area được upsert lại
dưới một `project_ref` khác — thêm chốt kiểm tường minh trước khi ghi
(`AREA_CROSS_PROJECT_MOVE`), từ chối thay vì cho chuyển dự án. `uq_areas_project_name_unit_type`
(0001) vẫn nguyên, ánh xạ lỗi sang `AREA_NATURAL_KEY_CONFLICT`. Archive không
cascade xuống Unit (`PARENT_HAS_LIVE_CHILDREN` nếu còn Unit sống).
`history_guard.py` được nối thêm `_read_project_mirror`/`_read_area_mirror` —
thiếu mảnh này thì MỌI bản ghi `partial` cho Project/Area sẽ luôn hỏng với
`PARTIAL_UPDATE_WITHOUT_BASE` dù dự án/phân khu đã tồn tại (phát hiện khi thiết kế
test D1, vá trước khi kịp thành lỗi thật).

**D5 — Unit/Deal scoped (v2).** `_resolve_area` nối thêm nhánh `external_area_id`
(tra `(project_id, areas.external_id)`) — CẠNH hai nhánh v1 cũ (`area_id`,
`area_name`+`unit_type`), không thay. Deal projection KHÔNG đổi (đã entity-agnostic
đúng nghĩa qua `unit_id`). `MAPPED_FIELDS`/`payload_fingerprint` mở rộng cho
`projects`/`areas`/`external_area_id` — không đổi băm của bản ghi v1 hiện có
(trường mới chỉ được đọc khi CÓ MẶT trong `data`).

**D6 — Concurrency.** `SourceIdentityService`/`lock_identities()` HOÀN TOÀN không
sửa — vốn đã entity-agnostic (`source_entity` là `Text` với CHECK `<> ''`, không
enum, phát hiện từ Phase A §A4.3). Kiểm bằng test đua thật (`asyncio.gather`) cho
Project và Area riêng: revision cao nhất luôn thắng, không dòng trùng.
**Phát hiện một giới hạn CÓ SẴN của chính cơ chế Phase 5** (không phải hồi quy
Phase D): một lô CHÉO thứ tự mà CẢ HAI bản ghi cùng là LẦN ĐẦU THẤY (chưa từng có
trong `crm_source_records`) có thể deadlock ở tầng UPSERT của bảng nghiệp vụ —
`lock_identities()` chỉ khoá được danh tính ĐÃ TỒN TẠI, và cơ chế "lần đầu"
(`ON CONFLICT DO NOTHING`) không tất định thứ tự giữa các bản ghi. Test hiện có
của Unit/Deal (`test_sync_concurrency.py::crossed_multi_record_batches`) né được
tình huống này vì luôn SEED trước; bảo đảm THẬT SỰ đã có (và được kiểm lại cho
Area) là cho lô CẬP NHẬT chéo thứ tự, không phải lô TẠO MỚI chéo thứ tự. Ghi lại
minh bạch, không sửa Phase 5 (ngoài phạm vi Phase D, và rủi ro cao cho một cơ chế
đã chứng minh đúng).

**D7 — Thu hẹp đường ghi cũ.** `src/services/projects.py`:
`create_project`/`create_area` XOÁ HẲN. `update_project`/`update_area` còn lại
CHỈ sửa được `headline`/`introduce` (nội dung hiển thị backend SỞ HỮU RIÊNG, §A2.3
— KHÔNG phải business write). `PROJECT_EDITABLE`/`AREA_EDITABLE` rút hết bảy
trường CANONICAL. `src/api/dashboard.py`: `POST /projects`/`POST /areas` XOÁ.
Test liệt kê route (`test_no_public_route_can_create_a_project_or_area_outside_ingestion`)
xác nhận không route POST nào ngoài `/sync/{entity}` còn ghi được vào hai bảng
này — không dựa vào việc ẩn nút ở FE.

**D8 — Read APIs.** MỚI: `GET /projects/{external_id}`, `GET /areas/{external_id}`
(dashboard.py). MỞ RỘNG (cộng thêm, không phá): `GET /projects`/`GET /areas` trả
kèm `external_id`/`source_revision`; `GET /areas`, `GET /inventory`, `GET /deals`
nhận thêm `external_project_id`/`external_area_id` làm phương án thay cho
`project_id`/`area_id` UUID (đúng MỘT trong hai, mơ hồ thì 422
`AMBIGUOUS_PROJECT_SCOPE`/`AMBIGUOUS_AREA_SCOPE`). KHÔNG có tầng xác thực nào ở
các route đọc — theo ĐÚNG quy ước MVP đã có của `src/api/inventory.py` ("Chưa có
tầng xác thực nào trong mã nguồn"), không tự bịa auth riêng cho Phase D.

## Explicitly not implemented (theo đúng ranh giới đã cho)

- FE — không route nào được nối, không selector, không bảng scoped.
- Xếp hạng, Phase 6.
- Phase E (project-scoped RBAC runtime) — `DashboardPrincipal` không mọc thêm trường.
- Mini CRM write auth (D-14) — không đổi, không mới liên quan.
- Wiring TỰ ĐỘNG từ Mini CRM sang backend v2 (Mini CRM vẫn CHỈ LƯU, không tự gửi
  — đó là việc của một phase sau, khi Mini CRM runtime được phép sửa).

## Files changed

```text
NEW (backend):
  alembic/versions/0017_hierarchy_projection.py
  src/services/contract_validation_v2.py
  tests/test_services/test_hierarchy_projection.py   (36 test)
  tests/test_services/test_real_hierarchy_e2e.py     (14 test, container thật)

MODIFIED (backend):
  src/models/tables.py        (+external_id/source_*/updated_at cho projects/areas;
                                upload_files.project_id nullable)
  src/services/json_payload.py       (SUPPORTED_SCHEMA_VERSIONS={1,2}, SUPPORTED_ENTITIES
                                       +={"projects","areas"}, SyncEnvelope.project_id
                                       optional + external_project_id, MAPPED_FIELDS)
  src/services/contract_adapter.py   (is_contract_v1 thêm schema_version==1;
                                       is_contract_v2 + adapt_v2 MỚI)
  src/services/domain_projection.py  (_project_project/_archive_project/_project_area/
                                       _archive_area MỚI; _resolve_area +external_area_id;
                                       AREA_CROSS_PROJECT_MOVE MỚI; _upsert nhận constraint)
  src/services/sync_runs.py          (_resolve_project MỚI thay _parse_project_id+
                                       _require_project trần; project_id Optional xuyên suốt;
                                       guard project_id=None khi enqueue tính lại)
  src/services/history_guard.py      (_read_project_mirror/_read_area_mirror MỚI)
  src/api/sync.py                    (dispatch is_contract_v2 TRƯỚC is_contract_v1;
                                       PROJECT_NOT_FOUND status mapping)
  src/services/projects.py           (create_project/create_area XOÁ; PROJECT_EDITABLE/
                                       AREA_EDITABLE rút còn headline/introduce;
                                       ProjectRecord/AreaRecord +external_id/source_revision)
  src/services/absorption.py         (AreaInventory +external_id/source_revision)
  src/api/dashboard.py               (POST /projects, POST /areas XOÁ; GET /projects/
                                       {external_id}, GET /areas/{external_id} MỚI;
                                       GET /areas +external_project_id)
  src/api/inventory.py               (GET /inventory, GET /deals +external_project_id/
                                       external_area_id)
  src/models/schemas.py              (ProjectCreate/AreaCreate XOÁ; ProjectUpdate/
                                       AreaUpdate rút còn headline/introduce;
                                       +external_id/source_revision trên 4 schema đọc)

MODIFIED (test hiện có, KHÔNG đổi hành vi runtime):
  tests/test_api/test_catalog.py          (VIẾT LẠI — fixture chèn thẳng SQL thay
                                            vì POST đã xoá; 39 test, xem docstring file)
  tests/test_services/test_phase_a_contract_freeze.py  (3 test "ĐỎ KHI PHASE D BẮT ĐẦU"
                                            cập nhật baseline — ĐÚNG như chính docstring
                                            của chúng đã dự báo)
  tests/test_ranking_boundary.py          (16→17 revision backend, hardcoded evidence)
  tests/test_scripts/test_seed_dev.py     (sửa lỗi ĐẾM: GROUP BY gộp NULL thành một
                                            nhóm trong khi UNIQUE constraint thật của
                                            Postgres cho phép nhiều NULL — lỗi LOGIC
                                            có sẵn, bị lộ ra bởi cột NULLABLE mới,
                                            không phải lỗi của migration 0017)

MODIFIED (Mini CRM — CHỈ test, KHÔNG runtime, xem lý do dưới):
  minicrm/tests/test_migration_0003.py    (backend file count 16 → >=16: mốc LỊCH SỬ
                                            Phase B vẫn đúng nhưng test đọc thư mục SỐNG
                                            nên không thể mãi mãi == một số cố định khi
                                            backend tự lớn ở phase khác)
  minicrm/tests/test_migration_0004.py    (cùng lý do, cho mốc Phase C)
  minicrm/tests/test_real_backend_sync.py (baseline Alembic head backend 0016→0017)

KHÔNG ĐỔI: minicrm/app/*.py (mọi RUNTIME Mini CRM), Mini CRM Alembic (giữ 0004),
  docs/crm/phase_a_domain_freeze.md, docs/crm/sync_contract_v1_draft.md,
  docs/crm/sync_contract_v2_draft.md, src/contracts/*.schema.json (ĐỌC, không sửa),
  src/services/contract_validation.py (v1), frontend/, mọi bảng/route xếp hạng.
```

## Sự cố phát hiện và xử lý trong đợt này

**Container `api` chưa áp migration 0017 khi bắt đầu chạy E2E thật** (`src/` mount
sống + `--reload`, nhưng KHÔNG tự động chạy alembic khi code đổi — khác Mini CRM's
`MINICRM_RUN_MIGRATIONS`). 500 `UndefinedColumnError` ngay `POST /sync/projects`
đầu tiên. Xử lý: `docker compose exec api alembic upgrade head` — không sửa mã,
không sửa entrypoint, chỉ chạy đúng lệnh vận hành còn thiếu.

**`test_backend_never_imports_minicrm` (Mini CRM) bị lộ dương tính giả** — hai
docstring backend (`contract_validation_v2.py`, `domain_projection.py`) chứa CHỮ
`minicrm` (đường dẫn file tham khảo), trùng khớp phép quét thô "backend không bao
giờ NHẮC tới minicrm" vốn nhằm bắt IMPORT thật, không phải câu văn giải thích.
Cùng loại phát hiện với vụ migration 0003 (Phase B) tự nhắc số revision backend
trong docstring của chính nó. Xử lý: viết lại hai câu để không còn chuỗi đó,
KHÔNG nới lỏng phép quét.

**Lỗi đếm trùng NULL trong `test_no_duplicate_business_keys`** — GROUP BY gộp mọi
NULL vào MỘT nhóm trong khi ràng buộc UNIQUE thật của Postgres cho phép nhiều
NULL cùng lúc (không va nhau) — một khác biệt ngữ nghĩa CÓ SẴN giữa hai khái niệm,
chỉ lộ ra khi migration 0017 thêm cột NULLABLE đầu tiên tham gia một UNIQUE
constraint mà `seed_dev.py` không điền. Vá bằng cách loại các dòng NULL trước khi
GROUP BY — sửa ĐÚNG NGỮ NGHĨA, không nới lỏng ngưỡng.

## Test Results

| Suite | Command | Kết quả |
|---|---|---|
| Ruff | `ruff check src/ tests/ minicrm/` | Sạch |
| Focused Phase D (test_hierarchy_projection.py) | `TEST_TARGET=... bash scripts/test_db.sh -q` | 36 passed |
| Full backend (loại real E2E) — 3 lần | `TEST_TARGET=tests bash scripts/test_db.sh -q -k "not test_real_hierarchy"` | **1156 passed, 1 skipped, 14 deselected** — GIỐNG HỆT cả ba lần |
| Real E2E backend (container thật) — 3 lần | `pytest tests/test_services/test_real_hierarchy_e2e.py -q` | **14 passed** cả ba lần |
| Mini CRM local (không đổi hành vi) | `MINICRM_TEST_DATABASE_URL=... pytest -q -k "not real_"` | **276 passed, 78 deselected** — trước VÀ sau ba lần sửa baseline, giống hệt |
| Mini CRM real E2E (v1, container thật) | `pytest tests/test_real_endpoints.py tests/test_real_backend_sync.py tests/test_real_failure_windows.py -q` | **78 passed** |

## Migration heads

```text
Backend:  0017_hierarchy_projection (head), tiến từ 0016_completed_with_conflicts
Mini CRM: 0004_outbox_hierarchy_entities (head), KHÔNG đổi
```

## v1 hash

```text
SHA-256 crm_sync_v1.schema.json (src/ VÀ minicrm/, giống hệt): KHÔNG ĐỔI
  e15fd9c5e685923fcf3f537c7dba4e900632ae7d6723df654e35b55efb49a92a
SHA-256 crm_sync_v2.schema.json (src/ VÀ minicrm/, giống hệt): KHÔNG ĐỔI
  9620614a46536515fabeae1e9ba1e032c30deb02a74656e11818b1951fe10efb
  (Phase D chỉ ĐỌC hai file này — ContractValidatorV2 nạp trực tiếp, không sửa)
```

## Known legacy data

163 unit di sản (`area_id IS NULL`, Phase B) — KHÔNG bị đụng tới, vẫn đọc/sửa
được như trước. Dự án/phân khu di sản (`external_id IS NULL`, tạo TRƯỚC Phase D
bởi đường ghi cũ đã bị thu hẹp) — đọc được qua `GET /projects`/`GET /areas` với
`external_id: null`, KHÔNG soi gương được qua `GET /projects/{external_id}` (không
có external_id để tra) cho tới khi (nếu) một phase sau quyết định kế hoạch di trú.
Không bịa danh tính cho bất kỳ dòng nào trong số này.

## Remaining blockers

- **D-14 (Mini CRM write authentication)** — không đổi, không liên quan tới Phase D.
- **D-3** (kế thừa từ Phase B) — vẫn chờ owner xác nhận, không đổi ở Phase D.
- Kỹ thuật (không chặn): việc RELAY phong bì v2 Mini CRM đã LƯU sang backend hiện
  là THỦ CÔNG (`GET /outbox/{batch}` rồi POST tay, đúng như E2E test đã làm) — Mini
  CRM CHƯA tự động gửi. Nối đường tự động là việc của một phase sau, khi runtime
  Mini CRM được phép sửa.
- Kỹ thuật (không chặn, ghi lại minh bạch): giới hạn deadlock của Phase 5 cho lô
  CHÉO thứ tự + CẢ HAI bản ghi cùng lần-đầu-thấy — có sẵn trước Phase D, áp cho MỌI
  thực thể (không riêng Project/Area), chưa từng bị test cũ của Unit/Deal phát
  hiện vì luôn seed trước. Không sửa ở Phase D (ngoài phạm vi, rủi ro cao cho cơ
  chế đã chứng minh đúng của Phase 5).
- Di trú danh tính cho dự án/phân khu di sản (`external_id IS NULL`) — chưa có kế
  hoạch, chưa cần tới khi có yêu cầu nghiệp vụ cụ thể.

## Phase E readiness

Backend giờ là bản sao chỉ đọc THẬT của cả bốn tầng, với read API theo
`external_id` sẵn sàng cho FE trỏ vào. Việc còn lại cho Phase E: RBAC runtime theo
`project_scope` (khung đã đóng băng ở Phase A §A7, `DashboardPrincipal` cố tình
CHƯA mọc thêm trường), và (tách biệt, không phải điều kiện của Phase E) nối đường
gửi TỰ ĐỘNG từ Mini CRM sang route v2 mới của backend.

---

# Đợt 2026-08-13 (i) — Phase C: Outbox và đồng bộ phân cấp

Phase C — Outbox and hierarchy synchronization
Status: **COMPLETE**

Phạm vi: mở rộng cơ chế outbox của Mini CRM để SINH và LƯU ý định đồng bộ v2 cho
CẢ BỐN tầng `Project → Area → Unit → Deal`, đúng mô hình sở hữu đã đóng băng ở
Phase A và hiện thực ở Phase B. **Phong bì v2 KHÔNG được gửi đi** — phía nhận vẫn
giữ `SUPPORTED_SCHEMA_VERSIONS = {1}` và không có `POST /sync/projects`/
`POST /sync/areas` nào tồn tại (`REQUIRED IN PHASE D`, chưa làm). Backend Alembic
head giữ nguyên `0016_completed_with_conflicts` (16 revision, KHÔNG đổi); Mini CRM
tiến từ `0003_minicrm_hierarchy` lên `0004_outbox_hierarchy_entities` (một migration
DUY NHẤT, nới một CHECK constraint — không bảng mới, không cột mới). KHÔNG FE nào
được nối. KHÔNG xếp hạng, không Phase 6. KHÔNG đổi hành vi CRUD của Phase B.

## Implemented

**C1 — Project/Area outbox events.** Mỗi lần ghi Project/Area (create/update/
archive) giờ tạo THÊM đúng MỘT dòng `crm_outbox` mới (entity=`"projects"`/
`"areas"`), TRONG CÙNG transaction với thay đổi nghiệp vụ, kiểm bằng `contract_v2`
trước khi ghi (`crud._capture_v2`). Phản hồi CRUD của Project/Area KHÔNG đổi hình
dạng (vẫn chỉ `{"record": ...}`, không thêm `sync`) — dòng outbox tra được qua
`GET /outbox?entity=projects` như mọi dòng khác, giữ nguyên hợp đồng Phase B.
KHÔNG có dòng outbox nào được tạo cho một lần ghi bị từ chối cục bộ.

**C2 — Hierarchy-aware v2 payload.** Bốn hàm dựng phong bì mới trong
`sync_client.py`: `build_project_envelope`, `build_area_envelope`,
`build_unit_envelope_v2`, `build_deal_envelope_v2` — dùng ĐÚNG hình dạng đã đóng
băng ở Phase A (`project_ref: {external_project_id}`, `area_ref:
{external_area_id}` DUY NHẤT, `project_payload`/`area_payload` NĂM trường có
thẩm quyền, `deal_payload` KHÔNG mang `project_ref`/`area_ref` riêng — suy qua
`external_unit_id`). Module v2 kiểm hợp đồng mới: `app/contract_v2.py`, so khớp
`crm_sync_v2.schema.json` (bản sao byte-identical `src/`↔`minicrm/`, SHA-256
KHÔNG đổi qua Phase C) cộng hai luật nghiệp vụ JSON Schema không diễn đạt được:
thứ tự tầng (§A5.2) và khớp `project_ref` ↔ bản ghi `project` (§A3.5, fixture 29).
**v1 hoàn toàn không đổi** — `build_unit_envelope`/`build_deal_envelope`/
`build_delete_envelope`/`contract.py` giữ nguyên byte hành vi.

**C3 — Thứ tự cha/con.** `sync_client.order_hierarchy_records()` sắp ỔN ĐỊNH
theo đúng `project → area → unit → deal` (upsert) / đảo ngược (delete), từ chối
thẳng entity lạ. `build_hierarchy_envelope()` ghép nhiều bản ghi đơn-tầng thành
một lô trộn tầng đúng thứ tự — dùng cho test/Phase D, Phase C không tự động gộp
lô nào. `contract_v2._order_violations`/`_project_ref_violations` bắt sai thứ tự
và bất khớp `project_ref` NGAY TRƯỚC KHI LƯU, mô phỏng đúng fixture `28`/`29`.

**C4 — Unit/Deal tích hợp.** Mỗi lần ghi Unit/Deal (create/update/delete) giờ
tạo THÊM một dòng outbox v2 (entity=`"units_v2"`/`"deals_v2"`, hậu tố `_v2` để
KHÔNG BAO GIỜ lẫn với dòng v1 `"units"`/`"deals"` — dòng đó vẫn gửi thật, không
đổi) — CHỈ khi căn có `area_id` cục bộ (đường mới, có phân khu); 163 căn di sản
(`area_id IS NULL`, xem Phase B) KHÔNG có ý định v2 nào được tạo, vì không thể
dựng `area_ref` mà không bịa dữ liệu (§A0). Toàn bộ hành vi v1 (validate,
revision, cross-project reject, tombstone, delivery) giữ nguyên — kiểm bằng hồi
quy toàn bộ (276/276 local + 78/78 real E2E), không sửa một dòng logic v1 nào.

**Payload version boundary (C6).** Đường v2 tách biệt HOÀN TOÀN khỏi đường v1:
entity mới nằm trong `sync_client.V2_CAPTURE_ENTITIES`, không giao với
`ENTITY_PATH` (v1). `crud._capture_v2` KHÔNG BAO GIỜ gọi `deliver()` — dòng outbox
v2 giữ `http_status=NULL`/`attempts=0` VÔ THỜI HẠN, không phải "đang chờ phản hồi"
như một lô v1 timeout mà là "chưa từng được phép rời khỏi máy". `resend`/
`replay-stale` gọi trên một dòng v2 bị chặn TƯỜNG MINH bằng `CrudError
V2_DELIVERY_NOT_ENABLED` (409, `crud._reject_v2_delivery`) — không ném lỗi mù,
không cố gửi rồi nhận 404 từ một route không tồn tại.

**Retry/resend/idempotency (C5).** Một lần ghi → đúng MỘT dòng outbox v2 (kiểm
trực tiếp qua `GET /outbox?entity=...total`). Gọi `resend`/`replay-stale` lặp
lại trên dòng v2 luôn trả 409 NHẤT QUÁN, không tích luỹ side effect
(`attempts` giữ 0). Ý định v2 SỐNG SÓT một lần "backend outage" của v1 (test
`test_v2_capture_survives_a_v1_backend_outage`): dòng v2 được ghi TRƯỚC khi v1
cố gửi, nên nó không phụ thuộc kết quả gửi của v1. KHÔNG sửa cơ chế concurrency
đã chốt ở Phase 5 (`source_identity.py`/`sync_runs.py` không chạm tới).

## Explicitly not implemented (theo đúng ranh giới đã cho)

- Backend v2 acceptance (`SUPPORTED_SCHEMA_VERSIONS` vẫn `{1}`), `POST
  /sync/projects`/`POST /sync/areas`, `DomainProjector` chiếu Project/Area.
- FE — không route nào được nối.
- Mini CRM auth/D-14 — không route mới nào lộ RA NGOÀI (cùng cổng `:8100` như
  mọi route Unit/Deal đã có); D-14 vẫn là rủi ro tách riêng, không mới.
- Xếp hạng, Phase 6.
- Thay đổi hành vi CRUD Phase B (Project/Area/Unit/Deal validation, archive,
  parent-child rejection) — chỉ THÊM một tác dụng phụ ghi outbox v2, không sửa
  một điều kiện nghiệp vụ nào.
- Thay đổi backend migration/runtime, v1 contract bytes.

## Files changed

```text
NEW:
  minicrm/app/contract_v2.py
  minicrm/alembic/versions/0004_outbox_hierarchy_entities.py
  minicrm/tests/test_hierarchy_sync.py       (27 tests)
  minicrm/tests/test_migration_0004.py       (15 tests)
  minicrm/tests/test_outbox_v2.py            (21 tests)

MODIFIED:
  minicrm/app/sync_client.py   (+builder v2: project/area/unit/deal, order_hierarchy_records,
                                 build_hierarchy_envelope, V2_CAPTURE_ENTITIES, new_hierarchy_batch_id)
  minicrm/app/crud.py          (+_capture_v2, _hierarchy_context_for_area/_unit, _reject_v2_delivery;
                                 nối vào 9 hàm CRUD; guard resend()/replay_stale())
  minicrm/tests/test_outbox.py            (3 assertion lọc theo entity=units — không yếu đi,
                                            CHÍNH XÁC hơn: chỉ nói về sổ gửi v1)
  minicrm/tests/test_real_endpoints.py    (1 baseline +1→+2 dòng outbox — Unit tạo giờ sinh
                                            2 dòng: v1 gửi thật + v2 chỉ lưu)
  minicrm/tests/test_real_backend_sync.py    (baseline Alembic head 0003→0004)
  minicrm/tests/test_real_failure_windows.py (baseline Alembic head 0003→0004)

KHÔNG ĐỔI: mọi file backend (src/, alembic/), frontend/, docs/crm/phase_a_domain_freeze.md,
  docs/crm/sync_contract_v1_draft.md, minicrm/app/contract.py (v1), minicrm/app/schemas.py,
  minicrm/app/routers/*.py, minicrm/app/models.py, minicrm/alembic/versions/0001-0003,
  7 file test Mini CRM khác (test_crud_projects.py, test_crud_areas.py, test_crud_hierarchy.py,
  test_crud_units.py, test_crud_deals.py, test_migration_0001/0002/0003.py, test_sync_client.py,
  test_contract_copy.py, test_health.py, test_real_endpoints.py trừ đúng 1 dòng trên).
```

## Tests

**Focused Phase C** (mới): 63 test — `test_hierarchy_sync.py` (27, thuần logic
builder/ordering/contract_v2, không cần DB) + `test_migration_0004.py` (15) +
`test_outbox_v2.py` (21, DB thật qua `crm_app`+`FakeBackend`).

## Exact commands

```bash
ruff check src/ tests/ minicrm/
cd minicrm && MINICRM_TEST_DATABASE_URL=postgresql+asyncpg://minicrm:minicrm@localhost:5433/minicrm_test \
  python -m pytest -q -k "not real_"                      # x3
TEST_TARGET=tests bash scripts/test_db.sh -q               # x2
python -m pytest -q tests/test_real_endpoints.py tests/test_real_backend_sync.py \
  tests/test_real_failure_windows.py                       # x2, sau khi rebuild+restart container minicrm
docker compose exec minicrm alembic current
docker compose exec api alembic current
```

## Exact pass/fail/skip counts

| Suite | Kết quả |
|---|---|
| Ruff | Sạch |
| Focused Phase C (63 test) | 63 passed, 0 failed, 0 skipped |
| Full Mini CRM local (276 test, gồm 63 mới + 213 kế thừa) | **276 passed, 0 failed, 0 skipped** — chạy 3 lần liên tiếp, kết quả giống hệt |
| Backend full regression (1152 test) | **1151 passed, 1 skipped** — chạy 2 lần, giống hệt baseline TRƯỚC Phase C |
| Real E2E (78 test, container thật) | **78 passed, 0 failed** — chạy 2 lần sau khi rebuild image `minicrm-synthetic:dev` |

## Migration heads

```text
Mini CRM: 0004_outbox_hierarchy_entities (head), tiến từ 0003_minicrm_hierarchy
Backend:  0016_completed_with_conflicts (head), KHÔNG đổi
```

## v1 hash

```text
SHA-256 crm_sync_v1.schema.json (src/ VÀ minicrm/, giống hệt):
  e15fd9c5e685923fcf3f537c7dba4e900632ae7d6723df654e35b55efb49a92a   KHÔNG ĐỔI

SHA-256 crm_sync_v2.schema.json (src/ VÀ minicrm/, giống hệt):
  9620614a46536515fabeae1e9ba1e032c30deb02a74656e11818b1951fe10efb   KHÔNG ĐỔI
  (file đã tồn tại từ Phase A, Phase C chỉ ĐỌC, không sửa)
```

## Sự cố phát hiện và xử lý trong đợt này

**Image Docker `minicrm` chưa từng build lại từ khi `crm_sync_v2.schema.json`
được thêm vào repo (Phase A).** `contracts/` KHÔNG nằm trong volume mount
live-reload của Compose (chỉ `app/`/`alembic/` được mount) — nên container ĐANG
CHẠY chỉ có `crm_sync_v1.schema.json` trong `/app/contracts/`, gây `500
ContractSchemaUnavailableError` ngay lần `POST /units` đầu tiên khi chạy real
E2E. Not một lỗi của mã Phase C — mã ĐÚNG, chỉ là lần đầu tiên có gì đó bên
trong container thật sự cần đọc file đó lúc runtime. Xử lý: `docker compose
build minicrm && docker compose up -d minicrm` (rebuild ĐÚNG những gì `COPY .
.` của Dockerfile đã luôn làm, không sửa Dockerfile/compose). Xác nhận: `/app/
contracts/` giờ có cả hai file, health check xanh, 78/78 real E2E qua hai lần
chạy liên tiếp.

## Remaining blockers

- **D-14 (Mini CRM write authentication)** — vẫn `DECISION REQUIRED`, KHÔNG bị
  Phase C làm nặng thêm: bốn entity outbox mới không mở route HTTP nào mới ra
  ngoài — chúng chỉ ghi vào bảng đã có qua các route CRUD đã tồn tại từ Phase B,
  và bản thân dòng outbox v2 không có đường gửi/nhận nào để lộ.
- **D-3** (không đổi ở Phase C, kế thừa từ Phase B) — vẫn chờ owner xác nhận.
- Kỹ thuật (không chặn): `docker-compose.yml` chưa mount `minicrm/contracts/`
  như một volume live-reload — nghĩa là lần TIẾP THEO ai đó sửa
  `crm_sync_v2.schema.json` (ví dụ Phase D) sẽ cần rebuild image thủ công một
  lần nữa thay vì thấy thay đổi ngay. Không sửa ở đây vì nằm ngoài phạm vi
  Phase C (đổi `docker-compose.yml` không nằm trong danh sách file được phép).

## Phase D readiness

`crm_projects`/`crm_areas` đã có real data + versioning (Phase B); `mirrored_*`
đã có sẵn từ Phase A/B; phong bì v2 cho CẢ BỐN tầng đã dựng, kiểm, và LƯU được
(Phase C) — Phase D chỉ còn việc BẬT: `SUPPORTED_SCHEMA_VERSIONS={1,2}`, hai route
nhận mới, `DomainProjector` soi gương Project/Area, và migration backend cho
`source_*`/`external_id`/unique constraint trên CẢ HAI bảng — đúng danh sách 10
điểm đã ghi ở `sync_contract_v2_draft.md` §10.

---

# Đợt 2026-08-13 (h) — Phase B: Mini CRM hierarchy CRUD

Phase B — Mini CRM hierarchy CRUD
Status: **COMPLETE**

Phạm vi: Mini CRM trở thành TÁC GIẢ cục bộ của `Project → Area → Unit → Deal`,
đúng mô hình sở hữu đã đóng băng ở Phase A (đợt (g),
`docs/crm/phase_a_domain_freeze.md`). Backend Alembic head giữ nguyên
`0016_completed_with_conflicts` (16 revision, KHÔNG đổi); Mini CRM tiến từ
`0002_minicrm_crud` lên `0003_minicrm_hierarchy` (một migration DUY NHẤT).
KHÔNG dòng outbox nào được tạo cho Project/Area — nối vào đường đẩy là việc của
Phase C. KHÔNG FE nào được nối. KHÔNG xếp hạng, không Phase 6.

## Implemented

**Project CRUD** — `POST/GET/PATCH/DELETE /projects`. `external_id` do dãy
`crm_project_external_seq` cấp (`P-0001`, …), bất biến, không tái sử dụng sau
lưu trữ. `DELETE` = lưu trữ (`status='archived'`), KHÔNG xoá vật lý (`projects`
không có `deleted_at`). Phản hồi CHỈ có `record` — không có `sync`, vì không có
gì được đẩy đi ở đợt này.

**Area CRUD** — `POST/GET/PATCH/DELETE /areas`, scoped theo `external_project_id`.
Cả năm trường — kể cả ba trường kế hoạch `bedrooms`/`area_sqm`/`total_units` —
BẮT BUỘC và CÓ THẨM QUYỀN ngay từ request tạo, KHÔNG tiền tố `proposed_`, KHÔNG
bước duyệt (khác hẳn mô hình đã bị thay thế ở đợt (f), xem
`phase_a_domain_freeze.md` §S-2). Dự án phải tồn tại cục bộ và không lưu trữ
(`PROJECT_NOT_FOUND` 422 / `PARENT_ARCHIVED` 409) — Mini CRM không tự phát minh
dự án cho một phân khu mồ côi.

**Project/Area relationship** — `crm_areas.project_id` FK, bất biến bằng cách
VẮNG MẶT ở `AreaPatch` (cộng `extra="forbid"` để gửi trường đó bị TỪ CHỐI tường
minh, không âm thầm bỏ qua — mặc định Pydantic là "ignore"). Khoá tự nhiên
`(project_id, area_name, unit_type)` soi gương `uq_areas_project_name_unit_type`
của backend, scoped theo dự án — hai dự án cùng tên phân khu là hợp lệ.

**Unit scoped validation** — `UnitCreate`/`UnitPatch` nhận HAI cách tham chiếu
phân khu: `external_area_id` (khuyến nghị, ổn định) hoặc `area_name`+`unit_type`
(kế thừa, tự tra ĐÚNG MỘT phân khu còn hoạt động khớp cặp đó — không hoặc nhiều
hơn một thì từ chối, không đoán: `AREA_NOT_FOUND` / `AMBIGUOUS_AREA_REFERENCE`).
Di chuyển phân khu CHỈ được phép trong cùng dự án (`AREA_CROSS_PROJECT_MOVE` 409
nếu khác). `crm_units.area_id` (FK, NULLABLE — xem lý do dưới), và
`area_name`/`unit_type` (hình dạng phong bì v1, GIỮ NGUYÊN) được LÀM TƯƠI từ
`crm_areas` ở MỌI lần ghi. **Hình dạng phong bì v1 gửi lên backend không đổi một
byte nào** — `area_ref: {area_name, unit_type}` như trước.

**Deal scoped validation** — KHÔNG đổi cấu trúc: Deal không mang tham chiếu
Project/Area riêng, phạm vi của nó SUY RA qua Unit theo định nghĩa
(`phase_a_domain_freeze.md` §A3.5). Xác nhận bằng hồi quy có chủ đích, không
thêm mã mới (`_require_mirrored_unit` đã có từ Phase 4 vẫn đủ).

**Parent-child archive/delete** — không cascade, ở cả ba tầng:
- Project còn Area `active` → từ chối lưu trữ (`PARENT_HAS_LIVE_CHILDREN` 409).
- Area còn Unit sống (`deleted_at IS NULL`) → từ chối lưu trữ.
- Unit còn Deal sống → từ chối xoá (D-3, xem "Quyết định cần xác nhận" dưới).
Lỗi mang đủ `parent_entity`/`parent_external_id`/`child_entity`/`child_external_ids`
(cắt ở 20, `PARENT_HAS_LIVE_CHILDREN` báo tổng số thật trong message).

**Mini CRM migration** — `0003_minicrm_hierarchy`: `crm_projects`, `crm_areas`
(FK `crm_projects`, khoá tự nhiên, năm CHECK kế hoạch), `crm_units.area_id`
(FK NULLABLE), hai sequence mới. Idempotent (kiểm hai lần), downgrade sạch
(kiểm cả hai chiều). KHÔNG đụng migration nào của backend.

**Security boundary / D-14** — Không route mới nào được nối vào FE. Cổng
`:8100` giữ NGUYÊN mức lộ diện đã có từ trước Phase B (`docker-compose.yml`
`ports: "8100:8000"` — giống hệt cách mọi service dev khác trong file này phơi
ra host, không có gì mới). D-14 (Mini CRM chưa có xác thực ghi) VẪN LÀ
`DECISION REQUIRED`, KHÔNG được giải quyết ở đợt này — nó chặn phần FE-ghi của
Phase F/G, KHÔNG chặn Phase B (CRUD nội bộ, chưa route nào phơi cho FE).

## Quyết định cần chủ dự án xác nhận

**D-3 — xoá Unit khi còn Deal sống.** `phase_a_domain_freeze.md` §A1.8 CHỐT quy
tắc này (từ chối, không cascade) nhưng gắn cờ tường minh: *"đó là THAY ĐỔI so
với hành vi hôm nay... nên được chủ dự án xác nhận trước Phase B."* Đợt này
THỰC THI đúng quy tắc đã CHỐT (không phải bịa một quyết định mới) và ghi lại rõ
ràng ở đây để chủ dự án có cơ hội đảo ngược nếu cần — xem
`tests/test_crud_hierarchy.py::test_unit_with_a_live_deal_cannot_be_deleted`.

## Nợ kỹ thuật ghi lại tường minh (không phải bị bỏ sót)

`uq_crm_units_live_code` (mã căn duy nhất trong một phân khu, còn sống) VẪN
khoá trên `(area_name, unit_type, unit_code)` — không đổi sang `(area_id,
unit_code)`. Lý do: 163 căn tạo TRƯỚC Phase B có `area_id = NULL`, và ràng buộc
mới sẽ khiến TẤT CẢ chúng cùng rơi vào một "phân khu NULL" — chặt hơn ý nghĩa
cũ một cách không chủ đích. An toàn hơn là đúng hơn ở đợt này; xem docstring
đầy đủ ở `minicrm/alembic/versions/0003_minicrm_hierarchy.py` mục thiết kế 3.

## Vì sao 163 căn di sản không có `area_id`

Database Mini CRM ĐANG CHẠY (container thật) mang 163 căn tạo trước
`crm_areas` tồn tại. Backfill một `crm_areas` giả cho chúng sẽ cần bịa số kế
hoạch (`bedrooms`/`area_sqm`/`total_units`) — đúng điều Phase A cấm
("`total_units` KHÔNG BAO GIỜ suy ra bằng cách đếm"). `crm_units.area_id` vì
vậy NULLABLE; 163 dòng đó ở lại là DI SẢN, đọc/sửa (trừ đổi phân khu) bình
thường, không bị đụng tới. Căn tạo/sửa TỪ Phase B trở đi LUÔN có `area_id`.

## Files changed

```text
TẠO MỚI:
  minicrm/alembic/versions/0003_minicrm_hierarchy.py
  minicrm/app/routers/projects.py
  minicrm/app/routers/areas.py
  minicrm/tests/test_crud_projects.py            (15 test)
  minicrm/tests/test_crud_areas.py                (15 test)
  minicrm/tests/test_crud_hierarchy.py            (23 test)
  minicrm/tests/test_migration_0003.py            (18 test)

SỬA:
  minicrm/app/models.py       thêm crm_projects, crm_areas; crm_units.area_id
                               (chỉ khai báo cột — migration là nguồn sự thật)
  minicrm/app/crud.py         Project/Area CRUD; _resolve_area_reference;
                               harden create_unit/update_unit/delete_unit;
                               parent-child archive/delete cho cả ba tầng
  minicrm/app/schemas.py      ProjectOut/Create/Patch/WriteOut,
                               AreaOut/Create/Patch/WriteOut; UnitCreate/Patch
                               nhận external_area_id (hai cách tham chiếu)
  minicrm/app/main.py         nối router projects/areas
  minicrm/tests/conftest.py   seed MỘT dự án+phân khu BOOTSTRAP (đặt tên rõ
                               TỔNG HỢP) sau migration, để MỌI test đang dùng
                               hình dạng area_name/unit_type CŨ (từ trước
                               Phase B) tiếp tục qua KHÔNG SỬA MỘT DÒNG
  minicrm/tests/test_migration_0002.py   fixture `upgraded` nhắm ĐÚNG revision
                               0002 thay vì "head" — "head" giờ trỏ sang 0003,
                               và file này kiểm 0002 CỤ THỂ (SIẾT, không nới:
                               cô lập test khỏi việc thêm migration TƯƠNG LAI)
  minicrm/tests/test_real_backend_sync.py         baseline Mini CRM head
                               0002→0003 (bằng chứng migration thật, ghi rõ lý
                               do trong comment, cùng khuôn Phase A)
  minicrm/tests/test_real_failure_windows.py      baseline Mini CRM head
                               0002→0003, cùng lý do

KHÔNG SỬA:
  toàn bộ src/, alembic/ (backend), frontend/, docs/crm/phase_a_domain_freeze.md
  minicrm/app/sync_client.py, minicrm/app/contract.py, minicrm/app/db.py,
  minicrm/app/config.py, minicrm/app/routers/units.py, deals.py, outbox.py
  minicrm/tests/test_crud_units.py, test_crud_deals.py, test_outbox.py,
  test_sync_client.py, test_contract_copy.py, test_migration_0001.py,
  test_real_endpoints.py    (0 test nào trong các file này bị sửa nội dung)
```

## Migration heads

```text
Mini CRM   0002_minicrm_crud  →  0003_minicrm_hierarchy   (container thật, xác nhận bằng
                                                            `docker compose exec minicrm alembic current`)
Backend    0016_completed_with_conflicts                  (KHÔNG ĐỔI, xác nhận bằng
                                                            `docker compose exec api alembic current`)
```

## Test commands và kết quả THẬT

**Command:** `MINICRM_TEST_DATABASE_URL=... pytest tests/test_crud_projects.py tests/test_crud_areas.py tests/test_crud_hierarchy.py tests/test_migration_0003.py -q`
**Result:** PASS — **71 passed, 0 failed, 0 skipped**

**Command:** `MINICRM_TEST_DATABASE_URL=... pytest -q` (toàn bộ Mini CRM cục bộ, trừ 3 file real E2E — chạy HAI LẦN liên tiếp)
**Result:** PASS cả hai lần — **213 passed, 0 failed, 0 skipped** mỗi lần
(142 test có sẵn từ trước Phase B + 71 test mới; TOÀN BỘ 142 test cũ qua KHÔNG
SỬA MỘT DÒNG assertion nào — hồi quy thật, không phải viết lại)

**Command:** `docker compose restart minicrm` rồi `pytest tests/test_real_backend_sync.py tests/test_real_failure_windows.py tests/test_real_endpoints.py -q` (container thật, sau khi seed một dự án+phân khu BOOTSTRAP qua chính API mới để giữ tương thích với `real_env.AREA_NAME`/`UNIT_TYPE`)
**Result:** PASS — **78 passed, 0 failed, 0 skipped**
**Notes:** Một lần chạy sớm hơn khi ĐANG chạy song song với bộ test cục bộ ở
nền cho ĐÚNG MỘT test KHÔNG liên quan (`test_outbox.py::test_replay_stale_without_any_stale_batch_is_refused`)
báo FAIL — chẩn đoán là tranh chấp CROSS-PROCESS trên cùng một PostgreSQL
server (đúng loại đã ghi nhận ở đợt Phase 5.5 P0). Chạy lại CÔ LẬP (không có
tiến trình nào khác chạm DB) → xanh. Chạy lại toàn bộ 213 test cục bộ cô lập
lần nữa → xanh. Không phải khiếm khuyết mã.

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh -q` (toàn bộ backend)
**Result:** PASS — **1151 passed, 0 failed, 1 skipped**
**Notes:** Y HỆT nền của đợt (g) — Phase B không đụng một file backend nào, và
kết quả regression chứng minh đúng điều đó.

**Command:** `docker compose exec minicrm alembic current` / `docker compose exec api alembic current`
**Result:** Mini CRM `0003_minicrm_hierarchy (head)`; Backend `0016_completed_with_conflicts (head)`

**Command:** `ruff check src/ tests/ minicrm/`
**Result:** PASS — All checks passed!

**Không có test nào bị đếm là PASS trong khi thực ra SKIP.**

## Phase 6 status

**VẪN CHƯA BẮT ĐẦU.** Không đụng tới trong đợt này.

## Phase C readiness

**SẴN SÀNG.** Ba đầu vào Phase C cần đều đã có: (a) `crm_projects`/`crm_areas`
tồn tại với dữ liệu THẬT, có phiên bản, có khoá tự nhiên đã kiểm; (b) ba cột
`mirrored_*` đã có sẵn trên cả hai bảng (Phase B không dùng, nhưng không cần
migration thêm để Phase C bắt đầu ghi vào chúng); (c) `_resolve_area_reference`
chứng minh luật tham chiếu ổn định hoạt động đúng, sẵn sàng làm nền cho việc
dựng phong bì v2.

**Một điều kiện Phase C nên xác nhận trước khi bắt đầu:** D-3 (mục "Quyết định
cần chủ dự án xác nhận" ở trên) — nếu chủ dự án đảo ngược D-3, quy tắc tương
ứng ở tầng chiếu backend (Phase D) cũng phải đảo theo, nên xác nhận sớm rẻ hơn
sửa muộn.

---

# Đợt 2026-08-12 (g) — Phase A: đảo ngược mô hình sở hữu (Mini CRM là nguồn sự thật)

Phase A — Ownership Model Revision
Status: **COMPLETE**

Phạm vi: **chỉ hợp đồng, tài liệu và test.** Không CRUD runtime, không tầng chiếu,
không RBAC runtime, không FE, không migration, không outbox runtime, không xếp
hạng. Backend Alembic head giữ nguyên `0016_completed_with_conflicts`; Mini CRM
giữ nguyên `0002_minicrm_crud`. `SUPPORTED_SCHEMA_VERSIONS` vẫn là `{1}`.

## Vì sao đợt này tồn tại

Đợt (f) đóng băng mô hình **backend sở hữu Project/Area tuyệt đối**, với Mini CRM
chỉ đọc (v1) hoặc đề xuất (v2 nháp), và một quy trình duyệt phân khu. Chủ dự án
chỉ đạo kiến trúc **ngược lại**: Mini CRM phải là nguồn sự thật cho cả bốn tầng
`Project → Area → Unit → Deal`; backend chỉ là bản sao chiếu chỉ đọc. Đợt (g)
thực thi đảo ngược đó ở tầng hợp đồng/tài liệu, giữ nguyên vẹn phần quyết định
không phụ thuộc vào ai sở hữu Project/Area.

## Mô hình CŨ → MỚI

```text
CŨ (đợt f):
  Backend sở hữu Project/Area tuyệt đối.
  Mini CRM: v1 chỉ tham chiếu; v2 nháp CHỈ ĐỀ XUẤT (status='pending' → duyệt).
  Ba trường kế hoạch (bedrooms/area_sqm/total_units): proposed_*, KHÔNG ràng buộc.
  area_ref: ba hình dạng (external_area_id | area_id | area_name+unit_type).
  Vai trò admin: ghi được Project/Area ở backend (create_update_project/area = true).

MỚI (đợt g):
  Mini CRM sở hữu CẢ BỐN tầng: Project, Area, Unit, Deal.
  Backend: BẢN SAO CHỈ ĐỌC (mirror/projection) + đường nhập + API đọc.
  Backend KHÔNG BAO GIỜ tự tạo/tự sửa bốn thực thể nghiệp vụ.
  Năm trường của Area (area_name, unit_type, bedrooms, area_sqm, total_units):
    TẤT CẢ bắt buộc VÀ có thẩm quyền ngay từ hệ nguồn — KHÔNG còn bước duyệt.
  area_ref: CHỈ MỘT hình dạng (external_area_id).
  project_ref: hình dạng CHUẨN đổi sang external_project_id (project_id giữ lại
    chỉ để tương thích cài đặt đã cấu hình ánh xạ sẵn).
  Vai trò backend: KHÔNG ai ghi được Project/Area, kể cả admin
    (create_update_project/area = false cho MỌI vai trò).
  FE: ĐỌC từ backend; GHI qua Mini CRM (hoặc cổng ghi tường minh — D-4).
```

## Ma trận sở hữu (chuẩn, đợt g)

| Entity | Canonical owner | Mini CRM | Backend | FE | Allowed writer |
|---|---|---|---|---|---|
| Project | **Mini CRM** | Tác giả CRUD + phiên bản | Bản sao chỉ đọc | Đọc backend; ghi Mini CRM | Mini CRM; backend chỉ qua tầng chiếu |
| Area | **Mini CRM** | Tác giả CRUD + phiên bản + 5 trường có thẩm quyền | Bản sao chỉ đọc | Đọc backend; ghi Mini CRM | Mini CRM; backend chỉ qua tầng chiếu |
| Unit | **Mini CRM** | Tác giả | Bản sao chỉ đọc | Đọc backend; ghi Mini CRM | Mini CRM; backend chỉ qua tầng chiếu |
| Deal | **Mini CRM** | Tác giả | Bản sao chỉ đọc | Đọc backend; ghi Mini CRM | Mini CRM; backend chỉ qua tầng chiếu |
| Sync state | Chia đôi theo nửa | Sở hữu nửa GỬI | Sở hữu nửa NHẬN | Đọc nửa NHẬN | Mỗi bên ghi nửa của mình |
| Analytics | **Backend** | — | Chủ sở hữu, dẫn xuất | Đọc backend | Bộ tính của backend |

## Thay đổi hợp đồng

**Định danh.** `external_id` bất biến/không tái sử dụng ở CẢ BỐN tầng (mở rộng từ
hai). Phạm vi duy nhất `(source_instance_id, entity)`. `project_ref` đổi hình dạng
chuẩn sang `{external_project_id}`; `area_ref` CHỈ CÒN `{external_area_id}` — hai
hình dạng cũ (`{area_id}`, `{area_name,unit_type}`) BỊ BỎ vì hệ nguồn giờ sở hữu
danh tính và có thể sửa tên, nên tham chiếu theo UUID/nội dung phía nhận không
còn hợp lý.

**Tham chiếu.** `area` không mang `project_ref` riêng (một lô = một dự án, tham
chiếu ở phong bì). `deal` vẫn không mang `project_ref`/`area_ref` (suy qua unit).
Khi lô chứa bản ghi `entity='project'`, `external_id` của nó phải khớp
`project_ref.external_project_id` — luật nghiệp vụ, không diễn đạt được bằng JSON
Schema thuần (fixture `29` canh).

**Sự kiện.** Cả 12 sự kiện (`project_created/updated/deleted`,
`area_created/updated/deleted`, `unit_*`, `deal_*`) giờ là sự kiện đồng bộ do hệ
nguồn phát — ánh xạ sang `(entity, operation)` đã có của repo, không phát minh từ
vựng thứ hai. Thứ tự: `project → area → unit → deal` khi upsert, đảo ngược khi xoá.

**Phiên bản/tombstone.** Không đổi luật — mở rộng nguyên xi lên `project`/`area`.
`crm_source_records.source_entity` là `Text` không enum
(`0006_sync_foundation.py:224`), nên KHÔNG cần migration cho sổ danh tính; toàn bộ
máy sáu nhánh và khoá `FOR UPDATE` của Phase 5 áp dụng không sửa gì.

**Tương thích v1/v2.** v1 **bất biến, KHÔNG đổi qua cả hai đợt v2**:
SHA-256 `e15fd9c5…b49a92a`. v2 **không phải mở rộng cộng thêm của v1** — nó là một
mô hình sở hữu KHÁC (v1: phía nhận sở hữu Project/Area; v2: hệ nguồn sở hữu).
`SUPPORTED_SCHEMA_VERSIONS` vẫn `{1}`; bật v2 là việc của Phase D.

## Phát hiện mới — hai hệ quả chưa có ở đợt (f)

1. **Bốn đường ghi hiện có ở backend mâu thuẫn với mô hình mới:**
   `ProjectService.create_project`/`create_area`/`update_project`/`update_area`
   (`src/services/projects.py:110,170,222,249`). Phase A **không gỡ chúng** —
   `REQUIRED IN PHASE D — NOT IMPLEMENTED NOW`. Có test canh
   (`test_the_existing_backend_write_paths_into_projects_and_areas_are_untouched`)
   xác nhận chúng còn nguyên và sẽ đỏ khi Phase D gỡ/giới hạn.

2. **Rủi ro bảo mật mới, nghiêm trọng — Mini CRM không có xác thực ghi.** Đúng khi
   Mini CRM chỉ là bộ sinh dữ liệu tổng hợp (quyết định trước). Dưới mô hình mới,
   Mini CRM là hệ thống bản ghi mà FE ghi qua: không xác thực nghĩa là ai chạm
   được cổng `:8100` cũng ghi được mọi dữ liệu nghiệp vụ. Ghi thành `DECISION
   REQUIRED D-14` trong `docs/roadmap.md`, và
   `docs/crm/authorization_matrix.json::mini_crm_write_authorization`. **Chặn
   phần FE-ghi của Phase F/G, KHÔNG chặn Phase B.**

## Artifact đã tạo / sửa

```text
GHI ĐÈ (v2 là dự thảo, được phép sửa cho tới khi Phase D bật):
  src/contracts/crm_sync_v2.schema.json         mô hình sở hữu mới; SHA-256 đổi
                                                 từ bản (f) sang
                                                 9620614a46536515fabeae1e9ba1e032c30deb02a74656e11818b1951fe10efb
  minicrm/contracts/crm_sync_v2.schema.json     bản sao BYTE-IDENTICAL

GHI ĐÈ HOÀN TOÀN (thay thế mô hình sở hữu cũ):
  docs/crm/phase_a_domain_freeze.md             §S mới — "Quyết định đã bị thay
                                                 thế", giữ nguyên văn 7 quyết định
                                                 cũ (S-1..S-7) làm lịch sử
  docs/crm/sync_contract_v2_draft.md            §2 mới — giải thích khác biệt
                                                 NỀN TẢNG giữa v1/v2, không phải
                                                 mở rộng cộng thêm

SỬA (không ghi đè):
  docs/crm/authorization_matrix.json            4 hành động create_update_* →
                                                 false cho MỌI vai trò; thêm khối
                                                 mini_crm_write_authorization
  docs/crm/fixtures/README.md                   mục lục 12 fixture v2 mới (18–29)
  docs/roadmap.md                               "Ownership Model — REVISED" mới
                                                 ở đầu; D-1/D-2 đánh dấu ĐÃ CHỐT
                                                 (ngược khuyến nghị gốc); D-14 mới;
                                                 R-10 nâng CAO; ghi chú SUPERSEDED
                                                 ở đầu mục Phase A, B, C, D, E, G
                                                 (nội dung GIỮ NGUYÊN VĂN bên dưới
                                                 mỗi ghi chú — không xoá, không
                                                 viết lại)

TẠO MỚI (thay thế fixture cũ):
  docs/crm/fixtures/18_v2_project_created.json … 29_v2_project_record_mismatches_envelope.json
                                                 12 fixture — 5 hợp lệ, 5 sai
                                                 schema, 2 sai nghiệp vụ

XOÁ (thuộc mô hình đã bị thay thế, không giữ song song):
  docs/crm/fixtures/18_v2_area_proposal.json
  docs/crm/fixtures/19_v2_mixed_tier_ordered.json
  docs/crm/fixtures/20_v2_archive_reverse_order.json
  docs/crm/fixtures/21_v2_area_ref_by_stable_id.json
  docs/crm/fixtures/22_v2_project_entity_rejected.json
  docs/crm/fixtures/23_v2_area_payload_with_project_ref.json
  docs/crm/fixtures/24_v2_area_ref_hybrid_shape.json
  docs/crm/fixtures/25_v2_area_delete_carrying_payload.json
  docs/crm/fixtures/26_v2_unit_before_its_area.json

SỬA (test suite, để phản ánh mô hình mới):
  tests/test_services/test_phase_a_contract_freeze.py   viết lại theo mô hình
                                                 mới: 4 thực thể, area_ref một
                                                 hình dạng, area_payload năm
                                                 trường có thẩm quyền, thêm
                                                 test canh 4 đường ghi cũ chưa
                                                 bị gỡ, thêm test canh
                                                 mini_crm_write_authorization,
                                                 thêm test "không sót fixture
                                                 của mô hình đã bị thay thế"

KHÔNG SỬA:
  src/contracts/crm_sync_v1.schema.json         SHA-256 không đổi
  minicrm/contracts/crm_sync_v1.schema.json     SHA-256 không đổi
  docs/crm/sync_contract_v1_draft.md
  tests/test_services/test_contract_validation.py   phép canh phân vùng theo
                                                 schema_version ĐỌC TỪ NỘI DUNG
                                                 FILE — tự động nhận fixture mới
                                                 mà không cần sửa danh sách nào
  toàn bộ src/**/*.py, minicrm/app/**/*.py, frontend/**, alembic/**
```

## Lệnh và kết quả THẬT

**Command:** `pytest tests/test_services/test_phase_a_contract_freeze.py -q`
**Result:** PASS
**Passed/failed/skipped:** 43 passed, 0 failed, 0 skipped

**Command:** `pytest tests/test_services/test_contract_validation.py tests/test_services/test_phase_a_contract_freeze.py tests/test_services/test_json_payload.py tests/test_services/test_ranking_boundary.py -q`
**Result:** PASS
**Passed/failed/skipped:** 152 passed, 0 failed, 0 skipped

**Command:** `cd minicrm && pytest tests/test_contract_copy.py -q`
**Result:** PASS
**Passed/failed/skipped:** 31 passed, 0 failed, 0 skipped
**Notes:** phép canh SHA-256 hai bản sao v1 vẫn xanh; hai bản sao v2 (đợt g) khớp nhau.

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh -q`
**Result:** PASS
**Passed/failed/skipped:** **1151 passed, 0 failed, 1 skipped** (chạy hai lần liên tiếp: 519.32s và
một lần trước đó, cùng kết quả)
**Notes:** Nền trước đợt này (đợt (f)) là 1142 passed / 1 skipped. Chênh lệch +9
khớp CHÍNH XÁC với số test tăng thêm trong `test_phase_a_contract_freeze.py`
(34 ở đợt (f) → 43 ở đợt (g)). Không test nào khác bị sửa nội dung hay nới lỏng.
**1 SKIP KHÔNG được tính là PASS**: `tests/test_scheduler.py:18`
(apscheduler chỉ có trong image), khoảng trống có sẵn từ trước, không liên
quan Phase A.

**Command:** `ruff check src/ tests/ minicrm/`
**Result:** PASS — All checks passed!

## Kiểm chứng phạm vi

```text
alembic head backend            0016_completed_with_conflicts   KHÔNG ĐỔI
alembic head Mini CRM           0002_minicrm_crud               KHÔNG ĐỔI
SUPPORTED_SCHEMA_VERSIONS       frozenset({1})                  KHÔNG ĐỔI
SUPPORTED_ENTITIES              frozenset({"units","deals"})    KHÔNG ĐỔI
contract_validation.SCHEMA_PATH crm_sync_v1.schema.json         KHÔNG ĐỔI
DashboardPrincipal fields       {"role"}                        KHÔNG ĐỔI
ProjectService bốn đường ghi    create_project/create_area/
                                update_project/update_area      KHÔNG ĐỔI, còn nguyên
SHA-256 v1 (cả hai bản)         e15fd9c5…b49a92a                KHÔNG ĐỔI qua CẢ HAI đợt v2
File mtime < 3 giờ trong
src/ minicrm/app/ minicrm/alembic/
alembic/ frontend/src/          DUY NHẤT src/contracts/crm_sync_v2.schema.json
                                (schema tĩnh, không được runtime nào đọc)
```

Bốn khẳng định runtime **có test canh** (`test_phase_a_contract_freeze.py` nhóm
3). Chúng sẽ ĐỎ khi Phase D/E bắt đầu — tín hiệu ĐÚNG, mỗi test ghi rõ phase nào
được phép làm nó đỏ.

## Phase 6 status

**VẪN CHƯA BẮT ĐẦU.** Không đụng tới trong đợt này. `tests/test_ranking_boundary.py`
nằm trong bộ test đã chạy ở trên, toàn bộ PASS, không sửa assertion nào.

## Phase B có được mở không?

**CÓ**, nhưng đặc tả Phase B hiện có trong `docs/roadmap.md` (viết cho mô hình
CŨ) **cần viết lại trước khi triển khai** — đã đánh dấu SUPERSEDED tại chỗ, trỏ
tới `phase_a_domain_freeze.md` §A1.1/§A1.2/§A2.4 cho mô hình chuẩn. Cổng ra Phase
A vẫn ĐẠT: cả 15 câu hỏi của cổng có câu trả lời tường minh, nhất quán nội bộ,
dưới mô hình sở hữu MỚI.

**Một điều kiện MỚI cần đóng trước khi FE có đường ghi (Phase F/G), không chặn
Phase B:** `D-14` — Mini CRM chưa có xác thực ghi. Phase B (CRUD nội bộ Mini CRM,
chưa có FE nào gọi tới) không bị chặn bởi việc này; Phase F/G thì có.

## Phạm vi KHÔNG làm trong đợt này

```text
- Không CRUD Project/Area/Unit/Deal (Phase B).
- Không sự kiện outbox nào cho phân cấp (Phase C).
- Không tầng chiếu backend cho project/area (Phase D).
- Không bật v2 ở runtime (Phase D).
- Không migration (Phase D — areas.external_id, projects.external_id).
- Không gỡ/giới hạn bốn đường ghi hiện có của ProjectService (Phase D).
- Không RBAC theo phạm vi dự án (Phase E).
- Không xác thực ghi cho Mini CRM (D-14, chưa chốt).
- Không bộ chọn / bảng FE (Phase F, G).
- Không E2E container thật cho phân cấp (Phase H).
- Không xếp hạng, không Phase 6.
- Không đổi một dòng hành vi production nào.
```

---

# Đợt 2026-08-12 (f) — Phase A: đóng băng mô hình miền và hợp đồng (v2 dự thảo)

Phase A — Domain and Contract Freeze
Status: **COMPLETE**

Phạm vi: **chỉ hợp đồng, tài liệu và test.** Không CRUD, không tầng chiếu, không
RBAC runtime, không FE, không migration, không outbox runtime, không xếp hạng.
Backend Alembic head giữ nguyên `0016_completed_with_conflicts` (16 file revision);
Mini CRM giữ nguyên `0002_minicrm_crud`. `SUPPORTED_SCHEMA_VERSIONS` vẫn là `{1}`.

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| Mô hình miền 4 tầng | Có ở tầng khoá ngoại, KHÔNG khai báo ở đâu | **ĐÃ ĐÓNG BĂNG** — `docs/crm/phase_a_domain_freeze.md` |
| Sở hữu Project/Area | Mâu thuẫn tiềm tàng giữa roadmap và hợp đồng v1 §2 | **ĐÃ GIẢI QUYẾT** — backend là chủ sở hữu chuẩn cả hai |
| Project = tenant? | Chưa ai trả lời | **ĐÃ CHỐT** — thực thể NGHIỆP VỤ; phạm vi bảo mật mô hình RIÊNG |
| Hợp đồng v2 | Không tồn tại | **DỰ THẢO ĐÃ ĐÓNG BĂNG** — `crm_sync_v2.schema.json`, hai bản byte-identical |
| Tính bất biến của v1 | Chỉ có phép so hai bản sao với nhau | **GHIM SHA-256 TUYỆT ĐỐI** — sửa cả hai bản cùng lúc cũng không lách được |
| Ma trận phân quyền theo dự án | Không tồn tại | **DỮ LIỆU ĐỌC ĐƯỢC BẰNG MÁY** — `docs/crm/authorization_matrix.json`, 8 test kiểm |
| Thứ tự sự kiện cha–con | Chỉ "unit trước deal" (2 tầng) | **BA TẦNG**, và đảo chiều khi xoá |
| `SUPPORTED_SCHEMA_VERSIONS` | `{1}` | **VẪN `{1}`** — Phase A không bật v2 |
| Ranh giới Phase 6 | NOT STARTED | **VẪN NOT STARTED** |

## Quyết định đã chốt

Đầy đủ ở `docs/crm/phase_a_domain_freeze.md`. Mười lăm cổng của Phase A đều có câu
trả lời; bảng nghiệm thu ở cuối tài liệu đó. Bảy quyết định đáng nêu lại:

1. **Project — backend sở hữu tuyệt đối.** v2 **cố ý KHÔNG có** thực thể
   `project` trong `records[]`. Dự án không bao giờ đi lên qua đường đồng bộ.
   Điều này đóng mâu thuẫn "cả hai hệ cùng sở hữu Project" ở tầng HỢP ĐỒNG chứ
   không phải ở tầng thoả thuận.
2. **Area — mô hình "hệ nguồn ĐỀ XUẤT, người vận hành DUYỆT".** Hệ nguồn phát
   biểu đề xuất; bản ghi hạ cánh ở `status='pending'` và **không dùng được** cho
   tới khi có người duyệt. Ba cột cần thiết (`status`, `reviewed_by`,
   `reviewed_at`, `review_reason`) **đã có** từ migration `0002` — không cột nào
   phải thêm cho riêng quy trình duyệt.
   Ba trường kế hoạch đi trong hợp đồng dưới tên `proposed_total_units` /
   `proposed_bedrooms` / `proposed_area_sqm`, và phía nhận **bị cấm** ghi thẳng
   chúng vào `areas`. Lý do là lý do nguyên văn của hợp đồng v1 §2, giữ nguyên
   vẹn: `total_units` là MẪU SỐ của tỷ lệ hấp thụ, và một mẫu số do máy đặt sẽ
   làm sai mọi con số phía sau một cách im lặng.
3. **Project là thực thể NGHIỆP VỤ, không phải tenant.** `uq_units_source_identity`
   khoá trên `(source_instance_id, external_unit_id)` — KHÔNG có `project_id`.
   Tuyên bố Project là tenant sẽ tạo hai trục cô lập cạnh tranh nhau.
   Không có tầng tổ chức/tenant.
4. **Xoá: không bao giờ vật lý.** Project/Area → `status='archived'` (hai bảng
   này KHÔNG có `deleted_at`); Unit/Deal → tombstone `deleted_at`. Hợp đồng giữ
   MỘT tên thao tác (`delete`) và phía nhận chọn cách biểu diễn — hệ nguồn không
   phải biết chi tiết lưu trữ của phía nhận.
5. **Không cascade, và cha không archive được khi con còn sống.** Cascade ở tầng
   chiếu sẽ đánh dấu đã xoá những dòng mà hệ nguồn vẫn tin là sống; lần sau hệ
   nguồn gửi lại con với revision cao hơn và backend hồi sinh nó — một vòng dao
   động không hội tụ.
6. **Phạm vi phân quyền TĨNH**, gắn vào token, cưỡng chế ở **tầng truy vấn**
   (không phải tầng route, vì unit/deal suy ra dự án qua JOIN 2–3 tầng nên một
   route nhận `area_id` vẫn phải giới hạn). Không cấu hình = **không dự án nào**
   (fail-closed). `admin` xuyên mọi dự án bằng cách được CẤP phạm vi `ALL` tường
   minh, **không** bằng một nhánh mã "nếu admin thì bỏ qua".
7. **Bản ghi bị từ chối giữ đủ 6 trường để phục hồi**, và **cả sáu đều ánh xạ vào
   cột đã có** của `upload_errors` (migration `0006`) — hợp đồng không đòi thêm
   cột lỗi nào.

## Phát hiện khiến Phase A KHÔNG cần migration

`crm_source_records.source_entity` là `sa.Text()` với CHECK **duy nhất là `<> ''`**
(`alembic/versions/0006_sync_foundation.py:224`) — **không có enum**. Vì vậy bản
ghi `area` ghi vào sổ danh tính mà **không cần migration nào cho
`crm_source_records`**, và toàn bộ máy quyết định sáu nhánh (`insert`/`update`/
`skip_stale`/`duplicate_noop`/`conflict`/`tombstone`) cùng khoá hàng
`SELECT ... FOR UPDATE` + `lock_identities()` của Phase 5 áp dụng cho `area`
**không sửa một dòng nào**.

Migration duy nhất mà v2 tạo ra là `areas.external_id` + `source_*` +
`uq_areas_source_identity` — **thuộc Phase D**, đã ghi ở
`docs/crm/sync_contract_v2_draft.md` §9 mục 6. Phase A không tạo nó.

## Quyết định chưa giải quyết

Không mục nào nằm trong cổng ra Phase A. Ghi lại để không ai tưởng Phase A đã trả
lời chúng: D-3 (xoá unit khi còn deal sống — Phase A CHỐT là từ chối, nhưng đó là
THAY ĐỔI so với hành vi hôm nay và cần chủ dự án xác nhận trước Phase B), D-4 (nơi
tổng hợp outbox), D-5 (retry tự động), D-6 (tầng tổ chức), D-7 (`STALE_AFTER_MS` —
vẫn BLOCKED), D-8 (từ vựng trạng thái căn — vẫn `UNKNOWN`), D-9 (ai xử lý đụng độ),
D-10 (cắt sang `domain_units_deals`), D-11 (nguồn đặc trưng khảo sát).

**Về thẩm quyền:** hai quyết định từng được `docs/roadmap.md` ghi là
*DECISION REQUIRED — chủ dự án* đã được đóng băng ở đợt này theo yêu cầu tường
minh của đề bài (mục A1: *"Does Mini CRM create proposals or authoritative records
for Project/Area?"*; mục A7: *"static or dynamic project scope"*): **D-1 → mô hình
đề xuất–duyệt**, **D-2 → phạm vi tĩnh**. Cả hai đảo ngược được với chi phí đã ghi
ở cuối `phase_a_domain_freeze.md`.

## Sửa đổi tường minh đối với hợp đồng v1 §2

`docs/crm/sync_contract_v1_draft.md` §2 ghi *"`projects`, `areas` — CRM tham chiếu
được, không tạo được"*. Mô hình đề xuất–duyệt **mở rộng** vế `areas` và **chỉ áp
cho v2**; dưới v1 hệ nguồn vẫn hoàn toàn không tạo được phân khu. Lý do gốc của v1
được giữ nguyên vẹn, không bị bỏ qua — xem quyết định 2 ở trên.

**File `sync_contract_v1_draft.md` KHÔNG bị sửa.** Sự kế thừa được ghi ở
`sync_contract_v2_draft.md` và `phase_a_domain_freeze.md` §A1.2, để bản ghi lịch
sử của v1 không bị viết lại.

## Artifact đã tạo / sửa

```text
TẠO:
  src/contracts/crm_sync_v2.schema.json           schema v2, hợp lệ Draft 2020-12
  minicrm/contracts/crm_sync_v2.schema.json       bản sao BYTE-IDENTICAL
  docs/crm/phase_a_domain_freeze.md               bản ghi quyết định Phase A (A1–A5, A7, A8)
  docs/crm/sync_contract_v2_draft.md              A6 — phiên bản, tương thích, ngừng dùng
  docs/crm/authorization_matrix.json              A7 — dữ liệu chính sách đọc được bằng máy
  docs/crm/fixtures/18..26_v2_*.json              9 phong bì ví dụ (4 hợp lệ, 4 sai schema,
                                                  1 hợp lệ-schema-nhưng-sai-hợp-đồng)
  tests/test_services/test_phase_a_contract_freeze.py   34 test

SỬA:
  docs/crm/fixtures/README.md                     mục lục 9 fixture mới
  tests/test_services/test_contract_validation.py phép canh "mọi fixture đều được
                                                  phủ" nay PHÂN VÙNG THEO PHIÊN BẢN

KHÔNG SỬA:
  src/contracts/crm_sync_v1.schema.json           SHA-256 không đổi
  minicrm/contracts/crm_sync_v1.schema.json       SHA-256 không đổi
  docs/crm/sync_contract_v1_draft.md
  toàn bộ src/**/*.py, minicrm/app/**/*.py, frontend/**, alembic/**
```

### Về việc sửa `test_contract_validation.py` — SIẾT, không nới

Test `test_every_fixture_on_disk_is_covered_by_this_test_module` **đỏ thật** khi 9
fixture v2 xuất hiện. Nó đang làm đúng việc của nó: một fixture không được bộ kiểm
nào ngó tới là một kịch bản biến mất khỏi bộ kiểm thử mà không ai biết.

Cách sửa **không** phải nhét fixture v2 vào `SCHEMA_INVALID_FIXTURES` — làm thế sẽ
xanh nhưng nói sai: chúng không phải phong bì hỏng, chúng là phong bì của một
phiên bản khác, và hai phiên bản loại trừ lẫn nhau theo thiết kế.

Cách đã làm: **tách thành hai test, phân vùng theo `schema_version` ĐỌC TỪ NỘI
DUNG FILE** (không theo tên file — phân vùng theo tên sẽ sai ngay lần đầu ai đó
đặt tên khác quy ước, và cái sai đó lại im lặng):

* `test_every_v1_fixture_on_disk_is_covered_by_this_test_module` — như cũ, phạm vi v1.
* `test_every_non_v1_fixture_on_disk_is_covered_by_the_phase_a_freeze_module` —
  MỚI, đọc danh sách THẬT trong module kia thay vì chép tay lại.

Bảo đảm tổng thể **mạnh hơn trước**: trước đây chỉ một module chịu trách nhiệm;
giờ mọi fixture phải được đúng một module nhận, và có test cho cả hai vế.

**Đã kiểm rằng phép canh mới THẬT SỰ bắt được**: thả một fixture
`99_uncovered_probe.json` (`schema_version: 3`) không được module nào nhắc tới ⇒
`test_every_non_v1_fixture_...` ĐỎ đúng như mong đợi; gỡ probe ⇒ xanh lại.

## Lệnh và kết quả THẬT

**Command:** `.venv/bin/python -m pytest tests/test_services/test_phase_a_contract_freeze.py -q`
**Result:** PASS
**Passed/failed/skipped:** 34 passed, 0 failed, 0 skipped

**Command:** `.venv/bin/python -m pytest tests/test_services/test_contract_validation.py tests/test_services/test_phase_a_contract_freeze.py tests/test_services/test_json_payload.py tests/test_ranking_boundary.py -q`
**Result:** PASS
**Passed/failed/skipped:** 143 passed, 0 failed, 0 skipped

**Command:** `cd minicrm && ../.venv/bin/python -m pytest tests/test_contract_copy.py -q`
**Result:** PASS
**Passed/failed/skipped:** 31 passed, 0 failed, 0 skipped
**Notes:** phép canh SHA-256 hai bản sao v1 vẫn xanh sau khi thêm hai file v2 vào
cùng thư mục.

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh -q`
**Result:** PASS
**Passed/failed/skipped:** **1142 passed, 0 failed, 1 skipped** (chạy hai lần liên tiếp, cùng kết quả:
551.17s và 536.09s)
**Notes:** Nền trước đợt này là 1107 passed / 1 skipped. Chênh lệch +35 khớp CHÍNH XÁC:
34 test mới của `test_phase_a_contract_freeze.py`, cộng 1 vì phép canh phủ
fixture được tách làm hai test. Không test nào cũ bị sửa nội dung hay bị nới lỏng.
**1 SKIP KHÔNG được tính là PASS**: đó là `tests/test_scheduler.py:18`
(*"apscheduler chỉ có trong image — chạy file này trong container"*), khoảng
trống CÓ SẴN từ trước, không liên quan Phase A.

**Command:** `.venv/bin/python -m ruff check src/ tests/ minicrm/`
**Result:** PASS — All checks passed!

## Kiểm chứng phạm vi

```text
alembic head backend            0016_completed_with_conflicts   KHÔNG ĐỔI
alembic head Mini CRM           0002_minicrm_crud               KHÔNG ĐỔI
SUPPORTED_SCHEMA_VERSIONS       frozenset({1})                  KHÔNG ĐỔI
SUPPORTED_ENTITIES              frozenset({"units","deals"})    KHÔNG ĐỔI
contract_validation.SCHEMA_PATH crm_sync_v1.schema.json         KHÔNG ĐỔI
DashboardPrincipal fields       {"role"}                        KHÔNG ĐỔI
SHA-256 v1 (cả hai bản)         e15fd9c5…b49a92a                KHÔNG ĐỔI
```

Bốn khẳng định cuối **có test canh** (`test_phase_a_contract_freeze.py`, nhóm 3).
Chúng sẽ ĐỎ khi Phase D/E bắt đầu — và đó là tín hiệu ĐÚNG, không phải hồi quy;
mỗi test ghi rõ phase nào được phép làm nó đỏ.

## Phase 6 status

**VẪN CHƯA BẮT ĐẦU.** `tests/test_ranking_boundary.py` toàn bộ PASS, không sửa một
assertion nào: không `src/ranking/`, không bảng xếp hạng nào bị ghi, không
worker/cò kích hoạt, không route/schema xếp hạng nào lộ ra.

## Phase B có được mở không?

**CÓ.** Cổng ra Phase A — *"No ambiguous parent-child or source-of-truth rule
remains"* — **ĐẠT**: cả 15 câu hỏi của cổng đều có câu trả lời tường minh, nhất
quán nội bộ, và nằm trong repo chứ không nằm trong hội thoại.

Ba đầu vào mà Phase B cần và giờ đã có: (a) mô hình sở hữu Area dứt khoát, nên
Mini CRM biết nó được ghi gì và không được ghi gì; (b) luật danh tính/phiên bản
cho `area` bằng đúng luật của `unit`/`deal`, nên không phải phát minh cơ chế mới;
(c) luật cha–con và thứ tự, nên phần kiểm cục bộ của Phase B có đích rõ ràng.

**Một điều kiện nên đóng trước khi Phase B viết mã:** D-3 (từ chối tombstone một
căn còn giao dịch sống) là một THAY ĐỔI hành vi so với hôm nay. Phase A chốt nó để
luật cha–con nhất quán ở cả ba tầng, nhưng nó đáng được chủ dự án xác nhận —
không chặn việc bắt đầu Phase B, chỉ chặn phần cưỡng chế cụ thể đó.

## Phạm vi KHÔNG làm trong đợt này

```text
- Không CRUD Project/Area/Unit/Deal (Phase B).
- Không sự kiện outbox nào cho phân cấp (Phase C).
- Không tầng chiếu backend cho `area` (Phase D).
- Không bật v2 ở runtime (Phase D).
- Không migration (Phase D — areas.external_id).
- Không RBAC theo phạm vi dự án (Phase E).
- Không bộ chọn / bảng FE (Phase F, G).
- Không E2E container thật cho phân cấp (Phase H).
- Không xếp hạng, không Phase 6.
- Không đổi một dòng hành vi production nào.
```

---

# Đợt 2026-08-12 (e) — Phase 5.5: chốt ngưỡng `STALE_AFTER_MS` — BLOCKED (thiếu quyết định nghiệp vụ)

Phase 5.5 — Freshness policy (STALE_AFTER_MS) finalized
Status: **BLOCKED (missing business input)**

Không có thay đổi mã nguồn nào trong đợt này. `frontend/src/utils/freshness.js`
giữ nguyên hằng số tạm `STALE_AFTER_MS = 24h` cùng nhãn "NGƯỠNG TẠM, chưa phải
quyết định nghiệp vụ" đã có từ đợt (d) — nhãn đó VẪN ĐÚNG sau khi điều tra, nên
không có gì để sửa ở đó.

## Evidence used

Trước khi đề xuất một con số, đã kiểm ba nguồn bằng chứng thật, không suy đoán:

**1. Dữ liệu `upload_files` (transport_mode='api_push') trên database DEV thật**
(truy vấn trực tiếp `absorptionforecast-db-1`, không phải database test):

```text
Tổng số lô: 451 (units: 332 · deals: 119)
Khoảng thời gian: 2026-08-11 15:41:15 → 2026-08-12 10:49:15 (~19 giờ)

Phân bố khoảng cách giữa hai lô liên tiếp:
  < 5 giây:        366 lô   (81%)
  5–60 giây:        62 lô
  1–10 phút:         12 lô
  10–60 phút:         6 lô
  > 1 giờ:            4 lô  (lớn nhất: 9.48 giờ, rồi 2.81 giờ, 2.16 giờ, 1.08 giờ)
```

**Kết luận từ số 1**: phân bố này khớp CHÍNH XÁC với nhịp làm việc của kỹ sư
(bùng nổ hàng loạt lô cách nhau dưới 5 giây khi chạy bộ test/E2E thật, xen giữa
là các khoảng nghỉ vài giờ khi không ai làm việc — khoảng 9.48 giờ trùng với một
đêm giữa hai phiên làm việc). Đây là NHIỄU CỦA HOẠT ĐỘNG KỸ SƯ, không phải nhịp
đồng bộ của một đội kinh doanh dùng CRM thật.

**2. Kiến trúc đồng bộ**: `grep` toàn bộ `minicrm/app/*.py` cho
`cron|schedule|interval|periodic|APScheduler` — KHÔNG có kết quả nào. Mini CRM
đẩy dữ liệu ra ngay sau mỗi lần COMMIT cục bộ (outbox transactional, Phase 4:
"local commit → automatic outbound sync trigger") — KHÔNG có job định kỳ nào.
Do đó "chu kỳ đồng bộ" không tồn tại như một khái niệm hạ tầng: tần suất đồng
bộ hoàn toàn theo tần suất người dùng CRM tạo/sửa dữ liệu, và đó là một câu hỏi
NGHIỆP VỤ (đội kinh doanh dùng CRM năng động cỡ nào), không phải kỹ thuật.

**3. `scripts/sync_simulator.py`** — công cụ duy nhất tạo ra lưu lượng "giống
CRM" trong môi trường này — tự ghi rõ trong docstring: *"KHÔNG PHẢI PRODUCTION
CRM... KHÔNG PHẢI NGUỒN SỰ THẬT NGHIỆP VỤ"*. Không có Mini CRM thật đang phục
vụ một đội kinh doanh thật ở dự án này — Mini CRM là hạ tầng tổng hợp
(project-owned synthetic infrastructure, xác nhận lại từ ranh giới Phase 4).

**4. `docs/roadmap.md`** (đọc, KHÔNG sửa — ngoài phạm vi file được phép của đợt
này) đã tự ghi nhận đúng khoảng trống này TỪ TRƯỚC, ở đợt tư vấn kiến trúc
trước đó: dòng 1579 `ngưỡng **DECISION REQUIRED**`, dòng 1466
`fresh / aging / stale **[GAP]** — cần ngưỡng`, và hợp đồng đề xuất
`GET /api/v1/data-freshness` (C-5, **PROPOSED CONTRACT — NOT IMPLEMENTED**) đã
thiết kế `thresholds{fresh,aging}` như một giá trị CẤU HÌNH ĐƯỢC, không phải
một hằng số kỹ thuật suy ra từ log.

## Vì sao đây là BLOCKED, không phải "cứ chọn số tốt nhất có thể"

Đề bài cho phép "nếu bằng chứng đủ, cứ tiến hành với giá trị có căn cứ tốt nhất"
— nhưng bằng chứng ở đây KHÔNG đủ theo đúng nghĩa đó: một con số suy ra từ nhịp
chạy test của kỹ sư (vd. "2-3× khoảng cách trung vị quan sát được") sẽ trông có
vẻ có căn cứ trong khi thực chất là bịa — nó đo hành vi CI/dev, không đo hành vi
kinh doanh. Làm vậy đúng là loại "invented business threshold" mà toàn bộ đợt
Phase 5.5 P0 trước đã nhiều lần được yêu cầu tránh, và sẽ khiến ngưỡng MỚI trông
đáng tin hơn ngưỡng CŨ (24h) một cách giả tạo — trong khi cả hai đều không có
câu trả lời cho câu hỏi thật: "Mini CRM thật (khi có) sẽ được đội kinh doanh
cập nhật thường xuyên cỡ nào, và họ chấp nhận dashboard cũ tới đâu trước khi
coi là không đáng tin?"

## Business input cần thiết để mở khoá

Cần MỘT trong hai:

1. **Một SLA nghiệp vụ tường minh**, ví dụ: "Dashboard được coi là cũ nếu không
   có lần đồng bộ CRM thành công nào trong N phút/giờ" — do chủ sản phẩm hoặc
   đội vận hành kinh doanh quyết định, dựa trên việc họ dự định dùng CRM thật
   (chưa tồn tại) năng động ra sao.
2. **Dữ liệu nhịp độ CRM THẬT** — một khi có Mini CRM thật (không còn là hạ
   tầng tổng hợp) phục vụ một đội kinh doanh thật trong một khoảng thời gian đủ
   dài để đo được nhịp tạo/sửa unit/deal tự nhiên (không phải nhịp kỹ sư chạy
   test) — LÚC ĐÓ mới có căn cứ kỹ thuật hợp lệ để suy ra ngưỡng theo công thức
   "2-3× nhịp quan sát".

Không có cả hai ở đợt này. `docs/roadmap.md` đã ghi nhận khoảng trống này —
không lặp lại việc ghi ở đây theo yêu cầu phạm vi file của đợt này (không sửa
`docs/roadmap.md`).

## Per-entity-type

**KHÔNG đánh giá được** — cùng lý do: units (332 lô) và deals (119 lô) trong dữ
liệu dev đều là nhiễu kỹ sư, tỷ lệ 332:119 phản ánh việc test units nhiều hơn
deals trong bộ test, KHÔNG phản ánh tần suất nghiệp vụ thật của hai loại giao
dịch. Không có căn cứ để tách ngưỡng theo loại thực thể.

## Implementation

**KHÔNG có.** `STALE_AFTER_MS` giữ nguyên ở
`frontend/src/utils/freshness.js:12`, giá trị và chú thích không đổi. Không
file nào khác bị sửa.

## Tests

**Không chạy bộ hồi quy đầy đủ** — không có thay đổi mã nguồn nào để kiểm
chứng. `ruff check src/ tests/ minicrm/` chạy lại để xác nhận cây làm việc vẫn
sạch sau khi đọc/truy vấn (không sửa file mã nguồn nào): **All checks passed**.

## Remaining note

Ngưỡng này CÓ THỂ chốt lại BẤT CỨ LÚC NÀO chỉ bằng một thay đổi cấu hình MỘT
DÒNG ở `frontend/src/utils/freshness.js` (hoặc chuyển thành biến môi
trường/response backend nếu quyết định muốn cấu hình runtime thay vì build-time
— đó là một lựa chọn kỹ thuật nhỏ, không phải điều đang chặn). Không cần thêm
việc kỹ thuật nào khác một khi có câu trả lời nghiệp vụ — toàn bộ cơ chế đọc
`last_successful_sync`/`last_attempted_sync`/`last_sync_status` từ backend,
phân loại `fresh/stale/sync_failed/never_synced/calculation_outdated`, và hiển
thị trên `FreshnessBanner` đã hoạt động đúng và có test từ đợt (d) — chỉ riêng
CON SỐ ngưỡng là đang chờ quyết định.

---

# Đợt 2026-08-12 (d) — Phase 5.5 P0: sửa dữ liệu gây hiểu lầm, xác thực, phân quyền, mặt đọc vận hành, ngữ nghĩa trạng thái

Phase 5.5 P0 implementation
Status: **COMPLETE**

Bốn phát hiện của tư vấn kiến trúc Phase 5.5 (F-1 file/CRM lẫn lộn, F-2 đồng hồ
trình duyệt giả làm bằng chứng đồng bộ, thiếu xác thực/phân quyền, `conflict`
biến thành `failed`) đều đã được SỬA và KIỂM CHỨNG bằng test thật trên
PostgreSQL/container thật — không phải chỉ tài liệu hoá.

## P0-A — dữ liệu gây hiểu lầm

- **`transport_mode`**: `GET /files` nhận thêm tham số lọc `transport_mode=file_upload|api_push`
  (422 `INVALID_TRANSPORT_MODE` nếu sai giá trị). Mỗi dòng `FileSummary` giờ
  luôn mang `transport_mode`, `external_batch_id`, `source_system`; `filename`
  đổi sang `str | None` (đúng thực tế: lô CRM có `filename=NULL`). Giá trị lạ
  (không phải hai giá trị của CHECK constraint) ánh xạ về `"unknown"` — phòng
  thủ, dù `ck_upload_files_transport_mode` khiến NULL/lạ thật sự không xảy ra
  với dữ liệu mới. `src/api/files.py`, `src/models/schemas.py`.
- **Tách file/CRM ở giao diện**: `UploadPage.jsx` mặc định CHỈ hiện file tải tay
  (fix trực tiếp F-1: 361 lô CRM từng lẫn vào "Lịch sử nạp" với `filename=NULL`).
  Thêm hai tab "Tải lên file" / "Đồng bộ CRM"; `FileStatusTable.jsx` đổi cột đầu
  theo `transportMode` (tên file ↔ mã lô + hệ nguồn) thay vì suy đoán qua việc
  có `filename` hay không.
- **Bằng chứng đồng bộ THẬT thay đồng hồ trình duyệt**: `AbsorptionSummaryOut`
  thêm ba trường `last_successful_sync` / `last_attempted_sync` /
  `last_sync_status`, đọc từ `upload_files` (`transport_mode='api_push'`) —
  KHÔNG suy đoán ngưỡng cũ/mới ở backend (đó là quyết định nghiệp vụ chưa có
  chủ). `src/api/dashboard.py::_sync_freshness`.
- **Bỏ `new Date()` làm bằng chứng đồng bộ**: `DashboardPage.jsx` đổi
  `lastSync` → `lastViewedAt`, hiển thị RÕ là "Xem lúc" (đồng hồ trình duyệt,
  KHÔNG BAO GIỜ gọi là "Đồng bộ lúc"). Thêm `FreshnessBanner` đọc ba trường
  backend ở trên, cộng nhãn nguồn (`calculator`). Ngưỡng "cũ bao lâu thì cảnh
  báo" (`STALE_AFTER_MS = 24h` trong `frontend/src/utils/freshness.js`) được
  gắn nhãn TƯỜNG MINH là NGƯỠNG TẠM, không phải quyết định nghiệp vụ đã chốt —
  xem DECISION REQUIRED trong `docs/roadmap.md` Phase 5.5 (không sửa file đó
  trong đợt này).

## P0-B — mặt truy cập nguy hiểm

- **RBAC mới**: `src/services/dashboard_auth.py`. Ba vai trò tĩnh
  (`business_viewer < pipeline_operator < admin`), MỖI vai trò ứng với ĐÚNG MỘT
  token cấu hình qua `Settings` (`DASHBOARD_BUSINESS_VIEWER_TOKEN`,
  `DASHBOARD_PIPELINE_OPERATOR_TOKEN`, `DASHBOARD_ADMIN_TOKEN`). Vai trò suy ra
  từ TOKEN NÀO KHỚP (`secrets.compare_digest`), KHÔNG BAO GIỜ từ một trường
  client tự khai (không có `X-Role`). Header `Authorization: Bearer <token>` —
  TÁI DÙNG cơ chế đã có sẵn (nhưng chưa ai gọi) ở `frontend/src/api/client.js`.
  Chưa cấu hình token nào = mặt đọc ĐÓNG (503 `DASHBOARD_AUTH_DISABLED`), giống
  hệt nguyên tắc đã có của `OPS_API_TOKEN`.
- **Route được bảo vệ**: `GET /sync-runs`, `GET /sync-errors`,
  `GET /sync-runs/{id}/payload` đòi `pipeline_operator+`.
- **Payload thô CHE mặc định**: `GET /sync-runs/{id}/payload?view=redacted`
  (mặc định) không trả `payload`, chỉ trả `payload_sha256`/`payload_bytes`.
  `view=raw`: `pipeline_operator` cần thêm `confirm=true` (403
  `RAW_PAYLOAD_CONFIRMATION_REQUIRED` nếu thiếu); `admin` không cần.
- **`POST /sync-runs/{id}/reprocess` — HAI đường xác thực CỘNG THÊM nhau,
  không đường nào thay thế đường kia**:
  1. `X-API-Key` đúng `source_instance_id` của lô — đường VỐN CÓ từ Phase 3, để
     CHÍNH hệ nguồn tự chạy lại lô của nó. KHÔNG đổi hành vi, không cần
     `confirm`, không có test nào cũ bị sửa vì đường này.
  2. `Authorization: Bearer <token vai trò>` (`pipeline_operator+`) — đường
     MỚI, để người trực vận hành chạy lại lô thay hệ nguồn. Đòi thêm
     `confirm=true` (422 `CONFIRMATION_REQUIRED` nếu thiếu) — an toàn bấm nhầm,
     cùng khuôn với `override_safety_gate` đã có ở `reconciliation.py`.
  Quyết định KHÔNG gộp hai đường / KHÔNG thay đường cũ: `/sync-runs/{id}`,
  `/sync-runs/{id}/errors` là mặt POLL của CHÍNH hệ nguồn đã gửi lô (không có
  vai trò con người nào sở hữu khoá CRM), khác hẳn mặt TỔNG QUAN của người trực
  — bắt cả hai đi qua RBAC người sẽ phá luôn hợp đồng polling máy-với-máy đã có
  từ Phase 3 và làm đỏ hàng chục test hiện có. Đây là quyết định TƯỜNG MINH,
  không phải một khoảng trống bị bỏ sót.
- **Audit**: mọi hành động bảo vệ (`list_sync_runs`, `list_sync_errors`,
  `view_payload`, `reprocess`) ghi MỘT dòng log có cấu trúc
  (`dashboard.audit.<action>`, kèm vai trò + ngữ cảnh) qua `dashboard_auth.audit()`.
  KHÔNG có bảng audit truy vấn được — xem "Khoảng trống còn lại".
- **Secret không log**: token đọc qua `SecretStr`; `audit()` chỉ ghi `role`,
  không bao giờ ghi token thô hay tiền tố token.

## P0-C — phân quyền

| Hành động | business_viewer | pipeline_operator | admin |
|---|---:|---:|---:|
| Xem dashboard doanh nghiệp / units / deals | ALLOW (mở, không cần token — như hiện trạng MVP1) | ALLOW | ALLOW |
| `GET /sync-runs` (tổng quan pipeline) | DENY (403) | ALLOW | ALLOW |
| `GET /sync-errors` | DENY (403) | ALLOW | ALLOW |
| Xem payload REDACTED | DENY (403, chặn ở `require_operator`) | ALLOW | ALLOW |
| Xem payload RAW | DENY | CONDITIONAL (`confirm=true`) | ALLOW |
| `reprocess` qua token vai trò | DENY (403) | CONDITIONAL (`confirm=true`) | ALLOW (`confirm=true` vẫn cần — an toàn thao tác, không phải rào quyền) |
| Quản lý user/vai trò | N/A — không có API (vai trò tĩnh, cấu hình qua biến môi trường; xem "Khoảng trống còn lại") | N/A | N/A |
| Đổi cấu hình pipeline | N/A — không có API tương ứng nào tồn tại | N/A | N/A |

Enforcement: backend (FastAPI `Depends(require_role(...))`), KHÔNG có tầng FE
nào tự quyết định quyền — `require_operator`/`require_role` chặn TRƯỚC khi vào
hàm xử lý. 401/403 luôn có cấu trúc `{"message", "error_code"}` nhất quán.

## Bước 4 — mặt đọc vận hành mới

- **`GET /sync-runs`**: lọc `source` (`source_system`), `status`,
  `external_batch_id`, `from`/`to` (`uploaded_at`), sắp `asc`/`desc`, phân
  trang. CHỈ lô CRM (`transport_mode='api_push'`) — file tải tay có
  `/files?transport_mode=file_upload` riêng.
- **`GET /sync-errors`**: JOIN sang `upload_files` để lọc `source`, `entity`,
  `batch`, `error_code`/`error_category`, khoảng ngày; tra XUYÊN LÔ (khác
  `/sync-runs/{id}/errors` vốn chỉ một lô).
- **`GET /sync-runs/{id}/payload`**: xem P0-B.
- **`GET /deals`**: `src/api/inventory.py`, cùng quy ước với `/inventory` đã
  có (đọc thẳng `deals`/`units`, KHÔNG qua dữ liệu tổng hợp cũ). MỞ — cùng mức
  với `/inventory`, không cần token (dữ liệu nghiệp vụ, không phải nội bộ
  pipeline).
- Không N+1: mỗi endpoint đúng MỘT câu đếm + MỘT câu lấy dữ liệu, không vòng
  lặp truy vấn theo từng dòng.

## Bước 5 — nhất quán ngữ nghĩa

- **Trạng thái kết thúc mới**: `SyncRunService._terminal_status` (`src/services/sync_runs.py`)
  viết lại hoàn toàn. Lỗi cũ: `blocked = rejected + conflicts` coi đụng độ
  ngang hàng bản ghi hỏng, nên một lô MỘT bản ghi mà bản ghi đó là `conflict`
  (không có gì khác hỏng) báo `status='failed'` — SAI, vì đụng độ là một quyết
  định ĐÃ ghi nhận, không mất dữ liệu. Quy tắc mới:
  - `rejected==0, conflicts==0` → `completed`
  - `rejected==0, conflicts>0` → **`completed_with_conflicts`** (MỚI)
  - `rejected>0, processed>0` → `partially_completed`
  - `rejected>0, processed==0` → `failed` (thất bại TOÀN PHẦN đúng nghĩa —
    KHÔNG một bản ghi nào đi qua được, kể cả dưới dạng đụng độ/skip)
- **Migration BẮT BUỘC (đã kiểm chứng cần thiết, không suy đoán)**: `status`
  ghi qua CHECK constraint `ck_upload_files_status`, giới hạn đúng 5 giá trị
  cũ. `alembic/versions/0016_completed_with_conflicts.py` nới thêm
  `'completed_with_conflicts'` — xác nhận bằng CÁCH CHẠY THẬT: không có
  migration này, `UPDATE ... SET status='completed_with_conflicts'` NỔ NGAY
  `CheckViolationError` (bắt được khi chạy `tests/test_api/test_sync_idempotency.py`
  lần đầu). KHÔNG đổi bảng, không đổi cột nào khác, không backfill (dữ liệu cũ
  vẫn hợp lệ với danh sách mới — siêu tập). Backend Alembic head:
  `0015_ranking_results` (15 file) → **`0016_completed_with_conflicts` (16 file)**.
  Mini CRM head giữ nguyên `0002_minicrm_crud` — hai cây KHÔNG giao nhau (kiểm
  lại bằng `minicrm/tests/test_real_backend_sync.py::test_the_two_alembic_histories_stay_separate`,
  đã cập nhật số revision mong đợi).
- **Nguồn dữ liệu dashboard doanh nghiệp (5B)**: kiểm tra thực tế —
  `frontend/src/api/endpoints.js` CHƯA có lời gọi nào tới `/inventory` (xác
  nhận lại bằng grep), nên rủi ro "trang cùng lúc đọc hai nguồn xung đột" mà
  tư vấn kiến trúc nêu là rủi ro KIẾN TRÚC (đã ghi ở `docs/roadmap.md` Phase
  5.5), không phải một lỗi hiển thị đang xảy ra trên trang nào hôm nay. Quyết
  định P0 (không âm thầm): (1) `AbsorptionSummaryOut.calculator` giờ LUÔN hiển
  thị trên `DashboardPage.jsx` (`FreshnessBanner`) nên nguồn số liệu KHÔNG BAO
  GIỜ xuất hiện mà không gắn nhãn; (2) ba trường bằng chứng đồng bộ đi kèm cả
  hai bộ tính (`legacy_aggregate` VÀ `domain_units_deals`, kiểm bằng
  `test_freshness_fields_are_present_on_the_domain_calculator_too`). Dựng một
  màn hình tồn kho CRM-mirror đầy đủ bị coi là NGOÀI PHẠM VI P0 (không có
  trang nào đang trộn hai nguồn để sửa hôm nay) — đây là quyết định phạm vi
  TƯỜNG MINH, không phải bỏ sót, và khớp chỉ đạo phạm vi FE của người dùng
  trong đợt này ("dùng frontend hiện có, sửa tối thiểu, không dựng màn hình
  vận hành mới").

## Files changed

**Backend (mới):**
`src/services/dashboard_auth.py`,
`alembic/versions/0016_completed_with_conflicts.py`

**Backend (sửa):**
`src/config.py` (ba token vai trò + `dashboard_auth_configured`),
`src/models/schemas.py` (`FileSummary.transport_mode`/`external_batch_id`/`source_system`,
`AbsorptionSummaryOut` × 3 trường freshness, `SyncRunStatus` thêm
`completed_with_conflicts`, `SyncRunSummary`/`SyncRunList`/`SyncErrorEntryOut`/
`SyncErrorList`/`SyncPayloadOut`/`DealOut`/`DealList` mới),
`src/api/files.py` (lọc `transport_mode`),
`src/api/sync.py` (`GET /sync-runs`, `GET /sync-errors`,
`GET /sync-runs/{id}/payload`, `_authorize_reprocess` kép, `confirm`),
`src/api/inventory.py` (`GET /deals`),
`src/api/dashboard.py` (`_sync_freshness`),
`src/services/sync_runs.py` (`_terminal_status` viết lại).

**Backend (test mới):**
`tests/test_services/test_dashboard_auth.py` (14),
`tests/test_services/test_terminal_status.py` (7),
`tests/test_api/test_pipeline_read_surface.py` (19),
`tests/test_api/test_absorption_freshness.py` (7),
`tests/test_migrations/test_0016_completed_with_conflicts.py` (5).

**Backend (test sửa):**
`tests/test_api/test_files.py` (+5 test transport_mode),
`tests/test_api/test_sync_idempotency.py` (+1 test `completed_with_conflicts`),
`tests/test_services/test_source_identity.py` (1 assertion sửa: lô một bản ghi
đụng độ không còn là `'failed'`),
`tests/test_ranking_boundary.py` (đếm revision 15→16, lý do ghi rõ trong test).

**Mini CRM (test sửa):**
`minicrm/tests/test_real_backend_sync.py` (1 assertion: backend head
`0015_ranking_results` → `0016_completed_with_conflicts`).

**Frontend (mới):**
`frontend/src/utils/freshness.js`.

**Frontend (sửa):**
`frontend/src/api/client.js` (401/403 thông báo thân thiện, `isAuthError`),
`frontend/src/api/endpoints.js` (`listFiles(transportMode)`, `listSyncRuns`,
`listSyncErrors`, `listDeals`),
`frontend/src/pages/DashboardPage.jsx` (`lastViewedAt`, `FreshnessBanner`,
nhãn `calculator`),
`frontend/src/pages/UploadPage.jsx` (tab Tải lên file / Đồng bộ CRM),
`frontend/src/components/FileStatusTable.jsx` (cột theo `transportMode`).

**Cấu hình:**
`.env.example` (ba biến `DASHBOARD_*_TOKEN` mới, có ghi chú).

## Commands executed

```bash
ruff check src/ tests/ minicrm/
# All checks passed! (0 lỗi)

TEST_DATABASE_URL=... python -m alembic upgrade head
# 0015_ranking_results -> 0016_completed_with_conflicts

TEST_DATABASE_URL=... python -m pytest tests -q     # lần 1
# 1 failed (test_source_identity.py — assertion CŨ, ĐÃ SỬA), 1106 passed, 1 skipped
TEST_DATABASE_URL=... python -m pytest tests -q     # lần 2, sau khi sửa
# 1107 passed, 1 skipped in 426.22s
TEST_DATABASE_URL=... python -m pytest tests -q     # lần 3
# 1107 passed, 1 skipped in 420.73s
# Hai lần sau GIỐNG HỆT nhau về số lượng — xác nhận tất định.

cd minicrm && MINICRM_TEST_DATABASE_URL=... pytest -q
# 220 passed (bao gồm 78 test container thật)

pytest minicrm/tests/test_real_backend_sync.py minicrm/tests/test_real_failure_windows.py \
       minicrm/tests/test_real_endpoints.py -q
# 78 passed — container backend + Mini CRM THẬT, restart api container để áp
# migration 0016 lên database dev trước khi chạy

docker compose restart api   # áp dụng 0016 lên database DEV qua entrypoint RUN_MIGRATIONS=true

cd frontend && npm run build
# ✓ 643 modules transformed, built in 2.54s — KHÔNG lỗi
```

## Actual results

- **Ruff**: sạch trên `src/`, `tests/`, `minicrm/`.
- **Backend full suite**: 1107 passed / 1 skipped (`test_scheduler.py` — thiếu
  `apscheduler` ngoài image, đúng như quy ước cũ, KHÔNG bị biến thành PASS).
  Chạy lại lần đầu bắt được ĐÚNG MỘT test đang mã hoá hành vi CŨ (đã sửa theo
  đúng yêu cầu 5A, không phải "làm xanh bằng cách nới lỏng" — assertion mới
  chặt hơn, không lỏng hơn).
- **Mini CRM**: 220 passed, gồm 78 test container thật (backend :8000 + Mini
  CRM :8100 + hai Postgres :5432/:5433). Một assertion cứng số revision đã cập
  nhật (bằng chứng migration mới tồn tại, không phải nới lỏng).
- **Frontend**: `npm run build` thành công (606.83 kB / gzip 184.57 kB, cảnh
  báo chunk-size CÓ SẴN từ trước, không phải hồi quy mới). Dev server (mount
  sống `./frontend/src`) phục vụ mọi module đã sửa với HTTP 200, không lỗi
  resolve. **KHÔNG có bộ test frontend tự động** (không `vitest`/`jest` nào
  được cấu hình trong repo này — xác nhận qua `package.json`) — xác minh giới
  hạn ở build + kiểm tra thủ công qua dev server, KHÔNG tự nhận là "đã test
  UI" theo nghĩa test tự động.

## Known skips

- `tests/test_scheduler.py` — SKIP (thiếu `apscheduler` ngoài Docker image),
  từ trước đợt này, không liên quan.
- Không có bộ test tự động cho frontend (không phải "known skip" của TASK này —
  là khoảng trống hạ tầng đã tồn tại từ đầu dự án).

## Remaining blockers / quyết định phạm vi đã ghi lại (không âm thầm)

1. **Mini CRM outbox (list/resend/replay-stale) KHÔNG được đưa vào RBAC mới.**
   Quyết định TƯỜNG MINH, người dùng xác nhận trong đợt này: Mini CRM là hệ
   thống cô lập theo kiến trúc từ Phase 4 (không cơ sở dữ liệu chung, không
   import chéo); áp RBAC của backend lên nó sẽ phá ranh giới đó và đòi viết lại
   khoảng 30 test container thật hiện có. Nếu cần bảo vệ, đó là một quyết định
   riêng (thêm một token vận hành CHO MINI CRM, theo đúng mẫu `X-Ops-Token` đã
   có ở backend) — chưa làm trong đợt này.
2. **Không có màn hình vận hành mới (sync-runs/sync-errors/payload/reprocess).**
   Theo đúng chỉ đạo phạm vi FE của người dùng ("dùng frontend hiện có, sửa tối
   thiểu, không dựng console vận hành mới trừ khi bắt buộc"). Bốn endpoint MỚI
   tồn tại đầy đủ, có RBAC, có test — gọi được qua API/curl hoặc
   `frontend/src/api/endpoints.js` (`listSyncRuns`/`listSyncErrors`/`listDeals`
   đã sẵn sàng, chưa có màn hình nào gọi).
3. **Không có API quản lý user/vai trò hay đổi cấu hình pipeline.** Vai trò là
   BA TOKEN TĨNH cấu hình qua biến môi trường, không phải bảng người dùng —
   đúng "cơ chế nhỏ nhất, cấu hình qua deployment, fail-closed" mà đề bài yêu
   cầu khi chưa có hệ xác thực người dùng thật. Quản lý vai trò ĐỘNG (tạo/sửa/
   xoá qua API) cần một bảng mới → một migration mới → ngoài phạm vi "không
   migration trừ khi chứng minh được cần" của đợt này.
4. **Không có API audit-history truy vấn được.** Audit hiện là log có cấu trúc
   (`dashboard.audit.*`), đủ để tra qua công cụ tổng hợp log, nhưng không có
   endpoint đọc lại. Một bảng audit truy vấn được cũng cần migration mới —
   hoãn, cùng lý do với mục 3.
5. **`STALE_AFTER_MS` (24h) trong `frontend/src/utils/freshness.js` là NGƯỠNG
   TẠM**, gắn nhãn tường minh trong code, KHÔNG PHẢI quyết định nghiệp vụ đã
   chốt — chủ sở hữu sản phẩm cần quyết định ngưỡng thật (đã ghi trong
   DECISION REQUIRED của `docs/roadmap.md` Phase 5.5 từ đợt tư vấn kiến trúc
   trước; không sửa file đó trong đợt này vì không nằm trong phạm vi được phép
   sửa của đợt này).
6. **Không có bộ test tự động cho frontend** (khoảng trống có sẵn từ đầu dự
   án, không phải do đợt này gây ra) — xác minh FE giới hạn ở build thành công
   + kiểm tra thủ công qua dev server.

## Phase 6 status

**VẪN CHƯA BẮT ĐẦU.** `tests/test_ranking_boundary.py` (đã cập nhật đúng một
assertion số lượng revision, lý do ghi trong chính test) toàn bộ PASS: không
`src/ranking/`, không bảng xếp hạng nào bị ghi, không worker/cò kích hoạt nào
nhắc tới ranking, không route/schema ranking nào lộ ra, bốn bảng Phase 2 vẫn
đúng hình dạng. `ranking_runs`/`ranking_scores` vẫn rỗng.

---

# Đợt 2026-08-12 (c) — Phase 5 hotfix: khoá hàng ở tầng danh tính nguồn

Phạm vi: **chỉ sửa lỗi đồng thời**. Không migration, không đổi schema, không đổi
hợp đồng payload, không động cơ xếp hạng, không worker, không cò kích hoạt, không
nguồn khảo sát. Backend Alembic head giữ nguyên `0015_ranking_results` (15 file
revision); Mini CRM giữ nguyên `0002_minicrm_crud`.

**Phase 5 chuyển từ `PARTIAL / FAIL` sang `IMPLEMENTED`.** Cổng đồng thời ĐẠT.

## Khiếm khuyết đã tái hiện

Đợt trước (2026-08-12 (b)) tìm ra và ghi lại. Tái hiện lại ở đây làm mốc:

```text
đang giữ: revision 5
lô A revision 7   đọc 5  →  "newer"  →  update
lô B revision 6   đọc 5  →  "newer"  →  update      ← lẽ ra phải skip_stale
lô nào COMMIT SAU thì thắng  ⇒  revision 6 ghi đè revision 7
conflict_count = 0 · rows_failed = 0 · cả hai lô trả 202
```

Điều tệ nhất không phải mất một bản cập nhật, mà là **không ai biết đã mất**. Cơ
chế phát hiện đụng độ cũng không cứu được: nó dựa trên việc ĐỌC ĐƯỢC bản đang giữ,
mà ở đây cả hai đọc trúng cùng một bản đã cũ nên không bên nào nhìn thấy bên kia.

## Nguyên nhân gốc

`src/services/source_identity.py::_load()` đọc `crm_source_records` bằng một câu
`SELECT` thường. Toàn bộ bảng quyết định (`insert` / `update` / `skip_stale` /
`duplicate_noop` / `conflict` / `tombstone`) là một trình tự **đọc → so → ghi**, và
một trình tự như thế chỉ đúng khi không ai chen vào giữa. Tầng chiếu miền cũng
không có điều kiện phiên bản nào để vá lại sự bất nhất đó.

## Bản vá đã chọn

**Khoá bi quan ở đúng chỗ đang quyết định**, cộng ba việc phụ mà cuộc đua lôi ra:

1. **`_load()` → `SELECT ... FOR UPDATE`.** Khoá được giữ tới COMMIT của
   transaction NGOÀI, nên quyết định danh tính và việc chiếu xuống `units`/`deals`
   cùng nằm trong một khoảng không ai đọc chen được.
2. **`lock_identities()` — khoá TRƯỚC theo thứ tự tất định.** Nếu mỗi bản ghi tự
   khoá khi tới lượt thì thứ tự khoá bằng thứ tự bản ghi trong phong bì, và hai lô
   chứa cùng hai bản ghi theo hai thứ tự ngược nhau (`[A,B]` và `[B,A]`) sẽ khoá
   chéo nhau rồi deadlock. Hợp đồng KHÔNG quy định thứ tự bản ghi, nên không được
   trông cậy vào việc hai hệ nguồn tình cờ xếp giống nhau. `ORDER BY
   source_record_id` đặt nút `LockRows` lên trên nút `Sort`, nên mọi transaction đi
   qua các dòng theo cùng một hướng. Bước này KHÔNG đổi thứ tự XỬ LÝ — `json_path`
   và thứ tự lỗi trả về giữ nguyên. Nó nằm NGOÀI SAVEPOINT của từng bản ghi, vì một
   SAVEPOINT bị cuộn lại sẽ nhả những khoá lấy được bên trong nó.
3. **Cuộc đua CHÈN LẦN ĐẦU: `ON CONFLICT DO NOTHING RETURNING id`.** `FOR UPDATE`
   không khoá được một dòng CHƯA TỒN TẠI, nên khoá hàng không che được cuộc đua
   này. Không có bản vá, bên thua nhận `IntegrityError`, rơi vào cái bẫy chung ở
   `apply_records` và bị ghi nhận thành `CONSTRAINT_VIOLATION` — an toàn (không
   nhân bản) nhưng SAI, vì bên thua có thể là bên mang phiên bản CAO HƠN.

   Bản đầu tiên dùng `try/except IntegrityError` quanh một SAVEPOINT riêng. Nó
   **vẫn hỏng**, và cách sửa cuối cùng là bỏ hẳn exception khỏi thiết kế: để
   PostgreSQL xử lý đụng độ bên trong câu lệnh. Có dòng trả về ⇒ ta chèn được;
   không có dòng ⇒ đọc lại (và `FOR UPDATE` sẽ CHỜ transaction kia commit) rồi đi
   tiếp đường so phiên bản bình thường. Không exception nào đi qua ranh giới
   SAVEPOINT thì không phải trả lời câu hỏi "session còn dùng được tới mức nào" —
   câu trả lời đó phụ thuộc phiên bản thư viện chứ không phụ thuộc mã ở đây.
   `DO NOTHING` chỉ định ĐÍCH DANH `uq_crm_source_records_identity`; dạng trần sẽ
   nuốt mọi vi phạm ràng buộc, kể cả những vi phạm thật sự cần nổ ra.
4. **Hai chốt thứ tự mốc thời gian.** Cuộc đua lôi ra một lớp lỗi thứ hai mà không
   ai đoán trước: `now` được chốt một lần cho cả lô, nên hai lô song song mang hai
   mốc lệch nhau vài mili giây **theo chiều bất kỳ**. Lô ghi sau có thể mang mốc
   SỚM hơn lô ghi trước, và khi đó:

   * `ck_crm_source_records_seen_order` (`last_seen_at >= first_seen_at`) nổ;
   * `ck_units_updated_after_created` (`updated_at >= created_at`) nổ ở nhánh
     `DO UPDATE`, vì `created_at` giữ giá trị của dòng ĐANG CÓ.

   Sửa: kéo `moment` lên ít nhất bằng `first_seen_at` ở đường thua-cuộc-đua, và
   dùng `GREATEST(excluded.updated_at, <bảng>.created_at)` ở `_upsert` và
   `_tombstone`. **Không phải bịa ra thời gian** — chỉ từ chối ghi một thứ tự bất
   khả. Cả hai mốc đều là mốc của PHÍA NHẬN; thứ tự sự kiện ở hệ nguồn vẫn hoàn
   toàn do `source_revision` quyết định.

**KHÔNG thêm điều kiện phiên bản vào `ON CONFLICT DO UPDATE` của bảng nghiệp vụ.**
Sau khi phép so được tuần tự hoá, nó là thừa; và nó sẽ để `crm_source_records`
lệch khỏi `units` — bản sao và sổ danh tính kể hai câu chuyện khác nhau, tệ hơn
hẳn vấn đề nó định giải.

## Bộ test trước → sau

| | Trước hotfix | Sau hotfix |
|---|---|---|
| `test_sync_concurrency.py` | 9 test · **8 passed, 1 xfail(strict)** | **22 test · 22 passed, 0 xfail** |
| Backend toàn bộ | 1035 passed, 1 skipped, **1 xfailed** | **1049 passed, 1 skipped, 0 xfailed** |
| Mini CRM (có DB test) | 218 passed | **220 passed** |
| E2E container thật | 76 passed | **78 passed** |

**`xfail` đã được gỡ, không phải được nới.** Test khẳng định hành vi ĐÚNG
(`phiên bản cao nhất sống sót`) nay PASS vì lý do đúng của nó. Test ghi lại hành
vi SAI (`cả hai lô cùng update`) đã được **thay thế** bằng test khẳng định hành vi
đúng (`lô cũ nhận skip_stale`). Không assertion nào bị làm yếu đi.

Ở phía Mini CRM, `test_real_failure_windows.py` trước đây chỉ dám khẳng định
"hội tụ về MỘT TRONG HAI phiên bản" vì khẳng định mạnh hơn khi đó chớp tắt. Nay nó
khẳng định thẳng: **phiên bản CAO NHẤT thắng, bất kể lô nào tới trước.**

Bộ ghim cuộc đua cũng phải viết lại. Bản cũ đặt `asyncio.Barrier` ngay sau
`_load()`; sau khi có khoá thì cách đó **deadlock theo đúng thiết kế** — lô thứ
nhất giữ khoá rồi đứng chờ ở barrier, lô thứ hai chặn ở tầng database và không bao
giờ tới được barrier. Bản mới đi theo ngữ nghĩa của khoá: lô A lấy khoá → phát tín
hiệu → GIỮ khoá một nhịp → commit; lô B chỉ khởi động sau tín hiệu và phải chặn.
Nhờ vậy **việc lô B phải CHỜ trở thành một điều khẳng định được** — thời gian chạy
của nó không thể ngắn hơn khoảng A giữ khoá. Thiếu khẳng định đó, mọi test còn lại
vẫn xanh trên một hệ thống hoàn toàn không khoá gì.

## Lệnh và kết quả THẬT

| Lệnh | Kết quả |
|---|---|
| `TEST_TARGET=tests/test_services/test_sync_concurrency.py bash scripts/test_db.sh` | **PASS** — 22 passed |
| Lặp lại đúng lệnh trên **10 lần liên tiếp** | **PASS** — 22 passed cả 10 lần, không một lần chớp tắt |
| `TEST_TARGET=tests/test_services/test_source_identity.py …` | **PASS** — 20 passed |
| `TEST_TARGET=tests/test_services/test_domain_projection.py …` | **PASS** — 40 passed |
| `TEST_TARGET=tests/test_services/test_history_guard.py …` | **PASS** — 24 passed |
| `TEST_TARGET=tests/test_api/test_seeded_dashboard.py …` | **PASS** — 14 passed |
| `TEST_TARGET=tests/test_jobs/test_excel_to_database.py …` | **PASS** — 8 passed |
| `TEST_TARGET=tests/test_services/test_import_records.py …` | **PASS** — 41 passed |
| `TEST_TARGET=tests/test_ranking_boundary.py …` | **PASS** — 15 passed |
| `TEST_TARGET=tests bash scripts/test_db.sh` **×3** | **PASS** — 1049 passed, 1 skipped, cả 3 lần giống nhau |
| `cd minicrm && MINICRM_TEST_DATABASE_URL=… pytest -q` | **PASS** — 220 passed |
| `pytest minicrm/tests/test_real_*.py` | **PASS** — 78 passed (container thật) |
| `pytest minicrm/tests/test_real_failure_windows.py` ×3 | **PASS** — 30 passed cả 3 lần |
| `docker compose exec api alembic current` | **PASS** — `0015_ranking_results (head)` |
| `ruff check src/ tests/ minicrm/` | **PASS** — All checks passed |

**SKIP, báo cáo đúng là SKIP:** `tests/test_scheduler.py` — thiếu `apscheduler` ở
máy cục bộ (chỉ có trong image). Không đổi so với Phase 1–5.

## Kiểm chứng toàn vẹn dữ liệu

Đọc trên database ĐANG CHẠY sau toàn bộ các cuộc đua:

```text
live_units_mismatched = 0      units còn sống khớp revision với crm_source_records
live_deals_mismatched = 0      deals còn sống khớp revision với crm_source_records
dup_identities        = 0      không danh tính nào bị nhân bản
duplicate domain rows = 0      không căn nào bị nhân bản
conflicts_recorded    = 0      không đụng độ nào bị bỏ sót (và cũng không có đụng độ thật)
ranking_runs          = 0      Phase 6 chưa bắt đầu
ranking_scores        = 0
published ranking_configs = 1  vẫn đúng cấu hình hạt giống v1 của Phase 2
```

25 dòng `units` ĐÃ TOMBSTONE có `source_revision` thấp hơn `crm_source_records`.
Đó là hành vi CÓ SẴN và ĐÚNG, không phải hệ quả của bản vá: `_tombstone()` chỉ đặt
`deleted_at`/`updated_at`, còn `units.source_revision` mang phiên bản của lần
upsert NỘI DUNG cuối cùng. `crm_source_records` mới là sổ phiên bản. Ghi lại ở đây
để lần sau không ai đọc con số đó thành một sự bất nhất.

## Ranh giới Phase 6 — KHÔNG bắt đầu

`src/ranking/` không tồn tại. Không module nào ghi hay import bốn bảng xếp hạng.
Không job, không hàng đợi, không cò kích hoạt sau COMMIT, không endpoint, không
model phản hồi. `tests/test_ranking_boundary.py` (15 test, không cần DB) canh toàn
bộ những điều đó và vẫn xanh sau bản vá.

Hành vi hiện tại, phát biểu đúng như nó là:

```text
Mini CRM cập nhật  →  backend chiếu units/deals  →  DỪNG. Chưa có lô xếp hạng nào.
```

## Còn nợ sau hotfix

* **Nguồn dữ liệu khảo sát — chưa ai được giao.** Vẫn là hạng mục nghiêm trọng
  nhất còn mở, và hotfix này không đụng tới nó.
* **Từ vựng trạng thái căn — `UNKNOWN`.** Backend không có bảng alias cho căn.
* **`units.listed_at` / `days_on_market` / giá — BLOCKED** bởi hợp đồng v1.
* **`sync_mode=full_snapshot` chưa có hệ nguồn nào dùng.**
* **Mini CRM vẫn là hạ tầng TỔNG HỢP do chính dự án viết.** Nó vừa giúp tìm ra một
  khiếm khuyết thật ở tầng nhận — điều đó nói lên giá trị của nó như một CÔNG CỤ
  KIỂM, và không nói gì thêm về việc một CRM có thật sẽ gửi đúng hình dạng này.
* **Chi phí của khoá chưa được đo dưới tải.** Hai lô cho cùng một bản ghi giờ phải
  xếp hàng. Ở quy mô pilot (vài trăm bản ghi mỗi lô, một hệ nguồn) điều đó không
  đáng kể, nhưng con số đó chưa được đo, nên đừng khẳng định nó rẻ.

---

# Đợt 2026-08-12 (b) — Phase 5: gia cố, và một khiếm khuyết THẬT được tìm ra

Phạm vi: **chỉ TEST**. Không migration mới, không bảng mới, không endpoint mới,
không động cơ xếp hạng, không worker, không cò kích hoạt, không nguồn khảo sát.
Backend Alembic head giữ nguyên `0015_ranking_results`; Mini CRM giữ nguyên
`0002_minicrm_crud`.

Hai file mã nguồn duy nhất bị sửa, và cả hai là để **sửa lỗi Phase 5 tự tìm ra**:
`minicrm/app/crud.py` (xem khiếm khuyết 1 dưới đây).

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| Hợp đồng: phép canh SHA-256 tự nó có bắt được trôi không | UNKNOWN | **VERIFIED** — 4 test mới, gồm một lần làm trôi thật |
| Kiểm hợp đồng HAI PHÍA trên cùng một phong bì | UNKNOWN | **VERIFIED** — 6 test mới, cả hợp lệ lẫn hỏng |
| Sửa đồng thời một bản ghi Mini CRM | UNKNOWN | **VERIFIED** — dãy phiên bản đúng sau khi sửa lỗi |
| Gửi lại đồng thời cùng một lô | UNKNOWN | **VERIFIED** — không có bản chiếu thứ hai |
| Lô CŨ tới SAU lô mới (out-of-order thật) | UNKNOWN | **VERIFIED** — `skip_stale`, không ghi đè |
| Backend tắt giữa lúc ghi | Kiểm ở Phase 4 | **VERIFIED lại**, kèm phục hồi bằng gửi lại tường minh |
| Khởi động lại Mini CRM + database của nó | UNKNOWN | **VERIFIED** — dữ liệu, sổ gửi đi, Alembic đều còn |
| Khởi động lại backend | UNKNOWN | **VERIFIED** — tính bất biến của lô nằm ở DB, không ở bộ nhớ |
| **Hai lô đồng bộ SONG SONG cho cùng một bản ghi** | UNKNOWN | **BROKEN — khiếm khuyết đã xác nhận, xem dưới** |
| Ranh giới Phase 6 (không có xếp hạng) | Chỉ là một câu trong tài liệu | **VERIFIED** — 15 test kiểm được |
| Động cơ / worker / cò xếp hạng | NOT IMPLEMENTED | **VẪN NOT IMPLEMENTED** (Phase 6) |
| Nguồn dữ liệu khảo sát | NOT IMPLEMENTED | **VẪN NOT IMPLEMENTED — chưa ai được giao** |
| **Tương thích với CRM THẬT của khách hàng** | BLOCKED | **VẪN BLOCKED** |

Bằng chứng đầu-cuối của Mini CRM: **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY**.

## ⚠ Hai khiếm khuyết Phase 5 tìm ra

Đây là phần đáng giá nhất của cả phase. Cả hai đều **im lặng** — không có lỗi nào
phát ra, không có test cũ nào đỏ, và cả hai chỉ lộ ra khi có hai thứ chạy cùng lúc.

### Khiếm khuyết 1 — Mini CRM báo cáo sai bản ghi mình vừa ghi (ĐÃ SỬA)

Mọi thao tác ghi ĐỌC LẠI dòng sau khi commit rồi trả kết quả đọc đó về. Với một
request đơn lẻ thì không phân biệt được. Với hai `PATCH` song song thì request thứ
nhất commit ở revision 2, đọc lại, và thấy revision 3 do request thứ hai vừa ghi —
nên nó khai **revision 3** trong khi lô nó vừa gửi mang **revision 2**. `record` và
`sync` trong cùng một thân phản hồi nói về hai sự việc khác nhau, và không có gì
trong phản hồi cho biết điều đó.

**Sửa:** trả về dòng lấy từ `RETURNING` ngay trong transaction (`_written()` ở
`app/crud.py`), kèm dấu mirrored suy từ kết quả đẩy CỦA CHÍNH lời gọi đó. Xoá mềm
cũng chuyển sang `RETURNING` thay vì đọc lại. Ba test khoá bất biến này lại ở mức
một request, nơi nó tất định và rẻ.

### Khiếm khuyết 2 — Backend: hai lô song song, lô CŨ HƠN thắng (CHƯA SỬA, cố ý)

`SourceIdentityService._load()` đọc `crm_source_records` bằng một câu `SELECT`
thường — không `FOR UPDATE`, và câu ghi ở `DomainProjector._upsert` không có điều
kiện phiên bản. Trình tự read-modify-write vì thế **không được tuần tự hoá**:

```text
lô A (revision 7)   đọc stored=5  →  "newer"  →  update
lô B (revision 6)   đọc stored=5  →  "newer"  →  update     ← lẽ ra phải skip_stale
lô nào COMMIT SAU thì thắng, kể cả khi nó mang phiên bản THẤP hơn
```

**Hậu quả:** `units` giữ trạng thái của revision 6 trong khi revision 7 đã được
chấp nhận và đã trả `202` cho hệ nguồn. `rows_failed = 0` ở cả hai lô,
`conflict_count` vẫn `0`, và hệ nguồn **không có cách nào biết** bản cập nhật mới
của nó vừa bị một bản cũ nuốt mất. Đây đúng là điều kiện dừng "một payload cũ ghi
đè dữ liệu backend" của Phase 5.

**Bằng chứng, tất định:** `tests/test_services/test_sync_concurrency.py` ghim cuộc
đua bằng một `asyncio.Barrier` đặt ngay sau `_load()` (cả hai lô chắc chắn đọc
xong trước khi bên nào ghi), rồi cho lô cũ hơn commit sau. Bản vá trong test chỉ
THÊM `await` — không đổi truy vấn, không đổi quyết định. Chạy 3 lần liên tiếp cho
cùng một kết quả.

**KHÔNG sửa ở Phase 5**, vì phạm vi Phase 5 cấm đổi hành vi tầng nhận, và vì hai
hướng sửa có đánh đổi khác nhau cần người quyết định:

1. `SELECT ... FOR UPDATE` trong `_load()` — tuần tự hoá đúng chỗ đang quyết định.
   Đúng nhất; trả giá bằng việc hai lô cho cùng một bản ghi phải xếp hàng.
2. Điều kiện phiên bản trong `ON CONFLICT DO UPDATE`
   (`WHERE excluded.source_revision > units.source_revision`) — bảo vệ bảng nghiệp
   vụ nhưng để `crm_source_records` lệch, nên bản sao và sổ danh tính kể hai câu
   chuyện khác nhau.

Khiếm khuyết được giữ trong bộ test ở **hai** dạng để nó không biến mất: một test
khẳng định hành vi SAI đang có (sẽ ĐỎ khi ai đó sửa xong — đó là tín hiệu chuyển
trạng thái, không phải hồi quy), và một test khẳng định hành vi ĐÚNG cần có, đang
`xfail(strict=True)`. **Không có SKIP nào bị đổi thành PASS, và không có chốt nào
bị nới lỏng.**

## Quyết định thiết kế đáng ghi lại

* **Cuộc đua được GHIM, không để may rủi.** Phiên bản đầu của phép thử đồng thời
  chạy qua container thật và cho kết quả khi đỏ khi xanh — hai lô chồng lấn nhau
  vài mili giây. Một test chớp tắt còn tệ hơn không có test, vì nó dạy người ta
  chạy lại. Bản cuối tách làm hai: một test tất định ở tầng service (barrier), và
  một test qua container chỉ khẳng định những gì đúng ở MỌI thứ tự.
* **Phép canh hợp đồng tự nó được kiểm.** Một test so hai file mà chưa bao giờ
  thấy chúng khác nhau thì chưa chứng minh được nó phát hiện được sự khác nhau.
  Bản sao bị làm trôi thật (trong `tmp_path`, không đụng file thật) rồi khẳng định
  phép so gãy — kể cả với một lần định dạng lại không đổi ngữ nghĩa.
* **Kiểm hợp đồng HAI PHÍA trên cùng một phong bì.** Sáu phong bì hỏng đi qua cả
  bộ kiểm của Mini CRM lẫn bộ kiểm của backend. Chỉ một phía bắt được nghĩa là
  phía kia đang chạy trên một hợp đồng khác — mà phép so SHA-256 sẽ không thấy gì,
  vì hai file vẫn giống nhau còn hành vi thì không.
* **Out-of-order được dựng bằng sự cố THẬT, không bằng `replay-stale`.** Đường
  `replay-stale` tự khai mình đang phát lại bản cũ. Ở kịch bản mới, lô cũ trở nên
  cũ một cách tự nhiên: nó hỏng lúc backend tắt, một lần sửa mới hơn đi qua trước,
  rồi lô cũ mới được gửi lại. Backend chưa từng thấy lô đó nên nó thật sự chạy tầng
  SO PHIÊN BẢN, khác hẳn một lần gửi lại thường (dừng ở tầng nhận diện lô).
* **Ranh giới Phase 6 thành khẳng định KIỂM ĐƯỢC.** `tests/test_ranking_boundary.py`
  không cần database: nó đọc mã nguồn và bảng định tuyến thật. Khi Phase 6 bắt đầu,
  đúng những test này phải ĐỎ — đó là tín hiệu chuyển phase.
* **`-p no:logging` đã bị bỏ khỏi lệnh chạy.** Nó tắt plugin cung cấp fixture
  `caplog` và biến 2 test của `test_history_guard.py` thành ERROR — một "hồi quy"
  do chính lệnh đo gây ra. Ghi lại vì nó suýt được báo cáo thành một hồi quy thật.

## Lệnh và kết quả THẬT

| Lệnh | Kết quả |
|---|---|
| `docker compose exec api alembic current` | **PASS** — `0015_ranking_results (head)` |
| `docker compose exec minicrm alembic current` | **PASS** — `0002_minicrm_crud (head)` |
| `sha256sum` cả hai bản schema | **PASS** — `e15fd9c5e685923fcf3f537c7dba4e900632ae7d6723df654e35b55efb49a92a` |
| `grep -rn "from src\." minicrm/` | **PASS** — không output |
| `grep -rn "minicrm" src/` | **PASS** — không output |
| `ruff check src/ tests/ minicrm/` | **PASS** — All checks passed |
| `TEST_TARGET=tests/test_services/test_domain_projection.py bash scripts/test_db.sh` | **PASS** — 40 passed |
| `TEST_TARGET=tests/test_services/test_source_identity.py bash scripts/test_db.sh` | **PASS** — 20 passed |
| `TEST_TARGET=tests/test_services/test_history_guard.py bash scripts/test_db.sh` | **PASS** — 24 passed |
| `TEST_TARGET=tests/test_api/test_seeded_dashboard.py bash scripts/test_db.sh` | **PASS** — 14 passed |
| `TEST_TARGET=tests/test_jobs/test_excel_to_database.py bash scripts/test_db.sh` | **PASS** — 8 passed |
| `TEST_TARGET=tests/test_services/test_import_records.py bash scripts/test_db.sh` | **PASS** — 41 passed |
| `TEST_TARGET=tests/test_services/test_sync_concurrency.py bash scripts/test_db.sh` (×3) | **PASS** — 8 passed, 1 xfailed, cả 3 lần giống nhau |
| `TEST_TARGET=tests/test_ranking_boundary.py bash scripts/test_db.sh` | **PASS** — 15 passed |
| `TEST_TARGET=tests bash scripts/test_db.sh` (×3) | **PASS** — **1035 passed, 1 skipped, 1 xfailed** |
| `pytest minicrm/tests/test_real_backend_sync.py` | **PASS** — 28 passed |
| `pytest minicrm/tests/test_real_failure_windows.py` | **PASS** — 28 passed |
| `pytest minicrm/tests/test_real_endpoints.py` | **PASS** — 20 passed |
| `cd minicrm && pytest -q` | **PASS** — **119 passed, 99 skipped** |
| `cd minicrm && MINICRM_TEST_DATABASE_URL=… pytest -q` (×2) | **PASS** — **218 passed** |

**Tất định:** backend `diff run_1 run_2` → **không khác biệt** (`1035 passed,
1 skipped, 1 xfailed` cả hai lần). Mini CRM `diff run_1 run_2` → **không khác biệt**
(`218 passed`). Thời gian chạy bị cắt khỏi phép so.

**SKIP, báo cáo đúng là SKIP:**

* `tests/test_scheduler.py` — 1 SKIP, thiếu `apscheduler` ở máy cục bộ (chỉ có
  trong image). Không đổi so với Phase 1–4.
* `cd minicrm && pytest -q` (không có biến môi trường): **99 SKIP** vì chốt an
  toàn từ chối mọi database không kết thúc bằng `_test`. Chốt này **không bị nới
  lỏng** để làm suite xanh hơn; lệnh có biến môi trường được ghi ngay bên trên.

## Bằng chứng cửa sổ hỏng

| Cửa sổ | Sự cố được TIÊM | Trạng thái cục bộ | Sổ gửi đi | Trạng thái backend | Phục hồi | Kết quả |
|---|---|---|---|---|---|---|
| Sửa đồng thời | 2 × `PATCH` cùng lúc | revision 2 và 3, đúng một dòng | 2 lô riêng | hội tụ về một trong hai, đúng một dòng, sổ danh tính khớp bản sao | — | PASS |
| Gửi lại đồng thời | 2 × `resend` cùng lô | không đổi | **một** dòng, `attempts ≥ 3` | **một** `upload_files`, không có bản chiếu thứ hai | — | PASS |
| Backend tắt | `docker compose stop api` | commit ở revision 2, `mirrored_revision = 1` | `http_status NULL`, `sent_at NULL`, `attempts 1`, `last_error` có | **0** lô — không byte nào tới nơi | `start api` + `resend` tường minh | PASS |
| Lô cũ tới muộn | lô rev 2 gửi lại SAU khi rev 3 đã qua | revision 3 | `attempts 2`, `http 202`, `last_error` được xoá | `skip_stale = 1`, `untouched = 1`, **không ghi đè** | — | PASS |
| Khởi động lại Mini CRM | `restart minicrm_db minicrm` | dòng `crm_units` y nguyên từng trường | payload y nguyên, `attempts` y nguyên | — | `resend` sau khởi động lại → `insert = 1` | PASS |
| Khởi động lại backend | `restart api` | — | — | lô cũ vẫn được nhận ra: `replayed`, **cùng** `sync_run_id`, vẫn một `upload_files` | — | PASS |
| Hai lô song song ở BACKEND | `asyncio.Barrier` ghim thứ tự đọc/ghi | — | — | **lô revision 6 ghi đè revision 7**, `conflict_count = 0`, cả hai lô `completed` | không có | **KHIẾM KHUYẾT** |
| Trôi hợp đồng | sửa một ký tự trong bản sao (ở `tmp_path`) | — | — | — | — | PASS — phép so SHA-256 gãy đúng như phải gãy |

## Ranh giới xếp hạng (Phase 6)

Kiểm trên hệ ĐANG CHẠY sau toàn bộ Phase 5:

```text
ranking_runs      = 0
ranking_scores    = 0
feature_snapshots = 0
ranking_configs   = 1  (đúng một bản published, chính là cấu hình hạt giống v1 của Phase 2)
```

`tests/test_ranking_boundary.py` (15 test, không cần DB) khẳng định thêm:
`src/ranking/` không tồn tại; không module nào ghi hay import bốn bảng xếp hạng;
`src/jobs/`, `task_queue.py`, `worker.py`, `scheduler.py` không nhắc tới xếp hạng;
`api/sync.py`, `sync_runs.py`, `domain_projection.py` không nhắc tới xếp hạng;
bảng định tuyến thật không có endpoint nào; `schemas.py` không khai model nào; và
bốn bảng vẫn giữ đủ cột mà Phase 6 sẽ cần.

**Hành vi hiện tại, phát biểu đúng như nó là:**

```text
Mini CRM cập nhật  →  backend chiếu units/deals  →  DỪNG. Chưa có lô xếp hạng nào.
```

Không có test nào ở đây bị chặn bởi mã Phase 6 còn thiếu: chúng kiểm sự VẮNG MẶT
của mã đó, nên chúng chạy được ngay bây giờ. Việc tính điểm, tính đặc trưng, cò
kích hoạt sau COMMIT và API đọc kết quả — toàn bộ nằm ở Phase 6.

## Còn nợ sau Phase 5 (KHÔNG được coi là đã xong)

* **KHIẾM KHUYẾT ĐỒNG THỜI Ở TẦNG NHẬN — chưa sửa.** Xem trên. Phải sửa trước khi
  có bất kỳ hệ nguồn nào đẩy song song, và trước Phase 6 nếu điểm xếp hạng sẽ được
  tính từ `units`/`deals`.
* **Nguồn dữ liệu khảo sát — chưa ai được giao.** Vẫn là hạng mục nghiêm trọng
  nhất còn mở. Phase 5 không đụng tới nó.
* **Từ vựng trạng thái căn — `UNKNOWN`.** Backend không có bảng alias cho căn.
  Một CRM thật phát `con_trong` vẫn mất toàn bộ bản ghi căn ngay lô đầu.
* **`units.listed_at` / `days_on_market` / giá — BLOCKED.**
* **`sync_mode=full_snapshot` chưa có hệ nguồn nào dùng.** Mini CRM CRUD chỉ phát
  `incremental`; đường tombstone theo ảnh chụp vẫn chỉ có fixture kiểm.
* **Mini CRM vẫn là hạ tầng TỔNG HỢP do chính dự án viết.** 218 test xanh, hai lần
  khởi động lại container, và một khiếm khuyết thật được tìm ra — không điều nào
  trong số đó nói được rằng một CRM có thật sẽ gửi đúng hình dạng này.

---

# Đợt 2026-08-12 — Phase 4: CRUD Mini CRM và cò đẩy tự động sau commit

Phạm vi: 14 endpoint CRUD/outbox trong `minicrm/`, migration `0002_minicrm_crud`,
bản sao hợp đồng `minicrm/contracts/`, và một bộ kiểm đầu-cuối chạy qua **container
thật, HTTP thật, hai database thật**.

**Không** động cơ xếp hạng, **không** worker xếp hạng, **không** khảo sát,
**không** UI, **không** khách hàng/giá/PII. Backend Alembic head giữ nguyên
`0015_ranking_results`.

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| CRUD căn hộ ở Mini CRM | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| CRUD giao dịch ở Mini CRM | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Cò đẩy tự động sau khi commit | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Sổ gửi đi: tra / gửi lại / phát lại bản cũ | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Kiểm hợp đồng ở PHÍA NGUỒN, trước khi gửi | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Vòng CRUD → HTTP → chiếu miền, kiểm bằng container thật | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Động cơ xếp hạng / worker xếp hạng | NOT IMPLEMENTED | **VẪN NOT IMPLEMENTED** (Phase 6) |
| Nguồn dữ liệu khảo sát | NOT IMPLEMENTED | **VẪN NOT IMPLEMENTED — chưa ai được giao** |
| **Khả năng tương thích với CRM THẬT của khách hàng** | BLOCKED | **VẪN BLOCKED** — xem cảnh báo dưới |

## ⚠ Mini CRM này KHÔNG phải bằng chứng tương thích — và ở Phase 4 rủi ro LỚN HƠN

Mini CRM là hạ tầng TỔNG HỢP **do chính dự án này viết**, theo đúng **cách chính
dự án này đọc** hợp đồng v1. Vòng HTTP xanh ở đây chứng minh **đúng một điều**:
hợp đồng v1 và phía nhận hiện tại khớp nhau. Nó **không** nói gì về việc một CRM
có thật sẽ phát ra hình dạng nào, dùng từ vựng trạng thái nào, hay có cấp được
`source_revision` đơn điệu hay không.

Rủi ro ở Phase 4 **lớn hơn** Phase 3, không nhỏ đi: một hệ thống có CRUD đầy đủ,
sổ gửi đi, nút gửi lại và 28 test đầu-cuối xanh trông thuyết phục hơn hẳn một file
JSON tĩnh — trong khi giá trị làm bằng chứng của nó **không đổi**. Vì thế nhãn
tiếp tục được gắn vào chính SẢN PHẨM: `/health` trả `disclaimer`, mọi phong bì
mang `_comment` tự khai là tổng hợp, và
`docs/crm/activation_prerequisites.md` giữ mục Mini CRM ở trạng thái ⬜.

## Quyết định thiết kế đáng ghi lại

* **Một thứ tự, không ngoại lệ: GHI → COMMIT → GỬI.** Dòng `crm_outbox` được ghi
  bằng CHÍNH session của thao tác CRUD, nên nó cùng sống hoặc cùng chết với thay
  đổi nghiệp vụ. Tách ra hai transaction sẽ mở hai lỗ hổng đối xứng: commit dữ liệu
  rồi chết trước khi ghi outbox (thay đổi tồn tại mà không ai biết phải gửi), hoặc
  ghi outbox rồi rollback dữ liệu (gửi đi một trạng thái chưa từng tồn tại).
* **Phong bì dựng từ DÒNG ĐÃ GHI, không từ thân request.** Đây là thứ khiến chốt
  A4 đúng mà không cần ai nhớ tới nó: `PATCH {"deal_status":"sold","sold_at":…}`
  chỉ mang hai trường, nhưng phong bì dựng lại từ `crm_deals` sau khi ghi nên
  `reserved_at` cũ đi kèm tự nhiên. Dựng từ thân request thì backend từ chối bằng
  `HISTORY_TIMESTAMP_DROPPED`, và ai đó sẽ "sửa" bằng cách khai `partial` — che
  mất đúng cái giới hạn mà A4 sinh ra để phơi bày.
* **Kiểm hợp đồng nằm TRONG transaction, gửi nằm NGOÀI.** Phong bì hỏng ⇒ rollback
  ⇒ không tồn tại bản ghi đã commit nào mà Mini CRM không diễn đạt nổi thành payload
  hợp lệ. Gửi trong transaction thì backend có thể đã chiếu xong một bản ghi mà
  giây sau Mini CRM rollback — không lấy lại được.
* **Đẩy hỏng KHÔNG làm hỏng request CRUD.** Trả 201/200 kèm `sync.status =
  sync_failed`/`sync_pending`. Trả 502 cho một request đã ghi thành công sẽ khiến
  người gọi thử lại và tạo ra căn thứ hai cho cùng một căn hộ.
* **`sync_failed` và `sync_pending` là hai thứ khác nhau.** `sync_failed` = chắc
  chắn không có tác dụng gì ở backend (backend từ chối, hoặc kết nối chưa bao giờ
  mở được). `sync_pending` = request đã đi nhưng không có phản hồi — thật sự không
  biết. Gộp lại thành một chữ "lỗi" sẽ khiến người vận hành gửi lại một lô có thể
  đã tới nơi, hoặc ngồi chờ một lô chắc chắn sẽ không bao giờ tới.
* **Giao dịch bị TỪ CHỐI TRƯỚC KHI GHI nếu căn chưa lên tới backend** (409
  `UNIT_NOT_MIRRORED`), chứ không phải cho tạo rồi chặn ở bước gửi. Cách sau tạo
  ra một bản ghi đã commit KHÔNG có dòng outbox nào — nên không có gì để `resend`.
  Đó đúng là "thay đổi đã commit bị âm thầm bỏ rơi" mà Phase 4 cấm.
* **`resend` và `replay-stale` KHÔNG được lẫn vào nhau.** `resend` dùng ĐÚNG batch
  id cũ ⇒ backend trả kết quả đã lưu (`replayed=true`) và dừng ở tầng nhận diện lô.
  `replay-stale` bắt buộc dùng batch id MỚI với payload và `source_revision` CŨ ⇒
  backend mới thật sự chạy tầng so phiên bản và cho `skip_stale`. Dùng lại batch id
  cũ cho đường thứ hai sẽ khiến phép thử `skip_stale` luôn "đạt" mà chẳng kiểm gì.
* **`replay-stale` từ chối một lô KHÔNG cũ thật** (409 `BATCH_NOT_STALE`). Một
  phép thử tự khẳng định kết quả mà không kiểm điều kiện tiền đề thì chứng minh
  được số không.
* **`external_id` sinh từ SEQUENCE của PostgreSQL**, không từ `max()+1`. Sequence
  không lùi kể cả khi transaction gọi nó bị rollback. Vài id bị bỏ phí là cái giá,
  và nó rẻ: id bỏ phí vô hại, id dùng lại thì gắn lịch sử của một căn đã xoá vào
  một căn khác — hỏng vĩnh viễn và không phát hiện được từ phía nhận (giả định A1).
* **Mini CRM giữ BẢN SAO schema (`minicrm/contracts/`), không import bản của
  backend.** Hai lý do: `src/` không tồn tại trong image của Mini CRM (build context
  là `./minicrm`), và quan trọng hơn — nếu Mini CRM kiểm bằng chính đối tượng mà
  backend dùng để chấm điểm nó thì nó sẽ luôn tự cho mình là đúng, và bộ kiểm hợp
  đồng phía nhận mất hết ý nghĩa. Rủi ro lệch bản được đóng bằng **so SHA-256 ở
  tầng test** (`tests/test_contract_copy.py`), không bằng một lời hứa.
* **Bộ kiểm phía nguồn NGHIÊM hơn schema, và chỉ theo chiều an toàn.** Nó chặn
  `unit_status`/`deal_status` ngoài từ vựng backend, lô rỗng, `external_id` trùng
  trong một lô, `sold_at < reserved_at`. Chặn nhiều hơn chỉ khiến một payload đáng
  ngờ không rời khỏi máy. **Backend vẫn là căn cứ tương thích CUỐI CÙNG.**
* **`FakeBackend` trong test đơn vị cố ý NGU.** Nó biết đúng một luật (cùng batch
  id ⇒ `replayed=true`) và KHÔNG mô phỏng tầng so phiên bản. Viết một bản sao
  `SourceIdentityService` vào file test sẽ tạo ra một phép thử tự chấm điểm mình:
  mọi kết luận về `skip_stale`/`conflict` sẽ là kết luận về bản sao trong test.

## Lệnh và kết quả THẬT

**Command:** `docker compose up -d --build minicrm_db minicrm`
**Result:** PASS — image `minicrm-synthetic:dev` dựng lại, `minicrm` healthy ở :8100

**Command:** `docker compose exec minicrm alembic -c alembic.ini upgrade head`
(qua `MINICRM_RUN_MIGRATIONS=true` ở entrypoint)
**Result:** PASS — `0001_minicrm_initial -> 0002_minicrm_crud`

**Command:** `pytest minicrm/tests/test_real_backend_sync.py -q`
**Result:** PASS — **28 passed** (container thật, HTTP thật, hai database thật)

**Command:** `cd minicrm && pytest -q`
**Result:** PASS — **63 passed, 96 skipped**.
96 SKIP là các test cần DB thật; chúng bỏ qua khi không có
`MINICRM_TEST_DATABASE_URL`. **Đây là SKIP, không phải PASS.**

**Command:** `cd minicrm && MINICRM_TEST_DATABASE_URL=postgresql+asyncpg://minicrm:minicrm@localhost:5433/minicrm_test pytest -q`
**Result:** PASS — **159 passed**

**Command:** `TEST_TARGET=<từng file> bash scripts/test_db.sh`
**Result:** PASS — `test_domain_projection` 40 · `test_source_identity` 20 ·
`test_history_guard` 24 · `test_seeded_dashboard` 14 · `test_excel_to_database` 8 ·
`test_import_records` 41

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh`
**Result:** PASS — **1012 passed, 1 skipped** trong 386.38s.
SKIP là `tests/test_scheduler.py` (thiếu `apscheduler` ở máy cục bộ; có trong
image). **Đây là SKIP, không phải PASS** — không đổi so với Phase 1–3.

**Command:** `ruff check src/ tests/ minicrm/`
**Result:** PASS — All checks passed!

**Command:** `sha256sum src/contracts/crm_sync_v1.schema.json minicrm/contracts/crm_sync_v1.schema.json`
**Result:** PASS — trùng khớp
`e15fd9c5e685923fcf3f537c7dba4e900632ae7d6723df654e35b55efb49a92a`

## Kịch bản đầu-cuối, chạy qua container THẬT

| # | Kịch bản | Kết quả THẬT của backend |
|---|---|---|
| 1 | `POST /units` | HTTP 202 · `decisions.insert=1` · `projections.inserted=1` · đúng **một** dòng `units` |
| 2 | `PATCH /units/{id}` | `decisions.update=1` · vẫn **một** dòng, `source_revision=2` |
| 3 | `POST /deals` sau khi có căn | `decisions.insert=1` · giải đúng `unit_id` qua `(source_instance_id, external_unit_id)` |
| 4 | `POST /deals` khi CHƯA có căn | **422 cục bộ**, `sent=false`, **không** dòng outbox mới — không byte nào rời máy |
| 5 | `reserved → sold` | `decisions.update=1` · backend giữ **cả** `reserved_at` lẫn `sold_at` (chốt A4) |
| 6 | `POST /outbox/{id}/resend` | HTTP **200**, `replayed=true`, **cùng** `sync_run_id`, số dòng `units` KHÔNG đổi, `upload_files` vẫn 1 lô |
| 7 | `POST /outbox/replay-stale` | HTTP 202, `decisions.skip_stale=1`, `projections.untouched=1`, trạng thái backend **không đổi** |
| 8 | `DELETE /deals/{id}` | `decisions.tombstone=1` · dòng vẫn còn, `deleted_at` được đặt (xoá MỀM) |
| 9 | `DELETE /units/{id}` | `decisions.tombstone=1` · `projections.tombstoned=1` · biến mất khỏi đường đọc `deleted_at IS NULL`; `crm_source_records.state='tombstoned'` |
| 10 | **Tắt hẳn container `api`**, rồi `POST /units` | HTTP **201** cục bộ · `sync.status=sync_failed`, `http_status=null` · dòng `crm_units` **còn nguyên**, `mirrored_revision IS NULL` |
| 11 | Bật lại `api`, `POST /outbox/{id}/resend` | HTTP 202 · `decisions.insert=1` · `mirrored_revision` khớp `source_revision` |

Kịch bản 10 là điểm đáng nói nhất: nó **tắt backend thật** giữa lúc ghi. Mock đã
kiểm điều này rồi, nhưng mock không chứng minh được rằng một thay đổi đã commit
sống sót qua một sự cố hạ tầng có thật.

## Kiểm chứng ở hai database

**Mini CRM (`minicrm_db`, cổng 5433):**

| Kiểm | Kết quả |
|---|---|
| `alembic_version` | `0002_minicrm_crud` |
| Bảng của backend rò vào | **0** |
| `crm_units` | 22 dòng, `external_id` từ `U-0001` liên tục, không id nào dùng lại |
| `crm_deals` | 11 dòng, `mirrored_revision = source_revision` ở mọi dòng |
| `crm_outbox` | mọi lô giữ payload + phản hồi nguyên văn; `attempts` đếm đúng số lần gửi; 11 dòng có `replay_of` |

**Backend (`db`, cổng 5432):**

| Kiểm | Kết quả |
|---|---|
| `alembic_version` | `0015_ranking_results` — **KHÔNG ĐỔI** |
| Số file revision | 15 — không file nào của backend bị sửa trong Phase 4 |
| Bảng `crm_units`/`crm_deals`/`crm_outbox` rò vào | **0** |
| `units` của `mini-crm-dev` | 23 dòng (22 của Phase 4 + `U-P3-0001` của Phase 3) |
| `deals` của `mini-crm-dev` | 11 dòng, `reserved_at` **còn nguyên** sau khi chuyển `sold` |
| `upload_files` của `mini_crm` | 100 lô, **toàn bộ** `completed` |
| `ranking_runs` | **0 — ĐÚNG NHƯ MONG ĐỢI.** Phase 4 không nối dây kích hoạt xếp hạng |

**Cô lập ở mức mã nguồn:**
`grep -rn "from src\." minicrm/` → **không output**
`grep -rn "minicrm" src/` → **không output**

## Ba lỗi TEST đã tự phát hiện và sửa

Ghi lại vì cả ba đều là loại lỗi khiến một phép thử xanh mà không kiểm được gì:

1. **Khẳng định trên `decisions` đầy đủ.** Backend trả về TOÀN BỘ từ vựng quyết
   định (phần lớn bằng 0); test so bằng `== {"insert": 1}` nên hỏng. Sửa: lọc các
   khoá bằng 0 rồi so trên phần có thật — test không còn hỏng vì backend thêm một
   loại quyết định mới, việc chẳng nói gì về tính tương thích.
2. **Đọc lại trạng thái ở CUỐI vòng thay vì chụp tại chỗ.** Ba test "hai bên khớp
   nhau" truy vấn DB lúc chạy test, tức là SAU khi cả vòng đã tombstone bản ghi —
   chúng khẳng định về một thời điểm khác với thời điểm chúng nhận là đang nói tới.
   Sửa: chụp cả hai phía ngay sau mỗi bước trong fixture.
3. **`test_migration_0001.py` chạy theo `head`.** Từ Phase 4 head là `0002`, nên
   một test về migration 0001 lặng lẽ đổi đối tượng nó đang kiểm. Sửa: PIN đúng
   `0001_minicrm_initial`.

## Còn nợ sau Phase 4 (KHÔNG được coi là đã xong)

* **Nguồn dữ liệu khảo sát — chưa ai được giao.** Vẫn là hạng mục nghiêm trọng
  nhất còn mở, và Phase 4 không đụng tới nó.
* **Từ vựng trạng thái căn — `UNKNOWN`.** Mini CRM phát đúng bốn giá trị tiếng Anh
  vì backend không có bảng alias cho căn. Một CRM thật phát `con_trong` vẫn sẽ mất
  toàn bộ bản ghi căn ngay lô đầu.
* **`units.listed_at` / `days_on_market` / giá — BLOCKED.** Hợp đồng v1 không chở
  chúng, và Mini CRM cố ý không có chúng để chở.
* **`sync_mode=full_snapshot` chưa được Mini CRM dùng.** CRUD chỉ phát
  `incremental`. Đường snapshot ở backend vẫn chỉ có fixture tổng hợp kiểm.
* **Chưa có cò kích hoạt tính lại xếp hạng sau COMMIT.** `ranking_runs` vẫn 0, và
  đó là trạng thái ĐÚNG cho tới Phase 6.

---

# Đợt 2026-08-11 (g) — Phase 3C: sync client và lần đẩy HTTP THẬT đầu tiên

Phạm vi: `minicrm/app/sync_client.py` + phép thử đầu-cuối qua HTTP thật.
**Không CRUD, không UI, không deals** — đó là Phase 4.

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| Sync client của Mini CRM | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Đường Mini CRM → backend qua HTTP thật | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| **Khả năng tương thích với CRM THẬT của khách hàng** | BLOCKED | **VẪN BLOCKED** — xem cảnh báo dưới |

## ⚠ Mini CRM này KHÔNG phải bằng chứng tương thích

Đây là hạ tầng TỔNG HỢP **do chính dự án này viết**, theo đúng cách chính dự án
này hiểu hợp đồng v1. Nó chứng minh **phía NHẬN cư xử đúng hợp đồng**; nó không
chứng minh — và không thể chứng minh — rằng một Mini CRM do bên khác xây sẽ gửi
đúng hình dạng này.

Rủi ro cụ thể: một Mini CRM có service riêng, database riêng, endpoint `/health`
và một lần đẩy HTTP thành công trông thuyết phục hơn hẳn một file JSON tĩnh. Vì
thế nhãn được gắn vào chính SẢN PHẨM, không để trong tài liệu: `/health` trả kèm
`disclaimer`, và mọi phong bì mang `_comment` tự khai là tổng hợp.
`docs/crm/activation_prerequisites.md` giữ mục Mini CRM ở trạng thái ⬜.

## Quyết định thiết kế đáng ghi lại

* **Client KHÔNG import validator của backend.** Nó dựng một dict rồi POST. Import
  validator vào đây sẽ khiến Mini CRM luôn tự cho mình là đúng, và bộ kiểm hợp
  đồng phía nhận mất hết ý nghĩa — payload sai sẽ không bao giờ bị bắt.
* **Ghi `crm_outbox` TRƯỚC khi gửi.** Gửi xong mới ghi thì một lần timeout để lại
  một lô đã tới backend mà Mini CRM không có vết gì.
* **Timeout KHÔNG ghi mã lỗi bịa ra.** Dòng outbox giữ `http_status = NULL`, đọc
  là "đã gửi, CHƯA biết kết quả" — và đó là sự thật: lô có thể đã tới nơi và đã
  được xử lý. Ghi 500 vào đó là khẳng định một điều ta không biết.
* **Không retry ở Phase 3.** Retry mù sẽ nhân bản `external_batch_id` hoặc che mất
  một lỗi hợp đồng thật sau vài lần thử. Phase 4 thêm nút gửi lại TƯỜNG MINH, dùng
  lại đúng batch id cũ — đó là phép thử idempotency, không phải cơ chế phục hồi.
* **`area_ref` dùng dạng `{area_name, unit_type}`, KHÔNG dùng `{area_id}`.** Một
  `source_instance_id` phải dùng đúng MỘT hình dạng vĩnh viễn: dấu vân payload tính
  trên tập trường đã ánh xạ, nên đổi hình dạng cho cùng một căn ở cùng revision sẽ
  sinh hai dấu vân khác nhau và backend báo `conflict` giả.

## Lệnh và kết quả THẬT

**Command:** `python -m scripts.sync_simulator --issue-key --instance mini-crm-dev`
**Result:** PASS — cấp khoá `afsk_iUx…`, backend chỉ giữ hash.

**Command:** đẩy một lô 1 căn từ TRONG container Mini CRM sang `http://api:8000`
```
docker compose exec -T minicrm python -c "... SyncClient().push_units(UNITS) ..."
```
**Result:** PASS
```
HTTP 202
accepted: True | replayed: False
sync_run_id: 0bc87fb6-c628-4f58-b971-63de4906eeac
decisions:   {"insert": 1, ...}
projections: {"inserted": 1, ..., "rejected": 0}
```

**Kiểm chứng ở BACKEND:**

| Kiểm | Kết quả |
|---|---|
| `upload_files` của `mini-crm-dev` | `completed`, `rows_ok=1`, `transport_mode=api_push`, batch `mc-units-d08573f6-…` |
| `units` đã chiếu | `U-P3-0001 \| P3-01-01 \| Căn hộ \| available \| rev=1` |
| `ranking_runs` | **0 — ĐÚNG NHƯ MONG ĐỢI.** Dây kích hoạt sau COMMIT là Phase 4/5, chưa nối |

**Kiểm chứng ở MINI CRM:**

| Kiểm | Kết quả |
|---|---|
| `crm_outbox` | `units \| http=202 \| payload_records=1 \| resp_run=0bc87fb6-…` |

**Command:** `cd minicrm && pytest -q` (với `MINICRM_TEST_DATABASE_URL` trỏ `minicrm_test`)
**Result:** PASS — **35 passed**

**Command:** `ruff check src/ tests/ minicrm/`
**Result:** PASS — All checks passed!

## Hai lỗi TEST đã tự phát hiện và sửa

Ghi lại vì cả hai đều là loại lỗi khiến một phép thử xanh mà không kiểm được gì:

1. `test_only_one_holding_deal_per_unit` dựng một dòng có `sold_at < reserved_at`,
   nên `ck_crm_deals_sold_after_reserved` nổ TRƯỚC chỉ mục duy nhất đang cần kiểm.
   Sửa: `reserved_at=None` để phép thử chạm đúng ràng buộc nó nhắm tới.
2. `test_minicrm_never_imports_backend_modules` tìm chuỗi `"from src."` trên cả
   file, nên nó khớp với chính chuỗi hằng số của nó. Sửa: so theo TỪNG DÒNG và đòi
   dòng BẮT ĐẦU bằng câu import; chuỗi tìm kiếm được GHÉP thay vì viết thẳng, để
   phép kiểm `grep -rn "from src\." minicrm/` của quy trình trả về RỖNG thật.

---

# Đợt 2026-08-11 (f) — Phase 3B: schema Mini CRM (cây Alembic RIÊNG)

Phạm vi: ba bảng trong một cây Alembic **hoàn toàn độc lập**.

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| `crm_units`, `crm_deals`, `crm_outbox` | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Lịch sử Alembic riêng của Mini CRM | NOT IMPLEMENTED | **IMPLEMENTED** — `0001_minicrm_initial`, `down_revision = None` |

## Ranh giới đã cưỡng chế

| | Backend | Mini CRM |
|---|---|---|
| `alembic.ini` | `/alembic.ini` | `/minicrm/alembic.ini` |
| `env.py` đọc cấu hình từ | `src.config.get_settings()` | `app.config` (`env_prefix="MINICRM_"`) |
| Biến DSN | `DATABASE_URL` | **`MINICRM_DATABASE_URL`** |
| `alembic_version` | database `AbsorptionForecast` → `0015_ranking_results` | database `minicrm` → `0001_minicrm_initial` |

`minicrm/alembic/env.py` **không import `src.config`**. Đọc nhầm `DATABASE_URL`
nghĩa là chạy migration của Mini CRM lên database của backend và ghi
`0001_minicrm_initial` vào bảng `alembic_version` của backend — trộn hai lịch sử
làm một, không có đường lùi sạch.

## Ràng buộc SOI GƯƠNG backend

Mục đích không phải trang trí: Mini CRM **không thể sinh ra** một payload mà
backend chắc chắn từ chối. Bắt lỗi tại nguồn rẻ hơn nhiều so với bắt ở đầu kia của
một request HTTP, và nó giữ cho lô bị từ chối ở backend mang ý nghĩa thật — một lô
đỏ nghĩa là hợp đồng có vấn đề, không phải Mini CRM cẩu thả.

* `unit_status IN ('available','reserved','sold','blocked')` — đúng tập của backend.
  Backend **không có bảng alias cho unit**, nên Mini CRM phát thẳng từ vựng chuẩn.
* `deal_status` đúng bảy giá trị chuẩn; `cancelled` KHÔNG có ở đây.
* Trạng thái ↔ mốc thời gian (`reserved`→`reserved_at`, `sold`→`sold_at`,
  `lost`→`lost_at`, `sold_at >= reserved_at`) — chốt A4 nhìn từ phía NGUỒN.
* `uq_crm_deals_holding_per_unit` — soi gương `uq_deals_active_per_unit`.
* `uq_crm_units_live_code` (partial, `WHERE deleted_at IS NULL`) — tombstone giải
  phóng MÃ CĂN nhưng **không bao giờ** giải phóng `external_id` (giả định A1).

**Một điểm KHÁC backend, có chủ đích:** `source_revision` là `NOT NULL CHECK > 0`.
Backend cho phép NULL (hệ nguồn có thể chỉ gửi `source_updated_at`); Mini CRM thì
không, vì nó LÀ hệ nguồn và phải cấp được số phiên bản đơn điệu. Cho phép NULL ở
đây là tự cho phép mình vi phạm giả định A2 của chính hợp đồng mình đang gửi.

**KHÔNG có** khách hàng, hợp đồng, PII, giá, hoa hồng, lịch thanh toán, nhân viên
bán — hợp đồng v1 không cần, và thêm vào là mời dữ liệu cá nhân vào một hệ thống
chưa có tầng bảo vệ nào.

## Lệnh và kết quả THẬT

**Command:** `docker compose exec minicrm alembic -c alembic.ini upgrade head`
(chạy tự động qua `MINICRM_RUN_MIGRATIONS=true` lúc khởi động)
**Result:** PASS — `Running upgrade -> 0001_minicrm_initial`

**Command:** `pytest tests/test_migration_0001.py` (trong `minicrm/`)
**Result:** PASS — 20 test, database dùng một lần `mc0001_<hex>_test` mỗi test.
Phủ: lên/xuống/chạy lại, `alembic_version` riêng, **bảng backend KHÔNG có trong DB
Mini CRM**, migration không nhắc tên bảng backend, trạng thái căn/giao dịch lạ bị
chặn, ba ràng buộc trạng thái↔mốc, `sold` trước `reserved`, một giao dịch giữ mỗi
căn, `lost` KHÔNG chặn giao dịch mới, mã căn duy nhất khi còn sống, tombstone giải
phóng mã nhưng không giải phóng `external_id`, giao dịch mồ côi bị chặn.

**Kiểm cô lập database (lệnh thật):**

| Kiểm | Kết quả |
|---|---|
| Bảng trong DB Mini CRM | `alembic_version, crm_deals, crm_outbox, crm_units` |
| `alembic_version` Mini CRM | `0001_minicrm_initial` |
| `alembic_version` backend | `0015_ranking_results` |
| Bảng backend rò vào DB Mini CRM | **0** |
| Bảng `crm_*` rò vào DB backend | **0** |

---

# Đợt 2026-08-11 (e) — Phase 3A: môi trường Mini CRM (ứng dụng + Docker)

Phạm vi: cây ứng dụng riêng, image riêng, database riêng, cấu hình riêng.

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| Ứng dụng Mini CRM | NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Service `minicrm` + `minicrm_db` | NOT IMPLEMENTED | **IMPLEMENTED** — cả hai `healthy` |
| Cấu hình cô lập (`MINICRM_`) | NOT IMPLEMENTED | **IMPLEMENTED** |
| CRUD / UI Mini CRM | NOT IMPLEMENTED | **KHÔNG ĐỔI** — Phase 4 |

## Cô lập ở bốn tầng

| Tầng | Backend | Mini CRM |
|---|---|---|
| Ứng dụng | `src/` | `minicrm/app/` |
| Image | `absorptionforecast-backend:dev` | `minicrm-synthetic:dev` |
| Database | `db:5432` → `AbsorptionForecast` | `minicrm_db:5432` → `minicrm` |
| Cổng host | 5432 / 8000 | **5433 / 8100** |
| Volume | `pgdata` | **`minicrm_pgdata`** |
| Tiền tố biến | (không) | **`MINICRM_`** |

Cổng khác nhau là có chủ đích: gõ nhầm cổng không được phép trỏ vào nhầm hệ.

**Tiền tố `MINICRM_` là ranh giới quan trọng nhất, và nó nằm ở đúng một dòng**
(`env_prefix="MINICRM_"` trong `app/config.py`). Hai hệ chạy cùng mạng Compose và
có thể cùng đọc một `.env`; không có tiền tố, Mini CRM sẽ đọc trúng `DATABASE_URL`
của backend. Có test riêng cho việc này
(`test_config_uses_the_minicrm_prefix`): đặt `DATABASE_URL` trỏ vào
`AbsorptionForecast` rồi khẳng định Mini CRM **không** nhặt nó.

**Phụ thuộc tối thiểu:** FastAPI, uvicorn, SQLAlchemy, asyncpg, psycopg2-binary,
alembic, pydantic-settings, httpx, pytest. **KHÔNG có** prophet, langgraph, rq,
pandas, cloudinary — Mini CRM không dự báo, không chạy agent, không có hàng đợi.

`MINICRM_RUN_MIGRATIONS` dùng tiền tố riêng chứ không dùng lại `RUN_MIGRATIONS`:
tên chung nghĩa là bật migrate cho hệ này sẽ bật luôn cho hệ kia.

## Lệnh và kết quả THẬT

**Command:** `docker compose config`
**Result:** PASS — cấu hình hợp lệ

**Command:** `docker compose up -d --build minicrm_db minicrm`
**Result:** PASS — cả 8 service chạy; `minicrm` và `minicrm_db` đều `healthy`

**Command:** `curl -fsS http://localhost:8100/health`
**Result:** PASS — **HTTP 200**
```json
{"status":"ok","app":"Mini CRM (synthetic)","source_instance_id":"mini-crm-dev",
 "disclaimer":"Mini CRM TỔNG HỢP do chính dự án này viết. KHÔNG phải CRM của khách hàng, ..."}
```

**Command:** `grep -rn "from src\." minicrm/`
**Result:** PASS — **không có output**

**Command:** `grep -rn "minicrm" src/`
**Result:** PASS — **không có output**

## Hồi quy backend (chạy SAU khi dựng Mini CRM)

| Command | Result |
|---|---|
| `TEST_TARGET=tests/test_services/test_domain_projection.py bash scripts/test_db.sh` | PASS — 40 passed |
| `TEST_TARGET=tests/test_services/test_source_identity.py bash scripts/test_db.sh` | PASS — 20 passed |
| `TEST_TARGET=tests/test_services/test_history_guard.py bash scripts/test_db.sh` | PASS — 24 passed |
| `TEST_TARGET=tests/test_api/test_seeded_dashboard.py bash scripts/test_db.sh` | PASS — 14 passed |
| `TEST_TARGET=tests/test_jobs/test_excel_to_database.py bash scripts/test_db.sh` | PASS — 8 passed |
| `TEST_TARGET=tests/test_services/test_import_records.py bash scripts/test_db.sh` | PASS — 41 passed |
| `TEST_TARGET=tests bash scripts/test_db.sh` | PASS — **1012 passed, 1 skipped** |

**SKIP:** `tests/test_scheduler.py` — cần `apscheduler`, gói chỉ có trong image
container, không có ở venv cục bộ. **Đây là SKIP, không phải PASS.** Không đổi so
với Phase 1/2.

Backend `alembic_version` vẫn `0015_ranking_results`; 15 file revision, không file
nào bị sửa.

## Chặn còn lại

Không mục nào mới. Vẫn nguyên: từ vựng trạng thái unit (`UNKNOWN`),
`units.listed_at`, giá, **và quan trọng nhất — chưa ai được giao việc sản xuất ảnh
chụp đặc trưng khảo sát**.

## Phạm vi KHÔNG làm trong đợt này

CRUD Mini CRM (Phase 4); UI; deals CRUD; bộ tính điểm; worker xếp hạng; API xếp
hạng; bộ sản xuất khảo sát; bảng khảo sát thô; sửa migration backend; thêm bảng
nghiệp vụ backend; di chuyển file backend.

---

# Đợt 2026-08-11 (d) — Phase 2B: ranking_runs và ranking_scores (migration 0015)

Phạm vi: **hai bảng, thuần CỘNG THÊM**. Không bảng nào đang có bị sửa. Không mã
tính điểm, không worker, không API — chỉ schema.

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| `ranking_runs` (vòng đời lần xếp hạng) | DESIGNED BUT NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| `ranking_scores` (điểm + thứ hạng) | DESIGNED BUT NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Bản chiếu Core 4 bảng xếp hạng | — | **IMPLEMENTED** (`src/models/tables.py`, có test đối chiếu schema thật) |
| Điểm mở rộng dọn DB ở `conftest.py` | rỗng | **IMPLEMENTED** — 4 bảng xếp hạng, thứ tự con-trước-cha |
| **Bộ tính điểm / worker / API xếp hạng** | DESIGNED BUT NOT IMPLEMENTED | **KHÔNG ĐỔI** — Phase 3+ |

## Quyết định schema đáng ghi lại

* **`uq_ranking_runs_queued_per_project`** (partial unique `WHERE status='queued'`)
  là chốt CHỐNG DỒN. Xếp hạng lại luôn ở phạm vi TOÀN DỰ ÁN — vì `rank_in_project`
  dịch chuyển khi bất kỳ căn nào đổi điểm — nên một trăm lô đồng bộ trong một phút
  mà sinh một trăm lần tính lại là lụt hàng đợi để đổi lấy đúng một kết quả. Chốt
  nằm ở DB, không nằm ở trí nhớ người viết truy vấn.
* **`scope_type` chỉ nhận `'project'`.** Cột tồn tại để truy vấn sau không phải
  đoán, nhưng tập giá trị khoá lại đúng một phần tử: phạm vi phân khu KHÔNG giữ
  được `rank_in_project` đúng, và một cột cho phép giá trị mà hệ thống chưa xử lý
  được là một cột mời dùng sai.
* **`sync_run_id` dùng `ON DELETE SET NULL`, không CASCADE.** Dọn `upload_files`
  cũ không được xoá lịch sử xếp hạng; mất liên kết ngược thì chấp nhận được, mất
  cả dòng thì không. `unit_id` thì ngược lại — CASCADE, vì một dòng điểm cho căn
  không còn tồn tại là dữ liệu vô nghĩa (trên thực tế nhánh này gần như không chạy:
  `units` bị xoá MỀM).
* **KHÔNG có UNIQUE trên `(project_id, rank_in_project)`.** Lúc chèn lại cả dự án,
  trạng thái trung gian vi phạm nó; một ràng buộc phải `DEFERRABLE` mới sống được,
  chỉ để chặn một lỗi mà chính tầng ghi không thể tạo ra.

## Lệnh và kết quả THẬT

**Command:** `TEST_TARGET=tests/test_migrations/test_0015_ranking_results.py bash scripts/test_db.sh`
**Result:** PASS — **22 passed / 0 failed**
**Notes:** chạy trên database DÙNG MỘT LẦN `mig15_<hex>_test`, tạo và huỷ trong
từng test. Phủ: lên/xuống, chốt chống dồn (chặn cùng dự án, CHO PHÉP khác dự án,
run đã kết thúc giải phóng chỗ), trạng thái ↔ mốc thời gian, trigger lạ,
`scope_type` lạ, bộ đếm không nhất quán, trùng `unit_id`, điểm/thứ hạng/coverage
ngoài khoảng, khoá ngoại, cascade dự án, cascade căn, `SET NULL` của `sync_run_id`,
bảng cũ không đổi, và **bản chiếu Core khớp schema thật**.

**Command:** `bash scripts/migrate.sh 0015_ranking_results`
**Result:** PASS — sao lưu `backups/pre_0015_ranking_results_20260811_220102.dump`
đã kiểm đọc được, rồi mới migrate. Revision sau khi migrate: `0015_ranking_results`.

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh`
**Result:** PASS — **1012 passed, 1 skipped** (trước Phase 2 là 973 passed;
+39 test migration mới).
**SKIP:** `tests/test_scheduler.py` — cần `apscheduler`, gói chỉ có trong image
container, không có ở venv cục bộ. **Đây là SKIP, không phải PASS.**

**Command:** `ruff check src/ tests/`
**Result:** PASS — All checks passed!

## Chặn còn lại

Không mục nào mới. Danh sách chặn giữ nguyên như Phase 2A bên dưới.

## Phạm vi KHÔNG làm trong đợt này

Bộ tính đặc trưng; bộ tính điểm; worker xếp hạng; API config; API đọc; endpoint
ảnh chụp khảo sát; job audit; Mini CRM; dây kích hoạt sau COMMIT ở `SyncRunService`.
**Chưa dòng `ranking_scores` hay `ranking_runs` nào tồn tại ngoài test.**

---

# Đợt 2026-08-11 (c) — Phase 2A: feature_snapshots, ranking_configs, config v1 (migration 0014)

Phạm vi: **hai bảng + một dòng seed, thuần CỘNG THÊM**. Không bảng nào đang có bị
sửa, không cột nào bị đổi, không dòng nghiệp vụ nào bị đụng.

## Điều kiện tiên quyết — baseline đã khôi phục

Database dev đang RỖNG khi mở Phase 2 (ghi nhận ở Đợt 2026-08-11 (b): volume
`pgdata` được tạo lại lúc 15:18, chưa seed lại). Đã xử lý TRƯỚC khi đụng vào bất
kỳ file nào của Phase 2:

**Command:** `python -m scripts.seed_dev`
**Result:** PASS — 1085 dòng / 29 bảng

**Command:** `python -m scripts.baseline_dev_data --compare docs/baselines/dev_0013.json`
**Result:** PASS — **KHỚP — 29 bảng giống hệt baseline**

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| `feature_snapshots` | DESIGNED BUT NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| `ranking_configs` | DESIGNED BUT NOT IMPLEMENTED | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** |
| Config v1 (vận hành, tổng trọng số 1.0) | — | **IMPLEMENTED** — 1 dòng `published`, do migration seed |
| **Đặc trưng khảo sát** | BLOCKED | **VẪN BLOCKED** — chưa có bộ tổng hợp nào sản xuất chúng |
| **`days_on_market`** | BLOCKED | **VẪN BLOCKED** — cần `units.listed_at` |

## Quyết định schema đáng ghi lại

* **`feature_snapshots` mang `project_id` trong khoá danh tính.** Phạm vi
  `unit_type` chỉ là một CHUỖI (`'2PN'`), không phải khoá ngoại. Thiếu `project_id`
  thì `view_quality` của loại 2PN ở dự án A ghi đè lên chính nó ở dự án B — hai dự
  án khác nhau dùng chung một dòng, và không ai phát hiện ra. Test
  `test_same_feature_identity_in_two_projects_is_allowed` chốt điều ngược lại.
* **`scope_id` là TEXT, không phải UUID.** Nó phải chứa được cả uuid dạng chuỗi
  (phạm vi `unit`/`area`) lẫn chuỗi `unit_type` nguyên văn. Cái giá: không khoá
  ngoại nào cưỡng chế được nó. Đánh đổi có chủ đích, ghi trong docstring migration.
* **Đúng MỘT config `published`**, cưỡng chế bằng partial unique index — cùng ý
  tưởng với `uq_deals_active_per_unit` (0007).
* **Config v1 CHỈ có đặc trưng VẬN HÀNH.** Bốn đặc trưng
  (`unit_available` 0.50, `has_active_deal` 0.20, `area_velocity_norm` 0.20,
  `area_conversion_norm` 0.10), tổng **1.0**, tất cả suy được từ
  `units`/`deals`/`areas` đang có. Đặc trưng khảo sát KHÔNG có mặt, và đây không
  phải chuyện bỏ sót: chưa có bộ tổng hợp nào sản xuất chúng, nên mọi giá trị sẽ
  MISSING, chính sách `skip` loại chúng khỏi mẫu số, `coverage` tụt dưới
  `min_weight_coverage`, và **MỌI căn bị bỏ qua — không thứ hạng nào được sinh ra**.
  Một config trông đầy đủ mà cho ra bảng rỗng là kiểu hỏng tệ nhất ở đây.

## Lệnh và kết quả THẬT

**Command:** `TEST_TARGET=tests/test_migrations/test_0014_ranking_foundation.py bash scripts/test_db.sh`
**Result:** PASS — **17 passed / 0 failed**
**Notes:** database dùng một lần `mig14_<hex>_test`. Phủ: lên/xuống,
`feature_value` ngoài [0,1] (trên và dưới), `scope` lạ, `source` lạ, `confidence`
ngoài khoảng, trùng khoá danh tính, **cùng danh tính ở hai dự án thì HỢP LỆ**,
config `published` thứ hai bị chặn, `published` thiếu `published_at` bị chặn,
`weights` rỗng bị chặn, coverage ngoài khoảng, seed đúng 1 config `published`,
**seed chỉ có đặc trưng vận hành và tổng trọng số = 1.0**, bảng cũ không đổi.

**Command:** `bash scripts/migrate.sh 0014_ranking_foundation`
**Result:** PASS — sao lưu `backups/pre_0014_ranking_foundation_20260811_220052.dump`
(158813 byte, 30 bảng có dữ liệu) đã kiểm đọc được, rồi mới migrate.

**Command:** `TEST_TARGET=tests/test_migrations bash scripts/test_db.sh`
**Result:** PASS — **112 passed / 0 failed** (toàn bộ test migration 0005→0015)

**Command:** `TEST_TARGET=tests/test_services/test_legacy_boundary.py bash scripts/test_db.sh`
**Result:** PASS — 14 passed

**Command:** `TEST_TARGET=tests/test_api/test_seeded_dashboard.py bash scripts/test_db.sh`
**Result:** PASS — 14 passed

**Command:** `TEST_TARGET=tests/test_jobs/test_excel_to_database.py bash scripts/test_db.sh`
**Result:** PASS — 8 passed

**Command:** `TEST_TARGET=tests/test_services/test_import_records.py bash scripts/test_db.sh`
**Result:** PASS — 41 passed

**Command:** `python -m scripts.baseline_dev_data --compare docs/baselines/dev_0013.json` (SAU cả hai migration)
**Result:** KHÁC BIỆT — và khác biệt ĐÚNG như mong đợi:
```
- alembic revision: 0013_calculator_comparisons -> 0015_ranking_results
- bảng MỚI: feature_snapshots (0 dòng)
- bảng MỚI: ranking_configs (1 dòng)
- bảng MỚI: ranking_runs (0 dòng)
- bảng MỚI: ranking_scores (0 dòng)
```
**Không một bảng nghiệp vụ cũ nào đổi số dòng.** Đây chính là bất biến cần chứng minh.

**Command:** `python -m scripts.baseline_dev_data --write docs/baselines/dev_0015.json`
**Result:** PASS — 33 bảng, 1086 dòng (1085 cũ + 1 config seed), revision
`0015_ranking_results`. Đối chiếu lại ngay sau đó: **KHỚP — 33 bảng**.

## Bất biến dữ liệu đã kiểm (database dev)

| Kiểm | Kết quả |
|---|---|
| `alembic_version` | `0015_ranking_results` |
| Số config `published` | **1** (v1, `created_by = migration_0014`) |
| Tổng trọng số v1 | **1.000000** |
| Khoá đặc trưng v1 | `area_conversion_norm, area_velocity_norm, has_active_deal, unit_available` |
| Đặc trưng khảo sát/`days_on_market`/`price` trong v1 | **0** |
| Dòng `ranking_scores` / `ranking_runs` / `feature_snapshots` | **0 / 0 / 0** |
| Số file revision | 15 (không file cũ nào bị sửa) |

## Chặn còn lại

* **Từ vựng trạng thái unit: `UNKNOWN`** — không có bảng alias cho unit. Không đổi
  từ Phase 1.
* `units.listed_at` / `days_on_market`: **BLOCKED BY REAL MINI CRM**.
* Giá: **BLOCKED** — không có trường giá ở hợp đồng lẫn mô hình miền.
* **Ai sản xuất ảnh chụp đặc trưng khảo sát: CHƯA CHỐT.** Đây là chặn nghiêm trọng
  nhất còn lại. Bốn đặc trưng vận hành chỉ phân biệt được "căn còn trống, phân khu
  bán nhanh" — thứ tra được bằng một câu SQL. Bộ xếp hạng chỉ có giá trị nghiệp vụ
  khi có `view_quality`, `natural_light`, `privacy`, `noise_level`, và chưa ai được
  giao việc sản xuất chúng. Phải chốt trước khi mở config v2.
* `AreaService.summary()` chỉ nhận biết bộ tính một nửa — chưa chặn (cấm cắt sang).
* `tests/test_scheduler.py` bị SKIP ở venv cục bộ (thiếu `apscheduler`); chạy được
  trong container.

## Phạm vi KHÔNG làm trong đợt này

Bảng `ranking_audit` (**cố ý KHÔNG tạo** — mọi trạng thái nó định ghi đã nằm ở
`ranking_runs`; audit là một JOB); `survey_raw_responses`; `ranking_score_history`;
`feature_history`; `event_log`; `units.listed_at`; trường giá; mã bộ xếp hạng;
worker; API; Mini CRM; di chuyển file; đổi hành vi Excel/CSV hay dashboard.

---

# Đợt 2026-08-11 (b) — Phase 1: chốt an toàn trước khi thêm bảng xếp hạng

Phạm vi: **SỬA LỖI và AN TOÀN QUY TRÌNH**. Không thêm bảng, không thêm revision
Alembic, không đụng luồng Excel/CSV, không cắt sang dashboard, không di chuyển
file nào. `alembic head` giữ nguyên `0013_calculator_comparisons`.

## Trạng thái trước → sau

| Hạng mục | Trước | Sau |
|---|---|---|
| `area_ref` dạng `{area_id}` ở tầng chiếu | BROKEN | **IMPLEMENTED** |
| `units.unit_type` khi dùng nhánh `area_id` | UNKNOWN | **IMPLEMENTED** (suy từ `areas.unit_type`) |
| Đường migrate an toàn (Makefile) | BROKEN | **IMPLEMENTED** |
| `make typecheck` (mypy không có trong requirements) | BROKEN | **đã gỡ target** |
| Hạ tầng dọn DB dùng chung | INCOMPLETE | **IMPLEMENTED** (2/10 module đã chuyển) |
| 4 docstring/comment nói sai hiện trạng | BROKEN | **IMPLEMENTED** |
| Tái tổ chức module | — | **DESIGNED BUT NOT IMPLEMENTED** (hoãn có chủ đích) |
| Bảng xếp hạng (0014/0015) | DESIGNED BUT NOT IMPLEMENTED | **không đổi** — Phase 2 |
| Mini CRM thật | BLOCKED BY REAL MINI CRM | **không đổi** |

## Lỗi P0 đã sửa

`area_ref` của hợp đồng v1 là một `oneOf`: `{area_id}` hoặc `{area_name, unit_type}`.
JSON Schema nhận cả hai, `contract_adapter._flatten_area_ref()` chuyển cả hai,
nhưng `DomainProjector._project_unit()` chỉ đọc được hình dạng thứ hai — nó gọi
`_require_text(data, "area_name")` và ném `MISSING_FIELD` trên một trường mà
payload đó **không được phép mang** (`unit_payload` đóng `additionalProperties`).
Một hệ nguồn chuẩn hoá theo `area_id` sẽ mất **toàn bộ** bản ghi căn ngay lô đầu.
Không fixture nào trong 17 fixture ở `docs/crm/fixtures/` phủ nhánh này.

Điểm mà kế hoạch ban đầu bỏ sót: nhánh `area_id` **không mang `unit_type`**, mà
`units.unit_type` là `NOT NULL CHECK <> ''`. Hợp đồng không có chỗ nào để gửi nó
kèm `area_id`. Nguồn duy nhất còn lại là dòng `areas` đã tra được — và ở nhánh
`area_name` thì `uq_areas_project_name_unit_type` đã bảo đảm hai giá trị bằng
nhau, nên đọc từ `areas` là tương đương mà chỉ còn MỘT nguồn.

Quy tắc đã chốt: `area_id` **thắng** khi cả hai cùng có; tra luôn giới hạn theo
`project_id`; tra không thấy thì **TỪ CHỐI**, không rơi về `area_name`.

`history_guard._read_unit_mirror()` **CỐ Ý KHÔNG** trả `area_id`, và điều này đã
được ghi thành comment tại chỗ. Trả nó sẽ khiến `merge_record()` chép `area_id`
CŨ vào một bản ghi partial đang chuyển phân khu bằng `area_name`; `area_id` thắng,
nên lệnh chuyển biến mất **không một lỗi nào**. Test
`test_partial_area_move_by_name_is_not_overridden_by_stale_mirror` chốt điều đó.

## Lệnh và kết quả THẬT

**Command:** `TEST_TARGET=tests/test_services/test_domain_projection.py bash scripts/test_db.sh`
**Result:** PASS
**Passed/failed:** 40 passed / 0 failed (34 test cũ + 6 test mới cho nhánh `area_id`)

**Command:** `TEST_TARGET=tests/test_services/test_legacy_boundary.py bash scripts/test_db.sh`
**Result:** PASS — 14 passed / 0 failed

**Command:** `TEST_TARGET=tests/test_api/test_seeded_dashboard.py bash scripts/test_db.sh`
**Result:** PASS — 14 passed / 0 failed

**Command:** `TEST_TARGET=tests/test_jobs/test_excel_to_database.py bash scripts/test_db.sh`
**Result:** PASS — 8 passed / 0 failed

**Command:** `TEST_TARGET=tests/test_services/test_import_records.py bash scripts/test_db.sh`
**Result:** PASS — 41 passed / 0 failed

**Command:** `TEST_TARGET=tests/test_scripts/test_seed_dev.py bash scripts/test_db.sh`
**Result:** PASS — 27 passed / 0 failed

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh -q --tb=no` — chạy **HAI LẦN**
liên tiếp, KHÔNG dựng lại database
**Result:** PASS — `973 passed, 1 skipped` ở **cả hai** lượt (401.73s rồi 341.83s).
Kết quả trùng khớp hoàn toàn; chỉ chuỗi thời gian chạy khác nhau.
**Notes:** 1 test bỏ qua là `test_scheduler.py` — cần `apscheduler`, gói này chỉ có
trong image container, không có ở venv cục bộ.

**Command:** `grep -rnP "^\t.*alembic" Makefile scripts/ docker/`
**Result:** PASS — chỉ còn `alembic revision` (sinh file revision, không đụng
database). Không còn `alembic upgrade` nào chạy được ngoài `scripts/migrate.sh`,
`docker/entrypoint.sh` và `scripts/test_db.sh`.

**Command:** `make check`
**Result:** PASS — thoát 0. Không còn lỗi thiếu mypy. (293 passed, 681 skipped —
không có biến môi trường DB nên phần test cần DB tự bỏ qua, đúng thiết kế.)

**Command:** `ruff check src/ tests/`
**Result:** PASS — All checks passed!

**Command:** `ruff check scripts/`
**Result:** PARTIAL — 8 lỗi, **toàn bộ** nằm ở `log_antigravity.py`, `log_hook.py`,
`log_manual.py`, `submit_log.py`. Đây là lỗi CÓ TỪ TRƯỚC ở nhóm tiện ích ghi log,
không thuộc phạm vi Phase 1 và không file nào trong số đó bị đụng tới.

**Command:** `SELECT version_num FROM alembic_version` (database dev)
**Result:** `0013_calculator_comparisons` — KHÔNG ĐỔI. `ls alembic/versions/*.py | wc -l` = 13.

**Command:** `SELECT count(*) FROM information_schema.tables WHERE table_name IN
('feature_snapshots','ranking_configs','ranking_runs','ranking_scores')`
**Result:** `0` — chưa bảng xếp hạng nào tồn tại, đúng như phạm vi Phase 1.

**Command:** `git status --porcelain | grep -E "^R|^ R"`
**Result:** rỗng — không file nào bị di chuyển hay đổi tên.

## Ghi nhận trung thực: database DEV đang RỖNG

`python -m scripts.baseline_dev_data --compare docs/baselines/dev_0013.json` báo
**KHÔNG KHỚP**: mọi bảng về 0 (`projects` 4 → 0, `sales_records` 360 → 0,
`absorption_daily` 360 → 0, …).

**Việc này KHÔNG do Phase 1 gây ra**, và có bằng chứng dứt khoát:

* `pg_stat_user_tables` của database `AbsorptionForecast` cho thấy **chỉ
  `alembic_version` từng có insert** (`ins=1`); **mọi bảng nghiệp vụ khác:
  `n_tup_ins = 0`, `n_tup_del = 0`**. Không dòng nghiệp vụ nào từng được chèn,
  và không dòng nào từng bị xoá, kể từ khi tiến trình postgres hiện tại khởi động.
* `docker volume inspect absorptionforecast_pgdata` → tạo lúc `2026-08-11T15:18:09+07:00`;
  `pg_postmaster_start_time()` → `2026-08-11 08:18:11 UTC` (cùng thời điểm).

Nghĩa là volume `pgdata` đã được **tạo lại** lúc 15:18, entrypoint của container
`api` (`RUN_MIGRATIONS=true`, chỉ dùng ở dev) migrate lên `0013`, và database
chưa được seed lại kể từ đó. Số liệu 4 dự án đọc được sớm hơn trong ngày đến từ
volume TRƯỚC lần tạo lại.

Khôi phục khi cần: `python -m scripts.seed_dev`, hoặc phục hồi từ
`backups/pre_head_20260810_131334.dump`. **Chưa thực hiện** — đây là môi trường
dev của người vận hành, và việc tạo lại volume có vẻ là chủ đích.

Điều này **không chặn Phase 1**: Phase 1 không đổi schema và không đổi dữ liệu.
Nhưng nó có nghĩa là phép đối chiếu baseline **chưa dùng được làm cổng nghiệm thu**
cho tới khi database dev được seed lại — cần lưu ý trước khi mở Phase 2.

## Chặn còn lại

* **Từ vựng trạng thái unit: `UNKNOWN`.** `domain_projection.py` chỉ hạ chữ thường
  rồi đòi giá trị thuộc `{available, reserved, sold, blocked}`; **không có bảng
  alias cho unit** (khác `deals`, vốn có `cancelled`/`canceled` → `lost`). Bản dự
  thảo hợp đồng §7 đề xuất `con_trong` → `available` nhưng **chưa được cài đặt**.
  Một CRM thật phát từ vựng tiếng Việt sẽ mất toàn bộ bản ghi căn — cùng lớp sự cố
  với lỗi P0 vừa sửa, chỉ ở một tầng khác. Phải hỏi đội CRM.
* `units.listed_at` / `days_on_market`: **BLOCKED BY REAL MINI CRM** — không nguồn
  nào backfill được. **Cấm** dùng `units.created_at` thay thế (nó là lúc bắt đầu
  soi gương, không phải lúc mở bán); dùng nó làm khoá phụ phá hoà thì an toàn.
* Giá: **BLOCKED** — không có trường giá ở hợp đồng lẫn mô hình miền.
* `AreaService.summary()` chỉ nhận biết bộ tính MỘT NỬA: `units_sold` đọc
  `sales_records` và `units_remaining` đọc `inventory_snapshots` **không lọc theo
  lineage**, trong khi `avg_velocity_30d`/`updated_at` thì có. Cắt sang
  `domain_units_deals` hôm nay sẽ cho một dashboard nửa vời. Không chặn Phase 1 vì
  **cấm cắt sang**; phải xử lý trước khi cắt.
* Ai sản xuất ảnh chụp đặc trưng khảo sát: **chưa chốt**. Không chặn Phase 1,
  nhưng chặn quyết định config v2 ở Phase 2.
* Database dev cần seed lại trước khi dùng baseline làm cổng nghiệm thu (xem trên).

## Phạm vi KHÔNG làm trong đợt này

Bảng xếp hạng và revision Alembic mới; mã bộ xếp hạng; Mini CRM; nhận đặc trưng
khảo sát; **di chuyển bất kỳ file nào** trong `src/services/`, `src/api/`,
`src/jobs/`, `src/models/`; đổi hành vi Excel/CSV; đổi hành vi dashboard;
`absorption_daily`; `projects.absorption_calculator`; thêm mypy;
`docs/crm/fixtures/18_unit_by_area_id.json` (cố ý không tạo — xem dưới).

Ba điều chỉnh so với kế hoạch, có lý do:

1. **Không thêm `area_id` vào `_read_unit_mirror()`** — kế hoạch ban đầu có mục
   này; nó sẽ tạo ra lỗi mất dữ liệu im lặng đã mô tả ở trên.
2. **Không tạo fixture 18.** Cả 17 fixture hiện có đều tự chứa và chạy được trên
   bất kỳ database đã migrate nào. Một fixture mang `area_id` cần UUID có thật
   trong database đích, nên nó sẽ hoặc hỏng hoặc phải sửa tay — và sẽ dạy người
   vận hành rằng một fixture đỏ là chuyện bình thường. Nhánh `area_id` được chứng
   minh bằng test tích hợp tự dựng phân khu và biết id của chúng.
3. **`make check` không cần sửa** — nó vốn đã là `lint format test`, không bao giờ
   gồm `typecheck`. Chỉ target rời và mục `.PHONY` bị gỡ.

`docs/ranking/data_contracts.md` **không còn trong repo** (đã xuất PDF rồi gỡ), nên
không có gì để sửa ở đó. Năm quyết định chuẩn đã có sẵn và ĐÚNG trong
`docs/ranking/implementation_plan.md`; chỉ sửa một tham chiếu treo tới file đã gỡ.

---

# Đợt 2026-08-11 — Technical assessment: current ingestion, future Mini CRM contract, and normalized output for analytics/AI

Prepared as a repository-evidence review, ahead of Phase 8F (source reconciliation). Every claim below is anchored to a file/line or a command actually run against this repo. Where the repository is silent or the two subsystems disagree, that is reported as a gap or a contradiction, not resolved by assumption.

**Standing facts that govern how to read everything below** (see also `docs/crm/activation_prerequisites.md`, `docs/crm/sync_contract_v1_draft.md`):

- This repository contains the **CRM sync receiver** — the code that would accept, validate, mirror and reconcile data from a Mini CRM. It is **not** a Mini CRM product, and no Mini CRM exists yet.
- The existing Excel/CSV path is **aggregate-only**: it produces daily sums (`sales_records.units_sold`, `inventory_snapshots.units_remaining`), never individual units or deals.
- Every JSON-sync test fixture under `docs/crm/fixtures/` is synthetic, written by this project. A fixture passing proves the **receiver** behaves as coded; it proves nothing about compatibility with any real Mini CRM, because no real Mini CRM payload has ever been observed.
- `scope='source'` reconciliation (comparing the mirror against CRM-published totals) is refused today by `src/api/reconciliation.py:101-113` with `UNSUPPORTED_RECONCILIATION_SCOPE`, and cannot pass against real truth until a real Mini CRM exists to publish that truth.
- Dashboard cutover (switching `projects.absorption_calculator` from `legacy_aggregate` to `domain_units_deals`) is forbidden until the cutover gate (Phase 8G, not yet built) passes. No code path in the repository writes to `absorption_calculator` other than the migration's default — confirmed by `grep -rn absorption_calculator src/` returning only reads (`src/services/absorption.py:259,265`) and comment references, never a write.

---

## Part 1 — Architecture map

### 1A. Existing aggregate file flow (Excel/CSV)

| Step | Detail |
|---|---|
| **1. Upload API** | `POST /files/upload` — `src/api/files.py:145-250`. Input: multipart `file` + form fields `template` (`sales`\|`inventory`\|`areas`) + `project_id`. Validates `template ∈ TEMPLATES` (files.py:167), project exists via `_project_exists` (files.py:51-59), file suffix via `validate_suffix` **before** reading the body (files.py:184, `src/services/file_upload.py:57-65`). |
| **2. Storage + checksum** | `FileUploadService.save` (`src/services/file_upload.py:74-116`) streams the file in 1 MB chunks, rejects `FILE_TOO_LARGE` mid-stream and `EMPTY_FILE` at end; computes SHA-256 while writing. Writer: local disk under `settings.upload_dir`, a Docker volume (`uploads:`) shared between `api` and `worker` (`docker-compose.yml:18`). |
| **3. `upload_files` row created** | `_create_upload_file` (files.py:73-91) inserts `status='pending'` **synchronously in the request**, own transaction. Duplicate-file check `_find_duplicate` (files.py:62-70) is an **application-level** `SELECT` on `(project_id, checksum)` — **not** DB-enforced: migration `0005_idempotent_csv_ingestion.py` deliberately **dropped** the `uq_upload_files_project_checksum` unique constraint and replaced it with a plain index (`ix_upload_files_project_id_checksum`), moving duplicate protection down to the business-key uniqueness of the target tables. This means two concurrent uploads of the same file can both pass the app-level duplicate check (race window), and the actual duplicate protection is `uq_sales_area_date_external_id` / `uq_inventory_area_date_type` at insert time. |
| **4. Async boundary / enqueue** | `get_queue(INGEST_QUEUE).enqueue("src.jobs.parse_upload.run_parse_upload", ...)` (files.py:220-229). **No `Retry(...)` is configured on this enqueue call** — confirmed by `grep -n "Retry(" src/api/files.py` returning nothing. Contrast: the sync-path domain-recompute enqueue (`src/services/sync_runs.py:626-633`) **does** pass `Retry(max=3, interval=[10,30,60])`. A crashed `run_parse_upload` job is therefore **not automatically retried** by RQ; it relies on `_failed()`/`_mark_file_failed` (parse_upload.py:264-292, 294-324) to leave the `upload_files` row in a terminal `failed` state, and a human/script must re-upload. |
| **5. Parser** | `ExcelParserService.parse_to_csv` (`src/services/excel_parser.py`) reads `.xlsx/.xlsm/.xlsb/.xls/.ods` via `python-calamine`, `.csv/.txt` via stdlib `csv`. Output: a **staging CSV** on disk (not DB rows yet), plus a list of `ParseError` (row/column-level). Structural failures (wrong format, empty file, unreadable) raise `ExcelParseError` and kill the whole file — `run_parse_upload` catches this and returns `status="failed"` (parse_upload.py:161-172). Cell-level failures (bad type, out-of-range) do **not** kill the file: they become per-row entries that flow to `upload_errors`. |
| **6. Validation** | Per-cell: type coercion + single-cell `CHECK`-equivalent bounds inside the parser (excel_parser.py docstring lines 55-63: "nhận dạng định dạng, ánh xạ cột, ép kiểu, và CHECK constraint ở phạm vi MỘT Ô"). Cross-row / DB-dependent validation (area lookup, business-key duplicates within the file) happens in `ImportService._insert_rows` (`src/services/import_records.py:273-362`), **not** in the parser. |
| **7. RQ ingest job → `ImportService.load`** | `src/services/import_records.py:176-258`. **Transaction boundary: exactly one `session.begin()`** wraps the entire load — set `status='processing'`, delete prior `upload_errors` for this file (rerun support), resolve `area_name`(+`unit_type`) → `areas.id` via `_load_area_index` (one query, not per-row), batch-upsert via `_upsert_stmt` (`ON CONFLICT DO NOTHING` when `versioned_by is None`, `ON CONFLICT DO UPDATE ... WHERE incoming.version IS NOT NULL AND (stored IS NULL OR incoming > stored)` otherwise — import_records.py:71-118), check error-rate threshold (`import_error_threshold`, default 0.5) and **raise `ImportRejectedError` to roll back the whole batch** if exceeded (import_records.py:229-233), else persist capped error rows (max 1000) and set terminal `status`. |
| **8. Target tables** | `sales_records`, `inventory_snapshots`, `areas` (via `TARGET_TABLES` map, `src/models/tables.py:215-219`). Idempotency: business-key `ON CONFLICT` — `uq_sales_area_date_external_id`, `uq_inventory_area_date_type` — versioned by `source_updated_at` (nullable; NULL never overwrites, added in `0005`). |
| **9. Legacy calculator recompute** | `AbsorptionCalculatorService.recompute` (`src/services/absorption.py:98-149`) is called **synchronously, inside the same RQ job**, right after `ImportService.load` succeeds (`src/jobs/parse_upload.py:99-101`, `asyncio.run(...)`). It is a **full delete-and-rewrite** of `absorption_daily` rows scoped to `calculator = 'legacy_aggregate'` for the affected project's areas (absorption.py:137-142) — not incremental. |
| **10. `absorption_daily`, `calculator='legacy_aggregate'`** | Final derived table for this flow. `units_reserved` is always written `NULL` for this lineage (absorption.py:182) — the legacy calculator has no concept of "reserved". |
| **11. Dashboard/API read** | `GET /areas`, `GET /absorption`, `GET /absorption/summary` (`src/api/dashboard.py:180-284`) — default to `legacy_aggregate` (dashboard.py:241, `PRODUCTION_CALCULATOR` in `src/api/inventory.py:41`). Frontend `UploadPage` polls `GET /files/{id}/status` (`src/api/files.py:284-312`), reading from `upload_files`, not from the (ephemeral, 500s TTL) RQ job result — files.py comment lines 15-16. |

**Failure/retry summary for this flow:** structural file errors → job `status="failed"`, DB row marked failed via a **separate** transaction (`_mark_file_failed`, parse_upload.py:294-324) so a rollback of the load transaction does not also roll back the failure marker — this mirrors a documented S1 production bug (`import_records.py:24-29` docstring references the lesson: `status='processing'` inside the rolled-back transaction would otherwise strand the row at `pending`). Error-rate threshold exceeded → whole batch rejected, no partial insert. Constraint violation at DB level → caught, only the constraint **name** surfaces to logs (never `str(exc)`, which could contain row data) — `import_records.py` via `src/jobs/parse_upload.py:174-206` `_constraint_name`.

**Relevant tests:** `tests/test_jobs/test_excel_to_database.py`, `tests/test_jobs/test_parse_upload.py`, `tests/test_services/test_import_records.py`, `tests/test_services/test_excel_parser.py`, `tests/test_migrations/test_0005_idempotent_csv_ingestion.py`.

---

### 1B. Future Mini CRM flow

No real Mini CRM exists. Every step below is marked with exactly one status:
**IMPLEMENTED** · **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** · **DESIGNED BUT NOT IMPLEMENTED** · **BLOCKED BY REAL MINI CRM** · **NOT PLANNED**.

| # | Step | Status | Evidence |
|---|---|---|---|
| 1 | Future Mini CRM exists and sends payloads | **BLOCKED BY REAL MINI CRM** | No CRM client, webhook, or polling job anywhere in `src/` — by design, per `AGENTS.md`/session-standing constraints. |
| 2 | Authenticated sync API | **IMPLEMENTED** (auth mechanism); **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** (as exercised end-to-end) | `POST /api/v1/sync/{entity}` (`src/api/sync.py:144-260`). `X-API-Key` → `SyncCredentialService.authenticate` (`src/services/sync_credentials.py:155-`), hash-compared (`secrets`-safe via `compare_digest` pattern), bound to a declared `source_instance_id` (INSTANCE_MISMATCH → 403). Table `sync_credentials` (migration `0008`). No credential has ever been issued to a real system — only test/dev credentials. |
| 3 | Contract/schema validation | **IMPLEMENTED** (validator); **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** (as proof of correctness) | `ContractValidator` (`src/services/contract_validation.py`) validates against `src/contracts/crm_sync_v1.schema.json` (JSON Schema 2020-12). Runs as Gate 3 of 4 in `src/api/sync.py:191-227`. 17 fixtures in `docs/crm/fixtures/` exercise pass/fail paths — all synthetic, none from a real CRM. |
| 4 | Sync run (batch bookkeeping) | **IMPLEMENTED** | `upload_files` row, shared with the file path (migration `0006`). `SyncRunService.run`/`_process` (`src/services/sync_runs.py:260-580`). Batch identity `(source_system, source_instance_id, external_batch_id)`, replay returns the prior result unprocessed (`_find_existing_run`, sync_runs.py:421-436). |
| 5 | Raw payload retention | **IMPLEMENTED** | `sync_payloads` table (migration `0009`, hardened by `0010` to `ON DELETE RESTRICT` after an incident — `docs/runbooks/migrations.md`). `SyncPayloadService.store`/`fetch`/`verify_integrity` (`src/services/sync_payloads.py:125-184`). Hash on the **canonicalized** form (`sort_keys`), not raw bytes, so it survives the JSONB round-trip. |
| 6 | Source identity | **IMPLEMENTED** | `crm_source_records` table (migration `0006`). `SourceIdentityService.apply` (`src/services/source_identity.py:125-179`) — six decisions: `insert`/`update`/`skip_stale`/`duplicate_noop`/`conflict`/`tombstone`. |
| 7 | Version ordering | **IMPLEMENTED** | `_compare` (`source_identity.py:90-112`) — `source_revision` first, else `source_updated_at`, else `unknown`. `payload_hash` is explicitly **never** used for ordering (only equality) — `source_identity.py` docstring lines 29-40. |
| 8 | History guard (A4) | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** | `src/services/history_guard.py`. Rejects a "full" update that silently drops `reserved_at`/`sold_at`/`lost_at` with `HISTORY_TIMESTAMP_DROPPED`; distinguishes absent/explicit-null/present per `payload_completeness`. Proven only against fixtures 13-17 (`docs/crm/fixtures/`) — **whether a real Mini CRM preserves these timestamps at all is A4 in `docs/crm/activation_prerequisites.md`, still unanswered (⬜).** |
| 9 | Conflict/tombstone handling | **IMPLEMENTED** | Conflict: same version, different content → keep accepted state, record `conflict_count`+1 (`source_identity.py:322-364`). Tombstone: `deleted_at` set, row never physically deleted (`domain_projection.py:364-388`). |
| 10 | `crm_source_records` write | **IMPLEMENTED** | See #6. |
| 11 | `units`/`deals` write (domain projection) | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** | `DomainProjector` (`src/services/domain_projection.py:168-389`). **Contradiction found:** the contract schema's `area_ref` (`crm_sync_v1.schema.json` `$defs/area_ref`, lines 138-174) allows **either** `{"area_id": "<uuid>"}` **or** `{"area_name","unit_type"}`, and `contract_adapter._flatten_area_ref` (lines 73-82) faithfully passes through whichever the source sent — but `DomainProjector._project_unit` (`domain_projection.py:215-272`) only ever reads `data["area_name"]`/`data["unit_type"]` (`_require_text(data, "area_name", ...)`, line 224) and never reads `data["area_id"]`. A payload using `area_ref: {"area_id": ...}` — a shape the schema explicitly permits — would be adapted to `{"area_id": ...}` and then rejected with `MISSING_FIELD` on `area_name`. No fixture exercises `area_ref` by `area_id`; all 17 fixtures use `area_name`+`unit_type` (`grep -c area_ref` across fixtures, confirmed by inspection). This is a real implementation gap, not resolved anywhere in the repo. |
| 12 | Domain recomputation | **IMPLEMENTED** (mechanism); never runs against real data | `DomainAbsorptionCalculatorService.compute`/`persist` (`src/services/domain_absorption.py:111-384`). Enqueued after a sync run that actually changed the mirror (`sync_runs.py:568-570`, `Retry(max=3, ...)`). Recovery lattice for the commit↔enqueue crash window: `src/services/domain_recompute_audit.py`, `GET /api/v1/ops/domain-recompute` (Phase 8A). |
| 13 | `absorption_daily` with `calculator='domain_units_deals'` | **IMPLEMENTED**, never used in production reads | Written only by `DomainAbsorptionCalculatorService.persist` (never auto-called from the sync flow — Phase 6/7 deliberately leaves it uncalled in production; only jobs/scripts/tests call it). Dashboard defaults never read it (`PRODUCTION_CALCULATOR = CALCULATOR_LEGACY`, `src/api/inventory.py:41`). |
| 14 | Reconciliation — internal scope | **IMPLEMENTED**, self-consistency only | `ReconciliationService.run(scope="internal")` — 9 checks, `src/services/reconciliation.py:148-158`. Proves the mirror is self-consistent; proves **nothing** about the real CRM (module docstring, reconciliation.py:1-16). |
| 15 | Reconciliation — snapshot scope | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** | Adds 3 checks + `SnapshotGate` (`src/services/snapshot_gate.py`) tombstone-by-omission with a hard safety floor `max(5%, 25)` absent records, requiring human override past that floor. |
| 16 | Reconciliation — source scope | **DESIGNED BUT NOT IMPLEMENTED** (target of the pending Phase 8F plan); currently **refused outright** | `src/api/reconciliation.py:101-113` returns `422 UNSUPPORTED_RECONCILIATION_SCOPE` for `scope='source'` unconditionally. `ReconciliationService.run(scope="source")` is not dispatched to any check at all — the dispatch tuple never includes source-total checks. |
| 17 | Parallel-run capture (comparison history) | **IMPLEMENTED WITH SYNTHETIC FIXTURES ONLY** | `calculator_comparisons` table (migration `0013`), `ParallelRunCaptureService` (`src/services/parallel_run.py`), scheduled job (`enqueue_parallel_run_capture`, `src/scheduler.py:39-50`, cron `30 3 * * *`). Every row so far derives from synthetic seed data. |
| 18 | Difference classification | **IMPLEMENTED** (pure function, deterministic); classifies only synthetic history so far | `src/services/comparison_rules.py`. Six classes, `unexplained`/`anomaly`/`definition_drift` block by default. |
| 19 | Cutover gate (14-day evidence gate + human sign-off) | **DESIGNED BUT NOT IMPLEMENTED** | Referenced throughout (`docs/crm/parallel_run.md`, `activation_prerequisites.md` B11) as Phase 8G. No gate-evaluation code exists in `src/` yet — confirmed no `cutover` module under `src/services/` or `src/api/`. |
| 20 | Dashboard cutover | **NOT PLANNED** for this phase, and structurally blocked | No code path writes `projects.absorption_calculator` (verified by grep, see Part 0). `docs/crm/activation_prerequisites.md` lines 87-102 lists the six simultaneous conditions required first. |

---

## Part 2 — Database inventory

| Table | Role | Source of truth | Writer | Main readers | Key identity | Derived? | Current status |
|---|---|---|---|---|---|---|---|
| `projects` | Master data: projects | This app | `ProjectService` (`src/services/projects.py`), API `POST /projects` | Dashboard, uploads, sync | `id` (UUID PK) | No | Live, in use |
| `areas` | Master data: sub-areas | This app | `ProjectService`, `POST /areas`; also target of Excel `areas` template | Dashboard, import, projection | `id` PK; natural key `(project_id, area_name, unit_type)` unique | No | Live, in use |
| `upload_files` | Batch record — **shared** by file uploads and JSON sync (`transport_mode`) | This app | `src/api/files.py` (create), `ImportService`, `SyncRunService` (update status) | Status polling, reconciliation, ops audit | `id` PK; batch identity `(source_system, source_instance_id, external_batch_id)` partial-unique when API-pushed | No (raw batch metadata) | Live |
| `upload_errors` | Per-row/per-record error detail | This app | `ImportService`, `SyncRunService._persist_errors` | `/files/{id}/errors`, `/sync-runs/{id}/errors`, reconciliation `_check_rejected_records` | `id` PK; `file_id` FK | No | Live |
| `sales_records` | Aggregate sales fact, from Excel/CSV | Excel/CSV file | `ImportService` | `AbsorptionCalculatorService`, `AreaService.summary` | `id` PK; business key `(area_id, sold_date, external_record_id)` unique | No (raw import) | Live, legacy production path |
| `inventory_snapshots` | Aggregate inventory fact, from Excel/CSV | Excel/CSV file | `ImportService` | `AreaService.list_areas/summary` | `id` PK; `(area_id, snapshot_date, snapshot_type)` unique | No | Live |
| `absorption_daily` | Daily absorption metrics, **two coexisting lineages** | Derived — recomputed, never hand-edited | `AbsorptionCalculatorService.recompute` (`legacy_aggregate`), `DomainAbsorptionCalculatorService.persist` (`domain_units_deals`, never auto-invoked in prod) | Dashboard, forecast (future), parallel-run comparator | `id` PK; `(area_id, stat_date, calculator)` unique since `0012` | **Yes** — fully derived | Live for `legacy_aggregate`; `domain_units_deals` code-complete but 0 rows in dev DB (verified: `SELECT calculator, count(*) FROM absorption_daily` → `legacy_aggregate\|360`, no `domain_units_deals` rows) |
| `crm_source_records` | Source-identity ledger: what has this instance told us, at what version | This app (mirrors CRM's claims) | `SourceIdentityService.apply` | Reconciliation (all checks), `SyncRunService` (idempotency) | `id` PK; `(source_system, source_instance_id, source_entity, source_record_id)` unique | No — raw acceptance record | Live, 0 rows in dev DB (no real batches ever accepted) |
| `units` | Domain mirror of CRM units — one-way copy | **CRM** (future); app owns only `deleted_at`/timestamps | `DomainProjector._project_unit` | `DomainAbsorptionCalculatorService`, `/inventory`, reconciliation | `id` PK; `(source_instance_id, external_unit_id)` unique; `(area_id, unit_code) WHERE deleted_at IS NULL` unique | No — mirror, not derived | Structurally live, 0 rows in dev DB |
| `deals` | Domain mirror of CRM deals | **CRM** (future) | `DomainProjector._project_deal` | Same as `units` | `id` PK; `(source_instance_id, external_deal_id)` unique; `(unit_id) WHERE status IN ('reserved','sold') AND deleted_at IS NULL` unique | No — mirror | Structurally live, 0 rows |
| `sync_credentials` | Machine-to-machine API keys | This app | `SyncCredentialService.issue` | Auth on `/sync/*` and `/reconciliation/runs` | `id` PK; `key_hash` unique globally; bound to one `source_instance_id` | No | Live, 0 rows in dev DB (no credential issued outside tests) |
| `sync_payloads` | Raw payload retention, one per batch | This app (verbatim capture) | `SyncPayloadService.store`, inside the same transaction as `upload_files` creation | Replay (`/sync-runs/{id}/reprocess`), conformance harness, incident investigation | `id` PK; `sync_run_id` unique (1:1 with `upload_files`); `ON DELETE RESTRICT` (0010, deliberately hardened) | No | Live, 0 rows |
| `reconciliation_runs` | One row per reconciliation execution | This app | `ReconciliationService.run` | `/reconciliation/runs/{id}`, ops review | `id` PK; `project_id` FK | No — a log of a computation, not a fact table | Live, 0 rows |
| `reconciliation_findings` | Per-finding detail, machine-readable | This app | `ReconciliationService._persist_findings` | `/reconciliation/runs/{id}/findings` | `id` PK; `reconciliation_run_id` FK (`ON DELETE CASCADE`) | No | Live, 0 rows |
| `calculator_comparisons` | Observation history: legacy vs. domain calculator, per capture | Derived observational data — never a source for anything else | `ParallelRunCaptureService.capture` (INSERT-only) | `/parallel-run/{id}`, `/parallel-run/{id}/verdicts`, future Phase 8G gate | `id` PK; index on `(project_id, compared_at DESC)` | Yes — purely observational, append-only | Live, 0 rows |
| `forecasts` | Prophet forecast output | This app (future — MVP2) | Not yet implemented (`src/jobs/forecast.py:33` — `# TODO (MVP 2): đọc absorption_daily → Prophet → sellout_date + CI 90%`) | Dashboard (planned) | `id` PK; `(job_id, area_id)` unique | Yes — would be derived from `absorption_daily` | **NOT IMPLEMENTED.** Table exists (migration `0001`), 6 rows in dev DB are from `scripts/seed_dev.py` synthetic seed data, not from any real forecast run. `run_daily_forecast` is a stub that does nothing (`src/jobs/forecast.py:16-53`). |

**Tombstone/delete convention, consistent across the domain-mirror tables** (`units`, `deals`, `crm_source_records`): soft-delete only, via `deleted_at`. No sync path ever issues a hard `DELETE`. A later `upsert` for the same identity revives the row (`domain_projection.py:267-269`). Confirmed no physical delete anywhere in `src/services/domain_projection.py`, `source_identity.py`.

---

## Part 3 — Mini CRM input contract

The contract is `src/contracts/crm_sync_v1.schema.json` (JSON Schema 2020-12) plus `docs/crm/sync_contract_v1_draft.md` (the human-readable draft, explicitly marked **DRAFT**). Field names here are the receiver's canonical names; a real CRM's own field names are **not known** and are not invented below — every CRM-specific mapping is marked TBD.

### 3A. Envelope-level fields

| Field | Type | Required? | Meaning | Validation | Target | Status |
|---|---|---|---|---|---|---|
| `schema_version` | integer, `const: 1` | Yes | Contract version | Rejected outright if not `1` (`crm_sync_v1.schema.json:23-26`) | `upload_files.schema_version` | IMPLEMENTED |
| `source_system` | string, `^[a-z0-9_]+$`, ≤64 | Yes | CRM product type, e.g. `mini_crm` | Regex + length (schema.json:28-34) | `upload_files.source_system` | IMPLEMENTED |
| `source_instance_id` | string, `^[a-z0-9_-]+$`, ≤64 | Yes | One deployment/installation; isolation boundary | Regex + length; bound to exactly one credential (`0008_sync_credentials.py` docstring) | `upload_files.source_instance_id` | IMPLEMENTED |
| `external_batch_id` | string, ≤128 | Yes | CRM-assigned batch identity; resend = idempotent replay | Non-empty; batch identity index `uq_upload_files_source_batch` (partial, `external_batch_id IS NOT NULL`) | `upload_files.external_batch_id` | IMPLEMENTED |
| `sync_mode` | enum `incremental`\|`full_snapshot` | Yes | Delta vs. complete-state-of-scope | Determines whether `snapshot` block is required (`allOf` in schema.json:76-101) | `upload_files.sync_mode` | IMPLEMENTED |
| `project_ref` | object, `{project_id: uuid}` only | Yes | Which existing project this batch belongs to | `oneOf` restricted to `project_id` only — **`project_code` is explicitly not accepted** because `projects` has no such column (schema.json:120-137, `docs/crm/sync_contract_v1_draft.md` §3.1) | `upload_files.project_id` (via `contract_adapter._project_id`) | IMPLEMENTED for `project_id`; project-code mapping is a **BLOCKED** item (A5/§3.1 identity blocker — the future CRM must either learn our UUIDs or we must add a code→id mapping, neither exists) |
| `source_extracted_at` | timestamp, tz-required | Yes | When the CRM extracted this data (not send time, not receive time) | ISO-8601 with offset, rejected if naive (`_parse_timestamp`, `json_payload.py:251-271`, contract §6) | Not persisted as a distinct column today — **gap**: `upload_files` has no `source_extracted_at` column; only `uploaded_at` (receive time) is stored. **MISSING** in Layer-1 output. | IMPLEMENTED in validation only; **not stored** |
| `snapshot` | object (see below) | Required iff `sync_mode='full_snapshot'`; forbidden otherwise | Snapshot completeness metadata | `allOf` enforces the either/or (schema.json:76-101); `_parse_snapshot` re-checks (`json_payload.py:391-437`) | `upload_files.snapshot_id/chunk_index/chunk_total/snapshot_complete/snapshot_scope` (migration `0011`) | IMPLEMENTED |
| `records` | array, 0–5000 items | Yes | The batch payload | `maxItems: 5000` (schema.json:69); size gate additionally caps the whole request body at 5 MB (`sync_payloads.py:55`) | `crm_source_records` + `units`/`deals` per record | IMPLEMENTED |

**Batch identity & retry:** identity is `(source_system, source_instance_id, external_batch_id)`. A resend of the same triple returns the prior result verbatim, no reprocessing (`sync_runs.py:276-293`). This is what makes network-level retry safe.

**Source-instance isolation:** enforced twice — at the credential layer (`sync_credentials.source_instance_id`, `INSTANCE_MISMATCH` → 403) and at the data layer (every mirror row and every `crm_source_records` row carries `source_instance_id`, and all uniqueness/lookups are scoped by it).

**Incremental vs. full snapshot:** `incremental` = only changed records, no absence inference. `full_snapshot` = declares completeness over a `scope`; absent-record tombstoning is possible but **only through a separate, explicit `POST /reconciliation/runs {scope:"snapshot"}` call** (`src/api/reconciliation.py`, `ReconciliationService._reconcile_snapshot`) — **ingesting a `full_snapshot` batch by itself never tombstones anything**. This two-step design (ingest, then separately reconcile-and-maybe-tombstone) is a fact worth being explicit about: it is easy to assume snapshot ingestion is self-tombstoning, and it is not.

**Chunking:** only defined for `full_snapshot` (`chunk_index`/`chunk_total`/`snapshot_complete`, all validated together-or-not-at-all — `ck_upload_files_snapshot_fields_together`, migration `0011`). `incremental` batches have **no** chunking mechanism in the contract; a logically large incremental change must be split into independent batches with distinct `external_batch_id`s, each under the 5000-record / 5 MB caps.

**Snapshot completion:** `snapshot_complete: true` on the terminal chunk, **and** `SnapshotGate.completeness()` (`src/services/snapshot_gate.py:135-170`) requires all `chunk_total` indices to actually be present (not just the flag) before treating a snapshot as complete. An incomplete snapshot **never** triggers tombstoning — this is the single most heavily documented safety property in the reconciliation code (`snapshot_gate.py:1-28`).

**Explicit vs. inferred deletion:** `operation: "delete"` on a record is explicit (soft-delete, `deleted_at` set). Inferred deletion (absence from a complete snapshot) only fires through `SnapshotGate`, gated by `max(5% of live records, 25 records)` — beyond that, requires `override_safety_gate: true` from a human caller, itself recorded as a finding.

**Source revision watermark:** at the **record** level, `source_revision` (monotonic integer, preferred) or `source_updated_at` (timestamp) — required on every record including deletes (`MISSING_SOURCE_VERSION` if both absent). At the **batch/instance** level, there is **no implemented watermark concept**. The S2-dialect-only field `source_cursor` (`json_payload.py:336-338`, stored as `upload_files.last_source_cursor`) is captured verbatim and exposed via `GET /sync-runs/{id}`, but it is **not part of the v1 contract schema** (absent from `crm_sync_v1.schema.json`'s `additionalProperties:false` top level) and is **never validated for monotonicity or used by any check** — confirmed by `grep -rn last_source_cursor src/` showing only storage/exposure, no comparison logic anywhere. This is the exact gap Phase 8F's proposed `source_revision_watermark` (in the not-yet-implemented source-totals contract) is meant to fill.

**Timezone requirements:** every timestamp in the contract must carry an explicit UTC offset; naive timestamps are rejected (`_parse_timestamp`, `json_payload.py:268-269`; same rule independently enforced in `domain_projection._optional_timestamp`, lines 126-134).

**Payload size limits:** 5 MB per request body (`MAX_PAYLOAD_BYTES`, `sync_payloads.py:55`), measured on raw bytes **before** JSON parsing (`src/api/sync.py:163-184`); 5000 records per batch (schema.json:69).

### 3B. Unit record fields

| Field | Why the backend needs it | Target | Used for | Missing/invalid behavior | Owner |
|---|---|---|---|---|---|
| `entity` (`"unit"`) | Routes to the unit branch of the `record` schema and to `DomainProjector._project_unit` | Dispatch only, not persisted verbatim | Identity/dispatch | Not `"unit"`/`"deal"` → schema rejects the whole envelope | System (contract) |
| `operation` (`upsert`\|`delete`) | Decides insert/update vs. tombstone | Drives `SourceIdentityService` decision | Projection | Unrecognized value → `UNSUPPORTED_OPERATION`, record rejected, batch continues | CRM |
| `external_id` | Stable CRM-side identity; **never reused across entities** (A1, unanswered) | `crm_source_records.source_record_id`, `units.external_unit_id` | Identity, reconciliation | Missing → `MISSING_SOURCE_RECORD_ID`, record rejected | CRM |
| `source_revision` **or** `source_updated_at` | Only accepted basis for version ordering; `payload_hash` is never used for ordering | `crm_source_records.source_revision`/`source_updated_at`, `units.source_revision`/`source_updated_at` | Ordering, analytics freshness | Neither present → `MISSING_SOURCE_VERSION`, record rejected | CRM |
| `payload_completeness` (`full`\|`partial`, default `full`) | Disambiguates "field absent = unchanged" (partial) from "field absent = drop it" (full, rejected loudly) | Drives `history_guard.merge_record`/`guard_history` | Data-loss prevention | Unrecognized value → `UNSUPPORTED_PAYLOAD_COMPLETENESS` | CRM |
| `payload.area_ref` | Resolves which `areas.id` this unit belongs to | `units.area_id` (via lookup) | Identity, analytics grain | Unresolvable → `UNKNOWN_AREA`, record rejected — **never auto-creates an area** | CRM (content), system (resolution) |
| `payload.unit_code` | Human-facing unit code; unique-per-area among live units | `units.unit_code` | Identity, display | Missing/blank → `MISSING_FIELD` | CRM |
| `payload.unit_status` | Verbatim source status → mapped to canonical `units.status` | `units.status` (mapped) | Analytics, forecasting grain | Unmapped value → `UNKNOWN_UNIT_STATUS`, **rejected, never guessed** | CRM |
| deleted marker | `operation: "delete"` at the record envelope level (there is no per-field "deleted" flag inside `payload`) | `units.deleted_at` | Tombstone | N/A | CRM |

### 3C. Deal record fields

| Field | Why needed | Target | Used for | Missing/invalid | Owner |
|---|---|---|---|---|---|
| `entity` (`"deal"`) | Dispatch | — | — | Same as unit | System |
| `operation` | Insert/update/tombstone | — | Projection | Same as unit | CRM |
| `external_id` | Stable identity | `deals.external_deal_id` | Identity | Same as unit | CRM |
| `source_revision`/`source_updated_at` | Ordering | `deals.source_revision`/`source_updated_at` | Ordering | Same as unit | CRM |
| `payload_completeness` | Same disambiguation, deal-specific: applies to `reserved_at`/`sold_at`/`lost_at` | — | History preservation | Same as unit | CRM |
| `payload.external_unit_id` | Deal must attach to an **already-mirrored** unit — unit-before-deal is a hard dependency, not just an ordering hint | `deals.unit_id` (via lookup) | Referential integrity | Unit not yet in mirror → `UNKNOWN_UNIT_REFERENCE`, record rejected. **Contract §11 requires all `entity=unit` records to precede all `entity=deal` records within one batch**; a CRM that cannot guarantee this per A8 (unanswered) needs a holding buffer that does not exist in this repo | CRM |
| `payload.deal_status` | Verbatim status → mapped to canonical `deals.status`, original kept in `deals.source_status` | `deals.status` (mapped), `deals.source_status` (verbatim) | Analytics, definition-drift detection | Unmapped → `UNKNOWN_DEAL_STATUS`, rejected. Only `cancelled`/`canceled` → `lost` is a built-in alias (`domain_projection.py:51`) | CRM |
| `payload.reserved_at`/`sold_at`/`lost_at` | History timestamps A4 depends on | `deals.reserved_at`/`sold_at`/`lost_at` | Absorption analytics (reservation date is the entire point of A4) | See below | CRM |

**Full vs. partial semantics** (`docs/crm/activation_prerequisites.md` A4 section; implemented in `history_guard.py`):

| completeness | field state | mirror has value | outcome |
|---|---|---|---|
| full | absent | yes | **rejected**, `HISTORY_TIMESTAMP_DROPPED` |
| full | absent | no | accepted, stays empty |
| full | explicit `null` | any | accepted, cleared, `warning` logged |
| partial | absent | any | **preserved** (merged from mirror before hashing) |
| partial | explicit `null` | any | cleared |
| either | `null` on a non-nullable mapped field | — | rejected, `NULL_NOT_ALLOWED` |
| partial | no prior mirror row exists | — | rejected, `PARTIAL_UPDATE_WITHOUT_BASE` |

**`HISTORY_TIMESTAMP_DROPPED`**: a `full` update that omits a timestamp the mirror currently holds. Only fires for `decision == "update"` with an existing mirror row (`history_guard.py:294-296`) — never for first-sight `insert`. Escape hatch `SYNC_PRESERVE_DROPPED_TIMESTAMPS` (default **off**) copies the old value forward with a `warning` log instead of rejecting — and per `src/config.py:62-66`, enabling it is documented to require the cutover gate to fail.

**`PARTIAL_UPDATE_WITHOUT_BASE`**: a `partial` record for an identity with no prior accepted row. There is nothing to merge against, so the record is rejected rather than silently treated as complete.

**Status mapping**: no default/guess ever occurs. An unrecognized source status is rejected, never coerced to a "safe" value — the contract doc calls out explicitly (§7) that defaulting an unmapped status to `available` "biến một căn đã bán thành căn còn trống" (turns a sold unit into an available one) — the single most dangerous failure mode identified.

**Timestamp preservation**: see A4 table above; enforced only on `update` decisions.

**Deal tombstone behavior**: same as units — `deleted_at` set, never physically removed; a later `upsert` revives it (`SourceIdentityService._accept`, `source_identity.py:244-283`, explicit comment that resurrection is intentional).

**Why customer/PII/pricing fields are out of scope**: the domain model (`units`, `deals`) has no columns for them — `src/models/tables.py:226-263` lists every column on both tables, and none is customer-, contact-, price-, commission-, or agent-related. The contract schema's `additionalProperties: false` on every payload object (`unit_payload`, `deal_payload`, and their `_partial` variants) means such fields, if sent, would fail schema validation outright rather than being silently accepted and dropped.

### 3D. Fields explicitly not accepted

Not modeled anywhere in `units`/`deals`, and would be rejected by `additionalProperties: false` if sent inside a record `payload`:

- Customer identity / name
- Personal contact data (phone, email, address)
- Pricing, commission
- Contract number
- Payment schedule
- Sales-agent personal data

**Unknown-field handling and hashing.** Because `additionalProperties: false` is set on `unit_payload`/`deal_payload`/`unit_payload_partial`/`deal_payload_partial`, an unknown field in a `payload` object causes **schema rejection of the whole record at Gate 3**, not silent ignoring — a payload with an unmodeled field never reaches the hashing/conflict-detection layer at all. Separately, and defensively, even if a field reached that layer, `payload_fingerprint`/`mapped_view` (`json_payload.py:187-237`) hash only the explicit `MAPPED_FIELDS` allow-list (`units`: `area_id, area_name, unit_type, unit_code, status`; `deals`: `external_unit_id, status, reserved_at, sold_at, lost_at`) — an unmodeled field could never affect the hash or trigger a false conflict even in a hypothetical relaxed-schema future.

---

## Part 4 — Exact output for analytics and AI forecasting

### Layer 1 — Canonical normalized output

**Unit output** (`units` table, `src/models/tables.py:226-242`):

| Field | Status |
|---|---|
| `project_id` | **DERIVED** — not a column; resolved via `units.area_id → areas.project_id` join. No code path stores it redundantly (deliberate, per `0007` migration docstring: "thêm nữa là mở đường cho hai giá trị mâu thuẫn") |
| `area_id` | CURRENTLY STORED |
| `external_unit_id` | CURRENTLY STORED |
| `unit_code` | CURRENTLY STORED |
| `unit_type` | CURRENTLY STORED |
| `status` | CURRENTLY STORED (canonical, mapped) |
| `deleted_at` | CURRENTLY STORED |
| `source_system` | CURRENTLY STORED |
| `source_instance_id` | CURRENTLY STORED |
| `source_revision` | CURRENTLY STORED (nullable) |
| `source_updated_at` | CURRENTLY STORED (nullable) |
| `first_seen_at` | **MISSING** on `units` itself — the closest equivalent is `crm_source_records.first_seen_at`, a **separate** table keyed by `(source_system, source_instance_id, source_entity, source_record_id)`, joinable but not denormalized onto `units` |
| `last_seen_at` | Same as `first_seen_at` — lives on `crm_source_records`, not on `units`. `units.updated_at` (present) is a close but **not identical** proxy: it updates on every accepted write, whereas `last_seen_at` on `crm_source_records` updates even on `skip_stale`/`duplicate_noop`/`conflict` (touches that don't change the mirror) |

**Deal output** (`deals` table, `src/models/tables.py:244-263`):

| Field | Status |
|---|---|
| `project_id` / resolvable project scope | **DERIVED**, two joins away: `deals.unit_id → units.area_id → areas.project_id` |
| `unit_id` | CURRENTLY STORED |
| `external_deal_id` | CURRENTLY STORED |
| `external_unit_id` | **DERIVED** — not a column on `deals`; must join to `units.external_unit_id` via `unit_id` |
| `status` | CURRENTLY STORED (canonical) |
| `source_status` | CURRENTLY STORED (verbatim) |
| `reserved_at`/`sold_at`/`lost_at` | CURRENTLY STORED (nullable, history-guarded) |
| `deleted_at` | CURRENTLY STORED |
| `source_system`/`source_instance_id` | CURRENTLY STORED |
| `source_revision`/`source_updated_at` | CURRENTLY STORED (nullable) |
| `first_seen_at`/`last_seen_at` | **MISSING** on `deals` itself, same as units — only on `crm_source_records` |

### Layer 2 — Daily analytics output (`absorption_daily`)

| Field | Meaning |
|---|---|
| `project_id` | **MISSING as a column** — the table has `area_id` only; project is one join away via `areas.project_id`. No denormalized `project_id` on `absorption_daily` (`src/models/tables.py:193-212`) |
| `area_id` | CURRENTLY STORED |
| `stat_date` | CURRENTLY STORED |
| `units_sold` | CURRENTLY STORED, both lineages |
| `units_remaining` | CURRENTLY STORED, **nullable** — `NULL` for any row the producing calculator structurally cannot compute it for (never `0` as a stand-in) |
| `units_reserved` | CURRENTLY STORED, **nullable** — always `NULL` for `legacy_aggregate` (no concept of "reserved" in aggregate CSV data), populated for `domain_units_deals` |
| `calculator` | CURRENTLY STORED — `legacy_aggregate` \| `domain_units_deals`, `NOT NULL` |
| `computation_id` | CURRENTLY STORED, **nullable** (added `0012`; legacy rows written before `0012` are `NULL`; every row written since carries one UUID per computation run) |
| `computed_at` | CURRENTLY STORED |
| `provenance` beyond `calculator`/`computation_id` | **MISSING** — no column records *which sync run* or *which record set* produced a given `domain_units_deals` row. Traceable only indirectly: `computed_at` bounds the window, and `crm_source_records.last_seen_at` can be cross-referenced by time, not by a stored FK |
| `data_quality_status` | CURRENTLY STORED — `'ok'`\|`'warning'` (warning during the first `LONG_WINDOW=30` days per area, insufficient history for the 30-day rolling window; see `absorption.py:172-174`) |

**Meaning of NULL vs. 0 vs. missing row vs. lineage:**
- `units_reserved IS NULL` = "this calculator cannot compute this metric" (always true for `legacy_aggregate`). `units_reserved = 0` = "this calculator computed it, and zero units are held."
- A missing `(area_id, stat_date, calculator)` row = no computation was run for that lineage on that day at all — distinct from a computed `0`.
- `calculator = 'legacy_aggregate'`: derived from `sales_records`+`inventory_snapshots` (Excel/CSV aggregates). `calculator = 'domain_units_deals'`: derived from `units`+`deals` (per-unit CRM mirror), **never automatically written in production** — only by explicit script/job calls, never triggered by the dashboard read path.

**Known limitation, stated plainly by the repo's own comments** (`src/services/domain_absorption.py:76-79, 296-306`): historical `units_reserved` and the reservation-adjusted portion of `units_remaining` are **approximate** for `domain_units_deals` — `deals` carries no per-day event log, so "held today" is computed from the *current* live reservation count and applied uniformly across the entire historical date range, not reconstructed day-by-day. This is a named, accepted approximation (`comparison_rules.py` class `approximation`), not a bug — but it means `domain_units_deals` historical `units_reserved`/`units_remaining` values for past dates are **not exact** even once real CRM data exists, unless a future `unit_status_events` table (explicitly deferred, per multiple docstrings — "quyết định 3 — hoãn `unit_status_events`") is built.

### Layer 3 — AI/forecasting feature output

**Nothing in this layer is implemented.** No feature table, feature-store, or feature-serialization code exists anywhere in `src/`. `src/jobs/forecast.py` is a stub (lines 33-53) that logs start/finish and returns zero counts; `src/agents/graph.py` is the LangGraph skeleton wired to placeholder nodes (`src/agents/nodes/example_node.py`) with no absorption-specific logic. `prophet` is a declared dependency (`requirements.txt:28`) but `grep -rn "Prophet()" src/` finds no instantiation anywhere. Everything below is **PROPOSED**.

#### Required features for MVP forecasting (PROPOSED)

| Feature | Formula/source | Grain | Required input fields | Null meaning | Leakage risk | Status |
|---|---|---|---|---|---|---|
| `project_id`, `area_id`, `stat_date` | Direct / one join | daily × area | `absorption_daily.area_id`+join, `stat_date` | — | none | PROPOSED |
| `units_sold` | `absorption_daily.units_sold` | daily × area | same | 0 = no sales that day (already a real 0 today, both lineages) | none | PROPOSED (pass-through of an IMPLEMENTED column) |
| `units_remaining`, `units_reserved` | `absorption_daily.*` | daily × area | same | NULL = calculator can't compute (see Layer 2) | none if NULL rows excluded, else stale-inventory leakage | PROPOSED |
| `total_units` | `areas.total_units` (Excel path) or count of live `units` (domain path) — **the two are not reconciled today**; the repo has no code that checks `areas.total_units == count(live units in that area)` | area (mostly static) | `areas.total_units`, `units.status` | n/a | none | PROPOSED; **the two sources of "total inventory" can silently disagree — no check exists** |
| `sell_through_rate`, `absorption_rate` | `units_sold_cumulative / total_units` and variants | daily × area | above | undefined at `total_units=0` | must use only data available as-of `stat_date`, no future leakage | PROPOSED |
| `rolling_7d_units_sold`, `rolling_30d_units_sold` | Sum, not mean, over trailing window | daily × area | `absorption_daily.units_sold` history | — | **note:** existing `velocity_7d`/`velocity_30d` in `absorption_daily` are already rolling **means**, not sums — a forecasting feature named "rolling_Nd_units_sold" as a *sum* would be a **new, separate** computation, not a reuse of the stored `velocity_*` columns | PROPOSED |
| `rolling_7d_absorption`, `rolling_30d_absorption` | Rolling mean of the rate | daily × area | derived | — | same as above | PROPOSED |
| `days_since_last_sale` | `stat_date - max(sold date ≤ stat_date)` | daily × area | `deals.sold_at` or `sales_records.sold_date` | undefined before first sale | must be computed as-of, not from full history | PROPOSED |
| `active_unit_count`, `active_deal_count`, `sold_deal_count`, `reserved_deal_count` | `COUNT` over `units`/`deals` filtered by status and `deleted_at IS NULL` | daily × area (point-in-time) | `units.status`, `deals.status`, `deleted_at` | 0 is a real answer | **as-of-date point-in-time counts are not reconstructable from current `units`/`deals`** — they carry only current state plus `updated_at`, no event history; only the domain calculator's *current-day* snapshot logic exists (`domain_absorption.py`), not a historical per-day unit-state reconstruction | PROPOSED, and **historically blocked without an event log** (same `unit_status_events` gap noted in Layer 2) |
| `cancellation`/`lost` count | `COUNT(deals) WHERE status='lost'` | daily × area | `deals.status`, `deals.lost_at` | 0 is real | none | PROPOSED |
| `source_data_as_of` | Batch-level `source_extracted_at` | per batch, would need to be attached to feature rows | **`upload_files` does not store `source_extracted_at`** (see Part 3A gap) | — | — | PROPOSED, and currently **BLOCKED** by the missing storage column noted above |
| `data_freshness_minutes` | `now - source_extracted_at` (or, as available today, `now - upload_files.finished_at`) | per read | same gap as above | — | — | PROPOSED |
| `data_quality_status` | `absorption_daily.data_quality_status` (exists) + reconciliation `passed` (exists) combined | daily × area | both already stored | — | — | PROPOSED composition of two IMPLEMENTED signals |
| `feature_version` | New concept | — | — | — | — | PROPOSED, **no equivalent exists anywhere in the schema today** (contrast: `forecasts.feature_version` exists as a column but is never written by any code — the table exists, the writer does not) |

#### Useful future features (PROPOSED unless noted)

| Feature | Formula/source | Grain | Required input fields | Null meaning | Leakage risk | Status |
|---|---|---|---|---|---|---|
| Deal-stage duration | `time between consecutive deal.status transitions` | per deal | Requires a status-transition log — **`deals` stores only the current status plus three named timestamps (`reserved_at`/`sold_at`/`lost_at`), not a generic transition log** for the pre-reservation stages (`lead`→`qualified`→`interested`→`viewing`) | — | — | PROPOSED — **structurally blocked** without a new event table; not something the current `deals` schema can produce for the early funnel stages |
| Reservation-to-sale duration | `sold_at - reserved_at` | per deal | Both columns already exist and are populated when present | undefined if either missing (common — a unit can be sold directly without a recorded reservation) | none | PROPOSED, buildable **today** from existing columns, once real data exists |
| Sales velocity trend | Trend of `velocity_30d` over time | area | `absorption_daily.velocity_30d` (exists) | — | must not use future rows | PROPOSED |
| Historical cancellation rate | `lost / (sold + lost)` over a window | area | `deals.status`, `deals.lost_at` | undefined with zero denominator | none | PROPOSED |
| Area-level conversion | `sold / (reserved + sold + lost)` | area | `deals.status` | undefined with zero denominator | none | PROPOSED |
| Project-level seasonality | Calendar features on `stat_date` | project | `absorption_daily.stat_date` | n/a | none | PROPOSED |
| Inventory aging | `stat_date - units.created_at` for still-available units | area/unit | `units.created_at`, `units.status` | n/a | must be as-of, not current | PROPOSED |
| Source coverage | `count(live units) / declared total (if CRM ever publishes one)` | project | Requires the not-yet-built source-totals contract (Phase 8F) | — | — | PROPOSED, **BLOCKED by real Mini CRM** (needs a real source-totals feed) |
| Data completeness | `payload_completeness` distribution, rejection rate | project | `upload_errors`, `sync_runs` summaries (exist) | — | — | PROPOSED, buildable from existing tables |
| External market signals | Any external data source | — | Not present anywhere in this repo | — | — | PROPOSED, **entirely out of current scope** — no external-data ingestion path exists |

No customer PII, free-text notes, or sensitive fields are proposed anywhere above — consistent with Part 3D and the fact that no such fields exist in the domain tables to draw from.

#### Target variables for future forecasting (PROPOSED, none implemented)

- Future `units_sold` over the next 7 days
- Future `units_sold` over the next 30 days
- Future `units_remaining`
- Time-to-sell (per unit or per area)
- Probability of deal conversion (reserved → sold)

None of these appear anywhere in `src/` — `forecasts.velocity_forecast`/`pred_lower`/`pred_upper`/`sellout_date`/`confidence_label` columns exist in the schema (migration `0001`) but are **never written**; the only writer of `forecasts` rows in the entire repository is `scripts/seed_dev.py`'s synthetic seed data (`scripts/seed_dev.py:522-`), not a real forecast job.

---

## Part 5 — AI data quality and lineage requirements

| Metadata field | Exists today? | Where |
|---|---|---|
| `source_system` | Yes | `upload_files`, `crm_source_records`, `units`, `deals` |
| `source_instance_id` | Yes | Same tables |
| `source_revision` | Yes, nullable | `crm_source_records`, `units`, `deals` |
| `source_updated_at` | Yes, nullable | Same |
| `source_extracted_at` | **No** — validated on ingest, never persisted | Contract-only field, gap noted in Part 3A |
| `recorded_at` (when this system first saw/stored a fact) | Partial — `crm_source_records.first_seen_at`/`last_seen_at` cover the source-record ledger; `units.created_at`/`updated_at` and `deals.created_at`/`updated_at` cover the mirror rows; `absorption_daily.computed_at` covers derived metrics. No single unified `recorded_at` concept spans all three | Multiple tables |
| `computed_at` | Yes | `absorption_daily` |
| `calculator` | Yes | `absorption_daily` |
| `computation_id` | Yes, nullable (post-`0012` rows only) | `absorption_daily` |
| `feature_version` | **No** implementation; column exists unused on `forecasts` | See Layer 3 |
| `schema_version` | Yes, per batch | `upload_files.schema_version` |
| `reconciliation_status` | Yes, per run, not per row | `reconciliation_runs.passed`/`status` — **not attached to individual `units`/`deals` rows**, only to a project-level run |
| `data_quality_status` | Yes, per `absorption_daily` row only | `absorption_daily.data_quality_status` — **not present** on `units`/`deals` |
| stale flag | Partial — `domain_recompute_audit` (Phase 8A) detects staleness of the *domain lineage as a whole*, exposed via `GET /api/v1/ops/domain-recompute` | `src/services/domain_recompute_audit.py` — this is an operational health signal, not a per-row or per-feature freshness flag |
| completeness flag | Yes, per record, at ingest time (`payload_completeness`); not retained as a per-row column on `units`/`deals` after projection | `crm_source_records`/ingest path only |
| rejected_record_count | Yes, per batch | `upload_files.rows_failed`, `error_summary.errors` breakdown by category |
| source_checksum | Partial — `sync_payloads.payload_sha256` covers the whole raw payload of a batch; there is **no per-record or per-entity-set checksum** (the exact thing Phase 8F's proposed `external_id_checksum` would add, and does not exist yet) | `sync_payloads` |

**Why business timestamps must not be confused with system timestamps.** The repo enforces this distinction structurally in two independent ways: (1) `deals.reserved_at`/`sold_at`/`lost_at` are business facts owned by the CRM and protected by the A4 history guard, never derived from receive time; (2) `deals.created_at`/`updated_at` and `crm_source_records.first_seen_at`/`last_seen_at` are **this system's own clock**, set by `datetime.now(UTC)` at write time (`domain_projection.py`, `source_identity.py`), and are never compared against or substituted for the business timestamps in any version-ordering or absorption-date logic. `_compare` in `source_identity.py:90-112` explicitly never reads server receive time. Conflating the two would, for example, make `absorption_daily.stat_date` (derived from `deals.sold_at`, a business fact) drift to reflect when data arrived rather than when a sale happened — which is exactly the failure the codebase's naive-timestamp rejection (mandatory tz offset) exists to prevent one layer up.

**How the AI layer could determine freshness/origin/exactness today, and where it cannot:**
- *Fresh vs. stale*: possible for the domain-mirror lineage as a whole via the Phase 8A audit endpoint; **not possible per-row** — no row carries a "computed relative to what upstream state" pointer beyond `computed_at`'s wall-clock timestamp.
- *CRM vs. legacy origin*: fully determinable — `absorption_daily.calculator` is authoritative and DB-constrained (`ck_absorption_daily_calculator`).
- *Current vs. historical*: `stat_date` plus `computed_at` distinguish these for `absorption_daily`; for `units`/`deals` there is no historical state at all (current-state mirror only), so "was this unit's status X on date D" is **not answerable** without the deferred `unit_status_events` table.
- *Exact / approximate / unavailable / rejected*: partially answerable — `units_reserved IS NULL` = unavailable; `data_quality_status='warning'` = approximate (insufficient rolling-window history); rejected records are counted in `upload_files.rows_failed` and detailed in `upload_errors`, but a rejected record never reaches `absorption_daily` at all, so a consuming AI layer sees the aggregate rejection count only through a separate join to `upload_files`, not inline with the metric row.
- *Which calculator produced a metric*: fully answerable (`calculator` column, DB-constrained).
- *Which records were used to derive it*: **not answerable at the row level** — no FK from `absorption_daily` to the specific `deals`/`units` rows that fed a given `stat_date`/`area_id` computation. Traceable only approximately, by re-running the deterministic calculator against the mirror as of `computed_at`.

---

## Part 6 — Current status matrix

| Capability | Status | Evidence | Blocking reason | Next action |
|---|---|---|---|---|
| Excel/CSV ingestion | COMPLETE | `src/services/import_records.py`, `src/jobs/parse_upload.py`, live in dev DB (`sales_records`=360 rows, `inventory_snapshots`=72 rows) | — | — |
| JSON sync (receiver) | COMPLETE WITH SYNTHETIC FIXTURES ONLY | `src/api/sync.py`, `src/services/sync_runs.py`, 17 fixtures | No real Mini CRM to send real payloads | Await real CRM payloads (Nhóm C, `activation_prerequisites.md`) |
| Authentication | COMPLETE | `sync_credentials`, `SyncCredentialService`, `0008` | — | Issue a real credential only once a real `source_instance_id` exists |
| Payload retention | COMPLETE | `sync_payloads`, `0009`/`0010` hardening | — | — |
| Idempotency (batch + record) | COMPLETE | `sync_runs.py`, `source_identity.py`, tests `test_sync_idempotency.py` | — | — |
| Version ordering | COMPLETE | `source_identity._compare` | — | — |
| Conflict handling | COMPLETE | `SourceIdentityService._record_conflict` | — | — |
| Tombstones | COMPLETE | `domain_projection._tombstone`, `DECISIONS` set | — | — |
| Snapshot reconciliation (scope='snapshot') | COMPLETE WITH SYNTHETIC FIXTURES ONLY | `SnapshotGate`, fixtures 07/08 | Never run against a real multi-chunk CRM export | Same as above |
| Source reconciliation (scope='source') | NOT IMPLEMENTED | `src/api/reconciliation.py:101-113` unconditional refusal | No Mini CRM to publish source totals; no source-totals contract built yet (Phase 8F, planned not built) | Implement per the pending 8F plan once approved |
| Domain recomputation | COMPLETE | `domain_absorption.py`, enqueue in `sync_runs.py`, `Retry(max=3,...)` | — | — |
| Recovery audit (commit↔enqueue gap) | COMPLETE | `domain_recompute_audit.py`, `GET /ops/domain-recompute`, scheduled hourly | — | Verified via a deliberate crash-window drill per Phase 8A completion report (prior session) |
| A4 history guard | COMPLETE WITH SYNTHETIC FIXTURES ONLY | `history_guard.py`, fixtures 13-17 | Whether real Mini CRM preserves history at all is unanswered (A4 in Nhóm A) | Await CRM's answer |
| Conformance harness | COMPLETE | `src/services/conformance.py`, `scripts/conformance_check.py`, always-rollback transaction, row-count-verified | Accepts real payloads too (validation-only), but none have been fed to it | Run it against the first real payload, whenever one exists |
| Parallel-run capture | COMPLETE WITH SYNTHETIC FIXTURES ONLY | `calculator_comparisons` (`0013`), `ParallelRunCaptureService`, scheduled cron | All 0 rows so far derive from seed/test data | Needs real domain data to accumulate meaningful history |
| Difference classification | COMPLETE | `comparison_rules.py`, deterministic, tested | — | — |
| Source reconciliation | NOT IMPLEMENTED | Same as row above | Same | Same |
| Forecasting (Prophet/LangGraph) | NOT IMPLEMENTED | `src/jobs/forecast.py` is a stub; no `Prophet()` call anywhere in `src/` | MVP2 scope, not started | Build the actual Prophet pipeline (out of scope for Phase 8) |
| Dashboard cutover | BLOCKED | No write path to `projects.absorption_calculator` exists; `activation_prerequisites.md` lists 6 simultaneous gate conditions, none met | Phases 8G, Nhóm A/C all open | Do not attempt until 8G exists and passes |
| Mini CRM availability | OUT OF SCOPE (for this repository) | Explicit standing constraint across every phase of this project | This repo builds the receiver, not the CRM | N/A |

---

# What the current backend can do

- Accept, validate, deduplicate, and persist **aggregate** absorption data from Excel/CSV files, fully idempotently, with per-row error reporting and an error-rate rollback threshold — in continuous production use (360 `sales_records`, 72 `inventory_snapshots` rows in the dev database as of this assessment).
- Accept a **JSON sync payload** conforming to contract v1 over an authenticated, per-instance-isolated API; validate it against a JSON Schema; enforce record-level version ordering, idempotency, conflict detection, tombstoning, and a loud rejection of any update that would silently drop a `reserved_at`/`sold_at`/`lost_at` history timestamp.
- Mirror accepted CRM records into a normalized per-unit, per-deal domain model (`units`, `deals`), completely isolated from the legacy Excel/CSV path — the two calculators (`legacy_aggregate`, `domain_units_deals`) can write to the same `absorption_daily` table without colliding, and the domain lineage has never been read by the production dashboard.
- Run internal (self-consistency) and snapshot-scoped reconciliation checks against its own mirror, entirely without a real CRM, and record every finding in a structured, queryable form.
- Capture, on a schedule, a comparison between the two calculators and classify every difference by a fixed, pre-committed rule set that defaults unexplained differences to "blocking."
- Detect and self-heal (with a persistent alert either way) the specific crash window between a sync commit and its domain-recompute enqueue.
- Run a read-only conformance check of any payload — synthetic or real — against the exact code path production sync uses, inside a transaction that is provably always rolled back.

# What the future Mini CRM must send

- An envelope with `schema_version=1`, a declared `source_system`/`source_instance_id`, a stable `external_batch_id`, an explicit `sync_mode`, a `project_ref` **by UUID only** (no code-based project lookup exists), and a timezone-qualified `source_extracted_at`.
- For each unit: a stable, never-reused `external_id`; a monotonic `source_revision` or a tz-qualified `source_updated_at`; a resolvable `area_ref`; a verbatim `unit_status` string that the receiver's fixed mapping table recognizes (or the record is rejected, never guessed).
- For each deal: the same identity/version discipline; the target unit's `external_id`, which **must already be mirrored** (units strictly before deals within a batch); a verbatim `deal_status`; and, critically, **every currently-set history timestamp on every full update**, or an explicit `payload_completeness: "partial"` declaration if it intends to send only changed fields.
- Nothing about customers, pricing, contracts, or personal data — the schema does not accept it, by design.
- Eventually, for source reconciliation to become possible at all: some form of published totals (active/deleted counts, per-status counts, an ID-set checksum, a revision watermark) — none of which the receiver can demand today, because no source-totals contract has been built yet (that is precisely the pending Phase 8F work).

# What the AI/forecasting layer will receive

Today: nothing forecasting-specific. What exists is the raw material — `absorption_daily` rows with an explicit `calculator` label, nullable metrics that distinguish "not computable" from "computed as zero," and a `data_quality_status` flag for insufficient rolling-window history. There is no feature table, no feature versioning, no rolling-sum features (only rolling-mean `velocity_7d`/`velocity_30d`), no point-in-time historical unit/deal state, and no forecast target columns that are actually written by any code. Everything under "Layer 3" in Part 4 is a proposal to be built, not a description of what exists, and several of the proposed features (point-in-time deal-stage counts, per-unit inventory-aging using true historical state) are structurally blocked until a deal/unit event log — deferred by an explicit prior decision — is built.

# What is implemented versus proposed

**Implemented** (against synthetic fixtures, receiver-side only): the entire CRM sync receiver pipeline — auth, size/schema/business validation, idempotency, versioning, history guard, tombstoning, domain projection, domain recomputation, internal/snapshot reconciliation, parallel-run capture, deterministic difference classification, and a rollback-safe conformance harness.

**Proposed, not implemented**: source-scope reconciliation and its totals contract (Phase 8F); the cutover gate (Phase 8G); every AI/forecasting feature and target variable in Part 4 Layer 3; `source_extracted_at`/`feature_version`/per-record checksum storage; point-in-time historical unit/deal state; a unified `recorded_at` concept; project-code-based project resolution.

**Genuine gaps found during this review, not previously documented elsewhere in the repo:**
1. `area_ref` by `area_id` is schema-legal but rejected by `DomainProjector` (Part 1B, row 11) — no fixture exercises this path, so it has gone unnoticed until now.
2. Duplicate-file detection on the Excel/CSV path is application-level only since migration `0005`, not DB-enforced (Part 1A, step 3) — an intentional trade-off per the migration's own docstring, but worth flagging as a live race window.
3. `run_parse_upload`'s RQ enqueue carries no `Retry(...)`, unlike every sync-path enqueue — an asymmetry, not necessarily a bug.
4. `areas.total_units` (Excel-path inventory ceiling) and the live count of `units` rows (CRM-path inventory ceiling) are never checked against each other anywhere in the code.

# What is still blocked

- **Source reconciliation** (`scope='source'`) — refused unconditionally today; needs both a source-totals contract (Phase 8F, plan pending approval) and, to mean anything, a real Mini CRM publishing real totals.
- **A4** (history preservation) and every other Nhóm A question in `activation_prerequisites.md` — these require an answer from a Mini CRM team that does not exist yet; the receiver can only guarantee that a "no" answer surfaces loudly instead of silently.
- **Dashboard cutover** — structurally blocked (no write path to `absorption_calculator`), and gated behind Phase 8G plus every Nhóm A/B/C condition being simultaneously true.
- **14-day continuous parallel-run evidence** — infrastructure exists and is scheduled, but the clock cannot start meaningfully until there is real domain data (`domain_has_data=true` rows) to compare, and today there is none.
- **Any AI/forecasting capability** — no feature pipeline, no forecast job logic, no target-variable computation exists; this is unstarted work, not blocked-but-designed work.

# Minimal next steps

1. Decide and document the resolution to the `area_ref`-by-`area_id` gap (Part 1B row 11) before any real or synthetic payload attempts to use that contract-legal shape.
2. Proceed with the Phase 8F plan (source-totals schema, validator, checks) once approved, keeping the standing constraint that no source-totals persistence table is added unless the eventual real Mini CRM contract proves retention is required.
3. Build the Phase 8G cutover gate, reading `calculator_comparisons_gate` and the classification rules already in place — the hardest parts (deterministic classification, vacuous-match exclusion) are already built; the gate itself (14-day count, human sign-off recording) is not.
4. When forecasting work begins, treat Part 4 Layer 3 of this document as the starting proposal, not as a description of existing behavior — every field there needs its own design/implementation pass, and several are blocked on a deal/unit event log that has been deferred, not built.
5. Do not invent CRM field names, status values, or additional forecast features beyond what is written here; extend this document with newly discovered evidence, not with assumptions, as Phase 8F and later phases proceed.

---

# Đợt 2026-08-09 (b) — mô hình miền S3: `units`, `deals`, bộ tính hấp thụ mới

Tiếp ngay sau phần S2 bên dưới, cùng ngày. Phạm vi: **tầng miền S3 và chuẩn bị
chạy song song**. Toàn bộ dự án tích hợp CRM CHƯA hoàn thành.

**CRM vẫn là nguồn sự thật.** Đồng bộ một chiều CRM → ứng dụng; không có đường
ghi ngược nào. Ứng dụng chỉ sở hữu metadata soi gương (`deleted_at`, mốc ghi
nhận) và kết quả tính toán.

**Chưa cắt sang bộ tính mới.** Dashboard vẫn đọc bộ tính cũ.

## Migration

`alembic/versions/0007_s3_domain_model.py` (down_revision `0006_sync_foundation`).
Head hiện tại: `0007_s3_domain_model`.

| Bảng | Thay đổi |
|---|---|
| `units` | **Mới.** Danh tính nguồn `(source_instance_id, external_unit_id)` UNIQUE; `area_id` FK; `unit_code`, `unit_type`, `status`; phiên bản nguồn (`source_revision`, `source_updated_at`); `deleted_at`; mốc tạo/sửa. Partial unique `(area_id, unit_code) WHERE deleted_at IS NULL`. CHECK `status IN ('available','reserved','sold','blocked')` |
| `deals` | **Mới.** Danh tính nguồn `(source_instance_id, external_deal_id)` UNIQUE; `unit_id` FK; `status` + `source_status` (giá trị nguyên văn của hệ nguồn); `reserved_at`/`sold_at`/`lost_at`; phiên bản nguồn; `deleted_at`. CHECK trạng thái ↔ mốc thời gian. **Partial unique `(unit_id) WHERE status IN ('reserved','sold') AND deleted_at IS NULL`** |
| `absorption_daily` | +`units_remaining` (**NULL được**) + CHECK `IS NULL OR >= 0` |

**Không có `units.project_id`** — dự án suy ra qua `areas.project_id` (SRS §5.2.8
đã bác cột này). Thêm nữa là mở đường cho hai giá trị mâu thuẫn về cùng một sự thật.

**`units_remaining` NULL được** vì bộ tính cũ đọc `sales_records`, không biết tồn
kho theo từng căn. Điền `0` cho những dòng đó là nói sai; NULL đọc đúng là "bộ
tính sinh ra dòng này không tính được số đó".

**Ràng buộc một giao dịch đang giữ mỗi căn KHÔNG chặn lịch sử**: `lost` và các
giai đoạn `lead`…`viewing` nằm ngoài tập đang giữ, giao dịch đã tombstone cũng
vậy. Có test dựng 5 giao dịch trên một căn để chốt điều này. Huỷ một giao dịch
đưa nó ra khỏi tập đang giữ nên luồng huỷ vẫn chạy được.

`upgrade()` và `downgrade()` đều có; không xoá dữ liệu ở cả hai chiều.

## Code

| File | Vai trò |
|---|---|
| `src/services/domain_projection.py` | **Mới.** `DomainProjector` — chiếu bản ghi nguồn đã chấp nhận vào `units`/`deals`. KHÔNG lặp lại logic phiên bản: `SourceIdentityService` (S2) trả lời "có chấp nhận không", tầng này trả lời "bảng nghiệp vụ đổi thế nào" |
| `src/services/domain_absorption.py` | **Mới.** `DomainAbsorptionCalculatorService` (đọc `units`+`deals`) và `ParallelRunComparator` |
| `src/services/sync_runs.py` | Gọi tầng chiếu trong **SAVEPOINT theo từng bản ghi**; đếm thêm `projections` |
| `src/api/inventory.py` | **Mới.** `GET /inventory`, `GET /absorption/parallel-run` |
| `src/api/dashboard.py` | `/absorption/summary` nhận `?calculator=`; thêm `units_reserved`, `calculator` vào phản hồi |
| `src/models/tables.py`, `src/models/schemas.py`, `src/main.py` | Bản chiếu Core, model phản hồi, gắn router |

**SAVEPOINT theo từng bản ghi** là quyết định đáng nêu: quyết định danh tính và
việc chiếu phải CÙNG sống hoặc CÙNG chết. Không có nó, một bản ghi bị tầng chiếu
từ chối vẫn để lại dòng "đã chấp nhận" trong `crm_source_records`, và lần gửi lại
bản đã sửa sẽ bị bỏ qua vì "đã có rồi". Có test chốt: gửi lại bản ghi đã bị từ
chối thì lần sau vào được.

## Trạng thái giao dịch và ánh xạ

Tập chuẩn lấy từ **SRS §5.2.8**: `lead` · `qualified` · `interested` · `viewing` ·
`reserved` · `sold` · `lost`.

Repo **không có** trạng thái `cancelled`. Hệ nguồn gửi `cancelled`/`canceled` thì
tầng chiếu ánh xạ về `lost` (về mặt hấp thụ hai thứ giống hệt nhau: căn quay lại
quỹ hàng, không tính là đã bán) và giữ nguyên chữ gốc ở `deals.source_status` để
còn truy được. **Mọi giá trị khác bị TỪ CHỐI** — không có mặc định nào.

Bản ghi bị từ chối: không ghi vào `deals`, không tính vào hấp thụ, và sinh một
dòng `upload_errors` có `error_code`, `source_record_id`, `json_path`,
`field_name`.

## Bộ tính hấp thụ mới

Đếm theo GIAO DỊCH, không theo `units.status`: giao dịch là sự kiện có mốc thời
gian, còn `units.status` chỉ là ảnh chụp hiện tại.

* `sold` còn sống → đã bán, vào ngày `date(sold_at)`.
* `reserved` còn sống → đang giữ chỗ.
* `lost` (gồm `cancelled` đã ánh xạ) → không tính gì; căn quay lại quỹ hàng.
* Căn `blocked` nằm NGOÀI quỹ bán được.
* `deleted_at IS NOT NULL` bị loại khỏi mọi phép đếm.
* Quan hệ không hợp lệ (giao dịch còn sống trên căn đã xoá, số giữ chỗ vượt quỹ
  hàng) được **báo ra ở `anomalies`**, không đếm im lặng thành bán hay còn trống.

`compute()` **thuần đọc**, trả kết quả trong bộ nhớ. Ghi xuống `absorption_daily`
chỉ xảy ra khi gọi `persist()` tường minh — chưa có đường sản xuất nào gọi.

## Chạy song song

`ParallelRunComparator` chạy CẢ HAI bộ tính trên cùng một dự án và trả chênh lệch
theo từng chỉ số, kèm `anomalies`. Không ghi gì, không đổi đường đọc của dashboard.

`GET /absorption/parallel-run` trả thêm `production_calculator` — cho biết
dashboard đang thực sự đọc bộ nào. Giá trị hiện tại: **`legacy_aggregate`**.

Điểm cắt sang duy nhất là hằng số `PRODUCTION_CALCULATOR` trong
`src/api/inventory.py`. Đổi nó là một quyết định tường minh, không phải hệ quả phụ.

## API

| Endpoint | Ghi chú |
|---|---|
| `GET /api/v1/inventory` | Tồn kho từ `units`+`deals`. Lọc `area_id`, `unit_status`, `deal_status`, `include_deleted`, `include_units`, phân trang |
| `GET /api/v1/absorption/parallel-run` | Đối chiếu hai bộ tính; **không** đổi số liệu sản xuất |
| `GET /api/v1/absorption/summary` | **Mở rộng, không phá vỡ**: thêm `units_reserved` (NULL với bộ tính cũ) và `calculator`; nhận `?calculator=domain_units_deals` để đối chiếu. Mặc định vẫn là bộ cũ |
| `POST /api/v1/sync/{entity}`, `GET /api/v1/sync-runs/{id}[/errors]` | Thêm `projections` vào phản hồi (đếm tác động lên bản sao) |

Không trường phản hồi nào bị xoá. `POST /api/v1/files/upload` và API đồng bộ S2
vẫn chạy — có test chốt.

## Tests

| File | Nội dung |
|---|---|
| `tests/test_migrations/test_0007_s3_domain_model.py` | **Mới, 11 test.** Tiến → lùi → tiến lại · dữ liệu tổng hợp cũ còn nguyên và `units_remaining` là NULL · danh tính nguồn duy nhất · mã căn chỉ duy nhất trong phạm vi căn còn sống · CHECK trạng thái căn và giao dịch (gồm cả việc `cancelled` KHÔNG hợp lệ ở tầng DB) · `sold` bắt buộc có `sold_at` · một giao dịch đang giữ mỗi căn · **lịch sử 5 giao dịch trên một căn vẫn hợp lệ** · tombstone cho cả hai bảng |
| `tests/test_services/test_domain_projection.py` | **Mới, 34 test.** Chiếu căn/giao dịch mới · nạp lại không chạm bảng · bản mới hơn cập nhật · bản cũ bị bỏ qua · đụng độ không ghi đè · tombstone · bản cũ không làm sống lại tombstone, bản mới thì được · từ chối trạng thái lạ, căn/phân khu không tồn tại, giao dịch giữ thứ hai · gửi lại bản đã sửa vào được · `cancelled`→`lost` giữ chữ gốc · đếm bán/giữ chỗ/còn lại · huỷ trả căn về quỹ · căn/giao dịch đã xoá không được đếm · nhiều giao dịch lịch sử không đếm trùng · tính tất định · `compute()` không ghi gì · `persist()` ghi `units_remaining` · chạy song song báo chênh lệch và không đổi số sản xuất |
| `tests/test_api/test_inventory.py` | **Mới, 16 test.** Tồn kho và bộ lọc · tombstone ẩn/hiện · phân trang · 404/422 · bất thường hiện ra · summary giữ trường cũ và mặc định bộ cũ · `?calculator=` · bộ tính lạ bị từ chối · chạy song song nêu `production_calculator` và không đổi kết quả dashboard · route S1/S2 còn sống · trạng thái lạ không sinh kết quả nghiệp vụ |
| `tests/test_services/test_source_identity.py`, `tests/test_api/test_sync.py` | **Sửa fixture, KHÔNG nới lỏng khẳng định.** Trước 0007, `data` của phong bì là khối mờ nên test dùng `{"v": 1}`; giờ tầng chiếu diễn giải nó thật, nên payload phải là một căn hợp lệ và module phải seed phân khu. Mọi bất biến của S2 (so phiên bản, tombstone, replay, đếm) giữ nguyên; một khẳng định chép cứng dấu vân được đổi sang dựng từ chính helper |

## Lệnh đã chạy và kết quả thực tế

| Lệnh | Kết quả |
|---|---|
| `ruff format` trên các file đã đụng | 7 file reformat, sau đó sạch |
| `ruff check src/ tests/ alembic/versions/0007_s3_domain_model.py` | **All checks passed** |
| `ruff check src/ tests/ alembic/` | 2 lỗi I001 ở `alembic/env.py` và `alembic/versions/0001_initial_schema.py` — **có sẵn từ trước**, không thuộc phạm vi đợt này, không sửa |
| `mypy src/` | **KHÔNG chạy được** — `No module named mypy`. Không tính là đạt |
| `pytest tests/test_migrations -q` | **17 passed / 0 failed** (0005: 3 · 0006: 3 · 0007: 11) |
| `pytest test_domain_projection.py test_inventory.py -q` | **50 passed / 0 failed** |
| Hồi quy S1+S2 (7 file) | **229 passed / 0 failed** |
| `TEST_TARGET=tests bash scripts/test_db.sh -q` | **487 passed / 0 failed** (143.67s) |
| `pytest tests/ -q` (không có DATABASE_URL) | **148 passed, 339 skipped** |
| `alembic heads` | `0007_s3_domain_model (head)` |

Nền trước đợt này là **426 passed**; sau đợt này **487 passed** (+61).

## Remaining Limitations & Risks

* **CHƯA cắt sang bộ tính mới.** Dashboard vẫn đọc bộ tính cũ
  (`legacy_aggregate`). Cắt sang là quyết định tường minh, chưa ai ra.
* **CHƯA thử với payload CRM thật.** Toàn bộ kiểm chứng dùng fixture dựng trong
  test. Chưa có hệ CRM nào kết nối.
* **Không backfill được từ `sales_records`.** Dữ liệu tổng hợp cũ không chứa danh
  tính từng căn hay từng giao dịch; dựng `units`/`deals` từ nó là bịa dữ liệu.
  Số liệu S3 chỉ có sau khi CRM đồng bộ thật. Bộ tính cũ vì vậy vẫn phải sống.
* **`units_remaining` theo ngày là xấp xỉ.** Nó trừ số giữ chỗ HIỆN TẠI khỏi mọi
  ngày trong chuỗi, vì `reserved` không có nhật ký sự kiện để dựng lại lịch sử.
  Số liệu hiện tại thì đúng; số liệu lịch sử của những ngày có giữ chỗ khác hôm
  nay thì lệch.
* **Vận tốc của bộ tính mới chưa so được với bộ cũ** ở mức tổng dự án:
  `?calculator=domain_units_deals` trả `avg_velocity_30d = 0` thay vì bịa một con
  số không cùng định nghĩa.
* **Chưa có xác thực** — vẫn đúng như S1/S2: không có endpoint đăng nhập, không
  có RBAC. `GET /inventory` và `/parallel-run` mở như phần còn lại của API. MVP 3
  mới làm phần này.
* **Chưa có đối chiếu xoá theo `full_snapshot`** (thừa kế từ S2): bản ghi vắng mặt
  trong snapshot KHÔNG tự động bị tombstone. Muốn phát hiện xoá thì CRM phải gửi
  `operation: "delete"`.
* **`units.status` do CRM sở hữu nhưng không được dùng để đếm.** Nếu nó mâu thuẫn
  với giao dịch (căn `available` mà có giao dịch `sold`), bộ tính đi theo giao
  dịch. Chưa có cảnh báo riêng cho kiểu lệch này.
* Các hạn chế của S1 và S2 vẫn còn nguyên hiệu lực — xem hai mục bên dưới.

## Phạm vi: những gì KHÔNG được cài đặt trong đợt này

* **KHÔNG** ghi ngược về CRM dưới bất kỳ hình thức nào.
* **KHÔNG** cắt sang bộ tính mới ở đường sản xuất — cố ý, chờ quyết định.
* **KHÔNG** có `customers` / `customer_interactions`.
* **KHÔNG** có giá, ngân sách, trường "quan tâm", hay xử lý tiền tệ.
* **KHÔNG** có bảng `inventory_status` riêng; trạng thái tồn kho là `units.status`
  cộng với giao dịch.
* **KHÔNG** tách `bookings` khỏi `deals` — một thực thể có trạng thái.
* **KHÔNG** có `units.project_id`.
* **KHÔNG** có CRM polling, webhook, hàng đợi mới, schema registry, data warehouse.
* **KHÔNG** đổi tên `upload_files`; **KHÔNG** xoá `sales_records` /
  `inventory_snapshots`; **KHÔNG** đổi API upload CSV; **KHÔNG** thêm dependency.

## Final Result — đợt 2026-08-09 (b)

**DONE** (cho phạm vi tầng miền S3 + chuẩn bị chạy song song)

Căn cứ: migration 0007 chạy được cả tiến lẫn lùi trên database thật (11 test);
toàn bộ suite 487 passed / 0 failed, không có lỗi nào chưa giải thích được; hồi
quy S1+S2 riêng 229 passed. Chạy song song đã cài đặt và có test chứng minh nó
KHÔNG đổi số liệu sản xuất. **Cắt sang bộ tính mới cố ý chưa thực hiện.** `mypy`
không chạy được vì chưa cài — đã ghi rõ, không tính là đạt.

Toàn bộ dự án tích hợp CRM chưa hoàn thành: chưa nối CRM thật, chưa cắt sang,
chưa có xác thực.

---

# Đợt 2026-08-09 (a) — nền đồng bộ CRM → ứng dụng (S2)

Phần này ghi việc của ngày 09/08. Báo cáo của 08/08 và 07/08 giữ nguyên bên dưới.

Phạm vi: **chỉ phần NỀN dùng lại được** cho việc mirror CRM ở giai đoạn sau —
nạp JSON, danh tính bản ghi nguồn, metadata lô, mô hình lỗi, so phiên bản và xử
lý đụng độ, tombstone, API đồng bộ. Đồng bộ MỘT CHIỀU: CRM là nguồn sự thật, bên
này không ghi ngược.

## Migration

`alembic/versions/0006_sync_foundation.py` (down_revision `0005_idempotent_csv_ingestion`).
**Không** đổi tên `upload_files` — bốn khoá ngoại đang trỏ vào nó.

| Bảng | Thay đổi |
|---|---|
| `upload_files` | +12 cột: `source_system`, `source_instance_id`, `source_entity`, `input_format`, `transport_mode`, `sync_mode`, `schema_version`, `external_batch_id`, `rows_received`, `finished_at`, `last_source_cursor`, `error_summary`. `filename`/`checksum` → NULL được. `ck_upload_files_status` thêm `partially_completed`. Partial-unique `uq_upload_files_source_batch` trên `(source_system, source_instance_id, external_batch_id)` |
| `upload_errors` | +8 cột: `error_category`, `json_path`, `source_record_id`, `record_locator`, `field_name`, `raw_value_redacted`, `retry_status`, `resolved_at`. `row_number` → NULL được (bản ghi JSON không có số dòng), thay bằng CHECK "phải định vị được bằng row_number HOẶC json_path, trừ lỗi mức lô" |
| `crm_source_records` | **Bảng mới.** Danh tính duy nhất `(source_system, source_instance_id, source_entity, source_record_id)`; giữ phiên bản nguồn (`source_revision` + `source_updated_at`), `payload_hash`, `state`, `last_decision`, metadata đụng độ, `first_seen_at`/`last_seen_at`, `deleted_at` |

Cột thêm vào `upload_files` đều có DEFAULT mô tả ĐÚNG dữ liệu cũ (`manual_upload`
/ `csv` / `file_upload` / `full_snapshot`), nên lô tải file trước đây không bị nói
sai về nguồn gốc. `upgrade()` và `downgrade()` đều có; không xoá dữ liệu ở chiều
tiến. Chiều lùi buộc phải xoá lô đẩy qua API (`filename IS NULL`) và lỗi JSON
(`row_number IS NULL`) vì lược đồ cũ không chứa được chúng — có test chốt rằng dữ
liệu CSV KHÔNG bị vạ lây.

## Code

| File | Vai trò |
|---|---|
| `src/services/json_payload.py` | **Mới.** `JsonPayloadParser` — phong bì JSON → bản ghi staging đã chuẩn hoá. Không biết gì về bảng nghiệp vụ. Kèm `payload_fingerprint()` (băm ổn định, không phụ thuộc thứ tự khoá) và `redact()` |
| `src/services/source_identity.py` | **Mới.** `SourceIdentityService` — phân giải danh tính, so phiên bản, ra đúng một trong sáu quyết định, cập nhật `crm_source_records` |
| `src/services/sync_runs.py` | **Mới.** `SyncRunService` — vòng đời lô: tạo/nhận lại lô idempotent, xử lý bản ghi trong một transaction, ghi lỗi, chốt trạng thái kết thúc |
| `src/api/sync.py` | **Mới.** Ba endpoint đồng bộ |
| `src/models/tables.py` | Bản chiếu Core cho cột mới + bảng `crm_source_records` |
| `src/models/schemas.py` | `SyncRunAccepted`, `SyncRunDetail`, `SyncRecordError`, `SyncRunErrorList` |
| `src/main.py` | Gắn `sync_router` vào `/api/v1` |

**Sáu quyết định** cho mỗi bản ghi nguồn: `insert` · `update` · `skip_stale` ·
`duplicate_noop` · `conflict` · `tombstone`.

**So phiên bản** không bao giờ dùng giờ máy nhận. Thứ tự căn cứ: `source_revision`
(số thứ tự CRM cấp, ưu tiên vì không phụ thuộc đồng hồ) → `source_updated_at` (bắt
buộc có múi giờ) → không so được thì `payload_hash` quyết định (giống nhau =
`duplicate_noop`, khác nhau = `conflict`).

**Đụng độ được GHI LẠI, không hoà giải.** Cùng phiên bản mà khác dấu vân thì trạng
thái đã chấp nhận giữ nguyên; `conflict_count`/`conflict_payload_hash` tăng lên và
một dòng `upload_errors` loại `conflict` được ghi để người vận hành thấy được.

**Tombstone.** `delete` mới hơn → tombstone. Một `upsert` CŨ HƠN không làm sống
lại được. Một `upsert` MỚI HƠN thì có — CRM xoá rồi tạo lại là chuyện bình thường
và CRM là nguồn sự thật. Tombstone ở giai đoạn này KHÔNG kích hoạt tính lại dữ
liệu nghiệp vụ nào (chưa có bảng nào để tính).

## API

| Endpoint | Ghi chú |
|---|---|
| `POST /api/v1/sync/{entity}` | 202 với lô mới, **200 + `replayed=true`** khi `external_batch_id` đã xử lý (không xử lý lại) |
| `GET /api/v1/sync-runs/{id}` | Trạng thái, nguồn, mã lô, đếm theo từng quyết định, mốc bắt đầu/kết thúc, `error_summary` |
| `GET /api/v1/sync-runs/{id}/errors` | Lỗi kèm `json_path`; lọc theo `error_category`, phân trang `limit`/`offset` |

`POST /api/v1/files/upload` **không đổi** — có test chốt route vẫn còn.

Phong bì mang thêm `project_id` so với bản mẫu trong đề bài: `upload_files.project_id`
là NOT NULL FK và mọi luồng nạp trong repo đều gắn theo dự án. Cho cột đó NULL sẽ
là thay đổi lớn hơn nhiều so với việc thêm một trường vào phong bì.

Chưa có tầng xác thực nào trong mã nguồn (MVP 3 mới làm — SRS §2.4), nên ba
endpoint này mở đúng như route upload hiện tại. **Không** tự bịa auth riêng.

## Tests

| File | Nội dung |
|---|---|
| `tests/test_services/test_json_payload.py` | **Mới, 38 test.** Phong bì hợp lệ · thiếu/rỗng trường bắt buộc · phiên bản phong bì lạ · thực thể lạ · lệch entity giữa route và thân · bản ghi hỏng không giết bản ghi lành · `json_path` không cần `row_number` · thao tác lạ bị từ chối (không mặc định về `upsert`) · mốc thời gian trần bị từ chối · upsert thiếu phiên bản bị từ chối · trùng danh tính trong một lô · dấu vân không đổi theo thứ tự khoá · che dữ liệu nhạy cảm |
| `tests/test_services/test_source_identity.py` | **Mới, 20 test.** Bản ghi mới · mới hơn thì cập nhật · cũ hơn bị bỏ qua · cùng phiên bản cùng hash là no-op · cùng phiên bản khác hash là đụng độ (không ghi đè) · revision ưu tiên hơn timestamp · đến ngược thứ tự vẫn hội tụ · tombstone · upsert cũ không làm sống lại tombstone · upsert mới thì được · replay `external_batch_id` không tạo lô thứ hai · cùng mã lô khác `source_instance_id` là lô khác · số đếm khớp thực tế · lô hỏng kết thúc ở `failed` · chạy lại an toàn |
| `tests/test_api/test_sync.py` | **Mới, 18 test.** Ba endpoint, mã HTTP, 404/422, replay 200, `partially_completed`, lọc/phân trang lỗi, đụng độ hiện ra ở `/errors`, không lộ PII/SQL, route CSV còn nguyên |
| `tests/test_migrations/test_0006_sync_foundation.py` | **Mới, 3 test.** Tiến → lùi → tiến lại trên database dùng-một-lần · lô CSV cũ sống sót và được gắn nhãn đúng nguồn gốc · đi lùi xoá đúng phần lược đồ cũ không chứa được, không đụng dữ liệu CSV |
| `tests/test_migrations/test_0005_idempotent_csv_ingestion.py` | **Sửa 4 chỗ**: nâng tới ĐÚNG revision `0005` thay vì `head`. File này kiểm 0005; thêm revision mới không được làm nó đổi ý nghĩa. Mọi khẳng định giữ nguyên |

Không test cũ nào bị nới lỏng hay xoá.

## Lệnh đã chạy và kết quả thực tế

| Lệnh | Kết quả |
|---|---|
| `ruff format` trên các file đã đụng | 7 file được format lại, sau đó sạch |
| `ruff check src/ tests/ alembic/versions/0006_sync_foundation.py` | **All checks passed** |
| `mypy src/` | **KHÔNG chạy được** — mypy chưa cài trong `.venv` (`No module named mypy`), dù Makefile có target `typecheck`. Không tính là đạt |
| `TEST_TARGET=tests/test_migrations bash scripts/test_db.sh -q` | **6 passed / 0 failed** (3 của 0005 + 3 của 0006) |
| `TEST_TARGET=tests/test_services/test_json_payload.py` (không cần DB) | **38 passed / 0 failed** |
| `TEST_TARGET=tests/test_services/test_source_identity.py bash scripts/test_db.sh -q` | **20 passed / 0 failed** |
| `TEST_TARGET=tests/test_api/test_sync.py bash scripts/test_db.sh -q` | **18 passed / 0 failed** |
| `pytest test_import_records.py test_parse_upload.py test_files.py test_excel_parser.py -q` (hồi quy CSV + S1) | **153 passed / 0 failed** |
| `TEST_TARGET=tests bash scripts/test_db.sh -q` (toàn bộ suite) | **426 passed / 0 failed** (100.13s) |
| `pytest tests/ -q` (không có DATABASE_URL) | **148 passed, 278 skipped** |

Nền trước đợt này là **347 passed**; sau đợt này **426 passed** (+79 test mới).

## Remaining Limitations & Risks

* **Chưa ghi dữ liệu nghiệp vụ.** Bản ghi được kiểm tra, so phiên bản và theo dõi
  trong `crm_source_records`, nhưng phần `data` CHƯA vào bảng nào — `units` và
  `deals` thuộc giai đoạn sau. Đây đúng là phạm vi của đợt này, nhưng phải nói rõ:
  gọi API đồng bộ lúc này KHÔNG làm đổi bất kỳ số liệu nào trên dashboard.
* **Chỉ hai thực thể được nhận:** `units` và `deals`. Gửi thực thể khác trả 404.
* *(Cập nhật đợt (b) cùng ngày: hai thực thể này giờ ĐÃ được chiếu xuống bảng
  `units`/`deals` — xem mục S3 ở đầu file. Đoạn "chưa ghi dữ liệu nghiệp vụ" ngay
  dưới đây mô tả trạng thái tại thời điểm S2.)*
* **Xử lý đồng bộ chạy trong request**, không qua hàng đợi (đề bài cấm thêm queue).
  Lô rất lớn sẽ giữ request lâu; quy mô pilot vài trăm bản ghi thì không thành vấn đề.
* **`full_snapshot` chưa tự tombstone bản ghi vắng mặt.** Cột `sync_mode` được ghi
  lại nhưng chưa có bước đối chiếu "bản ghi không xuất hiện trong snapshot ⇒ đã bị
  xoá". Muốn phát hiện xoá lúc này thì CRM phải gửi `operation: "delete"`.
* **Lô đẩy qua API không lưu lại payload gốc.** Không có artifact để chạy lại như
  luồng file; muốn nạp lại thì CRM phải gửi lại lô.
* **`conflict` được ghi nhận nhưng chưa có đường xử lý tiếp** — không có API đánh
  dấu đã giải quyết, `retry_status` mới chỉ là cột.
* **Chưa chạy với CRM thật.** Toàn bộ kiểm chứng dùng payload dựng trong test.
* Các hạn chế của đợt S1 (08/08) vẫn còn nguyên hiệu lực — xem mục bên dưới.

## Phạm vi: những gì KHÔNG được cài đặt trong đợt này

Xác nhận rõ, đối chiếu từng mục:

* **KHÔNG** tạo bảng `units`.
* **KHÔNG** tạo bảng `deals`.
* **KHÔNG** đổi `absorption_daily` (không thêm cột, không đổi cách tính).
* **KHÔNG** có absorption calculator mới; `AbsorptionCalculatorService` vẫn đọc
  `sales_records` y như cũ.
* **KHÔNG** tính số căn giữ chỗ hay tồn kho theo từng căn.
* **KHÔNG** ghi ngược về CRM dưới bất kỳ hình thức nào.
* **KHÔNG** thêm webhook, poller, Celery, Kafka, schema registry hay data warehouse.
* **KHÔNG** đổi tên `upload_files`; **KHÔNG** xoá `sales_records` /
  `inventory_snapshots`; **KHÔNG** đổi API upload CSV; **KHÔNG** thêm dependency.

Đây mới là NỀN của S2. Toàn bộ dự án S3 chưa hoàn thành.

## Final Result — đợt 2026-08-09 (a)

**DONE** (cho phạm vi nền đồng bộ S2)

Căn cứ: migration 0006 chạy được cả tiến lẫn lùi trên database thật (3 test);
toàn bộ suite 426 passed / 0 failed, không có lỗi nào chưa giải thích được; hồi
quy CSV + S1 riêng 153 passed; lô hỏng kết thúc ở `failed` có test chứng minh.
`mypy` không chạy được vì chưa cài — đã ghi rõ, không tính là đạt.

---

# Đợt 2026-08-08 — sửa lỗi production S1: nạp CSV lặp lại được

Phần này ghi việc của ngày 08/08. Báo cáo của ngày 07/08 giữ nguyên bên dưới.

## Root Cause

Một lô nạp chạm phải khoá nghiệp vụ đã có sẽ **treo vĩnh viễn ở trạng thái
`pending`**, và file đã sửa cũng không nạp lại được nữa. Ba nguyên nhân chồng lên
nhau, phải gỡ cả ba mới hết:

1. **Ghi bằng `INSERT` trần.** `ImportService._insert_rows()` gọi `sa.insert(target)`
   không kèm `ON CONFLICT`. Chống trùng xuyên các lần nạp phó mặc cho ràng buộc
   `uq_sales_area_date_external_id`, nên chỉ cần MỘT dòng trùng là `IntegrityError`
   cuốn theo cả lô — kể cả những dòng hoàn toàn hợp lệ. Hành vi này từng được chốt
   lại bằng test `test_business_key_violation_rolls_back_everything`, tức là nó được
   coi là đúng chứ không phải bị bỏ sót. Hệ quả thực tế: hai lô có phần dữ liệu
   chồng lấn — chuyện bình thường khi nguồn xuất theo cửa sổ ngày — không bao giờ
   nạp được lô thứ hai.

2. **`IntegrityError` không được bắt ở job.** `src/jobs/parse_upload.py` chỉ bắt
   `ImportRejectedError`, `ExcelParseError`, `OSError`. Lỗi ràng buộc thoát ra
   ngoài, RQ đánh dấu job failed, còn `upload_files` thì **không ai đụng tới**.

3. **Lệnh đặt `status='processing'` nằm TRONG transaction bị rollback.**
   `ImportService.load()` mở đúng một `session.begin()` bao cả việc cập nhật trạng
   thái lẫn việc chèn dữ liệu (`src/services/import_records.py`). Rollback trả bản
   ghi về `pending` chứ không để lại `processing`, nên nhìn từ ngoài lô giống hệt
   một lô vừa nhận và chưa chạy — người dùng poll `/status` chờ mãi.

Cộng thêm: `uq_upload_files_project_checksum` lấy BYTE của file làm danh tính lô.
Lô hỏng vẫn giữ chỗ checksum đó, nên **gửi lại đúng file đó bị trả 409** thay vì
được xử lý lại. Nạp lại một lô không đổi lẽ ra phải là thao tác không-làm-gì.

## Migration

`alembic/versions/0005_idempotent_csv_ingestion.py` (down_revision `0004_cover_image_public_id`):

| Thao tác | Lý do |
|---|---|
| `DROP CONSTRAINT uq_upload_files_project_checksum` | Byte của file không phải danh tính lô; chống trùng chuyển về khoá nghiệp vụ của bảng đích |
| `CREATE INDEX ix_upload_files_project_id_checksum` | Vẫn tra nhanh "file này đã nạp chưa", nhưng không chặn |
| `ADD COLUMN sales_records.source_updated_at` (TS, NULL) | Phiên bản bản ghi ở nguồn — cơ sở duy nhất để biết bản đến có mới hơn không |
| `ADD COLUMN inventory_snapshots.source_updated_at` (TS, NULL) | Như trên |

`upgrade()` và `downgrade()` đều có. **Không xoá dữ liệu**: cả bốn thao tác giữ
nguyên mọi dòng đang có. `downgrade()` dựng lại UNIQUE cũ và sẽ **vỡ có chủ đích**
nếu lúc đó dữ liệu đã có hai lô trùng `(project_id, checksum)` — ràng buộc cũ không
còn mô tả được dữ liệu mới, im lặng bỏ dòng mới là tệ hơn.

**Cột NULL được, không có default.** Dữ liệu nạp trước 0005 không mang phiên bản;
bịa ra một mốc mặc định là nói dối về nguồn. Quy tắc so sánh xử lý NULL tường minh.

## Code

| File | Thay đổi |
|---|---|
| `src/services/import_records.py` | Thêm `_upsert_stmt()` dựng `INSERT ... ON CONFLICT` theo template; `_insert_rows()` dùng nó thay `sa.insert()`; thêm chốt chặn trùng KHOÁ NGHIỆP VỤ trong cùng một file; xoá `upload_errors` cũ của lô ở đầu transaction để chạy lại cho ra trạng thái sạch |
| `src/services/excel_parser.py` | Thêm kiểu cột `timestamp` + `_to_timestamp()` (BẮT BUỘC có múi giờ); thêm cột tuỳ chọn `source_updated_at` vào template `sales` và `inventory`; thêm `conflict_constraint` / `conflict_columns` / `versioned_by` vào `TableTemplate` |
| `src/jobs/parse_upload.py` | Bắt `IntegrityError`, chuyển lô sang `failed` ở transaction RIÊNG; `_constraint_name()` chỉ moi tên ràng buộc, không lộ SQL/tham số |
| `src/models/tables.py` | Bản chiếu Core thêm `source_updated_at` cho hai bảng |

**Quy tắc ghi đè** (`_upsert_stmt`), áp ở tầng SQL chứ không đọc-rồi-ghi:

```
DO UPDATE ... WHERE excluded.source_updated_at IS NOT NULL
              AND (target.source_updated_at IS NULL
                   OR excluded.source_updated_at > target.source_updated_at)
```

- Bản đến mới hơn → ghi đè. `id` và `created_at` giữ nguyên (danh tính và lúc xuất hiện lần đầu).
- Mốc cũ hơn **hoặc bằng** → bỏ qua (`>` chứ không `>=`).
- Bản đến không có mốc → **không** ghi đè. Nhờ vậy nạp lại một file không có cột phiên bản là thao tác không-làm-gì.
- Dòng đang có không mốc, bản đến có → ghi đè. Đây là chiều duy nhất NULL bị thay.
- Template `areas` không có phiên bản → `DO NOTHING`, để một file cũ không lặng lẽ đổi `total_units` (mẫu số của tỷ lệ hấp thụ).

Chốt chặn trùng khoá nghiệp vụ trong cùng một file là bắt buộc, không phải tuỳ chọn:
Postgres không cho một lệnh `ON CONFLICT DO UPDATE` chạm hai lần vào cùng một dòng.
Dòng sau thành lỗi `DUPLICATE_KEY` theo dòng, giữ dòng đầu.

## Tests

| File | Thêm/sửa |
|---|---|
| `tests/test_services/test_import_records.py` | **+12 test mới**: nạp lại không thêm dòng · lô chồng lấn · trùng khoá trong file không làm vỡ lô · mốc mới hơn ghi đè (kiểm cả `id`/`created_at`/`file_id`) · mốc cũ hơn bị bỏ qua · mốc bằng nhau không ghi đè · NULL không ghi đè · có mốc thay được dòng không mốc · replay + versioning cho `inventory_snapshots` · nạp lại danh mục `areas` không đổi `total_units`. **3 test cũ viết lại** theo hợp đồng mới (xem "Test đã đổi ý nghĩa") |
| `tests/test_jobs/test_parse_upload.py` | **+4 test mới**: lô hỏng kết thúc ở `failed` chứ không `pending` · thông báo lỗi không lộ SQL/dữ liệu dòng · chạy lại lô hỏng sau khi gỡ nguyên nhân → `completed`, lỗi cũ được dọn · chạy lại nguyên đường job không thêm dòng |
| `tests/test_migrations/test_0005_idempotent_csv_ingestion.py` | **Mới, +3 test**: tiến → lùi → tiến lại trên database dùng-một-lần, soi `pg_constraint`/`pg_indexes`/`information_schema` ở từng chặng · dữ liệu cũ sống sót qua upgrade · downgrade vỡ đúng lúc dữ liệu đã trùng checksum |
| `tests/test_services/test_excel_parser.py` | Sửa 5 test theo hợp đồng template mới (thêm `source_updated_at`); `test_template_columns_exist_in_alembic_schema` giờ quét MỌI revision chứ không chỉ 0001 |

**Test đã đổi ý nghĩa** — cả ba đều đang chốt lại chính hành vi lỗi, nên viết lại
là bắt buộc; không phải nới lỏng để test xanh:

- `test_business_key_violation_rolls_back_everything` → `test_business_key_duplicate_no_longer_rolls_back_the_batch`. Cùng dữ liệu đầu vào, kỳ vọng ngược lại: dòng đầu vào bảng, dòng sau thành lỗi theo dòng.
- `test_schema_blocks_a_second_file_with_the_same_checksum` → `test_schema_allows_two_files_with_the_same_checksum`. Vẫn khẳng định phần quan trọng: **dữ liệu** không nhân đôi.
- `test_concurrent_imports_of_same_checksum_leave_exactly_one_file` → `..._of_same_file_leave_exactly_one_row`. Bất biến chuyển từ tầng file xuống tầng dữ liệu.

Thêm `test_unabsorbable_constraint_violation_still_rolls_back_everything` để giữ
lại phần kiểm rollback: `ON CONFLICT` chỉ nhắm được MỘT ràng buộc, nên vi phạm
`uq_sales_area_source_row_hash` vẫn làm vỡ cả lô — và đó là hành vi đúng.

## Lệnh đã chạy và kết quả thực tế

| Lệnh | Kết quả |
|---|---|
| `TEST_TARGET=tests/test_services/test_import_records.py bash scripts/test_db.sh -q` | **41 passed / 0 failed** |
| `TEST_TARGET=tests/test_jobs/test_parse_upload.py bash scripts/test_db.sh -q` | **21 passed / 0 failed** |
| `TEST_TARGET=tests/test_migrations bash scripts/test_db.sh -q` | **3 passed / 0 failed** |
| `TEST_TARGET=tests bash scripts/test_db.sh -q` (toàn bộ suite) | **347 passed / 0 failed** (99.15s) |
| `pytest tests/ -q` (không có DATABASE_URL) | **110 passed, 237 skipped** |
| `alembic upgrade head` → `downgrade 0004` → `upgrade head` trên DB dùng-một-lần | PASS, soi lược đồ khớp ở cả ba chặng |
| `ruff check src/ tests/ alembic/versions/0005_idempotent_csv_ingestion.py` | **All checks passed** |
| `ruff format` trên các file đã đụng | 4 file được format lại, sau đó `--check` sạch |
| `mypy src/` | **KHÔNG chạy được** — mypy chưa cài trong `.venv` (`No module named mypy`), dù Makefile có target `typecheck` |

Nền trước đợt này là **329 passed**; sau đợt này **347 passed** (+19 test mới, 3 test
viết lại, 5 test sửa theo hợp đồng template).

## Remaining Limitations & Risks

* **Sửa số liệu đòi hỏi file phải có cột `source_updated_at`.** File không có cột đó
  nạp lại sẽ KHÔNG ghi đè — đúng theo thiết kế (không có phiên bản thì không có cơ
  sở nói bản nào mới hơn), nhưng người dùng quen sửa Excel rồi nạp lại sẽ thấy số
  không đổi. Cần nêu rõ trong hướng dẫn nạp dữ liệu.
* **Tầng API vẫn trả 409 `DUPLICATE_FILE`** khi upload lại file GIỐNG HỆT về byte:
  `_find_duplicate()` trong `src/api/files.py` là một truy vấn, không phải ràng buộc,
  nên bỏ UNIQUE không đụng tới nó. Giữ nguyên theo yêu cầu "không đổi API upload".
  Hệ quả còn lại: sau một lô hỏng, file ĐÃ SỬA (khác byte → khác checksum) nạp lại
  bình thường; chỉ file y hệt là còn bị chặn ở API. Chạy lại lô hỏng ở tầng job thì
  không vướng gì — đã có test.
* **`rows_ok` nghĩa là "số dòng qua được tầng ghi", không phải "số dòng mới trong
  bảng".** Một dòng bị bỏ qua vì bản đang có đã mới hơn vẫn tính là `rows_ok`. Con số
  này đi thẳng vào `upload_files.rows_ok` và `/files/{id}/status`.
* **`_to_timestamp` từ chối mốc thời gian không có múi giờ.** Cố ý: đoán bừa là UTC
  sẽ lệch 7 tiếng so với giờ Việt Nam và âm thầm đảo thứ tự "bản nào mới hơn". Hệ quả:
  file Excel có cột thời gian trần sẽ báo lỗi theo dòng. Chưa kiểm chứng với file
  Excel thật do calamine trả `datetime` trần — mới chỉ đi qua đường CSV.
* **Vẫn còn một đường làm vỡ cả lô:** vi phạm ràng buộc KHÁC với ràng buộc mà
  `ON CONFLICT` nhắm tới (ví dụ `uq_sales_area_source_row_hash` giữa hai lô khác
  nhau). Lúc đó lô rollback sạch và chuyển sang `failed` — không còn treo `pending`,
  nhưng cũng không nạp được phần hợp lệ.
* **Chưa chạy trên dữ liệu thật của dự án pilot.** Toàn bộ kiểm chứng dùng file dựng
  trong test.
* **8 lỗi ruff và lệch format ở `scripts/log_*.py`, `alembic/env.py`,
  `alembic/versions/0001_initial_schema.py`, `src/logging_config.py`** — có sẵn từ
  trước, KHÔNG sửa trong đợt này để giữ diff gọn cho người review.

## Phạm vi: S2 và S3 KHÔNG được cài đặt

> **Cập nhật 09/08:** phần NỀN của S2 đã được cài đặt ở đợt sau (xem mục đầu file).
> Đoạn dưới đây mô tả đúng trạng thái tại thời điểm 08/08 và được giữ nguyên văn.
> S3 vẫn chưa có: không có `units`, `deals`, hay absorption calculator mới.

Đợt này chỉ làm **S1** (sửa lỗi tại chỗ trên luồng nạp CSV). Xác nhận rõ:

* **KHÔNG** cài S2: không có `crm_source_records`, không có `source_system` /
  `source_entity` / `external_batch_id` / `sync_mode` / `schema_version` /
  `last_source_cursor` / `error_summary`, không có endpoint `/api/v1/sync/*`, không
  có parser JSON, không tổng quát hoá `upload_files` thành `sync_runs` hay
  `upload_errors` thành `validation_errors`.
* **KHÔNG** cài S3: không có bảng `units`, `deals`, `customers`,
  `customer_interactions`; `absorption_daily` không thêm `units_remaining`;
  `AbsorptionCalculatorService` vẫn đọc `sales_records` như cũ.
* **KHÔNG** đổi API upload: vẫn `POST /api/v1/files/upload`, cùng tham số, cùng mã
  trạng thái. Không đổi tên bảng, file hay class nào. Không thêm dependency.
* Phân tích đầy đủ ba phương án: `docs/product/crm_ingestion_architecture_review.md`.

## Final Result — đợt 2026-08-08

**DONE**

Căn cứ: migration chạy được cả tiến lẫn lùi trên database thật (3 test); toàn bộ
suite 347 passed / 0 failed, không có lỗi nào chưa giải thích được; lô hỏng kết thúc
ở `failed` và có test chứng minh nó không còn treo `pending`. Mục `typecheck` không
chạy được vì mypy chưa cài — đã ghi rõ ở bảng lệnh, không tính là đạt.

---

# Đợt 2026-08-07 (giữ nguyên)

> Giữ nguyên văn để đối chiếu. Vài con số dưới đây đã cũ sau đợt 08/08: head giờ là
> `0005_idempotent_csv_ingestion` (5 revision, không phải 4), và suite là 347 test
> chứ không phải 329.

## Repository Audit

**Database engine:** PostgreSQL 15 (Docker Compose, service `db`). Không có H2 hay
SQLite ở bất kỳ đâu — mọi test chạm DB đều chạy trên PostgreSQL thật.

**Migration tool:** Alembic (không phải Flyway/Liquibase). 4 revision, head là
`0004_cover_image_public_id`.

**Tables discovered:** 21 bảng (+ `alembic_version`), theo bậc phụ thuộc khoá ngoại
— cũng chính là thứ tự chèn của seed:

| Bậc | Bảng |
|---|---|
| 0 | `users` |
| 1 | `projects`, `settings`, `refresh_tokens`, `audit_logs` |
| 2 | `areas`, `upload_files`, `forecast_jobs` |
| 3 | `sales_records`, `inventory_snapshots`, `absorption_daily`, `upload_errors`, `user_areas`, `forecasts` |
| 4 | `forecast_points`, `explanations`, `alerts`, `suggestions`, `llm_calls` |
| 5 | `proposals` |
| 6 | `approvals` |

Đồ thị trên dựng từ `pg_constraint` của database đang chạy, không phải từ trí nhớ.

**Seed command:**

```bash
python -m scripts.seed_dev            # nạp / cập nhật (idempotent)
python -m scripts.seed_dev --reset    # xoá bản ghi của seed rồi nạp lại
python -m scripts.seed_dev --counts   # chỉ in số dòng, không ghi
```

**Test commands:**

```bash
bash scripts/test_db.sh                      # mặc định: tests/test_services/test_import_records.py
TEST_TARGET=tests bash scripts/test_db.sh    # toàn bộ suite
```

Script tự dựng service `db`, chờ `pg_isready`, tạo `<POSTGRES_DB>_test`, chạy
`alembic upgrade head`, rồi gọi pytest với `TEST_DATABASE_URL`. Không có
Testcontainers trong repo nên đây là thiết lập an toàn nhỏ nhất hiện có.

**Ghi chú về từ vựng đề bài.** Yêu cầu viết theo hệ Java/Spring (Flyway, JPA
entity/repository, Testcontainers, H2). Repo này là Python/FastAPI/SQLAlchemy
Core/Alembic. Các mục dưới đây ánh xạ sang ngăn xếp thật chứ không giả vờ có
những thứ không tồn tại.

## Seed Data

**Tables populated:** cả 21/21 bảng, không bảng nào còn rỗng.

**Row counts** (tổng 1085 dòng):

| Bảng | Dòng | Bảng | Dòng |
|---|---|---|---|
| `users` | 6 | `forecast_jobs` | 4 |
| `projects` | 4 | `forecasts` | 6 |
| `areas` | 10 | `forecast_points` | 180 |
| `upload_files` | 8 | `explanations` | 4 |
| `upload_errors` | 12 | `alerts` | 6 |
| `sales_records` | 360 | `suggestions` | 6 |
| `inventory_snapshots` | 72 | `proposals` | 6 |
| `absorption_daily` | 360 | `approvals` | 3 |
| `user_areas` | 10 | `llm_calls` | 6 |
| `settings` | 5 | `audit_logs` | 12 |
| `refresh_tokens` | 5 | | |

**Relationships:** khoá ngoại hợp lệ toàn bộ — `test_no_row_violates_any_foreign_key`
đọc từng ràng buộc trong `pg_constraint` rồi LEFT JOIN đối chiếu, 0 dòng mồ côi.
1-N: dự án→phân khu→bản ghi bán hàng, job→forecast→điểm dự báo/cảnh báo/gợi ý.
N-N: `user_areas` có người phụ trách nhiều phân khu và phân khu có nhiều người.
Chuỗi tự tham chiếu: `refresh_tokens.replaced_by` (token bị xoay vòng).

**Statuses & edge cases:**

* Đủ mọi giá trị của 15 cột trạng thái/enum — có test tham số hoá khẳng định từng
  cột, gồm `projects.status` và `areas.status` đủ bốn giá trị
  `pending/active/rejected/archived`.
* Ràng buộc CHECK điều kiện chéo đều được tôn trọng: `finished_at` chỉ có ở job đã
  kết thúc, `closed_at` chỉ có ở alert/proposal không còn `open`, `error_code` chỉ
  có ở llm_call không `success`.
* Trường tuỳ chọn có cả hai trạng thái (12 cột được kiểm tra): dự án không ảnh bìa,
  `headline`/`introduce` rỗng, `uploaded_by` NULL, lỗi không gắn cột,
  forecast không có `sellout_date`/`mape`.
* Biên: `areas.total_units = 0`; ngày bán bằng 0 xen kẽ trong chuỗi; token đã hết
  hạn, token đã thu hồi, người dùng `is_active = false`.
* Chuỗi thời gian 60 ngày × 6 phân khu, có điểm `is_observed = false` (lấp đầy
  khoảng trống) và `data_quality_status` cả `ok`/`warning`/`error`.
* **Không** có bản ghi cố tình sai trong bộ dữ liệu nền — các trường hợp không hợp
  lệ nằm ở test, không nằm trong seed.
* Dữ liệu hư cấu hoàn toàn: tên có tiền tố `DEMO`, email `@demo.local`, IP dải
  TEST-NET-2 (`198.51.100.0/24`), `password_hash` cố ý không phải hash hợp lệ nên
  không tài khoản nào đăng nhập được.

**Idempotency:** mọi khoá chính là `uuid5(NS_SEED, "<bảng>:<khoá nghiệp vụ>")` nên
lần chạy thứ hai rơi đúng vào `ON CONFLICT (pk) DO UPDATE`. Đã kiểm chứng: lần 1,
lần 2 và lần 3 (`--reset`) đều cho 1085 dòng, không lệch một dòng nào. `--reset`
chỉ xoá dòng thuộc seed và giữ lại dòng còn bị dữ liệu ngoài seed tham chiếu.

## Tests Executed

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh` (chạy trên DB test vừa dựng lại)
**Result:** PASS
**Passed/failed:** 329 passed / 0 failed
**Notes:** nền trước khi làm việc này là 288 passed; 41 test mới đều là test của seed.

**Command:** `TEST_TARGET=tests bash scripts/test_db.sh` (chạy lần hai, KHÔNG dựng lại DB)
**Result:** PASS
**Passed/failed:** 329 passed / 0 failed
**Notes:** chứng minh suite lặp lại được, không phụ thuộc database sạch tinh.

**Command:** `TEST_TARGET=tests/test_scripts bash scripts/test_db.sh`
**Result:** PASS
**Passed/failed:** 27 passed / 0 failed
**Notes:** seed trên DB rỗng, seed lần hai, `--reset`, tính tất định của id, toàn vẹn
khoá ngoại, mọi CHECK, không trùng khoá nghiệp vụ, độ phủ enum, độ phủ trường NULL.

**Command:** `TEST_TARGET=tests/test_api/test_seeded_dashboard.py bash scripts/test_db.sh`
**Result:** PASS
**Passed/failed:** 14 passed / 0 failed
**Notes:** mọi endpoint đọc chạy trên dữ liệu seed; đối chiếu tên trường với những gì
`frontend/src/api/endpoints.js` bóc tách; lọc theo ngày, thứ tự sắp xếp, 404, 422.

**Command:** `alembic upgrade head` trên database mới tạo (rỗng hoàn toàn)
**Result:** PASS
**Passed/failed:** 4/4 revision chạy sạch
**Notes:** mô phỏng clean checkout; sau đó seed chạy được ngay, không cần bước thủ công.

**Command:** khởi động ứng dụng trên DB rỗng vừa migrate + seed (ASGI in-process)
**Result:** PASS
**Passed/failed:** `GET /api/v1/projects` → 200 (4 dự án), `GET /api/v1/absorption/summary` → 200
**Notes:** thẻ tổng hợp trả số thật (`units_sold: 128`, `avg_velocity_30d: 2.2667`), không phải 0.

**Command:** `ruff check` và `ruff format --check` trên `src scripts tests`
**Result:** PARTIAL
**Passed/failed:** file mới sạch; còn 8 lỗi lint + 5 file lệch format
**Notes:** toàn bộ nằm ở `scripts/log_antigravity.py`, `log_hook.py`, `log_manual.py`,
`submit_log.py` — tiện ích ghi log có sẵn, không liên quan việc này nên không sửa
để giữ thay đổi gọn.

**Command:** rà chất lượng dữ liệu bằng SQL trên DB đã seed
**Result:** PASS
**Passed/failed:** 6/6 phép kiểm sạch
**Notes:** 0 mốc thời gian thiếu múi giờ, 0 điểm dự báo nằm trước ngày cắt dữ liệu,
0 phân khu bán vượt quỹ căn, 0 tồn kho âm, 0 email trùng, 0 bảng còn rỗng.

## Bugs Fixed

**Bug:** `python -m scripts.seed_dev --reset` nổ `ForeignKeyViolationError` khi có
dòng ngoài seed trỏ vào dòng của seed (ví dụ lập trình viên tạo tay một dự án và
chọn `created_by` là tài khoản demo).
**Root cause:** `--reset` xoá mọi dòng thuộc seed theo thứ tự ngược phụ thuộc, nhưng
không xét tới việc dòng cha vẫn còn bị dữ liệu KHÔNG thuộc seed tham chiếu.
Dùng `ON DELETE CASCADE` để chữa thì còn tệ hơn: sẽ xoá luôn dữ liệu người dùng.
**Files changed:** `scripts/seed_dev.py`
**Fix:** thêm `_referencing_columns()` đọc đồ thị khoá ngoại từ `pg_constraint`, và
`_delete_seed_rows()` gắn `NOT EXISTS` cho mỗi bảng con — giữ lại dòng còn bị tham
chiếu (chúng được upsert làm mới ngay sau đó). Vì đang xoá ngược thứ tự phụ thuộc,
con của seed đã biến mất trước, nên phần còn tham chiếu chắc chắn là của người dùng.
**Regression test:** `tests/test_scripts/test_seed_dev.py::test_reset_only_deletes_rows_the_seed_owns`

**Bug:** dự án mà giao diện mở lên mặc định gần như không có dữ liệu.
**Root cause:** `GET /projects` sắp theo `name`, còn `activeProjectId()` ở frontend
lấy `rows[0]`. Bản seed đầu chỉ nhồi dữ liệu cho một dự án, và theo thứ tự chữ cái
dự án đó không đứng đầu → người mở demo thấy dashboard trắng.
**Files changed:** `scripts/seed_dev.py`
**Fix:** `BUSY_AREAS` mở rộng để chạm cả bốn dự án; thêm `PROJECT_FILES` để
`sales_records.file_id` luôn trỏ vào tệp cùng dự án với phân khu, thêm `F07`/`F08`.
**Regression test:** `tests/test_api/test_seeded_dashboard.py::test_the_default_project_the_ui_opens_is_not_empty`

**Bug:** thêm test mới làm 175 test đang xanh chuyển thành lỗi.
**Root cause:** fixture của tôi chỉ dọn database ở đầu test. Dữ liệu seed còn lại
sau đó làm `DELETE FROM areas`/`projects` trong `clean_db` của các module khác nổ
khoá ngoại — những fixture đó chỉ biết 8 bảng của luồng nạp file.
**Files changed:** `tests/test_scripts/test_seed_dev.py`, `tests/test_api/test_seeded_dashboard.py`
**Fix:** dọn cả hai đầu (`yield` rồi TRUNCATE lại), giữ phạm vi ảnh hưởng nằm trong
module của mình thay vì sửa fixture của các module khác.
**Regression test:** chạy `TEST_TARGET=tests bash scripts/test_db.sh` hai lần liên
tiếp không dựng lại database — 329 passed cả hai lần.

**Bug:** `thư mục uploads/` chứa file người dùng tải lên nhưng không bị git bỏ qua.
**Root cause:** `.gitignore` không có mục nào cho `settings.upload_dir` (`./uploads`).
**Files changed:** `.gitignore`
**Fix:** thêm `uploads/`.
**Regression test:** không có — thay đổi cấu hình git, `git status --porcelain`
không còn liệt kê `uploads/`.

## Known Issues

* **Không có tầng xác thực nào trong mã nguồn.** Không có endpoint đăng nhập, không
  có middleware JWT, không có dependency phân quyền. Ba bảng `users`,
  `refresh_tokens`, `user_areas` đã có trong schema và đã được seed, nhưng chưa
  code nào đọc chúng. Vì vậy hạng mục "test authentication và authorization" của
  yêu cầu **không có gì để chạy** — đây là báo cáo trung thực, không phải bỏ sót.
  Khi MVP 3 làm auth, dữ liệu seed đã sẵn sàng: 3 vai trò, tài khoản bị vô hiệu
  hoá, token hết hạn/đã thu hồi/đã xoay vòng.
* **Suite phụ thuộc thứ tự chạy ở mức mong manh.** `clean_db` của các module cũ chỉ
  xoá 8 bảng của luồng nạp file. Test mới của tôi đã tự dọn nên hiện tại xanh, nhưng
  bất kỳ test nào sau này ghi vào `forecasts`/`users` mà không tự dọn sẽ làm vỡ
  module khác. Cách chữa gốc là một fixture dọn dẹp dùng chung ở `tests/conftest.py`.
* **8 lỗi ruff và 5 file lệch format** trong `scripts/log_*.py` và
  `scripts/submit_log.py`. Có sẵn từ trước, không thuộc phạm vi việc này.
* **Frontend chưa được kiểm chứng bằng trình duyệt trên dữ liệu seed.** Hợp đồng JSON
  đã được đối chiếu bằng test với đúng các tên trường mà `endpoints.js` bóc tách,
  nhưng chưa có ai mở giao diện lên xem.
* **Seed chưa chạm dữ liệu tham chiếu của môi trường production.** Đúng như yêu cầu:
  đây là script dev/test, không nằm trong migration, và migration không chứa dữ liệu.

## Final Result

PASS

---

# Phụ lục — tài liệu kiến trúc (giữ từ bản trước)

Phần dưới đây là tài liệu tham chiếu đã có sẵn trong repo (luồng end-to-end, hợp
đồng API, lược đồ CSDL, ghi chú Alembic). Giữ lại vì báo cáo trạng thái ở trên
không thay thế được nội dung này.

**Ngày:** 2026-08-06 · **Nhánh:** `feature/Vuong-Pipeline-#10` · **Commit mới nhất:** `027f87a`

---

## 1. Luồng end-to-end

Tạo master data là bước ĐỨNG TRƯỚC nạp dữ liệu: không có dự án thì không upload
được (API upload bắt buộc `project_id`), không có phân khu thì `ImportService`
không tra được `area_id` cho từng dòng bán hàng.

```
POST /api/v1/projects   { name, launch_date }
  └─ ProjectService.create_project()          src/services/projects.py
       ├─ sinh UUID, created_at = datetime.now(UTC), status = 'active'
       ├─ INSERT INTO projects            (1 transaction, commit 1 lần)
       └─ 201 ProjectDetail { project_id, name, launch_date, status, created_at, … }

PATCH /api/v1/projects/{project_id}   { name?, launch_date?, headline?, introduce? }
  └─ ProjectService.update_project()  → 200 ProjectDetail

POST /api/v1/areas      { project_id, area_name, unit_type, bedrooms, area_sqm, total_units }
  └─ ProjectService.create_area()             src/services/projects.py
       ├─ SELECT projects.status WHERE id = project_id   ─┐
       │    · không có       → CatalogRejectedError PROJECT_NOT_FOUND   │ CÙNG một
       │    · khác 'active'  → CatalogRejectedError PROJECT_NOT_ACTIVE  │ transaction
       ├─ INSERT INTO areas                                             │
       │    · vi phạm uq_areas_project_name_unit_type → DUPLICATE_AREA ─┘
       └─ 201 AreaDetail { area_id, project_id, area_name, unit_type, status, … }

PATCH /api/v1/areas/{area_id}   { area_name?, unit_type?, bedrooms?, area_sqm?,
                                  total_units?, headline?, introduce? }
  └─ ProjectService.update_area()    → 200 AreaDetail
       · không có phân khu → AREA_NOT_FOUND (404)
       · trùng (project_id, area_name, unit_type) → DUPLICATE_AREA (409)

        ↓ sau đó mới tới luồng đã có từ trước
POST /api/v1/files/upload → worker parse → PostgreSQL → GET /api/v1/absorption
```

Kiểm tra dự án và INSERT nằm trong **cùng một transaction**: tách ra thì giữa hai
bước dự án có thể bị đổi trạng thái, và phân khu vẫn lọt vào một dự án đã đóng.

---

## 2. File thay đổi và trách nhiệm

| File | Trách nhiệm |
|---|---|
| `src/services/projects.py` | **Mới.** `ProjectService.create_project()` / `create_area()`, `CatalogRejectedError` (mang `error_code`, không biết gì về HTTP) |
| `src/api/dashboard.py` | Thêm 2 route POST vào đúng router đang giữ `GET /projects` và `GET /areas`; ánh xạ `error_code` → mã HTTP |
| `src/models/schemas.py` | `ProjectCreate`, `ProjectDetail`, `AreaCreate`, `AreaDetail`, kiểu `NonBlankStr` |
| `src/models/tables.py` | Bổ sung `headline`, `introduce`, `cover_image_url` vào bản chiếu Core của `projects` và `areas` |
| `alembic/versions/0003_content_column_defaults.py` | **Mới.** Đặt `DEFAULT ''` cho `headline` / `introduce` |
| `tests/test_api/test_catalog.py` | 59 test chạy trên PostgreSQL thật (34 tạo mới + 25 sửa) |
| `frontend/src/pages/CatalogPage.jsx` | **Mới.** Trang TẠO và SỬA dự án / phân khu: form, trạng thái tải – thành công – lỗi |
| `frontend/src/api/endpoints.js` | Thêm `createProject`, `createArea`, `updateProject`, `updateArea`, `listProjectZones`, `listProjectsForImport`; `listAreas(projectId)` nhận tham số; **gỡ conflict marker còn sót** |
| `frontend/src/api/client.js` | Thêm `api.patch`, `api.del` |
| `src/services/images.py` | **Mới.** `ImageService` dùng chung cho cả hai thực thể, bọc `CloudinaryClient` để test thay được |
| `alembic/versions/0004_cover_image_public_id.py` | **Mới.** Cột `cover_image_public_id` |
| `tests/test_api/test_images.py` | **Mới.** 63 test — 49 ở tầng service + 14 ở tầng HTTP, mọi ca chạy cho CẢ dự án lẫn phân khu |
| `frontend/src/App.jsx` | Thêm route `/catalog` và mục điều hướng "Danh mục" |
| `frontend/src/pages/ImportSelectPage.jsx` | Đổi sang `listProjectsForImport` (hệ quả của việc gỡ conflict) |

POST được đặt cùng file với GET tương ứng để mỗi đường dẫn chỉ định nghĩa ở một
chỗ; tách file riêng thì `/projects` nằm rải hai nơi.

---

## 3. API

### POST /api/v1/projects → 201

| Trường | Kiểu | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `name` | str | ✔ | strip xong phải khác rỗng, ≤ 255 |
| `launch_date` | date | ✔ | ngày hợp lệ |
| `headline` | str | — | mặc định `""`, ≤ 255 |
| `introduce` | str | — | mặc định `""` |
| `cover_image_url` | str \| null | — | mặc định `null` |

Trả `ProjectDetail`: `project_id`, `name`, `launch_date`, `status`, `headline`,
`introduce`, `cover_image_url`, `created_at`.

### POST /api/v1/areas → 201

| Trường | Kiểu | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `project_id` | UUID | ✔ | phải tồn tại và đang `active` |
| `area_name` | str | ✔ | strip xong phải khác rỗng |
| `unit_type` | str | ✔ | strip xong phải khác rỗng |
| `bedrooms` | int | ✔ | `>= 0` |
| `area_sqm` | Decimal | ✔ | `> 0` |
| `total_units` | int | ✔ | `>= 0` |
| `headline` / `introduce` / `cover_image_url` | | — | như trên |

Trả `AreaDetail`: `area_id`, `project_id`, `area_name`, `unit_type`, `bedrooms`,
`area_sqm`, `total_units`, `status`, `headline`, `introduce`, `cover_image_url`,
`created_at`.

### PATCH /api/v1/projects/{project_id} → 200

Body: `name?`, `launch_date?`, `headline?`, `introduce?` — **chỉ ghi trường có
mặt**, trường vắng mặt giữ nguyên. Cần ít nhất một trường, nếu không → 422
`NO_CHANGES`.

### PATCH /api/v1/areas/{area_id} → 200

Body: `area_name?`, `unit_type?`, `bedrooms?`, `area_sqm?`, `total_units?`,
`headline?`, `introduce?`.
`project_id` và `status` **không có trong body** nên không sửa được qua API; gửi
kèm cũng bị bỏ qua. Gọi thẳng service với hai trường đó → `FIELD_NOT_EDITABLE`.

### Mã lỗi

| Tình huống | HTTP | `error_code` |
|---|---|---|
| Rỗng / chỉ khoảng trắng ở `name`, `area_name`, `unit_type` | 422 | (pydantic) |
| `launch_date` sai định dạng, `bedrooms < 0`, `area_sqm <= 0`, `total_units < 0`, `project_id` sai UUID | 422 | (pydantic) |
| Dự án không tồn tại | **404** | `PROJECT_NOT_FOUND` |
| Dự án không ở trạng thái `active` | **409** | `PROJECT_NOT_ACTIVE` |
| Trùng `(project_id, area_name, unit_type)` | **409** | `DUPLICATE_AREA` |
| Phân khu không tồn tại (PATCH) | **404** | `AREA_NOT_FOUND` |
| PATCH không gửi trường nào | 422 | `NO_CHANGES` |
| Sửa trường không được phép (gọi thẳng service) | 422 | `FIELD_NOT_EDITABLE` |

`"   "` bị loại đúng như `""` vì schema strip trước rồi mới đo độ dài.

### Nội dung hiển thị: `headline` và `introduce`

Có mặt ở **cả ba luồng** của dự án lẫn phân khu:

| Luồng | Cách dùng |
|---|---|
| Đọc | `GET /projects` và `GET /areas` trả kèm hai trường, để form sửa đổ sẵn giá trị cũ mà không cần thêm endpoint chi tiết |
| Tạo | `POST /projects`, `POST /areas` — tuỳ chọn, không gửi thì DB điền `''` (DEFAULT của 0003) |
| Sửa | `PATCH /projects/{id}`, `PATCH /areas/{id}` |

Khác `name` / `area_name`: hai trường này **được phép rỗng** — chúng là nội dung
hiển thị nên người dùng phải xoá được. Vì vậy dùng `str | None` chứ không dùng
`NonBlankStr`. `headline` giới hạn 255 ký tự (khớp `VARCHAR(255)` dưới DB), quá
thì 422 ngay ở API thay vì để vỡ ở tầng ghi.

`cover_image_url` không sửa qua PATCH mà qua nhóm endpoint ảnh riêng ở trên —
đổi ảnh phải kèm thao tác trên Cloudinary, không chỉ đổi một chuỗi trong DB.

### Ảnh bìa: `POST` · `GET` · `PUT` · `DELETE` `/{projects|areas}/{id}/image`

Mỗi dự án / phân khu có **tối đa một ảnh**, lưu trên Cloudinary.

| Method | Ý nghĩa | Thành công |
|---|---|---|
| `GET` | Xem ảnh hiện tại | 200 `ImageDetail { owner_id, url, public_id }` |
| `POST` | Tải ảnh lần đầu | 201 |
| `PUT` | Thay ảnh ("đặt thành", chấp nhận cả khi chưa có) | 200 |
| `DELETE` | Xoá ảnh | 204 |

| Tình huống | HTTP | `error_code` |
|---|---|---|
| Dự án / phân khu không tồn tại | 404 | `PROJECT_NOT_FOUND` · `AREA_NOT_FOUND` |
| Chưa có ảnh (GET / DELETE) | 404 | `IMAGE_NOT_FOUND` |
| POST khi đã có ảnh | 409 | `IMAGE_ALREADY_EXISTS` |
| Sai định dạng | 415 | `UNSUPPORTED_IMAGE_FORMAT` |
| Quá dung lượng | 413 | `IMAGE_TOO_LARGE` |
| File rỗng | 422 | `EMPTY_IMAGE` |
| Chưa cấu hình Cloudinary | 503 | `STORAGE_NOT_CONFIGURED` |
| Cloudinary tải lên / xoá hỏng | 502 | `STORAGE_UPLOAD_FAILED` · `STORAGE_DELETE_FAILED` |

`GET /projects` và `GET /areas` trả kèm `cover_image_url`, nên danh sách hiển thị
được ảnh mà không phải gọi thêm endpoint.

**Đính ảnh lúc TẠO** — `POST /projects` và `POST /areas` vẫn nhận JSON như cũ,
KHÔNG đổi sang multipart. Giao diện làm hai bước: tạo bản ghi → nếu người dùng có
chọn ảnh thì gọi `POST /{id}/image`. Lý do: `public_id` trên Cloudinary gắn theo
id của bản ghi, mà id chỉ có sau khi tạo; đổi endpoint tạo sang multipart sẽ phá
hợp đồng JSON mà mọi client hiện có đang dùng. Ảnh là tuỳ chọn nên upload hỏng
KHÔNG xoá bản ghi vừa tạo — bản ghi không ảnh vẫn hợp lệ; giao diện báo rõ để
người dùng thử lại ở khối sửa.

**Giữ DB và kho ảnh đồng bộ** — thứ tự thao tác được chọn để không bao giờ có
file mồ côi:

1. Kiểm tra đuôi + dung lượng + rỗng.
2. Upload lên Cloudinary.
3. Ghi DB. Hỏng ở bước này → **xoá ngay ảnh vừa upload** rồi mới ném lỗi.
4. Chỉ khi DB đã commit mới xoá ảnh **cũ**.

Khi xoá: Cloudinary trước, DB sau. Cloudinary hỏng thì DỪNG, không đụng DB —
xoá tham chiếu trước là mất `public_id`, ảnh nằm lại vĩnh viễn không ai dọn được.
`public_id` cố định theo thực thể (`project-{uuid}`) nên thay ảnh là ghi đè đúng
chỗ, không sinh rác.

---

## 4. Cơ sở dữ liệu

**Trạng thái:** `pending` · `active` · `rejected` · `archived`
(`ck_projects_status`, `ck_areas_status`). MVP 1 tạo xong là `active` ngay —
`INITIAL_STATUS = "active"`; các cột `created_by`, `reviewed_by`, `reviewed_at`,
`review_reason` để `NULL`.

**Ràng buộc bảng `projects`:** `pk_projects`, `ck_projects_status`,
`fk_projects_created_by`, `fk_projects_reviewed_by`.
Không có UNIQUE trên `name` → **hai dự án trùng tên là hợp lệ** (một khu đô thị
có thể mở bán nhiều đợt).

**Ràng buộc bảng `areas`:** `pk_areas`, `fk_areas_project_id`,
`fk_areas_created_by`, `fk_areas_reviewed_by`,
`uq_areas_project_name_unit_type`, `ck_areas_status`,
`ck_areas_area_name_not_blank`, `ck_areas_unit_type_not_blank`,
`ck_areas_bedrooms_nonnegative`, `ck_areas_area_sqm_positive`,
`ck_areas_total_units_nonnegative`.

**Transaction / rollback:** mỗi thao tác nằm trong một `session.begin()` duy
nhất, commit đúng một lần. Vỡ ở bất kỳ bước nào thì rollback toàn bộ — không để
lại phân khu nửa vời. `IntegrityError` được nhận diện theo TÊN ràng buộc, chỉ
`uq_areas_project_name_unit_type` mới thành `DUPLICATE_AREA`; các
`IntegrityError` khác vẫn nổi lên như sự cố thật, không bị nuốt.

---

## 5. Alembic

| Revision | down_revision | Nội dung |
|---|---|---|
| `0001_initial_schema` | — | 21 bảng theo ERD SRS §5.6 |
| `0002_project_area_approval` | `0001_initial_schema` | Cột workflow duyệt + `headline`, `introduce`, `cover_image_url` cho `projects` và `areas` |
| `0004_cover_image_public_id` | `0003_content_column_defaults` | **Mới.** Thêm `cover_image_public_id` (Text, NULL) cho `projects` và `areas` |
| `0003_content_column_defaults` | `0002_project_area_approval` | `ALTER COLUMN … SET DEFAULT ''` cho `headline` và `introduce` ở cả hai bảng |

`0004` cần thiết vì `cover_image_url` đủ để HIỂN THỊ nhưng không đủ để XOÁ: API
xoá của Cloudinary nhận `public_id`, còn suy ngược từ URL thì dễ vỡ (URL kèm
version, transformation, đuôi file). Không lưu public_id là không dọn được ảnh,
để lại file mồ côi.

Tính năng sửa dự án / phân khu **không cần migration mới** — chỉ ghi vào cột đã
có. `alembic current` vẫn là `0003_content_column_defaults (head)`.

**Vì sao cần 0003:** `0002` khai `headline` và `introduce` là `NOT NULL` nhưng
KHÔNG có `server_default`. Mọi câu `INSERT` không liệt kê hai cột đó đều vỡ với
`NotNullViolationError` — đo được **42 lỗi + 2 test hỏng**, gồm cả
`ImportService` khi nạp template `areas` và toàn bộ fixture tạo project.
Không sửa trực tiếp `0002` vì nó ĐÃ chạy trên database dev; sửa migration đã áp
dụng thì DB cũ và DB dựng mới sẽ trôi khỏi nhau. `0003` có cả `upgrade()` và
`downgrade()`.

---

## 6. Test

`tests/test_api/test_catalog.py` — **34 test**, chạy trên PostgreSQL thật, mọi
khẳng định "đã lưu" đều đọc lại bằng `SELECT` chứ không tin response:

- Tạo dự án 201, `status='active'`, đọc lại từ DB, trim khoảng trắng ở `name`
- Từ chối `name` rỗng / chỉ khoảng trắng / thiếu; `launch_date` sai (4 trường hợp)
- Trùng tên dự án vẫn tạo được (đúng theo schema)
- Tạo phân khu 201, đúng `project_id`, `status='active'`, đọc lại từ DB
- Dự án không tồn tại → 404; dự án `pending`/`rejected`/`archived` → 409
- 9 trường hợp dữ liệu sai → 422
- Trùng `(project_id, area_name, unit_type)` → 409, không phải traceback
- Cùng tên khác `unit_type`, hoặc cùng tên ở dự án khác → vẫn tạo được
- Phân khu của dự án A không dính sang dự án B
- Rollback khi chèn hỏng; khoá ngoại chặn phân khu trỏ vào dự án không có thật

**Test sửa (PATCH) — 25 test:**
- Sửa dự án lưu xuống DB; PATCH một phần không xoá trường còn lại
- Gửi `status` kèm theo không làm đổi trạng thái
- 404 khi dự án / phân khu không tồn tại; 422 khi UUID sai
- 422 cho tên rỗng, ngày sai, không gửi trường nào
- Sửa phân khu lưu xuống DB; quan hệ với dự án cha KHÔNG đổi dù gửi `project_id`
- Đổi tên trùng phân khu khác trong cùng dự án → 409, và **rollback**: giá trị cũ
  giữ nguyên, không dính phần sửa nào khác
- Cùng tên ở dự án khác vẫn đổi được
- 8 trường hợp dữ liệu sai → 422, bản ghi giữ nguyên
- Gọi thẳng service với `project_id` → `FIELD_NOT_EDITABLE`

### Lệnh đã chạy và kết quả thực tế

| Lệnh | Kết quả |
|---|---|
| `python -m compileall src` | OK |
| `alembic current` | `0003_content_column_defaults (head)` |
| `alembic heads` | `0003_content_column_defaults (head)` |
| `alembic upgrade head` | chạy `0002 → 0003` |
| `pytest -q` (có DB) | **288 passed** |
| `pytest -q` (không DB) | **110 passed, 178 skipped** |
| `bash scripts/test_db.sh` | dựng lại DB test từ migration |
| `cd frontend && npm run build` | **xanh** (`built in 2.50s`) |
| `ruff check src/ tests/` | sạch |

---

## 6b. Luồng giao diện danh mục

Trang `/catalog` ("Danh mục") trên thanh điều hướng:

0. **Tạo dự án** → `POST /projects`; tạo xong tự chọn dự án đó.
   Cả 4 form (tạo/sửa × dự án/phân khu) đều có ô "Tiêu đề hiển thị" và "Mô tả"
   dựng từ component chung `ContentFields` — `introduce` là textarea vì cột dưới
   DB là `Text`.
   **Tạo phân khu** dưới dự án đang chọn → `POST /areas`.
   Khi chưa có dự án nào, trang chỉ hiện khối tạo kèm dòng hướng dẫn — trước đây
   trang chỉ có phần sửa nên database rỗng là bế tắc (xem
   `docs/bugs.md` BUG-CATALOG-CREATE-001).
1. Chọn dự án → form hiện `name`, `launch_date` → **Lưu dự án** → `PATCH /projects/{id}`.
2. Chọn phân khu của dự án đó → form hiện `area_name`, `unit_type`, `bedrooms`,
   `area_sqm`, `total_units` → **Lưu phân khu** → `PATCH /areas/{id}`.
3. Trạng thái: "Đang tải danh mục…", nút chuyển "Đang lưu…" và bị khoá khi gửi,
   băng thông báo xanh khi thành công, băng đỏ kèm câu lỗi của backend khi hỏng
   (409 trùng phân khu, 404 không tìm thấy, 422 dữ liệu sai).

Không có form sửa `status` hay `project_id` vì backend không nhận hai trường đó.

`listAreas(projectId)` giờ nhận tham số — gọi không tham số vẫn giữ hành vi cũ
(Dashboard dùng dự án mặc định), nhưng trang Danh mục phải truyền dự án đang
chọn, nếu không đổi dự án mà danh sách phân khu vẫn là của dự án cũ.

---

## 7. Đã làm và còn thiếu

**Đã có**
- Tạo dự án và phân khu: `POST /api/v1/projects`, `POST /api/v1/areas`
- Đọc: `GET /projects`, `/areas`, `/absorption`, `/absorption/summary`
- Nạp dữ liệu: upload → parse → validate → PostgreSQL → `upload_files` /
  `upload_errors`, chống trùng file theo `(project_id, checksum)`, ngưỡng tỷ lệ
  lỗi 0.5, `errors.csv`, dọn file sau khi nạp xong
- Tính tốc độ hấp thụ: `absorption_daily` tính lại sau mỗi lần nạp

**Chưa làm**
- **Workflow duyệt.** Cột đã có (0002) nhưng chưa có endpoint duyệt/từ chối, và
  chưa chỗ nào lọc theo `status` — một dự án `pending` vẫn dùng được bình thường.
- **`created_by` / `reviewed_by` luôn NULL** vì chưa có xác thực (MVP 3).
- **Chưa có xoá dự án / phân khu.**
- **Đính ảnh lúc tạo là hai lời gọi API**, không phải một giao dịch nguyên tử.
  Bản ghi tạo xong mà ảnh hỏng thì bản ghi vẫn còn (không ảnh) — chấp nhận được
  vì ảnh là tuỳ chọn.
- **Chưa gọi Cloudinary thật lần nào**: môi trường phát triển chưa có khoá, nên
  toàn bộ test và kiểm chứng E2E dùng client giả. Đường đi qua SDK thật
  (`cloudinary.uploader.upload` / `.destroy`) cần một lần chạy tay với khoá thật.
- **Chưa có phân quyền**: dự án chưa có tầng xác thực, nên bất kỳ ai gọi được API
  đều sửa/xoá được ảnh. Phần "authorization" của yêu cầu chưa kiểm được.
- **Không có test tự động cho frontend**: dự án chưa cài test runner JS, phần FE
  chỉ được kiểm bằng `npm run build` và gọi API thật.
- `ImportSelectPage` hiển thị `location`, `zone_count`, `sold_pct` bằng giá trị
  0 / rỗng vì backend chưa có endpoint tổng hợp cho các số đó.
- **`headline` / `introduce` chưa bắt buộc có nội dung**: `NOT NULL` nhưng
  `DEFAULT ''`, nên tạo được bản ghi với nội dung rỗng.
- Không có bảng `notifications` (đúng phạm vi yêu cầu).

---

## 8. Chạy và việc thủ công còn lại

```bash
make up                                  # dựng lại image + chạy toàn bộ stack
bash scripts/test_db.sh                  # test tích hợp trên database test riêng
pytest tests/ -q                         # test không cần DB (phần cần DB sẽ skip)
alembic upgrade head                     # nâng schema lên 0003
```

**Biến môi trường Cloudinary** (đã thêm vào `.env.example`, giá trị thật KHÔNG
commit):

```
CLOUDINARY_CLOUD_NAME · CLOUDINARY_API_KEY · CLOUDINARY_API_SECRET
CLOUDINARY_FOLDER=absorptionforecast · IMAGE_MAX_SIZE=5242880
```

Thiếu ba biến đầu thì API ảnh trả 503 `STORAGE_NOT_CONFIGURED`; phần còn lại của
hệ thống vẫn chạy bình thường.

**Việc thủ công còn lại**

1. **`make up` để dựng lại image compose** — container `api`/`worker` vẫn đang
   chạy code cũ từ phiên trước.
2. **Quyết định `headline` / `introduce` có bắt buộc không.** Nếu nghiệp vụ yêu
   cầu có nội dung thật thì thêm `CHECK <> ''` trong một revision mới kèm bước
   điền dữ liệu cho dòng cũ, và đổi hai trường này thành bắt buộc ở API.
3. **Còn nhiều file chưa commit từ các phiên trước**: `src/db.py`,
   `src/services/excel_parser.py`, `src/services/file_upload.py`,
   `src/task_queue.py`, `src/worker.py`. Chúng có trên đĩa và test xanh, nhưng
   chưa vào git — nên commit trước khi ai đó clone hoặc reset.
4. Database test không tự nâng cấp khi migration đã ở head; xoá
   `<POSTGRES_DB>_test` rồi để `scripts/test_db.sh` dựng lại.
