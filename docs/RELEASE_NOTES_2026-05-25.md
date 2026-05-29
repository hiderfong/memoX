# Release Notes - 2026-05-25

This release focuses on making MemoX safer and more operable for long-running
real-user deployments.

## Highlights

- Added configurable `tool_policy` settings for high-permission tools.
- Hardened `web_fetch`, `web_search`, and `playwright_crawler` against unsafe
  internal network targets while preserving explicit internal host allowlists.
- Added bounded runtime limits for `web_fetch` and `web_search` covering timeout,
  response bytes, extracted text length, and search result count.
- Added database tool access policy for named data sources, raw connection
  strings, read/write/admin modes, DDL, multi-statement SQL, and result row
  limits.
- Added redacted tool-call audit logging with worker/task context.
- Added admin APIs for tool policy management and tool audit filtering.
- Added Settings and System Status UI surfaces for reviewing tool policy and
  tool audit events.
- Updated frontend compatibility for current Ant Design and React Router
  warnings.
- Fixed CI frontend job paths to use `frontend_wip`.
- Moved local embedding model dependencies behind the `local-embeddings` extra
  so the production Docker image skips `sentence-transformers`/`torch` by
  default.
- Added configurable Playwright crawler resource controls for concurrency,
  queue wait, total timeout, page count, response size, and output size.
- Added release readiness and changeset handoff docs.
- Completed I2V Phase 2: Wan2.7 `input.media` protocol, local upload fallback
  through DashScope temporary OSS, batch I2V API, video editing API, and
  knowledge-base image entry points.
- Added a React media creation workspace with batch I2V submission, video
  editing, persisted media assets, queued/running/success/failed status,
  retry for failed assets, startup interruption recovery, and a bounded
  in-process media task queue.
- Added a productized knowledge graph exploration payload and UI with entity
  search, neighborhood depth, predicate and confidence filters, core-entity
  facets, and relation provenance.
- Added administrator knowledge graph governance actions for merging duplicate
  entities, splitting selected entity evidence into a new entity, and correcting
  or deleting noisy extracted relations.
- Added a generated knowledge graph quality review queue for duplicate entities,
  low-confidence relations, isolated weak relations, and identity conflicts.
- Identity-conflict and source-cluster divergence candidates now carry
  executable split suggestions, so administrators can open a prefilled split
  workflow from the quality queue.
- Persisted quality review decisions so accepted, ignored, and snoozed graph
  candidates stay out of the default queue.
- Bound persisted graph review decisions to candidate content fingerprints so
  changed candidates are reactivated instead of being hidden by stale decisions.
- Added batch graph-review decisions and expandable candidate evidence to make
  administrator graph cleanup less one-row-at-a-time.
- Added graph extraction quality metrics for health score, coverage, extraction
  density, low-confidence ratio, isolated-relation ratio, and review backlog.
- Persisted graph quality metric snapshots and exposed a recent trend panel so
  operators can see whether graph health is improving over time.
- Added threshold-based graph quality alerts for health score, health drops,
  low-confidence ratio, isolated-relation ratio, and open review backlog.
- Routed graph quality alerts into deduplicated `knowledge_graph_quality_alert`
  ops events and the System Status dashboard so operators can see graph health
  issues alongside backup, lifecycle, and task-runner signals.
- Added configurable non-blocking graph quality gates so operators can define
  minimum health, maximum low-confidence/isolated/backlog ratios, and see gate
  failures in both graph review and system readiness views.
- Document upload and URL import now automatically write graph quality snapshots
  after graph extraction, including the triggering document and any health-score
  drop from the previous snapshot.
- Graph quality regressions now open deduplicated
  `knowledge_graph_governance_task` events with suggested administrator actions,
  surfaced on System Status and filterable in operational events.
- Graph governance decisions and graph mutations now re-run quality checks and
  write `governance_task_resolved` when the actionable graph quality task has
  recovered.

## Operational Notes

- Review `docs/RELEASE_READINESS.md` before deploying to real users.
- Use `GET /api/system/tool-audit` to verify policy denials show as `rejected`
  rather than generic `error`.
- Keep `tool_policy.database.allow_raw_connection_strings=false` for shared
  deployments unless an operator has a specific temporary need.
- Add `tool_policy.network.allow_internal_hosts` only for trusted internal
  services that Workers must access.
- Treat diagnostic bundles, backup archives, and audit logs as sensitive
  operational data.

## Validation

Latest local verification in this working tree:

| Check | Result |
|---|---|
| `git diff --check` | Passed |
| `.venv/bin/python -m ruff check src tests` | Passed |
| `.venv/bin/python -m pytest tests --ignore=tests/e2e -q --tb=short` | Passed; one skipped |
| `cd frontend_wip && npm run build` | Passed; no Vite large chunk warning |

GitHub PR checks:

- PR #1 Backend: passed
- PR #1 Frontend: passed
- PR #2 Backend: passed
- PR #2 Frontend: passed
- E2E: skipped by workflow unless manually requested

## Known Follow-Ups

- Expand knowledge graph productization with optional external notification
  delivery for graph quality alerts and pre-rollout quality gates.
- Add a dedicated frontend batch I2V/video-editing workspace if users need
  multi-asset creative production beyond the current API and document-preview
  entry point.
- Consider CSV or diagnostic-bundle export for tool audit events if support
  workflows need offline review.
- Plan an external job backend before scaling beyond the current single-node
  deployment model.
