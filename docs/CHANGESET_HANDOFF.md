# MemoX Changeset Handoff

Date: 2026-05-25

This handoff summarizes the current working-tree changes for review, release
planning, or PR splitting. It is intentionally operational: reviewers should be
able to map each slice to files, behavior, tests, and residual risk.

## Executive Summary

The current changes move MemoX closer to a long-running real-user deployment by
hardening tool permissions, making tool calls auditable, exposing policy and
audit controls in the admin UI, and documenting release readiness.

The highest-risk paths covered by tests are:

- Database tool policy enforcement for read-only, write, DDL, multi-statement,
  raw connection strings, named data sources, and row limits.
- Server-side network access safety for `web_fetch`, `web_search`, and
  `playwright_crawler`.
- Tool audit persistence, argument/result redaction, status classification, and
  admin filtering.
- Tool policy API persistence, redacted config handling, and permission checks.
- Admin Settings and System Status UI flows for policy review and audit search.

## Suggested Review Slices

### 1. Network safety for web tools

Primary files:

- `src/tools/net_safety.py`
- `src/tools/web.py`
- `src/tools/playwright_crawler.py`
- `tests/test_web_tools.py`
- `tests/test_tool_playwright_crawler.py`

Review focus:

- URL validation blocks localhost, private, link-local, and reserved targets by
  default.
- Redirects are validated manually for `web_fetch`.
- Playwright validates both the main navigation URL and HTTP(S) subrequests.
- Explicit internal host allowlist keeps useful local-tool workflows possible.

Key residual risk:

- Browser crawling depends on Playwright runtime availability and still needs
  deployment-level resource limits for hostile public pages.

### 2. Database tool policy

Primary files:

- `src/tools/database.py`
- `src/config/__init__.py`
- `config.example.yaml`
- `config.yaml`
- `tests/test_tool_database.py`
- `tests/test_config_validation.py`

Review focus:

- Named data sources are preferred over raw connection strings.
- Raw connection strings, writes, DDL, and multiple statements are gated by
  config.
- `default_access_mode=read_only` remains the conservative default.
- Query results are capped by policy-level `max_result_rows`.

Key residual risk:

- Shared deployments should keep `allow_raw_connection_strings=false` unless an
  operator has a clear temporary need.

### 3. Tool audit persistence and classification

Primary files:

- `src/agents/base_agent.py`
- `src/agents/worker_pool.py`
- `src/storage/persistence.py`
- `tests/test_tool_audit.py`
- `tests/test_persistence.py`

Review focus:

- Every registered tool call is logged through `ToolRegistry`.
- Audit context carries worker/task/user metadata.
- Arguments and results are summarized to avoid leaking obvious secrets.
- Policy denials are classified as `rejected`, including Chinese safety messages
  such as "禁止访问" and "访问被拒绝".
- Audit writes are non-blocking so tool execution is not made fragile by logging
  failures.

Key residual risk:

- Audit redaction is defensive but not a replacement for avoiding secrets in
  prompts, task descriptions, or arbitrary tool output.

### 4. Admin API and UI for tool policy/audit

Primary files:

- `src/web/routers/system.py`
- `frontend_wip/src/shared.tsx`
- `frontend_wip/src/pages/SettingsPage.tsx`
- `frontend_wip/src/pages/SystemStatusPage.tsx`
- `tests/test_tool_policy_api.py`
- `tests/test_system_health_api.py`
- `tests/test_api_permissions.py`
- `tests/e2e/test_tool_policy_audit_flow.py`

Review focus:

- `GET /api/system/tool-policy` masks data-source connection strings.
- `PUT /api/system/tool-policy` persists network and database tool policy to
  config while preserving redacted values when the user does not edit them.
- `GET /api/system/tool-audit` supports filtering by tool, status, worker, task,
  limit, and offset.
- Admin pages expose policy editing and audit filtering without requiring direct
  file access.

Key residual risk:

- Runtime policy updates affect in-process config; multi-instance deployments
  would need a stronger config distribution model.

### 5. Frontend compatibility cleanup

Primary files:

- `frontend_wip/src/App.tsx`
- `frontend_wip/src/main.tsx`
- `frontend_wip/src/pages/LoginPage.tsx`
- `frontend_wip/src/pages/DocumentsPage.tsx`
- `frontend_wip/src/pages/ChatPage.tsx`
- `frontend_wip/src/pages/WorkersPage.tsx`
- `frontend_wip/src/pages/WorkflowsPage.tsx`
- `frontend_wip/src/components/KnowledgeGraphView.tsx`
- `frontend_wip/src/components/WorkflowCanvas.tsx`

Review focus:

- Ant Design deprecated props are replaced with current equivalents.
- React Router future flags are enabled to remove framework warnings.
- Unused router imports are removed.

Key residual risk:

- The production build still reports chunk-size warnings; this is not a release
  blocker, but it is a performance follow-up.

### 6. Release and operations documentation

Primary files:

- `README.md`
- `docs/API.md`
- `docs/DEPLOYMENT.md`
- `docs/RECOVERY_RUNBOOK.md`
- `docs/RELEASE_READINESS.md`
- `docs/CHANGESET_HANDOFF.md`
- `tests/test_deployment_files.py`

Review focus:

- API docs include tool policy and tool audit endpoints.
- Deployment docs link release readiness and recovery runbooks.
- Release readiness gives a go/no-go checklist for real-user operation.
- Deployment-file regression tests guard important docs and operational paths.

Key residual risk:

- Readiness docs are only useful if operators actually fill in the go/no-go
  record for each release.

## Recommended Commit Split

1. `tooling: add network safety checks for web-capable tools`
2. `tooling: enforce configurable database access policy`
3. `audit: persist redacted tool-call audit events`
4. `system: expose tool policy and audit admin APIs`
5. `frontend: add tool policy and audit admin views`
6. `tests: cover tool policy, audit, and e2e admin flow`
7. `docs: add release readiness and changeset handoff`

If keeping this as a single PR, reviewers should still walk the slices in the
order above. Network/database policy changes should be reviewed before UI
changes because the UI is only a control surface for the backend behavior.

## Pre-Commit Hygiene Review

Current review result:

- `config.yaml` is tracked by this repository. Its current diff only adds the
  default `tool_policy` block and does not introduce concrete provider keys,
  passwords, or local host paths.
- `config.example.yaml` mirrors the same default policy with comments for new
  deployments.
- Non-ignored untracked files are expected: release docs, network-safety module,
  and new policy/audit tests.
- Ignored generated artifacts include local caches, `frontend_wip/dist/`,
  `frontend_wip/node_modules/`, virtual environments, and local data/backups.
- Existing tracked hygiene debt remains outside this changeset:
  `frontend_wip/src/App.tsx.bak` and `data/skills_registry.json` are already
  tracked. They are not modified here, but should be considered for a separate
  repository-cleanup PR.
- Added-line sensitive-pattern scan only found config field names, environment
  placeholders, test fixtures, and documentation placeholders.

## Validation Matrix

Required before merging:

```bash
uv run --extra dev ruff check .
uv run --extra dev pytest
cd frontend_wip && npm run build
git diff --check
```

Latest local verification:

| Command | Result |
|---|---|
| `uv run --extra dev ruff check .` | Passed |
| `uv run --extra dev pytest` | `561 passed, 3 skipped` |
| `cd frontend_wip && npm run build` | Passed; Vite reported the known large-chunk warning |
| `git diff --check` | Passed |

Recommended for release:

```bash
uv run --extra dev python scripts/smoke_test.py --frontend
uv run --extra dev python scripts/docker_smoke_test.py
```

Manual browser smoke path:

- Login as admin.
- Open Settings.
- Load and edit the Tool Permission Policy card.
- Open System Status.
- Filter Tool Audit by tool name, status, worker, and task.
- Confirm there are no new browser console warnings or errors.

## Known Follow-Ups

- Split large frontend bundles if the React build warning becomes user-visible
  on slow networks.
- Replace the current knowledge graph LLM extraction fallback with a real
  provider-backed batch extractor when graph extraction becomes a product
  priority.
- Consider exporting tool audit data as CSV or diagnostics attachment if support
  workflows need offline review.
- Add a true multi-runner queue or external job backend before horizontal
  scaling beyond the current single-node deployment model.
