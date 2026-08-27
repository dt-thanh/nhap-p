# Governance API — `src/api/governance.py`

Status: **Implemented, P5 (2026-08-25).** Wires `0033`/`0034`'s schema to
HTTP. Full design rationale and state machine: `src/services/governance.py`
module docstring. Architecture context: `docs/ranking/ranking_consultant.md`
§21.1 (records the gap this closes).

All routes mounted at `/api/v1/governance`. Auth: `Authorization: Bearer
<token>` via `src/services/dashboard_auth.py` (same mechanism as
`/ranking/*`). No new auth system.

**Never writes `ranking_configs`, `ranking_scores`, or `ranking_runs`.**
Publishing a weight set still requires `POST /ranking/configs` and
`POST /ranking/configs/{version}/publish` (existing, `src/api/ranking.py`) —
this API only tracks the proposal/review/justification/evidence trail around
that decision.

## Identity model — read before integrating

`DashboardPrincipal` (the thing `Depends(require_role(...))` resolves) carries
**only `role` and `project_scope` — no per-person identifier.** Every route
below therefore takes `*_expert_id` as an explicit request field, resolved
against `expert_profiles` via `identity_subject` (caller-supplied, e.g. an
email or SSO subject). This mirrors the existing precedent in
`src/services/ranking_config.py::create_draft(created_by: str, ...)`, which
has never derived "who did this" from the auth principal either. **Nothing
here validates that the caller IS the expert_id they claim** — that gap is
open question D18, not silently resolved by this implementation.

## Endpoints

| Method | Path | Min role | Purpose |
|---|---|---|---|
| POST | `/governance/experts` | viewer | Self-register / fetch expert profile by `identity_subject` (idempotent) |
| GET | `/governance/experts/{id}` | viewer | Fetch one expert profile |
| POST | `/governance/proposals` | operator | Create a proposal (`draft`) against a `base_config_id` |
| GET | `/governance/proposals` | viewer | List proposals, filter by `project_id`/`status` |
| GET | `/governance/proposals/{id}` | viewer | Fetch one proposal |
| PATCH | `/governance/proposals/{id}/config` | admin | Attach an existing `ranking_configs` draft (does not create one) |
| POST | `/governance/proposals/{id}/submit` | operator | `draft` → `submitted` (requires ≥1 justification) |
| POST | `/governance/proposals/{id}/withdraw` | operator | `draft`/`submitted`/`under_review` → `withdrawn` |
| POST | `/governance/proposals/{id}/publish` | admin | `approved` → `published` — **verifies**, never performs, the underlying config publish |
| POST | `/governance/proposals/{id}/justifications` | operator | Create/update a per-feature justification (only while `draft`) |
| GET | `/governance/proposals/{id}/justifications` | viewer | List justifications for a proposal |
| POST | `/governance/evidence` | operator | Register metadata for a file already on storage (no multipart upload here) |
| POST | `/governance/evidence/link` | operator | Link a document to a justification (idempotent) |
| GET | `/governance/justifications/{id}/evidence` | viewer | List documents linked to a justification |
| POST | `/governance/proposals/{id}/reviews` | admin | One reviewer, one decision (`approved`\|`rejected`\|`request_changes`) |
| GET | `/governance/proposals/{id}/reviews` | viewer | List reviews for a proposal |

Full request/response schemas: FastAPI auto-generates them at `/docs`
(OpenAPI) once the app is running — see `src/models/schemas.py` (section
`# --- Governance ---`) for the Pydantic source of truth.

## Full lifecycle (happy path)

```
1. POST /governance/experts                          {identity_subject} → expert_id
2. POST /governance/proposals                         {base_config_id, project_id, created_by_expert_id} → draft
3. POST /governance/proposals/{id}/justifications      one call per changed feature
4. POST /governance/evidence  (+ /evidence/link)       optional, attach supporting PDFs
5. POST /governance/proposals/{id}/submit              draft → submitted
6. POST /ranking/configs                               (existing) admin drafts the actual weight set
7. PATCH /governance/proposals/{id}/config             {proposed_config_id} attaches it to the proposal
8. POST /governance/proposals/{id}/reviews             {decision: "approved"} → approved (requires step 7 first)
9. POST /ranking/configs/{version}/publish             (existing) config goes live
10. POST /governance/proposals/{id}/publish            proposal → published (verifies step 9 already happened)
```

Steps 6/9 are the pre-existing config endpoints — this API deliberately never
substitutes for them (see module docstring, and `ranking_v2_ahp.md` §3 for
why: a second write path to `ranking_configs` is the exact invariant
`tests/test_ranking_boundary.py` exists to prevent).

## Error codes

All errors: `HTTPException` with `detail: {message, error_code}`.
`*_NOT_FOUND` → 404. `ALREADY_REVIEWED` / `DUPLICATE_OBJECT_STORAGE_KEY` /
`PROPOSAL_STATUS_INVALID` → 409. Everything else (validation) → 422.

## Tests

- `tests/test_services/test_governance.py` — 13 tests, service layer against
  a real Postgres test DB (`bash scripts/test_db.sh` with
  `TEST_TARGET=tests/test_services/test_governance.py`). Covers the full
  draft→published lifecycle, duplicate-reviewer rejection, justification
  edit-lock after submission, evidence upload/link/idempotency, and the
  `mark_published` guard against `ranking_configs` not actually being
  published yet.
- `tests/test_ranking_boundary.py` — extended with
  `test_governance_tables_have_exactly_one_writer_module` and
  `test_no_module_writes_to_a_governance_table_it_is_not_declared_for`,
  mirroring the existing four-table single-writer invariant for all seven
  governance tables.

## Known gaps (not built here — see `ranking_consultant.md` §21.1/§21.11)

- No route accepts a multipart file upload directly — `POST /governance/evidence`
  only registers metadata for a file a caller has already placed in object
  storage. A thin upload-and-register endpoint is a small follow-up, not yet
  built.
- No `resubmit` path after `request_changes` — a proposal parked in
  `under_review` currently has no route back to an editable state. Flagged as
  an open question (see `governance.py` module docstring), not silently
  worked around.
- §21 (chunking/embedding/RAG retrieval) still requires `pgvector` (D15) and
  an embedding-model decision (D16) before it can start, and now additionally
  depends on this API actually being used to create real proposals/documents
  to chunk.
