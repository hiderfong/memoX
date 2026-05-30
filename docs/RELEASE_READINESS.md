# MemoX Release Readiness

This checklist is for a single-node MemoX deployment that will serve real users
for an extended period. It complements `DEPLOYMENT.md` and
`RECOVERY_RUNBOOK.md`: use this document to decide whether a build is ready to
ship, then use the runbook when a maintenance window or incident starts.

## Release Gate

Do not release until every required item below is complete or has an explicit
owner and written exception.

| Area | Required before release |
|---|---|
| Authentication | `auth.enabled=true`, non-empty admin password, and no shared default credentials |
| Secrets | Provider keys live in environment variables or secret storage, not committed files |
| Persistence | `config.yaml`, `data/`, `workspace/`, and `backups/` are durable host paths |
| Backups | A fresh backup archive is created, verified, and copied off the host or mirrored |
| Restore | A restore drill or API restore preflight has passed on the selected build |
| Background tasks | Task submission, polling, retry, and startup recovery pass regression tests |
| Tool policy | Network and database permissions are reviewed for the deployment environment |
| Tool audit | Admins can filter `success`, `rejected`, and `error` tool calls by tool/task/worker |
| Frontend | Settings and System Status admin pages load without console errors |
| I2V / media studio | If enabled, DashScope key, file signing secret, local upload fallback, background queue, retry, and endpoint tests pass |
| Tests | Backend tests, lint, frontend build, and at least one smoke test pass |

## Configuration Checks

- Start from `config.example.yaml` and apply deployment-specific overrides.
- Keep `server.host=0.0.0.0` inside containers and put TLS at the reverse proxy.
- Keep public auth paths minimal. `/api/docs`, `/api/redoc`, and
  `/api/openapi.json` are useful for private deployments but should be protected
  by network controls if the service is internet-facing.
- Confirm `/api/files/` is not in `auth.public_paths` and
  `MEMOX_FILE_SIGNING_SECRET` is set when external services need to fetch local
  uploads through short-lived URLs.
- If `image_to_video.enabled=true`, confirm `DASHSCOPE_API_KEY` is set,
  `image_to_video.model` and `image_to_video.edit_model` match the deployed
  DashScope models, and `/api/videos/i2v` can process both public URLs and
  local `/api/files/{name}` references.
- Confirm `/api/videos/i2v/jobs`, `/api/videos/i2v/batch/jobs`, and
  `/api/videos/edit/jobs` return queued media assets, `/api/videos/jobs/status`
  reports bounded queue pressure, and failed assets can be retried through
  `/api/videos/assets/{asset_id}/retry`.
- Confirm `ops.auto_backup_enabled`, `ops.auto_backup_interval_hours`,
  `ops.max_backups`, and retention settings match the expected user data volume.
- Set `ops.archive_mirror_dir` when the host has an attached backup disk or
  external sync mount.
- Use production model and embedding providers. The `hash` embedding provider is
  only for tests, demos, and smoke checks.

## Tool Permission Review

MemoX should preserve useful tool capability while keeping the default blast
radius controlled.

- Network tools block localhost, private IP ranges, link-local hosts, and
  reserved addresses by default.
- Add explicit `tool_policy.network.allow_internal_hosts` entries for local
  services that Workers must reach, such as `127.0.0.1:3000` or an internal
  search gateway.
- Review `tool_policy.web` so `web_search` and `web_fetch` have bounded timeout,
  response size, extracted text size, and search result count for the host.
- Review `tool_policy.playwright_crawler` against host capacity. Keep
  `max_concurrency`, `total_timeout_seconds`, `max_pages`, and
  `max_response_bytes` conservative on shared deployments.
- Prefer named database sources in `tool_policy.database.data_sources`.
- Keep `allow_raw_connection_strings=false` for shared deployments unless there
  is a short-lived operator need.
- Keep `default_access_mode=read_only`; enable `allow_write` only for Workers
  that have a clear business workflow requiring writes.
- Keep `allow_ddl=false` and `allow_multiple_statements=false` unless a
  maintenance-only Worker is explicitly trusted.
- Check `GET /api/system/tool-audit?status=rejected` after smoke tests. Policy
  denials should appear as `rejected`, not generic `error`.

## Background Task Checks

Before releasing changes to orchestration, Workers, or persistence:

- Submit a task through `POST /api/tasks` and confirm the initial response is
  returned before work finishes.
- Poll `GET /api/tasks/{task_id}` until a terminal state.
- Inspect `GET /api/tasks/{task_id}/events` for subtask checkpoints and failure
  types.
- Restart the service while a retryable task is queued or running, then confirm
  lease recovery resumes or requeues the job.
- Confirm `POST /api/tasks/{task_id}/retry` works for timeout or retryable
  failures and does not retry cancelled user intent.
- Run `tests/e2e/test_deterministic_collab_orchestrator_flow.py`; it requires no
  provider secrets and covers the real `IterativeOrchestrator`, `WorkerPool`,
  `WorkerAgent`, sandbox file tools, mail tools, dependency ordering, and
  quality evaluation with a deterministic provider.
- Run `tests/e2e/test_deterministic_multiagent_task_flow.py`; it requires no
  provider secrets and covers successful execution, retryable failure, manual
  retry, trace aggregation, file artifacts, and checkpoint recovery.
- Run `tests/e2e/test_scheduled_task_queue_flow.py`; it verifies scheduled
  tasks created through the API fire into the same persistent background task
  queue with source context and active groups preserved.

## Validation Commands

Run these from the repository root before preparing a release branch or tag:

```bash
uv run --extra dev ruff check .
uv run --extra dev pytest
uv run --extra dev pytest tests/e2e/test_deterministic_collab_orchestrator_flow.py
uv run --extra dev pytest tests/e2e/test_deterministic_multiagent_task_flow.py
uv run --extra dev pytest tests/e2e/test_scheduled_task_queue_flow.py
cd frontend_wip && npm run build
uv run --extra dev python scripts/smoke_test.py --frontend
MEMOX_BROWSER_E2E=1 uv run --extra dev pytest tests/e2e/test_admin_ui_browser_flow.py
uv run --extra dev python scripts/docker_smoke_test.py
```

Real-key provider checks that require unrestricted external network access are
the release gate. Before publishing a `v*` tag, run the `Release Gate` GitHub
Actions workflow or push the tag and wait for it to complete. It runs
`uv run --extra dev python scripts/run_external_e2e.py --phases smoke` without
`--allow-missing-secrets`; missing DeepSeek, MiniMax, Qwen, DashScope, or file
signing secrets must fail the gate rather than skipping phases. Attach the
`release-gate-e2e-report` artifact to the release notes. Use
`docs/EXTERNAL_AGENT_E2E_RUNBOOK.md` for manual troubleshooting.

For a faster code-review loop, the following targeted tests cover the current
task execution, tool policy, audit, and operational API paths:

```bash
uv run --extra dev pytest \
  tests/test_task_jobs.py \
  tests/e2e/test_deterministic_collab_orchestrator_flow.py \
  tests/e2e/test_deterministic_multiagent_task_flow.py \
  tests/e2e/test_scheduled_task_queue_flow.py \
  tests/test_tool_database.py \
  tests/test_tool_audit.py \
  tests/test_tool_policy_api.py \
  tests/e2e/test_admin_ui_browser_flow.py \
  tests/e2e/test_tool_policy_audit_flow.py \
  tests/test_system_health_api.py
```

## Browser Smoke Path

After frontend or admin API changes, verify the real browser flow:

1. Start a disposable backend with a temporary config and deterministic
   `embedding_provider: hash`.
2. Start the Vite frontend against that backend.
3. Log in as an admin.
4. Open Settings and load the Tool Permission Policy card.
5. Open System Status and filter Tool Audit by `tool_name`, `status`,
   `worker_id`, and `task_id`.
6. Confirm there are no new browser console warnings or errors.

Save the screenshot path or CI artifact in the release notes.

## Backup And Restore Gate

Before upgrading a deployment with real user data:

```bash
uv run --extra dev python scripts/ops_check.py
uv run --extra dev python scripts/backup_restore.py create
uv run --extra dev python scripts/backup_restore.py verify backups/<backup-file>.tar.gz
uv run --extra dev python scripts/restore_drill.py
```

For API-level restore readiness:

```bash
curl -fsS "$MEMOX_URL/api/system/backups/$BACKUP_NAME/restore-preflight" \
  -H "Authorization: Bearer $MEMOX_TOKEN"
```

Only perform a real restore during a maintenance window and after following
`RECOVERY_RUNBOOK.md`.

## Go/No-Go Decision

Record the release decision with this minimal template:

```text
Build or commit:
Operator:
Date:
Backend tests:
Lint:
Frontend build:
Smoke test:
Docker smoke:
Latest verified backup:
Restore drill or preflight:
Known exceptions:
Decision: GO / NO-GO
```

## Residual Risks To Track

- The default deployment is single-node. SQLite leases support restart recovery,
  but this is not a distributed multi-runner queue.
- The React build may emit bundle-size warnings as the admin UI grows. Treat
  warnings as a performance follow-up before broad rollout.
- LLM-assisted knowledge graph extraction uses the configured provider and
  falls back per chunk to rule-based extraction; validate provider, model, cost,
  and graph quality before enabling it broadly.
- Local backup archives are sensitive and remain on the host unless mirrored or
  copied off-host.
- Browser-based dynamic crawling depends on Playwright browser availability in
  the runtime environment.
