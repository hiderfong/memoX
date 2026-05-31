# MemoX Deployment

This guide describes the current single-node deployment path for long-running
user trials. Before shipping a build to real users, complete
[RELEASE_READINESS.md](RELEASE_READINESS.md) and keep
[RECOVERY_RUNBOOK.md](RECOVERY_RUNBOOK.md) ready for maintenance windows and
incidents.

## Prerequisites

- Docker with the Compose plugin
- A server with enough disk for uploaded documents, Chroma data, SQLite databases, and generated workspace files
- Provider keys for the models enabled in `config.yaml`

## First Start

```bash
cp .env.production.example .env
cp config.example.yaml config.yaml
```

Edit `.env` and set at least:

```bash
MEMOX_ADMIN_PASSWORD=use-a-long-random-password
DASHSCOPE_API_KEY=your-dashscope-key
QWEN_API_KEY=your-qwen-key
DEEPSEEK_API_KEY=your-deepseek-key
MINIMAX_API_KEY=your-minimax-key
MEMOX_FILE_SIGNING_SECRET=use-another-long-random-secret
```

If you change the default provider or Worker templates in `config.yaml`, also fill the matching provider keys.
For the production host-by-host checklist, use
[PRODUCTION_DEPLOYMENT_CHECKLIST.md](PRODUCTION_DEPLOYMENT_CHECKLIST.md).
Configure browser access through `server.cors_origins` in `config.yaml`. Keep
the list limited to the deployed React UI origins, for example your production
domain and any trusted internal admin domain. Do not rely on hard-coded public
IP addresses in application code.

Start the service:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f memox
```

Open:

- App: `http://localhost:8080`
- Swagger UI: `http://localhost:8080/api/docs`
- Health check: `http://localhost:8080/api/health`

## Persistence

The Compose file bind-mounts these host paths:

| Host path | Container path | Purpose |
|---|---|---|
| `./config.yaml` | `/app/config.yaml` | Runtime configuration; Worker management APIs update `worker_templates` here |
| `./data` | `/app/data` | Chroma, SQLite, uploads, BM25 index, groups, workflow state |
| `./workspace` | `/app/workspace` | Worker task artifacts and shared files |
| `./backups` | `/app/backups` | Local backup archives visible to the admin readiness report |
| external or mounted directory | configured `ops.archive_mirror_dir` | Optional mirror target for backups and diagnostic bundles |

Back up `config.yaml`, `.env`, `data/`, and `workspace/` together before upgrades, then copy backup archives off the host. For continuous off-host protection, mount an external directory into the container and point `ops.archive_mirror_dir` at that path.

## Long-Running Tasks

Task submission is asynchronous: `POST /api/tasks` persists the request, returns a task record immediately, and an in-process runner continues execution in the background. Clients should poll `GET /api/tasks/{task_id}` for terminal states and inspect `GET /api/tasks/{task_id}/events` for progress, subtask checkpoints, failure reasons, and lease transitions. Retryable failures can be requeued with `POST /api/tasks/{task_id}/retry`.

The runner stores recoverable job requests, checkpoints, and events in SQLite. On service startup it claims queued or running jobs whose lease is free or expired, restores the latest checkpoint, skips completed subtasks, and continues the remaining work. Active leases are refreshed during execution; if a runner loses its lease, it stops local execution without writing a terminal task status so another owner can recover cleanly.

Failure events include a machine-readable `failure_type`: `user_cancelled`, `orchestrator_cancelled`, `lease_lost`, `timeout`, `retryable_exception`, or `non_retryable_exception`. Treat `timeout`, `lease_lost`, and `retryable_exception` as safe candidates for retry; treat `user_cancelled` and `non_retryable_exception` as requiring user intent or operator review.

Automatic retry is controlled by the `coordinator.task_auto_retry_*` settings. The default example configuration enables up to two automatic retries for `timeout` and `retryable_exception`, starting after 30 seconds and backing off up to 300 seconds. Each scheduled retry is persisted in SQLite as `task_jobs.next_retry_at`, so a service restart can reload and continue pending retry timers instead of losing them.

## Backup and Restore

For incident response and maintenance-window sequencing, use
[RECOVERY_RUNBOOK.md](RECOVERY_RUNBOOK.md). The commands below are the lower
level building blocks.

For a consistent backup, pause writes first. On the single-node Compose deployment the simplest path is:

```bash
docker compose down
uv run --extra dev python scripts/backup_restore.py create
uv run --extra dev python scripts/backup_restore.py verify backups/<backup-file>.tar.gz
docker compose up -d
```

The backup archive includes existing `config.yaml`, `.env`, `data/`, and `workspace/` paths. Missing paths are recorded in `memox-backup.json` inside the archive, so a fresh deployment without `.env` or `workspace/` can still be backed up if at least one persistent path exists.

Inspect a backup before restoring:

```bash
uv run --extra dev python scripts/backup_restore.py inspect backups/<backup-file>.tar.gz
```

Prune old local archives after confirming an external copy exists:

```bash
uv run --extra dev python scripts/backup_restore.py prune --keep 14 --dry-run
uv run --extra dev python scripts/backup_restore.py prune --keep 14
```

Restore into an empty directory for migration or disaster-recovery drills:

```bash
mkdir -p /tmp/memox-restore-check
uv run --extra dev python scripts/backup_restore.py restore backups/<backup-file>.tar.gz --target /tmp/memox-restore-check
```

Restoring into an existing deployment refuses to overwrite files unless `--overwrite` is provided:

```bash
docker compose down
uv run --extra dev python scripts/backup_restore.py restore backups/<backup-file>.tar.gz --target . --overwrite
docker compose up -d
```

Treat backup archives as sensitive. They may contain `.env`, API keys, uploaded documents, vector indexes, SQLite databases, and Worker artifacts.

Run a complete local recovery drill after changing deployment code, backup tooling, or host storage:

```bash
uv run --extra dev python scripts/restore_drill.py
```

The drill creates a temporary source deployment, starts MemoX from its real `config.yaml`, uploads a searchable document, stops the service, creates and verifies a backup, restores it into a second directory, starts MemoX from the restored deployment root, then checks login, document listing, chunks, search, Worker configuration, and `workspace/` artifacts. It is intentionally offline and uses `embedding_provider: hash`, so it does not call external model providers.

## Index Consistency Checks

If users report missing search results, duplicate documents, or failed uploads, run a read-only consistency audit from the deployment root:

```bash
uv run --extra dev python scripts/index_consistency.py
```

The audit compares:

- Chroma documents and chunks
- BM25 chunk IDs
- `documents_manifest.json` entries used for duplicate/update detection

To rebuild repairable state:

```bash
docker compose down
uv run --extra dev python scripts/backup_restore.py create
uv run --extra dev python scripts/index_consistency.py --repair
docker compose up -d
```

`--repair` rebuilds BM25 from Chroma and removes manifest entries that point to Chroma documents that no longer exist. It does not synthesize missing manifest entries for legacy/URL-imported documents because their original content hash may not be recoverable safely.

## Operational Check

Run a quick read-only operational check from the deployment root:

```bash
uv run --extra dev python scripts/ops_check.py
```

The default check loads `config.yaml`, checks configured persistent directories, audits Chroma/BM25/manifest consistency, runs SQLite quick checks, checks disk free space, and verifies the latest `backups/memox-backup-*.tar.gz` archive if one exists. It warns if the latest backup is older than 24 hours or more than 14 local backup archives exist. Missing backups or fresh persistent directories are warnings; index corruption or an unreadable backup is an error.

If `config.yaml` references environment variables such as `${MEMOX_ADMIN_PASSWORD}`, run the check from a shell where those variables are exported.

The service also starts an in-process maintenance runner when `ops.auto_backup_enabled=true`. By default it waits 5 minutes after startup, then creates and verifies a local backup when the newest archive is older than 24 hours, and prunes archives beyond `ops.max_backups`. Runtime backups include `config.yaml`, `data/`, and `workspace/`; host-only secrets in `.env` should still be protected by the CLI backup flow or an external secret backup. If `ops.archive_mirror_dir` is set, each automatic or manual backup is also copied into `<mirror>/backups/` with checksum verification; the same mirror target receives diagnostic bundles under `<mirror>/diagnostics/`. Mirror failures are recorded as warnings in the admin system health report so local backups can still complete. Administrators can also trigger the same backup maintenance flow on demand from the system status page or by calling `POST /api/system/maintenance/backup`.

Use explicit flags for heavier actions:

```bash
uv run --extra dev python scripts/ops_check.py --create-backup
uv run --extra dev python scripts/ops_check.py --max-backup-age-hours 12 --max-backups 30
uv run --extra dev python scripts/ops_check.py --smoke
uv run --extra dev python scripts/ops_check.py --restore-drill
```

## External Monitoring Probe

For production uptime monitoring, run the read-only probe from a trusted admin
host:

```bash
MEMOX_URL=https://memox.example.com \
MEMOX_TOKEN=<admin-token> \
uv run --extra dev python scripts/production_monitor_check.py
```

The probe collects `/api/health`, `/api/system/health`,
`/api/videos/jobs/status`, recent operational warnings/errors, and recent tool
audit errors/rejections. It prints a JSON report with `status` set to `ok`,
`warning`, or `error`. Use `--strict` when the scheduler should treat warnings
as a failed check:

```bash
MEMOX_URL=https://memox.example.com \
MEMOX_ADMIN_PASSWORD=<admin-password> \
uv run --extra dev python scripts/production_monitor_check.py --strict
```

Tune the queue and audit thresholds with `--max-media-pending`,
`--max-media-persisted-queued`, `--max-media-persisted-running`,
`--max-recent-tool-errors`, and `--max-recent-tool-rejections` for the expected
traffic level of the deployment.

The repository also includes `.github/workflows/production-monitor.yml` for
scheduled or manual GitHub Actions monitoring. Configure
`MEMOX_PRODUCTION_URL` plus either `MEMOX_PRODUCTION_TOKEN` or
`MEMOX_PRODUCTION_ADMIN_PASSWORD` as repository secrets or variables. The
workflow defaults to `--strict`, so warnings fail the run and can trigger normal
GitHub notification channels. Each run writes a Step Summary and uploads a
`production-monitor-report` artifact. Use
[PRODUCTION_MONITOR_RUNBOOK.md](PRODUCTION_MONITOR_RUNBOOK.md) when the probe
reports `warning` or `error`.

## Upgrade

```bash
git pull
docker compose build --pull
docker compose up -d
docker compose logs -f memox
```

Run a basic health check after the container becomes healthy:

```bash
curl -fsS http://localhost:8080/api/health
```

Administrators can inspect the deeper runtime readiness report after logging in. The API report includes config, persistent paths, index consistency, SQLite, disk space, backup metadata checks, archive mirror configuration, and SQLite schema version/migration records. The diagnostics export endpoint creates a zip package with the health report, backup list, recent operational events, index consistency report, redacted config, and redacted tails of common local log files; use it when escalating a production issue. JSON reports and log tails redact common API keys, bearer tokens, passwords, secrets, cookies, and private keys, but diagnostics bundles should still be treated as sensitive operational artifacts. When `ops.archive_mirror_dir` is set, the exported zip is also mirrored to `<mirror>/diagnostics/`. Use `scripts/ops_check.py` when a full backup checksum verification is needed.

Lifecycle cleanup is intentionally conservative. `POST /api/system/maintenance/lifecycle?dry_run=true` reports expired operational events, audit log rows, terminal background job records, and diagnostic bundles according to `ops.ops_event_retention_days`, `ops.audit_log_retention_days`, `ops.task_job_retention_days`, `ops.diagnostic_retention_days`, and `ops.max_diagnostic_bundles`; `dry_run=false` executes that cleanup and records a `lifecycle_cleanup` operational event. It does not delete chats, memories, uploaded documents, task history, checkpoints, events, or workspace files.

```bash
curl -fsS http://localhost:8080/api/system/health -H "Authorization: Bearer <token>"
curl -fsS http://localhost:8080/api/system/backups -H "Authorization: Bearer <token>"
curl -fsS "http://localhost:8080/api/system/events?limit=20" -H "Authorization: Bearer <token>"
curl -fsS "http://localhost:8080/api/system/tool-audit?limit=20" -H "Authorization: Bearer <token>"
curl -fsS "http://localhost:8080/api/system/tool-policy" -H "Authorization: Bearer <token>"
curl -fsS -OJ "http://localhost:8080/api/system/diagnostics/export" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/indexes/repair" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/backups/<backup-file>.tar.gz/verify" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/backups/<backup-file>.tar.gz/restore-preflight" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/backups/<backup-file>.tar.gz/restore-drill" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/maintenance/backup?force=true" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/maintenance/lifecycle?dry_run=true" -H "Authorization: Bearer <token>"
```

Only run a real restore during a maintenance window and after reviewing `restore-preflight`. The API requires the archive name to be typed back exactly, requires overwrite and maintenance acknowledgements, and creates a verified safety backup before writing restored files. After a real restore, run `/api/system/indexes/repair`, restart the service so restored config/SQLite/vector-store state is loaded cleanly, then check `/api/system/health`.

```bash
curl -fsS -X POST "http://localhost:8080/api/system/backups/<backup-file>.tar.gz/restore" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"confirm_archive_name":"<backup-file>.tar.gz","acknowledge_overwrite":true,"acknowledge_maintenance_mode":true}'
```

## Deployment Smoke Test

Before changing a real deployment, run the offline Docker smoke test:

```bash
uv run --extra dev python scripts/docker_smoke_test.py
```

The script builds the Compose image, starts a temporary container with `embedding_provider: hash`, checks `/api/health`, API docs, OpenAPI, login, `/api/auth/me`, authenticated system health, backup listing, operational events, diagnostics export, index repair, backup verification, restore preflight, true-restore rejection guards, and a temporary restore drill, then shuts the container down. The `hash` embedding provider is deterministic and network-free; it is meant for smoke tests and demos, not production retrieval quality.

For a faster local process smoke test without rebuilding the image, `scripts/smoke_test.py` covers the same operational API path against disposable data.

The production Docker image intentionally skips heavy optional extras such as `sentence-transformers` and Streamlit. Prefer DashScope/OpenAI embeddings in container deployments, or build a custom image with `uv sync --extra local-embeddings` if you need local semantic embeddings.

## Operational Notes

- Keep `auth.enabled=true` for any shared deployment. Startup fails if the configured admin password resolves to an empty value.
- Keep `/api/files/` out of `auth.public_paths`. Uploaded files require Bearer
  authentication or a short-lived signed URL generated by `POST /api/files/sign`.
  Configure `MEMOX_FILE_SIGNING_SECRET` before enabling workflows that pass local
  uploads to external services.
- Repeated failed logins for the same username and client are temporarily locked and return HTTP `429` with `Retry-After`; wait for the lock window instead of restarting the service.
- Restrict access to Worker management APIs. Creating, updating, or deleting Workers persists changes into `config.yaml`.
- Tune `tool_policy.web` and `tool_policy.playwright_crawler` for the host. The
  default `web_search` / `web_fetch` policy allows public web access with
  bounded timeout, response bytes, extracted text, and search result count.
  Browser crawling is heavier; raise Playwright concurrency, page count,
  response size, and output size only when the deployment has enough CPU and
  memory headroom.
- Use a reverse proxy with TLS for internet-facing use. The bundled container exposes plain HTTP on port `8080`.
- Treat `data/`, `workspace/`, `.env`, and `config.yaml` as sensitive. They may contain uploaded documents, task artifacts, API keys, or generated outputs.
- This Compose file is a single-node deployment. It does not provide queue workers, multi-instance locking, or managed database backups.
