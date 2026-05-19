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

Back up all three paths together before upgrades.

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

## Deployment Smoke Test

Before changing a real deployment, run the offline Docker smoke test:

```bash
uv run --extra dev python scripts/docker_smoke_test.py
```

The script builds the Compose image, starts a temporary container with `embedding_provider: hash`, checks `/api/health`, API docs, OpenAPI, login and `/api/auth/me`, then shuts the container down. The `hash` embedding provider is deterministic and network-free; it is meant for smoke tests and demos, not production retrieval quality.

The production Docker image intentionally skips heavy optional extras such as `sentence-transformers` and Streamlit. Prefer DashScope/OpenAI embeddings in container deployments, or build a custom image with `uv sync --extra local-embeddings` if you need local semantic embeddings.

## Operational Notes

- Keep `auth.enabled=true` for any shared deployment. Startup fails if the configured admin password resolves to an empty value.
- Restrict access to Worker management APIs. Creating, updating, or deleting Workers persists changes into `config.yaml`.
- Use a reverse proxy with TLS for internet-facing use. The bundled container exposes plain HTTP on port `8080`.
- Treat `data/`, `workspace/`, `.env`, and `config.yaml` as sensitive. They may contain uploaded documents, task artifacts, API keys, or generated outputs.
- This Compose file is a single-node deployment. It does not provide queue workers, multi-instance locking, or managed database backups.
