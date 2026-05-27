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

Latest local verification on `codex/playwright-resource-controls`:

| Check | Result |
|---|---|
| `git diff --check` | Passed |
| `uv run --extra dev ruff check .` | Passed |
| `uv run --extra dev pytest` | `565 passed, 3 skipped` |
| `cd frontend_wip && npm run build` | Passed with known large chunk warning |
| `uv run --extra dev python scripts/smoke_test.py --frontend` | Passed |
| `uv run --extra dev python scripts/docker_smoke_test.py` | Passed; rebuilt `memox:local` at `1.81GB` |

GitHub PR checks:

- PR #1 Backend: passed
- PR #1 Frontend: passed
- PR #2 Backend: passed
- PR #2 Frontend: passed
- E2E: skipped by workflow unless manually requested

## Known Follow-Ups

- Split large frontend bundles if load time becomes user-visible on slower
  networks.
- Replace the current knowledge graph LLM extraction fallback with a real
  provider-backed batch extractor when that feature becomes a product priority.
- Consider CSV or diagnostic-bundle export for tool audit events if support
  workflows need offline review.
- Plan an external job backend before scaling beyond the current single-node
  deployment model.
