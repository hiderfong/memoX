# MemoX Deployment

This guide describes the current single-node deployment path for long-running user trials.

## Prerequisites

- Docker with the Compose plugin
- A server with enough disk for uploaded documents, Chroma data, SQLite databases, and generated workspace files
- Provider keys for the models enabled in `config.yaml`

## First Start

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

Edit `.env` and set at least:

```bash
MEMOX_ADMIN_PASSWORD=use-a-long-random-password
DASHSCOPE_API_KEY=your-dashscope-key
```

If you change the default provider or Worker templates in `config.yaml`, also fill the matching provider keys.

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

Back up `config.yaml`, `.env`, `data/`, and `workspace/` together before upgrades, then copy backup archives off the host.

## Backup and Restore

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

The service also starts an in-process maintenance runner when `ops.auto_backup_enabled=true`. By default it waits 5 minutes after startup, then creates and verifies a local backup when the newest archive is older than 24 hours, and prunes archives beyond `ops.max_backups`. Runtime backups include `config.yaml`, `data/`, and `workspace/`; host-only secrets in `.env` should still be protected by the CLI backup flow or an external secret backup. Each automatic maintenance run is recorded in SQLite and surfaced in the admin system health report. Administrators can also trigger the same backup maintenance flow on demand from the system status page or by calling `POST /api/system/maintenance/backup`.

Use explicit flags for heavier actions:

```bash
uv run --extra dev python scripts/ops_check.py --create-backup
uv run --extra dev python scripts/ops_check.py --max-backup-age-hours 12 --max-backups 30
uv run --extra dev python scripts/ops_check.py --smoke
uv run --extra dev python scripts/ops_check.py --restore-drill
```

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

Administrators can inspect the deeper runtime readiness report after logging in. The API report includes config, persistent paths, index consistency, SQLite, disk space, and lightweight backup metadata checks; use `scripts/ops_check.py` when a full backup checksum verification is needed.

```bash
curl -fsS http://localhost:8080/api/system/health -H "Authorization: Bearer <token>"
curl -fsS http://localhost:8080/api/system/backups -H "Authorization: Bearer <token>"
curl -fsS "http://localhost:8080/api/system/events?limit=20" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/indexes/repair" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/backups/<backup-file>.tar.gz/verify" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/backups/<backup-file>.tar.gz/restore-preflight" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/backups/<backup-file>.tar.gz/restore-drill" -H "Authorization: Bearer <token>"
curl -fsS -X POST "http://localhost:8080/api/system/maintenance/backup?force=true" -H "Authorization: Bearer <token>"
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

The script builds the Compose image, starts a temporary container with `embedding_provider: hash`, checks `/api/health`, API docs, OpenAPI, login, `/api/auth/me`, authenticated system health, backup listing, operational events, index repair, backup verification, restore preflight, true-restore rejection guards, and a temporary restore drill, then shuts the container down. The `hash` embedding provider is deterministic and network-free; it is meant for smoke tests and demos, not production retrieval quality.

For a faster local process smoke test without rebuilding the image, `scripts/smoke_test.py` covers the same operational API path against disposable data.

The production Docker image intentionally skips heavy optional extras such as `sentence-transformers` and Streamlit. Prefer DashScope/OpenAI embeddings in container deployments, or build a custom image with `uv sync --extra local-embeddings` if you need local semantic embeddings.

## Operational Notes

- Keep `auth.enabled=true` for any shared deployment. Startup fails if the configured admin password resolves to an empty value.
- Restrict access to Worker management APIs. Creating, updating, or deleting Workers persists changes into `config.yaml`.
- Use a reverse proxy with TLS for internet-facing use. The bundled container exposes plain HTTP on port `8080`.
- Treat `data/`, `workspace/`, `.env`, and `config.yaml` as sensitive. They may contain uploaded documents, task artifacts, API keys, or generated outputs.
- This Compose file is a single-node deployment. It does not provide queue workers, multi-instance locking, or managed database backups.
