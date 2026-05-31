# MemoX Production Deployment Checklist

Use this checklist after `v1.0.0` has passed Release Gate and before opening a
deployment to real users.

## Secrets

- Copy `.env.production.example` to `.env` on the production host.
- Replace every `replace-with-*` placeholder with a real secret.
- Keep `.env` outside Git and back it up through an operator-controlled secret
  backup process.
- Rotate any provider key or password that has ever appeared in chat, logs, or a
  shared document.

Required production variables:

- `MEMOX_ADMIN_PASSWORD`
- `MEMOX_FILE_SIGNING_SECRET`
- `DASHSCOPE_API_KEY`
- `QWEN_API_KEY`
- `DEEPSEEK_API_KEY`
- `MINIMAX_API_KEY`

## Host Paths

Create durable host paths before starting Compose:

```bash
mkdir -p data workspace backups
```

For off-host protection, mount an external disk or sync directory and set
`ops.archive_mirror_dir` in `config.yaml`.

## Network

- Put TLS at the reverse proxy.
- Keep the container service on private/plain HTTP, usually port `8080`.
- Set `server.cors_origins` to the exact production UI origin(s).
- Protect `/api/docs`, `/api/redoc`, and `/api/openapi.json` with network
  controls if the deployment is internet-facing.

## Before First User Traffic

Run from the deployment root with `.env` loaded:

```bash
set -a
source .env
set +a
uv run --extra dev python scripts/ops_check.py --create-backup --restore-drill --timeout 240
```

Start the service:

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8080/api/health
```

After logging in as admin, confirm:

- System Status loads without browser console errors.
- Latest backup is verified.
- Tool audit filters work for `success`, `rejected`, and `error`.
- I2V job queue reports zero unexpected persisted running jobs.
- A diagnostics export can be generated and stored securely.

## Monitoring Probe

Run the read-only monitoring probe from a trusted admin host after the service is
reachable:

```bash
MEMOX_URL=https://memox.example.com \
MEMOX_TOKEN=<admin-token> \
uv run --extra dev python scripts/production_monitor_check.py
```

If a long-lived token is not available, let the probe log in with the admin
password supplied by the host secret store:

```bash
MEMOX_URL=https://memox.example.com \
MEMOX_ADMIN_PASSWORD=<admin-password> \
uv run --extra dev python scripts/production_monitor_check.py --strict
```

The probe checks `/api/health`, `/api/system/health`, media job pressure,
recent warning/error events, and tool audit error/rejection volume. It prints a
JSON snapshot. `error` exits non-zero; `--strict` also exits non-zero on
`warning`, which is useful for cron, GitHub Actions, or an external uptime
monitor.

## First 24 Hours

Watch these signals closely:

- Failed login spikes and HTTP `429` lockouts.
- External provider failures or rising latency.
- Task failure rate and retry counts.
- I2V queue duration and failed media jobs.
- Disk free space under `data/`, `workspace/`, and `backups/`.
- Backup mirror warnings in System Status.

Keep a verified backup and restore drill record with the release notes.
